# Recording & Archive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Запись камер 08:00–19:00 (часовой пояс из конфига) из дополнительного потока в H.264 часовыми файлами, автоочистка, просмотр и скачивание архива в плеере для всех из белого списка.

**Architecture:** go2rtc отдаёт по RTSP (`:8554`, только docker-сеть) потоки `<id>~rec` (доп. поток Xiaomi/Dahua) и основные потоки. `Recorder` в процессе streemcam по расписанию запускает на каждую камеру ffmpeg (`-f segment`, фрагментированный MP4, H.264+AAC), перезапускает упавшие процессы и шлёт алерты. `Archive` отдаёт списки дней/часов и файлы с проверкой путей и чистит старое. FastAPI-эндпоинты `/api/archive/...` под сессией, вкладка «Архив» в плеере.

**Tech Stack:** Python 3.12+, asyncio subprocess, ffmpeg (в Docker-образе), zoneinfo + `tzdata`, FastAPI `FileResponse` (Range), существующие Catalog/Config.

**Spec:** `docs/superpowers/specs/2026-09-25-recording-design.md`

## Global Constraints

- Расписание: `start` включительно, `end` не включительно, время в `recording.timezone` (по умолчанию `Europe/Moscow`), `start`/`end` формата `HH:MM`, `start < end`.
- Поток записи: Xiaomi → `<id>~rec` (`subtype=record_subtype`, по умолчанию 1), Dahua → `<id>~rec` (`subtype=1`); RTSP и Tuya → основной поток `<id>`.
- ffmpeg: `libx264 -preset veryfast -crf 28 -g 50`, `aac -b:a 64k`, `-f segment -segment_time 3600 -segment_atclocktime 1 -reset_timestamps 1 -strftime 1 -segment_format mp4 -segment_format_options movflags=+frag_keyframe+empty_moov+default_base_moof`, выход `<root>/<cam>/%Y-%m-%d/%H-%M-%S.mp4`, ffmpeg запускается с `TZ=<recording.timezone>`.
- Имена: день `^\d{4}-\d{2}-\d{2}$`, файл `^\d{2}-\d{2}-\d{2}$` (+`.mp4`); час = первые две цифры.
- Хранение: `retention_days` (14), `min_free_gb` (15); файл «пишется», если изменялся за последние 60 с — такие не удаляются.
- Доступ к архиву — та же сессия, что у `/api/cameras` (401/403); 404 для неизвестной камеры, неверного формата, отсутствующего файла; путь обязан лежать внутри `recording.path` после `resolve()`.
- Перезапуск ffmpeg: пауза 5→10→20→40→60 с (максимум 60); процесс, проработавший > 60 с, считается здоровым; алерт админам, если камера не пишется > `alert_minutes` (10) в окне расписания; одно уведомление о восстановлении.
- Мягкая остановка: `q\n` в stdin, ожидание 10 с, `terminate`, 5 с, `kill`.
- `recording.enabled` по умолчанию `false`; камеры из конфига `record: true` по умолчанию; Tuya пишутся при `recording.tuya: true`.
- Тексты пользователю — на русском. Тесты без реального ffmpeg и без сети.
- Команды: `.venv/Scripts/python -m pytest ...` (Git Bash, Windows).

---

### Task 1: Конфиг записи и расписание

**Files:**
- Modify: `pyproject.toml`, `streemcam/config.py`
- Create: `streemcam/recording/__init__.py` (пустой), `streemcam/recording/schedule.py`
- Test: `tests/test_config.py`, `tests/test_rec_schedule.py`

**Interfaces:**
- Produces:
  - `RecordingConfig(enabled=False, timezone="Europe/Moscow", start="08:00", end="19:00", retention_days=14, min_free_gb=15, path="recordings", rtsp_url="rtsp://127.0.0.1:8554", tuya=True, alert_minutes=10)`; `Config.recording: RecordingConfig`.
  - Поле `record: bool = True` у `RtspCamera`, `DahuaCamera`, `XiaomiCamera`; `XiaomiCamera.record_subtype: int = 1`.
  - `schedule.parse_hhmm(s: str) -> datetime.time`; `schedule.is_recording_time(now: datetime, rec: RecordingConfig) -> bool`; `schedule.seconds_until_change(now: datetime, rec: RecordingConfig) -> float`; `schedule.local_now(now: datetime, rec) -> datetime` (перевод aware-времени в зону записи).

- [ ] **Step 1: Зависимость**

В `pyproject.toml` в `dependencies` добавить `"tzdata>=2024.1",` (нужно `zoneinfo` на Windows). Run: `.venv/Scripts/python -m pip install -e ".[dev]"`.

- [ ] **Step 2: Падающие тесты конфига**

Дописать в `tests/test_config.py`:
```python
def test_recording_defaults(cfg):
    r = cfg.recording
    assert (r.enabled, r.timezone, r.start, r.end) == (False, "Europe/Moscow", "08:00", "19:00")
    assert (r.retention_days, r.min_free_gb, r.path) == (14, 15, "recordings")
    assert (r.rtsp_url, r.tuya, r.alert_minutes) == ("rtsp://127.0.0.1:8554", True, 10)
    assert all(c.record for c in cfg.cameras)
    assert cfg.camera("room").record_subtype == 1


@pytest.mark.parametrize("rec", [
    {"start": "8:00"}, {"end": "25:00"}, {"start": "19:00", "end": "08:00"},
    {"start": "08:00", "end": "08:00"}, {"timezone": "Mars/Olympus"},
])
def test_recording_validation(make_cfg, rec):
    with pytest.raises(ConfigError, match="recording"):
        make_cfg(recording=rec)


def test_rtsp_url_trailing_slash(make_cfg):
    assert make_cfg(recording={"rtsp_url": "rtsp://go2rtc:8554/"}).recording.rtsp_url == "rtsp://go2rtc:8554"
```

