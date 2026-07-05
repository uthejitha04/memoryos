import pathlib
import sys
import wave

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

DATA = pathlib.Path(__file__).parent / "data"


@pytest.fixture(scope="session")
def speech_audio() -> np.ndarray:
    """~12.5s of TTS speech, float32 mono 16kHz."""
    with wave.open(str(DATA / "speech.wav")) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


@pytest.fixture(autouse=True)
def _no_ai_keys_leak(monkeypatch):
    """Tests opt in to AI keys explicitly; the developer machine's .env or
    shell must never bleed into test behavior."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)


@pytest.fixture()
def stub_hub(monkeypatch, tmp_path):
    """A server Hub whose whisper worker is a no-op, with sessions in tmp."""
    import server

    monkeypatch.setattr(server.Hub, "_worker", lambda self: None)
    monkeypatch.setattr(server, "SESSIONS", tmp_path)
    monkeypatch.setattr(server, "VOCAB_FILE", tmp_path / "vocab.txt")
    hub = server.Hub("stub-model")
    monkeypatch.setattr(server, "hub", hub)
    monkeypatch.setattr(server, "omi_link", server.OmiLink(hub))
    monkeypatch.setattr(server, "polisher", server.Polisher(hub))
    # keep polish output deterministic: speaker labeling has its own test
    try:
        import speakers

        monkeypatch.setattr(speakers, "available", lambda: False)
    except ImportError:
        pass
    # memory engine with a fast fake embedder
    from memory import MemoryStore
    from tests.test_memory import fake_embedder

    store = MemoryStore(tmp_path / "memoryos.db", embed_fn=fake_embedder)
    monkeypatch.setattr(server, "memstore", store)
    server.polisher.on_polished = server.index_and_enrich
    yield hub
    store.close()
