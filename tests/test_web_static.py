from fastapi.testclient import TestClient


def test_index_and_assets(web):
    client = TestClient(web.app)
    r = client.get("/")
    assert r.status_code == 200
    assert "/static/app.js" in r.text
    assert "telegram-web-app.js" in r.text
    js = client.get("/static/app.js")
    assert js.status_code == 200
    assert "/go2rtc/video-stream.js" in js.text
    assert client.get("/static/style.css").status_code == 200


def test_compat_toggle_removed(web):
    client = TestClient(web.app)
    assert 'id="compat"' not in client.get("/").text
    assert "#compat" not in client.get("/static/app.js").text


def test_archive_ui_present(web):
    client = TestClient(web.app)
    assert 'id="mode-archive"' in client.get("/").text
    js = client.get("/static/app.js").text
    assert "/api/archive/" in js and "download=1" in js


def test_archive_video_starts_muted(web):
    client = TestClient(web.app)
    html = client.get("/").text
    assert 'id="archive-video"' in html
    idx = html.index('id="archive-video"')
    tag_start = html.rindex("<video", 0, idx)
    tag_end = html.index(">", idx)
    assert "muted" in html[tag_start:tag_end]


def test_inline_card_playback_present(web):
    client = TestClient(web.app)
    js = client.get("/static/app.js").text
    # Inline playback controls inside the card: stop, mute toggle, fullscreen.
    assert "muted" in js
    assert "#cams video-stream" in js
    assert "ondisconnect" in js


def test_assets_versioned_by_content(web):
    import hashlib
    import re

    from streemcam.web.server import STATIC_DIR
    html = TestClient(web.app).get("/").text
    for name in ("app.js", "style.css"):
        digest = hashlib.sha256((STATIC_DIR / name).read_bytes()).hexdigest()[:10]
        assert f'/static/{name}?v={digest}"' in html
    assert not re.search(r'/static/app\.js"', html)


def test_live_mode_button(web):
    client = TestClient(web.app)
    assert '<button id="grid" hidden>Live</button>' in client.get("/").text
    js = client.get("/static/app.js").text
    assert "liveMode" in js and "liveCards" in js
