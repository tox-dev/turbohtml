"""
Minify CSS to the smallest output that still parses to the same cascade.

The Python minifiers (``rcssmin``, ``csscompressor``) are non-destructive byte rewriters: they strip comments and
whitespace but never touch values, so ``#ffffff`` and ``0.5px`` survive untouched. turbohtml ports tdewolff/minify's
value-rewriting algorithm into C and then goes past it with optimizations that are *value-safe* -- they produce a
stylesheet that parses to the same cascade -- so colors collapse to their shortest hex, numbers drop redundant zeros
and units, ``calc()`` with constant operands is folded, and adjacent equal-bodied rules merge. The result is the
smallest round-trip-safe output of the CSS minifiers on PyPI.

This module is the policy-free facade; the whole tokenizer, grammar, and value engine run in C
(``turbohtml._html._minify_css``), working directly on the input's UTF-8 bytes. There are no options to tune: every
transformation is one whose output is guaranteed equivalent to the input, so a bare :func:`minify_css` is safe by
construction.
"""

from __future__ import annotations

from ._html import _minify_css, _minify_css_inline


def minify_css(css: str) -> str:
    """
    Minify a full CSS stylesheet.

    :param css: the stylesheet source (rules, at-rules, comments).
    :returns: the minified stylesheet.
    """
    return _minify_css(css)


def minify_css_inline(css: str) -> str:
    """
    Minify an inline declaration list, the value of an HTML ``style`` attribute.

    Use this rather than :func:`minify_css` when the source is bare declarations (``color:red; margin:0``) with no
    surrounding selector or braces.

    :param css: the declaration-list source.
    :returns: the minified declaration list.
    """
    return _minify_css_inline(css)


__all__ = [
    "minify_css",
    "minify_css_inline",
]
