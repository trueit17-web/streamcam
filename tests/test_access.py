import pytest

from streemcam.access import Access, AccessDenied
from streemcam.db import Store
from streemcam.identity import Identity
from streemcam.streams import StreamRegistry
from streemcam.tg_auth import TgAuthError
from streemcam.tokens import TokenError
from tg_helpers import make_init_data

USER = Identity("tg", 42)
ADMIN = Identity("tg", 1)
STRANGER = Identity("tg", 999)


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def access(cfg, clock):
    return Access(cfg, Store(":memory:"), StreamRegistry(cfg.max_streams_per_user), clock=clock)


def test_seeded_from_config(access):
    assert access.is_allowed(USER)
    assert access.is_allowed(Identity("dc", 77))
    assert not access.is_allowed(STRANGER)


def test_admins_always_allowed(access):
    assert access.is_admin(ADMIN) and access.is_allowed(ADMIN)
    assert access.is_admin(Identity("dc", 2))
    assert not access.is_admin(USER)


async def test_allow_and_deny(access):
    assert access.allow(STRANGER, by=ADMIN) is True
    assert access.is_allowed(STRANGER)
    assert STRANGER in access.list_allowed()
    assert await access.deny(STRANGER) is True
    assert not access.is_allowed(STRANGER)


async def test_deny_kicks_streams(access):
    kicked = []

    async def closer():
        kicked.append(True)

    access.registry.acquire(USER, closer)
    await access.deny(USER)
    assert kicked == [True]


def test_link_token_one_time(access):
    token = access.issue_link_token(USER)
    assert access.redeem_link_token(token) == USER
    with pytest.raises(TokenError):
        access.redeem_link_token(token)


def test_link_token_expires(access, clock):
    token = access.issue_link_token(USER)
    clock.t += 10 * 60 + 1
    with pytest.raises(TokenError):
        access.redeem_link_token(token)


async def test_link_token_for_denied_user(access):
    token = access.issue_link_token(USER)
    await access.deny(USER)
    with pytest.raises(AccessDenied) as e:
        access.redeem_link_token(token)
    assert e.value.identity == USER


async def test_session(access, clock):
    s = access.issue_session(USER)
    assert access.check_session(s) == USER
    with pytest.raises(TokenError):
        access.check_session(access.issue_link_token(USER))  # тип токена другой
    await access.deny(USER)
    with pytest.raises(AccessDenied):
        access.check_session(s)


def test_session_expires(access, clock):
    s = access.issue_session(USER)
    clock.t += 12 * 3600 + 1
    with pytest.raises(TokenError):
        access.check_session(s)


def test_login_telegram(access, clock):
    data = make_init_data("123456:TEST", user_id=42, auth_date=int(clock.t))
    assert access.login_telegram(data) == USER
    stranger = make_init_data("123456:TEST", user_id=999, auth_date=int(clock.t))
    with pytest.raises(AccessDenied):
        access.login_telegram(stranger)
    with pytest.raises(TgAuthError):
        access.login_telegram("garbage")


def test_login_telegram_without_bot_token(make_cfg, clock):
    cfg = make_cfg(telegram={})
    access = Access(cfg, Store(":memory:"), StreamRegistry(4), clock=clock)
    with pytest.raises(TgAuthError):
        access.login_telegram(make_init_data("123456:TEST", auth_date=int(clock.t)))
