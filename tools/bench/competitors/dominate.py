"""dominate: assemble a tree with tag objects and ``add``."""

from __future__ import annotations

import functools

from dominate import tags
from dominate.util import escape as _escape
from dominate.util import unescape as _unescape

REQUIREMENTS = ("dominate>=2.9.1",)


def build_e(count: int) -> None:
    """Build a ``<ul>`` of rows with dominate's tag objects and render it."""
    ul = tags.ul()
    for index in range(count):
        ul.add(tags.li(f"item {index}", **{"class": "item", "data-i": str(index)}))
    _ = ul.render(pretty=False)


def construct(count: int) -> None:
    """Construct ``count`` dominate tag objects with attributes and text, in isolation from rendering."""
    for index in range(count):
        tags.li(f"item {index}", **{"class": "item", "data-i": str(index)})


@functools.cache
def _tree(count: int) -> tags.ul:
    """Return a built ``<ul>`` of ``count`` rows, cached so ``render`` times only the emit step."""
    return tags.ul(*[tags.li(f"item {index}", **{"class": "item", "data-i": str(index)}) for index in range(count)])


def emit(count: int) -> None:
    """Render a pre-built ``count``-row tree, in isolation from construction."""
    _ = _tree(count).render(pretty=False)


def escape(text: str) -> None:
    """Escape text with dominate's util.escape."""
    _escape(text)


def unescape(text: str) -> None:
    """Resolve character references with dominate's util.unescape."""
    _unescape(text)


OPERATIONS = {
    "build-e": (build_e, "dominate"),
    "construct": (construct, "dominate"),
    "emit": (emit, "dominate"),
    "escape": (escape, "dominate"),
    "unescape": (unescape, "dominate"),
}
