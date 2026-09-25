import logging

import httpx

from .catalog import Catalog

log = logging.getLogger(__name__)

COMPAT_SUFFIX = "~h264"


def compat_name(cam_id: str) -> str:
    return cam_id + COMPAT_SUFFIX


def compat_src(cam_id: str) -> str:
    return f"ffmpeg:{cam_id}#video=h264#width=1280#audio=aac"


def tuya_src(internal_url: str, device_id: str, key: str) -> str:
    # device_id проверен Catalog.set_tuya (^[A-Za-z0-9]+$), key — Config (^[A-Za-z0-9_-]{16,}$)
    return f"echo:curl -fsS {internal_url}/tuya/{device_id}?key={key}"


def desired_streams(catalog: Catalog, internal_url: str, key: str | None) -> dict[str, str]:
    streams: dict[str, str] = {}
    for cam in catalog.all():
        if cam.kind == "tuya":
            if not key:
                continue
            streams[cam.id] = tuya_src(internal_url, cam.device_id, key)
        streams[compat_name(cam.id)] = compat_src(cam.id)
    return streams


def _managed(name: str) -> bool:
    return name.startswith("tuya_") or name.endswith(COMPAT_SUFFIX)


def _current_src(info) -> str | None:
    producers = (info or {}).get("producers") or []
    if producers and isinstance(producers[0], dict):
        return producers[0].get("url")
    return None


class Go2rtcSync:
    def __init__(self, http: httpx.AsyncClient, catalog: Catalog, internal_url: str, internal_key: str | None):
        self._http = http
        self._catalog = catalog
        self._internal_url = internal_url
        self._key = internal_key

    async def sync(self) -> None:
        desired = desired_streams(self._catalog, self._internal_url, self._key)
        try:
            r = await self._http.get("/api/streams")
            current = r.json() if r.status_code == 200 else {}
        except (httpx.HTTPError, ValueError) as e:
            log.warning("go2rtc sync skipped: %s", e)
            return
        for name, src in desired.items():
            if _current_src(current.get(name)) == src:
                continue
            try:
                await self._http.put("/api/streams", params={"name": name, "src": src})
            except httpx.HTTPError as e:
                log.warning("go2rtc: cannot register %s: %s", name, e)
        for name in current:
            if _managed(name) and name not in desired:
                try:
                    await self._http.delete("/api/streams", params={"src": name})
                except httpx.HTTPError as e:
                    log.warning("go2rtc: cannot delete %s: %s", name, e)
