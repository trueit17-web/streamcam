from streemcam.catalog import CameraInfo, Catalog, imou_cam_id, tuya_cam_id
from streemcam.imou.models import ImouCamera
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


def test_set_imou(cfg):
    c = Catalog(cfg)
    c.set_tuya([TuyaCamera("bf1", "Я", True)])
    c.set_imou([ImouCamera("AB12CD", "Улица", True), ImouCamera("bad id", "Х", True)])
    imou = c.get("imou_ab12cd")
    assert (imou.kind, imou.device_id, imou.name, imou.online) == ("imou", "AB12CD", "Улица", True)
    assert imou_cam_id("AB12CD") == "imou_ab12cd"
    assert [x.kind for x in c.all()][-2:] == ["tuya", "imou"]
    assert len(c.imou()) == 1


def test_imou_sorted_by_name(cfg):
    c = Catalog(cfg)
    c.set_imou([ImouCamera("B2", "Улица", True), ImouCamera("A1", "Гараж", True)])
    assert [x.name for x in c.imou()] == ["Гараж", "Улица"]


def test_mark_imou_offline(cfg):
    c = Catalog(cfg)
    c.set_imou([ImouCamera("AB12CD", "Улица", True)])
    c.mark_imou_offline()
    assert c.get("imou_ab12cd").online is False


def test_loaded_flags(make_cfg):
    cfg = make_cfg(tuya={"enabled": True}, imou={"enabled": True, "app_id": "a", "app_secret": "s"},
                   internal_key="k" * 20)
    c = Catalog(cfg)
    assert (c.tuya_loaded, c.imou_loaded) == (False, False)
    c.set_imou([])
    c.mark_tuya_offline()
    assert (c.tuya_loaded, c.imou_loaded) == (False, True)
    c.set_tuya([])
    assert c.tuya_loaded is True


def test_disabled_providers_count_as_loaded(cfg):
    c = Catalog(cfg)
    assert (c.tuya_loaded, c.imou_loaded) == (True, True)


def test_homes(base_data):
    from streemcam.config import parse_config
    base_data["cameras"][0]["home"] = "Дача"
    base_data["imou"] = {"enabled": True, "app_id": "a", "app_secret": "s", "home": "Город"}
    base_data["internal_key"] = "k" * 16
    base_data["homes"] = {"gate": "Офис", "Двор мамы": "Мама", "tuya_bf2": "Гараж", "Ю": "не выиграет у id"}
    c = Catalog(parse_config(base_data))
    c.set_tuya([TuyaCamera("bf1", "Я", True, home="Мой дом"), TuyaCamera("bf2", "Ю", True, home="Мой дом")])
    c.set_imou([ImouCamera("AB12CD", "Улица", True), ImouCamera("SH1", "Двор мамы", True),
                ImouCamera("OW1", "Ворота", True, home="Дом владельца")])
    assert [(x.id, x.home) for x in c.all()] == [
        ("yard", "Дача"), ("gate", "Офис"), ("room", None), ("tuya_bf2", "Гараж"), ("tuya_bf1", "Мой дом"),
        ("imou_ow1", "Дом владельца"), ("imou_sh1", "Мама"), ("imou_ab12cd", "Город")]
    c.mark_tuya_offline()
    c.mark_imou_offline()
    assert (c.get("tuya_bf1").home, c.get("imou_ab12cd").home) == ("Мой дом", "Город")
