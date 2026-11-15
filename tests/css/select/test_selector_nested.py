from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

import pytest

from turbohtml import Document, Element, parse, parse_xml
from turbohtml.cssom import computed_style
from turbohtml.query import compile  # ruff:ignore[builtin-import-shadowing]  # the soupsieve entry-point name
from turbohtml.rewrite import rewrite

if TYPE_CHECKING:
    from collections.abc import Callable

    from work_steps import WorkSteps


def _wrap(template: str, leaf: str, levels: int) -> str:
    selector = leaf
    for _ in range(levels):
        selector = template.format(selector)
    return selector


def _spine(depth: int) -> Document:
    return parse(f"<body>{'<section>' * depth}<em>z</em>")


def _row(count: int) -> Document:
    return parse(f"<body><ol>{'<li>' * count}</ol>")


def _last(document: Document, tag: str) -> Element:
    found = document.select(tag)[-1]
    assert isinstance(found, Element)
    return found


def _first(document: Document, tag: str) -> Element:
    found = document.select_one(tag)
    assert found is not None
    return found


def _styled(rule: str, depth: int) -> Element:
    return _first(parse(f"<style>{rule}{{color:red}}</style>{'<section>' * depth}<em>z</em>"), "em")


def _scoped_filter(depth: int) -> list[Element]:
    return compile(_wrap(":where({} *, em:scope)", "q", 3)).filter(_spine(depth).select("section, em"))


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(lambda depth: partial(_spine(depth).select, _wrap(":is({} *)", "q", 3)), id="is-ancestor-select"),
        pytest.param(
            lambda depth: partial(_last(_spine(depth), "em").closest, _wrap(":where({} section)", "q", 3)),
            id="where-ancestor-closest",
        ),
        pytest.param(
            lambda depth: partial(_spine(depth).select, _wrap(":not(:not({} *))", "q", 3)), id="double-not-select"
        ),
        pytest.param(
            lambda depth: partial(computed_style, _styled(_wrap(":is({} *)", "q", 3), depth)), id="is-ancestor-style"
        ),
        pytest.param(
            lambda depth: partial(computed_style, _styled(_wrap(":is({} *, q:scope)", "q", 3), depth)),
            id="scope-list-style",
        ),
        pytest.param(lambda depth: partial(_scoped_filter, depth), id="scope-list-filter"),
        pytest.param(
            lambda depth: partial(_spine(depth).select, _wrap("section:has({})", "q", 4)), id="has-in-has-select"
        ),
        pytest.param(
            lambda depth: partial(_first(_spine(depth), "section").matches, _wrap("section:has({})", "q", 4)),
            id="has-in-has-matches",
        ),
        pytest.param(
            lambda depth: partial(_spine(depth).select, _wrap("section:has(:scope {})", "q", 4)),
            id="scope-has-in-has-select",
        ),
    ],
)
def test_nested_list_on_a_spine_work_linear(case: Callable[[int], Callable[[], object]], work_steps: WorkSteps) -> None:
    work_steps.assert_linear(case, (120, 480))


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            lambda count: partial(_last(_row(count), "li").matches, _wrap(":where({} ~ li)", "q", 3)),
            id="where-sibling-matches",
        ),
        pytest.param(
            lambda count: partial(_row(count).select, _wrap(":nth-last-child(n of {})", "li", 4)),
            id="nth-of-in-nth-of-select",
        ),
        pytest.param(
            lambda count: partial(_first(_row(count), "li").matches, _wrap(":nth-last-child(n of {})", "li", 4)),
            id="nth-of-in-nth-of-matches",
        ),
        pytest.param(
            lambda count: partial(_last(_row(count), "li").matches, _wrap(":nth-child(2n of {})", "li", 4)),
            id="nth-of-in-nth-of-matches-last",
        ),
        pytest.param(
            lambda count: partial(_row(count).select, ":nth-child(n of :nth-last-of-type(n):nth-of-type(n))"),
            id="positions-in-nth-of-select",
        ),
        pytest.param(
            lambda count: partial(
                _last(_row(count), "li").matches, ":nth-child(n of :nth-last-of-type(n):nth-of-type(n))"
            ),
            id="positions-in-nth-of-matches",
        ),
        pytest.param(
            lambda count: partial(parse(f"<body>{'<ol>' * 6}{'<li>' * count}").select, "ol:has(q) li"),
            id="has-left-of-combinator",
        ),
        pytest.param(
            lambda count: partial(_row(count).select, f"body:has({_wrap('li:has(~ {})', 'q', 6)})"),
            id="sibling-has-in-descendant-has",
        ),
    ],
)
def test_nested_list_on_a_row_work_linear(case: Callable[[int], Callable[[], object]], work_steps: WorkSteps) -> None:
    work_steps.assert_linear(case, (600, 2400))


