"""nh3: the Rust ammonia binding."""

from __future__ import annotations

import nh3

REQUIREMENTS = ("nh3>=0.3.6",)


def sanitize(text: str) -> None:
    """Sanitize with nh3's allowlist clean."""
    nh3.clean(text)


def escape(text: str) -> None:
    """Escape text with nh3's clean_text, its HTML escaper (escapes more chars than turbohtml, same op class)."""
    nh3.clean_text(text)


OPERATIONS = {
    "sanitize": (sanitize, "nh3"),
    "escape": (escape, "nh3"),
}
