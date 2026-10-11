from __future__ import annotations

import pytest

from turbohtml.clean import minify_css


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("U+0042-0041", "U+0042-0041", id="reversed-alone"),
        pytest.param("U+0042-0041,U+0043", "U+0042-0041,U+0043", id="reversed-first"),
        pytest.param("U+0041,U+0042-0041", "U+0041,U+0042-0041", id="reversed-last"),
        pytest.param("U+0042-0042", "U+42", id="equal-boundaries"),
        pytest.param("U+0041-0042", "U+41-42", id="ascending-control"),
    ],
)
def test_minify_css_preserves_reversed_unicode_ranges(value: str, expected: str) -> None:
    assert minify_css(f"@font-face{{font-family:x;src:url(x);unicode-range:{value}}}") == (
        f"@font-face{{font-family:x;src:url(x);unicode-range:{expected}}}"
    )
