import asyncio
import os
from datetime import datetime, timezone

import pytest

from streemcam.catalog import Catalog
from streemcam.go2rtc_config import TUYA_AUDIO_FILTER
from streemcam.recording.recorder import Recorder, RecTarget, backoff, ffmpeg_args, targets
from streemcam.tuya.models import TuyaCamera


class FakeStdin:
    def __init__(self, proc):
        self.proc = proc
        self.data = b""

    def write(self, b):
        self.data += b
        if b == b"q\n" and self.proc.graceful:
            self.proc.returncode = 0

    async def drain(self):
        pass


class FakeProc:
    def __init__(self, graceful=True):
        self.returncode = None
        self.graceful = graceful
        self.stdin = FakeStdin(self)
        self.terminated = self.killed = False

    async def wait(self):
        while self.returncode is None:
            await asyncio.sleep(0.001)
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9


class Env:
    def __init__(self, cfg, catalog):
        self.calls = []
        self.procs = []
        self.alerts = []
        self.wall = datetime(2026, 9, 25, 5, 0, tzinfo=timezone.utc)  # 08:00 MSK
        self.mono = 1000.0
        self.wallclock = 2_000_000.0

        async def spawn(*args, **kwargs):
            self.calls.append((args, kwargs))
            p = FakeProc()
            self.procs.append(p)
            return p

        async def on_alert(text):
            self.alerts.append(text)

        self.rec = Recorder(cfg, catalog, spawn=spawn, now=lambda: self.wall,
                            clock=lambda: self.mono, on_alert=on_alert,
                            wall_clock=lambda: self.wallclock)


@pytest.fixture
def rcfg(make_cfg, tmp_path):
    return make_cfg(recording={"enabled": True, "path": str(tmp_path), "rtsp_url": "rtsp://go2rtc:8554"})


def test_backoff():
    assert [backoff(n) for n in (1, 2, 3, 4, 5, 6)] == [5, 10, 20, 40, 60, 60]


def test_ffmpeg_args(tmp_path):
    args = ffmpeg_args("rtsp://go2rtc:8554/room~rec", tmp_path / "room")
    assert args[0] == "ffmpeg"
    assert args[args.index("-i") + 1] == "rtsp://go2rtc:8554/room~rec"
    assert args[args.index("-i") - 2:args.index("-i")] == ["-timeout", "10000000"]
    assert args[args.index("-loglevel") + 1] == "error"
    joined = " ".join(args)
    for part in ["-rtsp_transport tcp", "-timeout 10000000", "-c:v libx264", "-preset veryfast",
                 "-crf 28", "-g 50",
                 "-c:a aac", "-b:a 64k", "-f segment", "-segment_time 3600", "-segment_atclocktime 1",
                 "-reset_timestamps 1", "-strftime 1", "-segment_format mp4",
                 "-segment_format_options movflags=+frag_keyframe+empty_moov+default_base_moof",
                 "-map 0:v:0", "-map 0:a:0?"]:
        assert part in joined
    assert args[-1] == str(tmp_path / "room" / "%Y-%m-%d" / "%H-%M-%S.mp4")
    assert "-af" not in args


def test_ffmpeg_args_with_audio_filter(tmp_path):
    args = ffmpeg_args("rtsp://go2rtc:8554/tuya_bf1", tmp_path / "tuya_bf1", audio_filter=TUYA_AUDIO_FILTER)
    assert args[args.index("-af") + 1] == TUYA_AUDIO_FILTER
    assert args[args.index("-af") + 2] == "-c:a"


def test_targets(rcfg):
    catalog = Catalog(rcfg)
    catalog.set_tuya([TuyaCamera("bf1", "Прихожая", True)])
    ts = {t.cam_id: t for t in targets(rcfg, catalog)}
    assert ts["yard"] == RecTarget("yard", "Двор", "rtsp://go2rtc:8554/yard", None)
    assert ts["yard"].audio_filter is None
    assert ts["gate"].input_url == "rtsp://go2rtc:8554/gate~rec"
    assert ts["gate"].audio_filter is None
    assert ts["room"].input_url == "rtsp://go2rtc:8554/room~rec"
    assert ts["room"].audio_filter is None
    assert ts["tuya_bf1"].input_url == "rtsp://go2rtc:8554/tuya_bf1"
    assert ts["tuya_bf1"].audio_filter == TUYA_AUDIO_FILTER


