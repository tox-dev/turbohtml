from __future__ import annotations

import pickle  # ruff:ignore[suspicious-pickle-import]  # round-tripping this test's own bytes
from functools import partial
from io import BytesIO
from typing import TYPE_CHECKING, Final, cast
from xml.etree import (  # ruff:ignore[suspicious-xml-etree-import]  # parsing turbohtml's own output, not untrusted input
    ElementTree as ET,
)

import pytest
from bench.operations import INPUTS

from turbohtml import Canonical, CData, Element, Html, ProcessingInstruction, Text, parse, parse_xml

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

    from lxml import etree as lxml_types
    from work_steps import WorkSteps

    from turbohtml import Document, Node


def test_returns_utf8_bytes() -> None:
    out = _one("<p>x</p>", "p").canonicalize()
    assert isinstance(out, bytes)
    assert out == b"<p>x</p>"


def _one(markup: str, selector: str) -> Element:
    node = parse(markup).select_one(selector)
    assert node is not None
    return node


def test_default_options_are_1_0_without_comments() -> None:
    node = _one("<a><!--c--><b>t</b></a>", "a")
    assert node.canonicalize() == node.canonicalize(Canonical(version="1.0", with_comments=False))


def test_empty_element_is_a_start_end_pair() -> None:
    assert _one("<div></div>", "div").canonicalize() == b"<div></div>"


def test_void_element_is_a_start_end_pair() -> None:
    assert _one("<p><br></p>", "p").canonicalize() == b"<p><br></br></p>"


def test_non_ascii_text_stays_literal_utf8() -> None:
    assert Element("p", children=[Text("café → \xa9")]).canonicalize() == "<p>café → \xa9</p>".encode()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("a&b", b"a&amp;b", id="ampersand"),
        pytest.param("a<b", b"a&lt;b", id="less-than"),
        pytest.param("a>b", b"a&gt;b", id="greater-than"),
        pytest.param("a\rb", b"a&#xD;b", id="carriage-return"),
        pytest.param('a"b', b'a"b', id="quote-stays-literal"),
        pytest.param("a\tb\nc", b"a\tb\nc", id="tab-newline-stay-literal"),
    ],
)
def test_text_character_references(text: str, expected: bytes) -> None:
    assert Element("p", children=[Text(text)]).canonicalize() == b"<p>" + expected + b"</p>"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("a&b", b"a&amp;b", id="ampersand"),
        pytest.param("a<b", b"a&lt;b", id="less-than"),
        pytest.param("a>b", b"a>b", id="greater-than-stays-literal"),
        pytest.param('a"b', b"a&quot;b", id="quote"),
        pytest.param("a\tb", b"a&#x9;b", id="tab"),
        pytest.param("a\nb", b"a&#xA;b", id="newline"),
        pytest.param("a\rb", b"a&#xD;b", id="carriage-return"),
    ],
)
def test_attribute_character_references(value: str, expected: bytes) -> None:
    assert Element("p", {"title": value}).canonicalize() == b'<p title="' + expected + b'"></p>'


def test_valueless_attribute_becomes_empty_string() -> None:
    assert Element("input", {"disabled": None}).canonicalize() == b'<input disabled=""></input>'


def test_attribute_value_starting_with_a_special() -> None:
    assert Element("p", {"title": "&<x"}).canonicalize() == b'<p title="&amp;&lt;x"></p>'


def test_attributes_sort_by_name_within_no_namespace() -> None:
    assert _one("<p z=1 a=2 m=3>x", "p").canonicalize() == b'<p a="2" m="3" z="1">x</p>'


def test_attributes_sort_no_namespace_before_namespaced() -> None:
    node = _one("<svg xml:lang=en b=2 a=1 xlink:href=h></svg>", "svg")
    assert node.canonicalize() == (
        b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"'
        b' a="1" b="2" xlink:href="h" xml:lang="en"></svg>'
    )


def test_svg_root_declares_default_namespace() -> None:
    assert _one('<svg><rect x="1"/></svg>', "svg").canonicalize() == (
        b'<svg xmlns="http://www.w3.org/2000/svg"><rect x="1"></rect></svg>'
    )


def test_mathml_root_declares_default_namespace() -> None:
    assert _one("<math><mi>x</mi></math>", "math").canonicalize() == (
        b'<math xmlns="http://www.w3.org/1998/Math/MathML"><mi>x</mi></math>'
    )


def test_nested_foreign_element_does_not_redeclare_default() -> None:
    out = _one("<svg><g><rect/></g></svg>", "svg").canonicalize()
    assert out.count(b"xmlns=") == 1


def test_html_element_carries_no_namespace() -> None:
    assert b"xmlns" not in _one("<p id=a>x</p>", "p").canonicalize()


def test_stored_xmlns_attribute_is_not_double_emitted() -> None:
    node = _one('<svg xmlns="http://www.w3.org/2000/svg" width="1"><rect/></svg>', "svg")
    assert node.canonicalize() == b'<svg xmlns="http://www.w3.org/2000/svg" width="1"><rect></rect></svg>'


def test_cdata_section_becomes_escaped_text() -> None:
    assert Element("a", children=[CData("<x>&")]).canonicalize() == b"<a>&lt;x&gt;&amp;</a>"


def _xml_namespace_host() -> Element:
    host = parse_xml('<r xmlns:p="urn:p"><q/></r>').select_one("q")
    assert host is not None
    return host


_HOSTS: Final[list[pytest.ParameterSet]] = [
    pytest.param(lambda: Element("p"), (b"<p", b"</p>"), id="html-tree"),
    pytest.param(_xml_namespace_host, (b'<q xmlns:p="urn:p"', b"</q>"), id="xml-namespace-tree"),
]
_DROPPED_TEXT: Final[list[pytest.ParameterSet]] = [
    pytest.param("a\x01b", "ab", id="c0-control"),
    pytest.param("a\ufffeb", "ab", id="noncharacter-fffe"),
    pytest.param("a\uffffb", "ab", id="noncharacter-ffff"),
    pytest.param("a\ud800b", "ab", id="lone-surrogate"),
    pytest.param("\x01<&>", "&lt;&amp;&gt;", id="control-before-specials"),
    pytest.param("a\x01\rb", "a&#xD;b", id="control-before-carriage-return"),
]


@pytest.mark.parametrize(("make_host", "tags"), _HOSTS)
@pytest.mark.parametrize("child", [pytest.param(Text, id="text"), pytest.param(CData, id="cdata")])
@pytest.mark.parametrize(("text", "expected"), _DROPPED_TEXT)
def test_content_drops_characters_xml_cannot_hold(
    make_host: Callable[[], Element], tags: tuple[bytes, bytes], child: Callable[[str], Node], text: str, expected: str
) -> None:
    host = make_host()
    host.append(child(text))
    out = host.canonicalize()
    assert out == tags[0] + b">" + expected.encode() + tags[1]
    ET.fromstring(out)  # ruff:ignore[suspicious-xml-element-tree-usage]  # our own canonical output, parsed to assert it is well-formed


@pytest.mark.parametrize(
    "filler",
    [
        pytest.param("a", id="ascii"),
        pytest.param("", id="private-use"),
        pytest.param("\U0001f600", id="astral"),
    ],
)
@pytest.mark.parametrize(
    ("probe", "written"),
    [
        pytest.param("\x00", "", id="nul"),
        pytest.param("\x1f", "", id="unit-separator"),
        pytest.param("\ud800", "", id="first-surrogate"),
        pytest.param("\udfff", "", id="last-surrogate"),
        pytest.param("￾", "", id="noncharacter-fffe"),
        pytest.param("￿", "", id="noncharacter-ffff"),
        pytest.param("&", "&amp;", id="ampersand"),
        pytest.param(">", "&gt;", id="greater-than"),
        pytest.param("\r", "&#xD;", id="carriage-return"),
        pytest.param("\t", "\t", id="tab"),
        pytest.param("\n", "\n", id="newline"),
        pytest.param("퟿", "퟿", id="below-surrogates"),
        pytest.param("�", "�", id="replacement-character"),
    ],
)
def test_text_writes_a_character_at_every_offset(filler: str, probe: str, written: str) -> None:
    placements = [(filler * at, filler * (length - at - 1)) for length in range(1, 20) for at in range(length)]
    outs = [Element("p", children=[Text(head + probe + tail)]).canonicalize() for head, tail in placements]
    assert outs == [f"<p>{head}{written}{tail}</p>".encode() for head, tail in placements]


@pytest.mark.parametrize(
    ("probe", "written"),
    [
        pytest.param("\x01", "", id="c0-control"),
        pytest.param("\udc00", "", id="lone-surrogate"),
        pytest.param("￿", "", id="noncharacter-ffff"),
        pytest.param('"', "&quot;", id="quote"),
        pytest.param("<", "&lt;", id="less-than"),
        pytest.param("\t", "&#x9;", id="tab"),
        pytest.param("\n", "&#xA;", id="newline"),
        pytest.param(">", ">", id="greater-than"),
        pytest.param("?", "?", id="question-mark"),
    ],
)
def test_attribute_value_writes_a_character_at_every_offset(probe: str, written: str) -> None:
    placements = [("x" * at, "x" * (length - at - 1)) for length in range(1, 12) for at in range(length)]
    outs = [Element("p", {"title": head + probe + tail}).canonicalize() for head, tail in placements]
    assert outs == [f'<p title="{head}{written}{tail}"></p>'.encode() for head, tail in placements]


@pytest.mark.parametrize(("make_host", "tags"), _HOSTS)
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("a\x01b", "ab", id="c0-control"),
        pytest.param("a\ufffeb", "ab", id="noncharacter-fffe"),
        pytest.param("a\ud800b", "ab", id="lone-surrogate"),
        pytest.param("\x01>&", ">&amp;", id="control-before-literal-gt"),
        pytest.param("a\x01\t\n\rb", "a&#x9;&#xA;&#xD;b", id="control-before-whitespace-controls"),
    ],
)
def test_attribute_value_drops_characters_xml_cannot_hold(
    make_host: Callable[[], Element], tags: tuple[bytes, bytes], value: str, expected: str
) -> None:
    host = make_host()
    host.attrs["title"] = value
    out = host.canonicalize()
    assert out == tags[0] + b' title="' + expected.encode() + b'">' + tags[1]
    ET.fromstring(out)  # ruff:ignore[suspicious-xml-element-tree-usage]  # our own canonical output, parsed to assert it is well-formed


def test_processing_instruction_with_data() -> None:
    assert Element("a", children=[ProcessingInstruction("pi", "d a")]).canonicalize() == b"<a><?pi d a?></a>"


def test_processing_instruction_without_data_omits_space() -> None:
    assert Element("a", children=[ProcessingInstruction("pi", "")]).canonicalize() == b"<a><?pi?></a>"


def test_processing_instruction_invalid_target_is_omitted() -> None:
    assert Element("a", children=[ProcessingInstruction("1x", "d")]).canonicalize() == b"<a></a>"


def test_comment_dropped_by_default() -> None:
    assert _one("<a><!--c--><b/></a>", "a").canonicalize() == b"<a><b></b></a>"


def test_comment_kept_with_comments() -> None:
    assert _one("<a><!--c--><b/></a>", "a").canonicalize(Canonical(with_comments=True)) == b"<a><!--c--><b></b></a>"


def test_doctype_is_dropped() -> None:
    assert parse("<!doctype html><p>x").canonicalize() == (b"<html><head></head><body><p>x</p></body></html>")


def test_document_leading_comment_gets_trailing_newline() -> None:
    assert parse("<!--top--><p>x").canonicalize(Canonical(with_comments=True)) == (
        b"<!--top-->\n<html><head></head><body><p>x</p></body></html>"
    )


def test_document_leading_comment_dropped_without_comments() -> None:
    assert parse("<!--top--><p>x").canonicalize() == b"<html><head></head><body><p>x</p></body></html>"


@pytest.mark.parametrize("comment_length", [pytest.param(8192, id="medium"), pytest.param(32768, id="large")])
@pytest.mark.parametrize(
    ("parse_document", "source", "expected"),
    [
        pytest.param(parse, "<p>x</p>", b"<html><head></head><body><p>x</p></body></html>", id="html"),
        pytest.param(parse_xml, '<r xmlns="urn:x">x</r>', b'<r xmlns="urn:x">x</r>', id="xml"),
    ],
)
def test_document_drops_large_comments(
    parse_document: Callable[[str], Document], source: str, expected: bytes, comment_length: int
) -> None:
    assert parse_document(f"{source}<!--{'x' * comment_length}-->").canonicalize() == expected


@pytest.mark.parametrize(
    ("parse_document", "source", "expected"),
    [
        pytest.param(
            parse_xml,
            f'<r xmlns="urn:x">{"<a/>" * 1024}</r>',
            b'<r xmlns="urn:x">' + b"<a></a>" * 1024 + b"</r>",
            id="xml",
        ),
        pytest.param(
            parse,
            f"<main>{'<p>x</p>' * 512}</main>",
            b"<html><head></head><body><main>" + b"<p>x</p>" * 512 + b"</main></body></html>",
            id="html",
        ),
    ],
)
def test_document_canonicalizes_many_elements(
    parse_document: Callable[[str], Document], source: str, expected: bytes
) -> None:
    assert parse_document(source).canonicalize() == expected


def test_document_processing_instructions_bracket_the_root() -> None:
    doc = parse("<p>x")
    root = doc.select_one("html")
    assert root is not None
    root.insert_before(ProcessingInstruction("pi", "lead"))
    root.insert_after(ProcessingInstruction("pi", "tail"))
    out = doc.canonicalize()
    assert out.startswith(b"<?pi lead?>\n<html>")
    assert out.endswith(b"</html>\n<?pi tail?>")


def test_document_trailing_comment_gets_leading_newline() -> None:
    doc = parse("<!doctype html><html></html><!--after-->")
    assert doc.canonicalize(Canonical(with_comments=True)).endswith(b"</html>\n<!--after-->")


def test_whitespace_in_content_is_preserved() -> None:
    node = Element("a", children=[Text(" x "), Element("b"), Text(" y ")])
    assert node.canonicalize() == b"<a> x <b></b> y </a>"


def test_long_namespace_uris_canonicalize_in_any_declaration_order() -> None:
    uri_x = f"urn:{'x' * 129}"
    uri_y = f"urn:{'y' * 130}"
    first = parse_xml(f'<svg xmlns:a="{uri_x}" xmlns:b="{uri_y}"><g a:p="1" b:q="2"/></svg>')
    second = parse_xml(f'<svg xmlns:b="{uri_y}" xmlns:a="{uri_x}"><g b:q="2" a:p="1"/></svg>')
    assert first.canonicalize() == second.canonicalize()


