import logging

import discord
from discord import app_commands

from ..access import Access
from ..admin import USAGE, format_users, parse_target
from ..config import Config
from ..identity import Identity

log = logging.getLogger(__name__)

MAX_CAMERA_BUTTONS = 24  # Discord: не больше 25 кнопок в сообщении, одна занята «Все камеры»


def cams_links(cfg: Config, access: Access, ident: Identity) -> list[tuple[str, str]]:
    def link(fragment: str = "") -> str:
        return f"{cfg.public_url}/?t={access.issue_link_token(ident)}{fragment}"

    links = [("📹 Все камеры", link())]
    if len(cfg.cameras) <= MAX_CAMERA_BUTTONS:
        links += [(cam.name, link(f"#cam={cam.id}")) for cam in cfg.cameras]
    return links


def build_view(links: list[tuple[str, str]]) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for label, url in links:
        view.add_item(discord.ui.Button(style=discord.ButtonStyle.link, label=label[:80], url=url))
    return view


class DcHandlers:
    def __init__(self, cfg: Config, access: Access):
        self.cfg = cfg
        self.access = access

    async def _reply(self, interaction: discord.Interaction, text: str, **kwargs) -> None:
        await interaction.response.send_message(text, ephemeral=True, **kwargs)

    async def cams(self, interaction: discord.Interaction) -> None:
        ident = Identity("dc", interaction.user.id)
        if not self.access.is_allowed(ident):
            await self._reply(interaction, f"Нет доступа. Ваш ID: {ident.user_id} — передайте его администратору.")
            return
        view = build_view(cams_links(self.cfg, self.access, ident))
        await self._reply(
            interaction,
            f"Ссылки личные и одноразовые, действуют {self.cfg.token_ttl_minutes} мин.",
            view=view)

    async def _require_admin(self, interaction: discord.Interaction) -> Identity | None:
        ident = Identity("dc", interaction.user.id)
        if not self.access.is_admin(ident):
            await self._reply(interaction, "Команда только для администраторов.")
            return None
        return ident

    async def allow(self, interaction: discord.Interaction, target: str) -> None:
        admin = await self._require_admin(interaction)
        if admin is None:
            return
        try:
            ident = parse_target(target, "dc")
        except ValueError:
            await self._reply(interaction, USAGE)
            return
        added = self.access.allow(ident, by=admin)
        await self._reply(interaction, f"✅ {ident} добавлен." if added else f"{ident} уже в списке.")

    async def deny(self, interaction: discord.Interaction, target: str) -> None:
        if await self._require_admin(interaction) is None:
            return
        try:
            ident = parse_target(target, "dc")
        except ValueError:
            await self._reply(interaction, USAGE)
            return
        removed = await self.access.deny(ident)
        await self._reply(interaction, f"🚫 {ident} удалён." if removed else f"{ident} не было в списке.")

    async def users(self, interaction: discord.Interaction) -> None:
        if await self._require_admin(interaction) is None:
            return
        await self._reply(interaction, format_users(self.access.list_allowed()))


class DcBot(discord.Client):
    def __init__(self, cfg: Config, access: Access):
        super().__init__(intents=discord.Intents.default())
        self.cfg = cfg
        self.tree = app_commands.CommandTree(self)
        h = DcHandlers(cfg, access)

        @self.tree.command(name="cams", description="Открыть камеры")
        async def cams(interaction: discord.Interaction) -> None:
            await h.cams(interaction)

        @self.tree.command(name="allow", description="Выдать доступ к камерам (админ)")
        @app_commands.describe(target="@пользователь, ID, tg:<id> или dc:<id>")
        async def allow(interaction: discord.Interaction, target: str) -> None:
            await h.allow(interaction, target)

        @self.tree.command(name="deny", description="Отозвать доступ к камерам (админ)")
        @app_commands.describe(target="@пользователь, ID, tg:<id> или dc:<id>")
        async def deny(interaction: discord.Interaction, target: str) -> None:
            await h.deny(interaction, target)

        @self.tree.command(name="users", description="Белый список (админ)")
        async def users(interaction: discord.Interaction) -> None:
            await h.users(interaction)

    async def setup_hook(self) -> None:
        if self.cfg.discord.guild_ids:
            for gid in self.cfg.discord.guild_ids:
                guild = discord.Object(id=gid)
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()


async def run_discord(cfg: Config, access: Access) -> None:
    client = DcBot(cfg, access)
    async with client:
        await client.start(cfg.discord.bot_token)
