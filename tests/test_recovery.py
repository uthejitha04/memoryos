"""Crash resilience: immediate enrichment at session end, startup recovery,
and truncated-WAV repair."""

import time
import wave

import numpy as np
from fastapi.testclient import TestClient


def wait_for(cond, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.1)
    return False


def test_session_end_enriches_immediately_without_polish(stub_hub, monkeypatch):
    """Even with auto-polish OFF (polish dead/failed), ending a session must
    index + enrich from the live transcript."""
    import server

    stub_hub.auto_polish = False
    stub_hub.drain_timeout = 0.5  # stubbed worker never drains the queue
    stub_hub.feed_audio(np.random.default_rng(0)
                        .normal(0, 0.1, 16000 * 4).astype(np.float32))
    name = stub_hub.session.name
    stub_hub.session.add_line(
        {"time": "10:00:00", "text": "I will send the report on Monday."})
    stub_hub.end_session()
    assert wait_for(lambda: (r := server.memstore.recording(name)) and r["enriched"])
    hits = server.memstore.search("report Monday", recording=name)
    assert hits and "report" in hits[0]["text"]


def test_startup_recovery_indexes_orphans(stub_hub):
    import server

    name = "session_20260105_090000"
    (server.SESSIONS / f"{name}.txt").write_text(
        "[09:00:01] we agreed to ship the beta next week\n", encoding="utf-8")
    assert server.memstore.recording(name) is None
    server.recover_unindexed_sessions()
    rec = server.memstore.recording(name)
    assert rec and rec["enriched"] == 1
    hits = server.memstore.search("ship the beta", recording=name)
    assert hits
    # second run is a no-op (already enriched)
    server.recover_unindexed_sessions()
    assert server.memstore.stats()["recordings"] == 1


def test_repair_wav_fixes_truncated_header(stub_hub, tmp_path):
    import server

    # write a valid wav, then zero its size fields — exactly what a file
    # looks like when the process is killed before wave.close()
    path = server.SESSIONS / "session_20260105_100000.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x01\x00" * 16000)
    with open(path, "r+b") as f:
        f.seek(40)                      # data chunk size -> 0
        f.write(b"\x00\x00\x00\x00")
    with wave.open(str(path)) as broken:
        assert broken.getnframes() == 0
    server.repair_wav(path)
    with wave.open(str(path)) as fixed:
        assert fixed.getnframes() == 16000

    # healthy files are untouched
    before = path.read_bytes()
    server.repair_wav(path)
    assert path.read_bytes() == before
