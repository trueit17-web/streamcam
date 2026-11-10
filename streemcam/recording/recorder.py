import asyncio
import io
import logging
import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from ..catalog import Catalog
from ..config import Config
from ..go2rtc_config import P2P_HOST, TUYA_AUDIO_FILTER, rec_stream_name, tuya_http_input
from .schedule import is_recording_time, local_now

log = logging.getLogger(__name__)

TICK_SECONDS = 5
HEALTHY_AFTER_SECONDS = 60
STALL_SECONDS = 60
FRESH_SECONDS = 30
STOP_WAIT_SECONDS = 10.0
# Один проход по камерам занимает доли секунды. Если он висит дольше, запись встала бы для всех
# камер сразу (так было 09.11: ни одного сообщения записи 15 минут), поэтому проход прерывается,
# а в лог пишется, где он застрял.
TICK_TIMEOUT_SECONDS = 90.0
ALERT_TIMEOUT_SECONDS = 5.0   # уведомление в Telegram не должно держать запись


def backoff(failures: int) -> float:
    return float(min(60, 5 * 2 ** (failures - 1)))


def ffmpeg_args(input_url: str, out_dir: Path, ffmpeg: str = "ffmpeg",
                 audio_filter: str | None = None, copy_video: bool = False,
                 video_tag: str | None = None) -> list[str]:
    if urlsplit(input_url).scheme in ("http", "https"):
        input_opts = ["-rw_timeout", "10000000"]
    else:
        input_opts = ["-rtsp_transport", "tcp", "-timeout", "10000000"]
    args = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",  # без -nostdin: stdin нужен для мягкой остановки "q"
        *input_opts, "-i", input_url,
        "-map", "0:v:0", "-map", "0:a:0?",
    ]
    if copy_video:
        args += ["-c:v", "copy"]
        if video_tag:
            args += ["-tag:v", video_tag]
    else:
        args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-g", "50"]
    if audio_filter:
        args += ["-af", audio_filter]
    args += [
        "-c:a", "aac", "-b:a", "64k",
        "-f", "segment", "-segment_time", "3600", "-segment_atclocktime", "1",
        "-reset_timestamps", "1", "-strftime", "1", "-segment_format", "mp4",
        "-segment_format_options", "movflags=+frag_keyframe+empty_moov+default_base_moof",
        str(out_dir / "%Y-%m-%d" / "%H-%M-%S.mp4"),
    ]
    return args


def keepalive_args(input_url: str, ffmpeg: str = "ffmpeg") -> list[str]:
    """Читает поток и ничего не сохраняет: go2rtc держит подключение к камере открытым."""
    return [ffmpeg, "-hide_banner", "-loglevel", "error", "-rtsp_transport", "tcp", "-timeout", "10000000",
            "-i", input_url, "-map", "0:v:0", "-c", "copy", "-f", "null", "-"]


@dataclass(frozen=True)
class RecTarget:
    cam_id: str
    name: str
    input_url: str
    audio_filter: str | None = None
    # Imou пишем без перекодирования: 2K HEVC с десятка камер перекодировать дорого
    copy_video: bool = False
    p2p: bool = False
    keepalive: bool = False  # вне окна записи: только держать подключение


def targets(cfg: Config, catalog: Catalog) -> list[RecTarget]:
    result = []
    for cam in catalog.all():
        if cam.kind in ("tuya", "imou") and cam.online is False:
            continue
        stream = rec_stream_name(cfg, cam)
        if stream is not None:
            if cam.kind == "tuya":
                input_url = tuya_http_input(cam.id, cfg.go2rtc_url.rstrip("/"))
                audio_filter = TUYA_AUDIO_FILTER
            else:
                input_url = f"{cfg.recording.rtsp_url}/{stream}"
                audio_filter = None
            src = cfg.camera(cam.id)
            p2p = getattr(src, "host", None) == P2P_HOST
            result.append(RecTarget(cam.id, cam.name, input_url, audio_filter,
                                    copy_video=cam.kind == "imou", p2p=p2p))
    return result


def keepalive_targets(cfg: Config, catalog: Catalog) -> list[RecTarget]:
    if not cfg.recording.p2p_keepalive:
        return []
    return [RecTarget(t.cam_id, t.name, t.input_url, keepalive=True)
            for t in targets(cfg, catalog) if t.p2p]


