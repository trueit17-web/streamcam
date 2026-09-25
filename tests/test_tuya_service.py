import pytest

from streemcam.catalog import Catalog
from streemcam.tuya.login import QrSession
from streemcam.tuya.models import TuyaCamera, TuyaCredentials, TuyaError
from streemcam.tuya.service import FAILURE_ALERT_AFTER, TuyaService
from streemcam.tuya.store import TuyaStore

CREDS = TuyaCredentials("UC1", "term", "https://x", {"uid": "u1"})


class FakeClient:
    def __init__(self, cams=None):
        self.cams = cams if cams is not None else [TuyaCamera("bf1", "Прихожая", True)]
        self.fail = False

    def list_cameras(self):
        if self.fail:
            raise TuyaError("(1010) token invalid")
        return self.cams

    def allocate_rtsp(self, device_id):
        if self.fail:
            raise TuyaError("boom")
        return f"rtsps://tuya/{device_id}"


class FakeLogin:
    def __init__(self, polls):
        self.polls = list(polls)
        self.started = []

    def start(self, user_code):
        self.started.append(user_code)
        return QrSession(user_code, f"QR-{len(self.started)}")

    def poll(self, session):
        return self.polls.pop(0) if self.polls else None


class FakeSync:
    def __init__(self):
        self.count = 0

    async def sync(self):
        self.count += 1


@pytest.fixture
def env(cfg):
    client = FakeClient()
    alerts = []

    async def on_alert(text):
        alerts.append(text)

    def make(polls=()):
        store = TuyaStore(":memory:")
        sync = FakeSync()
        catalog = Catalog(cfg)
        svc = TuyaService(cfg, catalog, store, sync=sync, on_alert=on_alert,
                          login=FakeLogin(polls), client_factory=lambda creds, st: client)
        return svc, store, sync, catalog
    return make, client, alerts


async def test_not_logged_in(env):
    make, _, _ = env
    svc, _, sync, catalog = make()
    svc.load()
    assert svc.logged_in is False
    assert await svc.refresh() is False
    assert catalog.tuya() == [] and sync.count == 1
    with pytest.raises(TuyaError):
        await svc.stream_url("bf1")


async def test_login_success(env):
    make, _, _ = env
    svc, store, sync, catalog = make(polls=[None, CREDS])
    session = await svc.start_login("UC1")
    assert session.qr_payload == "tuyaSmart--qrLogin?token=QR-1"
    assert await svc.wait_login(session, timeout=1.0, interval=0) is True
    assert svc.logged_in and store.load() == CREDS
    assert [c.id for c in svc.cameras()] == ["tuya_bf1"]
    assert sync.count == 1
    assert await svc.stream_url("bf1") == "rtsps://tuya/bf1"


async def test_login_timeout(env):
    make, _, _ = env
    svc, store, _, _ = make(polls=[])
    session = await svc.start_login("UC1")
    assert await svc.wait_login(session, timeout=0.05, interval=0.01) is False
    assert not svc.logged_in and store.load() is None


async def test_new_login_cancels_previous(env):
    make, _, _ = env
    svc, _, _, _ = make(polls=[None, None, CREDS])
    first = await svc.start_login("UC1")
    await svc.start_login("UC1")
    assert await svc.wait_login(first, timeout=1.0, interval=0) is False


async def test_load_from_store(env):
    make, _, _ = env
    svc, store, _, _ = make()
    store.save(CREDS)
    svc.load()
    assert svc.logged_in
    assert await svc.refresh() is True


async def test_refresh_failures_alert_once(env):
    make, client, alerts = env
    svc, store, _, catalog = make()
    store.save(CREDS)
    svc.load()
    await svc.refresh()
    client.fail = True
    for _ in range(FAILURE_ALERT_AFTER + 2):
        assert await svc.refresh() is False
    assert len(alerts) == 1 and "/tuya_login" in alerts[0]
    assert [c.id for c in catalog.tuya()] == ["tuya_bf1"]  # последний известный список сохраняется
    client.fail = False
    assert await svc.refresh() is True
    client.fail = True
    for _ in range(FAILURE_ALERT_AFTER):
        await svc.refresh()
    assert len(alerts) == 2


async def test_logout(env):
    make, _, _ = env
    svc, store, sync, catalog = make()
    store.save(CREDS)
    svc.load()
    await svc.refresh()
    await svc.logout()
    assert not svc.logged_in and store.load() is None
    assert catalog.tuya() == [] and sync.count == 2


async def test_login_race_stale_poll_ignored(env):
    """TOCTOU race: poll for stale session should not commit credentials."""
    make, _, _ = env
    svc, store, _, _ = make()

    # Custom login that simulates concurrent start_login during poll
    class RaceLogin:
        def __init__(self, svc):
            self.svc = svc
            self.started = []

        def start(self, user_code):
            self.started.append(user_code)
            return QrSession(user_code, f"QR-{len(self.started)}")

        def poll(self, session):
            # Simulate concurrent start_login while we're polling
            if session.qr_payload == "tuyaSmart--qrLogin?token=QR-1":
                # Start a new login attempt (sets _current_session to QR-2)
                self.svc._current_session = QrSession("UC1", "QR-2")
                # Return credentials for the old session
                return CREDS
            return None

    race_login = RaceLogin(svc)
    svc._login = race_login

    # Start first login (QR-1)
    first = await svc.start_login("UC1")
    assert first.qr_payload == "tuyaSmart--qrLogin?token=QR-1"

    # Wait for login; poll will trigger concurrent start_login and return CREDS
    result = await svc.wait_login(first, timeout=1.0, interval=0)

    # Stale poll result should be ignored
    assert result is False
    assert store.load() is None  # Credentials not saved
    assert svc.logged_in is False  # Client not created
