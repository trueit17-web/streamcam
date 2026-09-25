from dataclasses import dataclass, field


class TuyaError(Exception):
    pass


class TuyaLoginError(TuyaError):
    pass


@dataclass
class TuyaCredentials:
    user_code: str
    terminal_id: str
    endpoint: str
    token_info: dict = field(default_factory=dict)


@dataclass(frozen=True)
class TuyaCamera:
    device_id: str
    name: str
    online: bool
