"""
hyperpython: assemble a tree with keyword attributes and subscript children.

Benchmarked only in a venv of its own: hyperpython pins ``markupsafe<2``, older than the rest of the suite, and its
``sidekick`` dependency needs ``<0.7`` (0.7+ registers ``typing.Mapping`` with ``functools.singledispatch``, a
``TypeError`` at import on Python 3.11+).
"""

from __future__ import annotations

from hyperpython import li, ul  # ty: ignore[unresolved-import]  # undeclared: pins an older markupsafe

REQUIREMENTS = ("hyperpython>=1.1.1", "sidekick<0.7")


def build_e(count: int) -> None:
    """Build a ``<ul>`` of rows with hyperpython's subscript-children syntax and stringify it."""
    rows = [li(class_="item", data_i=str(index))[f"item {index}"] for index in range(count)]
    _ = str(ul(rows))


OPERATIONS = {"build-e": (build_e, "hyperpython")}
