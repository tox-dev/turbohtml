"""Canonical XML (c14n) serialization: ``Node.canonicalize`` and the ``Canonical`` config."""

from __future__ import annotations

import pickle  # ruff:ignore[suspicious-pickle-import]  # round-tripping this test's own bytes
from io import BytesIO
from typing import Final, cast

import pytest
from bench.operations import INPUTS

from turbohtml import Canonical, CData, Element, Html, ProcessingInstruction, Text, parse, parse_xml


def _one(markup: str, selector: str) -> Element:
    node = parse(markup).select_one(selector)
    assert node is not None
    return node


def test_returns_utf8_bytes() -> None:
    out = _one("<p>x</p>", "p").canonicalize()
    assert isinstance(out, bytes)
    assert out == b"<p>x</p>"


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


@pytest.mark.parametrize(
    ("xml", "comment_length"),
    [
        pytest.param(False, 8192, id="html-medium"),
        pytest.param(False, 32768, id="html-large"),
        pytest.param(True, 8192, id="xml-medium"),
        pytest.param(True, 32768, id="xml-large"),
    ],
)
def test_document_drops_large_comments(*, xml: bool, comment_length: int) -> None:
    source = '<r xmlns="urn:x">x</r>' if xml else "<p>x</p>"
    document = parse_xml if xml else parse
    expected = b'<r xmlns="urn:x">x</r>' if xml else b"<html><head></head><body><p>x</p></body></html>"
    assert document(source + "<!--" + "x" * comment_length + "-->").canonicalize() == expected


@pytest.mark.parametrize("xml", [pytest.param(False, id="html"), pytest.param(True, id="xml")])
def test_document_canonicalizes_many_elements(*, xml: bool) -> None:
    if xml:
        source = '<r xmlns="urn:x">' + "<a/>" * 1024 + "</r>"
        expected = b'<r xmlns="urn:x">' + b"<a></a>" * 1024 + b"</r>"
    else:
        source = "<main>" + "<p>x</p>" * 512 + "</main>"
        expected = b"<html><head></head><body><main>" + b"<p>x</p>" * 512 + b"</main></body></html>"
    assert (parse_xml if xml else parse)(source).canonicalize() == expected


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
    node = _one("<svg xlink:href=a><g xlink:href=b><rect/></g></svg>", "svg")
    out = node.canonicalize(Canonical(exclusive=True))
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
    ],
)
def test_xml_c14n_sorts_attributes_by_namespace_uri(source: str, expected: bytes) -> None:
    assert parse_xml(source).canonicalize() == expected


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
    source: Final = '<r><a xmlns:p="urn:p"><b/></a></r>'
    assert parse_xml(source).canonicalize(Canonical(exclusive=True, inclusive_ns_prefixes=("p",))) == (
        b'<r><a xmlns:p="urn:p"><b></b></a></r>'
    )


def test_xml_c14n_forced_empty_default_without_parent_binding() -> None:
    source: Final = '<r><a xmlns=""><b/></a></r>'
    assert parse_xml(source).canonicalize(Canonical(exclusive=True, inclusive_ns_prefixes=("#default",))) == (
        b"<r><a><b></b></a></r>"
    )


def test_xml_c14n_apex_inherits_namespace_axis() -> None:
    document = parse_xml('<r xmlns:p="urn:p"><p:a/></r>')
    assert document.children[0].children[0].canonicalize() == b'<p:a xmlns:p="urn:p"></p:a>'