def test_exclusive_drops_unused_ancestor_namespace() -> None:
    node = _one("<svg xlink:href=x><g><rect/></g></svg>", "g")
    assert node.canonicalize(Canonical(exclusive=True)) == b'<g xmlns="http://www.w3.org/2000/svg"><rect></rect></g>'


def test_inclusive_keeps_in_scope_ancestor_namespace() -> None:
    node = _one("<svg xlink:href=x><g><rect/></g></svg>", "g")
    assert node.canonicalize() == (
        b'<g xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"><rect></rect></g>'
    )


def test_exclusive_renders_namespace_on_using_element() -> None:
    node = _one("<svg xlink:href=x><g><rect/></g></svg>", "svg")
    assert node.canonicalize(Canonical(exclusive=True)) == (
        b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"'
        b' xlink:href="x"><g><rect></rect></g></svg>'
    )


def test_inclusive_ns_prefixes_promotes_unused_prefix_to_apex() -> None:
    node = _one("<svg xlink:href=x><g><rect/></g></svg>", "g")
    assert node.canonicalize(Canonical(exclusive=True, inclusive_ns_prefixes=("xlink",))) == (
        b'<g xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"><rect></rect></g>'
    )


def test_inclusive_ns_prefixes_ignores_prefix_not_in_scope() -> None:
    node = _one("<svg><g><rect/></g></svg>", "g")
    assert node.canonicalize(Canonical(exclusive=True, inclusive_ns_prefixes=("xlink",))) == (
        b'<g xmlns="http://www.w3.org/2000/svg"><rect></rect></g>'
    )


def test_inclusive_ns_prefixes_ignores_unrelated_prefix() -> None:
    node = _one("<svg xlink:href=x><g><rect/></g></svg>", "g")
    assert node.canonicalize(Canonical(exclusive=True, inclusive_ns_prefixes=("other",))) == (
        b'<g xmlns="http://www.w3.org/2000/svg"><rect></rect></g>'
    )


def test_inclusive_ns_prefixes_matches_within_a_longer_list() -> None:
    node = _one("<svg xlink:href=x><g><rect/></g></svg>", "g")
    assert node.canonicalize(Canonical(exclusive=True, inclusive_ns_prefixes=("xlink", "extra"))) == (
        b'<g xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"><rect></rect></g>'
    )


def test_exclusive_nested_use_renders_prefix_once() -> None:
    out = _one("<svg xlink:href=a><g xlink:href=b><rect/></g></svg>", "svg").canonicalize(Canonical(exclusive=True))
    assert out.count(b"xmlns:xlink") == 1


@pytest.mark.parametrize(
    "source_order",
    [pytest.param({"ab": "2", "a": "1"}, id="reversed"), pytest.param({"a": "1", "ab": "2"}, id="sorted")],
)
def test_attributes_sort_shorter_local_name_first(source_order: dict[str, str]) -> None:
    assert Element("p", source_order).canonicalize() == b'<p a="1" ab="2"></p>'


def test_stored_namespace_prefix_declaration_is_dropped() -> None:
    # turbohtml's HTML infoset binds no arbitrary prefix, so a stored xmlns:* is not a plain attribute
    assert Element("p", {"xmlns:foo": "bar", "id": "x"}).canonicalize() == b'<p id="x"></p>'


@pytest.mark.parametrize(
    ("source", "selector", "options", "expected"),
    [
        pytest.param(
            '<p xmlns:p="urn:p"><p:x></p:x></p>',
            "p",
            Canonical(),
            b'<p xmlns:p="urn:p"><p:x></p:x></p>',
            id="root-inclusive",
        ),
        pytest.param(
            '<p xmlns:p="urn:p"><p:x></p:x></p>',
            "p",
            Canonical(exclusive=True),
            b'<p><p:x xmlns:p="urn:p"></p:x></p>',
            id="root-exclusive",
        ),
        pytest.param(
            '<r xmlns:p="urn:p"><p:a></p:a></r>',
            "r",
            Canonical(),
            b'<r xmlns:p="urn:p"><p:a></p:a></r>',
            id="descendant-inclusive",
        ),
        pytest.param(
            '<r xmlns:p="urn:p"><a><p:b></p:b></a></r>',
            "a",
            Canonical(),
            b'<a xmlns:p="urn:p"><p:b></p:b></a>',
            id="ancestor-inclusive",
        ),
        pytest.param(
            '<r xmlns:p="urn:p"><a><p:b></p:b></a></r>',
            "a",
            Canonical(exclusive=True),
            b'<a><p:b xmlns:p="urn:p"></p:b></a>',
            id="ancestor-exclusive",
        ),
        pytest.param(
            '<r xmlns:p="urn:old"><a xmlns:p="urn:new"><p:b></p:b></a></r>',
            "r",
            Canonical(),
            b'<r xmlns:p="urn:old"><a xmlns:p="urn:new"><p:b></p:b></a></r>',
            id="redeclared-inclusive",
        ),
        pytest.param(
            '<r xmlns:p="urn:old"><a xmlns:p="urn:new"><p:b></p:b></a></r>',
            "r",
            Canonical(exclusive=True),
            b'<r><a><p:b xmlns:p="urn:new"></p:b></a></r>',
            id="redeclared-exclusive",
        ),
        pytest.param(
            '<r xmlns:p="urn:p"><a xmlns:p="urn:p"><p:b></p:b></a></r>',
            "r",
            Canonical(),
            b'<r xmlns:p="urn:p"><a><p:b></p:b></a></r>',
            id="same-uri-redeclared-inclusive",
        ),
        pytest.param(
            '<r xmlns:p="urn:p"><p:a><p:b></p:b></p:a></r>',
            "r",
            Canonical(exclusive=True),
            b'<r><p:a xmlns:p="urn:p"><p:b></p:b></p:a></r>',
            id="nested-use-renders-once-exclusive",
        ),
        pytest.param(
            '<p xmlns:p="urn:p" p:a="v"></p>',
            "p",
            Canonical(),
            b'<p xmlns:p="urn:p" p:a="v"></p>',
            id="attribute-inclusive",
        ),
        pytest.param(
            '<p xmlns:p="urn:p" p:a="v"></p>',
            "p",
            Canonical(exclusive=True),
            b'<p xmlns:p="urn:p" p:a="v"></p>',
            id="attribute-exclusive",
        ),
        pytest.param(
            '<svg xmlns:p="urn:p"><p:x></p:x></svg>',
            "svg",
            Canonical(),
            b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:p="urn:p"><p:x></p:x></svg>',
            id="beside-default-inclusive",
        ),
        pytest.param(
            '<svg xmlns:p="urn:p"><p:x></p:x></svg>',
            "svg",
            Canonical(exclusive=True),
            b'<svg xmlns="http://www.w3.org/2000/svg"><p:x xmlns:p="urn:p"></p:x></svg>',
            id="beside-default-exclusive",
        ),
        pytest.param(
            '<r xmlns:p="urn:p"><a><b></b></a></r>',
            "a",
            Canonical(),
            b"<a><b></b></a>",
            id="unused-ancestor-inclusive",
        ),
        pytest.param(
            '<r xmlns:p="urn:p"><a><b></b></a></r>',
            "a",
            Canonical(exclusive=True),
            b"<a><b></b></a>",
            id="unused-ancestor-exclusive",
        ),
        pytest.param(
            '<r><a xmlns:p="urn:p"></a><c><p:d></p:d></c></r>',
            "r",
            Canonical(),
            b'<r><a xmlns:p="urn:p"></a><c><p:d></p:d></c></r>',
            id="sibling-declaration-inclusive",
        ),
        pytest.param(
            '<r><a xmlns:p="urn:p"></a><c><p:d></p:d></c></r>',
            "r",
            Canonical(exclusive=True),
            b"<r><a></a><c><p:d></p:d></c></r>",
            id="sibling-declaration-exclusive",
        ),
        pytest.param(
            '<r xmlns:p="urn:p"><a><b></b></a></r>',
            "a",
            Canonical(exclusive=True, inclusive_ns_prefixes=("p",)),
            b'<a xmlns:p="urn:p"><b></b></a>',
            id="forced-ancestor-exclusive",
        ),
        pytest.param(
            '<r><a xmlns:p="urn:p"><b></b></a></r>',
            "r",
            Canonical(exclusive=True, inclusive_ns_prefixes=("p",)),
            b'<r><a xmlns:p="urn:p"><b></b></a></r>',
            id="forced-descendant-exclusive",
        ),
        pytest.param(
            '<p xmlns:p="urn:p"><p:x></p:x></p>',
            "p",
            Canonical(exclusive=True, inclusive_ns_prefixes=("p",)),
            b'<p xmlns:p="urn:p"><p:x></p:x></p>',
            id="forced-used-root-exclusive",
        ),
        pytest.param(
            '<svg xmlns:z="urn:z" xmlns:a="urn:a" xlink:href="v"><a:x></a:x><z:y></z:y></svg>',
            "svg",
            Canonical(),
            (
                b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:a="urn:a" xmlns:xlink="http://www.w3.org/1999/xlink"'
                b' xmlns:z="urn:z" xlink:href="v"><a:x></a:x><z:y></z:y></svg>'
            ),
            id="sorted-around-xlink",
        ),
    ],
)
def test_html_c14n_renders_prefixed_declaration_where_used(
    source: str, selector: str, options: Canonical, expected: bytes
) -> None:
    assert _one(source, selector).canonicalize(options) == expected


@pytest.mark.parametrize(
    ("source", "selector", "options", "expected"),
    [
        pytest.param(
            '<r xmlns:p="urn:p"><a>text<p:b></p:b></a></r>',
            "a",
            Canonical(),
            b'<a xmlns:p="urn:p">text<p:b></p:b></a>',
            id="text-in-subtree-inclusive",
        ),
        pytest.param(
            '<r xmlns:p="urn:p"><a>text<p:b></p:b></a></r>',
            "a",
            Canonical(exclusive=True),
            b'<a>text<p:b xmlns:p="urn:p"></p:b></a>',
            id="text-in-subtree-exclusive",
        ),
        pytest.param(
            '<r xmlns:p="urn:p" class="x"><a><p:b></p:b></a></r>',
            "a",
            Canonical(),
            b'<a xmlns:p="urn:p"><p:b></p:b></a>',
            id="plain-attribute-on-ancestor",
        ),
        pytest.param(
            '<r xmlns:p="urn:old"><a xmlns:p="urn:new"><p:b></p:b></a></r>',
            "a",
            Canonical(),
            b'<a xmlns:p="urn:new"><p:b></p:b></a>',
            id="redeclared-above-apex-inclusive",
        ),
        pytest.param(
            '<r xmlns:p="urn:old"><a xmlns:p="urn:new"><p:b></p:b></a></r>',
            "a",
            Canonical(exclusive=True),
            b'<a><p:b xmlns:p="urn:new"></p:b></a>',
            id="redeclared-above-apex-exclusive",
        ),
        pytest.param(
            '<r xmlns:p="urn:old"><a xmlns:p="urn:new"><c><p:b></p:b></c></a></r>',
            "c",
            Canonical(),
            b'<c xmlns:p="urn:new"><p:b></p:b></c>',
            id="nearest-redeclaration-above-apex-inclusive",
        ),
        pytest.param(
            '<r xmlns:p="urn:old"><a xmlns:p="urn:new"><c><p:b></p:b></c></a></r>',
            "c",
            Canonical(exclusive=True),
            b'<c><p:b xmlns:p="urn:new"></p:b></c>',
            id="nearest-redeclaration-above-apex-exclusive",
        ),
        pytest.param(
            '<r xmlns:p="urn:p"><p:a p:b="v"></p:a></r>',
            "r",
            Canonical(exclusive=True),
            b'<r><p:a xmlns:p="urn:p" p:b="v"></p:a></r>',
            id="tag-and-attribute-use-one-prefix-exclusive",
        ),
        pytest.param(
            '<r xmlns:p="urn:p"><p:a plain="v"></p:a></r>',
            "r",
            Canonical(exclusive=True),
            b'<r><p:a xmlns:p="urn:p" plain="v"></p:a></r>',
            id="plain-attribute-exclusive",
        ),
        pytest.param(
            '<r xmlns:p="urn:old"><p:a><x xmlns:p="urn:new"><p:b></p:b></x></p:a></r>',
            "r",
            Canonical(exclusive=True),
            b'<r><p:a xmlns:p="urn:old"><x><p:b xmlns:p="urn:new"></p:b></x></p:a></r>',
            id="nested-redeclared-use-exclusive",
        ),
        pytest.param(
            '<r xmlns:p="urn:a"><a xmlns:p="urn:bb"><p:b></p:b></a></r>',
            "r",
            Canonical(),
            b'<r xmlns:p="urn:a"><a xmlns:p="urn:bb"><p:b></p:b></a></r>',
            id="redeclared-with-longer-uri-inclusive",
        ),
        pytest.param(
            '<r xmlns:p="urn:p"><a><b></b></a></r>',
            "a",
            Canonical(exclusive=True, inclusive_ns_prefixes=("pq",)),
            b"<a><b></b></a>",
            id="inclusive-list-entry-extends-prefix",
        ),
        pytest.param(
            '<r xmlns:abc="urn:a" xmlns:hello="urn:h" xmlns:xml="urn:x" xmlns:xmlns="urn:m" xmlns:xlink="urn:custom"'
            ' abc:x="1" hello:y="2" xlink:href="v"></r>',
            "r",
            Canonical(),
            (
                b'<r xmlns:abc="urn:a" xmlns:hello="urn:h" xmlns:xlink="http://www.w3.org/1999/xlink"'
                b' abc:x="1" hello:y="2" xlink:href="v"></r>'
            ),
            id="reserved-and-short-or-long-prefixes",
        ),
        pytest.param('<p xmlns:p=""></p>', "p", Canonical(), b"<p></p>", id="empty-declaration-value"),
    ],
)
def test_html_c14n_prefixed_declaration_edge_shapes(
    source: str, selector: str, options: Canonical, expected: bytes
) -> None:
    assert _one(source, selector).canonicalize(options) == expected


