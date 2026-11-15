from __future__ import annotations

import operator
import sys
from functools import partial
from typing import TYPE_CHECKING, Final, cast

import pytest
from bench.core import OPERATIONS
from bench.operations import INPUTS

import turbohtml
from turbohtml import Element, Node, _html, parse  # ruff:ignore[import-private-name]  # the native entry takes any list
from turbohtml.clean import linkify_node
from turbohtml.query import Query, SelectorSyntaxError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from work_steps import WorkSteps

    from turbohtml import Document


_DOC: Final[str] = (
    "<ul id=list><li class=item>a</li><li class='item on'>b</li><li class=item>c</li></ul><p id=p>after</p>"
)


def test_construct_from_html_wraps_the_root() -> None:
    assert _tags(Query("<div></div>")) == ["html"]


def _tags(query: Query) -> list[str]:
    return [element.tag for element in query]


def test_construct_from_document() -> None:
    assert _tags(Query(parse("<div></div>"))) == ["html"]


def test_construct_from_element() -> None:
    element = parse(_DOC).select_one("#list")
    assert element is not None
    assert _tags(Query(element)) == ["ul"]


def test_construct_from_iterable_deduplicates() -> None:
    items = parse(_DOC).select("li")
    # the same element passed twice collapses to one, in first-seen order
    assert len(Query([*items, items[0]])) == 3


def test_call_is_find() -> None:
    query = Query(_DOC)
    assert _tags(query("li")) == _tags(query.find("li"))


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
    divs = Query(parse("<div id=outer><div id=inner><a>x</a></div></div>").select("div"))
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
    "reorder",
    [
        pytest.param(lambda nodes: nodes, id="document-order"),
        pytest.param(operator.itemgetter(slice(None, None, -1)), id="reversed"),
        pytest.param(lambda nodes: nodes[1::2] + nodes[::2], id="interleaved"),
    ],
)
@pytest.mark.parametrize(
    ("selector", "count"),
    [
        pytest.param("book", 1, id="shared-match"),
        pytest.param("missing", 0, id="no-match"),
        pytest.param("part.mid", 1, id="match-inside-the-chain"),
        pytest.param("part", 100, id="self-match"),
        pytest.param("book part", 100, id="descendant"),
        pytest.param(".mid part, book", 51, id="selector-list"),
        pytest.param(":scope", 100, id="scope-per-element"),
        pytest.param(":not(:scope)", 100, id="scope-excluded"),
        pytest.param("part:not(:scope)", 99, id="scope-excluded-with-type"),
        pytest.param("book:not(:scope)", 1, id="scope-excluded-far-match"),
        pytest.param(":scope > part", 0, id="scope-child"),
        pytest.param(":scope part", 0, id="scope-descendant"),
        pytest.param(".mid :scope", 50, id="scope-below-a-match"),
        pytest.param(":not(:scope) > :scope", 100, id="scope-and-not-scope"),
        pytest.param("part:has(:scope)", 0, id="scope-inside-has"),
        pytest.param("part:has(> :scope)", 0, id="scope-child-inside-has"),
        pytest.param("part:not(:has(:scope)):not(:scope)", 99, id="scope-inside-negated-has"),
        pytest.param(":is(:scope, .mid)", 100, id="scope-in-a-list"),
        pytest.param(":where(.mid, book):not(:scope)", 2, id="scope-excluded-from-a-list"),
        pytest.param(":nth-child(1 of :scope)", 100, id="scope-in-nth-of"),
        pytest.param(":not(:is(:scope, part))", 1, id="scope-nested-negation"),
    ],
)
def test_query_closest_deeply_nested_answers_as_each_element(
    selector: str, count: int, reorder: Callable[[list[Element]], list[Element]]
) -> None:
    # a climb past 32 ancestors starts to share its answer with the climbs after it
    document: Final = turbohtml.parse_xml(
        f"<book>{'<part>' * 49}<part class='mid'>{'<part>' * 50}{'</part>' * 100}</book>"
    )
    sections: Final = reorder(document.select("part"))
    nearest: Final = [section.closest(selector) for section in sections]
    found: Final = list(Query(sections).closest(selector))
    assert found == list(dict.fromkeys(node for node in nearest if node is not None))
    assert len(found) == count


