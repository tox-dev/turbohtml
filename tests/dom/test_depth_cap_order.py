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
    document = parse(f"{'<div>' * depth}<table><tr>c<td>E")
    deepest = 0
    stack: list[tuple[Node, int]] = [(document, 0)]
    while stack:
        node, level = stack.pop()
        if isinstance(node, Element):
            deepest = max(deepest, level)
        stack.extend((child, level + 1) for child in getattr(node, "children", []) or [])
    assert deepest <= _DEPTH_CAP


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


@pytest.mark.parametrize("depth", _DEPTHS)
def test_table_body_past_the_depth_finds_its_table_section(depth: int) -> None:
    # the table body and its row sit below the divs, so the table-scope searches must reach past the cap to find them
    assert parse(f"<table><tr><td>a</td>{'<div>' * depth}<table><thead>c<tr>E").text == "acE"


@pytest.mark.parametrize("depth", _DEPTHS)
def test_templates_count_toward_the_depth_cap(depth: int) -> None:
    document = parse(f"{'<template>' * (depth // 2)}<table><tr>c<td>E")
    deepest = 0
    stack: list[tuple[Node, int]] = [(document, 0)]
    while stack:
        node, level = stack.pop()
        if isinstance(node, Element):
            deepest = max(deepest, level)
        stack.extend((child, level + 1) for child in getattr(node, "children", []) or [])
    assert deepest <= _DEPTH_CAP
