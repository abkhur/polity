"""Tests for portable terminal glyph helpers."""

from src.terminal_ui import glyphs, supports_unicode


class _FakeStream:
    def __init__(self, encoding: str | None) -> None:
        self.encoding = encoding


def test_supports_unicode_for_utf8_stream() -> None:
    assert supports_unicode(_FakeStream("utf-8")) is True


def test_supports_unicode_for_ascii_stream() -> None:
    assert supports_unicode(_FakeStream("ascii")) is False


def test_glyphs_fallback_to_ascii() -> None:
    ui = glyphs(_FakeStream("ascii"))
    assert ui["separator"].startswith("=")
    assert ui["column"] == "|"
    assert ui["plus_minus"] == "+/-"
