from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Final

import pytest

from turbohtml import Element, parse
from turbohtml.query import compile  # ruff:ignore[builtin-import-shadowing]  # the soupsieve entry-point name

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from work_steps import WorkSteps


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


def _expected(*spans: range) -> list[str | None]:
    return [f"e{index}" for span in spans for index in span]


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


def _ids(nodes: Iterable[Element | None]) -> list[str | None]:
    return [None if node is None else node.attr("id") for node in nodes]


@pytest.mark.parametrize(
    ("selector", "html", "expected"),
    [
        pytest.param("a ~ b", "<a></a><div><b id=b1></b><b id=b2></b></div>", [], id="left-tag-elsewhere"),
        pytest.param(
            "em ~ b, a ~ b",
            "<div><a></a><b id=b1></b><b id=b2></b></div>",
            ["b1", "b2"],
            id="one-alternative-left-absent",
        ),
        pytest.param(
            "em ~ b, a ~ b.y",
            "<div><a></a><b class=y id=b1></b><b id=b2></b></div>",
            ["b1"],
            id="alternatives-keep-own-answers",
        ),
        pytest.param(
            ".x ~ .y",
            "<div><b class=y id=b1></b><b class='x y' id=b2></b><b class=y id=b3></b></div>",
            ["b3"],
            id="previous-candidate-is-left-match",
        ),
        pytest.param(
            "a ~ b",
            "<div><a></a><b id=o1><b id=i1></b><b id=i2></b></b><b id=o2></b></div>",
            ["o1", "o2"],
            id="nested-run-without-left",
        ),
        pytest.param(
            "a ~ b",
            "<div><b id=o1><a></a><b id=i1></b></b><b id=o2></b></div>",
            ["i1"],
            id="nested-run-with-left",
        ),
        pytest.param(
            "a ~ b c",
            "<div><b><c id=c1></c><c id=c2></c></b><a></a><b><c id=c3></c><c id=c4></c></b></div>",
            ["c3", "c4"],
            id="sibling-tested-per-descendant",
        ),
        pytest.param(
            "a ~ b ~ i",
            "<div><b></b><i id=i1></i><a></a><i id=i2></i><b></b><i id=i3></i><i id=i4></i></div>",
            ["i3", "i4"],
            id="chained-short-run",
        ),
        pytest.param(
            ":is(a ~ b) > i",
            "<div><b><i id=i1></i></b><a></a><b><i id=i2></i></b><b><i id=i3></i></b></div>",
            ["i2", "i3"],
            id="nested-selector",
        ),
    ],
)
def test_select_reuses_earlier_sibling_answer(selector: str, html: str, expected: list[str | None]) -> None:
    assert _ids(parse(html).select(selector)) == expected


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param("<div><b id=b1></b><a></a><b id=b2></b></div>", ["b2"], id="left-present"),
        pytest.param("<div><b id=b1></b><b id=b2></b></div>", [None], id="left-absent"),
    ],
)
def test_select_one_left_tag(html: str, expected: list[str | None]) -> None:
    assert _ids([parse(html).select_one("a ~ b")]) == expected


def test_matcher_filter_out_of_document_order() -> None:
    document = parse("<div><b class='x y' id=b1></b><b class=y id=b2></b></div>")
    later, earlier = document.select_one("#b2"), document.select_one("#b1")
    assert later is not None
    assert earlier is not None
    assert _ids(compile(".x ~ .y").filter([later, earlier])) == ["b2"]


def test_select_after_inserting_left_tag() -> None:
    document = parse("<div><b id=b1></b><b id=b2></b></div>")
    before = _ids(document.select("a ~ b"))
    first = document.select_one("#b1")
    assert first is not None
    first.insert_before(Element("a"))
    assert (before, _ids(document.select("a ~ b"))) == ([], ["b1", "b2"])


def test_select_descendant_of_general_sibling() -> None:
    document = parse(_siblings(("b", 40), ("a", 1), ("b", 40), filled=range(81)))
    assert _ids(document.select("a ~ b i")) == [f"c{index}" for index in range(41, 81)]


