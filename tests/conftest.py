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
