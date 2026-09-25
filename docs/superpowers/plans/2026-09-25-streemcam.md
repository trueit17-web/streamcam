# streemcam Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Живой просмотр RTSP/Dahua/Xiaomi-камер по кнопке из Telegram (Mini App) и Discord (личные ссылки) для пользователей из белого списка.

**Architecture:** go2rtc забирает потоки с камер и отдаёт MSE/WebRTC по WebSocket. Один Python-процесс `streemcam` содержит FastAPI-шлюз (плеер, авторизация, прокси WS к go2rtc только для разрешённых камер), aiogram-бота и discord.py-бота. Всё запускается docker-compose вместе с go2rtc и cloudflared.

**Tech Stack:** Python 3.12, FastAPI, uvicorn, httpx, websockets ≥ 13, aiogram 3, discord.py 2, pydantic 2, PyYAML, SQLite (stdlib), pytest + pytest-asyncio; go2rtc ≥ 1.9.13; Cloudflare Tunnel.

**Spec:** `docs/superpowers/specs/2026-09-25-streemcam-design.md`

## Global Constraints

- Python ≥ 3.12; зависимости только из списка в `pyproject.toml` (Task 1).
- go2rtc ≥ 1.9.13 (нужен источник `xiaomi://`).
- go2rtc API никогда не публикуется наружу; снаружи доступен только `streemcam` (через туннель).
- Платформы в коде обозначаются `"tg"` и `"dc"`; в YAML — ключи `telegram` / `discord`.
- ID камеры: `^[a-z0-9_-]+$`, уникален.
- Токен ссылки: одноразовый, TTL `token_ttl_minutes` (по умолчанию 10). Сессия: TTL `session_ttl_hours` (по умолчанию 12). Лимит потоков на пользователя: `max_streams_per_user` (по умолчанию 4).
- `initData` Telegram — не старше 3600 с.
- Секреты (`config.yaml`, `.env`, `data/`, `go2rtc/`) не коммитятся.
- Все тексты для пользователя — на русском.
- Команды ниже предполагают активированное venv (`.venv\Scripts\activate` в PowerShell).

### Отклонение от спеки (согласовать при ревью)
Сессия передаётся не cookie, а токеном: `Authorization: Bearer` для fetch и `?s=` для WebSocket/картинок, хранится в `sessionStorage`. Причина: Telegram Web (web.telegram.org) открывает Mini App в iframe, где cookie с `SameSite=Lax` не отправляются. Автопереподключение плеера — встроенное в `video-rtc.js` go2rtc.

## File Structure

```
pyproject.toml, .gitignore
streemcam/
  __init__.py
  __main__.py          CLI: run | render-go2rtc | link
  identity.py          Identity(platform, user_id)
  config.py            pydantic-модели конфига, load_config, ConfigError
  go2rtc_config.py     stream_url, render, write_go2rtc_config
  tokens.py            HMAC-подпись токенов (sign/verify)
  tg_auth.py           проверка Telegram initData
  db.py                Store: SQLite (белый список, одноразовые токены)
  streams.py           StreamRegistry: лимит и принудительное закрытие потоков
  access.py            Access: вся политика доступа (фасад над tokens/db/streams)
  monitor.py           CameraMonitor: опрос камер, кэш снимков, алерты
  admin.py             parse_target, format_users, USAGE (общие для ботов)
  app.py               сборка и запуск всего, supervise, alert_text
  web/__init__.py
  web/server.py        create_app: REST, WS-прокси, статика
  web/static/index.html, app.js, style.css
  tg/__init__.py, tg/bot.py
  dc/__init__.py, dc/bot.py
tests/
  conftest.py, tg_helpers.py, test_*.py
Dockerfile, docker-compose.yml, config.example.yaml, .env.example, README.md
```

---

### Task 1: Каркас проекта и конфиг

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `streemcam/__init__.py`, `streemcam/identity.py`, `streemcam/config.py`
- Test: `tests/conftest.py`, `tests/test_config.py`

**Interfaces:**
- Produces:
  - `Identity(platform: Literal["tg","dc"], user_id: int)` — frozen dataclass, `str()` → `"tg:42"`.
  - `Config` (поля ниже), `Config.camera(cam_id) -> Camera | None`, `UserLists.ids(platform) -> list[int]`.
  - Модели камер `RtspCamera`, `DahuaCamera`, `XiaomiCamera`; `Camera` — их union.
  - `parse_config(data: dict) -> Config`, `load_config(path) -> Config`, `ConfigError`.
  - Фикстуры `base_data` (dict), `make_cfg(**overrides) -> Config`, `cfg` (Config).

- [ ] **Step 1: Создать pyproject, .gitignore, venv**

`pyproject.toml`:
```toml
[build-system]
requires = ["setuptools>=69"]
build-backend = "setuptools.build_meta"

[project]
name = "streemcam"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "httpx>=0.27",
    "websockets>=13",
    "aiogram>=3.13",
    "discord.py>=2.4",
    "pydantic>=2.8",
    "PyYAML>=6",
    "python-dotenv>=1.0",
]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.24"]

[tool.setuptools.packages.find]
include = ["streemcam*"]

[tool.setuptools.package-data]
streemcam = ["web/static/*"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

`.gitignore`:
```
.venv/
__pycache__/
*.egg-info/
.pytest_cache/
config.yaml
.env
data/
go2rtc/
```

`streemcam/__init__.py`: пустой файл.

Run:
```
python -m venv .venv
.venv\Scripts\activate
pip install -e .[dev]
```
Expected: установка без ошибок.

- [ ] **Step 2: Identity**

`streemcam/identity.py`:
```python
from dataclasses import dataclass
from typing import Literal

Platform = Literal["tg", "dc"]


@dataclass(frozen=True)
class Identity:
    platform: Platform
    user_id: int

    def __str__(self) -> str:
        return f"{self.platform}:{self.user_id}"
```

- [ ] **Step 3: Написать фикстуры и падающие тесты конфига**

`tests/conftest.py`:
```python
import copy

import pytest

from streemcam.config import parse_config

BASE = {
    "public_url": "https://cams.example.com",
    "secret": "test-secret-0123456789",
    "telegram": {"bot_token": "123456:TEST", "mini_app_short_name": "cams", "channel_id": -100500},
    "discord": {"bot_token": "dc-token", "guild_ids": [1]},
    "admins": {"telegram": [1], "discord": [2]},
    "allow": {"telegram": [42], "discord": [77]},
    "cameras": [
        {"id": "yard", "name": "Двор", "type": "rtsp", "url": "rtsp://10.0.0.5:554/stream1"},
        {"id": "gate", "name": "Ворота", "type": "dahua", "host": "10.0.0.6",
         "user": "admin", "password": "p@ss:w/rd", "subtype": 1},
        {"id": "room", "name": "Комната", "type": "xiaomi", "account": "1234567", "region": "de",
         "host": "10.0.0.7", "did": "987654", "model": "chuangmi.camera.v2"},
    ],
}


@pytest.fixture
def base_data():
    return copy.deepcopy(BASE)


@pytest.fixture
def make_cfg():
    def make(**overrides):
        data = copy.deepcopy(BASE)
        data.update(overrides)
        return parse_config(data)
    return make


@pytest.fixture
def cfg(make_cfg):
    return make_cfg()
```

`tests/test_config.py`:
```python
import pytest
import yaml

from streemcam.config import ConfigError, load_config


def test_parses_all_camera_types(cfg):
    assert [c.type for c in cfg.cameras] == ["rtsp", "dahua", "xiaomi"]
    gate = cfg.camera("gate")
    assert (gate.port, gate.channel, gate.subtype) == (554, 1, 1)
    assert cfg.camera("nope") is None


def test_defaults(cfg):
    assert cfg.max_streams_per_user == 4
    assert cfg.token_ttl_minutes == 10
    assert cfg.session_ttl_hours == 12
    assert cfg.player_mode == "mse"
    assert cfg.admins.ids("tg") == [1]
    assert cfg.allow.ids("dc") == [77]


def test_public_url_trailing_slash_stripped(make_cfg):
    assert make_cfg(public_url="https://x.example/").public_url == "https://x.example"


def test_missing_field_reports_path(make_cfg, base_data):
    cams = base_data["cameras"]
    del cams[1]["host"]
    with pytest.raises(ConfigError) as e:
        make_cfg(cameras=cams)
    assert "cameras.1.dahua.host" in str(e.value)


def test_duplicate_camera_ids_rejected(make_cfg, base_data):
    cams = base_data["cameras"]
    cams[1]["id"] = "yard"
    with pytest.raises(ConfigError, match="duplicate camera id: yard"):
        make_cfg(cameras=cams)


def test_bad_camera_id_rejected(make_cfg, base_data):
    cams = base_data["cameras"]
    cams[0]["id"] = "Yard 1"
    with pytest.raises(ConfigError):
        make_cfg(cameras=cams)


def test_empty_bot_token_means_disabled(make_cfg):
    assert make_cfg(telegram={"bot_token": ""}).telegram.bot_token is None


def test_load_config_expands_env(tmp_path, monkeypatch, base_data):
    monkeypatch.setenv("SC_SECRET", "env-secret-0123456789")
    monkeypatch.delenv("SC_TG", raising=False)
    base_data["secret"] = "${SC_SECRET}"
    base_data["telegram"]["bot_token"] = "${SC_TG:-}"
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(base_data, allow_unicode=True), encoding="utf-8")
    cfg = load_config(path)
    assert cfg.secret == "env-secret-0123456789"
    assert cfg.telegram.bot_token is None


def test_load_config_missing_env_fails(tmp_path, monkeypatch, base_data):
    monkeypatch.delenv("SC_MISSING", raising=False)
    base_data["secret"] = "${SC_MISSING}"
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(base_data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ConfigError, match="SC_MISSING"):
        load_config(path)
```

- [ ] **Step 4: Запустить — убедиться, что падает**

Run: `python -m pytest tests/test_config.py -v`
Expected: ошибка импорта `streemcam.config`.

- [ ] **Step 5: Реализовать config.py**

`streemcam/config.py`:
```python
import os
import re
from pathlib import Path
from typing import Annotated, Literal, Union

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

CAMERA_ID = r"^[a-z0-9_-]+$"
_ENV_RE = re.compile(r"\$\{(\w+)(?::-([^}]*))?\}")


class ConfigError(Exception):
    pass


class RtspCamera(BaseModel):
    type: Literal["rtsp"]
    id: str = Field(pattern=CAMERA_ID)
    name: str
    url: str  # любой источник go2rtc: rtsp://, ffmpeg:..., и т.д.


class DahuaCamera(BaseModel):
    type: Literal["dahua"]
    id: str = Field(pattern=CAMERA_ID)
    name: str
    host: str
    port: int = 554
    user: str
    password: str
    channel: int = 1
    subtype: Literal[0, 1] = 0


class XiaomiCamera(BaseModel):
    type: Literal["xiaomi"]
    id: str = Field(pattern=CAMERA_ID)
    name: str
    account: str
    region: str
    host: str
    did: str
    model: str
    subtype: int | None = None


Camera = Annotated[Union[RtspCamera, DahuaCamera, XiaomiCamera], Field(discriminator="type")]


class UserLists(BaseModel):
    telegram: list[int] = []
    discord: list[int] = []

    def ids(self, platform: str) -> list[int]:
        return self.telegram if platform == "tg" else self.discord


class _BotConfig(BaseModel):
    bot_token: str | None = None

    @field_validator("bot_token", mode="before")
    @classmethod
    def _empty_is_none(cls, v):
        return v or None


class TelegramConfig(_BotConfig):
    mini_app_short_name: str = "cams"
    channel_id: int | str | None = None


class DiscordConfig(_BotConfig):
    guild_ids: list[int] = []


class Config(BaseModel):
    public_url: str
    secret: str = Field(min_length=16)
    listen_host: str = "127.0.0.1"
    listen_port: int = 8080
    session_ttl_hours: int = 12
    token_ttl_minutes: int = 10
    max_streams_per_user: int = 4
    player_mode: str = "mse"
    go2rtc_url: str = "http://127.0.0.1:1984"
    go2rtc_api_listen: str = "127.0.0.1:1984"
    webrtc_listen: str = ":8555"
    db_path: str = "data/streemcam.db"
    probe_interval_seconds: int = 60
    offline_alert_minutes: int = 5
    telegram: TelegramConfig = TelegramConfig()
    discord: DiscordConfig = DiscordConfig()
    admins: UserLists = UserLists()
    allow: UserLists = UserLists()
    cameras: list[Camera]

    @field_validator("public_url")
    @classmethod
    def _strip_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @model_validator(mode="after")
    def _unique_camera_ids(self):
        seen = set()
        for cam in self.cameras:
            if cam.id in seen:
                raise ValueError(f"duplicate camera id: {cam.id}")
            seen.add(cam.id)
        return self

    def camera(self, cam_id: str) -> Camera | None:
        return next((c for c in self.cameras if c.id == cam_id), None)


