from __future__ import annotations

from typing import TYPE_CHECKING, Final, cast

import pytest

from turbohtml import Element, Text, etree, parse, parse_xml
from turbohtml.etree import document_fromstring, fragment_fromstring, fromstring, strip_elements, strip_tags, tostring

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from work_steps import WorkSteps

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


@pytest.mark.parametrize("index", [pytest.param(-1, id="from-end"), pytest.param(0, id="first")])
def test_element_tree_insert_into_childless(incoming: ElementView, index: int) -> None:
    empty: Final = etree.fromstring("<div>start</div>")
    empty.insert(index, incoming)
    assert etree.tostring(empty, encoding="unicode") == "<div>start<strong>N</strong>tail</div>"


@pytest.mark.parametrize(
    ("index", "expected"),
    [
        pytest.param(0, "<ul><li>d</li><li>c</li><li>b</li><li>a</li></ul>", id="front-reverses"),
        pytest.param(-1, "<ul><li>a</li><li>b</li><li>c</li><li>d</li></ul>", id="before-last-restores"),
    ],
)
def test_element_tree_insert_moving_each_child_near_either_end_orders_them(index: int, expected: str) -> None:
    listing = etree.fromstring("<ul><li>a</li><li>b</li><li>c</li><li>d</li></ul>")
    for item in list(listing):
        listing.insert(index, item)
    assert etree.tostring(listing, encoding="unicode") == expected


@pytest.mark.parametrize("index", [pytest.param(1, id="second"), pytest.param(-2, id="second-from-end")])
def test_element_tree_insert_near_either_end_stays_linear(index: int, work_steps: WorkSteps) -> None:
    def move_each(size: int) -> Callable[[], object]:
        listing = etree.fromstring(f"<ol>{'<li>i</li>' * size}</ol>")
        items = list(listing)

        def move_all() -> None:
            for item in items:
                listing.insert(index, item)

        return move_all

    work_steps.assert_linear(move_each, (500, 2000))


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


def test_element_tree_clear_removes_many_attributes_linearly(work_steps: WorkSteps) -> None:
    work_steps.assert_linear(lambda count: _attributed_element(count).clear, (1_000, 4_000))


def _attributed_element(count: int) -> ElementView:
    return fromstring(f"<p {' '.join(f'k{index}' for index in range(count))}></p>")


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
def test_element_tree_empty_strip_filter_retains_content(
    tree: ElementView, function: Callable[[ElementView, *tuple[str | Iterable[str] | None, ...]], None]
) -> None:
    function(tree)
    assert tostring(tree, encoding="unicode") == "<div>start<a>A</a>one<b>B</b>two</div>"


def test_element_tree_strip_elements_leaves_nested_match_in_removed_subtree() -> None:
    tree: Final = etree.fromstring("<div><b>one<i>two</i>three</b>four<i>five</i>six</div>")
    outer: Final = tree[0]
    etree.strip_elements(tree, "b", "i")
    assert (etree.tostring(tree, encoding="unicode"), etree.tostring(outer, encoding="unicode")) == (
        "<div/>",
        "<b>one<i>two</i>three</b>",
    )


def test_element_tree_strip_elements_leaves_template_content() -> None:
    tree: Final = etree.fromstring("<div><template><b>kept</b></template><b>gone</b></div>")
    etree.strip_elements(tree, "b")
    assert etree.tostring(tree, encoding="unicode") == "<div><template><b>kept</b></template></div>"


def test_element_tree_strip_elements_stops_at_tail_holder() -> None:
    tree: Final = etree.fromstring("<div><p><b>bold</b></p>tail</div>")
    removed: Final = tree[0]
    tree.remove(removed)  # parks the element and its tail under a holder that getparent() does not climb past
    holder: Final = removed.node.parent
    assert isinstance(holder, Element)
    etree.strip_elements(etree.ElementView(holder), "b")
    assert etree.tostring(removed, encoding="unicode") == "<p><b>bold</b></p>tail"


