from __future__ import annotations

from typing import Final, cast

import pytest
from bench.operations import INPUTS

from turbohtml import (
    CData,
    Comment,
    Doctype,
    Document,
    Element,
    HTMLParseError,
    Node,
    ProcessingInstruction,
    Text,
    parse_xml,
)


def elements(node: Element) -> list[Element]:
    return [child for child in node if isinstance(child, Element)]


def root_of(doc: Document) -> Element:
    root = doc.children[0]
    assert isinstance(root, Element)
    return root


def data_of(node: Node) -> str:
    assert isinstance(node, (Text, CData, Comment))
    return node.data


def test_returns_document_with_single_root() -> None:
    doc = parse_xml("<root><child>hi</child></root>")
    assert isinstance(doc, Document)
    root = doc.children[0]
    assert isinstance(root, Element)
    assert root.tag == "root"
    assert [child.tag for child in elements(root)] == ["child"]


def test_underscore_and_punctuation_in_names() -> None:
    doc = parse_xml('<_root x.y-z="1"><a.b-c/></_root>')
    root = root_of(doc)
    assert root.tag == "_root"
    assert dict(root.attrs) == {"x.y-z": "1"}
    assert elements(root)[0].tag == "a.b-c"


def test_case_sensitive_tag_names() -> None:
    doc = parse_xml("<Root><Root/></Root>")
    outer = root_of(doc)
    assert outer.tag == "Root"
    assert elements(outer)[0].tag == "Root"


def test_case_sensitive_end_tag_mismatch() -> None:
    with pytest.raises(HTMLParseError) as info:
        parse_xml("<Root></root>")
    assert info.value.error.code == "xml-mismatched-tag"


def test_self_closing_any_element() -> None:
    doc = parse_xml("<root><br/><input/><custom/></root>")
    root = root_of(doc)
    assert [child.tag for child in elements(root)] == ["br", "input", "custom"]
    assert all(len(child.children) == 0 for child in elements(root))


def test_void_like_name_is_not_special() -> None:
    doc = parse_xml("<img>text</img>")
    root = root_of(doc)
    assert root.tag == "img"
    assert data_of(root.children[0]) == "text"


def test_xml_declaration_is_not_a_node() -> None:
    doc = parse_xml('<?xml version="1.0" encoding="UTF-8"?><root/>')
    assert [type(child) for child in doc.children] == [Element]


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param("<?xml version='1.0'?><r/>", id="version-only-single-quote"),
        pytest.param('<?xml version = "1.0"?><r/>', id="spaces-around-equals"),
        pytest.param('<?xml version="1.11"?><r/>', id="multi-digit-minor"),
        pytest.param('<?xml version="1.0" ?><r/>', id="trailing-space"),
        pytest.param('<?xml version="1.0" encoding="a.b_c-1"?><r/>', id="encoding-all-name-chars"),
        pytest.param('<?xml version="1.0" standalone="no"?><r/>', id="standalone-no"),
        pytest.param('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><r/>', id="encoding-then-standalone"),
    ],
)
def test_well_formed_xml_declaration_parses(markup: str) -> None:
    assert root_of(parse_xml(markup)).tag == "r"


@pytest.mark.parametrize(
    "target",
    [
        pytest.param("target", id="unrelated"),
        pytest.param("foo", id="non-x-length-three"),
        pytest.param("xyz", id="x-then-non-m"),
        pytest.param("xmz", id="xm-then-non-l"),
        pytest.param("xml-stylesheet", id="xml-prefixed-name"),
    ],
)
def test_non_reserved_pi_targets_parse(target: str) -> None:
    pi = root_of(parse_xml(f"<r><?{target} data?></r>")).children[0]
    assert isinstance(pi, ProcessingInstruction)
    assert pi.target == target


def test_comment_and_processing_instruction() -> None:
    doc = parse_xml("<root><!-- note --><?target the data?></root>")
    root = doc.children[0]
    comment, pi = root.children
    assert isinstance(comment, Comment)
    assert comment.data == " note "
    assert isinstance(pi, ProcessingInstruction)
    assert pi.target == "target"
    assert pi.data == "the data"


def test_cdata_section_is_its_own_node() -> None:
    doc = parse_xml("<root><![CDATA[<not> & parsed]]></root>")
    cdata = doc.children[0].children[0]
    assert isinstance(cdata, CData)
    assert cdata.data == "<not> & parsed"


def test_top_level_comment_and_pi_attach_to_document() -> None:
    doc = parse_xml("<!-- lead --><?go now?><root/><!-- tail -->")
    assert [type(child) for child in doc.children] == [Comment, ProcessingInstruction, Element, Comment]


def test_doctype_becomes_a_node() -> None:
    doc = parse_xml('<!DOCTYPE root SYSTEM "root.dtd"><root/>')
    doctype = doc.children[0]
    assert isinstance(doctype, Doctype)
    assert doctype.name == "root"


