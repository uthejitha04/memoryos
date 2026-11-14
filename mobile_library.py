"""Durable local-only phone-to-web imports. One process owns a data directory."""
import datetime as dt
import json
import logging
import pathlib
import re
import shutil
import threading
import wave

from mobile_ingest import load_json, save_json


class MobileLibrary:
    def __init__(self, data_dir, sessions_dir, store, notify):
        self.data_dir, self.sessions_dir = data_dir, sessions_dir
        self.store, self.notify = store, notify
        self.lock = threading.RLock()

    def marker(self, recording_id):
        if not re.fullmatch(r"[0-9a-f]{64}", recording_id):
            raise ValueError("Invalid recording ID")
        directory = pathlib.Path(self.data_dir()) / "mobile"
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{recording_id}.library.json"

    def status(self, recording_id):
        marker = self.marker(recording_id)
        return load_json(marker) if marker.exists() else {"status": "not_imported"}

    def queue(self, recording_id):
        with self.lock:
            current = self.status(recording_id)
            if current["status"] != "not_imported":
                return current
            created = dt.datetime.now(dt.timezone.utc)
            current = {"status": "pending", "created": created.isoformat(),
                       "session": f"session_{created:%Y%m%d_%H%M%S}_mobile_{recording_id}"}
            save_json(self.marker(recording_id), current)
            return current

    @staticmethod
    def write_text(path, text):
        temporary = path.with_suffix(path.suffix + ".importing")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)

    def import_recording(self, wav, transcript):
        with self.lock:
            current = self.queue(wav.stem)
            if current["status"] == "deleted":
                return current
            name, store = current["session"], self.store()
            sessions = pathlib.Path(self.sessions_dir())
            if (current["status"] == "ready" and store is not None
                    and store.recording(name) and (sessions / f"{name}.txt").exists()
                    and (sessions / f"{name}.wav").exists()):
                return current
            current = {k: v for k, v in current.items() if k != "detail"}
            current["status"] = "pending"
            save_json(self.marker(wav.stem), current)
            try:
                if store is None:
                    raise RuntimeError("Memory engine unavailable")
                if transcript.get("status") != "complete":
                    raise RuntimeError("Transcription incomplete")
                segments = [{**s, "speaker": s.get("speaker", "")} for s in transcript["segments"]]
                with wave.open(str(wav), "rb") as audio:
                    duration = audio.getnframes() / audio.getframerate()
                sessions.mkdir(parents=True, exist_ok=True)
                destination = sessions / f"{name}.wav"
                if not destination.exists():
                    temporary = destination.with_suffix(".wav.importing")
                    shutil.copyfile(wav, temporary)
                    temporary.replace(destination)
                # Publish transcript last: the web list discovers *.txt files.
                structured = "".join(json.dumps(s, ensure_ascii=False) + "\n" for s in segments)
                self.write_text(sessions / f"{name}.jsonl", structured)
                self.write_text(sessions / f"{name}.txt", transcript["text"])
                store.index_recording(name, segments, created=current["created"], duration=duration)
                # Upload consent does not imply external AI-provider consent.
                if not store.recording(name)["enriched"]:
                    from enrich import heuristic_title
                    store.set_enrichment(name, title=heuristic_title(transcript["text"])
                                         or "Omi recording", category="conversation")
                current["status"] = "ready"
                save_json(self.marker(wav.stem), current)
            except Exception:
                logging.getLogger("memoryos").exception("Phone library import failed")
                current.update(status="failed", detail="Transcript saved; library import failed. Retry the library import.")
                save_json(self.marker(wav.stem), current)
            else:
                self.notify({"type": "memory", "session": name, "status": "indexed"})
            return current

    def recover(self):
        directory = pathlib.Path(self.data_dir()) / "mobile"
        for marker in sorted(directory.glob("*.library.json")):
            recording_id = marker.name.removesuffix(".library.json")
            try:
                if self.status(recording_id)["status"] == "deleted":
                    continue
                transcript = load_json(directory / f"{recording_id}.json")
                if transcript.get("status") == "complete":
                    self.import_recording(directory / f"{recording_id}.wav", transcript)
            except (OSError, ValueError, KeyError):
                continue  # unfinished transcription/damaged marker needs manual retry

    def mark_deleted(self, name):
        if "_mobile_" not in name:
            return
        with self.lock:
            recording_id = name.rsplit("_mobile_", 1)[1]
            current = self.status(recording_id)
            if current.get("session") == name:
                current["status"] = "deleted"
                save_json(self.marker(recording_id), current)
