"""html5lib: the pure-Python WHATWG reference parser."""

from __future__ import annotations

import html5lib

REQUIREMENTS = ("html5lib>=1.1",)


def parse(text: str) -> None:
    """Parse a whole document with html5lib."""
    html5lib.parse(text)


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
}
