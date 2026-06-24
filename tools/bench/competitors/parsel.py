"""parsel: Scrapy's selector library (lxml + cssselect), find, select, and the ::attr/::text extraction idioms."""

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


def extract_attr(text: str) -> None:
    """Pull every anchor's href with parsel's ``::attr(href)`` getall."""
    _parsed(text).css("a::attr(href)").getall()


def extract_text(text: str) -> None:
    """Pull every anchor's text with parsel's ``::text`` getall."""
    _parsed(text).css("a::text").getall()


OPERATIONS = {
    "find": (find, "parsel"),
    "select": (select, "parsel"),
    "extract-attr": (extract_attr, "parsel"),
    "extract-text": (extract_text, "parsel"),
}
