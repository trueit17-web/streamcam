import os

import pytest
from fastapi.testclient import TestClient

from streemcam.identity import Identity
from streemcam.recording.archive import Archive

USER = Identity("tg", 42)


@pytest.fixture
def aweb(make_web, cfg, tmp_path):
    d = tmp_path / "yard" / "2026-09-25"
    d.mkdir(parents=True)
    f = d / "08-00-00.mp4"
    f.write_bytes(bytes(range(256)) * 4)          # 1024 байта
    os.utime(f, (0, 0))
    web = make_web(cfg)
    web.archive = Archive(tmp_path, web.catalog)
    web.app = web.make_app(archive=web.archive)
    return web


def h(web):
    return {"Authorization": f"Bearer {web.access.issue_session(USER)}"}


def test_days_hours_and_file(aweb):
    c = TestClient(aweb.app)
    assert c.get("/api/archive/yard/days", headers=h(aweb)).json() == {"days": ["2026-09-25"]}
    assert c.get("/api/archive/yard/2026-09-25", headers=h(aweb)).json() == {
        "hours": [{"name": "08-00-00", "hour": 8, "size": 1024, "recording": False}]}
    r = c.get("/api/archive/yard/2026-09-25/08-00-00.mp4", headers=h(aweb))
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp4" and len(r.content) == 1024
    assert "attachment" not in r.headers.get("content-disposition", "")


def test_range_and_download(aweb):
    c = TestClient(aweb.app)
    r = c.get("/api/archive/yard/2026-09-25/08-00-00.mp4",
              headers={**h(aweb), "Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100
    r = c.get(f"/api/archive/yard/2026-09-25/08-00-00.mp4?download=1&s={aweb.access.issue_session(USER)}")
    assert r.status_code == 200
    assert 'filename="yard_2026-09-25_08-00-00.mp4"' in r.headers["content-disposition"]
    assert r.headers["content-disposition"].startswith("attachment")


def test_errors(aweb):
    c = TestClient(aweb.app)
    assert c.get("/api/archive/yard/days").status_code == 401
    assert c.get("/api/archive/nope/days", headers=h(aweb)).status_code == 404
    assert c.get("/api/archive/yard/2026-9-25", headers=h(aweb)).status_code == 404
    assert c.get("/api/archive/yard/2026-09-25/09-00-00.mp4", headers=h(aweb)).status_code == 404
    assert c.get("/api/archive/yard/2026-09-25/..%2F..%2Fx.mp4", headers=h(aweb)).status_code == 404


async def test_denied_user(aweb):
    token = aweb.access.issue_session(USER)
    await aweb.access.deny(USER)
    r = TestClient(aweb.app).get("/api/archive/yard/days", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


def test_disabled_archive(web):
    c = TestClient(web.app)
    assert c.get("/api/archive/yard/days", headers=h(web)).status_code == 404
    assert c.get("/api/cameras", headers=h(web)).json()["recording"] is False


def test_cameras_recording_flag(aweb):
    assert TestClient(aweb.app).get("/api/cameras", headers=h(aweb)).json()["recording"] is True


def test_download_survives_file_growing_during_streaming(aweb, monkeypatch):
    """Starlette FileResponse without Range reads to EOF past the Content-Length taken from
    stat() -> uvicorn "Response content longer than Content-Length" if the file (ffmpeg segment
    still being written) grows mid-response. archive_file must stream exactly the size seen at
    stat() time for requests with no Range header."""
    real_open = open
    grown = {"done": False}

    def patched_open(file, *args, **kwargs):
        f = real_open(file, *args, **kwargs)
        if not grown["done"] and str(file).endswith("08-00-00.mp4"):
            grown["done"] = True
            with real_open(file, "ab") as af:
                af.write(b"E" * 4096)
        return f

    monkeypatch.setattr("streemcam.web.server.open", patched_open, raising=False)
    c = TestClient(aweb.app)
    r = c.get("/api/archive/yard/2026-09-25/08-00-00.mp4", headers=h(aweb))
    assert grown["done"] is True
    assert r.status_code == 200
    assert len(r.content) == 1024
    assert r.headers["content-length"] == "1024"
    assert r.headers["content-type"] == "video/mp4"
    assert r.headers["accept-ranges"] == "bytes"
