from __future__ import annotations

import pytest

from turbohtml.clean import minify_css


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("normal normal normal normal normal 16px serif", id="five-defaults"),
        pytest.param("normal normal normal normal normal normal 16px serif", id="six-defaults"),
        pytest.param("italic normal normal normal normal 16px serif", id="style-plus-four-defaults"),
        pytest.param("normal normal normal normal normal 16px/normal serif", id="with-line-height"),
        pytest.param("oblique 20deg normal normal normal normal 16px serif", id="oblique-angle-plus-four-defaults"),
        pytest.param("normal normal normal normal normal 0 serif", id="zero-font-size"),
        pytest.param("normal normal normal normal normal medium serif", id="keyword-font-size"),
        pytest.param("normal normal normal normal normal 400 16px serif", id="numeric-weight"),
        pytest.param("oblique normal normal normal normal 16px serif", id="oblique-without-angle"),
    ],
)
def test_minify_css_keeps_overlong_font_options(value: str) -> None:
    source = f"a{{font:{value}}}"
    assert minify_css(source) == source


def test_minify_css_keeps_overlong_calculated_font_size_invalid() -> None:
    assert minify_css("a{font:normal normal normal normal normal calc(16px) serif}") == (
        "a{font:normal normal normal normal normal 16px serif}"
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("normal normal normal normal 16px serif", "16px serif", id="four-defaults"),
        pytest.param("normal normal normal normal 16px/normal serif", "16px serif", id="with-line-height"),
        pytest.param("normal normal 16px Normal", "16px Normal", id="normal-family-name"),
        pytest.param("normal normal normal normal 0 serif", "0 serif", id="valid-zero-font-size"),
        pytest.param("normal normal normal normal 16px Times New Roman", "16px Times New Roman", id="multiword-family"),
        pytest.param("normal normal normal normal calc(16px) serif", "16px serif", id="valid-calculated-size"),
        pytest.param(
            "oblique 20deg normal normal normal 16px serif",
            "oblique 20deg 16px serif",
            id="oblique-angle-with-four-options",
        ),
    ],
)
def test_minify_css_still_shortens_valid_font_options(value: str, expected: str) -> None:
    assert minify_css(f"a{{font:{value}}}") == f"a{{font:{expected}}}"
