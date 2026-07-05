"""
The soupsieve-shaped CSS matching surface of :mod:`turbohtml.query`, over the native selector engine.

`soupsieve <https://facelessuser.github.io/soupsieve/>`__ is BeautifulSoup's CSS selector engine. A bs4 stack reaches
it through ``soupsieve.compile(selector)`` (returning a reusable matcher) and the module-level
``select``/``select_one``/``iselect``/``match``/``filter``/``closest`` helpers. turbohtml already matches the same CSS
through :meth:`~turbohtml.Node.select`/:meth:`~turbohtml.Node.matches`/:meth:`~turbohtml.Node.closest`; this
surface wraps those node methods in the soupsieve call shapes so porting a soupsieve (or bs4 ``Tag.select``) codebase is
a mechanical import swap rather than a rewrite. It is re-exported from :mod:`turbohtml.query` next to the pyquery-style
:class:`~turbohtml.query.Query`, the one namespace for searching a tree.

It is a pure-Python facade -- no second engine. :func:`compile` validates the selector once up front (soupsieve
compiles eagerly), raising :class:`turbohtml.SelectorSyntaxError` for a malformed selector, and the returned
:class:`Matcher` re-runs the native engine per call. The selector entry points on the C core take only the selector
string, so the soupsieve ``namespaces`` and ``flags`` arguments are bundled into one immutable :class:`Matching` config
that travels
with the matcher for API parity; they do not alter match *results* (mirroring soupsieve, whose ``flags`` are advisory
and whose namespace discrimination turbohtml does not yet apply -- see the module reference for the limitation).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, cast

from ._html import Document, Element, parse
from ._selectors import SelectorSyntaxError

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping

#: The soupsieve ``DEBUG`` flag value, re-exported so a port can pass it without importing soupsieve. turbohtml exposes
#: no compiled-selector dump, so it is accepted and carried but has no effect.
DEBUG: Final = 0x1


def escape_identifier(ident: str) -> str:
    """
    Escape a string so it is a valid CSS identifier, like ``soupsieve.escape`` / ``CSS.escape``.

    Useful for building a selector around a class or id read from data: ``f"#{escape_identifier(raw_id)}"`` is safe even
    when ``raw_id`` starts with a digit or holds ``.``/``#``/spaces.

    :param ident: the raw identifier text.
    :returns: the identifier with CSS-significant characters backslash- or hex-escaped per the CSSOM rules.
    """
    out: list[str] = []
    for position, char in enumerate(ident):
        code = ord(char)
        is_digit = 0x30 <= code <= 0x39
        # a digit leading the identifier (or following an initial dash) cannot start a CSS name, so it is hex-escaped
        leading_digit = is_digit and (position == 0 or (position == 1 and ident[0] == "-"))
        is_name_char = code >= 0x80 or is_digit or char in "-_" or 0x41 <= code <= 0x5A or 0x61 <= code <= 0x7A
        if code == 0:
            out.append("�")
        elif code <= 0x1F or code == 0x7F or leading_digit:
            out.append(f"\\{code:x} ")
        elif position == 0 and code == 0x2D and len(ident) == 1:
            out.append(f"\\{char}")
        elif is_name_char:
            out.append(char)
        else:
            out.append(f"\\{char}")
    return "".join(out)


@dataclass(frozen=True)
class Matching:
    """
    The soupsieve ``namespaces`` and ``flags`` arguments, bundled into one immutable, thread-safe config.

    Pass it as the ``options`` of :func:`compile` (or any module-level helper) to mirror a soupsieve call. Both fields
    are carried for API parity and do not change which elements match: turbohtml selects by an element's local name, so
    a prefixed type selector (``svg|rect``) matches every ``rect`` regardless of the ``namespaces`` mapping, and
    ``flags`` is advisory as in soupsieve. ``Matching()`` reproduces soupsieve's default HTML matching.

    :param namespaces: prefix-to-URI map for prefixed type selectors, accepted for soupsieve parity; the native engine
        does not discriminate by namespace URI (see the module reference).
    :param flags: the soupsieve flag bitmask (only :data:`DEBUG` is defined); accepted and carried but inert.
    """

    namespaces: Mapping[str, str] | None = None
    flags: int = 0

    @classmethod
    def soupsieve(cls, namespaces: Mapping[str, str] | None = None, flags: int = 0) -> Matching:
        """
        Build a config straight from soupsieve's ``(namespaces, flags)`` call convention.

        Lets a port keep the original argument list -- ``Matching.soupsieve(namespaces=ns, flags=DEBUG)`` -- rather
        than renaming keywords at every call site.

        :param namespaces: the soupsieve ``namespaces`` mapping.
        :param flags: the soupsieve ``flags`` bitmask.
        :returns: the equivalent :class:`Matching`.
        """
        return cls(namespaces=namespaces, flags=flags)


_DEFAULT: Final = Matching()
# A throwaway element to validate selector syntax at compile time; matching against it parses the selector, raising on
# a malformed one, without needing the caller's node.
_PROBE: Final = cast("Element", parse("<x></x>").root)


class Matcher:
    """
    A selector compiled against turbohtml's engine, with the soupsieve matcher methods.

    Build one with :func:`compile` and reuse it; it is immutable and thread-safe. Each method runs the native selector
    engine over the node passed in, so one :class:`Matcher` serves many trees.

    :param selector: the CSS selector; validated immediately, raising :class:`turbohtml.SelectorSyntaxError` when
        malformed.
    :param options: the :class:`Matching` config, or ``None`` for soupsieve's defaults.
    """

    def __init__(self, selector: str, options: Matching | None = None, /) -> None:
        """Validate the selector (a bad one raises :class:`turbohtml.SelectorSyntaxError`) and store it."""
        _PROBE.matches(selector)
        self._selector = selector
        self._options = options if options is not None else _DEFAULT

    @property
    def pattern(self) -> str:
        """The selector string, named as soupsieve's ``SoupSieve.pattern``."""
        return self._selector

    @property
    def namespaces(self) -> Mapping[str, str] | None:
        """The configured namespace map, named as soupsieve's ``SoupSieve.namespaces``."""
        return self._options.namespaces

    @property
    def flags(self) -> int:
        """The configured flag bitmask, named as soupsieve's ``SoupSieve.flags``."""
        return self._options.flags

    def match(self, node: Element) -> bool:
        """
        Test whether one element matches the selector.

        :param node: the element to test.
        :returns: whether it matches.
        """
        return node.matches(self._selector)

    def select(self, node: Element | Document, limit: int = 0) -> list[Element]:
        """
        Collect the descendants of an element that match the selector, in document order.

        :param node: the element or document whose descendants are searched.
        :param limit: the most matches to return, or ``0`` for all.
        :returns: the matching descendants.
        """
        results = node.select(self._selector)
        return results if limit <= 0 else results[:limit]

    def select_one(self, node: Element | Document) -> Element | None:
        """
        Return the first descendant of an element that matches the selector.

        :param node: the element or document whose descendants are searched.
        :returns: the first match in document order, or ``None``.
        """
        return node.select_one(self._selector)

    def iselect(self, node: Element | Document, limit: int = 0) -> Iterator[Element]:
        """
        Iterate the descendants of an element that match the selector, in document order.

        :param node: the element or document whose descendants are searched.
        :param limit: the most matches to yield, or ``0`` for all.
        :returns: an iterator over the matching descendants.
        """
        yield from self.select(node, limit)

    def filter(self, iterable: Element | Document | Iterable[Element]) -> list[Element]:
        """
        Keep the members of an iterable that match the selector.

        :param iterable: elements to test, or one element or document whose direct element children are tested
            (soupsieve's rule for a single node).
        :returns: the members that match.
        """
        if isinstance(iterable, (Element, Document)):
            candidates: Iterable[Element] = (child for child in iterable.children if isinstance(child, Element))
        else:
            candidates = iterable
        return [node for node in candidates if node.matches(self._selector)]

    def closest(self, node: Element) -> Element | None:
        """
        Return the nearest element at or above one that matches the selector.

        :param node: the element to walk up from.
        :returns: the nearest self-or-ancestor that matches, or ``None``.
        """
        return node.closest(self._selector)


