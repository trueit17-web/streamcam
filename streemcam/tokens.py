import base64
import binascii
import hashlib
import hmac
import json
import time


class TokenError(Exception):
    pass


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _mac(body: str, secret: str) -> str:
    return _b64e(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())


def sign(payload: dict, secret: str) -> str:
    body = _b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    return f"{body}.{_mac(body, secret)}"


def verify(token: str, secret: str, typ: str, now: float | None = None) -> dict:
    parts = token.split(".")
    if len(parts) != 2:
        raise TokenError("malformed token")
    body, sig = parts
    if not hmac.compare_digest(sig.encode(), _mac(body, secret).encode()):
        raise TokenError("bad signature")
    try:
        payload = json.loads(_b64d(body))
    except (ValueError, binascii.Error):
        raise TokenError("malformed token") from None
    if payload.get("typ") != typ:
        raise TokenError("wrong token type")
    if payload.get("exp", 0) < (time.time() if now is None else now):
        raise TokenError("token expired")
    return payload