def _expand_env(obj):
    if isinstance(obj, str):
        def repl(m: re.Match) -> str:
            name, default = m.group(1), m.group(2)
            value = os.environ.get(name)
            if value is None:
                if default is None:
                    raise ConfigError(f"environment variable {name} is not set")
                return default
            return value
        return _ENV_RE.sub(repl, obj)
    if isinstance(obj, dict):
        return {k: _expand_env(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_env(v) for v in obj]
    return obj


def parse_config(data: dict) -> Config:
    try:
        return Config.model_validate(data)
    except ValidationError as e:
        lines = [".".join(str(p) for p in err["loc"]) + ": " + err["msg"] for err in e.errors()]
        raise ConfigError("invalid config:\n" + "\n".join(lines)) from None


def load_config(path: str | os.PathLike) -> Config:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return parse_config(_expand_env(data))
```

- [ ] **Step 6: Запустить тесты**

Run: `python -m pytest tests/test_config.py -v`
Expected: все PASS. Если тест `test_duplicate_camera_ids_rejected` падает на тексте — pydantic добавляет префикс `Value error, `; `match` ищет подстроку, так что проходит.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml .gitignore streemcam tests
git commit -m "feat: project skeleton and config models"
```

---

### Task 2: Генерация go2rtc.yaml

**Files:**
- Create: `streemcam/go2rtc_config.py`
- Test: `tests/test_go2rtc_config.py`

**Interfaces:**
- Consumes: `Config`, `RtspCamera`, `DahuaCamera`, `XiaomiCamera` (Task 1).
- Produces: `stream_url(cam) -> str`, `render(cfg, existing: dict | None) -> dict`, `write_go2rtc_config(path, cfg) -> None`.
- Важно: go2rtc сам дописывает в свой yaml токены Xiaomi (ключ `xiaomi`) после входа через WebUI — `render` обязан сохранять все чужие ключи.

- [ ] **Step 1: Падающие тесты**

`tests/test_go2rtc_config.py`:
```python
import yaml

from streemcam.go2rtc_config import render, stream_url, write_go2rtc_config


def test_stream_urls(cfg):
    assert stream_url(cfg.camera("yard")) == "rtsp://10.0.0.5:554/stream1"
    assert stream_url(cfg.camera("gate")) == (
        "rtsp://admin:p%40ss%3Aw%2Frd@10.0.0.6:554/cam/realmonitor?channel=1&subtype=1"
    )
    assert stream_url(cfg.camera("room")) == (
        "xiaomi://1234567:de@10.0.0.7?did=987654&model=chuangmi.camera.v2"
    )


def test_xiaomi_subtype(make_cfg, base_data):
    cams = base_data["cameras"]
    cams[2]["subtype"] = 1
    assert stream_url(make_cfg(cameras=cams).camera("room")).endswith("&subtype=1")


def test_render_sets_streams_and_listen(cfg):
    data = render(cfg, None)
    assert set(data["streams"]) == {"yard", "gate", "room"}
    assert data["api"]["listen"] == "127.0.0.1:1984"
    assert data["webrtc"]["listen"] == ":8555"


def test_render_preserves_foreign_keys(cfg):
    existing = {
        "xiaomi": {"1234567": "secret-token"},
        "streams": {"old": "rtsp://x"},
        "api": {"username": "u"},
    }
    data = render(cfg, existing)
    assert data["xiaomi"] == {"1234567": "secret-token"}
    assert "old" not in data["streams"]
    assert data["api"] == {"username": "u", "listen": "127.0.0.1:1984"}
    assert existing["streams"] == {"old": "rtsp://x"}  # вход не мутируется


def test_write_keeps_xiaomi_tokens_between_runs(tmp_path, cfg):
    path = tmp_path / "go2rtc" / "go2rtc.yaml"
    write_go2rtc_config(path, cfg)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["xiaomi"] = {"1234567": "tok"}
    path.write_text(yaml.safe_dump(data), encoding="utf-8")

    write_go2rtc_config(path, cfg)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["xiaomi"] == {"1234567": "tok"}
    assert data["streams"]["yard"] == "rtsp://10.0.0.5:554/stream1"
```

- [ ] **Step 2: Убедиться, что падает**

Run: `python -m pytest tests/test_go2rtc_config.py -v`
Expected: ImportError.

- [ ] **Step 3: Реализация**

`streemcam/go2rtc_config.py`:
```python
import os
from pathlib import Path
from urllib.parse import quote, urlencode

import yaml

from .config import Config, DahuaCamera, RtspCamera, XiaomiCamera


def stream_url(cam) -> str:
    match cam:
        case RtspCamera():
            return cam.url
        case DahuaCamera():
            user = quote(cam.user, safe="")
            password = quote(cam.password, safe="")
            return (f"rtsp://{user}:{password}@{cam.host}:{cam.port}"
                    f"/cam/realmonitor?channel={cam.channel}&subtype={cam.subtype}")
        case XiaomiCamera():
            query = {"did": cam.did, "model": cam.model}
            if cam.subtype is not None:
                query["subtype"] = cam.subtype
            return f"xiaomi://{quote(cam.account, safe='')}:{cam.region}@{cam.host}?{urlencode(query)}"
    raise TypeError(f"unknown camera type: {cam!r}")


def render(cfg: Config, existing: dict | None) -> dict:
    data = dict(existing or {})
    data["streams"] = {cam.id: stream_url(cam) for cam in cfg.cameras}
    data["api"] = {**(data.get("api") or {}), "listen": cfg.go2rtc_api_listen}
    data["webrtc"] = {**(data.get("webrtc") or {}), "listen": cfg.webrtc_listen}
    return data


def write_go2rtc_config(path: str | os.PathLike, cfg: Config) -> None:
    path = Path(path)
    existing = None
    if path.exists():
        existing = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(render(cfg, existing), allow_unicode=True, sort_keys=False),
                    encoding="utf-8")
```

- [ ] **Step 4: Тесты проходят**

Run: `python -m pytest tests/test_go2rtc_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add streemcam/go2rtc_config.py tests/test_go2rtc_config.py
git commit -m "feat: generate go2rtc config from camera list"
```

---

### Task 3: Подписанные токены и проверка Telegram initData

**Files:**
- Create: `streemcam/tokens.py`, `streemcam/tg_auth.py`
- Test: `tests/test_tokens.py`, `tests/tg_helpers.py`, `tests/test_tg_auth.py`

**Interfaces:**
- Consumes: `Identity` (Task 1).
- Produces:
  - `tokens.sign(payload: dict, secret: str) -> str`
  - `tokens.verify(token: str, secret: str, typ: str, now: float | None = None) -> dict` — поднимает `TokenError`; payload обязан содержать `typ` и `exp`.
  - `tg_auth.verify_init_data(init_data: str, bot_token: str, max_age: int = 3600, now: float | None = None) -> Identity` — поднимает `TgAuthError`.
  - `tests/tg_helpers.make_init_data(bot_token, user_id=42, auth_date=None) -> str`.

- [ ] **Step 1: Падающие тесты токенов**

`tests/test_tokens.py`:
```python
import pytest

from streemcam.tokens import TokenError, sign, verify

SECRET = "s" * 20


def test_roundtrip():
    t = sign({"typ": "session", "u": 1, "exp": 2000}, SECRET)
    assert verify(t, SECRET, "session", now=1000)["u"] == 1


def test_expired():
    t = sign({"typ": "session", "exp": 999}, SECRET)
    with pytest.raises(TokenError, match="expired"):
        verify(t, SECRET, "session", now=1000)


def test_wrong_secret():
    t = sign({"typ": "session", "exp": 2000}, SECRET)
    with pytest.raises(TokenError, match="signature"):
        verify(t, "x" * 20, "session", now=1000)


def test_wrong_type():
    t = sign({"typ": "link", "exp": 2000}, SECRET)
    with pytest.raises(TokenError, match="type"):
        verify(t, SECRET, "session", now=1000)


def test_tampered_body():
    t = sign({"typ": "session", "u": 1, "exp": 2000}, SECRET)
    other = sign({"typ": "session", "u": 2, "exp": 2000}, SECRET)
    forged = other.split(".")[0] + "." + t.split(".")[1]
    with pytest.raises(TokenError):
        verify(forged, SECRET, "session", now=1000)


@pytest.mark.parametrize("bad", ["", "abc", "a.b.c", "!!!.???"])
def test_malformed(bad):
    with pytest.raises(TokenError):
        verify(bad, SECRET, "session", now=1000)
```

- [ ] **Step 2: Убедиться, что падает**

Run: `python -m pytest tests/test_tokens.py -v` → ImportError.

- [ ] **Step 3: Реализация tokens.py**

`streemcam/tokens.py`:
```python
import base64
import binascii
import hashlib
import hmac
import json
import time


class TokenError(Exception):
    pass


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _mac(body: str, secret: str) -> str:
    return _b64e(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())


def sign(payload: dict, secret: str) -> str:
    body = _b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    return f"{body}.{_mac(body, secret)}"


def verify(token: str, secret: str, typ: str, now: float | None = None) -> dict:
    parts = token.split(".")
    if len(parts) != 2:
        raise TokenError("malformed token")
    body, sig = parts
    if not hmac.compare_digest(sig.encode(), _mac(body, secret).encode()):
        raise TokenError("bad signature")
    try:
        payload = json.loads(_b64d(body))
    except (ValueError, binascii.Error):
        raise TokenError("malformed token") from None
    if payload.get("typ") != typ:
        raise TokenError("wrong token type")
    if payload.get("exp", 0) < (time.time() if now is None else now):
        raise TokenError("token expired")
    return payload
```

- [ ] **Step 4: Тесты токенов проходят**

Run: `python -m pytest tests/test_tokens.py -v` → PASS.

- [ ] **Step 5: Хелпер и падающие тесты initData**

`tests/tg_helpers.py`:
```python
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode


def make_init_data(bot_token: str, user_id: int = 42, auth_date: int | None = None) -> str:
    """Строит initData так же, как Telegram (алгоритм из документации Mini Apps)."""
    fields = {
        "auth_date": str(int(time.time()) if auth_date is None else auth_date),
        "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
        "user": json.dumps({"id": user_id, "first_name": "Иван"}, ensure_ascii=False),
    }
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)
```

`tests/test_tg_auth.py`:
```python
from urllib.parse import parse_qsl, urlencode

import pytest

from streemcam.identity import Identity
from streemcam.tg_auth import TgAuthError, verify_init_data
from tg_helpers import make_init_data

TOKEN = "123456:TEST"


def test_valid():
    data = make_init_data(TOKEN, user_id=42, auth_date=1000)
    assert verify_init_data(data, TOKEN, now=1500) == Identity("tg", 42)


def test_wrong_bot_token():
    data = make_init_data(TOKEN, auth_date=1000)
    with pytest.raises(TgAuthError):
        verify_init_data(data, "999:OTHER", now=1500)


def test_tampered_user():
    fields = dict(parse_qsl(make_init_data(TOKEN, user_id=42, auth_date=1000)))
    fields["user"] = fields["user"].replace("42", "43")
    with pytest.raises(TgAuthError):
        verify_init_data(urlencode(fields), TOKEN, now=1500)


def test_too_old():
    data = make_init_data(TOKEN, auth_date=1000)
    with pytest.raises(TgAuthError, match="expired"):
        verify_init_data(data, TOKEN, now=1000 + 3601)


@pytest.mark.parametrize("bad", ["", "garbage", "auth_date=1&user=%7B%7D"])
def test_malformed(bad):
    with pytest.raises(TgAuthError):
        verify_init_data(bad, TOKEN, now=1500)
```

- [ ] **Step 6: Убедиться, что падает**

Run: `python -m pytest tests/test_tg_auth.py -v` → ImportError.

- [ ] **Step 7: Реализация tg_auth.py**

`streemcam/tg_auth.py`:
```python
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from .identity import Identity


class TgAuthError(Exception):
    pass


def verify_init_data(init_data: str, bot_token: str, max_age: int = 3600,
                     now: float | None = None) -> Identity:
    try:
        fields = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        raise TgAuthError("malformed init data") from None
    received = fields.pop("hash", None)
    if not received:
        raise TgAuthError("no hash")
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(received, expected):
        raise TgAuthError("bad hash")
    try:
        auth_date = int(fields["auth_date"])
        user_id = int(json.loads(fields["user"])["id"])
    except (KeyError, ValueError, TypeError):
        raise TgAuthError("malformed init data") from None
    if (time.time() if now is None else now) - auth_date > max_age:
        raise TgAuthError("init data expired")
    return Identity("tg", user_id)
```

- [ ] **Step 8: Тесты проходят**

Run: `python -m pytest tests/test_tokens.py tests/test_tg_auth.py -v` → PASS.

- [ ] **Step 9: Commit**

```bash
git add streemcam/tokens.py streemcam/tg_auth.py tests/test_tokens.py tests/test_tg_auth.py tests/tg_helpers.py
git commit -m "feat: signed tokens and Telegram initData verification"
```

---

### Task 4: Хранилище, реестр потоков, политика доступа

**Files:**
- Create: `streemcam/db.py`, `streemcam/streams.py`, `streemcam/access.py`
- Test: `tests/test_db.py`, `tests/test_streams.py`, `tests/test_access.py`

**Interfaces:**
- Consumes: `Config`, `Identity`, `tokens.sign/verify/TokenError`, `tg_auth.verify_init_data/TgAuthError`.
- Produces:
  - `Store(path: str)` (`":memory:"` для тестов): `add_allowed(ident, added_by: int | None) -> bool`, `remove_allowed(ident) -> bool`, `is_allowed(ident) -> bool`, `list_allowed() -> list[Identity]`, `add_link_token(jti, ident, expires_at: float)`, `use_link_token(jti, now: float) -> bool`.
  - `StreamRegistry(max_per_user: int)`: `acquire(ident, closer)` (поднимает `StreamLimitError`), `release(ident, closer)`, `count(ident) -> int`, `async kick(ident)`. `closer` — `Callable[[], Awaitable[None]]`.
  - `Access(cfg, store, registry, clock=time.time)`: `is_admin(ident)`, `is_allowed(ident)`, `allow(ident, by: Identity) -> bool`, `async deny(ident) -> bool`, `list_allowed() -> list[Identity]`, `issue_link_token(ident) -> str`, `redeem_link_token(token) -> Identity`, `issue_session(ident) -> str`, `check_session(token) -> Identity`, `login_telegram(init_data) -> Identity`.
  - `AccessDenied(identity)` — исключение с атрибутом `.identity`.

- [ ] **Step 1: Падающие тесты Store**

`tests/test_db.py`:
```python
from streemcam.db import Store
from streemcam.identity import Identity

A = Identity("tg", 42)
B = Identity("dc", 77)


def test_allowed_crud():
    s = Store(":memory:")
    assert s.add_allowed(A, 1) is True
    assert s.add_allowed(A, 1) is False
    s.add_allowed(B, None)
    assert s.is_allowed(A) and s.is_allowed(B)
    assert not s.is_allowed(Identity("dc", 42))  # платформа важна
    assert set(s.list_allowed()) == {A, B}
    assert s.remove_allowed(A) is True
    assert s.remove_allowed(A) is False
    assert not s.is_allowed(A)


def test_link_token_single_use():
    s = Store(":memory:")
    s.add_link_token("j1", A, expires_at=200)
    assert s.use_link_token("j1", now=100) is True
    assert s.use_link_token("j1", now=100) is False


def test_link_token_expired_or_unknown():
    s = Store(":memory:")
    s.add_link_token("j1", A, expires_at=200)
    assert s.use_link_token("j1", now=201) is False
    assert s.use_link_token("nope", now=100) is False


def test_file_db_creates_parent(tmp_path):
    s = Store(str(tmp_path / "sub" / "x.db"))
    s.add_allowed(A, None)
    assert Store(str(tmp_path / "sub" / "x.db")).is_allowed(A)
```

- [ ] **Step 2: Убедиться, что падает**

Run: `python -m pytest tests/test_db.py -v` → ImportError.

- [ ] **Step 3: Реализация db.py**

`streemcam/db.py`:
```python
import sqlite3
import time
from pathlib import Path

from .identity import Identity

SCHEMA = """
CREATE TABLE IF NOT EXISTS allowed (
    platform TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    added_by INTEGER,
    added_at REAL NOT NULL,
    PRIMARY KEY (platform, user_id)
);
CREATE TABLE IF NOT EXISTS link_tokens (
    jti TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    expires_at REAL NOT NULL,
    used INTEGER NOT NULL DEFAULT 0
);
"""


class Store:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.executescript(SCHEMA)

    def add_allowed(self, ident: Identity, added_by: int | None) -> bool:
        cur = self._db.execute(
            "INSERT OR IGNORE INTO allowed (platform, user_id, added_by, added_at) VALUES (?, ?, ?, ?)",
            (ident.platform, ident.user_id, added_by, time.time()))
        return cur.rowcount == 1

    def remove_allowed(self, ident: Identity) -> bool:
        cur = self._db.execute("DELETE FROM allowed WHERE platform = ? AND user_id = ?",
                               (ident.platform, ident.user_id))
        return cur.rowcount == 1

    def is_allowed(self, ident: Identity) -> bool:
        row = self._db.execute("SELECT 1 FROM allowed WHERE platform = ? AND user_id = ?",
                               (ident.platform, ident.user_id)).fetchone()
        return row is not None

    def list_allowed(self) -> list[Identity]:
        rows = self._db.execute("SELECT platform, user_id FROM allowed ORDER BY platform, user_id")
        return [Identity(p, u) for p, u in rows]

    def add_link_token(self, jti: str, ident: Identity, expires_at: float) -> None:
        self._db.execute("DELETE FROM link_tokens WHERE expires_at < ?", (time.time() - 86400,))
        self._db.execute(
            "INSERT INTO link_tokens (jti, platform, user_id, expires_at) VALUES (?, ?, ?, ?)",
            (jti, ident.platform, ident.user_id, expires_at))

    def use_link_token(self, jti: str, now: float) -> bool:
        cur = self._db.execute(
            "UPDATE link_tokens SET used = 1 WHERE jti = ? AND used = 0 AND expires_at >= ?",
            (jti, now))
        return cur.rowcount == 1
```

- [ ] **Step 4: Тесты Store проходят**

Run: `python -m pytest tests/test_db.py -v` → PASS.

- [ ] **Step 5: Падающие тесты StreamRegistry**

`tests/test_streams.py`:
```python
import pytest

from streemcam.identity import Identity
from streemcam.streams import StreamLimitError, StreamRegistry

A = Identity("tg", 42)


def make_closer(log):
    async def closer():
        log.append(closer)
    return closer


async def test_limit_and_release():
    r = StreamRegistry(max_per_user=2)
    c1, c2, c3 = make_closer([]), make_closer([]), make_closer([])
    r.acquire(A, c1)
    r.acquire(A, c2)
    with pytest.raises(StreamLimitError):
        r.acquire(A, c3)
    r.release(A, c1)
    r.acquire(A, c3)
    assert r.count(A) == 2
    assert r.count(Identity("dc", 42)) == 0


async def test_kick_calls_all_closers():
    r = StreamRegistry(max_per_user=4)
    log = []
    c1, c2 = make_closer(log), make_closer(log)
    r.acquire(A, c1)
    r.acquire(A, c2)
    await r.kick(A)
    assert set(log) == {c1, c2}
    assert r.count(A) == 0
    await r.kick(A)  # повторный kick безопасен


async def test_release_unknown_is_noop():
    r = StreamRegistry(max_per_user=1)
    r.release(A, make_closer([]))
    assert r.count(A) == 0
```

- [ ] **Step 6: Реализация streams.py**

`streemcam/streams.py`:
```python
from collections import defaultdict
from collections.abc import Awaitable, Callable

from .identity import Identity

Closer = Callable[[], Awaitable[None]]


class StreamLimitError(Exception):
    pass


class StreamRegistry:
    def __init__(self, max_per_user: int):
        self._max = max_per_user
        self._active: dict[Identity, set[Closer]] = defaultdict(set)

    def acquire(self, ident: Identity, closer: Closer) -> None:
        if len(self._active[ident]) >= self._max:
            raise StreamLimitError(f"{ident} already has {self._max} streams")
        self._active[ident].add(closer)

    def release(self, ident: Identity, closer: Closer) -> None:
        closers = self._active.get(ident)
        if closers is None:
            return
        closers.discard(closer)
        if not closers:
            del self._active[ident]

    def count(self, ident: Identity) -> int:
        return len(self._active.get(ident, ()))

    async def kick(self, ident: Identity) -> None:
        for closer in list(self._active.pop(ident, ())):
            await closer()
```

Run: `python -m pytest tests/test_streams.py -v` → PASS.

- [ ] **Step 7: Падающие тесты Access**

`tests/test_access.py`:
```python
import pytest

from streemcam.access import Access, AccessDenied
from streemcam.db import Store
from streemcam.identity import Identity
from streemcam.streams import StreamRegistry
from streemcam.tg_auth import TgAuthError
from streemcam.tokens import TokenError
from tg_helpers import make_init_data

USER = Identity("tg", 42)
ADMIN = Identity("tg", 1)
STRANGER = Identity("tg", 999)


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def access(cfg, clock):
    return Access(cfg, Store(":memory:"), StreamRegistry(cfg.max_streams_per_user), clock=clock)


def test_seeded_from_config(access):
    assert access.is_allowed(USER)
    assert access.is_allowed(Identity("dc", 77))
    assert not access.is_allowed(STRANGER)


def test_admins_always_allowed(access):
    assert access.is_admin(ADMIN) and access.is_allowed(ADMIN)
    assert access.is_admin(Identity("dc", 2))
    assert not access.is_admin(USER)


async def test_allow_and_deny(access):
    assert access.allow(STRANGER, by=ADMIN) is True
    assert access.is_allowed(STRANGER)
    assert STRANGER in access.list_allowed()
    assert await access.deny(STRANGER) is True
    assert not access.is_allowed(STRANGER)


async def test_deny_kicks_streams(access):
    kicked = []

    async def closer():
        kicked.append(True)

    access.registry.acquire(USER, closer)
    await access.deny(USER)
    assert kicked == [True]


def test_link_token_one_time(access):
    token = access.issue_link_token(USER)
    assert access.redeem_link_token(token) == USER
    with pytest.raises(TokenError):
        access.redeem_link_token(token)


def test_link_token_expires(access, clock):
    token = access.issue_link_token(USER)
    clock.t += 10 * 60 + 1
    with pytest.raises(TokenError):
        access.redeem_link_token(token)


async def test_link_token_for_denied_user(access):
    token = access.issue_link_token(USER)
    await access.deny(USER)
    with pytest.raises(AccessDenied) as e:
        access.redeem_link_token(token)
    assert e.value.identity == USER


async def test_session(access, clock):
    s = access.issue_session(USER)
    assert access.check_session(s) == USER
    with pytest.raises(TokenError):
        access.check_session(access.issue_link_token(USER))  # тип токена другой
    await access.deny(USER)
    with pytest.raises(AccessDenied):
        access.check_session(s)


def test_session_expires(access, clock):
    s = access.issue_session(USER)
    clock.t += 12 * 3600 + 1
    with pytest.raises(TokenError):
        access.check_session(s)


def test_login_telegram(access, clock):
    data = make_init_data("123456:TEST", user_id=42, auth_date=int(clock.t))
    assert access.login_telegram(data) == USER
    stranger = make_init_data("123456:TEST", user_id=999, auth_date=int(clock.t))
    with pytest.raises(AccessDenied):
        access.login_telegram(stranger)
    with pytest.raises(TgAuthError):
        access.login_telegram("garbage")


def test_login_telegram_without_bot_token(make_cfg, clock):
    cfg = make_cfg(telegram={})
    access = Access(cfg, Store(":memory:"), StreamRegistry(4), clock=clock)
    with pytest.raises(TgAuthError):
        access.login_telegram(make_init_data("123456:TEST", auth_date=int(clock.t)))
```

- [ ] **Step 8: Убедиться, что падает**

Run: `python -m pytest tests/test_access.py -v` → ImportError.

- [ ] **Step 9: Реализация access.py**

`streemcam/access.py`:
```python
import secrets
import time
from collections.abc import Callable

from .config import Config
from .db import Store
from .identity import Identity
from .streams import StreamRegistry
from .tg_auth import TgAuthError, verify_init_data
from .tokens import TokenError, sign, verify


class AccessDenied(Exception):
    def __init__(self, identity: Identity):
        super().__init__(f"{identity} is not allowed")
        self.identity = identity


class Access:
    def __init__(self, cfg: Config, store: Store, registry: StreamRegistry,
                 clock: Callable[[], float] = time.time):
        self.cfg = cfg
        self.store = store
        self.registry = registry
        self._clock = clock
        for platform in ("tg", "dc"):
            for uid in cfg.allow.ids(platform):
                store.add_allowed(Identity(platform, uid), None)

    def is_admin(self, ident: Identity) -> bool:
        return ident.user_id in self.cfg.admins.ids(ident.platform)

    def is_allowed(self, ident: Identity) -> bool:
        return self.is_admin(ident) or self.store.is_allowed(ident)

    def allow(self, ident: Identity, by: Identity) -> bool:
        return self.store.add_allowed(ident, by.user_id)

    async def deny(self, ident: Identity) -> bool:
        removed = self.store.remove_allowed(ident)
        await self.registry.kick(ident)
        return removed

    def list_allowed(self) -> list[Identity]:
        return self.store.list_allowed()

    def issue_link_token(self, ident: Identity) -> str:
        jti = secrets.token_urlsafe(12)
        exp = self._clock() + self.cfg.token_ttl_minutes * 60
        self.store.add_link_token(jti, ident, exp)
        return sign({"typ": "link", "jti": jti, "p": ident.platform, "u": ident.user_id, "exp": exp},
                    self.cfg.secret)

    def redeem_link_token(self, token: str) -> Identity:
        now = self._clock()
        payload = verify(token, self.cfg.secret, "link", now=now)
        if not self.store.use_link_token(payload["jti"], now):
            raise TokenError("link token already used")
        return self._require_allowed(Identity(payload["p"], payload["u"]))

    def issue_session(self, ident: Identity) -> str:
        exp = self._clock() + self.cfg.session_ttl_hours * 3600
        return sign({"typ": "session", "p": ident.platform, "u": ident.user_id, "exp": exp},
                    self.cfg.secret)

    def check_session(self, token: str) -> Identity:
        payload = verify(token, self.cfg.secret, "session", now=self._clock())
        return self._require_allowed(Identity(payload["p"], payload["u"]))

    def login_telegram(self, init_data: str) -> Identity:
        bot_token = self.cfg.telegram.bot_token
        if not bot_token:
            raise TgAuthError("telegram bot is not configured")
        ident = verify_init_data(init_data, bot_token, now=self._clock())
        return self._require_allowed(ident)

    def _require_allowed(self, ident: Identity) -> Identity:
        if not self.is_allowed(ident):
            raise AccessDenied(ident)
        return ident
```

- [ ] **Step 10: Все тесты проходят**

Run: `python -m pytest -v` → PASS.

- [ ] **Step 11: Commit**

```bash
git add streemcam/db.py streemcam/streams.py streemcam/access.py tests/test_db.py tests/test_streams.py tests/test_access.py
git commit -m "feat: whitelist store, stream registry and access policy"
```

---

### Task 5: Монитор камер (онлайн-статус, кэш снимков, алерты)

**Files:**
- Create: `streemcam/monitor.py`
- Test: `tests/test_monitor.py`

**Interfaces:**
- Consumes: `Config`.
- Produces: `CameraMonitor(cfg, http: httpx.AsyncClient, on_alert: Callable[[str, bool], Awaitable[None]] | None = None, clock=time.monotonic)`; методы `async probe(cam_id)`, `async probe_all()`, `async run()` (бесконечный цикл), `is_online(cam_id) -> bool | None` (`None` — ещё не проверялась), `snapshot(cam_id) -> bytes | None`.
- `on_alert(cam_id, False)` — один раз, когда камера офлайн ≥ `offline_alert_minutes`; `on_alert(cam_id, True)` — когда такая камера снова онлайн.
- Проба: `GET {go2rtc}/api/frame.jpeg?src=<cam>`; тот же JPEG служит превью.

- [ ] **Step 1: Падающие тесты**

`tests/test_monitor.py`:
```python
import httpx
import pytest

from streemcam.monitor import CameraMonitor


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def client(up: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/frame.jpeg"
        cam = request.url.params["src"]
        if up.get(cam) == "error":
            raise httpx.ConnectError("refused")
        if up.get(cam):
            return httpx.Response(200, content=b"JPEG-" + cam.encode())
        return httpx.Response(500)
    return httpx.AsyncClient(base_url="http://go2rtc", transport=httpx.MockTransport(handler))


@pytest.fixture
def alerts():
    return []


def make(cfg, up, alerts, clock):
    async def on_alert(cam_id, online):
        alerts.append((cam_id, online))
    return CameraMonitor(cfg, client(up), on_alert, clock=clock)


async def test_probe_all_sets_status_and_snapshot(cfg, alerts):
    m = make(cfg, {"yard": True, "gate": False, "room": "error"}, alerts, Clock())
    assert m.is_online("yard") is None
    await m.probe_all()
    assert m.is_online("yard") is True
    assert m.snapshot("yard") == b"JPEG-yard"
    assert m.is_online("gate") is False
    assert m.is_online("room") is False
    assert m.snapshot("gate") is None
    assert m.is_online("unknown") is None


async def test_alert_once_after_threshold_then_recovery(cfg, alerts):
    up = {"yard": False}
    clock = Clock()
    m = make(cfg, up, alerts, clock)
    await m.probe("yard")
    clock.t = 299
    await m.probe("yard")
    assert alerts == []
    clock.t = 300
    await m.probe("yard")
    assert alerts == [("yard", False)]
    clock.t = 400
    await m.probe("yard")
    assert alerts == [("yard", False)]
    up["yard"] = True
    await m.probe("yard")
    assert alerts == [("yard", False), ("yard", True)]


async def test_short_outage_no_alert(cfg, alerts):
    up = {"yard": False}
    clock = Clock()
    m = make(cfg, up, alerts, clock)
    await m.probe("yard")
    up["yard"] = True
    clock.t = 100
    await m.probe("yard")
    assert alerts == []


async def test_snapshot_kept_while_offline(cfg, alerts):
    up = {"yard": True}
    m = make(cfg, up, alerts, Clock())
    await m.probe("yard")
    up["yard"] = False
    await m.probe("yard")
    assert m.snapshot("yard") == b"JPEG-yard"
```

- [ ] **Step 2: Убедиться, что падает**

Run: `python -m pytest tests/test_monitor.py -v` → ImportError.

- [ ] **Step 3: Реализация**

`streemcam/monitor.py`:
```python
import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from .config import Config

log = logging.getLogger(__name__)

AlertFn = Callable[[str, bool], Awaitable[None]]


@dataclass
class _State:
    online: bool | None = None
    offline_since: float | None = None
    alerted: bool = False
    snapshot: bytes | None = None


class CameraMonitor:
    def __init__(self, cfg: Config, http: httpx.AsyncClient, on_alert: AlertFn | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.cfg = cfg
        self._http = http
        self._on_alert = on_alert
        self._clock = clock
        self._state: dict[str, _State] = {cam.id: _State() for cam in cfg.cameras}

    def is_online(self, cam_id: str) -> bool | None:
        state = self._state.get(cam_id)
        return state.online if state else None

    def snapshot(self, cam_id: str) -> bytes | None:
        state = self._state.get(cam_id)
        return state.snapshot if state else None

    async def probe(self, cam_id: str) -> None:
        state = self._state[cam_id]
        image = None
        try:
            r = await self._http.get("/api/frame.jpeg", params={"src": cam_id}, timeout=20)
            if r.status_code == 200 and r.content:
                image = r.content
        except httpx.HTTPError as e:
            log.debug("probe %s failed: %s", cam_id, e)

        now = self._clock()
        if image is not None:
            state.online = True
            state.offline_since = None
            state.snapshot = image
            if state.alerted:
                state.alerted = False
                await self._alert(cam_id, True)
            return

        if state.online is not False:
            log.warning("camera %s is offline", cam_id)
        state.online = False
        if state.offline_since is None:
            state.offline_since = now
        if not state.alerted and now - state.offline_since >= self.cfg.offline_alert_minutes * 60:
            state.alerted = True
            await self._alert(cam_id, False)

    async def probe_all(self) -> None:
        await asyncio.gather(*(self.probe(cam.id) for cam in self.cfg.cameras))

    async def run(self) -> None:
        while True:
            await self.probe_all()
            await asyncio.sleep(self.cfg.probe_interval_seconds)

    async def _alert(self, cam_id: str, online: bool) -> None:
        if self._on_alert is None:
            return
        try:
            await self._on_alert(cam_id, online)
        except Exception:
            log.exception("alert for %s failed", cam_id)
```

- [ ] **Step 4: Тесты проходят**

Run: `python -m pytest tests/test_monitor.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add streemcam/monitor.py tests/test_monitor.py
git commit -m "feat: camera monitor with snapshot cache and offline alerts"
```

---

### Task 6: Веб-шлюз — авторизация и REST

**Files:**
- Create: `streemcam/web/__init__.py` (пустой), `streemcam/web/server.py`
- Modify: `tests/conftest.py` (добавить веб-фикстуры)
- Test: `tests/test_web_api.py`

**Interfaces:**
- Consumes: `Access`, `AccessDenied`, `TokenError`, `TgAuthError`, `StreamRegistry`, `Config`; монитор — любой объект с `is_online(cam_id)` и `snapshot(cam_id)`.
- Produces: `create_app(cfg, access, monitor, registry, http: httpx.AsyncClient, upstream_connect=None) -> FastAPI`.
  - `POST /api/tg/session` `{init_data}` → `200 {token}` | `401 {error:"invalid_init_data"}` | `403 {error:"not_allowed", user_id}`
  - `POST /api/link/redeem` `{t}` → `200 {token}` | `401 {error:"invalid_link"}` | `403 {error:"not_allowed", user_id}`
  - `GET /api/cameras` (сессия) → `{player_mode, max_streams, cameras:[{id,name,online}]}`
  - `GET /api/snapshot/{cam_id}` (сессия) → `image/jpeg` | 404
  - `GET /go2rtc/{name}` — прокси только `video-rtc.js`, `video-stream.js` из go2rtc.
  - Сессия: заголовок `Authorization: Bearer <token>` или query `s=<token>`; нет/битая → 401, пользователь удалён → 403.
- `upstream_connect` используется в Task 7; здесь только принимается параметр.

- [ ] **Step 1: Добавить веб-фикстуры в conftest**

Дописать в конец `tests/conftest.py`:
```python
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient

from streemcam.access import Access
from streemcam.db import Store
from streemcam.streams import StreamRegistry
from streemcam.web.server import create_app


class StubMonitor:
    def __init__(self):
        self.online = {"yard": True, "gate": False}
        self.snaps = {"yard": b"JPEG"}

    def is_online(self, cam_id):
        return self.online.get(cam_id)

    def snapshot(self, cam_id):
        return self.snaps.get(cam_id)


class FakeUpstream:
    """Эхо-заменитель WebSocket go2rtc: текст -> 'echo:<текст>', байты -> те же байты."""

    def __init__(self, src):
        self.src = src
        self.queue = asyncio.Queue()

    async def send(self, message):
        await self.queue.put(f"echo:{message}" if isinstance(message, str) else message)

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.queue.get()


@asynccontextmanager
async def fake_connect(src):
    yield FakeUpstream(src)


def _go2rtc_http(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/video-stream.js":
        return httpx.Response(200, text="// video-stream")
    return httpx.Response(404)


@pytest.fixture
def make_web():
    def make(cfg):
        registry = StreamRegistry(cfg.max_streams_per_user)
        access = Access(cfg, Store(":memory:"), registry)
        http = httpx.AsyncClient(base_url="http://go2rtc", transport=httpx.MockTransport(_go2rtc_http))
        app = create_app(cfg, access, StubMonitor(), registry, http, upstream_connect=fake_connect)
        return SimpleNamespace(app=app, access=access, registry=registry)
    return make


@pytest.fixture
def web(make_web, cfg):
    return make_web(cfg)
```

- [ ] **Step 2: Падающие тесты API**

`tests/test_web_api.py`:
```python
import time

from fastapi.testclient import TestClient

from streemcam.identity import Identity
from tg_helpers import make_init_data

USER = Identity("tg", 42)


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_tg_session_and_cameras(web):
    client = TestClient(web.app)
    r = client.post("/api/tg/session",
                    json={"init_data": make_init_data("123456:TEST", 42, int(time.time()))})
    assert r.status_code == 200
    token = r.json()["token"]

    r = client.get("/api/cameras", headers=bearer(token))
    assert r.status_code == 200
    body = r.json()
    assert body["player_mode"] == "mse"
    assert body["max_streams"] == 4
    assert body["cameras"] == [
        {"id": "yard", "name": "Двор", "online": True},
        {"id": "gate", "name": "Ворота", "online": False},
        {"id": "room", "name": "Комната", "online": None},
    ]


def test_tg_session_not_allowed_returns_id(web):
    client = TestClient(web.app)
    r = client.post("/api/tg/session",
                    json={"init_data": make_init_data("123456:TEST", 999, int(time.time()))})
    assert r.status_code == 403
    assert r.json() == {"error": "not_allowed", "user_id": 999}


def test_tg_session_invalid(web):
    r = TestClient(web.app).post("/api/tg/session", json={"init_data": "garbage"})
    assert r.status_code == 401


def test_link_redeem_once(web):
    client = TestClient(web.app)
    t = web.access.issue_link_token(Identity("dc", 77))
    r = client.post("/api/link/redeem", json={"t": t})
    assert r.status_code == 200
    assert client.get("/api/cameras", headers=bearer(r.json()["token"])).status_code == 200
    assert client.post("/api/link/redeem", json={"t": t}).status_code == 401


async def test_link_redeem_denied_user(web):
    t = web.access.issue_link_token(USER)
    await web.access.deny(USER)
    r = TestClient(web.app).post("/api/link/redeem", json={"t": t})
    assert r.status_code == 403
    assert r.json()["user_id"] == 42


def test_cameras_requires_session(web):
    client = TestClient(web.app)
    assert client.get("/api/cameras").status_code == 401
    assert client.get("/api/cameras", headers=bearer("bad.token")).status_code == 401


def test_session_via_query_param(web):
    token = web.access.issue_session(USER)
    assert TestClient(web.app).get(f"/api/cameras?s={token}").status_code == 200


async def test_session_after_deny_is_forbidden(web):
    token = web.access.issue_session(USER)
    await web.access.deny(USER)
    assert TestClient(web.app).get("/api/cameras", headers=bearer(token)).status_code == 403


def test_snapshot(web):
    client = TestClient(web.app)
    h = bearer(web.access.issue_session(USER))
    r = client.get("/api/snapshot/yard", headers=h)
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert r.content == b"JPEG"
    assert client.get("/api/snapshot/gate", headers=h).status_code == 404
    assert client.get("/api/snapshot/nope", headers=h).status_code == 404
    assert client.get("/api/snapshot/yard").status_code == 401


def test_go2rtc_js_proxy_whitelist(web):
    client = TestClient(web.app)
    r = client.get("/go2rtc/video-stream.js")
    assert r.status_code == 200
    assert r.text == "// video-stream"
    assert "javascript" in r.headers["content-type"]
    assert client.get("/go2rtc/api.js").status_code == 404
```

- [ ] **Step 3: Убедиться, что падает**

Run: `python -m pytest tests/test_web_api.py -v` → ImportError `streemcam.web.server`.

- [ ] **Step 4: Реализация**

`streemcam/web/server.py`:
```python
import logging

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from ..access import Access, AccessDenied
from ..config import Config
from ..identity import Identity
from ..streams import StreamRegistry
from ..tg_auth import TgAuthError
from ..tokens import TokenError

log = logging.getLogger(__name__)

GO2RTC_JS = {"video-rtc.js", "video-stream.js"}


class TgSessionIn(BaseModel):
    init_data: str


class LinkIn(BaseModel):
    t: str


def _denied(ident: Identity) -> JSONResponse:
    return JSONResponse({"error": "not_allowed", "user_id": ident.user_id}, status_code=403)


def create_app(cfg: Config, access: Access, monitor, registry: StreamRegistry,
               http: httpx.AsyncClient, upstream_connect=None) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def current_user(request: Request, s: str | None = None) -> Identity:
        token = s
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
        if not token:
            raise HTTPException(401, "no session")
        try:
            return access.check_session(token)
        except TokenError:
            raise HTTPException(401, "invalid session") from None
        except AccessDenied:
            raise HTTPException(403, "not allowed") from None

    @app.post("/api/tg/session")
    async def tg_session(body: TgSessionIn):
        try:
            ident = access.login_telegram(body.init_data)
        except TgAuthError:
            return JSONResponse({"error": "invalid_init_data"}, status_code=401)
        except AccessDenied as e:
            return _denied(e.identity)
        return {"token": access.issue_session(ident)}

    @app.post("/api/link/redeem")
    async def link_redeem(body: LinkIn):
        try:
            ident = access.redeem_link_token(body.t)
        except TokenError:
            return JSONResponse({"error": "invalid_link"}, status_code=401)
        except AccessDenied as e:
            return _denied(e.identity)
        return {"token": access.issue_session(ident)}

    @app.get("/api/cameras")
    async def cameras(user: Identity = Depends(current_user)):
        return {
            "player_mode": cfg.player_mode,
            "max_streams": cfg.max_streams_per_user,
            "cameras": [{"id": c.id, "name": c.name, "online": monitor.is_online(c.id)}
                        for c in cfg.cameras],
        }

    @app.get("/api/snapshot/{cam_id}")
    async def snapshot(cam_id: str, user: Identity = Depends(current_user)):
        data = monitor.snapshot(cam_id) if cfg.camera(cam_id) else None
        if data is None:
            raise HTTPException(404, "no snapshot")
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/go2rtc/{name}")
    async def go2rtc_js(name: str):
        if name not in GO2RTC_JS:
            raise HTTPException(404)
        r = await http.get(f"/{name}")
        if r.status_code != 200:
            raise HTTPException(502, "go2rtc unavailable")
        return Response(r.content, media_type="application/javascript",
                        headers={"Cache-Control": "public, max-age=3600"})

    return app
```

- [ ] **Step 5: Тесты проходят**

Run: `python -m pytest -v` → PASS.

- [ ] **Step 6: Commit**

```bash
git add streemcam/web tests/conftest.py tests/test_web_api.py
git commit -m "feat: web gateway auth and REST endpoints"
```

---

### Task 7: Веб-шлюз — WebSocket-прокси к go2rtc

**Files:**
- Modify: `streemcam/web/server.py`
- Test: `tests/test_web_ws.py`

**Interfaces:**
- Consumes: `create_app` из Task 6; `upstream_connect(src: str)` — асинхронный контекст-менеджер, отдающий объект с `async send(str | bytes)` и асинхронной итерацией входящих `str | bytes` (как `websockets.asyncio.client.ClientConnection`).
- Produces: `WS /api/ws?src=<cam>&s=<session>`; коды закрытия: `4401` нет/битая сессия, `4403` нет доступа или выгнан через `/deny`, `4404` неизвестная камера, `4429` превышен лимит потоков, `1011` ошибка go2rtc.
- Produces: `default_upstream(go2rtc_url: str)` — фабрика для прод-режима.

- [ ] **Step 1: Падающие тесты**

`tests/test_web_ws.py`:
```python
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from streemcam.identity import Identity

USER = Identity("tg", 42)


def ws_url(web, src="yard", ident=USER):
    return f"/api/ws?src={src}&s={web.access.issue_session(ident)}"


def test_proxies_text_and_bytes(web):
    with TestClient(web.app) as client:
        with client.websocket_connect(ws_url(web)) as ws:
            ws.send_text('{"type":"mse"}')
            assert ws.receive_text() == 'echo:{"type":"mse"}'
            ws.send_bytes(b"\x00\x01")
            assert ws.receive_bytes() == b"\x00\x01"


def test_releases_stream_after_disconnect(web):
    with TestClient(web.app) as client:
        with client.websocket_connect(ws_url(web)) as ws:
            ws.send_text("x")
            ws.receive_text()
            assert web.registry.count(USER) == 1
        assert web.registry.count(USER) == 0


@pytest.mark.parametrize("query,code", [
    ("src=yard", 4401),
    ("src=yard&s=bad.token", 4401),
])
def test_rejects_without_session(web, query, code):
    with TestClient(web.app) as client:
        with pytest.raises(WebSocketDisconnect) as e:
            with client.websocket_connect(f"/api/ws?{query}"):
                pass
        assert e.value.code == code


def test_rejects_unknown_camera(web):
    with TestClient(web.app) as client:
        with pytest.raises(WebSocketDisconnect) as e:
            with client.websocket_connect(ws_url(web, src="nope")):
                pass
        assert e.value.code == 4404


def test_stream_limit(make_web, make_cfg):
    web = make_web(make_cfg(max_streams_per_user=1))
    with TestClient(web.app) as client:
        with client.websocket_connect(ws_url(web)) as ws:
            ws.send_text("x")
            ws.receive_text()
            with pytest.raises(WebSocketDisconnect) as e:
                with client.websocket_connect(ws_url(web, src="gate")):
                    pass
            assert e.value.code == 4429


def test_deny_kicks_open_stream(web):
    with TestClient(web.app) as client:
        with client.websocket_connect(ws_url(web)) as ws:
            ws.send_text("x")
            ws.receive_text()
            client.portal.call(web.access.deny, USER)
            with pytest.raises(WebSocketDisconnect) as e:
                ws.receive_text()
            assert e.value.code == 4403
```

- [ ] **Step 2: Убедиться, что падает**

Run: `python -m pytest tests/test_web_ws.py -v`
Expected: FAIL (маршрута `/api/ws` нет — WebSocketDisconnect с кодом 1000 или 403-подобный отказ, коды не совпадают).

- [ ] **Step 3: Реализация**

В `streemcam/web/server.py` добавить импорты:
```python
import asyncio
import contextlib
from urllib.parse import urlencode

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import WebSocketException as UpstreamError

from ..streams import StreamLimitError
```

Добавить функции уровня модуля:
```python
def default_upstream(go2rtc_url: str):
    ws_base = "ws" + go2rtc_url[4:] if go2rtc_url.startswith("http") else go2rtc_url

    def connect(src: str):
        return ws_connect(f"{ws_base}/api/ws?{urlencode({'src': src})}", max_size=None)
    return connect


async def _pump(ws: WebSocket, upstream, kicked: asyncio.Event) -> None:
    async def client_to_upstream():
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                return
            if msg.get("text") is not None:
                await upstream.send(msg["text"])
            elif msg.get("bytes") is not None:
                await upstream.send(msg["bytes"])

    async def upstream_to_client():
        async for msg in upstream:
            if isinstance(msg, bytes):
                await ws.send_bytes(msg)
            else:
                await ws.send_text(msg)

    tasks = [asyncio.create_task(client_to_upstream()),
             asyncio.create_task(upstream_to_client()),
             asyncio.create_task(kicked.wait())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            if not t.cancelled() and t.exception() is not None:
                log.debug("stream pump ended: %r", t.exception())
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
```

Внутри `create_app`: сразу после `app = FastAPI(...)` добавить
```python
    upstream_connect = upstream_connect or default_upstream(cfg.go2rtc_url)
```
и перед `return app` добавить маршрут:
```python
    @app.websocket("/api/ws")
    async def ws_proxy(ws: WebSocket, src: str = "", s: str = ""):
        try:
            ident = access.check_session(s)
        except TokenError:
            await ws.close(code=4401)
            return
        except AccessDenied:
            await ws.close(code=4403)
            return
        if cfg.camera(src) is None:
            await ws.close(code=4404)
            return

        kicked = asyncio.Event()

        async def closer():
            kicked.set()

        try:
            registry.acquire(ident, closer)
        except StreamLimitError:
            await ws.close(code=4429)
            return

        close_code = 1000
        try:
            await ws.accept()
            async with upstream_connect(src) as upstream:
                await _pump(ws, upstream, kicked)
            if kicked.is_set():
                close_code = 4403
        except (WebSocketDisconnect, UpstreamError, OSError) as e:
            log.info("stream %s for %s ended: %r", src, ident, e)
            close_code = 1011
        finally:
            registry.release(ident, closer)
        with contextlib.suppress(Exception):
            await ws.close(code=close_code)
```

- [ ] **Step 4: Тесты проходят**

Run: `python -m pytest -v` → PASS.
Если `test_deny_kicks_open_stream` зависает: убедиться, что `closer` только ставит `kicked`, а `_pump` ждёт `kicked.wait()` — закрытие делает сам обработчик.

- [ ] **Step 5: Commit**

```bash
git add streemcam/web/server.py tests/test_web_ws.py
git commit -m "feat: authorized WebSocket proxy to go2rtc with stream limits"
```

---

### Task 8: Плеер (фронтенд)

**Files:**
- Create: `streemcam/web/static/index.html`, `streemcam/web/static/app.js`, `streemcam/web/static/style.css`
- Modify: `streemcam/web/server.py` (маршрут `/` и статика)
- Test: `tests/test_web_static.py`

**Interfaces:**
- Consumes: API из Task 6/7; `video-stream.js` go2rtc (кастомный элемент `<video-stream>`, свойства `mode`, `src`; сам переподключается).
- Поведение страницы:
  1. В Telegram (`Telegram.WebApp.initData` непустой) → `POST /api/tg/session`.
  2. Иначе при `?t=` → `POST /api/link/redeem`, `t` убирается из адреса.
  3. Иначе токен из `sessionStorage`.
  4. Список камер с превью; `#cam=<id>` сразу открывает камеру; кнопка «Сетка» показывает до `max_streams` камер одновременно; полноэкранный режим.
  5. Раз в 60 с перезапрашивает `/api/cameras`; на 401/403 — сообщение и остановка плееров.

- [ ] **Step 1: Падающий тест**

`tests/test_web_static.py`:
```python
from fastapi.testclient import TestClient


def test_index_and_assets(web):
    client = TestClient(web.app)
    r = client.get("/")
    assert r.status_code == 200
    assert "/static/app.js" in r.text
    assert "telegram-web-app.js" in r.text
    js = client.get("/static/app.js")
    assert js.status_code == 200
    assert "/go2rtc/video-stream.js" in js.text
    assert client.get("/static/style.css").status_code == 200
```

Run: `python -m pytest tests/test_web_static.py -v` → FAIL (404).

- [ ] **Step 2: index.html**

`streemcam/web/static/index.html`:
```html
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>Камеры</title>
  <script src="https://telegram.org/js/telegram-web-app.js"></script>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
  <header>
    <button id="back" hidden>← Назад</button>
    <h1 id="title">Камеры</h1>
    <button id="grid" hidden>Сетка</button>
  </header>
  <main>
    <p id="message" hidden></p>
    <ul id="cams" hidden></ul>
    <section id="viewer" hidden></section>
  </main>
  <script type="module" src="/static/app.js"></script>
</body>
</html>
```

- [ ] **Step 3: app.js**

`streemcam/web/static/app.js`:
```js
import "/go2rtc/video-stream.js";

const $ = (sel) => document.querySelector(sel);
const tg = window.Telegram?.WebApp;
let token = null;
let info = null;

function store(key, value) {
  try { sessionStorage.setItem(key, value); } catch {}
}
function load(key) {
  try { return sessionStorage.getItem(key); } catch { return null; }
}

function showMessage(text) {
  $("#message").textContent = text;
  $("#message").hidden = false;
  $("#cams").hidden = true;
  $("#viewer").hidden = true;
  $("#viewer").replaceChildren();
  $("#back").hidden = true;
  $("#grid").hidden = true;
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (token) headers.Authorization = `Bearer ${token}`;
  return fetch(path, { ...options, headers });
}

async function login() {
  const params = new URLSearchParams(location.search);
  let r;
  if (tg && tg.initData) {
    tg.ready();
    tg.expand();
    r = await api("/api/tg/session", { method: "POST", body: JSON.stringify({ init_data: tg.initData }) });
  } else if (params.get("t")) {
    r = await api("/api/link/redeem", { method: "POST", body: JSON.stringify({ t: params.get("t") }) });
    history.replaceState(null, "", location.pathname + location.hash);
  } else {
    token = load("sc_token");
    if (!token) showMessage("Откройте камеры через бота: /cams в Telegram или Discord.");
    return Boolean(token);
  }
  const data = await r.json().catch(() => ({}));
  if (r.ok) {
    token = data.token;
    store("sc_token", token);
    return true;
  }
  if (r.status === 403) {
    showMessage(`Нет доступа. Ваш ID: ${data.user_id}. Передайте его администратору.`);
  } else {
    showMessage("Ссылка недействительна или устарела. Запросите новую командой /cams.");
  }
  return false;
}

async function refresh() {
  const r = await api("/api/cameras");
  if (r.status === 401 || r.status === 403) {
    token = null;
    store("sc_token", "");
    showMessage(r.status === 403 ? "Доступ отозван." : "Сессия истекла. Запросите новую ссылку командой /cams.");
    return false;
  }
  if (!r.ok) return true;
  info = await r.json();
  return true;
}

function snapshotUrl(id) {
  return `/api/snapshot/${encodeURIComponent(id)}?s=${encodeURIComponent(token)}&_=${Date.now()}`;
}

function streamUrl(id) {
  return `${location.origin}/api/ws?src=${encodeURIComponent(id)}&s=${encodeURIComponent(token)}`;
}

function statusText(online) {
  return online === true ? "онлайн" : online === false ? "офлайн" : "проверяется";
}

function renderList() {
  const list = $("#cams");
  list.replaceChildren(...info.cameras.map((cam) => {
    const li = document.createElement("li");
    li.className = `cam ${cam.online === false ? "offline" : ""}`;
    const img = document.createElement("img");
    img.alt = cam.name;
    img.src = snapshotUrl(cam.id);
    img.onerror = () => { img.removeAttribute("src"); };
    const caption = document.createElement("div");
    caption.innerHTML = `<b></b><span class="status"></span>`;
    caption.querySelector("b").textContent = cam.name;
    caption.querySelector(".status").textContent = statusText(cam.online);
    li.append(img, caption);
    li.onclick = () => openCameras([cam]);
    return li;
  }));
  $("#message").hidden = true;
  $("#viewer").hidden = true;
  $("#viewer").replaceChildren();
  list.hidden = false;
  $("#back").hidden = true;
  $("#grid").hidden = info.cameras.length < 2;
  $("#title").textContent = "Камеры";
}

function player(cam) {
  const box = document.createElement("div");
  box.className = "player";
  const video = document.createElement("video-stream");
  video.mode = info.player_mode;
  video.src = streamUrl(cam.id);
  const bar = document.createElement("div");
  bar.className = "bar";
  const name = document.createElement("span");
  name.textContent = cam.online === false ? `${cam.name} — камера офлайн` : cam.name;
  const retry = document.createElement("button");
  retry.textContent = "↻";
  retry.title = "Повторить";
  // VideoRTC игнорирует новый src, пока открыт старый WebSocket — сначала рвём соединение
  retry.onclick = () => { video.ondisconnect(); video.src = streamUrl(cam.id); };
  const full = document.createElement("button");
  full.textContent = "⛶";
  full.title = "Во весь экран";
  full.onclick = () => (box.requestFullscreen?.() ?? box.webkitRequestFullscreen?.());
  bar.append(name, retry, full);
  box.append(video, bar);
  return box;
}

function openCameras(cams) {
  const viewer = $("#viewer");
  viewer.className = cams.length > 1 ? "grid" : "single";
  viewer.replaceChildren(...cams.map(player));
  viewer.hidden = false;
  $("#cams").hidden = true;
  $("#back").hidden = false;
  $("#grid").hidden = true;
  $("#title").textContent = cams.length > 1 ? "Сетка" : cams[0].name;
  if (cams.length === 1) history.replaceState(null, "", `#cam=${cams[0].id}`);
}