def compile(selector: str, options: Matching | None = None, /) -> Matcher:  # noqa: A001  # the soupsieve entry-point name
    """
    Compile a CSS selector into a reusable :class:`Matcher`, like ``soupsieve.compile``.

    The selector is validated up front, so a malformed one raises :class:`turbohtml.SelectorSyntaxError` here, not at
    the first match.

    :param selector: the CSS selector.
    :param options: the :class:`Matching` config, or ``None`` for soupsieve's defaults.
    :returns: a matcher bound to the selector.
    """
    return Matcher(selector, options)


def css(selector: str, options: Matching | None = None, /) -> Matcher:
    """
    Compile a CSS selector into a :class:`Matcher`; a readable alias of :func:`compile`.

    :param selector: the CSS selector.
    :param options: the :class:`Matching` config, or ``None`` for soupsieve's defaults.
    :returns: a matcher bound to the selector.
    """
    return Matcher(selector, options)


def match(selector: str, node: Element, options: Matching | None = None, /) -> bool:
    """
    Test whether an element matches a selector, compiling it for this one call.

    :param selector: the CSS selector.
    :param node: the element to test.
    :param options: the :class:`Matching` config, or ``None`` for soupsieve's defaults.
    :returns: whether the element matches.
    """
    return Matcher(selector, options).match(node)


