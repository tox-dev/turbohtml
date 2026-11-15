from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Final

import pytest

from turbohtml import Document, Element, Range, parse, parse_xml
from turbohtml.clean import sanitize_node
from turbohtml.transform import Transform

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

    from turbohtml import DocumentFragment, Node

# every <p> reconstructs the unclosed <a>, so each reconstruction shares the one long value of its start tag
_MARKUP: Final[str] = f'<p><a data-x="{"A" * 10_000}" title="t">{"<p>x" * 1000}'
_BOUND: Final[int] = 4_000_000
_SHEET: Final[str] = (
    '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
    '<xsl:template match="/"><xsl:value-of select="count(//a)"/></xsl:template></xsl:stylesheet>'
)


def _found(root: Node, tag: str) -> Element:
    node = root.find(tag)
    assert node is not None, tag
    return node


def _clone_contents(page: Document) -> DocumentFragment:
    body: Final = _found(page, "body")
    boundary: Final = Range(body, 0)
    boundary.select_node_contents(body)
    return boundary.clone_contents()


@pytest.mark.parametrize(
    "duplicate",
    [
        pytest.param(copy.deepcopy, id="deepcopy"),
        pytest.param(lambda page: copy.deepcopy(copy.deepcopy(page)), id="deepcopy-of-a-copy"),
        pytest.param(sanitize_node, id="sanitize-node"),
        pytest.param(Document.structured_data, id="structured-data"),
        pytest.param(lambda page: Element("div").append(_found(page, "body")), id="adopt"),
        pytest.param(lambda page: Element("div").extend(_found(page, "body").children), id="adopt-siblings"),
        pytest.param(_clone_contents, id="range-clone-contents"),
    ],
)
def test_copied_document_keeps_reconstructed_values_shared(duplicate: Callable[[Document], object]) -> None:
    page: Final = parse(_MARKUP)
    assert _peak(lambda: duplicate(page)) < _BOUND


def _peak(action: Callable[[], object]) -> int:
    tracing: Final[ModuleType] = pytest.importorskip("tracemalloc")  # PyPy has none
    tracing.start()
    try:
        before: Final[int] = tracing.get_traced_memory()[0]
        action()
        return tracing.get_traced_memory()[1] - before
    finally:
        tracing.stop()


@pytest.mark.parametrize(
    "insert",
    [
        pytest.param(lambda: Element("div").set_inner_html(_MARKUP), id="set-inner-html"),
        pytest.param(lambda: Element("div").insert_adjacent_html("beforeend", _MARKUP), id="insert-adjacent-html"),
        pytest.param(lambda: Element("div").attach_shadow().set_inner_html(_MARKUP), id="shadow-set-inner-html"),
    ],
)
def test_inserted_markup_keeps_reconstructed_values_shared(insert: Callable[[], object]) -> None:
    # the top-level nodes of the parsed fragment are copied one by one: they still share
    parse(_MARKUP)  # warm the parser's own tables, which are not the copy's
    assert _peak(insert) < _BOUND


@pytest.mark.parametrize(
    "consume",
    [
        pytest.param(lambda fragment: Element("div").append(fragment), id="adopt-children"),
        pytest.param(Transform(parse_xml(_SHEET)), id="xslt-snapshot"),
    ],
)
def test_fragment_children_keep_reconstructed_values_shared(consume: Callable[[DocumentFragment], object]) -> None:
    # the children of a fragment are copied one by one as well
    fragment: Final = _clone_contents(parse(_MARKUP))
    assert _peak(lambda: consume(fragment)) < _BOUND


def test_copy_shares_many_distinct_reconstructed_elements() -> None:
    # more shared values than the memo starts with room for; each block reconstructs at most 16 of the 40 elements
    tags: Final = [f'<b class="{index:02d}-{"c" * 40}" data-i="{index}">' for index in range(40)]
    page: Final = parse(f"<p>{''.join(tags[:16])}<p>x{''.join(tags[16:32])}<p>x{''.join(tags[32:])}<p>y")
    clone: Final = copy.deepcopy(page)
    assert clone.serialize() == page.serialize()
    assert {bold.attrs["data-i"] for bold in clone.select("b")} == {str(index) for index in range(40)}


@pytest.mark.parametrize(
    ("duplicate", "pick"),
    [
        pytest.param(copy.deepcopy, lambda clone, page: (clone, page), id="deepcopy"),
        pytest.param(copy.deepcopy, lambda clone, page: (page, clone), id="deepcopy-source"),
        pytest.param(_clone_contents, lambda clone, page: (clone, page), id="range-clone"),
        pytest.param(_clone_contents, lambda clone, page: (page, clone), id="range-source"),
    ],
)
def test_copy_with_shared_values_stays_independent(
    duplicate: Callable[[Document], Node], pick: Callable[[Node, Document], tuple[Node, Node]]
) -> None:
    value: Final = "A" * 40
    page: Final = parse(f'<p><a data-x="{value}" title="t"><p>x<p>y')
    written, kept = pick(duplicate(page), page)
    first, second, third = written.select("a")
    second.attrs["data-x"] = "changed"
    del third.attrs["data-x"]
    first.attrs["lang"] = "en"
    assert [dict(anchor.attrs) for anchor in written.select("a")] == [
        {"data-x": value, "title": "t", "lang": "en"},
        {"data-x": "changed", "title": "t"},
        {"title": "t"},
    ]
    assert [dict(anchor.attrs) for anchor in kept.select("a")] == [{"data-x": value, "title": "t"}] * 3


