from fastapi.testclient import TestClient

from streemcam.catalog import Catalog
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
