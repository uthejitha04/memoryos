# MemoryOS

An AI Memory Operating System — a second brain that remembers everything you
hear, discuss, learn, and promise. Local-first, privacy-first, free-tier
viable. Grew out of (and still contains) a best-in-class transcriber for the
[Omi](https://github.com/BasedHardware/omi) pendant; works with any
microphone too.

Docs: [PRD.md](PRD.md) · [ARCHITECTURE.md](ARCHITECTURE.md) ·
[DESIGN_SYSTEM.md](DESIGN_SYSTEM.md) · [ROADMAP.md](ROADMAP.md)

## Phone uploads in the web library — 2026-10-26

The main `server.py` API now imports completed `/mobile/recordings/{sha256}`
uploads into Memories, Search and Ask. Original WAV bytes/sample rate and
transcript segment offsets are preserved. Retrying the same upload reuses one
memory. Imports use local embeddings and a simple transcript-derived title;
they do not automatically call an AI provider or sync to another server.

The upload response/status includes `library.status` (`pending`, `ready`,
`failed`, `deleted` or `not_imported`) separately from transcription status.
`POST /mobile/recordings/{sha256}/library` retries indexing a completed
transcript without retranscription; it requires the same `x-memoryos-token`
header as uploads. Pending imports recover when the main server restarts.
Previously completed uploads can be imported explicitly with that endpoint;
existing personal uploads are not automatically migrated.

Imported dates represent first library submission in UTC, not the original
phone capture time. Deleting a web memory removes its library copy and index
and prevents retry/restart from bringing it back. **The uploaded source files
under `mobile/` and the phone's local recording are retained** in this preview;
this is not an erase-everywhere action. A retention/purge workflow is still needed.

This bridge is implemented only in the main server. The existing dedicated
`mobile_server.py` laptop service remains transcription/summary-only. Existing
phone pairing is unchanged; main-server pairing and physical acceptance remain
next steps. Deployment is not implied. Use one server process per data directory.

## The memory engine (Phase 1)

Every recording is transcribed, polished, then automatically **indexed into
a local memory store** (SQLite + MiniLM embeddings — nothing leaves your
machine) and **enriched** (AI title, category, summary, and extracted
people, projects, topics, action items, promises, decisions, deadlines).

- **Memories** — glass timeline of everything captured: search, replay,
  polish, entities, action items.
- **Ask** — "What promises did I make?" → hybrid semantic+keyword retrieval
  → answer with citations `[title · mm:ss]`. Without a Groq key you get the
  matching excerpts; with one you get synthesized answers.
- APIs: `/memory/search`, `/ask`, `/memory/recordings`, `/memory/entities`,
  `/memory/stats`, `/memory/reindex/{name}`.

## The app (server + web UI)

```
Omi pendant --BLE--> ┐
                     ├--> server.py --> Opus decode --> faster-whisper --> live web UI
laptop mic --------> ┘                                       └--> sessions/*.txt
```

### Setup

The laptop app now uses the Python Bluetooth client when opened on localhost,
including inside Electron. Turn on Windows Bluetooth, charge the pendant, and
disconnect it from the official Omi app before choosing **Connect Omi**.
Only one capture source can record at a time. If the pendant drops out, received
audio remains in its WAV file and the client attempts to reconnect up to five
times; the status explicitly identifies the recording gap. Audio generated
while the pendant is disconnected is not recovered from device storage.

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

The requirements include the local memory engine. Prepare the models while
online once, then use transcription and memory retrieval offline:

```powershell
.venv\Scripts\python -c "from memory import default_embedder; default_embedder(['setup'])"
.venv\Scripts\python -c "from faster_whisper import WhisperModel; WhisperModel('small'); WhisperModel('distil-large-v3')"
```

Without an AI provider key, Ask returns relevant saved excerpts with citations;
it does not generate new answers or AI summaries. The embedding, live, and
polish models were installed and tested on this computer during the repair.

### Verified capture and citations

The isolated recording-to-Ask flow was verified on 2026-09-15 using synthetic speech and the real local Whisper and MiniLM models. New live recordings preserve audio offsets, so citations can seek to the retrieved chunk without waiting for polishing. Recovery and reindexing preserve those offsets. Older transcripts without offsets still fall back to the beginning until polished.

The user verified physical Omi speech capture and connection retention after stopping. All-day mobile capture and interruption recovery remain unverified. See [Development](DEVELOPMENT.md) for the reproducible checks and their limits.

### Run

```powershell
start.bat                                 # or: .venv\Scripts\python server.py
```

Opens http://localhost:8090. In the app:

- **Live tab**
  - **Connect Omi** — connects the pendant without recording. Then press **Start recording**.
    (First disconnect it from the Omi phone app — BLE allows one connection
    at a time. Pendant must be charged and nearby.)
  - **Device picker + Record microphone** — use any input: built-in mic, headset,
    USB/Bluetooth mic. Device names appear after the first recording grants
    mic permission.
  - A red **REC** timer shows while a session is recording.
- **Sessions tab** — every session saves the **full audio (WAV)** plus the
  transcript (txt + jsonl). Browse, search, replay in-page, download,
  rename (✏️), or delete.
  - **✨ Polish** — re-transcribes the saved audio with `distil-large-v3`
    (much more accurate than the live model) with a progress bar; the
    polished transcript appears alongside the live one. If `resemblyzer`
    is installed (`pip install resemblyzer` — pulls PyTorch), polished
    lines get **Speaker 1 / Speaker 2** labels via voice-embedding
    clustering.
  - **📝 Summarize** — summary + action items (needs `GROQ_API_KEY`).
- **Search** — the Sessions tab search box matches titles and full
  transcript text (live + polished).

### Phone recording (HTTPS)

Browsers only allow mic capture on secure origins, so for the phone run:

```powershell
.venv\Scripts\python server.py --https
```

A self-signed certificate is generated into `./certs` (SANs include your LAN
IPs). On the phone open `https://<laptop-ip>:8090`, accept the certificate
warning once, and Record works from the phone's mic/headset. Without
`--https`, phones can still watch transcripts read-only over plain HTTP.

### AI and voice models (.env)

Put keys in `.env` (git-ignored) and restart. `NVIDIA_API_KEY` makes Nemotron
(`nvidia/nemotron-3.5-lightning-30b-a3b`, override with `NVIDIA_MODEL`) the only
model for Ask, titles, enrichment and summaries. `GROQ_API_KEY` switches live
transcription and polish to Groq `whisper-large-v3`, which auto-detects
language per chunk. Both send data to those cloud providers.

### Optional: Groq free tier (cloud accuracy + summaries)

Get a free API key at https://console.groq.com and set it before starting:

```powershell
$env:GROQ_API_KEY = "gsk_..."
```

This unlocks: polish engine dropdown (`whisper-large-v3` on Groq — best
accuracy available, faster than local) and 📝 Summarize (Llama 3.3 70B).
Everything else works fully offline without it.

### Options

```powershell
.venv\Scripts\python server.py --model small --port 8090
```

Models (CPU, int8): `small` = fast + good (default sweet spot for live use),
`distil-large-v3` / `large-v3` = best accuracy but slower, `tiny`/`base` = rough
drafts. First use of a model downloads it to the HF cache.

### Desktop app (Electron)

```powershell
MemoryOS.bat        # native window; starts the core automatically
```
First run: `cd desktop && npm install` (done during setup).

### Cloud deploy (Stage B)

The repo ships a `Dockerfile` + `railway.toml`. On Railway: New Project →
Deploy from GitHub repo → set env vars from `.env` → it builds and serves
`/status` as the healthcheck. Phone + laptop then share one public URL.

### CLI-only version

`omi_transcribe.py` is a standalone script that does BLE -> Whisper -> console,
no server:

```powershell
.venv\Scripts\python omi_transcribe.py --list      # scan BLE devices
.venv\Scripts\python omi_transcribe.py             # connect + transcribe
```

## How it talks to the Omi (open-source protocol)

- Audio service `19b10000-e8f2-537e-4f6c-d104768a1214`
- Audio characteristic `19b10001-...` (notify): each notification is
  `[packet_id u16 LE][frame_index u8][audio fragment]`; the packet counter
  increments for every notification, while index zero starts a new frame.
  Duplicate notifications are ignored; gaps discard the partial frame until
  the next frame start. The final unconfirmed frame is discarded on disconnect.
- Codec characteristic `19b10002-...` (read): 0/1 = PCM16 16k/8k, 20/21 = Opus 16k.

## Roadmap

- Phase 3: HTTPS (self-signed) so the phone browser can stream its mic;
  optional Groq free-tier Whisper large-v3 for cloud-quality accuracy;
  speaker diarization; summaries.

<!-- repository-guide:start -->
## Repository guide

- [Agent instructions](AGENTS.md)
- [Founder mission](FOUNDER.md)
- [Current status and session history](LOG.md)
- [Architecture](ARCHITECTURE.md)
- [Roadmap](ROADMAP.md)
- [Setup and verification](DEVELOPMENT.md)
<!-- repository-guide:end -->

### Live recording controls

**Connect Omi** connects the pendant without recording. **Start recording** captures Omi audio; **Stop recording** saves the session and leaves Bluetooth connected. **Disconnect Omi** explicitly closes the connection (and stops any active Omi recording). Idle audio is discarded, and a new recording starts a fresh session. Connection loss stops recording; after reconnection, press Start recording again.

Omi is the primary source. Computer/phone microphone controls are under **Use a computer or phone microphone instead**; only one source can record at a time. Live transcripts arrive after roughly 6–20 seconds of audio plus processing time. Captions distinguish audio duration from processing time, which excludes queue wait.

After updating this version, restart the Python server and refresh the page: the separate recording controls require the new backend endpoints.

### Native Android Omi preview

The Android launcher now connects directly to an Omi pendant and saves PCM/Opus audio as private WAV files, with separate connection and recording controls and local playback. See [Android setup and limits](android/README.md). The 0.3 preview adds explicit HTTPS upload to a paired laptop and local Whisper transcription, with text cached on the phone. The user verified pairing, upload and accurate transcript display on the phone; the desktop memory database remains separate. On 2026-09-16 the user confirmed clear recording/playback, recording with the screen locked nearby, and a short walk carrying the phone without USB. Long-duration reliability and reconnection remain to verify.
