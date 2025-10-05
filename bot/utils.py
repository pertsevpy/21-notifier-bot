"""Вспомогательные функции для форматирования сообщений и очистки HTML"""

import logging
import re
from datetime import datetime
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
        utc_time = datetime.fromisoformat(utc_time_str.replace("Z", "+00:00"))
        local_tz = ZoneInfo(timezone_str)
        local_time = utc_time.astimezone(local_tz)
        return local_time.strftime("%d.%m.%Y %H:%M (%Z)")
    except ValueError as e:
        logger.error("Invalid time format: %s", e)
        return f"{utc_time_str.replace('T', ' ').replace('Z', ' UTC')} (error: {e})"
    except ZoneInfoNotFoundError as e:
        logger.error("Invalid timezone: %s, falling back to UTC", e)
        try:
            utc_time = datetime.fromisoformat(
                utc_time_str.replace("Z", "+00:00")
            )
            local_tz = ZoneInfo(default_tz)
            local_time = utc_time.astimezone(local_tz)
            return local_time.strftime("%d.%m.%Y %H:%M (%Z)")
        except ValueError as ve:
            logger.error("Failed to convert to UTC: %s", ve)
            return f"{utc_time_str.replace('T', ' ').replace('Z', ' UTC')} (error: {ve})"
