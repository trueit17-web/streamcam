import hashlib
import hmac
import json
import time
from urllib.parse import urlencode


def make_init_data(bot_token: str, user_id: int = 42, auth_date: int | None = None) -> str:
    """Строит initData так же, как Telegram (алгоритм из документации Mini Apps)."""
    fields = {
        "auth_date": str(int(time.time()) if auth_date is None else auth_date),
        "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
        "user": json.dumps({"id": user_id, "first_name": "Иван"}, ensure_ascii=False),
    }
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)