def test_element_tree_strip_elements_stops_at_tail_holder_below_root() -> None:
    tree: Final = etree.fromstring("<div><p><b>bold</b></p>tail</div>")
    removed: Final = tree[0]
    tree.remove(removed)
    holder: Final = removed.node.parent
    assert isinstance(holder, Element)
    outer: Final = etree.fromstring("<main><i>z</i></main>")
    outer.append(etree.ElementView(holder))
    etree.strip_elements(outer, "b")
    assert etree.tostring(removed, encoding="unicode") == "<p><b>bold</b></p>tail"


def test_element_tree_getparent_of_a_detached_element_is_none() -> None:
    assert etree.ElementView(Element("p")).getparent() is None


def test_element_tree_strip_elements_star_removes_every_element() -> None:
    tree: Final = etree.fromstring("<div>a<b>x</b>c<i>y</i></div>")
    etree.strip_elements(tree, "*")
    assert etree.tostring(tree, encoding="unicode") == "<div>a</div>"


def test_element_tree_strip_elements_stays_linear(work_steps: WorkSteps) -> None:
    work_steps.assert_linear(_match_per_level, (700, 2_800))


def _match_per_level(depth: int) -> Callable[[], None]:
    root: Final = parse_xml(f"<r>{'<s><m/>' * depth}{'</s>' * depth}</r>").root
    assert root is not None
    tree: Final = etree.ElementView(root)

    def strip() -> None:
        etree.strip_elements(tree, "m")

    return strip


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


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        pytest.param("<div><i>a</i>1<i>b</i>2<i>c</i>3</div>", "<div>123</div>", id="tail-each"),
        pytest.param("<div><i>a</i><i>b</i><i>c</i>3</div>", "<div>3</div>", id="tail-last"),
    ],
)
def test_element_tree_drop_tree_leaves_each_tail_in_parent(markup: str, expected: str) -> None:
    tree: Final = etree.fromstring(markup)
    dropped: Final = list(tree)
    for element in reversed(dropped):
        element.drop_tree()
    assert ([element.tail for element in dropped], etree.tostring(tree, encoding="unicode")) == ([None] * 3, expected)


def test_element_tree_repeated_drop_tree_stays_linear(work_steps: WorkSteps) -> None:
    work_steps.assert_linear(_repeated_drops, (1_000, 4_000))


def _repeated_drops(count: int) -> Callable[[], None]:
    tree: Final = etree.fromstring(f"<div>{'<i>a</i>t' * count}</div>")
    elements: Final = tree.findall(".//i")

    def drop() -> None:
        for element in reversed(elements):
            element.drop_tree()

    return drop


@pytest.fixture
def tree() -> ElementView:
    return fromstring("<div>start<a>A</a>one<b>B</b>two</div>")


@pytest.fixture
def incoming() -> ElementView:
    return fragment_fromstring("<strong>N</strong>tail")


@pytest.mark.parametrize(
    ("attribute", "path", "expected"),
    [
        pytest.param("text", "p/text()", "price: 10 EUR", id="text"),
        pytest.param("tail", "text()", "after: 10 EUR", id="tail"),
    ],
)
def test_element_tree_text_setter_keeps_one_text_node(attribute: str, path: str, expected: str) -> None:
    tree: Final = fragment_fromstring("<div><p>price: </p>after: </div>")
    for more in ("1", "0", " EUR"):
        setattr(tree[0], attribute, getattr(tree[0], attribute) + more)
    texts: Final = tree.xpath(path)
    assert isinstance(texts, list)
    assert [str(value) for value in texts] == [expected]  # XPath sees one text node, as lxml does


def test_element_tree_text_setter_writes_into_the_held_text_node() -> None:
    tree: Final = fragment_fromstring("<div>one<b></b></div>")
    held: Final = tree.node.children[0]
    assert isinstance(held, Text)
    tree.text = "two"
    assert (held.data, held.parent) == ("two", tree.node)
