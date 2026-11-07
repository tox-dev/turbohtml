"""Keep the fallback adapters on the same ASCII whitespace policy."""

from __future__ import annotations

import re
from typing import Final

PRESERVE_TAGS: Final = frozenset({
    "pre",
    "textarea",
    "listing",
    "title",
    "script",
    "style",
    "xmp",
    "iframe",
    "noembed",
    "noframes",
    "plaintext",
    "svg",
    "math",
})
SPACE_RUN: Final = re.compile(r"[ \t\n\f\r]+")

__all__ = [
    "PRESERVE_TAGS",
    "SPACE_RUN",
]
