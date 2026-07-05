"""Layout-aware text export via Node.to_text() (the inscriptis role).

Golden cases pin the layout for every block, list, table, link, and image path,
and a token check confirms no visible text is dropped. A second helper drives the
binding's enum validation.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest

from turbohtml import Markdown, PlainText, parse

if TYPE_CHECKING:
    from collections.abc import Mapping


def to_text(html: str, opts: PlainText) -> str:
    return parse(html).to_text(opts)


@pytest.mark.parametrize(
    ("html", "opts", "expected"),
    [
        pytest.param("<h1>Title</h1><p>Body text.</p>", PlainText(), "Title\n\nBody text.", id="heading-and-para"),
        pytest.param("<p>a <b>bold</b> <i>x</i> c</p>", PlainText(), "a bold x c", id="inline-markup-stripped"),
        pytest.param("<p>one</p><p>two</p>", PlainText(), "one\n\ntwo", id="paragraphs"),
        pytest.param("<div>a\n  b\tc</div>", PlainText(), "a b c", id="collapse-whitespace"),
        # &#13; injects a real U+000D past the CR->LF preprocessor fold; CR is HTML
        # whitespace, so a text-node run of it must collapse like any other run.
        pytest.param("<div>a&#13;&#13;b</div>", PlainText(), "a b", id="collapse-carriage-return"),
        pytest.param("<p>a<br>b</p>", PlainText(), "a\nb", id="line-break"),
        pytest.param("<p>a<wbr>b</p>", PlainText(), "ab", id="wbr"),
        pytest.param("<section><p>x</p></section>", PlainText(), "x", id="transparent-container"),
        pytest.param("<head><title>t</title></head><body>x</body>", PlainText(), "x", id="head-skipped"),
        pytest.param("<p>a</p><script>s()</script><p>b</p>", PlainText(), "a\n\nb", id="script-skipped"),
        pytest.param("<p>x<svg><style>.c{fill:red}</style></svg>y</p>", PlainText(), "xy", id="svg-style-suppressed"),
        pytest.param("<p>x<svg><script>a=1</script></svg>y</p>", PlainText(), "xy", id="svg-script-suppressed"),
    ],
)
def test_blocks(html: str, opts: PlainText, expected: str) -> None:
    assert to_text(html, opts) == expected


@pytest.mark.parametrize(
    ("html", "opts", "expected"),
    [
        pytest.param("<ul><li>one</li><li>two</li></ul>", PlainText(), "* one\n* two", id="ul"),
        pytest.param("<ol><li>a</li><li>b</li></ol>", PlainText(), "1. a\n2. b", id="ol"),
        pytest.param('<ol start="3"><li>a</li></ol>', PlainText(), "3. a", id="ol-start"),
        pytest.param('<ol start="x"><li>a</li></ol>', PlainText(), "1. a", id="ol-start-invalid"),
        pytest.param("<ul><li>a<ul><li>b</li></ul></li></ul>", PlainText(), "* a\n  * b", id="nested"),
        pytest.param("<ul><li>x</li></ul>", PlainText(bullet="- "), "- x", id="custom-bullet"),
        pytest.param("<ul><li> <ul><li>x</li></ul></li></ul>", PlainText(), "*\n  * x", id="li-leading-ws"),
        pytest.param("<ul><li></li></ul>", PlainText(), "*", id="empty-li"),
        pytest.param(
            "<ul><li>A</li><ul><li>B</li><li>C</li></ul><li>D</li></ul>",
            PlainText(),
            "* A\n  * B\n  * C\n* D",
            id="list-nested-directly-in-list",
        ),
        pytest.param(
            "<ul><li>A</li><ol><li>B</li></ol><menu><li>C</li></menu></ul>",
            PlainText(),
            "* A\n  1. B\n  * C",
            id="ordered-and-menu-nested-directly-in-list",
        ),
        pytest.param(
            '<ol><li value="100">Coffee</li><li>Tea</li><li>Milk</li></ol>',
            PlainText(),
            "100. Coffee\n101. Tea\n102. Milk",
            id="li-value-sets-ordinal",
        ),
        pytest.param(
            '<ol><li>a</li><li value="7">b</li><li>c</li></ol>',
            PlainText(),
            "1. a\n7. b\n8. c",
            id="li-value-restarts-run",
        ),
    ],
)
def test_lists(html: str, opts: PlainText, expected: str) -> None:
    assert "\n".join(line.rstrip() for line in to_text(html, opts).splitlines()) == expected


@pytest.mark.parametrize(
    ("html", "opts", "expected"),
    [
        pytest.param(
            "<table><tr><th>Name</th><th>Age</th></tr><tr><td>Alice</td><td>30</td></tr></table>",
            PlainText(),
            "Name   Age\nAlice  30",
            id="aligned",
        ),
        pytest.param(
            "<table><tr><td>a</td><td>b</td></tr><tr><td>c</td></tr></table>",
            PlainText(),
            "a  b\nc",
            id="ragged",
        ),
        pytest.param(
            "<table><tr><td>a</td><td>b</td></tr></table>",
            PlainText(table_cell_separator=" | "),
            "a | b",
            id="custom-separator",
        ),
        pytest.param(
            "<table><tr><!--c--><td>a</td></tr><tr><td>bb</td></tr></table>",
            PlainText(),
            "a\nbb",
            id="comment-in-row",
        ),
    ],
)
def test_tables(html: str, opts: PlainText, expected: str) -> None:
    assert "\n".join(line.rstrip() for line in to_text(html, opts).splitlines()) == expected


@pytest.mark.parametrize(
    ("html", "opts", "expected"),
    [
        pytest.param("<pre>a\n  b</pre>", PlainText(), "a\n  b", id="pre-preserved"),
        pytest.param("<pre>x\n</pre>", PlainText(), "x", id="pre-trailing-newline"),
        pytest.param(
            "<p>before</p><blockquote><p>quote</p></blockquote>",
            PlainText(),
            "before\n\n    quote",
            id="blockquote-indented",
        ),
        pytest.param("<blockquote><p>q</p></blockquote>", PlainText(), "    q", id="blockquote-first"),
    ],
)
def test_pre_and_quote(html: str, opts: PlainText, expected: str) -> None:
    assert to_text(html, opts) == expected


@pytest.mark.parametrize(
    ("html", "opts", "expected"),
    [
        pytest.param('<p>see <a href="/x">here</a></p>', PlainText(), "see here", id="links-none-default"),
        pytest.param(
            '<p>see <a href="http://x">here</a></p>',
            PlainText(links="inline"),
            "see here (http://x)",
            id="links-inline",
        ),
        pytest.param(
            '<p><a href="/a">A</a> <a href="/b">B</a></p>',
            PlainText(links="footnote"),
            "A[1] B[2]\n\n[1] /a\n[2] /b",
            id="links-footnote",
        ),
        pytest.param("<p><a>no href</a></p>", PlainText(links="inline"), "no href", id="link-no-href"),
        pytest.param('<p>a<img src="i" alt="cat">b</p>', PlainText(), "ab", id="images-off-default"),
        pytest.param('<p>a <img src="i" alt="cat"> b</p>', PlainText(images=True), "a cat b", id="images-alt"),
        pytest.param(
            '<p>a <img src="i"> b</p>',
            PlainText(images=True, default_image_alt="img"),
            "a img b",
            id="images-default-alt",
        ),
        pytest.param('<p>a<img src="i">b</p>', PlainText(images=True), "ab", id="images-no-alt"),
    ],
)
def test_links_and_images(html: str, opts: PlainText, expected: str) -> None:
    assert to_text(html, opts) == expected


@pytest.mark.parametrize(
    ("html", "opts", "expected"),
    [
        pytest.param("<p>a<span>x<!--c-->y</span>b</p>", PlainText(), "axyb", id="comment-in-inline"),
        pytest.param("<p>a<span><script>s</script>y</span></p>", PlainText(), "ay", id="script-in-inline"),
        pytest.param("<div><span>a<h2>H</h2>b</span></div>", PlainText(), "a\n\nHb", id="block-in-inline"),
        pytest.param("<p>a<template>t</template>b</p>", PlainText(), "atb", id="template-in-inline"),
        pytest.param("<ul><li><!--c-->x</li></ul>", PlainText(), "* x", id="li-leading-comment"),
        pytest.param("<ul><li><script>s</script>x</li></ul>", PlainText(), "* x", id="li-leading-script"),
        pytest.param("<div><p>a</p><!--c--><p>b</p></div>", PlainText(), "a\n\nb", id="comment-between-blocks"),
        pytest.param('<ol start="2x"><li>a</li></ol>', PlainText(), "2. a", id="ol-start-digits-then-letter"),
        pytest.param("<ul><svg></svg><li>a</li></ul>", PlainText(), "* a", id="foreign-in-list"),
        pytest.param("<ul><!--c--><li>a</li></ul>", PlainText(), "* a", id="comment-in-list"),
        pytest.param("<ul><template></template><li>a</li></ul>", PlainText(), "* a", id="non-li-element-in-list"),
        pytest.param("<ul><li><svg></svg>x</li></ul>", PlainText(), "* x", id="foreign-leads-li"),
        pytest.param('<ol start="-5"><li>a</li></ol>', PlainText(), "1. a", id="ol-negative-start"),
        pytest.param("<p>a<br> b</p>", PlainText(), "a\nb", id="break-then-space"),
        pytest.param("<pre></pre>", PlainText(), "", id="pre-empty"),
        pytest.param(
            '<p>a <img src="i"> b</p>',
            PlainText(images=True, default_image_alt="an img"),
            "a an img b",
            id="alt-spaces",
        ),
    ],
)
def test_text_edge_cases(html: str, opts: PlainText, expected: str) -> None:
    assert to_text(html, opts) == expected


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param("<table><tr><td>x<br>y</td></tr><tr><td>z</td></tr></table>", "x y\nz", id="cell-newline"),
        pytest.param("<table><!--c--><tr><td>a</td></tr><tr><td>b</td></tr></table>", "a\nb", id="comment-table-child"),
        pytest.param("<table></table>", "", id="empty-table"),
        pytest.param("<table><tr></tr></table>", "", id="row-no-cells"),
        pytest.param(
            "<table><caption>c</caption><thead><tr><th>H</th></tr></thead>"
            "<tbody><tr><td>a</td></tr></tbody><tfoot><tr><td>f</td></tr></tfoot></table>",
            "H\na\nf",
            id="full-sections",
        ),
        pytest.param("<table><tr><template></template><th>H</th><td>x</td></tr></table>", "H  x", id="th-and-template"),
    ],
)
def test_table_edge_cases(html: str, expected: str) -> None:
    assert "\n".join(line.rstrip() for line in parse(html).to_text().splitlines()) == expected


def test_blockquote_in_tight_item() -> None:
    out = parse("<ul><li>x<blockquote><p>q</p></blockquote></li></ul>").to_text()
    assert out == "* x\n      q"


def test_loose_block_in_tight_item() -> None:
    out = parse("<ul><li>x<table><tr><td>y</td></tr></table></li></ul>").to_text()
    assert out == "* x\n  y"


def test_footnote_links_grow_past_initial_capacity() -> None:
    html = "<p>" + "".join(f'<a href="/{i}">L{i}</a>' for i in range(10)) + "</p>"
    out = parse(html).to_text(PlainText(links="footnote"))
    assert "[10] /9" in out


def test_word_wrap() -> None:
    out = parse("<p>" + "word " * 10 + "</p>").to_text(PlainText(width=20))
    assert all(len(line) <= 20 for line in out.splitlines())
    assert out.split() == ["word"] * 10


def test_strict_layout_runs() -> None:
    assert parse("<div>a</div><div>b</div>").to_text(PlainText(layout="strict")) == "a\n\nb"


def test_explicit_none_is_the_default() -> None:
    page = parse("<h1>T</h1><p>body</p>")
    assert page.to_text(None) == page.to_text()


def test_options_must_be_a_plain_text() -> None:
    with pytest.raises(TypeError, match="options must be a PlainText"):
        parse("<p>x</p>").to_text(object())  # ty: ignore[invalid-argument-type]  # pass a non-PlainText to test the type error


def test_rejects_another_renderers_config() -> None:
    with pytest.raises(TypeError, match="options must be a PlainText, not Markdown"):
        parse("<p>x</p>").to_text(Markdown())  # ty: ignore[invalid-argument-type]  # the wrong config class is rejected


def test_to_text_on_subtree_and_text_node() -> None:
    doc = parse("<article><h2>T</h2><p>body</p></article>")
    article = doc.find("article")
    assert article is not None
    assert article.to_text() == "T\n\nbody"
    para = doc.find("p")
    assert para is not None
    assert para.children[0].to_text() == "body"


def test_to_text_on_foreign_and_template() -> None:
    svg = parse("<p><svg><desc>cap</desc></svg></p>").find("svg")
    assert svg is not None
    assert svg.to_text() == "cap"
    template = parse("<template><p>inside</p></template>").find("template")
    assert template is not None
    assert template.to_text() == "inside"


@pytest.mark.parametrize(
    ("opts", "exc", "match"),
    [
        pytest.param({"links": "nope"}, ValueError, "links", id="invalid-links"),
        pytest.param({"layout": "nope"}, ValueError, "layout", id="invalid-layout"),
        pytest.param({"links": 5}, TypeError, "string", id="non-string-enum"),
        pytest.param({"width": "x"}, TypeError, "int", id="wrong-type-width"),
        pytest.param({"bogus": 1}, TypeError, "keyword", id="unknown-keyword"),
    ],
)
def test_invalid_options(opts: Mapping[str, str | int], exc: type[Exception], match: str) -> None:
    # an unknown name is rejected when the config is built; a bad value reaches the
    # renderer through PlainText and is rejected there, so one call covers both
    with pytest.raises(exc, match=match):
        parse("<p>x</p>").to_text(PlainText(**opts))  # ty: ignore[invalid-argument-type]  # pass invalid options to test they are rejected


_WORD = re.compile(r"[0-9a-z]+")


@pytest.mark.parametrize(
    "html",
    [
        pytest.param("<h1>Hello World</h1><p>Some text with <b>bold</b> bits.</p>", id="article"),
        pytest.param("<ul><li>apple</li><li>banana</li></ul>", id="list"),
        pytest.param("<table><tr><th>Name</th><th>Age</th></tr><tr><td>Alice</td><td>30</td></tr></table>", id="table"),
        pytest.param("<blockquote><p>a quote here</p></blockquote><p>and more</p>", id="quote"),
    ],
)
def test_no_visible_text_lost(html: str) -> None:
    source = _WORD.findall(" ".join(parse(html).stripped_strings).lower())
    rendered = _WORD.findall(parse(html).to_text().lower())
    assert rendered == source
