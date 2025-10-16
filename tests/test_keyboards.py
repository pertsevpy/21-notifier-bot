from bot.keyboards import Keyboards


def test_get_main_menu_keyboard():
    kb = Keyboards.get_main_menu_keyboard()
    assert "📊 Статус" in str(kb)  # Проверяем наличие кнопок


def test_get_campuses_keyboard():
    campuses = [
        {"fullName": "Campus1"},
        {"fullName": "Very Long Campus Name That Should Be Truncated"},
    ]
    kb = Keyboards.get_campuses_keyboard(campuses)
    assert "Campus1" in str(kb)
    assert "Very Long Campus Name That" in str(kb)  # Truncated


def test_get_available_timezones_keyboard():
    kb = Keyboards.get_available_timezones_keyboard()
    assert "(UTC+3)" in str(kb)
