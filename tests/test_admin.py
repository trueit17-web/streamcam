import pytest

from streemcam.admin import format_users, parse_target
from streemcam.identity import Identity


@pytest.mark.parametrize("arg,default,expected", [
    ("123", "tg", Identity("tg", 123)),
    (" 123 ", "dc", Identity("dc", 123)),
    ("dc:5", "tg", Identity("dc", 5)),
    ("tg:9", "dc", Identity("tg", 9)),
    ("<@55>", "tg", Identity("dc", 55)),
    ("<@!55>", "tg", Identity("dc", 55)),
])
def test_parse_target(arg, default, expected):
    assert parse_target(arg, default) == expected


@pytest.mark.parametrize("arg", ["", "abc", "xx:1", "tg:", "tg:-1", "@user"])
def test_parse_target_rejects(arg):
    with pytest.raises(ValueError):
        parse_target(arg, "tg")


def test_format_users():
    assert format_users([]) == "Белый список пуст."
    assert format_users([Identity("tg", 1), Identity("dc", 2)]) == "Белый список:\ntg:1\ndc:2"
