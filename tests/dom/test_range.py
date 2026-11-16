from __future__ import annotations

import sys
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Final, assert_type

import pytest

from turbohtml import (
    CData,
    Comment,
    Doctype,
    Document,
    Element,
    MutationObserver,
    Node,
    ProcessingInstruction,
    Range,
    StaticRange,
    Text,
    parse,
    parse_xml,
)
from turbohtml.transform import Transform

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from types import ModuleType

    from work_steps import WorkSteps


def _doctype(doc: Document) -> Doctype:
    return next(child for child in doc.children if isinstance(child, Doctype))


def test_construct_collapsed_at_container() -> None:
    div = _by_id(parse("<div id=a><p>x</p></div>"), "a")
    boundary = Range(div, 0)
    assert boundary.collapsed
    assert (boundary.start_container, boundary.end_container, boundary.start_offset, boundary.end_offset) == (
        div,
        div,
        0,
        0,
    )


def _by_id(root: Element | Document, value: str) -> Element:
    node = root.find(id=value)
    assert node is not None, value
    return node


def test_construct_defaults_offset_to_zero() -> None:
    div = Element("div")
    assert Range(div).start_offset == 0


def test_construct_rejects_non_node() -> None:
    with pytest.raises(TypeError, match="container must be a node"):
        Range("nope")  # ty: ignore[invalid-argument-type]  # passes a wrong type on purpose to check the runtime rejection


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


def _found(root: Element | Document, tag: str) -> Element:
    node = root.find(tag)
    assert node is not None, tag
    return node


def test_set_start_after_end_drags_end() -> None:
    div = _by_id(parse("<div id=a><p>a</p><p>b</p><p>c</p></div>"), "a")
    boundary = Range(div, 1)
    boundary.set_start(div, 2)  # new start lands past the collapsed end, so the end follows it
    assert (boundary.start_offset, boundary.end_offset) == (2, 2)


def test_set_end_before_start_drags_start() -> None:
    div = _by_id(parse("<div id=a><p>a</p><p>b</p><p>c</p></div>"), "a")
    boundary = Range(div, 2)
    boundary.set_end(div, 1)
    assert (boundary.start_offset, boundary.end_offset) == (1, 1)


def test_set_boundary_keeps_order_when_valid() -> None:
    div = _by_id(parse("<div id=a><p>a</p><p>b</p><p>c</p></div>"), "a")
    boundary = Range(div, 0)
    boundary.set_end(div, 2)
    boundary.set_start(div, 1)
    assert (boundary.start_offset, boundary.end_offset) == (1, 2)


def test_set_start_across_trees_collapses() -> None:
    div = _by_id(parse("<div id=a><p>x</p></div>"), "a")
    other = Element("section")
    boundary = Range(div, 0)
    boundary.set_end(div, 1)
    boundary.set_start(other, 0)  # a different tree drags the end onto the new start
    assert boundary.collapsed
    assert boundary.start_container == other


def test_set_end_across_trees_collapses() -> None:
    div = _by_id(parse("<div id=a><p>x</p></div>"), "a")
    other = Element("section")
    boundary = Range(div, 0)
    boundary.set_end(other, 0)
    assert boundary.collapsed
    assert boundary.end_container == other


def test_set_start_across_roots_same_tree_collapses() -> None:
    div = _by_id(parse("<div id=a><p>x</p><p>y</p></div>"), "a")
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
        boundary.set_start("x", 0)  # ty: ignore[invalid-argument-type]  # passes a wrong type on purpose to check the runtime rejection


@pytest.mark.parametrize(
    "setter",
    ["set_start_before", "set_start_after", "set_end_before", "set_end_after", "select_node"],
)
def test_reference_setters_reject_parentless(setter: str) -> None:
    boundary = Range(Element("div"))
    with pytest.raises(ValueError, match="no parent"):
        getattr(boundary, setter)(Element("span"))


def test_set_start_after_and_end_before_bracket_a_node() -> None:
    div = _by_id(parse("<div id=a><p>a</p><b>bold</b><p>c</p></div>"), "a")
    boundary = Range(div, 0)
    boundary.set_start_after(div.children[0])
    boundary.set_end_before(div.children[2])
    assert (boundary.start_offset, boundary.end_offset) == (1, 2)
    assert _tags(boundary.clone_contents().children)[0] == "b"


def _tags(nodes: Iterable[Node]) -> list[str]:
    # The tag names of a run of nodes, asserting each is an element.
    names = []
    for node in nodes:
        assert isinstance(node, Element)
        names.append(node.tag)
    return names


def test_select_node_wraps_the_node() -> None:
    doc = parse("<div id=a><p>a</p><b>bold</b><p>c</p></div>")
    boundary = Range(_by_id(doc, "a"), 0)
    boundary.select_node(_found(doc, "b"))
    assert (boundary.start_offset, boundary.end_offset) == (1, 2)


def test_select_node_contents_spans_children() -> None:
    div = _by_id(parse("<div id=a><p>a</p><p>b</p></div>"), "a")
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
def test_collapse(to_start: bool, expected: tuple[int, int]) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]  # pytest case, not a flag
    div = _by_id(parse("<div id=a><p>a</p><p>b</p></div>"), "a")
    boundary = Range(div, 0)
    boundary.set_end(div, 2)
    boundary.collapse(to_start)
    assert (boundary.start_offset, boundary.end_offset) == expected


def test_collapse_defaults_to_end() -> None:
    div = _by_id(parse("<div id=a><p>a</p><p>b</p></div>"), "a")
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
    div = _by_id(parse("<div id=a><p>a</p><p>b</p><p>c</p></div>"), "a")
    left = Range(div, 0)
    left.set_end(div, 1)
    right = Range(div, 0)
    right.set_end(div, 2)
    assert left.compare_boundary_points(how, right) == expected


def test_compare_boundary_points_rejects_bad_how() -> None:
    boundary = Range(Element("div"), 0)
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
        boundary.compare_boundary_points(0, object())  # ty: ignore[invalid-argument-type]  # passes a wrong type on purpose to check the runtime rejection


@pytest.mark.parametrize(
    ("offset", "expected"),
    [pytest.param(0, -1, id="before"), pytest.param(1, 0, id="inside"), pytest.param(3, 1, id="after")],
)
def test_compare_point(offset: int, expected: int) -> None:
    div = _by_id(parse("<div id=a><p>a</p><p>b</p><p>c</p></div>"), "a")
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
def test_is_point_in_range(offset: int, expected: bool) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]  # pytest expectation, not a flag
    div = _by_id(parse("<div id=a><p>a</p><p>b</p><p>c</p></div>"), "a")
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
    div = _by_id(parse("<div id=a><p>a</p><p>b</p><p>c</p></div>"), "a")
    boundary = Range(div, 1)
    boundary.set_end(div, 2)
    assert boundary.intersects_node(div.children[1]) is True


