#!/usr/bin/env python
"""Telegram бот для мониторинга уведомлений с платформы 21-school"""

import asyncio
import json
import logging
import os
import pickle
import sys
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import requests
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from webdriver_manager.chrome import ChromeDriverManager

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


# Состояния для ConversationHandler
class BotStates(Enum):
    """Состояния для ConversationHandler"""

    MAIN_MENU = 0
    SETTING_LOGIN = 1
    SETTING_PASSWORD = 2
    SELECTING_CAMPUS = 3
    CONFIRM_SETTINGS = 4
    TEST_AUTH = 5


class ConfigManager:
    """Менеджер конфигурации с сохранением в файл"""

    def __init__(self, config_file="bot_config.pkl"):
        self.config_file = config_file
        self.config = self.load_config()

    def load_config(self) -> Dict:
        """Загрузка конфигурации из файла"""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, "rb") as f:
                    return pickle.load(f)
        except Exception as e:
            logger.error(f"Ошибка загрузки конфигурации: {e}")

        # Конфигурация по умолчанию
        return {
            "platform_login": "",
            "platform_password": "",
            "school_id": "",
            "campus_name": "",
            "admin_chat_id": "",
            "is_configured": False,
            "last_update": None,
        }

    def save_config(self):
        """Сохранение конфигурации в файл"""
        try:
            self.config["last_update"] = datetime.now()
            with open(self.config_file, "wb") as f:
                pickle.dump(self.config, f)
            logger.info("Конфигурация сохранена")
        except Exception as e:
            logger.error(f"Ошибка сохранения конфигурации: {e}")

    def update_setting(self, key: str, value: str):
        """Обновление настройки"""
        self.config[key] = value
        self.config["is_configured"] = all(
            [
                self.config["platform_login"],
                self.config["platform_password"],
                self.config["school_id"],
                self.config["admin_chat_id"],
            ]
        )
        self.save_config()

    def get_config_status(self) -> Tuple[bool, List[str]]:
        """Проверка полноты конфигурации"""
        missing = []
        if not self.config["platform_login"]:
            missing.append("логин")
        if not self.config["platform_password"]:
            missing.append("пароль")
        if not self.config["school_id"]:
            missing.append("кампус")
        if not self.config["admin_chat_id"]:
            missing.append("admin_chat_id")

        return len(missing) == 0, missing


