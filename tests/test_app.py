import asyncio

import pytest

from streemcam.app import alert_text, make_tg_bot, parse_listen, supervise, sync_forever
from streemcam.catalog import Catalog


def test_make_tg_bot_returns_none_for_invalid_token(caplog):
    with caplog.at_level("ERROR"):
        bot = make_tg_bot("bad-token")
    assert bot is None
    assert "telegram" in caplog.text.lower()


def test_make_tg_bot_returns_bot_for_valid_token():
    bot = make_tg_bot("123456:TEST-TOKEN-abcdefghij")
    assert bot is not None


def test_alert_text(cfg):
    catalog = Catalog(cfg)
    assert alert_text(catalog, cfg, "yard", False) == "⚠️ Камера «Двор» офлайн больше 5 мин."
    assert alert_text(catalog, cfg, "yard", True) == "✅ Камера «Двор» снова онлайн."


async def test_supervise_restarts_after_crash():
    calls = []

    async def factory():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("boom")
        await asyncio.Event().wait()  # третий запуск «живёт»

    task = asyncio.create_task(supervise("x", factory, base_delay=0, max_delay=0))
    for _ in range(50):
        await asyncio.sleep(0)
        if len(calls) == 3:
            break
    assert len(calls) == 3
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_parse_listen():
    assert parse_listen("127.0.0.1:8081") == ("127.0.0.1", 8081)
    assert parse_listen("0.0.0.0:8081") == ("0.0.0.0", 8081)
    assert parse_listen(":8081") == ("0.0.0.0", 8081)


async def test_sync_forever_repeats():
    class S:
        n = 0

        async def sync(self):
            S.n += 1

    task = asyncio.create_task(sync_forever(S(), 0))
    for _ in range(20):
        await asyncio.sleep(0)
    task.cancel()
    assert S.n >= 2


async def test_cleanup_forever_calls_cleanup(make_cfg, tmp_path):
    from datetime import datetime, timezone
    from streemcam.app import cleanup_forever

    cfg = make_cfg(recording={"enabled": True, "path": str(tmp_path), "retention_days": 3, "min_free_gb": 1})
    calls = []

    class A:
        def cleanup(self, today, retention_days, min_free_gb):
            calls.append((today.isoformat(), retention_days, min_free_gb))
            return []

    task = asyncio.create_task(cleanup_forever(
        A(), cfg, 0, now=lambda: datetime(2026, 9, 25, 22, 0, tzinfo=timezone.utc)))
    for _ in range(50):
        await asyncio.sleep(0)
        if len(calls) >= 2:
            break
    task.cancel()
    assert calls[0] == ("2026-09-26", 3, 1)   # 22:00 UTC = 01:00 MSK следующего дня
