"""Operation bodies the BeautifulSoup competitors share, parameterized by the tree builder they parse with."""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING

from bs4 import BeautifulSoup

if TYPE_CHECKING:
    from bs4.element import Tag


def find(backend: str, text: str) -> None:
    # Collect every anchor with BeautifulSoup's find_all.
    parsed(backend, text).find_all("a")


@functools.cache
def parsed(backend: str, text: str) -> BeautifulSoup:
    # Return a document parsed once, cached so the read-path operations time only the query.
    return BeautifulSoup(text, backend)


def socialcard(backend: str, text: str) -> None:
    # Read every meta property and content off a freshly parsed tree, BeautifulSoup's take on card extraction.
    for meta in fresh(backend, text).find_all("meta"):
        meta.get("property")
        meta.get("content")


def fresh(backend: str, text: str) -> BeautifulSoup:
    # Parse a fresh document for the mutating operations, which each iteration must run on an unmodified tree.
    return BeautifulSoup(text, backend)


@functools.cache
def body(backend: str, text: str) -> Tag:
    return parsed(backend, text).find_all("body")[0]


def encode_inner(backend: str, text: str) -> bytes:
    return body(backend, text).encode_contents()


def encode_inner_indent(backend: str, text: str) -> bytes:
    return body(backend, text).encode_contents(indent_level=0)


def serialize_inner_indent(backend: str, text: str) -> str:
    return body(backend, text).decode_contents(indent_level=0)


__all__ = [
    "body",
    "encode_inner",
    "encode_inner_indent",
    "find",
    "fresh",
    "parsed",
    "serialize_inner_indent",
    "socialcard",
]
