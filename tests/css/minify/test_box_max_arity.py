from __future__ import annotations

import pytest

from turbohtml.clean import minify_css


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("a{gap:1px 2px 1px}", "a{gap:1px 2px 1px}", id="gap-three"),
        pytest.param("a{grid-gap:1px 2px 1px}", "a{grid-gap:1px 2px 1px}", id="grid-gap-three"),
        pytest.param("a{overflow:hidden scroll hidden}", "a{overflow:hidden scroll hidden}", id="overflow-three"),
        pytest.param(
            "a{overscroll-behavior:contain auto contain}",
            "a{overscroll-behavior:contain auto contain}",
            id="overscroll-three",
        ),
        pytest.param("a{gap:1px 2px 1px 2px}", "a{gap:1px 2px 1px 2px}", id="gap-four"),
    ],
)
def test_minify_css_preserves_overlong_two_value_boxes(source: str, expected: str) -> None:
    assert minify_css(source) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("a{gap:1px 1px}", "a{gap:1px}", id="gap"),
        pytest.param("a{grid-gap:1px 1px}", "a{grid-gap:1px}", id="grid-gap"),
        pytest.param("a{overflow:hidden hidden}", "a{overflow:hidden}", id="overflow"),
        pytest.param("a{overscroll-behavior:contain contain}", "a{overscroll-behavior:contain}", id="overscroll"),
        pytest.param("a{margin:1px 2px 1px}", "a{margin:1px 2px}", id="margin-three"),
    ],
)
def test_minify_css_collapses_valid_box_values(source: str, expected: str) -> None:
    assert minify_css(source) == expected
