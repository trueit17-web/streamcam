from urllib.parse import parse_qsl, urlencode

import pytest

from streemcam.identity import Identity
from streemcam.tg_auth import TgAuthError, verify_init_data
from tg_helpers import make_init_data

TOKEN = "123456:TEST"


def test_valid():
    data = make_init_data(TOKEN, user_id=42, auth_date=1000)
    assert verify_init_data(data, TOKEN, now=1500) == Identity("tg", 42)


def test_wrong_bot_token():
    data = make_init_data(TOKEN, auth_date=1000)
    with pytest.raises(TgAuthError):
        verify_init_data(data, "999:OTHER", now=1500)


def test_tampered_user():
    fields = dict(parse_qsl(make_init_data(TOKEN, user_id=42, auth_date=1000)))
    fields["user"] = fields["user"].replace("42", "43")
    with pytest.raises(TgAuthError):
        verify_init_data(urlencode(fields), TOKEN, now=1500)


def test_too_old():
    data = make_init_data(TOKEN, auth_date=1000)
    with pytest.raises(TgAuthError, match="expired"):
        verify_init_data(data, TOKEN, now=1000 + 3601)


@pytest.mark.parametrize("bad", ["", "garbage", "auth_date=1&user=%7B%7D"])
def test_malformed(bad):
    with pytest.raises(TgAuthError):
        verify_init_data(bad, TOKEN, now=1500)
