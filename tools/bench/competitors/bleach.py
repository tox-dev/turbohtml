"""bleach: the html5lib-based clean."""

from __future__ import annotations

import bleach

REQUIREMENTS = ("bleach>=6.4",)


def sanitize(text: str) -> None:
    """Sanitize with bleach's html5lib-based clean."""
    bleach.clean(text)


OPERATIONS = {"sanitize": (sanitize, "bleach")}
