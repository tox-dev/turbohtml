from __future__ import annotations

import pytest

from turbohtml.clean import minify_css


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("red red red red", "red", id="four-values"),
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
