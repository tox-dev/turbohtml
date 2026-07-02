"""htmlmin: the pure-Python HTMLParser-based minifier (via the htmlmin2 fork; 0.1.12 imports the removed cgi module)."""

from __future__ import annotations

import htmlmin

REQUIREMENTS = ("htmlmin2>=0.1.13",)


def minify(text: str) -> None:
    """Minify with the folds htmlmin shares with turbohtml's Minify: collapse whitespace, drop comments and quotes."""
    htmlmin.minify(text, remove_comments=True, remove_optional_attribute_quotes=True)


OPERATIONS = {"minify": (minify, "htmlmin")}
