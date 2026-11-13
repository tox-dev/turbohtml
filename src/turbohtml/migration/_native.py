"""Native functions shared by the migration helpers."""

from __future__ import annotations

from turbohtml._html import (  # simplint: ignore[private-import-path]  # native extension lives in the root package
    bleach_allow_relative,
    bleach_attributes,
    collapse_whitespace,
    markup_escape,
    markup_escape_silent,
    markup_soft_str,
    parse_fragment,
    register_markup,
    unescape,
)

__all__ = [
    "bleach_allow_relative",
    "bleach_attributes",
    "collapse_whitespace",
    "markup_escape",
    "markup_escape_silent",
    "markup_soft_str",
    "parse_fragment",
    "register_markup",
    "unescape",
]
