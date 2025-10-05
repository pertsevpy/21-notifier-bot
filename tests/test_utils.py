<<<<<<< HEAD
from bot.utils import clean_html, escape_markdown
=======
"""Заглушка для utils"""

from bot.common.utils import clean_html, escape_markdown
>>>>>>> cb264a7 (Working with Selenium is moved to file selenium_utils.py)


def test_clean_html():
    assert clean_html("<p>Hello</p>") == "Hello"
    assert clean_html("Test&nbsp;123") == "Test 123"


def test_escape_markdown():
    assert escape_markdown("Hello_World!") == "Hello\\_World\\!"