def test_xml_c14n_pickle_preserves_namespace_axis() -> None:
    document = parse_xml('<r xmlns:p="urn:p"><p:a/></r>')
    restored = pickle.loads(pickle.dumps(document))  # ruff:ignore[suspicious-pickle-usage]  # this test's own bytes
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
    source: Final = '<r xmlns:p="urn:p">a&amp;b&lt;c&gt;d&#xD;<![CDATA[<x>&]]></r>'
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
    source: Final = '<r xmlns:p="urn:p"><!--x--><?pi a?><p:a/></r>'
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
    source: Final = '<!--pre--><?pi lead?><!DOCTYPE r><r xmlns:p="urn:p"/><?pi tail?><!--post-->'
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
    names: Final = tuple(f"p{index:02d}" for index in range(33))
    source: Final = "<r " + " ".join(f'xmlns:{name}="urn:{name}" {name}:x="v"' for name in names) + "/>"
    expected: Final = (
        "<r "
        + " ".join(f'xmlns:{name}="urn:{name}"' for name in names)
        + " "
        + " ".join(f'{name}:x="v"' for name in names)
        + "></r>"
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
    source: Final = "<r " + " ".join(f'xmlns:p{index}="urn:{index}"' for index in range(33)) + ' xmlns:bad="rel"/>'
    with pytest.raises(ValueError, match="relative namespace URI cannot be canonicalized"):
        parse_xml(source).canonicalize()


@pytest.mark.parametrize(
    ("source", "scope"),
    [
        pytest.param('<r><c xmlns:p="relative"><p:a/></c></r>', "document", id="descendant-binding"),
        pytest.param('<r xmlns:p="relative"><c><p:a/></c></r>', "child", id="inherited-binding"),
    ],
)
def test_xml_c14n_rejects_relative_namespace_in_subtree(source: str, scope: str) -> None:
    document: Final = parse_xml(source)
    node = document.children[0].children[0] if scope == "child" else document
    with pytest.raises(ValueError, match="relative namespace URI cannot be canonicalized"):
        node.canonicalize()


def test_xml_c14n_accepts_shadowed_relative_namespace_on_subtree() -> None:
    document: Final = parse_xml('<r xmlns:p="relative"><c xmlns:p="urn:absolute"><p:a/></c></r>')
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
    source: Final = f'<r xmlns:{prefix}="urn:p"><{prefix}:a/></r>'
    assert parse_xml(source).canonicalize(Canonical(exclusive=True)) == (
        f'<r><{prefix}:a xmlns:{prefix}="urn:p"></{prefix}:a></r>'.encode()
    )


def test_xml_c14n_exclusive_sorts_visible_prefixes() -> None:
    source: Final = '<r xmlns:p="urn:p" xmlns:long="urn:long"><x long:a="1" p:a="2"/></r>'
    assert parse_xml(source).canonicalize(Canonical(exclusive=True)) == (
        b'<r><x xmlns:long="urn:long" xmlns:p="urn:p" long:a="1" p:a="2"></x></r>'
    )


def test_xml_c14n_exclusive_renders_rebound_prefix_after_nonusing_element() -> None:
    source: Final = '<r xmlns:p="urn:a"><p:a><x xmlns:p="urn:b"><p:b/></x></p:a></r>'
    assert parse_xml(source).canonicalize(Canonical(exclusive=True)) == (
        b'<r><p:a xmlns:p="urn:a"><x><p:b xmlns:p="urn:b"></p:b></x></p:a></r>'
    )


def test_xml_c14n_exclusive_forced_prefix_list_renders_once() -> None:
    source: Final = '<r xmlns:p="urn:p"><a/></r>'
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
    source: Final = '<r xmlns="urn:d"><a/></r>'
    assert parse_xml(source).canonicalize(Canonical(exclusive=True, inclusive_ns_prefixes=("#default",))) == (
        b'<r xmlns="urn:d"><a></a></r>'
    )


def test_xml_c14n_exclusive_forces_unused_default_namespace() -> None:
    source: Final = '<p:r xmlns="urn:d" xmlns:p="urn:p"><a xmlns="urn:e"/></p:r>'
    assert parse_xml(source).canonicalize(Canonical(exclusive=True, inclusive_ns_prefixes=("#default",))) == (
        b'<p:r xmlns="urn:d" xmlns:p="urn:p"><a xmlns="urn:e"></a></p:r>'
    )


def test_xml_c14n_exclusive_forced_prefix_rebound_on_child() -> None:
    source: Final = '<r xmlns:p="urn:p"><p:a xmlns:p="urn:q"/></r>'
    assert parse_xml(source).canonicalize(Canonical(exclusive=True, inclusive_ns_prefixes=("p",))) == (
        b'<r xmlns:p="urn:p"><p:a xmlns:p="urn:q"></p:a></r>'
    )


def test_xml_c14n_exclusive_forced_prefix_rebound_without_use() -> None:
    source: Final = '<r xmlns:p="urn:a"><a xmlns:p="urn:b"><b/></a></r>'
    assert parse_xml(source).canonicalize(Canonical(exclusive=True, inclusive_ns_prefixes=("p",))) == (
        b'<r xmlns:p="urn:a"><a xmlns:p="urn:b"><b></b></a></r>'
    )


def test_xml_c14n_apex_uses_nearest_xml_attribute() -> None:
    document = parse_xml('<r xmlns:p="urn:p" xml:lang="en"><a xml:lang="fr"><p:b/></a></r>')
    assert document.children[0].children[0].children[0].canonicalize() == (b'<p:b xmlns:p="urn:p" xml:lang="fr"></p:b>')


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
    document = parse_xml(source)
    node = document.select_one(selector)
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
        pytest.param("a/", "..", "", id="parent-reference"),
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
    assert node.canonicalize(Canonical(version="1.1")) == (
        f'<b xml:base="{expected}"></b>'.encode() if expected else b"<b></b>"
    )


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
    attributes: Final = " ".join(f'a{index}="x"' for index in range(33))
    node = _one(f'<div xml:base="http://[bad"><p xml:base="x" {attributes}></p></div>', "p")
    with pytest.raises(ValueError, match="Invalid IPv6 URL"):
        node.canonicalize(Canonical(version="1.1"))


def test_xml_c14n_comment_is_omitted_without_option() -> None:
    source: Final = '<!--pre--><r xmlns:p="urn:p"><!--inner--><p:a/></r><!--post-->'
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
    node = _one("<div>" + "<span>" * 200 + "x" + "</span>" * 200 + "</div>", "div")
    out = node.canonicalize()
    assert out.startswith(b"<div>" + b"<span>" * 200)
    assert out.endswith(b"</span>" * 200 + b"</div>")


def test_many_attributes_beyond_stack_buffer_sort() -> None:
    names = [f"a{index:02d}" for index in range(40)]
    node = Element("p", dict.fromkeys(reversed(names), "1"))
    out = node.canonicalize().decode()
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
        "<div " + " ".join(f'data-{index:05d}="{index}"' for index in range(size, 0, -1)) + "></div>"
    ).select("div")[0]
    assert (
        element.canonicalize()
        == ("<div " + " ".join(f'data-{index:05d}="{index}"' for index in range(1, size + 1)) + "></div>").encode()
    )


