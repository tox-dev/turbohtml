from __future__ import annotations

import gc
import sys
from typing import Final

import pytest

from turbohtml import Element, parse


@pytest.mark.parametrize("count", [pytest.param(0, id="empty"), pytest.param(1, id="one"), pytest.param(33, id="many")])
@pytest.mark.parametrize(
    "limit",
    [
        pytest.param(None, id="unlimited"),
        pytest.param(0, id="zero"),
        pytest.param(1, id="first"),
        pytest.param(8, id="eight"),
        pytest.param(9, id="nine"),
        pytest.param(33, id="exact"),
        pytest.param(40, id="above-count"),
    ],
)
@pytest.mark.parametrize("warm", [pytest.param(False, id="cold"), pytest.param(True, id="warm")])
def test_find_all_result_order(count: int, limit: int | None, *, warm: bool) -> None:
    document: Final = parse("".join(f"<section><p id='{index}'></p><span></span></section>" for index in range(count)))
    if warm:
        document.find_all("span")
    assert [element.attrs["id"] for element in document.find_all("p", limit=limit)] == [
        str(index) for index in range(count)
    ][:limit]


def test_find_all_results_after_mutation() -> None:
    document: Final = parse("<main><p id='first'></p><p id='second'></p></main>")
    matches: Final = document.find_all("p")
    matches[0].tag = "span"
    matches[1].extract()
    main: Final = document.find("main")
    assert main is not None
    main.append(Element("p", attrs={"id": "third"}))
    assert [element.attrs["id"] for element in document.find_all("p")] == ["third"]


@pytest.mark.parametrize("limit", [0, 1, 2, 9])
@pytest.mark.parametrize("present", [True, False], ids=["present", "absent"])
@pytest.mark.parametrize("indexed", [True, False], ids=["indexed", "subtree"])
def test_find_all_filtered_result_limit(limit: int, *, present: bool, indexed: bool) -> None:
    document: Final = parse("<section><p id='a' data-x></p><p id='b'></p><p id='c' data-x></p><p id='d'></p></section>")
    document.find_all("span")
    origin: Final = document if indexed else document.find("section")
    assert origin is not None
    assert [element.attrs["id"] for element in origin.find_all("p", attrs={"data-x": present}, limit=limit)] == (
        ["a", "c"] if present else ["b", "d"]
    )[:limit]


def test_find_all_filtered_result_after_attribute_mutation() -> None:
    document: Final = parse("<p id='a' data-x></p><p id='b'></p><p id='c' data-x></p>")
    document.find_all("span")
    target: Final = document.find("p", id="b")
    assert target is not None
    target.attrs["data-x"] = None
    assert [element.attrs["id"] for element in document.find_all("p", attrs={"data-x": True})] == ["a", "b", "c"]


@pytest.mark.skipif(sys.implementation.name != "cpython", reason="CPython allocation-triggered collection")
@pytest.mark.parametrize("offset", [0, 1, 2])
@pytest.mark.parametrize("count", [pytest.param(0, id="clear"), pytest.param(33, id="replace")])
def test_find_all_results_during_collection(offset: int, count: int) -> None:
    thresholds: Final = gc.get_threshold()
    restore_gc: Final = gc.enable if gc.isenabled() else gc.disable
    gc.disable()
    gc.collect()
    document: Final = parse("<main>" + "<p id='old'>x</p>" * 33 + "</main>")
    document.find_all("p")
    main: Final = document.find("main")
    assert main is not None
    collect: Final = document.find_all
    changed = False

    def clear(phase: str, _info: dict[str, int]) -> None:
        nonlocal changed
        if phase == "start" and not changed:
            changed = True
            main.set_inner_html("<p id='new'>x</p>" * count)

    # Empty lists exhaust CPython's freelist so result allocation can trigger collection.
    reserve: Final[list[list[None]]] = [[] for _ in range(512)]
    gc.callbacks.append(clear)
    try:
        gc.set_threshold(gc.get_count()[0] + offset, thresholds[1], thresholds[2])
        gc.enable()
        result: Final = collect("p", limit=32)
        gc.collect()
    finally:
        gc.disable()
        gc.callbacks.remove(clear)
        gc.set_threshold(*thresholds)
        restore_gc()
        reserve.clear()
    expected: Final = ["new"] * min(count, 32) if sys.version_info < (3, 12) else ["old"] * 32
    assert (changed, [element.attrs["id"] for element in result], main.inner_html) == (
        True,
        expected,
        '<p id="new">x</p>' * count,
    )
