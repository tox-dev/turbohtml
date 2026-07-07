"""The DOM Range and StaticRange APIs: boundary points, comparison, and content operations."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from turbohtml import (
    CData,
    Comment,
    Doctype,
    Document,
    Element,
    Node,
    ProcessingInstruction,
    Range,
    StaticRange,
    Text,
    parse,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable


def _doctype(doc: Document) -> Doctype:
    return next(child for child in doc.children if isinstance(child, Doctype))


def _found(root: Element | Document, tag: str) -> Element:
    node = root.find(tag)
    assert node is not None, tag
    return node


def _by_id(root: Element | Document, value: str) -> Element:
    node = root.find(id=value)
    assert node is not None, value
    return node


def _tags(nodes: Iterable[Node]) -> list[str]:
    """The tag names of a run of nodes, asserting each is an element."""
    names = []
    for node in nodes:
        assert isinstance(node, Element)
        names.append(node.tag)
    return names


def _data(node: Node) -> str:
    """The character data of a node, asserting it is a Text node."""
    assert isinstance(node, Text)
    return node.data


def _first_child_datas(nodes: Iterable[Node]) -> list[str]:
    """The character data of each node's first child."""
    return [_data(node.children[0]) for node in nodes]


def test_construct_collapsed_at_container() -> None:
    doc = parse("<div id=a><p>x</p></div>")
    div = _by_id(doc, "a")
    boundary = Range(div, 0)
    assert boundary.collapsed
    assert boundary.start_container == div
    assert boundary.end_container == div
    assert boundary.start_offset == 0
    assert boundary.end_offset == 0


def test_construct_defaults_offset_to_zero() -> None:
    div = Element("div")
    assert Range(div).start_offset == 0


def test_construct_rejects_non_node() -> None:
    with pytest.raises(TypeError, match="container must be a node"):
        Range("nope")  # ty: ignore[invalid-argument-type]


def test_construct_rejects_doctype_boundary() -> None:
    doc = parse("<!doctype html><html></html>")
    doctype = next(child for child in doc.children if isinstance(child, Doctype))
    with pytest.raises(ValueError, match="doctype"):
        Range(doctype, 0)


@pytest.mark.parametrize(
    "offset",
    [pytest.param(-1, id="negative"), pytest.param(3, id="past-end")],
)
def test_construct_rejects_offset_out_of_range(offset: int) -> None:
    div = Element("div")
    div.append(Element("span"))
    with pytest.raises(IndexError, match="out of range"):
        Range(div, offset)


def test_common_ancestor_container() -> None:
    doc = parse("<div id=a><section><p>x</p></section><span>y</span></div>")
    div = _by_id(doc, "a")
    section = _found(doc, "section")
    span = _found(doc, "span")
    boundary = Range(div, 0)
    boundary.set_start_before(section)
    boundary.set_end_after(span)
    assert boundary.common_ancestor_container == div


def test_set_start_after_end_drags_end() -> None:
    doc = parse("<div id=a><p>a</p><p>b</p><p>c</p></div>")
    div = _by_id(doc, "a")
    boundary = Range(div, 1)
    boundary.set_start(div, 2)  # new start lands past the collapsed end, so the end follows it
    assert boundary.start_offset == 2
    assert boundary.end_offset == 2


def test_set_end_before_start_drags_start() -> None:
    doc = parse("<div id=a><p>a</p><p>b</p><p>c</p></div>")
    div = _by_id(doc, "a")
    boundary = Range(div, 2)
    boundary.set_end(div, 1)
    assert boundary.start_offset == 1
    assert boundary.end_offset == 1


def test_set_boundary_keeps_order_when_valid() -> None:
    doc = parse("<div id=a><p>a</p><p>b</p><p>c</p></div>")
    div = _by_id(doc, "a")
    boundary = Range(div, 0)
    boundary.set_end(div, 2)
    boundary.set_start(div, 1)
    assert (boundary.start_offset, boundary.end_offset) == (1, 2)