def test_range_clone_of_an_ordinary_element_does_not_follow_later_writes() -> None:
    page: Final = parse('<div><b title="one" id="x">bold</b></div>')
    bold: Final = _found(page, "b")
    boundary: Final = Range(bold, 0)
    boundary.select_node(bold)
    copied: Final = _found(boundary.clone_contents(), "b")
    bold.attrs["title"] = "two"
    copied.attrs["lang"] = "en"
    del bold.attrs["id"]
    assert dict(bold.attrs) == {"title": "two"}
    assert dict(copied.attrs) == {"title": "one", "id": "x", "lang": "en"}
    assert [dict(again.attrs) for again in copy.deepcopy(page).select("b")] == [{"title": "two"}]


def test_shared_values_survive_adoption_across_tree_kinds() -> None:
    value: Final = "A" * 40
    holder: Final = _found(parse_xml("<root/>"), "root")
    holder.append(_found(parse(f'<p><a data-x="{value}" title="t"><p>x<p>y'), "body"))
    target: Final = Element("div")
    target.append(_found(holder, "body"))
    assert [dict(anchor.attrs) for anchor in target.select("a")] == [{"data-x": value, "title": "t"}] * 3


def test_copy_shares_short_values_of_reconstructed_elements() -> None:
    # many short values on many reconstructions: a copy keeps the attribute slots of each element but no value buffer
    attrs: Final = "".join(f' a{index}="{"x" * 15}"' for index in range(600))
    page: Final = parse(f"<p><a{attrs}>{'<p>x' * 1000}")
    slots: Final = 600 * 1000 * 24  # each copied element still owns its attribute slots, as each parsed one does
    assert _peak(lambda: copy.deepcopy(page)) < slots + _BOUND


def test_copy_keeps_an_empty_value_apart_from_the_value_stored_after_it() -> None:
    # an empty value takes no room, so it has the address of the value that follows it in the start tag
    page: Final = parse(f'<p><a first="" href="/a-long-enough-value" last="">{"<p>x" * 3}')
    clone: Final = copy.deepcopy(page)
    assert [dict(anchor.attrs) for anchor in clone.find_all("a")] == [
        {"first": "", "href": "/a-long-enough-value", "last": ""}
    ] * 4


def test_repeated_moves_keep_reconstructed_values_shared() -> None:
    # each move is its own call, so the destination must keep the value the earlier moves copied from the same source
    page: Final = parse(_MARKUP)
    children: Final = list(_found(page, "body").children)
    target: Final = Element("div")
    assert _peak(lambda: _append_each(target, children)) < _BOUND


def _append_each(target: Element, nodes: list[Node]) -> None:
    for node in nodes:
        target.append(node)


def test_repeated_moves_from_freed_sources_keep_each_value() -> None:
    # a freed source's addresses can be reused by the next source, so an address alone cannot identify a value
    letters: Final = "ABCDEFGHIJKLMNOPQRST"
    target: Final = Element("div")
    for letter in letters:
        target.append(_found(parse(f'<p><a data-x="{letter * 40}"><p>x<p>y'), "body"))
    assert [anchor.attrs["data-x"] for anchor in target.select("a")] == [
        letter * 40 for letter in letters for _ in range(3)
    ]


def test_values_moved_one_call_at_a_time_stay_independent() -> None:
    value: Final = "A" * 40
    page: Final = parse(f'<p><a data-x="{value}" title="t"><p>x<p>y')
    target: Final = Element("div")
    _append_each(target, list(_found(page, "body").children))
    written: Final = target.select("a")
    written[1].attrs["data-x"] = "changed"
    del written[2].attrs["data-x"]
    assert [dict(anchor.attrs) for anchor in target.select("a")] == [
        {"data-x": value, "title": "t"},
        {"data-x": "changed", "title": "t"},
        {"title": "t"},
    ]


def test_a_source_value_changed_between_imports_is_copied_as_changed() -> None:
    # a source keeps each value until it is freed and its values never share an address, so a later import reads the
    # value the source holds now, not the one an earlier import copied from the same address
    value: Final = "A" * 40
    page: Final = parse(f'<p><a data-x="{value}" title="t"><p>x<p>y')
    children: Final = list(_found(page, "body").children)
    target: Final = Element("div")
    target.append(children[0])
    _found(children[1], "a").attrs["data-x"] = "short"
    del _found(children[2], "a").attrs["data-x"]
    target.append(children[1])
    target.append(children[2])
    assert [dict(anchor.attrs) for anchor in target.select("a")] == [
        {"data-x": value, "title": "t"},
        {"data-x": "short", "title": "t"},
        {"title": "t"},
    ]
