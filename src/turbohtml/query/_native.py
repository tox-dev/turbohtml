"""Native functions shared by the query helpers."""

from __future__ import annotations

from turbohtml._html import (  # simplint: ignore[private-import-path]  # native extension lives in the root package
    Document,
    Element,
    css_escape_identifier,
    matches_many,
    parse,
    query_add_class,
    query_attr,
    query_children,
    query_closest,
    query_has_class,
    query_parents,
    query_remove_class,
    query_siblings,
    query_text,
    query_toggle_class,
    query_unique,
    register_xpath_depth_error,
    register_xpath_string,
    select_limited,
    select_many,
)
from turbohtml._internal import SelectorSyntaxError  # simplint: ignore[private-import-path]  # avoid root import cycle

__all__ = [
    "Document",
    "Element",
    "SelectorSyntaxError",
    "css_escape_identifier",
    "matches_many",
    "parse",
    "query_add_class",
    "query_attr",
    "query_children",
    "query_closest",
    "query_has_class",
    "query_parents",
    "query_remove_class",
    "query_siblings",
    "query_text",
    "query_toggle_class",
    "query_unique",
    "register_xpath_depth_error",
    "register_xpath_string",
    "select_limited",
    "select_many",
]
