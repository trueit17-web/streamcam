import asyncio
import logging

import httpx
import yaml

from .catalog import Catalog
from .go2rtc_config import COMPAT_SUFFIX, compat_name, compat_src

log = logging.getLogger(__name__)

__all__ = ["COMPAT_SUFFIX", "compat_name", "compat_src", "tuya_src", "desired_streams", "Go2rtcSync"]


def tuya_src(internal_url: str, device_id: str, key: str) -> str:
    # device_id проверен Catalog.set_tuya (^[A-Za-z0-9]+$), key — Config (^[A-Za-z0-9_-]{16,}$)
    return f"echo:curl -fsS {internal_url}/tuya/{device_id}?key={key}"


def desired_streams(catalog: Catalog, internal_url: str, key: str | None) -> dict[str, str]:
    streams: dict[str, str] = {}
    if not key:
        return streams
    for cam in catalog.all():
        if cam.kind != "tuya":
            continue
        name = cam.id
        streams[name] = tuya_src(internal_url, cam.device_id, key)
        streams[compat_name(name)] = compat_src(name)
    return streams


def _normalize_src(value) -> str | None:
    if isinstance(value, list):
        return value[0] if value else None
    return value


class Go2rtcSync:
    def __init__(self, http: httpx.AsyncClient, catalog: Catalog, internal_url: str, internal_key: str | None):
        self._http = http
        self._catalog = catalog
        self._internal_url = internal_url
        self._key = internal_key
        self._lock = asyncio.Lock()

    async def sync(self) -> None:
        async with self._lock:
            await self._sync_locked()

    async def _sync_locked(self) -> None:
        desired = desired_streams(self._catalog, self._internal_url, self._key)
        try:
            r = await self._http.get("/api/config")
        except httpx.HTTPError as e:
            log.warning("go2rtc sync skipped: %s", e)
            return
        if r.status_code != 200:
            log.warning("go2rtc sync skipped: GET /api/config -> %s", r.status_code)
            return
        try:
            config = yaml.safe_load(r.text) or {}
        except yaml.YAMLError as e:
            log.warning("go2rtc sync skipped: cannot parse config: %s", e)
            return
        if not isinstance(config, dict):
            log.warning("go2rtc sync skipped: config is not a mapping")
            return

        streams = config.get("streams") or {}
        if not isinstance(streams, dict):
            streams = {}
        current_managed = {name: _normalize_src(src) for name, src in streams.items()
                           if name.startswith("tuya_")}
        if current_managed == desired:
            return

        new_streams = {name: src for name, src in streams.items() if not name.startswith("tuya_")}
        new_streams.update(desired)
        new_config = dict(config)
        new_config["streams"] = new_streams

        try:
            r = await self._http.post("/api/config",
                                       content=yaml.safe_dump(new_config, allow_unicode=True, sort_keys=False))
        except httpx.HTTPError as e:
            log.warning("go2rtc sync: cannot write config: %s", e)
            return
        if not (200 <= r.status_code < 300):
            log.error("go2rtc sync: POST /api/config -> %s", r.status_code)
            return

        try:
            r = await self._http.post("/api/restart")
        except httpx.HTTPError as e:
            log.warning("go2rtc sync: cannot restart: %s", e)
            return
        if not (200 <= r.status_code < 300):
            log.warning("go2rtc sync: POST /api/restart -> %s", r.status_code)