def test_set_start_across_trees_collapses() -> None:
    doc = parse("<div id=a><p>x</p></div>")
    div = _by_id(doc, "a")
    other = Element("section")
    boundary = Range(div, 0)
    boundary.set_end(div, 1)
    boundary.set_start(other, 0)  # a different tree drags the end onto the new start
    assert boundary.collapsed
    assert boundary.start_container == other


def test_set_end_across_trees_collapses() -> None:
    doc = parse("<div id=a><p>x</p></div>")
    div = _by_id(doc, "a")
    other = Element("section")
    boundary = Range(div, 0)
    boundary.set_end(other, 0)
    assert boundary.collapsed
    assert boundary.end_container == other


def test_set_start_across_roots_same_tree_collapses() -> None:
    doc = parse("<div id=a><p>x</p><p>y</p></div>")
    div = _by_id(doc, "a")
    snapshot = Range(div, 0)
    snapshot.set_end(div, 2)
    fragment = snapshot.clone_contents()  # lives in the same tree but on its own root
    orphan = fragment.children[0]
    boundary = Range(div, 0)
    boundary.set_end(div, 1)
    boundary.set_start(orphan, 0)
    assert boundary.collapsed
    assert boundary.start_container == orphan


def test_set_start_rejects_non_node() -> None:
    boundary = Range(Element("div"))
    with pytest.raises(TypeError, match="expected a node"):
        boundary.set_start("x", 0)  # ty: ignore[invalid-argument-type]


@pytest.mark.parametrize(
    "setter",
    ["set_start_before", "set_start_after", "set_end_before", "set_end_after", "select_node"],
)
def test_reference_setters_reject_parentless(setter: str) -> None:
    boundary = Range(Element("div"))
    with pytest.raises(ValueError, match="no parent"):
        getattr(boundary, setter)(Element("span"))


def test_set_start_after_and_end_before_bracket_a_node() -> None:
    doc = parse("<div id=a><p>a</p><b>bold</b><p>c</p></div>")
    div = _by_id(doc, "a")
    boundary = Range(div, 0)
    boundary.set_start_after(div.children[0])
    boundary.set_end_before(div.children[2])
    assert boundary.start_offset == 1
    assert boundary.end_offset == 2
    assert _tags(boundary.clone_contents().children)[0] == "b"


def test_select_node_wraps_the_node() -> None:
    doc = parse("<div id=a><p>a</p><b>bold</b><p>c</p></div>")
    div = _by_id(doc, "a")
    boundary = Range(div, 0)
    boundary.select_node(_found(doc, "b"))
    assert (boundary.start_offset, boundary.end_offset) == (1, 2)


def test_select_node_contents_spans_children() -> None:
    doc = parse("<div id=a><p>a</p><p>b</p></div>")
    div = _by_id(doc, "a")
    boundary = Range(div, 1)
    boundary.select_node_contents(div)
    assert (boundary.start_offset, boundary.end_offset) == (0, 2)


def test_select_node_contents_rejects_doctype() -> None:
    doc = parse("<!doctype html><html></html>")
    doctype = next(child for child in doc.children if isinstance(child, Doctype))
    boundary = Range(doc, 0)
    with pytest.raises(ValueError, match="doctype"):
        boundary.select_node_contents(doctype)


@pytest.mark.parametrize(
    ("to_start", "expected"),
    [pytest.param(True, (0, 0), id="to-start"), pytest.param(False, (2, 2), id="to-end")],
)
def test_collapse(to_start: bool, expected: tuple[int, int]) -> None:  # noqa: FBT001  # pytest case, not a flag
    doc = parse("<div id=a><p>a</p><p>b</p></div>")
    div = _by_id(doc, "a")
    boundary = Range(div, 0)
    boundary.set_end(div, 2)
    boundary.collapse(to_start)
    assert (boundary.start_offset, boundary.end_offset) == expected


def test_collapse_defaults_to_end() -> None:
    doc = parse("<div id=a><p>a</p><p>b</p></div>")
    div = _by_id(doc, "a")
    boundary = Range(div, 0)
    boundary.set_end(div, 2)
    boundary.collapse()
    assert boundary.collapsed
    assert boundary.start_offset == 2


