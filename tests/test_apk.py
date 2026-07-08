"""APK hosting: public download + token-gated upload."""

from fastapi.testclient import TestClient


def test_apk_404_before_upload(stub_hub):
    import server

    with TestClient(server.app) as c:
        assert c.get("/app.apk").status_code == 404


def test_apk_upload_then_download(stub_hub):
    import server

    with TestClient(server.app) as c:
        r = c.post("/admin/apk", files=[
            ("file", ("app-debug.apk", b"PK\x03\x04 fake apk bytes",
                      "application/vnd.android.package-archive"))])
        assert r.status_code == 200 and r.json()["bytes"] > 0
        d = c.get("/app.apk")
        assert d.status_code == 200
        assert d.content == b"PK\x03\x04 fake apk bytes"
        assert "android.package-archive" in d.headers["content-type"]


def test_apk_is_public_path(stub_hub, monkeypatch):
    import server

    # /app.apk must be reachable without the pairing token (for phone browsers)
    assert "/app.apk" in server.PUBLIC_PATHS
