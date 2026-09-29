from __future__ import annotations

import gc
import re
import sys
import threading
from typing import TYPE_CHECKING, Final, cast

import pytest
from typing_extensions import assert_type

import turbohtml
from turbohtml import Document, Element, XPath, XPathString, parse, parse_xml
from turbohtml.query import Query, select

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator
    from types import SimpleNamespace


_DOC = '<section><p class="lead">a</p><span>s</span><p class="tail">b</p><div><p>nested</p></div></section>'


def test_find_all_subtree_origin_walks_off_index() -> None:
    # the index covers the whole document, so a query rooted at a subtree element
    # cannot use it and falls back to the pre-order walk over that subtree
    section = parse(_DOC).find("section")
    assert section is not None
    assert len(section.find_all("p")) == 3  # both direct and the nested <p>


def test_find_all_subtree_origin_skips_other_tags() -> None:
    # the walk must skip the <span> and <div> whose atoms differ from <p>
    section = parse(_DOC).find("section")
    assert section is not None
    assert [element.text for element in section.find_all("p")] == ["a", "b", "nested"]


@pytest.mark.parametrize(
    ("limit", "count"),
    [
        pytest.param(1, 1, id="limit-below-matches"),
        pytest.param(2, 2, id="limit-at-some"),
        pytest.param(None, 3, id="no-limit"),
    ],
)
def test_find_all_subtree_origin_honours_limit(limit: int | None, count: int) -> None:
    section = parse(_DOC).find("section")
    assert section is not None
    assert len(section.find_all("p", limit=limit)) == count


def test_select_one_unknown_subject_walks() -> None:
    # a class-only selector has no type subject, so select_one cannot index it and
    # walks pre-order, skipping non-element nodes until the first match
    assert (match := parse(_DOC).select_one(".tail")) is not None
    assert match.text == "b"


def test_select_one_unknown_subject_no_match() -> None:
    # the walk exhausts the tree without a match and reports None
    assert parse(_DOC).select_one(".absent") is None


def test_select_one_subtree_origin_walks_off_index() -> None:
    # subject is known (<p>) but the origin is a subtree, so the indexed bucket is
    # unusable and the typed walk runs instead
    section = parse(_DOC).find("section")
    assert section is not None
    assert (match := section.select_one("p")) is not None
    assert match.text == "a"


def test_cached_selector_reused_across_calls() -> None:
    # repeating the same selector on one tree reuses the cached compile; the result
    # must be identical to the first, uncached call
    doc = parse(_DOC)
    first = [element.text for element in doc.select("p")]
    second = [element.text for element in doc.select("p")]
    assert first == second == ["a", "b", "nested"]


def test_cache_invalidates_on_new_attribute_name() -> None:
    # [data-x] compiled while no element carries data-x resolves it as an absent
    # attribute; setting data-x interns the name and bumps the tree's attribute
    # generation, so the next select must recompile and now match the element
    doc = parse("<div><a>x</a></div>")
    assert doc.select("[data-x]") == []
    anchor = doc.find("a")
    assert anchor is not None
    anchor.attrs["data-x"] = "v"
    assert [element.tag for element in doc.select("[data-x]")] == ["a"]


def test_cache_eviction_keeps_results_correct() -> None:
    # more distinct selectors than the cache holds evicts the least-recently-used
    # entry; every selector (including a re-query of the first, now-evicted one)
    # must still return the right element
    doc = parse("".join(f'<p class="c{index}">t{index}</p>' for index in range(20)))
    for index in range(20):
        assert [element.text for element in doc.select(f".c{index}")] == [f"t{index}"]
    assert [element.text for element in doc.select(".c0")] == ["t0"]


def test_cache_hit_on_equal_distinct_string() -> None:
    # the cache matches by identity first, then by string value, so a second call
    # with an equal but distinct selector object reuses the first compile
    doc = parse(_DOC)
    selector = "div p"
    assert [element.text for element in doc.select(selector)] == ["nested"]
    descendant = "p"
    rebuilt = f"div {descendant}"  # an f-string is built at runtime: equal content, a distinct object
    assert rebuilt is not selector
    assert [element.text for element in doc.select(rebuilt)] == ["nested"]


@pytest.mark.parametrize(
    ("selector", "texts"),
    [
        pytest.param("h2, p", ["h", "a", "b", "nested"], id="alternatives-differ-walk"),
        pytest.param("p.lead, p.tail", ["a", "b"], id="alternatives-share-subject-index"),
    ],
)
def test_select_selector_list_subject(selector: str, texts: list[str]) -> None:
    # a selector list shares the indexed bucket only when every alternative has the
    # same type subject; a list with differing subjects falls back to the walk
    doc = parse("<h2>h</h2>" + _DOC)
    assert [element.text for element in doc.select(selector)] == texts


def test_select_one_subtree_compound_uses_full_matcher() -> None:
    # a descendant combinator is not a single simple selector, so the subtree walk
    # routes through the full matcher rather than the simple fast path
    section = parse(_DOC).find("section")
    assert section is not None
    assert (match := section.select_one("div p")) is not None
    assert match.text == "nested"


def test_find_single_subtree_origin_walks_off_index() -> None:
    # find() for one element rooted at a subtree cannot use the whole-tree index
    section = parse(_DOC).find("section")
    assert section is not None
    assert (first := section.find("p")) is not None
    assert first.text == "a"


def test_select_one_indexed_skips_non_matching_candidate() -> None:
    # select_one over the index must reject a bucket element that fails the full
    # compound selector and keep scanning to the next candidate of the same tag
    doc = parse(_DOC)
    assert (match := doc.select_one("p.tail")) is not None  # the first <p> is .lead, not .tail
    assert match.text == "b"


def test_find_single_subtree_origin_no_match() -> None:
    # the typed subtree walk exhausts every descendant without a match and reports
    # None, exercising the loop's terminating condition
    section = parse(_DOC).find("section")
    assert section is not None
    assert section.find("table") is None


