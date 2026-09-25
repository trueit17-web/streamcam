from streemcam.tuya.models import TuyaCredentials
from streemcam.tuya.store import TuyaStore

CREDS = TuyaCredentials("UC1", "term-1", "https://apigw.tuyaeu.com",
                        {"t": 1, "uid": "u1", "expire_time": 7200,
                         "access_token": "a1", "refresh_token": "r1"})


def test_empty_store():
    assert TuyaStore(":memory:").load() is None


def test_save_load_roundtrip():
    s = TuyaStore(":memory:")
    s.save(CREDS)
    assert s.load() == CREDS


def test_save_replaces_single_row():
    s = TuyaStore(":memory:")
    s.save(CREDS)
    s.save(TuyaCredentials("UC2", "term-2", "https://x", {"uid": "u2"}))
    assert s.load().user_code == "UC2"


def test_update_token():
    s = TuyaStore(":memory:")
    s.save(CREDS)
    s.update_token({"t": 2, "uid": "u1", "access_token": "a2", "refresh_token": "r2", "expire_time": 7200})
    assert s.load().token_info["access_token"] == "a2"
    assert s.load().user_code == "UC1"


def test_update_token_without_account_is_noop():
    s = TuyaStore(":memory:")
    s.update_token({"access_token": "a"})
    assert s.load() is None


def test_clear():
    s = TuyaStore(":memory:")
    s.save(CREDS)
    s.clear()
    assert s.load() is None


def test_file_store_persists(tmp_path):
    path = str(tmp_path / "d" / "x.db")
    TuyaStore(path).save(CREDS)
    assert TuyaStore(path).load() == CREDS
