from __future__ import annotations

from typing import TYPE_CHECKING, Final
from xml.etree import (  # ruff:ignore[suspicious-xml-etree-import]  # parsing turbohtml's own output, not untrusted input
    ElementTree as ET,
)

import pytest

from turbohtml import Canonical, Element, Html, Indent, Minify, Text, parse, parse_xml
from turbohtml.transform import Transform

if TYPE_CHECKING:
    from collections.abc import Callable

    from turbohtml import Node

_SVG: Final[str] = "{http://www.w3.org/2000/svg}"
_MATHML: Final[str] = "{http://www.w3.org/1998/Math/MathML}"
_XLINK_HREF: Final[str] = "{http://www.w3.org/1999/xlink}href"

_SERIALIZERS: Final[tuple[pytest.ParameterSet, ...]] = (
    pytest.param(lambda node: node.serialize(Html(xml=True)), id="serialize"),
    pytest.param(lambda node: node.serialize(Html(xml=True, layout=Indent())), id="serialize-indent"),
    pytest.param(lambda node: "".join(node.serialize_iter(Html(xml=True))), id="serialize-iter"),
    pytest.param(
        lambda node: "".join(node.serialize_iter(Html(xml=True, layout=Indent()))), id="serialize-iter-indent"
    ),
)
_CANONICALIZERS: Final[tuple[pytest.ParameterSet, ...]] = (
    pytest.param(lambda node: node.canonicalize().decode(), id="c14n"),
    pytest.param(lambda node: node.canonicalize(Canonical(exclusive=True)).decode(), id="exclusive-c14n"),
    pytest.param(lambda node: node.canonicalize(Canonical(version="1.1")).decode(), id="c14n-1.1"),
)


def _body(markup: str) -> Node:
    body = parse(markup).select_one("body")
    assert body is not None
    return body


def _first_in_body(markup: str) -> Node:
    return _body(markup).children[0]


def _under_xml(left_out: Element) -> Node:
    document = parse_xml('<r xmlns="urn:a" xmlns:p="urn:r" xmlns:q="urn:q"><s/></r>')
    holder = document.select_one("s")
    assert holder is not None
    holder.append(left_out)
    root = document.root
    assert root is not None
    return root


def _left_out_in_xml(left_out: Element) -> Node:
    _under_xml(left_out)
    return left_out


_HTML_CASES: Final[tuple[pytest.ParameterSet, ...]] = (
    pytest.param(
        lambda: _body("<svg><foreignObject><d<>x<b>y</b></d<></foreignObject></svg>"),
        [("body", []), (f"{_SVG}svg", []), (f"{_SVG}foreignObject", []), ("b", [])],
        id="html-under-foreign-object",
    ),
    pytest.param(
        lambda: _body("<svg><foreignObject><d<><a\x01b><b>y</b></a\x01b></d<></foreignObject></svg>"),
        [("body", []), (f"{_SVG}svg", []), (f"{_SVG}foreignObject", []), ("b", [])],
        id="nested-left-out-run",
    ),
    pytest.param(
        lambda: _body("<svg><foreignObject><d< xmlns='urn:x'><b>y</b></d<></foreignObject></svg>"),
        [("body", []), (f"{_SVG}svg", []), (f"{_SVG}foreignObject", []), ("b", [])],
        id="default-declaration-on-left-out-element",
    ),
    pytest.param(
        lambda: _body("<svg><a\x01b><g>y</g></a\x01b><d<></d<><g/></svg>"),
        [("body", []), (f"{_SVG}svg", []), (f"{_SVG}g", []), (f"{_SVG}g", [])],
        id="svg-under-svg",
    ),
    pytest.param(
        lambda: _body("<math><d<><mi>y</mi></d<></math>"),
        [("body", []), (f"{_MATHML}math", []), (f"{_MATHML}mi", [])],
        id="mathml-under-mathml",
    ),
    pytest.param(
        lambda: _body("<d<><x-y>t</x-y></d<>"),
        [("body", []), ("x-y", [])],
        id="custom-element-under-left-out-element",
    ),
    pytest.param(
        lambda: _body("<svg><d< xmlns:xlink='urn:other'><a xlink:href='u'>t</a></d<></svg>"),
        [("body", []), (f"{_SVG}svg", []), (f"{_SVG}a", [_XLINK_HREF])],
        id="xlink-under-left-out-element",
    ),
    pytest.param(
        lambda: _body(f"<svg>{'<d<>' * 10}<g/></svg>"),
        [("body", []), (f"{_SVG}svg", []), (f"{_SVG}g", [])],
        id="deep-left-out-run",
    ),
    pytest.param(
        lambda: _first_in_body("<d<><svg><g/></svg><b>y</b></d<>"),
        [(f"{_SVG}svg", []), (f"{_SVG}g", []), ("b", [])],
        id="left-out-root",
    ),
)
_XML_CASES: Final[tuple[pytest.ParameterSet, ...]] = (
    pytest.param(
        lambda: _under_xml(
            Element(
                "1a",
                {"xmlns:p": "urn:p", "xmlns": "urn:b"},
                children=[Element("p:x"), Element("y", children=[Element("z")]), Element("q:z"), Element("div")],
            )
        ),
        [
            ("{urn:a}r", []),
            ("{urn:a}s", []),
            ("{urn:p}x", []),
            ("{urn:b}y", []),
            ("{urn:b}z", []),
            ("{urn:q}z", []),
            ("{urn:b}div", []),
        ],
        id="declarations-on-left-out-element",
    ),
    pytest.param(
        lambda: _under_xml(
            Element(
                "1a",
                {"xmlns:p": "urn:outer"},
                children=[
                    Element("2b", {"xmlns:p": "urn:inner"}, children=[Element("p:x")]),
                    Element("p:y"),
                    Element("p:w", {"xmlns:p": "urn:r"}),
                ],
            )
        ),
        [("{urn:a}r", []), ("{urn:a}s", []), ("{urn:inner}x", []), ("{urn:outer}y", []), ("{urn:r}w", [])],
        id="redeclared-in-left-out-run",
    ),
    pytest.param(
        lambda: _left_out_in_xml(
            Element("1a", {"xmlns:p": "urn:p", "xmlns": "urn:b"}, children=[Element("p:x"), Element("y")])
        ),
        [("{urn:p}x", []), ("{urn:b}y", [])],
        id="left-out-root-in-xml-tree",
    ),
    pytest.param(
        lambda: _under_xml(
            Element(
                "w:",
                children=[
                    Element(":v", children=[Element("a:b:c", children=[Element("1:d", children=[Element("x")])])])
                ],
            )
        ),
        [("{urn:a}r", []), ("{urn:a}s", []), ("{urn:a}x", [])],
        id="misplaced-colons",
    ),
)


