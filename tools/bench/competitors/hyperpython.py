"""hyperpython: assemble a tree with keyword attributes and subscript children."""

from __future__ import annotations

from hyperpython import li, ul

# sidekick 0.7+ registers typing.Mapping with functools.singledispatch, a TypeError on Python 3.11+
REQUIREMENTS = ("hyperpython>=1.1.1", "sidekick<0.7")


def build_e(count: int) -> None:
    """Build a ``<ul>`` of rows with hyperpython's subscript-children syntax and stringify it."""
    rows = [li(class_="item", data_i=str(index))[f"item {index}"] for index in range(count)]
    _ = str(ul(rows))


OPERATIONS = {"build-e": (build_e, "hyperpython")}
