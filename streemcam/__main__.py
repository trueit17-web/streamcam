import argparse
import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

from .admin import parse_target
from .config import ConfigError, load_config


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="streemcam")
    parser.add_argument("--config", default=os.environ.get("STREEMCAM_CONFIG", "config.yaml"))
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("run", help="запустить веб-шлюз и ботов")
    render = sub.add_parser("render-go2rtc", help="сгенерировать go2rtc.yaml из config.yaml")
    render.add_argument("--out", default="go2rtc/go2rtc.yaml")
    link = sub.add_parser("link", help="выдать одноразовую ссылку на плеер")
    link.add_argument("user", help="tg:<id> или dc:<id>")
    link.add_argument("--base", help="адрес вместо public_url (например http://127.0.0.1:8080)")
    args = parser.parse_args(argv)

    try:
        cfg = load_config(args.config)
    except (ConfigError, OSError) as e:
        print(e, file=sys.stderr)
        return 2

    if args.cmd == "render-go2rtc":
        from .go2rtc_config import write_go2rtc_config
        write_go2rtc_config(args.out, cfg)
        print(f"written {args.out}")
        return 0

    if args.cmd == "link":
        from .app import build_access
        try:
            ident = parse_target(args.user, "tg")
        except ValueError as e:
            print(e, file=sys.stderr)
            return 2
        token = build_access(cfg).issue_link_token(ident)
        print(f"{(args.base or cfg.public_url).rstrip('/')}/?t={token}")
        return 0

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from .app import run
    try:
        asyncio.run(run(cfg))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
