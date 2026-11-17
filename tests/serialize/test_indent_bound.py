from __future__ import annotations

import re
from functools import partial
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal

import pytest
from markdown_it import MarkdownIt

from turbohtml import Html, Indent, Markdown, Minify, PlainText, parse, parse_xml
from turbohtml.clean import OnDisallowed, Policy, minify, sanitize

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from work_steps import WorkSteps

    from turbohtml import Document, Element

_NESTED_ITEMS: Final[str] = f"{'<ul><li>' * 12}x"
# plain-text indent spends a budget that source text funds, so cap tests lead with enough text to pay for it
_FUNDING: Final[str] = "w" * 1024
_LABEL: Final[re.Pattern[str]] = re.compile(r"[LQ]\d+")


def test_markdown_list_past_the_nesting_cap_is_written_as_html() -> None:
    assert parse(_NESTED_ITEMS).to_markdown() == f"{'<ul><li>' * 12}x{'</li></ul>' * 12}"


def test_text_list_indent_stops_at_the_nesting_cap() -> None:
    capped = "\n".join([*(f"{'  ' * min(level, 8)}* " for level in range(11)), f"{'  ' * 8}* x"])
    assert parse(f"<p>{_FUNDING}</p>{_NESTED_ITEMS}").to_text() == f"{_FUNDING}\n\n{capped}"


def test_markdown_blockquote_past_the_nesting_cap_is_written_as_html() -> None:
    assert parse(f"{'<blockquote>' * 22}x").to_markdown() == f"{'<blockquote>' * 22}x{'</blockquote>' * 22}"


def test_text_blockquote_indent_stops_at_the_nesting_cap() -> None:
    text = parse(f"<p>{_FUNDING}</p>{'<blockquote>' * 22}x").to_text()
    assert text.rsplit("\n", 1)[-1] == f"{' ' * 68}x"


def test_markdown_root_item_past_the_nesting_cap_stops_indenting() -> None:
    assert _root_item(f"{'<li><ul>' * 9}s").to_markdown() == "\n".join([*_root_item_markers(9), f"{' ' * 16}s"])


def _root_item(markup: str) -> Element:
    item = parse(markup).select_one("li")
    assert item is not None
    return item


# a list item rendered as the root is not walked for the cap, so its nested lists count from the item's indent
def _root_item_markers(count: int) -> list[str]:
    return [f"{'  ' * level}- " for level in range(count)]


def test_markdown_root_quote_past_the_nesting_cap_drops_its_marker() -> None:
    item = _root_item(f"{'<li><ul>' * 8}<blockquote><blockquote>q")
    lines = [*_root_item_markers(8), f"{' ' * 16}> ", f"{' ' * 16}>", f"{' ' * 16}> q"]
    assert item.to_markdown() == "\n".join(lines)


def test_markdown_root_code_past_the_nesting_cap_is_fenced() -> None:
    item = _root_item(f"{'<li><ul>' * 9}<pre>  c\n  d</pre>")
    fence = f"{' ' * 16}```"
    lines = [*_root_item_markers(9), fence, f"{' ' * 16}  c", f"{' ' * 16}  d", fence]
    assert item.to_markdown(Markdown(code=Markdown.Code(block_style="indented"))) == "\n".join(lines)


