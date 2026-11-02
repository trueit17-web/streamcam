import pytest

from streemcam.imou.models import ImouAuthError, ImouCamera, ImouError
from streemcam.imou.multi import MultiImouClient


class Fake:
    def __init__(self, name, cams=None, error=None):
        self.name = name
        self.cams = cams or []
        self.error = error

    async def list_cameras(self):
        if self.error:
            raise self.error
        return list(self.cams)

    async def stream_urls(self, device_id):
        return {0: f"{self.name}:{device_id}"}


async def test_owner_account_wins_and_gets_home():
    main = Fake("main", [ImouCamera("A", "Мой двор", True), ImouCamera("S", "Чужая", True)])
    owner = Fake("owner", [ImouCamera("S", "Ворота", True), ImouCamera("O", "Гараж", False)])
    multi = MultiImouClient([(main, None), (owner, "Дача")])
    cams = {c.device_id: c for c in await multi.list_cameras()}
    assert cams["A"] == ImouCamera("A", "Мой двор", True)
    assert cams["S"] == ImouCamera("S", "Ворота", True, "Дача")
    assert cams["O"].home == "Дача"
    assert await multi.stream_urls("A") == {0: "main:A"}
    assert await multi.stream_urls("S") == {0: "owner:S"}
    assert await multi.stream_urls("O") == {0: "owner:O"}


async def test_all_accounts_failing_raises_first_error():
    multi = MultiImouClient([(Fake("main", error=ImouAuthError("x")), None), (Fake("o", error=ImouError("y")), None)])
    with pytest.raises(ImouAuthError):
        await multi.list_cameras()


@pytest.mark.parametrize("error", [ImouError("OP1013 limit"), ImouAuthError("bad key")])
async def test_main_account_error_keeps_other_accounts(error):
    main = Fake("main", error=error)
    owner = Fake("owner", [ImouCamera("O", "Гараж", True)])
    multi = MultiImouClient([(main, None), (owner, "Дача")])
    assert await multi.list_cameras() == [ImouCamera("O", "Гараж", True, "Дача")]
    assert await multi.stream_urls("O") == {0: "owner:O"}


@pytest.mark.parametrize("error", [ImouError("down"), ImouAuthError("bad key")])
async def test_extra_account_errors_are_skipped(error):
    main = Fake("main", [ImouCamera("S", "Чужая", True)])
    multi = MultiImouClient([(main, None), (Fake("owner", error=error), "Дача")])
    assert await multi.list_cameras() == [ImouCamera("S", "Чужая", True)]
    assert await multi.stream_urls("S") == {0: "main:S"}


async def test_failed_account_keeps_last_known_cameras():
    main = Fake("main", [ImouCamera("A", "Двор", True)])
    owner = Fake("owner", [ImouCamera("O", "Гараж", True)])
    multi = MultiImouClient([(main, None), (owner, None)])
    await multi.list_cameras()
    main.error = ImouError("OP1013 limit")
    cams = {c.device_id for c in await multi.list_cameras()}
    assert cams == {"A", "O"}
    assert await multi.stream_urls("A") == {0: "main:A"}