def test_index_reused_on_repeated_whole_tree_query() -> None:
    # the first whole-tree query builds the index; the next reuses it instead of
    # rebuilding, and both must return the same elements
    doc = parse(_DOC)
    assert [element.text for element in doc.find_all("p")] == ["a", "b", "nested"]
    assert [element.text for element in doc.find_all("p")] == ["a", "b", "nested"]
    assert (one := doc.find("p")) is not None
    assert one.text == "a"
    assert (two := doc.find("p")) is not None
    assert two.text == "a"


def first(html: str, tag: str) -> Element:
    element = parse(html).select_one(tag)
    assert element is not None
    return element


def test_re_no_group_returns_whole_matches() -> None:
    paragraph = first("<p>cat hat bat</p>", "p")
    assert paragraph.re(r"\w+at") == ["cat", "hat", "bat"]


def test_re_one_group_returns_that_group() -> None:
    paragraph = first("<p>id: 42, id: 7</p>", "p")
    assert paragraph.re(r"id: (\d+)") == ["42", "7"]


def test_re_two_groups_falls_back_to_whole_match() -> None:
    paragraph = first("<p>a=1 b=2</p>", "p")
    assert paragraph.re(r"(\w)=(\d)") == ["a=1", "b=2"]


def test_re_no_match_is_empty_list() -> None:
    assert first("<p>nothing</p>", "p").re(r"\d+") == []


def test_re_accepts_a_compiled_pattern() -> None:
    paragraph = first("<p>CAT cat</p>", "p")
    assert paragraph.re(re.compile(r"cat", re.IGNORECASE)) == ["CAT", "cat"]


def test_re_over_descendant_text() -> None:
    div = first("<div><b>foo</b> <i>bar</i></div>", "div")
    assert div.re(r"\w+") == ["foo", "bar"]


def test_re_over_attribute_value() -> None:
    anchor = first('<a href="/item/42/detail">x</a>', "a")
    assert anchor.re(r"/item/(\d+)", attr="href") == ["42"]


def test_re_over_absent_attribute_is_empty() -> None:
    assert first('<a href="/x">x</a>', "a").re(r"\d+", attr="title") == []


def test_re_attr_none_runs_over_text() -> None:
    paragraph = first("<p>year 2026</p>", "p")
    assert paragraph.re(r"\d+", attr=None) == ["2026"]


def test_re_over_valueless_attribute_is_empty() -> None:
    assert first("<input disabled>", "input").re(r".+", attr="disabled") == []


def test_re_bad_pattern_type() -> None:
    with pytest.raises(TypeError, match=r"pattern must be a str or a compiled re\.Pattern"):
        first("<p>x</p>", "p").re(123)  # ty: ignore[invalid-argument-type]


def test_re_invalid_regex_propagates() -> None:
    with pytest.raises(re.error):
        first("<p>x</p>", "p").re(r"(")


def test_re_attr_name_must_be_str() -> None:
    with pytest.raises(TypeError, match="attribute name must be a str"):
        first("<p>x</p>", "p").re(r"\d+", attr=123)  # ty: ignore[invalid-argument-type]


def test_re_first_returns_first_whole_match() -> None:
    assert first("<p>cat hat</p>", "p").re_first(r"\w+at") == "cat"


def test_re_first_returns_first_group() -> None:
    assert first("<p>id: 42, id: 7</p>", "p").re_first(r"id: (\d+)") == "42"


def test_re_first_two_groups_falls_back_to_whole_match() -> None:
    assert first("<p>a=1 b=2</p>", "p").re_first(r"(\w)=(\d)") == "a=1"


def test_re_first_no_match_defaults_to_none() -> None:
    assert first("<p>nothing</p>", "p").re_first(r"\d+") is None


def test_re_first_no_match_returns_given_default() -> None:
    assert first("<p>nothing</p>", "p").re_first(r"\d+", "missing") == "missing"


def test_re_first_default_is_keyword() -> None:
    assert first("<p>nothing</p>", "p").re_first(r"\d+", default="kw") == "kw"


def test_re_first_over_attribute_value() -> None:
    anchor = first('<a href="/item/42">x</a>', "a")
    assert anchor.re_first(r"/item/(\d+)", attr="href") == "42"


def test_re_first_over_absent_attribute_uses_default() -> None:
    assert first('<a href="/x">x</a>', "a").re_first(r"\d+", "none", attr="title") == "none"


def test_re_first_over_valueless_attribute_uses_default() -> None:
    assert first("<input disabled>", "input").re_first(r".+", "none", attr="disabled") == "none"


def test_re_first_bad_pattern_type() -> None:
    with pytest.raises(TypeError, match=r"pattern must be a str or a compiled re\.Pattern"):
        first("<p>x</p>", "p").re_first(123)  # ty: ignore[invalid-argument-type]


def test_re_first_attr_name_must_be_str() -> None:
    with pytest.raises(TypeError, match="attribute name must be a str"):
        first("<p>x</p>", "p").re_first(r"\d+", attr=123)  # ty: ignore[invalid-argument-type]


def test_re_requires_a_pattern() -> None:
    with pytest.raises(TypeError):
        first("<p>x</p>", "p").re()  # ty: ignore[missing-argument]


def test_re_first_requires_a_pattern() -> None:
    with pytest.raises(TypeError):
        first("<p>x</p>", "p").re_first()  # ty: ignore[missing-argument]


def _body(document: Document) -> Element:
    """The body element of a parsed document, asserted present."""
    body = document.select_one("body")
    assert body is not None
    return body


def _node(document: Document, selector: str) -> Element:
    """The first element matching selector, asserted present."""
    node = document.select_one(selector)
    assert node is not None
    return node


def test_keeps_match_and_drops_unrelated_siblings() -> None:
    document = parse("<body><nav>skip</nav><main><article>keep</article><aside>drop</aside></main></body>")
    document.prune("article")
    assert _body(document).serialize() == "<body><main><article>keep</article></main></body>"


def test_keeps_the_whole_subtree_under_a_match() -> None:
    document = parse("<main><article>text<b>bold</b><span>more</span></article><p>gone</p></main>")
    document.prune("article")
    assert (
        _body(document).serialize() == "<body><main><article>text<b>bold</b><span>more</span></article></main></body>"
    )


def test_keeps_each_of_several_matches() -> None:
    document = parse("<ul><li class=on>a</li><li>b</li><li class=on>c</li></ul>")
    document.prune("li.on")
    assert _body(document).serialize() == '<body><ul><li class="on">a</li><li class="on">c</li></ul></body>'


