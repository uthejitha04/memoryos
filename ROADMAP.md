# ROADMAP — MemoryOS

## Native Android next steps — 2026-09-16

- [x] Direct Omi BLE capture, separate start/stop, private WAV storage and playback.
- [x] User verified clear playback, nearby locked-screen recording, and an unplugged walk carrying the phone.
- [x] Authenticated, retryable recording transfer that preserves the phone's local copy.
- [x] Backend WAV ingestion and transcription with explicit processing status in the app.
- [x] Display transcripts on the phone; user verified accuracy.
- [ ] Summaries, action items and organized memories on the phone.
- [ ] Automatic reconnect, interruption recovery, background audio processing and longer battery/storage tests before daily reliance.

## Phase 1 — Memory engine (M1–M5) ✅ 2026-07-04

- [x] **M1** Memory store: SQLite (recordings/chunks/entities), local MiniLM
      embeddings, FTS5 keyword index, hybrid search, cascade deletes.
      10 tests incl. real-embedding semantics.
- [x] **M2** Enrichment: one-call LLM extraction (title/category/summary/
      entities) with heuristic fallback; auto-runs after every polish;
      user titles always win. Mocked-LLM + fallback tests.
- [x] **M3** Ask: `POST /ask` hybrid retrieval → cited answer (LLM) or
      honest excerpts (no key). Endpoint + unit tests.
- [x] **M4** Glass UI per DESIGN_SYSTEM.md: aurora background, frosted
      cards, floating nav (Live/Memories/Ask), timeline with stats strip,
      inline title editing, morph-confirm delete, skeletons, empty/error
      states, reduced-motion support, aria labels.
- [x] **M5** 58-test suite green; live walkthrough on real data (3 sessions
      indexed, semantic search + Ask verified in UI); docs updated.

Phase 2 (planned): Electron packaging, analytics view, daily/weekly digests,
audio import, scoped chat (per person/project), light mode.
Phase 3 (planned): React Native mobile, encrypted sync, biometric lock.

---

# Historical: v0.2 capture core (T1–T8)

Goal: a complete local transcription app around the Omi pendant (and any mic),
with best-possible free transcription, recordings, sessions, and phone support.

Already shipped (v1): BLE Omi client (open-source protocol), Opus decode,
silence-aware chunking, faster-whisper CPU pipeline, FastAPI server, web UI
with device picker, full-audio WAV recording, sessions library (view / replay /
download / delete).

---

## T1 — Automated test suite (foundation) ✅

Build `tests/` with pytest so every later task has a safety net.

- [x] Unit: `Chunker` — cuts at silence after MIN_CHUNK_SECS, force-cuts at
      MAX_CHUNK_SECS, skips all-silence chunks, `flush()` emits remainder.
- [x] Unit: `OpusStreamDecoder` — reassembles fragmented BLE packets
      (multi-fragment frames), survives corrupted frames without dying,
      round-trips audio (encode with PyAV libopus → decode → RMS sanity).
- [x] API: FastAPI `TestClient` with a stubbed Whisper worker — `/status`,
      `/sessions` list shape, text/audio endpoints, delete, 409 on deleting
      an active session, 400 on bad session names (path traversal guard).
- [x] E2E: stream a TTS wav into `/ws/audio` (real server, real `small`
      model) → assert transcript contains expected key phrases, WAV saved
      with ≥ 90% of streamed duration.

**Proof:** `pytest` green locally; e2e prints the transcript it matched.
Bonus: the suite caught 3 real bugs (broadcast on closed event loop, flush
emitting pure silence, sessions with no speech never listed).

## T2 — "Polish" pass: re-transcribe sessions with a big model ✅

Live uses `small` (keeps up in realtime); saved WAVs deserve
`distil-large-v3` quality after the fact.

- [x] `POST /sessions/{name}/polish` — background job re-transcribes the WAV
      with distil-large-v3, writes `session_*.polished.txt`, progress pushed
      over the events websocket.
- [x] Session detail UI: "✨ Polish" button with progress; polished
      transcript shown above the live one and downloadable.
- [x] Reject polish while that session is still recording / already running.

**Proof:** pytest (fake engine: happy path, rejections, delete cleanup) +
real-model e2e over the TTS fixture. Verified in the UI: polished output
corrected "Omitranscriber" → "Omi transcriber".

## T3 — HTTPS so the phone can record too ✅

Browser mic capture requires a secure context; LAN IPs over HTTP are blocked.

- [x] Self-signed cert generated on demand (`cryptography`) into `./certs/`,
      SANs cover localhost + all LAN IPs; reused on later runs.
- [x] `--https` flag → uvicorn serves TLS; UI websockets switch to `wss:`
      automatically.
- [x] README: phone steps (open `https://<ip>:8090`, accept warning once).

**Proof:** pytest — SAN check incl. LAN IPs, cert reuse, real TLS server
serves `/status`, plain HTTP against the TLS port fails.

## T4 — Session quality-of-life ✅

- [x] Rename: `PATCH /sessions/{name}` title in `meta.json` sidecar; shown
      in list; ✏️ Rename button in detail.
- [x] Search: `GET /sessions?q=` filters by title + live + polished
      transcript text; live search box in the Sessions tab.

**Proof:** pytest — rename shows in list, 404 on missing session, meta
deleted with session; search hits content and titles case-insensitively.

## T5 — Optional cloud engine (Groq free tier) for max accuracy ✅

- [x] Engine abstraction `ENGINES = {local, groq}` for polish.
- [x] Groq path: WAV split into ≤10-min chunks, POSTed to Groq
      `whisper-large-v3`, timestamps stitched with offsets. No key → 400
      with a setup hint. `/status` reports whether Groq is configured.
- [x] UI: engine dropdown next to Polish, only when Groq is configured.

**Proof:** pytest — no-key 400 with hint; mocked httpx verifies auth header,
model, response_format, valid RIFF chunks, 2 calls for 3s audio at 2s chunk
size, correct timestamp stitching; API errors surface as job status without
leaving a polished file behind.

## T6 — Summaries & action items ✅

- [x] `POST /sessions/{name}/summarize` — Groq Llama 3.3 70B over the
      polished (preferred) or live transcript → `session_*.summary.md`;
      400 hint without a key; 400 on empty transcript.
- [x] UI: 📝 Summarize button (shown when Groq configured) + summary panel.

**Proof:** pytest with mocked LLM — request shape, file written and served,
removed on delete; both 400 cases.

## T7 — Speaker labels (stretch) ✅

- [x] `speakers.py`: resemblyzer voice embeddings per segment +
      scipy agglomerative clustering → "Speaker N" prefixes in polished
      transcripts; fully optional (graceful no-op when not installed).
      Torch 2.12 installs fine on Python 3.14.
- [x] 2-voice TTS fixture test (David + Zira): exactly 2 speakers detected,
      collapsed turn sequence = Speaker 1 → Speaker 2 → Speaker 1.

## T8 — Ship it ✅

- [x] README updated for every feature above.
- [x] Full test suite green: **28 passed** (incl. both real-model e2e tests
      and the speaker test).
- [x] Fresh-start smoke on a restarted server: stream speech → session
      recorded; rename → title in list; search "coffee" → hits; polish →
      speaker-labeled distil-large-v3 transcript; delete → gone; UI detail
      view shows player + polished + live transcripts.
