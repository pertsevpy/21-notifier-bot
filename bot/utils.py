"""Вспомогательные функции для форматирования сообщений и очистки HTML"""

import re
from datetime import datetime

import pytz


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
    """Преобразует время UTC в локальный часовой пояс (переместили из telegram_bot.py для reuse)"""
    try:
        # Парсим время UTC
        utc_time = datetime.fromisoformat(utc_time_str.replace("Z", "+00:00"))
        # Создаем объект часового пояса
        local_tz = pytz.timezone(timezone_str)
        # Преобразуем время
        local_time = utc_time.astimezone(local_tz)
        # Форматируем в читаемый вид
        return local_time.strftime("%d.%m.%Y %H:%M (%Z)")
    except Exception as e:
        # Fallback на UTC при ошибке
        return (
            # В случае ошибки возвращаем оригинальное время
            utc_time_str.replace("T", " ").replace("Z", " UTC")
            + " (ошибка пояса: "
            + str(e)
            + ")"
        )
