"""soupsieve: BeautifulSoup's CSS selector engine, timed on its compiled select and per-element match."""

from __future__ import annotations

import functools

import soupsieve
from bs4 import BeautifulSoup

REQUIREMENTS = ("soupsieve>=2.8.4", "beautifulsoup4>=4.15")

_SELECT = soupsieve.compile("div a[href]")
_HAS = soupsieve.compile("div:has(a)")
_ANCHOR = soupsieve.compile("a")


@functools.cache
def _parsed(text: str) -> BeautifulSoup:
    """Return a document parsed once, cached so the read-path operations time only the query."""
    return BeautifulSoup(text, "html.parser")


def find(text: str) -> None:
    """Collect every anchor with a compiled soupsieve type selector over the document."""
    _ANCHOR.select(_parsed(text))


def select(text: str) -> None:
    """Collect every match of a compiled soupsieve selector over the document."""
    _SELECT.select(_parsed(text))


def select_has(text: str) -> None:
    """Collect every match of a compiled soupsieve :has() relational selector over the document."""
    _HAS.select(_parsed(text))


def match(text: str) -> None:
    """Test every anchor against a compiled soupsieve selector with its per-element match."""
    for anchor in _parsed(text).find_all("a"):
        _SELECT.match(anchor)


OPERATIONS = {
    "find": (find, "soupsieve"),
    "select": (select, "soupsieve"),
    "select-has": (select_has, "soupsieve"),
    "match": (match, "soupsieve"),
}
