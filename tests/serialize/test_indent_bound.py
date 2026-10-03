"""Nested lists and block quotes indent no deeper than the nesting cap, keeping output linear in the input."""

from __future__ import annotations

from typing import Final

import pytest

from turbohtml import Markdown, parse

_NESTED_ITEMS: Final = "<ul><li>" * 12 + "x"


def _capped_lines(marker: str) -> str:
    return "\n".join("  " * min(level, 10) + marker for level in range(12)) + "x"


def test_markdown_list_indent_stops_at_the_nesting_cap() -> None:
    assert parse(_NESTED_ITEMS).to_markdown() == _capped_lines("- ")


def test_text_list_indent_stops_at_the_nesting_cap() -> None:
    assert parse(_NESTED_ITEMS).to_text() == _capped_lines("* ")


@pytest.mark.parametrize(
    ("method", "expected"),
    [
        pytest.param("to_markdown", "> " * 20 + "x", id="markdown"),
        pytest.param("to_text", " " * 80 + "x", id="text"),
    ],
)
def test_blockquote_indent_stops_at_the_nesting_cap(method: str, expected: str) -> None:
    assert getattr(parse("<blockquote>" * 22 + "x"), method)() == expected


@pytest.mark.parametrize(
    ("source", "method", "expected"),
    [
        pytest.param("<ol><li>" * 8192, "to_markdown", 301_068, id="ordered-items-markdown"),
        pytest.param("<ol><li>" * 8192, "to_text", 301_068, id="ordered-items-text"),
        pytest.param("<blockquote>x<br>" * 4096, "to_markdown", 621_951, id="quoted-lines-markdown"),
        pytest.param("<blockquote>x<br>" * 4096, "to_text", 670_141, id="quoted-lines-text"),
    ],
)
def test_deep_nesting_output_stays_linear(source: str, method: str, expected: int) -> None:
    assert len(getattr(parse(source), method)()) == expected


def test_google_doc_margin_indent_stops_at_the_nesting_cap() -> None:
    document = parse("<ul><li style='margin-left:99999999999px'>x</li><li style='margin-left:72px'>y</li></ul>")
    assert document.to_markdown(Markdown.google_doc()) == "  " * 10 + "- x\n    - y"
