from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.filters import CommandObject

from streemcam.access import Access
from streemcam.db import Store
from streemcam.identity import Identity
from streemcam.streams import StreamRegistry
from streemcam.tg.bot import TgHandlers

LINK = "https://t.me/cams_bot/cams"


def make_msg(user_id=42, chat_type="private", reply_user=None):
    msg = MagicMock()
    msg.from_user.id = user_id
    msg.chat.type = chat_type
    msg.answer = AsyncMock()
    msg.bot.send_message = AsyncMock()
    if reply_user is None:
        msg.reply_to_message = None
    else:
        msg.reply_to_message.from_user.id = reply_user
    return msg


def cmd(name, args=None):
    return CommandObject(prefix="/", command=name, args=args)


def text_of(msg):
    return msg.answer.call_args.args[0]


def button_of(msg):
    return msg.answer.call_args.kwargs["reply_markup"].inline_keyboard[0][0]


@pytest.fixture
def access(cfg):
    return Access(cfg, Store(":memory:"), StreamRegistry(4))


@pytest.fixture
def h(cfg, access):
    return TgHandlers(cfg, access, LINK)


async def test_cams_private_opens_web_app(h):
    msg = make_msg()
    await h.cams(msg)
    assert button_of(msg).web_app.url == "https://cams.example.com"


async def test_cams_group_uses_direct_link(h):
    msg = make_msg(chat_type="supergroup")
    await h.cams(msg)
    assert button_of(msg).url == LINK


async def test_cams_denied_shows_id(h):
    msg = make_msg(user_id=999)
    await h.cams(msg)
    assert "999" in text_of(msg)
    assert "reply_markup" not in msg.answer.call_args.kwargs


async def test_allow_by_admin_with_arg(h, access):
    msg = make_msg(user_id=1)
    await h.allow(msg, cmd("allow", "dc:5"))
    assert access.is_allowed(Identity("dc", 5))
    assert "dc:5" in text_of(msg)


async def test_allow_by_reply(h, access):
    msg = make_msg(user_id=1, reply_user=555)
    await h.allow(msg, cmd("allow"))
    assert access.is_allowed(Identity("tg", 555))


async def test_allow_bad_arg_shows_usage(h):
    msg = make_msg(user_id=1)
    await h.allow(msg, cmd("allow", "abc"))
    assert "Использование" in text_of(msg)


async def test_allow_by_non_admin_rejected(h, access):
    msg = make_msg(user_id=42)
    await h.allow(msg, cmd("allow", "5"))
    assert not access.is_allowed(Identity("tg", 5))
    assert "администратор" in text_of(msg)


async def test_deny(h, access):
    msg = make_msg(user_id=1)
    await h.deny(msg, cmd("deny", "42"))
    assert not access.is_allowed(Identity("tg", 42))


async def test_users(h):
    msg = make_msg(user_id=1)
    await h.users(msg)
    assert "tg:42" in text_of(msg) and "dc:77" in text_of(msg)


async def test_post_channel(h):
    msg = make_msg(user_id=1)
    await h.post_channel(msg)
    call = msg.bot.send_message.call_args
    assert call.args[0] == -100500
    assert call.kwargs["reply_markup"].inline_keyboard[0][0].url == LINK


async def test_post_channel_without_channel(make_cfg, access):
    h = TgHandlers(make_cfg(telegram={"bot_token": "1:X"}), access, LINK)
    msg = make_msg(user_id=1)
    await h.post_channel(msg)
    assert "channel_id" in text_of(msg)
    msg.bot.send_message.assert_not_called()