$("#back").onclick = () => { history.replaceState(null, "", location.pathname); renderList(); };
$("#grid").onclick = () => openCameras(info.cameras.slice(0, info.max_streams));

async function main() {
  if (!(await login())) return;
  if (!(await refresh())) return;
  const wanted = new URLSearchParams(location.hash.slice(1)).get("cam");
  const cam = info.cameras.find((c) => c.id === wanted);
  cam ? openCameras([cam]) : renderList();
  setInterval(async () => {
    if (!token) return;
    const ok = await refresh();
    if (ok && !$("#cams").hidden) renderList();
  }, 60000);
}

main();
```

- [ ] **Step 4: style.css**

`streemcam/web/static/style.css`:
```css
:root {
  --bg: var(--tg-theme-bg-color, #0f1115);
  --fg: var(--tg-theme-text-color, #e8e8ea);
  --muted: var(--tg-theme-hint-color, #8a8f98);
  --card: var(--tg-theme-secondary-bg-color, #1a1d23);
  --accent: var(--tg-theme-button-color, #3b82f6);
  --accent-fg: var(--tg-theme-button-text-color, #fff);
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg); font: 15px/1.4 system-ui, sans-serif; }
header { display: flex; align-items: center; gap: 8px; padding: 10px 16px; position: sticky; top: 0; background: var(--bg); z-index: 1; }
header h1 { flex: 1; margin: 0; font-size: 17px; }
button { background: var(--accent); color: var(--accent-fg); border: 0; border-radius: 8px; padding: 6px 12px; font: inherit; cursor: pointer; }
main { padding: 0 16px 16px; }
#message { color: var(--muted); text-align: center; margin-top: 40px; }
#cams { list-style: none; margin: 0; padding: 0; display: grid; gap: 12px; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); }
.cam { background: var(--card); border-radius: 12px; overflow: hidden; cursor: pointer; }
.cam img { display: block; width: 100%; aspect-ratio: 16 / 9; object-fit: cover; background: #000; }
.cam div { display: flex; justify-content: space-between; padding: 8px 12px; }
.cam .status { color: var(--muted); }
.cam.offline img { opacity: .4; }
#viewer.single .player { max-width: 1280px; margin: 0 auto; }
#viewer.grid { display: grid; gap: 8px; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); }
.player { background: #000; border-radius: 12px; overflow: hidden; }
.player video-stream { display: block; width: 100%; aspect-ratio: 16 / 9; }
.player video { width: 100%; height: 100%; object-fit: contain; }
.player .bar { display: flex; gap: 8px; align-items: center; padding: 6px 10px; background: var(--card); }
.player .bar span { flex: 1; }
.player:fullscreen video-stream { height: 100%; aspect-ratio: auto; }
.player:fullscreen .bar { position: absolute; bottom: 0; left: 0; right: 0; opacity: .8; }
```

- [ ] **Step 5: Подключить статику в server.py**

В `streemcam/web/server.py` добавить импорты:
```python
from pathlib import Path

from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
```
константу рядом с `GO2RTC_JS`:
```python
STATIC_DIR = Path(__file__).parent / "static"
```
и внутри `create_app` перед `return app`:
```python
    @app.get("/")
    async def index():
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
```

- [ ] **Step 6: Тесты проходят**

Run: `python -m pytest -v` → PASS.

- [ ] **Step 7: Commit**

```bash
git add streemcam/web tests/test_web_static.py
git commit -m "feat: camera player page (Telegram Mini App + browser)"
```

---

### Task 9: Telegram-бот

**Files:**
- Create: `streemcam/admin.py`, `streemcam/tg/__init__.py` (пустой), `streemcam/tg/bot.py`
- Test: `tests/test_admin.py`, `tests/test_tg_bot.py`

**Interfaces:**
- Consumes: `Access`, `Config`, `Identity`.
- Produces:
  - `admin.parse_target(arg: str, default_platform: Platform) -> Identity` (`ValueError` при мусоре); понимает `123`, `tg:123`, `dc:123`, `<@123>`, `<@!123>` (упоминание → dc).
  - `admin.format_users(idents: list[Identity]) -> str`, `admin.USAGE: str`.
  - `tg.bot.TgHandlers(cfg, access, mini_app_link)` — методы `cams`, `allow`, `deny`, `users`, `post_channel`.
  - `tg.bot.build_router(handlers) -> aiogram.Router`, `async notify_admins(bot, cfg, text)`, `async run_telegram(bot, cfg, access)`.

- [ ] **Step 1: Падающие тесты admin**

`tests/test_admin.py`:
```python
import pytest

from streemcam.admin import format_users, parse_target
from streemcam.identity import Identity


@pytest.mark.parametrize("arg,default,expected", [
    ("123", "tg", Identity("tg", 123)),
    (" 123 ", "dc", Identity("dc", 123)),
    ("dc:5", "tg", Identity("dc", 5)),
    ("tg:9", "dc", Identity("tg", 9)),
    ("<@55>", "tg", Identity("dc", 55)),
    ("<@!55>", "tg", Identity("dc", 55)),
])
def test_parse_target(arg, default, expected):
    assert parse_target(arg, default) == expected


@pytest.mark.parametrize("arg", ["", "abc", "xx:1", "tg:", "tg:-1", "@user"])
def test_parse_target_rejects(arg):
    with pytest.raises(ValueError):
        parse_target(arg, "tg")


def test_format_users():
    assert format_users([]) == "Белый список пуст."
    assert format_users([Identity("tg", 1), Identity("dc", 2)]) == "Белый список:\ntg:1\ndc:2"
```

- [ ] **Step 2: Реализация admin.py**

`streemcam/admin.py`:
```python
import re

from .identity import Identity, Platform

_MENTION = re.compile(r"^<@!?(\d+)>$")

USAGE = ("Использование: /allow <id> | tg:<id> | dc:<id> "
         "(или ответом на сообщение пользователя). То же для /deny.")


def parse_target(arg: str, default_platform: Platform) -> Identity:
    arg = arg.strip()
    mention = _MENTION.match(arg)
    if mention:
        return Identity("dc", int(mention.group(1)))
    platform = default_platform
    if ":" in arg:
        prefix, arg = arg.split(":", 1)
        if prefix not in ("tg", "dc"):
            raise ValueError(f"unknown platform: {prefix}")
        platform = prefix
    if not arg.isdigit():
        raise ValueError(f"not a user id: {arg!r}")
    return Identity(platform, int(arg))


def format_users(idents: list[Identity]) -> str:
    if not idents:
        return "Белый список пуст."
    return "Белый список:\n" + "\n".join(str(i) for i in idents)
```

Run: `python -m pytest tests/test_admin.py -v` → PASS.

- [ ] **Step 3: Падающие тесты Telegram-обработчиков**

`tests/test_tg_bot.py`:
```python
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.filters import CommandObject

from streemcam.access import Access
from streemcam.db import Store
from streemcam.identity import Identity
from streemcam.streams import StreamRegistry
from streemcam.tg.bot import TgHandlers

LINK = "https://t.me/cams_bot/cams"


def make_msg(user_id=42, chat_type="private", reply_user=None):
    msg = MagicMock()
    msg.from_user.id = user_id
    msg.chat.type = chat_type
    msg.answer = AsyncMock()
    msg.bot.send_message = AsyncMock()
    if reply_user is None:
        msg.reply_to_message = None
    else:
        msg.reply_to_message.from_user.id = reply_user
    return msg


def cmd(name, args=None):
    return CommandObject(prefix="/", command=name, args=args)


def text_of(msg):
    return msg.answer.call_args.args[0]


def button_of(msg):
    return msg.answer.call_args.kwargs["reply_markup"].inline_keyboard[0][0]


@pytest.fixture
def access(cfg):
    return Access(cfg, Store(":memory:"), StreamRegistry(4))


@pytest.fixture
def h(cfg, access):
    return TgHandlers(cfg, access, LINK)


async def test_cams_private_opens_web_app(h):
    msg = make_msg()
    await h.cams(msg)
    assert button_of(msg).web_app.url == "https://cams.example.com"


async def test_cams_group_uses_direct_link(h):
    msg = make_msg(chat_type="supergroup")
    await h.cams(msg)
    assert button_of(msg).url == LINK


async def test_cams_denied_shows_id(h):
    msg = make_msg(user_id=999)
    await h.cams(msg)
    assert "999" in text_of(msg)
    assert "reply_markup" not in msg.answer.call_args.kwargs


async def test_allow_by_admin_with_arg(h, access):
    msg = make_msg(user_id=1)
    await h.allow(msg, cmd("allow", "dc:5"))
    assert access.is_allowed(Identity("dc", 5))
    assert "dc:5" in text_of(msg)


async def test_allow_by_reply(h, access):
    msg = make_msg(user_id=1, reply_user=555)
    await h.allow(msg, cmd("allow"))
    assert access.is_allowed(Identity("tg", 555))


async def test_allow_bad_arg_shows_usage(h):
    msg = make_msg(user_id=1)
    await h.allow(msg, cmd("allow", "abc"))
    assert "Использование" in text_of(msg)


async def test_allow_by_non_admin_rejected(h, access):
    msg = make_msg(user_id=42)
    await h.allow(msg, cmd("allow", "5"))
    assert not access.is_allowed(Identity("tg", 5))
    assert "администратор" in text_of(msg)


async def test_deny(h, access):
    msg = make_msg(user_id=1)
    await h.deny(msg, cmd("deny", "42"))
    assert not access.is_allowed(Identity("tg", 42))


async def test_users(h):
    msg = make_msg(user_id=1)
    await h.users(msg)
    assert "tg:42" in text_of(msg) and "dc:77" in text_of(msg)


async def test_post_channel(h):
    msg = make_msg(user_id=1)
    await h.post_channel(msg)
    call = msg.bot.send_message.call_args
    assert call.args[0] == -100500
    assert call.kwargs["reply_markup"].inline_keyboard[0][0].url == LINK


async def test_post_channel_without_channel(make_cfg, access):
    h = TgHandlers(make_cfg(telegram={"bot_token": "1:X"}), access, LINK)
    msg = make_msg(user_id=1)
    await h.post_channel(msg)
    assert "channel_id" in text_of(msg)
    msg.bot.send_message.assert_not_called()
```

Run: `python -m pytest tests/test_tg_bot.py -v` → ImportError.

- [ ] **Step 4: Реализация tg/bot.py**

`streemcam/tg/bot.py`:
```python
import logging

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo

from ..access import Access
from ..admin import USAGE, format_users, parse_target
from ..config import Config
from ..identity import Identity

log = logging.getLogger(__name__)

BUTTON_TEXT = "📹 Камеры"


class TgHandlers:
    def __init__(self, cfg: Config, access: Access, mini_app_link: str):
        self.cfg = cfg
        self.access = access
        self.mini_app_link = mini_app_link

    @staticmethod
    def _ident(message: Message) -> Identity | None:
        return Identity("tg", message.from_user.id) if message.from_user else None

    def _link_markup(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=BUTTON_TEXT, url=self.mini_app_link)]])

    async def cams(self, message: Message) -> None:
        ident = self._ident(message)
        if ident is None:
            return
        if not self.access.is_allowed(ident):
            await message.answer(f"Нет доступа. Ваш ID: {ident.user_id} — передайте его администратору.")
            return
        if message.chat.type == "private":
            markup = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=BUTTON_TEXT, web_app=WebAppInfo(url=self.cfg.public_url))]])
        else:
            markup = self._link_markup()
        await message.answer("Нажмите, чтобы открыть камеры:", reply_markup=markup)

    async def _require_admin(self, message: Message) -> Identity | None:
        ident = self._ident(message)
        if ident is None or not self.access.is_admin(ident):
            await message.answer("Команда только для администраторов.")
            return None
        return ident

    @staticmethod
    def _target(message: Message, command: CommandObject) -> Identity:
        if command.args:
            return parse_target(command.args, "tg")
        reply = message.reply_to_message
        if reply is not None and reply.from_user is not None:
            return Identity("tg", reply.from_user.id)
        raise ValueError("no target")

    async def allow(self, message: Message, command: CommandObject) -> None:
        admin = await self._require_admin(message)
        if admin is None:
            return
        try:
            target = self._target(message, command)
        except ValueError:
            await message.answer(USAGE)
            return
        added = self.access.allow(target, by=admin)
        await message.answer(f"✅ {target} добавлен." if added else f"{target} уже в списке.")

    async def deny(self, message: Message, command: CommandObject) -> None:
        admin = await self._require_admin(message)
        if admin is None:
            return
        try:
            target = self._target(message, command)
        except ValueError:
            await message.answer(USAGE)
            return
        removed = await self.access.deny(target)
        await message.answer(f"🚫 {target} удалён." if removed else f"{target} не было в списке.")

    async def users(self, message: Message) -> None:
        if await self._require_admin(message) is None:
            return
        await message.answer(format_users(self.access.list_allowed()))

    async def post_channel(self, message: Message) -> None:
        if await self._require_admin(message) is None:
            return
        channel = self.cfg.telegram.channel_id
        if channel is None:
            await message.answer("telegram.channel_id не задан в конфиге.")
            return
        await message.bot.send_message(
            channel, "📹 Камеры онлайн. Нажмите кнопку, чтобы смотреть.", reply_markup=self._link_markup())
        await message.answer("Опубликовано. Закрепите пост в канале.")