@pytest.mark.parametrize(
    ("source", "selector", "options", "expected"),
    [
        pytest.param(
            '<r xmlns:p="urn:p"><a><!--c--><p:b></p:b></a></r>',
            "a",
            Canonical(with_comments=True),
            b'<a xmlns:p="urn:p"><!--c--><p:b></p:b></a>',
            id="comment-kept-in-subtree",
        ),
        pytest.param(
            '<r xmlns:p="urn:p"><a><!--c--><p:b></p:b></a></r>',
            "a",
            Canonical(),
            b'<a xmlns:p="urn:p"><p:b></p:b></a>',
            id="comment-dropped-in-subtree",
        ),
        pytest.param(
            '<r xmlns:p="urn:p"><svg xlink:href="v"><g></g></svg><p:c></p:c></r>',
            "r",
            Canonical(),
            (
                b'<r xmlns:p="urn:p"><svg xmlns="http://www.w3.org/2000/svg"'
                b' xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="v"><g></g></svg><p:c></p:c></r>'
            ),
            id="xlink-binding-closes-inside-tree",
        ),
        pytest.param(
            '<svg xlink:href="v" xmlns:p="urn:p"><g><p:x></p:x></g></svg>',
            "g",
            Canonical(),
            (
                b'<g xmlns="http://www.w3.org/2000/svg" xmlns:p="urn:p" xmlns:xlink="http://www.w3.org/1999/xlink">'
                b"<p:x></p:x></g>"
            ),
            id="xlink-binding-inherited-from-ancestor",
        ),
        pytest.param(
            '<r xmlns:p="urn:p"><template><p:x></p:x></template></r>',
            "r",
            Canonical(),
            b'<r xmlns:p="urn:p"><template><p:x></p:x></template></r>',
            id="template-content-container",
        ),
        pytest.param(
            '<svg xmlns="http://www.w3.org/2000/svg" xmlns:p="urn:p"><p:x></p:x></svg>',
            "svg",
            Canonical(),
            b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:p="urn:p"><p:x></p:x></svg>',
            id="default-declaration-beside-prefixed",
        ),
        pytest.param(
            '<r xmlns:p="urn:p"><a><b></b></a></r>',
            "a",
            Canonical(exclusive=True, inclusive_ns_prefixes=("a", "p")),
            b'<a xmlns:p="urn:p"><b></b></a>',
            id="inclusive-list-matches-a-later-entry",
        ),
        pytest.param(
            '<r xmlns:p="urn:p"><a><b></b></a></r>',
            "a",
            Canonical(exclusive=True, inclusive_ns_prefixes=("p", "extra")),
            b'<a xmlns:p="urn:p"><b></b></a>',
            id="inclusive-list-matches-an-earlier-entry",
        ),
        pytest.param(
            '<r xmlns:p="urn:p"><a><b></b></a></r>',
            "a",
            Canonical(exclusive=True, inclusive_ns_prefixes=("q",)),
            b"<a><b></b></a>",
            id="inclusive-list-without-the-prefix",
        ),
        pytest.param(
            '<p xmlns:p="urn:p"></p>',
            "p",
            Canonical(),
            b"<p></p>",
            id="unused-declaration-on-root",
        ),
    ],
)
def test_html_c14n_prefixed_tree_walk_shapes(source: str, selector: str, options: Canonical, expected: bytes) -> None:
    assert _one(source, selector).canonicalize(options) == expected


def test_html_c14n_prefixed_element_keeps_processing_instructions() -> None:
    node = Element("p", {"xmlns:p": "urn:p"}, children=[ProcessingInstruction("pi", "d"), Element("p:x")])
    assert node.canonicalize() == b'<p xmlns:p="urn:p"><?pi d?><p:x></p:x></p>'


def test_html_c14n_prefixed_doctype_apex_is_empty() -> None:
    doc = parse('<!doctype html><p xmlns:p="urn:p"></p>')
    assert doc.children[0].canonicalize() == b""


def test_html_c14n_prefixed_document_drops_comments_by_default() -> None:
    assert parse('<!--lead--><p xmlns:p="urn:p"><p:x></p:x></p>').canonicalize() == (
        b'<html><head></head><body><p xmlns:p="urn:p"><p:x></p:x></p></body></html>'
    )


def test_html_c14n_prefixed_document_brackets_root_with_comments_and_processing_instructions() -> None:
    doc = parse('<!--lead--><p xmlns:p="urn:p"><p:x></p:x></p>')
    root = doc.select_one("html")
    assert root is not None
    root.insert_before(ProcessingInstruction("pi", "lead"))
    root.insert_after(ProcessingInstruction("pi", "tail"))
    assert doc.canonicalize(Canonical(with_comments=True)) == (
        b'<!--lead-->\n<?pi lead?>\n<html><head></head><body><p xmlns:p="urn:p"><p:x></p:x></p></body></html>\n'
        b"<?pi tail?>"
    )


def test_html_c14n_prefixed_document_keeps_trailing_comment_after_root() -> None:
    doc = parse('<!doctype html><html xmlns:p="urn:p"><p:x></p:x></html><!--after-->')
    assert doc.canonicalize(Canonical(with_comments=True)).endswith(b"</html>\n<!--after-->")


@pytest.mark.parametrize(
    ("html_source", "xml_source", "selector", "options"),
    [
        pytest.param(
            '<p xmlns:p="urn:p"><p:x></p:x></p>',
            '<p xmlns:p="urn:p"><p:x></p:x></p>',
            "p",
            Canonical(),
            id="root-inclusive",
        ),
        pytest.param(
            '<p xmlns:p="urn:p"><p:x></p:x></p>',
            '<p xmlns:p="urn:p"><p:x></p:x></p>',
            "p",
            Canonical(exclusive=True),
            id="root-exclusive",
        ),
        pytest.param(
            '<r xmlns:p="urn:p"><a><p:b></p:b></a></r>',
            '<r xmlns:p="urn:p"><a><p:b></p:b></a></r>',
            "a",
            Canonical(exclusive=True),
            id="ancestor-exclusive",
        ),
        pytest.param(
            '<r xmlns:p="urn:old"><a xmlns:p="urn:new"><p:b></p:b></a></r>',
            '<r xmlns:p="urn:old"><a xmlns:p="urn:new"><p:b></p:b></a></r>',
            "r",
            Canonical(exclusive=True),
            id="redeclared-exclusive",
        ),
        pytest.param(
            '<p xmlns:p="urn:p" p:a="v"></p>',
            '<p xmlns:p="urn:p" p:a="v"></p>',
            "p",
            Canonical(),
            id="attribute-inclusive",
        ),
        pytest.param(
            '<svg xmlns:p="urn:p"><p:x></p:x></svg>',
            '<svg xmlns="http://www.w3.org/2000/svg" xmlns:p="urn:p"><p:x></p:x></svg>',
            "svg",
            Canonical(),
            id="beside-default-inclusive",
        ),
    ],
)
def test_html_c14n_reparses_to_the_xml_canonical_form(
    html_source: str, xml_source: str, selector: str, options: Canonical
) -> None:
    output = _one(html_source, selector).canonicalize(options)
    xml_node = parse_xml(xml_source).select_one(selector)
    assert xml_node is not None
    assert output == xml_node.canonicalize(options)
    assert parse_xml(output.decode()).canonicalize(options) == output


@pytest.mark.parametrize(
    ("source", "selector", "options"),
    [
        pytest.param('<p xmlns:p="relative"><p:x></p:x></p>', "p", Canonical(), id="declared-inclusive"),
        pytest.param('<p xmlns:p="relative"><p:x></p:x></p>', "p", Canonical(exclusive=True), id="declared-exclusive"),
        pytest.param('<r xmlns:p="relative"><a><p:b></p:b></a></r>', "a", Canonical(), id="inherited-inclusive"),
        pytest.param(
            '<r xmlns:p="relative"><a><p:b></p:b></a></r>', "a", Canonical(exclusive=True), id="inherited-exclusive"
        ),
    ],
)
def test_html_c14n_rejects_used_relative_namespace(source: str, selector: str, options: Canonical) -> None:
    with pytest.raises(ValueError, match="relative namespace URI cannot be canonicalized"):
        _one(source, selector).canonicalize(options)


def test_html_c14n_drops_unused_relative_namespace() -> None:
    assert _one('<p xmlns:p="relative"><x></x></p>', "p").canonicalize() == b"<p><x></x></p>"


@pytest.mark.parametrize(
    "prefix",
    [pytest.param("é", id="two-bytes"), pytest.param("中", id="three-bytes"), pytest.param("𐐀", id="four-bytes")],
)
@pytest.mark.parametrize(
    "options", [pytest.param(Canonical(), id="inclusive"), pytest.param(Canonical(exclusive=True), id="exclusive")]
)
def test_html_c14n_declares_non_ascii_prefix(prefix: str, options: Canonical) -> None:
    node = Element(f"{prefix}:x", {f"xmlns:{prefix}": "urn:x"})
    assert node.canonicalize(options) == f'<{prefix}:x xmlns:{prefix}="urn:x"></{prefix}:x>'.encode()


def test_html_c14n_prefixed_declaration_steps_grow_linearly(work_steps: WorkSteps) -> None:
    options: Final[Canonical] = Canonical(exclusive=True)
    work_steps.assert_linear(
        lambda count: partial(_one(_prefixed_siblings(count), "r").canonicalize, options), (500, 2000)
    )


def _prefixed_siblings(count: int) -> str:
    declarations = " ".join(f'xmlns:p{index}="urn:{index}"' for index in range(count))
    children = "".join(f"<p{index}:a></p{index}:a>" for index in range(count))
    return f"<r {declarations}>{children}</r>"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param('<r xmlns:p="urn:p" p:a="v"/>', b'<r xmlns:p="urn:p" p:a="v"></r>', id="used-attribute"),
        pytest.param('<r xmlns:p="urn:p"/>', b'<r xmlns:p="urn:p"></r>', id="unused-declaration"),
        pytest.param(
            '<r xmlns="urn:r"><a xmlns=""/></r>', b'<r xmlns="urn:r"><a xmlns=""></a></r>', id="default-reset"
        ),
        pytest.param('<r xmlns:p="urn:p"><p:a/></r>', b'<r xmlns:p="urn:p"><p:a></p:a></r>', id="inherited-prefix"),
        pytest.param(
            '<r xmlns:p="urn:a"><a xmlns:p="urn:b" p:x="v"/></r>',
            b'<r xmlns:p="urn:a"><a xmlns:p="urn:b" p:x="v"></a></r>',
            id="rebound-prefix",
        ),
        pytest.param('<r xmlns:é="urn:p" é:x="v"/>', '<r xmlns:é="urn:p" é:x="v"></r>'.encode(), id="unicode-prefix"),
    ],
)
def test_xml_c14n_renders_namespace_axis(source: str, expected: bytes) -> None:
    assert parse_xml(source).canonicalize() == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            '<r xmlns:a="urn:z" xmlns:z="urn:a" a:x="1" z:x="2"/>',
            b'<r xmlns:a="urn:z" xmlns:z="urn:a" z:x="2" a:x="1"></r>',
            id="different-uri-text",
        ),
        pytest.param(
            '<r xmlns:a="urn:p" xmlns:b="urn:p:q" b:x="1" a:x="2"/>',
            b'<r xmlns:a="urn:p" xmlns:b="urn:p:q" a:x="2" b:x="1"></r>',
            id="uri-prefix-reversed",
        ),
        pytest.param(
            '<r xmlns:a="urn:p" xmlns:b="urn:p:q" a:x="2" b:x="1"/>',
            b'<r xmlns:a="urn:p" xmlns:b="urn:p:q" a:x="2" b:x="1"></r>',
            id="uri-prefix-sorted",
        ),
        pytest.param(
            '<r xmlns:a="urn:p" xmlns:b="urn:p" b:x="1" a:y="2" a:w="3"/>',
            b'<r xmlns:a="urn:p" xmlns:b="urn:p" a:w="3" b:x="1" a:y="2"></r>',
            id="one-uri-two-prefixes",
        ),
        pytest.param(
            '<r xmlns:a="http://www.w3.org/XML/1998/namespaces" xmlns:b="http://www.w3.org/XML/1998/n"'
            ' a:x="1" xml:lang="en" b:x="2" x="3"/>',
            b'<r xmlns:a="http://www.w3.org/XML/1998/namespaces" xmlns:b="http://www.w3.org/XML/1998/n"'
            b' x="3" b:x="2" xml:lang="en" a:x="1"></r>',
            id="around-the-xml-namespace",
        ),
        pytest.param(
            '<r xmlns:a="urn:z" xmlns:b="urn:m"><c xmlns:a="urn:a" b:x="1" a:x="2"/><d b:x="1" a:x="2"/></r>',
            b'<r xmlns:a="urn:z" xmlns:b="urn:m"><c xmlns:a="urn:a" a:x="2" b:x="1"></c><d b:x="1" a:x="2"></d></r>',
            id="rebound-below",
        ),
    ],
)
def test_xml_c14n_sorts_attributes_by_namespace_uri(source: str, expected: bytes) -> None:
    assert parse_xml(source).canonicalize() == expected


def test_xml_c14n_apex_sorts_attributes_by_namespace_uri_declared_above() -> None:
    document: Final[Document] = parse_xml('<r xmlns:a="urn:z" xmlns:b="urn:m" xml:lang="en"><c a:x="1" b:x="2"/></r>')
    assert document.children[0].children[0].canonicalize() == (
        b'<c xmlns:a="urn:z" xmlns:b="urn:m" xml:lang="en" b:x="2" a:x="1"></c>'
    )


def test_xml_c14n_sorts_many_attributes_on_one_long_uri() -> None:
    count: Final[int] = 2000
    uri: Final[str] = f"urn:{'a' * count}"
    attributes: Final[str] = " ".join(f'p:a{index:05}=""' for index in range(count))
    assert (
        parse_xml(_attributes_on_one_uri(count)).canonicalize()
        == f'<r xmlns:p="{uri}"><a {attributes}></a></r>'.encode()
    )


def _attributes_on_one_uri(count: int) -> str:
    uri: Final[str] = f"urn:{'a' * count}"
    attributes: Final[str] = " ".join(f'p:a{index:05}=""' for index in reversed(range(count)))
    return f'<r xmlns:p="{uri}"><a {attributes}/></r>'


def test_xml_c14n_sorts_attributes_by_distinct_long_uris() -> None:
    uris: Final[list[str]] = [f"urn:{'u' * 150}{index:02}" for index in range(20)]
    declarations: Final[str] = " ".join(sorted(f'xmlns:p{19 - index:02}="{uri}"' for index, uri in enumerate(uris)))
    attributes: Final[list[str]] = [f'p{19 - index:02}:v="{index}"' for index in range(20)]
    plain: Final[str] = f'plain="{"w" * 200}"'
    shuffled: Final[str] = " ".join(attributes[index * 7 % 20] for index in range(20))
    source: Final[str] = f"<r {declarations} {shuffled} {plain}/>"
    assert parse_xml(source).canonicalize() == f"<r {declarations} {plain} {' '.join(attributes)}></r>".encode()


