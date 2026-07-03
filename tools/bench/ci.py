"""
Real, offline benchmark cases for the CodSpeed CI regression gate.

The pyperf suite times every operation in :data:`bench.core.OPERATIONS` over real corpora (see :mod:`bench.corpus`).
:func:`benchmarks` walks that same registry and pairs each turbohtml operation with a lazy loader for one of those real
inputs -- the vendored html5lib-python and War-and-Peace corpora for the markup and text operations, the pinned upstream
stylesheet and JS library for the minifiers, and the bench's own inline cases for the rest. Both suites therefore share
the operation list and the corpus, so a new operation added to ``OPERATIONS`` is benchmarked here automatically. The
loaders are lazy so the corpus is read only when a benchmark runs (under ``--codspeed``); a case whose corpus is missing
raises, and the test skips it.
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
_LOADERS["minify-css"] = partial(corpus.large_text, *corpus.STYLESHEETS[1][1:])  # pico.css (90 kB)
_LOADERS["minify-js"] = partial(corpus.large_text, *corpus.JS_FILES[0][1:])  # underscore (67 kB)


def _inline(operation: str) -> object:
    """Return the first case the bench already defines inline for ``operation`` (no corpus needed)."""
    return INPUTS[operation]()[0][1]


def loader_for(operation: str) -> Callable[[], object]:
    """Return the lazy loader for ``operation``: a real corpus reader, or the bench's own inline case."""
    return _LOADERS.get(operation, partial(_inline, operation))


def benchmarks() -> Iterator[tuple[str, object, Callable[[], object]]]:
    """Yield ``(name, callable, load)`` for every turbohtml operation, in registry order."""
    for name, (run, _owner) in OPERATIONS.items():
        yield name, run, loader_for(name)


__all__ = [
    "benchmarks",
    "loader_for",
]
