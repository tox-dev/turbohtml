"""unicodedata: the CPython standard library's Unicode normalization, the in-process normalize baseline."""

from __future__ import annotations

import unicodedata
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from bench.operations import Timed

_REQUIREMENTS: Final[tuple[()]] = ()


def normalize(text: str) -> None:
    # Normalize text to Unicode NFC with the stdlib unicodedata.normalize.
    unicodedata.normalize("NFC", text)


OPERATIONS: Final[dict[str, tuple[Timed, str]]] = {
    "normalize": (normalize, "unicodedata"),
    "normalize-marks": (normalize, "unicodedata"),
}


__all__ = [
    "normalize",
]
