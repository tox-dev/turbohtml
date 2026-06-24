"""html-sanitizer: the allowlist Sanitizer over lxml."""

from __future__ import annotations

import html_sanitizer

REQUIREMENTS = ("html-sanitizer>=2.6",)

_SANITIZER = html_sanitizer.Sanitizer()


def sanitize(text: str) -> None:
    """Sanitize with html-sanitizer's allowlist Sanitizer."""
    _SANITIZER.sanitize(text)


OPERATIONS = {"sanitize": (sanitize, "html-sanitizer")}
