from __future__ import annotations

import pytest

from turbohtml.clean import minify_css


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("hsl(.5turn 100% 50%)", "hsl(.5turn 100% 50%)", id="turn-hue"),
        pytest.param("hsl(200grad 100% 50%)", "hsl(200grad 100% 50%)", id="grad-hue"),
        pytest.param("hsl(3.14159rad 100% 50%)", "hsl(3.14159rad 100% 50%)", id="radian-hue"),
        pytest.param("rgb(255px 0 0)", "rgb(255px 0 0)", id="invalid-rgb-channel-unit"),
        pytest.param("rgb(255s 0 0)", "rgb(255s 0 0)", id="invalid-one-letter-unit"),
        pytest.param("rgb(255 0 0 / .5px)", "rgb(255 0 0 / .5px)", id="invalid-alpha-unit"),
        pytest.param("hsl(180 100% 50%)", "#0ff", id="unitless-hue-control"),
        pytest.param("rgb(255 0 0)", "red", id="unitless-rgb-control"),
    ],
)
def test_minify_css_preserves_color_number_units(value: str, expected: str) -> None:
    assert minify_css(f"a{{color:{value}}}") == f"a{{color:{expected}}}"
