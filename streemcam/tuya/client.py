from tuya_sharing import Manager, SharingTokenListener

from .login import CLIENT_ID
from .models import TuyaCamera, TuyaCredentials, TuyaError
from .store import TuyaStore

CAMERA_CATEGORY = "sp"


class _TokenSaver(SharingTokenListener):
    def __init__(self, store: TuyaStore):
        self._store = store

    def update_token(self, token_info: dict) -> None:
        self._store.update_token(token_info)


class TuyaClient:
    """Синхронный клиент облака Tuya; вызывать через asyncio.to_thread."""

    def __init__(self, manager):
        self._manager = manager

    @classmethod
    def from_credentials(cls, creds: TuyaCredentials, store: TuyaStore, manager_factory=None) -> "TuyaClient":
        factory = manager_factory or Manager
        manager = factory(CLIENT_ID, creds.user_code, creds.terminal_id, creds.endpoint,
                          creds.token_info, _TokenSaver(store))
        return cls(manager)

    def list_cameras(self) -> list[TuyaCamera]:
        # как Manager.update_device_cache, но запоминаем, в каком доме каждое устройство
        m = self._manager
        homes: dict[str, str] = {}
        try:
            m.device_map.clear()
            for home in m.home_repository.query_homes():
                for d in m.device_repository.query_devices_by_home(home.id):
                    m.device_map[d.id] = d
                    homes[d.id] = home.name
        except Exception as e:
            raise TuyaError(str(e)) from e
        return [TuyaCamera(d.id, d.name, bool(d.online), homes.get(d.id))
                for d in m.device_map.values() if d.category == CAMERA_CATEGORY]

    def allocate_rtsp(self, device_id: str) -> str:
        try:
            url = self._manager.get_device_stream_allocate(device_id, "rtsp")
        except Exception as e:
            raise TuyaError(str(e)) from e
        if not url:
            raise TuyaError(f"no stream url for {device_id}")
        return url