def test_targets_skips_offline_tuya_cameras(rcfg):
    catalog = Catalog(rcfg)
    catalog.set_tuya([TuyaCamera("bf1", "Прихожая", True), TuyaCamera("bf2", "Кухня", False)])
    ts = {t.cam_id: t for t in targets(rcfg, catalog)}
    assert "tuya_bf1" in ts
    assert "tuya_bf2" not in ts


async def test_starts_in_window_creates_dirs_and_stops_outside(rcfg, tmp_path):
    env = Env(rcfg, Catalog(rcfg))
    await env.rec.tick()
    assert len(env.calls) == 3
    args, kwargs = env.calls[0]
    assert kwargs["env"]["TZ"] == "Europe/Moscow"
    assert kwargs["stdin"] == asyncio.subprocess.PIPE
    assert (tmp_path / "yard" / "2026-09-25").is_dir()
    assert not (tmp_path / "yard" / "2026-09-26").exists()
    assert env.rec.active_cams() == {"yard", "gate", "room"}

    await env.rec.tick()
    assert len(env.calls) == 3  # уже запущены — повторно не стартуем

    env.wall = datetime(2026, 9, 25, 16, 0, tzinfo=timezone.utc)  # 19:00 MSK
    await env.rec.tick()
    assert all(p.stdin.data == b"q\n" and p.returncode == 0 for p in env.procs)
    assert env.rec.active_cams() == set()


async def test_outside_window_nothing_starts(rcfg):
    env = Env(rcfg, Catalog(rcfg))
    env.wall = datetime(2026, 9, 25, 4, 0, tzinfo=timezone.utc)  # 07:00 MSK
    await env.rec.tick()
    assert env.calls == []


async def test_restart_with_backoff(make_cfg, tmp_path):
    cfg = make_cfg(recording={"enabled": True, "path": str(tmp_path)},
                   cameras=[{"id": "yard", "name": "Двор", "type": "rtsp", "url": "rtsp://x"}])
    env = Env(cfg, Catalog(cfg))
    await env.rec.tick()
    env.procs[0].returncode = 1         # упал
    await env.rec.tick()
    assert len(env.calls) == 1          # пауза 5 с ещё не прошла
    env.mono += 5
    await env.rec.tick()
    assert len(env.calls) == 2


async def test_alert_after_minutes_and_recovery(make_cfg, tmp_path):
    cfg = make_cfg(recording={"enabled": True, "path": str(tmp_path), "alert_minutes": 10},
                   cameras=[{"id": "yard", "name": "Двор", "type": "rtsp", "url": "rtsp://x"}])
    env = Env(cfg, Catalog(cfg))
    await env.rec.tick()
    for _ in range(12):                 # падает каждую минуту 12 минут
        env.procs[-1].returncode = 1
        env.mono += 60
        await env.rec.tick()
    assert len(env.alerts) == 1 and "Двор" in env.alerts[0]
    # свежий сегмент в папке дня — иначе процесс "жив, но не пишет" и не считается здоровым
    day_dir = tmp_path / "yard" / "2026-09-25"
    fresh = day_dir / "08-00-00.mp4"
    fresh.write_bytes(b"data")
    os.utime(fresh, (env.wallclock, env.wallclock))
    env.mono += 61                      # процесс живёт > 60 с — здоров
    await env.rec.tick()
    assert len(env.alerts) == 2 and "возобновилась" in env.alerts[1]