def build_router(h: TgHandlers) -> Router:
    router = Router()
    router.message.register(h.cams, Command("start", "cams"))
    router.message.register(h.allow, Command("allow"))
    router.message.register(h.deny, Command("deny"))
    router.message.register(h.users, Command("users"))
    router.message.register(h.post_channel, Command("post_channel"))
    return router


async def notify_admins(bot: Bot, cfg: Config, text: str) -> None:
    for uid in cfg.admins.telegram:
        try:
            await bot.send_message(uid, text)
        except Exception as e:
            log.warning("cannot notify admin %s: %s", uid, e)


async def run_telegram(bot: Bot, cfg: Config, access: Access) -> None:
    me = await bot.get_me()
    link = f"https://t.me/{me.username}/{cfg.telegram.mini_app_short_name}"
    dp = Dispatcher()
    dp.include_router(build_router(TgHandlers(cfg, access, link)))
    log.info("telegram bot @%s started", me.username)
    await dp.start_polling(bot, handle_signals=False, close_bot_session=False)
```

- [ ] **Step 5: Тесты проходят**

Run: `python -m pytest tests/test_admin.py tests/test_tg_bot.py -v` → PASS.

- [ ] **Step 6: Commit**

```bash
git add streemcam/admin.py streemcam/tg tests/test_admin.py tests/test_tg_bot.py
git commit -m "feat: Telegram bot with Mini App button and admin commands"
```

---

### Task 10: Discord-бот

**Files:**
- Create: `streemcam/dc/__init__.py` (пустой), `streemcam/dc/bot.py`
- Test: `tests/test_dc_bot.py`

**Interfaces:**
- Consumes: `Access`, `Config`, `Identity`, `admin.parse_target/format_users/USAGE`.
- Produces: `cams_links(cfg, access, ident) -> list[tuple[str, str]]` (label, url; первая — «Все камеры», затем по камере, если камер ≤ 24; каждая ссылка — свой одноразовый токен); `build_view(links) -> discord.ui.View`; `DcHandlers(cfg, access)` с методами `cams(interaction)`, `allow(interaction, target: str)`, `deny(interaction, target: str)`, `users(interaction)`; `DcBot(cfg, access)`; `async run_discord(cfg, access)`.

- [ ] **Step 1: Падающие тесты**

`tests/test_dc_bot.py`:
```python
from unittest.mock import AsyncMock, MagicMock

