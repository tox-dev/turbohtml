from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Final, Literal

import pytest
from markdown_it import MarkdownIt

from turbohtml import Html, Indent, Markdown, PlainText, parse, parse_xml

if TYPE_CHECKING:
    from collections.abc import Callable

    from work_steps import WorkSteps

    from turbohtml import Document

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


# an unclosed formatting element: the tree builder reopens it, attributes and all, in every block that follows
def _clone_href(size: int) -> str:
    return f"<p>a<a href='{'u' * size}'>x{'<p>b' * size}"


def _clone_title(size: int) -> str:
    return f"<p>a<a href=u title='{'t' * size}'>x{'<p>b' * size}"


def _clone_language(size: int) -> str:
    return f"<p>a<code class=language-{'u' * size}>x{'<pre>b</pre>' * size}"


# longer than the allowance either renderer starts with, so only a start tag or rendered text can pay for a repeat
_LONG_TARGET: Final[str] = "u" * 300


@pytest.mark.parametrize(
    ("markup", "options"),
    [
        pytest.param(_clone_href, Markdown(), id="href"),
        pytest.param(_clone_href, Markdown(links=Markdown.Links(style="reference")), id="href-reference"),
        pytest.param(_clone_title, Markdown(), id="title"),
        pytest.param(_clone_language, Markdown(), id="fence-language"),
    ],
)
def test_markdown_clone_attributes_stay_linear(
    markup: Callable[[int], str], options: Markdown, work_steps: WorkSteps
) -> None:
    work_steps.assert_linear(lambda n: partial(parse(markup(n)).to_markdown, options), (500, 2000))


def _raw_clone_href(size: int) -> str:
    return f"<table><tr><td colspan=2>{_clone_href(size)}"


def _raw_clone_title(size: int) -> str:
    return f"<table><tr><td colspan=2><p>a<b title={'u' * size}>x{'<p>b' * size}"


def _raw_list_clone_title(size: int) -> str:
    return f"<div>z<table><tr><td><ul><li><b title={'u' * size}>x{'<li>y' * size}"


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param(_raw_clone_href, id="href"),
        pytest.param(_raw_clone_title, id="title"),
        pytest.param(_raw_list_clone_title, id="list-in-cell"),
    ],
)
def test_markdown_raw_html_clone_attributes_stay_linear(markup: Callable[[int], str], work_steps: WorkSteps) -> None:
    work_steps.assert_linear(lambda n: parse(markup(n)).to_markdown, (500, 2000))


@pytest.mark.parametrize("links", ["inline", "footnote"])
def test_text_clone_href_stays_linear(links: Literal["inline", "footnote"], work_steps: WorkSteps) -> None:
    work_steps.assert_linear(lambda n: partial(parse(_clone_href(n)).to_text, PlainText(links=links)), (500, 2000))


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("<p><a href=/u title=t>one<p>two", '[one](/u "t")\n\n[two](/u "t")', id="allowance-pays"),
        pytest.param(
            f"<a href={_LONG_TARGET}><p>moved</a>",
            f"[]({_LONG_TARGET})\n\n[moved]({_LONG_TARGET})",
            id="start-tag-pays",
        ),
        pytest.param(
            f"<p><a href={_LONG_TARGET}>one<p>two<p>three",
            f"[one]({_LONG_TARGET})\n\n[two]({_LONG_TARGET})\n\nthree",
            id="link-unpaid",
        ),
        pytest.param(
            f"<p><a href={_LONG_TARGET}>one<p>two<p>{'w' * 300}<p>four",
            f"[one]({_LONG_TARGET})\n\n[two]({_LONG_TARGET})\n\n{'w' * 300}\n\n[four]({_LONG_TARGET})",
            id="text-pays",
        ),
        pytest.param("<p><code class=language-py>x<pre>y</pre>", "`x`\n\n```py\ny\n```", id="fence-paid"),
        pytest.param(f"<p><code class=language-{_LONG_TARGET}>x<pre>y</pre>", "`x`\n\n```\ny\n```", id="fence-unpaid"),
        pytest.param(
            "<table><tr><td colspan=2><p><b title=t>one<p>two",
            '<table><tbody><tr><td colspan="2"><p><b title="t">one</b></p><p><b title="t">two</b></p></td></tr></tbody>'
            "</table>",
            id="raw-html-allowance-pays",
        ),
        pytest.param(
            f"<table><tr><td colspan=2><p><b title={_LONG_TARGET} id=i>one<p>two<p>three",
            f'<table><tbody><tr><td colspan="2"><p><b title="{_LONG_TARGET}" id="i">one</b></p>'
            f'<p><b title="{_LONG_TARGET}" id="i">two</b></p><p><b id="i">three</b></p></td></tr></tbody></table>',
            id="raw-html-attribute-unpaid",
        ),
    ],
)
def test_markdown_clone_attributes_spend_a_budget(source: str, expected: str) -> None:
    assert parse(source).to_markdown() == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            "<table><tr><td><a href='a  b'>x</a></td></tr></table>",
            '| <a href="a&#32;&#32;b">x</a> |\n| --- |',
            id="cell-link-target-with-space-run",
        ),
        pytest.param(
            "<table><tr><td><a href='a  b'>x<p>y",
            '| <a href="a&#32;&#32;b">x y</a> |\n| --- |',
            id="cell-link-clone",
        ),
        pytest.param(
            "<table><tr><td><b><i>x&amp;<table><tr><td colspan=2>y</td></tr></table>z</i></b></td></tr></table>",
            '| <strong><em>x&<table><tr><td colspan="2">y</td></tr></table>z</em></strong> |\n| --- |',
            id="emphasis-around-raw-table-in-cell",
        ),
        pytest.param(
            "<b><table><tr><th>a</th></tr><tr><td>b</td><td>c</td></tr></table></b>",
            "<table><tbody><tr><th><strong>a</strong></th></tr><tr><td><strong>b</strong></td>"
            "<td><strong>c</strong></td></tr></tbody></table>",
            id="marker-around-raw-table",
        ),
        pytest.param("<p><a>one<p>two", "one\n\ntwo", id="link-without-href"),
        pytest.param(
            "<p><code>x<pre>y</pre><pre>z</pre>", "`x`\n\n```\ny\n```\n\n```\nz\n```", id="fence-without-language"
        ),
    ],
)
def test_markdown_link_and_fence_shapes(source: str, expected: str) -> None:
    assert parse(source).to_markdown() == expected


