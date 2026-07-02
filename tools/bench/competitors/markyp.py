"""markyp: assemble a tree with element classes taking children first and attributes as keywords."""

from __future__ import annotations

from markyp_html.lists import li, ul

REQUIREMENTS = ("markyp-html>=0.2306.2",)


def build_e(count: int) -> None:
    """Build a ``<ul>`` of rows with markyp-html's element classes and stringify it."""
    rows = [li(f"item {index}", class_="item", **{"data-i": str(index)}) for index in range(count)]
    _ = str(ul(*rows))


OPERATIONS = {"build-e": (build_e, "markyp")}