def test_xml_c14n_attribute_sort_steps_grow_linearly_in_uri_length(work_steps: WorkSteps) -> None:
    work_steps.assert_linear(lambda count: partial(parse_xml(_attributes_on_one_uri(count)).canonicalize), (500, 2000))


def test_xml_c14n_exclusive_redeclared_uri_steps_grow_linearly_in_uri_length(work_steps: WorkSteps) -> None:
    options: Final[Canonical] = Canonical(exclusive=True)
    work_steps.assert_linear(
        lambda count: partial(parse_xml(_redeclared_uri(count)).canonicalize, options), (500, 2000)
    )


def _redeclared_uri(count: int) -> str:
    uri: Final[str] = f"urn:{'a' * count}"
    return f'<p:r xmlns:p="{uri}"><m xmlns:p="{uri}">{"<p:a/>" * count}</m></p:r>'


@pytest.mark.parametrize(
    ("source", "exclusive", "expected"),
    [
        pytest.param(
            '<r xmlns:a="urn:a" xmlns:b="urn:b" a:x="1" b:y="2"><c xmlns:a="urn:c" b:z="3"/></r>',
            False,
            b'<r xmlns:a="urn:a" xmlns:b="urn:b" a:x="1" b:y="2"><c xmlns:a="urn:c" b:z="3"></c></r>',
            id="inclusive-rebound-and-two-prefixes",
        ),
        pytest.param(
            '<r xmlns:a="urn:a" xmlns:b="urn:b" a:x="1" b:y="2"><c xmlns:a="urn:c" b:z="3"/></r>',
            True,
            b'<r xmlns:a="urn:a" xmlns:b="urn:b" a:x="1" b:y="2"><c b:z="3"></c></r>',
            id="exclusive-unused-rebound",
        ),
        pytest.param(
            '<r xmlns="urn:r" xmlns:p="urn:p"><a xmlns=""><p:b xmlns:q="urn:q" q:x="1"/></a></r>',
            False,
            b'<r xmlns="urn:r" xmlns:p="urn:p"><a xmlns=""><p:b xmlns:q="urn:q" q:x="1"></p:b></a></r>',
            id="inclusive-default-reset-and-inherited-prefix",
        ),
        pytest.param(
            '<r xmlns="urn:r" xmlns:p="urn:p"><a xmlns=""><p:b xmlns:q="urn:q" q:x="1"/></a></r>',
            True,
            b'<r xmlns="urn:r"><a xmlns=""><p:b xmlns:p="urn:p" xmlns:q="urn:q" q:x="1"></p:b></a></r>',
            id="exclusive-default-reset-and-inherited-prefix",
        ),
        pytest.param(
            '<r xmlns:p="urn:p"><a><p:b p:x="1"/></a></r>',
            False,
            b'<r xmlns:p="urn:p"><a><p:b p:x="1"></p:b></a></r>',
            id="inclusive-distant-binding",
        ),
        pytest.param(
            '<r xmlns:p="urn:p"><a><p:b p:x="1"/></a></r>',
            True,
            b'<r><a><p:b xmlns:p="urn:p" p:x="1"></p:b></a></r>',
            id="exclusive-distant-binding",
        ),
        pytest.param(
            '<r xmlns:a="urn:a" xmlns:b="urn:b"><a xmlns:a="urn:c" b:x="1"><b a:x="2"/></a></r>',
            False,
            b'<r xmlns:a="urn:a" xmlns:b="urn:b"><a xmlns:a="urn:c" b:x="1"><b a:x="2"></b></a></r>',
            id="inclusive-rebound-at-intermediate-element",
        ),
        pytest.param(
            '<r xmlns:a="urn:a" xmlns:b="urn:b"><a xmlns:a="urn:c" b:x="1"><b a:x="2"/></a></r>',
            True,
            b'<r><a xmlns:b="urn:b" b:x="1"><b xmlns:a="urn:c" a:x="2"></b></a></r>',
            id="exclusive-rebound-at-intermediate-element",
        ),
        pytest.param(
            '<r id="x"><longprefix:a xmlns:longprefix="urn:p" id="y"/></r>',
            True,
            b'<r id="x"><longprefix:a xmlns:longprefix="urn:p" id="y"></longprefix:a></r>',
            id="exclusive-local-long-prefix",
        ),
        pytest.param(
            '<r><a xmlns=""><b/></a></r>',
            False,
            b"<r><a><b></b></a></r>",
            id="inclusive-empty-default-without-parent-binding",
        ),
        pytest.param(
            '<r xmlns=""><a xmlns="urn:a"/></r>',
            False,
            b'<r><a xmlns="urn:a"></a></r>',
            id="inclusive-new-default-after-empty-parent-binding",
        ),
    ],
)
def test_xml_c14n_nested_namespace_scopes(source: str, expected: bytes, *, exclusive: bool) -> None:
    assert parse_xml(source).canonicalize(Canonical(exclusive=exclusive)) == expected


def test_xml_c14n_forced_prefix_declared_below_apex() -> None:
    source: Final[str] = '<r><a xmlns:p="urn:p"><b/></a></r>'
    assert parse_xml(source).canonicalize(Canonical(exclusive=True, inclusive_ns_prefixes=("p",))) == (
        b'<r><a xmlns:p="urn:p"><b></b></a></r>'
    )


def test_xml_c14n_forced_empty_default_without_parent_binding() -> None:
    source: Final[str] = '<r><a xmlns=""><b/></a></r>'
    assert parse_xml(source).canonicalize(Canonical(exclusive=True, inclusive_ns_prefixes=("#default",))) == (
        b"<r><a><b></b></a></r>"
    )


def test_xml_c14n_apex_inherits_namespace_axis() -> None:
    document = parse_xml('<r xmlns:p="urn:p"><p:a/></r>')
    assert document.children[0].children[0].canonicalize() == b'<p:a xmlns:p="urn:p"></p:a>'


def test_xml_c14n_pickle_preserves_namespace_axis() -> None:
    restored = pickle.loads(pickle.dumps(parse_xml('<r xmlns:p="urn:p"><p:a/></r>')))  # ruff:ignore[suspicious-pickle-usage]  # this test's own bytes
    assert restored.canonicalize() == b'<r xmlns:p="urn:p"><p:a></p:a></r>'


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param('<r xmlns:p="urn:p"><p:a/></r>', b'<r><p:a xmlns:p="urn:p"></p:a></r>', id="used-child"),
        pytest.param('<r xmlns:p="urn:p"><a/></r>', b"<r><a></a></r>", id="unused-prefix"),
        pytest.param(
            '<r xmlns:p="urn:a"><a xmlns:p="urn:b"><p:x/></a></r>',
            b'<r><a><p:x xmlns:p="urn:b"></p:x></a></r>',
            id="rebound-prefix",
        ),
        pytest.param(
            '<r xmlns="urn:r"><a xmlns=""/></r>', b'<r xmlns="urn:r"><a xmlns=""></a></r>', id="default-reset"
        ),
    ],
)
def test_xml_c14n_exclusive_renders_visible_bindings(source: str, expected: bytes) -> None:
    assert parse_xml(source).canonicalize(Canonical(exclusive=True)) == expected


def test_xml_c14n_exclusive_keeps_forced_prefix() -> None:
    document = parse_xml('<r xmlns:p="urn:p"><a/></r>')
    assert (
        document.children[0].children[0].canonicalize(Canonical(exclusive=True, inclusive_ns_prefixes=("p",)))
        == b'<a xmlns:p="urn:p"></a>'
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            '<r xmlns:p="urn:p" xmlns:q="urn:q" p:a="1" q:a="2"/>',
            b'<r xmlns:p="urn:p" xmlns:q="urn:q" p:a="1" q:a="2"></r>',
            id="same-length-prefixes",
        ),
        pytest.param(
            '<r xmlns:xml="http://www.w3.org/XML/1998/namespace" xml:lang="en"/>',
            b'<r xml:lang="en"></r>',
            id="reserved-xml-prefix",
        ),
        pytest.param(
            '<r xmlns:ࠀ="urn:p" ࠀ:x="v"/>', '<r xmlns:ࠀ="urn:p" ࠀ:x="v"></r>'.encode(), id="three-byte-prefix"
        ),
        pytest.param('<r xmlns:𐀀="urn:p" 𐀀:x="v"/>', '<r xmlns:𐀀="urn:p" 𐀀:x="v"></r>'.encode(), id="four-byte-prefix"),
        pytest.param(
            '<r xmlns:p="urn:p" a="1" ab="2" p:a="3" p:ab="4"/>',
            b'<r xmlns:p="urn:p" a="1" ab="2" p:a="3" p:ab="4"></r>',
            id="local-name-length",
        ),
        pytest.param(
            '<r xmlns:p="urn:p" xmlns:q="urn:p" p:a="1" q:b="2"/>',
            b'<r xmlns:p="urn:p" xmlns:q="urn:p" p:a="1" q:b="2"></r>',
            id="same-namespace-uri",
        ),
    ],
)
def test_xml_c14n_namespace_prefixes_and_attribute_keys(source: str, expected: bytes) -> None:
    assert parse_xml(source).canonicalize() == expected


def test_xml_c14n_preserves_text_and_cdata() -> None:
    source: Final[str] = '<r xmlns:p="urn:p">a&amp;b&lt;c&gt;d&#xD;<![CDATA[<x>&]]></r>'
    assert parse_xml(source).canonicalize() == b'<r xmlns:p="urn:p">a&amp;b&lt;c&gt;d&#xD;&lt;x&gt;&amp;</r>'


def test_xml_c14n_plain_text() -> None:
    assert parse_xml('<r xmlns:p="urn:p">plain</r>').canonicalize() == b'<r xmlns:p="urn:p">plain</r>'


@pytest.mark.parametrize(
    ("source_value", "expected"),
    [
        pytest.param("&amp;", b"&amp;", id="ampersand"),
        pytest.param("&lt;", b"&lt;", id="less-than"),
        pytest.param("&quot;", b"&quot;", id="quote"),
        pytest.param("&#x9;", b"&#x9;", id="tab"),
        pytest.param("&#xA;", b"&#xA;", id="newline"),
        pytest.param("&#xD;", b"&#xD;", id="carriage-return"),
    ],
)
def test_xml_c14n_attribute_character_references(source_value: str, expected: bytes) -> None:
    source = f'<r xmlns:p="urn:p" p:a="{source_value}"/>'
    assert parse_xml(source).canonicalize() == b'<r xmlns:p="urn:p" p:a="' + expected + b'"></r>'


def test_xml_c14n_renders_comments_and_processing_instructions() -> None:
    source: Final[str] = '<r xmlns:p="urn:p"><!--x--><?pi a?><p:a/></r>'
    assert parse_xml(source).canonicalize(Canonical(with_comments=True)) == (
        b'<r xmlns:p="urn:p"><!--x--><?pi a?><p:a></p:a></r>'
    )


def test_xml_c14n_processing_instruction_without_data() -> None:
    assert parse_xml('<r xmlns:p="urn:p"><?pi?></r>').canonicalize() == b'<r xmlns:p="urn:p"><?pi?></r>'


def test_xml_c14n_omits_inserted_processing_instruction_with_invalid_target() -> None:
    root = cast("Element", parse_xml('<r xmlns:p="urn:p"/>').children[0])
    root.append(ProcessingInstruction("1x", "d"))
    assert root.canonicalize() == b'<r xmlns:p="urn:p"></r>'


def test_xml_c14n_document_nodes_surround_root() -> None:
    source: Final[str] = '<!--pre--><?pi lead?><!DOCTYPE r><r xmlns:p="urn:p"/><?pi tail?><!--post-->'
    assert parse_xml(source).canonicalize(Canonical(with_comments=True)) == (
        b'<!--pre-->\n<?pi lead?>\n<r xmlns:p="urn:p"></r>\n<?pi tail?>\n<!--post-->'
    )


def test_xml_c14n_inherits_xml_attributes_on_apex() -> None:
    document = parse_xml('<r xmlns:p="urn:p" xml:lang="en"><p:a xml:space="preserve"/></r>')
    assert document.children[0].children[0].canonicalize() == (
        b'<p:a xmlns:p="urn:p" xml:lang="en" xml:space="preserve"></p:a>'
    )


def test_xml_c14n_exclusive_omits_ancestor_xml_attributes() -> None:
    document = parse_xml(
        '<r xml:lang="en" xml:base="urn:root" xml:mode="x"><a xml:space="preserve"><b xml:lang="fr"/></a></r>'
    )
    node = document.children[0].children[0].children[0]
    assert node.canonicalize(Canonical(exclusive=True)) == b'<b xml:lang="fr"></b>'


def test_xml_c14n_1_1_does_not_inherit_xml_id() -> None:
    document = parse_xml('<r xmlns:p="urn:p" xml:id="top" xml:lang="en"><p:a/></r>')
    assert document.children[0].children[0].canonicalize(Canonical(version="1.1")) == (
        b'<p:a xmlns:p="urn:p" xml:lang="en"></p:a>'
    )


def test_xml_c14n_many_namespaces_and_attributes() -> None:
    names: Final[tuple[str, ...]] = tuple(f"p{index:02d}" for index in range(33))
    source: Final[str] = f"""<r {" ".join(f'xmlns:{name}="urn:{name}" {name}:x="v"' for name in names)}/>"""
    expected: Final[bytes] = (
        f"""<r {" ".join(f'xmlns:{name}="urn:{name}"' for name in names)} """
        f"""{" ".join(f'{name}:x="v"' for name in names)}></r>"""
    ).encode()
    assert parse_xml(source).canonicalize() == expected


@pytest.mark.parametrize(
    ("namespace", "options"),
    [
        pytest.param("relative", Canonical(), id="plain-relative"),
        pytest.param("./relative", Canonical(exclusive=True), id="exclusive-relative"),
        pytest.param("a/b", Canonical(version="1.1"), id="slash-before-colon"),
        pytest.param("1:bad", Canonical(), id="digit-before-colon"),
        pytest.param("é:bad", Canonical(), id="non-ascii-before-colon"),
        pytest.param("aé:bad", Canonical(), id="non-ascii-in-scheme"),
        pytest.param("a/:bad", Canonical(), id="slash-in-scheme"),
        pytest.param("https", Canonical(), id="scheme-without-colon"),
    ],
)
def test_xml_c14n_rejects_relative_namespace(namespace: str, options: Canonical) -> None:
    with pytest.raises(ValueError, match="relative namespace URI cannot be canonicalized"):
        parse_xml(f'<r xmlns:p="{namespace}"/>').canonicalize(options)


