from dataclasses import dataclass

from .models import TuyaCredentials, TuyaLoginError

CLIENT_ID = "HA_3y9q4ak7g4ephrvke"  # client_id интеграции Tuya в Home Assistant (см. спеку, «Риски»)
SCHEMA = "haauthorize"
TOKEN_KEYS = ("t", "uid", "expire_time", "access_token", "refresh_token")


@dataclass(frozen=True)
class QrSession:
    user_code: str
    token: str

    @property
    def qr_payload(self) -> str:
        return f"tuyaSmart--qrLogin?token={self.token}"


class TuyaLogin:
    """Синхронная обёртка над LoginControl; вызывать через asyncio.to_thread."""

    def __init__(self, control=None):
        if control is None:
            from tuya_sharing import LoginControl
            control = LoginControl()
        self._control = control

    def start(self, user_code: str) -> QrSession:
        response = self._control.qr_code(CLIENT_ID, SCHEMA, user_code)
        if not response.get("success"):
            raise TuyaLoginError(response.get("msg") or "Tuya QR request failed")
        return QrSession(user_code, response["result"]["qrcode"])

    def poll(self, session: QrSession) -> TuyaCredentials | None:
        ok, info = self._control.login_result(session.token, CLIENT_ID, session.user_code)
        if not ok:
            return None
        return TuyaCredentials(
            user_code=session.user_code,
            terminal_id=info["terminal_id"],
            endpoint=info["endpoint"],
            token_info={k: info[k] for k in TOKEN_KEYS if k in info},
        )
