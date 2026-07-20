"""The compiled Matcher's soupsieve-shaped bound methods over the native engine."""

from __future__ import annotations

from typing import cast

import pytest

from turbohtml import Element, parse
from turbohtml.query import (
    Matching,
    SelectorSyntaxError,
    compile,  # ruff:ignore[builtin-import-shadowing]  # the soupsieve entry-point name
    css,
)

_DOC = "<div><a href=x>one</a><span><a href=y>two</a></span><a>bare</a></div>"


def _root() -> Element:
    root = parse(_DOC).root
    assert root is not None
    return root


def test_compile_returns_a_reusable_matcher() -> None:
    matcher = compile("a[href]")
    first, second = matcher.select(_root()), matcher.select(_root())
    assert [node.attr("href") for node in first] == [node.attr("href") for node in second] == ["x", "y"]


def test_css_is_a_compile_alias() -> None:
    assert [node.tag for node in css("a[href]").select(_root())] == ["a", "a"]


def test_select_returns_descendants_in_document_order() -> None:
    assert [node.attr("href") for node in compile("a[href]").select(_root())] == ["x", "y"]


def test_select_limit_caps_the_result() -> None:
    assert [node.attr("href") for node in compile("a[href]").select(_root(), limit=1)] == ["x"]


def test_select_zero_limit_returns_all() -> None:
    assert len(compile("a[href]").select(_root(), limit=0)) == 2


def test_select_one_returns_the_first_match() -> None:
    found = compile("a[href]").select_one(_root())
    assert found is not None
    assert found.attr("href") == "x"


def test_select_one_returns_none_when_absent() -> None:
    assert compile("table").select_one(_root()) is None


def test_iselect_yields_lazily() -> None:
    assert [node.attr("href") for node in compile("a[href]").iselect(_root())] == ["x", "y"]


def test_iselect_limit_caps_the_stream() -> None:
    assert [node.attr("href") for node in compile("a[href]").iselect(_root(), limit=1)] == ["x"]


def test_match_tests_a_single_element() -> None:
    anchor = compile("a[href]").select_one(_root())
    assert anchor is not None
    assert compile("a[href]").match(anchor) is True


def test_match_is_false_for_a_non_match() -> None:
    assert compile("a[href]").match(_root()) is False


def test_filter_keeps_matching_members_of_an_iterable() -> None:
    anchors = _root().select("a")
    assert [node.attr("href") for node in compile("[href]").filter(anchors)] == ["x", "y"]


def test_filter_accepts_a_generator() -> None:
    anchors = _root().select("a")
    assert [node.attr("href") for node in compile("[href]").filter(node for node in anchors)] == ["x", "y"]


def test_filter_preserves_order_and_duplicates_across_trees() -> None:
    first = parse("<a id=first class=hit></a><a id=miss></a>").select("a")
    second = parse("<a id=second class=hit></a>").select_one("a")
    assert second is not None
    assert [node.attr("id") for node in compile(".hit").filter([second, first[1], first[0], second])] == [
        "second",
        "first",
        "second",
    ]


@pytest.mark.parametrize(
    "candidates",
    [
        pytest.param([1], id="first"),
        pytest.param([_root(), 1], id="later"),
    ],
)
def test_filter_rejects_non_elements(candidates: list[object]) -> None:
    with pytest.raises(TypeError, match="filter candidates must be Element instances"):
        compile("*").filter(cast("list[Element]", candidates))


def test_filter_on_an_element_tests_its_direct_children() -> None:
    parent = parse("<div>text<a href=x>k</a><span><a href=deep>d</a></span><b>n</b></div>").select_one("div")
    assert parent is not None
    assert [node.tag for node in compile("a, span").filter(parent)] == ["a", "span"]


def test_closest_walks_up_to_the_nearest_match() -> None:
    anchor = compile("a[href=y]").select_one(_root())
    assert anchor is not None
    found = compile("div").closest(anchor)
    assert found is not None
    assert found.tag == "div"


def test_closest_returns_none_without_a_match() -> None:
    anchor = compile("a[href=x]").select_one(_root())
    assert anchor is not None
    assert compile("table").closest(anchor) is None


def test_compile_rejects_a_malformed_selector() -> None:
    with pytest.raises(SelectorSyntaxError):
        compile("a[")


def test_selector_syntax_error_is_a_value_error() -> None:
    assert issubclass(SelectorSyntaxError, ValueError)


def test_pattern_exposes_the_selector() -> None:
    assert compile("div a").pattern == "div a"


def test_namespaces_and_flags_default_to_soupsieves() -> None:
    matcher = compile("a")
    assert matcher.namespaces is None
    assert matcher.flags == 0


def test_options_are_carried_on_the_matcher() -> None:
    matcher = compile("a", Matching(namespaces={"svg": "http://www.w3.org/2000/svg"}, flags=1))
    assert matcher.namespaces == {"svg": "http://www.w3.org/2000/svg"}
    assert matcher.flags == 1
