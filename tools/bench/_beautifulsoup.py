"""Operation bodies the BeautifulSoup competitors share, parameterized by the tree builder they parse with."""

from __future__ import annotations

import functools

from bs4 import BeautifulSoup


def find(backend: str, text: str) -> None:
    # Collect every anchor with BeautifulSoup's find_all.
    parsed(backend, text).find_all("a")


def socialcard(backend: str, text: str) -> None:
    # Read every meta property and content off a freshly parsed tree, BeautifulSoup's take on card extraction.
    for meta in fresh(backend, text).find_all("meta"):
        meta.get("property")
        meta.get("content")


@functools.cache
def parsed(backend: str, text: str) -> BeautifulSoup:
    # Return a document parsed once, cached so the read-path operations time only the query.
    return BeautifulSoup(text, backend)


def fresh(backend: str, text: str) -> BeautifulSoup:
    # Parse a fresh document for the mutating operations, which each iteration must run on an unmodified tree.
    return BeautifulSoup(text, backend)


__all__ = [
    "find",
    "fresh",
    "parsed",
    "socialcard",
]
