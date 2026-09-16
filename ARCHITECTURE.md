# MemoryOS — Architecture

Version 0.3 · 2026-07-04 · Single source of truth for system design.

## Native Android preview — 2026-09-16

The current Android launcher is native Kotlin, rather than the planned React Native client below. `OmiActivity` scans and controls `OmiService`, a connected-device foreground service. BLE notifications are assembled and decoded by `OmiAudio` into private WAV files; the activity plays completed recordings locally. Stop preserves the Bluetooth connection. Disconnect saves received audio and requires manual reconnection. No upload or backend transcription is connected to this launcher yet.

Opus output is saved at the Android decoder's actual sample rate (normally 48 kHz); future backend ingestion must read the WAV header and normalize audio rather than assume the desktop pipeline's 16 kHz. Recording uses a wake lock and stops at two hours. Decode and disk writes currently share the main looper, a limitation to address and measure before all-day use. The earlier WebView/microphone client remains internal.

## 1. System overview

```
┌──────────────────────────── CLIENTS ─────────────────────────────┐
│  Glass Web UI (Phase 1) → Electron shell (Phase 2, same UI)      │
│  React Native mobile (Phase 3) — all clients speak one API       │
└───────────────────────────────┬──────────────────────────────────┘
                                │ REST + WebSocket (localhost / LAN TLS)
┌───────────────────────────────▼──────────────────────────────────┐
│                 MEMORYOS CORE — FastAPI, local-first             │
│                                                                  │
│  capture     BLE Omi link · browser-mic ingest · chunker         │
│  transcribe  live engine (local small / Groq large-v3 + fallback)│
│              polish (distil-large-v3 / Groq) · speaker labels    │
│  memory      SQLite: recordings, chunks(+embeddings), entities,  │
│              FTS5 keyword index                                  │
│  enrich      title/category/summary/entity extraction (LLM)      │
│  ask         hybrid retrieval → answer synthesis with citations  │
│  sync        (Phase 3) encrypted push/pull to Postgres           │
└──────────────────────────────────────────────────────────────────┘
```

## 2. Module map (current repo layout)

| File | Responsibility |
|---|---|
| `server.py` | FastAPI app: routes, Hub (live pipeline state), OmiLink (BLE), Polisher (offline re-transcription jobs) |
| `omi_transcribe.py` | Protocol + DSP primitives: Opus/BLE decoding, PCM decoding, silence-aware Chunker; standalone CLI |
| `speakers.py` | Optional diarization (resemblyzer embeddings + clustering) |
| `certs.py` | Self-signed TLS for phone capture |
| `memory.py` | (M1) MemoryStore: schema, indexing, hybrid search, entities, stats |
| `enrich.py` | (M2) LLM enrichment + heuristic fallback |
| `static/index.html` | Glass UI (single-file client by design — Electron wraps it verbatim in Phase 2) |
| `tests/` | pytest suite; fast tests use stubbed engines, e2e tests use real models |

Design pattern: **engines are injected/registried** (`ENGINES` dict for
polish, `live_engine` strategy on Hub, `embed_fn` injectable on MemoryStore)
so tests substitute fakes and providers can be swapped without touching
callers (dependency-inversion in practice).

## 3. Data model

### 3.1 Filesystem (audio + transcripts stay as files — trivially portable)
```
sessions/
  session_YYYYMMDD_HHMMSS.wav            full audio (16k mono PCM16)
  session_YYYYMMDD_HHMMSS.txt            live transcript
  session_YYYYMMDD_HHMMSS.jsonl          live transcript, structured
  session_YYYYMMDD_HHMMSS.polished.txt   big-model transcript (+speakers)
  session_YYYYMMDD_HHMMSS.summary.md     LLM summary (optional)
  session_YYYYMMDD_HHMMSS.meta.json      {title}
vocab.txt                                user glossary
certs/                                   self-signed TLS
```

### 3.2 SQLite `memoryos.db` (M1)
```sql
recordings(name PK, title, category, created, duration, summary, enriched)
chunks(id PK, recording, start, speaker, text, embedding BLOB)  -- 384-f32
chunks_fts(text, recording, chunk_id)   -- FTS5 virtual table
entities(id PK, recording, type, value, context)
-- type ∈ person|project|topic|action|promise|decision|deadline|question
```
Rationale: chunks ≈ 60 words, split on speaker change, so retrieval hits
carry "who said it, when." Embeddings stored inline as BLOBs; at
single-user scale (≤100k chunks) brute-force cosine in numpy is <50ms —
no vector DB until sync-scale demands it.

