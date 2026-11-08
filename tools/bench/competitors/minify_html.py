"""minify-html: the Rust minify-html (formerly hyperbuild) binding."""

from __future__ import annotations

from typing import Final

import minify_html

_REQUIREMENTS: Final[tuple[str, ...]] = ("minify-html>=0.18.1",)


def minify(text: str) -> str:
    # Minify with the folds turbohtml's Minify performs, leaving CSS/JS untouched for a like-for-like comparison.
    return minify_html.minify(text, keep_comments=False, minify_css=False, minify_js=False)


OPERATIONS = {"minify": (minify, "minify-html")}