import pytest

from streemcam.access import Access
from streemcam.db import Store
from streemcam.dc.bot import DcHandlers, cams_links
from streemcam.identity import Identity
from streemcam.streams import StreamRegistry

USER = Identity("dc", 77)


@pytest.fixture
def access(cfg):
    return Access(cfg, Store(":memory:"), StreamRegistry(4))


@pytest.fixture
def h(cfg, access):
    return DcHandlers(cfg, access)


def make_inter(user_id):
    inter = MagicMock()
    inter.user.id = user_id
    inter.response.send_message = AsyncMock()
    return inter


def sent(inter):
    return inter.response.send_message.call_args


def test_cams_links(cfg, access):
    links = cams_links(cfg, access, USER)
    assert [label for label, _ in links] == ["📹 Все камеры", "Двор", "Ворота", "Комната"]
    assert all(url.startswith("https://cams.example.com/?t=") for _, url in links)
    assert links[1][1].endswith("#cam=yard")
    tokens = {url.split("?t=")[1].split("#")[0] for _, url in links}
    assert len(tokens) == 4  # у каждой кнопки свой одноразовый токен
    assert access.redeem_link_token(links[2][1].split("?t=")[1].split("#")[0]) == USER


def test_cams_links_many_cameras(make_cfg):
    cams = [{"id": f"c{i}", "name": f"C{i}", "type": "rtsp", "url": "rtsp://x"} for i in range(25)]
    cfg = make_cfg(cameras=cams)
    access = Access(cfg, Store(":memory:"), StreamRegistry(4))
    assert len(cams_links(cfg, access, USER)) == 1