Run: `.venv/Scripts/python -m pytest tests/test_config.py -v` → новые FAIL.

- [ ] **Step 3: Реализация конфига**

В `streemcam/config.py`:
- Импорты: `from zoneinfo import ZoneInfo, ZoneInfoNotFoundError`.
- После `CAMERA_ID = ...`: `HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")`.
- В `RtspCamera`, `DahuaCamera`, `XiaomiCamera` добавить поле `record: bool = True` (последним полем); в `XiaomiCamera` ещё `record_subtype: int = 1`.
- После `TuyaConfig` добавить:
```python
class RecordingConfig(BaseModel):
    enabled: bool = False
    timezone: str = "Europe/Moscow"
    start: str = "08:00"
    end: str = "19:00"
    retention_days: int = 14
    min_free_gb: int = 15
    path: str = "recordings"
    rtsp_url: str = "rtsp://127.0.0.1:8554"
    tuya: bool = True
    alert_minutes: int = 10

    @field_validator("start", "end")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        if not HHMM.match(v):
            raise ValueError("recording time must be HH:MM")
        return v

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"recording timezone not found: {v}") from None
        return v

    @field_validator("rtsp_url")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.rstrip("/")

    @model_validator(mode="after")
    def _order(self):
        if self.start >= self.end:
            raise ValueError("recording start must be earlier than end")
        return self
```
- В `Config` после `tuya: TuyaConfig = TuyaConfig()`: `recording: RecordingConfig = RecordingConfig()`.

(Сообщения ошибок pydantic для вложенной модели содержат путь `recording.…`, поэтому `match="recording"` в тестах срабатывает.)

- [ ] **Step 4: Падающие тесты расписания**

`tests/test_rec_schedule.py`:
```python
from datetime import datetime, timezone

import pytest

from streemcam.config import RecordingConfig
from streemcam.recording.schedule import (is_recording_time, local_now, parse_hhmm,
                                          seconds_until_change)

REC = RecordingConfig()  # Europe/Moscow = UTC+3, 08:00–19:00


def utc(h, m=0, s=0):
    return datetime(2026, 9, 25, h, m, s, tzinfo=timezone.utc)


def test_parse_hhmm():
    assert parse_hhmm("08:30").hour == 8 and parse_hhmm("08:30").minute == 30


@pytest.mark.parametrize("now,expected", [
    (utc(4, 59), False),   # 07:59 MSK
    (utc(5, 0), True),     # 08:00 MSK
    (utc(15, 59), True),   # 18:59 MSK
    (utc(16, 0), False),   # 19:00 MSK
    (utc(21, 0), False),   # 00:00 MSK
])
def test_is_recording_time(now, expected):
    assert is_recording_time(now, REC) is expected


def test_other_timezone():
    rec = RecordingConfig(timezone="UTC")
    assert is_recording_time(utc(8, 0), rec) is True
    assert is_recording_time(utc(5, 0), rec) is False


def test_local_now():
    assert local_now(utc(5, 0), REC).hour == 8


def test_seconds_until_change():
    assert seconds_until_change(utc(4, 59, 30), REC) == 30          # до начала
    assert seconds_until_change(utc(5, 0), REC) == 11 * 3600        # до конца
    assert seconds_until_change(utc(16, 0), REC) == 13 * 3600       # до завтрашнего начала
```

Run: `.venv/Scripts/python -m pytest tests/test_rec_schedule.py -v` → ImportError.

- [ ] **Step 5: Реализация расписания**

`streemcam/recording/__init__.py`: пустой.

`streemcam/recording/schedule.py`:
```python
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from ..config import RecordingConfig


def parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def local_now(now: datetime, rec: RecordingConfig) -> datetime:
    return now.astimezone(ZoneInfo(rec.timezone))


def is_recording_time(now: datetime, rec: RecordingConfig) -> bool:
    t = local_now(now, rec).time()
    return parse_hhmm(rec.start) <= t < parse_hhmm(rec.end)


def seconds_until_change(now: datetime, rec: RecordingConfig) -> float:
    local = local_now(now, rec)
    start = local.replace(hour=parse_hhmm(rec.start).hour, minute=parse_hhmm(rec.start).minute,
                          second=0, microsecond=0)
    end = local.replace(hour=parse_hhmm(rec.end).hour, minute=parse_hhmm(rec.end).minute,
                        second=0, microsecond=0)
    if local < start:
        target = start
    elif local < end:
        target = end
    else:
        target = start + timedelta(days=1)
    return (target - local).total_seconds()
```

- [ ] **Step 6: Тесты проходят, commit**

Run: `.venv/Scripts/python -m pytest -q` → всё PASS, 0 warnings.
```bash
git add pyproject.toml streemcam/config.py streemcam/recording tests/test_config.py tests/test_rec_schedule.py
git commit -m "feat: recording config and schedule"
```

---

### Task 2: Потоки для записи в go2rtc

**Files:**
- Modify: `streemcam/go2rtc_config.py`
- Test: `tests/test_go2rtc_config.py`

**Interfaces:**
- Consumes: `Config`, `RecordingConfig`, поля `record`/`record_subtype` (Task 1); `CameraInfo` из `streemcam/catalog.py` (есть: `id`, `name`, `kind`, `device_id`, `online`).
- Produces: `REC_SUFFIX = "~rec"`; `stream_url(cam, subtype: int | None = None) -> str` (опциональный override подпотока); `rec_stream_name(cfg, cam: CameraInfo) -> str | None` — имя потока go2rtc для записи или `None`, если камера не пишется; `render()` добавляет `rtsp: {listen: ":8554"}` и потоки `<id>~rec` для xiaomi/dahua с `record: true`.

