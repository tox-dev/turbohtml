from __future__ import annotations

import gc
import sys
from typing import Final

import pytest

from turbohtml import Element


@pytest.mark.skipif(sys.implementation.name != "cpython", reason="CPython allocation-triggered collection")
@pytest.mark.parametrize(
    ("method", "expected"),
    [
        pytest.param("values", ["old", ["before", "value"], "tail"], id="values"),
        pytest.param("items", [("id", "old"), ("class", ["before", "value"]), ("title", "tail")], id="items"),
        pytest.param("copy", {"id": "old", "class": ["before", "value"], "title": "tail"}, id="copy"),
    ],
)
@pytest.mark.parametrize("offset", [0, 1, 2, 3])
def test_attrs_snapshot_during_collection(
    method: str,
    expected: list[str | list[str] | tuple[str, str | list[str]]] | dict[str, str | list[str]],
    offset: int,
) -> None:
    thresholds: Final = gc.get_threshold()
    restore_gc: Final = gc.enable if gc.isenabled() else gc.disable
    gc.disable()
    gc.collect()
    attrs: Final = Element("a", {"id": "old", "class": "before value", "title": "tail"}).attrs
    collect: Final = getattr(attrs, method)
    changed = False

    def replace_values(phase: str, _info: dict[str, int]) -> None:
        nonlocal changed
        if phase == "start" and not changed:
            changed = True
            attrs["id"] = "new"
            attrs["class"] = "after value"
            attrs["title"] = "changed"

    # Exhaust freelists so list and tuple allocations can trigger collection.
    lists: Final[list[list[None]]] = [[] for _ in range(512)]
    tuples: Final = [(index, None) for index in range(4096)]
    gc.callbacks.append(replace_values)
    try:
        gc.set_threshold(gc.get_count()[0] + offset, thresholds[1], thresholds[2])
        gc.enable()
        result: Final = collect()
        gc.collect()
    finally:
        gc.disable()
        gc.callbacks.remove(replace_values)
        gc.set_threshold(*thresholds)
        restore_gc()
        lists.clear()
        tuples.clear()
    assert (changed, result, attrs.copy()) == (
        True,
        expected,
        {"id": "new", "class": ["after", "value"], "title": "changed"},
    )


@pytest.mark.parametrize("method", ["keys", "values", "items", "copy"])
def test_attrs_empty_snapshot(method: str) -> None:
    attrs: Final = Element("div").attrs
    assert getattr(attrs, method)() == ({} if method == "copy" else [])


@pytest.mark.parametrize("method", ["iter", "keys"])
def test_attrs_names_snapshot(method: str) -> None:
    attrs: Final = Element("div", {"id": "first", "data-name": "second"}).attrs
    snapshot: Final = iter(attrs) if method == "iter" else attrs.keys()
    attrs["later"] = "third"
    assert list(snapshot) == ["id", "data-name"]
