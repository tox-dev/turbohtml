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

from typing import Final, Literal

from turbohtml._html import Minify, _minify_js, parse

from ._jsminify import JSMinify
from ._render import Html

__all__ = ["JSMinify", "Minify", "minify", "minify_js"]

_DEFAULT: Final = Minify()


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


def minify_js(source: str, options: JSMinify | None = None, on_error: Literal["raise", "passthrough"] = "raise") -> str:
    """
    Minify a JavaScript source string, returning the shortest equivalent program.

    Every transform preserves semantics, so the result evaluates identically to ``source``.
    ``options`` defaults to the full pipeline (fold and mangle).

    ``on_error`` chooses what happens when the parser cannot handle the script. The default
    ``"raise"`` fails loudly; ``"passthrough"`` returns ``source`` unchanged instead, the
    never-fail contract ``jsmin`` and ``rjsmin`` guarantee -- the same verbatim fallback the
    inline-``<script>`` path already applies so one bad script cannot break serialization.

    :param source: the JavaScript source to minify.
    :param options: which optional passes to run; ``None`` runs the full pipeline.
    :param on_error: ``"raise"`` (default) to raise on a script the parser cannot handle, or
        ``"passthrough"`` to return ``source`` unchanged.
    :returns: the minified JavaScript, or ``source`` verbatim when it is unparsable and
        ``on_error="passthrough"``.
    :raises TypeError: if ``source`` is not a :class:`str`.
    :raises ValueError: if ``on_error`` is not ``"raise"`` or ``"passthrough"``, or -- when
        ``on_error="raise"`` -- if the parser cannot handle the script (a lexical or syntax error,
        or input nested past the depth limit), with the message naming the construct, its byte
        offset, and the offending token.
    """
    return _minify_js(source, (config := options or JSMinify()).fold, config.mangle, on_error)
