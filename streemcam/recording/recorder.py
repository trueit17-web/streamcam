import asyncio
import logging
import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..catalog import Catalog
from ..config import Config
from ..go2rtc_config import rec_stream_name
from .schedule import is_recording_time, local_now

log = logging.getLogger(__name__)

TICK_SECONDS = 5
HEALTHY_AFTER_SECONDS = 60
STALL_SECONDS = 180
STOP_WAIT_SECONDS = 10.0


def backoff(failures: int) -> float:
    return float(min(60, 5 * 2 ** (failures - 1)))


def ffmpeg_args(input_url: str, out_dir: Path, ffmpeg: str = "ffmpeg") -> list[str]:
    return [
        ffmpeg, "-hide_banner", "-loglevel", "warning", "-y",  # без -nostdin: stdin нужен для мягкой остановки "q"
        "-rtsp_transport", "tcp", "-timeout", "10000000", "-i", input_url,
        "-map", "0:v:0", "-map", "0:a:0?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-g", "50",
        "-c:a", "aac", "-b:a", "64k",
        "-f", "segment", "-segment_time", "3600", "-segment_atclocktime", "1",
        "-reset_timestamps", "1", "-strftime", "1", "-segment_format", "mp4",
        "-segment_format_options", "movflags=+frag_keyframe+empty_moov+default_base_moof",
        str(out_dir / "%Y-%m-%d" / "%H-%M-%S.mp4"),
    ]


@dataclass(frozen=True)
class RecTarget:
    cam_id: str
    name: str
    input_url: str


def targets(cfg: Config, catalog: Catalog) -> list[RecTarget]:
    result = []
    for cam in catalog.all():
        stream = rec_stream_name(cfg, cam)
        if stream is not None:
            result.append(RecTarget(cam.id, cam.name, f"{cfg.recording.rtsp_url}/{stream}"))
    return result


@dataclass
class _State:
    target: RecTarget
    proc: object = None
    started_at: float = 0.0
    failures: int = 0
    next_start: float = 0.0
    down_since: float | None = None
    alerted: bool = False


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
        return {cid for cid, st in self._states.items() if st.proc is not None and st.proc.returncode is None}

    async def run(self) -> None:
        if not self.available():
            log.error("recording disabled: %s not found in PATH", self._ffmpeg)
            await asyncio.Event().wait()
        try:
            while True:
                try:
                    await self.tick()
                except Exception:
                    log.exception("recorder tick failed")
                await asyncio.sleep(TICK_SECONDS)
        finally:
            await self.stop_all()

    async def tick(self) -> None:
        mono = self._clock()
        wall = self._now()
        wanted = {t.cam_id: t for t in targets(self.cfg, self.catalog)} \
            if is_recording_time(wall, self.cfg.recording) else {}

        to_remove = [cam_id for cam_id in self._states if cam_id not in wanted]
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
        out_dir = self._root / st.target.cam_id
        local = local_now(wall, self.cfg.recording)
        (out_dir / local.strftime("%Y-%m-%d")).mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "TZ": self.cfg.recording.timezone}
        st.proc = await self._spawn(*ffmpeg_args(st.target.input_url, out_dir, self._ffmpeg),
                                    stdin=asyncio.subprocess.PIPE,
                                    stdout=asyncio.subprocess.DEVNULL, env=env)
        st.started_at = mono

    def _is_stalled(self, st: _State, mono: float, wall: datetime) -> bool:
        if st.proc is None or st.proc.returncode is not None:
            return False
        if mono - st.started_at < STALL_SECONDS:
            return False
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
        if newest_mtime is None:
            return True
        return (self._wall_clock() - newest_mtime) >= STALL_SECONDS

    async def _check_health(self, st: _State, mono: float, wall: datetime) -> None:
        if self._is_stalled(st, mono, wall):
            log.warning("recorder for %s stalled (no fresh output for %ss), restarting",
                        st.target.cam_id, STALL_SECONDS)
            st.proc.terminate()
            return
        running = st.proc is not None and st.proc.returncode is None
        if running and mono - st.started_at >= HEALTHY_AFTER_SECONDS:
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
            await self._on_alert(text)
        except Exception:
            log.exception("recording alert failed")
