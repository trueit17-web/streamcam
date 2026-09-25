import httpx
import yaml

from streemcam.catalog import Catalog
from streemcam.go2rtc_config import COMPAT_SUFFIX, compat_name, compat_src
from streemcam.go2rtc_sync import Go2rtcSync, desired_streams, tuya_src
from streemcam.tuya.models import TuyaCamera

KEY = "k" * 20
URL = "http://streemcam:8081"


def test_helpers():
    assert COMPAT_SUFFIX == "~h264"
    assert compat_name("room") == "room~h264"
    assert compat_src("room") == "ffmpeg:room#video=h264#width=1280#audio=aac"
    assert tuya_src(URL, "bf1", KEY) == f"echo:curl -fsS {URL}/tuya/bf1?key={KEY}"


def test_desired_streams(cfg):
    catalog = Catalog(cfg)
    catalog.set_tuya([TuyaCamera("bf1", "Прихожая", True)])
    d = desired_streams(catalog, URL, KEY)
    assert d["tuya_bf1"] == tuya_src(URL, "bf1", KEY)
    assert d["tuya_bf1~h264"] == compat_src("tuya_bf1")
    assert "yard" not in d and "yard~h264" not in d  # потоки из конфига рендерит go2rtc-config
    assert len(d) == 2


def test_desired_streams_without_key_skips_tuya(cfg):
    catalog = Catalog(cfg)
    catalog.set_tuya([TuyaCamera("bf1", "Прихожая", True)])
    d = desired_streams(catalog, URL, None)
    assert d == {}


def recorder(config_yaml: str | None, config_status: int = 200,
             post_config_status: int = 200, restart_status: int = 200):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/api/config":
            calls.append(("GET", None))
            if config_yaml is None:
                return httpx.Response(config_status, text="")
            return httpx.Response(config_status, text=config_yaml)
        if request.method == "POST" and request.url.path == "/api/config":
            calls.append(("POST /api/config", request.content.decode("utf-8")))
            return httpx.Response(post_config_status)
        if request.method == "POST" and request.url.path == "/api/restart":
            calls.append(("POST /api/restart", None))
            return httpx.Response(restart_status)
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")
    return calls, httpx.AsyncClient(base_url="http://go2rtc", transport=httpx.MockTransport(handler))


async def test_no_change_skips_writes(cfg):
    catalog = Catalog(cfg)
    catalog.set_tuya([TuyaCamera("bf1", "Прихожая", True)])
    config = {
        "streams": {
            "xiaomi_cam": "xiaomi://...",
            "tuya_bf1": tuya_src(URL, "bf1", KEY),
            "tuya_bf1~h264": compat_src("tuya_bf1"),
        },
    }
    calls, http = recorder(yaml.safe_dump(config))
    await Go2rtcSync(http, catalog, URL, KEY).sync()
    assert calls == [("GET", None)]


async def test_no_change_normalises_single_element_list(cfg):
    catalog = Catalog(cfg)
    catalog.set_tuya([TuyaCamera("bf1", "Прихожая", True)])
    config = {
        "streams": {
            "tuya_bf1": [tuya_src(URL, "bf1", KEY)],
            "tuya_bf1~h264": [compat_src("tuya_bf1")],
        },
    }
    calls, http = recorder(yaml.safe_dump(config))
    await Go2rtcSync(http, catalog, URL, KEY).sync()
    assert calls == [("GET", None)]


async def test_new_tuya_camera_writes_config_and_restarts(cfg):
    catalog = Catalog(cfg)
    catalog.set_tuya([TuyaCamera("bf1", "Прихожая", True)])
    config = {
        "xiaomi": {"1234567": "secret-token"},
        "streams": {"manual": "rtsp://x"},
    }
    calls, http = recorder(yaml.safe_dump(config))
    await Go2rtcSync(http, catalog, URL, KEY).sync()
    kinds = [c[0] for c in calls]
    assert kinds == ["GET", "POST /api/config", "POST /api/restart"]
    posted = yaml.safe_load(calls[1][1])
    assert posted["xiaomi"] == {"1234567": "secret-token"}
    assert posted["streams"]["manual"] == "rtsp://x"
    assert posted["streams"]["tuya_bf1"] == tuya_src(URL, "bf1", KEY)
    assert posted["streams"]["tuya_bf1~h264"] == compat_src("tuya_bf1")


async def test_removed_tuya_camera_writes_config_and_restarts(cfg):
    catalog = Catalog(cfg)  # no Tuya cameras now
    config = {
        "streams": {
            "manual": "rtsp://x",
            "tuya_bf1": tuya_src(URL, "bf1", KEY),
            "tuya_bf1~h264": compat_src("tuya_bf1"),
        },
    }
    calls, http = recorder(yaml.safe_dump(config))
    await Go2rtcSync(http, catalog, URL, KEY).sync()
    kinds = [c[0] for c in calls]
    assert kinds == ["GET", "POST /api/config", "POST /api/restart"]
    posted = yaml.safe_load(calls[1][1])
    assert "tuya_bf1" not in posted["streams"]
    assert "tuya_bf1~h264" not in posted["streams"]
    assert posted["streams"]["manual"] == "rtsp://x"


async def test_get_config_error_skips_writes(cfg):
    calls, http = recorder(None, config_status=500)
    await Go2rtcSync(http, Catalog(cfg), URL, KEY).sync()
    assert calls == [("GET", None)]


async def test_post_config_error_skips_restart(cfg):
    catalog = Catalog(cfg)
    catalog.set_tuya([TuyaCamera("bf1", "Прихожая", True)])
    config = {"streams": {}}
    calls, http = recorder(yaml.safe_dump(config), post_config_status=500)
    await Go2rtcSync(http, catalog, URL, KEY).sync()
    kinds = [c[0] for c in calls]
    assert kinds == ["GET", "POST /api/config"]


async def test_sync_survives_go2rtc_down(cfg):
    def handler(request):
        raise httpx.ConnectError("down")
    http = httpx.AsyncClient(base_url="http://go2rtc", transport=httpx.MockTransport(handler))
    await Go2rtcSync(http, Catalog(cfg), URL, KEY).sync()  # не бросает