def test_doctype_with_internal_subset() -> None:
    doc = parse_xml("<!DOCTYPE root [ <!ELEMENT root (#PCDATA)> ]><root/>")
    assert isinstance(doc.children[0], Doctype)


@pytest.mark.parametrize(
    ("markup", "text"),
    [
        pytest.param("<r>&lt;&gt;&amp;&quot;&apos;</r>", "<>&\"'", id="predefined"),
        pytest.param("<r>&#65;&#x42;&#x43;</r>", "ABC", id="numeric"),
        pytest.param("<r>&#9;&#10;&#13;</r>", "\t\n\r", id="numeric-whitespace"),
        pytest.param("<r>&#xE000;</r>", "", id="numeric-private-use"),
        pytest.param("<r>&#x1F600;</r>", "\U0001f600", id="numeric-supplementary"),
    ],
)
def test_entity_and_character_references(markup: str, text: str) -> None:
    assert data_of(root_of(parse_xml(markup)).children[0]) == text


def test_line_endings_normalize_to_lf() -> None:
    assert data_of(root_of(parse_xml("<r>a\r\nb\rc</r>")).children[0]) == "a\nb\nc"


def test_allowed_control_whitespace_in_content() -> None:
    assert data_of(root_of(parse_xml("<r>\ta\nb</r>")).children[0]) == "\ta\nb"


def test_lone_right_bracket_in_content_is_text() -> None:
    assert data_of(root_of(parse_xml("<r>a]b]]c</r>")).children[0]) == "a]b]]c"


def test_whitespace_variants_separate_markup() -> None:
    doc = parse_xml('<r\ta="1"\nb="2"\rc="3"/>')
    assert dict(root_of(doc).attrs) == {"a": "1", "b": "2", "c": "3"}


def test_processing_instruction_without_data() -> None:
    doc = parse_xml("<root><?bare?></root>")
    pi = root_of(doc).children[0]
    assert isinstance(pi, ProcessingInstruction)
    assert (pi.target, pi.data) == ("bare", "")


def test_single_quoted_attribute_value() -> None:
    assert dict(root_of(parse_xml("<r a='v'/>")).attrs) == {"a": "v"}


def test_attribute_value_whitespace_folds_to_space() -> None:
    assert dict(root_of(parse_xml('<r a="x\ty\nz\rw"/>')).attrs) == {"a": "x y z w"}


def test_attribute_value_crlf_folds_to_one_space() -> None:
    # XML 2.11 collapses CRLF to a single LF before 3.3.3 folds it to one space, not two
    assert dict(root_of(parse_xml('<r a="x\r\ny\r\n\tz"/>')).attrs) == {"a": "x y  z"}


def test_cdata_normalizes_line_endings() -> None:
    assert data_of(root_of(parse_xml("<r><![CDATA[a\r\nb\rc]]></r>")).children[0]) == "a\nb\nc"


def test_cdata_trailing_carriage_return_folds_to_lf() -> None:
    # a lone CR as the last content character still folds, exercising the CRLF look-ahead's edge
    assert data_of(root_of(parse_xml("<r><![CDATA[a\r]]></r>")).children[0]) == "a\n"


def test_attribute_ending_in_carriage_return_is_unterminated() -> None:
    with pytest.raises(HTMLParseError) as info:
        parse_xml('<r a="x\r')
    assert info.value.error.code == "xml-unterminated-attribute"


def test_comment_normalizes_line_endings() -> None:
    assert data_of(root_of(parse_xml("<r><!--a\r\nb\rc-->x</r>")).children[0]) == "a\nb\nc"


def test_processing_instruction_normalizes_line_endings() -> None:
    pi = root_of(parse_xml("<r><?t a\r\nb\rc?></r>")).children[0]
    assert isinstance(pi, ProcessingInstruction)
    assert pi.data == "a\nb\nc"


def test_attribute_reference_is_resolved() -> None:
    assert dict(root_of(parse_xml('<r a="1 &lt; 2"/>')).attrs) == {"a": "1 < 2"}


def test_namespace_prefix_and_declaration_preserved() -> None:
    doc = parse_xml('<root xmlns:h="urn:h" xmlns="urn:d"><h:a h:k="v" id="1"/></root>')
    root = root_of(doc)
    assert dict(root.attrs) == {"xmlns:h": "urn:h", "xmlns": "urn:d"}
    child = elements(root)[0]
    assert child.tag == "h:a"
    assert dict(child.attrs) == {"h:k": "v", "id": "1"}


def test_reserved_xml_prefix_needs_no_declaration() -> None:
    doc = parse_xml('<root xml:lang="en"/>')
    assert dict(root_of(doc).attrs) == {"xml:lang": "en"}


def test_prefix_declared_on_same_element() -> None:
    doc = parse_xml('<p:root xmlns:p="urn:p"/>')
    assert root_of(doc).tag == "p:root"


