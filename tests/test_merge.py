import json
import os
import shutil
import subprocess
import time
from datetime import datetime

import pytest

from streemcam.recording.merge import HourMerger, hour_groups

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                                reason="ffmpeg not installed")

NOW = datetime(2026, 11, 11, 12, 30)


def make_piece(path, seconds=2, size="320x240", codec="libx264"):
    path.parent.mkdir(parents=True, exist_ok=True)
    # как пишет recorder: фрагментированный mp4
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", f"testsrc=size={size}:rate=10:duration={seconds}",
                    "-c:v", codec, "-movflags", "frag_keyframe+empty_moov+default_base_moof",
                    "-f", "mp4", str(path)], check=True)
    old = time.time() - 600
    os.utime(path, (old, old))
    return path


def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json",
                          str(path)], capture_output=True, check=True).stdout
    return float(json.loads(out)["format"]["duration"])


async def test_split_hour_is_merged_into_first_piece(tmp_path):
    day = tmp_path / "cam1" / "2026-11-11"
    make_piece(day / "10-00-00.mp4", 2)
    make_piece(day / "10-23-14.mp4", 3)
    make_piece(day / "10-47-01.mp4", 1)
    make_piece(day / "11-00-00.mp4", 2)
    merged = await HourMerger(tmp_path).merge_pending(NOW)
    assert merged == 1
    assert sorted(f.name for f in day.glob("*.mp4")) == ["10-00-00.mp4", "11-00-00.mp4"]
    assert duration(day / "10-00-00.mp4") == pytest.approx(6, abs=0.5)
    assert not list(day.glob("*.merging"))
    # склеенный файл не выглядит «пишущимся»: время изменения как у последнего куска
    assert time.time() - (day / "10-00-00.mp4").stat().st_mtime > 300


async def test_current_hour_is_not_touched(tmp_path):
    day = tmp_path / "cam1" / "2026-11-11"
    make_piece(day / "12-00-00.mp4")
    make_piece(day / "12-10-00.mp4")
    assert await HourMerger(tmp_path).merge_pending(NOW) == 0
    assert len(list(day.glob("*.mp4"))) == 2


async def test_recently_written_piece_waits(tmp_path):
    day = tmp_path / "cam1" / "2026-11-11"
    make_piece(day / "10-00-00.mp4")
    fresh = make_piece(day / "10-30-00.mp4")
    os.utime(fresh, None)
    assert await HourMerger(tmp_path).merge_pending(NOW) == 0


async def test_previous_day_is_merged(tmp_path):
    day = tmp_path / "cam1" / "2026-11-10"
    make_piece(day / "18-00-00.mp4")
    make_piece(day / "18-40-00.mp4")
    assert await HourMerger(tmp_path).merge_pending(NOW) == 1
    assert [f.name for f in day.glob("*.mp4")] == ["18-00-00.mp4"]


async def test_different_pieces_are_left_alone(tmp_path):
    day = tmp_path / "cam1" / "2026-11-11"
    make_piece(day / "10-00-00.mp4", size="320x240")
    make_piece(day / "10-30-00.mp4", size="640x480")
    merger = HourMerger(tmp_path)
    assert await merger.merge_pending(NOW) == 0
    assert len(list(day.glob("*.mp4"))) == 2
    assert merger.pending(NOW) == []   # не пытаемся снова каждые 5 минут


def test_hour_groups_ignores_foreign_files(tmp_path):
    (tmp_path / "10-00-00.mp4").write_bytes(b"x")
    (tmp_path / "10-00-00.merging").write_bytes(b"x")
    (tmp_path / "notes.mp4").write_bytes(b"x")
    assert list(hour_groups(tmp_path)) == [10]
    assert len(hour_groups(tmp_path)[10]) == 1
