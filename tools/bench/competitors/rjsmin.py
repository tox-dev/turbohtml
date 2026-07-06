"""rjsmin: a fast regex-substitution JavaScript minifier (whitespace and comments only)."""

from __future__ import annotations

import rjsmin

REQUIREMENTS = ("rjsmin>=1.2",)


def minify_js(source: str) -> str:
    """Minify JavaScript with rjsmin's single regex-substitution pass."""
    return rjsmin.jsmin(source)


OPERATIONS = {"minify-js": (minify_js, "rjsmin")}