def test_descendant_combinator_uses_the_full_matcher() -> None:
    document = parse("<main><section><article>in</article></section><article>out</article></main>")
    document.prune("section article")
    assert _body(document).serialize() == "<body><main><section><article>in</article></section></main></body>"


def test_nested_matches_keep_the_outer_whole_subtree() -> None:
    document = parse("<div class=keep><span class=keep>x</span><i>y</i></div><div>drop</div>")
    document.prune(".keep")
    assert _body(document).serialize() == '<body><div class="keep"><span class="keep">x</span><i>y</i></div></body>'


def test_grows_the_keep_buffer_for_many_matches() -> None:
    items = "".join(f"<li class=x>{index}</li>" for index in range(20))
    document = parse(f"<ul>{items}</ul>")
    container = document.select_one("ul")
    assert container is not None
    container.prune("li.x")
    assert len(container.select("li")) == 20


def test_no_match_empties_the_subtree() -> None:
    document = parse("<main><p>a</p><p>b</p></main>")
    main = document.select_one("main")
    assert main is not None
    main.prune("article")
    assert main.serialize() == "<main></main>"


def test_no_match_on_the_document_root_empties_everything() -> None:
    document = parse("<main><p>a</p></main>")
    document.prune("article")
    assert document.select_one("html") is None


def test_removes_text_and_comment_nodes_outside_a_match() -> None:
    document = parse("<main>loose<!--c--><article>keep</article>more</main>")
    document.prune("article")
    assert _body(document).serialize() == "<body><main><article>keep</article></main></body>"


def test_prunes_relative_to_the_node_it_is_called_on() -> None:
    document = parse("<section><h1>title</h1><div><p class=hit>x</p><p>y</p></div></section>")
    section = document.select_one("section")
    assert section is not None
    section.prune(".hit")
    assert section.serialize() == '<section><div><p class="hit">x</p></div></section>'


def test_returns_the_node_for_chaining() -> None:
    document = parse("<main><article>a</article></main>")
    assert assert_type(document.prune("article"), Document) is document


def test_keeps_a_deep_match_through_its_ancestor_chain() -> None:
    markup = "<main><section><article class=keep>deep</article></section><aside>drop</aside></main>"
    document = parse(markup)
    document.prune(".keep")
    assert (
        _body(document).serialize()
        == '<body><main><section><article class="keep">deep</article></section></main></body>'
    )


def test_rejects_a_non_str_selector() -> None:
    document = parse("<p>x</p>")
    with pytest.raises(TypeError):
        getattr(document, "prune")(123)  # ruff:ignore[get-attr-with-constant]  # getattr keeps the bad arg from the type checker


def test_rejects_an_invalid_selector() -> None:
    with pytest.raises(ValueError, match="selector"):
        parse("<p>x</p>").prune("[")


@pytest.mark.parametrize(
    ("markup", "where", "selector", "expected"),
    [
        pytest.param(
            "<main><article>text<b>bold</b></article><p>keep</p></main>",
            None,
            "article",
            "<body><main><p>keep</p></main></body>",
            id="single-match-and-its-subtree",
        ),
        pytest.param(
            "<ul><li class=off>a</li><li>b</li><li class=off>c</li></ul>",
            None,
            "li.off",
            "<body><ul><li>b</li></ul></body>",
            id="each-of-several-matches",
        ),
        pytest.param(
            "<main>loose<!--c--><aside>drop</aside>more</main>",
            None,
            "aside",
            "<body><main>loose<!--c-->more</main></body>",
            id="keeps-unrelated-siblings-and-text",
        ),
        pytest.param(
            "<main><section><article>in</article></section><article>out</article></main>",
            None,
            "section article",
            "<body><main><section></section><article>out</article></main></body>",
            id="descendant-combinator-uses-the-full-matcher",
        ),
        pytest.param(
            "<div class=drop><span class=drop>x</span><i>y</i></div><p>stay</p>",
            None,
            ".drop",
            "<body><p>stay</p></body>",
            id="nested-match-inside-a-removed-match-drops-harmlessly",
        ),
        pytest.param(
            "<main><p>a</p><p>b</p></main>",
            "main",
            "article",
            "<main><p>a</p><p>b</p></main>",
            id="no-match-leaves-the-subtree-untouched",
        ),
    ],
)
def test_remove_drops_each_matching_subtree(markup: str, where: str | None, selector: str, expected: str) -> None:
    document = parse(markup)
    target = _body(document) if where is None else _node(document, where)
    assert assert_type(target.remove(selector), Element) is target
    assert target.serialize() == expected


@pytest.mark.parametrize(
    ("markup", "where", "selector", "expected"),
    [
        pytest.param(
            "<p>a <b>bold</b> z</p>",
            None,
            "b",
            "<body><p>a bold z</p></body>",
            id="single-match-keeping-its-children",
        ),
        pytest.param(
            "<p><span>one</span> and <span>two</span></p>",
            None,
            "span",
            "<body><p>one and two</p></body>",
            id="each-of-several-matches",
        ),
        pytest.param(
            "<div class=wrap><p>x</p><p>y</p></div>",
            None,
            ".wrap",
            "<body><p>x</p><p>y</p></body>",
            id="a-match-with-element-children",
        ),
        pytest.param(
            "<p>a<span></span>b</p>",
            None,
            "span",
            "<body><p>ab</p></body>",
            id="empty-match-is-just-dropped",
        ),
        pytest.param(
            "<p><b><i>x</i></b></p>",
            None,
            "b, i",
            "<body><p>x</p></body>",
            id="nested-matches-collapse-to-the-inner-content",
        ),
        pytest.param(
            "<main><section><b>in</b></section><b>out</b></main>",
            None,
            "section b",
            "<body><main><section>in</section><b>out</b></main></body>",
            id="descendant-combinator-uses-the-full-matcher",
        ),
        pytest.param(
            "<p>a<b>x</b></p>",
            "p",
            "i",
            "<p>a<b>x</b></p>",
            id="no-match-leaves-the-subtree-untouched",
        ),
    ],
)
def test_strip_tags_unwraps_each_matching_element(markup: str, where: str | None, selector: str, expected: str) -> None:
    document = parse(markup)
    target = _body(document) if where is None else _node(document, where)
    assert assert_type(target.strip_tags(selector), Element) is target
    assert target.serialize() == expected


