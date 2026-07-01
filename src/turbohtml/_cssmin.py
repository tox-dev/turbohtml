"""
Minify CSS to the smallest output that still parses to the same cascade.

The other CSS minifiers on PyPI shrink stylesheets either by stripping whitespace (``rcssmin``) or by rewriting values
(``csscompressor``, ``cssmin``), but each of them rewrites the *internal* whitespace of a custom-property value, which
CSS Variables 1 §3 says to preserve; ``cssmin`` and ``css-html-js-minify`` also collapse whitespace inside strings.
turbohtml applies every value-rewriting optimization that is provably value-safe -- colors collapse to their shortest
form, numbers and units drop what is redundant, constant ``calc()`` folds, shorthands and adjacent rules merge -- while
keeping custom-property values and string contents byte-exact, so it is the smallest output that round-trips.

The whole tokenizer, grammar, and value engine run in C (``turbohtml._html._minify_css``), working directly on the
input's UTF-8 bytes. Every transform is value-safe at any baseline; the baseline only bounds how new the output
*syntax* may be, so the result always parses to the same cascade as the input.
"""

from __future__ import annotations

from dataclasses import dataclass

from ._html import _minify_css, _minify_css_inline

__all__ = [
    "CSSMinify",
    "minify_css",
    "minify_css_inline",
]


@dataclass(frozen=True, slots=True)
class CSSMinify:
    """
    Options for :func:`minify_css` and :func:`minify_css_inline`.

    ``baseline`` is the `Baseline <https://web.dev/baseline>`__ year the output may target: the minifier applies a
    transform whose output syntax reached Baseline in year ``Y`` only when ``baseline >= Y``. ``None`` (the default)
    emits only long-interoperable syntax; ``2021`` additionally merges the shorthands that reached Baseline that year
    (``inset``, the flex ``gap``, the two-value ``overflow``). Every year is value-safe -- the year bounds only how new
    the output syntax may be, never the cascade it parses to.
    """

    baseline: int | None = None


def minify_css(css: str, options: CSSMinify | None = None) -> str:
    """
    Minify a full CSS stylesheet.

    :param css: the stylesheet source (rules, at-rules, comments).
    :param options: the minification options; defaults to :class:`CSSMinify` (the most compatible output).
    :returns: the minified stylesheet.
    """
    return _minify_css(css, (options or CSSMinify()).baseline or 0)


def minify_css_inline(css: str, options: CSSMinify | None = None) -> str:
    """
    Minify an inline declaration list, the value of an HTML ``style`` attribute.

    Use this rather than :func:`minify_css` when the source is bare declarations (``color:red; margin:0``) with no
    surrounding selector or braces.

    :param css: the declaration-list source.
    :param options: the minification options; defaults to :class:`CSSMinify` (the most compatible output).
    :returns: the minified declaration list.
    """
    return _minify_css_inline(css, (options or CSSMinify()).baseline or 0)
