"""css-html-js-minify: the pure-Python CSS/HTML/JS minifier."""

from __future__ import annotations

import css_html_js_minify

REQUIREMENTS = ("css-html-js-minify>=2.5",)


def minify_css(css: str) -> str:
    """Minify a stylesheet with css-html-js-minify's css_minify."""
    return css_html_js_minify.css_minify(css)


def minify(text: str) -> str:
    """Minify an HTML document with css-html-js-minify's html_minify."""
    return css_html_js_minify.html_minify(text)


def minify_js(source: str) -> str:
    """Minify a JavaScript source with css-html-js-minify's js_minify."""
    return css_html_js_minify.js_minify(source)


OPERATIONS = {
    "minify-css": (minify_css, "css-html-js-minify"),
    "minify": (minify, "css-html-js-minify"),
    "minify-js": (minify_js, "css-html-js-minify"),
}
