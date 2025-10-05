"""Клавиатуры для Telegram бота"""

from typing import List, Dict
from zoneinfo import available_timezones

from telegram import ReplyKeyboardMarkup


class Keyboards:
    """Класс для создания клавиатур бота"""

    @staticmethod
    def get_main_menu_keyboard() -> ReplyKeyboardMarkup:
        """Клавиатура главного меню"""
        keyboard = [
            ["📊 Статус"],
            ["▶️ Запуск", "⏹️ Остановка"],
            ["🔐 Тест авторизации", "🔄 Сброс настроек"],
            ["⚙️ Настройки", "🔔 Последнее уведомление"],
        ]
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    @staticmethod
    def get_settings_keyboard() -> ReplyKeyboardMarkup:
        """Клавиатура настроек"""
        keyboard = [
            ["👤 Установить логин", "🔑 Установить пароль"],
            ["🏫 Выбрать кампус", "⏰ Часовой пояс"],
            ["✅ Проверить настройки"],
            ["🔙 Главное меню"],
        ]
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    @staticmethod
    def get_campuses_keyboard(campuses: List[Dict]) -> ReplyKeyboardMarkup:
        """Создает клавиатуру с кампусами"""
        keyboard = []
        current_row = []

        for i, campus in enumerate(campuses):
            campus_name = campus["fullName"]
            if len(campus_name) > 30:
                campus_name = campus_name[:27] + "..."

            current_row.append(campus_name)

            if len(current_row) >= 2 or i == len(campuses) - 1:
                keyboard.append(current_row)
                current_row = []

        keyboard.append(["🔙 Назад к настройкам"])
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    @staticmethod
    def get_available_timezones() -> list[str]:
        """Возвращает список доступных часовых поясов России"""
        timezones = [
            "Europe/Kaliningrad",  # UTC+2
            "Europe/Moscow",  # UTC+3
            "Europe/Samara",  # UTC+4
            "Asia/Tashkent",  # UTC+5
            "Asia/Yekaterinburg",  # UTC+5
            "Asia/Omsk",  # UTC+6
            "Asia/Novosibirsk",  # UTC+7
            "Asia/Novokuznetsk",  # UTC+7
            "Asia/Krasnoyarsk",  # UTC+7
            "Asia/Irkutsk",  # UTC+8
            "Asia/Chita",  # UTC+9
            "Asia/Vladivostok",  # UTC+10
            "Asia/Magadan",  # UTC+11
            "Asia/Sakhalin",  # UTC+11
            "Asia/Kamchatka",  # UTC+12
            "Asia/Anadyr",  # UTC+12
        ]
        return [tz for tz in timezones if tz in available_timezones()]

    @staticmethod
    def get_timezone_display_name(timezone: str) -> str:
        """Возвращает понятное название пояса для UI"""
        display_names = {
            "Europe/Kaliningrad": "Калининград \n(UTC+2)",
            "Europe/Moscow": "Москва \n(UTC+3)",
            "Europe/Samara": "Самара \n(UTC+4)",
            "Asia/Tashkent": "Ташкент \n(UTC+5)",
            "Asia/Yekaterinburg": "Екатеринбург \n(UTC+5)",
            "Asia/Omsk": "Омск \n(UTC+6)",
            "Asia/Novosibirsk": "Новосибирск \n(UTC+7)",
            "Asia/Novokuznetsk": "Новокузнецк \n(UTC+7)",
            "Asia/Krasnoyarsk": "Красноярск \n(UTC+7)",
            "Asia/Irkutsk": "Иркутск \n(UTC+8)",
            "Asia/Chita": "Чита \n(UTC+9)",
            "Asia/Vladivostok": "Владивосток \n(UTC+10)",
            "Asia/Magadan": "Магадан \n(UTC+11)",
            "Asia/Sakhalin": "Сахалин \n(UTC+11)",
            "Asia/Kamchatka": "Камчатка \n(UTC+12)",
            "Asia/Anadyr": "Анадырь \n(UTC+12)",
        }
        return display_names.get(timezone, timezone)

    @staticmethod
    def get_available_timezones_keyboard() -> ReplyKeyboardMarkup:
        """Клавиатура с доступными часовыми поясами России"""
        timezones = Keyboards.get_available_timezones()
        keyboard = [
            [
                Keyboards.get_timezone_display_name(tz)
                for tz in timezones[i : i + 2]
            ]
            for i in range(0, len(timezones), 2)
        ]
        keyboard.append(["🔙 Назад к настройкам"])
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
