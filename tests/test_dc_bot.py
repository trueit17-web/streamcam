from unittest.mock import AsyncMock, MagicMock

import pytest

from streemcam.access import Access
from streemcam.catalog import Catalog
from streemcam.db import Store
from streemcam.dc.bot import DcHandlers, cams_links
from streemcam.identity import Identity
from streemcam.streams import StreamRegistry

USER = Identity("dc", 77)


@pytest.fixture
def access(cfg):
    return Access(cfg, Store(":memory:"), StreamRegistry(4))


@pytest.fixture
def h(cfg, access):
    return DcHandlers(cfg, Catalog(cfg), access)


def make_inter(user_id):
    inter = MagicMock()
    inter.user.id = user_id
    inter.response.send_message = AsyncMock()
    return inter


def sent(inter):
    return inter.response.send_message.call_args


def test_cams_links(cfg, access):
    links = cams_links(cfg, Catalog(cfg), access, USER)
    assert [label for label, _ in links] == ["📹 Все камеры", "Двор", "Ворота", "Комната"]
    assert all(url.startswith("https://cams.example.com/?t=") for _, url in links)
    assert links[1][1].endswith("#cam=yard")
    tokens = {url.split("?t=")[1].split("#")[0] for _, url in links}
    assert len(tokens) == 4  # у каждой кнопки свой одноразовый токен
    assert access.redeem_link_token(links[2][1].split("?t=")[1].split("#")[0]) == USER


def test_cams_links_many_cameras(make_cfg):
    cams = [{"id": f"c{i}", "name": f"C{i}", "type": "rtsp", "url": "rtsp://x"} for i in range(25)]
    cfg = make_cfg(cameras=cams)
    access = Access(cfg, Store(":memory:"), StreamRegistry(4))
    assert len(cams_links(cfg, Catalog(cfg), access, USER)) == 1


async def test_cams_allowed(h):
    inter = make_inter(77)
    await h.cams(inter)
    call = sent(inter)
    assert call.kwargs["ephemeral"] is True
    assert len(call.kwargs["view"].children) == 4


async def test_cams_denied(h):
    inter = make_inter(999)
    await h.cams(inter)
    call = sent(inter)
    assert "999" in call.args[0]
    assert call.kwargs["ephemeral"] is True
    assert "view" not in call.kwargs


async def test_allow_mention_by_admin(h, access):
    inter = make_inter(2)
    await h.allow(inter, "<@5>")
    assert access.is_allowed(Identity("dc", 5))
    assert sent(inter).kwargs["ephemeral"] is True


async def test_allow_telegram_user(h, access):
    await h.allow(make_inter(2), "tg:9")
    assert access.is_allowed(Identity("tg", 9))


async def test_allow_non_admin(h, access):
    inter = make_inter(77)
    await h.allow(inter, "5")
    assert not access.is_allowed(Identity("dc", 5))
    assert "администратор" in sent(inter).args[0]


async def test_allow_bad_target(h):
    inter = make_inter(2)
    await h.allow(inter, "bob")
    assert "Использование" in sent(inter).args[0]


async def test_deny_and_users(h, access):
    await h.deny(make_inter(2), "77")
    assert not access.is_allowed(USER)
    inter = make_inter(2)
    await h.users(inter)
    assert "tg:42" in sent(inter).args[0]