def test_kinds_are_all_handled() -> None:
    assert root_of(parse_xml("<ascii>x</ascii>")).tag == "ascii"
    assert data_of(root_of(parse_xml("<中>文</中>")).children[0]) == "文"
    assert data_of(root_of(parse_xml("<r>\U0001d538</r>")).children[0]) == "\U0001d538"


def test_non_ascii_attribute_names_encode() -> None:
    doc = parse_xml('<r é="a" 中="b" \U0001d538="c"/>')
    assert dict(root_of(doc).attrs) == {"é": "a", "中": "b", "\U0001d538": "c"}


def test_attribute_name_lookup_is_case_sensitive() -> None:
    root = root_of(parse_xml('<test x="a" Y="b">t</test>'))
    assert root.attrs["Y"] == "b"
    assert root.attrs["x"] == "a"
    with pytest.raises(KeyError):
        root.attrs["y"]
    with pytest.raises(KeyError):
        root.attrs["X"]


def test_attribute_get_and_contains_are_case_sensitive() -> None:
    root = root_of(parse_xml('<test Y="b">t</test>'))
    assert root.attrs.get("Y") == "b"
    assert root.attrs.get("y") is None
    assert "Y" in root.attrs
    assert "y" not in root.attrs


def test_attr_method_is_case_sensitive() -> None:
    root = root_of(parse_xml('<test Y="b">t</test>'))
    assert root.attr("Y") == "b"
    assert root.attr("y") is None
    assert root.attr("y", "fallback") == "fallback"


def test_attributes_differing_only_in_case_are_distinct() -> None:
    root = root_of(parse_xml('<test a="lower" A="upper"/>'))
    assert root.attrs["a"] == "lower"
    assert root.attrs["A"] == "upper"


def test_attribute_set_preserves_case_and_stays_distinct() -> None:
    root = root_of(parse_xml('<test Y="b"/>'))
    root.attrs["Z"] = "c"
    root.attrs["z"] = "d"
    assert dict(root.attrs) == {"Y": "b", "Z": "c", "z": "d"}


def test_attribute_delete_is_case_sensitive() -> None:
    root = root_of(parse_xml('<test Y="b"/>'))
    with pytest.raises(KeyError):
        del root.attrs["y"]
    del root.attrs["Y"]
    assert dict(root.attrs) == {}


def test_re_over_attribute_is_case_sensitive() -> None:
    root = root_of(parse_xml('<test Y="abc"/>'))
    assert root.re(r"(\w)", attr="Y") == ["a", "b", "c"]
    assert root.re(r"(\w)", attr="y") == []
    assert root.re_first(r"(\w)", attr="y", default="none") == "none"


def test_lowercase_hex_character_reference() -> None:
    assert data_of(root_of(parse_xml("<r>&#xe9;&#xff;</r>")).children[0]) == "éÿ"


def test_start_tag_after_closed_root() -> None:
    with pytest.raises(HTMLParseError) as info:
        parse_xml("<a></a><b/>")
    assert info.value.error.code == "xml-content-outside-root"


def test_deeply_nested_document_grows_buffers() -> None:
    depth = 25
    opens = "".join(f'<n{i} xmlns:p{i}="urn:{i}">' for i in range(depth))
    closes = "".join(f"</n{i}>" for i in reversed(range(depth)))
    node = root_of(parse_xml(opens + closes))
    for _ in range(depth - 1):
        node = elements(node)[0]
    assert node.tag == f"n{depth - 1}"


def test_many_attributes_grow_buffers() -> None:
    attrs = " ".join(f'a{i}="{i}"' for i in range(12))
    parsed = dict(root_of(parse_xml(f"<r {attrs}/>")).attrs)
    assert parsed["a11"] == "11"


def test_long_reference_run_grows_scratch() -> None:
    body = "a" * 100 + "&amp;" + "b" * 100
    assert data_of(root_of(parse_xml(f"<r>{body}</r>")).children[0]) == "a" * 100 + "&" + "b" * 100


