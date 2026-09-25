import logging

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from ..access import Access, AccessDenied
from ..config import Config
from ..identity import Identity
from ..streams import StreamRegistry
from ..tg_auth import TgAuthError
from ..tokens import TokenError

log = logging.getLogger(__name__)

GO2RTC_JS = {"video-rtc.js", "video-stream.js"}


class TgSessionIn(BaseModel):
    init_data: str


class LinkIn(BaseModel):
    t: str


def _denied(ident: Identity) -> JSONResponse:
    return JSONResponse({"error": "not_allowed", "user_id": ident.user_id}, status_code=403)


def create_app(cfg: Config, access: Access, monitor, registry: StreamRegistry,
               http: httpx.AsyncClient, upstream_connect=None) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def current_user(request: Request, s: str | None = None) -> Identity:
        token = s
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
        if not token:
            raise HTTPException(401, "no session")
        try:
            return access.check_session(token)
        except TokenError:
            raise HTTPException(401, "invalid session") from None
        except AccessDenied:
            raise HTTPException(403, "not allowed") from None

    @app.post("/api/tg/session")
    async def tg_session(body: TgSessionIn):
        try:
            ident = access.login_telegram(body.init_data)
        except TgAuthError:
            return JSONResponse({"error": "invalid_init_data"}, status_code=401)
        except AccessDenied as e:
            return _denied(e.identity)
        return {"token": access.issue_session(ident)}

    @app.post("/api/link/redeem")
    async def link_redeem(body: LinkIn):
        try:
            ident = access.redeem_link_token(body.t)
        except TokenError:
            return JSONResponse({"error": "invalid_link"}, status_code=401)
        except AccessDenied as e:
            return _denied(e.identity)
        return {"token": access.issue_session(ident)}

    @app.get("/api/cameras")
    async def cameras(user: Identity = Depends(current_user)):
        return {
            "player_mode": cfg.player_mode,
            "max_streams": cfg.max_streams_per_user,
            "cameras": [{"id": c.id, "name": c.name, "online": monitor.is_online(c.id)}
                        for c in cfg.cameras],
        }

    @app.get("/api/snapshot/{cam_id}")
    async def snapshot(cam_id: str, user: Identity = Depends(current_user)):
        data = monitor.snapshot(cam_id) if cfg.camera(cam_id) else None
        if data is None:
            raise HTTPException(404, "no snapshot")
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/go2rtc/{name}")
    async def go2rtc_js(name: str):
        if name not in GO2RTC_JS:
            raise HTTPException(404)
        r = await http.get(f"/{name}")
        if r.status_code != 200:
            raise HTTPException(502, "go2rtc unavailable")
        return Response(r.content, media_type="application/javascript",
                        headers={"Cache-Control": "public, max-age=3600"})

    return app
