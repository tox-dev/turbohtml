"""The whitespace fold behind ``Markup.striptags`` in C: the words joined by single spaces."""

from __future__ import annotations

import pytest

from turbohtml._html import _collapse_whitespace
from turbohtml.migration.markupsafe import Markup


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("  a \t b\n\nc  ", "a b c", id="runs-collapse-and-edges-drop"),
        pytest.param("a\u00a0b\u2003c", "a b c", id="unicode-whitespace-splits"),
        pytest.param("", "", id="empty"),
        pytest.param(" \n ", "", id="only-whitespace"),
        pytest.param("one", "one", id="one-word"),
        pytest.param("\u00e9t\u00e9 \U0001f600", "\u00e9t\u00e9 \U0001f600", id="wide-code-points-survive"),
    ],
)
def test_collapse_whitespace(text: str, expected: str) -> None:
    assert _collapse_whitespace(text) == expected


def test_collapse_needs_a_str() -> None:
    with pytest.raises(TypeError, match="must be a str"):
        _collapse_whitespace(b"a b")  # ty: ignore[invalid-argument-type]  # the argument check is the point


def test_striptags_reads_the_same_fold() -> None:
    assert Markup("<p>a \n <b>b</b>\t</p> c").striptags() == "a b c"
