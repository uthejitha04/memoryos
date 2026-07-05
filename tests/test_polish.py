import time
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient


def make_session_with_wav(stub_hub, audio: np.ndarray):
    stub_hub.feed_audio(audio)
    name = stub_hub.session.name
    stub_hub.session.add_line({"time": "10:00:00", "text": "live line"})
    stub_hub.end_session()
    return name


def wait_for(cond, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.1)
    return False


@pytest.fixture()
def fake_engine(monkeypatch):
    import server

    def engine(wav_path, progress_cb):
        with wave.open(str(wav_path)) as w:
            assert w.getnframes() > 0
        progress_cb(0.5)
        return [{"start": 0.0, "end": 1.0, "text": "polished text alpha"},
                {"start": 61.5, "end": 62.0, "text": "polished text beta"}]

    monkeypatch.setitem(server.ENGINES, "local", engine)
    return engine


def test_polish_happy_path(stub_hub, fake_engine):
    import server

    name = make_session_with_wav(stub_hub, np.random.default_rng(0)
                                 .normal(0, 0.1, 16000).astype(np.float32))
    with TestClient(server.app) as c:
        assert c.post(f"/sessions/{name}/polish").json()["ok"] is True
        assert wait_for(lambda: server.polisher.jobs[name]["status"] == "done")
        polished = c.get(f"/sessions/{name}/polished").text
        assert "[0:00] polished text alpha" in polished
        assert "[1:01] polished text beta" in polished
        sessions = c.get("/sessions").json()
        assert next(s for s in sessions if s["name"] == name)["polished"] is True
        # delete removes the polished file too
        c.delete(f"/sessions/{name}")
        assert c.get(f"/sessions/{name}/polished").status_code == 404


def test_polish_rejections(stub_hub, fake_engine):
    import server

    with TestClient(server.app) as c:
        # active session -> 409
        stub_hub.feed_audio(np.zeros(1600, dtype=np.float32))
        active = stub_hub.session.name
        assert c.post(f"/sessions/{active}/polish").status_code == 409
        stub_hub.end_session()

        # unknown engine -> 400
        assert c.post(f"/sessions/{active}/polish?engine=bogus").status_code == 400

        # no wav -> 404
        missing = "session_20200101_000000"
        (server.SESSIONS / f"{missing}.txt").touch()
        assert c.post(f"/sessions/{missing}/polish").status_code == 404

        # double-start -> 409 while running
        import threading
        gate = threading.Event()

        def slow_engine(wav_path, progress_cb):
            gate.wait(10)
            return [{"start": 0, "end": 1, "text": "x"}]

        server.ENGINES["local"] = slow_engine
        assert c.post(f"/sessions/{active}/polish").status_code == 200
        assert c.post(f"/sessions/{active}/polish").status_code == 409
        gate.set()
        assert wait_for(lambda: server.polisher.jobs[active]["status"] == "done")


@pytest.mark.e2e
def test_polish_real_model(stub_hub, speech_audio):
    """Real distil-large-v3 pass over the TTS fixture."""
    import server

    name = make_session_with_wav(stub_hub, speech_audio)
    with TestClient(server.app) as c:
        assert c.post(f"/sessions/{name}/polish").status_code == 200
        assert wait_for(lambda: server.polisher.jobs[name]["status"] == "done",
                        timeout=300), server.polisher.jobs[name]
        polished = c.get(f"/sessions/{name}/polished").text.lower()
        for phrase in ("quick brown fox", "coffee shop", "project roadmap"):
            assert phrase in polished
        print(f"\nPolished transcript:\n{polished}")