def test_select_sibling_runs_in_separate_parents() -> None:
    html = f"{_siblings(('a', 1), ('b', 50))}<section>{'<b></b>' * 50}</section>"
    assert _ids(parse(html).select("a ~ b")) == _expected(range(1, 51))


def test_select_sibling_runs_nested_in_each_sibling() -> None:
    # preorder leaves each run for its children's runs and comes back, so answers for many parents interleave
    run = f"<b>{'<i></i>' * 40}</b>"
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


def _element(html: str, element_id: str) -> Element:
    found = parse(html).select_one(f"#{element_id}")
    assert found is not None
    return found


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


_DEEP: Final[str] = f"{'<div>' * 300}<p id=leaf></p>"


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
        pytest.param("a ~ div p", f"<div><a></a><div>{'<div>' * 40}<p id=leaf></p>", id="general-sibling"),
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


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param(_siblings(("a", 1), ("b", 120)), ["e61", "e120", "e119"], id="cached-match"),
        pytest.param(_siblings(("b", 120)), [], id="cached-failure"),
    ],
)
def test_matcher_filter_long_runs_out_of_order(html: str, expected: list[str | None]) -> None:
    elements: Final = parse(html).select("b")
    assert _ids(compile("a ~ b").filter([elements[60], elements[119], elements[118]])) == expected


@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        pytest.param(".x ~ .y i", ["deep"], id="matched-ancestor"),
        pytest.param(".missing .x ~ .y i", [], id="absent-ancestor"),
    ],
)
def test_select_descendant_retries_ancestor_sibling(selector: str, expected: list[str | None]) -> None:
    document: Final = parse("<div><b class=x></b><b class=y><div class=y><i id=deep></i></div></b></div>")
    assert _ids(document.select(selector)) == expected


def test_select_sibling_alternatives_preserve_match() -> None:
    document: Final = parse("<section class=x></section><div><b></b><b></b><a class=x></a><b id=hit></b></div>")
    assert _ids(document.select(".x ~ b, .y ~ b")) == ["hit"]


def test_select_nth_sibling_skips_nonmatching_elements() -> None:
    document: Final = parse(
        "<div><i class=x id=a></i><i class=y></i><i class=x></i><i class=y></i>"
        "<i class=x id=c></i><i class=y></i><i class=x id=d></i></div>"
    )
    assert _ids(document.select("i:nth-child(odd of .x)")) == ["a", "c"]


_SIBLING_CACHE_CASES: Final[list[pytest.ParameterSet]] = [
    pytest.param("a ~ b", ["hit"], "hit", id="indexed"),
    pytest.param(".x ~ .y", ["hit", "tail"], "hit", id="classes"),
    pytest.param("a ~ b, a ~ p", ["hit", "tail"], "hit", id="different-subjects"),
    pytest.param("q ~ b", [], None, id="missing-known-left"),
    pytest.param("widget ~ b", [], None, id="unknown-left"),
]


@pytest.mark.parametrize(("selector", "expected", "first"), _SIBLING_CACHE_CASES)
def test_select_sibling_mixed_api_cache(selector: str, expected: list[str], first: str | None) -> None:
    document: Final = parse("<div><a class=x></a><b class=y id=hit></b><p class=y id=tail></p></div>")
    target: Final = document.select("#hit")[0]
    assert (
        target.matches(selector),
        _ids(document.select(selector)),
        _ids([document.select_one(selector)]),
        _ids([target.closest(selector)]),
    ) == (bool(expected), expected, [first], [first])


@pytest.mark.parametrize(("selector", "expected", "first"), _SIBLING_CACHE_CASES)
def test_select_sibling_mixed_api_cache_after_selection(selector: str, expected: list[str], first: str | None) -> None:
    document: Final = parse("<div><a class=x></a><b class=y id=hit></b><p class=y id=tail></p></div>")
    target: Final = document.select("#hit")[0]
    document.select(selector)
    assert (
        target.matches(selector),
        _ids(document.select(selector)),
        _ids([document.select_one(selector)]),
        _ids([target.closest(selector)]),
    ) == (bool(expected), expected, [first], [first])


