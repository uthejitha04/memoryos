"""Private, retryable phone uploads. No cloud providers or personal session mutation."""
import asyncio
import hashlib
import json
import os
import pathlib
import re
import secrets
import threading
import wave

from fastapi import APIRouter, HTTPException, Request

MAX_BYTES = 700_000_000
MAX_SECONDS = 7201
_busy = threading.Lock()
_active = set()
_summary_busy = threading.Lock()
_summary_active = set()
# Windows does not permit replacing a file while a status reader has it open.
# Each service runs one process; serialize its metadata reads and writes.
_metadata_lock = threading.RLock()


def transcribe_local(path):
    from faster_whisper import WhisperModel
    # Use an already cached model; never download or call a cloud provider here.
    model = WhisperModel("small", device="cpu", compute_type="int8", local_files_only=True)
    segments, _ = model.transcribe(str(path), vad_filter=True, beam_size=5)
    return [{"start": s.start, "end": s.end, "text": s.text.strip()} for s in segments]


def save_json(path, value):
    with _metadata_lock:
        temp = path.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        temp.replace(path)


def load_json(path):
    with _metadata_lock:
        return json.loads(path.read_text(encoding="utf-8"))


def validate_wav(path):
    try:
        with wave.open(str(path), "rb") as wav:
            rate, frames = wav.getframerate(), wav.getnframes()
            if (wav.getnchannels() != 1 or wav.getsampwidth() != 2
                    or rate not in (8000, 16000, 24000, 48000)
                    or not 0 < frames / rate <= MAX_SECONDS):
                raise ValueError("Expected mono PCM16 WAV, up to two hours")
            total = 0
            while chunk := wav.readframes(65536):
                total += len(chunk)
            if total != frames * 2:
                raise ValueError("WAV audio is incomplete")
    except (wave.Error, EOFError, ValueError) as exc:
        raise HTTPException(400, "Invalid or incomplete mono PCM16 WAV") from exc


