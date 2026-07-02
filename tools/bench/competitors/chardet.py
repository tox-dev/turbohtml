"""chardet: the pure-Python universal character encoding detector."""

from __future__ import annotations

import chardet

REQUIREMENTS = ("chardet>=5.2",)


def encoding(data: bytes) -> None:
    """Detect a byte stream's character encoding with chardet's prober ensemble."""
    chardet.detect(data)


OPERATIONS = {"encoding": (encoding, "chardet")}