def test_query_closest_deep_chain_shared_by_siblings() -> None:
    document: Final = turbohtml.parse_xml(f"<book>{'<a>' * 80}<part/><part/><part/>{'</a>' * 80}</book>")
    assert list(Query(document.select("part")).closest("book")) == document.select("book")
    assert len(Query(document.select("part")).closest("missing")) == 0


@pytest.mark.parametrize(
    ("selector", "count"),
    [
        pytest.param(":scope", 61, id="scope"),
        pytest.param(":not(:scope)", 61, id="scope-excluded"),
        pytest.param(":scope > part", 1, id="scope-child"),
        pytest.param("part:not(:scope)", 60, id="scope-excluded-with-type"),
        pytest.param("part:has(:scope)", 0, id="scope-inside-has"),
        pytest.param("part", 60, id="no-scope"),
    ],
)
def test_query_closest_scope_from_nodes_that_are_not_elements(selector: str, count: int) -> None:
    # above a node that is not an element :scope stands for the document element, unlike above an element
    document: Final = turbohtml.parse_xml(f"<book>{'<part>t' * 60}{'</part>' * 60}</book>")
    sections: Final = document.select("part")
    nodes: Final = [node for section in sections for node in (section.children[0], section)][::-1]
    nearest: Final = [node.closest(selector) for node in nodes]
    found: Final = list(Query(cast("list[Element]", nodes)).closest(selector))
    assert found == list(dict.fromkeys(node for node in nearest if node is not None))
    assert len(found) == count


def _tree(name: str, depth: int) -> list[Element]:
    return turbohtml.parse_xml(f"<book id='{name}'>{'<part>' * depth}{'</part>' * depth}</book>").select("part")


def test_query_closest_deep_trees_interleaved() -> None:
    mixed: Final = [section for pair in zip(_tree("first", 40), _tree("second", 40), strict=True) for section in pair]
    assert [node.attrs["id"] for node in Query(mixed).closest("book")] == ["first", "second"]


@pytest.mark.parametrize(
    ("pick", "selector"),
    [
        pytest.param(lambda a, b, c: [a[0], b[0], a[1], c[0], b[1], a[0], c[1]], "book", id="recurring-trees"),
        pytest.param(lambda a, b, c: [b[0], a[0], b[1], a[1], c[0]], "book", id="later-tree-first"),
        pytest.param(lambda a, b, c: [a[5], b[5], a[2], c[1], b[0], a[7]], "part", id="self-matches"),
        pytest.param(lambda a, b, c: [a[9], b[9], a[0], c[3], a[9], b[2]], "part:not(:scope)", id="scope"),
        pytest.param(lambda a, b, c: [c[1], a[0], c[0], b[0], a[1]], "missing", id="no-match"),
        pytest.param(lambda a, b, c: [a[0], a[1], b[0], b[1], c[0]], ":scope", id="runs-in-order"),
    ],
)
def test_query_closest_mixed_trees_keep_the_order_the_set_reaches_them(
    pick: Callable[[list[Element], list[Element], list[Element]], list[Element]], selector: str
) -> None:
    nodes: Final = pick(_tree("a", 40), _tree("b", 10), _tree("c", 40))
    nearest: Final = [node.closest(selector) for node in nodes]
    assert list(Query(nodes).closest(selector)) == list(dict.fromkeys(node for node in nearest if node is not None))


