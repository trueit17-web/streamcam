import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from ..catalog import CameraInfo, Catalog
from ..config import Config
from .login import QrSession, TuyaLogin
from .models import TuyaError
from .store import TuyaStore

log = logging.getLogger(__name__)

FAILURE_ALERT_AFTER = 3
RELOGIN_TEXT = ("⚠️ Tuya: не удаётся получить список камер несколько раз подряд. "
                "Возможно, нужен повторный вход: /tuya_login <код пользователя>.")


class TuyaService:
    def __init__(self, cfg: Config, catalog: Catalog, store: TuyaStore, sync=None,
                 on_alert: Callable[[str], Awaitable[None]] | None = None,
                 login: TuyaLogin | None = None, client_factory=None):
        self.cfg = cfg
        self.catalog = catalog
        self._store = store
        self._sync = sync
        self._on_alert = on_alert
        self._login = login
        if client_factory is None:
            from .client import TuyaClient
            client_factory = TuyaClient.from_credentials
        self._client_factory = client_factory
        self._client = None
        self._current_session: QrSession | None = None
        self._failures = 0

    @property
    def logged_in(self) -> bool:
        return self._client is not None

    def load(self) -> None:
        creds = self._store.load()
        if creds is not None:
            self._client = self._client_factory(creds, self._store)

    def cameras(self) -> list[CameraInfo]:
        return self.catalog.tuya()

    def account_uid(self) -> str | None:
        creds = self._store.load()
        if creds is None:
            return None
        return creds.token_info.get("uid")

    async def start_login(self, user_code: str) -> QrSession:
        if self._login is None:
            self._login = TuyaLogin()
        session = await asyncio.to_thread(self._login.start, user_code)
        self._current_session = session
        return session

    async def wait_login(self, session: QrSession, timeout: float = 120.0, interval: float = 2.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._current_session is not session:
                return False  # начата новая попытка входа
            try:
                creds = await asyncio.to_thread(self._login.poll, session)
            except Exception as e:
                log.warning("tuya login poll failed: %s", e)
                creds = None
            if creds is not None:
                # Re-check that this session is still current after poll returns (TOCTOU fix)
                if self._current_session is not session:
                    return False  # новая попытка началась, проигнорируем старый результат
                self._store.save(creds)
                self._client = self._client_factory(creds, self._store)
                self._current_session = None
                self._failures = 0
                await self.refresh()
                return True
            await asyncio.sleep(interval)
        return False

    async def refresh(self) -> bool:
        if self._client is None:
            self.catalog.set_tuya([])
            await self._run_sync()
            return False
        try:
            cams = await asyncio.to_thread(self._client.list_cameras)
        except TuyaError as e:
            self._failures += 1
            log.warning("tuya refresh failed (%d in a row): %s", self._failures, e)
            if self._failures == FAILURE_ALERT_AFTER:
                self.catalog.mark_tuya_offline()
                if self._on_alert is not None:
                    await self._on_alert(RELOGIN_TEXT)
            return False
        self._failures = 0
        self.catalog.set_tuya(cams)
        await self._run_sync()
        return True

    async def logout(self) -> None:
        self._store.clear()
        self._client = None
        self._failures = 0
        self.catalog.set_tuya([])
        await self._run_sync()

    async def stream_url(self, device_id: str) -> str:
        if self._client is None:
            raise TuyaError("not logged in to Tuya")
        return await asyncio.to_thread(self._client.allocate_rtsp, device_id)

    async def run(self) -> None:
        while True:
            await self.refresh()
            await asyncio.sleep(self.cfg.tuya.refresh_minutes * 60)

    async def _run_sync(self) -> None:
        if self._sync is not None:
            await self._sync.sync()
