"""htbuilder: assemble a tree with one call for attributes and a second for children."""

from __future__ import annotations

from htbuilder import li, ul

REQUIREMENTS = ("htbuilder>=0.9",)


def build_e(count: int) -> None:
    """Build a ``<ul>`` of rows with htbuilder's attributes-then-children calls and stringify it."""
    rows = [li(_class="item", data_i=str(index))(f"item {index}") for index in range(count)]
    _ = str(ul(*rows))


OPERATIONS = {"build-e": (build_e, "htbuilder")}
