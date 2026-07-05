import json
import time
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient


def make_session_with_wav(stub_hub, secs=2.0):
    stub_hub.feed_audio(np.random.default_rng(0)
                        .normal(0, 0.1, int(16000 * secs)).astype(np.float32))
    name = stub_hub.session.name
    stub_hub.end_session()
    return name


def test_groq_without_key_gives_hint(stub_hub, monkeypatch):
    import server

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    name = make_session_with_wav(stub_hub)
    with TestClient(server.app) as c:
        assert c.get("/status").json()["groq"] is False
        r = c.post(f"/sessions/{name}/polish?engine=groq")
        assert r.status_code == 400
        assert "GROQ_API_KEY" in r.json()["detail"]
        assert "console.groq.com" in r.json()["detail"]


def test_groq_request_shape_and_stitching(stub_hub, monkeypatch):
    """Mock httpx.post: verify the outgoing request and multi-chunk stitching."""
    import httpx

    import server

    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_123")
    monkeypatch.setattr(server, "GROQ_CHUNK_SECS", 2)  # force 2 chunks from 3s
    calls = []

    def fake_post(url, headers=None, files=None, data=None, timeout=None):
        calls.append({"url": url, "headers": headers, "files": files, "data": data})
        # each chunk "transcribes" to one segment at its local 0.5s mark
        body = {"segments": [{"start": 0.5, "end": 1.0,
                              "text": f"chunk {len(calls)} words "}]}
        return httpx.Response(200, json=body,
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)

    name = make_session_with_wav(stub_hub, secs=3.0)
    with TestClient(server.app) as c:
        assert c.get("/status").json()["groq"] is True
        assert c.post(f"/sessions/{name}/polish?engine=groq").status_code == 200
        deadline = time.time() + 15
        while time.time() < deadline:
            if server.polisher.jobs[name]["status"] == "done":
                break
            time.sleep(0.1)
        assert server.polisher.jobs[name]["status"] == "done"

        assert len(calls) == 2, "3s audio with 2s chunks should make 2 calls"
        for call in calls:
            assert call["url"].startswith("https://api.groq.com/")
            assert call["headers"]["Authorization"] == "Bearer gsk_test_123"
            assert call["data"]["model"] == server.GROQ_STT_MODEL
            assert call["data"]["response_format"] == "verbose_json"
            fname, blob, mime = call["files"]["file"]
            assert mime == "audio/wav" and blob[:4] == b"RIFF"
            with wave.open(__import__("io").BytesIO(blob)) as w:
                assert w.getframerate() == 16000 and w.getnchannels() == 1

        polished = c.get(f"/sessions/{name}/polished").text
        # second chunk's 0.5s local timestamp is offset by 2s -> [0:02]
        assert "[0:00] chunk 1 words" in polished
        assert "[0:02] chunk 2 words" in polished


def test_groq_engine_error_reported(stub_hub, monkeypatch):
    import httpx

    import server

    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_123")
    monkeypatch.setattr(server.time, "sleep", lambda s: None)  # fast retries

    def failing_post(url, **kw):
        return httpx.Response(429, json={"error": "rate limit"},
                              request=httpx.Request("POST", url),
                              headers={"retry-after": "1"})

    monkeypatch.setattr(httpx, "post", failing_post)
    name = make_session_with_wav(stub_hub)
    with TestClient(server.app) as c:
        assert c.post(f"/sessions/{name}/polish?engine=groq").status_code == 200
        deadline = time.time() + 15
        while time.time() < deadline:
            status = server.polisher.jobs[name]["status"]
            if status != "running":
                break
            time.sleep(0.1)
        assert status.startswith("error"), status
        # no polished file left behind
        assert c.get(f"/sessions/{name}/polished").status_code == 404