- [ ] **Step 1: Падающие тесты**

Дописать в `tests/test_go2rtc_config.py`:
```python
from streemcam.catalog import CameraInfo
from streemcam.go2rtc_config import REC_SUFFIX, rec_stream_name


def test_render_rec_streams_and_rtsp(cfg):
    data = render(cfg, {"rtsp": {"username": "u"}})
    assert data["rtsp"] == {"username": "u", "listen": ":8554"}
    assert data["streams"]["room~rec"].endswith("&subtype=1")
    assert data["streams"]["room~rec"].startswith("xiaomi://")
    assert data["streams"]["gate~rec"].endswith("subtype=1")
    assert "yard~rec" not in data["streams"]  # rtsp пишется из основного потока


def test_render_skips_rec_when_record_false(make_cfg, base_data):
    cams = base_data["cameras"]
    cams[2]["record"] = False
    cams[2]["record_subtype"] = 2
    data = render(make_cfg(cameras=cams), None)
    assert "room~rec" not in data["streams"]


def test_rec_stream_name(cfg, make_cfg, base_data):
    assert REC_SUFFIX == "~rec"
    assert rec_stream_name(cfg, CameraInfo("yard", "Двор", "config")) == "yard"
    assert rec_stream_name(cfg, CameraInfo("gate", "Ворота", "config")) == "gate~rec"
    assert rec_stream_name(cfg, CameraInfo("room", "Комната", "config")) == "room~rec"
    tuya = CameraInfo("tuya_bf1", "Прихожая", "tuya", device_id="bf1", online=True)
    assert rec_stream_name(cfg, tuya) == "tuya_bf1"
    assert rec_stream_name(make_cfg(recording={"tuya": False}), tuya) is None
    cams = base_data["cameras"]
    cams[0]["record"] = False
    assert rec_stream_name(make_cfg(cameras=cams), CameraInfo("yard", "Двор", "config")) is None
    assert rec_stream_name(cfg, CameraInfo("nope", "X", "config")) is None
```

Run: `.venv/Scripts/python -m pytest tests/test_go2rtc_config.py -v` → FAIL.

- [ ] **Step 2: Реализация**

В `streemcam/go2rtc_config.py`:
- Импорт: `from .catalog import CameraInfo` (в `catalog.py` нет импорта go2rtc_config — цикла нет).
- После `COMPAT_SUFFIX` добавить `REC_SUFFIX = "~rec"`.
- `stream_url(cam, subtype: int | None = None)`: для `DahuaCamera` использовать `subtype if subtype is not None else cam.subtype`; для `XiaomiCamera` — `sub = subtype if subtype is not None else cam.subtype`, добавлять `query["subtype"] = sub`, если `sub is not None`. RTSP игнорирует `subtype`.
- Функция:
```python
def rec_stream_name(cfg: Config, cam: CameraInfo) -> str | None:
    if cam.kind == "tuya":
        return cam.id if cfg.recording.tuya else None
    src = cfg.camera(cam.id)
    if src is None or not src.record:
        return None
    if isinstance(src, (XiaomiCamera, DahuaCamera)):
        return cam.id + REC_SUFFIX
    return cam.id
```
- В `render()` после цикла compat-потоков:
```python
    for cam in cfg.cameras:
        if not cam.record:
            continue
        if isinstance(cam, XiaomiCamera):
            streams[cam.id + REC_SUFFIX] = stream_url(cam, subtype=cam.record_subtype)
        elif isinstance(cam, DahuaCamera):
            streams[cam.id + REC_SUFFIX] = stream_url(cam, subtype=1)
```
  и после строки с `webrtc`: `data["rtsp"] = {**(data.get("rtsp") or {}), "listen": ":8554"}`.

- [ ] **Step 3: Тесты проходят, commit**

Run: `.venv/Scripts/python -m pytest -q` → всё PASS.
```bash
git add streemcam/go2rtc_config.py tests/test_go2rtc_config.py
git commit -m "feat: go2rtc streams for recording sub-streams"
```

---

### Task 3: Recorder

**Files:**
- Create: `streemcam/recording/recorder.py`
- Test: `tests/test_recorder.py`

**Interfaces:**
- Consumes: `is_recording_time`, `local_now` (Task 1), `rec_stream_name` (Task 2), `Catalog.all()`.
- Produces:
  - `ffmpeg_args(input_url: str, out_dir: Path) -> list[str]`
  - `RecTarget(cam_id: str, name: str, input_url: str)` (frozen dataclass)
  - `targets(cfg, catalog) -> list[RecTarget]`
  - `Recorder(cfg, catalog, spawn=asyncio.create_subprocess_exec, now=None, clock=time.monotonic, on_alert=None, ffmpeg="ffmpeg")`:
    `available() -> bool`, `async tick() -> None`, `async run() -> None`, `async stop_all() -> None`, `active_cams() -> set[str]`.
  - `backoff(failures: int) -> float` → `min(60, 5 * 2 ** (failures - 1))`.
  - `on_alert(text: str)` — корутина.

- [ ] **Step 1: Падающие тесты**

