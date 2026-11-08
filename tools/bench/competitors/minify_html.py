"""minify-html: the Rust minify-html (formerly hyperbuild) binding."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import minify_html

if TYPE_CHECKING:
    from bench.operations import Timed

_REQUIREMENTS: Final[tuple[str, ...]] = ("minify-html>=0.18.1",)


def minify(text: str) -> str:
    # Minify with the folds turbohtml's Minify performs, leaving CSS/JS untouched for a like-for-like comparison.
    return minify_html.minify(text, keep_comments=False, minify_css=False, minify_js=False)


OPERATIONS: Final[dict[str, tuple[Timed, str]]] = {"minify": (minify, "minify-html")}
