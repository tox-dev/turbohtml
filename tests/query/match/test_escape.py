"""The CSS identifier escaper mirrors soupsieve.escape / CSS.escape, character by character."""

from __future__ import annotations

import pytest

from turbohtml.query import escape_identifier


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param("", "", id="empty"),
        pytest.param("foo", "foo", id="plain"),
        pytest.param("ABC", "ABC", id="uppercase-kept"),
        pytest.param("_x", "_x", id="underscore-kept"),
        pytest.param("a-", "a-", id="trailing-dash-kept"),
        pytest.param("café", "café", id="non-ascii-kept"),
        pytest.param("foo bar", "foo\\ bar", id="space-backslashed"),
        pytest.param("a#b.c", "a\\#b\\.c", id="punctuation-backslashed"),
        pytest.param("-", "\\-", id="lone-dash"),
        pytest.param("--", "--", id="double-dash-kept"),
        pytest.param("-1", "-\\31 ", id="dash-then-digit"),
        pytest.param("12ab", "\\31 2ab", id="leading-digit"),
        pytest.param("0", "\\30 ", id="lone-digit"),
        pytest.param("a\tb", "a\\9 b", id="interior-control"),
        pytest.param("\x7f", "\\7f ", id="delete-char"),
        pytest.param("\x00abc", "�abc", id="null-to-replacement"),
    ],
)
def test_escape_matches_cssom(raw: str, expected: str) -> None:
    assert escape_identifier(raw) == expected