def test_intersects_node_disjoint() -> None:
    div = _by_id(parse("<div id=a><p>a</p><p>b</p><p>c</p></div>"), "a")
    boundary = Range(div, 1)
    boundary.set_end(div, 2)
    assert boundary.intersects_node(div.children[2]) is False


def test_intersects_node_root_has_no_parent() -> None:
    div = _by_id(parse("<div id=a><p>a</p></div>"), "a")
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
    div = _by_id(parse("<div id=a><p>x</p><p>y</p><span>z</span></div>"), "a")
    boundary = Range(div, 0)
    boundary.set_end(div, 2)
    fragment = boundary.clone_contents()
    assert _tags(fragment.children) == ["p", "p"]
    assert _tags(div.children) == ["p", "p", "span"]


def test_clone_contents_of_collapsed_range_is_empty() -> None:
    fragment = Range(Element("div"), 0).clone_contents()
    assert list(fragment.children) == []


def test_clone_contents_within_one_text_node() -> None:
    text = _found(parse("<p>Hello World</p>"), "p").children[0]
    boundary = Range(text, 0)
    boundary.set_end(text, 5)
    fragment = boundary.clone_contents()
    assert _data(fragment.children[0]) == "Hello"
    assert _data(text) == "Hello World"


def _data(node: Node) -> str:
    # The character data of a node, asserting it is a Text node.
    assert isinstance(node, Text)
    return node.data


def test_clone_contents_partial_text_boundaries() -> None:
    div = _by_id(parse("<div id=a><p>Hello</p><p>World</p></div>"), "a")
    first_text = div.children[0].children[0]
    last_text = div.children[1].children[0]
    boundary = Range(first_text, 2)
    boundary.set_end(last_text, 3)
    fragment = boundary.clone_contents()
    assert _first_child_datas(fragment.children) == ["llo", "Wor"]
    assert _data(first_text) == "Hello"


def _first_child_datas(nodes: Iterable[Node]) -> list[str]:
    # The character data of each node's first child.
    return [_data(node.children[0]) for node in nodes]


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
    div = _by_id(parse("<div id=a><p>x</p><p>y</p><span>z</span></div>"), "a")
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
    section = _found(parse("<div id=a><section><p>x</p><p>y</p></section></div>"), "section")
    inner = section.children[1].children[0]
    boundary = Range(section, 0)
    boundary.set_start(section, 0)
    boundary.set_end(inner, 1)
    boundary.extract_contents()
    assert (boundary.start_container, boundary.start_offset) == (section, 0)


def test_extract_contents_within_one_text_node() -> None:
    text = _found(parse("<p>Hello World</p>"), "p").children[0]
    boundary = Range(text, 0)
    boundary.set_end(text, 6)
    fragment = boundary.extract_contents()
    assert _data(fragment.children[0]) == "Hello "
    assert _data(text) == "World"


def test_extract_contents_partial_text_boundaries() -> None:
    div = _by_id(parse("<div id=a><p>Hello</p><p>World</p></div>"), "a")
    first_text = div.children[0].children[0]
    last_text = div.children[1].children[0]
    boundary = Range(first_text, 2)
    boundary.set_end(last_text, 3)
    fragment = boundary.extract_contents()
    assert _first_child_datas(fragment.children) == ["llo", "Wor"]
    assert _data(first_text) == "He"
    assert _data(last_text) == "ld"


def test_extract_contents_of_collapsed_range_is_empty() -> None:
    fragment = Range(Element("div"), 0).extract_contents()
    assert list(fragment.children) == []


def test_delete_contents_removes_without_returning() -> None:
    div = _by_id(parse("<div id=a><p>x</p><p>y</p><span>z</span></div>"), "a")
    boundary = Range(div, 0)
    boundary.set_end(div, 2)
    assert boundary.delete_contents() is None
    assert _tags(div.children) == ["span"]
    assert boundary.collapsed


def test_delete_contents_within_one_text_node() -> None:
    text = _found(parse("<p>Hello World</p>"), "p").children[0]
    boundary = Range(text, 0)
    boundary.set_end(text, 6)
    boundary.delete_contents()
    assert _data(text) == "World"


def test_insert_node_into_element() -> None:
    div = _by_id(parse("<div id=a><p>x</p><p>y</p></div>"), "a")
    Range(div, 1).insert_node(Element("b"))
    assert _tags(div.children) == ["p", "b", "p"]


def test_delete_contents_from_an_empty_parsed_comment() -> None:
    # a parsed empty comment has a NULL text pointer, which memcpy may not receive even for length 0
    paragraph: Final = _by_id(parse("<p id=a><!---->x</p>"), "a")
    comment, text = paragraph.children
    boundary: Final = Range(comment, 0)
    boundary.set_end(text, 1)
    boundary.delete_contents()
    assert paragraph.serialize() == '<p id="a"><!----></p>'


def test_insert_node_preserves_hash_across_trees() -> None:
    node = Element("b")
    held = {node}
    destination = Element("div")
    Range(destination).insert_node(node)
    assert node in held
    assert destination.children[0] in held


def test_insert_node_splits_text() -> None:
    doc = parse("<p>HelloWorld</p>")
    Range(_found(doc, "p").children[0], 5).insert_node(Element("b"))
    assert _found(doc, "p").html == "<p>Hello<b></b>World</p>"


def test_insert_node_extends_collapsed_range() -> None:
    boundary = Range(_by_id(parse("<div id=a><p>x</p></div>"), "a"), 1)
    boundary.insert_node(Element("b"))
    assert not boundary.collapsed
    assert boundary.end_offset == 2


def test_insert_fragment_extends_by_its_length() -> None:
    div = _by_id(parse("<div id=a><p>x</p></div>"), "a")
    donor_div = _by_id(parse("<div id=b><i>i</i><u>u</u></div>"), "b")
    fragment = Range(donor_div, 0)
    fragment.set_end(donor_div, 2)
    piece = fragment.extract_contents()
    boundary = Range(div, 1)
    boundary.insert_node(piece)
    assert boundary.end_offset == 3


def test_insert_node_moves_within_same_tree() -> None:
    doc = parse("<div id=a><p>x</p><span>y</span></div>")
    div = _by_id(doc, "a")
    Range(div, 0).insert_node(_found(doc, "span"))
    assert _tags(div.children) == ["span", "p"]


def test_insert_node_before_reference_that_is_the_node() -> None:
    div = _by_id(parse("<div id=a><p>x</p><span>y</span></div>"), "a")
    Range(div, 0).insert_node(div.children[0])  # the node already sits at the reference position
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


