"""A raw-text element given element or comment children through the DOM serializes them as markup.

The WHATWG fragment serialization appends only a Text child of a raw-text element literally; any other child takes its
ordinary markup form, in every layout and in the lossless serializer.
"""

from __future__ import annotations

import threading
from typing import Final

import pytest

from turbohtml import CData, Comment, Element, Html, Indent, Minify, Text, parse, parse_fragment
from turbohtml.clean import CSSMinify, JSMinify

_RAW_TEXT_TAGS: Final = ("script", "style", "xmp", "iframe", "noembed", "noframes", "plaintext")


@pytest.mark.parametrize("tag", _RAW_TEXT_TAGS)
@pytest.mark.parametrize(
    "layout",
    [pytest.param(None, id="compact"), pytest.param(Indent(2), id="indent"), pytest.param(Minify(), id="minify")],
)
def test_rawtext_children_serialize_as_markup(tag: str, layout: Indent | Minify | None) -> None:
    minify: Final = isinstance(layout, Minify)
    end_tag: Final = "" if minify and tag == "plaintext" else f"</{tag}>"
    assert _mixed(tag).serialize(Html(layout=layout)) == f"<{tag}>a<<b>i&lt;</b>{'' if minify else '<!--c-->'}{end_tag}"


@pytest.mark.parametrize("tag", _RAW_TEXT_TAGS)
def test_rawtext_children_inner_minify(tag: str) -> None:
    assert _mixed(tag).serialize(Html(layout=Minify()), inner=True) == "a<<b>i&lt;</b>"


@pytest.mark.parametrize(
    ("layout", "expected"),
    [
        pytest.param(Minify(), "<script>a<<b>i&lt;</b></script>", id="strip"),
        pytest.param(Minify(strip_comments=False), "<script>a<<b>i&lt;<!--n--></b><!--c--></script>", id="keep"),
    ],
)
def test_rawtext_children_minify_comments(layout: Minify, expected: str) -> None:
    script: Final = Element(
        "script", children=[Text("a<"), Element("b", children=[Text("i<"), Comment("n")]), Comment("c")]
    )
    assert script.serialize(Html(layout=layout)) == expected


@pytest.mark.parametrize("layout", [pytest.param(None, id="compact"), pytest.param(Indent(2), id="indent")])
def test_rawtext_children_serialize_iter(layout: Indent | None) -> None:
    assert "".join(_mixed("script").serialize_iter(Html(layout=layout))) == "<script>a<<b>i&lt;</b><!--c--></script>"


def test_rawtext_children_indent_inside_head() -> None:
    head: Final = Element("head", children=[_mixed("style")])
    assert head.serialize(Html(layout=Indent(2))) == "<head>\n  <style>a<<b>i&lt;</b><!--c--></style>\n</head>"


def test_rawtext_children_noscript_with_scripting() -> None:
    noscript: Final = parse("<body><noscript>a<</noscript>", scripting=True).find("noscript")
    assert noscript is not None
    noscript.append(Element("b", children=[Element("noscript", children=[Element("i"), Text("t<")])]))
    assert noscript.html == "<noscript>a<<b><noscript><i></i>t<</noscript></b></noscript>"


def test_rawtext_children_nested_rawtext() -> None:
    style: Final = Element("style", children=[Text("x<"), Comment("d")])
    assert Element("script", children=[Element("b", children=[style])]).html == (
        "<script><b><style>x<<!--d--></style></b></script>"
    )


def test_rawtext_children_deep_nesting_stays_iterative() -> None:
    # under a 256 KiB stack a walk that recursed once per nested raw-text element would overflow at this depth
    depth: Final = 2_000
    script: Final = Element("script")
    current = script
    for _ in range(depth):
        inner = Element("script")
        current.append(Element("b", children=[inner]))
        current = inner
    current.append(Text("x<"))
    captured: Final[dict[str, str]] = {}

    def run() -> None:
        captured["compact"] = script.html
        captured["minify"] = script.serialize(Html(layout=Minify()))
        captured["lossless"] = script.to_source()

    previous: Final = threading.stack_size(256 * 1024)
    try:
        worker: Final = threading.Thread(target=run)
        worker.start()
        worker.join()
    finally:
        threading.stack_size(previous)
    expected: Final = "<script><b>" * depth + "<script>x<</script>" + "</b></script>" * depth
    assert captured == {"compact": expected, "minify": expected, "lossless": expected}


