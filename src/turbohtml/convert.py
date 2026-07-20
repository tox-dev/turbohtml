"""
turbohtml.convert: translate between the query languages turbohtml speaks.

:func:`css_to_xpath` turns a CSS selector list into an equivalent XPath 1.0 expression, replacing `cssselect
<https://github.com/scrapy/cssselect>`__ (the translator behind lxml's ``cssselect()``, parsel, and pyquery). turbohtml
runs both a CSS selector engine and an XPath engine in-process, so the translation is a C pass over the parsed
selector, and a differential suite checks the output selects the same nodes as the native CSS engine. The emitted
string is not cssselect's: every predicate is context-free, and the HTML semantics are the ones turbohtml's own engine
implements (the WHATWG case-insensitive attribute set, Selectors 4 ``:empty``, the exact ``fieldset``/``legend`` rule
for ``:disabled``).

:class:`GenericTranslator` and :class:`HTMLTranslator` mirror cssselect's classes so ports are mechanical; both
delegate to the same engine, which always applies the HTML rules (element and attribute names lowercase).
"""

from __future__ import annotations

from ._html import _css_specificity, _css_to_xpath
from ._internal._selectors import SelectorSyntaxError

__all__ = [
    "ExpressionError",
    "GenericTranslator",
    "HTMLTranslator",
    "SelectorError",
    "SelectorSyntaxError",
    "css_specificity",
    "css_to_xpath",
]


class SelectorError(Exception):
    """Parent of :class:`ExpressionError`, mirroring cssselect's error base for a valid-but-untranslatable selector."""


class ExpressionError(SelectorError, RuntimeError):
    """The selector is valid CSS but has no XPath 1.0 equivalent (for example ``:dir()`` or ``*:first-of-type``)."""


def css_to_xpath(selector: str, *, prefix: str = "descendant-or-self::") -> str:
    """
    Translate a CSS selector list to an equivalent XPath 1.0 expression.

    The XPath selects the same nodes as the selector does under :meth:`turbohtml.Node.select`; a comma-separated list
    becomes a ``|`` union with the prefix prepended to each arm.

    :param selector: the CSS selector list to translate.
    :param prefix: prepended to each arm; the default scopes the expression to the context node's subtree, matching
        cssselect.
    :raises SelectorSyntaxError: the selector does not parse.
    :raises ExpressionError: the selector parses but cannot be expressed in XPath 1.0.
    :returns: the XPath 1.0 expression.
    """
    try:
        return _css_to_xpath(selector, prefix)
    except NotImplementedError as error:
        raise ExpressionError(str(error)) from None


def css_specificity(selector: str) -> list[tuple[int, int, int]]:
    """
    Return the specificity of each selector in a comma-separated list.

    Each triple is ``(a, b, c)`` per `CSS Selectors Level 4 §17
    <https://www.w3.org/TR/selectors-4/#specificity-rules>`_: ``a`` counts id selectors, ``b`` counts class, attribute,
    and pseudo-class selectors, and ``c`` counts type and pseudo-element selectors. ``:is()``, ``:not()``, and
    ``:has()`` take the specificity of their most specific argument; ``:where()`` contributes zero. The list is one
    triple per comma-separated selector, the shape ``cssselect``'s ``Selector.specificity()`` returns per parsed arm.

    :param selector: the CSS selector list to weigh.
    :raises SelectorSyntaxError: the selector does not parse.
    :returns: one ``(a, b, c)`` triple per selector in the list, in source order.
    """
    return _css_specificity(selector)


class GenericTranslator:
    """
    A cssselect-shaped translator: :meth:`css_to_xpath` is a method here, as in cssselect.

    Unlike cssselect's generic (XML) translator, the translation always follows the HTML rules turbohtml's selector
    engine implements: element and attribute names are matched lowercase, and the WHATWG case-insensitive attribute set
    compares values case-insensitively.
    """

    def css_to_xpath(self, css: str, prefix: str = "descendant-or-self::") -> str:  # ruff:ignore[no-self-use]  # cssselect shape needs an instance method
        """
        Translate a CSS selector list to an equivalent XPath 1.0 expression.

        :param css: the CSS selector list to translate.
        :param prefix: prepended to each arm of the translated union.
        :raises SelectorSyntaxError: the selector does not parse.
        :raises ExpressionError: the selector parses but cannot be expressed in XPath 1.0.
        :returns: the XPath 1.0 expression.
        """
        return css_to_xpath(css, prefix=prefix)


class HTMLTranslator(GenericTranslator):
    """
    The cssselect ``HTMLTranslator`` shape.

    :param xhtml: accepted for signature compatibility with cssselect; the translation applies the HTML lowercasing
        rules either way, because that is what turbohtml's parser and selector engine do.
    """

    def __init__(self, xhtml: bool = False) -> None:  # ruff:ignore[boolean-type-hint-positional-argument, boolean-default-value-positional-argument]  # positional bool mirrors cssselect
        """Record the cssselect-compatible ``xhtml`` flag."""
        self.xhtml = xhtml