def _typed(tags: Iterable[str], *, filled: bool = False) -> str:
    # past 32 siblings a position walk hands over to the position memo, which indexes the whole run; `filled` gives
    # each eN a child, so a query leaves the run between two siblings and the one-entry nth memo loses its place
    body = "".join(f"<{tag} id=e{index}>{'<i></i>' if filled else ''}</{tag}>" for index, tag in enumerate(tags))
    return f"<div id=p>{body}</div>"


_DISTINCT: Final[str] = _typed(f"x-{index}" for index in range(80))
_CYCLE: Final[str] = _typed(("a", "b", "x-a", "x-b", "x-long", "svg")[index % 6] for index in range(78))
_LONE: Final[str] = _typed(["a"] * 40 + ["x-lone"] + ["a"] * 40)
# each eN holds a child of the next sibling's type, the last candidate the one-entry nth memo saw before that sibling
_COUSINS: Final[str] = "<div id=p>{}</div>".format(
    "".join(f"<x-{index} id=e{index}><x-{index + 1}></x-{index + 1}></x-{index}>" for index in range(50))
)


@pytest.mark.parametrize(
    ("selector", "html", "expected"),
    [
        pytest.param(":first-of-type", _DISTINCT, _expected(range(80)), id="first-distinct"),
        pytest.param(":last-of-type", _DISTINCT, _expected(range(80)), id="last-distinct"),
        pytest.param(":only-of-type", _DISTINCT, _expected(range(80)), id="only-distinct"),
        pytest.param(":nth-of-type(1)", _DISTINCT, _expected(range(80)), id="nth-distinct"),
        pytest.param(":nth-last-of-type(2)", _DISTINCT, [], id="nth-last-distinct"),
        pytest.param(":first-of-type", _CYCLE, _expected(range(6)), id="first-cycle"),
        pytest.param(":last-of-type", _CYCLE, _expected(range(72, 78)), id="last-cycle"),
        pytest.param(":only-of-type", _CYCLE, [], id="only-cycle"),
        pytest.param(":nth-of-type(2)", _CYCLE, _expected(range(6, 12)), id="nth-cycle"),
        pytest.param(":nth-of-type(13)", _CYCLE, _expected(range(72, 78)), id="nth-cycle-end"),
        pytest.param(":nth-last-of-type(2)", _CYCLE, _expected(range(66, 72)), id="nth-last-cycle"),
        pytest.param(":nth-last-of-type(13)", _CYCLE, _expected(range(6)), id="nth-last-cycle-start"),
        pytest.param("x-long:nth-of-type(odd)", _CYCLE, _expected(range(4, 78, 12)), id="nth-cycle-typed"),
        pytest.param(":first-of-type", _LONE, ["e0", "e40"], id="first-lone"),
        pytest.param(":last-of-type", _LONE, ["e40", "e80"], id="last-lone"),
        pytest.param(":only-of-type", _LONE, ["e40"], id="only-lone"),
        pytest.param(":nth-last-of-type(41)", _LONE, ["e39"], id="nth-last-lone"),
        pytest.param(":nth-child(40)", _typed(["p"] * 80, filled=True), ["e39"], id="nth-child-filled"),
        pytest.param(":nth-last-child(40)", _typed(["p"] * 80, filled=True), ["e40"], id="nth-last-child-filled"),
        pytest.param(":nth-of-type(3)", _typed(["p", "b"] * 40, filled=True), ["e4", "e5"], id="nth-of-type-filled"),
        pytest.param(
            ":nth-of-type(1)",
            _COUSINS,
            _expected(range(50)),
            id="nth-memo-from-cousin",
        ),
        pytest.param(
            ".c:nth-child(odd)",
            _typed(["p"] * 90).replace("<p id=e0>", "<p id=e0 class=c>").replace("<p id=e44>", "<p id=e44 class=c>"),
            ["e0", "e44"],
            id="nth-memo-past-gap",
        ),
        pytest.param(":has(~ :nth-last-of-type(2))", _CYCLE, _expected(range(71)), id="inside-has"),
        pytest.param(":last-of-type ~ :last-of-type", _CYCLE, _expected(range(73, 78)), id="either-side-of-tilde"),
    ],
)
def test_select_type_position_in_long_run(selector: str, html: str, expected: list[str | None]) -> None:
    assert _ids(parse(html).select(f"#p > {selector}")) == expected


