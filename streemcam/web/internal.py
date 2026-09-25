import hmac
import logging

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

from ..catalog import Catalog
from ..tuya.models import TuyaError

log = logging.getLogger(__name__)


def create_internal_app(catalog: Catalog, tuya, internal_key: str | None) -> FastAPI:
    """Внутренний API только для go2rtc (docker-сеть). Выдаёт свежие RTSP-ссылки Tuya."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/tuya/{device_id}")
    async def tuya_stream(device_id: str, key: str = ""):
        if not internal_key or not hmac.compare_digest(key.encode(), internal_key.encode()):
            return PlainTextResponse("forbidden", status_code=403)
        if not any(c.device_id == device_id for c in catalog.tuya()):
            return PlainTextResponse("unknown device", status_code=404)
        if not tuya.logged_in:
            return PlainTextResponse("not logged in to Tuya", status_code=503)
        try:
            url = await tuya.stream_url(device_id)
        except TuyaError as e:
            log.warning("tuya stream url for %s failed: %s", device_id, e)
            return PlainTextResponse("tuya cloud error", status_code=502)
        return PlainTextResponse(url)

    return app