@pytest.mark.skipif(
    sys.implementation.name != "cpython", reason="requires immediate CPython reference-count finalization"
)
@pytest.mark.parametrize(
    ("moved", "expected"),
    [
        pytest.param(1, ["first", "third", "second"], id="first-of-its-tree"),
        pytest.param(3, ["first", "second", "third"], id="later-in-its-tree"),
    ],
)
@pytest.mark.parametrize("selector", [pytest.param("book", id="plain"), pytest.param("book:not(:scope)", id="scope")])
def test_query_closest_takes_a_node_that_moved_trees_during_the_call(
    moved: int, expected: list[str], selector: str
) -> None:
    first: Final = _tree("first", 40)
    second: Final = turbohtml.parse_xml("<book id='second'><part/><part/></book>").select("part")
    destination: Final = turbohtml.parse_xml("<book id='third'/>").select_one("book")
    assert destination is not None
    nodes: Final = [first[0], second[0], first[1], second[1]]

    class MovingSelector(str):  # ruff:ignore[subclass-builtin]  # native selectors require a real str
        __slots__ = ()

        def __del__(self) -> None:
            destination.append(nodes[moved])

    # the selector cache of the second tree evicts this one when the call compiles its selector there
    second[0].select(MovingSelector("unmatched"))
    for index in range(15):
        second[0].select(f"unused{index}")
    assert [node.attrs["id"] for node in Query(nodes).closest(selector)] == expected


@pytest.mark.parametrize(
    ("selector", "count"),
    [
        pytest.param("lead ~ part", 40, id="left-match"),
        pytest.param("x ~ part", 0, id="no-left-match"),
        pytest.param("lead ~ part:not(:scope)", 0, id="scope-excluded"),
        pytest.param("lead ~ :scope", 40, id="scope-per-element"),
    ],
)
def test_query_closest_sibling_combinator_over_a_long_run(selector: str, count: int) -> None:
    # the elements of one tree share a sibling memo, which a walk past 32 siblings reads
    sections: Final = turbohtml.parse_xml(f"<book><lead/>{'<part/>' * 40}</book>").select("part")
    nearest: Final = [section.closest(selector) for section in sections]
    found: Final = list(Query(sections).closest(selector))
    assert found == [node for node in nearest if node is not None]
    assert len(found) == count


@pytest.mark.parametrize("selector", [pytest.param(".hit *", id="plain"), pytest.param(".hit :scope", id="scoped")])
def test_query_closest_under_long_class_grows_linearly(selector: str, work_steps: WorkSteps) -> None:
    # every climb asks the class value of the shared parent
    work_steps.assert_linear(
        lambda n: partial(Query(parse(f'<ol class="{"w " * n}">{"<li>" * n}'))("li").closest, selector), (250, 1000)
    )
    found: Final = Query(parse(f'<ol class="{"w " * 200}hit"><li><li></ol>'))("li").closest(selector)
    assert [node.tag for node in found] == ["li", "li"]


def test_query_closest_deeply_nested_grows_linearly(work_steps: WorkSteps) -> None:
    # Counted steps rather than seconds: a wall-clock budget is flaky on shared runners.
    work_steps.assert_linear(
        lambda n: partial(
            Query(turbohtml.parse_xml(f"<book>{'<part>' * n}{'</part>' * n}</book>"))("part").closest, "book"
        ),
        (500, 2000),
    )


