import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from streemcam.identity import Identity

USER = Identity("tg", 42)


def ws_url(web, src="yard", ident=USER):
    return f"/api/ws?src={src}&s={web.access.issue_session(ident)}"


def test_proxies_text_and_bytes(web):
    with TestClient(web.app) as client:
        with client.websocket_connect(ws_url(web)) as ws:
            ws.send_text('{"type":"mse"}')
            assert ws.receive_text() == 'echo:{"type":"mse"}'
            ws.send_bytes(b"\x00\x01")
            assert ws.receive_bytes() == b"\x00\x01"


def test_releases_stream_after_disconnect(web):
    with TestClient(web.app) as client:
        with client.websocket_connect(ws_url(web)) as ws:
            ws.send_text("x")
            ws.receive_text()
            assert web.registry.count(USER) == 1
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


def test_deny_kicks_open_stream(web):
    with TestClient(web.app) as client:
        with client.websocket_connect(ws_url(web)) as ws:
            ws.send_text("x")
            ws.receive_text()
            client.portal.call(web.access.deny, USER)
            with pytest.raises(WebSocketDisconnect) as e:
                ws.receive_text()
            assert e.value.code == 4403
