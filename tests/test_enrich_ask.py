"""M2 enrichment + M3 Ask: LLM paths mocked, fallbacks exercised, API wired."""

import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

import enrich


# ---------------- M2: enrichment unit ----------------

def test_heuristic_title_strips_stamps_and_speakers():
    t = enrich.heuristic_title(
        "[0:00] Speaker 1: Good morning everyone let us start the meeting now ok")
    assert t.startswith("Good morning everyone")
    assert "…" in t
    assert enrich.heuristic_title("") == "Untitled memory"
    assert enrich.heuristic_title("[0:00] uh") == "Untitled memory"


def test_enrich_without_key_is_heuristic(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    r = enrich.enrich_transcript("We discussed the pricing model in detail today.")
    assert r["mode"] == "heuristic"
    assert r["title"].startswith("We discussed the pricing")
    assert r["category"] == "conversation" and r["entities"] == []


def test_enrich_with_mocked_llm(monkeypatch):
    import httpx

    monkeypatch.setenv("GROQ_API_KEY", "gsk_t")
    payload = {
        "title": "Pricing sync with Rahul",
        "category": "meeting",
        "summary": "Pricing discussion.",
        "entities": [
            {"type": "person", "value": "Rahul", "context": "said pricing"},
            {"type": "promise", "value": "send plan Friday"},
            {"type": "person"},          # missing value -> dropped
        ],
    }
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["body"] = json
        import json as j
        return httpx.Response(200, json={"choices": [{"message": {
            "content": j.dumps(payload)}}]},
            request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    r = enrich.enrich_transcript("Rahul: pricing should be $20.")
    assert r["mode"] == "llm" and r["title"] == "Pricing sync with Rahul"
    assert r["category"] == "meeting" and len(r["entities"]) == 2
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert "Rahul: pricing" in captured["body"]["messages"][0]["content"]


def test_enrich_llm_failure_falls_back(monkeypatch):
    import httpx

    monkeypatch.setenv("GROQ_API_KEY", "gsk_t")
    monkeypatch.setattr(httpx, "post",
                        lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("x")))
    r = enrich.enrich_transcript("Some conversation happened here today.")
    assert r["mode"] == "heuristic"


# ---------------- M3: ask unit ----------------

HITS = [{"recording": "session_20260101_000000", "start": 65.0,
         "speaker": "Speaker 1", "text": "pricing should be twenty dollars",
         "score": 0.9}]


def test_ask_empty_hits(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    r = enrich.ask_answer("what about pricing?", [])
    assert r["mode"] == "empty" and r["citations"] == []


def test_ask_retrieval_only_without_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    r = enrich.ask_answer("pricing?", HITS, {"session_20260101_000000": "Sync"})
    assert r["mode"] == "retrieval"
    assert r["citations"][0]["title"] == "Sync"
    assert r["citations"][0]["stamp"] == "1:05"


def test_ask_llm_with_citations(monkeypatch):
    import httpx

    monkeypatch.setenv("GROQ_API_KEY", "gsk_t")
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["content"] = json["messages"][0]["content"]
        return httpx.Response(200, json={"choices": [{"message": {
            "content": "Pricing is $20/month [1]."}}]},
            request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    r = enrich.ask_answer("what pricing?", HITS)
    assert r["mode"] == "llm" and "[1]" in r["answer"]
    assert "[source 1]" in captured["content"]
    assert "Speaker 1" in captured["content"]


# ---------------- API wiring ----------------

def make_indexed_session(stub_hub, monkeypatch):
    import server

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setitem(server.ENGINES, "local", lambda wav, cb: [
        {"start": 0.0, "end": 4.0, "speaker": "Speaker 1",
         "text": "Rahul said pricing should be twenty dollars."}])
    stub_hub.auto_polish = True
    stub_hub.feed_audio(np.random.default_rng(0)
                        .normal(0, 0.1, 16000 * 4).astype(np.float32))
    name = stub_hub.session.name
    stub_hub.end_session()
    deadline = time.time() + 15
    while time.time() < deadline:
        if server.memstore.recording(name) and \
                server.memstore.recording(name)["enriched"]:
            return name
        time.sleep(0.1)
    raise AssertionError("session was never indexed+enriched")


def test_polish_triggers_index_and_enrich(stub_hub, monkeypatch):
    import server

    name = make_indexed_session(stub_hub, monkeypatch)
    with TestClient(server.app) as c:
        recs = c.get("/memory/recordings").json()
        assert recs[0]["name"] == name
        assert recs[0]["title"].startswith("Rahul said pricing")
        r = c.get("/memory/search", params={"q": "pricing dollars"}).json()
        assert any("pricing" in h["text"] for h in r["hits"])
        stats = c.get("/memory/stats").json()
        assert stats["recordings"] == 1 and stats["chunks"] >= 1


def test_ask_endpoint_retrieval_mode(stub_hub, monkeypatch):
    import server

    make_indexed_session(stub_hub, monkeypatch)
    with TestClient(server.app) as c:
        r = c.post("/ask", json={"question": "what did Rahul say about pricing?"})
        body = r.json()
        assert r.status_code == 200 and body["mode"] == "retrieval"
        assert any("pricing" in cit["text"] for cit in body["citations"])
        assert c.post("/ask", json={"question": "  "}).status_code == 400


def test_delete_cascades_to_memory(stub_hub, monkeypatch):
    import server

    name = make_indexed_session(stub_hub, monkeypatch)
    with TestClient(server.app) as c:
        assert c.delete(f"/sessions/{name}").status_code == 200
        assert c.get("/memory/recordings").json() == []
        assert c.get("/memory/search", params={"q": "pricing"}).json()["hits"] == []


def test_rename_updates_memory_title(stub_hub, monkeypatch):
    import server

    name = make_indexed_session(stub_hub, monkeypatch)
    with TestClient(server.app) as c:
        c.patch(f"/sessions/{name}", json={"title": "Pricing call"})
        assert c.get("/memory/recordings").json()[0]["title"] == "Pricing call"


def test_memory_unavailable_returns_503(stub_hub, monkeypatch):
    import server

    monkeypatch.setattr(server, "memstore", None)
    with TestClient(server.app) as c:
        r = c.get("/memory/search", params={"q": "x"})
        assert r.status_code == 503
        assert "sentence-transformers" in r.json()["detail"]


def test_reindex_endpoint(stub_hub, monkeypatch):
    import server

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    name = "session_20260102_000000"
    (server.SESSIONS / f"{name}.polished.txt").write_text(
        "[0:05] Speaker 2: the launch is planned for March\n", encoding="utf-8")
    (server.SESSIONS / f"{name}.txt").touch()
    with TestClient(server.app) as c:
        assert c.post(f"/memory/reindex/{name}").status_code == 200
        hits = c.get("/memory/search", params={"q": "launch March"}).json()["hits"]
        assert any("launch" in h["text"] for h in hits)
        assert c.post("/memory/reindex/session_20990101_000000").status_code == 404
