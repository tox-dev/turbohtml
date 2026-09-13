"""rjsmin: a fast regex-substitution JavaScript minifier (whitespace and comments only)."""

from __future__ import annotations

import rjsmin

REQUIREMENTS = ("rjsmin>=1.2",)


def minify_js(source: str) -> str:
    """Minify JavaScript with rjsmin's single regex-substitution pass."""
    return rjsmin.jsmin(source)


OPERATIONS = {
    "minify-js-guards": (minify_js, "rjsmin"),
    "minify-js-propagation": (minify_js, "rjsmin"),
    "minify-js-var-initialization": (minify_js, "rjsmin"),
    "minify-js-unused-declarations": (minify_js, "rjsmin"),
    "minify-js-unlink": (minify_js, "rjsmin"),
    "minify-js-single-use": (minify_js, "rjsmin"),
    "minify-js-sequences": (minify_js, "rjsmin"),
    "minify-js": (minify_js, "rjsmin"),
    "minify-js-names": (minify_js, "rjsmin"),
}
