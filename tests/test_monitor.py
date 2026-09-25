import httpx
import pytest

from streemcam.catalog import Catalog
from streemcam.monitor import CameraMonitor


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def client(up: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/frame.jpeg"
        cam = request.url.params["src"]
        if up.get(cam) == "error":
            raise httpx.ConnectError("refused")
        if up.get(cam):
            return httpx.Response(200, content=b"JPEG-" + cam.encode())
        return httpx.Response(500)
    return httpx.AsyncClient(base_url="http://go2rtc", transport=httpx.MockTransport(handler))


@pytest.fixture
def alerts():
    return []


def make(cfg, up, alerts, clock):
    async def on_alert(cam_id, online):
        alerts.append((cam_id, online))
    return CameraMonitor(cfg, Catalog(cfg), client(up), on_alert, clock=clock)


async def test_probe_all_sets_status_and_snapshot(cfg, alerts):
    m = make(cfg, {"yard": True, "gate": False, "room": "error"}, alerts, Clock())
    assert m.is_online("yard") is None
    await m.probe_all()
    assert m.is_online("yard") is True
    assert m.snapshot("yard") == b"JPEG-yard"
    assert m.is_online("gate") is False
    assert m.is_online("room") is False
    assert m.snapshot("gate") is None
    assert m.is_online("unknown") is None


async def test_alert_once_after_threshold_then_recovery(cfg, alerts):
    up = {"yard": False}
    clock = Clock()
    m = make(cfg, up, alerts, clock)
    await m.probe("yard")
    clock.t = 299
    await m.probe("yard")
    assert alerts == []
    clock.t = 300
    await m.probe("yard")
    assert alerts == [("yard", False)]
    clock.t = 400
    await m.probe("yard")
    assert alerts == [("yard", False)]
    up["yard"] = True
    await m.probe("yard")
    assert alerts == [("yard", False), ("yard", True)]


async def test_short_outage_no_alert(cfg, alerts):
    up = {"yard": False}
    clock = Clock()
    m = make(cfg, up, alerts, clock)
    await m.probe("yard")
    up["yard"] = True
    clock.t = 100
    await m.probe("yard")
    assert alerts == []


async def test_snapshot_kept_while_offline(cfg, alerts):
    up = {"yard": True}
    m = make(cfg, up, alerts, Clock())
    await m.probe("yard")
    up["yard"] = False
    await m.probe("yard")
    assert m.snapshot("yard") == b"JPEG-yard"


async def test_probe_all_follows_catalog(cfg, alerts):
    from streemcam.tuya.models import TuyaCamera
    catalog = Catalog(cfg)
    m = CameraMonitor(cfg, catalog, client({"tuya_bf1": True}), None, clock=Clock())
    catalog.set_tuya([TuyaCamera("bf1", "Прихожая", True)])
    await m.probe_all()
    assert m.snapshot("tuya_bf1") == b"JPEG-tuya_bf1"
