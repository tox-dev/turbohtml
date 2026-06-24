"""
Per-iteration setup for operations that mutate their parsed input.

An operation that changes the tree it is handed -- tagging links, replacing a body's inner HTML, absolutizing
hrefs -- cannot reuse one cached parse across pyperf iterations: the second measurement onward would time a tree
already altered by the first (a body left holding the small replacement, hrefs already absolute), so the number is
neither the real workload nor stable. :class:`Mutating` pairs an untimed ``setup`` that produces a fresh tree for
each iteration with the timed ``run`` that mutates it; the worker measures it through pyperf's ``bench_time_func`` so
only ``run`` is on the clock. Read-path operations keep their cached parse and stay plain callables.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True)
class Mutating:
    """A mutating operation: ``setup(case)`` builds a fresh tree (untimed), ``run(tree)`` mutates it (timed)."""

    setup: Callable[..., object]
    run: Callable[..., object]
