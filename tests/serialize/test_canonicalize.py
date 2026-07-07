"""Canonical XML (c14n) serialization: ``Node.canonicalize`` and the ``Canonical`` config."""

from __future__ import annotations

import pytest

from turbohtml import Canonical, CData, Element, ProcessingInstruction, Text, parse


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


def test_inherited_attributes_skip_non_xml_ancestor_attributes() -> None:
    node = _one("<div id=x xml:lang=en><p>y</p></div>", "p")
    assert node.canonicalize() == b'<p xml:lang="en">y</p>'


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


def test_1_1_inherits_a_six_char_xml_attribute_that_is_not_xml_id() -> None:
    node = _one("<div xml:xx=v><p>x</p></div>", "p")
    assert node.canonicalize(Canonical(version="1.1")) == b'<p xml:xx="v">x</p>'


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