def select(
    selector: str, node: Element | Document, options: Matching | None = None, /, *, limit: int = 0
) -> list[Element]:
    """
    Collect the descendants of an element matching a selector, compiling it for this one call.

    :param selector: the CSS selector.
    :param node: the element or document whose descendants are searched.
    :param options: the :class:`Matching` config, or ``None`` for soupsieve's defaults.
    :param limit: the most matches to return, or ``0`` for all.
    :returns: the matching descendants in document order.
    """
    return Matcher(selector, options).select(node, limit)


def select_one(selector: str, node: Element | Document, options: Matching | None = None, /) -> Element | None:
    """
    Return the first descendant of an element matching a selector, compiling it for this one call.

    :param selector: the CSS selector.
    :param node: the element or document whose descendants are searched.
    :param options: the :class:`Matching` config, or ``None`` for soupsieve's defaults.
    :returns: the first match in document order, or ``None``.
    """
    return Matcher(selector, options).select_one(node)


def iselect(
    selector: str, node: Element | Document, options: Matching | None = None, /, *, limit: int = 0
) -> Iterator[Element]:
    """
    Iterate the descendants of an element matching a selector, compiling it for this one call.

    :param selector: the CSS selector.
    :param node: the element or document whose descendants are searched.
    :param options: the :class:`Matching` config, or ``None`` for soupsieve's defaults.
    :param limit: the most matches to yield, or ``0`` for all.
    :returns: an iterator over the matching descendants.
    """
    return Matcher(selector, options).iselect(node, limit)


def filter(  # noqa: A001  # the soupsieve entry-point name
    selector: str, iterable: Element | Document | Iterable[Element], options: Matching | None = None, /
) -> list[Element]:
    """
    Keep the members of an iterable matching a selector, compiling it for this one call.

    :param selector: the CSS selector.
    :param iterable: elements to test, or one element or document whose direct element children are tested.
    :param options: the :class:`Matching` config, or ``None`` for soupsieve's defaults.
    :returns: the members that match.
    """
    return Matcher(selector, options).filter(iterable)


def closest(selector: str, node: Element, options: Matching | None = None, /) -> Element | None:
    """
    Return the nearest self-or-ancestor of an element matching a selector, compiling it for this one call.

    :param selector: the CSS selector.
    :param node: the element to walk up from.
    :param options: the :class:`Matching` config, or ``None`` for soupsieve's defaults.
    :returns: the nearest matching element, or ``None``.
    """
    return Matcher(selector, options).closest(node)


__all__ = [
    "DEBUG",
    "Matcher",
    "Matching",
    "SelectorSyntaxError",
    "closest",
    "compile",
    "css",
    "escape_identifier",
    "filter",
    "iselect",
    "match",
    "select",
    "select_one",
]