def test_select_type_position_text_between_siblings() -> None:
    document = parse(_typed(f"x-{index % 2}" for index in range(80)).replace("><x-", "> <!--gap--><x-"))
    assert [_ids(document.select(f"#p > {selector}")) for selector in (":first-of-type", ":nth-last-of-type(1)")] == [
        ["e0", "e1"],
        ["e78", "e79"],
    ]


def test_select_type_position_runs_nested_in_each_sibling() -> None:
    # every sibling of the outer run holds a run of its own, so the memo takes many sibling lists in one query
    inner = "".join(f"<y-{index}></y-{index}>" for index in range(40))
    body = "".join(f"<x-{index}>{inner}</x-{index}>" for index in range(40))
    document = parse(f"<div id=p>{body}</div>")
    counts = [len(document.select(selector)) for selector in ("#p :only-of-type", "#p :nth-of-type(2)", "y-39")]
    assert counts == [1640, 0, 40]


@pytest.mark.parametrize(
    "selector",
    [pytest.param(":first-of-type", id="first-of-type"), pytest.param(":nth-last-of-type(2)", id="nth-last-of-type")],
)
def test_select_type_position_work_linear(selector: str, work_steps: WorkSteps) -> None:
    work_steps.assert_linear(
        lambda size: partial(parse(_typed(f"x-{index}" for index in range(size))).select, f"#p > {selector}"),
        (1000, 4000),
    )


def test_select_one_type_position_in_long_run() -> None:
    assert _ids([parse(_CYCLE).select_one("#p > :nth-last-of-type(2)")]) == ["e66"]


def test_remove_type_position_in_long_run() -> None:
    document = parse(_CYCLE)
    document.remove("#p > :last-of-type")
    assert _ids(document.select("#p > *")) == _expected(range(72))


def test_prune_type_position_in_long_run() -> None:
    document = parse(_CYCLE)
    document.prune("#p > :first-of-type")
    assert _ids(document.select("#p > *")) == _expected(range(6))


@pytest.mark.parametrize(
    "order",
    [pytest.param(slice(None), id="forward"), pytest.param(slice(None, None, -1), id="reverse")],
)
@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        pytest.param(":last-of-type", _expected(range(72, 78)), id="last-of-type"),
        pytest.param(":nth-of-type(2)", _expected(range(6, 12)), id="nth-of-type"),
        pytest.param(":nth-last-child(3)", ["e75"], id="nth-last-child"),
    ],
)
def test_matcher_filter_type_position_in_long_run(selector: str, expected: list[str | None], order: slice) -> None:
    candidates = parse(_CYCLE).select("#p > *")[order]
    assert set(_ids(compile(selector).filter(candidates))) == set(expected)


@pytest.mark.parametrize(
    ("html", "element_id", "selector", "expected"),
    [
        pytest.param(_DISTINCT, "e79", ":first-of-type", True, id="first-distinct"),
        pytest.param(_DISTINCT, "e0", ":only-of-type", True, id="only-distinct"),
        pytest.param(_LONE, "e80", ":first-of-type", False, id="first-after-lone"),
        pytest.param(_CYCLE, "e77", ":nth-of-type(13)", True, id="nth"),
        pytest.param(_CYCLE, "e0", ":nth-last-of-type(12)", False, id="nth-last"),
    ],
)
def test_matches_type_position_in_long_run(html: str, element_id: str, selector: str, *, expected: bool) -> None:
    assert _element(html, element_id).matches(selector) is expected


