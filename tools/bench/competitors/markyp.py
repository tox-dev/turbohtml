"""markyp: assemble a tree with element classes taking children first and attributes as keywords."""

from __future__ import annotations

import functools

from markyp_html.lists import li, ul

REQUIREMENTS = ("markyp-html>=0.2306.2",)


def build_e(count: int) -> None:
    """Build a ``<ul>`` of rows with markyp-html's element classes and stringify it."""
    rows = [li(f"item {index}", class_="item", **{"data-i": str(index)}) for index in range(count)]
    _ = str(ul(*rows))


def construct(count: int) -> None:
    """Construct ``count`` markyp-html elements with attributes and text, in isolation from stringification."""
    for index in range(count):
        li(f"item {index}", class_="item", **{"data-i": str(index)})


@functools.cache
def _tree(count: int) -> ul:
    """Return a built ``<ul>`` of ``count`` rows, cached so ``str`` times only the emit step."""
    return ul(*[li(f"item {index}", class_="item", **{"data-i": str(index)}) for index in range(count)])


def emit(count: int) -> None:
    """Stringify a pre-built ``count``-row tree, in isolation from construction."""
    _ = str(_tree(count))


OPERATIONS = {
    "build-e": (build_e, "markyp"),
    "construct": (construct, "markyp"),
    "emit": (emit, "markyp"),
}
