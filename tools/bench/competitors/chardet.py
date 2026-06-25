"""chardet: the pure-Python Universal Encoding Detector (~189M downloads/month)."""

from __future__ import annotations

import chardet

REQUIREMENTS = ("chardet>=5.2",)


def encoding(data: bytes) -> None:
    """Detect a byte stream's encoding with chardet's prober chain."""
    chardet.detect(data)


OPERATIONS = {"encoding": (encoding, "chardet")}
