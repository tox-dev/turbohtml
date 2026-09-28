from __future__ import annotations

import gc
import sys
from typing import Final

import pytest

from turbohtml import Element
from turbohtml.query import Query


@pytest.mark.skipif(sys.implementation.name != "cpython", reason="CPython allocation-triggered collection")
@pytest.mark.parametrize("offset", [0, 1, 2, 3])
@pytest.mark.parametrize("count", [pytest.param(1, id="one"), pytest.param(17, id="many")])
def test_parent_collection_preserves_wrappers(offset: int, count: int) -> None:
    thresholds: Final = gc.get_threshold()
    restore_gc: Final = gc.enable if gc.isenabled() else gc.disable
    gc.disable()
    gc.collect()
    children: Final = [Element("i") for _ in range(count)]
    parents: Final = [Element("section", children=[child]) for child in children]
    query: Final = Query(children)
    target: Final = Element("main")
    collect: Final = query.parent
    changed = False

    def adopt(phase: str, _info: dict[str, int]) -> None:
        nonlocal changed
        if phase == "start" and not changed:
            changed = True
            target.extend(children)

    # Exhaust the list freelist so result allocation can trigger collection.
    reserve: Final[list[list[None]]] = [[] for _ in range(512)]
    gc.callbacks.append(adopt)
    try:
        gc.set_threshold(gc.get_count()[0] + offset, thresholds[1], thresholds[2])
        gc.enable()
        result: Final = collect()
        gc.collect()
    finally:
        gc.disable()
        gc.callbacks.remove(adopt)
        gc.set_threshold(*thresholds)
        restore_gc()
        reserve.clear()
    assert (changed, tuple(result), tuple(child.parent for child in children)) in {
        (True, tuple(parents), (target,) * count),
        (True, (target,), (target,) * count),
    }
