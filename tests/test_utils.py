from bot.utils import clean_html, escape_markdown


def test_clean_html():
    assert clean_html("<p>Hello</p>") == "Hello"
    assert clean_html("Test&nbsp;123") == "Test 123"


def test_escape_markdown():
    assert escape_markdown("Hello_World!") == "Hello\\_World\\!"
