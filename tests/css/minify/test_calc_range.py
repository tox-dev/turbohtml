from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest

from turbohtml.clean import minify_css

if TYPE_CHECKING:
    from _pytest.mark.structures import ParameterSet

_CALC_RANGE_CASES: Final[list[ParameterSet]] = [
    pytest.param("a{width:calc(0px - 1px)}", "a{width:calc(-1px)}", id="width-clamps-negative"),
    pytest.param("a{padding:calc(1px - 2px)}", "a{padding:calc(-1px)}", id="padding-clamps-negative"),
    pytest.param("a{opacity:calc(0 - 1)}", "a{opacity:calc(-1)}", id="opacity-clamps-negative"),
    pytest.param("a{margin:calc(0px - 1px)}", "a{margin:-1px}", id="margin-allows-negative"),
    pytest.param("a{margin-left:calc(0px - 1px)}", "a{margin-left:-1px}", id="margin-side-allows-negative"),
    pytest.param("a{inset:calc(0px - 1px)}", "a{inset:-1px}", id="inset-allows-negative"),
    pytest.param(
        "a{inset-inline-start:calc(0px - 1px)}", "a{inset-inline-start:-1px}", id="inset-side-allows-negative"
    ),
    pytest.param("a{top:calc(0px - 1px)}", "a{top:-1px}", id="top-allows-negative"),
    pytest.param("a{right:calc(0px - 1px)}", "a{right:-1px}", id="right-allows-negative"),
    pytest.param("a{bottom:calc(0px - 1px)}", "a{bottom:-1px}", id="bottom-allows-negative"),
    pytest.param("a{left:calc(0px - 1px)}", "a{left:-1px}", id="left-allows-negative"),
    pytest.param("a{z-index:calc(0 - 1)}", "a{z-index:-1}", id="z-index-allows-negative"),
    pytest.param("a{order:calc(0 - 1)}", "a{order:-1}", id="order-allows-negative"),
    pytest.param("a{text-indent:calc(0px - 1px)}", "a{text-indent:-1px}", id="text-indent-allows-negative"),
    pytest.param("a{transform:translateX(calc(0px - 1px))}", "a{transform:translateX(-1px)}", id="nested-calc"),
    pytest.param(
        "a{width:var(--x,calc(0px - 1px))}",
        "a{width:var(--x,calc(-1px))}",
        id="var-fallback-keeps-clamp",
    ),
    pytest.param(
        "a{width:env(foo,calc(0px - 1px))}",
        "a{width:env(foo,calc(-1px))}",
        id="env-fallback-keeps-clamp",
    ),
]


@pytest.mark.parametrize(("source", "expected"), _CALC_RANGE_CASES)
def test_minify_css_calc_range(source: str, expected: str) -> None:
    assert minify_css(source) == expected
