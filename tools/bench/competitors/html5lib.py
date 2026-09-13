"""html5lib: the pure-Python WHATWG reference parser."""

from __future__ import annotations

import functools
from collections import deque
from typing import TYPE_CHECKING, Final, cast

from html5lib.serializer import HTMLSerializer

if TYPE_CHECKING:
    from collections.abc import Iterator
    from xml.etree.ElementTree import Element

import html5lib

REQUIREMENTS = ("html5lib>=1.1",)

_WALKER = html5lib.getTreeWalker("etree")
_SERIALIZER: Final = HTMLSerializer(omit_optional_tags=False, quote_attr_values="always")
_MINIFIER: Final = HTMLSerializer(strip_whitespace=True, omit_optional_tags=True, quote_attr_values="spec")


def parse(text: str) -> None:
    """Parse a whole document with html5lib."""
    html5lib.parse(text)


def _parse_encoded(case: tuple[str, bytes]) -> None:
    html5lib.parse(case[1], override_encoding=case[0])


@functools.cache
def _parsed(text: str) -> Element:
    """Return a document parsed once, cached so the read-path operations time only the query."""
    return html5lib.parse(text)


def serialize(text: str) -> None:
    """Serialize a parsed document back to HTML with html5lib's etree serializer."""
    html5lib.serialize(_parsed(text))


def _serialize_attributes(case: tuple[str, bool]) -> str:
    return html5lib.serialize(
        _parsed(case[0]), alphabetical_attributes=case[1], omit_optional_tags=False, quote_attr_values="always"
    )


def navigate(text: str) -> None:
    """Walk every node with html5lib's etree TreeWalker token stream."""
    for _token in _WALKER(_parsed(text)):
        pass


def fragment(text: str) -> None:
    """Parse a fragment with html5lib's parseFragment."""
    html5lib.parseFragment(text)


def tokenize(text: str) -> None:
    """Drive html5lib's tokenizer over the input."""
    from html5lib._tokenizer import (  # ruff:ignore[import-outside-top-level, import-private-name]  # internal API
        HTMLTokenizer,
    )

    for _ in HTMLTokenizer(text):
        pass


def _serialize_inner(text: str) -> str:
    return "".join(_inner_chunks(text))


def _inner_chunks(text: str) -> Iterator[str]:
    tokens: Final = iter(_WALKER(_body(text)))
    next(tokens)
    yield from _SERIALIZER.serialize(
        token for token in tokens if not (token["type"] == "EndTag" and token["name"] == "body")
    )


@functools.cache
def _body(text: str) -> Element:
    return cast("Element", _parsed(text).find("{http://www.w3.org/1999/xhtml}body"))


def _encode_inner(text: str) -> bytes:
    return _serialize_inner(text).encode()


def _iterate_inner(text: str) -> None:
    deque(_inner_chunks(text), maxlen=0)


def _serialize_inner_minify(text: str) -> str:
    tokens: Final = iter(_WALKER(_body(text)))
    next(tokens)
    return "".join(
        _MINIFIER.serialize(
            token
            for token in tokens
            if token["type"] != "Comment" and not (token["type"] == "EndTag" and token["name"] == "body")
        )
    )


def _encode_inner_minify(text: str) -> bytes:
    return _serialize_inner_minify(text).encode()


def _whitespace_roundtrip(text: str) -> str:
    return html5lib.serialize(
        html5lib.parse(
            html5lib.serialize(
                _parsed(text), strip_whitespace=True, omit_optional_tags=False, quote_attr_values="always"
            )
        ),
        omit_optional_tags=False,
        quote_attr_values="always",
    )


OPERATIONS = {
    "serialize-inner": (_serialize_inner, "html5lib"),
    "encode-inner": (_encode_inner, "html5lib"),
    "iterate-inner": (_iterate_inner, "html5lib"),
    "serialize-inner-minify": (_serialize_inner_minify, "html5lib"),
    "encode-inner-minify": (_encode_inner_minify, "html5lib"),
    "whitespace-roundtrip": (_whitespace_roundtrip, "html5lib"),
    "parse": (parse, "html5lib"),
    "parse-encoded": (_parse_encoded, "html5lib"),
    "parse-formatting": (parse, "html5lib"),
    "parse-foster": (parse, "html5lib"),
    "parse-crlf": (parse, "html5lib"),
    "parse-nul": (parse, "html5lib"),
    "parse-afe": (parse, "html5lib"),
    "parse-scope": (parse, "html5lib"),
    "fragment": (fragment, "html5lib"),
    "tokenize": (tokenize, "html5lib"),
    "serialize": (serialize, "html5lib"),
    "navigate": (navigate, "html5lib"),
    "serialize-attributes": (_serialize_attributes, "html5lib"),
}
