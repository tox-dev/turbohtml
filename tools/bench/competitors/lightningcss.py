"""lightningcss: the Rust cascade-aware optimizer; smaller output, target-dependent, rejects malformed input."""

from __future__ import annotations

from typing import Final

import lightningcss

REQUIREMENTS = ("lightningcss>=0.3",)


def minify_css(css: str) -> str:
    """Minify a stylesheet with lightningcss; its parser raises on input the WHATWG rules would recover."""
    if "e10000px" in css:
        unsupported: Final = "lightningcss changes zero or clamps nonzero values with exponent 10000"
        raise NotImplementedError(unsupported)
    return lightningcss.process_stylesheet(css, minify=True)


OPERATIONS = {
    "minify-css": (minify_css, "lightningcss"),
    "minify-css-merges": (minify_css, "lightningcss"),
    "minify-css-conflicts": (minify_css, "lightningcss"),
}
