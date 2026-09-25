# Tuya Cameras + C701 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Камеры из аккаунта Tuya/Smart Life (вход по QR через Telegram-бота) и Xiaomi C701 в плеере streemcam, плюс «совместимый режим» H.264 с лимитом перекодировок.

**Architecture:** Новый пакет `streemcam/tuya/` (вход по QR, хранение токенов, клиент облака) и `TuyaService`, который наполняет динамический `Catalog` камер. `Go2rtcSync` регистрирует в go2rtc через API потоки `tuya_<id>` (источник `echo:curl` к внутреннему серверу streemcam на :8081, выдающему свежую RTSP-ссылку) и потоки `<id>~h264` (ffmpeg-перекодирование). Веб, монитор и боты переходят с `cfg.cameras` на `Catalog`.

**Tech Stack:** Python 3.12+, FastAPI, httpx, aiogram 3, discord.py 2, `tuya-device-sharing-sdk`, `segno`, go2rtc ≥ 1.9.13.

**Spec:** `docs/superpowers/specs/2026-09-25-tuya-cameras-design.md` (база: `docs/superpowers/specs/2026-09-25-streemcam-design.md`)

## Global Constraints

- Один аккаунт Tuya. Камеры Tuya — все устройства категории `sp`, ID `tuya_` + `device_id.lower()`.
- `device_id` Tuya допускается только `^[A-Za-z0-9]+$`; иные устройства пропускаются (лог warning).
- `internal_key` — `^[A-Za-z0-9_-]{16,}$`, из `.env` (`STREEMCAM_INTERNAL_KEY`); обязателен при `tuya.enabled: true`.
- Внутренний сервер (:8081) не публикуется наружу; сравнение ключа — `hmac.compare_digest`.
- Совместимый поток: имя `<cam_id>~h264`, источник `ffmpeg:<cam_id>#video=h264#width=1280#audio=aac`.
- Лимит перекодировок `max_transcodes` (по умолчанию 2), при превышении WS закрывается кодом `4430`.
- ID камер из конфига не могут начинаться с `tuya_` (и `~` запрещена существующим regex `^[a-z0-9_-]+$`).
- SDK: `CLIENT_ID = "HA_3y9q4ak7g4ephrvke"`, `SCHEMA = "haauthorize"`, QR-содержимое `tuyaSmart--qrLogin?token=<qrcode>`.
- Вызовы SDK синхронные — только через `asyncio.to_thread`. Тесты — без сети (SDK подменяется).
- Команды Tuya — только администраторам. Все тексты пользователю — на русском.
- Команды: venv `.venv/Scripts/python -m pytest ...` (Git Bash на Windows).

### Отклонения от спеки (согласовать при ревью)
1. Спека: «`TuyaAuthError` → уведомление». SDK не различает ошибки авторизации (бросает общий `Exception("(code) msg")`), поэтому уведомление админам шлётся после **3 подряд** неудачных обновлений списка камер Tuya, текст предлагает `/tuya_login`; сбрасывается после успеха.
2. Спека: «при 4430 плеер показывает сообщение». Плеер go2rtc (`video-rtc.js`) не отдаёт код закрытия, поэтому при включённом совместимом режиме под переключателем постоянно видна подсказка о лимите.
3. Для совместимого потока звук `aac` (а не `opus`): цель режима — максимальная совместимость (Safari/Firefox MSE).

## File Structure

```
streemcam/
  config.py            + TuyaConfig, max_transcodes, internal_listen/url/key, валидации
  catalog.py           NEW  CameraInfo, Catalog, tuya_cam_id
  go2rtc_sync.py       NEW  compat_name/compat_src/tuya_src, Go2rtcSync
  streams.py           + TranscodeLimiter
  monitor.py           работает по Catalog; для Tuya — online из облака, снимок раз в N мин
  app.py               + Catalog, Go2rtcSync, TuyaService, внутренний сервер; alert_text по Catalog
  tuya/__init__.py     NEW (пустой)
  tuya/models.py       NEW  TuyaCredentials, TuyaCamera, TuyaError, TuyaLoginError
  tuya/store.py        NEW  TuyaStore (SQLite, одна строка)
  tuya/login.py        NEW  QrSession, TuyaLogin (LoginControl)
  tuya/qr.py           NEW  qr_png (segno)
  tuya/client.py       NEW  TuyaClient (Manager)
  tuya/service.py      NEW  TuyaService (вход, обновление, выдача ссылки)
  web/server.py        Catalog вместо cfg.cameras, kind, compat + лимит
  web/internal.py      NEW  внутреннее приложение GET /tuya/{device_id}
  web/static/*         переключатель «Совместимый режим»
  tg/bot.py            /tuya_login /tuya_status /tuya_logout
  dc/bot.py            Catalog вместо cfg.cameras
tests/ ...             новые test_*.py + правки существующих
config.example.yaml, .env.example, docker-compose.yml, README.md
```

---

### Task 1: Конфиг и зависимости

**Files:**
- Modify: `pyproject.toml`, `streemcam/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Config.tuya: TuyaConfig(enabled: bool=False, refresh_minutes: int=10, snapshot_minutes: int=15)`, `Config.max_transcodes: int = 2`, `Config.internal_listen: str = "127.0.0.1:8081"`, `Config.internal_url: str = "http://127.0.0.1:8081"`, `Config.internal_key: str | None = None`.

- [ ] **Step 1: Зависимости**

В `pyproject.toml` в `dependencies` добавить две строки после `"python-dotenv>=1.0",`:
```toml
    "tuya-device-sharing-sdk>=0.2.1",
    "segno>=1.6",
```
Run: `.venv/Scripts/python -m pip install -e ".[dev]"` → установка без ошибок.
Run: `.venv/Scripts/python -c "from tuya_sharing import LoginControl, Manager, SharingTokenListener; import segno; print('ok')"` → `ok`.

- [ ] **Step 2: Падающие тесты**

Дописать в конец `tests/test_config.py`:
```python
def test_tuya_defaults(cfg):
    assert cfg.tuya.enabled is False
    assert cfg.tuya.refresh_minutes == 10
    assert cfg.tuya.snapshot_minutes == 15
    assert cfg.max_transcodes == 2
    assert cfg.internal_listen == "127.0.0.1:8081"
    assert cfg.internal_url == "http://127.0.0.1:8081"
    assert cfg.internal_key is None


def test_tuya_enabled_requires_internal_key(make_cfg):
    with pytest.raises(ConfigError, match="internal_key"):
        make_cfg(tuya={"enabled": True})
    cfg = make_cfg(tuya={"enabled": True}, internal_key="k" * 16)
    assert cfg.internal_key == "k" * 16


@pytest.mark.parametrize("key", ["short", "has space 0123456789", "bad/char-0123456789"])
def test_internal_key_format(make_cfg, key):
    with pytest.raises(ConfigError, match="internal_key"):
        make_cfg(internal_key=key)


def test_empty_internal_key_is_none(make_cfg):
    assert make_cfg(internal_key="").internal_key is None


def test_internal_url_trailing_slash_stripped(make_cfg):
    assert make_cfg(internal_url="http://streemcam:8081/").internal_url == "http://streemcam:8081"


def test_config_camera_id_cannot_start_with_tuya(make_cfg, base_data):
    cams = base_data["cameras"]
    cams[0]["id"] = "tuya_abc"
    with pytest.raises(ConfigError, match="tuya_"):
        make_cfg(cameras=cams)
```

Run: `.venv/Scripts/python -m pytest tests/test_config.py -v` → новые тесты FAIL (`AttributeError: 'Config' object has no attribute 'tuya'` и т.п.).

- [ ] **Step 3: Реализация**

В `streemcam/config.py`:

1) После `CAMERA_ID = ...` добавить:
```python
INTERNAL_KEY = re.compile(r"^[A-Za-z0-9_-]{16,}$")
```

2) После класса `DiscordConfig` добавить:
```python
class TuyaConfig(BaseModel):
    enabled: bool = False
    refresh_minutes: int = 10
    snapshot_minutes: int = 15
```

3) В `Config` после строки `offline_alert_minutes: int = 5` добавить поля:
```python
    max_transcodes: int = 2
    internal_listen: str = "127.0.0.1:8081"
    internal_url: str = "http://127.0.0.1:8081"
    internal_key: str | None = None
    tuya: TuyaConfig = TuyaConfig()
```

4) В `Config` после валидатора `_strip_slash` добавить:
```python
    @field_validator("internal_url")
    @classmethod
    def _strip_internal_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @field_validator("internal_key", mode="before")
    @classmethod
    def _internal_key_format(cls, v):
        if not v:
            return None
        if not INTERNAL_KEY.match(v):
            raise ValueError("internal_key must match ^[A-Za-z0-9_-]{16,}$")
        return v
```

5) Заменить `_unique_camera_ids` на:
```python
    @model_validator(mode="after")
    def _check_cameras_and_tuya(self):
        seen = set()
        for cam in self.cameras:
            if cam.id in seen:
                raise ValueError(f"duplicate camera id: {cam.id}")
            if cam.id.startswith("tuya_"):
                raise ValueError(f"camera id must not start with tuya_: {cam.id}")
            seen.add(cam.id)
        if self.tuya.enabled and not self.internal_key:
            raise ValueError("internal_key is required when tuya.enabled is true")
        return self
```

- [ ] **Step 4: Тесты проходят**

Run: `.venv/Scripts/python -m pytest -q` → все PASS, 0 warnings.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml streemcam/config.py tests/test_config.py
git commit -m "feat: config for Tuya, transcode limit and internal server"
```

---

### Task 2: Tuya — модели, хранилище, вход по QR, картинка QR

**Files:**
- Create: `streemcam/tuya/__init__.py` (пустой), `streemcam/tuya/models.py`, `streemcam/tuya/store.py`, `streemcam/tuya/login.py`, `streemcam/tuya/qr.py`
- Test: `tests/test_tuya_store.py`, `tests/test_tuya_login.py`

**Interfaces:**
- Produces:
  - `TuyaCredentials(user_code: str, terminal_id: str, endpoint: str, token_info: dict)` (dataclass)
  - `TuyaCamera(device_id: str, name: str, online: bool)` (frozen dataclass)
  - `TuyaError(Exception)`, `TuyaLoginError(TuyaError)`
  - `TuyaStore(path: str)`: `save(creds)`, `load() -> TuyaCredentials | None`, `update_token(token_info: dict)`, `clear()`
  - `CLIENT_ID`, `SCHEMA`; `QrSession(user_code: str, token: str)` с `.qr_payload -> str`; `TuyaLogin(control=None)`: `start(user_code) -> QrSession`, `poll(session) -> TuyaCredentials | None`
  - `qr_png(payload: str) -> bytes`

- [ ] **Step 1: Модели**

`streemcam/tuya/__init__.py`: пустой файл.

`streemcam/tuya/models.py`:
```python
from dataclasses import dataclass, field


class TuyaError(Exception):
    pass


class TuyaLoginError(TuyaError):
    pass


@dataclass
class TuyaCredentials:
    user_code: str
    terminal_id: str
    endpoint: str
    token_info: dict = field(default_factory=dict)


@dataclass(frozen=True)
class TuyaCamera:
    device_id: str
    name: str
    online: bool
```

- [ ] **Step 2: Падающие тесты хранилища**

`tests/test_tuya_store.py`:
```python
from streemcam.tuya.models import TuyaCredentials
from streemcam.tuya.store import TuyaStore

