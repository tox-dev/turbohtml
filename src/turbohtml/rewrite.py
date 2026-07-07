"""
turbohtml.rewrite: a DOM-less, single-pass HTML rewriter.

Where :func:`turbohtml.parse` builds a document you navigate and mutate, :func:`rewrite` transforms markup on
the wire without ever building a tree. It streams the WHATWG tokenizer over the input, tracks only the stack of
currently-open elements, and calls a handler for each element a CSS selector matches (and, if you ask, for each
run of text, each comment, and the doctype). A handler edits the node in place -- set an attribute, insert markup
before or after it, replace its inner content, or drop it -- and the edited output is emitted incrementally.

Memory is proportional to the open-element depth plus whatever a handler chooses to buffer, never to the document
size, so a multi-megabyte page rewrites in a fixed working set. This is the model Cloudflare's lol-html popularized;
turbohtml reuses its own native tokenizer and CSS selector engine behind it.

Because the pass never looks ahead, only the selectors decidable from an element and its ancestors stream: type,
universal, id, class, and attribute selectors, the descendant (space) and child (``>``) combinators, ``:root``,
and :is()/:where()/:not() over that subset. The sibling combinators (``+``, ``~``), the positional and structural
pseudo-classes (``:nth-child``, ``:last-of-type``, ``:only-child``, ``:empty``), and ``:has()`` need content the
stream has not seen yet, so a selector using one raises :class:`~turbohtml.SelectorSyntaxError`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from ._html import _rewrite, _RewriteHandle

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = [
    "CommentHandler",
    "DoctypeHandler",
    "Element",
    "ElementHandler",
    "TextHandler",
    "rewrite",
]

Element = _RewriteHandle
"""The node handle a rewrite handler receives.

The same handle backs every handler kind; which members apply depends on :attr:`~Element.kind`. An element handle
exposes :attr:`~Element.tag`, :attr:`~Element.attrs`, and the attribute and content edits; a text or comment handle
exposes :attr:`~Element.text` and :meth:`~Element.set_text`; a doctype handle exposes :attr:`~Element.name`,
:attr:`~Element.public_id`, and :attr:`~Element.system_id`. Insertion and removal (:meth:`~Element.before`,
:meth:`~Element.after`, :meth:`~Element.replace`, :meth:`~Element.remove`) apply to every kind.

The handle is valid only for the duration of the handler call; stashing it and using it afterwards raises
:class:`RuntimeError`.
"""


class ElementHandler(Protocol):
    """A callback invoked with the matched :class:`Element` for each element a selector matches."""

    def __call__(self, element: Element, /) -> None:
        """Edit the matched ``element`` in place."""


class TextHandler(Protocol):
    """A callback invoked with each run of character data as an :class:`Element` handle of text kind."""

    def __call__(self, text: Element, /) -> None:
        """Edit the ``text`` run in place."""


class CommentHandler(Protocol):
    """A callback invoked with each comment as an :class:`Element` handle of comment kind."""

    def __call__(self, comment: Element, /) -> None:
        """Edit the ``comment`` in place."""


class DoctypeHandler(Protocol):
    """A callback invoked with the document type declaration as an :class:`Element` handle of doctype kind."""

    def __call__(self, doctype: Element, /) -> None:
        """Edit the ``doctype`` in place."""


def rewrite(
    html: str,
    *,
    elements: Iterable[tuple[str, ElementHandler]] = (),
    text: TextHandler | None = None,
    comments: CommentHandler | None = None,
    doctype: DoctypeHandler | None = None,
) -> str:
    """
    Rewrite ``html`` in a single streaming pass and return the transformed markup.

    :param html: the markup to rewrite.
    :param elements: an iterable of ``(selector, handler)`` pairs; ``handler`` is called with the matched
        :class:`Element` for each element the streamable CSS ``selector`` matches, in document order. When several
        selectors match one element their handlers run in the order given.
    :param text: called with each run of character data (a text-kind :class:`Element`); None skips text.
    :param comments: called with each comment (a comment-kind :class:`Element`); None skips comments.
    :param doctype: called with the document type declaration (a doctype-kind :class:`Element`); None skips it.
    :returns: the rewritten markup. An untouched construct is reproduced verbatim, so a rewrite that edits nothing
        returns the input unchanged (character references and original quoting are preserved).
    :raises TypeError: if ``html`` is not a str.
    :raises SelectorSyntaxError: if a selector is malformed or uses a construct the stream cannot match (a sibling
        combinator, a positional or structural pseudo-class, or ``:has()``).
    """
    return _rewrite(html, tuple(elements), text, comments, doctype)
