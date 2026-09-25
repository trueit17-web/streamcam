from streemcam.db import Store
from streemcam.identity import Identity

A = Identity("tg", 42)
B = Identity("dc", 77)


def test_allowed_crud():
    s = Store(":memory:")
    assert s.add_allowed(A, 1) is True
    assert s.add_allowed(A, 1) is False
    s.add_allowed(B, None)
    assert s.is_allowed(A) and s.is_allowed(B)
    assert not s.is_allowed(Identity("dc", 42))  # платформа важна
    assert set(s.list_allowed()) == {A, B}
    assert s.remove_allowed(A) is True
    assert s.remove_allowed(A) is False
    assert not s.is_allowed(A)


def test_link_token_single_use():
    s = Store(":memory:")
    s.add_link_token("j1", A, expires_at=200)
    assert s.use_link_token("j1", now=100) is True
    assert s.use_link_token("j1", now=100) is False


def test_link_token_expired_or_unknown():
    s = Store(":memory:")
    s.add_link_token("j1", A, expires_at=200)
    assert s.use_link_token("j1", now=201) is False
    assert s.use_link_token("nope", now=100) is False


def test_file_db_creates_parent(tmp_path):
    s = Store(str(tmp_path / "sub" / "x.db"))
    s.add_allowed(A, None)
    assert Store(str(tmp_path / "sub" / "x.db")).is_allowed(A)