`tests/test_recorder.py`:
```python
import asyncio
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from streemcam.catalog import Catalog
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

        async def spawn(*args, **kwargs):
            self.calls.append((args, kwargs))
            p = FakeProc()
            self.procs.append(p)
            return p

        async def on_alert(text):
            self.alerts.append(text)

        self.rec = Recorder(cfg, catalog, spawn=spawn, now=lambda: self.wall,
                            clock=lambda: self.mono, on_alert=on_alert)


@pytest.fixture
def rcfg(make_cfg, tmp_path):
    return make_cfg(recording={"enabled": True, "path": str(tmp_path), "rtsp_url": "rtsp://go2rtc:8554"})


def test_backoff():
    assert [backoff(n) for n in (1, 2, 3, 4, 5, 6)] == [5, 10, 20, 40, 60, 60]


def test_ffmpeg_args(tmp_path):
    args = ffmpeg_args("rtsp://go2rtc:8554/room~rec", tmp_path / "room")
    assert args[0] == "ffmpeg"
    assert args[args.index("-i") + 1] == "rtsp://go2rtc:8554/room~rec"
    joined = " ".join(args)
    for part in ["-rtsp_transport tcp", "-c:v libx264", "-preset veryfast", "-crf 28", "-g 50",
                 "-c:a aac", "-b:a 64k", "-f segment", "-segment_time 3600", "-segment_atclocktime 1",
                 "-reset_timestamps 1", "-strftime 1", "-segment_format mp4",
                 "-segment_format_options movflags=+frag_keyframe+empty_moov+default_base_moof",
                 "-map 0:v:0", "-map 0:a:0?"]:
        assert part in joined
    assert args[-1] == str(tmp_path / "room" / "%Y-%m-%d" / "%H-%M-%S.mp4")


def test_targets(rcfg):
    catalog = Catalog(rcfg)
    catalog.set_tuya([TuyaCamera("bf1", "Прихожая", True)])
    ts = {t.cam_id: t for t in targets(rcfg, catalog)}
    assert ts["yard"] == RecTarget("yard", "Двор", "rtsp://go2rtc:8554/yard")
    assert ts["gate"].input_url == "rtsp://go2rtc:8554/gate~rec"
    assert ts["room"].input_url == "rtsp://go2rtc:8554/room~rec"
    assert ts["tuya_bf1"].input_url == "rtsp://go2rtc:8554/tuya_bf1"


async def test_starts_in_window_creates_dirs_and_stops_outside(rcfg, tmp_path):
    env = Env(rcfg, Catalog(rcfg))
    await env.rec.tick()
    assert len(env.calls) == 3
    args, kwargs = env.calls[0]
    assert kwargs["env"]["TZ"] == "Europe/Moscow"
    assert kwargs["stdin"] == asyncio.subprocess.PIPE
    assert (tmp_path / "yard" / "2026-09-25").is_dir()
    assert (tmp_path / "yard" / "2026-09-26").is_dir()
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
    env.mono += 61                      # процесс живёт > 60 с — здоров
    await env.rec.tick()
    assert len(env.alerts) == 2 and "возобновилась" in env.alerts[1]


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
```

Run: `.venv/Scripts/python -m pytest tests/test_recorder.py -v` → ImportError.

- [ ] **Step 2: Реализация**

`streemcam/recording/recorder.py`:
```python
import asyncio
import logging
import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..catalog import Catalog
from ..config import Config
from ..go2rtc_config import rec_stream_name
from .schedule import is_recording_time, local_now

log = logging.getLogger(__name__)

TICK_SECONDS = 30
HEALTHY_AFTER_SECONDS = 60
STOP_WAIT_SECONDS = 10.0


def backoff(failures: int) -> float:
    return float(min(60, 5 * 2 ** (failures - 1)))


def ffmpeg_args(input_url: str, out_dir: Path, ffmpeg: str = "ffmpeg") -> list[str]:
    return [
        ffmpeg, "-hide_banner", "-loglevel", "warning", "-y",  # без -nostdin: stdin нужен для мягкой остановки "q"
        "-rtsp_transport", "tcp", "-i", input_url,
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
                 clock=time.monotonic, on_alert=None, ffmpeg: str = "ffmpeg"):
        self.cfg = cfg
        self.catalog = catalog
        self._spawn = spawn or asyncio.create_subprocess_exec
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._clock = clock
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
                await self.tick()
                await asyncio.sleep(TICK_SECONDS)
        finally:
            await self.stop_all()

    async def tick(self) -> None:
        mono = self._clock()
        wall = self._now()
        wanted = {t.cam_id: t for t in targets(self.cfg, self.catalog)} \
            if is_recording_time(wall, self.cfg.recording) else {}

        for cam_id, st in list(self._states.items()):
            if cam_id not in wanted:
                await self._stop(st)
                del self._states[cam_id]
                continue
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
            if st.proc is None and mono >= st.next_start:
                await self._start(st, wall, mono)
            await self._check_health(st, mono)

    async def _start(self, st: _State, wall: datetime, mono: float) -> None:
        out_dir = self._root / st.target.cam_id
        local = local_now(wall, self.cfg.recording)
        for day in (local, local + timedelta(days=1)):
            (out_dir / day.strftime("%Y-%m-%d")).mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "TZ": self.cfg.recording.timezone}
        try:
            st.proc = await self._spawn(*ffmpeg_args(st.target.input_url, out_dir, self._ffmpeg),
                                        stdin=asyncio.subprocess.PIPE,
                                        stdout=asyncio.subprocess.DEVNULL, env=env)
            st.started_at = mono
        except OSError as e:
            log.error("cannot start ffmpeg for %s: %s", st.target.cam_id, e)
            st.failures += 1
            st.next_start = mono + backoff(st.failures)

    async def _check_health(self, st: _State, mono: float) -> None:
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
        for st in list(self._states.values()):
            await self._stop(st)
        self._states.clear()

    async def _alert(self, text: str) -> None:
        if self._on_alert is None:
            return
        try:
            await self._on_alert(text)
        except Exception:
            log.exception("recording alert failed")
```