CREDS = TuyaCredentials("UC1", "term-1", "https://apigw.tuyaeu.com",
                        {"t": 1, "uid": "u1", "expire_time": 7200,
                         "access_token": "a1", "refresh_token": "r1"})


def test_empty_store():
    assert TuyaStore(":memory:").load() is None


def test_save_load_roundtrip():
    s = TuyaStore(":memory:")
    s.save(CREDS)
    assert s.load() == CREDS


def test_save_replaces_single_row():
    s = TuyaStore(":memory:")
    s.save(CREDS)
    s.save(TuyaCredentials("UC2", "term-2", "https://x", {"uid": "u2"}))
    assert s.load().user_code == "UC2"


def test_update_token():
    s = TuyaStore(":memory:")
    s.save(CREDS)
    s.update_token({"t": 2, "uid": "u1", "access_token": "a2", "refresh_token": "r2", "expire_time": 7200})
    assert s.load().token_info["access_token"] == "a2"
    assert s.load().user_code == "UC1"


def test_update_token_without_account_is_noop():
    s = TuyaStore(":memory:")
    s.update_token({"access_token": "a"})
    assert s.load() is None


def test_clear():
    s = TuyaStore(":memory:")
    s.save(CREDS)
    s.clear()
    assert s.load() is None


def test_file_store_persists(tmp_path):
    path = str(tmp_path / "d" / "x.db")
    TuyaStore(path).save(CREDS)
    assert TuyaStore(path).load() == CREDS
```

Run: `.venv/Scripts/python -m pytest tests/test_tuya_store.py -v` → ImportError.

- [ ] **Step 3: Реализация store**

`streemcam/tuya/store.py`:
```python
import json
import sqlite3
import time
from pathlib import Path

from .models import TuyaCredentials

SCHEMA = """
CREATE TABLE IF NOT EXISTS tuya_account (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    user_code TEXT NOT NULL,
    terminal_id TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    token_json TEXT NOT NULL,
    updated_at REAL NOT NULL
);
"""


class TuyaStore:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.executescript(SCHEMA)

    def save(self, creds: TuyaCredentials) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO tuya_account "
            "(id, user_code, terminal_id, endpoint, token_json, updated_at) VALUES (1, ?, ?, ?, ?, ?)",
            (creds.user_code, creds.terminal_id, creds.endpoint,
             json.dumps(creds.token_info), time.time()))

    def load(self) -> TuyaCredentials | None:
        row = self._db.execute(
            "SELECT user_code, terminal_id, endpoint, token_json FROM tuya_account WHERE id = 1").fetchone()
        if row is None:
            return None
        user_code, terminal_id, endpoint, token_json = row
        return TuyaCredentials(user_code, terminal_id, endpoint, json.loads(token_json))

    def update_token(self, token_info: dict) -> None:
        self._db.execute("UPDATE tuya_account SET token_json = ?, updated_at = ? WHERE id = 1",
                         (json.dumps(token_info), time.time()))

    def clear(self) -> None:
        self._db.execute("DELETE FROM tuya_account")
```

Run: `.venv/Scripts/python -m pytest tests/test_tuya_store.py -v` → PASS.

- [ ] **Step 4: Падающие тесты входа и QR**

`tests/test_tuya_login.py`:
```python
import pytest

from streemcam.tuya.login import CLIENT_ID, SCHEMA, QrSession, TuyaLogin
from streemcam.tuya.models import TuyaCredentials, TuyaLoginError
from streemcam.tuya.qr import qr_png

INFO = {"t": 1700000000000, "uid": "eu1", "expire_time": 7200, "access_token": "at",
        "refresh_token": "rt", "terminal_id": "term-1", "endpoint": "https://apigw.tuyaeu.com",
        "username": "someone"}


class FakeControl:
    def __init__(self, qr_response, results):
        self.qr_response = qr_response
        self.results = list(results)
        self.calls = []

    def qr_code(self, client_id, schema, user_code):
        self.calls.append(("qr_code", client_id, schema, user_code))
        return self.qr_response

    def login_result(self, token, client_id, user_code):
        self.calls.append(("login_result", token, client_id, user_code))
        return self.results.pop(0)


def test_start_returns_session_with_payload():
    control = FakeControl({"success": True, "result": {"qrcode": "QR123"}}, [])
    session = TuyaLogin(control).start("UC1")
    assert session == QrSession("UC1", "QR123")
    assert session.qr_payload == "tuyaSmart--qrLogin?token=QR123"
    assert control.calls == [("qr_code", CLIENT_ID, SCHEMA, "UC1")]


def test_start_failure_raises():
    control = FakeControl({"success": False, "msg": "user code invalid"}, [])
    with pytest.raises(TuyaLoginError, match="user code invalid"):
        TuyaLogin(control).start("BAD")


def test_poll_pending_then_success():
    control = FakeControl({}, [(False, {}), (True, INFO)])
    login = TuyaLogin(control)
    session = QrSession("UC1", "QR123")
    assert login.poll(session) is None
    creds = login.poll(session)
    assert creds == TuyaCredentials(
        "UC1", "term-1", "https://apigw.tuyaeu.com",
        {"t": 1700000000000, "uid": "eu1", "expire_time": 7200,
         "access_token": "at", "refresh_token": "rt"})
    assert control.calls[-1] == ("login_result", "QR123", CLIENT_ID, "UC1")


def test_constants():
    assert CLIENT_ID == "HA_3y9q4ak7g4ephrvke"
    assert SCHEMA == "haauthorize"


def test_qr_png_is_png():
    data = qr_png("tuyaSmart--qrLogin?token=QR123")
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(data) > 100
```

Run: `.venv/Scripts/python -m pytest tests/test_tuya_login.py -v` → ImportError.

- [ ] **Step 5: Реализация login и qr**

`streemcam/tuya/login.py`:
```python
from dataclasses import dataclass

from .models import TuyaCredentials, TuyaLoginError

CLIENT_ID = "HA_3y9q4ak7g4ephrvke"  # client_id интеграции Tuya в Home Assistant (см. спеку, «Риски»)
SCHEMA = "haauthorize"
TOKEN_KEYS = ("t", "uid", "expire_time", "access_token", "refresh_token")


@dataclass(frozen=True)
class QrSession:
    user_code: str
    token: str

    @property
    def qr_payload(self) -> str:
        return f"tuyaSmart--qrLogin?token={self.token}"


class TuyaLogin:
    """Синхронная обёртка над LoginControl; вызывать через asyncio.to_thread."""

    def __init__(self, control=None):
        if control is None:
            from tuya_sharing import LoginControl
            control = LoginControl()
        self._control = control

    def start(self, user_code: str) -> QrSession:
        response = self._control.qr_code(CLIENT_ID, SCHEMA, user_code)
        if not response.get("success"):
            raise TuyaLoginError(response.get("msg") or "Tuya QR request failed")
        return QrSession(user_code, response["result"]["qrcode"])

    def poll(self, session: QrSession) -> TuyaCredentials | None:
        ok, info = self._control.login_result(session.token, CLIENT_ID, session.user_code)
        if not ok:
            return None
        return TuyaCredentials(
            user_code=session.user_code,
            terminal_id=info["terminal_id"],
            endpoint=info["endpoint"],
            token_info={k: info[k] for k in TOKEN_KEYS if k in info},
        )
```

`streemcam/tuya/qr.py`:
```python
import io

import segno


def qr_png(payload: str) -> bytes:
    buf = io.BytesIO()
    segno.make(payload, error="m").save(buf, kind="png", scale=8, border=2)
    return buf.getvalue()
```

- [ ] **Step 6: Тесты проходят**

Run: `.venv/Scripts/python -m pytest tests/test_tuya_store.py tests/test_tuya_login.py -v` → PASS; затем `.venv/Scripts/python -m pytest -q` → всё PASS.

- [ ] **Step 7: Commit**

```bash
git add streemcam/tuya tests/test_tuya_store.py tests/test_tuya_login.py
git commit -m "feat: Tuya QR login, credential store and QR image"
```

---

### Task 3: Tuya — клиент облака

**Files:**
- Create: `streemcam/tuya/client.py`
- Test: `tests/test_tuya_client.py`

**Interfaces:**
- Consumes: `TuyaCredentials`, `TuyaCamera`, `TuyaError` (Task 2), `TuyaStore.update_token` (Task 2), `CLIENT_ID` (Task 2).
- Produces: `TuyaClient(manager)`; `TuyaClient.from_credentials(creds, store, manager_factory=None) -> TuyaClient`; `list_cameras() -> list[TuyaCamera]`; `allocate_rtsp(device_id: str) -> str` (обе синхронные, бросают `TuyaError`). `CAMERA_CATEGORY = "sp"`.
- Сигнатура SDK: `Manager(client_id, user_code, terminal_id, end_point, token_response, listener)`; `manager.update_device_cache()`; `manager.device_map: dict[str, CustomerDevice]` (поля `id`, `name`, `category`, `online`); `manager.get_device_stream_allocate(device_id, "rtsp") -> str | None`; SDK при ошибке API бросает `Exception("(code) msg")`. `SharingTokenListener.update_token(self, token_info: dict)`.

- [ ] **Step 1: Падающие тесты**

`tests/test_tuya_client.py`:
```python
from types import SimpleNamespace

import pytest

from streemcam.tuya.client import CAMERA_CATEGORY, TuyaClient
from streemcam.tuya.login import CLIENT_ID
from streemcam.tuya.models import TuyaCamera, TuyaCredentials, TuyaError
from streemcam.tuya.store import TuyaStore

CREDS = TuyaCredentials("UC1", "term-1", "https://apigw.tuyaeu.com", {"uid": "u1", "access_token": "a1"})


class FakeManager:
    def __init__(self, *args):
        self.args = args
        self.device_map = {}
        self.fail = None
        self.url = "rtsps://tuya.example/stream?sig=1"

    def update_device_cache(self):
        if self.fail:
            raise Exception(self.fail)
        self.device_map = {
            "bf1": SimpleNamespace(id="bf1", name="Прихожая", category="sp", online=True),
            "bf2": SimpleNamespace(id="bf2", name="Розетка", category="cz", online=True),
            "bf3": SimpleNamespace(id="bf3", name="Гараж", category="sp", online=False),
        }

    def get_device_stream_allocate(self, device_id, stream_type):
        if self.fail:
            raise Exception(self.fail)
        assert stream_type == "rtsp"
        return self.url


def test_from_credentials_builds_manager_with_listener():
    store = TuyaStore(":memory:")
    store.save(CREDS)
    client = TuyaClient.from_credentials(CREDS, store, manager_factory=FakeManager)
    args = client._manager.args
    assert args[:5] == (CLIENT_ID, "UC1", "term-1", "https://apigw.tuyaeu.com", CREDS.token_info)
    listener = args[5]
    listener.update_token({"uid": "u1", "access_token": "a2"})
    assert store.load().token_info["access_token"] == "a2"


def test_list_cameras_filters_category():
    assert CAMERA_CATEGORY == "sp"
    client = TuyaClient(FakeManager())
    assert client.list_cameras() == [TuyaCamera("bf1", "Прихожая", True), TuyaCamera("bf3", "Гараж", False)]


def test_list_cameras_error():
    m = FakeManager()
    m.fail = "(1010) token invalid"
    with pytest.raises(TuyaError, match="1010"):
        TuyaClient(m).list_cameras()


