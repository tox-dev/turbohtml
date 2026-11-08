from __future__ import annotations

from typing import Final

import pytest
from markdown_it import MarkdownIt

from turbohtml import Markdown, parse

_NESTED_ITEMS: Final[str] = f"{'<ul><li>' * 12}x"
# plain-text indent spends a budget that source text funds, so cap tests lead with enough text to pay for it
_FUNDING: Final[str] = "w" * 1024


def test_markdown_list_indent_stops_at_the_nesting_cap() -> None:
    assert parse(_NESTED_ITEMS).to_markdown() == _capped_lines("- ")


def _capped_lines(marker: str) -> str:
    return "\n".join("  " * min(level, 8) + marker for level in range(12)) + "x"


def test_text_list_indent_stops_at_the_nesting_cap() -> None:
    assert parse(f"<p>{_FUNDING}</p>{_NESTED_ITEMS}").to_text() == f"{_FUNDING}\n\n{_capped_lines('* ')}"


def test_markdown_blockquote_indent_stops_at_the_nesting_cap() -> None:
    assert parse(f"{'<blockquote>' * 22}x").to_markdown() == f"{'> ' * 17}x"


def test_text_blockquote_indent_stops_at_the_nesting_cap() -> None:
    text = parse(f"<p>{_FUNDING}</p>{'<blockquote>' * 22}x").to_text()
    assert text.rsplit("\n", 1)[-1] == f"{' ' * 68}x"


@pytest.mark.parametrize(
    ("source", "method", "expected"),
    [
        pytest.param("<ol><li>" * 8192, "to_markdown", 251_973, id="ordered-items-markdown"),
        pytest.param("<ol><li>" * 8192, "to_text", 55_537, id="ordered-items-text"),
        pytest.param("<blockquote>x<br>" * 4096, "to_markdown", 529_989, id="quoted-lines-markdown"),
        pytest.param("<blockquote>x<br>" * 4096, "to_text", 20_540, id="quoted-lines-text"),
    ],
)
def test_deep_nesting_output_stays_linear(source: str, method: str, expected: int) -> None:
    assert len(getattr(parse(source), method)()) == expected


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("<blockquote>a" * 400, id="quotes"),
        pytest.param("<ol><li>a" * 400, id="ordered"),
        pytest.param("<ul><li>a" * 400, id="unordered"),
        pytest.param("<blockquote>a" * 200 + "<ul><li>a" * 200, id="lists-in-quotes"),
    ],
)
def test_text_indent_spends_the_padding_budget(source: str) -> None:
    # the fuzz amplify lane's to_text bound
    assert len(parse(source).to_text()) <= 2 * len(source) + 64


def test_text_table_padding_stays_linear() -> None:
    source = _uneven_table(256, 128)
    assert len(parse(source).to_text()) <= 2 * len(source)


def _uneven_table(width: int, rows: int) -> str:
    return f"<table><tr><td>{'X' * width}</td><td>y</td></tr>{'<tr><td>x</td><td>y</td></tr>' * rows}</table>"


def test_text_table_sparse_rows_stay_linear() -> None:
    source = f"<table><tr>{'<td>x</td>' * 200}</tr>{'<tr><td>x</td></tr>' * 200}</table>"
    assert len(parse(source).to_text()) <= 2 * len(source)


def test_annotated_table_keeps_cell_spans_after_padding_limit() -> None:
    text, spans = parse(_uneven_table(64, 16)).to_annotated_text({"td": ["cell"]})
    assert [text[start:end] for start, end, _label in spans] == ["X" * 64, "y", *["x", "y"] * 16]


def test_google_doc_margin_indent_stops_at_the_nesting_cap() -> None:
    document = parse("<ul><li style='margin-left:99999999999px'>x</li><li style='margin-left:72px'>y</li></ul>")
    assert document.to_markdown(Markdown.google_doc()) == f"{'  ' * 8}- x\n    - y"


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("".join(f"<ul><li>L{level}" for level in range(12)), id="lists"),
        pytest.param("".join(f"<blockquote>Q{level}" for level in range(22)), id="quotes"),
        pytest.param(
            "".join(f"<blockquote>Q{level}" for level in range(3)) + "".join(f"<ul><li>L{level}" for level in range(9)),
            id="lists-in-quotes",
        ),
        pytest.param(
            "".join(f"<ul><li>L{level}" for level in range(8)) + "".join(f"<blockquote>Q{level}" for level in range(4)),
            id="quotes-in-lists",
        ),
    ],
)
def test_markdown_past_the_cap_reads_back_under_commonmark_nesting(source: str) -> None:
    # markdown-it's CommonMark preset stops reading blocks at nesting level 20
    labels = sorted(source.replace("<ul><li>", " ").replace("<blockquote>", " ").split())
    tokens = MarkdownIt("commonmark").parse(parse(source).to_markdown())
    assert sorted(token.content for token in tokens if token.type == "inline") == labels
