"""Вспомогательные функции для форматирования сообщений и очистки HTML"""

import re
from datetime import datetime
from typing import Dict


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


def format_notification_message(notification: Dict) -> str:
    """Форматирование сообщения об уведомлении с правильным экранированием"""
    try:
        time_str = datetime.fromisoformat(
            notification["time"].replace("Z", "+00:00")
        ).strftime("%d.%m.%Y %H:%M")
        message_text = clean_html(notification["message"])

        escaped_time = escape_markdown(time_str)
        escaped_type = escape_markdown(
            notification.get("groupName", "Неизвестно")
        )
        escaped_message = escape_markdown(message_text)
        escaped_id = escape_markdown(notification["id"])

        formatted_message = (
            f"🔔 *Новое уведомление* 🔔\n"
            f"📅 *Время:* {escaped_time}\n"
            f"📋 *Тип:* {escaped_type}\n"
            f"💬 *Сообщение:*\n"
            f"{escaped_message}\n"
            f"🆔 *ID:* `{escaped_id}`"
        )

        return formatted_message.strip()

    except Exception as e:
        return (
            f"🔔 Новое уведомление\n\n"
            f"{e}\n"
            f"Время: {notification.get('time', 'Неизвестно')}\n"
            f"Тип: {notification.get('groupName', 'Неизвестно')}\n\n"
            f"Сообщение: {notification.get('message', '')}"
        )
