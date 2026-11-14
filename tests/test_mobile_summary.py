import json
import threading
import time

import pytest

import mobile_ingest as mobile
import mobile_summary as summary
from tests.test_mobile_ingest import client, audio, upload, wait


@pytest.fixture(autouse=True)
def clean_summary(monkeypatch):
    monkeypatch.delenv("MEMORYOS_SUMMARY_PROVIDER", raising=False)
    monkeypatch.delenv("MEMORYOS_SUMMARY_MODEL", raising=False)
    yield
    deadline = time.monotonic() + 5
    while mobile._summary_busy.locked() and time.monotonic() < deadline:
        time.sleep(.01)
    assert not mobile._summary_busy.locked()


def transcribed(client):
    path, response = upload(client, audio())
    assert response.status_code == 202
    assert wait(client, path)["status"] == "complete"
    return path + "/summary"


def enabled(monkeypatch, cloud=False):
    config = {"provider": "groq" if cloud else "ollama", "model": "test-model", "cloud": cloud, "ready": True}
    monkeypatch.setattr(summary, "settings", lambda: config)
    return {"x-summary-provider": config["provider"], "x-summary-model": config["model"]}


def test_disabled_and_auth(client):
    assert client.get("/mobile/summary-settings").json()["ready"] is False
    path = transcribed(client)
    assert client.post(path).status_code == 503
    assert client.post(path, headers={"x-memoryos-token": "wrong"}).status_code == 401


def test_cloud_needs_consent_and_matching_provider(client, monkeypatch):
    path = transcribed(client)
    headers = enabled(monkeypatch, cloud=True)
    assert client.post(path).status_code == 409
    assert client.post(path, headers=headers).status_code == 400
    monkeypatch.setattr(summary, "generate", lambda *args: {"summary": "Test", "actions": []})
    headers["x-summary-cloud-consent"] = "yes"
    assert client.post(path, headers=headers).status_code == 202
    assert wait(client, path)["status"] == "complete"


def test_summary_persisted_idempotent_and_transcript_unchanged(client, monkeypatch):
    path = transcribed(client)
    original = client.get(path.removesuffix("/summary")).json()
    headers = enabled(monkeypatch)
    calls = []
    def generate(transcript, config):
        calls.append(transcript)
        return {"summary": "A synthetic conversation.", "actions": []}
    monkeypatch.setattr(summary, "generate", generate)
    assert client.post(path, headers=headers).status_code == 202
    assert wait(client, path)["summary"] == "A synthetic conversation."
    assert client.post(path, headers=headers).json()["status"] == "complete"
    assert len(calls) == 1
    assert client.get(path.removesuffix("/summary")).json() == original


def test_failed_summary_can_retry(client, monkeypatch):
    path = transcribed(client)
    headers = enabled(monkeypatch)
    def fail(*args):
        raise ValueError("secret provider diagnostics")
    monkeypatch.setattr(summary, "generate", fail)
    client.post(path, headers=headers)
    result = wait(client, path)
    assert result["status"] == "failed" and "secret" not in str(result)
    monkeypatch.setattr(summary, "generate", lambda *args: {"summary": "Done", "actions": []})
    client.post(path, headers=headers)
    assert wait(client, path)["status"] == "complete"


@pytest.mark.parametrize("text,code", [("", 400), ("x" * 24001, 413)])
def test_empty_and_long_transcripts_rejected(client, tmp_path, text, code):
    path = transcribed(client)
    meta = next((tmp_path / "mobile").glob("*.json"))
    meta.write_text(json.dumps({"status": "complete", "text": text}), encoding="utf-8")
    assert client.post(path).status_code == code


def test_action_requires_verbatim_evidence():
    good = {"summary": "A follow-up was promised.", "actions": [{"task": "Send report", "quote": "I will send the report tomorrow."}]}
    assert summary.validate(json.dumps(good), "I will send the report tomorrow.")["actions"]
    with pytest.raises(ValueError):
        summary.validate(json.dumps(good), "No report was discussed.")
    good["actions"] = []
    assert summary.validate(json.dumps(good), "Hello")["actions"] == []


def test_restart_and_duplicate_summary(client, tmp_path, monkeypatch):
    path = transcribed(client)
    headers = enabled(monkeypatch)
    meta = next((tmp_path / "mobile").glob("*.json")).with_suffix(".summary.json")
    meta.write_text('{"status":"processing"}', encoding="utf-8")
    assert client.get(path).json()["status"] == "interrupted"
    release = threading.Event()
    calls = []
    def generate(*args):
        calls.append(True)
        release.wait(5)
        return {"summary": "Done", "actions": []}
    monkeypatch.setattr(summary, "generate", generate)
    try:
        assert client.post(path, headers=headers).status_code == 202
        assert client.post(path, headers=headers).json()["status"] == "processing"
    finally:
        release.set()
    assert wait(client, path)["status"] == "complete"
    assert len(calls) == 1


def test_local_request_does_not_use_cloud_or_proxies(monkeypatch):
    import httpx
    calls = []
    class FakeClient:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, json):
            calls.append(url)
            if url.endswith("/show"):
                return httpx.Response(200, json={}, request=httpx.Request("POST", url))
            return httpx.Response(200, json={"message": {"content": '{"summary":"Hello was said.","actions":[]}'}}, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "Client", FakeClient)
    assert summary.generate({"text": "Hello"}, {"provider": "ollama", "model": "local-test"})["summary"]
    assert all(url.startswith("http://127.0.0.1:11434/") for url in calls)
