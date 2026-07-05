"""End-to-end: real whisper model, real websocket, real files.

Streams the TTS fixture into /ws/audio and asserts the transcript contains
the expected phrases and the WAV recording is intact.

Marked slow: run with `pytest -m e2e` (or plain `pytest` runs it too).
"""

import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

EXPECTED_PHRASES = ["quick brown fox", "coffee shop", "project roadmap"]


@pytest.fixture()
def real_hub(monkeypatch, tmp_path):
    import server

    monkeypatch.setattr(server, "SESSIONS", tmp_path)
    hub = server.Hub("small")          # real model (cached from normal use)
    monkeypatch.setattr(server, "hub", hub)
    monkeypatch.setattr(server, "omi_link", server.OmiLink(hub))
    deadline = time.time() + 120
    while not hub.model_ready and time.time() < deadline:
        time.sleep(0.2)
    assert hub.model_ready, "whisper model failed to load"
    return hub


@pytest.mark.e2e
def test_stream_speech_end_to_end(real_hub, speech_audio):
    import server

    with TestClient(server.app) as c:
        with c.websocket_connect("/ws/audio") as ws:
            pcm16 = (speech_audio * 32767).astype("<i2")
            step = 1600  # 100ms
            for i in range(0, len(pcm16), step):
                ws.send_bytes(pcm16[i:i + step].tobytes())
            # trailing silence so the silence-cut fires
            for _ in range(20):
                ws.send_bytes(np.zeros(step, dtype="<i2").tobytes())
            while real_hub.session is None:
                time.sleep(0.05)
            name = real_hub.session.name

        # session closes on disconnect; transcription may still be draining
        deadline = time.time() + 90
        text = ""
        while time.time() < deadline:
            r = c.get(f"/sessions/{name}/text")
            text = r.text.lower() if r.status_code == 200 else ""
            if all(p in text for p in EXPECTED_PHRASES):
                break
            time.sleep(1)
        for phrase in EXPECTED_PHRASES:
            assert phrase in text, f"missing {phrase!r} in transcript: {text!r}"

        sessions = {s["name"]: s for s in c.get("/sessions").json()}
        streamed_secs = len(speech_audio) / 16000
        assert sessions[name]["secs"] >= streamed_secs * 0.9
        print(f"\nE2E transcript:\n{text}")
