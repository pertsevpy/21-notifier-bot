#!/usr/bin/env python3
"""Telegram бот для мониторинга уведомлений с платформы 21-school"""

import logging
import os
import sys

from dotenv import load_dotenv
from bot.telegram_bot import TelegramSchoolNotifier

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


def main():
    """Основная функция"""
    # Загружаем переменные из .env файла
    load_dotenv()
    # Перезапись при наличии
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")

    if not telegram_token:
        print("❌ Не установлена переменная окружения TELEGRAM_BOT_TOKEN")
        print("Установите токен: export TELEGRAM_BOT_TOKEN='your_bot_token'")
        sys.exit(1)

    bot = TelegramSchoolNotifier(telegram_token=telegram_token)
    bot.run()


if __name__ == "__main__":
    main()
