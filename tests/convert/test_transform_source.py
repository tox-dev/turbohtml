from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest

from turbohtml import Comment, DocumentFragment, Element, Text, parse_fragment, parse_xml
from turbohtml.transform import Transform

if TYPE_CHECKING:
    from collections.abc import Callable

    from turbohtml import Node


def _constructed_child() -> Node:
    root: Final = Element("root", children=[Element("child", children=[Text("hello")])])
    return root.children[0]


def _detached_element() -> Node:
    document: Final = parse_xml("<root><child>hello</child></root>")
    child: Final = document.children[0].children[0]
    document.remove("child")
    return child


def _fragment() -> Node:
    fragment: Final = DocumentFragment()
    fragment.append(Element("b", children=[Text("hello")]))
    fragment.append(Text("world"))
    return fragment


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(lambda: Element("root", children=[Text("hello")]), "<root>hello</root>", id="element"),
        pytest.param(lambda: Text("hello"), "hello", id="text"),
        pytest.param(lambda: Comment("hello"), "<!--hello-->", id="comment"),
        pytest.param(
            lambda: parse_fragment("<b>hello</b><i>world</i>"), "<div><b>hello</b><i>world</i></div>", id="fragment"
        ),
        pytest.param(lambda: parse_xml("<root>hello</root>"), "<root>hello</root>", id="document"),
        pytest.param(
            lambda: parse_xml("<root><child/></root>").children[0].children[0],
            "<root><child/></root>",
            id="attached-child",
        ),
        pytest.param(DocumentFragment, "", id="empty-fragment"),
        pytest.param(_fragment, "<b>hello</b>world", id="document-fragment"),
        pytest.param(_constructed_child, "<root><child>hello</child></root>", id="constructed-child"),
        pytest.param(_detached_element, "<child>hello</child>", id="detached-element"),
    ],
)
@pytest.mark.parametrize("select", [pytest.param(".", id="self"), pytest.param("/node()", id="root-children")])
def test_transform_source_root(source: Callable[[], Node], expected: str, select: str) -> None:
    node: Final = source()
    original: Final = str(node)
    convert: Final = Transform(
        parse_xml(
            '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
            '<xsl:output method="xml" omit-xml-declaration="yes"/>'
            f'<xsl:template match="/"><xsl:copy-of select="{select}"/></xsl:template></xsl:stylesheet>'
        )
    )
    assert (convert(node), str(node), convert(node)) == (expected, original, expected)


def test_transform_html_nonroot_pattern() -> None:
    convert: Final = Transform(
        parse_xml(
            '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
            '<xsl:output method="text"/><xsl:template match="p"><xsl:text>hit</xsl:text></xsl:template>'
            "</xsl:stylesheet>"
        )
    )
    assert convert(parse_fragment("<p>x</p>")) == "hit"


@pytest.mark.parametrize("fail", [pytest.param(False, id="success"), pytest.param(True, id="termination")])
def test_transform_standalone_text_preserves_source(*, fail: bool) -> None:
    source: Final = Element("root", children=[Text("  "), Element("b", children=[Text("hello")]), Text(" world")])
    original: Final = str(source)
    terminate: Final = '<xsl:message terminate="yes">stop</xsl:message>' if fail else ""
    convert: Final = Transform(
        parse_xml(
            '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
            '<xsl:output method="text"/><xsl:strip-space elements="*"/>'
            f'<xsl:template match="/"><xsl:copy-of select="."/>{terminate}</xsl:template></xsl:stylesheet>'
        )
    )
    if fail:
        with pytest.raises(RuntimeError, match="stop"):
            convert(source)
    else:
        assert convert(source) == "hello world"
    assert str(source) == original


@pytest.mark.parametrize(
    ("select", "expected"),
    [
        pytest.param("count(/*)", "1", id="element-count"),
        pytest.param("name(/*)", "b", id="element-name"),
        pytest.param("count(/node())", "2", id="child-count"),
        pytest.param("/b", "hello", id="root-path"),
    ],
)
def test_transform_fragment_document_context(select: str, expected: str) -> None:
    convert: Final = Transform(
        parse_xml(
            '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
            '<xsl:output method="text"/>'
            f'<xsl:template match="/"><xsl:value-of select="{select}"/></xsl:template></xsl:stylesheet>'
        )
    )
    assert convert(_fragment()) == expected


@pytest.mark.parametrize("level", ["single", "multiple", "any"])
@pytest.mark.parametrize("child", [False, True], ids=["root", "child"])
def test_transform_detached_namespace_numbering(level: str, *, child: bool) -> None:
    document: Final = parse_xml(
        '<outer><root xmlns:a="urn:same" xmlns:b="urn:same"><a:n/><b:n/><a:n xmlns:a="urn:other"/></root></outer>'
    )
    root: Final = document.find("root")
    assert root is not None
    document.remove("root")
    source: Final = root.children[0] if child else root
    convert: Final = Transform(
        parse_xml(
            '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
            '<xsl:output method="text"/><xsl:template match="/">'
            f'<xsl:for-each select="root/*"><xsl:number level="{level}"/>'
            "<xsl:text>,</xsl:text></xsl:for-each></xsl:template></xsl:stylesheet>"
        )
    )
    assert (convert(source), convert(source)) == ("1,2,1,", "1,2,1,")
