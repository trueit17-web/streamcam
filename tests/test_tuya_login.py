import pytest

from streemcam.tuya.login import CLIENT_ID, SCHEMA, QrSession, TuyaLogin
from streemcam.tuya.models import TuyaCredentials, TuyaLoginError
from streemcam.tuya.qr import qr_png

INFO = {"t": 1700000000000, "uid": "eu1", "expire_time": 7200, "access_token": "at",
        "refresh_token": "rt", "terminal_id": "term-1", "endpoint": "https://apigw.tuyaeu.com",
        "username": "someone"}


class FakeControl:
    def __init__(self, qr_response, results):
        self.qr_response = qr_response
        self.results = list(results)
        self.calls = []

    def qr_code(self, client_id, schema, user_code):
        self.calls.append(("qr_code", client_id, schema, user_code))
        return self.qr_response

    def login_result(self, token, client_id, user_code):
        self.calls.append(("login_result", token, client_id, user_code))
        return self.results.pop(0)


def test_start_returns_session_with_payload():
    control = FakeControl({"success": True, "result": {"qrcode": "QR123"}}, [])
    session = TuyaLogin(control).start("UC1")
    assert session == QrSession("UC1", "QR123")
    assert session.qr_payload == "tuyaSmart--qrLogin?token=QR123"
    assert control.calls == [("qr_code", CLIENT_ID, SCHEMA, "UC1")]


def test_start_failure_raises():
    control = FakeControl({"success": False, "msg": "user code invalid"}, [])
    with pytest.raises(TuyaLoginError, match="user code invalid"):
        TuyaLogin(control).start("BAD")


def test_poll_pending_then_success():
    control = FakeControl({}, [(False, {}), (True, INFO)])
    login = TuyaLogin(control)
    session = QrSession("UC1", "QR123")
    assert login.poll(session) is None
    creds = login.poll(session)
    assert creds == TuyaCredentials(
        "UC1", "term-1", "https://apigw.tuyaeu.com",
        {"t": 1700000000000, "uid": "eu1", "expire_time": 7200,
         "access_token": "at", "refresh_token": "rt"})
    assert control.calls[-1] == ("login_result", "QR123", CLIENT_ID, "UC1")


def test_constants():
    assert CLIENT_ID == "HA_3y9q4ak7g4ephrvke"
    assert SCHEMA == "haauthorize"


def test_qr_png_is_png():
    data = qr_png("tuyaSmart--qrLogin?token=QR123")
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(data) > 100
