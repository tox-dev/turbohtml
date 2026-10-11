from __future__ import annotations

import pytest

from turbohtml.clean import minify_css


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("400 400 16px serif", id="two-default-weights"),
        pytest.param("bold 400 16px serif", id="bold-then-default"),
        pytest.param("400 bold 16px serif", id="default-then-bold"),
        pytest.param("bold bold 16px serif", id="two-bold-weights"),
        pytest.param("bolder 400 16px serif", id="relative-heavier-then-default"),
        pytest.param("lighter 400 16px serif", id="relative-lighter-then-default"),
    ],
)
def test_minify_css_keeps_duplicate_font_weights(value: str) -> None:
    source = f"a{{font:{value}}}"
    assert minify_css(source) == source


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("400 16px serif", "16px serif", id="single-default-weight"),
        pytest.param("bold 16px serif", "700 16px serif", id="single-bold-weight"),
        pytest.param("bold normal 16px serif", "700 16px serif", id="weight-and-other-default"),
        pytest.param("bold 16px/400 serif", "700 16px/400 serif", id="numeric-line-height"),
        pytest.param("bolder 16px serif", "bolder 16px serif", id="single-relative-weight"),
        pytest.param("blur 400 16px serif", "blur 16px serif", id="b-prefix-nonweight"),
        pytest.param("light 400 16px serif", "light 16px serif", id="l-prefix-nonweight"),
    ],
)
def test_minify_css_still_shortens_one_font_weight(value: str, expected: str) -> None:
    assert minify_css(f"a{{font:{value}}}") == f"a{{font:{expected}}}"