def _wrap_last_match_first(text: Text) -> None:
    for offset in range(len(text.data) - 2, -1, -2):  # the earlier offsets stay valid
        hit = Range(text, offset)
        hit.set_end(text, offset + 1)
        hit.surround_contents(Element("mark"))


def _wrap_first_match_first(text: Text) -> None:
    rest: Node | None = text
    while isinstance(rest, Text) and "x" in rest.data:
        offset = rest.data.index("x")
        hit = Range(rest, offset)
        hit.set_end(rest, offset + 1)
        mark = Element("mark")
        hit.surround_contents(mark)
        rest = mark.next_sibling  # the split left the unsearched text after the wrapper


def _delete_every_match(text: Text) -> None:
    for offset in range(len(text.data) - 2, 0, -2):
        hit = Range(text, offset)
        hit.set_end(text, offset + 1)
        hit.delete_contents()


def _insert_between_matches(text: Text) -> None:
    for offset in range(len(text.data) - 2, 0, -2):
        Range(text, offset).insert_node(Element("wbr"))


@pytest.mark.parametrize(
    "edit",
    [
        pytest.param(_wrap_last_match_first, id="surround-last-first"),
        pytest.param(_wrap_first_match_first, id="surround-first-first"),
        pytest.param(_delete_every_match, id="delete"),
        pytest.param(_insert_between_matches, id="insert"),
    ],
)
def test_repeated_edits_of_one_text_node_keep_no_copy_per_edit(edit: Callable[[Text], None]) -> None:
    # PyPy has no tracemalloc; the arena that the peak measures is allocated through PyMem, which CPython traces
    tracing: Final[ModuleType] = pytest.importorskip("tracemalloc")
    text = _data_node(parse(f"<p>{'xo' * 1500}</p>"))
    tracing.start()
    try:
        edit(text)
        peak: Final[int] = tracing.get_traced_memory()[1]
    finally:
        tracing.stop()
    assert peak < 4 * 1024 * 1024  # the copies of the remaining text take 10 to 19 MiB on the old code


def _data_node(doc: Document) -> Text:
    text = _found(doc, "p").children[0]
    assert isinstance(text, Text)
    return text


@pytest.mark.parametrize(("edited", "untouched"), [("option", "selectedcontent"), ("selectedcontent", "option")])
def test_edit_in_place_leaves_text_shared_with_a_parser_clone(edited: str, untouched: str) -> None:
    doc = parse("<select><button><selectedcontent></selectedcontent></button><option>hello world</option></select>")
    text = _found(doc, edited).children[0]
    for expected in ("hllo world", "hlo world"):  # the second run closes up inside the node's own buffer
        cut = Range(text, 1)
        cut.set_end(text, 2)
        cut.delete_contents()
        assert _data(text) == expected
        assert _data(_found(doc, untouched).children[0]) == "hello world"


def test_split_halves_edit_independently() -> None:
    text = Text("abcdefghij")
    paragraph = Element("p", children=[text])
    cut = Range(text, 4)
    cut.set_end(text, 6)
    cut.delete_contents()  # "abcdghij", now in a buffer of the node's own
    Range(text, 4).insert_node(Element("br"))
    tail = paragraph.children[2]
    assert isinstance(tail, Text)
    assert (_data(text), _data(tail)) == ("abcd", "ghij")
    for node, expected in ((text, "ad"), (tail, "gj")):
        cut = Range(node, 1)
        cut.set_end(node, 3)
        cut.delete_contents()
        assert _data(node) == expected
    assert (_data(text), _data(tail)) == ("ad", "gj")
    Range(tail, 2).insert_node(Element("hr"))  # a split at the end leaves an empty tail
    assert [_data(node) for node in paragraph.children if isinstance(node, Text)] == ["ad", "gj", ""]
    tail.data = "rewritten"
    assert (_data(text), _data(tail)) == ("ad", "rewritten")


def test_edit_in_place_records_the_old_text() -> None:
    text = Text("abcdef")
    Element("p", children=[text])
    observer = MutationObserver()
    observer.observe(text, character_data=True, character_data_old_value=True)
    for start in (2, 1):
        cut = Range(text, start)
        cut.set_end(text, start + 1)
        cut.delete_contents()
    assert [record.old_value for record in observer.take_records()] == ["abcdef", "abdef"]
    assert text.data == "adef"


def test_insert_node_at_stale_offset_past_children() -> None:
    div = _by_id(parse("<div id=a><p>a</p><i>b</i><u>c</u></div>"), "a")
    boundary = Range(div, 3)  # a valid end offset when the range is made
    div.remove("u")  # ...then the tree shrinks under it, so the walk runs off the end
    boundary.insert_node(Element("x"))
    assert _tags(div.children) == ["p", "i", "x"]


def test_replaced_text_moves_boundaries_into_the_new_value() -> None:
    text = Text("A" * 64)
    Element("p", children=[text])
    inside = Range(text, 10)
    inside.set_end(text, 60)
    text.data = "xy"
    assert (inside.start_offset, inside.end_offset) == (0, 0)
    assert inside.compare_point(text, 1) == 1


def test_deleted_run_moves_later_boundaries_back() -> None:
    text = Text("abcdefgh")
    Element("p", children=[text])
    inside = Range(text, 3)
    before = Range(text, 1)
    after = Range(text, 6)
    cut = Range(text, 2)
    cut.set_end(text, 4)
    cut.delete_contents()
    assert text.data == "abefgh"
    assert (inside.start_offset, before.start_offset) == (2, 1)
    assert (after.start_offset, after.end_offset) == (4, 4)


def test_split_moves_later_boundaries_into_the_new_node() -> None:
    text = Text("abcdefgh")
    paragraph = Element("p", children=[text])
    later = Range(text, 6)
    at_split = Range(text, 3)
    at_parent = Range(paragraph, 1)
    Range(text, 3).insert_node(Element("br"))
    tail = paragraph.children[2]
    assert (later.start_container == tail, later.start_offset) == (True, 3)
    assert (at_split.start_container == text, at_split.start_offset) == (True, 3)
    assert (at_parent.start_container == paragraph, at_parent.start_offset) == (True, 3)


def test_normalize_folds_boundaries_into_the_first_text() -> None:
    first, second = Text("ab"), Text("cd")
    paragraph = Element("p", children=[first, second])
    in_second = Range(second, 1)
    between = Range(paragraph, 1)
    paragraph.normalize()
    assert len(paragraph.children) == 1
    assert (in_second.start_container == first, in_second.start_offset) == (True, 3)
    assert (between.start_container == paragraph, between.start_offset) == (True, 1)


