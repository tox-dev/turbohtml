"""Pickling rebuilds documents, fragments, and shadow roots structurally, the way copy.deepcopy does, instead of
serializing and reparsing: nothing the markup cannot express is lost."""

from __future__ import annotations

import copy
import pickle  # ruff:ignore[suspicious-pickle-import]  # round-tripping our own trusted payloads
from typing import TYPE_CHECKING, Final, NoReturn

import pytest

from turbohtml import Doctype, Document, DocumentFragment, Element, Range, ShadowRoot, Text, parse, parse_xml
from turbohtml._html import _reconstruct
from turbohtml.transform import Transform

if TYPE_CHECKING:
    from collections.abc import Callable

    from turbohtml import Node

_QUIRKS = '<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN"><p class=Foo>x<table></table></p>'


def _roundtrip(node: Node) -> Node:
    return pickle.loads(pickle.dumps(node))  # ruff: ignore[suspicious-pickle-usage]  # the payload is produced by this test


def _element(node: Node | None) -> Element:
    assert isinstance(node, Element)
    return node


def _kind(node: Node) -> int:
    reduced = node.__reduce__()
    assert isinstance(reduced, tuple)
    return reduced[1][0]


class _Unbooleanable:
    def __bool__(self) -> NoReturn:
        msg = "no truth value"
        raise RuntimeError(msg)


def test_pickled_document_keeps_the_doctype_identifiers() -> None:
    doctype = _roundtrip(parse(_QUIRKS)).children[0]
    assert isinstance(doctype, Doctype)
    assert (doctype.public_id, doctype.system_id) == ("-//W3C//DTD HTML 4.01 Transitional//EN", None)


def test_pickled_document_keeps_the_quirks_tree_shape() -> None:
    document = parse(_QUIRKS)
    assert _roundtrip(document).equals(document)


@pytest.mark.parametrize(
    "duplicate",
    [
        pytest.param(_roundtrip, id="pickle"),
        pytest.param(copy.copy, id="copy"),
        pytest.param(copy.deepcopy, id="deepcopy"),
    ],
)
def test_duplicated_document_keeps_quirks_mode_selector_matching(duplicate: Callable[[Node], Node]) -> None:
    clone = duplicate(parse(_QUIRKS))
    assert isinstance(clone, Document)
    assert [node.html for node in clone.select(".foo")] == ['<p class="Foo">x<table></table></p>']


def test_pickled_document_keeps_adjacent_text_nodes() -> None:
    document = parse("<p>a</p>")
    _element(document.select_one("p")).append(Text("b"))
    clone = _roundtrip(document)
    assert isinstance(clone, Document)
    paragraph = _element(clone.select_one("p"))
    assert [child.html for child in paragraph.children] == ["a", "b"]


def test_pickled_document_keeps_a_structure_the_parser_would_repair() -> None:
    document = parse("<p id=a></p>")
    _element(document.select_one("#a")).append(Element("div"))
    assert _roundtrip(document).html == document.html


def test_pickled_document_has_no_source_positions() -> None:
    clone = _roundtrip(parse("<p>x</p>"))
    assert isinstance(clone, Document)
    assert _element(clone.select_one("p")).position is None


def test_element_containing_a_template_pickles() -> None:
    div = _element(parse("<div><template><td>c</td></template></div>").select_one("div"))
    assert _roundtrip(div).html == "<div><template><td>c</td></template></div>"


def test_pickled_foreign_element_keeps_its_case() -> None:
    svg = _element(parse('<svg viewBox="0 0 1 1"><foreignObject><p>x</p></foreignObject></svg>').select_one("svg"))
    assert _roundtrip(svg).equals(svg)


def test_pickled_fragment_round_trips() -> None:
    div = _element(parse("<div><b>1</b>2<i>3</i></div>").select_one("div"))
    boundary = Range(div, 0)
    boundary.set_end(div, 3)
    fragment = boundary.clone_contents()
    assert _roundtrip(fragment).html == fragment.html


@pytest.mark.parametrize("mode", ["open", "closed"])
def test_pickled_shadow_root_is_a_shadow_root(mode: str) -> None:
    host = Element("div", {"id": "h"}, [Text("light")])
    root = host.attach_shadow(mode)
    root.append(Element("b"))
    clone = _roundtrip(root)
    assert isinstance(clone, ShadowRoot)
    assert (clone.mode, clone.html, clone.host.html) == (mode, "<b></b>", '<div id="h">light</div>')


def test_reconstruct_accepts_a_legacy_markup_document() -> None:
    node = _reconstruct(_kind(parse("")), "<p>x</p>", [])
    assert isinstance(node, Document)
    assert node.html == "<html><head></head><body><p>x</p></body></html>"


def test_reconstruct_accepts_a_legacy_markup_xml_document() -> None:
    node = _reconstruct(_kind(parse("")), "<R><Q/></R>", [], 1)
    assert node.html == "<R><Q></Q></R>"


def test_reconstruct_rejects_an_unreadable_quirks_flag() -> None:
    with pytest.raises(RuntimeError, match="no truth value"):
        _reconstruct(_kind(parse("")), _Unbooleanable(), [])


def test_reconstruct_rejects_a_non_node_child() -> None:
    with pytest.raises(TypeError, match="must be a node"):
        _reconstruct(_kind(Element("div")), ("div", {}), ["x"])  # ty: ignore[invalid-argument-type]


def test_reconstruct_rejects_a_document_child() -> None:
    with pytest.raises(TypeError, match="Document cannot be inserted"):
        _reconstruct(_kind(Element("div")), ("div", {}), [parse("")])


@pytest.mark.parametrize(
    ("payload", "error", "message"),
    [
        pytest.param(("div", 0), TypeError, "Element", id="host-not-element"),
        pytest.param((Element("div"), 2), ValueError, "mode out of range", id="mode-out-of-range"),
    ],
)
def test_reconstruct_rejects_a_malformed_shadow_payload(payload: object, error: type[Exception], message: str) -> None:
    kind = _kind(Element("div").attach_shadow("open"))
    with pytest.raises(error, match=message):
        _reconstruct(kind, payload, [])


def test_reconstruct_rejects_a_host_that_already_has_a_shadow_root() -> None:
    host = Element("div")
    host.attach_shadow("open")
    with pytest.raises(ValueError, match="already has a shadow root"):
        _reconstruct(_kind(Element("p").attach_shadow("open")), (host, 0), [])


def test_pickled_xml_document_round_trips() -> None:
    document = parse_xml('<R a="1"><Q/>t</R>')
    assert _roundtrip(document).equals(document)


@pytest.mark.parametrize(
    ("select", "expected"),
    [
        pytest.param("count(/*)", "1", id="element-count"),
        pytest.param("name(/*)", "b", id="element-name"),
        pytest.param("count(/node())", "2", id="child-count"),
        pytest.param("/b", "hello", id="root-path"),
    ],
)
def test_pickled_fragment_transform_document_context(select: str, expected: str) -> None:
    source: Final = DocumentFragment()
    source.append(Element("b", children=[Text("hello")]))
    source.append(Text("world"))
    convert: Final = Transform(
        parse_xml(
            '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
            '<xsl:output method="text"/>'
            f'<xsl:template match="/"><xsl:value-of select="{select}"/></xsl:template></xsl:stylesheet>'
        )
    )
    assert convert(_roundtrip(source)) == expected
