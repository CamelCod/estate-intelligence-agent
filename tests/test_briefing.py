"""
Tests for daily briefing format and word count validation.
"""

import re
from typing import List


def strip_markdown(text: str) -> str:
    """Remove markdown formatting for plain text comparison."""
    text = re.sub(r'\*\*', '', text)
    text = re.sub(r'#', '', text)
    text = re.sub(r'-', '', text)
    return text


def count_emoji(text: str) -> int:
    """Count emoji characters in text."""
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"  # emoticons
        "\U0001F300-\U0001F5FF"  # symbols & pictographs
        "\U0001F680-\U0001F6FF"  # transport & map symbols
        "\U0001F1E0-\U0001F1FF"  # flags
        "\U00002702-\U000027B0"
        "\U000024C2-\U0001F251"
        "]+",
        flags=re.UNICODE
    )
    return len(emoji_pattern.findall(text))


def validate_briefing(briefing_text: str) -> List[str]:
    """
    Validate a daily briefing against format rules.
    Returns list of error messages (empty = valid).
    """
    errors = []

    word_count = len(briefing_text.split())
    if word_count > 250:
        errors.append(f"Word count {word_count} exceeds 250 limit")

    has_markdown = any([
        '**' in briefing_text,
        '#' in briefing_text,
        '- ' in briefing_text and briefing_text.count('\n') < 3
    ])
    if has_markdown:
        errors.append("Briefing contains markdown (no **, #, or - symbols allowed)")

    emoji_count = count_emoji(briefing_text)
    if emoji_count > 3:
        errors.append(f"Emoji count {emoji_count} exceeds 3 limit")

    if '?' not in briefing_text:
        errors.append("Briefing must end with a question to encourage engagement")

    return errors


def test_strip_markdown():
    assert strip_markdown("**bold** and *italic*") == "bold and italic"
    assert strip_markdown("# Heading") == "Heading"


def test_count_emoji():
    assert count_emoji("Hello 👋 World 🌍") == 2
    assert count_emoji("No emoji here") == 0
    assert count_emoji("🏡") == 1


def test_validate_briefing_valid():
    valid_briefing = """Good evening, Ahmed. Here's your home briefing for Thursday, 3 April.

Khalid arrived at 7:02am and left at 7:08pm — all as expected. Today is not Mariam's scheduled day.

A delivery was made at 2:17pm. No other activity.

Tip: You can ask me anything — try 'Did anyone go into the garden today?'

Anything specific you'd like me to check from today? 🏡"""

    errors = validate_briefing(valid_briefing)
    assert errors == [], f"Expected valid briefing, got errors: {errors}"


def test_validate_briefing_word_count():
    long_briefing = "word " * 300  # 300 words
    errors = validate_briefing(long_briefing)
    assert any("exceeds 250" in e for e in errors)


def test_validate_briefing_emoji_count():
    emoji_briefing = "Test 🏡 🏡 🏡 🏡 end?"
    errors = validate_briefing(emoji_briefing)
    assert any("exceeds 3" in e for e in errors)


def test_validate_briefing_no_question():
    no_question = "Good evening. Everything is fine. No issues today."
    errors = validate_briefing(no_question)
    assert any("question" in e for e in errors)


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])