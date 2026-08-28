"""
Модуль конфигурации бота.
Загрузка, валидация и доступ к настройкам из config.json.
"""

import json
import os
from pathlib import Path
from typing import Any


class Config:
    """Менеджер конфигурации бота."""

    _instance = None
    _data: dict[str, Any] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def load(self, path: str | None = None) -> None:
        """Загрузить конфигурацию из JSON-файла."""
        if path is None:
            path = os.path.join(Path(__file__).parent.parent, "config.json")

        with open(path, "r", encoding="utf-8") as f:
            self._data = json.load(f)

        self._validate()

    def _validate(self) -> None:
        """Проверить обязательные поля конфигурации."""
        required = ["token", "owner_ids"]
        for key in required:
            if key not in self._data:
                raise ValueError(f"Отсутствует обязательное поле конфигурации: {key}")

        if not self._data["token"] or self._data["token"] == "YOUR_BOT_TOKEN_HERE":
            print(
                "[ПРЕДУПРЕЖДЕНИЕ] Токен бота не установлен! "
                "Измените 'token' в config.json."
            )

    @property
    def token(self) -> str:
        return self._data.get("token", "")

    @property
    def owner_ids(self) -> list[int]:
        return self._data.get("owner_ids", [])

    @property
    def whitelist_ids(self) -> list[int]:
        return self._data.get("whitelist_ids", [])

    @property
    def log_channel_name(self) -> str:
        return self._data.get("log_channel_name", "anti-raid-logs")

    @property
    def anti_nuke(self) -> dict[str, Any]:
        return self._data.get("anti_nuke", {})

    @property
    def anti_raid(self) -> dict[str, Any]:
        return self._data.get("anti_raid", {})

    @property
    def anti_spam(self) -> dict[str, Any]:
        return self._data.get("anti_spam", {})

    def is_whitelisted(self, user_id: int) -> bool:
        """Проверить, находится ли пользователь в белом списке."""
        return user_id in self.owner_ids or user_id in self.whitelist_ids

    def save(self, path: str | None = None) -> None:
        """Сохранить текущую конфигурацию в файл."""
        if path is None:
            path = os.path.join(Path(__file__).parent.parent, "config.json")

        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=4, ensure_ascii=False)

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)