def test_allocate_rtsp():
    assert TuyaClient(FakeManager()).allocate_rtsp("bf1") == "rtsps://tuya.example/stream?sig=1"


def test_allocate_rtsp_none_or_error():
    m = FakeManager()
    m.url = None
    with pytest.raises(TuyaError):
        TuyaClient(m).allocate_rtsp("bf1")
    m.fail = "(500) boom"
    with pytest.raises(TuyaError, match="boom"):
        TuyaClient(m).allocate_rtsp("bf1")
```

Run: `.venv/Scripts/python -m pytest tests/test_tuya_client.py -v` → ImportError.

- [ ] **Step 2: Реализация**

`streemcam/tuya/client.py`:
```python
from tuya_sharing import Manager, SharingTokenListener

from .login import CLIENT_ID
from .models import TuyaCamera, TuyaCredentials, TuyaError
from .store import TuyaStore

CAMERA_CATEGORY = "sp"


class _TokenSaver(SharingTokenListener):
    def __init__(self, store: TuyaStore):
        self._store = store

    def update_token(self, token_info: dict) -> None:
        self._store.update_token(token_info)


class TuyaClient:
    """Синхронный клиент облака Tuya; вызывать через asyncio.to_thread."""

    def __init__(self, manager):
        self._manager = manager

    @classmethod
    def from_credentials(cls, creds: TuyaCredentials, store: TuyaStore, manager_factory=None) -> "TuyaClient":
        factory = manager_factory or Manager
        manager = factory(CLIENT_ID, creds.user_code, creds.terminal_id, creds.endpoint,
                          creds.token_info, _TokenSaver(store))
        return cls(manager)

    def list_cameras(self) -> list[TuyaCamera]:
        try:
            self._manager.update_device_cache()
        except Exception as e:
            raise TuyaError(str(e)) from e
        return [TuyaCamera(d.id, d.name, bool(d.online))
                for d in self._manager.device_map.values() if d.category == CAMERA_CATEGORY]

    def allocate_rtsp(self, device_id: str) -> str:
        try:
            url = self._manager.get_device_stream_allocate(device_id, "rtsp")
        except Exception as e:
            raise TuyaError(str(e)) from e
        if not url:
            raise TuyaError(f"no stream url for {device_id}")
        return url
```

- [ ] **Step 3: Тесты проходят**

Run: `.venv/Scripts/python -m pytest tests/test_tuya_client.py -v` → PASS; `.venv/Scripts/python -m pytest -q` → всё PASS.

- [ ] **Step 4: Commit**

```bash
git add streemcam/tuya/client.py tests/test_tuya_client.py
git commit -m "feat: Tuya cloud client (camera list, RTSP allocation, token persistence)"
```

---

### Task 4: Каталог камер и перевод потребителей на него

**Files:**
- Create: `streemcam/catalog.py`
- Modify: `streemcam/web/server.py`, `streemcam/monitor.py`, `streemcam/dc/bot.py`, `streemcam/app.py`
- Modify tests: `tests/conftest.py`, `tests/test_web_api.py`, `tests/test_monitor.py`, `tests/test_dc_bot.py`, `tests/test_app.py`
- Test: `tests/test_catalog.py`

**Interfaces:**
- Consumes: `Config`, `TuyaCamera` (Task 2).
- Produces:
  - `tuya_cam_id(device_id: str) -> str` → `"tuya_" + device_id.lower()`
  - `CameraInfo(id: str, name: str, kind: Literal["config","tuya"], device_id: str | None = None, online: bool | None = None)` (frozen dataclass)
  - `Catalog(cfg)`: `all() -> list[CameraInfo]`, `get(cam_id) -> CameraInfo | None`, `tuya() -> list[CameraInfo]`, `set_tuya(cameras: list[TuyaCamera]) -> None`
  - `create_app(cfg, catalog, access, monitor, registry, http, upstream_connect=None)` — новый второй параметр `catalog`; `/api/cameras` элементы `{"id","name","kind","online"}`.
  - `CameraMonitor(cfg, catalog, http, on_alert=None, clock=time.monotonic)`.
  - `cams_links(cfg, catalog, access, ident)`, `DcHandlers(cfg, catalog, access)`, `DcBot(cfg, catalog, access)`, `run_discord(cfg, catalog, access)`.
  - `alert_text(catalog, cfg, cam_id, online)`.
  - Фикстура `make_web(cfg, upstream_connect=fake_connect)` возвращает `SimpleNamespace(app, access, registry, catalog)`.

- [ ] **Step 1: Падающие тесты каталога**

`tests/test_catalog.py`:
```python
from streemcam.catalog import CameraInfo, Catalog, tuya_cam_id
from streemcam.tuya.models import TuyaCamera


def test_config_cameras(cfg):
    c = Catalog(cfg)
    assert [x.id for x in c.all()] == ["yard", "gate", "room"]
    assert c.get("yard") == CameraInfo("yard", "Двор", "config")
    assert c.get("nope") is None
    assert c.tuya() == []


def test_tuya_cameras_merged_sorted_by_name(cfg):
    c = Catalog(cfg)
    c.set_tuya([TuyaCamera("BF2zz", "Прихожая", True), TuyaCamera("bf1", "Гараж", False)])
    assert [x.id for x in c.all()] == ["yard", "gate", "room", "tuya_bf1", "tuya_bf2zz"]
    cam = c.get("tuya_bf2zz")
    assert cam == CameraInfo("tuya_bf2zz", "Прихожая", "tuya", device_id="BF2zz", online=True)
    assert [x.device_id for x in c.tuya()] == ["bf1", "BF2zz"]


def test_set_tuya_replaces_and_skips_unsafe_ids(cfg):
    c = Catalog(cfg)
    c.set_tuya([TuyaCamera("bf1", "A", True)])
    c.set_tuya([TuyaCamera("bad id;rm", "Evil", True), TuyaCamera("bf9", "B", True)])
    assert [x.id for x in c.tuya()] == ["tuya_bf9"]


def test_tuya_cam_id():
    assert tuya_cam_id("BfAbC123") == "tuya_bfabc123"
```

Run: `.venv/Scripts/python -m pytest tests/test_catalog.py -v` → ImportError.

- [ ] **Step 2: Реализация каталога**

`streemcam/catalog.py`:
```python
import logging
import re
from dataclasses import dataclass
from typing import Literal

from .config import Config
from .tuya.models import TuyaCamera

log = logging.getLogger(__name__)

TUYA_DEVICE_ID = re.compile(r"^[A-Za-z0-9]+$")


def tuya_cam_id(device_id: str) -> str:
    return "tuya_" + device_id.lower()


@dataclass(frozen=True)
class CameraInfo:
    id: str
    name: str
    kind: Literal["config", "tuya"]
    device_id: str | None = None
    online: bool | None = None


class Catalog:
    """Единый список камер: из config.yaml (статично) + камеры Tuya (обновляются в рантайме)."""

    def __init__(self, cfg: Config):
        self._config = [CameraInfo(c.id, c.name, "config") for c in cfg.cameras]
        self._tuya: list[CameraInfo] = []

    def all(self) -> list[CameraInfo]:
        return self._config + self._tuya

    def get(self, cam_id: str) -> CameraInfo | None:
        return next((c for c in self.all() if c.id == cam_id), None)

    def tuya(self) -> list[CameraInfo]:
        return list(self._tuya)

    def set_tuya(self, cameras: list[TuyaCamera]) -> None:
        infos = []
        for cam in cameras:
            if not TUYA_DEVICE_ID.match(cam.device_id):
                log.warning("skipping Tuya device with unsafe id %r", cam.device_id)
                continue
            infos.append(CameraInfo(tuya_cam_id(cam.device_id), cam.name, "tuya",
                                    device_id=cam.device_id, online=cam.online))
        self._tuya = sorted(infos, key=lambda c: c.name)
```

Run: `.venv/Scripts/python -m pytest tests/test_catalog.py -v` → PASS.

- [ ] **Step 3: Перевести web/server.py на каталог**

В `streemcam/web/server.py`:
- Добавить импорт: `from ..catalog import Catalog`.
- Сигнатура: `def create_app(cfg: Config, catalog: Catalog, access: Access, monitor, registry: StreamRegistry, http: httpx.AsyncClient, upstream_connect=None) -> FastAPI:`
- В `cameras()` заменить генератор на:
```python
            "cameras": [{"id": c.id, "name": c.name, "kind": c.kind, "online": monitor.is_online(c.id)}
                        for c in catalog.all()],
```
- В `snapshot()` заменить `cfg.camera(cam_id)` на `catalog.get(cam_id)`.
- В `ws_proxy()` заменить `if cfg.camera(src) is None:` на `if catalog.get(src) is None:`.

- [ ] **Step 4: Перевести monitor на каталог (поведение прежнее)**

В `streemcam/monitor.py`:
- Импорт: `from .catalog import Catalog`.
- `__init__(self, cfg: Config, catalog: Catalog, http: httpx.AsyncClient, on_alert: AlertFn | None = None, clock=time.monotonic)`; сохранить `self.catalog = catalog`; `self._state: dict[str, _State] = {}`.
- Добавить метод:
```python
    def _st(self, cam_id: str) -> _State:
        return self._state.setdefault(cam_id, _State())
```
- В `probe()` заменить `state = self._state[cam_id]` на `state = self._st(cam_id)`.
- В `probe_all()` итерировать `self.catalog.all()`: `await asyncio.gather(*(self.probe(cam.id) for cam in self.catalog.all()))`.

- [ ] **Step 5: Перевести dc/bot.py на каталог**

В `streemcam/dc/bot.py`:
- Импорт: `from ..catalog import Catalog`.
- `cams_links(cfg: Config, catalog: Catalog, access: Access, ident: Identity)`: внутри заменить `cfg.cameras` на `cameras = catalog.all()` (взять один раз в начале функции) и использовать `cameras` в проверке длины и генераторе.
- `DcHandlers.__init__(self, cfg, catalog, access)`: сохранить `self.catalog = catalog`; в `cams()` вызывать `cams_links(self.cfg, self.catalog, self.access, ident)`.
- `DcBot.__init__(self, cfg, catalog, access)`: `h = DcHandlers(cfg, catalog, access)`.
- `run_discord(cfg, catalog, access)`: `client = DcBot(cfg, catalog, access)`.

- [ ] **Step 6: alert_text и app.run на каталог**

В `streemcam/app.py`:
- Импорт: `from .catalog import Catalog`.
- Заменить `alert_text`:
```python
def alert_text(catalog: Catalog, cfg: Config, cam_id: str, online: bool) -> str:
    cam = catalog.get(cam_id)
    name = cam.name if cam else cam_id
    if online:
        return f"✅ Камера «{name}» снова онлайн."
    return f"⚠️ Камера «{name}» офлайн больше {cfg.offline_alert_minutes} мин."
```
- В `run()`: после `access = build_access(cfg)` добавить `catalog = Catalog(cfg)`; в `on_alert` — `alert_text(catalog, cfg, cam_id, online)`; `monitor = CameraMonitor(cfg, catalog, http, on_alert)`; `app = create_app(cfg, catalog, access, monitor, access.registry, http)`; Discord: `lambda: run_discord(cfg, catalog, access)`.

- [ ] **Step 7: Обновить существующие тесты под новые сигнатуры**

- `tests/conftest.py`: импорт `from streemcam.catalog import Catalog`; в `make_web.make`:
```python
        catalog = Catalog(cfg)
        app = create_app(cfg, catalog, access, StubMonitor(), registry, http, upstream_connect=upstream_connect)
        return SimpleNamespace(app=app, access=access, registry=registry, catalog=catalog)
