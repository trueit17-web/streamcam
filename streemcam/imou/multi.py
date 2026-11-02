import dataclasses
import logging

from .models import ImouAuthError, ImouCamera, ImouError

log = logging.getLogger(__name__)


class MultiImouClient:
    """Несколько аккаунтов Imou как один клиент.
    Камера, которую видят несколько аккаунтов, берётся из последнего: это владелец,
    а основному аккаунту она лишь расшарена, и видео ему облако не отдаёт.
    Если аккаунт не ответил (например, исчерпал лимит запросов), его камеры берутся из прошлого
    удачного списка, а остальные аккаунты работают как обычно. Ошибка наружу — только если не ответил никто."""

    def __init__(self, accounts: list[tuple[object, str | None]]):
        self._accounts = accounts
        self._owner: dict[str, object] = {}
        self._last: dict[int, list[ImouCamera]] = {}

    async def list_cameras(self) -> list[ImouCamera]:
        cams: dict[str, ImouCamera] = {}
        owner: dict[str, object] = {}
        first_error: ImouError | None = None
        answered = False
        for i, (client, home) in enumerate(self._accounts):
            try:
                found = await client.list_cameras()
            except ImouError as e:
                first_error = first_error or e
                kind = "credentials rejected" if isinstance(e, ImouAuthError) else "failed"
                log.warning("imou account %d %s: %s", i, kind, e)
                found = self._last.get(i)
                if found is None:
                    continue
            else:
                answered = True
                self._last[i] = found
            for cam in found:
                if home and not cam.home:
                    cam = dataclasses.replace(cam, home=home)
                cams[cam.device_id] = cam
                owner[cam.device_id] = client
        if not answered and first_error is not None:
            raise first_error
        self._owner = owner
        return list(cams.values())

    async def stream_urls(self, device_id: str) -> dict[int, str]:
        client = self._owner.get(device_id) or self._accounts[0][0]
        return await client.stream_urls(device_id)
