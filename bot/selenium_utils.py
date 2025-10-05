# bot/selenium_utils.py
"""Утилиты для работы с Selenium WebDriver"""

import logging
import os
import re
import shutil
from typing import Optional
from urllib.parse import parse_qs, urlparse

from selenium import webdriver
from selenium.common.exceptions import (
    TimeoutException,
    WebDriverException,
    SessionNotCreatedException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as ec
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

logger = logging.getLogger(__name__)


class SeleniumManager:
    """Менеджер для работы с Selenium WebDriver"""

    def __init__(self):
        pass

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
        found_browser = self._find_browser_path()
        if found_browser:
            chrome_options.binary_location = found_browser
            logger.info("Используем браузер: %s", found_browser)
        else:
            logger.warning("Браузер не найден, пробуем без указания пути...")

        return self._create_driver_with_retry(chrome_options)

    @staticmethod
    def _find_browser_path():
        """Поиск пути к браузеру в системе"""

        # Список возможных путей к браузерам
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

        # Поиск по прямым путям
        for browser_path in possible_browsers:
            if os.path.exists(browser_path):
                logger.info("Найден браузер: %s", browser_path)
                return browser_path

        # Поиск через which
        for browser_cmd in [
            "google-chrome",
            "google-chrome-stable",
            "chromium",
            "chromium-browser",
        ]:
            try:
                browser_path = shutil.which(browser_cmd)
                if browser_path:
                    logger.info("Найден браузер через which: %s", browser_path)
                    return browser_path
            except Exception:
                continue

        return None

    @staticmethod
    def _create_driver_with_retry(chrome_options):
        """Создание драйвера с обработкой ошибок"""
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

    def login_via_selenium(self, login: str, password: str) -> Optional[str]:
        """Авторизация через Selenium (fallback метод)"""
        driver = None
        try:
            driver = self.setup_driver(headless=True)
            logger.info("Запуск браузера для авторизации через Selenium...")

            driver.get("https://platform.21-school.ru")

            WebDriverWait(driver, 20).until(
                ec.presence_of_element_located((By.NAME, "username"))
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

    @staticmethod
    def _has_token_in_url(driver) -> bool:
        """Проверяет наличие токена в URL"""
        current_url = driver.current_url
        token_indicators = ["tokenId", "access_token", "id_token", "token"]
        return any(indicator in current_url for indicator in token_indicators)

    @staticmethod
    def _has_token_in_local_storage(driver) -> bool:
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
        except Exception:
            return False

    @staticmethod
    def _has_token_in_session_storage(driver) -> bool:
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
        except Exception:
            return False

    @staticmethod
    def _has_token_in_cookies(driver) -> bool:
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
        except Exception:
            return False

    @staticmethod
    def _is_dashboard_loaded(driver) -> bool:
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
                except Exception:
                    continue

            return False
        except Exception:
            return False

    @staticmethod
    def _extract_token_from_local_storage(driver) -> Optional[str]:
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

    @staticmethod
    def _extract_token_from_session_storage(driver) -> Optional[str]:
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

    @staticmethod
    def _extract_token_from_cookies(driver) -> Optional[str]:
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

    @staticmethod
    def _extract_token_from_page_content(driver) -> Optional[str]:
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

                        match = re.search(pattern, script_content)
                        if match:
                            return match.group(1)

            return None
        except Exception as e:
            logger.warning(
                "Ошибка при поиске токена в содержимом страницы: %s", e
            )
            return None

    @staticmethod
    def _extract_token_from_url(url: str) -> Optional[str]:
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