```
- `tests/test_web_api.py::test_tg_session_and_cameras`: ожидаемые элементы дополнить `"kind": "config"`:
```python
    assert body["cameras"] == [
        {"id": "yard", "name": "Двор", "kind": "config", "online": True},
        {"id": "gate", "name": "Ворота", "kind": "config", "online": False},
        {"id": "room", "name": "Комната", "kind": "config", "online": None},
    ]
```
- `tests/test_monitor.py`: импорт `from streemcam.catalog import Catalog`; в функции `make` — `return CameraMonitor(cfg, Catalog(cfg), client(up), on_alert, clock=clock)`.
- `tests/test_dc_bot.py`: импорт `from streemcam.catalog import Catalog`; фикстура `h` → `DcHandlers(cfg, Catalog(cfg), access)`; в `test_cams_links` → `cams_links(cfg, Catalog(cfg), access, USER)`; в `test_cams_links_many_cameras` → `cams_links(cfg, Catalog(cfg), access, USER)`.
- `tests/test_app.py`: импорт `from streemcam.catalog import Catalog`; `test_alert_text`:
```python
def test_alert_text(cfg):
    catalog = Catalog(cfg)
    assert alert_text(catalog, cfg, "yard", False) == "⚠️ Камера «Двор» офлайн больше 5 мин."
    assert alert_text(catalog, cfg, "yard", True) == "✅ Камера «Двор» снова онлайн."
```

Добавить в `tests/test_web_api.py` тест, что камеры Tuya из каталога видны и доступны для снимка/404:
```python
def test_cameras_include_tuya(web):
    from streemcam.tuya.models import TuyaCamera
    web.catalog.set_tuya([TuyaCamera("bf1", "Прихожая", True)])
    client = TestClient(web.app)
    h = bearer(web.access.issue_session(USER))
    cams = client.get("/api/cameras", headers=h).json()["cameras"]
    assert cams[-1] == {"id": "tuya_bf1", "name": "Прихожая", "kind": "tuya", "online": None}
    assert client.get("/api/snapshot/tuya_bf1", headers=h).status_code == 404  # снимка ещё нет
```
И в `tests/test_monitor.py` тест, что монитор подхватывает камеры, добавленные в каталог позже:
```python
async def test_probe_all_follows_catalog(cfg, alerts):
    from streemcam.catalog import Catalog
    from streemcam.tuya.models import TuyaCamera
    catalog = Catalog(cfg)
    m = CameraMonitor(cfg, catalog, client({"tuya_bf1": True}), None, clock=Clock())
    catalog.set_tuya([TuyaCamera("bf1", "Прихожая", True)])
    await m.probe_all()
    assert m.snapshot("tuya_bf1") == b"JPEG-tuya_bf1"
```
(Task 9 изменит поведение для камер Tuya и обновит этот тест.)

- [ ] **Step 8: Все тесты проходят**

Run: `.venv/Scripts/python -m pytest -q` → всё PASS, 0 warnings.

- [ ] **Step 9: Commit**

```bash
git add streemcam/catalog.py streemcam/web/server.py streemcam/monitor.py streemcam/dc/bot.py streemcam/app.py tests
git commit -m "feat: dynamic camera catalog used by web, monitor and bots"
```

---

### Task 5: Синхронизация потоков с go2rtc

**Files:**
- Create: `streemcam/go2rtc_sync.py`
- Test: `tests/test_go2rtc_sync.py`

**Interfaces:**
- Consumes: `Catalog`, `CameraInfo` (Task 4).
- Produces: `COMPAT_SUFFIX = "~h264"`; `compat_name(cam_id) -> str`; `compat_src(cam_id) -> str`; `tuya_src(internal_url, device_id, key) -> str`; `desired_streams(catalog, internal_url, key) -> dict[str, str]`; `Go2rtcSync(http, catalog, internal_url, internal_key)` с `async sync() -> None`.
- API go2rtc: `GET /api/streams` → `{name: {"producers": [{"url": src, ...}], ...}}`; `PUT /api/streams?name=&src=` создаёт/заменяет поток; `DELETE /api/streams?src=<name>` удаляет. Существующий поток с тем же источником не трогаем (замена рвёт текущих зрителей).

- [ ] **Step 1: Падающие тесты**

`tests/test_go2rtc_sync.py`:
```python
import httpx

from streemcam.catalog import Catalog
from streemcam.go2rtc_sync import (COMPAT_SUFFIX, Go2rtcSync, compat_name, compat_src,
                                   desired_streams, tuya_src)
from streemcam.tuya.models import TuyaCamera

KEY = "k" * 20
URL = "http://streemcam:8081"


def test_helpers():
    assert COMPAT_SUFFIX == "~h264"
    assert compat_name("room") == "room~h264"
    assert compat_src("room") == "ffmpeg:room#video=h264#width=1280#audio=aac"
    assert tuya_src(URL, "bf1", KEY) == f"echo:curl -fsS {URL}/tuya/bf1?key={KEY}"


def test_desired_streams(cfg):
    catalog = Catalog(cfg)
    catalog.set_tuya([TuyaCamera("bf1", "Прихожая", True)])
    d = desired_streams(catalog, URL, KEY)
    assert d["tuya_bf1"] == tuya_src(URL, "bf1", KEY)
    assert d["tuya_bf1~h264"] == compat_src("tuya_bf1")
    assert d["yard~h264"] == compat_src("yard")
    assert "yard" not in d  # потоки из конфига рендерит go2rtc-config
    assert len(d) == 5


def test_desired_streams_without_key_skips_tuya(cfg):
    catalog = Catalog(cfg)
    catalog.set_tuya([TuyaCamera("bf1", "Прихожая", True)])
    d = desired_streams(catalog, URL, None)
    assert "tuya_bf1" not in d and "tuya_bf1~h264" not in d


def recorder(current):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, dict(request.url.params)))
        if request.method == "GET":
            return httpx.Response(200, json=current)
        return httpx.Response(200)
    return calls, httpx.AsyncClient(base_url="http://go2rtc", transport=httpx.MockTransport(handler))


async def test_sync_puts_missing_and_deletes_stale(cfg):
    catalog = Catalog(cfg)
    catalog.set_tuya([TuyaCamera("bf1", "Прихожая", True)])
    current = {
        "yard": {"producers": [{"url": "rtsp://x"}]},
        "yard~h264": {"producers": [{"url": compat_src("yard")}]},   # уже верный — не трогаем
        "tuya_old": {"producers": [{"url": "echo:old"}]},             # устарел — удалить
        "gate~h264": {"producers": [{"url": "ffmpeg:gate#old"}]},     # другой src — заменить
        "manual": {"producers": []},                                   # чужой — не трогаем
    }
    calls, http = recorder(current)
    await Go2rtcSync(http, catalog, URL, KEY).sync()
    puts = {p["name"]: p["src"] for m, p in calls if m == "PUT"}
    deletes = [p["src"] for m, p in calls if m == "DELETE"]
    assert "yard~h264" not in puts
    assert puts["gate~h264"] == compat_src("gate")
    assert puts["room~h264"] == compat_src("room")
    assert puts["tuya_bf1"] == tuya_src(URL, "bf1", KEY)
    assert puts["tuya_bf1~h264"] == compat_src("tuya_bf1")
    assert deletes == ["tuya_old"]


async def test_sync_survives_go2rtc_down(cfg):
    def handler(request):
        raise httpx.ConnectError("down")
    http = httpx.AsyncClient(base_url="http://go2rtc", transport=httpx.MockTransport(handler))
    await Go2rtcSync(http, Catalog(cfg), URL, KEY).sync()  # не бросает
```

Run: `.venv/Scripts/python -m pytest tests/test_go2rtc_sync.py -v` → ImportError.

- [ ] **Step 2: Реализация**

`streemcam/go2rtc_sync.py`:
```python
import logging

import httpx

from .catalog import Catalog

log = logging.getLogger(__name__)

COMPAT_SUFFIX = "~h264"


def compat_name(cam_id: str) -> str:
    return cam_id + COMPAT_SUFFIX


def compat_src(cam_id: str) -> str:
    return f"ffmpeg:{cam_id}#video=h264#width=1280#audio=aac"


def tuya_src(internal_url: str, device_id: str, key: str) -> str:
    # device_id проверен Catalog.set_tuya (^[A-Za-z0-9]+$), key — Config (^[A-Za-z0-9_-]{16,}$)
    return f"echo:curl -fsS {internal_url}/tuya/{device_id}?key={key}"


def desired_streams(catalog: Catalog, internal_url: str, key: str | None) -> dict[str, str]:
    streams: dict[str, str] = {}
    for cam in catalog.all():
        if cam.kind == "tuya":
            if not key:
                continue
            streams[cam.id] = tuya_src(internal_url, cam.device_id, key)
        streams[compat_name(cam.id)] = compat_src(cam.id)
    return streams


def _managed(name: str) -> bool:
    return name.startswith("tuya_") or name.endswith(COMPAT_SUFFIX)


def _current_src(info) -> str | None:
    producers = (info or {}).get("producers") or []
    if producers and isinstance(producers[0], dict):
        return producers[0].get("url")
    return None


class Go2rtcSync:
    def __init__(self, http: httpx.AsyncClient, catalog: Catalog, internal_url: str, internal_key: str | None):
        self._http = http
        self._catalog = catalog
        self._internal_url = internal_url
        self._key = internal_key

    async def sync(self) -> None:
        desired = desired_streams(self._catalog, self._internal_url, self._key)
        try:
            r = await self._http.get("/api/streams")
            current = r.json() if r.status_code == 200 else {}
        except (httpx.HTTPError, ValueError) as e:
            log.warning("go2rtc sync skipped: %s", e)
            return
        for name, src in desired.items():
            if _current_src(current.get(name)) == src:
                continue
            try:
                await self._http.put("/api/streams", params={"name": name, "src": src})
            except httpx.HTTPError as e:
                log.warning("go2rtc: cannot register %s: %s", name, e)
        for name in current:
            if _managed(name) and name not in desired:
                try:
                    await self._http.delete("/api/streams", params={"src": name})
                except httpx.HTTPError as e:
                    log.warning("go2rtc: cannot delete %s: %s", name, e)