@dataclass
class _State:
    target: RecTarget
    proc: object = None
    started_at: float = 0.0
    failures: int = 0
    next_start: float = 0.0
    down_since: float | None = None
    alerted: bool = False
    # кодек копируемого видео заранее неизвестен: HEVC пишем с меткой hvc1 (иначе Safari не играет),
    # а H.264 с ней ffmpeg не примет — после неудачного запуска пробуем без метки, и наоборот
    hvc1: bool = True
    codec_known: bool = False


class Recorder:
    def __init__(self, cfg: Config, catalog: Catalog, spawn=None, now=None,
                 clock=time.monotonic, on_alert=None, ffmpeg: str = "ffmpeg",
                 wall_clock=time.time):
        self.cfg = cfg
        self.catalog = catalog
        self._spawn = spawn or asyncio.create_subprocess_exec
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._clock = clock
        self._wall_clock = wall_clock
        self._on_alert = on_alert
        self._ffmpeg = ffmpeg
        self._root = Path(cfg.recording.path)
        self._states: dict[str, _State] = {}

    def available(self) -> bool:
        return shutil.which(self._ffmpeg) is not None

    def active_cams(self) -> set[str]:
        return {cid for cid, st in self._states.items()
                if st.proc is not None and st.proc.returncode is None and not st.target.keepalive}

    async def run(self) -> None:
        if not self.available():
            log.error("recording disabled: %s not found in PATH", self._ffmpeg)
            await asyncio.Event().wait()
        try:
            while True:
                await self._guarded_tick()
                await asyncio.sleep(TICK_SECONDS)
        finally:
            await self.stop_all()

    async def _guarded_tick(self, timeout: float = TICK_TIMEOUT_SECONDS) -> None:
        task = asyncio.ensure_future(self.tick())
        try:
            done, _ = await asyncio.wait({task}, timeout=timeout)
        except asyncio.CancelledError:
            task.cancel()
            raise
        if not done:
            buf = io.StringIO()
            task.print_stack(file=buf)
            log.error("recorder tick hung for %ss, cancelling it:\n%s", timeout, buf.getvalue())
            task.cancel()
            try:
                await task
            except BaseException:
                pass
            return
        if not task.cancelled() and task.exception() is not None:
            log.error("recorder tick failed", exc_info=task.exception())

    async def tick(self) -> None:
        mono = self._clock()
        wall = self._now()
        active = targets(self.cfg, self.catalog) if is_recording_time(wall, self.cfg.recording) \
            else keepalive_targets(self.cfg, self.catalog)
        wanted = {t.cam_id: t for t in active}

        # смена окна (запись <-> поддержание связи) меняет цель: старый процесс останавливаем
        to_remove = [cam_id for cam_id, st in self._states.items()
                     if cam_id not in wanted or wanted[cam_id].keepalive != st.target.keepalive]
        if to_remove:
            await asyncio.gather(*(self._stop(self._states[cam_id]) for cam_id in to_remove))
            for cam_id in to_remove:
                del self._states[cam_id]

        for cam_id, st in list(self._states.items()):
            if st.proc is not None and st.proc.returncode is not None:
                log.warning("recorder for %s exited with %s", cam_id, st.proc.returncode)
                st.proc = None
                st.failures += 1
                st.next_start = mono + backoff(st.failures)
                if st.down_since is None:
                    st.down_since = mono
                if st.target.copy_video and not st.codec_known:
                    st.hvc1 = not st.hvc1

        for cam_id, target in wanted.items():
            st = self._states.get(cam_id)
            if st is None:
                st = self._states[cam_id] = _State(target, down_since=mono)
            try:
                if st.proc is None and mono >= st.next_start:
                    await self._start(st, wall, mono)
                await self._check_health(st, mono, wall)
            except Exception:
                log.exception("error handling camera %s", cam_id)
                st.failures += 1
                st.next_start = mono + backoff(st.failures)
                if st.down_since is None:
                    st.down_since = mono

    async def _start(self, st: _State, wall: datetime, mono: float) -> None:
        if st.target.keepalive:
            st.proc = await self._spawn(*keepalive_args(st.target.input_url, self._ffmpeg),
                                        stdin=asyncio.subprocess.PIPE,
                                        stdout=asyncio.subprocess.DEVNULL)
            st.started_at = mono
            return
        out_dir = self._root / st.target.cam_id
        local = local_now(wall, self.cfg.recording)
        (out_dir / local.strftime("%Y-%m-%d")).mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "TZ": self.cfg.recording.timezone}
        tag = "hvc1" if st.target.copy_video and st.hvc1 else None
        st.proc = await self._spawn(*ffmpeg_args(st.target.input_url, out_dir, self._ffmpeg,
                                                  st.target.audio_filter, st.target.copy_video, tag),
                                    stdin=asyncio.subprocess.PIPE,
                                    stdout=asyncio.subprocess.DEVNULL, env=env)
        st.started_at = mono

    def _newest_mtime(self, st: _State, wall: datetime) -> float | None:
        local = local_now(wall, self.cfg.recording)
        today_dir = self._root / st.target.cam_id / local.strftime("%Y-%m-%d")
        newest_mtime = None
        if today_dir.is_dir():
            for f in today_dir.glob("*.mp4"):
                try:
                    mt = f.stat().st_mtime
                except OSError:
                    continue
                if newest_mtime is None or mt > newest_mtime:
                    newest_mtime = mt
        return newest_mtime

    def _is_stalled(self, st: _State, mono: float, wall: datetime) -> bool:
        if st.proc is None or st.proc.returncode is not None:
            return False
        if mono - st.started_at < STALL_SECONDS:
            return False
        newest_mtime = self._newest_mtime(st, wall)
        if newest_mtime is None:
            return True
        return (self._wall_clock() - newest_mtime) >= STALL_SECONDS

    async def _check_health(self, st: _State, mono: float, wall: datetime) -> None:
        if st.target.keepalive:
            # файлов нет: следим только за перезапуском упавшего процесса, без уведомлений
            if st.proc is not None and st.proc.returncode is None and mono - st.started_at >= HEALTHY_AFTER_SECONDS:
                st.failures = 0
            return
        if self._is_stalled(st, mono, wall):
            log.warning("recorder for %s stalled (no fresh output for %ss), restarting",
                        st.target.cam_id, STALL_SECONDS)
            st.proc.terminate()
            return
        running = st.proc is not None and st.proc.returncode is None
        healthy = False
        if running and mono - st.started_at >= HEALTHY_AFTER_SECONDS:
            newest_mtime = self._newest_mtime(st, wall)
            healthy = newest_mtime is not None and (self._wall_clock() - newest_mtime) <= FRESH_SECONDS
        if healthy:
            st.codec_known = True
            st.failures = 0
            st.down_since = None
            if st.alerted:
                st.alerted = False
                await self._alert(f"✅ Запись камеры «{st.target.name}» возобновилась.")
            return
        if (st.down_since is not None and not st.alerted
                and mono - st.down_since >= self.cfg.recording.alert_minutes * 60):
            st.alerted = True
            await self._alert(f"⚠️ Запись камеры «{st.target.name}» не идёт больше "
                              f"{self.cfg.recording.alert_minutes} мин.")

    async def _stop(self, st: _State) -> None:
        p = st.proc
        st.proc = None
        if p is None or p.returncode is not None:
            return
        try:
            p.stdin.write(b"q\n")
            await p.stdin.drain()
        except (OSError, AttributeError):
            pass
        try:
            await asyncio.wait_for(p.wait(), STOP_WAIT_SECONDS)
            return
        except asyncio.TimeoutError:
            p.terminate()
        try:
            await asyncio.wait_for(p.wait(), 5)
        except asyncio.TimeoutError:
            p.kill()
            await p.wait()

    async def stop_all(self) -> None:
        await asyncio.gather(*(self._stop(st) for st in list(self._states.values())))
        self._states.clear()

    async def _alert(self, text: str) -> None:
        if self._on_alert is None:
            return
        try:
            await asyncio.wait_for(self._on_alert(text), ALERT_TIMEOUT_SECONDS)
        except Exception:
            log.exception("recording alert failed")
