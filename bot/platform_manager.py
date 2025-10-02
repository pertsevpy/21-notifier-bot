"""Взаимодействие с платформой 21-school
(авторизация, получение кампусов и уведомлений)"""

import json
import logging
import os
from datetime import datetime, timedelta
from logging import Logger
from typing import Dict, List, Optional
from urllib.parse import parse_qs, urlparse

import requests
from selenium import webdriver
from selenium.common.exceptions import (
    TimeoutException,
    WebDriverException,
    SessionNotCreatedException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

from .config_manager import ConfigManager

logger: Logger = logging.getLogger(__name__)


class SchoolPlatformManager:
    """Менеджер для работы с платформой 21-school"""

    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        self.token = None
        self.token_expiry = None
        self.last_notification_ids = set()
        self.session = requests.Session()
        self.campuses = []

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
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=chrome_options)
            driver.execute_script(
                "Object.defineProperty(navigator, 'webdriver', "
                "{get: () => undefined})"
            )
            return driver
        except (
            WebDriverException,
            SessionNotCreatedException,
            TimeoutException,
        ) as e:
            logger.warning("Стандартный метод не сработал: %s", e)
            logger.info("Пробуем альтернативные методы...")
            return self._setup_driver_manual(chrome_options)

    def _setup_driver_manual(self, chrome_options):
        """Ручная настройка драйвера с поиском браузера"""
        import shutil

        possible_browsers = [
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/local/bin/chrome",
            "/opt/google/chrome/chrome",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/usr/local/bin/chromium",
            "/snap/bin/chromium",
            "/var/lib/flatpak/exports/bin/com.google.Chrome",
        ]

        found_browser = None
        for browser_path in possible_browsers:
            if os.path.exists(browser_path):
                found_browser = browser_path
                logger.info("Найден браузер: %s", browser_path)
                break

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
                            "Найден браузер через which: %s", browser_path
                        )
                        break
                except:
                    continue

        if found_browser:
            chrome_options.binary_location = found_browser
            logger.info("Используем браузер: %s", found_browser)
        else:
            logger.warning("Браузер не найден, пробуем без указания пути...")

        try:
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=chrome_options)
            driver.execute_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            return driver
        except Exception as e:
            logger.error("Ошибка при создании драйвера: %s", e)
            raise

    def login_via_api(
        self, login: str = None, password: str = None
    ) -> Optional[str]:
        """Авторизация через API Keycloak"""
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
            logger.error("❌ Ошибка API авторизации: %s", e)
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
            logger.error("❌ Ошибка парсинга JSON ответа API: %s", e)
            return None
        except Exception as e:
            logger.error("❌ Неожиданная ошибка при API авторизации: %s", e)
            return None

    def login_and_get_token(
        self, login: str = None, password: str = None
    ) -> Optional[str]:
        """Авторизация на платформе с приоритетом API, fallback на Selenium"""
        login = login or self.config_manager.config["platform_login"]
        password = password or self.config_manager.config["platform_password"]

        if not login or not password:
            logger.error("Логин или пароль не установлены")
            return None

        logger.info("🔄 Попытка авторизации через API...")
        api_token = self.login_via_api(login, password)

        if api_token:
            self.token = api_token
            self.token_expiry = datetime.now() + timedelta(hours=10)
            logger.info("✅ Авторизация через API успешна")
            return api_token

        logger.warning(
            "❌ API авторизация не удалась, пробуем через Selenium..."
        )
        selenium_token = self.login_via_selenium(login, password)

        if selenium_token:
            self.token = selenium_token
            self.token_expiry = datetime.now() + timedelta(hours=23)
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

            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.NAME, "username"))
            )

            username_field = driver.find_element(By.NAME, "username")
            password_field = driver.find_element(By.NAME, "password")

            username_field.clear()
            username_field.send_keys(login)
            password_field.clear()
            password_field.send_keys(password)

            password_field.submit()

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
            logger.error("❌ Ошибка при Selenium авторизации: %s", e)
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
            WebDriverWait(driver, timeout).until(
                lambda d: any(
                    [
                        self._has_token_in_url(d),
                        self._has_token_in_local_storage(d),
                        self._has_token_in_session_storage(d),
                        self._has_token_in_cookies(d),
                        self._is_dashboard_loaded(d),
                    ]
                )
            )

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
                    logger.info(
                        "Токен найден через %s", source_method.__name__
                    )
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
        """Проверяет, загрузилась ли главная страница"""
        try:
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
            logger.warning("Ошибка при чтении Local Storage: %s", e)
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
            logger.warning("Ошибка при чтении Session Storage: %s", e)
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
            logger.warning("Ошибка при чтении cookies: %s", e)
            return None

    def _extract_token_from_page_content(self, driver) -> Optional[str]:
        """Извлечение токена из содержимого страницы"""
        try:
            scripts = driver.find_elements(By.TAG_NAME, "script")
            for script in scripts:
                script_content = script.get_attribute("innerHTML")
                if script_content:
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
                "Ошибка при поиске токена в содержимом страницы: %s", e
            )
            return None

    def extract_token_from_url(self, url: str) -> Optional[str]:
        """Извлечение токена из URL"""
        parsed_url = urlparse(url)

        if parsed_url.fragment:
            fragment_params = parse_qs(parsed_url.fragment)
            for key in ["tokenId", "access_token", "id_token"]:
                token = fragment_params.get(key, [None])[0]
                if token:
                    return token

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

            logger.info("Получено %d кампусов", len(campuses))
            self.campuses = campuses
            return campuses

        except requests.exceptions.RequestException as e:
            logger.error("Ошибка при получении списка кампусов: %s", e)
            return None
        except json.JSONDecodeError as e:
            logger.error("Ошибка парсинга JSON кампусов: %s", e)
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
                logger.error("GraphQL ошибки: %s", data["errors"])
                return None

            notifications = (
                data.get("data", {})
                .get("s21Notification", {})
                .get("getS21Notifications", {})
                .get("notifications", [])
            )
            return notifications

        except requests.exceptions.RequestException as e:
            logger.error("Ошибка при запросе уведомлений: %s", e)
            return None
        except json.JSONDecodeError as e:
            logger.error("Ошибка парсинга JSON: %s", e)
            return None

    def get_new_notifications(self) -> List[Dict]:
        """Получение только новых уведомлений"""
        current_notifications = self.get_notifications()
        if not current_notifications:
            return []

        current_ids = {n["id"] for n in current_notifications}

        if not self.last_notification_ids:
            self.last_notification_ids = current_ids
            return []

        new_ids = current_ids - self.last_notification_ids
        new_notifications = [
            n for n in current_notifications if n["id"] in new_ids
        ]

        self.last_notification_ids = current_ids

        return new_notifications

    def get_last_notification(self) -> Optional[Dict]:
        """Получение последнего уведомления через GraphQL API"""
        if not self.token:
            logger.error("Токен не установлен для получения уведомлений")
            return None

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

        payload = {
            "operationName": "getUserNotifications",
            "variables": {"paging": {"offset": 0, "limit": 1}},
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
                    "GraphQL ошибки при запросе последнего уведомления: ",
                    data["errors"],
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
                    "Получено последнее уведомление: %s", last_notification["id"]
                )
                return last_notification
            else:
                logger.info("Уведомлений нет")
                return None

        except requests.exceptions.RequestException as e:
            logger.error("Ошибка при запросе последнего уведомления: %s", e)
            return None
        except json.JSONDecodeError as e:
            logger.error("Ошибка парсинга JSON последнего уведомления: %s", e)
            return None
