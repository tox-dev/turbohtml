from __future__ import annotations

import gc
import sys
import weakref
from typing import TYPE_CHECKING

import pytest

from turbohtml.saxparse import StartElement, iter_events

if TYPE_CHECKING:
    from collections.abc import Iterator

    from turbohtml.saxparse import SaxEvent


@pytest.mark.skipif(
    sys.implementation.name == "pypy",
    reason="cpyext never breaks a cycle that runs through both a C extension object and a Python one, "
    "so this cycle leaks there; see docs/explanation/interpreters.rst",
)
def test_gc_reclaims_a_reference_cycle_through_the_source() -> None:
    class _Cyclic(str):  # ruff:ignore[subclass-builtin]  # the C iterator requires str, and the subclass can hold the cycle
        __slots__ = ("loop", "sentinel")
        loop: Iterator[SaxEvent]
        sentinel: _Sentinel

    class _Sentinel:
        back: _Cyclic

    source = _Cyclic("<p>x</p>")
    events = iter_events(source)
    assert isinstance(next(events), StartElement)  # create the C iterator before forming the cycle
    sentinel = _Sentinel()
    source.loop = events
    source.sentinel = sentinel
    sentinel.back = source
    marker = weakref.ref(sentinel)
    del source, events, sentinel
    gc.collect()
    assert marker() is None
