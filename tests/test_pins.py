"""P8 pinned conversations: toggle, pinned-first ordering, 10-pin cap."""

from fastapi.testclient import TestClient


def make_sessions(server, n):
    names = [f"session_202601{i:02d}_000000" for i in range(1, n + 1)]
    for name in names:
        (server.SESSIONS / f"{name}.txt").write_text(
            f"[00:00:01] content of {name}\n", encoding="utf-8")
    return names


def test_pin_unpin_and_ordering(stub_hub):
    import server

    a, b, c = make_sessions(server, 3)
    with TestClient(server.app) as cl:
        r = cl.patch(f"/sessions/{a}", json={"pinned": True})
        assert r.status_code == 200 and r.json()["pinned"] is True

        listed = cl.get("/sessions").json()
        assert listed[0]["name"] == a and listed[0]["pinned"] is True
        assert [s["pinned"] for s in listed] == [True, False, False]
        # unpinned keep newest-first order after the pinned block
        assert [s["name"] for s in listed[1:]] == sorted([b, c], reverse=True)

        # pin survives a title update (meta merge, not overwrite)
        cl.patch(f"/sessions/{a}", json={"title": "Important call"})
        s = next(x for x in cl.get("/sessions").json() if x["name"] == a)
        assert s["pinned"] is True and s["title"] == "Important call"

        # unpin
        cl.patch(f"/sessions/{a}", json={"pinned": False})
        assert not any(s["pinned"] for s in cl.get("/sessions").json())


def test_pin_cap_at_ten(stub_hub):
    import server

    names = make_sessions(server, 11)
    with TestClient(server.app) as cl:
        for name in names[:10]:
            assert cl.patch(f"/sessions/{name}",
                            json={"pinned": True}).status_code == 200
        r = cl.patch(f"/sessions/{names[10]}", json={"pinned": True})
        assert r.status_code == 409
        assert "unpin" in r.json()["detail"].lower()

        # re-pinning an already-pinned one is fine (not a new pin)
        assert cl.patch(f"/sessions/{names[0]}",
                        json={"pinned": True}).status_code == 200

        # after unpinning one, the 11th can be pinned
        cl.patch(f"/sessions/{names[0]}", json={"pinned": False})
        assert cl.patch(f"/sessions/{names[10]}",
                        json={"pinned": True}).status_code == 200


def test_patch_requires_some_field(stub_hub):
    import server

    (name,) = make_sessions(server, 1)
    with TestClient(server.app) as cl:
        assert cl.patch(f"/sessions/{name}", json={}).status_code == 400
