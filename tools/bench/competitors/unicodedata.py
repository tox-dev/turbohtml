"""unicodedata: the CPython standard library's Unicode normalization, the in-process normalize baseline."""

from __future__ import annotations

import unicodedata

REQUIREMENTS = ()


def normalize(text: str) -> None:
    """Normalize text to Unicode NFC with the stdlib unicodedata.normalize."""
    unicodedata.normalize("NFC", text)


OPERATIONS = {"normalize": (normalize, "unicodedata")}