## 4. Data flow

```
audio source (Omi BLE / browser mic)
  → Chunker (silence-aware, 6–20s)
  → Hub worker: live engine (Groq large-v3 if key → local small fallback)
      prompt = user vocab glossary + last-line context
  → live transcript line → WS broadcast + session files
session end
  → auto-polish (≥3s audio): distil-large-v3 or Groq → speaker labels
  → on polish success: MemoryStore.index_recording(chunks+embeddings+FTS)
  → enrich (M2): title/category/summary/entities → recordings+entities rows
ask (M3)
  → hybrid retrieval (semantic top-k ∪ keyword) → prompt with excerpts
  → Groq LLM answer with [recording @ mm:ss] citations
  → no key: return excerpts directly (retrieval-only mode)
```

## 5. API surface

### Existing
```
GET  /                    UI          GET  /status              state+engines
WS   /ws                  events      WS   /ws/audio            mic PCM ingest
POST /omi/connect|disconnect
GET  /sessions?q=         list        PATCH /sessions/{n}       rename
GET  /sessions/{n}/text|audio|polished|summary
POST /sessions/{n}/polish?engine=     POST /sessions/{n}/summarize
DELETE /sessions/{n}
GET/PUT /vocab
```

### Phase 1 additions (M1–M3)
```
GET  /memory/search?q=&k=       hybrid search → hits with citations
POST /ask {question}            answer + citations + mode (llm|retrieval)
GET  /memory/recordings         enriched recording list (timeline source)
GET  /memory/entities?type=&recording=
GET  /memory/stats              counts, hours, top people/topics
POST /memory/reindex/{name}     manual (re)index escape hatch
```
Errors: 400 invalid input, 404 unknown recording, 409 conflicting job,
503 memory engine unavailable (embeddings missing). All errors JSON
`{detail}` with actionable hints.

## 6. AI pipeline decisions (per AI Development Rules)

| Concern | Decision |
|---|---|
| Models | STT live: Groq `whisper-large-v3` → local `small` fallback. Polish: local `distil-large-v3` / Groq. Embeddings: local `all-MiniLM-L6-v2` (384-d). LLM (enrichment/Ask/summaries): **Gemini `gemini-2.5-flash` primary → Groq `llama-3.3-70b` fallback** via `enrich.llm_chat()`; keys loaded from `.env` (gitignored). |
| Prompt strategy | Enrichment: single JSON-schema extraction prompt per session (1 call). Ask: retrieved excerpts with source tags + instruction to cite; refuse when excerpts are insufficient. Vocab glossary prepended to STT prompts. |
| Token/cost optimization | Transcripts truncated to 24k chars for enrichment; Ask sends only top-k chunks (~1.5k tokens), not transcripts. All LLM usage is free-tier Groq; $0 baseline. |
| Caching | Enrichment cached in `recordings` (enriched flag); embeddings computed once per chunk at index time; embedder model loaded once per process. |
| Fallbacks | No key → heuristic titles, retrieval-only Ask, local STT. Groq error → silent local fallback (live) or job error surfaced (polish). |
| Privacy | Local-only by default. With a Groq key, audio chunks (live/polish) and transcript excerpts (enrich/ask) go to Groq — documented in README; user opts in by setting the key. Embeddings always local. |

## 7. Security model

- Phase 1: local process, binds 0.0.0.0 for LAN phone use; TLS via
  self-signed cert (`--https`). Session names strictly validated
  (`SESSION_NAME_RE`) against path traversal. No auth (single-user LAN);
  documented risk: anyone on the LAN can reach the API when exposed —
  Phase 2 adds a pairing token for non-localhost clients.
- Phase 3: SQLCipher-or-OS-keystore decision, E2E-encrypted sync payloads,
  biometric/PIN via client OS APIs.

## 8. Scalability posture

Single-user local core stays simple (one SQLite writer, threaded jobs).
The API boundary is the scaling seam: sync service (Postgres, per-user
row keys) is additive; clients never talk to Postgres directly. Vector
search upgrades (sqlite-vec / pgvector) slot behind `MemoryStore.search()`
without API changes.

## 9. Testing strategy

- Fast tests: stub engines/embedders, tmp dirs, no network (all mocked).
- e2e (`-m e2e`): real whisper models over TTS fixtures.
- Every feature ships with tests before it's marked complete (see rules).
