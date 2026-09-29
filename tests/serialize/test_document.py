"""The Indent layout adds whitespace only where the default CSS renders none, so the
reparsed page reads the same and indenting the output again reproduces it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest

from turbohtml import Element, Html, Indent, Text, parse, parse_fragment, parse_xml

if TYPE_CHECKING:
    from wpt_tree_corpus import WptHtmlTreeCorpus

_PRETTY: Final = Html(layout=Indent())


def _pretty(html: str, selector: str) -> str:
    node = parse(html).select_one(selector)
    assert node is not None
    return node.serialize(_PRETTY)


@pytest.mark.parametrize(
    ("html", "selector", "expected"),
    [
        pytest.param("<p>a<b>b</b>c</p>", "p", "<p>a<b>b</b>c</p>", id="inline-in-paragraph"),
        pytest.param(
            "<div><span>a</span><span>b</span></div>", "div", "<div><span>a</span><span>b</span></div>", id="spans"
        ),
        pytest.param('<p><a href="x">link</a>, more</p>', "p", '<p><a href="x">link</a>, more</p>', id="anchor"),
        pytest.param(
            "<table><tr><td>a</td><td>b <i>c</i></td></tr></table>",
            "tr",
            "<tr>\n  <td>a</td>\n  <td>b <i>c</i></td>\n</tr>",
            id="table-cells",
        ),
        pytest.param(
            "<math><mi>x</mi><mo>+</mo></math>", "math", "<math>\n  <mi>x</mi>\n  <mo>+</mo>\n</math>", id="mathml"
        ),
        pytest.param(
            "<div>text<div>block</div>tail</div>",
            "div",
            "<div>\n  text\n  <div>block</div>\n  tail\n</div>",
            id="runs-between-blocks",
        ),
        pytest.param(
            "<div> a <b>b</b> <p>x</p> c </div>",
            "div",
            "<div>\n  a <b>b</b>\n  <p>x</p>\n  c\n</div>",
            id="run-edges-trimmed",
        ),
        pytest.param(
            "<div>\n  <p>x</p>\n  <p>y</p>\n</div>",
            "div",
            "<div>\n  <p>x</p>\n  <p>y</p>\n</div>",
            id="whitespace-between-blocks-dropped",
        ),
        pytest.param("<div> </div>", "div", "<div></div>", id="whitespace-only-block"),
        pytest.param("<div><!--c--><p>x</p></div>", "div", "<div>\n  <!--c-->\n  <p>x</p>\n</div>", id="comment-run"),
        pytest.param(
            "<svg><text><tspan>a</tspan><tspan>b</tspan></text></svg>",
            "svg",
            "<svg>\n  <text><tspan>a</tspan><tspan>b</tspan></text>\n</svg>",
            id="svg-text-verbatim",
        ),
        pytest.param("<svg>\n<g></g></svg>", "svg", "<svg>\n<g></g></svg>", id="foreign-with-text-verbatim"),
        pytest.param("<div><pre> a </pre></div>", "div", "<div>\n  <pre> a </pre>\n</div>", id="pre-verbatim"),
        pytest.param(
            "<div><listing><p>x</p></listing></div>",
            "div",
            "<div>\n  <listing><p>x</p></listing>\n</div>",
            id="listing-verbatim",
        ),
        pytest.param("<div><xmp> a </xmp></div>", "div", "<div>\n  <xmp> a </xmp>\n</div>", id="raw-block-verbatim"),
        pytest.param("<span><div>x</div></span>", "span", "<span><div>x</div></span>", id="inline-holding-block"),
    ],
)
def test_indent_layout(html: str, selector: str, expected: str) -> None:
    assert _pretty(html, selector) == expected


@pytest.mark.parametrize(
    ("html", "selector"),
    [
        pytest.param("<p>a<b>b</b>c</p>", "p", id="bold"),
        pytest.param("<p>x<span>y</span>z</p>", "p", id="span"),
        pytest.param('<p><a href="x">link</a>, more</p>', "p", id="anchor"),
        pytest.param("<p>1<math><mi>x</mi></math>2</p>", "p", id="inline-math"),
    ],
)
def test_indent_keeps_inline_text(html: str, selector: str) -> None:
    reparsed: Final = parse(_pretty(html, selector)).select(selector)
    assert [node.text for node in reparsed] == [node.text for node in parse(html).select(selector)]


def test_indent_document() -> None:
    source: Final = "<!DOCTYPE html><title>t</title><h1>T</h1><p>a <b>b</b></p><script>x</script>"
    assert parse(source).serialize(_PRETTY) == (
        "<!DOCTYPE html>\n<html>\n  <head>\n    <title>t</title>\n  </head>\n  <body>\n    <h1>T</h1>\n"
        "    <p>a <b>b</b></p>\n    <script>x</script>\n  </body>\n</html>"
    )


def test_indent_body_with_only_inline_content() -> None:
    assert (
        parse("text <b>only</b>").serialize(_PRETTY)
        == "<html>\n  <head></head>\n  <body>text <b>only</b></body>\n</html>"
    )


def _compact_roundtrips(source: str) -> bool:
    compact = parse(source).serialize()
    return parse(compact).serialize() == compact


def _pretty_is_fixpoint(source: str) -> bool:
    pretty = parse(source).serialize(_PRETTY)
    return parse(pretty).serialize(_PRETTY) == pretty


def test_indent_reindent_is_fixpoint(wpt_html_tree_corpus: WptHtmlTreeCorpus) -> None:
    # only markup the compact serializer round-trips can be asked to reindent stably
    unstable: Final = [
        case["data"]
        for case in wpt_html_tree_corpus["cases"]
        if _compact_roundtrips(case["data"]) and not _pretty_is_fixpoint(case["data"])
    ]
    assert unstable == []


def test_indent_drops_empty_and_blank_text_nodes() -> None:
    root: Final = Element("div", children=[Text(""), Element("p", children=[Text("x")]), Text(""), Text(" \n")])
    assert root.serialize(_PRETTY) == "<div>\n  <p>x</p>\n</div>"


def test_indent_keeps_blank_text_inside_a_run() -> None:
    root: Final = Element("p", children=[Element("b", children=[Text("a")]), Text(""), Text(" "), Element("i")])
    assert root.serialize(_PRETTY) == "<p><b>a</b> <i></i></p>"


def test_indent_comment_in_document() -> None:
    assert parse("<!--a--><p>x</p>").serialize(_PRETTY).splitlines()[0] == "<!--a-->"


def test_indent_text_root() -> None:
    assert Text(" a ").serialize(_PRETTY) == " a "


def test_indent_shadow_root() -> None:
    shadow: Final = Element("div").attach_shadow()
    shadow.set_inner_html(" a <p>x</p><b>y</b> ")
    assert shadow.serialize(_PRETTY) == "a\n<p>x</p>\n<b>y</b>"


def test_indent_inline_inner_root_is_verbatim() -> None:
    root: Final = parse_fragment("<span> a <b>b</b> </span>").children[0]
    assert root.serialize(_PRETTY, inner=True) == " a <b>b</b> "


def test_indent_xml_syntax_empties_blank_block() -> None:
    root: Final = parse_fragment("<div> </div>").children[0]
    assert root.serialize(Html(xml=True, layout=Indent())) == "<div/>"


def test_indent_xml_syntax_trims_run_text() -> None:
    root: Final = parse_fragment("<div> a&lt; <p>x</p></div>").children[0]
    assert root.serialize(Html(xml=True, layout=Indent())) == "<div>\n  a&lt;\n  <p>x</p>\n</div>"


def test_indent_xml_tree() -> None:
    document: Final = parse_xml("<root><a>x</a><b><c/></b></root>")
    assert document.serialize(Html(xml=True, layout=Indent())) == "<root>\n  <a>x</a>\n  <b>\n    <c/>\n  </b>\n</root>"


def test_indent_xml_tree_is_fixpoint() -> None:
    options: Final = Html(xml=True, layout=Indent())
    pretty: Final = parse_xml("<root><a>x</a><b><c/></b></root>").serialize(options)
    assert parse_xml(pretty).serialize(options) == pretty


def test_indent_meta_charset_skips_foreign_root() -> None:
    root: Final = parse("<svg><g></g></svg>").select_one("svg")
    assert root is not None
    assert root.serialize(Html(layout=Indent(), meta_charset=True)) == "<svg>\n  <g></g>\n</svg>"
