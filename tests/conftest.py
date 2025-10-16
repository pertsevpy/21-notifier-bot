"""автоматически настраивать pytest"""

import os
import sys
from unittest.mock import patch

import pytest

# Добавляем корень проекта в sys.path
sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
)

from bot.config_manager import ConfigManager


@pytest.fixture
def temp_config_file(tmp_path):
    """Фикстура для временного config файла"""
    config_file = tmp_path / "bot_config.json"
    yield str(config_file)
    if os.path.exists(config_file):
        os.remove(config_file)


@pytest.fixture
def mock_config_manager(temp_config_file):
    """Mock ConfigManager с temp файлом"""
    with patch("bot.config_manager.os.getenv", return_value="test_password"):
        cm = ConfigManager(config_file=temp_config_file)
        yield cm


@pytest.fixture
def mock_requests_post():
    """Mock для requests.post"""
    with patch("requests.Session.post") as mock_post:
        yield mock_post
