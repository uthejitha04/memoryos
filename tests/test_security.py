"""P5 hardening: pairing token for remote clients, rate limit, headers."""

import asyncio

import httpx
import pytest


@pytest.fixture()
def secured(stub_hub, monkeypatch):
    import server

    monkeypatch.setattr(server, "PAIRING_TOKEN", "sekret-token")
    server._rate_buckets.clear()
    return server


def remote_client(server, ip="203.0.113.9"):
    transport = httpx.ASGITransport(app=server.app, client=(ip, 4242))
    return httpx.AsyncClient(transport=transport, base_url="http://memoryos.local")


def run(coro):
    return asyncio.run(coro)


def test_remote_without_token_gets_401(secured):
    async def go():
        async with remote_client(secured) as c:
            r = await c.get("/status")
            assert r.status_code == 401
            assert "pairing" in r.json()["detail"]
    run(go())


def test_pairing_link_sets_cookie_then_cookie_works(secured):
    async def go():
        async with remote_client(secured) as c:
            r = await c.get("/status", params={"token": "sekret-token"})
            assert r.status_code == 200
            assert r.cookies.get("memoryos_token") == "sekret-token"
            r2 = await c.get("/status")  # cookie persisted on the client
            assert r2.status_code == 200
    run(go())


def test_wrong_token_rejected(secured):
    async def go():
        async with remote_client(secured) as c:
            r = await c.get("/status", params={"token": "guess"})
            assert r.status_code == 401
    run(go())


def test_localhost_needs_no_token(secured):
    async def go():
        transport = httpx.ASGITransport(app=secured.app, client=("127.0.0.1", 1))
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://localhost") as c:
            assert (await c.get("/status")).status_code == 200
    run(go())


def test_rate_limit(secured, monkeypatch):
    monkeypatch.setattr(secured, "RATE_LIMIT_PER_MIN", 5)

    async def go():
        async with remote_client(secured, ip="203.0.113.77") as c:
            for _ in range(5):
                await c.get("/status", params={"token": "sekret-token"})
            r = await c.get("/status", params={"token": "sekret-token"})
            assert r.status_code == 429
    run(go())


def test_security_headers_always_present(secured):
    async def go():
        transport = httpx.ASGITransport(app=secured.app, client=("127.0.0.1", 1))
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://localhost") as c:
            r = await c.get("/status")
            assert r.headers["x-content-type-options"] == "nosniff"
            assert r.headers["x-frame-options"] == "DENY"
    run(go())


def test_token_file_created_once(stub_hub, tmp_path, monkeypatch):
    import server

    monkeypatch.setattr(server, "ROOT", tmp_path)
    t1 = server.get_or_create_pairing_token()
    t2 = server.get_or_create_pairing_token()
    assert t1 == t2 and len(t1) >= 24
