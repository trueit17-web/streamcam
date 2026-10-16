import pytest

from streemcam.catalog import Catalog
from streemcam.imou.models import ImouAuthError, ImouCamera, ImouError
from streemcam.imou.service import AUTH_TEXT, FAIL_TEXT, FAILURE_ALERT_AFTER, ImouService


class FakeClient:
    def __init__(self):
        self.cams = [ImouCamera("AB12CD", "Улица", True)]
        self.fail: Exception | None = None

    async def list_cameras(self):
        if self.fail:
            raise self.fail
        return self.cams

    async def stream_url(self, device_id):
        self.stream_calls = getattr(self, "stream_calls", 0) + 1
        if self.fail:
            raise self.fail
        return f"https://h/{device_id}.m3u8"


class FakeSync:
    def __init__(self):
        self.count = 0

    async def sync(self):
        self.count += 1


@pytest.fixture
def env(cfg):
    client, sync, alerts = FakeClient(), FakeSync(), []

    async def on_alert(text):
        alerts.append(text)

    catalog = Catalog(cfg)
    svc = ImouService(cfg, catalog, client, sync=sync, on_alert=on_alert)
    return svc, client, sync, alerts, catalog


async def test_refresh_ok(env):
    svc, client, sync, alerts, catalog = env
    assert await svc.refresh() is True
    assert [c.id for c in catalog.imou()] == ["imou_ab12cd"]
    assert sync.count == 1 and alerts == []
    assert svc.client is client


async def test_refresh_error_marks_offline_and_alerts_on_third(env):
    svc, client, sync, alerts, catalog = env
    await svc.refresh()
    client.fail = ImouError("down")
    for i in range(FAILURE_ALERT_AFTER + 1):
        assert await svc.refresh() is False
        assert catalog.imou()[0].online is False
        assert len(alerts) == (1 if i >= FAILURE_ALERT_AFTER - 1 else 0)
    assert alerts == [FAIL_TEXT]
    client.fail = None
    assert await svc.refresh() is True
    assert catalog.imou()[0].online is True
    client.fail = ImouError("down")
    for _ in range(FAILURE_ALERT_AFTER):
        await svc.refresh()
    assert alerts == [FAIL_TEXT, FAIL_TEXT]


async def test_auth_error_alerts_once_until_success(env):
    svc, client, _, alerts, catalog = env
    client.fail = ImouAuthError("OP1008")
    assert await svc.refresh() is False
    assert await svc.refresh() is False
    assert alerts == [AUTH_TEXT]
    client.fail = None
    await svc.refresh()
    client.fail = ImouAuthError("OP1008")
    await svc.refresh()
    assert alerts == [AUTH_TEXT, AUTH_TEXT]


async def test_refresh_without_alert_callback(cfg):
    client = FakeClient()
    client.fail = ImouAuthError("OP1008")
    svc = ImouService(cfg, Catalog(cfg), client)
    assert await svc.refresh() is False


async def test_stream_url_delegates(env):
    svc, *_ = env
    assert await svc.stream_url("AB12CD") == "https://h/AB12CD.m3u8"


async def test_stream_url_cached_including_errors(cfg):
    from streemcam.imou.service import STREAM_CACHE_SECONDS
    t = [0.0]
    client = FakeClient()
    svc = ImouService(cfg, Catalog(cfg), client, clock=lambda: t[0])
    assert await svc.stream_url("AB12CD") == "https://h/AB12CD.m3u8"
    assert await svc.stream_url("AB12CD") == "https://h/AB12CD.m3u8"
    assert client.stream_calls == 1
    t[0] = STREAM_CACHE_SECONDS
    client.fail = ImouError("OP1009 No right")
    for _ in range(3):
        with pytest.raises(ImouError):
            await svc.stream_url("AB12CD")
    assert client.stream_calls == 2
    t[0] = 2 * STREAM_CACHE_SECONDS
    client.fail = None
    assert await svc.stream_url("AB12CD") == "https://h/AB12CD.m3u8"
    assert client.stream_calls == 3
