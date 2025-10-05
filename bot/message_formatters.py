"""Форматирование сообщений для Telegram бота"""

from typing import Dict

from bot.utils import convert_utc_to_local, clean_html, escape_markdown


class MessageFormatters:
    """Класс для форматирования сообщений бота"""

    def __init__(self, config_manager):
        self.config_manager = config_manager

    def format_notification_message(self, notification: Dict) -> str:
        """Форматирование сообщения об уведомлении с правильным экранированием"""
        try:
            timezone_str = self.config_manager.config.get(
                "timezone", "Europe/Moscow"
            )
            time_str = convert_utc_to_local(notification["time"], timezone_str)
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

    def format_notification_html(self, notification: Dict) -> str:
        """Форматирование сообщения в HTML"""
        timezone_str = self.config_manager.config.get(
            "timezone", "Europe/Moscow"
        )
        time_str = convert_utc_to_local(notification["time"], timezone_str)
        message_text = clean_html(notification["message"])

        return (
            f"<b>🔔 Новое уведомление</b>\n"
            f"<b>📅 Время:</b> {time_str}\n"
            f"<b>📋 Тип:</b> {notification.get('groupName', 'Неизвестно')}\n"
            f"<b>💬 Сообщение:</b>\n{message_text}\n"
            f"<b>🆔 ID:</b> <code>{notification['id']}</code>"
        )

    def format_notification_plain(self, notification: Dict) -> str:
        """Форматирование сообщения без разметки"""
        timezone_str = self.config_manager.config.get(
            "timezone", "Europe/Moscow"
        )
        time_str = convert_utc_to_local(notification["time"], timezone_str)
        message_text = clean_html(notification["message"])

        return (
            f"🔔 Новое уведомление\n"
            f"Время: {time_str}\n"
            f"Тип: {notification.get('groupName', 'Неизвестно')}\n"
            f"Сообщение:\n{message_text}\n"
            f"ID: {notification['id']}"
        )