@pytest.mark.parametrize("write", [*_SERIALIZERS, *_CANONICALIZERS])
@pytest.mark.parametrize(("build", "expected"), [*_HTML_CASES, *_XML_CASES])
def test_left_out_element_children_keep_their_namespace(
    write: Callable[[Node], str], build: Callable[[], Node], expected: list[tuple[str, list[str]]]
) -> None:
    assert _names(write(build())) == expected


def _names(output: str) -> list[tuple[str, list[str]]]:
    # the wrapper takes the output of a left-out root, which has one element per child, and is in no namespace itself
    wrapper = ET.fromstring(f"<w>{output}</w>")  # ruff:ignore[suspicious-xml-element-tree-usage]  # our own output
    return [(element.tag, sorted(element.attrib)) for element in wrapper.iter()][1:]


@pytest.mark.parametrize("write", [*_SERIALIZERS, *_CANONICALIZERS])
@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        pytest.param(
            "<a\x01b xmlns:p='urn:p' xmlns:e='' class='c'><p:x>t</p:x><p:y xmlns:p='urn:own'/></a\x01b>",
            [("body", []), ("{urn:p}x", []), ("{urn:own}y", [])],
            id="prefix",
        ),
        pytest.param(
            "<svg><foreignObject><d< xmlns:p='urn:p'><b xmlns='urn:own' xmlns:e=''><p:i></p:i></b></d<>"
            "</foreignObject></svg>",
            [("body", []), (f"{_SVG}svg", []), (f"{_SVG}foreignObject", []), ("b", []), ("{urn:p}i", [])],
            id="child-declarations-not-written",
        ),
        pytest.param(
            "<svg><d< xmlns:xlink='urn:other' xmlns:p='urn:p' xmlns:abcde='urn:e'><a xlink:href='u'><p:i/></a></d<>"
            "</svg>",
            [("body", []), (f"{_SVG}svg", []), (f"{_SVG}a", [_XLINK_HREF]), ("{urn:p}i", [])],
            id="xlink-bound-by-the-child",
        ),
        pytest.param(
            "<div xmlns:p='urn:p'><a\x01b xmlns:p='urn:outer'><c\x01d xmlns:p='urn:inner'><p:x></p:x></c\x01d>"
            "<p:y></p:y></a\x01b><e\x01f xmlns:p='urn:leaf'></e\x01f><p:z></p:z></div>",
            [("body", []), ("div", []), ("{urn:inner}x", []), ("{urn:outer}y", []), ("{urn:p}z", [])],
            id="nested-run-redeclares",
        ),
        pytest.param(
            "<div xmlns:p='urn:p'><a\x01b xmlns:p='urn:p'><p:x></p:x></a\x01b></div>",
            [("body", []), ("div", []), ("{urn:p}x", [])],
            id="run-repeats-the-binding-in-scope",
        ),
    ],
)
def test_children_bind_the_prefix_a_left_out_element_declares(
    write: Callable[[Node], str], markup: str, expected: list[tuple[str, list[str]]]
) -> None:
    assert _names(write(_body(markup))) == expected