@pytest.mark.parametrize(
    ("how", "expected"),
    [
        pytest.param(Range.START_TO_START, 0, id="start-to-start-equal"),
        pytest.param(Range.START_TO_END, 1, id="start-to-end"),
        pytest.param(Range.END_TO_END, -1, id="end-to-end"),
        pytest.param(Range.END_TO_START, -1, id="end-to-start"),
    ],
)
def test_compare_boundary_points(how: int, expected: int) -> None:
    doc = parse("<div id=a><p>a</p><p>b</p><p>c</p></div>")
    div = _by_id(doc, "a")
    left = Range(div, 0)
    left.set_end(div, 1)
    right = Range(div, 0)
    right.set_end(div, 2)
    assert left.compare_boundary_points(how, right) == expected


def test_compare_boundary_points_rejects_bad_how() -> None:
    div = Element("div")
    boundary = Range(div, 0)
    with pytest.raises(ValueError, match="how must be"):
        boundary.compare_boundary_points(7, boundary)


def test_compare_boundary_points_rejects_foreign_range() -> None:
    left = Range(Element("div"))
    right = Range(Element("div"))
    with pytest.raises(ValueError, match="different trees"):
        left.compare_boundary_points(Range.START_TO_START, right)


def test_compare_boundary_points_rejects_non_range() -> None:
    boundary = Range(Element("div"))
    with pytest.raises(TypeError):
        boundary.compare_boundary_points(0, object())  # ty: ignore[invalid-argument-type]


@pytest.mark.parametrize(
    ("offset", "expected"),
    [pytest.param(0, -1, id="before"), pytest.param(1, 0, id="inside"), pytest.param(3, 1, id="after")],
)
def test_compare_point(offset: int, expected: int) -> None:
    doc = parse("<div id=a><p>a</p><p>b</p><p>c</p></div>")
    div = _by_id(doc, "a")
    boundary = Range(div, 1)
    boundary.set_end(div, 2)
    assert boundary.compare_point(div, offset) == expected


def test_compare_point_rejects_foreign_tree() -> None:
    boundary = Range(Element("div"))
    with pytest.raises(ValueError, match="different tree"):
        boundary.compare_point(Element("span"), 0)


def test_compare_point_rejects_bad_offset() -> None:
    div = Element("div")
    boundary = Range(div, 0)
    with pytest.raises(IndexError):
        boundary.compare_point(div, 5)


@pytest.mark.parametrize(
    ("offset", "expected"),
    [pytest.param(0, False, id="before"), pytest.param(1, True, id="inside"), pytest.param(3, False, id="after")],
)
def test_is_point_in_range(offset: int, expected: bool) -> None:  # noqa: FBT001  # pytest expectation, not a flag
    doc = parse("<div id=a><p>a</p><p>b</p><p>c</p></div>")
    div = _by_id(doc, "a")
    boundary = Range(div, 1)
    boundary.set_end(div, 2)
    assert boundary.is_point_in_range(div, offset) is expected


def test_is_point_in_range_outside_tree_is_false() -> None:
    boundary = Range(Element("div"))
    assert boundary.is_point_in_range(Element("span"), 0) is False


def test_is_point_in_range_rejects_bad_offset() -> None:
    div = Element("div")
    boundary = Range(div, 0)
    with pytest.raises(IndexError):
        boundary.is_point_in_range(div, 5)


def test_intersects_node_overlapping() -> None:
    doc = parse("<div id=a><p>a</p><p>b</p><p>c</p></div>")
    div = _by_id(doc, "a")
    boundary = Range(div, 1)
    boundary.set_end(div, 2)
    assert boundary.intersects_node(div.children[1]) is True


def test_intersects_node_disjoint() -> None:
    doc = parse("<div id=a><p>a</p><p>b</p><p>c</p></div>")
    div = _by_id(doc, "a")
    boundary = Range(div, 1)
    boundary.set_end(div, 2)
    assert boundary.intersects_node(div.children[2]) is False


