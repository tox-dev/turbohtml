"""Each live node has one wrapper, so the same node is the same Python object.

The C layer caches one wrapper per node (cleared when the wrapper dies), giving
``node is node`` identity and sparing hot traversal/query loops the per-access
wrapper churn.
"""

from __future__ import annotations

import pytest

import turbohtml

_DOC = "<html><body><div id=a><p>x</p><p>y</p></div></body></html>"


def test_repeated_find_returns_the_same_object() -> None:
    doc = turbohtml.parse(_DOC)
    assert doc.find("div") is doc.find("div")


def test_find_and_find_all_agree_on_identity() -> None:
    doc = turbohtml.parse(_DOC)
    assert doc.find("p") is doc.find_all("p")[0]


@pytest.mark.parametrize(
    "axis",
    [
        pytest.param(lambda n: n.parent, id="parent"),
        pytest.param(lambda n: n.next_sibling, id="next_sibling"),
        pytest.param(lambda n: n.previous_sibling, id="previous_sibling"),
    ],
)
def test_navigation_axes_preserve_identity(axis: object) -> None:
    doc = turbohtml.parse(_DOC)
    paras = doc.find_all("p")
    node = paras[1]
    assert axis(node) is axis(node)  # ty: ignore[not-callable]


def test_child_parent_round_trip_is_identical() -> None:
    doc = turbohtml.parse(_DOC)
    div = doc.find("div")
    assert div.find("p").parent is div


def test_children_iteration_matches_indexing_identity() -> None:
    doc = turbohtml.parse(_DOC)
    div = doc.find("div")
    assert next(iter(div.children)) is next(iter(div.children))


def test_same_tree_move_preserves_identity() -> None:
    doc = turbohtml.parse(_DOC)
    paragraph = doc.find("p")
    body = doc.find("body")
    body.append(paragraph)  # relink within the same tree
    assert body.find_all("p")[-1] is paragraph
