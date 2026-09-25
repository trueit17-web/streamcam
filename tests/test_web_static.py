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