@pytest.mark.parametrize(
    ("markup", "code"),
    [
        pytest.param("<a></b>", "xml-mismatched-tag", id="mismatched"),
        pytest.param("<a>", "xml-premature-eof", id="unclosed"),
        pytest.param("</a>", "xml-unexpected-end-tag", id="stray-end"),
        pytest.param("", "xml-no-root-element", id="empty"),
        pytest.param("   ", "xml-no-root-element", id="whitespace-only"),
        pytest.param("<a/><b/>", "xml-multiple-roots", id="two-roots"),
        pytest.param("<a/>tail", "xml-content-outside-root", id="text-after-root"),
        pytest.param("<a/><![CDATA[x]]>", "xml-cdata-outside-root", id="cdata-outside"),
        pytest.param("junk<a/>", "xml-content-outside-root", id="text-before-root"),
        pytest.param("<1/>", "xml-invalid-name", id="bad-start-name"),
        pytest.param("</1>", "xml-invalid-name", id="bad-end-name"),
        pytest.param("<a 1='x'/>", "xml-invalid-name", id="bad-attr-name"),
        pytest.param("<a><??></a>", "xml-invalid-name", id="bad-pi-name"),
        pytest.param("<!DOCTYPE 1><a/>", "xml-invalid-name", id="bad-doctype-name"),
        pytest.param("<a b/>", "xml-expected-equals", id="attr-no-equals"),
        pytest.param("<a b", "xml-expected-equals", id="attr-eof"),
        pytest.param("<a b=x/>", "xml-unquoted-attribute", id="unquoted"),
        pytest.param("<a b=", "xml-unquoted-attribute", id="value-eof"),
        pytest.param('<a b="x', "xml-unterminated-attribute", id="unterminated-value"),
        pytest.param('<a b="<"/>', "xml-lt-in-attribute", id="lt-in-value"),
        pytest.param('<a b="1" b="2"/>', "xml-duplicate-attribute", id="duplicate-attr"),
        pytest.param("<a", "xml-unclosed-start-tag", id="tag-eof"),
        pytest.param('<a b="1"c="2"/>', "xml-unclosed-start-tag", id="missing-space"),
        pytest.param("<a /x", "xml-unclosed-start-tag", id="bad-slash"),
        pytest.param("</a", "xml-unterminated-end-tag", id="end-tag-eof"),
        pytest.param("<a></a x>", "xml-unterminated-end-tag", id="end-tag-junk"),
        pytest.param("<!x><a/>", "xml-unterminated-markup", id="bad-bang"),
        pytest.param("<a><!-- c </a>", "xml-unterminated-comment", id="unterminated-comment"),
        pytest.param("<a><!-- a--b --></a>", "xml-double-hyphen-in-comment", id="double-hyphen"),
        pytest.param("<a><![CDATA[x</a>", "xml-unterminated-cdata", id="unterminated-cdata"),
        pytest.param("<a><?pi go</a>", "xml-unterminated-pi", id="unterminated-pi"),
        pytest.param("<?pi", "xml-unterminated-pi", id="pi-eof-after-target"),
        pytest.param("<a><?xml v?></a>", "xml-reserved-pi-target", id="reserved-pi-target"),
        pytest.param("<!DOCTYPE root", "xml-unterminated-doctype", id="unterminated-doctype"),
        pytest.param("<a><!DOCTYPE x></a>", "xml-doctype-outside-prolog", id="doctype-nested"),
        pytest.param("<a/><!DOCTYPE x>", "xml-doctype-outside-prolog", id="doctype-after-root"),
        pytest.param("<!DOCTYPE x><!DOCTYPE y><a/>", "xml-doctype-outside-prolog", id="doctype-twice"),
        pytest.param("<r>&bad;</r>", "xml-undefined-entity", id="undefined-entity"),
        pytest.param('<r a="&bad;"/>', "xml-undefined-entity", id="undefined-entity-in-attr"),
        pytest.param("<r>&amp", "xml-unterminated-reference", id="unterminated-entity"),
        pytest.param("<r>&#65", "xml-unterminated-reference", id="unterminated-numeric"),
        pytest.param("<r>&", "xml-unterminated-reference", id="bare-ampersand"),
        pytest.param("<r>&#", "xml-unterminated-reference", id="bare-numeric"),
        pytest.param("<r>&#;</r>", "xml-invalid-char-ref", id="empty-numeric"),
        pytest.param("<r>&#x;</r>", "xml-invalid-char-ref", id="empty-hex"),
        pytest.param("<r>&#5x;</r>", "xml-invalid-char-ref", id="bad-decimal-digit"),
        pytest.param("<r>&#x/;</r>", "xml-invalid-char-ref", id="hex-below-zero"),
        pytest.param("<r>&#xG;</r>", "xml-invalid-char-ref", id="bad-hex-upper"),
        pytest.param("<r>&#xg;</r>", "xml-invalid-char-ref", id="bad-hex-lower"),
        pytest.param("<r>&#8;</r>", "xml-invalid-char-ref", id="control-char-ref"),
        pytest.param("<r>&#0;</r>", "xml-invalid-char-ref", id="null-char-ref"),
        pytest.param("<r>&#xD800;</r>", "xml-invalid-char-ref", id="surrogate-char-ref"),
        pytest.param("<r>&#xFFFF;</r>", "xml-invalid-char-ref", id="noncharacter-ref"),
        pytest.param("<r>&#99999999999;</r>", "xml-invalid-char-ref", id="overflow-char-ref"),
        pytest.param("<r>&#X43;</r>", "xml-invalid-char-ref", id="uppercase-hex-marker"),
        pytest.param("<r>￿</r>", "xml-invalid-char", id="noncharacter-in-content"),
        pytest.param("<r>￾</r>", "xml-invalid-char", id="noncharacter-fffe-in-content"),
        pytest.param('<r a="￿"/>', "xml-invalid-char", id="noncharacter-in-attribute"),
        pytest.param("<r><!-- ￿ --></r>", "xml-invalid-char", id="noncharacter-in-comment"),
        pytest.param("<r><![CDATA[￿]]></r>", "xml-invalid-char", id="noncharacter-in-cdata"),
        pytest.param("<r><?pi ￿?></r>", "xml-invalid-char", id="noncharacter-in-pi"),
        pytest.param("<r>\x0c</r>", "xml-invalid-char", id="form-feed-in-content"),
        pytest.param("<r><?pi a\x0cb?></r>", "xml-invalid-char", id="form-feed-in-pi"),
        pytest.param("<r><?pitarget+data?></r>", "xml-malformed-pi", id="pi-target-data-no-space"),
        pytest.param("<?XML version='1.0'?><r/>", "xml-reserved-pi-target", id="uppercase-xml-decl-target"),
        pytest.param("<r><?xmL go?></r>", "xml-reserved-pi-target", id="mixed-case-reserved-target"),
        pytest.param("<?xml?><r/>", "xml-malformed-declaration", id="decl-no-version"),
        pytest.param("<?xml ?><r/>", "xml-malformed-declaration", id="decl-space-no-version"),
        pytest.param("<?xml encoding='UTF-8'?><r/>", "xml-malformed-declaration", id="decl-encoding-first"),
        pytest.param("<?xml version?><r/>", "xml-malformed-declaration", id="decl-version-no-equals"),
        pytest.param("<?xml version", "xml-malformed-declaration", id="decl-version-eof-before-equals"),
        pytest.param("<?xml version=?><r/>", "xml-malformed-declaration", id="decl-version-no-quote"),
        pytest.param("<?xml version=", "xml-malformed-declaration", id="decl-version-eof-before-quote"),
        pytest.param('<?xml version="1.0?><r/>', "xml-malformed-declaration", id="decl-version-unterminated"),
        pytest.param('<?xml version="2.0"?><r/>', "xml-malformed-declaration", id="decl-version-not-one"),
        pytest.param('<?xml version="1x"?><r/>', "xml-malformed-declaration", id="decl-version-too-short"),
        pytest.param('<?xml version="1_0"?><r/>', "xml-malformed-declaration", id="decl-version-no-dot"),
        pytest.param('<?xml version="1.0z"?><r/>', "xml-malformed-declaration", id="decl-version-bad-digit"),
        pytest.param('<?xml version="1./"?><r/>', "xml-malformed-declaration", id="decl-version-below-zero"),
        pytest.param('<?xml version="1.0"x?><r/>', "xml-malformed-declaration", id="decl-attr-no-space"),
        pytest.param('<?xml version="1.0" bad="x"?><r/>', "xml-malformed-declaration", id="decl-unknown-attr"),
        pytest.param('<?xml version="1.0" encoding=""?><r/>', "xml-malformed-declaration", id="decl-encoding-empty"),
        pytest.param('<?xml version="1.0" encoding?><r/>', "xml-malformed-declaration", id="decl-encoding-no-value"),
        pytest.param(
            '<?xml version="1.0" standalone?><r/>', "xml-malformed-declaration", id="decl-standalone-no-value"
        ),
        pytest.param(
            '<?xml version="1.0" encoding="8"?><r/>', "xml-malformed-declaration", id="decl-encoding-bad-start"
        ),
        pytest.param(
            '<?xml version="1.0" encoding="{"?><r/>', "xml-malformed-declaration", id="decl-encoding-start-above-z"
        ),
        pytest.param(
            '<?xml version="1.0" encoding="a{"?><r/>', "xml-malformed-declaration", id="decl-encoding-char-above-z"
        ),
        pytest.param(
            '<?xml version="1.0" encoding="a b"?><r/>', "xml-malformed-declaration", id="decl-encoding-bad-char"
        ),
        pytest.param(
            '<?xml version="1.0" standalone="maybe"?><r/>', "xml-malformed-declaration", id="decl-standalone-bad"
        ),
        pytest.param(
            '<?xml version="1.0" standalone="yes" encoding="UTF-8"?><r/>',
            "xml-malformed-declaration",
            id="decl-encoding-after-standalone",
        ),
        pytest.param(
            '<?xml version="1.0" standalone="yes" standalone="no"?><r/>',
            "xml-malformed-declaration",
            id="decl-duplicate-standalone",
        ),
        pytest.param("<r>x\r", "xml-premature-eof", id="trailing-cr"),
        pytest.param("<", "xml-invalid-name", id="bare-lt"),
        pytest.param("</", "xml-invalid-name", id="bare-end"),
        pytest.param("<r>]]></r>", "xml-cdata-close-in-content", id="cdata-close-in-content"),
        pytest.param("<r>\x01</r>", "xml-invalid-char", id="control-char"),
        pytest.param("<a:r/>", "xml-undeclared-namespace", id="undeclared-element-prefix"),
        pytest.param('<r xmlns:pp="u" p:x="1"/>', "xml-undeclared-namespace", id="undeclared-attr-prefix-len"),
        pytest.param('<r xmlns:px="u" py:x="1"/>', "xml-undeclared-namespace", id="undeclared-attr-prefix-char"),
        pytest.param("<̀a/>", "xml-invalid-name", id="combining-mark-name-start"),
        pytest.param("<·a/>", "xml-invalid-name", id="middle-dot-name-start"),
        pytest.param('<r xmlns:="u"/>', "xml-invalid-namespace-decl", id="empty-prefix-declaration"),
        pytest.param('<r xmlns:xml="urn:x"/>', "xml-reserved-prefix", id="xml-prefix-rebound"),
        pytest.param(
            '<r xmlns:xmlns="http://www.w3.org/2000/xmlns/"/>', "xml-reserved-prefix", id="xmlns-prefix-declared"
        ),
        pytest.param(
            '<r xmlns:p="http://www.w3.org/XML/1998/namespace"/>', "xml-reserved-namespace", id="xml-uri-other-prefix"
        ),
        pytest.param('<r xmlns:p="http://www.w3.org/2000/xmlns/"/>', "xml-reserved-namespace", id="xmlns-uri-bound"),
        pytest.param(
            '<r xmlns="http://www.w3.org/XML/1998/namespace"/>', "xml-reserved-namespace", id="xml-uri-as-default"
        ),
        pytest.param('<r xmlns="http://www.w3.org/2000/xmlns/"/>', "xml-reserved-namespace", id="xmlns-uri-as-default"),
        pytest.param(
            '<r xmlns:a="u" xmlns:b="u"><q a:k="1" b:k="2"/></r>',
            "xml-duplicate-attribute",
            id="expanded-name-collision",
        ),
        pytest.param("<?a:b d?><r/>", "xml-invalid-pi-target", id="colon-in-pi-target"),
    ],
)
def test_well_formedness_error(markup: str, code: str) -> None:
    with pytest.raises(HTMLParseError) as info:
        parse_xml(markup)
    assert info.value.error.code == code