@pytest.mark.parametrize(
    ("method", "markup", "where", "selector", "expected"),
    [
        pytest.param(
            "remove",
            "<section><p class=hit>x</p></section><p class=hit>y</p>",
            "section",
            ".hit",
            ("<section></section>", '<body><section></section><p class="hit">y</p></body>'),
            id="remove",
        ),
        pytest.param(
            "strip_tags",
            "<section><b>in</b></section><b>out</b>",
            "section",
            "b",
            ("<section>in</section>", "<body><section>in</section><b>out</b></body>"),
            id="strip_tags",
        ),
    ],
)
def test_bulk_edit_acts_only_within_the_node_it_is_called_on(
    method: str,
    markup: str,
    where: str,
    selector: str,
    expected: tuple[str, str],
) -> None:
    expected_node, expected_body = expected
    document = parse(markup)
    node = _node(document, where)
    getattr(node, method)(selector)
    assert node.serialize() == expected_node
    assert _body(document).serialize() == expected_body


def test_remove_grows_the_snapshot_buffer_for_many_matches() -> None:
    items = "".join(f"<li class=x>{index}</li>" for index in range(20))
    document = parse(f"<ul>{items}<li>keep</li></ul>")
    container = _node(document, "ul")
    container.remove("li.x")
    assert [item.text for item in container.select("li")] == ["keep"]


def test_strip_tags_grows_the_snapshot_buffer_for_many_matches() -> None:
    spans = "".join(f"<span>{index}</span>" for index in range(20))
    document = parse(f"<p>{spans}</p>")
    paragraph = _node(document, "p")
    paragraph.strip_tags("span")
    assert paragraph.select("span") == []
    assert paragraph.text == "".join(str(index) for index in range(20))


def test_remove_is_the_inverse_of_prune() -> None:
    markup = "<main><article>keep</article><aside>drop</aside></main>"
    pruned = parse(markup)
    pruned.prune("aside")
    removed = parse(markup)
    removed.remove("article")
    assert _body(pruned).serialize() == "<body><main><aside>drop</aside></main></body>"
    assert _body(removed).serialize() == "<body><main><aside>drop</aside></main></body>"


def test_strip_tags_is_the_bulk_form_of_unwrap() -> None:
    bulk = parse("<p><b>x</b></p>")
    bulk.strip_tags("b")
    single = parse("<p><b>x</b></p>")
    _node(single, "b").unwrap()
    assert _body(bulk).serialize() == "<body><p>x</p></body>"
    assert _body(single).serialize() == "<body><p>x</p></body>"


@pytest.mark.parametrize("method", [pytest.param("remove", id="remove"), pytest.param("strip_tags", id="strip_tags")])
def test_bulk_edit_returns_the_node_for_chaining(method: str) -> None:
    document = parse("<main><article><b>a</b></article></main>")
    assert (
        assert_type(document.remove("article") if method == "remove" else document.strip_tags("article"), Document)
        is document
    )


@pytest.mark.parametrize("method", [pytest.param("remove", id="remove"), pytest.param("strip_tags", id="strip_tags")])
def test_bulk_edit_rejects_a_non_str_selector(method: str) -> None:
    document = parse("<p>x</p>")
    with pytest.raises(TypeError):
        getattr(document, method)(123)  # getattr keeps the bad arg from the type checker


@pytest.mark.parametrize("method", [pytest.param("remove", id="remove"), pytest.param("strip_tags", id="strip_tags")])
def test_bulk_edit_rejects_an_invalid_selector(method: str) -> None:
    with pytest.raises(ValueError, match="selector"):
        getattr(parse("<p>x</p>"), method)("[")


@pytest.mark.parametrize("depth", [pytest.param(1, id="shallow"), pytest.param(100, id="deep")])
@pytest.mark.parametrize("selector", [pytest.param("b", id="leaves"), pytest.param("section, b", id="overlap")])
def test_prune_shared_ancestors(depth: int, selector: str) -> None:
    document: Final = parse(
        "<main>" + "<section>" * depth + "<b>A</b><i>drop</i><b>B</b>" + "</section>" * depth + "<i>outside</i></main>"
    )
    document.prune(selector)
    assert document.xpath("//main//text()") == (["A", "drop", "B"] if selector == "section, b" else ["A", "B"])


def test_prune_keeps_matching_subtree() -> None:
    document: Final = parse("<main><section><b>A</b><i>inside</i></section><i>outside</i></main>")
    document.prune("section, b")
    assert document.xpath("//main//text()") == ["A", "inside"]


def test_prune_detached_subtree_keeps_removed_references() -> None:
    document: Final = parse("<main><section><div><b>A</b><i>drop</i><b>B</b></div></section></main>")
    section: Final = document.select_one("section")
    removed: Final = document.select_one("i")
    assert section is not None
    assert removed is not None
    document.remove("section")
    section.prune("b")
    assert (section.serialize(), removed.serialize(), removed.parent) == (
        "<section><div><b>A</b><b>B</b></div></section>",
        "<i>drop</i>",
        None,
    )


