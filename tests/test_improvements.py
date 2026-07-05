"""Live-quality upgrades: vocabulary biasing, Groq live engine, auto-polish."""

import time

import numpy as np
import pytest
from fastapi.testclient import TestClient


class FakeSegment:
    def __init__(self, text):
        self.text = text


class FakeLocalModel:
    def __init__(self, text="local words"):
        self.text = text
        self.calls = []

    def transcribe(self, chunk, **kw):
        self.calls.append(kw)
        return [FakeSegment(self.text)], None


# ---------------- prompt building ----------------

def test_build_live_prompt():
    import server

    p = server.build_live_prompt("Omi\nNaveen\n\nHyperFrames", "we said things")
    assert p.startswith("Glossary: Omi, Naveen, HyperFrames.")
    assert p.endswith("we said things")
    assert server.build_live_prompt("", "") == ""
    # long context is trimmed from the front, total capped
    p = server.build_live_prompt("x", "a" * 1000)
    assert len(p) <= 800


def test_vocab_endpoints_roundtrip(stub_hub):
    import server

    with TestClient(server.app) as c:
        assert c.get("/vocab").text == ""
        r = c.put("/vocab", json={"text": "Omi\nNaveen\n"})
        assert r.json()["terms"] == 2
        assert c.get("/vocab").text == "Omi\nNaveen"
        assert server.load_vocab() == "Omi\nNaveen"


# ---------------- live engine selection ----------------

def test_live_transcribe_uses_groq_when_configured(stub_hub, monkeypatch):
    import httpx

    import server

    monkeypatch.setenv("GROQ_API_KEY", "gsk_live")
    stub_hub.live_engine = "groq"
    stub_hub.last_text = "previous line"
    server.VOCAB_FILE.write_text("Omi", encoding="utf-8")
    captured = {}

    def fake_post(url, headers=None, files=None, data=None, timeout=None):
        captured.update(url=url, data=data)
        return httpx.Response(200, json={"text": " groq heard this "},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    chunk = np.random.default_rng(0).normal(0, 0.1, 16000).astype(np.float32)
    text, engine = stub_hub.live_transcribe(chunk)
    assert (text, engine) == ("groq heard this", "groq")
    assert captured["data"]["model"] == server.GROQ_STT_MODEL
    assert "Glossary: Omi." in captured["data"]["prompt"]
    assert "previous line" in captured["data"]["prompt"]


def test_live_transcribe_falls_back_to_local(stub_hub, monkeypatch):
    import httpx

    import server

    monkeypatch.setenv("GROQ_API_KEY", "gsk_live")
    stub_hub.live_engine = "groq"
    stub_hub._model = FakeLocalModel("local fallback words")

    def failing_post(url, **kw):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx, "post", failing_post)
    chunk = np.zeros(16000, dtype=np.float32)
    text, engine = stub_hub.live_transcribe(chunk)
    assert (text, engine) == ("local fallback words", "local")


def test_local_engine_gets_vocab_prompt(stub_hub):
    import server

    server.VOCAB_FILE.write_text("HyperFrames\nNaveen", encoding="utf-8")
    stub_hub.live_engine = "local"
    stub_hub._model = FakeLocalModel()
    stub_hub.live_transcribe(np.zeros(16000, dtype=np.float32))
    prompt = stub_hub._model.calls[0]["initial_prompt"]
    assert "HyperFrames" in prompt and "Naveen" in prompt


# ---------------- auto-polish on session end ----------------

def wait_for(cond, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.1)
    return False


def test_auto_polish_fires_on_session_end(stub_hub, monkeypatch):
    import server

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setitem(server.ENGINES, "local", lambda wav, cb: [
        {"start": 0.0, "end": 1.0, "text": "auto polished"}])
    stub_hub.auto_polish = True
    stub_hub.feed_audio(np.random.default_rng(0)
                        .normal(0, 0.1, 16000 * 4).astype(np.float32))
    name = stub_hub.session.name
    stub_hub.end_session()
    assert wait_for(lambda: server.polisher.jobs.get(name, {}).get("status") == "done")
    assert "auto polished" in (server.SESSIONS / f"{name}.polished.txt").read_text(encoding="utf-8")


def test_auto_polish_skips_tiny_sessions(stub_hub):
    import server

    stub_hub.auto_polish = True
    stub_hub.feed_audio(np.zeros(16000, dtype=np.float32))  # 1s < 3s minimum
    name = stub_hub.session.name
    stub_hub.end_session()
    time.sleep(0.3)
    assert name not in server.polisher.jobs