def test_sibling_has_levels_in_descendant_has_work_linear(work_steps: WorkSteps) -> None:
    document = _row(300)
    # the step count shifts by a quarter with the process's heap layout over a three-fold level gap, so the exponent
    # gate has room; a per-level square still measures 2
    work_steps.assert_linear(
        lambda levels: partial(document.select, f"body:has({_wrap('li:has(~ {})', 'q', levels)})"),
        (3, 9),
        exponent=1.5,
    )


def test_ancestor_rules_work_linear_in_depth(work_steps: WorkSteps) -> None:
    work_steps.assert_linear(lambda depth: _cascade_of_ancestor_rules(3000, depth), (100, 400))


def _cascade_of_ancestor_rules(rules: int, depth: int) -> Callable[[], None]:
    document = parse(f"<style>{':is(q *) *{color:red}' * rules}</style><body>{'<section>' * depth}<em>z</em>")
    return partial(_style_all, document.select("section, em"))


def _style_all(elements: list[Element]) -> None:
    for element in elements:
        computed_style(element)


def test_ancestor_rules_work_linear_in_rule_count(work_steps: WorkSteps) -> None:
    work_steps.assert_linear(lambda rules: _cascade_of_ancestor_rules(rules, 400), (1000, 3000))


def test_rules_of_nested_lists_cost_like_flat_rules(work_steps: WorkSteps) -> None:
    def style(rule: str) -> Callable[[], object]:
        return partial(computed_style, _styled(f"{rule}{{color:red}}" * 150 + rule, 300))

    work_steps.assert_within(style(":is(q *) *"), style("q * *"), factor=4)


@pytest.mark.parametrize(
    ("nested", "flat"),
    [
        pytest.param(":is(section em) b", "section em b", id="is-left-of-combinator"),
        pytest.param(":is(:is(section em) *) *", ":is(section em * *)", id="is-ancestors"),
        pytest.param(":where(:not(:not(section > em)) *) *", ":is(section > em * *)", id="not-not"),
        pytest.param(":is(:is(li ~ li) ~ li) + li", ":is(li ~ li ~ li + li)", id="siblings"),
        pytest.param("section:has(section:has(em))", "section:has(section em)", id="has-in-has"),
        pytest.param("section:has(:scope > section:has(> em))", "section:has(> section > em)", id="scope-has"),
        pytest.param("ol:has(li:has(~ li:has(~ li:has(+ li))))", "ol:has(li ~ li ~ li + li)", id="sibling-has-in-has"),
        pytest.param(":is(section:has(em) section) b", ":is(section section) b", id="has-left-of-combinator"),
        pytest.param(":is(li:has(~ li:has(~ li)))", "li:has(~ li ~ li)", id="sibling-chain-in-is"),
        pytest.param(":is(li:has(+ li:has(+ li)))", "li:has(+ li + li)", id="adjacent-chain-in-is"),
        pytest.param("li:has(~ :is(li:has(~ li)))", "li:has(~ li:has(~ li))", id="sibling-chain-under-is"),
        pytest.param("li:has(~ li:has(b))", "li:has(~ li b)", id="descendant-has-in-sibling-chain"),
        pytest.param(":nth-child(1 of li:nth-child(2))", "li:nth-child(2)", id="plain-position-in-of"),
        pytest.param("li:nth-child(2):is(ol li)", "ol li:nth-child(2)", id="plain-position-beside-a-combinator-list"),
        pytest.param("ol:has(li:has(+ li:has(+ li)))", "ol:has(li + li + li)", id="adjacent-chain-in-has"),
        pytest.param(":nth-child(2n+1 of :nth-child(odd of li))", "li:nth-child(4n+1)", id="nth-of-nth-of"),
        pytest.param(":nth-last-child(2 of :nth-last-child(-n+5 of li))", "li:nth-last-child(2)", id="nth-last-of"),
        pytest.param(":nth-child(3 of :nth-of-type(n):nth-last-of-type(n))", "li:nth-child(3)", id="positions-in-of"),
    ],
)
@pytest.mark.parametrize(
    "document",
    [
        pytest.param(lambda: parse(f"<body><section><section><em><b></b></em><ol>{'<li>' * 9}"), id="shallow-html"),
        pytest.param(lambda: parse(f"<body>{'<section>' * 40}<em><b></b></em><ol>{'<li>' * 50}"), id="html"),
        pytest.param(
            lambda: parse_xml(f"<r>{'<section>' * 600}<em><b/></em><ol>{'<li/>' * 70}</ol>{'</section>' * 600}</r>"),
            id="deep-xml",
        ),
    ],
)
def test_nested_list_answers_as_its_flat_spelling(nested: str, flat: str, document: Callable[[], Document]) -> None:
    built = document()
    elements = built.select("*")
    assert built.select(nested) == built.select(flat)
    assert [element.matches(nested) for element in elements] == [element.matches(flat) for element in elements]
    assert [element.closest(nested) for element in elements[::7]] == [
        element.closest(flat) for element in elements[::7]
    ]
    assert compile(nested).filter(elements[::-1]) == compile(flat).filter(elements[::-1])