@pytest.mark.parametrize(
    ("build", "selector"),
    [
        pytest.param(lambda n: _tree("book", n), "book:not(:scope)", id="scope-far-match"),
        pytest.param(lambda n: _tree("book", n), ":scope > missing", id="scope-no-match"),
        pytest.param(
            lambda n: [node for pair in zip(_tree("a", n // 2), _tree("b", n // 2), strict=True) for node in pair],
            "book",
            id="alternating-trees",
        ),
    ],
)
def test_query_closest_shapes_grow_linearly(
    work_steps: WorkSteps, build: Callable[[int], list[Element]], selector: str
) -> None:
    work_steps.assert_linear(lambda n: partial(Query(build(n)).closest, selector), (500, 2000))


def test_query_closest_of_an_empty_set() -> None:
    assert _html.query_closest([], "p") == []


def test_query_closest_keeps_an_answer_seen_again_after_another() -> None:
    document: Final = turbohtml.parse('<main id="a"><p>x</p><p>y</p></main><main id="b"><p>z</p></main>')
    first, second, third = document.find_all("p")
    assert [node.attrs["id"] for node in Query([first, third, second]).closest("main")] == ["a", "b"]


def test_query_closest_climbs_from_a_node_that_is_not_an_element() -> None:
    document: Final = parse("<div class=box><p>a<!--b--></p></div>")
    paragraph = document.find("p")
    assert paragraph is not None
    assert list(Query(cast("list[Element]", [paragraph, *paragraph.children])).closest("p")) == [paragraph]


@pytest.mark.parametrize(
    "nodes",
    [
        pytest.param(["p"], id="first-not-a-node"),
        pytest.param([parse("<p>a</p>").find("p"), "p"], id="later-not-a-node"),
        pytest.param([parse("<p>a</p>").find("p"), parse("<p>b</p>").find("p"), "p"], id="not-a-node-after-trees"),
    ],
)
@pytest.mark.parametrize("selector", [pytest.param("p", id="plain"), pytest.param("p:not(:scope)", id="scope")])
def test_query_closest_entry_rejects_a_non_node(nodes: list[Element | str | None], selector: str) -> None:
    with pytest.raises(TypeError, match="turbohtml element"):
        _html.query_closest(cast("list[Node]", nodes), selector)


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


def test_the_query_facade_still_composes() -> None:
    query = Query("<ul><li class='a'>one</li><li>two</li></ul>")("li")
    assert query.text() == "one two"
    assert query.add_class("z").has_class("z") is True
    assert query.attr("class") == "a z"
    assert query.remove_class("a").attr("class") == "z"


@pytest.mark.parametrize("kind", [pytest.param("css", id="css"), pytest.param("xpath", id="xpath")])
@pytest.mark.parametrize(
    "size", [pytest.param(1, id="single"), pytest.param(2, id="pair"), pytest.param(100, id="wide")]
)
def test_paths_after_sibling_removal(kind: str, size: int) -> None:
    document: Final[Document] = parse(f"<ul>{'<li>x</li><span>y</span>' * size}</ul>")
    nodes: Final[list[Element]] = document.select("li")
    before: Final[list[str]] = [_path(node, kind) for node in nodes]
    document.remove("li:first-of-type")
    assert (before, [_path(node, kind) for node in nodes[1:]]) == (
        [_expected(kind, index, size) for index in range(1, size + 1)],
        [_expected(kind, index, size - 1) for index in range(1, size)],
    )


def _path(node: Element, kind: str) -> str:
    return node.css_path() if kind == "css" else node.xpath_path()


def _expected(kind: str, index: int, size: int) -> str:
    if kind == "css":
        return f"html > body > ul > li{f':nth-of-type({index})' if size > 1 else ''}"
    return f"/html/body/ul/li{f'[{index}]' if size > 1 else ''}"


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
    ],
)
def test_css_path_after_id_change(target: int, value: str, expected: str) -> None:
    document: Final[Document] = parse('<p id="first">one</p><p id="second">two</p>')
    nodes: Final[list[Element]] = document.select("p")
    before: Final[str] = nodes[0].css_path()
    nodes[target].attrs["id"] = value
    assert (before, nodes[0].css_path()) == ("#first", expected)


def test_css_path_after_id_deletion() -> None:
    document: Final[Document] = parse('<p id="first">one</p><p id="second">two</p>')
    nodes: Final[list[Element]] = document.select("p")
    before: Final[str] = nodes[0].css_path()
    del nodes[0].attrs["id"]
    assert (before, nodes[0].css_path()) == ("#first", "html > body > p:nth-of-type(1)")


def test_css_path_of_detached_element_ignores_the_same_id_in_the_document() -> None:
    document: Final[Document] = parse('<i id="same"></i><b>text</b>')
    element: Final = document.select("b")[0].extract()
    element.attrs["id"] = "same"
    assert element.css_path() == "b"


@pytest.mark.parametrize("kind", [pytest.param("css", id="css"), pytest.param("xpath", id="xpath")])
def test_path_for_last_sibling_before_any_other_path(kind: str) -> None:
    document: Final[Document] = parse("<ul><li>a</li><span>b</span>text<li>c</li><li>d</li></ul>")
    assert _path(document.select("li")[-1], kind) == _expected(kind, 3, 3)


@pytest.mark.parametrize(
    ("kind", "prefix", "steps"),
    [
        pytest.param(
            "css",
            "html > body > ul > ",
            [
                "x-a:nth-of-type(1)",
                "li:nth-of-type(1)",
                "x-b",
                "x-a:nth-of-type(2)",
                "li:nth-of-type(2)",
                "x-c",
                "li:nth-of-type(3)",
            ],
            id="css",
        ),
        pytest.param(
            "xpath",
            "/html/body/ul/",
            ["x-a[1]", "li[1]", "x-b", "x-a[2]", "li[2]", "x-c", "li[3]"],
            id="xpath",
        ),
    ],
)
@pytest.mark.parametrize("order", ["sorted", "reversed"])
def test_paths_among_siblings_of_other_types(kind: str, prefix: str, steps: list[str], order: str) -> None:
    document: Final[Document] = parse(
        "<ul><x-a>1</x-a><li>2</li>text<x-b>3</x-b><x-a>4</x-a><li>5</li><x-c>6</x-c><li>7</li></ul>"
    )
    nodes: Final[list[Element]] = document.select("ul > *")
    paths: Final[dict[Element, str]] = {node: _path(node, kind) for node in _reorder(nodes, order)}
    assert [paths[node] for node in nodes] == [prefix + step for step in steps]


def _reorder(nodes: list[Element], order: str) -> list[Element]:
    if order == "reversed":
        return nodes[::-1]
    return nodes[::2] + nodes[1::2] if order == "shuffled" else nodes


@pytest.mark.parametrize(
    ("kind", "prefix"),
    [pytest.param("css", "html > body > ", id="css"), pytest.param("xpath", "/html/body/", id="xpath")],
)
def test_paths_for_siblings_with_distinct_tag_names(kind: str, prefix: str) -> None:
    names: Final[list[str]] = [f"x-{index}" for index in range(300)]
    document: Final[Document] = parse("".join(f"<{name}></{name}>" for name in names))
    assert [_path(node, kind) for node in document.select("body > *")] == [prefix + name for name in names]


@pytest.mark.parametrize("kind", [pytest.param("css", id="css"), pytest.param("xpath", id="xpath")])
def test_paths_for_distinct_siblings_stay_linear(kind: str, work_steps: WorkSteps) -> None:
    work_steps.assert_linear(lambda count: partial(_path_each_sibling, count, kind), (500, 2000))


def _path_each_sibling(count: int, kind: str) -> None:
    document: Final[Document] = parse("".join(f"<x-{index}></x-{index}>" for index in range(count)))
    for node in document.select("body > *"):
        _path(node, kind)


def test_css_path_after_duplicate_id_removal() -> None:
    document: Final[Document] = parse('<p id="same">one</p><p id="same">two</p>')
    nodes: Final[list[Element]] = document.select("p")
    before: Final[str] = nodes[0].css_path()
    del nodes[1].attrs["id"]
    assert (before, nodes[0].css_path()) == ("html > body > p:nth-of-type(1)", "#same")


def test_css_path_of_detached_element_with_id_absent_from_document() -> None:
    element: Final = parse("<p>one</p>").select("p")[0]
    element.extract()
    element.attrs["id"] = "gone"
    assert element.css_path() == "p"


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        pytest.param("<p>one</p>", ["div", "div > p"], id="plain-child"),
        pytest.param('<p id="one">one</p>', ["div", "#one"], id="own-id-child"),
        pytest.param('<p id="top">one</p>', ["div", "#top"], id="child-repeats-root-id"),
        pytest.param('<p id="top">one</p><i id="only">two</i>', ["div", "#top", "#only"], id="mixed-ids"),
    ],
)
def test_css_path_of_detached_fragment_root_ignores_its_own_id(markup: str, expected: list[str]) -> None:
    root: Final = turbohtml.parse_fragment(markup)
    root.extract()
    root.attrs["id"] = "top"
    assert [node.css_path() for node in (root, *root.select("p, i"))] == expected