@pytest.mark.skipif(
    sys.implementation.name != "cpython", reason="requires immediate CPython reference-count finalization"
)
@pytest.mark.parametrize("adopt", [pytest.param(False, id="same-tree"), pytest.param(True, id="adopted-owner")])
@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        pytest.param(lambda node: str(len(node.select("[fresh]"))), "1", id="select"),
        pytest.param(lambda node: (node.select_one("[fresh]") or node).tag, "b", id="select-one"),
        pytest.param(lambda node: str(node.matches("[fresh]")), "True", id="matches"),
        pytest.param(lambda node: (node.closest("[fresh]") or Element("missing")).tag, "main", id="closest"),
        pytest.param(lambda node: node.prune("[fresh]").inner_html, '<b fresh="yes">hit</b>', id="prune"),
        pytest.param(lambda node: node.remove("[fresh]").inner_html, "<i>miss</i>", id="remove"),
        pytest.param(lambda node: node.strip_tags("[fresh]").inner_html, "hit<i>miss</i>", id="strip-tags"),
        pytest.param(lambda node: str(len(Query([node, Element("aside")]).find("[fresh]"))), "1", id="query-find"),
        pytest.param(lambda node: str(len(Query(node).filter("[fresh]"))), "1", id="query-filter"),
    ],
)
def test_selector_eviction_refreshes_mutated_owner(
    operation: Callable[[Element], str], expected: str, *, adopt: bool
) -> None:
    owner: Final = parse("<main><b>hit</b><i>miss</i></main>").select("main")[0]
    destination: Final = Element("section")

    class MutatingSelector(str):  # ruff:ignore[subclass-builtin]  # native selectors require a real str
        __slots__ = ()

        def __del__(self) -> None:
            if adopt:
                destination.append(owner)
            owner.attrs["fresh"] = "yes"
            owner.select("b")[0].attrs["fresh"] = "yes"

    owner.select(MutatingSelector("unmatched"))
    for index in range(15):
        owner.select(f"unused{index}")
    assert operation(owner) == expected


@pytest.mark.skipif(
    sys.implementation.name != "cpython", reason="requires immediate CPython reference-count finalization"
)
def test_selector_eviction_refreshes_owner_returned_to_original_tree() -> None:
    home: Final = Element("section")
    owner: Final = Element("main")
    owner.append(Element("b"))
    home.append(owner)
    destination: Final = Element("aside")

    class MovingSelector(str):  # ruff:ignore[subclass-builtin]  # native selectors require a real str
        __slots__ = ()

        def __del__(self) -> None:
            destination.append(owner)
            home.append(owner)
            owner.select("b")[0].attrs["fresh"] = "yes"

    owner.select(MovingSelector("unmatched"))
    for index in range(15):
        owner.select(f"unused{index}")
    assert [node.tag for node in owner.select("[fresh]")] == ["b"]


@pytest.mark.parametrize("count", [pytest.param(0, id="empty"), pytest.param(1, id="one"), pytest.param(33, id="many")])
@pytest.mark.parametrize(
    "limit", [pytest.param(0, id="all"), pytest.param(1, id="first"), pytest.param(40, id="above")]
)
@pytest.mark.parametrize("warm", [pytest.param(False, id="cold"), pytest.param(True, id="warm")])
def test_select_indexed_order(count: int, limit: int, *, warm: bool) -> None:
    document: Final = parse("".join(f"<section><input id='{index}'><span></span></section>" for index in range(count)))
    if warm:
        document.select("span")
    assert [element.attrs["id"] for element in select("input", document, limit=limit)] == [
        str(index) for index in range(count)
    ][: limit or None]


def test_select_indexed_mutation() -> None:
    document: Final = parse("<main><input id='first'><input id='second'></main>")
    first, second = document.select("input")
    first.tag = "span"
    second.extract()
    main: Final = document.find("main")
    assert main is not None
    main.append(Element("input", attrs={"id": "third"}))
    assert [element.attrs["id"] for element in document.select("input")] == ["third"]


def test_select_indexed_subtree_scope() -> None:
    document: Final = parse("<input id='outside'><main><input id='inside'></main>")
    document.select("input")
    main: Final = document.find("main")
    assert main is not None
    assert [element.attrs["id"] for element in main.select("input")] == ["inside"]


def test_select_indexed_xml_case() -> None:
    document: Final = parse_xml('<root><input id="lower"/><INPUT id="upper"/></root>')
    assert [element.attrs["id"] for element in document.select("input")] == ["lower"]


@pytest.mark.skipif(sys.implementation.name != "cpython", reason="CPython allocation-triggered collection")
@pytest.mark.parametrize("offset", range(8), ids=lambda offset: f"allocation-{offset}")
@pytest.mark.parametrize("count", [pytest.param(7, id="shrink"), pytest.param(67, id="grow")])
def test_select_indexed_collection(offset: int, count: int) -> None:
    thresholds: Final = gc.get_threshold()
    restore_gc: Final = gc.enable if gc.isenabled() else gc.disable
    gc.disable()
    gc.collect()
    document: Final = parse("<main>" + "<input id='old'>" * 33 + "</main>")
    document.select("input")
    main: Final = document.find("main")
    assert main is not None
    collect: Final = document.select
    changed = False

    def clear(phase: str, _info: dict[str, int]) -> None:
        nonlocal changed
        if phase == "start" and not changed:
            changed = True
            main.set_inner_html("<input id='new'>" * count)

    # Empty lists exhaust CPython's freelist so result allocation can trigger collection.
    reserve: Final[list[list[None]]] = [[] for _ in range(512)]
    gc.callbacks.append(clear)
    try:
        gc.set_threshold(gc.get_count()[0] + offset, thresholds[1], thresholds[2])
        gc.enable()
        result: Final = collect("input")
        gc.collect()
    finally:
        gc.disable()
        gc.callbacks.remove(clear)
        gc.set_threshold(*thresholds)
        restore_gc()
        reserve.clear()
    assert (changed, tuple(element.attrs["id"] for element in result), main.inner_html) in {
        (True, ("old",) * 33, '<input id="new">' * count),
        (True, ("new",) * count, '<input id="new">' * count),
    }


@pytest.mark.parametrize(
    "limit", [pytest.param(1, id="first"), pytest.param(2, id="exact"), pytest.param(9, id="above")]
)
def test_select_indexed_filtered_limit(limit: int) -> None:
    document: Final = parse('<input id="skip"><input id="first" checked><input id="second" checked>')
    document.select("input")
    assert [element.attrs["id"] for element in select("input[checked]", document, limit=limit)] == ["first", "second"][
        :limit
    ]


def test_one_compiled_expression_across_threads_each_correct() -> None:
    selector = XPath("//td[@class=$cls]")
    documents = [
        turbohtml.parse(f"<table><tr><td class='num'>{index}</td><td>x</td></tr></table>") for index in range(8)
    ]
    results: dict[int, list[str]] = {}
    lock = threading.Lock()
    start = threading.Barrier(len(documents))

    def worker(index: int) -> None:
        start.wait()
        result = selector(documents[index], cls="num")
        assert isinstance(result, list)
        cells = [cell.text for cell in result if isinstance(cell, Element)]
        with lock:
            results[index] = cells

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(len(documents))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == {index: [str(index)] for index in range(len(documents))}


