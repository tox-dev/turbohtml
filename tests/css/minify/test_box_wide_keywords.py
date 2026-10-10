from __future__ import annotations

import pytest

from turbohtml.clean import minify_css


@pytest.mark.parametrize(
    ("property_name", "keyword"),
    [
        pytest.param("margin", "inherit", id="margin-inherit"),
        pytest.param("margin", "initial", id="margin-initial"),
        pytest.param("margin", "unset", id="margin-unset"),
        pytest.param("margin", "revert", id="margin-revert"),
        pytest.param("margin", "revert-layer", id="margin-revert-layer"),
        pytest.param("padding", "inherit", id="padding"),
        pytest.param("border-width", "inherit", id="border-width"),
        pytest.param("border-style", "inherit", id="border-style"),
        pytest.param("inset", "inherit", id="inset"),
        pytest.param("border-radius", "inherit", id="border-radius"),
        pytest.param("scroll-margin", "inherit", id="scroll-margin"),
        pytest.param("scroll-padding", "inherit", id="scroll-padding"),
        pytest.param("gap", "inherit", id="gap"),
        pytest.param("grid-gap", "inherit", id="grid-gap"),
        pytest.param("overflow", "inherit", id="overflow"),
        pytest.param("overscroll-behavior", "inherit", id="overscroll-behavior"),
    ],
)
def test_minify_css_box_preserves_repeated_wide_keyword(property_name: str, keyword: str) -> None:
    source = f"a{{{property_name}:{keyword} {keyword}}}"
    assert minify_css(source) == source


def test_minify_css_box_preserves_valid_fallback() -> None:
    source = "a{margin:1px;margin:inherit inherit}"
    assert minify_css(source) == source


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("a{margin:1px 1px}", "a{margin:1px}", id="margin"),
        pytest.param("a{overflow:hidden hidden}", "a{overflow:hidden}", id="overflow"),
    ],
)
def test_minify_css_box_still_collapses_equal_values(source: str, expected: str) -> None:
    assert minify_css(source) == expected
