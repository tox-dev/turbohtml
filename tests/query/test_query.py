"""The pyquery-style fluent chaining query wrapper."""

from __future__ import annotations

from typing import cast

import pytest

from turbohtml import Element, parse
from turbohtml.query import Query, SelectorSyntaxError

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