Заметка про алерт в тесте `test_alert_after_minutes_and_recovery`: после падения процесс перезапускается на следующем тике (пауза 5–60 с < 60 с шага), `down_since` не сбрасывается, пока процесс не проживёт ≥ 60 с — поэтому за 12 минут падений ровно один алерт, а затем при `mono += 61` без падения — восстановление.

- [ ] **Step 3: Тесты проходят, commit**

Run: `.venv/Scripts/python -m pytest tests/test_recorder.py -v` → PASS; `.venv/Scripts/python -m pytest -q` → всё PASS.
```bash
git add streemcam/recording/recorder.py tests/test_recorder.py
git commit -m "feat: scheduled ffmpeg recorder with restart backoff and alerts"
```

---

### Task 4: Archive

**Files:**
- Create: `streemcam/recording/archive.py`
- Test: `tests/test_archive.py`

**Interfaces:**
- Consumes: `Catalog.get()`.
- Produces: `DAY_RE`, `NAME_RE`; `HourEntry(name: str, hour: int, size: int, recording: bool)` (frozen dataclass);
  `Archive(root: Path, catalog, clock=time.time)`: `days(cam_id) -> list[str] | None`, `hours(cam_id, day) -> list[HourEntry] | None`, `file(cam_id, day, name) -> Path | None`, `cleanup(today: date, retention_days: int, min_free_gb: float, disk_free=None) -> list[Path]`.
  `None` = камера неизвестна / формат неверен (→ 404 в вебе).

- [ ] **Step 1: Падающие тесты**

`tests/test_archive.py`:
```python
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
```

Run: `.venv/Scripts/python -m pytest tests/test_archive.py -v` → ImportError.

- [ ] **Step 2: Реализация**

`streemcam/recording/archive.py`:
```python
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
```

- [ ] **Step 3: Тесты проходят, commit**

Run: `.venv/Scripts/python -m pytest tests/test_archive.py -v` → PASS; `.venv/Scripts/python -m pytest -q` → всё PASS.
```bash
git add streemcam/recording/archive.py tests/test_archive.py
git commit -m "feat: recordings archive listing, safe file lookup and cleanup"
```

---

### Task 5: API архива

**Files:**
- Modify: `streemcam/web/server.py`
- Test: `tests/test_web_archive.py`

**Interfaces:**
- Consumes: `Archive` (Task 4); существующие `create_app(cfg, catalog, access, monitor, registry, http, upstream_connect=None)`, `current_user`.
- Produces: `create_app(..., upstream_connect=None, archive=None)`; `/api/cameras` дополнительно `"recording": bool` (`archive is not None`); эндпоинты:
  - `GET /api/archive/{cam_id}/days` → `{"days": [...]}`
  - `GET /api/archive/{cam_id}/{day}` → `{"hours": [{"name","hour","size","recording"}]}`
  - `GET /api/archive/{cam_id}/{day}/{name}.mp4[?download=1]` → `FileResponse` (`video/mp4`, Range), при `download=1` — `Content-Disposition: attachment; filename="<cam>_<day>_<name>.mp4"`.
  - `archive is None` → 404 на все три.

- [ ] **Step 1: Падающие тесты**

`tests/test_web_archive.py`:
```python
import os

import pytest
from fastapi.testclient import TestClient

from streemcam.identity import Identity
from streemcam.recording.archive import Archive

USER = Identity("tg", 42)


@pytest.fixture
def aweb(make_web, cfg, tmp_path):
    d = tmp_path / "yard" / "2026-09-25"
    d.mkdir(parents=True)
    f = d / "08-00-00.mp4"
    f.write_bytes(bytes(range(256)) * 4)          # 1024 байта
    os.utime(f, (0, 0))
    web = make_web(cfg)
    web.archive = Archive(tmp_path, web.catalog)
    web.app = web.make_app(archive=web.archive)
    return web


def h(web):
    return {"Authorization": f"Bearer {web.access.issue_session(USER)}"}


def test_days_hours_and_file(aweb):
    c = TestClient(aweb.app)
    assert c.get("/api/archive/yard/days", headers=h(aweb)).json() == {"days": ["2026-09-25"]}
    assert c.get("/api/archive/yard/2026-09-25", headers=h(aweb)).json() == {
        "hours": [{"name": "08-00-00", "hour": 8, "size": 1024, "recording": False}]}
    r = c.get("/api/archive/yard/2026-09-25/08-00-00.mp4", headers=h(aweb))
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp4" and len(r.content) == 1024
    assert "attachment" not in r.headers.get("content-disposition", "")


def test_range_and_download(aweb):
    c = TestClient(aweb.app)
    r = c.get("/api/archive/yard/2026-09-25/08-00-00.mp4",
              headers={**h(aweb), "Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100
    r = c.get(f"/api/archive/yard/2026-09-25/08-00-00.mp4?download=1&s={aweb.access.issue_session(USER)}")
    assert r.status_code == 200
    assert 'filename="yard_2026-09-25_08-00-00.mp4"' in r.headers["content-disposition"]
    assert r.headers["content-disposition"].startswith("attachment")


def test_errors(aweb):
    c = TestClient(aweb.app)
    assert c.get("/api/archive/yard/days").status_code == 401
    assert c.get("/api/archive/nope/days", headers=h(aweb)).status_code == 404
    assert c.get("/api/archive/yard/2026-9-25", headers=h(aweb)).status_code == 404
    assert c.get("/api/archive/yard/2026-09-25/09-00-00.mp4", headers=h(aweb)).status_code == 404
    assert c.get("/api/archive/yard/2026-09-25/..%2F..%2Fx.mp4", headers=h(aweb)).status_code == 404


async def test_denied_user(aweb):
    token = aweb.access.issue_session(USER)
    await aweb.access.deny(USER)
    r = TestClient(aweb.app).get("/api/archive/yard/days", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


def test_disabled_archive(web):
    c = TestClient(web.app)
    assert c.get("/api/archive/yard/days", headers=h(web)).status_code == 404
    assert c.get("/api/cameras", headers=h(web)).json()["recording"] is False


def test_cameras_recording_flag(aweb):
    assert TestClient(aweb.app).get("/api/cameras", headers=h(aweb)).json()["recording"] is True
```

