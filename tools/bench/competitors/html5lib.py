"""html5lib: the pure-Python WHATWG reference parser."""

from __future__ import annotations

import functools

import html5lib

REQUIREMENTS = ("html5lib>=1.1",)

_WALKER = html5lib.getTreeWalker("etree")


def parse(text: str) -> None:
    """Parse a whole document with html5lib."""
    html5lib.parse(text)


@functools.cache
def _parsed(text: str) -> object:
    """Return a document parsed once, cached so the read-path operations time only the query."""
    return html5lib.parse(text)


def serialize(text: str) -> None:
    """Serialize a parsed document back to HTML with html5lib's etree serializer."""
    html5lib.serialize(_parsed(text))


def navigate(text: str) -> None:
    """Walk every node with html5lib's etree TreeWalker token stream."""
    for _token in _WALKER(_parsed(text)):
        pass


def fragment(text: str) -> None:
    """Parse a fragment with html5lib's parseFragment."""
    html5lib.parseFragment(text)


def tokenize(text: str) -> None:
    """Drive html5lib's tokenizer over the input."""
    from html5lib._tokenizer import HTMLTokenizer  # noqa: PLC0415, PLC2701  # html5lib's tokenizer is internal API

    for _ in HTMLTokenizer(text):
        pass


OPERATIONS = {
    "parse": (parse, "html5lib"),
    "fragment": (fragment, "html5lib"),
    "tokenize": (tokenize, "html5lib"),
    "serialize": (serialize, "html5lib"),
    "navigate": (navigate, "html5lib"),
}