def test_removed_element_moves_its_boundaries_to_the_parent() -> None:
    doc = parse("<p>one<b>two</b>three</p>")
    paragraph = _found(doc, "p")
    bold = _found(doc, "b")
    inside = Range(bold.children[0], 1)
    after = Range(paragraph, 3)
    bold.decompose()
    assert (inside.start_container == paragraph, inside.start_offset) == (True, 1)
    assert (after.start_container == paragraph, after.start_offset) == (True, 2)


def test_normalize_folds_an_empty_text_boundary_into_the_text_before() -> None:
    first = Text("ab")
    paragraph = Element("p", children=[first, Text(""), Text("cd")])
    at_empty = Range(paragraph.children[1], 0)
    paragraph.normalize()
    assert len(paragraph.children) == 1
    assert (at_empty.start_container == first, at_empty.start_offset) == (True, 2)


def test_decomposing_a_detached_element_keeps_live_ranges() -> None:
    doc = parse("<p><b>x</b>y</p>")
    bold = _found(doc, "b")
    boundary = Range(doc, 0)
    bold.decompose()
    bold.decompose()
    assert boundary.start_container == doc


def test_unwrap_moves_a_boundary_in_the_moved_text_to_the_parent() -> None:
    doc = parse("<p>one<b>two</b>three</p>")
    paragraph = _found(doc, "p")
    boundary = Range(_found(doc, "b").children[0], 1)
    _found(doc, "b").unwrap()
    assert paragraph.html == "<p>onetwothree</p>"
    assert (boundary.start_container == paragraph, boundary.start_offset) == (True, 2)


def test_prune_moves_a_boundary_inside_a_removed_text_to_its_parent() -> None:
    doc = parse("<p>one<b>two</b>three</p>")
    paragraph = _found(doc, "p")
    inside = Range(paragraph.children[0], 1)
    doc.prune("b")
    assert (inside.start_container == paragraph, inside.start_offset) == (True, 0)


def test_appended_child_keeps_boundaries_on_their_container() -> None:
    paragraph = Element("p", children=[Text("a")])
    at_end = Range(paragraph, 1)
    paragraph.append(Element("b"))
    assert (at_end.start_container == paragraph, at_end.start_offset) == (True, 1)


def test_strip_tags_moves_a_boundary_in_the_unwrapped_text_to_the_parent() -> None:
    doc = parse("<p>one<b>two</b>three</p>")
    paragraph = _found(doc, "p")
    boundary = Range(_found(doc, "b").children[0], 1)
    doc.strip_tags("b")
    assert paragraph.html == "<p>onetwothree</p>"
    assert (boundary.start_container == paragraph, boundary.start_offset) == (True, 2)


def test_insertion_moves_a_boundary_that_only_ends_in_the_parent() -> None:
    paragraph = Element("p", children=[Text("a"), Element("i")])
    after_text = Range(paragraph, 2)
    start_in_text = Range(paragraph.children[0], 0)
    start_in_text.set_end(paragraph, 2)
    paragraph.children[1].insert_before(Element("b"))
    assert (start_in_text.end_container == paragraph, start_in_text.end_offset) == (True, 3)
    assert (after_text.start_container == paragraph, after_text.start_offset) == (True, 3)


@pytest.mark.parametrize(
    "operation",
    [
        pytest.param(lambda boundary: boundary.clone_contents(), id="clone_contents"),
        pytest.param(lambda boundary: boundary.extract_contents(), id="extract_contents"),
        pytest.param(lambda boundary: boundary.delete_contents(), id="delete_contents"),
    ],
)
def test_content_operation_past_the_boundary_path_limit_raises(operation: Callable[[Range], object]) -> None:
    doc = parse_xml(f"<r>{'<p>' * 400}<i>x</i>{'</p>' * 400}{'<q>' * 400}<j>y</j>{'</q>' * 400}</r>")
    boundary = Range(_found(doc, "i").children[0], 0)
    boundary.set_end(_found(doc, "j").children[0], 1)
    with pytest.raises(RecursionError, match="does not support boundary paths"):
        operation(boundary)


@pytest.mark.parametrize(
    "replace",
    [
        pytest.param(lambda paragraph: paragraph.set_inner_html("<i>x</i>"), id="set_inner_html"),
        pytest.param(lambda paragraph: paragraph.set_text("x"), id="set_text"),
        pytest.param(lambda paragraph: paragraph.clear(), id="clear"),
    ],
)
def test_replacing_children_collapses_a_boundary_in_the_removed_text(replace: Callable[[Element], object]) -> None:
    paragraph = _found(parse("<p>hello <b>bold</b> tail</p>"), "p")
    boundary = Range(paragraph.children[0], 2)
    replace(paragraph)
    assert (boundary.start_container == paragraph, boundary.start_offset) == (True, 0)
    assert (boundary.end_container == paragraph, boundary.end_offset) == (True, 0)


def test_shadow_set_inner_html_collapses_a_boundary_in_the_removed_child() -> None:
    root = _by_id(parse("<div id=h><span>s</span></div>"), "h").attach_shadow(mode="open")
    root.append(Element("p"))
    boundary = Range(root.children[0], 0)
    root.set_inner_html("<i>q</i>")
    assert (boundary.start_container == root, boundary.start_offset) == (True, 0)


def test_transform_with_strip_space_leaves_source_boundaries_in_place() -> None:
    doc = parse_xml("<r><a>x</a>   <b>y</b></r>")
    root = _found(doc, "r")
    whitespace = root.children[1]
    inside = Range(whitespace, 1)
    after = Range(root, 2)
    stylesheet = Transform(
        parse_xml(
            '<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="1.0">'
            '<xsl:strip-space elements="*"/><xsl:output method="text"/>'
            '<xsl:template match="/">x</xsl:template></xsl:stylesheet>'
        )
    )
    assert stylesheet(doc) == "x"
    assert (inside.start_container == whitespace, inside.start_offset) == (True, 1)
    assert (after.start_container == root, after.start_offset) == (True, 2)


def test_insert_node_rejects_self() -> None:
    div = _by_id(parse("<div id=a><p>x</p></div>"), "a")
    boundary = Range(div, 0)
    with pytest.raises(ValueError, match="cannot insert"):
        boundary.insert_node(div)


def test_insert_node_rejects_document() -> None:
    doc = parse("<div id=a><p>x</p></div>")
    boundary = Range(_by_id(doc, "a"), 0)
    with pytest.raises(ValueError, match="Document"):
        boundary.insert_node(doc)


def test_insert_node_rejects_ancestor_cycle() -> None:
    doc = parse("<div id=a><p>x</p></div>")
    div = _by_id(doc, "a")
    boundary = Range(_found(doc, "p"), 0)
    with pytest.raises(ValueError, match="own subtree"):
        boundary.insert_node(div)


