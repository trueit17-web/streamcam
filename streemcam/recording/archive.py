import logging
import re
import shutil
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from ..catalog import Catalog

log = logging.getLogger(__name__)

DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NAME_RE = re.compile(r"^\d{2}-\d{2}-\d{2}$")
CAM_RE = re.compile(r"^[A-Za-z0-9_-]+$")
RECORDING_WINDOW_SECONDS = 60


@dataclass(frozen=True)
class HourEntry:
    name: str
    hour: int
    size: int
    recording: bool


class Archive:
    def __init__(self, root: Path, catalog: Catalog, clock=time.time):
        self.root = Path(root)
        self.catalog = catalog
        self._clock = clock
        self._on_delete = None  # хук для тестов: вызывается с путём после удаления файла

    def _cam_dir(self, cam_id: str) -> Path | None:
        if not CAM_RE.match(cam_id) or self.catalog.get(cam_id) is None:
            return None
        return self.root / cam_id

    def days(self, cam_id: str) -> list[str] | None:
        cam_dir = self._cam_dir(cam_id)
        if cam_dir is None:
            return None
        if not cam_dir.is_dir():
            return []
        return sorted((d.name for d in cam_dir.iterdir() if d.is_dir() and DAY_RE.match(d.name)),
                      reverse=True)

    def hours(self, cam_id: str, day: str) -> list[HourEntry] | None:
        cam_dir = self._cam_dir(cam_id)
        if cam_dir is None or not DAY_RE.match(day):
            return None
        day_dir = cam_dir / day
        if not day_dir.is_dir():
            return []
        now = self._clock()
        entries = []
        for f in sorted(day_dir.glob("*.mp4")):
            if not NAME_RE.match(f.stem):
                continue
            st = f.stat()
            entries.append(HourEntry(f.stem, int(f.stem[:2]), st.st_size,
                                     now - st.st_mtime < RECORDING_WINDOW_SECONDS))
        return entries

    def file(self, cam_id: str, day: str, name: str) -> Path | None:
        cam_dir = self._cam_dir(cam_id)
        if cam_dir is None or not DAY_RE.match(day) or not NAME_RE.match(name):
            return None
        path = (cam_dir / day / f"{name}.mp4").resolve()
        root = self.root.resolve()
        if root not in path.parents or not path.is_file():
            return None
        return path

    def _all_files(self) -> list[Path]:
        if not self.root.is_dir():
            return []
        files = []
        for cam_dir in self.root.iterdir():
            if not cam_dir.is_dir():
                continue
            for day_dir in cam_dir.iterdir():
                if day_dir.is_dir() and DAY_RE.match(day_dir.name):
                    files.extend(f for f in day_dir.glob("*.mp4") if NAME_RE.match(f.stem))
        return files

    def _delete(self, f: Path) -> None:
        try:
            f.unlink()
        except OSError as e:
            log.warning("cannot delete %s: %s", f, e)
            return
        if self._on_delete is not None:
            self._on_delete(f)
        try:
            f.parent.rmdir()  # удалится, только если папка дня опустела
        except OSError:
            pass

    def cleanup(self, today: date, retention_days: int, min_free_gb: float, disk_free=None) -> list[Path]:
        disk_free = disk_free or (lambda p: shutil.disk_usage(p).free)
        now = self._clock()
        cutoff = (today - timedelta(days=retention_days)).isoformat()
        deleted: list[Path] = []

        def recording(f: Path) -> bool:
            try:
                return now - f.stat().st_mtime < RECORDING_WINDOW_SECONDS
            except OSError:
                return True

        for f in self._all_files():
            if f.parent.name < cutoff and not recording(f):
                self._delete(f)
                deleted.append(f)

        if self.root.is_dir():
            candidates = sorted((f for f in self._all_files() if not recording(f)),
                                key=lambda f: (f.parent.name, f.stem))
            for f in candidates:
                if disk_free(self.root) >= min_free_gb * 10**9:
                    break
                self._delete(f)
                deleted.append(f)
        if deleted:
            log.info("archive cleanup removed %d files", len(deleted))
        return deleted
