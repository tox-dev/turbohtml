from __future__ import annotations

import pytest

from turbohtml.clean import minify_css


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            "a{background-position:right 10.5% bottom 20.5%}",
            "a{background-position:right 10.5% bottom 20.5%}",
            id="fractional-both-axes",
        ),
        pytest.param(
            "a{background-position:right .5% bottom .5%}",
            "a{background-position:right .5% bottom .5%}",
            id="fractional-below-one",
        ),
        pytest.param(
            "a{background-position:left 10% bottom 20.5%}",
            "a{background-position:left 10% bottom 20.5%}",
            id="fractional-vertical-offset",
        ),
        pytest.param(
            "a{background-position:right -10.5% bottom 20%}",
            "a{background-position:right -10.5% top 80%}",
            id="negative-fractional",
        ),
        pytest.param(
            "a{background-position:right 1e2% bottom 20%}",
            "a{background-position:0 80%}",
            id="normalized-integer-exponent",
        ),
        pytest.param(
            "a{background-position:right 1000000000000000000% bottom 20%}",
            "a{background-position:right 1e18% top 80%}",
            id="large-number",
        ),
        pytest.param(
            "a{background-position:right 12345678901234567890% bottom 20%}",
            "a{background-position:right 12345678901234567890% top 80%}",
            id="overlong-integer",
        ),
        pytest.param(
            "a{background:url(x) right 10.5% bottom 20.5%}",
            "a{background:url(x)right 10.5% bottom 20.5%}",
            id="background-shorthand",
        ),
        pytest.param(
            "a{background-position:right -10% bottom 20%}",
            "a{background-position:110% 80%}",
            id="negative-integer-control",
        ),
        pytest.param(
            "a{background-position:right 10% bottom 20%}",
            "a{background-position:90% 80%}",
            id="integer-control",
        ),
    ],
)
def test_minify_css_position_fractional_offsets(source: str, expected: str) -> None:
    assert minify_css(source) == expected
