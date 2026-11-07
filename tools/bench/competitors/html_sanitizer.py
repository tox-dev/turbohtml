"""html-sanitizer: the allowlist Sanitizer over lxml."""

from __future__ import annotations

from typing import Final

import html_sanitizer

REQUIREMENTS: Final[tuple[str, ...]] = ("html-sanitizer>=2.6",)

_SANITIZER = html_sanitizer.Sanitizer()


def sanitize(text: str) -> None:
    _SANITIZER.sanitize(text)


OPERATIONS = {"sanitize": (sanitize, "html-sanitizer")}
