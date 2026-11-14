"""LLM provider chain: Gemini primary, Groq fallback, honest failures."""

import httpx
import pytest

import enrich


def gemini_response(text):
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def groq_response(text):
    return {"choices": [{"message": {"content": text}}]}


@pytest.fixture(autouse=True)
def no_env_keys(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_MODEL", raising=False)


def test_nemotron_answers_alone_when_configured(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-k")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_k")  # kept for speech only
    calls = []

    def fake_post(url, headers=None, json=None, **kw):
        calls.append((url, json))
        assert headers["Authorization"] == "Bearer nvapi-k"
        reply = '<think>plan</think>Sure:\n```json\n{"title": "x"}\n```'
        return httpx.Response(200, json=groq_response(reply),
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    assert enrich.llm_chat("hello", json_mode=True) == '{"title": "x"}'
    assert enrich.llm_chat("hello").startswith("Sure:")
    assert all(url == enrich.NVIDIA_URL for url, _ in calls)
    assert calls[0][1]["model"] == "nvidia/nemotron-3.5-lightning-30b-a3b"


def test_nemotron_model_override_and_no_silent_fallback(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-k")
    monkeypatch.setenv("NVIDIA_MODEL", "nvidia/nemotron-3-ultra-550b-a55b")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_k")
    calls = []

    def fake_post(url, json=None, **kw):
        calls.append((url, json["model"]))
        return httpx.Response(429, json={}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    with pytest.raises(RuntimeError, match="nemotron-3-ultra"):
        enrich.llm_chat("hello")
    assert calls == [(enrich.NVIDIA_URL, "nvidia/nemotron-3-ultra-550b-a55b")]


def test_gemini_is_primary(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gem_k")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_k")
    calls = []

    def fake_post(url, **kw):
        calls.append(url)
        assert "generativelanguage.googleapis.com" in url
        assert "key=gem_k" in url
        return httpx.Response(200, json=gemini_response("gemini says hi"),
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    assert enrich.llm_chat("hello") == "gemini says hi"
    assert len(calls) == 1  # groq never touched


def test_gemini_json_mode_sets_mime(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gem_k")
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured.update(json)
        return httpx.Response(200, json=gemini_response('{"a":1}'),
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    enrich.llm_chat("x", json_mode=True)
    assert captured["generationConfig"]["responseMimeType"] == "application/json"


def test_falls_back_to_groq_when_gemini_fails(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gem_k")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_k")
    calls = []

    def fake_post(url, **kw):
        calls.append(url)
        if "generativelanguage" in url:
            return httpx.Response(429, json={"error": "quota"},
                                  request=httpx.Request("POST", url))
        return httpx.Response(200, json=groq_response("groq took over"),
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    assert enrich.llm_chat("hello") == "groq took over"
    assert len(calls) == 2


def test_groq_only_works_without_gemini(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_k")

    def fake_post(url, **kw):
        assert "api.groq.com" in url
        return httpx.Response(200, json=groq_response("groq solo"),
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    assert enrich.llm_chat("hello") == "groq solo"


def test_all_providers_failing_raises_with_details(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gem_k")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_k")
    monkeypatch.setattr(httpx, "post", lambda url, **kw: httpx.Response(
        500, json={}, request=httpx.Request("POST", url)))
    with pytest.raises(RuntimeError) as e:
        enrich.llm_chat("hello")
    assert "gemini" in str(e.value) and "groq" in str(e.value)


def test_openrouter_is_last_resort(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gem_k")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_k")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-k")
    calls = []

    def fake_post(url, headers=None, **kw):
        calls.append(url)
        if "openrouter.ai" in url:
            assert headers["Authorization"] == "Bearer sk-or-k"
            return httpx.Response(200, json=groq_response("openrouter saves the day"),
                                  request=httpx.Request("POST", url))
        return httpx.Response(500, json={}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    assert enrich.llm_chat("hello") == "openrouter saves the day"
    assert len(calls) == 3  # gemini failed, groq failed, openrouter answered
    assert "openrouter.ai" in calls[2]


def test_no_keys_raises(monkeypatch):
    with pytest.raises(RuntimeError, match="no AI provider"):
        enrich.llm_chat("hello")
    assert enrich.ai_available() is False
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    assert enrich.ai_available() is True


def test_env_file_loader(tmp_path, monkeypatch):
    import server

    monkeypatch.delenv("X", raising=False)  # register teardown restore
    monkeypatch.setattr(server, "ROOT", tmp_path)
    (tmp_path / ".env").write_text(
        "# comment\nGEMINI_API_KEY=from_file\n\nBROKEN LINE\nX=1\n",
        encoding="utf-8")
    server.load_env_file()
    assert __import__("os").environ["GEMINI_API_KEY"] == "from_file"
    assert __import__("os").environ["X"] == "1"
    # existing env wins over file
    monkeypatch.setenv("GEMINI_API_KEY", "explicit")
    server.load_env_file()
    assert __import__("os").environ["GEMINI_API_KEY"] == "explicit"