def _html_left_out() -> tuple[Node, Element]:
    body = _body("<d<><b>t</b></d<>")
    left_out = body.children[0]
    assert isinstance(left_out, Element)
    return body, left_out


def _xml_left_out() -> tuple[Node, Element]:
    left_out = Element("1a", children=[Element("b")])
    return _under_xml(left_out), left_out


@pytest.mark.parametrize("write", _CANONICALIZERS)
@pytest.mark.parametrize(
    ("build", "kept"),
    [
        pytest.param(_html_left_out, [("body", []), ("b", [])], id="html-tree"),
        pytest.param(_xml_left_out, [("{urn:a}r", []), ("{urn:a}s", []), ("{urn:a}b", [])], id="xml-tree"),
    ],
)
def test_canonicalize_follows_a_renamed_element(
    write: Callable[[Node], str], build: Callable[[], tuple[Node, Element]], kept: list[tuple[str, list[str]]]
) -> None:
    root, left_out = build()
    before = _names(write(root))
    left_out.tag = "i"
    renamed = _names(write(root))
    left_out.tag = "2b"
    assert (before, len(renamed), _names(write(root))) == (kept, len(kept) + 1, kept)


@pytest.mark.parametrize(
    "inner",
    [
        pytest.param(lambda node: node.inner_xml, id="inner-xml"),
        pytest.param(lambda node: node.serialize(Html(xml=True), inner=True), id="serialize-inner"),
    ],
)
def test_inner_xml_binds_what_a_left_out_child_declares(inner: Callable[[Node], str]) -> None:
    left_out = Element("1a", {"xmlns:p": "urn:p"}, children=[Element("p:x"), Element("q:y")])
    _under_xml(left_out)
    holder = left_out.parent
    assert holder is not None
    assert _names(inner(holder)) == [("{urn:p}x", []), ("{urn:q}y", [])]


def test_serialize_xml_skips_declarations_a_child_has_or_cannot_use() -> None:
    declarations = {f"xmlns:n{index:02}": f"urn:n{index}" for index in range(20)}
    run = Element(
        "1a",
        {**declarations, "xmlns:nn": "urn:nn", "xmlns:xlink": "urn:other", "xmlns:e": ""},
        children=[
            Element(
                "2b",
                {"xmlns:n05": "urn:inner"},
                children=[
                    Element(
                        "n05:x",
                        {"xmlns:n07": "urn:own", "xmlns:zz": "urn:zz", "xmlns:aa": "urn:aa", "xmlns:n0x": "urn:0x"},
                        children=[Text("t")],
                    )
                ],
            )
        ],
    )
    names = _names(_under_xml(run).serialize(Html(xml=True)))
    assert names == [("{urn:a}r", []), ("{urn:a}s", []), ("{urn:inner}x", [])]


@pytest.mark.parametrize(
    "write",
    [
        pytest.param(lambda node: node.serialize(Html(xml=True)), id="serialize"),
        pytest.param(lambda node: node.canonicalize().decode(), id="c14n"),
    ],
)
def test_deep_left_out_run_keeps_the_outermost_declaration(write: Callable[[Node], str]) -> None:
    assert _names(write(_under_xml(_deep_left_out_run(12))))[-1] == ("{urn:p}x", [])


def _deep_left_out_run(depth: int) -> Element:
    node = Element("p:x")
    for level in range(depth):
        node = Element(f"{level}d", {f"xmlns:d{level}": f"urn:d{level}"}, children=[node])
    return Element("0e", {"xmlns:p": "urn:p"}, children=[node])


@pytest.mark.parametrize(
    "write",
    [
        pytest.param(lambda node: node.serialize(Html(xml=True)), id="serialize"),
        pytest.param(lambda node: "".join(node.serialize_iter(Html(xml=True))), id="serialize-iter"),
    ],
)
def test_serialize_xml_refuses_declaration_copies_past_their_allowance(write: Callable[[Node], str]) -> None:
    with pytest.raises(ValueError, match="elements left out for their names exceed the allowance"):
        write(_wide_left_out())


def _wide_left_out() -> Node:
    declarations = " ".join(f'xmlns:n{index}="urn:{"x" * 60}{index}"' for index in range(64))
    return parse(f"<a\x01b {declarations}>{'<i></i>' * 64}</a\x01b>")


def test_xslt_reports_declaration_copies_past_their_allowance() -> None:
    sheet = parse_xml(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:output method="xml" omit-xml-declaration="yes"/>'
        '<xsl:template match="/"><out><xsl:copy-of select="//body/node()"/></out></xsl:template></xsl:stylesheet>'
    )
    with pytest.raises(ValueError, match="elements left out for their names exceed the allowance"):
        Transform(sheet)(_wide_left_out())


def test_minify_xml_writes_markup_inside_raw_text() -> None:
    node = Element("div", children=[Element("script", children=[Element("b", children=[Text("x")])])])
    assert node.serialize(Html(xml=True, layout=Minify())) == "<div><script><b>x</b></script></div>"
