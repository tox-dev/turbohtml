from __future__ import annotations

from typing import Final

import pytest

from turbohtml.clean import minify_css


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("underline solid line-through", id="style-between-lines"),
        pytest.param("line-through solid underline", id="reversed-lines"),
        pytest.param("underline currentcolor overline", id="color-between-lines"),
        pytest.param("overline solid blink", id="blink-after-style"),
        pytest.param("none underline", id="default-with-line"),
    ],
)
def test_minify_css_preserves_split_decoration_lines(value: str) -> None:
    source: Final = f"a{{text-decoration:{value}}}"
    assert minify_css(source) == source


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("solid underline line-through", "underline line-through", id="style-before-lines"),
        pytest.param("underline line-through solid", "underline line-through", id="style-after-lines"),
        pytest.param("underline overline currentcolor", "underline overline", id="color-after-lines"),
    ],
)
def test_minify_css_reduces_adjacent_decoration_lines(value: str, expected: str) -> None:
    assert minify_css(f"a{{text-decoration:{value}}}") == f"a{{text-decoration:{expected}}}"
