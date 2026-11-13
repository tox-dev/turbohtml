"""Native functions shared by the extraction helpers."""

from __future__ import annotations

from turbohtml._html import (  # simplint: ignore[private-import-path]  # native extension lives in the root package
    Element,
    HTMLParseError,
    boilerplate,
    microdata_as_dict,
    parse,
    parse_xml,
    register_article,
    register_feed,
    register_links,
    register_structured_data,
    url_clean,
    url_normalize,
)

__all__ = [
    "Element",
    "HTMLParseError",
    "boilerplate",
    "microdata_as_dict",
    "parse",
    "parse_xml",
    "register_article",
    "register_feed",
    "register_links",
    "register_structured_data",
    "url_clean",
    "url_normalize",
]
