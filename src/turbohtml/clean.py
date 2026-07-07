"""
turbohtml.clean: scrub and tidy HTML -- sanitize against an allowlist, auto-link plain URLs, and minify.

The sanitizer replaces `bleach <https://github.com/mozilla/bleach>`__/``nh3``/``html-sanitizer``, the linkifier
replaces ``bleach.linkify``/``linkify-it-py``, ``minify`` replaces ``minify-html``/``htmlmin``, the CSS minifier
(:func:`minify_css`) replaces ``rcssmin``/``csscompressor``/``cssmin``, and the JavaScript minifier (:func:`minify_js`)
replaces ``jsmin``/``rjsmin``. The three minifiers sit together here: ``minify`` for a whole HTML document,
:func:`minify_css` for a stylesheet, and :func:`minify_js` for a script. Every configurable entry point takes a frozen,
thread-safe config object (:class:`Policy`, :class:`Linkify`, :class:`Minify`, :class:`CSSMinify`, :class:`JSMinify`).
"""

from __future__ import annotations

from ._cssmin import (
    CSSMinify,
    minify_css,
    minify_css_inline,
)
from ._html import Minify
from ._linkify import (
    DEFAULT_CALLBACKS,
    Callback,
    LinkCandidate,
    LinkDetector,
    Linker,
    Linkify,
    LinkSpan,
    linkify,
    nofollow,
    target_blank,
)
from ._minify import JSMinify, minify, minify_js
from ._sanitizer import (
    DEFAULT_ATTRIBUTES,
    DEFAULT_CSS_PROPERTIES,
    DEFAULT_SCHEMES,
    DEFAULT_TAGS,
    OnDisallowed,
    Policy,
    Removed,
    Sanitizer,
    sanitize,
    sanitize_report,
)

__all__ = [
    "DEFAULT_ATTRIBUTES",
    "DEFAULT_CALLBACKS",
    "DEFAULT_CSS_PROPERTIES",
    "DEFAULT_SCHEMES",
    "DEFAULT_TAGS",
    "CSSMinify",
    "Callback",
    "JSMinify",
    "LinkCandidate",
    "LinkDetector",
    "LinkSpan",
    "Linker",
    "Linkify",
    "Minify",
    "OnDisallowed",
    "Policy",
    "Removed",
    "Sanitizer",
    "linkify",
    "minify",
    "minify_css",
    "minify_css_inline",
    "minify_js",
    "nofollow",
    "sanitize",
    "sanitize_report",
    "target_blank",
]