@pytest.mark.parametrize(
    ("node", "expected"),
    [
        pytest.param(Element("script", children=[CData("q<"), Element("i")]), "<script>q<<i></i></script>", id="child"),
        pytest.param(
            Element("script", children=[Element("i"), CData("q<")]), "<script><i></i>q<</script>", id="after-markup"
        ),
        pytest.param(
            Element("script", children=[Element("b", children=[Element("style", children=[CData("q<")])])]),
            "<script><b><style>q<</style></b></script>",
            id="nested",
        ),
        pytest.param(
            Element("script", children=[Element("b", children=[CData("q<")])]),
            "<script><b><![CDATA[q<]]></b></script>",
            id="inside-markup",
        ),
    ],
)
def test_rawtext_children_cdata(node: Element, expected: str) -> None:
    assert node.html == expected


def test_rawtext_children_cdata_feeds_minifier() -> None:
    script: Final = Element("script", children=[Text("var a = 1;"), CData("var b = 2;")])
    assert script.serialize(Html(layout=Minify(minify_js=JSMinify()))) == "<script>var a=1,b=2</script>"


@pytest.mark.parametrize(
    ("node", "layout", "expected"),
    [
        pytest.param(
            Element("script", children=[Text("var a = 1;"), Element("b", children=[Text("inner")])]),
            Minify(minify_js=JSMinify()),
            "<script>var a = 1;<b>inner</b></script>",
            id="js",
        ),
        pytest.param(
            Element("style", children=[Text("a{color:red}"), Element("p"), Text("{color:blue}")]),
            Minify(minify_css=CSSMinify()),
            "<style>a{color:red}<p></p>{color:blue}</style>",
            id="css",
        ),
    ],
)
def test_rawtext_children_skip_embedded_minifier(node: Element, layout: Minify, expected: str) -> None:
    assert node.serialize(Html(layout=layout)) == expected


def test_rawtext_children_lossless() -> None:
    script: Final = parse("<script>a&amp;<</script>", source_locations=True).find("script")
    assert script is not None
    script.append(CData("d<"))
    script.append(Element("b", children=[Text("i<")]))
    script.append(CData("e<"))
    script.append(Text("t<"))
    script.append(Comment("c"))
    assert script.to_source() == "<script>a&amp;<d<<b>i&lt;</b>e<t<<!--c--></script>"


def test_rawtext_children_lossless_keeps_moved_source() -> None:
    # to_source copies the span of every subtree a mutation left untouched, a moved one inside a raw-text element too
    doc: Final = parse("<script>s&amp;<</script><P CLASS='x'>a&amp;b<I>i</I></P>", source_locations=True)
    script: Final = doc.find("script")
    paragraph: Final = doc.find("p")
    assert script is not None
    assert paragraph is not None
    script.append(paragraph)
    assert script.to_source() == "<script>s&amp;<<P CLASS='x'>a&amp;b<I>i</I></P></script>"


def test_rawtext_children_lossless_shadow_root_text_escaped() -> None:
    # a clonable shadow root carries a flag bit that a raw-text check on a non-element would misread
    doc: Final = parse(
        '<div><template shadowrootmode="open" shadowrootclonable>x</template></div>', source_locations=True
    )
    host = doc.find("div")
    assert host is not None
    shadow: Final = host.shadow_root
    assert shadow is not None
    text = shadow.children[0]
    assert isinstance(text, Text)
    text.data = "y<"
    assert shadow.to_source() == "y&lt;"


def test_rawtext_children_reparse_as_text() -> None:
    # the spec lists this as output that does not round-trip: raw-text content reparses as one text node
    script: Final = parse_fragment(_mixed("script").html).find("script")
    assert script is not None
    assert [(type(child), child.text) for child in script.children] == [(Text, "a<<b>i&lt;</b><!--c-->")]


def _mixed(tag: str) -> Element:
    return Element(tag, children=[Text("a<"), Element("b", children=[Text("i<")]), Comment("c")])
