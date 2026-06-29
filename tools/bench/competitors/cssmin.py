"""cssmin: the BSD CSS minifier (a YUI-derived rewriter)."""

from __future__ import annotations

import cssmin

REQUIREMENTS = ("cssmin>=0.2",)


def minify_css(css: str) -> None:
    """Minify a stylesheet with cssmin's cssmin."""
    cssmin.cssmin(css)


OPERATIONS = {"minify-css": (minify_css, "cssmin")}
