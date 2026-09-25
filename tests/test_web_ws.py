import asyncio
import time
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from streemcam.identity import Identity

USER = Identity("tg", 42)


def _close_and_wait_released(client, web, ws, ident=USER, timeout=2.0, expected=0):
    """Close the socket and wait until the server has released its stream slot.

    Works around a Starlette TestClient race: leaving `websocket_connect(...)`
    sends a close frame and then *immediately* cancels the app task's scope and
    waits on its future, without waiting for our handler to actually finish
    running. If the handler (still inside `registry.release`/its final
    `ws.close`) hasn't reached the post-app `sleep_forever` yet when that
    cancel fires, `fut.result()` raises a stray `concurrent.futures.
    CancelledError` out of the `with` block — intermittently, since it's a
    timing race. Closing explicitly and polling for the registry to actually
    drop the slot ensures the handler has already returned normally before we
    let the context manager's own close/cancel run.
    """
    ws.close()
    deadline = time.monotonic() + timeout
    while web.registry.count(ident) != expected:
        if time.monotonic() > deadline:
            raise AssertionError(
                f"stream for {ident} was not released to {expected} within {timeout}s"
            )
        client.portal.call(asyncio.sleep, 0.01)


class _SentThenFail:
    """Upstream stub: echoes nothing, waits for one client message, then ends.

    Subclasses choose how the async iteration ends after that message: with an
    exception (simulating go2rtc dropping the connection) or by simply
    exhausting the iterator (simulating go2rtc closing the stream cleanly).
    """

    def __init__(self):
        self.sent = asyncio.Event()

    async def send(self, message):
        self.sent.set()

    def __aiter__(self):
        return self


class _RaisingUpstream(_SentThenFail):
    async def __anext__(self):
        await self.sent.wait()
        raise ConnectionResetError("go2rtc gone")


class _EndingUpstream(_SentThenFail):
    async def __anext__(self):
        await self.sent.wait()
        raise StopAsyncIteration


def ws_url(web, src="yard", ident=USER):
    return f"/api/ws?src={src}&s={web.access.issue_session(ident)}"


def test_proxies_text_and_bytes(web):
    with TestClient(web.app) as client:
        with client.websocket_connect(ws_url(web)) as ws:
            ws.send_text('{"type":"mse"}')
            assert ws.receive_text() == 'echo:{"type":"mse"}'
            ws.send_bytes(b"\x00\x01")
            assert ws.receive_bytes() == b"\x00\x01"
            _close_and_wait_released(client, web, ws)


def test_releases_stream_after_disconnect(web):
    with TestClient(web.app) as client:
        with client.websocket_connect(ws_url(web)) as ws:
            ws.send_text("x")
            ws.receive_text()
            assert web.registry.count(USER) == 1
            _close_and_wait_released(client, web, ws)
        assert web.registry.count(USER) == 0


@pytest.mark.parametrize("query,code", [
    ("src=yard", 4401),
    ("src=yard&s=bad.token", 4401),
])
def test_rejects_without_session(web, query, code):
    with TestClient(web.app) as client:
        with pytest.raises(WebSocketDisconnect) as e:
            with client.websocket_connect(f"/api/ws?{query}"):
                pass
        assert e.value.code == code


def test_rejects_unknown_camera(web):
    with TestClient(web.app) as client:
        with pytest.raises(WebSocketDisconnect) as e:
            with client.websocket_connect(ws_url(web, src="nope")):
                pass
        assert e.value.code == 4404


def test_stream_limit(make_web, make_cfg):
    web = make_web(make_cfg(max_streams_per_user=1))
    with TestClient(web.app) as client:
        with client.websocket_connect(ws_url(web)) as ws:
            ws.send_text("x")
            ws.receive_text()
            with pytest.raises(WebSocketDisconnect) as e:
                with client.websocket_connect(ws_url(web, src="gate")):
                    pass
            assert e.value.code == 4429
            _close_and_wait_released(client, web, ws)


def test_upstream_error_mid_stream_closes_1011(make_web, cfg):
    @asynccontextmanager
    async def raising_connect(src):
        yield _RaisingUpstream()

    web = make_web(cfg, upstream_connect=raising_connect)
    with TestClient(web.app) as client:
        with pytest.raises(WebSocketDisconnect) as e:
            with client.websocket_connect(ws_url(web)) as ws:
                ws.send_text("x")
                ws.receive_text()
        assert e.value.code == 1011
    assert web.registry.count(USER) == 0


def test_upstream_ending_closes_1011(make_web, cfg):
    @asynccontextmanager
    async def ending_connect(src):
        yield _EndingUpstream()

    web = make_web(cfg, upstream_connect=ending_connect)
    with TestClient(web.app) as client:
        with pytest.raises(WebSocketDisconnect) as e:
            with client.websocket_connect(ws_url(web)) as ws:
                ws.send_text("x")
                ws.receive_text()
        assert e.value.code == 1011
    assert web.registry.count(USER) == 0


def test_deny_kicks_open_stream(web):
    with TestClient(web.app) as client:
        with client.websocket_connect(ws_url(web)) as ws:
            ws.send_text("x")
            ws.receive_text()
            client.portal.call(web.access.deny, USER)
            with pytest.raises(WebSocketDisconnect) as e:
                ws.receive_text()
            assert e.value.code == 4403
            _close_and_wait_released(client, web, ws)


def recording_connect(log):
    @asynccontextmanager
    async def connect(src):
        log.append(src)
        from conftest import FakeUpstream
        yield FakeUpstream(src)
    return connect


def test_compat_uses_h264_stream(make_web, cfg):
    connected = []
    web = make_web(cfg, upstream_connect=recording_connect(connected))
    with TestClient(web.app) as client:
        with client.websocket_connect(ws_url(web) + "&compat=1") as ws:
            ws.send_text("x")
            assert ws.receive_text() == "echo:x"
            _close_and_wait_released(client, web, ws)
    assert connected == ["yard~h264"]


def test_transcode_limit(make_web, make_cfg):
    connected = []
    web = make_web(make_cfg(max_transcodes=1), upstream_connect=recording_connect(connected))
    with TestClient(web.app) as client:
        with client.websocket_connect(ws_url(web) + "&compat=1") as ws:
            ws.send_text("x")
            ws.receive_text()
            with pytest.raises(WebSocketDisconnect) as e:
                with client.websocket_connect(ws_url(web, src="gate") + "&compat=1"):
                    pass
            assert e.value.code == 4430
            with client.websocket_connect(ws_url(web, src="gate")) as ws2:  # без compat — можно
                ws2.send_text("y")
                assert ws2.receive_text() == "echo:y"
                _close_and_wait_released(client, web, ws2, expected=1)
            _close_and_wait_released(client, web, ws)
