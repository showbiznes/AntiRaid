"""
Anti-Raid модуль — защита от массового входа подозрительных пользователей.

Анализирует каждого нового участника на подозрительность:
  - Возраст аккаунта (< 7 дней = подозрительный)
  - Массовый вход (5+ за 10 секунд = рейд-режим)
  - Автоматический карантин или кик при рейде
"""

from __future__ import annotations


import time
from collections import deque
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands, tasks

from utils.config import Config
from utils.helpers import (
    ActionTracker,
    account_age_days,
    create_alert_embed,
    create_embed,
    format_timedelta,
    safe_kick,
)


class AntiRaid(commands.Cog):
    """Модуль защиты от рейдов."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.config = Config()
        settings = self.config.anti_raid

        # Параметры рейд-детекции
        self.join_limit = settings.get("join_limit", 5)
        self.join_window = settings.get("join_window_seconds", 10)
        self.suspicious_age_days = settings.get("suspicious_account_age_days", 7)
        self.raid_mode_duration = settings.get("raid_mode_duration_seconds", 300)
        self.quarantine_role_name = settings.get("quarantine_role_name", "Карантин")

        # Очередь последних входов (timestamp)
        self._join_timestamps: deque[float] = deque(maxlen=100)

        # Рейд-режим
        self._raid_mode: dict[int, float] = {}  # guild_id -> raid_start_time

        # Трекер входов для статистики
        self._join_tracker = ActionTracker(
            window_seconds=self.join_window,
            limit=self.join_limit,
        )

        # Запуск фоновой задачи для авто-отключения рейд-режима
        self._raid_mode_checker.start()

    def cog_unload(self) -> None:
        self._raid_mode_checker.cancel()

    @tasks.loop(seconds=30)
    async def _raid_mode_checker(self) -> None:
        """Автоматическое отключение рейд-режима по таймауту."""
        now = time.monotonic()
        expired_guilds = [
            gid
            for gid, start_time in self._raid_mode.items()
            if now - start_time > self.raid_mode_duration
        ]
        for gid in expired_guilds:
            guild = self.bot.get_guild(gid)
            if guild:
                await self._deactivate_raid_mode(guild)

    @_raid_mode_checker.before_loop
    async def _before_raid_checker(self) -> None:
        await self.bot.wait_until_ready()

    def is_raid_mode(self, guild_id: int) -> bool:
        """Проверить, активен ли рейд-режим."""
        return guild_id in self._raid_mode

    async def _activate_raid_mode(self, guild: discord.Guild) -> None:
        """Активировать рейд-режим на сервере."""
        if guild.id in self._raid_mode:
            return

        self._raid_mode[guild.id] = time.monotonic()

        log_channel = discord.utils.get(
            guild.text_channels, name=self.config.log_channel_name
        )
        if log_channel:
            embed = create_embed(
                title="🚨 РЕЙД-РЕЖИМ АКТИВИРОВАН",
                description=(
                    f"Обнаружен массовый вход: **{self.join_limit}+** пользователей "
                    f"за **{self.join_window}** секунд.\n\n"
                    f"**Действия:**\n"
                    f"• Все новые участники будут автоматически кикнуты\n"
                    f"• Режим отключится через **{format_timedelta(self.raid_mode_duration)}**\n"
                    f"• Или используйте `!raidmode off` для ручного отключения"
                ),
                color=discord.Color.dark_red(),
            )
            try:
                await log_channel.send(embed=embed)
            except discord.HTTPException:
                pass

    async def _deactivate_raid_mode(self, guild: discord.Guild) -> None:
        """Деактивировать рейд-режим."""
        if guild.id not in self._raid_mode:
            return

        del self._raid_mode[guild.id]

        log_channel = discord.utils.get(
            guild.text_channels, name=self.config.log_channel_name
        )
        if log_channel:
            embed = create_embed(
                title="✅ Рейд-режим деактивирован",
                description="Сервер вернулся в нормальный режим работы.",
                color=discord.Color.green(),
            )
            try:
                await log_channel.send(embed=embed)
            except discord.HTTPException:
                pass

    async def _get_or_create_quarantine_role(
        self, guild: discord.Guild
    ) -> discord.Role | None:
        """Получить или создать роль карантина."""
        role = discord.utils.get(guild.roles, name=self.quarantine_role_name)
        if role:
            return role

        try:
            # Создать роль без прав
            role = await guild.create_role(
                name=self.quarantine_role_name,
                permissions=discord.Permissions.none(),
                color=discord.Color.dark_grey(),
                reason="Anti-Raid: создание роли карантина",
            )

            # Запретить отправку сообщений и подключение к голосовым каналам
            for channel in guild.channels:
                try:
                    await channel.set_permissions(
                        role,
                        send_messages=False,
                        speak=False,
                        connect=False,
                        add_reactions=False,
                        reason="Anti-Raid: настройка карантина",
                    )
                except discord.HTTPException:
                    pass

            return role
        except discord.HTTPException:
            return None

    async def _quarantine_member(
        self, member: discord.Member, reason: str
    ) -> bool:
        """Поставить участника на карантин."""
        role = await self._get_or_create_quarantine_role(member.guild)
        if role is None:
            return False

        try:
            await member.add_roles(role, reason=reason)
            # Также замутить если возможно
            try:
                await member.timeout(
                    discord.utils.utcnow() + timedelta(hours=1),
                    reason=reason,
                )
            except discord.HTTPException:
                pass
            return True
        except discord.HTTPException:
            return False

    def _analyze_suspicion(self, member: discord.Member) -> tuple[int, list[str]]:
        """
        Анализировать подозрительность пользователя.
        Возвращает (score, reasons).
        """
        score = 0
        reasons: list[str] = []

        # Возраст аккаунта
        age = account_age_days(member)
        if age < 1:
            score += 50
            reasons.append(f"🆕 Аккаунт создан менее 1 дня назад ({age} д.)")
        elif age < 3:
            score += 35
            reasons.append(f"🆕 Аккаунт создан менее 3 дней назад ({age} д.)")
        elif age < self.suspicious_age_days:
            score += 20
            reasons.append(
                f"🆕 Аккаунт создан менее {self.suspicious_age_days} дней назад ({age} д.)"
            )

        # Имя пользователя — случайные символы (возможный авто-аккаунт)
        name = member.name
        if len(name) >= 15 and any(c.isdigit() for c in name):
            digit_ratio = sum(1 for c in name if c.isdigit()) / len(name)
            if digit_ratio > 0.4:
                score += 15
                reasons.append("🤖 Подозрительное имя (много цифр)")

        return score, reasons

    # ─────────────────────────────────────────────
    # Событие: новый участник
    # ─────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """Анализ каждого нового участника."""
        if not self.config.anti_raid.get("enabled", True):
            return

        if member.bot:
            return

        guild = member.guild
        now = time.monotonic()

        # Записать вход
        self._join_timestamps.append(now)

        # Проверить массовый вход
        cutoff = now - self.join_window
        recent_joins = sum(1 for t in self._join_timestamps if t > cutoff)

        if recent_joins >= self.join_limit:
            await self._activate_raid_mode(guild)

        # Если рейд-режим — кикать сразу
        if self.is_raid_mode(guild.id):
            kicked = await safe_kick(
                member, reason="Anti-Raid: рейд-режим — автоматический кик"
            )
            log_channel = discord.utils.get(
                guild.text_channels, name=self.config.log_channel_name
            )
            if log_channel:
                status = "кикнут" if kicked else "не удалось кикнуть"
                embed = create_alert_embed(
                    alert_type="Рейд-режим: кик",
                    user=member,
                    action=f"Автоматический кик ({status})",
                    details="Пользователь зашёл во время рейд-режима.",
                    color=discord.Color.red(),
                )
                try:
                    await log_channel.send(embed=embed)
                except discord.HTTPException:
                    pass
            return

        # Анализ подозрительности
        score, reasons = self._analyze_suspicion(member)

        log_channel = discord.utils.get(
            guild.text_channels, name=self.config.log_channel_name
        )

        if score >= 40:
            # Высокая подозрительность — карантин
            quarantined = await self._quarantine_member(
                member,
                reason=f"Anti-Raid: подозрительный аккаунт (score: {score})",
            )
            if log_channel:
                status = "✅ На карантине" if quarantined else "❌ Не удалось поставить на карантин"
                embed = create_alert_embed(
                    alert_type="⚠️ Подозрительный пользователь — КАРАНТИН",
                    user=member,
                    action=status,
                    details=(
                        f"**Уровень подозрительности:** {score}/100\n"
                        f"**Причины:**\n" + "\n".join(f"  {r}" for r in reasons)
                    ),
                    color=discord.Color.red(),
                )
                try:
                    await log_channel.send(embed=embed)
                except discord.HTTPException:
                    pass

        elif score >= 15:
            # Средняя подозрительность — только предупреждение
            if log_channel:
                embed = create_alert_embed(
                    alert_type="ℹ️ Новый подозрительный пользователь",
                    user=member,
                    action="Наблюдение",
                    details=(
                        f"**Уровень подозрительности:** {score}/100\n"
                        f"**Причины:**\n" + "\n".join(f"  {r}" for r in reasons)
                    ),
                    color=discord.Color.yellow(),
                )
                try:
                    await log_channel.send(embed=embed)
                except discord.HTTPException:
                    pass




    # ─────────────────────────────────────────────
    # Команды
    # ─────────────────────────────────────────────
    @commands.command(name="raidmode")
    @commands.has_permissions(administrator=True)
    async def raid_mode_command(self, ctx: commands.Context, mode: str = "status") -> None:
        """Управление рейд-режимом. Использование: !raidmode [on/off/status]"""
        if ctx.guild is None:
            return

        mode = mode.lower()

        if mode == "on":
            await self._activate_raid_mode(ctx.guild)
            await ctx.send("🚨 **Рейд-режим активирован вручную.**")

        elif mode == "off":
            await self._deactivate_raid_mode(ctx.guild)
            await ctx.send("✅ **Рейд-режим деактивирован.**")

        elif mode == "status":
            is_active = self.is_raid_mode(ctx.guild.id)
            status = "🚨 **АКТИВЕН**" if is_active else "✅ **Неактивен**"
            await ctx.send(f"Рейд-режим: {status}")

        else:
            await ctx.send("Использование: `!raidmode [on/off/status]`")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AntiRaid(bot))
