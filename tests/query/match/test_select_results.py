from __future__ import annotations

import gc
import sys
from typing import Final

import pytest

from turbohtml import Element, parse, parse_xml
from turbohtml.query import select


@pytest.mark.parametrize("count", [pytest.param(0, id="empty"), pytest.param(1, id="one"), pytest.param(33, id="many")])
@pytest.mark.parametrize(
    "limit", [pytest.param(0, id="all"), pytest.param(1, id="first"), pytest.param(40, id="above")]
)
@pytest.mark.parametrize("warm", [pytest.param(False, id="cold"), pytest.param(True, id="warm")])
def test_select_indexed_order(count: int, limit: int, *, warm: bool) -> None:
    document: Final = parse("".join(f"<section><input id='{index}'><span></span></section>" for index in range(count)))
    if warm:
        document.select("span")
    assert [element.attrs["id"] for element in select("input", document, limit=limit)] == [
        str(index) for index in range(count)
    ][: limit or None]


def test_select_indexed_mutation() -> None:
    document: Final = parse("<main><input id='first'><input id='second'></main>")
    first, second = document.select("input")
    first.tag = "span"
    second.extract()
    main: Final = document.find("main")
    assert main is not None
    main.append(Element("input", attrs={"id": "third"}))
    assert [element.attrs["id"] for element in document.select("input")] == ["third"]


def test_select_indexed_subtree_scope() -> None:
    document: Final = parse("<input id='outside'><main><input id='inside'></main>")
    document.select("input")
    main: Final = document.find("main")
    assert main is not None
    assert [element.attrs["id"] for element in main.select("input")] == ["inside"]


def test_select_indexed_xml_case() -> None:
    document: Final = parse_xml('<root><input id="lower"/><INPUT id="upper"/></root>')
    assert [element.attrs["id"] for element in document.select("input")] == ["lower"]


@pytest.mark.skipif(sys.implementation.name != "cpython", reason="CPython allocation-triggered collection")
@pytest.mark.parametrize("offset", range(8), ids=lambda offset: f"allocation-{offset}")
@pytest.mark.parametrize("count", [pytest.param(7, id="shrink"), pytest.param(67, id="grow")])
def test_select_indexed_collection(offset: int, count: int) -> None:
    thresholds: Final = gc.get_threshold()
    restore_gc: Final = gc.enable if gc.isenabled() else gc.disable
    gc.disable()
    gc.collect()
    document: Final = parse("<main>" + "<input id='old'>" * 33 + "</main>")
    document.select("input")
    main: Final = document.find("main")
    assert main is not None
    collect: Final = document.select
    changed = False

    def clear(phase: str, _info: dict[str, int]) -> None:
        nonlocal changed
        if phase == "start" and not changed:
            changed = True
            main.set_inner_html("<input id='new'>" * count)

    # Empty lists exhaust CPython's freelist so result allocation can trigger collection.
    reserve: Final[list[list[None]]] = [[] for _ in range(512)]
    gc.callbacks.append(clear)
    try:
        gc.set_threshold(gc.get_count()[0] + offset, thresholds[1], thresholds[2])
        gc.enable()
        result: Final = collect("input")
        gc.collect()
    finally:
        gc.disable()
        gc.callbacks.remove(clear)
        gc.set_threshold(*thresholds)
        restore_gc()
        reserve.clear()
    assert (changed, tuple(element.attrs["id"] for element in result), main.inner_html) in {
        (True, ("old",) * 33, '<input id="new">' * count),
        (True, ("new",) * count, '<input id="new">' * count),
    }


@pytest.mark.parametrize(
    "limit", [pytest.param(1, id="first"), pytest.param(2, id="exact"), pytest.param(9, id="above")]
)
def test_select_indexed_filtered_limit(limit: int) -> None:
    document: Final = parse('<input id="skip"><input id="first" checked><input id="second" checked>')
    document.select("input")
    assert [element.attrs["id"] for element in select("input[checked]", document, limit=limit)] == ["first", "second"][
        :limit
    ]