async def test_cams_allowed(h):
    inter = make_inter(77)
    await h.cams(inter)
    call = sent(inter)
    assert call.kwargs["ephemeral"] is True
    assert len(call.kwargs["view"].children) == 4


async def test_cams_denied(h):
    inter = make_inter(999)
    await h.cams(inter)
    call = sent(inter)
    assert "999" in call.args[0]
    assert call.kwargs["ephemeral"] is True
    assert "view" not in call.kwargs


async def test_allow_mention_by_admin(h, access):
    inter = make_inter(2)
    await h.allow(inter, "<@5>")
    assert access.is_allowed(Identity("dc", 5))
    assert sent(inter).kwargs["ephemeral"] is True


async def test_allow_telegram_user(h, access):
    await h.allow(make_inter(2), "tg:9")
    assert access.is_allowed(Identity("tg", 9))


async def test_allow_non_admin(h, access):
    inter = make_inter(77)
    await h.allow(inter, "5")
    assert not access.is_allowed(Identity("dc", 5))
    assert "администратор" in sent(inter).args[0]


async def test_allow_bad_target(h):
    inter = make_inter(2)
    await h.allow(inter, "bob")
    assert "Использование" in sent(inter).args[0]


async def test_deny_and_users(h, access):
    await h.deny(make_inter(2), "77")
    assert not access.is_allowed(USER)
    inter = make_inter(2)
    await h.users(inter)
    assert "tg:42" in sent(inter).args[0]