def test_css_path_of_detached_fragment_root_with_id_costs_no_more_than_without(work_steps: WorkSteps) -> None:
    work_steps.assert_within(_detached_fragment_root("top").css_path, _detached_fragment_root(None).css_path, factor=2)


def _detached_fragment_root(root_id: str | None) -> Element:
    root: Final = turbohtml.parse_fragment("".join(f'<p id="p{index}">{index}</p>' for index in range(1000)))
    root.extract()
    if root_id is not None:
        root.attrs["id"] = root_id
    return root


def test_css_path_of_constructed_root_with_id_ignores_its_own_id() -> None:
    root: Final = Element("div")
    root.attrs["id"] = "top"
    child: Final = Element("p")
    root.append(child)
    assert [root.css_path(), child.css_path()] == ["div", "div > p"]


def test_css_path_of_subtree_extracted_from_detached_fragment_root_with_id() -> None:
    root: Final = turbohtml.parse_fragment('<section><p id="one">one</p></section>')
    root.extract()
    root.attrs["id"] = "top"
    section: Final = root.select("section")[0]
    section.extract()
    assert [section.css_path(), section.select("p")[0].css_path()] == ["section", "section > p"]


def test_css_path_of_fragment_root_detached_by_replace_with_ignores_its_own_id() -> None:
    root: Final = turbohtml.parse_fragment("<p>one</p>")
    root.attrs["id"] = "top"
    root.replace_with(Element("span"))
    assert [root.css_path(), root.select("p")[0].css_path()] == ["div", "div > p"]


