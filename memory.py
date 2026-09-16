"""MemoryOS memory engine (M1 — see ARCHITECTURE.md §3.2, §5).

Local-first store for everything the user has heard: recordings, transcript
chunks with embeddings (semantic search), FTS5 keyword index, and extracted
entities (people, projects, topics, action items, promises, decisions).

SQLite only — no server, no cloud, private by default.
"""

import contextlib
import pathlib
import sqlite3
import threading

import numpy as np

ENTITY_TYPES = ("person", "project", "topic", "action", "promise", "decision",
                "deadline", "question")

CHUNK_TARGET_WORDS = 60      # ~2-4 sentences per searchable chunk
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

_embedder = None
_embedder_lock = threading.Lock()


def default_embedder(texts: list[str]) -> np.ndarray:
    """Local MiniLM embeddings (384-dim), lazily loaded, CPU."""
    global _embedder
    with _embedder_lock:
        if _embedder is None:
            from sentence_transformers import SentenceTransformer

            _embedder = SentenceTransformer(EMBED_MODEL, device="cpu")
    vecs = _embedder.encode(texts, normalize_embeddings=True,
                            show_progress_bar=False)
    return np.asarray(vecs, dtype=np.float32)


def embeddings_available() -> bool:
    import importlib.util

    # find_spec instead of import: checking must not pull torch into RAM
    return importlib.util.find_spec("sentence_transformers") is not None