def test_xml_c14n_rejects_relative_namespace_with_heap_allocated_bindings() -> None:
    source: Final[str] = f"""<r {" ".join(f'xmlns:p{index}="urn:{index}"' for index in range(33))} xmlns:bad="rel"/>"""
    with pytest.raises(ValueError, match="relative namespace URI cannot be canonicalized"):
        parse_xml(source).canonicalize()


def test_xml_c14n_rejects_relative_namespace_in_a_descendant() -> None:
    document: Final[Document] = parse_xml('<r><c xmlns:p="relative"><p:a/></c></r>')
    with pytest.raises(ValueError, match="relative namespace URI cannot be canonicalized"):
        document.canonicalize()


def test_xml_c14n_rejects_an_inherited_relative_namespace() -> None:
    node: Final[Node] = parse_xml('<r xmlns:p="relative"><c><p:a/></c></r>').children[0].children[0]
    with pytest.raises(ValueError, match="relative namespace URI cannot be canonicalized"):
        node.canonicalize()


def test_xml_c14n_accepts_shadowed_relative_namespace_on_subtree() -> None:
    document: Final[Document] = parse_xml('<r xmlns:p="relative"><c xmlns:p="urn:absolute"><p:a/></c></r>')
    assert document.children[0].children[0].canonicalize() == (b'<c xmlns:p="urn:absolute"><p:a></p:a></c>')


def test_xml_c14n_accepts_empty_default_namespace() -> None:
    assert parse_xml('<r xmlns=""/>').canonicalize() == b"<r></r>"


@pytest.mark.parametrize(
    "scheme",
    ["URN", "urn", "urx", "urnx", "http", "https", "hex", "htx", "httx", "httpx", "httpsx", "u0", "u+", "u-", "u."],
)
def test_xml_c14n_accepts_absolute_namespace_scheme(scheme: str) -> None:
    assert parse_xml(f'<r xmlns:p="{scheme}:part"><p:a/></r>').canonicalize() == (
        f'<r xmlns:p="{scheme}:part"><p:a></p:a></r>'.encode()
    )


@pytest.mark.parametrize(
    "prefix",
    [pytest.param("é", id="two-byte"), pytest.param("ࠀ", id="three-byte"), pytest.param("𐀀", id="four-byte")],
)
def test_xml_c14n_exclusive_renders_unicode_prefix_on_child(prefix: str) -> None:
    source: Final[str] = f'<r xmlns:{prefix}="urn:p"><{prefix}:a/></r>'
    assert parse_xml(source).canonicalize(Canonical(exclusive=True)) == (
        f'<r><{prefix}:a xmlns:{prefix}="urn:p"></{prefix}:a></r>'.encode()
    )


def test_xml_c14n_exclusive_sorts_visible_prefixes() -> None:
    source: Final[str] = '<r xmlns:p="urn:p" xmlns:long="urn:long"><x long:a="1" p:a="2"/></r>'
    assert parse_xml(source).canonicalize(Canonical(exclusive=True)) == (
        b'<r><x xmlns:long="urn:long" xmlns:p="urn:p" long:a="1" p:a="2"></x></r>'
    )


def test_xml_c14n_exclusive_renders_rebound_prefix_after_nonusing_element() -> None:
    source: Final[str] = '<r xmlns:p="urn:a"><p:a><x xmlns:p="urn:b"><p:b/></x></p:a></r>'
    assert parse_xml(source).canonicalize(Canonical(exclusive=True)) == (
        b'<r><p:a xmlns:p="urn:a"><x><p:b xmlns:p="urn:b"></p:b></x></p:a></r>'
    )


def test_xml_c14n_exclusive_forced_prefix_list_renders_once() -> None:
    source: Final[str] = '<r xmlns:p="urn:p"><a/></r>'
    assert parse_xml(source).canonicalize(Canonical(exclusive=True, inclusive_ns_prefixes=("p", "other"))) == (
        b'<r xmlns:p="urn:p"><a></a></r>'
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param('<l xmlns:long="urn:long"/>', b"<l></l>", id="prefix-longer-than-tag"),
        pytest.param(
            '<pp:a xmlns:p="urn:p" xmlns:pp="urn:pp"/>',
            b'<pp:a xmlns:pp="urn:pp"></pp:a>',
            id="tag-prefix-longer-than-binding",
        ),
        pytest.param(
            '<p:a xmlns:p="urn:p" xmlns:pp="urn:pp"/>',
            b'<p:a xmlns:p="urn:p"></p:a>',
            id="same-leading-byte",
        ),
        pytest.param(
            '<ࠀ:a xmlns:é="urn:e" xmlns:ࠀ="urn:p"/>',
            '<ࠀ:a xmlns:ࠀ="urn:p"></ࠀ:a>'.encode(),
            id="different-utf8-length",
        ),
        pytest.param(
            '<ê:a xmlns:é="urn:e" xmlns:ê="urn:p"/>',
            '<ê:a xmlns:ê="urn:p"></ê:a>'.encode(),
            id="same-utf8-length",
        ),
        pytest.param('<r xmlns=""/>', b"<r></r>", id="empty-default-root"),
        pytest.param(
            '<r xmlns=""><a xmlns="urn:a"/></r>', b'<r><a xmlns="urn:a"></a></r>', id="new-default-after-empty"
        ),
    ],
)
def test_xml_c14n_exclusive_prefix_boundaries(source: str, expected: bytes) -> None:
    assert parse_xml(source).canonicalize(Canonical(exclusive=True)) == expected


def test_xml_c14n_exclusive_forced_default_namespace() -> None:
    source: Final[str] = '<r xmlns="urn:d"><a/></r>'
    assert parse_xml(source).canonicalize(Canonical(exclusive=True, inclusive_ns_prefixes=("#default",))) == (
        b'<r xmlns="urn:d"><a></a></r>'
    )


def test_xml_c14n_exclusive_forces_unused_default_namespace() -> None:
    source: Final[str] = '<p:r xmlns="urn:d" xmlns:p="urn:p"><a xmlns="urn:e"/></p:r>'
    assert parse_xml(source).canonicalize(Canonical(exclusive=True, inclusive_ns_prefixes=("#default",))) == (
        b'<p:r xmlns="urn:d" xmlns:p="urn:p"><a xmlns="urn:e"></a></p:r>'
    )


def test_xml_c14n_exclusive_forced_prefix_rebound_on_child() -> None:
    source: Final[str] = '<r xmlns:p="urn:p"><p:a xmlns:p="urn:q"/></r>'
    assert parse_xml(source).canonicalize(Canonical(exclusive=True, inclusive_ns_prefixes=("p",))) == (
        b'<r xmlns:p="urn:p"><p:a xmlns:p="urn:q"></p:a></r>'
    )


def test_xml_c14n_exclusive_forced_prefix_rebound_without_use() -> None:
    source: Final[str] = '<r xmlns:p="urn:a"><a xmlns:p="urn:b"><b/></a></r>'
    assert parse_xml(source).canonicalize(Canonical(exclusive=True, inclusive_ns_prefixes=("p",))) == (
        b'<r xmlns:p="urn:a"><a xmlns:p="urn:b"><b></b></a></r>'
    )


@pytest.mark.parametrize(
    ("source", "exclusive", "expected"),
    [
        pytest.param(
            '<r><a xmlns:p="urn:a"><p:x/></a><b xmlns:p="urn:b"><p:x/></b><c/></r>',
            False,
            b'<r><a xmlns:p="urn:a"><p:x></p:x></a><b xmlns:p="urn:b"><p:x></p:x></b><c></c></r>',
            id="inclusive-sibling-bindings",
        ),
        pytest.param(
            '<r><a xmlns:p="urn:a"><p:x/></a><b xmlns:p="urn:b"><p:x/></b><c/></r>',
            True,
            b'<r><a><p:x xmlns:p="urn:a"></p:x></a><b><p:x xmlns:p="urn:b"></p:x></b><c></c></r>',
            id="exclusive-sibling-bindings",
        ),
        pytest.param(
            '<r xmlns:p="urn:a"><a xmlns:p="urn:b"/><b xmlns:p="urn:a"/><p:c/></r>',
            False,
            b'<r xmlns:p="urn:a"><a xmlns:p="urn:b"></a><b></b><p:c></p:c></r>',
            id="inclusive-shadowed-binding-restored",
        ),
        pytest.param(
            '<r xmlns:p="urn:a"><p:a><p:b xmlns:p="urn:b"/><p:c/></p:a></r>',
            True,
            b'<r><p:a xmlns:p="urn:a"><p:b xmlns:p="urn:b"></p:b><p:c></p:c></p:a></r>',
            id="exclusive-shadowed-use-restored",
        ),
        pytest.param(
            '<r xmlns:p="urn:p"><p:a p:x="1" p:y="2"/></r>',
            True,
            b'<r><p:a xmlns:p="urn:p" p:x="1" p:y="2"></p:a></r>',
            id="exclusive-prefix-used-three-times",
        ),
        pytest.param(
            '<r><a xmlns="urn:a"/></r>',
            True,
            b'<r><a xmlns="urn:a"></a></r>',
            id="exclusive-new-default-under-unbound-use",
        ),
    ],
)
def test_xml_c14n_scope_follows_open_elements(source: str, expected: bytes, *, exclusive: bool) -> None:
    assert parse_xml(source).canonicalize(Canonical(exclusive=exclusive)) == expected


def test_xml_c14n_apex_inherits_nearest_binding() -> None:
    document = parse_xml('<r xmlns:p="urn:a"><c xmlns:p="urn:b"><p:a/></c></r>')
    assert document.children[0].children[0].children[0].canonicalize() == b'<p:a xmlns:p="urn:b"></p:a>'


@pytest.mark.parametrize("listed", [pytest.param("pp", id="prefix"), pytest.param("#defaults", id="default")])
def test_xml_c14n_exclusive_forced_prefix_matches_whole_name(listed: str) -> None:
    source: Final[str] = '<q:r xmlns="urn:d" xmlns:p="urn:p" xmlns:q="urn:q"><q:a/></q:r>'
    assert parse_xml(source).canonicalize(Canonical(exclusive=True, inclusive_ns_prefixes=(listed,))) == (
        b'<q:r xmlns:q="urn:q"><q:a></q:a></q:r>'
    )


@pytest.mark.parametrize(
    ("source", "target", "expected"),
    [
        pytest.param('<r xmlns:p="urn:p"/>', "r", b'<r xmlns:p="urn:p" q:x="1"></r>', id="never-declared"),
        pytest.param(
            '<r><a xmlns:q="urn:q"/><b/></r>',
            "b",
            b'<r><a xmlns:q="urn:q"></a><b q:x="1"></b></r>',
            id="declared-by-closed-sibling",
        ),
    ],
)
def test_xml_c14n_assigned_attribute_with_unbound_prefix(source: str, target: str, expected: bytes) -> None:
    document: Final[Document] = parse_xml(source)
    element = document.find(target)
    assert element is not None
    element.attrs["q:x"] = "1"
    assert document.canonicalize() == expected


def test_xml_c14n_assigned_attribute_with_unbound_prefix_and_no_declaration() -> None:
    root = cast("Element", parse_xml('<r xmlns:p="urn:p"/>').children[0])
    del root.attrs["xmlns:p"]
    root.attrs["q:x"] = "1"
    assert root.canonicalize() == b'<r q:x="1"></r>'


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        pytest.param("q:a", b'<r><q:a></q:a><b><q:c xmlns:q="urn:q"></q:c></b></r>', id="unbound-prefix"),
        pytest.param(":a", b'<r><b><q:c xmlns:q="urn:q"></q:c></b></r>', id="empty-prefix"),
    ],
)
def test_xml_c14n_exclusive_inserted_element_name(tag: str, expected: bytes) -> None:
    root = cast("Element", parse_xml('<r><b xmlns:q="urn:q"><q:c/></b></r>').children[0])
    root.insert(0, Element(tag))
    assert root.canonicalize(Canonical(exclusive=True)) == expected


def _declarations_in_scope(size: int) -> str:
    declarations = " ".join(f'xmlns:n{index}="http://example.com/{index}"' for index in range(size))
    items = "".join(f'<item n{index}:k="v"/>' for index in range(size))
    return f"<doc><list {declarations}>{items}</list></doc>"


def _prefixed_depth(size: int) -> str:
    return f'<s:node xmlns:s="http://example.com/s">{"<s:node>" * size}{"</s:node>" * size}</s:node>'


@pytest.mark.parametrize("exclusive", [pytest.param(False, id="inclusive"), pytest.param(True, id="exclusive")])
@pytest.mark.parametrize(
    ("build", "sizes"),
    [
        pytest.param(_declarations_in_scope, (200, 400), id="declarations-in-scope"),
        pytest.param(_prefixed_depth, (2_000, 4_000), id="depth"),
    ],
)
def test_xml_c14n_namespace_scope_steps_grow_linearly(
    build: Callable[[int], str], sizes: tuple[int, int], work_steps: WorkSteps, *, exclusive: bool
) -> None:
    options: Final[Canonical] = Canonical(exclusive=exclusive)
    work_steps.assert_linear(lambda size: partial(parse_xml(build(size)).canonicalize, options), sizes)


def test_xml_c14n_apex_uses_nearest_xml_attribute() -> None:
    document = parse_xml('<r xmlns:p="urn:p" xml:lang="en"><a xml:lang="fr"><p:b/></a></r>')
    assert document.children[0].children[0].children[0].canonicalize() == (b'<p:b xmlns:p="urn:p" xml:lang="fr"></p:b>')


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            '<r xmlns:p="urn:p" xml:lang="en"><p:a/></r>',
            b'<p:a xmlns:p="urn:p" zz:a="1" x1:a="2" xml:lang="en"></p:a>',
            id="apex-inherits",
        ),
        pytest.param('<r xmlns:p="urn:p"><p:a/></r>', b'<p:a xmlns:p="urn:p" zz:a="1" x1:a="2"></p:a>', id="apex-own"),
    ],
)
def test_xml_c14n_apex_keeps_document_order_of_tied_attributes(source: str, expected: bytes) -> None:
    apex = cast("Element", parse_xml(source).children[0].children[0])
    apex.attrs["zz:a"] = "1"
    apex.attrs["x1:a"] = "2"
    assert apex.canonicalize() == expected


def test_xml_c14n_descendant_keeps_document_order_of_tied_attributes() -> None:
    root = cast("Element", parse_xml('<r xmlns:p="urn:p"><p:a/></r>').children[0])
    child = cast("Element", root.children[0])
    child.attrs["zz:a"] = "1"
    child.attrs["x1:a"] = "2"
    assert root.canonicalize() == b'<r xmlns:p="urn:p"><p:a zz:a="1" x1:a="2"></p:a></r>'


