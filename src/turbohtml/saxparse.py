"""
turbohtml.saxparse: DOM-less, event-driven HTML parsing.

Where :func:`turbohtml.parse` hands back a document you navigate, :func:`sax_parse` fires a callback for each
construct the parser builds -- a start tag, an end tag, a run of text, a comment, the doctype -- and hands back
nothing. The events reflect the fully WHATWG-correct construction (implied ``<html>``/``<head>``/``<body>``, foster
parenting out of tables, the adoption agency, and the rest), so you see the tree the parser *built*, not the raw token
soup; and no per-node Python object is ever created, so a one-pass extraction over a large document never pays for a
document-sized object graph and keeps only what your handler keeps. This is the event-driven model Python's
:class:`html.parser.HTMLParser`, expat, and htmlparser2 offer, with a spec-correct tree builder behind it and the
tokenization, construction, and walk all in C.

The one thing this is *not* is a way to parse a document larger than memory: a spec-correct tree builder cannot run in
space proportional to the open-element depth, because the adoption agency and text coalescing retroactively move and
merge nodes the walk has already emitted, so the working tree is retained until the parse ends (then freed in one
shot). The win over :func:`turbohtml.parse` is the object graph you never build and the tree you never keep; the win
over :class:`html.parser.HTMLParser` is speed and a tree the standard library's raw callbacks never reconstruct.

Two shapes share one C walk. Subclass :class:`SaxHandler`, override the events you care about, and pass an instance to
:func:`sax_parse` for the push (callback) form; or iterate :func:`iter_events` for the pull form, a stream of typed
:class:`StartElement`/:class:`EndElement`/:class:`Characters`/:class:`Comment`/:class:`Doctype`/
:class:`ProcessingInstruction` records. WHATWG HTML has no processing instructions -- ``<?xml ...>`` is a bogus
comment -- so a :class:`ProcessingInstruction` event stands in for exactly that ``<?...>`` construct, matching
:meth:`html.parser.HTMLParser.handle_pi`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple, cast

from ._html import _sax_events

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = [
    "Characters",
    "Comment",
    "Doctype",
    "EndElement",
    "ProcessingInstruction",
    "SaxEvent",
    "SaxHandler",
    "StartElement",
    "iter_events",
    "sax_parse",
]


class SaxHandler:
    """
    Base class for a :func:`sax_parse` callback object.

    Every method is a no-op by default; subclass and override only the events you need, the way you subclass
    :class:`html.parser.HTMLParser`.
    """

    def start_element(self, tag: str, attrs: tuple[tuple[str, str | None], ...]) -> None:
        """Handle a start tag ``tag`` with its ``attrs`` (each a ``(name, value)`` pair, value None if valueless)."""

    def end_element(self, tag: str) -> None:
        """Handle the end of element ``tag`` (fired for every element, including empty and void ones)."""

    def characters(self, data: str) -> None:
        """Handle a run of character data ``data``."""

    def comment(self, data: str) -> None:
        """Handle a comment with body ``data``."""

    def doctype(self, name: str, public_id: str | None, system_id: str | None) -> None:
        """Handle the document type declaration; ``public_id``/``system_id`` are None when the source omits them."""

    def processing_instruction(self, data: str) -> None:
        """Handle a ``<?...>`` construct with body ``data`` (a WHATWG bogus comment)."""


class StartElement(NamedTuple):
    """A start-tag event."""

    tag: str
    """the element's tag name."""
    attrs: tuple[tuple[str, str | None], ...]
    """the attributes, each a ``(name, value)`` pair with ``value`` None for a valueless attribute."""


class EndElement(NamedTuple):
    """An end-tag event, fired for every element the parser closes."""

    tag: str
    """the element's tag name."""


class Characters(NamedTuple):
    """A run of character data."""

    data: str
    """the text."""


class Comment(NamedTuple):
    """A comment event."""

    data: str
    """the comment body, between ``<!--`` and ``-->``."""


class Doctype(NamedTuple):
    """A document type declaration event."""

    name: str
    """the document type name, e.g. ``html``."""
    public_id: str | None
    """the public identifier, or None when the source omits it."""
    system_id: str | None
    """the system identifier, or None when the source omits it."""


class ProcessingInstruction(NamedTuple):
    """A ``<?...>`` event; WHATWG parses it as a bogus comment, so ``data`` is that comment's body."""

    data: str
    """the WHATWG bogus-comment body: the source between the opening ``<`` and the closing ``>``, so the leading
    ``?`` is kept (``<?php?>`` gives ``?php?``)."""


SaxEvent = StartElement | EndElement | Characters | Comment | Doctype | ProcessingInstruction
"""The union of every event :func:`iter_events` yields."""

_EVENTS: tuple[type[SaxEvent], ...] = (
    StartElement,
    EndElement,
    Characters,
    Comment,
    Doctype,
    ProcessingInstruction,
)


def iter_events(html: str) -> Iterator[SaxEvent]:
    """
    Parse ``html`` and yield its SAX events as typed records, one at a time, without building a document.

    :param html: the markup to parse.
    :returns: an iterator of :class:`StartElement`/:class:`EndElement`/:class:`Characters`/:class:`Comment`/
        :class:`Doctype`/:class:`ProcessingInstruction` records in document order.
    :raises TypeError: if ``html`` is not a str.
    """
    for kind, *fields in _sax_events(html):
        yield _EVENTS[cast("int", kind) - 1]._make(fields)


def sax_parse(html: str, handler: SaxHandler) -> None:
    """
    Parse ``html``, calling ``handler``'s methods for each event, and retain no tree.

    :param html: the markup to parse.
    :param handler: a :class:`SaxHandler` (or subclass) whose overridden methods receive the events.
    :raises TypeError: if ``html`` is not a str.
    """
    for event in iter_events(html):
        if isinstance(event, StartElement):
            handler.start_element(event.tag, event.attrs)
        elif isinstance(event, EndElement):
            handler.end_element(event.tag)
        elif isinstance(event, Characters):
            handler.characters(event.data)
        elif isinstance(event, Comment):
            handler.comment(event.data)
        elif isinstance(event, Doctype):
            handler.doctype(event.name, event.public_id, event.system_id)
        else:
            handler.processing_instruction(event.data)
