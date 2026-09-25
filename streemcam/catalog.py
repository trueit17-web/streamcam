import logging
import re
from dataclasses import dataclass
from typing import Literal

from .config import Config
from .tuya.models import TuyaCamera

log = logging.getLogger(__name__)

TUYA_DEVICE_ID = re.compile(r"^[A-Za-z0-9]+$")


def tuya_cam_id(device_id: str) -> str:
    return "tuya_" + device_id.lower()


@dataclass(frozen=True)
class CameraInfo:
    id: str
    name: str
    kind: Literal["config", "tuya"]
    device_id: str | None = None
    online: bool | None = None


class Catalog:
    """Единый список камер: из config.yaml (статично) + камеры Tuya (обновляются в рантайме)."""

    def __init__(self, cfg: Config):
        self._config = [CameraInfo(c.id, c.name, "config") for c in cfg.cameras]
        self._tuya: list[CameraInfo] = []

    def all(self) -> list[CameraInfo]:
        return self._config + self._tuya

    def get(self, cam_id: str) -> CameraInfo | None:
        return next((c for c in self.all() if c.id == cam_id), None)

    def tuya(self) -> list[CameraInfo]:
        return list(self._tuya)

    def set_tuya(self, cameras: list[TuyaCamera]) -> None:
        infos = []
        for cam in cameras:
            if not TUYA_DEVICE_ID.match(cam.device_id):
                log.warning("skipping Tuya device with unsafe id %r", cam.device_id)
                continue
            infos.append(CameraInfo(tuya_cam_id(cam.device_id), cam.name, "tuya",
                                    device_id=cam.device_id, online=cam.online))
        self._tuya = sorted(infos, key=lambda c: c.name)

    def mark_tuya_offline(self) -> None:
        """Спека §5: при недоступности облака камеры остаются в списке, но помечаются офлайн."""
        self._tuya = [CameraInfo(c.id, c.name, c.kind, device_id=c.device_id, online=False)
                      for c in self._tuya]
