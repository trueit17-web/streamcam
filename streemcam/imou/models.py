from dataclasses import dataclass


class ImouError(Exception):
    pass


class ImouAuthError(ImouError):
    pass


@dataclass(frozen=True)
class ImouCamera:
    device_id: str
    name: str
    online: bool
    home: str | None = None
