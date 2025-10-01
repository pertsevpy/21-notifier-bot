"""Логика Telegram-бота, включая обработчики команд и сообщений"""

import logging
from enum import Enum
from typing import Dict, List
import asyncio
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

from .config_manager import ConfigManager
from .platform_manager import SchoolPlatformManager
from .utils import convert_utc_to_local, clean_html, escape_markdown

logger = logging.getLogger(__name__)


class BotStates(Enum):
    """Состояния для ConversationHandler"""

    MAIN_MENU = 0
    SETTING_LOGIN = 1
    SETTING_PASSWORD = 2
    SELECTING_CAMPUS = 3
    CONFIRM_SETTINGS = 4
    TEST_AUTH = 5


class TelegramSchoolNotifier:
    """Telegram бот для уведомлений о событиях 21-school"""

    def __init__(self, telegram_token: str):
        self.telegram_token = telegram_token
        self.config_manager = ConfigManager()
        self.platform_manager = SchoolPlatformManager(self.config_manager)
        self.scheduler = AsyncIOScheduler()
        self.application = None
        self.is_running = False

        self.stats = {
            "last_check": None,
            "total_checks": 0,
            "notifications_sent": 0,
            "errors": 0,
        }

        self.setup_admin_chat_id()

    def setup_admin_chat_id(self):
        """Установка admin_chat_id при первом запуске"""
        if not self.config_manager.config["admin_chat_id"]:
            logger.info(
                "Admin chat ID не установлен, "
                "будет установлен при первом сообщении"
            )

    def get_main_menu_keyboard(self):
        """Клавиатура главного меню"""
        keyboard = [
            ["📊 Статус"],
            ["▶️ Запуск", "⏹️ Остановка"],
            ["🔐 Тест авторизации", "🔄 Сброс настроек"],
            ["⚙️ Настройки", "🔔 Последнее уведомление"],
        ]
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    def get_settings_keyboard(self):
        """Клавиатура настроек"""
        keyboard = [
            ["👤 Установить логин", "🔑 Установить пароль"],
            ["🏫 Выбрать кампус", "✅ Проверить настройки"],
            ["🔙 Главное меню"],
        ]
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    def get_campuses_keyboard(
        self, campuses: List[Dict]
    ) -> ReplyKeyboardMarkup:
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

    async def start_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Обработчик команды /start"""
        chat_id = str(update.effective_chat.id)

        if not self.config_manager.config["admin_chat_id"]:
            self.config_manager.update_setting("admin_chat_id", chat_id)
            await update.message.reply_text(
                "👋 Добро пожаловать! "
                "Вы установлены как администратор бота.\n\n"
                "Пожалуйста, настройте параметры для работы с платформой "
                "21-school.",
                reply_markup=self.get_main_menu_keyboard(),
            )
        else:
            if chat_id != self.config_manager.config["admin_chat_id"]:
                await update.message.reply_text(
                    "⛔ У вас нет прав для управления этим ботом"
                )
                return

            await update.message.reply_text(
                "🤖 Бот для уведомлений 21-school готов к работе!",
                reply_markup=self.get_main_menu_keyboard(),
            )

    async def stop_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Обработчик команды /stop"""
        await self.stop_monitoring(update, context)

    async def status_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Обработчик команды /status"""
        await self.status(update, context)

    async def open_settings_menu(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Открывает меню настроек при нажатии кнопки"""
        chat_id = str(update.effective_chat.id)
        if chat_id != self.config_manager.config["admin_chat_id"]:
            await update.message.reply_text(
                "⛔ У вас нет прав для управления этим ботом"
            )
            return

        await update.message.reply_text(
            "⚙️ Настройки платформы 21-school:",
            reply_markup=self.get_settings_keyboard(),
        )

    async def request_login(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Запрос логина"""
        chat_id = str(update.effective_chat.id)
        if chat_id != self.config_manager.config["admin_chat_id"]:
            await update.message.reply_text(
                "⛔ У вас нет прав для управления этим ботом"
            )
            return

        await update.message.reply_text(
            "Введите ваш логин от платформы 21-school:",
            reply_markup=ReplyKeyboardRemove(),
        )
        context.user_data["awaiting_login"] = True

    async def request_password(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Запрос пароля"""
        chat_id = str(update.effective_chat.id)
        if chat_id != self.config_manager.config["admin_chat_id"]:
            await update.message.reply_text(
                "⛔ У вас нет прав для управления этим ботом"
            )
            return

        await update.message.reply_text(
            "Введите ваш пароль от платформы 21-school:",
            reply_markup=ReplyKeyboardRemove(),
        )
        context.user_data["awaiting_password"] = True

    async def back_to_main_menu(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Возврат в главное меню"""
        chat_id = str(update.effective_chat.id)
        if chat_id != self.config_manager.config["admin_chat_id"]:
            await update.message.reply_text(
                "⛔ У вас нет прав для управления этим ботом"
            )
            return

        await update.message.reply_text(
            "Главное меню:", reply_markup=self.get_main_menu_keyboard()
        )

    async def handle_text_input(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Обрабатывает текстовый ввод (логин, пароль, выбор кампуса)"""
        chat_id = str(update.effective_chat.id)
        if chat_id != self.config_manager.config["admin_chat_id"]:
            return

        text = update.message.text
        logger.info(f"Обработка текстового ввода: '{text}'")

        if context.user_data.get("awaiting_campus_selection"):
            logger.info("Обрабатываем выбор кампуса")
            await self.handle_campus_selection(update, context)
            return

        if context.user_data.get("awaiting_login"):
            self.config_manager.update_setting("platform_login", text)
            await update.message.reply_text(
                f"✅ Логин установлен: {text}",
                reply_markup=self.get_settings_keyboard(),
            )
            context.user_data["awaiting_login"] = False
            logger.info("Логин установлен")

        elif context.user_data.get("awaiting_password"):
            self.config_manager.update_setting("platform_password", text)
            await update.message.reply_text(
                "✅ Пароль установлен",
                reply_markup=self.get_settings_keyboard(),
            )
            context.user_data["awaiting_password"] = False
            logger.info("Пароль установлен")

    async def select_campus(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Выбор кампуса из списка"""
        chat_id = str(update.effective_chat.id)
        if chat_id != self.config_manager.config["admin_chat_id"]:
            await update.message.reply_text(
                "⛔ У вас нет прав для управления этим ботом"
            )
            return

        logger.info("Начало выбора кампуса...")

        if (
            not self.config_manager.config["platform_login"]
            or not self.config_manager.config["platform_password"]
        ):
            await update.message.reply_text(
                "❌ Сначала установите логин и пароль для авторизации.",
                reply_markup=self.get_settings_keyboard(),
            )
            return

        await update.message.reply_text(
            "🔐 Выполняю авторизацию для получения списка кампусов..."
        )

        token = self.platform_manager.login_and_get_token()
        if not token:
            await update.message.reply_text(
                "❌ Ошибка авторизации! Проверьте логин и пароль.",
                reply_markup=self.get_settings_keyboard(),
            )
            return

        campuses = self.platform_manager.get_campuses()
        if not campuses:
            await update.message.reply_text(
                "❌ Не удалось получить список кампусов. Попробуйте позже.",
                reply_markup=self.get_settings_keyboard(),
            )
            return

        logger.info(f"Получено {len(campuses)} кампусов")

        context.user_data["campuses"] = campuses
        context.user_data["awaiting_campus_selection"] = True

        keyboard = self.get_campuses_keyboard(campuses)

        await update.message.reply_text(
            "🏫 Выберите ваш кампус из списка:", reply_markup=keyboard
        )

        logger.info("Клавиатура с кампусами отправлена пользователю")

    async def handle_campus_selection(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Обработка выбора кампуса"""
        chat_id = str(update.effective_chat.id)
        if chat_id != self.config_manager.config["admin_chat_id"]:
            await update.message.reply_text(
                "⛔ У вас нет прав для управления этим ботом"
            )
            return

        logger.info(f"Получено сообщение: {update.message.text}")

        selected_campus_name = update.message.text

        if selected_campus_name == "🔙 Назад к настройкам":
            await update.message.reply_text(
                "Возвращаюсь к настройкам:",
                reply_markup=self.get_settings_keyboard(),
            )
            context.user_data["awaiting_campus_selection"] = False
            logger.info("Пользователь вернулся к настройкам")
            return

        campuses = context.user_data.get("campuses", [])
        logger.info(f"Ищем кампус в списке из {len(campuses)} элементов")

        selected_campus = None

        for campus in campuses:
            if campus["fullName"] == selected_campus_name:
                selected_campus = campus
                logger.info(f"Найдено точное совпадение: {campus['fullName']}")
                break

        if not selected_campus:
            for campus in campuses:
                if selected_campus_name in campus["fullName"]:
                    selected_campus = campus
                    logger.info(
                        f"Найдено частичное совпадение: {campus['fullName']}"
                    )
                    break

        if not selected_campus:
            for campus in campuses:
                if selected_campus_name == campus["shortName"]:
                    selected_campus = campus
                    logger.info(f"Найдено по shortName: {campus['shortName']}")
                    break

        if selected_campus:
            self.config_manager.update_setting(
                "school_id", selected_campus["id"]
            )
            self.config_manager.update_setting(
                "campus_name", selected_campus["fullName"]
            )

            logger.info(
                f"Кампус сохранен: ID={selected_campus['id']}, "
                f"Name={selected_campus['fullName']}"
            )

            await update.message.reply_text(
                f"✅ Кампус выбран:\n\n"
                f"🏫 {selected_campus['fullName']}\n"
                f"🔗 ID: {selected_campus['id']}",
                reply_markup=self.get_settings_keyboard(),
            )
            logger.info("Сообщение об успешном выборе кампуса отправлено")
        else:
            logger.warning(f"Кампус не найден: '{selected_campus_name}'")
            await update.message.reply_text(
                "❌ Кампус не найден. Пожалуйста, выберите из списка.",
                reply_markup=self.get_campuses_keyboard(campuses),
            )

        context.user_data["awaiting_campus_selection"] = False

    async def show_settings(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Показать текущие настройки"""
        config = self.config_manager.config
        is_complete, missing = self.config_manager.get_config_status()

        status = (
            "✅ Полная"
            if is_complete
            else f"❌ Неполная (отсутствует: {', '.join(missing)})"
        )

        settings_text = f"""
⚙️ **Текущие настройки:**

👤 **Логин:** `{config['platform_login'] or 'Не установлен'}`
🔑 **Пароль:** `{'*' * 8 if config['platform_password'] else 'Не установлен'}`
🏫 **Кампус:** {config['campus_name'] or 'Не выбран'}
👑 **Admin Chat ID:** `{config['admin_chat_id'] or 'Не установлен'}`

📊 **Статус конфигурации:** {status}
🕐 **Последнее обновление:** {config['last_update'] or 'Никогда'}
        """

        await update.message.reply_text(settings_text, parse_mode="Markdown")

    async def start_monitoring(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Запуск мониторинга"""
        chat_id = str(update.effective_chat.id)
        if chat_id != self.config_manager.config["admin_chat_id"]:
            await update.message.reply_text(
                "⛔ У вас нет прав для управления этим ботом"
            )
            return

        is_complete, missing = self.config_manager.get_config_status()
        if not is_complete:
            await update.message.reply_text(
                f"❌ Конфигурация неполная! Отсутствуют: "
                f"{', '.join(missing)}\n"
                f"Пожалуйста, завершите настройку перед запуском."
            )
            return

        if self.is_running:
            await update.message.reply_text("🤖 Мониторинг уже запущен!")
            return

        self.is_running = True

        auth_result = await self.daily_auth()
        if not auth_result:
            await update.message.reply_text(
                "❌ Ошибка авторизации! Проверьте настройки."
            )
            self.is_running = False
            return

        self.scheduler.add_job(
            self.daily_auth, CronTrigger(hour=8, minute=0), id="daily_auth"
        )

        self.scheduler.add_job(
            self.check_notifications,
            IntervalTrigger(minutes=5),
            id="check_notifications",
        )

        self.scheduler.start()

        campus_name = self.config_manager.config["campus_name"]
        await update.message.reply_text(
            f"🚀 Мониторинг запущен для кампуса: {campus_name}!\n\n"
            "📅 Ежедневная авторизация: 8:00\n"
            "🔔 Проверка уведомлений: каждые 5 минут"
        )

        logger.info(f"Мониторинг запущен для кампуса: {campus_name}")

    async def stop_monitoring(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Остановка мониторинга"""
        chat_id = str(update.effective_chat.id)
        if chat_id != self.config_manager.config["admin_chat_id"]:
            await update.message.reply_text(
                "⛔ У вас нет прав для управления этим ботом"
            )
            return

        if not self.is_running:
            await update.message.reply_text("🤖 Мониторинг уже остановлен!")
            return

        self.is_running = False
        self.scheduler.shutdown()

        await update.message.reply_text("🛑 Мониторинг остановлен!")
        logger.info("Мониторинг остановлен")

    async def test_auth(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Тестирование авторизации"""
        chat_id = str(update.effective_chat.id)
        if chat_id != self.config_manager.config["admin_chat_id"]:
            await update.message.reply_text(
                "⛔ У вас нет прав для управления этим ботом"
            )
            return

        await update.message.reply_text("🔐 Тестирование авторизации...")

        token = self.platform_manager.login_and_get_token()
        if token:
            campuses = self.platform_manager.get_campuses()
            campus_count = len(campuses) if campuses else 0

            current_campus = self.config_manager.config["campus_name"]
            campus_info = (
                f"\n🏫 Текущий кампус: {current_campus}"
                if current_campus
                else ""
            )

            await update.message.reply_text(
                f"✅ Авторизация успешна!\n\n"
                f"Токен получен: {token[:30]}...\n"
                f"Доступно кампусов: {campus_count}"
                f"{campus_info}"
            )

            if campuses and current_campus:
                campuses_text = "📋 Доступные кампусы:\n"
                for i, campus in enumerate(campuses[:10], 1):
                    campuses_text += f"{i}. {campus['fullName']}\n"

                if len(campuses) > 10:
                    campuses_text += f"... и еще {len(campuses) - 10} кампусов"

                await update.message.reply_text(campuses_text)
        else:
            await update.message.reply_text(
                "❌ Ошибка авторизации!\n\n"
                "Проверьте:\n"
                "• Правильность логина и пароля\n"
                "• Доступ к платформе 21-school"
            )

    async def reset_settings(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Сброс настроек"""
        chat_id = str(update.effective_chat.id)
        if chat_id != self.config_manager.config["admin_chat_id"]:
            await update.message.reply_text(
                "⛔ У вас нет прав для управления этим ботом"
            )
            return

        admin_chat_id = self.config_manager.config["admin_chat_id"]
        self.config_manager.config = {
            "platform_login": "",
            "platform_password": "",
            "school_id": "",
            "campus_name": "",
            "admin_chat_id": admin_chat_id,
            "is_configured": False,
            "last_update": datetime.now(),
            "timezone": "Europe/Moscow",
        }
        self.config_manager.save_config()

        await update.message.reply_text("🔄 Все настройки сброшены!")

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Статус бота"""
        chat_id = str(update.effective_chat.id)
        if chat_id != self.config_manager.config["admin_chat_id"]:
            await update.message.reply_text(
                "⛔ У вас нет прав для управления этим ботом"
            )
            return

        status_text = "🟢 Запущен" if self.is_running else "🔴 Остановлен"
        is_complete, missing = self.config_manager.get_config_status()
        config_status = "✅ Полная" if is_complete else f"❌ Неполная"
        campus_name = self.config_manager.config["campus_name"] or "Не выбран"

        stats_text = f"""
🤖 **Статус бота**: {status_text}
⚙️ **Конфигурация**: {config_status}
🏫 **Кампус**: {campus_name}

📊 **Статистика:**
• Последняя проверка: {self.stats['last_check'] or 'Никогда'}
• Всего проверок: {self.stats['total_checks']}
• Отправлено уведомлений: {self.stats['notifications_sent']}
• Ошибок: {self.stats['errors']}

🔐 **Токен платформы:** {'✅ Установлен' if self.platform_manager.token else '❌ Отсутствует'}
📅 **Следующая авторизация:** {self.platform_manager.token_expiry or 'Неизвестно'}
        """

        await update.message.reply_text(stats_text)

    async def daily_auth(self) -> bool:
        """Ежедневная авторизация"""
        logger.info("Запуск ежедневной авторизации...")

        try:
            token = self.platform_manager.login_and_get_token()

            if token:
                message = "✅ Ежедневная авторизация прошла успешно!"
                logger.info(message)
                return True
            else:
                message = "❌ Ошибка ежедневной авторизации!"
                logger.error(message)
                return False

        except Exception as e:
            logger.error(f"Критическая ошибка при авторизации: {e}")
            self.stats["errors"] += 1
            return False

    async def check_notifications(self):
        """Проверка новых уведомлений"""
        if not self.is_running:
            return

        logger.info("Проверка новых уведомлений...")
        self.stats["total_checks"] += 1
        self.stats["last_check"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            if (
                not self.platform_manager.token
                or not self.platform_manager.token_expiry
                or self.platform_manager.token_expiry < datetime.now()
            ):
                logger.warning("Токен устарел, выполняем авторизацию...")
                await self.daily_auth()
                await asyncio.sleep(5)

            new_notifications = self.platform_manager.get_new_notifications()

            if new_notifications:
                logger.info(
                    f"Найдено {len(new_notifications)} новых уведомлений"
                )

                for notification in new_notifications:
                    await self.send_notification(notification)

                self.stats["notifications_sent"] += len(new_notifications)
            else:
                logger.info("Новых уведомлений нет")

        except Exception as e:
            logger.error(f"Ошибка при проверке уведомлений: {e}")
            self.stats["errors"] += 1

    async def send_notification(self, notification: Dict):
        """Отправка уведомления в Telegram с правильным форматированием"""
        try:
            message_text = self.format_notification_message(notification)

            admin_chat_id = self.config_manager.config["admin_chat_id"]
            if admin_chat_id:
                try:
                    await self.application.bot.send_message(
                        chat_id=admin_chat_id,
                        text=message_text,
                        parse_mode="MarkdownV2",
                    )
                    logger.info(
                        f"Уведомление отправлено: {notification['id']}"
                    )
                except Exception as markdown_error:
                    logger.warning(
                        f"MarkdownV2 не сработал, пробуем HTML: {markdown_error}"
                    )
                    html_message = self.format_notification_html(notification)
                    try:
                        await self.application.bot.send_message(
                            chat_id=admin_chat_id,
                            text=html_message,
                            parse_mode="HTML",
                        )
                        logger.info(
                            f"Уведомление отправлено в HTML: {notification['id']}"
                        )
                    except Exception as html_error:
                        logger.warning(
                            f"HTML не сработал, отправляем без форматирования: {html_error}"
                        )
                        plain_message = self.format_notification_plain(
                            notification
                        )
                        await self.application.bot.send_message(
                            chat_id=admin_chat_id,
                            text=plain_message,
                            parse_mode=None,
                        )
                        logger.info(
                            f"Уведомление отправлено без форматирования: "
                            f"{notification['id']}"
                        )

        except Exception as e:
            logger.error(f"Ошибка отправки уведомления: {e}")
            self.stats["errors"] += 1

    def format_notification_message(self, notification: Dict) -> str:
        """Форматирование сообщения об уведомлении с правильным экранированием
        и конвертацией времени"""
        try:
            timezone_str = self.config_manager.config.get(
                "timezone", "Europe/Moscow"
            )
            time_str = convert_utc_to_local(
                notification["time"], timezone_str
            )  # Используем из utils
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

    async def last_notification_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Обработчик кнопки 'Последнее уведомление'"""
        chat_id = str(update.effective_chat.id)
        if chat_id != self.config_manager.config["admin_chat_id"]:
            await update.message.reply_text(
                "⛔ У вас нет прав для управления этим ботом"
            )
            return

        await self.show_last_notification(update, context)

    async def show_last_notification(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Показывает последнее уведомление"""
        if not self.config_manager.config["school_id"]:
            await update.message.reply_text(
                "❌ Сначала выберите кампус в настройках.",
                reply_markup=self.get_settings_keyboard(),
            )
            return

        await update.message.reply_text(
            "🔍 Запрашиваю последнее уведомление..."
        )

        try:
            last_notification = self.platform_manager.get_last_notification()

            if last_notification:
                message_text = self.format_notification_message(
                    last_notification
                )

                try:
                    await update.message.reply_text(
                        text=message_text, parse_mode="MarkdownV2"
                    )
                    logger.info(
                        f"Последнее уведомление отправлено: {last_notification['id']}"
                    )
                except Exception as e:
                    logger.error(
                        f"Ошибка отправки последнего уведомления с Markdown: {e}"
                    )
                    plain_text = self.format_notification_plain(
                        last_notification
                    )
                    await update.message.reply_text(plain_text)
            else:
                await update.message.reply_text(
                    "📭 Уведомлений нет.\n"
                    "Когда появятся новые уведомления, "
                    "они будут отображаться здесь."
                )

        except Exception as e:
            logger.error(f"Ошибка при получении последнего уведомления: {e}")
            await update.message.reply_text(
                "❌ Ошибка при получении уведомления.\n"
                "Проверьте:\n"
                "• Настройки авторизации\n"
                "• Выбор кампуса\n"
                "• Интернет-соединение"
            )

    def run(self):
        """Синхронный запуск бота"""
        self.application = (
            Application.builder().token(self.telegram_token).build()
        )

        self.application.add_handler(
            CommandHandler("start", self.start_command)
        )
        self.application.add_handler(CommandHandler("stop", self.stop_command))
        self.application.add_handler(
            CommandHandler("status", self.status_command)
        )

        self.application.add_handler(
            MessageHandler(
                filters.Regex("^⚙️ Настройки$"), self.open_settings_menu
            )
        )
        self.application.add_handler(
            MessageHandler(filters.Regex("^📊 Статус$"), self.status_command)
        )
        self.application.add_handler(
            MessageHandler(filters.Regex("^▶️ Запуск$"), self.start_monitoring)
        )
        self.application.add_handler(
            MessageHandler(filters.Regex("^⏹️ Остановка$"), self.stop_command)
        )
        self.application.add_handler(
            MessageHandler(
                filters.Regex("^🔐 Тест авторизации$"), self.test_auth
            )
        )
        self.application.add_handler(
            MessageHandler(
                filters.Regex("^🔄 Сброс настроек$"), self.reset_settings
            )
        )

        self.application.add_handler(
            MessageHandler(
                filters.Regex("^👤 Установить логин$"), self.request_login
            )
        )
        self.application.add_handler(
            MessageHandler(
                filters.Regex("^🔑 Установить пароль$"), self.request_password
            )
        )
        self.application.add_handler(
            MessageHandler(
                filters.Regex("^🏫 Выбрать кампус$"), self.select_campus
            )
        )
        self.application.add_handler(
            MessageHandler(
                filters.Regex("^✅ Проверить настройки$"), self.show_settings
            )
        )
        self.application.add_handler(
            MessageHandler(
                filters.Regex("^🔙 Главное меню$"), self.back_to_main_menu
            )
        )
        self.application.add_handler(
            MessageHandler(
                filters.Regex("^🔔 Последнее уведомление$"),
                self.last_notification_command,
            )
        )

        self.application.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND, self.handle_text_input
            )
        )

        logger.info("Запуск Telegram бота...")
        self.application.run_polling()
