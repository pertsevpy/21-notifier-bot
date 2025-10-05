"""Взаимодействие с платформой 21-school
(авторизация, получение кампусов и уведомлений)"""

import json
import logging
from datetime import datetime, timedelta
from logging import Logger
from typing import Dict, List, Optional

import requests
import requests.adapters

from bot.config_manager import ConfigManager
from bot.selenium_utils import SeleniumManager

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
        self.selenium_manager = SeleniumManager()

        retry_strategy = requests.adapters.Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = requests.adapters.HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def login_via_api(
        self, login: str = None, password: str = None
    ) -> Optional[str]:
        """Авторизация через API Keycloak"""
        login, password = self._get_credentials(login, password)
        if not login or not password:
            return None

        return self._perform_api_authentication(login, password)

    def _get_credentials(self, login: str, password: str) -> tuple:
        """Получение и валидация учетных данных"""
        login = login or self.config_manager.config["platform_login"]
        password = password or self.config_manager.config["platform_password"]

        if not login or not password:
            logger.error("Логин или пароль не установлены для API авторизации")
            return None, None

        return login, password

    def _perform_api_authentication(
        self, login: str, password: str
    ) -> Optional[str]:
        """Выполнение аутентификации через API"""
        url, payload, headers = self._prepare_api_request(login, password)

        try:
            logger.info("Попытка авторизации через API...")
            response = self.session.post(
                url, headers=headers, data=payload, timeout=15
            )
            return self._handle_api_response(response)
        except requests.exceptions.RequestException as e:
            self._handle_request_exception(e)
            return None
        except json.JSONDecodeError as e:
            logger.error("❌ Ошибка парсинга JSON ответа API: %s", e)
            return None
        except Exception as e:
            logger.error("❌ Неожиданная ошибка при API авторизации: %s", e)
            return None

    @staticmethod
    def _prepare_api_request(login: str, password: str) -> tuple:
        """Подготовка запроса для API аутентификации"""
        # pylint: disable-next=line-too-long
        url = "https://auth.21-school.ru/auth/realms/EduPowerKeycloak/protocol/openid-connect/token"

        payload = {
            "client_id": "s21-open-api",
            "username": login,
            "password": password,
            "grant_type": "password",
        }

        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        return url, payload, headers

    @staticmethod
    def _handle_api_response(response) -> Optional[str]:
        """Обработка ответа от API"""
        response.raise_for_status()
        data = response.json()
        access_token = data.get("access_token")

        if access_token:
            logger.info("✅ Токен успешно получен через API")
            return access_token

        logger.error("❌ Токен не найден в ответе API")
        return None

    def _handle_request_exception(self, e: Exception):
        """Обработка исключений при запросе"""
        logger.error("❌ Ошибка API авторизации: %s", e)

        if hasattr(e, "response") and e.response is not None:
            self._handle_http_error(e.response.status_code)

    @staticmethod
    def _handle_http_error(status_code: int):
        """Обработка HTTP ошибок"""
        error_messages = {
            401: "Неверный логин или пароль",
            400: "Неверный запрос к API",
        }

        if status_code in error_messages:
            logger.error(error_messages[status_code])
        elif status_code >= 500:
            logger.error("Проблемы на сервере авторизации")

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
        return self.selenium_manager.login_via_selenium(login, password)

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
        except (
            requests.exceptions.RequestException,
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
            requests.exceptions.HTTPError,
        ) as e:
            logger.debug("Ошибка при проверке токена: %s", e)
            return False

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
                    "GraphQL ошибки при запросе последнего уведомления: %s",
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
                    "Получено последнее уведомление: %s",
                    last_notification["id"],
                )
                return last_notification

            logger.info("Уведомлений нет")
            return None

        except requests.exceptions.RequestException as e:
            logger.error("Ошибка при запросе последнего уведомления: %s", e)
            return None
        except json.JSONDecodeError as e:
            logger.error("Ошибка парсинга JSON последнего уведомления: %s", e)
            return None
