import time

from fastapi.testclient import TestClient

from streemcam.identity import Identity
from tg_helpers import make_init_data

USER = Identity("tg", 42)


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_tg_session_and_cameras(web):
    client = TestClient(web.app)
    r = client.post("/api/tg/session",
                    json={"init_data": make_init_data("123456:TEST", 42, int(time.time()))})
    assert r.status_code == 200
    token = r.json()["token"]

    r = client.get("/api/cameras", headers=bearer(token))
    assert r.status_code == 200
    body = r.json()
    assert body["player_mode"] == "mse"
    assert body["max_streams"] == 4
    assert body["max_transcodes"] == 2
    assert body["cameras"] == [
        {"id": "yard", "name": "Двор", "kind": "config", "online": True},
        {"id": "gate", "name": "Ворота", "kind": "config", "online": False},
        {"id": "room", "name": "Комната", "kind": "config", "online": None},
    ]


def test_tg_session_not_allowed_returns_id(web):
    client = TestClient(web.app)
    r = client.post("/api/tg/session",
                    json={"init_data": make_init_data("123456:TEST", 999, int(time.time()))})
    assert r.status_code == 403
    assert r.json() == {"error": "not_allowed", "user_id": 999}


def test_tg_session_invalid(web):
    r = TestClient(web.app).post("/api/tg/session", json={"init_data": "garbage"})
    assert r.status_code == 401


def test_link_redeem_once(web):
    client = TestClient(web.app)
    t = web.access.issue_link_token(Identity("dc", 77))
    r = client.post("/api/link/redeem", json={"t": t})
    assert r.status_code == 200
    assert client.get("/api/cameras", headers=bearer(r.json()["token"])).status_code == 200
    assert client.post("/api/link/redeem", json={"t": t}).status_code == 401


async def test_link_redeem_denied_user(web):
    t = web.access.issue_link_token(USER)
    await web.access.deny(USER)
    r = TestClient(web.app).post("/api/link/redeem", json={"t": t})
    assert r.status_code == 403
    assert r.json()["user_id"] == 42


def test_cameras_requires_session(web):
    client = TestClient(web.app)
    assert client.get("/api/cameras").status_code == 401
    assert client.get("/api/cameras", headers=bearer("bad.token")).status_code == 401


def test_session_via_query_param(web):
    token = web.access.issue_session(USER)
    assert TestClient(web.app).get(f"/api/cameras?s={token}").status_code == 200


async def test_session_after_deny_is_forbidden(web):
    token = web.access.issue_session(USER)
    await web.access.deny(USER)
    assert TestClient(web.app).get("/api/cameras", headers=bearer(token)).status_code == 403


def test_snapshot(web):
    client = TestClient(web.app)
    h = bearer(web.access.issue_session(USER))
    r = client.get("/api/snapshot/yard", headers=h)
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert r.content == b"JPEG"
    assert client.get("/api/snapshot/gate", headers=h).status_code == 404
    assert client.get("/api/snapshot/nope", headers=h).status_code == 404
    assert client.get("/api/snapshot/yard").status_code == 401


def test_cameras_include_tuya(web):
    from streemcam.tuya.models import TuyaCamera
    web.catalog.set_tuya([TuyaCamera("bf1", "Прихожая", True)])
    client = TestClient(web.app)
    h = bearer(web.access.issue_session(USER))
    cams = client.get("/api/cameras", headers=h).json()["cameras"]
    assert cams[-1] == {"id": "tuya_bf1", "name": "Прихожая", "kind": "tuya", "online": None}
    assert client.get("/api/snapshot/tuya_bf1", headers=h).status_code == 404  # снимка ещё нет


def test_go2rtc_js_proxy_whitelist(web):
    client = TestClient(web.app)
    r = client.get("/go2rtc/video-stream.js")
    assert r.status_code == 200
    assert r.text == "// video-stream"
    assert "javascript" in r.headers["content-type"]
    assert client.get("/go2rtc/api.js").status_code == 404
