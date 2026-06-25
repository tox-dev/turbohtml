"""simple-html: assemble a tree with a leading attribute dict and render it with the module ``render``."""

from __future__ import annotations

from simple_html import li, render, ul

REQUIREMENTS = ("simple-html>=3.1.1",)


def build_e(count: int) -> None:
    """Build a ``<ul>`` of rows with simple-html's dict-first tags and render it to a string."""
    rows = [li({"class": "item", "data-i": str(index)}, f"item {index}") for index in range(count)]
    _ = render(ul(rows))


OPERATIONS = {"build-e": (build_e, "simple-html")}
