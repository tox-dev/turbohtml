"""simple-html: assemble a tree with attribute-dict-first tag calls and ``render``."""

from __future__ import annotations

from simple_html import li, render, ul

REQUIREMENTS = ("simple-html>=3.1.1",)


def build_e(count: int) -> None:
    """Build a ``<ul>`` of rows with simple-html's attribute-dict-first calls and render it."""
    rows = [li({"class": "item", "data-i": str(index)}, f"item {index}") for index in range(count)]
    _ = render(ul(*rows))


OPERATIONS = {"build-e": (build_e, "simple-html")}
