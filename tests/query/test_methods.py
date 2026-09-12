from __future__ import annotations

import re
import sys
from typing import TYPE_CHECKING, Final

import pytest
from typing_extensions import assert_type

from turbohtml import Document, Element, parse
from turbohtml.query import Query

if TYPE_CHECKING:
    from collections.abc import Callable

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
