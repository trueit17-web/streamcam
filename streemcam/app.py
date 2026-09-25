import asyncio
import logging
from collections.abc import Awaitable, Callable

import httpx
import uvicorn

from .access import Access
from .config import Config
from .db import Store
from .monitor import CameraMonitor
from .streams import StreamRegistry
from .web.server import create_app

log = logging.getLogger(__name__)


async def supervise(name: str, factory: Callable[[], Awaitable[None]],
                    base_delay: float = 5.0, max_delay: float = 300.0) -> None:
    delay = base_delay
    while True:
        try:
            await factory()
            log.warning("%s stopped, restarting", name)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("%s crashed, restarting in %.0fs", name, delay)
        await asyncio.sleep(delay)
        delay = min(max(delay * 2, base_delay), max_delay)


def alert_text(cfg: Config, cam_id: str, online: bool) -> str:
    cam = cfg.camera(cam_id)
    name = cam.name if cam else cam_id
    if online:
        return f"✅ Камера «{name}» снова онлайн."
    return f"⚠️ Камера «{name}» офлайн больше {cfg.offline_alert_minutes} мин."


def build_access(cfg: Config) -> Access:
    return Access(cfg, Store(cfg.db_path), StreamRegistry(cfg.max_streams_per_user))


async def run(cfg: Config) -> None:
    access = build_access(cfg)
    http = httpx.AsyncClient(base_url=cfg.go2rtc_url, timeout=20)

    tg_bot = None
    if cfg.telegram.bot_token:
        from aiogram import Bot
        tg_bot = Bot(cfg.telegram.bot_token)

    async def on_alert(cam_id: str, online: bool) -> None:
        if tg_bot is not None:
            from .tg.bot import notify_admins
            await notify_admins(tg_bot, cfg, alert_text(cfg, cam_id, online))

    monitor = CameraMonitor(cfg, http, on_alert)
    app = create_app(cfg, access, monitor, access.registry, http)
    server = uvicorn.Server(uvicorn.Config(app, host=cfg.listen_host, port=cfg.listen_port,
                                           proxy_headers=True, forwarded_allow_ips="*"))

    background = [asyncio.create_task(supervise("monitor", monitor.run))]
    if tg_bot is not None:
        from .tg.bot import run_telegram
        background.append(asyncio.create_task(
            supervise("telegram", lambda: run_telegram(tg_bot, cfg, access))))
    else:
        log.info("telegram bot disabled (no token)")
    if cfg.discord.bot_token:
        from .dc.bot import run_discord
        background.append(asyncio.create_task(supervise("discord", lambda: run_discord(cfg, access))))
    else:
        log.info("discord bot disabled (no token)")

    try:
        await server.serve()
    finally:
        for t in background:
            t.cancel()
        await asyncio.gather(*background, return_exceptions=True)
        await http.aclose()
        if tg_bot is not None:
            await tg_bot.session.close()
