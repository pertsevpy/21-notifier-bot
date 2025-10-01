"""Вспомогательные функции для форматирования сообщений и очистки HTML"""

import re
from datetime import datetime
from zoneinfo import ZoneInfo


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
    try:
        utc_time = datetime.fromisoformat(utc_time_str.replace("Z", "+00:00"))
        local_tz = ZoneInfo(timezone_str)
        local_time = utc_time.astimezone(local_tz)
        return local_time.strftime("%d.%m.%Y %H:%M (%Z)")
    except ValueError as e:
        return (
            utc_time_str.replace("T", " ").replace("Z", " UTC")
            + f" (ошибка времени: {e})"
        )
    except ZoneInfo.InvalidTimeZone as e:
        return (
            utc_time_str.replace("T", " ").replace("Z", " UTC")
            + f" (ошибка пояса: {e})"
        )
