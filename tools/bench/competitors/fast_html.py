"""fast-html: assemble a generator tree with children-first tag calls and ``render``."""

from __future__ import annotations

from fast_html import li, render, ul

REQUIREMENTS = ("fast-html>=1.0.12",)


def build_e(count: int) -> None:
    """Build a ``<ul>`` of rows with fast-html's generator tags and render it."""
    rows = [li(f"item {index}", class_="item", data_i=str(index)) for index in range(count)]
    _ = render(ul(rows))


OPERATIONS = {"build-e": (build_e, "fast-html")}
