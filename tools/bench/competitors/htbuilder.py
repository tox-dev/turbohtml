"""htbuilder: assemble a tree with one call for attributes and a second for children."""

from __future__ import annotations

import functools

from htbuilder import li, ul

REQUIREMENTS = ("htbuilder>=0.9",)


def build_e(count: int) -> None:
    """Build a ``<ul>`` of rows with htbuilder's attributes-then-children calls and stringify it."""
    rows = [li(_class="item", data_i=str(index))(f"item {index}") for index in range(count)]
    _ = str(ul(*rows))


def construct(count: int) -> None:
    """Construct ``count`` htbuilder elements with attributes and children, in isolation from stringification."""
    for index in range(count):
        li(_class="item", data_i=str(index))(f"item {index}")


@functools.cache
def _tree(count: int) -> object:
    """Return a built ``<ul>`` of ``count`` rows, cached so ``str`` times only the emit step."""
    return ul(*[li(_class="item", data_i=str(index))(f"item {index}") for index in range(count)])


def emit(count: int) -> None:
    """Stringify a pre-built ``count``-row tree, in isolation from construction."""
    _ = str(_tree(count))


OPERATIONS = {
    "build-e": (build_e, "htbuilder"),
    "construct": (construct, "htbuilder"),
    "emit": (emit, "htbuilder"),
}
