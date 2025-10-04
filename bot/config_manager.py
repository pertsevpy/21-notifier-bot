"""Управление конфигурацией бота (чтение/запись настроек)"""

import json
import logging
import os
from datetime import datetime
from typing import Dict, Tuple, List
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)


class ConfigManager:
    """Менеджер конфигурации с сохранением в файл"""

    def __init__(self, config_file: str = "bot_config.json"):
        self.config_file = config_file
        self.config = self.load_config()
        # Загружаем пароль из переменной окружения
        self.config["platform_password"] = os.getenv("PLATFORM_PASSWORD", "")

    def load_config(self) -> Dict:
        """Загружает конфигурацию из JSON файла"""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, "r", encoding="utf-8") as f:
                    config = json.load(f)
                    # Валидация структуры конфига
                    required_keys = [
                        "platform_login",
                        "school_id",
                        "campus_name",
                        "admin_chat_id",
                        "is_configured",
                        "last_update",
                        "timezone",
                    ]
                    if not all(key in config for key in required_keys):
                        logger.error(
                            "Неверная структура конфигурации, возвращаем дефолт"
                        )
                        return self.get_default_config()
                    return config
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Ошибка загрузки конфигурации: %s", e)
        return self.get_default_config()

    @staticmethod
    def get_default_config() -> Dict:
        """Возвращает дефолтную конфигурацию"""
        return {
            "platform_login": "",
            "school_id": "",
            "campus_name": "",
            "admin_chat_id": "",
            "is_configured": False,
            "last_update": None,
            "timezone": "UTC+3",
        }

    def save_config(self):
        """Сохраняет конфигурацию в JSON файл"""
        try:
            self.config["last_update"] = datetime.now().isoformat()
            # Не сохраняем platform_password в файл
            config_to_save = {
                k: v
                for k, v in self.config.items()
                if k != "platform_password"
            }
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(config_to_save, f, indent=2, ensure_ascii=False)
            logger.info("Конфигурация сохранена")
        except (json.decoder.JSONDecodeError, OSError) as e:
            logger.error("Ошибка сохранения конфигурации: %s", e)

    def update_setting(self, key: str, value: str):
        """Обновляет настройку и сохраняет конфигурацию"""
        if key == "timezone":
            try:
                ZoneInfo(value)  # Проверяем валидность часового пояса
            except ZoneInfoNotFoundError:
                logger.error("Неверный часовой пояс: %s", value)
                return
        if key == "platform_password":
            os.environ["PLATFORM_PASSWORD"] = value
        self.config[key] = value
        self.config["is_configured"] = all(
            [
                self.config["platform_login"],
                self.config["school_id"],
                self.config["admin_chat_id"],
            ]
        )
        self.save_config()

    def get_config_status(self) -> Tuple[bool, List[str]]:
        """Проверка полноты конфигурации"""
        missing = []
        if not self.config["platform_login"]:
            missing.append("логин")
        if not self.config["platform_password"]:
            missing.append("пароль")
        if not self.config["school_id"]:
            missing.append("кампус")
        if not self.config["admin_chat_id"]:
            missing.append("admin_chat_id")

        return len(missing) == 0, missing