def test_intersects_node_root_has_no_parent() -> None:
    doc = parse("<div id=a><p>a</p></div>")
    div = _by_id(doc, "a")
    boundary = Range(div, 0)
    boundary.set_end(div, 1)
    root = div
    while root.parent is not None:
        root = root.parent
    assert boundary.intersects_node(root) is True


def test_intersects_node_outside_tree_is_false() -> None:
    boundary = Range(Element("div"))
    assert boundary.intersects_node(Element("span")) is False


def test_clone_contents_copies_children_without_moving() -> None:
    doc = parse("<div id=a><p>x</p><p>y</p><span>z</span></div>")
    div = _by_id(doc, "a")
    boundary = Range(div, 0)
    boundary.set_end(div, 2)
    fragment = boundary.clone_contents()
    assert _tags(fragment.children) == ["p", "p"]
    assert _tags(div.children) == ["p", "p", "span"]


def test_clone_contents_of_collapsed_range_is_empty() -> None:
    div = Element("div")
    fragment = Range(div, 0).clone_contents()
    assert list(fragment.children) == []


def test_clone_contents_within_one_text_node() -> None:
    doc = parse("<p>Hello World</p>")
    text = _found(doc, "p").children[0]
    boundary = Range(text, 0)
    boundary.set_end(text, 5)
    fragment = boundary.clone_contents()
    assert _data(fragment.children[0]) == "Hello"
    assert _data(text) == "Hello World"


def test_clone_contents_partial_text_boundaries() -> None:
    doc = parse("<div id=a><p>Hello</p><p>World</p></div>")
    div = _by_id(doc, "a")
    first_text = div.children[0].children[0]
    last_text = div.children[1].children[0]
    boundary = Range(first_text, 2)
    boundary.set_end(last_text, 3)
    fragment = boundary.clone_contents()
    assert _first_child_datas(fragment.children) == ["llo", "Wor"]
    assert _data(first_text) == "Hello"


def test_clone_contents_nested_element_recursion() -> None:
    doc = parse("<div id=a><section><b>keep</b><i>drop</i></section></div>")
    div = _by_id(doc, "a")
    section = _found(doc, "section")
    boundary = Range(section, 0)
    boundary.set_start(div, 0)
    boundary.set_end(section, 1)
    fragment = boundary.clone_contents()
    assert fragment.html == "<section><b>keep</b></section>"


def test_clone_contents_rejects_contained_doctype() -> None:
    doc = parse("<!doctype html><html><body></body></html>")
    boundary = Range(doc, 0)
    boundary.set_end(doc, 2)
    with pytest.raises(ValueError, match="doctype"):
        boundary.clone_contents()


def test_extract_contents_moves_children() -> None:
    doc = parse("<div id=a><p>x</p><p>y</p><span>z</span></div>")
    div = _by_id(doc, "a")
    kept = div.children[0]
    boundary = Range(div, 0)
    boundary.set_end(div, 2)
    fragment = boundary.extract_contents()
    assert _tags(fragment.children) == ["p", "p"]
    assert fragment.children[0] == kept  # a move preserves node identity
    assert _tags(div.children) == ["span"]
    assert boundary.collapsed
    assert boundary.start_offset == 0


def test_extract_contents_collapse_point_when_start_is_ancestor() -> None:
    doc = parse("<div id=a><section><p>x</p><p>y</p></section></div>")
    section = _found(doc, "section")
    inner = section.children[1].children[0]
    boundary = Range(section, 0)
    boundary.set_start(section, 0)
    boundary.set_end(inner, 1)
    boundary.extract_contents()
    assert boundary.start_container == section
    assert boundary.start_offset == 0


def test_extract_contents_within_one_text_node() -> None:
    doc = parse("<p>Hello World</p>")
    text = _found(doc, "p").children[0]
    boundary = Range(text, 0)
    boundary.set_end(text, 6)
    fragment = boundary.extract_contents()
    assert _data(fragment.children[0]) == "Hello "
    assert _data(text) == "World"


