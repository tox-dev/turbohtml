"""htpy: assemble a tree with subscript children and keyword attributes."""

from __future__ import annotations

import htpy

REQUIREMENTS = ("htpy>=26.5.1",)


def build_e(count: int) -> None:
    """Build a ``<ul>`` of rows with htpy's subscript-children syntax and stringify it."""
    rows = [htpy.li(class_="item", data_i=str(index))[f"item {index}"] for index in range(count)]
    _ = str(htpy.ul[rows])


OPERATIONS = {"build-e": (build_e, "htpy")}