```

- [ ] **Step 3: Тесты проходят**

Run: `.venv/Scripts/python -m pytest tests/test_go2rtc_sync.py -v` → PASS; `.venv/Scripts/python -m pytest -q` → всё PASS.

- [ ] **Step 4: Commit**

```bash
git add streemcam/go2rtc_sync.py tests/test_go2rtc_sync.py
git commit -m "feat: register Tuya and compatibility streams in go2rtc"
```

---

### Task 6: TuyaService

**Files:**
- Create: `streemcam/tuya/service.py`
- Test: `tests/test_tuya_service.py`

**Interfaces:**
- Consumes: `Config` (`tuya.refresh_minutes`), `Catalog` (Task 4), `TuyaStore`, `TuyaLogin`, `QrSession`, `TuyaCredentials`, `TuyaError` (Task 2), `TuyaClient.from_credentials` (Task 3), `Go2rtcSync.sync()` (Task 5).
- Produces: `TuyaService(cfg, catalog, store, sync=None, on_alert=None, login=None, client_factory=None)`:
  - `logged_in: bool` (property)
  - `load() -> None` — поднимает клиента из store, если есть данные
  - `async start_login(user_code) -> QrSession` (отменяет предыдущую попытку)
  - `async wait_login(session, timeout=120.0, interval=2.0) -> bool`
  - `async refresh() -> bool`
  - `async logout() -> None`
  - `async stream_url(device_id) -> str` (бросает `TuyaError`)
  - `cameras() -> list[CameraInfo]`
  - `async run() -> None` (refresh, затем sleep `refresh_minutes*60`, бесконечно)
  - `FAILURE_ALERT_AFTER = 3`; `on_alert(text: str)` — корутина.
  - `client_factory(creds, store) -> TuyaClient` (по умолчанию `TuyaClient.from_credentials`).

- [ ] **Step 1: Падающие тесты**

`tests/test_tuya_service.py`:
```python
import pytest

from streemcam.catalog import Catalog
from streemcam.tuya.login import QrSession
from streemcam.tuya.models import TuyaCamera, TuyaCredentials, TuyaError
from streemcam.tuya.service import FAILURE_ALERT_AFTER, TuyaService
from streemcam.tuya.store import TuyaStore

CREDS = TuyaCredentials("UC1", "term", "https://x", {"uid": "u1"})


class FakeClient:
    def __init__(self, cams=None):
        self.cams = cams if cams is not None else [TuyaCamera("bf1", "Прихожая", True)]
        self.fail = False

    def list_cameras(self):
        if self.fail:
            raise TuyaError("(1010) token invalid")
        return self.cams

    def allocate_rtsp(self, device_id):
        if self.fail:
            raise TuyaError("boom")
        return f"rtsps://tuya/{device_id}"


class FakeLogin:
    def __init__(self, polls):
        self.polls = list(polls)
        self.started = []

    def start(self, user_code):
        self.started.append(user_code)
        return QrSession(user_code, f"QR-{len(self.started)}")

    def poll(self, session):
        return self.polls.pop(0) if self.polls else None


class FakeSync:
    def __init__(self):
        self.count = 0

    async def sync(self):
        self.count += 1


@pytest.fixture
def env(cfg):
    client = FakeClient()
    alerts = []

    async def on_alert(text):
        alerts.append(text)

    def make(polls=()):
        store = TuyaStore(":memory:")
        sync = FakeSync()
        catalog = Catalog(cfg)
        svc = TuyaService(cfg, catalog, store, sync=sync, on_alert=on_alert,
                          login=FakeLogin(polls), client_factory=lambda creds, st: client)
        return svc, store, sync, catalog
    return make, client, alerts


async def test_not_logged_in(env):
    make, _, _ = env
    svc, _, sync, catalog = make()
    svc.load()
    assert svc.logged_in is False
    assert await svc.refresh() is False
    assert catalog.tuya() == [] and sync.count == 1
    with pytest.raises(TuyaError):
        await svc.stream_url("bf1")


async def test_login_success(env):
    make, _, _ = env
    svc, store, sync, catalog = make(polls=[None, CREDS])
    session = await svc.start_login("UC1")
    assert session.qr_payload == "tuyaSmart--qrLogin?token=QR-1"
    assert await svc.wait_login(session, timeout=1.0, interval=0) is True
    assert svc.logged_in and store.load() == CREDS
    assert [c.id for c in svc.cameras()] == ["tuya_bf1"]
    assert sync.count == 1
    assert await svc.stream_url("bf1") == "rtsps://tuya/bf1"


async def test_login_timeout(env):
    make, _, _ = env
    svc, store, _, _ = make(polls=[])
    session = await svc.start_login("UC1")
    assert await svc.wait_login(session, timeout=0.05, interval=0.01) is False
    assert not svc.logged_in and store.load() is None


async def test_new_login_cancels_previous(env):
    make, _, _ = env
    svc, _, _, _ = make(polls=[None, None, CREDS])
    first = await svc.start_login("UC1")
    await svc.start_login("UC1")
    assert await svc.wait_login(first, timeout=1.0, interval=0) is False


async def test_load_from_store(env):
    make, _, _ = env
    svc, store, _, _ = make()
    store.save(CREDS)
    svc.load()
    assert svc.logged_in
    assert await svc.refresh() is True


async def test_refresh_failures_alert_once(env):
    make, client, alerts = env
    svc, store, _, catalog = make()
    store.save(CREDS)
    svc.load()
    await svc.refresh()
    client.fail = True
    for _ in range(FAILURE_ALERT_AFTER + 2):
        assert await svc.refresh() is False
    assert len(alerts) == 1 and "/tuya_login" in alerts[0]
    assert [c.id for c in catalog.tuya()] == ["tuya_bf1"]  # последний известный список сохраняется
    client.fail = False
    assert await svc.refresh() is True
    client.fail = True
    for _ in range(FAILURE_ALERT_AFTER):
        await svc.refresh()
    assert len(alerts) == 2


async def test_logout(env):
    make, _, _ = env
    svc, store, sync, catalog = make()
    store.save(CREDS)
    svc.load()
    await svc.refresh()
    await svc.logout()
    assert not svc.logged_in and store.load() is None
    assert catalog.tuya() == [] and sync.count == 2
```

Run: `.venv/Scripts/python -m pytest tests/test_tuya_service.py -v` → ImportError.

- [ ] **Step 2: Реализация**

`streemcam/tuya/service.py`:
```python
import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from ..catalog import CameraInfo, Catalog
from ..config import Config
from .login import QrSession, TuyaLogin
from .models import TuyaError
from .store import TuyaStore

log = logging.getLogger(__name__)

FAILURE_ALERT_AFTER = 3
RELOGIN_TEXT = ("⚠️ Tuya: не удаётся получить список камер несколько раз подряд. "
                "Возможно, нужен повторный вход: /tuya_login <код пользователя>.")