def test_error_carries_line_and_column() -> None:
    with pytest.raises(HTMLParseError) as info:
        parse_xml("<root>\n</bad>")
    error = info.value.error
    assert error.code == "xml-mismatched-tag"
    assert error.line == 2
    assert error.col == 2


def test_combining_mark_is_a_name_char_but_not_a_name_start() -> None:
    tag = "a\u00e9\u0300"  # a start letter, a non-ASCII NameStartChar, then a combining grave (NameChar only)
    assert root_of(parse_xml(f"<{tag}/>")).tag == tag


def test_xml_prefix_may_bind_its_own_namespace() -> None:
    doc = parse_xml('<r xmlns:xml="http://www.w3.org/XML/1998/namespace" xml:lang="en"/>')
    assert dict(root_of(doc).attrs)["xml:lang"] == "en"


def test_default_declaration_of_a_plain_2000_length_uri_parses() -> None:
    doc = parse_xml('<r xmlns="http://www.w3.org/2000/abcde/"/>')  # same length as the reserved xmlns name, not it
    assert dict(root_of(doc).attrs) == {"xmlns": "http://www.w3.org/2000/abcde/"}


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param('<r xmlns:a="urn:a" xmlns:b="urn:b" a:k="1" b:k="2"/>', id="same-local-different-uri"),
        pytest.param('<r xmlns:a="u" xmlns:b="vv" a:k="1" b:k="2"/>', id="same-local-different-uri-length"),
        pytest.param('<r xmlns:a="urn:a" a:k="1" a:m="2"/>', id="same-uri-different-local"),
        pytest.param('<r xmlns:a="urn:a" a:k="1" a:kk="2"/>', id="same-uri-different-local-length"),
    ],
)
def test_attributes_with_distinct_expanded_names_are_allowed(markup: str) -> None:
    parse_xml(markup)  # a shared prefix, URI, or local name alone is not a collision