@pytest.mark.parametrize(
    "count", [pytest.param(4, id="small"), pytest.param(32, id="threshold"), pytest.param(512, id="large")]
)
@pytest.mark.parametrize(
    "order",
    [
        pytest.param(lambda nodes: nodes, id="sorted"),
        pytest.param(lambda nodes: list(reversed(nodes)), id="reversed"),
        pytest.param(lambda nodes: nodes[::2] + nodes[1::2], id="shuffled"),
    ],
)
@pytest.mark.parametrize("selector", [pytest.param("i", id="descendant"), pytest.param(":scope > i", id="scoped")])
def test_find_root_document_order(count: int, order: Callable[[list[Element]], list[Element]], selector: str) -> None:
    nodes = order(
        parse(f"""<main>{"".join(f'<div><i id="{index}"></i></div>' for index in range(count))}</main>""").select("div")
    )
    assert [node.attrs["id"] for node in Query(nodes).find(selector)] == [str(index) for index in range(count)]


def test_find_duplicate_roots_preserve_document_order() -> None:
    nodes: Final = parse(
        f"""<main>{"".join(f'<div><i id="{index}"></i></div>' for index in range(32))}</main>"""
    ).select("div")
    assert [node.attrs["id"] for node in Query([*reversed(nodes), *nodes]).find("i")] == [
        str(index) for index in range(32)
    ]


def test_find_root_order_after_mutation() -> None:
    document: Final = parse(f"""<main>{"".join(f'<div><i id="{index}"></i></div>' for index in range(32))}</main>""")
    nodes: Final = document.select("div")
    query: Final = Query(list(reversed(nodes)))
    before: Final = [node.attrs["id"] for node in query.find("i")]
    document.select("main")[0].append(nodes[0])
    assert (before, [node.attrs["id"] for node in query.find("i")]) == (
        [str(index) for index in range(32)],
        [str(index) for index in range(1, 32)] + ["0"],
    )


@pytest.mark.parametrize(
    "count", [pytest.param(4, id="small"), pytest.param(40, id="large"), pytest.param(400, id="deep")]
)
@pytest.mark.parametrize("order", ["sorted", "reversed", "shuffled"])
def test_find_nested_list_items_reports_each_match_once(count: int, order: str) -> None:
    nodes: Final = turbohtml.parse_xml(f"<ul>{'<li><ol>' * count}<b></b>{'</ol></li>' * count}</ul>").select("li")
    assert _tags(Query(_reorder(nodes, order)).find("li, b")) == ["li"] * (count - 1) + ["b"]


