from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.filters import CommandObject

from streemcam.access import Access
from streemcam.catalog import CameraInfo
from streemcam.db import Store
from streemcam.streams import StreamRegistry
from streemcam.tg.bot import TUYA_USAGE, TgHandlers, format_tuya_cameras
from streemcam.tuya.login import QrSession
from streemcam.tuya.models import TuyaLoginError

CAMS = [CameraInfo("tuya_bf1", "Прихожая", "tuya", "bf1", True),
        CameraInfo("tuya_bf2", "Гараж", "tuya", "bf2", False)]


class FakeTuya:
    def __init__(self, ok=True, start_error=None, start_exception=None, empty_cameras=False, uid=None):
        self.ok = ok
        self.start_error = start_error
        self.start_exception = start_exception
        self.empty_cameras = empty_cameras
        self.uid = uid
        self.logged_in = False
        self.logged_out = False

    async def start_login(self, code):
        if self.start_error:
            raise TuyaLoginError(self.start_error)
        if self.start_exception:
            raise self.start_exception
        return QrSession(code, "QR1")

    async def wait_login(self, session):
        self.logged_in = self.ok
        return self.ok

    def cameras(self):
        if not self.logged_in:
            return []
        return [] if self.empty_cameras else CAMS

    def account_uid(self):
        return self.uid

    async def logout(self):
        self.logged_out = True
        self.logged_in = False


def make_msg(user_id=1):
    msg = MagicMock()
    msg.from_user.id = user_id
    msg.answer = AsyncMock()
    msg.answer_photo = AsyncMock()
    return msg


def texts(msg):
    return [c.args[0] for c in msg.answer.call_args_list]


def cmd(args=None):
    return CommandObject(prefix="/", command="tuya_login", args=args)


@pytest.fixture
def access(cfg):
    return Access(cfg, Store(":memory:"), StreamRegistry(4))


def handlers(cfg, access, tuya):
    return TgHandlers(cfg, access, "https://t.me/bot/cams", tuya=tuya)


def test_format_tuya_cameras():
    text = format_tuya_cameras(CAMS)
    assert "Прихожая" in text and "tuya_bf1" in text and "онлайн" in text and "офлайн" in text
    assert "не найдено" in format_tuya_cameras([])


async def test_login_success_sends_qr_then_cameras(cfg, access):
    tuya = FakeTuya()
    msg = make_msg()
    await handlers(cfg, access, tuya).tuya_login(msg, cmd("UC1"))
    photo = msg.answer_photo.call_args
    assert photo.args[0].data.startswith(b"\x89PNG")
    assert "Smart Life" in photo.kwargs["caption"]
    assert "Прихожая" in texts(msg)[-1]


async def test_login_timeout(cfg, access):
    msg = make_msg()
    await handlers(cfg, access, FakeTuya(ok=False)).tuya_login(msg, cmd("UC1"))
    assert "/tuya_login" in texts(msg)[-1]
    assert "подтверждён" in texts(msg)[-1]


async def test_login_start_error(cfg, access):
    msg = make_msg()
    await handlers(cfg, access, FakeTuya(start_error="user code invalid")).tuya_login(msg, cmd("BAD"))
    assert "user code invalid" in texts(msg)[-1]
    msg.answer_photo.assert_not_called()


async def test_login_start_unexpected_exception(cfg, access):
    msg = make_msg()
    tuya = FakeTuya(start_exception=RuntimeError("network unreachable"))
    await handlers(cfg, access, tuya).tuya_login(msg, cmd("UC1"))
    assert "Не удалось начать вход в Tuya" in texts(msg)[-1]
    assert "network unreachable" in texts(msg)[-1]
    msg.answer_photo.assert_not_called()


async def test_login_success_but_no_cameras_yet(cfg, access):
    msg = make_msg()
    tuya = FakeTuya(empty_cameras=True)
    await handlers(cfg, access, tuya).tuya_login(msg, cmd("UC1"))
    assert "/tuya_status" in texts(msg)[-1]


async def test_login_requires_code(cfg, access):
    msg = make_msg()
    await handlers(cfg, access, FakeTuya()).tuya_login(msg, cmd())
    assert texts(msg)[-1] == TUYA_USAGE


async def test_login_non_admin(cfg, access):
    msg = make_msg(user_id=42)
    await handlers(cfg, access, FakeTuya()).tuya_login(msg, cmd("UC1"))
    assert "администратор" in texts(msg)[-1]
    msg.answer_photo.assert_not_called()


async def test_tuya_disabled(cfg, access):
    msg = make_msg()
    await handlers(cfg, access, None).tuya_login(msg, cmd("UC1"))
    assert "отключена" in texts(msg)[-1]


async def test_status_and_logout(cfg, access):
    tuya = FakeTuya(uid="u1")
    h = handlers(cfg, access, tuya)
    msg = make_msg()
    await h.tuya_status(msg)
    assert "не выполнен" in texts(msg)[-1]
    tuya.logged_in = True
    await h.tuya_status(msg)
    assert "Прихожая" in texts(msg)[-1]
    assert "Аккаунт: u1" in texts(msg)[-1]
    await h.tuya_logout(msg)
    assert tuya.logged_out and "Выход" in texts(msg)[-1]
