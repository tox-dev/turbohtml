"""htpy: assemble a tree with subscript children and keyword attributes."""

from __future__ import annotations

import functools

import htpy

REQUIREMENTS = ("htpy>=26.5.1",)


def build_e(count: int) -> None:
    """Build a ``<ul>`` of rows with htpy's subscript-children syntax and stringify it."""
    rows = [htpy.li(class_="item", data_i=str(index))[f"item {index}"] for index in range(count)]
    _ = str(htpy.ul[rows])


def construct(count: int) -> None:
    """Construct ``count`` htpy elements with attributes and children, in isolation from stringification."""
    for index in range(count):
        htpy.li(class_="item", data_i=str(index))[f"item {index}"]


@functools.cache
def _tree(count: int) -> htpy.Element:
    """Return a built ``<ul>`` of ``count`` rows, cached so ``str`` times only the emit step."""
    return htpy.ul[[htpy.li(class_="item", data_i=str(index))[f"item {index}"] for index in range(count)]]


def emit(count: int) -> None:
    """Stringify a pre-built ``count``-row tree, in isolation from construction."""
    _ = str(_tree(count))


OPERATIONS = {
    "build-e": (build_e, "htpy"),
    "construct": (construct, "htpy"),
    "emit": (emit, "htpy"),
}
