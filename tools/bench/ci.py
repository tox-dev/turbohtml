"""
Real, offline benchmark cases for the CodSpeed CI regression gate.

The pyperf suite times every operation in :data:`bench.core.OPERATIONS` over real corpora (see :mod:`bench.corpus`).
:func:`benchmarks` walks that same registry and pairs each turbohtml operation with a lazy loader for one of those real
inputs -- the vendored html5lib-python and War-and-Peace corpora for the markup and text operations, the pinned upstream
stylesheet and JS library for the minifiers, and the bench's own inline cases for the rest. Both suites therefore share
the operation list and the corpus, so a new operation added to ``OPERATIONS`` is benchmarked here automatically. The
loaders are lazy so the corpus is read only when a benchmark runs (under ``--codspeed``); a case whose corpus is missing
raises, and the test skips it.

Operations whose inline case is too small to clear Valgrind's heap-jitter floor are re-homed on a large real corpus and
surfaced under a corpus-qualified benchmark id (see :data:`_RESIZED`), so the noise-prone identity retires and a stable
one takes its place without CodSpeed reading the input change as a regression.
"""

from __future__ import annotations

from functools import cache, partial
from typing import TYPE_CHECKING

from bench import corpus
from bench.core import OPERATIONS
from bench.operations import INPUTS

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

_TEXT_BYTES = 1 << 16  # a 64 kB slice of the book: representative work without a multi-megabyte parse under Valgrind


@cache
def _spec() -> str:
    """Return the vendored whatwg HTML spec (235 kB): the read-path operations' shared real document."""
    _name, relative, encoding = corpus.CORPUS_FILES[5]
    return corpus.corpus_text(relative, encoding)


def _xpath_case() -> tuple[str, str]:
    """Return the ``(kind, document)`` pair the xpath operation expects, over the spec."""
    return "//a[@href]", _spec()


# read-path, parse and tokenize all run over one shared real document (bench.core caches the parse), so they measure the
# query, not a re-parse; xpath, the text corpora and the minifier corpora load their own real inputs.
_DOCUMENT_OPS = (
    "parse",
    "tokenize",
    "find",
    "select",
    "select-has",
    "match",
    "find-text",
    "text-content",
    "serialize",
    "minify",
    "edit",
    "class-edit",
    "strip-remove",
    "strip-tags",
    "set-html",
    "set-text",
    "navigate",
    "chain",
    "links-extract",
    "links-absolutize",
    "links-rewrite",
    "extract-attr",
    "extract-text",
    "htmlparser",
    "path",
    "path-xpath",
    "links-filter",
)

_LOADERS: dict[str, Callable[[], object]] = dict.fromkeys(_DOCUMENT_OPS, _spec)
_LOADERS["xpath"] = _xpath_case
_LOADERS["escape"] = partial(corpus.corpus, "war-and-peace/2600.txt", _TEXT_BYTES)
_LOADERS["unescape"] = partial(corpus.corpus, "war-and-peace/2600-h/2600-h.htm", _TEXT_BYTES)
_LOADERS["encoding"] = lambda: corpus.corpus("war-and-peace/2600.txt", _TEXT_BYTES).encode("cp1252")
_LOADERS["normalize"] = partial(corpus.corpus, "war-and-peace/2600.txt", _TEXT_BYTES)
_LOADERS["minify-css"] = partial(corpus.large_text, *corpus.STYLESHEETS[1][1:])  # pico.css (90 kB)
_LOADERS["minify-js"] = partial(corpus.large_text, *corpus.JS_FILES[0][1:])  # underscore (67 kB)


def _spec_case(kind: str) -> tuple[str, str]:
    """Wrap the spec in the ``(kind, document)`` pair a tuple-shaped operation expects."""
    return kind, _spec()


def _book_text() -> str:
    """Return a War-and-Peace plain-text slice: the string operations' large real input."""
    return corpus.corpus("war-and-peace/2600.txt", _TEXT_BYTES)


def _book_html() -> str:
    """Return a War-and-Peace HTML slice: the markup operations' large real input."""
    return corpus.corpus("war-and-peace/2600-h/2600-h.htm", _TEXT_BYTES)


# An inline case is too few instructions under Valgrind to clear the heap-layout jitter floor, so it swings >5%
# run-to-run (text-main and boilerplate tripped the required CodSpeed check on unrelated PRs). Re-home each on a large
# real corpus under a new corpus-qualified id: the flaky small id retires with no base and the large one is gated
# fresh, so the input change never reads as a regression. The synthetic tree builders, the fragment parser, the
# single-selector translate, and the fixed URL batch have no document corpus to grow into and stay inline.
_RESIZED: dict[str, tuple[str, Callable[[], object]]] = {
    "socialcard": ("socialcard-spec", _spec),
    "structured": ("structured-spec", _spec),
    "sanitize": ("sanitize-spec", _spec),
    "sanitize-templates": ("sanitize-templates-spec", _spec),
    "sanitize-report": ("sanitize-report-spec", _spec),
    "linkify": ("linkify-spec", _spec),
    "markdown-google": ("markdown-google-spec", _spec),
    "article": ("article-spec", _spec),
    "boilerplate": ("boilerplate-spec", _spec),
    "date": ("date-spec", _spec),
    "text-render": ("text-render-spec", _spec),
    "text-collapsed": ("text-collapsed-spec", _spec),
    "text-main": ("text-main-spec", _spec),
    "text-annotated": ("text-annotated-spec", _spec),
    "markdown": ("markdown-spec", partial(_spec_case, "default")),
    "tables": ("tables-spec", partial(_spec_case, "rows")),
    "extract-url": ("extract-url-spec", partial(_spec_case, "base")),
    "markup": ("markup-book", _book_text),
    "markup-op": ("markup-op-book", lambda: ("striptags", _book_html())),
    "detect": ("detect-book", lambda: ("find", _book_text())),
}


def _inline(operation: str) -> object:
    """Return the first case the bench already defines inline for ``operation`` (no corpus needed)."""
    return INPUTS[operation]()[0][1]


def loader_for(operation: str) -> Callable[[], object]:
    """Return the lazy loader for ``operation``: a resized real corpus, the spec, or the bench's own inline case."""
    if operation in _RESIZED:
        return _RESIZED[operation][1]
    return _LOADERS.get(operation, partial(_inline, operation))


def benchmarks() -> Iterator[tuple[str, object, Callable[[], object]]]:
    """
    Yield ``(name, callable, load)`` for every turbohtml operation, in registry order.

    A noise-prone inline operation is surfaced under its corpus-qualified id (see :data:`_RESIZED`) so CodSpeed
    retires the flaky small-input benchmark and gates the large-input one as a fresh identity.
    """
    for name, (run, _owner) in OPERATIONS.items():
        identity = _RESIZED[name][0] if name in _RESIZED else name
        yield identity, run, loader_for(name)


__all__ = [
    "benchmarks",
    "loader_for",
]
