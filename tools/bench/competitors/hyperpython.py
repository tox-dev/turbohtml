"""
hyperpython: assemble a tree with keyword attributes and subscript children.

Benchmarked only in a venv of its own: hyperpython pins ``markupsafe<2``, older than the rest of the suite, and its
``sidekick`` dependency needs ``<0.7`` (0.7+ registers ``typing.Mapping`` with ``functools.singledispatch``, a
``TypeError`` at import on Python 3.11+).
"""

from __future__ import annotations

import functools
from typing import Final

from hyperpython import li, ul  # ty: ignore[import-not-found]  # undeclared: pins an older markupsafe

_REQUIREMENTS: Final[tuple[str, ...]] = ("hyperpython>=1.1.1", "sidekick<0.7")


def build_e(count: int) -> None:
    # Build a ``<ul>`` of rows with hyperpython's subscript-children syntax and stringify it.
    _ = str(ul([li(class_="item", data_i=str(index))[f"item {index}"] for index in range(count)]))


def construct(count: int) -> None:
    # Construct ``count`` hyperpython elements with attributes and children, in isolation from stringification.
    for index in range(count):
        li(class_="item", data_i=str(index))[f"item {index}"]


@functools.cache
def _tree(count: int) -> object:
    # Return a built ``<ul>`` of ``count`` rows, cached so ``str`` times only the emit step.
    return ul([li(class_="item", data_i=str(index))[f"item {index}"] for index in range(count)])


def emit(count: int) -> None:
    # Stringify a pre-built ``count``-row tree, in isolation from construction.
    _ = str(_tree(count))


OPERATIONS = {
    "build-e": (build_e, "hyperpython"),
    "construct": (construct, "hyperpython"),
    "emit": (emit, "hyperpython"),
}