_XLINK: Final = ' xmlns:xlink="http://www.w3.org/1999/xlink"'
_SVG: Final = ' xmlns="http://www.w3.org/2000/svg"'


@pytest.mark.parametrize("exclusive", [False, True], ids=["inclusive", "exclusive"])
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
    source = INPUTS["canonicalize-deep"]()[1][1]
    assert isinstance(source, str)
    assert (
        parse(source).canonicalize()
        == (
            "<html><head></head><body>"
            f"<svg{_SVG}>" + f'<g><use{_XLINK} xlink:href="#x"></use>' * 150 + "</g>" * 150 + "</svg></body></html>"
        ).encode()
    )


@pytest.mark.parametrize("case", [0, 1, 2], ids=["deep", "sparse-xlink", "shallow"])
@pytest.mark.oracle
def test_canonicalize_benchmark_html_parser_difference(case: int) -> None:
    etree: Final = pytest.importorskip("lxml.etree")
    source: Final = cast("str", INPUTS["canonicalize-deep"]()[case][1])
    expected: Final = parse(source).canonicalize()
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
    etree: Final = pytest.importorskip("lxml.etree")
    tree: Final = parse(html)
    ours: Final = tree.canonicalize(Canonical(exclusive=exclusive, with_comments=with_comments))
    reparsed: Final = etree.parse(BytesIO(tree.serialize(Html(xml=True)).encode()))
    sink: Final = BytesIO()
    reparsed.write_c14n(sink, exclusive=exclusive, with_comments=with_comments)
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
    etree: Final = pytest.importorskip("lxml.etree")
    tree: Final = parse("<svg xlink:href=x><g><rect/></g></svg>")
    node: Final = tree.select_one(selector)
    assert node is not None
    ours: Final = node.canonicalize(Canonical(exclusive=exclusive, inclusive_ns_prefixes=tuple(prefixes or ())))
    root: Final = etree.fromstring(tree.serialize(Html(xml=True)).encode())
    target: Final = root.find(
        xpath, namespaces={"s": "http://www.w3.org/2000/svg", "m": "http://www.w3.org/1998/Math/MathML"}
    )
    theirs: Final = etree.tostring(target, method="c14n", exclusive=exclusive, inclusive_ns_prefixes=prefixes)
    assert ours == theirs
