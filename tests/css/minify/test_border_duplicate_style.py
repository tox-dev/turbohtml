from __future__ import annotations

from typing import Final

import pytest

from turbohtml.clean import minify_css


@pytest.mark.parametrize(
    ("property_name", "value"),
    [
        pytest.param("border", "dashed none", id="border-after"),
        pytest.param("border", "none solid", id="border-before"),
        pytest.param("border", "none none", id="border-two-none"),
        pytest.param("border", "none red solid", id="border-short-color-before-style"),
        pytest.param("border-left", "solid none", id="border-side"),
        pytest.param("outline", "auto none", id="outline-auto"),
        pytest.param("outline", "none solid", id="outline-solid"),
        pytest.param("column-rule", "none dashed", id="column-rule"),
        pytest.param("border", "none var(--style)", id="dynamic-style"),
    ],
)
def test_minify_css_preserves_duplicate_border_styles(property_name: str, value: str) -> None:
    source: Final = f"a{{{property_name}:{value}}}"
    assert minify_css(source) == source


@pytest.mark.parametrize(
    ("property_name", "value", "expected"),
    [
        pytest.param("border", "none red", "red", id="border-color"),
        pytest.param("border", "none red medium", "red", id="border-short-color"),
        pytest.param("border", "none rgb(0 0 0)", "#000", id="border-color-function"),
        pytest.param("border", "none color(display-p3 0 0 0)", "color(display-p3 0 0 0)", id="border-wide-color"),
        pytest.param("border", "none currentcolor", "none", id="border-long-color"),
        pytest.param("border", "medium none currentcolor", "none", id="border-defaults"),
        pytest.param("outline", "none red", "red", id="outline-color"),
        pytest.param("column-rule", "none red", "red", id="column-rule-color"),
    ],
)
def test_minify_css_drops_single_border_style_default(property_name: str, value: str, expected: str) -> None:
    assert minify_css(f"a{{{property_name}:{value}}}") == f"a{{{property_name}:{expected}}}"
