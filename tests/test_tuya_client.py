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
