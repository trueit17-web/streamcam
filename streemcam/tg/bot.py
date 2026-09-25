import logging

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo

from ..access import Access
from ..admin import USAGE, format_users, parse_target
from ..catalog import CameraInfo
from ..config import Config
from ..identity import Identity
from ..tuya.models import TuyaLoginError
from ..tuya.qr import qr_png

log = logging.getLogger(__name__)

BUTTON_TEXT = "📹 Камеры"

TUYA_USAGE = ("Использование: /tuya_login <код пользователя>. Код — в приложении Smart Life/Tuya Smart: "
              "«Я» → ⚙ Настройки → «Аккаунт и безопасность» → «Код пользователя».")
TUYA_QR_CAPTION = ("Откройте Smart Life → «+» (или значок сканера) → отсканируйте этот код и подтвердите вход. "
                   "Жду 2 минуты.")


def format_tuya_cameras(cams: list[CameraInfo]) -> str:
    if not cams:
        return "Камер Tuya не найдено."
    lines = [f"• {c.name} ({c.id}) — {'онлайн' if c.online else 'офлайн'}" for c in cams]
    return f"Камеры Tuya ({len(cams)}):\n" + "\n".join(lines)


class TgHandlers:
    def __init__(self, cfg: Config, access: Access, mini_app_link: str, tuya=None):
        self.cfg = cfg
        self.access = access
        self.mini_app_link = mini_app_link
        self.tuya = tuya

    @staticmethod
    def _ident(message: Message) -> Identity | None:
        return Identity("tg", message.from_user.id) if message.from_user else None

    def _link_markup(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=BUTTON_TEXT, url=self.mini_app_link)]])

    async def cams(self, message: Message) -> None:
        ident = self._ident(message)
        if ident is None:
            return
        if not self.access.is_allowed(ident):
            await message.answer(f"Нет доступа. Ваш ID: {ident.user_id} — передайте его администратору.")
            return
        if message.chat.type == "private":
            markup = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=BUTTON_TEXT, web_app=WebAppInfo(url=self.cfg.public_url))]])
        else:
            markup = self._link_markup()
        await message.answer("Нажмите, чтобы открыть камеры:", reply_markup=markup)

    async def _require_admin(self, message: Message) -> Identity | None:
        ident = self._ident(message)
        if ident is None or not self.access.is_admin(ident):
            await message.answer("Команда только для администраторов.")
            return None
        return ident

    @staticmethod
    def _target(message: Message, command: CommandObject) -> Identity:
        if command.args:
            return parse_target(command.args, "tg")
        reply = message.reply_to_message
        if reply is not None and reply.from_user is not None:
            return Identity("tg", reply.from_user.id)
        raise ValueError("no target")

    async def allow(self, message: Message, command: CommandObject) -> None:
        admin = await self._require_admin(message)
        if admin is None:
            return
        try:
            target = self._target(message, command)
        except ValueError:
            await message.answer(USAGE)
            return
        added = self.access.allow(target, by=admin)
        await message.answer(f"✅ {target} добавлен." if added else f"{target} уже в списке.")

    async def deny(self, message: Message, command: CommandObject) -> None:
        admin = await self._require_admin(message)
        if admin is None:
            return
        try:
            target = self._target(message, command)
        except ValueError:
            await message.answer(USAGE)
            return
        removed = await self.access.deny(target)
        await message.answer(f"🚫 {target} удалён." if removed else f"{target} не было в списке.")

    async def users(self, message: Message) -> None:
        if await self._require_admin(message) is None:
            return
        await message.answer(format_users(self.access.list_allowed()))

    async def post_channel(self, message: Message) -> None:
        if await self._require_admin(message) is None:
            return
        channel = self.cfg.telegram.channel_id
        if channel is None:
            await message.answer("telegram.channel_id не задан в конфиге.")
            return
        await message.bot.send_message(
            channel, "📹 Камеры онлайн. Нажмите кнопку, чтобы смотреть.", reply_markup=self._link_markup())
        await message.answer("Опубликовано. Закрепите пост в канале.")

    async def _require_tuya_admin(self, message: Message) -> bool:
        if await self._require_admin(message) is None:
            return False
        if self.tuya is None:
            await message.answer("Интеграция Tuya отключена в конфиге (tuya.enabled).")
            return False
        return True

    async def tuya_login(self, message: Message, command: CommandObject) -> None:
        if not await self._require_tuya_admin(message):
            return
        code = (command.args or "").strip()
        if not code:
            await message.answer(TUYA_USAGE)
            return
        try:
            session = await self.tuya.start_login(code)
        except TuyaLoginError as e:
            await message.answer(f"Не удалось начать вход в Tuya: {e}")
            return
        await message.answer_photo(BufferedInputFile(qr_png(session.qr_payload), "tuya-login.png"),
                                   caption=TUYA_QR_CAPTION)
        if not await self.tuya.wait_login(session):
            await message.answer("Вход не подтвёрждён (истекло время или начата новая попытка). "
                                 "Повторите /tuya_login <код пользователя>.")
            return
        await message.answer("✅ Вход в Tuya выполнен.\n" + format_tuya_cameras(self.tuya.cameras()))

    async def tuya_status(self, message: Message) -> None:
        if not await self._require_tuya_admin(message):
            return
        if not self.tuya.logged_in:
            await message.answer("Вход в Tuya не выполнен. " + TUYA_USAGE)
            return
        await message.answer("Вход в Tuya выполнен.\n" + format_tuya_cameras(self.tuya.cameras()))

    async def tuya_logout(self, message: Message) -> None:
        if not await self._require_tuya_admin(message):
            return
        await self.tuya.logout()
        await message.answer("Выход из Tuya выполнен, камеры Tuya убраны из списка.")


def build_router(h: TgHandlers) -> Router:
    router = Router()
    router.message.register(h.cams, Command("start", "cams"))
    router.message.register(h.allow, Command("allow"))
    router.message.register(h.deny, Command("deny"))
    router.message.register(h.users, Command("users"))
    router.message.register(h.post_channel, Command("post_channel"))
    router.message.register(h.tuya_login, Command("tuya_login"))
    router.message.register(h.tuya_status, Command("tuya_status"))
    router.message.register(h.tuya_logout, Command("tuya_logout"))
    return router


async def notify_admins(bot: Bot, cfg: Config, text: str) -> None:
    for uid in cfg.admins.telegram:
        try:
            await bot.send_message(uid, text)
        except Exception as e:
            log.warning("cannot notify admin %s: %s", uid, e)


async def run_telegram(bot: Bot, cfg: Config, access: Access, tuya=None) -> None:
    me = await bot.get_me()
    link = f"https://t.me/{me.username}/{cfg.telegram.mini_app_short_name}"
    dp = Dispatcher()
    dp.include_router(build_router(TgHandlers(cfg, access, link, tuya=tuya)))
    log.info("telegram bot @%s started", me.username)
    await dp.start_polling(bot, handle_signals=False, close_bot_session=False)
