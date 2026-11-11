"""Склейка кусков одного часа в один файл.

Запись режется на файлы по часам (10-00-00.mp4, 11-00-00.mp4 …). Если камера в течение часа
пропадала, ffmpeg после переподключения начинает новый файл (10-23-14.mp4), и час оказывается
разбит на куски. Когда час закончился, куски склеиваются без перекодирования в один файл
с именем первого куска; время, пока камеры не было, просто пропускается.
"""
import asyncio
import json
import logging
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path

from .archive import DAY_RE, NAME_RE

log = logging.getLogger(__name__)

MERGE_INTERVAL_SECONDS = 300
SETTLE_SECONDS = 120          # кусок не трогаем, пока он менялся недавно
MERGE_TIMEOUT_SECONDS = 600
TMP_SUFFIX = ".merging"


def hour_groups(day_dir: Path) -> dict[int, list[Path]]:
    groups: dict[int, list[Path]] = {}
    for f in sorted(day_dir.glob("*.mp4")):
        if NAME_RE.match(f.stem):
            groups.setdefault(int(f.stem[:2]), []).append(f)
    return groups


def _hour_finished(day: str, hour: int, local_now: datetime) -> bool:
    current_day = local_now.strftime("%Y-%m-%d")
    return day < current_day or (day == current_day and hour < local_now.hour)


class HourMerger:
    def __init__(self, root: Path, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe",
                 clock=time.time):
        self.root = Path(root)
        self._ffmpeg = ffmpeg
        self._ffprobe = ffprobe
        self._clock = clock
        self._skip: set[tuple[str, ...]] = set()   # наборы кусков, которые склеить не вышло

    def _cleanup_tmp(self) -> None:
        for f in self.root.glob(f"*/*/*{TMP_SUFFIX}"):
            try:
                f.unlink()
            except OSError:
                pass

    def pending(self, local_now: datetime) -> list[list[Path]]:
        """Законченные часы, разбитые на несколько кусков."""
        if not self.root.is_dir():
            return []
        now = self._clock()
        result = []
        for cam_dir in sorted(self.root.iterdir()):
            if not cam_dir.is_dir():
                continue
            for day_dir in sorted(cam_dir.iterdir()):
                if not (day_dir.is_dir() and DAY_RE.match(day_dir.name)):
                    continue
                for hour, pieces in hour_groups(day_dir).items():
                    if len(pieces) < 2 or not _hour_finished(day_dir.name, hour, local_now):
                        continue
                    if tuple(str(p) for p in pieces) in self._skip:
                        continue
                    try:
                        settled = all(now - p.stat().st_mtime >= SETTLE_SECONDS for p in pieces)
                    except OSError:
                        continue
                    if settled:
                        result.append(pieces)
        return result

    async def _run(self, *args: str) -> tuple[int, bytes]:
        proc = await asyncio.create_subprocess_exec(
            *args, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), MERGE_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return -1, b""
        if proc.returncode != 0:
            log.debug("%s failed: %s", args[0], err.decode(errors="replace")[-500:])
        return proc.returncode, out

    async def _layout(self, path: Path) -> tuple | None:
        """Что внутри файла: склеивать без перекодирования можно только одинаковые куски."""
        code, out = await self._run(self._ffprobe, "-v", "error", "-show_entries",
                                    "stream=codec_type,codec_name,width,height,sample_rate,channels",
                                    "-of", "json", str(path))
        if code != 0:
            return None
        try:
            streams = json.loads(out).get("streams", [])
        except ValueError:
            return None
        if not any(s.get("codec_type") == "video" for s in streams):
            return None
        return tuple(sorted(tuple(sorted((k, str(v)) for k, v in s.items())) for s in streams))

    async def merge(self, pieces: list[Path]) -> bool:
        key = tuple(str(p) for p in pieces)
        layouts = [await self._layout(p) for p in pieces]
        usable = [(p, lay) for p, lay in zip(pieces, layouts) if lay is not None]
        if any(lay != usable[0][1] for _, lay in usable):
            log.warning("cannot merge %s: pieces differ", key)
            self._skip.add(key)
            return False
        # кусок без читаемого видео (камера отвалилась сразу после подключения) смотреть нечего
        empty = [p for p, lay in zip(pieces, layouts) if lay is None]
        if len(usable) < 2:
            if not usable:
                self._skip.add(key)
                return False
            for p in empty:
                p.unlink(missing_ok=True)
            log.info("removed %d empty pieces next to %s", len(empty), usable[0][0])
            return True
        pieces = [p for p, _ in usable]
        target = pieces[0]
        tmp = target.with_name(target.stem + TMP_SUFFIX)
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as lst:
            for p, _ in usable:
                lst.write("file '" + str(p.resolve()).replace("'", "'\\''") + "'\n")
            list_path = lst.name
        try:
            code, _ = await self._run(self._ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                                      "-f", "concat", "-safe", "0", "-i", list_path,
                                      "-map", "0", "-c", "copy", "-movflags", "+faststart",
                                      "-f", "mp4", str(tmp))
        finally:
            os.unlink(list_path)
        if code != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
            log.warning("merging %s failed (ffmpeg exit %s)", key, code)
            tmp.unlink(missing_ok=True)
            self._skip.add(key)
            return False
        last_mtime = max(p.stat().st_mtime for p in pieces)
        os.utime(tmp, (last_mtime, last_mtime))
        os.replace(tmp, target)
        for p in pieces[1:] + empty:
            p.unlink(missing_ok=True)
        log.info("merged %d pieces into %s", len(pieces), target)
        return True

    async def merge_pending(self, local_now: datetime) -> int:
        merged = 0
        for pieces in self.pending(local_now):
            if await self.merge(pieces):
                merged += 1
        return merged

    async def run(self, local_now_fn, interval: float = MERGE_INTERVAL_SECONDS) -> None:
        self._cleanup_tmp()
        while True:
            try:
                await self.merge_pending(local_now_fn())
            except Exception:
                log.exception("hour merge failed")
            await asyncio.sleep(interval)
