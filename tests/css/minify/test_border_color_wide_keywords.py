from __future__ import annotations

import pytest

from turbohtml.clean import minify_css


@pytest.mark.parametrize(
    "keyword",
    ["inherit", "initial", "unset", "revert", "revert-layer"],
)
def test_minify_css_border_color_preserves_repeated_wide_keyword(keyword: str) -> None:
    source = f"a{{border-color:{keyword} {keyword}}}"
    assert minify_css(source) == source


def test_minify_css_border_color_preserves_wide_keyword_fallback() -> None:
    source = "a{border-color:blue;border-color:inherit inherit}"
    assert minify_css(source) == source


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("red red", "red", id="color"),
        pytest.param("rebeccapurple rebeccapurple", "#639", id="long-color"),
        pytest.param("currentColor currentColor", "initial", id="current-color"),
    ],
)
def test_minify_css_border_color_still_collapses_colors(value: str, expected: str) -> None:
    assert minify_css(f"a{{border-color:{value}}}") == f"a{{border-color:{expected}}}"


def test_minify_css_border_color_still_collapses_non_wide_identifier() -> None:
    assert minify_css("a{border-color:revertish revertish}") == "a{border-color:revertish}"