def test_insert_node_rejects_non_node() -> None:
    boundary = Range(Element("div"))
    with pytest.raises(TypeError, match="expected a node"):
        boundary.insert_node("x")  # ty: ignore[invalid-argument-type]  # passes a wrong type on purpose to check the runtime rejection


def test_surround_contents_wraps_text_slice() -> None:
    doc = parse("<p>one two three</p>")
    text = _found(doc, "p").children[0]
    boundary = Range(text, 4)
    boundary.set_end(text, 7)
    boundary.surround_contents(Element("em"))
    assert _found(doc, "p").html == "<p>one <em>two</em> three</p>"


def test_surround_contents_selects_the_wrapper() -> None:
    div = _by_id(parse("<div id=a><p>x</p><p>y</p></div>"), "a")
    boundary = Range(div, 0)
    boundary.set_end(div, 1)
    wrapper = assert_type(boundary.surround_contents(Element("section")), Element)
    assert isinstance(wrapper, Element)
    assert wrapper.tag == "section"
    assert boundary.start_container == div
    assert _tags(boundary.clone_contents().children) == ["section"]


def test_surround_contents_rejects_partial_non_text() -> None:
    div = _by_id(parse("<div id=a><p>one</p><p>two</p></div>"), "a")
    boundary = Range(div.children[0].children[0], 1)
    boundary.set_end(div, 2)
    with pytest.raises(ValueError, match="partially contains"):
        boundary.surround_contents(Element("em"))


@pytest.mark.parametrize(
    "wrapper",
    [
        pytest.param(lambda doc: doc, id="document"),
        pytest.param(_doctype, id="doctype"),
        pytest.param(lambda _: Text("x"), id="text"),
        pytest.param(lambda _: Comment("x"), id="comment"),
        pytest.param(lambda _: CData("x"), id="cdata"),
        pytest.param(lambda _: ProcessingInstruction("x", "y"), id="processing-instruction"),
    ],
)
def test_surround_contents_rejects_bad_wrapper_type(wrapper: Callable[[Document], Node]) -> None:
    doc = parse("<!doctype html><div id=a><p>x</p></div>")
    div = _by_id(doc, "a")
    boundary = Range(div, 0)
    boundary.set_end(div, 1)
    with pytest.raises(ValueError, match="must be an element"):
        boundary.surround_contents(wrapper(doc))


def test_surround_contents_rejecting_a_text_wrapper_keeps_the_contents() -> None:
    paragraph = _by_id(parse("<p id=a>one<b>two</b>three</p>"), "a")
    boundary = Range(paragraph, 1)
    boundary.set_end(paragraph, 3)
    with pytest.raises(ValueError, match="must be an element"):
        boundary.surround_contents(Text("x"))
    assert paragraph.html == '<p id="a">one<b>two</b>three</p>'


def test_surround_contents_rejects_fragment_wrapper() -> None:
    div = _by_id(parse("<div id=a><p>x</p><p>y</p></div>"), "a")
    snapshot = Range(div, 0)
    snapshot.set_end(div, 2)
    fragment = snapshot.clone_contents()
    boundary = Range(div, 0)
    boundary.set_end(div, 1)
    with pytest.raises(ValueError, match="must be an element"):
        boundary.surround_contents(fragment)


def test_surround_contents_rejects_non_node() -> None:
    boundary = Range(Element("div"))
    with pytest.raises(TypeError, match="expected a node"):
        boundary.surround_contents("x")  # ty: ignore[invalid-argument-type]  # passes a wrong type on purpose to check the runtime rejection


def test_clone_range_is_independent() -> None:
    div = _by_id(parse("<div id=a><p>x</p><p>y</p></div>"), "a")
    boundary = Range(div, 0)
    boundary.set_end(div, 1)
    twin = boundary.clone_range()
    assert isinstance(twin, Range)
    assert (twin.start_offset, twin.end_offset) == (0, 1)
    twin.set_end(div, 2)
    assert boundary.end_offset == 1  # the clone's edits do not touch the original


def test_repr() -> None:
    boundary = Range(_by_id(parse("<div id=a><p>x</p></div>"), "a"), 0)
    assert repr(boundary) == "<Range start=(Element('div'), 0) end=(Element('div'), 0)>"


def test_char_data_length_covers_comment_and_cdata() -> None:
    assert Range(Comment("abc"), 3).start_offset == 3
    assert Range(CData("xy"), 2).start_offset == 2


def test_static_range_stores_boundaries() -> None:
    div = _by_id(parse("<div id=a><p>x</p><p>y</p></div>"), "a")
    snapshot = StaticRange(div, 0, div, 2)
    assert (snapshot.start_container, snapshot.start_offset, snapshot.end_container, snapshot.end_offset) == (
        div,
        0,
        div,
        2,
    )
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
        StaticRange("x", 0, Element("b"), 0)  # ty: ignore[invalid-argument-type]  # passes a wrong type on purpose to check the runtime rejection


def test_static_range_is_immutable() -> None:
    assert not hasattr(StaticRange(Element("div"), 0, Element("div"), 0), "set_start")


def test_construct_requires_a_container() -> None:
    with pytest.raises(TypeError):
        Range()  # ty: ignore[missing-argument]  # omits a required argument on purpose to check the error


def test_set_start_requires_integer_offset() -> None:
    div = Element("div")
    with pytest.raises(TypeError):
        Range(div).set_start(div, "x")  # ty: ignore[invalid-argument-type]  # passes a wrong type on purpose to check the runtime rejection


def test_set_start_before_rejects_non_node() -> None:
    with pytest.raises(TypeError, match="expected a node"):
        Range(Element("div")).set_start_before("x")  # ty: ignore[invalid-argument-type]  # passes a wrong type on purpose to check the runtime rejection


def test_select_node_contents_rejects_non_node() -> None:
    with pytest.raises(TypeError, match="expected a node"):
        Range(Element("div")).select_node_contents("x")  # ty: ignore[invalid-argument-type]  # passes a wrong type on purpose to check the runtime rejection


def test_collapse_rejects_extra_arguments() -> None:
    with pytest.raises(TypeError):
        Range(Element("div")).collapse(True, True)  # ruff:ignore[boolean-positional-value-in-call]  # ty: ignore[too-many-positional-arguments]  # positional booleans pass the private helper's real signature


def test_compare_point_requires_integer_offset() -> None:
    div = Element("div")
    with pytest.raises(TypeError):
        Range(div).compare_point(div)  # ty: ignore[missing-argument]  # omits a required argument on purpose to check the error


def test_compare_point_rejects_non_node() -> None:
    with pytest.raises(TypeError, match="expected a node"):
        Range(Element("div")).compare_point("x", 0)  # ty: ignore[invalid-argument-type]  # passes a wrong type on purpose to check the runtime rejection


