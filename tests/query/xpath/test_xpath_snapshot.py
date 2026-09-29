from __future__ import annotations

import gc
import sys
from typing import Final

import pytest

from turbohtml import Element, XPath, XPathString, parse


@pytest.mark.skipif(sys.implementation.name != "cpython", reason="CPython allocation-triggered collection")
@pytest.mark.parametrize("smart_strings", [False, True], ids=["plain", "smart"])
@pytest.mark.parametrize("mixed", [False, True], ids=["nodes", "mixed"])
@pytest.mark.parametrize("offset", [0, 1, 2, 4])
def test_xpath_snapshot_during_collection(offset: int, *, smart_strings: bool, mixed: bool) -> None:
    thresholds: Final = gc.get_threshold()
    restore_gc: Final = gc.enable if gc.isenabled() else gc.disable
    gc.disable()
    gc.collect()
    document: Final = parse('<section><a id="old">first</a><b>second</b></section>')
    section: Final = document.select("section")[0]
    first, second = section.select("*")
    target: Final = Element("main")
    selector: Final = XPath("//a/@id | //a/text() | //b" if mixed else "//a | //b", smart_strings=smart_strings)
    context: Final = (document,)
    changed = False

    def adopt(phase: str, _info: dict[str, int]) -> None:
        nonlocal changed
        if phase == "start" and not changed:
            changed = True
            target.append(section)
            first.attrs["id"] = "changed"

    # Empty lists exhaust the freelist before the result allocation.
    reserve: Final[list[list[None]]] = [[] for _ in range(512)]
    gc.callbacks.append(adopt)
    try:
        gc.set_threshold(gc.get_count()[0] + offset, thresholds[1], thresholds[2])
        gc.enable()
        result: Final = selector(*context)
        gc.collect()
    finally:
        gc.disable()
        gc.callbacks.remove(adopt)
        gc.set_threshold(*thresholds)
        restore_gc()
        reserve.clear()
    assert (changed, result, target.select("b"), document.select("b")) == (
        True,
        ["old", "first", second] if mixed else [first, second],
        [second],
        [],
    )
    if smart_strings and mixed:
        assert isinstance(result, list)
        assert [(item.getparent(), item.attrname) for item in result if isinstance(item, XPathString)] == [
            (first, "id"),
            (first, None),
        ]


@pytest.mark.parametrize("compiled", [False, True], ids=["method", "compiled"])
def test_xpath_empty_smart_snapshot(*, compiled: bool) -> None:
    document: Final = parse("<section></section>")
    assert (XPath("//a", smart_strings=True)(document) if compiled else document.xpath("//a", smart_strings=True)) == []