class MemoryStore:
    """All reads/writes go through one connection guarded by a lock —
    correct and plenty fast for a single-user local app."""

    def __init__(self, db_path: pathlib.Path | str, embed_fn=None):
        self.db_path = str(db_path)
        self.embed_fn = embed_fn or default_embedder
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.db_path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._create_schema()

    def _create_schema(self):
        with self._lock, self._db:
            self._db.executescript("""
                CREATE TABLE IF NOT EXISTS recordings(
                    name TEXT PRIMARY KEY,
                    title TEXT DEFAULT '',
                    category TEXT DEFAULT '',
                    created TEXT DEFAULT '',
                    duration REAL DEFAULT 0,
                    summary TEXT DEFAULT '',
                    enriched INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS chunks(
                    id INTEGER PRIMARY KEY,
                    recording TEXT NOT NULL,
                    start REAL DEFAULT 0,
                    speaker TEXT DEFAULT '',
                    text TEXT NOT NULL,
                    embedding BLOB
                );
                CREATE INDEX IF NOT EXISTS idx_chunks_rec ON chunks(recording);
                CREATE TABLE IF NOT EXISTS entities(
                    id INTEGER PRIMARY KEY,
                    recording TEXT NOT NULL,
                    type TEXT NOT NULL,
                    value TEXT NOT NULL,
                    context TEXT DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_entities_rec ON entities(recording);
                CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type);
            """)
            try:
                self._db.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5("
                    "text, recording UNINDEXED, chunk_id UNINDEXED)")
                self.fts = True
            except sqlite3.OperationalError:
                self.fts = False  # keyword search degrades to none; semantic still works

    # ---------------- indexing ----------------

    @staticmethod
    def make_chunks(segments: list[dict]) -> list[dict]:
        """Merge transcript segments into ~CHUNK_TARGET_WORDS chunks,
        breaking on speaker changes so 'who said it' stays intact."""
        chunks, cur = [], None
        for seg in segments:
            text = (seg.get("text") or "").strip()
            if not text:
                continue
            spk = seg.get("speaker") or ""
            if (cur is None or cur["speaker"] != spk
                    or len(cur["text"].split()) >= CHUNK_TARGET_WORDS):
                if cur:
                    chunks.append(cur)
                cur = {"start": float(seg.get("start", 0)), "speaker": spk,
                       "text": text}
            else:
                cur["text"] += " " + text
        if cur:
            chunks.append(cur)
        return chunks

    def index_recording(self, name: str, segments: list[dict], *,
                        created: str = "", duration: float = 0.0) -> int:
        """(Re)index one recording's transcript for search. Returns chunk count."""
        chunks = self.make_chunks(segments)
        vecs = self.embed_fn([c["text"] for c in chunks]) if chunks else []
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO recordings(name, created, duration) VALUES(?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET created=excluded.created, "
                "duration=excluded.duration", (name, created, duration))
            self._delete_chunks(name)
            for c, v in zip(chunks, vecs):
                cur = self._db.execute(
                    "INSERT INTO chunks(recording, start, speaker, text, embedding) "
                    "VALUES(?,?,?,?,?)",
                    (name, c["start"], c["speaker"], c["text"],
                     np.asarray(v, dtype=np.float32).tobytes()))
                if self.fts:
                    self._db.execute(
                        "INSERT INTO chunks_fts(text, recording, chunk_id) "
                        "VALUES(?,?,?)", (c["text"], name, cur.lastrowid))
        return len(chunks)

    def _delete_chunks(self, name: str):
        """Caller holds the lock and transaction."""
        old = [r[0] for r in self._db.execute(
            "SELECT id FROM chunks WHERE recording=?", (name,))]
        self._db.execute("DELETE FROM chunks WHERE recording=?", (name,))
        if self.fts and old:
            self._db.executemany("DELETE FROM chunks_fts WHERE chunk_id=?",
                                 [(i,) for i in old])

    def set_enrichment(self, name: str, *, title="", category="", summary="",
                       entities: list[dict] | None = None):
        with self._lock, self._db:
            self._db.execute(
                "UPDATE recordings SET title=?, category=?, summary=?, enriched=1 "
                "WHERE name=?", (title[:200], category[:60], summary[:2000], name))
            self._db.execute("DELETE FROM entities WHERE recording=?", (name,))
            for e in entities or []:
                if e.get("type") in ENTITY_TYPES and e.get("value"):
                    self._db.execute(
                        "INSERT INTO entities(recording, type, value, context) "
                        "VALUES(?,?,?,?)",
                        (name, e["type"], str(e["value"])[:300],
                         str(e.get("context", ""))[:500]))

    def set_title(self, name: str, title: str):
        with self._lock, self._db:
            self._db.execute("UPDATE recordings SET title=? WHERE name=?",
                             (title[:200], name))

    def delete_recording(self, name: str):
        with self._lock, self._db:
            self._delete_chunks(name)
            self._db.execute("DELETE FROM entities WHERE recording=?", (name,))
            self._db.execute("DELETE FROM recordings WHERE name=?", (name,))

    # ---------------- search ----------------

    def semantic_search(self, query: str, top_k: int = 8,
                        recording: str | None = None) -> list[dict]:
        qv = np.asarray(self.embed_fn([query])[0], dtype=np.float32)
        sql = ("SELECT recording, start, speaker, text, embedding FROM chunks "
               "WHERE embedding IS NOT NULL")
        args: list = []
        if recording:
            sql += " AND recording=?"
            args.append(recording)
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        if not rows:
            return []
        mat = np.stack([np.frombuffer(r[4], dtype=np.float32) for r in rows])
        qn = qv / (np.linalg.norm(qv) or 1.0)
        norms = np.linalg.norm(mat, axis=1)
        norms[norms == 0] = 1.0
        scores = (mat @ qn) / norms
        order = np.argsort(-scores)[:top_k]
        return [{"recording": rows[i][0], "start": rows[i][1],
                 "speaker": rows[i][2], "text": rows[i][3],
                 "score": round(float(scores[i]), 3)} for i in order]

    def keyword_search(self, query: str, top_k: int = 8,
                       recording: str | None = None) -> list[dict]:
        if not self.fts or not query.strip():
            return []
        safe = " ".join('"' + t.replace('"', "") + '"'
                        for t in query.split() if t.strip('"'))
        sql = ("SELECT c.recording, c.start, c.speaker, c.text "
               "FROM chunks_fts f JOIN chunks c ON c.id = f.chunk_id "
               "WHERE chunks_fts MATCH ?")
        args: list = [safe]
        if recording:
            sql += " AND c.recording=?"
            args.append(recording)
        sql += " ORDER BY rank LIMIT ?"
        args.append(top_k)
        with self._lock:
            try:
                rows = self._db.execute(sql, args).fetchall()
            except sqlite3.OperationalError:
                return []  # unparseable query — keyword contributes nothing
        return [{"recording": r[0], "start": r[1], "speaker": r[2],
                 "text": r[3], "score": None} for r in rows]

    def search(self, query: str, top_k: int = 8,
               recording: str | None = None) -> list[dict]:
        """Hybrid: semantic + keyword, de-duplicated, semantic order first.
        Pass `recording` to scope the search to one memory."""
        sem = self.semantic_search(query, top_k, recording=recording)
        seen = {(h["recording"], h["start"]) for h in sem}
        extra = [h for h in self.keyword_search(query, top_k, recording=recording)
                 if (h["recording"], h["start"]) not in seen]
        return (sem + extra)[:top_k]

    # ---------------- reads ----------------

    _REC_COLS = ("name", "title", "category", "created", "duration",
                 "summary", "enriched")

    def recording(self, name: str) -> dict | None:
        with self._lock:
            r = self._db.execute(
                f"SELECT {','.join(self._REC_COLS)} FROM recordings "
                "WHERE name=?", (name,)).fetchone()
        return dict(zip(self._REC_COLS, r)) if r else None

    def recordings(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                f"SELECT {','.join('r.' + c for c in self._REC_COLS)}, "
                "(SELECT COUNT(*) FROM entities e WHERE e.recording = r.name "
                " AND e.type IN ('action','promise','deadline')) "
                "FROM recordings r ORDER BY r.name DESC").fetchall()
        return [dict(zip(self._REC_COLS + ("action_count",), r)) for r in rows]

    def entities(self, type: str | None = None,
                 recording: str | None = None) -> list[dict]:
        q = "SELECT recording, type, value, context FROM entities WHERE 1=1"
        args: list = []
        if type:
            q += " AND type=?"
            args.append(type)
        if recording:
            q += " AND recording=?"
            args.append(recording)
        with self._lock:
            rows = self._db.execute(q + " ORDER BY id", args).fetchall()
        return [dict(zip(("recording", "type", "value", "context"), r))
                for r in rows]

    def stats(self) -> dict:
        with self._lock:
            recs = self._db.execute("SELECT COUNT(*), COALESCE(SUM(duration),0) "
                                    "FROM recordings").fetchone()
            chunks = self._db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            by_type = dict(self._db.execute(
                "SELECT type, COUNT(*) FROM entities GROUP BY type").fetchall())
            top_people = [r[0] for r in self._db.execute(
                "SELECT value FROM entities WHERE type='person' "
                "GROUP BY LOWER(value) ORDER BY COUNT(*) DESC LIMIT 5")]
            top_topics = [r[0] for r in self._db.execute(
                "SELECT value FROM entities WHERE type='topic' "
                "GROUP BY LOWER(value) ORDER BY COUNT(*) DESC LIMIT 5")]
        return {"recordings": recs[0], "total_secs": round(recs[1], 1),
                "chunks": chunks, "entities": by_type,
                "top_people": top_people, "top_topics": top_topics}

    def close(self):
        with self._lock, contextlib.suppress(Exception):
            self._db.close()
