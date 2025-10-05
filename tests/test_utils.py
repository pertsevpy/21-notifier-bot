"""Тесты для вспомогательных функций"""

import pytest

from bot.utils import clean_html, escape_markdown, convert_utc_to_local


def test_clean_html():
    """Тест очистки HTML тегов"""
    # Базовые случаи
    assert clean_html("<p>Hello</p>") == "Hello"
    assert clean_html("Test&nbsp;123") == "Test 123"

    # Сложные случаи
    assert clean_html("<div><span>Text</span></div>") == "Text"
    assert clean_html("Before<br>After") == "BeforeAfter"
    assert clean_html("A &nbsp; B &nbsp; C") == "A   B   C"

    # Крайние случаи
    assert clean_html("") == ""
    assert clean_html("No tags here") == "No tags here"
    assert clean_html("<script>alert('test')</script>") == "alert('test')"


def test_escape_markdown():
    """Тест экранирования Markdown символов"""
    # Базовые символы
    assert escape_markdown("Hello_World!") == "Hello\\_World\\!"
    assert (
        escape_markdown("Text *with* **markdown**")
        == "Text \\*with\\* \\*\\*markdown\\*\\*"
    )

    # Все специальные символы
    test_text = "_*[]()~`>#+-=|{}.!"
    expected = "\\_\\*\\[\\]\\(\\)\\~\\`\\>\\#\\+\\-\\=\\|\\{\\}\\.\\!"
    assert escape_markdown(test_text) == expected

    # Крайние случаи
    assert escape_markdown("") == ""
    assert escape_markdown("Simple text") == "Simple text"
    assert escape_markdown(None) == ""


def test_convert_utc_to_local_valid():
    """Тест преобразования времени с валидными данными"""
    # Тест с Moscow timezone
    utc_time = "2023-10-05T12:00:00Z"
    result = convert_utc_to_local(utc_time, "Europe/Moscow")
    # Moscow UTC+3, поэтому 12:00 UTC = 15:00 Moscow
    assert "15:00" in result
    assert "05.10.2023" in result

    # Тест с UTC
    result_utc = convert_utc_to_local(utc_time, "UTC")
    assert "12:00" in result_utc
    assert "(UTC)" in result_utc


def test_convert_utc_to_local_edge_cases():
    """Тест крайних случаев преобразования времени"""
    # Пустая строка времени
    assert convert_utc_to_local("", "Europe/Moscow") == "Unknown time"

    # Пустой часовой пояс (должен использовать UTC по умолчанию)
    result = convert_utc_to_local("2023-10-05T12:00:00Z", "")
    assert "(UTC)" in result

    # None значения
    assert convert_utc_to_local(None, "Europe/Moscow") == "Unknown time"
    assert (
        convert_utc_to_local("2023-10-05T12:00:00Z", None)
        == "05.10.2023 12:00 (UTC)"
    )


def test_convert_utc_to_local_invalid():
    """Тест обработки невалидных данных"""
    # Неправильный формат времени
    result = convert_utc_to_local("invalid-time", "Europe/Moscow")
    assert "invalid-time" in result
    assert "error" in result.lower()

    # Неправильный часовой пояс
    result = convert_utc_to_local("2023-10-05T12:00:00Z", "Invalid/Timezone")
    # Должен упасть обратно на UTC
    assert "(UTC)" in result


def test_convert_utc_to_local_different_formats():
    """Тест различных форматов времени"""
    # Формат с Z
    result1 = convert_utc_to_local("2023-10-05T12:00:00Z", "Europe/Moscow")

    # Формат без Z (должен обрабатываться корректно)
    result2 = convert_utc_to_local(
        "2023-10-05T12:00:00+00:00", "Europe/Moscow"
    )

    # Оба должны давать одинаковое локальное время
    assert result1 == result2


@pytest.mark.parametrize(
    "timezone,expected_tz",
    [
        ("Europe/London", "(BST)"),
        ("America/New_York", "(EDT)"),
        ("Asia/Tokyo", "(JST)"),
    ],
)
def test_convert_utc_to_local_different_timezones(timezone, expected_tz):
    """Параметризованный тест для разных часовых поясов"""
    result = convert_utc_to_local("2023-10-05T12:00:00Z", timezone)
    assert expected_tz in result


def test_convert_utc_to_local_leap_second():
    """Тест обработки високосной секунды (если применимо)"""
    # Обычное время должно обрабатываться нормально
    result = convert_utc_to_local("2023-06-30T23:59:60Z", "UTC")
    # Проверяем, что не возникает исключение и возвращается какой-то результат
    assert result is not None
