from __future__ import annotations

from typing import Final

import pytest

from turbohtml.clean import minify_css


@pytest.mark.parametrize("property_name", ["border", "border-top", "outline", "column-rule"])
@pytest.mark.parametrize("value", ["none inherit", "inherit none", "medium initial", "unset none", "none revert-layer"])
def test_minify_css_keeps_mixed_border_wide_keyword(property_name: str, value: str) -> None:
    source: Final = f"a{{{property_name}:{value}}}"
    assert minify_css(source) == source


@pytest.mark.parametrize(
    ("property_name", "value", "expected"),
    [
        pytest.param("border", "medium solid currentcolor", "solid", id="border"),
        pytest.param("outline", "medium solid", "solid", id="outline"),
        pytest.param("column-rule", "medium solid currentcolor", "solid", id="column-rule"),
    ],
)
def test_minify_css_reduces_border_without_wide_keyword(property_name: str, value: str, expected: str) -> None:
    assert minify_css(f"a{{{property_name}:{value}}}") == f"a{{{property_name}:{expected}}}"
