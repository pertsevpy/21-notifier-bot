import os
import json

from bot.config_manager import ConfigManager


def test_load_config_default(mock_config_manager):
    cm = mock_config_manager
    assert cm.config["platform_login"] == ""
    assert cm.config["timezone"] == "UTC+3"
    assert not cm.config["is_configured"]


def test_save_config(temp_config_file):
    cm = ConfigManager(config_file=temp_config_file)
    cm.update_setting("platform_login", "test_login")
    cm.save_config()
    with open(temp_config_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["platform_login"] == "test_login"
    assert "platform_password" not in data  # Не сохраняем пароль


def test_update_setting_password(temp_config_file):
    cm = ConfigManager(config_file=temp_config_file)
    cm.update_setting("platform_password", "new_pass")
    assert os.environ.get("PLATFORM_PASSWORD") == "new_pass"  # В runtime env


def test_get_config_status(mock_config_manager):
    cm = mock_config_manager
    is_complete, missing = cm.get_config_status()
    assert not is_complete
    assert "логин" in missing

    cm.update_setting("platform_login", "login")
    cm.update_setting("school_id", "123")
    cm.update_setting("admin_chat_id", "456")
    is_complete, missing = cm.get_config_status()
    assert is_complete
    assert not missing


def test_invalid_timezone(mock_config_manager):
    cm = mock_config_manager
    cm.update_setting("timezone", "Invalid/TZ")  # Не меняет, log error
    assert cm.config["timezone"] == "UTC+3"  # Остается default
