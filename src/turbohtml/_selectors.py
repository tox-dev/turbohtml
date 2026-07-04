"""
The SelectorSyntaxError the CSS selector engine raises on a malformed selector.

Every selector-parse path shares this one type: the native :meth:`~turbohtml.Node.select`,
:meth:`~turbohtml.Node.matches`, and :meth:`~turbohtml.Node.closest`; the soupsieve-shaped
:mod:`turbohtml.match` facade; and the :func:`turbohtml.convert.css_to_xpath` translator. The
engine raises it from C, so importing this module registers the type with the core.
"""

from __future__ import annotations

from ._html import _register_selector_error

__all__ = ["SelectorSyntaxError"]


class SelectorSyntaxError(ValueError):
    """
    A CSS selector could not be parsed.

    Raised by :meth:`~turbohtml.Node.select`, :meth:`~turbohtml.Node.matches`, and
    :meth:`~turbohtml.Node.closest`, by the :mod:`turbohtml.match` helpers, and by
    :func:`turbohtml.convert.css_to_xpath`. It subclasses :class:`ValueError`, so code catching
    ``ValueError`` keeps working while a soupsieve port can catch it by its soupsieve name.
    """


_register_selector_error(SelectorSyntaxError)