def test_is_point_in_range_rejects_non_node() -> None:
    with pytest.raises(TypeError, match="expected a node"):
        Range(Element("div")).is_point_in_range("x", 0)  # ty: ignore[invalid-argument-type]  # passes a wrong type on purpose to check the runtime rejection


def test_intersects_node_rejects_non_node() -> None:
    with pytest.raises(TypeError, match="expected a node"):
        Range(Element("div")).intersects_node("x")  # ty: ignore[invalid-argument-type]  # passes a wrong type on purpose to check the runtime rejection


def test_static_range_requires_all_arguments() -> None:
    with pytest.raises(TypeError):
        StaticRange(Element("div"), 0)  # ty: ignore[missing-argument]  # omits a required argument on purpose to check the error


def test_extract_contents_rejects_contained_doctype() -> None:
    doc = parse("<!doctype html><html><body></body></html>")
    boundary = Range(doc, 0)
    boundary.set_end(doc, 2)
    with pytest.raises(ValueError, match="doctype"):
        boundary.extract_contents()


def test_clone_contents_skips_non_partial_edge_children() -> None:
    div = _by_id(parse("<div id=a><span>s</span><p>Hello</p><p>World</p><em>e</em></div>"), "a")
    first_text = div.children[1].children[0]
    last_text = div.children[2].children[0]
    boundary = Range(first_text, 2)
    boundary.set_end(last_text, 3)
    fragment = boundary.clone_contents()
    assert _first_child_datas(fragment.children) == ["llo", "Wor"]


def test_surround_contents_empties_the_wrapper_first() -> None:
    div = _by_id(parse("<div id=a><p>x</p><p>y</p></div>"), "a")
    wrapper = Element("section")
    wrapper.append(Element("old"))
    boundary = Range(div, 0)
    boundary.set_end(div, 1)
    boundary.surround_contents(wrapper)
    assert wrapper.html == "<section><p>x</p></section>"


def test_surround_contents_of_collapsed_range_inserts_empty_wrapper() -> None:
    div = _by_id(parse("<div id=a><p>x</p></div>"), "a")
    Range(div, 1).surround_contents(Element("span"))
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
    boundary = Range(Element("div"), 0)
    with pytest.raises(ValueError, match="how must be"):
        boundary.compare_boundary_points(-1, boundary)


def test_intersects_node_before_range_is_false() -> None:
    div = _by_id(parse("<div id=a><p>a</p><p>b</p><p>c</p></div>"), "a")
    boundary = Range(div, 1)
    boundary.set_end(div, 2)
    assert boundary.intersects_node(div.children[0]) is False


def test_insert_node_rejects_processing_instruction_container() -> None:
    boundary = Range(ProcessingInstruction("t", "d"), 0)
    with pytest.raises(ValueError, match="cannot insert"):
        boundary.insert_node(Element("b"))


def test_insert_node_leaves_non_collapsed_range() -> None:
    div = _by_id(parse("<div id=a><p>x</p><p>y</p></div>"), "a")
    boundary = Range(div, 0)
    boundary.set_end(div, 2)
    boundary.insert_node(Element("b"))  # the end follows the child inserted before it
    assert boundary.end_offset == 3
    assert not boundary.collapsed


def test_static_range_rejects_non_node_end() -> None:
    with pytest.raises(TypeError, match="must be nodes"):
        StaticRange(Element("a"), 0, "x", 0)  # ty: ignore[invalid-argument-type]  # passes a wrong type on purpose to check the runtime rejection


def test_static_range_rejects_doctype_end() -> None:
    doc = parse("<!doctype html><html></html>")
    with pytest.raises(ValueError, match="doctype"):
        StaticRange(_found(doc, "html"), 0, _doctype(doc), 0)


@pytest.mark.parametrize(
    "container",
    [
        pytest.param(Text("é😀"), id="text"),
        pytest.param(Comment("é😀"), id="comment"),
        pytest.param(CData("é😀"), id="cdata"),
        pytest.param(Element("div", children=[Element("i"), Element("b")]), id="element"),
    ],
)
@pytest.mark.parametrize("offset", [pytest.param(0, id="start"), pytest.param(2, id="end")])
def test_boundary_endpoint(container: Node, offset: int) -> None:
    boundary: Final = Range(container, offset)
    assert (boundary.start_container, boundary.start_offset, boundary.end_container, boundary.end_offset) == (
        container,
        offset,
        container,
        offset,
    )


@pytest.mark.parametrize(
    "container",
    [
        pytest.param(Text("é😀"), id="text"),
        pytest.param(Comment("é😀"), id="comment"),
        pytest.param(CData("é😀"), id="cdata"),
        pytest.param(Element("div", children=[Element("i"), Element("b")]), id="element"),
    ],
)
@pytest.mark.parametrize(
    "offset",
    [
        pytest.param(-sys.maxsize - 1, id="minimum"),
        pytest.param(-1, id="negative"),
        pytest.param(3, id="past"),
        pytest.param(sys.maxsize, id="maximum"),
    ],
)
def test_boundary_rejects_invalid_offset(container: Node, offset: int) -> None:
    with pytest.raises(IndexError, match="out of range"):
        Range(container, offset)


@pytest.mark.parametrize(
    "container",
    [
        pytest.param(Text("é😀"), id="text"),
        pytest.param(Comment("é😀"), id="comment"),
        pytest.param(CData("é😀"), id="cdata"),
    ],
)
def test_select_character_data_contents(container: Node) -> None:
    boundary: Final = Range(container)
    boundary.select_node_contents(container)
    assert (boundary.start_offset, boundary.end_offset) == (0, 2)


@pytest.mark.parametrize(
    ("offset", "expected"), [pytest.param(0, 1, id="before-child"), pytest.param(1, -1, id="after-child")]
)
def test_compare_descendant_with_ancestor_boundary(offset: int, expected: int) -> None:
    child: Final = Text("abc")
    parent: Final = Element("div", children=[child])
    assert Range(child, 1).compare_boundary_points(Range.START_TO_START, Range(parent, offset)) == expected


@pytest.mark.parametrize(
    "method", [pytest.param("clone_contents", id="clone"), pytest.param("extract_contents", id="extract")]
)
def test_range_contained_interval(method: str) -> None:
    root: Final = Element("div")
    root.set_inner_html("".join(f"<i>{index}</i>" for index in range(100)))
    boundary: Final = Range(root, 10)
    boundary.set_end(root, 90)
    result: Final = getattr(boundary, method)()
    assert result.html == "".join(f"<i>{index}</i>" for index in range(10, 90))


