from __future__ import annotations

import pytest

from turbohtml.clean import minify_css


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("a{flex-basis:0foo}", "a{flex-basis:0foo}", id="basis-unknown-unit"),
        pytest.param("a{flex-basis:0deg}", "a{flex-basis:0deg}", id="basis-angle"),
        pytest.param("a{flex-basis:0s}", "a{flex-basis:0s}", id="basis-time"),
        pytest.param("a{flex-basis:0py}", "a{flex-basis:0py}", id="basis-similar-unit"),
        pytest.param("a{flex-basis:0.1}", "a{flex-basis:.1}", id="basis-unitless-fraction"),
        pytest.param("a{flex-basis:1.0}", "a{flex-basis:1}", id="basis-unitless-nonzero"),
        pytest.param(
            "a{background-position:left 0foo top}", "a{background-position:0foo 0}", id="position-unknown-unit"
        ),
        pytest.param("a{background-position:left 0deg top}", "a{background-position:0deg 0}", id="position-angle"),
        pytest.param("a{background:url(x) left 0foo top 0}", "a{background:url(x)0foo 0}", id="background-layer"),
        pytest.param("a{box-shadow:1px 2px 0foo red}", "a{box-shadow:1px 2px 0foo red}", id="shadow-unknown-unit"),
        pytest.param("a{box-shadow:1px 2px 0deg red}", "a{box-shadow:1px 2px 0deg red}", id="shadow-angle"),
        pytest.param("a{box-shadow:1px 2px 0% red}", "a{box-shadow:1px 2px 0% red}", id="shadow-percentage"),
        pytest.param("a{flex-basis:0px}", "a{flex-basis:0}", id="valid-basis-length"),
        pytest.param("a{flex-basis:0em}", "a{flex-basis:0}", id="valid-basis-relative-length"),
        pytest.param("a{flex-basis:0%}", "a{flex-basis:0%}", id="valid-basis-percentage"),
        pytest.param("a{background-position:left 0px top}", "a{background-position:0 0}", id="valid-position-length"),
        pytest.param(
            "a{background-position:left 0% top}", "a{background-position:0 0}", id="valid-position-percentage"
        ),
        pytest.param("a{background-position:left 1 top}", "a{background-position:1 0}", id="position-unitless"),
        pytest.param("a{box-shadow:1px 2px 0px red}", "a{box-shadow:1px 2px red}", id="valid-shadow-length"),
        pytest.param("a{box-shadow:1px 2px 0em red}", "a{box-shadow:1px 2px red}", id="valid-shadow-em"),
        pytest.param("a{box-shadow:1px 2px 0pt red}", "a{box-shadow:1px 2px red}", id="valid-shadow-pt"),
    ],
)
def test_minify_css_zero_dimensions_keep_their_roles(source: str, expected: str) -> None:
    assert minify_css(source) == expected
