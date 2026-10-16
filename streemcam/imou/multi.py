import dataclasses
import logging

from .models import ImouAuthError, ImouCamera, ImouError

log = logging.getLogger(__name__)


class MultiImouClient:
    """Несколько аккаунтов Imou как один клиент. Первый — основной: его ошибки идут наружу.
    Камера, которую видят несколько аккаунтов, берётся из последнего: это владелец,
    а основному аккаунту она лишь расшарена, и видео ему облако не отдаёт."""

    def __init__(self, accounts: list[tuple[object, str | None]]):
        self._accounts = accounts
        self._owner: dict[str, object] = {}

    async def list_cameras(self) -> list[ImouCamera]:
        cams: dict[str, ImouCamera] = {}
        owner: dict[str, object] = {}
        for i, (client, home) in enumerate(self._accounts):
            try:
                found = await client.list_cameras()
            except ImouError as e:
                if i == 0:
                    raise
                kind = "credentials rejected" if isinstance(e, ImouAuthError) else "failed"
                log.warning("imou extra account %d %s: %s", i, kind, e)
                continue
            for cam in found:
                if home and not cam.home:
                    cam = dataclasses.replace(cam, home=home)
                cams[cam.device_id] = cam
                owner[cam.device_id] = client
        self._owner = owner
        return list(cams.values())

    async def stream_url(self, device_id: str) -> str:
        client = self._owner.get(device_id) or self._accounts[0][0]
        return await client.stream_url(device_id)
