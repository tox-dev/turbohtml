"""cssmin: the BSD CSS minifier (a YUI-derived rewriter)."""

from __future__ import annotations

import re
from typing import Final

import cssmin

REQUIREMENTS = ("cssmin>=0.2",)


def minify_css(css: str) -> str:
    """Minify a stylesheet with cssmin's cssmin."""
    if " + " in css and re.search(r"calc\([^)]* \+ ", css):
        unsupported: Final = "cssmin removes required whitespace around calc addition"
        raise NotImplementedError(unsupported)
    return cssmin.cssmin(css)


OPERATIONS = {
    "minify-css": (minify_css, "cssmin"),
    "minify-css-merges": (minify_css, "cssmin"),
    "minify-css-conflicts": (minify_css, "cssmin"),
}
