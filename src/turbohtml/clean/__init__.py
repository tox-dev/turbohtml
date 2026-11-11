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

from ._linkify import (
    DEFAULT_CALLBACKS,
    DEFAULT_PHONE_LABELS,
    Callback,
    LinkCandidate,
    LinkDetector,
    Linker,
    Linkify,
    LinkSpan,
    PhoneFormat,
    PhoneGrouping,
    PhoneNumber,
    PhoneNumbers,
    PhoneType,
    linkify,
    linkify_node,
    nofollow,
    target_blank,
)

# Minifier implementations live with rendering because both register types in the native core.
from ._native import (
    CSSMinify,
    JSMinify,
    Minify,
    collapse_whitespace_node,
    minify,
    minify_css,
    minify_css_inline,
    minify_js,
    strip_comments_node,
    transform_node,
)
from ._sanitizer import (
    DEFAULT_ATTRIBUTES,
    DEFAULT_CSS_PROPERTIES,
    DEFAULT_SCHEMES,
    DEFAULT_TAGS,
    OnDisallowed,
    Policy,
    Removed,
    Sanitizer,
    Transform,
    sanitize,
    sanitize_node,
    sanitize_report,
    sanitize_report_node,
)

__all__ = [
    "DEFAULT_ATTRIBUTES",
    "DEFAULT_CALLBACKS",
    "DEFAULT_CSS_PROPERTIES",
    "DEFAULT_PHONE_LABELS",
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
    "PhoneFormat",
    "PhoneGrouping",
    "PhoneNumber",
    "PhoneNumbers",
    "PhoneType",
    "Policy",
    "Removed",
    "Sanitizer",
    "Transform",
    "collapse_whitespace_node",
    "linkify",
    "linkify_node",
    "minify",
    "minify_css",
    "minify_css_inline",
    "minify_js",
    "nofollow",
    "sanitize",
    "sanitize_node",
    "sanitize_report",
    "sanitize_report_node",
    "strip_comments_node",
    "target_blank",
    "transform_node",
]
