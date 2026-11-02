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

    async def stream_urls(self, device_id):
        self.stream_calls = getattr(self, "stream_calls", 0) + 1
        if self.fail:
            raise self.fail
        return {0: f"https://h/{device_id}.m3u8", 1: f"https://h/{device_id}-sd.m3u8"}


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


async def test_stream_url_sd(env):
    svc, client, *_ = env
    assert await svc.stream_url("AB12CD", sd=True) == "https://h/AB12CD-sd.m3u8"
    assert await svc.stream_url("AB12CD") == "https://h/AB12CD.m3u8"
    assert client.stream_calls == 1  # HD и SD приходят одним вызовом


async def test_stream_url_sd_falls_back_to_hd(cfg):
    class HdOnly(FakeClient):
        async def stream_urls(self, device_id):
            return {0: "https://h/hd.m3u8"}
    svc = ImouService(cfg, Catalog(cfg), HdOnly())
    assert await svc.stream_url("AB12CD", sd=True) == "https://h/hd.m3u8"


async def test_stream_url_cached_for_hours_errors_for_minutes(cfg):
    from streemcam.imou.service import STREAM_CACHE_SECONDS, STREAM_ERROR_SECONDS
    t = [0.0]
    client = FakeClient()
    svc = ImouService(cfg, Catalog(cfg), client, clock=lambda: t[0])
    assert await svc.stream_url("AB12CD") == "https://h/AB12CD.m3u8"
    t[0] = 3600.0                       # снимок раз в час: адрес из кэша, без вызова API
    assert await svc.stream_url("AB12CD") == "https://h/AB12CD.m3u8"
    assert client.stream_calls == 1
    t[0] = STREAM_CACHE_SECONDS
    client.fail = ImouError("OP1009 No right")
    for _ in range(3):
        with pytest.raises(ImouError):
            await svc.stream_url("AB12CD")
    assert client.stream_calls == 2
    t[0] += STREAM_ERROR_SECONDS
    client.fail = None
    assert await svc.stream_url("AB12CD") == "https://h/AB12CD.m3u8"
    assert client.stream_calls == 3


async def test_quick_reconnect_refreshes_at_most_every_few_minutes(cfg):
    from streemcam.imou.service import STREAM_REFRESH_SECONDS
    t = [0.0]
    client = FakeClient()
    svc = ImouService(cfg, Catalog(cfg), client, clock=lambda: t[0])
    await svc.stream_url("AB12CD")
    while t[0] < STREAM_REFRESH_SECONDS - 5:  # go2rtc переподключается каждые 5 с: по адресу нет видео
        t[0] += 5
        await svc.stream_url("AB12CD")
    assert client.stream_calls == 1
    t[0] += 5
    await svc.stream_url("AB12CD")      # адрес старше 5 минут и снова нужен сразу — обновляем
    assert client.stream_calls == 2
    t[0] += 5
    await svc.stream_url("AB12CD")
    assert client.stream_calls == 2


async def test_same_address_on_refresh_doubles_the_pause(cfg):
    from streemcam.imou.service import STREAM_REFRESH_SECONDS
    t = [0.0]
    client = FakeClient()
    svc = ImouService(cfg, Catalog(cfg), client, clock=lambda: t[0])

    async def reconnect_loop(seconds):
        end = t[0] + seconds
        while t[0] < end:
            t[0] += 5
            await svc.stream_url("AB12CD")

    await svc.stream_url("AB12CD")
    await reconnect_loop(STREAM_REFRESH_SECONDS)
    assert client.stream_calls == 2      # через 5 минут обновили, облако отдало тот же адрес
    await reconnect_loop(2 * STREAM_REFRESH_SECONDS - 10)
    assert client.stream_calls == 2      # следующая попытка — только через 10 минут
    await reconnect_loop(10)
    assert client.stream_calls == 3