async def test_running_without_fresh_file_alerts_once_despite_stall_restarts(make_cfg, tmp_path):
    """Процесс работает (не падает сам), но никогда не пишет файл — watchdog периодически
    его перезапускает (terminate), но т.к. свежего сегмента так и нет, здоровье не
    восстанавливается: down_since не сбрасывается, и уведомление уходит ровно один раз."""
    cfg = make_cfg(recording={"enabled": True, "path": str(tmp_path), "alert_minutes": 10},
                   cameras=[{"id": "yard", "name": "Двор", "type": "rtsp", "url": "rtsp://x"}])
    env = Env(cfg, Catalog(cfg))
    await env.rec.tick()
    for _ in range(20):
        env.mono += 61
        env.wallclock += 61
        await env.rec.tick()
    assert len(env.alerts) == 1
    assert "не идёт" in env.alerts[0]


async def test_stall_watchdog_terminates_on_stale_file(rcfg, tmp_path):
    env = Env(rcfg, Catalog(rcfg))
    await env.rec.tick()
    day_dir = tmp_path / "yard" / "2026-09-25"
    stale_file = day_dir / "08-00-00.mp4"
    stale_file.write_bytes(b"data")
    os.utime(stale_file, (env.wallclock - 200, env.wallclock - 200))  # старше STALL_SECONDS (180)
    env.mono += 181
    await env.rec.tick()
    assert env.procs[0].terminated is True


async def test_stop_escalates_to_terminate(rcfg, monkeypatch):
    import streemcam.recording.recorder as mod
    monkeypatch.setattr(mod, "STOP_WAIT_SECONDS", 0.01)
    env = Env(rcfg, Catalog(rcfg))
    await env.rec.tick()
    for p in env.procs:
        p.graceful = False
    await env.rec.stop_all()
    assert all(p.terminated for p in env.procs)


def test_available(rcfg):
    assert Recorder(rcfg, Catalog(rcfg), ffmpeg="definitely-not-ffmpeg-xyz").available() is False


async def test_stall_watchdog_terminates_when_no_fresh_output(rcfg, tmp_path):
    env = Env(rcfg, Catalog(rcfg))
    await env.rec.tick()
    assert len(env.procs) == 3
    # ни один файл не записан, монотонные часы уходят за STALL_SECONDS (180)
    env.mono += 181
    await env.rec.tick()
    assert env.procs[0].terminated is True


async def test_stall_watchdog_skips_when_fresh_file_exists(rcfg, tmp_path):
    env = Env(rcfg, Catalog(rcfg))
    await env.rec.tick()
    day_dir = tmp_path / "yard" / "2026-09-25"
    fresh_file = day_dir / "08-00-00.mp4"
    fresh_file.write_bytes(b"data")
    os.utime(fresh_file, (env.wallclock, env.wallclock))  # свежий файл "сейчас"
    env.mono += 181                     # процесс работает дольше STALL_SECONDS
    await env.rec.tick()
    assert env.procs[0].terminated is False


async def test_camera_error_does_not_stop_others(make_cfg, tmp_path):
    cfg = make_cfg(recording={"enabled": True, "path": str(tmp_path)},
                   cameras=[{"id": "yard", "name": "Двор", "type": "rtsp", "url": "rtsp://x"},
                            {"id": "gate2", "name": "Ворота2", "type": "rtsp", "url": "rtsp://y"}])
    catalog = Catalog(cfg)
    ok_procs = []
    wall = datetime(2026, 9, 25, 5, 0, tzinfo=timezone.utc)  # 08:00 MSK
    mono = {"v": 1000.0}

    async def spawn(*args, **kwargs):
        if any("gate2" in a for a in args if isinstance(a, str)):
            raise ValueError("boom")
        p = FakeProc()
        ok_procs.append(p)
        return p

    rec = Recorder(cfg, catalog, spawn=spawn, now=lambda: wall, clock=lambda: mono["v"])
    await rec.tick()
    assert rec.active_cams() == {"yard"}
    assert len(ok_procs) == 1
    st = rec._states["gate2"]
    assert st.proc is None
    assert st.failures == 1
    assert st.next_start == mono["v"] + backoff(1)
