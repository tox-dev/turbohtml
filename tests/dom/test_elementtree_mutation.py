from __future__ import annotations

from typing import TYPE_CHECKING, Final, cast

import pytest

from turbohtml import Element, Text, etree, parse
from turbohtml.etree import document_fromstring, fragment_fromstring, fromstring, strip_elements, strip_tags, tostring

if TYPE_CHECKING:
    from collections.abc import Callable

    from turbohtml.etree import ElementView


@pytest.mark.parametrize(
    ("index", "expected"),
    [
        pytest.param(-20, "<div>start<strong>N</strong>tail<a>A</a>one<b>B</b>two</div>", id="before-first"),
        pytest.param(0, "<div>start<strong>N</strong>tail<a>A</a>one<b>B</b>two</div>", id="first"),
        pytest.param(-1, "<div>start<a>A</a>one<strong>N</strong>tail<b>B</b>two</div>", id="before-last"),
        pytest.param(1, "<div>start<a>A</a>one<strong>N</strong>tail<b>B</b>two</div>", id="middle"),
        pytest.param(20, "<div>start<a>A</a>one<b>B</b>two<strong>N</strong>tail</div>", id="after-last"),
    ],
)
def test_element_tree_insert(tree: ElementView, incoming: ElementView, index: int, expected: str) -> None:
    tree.insert(index, incoming)
    assert tostring(tree, encoding="unicode") == expected


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        pytest.param(
            lambda tree, incoming: tree[0].addnext(incoming),
            "<div>start<a>A</a>one<strong>N</strong>tail<b>B</b>two</div>",
            id="following-sibling",
        ),
        pytest.param(
            lambda tree, incoming: tree[1].addprevious(incoming),
            "<div>start<a>A</a>one<strong>N</strong>tail<b>B</b>two</div>",
            id="preceding-sibling",
        ),
        pytest.param(
            lambda tree, incoming: tree.extend([incoming]),
            "<div>start<a>A</a>one<b>B</b>two<strong>N</strong>tail</div>",
            id="extend",
        ),
        pytest.param(
            lambda tree, incoming: tree.replace(tree[0], incoming),
            "<div>start<strong>N</strong>tail<b>B</b>two</div>",
            id="replace",
        ),
    ],
)
def test_element_tree_moves_element_with_tail(
    tree: ElementView,
    incoming: ElementView,
    operation: Callable[[ElementView, ElementView], None],
    expected: str,
) -> None:
    operation(tree, incoming)
    assert tostring(tree, encoding="unicode") == expected


@pytest.mark.parametrize(
    ("keep_tail", "expected"),
    [
        pytest.param(True, "<div>start<a/>one<b>B</b>two</div>", id="keep-tail"),
        pytest.param(False, "<div>start<a/><b>B</b>two</div>", id="remove-tail"),
    ],
)
def test_element_tree_clear(tree: ElementView, expected: str, *, keep_tail: bool) -> None:
    tree[0].set("class", "one two")
    tree[0].clear(keep_tail=keep_tail)
    assert tostring(tree, encoding="unicode") == expected


def test_element_tree_unwrap_keeps_text(tree: ElementView) -> None:
    tree[0].drop_tag()
    assert tostring(tree, encoding="unicode") == "<div>startAone<b>B</b>two</div>"


def test_element_tree_tag_setter_changes_serialized_name(tree: ElementView) -> None:
    tree[0].tag = "strong"
    assert tostring(tree, encoding="unicode") == "<div>start<strong>A</strong>one<b>B</b>two</div>"


def test_element_tree_unwrap_requires_parent() -> None:
    with pytest.raises(ValueError, match="parent"):
        etree.Element("div").drop_tag()


def test_element_tree_itertext_joins_adjacent_text_nodes() -> None:
    view: Final = etree.ElementView(Element("p", children=[Text("one"), Text("two")]))
    assert list(view.itertext()) == ["onetwo"]


