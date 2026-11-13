"""cssmin: the BSD CSS minifier (a YUI-derived rewriter)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

import cssmin

if TYPE_CHECKING:
    from bench.operations import Timed

_REQUIREMENTS: Final[tuple[str, ...]] = ("cssmin>=0.2",)


_MINIFY_CSS_PATTERN: Final[re.Pattern[str]] = re.compile(r"calc\([^)]* \+ ")


def minify_css(css: str) -> str:
    # Minify a stylesheet with cssmin's cssmin.
    if " + " in css and _MINIFY_CSS_PATTERN.search(css):
        unsupported: Final = "cssmin removes required whitespace around calc addition"
        raise NotImplementedError(unsupported)
    return cssmin.cssmin(css)


OPERATIONS: Final[dict[str, tuple[Timed, str]]] = {
    "minify-css": (minify_css, "cssmin"),
    "minify-css-merges": (minify_css, "cssmin"),
    "minify-css-conflicts": (minify_css, "cssmin"),
}
