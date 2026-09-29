from __future__ import annotations

import gc
import sys
from typing import Final

import pytest

from turbohtml import Element, parse


@pytest.mark.skipif(sys.implementation.name != "cpython", reason="CPython allocation-triggered collection")
@pytest.mark.parametrize("method", ["assigned_nodes", "assigned_elements", "flattened_children"])
@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_shadow_results_survive_collection_adoption(method: str, offset: int) -> None:
    thresholds: Final = gc.get_threshold()
    restore_gc: Final = gc.enable if gc.isenabled() else gc.disable
    gc.disable()
    gc.collect()
    document: Final = parse("<div>text<span>hello</span></div>")
    host: Final = document.find("div")
    assert host is not None
    expected: Final = [child for child in host.children if method != "assigned_elements" or isinstance(child, Element)]
    root: Final = host.attach_shadow()
    root.set_inner_html("<slot></slot>")
    slot: Final = root.find("slot")
    assert slot is not None
    target: Final = Element("main")
    changed = False

    def adopt(phase: str, _info: dict[str, int]) -> None:
        nonlocal changed
        if phase == "start" and not changed:
            changed = True
            target.append(host)

    collect: Final = slot.assigned_elements if method == "assigned_elements" else slot.assigned_nodes
    # Retained empty lists exhaust the freelist so result allocation can trigger collection.
    reserve: Final[list[list[None]]] = [[] for _ in range(512)]
    gc.callbacks.append(adopt)
    try:
        gc.set_threshold(gc.get_count()[0] + offset, thresholds[1], thresholds[2])
        gc.enable()
        result: Final = slot.flattened_children if method == "flattened_children" else collect()
        gc.collect()
    finally:
        gc.disable()
        gc.callbacks.remove(adopt)
        gc.set_threshold(*thresholds)
        restore_gc()
        reserve.clear()
    assert (changed, result, host.parent) == (True, expected, target)