В `tests/conftest.py` в фикстуре `make_web` добавить в возвращаемый `SimpleNamespace` фабрику `make_app`, пересоздающую приложение с теми же зависимостями и дополнительными kwargs:
```python
        def make_app(**kwargs):
            return create_app(cfg, catalog, access, StubMonitor(), registry, http,
                              upstream_connect=upstream_connect, **kwargs)
        return SimpleNamespace(app=app, access=access, registry=registry, catalog=catalog, make_app=make_app)
```
(заменив существующую строку `return SimpleNamespace(...)`).

Run: `.venv/Scripts/python -m pytest tests/test_web_archive.py -v` → FAIL.

- [ ] **Step 2: Реализация**

В `streemcam/web/server.py`:
- Импорт: `from ..recording.archive import Archive`.
- Сигнатура: `def create_app(cfg, catalog, access, monitor, registry, http, upstream_connect=None, archive: Archive | None = None) -> FastAPI:`
- В `/api/cameras` добавить `"recording": archive is not None,`.
- Перед маршрутом `@app.get("/")` добавить:
```python
    def _archive() -> Archive:
        if archive is None:
            raise HTTPException(404, "recording disabled")
        return archive

    @app.get("/api/archive/{cam_id}/days")
    async def archive_days(cam_id: str, user: Identity = Depends(current_user)):
        days = _archive().days(cam_id)
        if days is None:
            raise HTTPException(404, "unknown camera")
        return {"days": days}

    @app.get("/api/archive/{cam_id}/{day}")
    async def archive_hours(cam_id: str, day: str, user: Identity = Depends(current_user)):
        hours = _archive().hours(cam_id, day)
        if hours is None:
            raise HTTPException(404, "not found")
        return {"hours": [{"name": e.name, "hour": e.hour, "size": e.size, "recording": e.recording}
                          for e in hours]}

    @app.get("/api/archive/{cam_id}/{day}/{name}.mp4")
    async def archive_file(cam_id: str, day: str, name: str, download: int = 0,
                           user: Identity = Depends(current_user)):
        path = _archive().file(cam_id, day, name)
        if path is None:
            raise HTTPException(404, "not found")
        if download:
            return FileResponse(path, media_type="video/mp4",
                                filename=f"{cam_id}_{day}_{name}.mp4")
        return FileResponse(path, media_type="video/mp4")
```
(`FileResponse` с `filename=` выставляет `Content-Disposition: attachment; filename="..."`; Range поддерживается Starlette ≥ 0.39 — установлена 1.7.)

- [ ] **Step 3: Тесты проходят, commit**

Run: `.venv/Scripts/python -m pytest -q` → всё PASS, 0 warnings.
```bash
git add streemcam/web/server.py tests/conftest.py tests/test_web_archive.py
git commit -m "feat: archive API (days, hours, ranged file download)"
```

---

### Task 6: Вкладка «Архив» в плеере

**Files:**
- Modify: `streemcam/web/static/index.html`, `streemcam/web/static/app.js`, `streemcam/web/static/style.css`
- Test: `tests/test_web_static.py`

**Interfaces:**
- Consumes: `/api/cameras` (`recording`), `/api/archive/...` (Task 5), существующие функции app.js: `$`, `api`, `token`, `info`, `showMessage`, `stopPlayers`, `renderList`, `snapshotUrl`.

- [ ] **Step 1: Падающий тест**

Дописать в `tests/test_web_static.py`:
```python
def test_archive_ui_present(web):
    client = TestClient(web.app)
    assert 'id="mode-archive"' in client.get("/").text
    js = client.get("/static/app.js").text
    assert "/api/archive/" in js and "download=1" in js
```
Run → FAIL.

- [ ] **Step 2: index.html**

В `<header>` сразу после `<h1 id="title">Камеры</h1>` вставить:
```html
    <button id="mode-archive" hidden>Архив</button>
```
В `<main>` после `<section id="viewer" hidden></section>` вставить:
```html
    <section id="archive" hidden>
      <div id="archive-days" class="chips"></div>
      <div id="archive-hours" class="chips"></div>
      <div id="archive-player" hidden>
        <video id="archive-video" controls playsinline preload="metadata"></video>
        <a id="archive-download" class="button" download>Скачать</a>
      </div>
      <p id="archive-empty" hidden></p>
    </section>
```

- [ ] **Step 3: app.js**

