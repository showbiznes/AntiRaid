"""
Logging модуль — логирование всех важных событий на сервере.

Создаёт канал логов автоматически и записывает:
  - Входы/выходы участников
  - Изменения ролей
  - Создание/удаление каналов
  - Действия бота
"""

from __future__ import annotations

from datetime import datetime, timezone

import discord
from discord.ext import commands

from utils.config import Config
from utils.helpers import account_age_days, create_embed


class Logging(commands.Cog):
    """Модуль логирования событий."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.config = Config()

    async def _ensure_log_channel(
        self, guild: discord.Guild
    ) -> discord.TextChannel | None:
        """Получить или создать канал логов."""
        name = self.config.log_channel_name
        channel = discord.utils.get(guild.text_channels, name=name)
        if channel:
            return channel

        # Создать канал с ограниченным доступом
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            guild.me: discord.PermissionOverwrite(
                read_messages=True,
                send_messages=True,
                embed_links=True,
            ),
        }

        try:
            channel = await guild.create_text_channel(
                name=name,
                overwrites=overwrites,
                topic="🛡️ Канал логирования Anti-Raid бота. Не удаляйте этот канал.",
                reason="Anti-Raid: создание канала логов",
            )
            return channel
        except discord.HTTPException:
            return None

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Создать каналы логов на всех серверах при запуске."""
        for guild in self.bot.guilds:
            await self._ensure_log_channel(guild)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        """Создать канал логов при подключении к новому серверу."""
        channel = await self._ensure_log_channel(guild)
        if channel:
            embed = create_embed(
                title="🛡️ Anti-Raid Bot подключен!",
                description=(
                    "Бот защиты сервера активирован.\n\n"
                    "**Активные модули:**\n"
                    "• 🔥 Anti-Nuke — защита от удаления каналов/ролей\n"
                    "• 🚨 Anti-Raid — защита от массового входа\n"
                    "• 🔇 Anti-Spam — защита от спама\n"
                    "• 📋 Логирование — запись всех событий\n\n"
                    "**Команды:**\n"
                    "`!raidmode [on/off/status]` — управление рейд-режимом\n"
                    "`!whitelist [add/remove] @user` — управление белым списком\n"
                    "`!antiraid status` — статус всех модулей"
                ),
                color=discord.Color.green(),
            )
            try:
                await channel.send(embed=embed)
            except discord.HTTPException:
                pass

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """Логирование входа участника."""
        channel = await self._ensure_log_channel(member.guild)
        if not channel:
            return

        age = account_age_days(member)
        created = member.created_at.strftime("%d.%m.%Y %H:%M UTC")

        embed = create_embed(
            title="📥 Новый участник",
            description=f"{member.mention} присоединился к серверу.",
            color=discord.Color.blue(),
            fields=[
                ("Пользователь", f"`{member}` (ID: `{member.id}`)", True),
                ("Возраст аккаунта", f"{age} дней", True),
                ("Дата создания", created, True),
            ],
        )

        if member.avatar:
            embed.set_thumbnail(url=member.avatar.url)

        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        """Логирование выхода участника."""
        channel = await self._ensure_log_channel(member.guild)
        if not channel:
            return

        roles = [r.name for r in member.roles if not r.is_default()]

        embed = create_embed(
            title="📤 Участник покинул сервер",
            description=f"`{member}` (ID: `{member.id}`) покинул сервер.",
            color=discord.Color.greyple(),
            fields=[
                ("Роли", ", ".join(roles) if roles else "Нет ролей", False),
            ],
        )

        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        """Логирование создания канала."""
        log_channel = await self._ensure_log_channel(channel.guild)
        if not log_channel:
            return

        embed = create_embed(
            title="➕ Канал создан",
            description=f"Канал `{channel.name}` ({channel.type}) создан.",
            color=discord.Color.green(),
        )

        try:
            await log_channel.send(embed=embed)
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role) -> None:
        """Логирование создания роли."""
        log_channel = await self._ensure_log_channel(role.guild)
        if not log_channel:
            return

        perms = []
        if role.permissions.administrator:
            perms.append("⚠️ Администратор")
        if role.permissions.manage_guild:
            perms.append("⚠️ Управление сервером")
        if role.permissions.manage_channels:
            perms.append("⚠️ Управление каналами")
        if role.permissions.ban_members:
            perms.append("⚠️ Бан участников")

        embed = create_embed(
            title="🏷️ Роль создана",
            description=f"Роль `{role.name}` создана.",
            color=discord.Color.teal(),
            fields=[
                (
                    "Опасные права",
                    "\n".join(perms) if perms else "✅ Нет опасных прав",
                    False,
                ),
            ],
        )

        try:
            await log_channel.send(embed=embed)
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_member_update(
        self, before: discord.Member, after: discord.Member
    ) -> None:
        """Логирование изменения ролей участника."""
        if before.roles == after.roles:
            return

        log_channel = await self._ensure_log_channel(after.guild)
        if not log_channel:
            return

        added = set(after.roles) - set(before.roles)
        removed = set(before.roles) - set(after.roles)

        if not added and not removed:
            return

        description_parts = []
        if added:
            roles_str = ", ".join(f"`{r.name}`" for r in added)
            description_parts.append(f"**Добавлены роли:** {roles_str}")
        if removed:
            roles_str = ", ".join(f"`{r.name}`" for r in removed)
            description_parts.append(f"**Убраны роли:** {roles_str}")

        # Проверить, добавлены ли опасные роли
        dangerous = any(
            r.permissions.administrator
            or r.permissions.manage_guild
            or r.permissions.manage_channels
            for r in added
        )

        embed = create_embed(
            title="⚠️ Изменение ролей" if dangerous else "🏷️ Изменение ролей",
            description=f"Участник: {after.mention}\n" + "\n".join(description_parts),
            color=discord.Color.red() if dangerous else discord.Color.blue(),
        )

        try:
            await log_channel.send(embed=embed)
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Logging(bot))
