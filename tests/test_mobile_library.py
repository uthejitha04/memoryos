"""Phone upload -> shared web library, including retry and process restart."""
import hashlib
import io
import json
import os
import pathlib
import subprocess
import sys
import time
import wave

import pytest
from fastapi.testclient import TestClient

import mobile_ingest as mobile
from mobile_library import MobileLibrary


def audio():
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(48000)
        wav.writeframes(b"\x10\x00" * 48000 * 4)
    return output.getvalue()


SEGMENTS = [{"start": 1.25, "end": 3.5, "text": "Send the coffee project report on Monday."}]


@pytest.fixture
def client(stub_hub, monkeypatch):
    import server
    monkeypatch.setattr(server, "PAIRING_TOKEN", "test-token")
    monkeypatch.setattr(mobile, "transcribe_local", lambda _: SEGMENTS)
    with TestClient(server.app, headers={"x-memoryos-token": "test-token"}) as client:
        yield client
        deadline = time.monotonic() + 5
        while mobile._busy.locked() and time.monotonic() < deadline:
            time.sleep(.01)
        assert not mobile._busy.locked()


def upload(client):
    data = audio()
    path = "/mobile/recordings/" + hashlib.sha256(data).hexdigest()
    assert client.put(path, content=data).status_code == 202
    return path


def wait_library(client, path, status="ready"):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = client.get(path).json()
        if result.get("library", {}).get("status") == status:
            return result
        time.sleep(.01)
    pytest.fail(f"Library did not reach {status}: {result}")


def test_upload_is_one_web_memory_with_playable_citation(client, monkeypatch):
    import server
    import enrich
    monkeypatch.setattr(enrich, "enrich_transcript", lambda _: pytest.fail("Import must not invoke AI"))
    path = upload(client)
    result = wait_library(client, path)
    name = result["library"]["session"]
    recordings = client.get("/memory/recordings").json()
    assert len(recordings) == 1 and recordings[0]["name"] == name
    assert recordings[0]["duration"] == 4
    sessions = client.get("/sessions").json()
    assert sessions[0]["secs"] == 4 and sessions[0]["has_audio"]
    assert "coffee" in client.get(f"/sessions/{name}/text").text
    assert client.get(f"/sessions/{name}/audio").content == audio()
    assert client.get(f"/sessions/{name}/audio", headers={"Range": "bytes=44-63"}).status_code == 206
    hits = client.get("/memory/search", params={"q": "coffee project report"}).json()["hits"]
    assert hits[0]["recording"] == name and hits[0]["start"] == 1.25
    answer = client.post("/ask", json={"question": "coffee project report", "recording": name}).json()
    assert answer["mode"] == "retrieval" and answer["citations"][0]["start"] == 1.25
    client.patch(f"/sessions/{name}", json={"title": "My Omi memory"})
    assert client.put(path, content=audio()).json()["library"]["session"] == name
    assert client.post(path + "/library").json()["status"] == "ready"
    assert server.memstore.stats()["recordings"] == 1
    assert server.memstore.recording(name)["title"] == "My Omi memory"


def test_import_failure_preserves_transcript_and_retries_without_transcription(client, monkeypatch):
    import server
    with monkeypatch.context() as patch:
        patch.setattr(server.memstore, "index_recording", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("fixture")))
        path = upload(client)
        result = wait_library(client, path, "failed")
        assert result["status"] == "complete" and "coffee" in result["text"]
    monkeypatch.setattr(mobile, "transcribe_local", lambda _: pytest.fail("No retranscription"))
    assert client.post(path + "/library").json()["status"] == "ready"
    assert len(client.get("/memory/recordings").json()) == 1


def test_deletion_survives_retry_and_recovery(client):
    import server
    path = upload(client)
    name = wait_library(client, path)["library"]["session"]
    assert client.delete(f"/sessions/{name}").status_code == 200
    assert client.put(path, content=audio()).json()["library"]["status"] == "deleted"
    server.mobile_library.recover()
    server.recover_unindexed_sessions()
    assert client.get("/sessions").json() == []
    assert client.get("/memory/recordings").json() == []
    assert client.get(f"/sessions/{name}/audio").status_code == 404
    assert list((server.DATA / "mobile").glob("*.wav"))  # phone upload original retained


def test_library_retry_requires_auth_and_completed_transcription(client):
    path = "/mobile/recordings/" + "0" * 64
    assert client.post(path + "/library", headers={"x-memoryos-token": "wrong"}).status_code == 401
    assert client.post(path + "/library").status_code == 404
    import server
    mobile.save_json(server.DATA / "mobile" / ("0" * 64 + ".json"), {"status": "processing"})
    assert client.post(path + "/library").status_code == 409


def test_silence_keeps_audio_without_inventing_search_results(client, monkeypatch):
    monkeypatch.setattr(mobile, "transcribe_local", lambda _: [])
    path = upload(client)
    name = wait_library(client, path)["library"]["session"]
    assert client.get(f"/sessions/{name}/audio").content == audio()
    assert client.get("/memory/search", params={"q": "coffee"}).json()["hits"] == []


def test_pending_import_recovers_in_fresh_process_and_second_restart(tmp_path):
    """Simulate death after saving transcription and an unfinished import marker."""
    data = audio()
    recording_id = hashlib.sha256(data).hexdigest()
    library = MobileLibrary(lambda: tmp_path, lambda: tmp_path / "sessions", lambda: None, lambda _: None)
    pending = library.queue(recording_id)
    wav = tmp_path / "mobile" / f"{recording_id}.wav"
    wav.write_bytes(data)
    mobile.save_json(wav.with_suffix(".json"), {"status": "complete", "segments": SEGMENTS, "text": SEGMENTS[0]["text"]})
    code = '''
import json, time
from types import SimpleNamespace
from fastapi.testclient import TestClient
import server
from memory import MemoryStore
from tests.test_memory import fake_embedder
server.memstore = MemoryStore(server.DATA / "memoryos.db", embed_fn=fake_embedder)
server.hub = SimpleNamespace(loop=None, session=None, broadcast=lambda _: None, run_background=lambda fn: fn())
server.polisher = None
with TestClient(server.app) as client:
    records = client.get("/memory/recordings").json()
    answer = client.post("/ask", json={"question": "coffee project report"}).json()
    print(json.dumps({"records": records, "citation": answer["citations"][0]}))
server.memstore.close()
'''
    env = dict(os.environ, MEMORYOS_DATA=str(tmp_path), MEMORYOS_SYNC_URL="", MEMORYOS_SYNC_TOKEN="",
               GEMINI_API_KEY="", GROQ_API_KEY="", OPENROUTER_API_KEY="", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    for _ in range(2):
        run = subprocess.run([sys.executable, "-c", code], cwd=pathlib.Path(__file__).parents[1], env=env,
                             capture_output=True, text=True, timeout=30, check=True)
        result = json.loads(run.stdout.strip().splitlines()[-1])
        assert len(result["records"]) == 1
        assert result["records"][0]["name"] == pending["session"]
        assert result["citation"]["start"] == 1.25
    assert library.status(recording_id)["status"] == "ready"
