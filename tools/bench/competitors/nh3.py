"""nh3: the Rust ammonia binding."""

from __future__ import annotations

import nh3

REQUIREMENTS = ("nh3>=0.3.6",)


def sanitize(text: str) -> None:
    """Sanitize with nh3's allowlist clean."""
    nh3.clean(text)


OPERATIONS = {"sanitize": (sanitize, "nh3")}
