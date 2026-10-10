from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest

from turbohtml.clean import minify_css

if TYPE_CHECKING:
    from _pytest.mark.structures import ParameterSet

_TYPE_CASES: Final[list[ParameterSet]] = [
    pytest.param("a{width:calc(1 - 1)}", "a{width:calc(0)}", id="number-zero-stays-number"),
    pytest.param("a{opacity:calc(1px - 1px)}", "a{opacity:calc(0px)}", id="length-zero-stays-length"),
    pytest.param("a{border-width:calc(1px + 0%)}", "a{border-width:calc(1px + 0%)}", id="zero-percent-keeps-type"),
    pytest.param("a{width:calc(1px + 0em)}", "a{width:calc(1px + 0em)}", id="mixed-length-zero-kept"),
    pytest.param("a{width:calc(2px + 1px)}", "a{width:3px}", id="nonzero-folds"),
]


@pytest.mark.parametrize(("source", "expected"), _TYPE_CASES)
def test_calc_preserves_types(source: str, expected: str) -> None:
    assert minify_css(source) == expected


@pytest.mark.parametrize(("source", "expected"), _TYPE_CASES)
def test_calc_type_output_is_a_fixed_point(source: str, expected: str) -> None:
    assert minify_css(minify_css(source)) == expected
