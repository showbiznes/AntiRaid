"""
Вспомогательные функции для анти-рейд бота.
Embeds, форматирование, работа с правами и audit log.
"""

from __future__ import annotations

import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from collections.abc import MutableMapping


class ActionTracker:
    """
    Потокобезопасный трекер действий пользователей.

    Хранит временные метки действий каждого пользователя и автоматически
    очищает устаревшие записи для экономии памяти.
    """

    def __init__(self, window_seconds: float = 60.0, limit: int = 3):
        self.window = window_seconds
        self.limit = limit
        self._actions: MutableMapping[int, list[float]] = defaultdict(list)
        self._last_cleanup = time.monotonic()
        self._cleanup_interval = 120.0  # Очистка каждые 2 минуты

    def record(self, user_id: int) -> int:
        """
        Записать действие пользователя и вернуть кол-во действий в окне.
        """
        now = time.monotonic()
        self._maybe_cleanup(now)

        actions = self._actions[user_id]
        actions.append(now)

        # Убрать устаревшие записи для этого пользователя
        cutoff = now - self.window
        self._actions[user_id] = [t for t in actions if t > cutoff]

        return len(self._actions[user_id])

    def get_count(self, user_id: int) -> int:
        """Получить текущее кол-во действий пользователя в окне."""
        now = time.monotonic()
        cutoff = now - self.window
        actions = self._actions.get(user_id, [])
        return sum(1 for t in actions if t > cutoff)

    def reset(self, user_id: int) -> None:
        """Сбросить счётчик действий пользователя."""
        self._actions.pop(user_id, None)

    def _maybe_cleanup(self, now: float) -> None:
        """Периодически очищать устаревшие записи всех пользователей."""
        if now - self._last_cleanup < self._cleanup_interval:
            return
        self._last_cleanup = now
        cutoff = now - self.window
        to_delete = []
        for uid, actions in self._actions.items():
            filtered = [t for t in actions if t > cutoff]
            if filtered:
                self._actions[uid] = filtered
            else:
                to_delete.append(uid)
        for uid in to_delete:
            del self._actions[uid]

    def is_over_limit(self, user_id: int) -> bool:
        """Проверить, превысил ли пользователь лимит действий."""
        return self.get_count(user_id) >= self.limit


def create_embed(
    title: str,
    description: str,
    color: discord.Color = discord.Color.red(),
    *,
    fields: list[tuple[str, str, bool]] | None = None,
    footer: str | None = None,
    thumbnail_url: str | None = None,
) -> discord.Embed:
    """Создать стандартный embed для логирования."""
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.now(timezone.utc),
    )

    if fields:
        for name, value, inline in fields:
            embed.add_field(name=name, value=value, inline=inline)

    if footer:
        embed.set_footer(text=footer)

    if thumbnail_url:
        embed.set_thumbnail(url=thumbnail_url)

    return embed


def create_alert_embed(
    alert_type: str,
    user: discord.User | discord.Member,
    action: str,
    details: str,
    color: discord.Color = discord.Color.red(),
) -> discord.Embed:
    """Создать embed оповещения о нарушении."""
    embed = create_embed(
        title=f"🚨 {alert_type}",
        description=f"**Действие:** {action}",
        color=color,
        fields=[
            ("Нарушитель", f"{user.mention} (`{user}` | ID: `{user.id}`)", False),
            ("Детали", details, False),
        ],
        footer=f"Anti-Raid Bot • {alert_type}",
    )

    if user.avatar:
        embed.set_thumbnail(url=user.avatar.url)

    return embed


async def get_audit_log_entry(
    guild: discord.Guild,
    action: discord.AuditLogAction,
    *,
    limit: int = 5,
) -> discord.AuditLogEntry | None:
    """
    Получить последнюю запись из audit log для указанного действия.
    Возвращает запись, созданную не более 10 секунд назад.
    """
    now = datetime.now(timezone.utc)
    try:
        async for entry in guild.audit_logs(limit=limit, action=action):
            # Проверяем, что запись свежая (не старше 10 секунд)
            if entry.created_at and (now - entry.created_at).total_seconds() < 10:
                return entry
    except discord.Forbidden:
        pass
    return None


async def strip_dangerous_permissions(
    member: discord.Member,
    reason: str = "Anti-Raid: снятие опасных прав",
) -> list[discord.Role]:
    """
    Снять все роли с опасными правами у пользователя.
    Возвращает список снятых ролей.
    """
    dangerous_perms = [
        "administrator",
        "manage_guild",
        "manage_channels",
        "manage_roles",
        "ban_members",
        "kick_members",
        "manage_webhooks",
        "manage_messages",
        "mention_everyone",
    ]

    removed_roles: list[discord.Role] = []

    for role in member.roles:
        if role.is_default() or role.managed:
            continue

        perms = role.permissions
        has_dangerous = any(getattr(perms, perm, False) for perm in dangerous_perms)
        if has_dangerous:
            try:
                await member.remove_roles(role, reason=reason)
                removed_roles.append(role)
            except discord.HTTPException:
                pass

    return removed_roles


async def safe_ban(
    guild: discord.Guild,
    user: discord.User | discord.Member,
    reason: str,
    *,
    delete_message_seconds: int = 0,
) -> bool:
    """Безопасно забанить пользователя с обработкой ошибок."""
    try:
        await guild.ban(
            user,
            reason=reason,
            delete_message_seconds=delete_message_seconds,
        )
        return True
    except discord.Forbidden:
        print(f"[ОШИБКА] Нет прав для бана {user} (ID: {user.id})")
        return False
    except discord.HTTPException as e:
        print(f"[ОШИБКА] HTTP ошибка при бане {user}: {e}")
        return False


async def safe_kick(
    member: discord.Member,
    reason: str,
) -> bool:
    """Безопасно кикнуть пользователя с обработкой ошибок."""
    try:
        await member.kick(reason=reason)
        return True
    except (discord.Forbidden, discord.HTTPException):
        return False


def format_timedelta(seconds: float) -> str:
    """Форматировать секунды в читаемую строку."""
    if seconds < 60:
        return f"{int(seconds)} сек."
    elif seconds < 3600:
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes} мин. {secs} сек."
    else:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        return f"{hours} ч. {minutes} мин."


def account_age_days(user: discord.User | discord.Member) -> int:
    """Получить возраст аккаунта в днях."""
    now = datetime.now(timezone.utc)
    return (now - user.created_at).days
