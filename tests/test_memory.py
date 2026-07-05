"""M1 memory engine: chunking, indexing, hybrid search, entities, deletion."""

import numpy as np
import pytest

from memory import ENTITY_TYPES, MemoryStore


def fake_embedder(texts):
    """Deterministic 8-dim 'embeddings': bag-of-chars, so related strings
    (sharing words) land near each other. Good enough to test ranking."""
    out = []
    for t in texts:
        v = np.zeros(8, dtype=np.float32)
        for i, ch in enumerate(t.lower()):
            v[(ord(ch) + i) % 8] += 1
        out.append(v / (np.linalg.norm(v) or 1))
    return np.stack(out)


@pytest.fixture()
def store(tmp_path):
    s = MemoryStore(tmp_path / "test.db", embed_fn=fake_embedder)
    yield s
    s.close()


SEGS = [
    {"start": 0.0, "end": 4.0, "speaker": "Speaker 1",
     "text": "Rahul said the pricing should be twenty dollars per month."},
    {"start": 4.0, "end": 8.0, "speaker": "Speaker 2",
     "text": "I promised to send the hiring plan by Friday."},
    {"start": 8.0, "end": 12.0, "speaker": "Speaker 2",
     "text": "Also the roadmap for the mobile app needs review."},
]


def test_chunking_breaks_on_speaker_change():
    chunks = MemoryStore.make_chunks(SEGS)
    assert len(chunks) == 2  # speaker 2's two segments merge
    assert chunks[0]["speaker"] == "Speaker 1"
    assert "hiring plan" in chunks[1]["text"] and "roadmap" in chunks[1]["text"]
    assert chunks[1]["start"] == 4.0


def test_chunking_splits_long_monologues():
    long = [{"start": float(i), "text": "word " * 30, "speaker": "S1"}
            for i in range(6)]  # 180 words same speaker
    chunks = MemoryStore.make_chunks(long)
    assert len(chunks) >= 3
    assert all(len(c["text"].split()) <= 90 for c in chunks)


def test_index_and_search_roundtrip(store):
    n = store.index_recording("session_20260101_000000", SEGS,
                              created="2026-01-01 00:00", duration=12.0)
    assert n == 2
    # hybrid search must surface the pricing chunk (exact rank depends on
    # the embedder; the real-model ranking test is the e2e below)
    hits = store.search("pricing dollars", top_k=3)
    hit = next(h for h in hits if "pricing" in h["text"])
    assert hit["recording"] == "session_20260101_000000"
    assert hit["speaker"] == "Speaker 1"

    # keyword search finds exact words semantic fake-embedder might miss
    kw = store.keyword_search("hiring")
    assert kw and "hiring" in kw[0]["text"]


def test_reindex_replaces_not_duplicates(store):
    store.index_recording("session_20260101_000000", SEGS)
    store.index_recording("session_20260101_000000", SEGS[:1])
    assert store.stats()["chunks"] == 1
    assert store.keyword_search("hiring") == []  # old chunk gone from FTS too


def test_empty_and_blank_segments(store):
    assert store.index_recording("session_20260102_000000", []) == 0
    assert store.index_recording("session_20260103_000000",
                                 [{"start": 0, "text": "   "}]) == 0
    assert store.search("anything") == []


def test_enrichment_and_entities(store):
    store.index_recording("session_20260101_000000", SEGS)
    store.set_enrichment(
        "session_20260101_000000",
        title="Pricing sync with Rahul", category="meeting",
        summary="Discussed pricing and hiring.",
        entities=[
            {"type": "person", "value": "Rahul", "context": "pricing"},
            {"type": "promise", "value": "send hiring plan by Friday"},
            {"type": "bogus-type", "value": "ignored"},
            {"type": "topic", "value": ""},   # empty value ignored
        ])
    rec = store.recording("session_20260101_000000")
    assert rec["title"] == "Pricing sync with Rahul" and rec["enriched"] == 1
    ents = store.entities(recording="session_20260101_000000")
    assert {e["type"] for e in ents} == {"person", "promise"}
    assert store.entities(type="person")[0]["value"] == "Rahul"


def test_delete_cascades(store):
    store.index_recording("session_20260101_000000", SEGS)
    store.set_enrichment("session_20260101_000000", title="x",
                         entities=[{"type": "person", "value": "Rahul"}])
    store.delete_recording("session_20260101_000000")
    assert store.recordings() == []
    assert store.entities() == []
    assert store.search("pricing") == []
    assert store.stats()["chunks"] == 0


def test_stats(store):
    store.index_recording("session_20260101_000000", SEGS, duration=12.0)
    store.set_enrichment("session_20260101_000000", entities=[
        {"type": "person", "value": "Rahul"},
        {"type": "person", "value": "rahul"},   # case-folded in top lists
        {"type": "topic", "value": "pricing"},
    ])
    s = store.stats()
    assert s["recordings"] == 1 and s["total_secs"] == 12.0
    assert s["entities"]["person"] == 2
    assert [p.lower() for p in s["top_people"]] == ["rahul"]


def test_entity_types_frozen():
    # the API contract in ARCHITECTURE.md §3.2
    assert set(ENTITY_TYPES) == {"person", "project", "topic", "action",
                                 "promise", "decision", "deadline", "question"}


@pytest.mark.e2e
def test_real_embeddings_semantics(tmp_path):
    """Real MiniLM: 'cost of the product' should hit the pricing chunk."""
    from memory import default_embedder, embeddings_available

    if not embeddings_available():
        pytest.skip("sentence-transformers not installed")
    s = MemoryStore(tmp_path / "real.db", embed_fn=default_embedder)
    s.index_recording("session_20260101_000000", SEGS)
    hits = s.semantic_search("what is the cost of the product?", top_k=1)
    assert hits and "pricing" in hits[0]["text"]
    s.close()
