"""Omi Transcriber — local transcription app.

Sources:
  * Omi pendant over BLE (open-source protocol, Opus stream)
  * Any microphone / headset / recording device via the browser (device picker)

Every session records the full audio to a WAV and the transcript to txt+jsonl
under ./sessions, browsable in the web UI.

Run:
    .venv\\Scripts\\python server.py [--model small] [--port 8090]
UI:
    http://localhost:8090   (phone on same Wi-Fi: http://<laptop-ip>:8090)
"""

import argparse
import asyncio
import collections
import contextlib
import datetime as dt
import json
import logging
import os
import pathlib
import secrets
import queue
import re
import threading
import time
import wave

import numpy as np
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

from omi_transcribe import (
    AUDIO_DATA_UUID,
    CODEC_NAMES,
    CODEC_TYPE_UUID,
    SAMPLE_RATE,
    Chunker,
    OpusStreamDecoder,
    PcmStreamDecoder,
)

ROOT = pathlib.Path(__file__).parent
SESSIONS = ROOT / "sessions"
SESSIONS.mkdir(exist_ok=True)
SESSION_NAME_RE = re.compile(r"^session_\d{8}_\d{6}$")
VOCAB_FILE = ROOT / "vocab.txt"

log = logging.getLogger("memoryos")


def setup_logging():
    from logging.handlers import RotatingFileHandler

    handler = RotatingFileHandler(ROOT / "memoryos.log", maxBytes=2_000_000,
                                  backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


def load_env_file():
    """Load KEY=VALUE lines from ./.env into the environment (existing
    environment variables win). Keeps API keys out of code and shell config."""
    p = ROOT / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def ai_available() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GROQ_API_KEY")
                or os.environ.get("OPENROUTER_API_KEY"))


# -- security: LAN pairing token + rate limiting (ARCHITECTURE.md §7) ---------

LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}
TOKEN_COOKIE = "memoryos_token"
RATE_LIMIT_PER_MIN = 240
PAIRING_TOKEN: str | None = None   # None = auth disabled (tests); set in main()

_rate_buckets: dict = collections.defaultdict(int)


def get_or_create_pairing_token() -> str:
    env_token = os.environ.get("PAIRING_TOKEN")
    if env_token:  # cloud deploys set it explicitly so it survives restarts
        return env_token.strip()
    p = ROOT / "pairing_token.txt"
    if not p.exists():
        p.write_text(secrets.token_urlsafe(24), encoding="utf-8")
    return p.read_text(encoding="utf-8").strip()


def _request_token(request) -> str | None:
    return (request.query_params.get("token")
            or request.cookies.get(TOKEN_COOKIE)
            or request.headers.get("x-memoryos-token"))


def ws_authorized(ws: WebSocket) -> bool:
    host = ws.client.host if ws.client else ""
    if host in LOCAL_HOSTS or PAIRING_TOKEN is None:
        return True
    tok = ws.cookies.get(TOKEN_COOKIE) or ws.query_params.get("token") or ""
    return secrets.compare_digest(tok, PAIRING_TOKEN)


def load_vocab() -> str:
    if VOCAB_FILE.exists():
        with contextlib.suppress(Exception):
            return VOCAB_FILE.read_text(encoding="utf-8").strip()
    return ""


def build_live_prompt(vocab: str, last_text: str) -> str:
    """Whisper biases toward the prompt's vocabulary and style. Feed it the
    user's glossary plus the tail of the conversation for context."""
    terms = ", ".join(t.strip() for t in vocab.splitlines() if t.strip())
    parts = []
    if terms:
        parts.append(f"Glossary: {terms}.")
    if last_text:
        parts.append(last_text[-300:])
    return " ".join(parts)[:800]


def chunk_to_wav_bytes(chunk: np.ndarray) -> bytes:
    import io

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes((np.clip(chunk, -1.0, 1.0) * 32767).astype("<i2").tobytes())
    return buf.getvalue()

app = FastAPI(title="MemoryOS")