def test_element_tree_text_setter_replaces_adjacent_nodes() -> None:
    node: Final = Element("p", children=[Text("one"), Text("two"), Element("b")])
    view: Final = etree.ElementView(node)
    view.text = "new"
    assert tostring(view, encoding="unicode") == "<p>new<b/></p>"


def test_element_tree_text_setter_removes_leading_text(tree: ElementView) -> None:
    tree.text = None
    assert tostring(tree, encoding="unicode") == "<div><a>A</a>one<b>B</b>two</div>"


def test_element_tree_tail_setter_replaces_adjacent_nodes() -> None:
    node: Final = Element("p", children=[Element("b"), Text("one"), Text("two")])
    view: Final = etree.ElementView(node)
    view[0].tail = "new"
    assert tostring(view, encoding="unicode") == "<p><b/>new</p>"


@pytest.mark.parametrize(
    "operation",
    [
        pytest.param(lambda tree: setattr(tree, "tail", "outside"), id="tail"),
        pytest.param(lambda tree: tree.addnext(etree.Element("p")), id="sibling"),
    ],
)
def test_element_tree_document_root_ignores_outside_content(operation: Callable[[ElementView], None]) -> None:
    tree: Final = document_fromstring("<p>text</p>")
    operation(tree)
    assert tostring(tree, encoding="unicode") == "<html><body><p>text</p></body></html>"


def test_element_tree_detaches_document_root() -> None:
    document: Final = parse("<p>text</p>")
    root: Final = document.root
    assert root is not None
    tree: Final = etree.ElementView(root)
    tree.drop_tree()
    assert (document.root, tree.getparent(), tostring(tree, encoding="unicode")) == (
        None,
        None,
        "<html><head/><body><p>text</p></body></html>",
    )


@pytest.mark.parametrize("function", [pytest.param(strip_tags, id="unwrap"), pytest.param(strip_elements, id="remove")])
def test_element_tree_empty_strip_filter_retains_content(tree: ElementView, function: Callable[..., None]) -> None:
    function(tree)
    assert tostring(tree, encoding="unicode") == "<div>start<a>A</a>one<b>B</b>two</div>"


@pytest.mark.parametrize(
    "operation",
    [
        pytest.param(lambda tree, child: tree.remove(child), id="remove"),
        pytest.param(lambda tree, child: tree.replace(child, etree.Element("i")), id="replace"),
        pytest.param(lambda tree, child: tree.index(child), id="index"),
    ],
)
def test_element_tree_rejects_nonchild(
    tree: ElementView, incoming: ElementView, operation: Callable[[ElementView, ElementView], int | None]
) -> None:
    with pytest.raises(ValueError, match="not"):
        operation(tree, incoming)


@pytest.mark.parametrize(
    "operation",
    [
        pytest.param(lambda tree, child: tree.append(child), id="append"),
        pytest.param(lambda tree, child: tree.remove(child), id="remove"),
        pytest.param(lambda tree, child: tree.insert(0, child), id="insert"),
        pytest.param(lambda tree, child: tree.replace(tree[0], child), id="replace"),
        pytest.param(lambda tree, child: tree.replace(child, tree[0]), id="replace-invalid-old"),
        pytest.param(lambda tree, child: tree[0].addnext(child), id="addnext"),
        pytest.param(lambda tree, child: tree[0].addprevious(child), id="addprevious"),
    ],
)
def test_element_tree_rejects_non_element(
    tree: ElementView, operation: Callable[[ElementView, ElementView], None]
) -> None:
    with pytest.raises(TypeError, match="ElementView"):
        operation(tree, cast("ElementView", "text"))


@pytest.fixture
def tree() -> ElementView:
    return fromstring("<div>start<a>A</a>one<b>B</b>two</div>")


@pytest.fixture
def incoming() -> ElementView:
    return fragment_fromstring("<strong>N</strong>tail")
