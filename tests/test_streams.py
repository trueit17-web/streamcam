import pytest

from streemcam.identity import Identity
from streemcam.streams import StreamLimitError, StreamRegistry

A = Identity("tg", 42)


def make_closer(log):
    async def closer():
        log.append(closer)
    return closer


async def test_limit_and_release():
    r = StreamRegistry(max_per_user=2)
    c1, c2, c3 = make_closer([]), make_closer([]), make_closer([])
    r.acquire(A, c1)
    r.acquire(A, c2)
    with pytest.raises(StreamLimitError):
        r.acquire(A, c3)
    r.release(A, c1)
    r.acquire(A, c3)
    assert r.count(A) == 2
    assert r.count(Identity("dc", 42)) == 0


async def test_kick_calls_all_closers():
    r = StreamRegistry(max_per_user=4)
    log = []
    c1, c2 = make_closer(log), make_closer(log)
    r.acquire(A, c1)
    r.acquire(A, c2)
    await r.kick(A)
    assert set(log) == {c1, c2}
    assert r.count(A) == 0
    await r.kick(A)  # повторный kick безопасен


async def test_release_unknown_is_noop():
    r = StreamRegistry(max_per_user=1)
    r.release(A, make_closer([]))
    assert r.count(A) == 0


async def test_kick_calls_all_closers_even_if_one_fails():
    r = StreamRegistry(max_per_user=4)
    log = []

    async def failing_closer():
        log.append("failing")
        raise RuntimeError("closer failed")

    async def success_closer():
        log.append("success")

    r.acquire(A, failing_closer)
    r.acquire(A, success_closer)
    await r.kick(A)
    assert "failing" in log
    assert "success" in log
    assert r.count(A) == 0
