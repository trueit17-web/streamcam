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
