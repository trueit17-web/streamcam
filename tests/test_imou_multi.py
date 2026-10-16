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

    async def stream_url(self, device_id):
        return f"{self.name}:{device_id}"


async def test_owner_account_wins_and_gets_home():
    main = Fake("main", [ImouCamera("A", "Мой двор", True), ImouCamera("S", "Чужая", True)])
    owner = Fake("owner", [ImouCamera("S", "Ворота", True), ImouCamera("O", "Гараж", False)])
    multi = MultiImouClient([(main, None), (owner, "Дача")])
    cams = {c.device_id: c for c in await multi.list_cameras()}
    assert cams["A"] == ImouCamera("A", "Мой двор", True)
    assert cams["S"] == ImouCamera("S", "Ворота", True, "Дача")
    assert cams["O"].home == "Дача"
    assert await multi.stream_url("A") == "main:A"
    assert await multi.stream_url("S") == "owner:S"
    assert await multi.stream_url("O") == "owner:O"


async def test_main_account_errors_propagate():
    multi = MultiImouClient([(Fake("main", error=ImouAuthError("x")), None), (Fake("o"), None)])
    with pytest.raises(ImouAuthError):
        await multi.list_cameras()


@pytest.mark.parametrize("error", [ImouError("down"), ImouAuthError("bad key")])
async def test_extra_account_errors_are_skipped(error):
    main = Fake("main", [ImouCamera("S", "Чужая", True)])
    multi = MultiImouClient([(main, None), (Fake("owner", error=error), "Дача")])
    assert await multi.list_cameras() == [ImouCamera("S", "Чужая", True)]
    assert await multi.stream_url("S") == "main:S"