def test_xml_c14n_apex_rebases_own_xml_base_past_other_attributes() -> None:
    document = parse_xml('<r xml:base="http://h.example/d/"><p:a xmlns:p="urn:p" tabindex="1" xml:base="x/"/></r>')
    apex = document.children[0].children[0]
    assert apex.canonicalize(Canonical(version="1.1")) == (
        b'<p:a xmlns:p="urn:p" tabindex="1" xml:base="http://h.example/d/x/"></p:a>'
    )


def test_xml_c14n_apex_takes_the_nearest_of_many_inherited_xml_attributes() -> None:
    count: Final[int] = 2048
    far: Final[str] = " ".join(f'xml:n{index}="far"' for index in range(count))
    near: Final[str] = " ".join(f'xml:n{index}="near"' for index in range(0, count, 2))
    own: Final[str] = " ".join(f'xml:n{index}="own"' for index in range(0, count, 4))
    source: Final[str] = f'<r xmlns:p="urn:p" {far} n="r"><a {near}><p:b {own} n="b"/></a></r>'
    values: Final[dict[str, str]] = {f"n{index}": ("own", "far", "near", "far")[index % 4] for index in range(count)}
    expected: Final[str] = " ".join(f'xml:{name}="{values[name]}"' for name in sorted(values))
    assert parse_xml(source).children[0].children[0].children[0].canonicalize() == (
        f'<p:b xmlns:p="urn:p" n="b" {expected}></p:b>'.encode()
    )


def test_xml_c14n_apex_inherits_many_xml_attributes_in_linear_time(work_steps: WorkSteps) -> None:
    def apex(count: int) -> Callable[[], bytes]:
        inherited: Final[str] = " ".join(f'xml:a{index}=""' for index in range(count))
        return parse_xml(f'<r xmlns:p="urn:p" {inherited}><p:a/></r>').children[0].children[0].canonicalize

    work_steps.assert_linear(apex, (500, 2000))


def test_xml_c14n_1_1_inherits_simple_xml_attributes() -> None:
    document = parse_xml('<r xmlns:p="urn:p" a="1" xml:lang="en" xml:mode="x"><p:a xml:space="preserve"/></r>')
    assert document.children[0].children[0].canonicalize(Canonical(version="1.1")) == (
        b'<p:a xmlns:p="urn:p" xml:lang="en" xml:space="preserve"></p:a>'
    )


def test_xml_c14n_1_1_does_not_inherit_other_xml_attribute() -> None:
    document = parse_xml('<r xmlns:p="urn:p" xml:ab="x"><p:a/></r>')
    assert document.children[0].children[0].canonicalize(Canonical(version="1.1")) == (b'<p:a xmlns:p="urn:p"></p:a>')


def test_xml_c14n_1_1_keeps_only_named_inherited_attributes() -> None:
    document = parse_xml(
        '<r xmlns:p="urn:p" xml:lang="en" xml:base="urn:root" xml:space="preserve" xml:shade="x">'
        '<p:a xml:lang="fr"/></r>'
    )
    assert document.children[0].children[0].canonicalize(Canonical(version="1.1")) == (
        b'<p:a xmlns:p="urn:p" xml:base="urn:root" xml:lang="fr" xml:space="preserve"></p:a>'
    )


@pytest.mark.parametrize(
    ("source", "selector", "expected"),
    [
        pytest.param(
            '<r xml:base="something/else"><a xml:base="bar/"><b xml:base="foo"/></a></r>',
            "b",
            b'<b xml:base="something/bar/foo"></b>',
            id="relative-chain",
        ),
        pytest.param(
            '<r xml:base="foo/bar"><a xml:base=".."><b xml:base=".."><d xml:base="x"/></b></a></r>',
            "d",
            b'<d xml:base="../x"></d>',
            id="leading-parent-segment",
        ),
        pytest.param(
            '<r xml:base="https://example.org/a/b"><a xml:base="../c/"><b xml:base="d"/></a></r>',
            "b",
            b'<b xml:base="https://example.org/c/d"></b>',
            id="absolute-chain",
        ),
        pytest.param(
            '<r xml:base="https://example.org/a/"><a><b/></a></r>',
            "b",
            b'<b xml:base="https://example.org/a/"></b>',
            id="absent-apex-base",
        ),
        pytest.param(
            '<r xml:base="foo/bar"><b/></r>',
            "b",
            b'<b xml:base="foo/bar"></b>',
            id="absent-apex-base-fast-path",
        ),
        pytest.param(
            '<r xmlns:p="urn:p" xml:base="foo/bar"><b/></r>',
            "b",
            b'<b xmlns:p="urn:p" xml:base="foo/bar"></b>',
            id="absent-apex-base-with-namespace",
        ),
        pytest.param(
            '<r xml:base="a/"><b own="x" xml:base="b"/></r>',
            "b",
            b'<b own="x" xml:base="a/b"></b>',
            id="base-after-ordinary-attribute",
        ),
        pytest.param(
            '<r xmlns:p="urn:p" xml:base="a/"><b own="x" xml:base="b"/></r>',
            "b",
            b'<b xmlns:p="urn:p" own="x" xml:base="a/b"></b>',
            id="base-after-ordinary-attribute-with-namespace",
        ),
        pytest.param(
            '<r xml:base="abc/"><a xml:base="../"/></r>',
            "a",
            b"<a></a>",
            id="empty-result",
        ),
        pytest.param(
            '<r xmlns:p="urn:p" xml:base="abc/"><a xml:base="../"/></r>',
            "a",
            b'<a xmlns:p="urn:p"></a>',
            id="empty-result-with-namespace",
        ),
        pytest.param(
            '<r xml:base="foo/bar"><a xml:base=""><b xml:base="./"/></a></r>',
            "b",
            b'<b xml:base="foo/bar"></b>',
            id="empty-middle-base",
        ),
        pytest.param(
            '<r xml:base="../"><a xml:base="../"><b xml:base="x#fragment"/></a></r>',
            "b",
            b'<b xml:base="../../x"></b>',
            id="fragment-and-leading-parents",
        ),
        pytest.param(
            '<r xml:base="a/b?old"><b xml:base="?new"/></r>',
            "b",
            b'<b xml:base="a/b?new"></b>',
            id="query-only-reference",
        ),
        pytest.param(
            '<r xml:base="a//b/"><b xml:base="../x"/></r>',
            "b",
            b'<b xml:base="a/x"></b>',
            id="consecutive-slashes",
        ),
        pytest.param(
            '<r xml:base="mé/"><b xml:base="café"/></r>',
            "b",
            '<b xml:base="mé/café"></b>'.encode(),
            id="unicode-path",
        ),
        pytest.param(
            '<r xml:base="foo/bar"><b xml:base="https://example.org/a/../b#fragment"/></r>',
            "b",
            b'<b xml:base="https://example.org/b"></b>',
            id="absolute-reference",
        ),
        pytest.param(
            '<r xml:base="https://example.org/a/"><b xml:base="//other.org/x/../y"/></r>',
            "b",
            b'<b xml:base="https://other.org/y"></b>',
            id="authority-reference",
        ),
        pytest.param(
            '<r xml:base=".."><b xml:base=".."/></r>',
            "b",
            b'<b xml:base="../../"></b>',
            id="terminal-parent-segments",
        ),
        pytest.param(
            '<r xml:base=".."><b xml:base="?q"/></r>',
            "b",
            b'<b xml:base="../?q"></b>',
            id="terminal-parent-with-query",
        ),
        pytest.param(
            '<r xml:base="..?old"><b xml:base="x"/></r>',
            "b",
            b'<b xml:base="../x"></b>',
            id="terminal-parent-before-query",
        ),
        pytest.param(
            '<r xml:base="a/.."><b xml:base="x"/></r>',
            "b",
            b'<b xml:base="x"></b>',
            id="terminal-parent-after-slash",
        ),
        pytest.param(
            '<r xml:base="urn:.."><b xml:base="x"/></r>',
            "b",
            b'<b xml:base="urn:../x"></b>',
            id="terminal-parent-after-scheme",
        ),
        pytest.param(
            '<r xml:base="a.."><b xml:base="x"/></r>',
            "b",
            b'<b xml:base="x"></b>',
            id="literal-dot-suffix",
        ),
        pytest.param(
            '<r xml:base="foo/bar#old"><b xml:base="x"/></r>',
            "b",
            b'<b xml:base="foo/x"></b>',
            id="base-fragment",
        ),
        pytest.param(
            '<r xml:base="foo/bar#old"><b xml:base=""/></r>',
            "b",
            b'<b xml:base="foo/bar"></b>',
            id="empty-reference-removes-base-fragment",
        ),
        pytest.param(
            '<r xml:base="urn:thing#old"><b xml:base=""/></r>',
            "b",
            b'<b xml:base="urn:thing"></b>',
            id="empty-opaque-reference-removes-base-fragment",
        ),
        pytest.param(
            '<r xml:base="https://example.org"><b xml:base="x"/></r>',
            "b",
            b'<b xml:base="https://example.org/x"></b>',
            id="empty-authority-path",
        ),
        pytest.param(
            '<r xml:base="https://example.org/a/"><b xml:base="../../../x"/></r>',
            "b",
            b'<b xml:base="https://example.org/x"></b>',
            id="parent-above-root",
        ),
        pytest.param(
            '<r xml:base="foo/bar"><b xml:base="https://other.org"/></r>',
            "b",
            b'<b xml:base="https://other.org"></b>',
            id="absolute-empty-path",
        ),
    ],
)
def test_xml_c14n_1_1_fixes_ancestor_base(source: str, selector: str, expected: bytes) -> None:
    node = parse_xml(source).select_one(selector)
    assert node is not None
    assert node.canonicalize(Canonical(version="1.1")) == expected


@pytest.mark.parametrize(
    ("base", "reference", "expected"),
    [
        pytest.param("../ab/", "b", "../ab/b", id="leading-parent-prefix"),
        pytest.param("ab/", "b", "ab/b", id="ordinary-relative-base"),
        pytest.param(".a/", "b", ".a/b", id="dot-starting-segment"),
        pytest.param("a:b/", "c", "a:b/c", id="base-scheme"),
        pytest.param("a/?q/", "b", "a/b", id="base-query"),
        pytest.param("a/#f/", "b", "a/b", id="base-fragment"),
        pytest.param("/a/", "b", "/a/b", id="rooted-base"),
        pytest.param("./a/", "b", "a/b", id="dot-base-segment"),
        pytest.param("a/../", "b", "b", id="parent-after-segment"),
        pytest.param("a/", "é", "a/é", id="unicode-reference"),
        pytest.param("a/", "", "a/", id="empty-reference"),
        pytest.param("a/", ".", "a/", id="dot-reference"),
        pytest.param("a/", ".b", "a/.b", id="dot-starting-reference"),
        pytest.param("a/", ".ab", "a/.ab", id="long-dot-starting-reference"),
        pytest.param("a/", "ab", "a/ab", id="two-character-reference"),
        pytest.param("a/", "b/c", "a/b/c", id="reference-slash"),
        pytest.param("a/", "b:c", "b:c", id="reference-scheme"),
        pytest.param("a/", "b?q", "a/b?q", id="reference-query"),
        pytest.param("a/", "b#f", "a/b", id="reference-fragment"),
    ],
)
def test_xml_c14n_1_1_resolves_relative_base_segments(base: str, reference: str, expected: str) -> None:
    node = parse_xml(f'<r xml:base="{base}"><b xml:base="{reference}"/></r>').select_one("b")
    assert node is not None
    assert node.canonicalize(Canonical(version="1.1")) == f'<b xml:base="{expected}"></b>'.encode()


def test_xml_c14n_1_1_drops_a_base_that_resolves_to_nothing() -> None:
    node = parse_xml('<r xml:base="a/"><b xml:base=".."/></r>').select_one("b")
    assert node is not None
    assert node.canonicalize(Canonical(version="1.1")) == b"<b></b>"


def test_xml_c14n_1_0_does_not_fix_ancestor_base() -> None:
    node = parse_xml('<r xml:base="foo/bar"><a xml:base="../x"/></r>').select_one("a")
    assert node is not None
    assert node.canonicalize(Canonical(version="1.0")) == b'<a xml:base="../x"></a>'


def test_html_c14n_1_1_fixes_ancestor_base() -> None:
    node = _one('<div xml:base="something/else"><section xml:base="bar/"><p xml:base="foo">x</p></section></div>', "p")
    assert node.canonicalize(Canonical(version="1.1")) == b'<p xml:base="something/bar/foo">x</p>'


@pytest.mark.parametrize(
    ("bindings", "attributes"),
    [
        pytest.param("", "", id="fast-path"),
        pytest.param('xmlns:p="urn:p"', "", id="namespace-stack"),
        pytest.param(
            'xmlns:p="urn:p"',
            " ".join(f'a{index}="x"' for index in range(33)),
            id="namespace-heap-attributes",
        ),
        pytest.param(
            " ".join(f'xmlns:p{index}="urn:{index}"' for index in range(33)),
            "",
            id="namespace-heap-bindings",
        ),
        pytest.param(
            " ".join(f'xmlns:p{index}="urn:{index}"' for index in range(33)),
            " ".join(f'a{index}="x"' for index in range(33)),
            id="namespace-both-heap",
        ),
    ],
)
def test_xml_c14n_1_1_rejects_invalid_ancestor_base(bindings: str, attributes: str) -> None:
    node = parse_xml(f'<r xml:base="http://[bad" {bindings}><b xml:base="x" {attributes}/></r>').select_one("b")
    assert node is not None
    with pytest.raises(ValueError, match="Invalid IPv6 URL"):
        node.canonicalize(Canonical(version="1.1"))


def test_html_c14n_1_1_rejects_invalid_ancestor_base_with_many_attributes() -> None:
    attributes: Final[str] = " ".join(f'a{index}="x"' for index in range(33))
    node = _one(f'<div xml:base="http://[bad"><p xml:base="x" {attributes}></p></div>', "p")
    with pytest.raises(ValueError, match="Invalid IPv6 URL"):
        node.canonicalize(Canonical(version="1.1"))