@app.middleware("http")
async def security_middleware(request, call_next):
    host = request.client.host if request.client else ""
    remote = host not in LOCAL_HOSTS
    if remote and PAIRING_TOKEN is not None:
        # rate limit before auth so brute force is throttled too
        bucket = (host, int(time.time() // 60))
        _rate_buckets[bucket] += 1
        if _rate_buckets[bucket] > RATE_LIMIT_PER_MIN:
            return JSONResponse({"detail": "rate limit exceeded"}, status_code=429)
        if len(_rate_buckets) > 10000:
            now = int(time.time() // 60)
            for k in [k for k in _rate_buckets if k[1] < now - 2]:
                del _rate_buckets[k]
        if not secrets.compare_digest(_request_token(request) or "", PAIRING_TOKEN):
            log.warning("rejected unpaired request from %s to %s",
                        host, request.url.path)
            return JSONResponse(
                {"detail": "pairing token required — open the pairing link "
                           "shown where the server was started"},
                status_code=401)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    # pairing by link: token in the URL becomes a long-lived cookie
    if remote and PAIRING_TOKEN is not None \
            and request.query_params.get("token") == PAIRING_TOKEN:
        response.set_cookie(TOKEN_COOKIE, PAIRING_TOKEN, max_age=365 * 86400,
                            httponly=True, samesite="lax")
    return response


class Session:
    """One recording session: full-audio WAV + transcript (txt and jsonl)."""

    def __init__(self):
        self.name = dt.datetime.now().strftime("session_%Y%m%d_%H%M%S")
        self.txt = SESSIONS / f"{self.name}.txt"
        self.jsonl = SESSIONS / f"{self.name}.jsonl"
        self.wav_path = SESSIONS / f"{self.name}.wav"
        self._wav = wave.open(str(self.wav_path), "wb")
        self._wav.setnchannels(1)
        self._wav.setsampwidth(2)
        self._wav.setframerate(SAMPLE_RATE)
        self._lock = threading.Lock()
        self.samples = 0
        self.line_count = 0
        # create the transcript file up front so the session is listed even
        # if nothing transcribable is ever spoken
        self.txt.touch()

    def add_audio(self, pcm: np.ndarray):
        ints = (np.clip(pcm, -1.0, 1.0) * 32767).astype("<i2")
        with self._lock:
            if self._wav is not None:
                self._wav.writeframes(ints.tobytes())
                self.samples += len(ints)

    def add_line(self, entry: dict):
        with self._lock:
            self.line_count += 1
            with open(self.txt, "a", encoding="utf-8") as f:
                f.write(f"[{entry['time']}] {entry['text']}\n")
            with open(self.jsonl, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def close(self):
        with self._lock:
            if self._wav is not None:
                self._wav.close()
                self._wav = None

    @property
    def seconds(self) -> float:
        return self.samples / SAMPLE_RATE


class Hub:
    """Shared state: whisper worker, websocket clients, active session."""

    def __init__(self, model_name: str, live_engine: str = "local",
                 auto_polish: bool = False):
        self.model_name = model_name
        self.live_engine = live_engine    # local | groq (groq falls back to local)
        self.auto_polish = auto_polish
        self.loop: asyncio.AbstractEventLoop | None = None
        self.clients: set[WebSocket] = set()
        self.chunks: queue.Queue[tuple[Session | None, np.ndarray] | None] = queue.Queue()
        self.session: Session | None = None
        self.chunker = Chunker(on_chunk=lambda c: self.chunks.put((self.session, c)))
        self.model_ready = False
        self.source = "none"          # none | mic | omi
        self.omi_status = "disconnected"
        self.last_text = ""           # context carry-over between live chunks
        self.drain_timeout = 120.0    # max wait for the transcribe queue at session end
        threading.Thread(target=self._worker, daemon=True).start()

    # -- session lifecycle ------------------------------------------------
    def start_session(self) -> Session:
        if self.session is None:
            self.session = Session()
            self.notify_status()
        return self.session

    def end_session(self):
        self.chunker.flush()
        if self.session is not None:
            ended = self.session
            ended.close()
            self.session = None
            self.last_text = ""
            self.notify_status()
            log.info("session %s ended (%.0fs audio, %d lines)",
                     ended.name, ended.seconds, ended.line_count)
            # 1) enrich NOW from the live transcript so action items and the
            #    AI title never depend on polish finishing (or the process
            #    surviving); 2) polish upgrades quality + re-enriches after.
            def enrich_now():
                # transcription of the final chunks may still be draining;
                # give the worker queue a moment before reading the transcript
                deadline = time.time() + self.drain_timeout
                while not self.chunks.empty() and time.time() < deadline:
                    time.sleep(0.5)
                segs = live_transcript_segments(ended.name)
                if segs:
                    try:
                        index_and_enrich(ended.name, segs)
                    except Exception:
                        log.exception("immediate enrich failed for %s", ended.name)

            if memstore is not None and ended.line_count > 0:
                threading.Thread(target=enrich_now, daemon=True).start()
            if (self.auto_polish and polisher is not None
                    and ended.samples / SAMPLE_RATE >= 3.0
                    and not polisher.running(ended.name)):
                engine = "groq" if groq_api_key() else "local"
                polisher.start(ended.name, ended.wav_path,
                               SESSIONS / f"{ended.name}.polished.txt", engine)

    def feed_audio(self, pcm: np.ndarray):
        self.start_session().add_audio(pcm)
        self.chunker.feed(pcm)

    # -- live transcription worker thread ------------------------------------
    def live_transcribe(self, chunk: np.ndarray) -> tuple[str, str]:
        """Transcribe one live chunk. Returns (text, engine_used)."""
        prompt = build_live_prompt(load_vocab(), self.last_text)
        if self.live_engine == "groq" and groq_api_key():
            try:
                return groq_live_transcribe(chunk_to_wav_bytes(chunk),
                                            groq_api_key(), prompt), "groq"
            except Exception as e:
                log.warning("groq live failed (%s); using local model", e)
        segments, _ = self._model.transcribe(
            chunk, vad_filter=True, beam_size=1,
            condition_on_previous_text=False,
            initial_prompt=prompt or None)
        return " ".join(s.text.strip() for s in segments).strip(), "local"

    def _worker(self):
        import os

        from faster_whisper import WhisperModel

        self._model = WhisperModel(self.model_name, device="cpu", compute_type="int8",
                                   cpu_threads=max(4, (os.cpu_count() or 4) - 2))
        self.model_ready = True
        self.notify_status()
        while True:
            item = self.chunks.get()
            if item is None:
                break
            session, chunk = item
            t0 = time.time()
            try:
                text, engine = self.live_transcribe(chunk)
            except Exception:
                continue
            if not text:
                continue
            self.last_text = text
            entry = {
                "type": "transcript",
                "time": dt.datetime.now().strftime("%H:%M:%S"),
                "text": text,
                "secs": round(len(chunk) / SAMPLE_RATE, 1),
                "lag": round(time.time() - t0, 1),
                "engine": engine,
            }
            if session is not None:
                entry["session"] = session.name
                session.add_line(entry)
            self.broadcast(entry)

    # -- broadcasting (thread-safe) -----------------------------------------
    def broadcast(self, msg: dict):
        loop = self.loop
        if loop is None or loop.is_closed():
            return
        with contextlib.suppress(RuntimeError):  # loop may close mid-call
            asyncio.run_coroutine_threadsafe(self._send_all(json.dumps(msg)), loop)

    async def _send_all(self, payload: str):
        dead = [ws for ws in self.clients]
        for ws in dead:
            try:
                await ws.send_text(payload)
            except Exception:
                self.clients.discard(ws)

    def status_dict(self):
        s = self.session
        return {
            "type": "status",
            "model": self.model_name,
            "model_ready": self.model_ready,
            "source": self.source,
            "omi": self.omi_status,
            "session": s.name if s else None,
            "recorded_secs": round(s.seconds, 1) if s else 0,
            "groq": bool(groq_api_key()),
            "ai": ai_available(),
            "live_engine": ("groq" if self.live_engine == "groq" and groq_api_key()
                            else "local"),
            "auto_polish": self.auto_polish,
        }

    def notify_status(self):
        self.broadcast(self.status_dict())


class OmiLink:
    """Background BLE connection to the pendant (Omi open-source protocol)."""

    def __init__(self, hub: Hub):
        self.hub = hub
        self.task: asyncio.Task | None = None

    def start(self, address: str | None):
        if self.task and not self.task.done():
            return
        self.task = asyncio.create_task(self._run(address))

    async def stop(self):
        if self.task and not self.task.done():
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        self.hub.omi_status = "disconnected"
        self.hub.notify_status()

    async def _run(self, address: str | None):
        from bleak import BleakClient, BleakScanner

        hub = self.hub
        try:
            if not address:
                hub.omi_status = "scanning..."
                hub.notify_status()
                devices = await BleakScanner.discover(timeout=10.0)
                for d in devices:
                    if "omi" in (d.name or "").lower() or "friend" in (d.name or "").lower():
                        address = d.address
                        break
                if not address:
                    hub.omi_status = "not found (is it connected to the phone app?)"
                    hub.notify_status()
                    return

            hub.omi_status = f"connecting to {address}..."
            hub.notify_status()
            async with BleakClient(address, timeout=30.0) as client:
                codec_id = 20
                with contextlib.suppress(Exception):
                    codec_id = (await client.read_gatt_char(CODEC_TYPE_UUID))[0]
                if codec_id in (20, 21):
                    decoder = OpusStreamDecoder()
                elif codec_id == 0:
                    decoder = PcmStreamDecoder(16000)
                elif codec_id == 1:
                    decoder = PcmStreamDecoder(8000)
                else:
                    hub.omi_status = f"unsupported codec {codec_id}"
                    hub.notify_status()
                    return

                hub.omi_status = f"connected ({CODEC_NAMES.get(codec_id, codec_id)})"
                hub.source = "omi"
                hub.start_session()
                hub.notify_status()

                def on_audio(_c, data: bytearray):
                    pcm = decoder.feed(bytes(data))
                    if pcm is not None and len(pcm):
                        hub.feed_audio(pcm)

                await client.start_notify(AUDIO_DATA_UUID, on_audio)
                while client.is_connected:
                    await asyncio.sleep(1)
                hub.omi_status = "disconnected"
        except asyncio.CancelledError:
            raise
        except Exception as e:
            hub.omi_status = f"error: {e}"
        finally:
            if hub.source == "omi":
                hub.source = "none"
                hub.end_session()
            hub.notify_status()


# -- polish: offline re-transcription of a saved session with a big model ----

POLISH_MODEL = "distil-large-v3"


def polish_segments_local(wav_path: pathlib.Path, progress_cb) -> list[dict]:
    """Transcribe a WAV with the high-accuracy local model."""
    import os

    from faster_whisper import WhisperModel

    model = WhisperModel(POLISH_MODEL, device="cpu", compute_type="int8",
                         cpu_threads=max(4, (os.cpu_count() or 4) - 2))
    with wave.open(str(wav_path)) as w:
        total = w.getnframes() / w.getframerate()
    prompt = build_live_prompt(load_vocab(), "")
    segments, _ = model.transcribe(str(wav_path), vad_filter=True, beam_size=5,
                                   initial_prompt=prompt or None)
    out = []
    for s in segments:
        out.append({"start": s.start, "end": s.end, "text": s.text.strip()})
        progress_cb(min(0.99, s.end / max(total, 0.1)))
    return out


GROQ_STT_MODEL = "whisper-large-v3"
GROQ_CHUNK_SECS = 600  # stay well under Groq's 25MB upload limit


def groq_api_key() -> str | None:
    import os

    return os.environ.get("GROQ_API_KEY") or None


def groq_live_transcribe(wav_bytes: bytes, key: str, prompt: str = "") -> str:
    """One fast Groq call for a live chunk; returns plain text."""
    import httpx

    data = {"model": GROQ_STT_MODEL, "response_format": "json"}
    if prompt:
        data["prompt"] = prompt
    r = httpx.post(
        "https://api.groq.com/openai/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {key}"},
        files={"file": ("audio.wav", wav_bytes, "audio/wav")},
        data=data,
        timeout=30,
    )
    r.raise_for_status()
    return r.json().get("text", "").strip()


def groq_transcribe_bytes(wav_bytes: bytes, key: str) -> list[dict]:
    """One Groq API call; returns [{start, end, text}] for that file.
    Retries on rate limits so long multi-chunk sessions survive them."""
    import httpx

    for attempt in range(3):
        r = httpx.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {key}"},
            files={"file": ("audio.wav", wav_bytes, "audio/wav")},
            data={"model": GROQ_STT_MODEL, "response_format": "verbose_json"},
            timeout=300,
        )
        if r.status_code in (429, 500, 502, 503) and attempt < 2:
            wait = float(r.headers.get("retry-after", 15 * (attempt + 1)))
            log.warning("groq polish chunk got %d; retrying in %.0fs",
                        r.status_code, wait)
            time.sleep(min(wait, 120))
            continue
        break
    r.raise_for_status()
    payload = r.json()
    return [{"start": s["start"], "end": s["end"], "text": s["text"].strip()}
            for s in payload.get("segments", [])]


def polish_segments_groq(wav_path: pathlib.Path, progress_cb) -> list[dict]:
    """Transcribe via Groq's free-tier whisper-large-v3, chunking long audio."""
    key = groq_api_key()
    if not key:
        raise RuntimeError("GROQ_API_KEY not set — get a free key at "
                           "https://console.groq.com and set the env var")
    with wave.open(str(wav_path)) as w:
        rate, width, nframes = w.getframerate(), w.getsampwidth(), w.getnframes()
        total_secs = nframes / rate
        out = []
        offset = 0.0
        chunk_frames = GROQ_CHUNK_SECS * rate
        while True:
            frames = w.readframes(chunk_frames)
            if not frames:
                break
            import io

            buf = io.BytesIO()
            with wave.open(buf, "wb") as part:
                part.setnchannels(1)
                part.setsampwidth(width)
                part.setframerate(rate)
                part.writeframes(frames)
            for seg in groq_transcribe_bytes(buf.getvalue(), key):
                out.append({"start": seg["start"] + offset,
                            "end": seg["end"] + offset, "text": seg["text"]})
            offset += len(frames) / (width * rate)
            progress_cb(min(0.99, offset / max(total_secs, 0.1)))
    return out


ENGINES = {"local": polish_segments_local, "groq": polish_segments_groq}

GROQ_LLM_MODEL = "llama-3.3-70b-versatile"

SUMMARY_PROMPT = """Summarize this conversation transcript. Output markdown with:

## Summary
2-4 sentences on what was discussed.

## Key points
Bulleted list of the important details.

## Action items
Bulleted list of concrete tasks/commitments mentioned (or "None mentioned").

Transcript:
"""


def groq_summarize(transcript: str, key: str) -> str:
    import httpx

    r = httpx.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": GROQ_LLM_MODEL,
            "messages": [{"role": "user", "content": SUMMARY_PROMPT + transcript[:24000]}],
            "temperature": 0.3,
        },
        timeout=120,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


class Polisher:
    def __init__(self, hub: "Hub", on_polished=None):
        self.hub = hub
        self.on_polished = on_polished    # callback(name, segments) after success
        self.jobs: dict[str, dict] = {}   # session name -> {status, progress}

    def running(self, name: str) -> bool:
        return self.jobs.get(name, {}).get("status") == "running"

    def start(self, name: str, wav_path: pathlib.Path, out_path: pathlib.Path,
              engine: str):
        self.jobs[name] = {"status": "running", "progress": 0.0, "engine": engine}
        self._notify(name)
        threading.Thread(target=self._run, daemon=True,
                         args=(name, wav_path, out_path, engine)).start()

    def _notify(self, name: str):
        self.hub.broadcast({"type": "polish", "session": name, **self.jobs[name]})

    def _run(self, name, wav_path, out_path, engine):
        job = self.jobs[name]
        try:
            def on_progress(p):
                if p - job["progress"] >= 0.05:
                    job["progress"] = round(p, 2)
                    self._notify(name)

            segs = ENGINES[engine](wav_path, on_progress)
            try:
                import speakers

                if speakers.available():
                    segs = speakers.label_segments(wav_path, segs)
            except Exception:
                pass  # speaker labels are best-effort extras
            lines = []
            for s in segs:
                m, sec = divmod(int(s["start"]), 60)
                who = f"{s['speaker']}: " if s.get("speaker") else ""
                lines.append(f"[{m}:{sec:02d}] {who}{s['text']}")
            out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            job.update(status="done", progress=1.0)
            log.info("polish done: %s (%s, %d segments)", name, engine, len(segs))
            if self.on_polished:
                try:
                    self.on_polished(name, segs)
                except Exception as e:
                    self.hub.broadcast({"type": "memory", "session": name,
                                        "status": f"index error: {e}"})
        except Exception as e:
            job.update(status=f"error: {e}")
            log.exception("polish failed: %s (%s)", name, engine)
        self._notify(name)


hub: Hub = None  # set in main()
omi_link: OmiLink = None
polisher: Polisher = None
memstore = None  # memory.MemoryStore | None when embeddings unavailable

POLISHED_LINE = re.compile(r"^\[(\d+):(\d{2})\]\s*(?:(Speaker \d+):\s*)?(.+)$")


def live_transcript_segments(name: str) -> list[dict]:
    """Best segments available without polish: the live transcript."""
    txt = SESSIONS / f"{name}.txt"
    try:
        text = txt.read_text(encoding="utf-8")
    except OSError:  # session deleted between check and read
        return []
    segs = parse_polished_text(text)
    if segs:
        return segs
    return [{"start": 0, "speaker": "",
             "text": re.sub(r"^\[[\d:]+\]\s*", "", ln)}
            for ln in text.splitlines() if ln.strip()]


def repair_wav(path: pathlib.Path):
    """If the process died mid-recording, the WAV header says 0 frames.
    Our writer always produces a canonical 44-byte PCM header, so the real
    sizes can be recomputed from the file length."""
    if not path.exists():
        return
    try:
        with wave.open(str(path)) as w:
            if w.getnframes() > 0:
                return
    except Exception:
        pass
    size = path.stat().st_size
    if size <= 44:
        return
    with open(path, "r+b") as f:
        f.seek(4)
        f.write((size - 8).to_bytes(4, "little"))
        f.seek(40)
        f.write((size - 44).to_bytes(4, "little"))
    log.warning("repaired truncated wav header: %s (%d bytes)", path.name, size)


def recover_unindexed_sessions():
    """Startup crash recovery: any session with a transcript that never made
    it into the memory index (server died mid-pipeline) gets indexed now."""
    if memstore is None:
        return
    for txt in sorted(SESSIONS.glob("session_*.txt")):
        name = txt.stem
        if name.endswith(".polished") or not SESSION_NAME_RE.match(name):
            continue
        rec = memstore.recording(name)
        if rec and rec["enriched"]:
            continue
        repair_wav(SESSIONS / f"{name}.wav")
        polished = SESSIONS / f"{name}.polished.txt"
        source = polished if polished.exists() else txt
        segs = parse_polished_text(source.read_text(encoding="utf-8")) \
            or live_transcript_segments(name)
        if not segs:
            continue
        try:
            log.info("recovery: indexing %s (%d segments)", name, len(segs))
            index_and_enrich(name, segs)
        except Exception:
            log.exception("recovery failed for %s", name)


def parse_polished_text(text: str) -> list[dict]:
    segs = []
    for line in text.splitlines():
        m = POLISHED_LINE.match(line.strip())
        if m:
            segs.append({"start": int(m.group(1)) * 60 + int(m.group(2)),
                         "speaker": m.group(3) or "", "text": m.group(4)})
    return segs


def index_and_enrich(name: str, segments: list[dict]):
    """Post-polish hook: index for search, then AI-enrich. Best-effort —
    a memory failure never breaks the polish that triggered it."""
    if memstore is None:
        return
    if not (SESSIONS / f"{name}.txt").exists():
        return  # session was deleted while this was queued
    import enrich as enrich_mod

    created = f"{name[8:12]}-{name[12:14]}-{name[14:16]} {name[17:19]}:{name[19:21]}"
    duration = 0.0
    wav = SESSIONS / f"{name}.wav"
    if wav.exists():
        with contextlib.suppress(Exception):
            with wave.open(str(wav)) as w:
                duration = w.getnframes() / w.getframerate()
    n = memstore.index_recording(name, segments, created=created,
                                 duration=duration)
    hub.broadcast({"type": "memory", "session": name, "status": "indexed",
                   "chunks": n})
    transcript = "\n".join(
        f"{s['speaker'] + ': ' if s['speaker'] else ''}{s['text']}"
        for s in segments)
    result = enrich_mod.enrich_transcript(transcript)
    # user-set titles win over AI titles
    user_title = _session_title(name)
    memstore.set_enrichment(name, title=user_title or result["title"],
                            category=result["category"],
                            summary=result["summary"],
                            entities=result["entities"])
    hub.broadcast({"type": "memory", "session": name, "status": "enriched",
                   "mode": result["mode"]})


@app.on_event("startup")
async def _startup():
    hub.loop = asyncio.get_running_loop()
    threading.Thread(target=recover_unindexed_sessions, daemon=True).start()


# -- pages & status -----------------------------------------------------------


@app.get("/")
async def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/manifest.json")
async def manifest():
    return FileResponse(ROOT / "static" / "manifest.json",
                        media_type="application/manifest+json")


@app.get("/icon.svg")
async def icon():
    return FileResponse(ROOT / "static" / "icon.svg", media_type="image/svg+xml")


@app.get("/sw.js")
async def service_worker():
    return FileResponse(ROOT / "static" / "sw.js",
                        media_type="application/javascript")


@app.get("/status")
async def status():
    return JSONResponse(hub.status_dict())


# -- sessions API -------------------------------------------------------------


def _session_paths(name: str):
    if not SESSION_NAME_RE.match(name):
        raise HTTPException(400, "bad session name")
    paths = {ext: SESSIONS / f"{name}.{ext}" for ext in ("txt", "jsonl", "wav")}
    paths["polished"] = SESSIONS / f"{name}.polished.txt"
    paths["meta"] = SESSIONS / f"{name}.meta.json"
    paths["summary"] = SESSIONS / f"{name}.summary.md"
    return paths


MAX_PINS = 10


def _session_meta(name: str) -> dict:
    meta = SESSIONS / f"{name}.meta.json"
    if meta.exists():
        with contextlib.suppress(Exception):
            return json.loads(meta.read_text(encoding="utf-8"))
    return {}


def _write_session_meta(name: str, updates: dict):
    meta = _session_meta(name)
    meta.update(updates)
    (SESSIONS / f"{name}.meta.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return meta


def _pinned_count() -> int:
    return sum(1 for m in SESSIONS.glob("session_*.meta.json")
               if _session_meta(m.stem.replace(".meta", "")).get("pinned"))


def _session_title(name: str) -> str:
    return _session_meta(name).get("title", "")


@app.get("/sessions")
async def list_sessions(q: str = ""):
    out = []
    needle = q.strip().lower()
    for txt in sorted(SESSIONS.glob("session_*.txt"), reverse=True):
        name = txt.stem
        if name.endswith(".polished"):
            continue
        title = _session_title(name)
        if needle:
            hay = title.lower() + " "
            with contextlib.suppress(Exception):
                hay += txt.read_text(encoding="utf-8").lower()
            polished_p = SESSIONS / f"{name}.polished.txt"
            if polished_p.exists():
                with contextlib.suppress(Exception):
                    hay += " " + polished_p.read_text(encoding="utf-8").lower()
            if needle not in hay:
                continue
        wav = SESSIONS / f"{name}.wav"
        secs = 0.0
        if hub.session and hub.session.name == name:
            secs = hub.session.seconds
        elif wav.exists():
            with contextlib.suppress(Exception):
                with wave.open(str(wav)) as w:
                    secs = w.getnframes() / w.getframerate()
        first_line = ""
        with contextlib.suppress(Exception):
            first_line = txt.read_text(encoding="utf-8").strip().splitlines()[0]
        out.append({
            "name": name,
            "title": title,
            "pinned": bool(_session_meta(name).get("pinned")),
            "date": f"{name[8:12]}-{name[12:14]}-{name[14:16]} {name[17:19]}:{name[19:21]}",
            "secs": round(secs, 1),
            "preview": first_line[:120],
            "has_audio": wav.exists(),
            "active": bool(hub.session and hub.session.name == name),
            "polished": (SESSIONS / f"{name}.polished.txt").exists(),
            "polishing": polisher.running(name) if polisher else False,
        })
    out.sort(key=lambda s: (not s["pinned"], ), reverse=False)  # pinned first, stable
    return out


@app.patch("/sessions/{name}")
async def update_session(name: str, body: dict):
    """Update session metadata: title and/or pinned (max MAX_PINS pins)."""
    paths = _session_paths(name)
    if not paths["txt"].exists():
        raise HTTPException(404)
    updates: dict = {}
    if "title" in body:
        updates["title"] = str(body.get("title", ""))[:200]
    if "pinned" in body:
        pinned = bool(body["pinned"])
        if pinned and not _session_meta(name).get("pinned") \
                and _pinned_count() >= MAX_PINS:
            raise HTTPException(409, f"pin limit reached ({MAX_PINS}) — "
                                     "unpin another memory first")
        updates["pinned"] = pinned
    if not updates:
        raise HTTPException(400, "nothing to update (send title and/or pinned)")
    meta = _write_session_meta(name, updates)
    if "title" in updates and memstore is not None and memstore.recording(name):
        memstore.set_title(name, updates["title"])
    return {"ok": True, **meta}


@app.get("/sessions/{name}/text")
async def session_text(name: str):
    p = _session_paths(name)["txt"]
    if not p.exists():
        raise HTTPException(404)
    return PlainTextResponse(p.read_text(encoding="utf-8"),
                             headers={"Content-Disposition": f'inline; filename="{name}.txt"'})


@app.get("/sessions/{name}/audio")
async def session_audio(name: str):
    p = _session_paths(name)["wav"]
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="audio/wav", filename=f"{name}.wav")


@app.get("/sessions/{name}/polished")
async def session_polished(name: str):
    p = _session_paths(name)["polished"]
    if not p.exists():
        raise HTTPException(404)
    return PlainTextResponse(p.read_text(encoding="utf-8"),
                             headers={"Content-Disposition": f'inline; filename="{name}.polished.txt"'})


@app.post("/sessions/{name}/polish")
async def session_polish(name: str, engine: str = "local"):
    paths = _session_paths(name)
    if engine not in ENGINES:
        raise HTTPException(400, f"unknown engine {engine!r}; have {sorted(ENGINES)}")
    if engine == "groq" and not groq_api_key():
        raise HTTPException(400, "GROQ_API_KEY not set — get a free key at "
                                 "https://console.groq.com and set the env var")
    if hub.session and hub.session.name == name:
        raise HTTPException(409, "session is still recording")
    if polisher.running(name):
        raise HTTPException(409, "polish already running for this session")
    if not paths["wav"].exists():
        raise HTTPException(404, "no audio recorded for this session")
    polisher.start(name, paths["wav"], paths["polished"], engine)
    return {"ok": True, "engine": engine}


@app.get("/sessions/{name}/summary")
async def session_summary(name: str):
    p = _session_paths(name)["summary"]
    if not p.exists():
        raise HTTPException(404)
    return PlainTextResponse(p.read_text(encoding="utf-8"),
                             headers={"Content-Disposition": f'inline; filename="{name}.summary.md"'})


@app.post("/sessions/{name}/summarize")
async def session_summarize(name: str):
    import enrich as enrich_mod

    paths = _session_paths(name)
    if not ai_available():
        raise HTTPException(400, "no AI key set — add GEMINI_API_KEY or "
                                 "GROQ_API_KEY (both have free tiers)")
    source = paths["polished"] if paths["polished"].exists() else paths["txt"]
    transcript = source.read_text(encoding="utf-8").strip() if source.exists() else ""
    if not transcript:
        raise HTTPException(400, "no transcript to summarize")
    try:
        summary = await asyncio.to_thread(
            enrich_mod.llm_chat, SUMMARY_PROMPT + transcript[:24000])
    except RuntimeError as e:
        raise HTTPException(502, f"AI providers unavailable: {e}")
    paths["summary"].write_text(summary, encoding="utf-8")
    return {"ok": True, "summary": summary}


@app.delete("/sessions/{name}")
async def delete_session(name: str):
    if hub.session and hub.session.name == name:
        raise HTTPException(409, "session is currently recording")
    for p in _session_paths(name).values():
        # background enrichment may briefly hold the wav open (Windows locks
        # open files) — retry instead of failing the delete
        for attempt in range(5):
            try:
                p.unlink(missing_ok=True)
                break
            except PermissionError:
                if attempt == 4:
                    raise HTTPException(503, "file busy — try again in a moment")
                time.sleep(0.2)
    if memstore is not None:
        memstore.delete_recording(name)
    return {"ok": True}


# -- omi control --------------------------------------------------------------


@app.get("/vocab")
async def get_vocab():
    return PlainTextResponse(load_vocab())


@app.put("/vocab")
async def put_vocab(body: dict):
    text = str(body.get("text", ""))[:5000]
    VOCAB_FILE.write_text(text, encoding="utf-8")
    return {"ok": True, "terms": len([t for t in text.splitlines() if t.strip()])}


# -- memory API (M1/M3) -------------------------------------------------------


def _require_memstore():
    if memstore is None:
        raise HTTPException(503, "memory engine unavailable — install "
                                 "sentence-transformers to enable it")
    return memstore


@app.get("/memory/search")
async def memory_search(q: str, k: int = 8):
    store = _require_memstore()
    if not q.strip():
        raise HTTPException(400, "empty query")
    hits = await asyncio.to_thread(store.search, q, max(1, min(k, 25)))
    titles = {r["name"]: r["title"] or r["name"] for r in store.recordings()}
    for h in hits:
        h["title"] = titles.get(h["recording"], h["recording"])
    return {"query": q, "hits": hits}


@app.post("/ask")
async def ask(body: dict):
    """Ask across all memories, or one memory when `recording` is given."""
    import enrich as enrich_mod

    store = _require_memstore()
    question = str(body.get("question", "")).strip()
    if not question:
        raise HTTPException(400, "empty question")
    recording = body.get("recording") or None
    if recording:
        if not SESSION_NAME_RE.match(recording):
            raise HTTPException(400, "bad recording name")
        if not store.recording(recording):
            raise HTTPException(404, "recording not in memory index")
    hits = await asyncio.to_thread(store.search, question, 8, recording)
    titles = {r["name"]: r["title"] or r["name"] for r in store.recordings()}
    result = await asyncio.to_thread(enrich_mod.ask_answer, question, hits, titles)
    result["question"] = question
    return result


@app.get("/memory/recordings")
async def memory_recordings():
    return _require_memstore().recordings()


@app.get("/memory/entities")
async def memory_entities(type: str | None = None, recording: str | None = None):
    return _require_memstore().entities(type=type, recording=recording)


@app.get("/memory/stats")
async def memory_stats():
    return _require_memstore().stats()


@app.post("/memory/reindex/{name}")
async def memory_reindex(name: str):
    store = _require_memstore()
    paths = _session_paths(name)
    source = paths["polished"] if paths["polished"].exists() else paths["txt"]
    if not source.exists():
        raise HTTPException(404, "no transcript for this session")
    segs = parse_polished_text(source.read_text(encoding="utf-8"))
    if not segs:  # live txt uses [HH:MM:SS] stamps; strip and index flat
        text = source.read_text(encoding="utf-8")
        segs = [{"start": 0, "speaker": "", "text": re.sub(r"^\[[\d:]+\]\s*", "", ln)}
                for ln in text.splitlines() if ln.strip()]
    if not segs:
        raise HTTPException(400, "transcript is empty")
    await asyncio.to_thread(index_and_enrich, name, segs)
    return {"ok": True, "chunks": store.stats()["chunks"]}


@app.post("/omi/connect")
async def omi_connect(address: str | None = None):
    omi_link.start(address)
    return {"ok": True}


@app.post("/omi/disconnect")
async def omi_disconnect():
    await omi_link.stop()
    return {"ok": True}


# -- websockets ---------------------------------------------------------------


@app.websocket("/ws")
async def ws_events(ws: WebSocket):
    if not ws_authorized(ws):
        await ws.close(code=4401)
        return
    await ws.accept()
    hub.clients.add(ws)
    await ws.send_text(json.dumps(hub.status_dict()))
    try:
        while True:
            await ws.receive_text()  # keepalive pings
    except WebSocketDisconnect:
        pass
    finally:
        hub.clients.discard(ws)


@app.websocket("/ws/audio")
async def ws_audio(ws: WebSocket):
    """Browser audio input (any mic/headset): binary PCM16 mono 16kHz."""
    if not ws_authorized(ws):
        await ws.close(code=4401)
        return
    await ws.accept()
    hub.source = "mic"
    hub.start_session()
    hub.notify_status()
    try:
        while True:
            data = await ws.receive_bytes()
            pcm = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
            hub.feed_audio(pcm)
    except WebSocketDisconnect:
        pass
    finally:
        if hub.source == "mic":
            hub.source = "none"
            hub.end_session()
            hub.notify_status()


def main():
    global hub, omi_link, polisher, memstore, PAIRING_TOKEN
    setup_logging()
    load_env_file()
    PAIRING_TOKEN = get_or_create_pairing_token()
    p = argparse.ArgumentParser()
    # 'small' keeps up with live speech on CPU; distil-large-v3/medium are more
    # accurate but slower than realtime on this class of laptop.
    p.add_argument("--model", default="small")
    # cloud hosts (Railway etc.) inject the port to bind via $PORT
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8090)))
    p.add_argument("--https", action="store_true",
                   help="serve TLS with a self-signed cert so phones can record")
    p.add_argument("--live-engine", choices=["auto", "local", "groq"], default="auto",
                   help="live transcription engine; auto = groq when GROQ_API_KEY is set")
    p.add_argument("--no-auto-polish", action="store_true",
                   help="don't automatically polish sessions when they end")
    args = p.parse_args()
    live = args.live_engine
    if live == "auto":
        live = "groq" if groq_api_key() else "local"
    hub = Hub(args.model, live_engine=live, auto_polish=not args.no_auto_polish)
    omi_link = OmiLink(hub)
    import memory as memory_mod

    if memory_mod.embeddings_available():
        memstore = memory_mod.MemoryStore(ROOT / "memoryos.db")
    else:
        print("Memory engine disabled: pip install sentence-transformers")
    polisher = Polisher(hub, on_polished=index_and_enrich)
    import uvicorn

    from certs import local_ipv4s

    ssl_kwargs = {}
    scheme = "http"
    if args.https:
        from certs import ensure_cert

        cert_path, key_path = ensure_cert()
        ssl_kwargs = {"ssl_certfile": str(cert_path), "ssl_keyfile": str(key_path)}
        scheme = "https"
    lan = [ip for ip in local_ipv4s() if ip != "127.0.0.1"]
    print(f"MemoryOS: {scheme}://localhost:{args.port}")
    for ip in lan:
        print(f"Phone/other devices (pairing link): "
              f"{scheme}://{ip}:{args.port}/?token={PAIRING_TOKEN}")
    if args.https:
        print("Phone: accept the certificate warning once; then Record works.")
    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning", **ssl_kwargs)


if __name__ == "__main__":
    main()
