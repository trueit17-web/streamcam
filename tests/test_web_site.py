import hashlib
import hmac
import time

from fastapi.testclient import TestClient

from streemcam.identity import Identity
from streemcam.tg_auth import TgAuthError, verify_login_widget
from streemcam.tokens import verify

BOT = "123456:TEST"


def widget(user_id, auth_date=None, bot=BOT, **extra):
    data = {"id": user_id, "first_name": "Иван", "auth_date": int(auth_date or time.time()), **extra}
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    data["hash"] = hmac.new(hashlib.sha256(bot.encode()).digest(), check.encode(), hashlib.sha256).hexdigest()
    return data


def test_verify_login_widget_ok_and_rejects_tampering():
    assert verify_login_widget(widget(42, username="ivan"), BOT) == Identity("tg", 42)
    bad = widget(42)
    bad["id"] = 1
    try:
        verify_login_widget(bad, BOT)
        raise AssertionError("tampered data accepted")
    except TgAuthError:
        pass


def test_verify_login_widget_expired():
    try:
        verify_login_widget(widget(42, auth_date=time.time() - 3 * 86400), BOT)
        raise AssertionError("expired data accepted")
    except TgAuthError:
        pass


def test_widget_login_gives_long_session(web, cfg):
    client = TestClient(web.app)
    r = client.post("/api/tg/widget", json=widget(42))
    assert r.status_code == 200
    payload = verify(r.json()["token"], cfg.secret, "session")
    assert payload["exp"] - time.time() > (cfg.web_session_days - 1) * 86400
    assert client.post("/api/tg/widget", json=widget(999)).status_code == 403
    assert client.post("/api/tg/widget", json={"id": 42, "hash": "x", "auth_date": 1}).status_code == 401


def test_web_page_and_config(make_web, cfg):
    async def username():
        return "cams_bot"
    app = make_web(cfg).make_app(tg_username=username)
    client = TestClient(app)
    html = client.get("/web").text
    assert "/static/web.js?v=" in html and "/static/web.css?v=" in html
    assert client.get("/api/web/config").json() == {"bot": "cams_bot"}
    js = client.get("/static/web.js").text
    assert "tg://resolve" in js and "download" in js


def test_web_config_without_bot(web):
    assert TestClient(web.app).get("/api/web/config").json() == {"bot": None}


def test_link_redeem_for_web_is_long(web, cfg):
    client = TestClient(web.app)
    t = web.access.issue_link_token(Identity("tg", 42))
    r = client.post("/api/link/redeem?web=1", json={"t": t})
    payload = verify(r.json()["token"], cfg.secret, "session")
    assert payload["exp"] - time.time() > 86400 * 2


def test_login_via_telegram_app(make_web, cfg):
    async def username():
        return "cams_bot"
    web = make_web(cfg)
    client = TestClient(web.make_app(tg_username=username))
    start = client.post("/api/web/login").json()
    assert start["bot"] == "cams_bot"
    code = start["code"]
    assert client.get(f"/api/web/login/{code}").json() == {"status": "pending"}
    assert web.access.confirm_web_login(code, Identity("tg", 42))
    r = client.get(f"/api/web/login/{code}")
    payload = verify(r.json()["token"], cfg.secret, "session")
    assert payload["u"] == 42 and payload["exp"] - time.time() > 86400 * 2
    assert client.get(f"/api/web/login/{code}").status_code == 410  # одноразовый
    assert client.get("/api/web/login/unknown").status_code == 410


def test_login_without_bot_is_503(web):
    assert TestClient(web.app).post("/api/web/login").status_code == 503
