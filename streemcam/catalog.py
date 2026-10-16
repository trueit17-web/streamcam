import dataclasses
import logging
import re
from dataclasses import dataclass
from typing import Literal

from .config import Config
from .imou.models import ImouCamera
from .tuya.models import TuyaCamera

log = logging.getLogger(__name__)

TUYA_DEVICE_ID = re.compile(r"^[A-Za-z0-9]+$")
IMOU_DEVICE_ID = TUYA_DEVICE_ID


def tuya_cam_id(device_id: str) -> str:
    return "tuya_" + device_id.lower()


def imou_cam_id(device_id: str) -> str:
    return "imou_" + device_id.lower()


@dataclass(frozen=True)
class CameraInfo:
    id: str
    name: str
    kind: Literal["config", "tuya", "imou"]
    device_id: str | None = None
    online: bool | None = None
    home: str | None = None


class Catalog:
    """Единый список камер: из config.yaml (статично) + камеры Tuya и Imou (обновляются в рантайме)."""

    def __init__(self, cfg: Config):
        self._homes = dict(cfg.homes)
        self._config = [CameraInfo(c.id, c.name, "config", home=self._home(c.id, c.name, c.home))
                        for c in cfg.cameras]
        self._tuya: list[CameraInfo] = []
        self._imou: list[CameraInfo] = []
        # Провайдер «загружен», когда хотя бы раз опубликовал список (выключенный — сразу).
        # До этого Go2rtcSync не трогает его потоки: иначе при старте go2rtc перезапускается лишний раз.
        self.tuya_loaded = not cfg.tuya.enabled
        self.imou_loaded = not cfg.imou.enabled
        self._imou_home = cfg.imou.home

    def _home(self, cam_id: str, name: str, default: str | None) -> str | None:
        """config.homes: ключ — id камеры или её название; id важнее."""
        return self._homes.get(cam_id) or self._homes.get(name) or default

    def all(self) -> list[CameraInfo]:
        return self._config + self._tuya + self._imou

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
                                    device_id=cam.device_id, online=cam.online,
                                    home=self._home(tuya_cam_id(cam.device_id), cam.name, cam.home)))
        self._tuya = sorted(infos, key=lambda c: c.name)
        self.tuya_loaded = True

    def mark_tuya_offline(self) -> None:
        """Спека §5: при недоступности облака камеры остаются в списке, но помечаются офлайн."""
        self._tuya = [dataclasses.replace(c, online=False)
                      for c in self._tuya]

    def imou(self) -> list[CameraInfo]:
        return list(self._imou)

    def set_imou(self, cameras: list[ImouCamera]) -> None:
        infos = []
        for cam in cameras:
            if not IMOU_DEVICE_ID.match(cam.device_id):
                log.warning("skipping Imou device with unsafe id %r", cam.device_id)
                continue
            infos.append(CameraInfo(imou_cam_id(cam.device_id), cam.name, "imou",
                                    device_id=cam.device_id, online=cam.online,
                                    home=self._home(imou_cam_id(cam.device_id), cam.name, self._imou_home)))
        self._imou = sorted(infos, key=lambda c: c.name)
        self.imou_loaded = True

    def mark_imou_offline(self) -> None:
        self._imou = [dataclasses.replace(c, online=False)
                      for c in self._imou]
