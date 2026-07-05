import numpy as np
from fastapi.testclient import TestClient


def make_client(stub_hub):
    import server

    return TestClient(server.app)


def record_fake_session(stub_hub, secs=1.0):
    """Push audio through the hub as if a source was live, then end."""
    pcm = np.random.default_rng(0).normal(0, 0.1, int(16000 * secs)).astype(np.float32)
    stub_hub.feed_audio(pcm)
    name = stub_hub.session.name
    stub_hub.session.add_line({"time": "10:00:00", "text": "hello world test line"})
    stub_hub.end_session()
    return name


def test_status_shape(stub_hub):
    with make_client(stub_hub) as c:
        s = c.get("/status").json()
    assert s["type"] == "status"
    assert s["model"] == "stub-model"
    assert s["source"] == "none"
    assert s["session"] is None


def test_session_lifecycle_and_files(stub_hub):
    name = record_fake_session(stub_hub, secs=1.5)
    with make_client(stub_hub) as c:
        sessions = c.get("/sessions").json()
        assert [s["name"] for s in sessions] == [name]
        assert sessions[0]["has_audio"] is True
        assert 1.2 < sessions[0]["secs"] < 1.8
        assert "hello world" in sessions[0]["preview"]

        text = c.get(f"/sessions/{name}/text").text
        assert "hello world test line" in text

        audio = c.get(f"/sessions/{name}/audio")
        assert audio.status_code == 200
        assert audio.headers["content-type"].startswith("audio/")
        assert audio.content[:4] == b"RIFF"

        assert c.delete(f"/sessions/{name}").status_code == 200
        assert c.get("/sessions").json() == []
        assert c.get(f"/sessions/{name}/text").status_code == 404


def test_cannot_delete_active_session(stub_hub):
    pcm = np.zeros(1600, dtype=np.float32)
    stub_hub.feed_audio(pcm)
    name = stub_hub.session.name
    with make_client(stub_hub) as c:
        assert c.delete(f"/sessions/{name}").status_code == 409
    stub_hub.end_session()


def test_bad_session_names_rejected(stub_hub):
    with make_client(stub_hub) as c:
        for bad in ("..%2f..%2fsecrets", "session_x", "session_20260101_000000x"):
            r = c.get(f"/sessions/{bad}/text")
            assert r.status_code in (400, 404)
            r = c.delete(f"/sessions/{bad}")
            assert r.status_code in (400, 404)


def test_ws_audio_creates_and_closes_session(stub_hub):
    with make_client(stub_hub) as c:
        with c.websocket_connect("/ws/audio") as ws:
            pcm = (np.random.default_rng(1).normal(0, 0.1, 16000) * 32767).astype("<i2")
            ws.send_bytes(pcm.tobytes())
            import time
            deadline = time.time() + 5
            while stub_hub.session is None and time.time() < deadline:
                time.sleep(0.05)
            assert stub_hub.session is not None
            name = stub_hub.session.name
        # after disconnect the session must be closed
        deadline = time.time() + 5
        while stub_hub.session is not None and time.time() < deadline:
            time.sleep(0.05)
        assert stub_hub.session is None
        sessions = c.get("/sessions").json()
        assert any(s["name"] == name and s["has_audio"] for s in sessions)
