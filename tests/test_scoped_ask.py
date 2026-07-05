"""P1 per-memory chat (scoped ask) + P2 action counts."""

import numpy as np
from fastapi.testclient import TestClient

from memory import MemoryStore
from tests.test_memory import fake_embedder


def test_store_scoped_search(tmp_path):
    s = MemoryStore(tmp_path / "t.db", embed_fn=fake_embedder)
    s.index_recording("session_20260101_000000",
                      [{"start": 0, "text": "we discussed pricing here"}])
    s.index_recording("session_20260102_000000",
                      [{"start": 0, "text": "pricing came up again later"}])
    all_hits = s.search("pricing", top_k=10)
    assert {h["recording"] for h in all_hits} == {
        "session_20260101_000000", "session_20260102_000000"}
    scoped = s.search("pricing", top_k=10, recording="session_20260102_000000")
    assert scoped and {h["recording"] for h in scoped} == {"session_20260102_000000"}
    # keyword path respects scope too
    kw = s.keyword_search("pricing", recording="session_20260101_000000")
    assert {h["recording"] for h in kw} == {"session_20260101_000000"}
    s.close()


def test_action_count_in_recordings(tmp_path):
    s = MemoryStore(tmp_path / "t.db", embed_fn=fake_embedder)
    s.index_recording("session_20260101_000000", [{"start": 0, "text": "x y z"}])
    s.set_enrichment("session_20260101_000000", title="t", entities=[
        {"type": "action", "value": "do the thing"},
        {"type": "promise", "value": "send it friday"},
        {"type": "deadline", "value": "march 3"},
        {"type": "person", "value": "Rahul"},        # not counted
    ])
    recs = s.recordings()
    assert recs[0]["action_count"] == 3
    s.close()


def seed(stub_hub):
    import server

    server.memstore.index_recording(
        "session_20260101_000000",
        [{"start": 0, "text": "alpha topic pricing decided here"}])
    server.memstore.index_recording(
        "session_20260102_000000",
        [{"start": 0, "text": "beta topic hiring decided there"}])
    (server.SESSIONS / "session_20260101_000000.txt").touch()
    (server.SESSIONS / "session_20260102_000000.txt").touch()


def test_ask_scoped_endpoint(stub_hub, monkeypatch):
    import server

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    seed(stub_hub)
    with TestClient(server.app) as c:
        r = c.post("/ask", json={"question": "what was decided?",
                                 "recording": "session_20260102_000000"}).json()
        assert r["citations"]
        assert all(cit["recording"] == "session_20260102_000000"
                   for cit in r["citations"])

        # unknown / invalid recording names
        assert c.post("/ask", json={"question": "x",
                                    "recording": "session_20990101_000000"}).status_code == 404
        assert c.post("/ask", json={"question": "x",
                                    "recording": "../evil"}).status_code == 400

        # unscoped still sees both
        r = c.post("/ask", json={"question": "what topics were decided?"}).json()
        assert {cit["recording"] for cit in r["citations"]} >= {
            "session_20260101_000000", "session_20260102_000000"}
