"""
The public HTML and JavaScript minifier entry points and the JavaScript options object.

:func:`minify` parses an HTML document and serializes it back through the shipped C
minifier (the :class:`~turbohtml.Minify` layout), the one-call convenience ``minify-html``
and ``htmlmin`` expose; every transform is round-trip safe, so the output reparses to the
same tree. :func:`minify_js` is the separate JavaScript minifier: the C extension exposes
only the numeric seam ``_minify_js(source, fold, mangle)``, and this layer gives it a typed,
defaulted surface plus the :class:`JSMinify` options object that the HTML serializer's
``Minify(minify_js=...)`` hook also accepts, so the standalone call and the
inline-``<script>`` path are configured the same way.
"""

from __future__ import annotations

from ._html import Minify, _minify_js, parse
from ._jsminify import JSMinify
from ._render import Html

__all__ = ["JSMinify", "Minify", "minify", "minify_js"]

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


def minify_js(source: str, options: JSMinify | None = None) -> str:
    """
    Minify a JavaScript source string, returning the shortest equivalent program.

    Raises :class:`ValueError` (carrying the byte offset) on a construct the parser does
    not handle, so an unminifiable script fails loudly rather than passing through
    unchanged. ``options`` defaults to the full pipeline.
    """
    return _minify_js(source, (config := options or JSMinify()).fold, config.mangle)
