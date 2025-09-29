"""Управление конфигурацией бота (чтение/запись настроек)"""

import logging
import os
import pickle
from datetime import datetime
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)


class ConfigManager:
    """Менеджер конфигурации с сохранением в файл"""

    def __init__(self, config_file: str = "bot_config.pkl"):
        self.config_file = config_file
        self.config = self.load_config()

    def load_config(self) -> Dict:
        """Загрузка конфигурации из файла"""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, "rb") as f:
                    return pickle.load(f)
        except (
            pickle.UnpicklingError,
            EOFError,
            AttributeError,
            ImportError,
        ) as e:
            logger.error("Ошибка загрузки конфигурации: %s", e)
        except OSError as e:  # Ошибки файловой системы
            logger.error("Ошибка доступа к файлу конфигурации: %s", e)

        # Конфигурация по умолчанию
        return {
            "platform_login": "",
            "platform_password": "",
            "school_id": "",
            "campus_name": "",
            "admin_chat_id": "",
            "is_configured": False,
            "last_update": None,
        }

    def save_config(self):
        """Сохранение конфигурации в файл"""
        try:
            self.config["last_update"] = datetime.now()
            with open(self.config_file, "wb") as f:
                # noinspection PyTypeChecker
                pickle.dump(self.config, f)
            logger.info("Конфигурация сохранена")
        except (pickle.PicklingError, TypeError) as e:
            logger.error("Ошибка конфигурации: %s", e)
        except OSError as e:
            logger.error("Ошибка файловой системы при сохранении: %s", e)

    def update_setting(self, key: str, value: str):
        """Обновление настройки"""
        self.config[key] = value
        self.config["is_configured"] = all(
            [
                self.config["platform_login"],
                self.config["platform_password"],
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