Добавить в конец файла перед строкой `main();`:
```js
let archiveMode = false;
let archiveCam = null;

function archiveUrl(path) {
  return `/api/archive/${path}`;
}

function fileUrl(cam, day, name, download) {
  const q = `s=${encodeURIComponent(token)}${download ? "&download=1" : ""}`;
  return archiveUrl(`${encodeURIComponent(cam)}/${day}/${name}.mp4?${q}`);
}

function hideArchive() {
  const video = $("#archive-video");
  video.pause();
  video.removeAttribute("src");
  video.load();
  $("#archive").hidden = true;
}

function setArchiveMode(on) {
  archiveMode = on;
  $("#mode-archive").textContent = on ? "Живое" : "Архив";
  hideArchive();
  renderList();
  $("#title").textContent = on ? "Архив" : "Камеры";
}

function chip(text, onClick, active) {
  const b = document.createElement("button");
  b.textContent = text;
  if (active) b.classList.add("active");
  b.onclick = onClick;
  return b;
}

async function openArchive(cam) {
  stopPlayers();
  archiveCam = cam;
  $("#cams").hidden = true;
  $("#grid").hidden = true;
  $("#back").hidden = false;
  $("#title").textContent = `Архив: ${cam.name}`;
  $("#archive").hidden = false;
  $("#archive-player").hidden = true;
  $("#archive-hours").replaceChildren();
  const r = await api(archiveUrl(`${encodeURIComponent(cam.id)}/days`));
  const days = r.ok ? (await r.json()).days : [];
  $("#archive-empty").hidden = days.length > 0;
  $("#archive-empty").textContent = "Записей пока нет.";
  $("#archive-days").replaceChildren(...days.map((d) => chip(d, () => openDay(cam, d))));
  if (days.length) openDay(cam, days[0]);
}

async function openDay(cam, day) {
  for (const b of $("#archive-days").children) b.classList.toggle("active", b.textContent === day);
  const r = await api(archiveUrl(`${encodeURIComponent(cam.id)}/${day}`));
  const hours = r.ok ? (await r.json()).hours : [];
  $("#archive-hours").replaceChildren(...hours.map((h) => {
    const label = `${h.name.slice(0, 2)}:${h.name.slice(3, 5)}${h.recording ? " •" : ""} · ${(h.size / 1e6).toFixed(0)} МБ`;
    return chip(label, (ev) => playHour(cam, day, h, ev.currentTarget));
  }));
}

function playHour(cam, day, h, button) {
  for (const b of $("#archive-hours").children) b.classList.toggle("active", b === button);
  const video = $("#archive-video");
  video.src = fileUrl(cam.id, day, h.name, false);
  video.play().catch(() => {});
  $("#archive-download").href = fileUrl(cam.id, day, h.name, true);
  $("#archive-player").hidden = false;
}

$("#mode-archive").onclick = () => setArchiveMode(!archiveMode);
```

Изменить существующие функции:
- В `renderList()`: заменить `li.onclick = () => openCameras([cam]);` на `li.onclick = () => (archiveMode ? openArchive(cam) : openCameras([cam]));`; в конце функции добавить `$("#mode-archive").hidden = !info.recording;` и `if (archiveMode) $("#grid").hidden = true;`.
- В обработчике `$("#back").onclick` добавить первым действием `hideArchive();`.
- В `showMessage(text)` добавить `hideArchive();` после `stopPlayers();` и `$("#mode-archive").hidden = true;`.

- [ ] **Step 4: style.css**

Дописать:
```css
#archive .chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }
#archive .chips button { background: var(--card); color: var(--fg); }
#archive .chips button.active { background: var(--accent); color: var(--accent-fg); }
#archive-player video { width: 100%; max-height: 70vh; background: #000; border-radius: 12px; }
#archive-download, .button { display: inline-block; margin-top: 8px; background: var(--accent); color: var(--accent-fg); border-radius: 8px; padding: 6px 12px; text-decoration: none; }
#archive-empty { color: var(--muted); }
```

- [ ] **Step 5: Проверки и commit**

Run: `node --check streemcam/web/static/app.js` → OK; `.venv/Scripts/python -m pytest -q` → всё PASS.
```bash
git add streemcam/web/static tests/test_web_static.py
git commit -m "feat: archive tab in the player (days, hours, playback, download)"
```

---

### Task 7: Подключение записи в приложение

**Files:**
- Modify: `streemcam/app.py`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `Recorder` (Task 3), `Archive` (Task 4), `create_app(..., archive=)` (Task 5), `local_now` (Task 1).
- Produces: `async cleanup_forever(archive, cfg, interval_seconds: float, now=None)` — при старте и затем каждые `interval_seconds` вызывает `archive.cleanup(local_now(now()).date(), cfg.recording.retention_days, cfg.recording.min_free_gb)` в `asyncio.to_thread`; в `run(cfg)` при `cfg.recording.enabled`: `Archive(Path(cfg.recording.path), catalog)`, `Recorder(cfg, catalog, on_alert=on_tuya_alert)`, supervise `recorder.run` и `cleanup_forever(archive, cfg, 3600)`, `create_app(..., archive=archive)`.

- [ ] **Step 1: Падающий тест**

Дописать в `tests/test_app.py`:
```python
async def test_cleanup_forever_calls_cleanup(make_cfg, tmp_path):
    from datetime import datetime, timezone
    from streemcam.app import cleanup_forever

    cfg = make_cfg(recording={"enabled": True, "path": str(tmp_path), "retention_days": 3, "min_free_gb": 1})
    calls = []

    class A:
        def cleanup(self, today, retention_days, min_free_gb):
            calls.append((today.isoformat(), retention_days, min_free_gb))
            return []

    task = asyncio.create_task(cleanup_forever(
        A(), cfg, 0, now=lambda: datetime(2026, 9, 25, 22, 0, tzinfo=timezone.utc)))
    for _ in range(50):
        await asyncio.sleep(0)
        if len(calls) >= 2:
            break
    task.cancel()
    assert calls[0] == ("2026-09-26", 3, 1)   # 22:00 UTC = 01:00 MSK следующего дня
```
Run → ImportError.

- [ ] **Step 2: Реализация**

