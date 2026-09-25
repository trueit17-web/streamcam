import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable

from .identity import Identity

Closer = Callable[[], Awaitable[None]]


class StreamLimitError(Exception):
    pass


class StreamRegistry:
    def __init__(self, max_per_user: int):
        self._max = max_per_user
        self._active: dict[Identity, set[Closer]] = defaultdict(set)

    def acquire(self, ident: Identity, closer: Closer) -> None:
        if len(self._active[ident]) >= self._max:
            raise StreamLimitError(f"{ident} already has {self._max} streams")
        self._active[ident].add(closer)

    def release(self, ident: Identity, closer: Closer) -> None:
        closers = self._active.get(ident)
        if closers is None:
            return
        closers.discard(closer)
        if not closers:
            del self._active[ident]

    def count(self, ident: Identity) -> int:
        return len(self._active.get(ident, ()))

    async def kick(self, ident: Identity) -> None:
        closers = list(self._active.pop(ident, ()))
        results = await asyncio.gather(*(c() for c in closers), return_exceptions=True)
        logger = logging.getLogger(__name__)
        for closer, result in zip(closers, results):
            if isinstance(result, Exception):
                logger.warning(f"Failed to close stream for {ident}: {result!r}")


class TranscodeLimiter:
    """Глобальный лимит одновременных перекодировок (совместимый режим)."""

    def __init__(self, maximum: int):
        self._max = maximum
        self.active = 0

    def try_acquire(self) -> bool:
        if self.active >= self._max:
            return False
        self.active += 1
        return True

    def release(self) -> None:
        self.active = max(0, self.active - 1)
