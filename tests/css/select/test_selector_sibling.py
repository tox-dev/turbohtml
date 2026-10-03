"""Sibling and descendant combinator walks over long sibling runs and deep chains: exact results on every API."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from turbohtml import Element, parse
from turbohtml.query import compile  # ruff:ignore[builtin-import-shadowing]  # the soupsieve entry-point name

if TYPE_CHECKING:
    from collections.abc import Iterable


def _siblings(*runs: tuple[str, int], filled: Iterable[int] = ()) -> str:
    # runs past 32 siblings reach the sibling memo, and past 48 stored answers the memo grows; the element eN at a
    # `filled` position holds an <i id=cN>
    tags = [tag for tag, count in runs for _ in range(count)]
    holders = set(filled)
    body = "".join(
        f"<{tag} id=e{index}>{f'<i id=c{index}></i>' if index in holders else ''}</{tag}>"
        for index, tag in enumerate(tags)
    )
    return f"<div id=p>{body}</div>"


def _ids(nodes: Iterable[Element | None]) -> list[str | None]:
    return [None if node is None else node.attr("id") for node in nodes]


def _expected(*spans: range) -> list[str | None]:
    return [f"e{index}" for span in spans for index in span]


def _element(html: str, element_id: str) -> Element:
    found = parse(html).select_one(f"#{element_id}")
    assert found is not None
    return found


@pytest.mark.parametrize(
    ("selector", "html", "expected"),
    [
        pytest.param("a ~ b", _siblings(("b", 120)), [], id="no-left-match"),
        pytest.param("a ~ b", _siblings(("a", 1), ("b", 120)), _expected(range(1, 121)), id="left-match-first"),
        pytest.param(
            "a ~ b", _siblings(("b", 60), ("a", 1), ("b", 60)), _expected(range(61, 121)), id="left-match-middle"
        ),
        pytest.param(
            "a ~ b",
            _siblings(("b", 40), ("a", 1), ("b", 40), ("a", 1), ("b", 40)),
            _expected(range(41, 81), range(82, 122)),
            id="interleaved-left-matches",
        ),
        pytest.param("x a ~ b", _siblings(("a", 1), ("b", 120)), [], id="left-fails-at-every-ancestor"),
        pytest.param("div a ~ b", _siblings(("a", 1), ("b", 120)), _expected(range(1, 121)), id="left-descendant"),
        pytest.param("[id=e60] ~ b", _siblings(("b", 120)), _expected(range(61, 120)), id="left-without-type"),
        pytest.param(
            "a ~ b ~ i",
            _siblings(("b", 50), ("a", 1), ("i", 30), ("b", 1), ("i", 50)),
            _expected(range(82, 132)),
            id="chained-general-siblings",
        ),
        pytest.param(
            "a + b ~ i",
            _siblings(("b", 1), ("i", 40), ("a", 1), ("b", 1), ("i", 40)),
            _expected(range(43, 83)),
            id="adjacent-then-general",
        ),
        pytest.param("p > b ~ i", _siblings(("b", 1), ("i", 60)), [], id="parent-mismatch"),
        pytest.param("div > b ~ i", _siblings(("i", 40), ("b", 1), ("i", 40)), _expected(range(41, 81)), id="child"),
        pytest.param(
            "b:has(~ a)", _siblings(("b", 60), ("a", 1), ("b", 60)), _expected(range(60)), id="has-later-sibling"
        ),
        pytest.param(":has(~ a)", _siblings(("b", 120)), [], id="has-later-sibling-none"),
        pytest.param(
            "b:has(~ [id=e100])", _siblings(("b", 120)), _expected(range(100)), id="has-later-sibling-without-type"
        ),
        pytest.param(
            "b:has(~ b i)",
            _siblings(("b", 81), filled=(40,)),
            _expected(range(40)),
            id="has-later-sibling-with-descendant",
        ),
        pytest.param(
            "b:has(+ b i)", _siblings(("b", 81), filled=(40,)), ["e39"], id="has-next-sibling-with-descendant"
        ),
        pytest.param(
            "b:has(~ b ~ u)",
            _siblings(("b", 40), ("u", 1), ("b", 40)),
            _expected(range(39)),
            id="has-chained-general-siblings",
        ),
        pytest.param(
            "b:has(+ :not(:scope) i)",
            _siblings(("b", 81), filled=(40,)),
            ["e39"],
            id="has-scope-keeps-direct-walk",
        ),
        pytest.param("b:has(~ :scope)", _siblings(("b", 60)), [], id="has-later-scope-never-matches"),
    ],
)
def test_select_sibling_runs(selector: str, html: str, expected: list[str | None]) -> None:
    assert _ids(parse(html).select(selector)) == expected


def test_select_descendant_of_general_sibling() -> None:
    document = parse(_siblings(("b", 40), ("a", 1), ("b", 40), filled=range(81)))
    assert _ids(document.select("a ~ b i")) == [f"c{index}" for index in range(41, 81)]


def test_select_sibling_runs_in_separate_parents() -> None:
    html = f"{_siblings(('a', 1), ('b', 50))}<section>{'<b></b>' * 50}</section>"
    assert _ids(parse(html).select("a ~ b")) == _expected(range(1, 51))


def test_select_sibling_runs_nested_in_each_sibling() -> None:
    # preorder leaves each run for its children's runs and comes back, so answers for many parents interleave
    run = "<b>" + "<i></i>" * 40 + "</b>"
    document = parse(f"<div>{run * 40}<a></a>{run * 40}</div>")
    assert [len(document.select(selector)) for selector in ("a ~ b > i", "b > i:first-child ~ i")] == [1600, 3120]


def test_select_one_after_long_run() -> None:
    assert _ids([parse(_siblings(("b", 60), ("a", 1), ("b", 60))).select_one("a ~ b")]) == ["e61"]


def test_remove_after_long_run() -> None:
    document = parse(_siblings(("b", 60), ("a", 1), ("b", 60)))
    document.remove("a ~ b")
    assert _ids(document.select("b")) == _expected(range(60))


def test_prune_after_long_run() -> None:
    document = parse(_siblings(("b", 60), ("a", 1), ("b", 60)))
    document.prune("a ~ b")
    assert _ids(document.select("b")) == _expected(range(61, 121))


def test_scope_select_from_container() -> None:
    container = _element(_siblings(("b", 40), ("a", 1), ("b", 40)), "p")
    assert _ids(container.select(":scope > a ~ b")) == _expected(range(41, 81))


@pytest.mark.parametrize(
    "selector",
    [
        pytest.param("a ~ b", id="memoized"),
        pytest.param(":scope ~ b, a ~ b", id="scope-per-candidate"),
    ],
)
def test_matcher_filter(selector: str) -> None:
    elements = parse(_siblings(("b", 40), ("a", 1), ("b", 40))).select("b, a")
    assert _ids(compile(selector).filter(elements)) == _expected(range(41, 81))


def test_matcher_reused_across_documents() -> None:
    matcher = compile("a ~ b")
    with_left = parse(_siblings(("a", 1), ("b", 60)))
    without_left = parse(_siblings(("b", 60)))
    results = [
        len(matcher.select(with_left)),
        len(matcher.select(without_left)),
        len(matcher.filter(without_left.select("b"))),
        len(matcher.filter(with_left.select("b"))),
    ]
    assert results == [60, 0, 0, 60]


@pytest.mark.parametrize(
    ("element_id", "selector", "expected"),
    [
        pytest.param("e120", "a ~ b ~ b", False, id="chained-no-left"),
        pytest.param("e120", "b ~ b ~ b", True, id="chained-left"),
        pytest.param("e120", "x b ~ b", False, id="left-fails-at-every-ancestor"),
        pytest.param("e0", "b:has(~ b ~ [id=e120])", True, id="has-later-sibling-single-test"),
        pytest.param("e120", "b:has(~ b)", False, id="has-later-sibling-last"),
    ],
)
def test_matches_long_run(element_id: str, selector: str, *, expected: bool) -> None:
    assert _element(_siblings(("b", 121)), element_id).matches(selector) is expected


@pytest.mark.parametrize(
    ("selector", "html", "expected"),
    [
        # a single test walks the later siblings directly, past text, into each subtree
        pytest.param(":has(~ b i)", "<div><em id=anchor></em>text<b><i></i></b></div>", True, id="descendant"),
        pytest.param(":has(~ b i)", "<div><em id=anchor><u></u><b><i></i></b></em></div>", False, id="inside-anchor"),
        pytest.param(":has(~ b ~ i)", "<div><em id=anchor></em><b></b><i></i></div>", True, id="later-sibling"),
        pytest.param(":has(~ b ~ i)", "<div><em id=anchor></em><i></i><b></b></div>", False, id="out-of-order"),
        pytest.param(":has(~ b ~ i)", "<div><b></b><em id=anchor></em><i></i></div>", False, id="left-before-anchor"),
    ],
)
def test_matches_has_later_sibling_chain(selector: str, html: str, *, expected: bool) -> None:
    assert _element(html, "anchor").matches(selector) is expected


def test_closest_long_run() -> None:
    inner = parse(_siblings(("a", 1), ("b", 60), filled=range(61))).select("i")[-1]
    assert _ids([inner.closest("a ~ b")]) == ["e60"]


_DEEP = "<div>" * 300 + "<p id=leaf></p>"


@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        pytest.param("a div div p", [], id="typed-ancestor-missing"),
        pytest.param("[data-q] div div p", [], id="untyped-ancestor-missing"),
        pytest.param("body div div div p", ["leaf"], id="typed-ancestors-present"),
        pytest.param("[id] ~ div div p", [], id="sibling-of-ancestor-missing"),
    ],
)
def test_select_deep_descendant_chain(selector: str, expected: list[str | None]) -> None:
    assert _ids(parse(_DEEP).select(selector)) == expected


@pytest.mark.parametrize(
    ("selector", "html"),
    [
        # the nearest ancestor fails on its siblings, a higher one does not
        pytest.param("a ~ div p", "<div><a></a><div>" + "<div>" * 40 + "<p id=leaf></p>", id="general-sibling"),
        pytest.param(
            "section > b ~ span em",
            "<section><b></b><span><div><b></b><span><em id=leaf></em></span></div></span></section>",
            id="child-then-general-sibling",
        ),
        pytest.param("a + b span", "<div><a></a><b><div><b><span id=leaf></span></b></div></b></div>", id="adjacent"),
    ],
)
def test_select_descendant_walks_past_sibling_failure(selector: str, html: str) -> None:
    assert _ids(parse(html).select(selector)) == ["leaf"]


def test_matches_deep_descendant_chain() -> None:
    assert _element(_DEEP, "leaf").matches("a div div div div p") is False
