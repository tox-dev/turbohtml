"""simple-html: assemble a tree with attribute-dict-first tag calls and ``render``."""

from __future__ import annotations

import functools

from simple_html import Node, li, render, ul

REQUIREMENTS = ("simple-html>=3.1.1",)


def build_e(count: int) -> None:
    """Build a ``<ul>`` of rows with simple-html's attribute-dict-first calls and render it."""
    rows = [li({"class": "item", "data-i": str(index)}, f"item {index}") for index in range(count)]
    _ = render(ul(*rows))


def construct(count: int) -> None:
    """Construct ``count`` simple-html elements with attributes and text, in isolation from rendering."""
    for index in range(count):
        li({"class": "item", "data-i": str(index)}, f"item {index}")


@functools.cache
def _tree(count: int) -> Node:
    """Return a built ``<ul>`` of ``count`` rows, cached so ``render`` times only the emit step."""
    return ul(*[li({"class": "item", "data-i": str(index)}, f"item {index}") for index in range(count)])


def emit(count: int) -> None:
    """Render a pre-built ``count``-row tree, in isolation from construction."""
    _ = render(_tree(count))


OPERATIONS = {
    "build-e": (build_e, "simple-html"),
    "construct": (construct, "simple-html"),
    "emit": (emit, "simple-html"),
}
