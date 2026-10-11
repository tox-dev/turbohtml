from __future__ import annotations

from typing import Final

import pytest

from turbohtml.clean import minify_css


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("red red red red", "red", id="four-values"),
        pytest.param("var(--x)", "var(--x)", id="one-variable"),
        pytest.param(
            "color(display-p3 1 0 0) color(display-p3 1 0 0)",
            "color(display-p3 1 0 0)",
            id="single-color-function",
        ),
        pytest.param("red red red red red", "red red red red red", id="five-values"),
        pytest.param(
            "currentColor currentColor currentColor currentColor currentColor",
            "currentcolor currentcolor currentcolor currentcolor currentcolor",
            id="five-currentcolor-values",
        ),
    ],
)
def test_minify_css_border_color_equal_values(value: str, expected: str) -> None:
    assert minify_css(f"a{{border-color:{value}}}") == f"a{{border-color:{expected}}}"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("var(--x) var(--x)", "var(--x)var(--x)", id="variable"),
        pytest.param("env(x) env(x) env(x)", "env(x)env(x)env(x)", id="environment"),
        pytest.param(
            "attr(data-x color) attr(data-x color) attr(data-x color) attr(data-x color)",
            "attr(data-x color)attr(data-x color)attr(data-x color)attr(data-x color)",
            id="attribute",
        ),
    ],
)
def test_minify_css_border_color_preserves_repeated_substitutions(value: str, expected: str) -> None:
    source: Final = f"a{{border-color:{value}}}"
    assert minify_css(source) == f"a{{border-color:{expected}}}"
