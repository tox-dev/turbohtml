"""airium: assemble a tree with context-manager tags and indentation tracking."""

from __future__ import annotations

from airium import Airium

REQUIREMENTS = ("airium>=0.2.7",)


def build_e(count: int) -> None:
    """Build a ``<ul>`` of rows with airium's context-manager tags and stringify it."""
    air = Airium()
    with air.ul():
        for index in range(count):
            with air.li(klass="item", **{"data-i": str(index)}):
                air(f"item {index}")
    _ = str(air)


OPERATIONS = {"build-e": (build_e, "airium")}
