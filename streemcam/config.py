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
