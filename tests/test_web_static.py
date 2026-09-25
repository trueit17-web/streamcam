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


def test_compat_toggle_present(web):
    client = TestClient(web.app)
    assert 'id="compat"' in client.get("/").text
    js = client.get("/static/app.js").text
    assert "compat=1" in js and "sc_compat" in js


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