# The fixup joins each ancestor's xml:base onto the value built from the ancestors above it. These chains carry that
# value past the point where it moves into the chain representation, and put parent segments, dot segments and schemes
# through the joins.
@pytest.mark.parametrize(
    ("bases", "apex", "expected"),
    [
        pytest.param(["kin/"] * 40, "", f"{'kin/' * 40}", id="exact-length-crossing"),
        pytest.param(["pq/"] * 60, f"{'../' * 30}w", f"{'pq/' * 30}w", id="parents-kept"),
        pytest.param(["/rt/", "lz"], f"{'../' * 25}v", "/v", id="parents-dropped-under-root"),
        pytest.param(["http://h.example/p", "", ""], f"?{'z' * 150}", f"http://h.example/p?{'z' * 150}", id="query"),
        pytest.param(
            ["jk/"] * 6,
            f"https://g.example/{'m' * 150}",
            f"https://g.example/{'m' * 150}",
            id="absolute-apex",
        ),
        pytest.param(
            ["s:", "t/", "t/", "t/"],
            f"//h.example/{'n' * 150}",
            f"s://h.example/{'n' * 150}",
            id="authority-takes-scheme",
        ),
        pytest.param(
            ["qd/", "yd/", "xd/", "wd"],
            f"../a:b/{'r' * 150}",
            f"a:b/{'r' * 150}",
            id="segment-becomes-scheme",
        ),
        pytest.param(["jk/"] * 3, f"/{'h' * 130}//d", f"/{'h' * 130}//d", id="rooted-keeps-empty-segment"),
        pytest.param(["", "", f"p//{'h' * 130}"], "", f"p/{'h' * 130}", id="relative-drops-empty-segment"),
        pytest.param(["", "", f"{'h' * 130}/."], "", f"{'h' * 130}/", id="trailing-dot"),
        pytest.param(["", "", f"{'h' * 130}/x/.."], "", f"{'h' * 130}/", id="trailing-parent"),
        pytest.param(["", "", f"{'h' * 130}/./"], "", f"{'h' * 130}/", id="trailing-dot-directory"),
        pytest.param(["", "", f"{'h' * 130}/a../../"], "", f"{'h' * 130}/", id="parent-after-dotted-name"),
        pytest.param(["gh/", "gh/"], f"ij/.x/{'e' * 130}", f"gh/gh/ij/.x/{'e' * 130}", id="two-character-segments"),
        pytest.param(["u", f"{'w/' * 40}", ""], f"{'../' * 40}y", "y", id="emptied-then-replaced"),
        pytest.param(["", "", "g/"], f"{'e' * 130}?q", f"g/{'e' * 130}?q", id="query-after-path"),
        pytest.param(["pq/"] * 5, f"{'../' * 60}", f"{'../' * 55}", id="parents-remain-after-joins"),
        pytest.param(
            ["/rt/", "pq/", "pq/", "pq/", "pq/"], f"{'../' * 60}", "/", id="parents-dropped-by-rooted-ancestor"
        ),
    ],
)
def test_xml_c14n_1_1_joins_ancestor_bases(bases: list[str], apex: str, expected: str) -> None:
    assert _fixed_base(bases, apex) == f'<s xml:base="{expected}"></s>'.encode()


def _fixed_base(bases: list[str], apex: str) -> bytes:
    opening = "".join(f'<a xml:base="{base}">' for base in bases)
    node = parse_xml(f'{opening}<s xml:base="{apex}"/>{"</a>" * len(bases)}').select_one("s")
    assert node is not None
    return node.canonicalize(Canonical(version="1.1"))


def test_xml_c14n_1_1_drops_base_that_deep_ancestors_empty() -> None:
    assert _fixed_base([f"{'w/' * 30}", ""], f"{'../' * 30}") == b"<s></s>"


@pytest.mark.parametrize(
    ("inner", "apex"),
    [
        pytest.param(["gh/"], f"http://x.example/{'k' * 150}", id="absolute"),
        pytest.param(["gh/", "gh/"], f"http://x.example/{'k' * 150}", id="absolute-settled"),
        pytest.param(["gh/", "gh/"], "k" * 150, id="relative"),
    ],
)
def test_xml_c14n_1_1_rejects_invalid_outer_base_above_long_value(inner: list[str], apex: str) -> None:
    with pytest.raises(ValueError, match="Invalid IPv6 URL"):
        _fixed_base(["http://[::1/", *inner], apex)


def test_html_c14n_1_1_joins_ancestor_bases_at_depth() -> None:
    wrapper: Final[str] = '<aside xml:base="ub/">'
    node = _one(f"{wrapper * 120}<em></em>", "em")
    assert node.canonicalize(Canonical(version="1.1")) == f'<em xml:base="{"ub/" * 120}"></em>'.encode()


@pytest.mark.parametrize(
    ("parse_markup", "value", "apex", "depths"),
    [
        pytest.param(parse_xml, "seg/" * 5, "", (1500, 6000), id="xml"),
        # the HTML tree builder caps the depth at 512, so this case stays below it
        pytest.param(parse, "seg/" * 250, "", (100, 400), id="html"),
        pytest.param(parse_xml, "seg/" * 5, "http://h.example/", (1000, 4000), id="absolute"),
    ],
)
def test_c14n_1_1_fixup_steps_grow_linearly(
    parse_markup: Callable[[str], Document], value: str, apex: str, depths: tuple[int, int], work_steps: WorkSteps
) -> None:
    work_steps.assert_linear(lambda depth: _base_chain_apex(parse_markup, value, apex, depth), depths)


def _base_chain_apex(parse_markup: Callable[[str], Document], value: str, apex: str, depth: int) -> Callable[[], bytes]:
    opening = f'<section xml:base="{value}">'
    node = parse_markup(f'{opening * depth}<s xml:base="{apex}"></s>{"</section>" * depth}').select_one("s")
    assert node is not None
    return partial(node.canonicalize, Canonical(version="1.1"))


def test_xml_c14n_comment_is_omitted_without_option() -> None:
    source: Final[str] = '<!--pre--><r xmlns:p="urn:p"><!--inner--><p:a/></r><!--post-->'
    assert parse_xml(source).canonicalize() == b'<r xmlns:p="urn:p"><p:a></p:a></r>'


def test_xml_c14n_doctype_node_is_empty() -> None:
    document = parse_xml('<!DOCTYPE r><r xmlns:p="urn:p"/>')
    assert document.children[0].canonicalize() == b""


def test_inherited_attributes_skip_non_xml_ancestor_attributes() -> None:
    node = _one("<div id=x xml:lang=en><p>y</p></div>", "p")
    assert node.canonicalize() == b'<p xml:lang="en">y</p>'


def test_exclusive_omits_ancestor_xml_attributes() -> None:
    node = _one(
        '<div xml:lang="en" xml:base="urn:root" xml:mode="x">'
        '<section xml:space="preserve"><p xml:lang="fr">x</p></section></div>',
        "p",
    )
    assert node.canonicalize(Canonical(exclusive=True)) == b'<p xml:lang="fr">x</p>'


def test_doctype_node_canonicalizes_to_nothing() -> None:
    doctype = parse("<!doctype html>").children[0]
    assert doctype.canonicalize() == b""


def test_template_content_is_transparent() -> None:
    assert _one("<template><p>x</p></template>", "template").canonicalize() == b"<template><p>x</p></template>"


def test_versions_coincide_for_a_complete_subtree() -> None:
    node = _one("<p z=1 a=2 xml:lang=en>x", "p")
    assert node.canonicalize(Canonical(version="1.1")) == node.canonicalize(Canonical(version="1.0"))


def test_1_0_inherits_ancestor_xml_attributes_onto_apex() -> None:
    node = _one("<div xml:lang=en xml:space=preserve xml:id=root><section><p/></section></div>", "section")
    assert node.canonicalize(Canonical(version="1.0")) == (
        b'<section xml:id="root" xml:lang="en" xml:space="preserve"><p></p></section>'
    )


def test_1_1_excludes_inherited_xml_id() -> None:
    node = _one("<div xml:lang=en xml:id=root><section><p/></section></div>", "section")
    assert node.canonicalize(Canonical(version="1.1")) == b'<section xml:lang="en"><p></p></section>'


def test_apex_own_xml_id_survives_under_1_1() -> None:
    node = _one("<div xml:lang=en><p xml:id=self>hi</p></div>", "p")
    assert node.canonicalize(Canonical(version="1.1")) == b'<p xml:id="self" xml:lang="en">hi</p>'


def test_nearest_ancestor_xml_value_wins() -> None:
    node = _one("<div xml:lang=en><span xml:lang=fr><p>x</p></span></div>", "p")
    assert node.canonicalize() == b'<p xml:lang="fr">x</p>'


def test_distinct_same_length_xml_attributes_both_inherit() -> None:
    node = _one("<div xml:lang=en><span xml:base=u><p>x</p></span></div>", "p")
    assert node.canonicalize() == b'<p xml:base="u" xml:lang="en">x</p>'


def test_1_1_does_not_inherit_other_xml_attribute() -> None:
    node = _one("<div xml:xx=v><p>x</p></div>", "p")
    assert node.canonicalize(Canonical(version="1.1")) == b"<p>x</p>"


def test_1_1_keeps_only_named_inherited_attributes() -> None:
    node = _one(
        '<div xml:lang="en" xml:base="urn:root" xml:space="preserve" xml:mode="x" xml:shade="x"><p>x</p></div>', "p"
    )
    assert node.canonicalize(Canonical(version="1.1")) == (
        b'<p xml:base="urn:root" xml:lang="en" xml:space="preserve">x</p>'
    )


def test_apex_does_not_reinherit_its_own_xml_attribute() -> None:
    node = _one("<div xml:lang=en><p xml:lang=fr>x</p></div>", "p")
    assert node.canonicalize().count(b"xml:lang") == 1


def test_apex_rebases_own_xml_base_past_other_attributes() -> None:
    node = _one('<div xml:base="http://h.example/d/"><p tabindex="1" xml:base="x/">x</p></div>', "p")
    assert node.canonicalize(Canonical(version="1.1")) == (b'<p tabindex="1" xml:base="http://h.example/d/x/">x</p>')


def test_apex_takes_the_nearest_of_many_inherited_xml_attributes() -> None:
    count: Final[int] = 2048
    far: Final[str] = " ".join(f"xml:n{index}=far" for index in range(count))
    near: Final[str] = " ".join(f"xml:n{index}=near" for index in range(0, count, 2))
    own: Final[str] = " ".join(f"xml:n{index}=own" for index in range(0, count, 4))
    node = _one(f"<div {far} n=div><section {near}><p {own} n=p>x</p></section></div>", "p")
    values: Final[dict[str, str]] = {f"n{index}": ("own", "far", "near", "far")[index % 4] for index in range(count)}
    expected: Final[str] = " ".join(f'xml:{name}="{values[name]}"' for name in sorted(values))
    assert node.canonicalize() == f'<p n="p" {expected}>x</p>'.encode()


def test_apex_inherits_many_xml_attributes_in_linear_time(work_steps: WorkSteps) -> None:
    def apex(count: int) -> Callable[[], bytes]:
        inherited: Final[str] = " ".join(f"xml:a{index}" for index in range(count))
        return _one(f"<div {inherited}><p>x</p></div>", "p").canonicalize

    work_steps.assert_linear(apex, (500, 2000))


def test_explicit_none_options_uses_defaults() -> None:
    node = _one("<p>x</p>", "p")
    assert node.canonicalize(None) == node.canonicalize()


def test_too_many_arguments_is_rejected() -> None:
    with pytest.raises(TypeError):
        _one("<p>x</p>", "p").canonicalize(Canonical(), Canonical())  # ty: ignore[too-many-positional-arguments]  # extra arg tests the arity check


def test_options_must_be_a_canonical_instance() -> None:
    with pytest.raises(TypeError, match="options must be a Canonical"):
        _one("<p>x</p>", "p").canonicalize(object())  # ty: ignore[invalid-argument-type]  # pass a non-Canonical to test the type error


def test_exclusive_1_1_is_rejected() -> None:
    with pytest.raises(ValueError, match=r"exclusive canonicalization is defined only over version 1\.0"):
        Canonical(exclusive=True, version="1.1")


def test_inclusive_ns_prefixes_requires_exclusive() -> None:
    with pytest.raises(ValueError, match="inclusive_ns_prefixes applies only in exclusive mode"):
        Canonical(inclusive_ns_prefixes=("xlink",))


def test_deep_subtree_does_not_overflow() -> None:
    out = _one(f"<div>{'<span>' * 200}x{'</span>' * 200}</div>", "div").canonicalize()
    assert out.startswith(b"<div>" + b"<span>" * 200)
    assert out.endswith(b"</span>" * 200 + b"</div>")


def test_many_attributes_beyond_stack_buffer_sort() -> None:
    names = [f"a{index:02d}" for index in range(40)]
    out = Element("p", dict.fromkeys(reversed(names), "1")).canonicalize().decode()
    positions = [out.index(f'{name}="1"') for name in names]
    assert positions == sorted(positions)


def test_w3c_spec_example_pis_and_comments() -> None:
    # W3C xml-c14n 3.7 first example, comments dropped, PIs kept, empty element paired
    node = Element(
        "doc",
        children=[
            ProcessingInstruction("pi-without-data", ""),
            Element("e1"),
            Element("e2"),
        ],
    )
    assert node.canonicalize() == b"<doc><?pi-without-data?><e1></e1><e2></e2></doc>"


@pytest.mark.parametrize(
    "size",
    [
        pytest.param(1, id="single"),
        pytest.param(31, id="31-attrs"),
        pytest.param(32, id="32-attrs"),
        pytest.param(33, id="33-attrs"),
        pytest.param(1_000, id="wide"),
    ],
)
def test_canonicalize_orders_many_attributes(size: int) -> None:
    element: Final[Element] = parse(
        f"""<div {" ".join(f'data-{index:05d}="{index}"' for index in range(size, 0, -1))}></div>"""
    ).select("div")[0]
    assert (
        element.canonicalize()
        == (f"""<div {" ".join(f'data-{index:05d}="{index}"' for index in range(1, size + 1))}></div>""").encode()
    )


_XLINK: Final[str] = ' xmlns:xlink="http://www.w3.org/1999/xlink"'
_SVG: Final[str] = ' xmlns="http://www.w3.org/2000/svg"'


@pytest.mark.parametrize("exclusive", [pytest.param(False, id="inclusive"), pytest.param(True, id="exclusive")])
@pytest.mark.parametrize(
    ("children", "expected"),
    [
        pytest.param(
            '<use xlink:href="a"/><use xlink:href="b"/>',
            f'<use{_XLINK} xlink:href="a"></use><use{_XLINK} xlink:href="b"></use>',
            id="empty-siblings",
        ),
        pytest.param(
            '<g xlink:href="a"><use xlink:href="b"/></g><use xlink:href="c"/>',
            f'<g{_XLINK} xlink:href="a"><use xlink:href="b"></use></g><use{_XLINK} xlink:href="c"></use>',
            id="nested-binding-closes",
        ),
        pytest.param(
            '<g xlink:href="a"><g><use xlink:href="b"/></g><use xlink:href="c"/></g>',
            f'<g{_XLINK} xlink:href="a"><g><use xlink:href="b"></use></g><use xlink:href="c"></use></g>',
            id="outer-binding-survives-inner-close",
        ),
        pytest.param(
            '<g xlink:href="a">text<!--comment--><use/></g><g><use xlink:href="b"/></g>',
            f'<g{_XLINK} xlink:href="a">text<use></use></g><g><use{_XLINK} xlink:href="b"></use></g>',
            id="text-and-comment",
        ),
    ],
)
def test_canonicalize_sibling_scopes(children: str, expected: str, *, exclusive: bool) -> None:
    node = parse(f"<svg>{children}</svg>").select_one("svg")
    assert node is not None
    assert node.canonicalize(Canonical(exclusive=exclusive)) == f"<svg{_SVG}>{expected}</svg>".encode()


