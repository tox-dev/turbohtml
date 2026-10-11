from __future__ import annotations

from typing import Final

import pytest

from turbohtml.clean import minify_css


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("none none", "none none", id="duplicate-none"),
        pytest.param("solid solid", "solid solid", id="duplicate-solid"),
        pytest.param("currentcolor currentcolor", "currentcolor currentcolor", id="duplicate-currentcolor"),
        pytest.param("none underline", "none underline", id="none-with-line"),
        pytest.param("underline none", "underline none", id="line-with-none"),
        pytest.param("none overline", "none overline", id="none-with-overline"),
        pytest.param("none line-through", "none line-through", id="none-with-line-through"),
        pytest.param("none blink", "none blink", id="none-with-blink"),
        pytest.param("none spelling-error", "none spelling-error", id="none-with-spelling-error"),
        pytest.param("none grammar-error", "none grammar-error", id="none-with-grammar-error"),
        pytest.param("solid wavy", "solid wavy", id="solid-with-style"),
        pytest.param("solid double", "solid double", id="solid-with-double"),
        pytest.param("solid dotted", "solid dotted", id="solid-with-dotted"),
        pytest.param("solid dashed", "solid dashed", id="solid-with-dashed"),
        pytest.param("currentcolor red", "currentcolor red", id="currentcolor-with-color"),
        pytest.param("none,solid", "none,solid", id="comma"),
        pytest.param("solid/none", "solid/none", id="slash"),
        pytest.param("none inherit", "none inherit", id="wide-keyword"),
        pytest.param("none initial", "none initial", id="initial-with-none"),
        pytest.param("none unset", "none unset", id="unset-with-none"),
        pytest.param("none revert", "none revert", id="revert-with-none"),
        pytest.param("none revert-layer", "none revert-layer", id="revert-layer-with-none"),
        pytest.param("solid var(--decoration)", "solid var(--decoration)", id="substitution"),
        pytest.param("none color(display-p3 1 0 0)", "color(display-p3 1 0 0)", id="valid-color-function"),
        pytest.param("none hsl(47.3 83% 72%)", "hsl(47.3 83% 72%)", id="valid-unfolded-hsl"),
        pytest.param("currentcolor hsl(47.3 83% 72%)", "currentcolor hsl(47.3 83% 72%)", id="duplicate-color-function"),
        pytest.param("none calc(var(--width))", "calc(var(--width))", id="valid-math-function"),
        pytest.param("none mystery(1px)", "none mystery(1px)", id="unknown-function"),
        pytest.param('none "x"', 'none "x"', id="unexpected-token"),
        pytest.param("none solid", "none", id="valid-defaults"),
        pytest.param("solid none", "none", id="valid-reversed-defaults"),
        pytest.param("currentcolor none solid", "none", id="valid-all-defaults"),
        pytest.param("white none solid", "#fff", id="valid-color"),
        pytest.param("underline overline solid", "underline overline", id="valid-lines"),
        pytest.param("none red", "red", id="valid-named-color"),
        pytest.param("currentcolor 2px", "2px", id="valid-thickness"),
        pytest.param("none auto", "auto", id="valid-auto-thickness"),
        pytest.param("none from-font", "from-font", id="valid-from-font-thickness"),
        pytest.param("none 10%", "10%", id="valid-percent-thickness"),
        pytest.param("none 0", "0", id="valid-zero-thickness"),
        pytest.param("none #fff", "#fff", id="valid-hash-color"),
        pytest.param("#fff none", "#fff", id="valid-hash-first"),
    ],
)
def test_text_decoration_defaults_preserve_validity(value: str, expected: str) -> None:
    source: Final = f"a {{ text-decoration: {value}; color: #ff0000 }}"
    output: Final = f"a{{text-decoration:{expected};color:red}}"
    assert minify_css(source) == output
