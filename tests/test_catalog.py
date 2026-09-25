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