```

Run: `python -m pytest tests/test_dc_bot.py -v` → ImportError.

- [ ] **Step 2: Реализация dc/bot.py**

`streemcam/dc/bot.py`:
```python
import logging

import discord
from discord import app_commands

from ..access import Access
from ..admin import USAGE, format_users, parse_target
from ..config import Config
from ..identity import Identity

log = logging.getLogger(__name__)

MAX_CAMERA_BUTTONS = 24  # Discord: не больше 25 кнопок в сообщении, одна занята «Все камеры»


def cams_links(cfg: Config, access: Access, ident: Identity) -> list[tuple[str, str]]:
    def link(fragment: str = "") -> str:
        return f"{cfg.public_url}/?t={access.issue_link_token(ident)}{fragment}"

    links = [("📹 Все камеры", link())]
    if len(cfg.cameras) <= MAX_CAMERA_BUTTONS:
        links += [(cam.name, link(f"#cam={cam.id}")) for cam in cfg.cameras]
    return links


def build_view(links: list[tuple[str, str]]) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for label, url in links:
        view.add_item(discord.ui.Button(style=discord.ButtonStyle.link, label=label[:80], url=url))
    return view


class DcHandlers:
    def __init__(self, cfg: Config, access: Access):
        self.cfg = cfg
        self.access = access

    async def _reply(self, interaction: discord.Interaction, text: str, **kwargs) -> None:
        await interaction.response.send_message(text, ephemeral=True, **kwargs)

    async def cams(self, interaction: discord.Interaction) -> None:
        ident = Identity("dc", interaction.user.id)
        if not self.access.is_allowed(ident):
            await self._reply(interaction, f"Нет доступа. Ваш ID: {ident.user_id} — передайте его администратору.")
            return
        view = build_view(cams_links(self.cfg, self.access, ident))
        await self._reply(
            interaction,
            f"Ссылки личные и одноразовые, действуют {self.cfg.token_ttl_minutes} мин.",
            view=view)

    async def _require_admin(self, interaction: discord.Interaction) -> Identity | None:
        ident = Identity("dc", interaction.user.id)
        if not self.access.is_admin(ident):
            await self._reply(interaction, "Команда только для администраторов.")
            return None
        return ident

    async def allow(self, interaction: discord.Interaction, target: str) -> None:
        admin = await self._require_admin(interaction)
        if admin is None:
            return
        try:
            ident = parse_target(target, "dc")
        except ValueError:
            await self._reply(interaction, USAGE)
            return
        added = self.access.allow(ident, by=admin)
        await self._reply(interaction, f"✅ {ident} добавлен." if added else f"{ident} уже в списке.")

    async def deny(self, interaction: discord.Interaction, target: str) -> None:
        if await self._require_admin(interaction) is None:
            return
        try:
            ident = parse_target(target, "dc")
        except ValueError:
            await self._reply(interaction, USAGE)
            return
        removed = await self.access.deny(ident)
        await self._reply(interaction, f"🚫 {ident} удалён." if removed else f"{ident} не было в списке.")

    async def users(self, interaction: discord.Interaction) -> None:
        if await self._require_admin(interaction) is None:
            return
        await self._reply(interaction, format_users(self.access.list_allowed()))


class DcBot(discord.Client):
    def __init__(self, cfg: Config, access: Access):
        super().__init__(intents=discord.Intents.default())
        self.cfg = cfg
        self.tree = app_commands.CommandTree(self)
        h = DcHandlers(cfg, access)

        @self.tree.command(name="cams", description="Открыть камеры")
        async def cams(interaction: discord.Interaction) -> None:
            await h.cams(interaction)

        @self.tree.command(name="allow", description="Выдать доступ к камерам (админ)")
        @app_commands.describe(target="@пользователь, ID, tg:<id> или dc:<id>")
        async def allow(interaction: discord.Interaction, target: str) -> None:
            await h.allow(interaction, target)

        @self.tree.command(name="deny", description="Отозвать доступ к камерам (админ)")
        @app_commands.describe(target="@пользователь, ID, tg:<id> или dc:<id>")
        async def deny(interaction: discord.Interaction, target: str) -> None:
            await h.deny(interaction, target)

        @self.tree.command(name="users", description="Белый список (админ)")
        async def users(interaction: discord.Interaction) -> None:
            await h.users(interaction)

    async def setup_hook(self) -> None:
        if self.cfg.discord.guild_ids:
            for gid in self.cfg.discord.guild_ids:
                guild = discord.Object(id=gid)
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()


async def run_discord(cfg: Config, access: Access) -> None:
    client = DcBot(cfg, access)
    async with client:
        await client.start(cfg.discord.bot_token)
```

- [ ] **Step 3: Тесты проходят**

Run: `python -m pytest tests/test_dc_bot.py -v` → PASS.

- [ ] **Step 4: Commit**

```bash
git add streemcam/dc tests/test_dc_bot.py
git commit -m "feat: Discord bot with personal one-time camera links"
```

---

### Task 11: Сборка приложения и CLI

**Files:**
- Create: `streemcam/app.py`, `streemcam/__main__.py`
- Test: `tests/test_app.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: всё выше.
- Produces:
  - `app.supervise(name, factory: Callable[[], Awaitable[None]], base_delay=5.0, max_delay=300.0)` — перезапуск при падении/завершении с экспоненциальной паузой.
  - `app.alert_text(cfg, cam_id, online) -> str`.
  - `async app.run(cfg)`.
  - CLI: `python -m streemcam [--config PATH] run | render-go2rtc [--out PATH] | link <tg:ID|dc:ID> [--base URL]`; возвращает код 0, при ошибке конфига — 2.

- [ ] **Step 1: Падающие тесты**

`tests/test_app.py`:
```python
import asyncio

import pytest

from streemcam.app import alert_text, supervise


def test_alert_text(cfg):
    assert alert_text(cfg, "yard", False) == "⚠️ Камера «Двор» офлайн больше 5 мин."
    assert alert_text(cfg, "yard", True) == "✅ Камера «Двор» снова онлайн."


async def test_supervise_restarts_after_crash():
    calls = []

    async def factory():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("boom")
        await asyncio.Event().wait()  # третий запуск «живёт»

    task = asyncio.create_task(supervise("x", factory, base_delay=0, max_delay=0))
    for _ in range(50):
        await asyncio.sleep(0)
        if len(calls) == 3:
            break
    assert len(calls) == 3
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
```

