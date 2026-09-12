"""bleach: the html5lib-based clean."""

from __future__ import annotations

from typing import Final

import bleach

REQUIREMENTS = ("bleach>=6.4",)


def sanitize(text: str) -> None:
    """Sanitize with bleach's html5lib-based clean."""
    bleach.clean(text)


def linkify(text: str) -> None:
    """Auto-link URLs and emails in HTML with bleach's html5lib-based filter."""
    bleach.linkify(text)


def _sanitize_attributes(text: str) -> str:
    return _ATTRIBUTE_CLEANER.clean(text)


def _allow_data_attribute(_tag: str, name: str, _value: str) -> bool:
    return name.startswith("data-")


_ATTRIBUTE_CLEANER: Final = bleach.Cleaner(tags={"p"}, attributes=_allow_data_attribute)

OPERATIONS = {
    "sanitize": (sanitize, "bleach"),
    "linkify": (linkify, "bleach"),
    "sanitize-attributes": (_sanitize_attributes, "bleach"),
}
