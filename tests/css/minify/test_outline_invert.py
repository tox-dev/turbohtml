from __future__ import annotations

import pytest

from turbohtml.clean import minify_css


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("a{outline:invert}", "a{outline:invert}", id="lone"),
        pytest.param("a{outline:medium none invert}", "a{outline:invert}", id="with-defaults"),
        pytest.param("a{outline:invert red}", "a{outline:invert red}", id="with-color"),
        pytest.param("a{outline:invert!important}", "a{outline:invert!important}", id="important"),
    ],
)
def test_minify_css_outline_keeps_retired_invert(source: str, expected: str) -> None:
    assert minify_css(source) == expected


def test_minify_css_outline_still_drops_current_defaults() -> None:
    assert minify_css("a{outline:medium none}") == "a{outline:none}"