@pytest.mark.parametrize(
    "method", [pytest.param("clone_contents", id="clone"), pytest.param("extract_contents", id="extract")]
)
def test_range_partial_excludes_siblings(method: str) -> None:
    root: Final = Element("div")
    root.set_inner_html(f'<section data-x="é水😀">abcdef{"<i></i>" * 100}</section>tail')
    boundary: Final = Range(root)
    boundary.set_end(root.children[0].children[0], 3)
    result: Final = getattr(boundary, method)()
    assert result.html == '<section data-x="é水😀">abc</section>'


@pytest.mark.parametrize(
    "operation",
    [
        pytest.param(Range.clone_contents, id="clone"),
        pytest.param(Range.extract_contents, id="extract"),
        pytest.param(Range.delete_contents, id="delete"),
    ],
)
def test_content_operation_over_siblings_is_linear(work_steps: WorkSteps, operation: Callable[[Range], object]) -> None:
    work_steps.assert_linear(lambda depth: partial(operation, _sibling_range(depth)), (500, 2000))


def _sibling_range(depth: int) -> Range:
    doc = parse_xml(f"{'<w>' * depth}{'<s/>' * depth}<i>x</i><j>y</j>{'<s/>' * depth}{'</w>' * depth}")
    boundary = Range(_found(doc, "i").children[0], 0)
    boundary.set_end(_found(doc, "j").children[0], 1)
    return boundary


@pytest.mark.parametrize(
    "operation",
    [
        pytest.param(Range.clone_contents, id="clone"),
        pytest.param(Range.extract_contents, id="extract"),
        pytest.param(Range.delete_contents, id="delete"),
    ],
)
def test_content_operation_with_deep_paths_ignores_ancestors(
    work_steps: WorkSteps, operation: Callable[[Range], object]
) -> None:
    work_steps.assert_within(
        partial(operation, _deep_path_range(2000)), partial(operation, _deep_path_range(1)), factor=2
    )


def _deep_path_range(depth: int) -> Range:
    path_down = f"{'<p>' * _PATH_LEVELS}<i>x</i>{'</p>' * _PATH_LEVELS}"
    path_across = f"{'<q>' * _PATH_LEVELS}<j>y</j>{'</q>' * _PATH_LEVELS}"
    doc = parse_xml(f"{'<w>' * depth}{'<s/>' * depth}{path_down}{path_across}{'</w>' * depth}")
    boundary = Range(_found(doc, "i").children[0], 0)
    boundary.set_end(_found(doc, "j").children[0], 1)
    return boundary


# Below the 400-level limit on boundary paths, so the content operations take their recursive path.
_PATH_LEVELS: Final[int] = 380


def test_surround_contents_over_siblings_is_linear(work_steps: WorkSteps) -> None:
    work_steps.assert_linear(lambda depth: partial(_surround_range(depth).surround_contents, Element("w")), (500, 2000))


def _surround_range(depth: int) -> Range:
    inner = _found(parse_xml(f"{'<w>' * depth}<v>x{'<s/>' * depth}y</v>{'</w>' * depth}"), "v")
    boundary = Range(inner.children[0], 0)
    boundary.set_end(inner.children[-1], 1)
    return boundary


def test_common_ancestor_container_of_deep_boundaries_is_linear(work_steps: WorkSteps) -> None:
    work_steps.assert_linear(_deep_boundaries, (500, 2000))


def _deep_boundaries(depth: int) -> Callable[[], object]:
    doc = parse_xml(f"<top>{'<k>' * depth}<lo>x</lo>{'</k>' * depth}{'<m>' * depth}<hi>y</hi>{'</m>' * depth}</top>")
    boundary = Range(_found(doc, "lo").children[0], 0)
    boundary.set_end(_found(doc, "hi").children[0], 1)
    return partial(getattr, boundary, "common_ancestor_container")


def test_clone_contents_over_siblings_keeps_boundary_text() -> None:
    assert _sibling_range(3).clone_contents().text == "xy"


def test_extract_contents_over_deep_paths_keeps_boundary_text() -> None:
    assert _deep_path_range(3).extract_contents().text == "xy"


def test_surround_contents_over_siblings_wraps_boundary_text() -> None:
    wrapper = Element("w")
    _surround_range(3).surround_contents(wrapper)
    assert len(wrapper.children) == 5


def test_common_ancestor_container_of_deep_boundaries_is_the_root() -> None:
    doc = parse_xml(f"<top>{'<k>' * 3}<lo>x</lo>{'</k>' * 3}{'<m>' * 3}<hi>y</hi>{'</m>' * 3}</top>")
    boundary = Range(_found(doc, "lo").children[0], 0)
    boundary.set_end(_found(doc, "hi").children[0], 1)
    assert boundary.common_ancestor_container == _found(doc, "top")


@pytest.mark.parametrize(
    "loop",
    [
        pytest.param(lambda wide: [wide.boundary.set_start_before(kid) for kid in wide.kids], id="start-before"),
        pytest.param(lambda wide: [wide.boundary.set_start_after(kid) for kid in wide.kids], id="start-after"),
        pytest.param(lambda wide: [wide.boundary.set_end_before(kid) for kid in wide.kids], id="end-before"),
        pytest.param(lambda wide: [wide.boundary.set_end_after(kid) for kid in wide.kids], id="end-after"),
        pytest.param(lambda wide: [wide.boundary.select_node(kid) for kid in wide.kids], id="select-node"),
        pytest.param(
            lambda wide: [wide.boundary.select_node(kid) for kid in reversed(wide.kids)], id="select-backwards"
        ),
        pytest.param(lambda wide: [wide.boundary.select_node_contents(wide.parent) for _ in wide.kids], id="contents"),
        pytest.param(lambda wide: [wide.boundary.set_start(wide.parent, at) for at in wide.offsets], id="set-start"),
        pytest.param(lambda wide: [wide.boundary.set_end(wide.parent, at) for at in wide.offsets], id="set-end"),
        pytest.param(
            lambda wide: [wide.boundary.set_start(wide.parent, at) for at in reversed(wide.offsets)],
            id="set-start-backwards",
        ),
        pytest.param(lambda wide: [Range(wide.parent, at) for at in wide.offsets], id="construct"),
        pytest.param(
            lambda wide: [wide.boundary.is_point_in_range(wide.parent, at) for at in wide.offsets], id="point-offset"
        ),
        pytest.param(lambda wide: [wide.boundary.is_point_in_range(kid, 0) for kid in wide.kids], id="point-node"),
        pytest.param(lambda wide: [wide.boundary.compare_point(kid, 0) for kid in wide.kids], id="compare-point"),
        pytest.param(lambda wide: [wide.boundary.intersects_node(kid) for kid in wide.kids], id="intersects"),
        pytest.param(lambda wide: [_compare_with_last(kid, wide.kids) for kid in wide.kids], id="compare-boundaries"),
        pytest.param(lambda wide: [_select(wide.boundary, kid).clone_contents() for kid in wide.kids], id="clone"),
        pytest.param(lambda wide: [_select(wide.boundary, kid).extract_contents() for kid in wide.kids], id="extract"),
        pytest.param(lambda wide: [_select(wide.boundary, kid).delete_contents() for kid in wide.kids], id="delete"),
        pytest.param(
            lambda wide: [_select(wide.boundary, kid).surround_contents(Element("b")) for kid in wide.kids],
            id="surround",
        ),
        pytest.param(
            lambda wide: [_span(text, 0, 1).surround_contents(Element("b")) for text in wide.kids[1::2]],
            id="surround-text",
        ),
        pytest.param(
            lambda wide: [_before(wide.boundary, kid).insert_node(Element("b")) for kid in wide.kids], id="insert"
        ),
        pytest.param(
            lambda wide: [Range(text, 1).insert_node(Element("b")) for text in wide.kids[1::2]], id="insert-split"
        ),
    ],
)
def test_range_call_per_sibling_is_linear(work_steps: WorkSteps, loop: Callable[[_Wide], object]) -> None:
    work_steps.assert_linear(lambda count: partial(loop, _Wide.of(count)), (300, 1200))


