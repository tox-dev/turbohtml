from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest

from turbohtml.clean import minify_css

if TYPE_CHECKING:
    from _pytest.mark.structures import ParameterSet

_NUMERIC_CASES: Final[list[ParameterSet]] = [
    pytest.param("a { z-index:1. }", "a{z-index:1.}", id="trailing-dot"),
    pytest.param("a { width:1.", "a{width:1.}", id="trailing-dot-at-eof"),
    pytest.param(
        "a { margin-top:1.; margin-right:2px; margin-bottom:3px; margin-left:4px }",
        "a{margin-top:1.;margin-right:2px;margin-bottom:3px;margin-left:4px}",
        id="trailing-dot-edge-keeps-longhands",
    ),
    pytest.param("a { z-index:+1. }", "a{z-index:1.}", id="positive-dot"),
    pytest.param("a { z-index:-1. }", "a{z-index:-1.}", id="negative-dot"),
    pytest.param("a { z-index:0. }", "a{z-index:0.}", id="zero-dot"),
    pytest.param("a { z-index:2;z-index:1. }", "a{z-index:2;z-index:1.}", id="integer-fallback"),
    pytest.param("a { z-index:2;z-index:1.!important }", "a{z-index:2;z-index:1.!important}", id="important-fallback"),
    pytest.param("a { width:2px;width:1.px }", "a{width:2px;width:1.px}", id="dimension-fallback"),
    pytest.param("a { width:2%;width:1.% }", "a{width:2%;width:1.%}", id="percentage-fallback"),
    pytest.param(
        "a { margin-top:1.px; margin-right:2px; margin-bottom:3px; margin-left:4px }",
        "a{margin-top:1.px;margin-right:2px;margin-bottom:3px;margin-left:4px}",
        id="invalid-edge-keeps-longhands",
    ),
    pytest.param("a { z-index:1.e0 }", "a{z-index:1.e0}", id="dot-before-exponent"),
    pytest.param("a { width:1.e0px }", "a{width:1.e0px}", id="dot-before-exponent-unit"),
    pytest.param("a { x:1./**/ }", "a{x:1.}", id="comment-after-dot"),
    pytest.param("a { x:1./**/px }", "a{x:1.px}", id="comment-before-unit"),
    pytest.param("a { x:1. 5 }", "a{x:1. 5}", id="dot-before-number"),
    pytest.param("a { x:1/**/.5 }", "a{x:1 .5}", id="comment-before-fraction"),
    pytest.param("a { width:.500px }", "a{width:.5px}", id="leading-fraction"),
    pytest.param("a { width:1.500px }", "a{width:1.5px}", id="digit-leading-fraction"),
    pytest.param("a { width:-1.500px }", "a{width:-1.5px}", id="negative-fraction"),
    pytest.param("a { width:+1.500px }", "a{width:1.5px}", id="positive-fraction"),
    pytest.param("a { width:1e3px }", "a{width:1e3px}", id="exponent"),
    pytest.param("a { width:1e-3px }", "a{width:.001px}", id="negative-exponent"),
    pytest.param("a{width:calc(1E1E3PX + 1px)}", "a{width:calc(1E1E3PX + 1px)}", id="calc-exponent-unit"),
    pytest.param("a{width:calc(1e1e-3px + 1px)}", "a{width:calc(1e1e-3px + 1px)}", id="calc-negative-exponent-unit"),
    pytest.param("a{width:calc(1E1E3PX)}", "a{width:calc(1E1E3PX)}", id="calc-single-exponent-unit"),
    pytest.param(
        "a{width:calc(1E1E3PX - 1E1E3PX)}",
        "a{width:calc(1E1E3PX - 1E1E3PX)}",
        id="calc-canceling-exponent-unit",
    ),
    pytest.param("a{width:calc(1e3px + 1px)}", "a{width:1001px}", id="calc-normal-exponent"),
    pytest.param("a { opacity:1.0 }", "a{opacity:1}", id="valid-whole-number"),
    pytest.param("a { --x:1. }", "a{--x:1.}", id="custom-property"),
]


@pytest.mark.parametrize(("source", "expected"), _NUMERIC_CASES)
def test_minify_css_numeric_token_boundaries(source: str, expected: str) -> None:
    assert minify_css(source) == expected


@pytest.mark.parametrize(("source", "expected"), _NUMERIC_CASES)
def test_minify_css_numeric_token_boundaries_fixed_point(source: str, expected: str) -> None:
    assert minify_css(minify_css(source)) == expected
