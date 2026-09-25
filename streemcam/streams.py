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
        for closer in list(self._active.pop(ident, ())):
            await closer()