def test_non_str_raises_type_error() -> None:
    with pytest.raises(TypeError):
        parse_xml(b"<root/>")  # ty: ignore[invalid-argument-type]


@pytest.mark.parametrize("count", [pytest.param(1, id="single"), pytest.param(1000, id="many")])
def test_xml_attribute_append_preserves_values_and_order(count: int) -> None:
    expected: Final = [(f"a{index}", str(index)) for index in range(count)]
    source: Final = "<root " + " ".join(f'{name}="{value}"' for name, value in expected) + "/>"
    root: Final = parse_xml(source).find("root")
    assert root is not None
    assert list(root.attrs.items()) == expected


@pytest.mark.parametrize(
    ("source", "code"),
    [
        pytest.param('<r a="1" a="2"/>', "xml-duplicate-attribute", id="lexical-duplicate"),
        pytest.param('<r p:a="1" p:a="2"/>', "xml-duplicate-attribute", id="duplicate-before-prefix-check"),
        pytest.param('<r xmlns:p="urn:a" xmlns:p=""/>', "xml-duplicate-attribute", id="duplicate-before-declaration"),
        pytest.param('<r a="1" a="&bad;"/>', "xml-undefined-entity", id="invalid-value-before-duplicate"),
    ],
)
def test_xml_attribute_append_preserves_first_error(source: str, code: str) -> None:
    with pytest.raises(HTMLParseError) as error:
        parse_xml(source)
    assert error.value.error.code == code


