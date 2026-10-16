import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from ..catalog import Catalog
from ..config import Config
from .models import ImouAuthError, ImouError

log = logging.getLogger(__name__)

FAILURE_ALERT_AFTER = 3
# go2rtc переподключает пустой поток несколько раз в секунду; без кэша каждый раз уходит запрос в облако
STREAM_CACHE_SECONDS = 20
AUTH_TEXT = "⚠️ Imou: облако отклонило appId/appSecret. Проверьте IMOU_APP_ID и IMOU_APP_SECRET в .env."
FAIL_TEXT = ("⚠️ Imou: не удаётся получить список камер несколько раз подряд. "
             "Проверьте доступ к облаку Imou и imou.api_url.")


class ImouService:
    """Раз в refresh_minutes обновляет список камер Imou в каталоге и потоки go2rtc."""

    def __init__(self, cfg: Config, catalog: Catalog, client, sync=None,
                 on_alert: Callable[[str], Awaitable[None]] | None = None, clock=time.monotonic):
        self.cfg = cfg
        self.catalog = catalog
        self.client = client
        self._sync = sync
        self._on_alert = on_alert
        self._failures = 0
        self._auth_alerted = False
        self._clock = clock
        self._streams: dict[str, tuple[float, str | ImouError]] = {}

    async def refresh(self) -> bool:
        try:
            cams = await self.client.list_cameras()
        except ImouAuthError as e:
            log.error("imou: credentials rejected: %s", e)
            self.catalog.mark_imou_offline()
            if not self._auth_alerted:
                self._auth_alerted = True
                await self._alert(AUTH_TEXT)
            return False
        except ImouError as e:
            self._failures += 1
            log.warning("imou refresh failed (%d in a row): %s", self._failures, e)
            self.catalog.mark_imou_offline()
            if self._failures == FAILURE_ALERT_AFTER:
                await self._alert(FAIL_TEXT)
            return False
        self._failures = 0
        self._auth_alerted = False
        self.catalog.set_imou(cams)
        if self._sync is not None:
            await self._sync.sync()
        return True

    async def stream_url(self, device_id: str) -> str:
        now = self._clock()
        cached = self._streams.get(device_id)
        if cached is None or now - cached[0] >= STREAM_CACHE_SECONDS:
            try:
                result: str | ImouError = await self.client.stream_url(device_id)
            except ImouError as e:
                result = e
            cached = (now, result)
            self._streams[device_id] = cached
        if isinstance(cached[1], ImouError):
            raise cached[1]
        return cached[1]

    async def run(self) -> None:
        while True:
            await self.refresh()
            await asyncio.sleep(self.cfg.imou.refresh_minutes * 60)

    async def _alert(self, text: str) -> None:
        if self._on_alert is not None:
            await self._on_alert(text)
