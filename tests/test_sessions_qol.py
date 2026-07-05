import numpy as np
from fastapi.testclient import TestClient


def make_session(stub_hub, text_line: str):
    stub_hub.feed_audio(np.random.default_rng(0).normal(0, 0.1, 8000).astype(np.float32))
    name = stub_hub.session.name
    stub_hub.session.add_line({"time": "10:00:00", "text": text_line})
    stub_hub.end_session()
    return name


def test_rename_shows_in_list(stub_hub):
    import server

    name = make_session(stub_hub, "we talked about databases")
    with TestClient(server.app) as c:
        r = c.patch(f"/sessions/{name}", json={"title": "DB planning call"})
        assert r.status_code == 200 and r.json()["title"] == "DB planning call"
        listed = c.get("/sessions").json()
        assert listed[0]["title"] == "DB planning call"

        # rename of a nonexistent session 404s
        assert c.patch("/sessions/session_20200101_000000",
                       json={"title": "x"}).status_code == 404
        # delete removes the meta sidecar
        c.delete(f"/sessions/{name}")
        assert not (server.SESSIONS / f"{name}.meta.json").exists()


def test_search_filters_by_content_and_title(stub_hub):
    import server

    make_session(stub_hub, "the quarterly budget review went well")
    with TestClient(server.app) as c:
        name2 = None
        # need distinct timestamps: fake a second session on disk
        name2 = "session_20250101_120000"
        (server.SESSIONS / f"{name2}.txt").write_text(
            "[12:00:00] pizza recipe with extra cheese\n", encoding="utf-8")

        assert len(c.get("/sessions").json()) == 2
        hits = c.get("/sessions", params={"q": "budget"}).json()
        assert len(hits) == 1 and "budget" in hits[0]["preview"]
        hits = c.get("/sessions", params={"q": "PIZZA"}).json()
        assert len(hits) == 1 and hits[0]["name"] == name2
        assert c.get("/sessions", params={"q": "nonexistent-word"}).json() == []

        # search by title
        c.patch(f"/sessions/{name2}", json={"title": "cooking notes"})
        hits = c.get("/sessions", params={"q": "cooking"}).json()
        assert len(hits) == 1 and hits[0]["name"] == name2
