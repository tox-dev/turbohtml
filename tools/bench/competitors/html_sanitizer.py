"""html-sanitizer: the allowlist Sanitizer over lxml."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import html_sanitizer

if TYPE_CHECKING:
    from bench.operations import Timed

_REQUIREMENTS: Final[tuple[str, ...]] = ("html-sanitizer>=2.6",)

_SANITIZER: Final[html_sanitizer.Sanitizer] = html_sanitizer.Sanitizer()


def sanitize(text: str) -> None:
    _SANITIZER.sanitize(text)


OPERATIONS: Final[dict[str, tuple[Timed, str]]] = {"sanitize": (sanitize, "html-sanitizer")}
