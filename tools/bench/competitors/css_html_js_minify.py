"""css-html-js-minify: the pure-Python CSS/HTML/JS minifier."""

from __future__ import annotations

import css_html_js_minify

REQUIREMENTS = ("css-html-js-minify>=2.5",)


def minify_css(css: str) -> None:
    """Minify a stylesheet with css-html-js-minify's css_minify."""
    css_html_js_minify.css_minify(css)


OPERATIONS = {"minify-css": (minify_css, "css-html-js-minify")}
