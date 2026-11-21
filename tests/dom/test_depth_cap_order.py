from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Final

import pytest

from turbohtml import Element, Node, parse

if TYPE_CHECKING:
    from collections.abc import Mapping

_DEPTH_CAP: Final[int] = 512
_DEPTHS: Final[list[pytest.ParameterSet]] = [
    pytest.param(505, id="505"),
    pytest.param(_DEPTH_CAP, id="512"),
    pytest.param(_DEPTH_CAP + 1, id="513"),
    pytest.param(2000, id="2000"),
]
_TAILS: Final[list[pytest.ParameterSet]] = [
    pytest.param("<table><tr>c<td>E", "cE", id="table-row"),
    pytest.param("<table>c<td>E", "cE", id="table-text"),
    pytest.param("<table><tbody>c<tr>E", "cE", id="table-body-row"),
    pytest.param("<table><tbody><tr>c<td>E", "cE", id="table-body-row-cell"),
    pytest.param("<table><thead>c<tr>E", "cE", id="table-head-row"),
    pytest.param("<table><colgroup>c</colgroup>E", "cE", id="colgroup"),
    pytest.param("<table><caption>c</caption>E", "Ec", id="caption"),
    pytest.param("<table><tr><td>a</td><td>c</td>E", "Eac", id="two-cells"),
    pytest.param("<ul><li>c<li>E</ul>F", "cEF", id="list"),
    pytest.param("<select>a<option>b</option>E</select>c", "abEc", id="select"),
]


@pytest.mark.parametrize(("tail", "expected"), _TAILS)
@pytest.mark.parametrize("depth", _DEPTHS)
def test_content_past_the_depth_cap_keeps_source_order(tail: str, expected: str, depth: int) -> None:
    assert parse(f"{'<div>' * depth}{tail}").text == expected


@pytest.mark.parametrize(
    ("prefix", "tail", "expected"),
    [
        pytest.param("<p>a<b>b", "<table><tr>c<td>E", "abcE", id="formatting-table-row"),
        pytest.param("<p>a<b>b", "<table><caption>c</caption>E", "abEc", id="formatting-caption"),
        pytest.param("<p>a<b>b", "<table><tr><td>a</td><td>c</td>E", "abEac", id="formatting-two-cells"),
        pytest.param("<p>a<b>b", "<ul><li>c<li>E</ul>F", "abcEF", id="formatting-list"),
    ],
)
@pytest.mark.parametrize("depth", _DEPTHS)
def test_reconstructed_formatting_past_the_depth_cap_keeps_source_order(
    prefix: str, tail: str, expected: str, depth: int
) -> None:
    assert parse(f"{prefix}{'<div>' * depth}{tail}").text == expected


@pytest.mark.parametrize("depth", [pytest.param(_DEPTH_CAP - 3, id="below"), pytest.param(2000, id="far-past")])
def test_capped_run_keeps_each_node_after_the_nodes_before_it(depth: int) -> None:
    document = parse(f"{'<div>' * depth}<ul><li>c<li>d<li>e</ul>f")
    assert document.text == "cdef"
    assert len(document.find_all("li")) == 3


@pytest.mark.parametrize("depth", [pytest.param(2000, id="2000"), pytest.param(_DEPTH_CAP * 2, id="1024")])
def test_dom_depth_stays_within_the_cap(depth: int) -> None:
    assert _deepest(parse(f"{'<div>' * depth}<table><tr>c<td>E")) <= _DEPTH_CAP


def _deepest(document: Node) -> int:
    deepest = 0
    stack: list[tuple[Node, int]] = [(document, 0)]
    while stack:
        node, level = stack.pop()
        if isinstance(node, Element):
            deepest = max(deepest, level)
        stack.extend((child, level + 1) for child in getattr(node, "children", []) or [])
    return deepest


@pytest.mark.parametrize(
    ("markup", "text"),
    [
        # the adoption agency nests the formatting clone under a furthest block the cap kept at its last level
        pytest.param(f"{'<x>' * 509}<b><p>t</b>u", "tu", id="clone-under-capped-block"),
        pytest.param(f"{'<x>' * 509}<b>q<p>r<i>s</b>t</i>u", "qrstu", id="clone-over-capped-children"),
        # the formatting clones between the anchor and its block stack up above it, so nodes deeper in it pass the cap
        pytest.param(f"{'<x>' * 507}<b><i><s><p>t<span>u</span>v</b>w", "tuvw", id="clone-chain-507"),
        pytest.param(f"{'<x>' * 510}<b><i><s><p>t<span>u</span>v</b>w", "tuvw", id="clone-chain-510"),
        pytest.param(
            f"{'<x>' * 503}<b><i><s><p>t{'<span>' * 6}u{'</span>' * 6}v</b>w", "tuvw", id="clone-chain-past-a-leaf"
        ),
        # the agency drops the elements between the anchor and its furthest block from the stack, not the DOM
        pytest.param(f"<a>{'<x>' * 508}<h1><b><h1><li><div><div><button><li><div><p><a>t", "t", id="dropped-entries"),
        # a second anchor takes the first off the stack while it stays an ancestor
        pytest.param(f"<a>{'<x>' * 505}<h1><table><a></table><select><nobr><a>t", "t", id="removed-anchor"),
        # the selected option's copy nests deeper under a selectedcontent than the option does under its select
        pytest.param(
            f"{'<x>' * 506}<select><option>t<b>u<i>v</i></b>w</option><div><div><selectedcontent></select>",
            "tuvwtuvw",
            id="selectedcontent-copy",
        ),
        # each closed form leaves the stack from under its open div, one DOM level the stack no longer counts
        pytest.param(f"{'<form><div></form>' * 600}t", "t", id="removed-forms-shallow-stack"),
        pytest.param(f"{'<form><b></form>' * 600}<p>t", "t", id="removed-forms-formatting"),
    ],
)
def test_dom_depth_stays_within_the_cap_when_the_stack_undercounts(markup: str, text: str) -> None:
    document = parse(markup)
    assert _deepest(document) <= _DEPTH_CAP
    assert document.text == text


