import asyncio
import contextlib
import logging
from pathlib import Path
from urllib.parse import urlencode

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.websockets import WebSocketDisconnect
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import WebSocketException as UpstreamError

from ..access import Access, AccessDenied
from ..catalog import Catalog
from ..config import Config
from ..identity import Identity
from ..streams import StreamLimitError, StreamRegistry
from ..tg_auth import TgAuthError
from ..tokens import TokenError

log = logging.getLogger(__name__)

GO2RTC_JS = {"video-rtc.js", "video-stream.js"}
STATIC_DIR = Path(__file__).parent / "static"


def default_upstream(go2rtc_url: str):
    ws_base = "ws" + go2rtc_url[4:] if go2rtc_url.startswith("http") else go2rtc_url

    def connect(src: str):
        return ws_connect(f"{ws_base}/api/ws?{urlencode({'src': src})}", max_size=None)
    return connect


async def _pump(ws: WebSocket, upstream, kicked: asyncio.Event) -> bool:
    """Relay messages between the client and go2rtc until either side ends.

    Returns True iff go2rtc caused the end (an error, or its stream closing) —
    the caller then closes the client socket with 1011. A kick always takes
    precedence over that (checked by the caller via `kicked.is_set()`), and
    client-side endings (disconnect, or an error sending to the client) are
    never reported as a go2rtc error.
    """
    async def client_to_upstream():
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                return
            if msg.get("text") is not None:
                await upstream.send(msg["text"])
            elif msg.get("bytes") is not None:
                await upstream.send(msg["bytes"])

    async def upstream_to_client():
        async for msg in upstream:
            if isinstance(msg, bytes):
                await ws.send_bytes(msg)
            else:
                await ws.send_text(msg)
        # go2rtc ended the stream without an error — still a go2rtc-caused end.
        raise UpstreamError("go2rtc closed the stream")

    client_task = asyncio.create_task(client_to_upstream())
    upstream_task = asyncio.create_task(upstream_to_client())
    kicked_task = asyncio.create_task(kicked.wait())
    tasks = [client_task, upstream_task, kicked_task]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        if kicked.is_set():
            return False
        if upstream_task.done() and not upstream_task.cancelled() and upstream_task.exception() is not None:
            log.debug("upstream stream ended: %r", upstream_task.exception())
            return True
        if client_task.done() and not client_task.cancelled() and client_task.exception() is not None:
            log.debug("client stream ended: %r", client_task.exception())
        return False
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


class TgSessionIn(BaseModel):
    init_data: str


class LinkIn(BaseModel):
    t: str


def _denied(ident: Identity) -> JSONResponse:
    return JSONResponse({"error": "not_allowed", "user_id": ident.user_id}, status_code=403)


def create_app(cfg: Config, catalog: Catalog, access: Access, monitor, registry: StreamRegistry,
               http: httpx.AsyncClient, upstream_connect=None) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    upstream_connect = upstream_connect or default_upstream(cfg.go2rtc_url)

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
            "cameras": [{"id": c.id, "name": c.name, "kind": c.kind, "online": monitor.is_online(c.id)}
                        for c in catalog.all()],
        }

    @app.get("/api/snapshot/{cam_id}")
    async def snapshot(cam_id: str, user: Identity = Depends(current_user)):
        data = monitor.snapshot(cam_id) if catalog.get(cam_id) else None
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

    @app.websocket("/api/ws")
    async def ws_proxy(ws: WebSocket, src: str = "", s: str = ""):
        try:
            ident = access.check_session(s)
        except TokenError:
            await ws.close(code=4401)
            return
        except AccessDenied:
            await ws.close(code=4403)
            return
        if catalog.get(src) is None:
            await ws.close(code=4404)
            return

        kicked = asyncio.Event()

        async def closer():
            kicked.set()

        try:
            registry.acquire(ident, closer)
        except StreamLimitError:
            await ws.close(code=4429)
            return

        close_code = 1000
        try:
            await ws.accept()
            async with upstream_connect(src) as upstream:
                if await _pump(ws, upstream, kicked):
                    close_code = 1011
        except (WebSocketDisconnect, UpstreamError, OSError) as e:
            log.info("stream %s for %s ended: %r", src, ident, e)
            close_code = 1011
        finally:
            registry.release(ident, closer)
        if kicked.is_set():
            close_code = 4403  # kick always takes precedence
        with contextlib.suppress(Exception):
            await ws.close(code=close_code)

    @app.get("/")
    async def index():
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    return app