@pytest.mark.parametrize("order", ["sorted", "reversed", "shuffled"])
def test_find_roots_covered_between_uncovered_ones_below_a_deep_chain(order: str) -> None:
    # each outer <s> covers the <s> inside it and nothing after it, and all of them sit too deep to climb from each
    branches: Final = "".join(f'<s><t id="{index}a"/><s><t id="{index}b"/></s></s>' for index in range(40))
    document: Final = turbohtml.parse_xml(f"<r>{'<x>' * 80}{branches}{'</x>' * 80}<s><t id='last'/></s></r>")
    found: Final = Query(_reorder(document.select("s"), order)).find("t")
    assert [node.attrs["id"] for node in found] == [f"{index}{half}" for index in range(40) for half in "ab"] + ["last"]


@pytest.mark.parametrize(
    ("order", "first", "second"),
    [("sorted", "left", "right"), ("reversed", "right", "left"), ("shuffled", "left", "right")],
)
def test_find_groups_deep_roots_by_the_tree_they_are_in(order: str, first: str, second: str) -> None:
    chain: Final = "<x>" * 80 + "<s><t/></s>" * 40 + "</x>" * 80
    left, right = turbohtml.parse_xml(f"<r><a>{chain}</a><b>{chain}</b></r>").select("r > *")
    right.extract()
    for name, tree in (("left", left), ("right", right)):
        for index, node in enumerate(tree.select("t")):
            node.attrs["id"] = f"{name}{index}"
    roots: Final = [node for pair in zip(left.select("s"), right.select("s"), strict=True) for node in pair]
    assert [node.attrs["id"] for node in Query(_reorder(roots, order)).find("t")] == [
        f"{name}{index}" for name in (first, second) for index in range(40)
    ]


@pytest.mark.parametrize("order", ["sorted", "reversed", "shuffled"])
def test_find_roots_under_parents_with_text_and_comments_keeps_order(order: str) -> None:
    items: Final = "".join(f'<li>lead<!--c--><b></b><a><code id="{index}"></code></a></li>' for index in range(40))
    nodes: Final = parse(f"<ul>{items}</ul>").select("a")
    assert [node.attrs["id"] for node in Query(_reorder(nodes, order)).find("code")] == [
        str(index) for index in range(40)
    ]


@pytest.mark.parametrize(
    ("markup", "roots", "target"),
    [
        pytest.param(lambda n: f"<ul>{'<li><ol>' * n}<b></b>{'</ol></li>' * n}</ul>", "li", "b", id="nested-items"),
        pytest.param(
            lambda n: f"<main>{'<section>' * n}{'<article><p></p></article>' * n}{'</section>' * n}</main>",
            "article",
            "p",
            id="siblings-below-a-chain",
        ),
        pytest.param(
            lambda n: f"<table><tbody>{'<tr><td><i></i></td></tr>' * n}</tbody></table>",
            "td",
            "i",
            id="cells-of-one-body",
        ),
    ],
)
def test_find_many_roots_grow_linearly(
    markup: Callable[[int], str], roots: str, target: str, work_steps: WorkSteps
) -> None:
    # Counted steps rather than seconds: a wall-clock budget is flaky on shared runners.
    work_steps.assert_linear(lambda n: partial(Query(turbohtml.parse_xml(markup(n)))(roots).find, target), (500, 2000))


@pytest.mark.parametrize(
    "case_index",
    [
        pytest.param(0, id="detached"),
        pytest.param(1, id="connected"),
        pytest.param(2, id="small"),
        pytest.param(3, id="documents"),
    ],
)
def test_query_root_groups_shared_inputs(case_index: int) -> None:
    case: Final = cast("tuple[str, int]", INPUTS["query-root-groups"]()[case_index][1])
    operation: Final = cast("Callable[[tuple[str, int]], Query]", OPERATIONS["query-root-groups"][0])
    assert [node.text for node in operation(case)] == [str(index) for index in range(case[1])]