def test_select_type_position_after_rename() -> None:
    document = parse(_LONE)
    before = _ids(document.select("#p > :only-of-type"))
    first = document.select_one("#e0")
    assert first is not None
    first.tag = "x-lone"
    assert (before, _ids(document.select("#p > :only-of-type"))) == (["e40"], [])


def _wrapped(opening: str, closing: str, depth: int, innermost: str) -> str:
    return opening * depth + innermost + closing * depth


_AROUND_A: Final[str] = _siblings(("b", 40), ("a", 1), ("b", 40))
_HOLDER: Final[str] = _siblings(("b", 81), filled=(40,))


@pytest.mark.parametrize(
    ("selector", "html", "expected"),
    [
        pytest.param(f"b:has({'~ b ' * 12}~ a)", _AROUND_A, _expected(range(28)), id="chain"),
        pytest.param(f"b:has({'~ b ' * 12}~ q)", _AROUND_A, [], id="chain-missing"),
        pytest.param(f"b:has(+ b {'~ b ' * 6}~ a)", _AROUND_A, _expected(range(33)), id="adjacent-lead-chain"),
        pytest.param("b:has(~ b ~ b i)", _HOLDER, _expected(range(39)), id="chain-descendant-tail"),
        pytest.param("b:has(~ b ~ b ~ b i)", _HOLDER, _expected(range(38)), id="chain-descendant-tail-deeper"),
        pytest.param("b:has(~ b:has(~ b ~ a))", _AROUND_A, _expected(range(38)), id="nested-chain"),
        pytest.param(_wrapped("b:has(~ ", ")", 8, "a"), _AROUND_A, _expected(range(33)), id="nested"),
        pytest.param(_wrapped("b:has(~ ", ")", 8, "q"), _AROUND_A, [], id="nested-missing"),
        pytest.param(_wrapped("b:has(~ :is(", "))", 6, "a"), _AROUND_A, _expected(range(35)), id="nested-in-is"),
        pytest.param(f"div:has({_wrapped('b:has(~ ', ')', 6, 'a')})", _AROUND_A, ["p"], id="descendant-led-nested"),
        pytest.param(
            f"div:has({_wrapped('b:has(~ ', ')', 3, 'q')})", _AROUND_A, [], id="descendant-led-nested-missing"
        ),
    ],
)
def test_select_chained_has_siblings(selector: str, html: str, expected: list[str | None]) -> None:
    document = parse(html)
    elements = document.select("[id]")
    assert _ids(document.select(selector)) == expected
    assert _ids([element for element in elements if element.matches(selector)]) == expected


@pytest.mark.parametrize(
    ("build", "sizes"),
    [
        pytest.param(lambda links: f"b:has({'~ b ' * links}~ q)", (2, 6), id="chain"),
        pytest.param(lambda links: f"b:has(+ b {'~ b ' * links}~ q)", (2, 6), id="adjacent-lead"),
        pytest.param(lambda links: f"b:has({'~ b ' * links}~ b q)", (2, 6), id="descendant-tail"),
        pytest.param(lambda depth: _wrapped("b:has(~ ", ")", depth, "q"), (2, 5), id="nested"),
    ],
)
def test_select_chained_has_siblings_work_linear(
    build: Callable[[int], str], sizes: tuple[int, int], work_steps: WorkSteps
) -> None:
    document = parse(_AROUND_A)
    work_steps.assert_linear(lambda size: partial(document.select, build(size)), sizes)


def test_matches_nested_has_siblings_work_linear(work_steps: WorkSteps) -> None:
    anchor: Final = parse(_AROUND_A).select_one("#e0")
    assert anchor is not None
    work_steps.assert_linear(lambda depth: partial(anchor.matches, _wrapped("b:has(~ ", ")", depth, "q")), (2, 5))
