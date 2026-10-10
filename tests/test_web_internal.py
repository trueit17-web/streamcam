from fastapi.testclient import TestClient

from streemcam.catalog import Catalog
from streemcam.imou.models import ImouCamera, ImouError
from streemcam.tuya.models import TuyaCamera, TuyaError
from streemcam.web.internal import create_internal_app

KEY = "k" * 20


class FakeTuya:
    def __init__(self):
        self.logged_in = True
        self.fail = False

    async def stream_url(self, device_id):
        if self.fail:
            raise TuyaError("boom")
        return f"rtsps://tuya/{device_id}?sig=1"


def make(cfg, key=KEY):
    catalog = Catalog(cfg)
    catalog.set_tuya([TuyaCamera("bf1", "Прихожая", True)])
    tuya = FakeTuya()
    return TestClient(create_internal_app(catalog, tuya, key)), tuya


def test_ok(cfg):
    client, _ = make(cfg)
    r = client.get(f"/tuya/bf1?key={KEY}")
    assert r.status_code == 200
    assert r.text == "rtsps://tuya/bf1?sig=1"
    assert r.headers["content-type"].startswith("text/plain")


def test_bad_or_missing_key(cfg):
    client, _ = make(cfg)
    assert client.get("/tuya/bf1?key=wrong").status_code == 403
    assert client.get("/tuya/bf1").status_code == 403


def test_no_key_configured_rejects_everything(cfg):
    client, _ = make(cfg, key=None)
    assert client.get("/tuya/bf1?key=").status_code == 403


def test_unknown_device(cfg):
    client, _ = make(cfg)
    assert client.get(f"/tuya/zz9?key={KEY}").status_code == 404


def test_not_logged_in(cfg):
    client, tuya = make(cfg)
    tuya.logged_in = False
    assert client.get(f"/tuya/bf1?key={KEY}").status_code == 503


def test_cloud_error(cfg):
    client, tuya = make(cfg)
    tuya.fail = True
    assert client.get(f"/tuya/bf1?key={KEY}").status_code == 502


class FakeImou:
    def __init__(self):
        self.fail = False
        self.asked = []

    async def stream_url(self, device_id):
        self.asked.append(device_id)
        if self.fail:
            raise ImouError("boom")
        return f"https://h/{device_id}.m3u8"


def make_imou(cfg, imou="fake", tuya=None):
    catalog = Catalog(cfg)
    catalog.set_imou([ImouCamera("AB12CD", "Улица", True)])
    fake = FakeImou() if imou == "fake" else imou
    return TestClient(create_internal_app(catalog, tuya, KEY, imou=fake)), fake


def test_imou_ok(cfg):
    client, imou = make_imou(cfg)
    r = client.get(f"/imou/AB12CD?key={KEY}")
    assert r.status_code == 200
    assert r.text == "ffmpeg:https://h/AB12CD.m3u8#video=copy#audio=aac"
    assert r.headers["content-type"].startswith("text/plain")
    assert imou.asked == ["AB12CD"]


def test_imou_bad_key(cfg):
    client, _ = make_imou(cfg)
    assert client.get("/imou/AB12CD?key=wrong").status_code == 403
    assert client.get("/imou/AB12CD").status_code == 403


def test_imou_unknown(cfg):
    client, _ = make_imou(cfg)
    assert client.get(f"/imou/ZZZ?key={KEY}").status_code == 404
    assert client.get(f"/imou/ab12cd?key={KEY}").status_code == 404


def test_imou_disabled(cfg):
    client, _ = make_imou(cfg, imou=None)
    assert client.get(f"/imou/AB12CD?key={KEY}").status_code == 404


def test_imou_cloud_error(cfg):
    client, imou = make_imou(cfg)
    imou.fail = True
    r = client.get(f"/imou/AB12CD?key={KEY}")
    assert r.status_code == 502
    assert "boom" not in r.text


def test_tuya_disabled_imou_enabled(cfg):
    catalog = Catalog(cfg)
    catalog.set_tuya([TuyaCamera("bf1", "Прихожая", True)])
    client = TestClient(create_internal_app(catalog, None, KEY, imou=FakeImou()))
    assert client.get(f"/tuya/bf1?key={KEY}").status_code == 404
