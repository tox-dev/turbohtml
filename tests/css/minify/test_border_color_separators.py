from __future__ import annotations

import pytest

from turbohtml.clean import minify_css


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("red,blue", id="comma"),
        pytest.param("red/blue", id="slash"),
        pytest.param(",red", id="leading-comma"),
        pytest.param("red,", id="trailing-comma"),
    ],
)
def test_minify_css_border_color_preserves_invalid_separator(value: str) -> None:
    source = f"a{{border-color:{value}}}"
    assert minify_css(source) == source


def test_minify_css_border_color_preserves_valid_fallback() -> None:
    source = "a{border-color:blue;border-color:red,green}"
    assert minify_css(source) == source
