# MemoryOS — Product Requirements Document

Version 0.3 · 2026-07-04 · Status: Phase 1 in design review

## 1. Vision

MemoryOS is an AI Memory Operating System — a second brain that remembers
everything the user hears, discusses, learns, and promises. It is not a
note-taking app and not a voice recorder: recordings are raw material; the
product is *recall*.

**North-star interaction:** the user asks, in plain language, "What did Rahul
say about pricing?" — and gets an accurate, cited answer drawn from every
conversation they've ever captured.

## 2. Principles

1. **Local-first.** Everything works offline. Cloud enhances, never replaces.
2. **Privacy-first.** Audio and transcripts never leave the device unless the
   user explicitly enables a cloud feature (and sees exactly what it sends).
3. **Apple-level quality.** Minimal, fluid, premium (see DESIGN_SYSTEM.md).
4. **Free-tier viable.** Core value works with zero paid API dependencies.

## 3. Personas & top user stories

**P1 — The builder** (our first user): records ideas, standups, calls.
- *As a builder, I want every recording transcribed accurately so I never
  re-listen to audio.* ✅ shipped
- *I want to ask questions across all my conversations and get cited
  answers.* → Phase 1 (M3)
- *I want action items and promises extracted automatically so nothing slips.*
  → Phase 1 (M2)

**P2 — The meeting-heavy professional**: back-to-back calls.
- *I want each meeting auto-titled and categorized so my timeline is
  scannable.* → Phase 1 (M2)
- *I want to see everything discussed with a specific person.* → Phase 1 (M3)
- *I want weekly summaries of what happened.* → Phase 2

**P3 — The wearable user**: Omi pendant all day.
- *I want all-day capture with battery-efficient BLE streaming.* ✅ shipped
- *I want my phone to capture when away from the laptop.* ✅ shipped (HTTPS)

## 4. Feature inventory

### Shipped (v0.2 — "capture core")
| Feature | Notes |
|---|---|
| Live transcription | faster-whisper `small` local; Groq large-v3 live when key set; auto-fallback |
| Omi wearable support | open-source BLE protocol, Opus decode |
| Any-mic capture | device picker; laptop, headset, USB, Bluetooth |
| Phone capture | `--https` self-signed cert flow |
| Sessions | full WAV recording + transcript (txt/jsonl), rename, delete |
| Polish | offline re-transcription with distil-large-v3 or Groq large-v3 |
| Speaker labels | resemblyzer embeddings + clustering (optional dep) |
| Auto-polish | fires on session end ≥3s |
| Custom vocabulary | user glossary biases live + polish via prompt |
| Keyword search | over titles + transcripts |
| Summaries | Groq LLM per session (optional key) |
| Test suite | 33 tests incl. 2 real-model e2e |

### Phase 1 (current — "the memory engine")
| ID | Feature | Acceptance criteria |
|---|---|---|
| M1 | Memory index | Every polished session is chunked, embedded (local MiniLM), FTS-indexed and entity-stored in SQLite within 30s of polish completing. Deleting a session removes all its memory rows. |
| M2 | AI enrichment | Sessions get auto title (≤60 chars), category, summary, and extracted entities (people, projects, topics, actions, promises, decisions, deadlines, questions). Without a Groq key: heuristic title from first words; no fake entities. Titles remain user-editable. |
| M3 | Ask | `POST /ask` answers natural-language questions with citations (recording + timestamp) using hybrid retrieval (semantic + keyword). Without a key: returns the retrieved excerpts labeled as such. Median latency ≤4s with key, ≤1s without. |
| M4 | Glass UI | MemoryOS rebrand per DESIGN_SYSTEM.md: timeline of glass cards (title, date, duration, category, people, action count), Ask view, floating nav. All existing controls preserved. Loading/empty/error states on every view. |
| M5 | Verification | Full pytest suite green; scripted walkthrough: record → auto-polish → auto-index → search → ask, on a fresh server. Docs updated. |

### Phase 2 (current)
| ID | Feature | Acceptance criteria |
|---|---|---|
| P1 | Per-memory AI chat | Inside a memory's detail view, the user can ask questions scoped to that recording only ("what did we say about X?") and get cited answers — essential for very long recordings. `/ask` accepts an optional `recording` param. |
| P2 | Action items everywhere | Timeline cards show an action-item count badge; detail view lists actions/promises/deadlines (already) and the AI summary includes an Action Items section. Existing sessions re-enriched now that AI keys exist. |
| P3 | Audio download | Every memory with audio is downloadable as WAV from detail view (⬇ audio). |
| P4 | Git repository | All code version-controlled with an initial commit before anything goes public. |
| P5 | Security hardening | Non-localhost API access requires a pairing token; basic rate limiting; security headers. |
| P6 | Installable phone app (PWA) | manifest + service worker; "Add to Home Screen" gives a full-screen app with icon. |
| P7 | Electron desktop app | MemoryOS.exe — double-click launch, no terminal. |
| P8 | Pinned conversations | User can pin/unpin memories (📌). Pinned appear first in the timeline. Hard cap of 10 pins; attempting an 11th returns a clear error. Stored in the meta sidecar so it works even without the memory engine. |

Deferred in Phase 2: analytics view, digests, audio import, light mode,
Google **Tasks** export (Keep has no public consumer API — documented
limitation), landing page, Supabase accounts + Railway deploy (Stage B,
needs user accounts).

### Phase 3 (later)
- React Native mobile app (Android first), reusing the same API
- Encrypted cloud sync (Postgres), conflict resolution, E2E encryption
- Biometric/PIN lock (Electron/RN keychain layer)
- Browser extension, web app
- Calendar integration, reminders

## 5. Non-goals (explicit)
- No always-on cloud dependency; no accounts required for local use.
- No Material/Bootstrap-style UI.
- No speculative multi-tenant SaaS backend until sync ships.

## 6. Success metrics (Phase 1)
- Ask returns a correct, cited answer on the scripted test corpus (automated).
- Zero data loss across restart (sessions + memory DB survive).
- All features degrade gracefully with no Groq key.

## 7. Open questions
- Groq free-tier rate limits under heavy use → measure in Phase 2; fallback
  chain already designed (Groq → local).
- SQLCipher vs OS-level disk encryption for at-rest encryption → decide in
  Phase 3 with the sync design.
