import numpy as np
from fastapi.testclient import TestClient


def make_session(stub_hub, line="we agreed to ship on friday"):
    stub_hub.feed_audio(np.random.default_rng(0).normal(0, 0.1, 8000).astype(np.float32))
    name = stub_hub.session.name
    stub_hub.session.add_line({"time": "10:00:00", "text": line})
    stub_hub.end_session()
    return name


def test_summarize_without_key_hints(stub_hub, monkeypatch):
    import server

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    name = make_session(stub_hub)
    with TestClient(server.app) as c:
        r = c.post(f"/sessions/{name}/summarize")
        assert r.status_code == 400 and "NVIDIA_API_KEY" in r.json()["detail"]


def test_summarize_with_mocked_llm(stub_hub, monkeypatch):
    import httpx

    import server

    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured.update(url=url, headers=headers, body=json)
        return httpx.Response(200, json={"choices": [{"message": {
            "content": "## Summary\nShipping friday.\n\n## Action items\n- ship it"}}]},
            request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    name = make_session(stub_hub)
    with TestClient(server.app) as c:
        r = c.post(f"/sessions/{name}/summarize")
        assert r.status_code == 200
        assert "ship it" in r.json()["summary"]
        # request shape
        assert captured["url"].endswith("/chat/completions")
        assert captured["headers"]["Authorization"] == "Bearer gsk_test"
        assert captured["body"]["model"] == server.GROQ_LLM_MODEL
        assert "we agreed to ship on friday" in captured["body"]["messages"][0]["content"]
        # saved and served
        assert "Shipping friday" in c.get(f"/sessions/{name}/summary").text
        # delete removes it
        c.delete(f"/sessions/{name}")
        assert c.get(f"/sessions/{name}/summary").status_code == 404


def test_summarize_empty_transcript_400(stub_hub, monkeypatch):
    import server

    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    # session with audio but no transcript lines
    stub_hub.feed_audio(np.zeros(8000, dtype=np.float32))
    name = stub_hub.session.name
    stub_hub.end_session()
    with TestClient(server.app) as c:
        r = c.post(f"/sessions/{name}/summarize")
        assert r.status_code == 400 and "no transcript" in r.json()["detail"]
