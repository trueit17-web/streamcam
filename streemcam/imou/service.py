import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from ..catalog import Catalog
from ..config import Config
from .models import ImouAuthError, ImouError

log = logging.getLogger(__name__)

FAILURE_ALERT_AFTER = 3
# У Imou лимиты на число вызовов API. Адрес живого HLS живёт долго, поэтому удачный ответ держим часами,
# а ошибку — минуты: go2rtc переподключает пустой поток несколько раз в секунду.
STREAM_CACHE_SECONDS = 6 * 3600
STREAM_ERROR_SECONDS = 300
# go2rtc спросил адрес снова вскоре после прошлого раза — значит, по старому адресу видео не пошло.
# Тогда адрес обновляем, но не чаще раза в STREAM_REFRESH_SECONDS. Если облако снова отдаёт тот же адрес
# (например, заглушку вместо видео, когда кончился трафик), пауза до следующего обновления удваивается.
RECONNECT_SECONDS = 60
STREAM_REFRESH_SECONDS = 300
# список камер меняется редко; каждое обновление — несколько вызовов API на аккаунт
MIN_REFRESH_MINUTES = 60
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
        self._streams: dict[str, tuple[float, dict[int, str] | ImouError]] = {}
        self._asked: dict[str, float] = {}
        self._refresh_after: dict[str, float] = {}

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

    def _stale(self, device_id: str, now: float) -> bool:
        cached = self._streams.get(device_id)
        if cached is None:
            return True
        at, result = cached
        if isinstance(result, ImouError):
            return now - at >= STREAM_ERROR_SECONDS
        if now - at >= STREAM_CACHE_SECONDS:
            return True
        asked = self._asked.get(device_id)
        reconnect = asked is not None and now - asked < RECONNECT_SECONDS
        return reconnect and now - at >= self._refresh_after.get(device_id, STREAM_REFRESH_SECONDS)

    async def stream_url(self, device_id: str, sd: bool = False) -> str:
        """Адрес потока; sd=True — второй (SD) поток, если облако его отдаёт: меньше трафика для записи."""
        now = self._clock()
        if self._stale(device_id, now):
            try:
                result: dict[int, str] | ImouError = await self.client.stream_urls(device_id)
            except ImouError as e:
                result = e
            previous = self._streams.get(device_id)
            if previous is not None and previous[1] == result:
                self._refresh_after[device_id] = min(
                    2 * self._refresh_after.get(device_id, STREAM_REFRESH_SECONDS), STREAM_CACHE_SECONDS)
            else:
                self._refresh_after.pop(device_id, None)
            self._streams[device_id] = (now, result)
        self._asked[device_id] = now
        result = self._streams[device_id][1]
        if isinstance(result, ImouError):
            raise result
        return result.get(1 if sd else 0) or result[0]

    async def run(self) -> None:
        while True:
            await self.refresh()
            await asyncio.sleep(max(self.cfg.imou.refresh_minutes, MIN_REFRESH_MINUTES) * 60)

    async def _alert(self, text: str) -> None:
        if self._on_alert is not None:
            await self._on_alert(text)