def test_extract_contents_partial_text_boundaries() -> None:
    doc = parse("<div id=a><p>Hello</p><p>World</p></div>")
    div = _by_id(doc, "a")
    first_text = div.children[0].children[0]
    last_text = div.children[1].children[0]
    boundary = Range(first_text, 2)
    boundary.set_end(last_text, 3)
    fragment = boundary.extract_contents()
    assert _first_child_datas(fragment.children) == ["llo", "Wor"]
    assert _data(first_text) == "He"
    assert _data(last_text) == "ld"


def test_extract_contents_of_collapsed_range_is_empty() -> None:
    div = Element("div")
    fragment = Range(div, 0).extract_contents()
    assert list(fragment.children) == []


def test_delete_contents_removes_without_returning() -> None:
    doc = parse("<div id=a><p>x</p><p>y</p><span>z</span></div>")
    div = _by_id(doc, "a")
    boundary = Range(div, 0)
    boundary.set_end(div, 2)
    assert boundary.delete_contents() is None
    assert _tags(div.children) == ["span"]
    assert boundary.collapsed


def test_delete_contents_within_one_text_node() -> None:
    doc = parse("<p>Hello World</p>")
    text = _found(doc, "p").children[0]
    boundary = Range(text, 0)
    boundary.set_end(text, 6)
    boundary.delete_contents()
    assert _data(text) == "World"


def test_insert_node_into_element() -> None:
    doc = parse("<div id=a><p>x</p><p>y</p></div>")
    div = _by_id(doc, "a")
    boundary = Range(div, 1)
    boundary.insert_node(Element("b"))
    assert _tags(div.children) == ["p", "b", "p"]


def test_insert_node_splits_text() -> None:
    doc = parse("<p>HelloWorld</p>")
    text = _found(doc, "p").children[0]
    boundary = Range(text, 5)
    boundary.insert_node(Element("b"))
    assert _found(doc, "p").html == "<p>Hello<b></b>World</p>"


def test_insert_node_extends_collapsed_range() -> None:
    doc = parse("<div id=a><p>x</p></div>")
    div = _by_id(doc, "a")
    boundary = Range(div, 1)
    boundary.insert_node(Element("b"))
    assert not boundary.collapsed
    assert boundary.end_offset == 2


def test_insert_fragment_extends_by_its_length() -> None:
    doc = parse("<div id=a><p>x</p></div>")
    div = _by_id(doc, "a")
    donor = parse("<div id=b><i>i</i><u>u</u></div>")
    donor_div = _by_id(donor, "b")
    fragment = Range(donor_div, 0)
    fragment.set_end(donor_div, 2)
    piece = fragment.extract_contents()
    boundary = Range(div, 1)
    boundary.insert_node(piece)
    assert boundary.end_offset == 3


def test_insert_node_moves_within_same_tree() -> None:
    doc = parse("<div id=a><p>x</p><span>y</span></div>")
    div = _by_id(doc, "a")
    span = _found(doc, "span")
    boundary = Range(div, 0)
    boundary.insert_node(span)
    assert _tags(div.children) == ["span", "p"]


def test_insert_node_before_reference_that_is_the_node() -> None:
    doc = parse("<div id=a><p>x</p><span>y</span></div>")
    div = _by_id(doc, "a")
    first = div.children[0]
    boundary = Range(div, 0)
    boundary.insert_node(first)  # the node already sits at the reference position
    assert _tags(div.children) == ["p", "span"]


@pytest.mark.parametrize(
    "container",
    [
        pytest.param(Comment("c"), id="comment"),
        pytest.param(Text("text"), id="parentless-text"),
        pytest.param(CData("data"), id="parentless-cdata"),
    ],
)
def test_insert_node_rejects_bad_boundary(container: Node) -> None:
    boundary = Range(container, 0)
    with pytest.raises(ValueError, match="cannot insert"):
        boundary.insert_node(Element("b"))


def test_insert_node_at_stale_offset_past_children() -> None:
    doc = parse("<div id=a><p>a</p><i>b</i><u>c</u></div>")
    div = _by_id(doc, "a")
    boundary = Range(div, 3)  # a valid end offset when the range is made
    div.remove("u")  # ...then the tree shrinks under it, so the walk runs off the end
    boundary.insert_node(Element("x"))
    assert _tags(div.children) == ["p", "i", "x"]


