from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest

from turbohtml.clean import minify_css

if TYPE_CHECKING:
    from _pytest.mark.structures import ParameterSet

_INTEGER_ROLES: Final[list[ParameterSet]] = [
    pytest.param("a{z-index:1.0}", "a{z-index:1.0}", id="fraction"),
    pytest.param("a{z-index:+1.0}", "a{z-index:+1.0}", id="positive-fraction"),
    pytest.param("a{z-index:-0.0}", "a{z-index:-0.0}", id="negative-zero"),
    pytest.param("a{z-index:1e0}", "a{z-index:1e0}", id="exponent"),
    pytest.param("a{z-index:1E+1}", "a{z-index:1E+1}", id="positive-exponent"),
    pytest.param("a{z-index:1e-1}", "a{z-index:1e-1}", id="negative-exponent"),
    pytest.param("a{z-index: /**/1.0/**/ }", "a{z-index:1.0}", id="literal-comments"),
    pytest.param("a{Z-INDEX:1.0!important}", "a{z-index:1.0!important}", id="literal-priority"),
    pytest.param("a{z-index:calc(1.0)}", "a{z-index:1}", id="math-integral"),
    pytest.param("a{z-index:calc(1000)}", "a{z-index:1000}", id="math-large-integer"),
    pytest.param("a{z-index:calc(-1.0)}", "a{z-index:-1}", id="math-negative-integer"),
    pytest.param("a{z-index:calc(-0.0)}", "a{z-index:0}", id="math-zero"),
    pytest.param("a{z-index:calc(1 - 1)}", "a{z-index:0}", id="math-zero-cancellation"),
    pytest.param("a{z-index:calc(1 + 1)}", "a{z-index:2}", id="math-resolved-integer"),
    pytest.param("a{z-index:CALC(1.0)!important}", "a{z-index:1!important}", id="math-integer-priority"),
    pytest.param("a{z-index:calc(1.0", "a{z-index:1}", id="math-integer-eof"),
    pytest.param("a{z-index:calc(1px)}", "a{z-index:calc(1px)}", id="math-dimension"),
    pytest.param("a{z-index:calc(+1.0)}", "a{z-index:1}", id="math-positive-integer"),
    pytest.param("a{z-index:3;z-index:calc(0px)}", "a{z-index:3;z-index:calc(0px)}", id="math-zero-dimension-cascade"),
    pytest.param("a{z-index:3;z-index:calc(0%)}", "a{z-index:3;z-index:calc(0%)}", id="math-zero-percentage-cascade"),
    pytest.param("a{z-index:calc(1px - 1px)}", "a{z-index:calc(1px - 1px)}", id="math-typed-cancellation"),
    pytest.param("a{z-index:calc(1.5)}", "a{z-index:calc(1.5)}", id="math-fraction"),
    pytest.param("a{z-index:calc(-1.5)}", "a{z-index:calc(-1.5)}", id="math-negative"),
    pytest.param("a{z-index:calc(1 + .5)}", "a{z-index:calc(1 + .5)}", id="math-arithmetic"),
    pytest.param("a{z-index: calc(1.5) /**/ }", "a{z-index:calc(1.5)}", id="math-comments"),
    pytest.param("a{z-index:CALC(1.5)!important}", "a{z-index:CALC(1.5)!important}", id="math-priority"),
    pytest.param("a{z-index:calc(1.5", "a{z-index:calc(1.5)}", id="math-eof"),
    pytest.param("a{z-index:calc(var(--x) + .5)}", "a{z-index:calc(var(--x) + .5)}", id="math-variable"),
    pytest.param("a{z-index:calc(calc(1.5))}", "a{z-index:calc(calc(1.5))}", id="nested-math"),
    pytest.param("a {Z-INDEX: calc}", "a{z-index:calc}", id="bare-calc"),
    pytest.param("a{z-index:calc /**/ 1.0}", "a{z-index:calc 1}", id="separated-calc-number"),
    pytest.param("a{z-index:calc[1.0]}", "a{z-index:calc[1]}", id="calc-bracket"),
    pytest.param("a{z-index:3;z-index:calc(.5)}", "a{z-index:3;z-index:calc(.5)}", id="math-leading-dot-cascade"),
    pytest.param("a{z-index:calc(1.5) 2}", "a{z-index:1.5 2}", id="math-followed-by-token"),
    pytest.param("a{z-index:}", "", id="empty-ordinary"),
    pytest.param("a{z-index:/**/!important}", "", id="comment-only-ordinary"),
    pytest.param("a{z-index:1}", "a{z-index:1}", id="integer"),
    pytest.param("a{z-index:+01}", "a{z-index:1}", id="signed-integer"),
    pytest.param("a{z-index:-0}", "a{z-index:0}", id="integer-zero"),
    pytest.param("a{z-index:auto}", "a{z-index:auto}", id="auto"),
    pytest.param("a{z-index:inherit}", "a{z-index:inherit}", id="inherit"),
    pytest.param("a{z-index:1.0px}", "a{z-index:1px}", id="dimension"),
    pytest.param("a{z-index:3;z-index:0px}", "a{z-index:3;z-index:0px}", id="zero-length-cascade"),
    pytest.param("a{z-index:3;z-index:-0.0PX}", "a{z-index:3;z-index:0px}", id="signed-zero-length-cascade"),
    pytest.param("a{z-index:3;z-index:0e5rem}", "a{z-index:3;z-index:0rem}", id="exponent-zero-length-cascade"),
    pytest.param("a{z-index:3;z-index:-0.0%}", "a{z-index:3;z-index:0%}", id="zero-percentage"),
    pytest.param("a{z-index:3;z-index:0s}", "a{z-index:3;z-index:0s}", id="zero-time"),
    pytest.param("a{z-index:3;z-index:0fr}", "a{z-index:3;z-index:0fr}", id="zero-flex"),
    pytest.param("a{z-index:3;z-index:0unknown}", "a{z-index:3;z-index:0unknown}", id="zero-unknown-unit"),
    pytest.param("a{z-index:min(1.0,2)}", "a{z-index:min(1,2)}", id="minimum"),
    pytest.param("a{z-index:clamp(0,1.5,2)}", "a{z-index:clamp(0,1.5,2)}", id="clamp"),
    pytest.param("a{z-index:foo(1.0)}", "a{z-index:foo(1)}", id="unknown-function"),
    pytest.param("a{z-index:1.0 2.0}", "a{z-index:1 2}", id="multiple-tokens"),
    pytest.param("a{opacity:1.0}", "a{opacity:1}", id="opacity"),
    pytest.param("a{line-height:calc(1.5)}", "a{line-height:1.5}", id="line-height"),
    pytest.param("a{width:calc(1.5px)}", "a{width:1.5px}", id="width"),
    pytest.param("a{--x:1.0;z-index:var(--x)}", "a{--x:1.0;z-index:var(--x)}", id="custom-property"),
    pytest.param("a{z-index:2;z-index:1.0}", "a{z-index:2;z-index:1.0}", id="literal-cascade"),
    pytest.param(
        "a{z-index:2!important;z-index:1.0!important}",
        "a{z-index:2!important;z-index:1.0!important}",
        id="literal-important-cascade",
    ),
    pytest.param("a{z-index:3;z-index:calc(1.5)}", "a{z-index:3;z-index:calc(1.5)}", id="math-cascade"),
    pytest.param(
        "a{z-index:3!important;z-index:calc(1.5)!important}",
        "a{z-index:3!important;z-index:calc(1.5)!important}",
        id="math-important-cascade",
    ),
]


@pytest.mark.parametrize(("source", "expected"), _INTEGER_ROLES)
def test_minify_css_integer_roles(source: str, expected: str) -> None:
    assert minify_css(source) == expected


@pytest.mark.parametrize(("source", "expected"), _INTEGER_ROLES)
def test_minify_css_integer_roles_are_a_fixed_point(source: str, expected: str) -> None:
    assert minify_css(minify_css(source)) == expected
