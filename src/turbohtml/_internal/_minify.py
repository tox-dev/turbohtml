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

import sys
from typing import TYPE_CHECKING, Final, Literal

from ._jsminify import JSMinify
from ._native import Element, Minify, minify_document, parse, strip_comments_node
from ._native import minify_js as _minify_js
from ._render import Html

if TYPE_CHECKING:
    from turbohtml import Document

_DEFAULT: Final[Minify] = Minify()


def minify(html: str, options: Minify | None = None) -> str:
    """
    Minify an HTML document.

    Parse ``html`` into a tree and serialize it with the minifying layout, folding insignificant whitespace, omitting
    the start/end tags the WHATWG rules make optional, dropping redundant attribute quotes, and stripping comments. Each
    transform is round-trip safe, so the result reparses to the same tree and minifying is idempotent.

    The result also reparses to the source's tree with scripting on, where ``<noscript>`` content is raw text: that
    content is copied from the source, as is the markup after a ``<noscript>`` the two readings place differently, up
    to the first element both place alike. A document that cannot be parted this way comes back unchanged.

    :param html: the HTML document to minify.
    :param options: which folds to apply; ``None`` engages every transform (the :class:`~turbohtml.Minify` default).
    :returns: the minified HTML.
    """
    layout = options if options is not None else _DEFAULT
    minified, start = minify_document(html, layout, ())
    return minified if minified is not None else _minify_parted(html, layout, start)


def _minify_parted(html: str, layout: Minify, start: int) -> str:
    # A noscript that a browser running scripts reads as raw text, while the scripting-off tree puts its markup in
    # other places too. The tree alone cannot give both readings back, so the source is copied from the noscript's start
    # tag to the first element both readings place alike, and the result is kept only when it reparses to the source's
    # trees under both modes. Failing that, the copy runs to the end of the source; failing that too, the source is
    # returned as it is.
    config = Html(layout=layout)
    off, on = (parse(html, scripting=scripting, source_locations=True) for scripting in (False, True))
    # stripping comments leaves the element offsets the regions are worked out from
    expected = (_canonical(off, config, layout), _canonical(on, config, layout))
    placed = {_start_offset(element): _ancestry(element) for element in off.iter_elements()}
    closes = {
        location.start_tag.start_offset: location.end_tag.end_offset
        for element in on.iter_elements()
        if element.tag == "noscript"
        and (location := element.source_location) is not None
        and location.end_tag is not None
    }
    regions: list[tuple[int, int]] = []
    minified: str | None = None
    max_regions = 8
    while start >= 0 and len(regions) < max_regions:
        regions.append((start, _resume_offset(on, placed, closes.get(start))))
        minified, start = minify_document(html, layout, tuple(regions))
    if minified is not None and _reparses_to(minified, layout, config, expected):
        return minified
    if not regions:
        return html
    tail = minify_document(html, layout, ((regions[0][0], sys.maxsize),))[0]
    return tail if tail is not None and _reparses_to(tail, layout, config, expected) else html


def _canonical(document: Document, config: Html, layout: Minify) -> str:
    # The minified form of the tree markup parses to: two trees that differ only where minifying folds give the same
    # string. Comments go first, since a stripped one can still decide whether a neighboring end tag is omitted.
    return (strip_comments_node(document) if layout.strip_comments else document).serialize(config)


def _start_offset(element: Element) -> int:
    location = element.source_location
    return location.start_tag.start_offset if location is not None else -1


def _ancestry(element: Element) -> tuple[str, ...]:
    return tuple(ancestor.tag for ancestor in element.ancestors if isinstance(ancestor, Element))


def _resume_offset(on: Document, placed: dict[int, tuple[str, ...]], close: int | None) -> int:
    # The start tag offset of the first element past the scripting-on noscript's end tag that the scripting-off tree
    # builds under the same ancestors, or sys.maxsize when there is none.
    if close is None:
        return sys.maxsize
    return next(
        (
            offset
            for element in on.iter_elements()
            if (offset := _start_offset(element)) >= close and placed.get(offset) == _ancestry(element)
        ),
        sys.maxsize,
    )


def _reparses_to(candidate: str, layout: Minify, config: Html, expected: tuple[str, str]) -> bool:
    return all(
        _canonical(parse(candidate, scripting=scripting), config, layout) == tree
        for scripting, tree in zip((False, True), expected, strict=True)
    )


def minify_js(source: str, options: JSMinify | None = None, on_error: Literal["raise", "passthrough"] = "raise") -> str:
    """
    Minify a JavaScript source string, returning the shortest equivalent program.

    Every transform preserves semantics, so the result evaluates identically to ``source``.
    ``options`` defaults to the full pipeline (fold and mangle). A rewrite that would nest the tree past the depth
    limit the passes are sized for is skipped, so a very long or deeply nested input can keep more of its original
    form than a short one.

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


__all__ = [
    "JSMinify",
    "minify",
    "minify_js",
]