# a cell closed by its own end tag, or by a row or body start, leaves the b after the table in the text
_CELL_CLOSED_TEXT: Final[Mapping[tuple[str, str], str]] = MappingProxyType({
    ("<table><tr><td>", tag): "ba" for tag in ("</td>", "<tr>", "<tbody>")
})


@pytest.mark.parametrize(
    "tag",
    [
        pytest.param("</p>", id="end-p"),
        pytest.param("</li>", id="end-li"),
        pytest.param("</table>", id="end-table"),
        pytest.param("</td>", id="end-td"),
        pytest.param("</caption>", id="end-caption"),
        pytest.param("</select>", id="end-select"),
        pytest.param("</template>", id="end-template"),
        pytest.param("</form>", id="end-form"),
        pytest.param("</button>", id="end-button"),
        pytest.param("</h1>", id="end-heading"),
        pytest.param("</b>", id="end-formatting"),
        pytest.param("</frameset>", id="end-frameset"),
        pytest.param("</body>", id="end-body"),
        pytest.param("<p>", id="start-p"),
        pytest.param("<li>", id="start-li"),
        pytest.param("<button>", id="start-button"),
        pytest.param("<table>", id="start-table"),
        pytest.param("<select>", id="start-select"),
        pytest.param("<td>", id="start-td"),
        pytest.param("<caption>", id="start-caption"),
        pytest.param("<tr>", id="start-row"),
        pytest.param("<tbody>", id="start-tbody"),
        pytest.param("<option>", id="start-option"),
        pytest.param("<dd>", id="start-dd"),
        pytest.param("<body>", id="start-body"),
        pytest.param("<template>", id="start-template"),
        pytest.param("<h1>", id="start-heading"),
        pytest.param("<nobr>", id="start-nobr"),
    ],
)
@pytest.mark.parametrize(
    "context",
    [
        pytest.param("", id="body"),
        pytest.param("<table><tr><td>", id="cell"),
        pytest.param("<b><i>", id="formatting"),
        pytest.param("<form>", id="form"),
        pytest.param("<p>", id="paragraph"),
        pytest.param("<select>", id="select"),
    ],
)
def test_searches_past_the_depth_find_their_element(context: str, tag: str) -> None:
    assert parse(f"{context}{'<x-open>' * 600}a{tag}b").text == _CELL_CLOSED_TEXT.get((context, tag), "ab")


@pytest.mark.parametrize("tail", ["x</b>y", "<p>x</b>y</p>", "x</b>y<b>z</b>w"])
@pytest.mark.parametrize("depth", [pytest.param(_DEPTH_CAP * 2, id="1024"), pytest.param(2000, id="2000")])
def test_adoption_agency_past_the_depth_keeps_text_order(tail: str, depth: int) -> None:
    # the formatting element sits below the cap, so the agency moves the block above it and clones the element there
    assert parse(f"{'<div>' * depth}<b><div>{tail}").text == parse(f"<b><div>{tail}").text


@pytest.mark.parametrize(
    ("tail", "expected"),
    [
        pytest.param("<p>y<a>x", "yx", id="text-then-reopened-anchor"),
        pytest.param("<p><i>y</i><a>x", "yx", id="element-then-reopened-anchor"),
        pytest.param("<p><i><s>y<a>x", "yx", id="nested-refusals"),
    ],
)
@pytest.mark.parametrize("depth", [pytest.param(300, id="300"), *_DEPTHS])
def test_adoption_agency_lifting_a_capped_element_keeps_text_order(tail: str, expected: str, depth: int) -> None:
    # eight blocks run the agency's outer loop to its end, lifting the paragraph the cap refused back under the cap
    assert parse(f"<a>{'<x>' * depth}{'<div>' * 8}{tail}").text == expected


@pytest.mark.parametrize("depth", _DEPTHS)
def test_table_body_past_the_depth_finds_its_table_section(depth: int) -> None:
    # the table body and its row sit below the divs, so the table-scope searches must reach past the cap to find them
    assert parse(f"<table><tr><td>a</td>{'<div>' * depth}<table><thead>c<tr>E").text == "acE"


@pytest.mark.parametrize("depth", _DEPTHS)
def test_templates_count_toward_the_depth_cap(depth: int) -> None:
    assert _deepest(parse(f"{'<template>' * (depth // 2)}<table><tr>c<td>E")) <= _DEPTH_CAP


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param(
            f"{'<x>' * 506}<select><ruby><selectedcontent><table><col><option>t<thead><tr><td>u", id="table-section"
        ),
        pytest.param(f"<a>{'<x>' * 504}<ruby><select><mi><selectedcontent><mi><option>t<optgroup>u", id="foreign"),
        pytest.param(
            f"<b><a>{'<x>' * 500}<object><u><button><div><pre><select><selectedcontent><object><option>t</option><a>u",
            id="reconstructed",
        ),
    ],
)
def test_capped_element_the_option_copy_detaches_takes_appends(markup: str) -> None:
    # the option copy empties the selectedcontent, detaching the capped element still open below it
    assert not parse(markup).text
