import secrets
import time
from collections.abc import Callable

from .config import Config
from .db import Store
from .identity import Identity
from .streams import StreamRegistry
from .tg_auth import TgAuthError, verify_init_data, verify_login_widget
from .tokens import TokenError, sign, verify


class AccessDenied(Exception):
    def __init__(self, identity: Identity):
        super().__init__(f"{identity} is not allowed")
        self.identity = identity


class Access:
    def __init__(self, cfg: Config, store: Store, registry: StreamRegistry,
                 clock: Callable[[], float] = time.time):
        self.cfg = cfg
        self.store = store
        self.registry = registry
        self._clock = clock
        # вход на сайт через приложение Telegram: код -> [срок, Identity или None, пока бот не подтвердил]
        self._web_logins: dict[str, list] = {}
        for platform in ("tg", "dc"):
            for uid in cfg.allow.ids(platform):
                store.add_allowed(Identity(platform, uid), None)

    def is_admin(self, ident: Identity) -> bool:
        return ident.user_id in self.cfg.admins.ids(ident.platform)

    def is_allowed(self, ident: Identity) -> bool:
        return self.is_admin(ident) or self.store.is_allowed(ident)

    def allow(self, ident: Identity, by: Identity) -> bool:
        return self.store.add_allowed(ident, by.user_id)

    async def deny(self, ident: Identity) -> bool:
        removed = self.store.remove_allowed(ident)
        await self.registry.kick(ident)
        return removed

    def list_allowed(self) -> list[Identity]:
        return self.store.list_allowed()

    def issue_link_token(self, ident: Identity) -> str:
        jti = secrets.token_urlsafe(12)
        exp = self._clock() + self.cfg.token_ttl_minutes * 60
        self.store.add_link_token(jti, ident, exp)
        return sign({"typ": "link", "jti": jti, "p": ident.platform, "u": ident.user_id, "exp": exp},
                    self.cfg.secret)

    def redeem_link_token(self, token: str) -> Identity:
        now = self._clock()
        payload = verify(token, self.cfg.secret, "link", now=now)
        if not self.store.use_link_token(payload["jti"], now):
            raise TokenError("link token already used")
        return self._require_allowed(Identity(payload["p"], payload["u"]))

    def issue_session(self, ident: Identity, hours: float | None = None) -> str:
        exp = self._clock() + (self.cfg.session_ttl_hours if hours is None else hours) * 3600
        return sign({"typ": "session", "p": ident.platform, "u": ident.user_id, "exp": exp},
                    self.cfg.secret)

    def check_session(self, token: str) -> Identity:
        payload = verify(token, self.cfg.secret, "session", now=self._clock())
        return self._require_allowed(Identity(payload["p"], payload["u"]))

    def login_telegram(self, init_data: str) -> Identity:
        bot_token = self.cfg.telegram.bot_token
        if not bot_token:
            raise TgAuthError("telegram bot is not configured")
        ident = verify_init_data(init_data, bot_token, now=self._clock())
        return self._require_allowed(ident)

    def login_telegram_widget(self, fields: dict) -> Identity:
        bot_token = self.cfg.telegram.bot_token
        if not bot_token:
            raise TgAuthError("telegram bot is not configured")
        return self._require_allowed(verify_login_widget(fields, bot_token, now=self._clock()))

    WEB_LOGIN_TTL = 300

    def start_web_login(self) -> str:
        now = self._clock()
        self._web_logins = {k: v for k, v in self._web_logins.items() if v[0] > now}
        code = secrets.token_urlsafe(16)
        self._web_logins[code] = [now + self.WEB_LOGIN_TTL, None]
        return code

    def confirm_web_login(self, code: str, ident: Identity) -> bool:
        """Бот получил /start login_<code> от пользователя: привязываем код к нему."""
        entry = self._web_logins.get(code)
        if entry is None or entry[0] <= self._clock() or entry[1] is not None:
            return False
        self._require_allowed(ident)
        entry[1] = ident
        return True

    def poll_web_login(self, code: str) -> Identity | None:
        """None — ещё ждём; Identity — подтверждено (код больше не действует); TokenError — код неизвестен или истёк."""
        entry = self._web_logins.get(code)
        if entry is None or entry[0] <= self._clock():
            self._web_logins.pop(code, None)
            raise TokenError("unknown or expired login code")
        if entry[1] is None:
            return None
        del self._web_logins[code]
        return entry[1]

    def _require_allowed(self, ident: Identity) -> Identity:
        if not self.is_allowed(ident):
            raise AccessDenied(ident)
        return ident