@dataclass(frozen=True, slots=True, kw_only=True)
class _Wide:
    boundary: Range
    parent: Element
    kids: list[Node]

    @classmethod
    def of(cls, count: int) -> _Wide:
        parent = _found(parse_xml(f"<top><mid>{'<s>t</s>u' * count}</mid></top>"), "mid")
        return cls(boundary=Range(parent), parent=parent, kids=list(parent.children))

    @property
    def offsets(self) -> range:
        return range(len(self.kids))


def _compare_with_last(kid: Node, kids: list[Node]) -> int:
    return Range(kid, 0).compare_boundary_points(Range.START_TO_START, Range(kids[-1], 0))


def _select(boundary: Range, kid: Node) -> Range:
    boundary.select_node(kid)
    return boundary


def _before(boundary: Range, kid: Node) -> Range:
    boundary.set_start_before(kid)
    boundary.set_end_before(kid)
    return boundary


@pytest.mark.parametrize("cursor", [0, 3, 7], ids=lambda cursor: f"cursor-{cursor}")
@pytest.mark.parametrize(
    "edit",
    [
        pytest.param(lambda parent: _select(Range(parent), parent[3]).surround_contents(Element("b")), id="surround"),
        pytest.param(lambda parent: _select(Range(parent), parent[0]).surround_contents(Element("b")), id="wrap-first"),
        pytest.param(lambda parent: Range(parent, 0).insert_node(Element("b")), id="insert-first"),
        pytest.param(lambda parent: Range(parent, 4).insert_node(Element("b")), id="insert-middle"),
        pytest.param(lambda parent: Range(parent, 8).insert_node(Element("b")), id="insert-last"),
        pytest.param(lambda parent: Range(parent, 2).insert_node(parent[6]), id="move-back"),
        pytest.param(lambda parent: Range(parent, 6).insert_node(parent[1]), id="move-forward"),
        pytest.param(lambda parent: Range(parent, 2).insert_node(parent[7]), id="move-last"),
        pytest.param(lambda parent: Range(parent, 1).insert_node(_fragment(3)), id="insert-fragment"),
        pytest.param(lambda parent: Range(parent[4][0], 0).insert_node(Element("b")), id="insert-in-child"),
        pytest.param(lambda parent: _span(parent, 2, 5).extract_contents(), id="extract-middle"),
        pytest.param(lambda parent: _span(parent, 0, 2).delete_contents(), id="delete-head"),
        pytest.param(lambda parent: _span(parent, 6, 8).delete_contents(), id="delete-tail"),
        pytest.param(lambda parent: _span(parent, 0, 8).delete_contents(), id="delete-all"),
        pytest.param(lambda parent: _span(parent, 3, 4).delete_contents(), id="delete-one"),
        pytest.param(lambda parent: parent[2].insert_before(Element("b")), id="node-insert-before"),
        pytest.param(lambda parent: parent[5].extract(), id="node-extract"),
        pytest.param(lambda parent: parent.insert(0, Element("b")), id="element-insert"),
        pytest.param(lambda parent: parent.append(Element("b")), id="element-append"),
    ],
)
def test_offsets_follow_tree_edits_between_range_calls(cursor: int, edit: Callable[[Element], object]) -> None:
    parent = Element("p", children=[Element("i", children=[Text("t")]) for _ in range(8)])
    _select(Range(parent), parent[cursor])
    edit(parent)
    assert _offsets_backwards(parent) == list(range(len(parent)))[::-1]


def _offsets_backwards(parent: Element) -> list[int]:
    boundary = Range(parent)
    return [_select(boundary, child).start_offset for child in reversed(parent.children)]


@pytest.mark.parametrize("offset", [3, 8])
def test_offsets_follow_an_insert_beside_two_cursors(offset: int) -> None:
    parent = Element("p", children=[Element("i") for _ in range(8)])
    _select(Range(parent), parent[5])
    Range(parent, 2)
    Range(parent, offset).insert_node(Element("b"))
    assert _offsets_backwards(parent) == list(range(9))[::-1]


def test_offsets_follow_normalize_between_range_calls() -> None:
    parent = Element("p", children=[Text("a"), Text("b"), Element("i")])
    assert _select(Range(parent), parent[2]).start_offset == 2
    parent.normalize()
    assert _offsets_backwards(parent) == [1, 0]


def test_offsets_follow_text_split_by_insert_node() -> None:
    parent = Element("p", children=[Element("i"), Text("ab"), Element("i")])
    _select(Range(parent), parent[2])
    Range(parent[1], 1).insert_node(Element("b"))
    assert _offsets_backwards(parent) == [4, 3, 2, 1, 0]


def test_offsets_follow_surround_with_a_filled_wrapper_after_an_edit() -> None:
    parent = Element("p", children=[Element("i"), Text("ab"), Element("i")])
    _select(Range(parent), parent[2])
    parent.append(Element("i"))
    _span(parent[1], 0, 1).surround_contents(Element("b", children=[Element("u")]))
    assert _offsets_backwards(parent) == [5, 4, 3, 2, 1, 0]


def _span(parent: Node, start: int, end: int) -> Range:
    boundary = Range(parent, start)
    boundary.set_end(parent, end)
    return boundary


def _fragment(count: int) -> Node:
    return _span(Element("div", children=[Element("b") for _ in range(count)]), 0, count).extract_contents()
