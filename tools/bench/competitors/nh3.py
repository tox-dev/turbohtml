"""nh3: the Rust ammonia binding."""

from __future__ import annotations

from typing import Final

import nh3

REQUIREMENTS = ("nh3>=0.3.6",)


def sanitize(text: str) -> None:
    """Sanitize with nh3's allowlist clean."""
    nh3.clean(text)


def escape(text: str) -> None:
    """Escape text with nh3's clean_text, its HTML escaper (escapes more chars than turbohtml, same op class)."""
    nh3.clean_text(text)


def _sanitize_attributes(text: str) -> str:
    return _ATTRIBUTE_CLEANER.clean(text)


_ATTRIBUTE_CLEANER: Final = nh3.Cleaner(tags={"p"}, attributes={}, generic_attribute_prefixes={"data-"})

OPERATIONS = {
    "sanitize": (sanitize, "nh3"),
    "escape": (escape, "nh3"),
    "sanitize-attributes": (_sanitize_attributes, "nh3"),
}
