import os
from pathlib import Path
from urllib.parse import quote, urlencode

import yaml

from .catalog import CameraInfo
from .config import Config, DahuaCamera, RtspCamera, XiaomiCamera

COMPAT_SUFFIX = "~h264"
REC_SUFFIX = "~rec"
LIVE_SUFFIX = "~live"
TUYA_AUDIO_FILTER = "highpass=f=120,lowpass=f=3400,aresample=48000"


def compat_name(cam_id: str) -> str:
    return cam_id + COMPAT_SUFFIX


def compat_src(cam_id: str) -> str:
    return f"ffmpeg:{cam_id}#video=h264#width=1280#audio=aac"


def tuya_http_input(cam_id: str, base: str = "http://127.0.0.1:1984") -> str:
    return f"{base}/api/stream.mp4?src={cam_id}&mp4=flac"


def tuya_live_src(cam_id: str) -> str:
    return f"ffmpeg:{tuya_http_input(cam_id)}#video=copy#audio=aac#raw=-af {TUYA_AUDIO_FILTER}"


def tuya_compat_src(cam_id: str) -> str:
    return f"ffmpeg:{tuya_http_input(cam_id)}#video=h264#width=1280#audio=aac#raw=-af {TUYA_AUDIO_FILTER}"


def rec_stream_name(cfg: Config, cam: CameraInfo) -> str | None:
    if cam.kind == "tuya":
        return cam.id if cfg.recording.tuya else None
    src = cfg.camera(cam.id)
    if src is None or not src.record:
        return None
    if isinstance(src, (XiaomiCamera, DahuaCamera)):
        return cam.id + REC_SUFFIX
    return cam.id


def stream_url(cam, subtype: int | None = None) -> str:
    match cam:
        case RtspCamera():
            return cam.url
        case DahuaCamera():
            user = quote(cam.user, safe="")
            password = quote(cam.password, safe="")
            sub = subtype if subtype is not None else cam.subtype
            return (f"rtsp://{user}:{password}@{cam.host}:{cam.port}"
                    f"/cam/realmonitor?channel={cam.channel}&subtype={sub}")
        case XiaomiCamera():
            query = {"did": cam.did, "model": cam.model}
            sub = subtype if subtype is not None else cam.subtype
            if sub is not None:
                query["subtype"] = sub
            return f"xiaomi://{quote(cam.account, safe='')}:{cam.region}@{cam.host}?{urlencode(query)}"
    raise TypeError(f"unknown camera type: {cam!r}")


def render(cfg: Config, existing: dict | None) -> dict:
    data = dict(existing or {})
    streams = {cam.id: stream_url(cam) for cam in cfg.cameras}
    for cam in cfg.cameras:
        streams[compat_name(cam.id)] = compat_src(cam.id)
    for cam in cfg.cameras:
        if not cam.record:
            continue
        if isinstance(cam, XiaomiCamera):
            streams[cam.id + REC_SUFFIX] = stream_url(cam, subtype=cam.record_subtype)
        elif isinstance(cam, DahuaCamera):
            streams[cam.id + REC_SUFFIX] = stream_url(cam, subtype=1)
    data["streams"] = streams
    data["api"] = {**(data.get("api") or {}), "listen": cfg.go2rtc_api_listen}
    data["webrtc"] = {**(data.get("webrtc") or {}), "listen": cfg.webrtc_listen}
    data["rtsp"] = {**(data.get("rtsp") or {}), "listen": ":8554"}
    return data


def write_go2rtc_config(path: str | os.PathLike, cfg: Config) -> None:
    path = Path(path)
    existing = None
    if path.exists():
        existing = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(render(cfg, existing), allow_unicode=True, sort_keys=False),
                    encoding="utf-8")
