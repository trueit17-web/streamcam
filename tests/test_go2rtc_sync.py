import httpx

from streemcam.catalog import Catalog
from streemcam.go2rtc_sync import (COMPAT_SUFFIX, Go2rtcSync, compat_name, compat_src,
                                   desired_streams, tuya_src)
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
    assert d["yard~h264"] == compat_src("yard")
    assert "yard" not in d  # потоки из конфига рендерит go2rtc-config
    assert len(d) == 5


def test_desired_streams_without_key_skips_tuya(cfg):
    catalog = Catalog(cfg)
    catalog.set_tuya([TuyaCamera("bf1", "Прихожая", True)])
    d = desired_streams(catalog, URL, None)
    assert "tuya_bf1" not in d and "tuya_bf1~h264" not in d


def recorder(current):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, dict(request.url.params)))
        if request.method == "GET":
            return httpx.Response(200, json=current)
        return httpx.Response(200)
    return calls, httpx.AsyncClient(base_url="http://go2rtc", transport=httpx.MockTransport(handler))


async def test_sync_puts_missing_and_deletes_stale(cfg):
    catalog = Catalog(cfg)
    catalog.set_tuya([TuyaCamera("bf1", "Прихожая", True)])
    current = {
        "yard": {"producers": [{"url": "rtsp://x"}]},
        "yard~h264": {"producers": [{"url": compat_src("yard")}]},   # уже верный — не трогаем
        "tuya_old": {"producers": [{"url": "echo:old"}]},             # устарел — удалить
        "gate~h264": {"producers": [{"url": "ffmpeg:gate#old"}]},     # другой src — заменить
        "manual": {"producers": []},                                   # чужой — не трогаем
    }
    calls, http = recorder(current)
    await Go2rtcSync(http, catalog, URL, KEY).sync()
    puts = {p["name"]: p["src"] for m, p in calls if m == "PUT"}
    deletes = [p["src"] for m, p in calls if m == "DELETE"]
    assert "yard~h264" not in puts
    assert puts["gate~h264"] == compat_src("gate")
    assert puts["room~h264"] == compat_src("room")
    assert puts["tuya_bf1"] == tuya_src(URL, "bf1", KEY)
    assert puts["tuya_bf1~h264"] == compat_src("tuya_bf1")
    assert deletes == ["tuya_old"]


async def test_sync_survives_go2rtc_down(cfg):
    def handler(request):
        raise httpx.ConnectError("down")
    http = httpx.AsyncClient(base_url="http://go2rtc", transport=httpx.MockTransport(handler))
    await Go2rtcSync(http, Catalog(cfg), URL, KEY).sync()  # не бросает
