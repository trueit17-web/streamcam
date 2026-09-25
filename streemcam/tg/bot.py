import logging

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo

from ..access import Access
from ..admin import USAGE, format_users, parse_target
from ..config import Config
from ..identity import Identity

log = logging.getLogger(__name__)

BUTTON_TEXT = "📹 Камеры"


class TgHandlers:
    def __init__(self, cfg: Config, access: Access, mini_app_link: str):
        self.cfg = cfg
        self.access = access
        self.mini_app_link = mini_app_link

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


def build_router(h: TgHandlers) -> Router:
    router = Router()
    router.message.register(h.cams, Command("start", "cams"))
    router.message.register(h.allow, Command("allow"))
    router.message.register(h.deny, Command("deny"))
    router.message.register(h.users, Command("users"))
    router.message.register(h.post_channel, Command("post_channel"))
    return router


async def notify_admins(bot: Bot, cfg: Config, text: str) -> None:
    for uid in cfg.admins.telegram:
        try:
            await bot.send_message(uid, text)
        except Exception as e:
            log.warning("cannot notify admin %s: %s", uid, e)


async def run_telegram(bot: Bot, cfg: Config, access: Access) -> None:
    me = await bot.get_me()
    link = f"https://t.me/{me.username}/{cfg.telegram.mini_app_short_name}"
    dp = Dispatcher()
    dp.include_router(build_router(TgHandlers(cfg, access, link)))
    log.info("telegram bot @%s started", me.username)
    await dp.start_polling(bot, handle_signals=False, close_bot_session=False)