class TuyaService:
    def __init__(self, cfg: Config, catalog: Catalog, store: TuyaStore, sync=None,
                 on_alert: Callable[[str], Awaitable[None]] | None = None,
                 login: TuyaLogin | None = None, client_factory=None):
        self.cfg = cfg
        self.catalog = catalog
        self._store = store
        self._sync = sync
        self._on_alert = on_alert
        self._login = login
        if client_factory is None:
            from .client import TuyaClient
            client_factory = TuyaClient.from_credentials
        self._client_factory = client_factory
        self._client = None
        self._current_session: QrSession | None = None
        self._failures = 0

    @property
    def logged_in(self) -> bool:
        return self._client is not None

    def load(self) -> None:
        creds = self._store.load()
        if creds is not None:
            self._client = self._client_factory(creds, self._store)

    def cameras(self) -> list[CameraInfo]:
        return self.catalog.tuya()

    async def start_login(self, user_code: str) -> QrSession:
        if self._login is None:
            self._login = TuyaLogin()
        session = await asyncio.to_thread(self._login.start, user_code)
        self._current_session = session
        return session

    async def wait_login(self, session: QrSession, timeout: float = 120.0, interval: float = 2.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._current_session is not session:
                return False  # начата новая попытка входа
            try:
                creds = await asyncio.to_thread(self._login.poll, session)
            except Exception as e:
                log.warning("tuya login poll failed: %s", e)
                creds = None
            if creds is not None:
                self._store.save(creds)
                self._client = self._client_factory(creds, self._store)
                self._current_session = None
                self._failures = 0
                await self.refresh()
                return True
            await asyncio.sleep(interval)
        return False

    async def refresh(self) -> bool:
        if self._client is None:
            self.catalog.set_tuya([])
            await self._run_sync()
            return False
        try:
            cams = await asyncio.to_thread(self._client.list_cameras)
        except TuyaError as e:
            self._failures += 1
            log.warning("tuya refresh failed (%d in a row): %s", self._failures, e)
            if self._failures == FAILURE_ALERT_AFTER and self._on_alert is not None:
                await self._on_alert(RELOGIN_TEXT)
            return False
        self._failures = 0
        self.catalog.set_tuya(cams)
        await self._run_sync()
        return True

    async def logout(self) -> None:
        self._store.clear()
        self._client = None
        self._failures = 0
        self.catalog.set_tuya([])
        await self._run_sync()

    async def stream_url(self, device_id: str) -> str:
        if self._client is None:
            raise TuyaError("not logged in to Tuya")
        return await asyncio.to_thread(self._client.allocate_rtsp, device_id)

    async def run(self) -> None:
        while True:
            await self.refresh()
            await asyncio.sleep(self.cfg.tuya.refresh_minutes * 60)

    async def _run_sync(self) -> None:
        if self._sync is not None:
            await self._sync.sync()
```

- [ ] **Step 3: Тесты проходят**

Run: `.venv/Scripts/python -m pytest tests/test_tuya_service.py -v` → PASS; `.venv/Scripts/python -m pytest -q` → всё PASS.

- [ ] **Step 4: Commit**

```bash
git add streemcam/tuya/service.py tests/test_tuya_service.py
git commit -m "feat: Tuya service (QR login flow, periodic refresh, stream URLs)"
```

---

### Task 7: Внутренний сервер выдачи ссылок

**Files:**
- Create: `streemcam/web/internal.py`
- Test: `tests/test_web_internal.py`

**Interfaces:**
- Consumes: `Catalog.tuya()` (Task 4), `TuyaService.logged_in`, `TuyaService.stream_url` (Task 6), `TuyaError`.
- Produces: `create_internal_app(catalog, tuya, internal_key: str | None) -> FastAPI` с `GET /tuya/{device_id}?key=` → 200 `text/plain` | 403 | 404 | 503 | 502.

- [ ] **Step 1: Падающие тесты**

`tests/test_web_internal.py`:
```python
from fastapi.testclient import TestClient

from streemcam.catalog import Catalog
from streemcam.tuya.models import TuyaCamera, TuyaError
from streemcam.web.internal import create_internal_app

KEY = "k" * 20


class FakeTuya:
    def __init__(self):
        self.logged_in = True
        self.fail = False

    async def stream_url(self, device_id):
        if self.fail:
            raise TuyaError("boom")
        return f"rtsps://tuya/{device_id}?sig=1"


def make(cfg, key=KEY):
    catalog = Catalog(cfg)
    catalog.set_tuya([TuyaCamera("bf1", "Прихожая", True)])
    tuya = FakeTuya()
    return TestClient(create_internal_app(catalog, tuya, key)), tuya


def test_ok(cfg):
    client, _ = make(cfg)
    r = client.get(f"/tuya/bf1?key={KEY}")
    assert r.status_code == 200
    assert r.text == "rtsps://tuya/bf1?sig=1"
    assert r.headers["content-type"].startswith("text/plain")


def test_bad_or_missing_key(cfg):
    client, _ = make(cfg)
    assert client.get("/tuya/bf1?key=wrong").status_code == 403
    assert client.get("/tuya/bf1").status_code == 403


def test_no_key_configured_rejects_everything(cfg):
    client, _ = make(cfg, key=None)
    assert client.get("/tuya/bf1?key=").status_code == 403


def test_unknown_device(cfg):
    client, _ = make(cfg)
    assert client.get(f"/tuya/zz9?key={KEY}").status_code == 404


def test_not_logged_in(cfg):
    client, tuya = make(cfg)
    tuya.logged_in = False
    assert client.get(f"/tuya/bf1?key={KEY}").status_code == 503


def test_cloud_error(cfg):
    client, tuya = make(cfg)
    tuya.fail = True
    assert client.get(f"/tuya/bf1?key={KEY}").status_code == 502
```

Run: `.venv/Scripts/python -m pytest tests/test_web_internal.py -v` → ImportError.

- [ ] **Step 2: Реализация**

`streemcam/web/internal.py`:
```python
import hmac
import logging

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

from ..catalog import Catalog
from ..tuya.models import TuyaError

log = logging.getLogger(__name__)


def create_internal_app(catalog: Catalog, tuya, internal_key: str | None) -> FastAPI:
    """Внутренний API только для go2rtc (docker-сеть). Выдаёт свежие RTSP-ссылки Tuya."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/tuya/{device_id}")
    async def tuya_stream(device_id: str, key: str = ""):
        if not internal_key or not hmac.compare_digest(key.encode(), internal_key.encode()):
            return PlainTextResponse("forbidden", status_code=403)
        if not any(c.device_id == device_id for c in catalog.tuya()):
            return PlainTextResponse("unknown device", status_code=404)
        if not tuya.logged_in:
            return PlainTextResponse("not logged in to Tuya", status_code=503)
        try:
            url = await tuya.stream_url(device_id)
        except TuyaError as e:
            log.warning("tuya stream url for %s failed: %s", device_id, e)
            return PlainTextResponse("tuya cloud error", status_code=502)
        return PlainTextResponse(url)

    return app
```

- [ ] **Step 3: Тесты проходят**

Run: `.venv/Scripts/python -m pytest tests/test_web_internal.py -v` → PASS; `.venv/Scripts/python -m pytest -q` → всё PASS.

- [ ] **Step 4: Commit**

```bash
git add streemcam/web/internal.py tests/test_web_internal.py
git commit -m "feat: internal endpoint serving fresh Tuya RTSP URLs to go2rtc"
```

---

### Task 8: Совместимый режим (прокси, лимит, плеер)

**Files:**
- Modify: `streemcam/streams.py`, `streemcam/web/server.py`, `streemcam/web/static/index.html`, `streemcam/web/static/app.js`, `streemcam/web/static/style.css`
- Test: `tests/test_streams.py`, `tests/test_web_ws.py`, `tests/test_web_static.py`

**Interfaces:**
- Consumes: `compat_name` (Task 5), `create_app(cfg, catalog, ...)` (Task 4).
- Produces: `TranscodeLimiter(maximum: int)` с `try_acquire() -> bool`, `release() -> None`, `active: int`; `WS /api/ws?src=&s=&compat=1` → upstream `<src>~h264`, при превышении `cfg.max_transcodes` — закрытие `4430`; `/api/cameras` дополнительно `"max_transcodes"`.

- [ ] **Step 1: Падающие тесты**

Дописать в `tests/test_streams.py`:
```python
from streemcam.streams import TranscodeLimiter


def test_transcode_limiter():
    t = TranscodeLimiter(2)
    assert t.try_acquire() and t.try_acquire()
    assert not t.try_acquire()
    assert t.active == 2
    t.release()
    assert t.try_acquire()
    t.release(); t.release(); t.release()  # лишний release не уходит в минус
    assert t.active == 0
```

Дописать в `tests/test_web_ws.py` (в файле уже есть `ws_url`, `_close_and_wait_released`, `USER`, импорты `pytest`, `TestClient`, `WebSocketDisconnect`):
```python
from contextlib import asynccontextmanager


def recording_connect(log):
    @asynccontextmanager
    async def connect(src):
        log.append(src)
        from conftest import FakeUpstream
        yield FakeUpstream(src)
    return connect


def test_compat_uses_h264_stream(make_web, cfg):
    connected = []
    web = make_web(cfg, upstream_connect=recording_connect(connected))
    with TestClient(web.app) as client:
        with client.websocket_connect(ws_url(web) + "&compat=1") as ws:
            ws.send_text("x")
            assert ws.receive_text() == "echo:x"
            _close_and_wait_released(client, web, ws)
    assert connected == ["yard~h264"]


def test_transcode_limit(make_web, make_cfg):
    connected = []
    web = make_web(make_cfg(max_transcodes=1), upstream_connect=recording_connect(connected))
    with TestClient(web.app) as client:
        with client.websocket_connect(ws_url(web) + "&compat=1") as ws:
            ws.send_text("x")
            ws.receive_text()
            with pytest.raises(WebSocketDisconnect) as e:
                with client.websocket_connect(ws_url(web, src="gate") + "&compat=1"):
                    pass
            assert e.value.code == 4430
            with client.websocket_connect(ws_url(web, src="gate")) as ws2:  # без compat — можно
                ws2.send_text("y")
                assert ws2.receive_text() == "echo:y"
                _close_and_wait_released(client, web, ws2, expected=1)
            _close_and_wait_released(client, web, ws)
```

Для этого у существующего хелпера `_close_and_wait_released(client, web, ws, ident=USER, timeout=2.0)` в `tests/test_web_ws.py` добавить параметр `expected: int = 0` и ждать `web.registry.count(ident) == expected` (вместо `!= 0` в условии цикла — `!= expected`); сообщение об ошибке таймаута поправить соответственно. Остальные вызовы хелпера не меняются.

Дописать в `tests/test_web_static.py`:
```python
def test_compat_toggle_present(web):
    client = TestClient(web.app)
    assert 'id="compat"' in client.get("/").text
    js = client.get("/static/app.js").text
    assert "compat=1" in js and "sc_compat" in js
```

И в `tests/test_web_api.py::test_tg_session_and_cameras` добавить `assert body["max_transcodes"] == 2`.

Run: `.venv/Scripts/python -m pytest tests/test_streams.py tests/test_web_ws.py tests/test_web_static.py tests/test_web_api.py -v` → новые FAIL.

- [ ] **Step 2: TranscodeLimiter**

Дописать в `streemcam/streams.py`:
```python
class TranscodeLimiter:
    """Глобальный лимит одновременных перекодировок (совместимый режим)."""

    def __init__(self, maximum: int):
        self._max = maximum
        self.active = 0

    def try_acquire(self) -> bool:
        if self.active >= self._max:
            return False
        self.active += 1
        return True

    def release(self) -> None:
        self.active = max(0, self.active - 1)
```

- [ ] **Step 3: Прокси**

В `streemcam/web/server.py`:
- Импорты: `from ..go2rtc_sync import compat_name`; расширить `from ..streams import StreamLimitError, StreamRegistry, TranscodeLimiter`.
- В `create_app` после `upstream_connect = ...`: `transcodes = TranscodeLimiter(cfg.max_transcodes)`.
- В ответ `/api/cameras` добавить `"max_transcodes": cfg.max_transcodes,`.
- Заменить сигнатуру и начало `ws_proxy` и блоки acquire/finally так, чтобы итоговый код был:
```python
    @app.websocket("/api/ws")
    async def ws_proxy(ws: WebSocket, src: str = "", s: str = "", compat: int = 0):
        try:
            ident = access.check_session(s)
        except TokenError:
            await ws.close(code=4401)
            return
        except AccessDenied:
            await ws.close(code=4403)
            return
        if catalog.get(src) is None:
            await ws.close(code=4404)
            return
        upstream_name = compat_name(src) if compat else src
        if compat and not transcodes.try_acquire():
            await ws.close(code=4430)
            return

        kicked = asyncio.Event()

        async def closer():
            kicked.set()

        try:
            registry.acquire(ident, closer)
        except StreamLimitError:
            if compat:
                transcodes.release()
            await ws.close(code=4429)
            return

        close_code = 1000
        try:
            await ws.accept()
            async with upstream_connect(upstream_name) as upstream:
                if await _pump(ws, upstream, kicked):
                    close_code = 1011
        except (WebSocketDisconnect, UpstreamError, OSError) as e:
            log.info("stream %s for %s ended: %r", upstream_name, ident, e)
            close_code = 1011
        finally:
            registry.release(ident, closer)
            if compat:
                transcodes.release()
        if kicked.is_set():
            close_code = 4403  # kick always takes precedence
        with contextlib.suppress(Exception):
            await ws.close(code=close_code)
```

- [ ] **Step 4: Плеер**

`streemcam/web/static/index.html` — в `<header>` перед `<button id="grid" hidden>Сетка</button>` вставить:
```html
    <label id="compat-wrap" title="Перекодировать видео в H.264 на сервере — для устройств без поддержки H.265">
      <input type="checkbox" id="compat"> Совместимый режим
    </label>
```
и в `<main>` первой строкой:
```html
    <p id="compat-hint" hidden>Совместимый режим перекодирует видео на сервере, одновременно доступно ограниченное число таких просмотров. Если видео не появляется — выключите режим или попробуйте позже.</p>
```

`streemcam/web/static/app.js`:
- После `let info = null;` добавить:
```js
let currentCams = [];
function loadCompat() {
  try { return localStorage.getItem("sc_compat") === "1"; } catch { return false; }
}
let compat = loadCompat();
```
- `streamUrl` заменить на:
```js
function streamUrl(id) {
  const extra = compat ? "&compat=1" : "";
  return `${location.origin}/api/ws?src=${encodeURIComponent(id)}&s=${encodeURIComponent(token)}${extra}`;
}
```
- В `openCameras(cams)` первой строкой после `stopPlayers();` добавить `currentCams = cams;`.
- После строки `$("#grid").onclick = ...` добавить:
```js
function applyCompatUi() {
  $("#compat").checked = compat;
  $("#compat-hint").hidden = !compat;
}
$("#compat").onchange = () => {
  compat = $("#compat").checked;
  try { localStorage.setItem("sc_compat", compat ? "1" : "0"); } catch {}
  applyCompatUi();
  if (!$("#viewer").hidden && currentCams.length) openCameras(currentCams);
};
applyCompatUi();
```

`streemcam/web/static/style.css` — дописать:
```css
#compat-wrap { display: flex; align-items: center; gap: 4px; font-size: 13px; color: var(--muted); white-space: nowrap; }
#compat-hint { color: var(--muted); font-size: 13px; margin: 0 0 8px; }
```

Run: `node --check streemcam/web/static/app.js` → без ошибок.

- [ ] **Step 5: Тесты проходят**

Run: `.venv/Scripts/python -m pytest -q` → всё PASS, 0 warnings. Затем 5 прогонов подряд `.venv/Scripts/python -m pytest tests/test_web_ws.py -q` — все зелёные.

- [ ] **Step 6: Commit**

```bash
git add streemcam/streams.py streemcam/web tests
git commit -m "feat: compatibility (H.264) mode with global transcode limit"
```

---

### Task 9: Монитор — камеры Tuya

**Files:**
- Modify: `streemcam/monitor.py`
- Test: `tests/test_monitor.py`

**Interfaces:**
- Consumes: `CameraInfo.kind/online` (Task 4), `Config.tuya.snapshot_minutes`.
- Produces: для `kind == "tuya"`: `is_online` = `CameraInfo.online` из облака; снимок `frame.jpeg` только если камера online и прошлый снимок старше `tuya.snapshot_minutes` минут (или его нет); алертов об офлайне для Tuya нет (ими занимается TuyaService). Камеры из конфига — без изменений.

- [ ] **Step 1: Падающие тесты**

В `tests/test_monitor.py` заменить `test_probe_all_follows_catalog` (из Task 4) на:
```python
async def test_tuya_status_from_cloud_and_rare_snapshots(cfg, alerts):
    from streemcam.catalog import Catalog
    from streemcam.tuya.models import TuyaCamera
    requested = []

    def handler(request):
        requested.append(request.url.params["src"])
        return httpx.Response(200, content=b"JPEG")
    http = httpx.AsyncClient(base_url="http://go2rtc", transport=httpx.MockTransport(handler))
    catalog = Catalog(cfg)
    clock = Clock()
    m = CameraMonitor(cfg, catalog, http, None, clock=clock)
    catalog.set_tuya([TuyaCamera("bf1", "Прихожая", True), TuyaCamera("bf2", "Гараж", False)])

    await m.probe_all()
    assert m.is_online("tuya_bf1") is True and m.is_online("tuya_bf2") is False
    assert requested.count("tuya_bf1") == 1 and "tuya_bf2" not in requested
    assert m.snapshot("tuya_bf1") == b"JPEG"

    clock.t = 14 * 60
    await m.probe_all()
    assert requested.count("tuya_bf1") == 1  # ещё рано
    clock.t = 15 * 60
    await m.probe_all()
    assert requested.count("tuya_bf1") == 2

    catalog.set_tuya([TuyaCamera("bf1", "Прихожая", False)])
    await m.probe_all()
    assert m.is_online("tuya_bf1") is False
```

Run: `.venv/Scripts/python -m pytest tests/test_monitor.py -v` → новый тест FAIL.

- [ ] **Step 2: Реализация**

В `streemcam/monitor.py`:
- В `_State` добавить поле `snapshot_at: float | None = None`.
- Импорт: `from .catalog import CameraInfo, Catalog`.
- Вынести HTTP-запрос кадра в метод и использовать его в `probe()`:
```python
    async def _fetch_frame(self, cam_id: str) -> bytes | None:
        try:
            r = await self._http.get("/api/frame.jpeg", params={"src": cam_id}, timeout=20)
            if r.status_code == 200 and r.content:
                return r.content
        except httpx.HTTPError as e:
            log.debug("probe %s failed: %s", cam_id, e)
        return None
```
  В `probe()` заменить блок `image = None ... except ...` на `image = await self._fetch_frame(cam_id)`.
- Добавить:
```python
    async def _check_tuya(self, cam: CameraInfo) -> None:
        state = self._st(cam.id)
        state.online = cam.online
        if not cam.online:
            return
        now = self._clock()
        if state.snapshot_at is not None and now - state.snapshot_at < self.cfg.tuya.snapshot_minutes * 60:
            return
        image = await self._fetch_frame(cam.id)
        if image is not None:
            state.snapshot = image
            state.snapshot_at = now

    async def _check(self, cam: CameraInfo) -> None:
        if cam.kind == "tuya":
            await self._check_tuya(cam)
        else:
            await self.probe(cam.id)
```
- `probe_all()`: `await asyncio.gather(*(self._check(cam) for cam in self.catalog.all()))`.

- [ ] **Step 3: Тесты проходят**

Run: `.venv/Scripts/python -m pytest tests/test_monitor.py -v` → PASS; `.venv/Scripts/python -m pytest -q` → всё PASS.

- [ ] **Step 4: Commit**

```bash
git add streemcam/monitor.py tests/test_monitor.py
git commit -m "feat: monitor uses Tuya cloud status and rare snapshots for Tuya cameras"
```

---

### Task 10: Telegram — команды Tuya

**Files:**
- Modify: `streemcam/tg/bot.py`
- Test: `tests/test_tg_tuya.py`

**Interfaces:**
- Consumes: `TuyaService` (Task 6: `start_login`, `wait_login`, `logged_in`, `cameras()`, `logout`), `TuyaLoginError` (Task 2), `qr_png` (Task 2).
- Produces: `TgHandlers(cfg, access, mini_app_link, tuya=None)` с методами `tuya_login(message, command)`, `tuya_status(message)`, `tuya_logout(message)`; зарегистрированы в `build_router` как `/tuya_login`, `/tuya_status`, `/tuya_logout`; `run_telegram(bot, cfg, access, tuya=None)`; `format_tuya_cameras(cams) -> str`; `TUYA_USAGE`.

- [ ] **Step 1: Падающие тесты**

`tests/test_tg_tuya.py`:
```python
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.filters import CommandObject

from streemcam.access import Access
from streemcam.catalog import CameraInfo
from streemcam.db import Store
from streemcam.streams import StreamRegistry
from streemcam.tg.bot import TUYA_USAGE, TgHandlers, format_tuya_cameras
from streemcam.tuya.login import QrSession
from streemcam.tuya.models import TuyaLoginError

CAMS = [CameraInfo("tuya_bf1", "Прихожая", "tuya", "bf1", True),
        CameraInfo("tuya_bf2", "Гараж", "tuya", "bf2", False)]


class FakeTuya:
    def __init__(self, ok=True, start_error=None):
        self.ok = ok
        self.start_error = start_error
        self.logged_in = False
        self.logged_out = False

    async def start_login(self, code):
        if self.start_error:
            raise TuyaLoginError(self.start_error)
        return QrSession(code, "QR1")

    async def wait_login(self, session):
        self.logged_in = self.ok
        return self.ok

    def cameras(self):
        return CAMS if self.logged_in else []

    async def logout(self):
        self.logged_out = True
        self.logged_in = False


def make_msg(user_id=1):
    msg = MagicMock()
    msg.from_user.id = user_id
    msg.answer = AsyncMock()
    msg.answer_photo = AsyncMock()
    return msg


def texts(msg):
    return [c.args[0] for c in msg.answer.call_args_list]


def cmd(args=None):
    return CommandObject(prefix="/", command="tuya_login", args=args)


@pytest.fixture
def access(cfg):
    return Access(cfg, Store(":memory:"), StreamRegistry(4))


def handlers(cfg, access, tuya):
    return TgHandlers(cfg, access, "https://t.me/bot/cams", tuya=tuya)


def test_format_tuya_cameras():
    text = format_tuya_cameras(CAMS)
    assert "Прихожая" in text and "tuya_bf1" in text and "онлайн" in text and "офлайн" in text
    assert "не найдено" in format_tuya_cameras([])


async def test_login_success_sends_qr_then_cameras(cfg, access):
    tuya = FakeTuya()
    msg = make_msg()
    await handlers(cfg, access, tuya).tuya_login(msg, cmd("UC1"))
    photo = msg.answer_photo.call_args
    assert photo.args[0].data.startswith(b"\x89PNG")
    assert "Smart Life" in photo.kwargs["caption"]
    assert "Прихожая" in texts(msg)[-1]


async def test_login_timeout(cfg, access):
    msg = make_msg()
    await handlers(cfg, access, FakeTuya(ok=False)).tuya_login(msg, cmd("UC1"))
    assert "/tuya_login" in texts(msg)[-1]


async def test_login_start_error(cfg, access):
    msg = make_msg()
    await handlers(cfg, access, FakeTuya(start_error="user code invalid")).tuya_login(msg, cmd("BAD"))
    assert "user code invalid" in texts(msg)[-1]
    msg.answer_photo.assert_not_called()


async def test_login_requires_code(cfg, access):
    msg = make_msg()
    await handlers(cfg, access, FakeTuya()).tuya_login(msg, cmd())
    assert texts(msg)[-1] == TUYA_USAGE


async def test_login_non_admin(cfg, access):
    msg = make_msg(user_id=42)
    await handlers(cfg, access, FakeTuya()).tuya_login(msg, cmd("UC1"))
    assert "администратор" in texts(msg)[-1]
    msg.answer_photo.assert_not_called()


async def test_tuya_disabled(cfg, access):
    msg = make_msg()
    await handlers(cfg, access, None).tuya_login(msg, cmd("UC1"))
    assert "отключена" in texts(msg)[-1]


async def test_status_and_logout(cfg, access):
    tuya = FakeTuya()
    h = handlers(cfg, access, tuya)
    msg = make_msg()
    await h.tuya_status(msg)
    assert "не выполнен" in texts(msg)[-1]
    tuya.logged_in = True
    await h.tuya_status(msg)
    assert "Прихожая" in texts(msg)[-1]
    await h.tuya_logout(msg)
    assert tuya.logged_out and "Выход" in texts(msg)[-1]
```

Run: `.venv/Scripts/python -m pytest tests/test_tg_tuya.py -v` → ImportError.

- [ ] **Step 2: Реализация**

В `streemcam/tg/bot.py`:
- Импорты: `from aiogram.types import BufferedInputFile` (добавить в существующий импорт из `aiogram.types`); `from ..catalog import CameraInfo`; `from ..tuya.models import TuyaLoginError`; `from ..tuya.qr import qr_png`.
- Константы и функция после `BUTTON_TEXT`:
```python
TUYA_USAGE = ("Использование: /tuya_login <код пользователя>. Код — в приложении Smart Life/Tuya Smart: "
              "«Я» → ⚙ Настройки → «Аккаунт и безопасность» → «Код пользователя».")
TUYA_QR_CAPTION = ("Откройте Smart Life → «+» (или значок сканера) → отсканируйте этот код и подтвердите вход. "
                   "Жду 2 минуты.")


def format_tuya_cameras(cams: list[CameraInfo]) -> str:
    if not cams:
        return "Камер Tuya не найдено."
    lines = [f"• {c.name} ({c.id}) — {'онлайн' if c.online else 'офлайн'}" for c in cams]
    return f"Камеры Tuya ({len(cams)}):\n" + "\n".join(lines)
```
- `TgHandlers.__init__(self, cfg, access, mini_app_link, tuya=None)`: сохранить `self.tuya = tuya`.
- Методы:
```python
    async def _require_tuya_admin(self, message: Message) -> bool:
        if await self._require_admin(message) is None:
            return False
        if self.tuya is None:
            await message.answer("Интеграция Tuya отключена в конфиге (tuya.enabled).")
            return False
        return True

    async def tuya_login(self, message: Message, command: CommandObject) -> None:
        if not await self._require_tuya_admin(message):
            return
        code = (command.args or "").strip()
        if not code:
            await message.answer(TUYA_USAGE)
            return
        try:
            session = await self.tuya.start_login(code)
        except TuyaLoginError as e:
            await message.answer(f"Не удалось начать вход в Tuya: {e}")
            return
        await message.answer_photo(BufferedInputFile(qr_png(session.qr_payload), "tuya-login.png"),
                                   caption=TUYA_QR_CAPTION)
        if not await self.tuya.wait_login(session):
            await message.answer("Вход не подтверждён (истекло время или начата новая попытка). "
                                 "Повторите /tuya_login <код пользователя>.")
            return
        await message.answer("✅ Вход в Tuya выполнен.\n" + format_tuya_cameras(self.tuya.cameras()))

    async def tuya_status(self, message: Message) -> None:
        if not await self._require_tuya_admin(message):
            return
        if not self.tuya.logged_in:
            await message.answer("Вход в Tuya не выполнен. " + TUYA_USAGE)
            return
        await message.answer("Вход в Tuya выполнен.\n" + format_tuya_cameras(self.tuya.cameras()))

    async def tuya_logout(self, message: Message) -> None:
        if not await self._require_tuya_admin(message):
            return
        await self.tuya.logout()
        await message.answer("Выход из Tuya выполнен, камеры Tuya убраны из списка.")
```
- В `build_router` добавить:
```python
    router.message.register(h.tuya_login, Command("tuya_login"))
    router.message.register(h.tuya_status, Command("tuya_status"))
    router.message.register(h.tuya_logout, Command("tuya_logout"))
```
- `run_telegram(bot, cfg, access, tuya=None)`: `TgHandlers(cfg, access, link, tuya=tuya)`.

- [ ] **Step 3: Тесты проходят**

Run: `.venv/Scripts/python -m pytest tests/test_tg_tuya.py tests/test_tg_bot.py -v` → PASS; `.venv/Scripts/python -m pytest -q` → всё PASS.

- [ ] **Step 4: Commit**

```bash
git add streemcam/tg/bot.py tests/test_tg_tuya.py
git commit -m "feat: Telegram admin commands for Tuya QR login, status and logout"
```

---

### Task 11: Сборка приложения

**Files:**
- Modify: `streemcam/app.py`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: всё выше.
- Produces: `parse_listen(value: str) -> tuple[str, int]`; `async sync_forever(sync, interval_seconds: float)`; `run(cfg)` поднимает: каталог, `Go2rtcSync`, при `tuya.enabled` — `TuyaService` (load + supervise `run`) и второй uvicorn на `internal_listen` с `create_internal_app`; иначе supervise `sync_forever(sync, 600)`.

- [ ] **Step 1: Падающие тесты**

Дописать в `tests/test_app.py`:
```python
import asyncio

from streemcam.app import parse_listen, sync_forever


def test_parse_listen():
    assert parse_listen("127.0.0.1:8081") == ("127.0.0.1", 8081)
    assert parse_listen("0.0.0.0:8081") == ("0.0.0.0", 8081)
    assert parse_listen(":8081") == ("0.0.0.0", 8081)


async def test_sync_forever_repeats():
    class S:
        n = 0

        async def sync(self):
            S.n += 1

    task = asyncio.create_task(sync_forever(S(), 0))
    for _ in range(20):
        await asyncio.sleep(0)
    task.cancel()
    assert S.n >= 2
```
(если `asyncio` уже импортирован в файле — не дублировать импорт.)

Run: `.venv/Scripts/python -m pytest tests/test_app.py -v` → ImportError.

- [ ] **Step 2: Реализация**

В `streemcam/app.py`:
- Импорты: `from .go2rtc_sync import Go2rtcSync`.
- Добавить функции:
```python
def parse_listen(value: str) -> tuple[str, int]:
    host, _, port = value.rpartition(":")
    return (host or "0.0.0.0"), int(port)


async def sync_forever(sync, interval_seconds: float) -> None:
    while True:
        await sync.sync()
        await asyncio.sleep(interval_seconds)
```
- В `run(cfg)` после `http = ...` и `catalog = Catalog(cfg)`:
```python
    sync = Go2rtcSync(http, catalog, cfg.internal_url, cfg.internal_key)
```
- Перед `monitor = ...` определить сервис Tuya (после `on_alert`):
```python
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
```
- Список фоновых задач: сразу после создания `background = [...]` добавить:
```python
    if tuya is not None:
        from .web.internal import create_internal_app
        host, port = parse_listen(cfg.internal_listen)
        internal = uvicorn.Server(uvicorn.Config(create_internal_app(catalog, tuya, cfg.internal_key),
                                                 host=host, port=port, log_level="warning"))
        background.append(asyncio.create_task(supervise("internal-api", internal.serve)))
        background.append(asyncio.create_task(supervise("tuya", tuya.run)))
    else:
        background.append(asyncio.create_task(supervise("go2rtc-sync", lambda: sync_forever(sync, 600))))
```
- Telegram: `lambda: run_telegram(tg_bot, cfg, access, tuya)`.

ВНИМАНИЕ: второй `uvicorn.Server.serve()` в том же процессе может перехватывать сигналы. Проверьте в установленной версии uvicorn, что `serve()` не мешает основному серверу (при необходимости переопределите `install_signal_handlers`/используйте `capture_signals` только у основного — зафиксируйте решение в отчёте). Smoke-проверка ниже обязательна.

- [ ] **Step 3: Тесты и smoke-проверка**

Run: `.venv/Scripts/python -m pytest -q` → всё PASS.

Smoke: во временной папке (НЕ в репозитории) создать `config.yaml` с одной rtsp-камерой, `listen_port: 18080`, `internal_listen: "127.0.0.1:18081"`, `internal_url: "http://127.0.0.1:18081"`, `internal_key: "k0123456789abcdef"`, `tuya: {enabled: true}`, `db_path: <tmp>/db.sqlite`, без токенов ботов. Запустить `.venv/Scripts/python -m streemcam --config <tmp>/config.yaml run` в фоне на ~6 с, затем:
- `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:18080/` → `200`
- `curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:18081/tuya/abc?key=k0123456789abcdef"` → `404`
- `curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:18081/tuya/abc?key=wrong"` → `403`
Остановить процесс (Ctrl+C / kill), убедиться, что оба сервера завершились. Приложить логи в отчёт.

- [ ] **Step 4: Commit**

```bash
git add streemcam/app.py tests/test_app.py
git commit -m "feat: wire Tuya service, go2rtc sync and internal API into the app"
```

---

### Task 12: Конфиги, compose, README

**Files:**
- Modify: `config.example.yaml`, `.env.example`, `docker-compose.yml`, `README.md`

**Interfaces:**
- Consumes: поля конфига (Task 1), команды бота (Task 10).

- [ ] **Step 1: config.example.yaml**

Заменить блок `cameras:` целиком на:
```yaml
cameras:
  # Xiaomi Smart Camera C701 (H.265). Доступ по локальной сети (с VPS — через WireGuard на роутере).
  - id: c701_hall
    name: Прихожая
    type: xiaomi
    account: "1234567890"    # ID аккаунта Xiaomi (виден в WebUI go2rtc после входа)
    region: de
    host: 192.168.1.31
    did: "111111111"
    model: chuangmi.camera.079ae2
  - id: c701_room
    name: Комната
    type: xiaomi
    account: "1234567890"
    region: de
    host: 192.168.1.32
    did: "222222222"
    model: chuangmi.camera.079ae2
  - id: c701_yard
    name: Двор
    type: xiaomi
    account: "1234567890"
    region: de
    host: 192.168.1.33
    did: "333333333"
    model: chuangmi.camera.079ae2
  - id: test
    name: Тестовый поток
    type: rtsp
    url: "ffmpeg:virtual?video&size=720#video=h264"
```
После строки `player_mode: ...` добавить:
```yaml
max_transcodes: 2            # одновременных перекодировок «совместимого режима» (4 vCPU → 1–2)

# Внутренний API для go2rtc (только docker-сеть, наружу не публикуется)
internal_listen: "0.0.0.0:8081"
internal_url: http://streemcam:8081
internal_key: ${STREEMCAM_INTERNAL_KEY:-}

tuya:
  enabled: true              # камеры из Smart Life/Tuya Smart; вход — /tuya_login в Telegram
  refresh_minutes: 10
  snapshot_minutes: 15
```

- [ ] **Step 2: .env.example**

Удалить строку `DAHUA_PASSWORD=`; после `STREEMCAM_SECRET=` добавить:
```
# ключ внутреннего API go2rtc → streemcam, сгенерировать так же, как секрет
STREEMCAM_INTERNAL_KEY=
```

- [ ] **Step 3: docker-compose.yml**

У сервиса `go2rtc` заменить комментарий образа на `# нужна версия >= 1.9.13 (xiaomi://); в образе есть curl для источников Tuya (echo:)`. У `streemcam` порт 8081 НЕ публикуется (ничего не добавлять в `ports`); добавить комментарий над `ports:` у `streemcam`: `# 8081 (внутренний API для go2rtc) доступен только в docker-сети — не публикуйте его`.

- [ ] **Step 4: README.md**

- Строку 3 заменить на: `Просмотр камер (RTSP, Xiaomi, Tuya/Smart Life, Dahua) из Telegram и Discord для пользователей из белого списка.`
- Удалить раздел `## Камеры Dahua` (2 строки).
- В `## Быстрый старт` п.1 дописать: `Сгенерировать и вписать ещё STREEMCAM_INTERNAL_KEY (той же командой).`
- Добавить разделы перед `## Изменение конфигурации`:
```markdown
## VPS и домашние камеры (WireGuard на роутере)
Камеры Xiaomi отдают видео только в локальной сети, поэтому VPS должен видеть домашнюю подсеть:
1. На VPS поднять WireGuard-сервер (например, `apt install wireguard`, интерфейс `wg0`, адрес `10.8.0.1/24`,
   порт UDP 51820 открыт), в `AllowedIPs` пира-роутера указать `10.8.0.2/32, 192.168.1.0/24` (ваша домашняя подсеть).
2. На роутере создать WireGuard-подключение к VPS (клиент), `AllowedIPs = 10.8.0.0/24`,
   разрешить маршрутизацию/NAT из туннеля в домашнюю сеть.
3. Проверить с VPS: `ping 192.168.1.31` (IP камеры). Контейнеры Docker ходят в эту подсеть через хост.
4. Закрепить за камерами постоянные IP (DHCP-резервирование на роутере).

## Камеры Tuya / Smart Life
1. В `config.yaml`: `tuya.enabled: true`, в `.env` — `STREEMCAM_INTERNAL_KEY`.
2. В приложении Smart Life / Tuya Smart: «Я» → ⚙ → «Аккаунт и безопасность» → «Код пользователя».
3. В личке с ботом (админ): `/tuya_login <код>` → бот пришлёт QR → Smart Life → сканер → подтвердить.
4. Камеры аккаунта появятся в плеере автоматически (`/tuya_status` — список, `/tuya_logout` — выход).
Вход использует client_id интеграции Tuya из Home Assistant; если Tuya его заблокирует, вход перестанет работать.

## Совместимый режим (H.265)
C701 отдают H.265 — его показывают Safari/iOS и Chrome/Edge с аппаратным декодированием. Если видео чёрное,
включите в плеере «Совместимый режим»: сервер перекодирует поток в H.264 720p. Одновременно — не больше
`max_transcodes` таких просмотров.
```
- В `## Камеры Xiaomi` дописать строку: `Xiaomi Smart Camera C701 — model chuangmi.camera.079ae2 (поддерживается go2rtc ≥ 1.9.13).`

- [ ] **Step 5: Проверки**

- С временными `config.yaml` и `.env` (скопированы из примеров, `STREEMCAM_SECRET` и `STREEMCAM_INTERNAL_KEY` заполнены сгенерированными значениями): `docker compose config -q` → без ошибок (если нужен демон Docker и он не запущен — отметить в отчёте); затем `.venv/Scripts/python -m streemcam --config config.yaml render-go2rtc --out <tmp>/go2rtc.yaml` → 4 потока. Удалить временные `config.yaml`/`.env` (они в .gitignore, в коммит не попадают).
- `.venv/Scripts/python -m pytest -q` → всё PASS.

- [ ] **Step 6: Commit**

```bash
git add config.example.yaml .env.example docker-compose.yml README.md
git commit -m "docs: C701 and Tuya examples, VPS WireGuard and compatibility mode guides"
```
