import re

from .identity import Identity, Platform

_MENTION = re.compile(r"^<@!?(\d+)>$")

USAGE = ("Использование: /allow <id> | tg:<id> | dc:<id> "
         "(или ответом на сообщение пользователя). То же для /deny.")


def parse_target(arg: str, default_platform: Platform) -> Identity:
    arg = arg.strip()
    mention = _MENTION.match(arg)
    if mention:
        return Identity("dc", int(mention.group(1)))
    platform = default_platform
    if ":" in arg:
        prefix, arg = arg.split(":", 1)
        if prefix not in ("tg", "dc"):
            raise ValueError(f"unknown platform: {prefix}")
        platform = prefix
    if not arg.isdigit():
        raise ValueError(f"not a user id: {arg!r}")
    return Identity(platform, int(arg))


def format_users(idents: list[Identity]) -> str:
    if not idents:
        return "Белый список пуст."
    return "Белый список:\n" + "\n".join(str(i) for i in idents)
