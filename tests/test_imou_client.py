import hashlib
import json

import httpx
import pytest

from streemcam.imou.client import ImouClient
from streemcam.imou.models import ImouAuthError, ImouCamera, ImouError

API = "https://openapi.example/openapi"


def ok(data=None):
    return {"result": {"code": "0", "msg": "ok", "data": data or {}}}


def err(code, msg="fail"):
    return {"result": {"code": code, "msg": msg}}


TOKEN = ok({"accessToken": "T1", "expireTime": 259200})


class FakeCloud:
    """Заглушка облака: method -> очередь ответов (dict — JSON-тело, httpx.Response — как есть)."""

    def __init__(self, responses: dict):
        self.responses = {k: list(v) for k, v in responses.items()}
        self.calls: list[tuple[str, dict]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        body = json.loads(request.content)
        self.calls.append((method, body))
        queue = self.responses.get(method)
        assert queue, f"unexpected call {method}"
        resp = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(resp, httpx.Response):
            return resp
        return httpx.Response(200, json=resp)

    def methods(self):
        return [m for m, _ in self.calls]


def make(responses):
    cloud = FakeCloud(responses)
    http = httpx.AsyncClient(transport=httpx.MockTransport(cloud.handler))
    client = ImouClient("APP", "SECRET", API, http, clock=lambda: 1700000000, nonce=lambda: "n1")
    return client, cloud


async def test_sign_and_body():
    client, cloud = make({"accessToken": [TOKEN], "deviceBaseList": [ok({"deviceList": []})]})
    assert await client.list_cameras() == []
    (m1, b1), (m2, b2) = cloud.calls
    assert m1 == "accessToken"
    sign = hashlib.md5(b"time:1700000000,nonce:n1,appSecret:SECRET").hexdigest()
    assert b1["system"] == {"ver": "1.0", "sign": sign, "appId": "APP", "time": 1700000000, "nonce": "n1"}
    assert "token" not in b1["params"]
    assert isinstance(b1["id"], str)
    assert m2 == "deviceBaseList"
    assert b2["params"]["token"] == "T1"
    assert b2["params"]["bindId"] == -1 and b2["params"]["limit"] == 50
    assert b2["params"]["type"] == "bindAndShare"


async def test_token_reused():
    client, cloud = make({"accessToken": [TOKEN], "deviceBaseList": [ok({"deviceList": []})]})
    await client.list_cameras()
    await client.list_cameras()
    assert cloud.methods().count("accessToken") == 1


async def test_tk1002_relogin_once():
    client, cloud = make({"accessToken": [TOKEN, ok({"accessToken": "T2"})],
                          "deviceBaseList": [err("TK1002"), ok({"deviceList": []})]})
    assert await client.list_cameras() == []
    assert cloud.methods() == ["accessToken", "deviceBaseList", "accessToken", "deviceBaseList"]
    assert cloud.calls[-1][1]["params"]["token"] == "T2"


async def test_tk1002_twice_raises():
    client, cloud = make({"accessToken": [TOKEN], "deviceBaseList": [err("TK1002")]})
    with pytest.raises(ImouError):
        await client.list_cameras()
    assert cloud.methods().count("accessToken") == 2


@pytest.mark.parametrize("code", ["OP1008", "SN1001", "OP1009"])
async def test_auth_codes(code):
    client, _ = make({"accessToken": [err(code)]})
    with pytest.raises(ImouAuthError):
        await client.list_cameras()


@pytest.mark.parametrize("resp", [httpx.Response(500), httpx.Response(200, text="not json"),
                                  httpx.Response(200, json={"foo": 1})])
async def test_bad_http_responses(resp):
    client, _ = make({"accessToken": [TOKEN], "deviceBaseList": [resp]})
    with pytest.raises(ImouError) as e:
        await client.list_cameras()
    assert not isinstance(e.value, ImouAuthError)


async def test_transport_error():
    def boom(request):
        raise httpx.ConnectError("refused")
    http = httpx.AsyncClient(transport=httpx.MockTransport(boom))
    client = ImouClient("APP", "SECRET", API, http)
    with pytest.raises(ImouError):
        await client.list_cameras()


async def test_error_text_has_no_secrets():
    client, _ = make({"accessToken": [TOKEN], "deviceBaseList": [err("DV1007", "bad token T1 SECRET")]})
    with pytest.raises(ImouError) as e:
        await client.list_cameras()
    assert "DV1007" in str(e.value)
    assert "SECRET" not in str(e.value) and "T1" not in str(e.value)


async def test_list_cameras():
    client, cloud = make({
        "accessToken": [TOKEN],
        "deviceBaseList": [ok({"count": 3, "deviceList": [{"deviceId": "AB12CD"}, {"deviceId": "ef34"},
                                                          {"deviceId": "GH56"}]})],
        "deviceBaseDetailList": [ok({"deviceList": [{"deviceId": "AB12CD", "name": "Улица"},
                                                    {"deviceId": "ef34", "name": "Гараж"},
                                                    {"deviceId": "GH56"}]})],
        "deviceOnline": [ok({"onLine": "1"}), ok({"onLine": "4"}), ok({"onLine": "0"})],
    })
    assert await client.list_cameras() == [
        ImouCamera("AB12CD", "Улица", True),
        ImouCamera("ef34", "Гараж", True),
        ImouCamera("GH56", "GH56", False),
    ]
    detail = next(b for m, b in cloud.calls if m == "deviceBaseDetailList")
    assert detail["params"]["deviceList"] == [{"deviceId": d, "channelList": "0"} for d in ("AB12CD", "ef34", "GH56")]
    online_ids = [b["params"]["deviceId"] for m, b in cloud.calls if m == "deviceOnline"]
    assert online_ids == ["AB12CD", "ef34", "GH56"]


async def test_list_cameras_online_error_is_offline():
    client, _ = make({
        "accessToken": [TOKEN],
        "deviceBaseList": [ok({"deviceList": [{"deviceId": "AB12CD"}]})],
        "deviceBaseDetailList": [ok({"deviceList": [{"deviceId": "AB12CD", "name": "Улица"}]})],
        "deviceOnline": [err("DV1003")],
    })
    assert await client.list_cameras() == [ImouCamera("AB12CD", "Улица", False)]


async def test_list_cameras_online_auth_error_propagates():
    client, _ = make({
        "accessToken": [TOKEN],
        "deviceBaseList": [ok({"deviceList": [{"deviceId": "AB12CD"}]})],
        "deviceBaseDetailList": [ok({"deviceList": [{"deviceId": "AB12CD", "name": "Улица"}]})],
        "deviceOnline": [err("OP1009")],
    })
    with pytest.raises(ImouAuthError):
        await client.list_cameras()


async def test_list_cameras_empty():
    client, cloud = make({"accessToken": [TOKEN], "deviceBaseList": [ok({"count": 0, "deviceList": []})]})
    assert await client.list_cameras() == []
    assert cloud.methods() == ["accessToken", "deviceBaseList"]


def live(*streams):
    return ok({"streams": list(streams)})


def stream(sid, hls, status="1"):
    return {"streamId": sid, "status": status, "hls": hls, "liveToken": "LT"}


async def test_stream_url_existing():
    client, cloud = make({"accessToken": [TOKEN], "getLiveStreamInfo": [
        live(stream(1, "https://h/1.m3u8"), stream(0, "https://h/0.m3u8"))]})
    assert await client.stream_url("AB12CD") == "https://h/0.m3u8"
    assert "bindDeviceLive" not in cloud.methods()
    assert cloud.calls[-1][1]["params"]["deviceId"] == "AB12CD"
    assert cloud.calls[-1][1]["params"]["channelId"] == "0"


async def test_stream_url_binds_when_missing():
    client, cloud = make({"accessToken": [TOKEN],
                          "getLiveStreamInfo": [err("LV1002"), live(stream(0, "https://h/0.m3u8"))],
                          "bindDeviceLive": [ok({"liveToken": "LT"})]})
    assert await client.stream_url("AB12CD") == "https://h/0.m3u8"
    bind = next(b for m, b in cloud.calls if m == "bindDeviceLive")
    assert bind["params"] == {"deviceId": "AB12CD", "channelId": "0", "streamId": 0, "token": "T1"}


async def test_stream_url_ignores_http_and_inactive():
    client, cloud = make({"accessToken": [TOKEN],
                          "getLiveStreamInfo": [live(stream(0, "http://h/0.m3u8"), stream(0, "https://h/x.m3u8", "0")),
                                                live(stream(0, "https://h/0.m3u8"))],
                          "bindDeviceLive": [ok({})]})
    assert await client.stream_url("AB12CD") == "https://h/0.m3u8"
    assert "bindDeviceLive" in cloud.methods()


async def test_stream_url_bind_already_exists():
    client, _ = make({"accessToken": [TOKEN],
                      "getLiveStreamInfo": [err("LV1002"), live(stream(0, "https://h/0.m3u8"))],
                      "bindDeviceLive": [err("LV1001")]})
    assert await client.stream_url("AB12CD") == "https://h/0.m3u8"


async def test_stream_url_none_raises():
    client, _ = make({"accessToken": [TOKEN], "getLiveStreamInfo": [err("LV1002")],
                      "bindDeviceLive": [ok({})]})
    with pytest.raises(ImouError, match="AB12CD"):
        await client.stream_url("AB12CD")


async def test_stream_url_other_error_raises():
    client, _ = make({"accessToken": [TOKEN], "getLiveStreamInfo": [err("DV1007")]})
    with pytest.raises(ImouError):
        await client.stream_url("AB12CD")
