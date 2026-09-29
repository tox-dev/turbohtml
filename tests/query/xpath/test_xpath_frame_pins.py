from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest

from turbohtml import Element

if TYPE_CHECKING:
    from types import SimpleNamespace


def test_xpath_frame_completed_arguments_keep_adopted_node() -> None:
    source: Final = Element("section", children=[Element("b")])
    child: Final = source.children[0]
    target: Final = Element("main")

    def empty(_context: SimpleNamespace) -> list[Element]:
        return []

    def adopt(_context: SimpleNamespace) -> list[Element]:
        target.append(child)
        return []

    def inspect(_context: SimpleNamespace, _first: list[Element], nodes: list[Element], _last: list[Element]) -> bool:
        nodes[0].attrs["live"] = "yes"
        return target.select("b[live=yes]") == [child]

    assert (
        source.xpath(
            "inspect(empty(), .//b, adopt())",
            extensions={(None, "empty"): empty, (None, "adopt"): adopt, (None, "inspect"): inspect},
        )
        is True
    )


@pytest.mark.parametrize(
    "expression",
    [pytest.param("div/b[adopt()]", id="contexts"), pytest.param("div[seen()]/b[adopt()]", id="steps")],
)
def test_xpath_frame_path_candidates_keep_adopted_nodes(expression: str) -> None:
    source: Final = Element(
        "section", children=[Element("div", children=[Element("b", {"id": str(index)})]) for index in range(2)]
    )
    target: Final = Element("main")

    def seen(_context: SimpleNamespace) -> bool:
        return True

    def adopt(context: SimpleNamespace) -> bool:
        target.append(context.context_node)
        return True

    result: Final = source.xpath(expression, extensions={(None, "seen"): seen, (None, "adopt"): adopt})
    assert isinstance(result, list)
    for node in result:
        assert isinstance(node, Element)
        node.attrs["live"] = "yes"
    assert [node.attrs["id"] for node in target.select("b[live=yes]")] == ["0", "1"]


@pytest.mark.parametrize(
    "expression",
    [
        pytest.param("(.//b)[adopt()]", id="callback-first"),
        pytest.param("(@id | .//b)[self::b and adopt()]", id="attribute-first"),
    ],
)
def test_xpath_frame_filter_compaction_keeps_adopted_node(expression: str) -> None:
    source: Final = Element(
        "section", {"id": "root"}, children=[Element("b", {"id": str(index)}) for index in range(3)]
    )
    target: Final = Element("main")

    def adopt(context: SimpleNamespace) -> bool:
        target.append(context.context_node)
        return context.context_node.attrs["id"] == "2"

    result: Final = source.xpath(expression, extensions={(None, "adopt"): adopt})
    assert isinstance(result, list)
    assert len(result) == 1
    assert isinstance(result[0], Element)
    result[0].attrs["live"] = "yes"
    assert [node.attrs["id"] for node in target.select("b[live=yes]")] == ["2"]


def test_xpath_frame_path_retains_accepted_nodes_after_compaction() -> None:
    source: Final = Element(
        "section",
        children=[
            Element("div", children=[Element("b", {"id": str(index * 3 + child)}) for child in range(3)])
            for index in range(8)
        ],
    )
    target: Final = Element("main")

    def adopt(context: SimpleNamespace) -> bool:
        target.append(context.context_node)
        return True

    def keep(context: SimpleNamespace) -> bool:
        return int(context.context_node.attrs["id"]) % 2 == 1

    result: Final = source.xpath("div/b[adopt()][keep()]", extensions={(None, "adopt"): adopt, (None, "keep"): keep})
    assert isinstance(result, list)
    for node in result:
        assert isinstance(node, Element)
        node.attrs["live"] = "yes"
    assert [node.attrs["id"] for node in target.select("b[live=yes]")] == [str(index) for index in range(1, 24, 2)]
