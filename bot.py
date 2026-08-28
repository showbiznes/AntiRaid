"""
Anti-Raid Discord Bot — главный файл.

Защита сервера от рейдов, нюков, спама и подозрительных пользователей.
Запуск: python bot.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Загрузка .env файла (токен и секреты)
def _load_env() -> None:
    """Загрузить переменные из .env файла."""
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

_load_env()

import discord
from discord.ext import commands

from utils.config import Config

# ─────────────────────────────────────────────
# Настройка Intents (все необходимые для защиты)
# ─────────────────────────────────────────────
intents = discord.Intents.default()
intents.members = True          # Отслеживание входов/выходов
intents.message_content = True  # Чтение содержимого сообщений (anti-spam)
intents.moderation = True       # Отслеживание банов/киков

# ─────────────────────────────────────────────
# Инициализация бота
# ─────────────────────────────────────────────
bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=commands.DefaultHelpCommand(no_category="Общие"),
)

config = Config()

# Список cog-модулей для загрузки
COGS = [
    "cogs.anti_nuke",
    "cogs.anti_raid",
    "cogs.anti_spam",
    "cogs.logging_cog",
]


@bot.event
async def on_ready() -> None:
    """Вызывается когда бот полностью готов к работе."""
    assert bot.user is not None
    print("=" * 50)
    print(f"  🛡️  Anti-Raid Bot запущен!")
    print(f"  Имя:     {bot.user.name}")
    print(f"  ID:      {bot.user.id}")
    print(f"  Серверы: {len(bot.guilds)}")
    print(f"  Модули:  {len(bot.cogs)}")
    print("=" * 50)

    # Установить статус
    activity = discord.Activity(
        type=discord.ActivityType.watching,
        name="за безопасностью сервера 🛡️",
    )
    await bot.change_presence(status=discord.Status.online, activity=activity)


# ─────────────────────────────────────────────
# Команды управления
# ─────────────────────────────────────────────
@bot.command(name="antiraid")
@commands.has_permissions(administrator=True)
async def antiraid_status(ctx: commands.Context, action: str = "status") -> None:
    """Статус и управление анти-рейд ботом. Использование: !antiraid status"""
    if action.lower() == "status":
        cog_status = []
        for cog_name in ["AntiNuke", "AntiRaid", "AntiSpam", "Logging"]:
            cog = bot.get_cog(cog_name)
            status = "✅ Активен" if cog else "❌ Не загружен"
            cog_status.append(f"**{cog_name}:** {status}")

        embed = discord.Embed(
            title="🛡️ Статус Anti-Raid Bot",
            description="\n".join(cog_status),
            color=discord.Color.green(),
        )

        # Настройки anti-nuke
        nuke_cfg = config.anti_nuke
        embed.add_field(
            name="⚙️ Anti-Nuke",
            value=(
                f"Лимит удаления каналов: **{nuke_cfg.get('channel_delete_limit', 2)}**\n"
                f"Лимит удаления ролей: **{nuke_cfg.get('role_delete_limit', 3)}**\n"
                f"Лимит банов: **{nuke_cfg.get('ban_limit', 3)}**"
            ),
            inline=True,
        )

        # Настройки anti-raid
        raid_cfg = config.anti_raid
        embed.add_field(
            name="⚙️ Anti-Raid",
            value=(
                f"Лимит входов: **{raid_cfg.get('join_limit', 5)}** / "
                f"**{raid_cfg.get('join_window_seconds', 10)}** сек.\n"
                f"Подозрительный возраст: **<{raid_cfg.get('suspicious_account_age_days', 7)}** дней"
            ),
            inline=True,
        )

        # Настройки anti-spam
        spam_cfg = config.anti_spam
        embed.add_field(
            name="⚙️ Anti-Spam",
            value=(
                f"Лимит сообщений: **{spam_cfg.get('message_limit', 10)}** / "
                f"**{spam_cfg.get('message_window_seconds', 5)}** сек.\n"
                f"Лимит упоминаний: **{spam_cfg.get('mention_limit', 8)}**"
            ),
            inline=True,
        )

        await ctx.send(embed=embed)
    else:
        await ctx.send("Использование: `!antiraid status`")


@bot.command(name="whitelist")
@commands.has_permissions(administrator=True)
async def whitelist_cmd(
    ctx: commands.Context, action: str = "list", member: discord.Member | None = None
) -> None:
    """
    Управление белым списком.
    Использование: !whitelist [add/remove/list] @user
    """
    if action.lower() == "list":
        wl = config.whitelist_ids
        if not wl:
            await ctx.send("📋 Белый список пуст.")
            return

        users = []
        for uid in wl:
            user = bot.get_user(uid)
            name = f"`{user}`" if user else f"ID: `{uid}`"
            users.append(name)

        embed = discord.Embed(
            title="📋 Белый список",
            description="\n".join(f"• {u}" for u in users),
            color=discord.Color.blue(),
        )
        await ctx.send(embed=embed)

    elif action.lower() == "add":
        if member is None:
            await ctx.send("❌ Укажите пользователя: `!whitelist add @user`")
            return
        if member.id not in config.whitelist_ids:
            config._data["whitelist_ids"].append(member.id)
            config.save()
            await ctx.send(f"✅ {member.mention} добавлен в белый список.")
        else:
            await ctx.send(f"ℹ️ {member.mention} уже в белом списке.")

    elif action.lower() == "remove":
        if member is None:
            await ctx.send("❌ Укажите пользователя: `!whitelist remove @user`")
            return
        if member.id in config.whitelist_ids:
            config._data["whitelist_ids"].remove(member.id)
            config.save()
            await ctx.send(f"✅ {member.mention} удалён из белого списка.")
        else:
            await ctx.send(f"ℹ️ {member.mention} не найден в белом списке.")

    else:
        await ctx.send("Использование: `!whitelist [add/remove/list] @user`")


# ─────────────────────────────────────────────
# Обработка ошибок
# ─────────────────────────────────────────────
@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError) -> None:
    """Глобальная обработка ошибок команд."""
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ У вас нет прав для использования этой команды.")
    elif isinstance(error, commands.CommandNotFound):
        pass  # Игнорировать неизвестные команды
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Пропущен аргумент: `{error.param.name}`")
    else:
        print(f"[ОШИБКА] {type(error).__name__}: {error}")


# ─────────────────────────────────────────────
# Загрузка модулей и запуск
# ─────────────────────────────────────────────
async def load_cogs() -> None:
    """Загрузить все cog-модули."""
    for cog in COGS:
        try:
            await bot.load_extension(cog)
            print(f"  ✅ Загружен: {cog}")
        except Exception as e:
            print(f"  ❌ Ошибка загрузки {cog}: {e}")


async def main() -> None:
    """Главная функция запуска бота."""
    config.load()

    if not config.token or config.token == "YOUR_BOT_TOKEN_HERE":
        print("=" * 50)
        print("  ❌ ОШИБКА: Токен бота не установлен!")
        print("  Откройте config.json и замените")
        print('  "YOUR_BOT_TOKEN_HERE" на ваш токен бота.')
        print("=" * 50)
        sys.exit(1)

    async with bot:
        await load_cogs()
        await bot.start(config.token)


if __name__ == "__main__":
    asyncio.run(main())
