from __future__ import annotations

from typing import Final

import pytest

from turbohtml import Element, Node, NodeFilter, NodeIterator, Text, parse
from turbohtml._html import _tree_verify


def test_tree_verify_keeps_an_iterator_reference_inside_its_root_across_normalize() -> None:
    root: Final = Element("div", children=[Text("x"), Text("y")])
    iterator: Final = NodeIterator(root)
    for _ in range(3):
        iterator.next_node()
    root.normalize()  # merging removes the reference, so the removal steps move it back to the merged text
    assert (_tree_verify(root), iterator.reference_node) == ((0, 0, 0, 0), root.children[0])


def test_tree_verify_keeps_a_filter_candidate_inside_its_root_across_normalize() -> None:
    merged: Final = Text("y")
    root: Final = Element("div", children=[Text("x"), merged])
    reports: Final[list[tuple[int, int, int, int]]] = []

    def record(node: Node) -> int:
        if node == merged:
            # merging removes the candidate, so the removal steps move it back to the merged text
            root.normalize()
            reports.append(_tree_verify(root))
        return NodeFilter.FILTER_SKIP

    assert (list(NodeIterator(root, filter=record)), reports) == ([], [(0, 0, 0, 0)])


def test_tree_verify_finds_nodes_in_every_arena_block() -> None:
    document: Final = parse("<p>" * 3_000)  # enough nodes to fill several arena blocks
    assert _tree_verify(document) == (0, 0, 0, 0)


def test_tree_verify_accepts_a_current_css_path_id_map() -> None:
    document: Final = parse('<p id="a"><b></b></p>')
    document.select("b")[0].css_path()
    assert _tree_verify(document) == (0, 0, 0, 0)


def test_tree_verify_rejects_a_non_node() -> None:
    with pytest.raises(TypeError, match="expected a turbohtml element"):
        _tree_verify(object())  # ty: ignore[invalid-argument-type] - the hook validates its argument at runtime
