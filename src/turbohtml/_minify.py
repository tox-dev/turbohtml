"""
Minify HTML the way ``minify-html`` and ``htmlmin`` did, by parsing then serializing through the shipped C minifier.

The minifier already lives in ``_c/serialize/minify.c`` and is reachable as the :class:`~turbohtml.Minify` layout passed
to :meth:`~turbohtml.Node.serialize`; this module is the one-call convenience those libraries expose. The model is the
one every safe minifier converged on: parse the input into a real WHATWG tree, then emit it once with the folds engaged,
so every transform is round-trip safe -- the output reparses to the same tree. That is the property the acceptance gate
checks as idempotence: ``minify(minify(x)) == minify(x)``.

This module is a thin facade; the whitespace fold, optional-tag omission, attribute unquoting, and comment stripping all
run in C below :func:`minify`, configured by the existing immutable :class:`~turbohtml.Minify` options object.
"""

from __future__ import annotations

from ._html import Minify, parse
from ._render import Html

_DEFAULT = Minify()


def minify(html: str, options: Minify | None = None) -> str:
    """
    Minify an HTML document.

    Parse ``html`` into a tree and serialize it with the minifying layout, folding insignificant whitespace, omitting
    the start/end tags the WHATWG rules make optional, dropping redundant attribute quotes, and stripping comments. Each
    transform is round-trip safe, so the result reparses to the same tree and minifying is idempotent.

    :param html: the HTML document to minify.
    :param options: which folds to apply; ``None`` engages every transform (the :class:`~turbohtml.Minify` default).
    :returns: the minified HTML.
    """
    return parse(html).serialize(Html(layout=options if options is not None else _DEFAULT))


__all__ = ["Minify", "minify"]
