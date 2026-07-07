"""``turbohtml.convert.css_specificity``: the (a, b, c) weight of each selector in a list, per CSS Selectors 4 §17."""

from __future__ import annotations

import pytest

from turbohtml.convert import css_specificity
from turbohtml.query import SelectorSyntaxError


@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        pytest.param("*", [(0, 0, 0)], id="universal-zero"),
        pytest.param("li", [(0, 0, 1)], id="type"),
        pytest.param("ul li", [(0, 0, 2)], id="descendant-two-types"),
        pytest.param("ul li a", [(0, 0, 3)], id="descendant-three-types"),
        pytest.param("#id", [(1, 0, 0)], id="id"),
        pytest.param(".c", [(0, 1, 0)], id="class"),
        pytest.param("a[href]", [(0, 1, 1)], id="type-and-attribute"),
        pytest.param("a.b#c", [(1, 1, 1)], id="type-class-id-compound"),
        pytest.param("div.x.y", [(0, 2, 1)], id="two-classes"),
        pytest.param("p > a.link", [(0, 1, 2)], id="child-combinator"),
        pytest.param("a:hover", [(0, 1, 1)], id="pseudo-class-counts-b"),
        pytest.param("li:nth-child(2)", [(0, 1, 1)], id="nth-child-counts-b"),
        pytest.param(":where(#x)", [(0, 0, 0)], id="where-is-zero"),
        pytest.param(":is(#a, .b)", [(1, 0, 0)], id="is-takes-most-specific-id"),
        pytest.param(":is(.a, .b.c)", [(0, 2, 0)], id="is-takes-most-specific-class-count"),
        pytest.param(":is(.x, .x y)", [(0, 1, 1)], id="is-tiebreak-on-c"),
        pytest.param(":is(li, div)", [(0, 0, 1)], id="is-equal-alternatives"),
        pytest.param(":not(.a.b)", [(0, 2, 0)], id="not-takes-argument"),
        pytest.param(":has(.x)", [(0, 1, 0)], id="has-takes-argument"),
        pytest.param("h1, .foo, #bar", [(0, 0, 1), (0, 1, 0), (1, 0, 0)], id="comma-list-one-triple-each"),
        pytest.param("*, li", [(0, 0, 0), (0, 0, 1)], id="universal-then-type"),
    ],
)
def test_specificity_matches_the_spec(selector: str, expected: list[tuple[int, int, int]]) -> None:
    assert css_specificity(selector) == expected


def test_specificity_rejects_an_invalid_selector() -> None:
    with pytest.raises(SelectorSyntaxError):
        css_specificity("a >> b")


def test_specificity_rejects_a_non_string() -> None:
    with pytest.raises(TypeError):
        css_specificity(123)  # ty: ignore[invalid-argument-type]  # intentional non-str exercises the C argument guard