class SchoolPlatformManager:
    """Менеджер для работы с платформой 21-school"""

    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        self.token = None
        self.token_expiry = None
        self.last_notification_ids = set()
        self.session = requests.Session()
        self.campuses = []  # Список кампусов

        # Настройка сессии с повторными попытками
        retry_strategy = requests.adapters.Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = requests.adapters.HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def setup_driver(self, headless: bool = True) -> webdriver.Chrome:
        """Настройка Chrome WebDriver с автоматическим поиском браузера"""
        chrome_options = Options()
        if headless:
            chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument(
            "--disable-blink-features=AutomationControlled"
        )
        chrome_options.add_experimental_option(
            "excludeSwitches", ["enable-automation"]
        )
        chrome_options.add_experimental_option("useAutomationExtension", False)

        try:
            # Попробуем использовать webdriver-manager с автоматическим определением
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=chrome_options)
            driver.execute_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            return driver
        except Exception as e:
            logger.warning(f"Стандартный метод не сработал: {e}")
            logger.info("Пробуем альтернативные методы...")

            # Альтернативный метод: ручное указание пути
            return self._setup_driver_manual(chrome_options)

    def _setup_driver_manual(self, chrome_options):
        """Ручная настройка драйвера с поиском браузера"""
        import shutil

        # Список возможных путей к браузерам
        possible_browsers = [
            # Google Chrome
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/local/bin/chrome",
            "/opt/google/chrome/chrome",
            # Chromium
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/usr/local/bin/chromium",
            # Snap (Ubuntu)
            "/snap/bin/chromium",
            # Flatpak
            "/var/lib/flatpak/exports/bin/com.google.Chrome",
        ]

        # Проверяем наличие браузеров в системе
        found_browser = None
        for browser_path in possible_browsers:
            if os.path.exists(browser_path):
                found_browser = browser_path
                logger.info(f"Найден браузер: {browser_path}")
                break

        # Если не нашли по путям, пробуем найти через which
        if not found_browser:
            for browser_cmd in [
                "google-chrome",
                "google-chrome-stable",
                "chromium",
                "chromium-browser",
            ]:
                try:
                    browser_path = shutil.which(browser_cmd)
                    if browser_path:
                        found_browser = browser_path
                        logger.info(
                            f"Найден браузер через which: {browser_path}"
                        )
                        break
                except:
                    continue

        if found_browser:
            chrome_options.binary_location = found_browser
            logger.info(f"Используем браузер: {found_browser}")
        else:
            logger.warning("Браузер не найден, пробуем без указания пути...")

        try:
            # Пробуем с найденным путем к браузеру
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=chrome_options)
            driver.execute_script(
                "Object.defineProperty(navigator, 'webdriver', "
                "{get: () => undefined})"
            )
            return driver
        except Exception as e:
            logger.error(f"Ошибка при создании драйвера: {e}")
            raise

    def login_via_api(
        self, login: str = None, password: str = None
    ) -> Optional[str]:
        """Авторизация через API Keycloak"""
        # Используем переданные учетные данные или из конфигурации
        login = login or self.config_manager.config["platform_login"]
        password = password or self.config_manager.config["platform_password"]

        if not login or not password:
            logger.error("Логин или пароль не установлены для API авторизации")
            return None

        url = "https://auth.21-school.ru/auth/realms/EduPowerKeycloak/protocol/openid-connect/token"

        payload = {
            "client_id": "s21-open-api",
            "username": login,
            "password": password,
            "grant_type": "password",
        }

        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        try:
            logger.info("Попытка авторизации через API...")
            response = self.session.post(
                url, headers=headers, data=payload, timeout=15
            )
            response.raise_for_status()

            data = response.json()
            access_token = data.get("access_token")

            if access_token:
                logger.info("✅ Токен успешно получен через API")
                return access_token
            else:
                logger.error("❌ Токен не найден в ответе API")
                return None

        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Ошибка API авторизации: {e}")
            if hasattr(e, "response") and e.response is not None:
                status_code = e.response.status_code
                if status_code == 401:
                    logger.error("Неверный логин или пароль")
                elif status_code == 400:
                    logger.error("Неверный запрос к API")
                elif status_code >= 500:
                    logger.error("Проблемы на сервере авторизации")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"❌ Ошибка парсинга JSON ответа API: {e}")
            return None
        except Exception as e:
            logger.error(f"❌ Неожиданная ошибка при API авторизации: {e}")
            return None

    def login_and_get_token(
        self, login: str = None, password: str = None
    ) -> Optional[str]:
        """Авторизация на платформе с приоритетом API, fallback на Selenium"""
        # Используем переданные учетные данные или из конфигурации
        login = login or self.config_manager.config["platform_login"]
        password = password or self.config_manager.config["platform_password"]

        if not login or not password:
            logger.error("Логин или пароль не установлены")
            return None

        # Пробуем сначала API метод
        logger.info("🔄 Попытка авторизации через API...")
        api_token = self.login_via_api(login, password)

        if api_token:
            self.token = api_token
            self.token_expiry = datetime.now() + timedelta(
                hours=10
            )  # API токен живет 10 часов
            logger.info("✅ Авторизация через API успешна")
            return api_token

        # Если API не сработал, используем Selenium как fallback
        logger.warning(
            "❌ API авторизация не удалась, пробуем через Selenium..."
        )
        selenium_token = self.login_via_selenium(login, password)

        if selenium_token:
            self.token = selenium_token
            self.token_expiry = datetime.now() + timedelta(
                hours=23
            )  # Selenium токен живет ~23 часа
            logger.info("✅ Авторизация через Selenium успешна")
            return selenium_token

        logger.error("❌ Все методы авторизации не удались")
        return None

    def login_via_selenium(self, login: str, password: str) -> Optional[str]:
        """Авторизация через Selenium (fallback метод)"""
        driver = None
        try:
            driver = self.setup_driver(headless=True)
            logger.info("Запуск браузера для авторизации через Selenium...")

            driver.get("https://platform.21-school.ru")

            # Ожидание формы авторизации
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.NAME, "username"))
            )

            # Заполнение формы
            username_field = driver.find_element(By.NAME, "username")
            password_field = driver.find_element(By.NAME, "password")

            username_field.clear()
            username_field.send_keys(login)
            password_field.clear()
            password_field.send_keys(password)

            # Отправка формы
            password_field.submit()

            # Ждем завершения авторизации
            token = self.wait_for_token(driver)

            if token:
                logger.info("✅ Токен получен через Selenium")
                return token
            else:
                logger.error("❌ Токен не найден после Selenium авторизации")
                return None

        except TimeoutException:
            logger.error("❌ Таймаут при Selenium авторизации")
            return None
        except Exception as e:
            logger.error(f"❌ Ошибка при Selenium авторизации: {e}")
            return None
        finally:
            if driver:
                driver.quit()

    def validate_token(self, token: str) -> bool:
        """Проверка валидности токена через API кампусов"""
        if not token:
            return False

        url = (
            "https://platform.21-school.ru/services/21-school/api/v1/campuses"
        )

        headers = {
            "Authorization": f"Bearer {token}",
            "User-Agent": "Mozilla/5.0 (compatible; SchoolNotifier/1.0)",
        }

        try:
            response = self.session.get(url, headers=headers, timeout=10)
            return response.status_code == 200
        except:
            return False

    def wait_for_token(self, driver, timeout: int = 30) -> Optional[str]:
        """Ожидание и извлечение токена из различных источников"""
        try:
            # Ждем завершения авторизации - проверяем различные условия
            WebDriverWait(driver, timeout).until(
                lambda d: any(
                    [
                        self._has_token_in_url(d),
                        self._has_token_in_local_storage(d),
                        self._has_token_in_session_storage(d),
                        self._has_token_in_cookies(d),
                        self._is_dashboard_loaded(
                            d
                        ),  # Или загрузилась главная страница
                    ]
                )
            )

            # Пробуем извлечь токен из разных источников
            token_sources = [
                self._extract_token_from_local_storage,
                self._extract_token_from_session_storage,
                self._extract_token_from_cookies,
                self._extract_token_from_url,
                self._extract_token_from_page_content,
            ]

            for source_method in token_sources:
                token = source_method(driver)
                if token:
                    logger.info(f"Токен найден через {source_method.__name__}")
                    return token

            return None

        except TimeoutException:
            logger.error("Таймаут при ожидании токена")
            return None

    def _has_token_in_url(self, driver) -> bool:
        """Проверяет наличие токена в URL"""
        current_url = driver.current_url
        token_indicators = ["tokenId", "access_token", "id_token", "token"]
        return any(indicator in current_url for indicator in token_indicators)

    def _has_token_in_local_storage(self, driver) -> bool:
        """Проверяет наличие токена в Local Storage"""
        try:
            local_storage = driver.execute_script(
                "return window.localStorage;"
            )
            token_keys = [
                "tokenId",
                "access_token",
                "id_token",
                "token",
                "authToken",
            ]
            return any(key in local_storage for key in token_keys)
        except:
            return False

    def _has_token_in_session_storage(self, driver) -> bool:
        """Проверяет наличие токена в Session Storage"""
        try:
            session_storage = driver.execute_script(
                "return window.sessionStorage;"
            )
            token_keys = [
                "tokenId",
                "access_token",
                "id_token",
                "token",
                "authToken",
            ]
            return any(key in session_storage for key in token_keys)
        except:
            return False

    def _has_token_in_cookies(self, driver) -> bool:
        """Проверяет наличие токена в cookies"""
        try:
            cookies = driver.get_cookies()
            token_names = [
                "tokenId",
                "access_token",
                "id_token",
                "token",
                "authToken",
            ]
            return any(cookie["name"] in token_names for cookie in cookies)
        except:
            return False

    def _is_dashboard_loaded(self, driver) -> bool:
        """Проверяет, загрузилась ли главная страница (альтернативный способ)"""
        try:
            # Проверяем наличие элементов, характерных для главной страницы после авторизации
            indicators = [
                "dashboard",
                "navbar",
                "menu",
                "profile",
                "//*[contains(text(), 'Dashboard')]",
                "//*[contains(text(), 'Главная')]",
                "//*[contains(@class, 'dashboard')]",
            ]

            for indicator in indicators:
                try:
                    if indicator.startswith("//"):
                        elements = driver.find_elements(By.XPATH, indicator)
                    else:
                        elements = driver.find_elements(
                            By.CLASS_NAME, indicator
                        )
                        if not elements:
                            elements = driver.find_elements(By.ID, indicator)

                    if elements:
                        return True
                except:
                    continue

            return False
        except:
            return False

    def _extract_token_from_url(self, driver) -> Optional[str]:
        """Извлечение токена из URL"""
        current_url = driver.current_url
        parsed_url = urlparse(current_url)

        # Проверка фрагмента URL
        if parsed_url.fragment:
            fragment_params = parse_qs(parsed_url.fragment)
            for key in ["tokenId", "access_token", "id_token", "token"]:
                token = fragment_params.get(key, [None])[0]
                if token:
                    return token

        # Проверка query параметров
        if parsed_url.query:
            query_params = parse_qs(parsed_url.query)
            for key in ["tokenId", "access_token", "id_token", "token"]:
                token = query_params.get(key, [None])[0]
                if token:
                    return token

        return None

    def _extract_token_from_local_storage(self, driver) -> Optional[str]:
        """Извлечение токена из Local Storage"""
        try:
            token_keys = [
                "tokenId",
                "access_token",
                "id_token",
                "token",
                "authToken",
            ]
            for key in token_keys:
                token = driver.execute_script(
                    f"return window.localStorage.getItem('{key}');"
                )
                if token:
                    return token
            return None
        except Exception as e:
            logger.warning(f"Ошибка при чтении Local Storage: {e}")
            return None

    def _extract_token_from_session_storage(self, driver) -> Optional[str]:
        """Извлечение токена из Session Storage"""
        try:
            token_keys = [
                "tokenId",
                "access_token",
                "id_token",
                "token",
                "authToken",
            ]
            for key in token_keys:
                token = driver.execute_script(
                    f"return window.sessionStorage.getItem('{key}');"
                )
                if token:
                    return token
            return None
        except Exception as e:
            logger.warning(f"Ошибка при чтении Session Storage: {e}")
            return None

    def _extract_token_from_cookies(self, driver) -> Optional[str]:
        """Извлечение токена из cookies"""
        try:
            cookies = driver.get_cookies()
            token_names = [
                "tokenId",
                "access_token",
                "id_token",
                "token",
                "authToken",
            ]
            for cookie in cookies:
                if cookie["name"] in token_names:
                    return cookie["value"]
            return None
        except Exception as e:
            logger.warning(f"Ошибка при чтении cookies: {e}")
            return None

    def _extract_token_from_page_content(self, driver) -> Optional[str]:
        """Извлечение токена из содержимого страницы (метод на крайний случай)"""
        try:
            # Ищем токен в JavaScript переменных
            scripts = driver.find_elements(By.TAG_NAME, "script")
            for script in scripts:
                script_content = script.get_attribute("innerHTML")
                if script_content:
                    # Ищем паттерны токенов
                    token_patterns = [
                        r"tokenId['\"]?\\s*[:=]\\s*['\"]([^'\"]+)['\"]",
                        r"access_token['\"]?\\s*[:=]\\s*['\"]([^'\"]+)['\"]",
                        r"id_token['\"]?\\s*[:=]\\s*['\"]([^'\"]+)['\"]",
                        r"token['\"]?\\s*[:=]\\s*['\"]([^'\"]+)['\"]",
                    ]

                    for pattern in token_patterns:
                        import re

                        match = re.search(pattern, script_content)
                        if match:
                            return match.group(1)

            return None
        except Exception as e:
            logger.warning(
                f"Ошибка при поиске токена в содержимом страницы: {e}"
            )
            return None

    def extract_token_from_url(self, url: str) -> Optional[str]:
        """Извлечение токена из URL"""
        parsed_url = urlparse(url)

        # Проверка фрагмента URL
        if parsed_url.fragment:
            fragment_params = parse_qs(parsed_url.fragment)
            for key in ["tokenId", "access_token", "id_token"]:
                token = fragment_params.get(key, [None])[0]
                if token:
                    return token

        # Проверка query параметров
        if parsed_url.query:
            query_params = parse_qs(parsed_url.query)
            for key in ["tokenId", "access_token", "id_token"]:
                token = query_params.get(key, [None])[0]
                if token:
                    return token

        return None

    def get_campuses(self) -> Optional[List[Dict]]:
        """Получение списка кампусов с платформы"""
        if not self.token:
            logger.error("Токен не установлен для получения кампусов")
            return None

        # Проверяем валидность токена
        if not self.validate_token(self.token):
            logger.warning("Токен невалиден, пробуем переавторизоваться...")
            new_token = self.login_and_get_token()
            if not new_token:
                logger.error(
                    "Не удалось переавторизоваться для получения кампусов"
                )
                return None
            self.token = new_token

        url = (
            "https://platform.21-school.ru/services/21-school/api/v1/campuses"
        )

        headers = {
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "Mozilla/5.0 (compatible; SchoolNotifier/1.0)",
        }

        try:
            response = self.session.get(url, headers=headers, timeout=15)
            response.raise_for_status()

            data = response.json()
            campuses = data.get("campuses", [])

            logger.info(f"Получено {len(campuses)} кампусов")
            self.campuses = campuses
            return campuses

        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка при получении списка кампусов: {e}")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"Ошибка парсинга JSON кампусов: {e}")
            return None

    def get_notifications(self) -> Optional[List[Dict]]:
        """Получение списка уведомлений"""
        if not self.token:
            logger.error("Токен не установлен")
            return None

        school_id = self.config_manager.config["school_id"]
        if not school_id:
            logger.error("School ID не установлен")
            return None

        url = "https://platform.21-school.ru/services/graphql"

        payload = {
            "operationName": "getUserNotifications",
            "variables": {"paging": {"offset": 0, "limit": 50}},
            "query": """query getUserNotifications($paging: PagingInput!) {
                s21Notification {
                    getS21Notifications(paging: $paging) {
                        notifications {
                            id
                            relatedObjectType
                            relatedObjectId
                            message
                            time
                            wasRead
                            groupName
                            __typename
                        }
                        totalCount
                        groupNames
                        __typename
                    }
                    __typename
                }
            }""",
        }

        headers = {
            "userrole": "STUDENT",
            "accept": "application/json",
            "content-type": "application/json",
            "schoolid": school_id,
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "Mozilla/5.0 (compatible; SchoolNotifier/1.0)",
        }

        try:
            response = self.session.post(
                url, json=payload, headers=headers, timeout=15
            )
            response.raise_for_status()

            data = response.json()

            if "errors" in data:
                logger.error(f"GraphQL ошибки: {data['errors']}")
                return None

            notifications = (
                data.get("data", {})
                .get("s21Notification", {})
                .get("getS21Notifications", {})
                .get("notifications", [])
            )
            return notifications

        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка при запросе уведомлений: {e}")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"Ошибка парсинга JSON: {e}")
            return None

    def get_new_notifications(self) -> List[Dict]:
        """Получение только новых уведомлений"""
        current_notifications = self.get_notifications()
        if not current_notifications:
            return []

        current_ids = {n["id"] for n in current_notifications}

        # Если это первый запуск, сохраняем ID и не возвращаем уведомления
        if not self.last_notification_ids:
            self.last_notification_ids = current_ids
            return []

        # Находим новые уведомления
        new_ids = current_ids - self.last_notification_ids
        new_notifications = [
            n for n in current_notifications if n["id"] in new_ids
        ]

        # Обновляем множество ID
        self.last_notification_ids = current_ids

        return new_notifications

    def get_last_notification(self) -> Optional[Dict]:
        """Получение последнего уведомления через GraphQL API"""
        if not self.token:
            logger.error("Токен не установлен для получения уведомлений")
            return None

        # Проверяем валидность токена
        if not self.validate_token(self.token):
            logger.warning(
                "Токен невалиден при запросе уведомлений, "
                "пробуем переавторизоваться..."
            )
            new_token = self.login_and_get_token()
            if not new_token:
                logger.error(
                    "Не удалось переавторизоваться для получения уведомлений"
                )
                return None
            self.token = new_token

        school_id = self.config_manager.config["school_id"]
        if not school_id:
            logger.error("School ID не установлен")
            return None

        url = "https://platform.21-school.ru/services/graphql"

        # Запрос для получения только последнего уведомления
        payload = {
            "operationName": "getUserNotifications",
            "variables": {
                "paging": {
                    "offset": 0,
                    "limit": 1,
                }  # Только одно последнее уведомление
            },
            "query": """query getUserNotifications($paging: PagingInput!) {
                s21Notification {
                    getS21Notifications(paging: $paging) {
                        notifications {
                            id
                            relatedObjectType
                            relatedObjectId
                            message
                            time
                            wasRead
                            groupName
                            __typename
                        }
                        totalCount
                        groupNames
                        __typename
                    }
                    __typename
                }
            }""",
        }

        headers = {
            "userrole": "STUDENT",
            "accept": "application/json",
            "content-type": "application/json",
            "schoolid": school_id,
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "Mozilla/5.0 (compatible; SchoolNotifier/1.0)",
        }

        try:
            logger.info("Запрос последнего уведомления...")
            response = self.session.post(
                url, json=payload, headers=headers, timeout=15
            )
            response.raise_for_status()

            data = response.json()

            if "errors" in data:
                logger.error(
                    f"GraphQL ошибки при запросе последнего уведомления: "
                    f"{data['errors']}"
                )
                return None

            notifications = (
                data.get("data", {})
                .get("s21Notification", {})
                .get("getS21Notifications", {})
                .get("notifications", [])
            )

            if notifications:
                last_notification = notifications[0]
                logger.info(
                    f"Получено последнее уведомление: "
                    f"{last_notification['id']}"
                )
                return last_notification
            else:
                logger.info("Уведомлений нет")
                return None

        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка при запросе последнего уведомления: {e}")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"Ошибка парсинга JSON последнего уведомления: {e}")
            return None