def test_scope_list_answers_per_scope() -> None:
    elements = parse(f"<body>{'<section>' * 40}<em></em>").select("section, em")
    selector = ":is(:is(section:scope, q) section, q) em"
    assert compile(selector).filter(elements) == [element for element in elements if element.matches(selector)]
    assert [element.matches(selector) for element in elements] == [False] * 40 + [False]
    assert [len(element.select(selector)) for element in elements[:3]] == [1, 1, 1]


def test_scope_list_inside_has_keeps_the_outer_answers() -> None:
    outer = _first(parse(f"<body>{'<section>' * 40}<em></em>"), "section")
    selector = ":is(:is(:scope, q) section, q) section:has(> :is(:is(:scope, q) section, q))"
    assert len(outer.select(selector)) == len(outer.select(":scope section section:has(> section)"))


def test_nested_rewrite_rule() -> None:
    seen: list[str] = []
    rewrite(
        f"{'<div>' * 50}<p>x</p>",
        elements=[(f"{_wrap(':is({} div)', 'div', 3)} p", lambda element: seen.append(element.tag))],
    )
    assert seen == ["p"]


def test_full_answer_table_starts_over() -> None:
    document = parse_xml(f"<r>{'<s>' * 4200}<t/>{'</s>' * 4200}</r>")
    selector = _wrap(":is({} *)", "q", 126)
    assert document.select(selector) == []
    assert _first(document, "t").matches(_wrap(":is({} *)", "s", 126))


def test_scope_read_after_an_element_is_answered_per_scope() -> None:
    items = parse(f"<body><ol><li class=z></li><li class=z></li>{'<li></li>' * 40}</ol>").select("li")
    selector = ":is(:nth-last-child(3 of :scope, .z), q) ~ li"
    assert compile(selector).filter(items) == items[2:]
    assert [item.matches(selector) for item in items] == [False, False] + [True] * 40


def test_of_clause_with_scope_counts_apart_for_each_has_anchor() -> None:
    document = parse("<div><b><a></a></b></div><div><b></b></div><div><b><a></a></b><b><a></a></b></div>")
    divs = document.select("div")
    selector = "div:has(:nth-last-child(1 of :scope a))"
    assert document.select(selector) == [divs[0], divs[2]]
    assert [div.matches(selector) for div in divs] == [True, False, True]


def test_scope_list_inside_has_walks_past_the_memo_run() -> None:
    document = parse_xml(f"<r>{'<b>' * 90}{'</b>' * 90}</r>")
    assert document.select("b:has(:is(:scope * *, q))") == document.select("b:has(* *)")


@pytest.mark.parametrize(
    ("nested", "flat"),
    [
        pytest.param(":is(:scope section, q) *", ":scope section *", id="matched-at-the-parent"),
        pytest.param(":is(:scope section q, q) *", ":scope section q *", id="asked-at-every-ancestor"),
    ],
)
def test_scope_list_asked_again_under_one_scope(nested: str, flat: str) -> None:
    outer = _first(_spine(60), "section")
    assert outer.select(nested) == outer.select(flat)


@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        pytest.param("r :nth-child(2 of li)", ["1"], id="of-clause-in-order"),
        pytest.param("r :nth-last-child(1 of li)", ["9"], id="of-clause-from-end"),
        pytest.param("r :nth-child(odd of i)", [], id="of-clause-no-match"),
    ],
)
def test_plain_of_clause_on_a_deep_tree(selector: str, expected: list[str]) -> None:
    items_xml = "".join(f"<li n='{n}'/>" for n in range(10))
    document = parse_xml(f"<r>{'<s>' * 600}{items_xml}{'</s>' * 600}</r>")
    items = document.select("li")
    assert [found.attrs["n"] for found in document.select(selector)] == expected
    assert [item.attrs["n"] for item in items if item.matches(selector)] == expected


def test_answer_table_of_many_alternatives_starts_over() -> None:
    document = parse_xml(f"<r>{'<s>' * 4200}{'</s>' * 4200}</r>")
    alternatives = ", ".join(f"q{index} *" for index in range(128))
    assert len(document.select(f":not(:is({alternatives})) *")) == 4200
