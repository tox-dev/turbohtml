from __future__ import annotations

from typing import Final

import pytest

from turbohtml.clean import minify_css


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            "a { --s:bold 16px; font:400 var(--s) serif; }",
            "a{--s:bold 16px;font:400 var(--s)serif}",
            id="known-duplicate-weight",
        ),
        pytest.param(
            "a { font:400 var(--s,bold 16px) serif; }",
            "a{font:400 var(--s,bold 16px)serif}",
            id="fallback-duplicate-weight",
        ),
        pytest.param(
            "a { font:400 var(--s,var(--t,bold 16px)) serif; }",
            "a{font:400 var(--s,var(--t,bold 16px))serif}",
            id="nested-fallback-weight",
        ),
        pytest.param(
            "a { --w:bold; font:400 var(--w) 16px serif; }",
            "a{--w:bold;font:400 var(--w)16px serif}",
            id="variable-weight-placement",
        ),
        pytest.param(
            "a { --s:italic 16px; font:400 var(--s) serif; }",
            "a{--s:italic 16px;font:400 var(--s)serif}",
            id="valid-style-size-control",
        ),
        pytest.param(
            "a { --s:16px; font:400 var(--s) serif; }", "a{--s:16px;font:400 var(--s)serif}", id="valid-size-control"
        ),
        pytest.param(
            "a { font:400 16px var(--family); }", "a{font:400 16px var(--family)}", id="variable-family-control"
        ),
        pytest.param(
            "a { font:400 16px/var(--height) serif; }",
            "a{font:400 16px/var(--height)serif}",
            id="variable-line-height-control",
        ),
        pytest.param(
            "a { font:400 calc(var(--size)) serif; }",
            "a{font:400 calc(var(--size))serif}",
            id="opaque-calculation-control",
        ),
        pytest.param(
            "a { font:400 env(missing-font,bold 16px) serif; }",
            "a{font:400 env(missing-font,bold 16px)serif}",
            id="environment-fallback-control",
        ),
        pytest.param("a { font:var(--font); }", "a{font:var(--font)}", id="one-component-control"),
        pytest.param("a { font:400 16px serif; }", "a{font:16px serif}", id="literal-default-weight-control"),
        pytest.param("a { font:bold 16px serif; }", "a{font:700 16px serif}", id="literal-bold-control"),
        pytest.param("a { font:400 calc(8px + 8px) serif; }", "a{font:16px serif}", id="folded-size-control"),
        pytest.param('a { font:400 16px "Arial"; }', "a{font:16px arial}", id="literal-family-control"),
        pytest.param("a { font:1px -1px; }", "a{font:1px -1px}", id="invalid-dimension-family"),
        pytest.param("a { font:0px -apple-system; }", "a{font:0 '-apple-system'}", id="ident-family-control"),
    ],
)
def test_font_substitutions(source: str, expected: str) -> None:
    first: Final = minify_css(source)
    assert (first, minify_css(first)) == (expected,) * 2
