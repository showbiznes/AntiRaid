"""
Anti-Spam модуль — защита от спама сообщениями.

Отслеживает:
  - Массовые сообщения (10+ за 5 сек = мут)
  - Дубликаты сообщений
  - Массовые упоминания (@everyone, @here, множественные пинги)
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import timedelta

import discord
from discord.ext import commands

from utils.config import Config
from utils.helpers import ActionTracker, create_alert_embed


class AntiSpam(commands.Cog):
    """Модуль защиты от спама."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.config = Config()
        settings = self.config.anti_spam

        self.message_limit = settings.get("message_limit", 10)
        self.message_window = settings.get("message_window_seconds", 5)
        self.duplicate_limit = settings.get("duplicate_limit", 5)
        self.duplicate_window = settings.get("duplicate_window_seconds", 10)
        self.mute_duration = settings.get("mute_duration_seconds", 300)
        self.mention_limit = settings.get("mention_limit", 8)

        # Трекер скорости сообщений
        self._message_tracker = ActionTracker(
            window_seconds=self.message_window,
            limit=self.message_limit,
        )

        # Хранилище последних сообщений для детекции дубликатов
        # user_id -> deque of (timestamp, content_hash)
        self._recent_messages: dict[int, deque[tuple[float, str]]] = defaultdict(
            lambda: deque(maxlen=20)
        )

        # Уже замученные (чтобы не мутить повторно)
        self._muted_users: set[int] = set()

    async def _get_log_channel(
        self, guild: discord.Guild
    ) -> discord.TextChannel | None:
        name = self.config.log_channel_name
        return discord.utils.get(guild.text_channels, name=name)

    async def _mute_member(
        self, member: discord.Member, reason: str, details: str
    ) -> None:
        """Замутить участника через timeout."""
        if member.id in self._muted_users:
            return
        self._muted_users.add(member.id)

        try:
            await member.timeout(
                discord.utils.utcnow() + timedelta(seconds=self.mute_duration),
                reason=reason,
            )
        except discord.HTTPException:
            pass

        log_channel = await self._get_log_channel(member.guild)
        if log_channel:
            embed = create_alert_embed(
                alert_type="🔇 Авто-мут за спам",
                user=member,
                action=reason,
                details=(
                    f"{details}\n"
                    f"**Длительность мута:** {self.mute_duration // 60} мин."
                ),
                color=discord.Color.orange(),
            )
            try:
                await log_channel.send(embed=embed)
            except discord.HTTPException:
                pass

        # Снять флаг через время мута
        self.bot.loop.call_later(
            self.mute_duration, self._muted_users.discard, member.id
        )

    def _check_duplicates(self, user_id: int, content: str) -> int:
        """Проверить количество дубликатов сообщений."""
        now = time.monotonic()
        cutoff = now - self.duplicate_window
        messages = self._recent_messages[user_id]

        # Убрать старые
        while messages and messages[0][0] < cutoff:
            messages.popleft()

        # Записать новое
        messages.append((now, content.lower().strip()))

        # Подсчитать дубликаты
        target = content.lower().strip()
        return sum(1 for _, c in messages if c == target)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Проверка каждого сообщения на спам."""
        if not self.config.anti_spam.get("enabled", True):
            return

        # Пропустить ботов, DM и белый список
        if message.author.bot:
            return
        if message.guild is None:
            return
        if self.config.is_whitelisted(message.author.id):
            return

        member = message.guild.get_member(message.author.id)
        if member is None:
            return

        # Пропустить администраторов
        if member.guild_permissions.administrator:
            return

        # ── Проверка 1: Скорость сообщений ──
        count = self._message_tracker.record(message.author.id)
        if self._message_tracker.is_over_limit(message.author.id):
            await self._mute_member(
                member,
                reason=f"Anti-Spam: {count} сообщений за {self.message_window} сек.",
                details=f"Отправлено **{count}** сообщений за **{self.message_window}** сек.",
            )
            self._message_tracker.reset(message.author.id)
            return

        # ── Проверка 2: Дубликаты сообщений ──
        if message.content:
            dup_count = self._check_duplicates(message.author.id, message.content)
            if dup_count >= self.duplicate_limit:
                await self._mute_member(
                    member,
                    reason=f"Anti-Spam: {dup_count} одинаковых сообщений",
                    details=(
                        f"Отправлено **{dup_count}** одинаковых сообщений за "
                        f"**{self.duplicate_window}** сек.\n"
                        f"Содержимое: `{message.content[:100]}...`"
                    ),
                )
                return

        # ── Проверка 3: Массовые упоминания ──
        total_mentions = len(message.mentions) + len(message.role_mentions)
        if message.mention_everyone:
            total_mentions += 10  # @everyone/@here — серьёзное нарушение

        if total_mentions >= self.mention_limit:
            await self._mute_member(
                member,
                reason=f"Anti-Spam: {total_mentions} упоминаний в одном сообщении",
                details=(
                    f"**{total_mentions}** упоминаний в одном сообщении.\n"
                    f"Содержимое: `{message.content[:100]}...`"
                ),
            )
            # Удалить сообщение с массовыми пингами
            try:
                await message.delete()
            except discord.HTTPException:
                pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AntiSpam(bot))
