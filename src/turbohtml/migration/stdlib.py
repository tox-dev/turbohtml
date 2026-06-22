"""
An :class:`python:html.parser.HTMLParser`-shaped adapter over the streaming tokenizer.

A drop-in base class for the many ``HTMLParser`` subclasses in the wild: subclass it, override the ``handle_*`` methods,
and call :meth:`~HTMLParser.feed`/:meth:`~HTMLParser.close` incrementally, exactly as with the standard library, but
over turbohtml's WHATWG-conformant tokenizer. Migrating is changing the import and the base class.

It is kept apart from the core API (which exposes the token stream and the tree directly) so the drop-in surface stays
bounded. It differs from the standard library only where the standard library diverges from the WHATWG algorithm
browsers run:

- Character references are always resolved (as ``convert_charrefs=True``), so :meth:`~HTMLParser.handle_data` receives
  decoded text and :meth:`~HTMLParser.handle_entityref`/:meth:`~HTMLParser.handle_charref` are never called. The
  ``convert_charrefs`` argument is accepted for signature compatibility but has no effect.
- A processing instruction (``<?...>``) and a CDATA section outside foreign content are comments per the HTML spec, so
  they reach :meth:`~HTMLParser.handle_comment`; :meth:`~HTMLParser.handle_pi` and :meth:`~HTMLParser.unknown_decl` are
  never called.
- A valueless attribute's value is the empty string rather than ``None`` (the tokenizer does not tell ``disabled``
  apart from ``disabled=""``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from turbohtml._html import Tokenizer, TokenType

if TYPE_CHECKING:
    from turbohtml._html import Token

__all__ = ["HTMLParser"]


class HTMLParser:
    """
    A subclass-and-override callback parser over the streaming tokenizer.

    :param convert_charrefs: accepted for compatibility but ignored; references are always resolved.
    """

    convert_charrefs: bool
    """Accepted for compatibility; references are always resolved regardless of its value."""

    def __init__(self, *, convert_charrefs: bool = True) -> None:
        """Create a parser."""
        self.convert_charrefs = convert_charrefs
        self._tokenizer = Tokenizer()
        self._lineno = 1
        self._offset = 0

    def feed(self, data: str) -> None:
        """
        Feed a chunk of HTML, dispatching the tokens it completes to the handlers.

        :param data: the next chunk of HTML.
        """
        for token in self._tokenizer.feed(data):
            self._dispatch(token)

    def close(self) -> None:
        """Signal end of input, dispatching any tokens still buffered."""
        for token in self._tokenizer.close():
            self._dispatch(token)

    def reset(self) -> None:
        """Discard the buffered input and position, ready to parse a new document."""
        self._tokenizer.reset()
        self._lineno = 1
        self._offset = 0

    def getpos(self) -> tuple[int, int]:
        """
        Report the source position of the last dispatched token.

        :returns: the 1-based line and 0-based column of the token last dispatched.
        """
        return (self._lineno, self._offset)

    def _dispatch(self, token: Token) -> None:
        # the token type guarantees the relevant fields are present, which the Token
        # API (where every field is optional) cannot express, so they are narrowed here
        self._lineno = token.line
        self._offset = token.col
        kind = token.type
        if kind is TokenType.START_TAG:
            tag = cast("str", token.tag)
            attrs = cast("list[tuple[str, str | None]]", token.attrs or [])
            if token.self_closing:
                self.handle_startendtag(tag, attrs)
            else:
                self.handle_starttag(tag, attrs)
        elif kind is TokenType.END_TAG:
            self.handle_endtag(cast("str", token.tag))
        elif kind is TokenType.TEXT:
            self.handle_data(cast("str", token.data))
        elif kind is TokenType.COMMENT:
            self.handle_comment(cast("str", token.data))
        else:  # TokenType.DOCTYPE
            self.handle_decl(_doctype_decl(token))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """
        Handle a start tag; override to act on it.

        :param tag: the lowercased tag name.
        :param attrs: the (name, value) attribute pairs, a valueless attribute's value the empty string.
        """

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """
        Handle a self-closing tag; by default fire start then end, like the standard library.

        :param tag: the lowercased tag name.
        :param attrs: the (name, value) attribute pairs.
        """
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        """
        Handle an end tag; override to act on it.

        :param tag: the lowercased tag name.
        """

    def handle_data(self, data: str) -> None:
        """
        Handle a run of text, with character references resolved.

        :param data: the decoded text.
        """

    def handle_comment(self, data: str) -> None:
        """
        Handle a comment (also processing instructions and CDATA, which are comments here).

        :param data: the comment text, without the ``<!--`` ``-->`` delimiters.
        """

    def handle_decl(self, decl: str) -> None:
        """
        Handle a ``<!DOCTYPE ...>`` declaration, with the leading ``<!`` and trailing ``>`` removed.

        :param decl: the declaration text, such as ``DOCTYPE html``.
        """

    def handle_pi(self, data: str) -> None:
        """
        Handle a processing instruction; never called, since PIs are comments in the HTML spec.

        :param data: the instruction text.
        """

    def handle_entityref(self, name: str) -> None:
        """
        Handle a named reference; never called, since references are always resolved.

        :param name: the entity name, without the ``&`` and ``;``.
        """

    def handle_charref(self, name: str) -> None:
        """
        Handle a numeric reference; never called, since references are always resolved.

        :param name: the numeric reference body, without the ``&#`` and ``;``.
        """

    def unknown_decl(self, data: str) -> None:
        """
        Handle a CDATA section; never called, since it is a comment outside foreign content.

        :param data: the section text.
        """


def _doctype_decl(token: Token) -> str:
    parts = ["DOCTYPE"]
    if token.name:
        parts.append(token.name)
    if token.public_id is not None:
        parts.append(f'PUBLIC "{token.public_id}"')
        if token.system_id is not None:
            parts.append(f'"{token.system_id}"')
    elif token.system_id is not None:
        parts.append(f'SYSTEM "{token.system_id}"')
    return " ".join(parts)