@pytest.mark.parametrize(
    ("source", "method", "expected"),
    [
        pytest.param("<ol><li>" * 8192, "to_markdown", 147_456, id="ordered-items-markdown"),
        pytest.param("<ol><li>" * 8192, "to_text", 55_537, id="ordered-items-text"),
        pytest.param("<blockquote>x<br>" * 4096, "to_markdown", 122_880, id="quoted-lines-markdown"),
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
# a target longer than the clone allowance plus the markup around it, so only the first clone can pay for it
_WIDE_TARGET: Final[str] = "u" * 1000


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


_DOCUMENT_WRITERS: Final[Mapping[str, Callable[[Document], str]]] = MappingProxyType({
    "html": lambda document: document.html,
    "inner-html": lambda document: document.inner_html,
    "inner-xml": lambda document: document.inner_xml,
    "serialize": lambda document: document.serialize(),
    "serialize-iter": lambda document: "".join(document.serialize_iter()),
    "serialize-xml": lambda document: document.serialize(Html(xml=True)),
    "indent": lambda document: document.serialize(Html(layout=Indent())),
    "minify-layout": lambda document: document.serialize(Html(layout=Minify())),
    "sorted": lambda document: document.serialize(Html(sort_attributes=True)),
    "to-source": lambda document: document.to_source(),
    "canonicalize": lambda document: document.canonicalize().decode(),
})
_STRING_WRITERS: Final[Mapping[str, Callable[[str], str]]] = MappingProxyType({
    "minify": minify,
    "sanitize": sanitize,
})


@pytest.mark.parametrize("write", _DOCUMENT_WRITERS.values(), ids=_DOCUMENT_WRITERS.keys())
def test_clone_attributes_stay_linear_in_each_writer(write: Callable[[Document], str], work_steps: WorkSteps) -> None:
    work_steps.assert_linear(lambda n: partial(write, parse(_clone_href(n))), (500, 2000))


@pytest.mark.parametrize("write", _STRING_WRITERS.values(), ids=_STRING_WRITERS.keys())
def test_clone_attributes_stay_linear_in_string_writers(write: Callable[[str], str], work_steps: WorkSteps) -> None:
    work_steps.assert_linear(lambda n: partial(write, _clone_href(n)), (500, 2000))


def test_clone_attributes_moved_into_an_xml_tree_stay_linear(work_steps: WorkSteps) -> None:
    work_steps.assert_linear(lambda n: partial(_xml_root_with(_clone_href(n)).serialize), (500, 2000))


# a clone moved into an XML tree keeps its implied flag, so the XML writers budget its attributes too
def _xml_root_with(markup: str) -> Element:
    root = parse_xml("<r xmlns:x='urn:x'/>").root
    body = parse(markup).select_one("body")
    assert root is not None
    assert body is not None
    root.append(body)
    return root


def test_clone_attributes_moved_into_an_xml_tree_canonicalize_stay_linear(work_steps: WorkSteps) -> None:
    work_steps.assert_linear(lambda n: partial(_xml_root_with(_clone_href(n)).canonicalize), (500, 2000))


def test_moved_clone_attributes_past_the_budget_are_left_out() -> None:
    text = _xml_root_with(f"<p><a href={_WIDE_TARGET}>one<p>two<p>three").canonicalize().decode()
    assert text.count(_WIDE_TARGET) == 2
    assert "three" in text


def test_moved_clone_namespace_declarations_past_the_budget_are_left_out() -> None:
    text = _xml_root_with(f"<p><a xmlns:y=urn:{_WIDE_TARGET} href=/u>one<p>two<p>three").canonicalize().decode()
    assert text.count("xmlns:y=") == 2
    assert text.count("href=") == 2


def test_clone_attributes_moved_back_into_an_html_tree_stay_linear(work_steps: WorkSteps) -> None:
    work_steps.assert_linear(lambda n: partial(getattr, _html_document_with(_clone_href(n)), "html"), (500, 2000))


# a clone that goes through an XML tree and back into an HTML one still keeps its implied flag
def _html_document_with(markup: str) -> Document:
    document = parse("<body>")
    body = document.select_one("body")
    assert body is not None
    body.append(_xml_root_with(markup))
    return document


def test_clone_attributes_moved_back_into_an_html_tree_past_the_budget_are_left_out() -> None:
    text = _html_document_with(f"<p><a href={_WIDE_TARGET}>one<p>two<p>three").html
    assert text.count(_WIDE_TARGET) == 2
    assert "three" in text


@pytest.mark.parametrize("write", _DOCUMENT_WRITERS.values(), ids=_DOCUMENT_WRITERS.keys())
def test_clone_attributes_past_the_budget_are_left_out(write: Callable[[Document], str]) -> None:
    text = write(parse(f"<p><a href={_WIDE_TARGET}>one<p>two<p>three"))
    assert text.count(_WIDE_TARGET) == 2
    assert "three" in text


@pytest.mark.parametrize("write", _STRING_WRITERS.values(), ids=_STRING_WRITERS.keys())
def test_clone_string_writers_leave_attributes_past_the_budget_out(write: Callable[[str], str]) -> None:
    text = write(f"<p><a href={_WIDE_TARGET}>one<p>two<p>three")
    assert text.count(_WIDE_TARGET) == 2
    assert "three" in text


@pytest.mark.parametrize("write", _DOCUMENT_WRITERS.values(), ids=_DOCUMENT_WRITERS.keys())
def test_clone_past_the_budget_writes_none_of_its_start_tag(write: Callable[[Document], str]) -> None:
    text = write(parse(f"<p><a title=t href={_WIDE_TARGET}>one<p>two<p>three"))
    assert text.count(_WIDE_TARGET) == 2
    assert text.count("title") == 2
    assert "three" in text


@pytest.mark.parametrize("write", _DOCUMENT_WRITERS.values(), ids=_DOCUMENT_WRITERS.keys())
def test_clone_keeps_its_value_once_its_source_is_removed(write: Callable[[Document], str]) -> None:
    document = parse(f"<p><a href={_WIDE_TARGET}>1<p>2")
    source = document.select_one("a")
    assert source is not None
    source.decompose()
    assert write(document).count(_WIDE_TARGET) == 1


def test_moved_clone_keeps_its_value_once_its_source_is_removed() -> None:
    root = _xml_root_with(f"<p><a href={_WIDE_TARGET}>1<p>2")
    source = root.select_one("a")
    assert source is not None
    source.decompose()
    assert root.canonicalize().decode().count(_WIDE_TARGET) == 1


def test_prefix_declared_clone_keeps_its_value_once_its_source_is_removed() -> None:
    document = parse(f"<p xmlns:y=urn:y><a href={_WIDE_TARGET}>1<p>2")
    source = document.select_one("a")
    assert source is not None
    source.decompose()
    assert document.canonicalize().decode().count(_WIDE_TARGET) == 1


def test_prefix_declared_clone_past_the_budget_writes_none_of_its_start_tag() -> None:
    text = parse(f"<p xmlns:y=urn:y><a y:b=1 title=t href={_WIDE_TARGET}>one<p>two<p>three").canonicalize().decode()
    assert text.count(_WIDE_TARGET) == 2
    assert text.count("title") == 2
    assert "three" in text


def test_xml_clone_leaves_out_an_attribute_name_xml_cannot_hold() -> None:
    text = parse('<p><a a"b=1 href=/u>one<p>two<p>three').serialize(Html(xml=True))
    assert text.count("href") == 3
    assert 'a"b' not in text


def test_xml_clone_keeps_only_the_namespace_bindings_xml_can_read() -> None:
    assert parse('<p><a xmlns:y="" href=/u>one<p>two').serialize(Html(xml=True)).count("xmlns:y") == 0
    bound = parse("<p><a xmlns:y=urn:y href=/u>one<p>two").serialize(Html(xml=True))
    assert bound.count('xmlns:y="urn:y"') == 2


def test_markdown_raw_clone_leaves_out_an_attribute_name_commonmark_cannot_read() -> None:
    source = '<table><tr><td colspan=2><p><b a"b=1 title=t>one<p>two'
    assert parse(source).to_markdown() == (
        '<table><tbody><tr><td colspan="2"><p><b title="t">one</b></p><p><b title="t">two</b></p></td></tr></tbody>'
        "</table>"
    )


@pytest.mark.parametrize(
    "policy",
    [
        pytest.param(
            Policy(
                tags=frozenset({"a", "p"}), attributes={"*": frozenset({"href"})}, on_disallowed_tag=OnDisallowed.REMOVE
            ),
            id="remove",
        ),
        pytest.param(
            Policy(
                tags=frozenset({"a", "p"}),
                attributes={"*": frozenset({"href"})},
                remove_with_content=frozenset({"span"}),
            ),
            id="remove-with-content",
        ),
    ],
)
def test_clone_keeps_its_href_once_its_source_is_removed(policy: Policy) -> None:
    text = sanitize(f"<span><a href={_WIDE_TARGET}>1</span><p>2", policy)
    assert text == f'<p><a href="{_WIDE_TARGET}">2</a></p>'


def test_clones_of_many_value_sets_each_keep_their_attributes() -> None:
    source = "".join(f"<p><a href=/{index}>x<p>y" for index in range(64))
    assert parse(source).html.count("href=") == 128


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


def test_text_clone_href_of_a_removed_source_is_kept_once() -> None:
    document = parse(f"<p><a href={_LONG_TARGET}>one<p>two<p>three")
    source = document.select_one("a")
    assert source is not None
    source.decompose()
    assert document.to_text(PlainText(links="inline")) == f"two ({_LONG_TARGET})\n\nthree"


@pytest.mark.parametrize("header", ["detect", "none"])
def test_markdown_table_padding_stays_linear(header: Literal["detect", "none"], work_steps: WorkSteps) -> None:
    options: Final = Markdown(tables=Markdown.Tables(pad=True, header=header))
    work_steps.assert_linear(lambda n: partial(parse(_wide_cell_over_rows(n)).to_markdown, options), (500, 2000))


def _wide_cell_over_rows(size: int) -> str:
    return f"<table><tr><td>{'x' * size}{'<tr><td>' * size}</table>"


@pytest.mark.parametrize("header", ["detect", "none"])
def test_markdown_table_past_the_padding_budget_is_written_unpadded(header: Literal["detect", "none"]) -> None:
    source: Final = f"<table><tr><td>{'x' * 2048}<td>y{'<tr><td>a<td>' * 256}</table>"
    padded: Final = parse(source).to_markdown(Markdown(tables=Markdown.Tables(pad=True, header=header)))
    # no row is padded, so the table is the one the unpadded layout writes, and a reader still takes every row
    assert padded == parse(source).to_markdown(Markdown(tables=Markdown.Tables(header=header)))
    tokens: Final = MarkdownIt("commonmark").enable("table").parse(padded)
    # 257 rows under the empty header a table without header cells takes
    assert sum(token.type == "tr_open" for token in tokens) == 258


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(f"<table>{'<tr><td>1</td><td>1</td><td>1</td><td>1</td>' * 40}</table>", id="short-cells"),
        pytest.param(f"<table>{'<tr><td><code>a</code><tr><td><code>aaaa</code>' * 32}</table>", id="code-cells"),
        pytest.param(f"<table>{'<tr><td><td>' * 50}</table>", id="empty-cells"),
        pytest.param(
            f"<table>{'<tr><td>a<td>short note' * 4}<tr><td>a<td>{'n' * 70}{'<tr><td>a<td>short note' * 8}</table>",
            id="one-long-note",
        ),
        pytest.param(
            f"<table><tr><td>{'x' * 300}{'<tr><td>' * 40}</table><table>{'<tr><td>a<td>a<td>a<td>a' * 10}</table>",
            id="table-after-an-unpadded-one",
        ),
    ],
)
@pytest.mark.parametrize("header", ["detect", "none"])
def test_markdown_table_cells_fund_their_own_padding(source: str, header: Literal["detect", "none"]) -> None:
    output: Final = parse(source).to_markdown(Markdown(tables=Markdown.Tables(pad=True, header=header)))
    assert len({len(line) for line in output.split("\n\n")[-1].split("\n")}) == 1


def test_markdown_table_padding_budget_is_spent_once() -> None:
    wide: Final = f"<table><tr><td>{'x' * 120}{'<tr><td>' * 5}</table>"
    output: Final = parse(wide * 2).to_markdown(Markdown(tables=Markdown.Tables(pad=True)))
    # the first table aligns on the allowance the document starts with, which the second can no longer draw on
    assert [len({len(line) for line in table.split("\n")}) == 1 for table in output.split("\n\n")] == [True, False]


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
        pytest.param("".join(f"<ol><li>L{level}" for level in range(12)), id="ordered-lists"),
    ],
)
def test_markdown_past_the_cap_reads_back_under_commonmark_nesting(source: str) -> None:
    # markdown-it's CommonMark preset stops reading blocks at nesting level 20, so past the cap the reader takes the
    # written HTML as one raw block; the labels it shows are the ones the source holds
    labels = sorted(_LABEL.findall(source))
    rendered = parse(MarkdownIt("commonmark").render(parse(source).to_markdown()))
    assert sorted(_LABEL.findall(rendered.text)) == labels


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
