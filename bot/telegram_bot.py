"""Логика Telegram-бота, включая обработчики команд и сообщений"""

import asyncio
import fcntl
import logging
from contextlib import contextmanager
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional
from zoneinfo import available_timezones

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.error import TelegramError
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


@contextmanager
def file_lock(lock_file: str):
    """Контекстный менеджер для файловой блокировки"""
    with open(lock_file, "w", encoding="UTF-8") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        except IOError:
            logger.error(
                "Другой экземпляр бота уже запущен (lock file: %s)", lock_file
            )
            raise RuntimeError(
                "Another instance of the bot is already running"
            )
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


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

    def __init__(
        self, telegram_token: str = None, lock_file: str = "bot.lock"
    ):
        self.telegram_token = telegram_token
        if not self.telegram_token:
            raise ValueError("TELEGRAM_TOKEN не задан")
        self.lock_file = lock_file
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
            ["🏫 Выбрать кампус", "⏰ Часовой пояс"],
            ["✅ Проверить настройки"],
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

    def get_available_timezones(self) -> list[str]:
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

    def get_timezone_display_name(self, timezone: str) -> str:
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

    def get_available_timezones_keyboard(self) -> ReplyKeyboardMarkup:
        """Клавиатура с доступными часовыми поясами России"""
        timezones = self.get_available_timezones()
        # noqa: E203
        keyboard = [
            [self.get_timezone_display_name(tz) for tz in timezones[i : i + 2]]
            for i in range(0, len(timezones), 2)
        ]
        keyboard.append(["🔙 Назад к настройкам"])
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    async def select_timezone(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Выбор часового пояса"""
        chat_id = str(update.effective_chat.id)
        if chat_id != self.config_manager.config["admin_chat_id"]:
            await update.message.reply_text("⛔ У вас нет прав")
            return
        current_tz = self.config_manager.config.get("timezone", "UTC")
        await update.message.reply_text(
            f"⏰ Текущий часовой пояс: "
            f"{self.get_timezone_display_name(current_tz)}\n"
            f"Выберите новый:",
            reply_markup=self.get_available_timezones_keyboard(),
        )
        context.user_data["awaiting_timezone_selection"] = True

    async def handle_timezone_selection(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Обработка выбора часового пояса"""
        chat_id = str(update.effective_chat.id)
        if chat_id != self.config_manager.config["admin_chat_id"]:
            await update.message.reply_text("⛔ У вас нет прав")
            return
        selected_display_name = update.message.text
        if selected_display_name == "🔙 Назад к настройкам":
            await update.message.reply_text(
                "Возвращаюсь к настройкам:",
                reply_markup=self.get_settings_keyboard(),
            )
        else:
            display_names = {
                self.get_timezone_display_name(tz): tz
                for tz in self.get_available_timezones()
            }
            selected_tz = display_names.get(selected_display_name)
            if selected_tz:
                self.config_manager.update_setting("timezone", selected_tz)
                await update.message.reply_text(
                    f"✅ Часовой пояс установлен: {selected_display_name}",
                    reply_markup=self.get_settings_keyboard(),
                )
            else:
                await update.message.reply_text(
                    "❌ Неверный пояс. Выберите из списка.",
                    reply_markup=self.get_available_timezones_keyboard(),
                )
        context.user_data["awaiting_timezone_selection"] = False

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

    async def set_password(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Запрашивает ввод пароля"""
        chat_id = str(update.effective_chat.id)
        if chat_id != self.config_manager.config["admin_chat_id"]:
            await update.message.reply_text("⛔ У вас нет прав")
            return
        await update.message.reply_text(
            "🔑 Введите пароль для платформы 21-school:",
            reply_markup=ReplyKeyboardMarkup(
                [["🔙 Отмена"]], resize_keyboard=True
            ),
        )
        context.user_data["awaiting_password_input"] = True

    async def handle_text_input(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Обрабатывает текстовый ввод (логин, пароль, выбор кампуса)"""
        chat_id = str(update.effective_chat.id)
        if chat_id != self.config_manager.config["admin_chat_id"]:
            await update.message.reply_text("⛔ У вас нет прав")
            return

        message_id = update.message.message_id
        text = update.message.text.strip()

        if context.user_data.get("awaiting_campus_selection"):
            logger.info("Обрабатываем выбор кампуса")
            await self.handle_campus_selection(update, context)

        elif context.user_data.get("awaiting_login"):
            self.config_manager.update_setting("platform_login", text)
            await update.message.reply_text(
                f"✅ Логин установлен: {text}",
                reply_markup=self.get_settings_keyboard(),
            )
            context.user_data["awaiting_login"] = False
            logger.info("Логин установлен")

        elif context.user_data.get("awaiting_password_input"):
            if text == "🔙 Отмена":
                context.user_data["awaiting_password_input"] = False
                await update.message.reply_text(
                    "Ввод пароля отменен",
                    reply_markup=self.get_settings_keyboard(),
                )
            else:
                self.config_manager.update_setting("platform_password", text)
                logger.info("Пароль установлен")
                await update.message.reply_text(
                    "✅ Пароль установлен",
                    reply_markup=self.get_settings_keyboard(),
                )
                context.user_data["awaiting_password_input"] = False
                try:
                    await self.application.bot.delete_message(
                        chat_id=chat_id, message_id=message_id
                    )
                    logger.info(
                        "Сообщение с паролем (ID: %s) удалено", message_id
                    )
                except TelegramError as e:
                    logger.error("Ошибка удаления сообщения с паролем: %s", e)

        elif context.user_data.get("awaiting_timezone_selection"):
            logger.info("Обработка ввода часового пояса")
            await self.handle_timezone_selection(update, context)

        else:
            logger.info("Обработка текстового ввода: %s", text)
            await update.message.reply_text(
                "Пожалуйста, используйте команды или выберите действие из меню.",
                reply_markup=self.get_main_menu_keyboard(),
            )

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

        logger.info("Получено %d кампусов", len(campuses))

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
        if not await self._check_admin_permissions(update):
            return

        selected_campus_name = update.message.text
        logger.info("Получено сообщение: %s", selected_campus_name)

        if await self._handle_back_button(
            update, context, selected_campus_name
        ):
            return

        campuses = context.user_data.get("campuses", [])
        selected_campus = self._find_campus_by_name(
            selected_campus_name, campuses
        )

        if selected_campus:
            await self._handle_campus_found(update, context, selected_campus)
        else:
            await self._handle_campus_not_found(
                update, context, selected_campus_name, campuses
            )

        context.user_data["awaiting_campus_selection"] = False

    async def _check_admin_permissions(self, update: Update) -> bool:
        """Проверка прав администратора"""
        chat_id = str(update.effective_chat.id)
        if chat_id != self.config_manager.config["admin_chat_id"]:
            await update.message.reply_text(
                "⛔ У вас нет прав для управления этим ботом"
            )
            return False
        return True

    async def _handle_back_button(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        selected_text: str,
    ) -> bool:
        """Обработка кнопки 'Назад'"""
        if selected_text == "🔙 Назад к настройкам":
            await update.message.reply_text(
                "Возвращаюсь к настройкам:",
                reply_markup=self.get_settings_keyboard(),
            )
            context.user_data["awaiting_campus_selection"] = False
            logger.info("Пользователь вернулся к настройкам")
            return True
        return False

    def _find_campus_by_name(
        self, campus_name: str, campuses: List[Dict]
    ) -> Optional[Dict]:
        """Поиск кампуса по различным критериям"""
        logger.info("Ищем кампус в списке из %d элементов", len(campuses))

        # Поиск по точному совпадению полного имени
        for campus in campuses:
            if campus["fullName"] == campus_name:
                logger.info(
                    "Найдено точное совпадение: %s", campus["fullName"]
                )
                return campus

        # Поиск по частичному совпадению полного имени
        for campus in campuses:
            if campus_name in campus["fullName"]:
                logger.info(
                    "Найдено частичное совпадение: %s", campus["fullName"]
                )
                return campus

        # Поиск по короткому имени
        for campus in campuses:
            if campus_name == campus["shortName"]:
                logger.info("Найдено по shortName: %s", campus["shortName"])
                return campus

        logger.warning("Кампус не найден: %s", campus_name)
        return None

    async def _handle_campus_found(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, campus: Dict
    ):
        """Обработка найденного кампуса"""
        self.config_manager.update_setting("school_id", campus["id"])
        self.config_manager.update_setting("campus_name", campus["fullName"])

        logger.info(
            "Кампус сохранен: ID=%s, Name=%s",
            campus["id"],
            campus["fullName"],
        )

        await update.message.reply_text(
            f"✅ Кампус выбран:\n\n"
            f"🏫 {campus['fullName']}\n"
            f"🔗 ID: {campus['id']}",
            reply_markup=self.get_settings_keyboard(),
        )
        logger.info("Сообщение об успешном выборе кампуса отправлено")

    async def _handle_campus_not_found(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        campus_name: str,
        campuses: List[Dict],
    ):
        """Обработка случая, когда кампус не найден"""
        logger.warning("Кампус не найден: %s", campus_name)
        await update.message.reply_text(
            "❌ Кампус не найден. Пожалуйста, выберите из списка.",
            reply_markup=self.get_campuses_keyboard(campuses),
        )

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

        logger.info("Мониторинг запущен для кампуса: %s", campus_name)

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
        config_status = "✅ Полная" if is_complete else "❌ Неполная"
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
            logger.error("Критическая ошибка при авторизации: %s", e)
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
                    "Найдено %d новых уведомлений", len(new_notifications)
                )

                for notification in new_notifications:
                    await self.send_notification(notification)

                self.stats["notifications_sent"] += len(new_notifications)
            else:
                logger.info("Новых уведомлений нет")

        except Exception as e:
            logger.error("Ошибка при проверке уведомлений: %s", e)
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
                        "Уведомление отправлено: %s", notification["id"]
                    )
                except Exception as markdown_error:
                    logger.warning(
                        "MarkdownV2 не сработал, пробуем HTML: %s",
                        markdown_error,
                    )
                    html_message = self.format_notification_html(notification)
                    try:
                        await self.application.bot.send_message(
                            chat_id=admin_chat_id,
                            text=html_message,
                            parse_mode="HTML",
                        )
                        logger.info(
                            "Уведомление отправлено в HTML: %s",
                            notification["id"],
                        )
                    except Exception as html_error:
                        logger.warning(
                            "HTML не сработал, отправляем без форматирования: %s",
                            html_error,
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
                            "Уведомление отправлено без форматирования: %s",
                            notification["id"],
                        )

        except Exception as e:
            logger.error("Ошибка отправки уведомления: %s", e)
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
                        "Последнее уведомление отправлено: %s",
                        last_notification["id"],
                    )
                except Exception as e:
                    logger.error(
                        "Ошибка отправки последнего уведомления с Markdown: %s",
                        e,
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
            logger.error("Ошибка при получении последнего уведомления: %s", e)
            await update.message.reply_text(
                "❌ Ошибка при получении уведомления.\n"
                "Проверьте:\n"
                "• Настройки авторизации\n"
                "• Выбор кампуса\n"
                "• Интернет-соединение"
            )

    def run(self):
        """Синхронный запуск бота"""
        try:
            with file_lock(self.lock_file):
                self.application = (
                    Application.builder().token(self.telegram_token).build()
                )

                self.application.add_handler(
                    CommandHandler("start", self.start_command)
                )
                self.application.add_handler(
                    CommandHandler("stop", self.stop_command)
                )
                self.application.add_handler(
                    CommandHandler("status", self.status_command)
                )

                self.application.add_handler(
                    MessageHandler(
                        filters.Regex("^⚙️ Настройки$"), self.open_settings_menu
                    )
                )
                self.application.add_handler(
                    MessageHandler(
                        filters.Regex("^📊 Статус$"), self.status_command
                    )
                )
                self.application.add_handler(
                    MessageHandler(
                        filters.Regex("^▶️ Запуск$"), self.start_monitoring
                    )
                )
                self.application.add_handler(
                    MessageHandler(
                        filters.Regex("^⏹️ Остановка$"), self.stop_command
                    )
                )
                self.application.add_handler(
                    MessageHandler(
                        filters.Regex("^🔐 Тест авторизации$"), self.test_auth
                    )
                )
                self.application.add_handler(
                    MessageHandler(
                        filters.Regex("^🔄 Сброс настроек$"),
                        self.reset_settings,
                    )
                )

                self.application.add_handler(
                    MessageHandler(
                        filters.Regex("^👤 Установить логин$"),
                        self.request_login,
                    )
                )
                self.application.add_handler(
                    MessageHandler(
                        filters.Regex("^🔑 Установить пароль$"),
                        self.set_password,
                    )
                )
                self.application.add_handler(
                    MessageHandler(
                        filters.Regex("^🏫 Выбрать кампус$"),
                        self.select_campus,
                    )
                )
                self.application.add_handler(
                    MessageHandler(
                        filters.Regex("^✅ Проверить настройки$"),
                        self.show_settings,
                    )
                )
                self.application.add_handler(
                    MessageHandler(
                        filters.Regex("^🔙 Главное меню$"),
                        self.back_to_main_menu,
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
                        filters.Regex("^⏰ Часовой пояс$"),
                        self.select_timezone,
                    )
                )
                self.application.add_handler(
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND, self.handle_text_input
                    )
                )

                logger.info("Запуск Telegram бота...")
                self.application.run_polling()
        except RuntimeError as e:
            logger.error("Ошибка запуска бота: %s", e)
            raise
