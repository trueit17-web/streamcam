import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from .identity import Identity


class TgAuthError(Exception):
    pass


def verify_init_data(init_data: str, bot_token: str, max_age: int = 3600,
                     now: float | None = None) -> Identity:
    try:
        fields = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        raise TgAuthError("malformed init data") from None
    received = fields.pop("hash", None)
    if not received:
        raise TgAuthError("no hash")
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(received.encode(), expected.encode()):
        raise TgAuthError("bad hash")
    try:
        auth_date = int(fields["auth_date"])
        user_id = int(json.loads(fields["user"])["id"])
    except (KeyError, ValueError, TypeError):
        raise TgAuthError("malformed init data") from None
    if (time.time() if now is None else now) - auth_date > max_age:
        raise TgAuthError("init data expired")
    return Identity("tg", user_id)
