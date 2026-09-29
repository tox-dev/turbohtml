"""The pyquery-style fluent chaining query wrapper."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Final, cast

import pytest
from bench.core import OPERATIONS
from bench.operations import INPUTS

import turbohtml
from turbohtml import Element, parse
from turbohtml._html import (
    _query_add_class,
    _query_attr,
    _query_children,
    _query_closest,
    _query_has_class,
    _query_parents,
    _query_remove_class,
    _query_siblings,
    _query_text,
    _query_toggle_class,
    _query_unique,
    _select_limited,
)
from turbohtml.build import E
from turbohtml.query import Query, SelectorSyntaxError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from turbohtml import Document


_DOC = "<ul id=list><li class=item>a</li><li class='item on'>b</li><li class=item>c</li></ul><p id=p>after</p>"


def _tags(query: Query) -> list[str]:
    return [element.tag for element in query]


def test_construct_from_html_wraps_the_root() -> None:
    assert _tags(Query("<div></div>")) == ["html"]


def test_construct_from_document() -> None:
    assert _tags(Query(parse("<div></div>"))) == ["html"]


def test_construct_from_element() -> None:
    element = parse(_DOC).select_one("#list")
    assert element is not None
    assert _tags(Query(element)) == ["ul"]


def test_construct_from_iterable_deduplicates() -> None:
    doc = parse(_DOC)
    items = doc.select("li")
    # the same element passed twice collapses to one, in first-seen order
    assert len(Query([*items, items[0]])) == 3


def test_call_is_find() -> None:
    query = Query(_DOC)
    assert _tags(query("li")) == _tags(query.find("li"))


def test_find_collects_across_the_set_in_document_order() -> None:
    assert [e.text for e in Query(_DOC)("li")] == ["a", "b", "c"]


def test_construct_deduplicates_a_node_adopted_during_iteration() -> None:
    node: Final = Element("b")
    destination: Final = Element("main")

    def nodes() -> Iterator[Element]:
        yield node
        destination.append(node)
        yield node

    assert list(Query(nodes())) == [node]


def test_find_across_multiple_nodes_deduplicates() -> None:
    # an outer and an inner div both reach the same <a>, which must appear once
    doc = parse("<div id=outer><div id=inner><a>x</a></div></div>")
    divs = Query(doc.select("div"))
    assert len(divs) == 2
    assert _tags(divs.find("a")) == ["a"]


def test_find_across_reversed_nested_roots_keeps_document_order() -> None:
    doc = parse(
        '<div id="outer"><p id="before"></p><section id="inner"><a id="link"></a></section><p id="after"></p></div>'
    )
    outer = doc.select_one("#outer")
    inner = doc.select_one("#inner")
    assert outer is not None
    assert inner is not None
    assert [element.attrs["id"] for element in Query([inner, outer]).find("*")] == ["before", "inner", "link", "after"]


def test_find_across_documents_keeps_first_document_order() -> None:
    first = parse('<main id="first"><p id="a"></p></main>').select_one("main")
    second = parse('<main id="second"><p id="b"></p></main>').select_one("main")
    assert first is not None
    assert second is not None
    assert [element.attrs["id"] for element in Query([second, first]).find("p")] == ["b", "a"]


def test_find_across_interleaved_documents_groups_each_document() -> None:
    first = parse('<main><p id="a"></p></main><main><p id="c"></p></main>').select("main")
    second = parse('<main><p id="b"></p></main>').select_one("main")
    assert second is not None
    assert [element.attrs["id"] for element in Query([first[0], second, first[1]]).find("p")] == ["a", "c", "b"]


def test_find_across_interleaved_detached_roots_keeps_first_root_order() -> None:
    first = parse('<main><p id="a"></p></main><main><p id="c"></p></main>').select("main")
    second = parse('<main><p id="b"></p></main>').select_one("main")
    assert second is not None
    first[0].extract()
    first[1].extract()
    assert [element.attrs["id"] for element in Query([first[0], second, first[1]]).find("p")] == ["a", "b", "c"]


def test_find_across_detached_roots_keeps_first_root_order() -> None:
    document = parse('<main id="first"><p id="a"></p></main><main id="second"><p id="b"></p></main>')
    first, second = document.select("main")
    first.extract()
    second.extract()
    assert [element.attrs["id"] for element in Query([second, first]).find("p")] == ["b", "a"]


def test_find_empty_query_is_empty() -> None:
    assert len(Query([]).find("*")) == 0


@pytest.mark.parametrize(
    ("selector", "error"),
    [pytest.param("[", SelectorSyntaxError, id="syntax"), pytest.param(cast("str", 1), TypeError, id="type")],
)
def test_find_multiple_roots_rejects_invalid_selector(selector: str, error: type[Exception]) -> None:
    roots = parse("<main></main><main></main>").select("main")
    with pytest.raises(error):
        Query(roots).find(selector)


def test_filter_keeps_matching_elements() -> None:
    assert [e.text for e in Query(_DOC)("li").filter(".on")] == ["b"]


def test_filter_empty_query_is_empty() -> None:
    assert len(Query([]).filter("[")) == 0


@pytest.mark.parametrize(
    ("selector", "error"),
    [pytest.param("[", SelectorSyntaxError, id="syntax"), pytest.param(cast("str", 1), TypeError, id="type")],
)
def test_filter_rejects_invalid_selector(selector: str, error: type[Exception]) -> None:
    with pytest.raises(error):
        Query(_DOC)("li").filter(selector)


def test_eq_selects_one_or_empty() -> None:
    items = Query(_DOC)("li")
    assert items.eq(1)[0].text == "b"
    assert len(items.eq(99)) == 0


def test_parent_deduplicates_and_drops_non_elements() -> None:
    # the three <li> share one <ul> parent
    assert _tags(Query(_DOC)("li").parent()) == ["ul"]
    # the <html> root's parent is the document, not an element, so it is dropped
    assert len(Query("<p></p>").parent()) == 0


def test_children_optionally_filters() -> None:
    assert [e.text for e in Query(_DOC)("#list").children()] == ["a", "b", "c"]
    assert [e.text for e in Query(_DOC)("#list").children(".on")] == ["b"]


def test_siblings_optionally_filters() -> None:
    first = Query(_DOC)("li").eq(0)
    assert [e.text for e in first.siblings()] == ["b", "c"]
    assert [e.text for e in first.siblings(".on")] == ["b"]


def test_closest_finds_or_skips() -> None:
    assert _tags(Query(_DOC)("li").closest("#list")) == ["ul"]
    # no ancestor matches, so the element contributes nothing
    assert len(Query(_DOC)("li").closest("#missing")) == 0


def test_items_yields_single_element_queries() -> None:
    assert [item.attr("class") for item in Query(_DOC)("li").items()] == ["item", "item on", "item"]


def test_attr_get_and_set() -> None:
    query = Query(_DOC)("li")
    assert query.attr("class") == "item"  # the first element, class joined to a string
    assert Query(_DOC)("#p").attr("id") == "p"
    assert query.attr("data-x") is None  # absent attribute
    assert Query([]).attr("id") is None  # empty set
    assert query.attr("data-k", "v") is query  # set returns the query for chaining
    assert all(e.attrs.get("data-k") == "v" for e in query)


def test_text_get_and_set() -> None:
    assert Query(_DOC)("li").text() == "a b c"
    query = Query("<p>old</p>")("p")
    assert query.text("new") is query
    assert query.text() == "new"


def test_html_returns_inner_or_none() -> None:
    assert Query("<div><b>hi</b></div>")("div").html() == "<b>hi</b>"
    assert Query([]).html() is None


def test_class_helpers() -> None:
    query = Query(_DOC)("li")
    assert query.has_class("on") is True
    assert query.has_class("nope") is False
    assert query.eq(0).add_class("new").attr("class") == "item new"
    assert query.eq(0).add_class("item").attr("class") == "item new"  # already present, unchanged
    assert query.eq(1).remove_class("on").attr("class") == "item"
    assert query.eq(2).remove_class("absent").attr("class") == "item"  # absent, unchanged
    toggled = Query("<a class=x></a><a></a>")("a")
    toggled.toggle_class("x")  # removes from the first, adds to the second
    assert [a.attrs.get("class") for a in toggled] == [[], ["x"]]


def test_sequence_protocol() -> None:
    query = Query(_DOC)("li")
    assert len(query) == 3
    assert query[0].text == "a"
    assert [e.text for e in query] == ["a", "b", "c"]


def test_equality_and_hash() -> None:
    doc = parse(_DOC)
    one = Query(doc.select("li"))
    two = Query(doc.select("li"))
    assert one == two
    assert hash(one) == hash(two)
    assert one != Query(doc.select("p"))
    assert one != object()


def test_repr() -> None:
    assert repr(Query(_DOC)("li")) == "Query(['li', 'li', 'li'])"


def test_chaining_example() -> None:
    # the worked pyquery-style chain from the issue, with turbohtml-native names
    href = Query(_DOC)("ul").find("li").filter(".on").eq(0).add_class("hot").attr("class")
    assert href == "item on hot"
    assert isinstance(Query(_DOC), Query)
    # construction from a bare Element round-trips through the wrapper
    element = parse("<span>x</span>").select_one("span")
    assert element is not None
    assert Query(element).text() == "x"
    assert isinstance(element, Element)


@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        pytest.param("main", ["first", "second"], id="shared-ancestors"),
        pytest.param("MAIN", ["first", "second"], id="html-tag-case"),
        pytest.param(":scope", ["a", "c", "b"], id="scope-per-element"),
        pytest.param("aside", [], id="no-matches"),
    ],
)
def test_query_closest_cross_tree_order(selector: str, expected: list[str]) -> None:
    first: Final = Query('<main id="first"><p id="a"></p><p id="b"></p></main>')("p")
    second: Final = Query('<main id="second"><p id="c"></p></main>')("p")
    assert [node.attrs["id"] for node in Query([first[0], second[0], first[1]]).closest(selector)] == expected


def test_query_closest_parent_ownership() -> None:
    selected: Final = Query("<main><p>x</p><p>y</p></main>")("p").closest("main")
    selected.attr("id", "retained")
    assert selected[0].serialize() == '<main id="retained"><p>x</p><p>y</p></main>'


def test_query_closest_mixed_html_xml_tag_case() -> None:
    html: Final = turbohtml.parse('<main id="html"><p/></main>').find("p")
    xml: Final = turbohtml.parse_xml('<Root><main id="xml"><p/></main><MAIN id="upper"><p/></MAIN></Root>')
    assert html is not None
    paragraphs: Final = [html, *xml.find_all("p")]
    assert [node.attrs["id"] for node in Query(paragraphs).closest("main")] == ["html", "xml"]


def test_query_closest_foreign_namespace_tag() -> None:
    svg_title: Final = turbohtml.parse('<svg><title id="svg">x</title></svg>').select_one("title")
    html_title: Final = turbohtml.parse('<title id="html">x</title>').select_one("title")
    assert svg_title is not None
    assert html_title is not None
    assert [node.attrs["id"] for node in Query([svg_title, html_title]).closest("title")] == ["svg", "html"]


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("h1", id="digit"),
        pytest.param("custom-tag", id="hyphen"),
        pytest.param("custom-element-with-a-long-tag-name", id="long"),
    ],
)
def test_query_closest_tag_name(name: str) -> None:
    document: Final = turbohtml.parse(f"<{name}><p/></{name}>")
    parent: Final = document.find(name)
    assert parent is not None
    assert list(Query(document.find_all("p")).closest(name)) == [parent]


@pytest.mark.parametrize("selector", [pytest.param("[", id="unclosed"), pytest.param("", id="empty")])
def test_query_closest_invalid_selector(selector: str) -> None:
    with pytest.raises(SelectorSyntaxError):
        Query("<p>x</p>")("p").closest(selector)


@pytest.mark.parametrize(
    ("indices", "expected"),
    [
        pytest.param([0, 1, 2], ["left", "right"], id="repeated-parent"),
        pytest.param([2, 0, 1], ["right", "left"], id="reverse-groups"),
        pytest.param([0, 2, 1], ["left", "right"], id="interleaved-groups"),
    ],
)
def test_query_parent_encounter_order(indices: list[int], expected: list[str]) -> None:
    children: Final = Query('<main id="left"><p></p><p></p></main><aside id="right"><p></p></aside>')("p")
    assert [node.attrs["id"] for node in Query(children[index] for index in indices).parent()] == expected


def test_query_parent_cross_tree_order() -> None:
    first: Final = Query('<main id="left"><p></p><p></p></main>')("p")
    second: Final = Query('<main id="right"><p></p></main>')("p")
    assert [node.attrs["id"] for node in Query([first[0], second[0], first[1]]).parent()] == ["left", "right"]


def test_query_parent_cross_tree_ownership() -> None:
    first: Final = Query('<main id="left"><p></p><p></p></main>')("p")
    second: Final = Query('<main id="right"><p></p></main>')("p")
    parents: Final = Query([first[0], second[0], first[1]]).parent()
    parents[0].attrs["id"] = "changed"
    assert [node.attrs["id"] for node in first.parent()] == ["changed"]


def test_query_parent_detached_root() -> None:
    assert list(Query([Element("p")]).parent()) == []


@pytest.mark.parametrize(
    ("selected", "expected"),
    [
        pytest.param(["a"], ["b", "c"], id="single-root"),
        pytest.param(["a", "b"], ["b", "c", "a"], id="same-parent"),
        pytest.param(["a", "b", "c"], ["b", "c", "a"], id="complete-parent"),
        pytest.param(["b", "a"], ["a", "c", "b"], id="reverse-roots"),
        pytest.param(["a", "d", "b"], ["b", "c", "e", "a"], id="interleaved-parents"),
    ],
)
def test_query_sibling_encounter_order(selected: list[str], expected: list[str]) -> None:
    document: Final = parse(
        '<main><p id="a"></p><b id="b"></b><i id="c"></i></main><aside><p id="d"></p><b id="e"></b></aside>'
    )
    roots: Final = [document.select_one(f"#{name}") for name in selected]
    assert all(isinstance(node, Element) for node in roots)
    assert [
        node.attrs["id"] for node in Query(node for node in roots if isinstance(node, Element)).siblings()
    ] == expected


def test_query_sibling_cross_tree_order() -> None:
    first: Final = Query('<main><p id="a"></p><p id="b"></p></main>')("p")
    second: Final = Query('<main><p id="c"></p><p id="d"></p></main>')("p")
    assert [node.attrs["id"] for node in Query([first[0], second[0], first[1], second[1]]).siblings()] == [
        "b",
        "d",
        "a",
        "c",
    ]


def test_query_sibling_detached_root() -> None:
    assert list(Query([Element("p")]).siblings()) == []


def test_unique_keeps_the_first_of_each_node() -> None:
    document = turbohtml.parse("<p>a</p><p>b</p>")
    first, second = document.find_all("p")
    assert _query_unique([first, second, first]) == [first, second]


def test_unique_reads_any_iterable() -> None:
    document = turbohtml.parse("<p>a</p><p>b</p>")
    assert len(_query_unique(iter(document.find_all("p")))) == 2


@pytest.mark.parametrize(
    "source",
    [pytest.param(5, id="not-iterable"), pytest.param(["text"], id="not-an-element")],
)
def test_unique_rejects_what_is_not_a_set_of_elements(source: object) -> None:
    with pytest.raises(TypeError):
        _query_unique(source)  # ty: ignore[invalid-argument-type]  # the argument check is the point


def test_unique_propagates_what_the_iterable_raises() -> None:
    def refusing() -> object:
        yield from ()
        msg = "no more elements"
        raise RuntimeError(msg)

    with pytest.raises(RuntimeError, match="no more elements"):
        _query_unique(refusing())  # ty: ignore[invalid-argument-type]  # the iterable raises, which is the point


def test_siblings_skips_the_element_itself() -> None:
    document = turbohtml.parse("<ul><li id=a>a</li><li id=b>b</li>text<li id=c>c</li></ul>")
    first = document.select_one("#a")
    assert first is not None
    assert [node.attrs["id"] for node in _query_siblings([first])] == ["b", "c"]


def test_siblings_of_a_detached_element_is_empty() -> None:
    assert _query_siblings([E.div()]) == []


def test_siblings_of_the_root_is_empty() -> None:
    document = turbohtml.parse("<p>a</p>")
    root = document.root
    assert root is not None
    assert _query_siblings([root]) == []


def test_siblings_deduplicates_across_the_set() -> None:
    document = turbohtml.parse("<ul><li id=a>a</li><li id=b>b</li></ul>")
    both = document.find_all("li")
    assert [node.attrs["id"] for node in _query_siblings(both)] == ["b", "a"]


@pytest.mark.parametrize(
    ("indices", "expected"),
    [
        pytest.param([], [], id="empty"),
        pytest.param([0], [1, 2], id="one"),
        pytest.param([0, 0], [1, 2], id="duplicate"),
        pytest.param([0, 1, 2], [1, 2, 0], id="all"),
        pytest.param([2, 0, 1], [0, 1, 2], id="reverse"),
    ],
)
def test_siblings_preserves_first_encounter_order(indices: list[int], expected: list[int]) -> None:
    children: Final = [Element("b"), Element("i"), Element("em")]
    Element("div", children=children)
    assert list(Query([children[index] for index in indices]).siblings()) == [children[index] for index in expected]


@pytest.mark.parametrize(
    ("call", "args"),
    [
        pytest.param(_query_siblings, ("notalist",), id="siblings"),
        pytest.param(_query_text, ("notalist",), id="text"),
        pytest.param(_query_attr, ("notalist", "id"), id="attr"),
        pytest.param(_query_has_class, ("notalist", "x"), id="has_class"),
        pytest.param(_query_add_class, ("notalist", "x"), id="add_class"),
        pytest.param(_query_remove_class, ("notalist", "x"), id="remove_class"),
        pytest.param(_query_toggle_class, ("notalist", "x"), id="toggle_class"),
    ],
)
def test_a_binding_rejects_a_non_list(call: object, args: tuple[object, ...]) -> None:
    with pytest.raises(TypeError):
        call(*args)  # ty: ignore[call-non-callable]  # the argument check is the point


def test_siblings_rejects_a_non_element() -> None:
    with pytest.raises(TypeError):
        _query_siblings(["text"])  # ty: ignore[invalid-argument-type]  # the element check is the point


def test_text_joins_the_set_with_a_space() -> None:
    document = turbohtml.parse("<p>one</p><p>two</p>")
    assert _query_text(document.find_all("p")) == "one two"


def test_text_of_an_empty_set_is_empty() -> None:
    assert not _query_text([])


def test_attr_of_an_empty_set_is_none() -> None:
    assert _query_attr([], "id") is None


def test_attr_reads_a_plain_value() -> None:
    document = turbohtml.parse("<p id=first>a</p>")
    assert _query_attr(document.find_all("p"), "id") == "first"


def test_attr_joins_a_tokenized_value() -> None:
    document = turbohtml.parse("<p class='a b'>x</p>")
    assert _query_attr(document.find_all("p"), "class") == "a b"


def test_attr_of_an_absent_name_is_none() -> None:
    document = turbohtml.parse("<p>x</p>")
    assert _query_attr(document.find_all("p"), "title") is None


def test_has_class_finds_it_on_any_element() -> None:
    document = turbohtml.parse("<p>a</p><p class='x'>b</p>")
    assert _query_has_class(document.find_all("p"), "x") is True


def test_has_class_answers_false_when_absent() -> None:
    document = turbohtml.parse("<p>a</p><p class='y'>b</p>")
    assert _query_has_class(document.find_all("p"), "x") is False


@pytest.mark.parametrize(
    ("markup", "edit", "expected"),
    [
        pytest.param("<p>x</p>", _query_add_class, ["new"], id="add-to-an-element-with-no-class"),
        pytest.param("<p class='a'>x</p>", _query_add_class, ["a", "new"], id="add-beside-an-existing-class"),
        pytest.param("<p class='new'>x</p>", _query_add_class, ["new"], id="add-what-is-already-there"),
        pytest.param("<p class='a new b'>x</p>", _query_remove_class, ["a", "b"], id="remove-keeps-the-others"),
        pytest.param("<p class='a'>x</p>", _query_remove_class, ["a"], id="remove-what-is-absent"),
        pytest.param("<p class='a'>x</p>", _query_toggle_class, ["a", "new"], id="toggle-on"),
        pytest.param("<p class='a new'>x</p>", _query_toggle_class, ["a"], id="toggle-off"),
    ],
)
def test_a_class_edit(markup: str, edit: object, expected: list[str]) -> None:
    document = turbohtml.parse(markup)
    elements = document.find_all("p")
    edit(elements, "new")  # ty: ignore[call-non-callable]  # the parametrized binding
    kept = document.select_one("p")
    assert kept is not None
    assert kept.attrs.get("class", []) == expected


def test_the_query_facade_still_composes() -> None:
    query = Query("<ul><li class='a'>one</li><li>two</li></ul>")("li")
    assert query.text() == "one two"
    assert query.add_class("z").has_class("z") is True
    assert query.attr("class") == "a z"
    assert query.remove_class("a").attr("class") == "z"


def test_parents_deduplicates_a_shared_parent_and_skips_the_root() -> None:
    document = turbohtml.parse("<div><p>a</p><p>b</p></div>")
    first, second = document.find_all("p")
    html = document.find("html")
    assert html is not None
    assert _query_parents([first, second, html]) == [first.parent]


def test_the_document_has_no_parent() -> None:
    assert _query_parents([turbohtml.parse("<p>a</p>")]) == []


def test_children_keeps_only_element_children_in_order() -> None:
    document = turbohtml.parse("<div>x<p>a</p>y<span>b</span></div><section><i>c</i></section>")
    div, section = document.find("div"), document.find("section")
    assert div is not None
    assert section is not None
    assert [child.tag for child in _query_children([div, section])] == ["p", "span", "i"]


def test_children_of_a_document_is_its_root() -> None:
    document = turbohtml.parse("<p>a</p>")
    assert [child.tag for child in _query_children([document])] == ["html"]


def test_closest_deduplicates_a_shared_ancestor_and_drops_a_miss() -> None:
    document = turbohtml.parse("<div class=box><p>a</p><p>b</p></div><p>c</p>")
    paragraphs = document.find_all("p")
    assert _query_closest(paragraphs, ".box") == [paragraphs[0].parent]


def test_closest_rejects_a_bad_selector() -> None:
    with pytest.raises(Exception, match="selector"):
        _query_closest(turbohtml.parse("<p>a</p>").find_all("p"), "p[")


@pytest.mark.parametrize(
    ("limit", "expected"),
    [
        pytest.param(0, ["a", "b", "c"], id="zero-is-all"),
        pytest.param(-1, ["a", "b", "c"], id="negative-is-all"),
        pytest.param(2, ["a", "b"], id="two"),
        pytest.param(5, ["a", "b", "c"], id="more-than-there-are"),
    ],
)
def test_select_limited_stops_after_the_limit(limit: int, expected: list[str]) -> None:
    document = turbohtml.parse("<p>a</p><div><p>b</p></div><p>c</p>")
    assert [node.text for node in _select_limited(document, "p", limit)] == expected


def test_select_limited_stops_early_on_an_indexed_tree() -> None:
    document = turbohtml.parse("".join(f"<p>{index}</p>" for index in range(2000)))
    assert [node.text for node in _select_limited(document, "p", 1)] == ["0"]


@pytest.mark.parametrize(
    ("entry", "args"),
    [
        pytest.param(_query_parents, (["p"],), id="parents-of-a-non-node"),
        pytest.param(_query_children, (["p"],), id="children-of-a-non-node"),
        pytest.param(_query_closest, (["p"], "p"), id="closest-of-a-non-node"),
        pytest.param(_select_limited, ("p", "p", 0), id="select-from-a-non-node"),
        pytest.param(_query_parents, ("p",), id="parents-of-a-non-list"),
        pytest.param(_query_children, ("p",), id="children-of-a-non-list"),
        pytest.param(_query_closest, ("p", "p"), id="closest-of-a-non-list"),
        pytest.param(_select_limited, (turbohtml.parse(""), 5, 0), id="select-with-a-non-str-selector"),
    ],
)
def test_the_entries_reject_a_non_node(entry: Callable[..., object], args: tuple[object, ...]) -> None:
    with pytest.raises(TypeError):
        entry(*args)


@pytest.mark.parametrize("kind", [pytest.param("css", id="css"), pytest.param("xpath", id="xpath")])
@pytest.mark.parametrize(
    "size", [pytest.param(1, id="single"), pytest.param(2, id="pair"), pytest.param(100, id="wide")]
)
def test_paths_after_sibling_removal(kind: str, size: int) -> None:
    document: Final[Document] = parse("<ul>" + "<li>x</li><span>y</span>" * size + "</ul>")
    nodes: Final[list[Element]] = document.select("li")
    before: Final[list[str]] = [_path(node, kind) for node in nodes]
    document.remove("li:first-of-type")
    assert (before, [_path(node, kind) for node in nodes[1:]]) == (
        [_expected(kind, index, size) for index in range(1, size + 1)],
        [_expected(kind, index, size - 1) for index in range(1, size)],
    )


@pytest.mark.parametrize("kind", [pytest.param("css", id="css"), pytest.param("xpath", id="xpath")])
def test_paths_after_sibling_append(kind: str) -> None:
    document: Final[Document] = parse("<ul><li>first</li></ul>")
    before: Final[str] = _path(document.select("li")[0], kind)
    document.select("ul")[0].append(Element("li"))
    assert (before, [_path(node, kind) for node in document.select("li")]) == (
        _expected(kind, 1, 1),
        [_expected(kind, 1, 2), _expected(kind, 2, 2)],
    )


@pytest.mark.parametrize(
    ("target", "value", "expected"),
    [
        pytest.param(0, "new", "#new", id="replace-anchor"),
        pytest.param(1, "first", "html > body > p:nth-of-type(1)", id="duplicate-anchor"),
        pytest.param(0, None, "html > body > p:nth-of-type(1)", id="delete-anchor"),
    ],
)
def test_css_path_after_id_change(target: int, value: str | None, expected: str) -> None:
    document: Final[Document] = parse('<p id="first">one</p><p id="second">two</p>')
    nodes: Final[list[Element]] = document.select("p")
    before: Final[str] = nodes[0].css_path()
    if value is None:
        del nodes[target].attrs["id"]
    else:
        nodes[target].attrs["id"] = value
    assert (before, nodes[0].css_path()) == ("#first", expected)


@pytest.mark.parametrize("kind", [pytest.param("css", id="css"), pytest.param("xpath", id="xpath")])
def test_path_for_last_sibling_before_any_other_path(kind: str) -> None:
    document: Final[Document] = parse("<ul><li>a</li><span>b</span>text<li>c</li><li>d</li></ul>")
    assert _path(document.select("li")[-1], kind) == _expected(kind, 3, 3)


def test_css_path_after_duplicate_id_removal() -> None:
    document: Final[Document] = parse('<p id="same">one</p><p id="same">two</p>')
    nodes: Final[list[Element]] = document.select("p")
    before: Final[str] = nodes[0].css_path()
    del nodes[1].attrs["id"]
    assert (before, nodes[0].css_path()) == ("html > body > p:nth-of-type(1)", "#same")


def _path(node: Element, kind: str) -> str:
    return node.css_path() if kind == "css" else node.xpath_path()


def _expected(kind: str, index: int, size: int) -> str:
    if kind == "css":
        return "html > body > ul > li" + (f":nth-of-type({index})" if size > 1 else "")
    return "/html/body/ul/li" + (f"[{index}]" if size > 1 else "")


@pytest.mark.parametrize("count", [4, 32, 512], ids=["small", "threshold", "large"])
@pytest.mark.parametrize("order", ["sorted", "reversed", "shuffled"])
@pytest.mark.parametrize("selector", ["i", ":scope > i"], ids=["descendant", "scoped"])
def test_find_root_document_order(count: int, order: str, selector: str) -> None:
    nodes = parse("<main>" + "".join(f'<div><i id="{index}"></i></div>' for index in range(count)) + "</main>").select(
        "div"
    )
    if order == "reversed":
        nodes.reverse()
    elif order == "shuffled":
        nodes = nodes[::2] + nodes[1::2]
    assert [node.attrs["id"] for node in Query(nodes).find(selector)] == [str(index) for index in range(count)]


def test_find_duplicate_roots_preserve_document_order() -> None:
    nodes: Final = parse(
        "<main>" + "".join(f'<div><i id="{index}"></i></div>' for index in range(32)) + "</main>"
    ).select("div")
    assert [node.attrs["id"] for node in Query([*reversed(nodes), *nodes]).find("i")] == [
        str(index) for index in range(32)
    ]


def test_find_root_order_after_mutation() -> None:
    document: Final = parse("<main>" + "".join(f'<div><i id="{index}"></i></div>' for index in range(32)) + "</main>")
    nodes: Final = document.select("div")
    query: Final = Query(list(reversed(nodes)))
    before: Final = [node.attrs["id"] for node in query.find("i")]
    document.select("main")[0].append(nodes[0])
    assert (before, [node.attrs["id"] for node in query.find("i")]) == (
        [str(index) for index in range(32)],
        [str(index) for index in range(1, 32)] + ["0"],
    )


@pytest.mark.parametrize("case_index", [0, 1, 2, 3], ids=["detached", "connected", "small", "documents"])
def test_query_root_groups_shared_inputs(case_index: int) -> None:
    case: Final = cast("tuple[str, int]", INPUTS["query-root-groups"]()[case_index][1])
    operation: Final = cast("Callable[[tuple[str, int]], Query]", OPERATIONS["query-root-groups"][0])
    assert [node.text for node in operation(case)] == [str(index) for index in range(case[1])]


@pytest.mark.parametrize("selector", ["p", "missing"], ids=["matches", "empty"])
def test_query_root_groups_interleave_handles_and_nested_roots(selector: str) -> None:
    left, right = parse(
        "<main><section><p>a</p></section><section><p>b</p></section></main>"
        "<main><section><p>d</p></section><section><p>e</p></section></main>"
    ).select("main")
    left.extract()
    right.extract()
    middle: Final = parse("<main><p>c</p></main>").select("main")[0]
    roots: Final = [left.select("section")[1], middle, right.select("section")[1], left, right]
    assert [node.text for node in Query(roots).find(selector)] == (["a", "b", "c", "d", "e"] if selector == "p" else [])


@pytest.mark.parametrize("empty_group", [0, 1, 2], ids=["first", "middle", "last"])
def test_query_root_groups_preserve_order_around_empty_group(empty_group: int) -> None:
    first, last = parse("<main><p>a</p></main><main><p>c</p></main>").select("main")
    first.extract()
    last.extract()
    middle: Final = parse("<main><p>b</p></main>").select("main")[0]
    roots: Final = [first, middle, last]
    roots[empty_group].select("p")[0].extract()
    assert [node.text for node in Query(roots).find("p")] == [
        value for index, value in enumerate("abc") if index != empty_group
    ]


def test_query_root_groups_refresh_after_reparenting() -> None:
    first, second = parse("<main><p>a</p></main><main><p>b</p></main>").select("main")
    first.extract()
    second.extract()
    query: Final = Query([second, first])
    query.find("p")
    first.append(second)
    assert [node.text for node in query.find("p")] == ["a", "b"]


def test_query_root_groups_refresh_after_cross_document_adoption() -> None:
    first: Final = parse("<main><p>a</p></main>").select("main")[0]
    second: Final = parse("<main><p>b</p></main>").select("main")[0]
    query: Final = Query([first, second])
    query.find("p")
    second.append(first)
    assert [node.text for node in query.find("p")] == ["b", "a"]


@pytest.mark.skipif(
    sys.implementation.name != "cpython", reason="requires immediate CPython reference-count finalization"
)
@pytest.mark.parametrize("adopt", [False, True], ids=["same-tree", "cross-document"])
def test_query_root_groups_follow_selector_eviction_mutation(*, adopt: bool) -> None:
    first, last = parse("<main><p>a</p></main><main><p>c</p></main>").select("main")
    middle: Final = parse("<main><p>b</p></main>").select("main")[0]
    first.extract()
    last.extract()
    destination: Final = middle if adopt else first
    extra: Final = [parse("<main><p>d</p></main>").select("main")[0] for _ in range(31)]

    class MovingSelector(str):  # ruff:ignore[subclass-builtin]  # native selectors require a real str
        __slots__ = ()

        def __del__(self) -> None:
            destination.append(last)

    first.select(MovingSelector("unmatched"))
    for index in range(15):
        first.select(f"unused{index}")
    assert [node.text for node in Query([first, middle, last, *extra]).find("p")] == [
        *(["a", "b", "c"] if adopt else ["a", "c", "b"]),
        *(["d"] * 31),
    ]


def test_query_root_groups_keep_later_documents_after_reordering() -> None:
    first, third = parse("<main><p>a</p></main><main><p>c</p></main>").select("main")
    first.extract()
    third.extract()
    second: Final = parse("<main><p>b</p></main>").select("main")[0]
    fourth: Final = parse("<main><p>d</p></main>").select("main")[0]
    assert [node.text for node in Query([first, second, third, fourth]).find("p")] == list("abcd")


def test_query_root_groups_interleaved_document_members() -> None:
    first: Final = parse("<main><p>a</p></main>" * 32).select("main")
    second: Final = parse("<main><p>b</p></main>" * 32).select("main")
    roots: Final = [node for pair in zip(first, second, strict=True) for node in pair]
    assert [node.text for node in Query(roots).find("p")] == ["a"] * 32 + ["b"] * 32


def test_query_root_groups_accept_selector_subclass() -> None:
    class Selector(str):  # ruff:ignore[subclass-builtin]  # the native API requires a real str
        __slots__ = ()

    roots: Final = [parse(f"<main><p>{index}</p></main>").select("main")[0] for index in range(32)]
    assert [node.text for node in Query(roots).find(Selector("p"))] == [str(index) for index in range(32)]


def test_query_root_groups_many_documents_reject_invalid_selector() -> None:
    roots: Final = [parse("<main><p>x</p></main>").select("main")[0] for _ in range(32)]
    with pytest.raises(SelectorSyntaxError):
        Query(roots).find("[")


@pytest.mark.parametrize(
    ("operation", "index"),
    [
        pytest.param("query-parents", 0, id="shared-parent"),
        pytest.param("query-parents", 1, id="distinct-parents"),
        pytest.param("query-closest", 1, id="distinct-ancestors"),
    ],
)
@pytest.mark.oracle
def test_pyquery_join_benchmark_output(operation: str, index: int) -> None:
    pyquery: Final = pytest.importorskip("bench.competitors.pyquery", exc_type=ImportError)
    case: Final = cast("tuple[int, bool]", INPUTS[operation]()[index][1])
    count, shared = case
    assert [(node.tag, "".join(node.itertext())) for node in pyquery.OPERATIONS[operation][0](case)] == (
        [("main", "x" * count)] if shared else [("main", "x")] * count
    )


@pytest.mark.oracle
def test_pyquery_closest_benchmark_rejects_duplicate_results() -> None:
    pyquery: Final = pytest.importorskip("bench.competitors.pyquery", exc_type=ImportError)
    with pytest.raises(NotImplementedError, match="duplicate ancestors"):
        pyquery.OPERATIONS["query-closest"][0](INPUTS["query-closest"]()[0][1])
