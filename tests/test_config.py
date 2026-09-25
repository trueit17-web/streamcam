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


@pytest.mark.parametrize("secret", [
    "замените-на-случайную-строку-от-32-символов",
    "please-change-me-please-change-me",
    "CHANGEME-CHANGEME-CHANGEME-1234",
])
def test_placeholder_secret_rejected(make_cfg, secret):
    with pytest.raises(ConfigError, match="secret"):
        make_cfg(secret=secret)


def test_load_config_missing_env_fails(tmp_path, monkeypatch, base_data):
    monkeypatch.delenv("SC_MISSING", raising=False)
    base_data["secret"] = "${SC_MISSING}"
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(base_data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ConfigError, match="SC_MISSING"):
        load_config(path)


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