В `streemcam/app.py`:
- Импорты: `from datetime import datetime, timezone`, `from pathlib import Path`.
- Функция:
```python
async def cleanup_forever(archive, cfg: Config, interval_seconds: float, now=None) -> None:
    from .recording.schedule import local_now
    now = now or (lambda: datetime.now(timezone.utc))
    while True:
        today = local_now(now(), cfg.recording).date()
        await asyncio.to_thread(archive.cleanup, today, cfg.recording.retention_days,
                                cfg.recording.min_free_gb)
        await asyncio.sleep(interval_seconds)
```
- В `run(cfg)` после блока Tuya (перед `monitor = ...`):
```python
    archive = recorder = None
    if cfg.recording.enabled:
        from .recording.archive import Archive
        from .recording.recorder import Recorder
        archive = Archive(Path(cfg.recording.path), catalog)
        recorder = Recorder(cfg, catalog, on_alert=on_tuya_alert)
```
- `app = create_app(cfg, catalog, access, monitor, access.registry, http, archive=archive)`.
- После `background = [...]`:
```python
    if recorder is not None:
        background.append(asyncio.create_task(supervise("recorder", recorder.run)))
        background.append(asyncio.create_task(
            supervise("archive-cleanup", lambda: cleanup_forever(archive, cfg, 3600))))
```
(`on_tuya_alert(text)` — уже существующая обёртка «отправить текст админам»; используется и для алертов записи.)

- [ ] **Step 3: Тесты и smoke-проверка**

Run: `.venv/Scripts/python -m pytest -q` → всё PASS.
Smoke (во временной папке вне репо): конфиг с `recording: {enabled: true, path: <tmp>/rec, start: "00:00", end: "23:59"}`, одна rtsp-камера, `listen_port: 18080`, без токенов, `tuya` выключен. Запуск `.venv/Scripts/python -m streemcam --config <tmp>/config.yaml run` на ~8 с. Ожидается: сервер отвечает 200 на `/`; если `ffmpeg` нет в PATH — в логе `recording disabled: ffmpeg not found`, приложение продолжает работать; если есть — в логе предупреждения ffmpeg о недоступном RTSP и перезапуски с паузой. `curl http://127.0.0.1:18080/api/archive/<cam>/days` без сессии → 401. Остановить процесс. Приложить логи.

- [ ] **Step 4: Commit**
```bash
git add streemcam/app.py tests/test_app.py
git commit -m "feat: run recorder and archive cleanup when recording is enabled"
```

---

### Task 8: Docker, примеры, README

**Files:**
- Modify: `Dockerfile`, `docker-compose.yml`, `.gitignore`, `.dockerignore`, `config.example.yaml`, `README.md`

- [ ] **Step 1: Dockerfile**

После `WORKDIR /app` вставить:
```dockerfile
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg tzdata \
 && rm -rf /var/lib/apt/lists/*
```

- [ ] **Step 2: compose и ignore-файлы**

В `docker-compose.yml` у сервиса `streemcam` в `volumes` добавить строку `- ./recordings:/recordings`.
В `.gitignore` и `.dockerignore` добавить строку `recordings/` (в `.dockerignore` — `recordings`).

- [ ] **Step 3: config.example.yaml**

В конец файла добавить:
```yaml

recording:
  enabled: true
  timezone: Europe/Moscow
  start: "08:00"             # включительно
  end: "19:00"               # не включительно; файлы режутся по часам
  retention_days: 14
  min_free_gb: 15            # при нехватке места удаляются самые старые часы
  path: /recordings
  rtsp_url: rtsp://go2rtc:8554
  tuya: true                 # писать камеры Tuya (поток из облака — расходует трафик)
  alert_minutes: 10
```
У камеры `test` добавить строку `    record: false` (тестовый поток не пишем). У камер C701 добавить комментарий в строке `model:` не нужно — `record_subtype` по умолчанию 1.

- [ ] **Step 4: README**

Добавить раздел перед `## Изменение конфигурации`:
```markdown
## Запись и архив
- Запись идёт по расписанию (`recording.start`–`recording.end`, часовой пояс `recording.timezone`)
  из дополнительного потока камеры (Xiaomi — `record_subtype`, по умолчанию 1), перекодируется в H.264
  и режется на часовые файлы `recordings/<камера>/<ГГГГ-ММ-ДД>/<ЧЧ-ММ-СС>.mp4`.
- Архив доступен всем из белого списка: в плеере кнопка «Архив» → камера → дата → час, есть «Скачать».
- Очистка: записи старше `retention_days` удаляются; если свободно меньше `min_free_gb`, удаляются самые старые часы.
- Оценка места: доп. поток ≈ 0,15–0,2 ГБ/ч на камеру → 3 камеры × 11 ч ≈ 5–7 ГБ в сутки.
- Не писать камеру: `record: false` у камеры; не писать Tuya: `recording.tuya: false`.
- Если запись не идёт больше `alert_minutes`, админам придёт уведомление в Telegram.
```

- [ ] **Step 5: Проверки и commit**

- `docker compose config -q` с временными `config.yaml`/`.env` из примеров (если демон недоступен — отметить в отчёте), удалить временные файлы.
- `.venv/Scripts/python -m streemcam --config config.example.yaml render-go2rtc --out <tmp>/go2rtc.yaml` с env `STREEMCAM_SECRET`, `STREEMCAM_INTERNAL_KEY` → в выводе есть `rtsp:` с `listen: :8554` и потоки `c701_*~rec`.
- `.venv/Scripts/python -m pytest -q` → всё PASS.
```bash
git add Dockerfile docker-compose.yml .gitignore .dockerignore config.example.yaml README.md
git commit -m "chore: ffmpeg in image, recordings volume, recording docs and example"
```
