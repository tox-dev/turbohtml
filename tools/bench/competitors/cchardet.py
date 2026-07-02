"""cchardet via faust-cchardet: the C uchardet binding (the original cchardet stops building at Python 3.10)."""

from __future__ import annotations

import cchardet

REQUIREMENTS = ("faust-cchardet>=2.1.19",)


def encoding(data: bytes) -> None:
    """Detect a byte stream's character encoding with cchardet's uchardet engine."""
    cchardet.detect(data)


OPERATIONS = {"encoding": (encoding, "faust-cchardet")}
