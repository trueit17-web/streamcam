from dataclasses import dataclass
from typing import Literal

Platform = Literal["tg", "dc"]


@dataclass(frozen=True)
class Identity:
    platform: Platform
    user_id: int

    def __str__(self) -> str:
        return f"{self.platform}:{self.user_id}"
