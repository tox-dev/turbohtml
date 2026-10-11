from __future__ import annotations

import pytest

from turbohtml.clean import minify_css


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("0 0 red 0", "0 0 red 0", id="color-before-blur"),
        pytest.param("0 0 inset 0", "0 0 inset 0", id="inset-before-blur"),
        pytest.param("0 red 0 0", "0 red 0 0", id="color-between-offsets"),
        pytest.param("0 0 red 0 0", "0 0 red 0 0", id="color-before-blur-and-spread"),
        pytest.param("0 0 0 red 0", "0 0 0 red 0", id="color-before-spread"),
        pytest.param("0 0 inset 0 0", "0 0 inset 0 0", id="inset-before-blur-and-spread"),
        pytest.param("0 inset 0 0 blue 0", "0 inset 0 0 blue 0", id="inset-between-offsets"),
        pytest.param("0 0 0 red", "0 0 red", id="color-after-lengths"),
        pytest.param("red 0 0 0", "red 0 0", id="color-before-lengths"),
        pytest.param("0 0 0 0 red", "0 0 red", id="zero-blur-and-spread"),
        pytest.param("0 0 1px 0", "0 0 1px", id="zero-spread"),
    ],
)
def test_minify_css_box_shadow_length_order(value: str, expected: str) -> None:
    assert minify_css(f"a{{box-shadow:{value}}}") == f"a{{box-shadow:{expected}}}"
