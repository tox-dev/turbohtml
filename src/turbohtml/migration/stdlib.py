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
- A processing instruction reaches :meth:`~HTMLParser.handle_pi`. A CDATA section outside foreign content remains a
  comment, so :meth:`~HTMLParser.unknown_decl` is never called.
- A valueless attribute's value is the empty string rather than ``None`` (the tokenizer does not tell ``disabled``
  apart from ``disabled=""``).
"""

from __future__ import annotations

from turbohtml._html import Tokenizer

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

    def feed(self, data: str) -> None:
        """
        Feed a chunk of HTML, dispatching the tokens it completes to the handlers.

        :param data: the next chunk of HTML.
        """
        self._tokenizer.feed(data)
        self._tokenizer.dispatch(self)

    def close(self) -> None:
        """Signal end of input, dispatching any tokens still buffered."""
        self._tokenizer.close()
        self._tokenizer.dispatch(self)

    def reset(self) -> None:
        """Discard the buffered input and position, ready to parse a new document."""
        self._tokenizer.reset()

    def getpos(self) -> tuple[int, int]:
        """
        Report the source position of the last dispatched token.

        :returns: the 1-based line and 0-based column of the token last dispatched.
        """
        return self._tokenizer.position()

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
        Handle a comment (also CDATA outside foreign content).

        :param data: the comment text, without the ``<!--`` ``-->`` delimiters.
        """

    def handle_decl(self, decl: str) -> None:
        """
        Handle a ``<!DOCTYPE ...>`` declaration, with the leading ``<!`` and trailing ``>`` removed.

        :param decl: the declaration text, such as ``DOCTYPE html``.
        """

    def handle_pi(self, data: str) -> None:
        """
        Handle an instruction with its target and data joined by one space.

        :param data: the target followed by data when present.
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
