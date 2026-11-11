"""Native functions shared by the internal helpers."""

from __future__ import annotations

from turbohtml._html import (  # simplint: ignore[private-import-path]  # native extension lives in the root package
    Element,
    Formatter,
    Indent,
    Minify,
    minify_css,
    minify_css_inline,
    minify_js,
    parse,
    register_css_minify,
    register_js_minify,
    register_locations,
    register_render_configs,
    register_selector_error,
)

__all__ = [
    "Element",
    "Formatter",
    "Indent",
    "Minify",
    "minify_css",
    "minify_css_inline",
    "minify_js",
    "parse",
    "register_css_minify",
    "register_js_minify",
    "register_locations",
    "register_render_configs",
    "register_selector_error",
]
