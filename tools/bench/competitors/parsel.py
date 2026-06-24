"""parsel: Scrapy's selector library (lxml + cssselect), find and select only."""

from __future__ import annotations

import functools

from parsel import Selector

REQUIREMENTS = ("parsel>=1.11",)


@functools.cache
def _parsed(text: str) -> Selector:
    """Return a document parsed once, cached so the read-path operations time only the query."""
    return Selector(text=text)


def find(text: str) -> None:
    """Collect every anchor with parsel's css."""
    _parsed(text).css("a")


def select(text: str) -> None:
    """Run the CSS selector with parsel's css (cssselect translates it to XPath on libxml2)."""
    _parsed(text).css("div a[href]")


OPERATIONS = {"find": (find, "parsel"), "select": (select, "parsel")}