def test_concurrent_evaluation_on_one_document_is_memory_safe() -> None:
    document = turbohtml.parse("<body>" + "".join(f"<p>{index}</p>" for index in range(100)) + "</body>")
    selector = XPath("//p")
    start = threading.Barrier(4)
    counts: list[int] = []
    lock = threading.Lock()

    def worker() -> None:
        start.wait()
        for _ in range(50):
            result = selector(document)
            assert isinstance(result, list)
            found = len(result)
            with lock:
                counts.append(found)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert counts == [100] * 200


@pytest.mark.skipif(sys.implementation.name == "pypy", reason="PyPy's gc exposes no is_tracked")
def test_object_is_gc_tracked() -> None:
    selector = XPath("//a")
    assert gc.is_tracked(selector)


def test_collect_with_live_objects_traverses_them() -> None:
    def extension(_context: SimpleNamespace) -> str:
        return "x"

    extensions: dict[tuple[str | None, str], Callable[..., str | float | bool]] = {(None, "x"): extension}
    plain = XPath("//a")
    with_extensions = XPath("x()", extensions=extensions)
    gc.collect()
    compiled_doc = turbohtml.parse("<a>one</a>")
    assert plain(compiled_doc) == [compiled_doc.xpath_one("//a")]
    assert with_extensions(compiled_doc) == "x"


@pytest.mark.skipif(
    sys.implementation.name == "pypy",
    reason="cpyext never breaks a cycle that runs through both a C extension object and a Python one, "
    "so this cycle leaks there; see docs/explanation/interpreters.rst",
)
def test_extension_reference_cycle_is_collected() -> None:
    class Holder:
        ref: XPath | None = None

        def extension(self, _context: SimpleNamespace) -> str:
            return repr(self.ref)

    holder = Holder()
    extensions: dict[tuple[str | None, str], Callable[..., str | float | bool]] = {(None, "x"): holder.extension}
    selector = XPath("x()", extensions=extensions)
    holder.ref = selector  # selector -> extensions -> bound method -> holder -> selector
    assert selector(turbohtml.parse("<a>one</a>")) == "XPath('x()')"
    del selector
    del holder
    del extensions
    assert gc.collect() >= 0


COMPILED_HTML: Final[str] = "<html><body><div><p>a</p><p>b</p></div><a href='/x'>x</a></body></html>"


@pytest.fixture
def compiled_doc() -> turbohtml.Node:
    return turbohtml.parse(COMPILED_HTML)


def tags(result: object) -> list[str]:
    assert isinstance(result, list)
    return [node.tag for node in result if isinstance(node, Element)]


def test_repeated_query_is_consistent(compiled_doc: turbohtml.Node) -> None:
    for _ in range(5):
        assert tags(compiled_doc.xpath("//p")) == ["p", "p"]


def test_move_to_front(compiled_doc: turbohtml.Node) -> None:
    # //p falls behind //a in the cache, then is queried again from a non-front slot
    compiled_doc.xpath("//p")
    compiled_doc.xpath("//a")
    assert tags(compiled_doc.xpath("//p")) == ["p", "p"]


def test_distinct_but_equal_key_hits_same_entry(compiled_doc: turbohtml.Node) -> None:
    name = "p"
    first = f"//{name}"
    second = f"//{name}"
    assert first is not second  # two distinct str objects with equal content
    compiled_doc.xpath(first)
    assert tags(compiled_doc.xpath(second)) == ["p", "p"]


def test_eviction_recompiles(compiled_doc: turbohtml.Node) -> None:
    assert tags(compiled_doc.xpath("//p")) == ["p", "p"]
    # fill past the cache capacity with distinct expressions, evicting //p
    for index in range(20):
        assert compiled_doc.xpath(f"//missing{index}") == []
    # //p was evicted; it must recompile to the same answer
    assert tags(compiled_doc.xpath("//p")) == ["p", "p"]


def test_compile_error_after_caching(compiled_doc: turbohtml.Node) -> None:
    compiled_doc.xpath("//p")  # populate the cache first
    with pytest.raises(ValueError, match="node test"):
        compiled_doc.xpath("//")


SMART_HTML: Final[str] = "<html><body><a href='/x' class='c'>link</a></body></html>"


@pytest.fixture
def smart_doc() -> turbohtml.Node:
    return turbohtml.parse(SMART_HTML)


def first_xpath_result(result: object) -> object:
    assert isinstance(result, list)
    return result[0]


def test_default_is_a_plain_string(smart_doc: turbohtml.Node) -> None:
    result = first_xpath_result(smart_doc.xpath("//a/@href"))
    assert type(result) is str
    assert result == "/x"


def test_smart_strings_false_is_a_plain_string(smart_doc: turbohtml.Node) -> None:
    result = first_xpath_result(smart_doc.xpath("//a/@href", smart_strings=False))
    assert type(result) is str


def test_a_plain_variable_keyword_does_not_enable_smart_strings(smart_doc: turbohtml.Node) -> None:
    # kwargs present but no smart_strings key: the result stays a plain str.
    result = first_xpath_result(smart_doc.xpath("//a[@href=$h]/@href", h="/x"))
    assert type(result) is str


def test_smart_attribute_remembers_its_element(smart_doc: turbohtml.Node) -> None:
    result = first_xpath_result(smart_doc.xpath("//a/@href", smart_strings=True))
    assert isinstance(result, XPathString)
    assert result == "/x"
    assert result.is_attribute is True
    assert result.is_text is False
    assert result.is_tail is False
    assert result.attrname == "href"
    parent = result.getparent()
    assert isinstance(parent, Element)
    assert parent.tag == "a"


def test_smart_text_remembers_its_element(smart_doc: turbohtml.Node) -> None:
    result = first_xpath_result(smart_doc.xpath("//a/text()", smart_strings=True))
    assert isinstance(result, XPathString)
    assert result == "link"
    assert result.is_text is True
    assert result.is_attribute is False
    assert result.attrname is None
    assert result.getparent().tag == "a"


