import pytest

from streemcam.tokens import TokenError, sign, verify

SECRET = "s" * 20


def test_roundtrip():
    t = sign({"typ": "session", "u": 1, "exp": 2000}, SECRET)
    assert verify(t, SECRET, "session", now=1000)["u"] == 1


def test_expired():
    t = sign({"typ": "session", "exp": 999}, SECRET)
    with pytest.raises(TokenError, match="expired"):
        verify(t, SECRET, "session", now=1000)


def test_wrong_secret():
    t = sign({"typ": "session", "exp": 2000}, SECRET)
    with pytest.raises(TokenError, match="signature"):
        verify(t, "x" * 20, "session", now=1000)


def test_wrong_type():
    t = sign({"typ": "link", "exp": 2000}, SECRET)
    with pytest.raises(TokenError, match="type"):
        verify(t, SECRET, "session", now=1000)


def test_tampered_body():
    t = sign({"typ": "session", "u": 1, "exp": 2000}, SECRET)
    other = sign({"typ": "session", "u": 2, "exp": 2000}, SECRET)
    forged = other.split(".")[0] + "." + t.split(".")[1]
    with pytest.raises(TokenError):
        verify(forged, SECRET, "session", now=1000)


@pytest.mark.parametrize("bad", ["", "abc", "a.b.c", "!!!.???"])
def test_malformed(bad):
    with pytest.raises(TokenError):
        verify(bad, SECRET, "session", now=1000)