def test_xml_attribute_append_keeps_later_attribute_replacement() -> None:
    root: Final = parse_xml('<root a="before"/>').find("root")
    assert root is not None
    root.attrs["a"] = "after"
    root.attrs["b"] = "new"
    assert list(root.attrs.items()) == [("a", "after"), ("b", "new")]


@pytest.mark.parametrize("quote", ['"', "'"], ids=["double", "single"])
@pytest.mark.parametrize("character", ["a", "é", "λ", "𐀀"], ids=["ascii", "latin1", "ucs2", "ucs4"])
@pytest.mark.parametrize("length", [0, 65, 65536], ids=["empty", "growth", "long"])
def test_xml_attribute_run_value(quote: str, character: str, length: int) -> None:
    value: Final = character * length
    root: Final = parse_xml(f"<root value={quote}{value}{quote}/>").find("root")
    assert root is not None
    assert root.attrs["value"] == value


@pytest.mark.parametrize("quote", ['"', "'"], ids=["double", "single"])
def test_xml_attribute_run_normalization(quote: str) -> None:
    root: Final = parse_xml(f"<root value={quote}" + "a" * 80 + f"&amp;b\t&#9;\r\n{quote}/>").find("root")
    assert root is not None
    assert root.attrs["value"] == "a" * 80 + "&b \t "


def test_xml_attribute_run_scratch_reuse() -> None:
    long: Final = "a" * 1024
    root: Final = parse_xml(f'<root first="{long}" second="short" third="{long}"/>').find("root")
    assert root is not None
    assert list(root.attrs.items()) == [("first", long), ("second", "short"), ("third", long)]


@pytest.mark.parametrize(
    ("tail", "code"),
    [
        pytest.param("<", "xml-lt-in-attribute", id="markup"),
        pytest.param("\x00", "xml-invalid-char", id="null"),
        pytest.param("\x01", "xml-invalid-char", id="control"),
    ],
)
def test_xml_attribute_run_error_position(tail: str, code: str) -> None:
    with pytest.raises(HTMLParseError) as error:
        parse_xml('<root a="' + "a" * 80 + tail + '"/>')
    assert (error.value.error.code, error.value.error.line, error.value.error.col) == (code, 1, 89)


@pytest.mark.parametrize(
    ("index", "expected"),
    [(0, "a" * 65536), (1, "a&b " * 8192), (2, "hello")],
    ids=["clean", "references", "tiny"],
)
def test_xml_value_benchmark_output(index: int, expected: str) -> None:
    root: Final = parse_xml(cast("str", INPUTS["parse-xml-values"]()[index][1])).find("root")
    assert root is not None
    assert root.attrs["value"] == expected


@pytest.mark.parametrize("prefix", ["p", "λ", "𐀀"], ids=["ascii", "ucs2", "ucs4"])
@pytest.mark.parametrize("count", [1, 128], ids=["single", "many"])
def test_xml_namespace_attribute_order(prefix: str, count: int) -> None:
    expected: Final = [(f"xmlns:{prefix}{index}", f"urn:{index}") for index in range(count)] + [
        (f"{prefix}{index}:value", "x") for index in range(count)
    ]
    root: Final = parse_xml("<root " + " ".join(f'{name}="{value}"' for name, value in expected) + "/>").find("root")
    assert root is not None
    assert list(root.attrs.items()) == expected


def test_xml_namespace_self_closing_rebinding() -> None:
    root: Final = parse_xml(
        '<root xmlns:p="urn:p" xmlns:q="urn:q"><before xmlns:p="urn:q" p:value="a"/>'
        '<after p:value="b" q:value="c"/></root>'
    ).find("after")
    assert root is not None
    assert list(root.attrs.items()) == [("p:value", "b"), ("q:value", "c")]


@pytest.mark.parametrize(
    ("source", "code"),
    [
        pytest.param(
            '<root xmlns:p="urn:p" xmlns:q="urn:q"><child xmlns:p="urn:q" p:value="a" q:value="b"/></root>',
            "xml-duplicate-attribute",
            id="rebound-collision",
        ),
        pytest.param(
            '<root xmlns:p="urn:p" xmlns:q="urn:p" p:value="a" q:value="b" missing:value="c"/>',
            "xml-undeclared-namespace",
            id="prefix-error-before-expanded-collision",
        ),
    ],
)
def test_xml_namespace_first_error(source: str, code: str) -> None:
    with pytest.raises(HTMLParseError) as error:
        parse_xml(source)
    assert error.value.error.code == code


