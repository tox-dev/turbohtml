"""htmlmin: a pure-Python regex/parser minifier (the maintained htmlmin2 fork runs on current Pythons)."""

from __future__ import annotations

import htmlmin

REQUIREMENTS = ("htmlmin2>=0.1.13",)


def minify(text: str) -> None:
    """Minify with the whitespace and comment folds comparable to turbohtml's Minify defaults."""
    htmlmin.minify(text, remove_comments=True, remove_empty_space=True)


OPERATIONS = {"minify": (minify, "htmlmin")}
