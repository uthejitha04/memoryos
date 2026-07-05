# MemoryOS

An AI Memory Operating System — a second brain that remembers everything you
hear, discuss, learn, and promise. Local-first, privacy-first, free-tier
viable. Grew out of (and still contains) a best-in-class transcriber for the
[Omi](https://github.com/BasedHardware/omi) pendant; works with any
microphone too.

Docs: [PRD.md](PRD.md) · [ARCHITECTURE.md](ARCHITECTURE.md) ·
[DESIGN_SYSTEM.md](DESIGN_SYSTEM.md) · [ROADMAP.md](ROADMAP.md)

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

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

### Run

```powershell
start.bat                                 # or: .venv\Scripts\python server.py
```

Opens http://localhost:8090. In the app:

- **Live tab**
  - **Connect Omi** — scans for the pendant and streams its audio.
    (First disconnect it from the Omi phone app — BLE allows one connection
    at a time. Pendant must be charged and nearby.)
  - **Device picker + 🎙️ Record** — use any input: built-in mic, headset,
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
  `[packet_id u16 LE][frame_index u8][opus fragment]`; fragments sharing a
  packet_id are reassembled into one Opus frame.
- Codec characteristic `19b10002-...` (read): 0/1 = PCM16 16k/8k, 20/21 = Opus 16k.

## Roadmap

- Phase 3: HTTPS (self-signed) so the phone browser can stream its mic;
  optional Groq free-tier Whisper large-v3 for cloud-quality accuracy;
  speaker diarization; summaries.
