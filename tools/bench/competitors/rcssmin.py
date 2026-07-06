"""rcssmin: the non-destructive C/Python whitespace-and-comment stripper."""

from __future__ import annotations

import rcssmin

REQUIREMENTS = ("rcssmin>=1.2",)


def minify_css(css: str) -> str:
    """Minify a stylesheet with rcssmin's non-destructive cssmin."""
    return rcssmin.cssmin(css)


OPERATIONS = {"minify-css": (minify_css, "rcssmin")}
