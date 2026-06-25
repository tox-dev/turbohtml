"""
turbohtml.convert: translate between the two query languages turbohtml runs natively.

turbohtml is unusual in carrying both a CSS selector engine and an XPath 1.0 engine in one process, so it can compile a
CSS selector to the XPath that selects the same nodes without a second library. :func:`css_to_xpath` is the one-call
form; :class:`Translator` mirrors the shape of ``cssselect``'s ``GenericTranslator`` / ``HTMLTranslator`` so a port off
`cssselect <https://github.com/scrapy/cssselect>`__ swaps the import and keeps the ``translator.css_to_xpath(css)``
call. The emitted XPath uses HTML semantics -- element and attribute names are lower-cased and the HTML
case-insensitive attribute set compares case-insensitively -- matching turbohtml's own CSS engine, so the result runs
unchanged through :meth:`turbohtml.Node.xpath` or any XPath 1.0 processor.

The translatable subset matches what XPath 1.0 can express faithfully: type, universal, class, id, every attribute
operator, the descendant/child/adjacent/general-sibling combinators, and the structural pseudo-classes
(``:root``, ``:empty``, the ``first``/``last``/``only`` and ``nth`` families, and a compound ``:not()``). A selector
the engine parses but cannot translate -- a relational ``:has()``, an input-state pseudo-class, ``:lang()``, or an
``*-of-type`` without a concrete element type -- raises :class:`SelectorSyntaxError`.
"""

from __future__ import annotations

from ._html import _css_to_xpath

__all__ = [
    "HTMLTranslator",
    "SelectorError",
    "SelectorSyntaxError",
    "Translator",
    "css_to_xpath",
]


class SelectorError(Exception):
    """Base class for every error :func:`css_to_xpath` raises, mirroring ``cssselect.SelectorError``."""


class SelectorSyntaxError(SelectorError):
    """A selector that cannot be parsed, or that uses a construct with no XPath 1.0 translation."""


def css_to_xpath(selector: str, *, prefix: str = "descendant-or-self::") -> str:
    """
    Translate a CSS selector to an equivalent XPath 1.0 location-path union.

    :param selector: the CSS selector, with comma-separated alternatives allowed.
    :param prefix: the axis prepended to each alternative; the default ``descendant-or-self::`` searches the whole
        subtree of the context node (as ``cssselect`` does), while ``child::`` or ``""`` scope the match more tightly.
    :returns: the XPath expression selecting the same elements turbohtml's CSS engine matches.
    :raises SelectorSyntaxError: the selector is malformed or uses an untranslatable construct.
    """
    try:
        return _css_to_xpath(selector, prefix)
    except ValueError as exc:
        raise SelectorSyntaxError(str(exc)) from exc


class Translator:
    """
    A ``cssselect``-shaped translator: ``Translator().css_to_xpath(css)`` mirrors ``GenericTranslator``/
    ``HTMLTranslator`` so a port keeps its call sites. The translator is stateless and thread-safe; the optional
    ``prefix`` is a per-call argument, exactly as in ``cssselect``.
    """

    def css_to_xpath(self, css: str, prefix: str = "descendant-or-self::") -> str:
        """
        Translate ``css`` to XPath, delegating to :func:`css_to_xpath`.

        :param css: the CSS selector to translate.
        :param prefix: the axis prepended to each alternative (see :func:`css_to_xpath`).
        :returns: the equivalent XPath 1.0 expression.
        :raises SelectorSyntaxError: the selector is malformed or uses an untranslatable construct.
        """
        return css_to_xpath(css, prefix=prefix)


HTMLTranslator = Translator
"""Alias of :class:`Translator`, the name a ``cssselect.HTMLTranslator`` port imports; turbohtml always emits HTML
semantics, so the two are one class."""
