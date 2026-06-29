"""csscompressor: the Python port of the YUI CSS compressor."""

from __future__ import annotations

import csscompressor

REQUIREMENTS = ("csscompressor>=0.9",)


def minify_css(css: str) -> None:
    """Minify a stylesheet with csscompressor's YUI-style compress."""
    csscompressor.compress(css)


OPERATIONS = {"minify-css": (minify_css, "csscompressor")}