@pytest.mark.parametrize("length", [0, 7, 8, 16], ids=["start", "last-byte", "next-block", "two-blocks"])
@pytest.mark.parametrize(
    ("tail", "expected"),
    [
        pytest.param("end", "end", id="plain"),
        pytest.param("&amp;end", "&end", id="reference"),
        pytest.param("\r\nend", "\nend", id="crlf"),
        pytest.param("]end", "]end", id="bracket"),
        pytest.param("<child/>end", "end", id="markup"),
    ],
)
def test_xml_text_block_boundary(length: int, tail: str, expected: str) -> None:
    root: Final = parse_xml("<root>" + "a" * length + tail + "</root>").find("root")
    assert root is not None
    assert root.text == "a" * length + expected


@pytest.mark.parametrize("text", ["a", "é", "λ", "𐀀"], ids=["ascii", "latin1", "ucs2", "ucs4"])
def test_xml_text_block_width(text: str) -> None:
    root: Final = parse_xml("<root>" + text * 65536 + "</root>").find("root")
    assert root is not None
    assert root.text == text * 65536


@pytest.mark.parametrize("length", [7, 8, 16], ids=["last-byte", "next-block", "two-blocks"])
@pytest.mark.parametrize(
    ("tail", "code"),
    [
        pytest.param("\x00", "xml-invalid-char", id="null"),
        pytest.param("\x01", "xml-invalid-char", id="control"),
        pytest.param("]]>", "xml-cdata-close-in-content", id="cdata-close"),
    ],
)
def test_xml_text_block_error_position(length: int, tail: str, code: str) -> None:
    with pytest.raises(HTMLParseError) as error:
        parse_xml("<root>" + "a" * length + tail + "</root>")
    assert (error.value.error.code, error.value.error.line, error.value.error.col) == (code, 1, length + 6)


@pytest.mark.parametrize(
    ("index", "expected"),
    [(0, "a" * 65536), (1, "a&b " * 8192), (2, "hello")],
    ids=["clean", "references", "tiny"],
)
def test_xml_text_benchmark_output(index: int, expected: str) -> None:
    root: Final = parse_xml(cast("str", INPUTS["parse-xml-text"]()[index][1])).find("root")
    assert root is not None
    assert root.text == expected


@pytest.mark.oracle
def test_round_trip_against_lxml() -> None:
    lxml_etree: Final = pytest.importorskip("lxml.etree")
    source: Final = (
        '<catalog xmlns:dc="urn:dc">'
        '<book id="b1"><dc:title>One</dc:title><price>9.99</price></book>'
        '<book id="b2"><dc:title>Two &amp; a half</dc:title><price>5.00</price></book>'
        "<!-- end --></catalog>"
    )
    theirs: Final = lxml_etree.fromstring(source.encode())
    ours: Final = root_of(parse_xml(source))
    their_locals: Final = [lxml_etree.QName(node).localname for node in theirs.iter() if isinstance(node.tag, str)]
    assert [node.tag.split(":")[-1] for node in (ours, *ours.descendants) if isinstance(node, Element)] == their_locals


@pytest.mark.parametrize(("index", "count"), [(0, 128), (1, 1)], ids=["many", "single"])
@pytest.mark.oracle
def test_lxml_namespace_benchmark_output(index: int, count: int) -> None:
    etree: Final = pytest.importorskip("lxml.etree", exc_type=ImportError)
    root: Final = etree.fromstring(cast("str", INPUTS["parse-xml-prefixes"]()[index][1]).encode())
    assert (root.tag, list(root.nsmap.items()), list(root.attrib.items()), len(root)) == (
        "root",
        [(f"p{number}", f"urn:{number}") for number in range(count)],
        [(f"{{urn:{number}}}value", "x") for number in range(count)],
        0,
    )


@pytest.mark.parametrize(
    ("index", "expected"),
    [(0, "a" * 65536), (1, "a&b " * 8192), (2, "hello")],
    ids=["clean", "references", "tiny"],
)
@pytest.mark.oracle
def test_lxml_text_benchmark_output(index: int, expected: str) -> None:
    etree: Final = pytest.importorskip("lxml.etree", exc_type=ImportError)
    root: Final = etree.fromstring(cast("str", INPUTS["parse-xml-text"]()[index][1]).encode())
    assert (root.tag, root.text, len(root)) == ("root", expected, 0)


@pytest.mark.parametrize(
    ("index", "expected"),
    [(0, "a" * 65536), (1, "a&b " * 8192), (2, "hello")],
    ids=["clean", "references", "tiny"],
)
@pytest.mark.oracle
def test_lxml_value_benchmark_output(index: int, expected: str) -> None:
    etree: Final = pytest.importorskip("lxml.etree", exc_type=ImportError)
    root: Final = etree.fromstring(cast("str", INPUTS["parse-xml-values"]()[index][1]).encode())
    assert (root.tag, root.text, list(root.attrib.items()), len(root)) == ("root", None, [("value", expected)], 0)
