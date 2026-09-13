"""Layout-aware text export via Node.to_text() (the inscriptis role).

Golden cases pin the layout for every block, list, table, link, and image path,
and a token check confirms no visible text is dropped. A second helper drives the
binding's enum validation.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

import pytest

from turbohtml import Markdown, PlainText, parse

if TYPE_CHECKING:
    from collections.abc import Mapping
if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    Rules = Mapping[str, Sequence[str]]


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


def annotate(html: str, rules: Rules) -> tuple[str, list[tuple[int, int, str]]]:
    return parse(html).to_annotated_text(rules)


def spans_text(html: str, rules: Rules) -> list[tuple[str, str]]:
    """Return (label, covered-text) pairs, the offset-independent view."""
    text, labels = annotate(html, rules)
    return [(label, text[start:end]) for start, end, label in labels]


def call_with(convert: Callable[..., object], *args: object, **kwargs: str | int) -> object:
    """Invoke the converter with arguments the static signature would reject, to drive
    the runtime validation; the converter arrives signature-erased on purpose."""
    return convert(*args, **kwargs)


@pytest.mark.parametrize(
    ("html", "rules", "expected"),
    [
        pytest.param(
            "<h1>Title</h1><p>Some <b>bold</b> words.</p>",
            {"h1": ["heading"], "b": ["emphasis"]},
            [("heading", "Title"), ("emphasis", "bold")],
            id="tag-rules",
        ),
        pytest.param(
            "<p>plain</p><p class='lead intro'>special</p>",
            {"p#class=lead": ["lead"]},
            [("lead", "special")],
            id="tag-attr-value",
        ),
        pytest.param(
            "<a href='/x' rel='nofollow'>link</a><a href='/y'>plain</a>",
            {"a#rel": ["tracked"]},
            [("tracked", "link")],
            id="tag-attr-present",
        ),
        pytest.param(
            "<p class='note'>x</p><b class='note'>y</b><i>z</i>",
            {"#class=note": ["noted"]},
            [("noted", "x"), ("noted", "y")],
            id="any-tag-attr-value",
        ),
        pytest.param(
            "<p id='a'>one</p><p>two</p>",
            {"#id": ["has-id"]},
            [("has-id", "one")],
            id="any-tag-attr-present",
        ),
        pytest.param(
            "<p>a <b><i>both</i></b> c</p>",
            {"b": ["bold"], "i": ["italic"]},
            [("italic", "both"), ("bold", "both")],
            id="nested",
        ),
        pytest.param(
            "<h2>Head</h2>",
            {"h2": ["heading", "important"]},
            [("heading", "Head"), ("important", "Head")],
            id="multiple-labels",
        ),
        pytest.param("<p>x</p>", {}, [], id="empty-rules"),
        pytest.param("<p>x</p>", {"b": ["bold"]}, [], id="no-match"),
        pytest.param(
            "<table><tr><th>Name</th></tr><tr><td>Bob</td></tr></table>",
            {"th": ["header"], "td": ["cell"]},
            [("header", "Name"), ("cell", "Bob")],
            id="table-cells",
        ),
        pytest.param(
            "<p>plain</p><svg><desc>x</desc></svg>",
            {"desc": ["svg-desc"]},
            [],
            id="foreign-element-not-matched",
        ),
        pytest.param(
            "<p class=''>empty</p><p>x</p>",
            {"p#class=note": ["noted"]},
            [],
            id="valueless-no-match",
        ),
        pytest.param(
            "<p class='lead intro'>x</p>",
            {"#class=intro": ["second"]},
            [("second", "x")],
            id="value-matches-second-token",
        ),
        pytest.param("<p class='other '>x</p>", {"#class=note": ["noted"]}, [], id="value-no-token-matches"),
        pytest.param("<p>keep</p><b></b>", {"b": ["bold"]}, [], id="empty-element-no-span"),
        pytest.param("<p class='lead'>x</p>", {"#class=lear": ["noted"]}, [], id="value-same-length-differs"),
        pytest.param(
            "<p>intro</p><h2>Head</h2>",
            {"h2": ["heading"]},
            [("heading", "Head")],
            id="block-not-first-trims-leading",
        ),
        pytest.param("<div><p>a<br></p></div>", {"p": ["para"]}, [("para", "a")], id="trailing-break-trimmed"),
    ],
)
def test_annotation_spans(html: str, rules: Rules, expected: list[tuple[str, str]]) -> None:
    assert spans_text(html, rules) == expected


def test_offsets_are_exact() -> None:
    text, labels = annotate("<h1>Title</h1><p>a <b>bold</b> b</p>", {"h1": ["h"], "b": ["e"]})
    assert text == "Title\n\na bold b"
    assert labels == [(0, 5, "h"), (9, 13, "e")]


def test_table_cell_offsets_map_into_the_grid() -> None:
    text, labels = annotate(
        "<table><tr><th>Name</th><th>Age</th></tr><tr><td>Alice</td><td>30</td></tr></table>",
        {"td": ["cell"]},
    )
    assert text == "Name   Age\nAlice  30"
    assert [(text[s:e]) for s, e, _ in labels] == ["Alice", "30"]


def test_content_inside_a_cell_is_not_annotated() -> None:
    # the cell renders into a throwaway buffer, so inner elements get no span
    _, labels = annotate("<table><tr><td>a <b>x</b></td></tr></table>", {"b": ["bold"], "td": ["cell"]})
    assert [label for _, _, label in labels] == ["cell"]


def test_leading_whitespace_shifts_offsets() -> None:
    text, labels = annotate("   <h1>Hi</h1>", {"h1": ["h"]})
    assert text == "Hi"
    assert labels == [(0, 2, "h")]


def test_links_and_options_compose_with_annotations() -> None:
    text, _labels = annotate("<p><a href='http://x'>site</a></p>", {"a": ["link"]})
    assert text == "site"
    text2, labels2 = parse("<p><a href='http://x'>site</a></p>").to_annotated_text(
        {"a": ["link"]}, PlainText(links="inline")
    )
    assert text2 == "site (http://x)"
    assert [text2[s:e] for s, e, _ in labels2] == ["site (http://x)"]


def test_annotated_text_rejects_another_renderers_config() -> None:
    with pytest.raises(TypeError, match="options must be a PlainText, not Markdown"):
        parse("<p>x</p>").to_annotated_text({"a": ["link"]}, Markdown())  # ty: ignore[invalid-argument-type]  # wrong config class


def test_many_spans_grow_the_buffer() -> None:
    html = "<p>" + "".join(f"<b>{i}</b>" for i in range(20)) + "</p>"
    _, labels = annotate(html, {"b": ["bold"]})
    assert len(labels) == 20


def test_deeply_nested_annotations_grow_the_active_stack() -> None:
    text, labels = annotate("<span>" * 12 + "deep" + "</span>" * 12, {"span": ["s"]})
    assert len(labels) == 12
    assert {text[s:e] for s, e, _ in labels} == {"deep"}


@pytest.mark.parametrize(
    ("rules", "exc", "match"),
    [
        pytest.param("notadict", TypeError, "dict", id="rules-not-dict"),
        pytest.param({5: ["x"]}, TypeError, "string", id="key-not-string"),
        pytest.param({"b": "bold"}, TypeError, "list", id="value-is-string"),
        pytest.param({"b": 5}, TypeError, "iterable", id="value-not-iterable"),
    ],
)
def test_invalid_rules(rules: object, exc: type[Exception], match: str) -> None:
    with pytest.raises(exc, match=match):
        call_with(parse("<p>x</p>").to_annotated_text, rules)


@pytest.mark.parametrize(
    ("kwargs", "exc", "match"),
    [
        pytest.param({"width": "x"}, TypeError, "int", id="width-wrong-type"),
        pytest.param({"links": "bad"}, ValueError, "links", id="invalid-links"),
        pytest.param({"layout": "bad"}, ValueError, "layout", id="invalid-layout"),
    ],
)
def test_annotated_text_invalid_options(kwargs: Mapping[str, str], exc: type[Exception], match: str) -> None:
    # a bad value reaches the renderer through PlainText and is rejected there
    with pytest.raises(exc, match=match):
        parse("<p>x</p>").to_annotated_text({"p": ["para"]}, PlainText(**kwargs))  # ty: ignore[invalid-argument-type]  # pass invalid options to test they are rejected


def test_annotated_text_explicit_none_is_the_default() -> None:
    page = parse("<p>a <b>b</b> c</p>")
    assert page.to_annotated_text({"b": ["x"]}, None) == page.to_annotated_text({"b": ["x"]})


def test_annotated_text_options_must_be_a_plain_text() -> None:
    with pytest.raises(TypeError, match="options must be a PlainText"):
        parse("<p>x</p>").to_annotated_text({"p": ["para"]}, object())  # ty: ignore[invalid-argument-type]  # pass a non-PlainText to test the type error


def test_rules_are_required() -> None:
    with pytest.raises(TypeError):
        parse("<p>x</p>").to_annotated_text()  # ty: ignore[missing-argument]  # annotation_rules is required


@pytest.mark.parametrize("count", [pytest.param(7, id="linear"), pytest.param(8, id="indexed")])
@pytest.mark.parametrize(
    ("html", "rules", "expected"),
    [
        pytest.param(
            '<span data-x="yes" data-y="yes">x</span>',
            {"#data-x": ["first"], "span": ["tag", "tag"], "#data-y": ["last"]},
            ("x", [(0, 1, "last"), (0, 1, "tag"), (0, 1, "tag"), (0, 1, "first")]),
            id="interleaved-and-duplicate-labels",
        ),
        pytest.param(
            '<p data-x="yes">a <b data-x="yes">b</b></p>',
            {"#data-x": ["wild"], "p": ["block"], "b": ["bold"]},
            ("a b", [(2, 3, "bold"), (2, 3, "wild"), (0, 3, "block"), (0, 3, "wild")]),
            id="nested-overlap",
        ),
        pytest.param(
            '<span class="no yes other">x</span><span class="yesterday">y</span>',
            {"span#class=yes": ["token"], "#class=no": ["wild"]},
            ("xy", [(0, 1, "wild"), (0, 1, "token")]),
            id="attribute-token",
        ),
        pytest.param(
            "<table><tr><td>a <b>x</b></td></tr></table>",
            {"b": ["bold"], "td": ["cell"]},
            ("a x", [(0, 3, "cell")]),
            id="table-cell-suppression",
        ),
        pytest.param(
            '<svg data-x="yes"><desc>x</desc></svg>',
            {"svg": ["foreign"], "#data-x": ["wild"]},
            ("x", [(0, 1, "wild")]),
            id="foreign-wildcard-only",
        ),
    ],
)
def test_annotation_rule_grouping_preserves_order(
    count: int, html: str, rules: dict[str, list[str]], expected: tuple[str, list[tuple[int, int, str]]]
) -> None:
    unrelated: Final = ("h1", "h2", "h3", "h4", "h5", "h6", "aside", "nav")
    padded: Final = {**rules, **{tag: [tag] for tag in unrelated[: count - len(rules)]}}
    assert annotate(html, padded) == expected
