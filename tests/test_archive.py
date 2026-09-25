import os
from datetime import date

from streemcam.catalog import Catalog
from streemcam.recording.archive import Archive, HourEntry

NOW = 1_800_000_000.0


def touch(root, cam, day, name, size=100, age=3600):
    d = root / cam / day
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{name}.mp4"
    f.write_bytes(b"x" * size)
    os.utime(f, (NOW - age, NOW - age))
    return f


def make(cfg, root):
    return Archive(root, Catalog(cfg), clock=lambda: NOW)


def test_days_and_hours(cfg, tmp_path):
    touch(tmp_path, "yard", "2026-09-24", "08-00-00")
    touch(tmp_path, "yard", "2026-09-25", "10-25-13", size=50)
    touch(tmp_path, "yard", "2026-09-25", "08-00-00", size=70)
    touch(tmp_path, "yard", "2026-09-25", "11-00-00", size=30, age=10)   # пишется
    (tmp_path / "yard" / "junk").mkdir()
    (tmp_path / "yard" / "2026-09-25" / "notes.txt").write_text("x")
    a = make(cfg, tmp_path)
    assert a.days("yard") == ["2026-09-25", "2026-09-24"]
    assert a.hours("yard", "2026-09-25") == [
        HourEntry("08-00-00", 8, 70, False),
        HourEntry("10-25-13", 10, 50, False),
        HourEntry("11-00-00", 11, 30, True),
    ]
    assert a.days("gate") == []
    assert a.days("nope") is None
    assert a.hours("yard", "bad") is None
    assert a.hours("yard", "2026-01-01") == []


def test_file_lookup_and_traversal(cfg, tmp_path):
    f = touch(tmp_path, "yard", "2026-09-25", "08-00-00")
    a = make(cfg, tmp_path)
    assert a.file("yard", "2026-09-25", "08-00-00") == f.resolve()
    assert a.file("yard", "2026-09-25", "09-00-00") is None
    assert a.file("yard", "..", "08-00-00") is None
    assert a.file("yard", "2026-09-25", "../../x") is None
    assert a.file("../yard", "2026-09-25", "08-00-00") is None
    assert a.file("nope", "2026-09-25", "08-00-00") is None


def test_cleanup_by_retention(cfg, tmp_path):
    old = touch(tmp_path, "yard", "2026-09-10", "08-00-00")
    keep = touch(tmp_path, "yard", "2026-09-12", "08-00-00")
    gone_cam = touch(tmp_path, "removed_cam", "2026-09-01", "08-00-00")
    deleted = make(cfg, tmp_path).cleanup(date(2026, 9, 25), retention_days=14, min_free_gb=0,
                                          disk_free=lambda p: 10**12)
    assert not old.exists() and not gone_cam.exists() and keep.exists()
    assert not (tmp_path / "yard" / "2026-09-10").exists()
    assert set(deleted) == {old, gone_cam}


def test_cleanup_by_free_space_skips_recording(cfg, tmp_path):
    a1 = touch(tmp_path, "yard", "2026-09-24", "08-00-00")
    a2 = touch(tmp_path, "gate", "2026-09-24", "09-00-00")
    live = touch(tmp_path, "yard", "2026-09-25", "08-00-00", age=5)
    free = {"bytes": 14 * 10**9}

    def disk_free(_):
        return free["bytes"]

    arch = make(cfg, tmp_path)
    deleted = []

    def fake_free_after_delete(path):
        deleted.append(path)
        free["bytes"] += 10**9
    arch._on_delete = fake_free_after_delete
    arch.cleanup(date(2026, 9, 25), retention_days=14, min_free_gb=15, disk_free=disk_free)
    assert not a1.exists() and a2.exists() and live.exists()   # удалён самый старый, дальше места хватило
    assert deleted == [a1]
