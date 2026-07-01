"""lightningcss: the Rust cascade-aware optimizer; smaller output, target-dependent, rejects malformed input."""

from __future__ import annotations

import lightningcss

REQUIREMENTS = ("lightningcss>=0.3",)


def minify_css(css: str) -> None:
    """Minify a stylesheet with lightningcss; its parser raises on input the WHATWG rules would recover."""
    lightningcss.process_stylesheet(css, minify=True)


OPERATIONS = {"minify-css": (minify_css, "lightningcss")}