def test_insert_node_rejects_self() -> None:
    doc = parse("<div id=a><p>x</p></div>")
    div = _by_id(doc, "a")
    boundary = Range(div, 0)
    with pytest.raises(ValueError, match="cannot insert"):
        boundary.insert_node(div)


def test_insert_node_rejects_document() -> None:
    doc = parse("<div id=a><p>x</p></div>")
    div = _by_id(doc, "a")
    boundary = Range(div, 0)
    with pytest.raises(ValueError, match="Document"):
        boundary.insert_node(doc)


def test_insert_node_rejects_ancestor_cycle() -> None:
    doc = parse("<div id=a><p>x</p></div>")
    div = _by_id(doc, "a")
    inner = _found(doc, "p")
    boundary = Range(inner, 0)
    with pytest.raises(ValueError, match="own subtree"):
        boundary.insert_node(div)


def test_insert_node_rejects_non_node() -> None:
    boundary = Range(Element("div"))
    with pytest.raises(TypeError, match="expected a node"):
        boundary.insert_node("x")  # ty: ignore[invalid-argument-type]


def test_surround_contents_wraps_text_slice() -> None:
    doc = parse("<p>one two three</p>")
    text = _found(doc, "p").children[0]
    boundary = Range(text, 4)
    boundary.set_end(text, 7)
    boundary.surround_contents(Element("em"))
    assert _found(doc, "p").html == "<p>one <em>two</em> three</p>"


def test_surround_contents_selects_the_wrapper() -> None:
    doc = parse("<div id=a><p>x</p><p>y</p></div>")
    div = _by_id(doc, "a")
    boundary = Range(div, 0)
    boundary.set_end(div, 1)
    wrapper = boundary.surround_contents(Element("section"))
    assert isinstance(wrapper, Element)
    assert wrapper.tag == "section"
    assert boundary.start_container == div
    assert _tags(boundary.clone_contents().children) == ["section"]


def test_surround_contents_rejects_partial_non_text() -> None:
    doc = parse("<div id=a><p>one</p><p>two</p></div>")
    div = _by_id(doc, "a")
    first = div.children[0].children[0]
    boundary = Range(first, 1)
    boundary.set_end(div, 2)
    with pytest.raises(ValueError, match="partially contains"):
        boundary.surround_contents(Element("em"))


@pytest.mark.parametrize(
    "wrapper",
    [
        pytest.param(lambda doc: doc, id="document"),
        pytest.param(_doctype, id="doctype"),
    ],
)
def test_surround_contents_rejects_bad_wrapper_type(wrapper: Callable[[Document], Node]) -> None:
    doc = parse("<!doctype html><div id=a><p>x</p></div>")
    div = _by_id(doc, "a")
    boundary = Range(div, 0)
    boundary.set_end(div, 1)
    with pytest.raises(ValueError, match="document, doctype, or fragment"):
        boundary.surround_contents(wrapper(doc))


def test_surround_contents_rejects_fragment_wrapper() -> None:
    doc = parse("<div id=a><p>x</p><p>y</p></div>")
    div = _by_id(doc, "a")
    snapshot = Range(div, 0)
    snapshot.set_end(div, 2)
    fragment = snapshot.clone_contents()
    boundary = Range(div, 0)
    boundary.set_end(div, 1)
    with pytest.raises(ValueError, match="fragment"):
        boundary.surround_contents(fragment)


def test_surround_contents_rejects_non_node() -> None:
    boundary = Range(Element("div"))
    with pytest.raises(TypeError, match="expected a node"):
        boundary.surround_contents("x")  # ty: ignore[invalid-argument-type]


def test_clone_range_is_independent() -> None:
    doc = parse("<div id=a><p>x</p><p>y</p></div>")
    div = _by_id(doc, "a")
    boundary = Range(div, 0)
    boundary.set_end(div, 1)
    twin = boundary.clone_range()
    assert isinstance(twin, Range)
    assert (twin.start_offset, twin.end_offset) == (0, 1)
    twin.set_end(div, 2)
    assert boundary.end_offset == 1  # the clone's edits do not touch the original


