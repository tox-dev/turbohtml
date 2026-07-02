"""jsmin: the Python port of Crockford's jsmin, a character-level state machine."""

from __future__ import annotations

from jsmin import jsmin

REQUIREMENTS = ("jsmin>=3.0",)


def minify_js(source: str) -> None:
    """Minify JavaScript with jsmin's Crockford-style character state machine."""
    jsmin(source)


OPERATIONS = {"minify-js": (minify_js, "jsmin")}
