from __future__ import annotations

from typing import Final

import pytest

from turbohtml import parse
from turbohtml.query import Query


@pytest.mark.parametrize("count", [31, 32, 33], ids=["small", "threshold", "large"])
@pytest.mark.parametrize(
    ("stride", "gap", "padding"),
    [
        pytest.param(1, "", 0, id="complete"),
        pytest.param(1, "", 1, id="one-omitted"),
        pytest.param(1, "text<!--gap-->", 2, id="dense"),
        pytest.param(19, "text<!--gap-->", 2, id="sparse"),
    ],
)
@pytest.mark.parametrize("order", ["sorted", "reversed", "shuffled"])
def test_find_sibling_root_order(count: int, stride: int, gap: str, padding: int, order: str) -> None:
    document: Final = parse(
        "<main>" + "".join(f"<div><i>{index}</i></div>{gap}" for index in range(count * stride + padding)) + "</main>"
    )
    start: Final = padding // 2
    roots = document.select("div")[start : start + count * stride : stride]
    if order == "reversed":
        roots.reverse()
    elif order == "shuffled":
        roots = roots[::2] + roots[1::2]
    assert [node.text for node in Query([*roots, *roots]).find("i")] == [
        str(start + index * stride) for index in range(count)
    ]


@pytest.mark.parametrize("order", ["sorted", "reversed", "shuffled"])
def test_find_mixed_parent_root_order(order: str) -> None:
    document: Final = parse(
        "<main>" + "".join(f"<section><div><i>{index}</i></div></section>" for index in range(40)) + "</main>"
    )
    roots = document.select("div")
    if order == "reversed":
        roots.reverse()
    elif order == "shuffled":
        roots = roots[::2] + roots[1::2]
    assert [node.text for node in Query(roots).find("i")] == [str(index) for index in range(40)]


def test_find_nested_root_order() -> None:
    document: Final = parse("<main>" + "<div><i>x</i>" * 40 + "</div>" * 40 + "</main>")
    assert list(Query(reversed(document.select("div"))).find("i")) == document.select("i")