def test_image_html_in_a_cell_keeps_its_tag() -> None:
    source = "<table><tr><td><img src=/a.png alt=a></td></tr></table>"
    assert parse(source).to_markdown(Markdown(images=Markdown.Images(mode="html"))) == (
        '| <img src="/a.png" alt="a"> |\n| --- |'
    )


@pytest.mark.parametrize(
    "write",
    [
        pytest.param(lambda document: document.html, id="html"),
        pytest.param(lambda document: document.serialize(), id="serialize"),
        pytest.param(lambda document: "".join(document.serialize_iter()), id="serialize-iter"),
    ],
)
def test_html_writers_keep_element_attributes(write: Callable[[Document], str]) -> None:
    assert write(parse("<p class=x>y")) == '<html><head></head><body><p class="x">y</p></body></html>'


@pytest.mark.parametrize(
    ("source", "links", "expected"),
    [
        pytest.param("<p><a href=/u>one<p>two", "inline", "one (/u)\n\ntwo (/u)", id="allowance-pays"),
        pytest.param(
            f"<a href={_LONG_TARGET}><p>moved</a>",
            "inline",
            f" ({_LONG_TARGET})\n\nmoved ({_LONG_TARGET})",
            id="start-tag-pays",
        ),
        pytest.param(
            f"<p><a href={_LONG_TARGET}>one<p>two<p>three",
            "footnote",
            f"one[1]\n\ntwo[2]\n\nthree\n\n[1] {_LONG_TARGET}\n[2] {_LONG_TARGET}",
            id="link-unpaid",
        ),
        pytest.param(
            f"<p><a href={_LONG_TARGET}>one<p>two<p>{'w' * 300}<p>four",
            "inline",
            f"one ({_LONG_TARGET})\n\ntwo ({_LONG_TARGET})\n\n{'w' * 300}\n\nfour ({_LONG_TARGET})",
            id="text-pays",
        ),
        pytest.param("<p><a href=/u>one<p>two", "none", "one\n\ntwo", id="links-none"),
        pytest.param("<p><a>one<p>two", "inline", "one\n\ntwo", id="link-without-href"),
    ],
)
def test_text_clone_href_spends_the_padding_budget(
    source: str, links: Literal["inline", "footnote", "none"], expected: str
) -> None:
    assert parse(source).to_text(PlainText(links=links)) == expected


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


def test_pretty_indent_stops_at_the_nesting_cap() -> None:
    depth: Final = 67
    options = Html(layout=Indent())
    document = parse_xml("<a>" * depth + "</a>" * depth)
    pretty = document.serialize(options)
    opened = [f"{'  ' * min(level, 64)}<a>" for level in range(depth - 1)]
    closed = [line.replace("<a>", "</a>") for line in reversed(opened)]
    assert pretty.split("\n") == [*opened, f"{'  ' * 64}<a></a>", *closed]
    assert "".join(document.serialize_iter(options)) == pretty


@pytest.mark.parametrize(
    "options",
    [
        pytest.param(Html(layout=Indent()), id="html"),
        pytest.param(Html(xml=True, layout=Indent()), id="xml"),
        pytest.param(Html(layout=Indent("\t")), id="tab"),
    ],
)
def test_pretty_deep_nesting_stays_linear(options: Html, work_steps: WorkSteps) -> None:
    work_steps.assert_linear(lambda depth: partial(_pretty_chain, depth, options), (500, 2000))


def _pretty_chain(depth: int, options: Html) -> str:
    return parse_xml("<a>" * depth + "</a>" * depth).serialize(options)
