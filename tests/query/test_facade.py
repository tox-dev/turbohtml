from __future__ import annotations

import gc
import sys
from typing import Final

import pytest

from turbohtml import Element, parse
from turbohtml.query import Query


@pytest.mark.parametrize(
    ("order", "expected"),
    [
        pytest.param((0, 1, 2, 3), ["a", "b", "c"], id="forward"),
        pytest.param((3, 2, 1, 0), ["c", "b", "a"], id="reverse"),
        pytest.param((1, 0, 3, 2), ["b", "a", "c"], id="interleaved"),
        pytest.param((0, 1, 0, 2, 3, 3), ["a", "b", "c"], id="duplicate-input"),
    ],
)
def test_parent_order_across_child_counts(order: tuple[int, ...], expected: list[str]) -> None:
    document: Final = parse(
        '<main><section id="a"><i></i></section>'
        '<section id="b">text<i></i><i></i></section>'
        '<section id="c"><i></i><!--gap--></section></main>'
    )
    nodes: Final = document.select("i")
    assert [node.attrs["id"] for node in Query(nodes[index] for index in order).parent()] == expected


def test_parent_after_sibling_changes() -> None:
    parent: Final = Element("main", children=[Element("i")])
    child: Final = parent.children[0]
    assert isinstance(child, Element)
    sibling: Final = Element("i")
    before: Final = list(Query(child).parent())
    parent.append(sibling)
    during: Final = list(Query([sibling, child]).parent())
    sibling.extract()
    assert (before, during, list(Query([sibling, child]).parent())) == ([parent], [parent], [parent])


@pytest.mark.parametrize(
    "count",
    [
        pytest.param(0, id="empty"),
        pytest.param(1, id="one"),
        pytest.param(16, id="inline"),
        pytest.param(17, id="spill"),
        pytest.param(64, id="many"),
    ],
)
def test_parent_many_distinct_nodes(count: int) -> None:
    query: Final = Query("".join(f'<section id="{index}"><i></i></section>' for index in range(count)))("i")
    assert [node.attrs["id"] for node in query.parent()] == [str(index) for index in range(count)]


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
