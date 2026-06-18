"""The per-tree element index and compiled-selector cache that back find()/select().

These exercise the read-path acceleration directly: the lazy subject-atom index
that whole-document tag queries enumerate, its fall back to a pre-order walk for a
subtree origin or an unknown subject, and the move-to-front compiled-selector cache
with its attribute-generation invalidation and capacity eviction. Behavior, not the
internal structure, is asserted: a query must return the same elements whether it
took the indexed bucket, the walk, a cached compile, or a fresh one.
"""

from __future__ import annotations

import pytest

from turbohtml import parse

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
    assert [element.text for element in doc.select("div p")] == ["nested"]
    descendant = "p"
    rebuilt = f"div {descendant}"  # an f-string is built at runtime: equal content, a distinct object
    assert rebuilt is not "div p"  # noqa: F632  # a content match, not an identity match, is the point
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
