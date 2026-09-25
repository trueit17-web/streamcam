import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from .catalog import Catalog
from .config import Config

log = logging.getLogger(__name__)

AlertFn = Callable[[str, bool], Awaitable[None]]


@dataclass
class _State:
    online: bool | None = None
    offline_since: float | None = None
    alerted: bool = False
    snapshot: bytes | None = None


class CameraMonitor:
    def __init__(self, cfg: Config, catalog: Catalog, http: httpx.AsyncClient,
                 on_alert: AlertFn | None = None, clock: Callable[[], float] = time.monotonic):
        self.cfg = cfg
        self.catalog = catalog
        self._http = http
        self._on_alert = on_alert
        self._clock = clock
        self._state: dict[str, _State] = {}

    def _st(self, cam_id: str) -> _State:
        return self._state.setdefault(cam_id, _State())

    def is_online(self, cam_id: str) -> bool | None:
        state = self._state.get(cam_id)
        return state.online if state else None

    def snapshot(self, cam_id: str) -> bytes | None:
        state = self._state.get(cam_id)
        return state.snapshot if state else None

    async def probe(self, cam_id: str) -> None:
        state = self._st(cam_id)
        image = None
        try:
            r = await self._http.get("/api/frame.jpeg", params={"src": cam_id}, timeout=20)
            if r.status_code == 200 and r.content:
                image = r.content
        except httpx.HTTPError as e:
            log.debug("probe %s failed: %s", cam_id, e)

        now = self._clock()
        if image is not None:
            state.online = True
            state.offline_since = None
            state.snapshot = image
            if state.alerted:
                state.alerted = False
                await self._alert(cam_id, True)
            return

        if state.online is not False:
            log.warning("camera %s is offline", cam_id)
        state.online = False
        if state.offline_since is None:
            state.offline_since = now
        if not state.alerted and now - state.offline_since >= self.cfg.offline_alert_minutes * 60:
            state.alerted = True
            await self._alert(cam_id, False)

    async def probe_all(self) -> None:
        await asyncio.gather(*(self.probe(cam.id) for cam in self.catalog.all()))

    async def run(self) -> None:
        while True:
            await self.probe_all()
            await asyncio.sleep(self.cfg.probe_interval_seconds)

    async def _alert(self, cam_id: str, online: bool) -> None:
        if self._on_alert is None:
            return
        try:
            await self._on_alert(cam_id, online)
        except Exception:
            log.exception("alert for %s failed", cam_id)