@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        pytest.param("p", ["a", "b", "c", "d", "e"], id="matches"),
        pytest.param("missing", [], id="empty"),
    ],
)
def test_query_root_groups_interleave_handles_and_nested_roots(selector: str, expected: list[str]) -> None:
    left, right = parse(
        "<main><section><p>a</p></section><section><p>b</p></section></main>"
        "<main><section><p>d</p></section><section><p>e</p></section></main>"
    ).select("main")
    left.extract()
    right.extract()
    middle: Final = parse("<main><p>c</p></main>").select("main")[0]
    roots: Final = [left.select("section")[1], middle, right.select("section")[1], left, right]
    assert [node.text for node in Query(roots).find(selector)] == expected


@pytest.mark.parametrize(
    "empty_group", [pytest.param(0, id="first"), pytest.param(1, id="middle"), pytest.param(2, id="last")]
)
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
@pytest.mark.parametrize(
    ("destination_index", "expected"),
    [
        pytest.param(0, ["a", "c", "b"], id="same-tree"),
        pytest.param(1, ["a", "b", "c"], id="cross-document"),
    ],
)
def test_query_root_groups_follow_selector_eviction_mutation(destination_index: int, expected: list[str]) -> None:
    first, last = parse("<main><p>a</p></main><main><p>c</p></main>").select("main")
    middle: Final = parse("<main><p>b</p></main>").select("main")[0]
    first.extract()
    last.extract()
    destination: Final = (first, middle)[destination_index]
    extra: Final = [parse("<main><p>d</p></main>").select("main")[0] for _ in range(31)]

    class MovingSelector(str):  # ruff:ignore[subclass-builtin]  # native selectors require a real str
        __slots__ = ()

        def __del__(self) -> None:
            destination.append(last)

    first.select(MovingSelector("unmatched"))
    for index in range(15):
        first.select(f"unused{index}")
    assert [node.text for node in Query([first, middle, last, *extra]).find("p")] == [*expected, *(["d"] * 31)]


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


@pytest.mark.oracle
def test_pyquery_join_shared_benchmark_output() -> None:
    pyquery: Final = pytest.importorskip("bench.competitors.pyquery", exc_type=ImportError)
    case: Final = cast("tuple[int, bool]", INPUTS["query-parents"]()[0][1])
    count, _ = case
    assert [(node.tag, "".join(node.itertext())) for node in pyquery.OPERATIONS["query-parents"][0](case)] == [
        ("main", "x" * count)
    ]


@pytest.mark.parametrize(
    "operation",
    [pytest.param("query-parents", id="distinct-parents"), pytest.param("query-closest", id="distinct-ancestors")],
)
@pytest.mark.oracle
def test_pyquery_join_distinct_benchmark_output(operation: str) -> None:
    pyquery: Final = pytest.importorskip("bench.competitors.pyquery", exc_type=ImportError)
    case: Final = cast("tuple[int, bool]", INPUTS[operation]()[1][1])
    count, _ = case
    assert [(node.tag, "".join(node.itertext())) for node in pyquery.OPERATIONS[operation][0](case)] == [
        ("main", "x")
    ] * count


@pytest.mark.oracle
def test_pyquery_closest_benchmark_rejects_duplicate_results() -> None:
    pyquery: Final = pytest.importorskip("bench.competitors.pyquery", exc_type=ImportError)
    with pytest.raises(NotImplementedError, match="duplicate ancestors"):
        pyquery.OPERATIONS["query-closest"][0](INPUTS["query-closest"]()[0][1])


def test_paths_and_index_follow_linkify_node() -> None:
    document: Final[Document] = parse("<body><div><a href=q>k</a> http://e.com</div>")
    assert [element.css_path() for element in document.find_all("a")] == ["html > body > div > a"]
    linkify_node(document.select("div")[0])
    first, second = document.find_all("a")  # the index lists the anchor linkify inserted
    assert [first.css_path(), second.css_path()] == [
        "html > body > div > a:nth-of-type(1)",
        "html > body > div > a:nth-of-type(2)",
    ]
    assert [first.xpath_path(), second.xpath_path()] == ["/html/body/div/a[1]", "/html/body/div/a[2]"]
    assert document.select(second.css_path()) == [second]