def test_repr() -> None:
    doc = parse("<div id=a><p>x</p></div>")
    div = _by_id(doc, "a")
    boundary = Range(div, 0)
    assert repr(boundary) == "<Range start=(Element('div'), 0) end=(Element('div'), 0)>"


def test_char_data_length_covers_comment_and_cdata() -> None:
    assert Range(Comment("abc"), 3).start_offset == 3
    assert Range(CData("xy"), 2).start_offset == 2


def test_static_range_stores_boundaries() -> None:
    doc = parse("<div id=a><p>x</p><p>y</p></div>")
    div = _by_id(doc, "a")
    snapshot = StaticRange(div, 0, div, 2)
    assert snapshot.start_container == div
    assert snapshot.start_offset == 0
    assert snapshot.end_container == div
    assert snapshot.end_offset == 2
    assert snapshot.collapsed is False


def test_static_range_collapsed_when_points_coincide() -> None:
    div = Element("div")
    assert StaticRange(div, 0, div, 0).collapsed is True


def test_static_range_skips_offset_validation() -> None:
    div = Element("div")  # no bounds check, unlike Range
    assert StaticRange(div, 99, div, 100).start_offset == 99


def test_static_range_allows_distinct_trees() -> None:
    snapshot = StaticRange(Element("a"), 0, Element("b"), 0)
    start = snapshot.start_container
    end = snapshot.end_container
    assert isinstance(start, Element)
    assert isinstance(end, Element)
    assert (start.tag, end.tag) == ("a", "b")


def test_static_range_rejects_doctype() -> None:
    doc = parse("<!doctype html><html></html>")
    doctype = next(child for child in doc.children if isinstance(child, Doctype))
    div = _found(doc, "html")
    with pytest.raises(ValueError, match="doctype"):
        StaticRange(doctype, 0, div, 0)


def test_static_range_rejects_non_node() -> None:
    with pytest.raises(TypeError, match="must be nodes"):
        StaticRange("x", 0, Element("b"), 0)  # ty: ignore[invalid-argument-type]


def test_static_range_is_immutable() -> None:
    assert not hasattr(StaticRange(Element("div"), 0, Element("div"), 0), "set_start")


def test_construct_requires_a_container() -> None:
    with pytest.raises(TypeError):
        Range()  # ty: ignore[missing-argument]


def test_set_start_requires_integer_offset() -> None:
    div = Element("div")
    with pytest.raises(TypeError):
        Range(div).set_start(div, "x")  # ty: ignore[invalid-argument-type]


def test_set_start_before_rejects_non_node() -> None:
    with pytest.raises(TypeError, match="expected a node"):
        Range(Element("div")).set_start_before("x")  # ty: ignore[invalid-argument-type]


def test_select_node_contents_rejects_non_node() -> None:
    with pytest.raises(TypeError, match="expected a node"):
        Range(Element("div")).select_node_contents("x")  # ty: ignore[invalid-argument-type]


def test_collapse_rejects_extra_arguments() -> None:
    with pytest.raises(TypeError):
        Range(Element("div")).collapse(True, True)  # noqa: FBT003  # ty: ignore[too-many-positional-arguments]


def test_compare_point_requires_integer_offset() -> None:
    div = Element("div")
    with pytest.raises(TypeError):
        Range(div).compare_point(div)  # ty: ignore[missing-argument]


def test_compare_point_rejects_non_node() -> None:
    with pytest.raises(TypeError, match="expected a node"):
        Range(Element("div")).compare_point("x", 0)  # ty: ignore[invalid-argument-type]


def test_is_point_in_range_rejects_non_node() -> None:
    with pytest.raises(TypeError, match="expected a node"):
        Range(Element("div")).is_point_in_range("x", 0)  # ty: ignore[invalid-argument-type]


def test_intersects_node_rejects_non_node() -> None:
    with pytest.raises(TypeError, match="expected a node"):
        Range(Element("div")).intersects_node("x")  # ty: ignore[invalid-argument-type]


def test_static_range_requires_all_arguments() -> None:
    with pytest.raises(TypeError):
        StaticRange(Element("div"), 0)  # ty: ignore[missing-argument]