@pytest.mark.parametrize(
    ("options", "apex_declaration", "child_declaration", "inherited"),
    [
        pytest.param(Canonical(), _XLINK, "", ' xml:lang="en"', id="inclusive"),
        pytest.param(Canonical(exclusive=True), "", _XLINK, "", id="exclusive"),
        pytest.param(
            Canonical(exclusive=True, inclusive_ns_prefixes=("xlink",)), _XLINK, _XLINK, "", id="forced-prefix"
        ),
    ],
)
def test_canonicalize_inherited_scope(
    options: Canonical, apex_declaration: str, child_declaration: str, inherited: str
) -> None:
    node = parse('<svg xlink:href="outer" xml:lang="en"><g><use xlink:href="inner"/></g></svg>').select_one("g")
    assert node is not None
    assert node.canonicalize(options) == (
        f'<g{_SVG}{apex_declaration}{inherited}><use{child_declaration} xlink:href="inner"></use></g>'.encode()
    )


def test_canonicalize_scope_after_attribute_mutation() -> None:
    node = parse('<svg xlink:href="outer"><g><use xlink:href="inner"/></g></svg>').select_one("svg")
    assert node is not None
    node.canonicalize()
    del node.attrs["xlink:href"]
    assert node.canonicalize() == f'<svg{_SVG}><g><use{_XLINK} xlink:href="inner"></use></g></svg>'.encode()


def test_canonicalize_shared_sparse_xlink_input() -> None:
    source = cast("tuple[str, int]", INPUTS["canonicalize-deep"]()[1][1])[0]
    assert isinstance(source, str)
    assert (
        parse(source).canonicalize()
        == (
            "<html><head></head><body>"
            f"""<svg{_SVG}>{f'<g><use{_XLINK} xlink:href="#x"></use>' * 150}{"</g>" * 150}</svg></body></html>"""
        ).encode()
    )


@pytest.mark.parametrize(
    "case", [pytest.param(0, id="deep"), pytest.param(1, id="sparse-xlink"), pytest.param(2, id="shallow")]
)
@pytest.mark.oracle
def test_canonicalize_benchmark_html_parser_difference(case: int) -> None:
    etree: Final[ModuleType] = pytest.importorskip("lxml.etree")
    source: Final[str] = cast("tuple[str, int]", INPUTS["canonicalize-deep"]()[case][1])[0]
    expected: Final[bytes] = parse(source).canonicalize()
    assert etree.tostring(etree.HTML(source), method="c14n") == (
        expected
        .replace(b"<head></head>", b"")
        .replace(b' xmlns="http://www.w3.org/2000/svg"', b"")
        .replace(b' xmlns:xlink="http://www.w3.org/1999/xlink"', b"")
    )


@pytest.mark.parametrize(
    ("html", "exclusive", "with_comments"),
    [
        pytest.param("<p z=1 a=2>x&amp;y</p>", False, False, id="attr-order"),
        pytest.param("<div><br><p class='a&b<c'>x&amp;<b>y</b></p></div>", False, False, id="mixed"),
        pytest.param("<p title='a\tb\nc\rd'>t&lt;u&gt;v</p>", False, False, id="char-refs"),
        pytest.param("<a> keep  the   spaces <b>y</b> here </a>", False, False, id="whitespace"),
        pytest.param("<svg xlink:href=x><a xlink:title=t><rect/></a></svg>", False, False, id="foreign-xlink"),
        pytest.param("<math><mi mathvariant=bold>x</mi></math>", False, False, id="mathml"),
        pytest.param("<a><!--note--><b>y</b><!--tail--></a>", True, False, id="comments-dropped"),
        pytest.param("<a><!--note--><b>y</b></a>", False, True, id="comments-kept"),
        pytest.param("<p>caf\xe9 → \xa9</p>", False, False, id="non-ascii"),
        pytest.param("<svg xlink:href=x><g><rect/></g></svg>", True, False, id="exclusive-doc"),
    ],
)
@pytest.mark.oracle
def test_whole_document_matches_lxml(html: str, *, exclusive: bool, with_comments: bool) -> None:
    etree: Final[ModuleType] = pytest.importorskip("lxml.etree")
    tree: Final[Document] = parse(html)
    ours: Final[bytes] = tree.canonicalize(Canonical(exclusive=exclusive, with_comments=with_comments))
    reparsed: Final[lxml_types._ElementTree[lxml_types._Element]] = etree.parse(
        BytesIO(tree.serialize(Html(xml=True)).encode())
    )
    sink: Final[BytesIO] = BytesIO()
    reparsed.write(sink, method="c14n", exclusive=exclusive, with_comments=with_comments)
    assert ours == sink.getvalue()


@pytest.mark.parametrize(
    ("selector", "xpath", "exclusive", "prefixes"),
    [
        pytest.param("g", ".//s:g", False, None, id="subtree-inclusive"),
        pytest.param("g", ".//s:g", True, None, id="subtree-exclusive-drops-unused"),
        pytest.param("g", ".//s:g", True, ["xlink"], id="subtree-exclusive-promotes-prefix"),
        pytest.param("svg", ".//s:svg", True, None, id="subtree-exclusive-renders-on-user"),
    ],
)
@pytest.mark.oracle
def test_subtree_matches_lxml(selector: str, xpath: str, *, exclusive: bool, prefixes: list[str] | None) -> None:
    etree: Final[ModuleType] = pytest.importorskip("lxml.etree")
    tree: Final[Document] = parse("<svg xlink:href=x><g><rect/></g></svg>")
    node: Final[Element | None] = tree.select_one(selector)
    assert node is not None
    ours: Final[bytes] = node.canonicalize(Canonical(exclusive=exclusive, inclusive_ns_prefixes=tuple(prefixes or ())))
    root: Final[lxml_types._Element] = etree.fromstring(tree.serialize(Html(xml=True)).encode())
    target: Final[lxml_types._Element | None] = root.find(
        xpath, namespaces={"s": "http://www.w3.org/2000/svg", "m": "http://www.w3.org/1998/Math/MathML"}
    )
    theirs: Final[bytes] = etree.tostring(target, method="c14n", exclusive=exclusive, inclusive_ns_prefixes=prefixes)
    assert ours == theirs


_LONG_URI: Final[str] = f"http://example.com/{'u' * 3000}"


def _xml_root(source: str) -> Node:
    return parse_xml(source).children[0]


def _xml_left_out_run() -> Node:
    document: Final[Document] = parse_xml('<r xmlns="urn:a"><s/></r>')
    holder: Final[Element | None] = document.select_one("s")
    assert holder is not None
    holder.append(Element("1a", {"xmlns:p": _LONG_URI}, children=[Element("p:b") for _ in range(64)]))
    return document


@pytest.mark.parametrize(
    ("build", "options"),
    [
        pytest.param(
            lambda: _xml_root(f'<r xmlns:p="{_LONG_URI}">{"<p:a/>" * 64}</r>'),
            Canonical(exclusive=True),
            id="xml-prefix-unused-on-root",
        ),
        pytest.param(
            lambda: _xml_root(f'<q:r xmlns:q="urn:q" xmlns="{_LONG_URI}">{"<a/>" * 64}</q:r>'),
            Canonical(exclusive=True),
            id="xml-default-namespace",
        ),
        pytest.param(
            lambda: _xml_root('<r xmlns:p="{}">{}</r>'.format(_LONG_URI, '<a p:b=""/>' * 64)),
            Canonical(exclusive=True),
            id="xml-attribute-prefix",
        ),
        pytest.param(
            lambda: _xml_root(f'<t xmlns:p="{_LONG_URI}"><r>{"<p:a/>" * 64}</r></t>').children[0],
            Canonical(exclusive=True),
            id="xml-declared-above-apex",
        ),
        pytest.param(
            lambda: _xml_root(f'<r xmlns:p="http://example.com/{"&amp;" * 3000}">{"<p:a/>" * 8}</r>'),
            Canonical(exclusive=True),
            id="xml-charged-as-written",
        ),
        pytest.param(
            lambda: _xml_root(
                "<r {}><a {}/></r>".format(
                    " ".join(f'xmlns:p{index}="{_LONG_URI}{index}"' for index in range(33)),
                    " ".join(f'p{index}:x=""' for index in range(33)),
                )
            ),
            Canonical(exclusive=True),
            id="xml-many-prefixes-on-one-element",
        ),
        pytest.param(_xml_left_out_run, Canonical(), id="xml-left-out-inclusive"),
        pytest.param(
            _xml_left_out_run, Canonical(exclusive=True, inclusive_ns_prefixes=("p",)), id="xml-left-out-forced"
        ),
        pytest.param(
            lambda: _one(f'<div xmlns:p="{_LONG_URI}">{"<p:a></p:a>" * 64}</div>', "div"),
            Canonical(exclusive=True),
            id="html-prefix-unused-on-root",
        ),
        pytest.param(
            lambda: _one(f'<div xmlns:z="{_LONG_URI}">{"<z:a></z:a>" * 64}</div>', "div"),
            Canonical(exclusive=True),
            id="html-prefix-after-xlink",
        ),
        pytest.param(
            lambda: _one(f'<div><x@y xmlns:p="{_LONG_URI}"><p:z></p:z>{"<a></a>" * 64}</x@y></div>', "div"),
            Canonical(),
            id="html-left-out-inclusive",
        ),
        pytest.param(
            lambda: _one(f'<div><x@y xmlns:p="{_LONG_URI}">{"<a></a>" * 64}</x@y></div>', "div"),
            Canonical(exclusive=True, inclusive_ns_prefixes=("p",)),
            id="html-left-out-forced",
        ),
    ],
)
def test_c14n_repeated_namespace_declarations_out_of_proportion_raise(
    build: Callable[[], Node], options: Canonical
) -> None:
    node: Final[Node] = build()
    with pytest.raises(ValueError, match="exceed the allowance of 65536 characters plus 32 per character of other"):
        node.canonicalize(options)


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(parse_xml, id="xml"),
        pytest.param(lambda source: _one(source, "root"), id="html"),
    ],
)
def test_c14n_repeated_namespace_declarations_in_proportion_are_kept(build: Callable[[str], Node]) -> None:
    name: Final[str] = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    node: Final[Node] = build(f'<root xmlns:r="{name}">{"<r:id></r:id>" * 4000}</root>')
    repeated: Final[str] = f'<r:id xmlns:r="{name}"></r:id>' * 4000
    assert node.canonicalize(Canonical(exclusive=True)) == f"<root>{repeated}</root>".encode()


def test_c14n_long_repeated_namespace_declaration_fits_a_short_document() -> None:
    document: Final[Document] = parse_xml(f'<r xmlns:p="{_LONG_URI * 10}"><p:a/><p:b/></r>')
    assert len(document.canonicalize(Canonical(exclusive=True))) > 60000


_ATOM: Final[str] = (
    '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:media="http://search.yahoo.com/mrss/">'
    "<title>t</title><entry><id>urn:e:1</id><title>one</title>"
    '<media:thumbnail url="http://example.com/1.png"/></entry>'
    "<entry><id>urn:e:2</id><title>two</title>"
    '<media:thumbnail url="http://example.com/2.png"/></entry></feed>'
)
_SOAP: Final[str] = (
    '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
    'xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd" '
    'xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">'
    '<s:Header><wsse:Security><wsu:Timestamp wsu:Id="ts"><wsu:Created>2026-11-15T00:00:00Z</wsu:Created>'
    "</wsu:Timestamp></wsse:Security></s:Header>"
    '<s:Body wsu:Id="body"><m:Order xmlns:m="urn:example:orders"><m:Item>1</m:Item><m:Item>2</m:Item></m:Order>'
    "</s:Body></s:Envelope>"
)


@pytest.mark.parametrize(
    ("source", "exclusive", "expected"),
    [
        pytest.param(
            _ATOM,
            True,
            b'<feed xmlns="http://www.w3.org/2005/Atom"><title>t</title><entry><id>urn:e:1</id><title>one</title>'
            b'<media:thumbnail xmlns:media="http://search.yahoo.com/mrss/" url="http://example.com/1.png">'
            b"</media:thumbnail></entry><entry><id>urn:e:2</id><title>two</title>"
            b'<media:thumbnail xmlns:media="http://search.yahoo.com/mrss/" url="http://example.com/2.png">'
            b"</media:thumbnail></entry></feed>",
            id="atom-exclusive",
        ),
        pytest.param(
            _SOAP,
            True,
            b'<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Header>'
            b'<wsse:Security xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0'
            b'.xsd"><wsu:Timestamp xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-util'
            b'ity-1.0.xsd" wsu:Id="ts"><wsu:Created>2026-11-15T00:00:00Z</wsu:Created></wsu:Timestamp></wsse:Security>'
            b'</s:Header><s:Body xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-util'
            b'ity-1.0.xsd" wsu:Id="body"><m:Order xmlns:m="urn:example:orders"><m:Item>1</m:Item><m:Item>2</m:Item>'
            b"</m:Order></s:Body></s:Envelope>",
            id="soap-exclusive",
        ),
        pytest.param(
            _SOAP,
            False,
            b'<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
            b'xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd" '
            b'xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">'
            b'<s:Header><wsse:Security><wsu:Timestamp wsu:Id="ts"><wsu:Created>2026-11-15T00:00:00Z</wsu:Created>'
            b"</wsu:Timestamp></wsse:Security></s:Header>"
            b'<s:Body wsu:Id="body"><m:Order xmlns:m="urn:example:orders"><m:Item>1</m:Item><m:Item>2</m:Item>'
            b"</m:Order></s:Body></s:Envelope>",
            id="soap-inclusive",
        ),
    ],
)
def test_xml_c14n_namespaced_documents_keep_their_canonical_form(
    source: str, expected: bytes, *, exclusive: bool
) -> None:
    assert parse_xml(source).canonicalize(Canonical(exclusive=exclusive)) == expected
