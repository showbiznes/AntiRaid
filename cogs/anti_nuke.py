"""
Anti-Nuke модуль — защита от массового удаления каналов, ролей и банов.

Ключевая функция: если пользователь удалил 2+ каналов — мгновенный бан.
Также отслеживает массовое удаление ролей и массовые баны.
"""

from __future__ import annotations

import discord
from discord.ext import commands

from utils.config import Config
from utils.helpers import (
    ActionTracker,
    create_alert_embed,
    get_audit_log_entry,
    safe_ban,
    strip_dangerous_permissions,
)


class AntiNuke(commands.Cog):
    """Модуль защиты от нюка (массовое уничтожение сервера)."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.config = Config()
        settings = self.config.anti_nuke

        # Трекер удаления каналов: лимит 2, окно 60 сек
        self.channel_delete_tracker = ActionTracker(
            window_seconds=settings.get("channel_delete_window_seconds", 60),
            limit=settings.get("channel_delete_limit", 2),
        )

        # Трекер удаления ролей
        self.role_delete_tracker = ActionTracker(
            window_seconds=settings.get("role_delete_window_seconds", 60),
            limit=settings.get("role_delete_limit", 3),
        )

        # Трекер массовых банов
        self.ban_tracker = ActionTracker(
            window_seconds=settings.get("ban_window_seconds", 60),
            limit=settings.get("ban_limit", 3),
        )

        # Трекер массовых киков
        self.kick_tracker = ActionTracker(
            window_seconds=settings.get("kick_window_seconds", 60),
            limit=settings.get("kick_limit", 5),
        )

        # Множество уже обработанных пользователей (чтобы не банить дважды)
        self._punished: set[int] = set()

    async def _get_log_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        """Получить канал для логирования."""
        name = self.config.log_channel_name
        return discord.utils.get(guild.text_channels, name=name)

    async def _punish_user(
        self,
        guild: discord.Guild,
        user: discord.User | discord.Member,
        reason: str,
        alert_type: str,
        details: str,
    ) -> None:
        """Наказать пользователя: снять права, забанить, залогировать."""
        if user.id in self._punished:
            return
        self._punished.add(user.id)

        # Снять опасные роли, если пользователь ещё на сервере
        member = guild.get_member(user.id)
        removed_roles: list[discord.Role] = []
        if member:
            removed_roles = await strip_dangerous_permissions(member, reason=reason)

        # Забанить
        banned = await safe_ban(guild, user, reason=reason)

        # Логирование
        log_channel = await self._get_log_channel(guild)
        if log_channel:
            status = "✅ Забанен" if banned else "❌ Не удалось забанить"
            roles_text = (
                ", ".join(r.name for r in removed_roles) if removed_roles else "—"
            )
            full_details = (
                f"{details}\n\n"
                f"**Статус:** {status}\n"
                f"**Снятые роли:** {roles_text}"
            )
            embed = create_alert_embed(
                alert_type=alert_type,
                user=user,
                action=reason,
                details=full_details,
                color=discord.Color.dark_red(),
            )
            try:
                await log_channel.send(embed=embed)
            except discord.HTTPException:
                pass

    # ─────────────────────────────────────────────
    # Событие: удаление канала
    # ─────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        """Отслеживание удаления каналов."""
        if not self.config.anti_nuke.get("enabled", True):
            return

        guild = channel.guild

        # Найти кто удалил канал через audit log
        entry = await get_audit_log_entry(guild, discord.AuditLogAction.channel_delete)
        if entry is None or entry.user is None:
            return

        user = entry.user

        # Пропустить бота и белый список
        if user.id == self.bot.user.id:  # type: ignore[union-attr]
            return
        if self.config.is_whitelisted(user.id):
            return

        # Записать действие
        count = self.channel_delete_tracker.record(user.id)

        # Логировать каждое удаление
        log_channel = await self._get_log_channel(guild)
        if log_channel and count < self.channel_delete_tracker.limit:
            embed = create_alert_embed(
                alert_type="Удаление канала",
                user=user,
                action=f"Удалил канал: #{channel.name}",
                details=(
                    f"**Тип канала:** {channel.type}\n"
                    f"**Удалено за окно:** {count} / {self.channel_delete_tracker.limit}\n"
                    f"⚠️ Ещё {self.channel_delete_tracker.limit - count} до автоматического бана"
                ),
                color=discord.Color.orange(),
            )
            try:
                await log_channel.send(embed=embed)
            except discord.HTTPException:
                pass

        # Если превышен лимит — МГНОВЕННЫЙ БАН
        if self.channel_delete_tracker.is_over_limit(user.id):
            await self._punish_user(
                guild=guild,
                user=user,
                reason=f"Anti-Nuke: массовое удаление каналов ({count} каналов)",
                alert_type="🔥 МАССОВОЕ УДАЛЕНИЕ КАНАЛОВ",
                details=(
                    f"Пользователь удалил **{count}** каналов за "
                    f"**{self.channel_delete_tracker.window}** сек.\n"
                    f"Последний удалённый канал: `#{channel.name}`"
                ),
            )
            self.channel_delete_tracker.reset(user.id)

    # ─────────────────────────────────────────────
    # Событие: удаление роли
    # ─────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        """Отслеживание массового удаления ролей."""
        if not self.config.anti_nuke.get("enabled", True):
            return

        guild = role.guild

        entry = await get_audit_log_entry(guild, discord.AuditLogAction.role_delete)
        if entry is None or entry.user is None:
            return

        user = entry.user
        if user.id == self.bot.user.id:  # type: ignore[union-attr]
            return
        if self.config.is_whitelisted(user.id):
            return

        count = self.role_delete_tracker.record(user.id)

        if self.role_delete_tracker.is_over_limit(user.id):
            await self._punish_user(
                guild=guild,
                user=user,
                reason=f"Anti-Nuke: массовое удаление ролей ({count} ролей)",
                alert_type="🔥 МАССОВОЕ УДАЛЕНИЕ РОЛЕЙ",
                details=(
                    f"Пользователь удалил **{count}** ролей за "
                    f"**{self.role_delete_tracker.window}** сек.\n"
                    f"Последняя удалённая роль: `{role.name}`"
                ),
            )
            self.role_delete_tracker.reset(user.id)

    # ─────────────────────────────────────────────
    # Событие: бан участника (детекция массовых банов)
    # ─────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User) -> None:
        """Отслеживание массовых банов."""
        if not self.config.anti_nuke.get("enabled", True):
            return

        entry = await get_audit_log_entry(guild, discord.AuditLogAction.ban)
        if entry is None or entry.user is None:
            return

        banner = entry.user
        if banner.id == self.bot.user.id:  # type: ignore[union-attr]
            return
        if self.config.is_whitelisted(banner.id):
            return

        count = self.ban_tracker.record(banner.id)

        if self.ban_tracker.is_over_limit(banner.id):
            await self._punish_user(
                guild=guild,
                user=banner,
                reason=f"Anti-Nuke: массовый бан участников ({count} банов)",
                alert_type="🔥 МАССОВЫЙ БАН",
                details=(
                    f"Пользователь забанил **{count}** людей за "
                    f"**{self.ban_tracker.window}** сек."
                ),
            )
            self.ban_tracker.reset(banner.id)

    # ─────────────────────────────────────────────
    # Событие: кик участника (детекция массовых киков)
    # ─────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        """Отслеживание массовых киков через audit log."""
        if not self.config.anti_nuke.get("enabled", True):
            return

        guild = member.guild

        entry = await get_audit_log_entry(guild, discord.AuditLogAction.kick)
        if entry is None or entry.user is None:
            return
        if entry.target is None or entry.target.id != member.id:
            return

        kicker = entry.user
        if kicker.id == self.bot.user.id:  # type: ignore[union-attr]
            return
        if self.config.is_whitelisted(kicker.id):
            return

        count = self.kick_tracker.record(kicker.id)

        if self.kick_tracker.is_over_limit(kicker.id):
            await self._punish_user(
                guild=guild,
                user=kicker,
                reason=f"Anti-Nuke: массовый кик участников ({count} киков)",
                alert_type="🔥 МАССОВЫЙ КИК",
                details=(
                    f"Пользователь кикнул **{count}** людей за "
                    f"**{self.kick_tracker.window}** сек."
                ),
            )
            self.kick_tracker.reset(kicker.id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AntiNuke(bot))
