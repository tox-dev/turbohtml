"""csscompressor: the Python port of the YUI CSS compressor."""

from __future__ import annotations

from typing import Final

import csscompressor

_REQUIREMENTS: Final[tuple[str, ...]] = ("csscompressor>=0.9",)


def minify_css(css: str) -> str:
    # Minify a stylesheet with csscompressor's YUI-style compress.
    return csscompressor.compress(css)


OPERATIONS = {
    "minify-css": (minify_css, "csscompressor"),
    "minify-css-merges": (minify_css, "csscompressor"),
    "minify-css-conflicts": (minify_css, "csscompressor"),
}
