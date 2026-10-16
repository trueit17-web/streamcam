import hmac
import logging

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

from ..catalog import Catalog
from ..imou.models import ImouError
from ..tuya.models import TuyaError

log = logging.getLogger(__name__)


def create_internal_app(catalog: Catalog, tuya, internal_key: str | None, imou=None) -> FastAPI:
    """Внутренний API только для go2rtc (docker-сеть). Выдаёт свежие ссылки на потоки Tuya и Imou."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/tuya/{device_id}")
    async def tuya_stream(device_id: str, key: str = ""):
        if not internal_key or not hmac.compare_digest(key.encode(), internal_key.encode()):
            return PlainTextResponse("forbidden", status_code=403)
        cam = next((c for c in catalog.tuya() if c.device_id == device_id), None)
        if tuya is None or cam is None:
            return PlainTextResponse("unknown device", status_code=404)
        if not cam.online:
            return PlainTextResponse("camera offline", status_code=503)
        if not tuya.logged_in:
            return PlainTextResponse("not logged in to Tuya", status_code=503)
        try:
            url = await tuya.stream_url(device_id)
        except TuyaError as e:
            log.warning("tuya stream url for %s failed: %s", device_id, e)
            return PlainTextResponse("tuya cloud error", status_code=502)
        return PlainTextResponse(url)

    @app.get("/imou/{device_id}")
    async def imou_stream(device_id: str, key: str = ""):
        if not internal_key or not hmac.compare_digest(key.encode(), internal_key.encode()):
            return PlainTextResponse("forbidden", status_code=403)
        cam = next((c for c in catalog.imou() if c.device_id == device_id), None)
        if imou is None or cam is None:
            return PlainTextResponse("unknown device", status_code=404)
        if not cam.online:  # офлайн по облаку: не тратим запрос и не будим камеру
            return PlainTextResponse("camera offline", status_code=503)
        try:
            url = await imou.stream_url(device_id)
        except ImouError as e:
            log.warning("imou stream url for %s failed: %s", device_id, e)
            return PlainTextResponse("imou cloud error", status_code=502)
        return PlainTextResponse(f"ffmpeg:{url}#video=copy#audio=aac")

    return app
