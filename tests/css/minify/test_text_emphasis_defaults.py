from __future__ import annotations

from typing import Final

import pytest

from turbohtml.clean import minify_css


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("none sesame", "none sesame", id="none-with-shape"),
        pytest.param("none redish sesame", "none redish sesame", id="unknown-r-name-with-shape"),
        pytest.param("none dot", "none dot", id="none-with-dot"),
        pytest.param("none circle", "none circle", id="none-with-circle"),
        pytest.param("none triangle", "none triangle", id="none-with-triangle"),
        pytest.param("none double-circle", "none double-circle", id="none-with-double-circle"),
        pytest.param("none none", "none none", id="duplicate-none"),
        pytest.param("currentcolor red", "currentcolor red", id="currentcolor-with-color"),
        pytest.param("currentcolor blue", "currentcolor blue", id="currentcolor-with-blue"),
        pytest.param("currentcolor #fff", "currentcolor #fff", id="currentcolor-with-hash"),
        pytest.param("#fff currentcolor", "#fff currentcolor", id="hash-before-currentcolor"),
        pytest.param("currentcolor currentcolor", "currentcolor currentcolor", id="duplicate-currentcolor"),
        pytest.param("open currentcolor dot", "open currentcolor dot", id="split-open-dot"),
        pytest.param("dot currentcolor open", "dot currentcolor open", id="split-dot-open"),
        pytest.param("filled currentcolor sesame", "filled currentcolor sesame", id="split-filled-sesame"),
        pytest.param("none open", "none open", id="none-with-open"),
        pytest.param('none "x"', 'none "x"', id="none-with-string"),
        pytest.param('"x" none', '"x" none', id="string-before-none"),
        pytest.param("none,red", "none,red", id="comma"),
        pytest.param("none/ red", "none/red", id="slash"),
        pytest.param("none var(--em)", "none var(--em)", id="substitution"),
        pytest.param("none inherit", "none inherit", id="wide-keyword"),
        pytest.param("none initial", "none initial", id="initial-with-none"),
        pytest.param("none unset", "none unset", id="unset-with-none"),
        pytest.param("none revert", "none revert", id="revert-with-none"),
        pytest.param("none revert-layer", "none revert-layer", id="revert-layer-with-none"),
        pytest.param("revert-layer none", "revert-layer none", id="revert-layer-before-none"),
        pytest.param("none mystery(1)", "none mystery(1)", id="unknown-function"),
        pytest.param("none 2px", "none 2px", id="unexpected-dimension"),
        pytest.param("currentcolor hsl(47.3 83% 72%)", "currentcolor hsl(47.3 83% 72%)", id="duplicate-color-function"),
        pytest.param("none red", "red", id="valid-color"),
        pytest.param("none orange", "orange", id="valid-six-character-color"),
        pytest.param("none crimson", "crimson", id="valid-seven-character-color"),
        pytest.param("none transparent", "#0000", id="valid-long-color"),
        pytest.param("red none", "red", id="valid-reversed-color"),
        pytest.param("blue none", "blue", id="valid-reversed-blue"),
        pytest.param("none #fff", "#fff", id="valid-hash"),
        pytest.param("#fff none", "#fff", id="valid-reversed-hash"),
        pytest.param("currentcolor sesame", "sesame", id="valid-shape"),
        pytest.param("red currentcolor", "red currentcolor", id="color-before-currentcolor"),
        pytest.param("sesame currentcolor", "sesame", id="valid-reversed-shape"),
        pytest.param("currentcolor open sesame", "open sesame", id="valid-open-shape"),
        pytest.param('currentcolor "x"', '"x"', id="valid-string"),
        pytest.param('"x" currentcolor', '"x"', id="valid-reversed-string"),
        pytest.param("none color(display-p3 1 0 0)", "color(display-p3 1 0 0)", id="valid-color-function"),
        pytest.param("none currentcolor", "none", id="valid-defaults"),
        pytest.param("currentcolor none", "none", id="valid-reversed-defaults"),
    ],
)
def test_text_emphasis_defaults_preserve_validity(value: str, expected: str) -> None:
    source: Final = f"a{{text-emphasis:{value};color:#ff0000}}"
    output: Final = f"a{{text-emphasis:{expected};color:red}}"
    assert minify_css(source) == output
