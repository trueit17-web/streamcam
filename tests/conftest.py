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