`tests/test_cli.py`:
```python
import yaml

from streemcam.__main__ import main


def write_cfg(tmp_path, base_data):
    path = tmp_path / "config.yaml"
    base_data["db_path"] = str(tmp_path / "data" / "db.sqlite")
    path.write_text(yaml.safe_dump(base_data, allow_unicode=True), encoding="utf-8")
    return path


def test_render_go2rtc(tmp_path, base_data):
    cfg_path = write_cfg(tmp_path, base_data)
    out = tmp_path / "go2rtc" / "go2rtc.yaml"
    assert main(["--config", str(cfg_path), "render-go2rtc", "--out", str(out)]) == 0
    assert "yard" in yaml.safe_load(out.read_text(encoding="utf-8"))["streams"]


def test_link(tmp_path, base_data, capsys):
    cfg_path = write_cfg(tmp_path, base_data)
    assert main(["--config", str(cfg_path), "link", "tg:42", "--base", "http://127.0.0.1:8080"]) == 0
    assert capsys.readouterr().out.strip().startswith("http://127.0.0.1:8080/?t=")


def test_bad_config_returns_2(tmp_path, capsys):
    path = tmp_path / "config.yaml"
    path.write_text("public_url: x\n", encoding="utf-8")
    assert main(["--config", str(path), "render-go2rtc", "--out", str(tmp_path / "o.yaml")]) == 2
    assert "invalid config" in capsys.readouterr().err
```

Run: `python -m pytest tests/test_app.py tests/test_cli.py -v` → ImportError.

- [ ] **Step 2: Реализация app.py**

`streemcam/app.py`:
```python
import asyncio
import logging
from collections.abc import Awaitable, Callable

import httpx
import uvicorn

from .access import Access
from .config import Config
from .db import Store
from .monitor import CameraMonitor
from .streams import StreamRegistry
from .web.server import create_app

log = logging.getLogger(__name__)


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


def alert_text(cfg: Config, cam_id: str, online: bool) -> str:
    cam = cfg.camera(cam_id)
    name = cam.name if cam else cam_id
    if online:
        return f"✅ Камера «{name}» снова онлайн."
    return f"⚠️ Камера «{name}» офлайн больше {cfg.offline_alert_minutes} мин."


def build_access(cfg: Config) -> Access:
    return Access(cfg, Store(cfg.db_path), StreamRegistry(cfg.max_streams_per_user))


async def run(cfg: Config) -> None:
    access = build_access(cfg)
    http = httpx.AsyncClient(base_url=cfg.go2rtc_url, timeout=20)

    tg_bot = None
    if cfg.telegram.bot_token:
        from aiogram import Bot
        tg_bot = Bot(cfg.telegram.bot_token)

    async def on_alert(cam_id: str, online: bool) -> None:
        if tg_bot is not None:
            from .tg.bot import notify_admins
            await notify_admins(tg_bot, cfg, alert_text(cfg, cam_id, online))

    monitor = CameraMonitor(cfg, http, on_alert)
    app = create_app(cfg, access, monitor, access.registry, http)
    server = uvicorn.Server(uvicorn.Config(app, host=cfg.listen_host, port=cfg.listen_port,
                                           proxy_headers=True, forwarded_allow_ips="*"))

    background = [asyncio.create_task(supervise("monitor", monitor.run))]
    if tg_bot is not None:
        from .tg.bot import run_telegram
        background.append(asyncio.create_task(
            supervise("telegram", lambda: run_telegram(tg_bot, cfg, access))))
    else:
        log.info("telegram bot disabled (no token)")
    if cfg.discord.bot_token:
        from .dc.bot import run_discord
        background.append(asyncio.create_task(supervise("discord", lambda: run_discord(cfg, access))))
    else:
        log.info("discord bot disabled (no token)")

    try:
        await server.serve()
    finally:
        for t in background:
            t.cancel()
        await asyncio.gather(*background, return_exceptions=True)
        await http.aclose()
        if tg_bot is not None:
            await tg_bot.session.close()
```

- [ ] **Step 3: Реализация __main__.py**

`streemcam/__main__.py`:
```python
import argparse
import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

from .admin import parse_target
from .config import ConfigError, load_config


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="streemcam")
    parser.add_argument("--config", default=os.environ.get("STREEMCAM_CONFIG", "config.yaml"))
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("run", help="запустить веб-шлюз и ботов")
    render = sub.add_parser("render-go2rtc", help="сгенерировать go2rtc.yaml из config.yaml")
    render.add_argument("--out", default="go2rtc/go2rtc.yaml")
    link = sub.add_parser("link", help="выдать одноразовую ссылку на плеер")
    link.add_argument("user", help="tg:<id> или dc:<id>")
    link.add_argument("--base", help="адрес вместо public_url (например http://127.0.0.1:8080)")
    args = parser.parse_args(argv)

    try:
        cfg = load_config(args.config)
    except (ConfigError, OSError) as e:
        print(e, file=sys.stderr)
        return 2

    if args.cmd == "render-go2rtc":
        from .go2rtc_config import write_go2rtc_config
        write_go2rtc_config(args.out, cfg)
        print(f"written {args.out}")
        return 0

    if args.cmd == "link":
        from .app import build_access
        try:
            ident = parse_target(args.user, "tg")
        except ValueError as e:
            print(e, file=sys.stderr)
            return 2
        token = build_access(cfg).issue_link_token(ident)
        print(f"{(args.base or cfg.public_url).rstrip('/')}/?t={token}")
        return 0

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from .app import run
    try:
        asyncio.run(run(cfg))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Тесты проходят**

Run: `python -m pytest -v` → все PASS.

- [ ] **Step 5: Commit**

```bash
git add streemcam/app.py streemcam/__main__.py tests/test_app.py tests/test_cli.py
git commit -m "feat: application wiring, supervisor and CLI"
```

---

### Task 12: Docker, примеры конфигов, README, ручная проверка

**Files:**
- Create: `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `config.example.yaml`, `.env.example`, `README.md`

**Interfaces:**
- Consumes: CLI из Task 11.
- Produces: `docker compose up -d --build` поднимает `go2rtc-config` (одноразово пишет `go2rtc/go2rtc.yaml`), `go2rtc`, `streemcam`, `cloudflared`.

- [ ] **Step 1: Dockerfile и .dockerignore**

`Dockerfile`:
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY streemcam ./streemcam
RUN pip install --no-cache-dir .
ENV STREEMCAM_CONFIG=/app/config.yaml
CMD ["python", "-m", "streemcam", "run"]
```

`.dockerignore`:
```
.venv
.git
data
go2rtc
tests
config.yaml
.env
```

- [ ] **Step 2: docker-compose.yml**

```yaml
services:
  go2rtc-config:
    build: .
    command: ["python", "-m", "streemcam", "render-go2rtc", "--out", "/go2rtc/go2rtc.yaml"]
    env_file: .env
    volumes:
      - ./config.yaml:/app/config.yaml:ro
      - ./go2rtc:/go2rtc

  go2rtc:
    image: alexxit/go2rtc:latest   # нужна версия >= 1.9.13 (поддержка xiaomi://)
    depends_on:
      go2rtc-config:
        condition: service_completed_successfully
    volumes:
      - ./go2rtc:/config
    ports:
      - "127.0.0.1:1984:1984"   # WebUI только локально (вход в аккаунт Xiaomi)
      - "8555:8555/tcp"         # WebRTC (нужен только при прямом доступе, не через туннель)
      - "8555:8555/udp"
    restart: unless-stopped

  streemcam:
    build: .
    env_file: .env
    depends_on: [go2rtc]
    volumes:
      - ./config.yaml:/app/config.yaml:ro
      - ./data:/app/data
    ports:
      - "127.0.0.1:8080:8080"   # локальная проверка; наружу — только через cloudflared
    restart: unless-stopped

  cloudflared:
    image: cloudflare/cloudflared:latest
    command: tunnel --no-autoupdate run
    environment:
      TUNNEL_TOKEN: ${CLOUDFLARE_TUNNEL_TOKEN}
    depends_on: [streemcam]
    restart: unless-stopped
```

- [ ] **Step 3: config.example.yaml и .env.example**

`config.example.yaml`:
```yaml
# Публичный HTTPS-адрес (hostname из Cloudflare Tunnel)
public_url: https://cams.example.com
secret: ${STREEMCAM_SECRET}

# Значения для docker-compose. Без Docker: listen_host 127.0.0.1,
# go2rtc_url http://127.0.0.1:1984, go2rtc_api_listen 127.0.0.1:1984
listen_host: 0.0.0.0
listen_port: 8080
go2rtc_url: http://go2rtc:1984
go2rtc_api_listen: ":1984"
player_mode: mse          # через туннель; на VPS с открытым 8555: "webrtc,mse"

telegram:
  bot_token: ${TELEGRAM_BOT_TOKEN:-}
  mini_app_short_name: cams   # short name из BotFather /newapp
  channel_id: "@my_channel"   # куда /post_channel публикует кнопку

discord:
  bot_token: ${DISCORD_BOT_TOKEN:-}
  guild_ids: []               # ID серверов для мгновенной регистрации команд

admins:
  telegram: [111111111]
  discord: [222222222222222222]

allow:
  telegram: []
  discord: []

cameras:
  - id: yard
    name: Двор
    type: rtsp
    url: rtsp://user:pass@192.168.1.10:554/stream1
  - id: gate
    name: Ворота
    type: dahua
    host: 192.168.1.20
    user: admin
    password: ${DAHUA_PASSWORD}
    channel: 1
    subtype: 1               # 0 — основной поток, 1 — дополнительный (обычно H.264)
  - id: room
    name: Комната
    type: xiaomi
    account: "1234567890"    # ID аккаунта Xiaomi (см. WebUI go2rtc после входа)
    region: de
    host: 192.168.1.30
    did: "123456789"
    model: chuangmi.camera.v2
  - id: test
    name: Тестовый поток
    type: rtsp
    url: "ffmpeg:virtual?video&size=720#video=h264"
```

`.env.example`:
```
STREEMCAM_SECRET=замените-на-случайную-строку-от-32-символов
TELEGRAM_BOT_TOKEN=
DISCORD_BOT_TOKEN=
CLOUDFLARE_TUNNEL_TOKEN=
DAHUA_PASSWORD=
```

- [ ] **Step 4: README.md**

```markdown
# streemcam

Просмотр камер (RTSP, Dahua, Xiaomi) из Telegram и Discord для пользователей из белого списка.

## Быстрый старт

1. `copy config.example.yaml config.yaml`, `copy .env.example .env`, заполнить.
   Секрет: `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
2. Cloudflare Zero Trust → Networks → Tunnels → Create tunnel → скопировать токен в
   `CLOUDFLARE_TUNNEL_TOKEN`. Public hostname: `cams.example.com` → `http://streemcam:8080`.
3. `docker compose up -d --build`

## Telegram
1. @BotFather → `/newbot` → токен в `TELEGRAM_BOT_TOKEN`.
2. @BotFather → `/newapp` → выбрать бота → URL = `public_url`, short name = `mini_app_short_name`.
3. Добавить бота админом в канал, в личке боту: `/post_channel`, закрепить пост.
4. Команды: `/cams`, админам — `/allow <id|dc:id>`, `/deny`, `/users` (можно ответом на сообщение).

## Discord
1. https://discord.com/developers → New Application → Bot → токен в `DISCORD_BOT_TOKEN`.
2. OAuth2 → URL Generator: scopes `bot`, `applications.commands` → пригласить на сервер.
3. ID сервера в `discord.guild_ids` (команды появятся сразу).
4. Команды: `/cams`, админам — `/allow`, `/deny`, `/users`.

## Камеры Xiaomi
Открыть http://127.0.0.1:1984 (WebUI go2rtc, только с сервера) → Add → Xiaomi → войти в аккаунт
Mi Home. go2rtc сохранит ключ в `go2rtc/go2rtc.yaml` (streemcam его не перезаписывает).
Там же видны `did` и `model` камер. Для получения ключей go2rtc нужен интернет.
Список поддерживаемых моделей — в документации go2rtc (`internal/xiaomi`).

## Камеры Dahua
`type: dahua`, `subtype: 1` — дополнительный поток (обычно H.264, работает во всех браузерах).

## Без Docker
`pip install .`, поменять адреса в конфиге (см. комментарии), затем
`python -m streemcam render-go2rtc`, запустить go2rtc с `go2rtc/go2rtc.yaml`, `python -m streemcam run`.

## Выдать ссылку вручную
`docker compose exec streemcam python -m streemcam link tg:123456 --base http://127.0.0.1:8080`
```

- [ ] **Step 5: Ручная проверка end-to-end**

Минимальный `config.yaml` с одной тестовой камерой `test` (`ffmpeg:virtual...`), `.env` с `STREEMCAM_SECRET`, остальные токены пустые, сервис `cloudflared` можно не запускать.

Run:
```
docker compose up -d --build go2rtc-config go2rtc streemcam
docker compose logs streemcam
docker compose exec streemcam python -m streemcam link tg:111111111 --base http://127.0.0.1:8080
```
Expected: в логах `Uvicorn running on http://0.0.0.0:8080`, `telegram bot disabled`, `discord bot disabled`; ссылка печатается.
Открыть ссылку в браузере → список камер с превью «Тестовый поток» (онлайн после первого опроса ≤ 60 с) → клик → идёт тестовая картинка. Повторное открытие той же ссылки в другой вкладке → «Ссылка недействительна или устарела».

- [ ] **Step 6: Прогнать все тесты и закоммитить**

Run: `python -m pytest -v` → все PASS.

```bash
git add Dockerfile .dockerignore docker-compose.yml config.example.yaml .env.example README.md
git commit -m "chore: docker compose deployment, examples and README"
```