class TelegramSchoolNotifier:
    """Telegram бот для уведомлений о событиях 21-school"""

    def __init__(self, telegram_token: str):
        self.telegram_token = telegram_token
        self.config_manager = ConfigManager()
        self.platform_manager = SchoolPlatformManager(self.config_manager)
        self.scheduler = AsyncIOScheduler()
        self.application = None
        self.is_running = False

        # Статистика
        self.stats = {
            "last_check": None,
            "total_checks": 0,
            "notifications_sent": 0,
            "errors": 0,
        }

        # Устанавливаем admin_chat_id при первом запуске
        self.setup_admin_chat_id()

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

        # Проверяем, ожидаем ли мы выбор кампуса
        if context.user_data.get("awaiting_campus_selection"):
            logger.info("Обрабатываем выбор кампуса")
            await self.handle_campus_selection(update, context)
            return

        # Остальная логика для логина и пароля
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

    async def start_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Обработчик команды /start"""
        chat_id = str(update.effective_chat.id)

        # Устанавливаем admin_chat_id при первом запуске
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
            # Ограничиваем длину текста для кнопки
            campus_name = campus["fullName"]
            if len(campus_name) > 30:
                campus_name = campus_name[:27] + "..."

            current_row.append(campus_name)

            # Создаем новую строку каждые 2 кампуса или в конце списка
            if len(current_row) >= 2 or i == len(campuses) - 1:
                keyboard.append(current_row)
                current_row = []

        keyboard.append(["🔙 Назад к настройкам"])
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        # Устанавливаем admin_chat_id при первом запуске
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

        return BotStates.MAIN_MENU

    async def main_menu(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Главное меню"""
        chat_id = str(update.effective_chat.id)
        if chat_id != self.config_manager.config["admin_chat_id"]:
            await update.message.reply_text(
                "⛔ У вас нет прав для управления этим ботом"
            )
            return

        text = update.message.text

        if text == "⚙️ Настройки":
            await update.message.reply_text(
                "⚙️ Настройки платформы 21-school:",
                reply_markup=self.get_settings_keyboard(),
            )
            return BotStates.MAIN_MENU

        elif text == "📊 Статус":
            await self.status(update, context)

        elif text == "▶️ Запуск":
            await self.start_monitoring(update, context)

        elif text == "⏹️ Остановка":
            await self.stop_monitoring(update, context)

        elif text == "🔐 Тест авторизации":
            await self.test_auth(update, context)

        elif text == "🔄 Сброс настроек":
            await self.reset_settings(update, context)

        return BotStates.MAIN_MENU

    async def settings_menu(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Меню настроек"""
        chat_id = str(update.effective_chat.id)
        if chat_id != self.config_manager.config["admin_chat_id"]:
            await update.message.reply_text(
                "⛔ У вас нет прав для управления этим ботом"
            )
            return BotStates.MAIN_MENU

        text = update.message.text

        if text == "👤 Установить логин":
            await update.message.reply_text(
                "Введите ваш логин от платформы 21-school:",
                reply_markup=ReplyKeyboardRemove(),
            )
            return BotStates.SETTING_LOGIN

        elif text == "🔑 Установить пароль":
            await update.message.reply_text(
                "Введите ваш пароль от платформы 21-school:",
                reply_markup=ReplyKeyboardRemove(),
            )
            return BotStates.SETTING_PASSWORD

        elif text == "🏫 Выбрать кампус":
            await self.select_campus(update, context)

        elif text == "✅ Проверить настройки":
            await self.show_settings(update, context)

        elif text == "🔙 Главное меню":
            await update.message.reply_text(
                "Главное меню:", reply_markup=self.get_main_menu_keyboard()
            )
            return BotStates.MAIN_MENU

        return BotStates.MAIN_MENU

    async def set_login(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Установка логина"""
        login = update.message.text.strip()
        self.config_manager.update_setting("platform_login", login)

        await update.message.reply_text(
            f"✅ Логин установлен: {login}",
            reply_markup=self.get_settings_keyboard(),
        )

        return BotStates.MAIN_MENU

    async def set_password(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Установка пароля"""
        password = update.message.text.strip()
        self.config_manager.update_setting("platform_password", password)

        await update.message.reply_text(
            "✅ Пароль установлен", reply_markup=self.get_settings_keyboard()
        )

        return BotStates.MAIN_MENU

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

        # Проверяем, установлены ли логин и пароль
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

        # Выполняем авторизацию
        token = self.platform_manager.login_and_get_token()
        if not token:
            await update.message.reply_text(
                "❌ Ошибка авторизации! Проверьте логин и пароль.",
                reply_markup=self.get_settings_keyboard(),
            )
            return

        # Получаем список кампусов
        campuses = self.platform_manager.get_campuses()
        if not campuses:
            await update.message.reply_text(
                "❌ Не удалось получить список кампусов. Попробуйте позже.",
                reply_markup=self.get_settings_keyboard(),
            )
            return

        logger.info(f"Получено {len(campuses)} кампусов")

        # Сохраняем кампусы в контексте для использования при выборе
        context.user_data["campuses"] = campuses
        context.user_data["awaiting_campus_selection"] = True

        # Создаем клавиатуру с кампусами
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

        # Ищем выбранный кампус в списке
        campuses = context.user_data.get("campuses", [])
        logger.info(f"Ищем кампус в списке из {len(campuses)} элементов")

        selected_campus = None

        # Сначала ищем точное совпадение
        for campus in campuses:
            if campus["fullName"] == selected_campus_name:
                selected_campus = campus
                logger.info(f"Найдено точное совпадение: {campus['fullName']}")
                break

        # Если не нашли, ищем частичное совпадение
        if not selected_campus:
            for campus in campuses:
                if selected_campus_name in campus["fullName"]:
                    selected_campus = campus
                    logger.info(
                        f"Найдено частичное совпадение: {campus['fullName']}"
                    )
                    break

        # Если все еще не нашли, ищем по короткому имени
        if not selected_campus:
            for campus in campuses:
                if selected_campus_name == campus["shortName"]:
                    selected_campus = campus
                    logger.info(f"Найдено по shortName: {campus['shortName']}")
                    break

        if selected_campus:
            # Сохраняем выбранный кампус
            self.config_manager.update_setting(
                "school_id", selected_campus["id"]
            )
            self.config_manager.update_setting(
                "campus_name", selected_campus["fullName"]
            )

            logger.info(
                f'Кампус сохранен: ID={selected_campus['id']}, "'
                f'f"Name={selected_campus['fullName']}'
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

        # Сбрасываем флаг ожидания выбора кампуса
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

        # Проверяем полноту конфигурации
        is_complete, missing = self.config_manager.get_config_status()
        if not is_complete:
            await update.message.reply_text(
                f"❌ Конфигурация неполная! Отсутствуют: {', '.join(missing)}\n"
                "Пожалуйста, завершите настройку перед запуском."
            )
            return

        if self.is_running:
            await update.message.reply_text("🤖 Мониторинг уже запущен!")
            return

        self.is_running = True

        # Начальная авторизация
        auth_result = await self.daily_auth()
        if not auth_result:
            await update.message.reply_text(
                "❌ Ошибка авторизации! Проверьте настройки."
            )
            self.is_running = False
            return

        # Настройка планировщика
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
            # Получаем список кампусов для проверки
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
                # Показываем доступные кампусы
                campuses_text = "📋 Доступные кампусы:\n"
                for i, campus in enumerate(
                    campuses[:10], 1
                ):  # Ограничим вывод
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

        # Создаем новую конфигурацию, сохраняя только admin_chat_id
        admin_chat_id = self.config_manager.config["admin_chat_id"]
        self.config_manager.config = {
            "platform_login": "",
            "platform_password": "",
            "school_id": "",
            "campus_name": "",
            "admin_chat_id": admin_chat_id,
            "is_configured": False,
            "last_update": datetime.now(),
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
            # Проверяем актуальность токена
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
                # Пробуем отправить с MarkdownV2
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
                    # Пробуем HTML разметку
                    try:
                        html_message = self.format_notification_html(
                            notification
                        )
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
                        # Отправляем без форматирования
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

    def clean_html(self, text: str) -> str:
        """Очистка HTML тегов из текста"""
        import re

        clean = re.compile("<.*?>")
        return re.sub(clean, "", text).replace("&nbsp;", " ")

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Отмена операции"""
        await update.message.reply_text(
            "Операция отменена.", reply_markup=self.get_main_menu_keyboard()
        )
        return BotStates.MAIN_MENU

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
        # Проверяем, настроен ли кампус
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
                # Используем универсальный метод отправки
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
                    # Пробуем отправить без форматирования
                    plain_text = self.format_notification_plain(
                        last_notification, 0
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

    def format_notification_message(self, notification: Dict) -> str:
        """Форматирование сообщения об уведомлении с правильным экранированием"""
        try:
            time_str = datetime.fromisoformat(
                notification["time"].replace("Z", "+00:00")
            ).strftime("%d.%m.%Y %H:%M")
            message_text = self.clean_html(notification["message"])

            # Экранируем все текстовые поля
            escaped_time = self.escape_markdown(time_str)
            escaped_type = self.escape_markdown(
                notification.get("groupName", "Неизвестно")
            )
            escaped_message = self.escape_markdown(message_text)
            escaped_id = self.escape_markdown(notification["id"])

            # Форматируем сообщение с использованием MarkdownV2
            formatted_message = f"""
    🔔 *Новое уведомление* 🔔

    📅 *Время:* {escaped_time}
    📋 *Тип:* {escaped_type}

    💬 *Сообщение:*
    {escaped_message}

    🆔 *ID:* `{escaped_id}`
            """

            return formatted_message.strip()

        except Exception as e:
            logger.error(f"Ошибка форматирования сообщения: {e}")
            # Возвращаем простой текст в случае ошибки
            return (
                f"🔔 Новое уведомление\n\n"
                f"Время: {notification.get('time', 'Неизвестно')}\n"
                f"Тип: {notification.get('groupName', 'Неизвестно')}\n\n"
                f"Сообщение: {notification.get('message', '')}"
            )

    def escape_markdown(self, text: str) -> str:
        """Экранирование специальных символов Markdown"""
        if not text:
            return ""

        # Символы, которые нужно экранировать в MarkdownV2
        escape_chars = r"_*[]()~`>#+-=|{}.!"
        escaped_text = ""

        for char in text:
            if char in escape_chars:
                escaped_text += "\\" + char
            else:
                escaped_text += char

        return escaped_text

    def run(self):
        """Синхронный запуск бота"""
        # Создаем приложение Telegram
        self.application = (
            Application.builder().token(self.telegram_token).build()
        )

        # Добавляем обработчики команд
        self.application.add_handler(
            CommandHandler("start", self.start_command)
        )
        self.application.add_handler(CommandHandler("stop", self.stop_command))
        self.application.add_handler(
            CommandHandler("status", self.status_command)
        )

        # Добавляем обработчики для кнопок главного меню
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

        # Добавляем обработчики для кнопок настроек
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

        # Обработчик для текстовых сообщений (для ввода логина, пароля)
        self.application.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND, self.handle_text_input
            )
        )

        # Запускаем бота
        logger.info("Запуск Telegram бота...")
        self.application.run_polling()


def main():
    """Основная функция"""
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")

    if not telegram_token:
        print("❌ Не установлена переменная окружения TELEGRAM_BOT_TOKEN")
        print("Установите токен: export TELEGRAM_BOT_TOKEN='your_bot_token'")
        sys.exit(1)

    # Создаем и запускаем бота
    bot = TelegramSchoolNotifier(telegram_token=telegram_token)
    bot.run()


if __name__ == "__main__":
    main()
