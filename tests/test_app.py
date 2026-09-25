import asyncio

import pytest

from streemcam.app import alert_text, supervise


def test_alert_text(cfg):
    assert alert_text(cfg, "yard", False) == "⚠️ Камера «Двор» офлайн больше 5 мин."
    assert alert_text(cfg, "yard", True) == "✅ Камера «Двор» снова онлайн."


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
