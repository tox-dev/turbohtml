"""The inner serialization of a raw-text element writes its Text and CDATA children literally, as the outer form does.

The WHATWG fragment serialization runs on the element itself, so a Text child whose parent is a raw-text element is
appended as is, whichever accessor asks for the element's content.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest

from turbohtml import CData, Comment, Element, Html, Indent, Minify, Text, parse

if TYPE_CHECKING:
    from collections.abc import Callable

_RAW_TEXT_TAGS: Final = ("script", "style", "xmp", "iframe", "noembed", "noframes", "plaintext")

_INNER_KEEPING_COMMENTS: Final = [
    pytest.param(lambda node: node.inner_html, id="inner_html"),
    pytest.param(lambda node: node.serialize(inner=True), id="serialize"),
    pytest.param(lambda node: "".join(node.serialize_iter(inner=True)), id="serialize_iter"),
    pytest.param(lambda node: node.encode(inner=True).decode(), id="encode"),
    pytest.param(lambda node: node.serialize(Html(layout=Indent(2)), inner=True), id="indent"),
    pytest.param(lambda node: "".join(node.serialize_iter(Html(layout=Indent(2)), inner=True)), id="indent-iter"),
]
_INNER: Final = [
    *_INNER_KEEPING_COMMENTS,
    pytest.param(lambda node: node.serialize(Html(layout=Minify()), inner=True), id="minify"),
]


@pytest.mark.parametrize("tag", _RAW_TEXT_TAGS)
@pytest.mark.parametrize("inner", _INNER)
def test_rawtext_inner_text_literal(tag: str, inner: Callable[[Element], str]) -> None:
    assert inner(Element(tag, children=[Text("a<b&c")])) == "a<b&c"


@pytest.mark.parametrize("tag", _RAW_TEXT_TAGS)
@pytest.mark.parametrize("inner", _INNER)
def test_rawtext_inner_cdata_literal(tag: str, inner: Callable[[Element], str]) -> None:
    assert inner(Element(tag, children=[Text("a<"), CData("x<")])) == "a<x<"


@pytest.mark.parametrize("tag", _RAW_TEXT_TAGS)
@pytest.mark.parametrize("inner", _INNER_KEEPING_COMMENTS)
def test_rawtext_inner_markup_children(tag: str, inner: Callable[[Element], str]) -> None:
    node: Final = Element(tag, children=[Text("a<"), Element("b", children=[Text("i<")]), Comment("c"), CData("d<")])
    assert inner(node) == "a<<b>i&lt;</b><!--c-->d<"


@pytest.mark.parametrize("inner", _INNER)
def test_rawtext_inner_nested_rawtext(inner: Callable[[Element], str]) -> None:
    style: Final = Element("style", children=[Text("s<"), CData("t<")])
    assert inner(Element("script", children=[Element("b", children=[style])])) == "<b><style>s<t<</style></b>"


@pytest.mark.parametrize("inner", _INNER)
def test_rawtext_inner_rawtext_child_of_ordinary(inner: Callable[[Element], str]) -> None:
    assert inner(Element("div", children=[Element("script", children=[Text("s<")])])) == "<script>s<</script>"


@pytest.mark.parametrize("inner", _INNER)
def test_rawtext_inner_noscript_with_scripting(inner: Callable[[Element], str]) -> None:
    noscript: Final = parse("<body><noscript>a<b</noscript>", scripting=True).find("noscript")
    assert noscript is not None
    assert inner(noscript) == "a<b"


@pytest.mark.parametrize("tag", ["noscript", "div", "textarea", "title"])
@pytest.mark.parametrize("inner", _INNER)
def test_rawtext_inner_ordinary_text_escaped(tag: str, inner: Callable[[Element], str]) -> None:
    assert inner(Element(tag, children=[Text("a<b&c")])) == "a&lt;b&amp;c"


@pytest.mark.parametrize(
    "inner",
    [
        pytest.param(lambda node: node.inner_xml, id="inner_xml"),
        pytest.param(lambda node: node.serialize(Html(xml=True), inner=True), id="serialize-xml"),
    ],
)
def test_rawtext_inner_xml_escapes(inner: Callable[[Element], str]) -> None:
    assert inner(Element("script", children=[Text("a<b")])) == "a&lt;b"


def test_rawtext_inner_shadow_root_text_escaped() -> None:
    # a clonable shadow root carries a flag bit that a raw-text check on a non-element would misread
    host: Final = parse('<div><template shadowrootmode="open" shadowrootclonable>x</template></div>').find("div")
    assert host is not None
    shadow: Final = host.shadow_root
    assert shadow is not None
    shadow.append(Text("y<"))
    assert shadow.inner_html == "xy&lt;"