def test_extract_contents_rejects_contained_doctype() -> None:
    doc = parse("<!doctype html><html><body></body></html>")
    boundary = Range(doc, 0)
    boundary.set_end(doc, 2)
    with pytest.raises(ValueError, match="doctype"):
        boundary.extract_contents()


def test_clone_contents_skips_non_partial_edge_children() -> None:
    doc = parse("<div id=a><span>s</span><p>Hello</p><p>World</p><em>e</em></div>")
    div = _by_id(doc, "a")
    first_text = div.children[1].children[0]
    last_text = div.children[2].children[0]
    boundary = Range(first_text, 2)
    boundary.set_end(last_text, 3)
    fragment = boundary.clone_contents()
    assert _first_child_datas(fragment.children) == ["llo", "Wor"]


def test_surround_contents_empties_the_wrapper_first() -> None:
    doc = parse("<div id=a><p>x</p><p>y</p></div>")
    div = _by_id(doc, "a")
    wrapper = Element("section")
    wrapper.append(Element("old"))
    boundary = Range(div, 0)
    boundary.set_end(div, 1)
    boundary.surround_contents(wrapper)
    assert wrapper.html == "<section><p>x</p></section>"


def test_surround_contents_of_collapsed_range_inserts_empty_wrapper() -> None:
    doc = parse("<div id=a><p>x</p></div>")
    div = _by_id(doc, "a")
    boundary = Range(div, 1)
    boundary.surround_contents(Element("span"))
    assert div.html == '<div id="a"><p>x</p><span></span></div>'


def test_surround_contents_rejects_contained_doctype() -> None:
    doc = parse("<!doctype html><html><body></body></html>")
    boundary = Range(doc, 0)
    boundary.set_end(doc, 2)
    with pytest.raises(ValueError, match="doctype"):
        boundary.surround_contents(Element("span"))


def test_surround_contents_fails_when_reinsert_is_illegal() -> None:
    comment = Comment("hello world")  # a comment cannot host an inserted wrapper
    boundary = Range(comment, 0)
    boundary.set_end(comment, 5)
    with pytest.raises(ValueError, match="cannot insert"):
        boundary.surround_contents(Element("b"))


def test_set_start_rejects_out_of_range_offset() -> None:
    div = Element("div")
    with pytest.raises(IndexError, match="out of range"):
        Range(div).set_start(div, 5)


def test_compare_boundary_points_rejects_negative_how() -> None:
    div = Element("div")
    boundary = Range(div, 0)
    with pytest.raises(ValueError, match="how must be"):
        boundary.compare_boundary_points(-1, boundary)


def test_intersects_node_before_range_is_false() -> None:
    doc = parse("<div id=a><p>a</p><p>b</p><p>c</p></div>")
    div = _by_id(doc, "a")
    boundary = Range(div, 1)
    boundary.set_end(div, 2)
    assert boundary.intersects_node(div.children[0]) is False


def test_insert_node_rejects_processing_instruction_container() -> None:
    boundary = Range(ProcessingInstruction("t", "d"), 0)
    with pytest.raises(ValueError, match="cannot insert"):
        boundary.insert_node(Element("b"))


def test_insert_node_leaves_non_collapsed_range() -> None:
    doc = parse("<div id=a><p>x</p><p>y</p></div>")
    div = _by_id(doc, "a")
    boundary = Range(div, 0)
    boundary.set_end(div, 2)
    boundary.insert_node(Element("b"))  # a non-collapsed range keeps its end untouched
    assert boundary.end_offset == 2
    assert not boundary.collapsed


def test_static_range_rejects_non_node_end() -> None:
    with pytest.raises(TypeError, match="must be nodes"):
        StaticRange(Element("a"), 0, "x", 0)  # ty: ignore[invalid-argument-type]


def test_static_range_rejects_doctype_end() -> None:
    doc = parse("<!doctype html><html></html>")
    with pytest.raises(ValueError, match="doctype"):
        StaticRange(_found(doc, "html"), 0, _doctype(doc), 0)
