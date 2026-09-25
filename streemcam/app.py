import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path

import httpx
import uvicorn

from .access import Access
from .catalog import Catalog
from .config import Config
from .db import Store
from .go2rtc_sync import Go2rtcSync
from .monitor import CameraMonitor
from .streams import StreamRegistry
from .web.server import create_app

log = logging.getLogger(__name__)


def parse_listen(value: str) -> tuple[str, int]:
    host, _, port = value.rpartition(":")
    return (host or "0.0.0.0"), int(port)


async def sync_forever(sync, interval_seconds: float) -> None:
    while True:
        await sync.sync()
        await asyncio.sleep(interval_seconds)


async def cleanup_forever(archive, cfg: Config, interval_seconds: float, now=None) -> None:
    from .recording.schedule import local_now
    now = now or (lambda: datetime.now(timezone.utc))
    while True:
        today = local_now(now(), cfg.recording).date()
        await asyncio.to_thread(archive.cleanup, today, cfg.recording.retention_days,
                                cfg.recording.min_free_gb)
        await asyncio.sleep(interval_seconds)


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


def alert_text(catalog: Catalog, cfg: Config, cam_id: str, online: bool) -> str:
    cam = catalog.get(cam_id)
    name = cam.name if cam else cam_id
    if online:
        return f"✅ Камера «{name}» снова онлайн."
    return f"⚠️ Камера «{name}» офлайн больше {cfg.offline_alert_minutes} мин."


def recording_config_warnings(cfg: Config) -> list[str]:
    """Спека §Global Constraints: внутри Docker relative path и 127.0.0.1/localhost неверны —
    файлы не попадут в volume /recordings, а ffmpeg не достучится до go2rtc."""
    warnings = []
    if not Path(cfg.recording.path).is_absolute():
        warnings.append(
            f"recording.path {cfg.recording.path!r} is not absolute — "
            "inside Docker recordings will not land in the mounted volume")
    if "127.0.0.1" in cfg.recording.rtsp_url or "localhost" in cfg.recording.rtsp_url:
        warnings.append(
            f"recording.rtsp_url {cfg.recording.rtsp_url!r} points at localhost — "
            "inside Docker ffmpeg cannot reach go2rtc through it")
    return warnings


def build_access(cfg: Config) -> Access:
    return Access(cfg, Store(cfg.db_path), StreamRegistry(cfg.max_streams_per_user))


def make_tg_bot(bot_token: str):
    """Строит aiogram Bot, но не даёт невалидному токену уронить всё приложение."""
    from aiogram import Bot
    from aiogram.utils.token import TokenValidationError

    try:
        return Bot(bot_token)
    except TokenValidationError:
        log.error("telegram bot disabled: invalid bot token")
        return None


async def run(cfg: Config) -> None:
    access = build_access(cfg)
    catalog = Catalog(cfg)
    http = httpx.AsyncClient(base_url=cfg.go2rtc_url, timeout=20)
    sync = Go2rtcSync(http, catalog, cfg.internal_url, cfg.internal_key)

    tg_bot = None
    if cfg.telegram.bot_token:
        tg_bot = make_tg_bot(cfg.telegram.bot_token)

    async def on_alert(cam_id: str, online: bool) -> None:
        if tg_bot is not None:
            from .tg.bot import notify_admins
            await notify_admins(tg_bot, cfg, alert_text(catalog, cfg, cam_id, online))

    async def on_tuya_alert(text: str) -> None:
        if tg_bot is not None:
            from .tg.bot import notify_admins
            await notify_admins(tg_bot, cfg, text)

    tuya = None
    if cfg.tuya.enabled:
        from .tuya.service import TuyaService
        from .tuya.store import TuyaStore
        tuya = TuyaService(cfg, catalog, TuyaStore(cfg.db_path), sync=sync, on_alert=on_tuya_alert)
        tuya.load()

    archive = recorder = None
    if cfg.recording.enabled:
        from .recording.archive import Archive
        from .recording.recorder import Recorder
        for warning in recording_config_warnings(cfg):
            log.warning(warning)
        archive = Archive(Path(cfg.recording.path), catalog)
        recorder = Recorder(cfg, catalog, on_alert=on_tuya_alert)

    monitor = CameraMonitor(cfg, catalog, http, on_alert)
    app = create_app(cfg, catalog, access, monitor, access.registry, http, archive=archive)
    server = uvicorn.Server(uvicorn.Config(app, host=cfg.listen_host, port=cfg.listen_port,
                                           proxy_headers=True, forwarded_allow_ips="*"))

    background = [asyncio.create_task(supervise("monitor", monitor.run))]
    if tuya is not None:
        from .web.internal import create_internal_app
        host, port = parse_listen(cfg.internal_listen)

        async def serve_internal() -> None:
            server = uvicorn.Server(uvicorn.Config(create_internal_app(catalog, tuya, cfg.internal_key),
                                                    host=host, port=port, log_level="warning"))
            await server.serve()

        background.append(asyncio.create_task(supervise("internal-api", serve_internal)))
        background.append(asyncio.create_task(supervise("tuya", tuya.run)))
    else:
        background.append(asyncio.create_task(supervise("go2rtc-sync", lambda: sync_forever(sync, 600))))
    if tg_bot is not None:
        from .tg.bot import run_telegram
        background.append(asyncio.create_task(
            supervise("telegram", lambda: run_telegram(tg_bot, cfg, access, tuya))))
    else:
        log.info("telegram bot disabled (no token)")
    if cfg.discord.bot_token:
        from .dc.bot import run_discord
        background.append(asyncio.create_task(supervise("discord", lambda: run_discord(cfg, catalog, access))))
    else:
        log.info("discord bot disabled (no token)")
    if recorder is not None:
        background.append(asyncio.create_task(supervise("recorder", recorder.run)))
        background.append(asyncio.create_task(
            supervise("archive-cleanup", lambda: cleanup_forever(archive, cfg, 3600))))

    try:
        await server.serve()
    finally:
        for t in background:
            t.cancel()
        await asyncio.gather(*background, return_exceptions=True)
        await http.aclose()
        if tg_bot is not None:
            await tg_bot.session.close()
