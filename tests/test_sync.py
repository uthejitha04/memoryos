"""P11 one-brain sync: cloud receiver endpoint + laptop pusher."""

import time

import httpx
import pytest
from fastapi.testclient import TestClient


def wait_for(cond, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.1)
    return False


NAME = "session_20260107_120000"


def test_sync_receive_saves_and_indexes(stub_hub, monkeypatch):
    import server

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with TestClient(server.app) as c:
        r = c.post("/sync/session", data={"name": NAME}, files=[
            ("files", (f"{NAME}.txt", b"[0:01] we synced this from the laptop",
                       "application/octet-stream")),
            ("files", (f"{NAME}.wav", b"RIFF" + b"\x00" * 40,
                       "application/octet-stream")),
        ])
        assert r.status_code == 200
        assert sorted(r.json()["saved"]) == [f"{NAME}.txt", f"{NAME}.wav"]
        assert (server.SESSIONS / f"{NAME}.txt").exists()
        assert wait_for(lambda: server.memstore.recording(NAME) is not None)
        hits = server.memstore.search("synced from the laptop", recording=NAME)
        assert hits


def test_sync_receive_rejects_bad_input(stub_hub):
    import server

    with TestClient(server.app) as c:
        r = c.post("/sync/session", data={"name": "../../evil"}, files=[
            ("files", ("x.txt", b"data", "application/octet-stream"))])
        assert r.status_code == 400
        r = c.post("/sync/session", data={"name": NAME}, files=[
            ("files", ("unrelated.exe", b"data", "application/octet-stream"))])
        assert r.status_code == 400
        assert not (server.SESSIONS / "unrelated.exe").exists()


def test_sync_push_sends_existing_files(stub_hub, monkeypatch):
    import server

    (server.SESSIONS / f"{NAME}.txt").write_text("[0:01] hello", encoding="utf-8")
    (server.SESSIONS / f"{NAME}.polished.txt").write_text("[0:01] hello!",
                                                          encoding="utf-8")
    monkeypatch.setenv("MEMORYOS_SYNC_URL", "https://cloud.example")
    monkeypatch.setenv("MEMORYOS_SYNC_TOKEN", "tok123")
    captured = {}

    def fake_post(url, data=None, files=None, headers=None, timeout=None):
        captured.update(url=url, data=data, headers=headers,
                        names=[f[1][0] for f in files])
        return httpx.Response(200, json={"ok": True},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    server.sync_push(NAME)
    assert captured["url"] == "https://cloud.example/sync/session"
    assert captured["data"] == {"name": NAME}
    assert captured["headers"]["x-memoryos-token"] == "tok123"
    assert sorted(captured["names"]) == [f"{NAME}.polished.txt", f"{NAME}.txt"]


def test_sync_push_noop_without_config(stub_hub, monkeypatch):
    import server

    monkeypatch.delenv("MEMORYOS_SYNC_URL", raising=False)
    called = []
    monkeypatch.setattr(httpx, "post", lambda *a, **k: called.append(1))
    (server.SESSIONS / f"{NAME}.txt").write_text("x", encoding="utf-8")
    server.sync_push(NAME)
    assert called == []


def test_sync_push_survives_network_failure(stub_hub, monkeypatch):
    import server

    monkeypatch.setenv("MEMORYOS_SYNC_URL", "https://cloud.example")
    monkeypatch.setattr(httpx, "post",
                        lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("down")))
    (server.SESSIONS / f"{NAME}.txt").write_text("x", encoding="utf-8")
    server.sync_push(NAME)  # must not raise
