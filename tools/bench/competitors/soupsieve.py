"""soupsieve: BeautifulSoup's CSS selector engine, timed on its compiled select and per-element match."""

from __future__ import annotations

import functools
from typing import Final

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


def _select_default(case: tuple[str, str]) -> None:
    if case[0] == ":default" and "<button>" in case[1]:
        unsupported: Final = "SoupSieve requires explicit type=submit for default buttons"
        raise NotImplementedError(unsupported)
    _select_relative(case)


def _select_relative(case: tuple[str, str]) -> None:
    soupsieve.select(case[0], _parsed(case[1]))


def match(text: str) -> None:
    """Test every anchor against a compiled soupsieve selector with its per-element match."""
    for anchor in _parsed(text).find_all("a"):
        _SELECT.match(anchor)


def escape_identifier(idents: tuple[str, ...]) -> None:
    """Escape each raw identifier with soupsieve's escape."""
    for ident in idents:
        soupsieve.escape(ident)


OPERATIONS = {
    "escape-identifier": (escape_identifier, "soupsieve"),
    "find": (find, "soupsieve"),
    "select": (select, "soupsieve"),
    "select-relative": (_select_relative, "soupsieve"),
    "select-default": (_select_default, "soupsieve"),
    "select-has": (select_has, "soupsieve"),
    "match": (match, "soupsieve"),
}
