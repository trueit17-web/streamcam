import yaml

from streemcam.catalog import CameraInfo
from streemcam.go2rtc_config import (COMPAT_SUFFIX, REC_SUFFIX, compat_name, compat_src, rec_stream_name,
                                     render, stream_url, write_go2rtc_config)


def test_compat_helpers():
    assert COMPAT_SUFFIX == "~h264"
    assert compat_name("room") == "room~h264"
    assert compat_src("room") == "ffmpeg:room#video=h264#width=1280#audio=aac"


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
    assert set(data["streams"]) == {"yard", "gate", "room", "yard~h264", "gate~h264", "room~h264", "gate~rec", "room~rec"}
    assert data["streams"]["yard~h264"] == compat_src("yard")
    assert data["api"]["listen"] == "127.0.0.1:1984"
    assert data["webrtc"]["listen"] == ":8555"
    assert data["rtsp"]["listen"] == ":8554"


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
