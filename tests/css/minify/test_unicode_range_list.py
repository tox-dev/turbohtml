from __future__ import annotations

import pytest

from turbohtml.clean import minify_css


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("U+41,,U+42", "U+41,,U+42", id="repeated-comma"),
        pytest.param(",U+41", ",U+41", id="leading-comma"),
        pytest.param("U+41,", "U+41,", id="trailing-comma"),
        pytest.param("U+41 U+42", "U+41 U+42", id="missing-comma"),
        pytest.param("U+41/**/U+42", "U+41/**/U+42", id="comment-without-comma"),
        pytest.param("U+41,U+42", "U+41-42", id="valid-comma"),
    ],
)
def test_minify_css_preserves_invalid_unicode_range_lists(value: str, expected: str) -> None:
    assert minify_css(f"@font-face{{font-family:x;src:url(x);unicode-range:{value}}}") == (
        f"@font-face{{font-family:x;src:url(x);unicode-range:{expected}}}"
    )