def test_smart_strings_alongside_a_variable(smart_doc: turbohtml.Node) -> None:
    # smart_strings is consumed as an option; h is still bound as a $variable.
    result = first_xpath_result(smart_doc.xpath("//a[@href=$h]/@href", h="/x", smart_strings=True))
    assert isinstance(result, XPathString)
    assert result.getparent().tag == "a"


def test_smart_strings_through_xpath_one(smart_doc: turbohtml.Node) -> None:
    result = smart_doc.xpath_one("//a/@class", smart_strings=True)
    assert isinstance(result, XPathString)
    assert result.attrname == "class"


def test_smart_strings_through_xpath_iter(smart_doc: turbohtml.Node) -> None:
    results = list(smart_doc.xpath_iter("//a/@href", smart_strings=True))
    assert all(isinstance(result, XPathString) for result in results)


EXTENSIONS_HTML: Final[str] = "<html><body><a href='/x'>one</a><a href='/y'>two</a></body></html>"


def count_nodes(_context: SimpleNamespace, nodes: list[object]) -> float:
    return float(len(nodes))


def shout(_context: SimpleNamespace, text: str) -> str:
    return text.upper()


def echo(_context: SimpleNamespace, value: float | bool) -> float | bool:  # ruff:ignore[boolean-type-hint-positional-argument]  # positional by convention
    return value


def context_tag(context: SimpleNamespace) -> str:
    return context.context_node.tag


EXTENSIONS: dict[tuple[str | None, str], Callable[..., str | float | bool | Element | Iterable[Element]]] = {
    (None, "count_nodes"): count_nodes,
    (None, "shout"): shout,
    (None, "echo"): echo,
    (None, "context_tag"): context_tag,
}


@pytest.fixture
def extension_doc() -> turbohtml.Node:
    return turbohtml.parse(EXTENSIONS_HTML)


def test_nodeset_argument_arrives_as_a_list(extension_doc: turbohtml.Node) -> None:
    assert extension_doc.xpath("count_nodes(//a)", extensions=EXTENSIONS) == pytest.approx(2.0)


def test_string_argument_and_string_return(extension_doc: turbohtml.Node) -> None:
    assert extension_doc.xpath("shout(string(//a[1]/@href))", extensions=EXTENSIONS) == "/X"


def test_number_argument_round_trips(extension_doc: turbohtml.Node) -> None:
    assert extension_doc.xpath("echo(40 + 2)", extensions=EXTENSIONS) == pytest.approx(42.0)


def test_boolean_argument_round_trips(extension_doc: turbohtml.Node) -> None:
    assert extension_doc.xpath("echo(true())", extensions=EXTENSIONS) is True


def test_context_node_is_the_current_element(extension_doc: turbohtml.Node) -> None:
    result = extension_doc.xpath("//a[context_tag()='a']", extensions=EXTENSIONS)
    assert isinstance(result, list)
    nodes = [n for n in result if isinstance(n, Element)]
    assert [n.text for n in nodes] == ["one", "two"]


def test_extension_in_a_predicate(extension_doc: turbohtml.Node) -> None:
    result = extension_doc.xpath("//a[count_nodes(.) = 1]", extensions=EXTENSIONS)
    assert isinstance(result, list)
    nodes = [n for n in result if isinstance(n, Element)]
    assert [n.text for n in nodes] == ["one", "two"]


def test_extension_alongside_a_variable(extension_doc: turbohtml.Node) -> None:
    assert extension_doc.xpath("shout($s)", s="hi", extensions=EXTENSIONS) == "HI"


def test_extension_through_xpath_one(extension_doc: turbohtml.Node) -> None:
    assert extension_doc.xpath_one("count_nodes(//a)", extensions=EXTENSIONS) == pytest.approx(2.0)


def test_unknown_function_with_extensions_raises(extension_doc: turbohtml.Node) -> None:
    with pytest.raises(ValueError, match="unknown function 'nope'"):
        extension_doc.xpath("nope()", extensions=EXTENSIONS)


def test_unknown_function_without_extensions_raises(extension_doc: turbohtml.Node) -> None:
    with pytest.raises(ValueError, match="unknown function 'count_nodes'"):
        extension_doc.xpath("count_nodes(//a)")


def test_empty_extensions_dict_registers_nothing(extension_doc: turbohtml.Node) -> None:
    with pytest.raises(ValueError, match="unknown function 'count_nodes'"):
        extension_doc.xpath("count_nodes(//a)", extensions={})


def test_none_extensions_is_the_same_as_omitting_it(extension_doc: turbohtml.Node) -> None:
    assert isinstance(extension_doc.xpath("//a", extensions=None), list)


def test_extension_that_raises_propagates(extension_doc: turbohtml.Node) -> None:
    with pytest.raises(ZeroDivisionError):
        extension_doc.xpath("boom()", extensions={(None, "boom"): lambda _context: 1 / 0})


def test_extension_returning_a_non_scalar_is_a_type_error(extension_doc: turbohtml.Node) -> None:
    with pytest.raises(TypeError, match="extension result must be"):
        extension_doc.xpath("bad()", extensions={(None, "bad"): lambda _context: [1, 2]})  # ty: ignore[invalid-argument-type]  # non-scalar return on purpose


def test_extensions_must_be_a_dict(extension_doc: turbohtml.Node) -> None:
    with pytest.raises(TypeError, match="extensions must be a dict"):
        extension_doc.xpath("//a", extensions="not a dict")  # ty: ignore[invalid-argument-type]  # wrong type on purpose


def test_extension_receiving_an_element_from_a_nodeset(extension_doc: turbohtml.Node) -> None:
    # the marshaled node-set holds Element objects the extension can navigate.
    def first_text(_context: SimpleNamespace, nodes: list[Element]) -> str:
        return nodes[0].text

    assert extension_doc.xpath("first_text(//a)", extensions={(None, "first_text"): first_text}) == "one"


def first_node(_context: SimpleNamespace, nodes: list[Element]) -> Element:
    return nodes[0]


def first_two(_context: SimpleNamespace, nodes: list[Element]) -> list[Element]:
    return nodes[:2]


