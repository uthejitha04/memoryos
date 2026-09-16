import hashlib
import io
import json
import threading
import time
import wave

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
import mobile_ingest as mobile


def audio(rate=48000):
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"\x10\x00" * rate)
    return output.getvalue()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(mobile, "transcribe_local", lambda path: [{"start": 0, "end": 1, "text": "Synthetic speech"}])
    app = FastAPI()
    app.include_router(mobile.make_router(lambda: tmp_path, lambda: "test-token"))
    with TestClient(app, headers={"x-memoryos-token": "test-token"}) as result:
        yield result
    deadline = time.monotonic() + 5
    while mobile._busy.locked() and time.monotonic() < deadline:
        time.sleep(.01)
    assert not mobile._busy.locked()


def upload(client, data):
    path = "/mobile/recordings/" + hashlib.sha256(data).hexdigest()
    return path, client.put(path, content=data)


def wait(client, path):
    for _ in range(500):
        response = client.get(path).json()
        if response["status"] != "processing":
            return response
        time.sleep(.01)
    pytest.fail("worker did not finish")


def test_token_required_even_on_localhost(client):
    assert client.get("/mobile/connection", headers={"x-memoryos-token": "wrong"}).status_code == 401
    assert client.get("/mobile/connection").status_code == 200


def test_upload_48k_complete_and_idempotent(client, tmp_path):
    data = audio()
    path, response = upload(client, data)
    assert response.status_code == 202
    assert wait(client, path)["text"] == "Synthetic speech"
    assert client.put(path, content=data).json()["status"] == "complete"
    assert len(list((tmp_path / "mobile").glob("*.wav"))) == 1
    assert next((tmp_path / "mobile").glob("*.wav")).read_bytes() == data


@pytest.mark.parametrize("data", [b"not a wav", audio()[:-10], audio(44100)], ids=["invalid", "truncated", "rate"])
def test_reject_bad_wav_and_cleanup(client, tmp_path, data):
    assert upload(client, data)[1].status_code == 400
    assert not list((tmp_path / "mobile").glob("*.wav"))
    assert not list((tmp_path / "mobile").glob("*.upload"))


def test_size_and_checksum_limits(client, monkeypatch):
    monkeypatch.setattr(mobile, "MAX_BYTES", 10)
    assert upload(client, audio())[1].status_code == 413
    monkeypatch.setattr(mobile, "MAX_BYTES", 700_000_000)
    assert client.put("/mobile/recordings/" + "0" * 64, content=audio()).status_code == 400
    assert client.get("/mobile/recordings/not-an-id").status_code == 400


def test_failure_retry_and_restart_recovery(client, tmp_path, monkeypatch):
    def failure(path):
        raise RuntimeError("private internal error")
    monkeypatch.setattr(mobile, "transcribe_local", failure)
    path, _ = upload(client, audio())
    assert wait(client, path)["status"] == "failed"
    monkeypatch.setattr(mobile, "transcribe_local", lambda path: [])
    meta = next((tmp_path / "mobile").glob("*.json"))
    meta.write_text(json.dumps({"status": "processing"}))
    assert client.get(path).json()["status"] == "interrupted"
    assert client.put(path, content=audio()).status_code == 202
    assert wait(client, path) == {"status": "complete", "segments": [], "text": ""}


def test_only_one_job_and_duplicate_processing(client, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def blocked(path):
        entered.set()
        release.wait(5)
        return []
    monkeypatch.setattr(mobile, "transcribe_local", blocked)
    path, response = upload(client, audio())
    try:
        assert response.status_code == 202 and entered.wait(2)
        assert client.put(path, content=audio()).json()["status"] == "processing"
        assert upload(client, audio(16000))[1].status_code == 409
    finally:
        release.set()
    assert wait(client, path)["status"] == "complete"


def test_cleanup_error_does_not_block_future_uploads(client, monkeypatch):
    import pathlib
    unlink = pathlib.Path.unlink
    def locked_file(path, *args, **kwargs):
        if path.suffix == ".upload":
            raise PermissionError("Synthetic locked file")
        return unlink(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(pathlib.Path, "unlink", locked_file)
        with pytest.raises(PermissionError):
            upload(client, b"invalid audio")
    assert not mobile._busy.locked()
    path, response = upload(client, audio())
    assert response.status_code == 202
    assert wait(client, path)["status"] == "complete"


def test_completion_during_status_read_is_not_reported_interrupted(client, tmp_path, monkeypatch):
    import pathlib
    recording_id = hashlib.sha256(audio()).hexdigest()
    directory = tmp_path / "mobile"
    directory.mkdir()
    wav = directory / f"{recording_id}.wav"
    meta = directory / f"{recording_id}.json"
    wav.write_bytes(audio())
    meta.write_text('{"status": "processing"}', encoding="utf-8")
    mobile._active.add(str(wav))
    original = pathlib.Path.read_text
    def finish_during_read(path, *args, **kwargs):
        value = original(path, *args, **kwargs)
        if path == meta:
            mobile.save_json(meta, {"status": "complete", "text": "done"})
            mobile._active.discard(str(wav))
        return value
    try:
        with monkeypatch.context() as patch:
            patch.setattr(pathlib.Path, "read_text", finish_during_read)
            assert client.get(f"/mobile/recordings/{recording_id}").json()["status"] == "processing"
        assert client.get(f"/mobile/recordings/{recording_id}").json()["status"] == "complete"
    finally:
        mobile._active.discard(str(wav))


@pytest.mark.e2e
def test_real_cached_model_transcribes_48k_speech(tmp_path):
    """Synthetic repository speech, upsampled to the native Android WAV rate."""
    import pathlib
    import numpy as np
    with wave.open(str(pathlib.Path(__file__).parent / "data/speech.wav")) as source:
        samples = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2")
        assert source.getframerate() == 16000
    data = io.BytesIO()
    with wave.open(data, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(48000)
        target.writeframes(np.repeat(samples, 3).tobytes())
    app = FastAPI()
    app.include_router(mobile.make_router(lambda: tmp_path, lambda: "synthetic-token"))
    with TestClient(app, headers={"x-memoryos-token": "synthetic-token"}) as client:
        path, response = upload(client, data.getvalue())
        assert response.status_code == 202
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            result = client.get(path).json()
            if result["status"] != "processing":
                break
            time.sleep(.1)
        assert result["status"] == "complete", result
        assert len(result["text"].split()) >= 10
        assert "coffee shop" in result["text"].lower()
