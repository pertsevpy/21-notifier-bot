"""Вспомогательные функции для форматирования сообщений и очистки HTML"""

import logging
import re
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)


def clean_html(text: str) -> str:
    """Очистка HTML тегов из текста"""
    clean = re.compile("<.*?>")
    return re.sub(clean, "", text).replace("&nbsp;", " ")


def escape_markdown(text: str) -> str:
    """Экранирование специальных символов Markdown"""
    if not text:
        return ""

    escape_chars = r"_*[]()~`>#+-=|{}.!"
    escaped_text = ""

    for char in text:
        if char in escape_chars:
            escaped_text += "\\" + char
        else:
            escaped_text += char

    return escaped_text


def convert_utc_to_local(utc_time_str: str, timezone_str: str) -> str:
    """Преобразует время UTC в локальный часовой пояс"""
    if not utc_time_str:
        logger.warning("Empty UTC time string provided")
        return "Unknown time"

    default_tz = "UTC"
    if not timezone_str:
        logger.warning("Empty timezone provided, using UTC")
        timezone_str = default_tz

    try:
        # Пробуем нормализовать и распарсить
        normalized_time = _normalize_time_string(utc_time_str)
        utc_time = datetime.fromisoformat(normalized_time)
        local_tz = ZoneInfo(timezone_str)
        local_time = utc_time.astimezone(local_tz)
        return local_time.strftime("%d.%m.%Y %H:%M (%Z)")
    except ValueError as e:
        logger.error("Invalid time format: %s", e)
        # Возвращаем в старом формате для совместимости с тестами
        return f"{utc_time_str.replace('T', ' ').replace('Z', ' UTC')} (error: {e})"
    except ZoneInfoNotFoundError as e:
        logger.error("Invalid timezone: %s, falling back to UTC", e)
        try:
            normalized_time = _normalize_time_string(utc_time_str)
            utc_time = datetime.fromisoformat(normalized_time)
            local_tz = ZoneInfo(default_tz)
            local_time = utc_time.astimezone(local_tz)
            return local_time.strftime("%d.%m.%Y %H:%M (%Z)")
        except ValueError as ve:
            logger.error("Failed to convert to UTC: %s", ve)
            return f"{utc_time_str.replace('T', ' ').replace('Z', ' UTC')} (error: {ve})"


def _normalize_time_string(time_str: str) -> str:
    """Нормализует строку времени для корректного парсинга"""
    # Убираем пробелы
    time_str = time_str.strip()

    # Заменяем Z на +00:00 для стандартизации
    if time_str.endswith("Z"):
        time_str = time_str[:-1] + "+00:00"

    # Обрабатываем миллисекунды/микросекунды
    if "." in time_str and "+" in time_str:
        # Разделяем на основную часть и часовой пояс
        main_part, tz_part = time_str.split("+", 1)
        tz_part = "+" + tz_part

        # Обрабатываем дробную часть секунд
        if "." in main_part:
            date_part, time_part = main_part.split("T", 1)
            if "." in time_part:
                time_without_fraction, fraction = time_part.split(".", 1)
                # Оставляем только 6 знаков для микросекунд (или меньше если нужно)
                if len(fraction) > 6:
                    fraction = fraction[:6]
                elif len(fraction) < 6:
                    fraction = fraction.ljust(6, "0")
                time_part = time_without_fraction + "." + fraction

            main_part = date_part + "T" + time_part

        return main_part + tz_part

    return time_str
