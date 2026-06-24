"""dominate: assemble a tree with tag objects and ``add``."""

from __future__ import annotations

from dominate import tags

REQUIREMENTS = ("dominate>=2.9.1",)


def build_e(count: int) -> None:
    """Build a ``<ul>`` of rows with dominate's tag objects and render it."""
    ul = tags.ul()
    for index in range(count):
        ul.add(tags.li(f"item {index}", **{"class": "item", "data-i": str(index)}))
    _ = ul.render(pretty=False)


OPERATIONS = {"build-e": (build_e, "dominate")}