def make_router(data_dir, token_value, library=None):
    router = APIRouter(prefix="/mobile")

    def authorize(request):
        token = token_value()
        if not token:
            raise HTTPException(503, "Pairing is not configured")
        if not secrets.compare_digest(request.headers.get("x-memoryos-token", ""), token):
            raise HTTPException(401, "Pair this phone with the laptop first")

    def paths(recording_id):
        if not re.fullmatch(r"[0-9a-f]{64}", recording_id):
            raise HTTPException(400, "Invalid recording ID")
        directory = pathlib.Path(data_dir()) / "mobile"
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{recording_id}.wav", directory / f"{recording_id}.json"

    def state(recording_id):
        wav, meta = paths(recording_id)
        if not meta.exists():
            if wav.exists():
                return {"status": "interrupted", "detail": "Tap Transcribe to retry"}
            raise HTTPException(404, "Recording not received yet")
        # Snapshot before reading: completion writes metadata before clearing active.
        active = str(wav) in _active
        result = load_json(meta)
        if result["status"] == "processing" and not active:
            return {"status": "interrupted", "detail": "Laptop restarted; tap Transcribe to retry"}
        if library is not None:
            result["library"] = library.status(recording_id)
        return result

    def process(wav, meta):
        try:
            segments = transcribe_local(wav)
            result = {"status": "complete", "segments": segments,
                      "text": "\n".join(s["text"] for s in segments)}
            save_json(meta, result)
        except Exception:
            save_json(meta, {"status": "failed", "detail": "Local transcription failed. Check the cached small Whisper model, then retry."})
        else:
            if library is not None:
                library.import_recording(wav, result)
        finally:
            _active.discard(str(wav))
            _busy.release()

    @router.post("/recordings/{recording_id}/library")
    async def retry_library(recording_id: str, request: Request):
        authorize(request)
        if library is None:
            raise HTTPException(503, "Use the main MemoryOS server for the shared library")
        wav, _ = paths(recording_id)
        transcript = state(recording_id)
        if transcript["status"] != "complete":
            raise HTTPException(409, "Finish transcription first")
        return await asyncio.to_thread(library.import_recording, wav, transcript)

    @router.get("/connection")
    async def connection(request: Request):
        authorize(request)
        return {"ok": True, "engine": "local-small", "max_bytes": MAX_BYTES}

    @router.get("/summary-settings")
    async def summary_settings(request: Request):
        authorize(request)
        from mobile_summary import settings
        return settings()

    def summary_path(recording_id):
        wav, _ = paths(recording_id)
        return wav.with_suffix(".summary.json")

    def summary_state(recording_id):
        path = summary_path(recording_id)
        active = str(path) in _summary_active
        if not path.exists():
            return {"status": "not_started"}
        value = load_json(path)
        if value["status"] == "processing" and not active:
            return {"status": "interrupted", "detail": "Laptop restarted; tap Create summary to retry"}
        return value

    def run_summary(path, transcript, config):
        try:
            from mobile_summary import generate
            result = generate(transcript, config)
            save_json(path, {"status": "complete", **result, "provider": config["provider"], "model": config["model"]})
        except Exception:
            save_json(path, {"status": "failed", "detail": "Summary could not be generated or verified. Check the selected AI setup and retry. Your transcript is unchanged."})
        finally:
            _summary_active.discard(str(path))
            _summary_busy.release()

    @router.get("/recordings/{recording_id}/summary")
    async def get_summary(recording_id: str, request: Request):
        authorize(request)
        return summary_state(recording_id)

    @router.post("/recordings/{recording_id}/summary", status_code=202)
    async def create_summary(recording_id: str, request: Request):
        authorize(request)
        from mobile_summary import settings, MAX_TRANSCRIPT_CHARS
        previous = summary_state(recording_id)
        if previous["status"] in ("complete", "processing"):
            return previous
        transcript = state(recording_id)
        if transcript["status"] != "complete":
            raise HTTPException(409, "Finish transcription first")
        text = transcript.get("text", "").strip()
        if not text:
            raise HTTPException(400, "There is no speech to summarize")
        if len(text) > MAX_TRANSCRIPT_CHARS:
            raise HTTPException(413, "Choose a shorter recording: summary preview limit is 24,000 characters")
        config = settings()
        if not config["ready"]:
            raise HTTPException(503, "Configure a summary model on the laptop first")
        # The app presents this provider before confirmation. A configuration change
        # cannot silently send a transcript to a different provider.
        if (request.headers.get("x-summary-provider") != config["provider"]
                or request.headers.get("x-summary-model") != config["model"]):
            raise HTTPException(409, "AI setup changed; review the provider and try again")
        if config["cloud"] and request.headers.get("x-summary-cloud-consent") != "yes":
            raise HTTPException(400, "Confirm sending this transcript to the selected AI provider")
        if not _summary_busy.acquire(blocking=False):
            raise HTTPException(409, "Another summary is running; retry shortly")
        path = summary_path(recording_id)
        try:
            save_json(path, {"status": "processing"})
            _summary_active.add(str(path))
            threading.Thread(target=run_summary, args=(path, transcript, config), daemon=True).start()
        except BaseException:
            _summary_active.discard(str(path))
            _summary_busy.release()
            raise
        return {"status": "processing"}

    @router.get("/recordings/{recording_id}")
    async def get_recording(recording_id: str, request: Request):
        authorize(request)
        return state(recording_id)

    @router.put("/recordings/{recording_id}", status_code=202)
    async def upload(recording_id: str, request: Request):
        authorize(request)
        wav, meta = paths(recording_id)
        if meta.exists():
            previous = state(recording_id)
            if previous["status"] in ("complete", "processing"):
                if library is not None and previous["status"] == "complete":
                    await asyncio.to_thread(library.import_recording, wav, previous)
                    return state(recording_id)
                return previous
        if not _busy.acquire(blocking=False):
            raise HTTPException(409, "Laptop is processing another recording. Retry shortly.")
        temp = wav.with_suffix(".upload")
        handed_off = False
        try:
            if not wav.exists():
                digest, size = hashlib.sha256(), 0
                with temp.open("wb") as out:
                    async for chunk in request.stream():
                        size += len(chunk)
                        if size > MAX_BYTES:
                            raise HTTPException(413, "Recording exceeds the upload limit")
                        digest.update(chunk)
                        out.write(chunk)
                    out.flush()
                    os.fsync(out.fileno())
                if digest.hexdigest() != recording_id:
                    raise HTTPException(400, "Recording checksum mismatch; retry upload")
                await asyncio.to_thread(validate_wav, temp)
                temp.replace(wav)
            if library is not None:
                library.queue(recording_id)
            save_json(meta, {"status": "processing"})
            _active.add(str(wav))
            thread = threading.Thread(target=process, args=(wav, meta), daemon=True)
            thread.start()
            handed_off = True
            return {"status": "processing"}
        finally:
            try:
                temp.unlink(missing_ok=True)
            finally:
                if not handed_off:
                    _active.discard(str(wav))
                    _busy.release()

    return router
