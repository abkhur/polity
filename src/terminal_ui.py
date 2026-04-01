"""Helpers for portable terminal output."""

from __future__ import annotations

import sys
from typing import TextIO


def supports_unicode(stream: TextIO | None = None) -> bool:
    target = stream or sys.stdout
    encoding = getattr(target, "encoding", None)
    if not encoding:
        return False

    try:
        "═│—±".encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return False
    return True


def glyphs(stream: TextIO | None = None) -> dict[str, str]:
    if supports_unicode(stream):
        return {
            "separator": "═" * 64,
            "title_dash": "—",
            "column": "│",
            "plus_minus": "±",
            "missing": "—",
        }

    return {
        "separator": "=" * 64,
        "title_dash": "-",
        "column": "|",
        "plus_minus": "+/-",
        "missing": "-",
    }
