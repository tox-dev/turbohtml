"""
turbohtml.extract: pull content and data out of HTML.

Home for the extraction result types and (as the campaign lands) the content/date/url helpers that replace
``trafilatura``/``readability-lxml``/``newspaper3k``/``goose3``/``justext``/``htmldate``/``courlan``/``extruct``.
The extraction itself runs through the node methods (``node.article()``, ``node.links()``, ``node.tables()``,
``node.structured_data()``); the records they return are re-exported here for discoverability and also stay
importable from the package root.
"""

from __future__ import annotations

from ._article import Article
from ._links import Link
from ._structured_data import MicrodataItem, StructuredData
from ._urls import UrlCleaning, clean_url, extract_links, normalize_url

__all__ = [
    "Article",
    "Link",
    "MicrodataItem",
    "StructuredData",
    "UrlCleaning",
    "clean_url",
    "extract_links",
    "normalize_url",
]
