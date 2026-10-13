"""Incremental public entry points must preserve the one-shot result."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final, NamedTuple

from turbohtml import (
    Document,
    Element,
    Formatter,
    Html,
    IncrementalParser,
    Indent,
    Minify,
    Node,
    Token,
    Tokenizer,
    TokenType,
    parse,
    parse_fragment,
    tokenize,
)

from .atheris_header import BYTEWISE, Header, feed_chunks
from .atheris_invariants import assert_invariant
from .atheris_registry import Target
from .round_trip_oracles import html_check, span_check, xpath_entry_check

if TYPE_CHECKING:
    from collections.abc import Callable

    from turbohtml import SourceLocation

__all__ = [
    "document_observation",
    "fragment_observation",
    "incremental_observation",
    "parser_targets",
    "token_observation",
]


# One node-set, one scalar and one attribute expression reach each XPath result shape on the fuzzed tree.
_XPATH_ENTRIES: Final = ("//*", "count(//*)", "//*/@*")


def parser_targets() -> tuple[Target, ...]:
    """Keep document and fragment grammar contexts distinct."""
    return (
        Target(
            "html-document",
            _document,
            ("turbohtml.parse", "turbohtml.Document", "turbohtml.Node"),
            (UnicodeDecodeError,),
        ),
        Target(
            "html-fragment",
            _fragment,
            (
                "turbohtml.parse_fragment",
                "turbohtml.Element",
                "turbohtml.Html",
                "turbohtml.Formatter",
                "turbohtml.Indent",
                "turbohtml.Minify",
            ),
            (UnicodeDecodeError,),
        ),
        Target(
            "html-incremental",
            _incremental,
            ("turbohtml.IncrementalParser",),
            (UnicodeDecodeError,),
            _incremental_chunks,
        ),
        Target(
            "html-tokenizer",
            _tokens,
            ("turbohtml.tokenize", "turbohtml.Tokenizer", "turbohtml.Token", "turbohtml.TokenType"),
            (UnicodeDecodeError,),
            _token_chunks,
        ),
    )


def _document(data: bytes) -> None:
    document_observation(data)


def _fragment(data: bytes) -> None:
    fragment_observation(data)


def _incremental(data: bytes) -> None:
    incremental_observation(data)


def _incremental_chunks(data: bytes, header: Header) -> None:
    incremental_observation(data, header)


def _tokens(data: bytes) -> None:
    token_observation(data)


def _token_chunks(data: bytes, header: Header) -> None:
    token_observation(data, header)


def document_observation(
    data: bytes,
    html: Callable[[str], str | None] = html_check,
    spans: Callable[[str], str | None] = span_check,
    entries: Callable[[str], str | None] = xpath_entry_check,
) -> str:
    """Exercise returned document records through serialization consumers, span and XPath entry-point oracles."""
    source: Final = data.decode("utf-8")
    document: Final = parse(source)
    _require("Document result type", condition=isinstance(document, (Document, Node)))
    assert_invariant(html, source)
    assert_invariant(spans, source)
    for expression in _XPATH_ENTRIES:
        assert_invariant(entries, json.dumps({"html": source, "xpath": expression}))
    return _serialization(document)


def fragment_observation(data: bytes) -> str:
    """Grammar fragments use a div context rather than document insertion modes."""
    fragment: Final = parse_fragment(data.decode("utf-8"), "div")
    _require("Fragment result type", condition=isinstance(fragment, Element))
    return _serialization(fragment)


def incremental_observation(data: bytes, header: Header = BYTEWISE) -> tuple[str, _Positions]:
    """
    Empty feeds and splits inside UTF-8 sequences must preserve the one-shot parse and its positions.

    The opts bits flip the defaults: 1 feeds decoded text, 2 drops positions, 4 records source locations.
    """
    source: Final = data.decode("utf-8")
    positions: Final = not header.opts & 2
    locations: Final = bool(header.opts & 4)
    parser: Final = IncrementalParser(positions=positions, source_locations=locations)
    if header.opts & 1:
        feed_chunks(parser.feed, source, header.max_chunk)
    else:
        feed_chunks(parser.feed, data, header.max_chunk)
    document: Final = parser.close()
    expected: Final = parse(data, positions=positions, source_locations=locations)
    observed: Final = _serialization(document)
    _require("Incremental document differs", condition=observed == expected.serialize())
    placed: Final = _positions(document)
    _require("Incremental positions differ", condition=placed == _positions(expected))
    return observed, placed


def token_observation(data: bytes, header: Header = BYTEWISE) -> tuple[_TokenSnapshot, ...]:
    """
    Streaming and reset must preserve token fields and source positions.

    The opts bits flip the defaults: 1 keeps character references apart, 2 captures source, 4 drops attributes.
    """
    source: Final = data.decode("utf-8")
    options: Final = {
        "resolve_references": not header.opts & 1,
        "capture_source": bool(header.opts & 2),
        "capture_attributes": not header.opts & 4,
    }
    tokenizer: Final = Tokenizer(**options)
    streamed: Final[list[Token]] = []
    feed_chunks(lambda chunk: streamed.extend(tokenizer.feed(chunk)), source, header.max_chunk)
    streamed.extend(tokenizer.close())
    observed: Final = tuple(_token_snapshot(token) for token in streamed)
    _require(
        "Incremental tokens differ",
        condition=observed == tuple(_token_snapshot(token) for token in tokenize(source, **options)),
    )
    tokenizer.reset()
    _require(
        "Reset tokens differ",
        condition=tuple(_token_snapshot(token) for token in tokenizer.feed(source))
        + tuple(_token_snapshot(token) for token in tokenizer.close())
        == observed,
    )
    return observed


def _positions(document: Document) -> _Positions:
    return tuple(
        (element.source_line, element.source_col, element.source_location) for element in document.iter_elements()
    )


def _serialization(node: Node) -> str:
    for formatter in Formatter:
        for layout in (None, Indent(2)):
            options: Final = Html(formatter=formatter, layout=layout)
            rendered: Final = node.serialize(options)
            _require("Iterator serialization differs", condition="".join(node.serialize_iter(options)) == rendered)
            _require(
                "Encoded serialization differs", condition=node.encode("utf-8", options) == rendered.encode("utf-8")
            )
    minified_options: Final = Html(layout=Minify())
    _require(
        "Minified encoding differs",
        condition=node.encode("utf-8", minified_options) == node.serialize(minified_options).encode("utf-8"),
    )
    return node.serialize()


def _token_snapshot(token: Token) -> _TokenSnapshot:
    _require("Token result type", condition=isinstance(token, Token))
    _require("Token kind type", condition=isinstance(token.type, TokenType))
    return _TokenSnapshot(
        token.type.name,
        token.tag,
        token.data,
        tuple(token.attrs) if token.attrs is not None else None,
        token.self_closing,
        token.line,
        token.col,
    )


def _require(message: str, *, condition: bool) -> None:
    if not condition:
        raise AssertionError(message)


class _TokenSnapshot(NamedTuple):
    kind: str
    tag: str | None
    data: str | None
    attrs: tuple[tuple[str, str], ...] | None
    self_closing: bool
    line: int
    col: int


_Positions = tuple[tuple[int | None, int | None, "SourceLocation | None"], ...]
