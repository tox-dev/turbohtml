from __future__ import annotations

import gc
import sys
from typing import TYPE_CHECKING, Final

import pytest

from turbohtml import Document, Element, Node, parse
from turbohtml.query import Query

if TYPE_CHECKING:
    from collections.abc import Callable


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
    document: Final[Document] = parse(
        '<main><section id="a"><i></i></section>'
        '<section id="b">text<i></i><i></i></section>'
        '<section id="c"><i></i><!--gap--></section></main>'
    )
    nodes: Final[list[Element]] = document.select("i")
    assert [node.attrs["id"] for node in Query(nodes[index] for index in order).parent()] == expected


def test_parent_after_sibling_changes() -> None:
    parent: Final[Element] = Element("main", children=[Element("i")])
    child: Final[Node] = parent.children[0]
    assert isinstance(child, Element)
    sibling: Final[Element] = Element("i")
    before: Final[list[Element]] = list(Query(child).parent())
    parent.append(sibling)
    during: Final[list[Element]] = list(Query([sibling, child]).parent())
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
    query: Final[Query] = Query("".join(f'<section id="{index}"><i></i></section>' for index in range(count)))("i")
    assert [node.attrs["id"] for node in query.parent()] == [str(index) for index in range(count)]


@pytest.mark.skipif(sys.implementation.name != "cpython", reason="CPython allocation-triggered collection")
@pytest.mark.parametrize("offset", [0, 1, 2, 3])
@pytest.mark.parametrize("count", [pytest.param(1, id="one"), pytest.param(17, id="many")])
def test_parent_collection_preserves_wrappers(offset: int, count: int) -> None:
    thresholds: Final[tuple[int, int, int]] = gc.get_threshold()
    gc.disable()
    gc.collect()
    children: Final[list[Element]] = [Element("i") for _ in range(count)]
    parents: Final[list[Element]] = [Element("section", children=[child]) for child in children]
    query: Final[Query] = Query(children)
    target: Final[Element] = Element("main")
    collect: Final[Callable[[], Query]] = query.parent
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
        result: Final[Query] = collect()
        gc.collect()
    finally:
        gc.disable()
        gc.callbacks.remove(adopt)
        gc.set_threshold(*thresholds)
        gc.enable()
        reserve.clear()
    assert (changed, tuple(result), tuple(child.parent for child in children)) in {
        (True, tuple(parents), (target,) * count),
        (True, (target,), (target,) * count),
    }
