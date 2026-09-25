import os
from pathlib import Path
from urllib.parse import quote, urlencode

import yaml

from .config import Config, DahuaCamera, RtspCamera, XiaomiCamera

COMPAT_SUFFIX = "~h264"


def compat_name(cam_id: str) -> str:
    return cam_id + COMPAT_SUFFIX


def compat_src(cam_id: str) -> str:
    return f"ffmpeg:{cam_id}#video=h264#width=1280#audio=aac"


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
    streams = {cam.id: stream_url(cam) for cam in cfg.cameras}
    for cam in cfg.cameras:
        streams[compat_name(cam.id)] = compat_src(cam.id)
    data["streams"] = streams
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