def each(_context: SimpleNamespace, nodes: list[Element]) -> Iterator[Element]:
    yield from nodes


def all_nodes(_context: SimpleNamespace, nodes: list[Element]) -> list[Element]:
    return nodes


def empty(_context: SimpleNamespace, _nodes: list[Element]) -> list[Element]:
    return []


NODESET_EXTENSIONS: dict[tuple[str | None, str], Callable[..., str | float | bool | Element | Iterable[Element]]] = {
    (None, "first_node"): first_node,
    (None, "first_two"): first_two,
    (None, "each"): each,
    (None, "all_nodes"): all_nodes,
    (None, "empty"): empty,
}


@pytest.fixture
def big_doc() -> turbohtml.Node:
    return turbohtml.parse("<ul>" + "".join(f"<li>{index}</li>" for index in range(12)) + "</ul>")


def _texts(result: object) -> list[str]:
    assert isinstance(result, list)
    return [node.text if isinstance(node, Element) else str(node) for node in result]


@pytest.mark.parametrize(
    ("fixture", "expression", "expected"),
    [
        pytest.param("doc", "first_node(//a)", ["one"], id="single-element-is-a-node-set"),
        pytest.param("doc", "first_two(//a)", ["one", "two"], id="list-of-elements-is-a-node-set"),
        pytest.param("doc", "each(//a)", ["one", "two"], id="generator-of-elements-is-a-node-set"),
        pytest.param("doc", "empty(//a)", [], id="empty-iterable-is-an-empty-node-set"),
        pytest.param("doc", "first_node(//a)/text()", ["one"], id="node-set-feeds-a-later-path-step"),
        pytest.param("doc", "first_two(//a)[2]", ["two"], id="node-set-feeds-a-predicate"),
        pytest.param(
            "big_doc", "all_nodes(//li)", [str(index) for index in range(12)], id="many-elements-grow-the-node-set"
        ),
    ],
)
def test_extension_result_becomes_a_node_set(
    extension_doc: turbohtml.Node, big_doc: turbohtml.Node, *, fixture: str, expression: str, expected: list[str]
) -> None:
    page: Final = big_doc if fixture == "big_doc" else extension_doc
    assert _texts(page.xpath(expression, extensions=NODESET_EXTENSIONS)) == expected


def test_extension_returning_an_integer_stays_a_number(extension_doc: turbohtml.Node) -> None:
    assert extension_doc.xpath("five()", extensions={(None, "five"): lambda _context: 5}) == pytest.approx(5.0)


def return_none(_context: SimpleNamespace) -> Element:
    return cast("Element", None)


def mixed(_context: SimpleNamespace, nodes: list[Element]) -> list[Element]:
    return [nodes[0], cast("Element", 123)]


def raise_partway(_context: SimpleNamespace, nodes: list[Element]) -> Iterator[Element]:
    yield nodes[0]
    msg = "boom"
    raise RuntimeError(msg)


_OTHER_DOCUMENT = turbohtml.parse("<p>elsewhere</p>")
_STRANGER = next(node for node in _OTHER_DOCUMENT.xpath_iter("//p") if isinstance(node, Element))


def steal(_context: SimpleNamespace) -> Element:
    return _STRANGER


@pytest.mark.parametrize(
    ("function", "expression", "exception", "match"),
    [
        pytest.param(
            return_none, "return_none()", TypeError, "extension result must be", id="none-result-is-a-type-error"
        ),
        pytest.param(
            mixed, "mixed(//a)", TypeError, "extension result must be", id="non-element-in-iterable-is-a-type-error"
        ),
        pytest.param(
            steal, "steal()", ValueError, "different document", id="foreign-document-element-is-a-value-error"
        ),
        pytest.param(
            raise_partway, "raise_partway(//a)", RuntimeError, "boom", id="iterable-that-raises-partway-propagates"
        ),
    ],
)
def test_extension_result_marshaling_is_rejected(
    extension_doc: turbohtml.Node,
    *,
    function: Callable[..., str | float | bool | Element | Iterable[Element]],
    expression: str,
    exception: type[Exception],
    match: str,
) -> None:
    name = expression[: expression.index("(")]
    with pytest.raises(exception, match=match):
        extension_doc.xpath(expression, extensions={(None, name): function})


@pytest.mark.parametrize("count", [31, 32, 33], ids=["small", "threshold", "large"])
@pytest.mark.parametrize(
    ("stride", "gap", "padding"),
    [
        pytest.param(1, "", 0, id="complete"),
        pytest.param(1, "", 1, id="one-omitted"),
        pytest.param(1, "text<!--gap-->", 2, id="dense"),
        pytest.param(19, "text<!--gap-->", 2, id="sparse"),
    ],
)
@pytest.mark.parametrize("order", ["sorted", "reversed", "shuffled"])
def test_find_sibling_root_order(count: int, stride: int, gap: str, padding: int, order: str) -> None:
    document: Final = parse(
        "<main>" + "".join(f"<div><i>{index}</i></div>{gap}" for index in range(count * stride + padding)) + "</main>"
    )
    start: Final = padding // 2
    roots = document.select("div")[start : start + count * stride : stride]
    if order == "reversed":
        roots.reverse()
    elif order == "shuffled":
        roots = roots[::2] + roots[1::2]
    assert [node.text for node in Query([*roots, *roots]).find("i")] == [
        str(start + index * stride) for index in range(count)
    ]


@pytest.mark.parametrize("order", ["sorted", "reversed", "shuffled"])
def test_find_mixed_parent_root_order(order: str) -> None:
    document: Final = parse(
        "<main>" + "".join(f"<section><div><i>{index}</i></div></section>" for index in range(40)) + "</main>"
    )
    roots = document.select("div")
    if order == "reversed":
        roots.reverse()
    elif order == "shuffled":
        roots = roots[::2] + roots[1::2]
    assert [node.text for node in Query(roots).find("i")] == [str(index) for index in range(40)]


def test_find_nested_root_order() -> None:
    document: Final = parse("<main>" + "<div><i>x</i>" * 40 + "</div>" * 40 + "</main>")
    assert list(Query(reversed(document.select("div"))).find("i")) == document.select("i")
