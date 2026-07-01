"""
turbohtml.clean: scrub and tidy HTML -- sanitize against an allowlist, auto-link plain URLs, and minify.

The sanitizer replaces `bleach <https://github.com/mozilla/bleach>`__/``nh3``/``html-sanitizer``, the linkifier
replaces ``bleach.linkify``/``linkify-it-py``, ``minify`` replaces ``minify-html``/``htmlmin``, and the CSS minifier
(:func:`minify_css`) replaces ``rcssmin``/``csscompressor``/``cssmin``. Every configurable entry point takes a
frozen, thread-safe config object (:class:`Policy`, :class:`Linkify`, :class:`Minify`).
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
    Detector,
    Link,
    Linker,
    Linkify,
    LinkSpan,
    linkify,
    nofollow,
    target_blank,
)
from ._minify import minify
from ._sanitizer import (
    DEFAULT_ATTRIBUTES,
    DEFAULT_CSS_PROPERTIES,
    DEFAULT_SCHEMES,
    DEFAULT_TAGS,
    OnDisallowed,
    Policy,
    Sanitizer,
    sanitize,
)

__all__ = [
    "DEFAULT_ATTRIBUTES",
    "DEFAULT_CALLBACKS",
    "DEFAULT_CSS_PROPERTIES",
    "DEFAULT_SCHEMES",
    "DEFAULT_TAGS",
    "CSSMinify",
    "Callback",
    "Detector",
    "Link",
    "LinkSpan",
    "Linker",
    "Linkify",
    "Minify",
    "OnDisallowed",
    "Policy",
    "Sanitizer",
    "linkify",
    "minify",
    "minify_css",
    "minify_css_inline",
    "nofollow",
    "sanitize",
    "target_blank",
]
