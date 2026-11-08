"""csscompressor: the Python port of the YUI CSS compressor."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import csscompressor

if TYPE_CHECKING:
    from bench.operations import Timed

_REQUIREMENTS: Final[tuple[str, ...]] = ("csscompressor>=0.9",)


def minify_css(css: str) -> str:
    # Minify a stylesheet with csscompressor's YUI-style compress.
    return csscompressor.compress(css)


OPERATIONS: Final[dict[str, tuple[Timed, str]]] = {
    "minify-css": (minify_css, "csscompressor"),
    "minify-css-merges": (minify_css, "csscompressor"),
    "minify-css-conflicts": (minify_css, "csscompressor"),
}
