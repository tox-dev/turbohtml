"""Branch-coverage edge cases for the XSD and RELAX NG engines, exercised through the public API."""

from __future__ import annotations

import sys

import pytest

from turbohtml import parse_xml
from turbohtml.validate import RelaxNG, XMLSchema

XS = 'xmlns:xs="http://www.w3.org/2001/XMLSchema"'
R = "http://relaxng.org/ns/structure/1.0"
DT = "http://www.w3.org/2001/XMLSchema-datatypes"


def xsd_ok(schema: str, xml: str) -> bool:
    return XMLSchema(schema).validate(parse_xml(xml)).valid


def rng_ok(schema: str, xml: str) -> bool:
    return RelaxNG(schema).validate(parse_xml(xml)).valid


def restricted(facets: str, base: str = "xs:string") -> str:
    return (
        f'<xs:schema {XS}><xs:element name="v"><xs:simpleType>'
        f'<xs:restriction base="{base}">{facets}</xs:restriction></xs:simpleType></xs:element></xs:schema>'
    )


def pattern_ok(pattern: str, value: str) -> bool:
    return xsd_ok(restricted(f'<xs:pattern value="{pattern}"/>'), f"<v>{value}</v>")


@pytest.mark.parametrize(
    ("type_name", "value", "ok"),
    [
        pytest.param("xs:weirdUnknownType", "anything", True, id="unknown-type-is-string"),
        pytest.param("xs:integer", "007", True, id="leading-zeros"),
        pytest.param("xs:decimal", "12", True, id="decimal-int-part"),
        pytest.param("xs:decimal", "0.5", True, id="decimal-frac"),
        pytest.param("xs:double", "12", True, id="double-plain"),
        pytest.param("xs:double", "1e5", True, id="double-exp-no-frac"),
        pytest.param("xs:double", "1.5", True, id="double-frac"),
        pytest.param("xs:double", "1e", False, id="double-exp-empty"),
        pytest.param("xs:double", ".", False, id="double-just-dot"),
        pytest.param("xs:date", "2020-06-15+05:30", True, id="date-tz-offset"),
        pytest.param("xs:date", "2020-06-15Z", True, id="date-tz-z"),
        pytest.param("xs:date", "2020-1a-15", False, id="date-nondigit"),
        pytest.param("xs:date", "2020-06-15+99:00", False, id="date-bad-tz"),
        pytest.param("xs:date", "2020-06-15+05", False, id="date-short-tz"),
        pytest.param("xs:dateTime", "2020-06-15T00:00:00", True, id="dateTime-no-tz"),
        pytest.param("xs:dateTime", "2020-06-15X00:00:00", False, id="dateTime-no-T"),
        pytest.param("xs:time", "12:00:00.", False, id="time-empty-frac"),
        pytest.param("xs:time", "12:00", False, id="time-short"),
        pytest.param("xs:duration", "P1Y", True, id="duration-year"),
        pytest.param("xs:duration", "PT1H", True, id="duration-time"),
        pytest.param("xs:duration", "P1YT30M", True, id="duration-date-time"),
        pytest.param("xs:duration", "P1X", False, id="duration-bad-unit"),
        pytest.param("xs:duration", "PT1HT2H", False, id="duration-double-t"),
        pytest.param("xs:duration", "P1H", False, id="duration-time-unit-outside-t"),
        pytest.param("xs:language", "en", True, id="language-single"),
        pytest.param("xs:language", "en-US-variant", True, id="language-multi"),
        pytest.param("xs:language", "en-", False, id="language-trailing-dash"),
        pytest.param("xs:hexBinary", "GG", False, id="hex-bad-char"),
        pytest.param("xs:base64Binary", "aGVs bG8=", True, id="base64-whitespace"),
        pytest.param("xs:base64Binary", "a b", False, id="base64-not-multiple"),
        pytest.param("xs:QName", "just", True, id="qname-ncname-only"),
        pytest.param("xs:Name", "-bad", False, id="name-bad-start"),
        pytest.param("xs:NCName", "a b", False, id="ncname-space"),
    ],
)
def test_datatype_edges(type_name: str, value: str, ok: bool) -> None:  # noqa: FBT001  # a pytest parametrize value, not a boolean-trap call site
    schema = f'<xs:schema {XS}><xs:element name="v" type="{type_name}"/></xs:schema>'
    assert xsd_ok(schema, f"<v>{value}</v>") is ok


@pytest.mark.parametrize(
    ("pattern", "value", "ok"),
    [
        pytest.param(r"\W+", "!!!", True, id="not-word"),
        pytest.param(r"\S+", "abc", True, id="not-space"),
        pytest.param(r"[\W]+", "!!", True, id="class-not-word"),
        pytest.param(r"a\nb", "a\nb", True, id="escaped-newline"),
        pytest.param(r"a\tb", "a\tb", True, id="escaped-tab"),
        pytest.param(r"[\.]+", "...", True, id="class-literal-escape"),
        pytest.param("[abc]+", "cba", True, id="class-multichar"),
        pytest.param("a{b", "a{b", True, id="brace-literal"),
        pytest.param("{x}", "{x}", True, id="lone-brace"),
        pytest.param("a{0}b", "b", True, id="repeat-zero"),
        pytest.param("a{0,2}", "aa", True, id="repeat-zero-two"),
        pytest.param("a{2,", "a{2,", True, id="unterminated-brace"),
        pytest.param("(a|)b", "b", True, id="empty-alternative"),
    ],
)
def test_regex_edges(pattern: str, value: str, ok: bool) -> None:  # noqa: FBT001  # a pytest parametrize value, not a boolean-trap call site
    assert pattern_ok(pattern, value) is ok


def test_enumeration_numeric_and_growth() -> None:
    facets = "".join(f'<xs:enumeration value="{n}"/>' for n in range(6))
    assert xsd_ok(restricted(facets, "xs:int"), "<v>3</v>")
    assert not xsd_ok(restricted(facets, "xs:int"), "<v>9</v>")


def test_multiple_patterns_growth() -> None:
    facets = "".join(f'<xs:pattern value="[a-z]{{{n}}}"/>' for n in range(1, 6))
    # every pattern must match; only a 1-char lowercase string satisfies length 1..5 simultaneously? no -- all lengths
    assert not xsd_ok(restricted(facets), "<v>abc</v>")


def test_length_exact_mismatch() -> None:
    assert not xsd_ok(restricted('<xs:length value="2"/>'), "<v>abcd</v>")


def test_group_ref_unknown() -> None:
    schema = (
        f'<xs:schema {XS}><xs:complexType name="t"><xs:group ref="nope"/></xs:complexType>'
        '<xs:element name="r" type="t"/></xs:schema>'
    )
    assert not xsd_ok(schema, "<r/>")  # the ref resolves to nothing, so the content cannot match


def test_many_elements_edecl_growth() -> None:
    parts = "".join(f'<xs:element name="e{n}" type="xs:string"/>' for n in range(12))
    body = "".join(f"<e{n}>x</e{n}>" for n in range(12))
    schema = (
        f'<xs:schema {XS}><xs:element name="r"><xs:complexType><xs:sequence>{parts}'
        f"</xs:sequence></xs:complexType></xs:element></xs:schema>"
    )
    assert xsd_ok(schema, f"<r>{body}</r>")


def test_schema_with_whitespace_and_comment() -> None:
    schema = (
        f"<xs:schema {XS}>\n  <!-- a comment -->\n"
        '  <xs:element name="r"><xs:complexType><xs:sequence>\n'
        '    <xs:element name="a" type="xs:string"/>\n'
        "  </xs:sequence></xs:complexType></xs:element>\n</xs:schema>"
    )
    assert xsd_ok(schema, "<r><a>x</a></r>")


def test_simple_type_list_no_restriction() -> None:
    schema = (
        f'<xs:schema {XS}><xs:element name="v"><xs:simpleType>'
        '<xs:list itemType="xs:int"/></xs:simpleType></xs:element></xs:schema>'
    )
    assert xsd_ok(schema, "<v>1 2 3</v>")


def test_restriction_nested_simpletype() -> None:
    schema = (
        f'<xs:schema {XS}><xs:element name="v"><xs:simpleType><xs:restriction>'
        '<xs:simpleType><xs:restriction base="xs:int"/></xs:simpleType>'
        '<xs:maxInclusive value="9"/>'
        "</xs:restriction></xs:simpleType></xs:element></xs:schema>"
    )
    assert xsd_ok(schema, "<v>5</v>")
    assert not xsd_ok(schema, "<v>50</v>")


def test_restriction_with_foreign_facet_child() -> None:
    schema = (
        f'<xs:schema {XS} xmlns:x="urn:x"><xs:element name="v"><xs:simpleType>'
        '<xs:restriction base="xs:string"><x:note>ignored</x:note>'
        '<xs:minLength value="2"/></xs:restriction></xs:simpleType></xs:element></xs:schema>'
    )
    assert xsd_ok(schema, "<v>ab</v>")
    assert not xsd_ok(schema, "<v>a</v>")


def test_named_simpletype_attribute() -> None:
    schema = (
        f"<xs:schema {XS}>"
        '<xs:simpleType name="ranged"><xs:restriction base="xs:int"><xs:maxInclusive value="5"/>'
        "</xs:restriction></xs:simpleType>"
        '<xs:element name="r"><xs:complexType>'
        '<xs:attribute name="a" type="ranged"/></xs:complexType></xs:element></xs:schema>'
    )
    assert xsd_ok(schema, '<r a="3"/>')
    assert not xsd_ok(schema, '<r a="9"/>')


def test_inline_simpletype_attribute() -> None:
    schema = (
        f'<xs:schema {XS}><xs:element name="r"><xs:complexType>'
        '<xs:attribute name="a"><xs:simpleType><xs:restriction base="xs:int">'
        '<xs:maxInclusive value="5"/></xs:restriction></xs:simpleType></xs:attribute>'
        "</xs:complexType></xs:element></xs:schema>"
    )
    assert xsd_ok(schema, '<r a="3"/>')
    assert not xsd_ok(schema, '<r a="9"/>')


def test_simple_content_restriction() -> None:
    schema = (
        f'<xs:schema {XS}><xs:element name="r"><xs:complexType><xs:simpleContent>'
        '<xs:restriction base="xs:int"/></xs:simpleContent></xs:complexType></xs:element></xs:schema>'
    )
    assert xsd_ok(schema, "<r>42</r>")
    assert not xsd_ok(schema, "<r>x</r>")


def test_instance_with_foreign_prefixed_attribute() -> None:
    schema = (
        f'<xs:schema {XS}><xs:element name="r"><xs:complexType>'
        '<xs:attribute name="a" type="xs:string"/></xs:complexType></xs:element></xs:schema>'
    )
    xml = '<r xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" a="v" xsi:nil="true"/>'
    assert xsd_ok(schema, xml)


def test_extension_without_own_particle() -> None:
    schema = (
        f"<xs:schema {XS}>"
        '<xs:complexType name="base"><xs:sequence><xs:element name="a" type="xs:string"/></xs:sequence>'
        "</xs:complexType>"
        '<xs:element name="r"><xs:complexType><xs:complexContent>'
        '<xs:extension base="base"><xs:attribute name="k" type="xs:string"/></xs:extension>'
        "</xs:complexContent></xs:complexType></xs:element></xs:schema>"
    )
    assert xsd_ok(schema, '<r k="v"><a>x</a></r>')


def test_simple_typed_element_with_child() -> None:
    schema = f'<xs:schema {XS}><xs:element name="v" type="xs:int"/></xs:schema>'
    assert not xsd_ok(schema, "<v><child/></v>")


def test_occurs_multi_digit_and_bounds() -> None:
    schema = (
        f'<xs:schema {XS}><xs:element name="r"><xs:complexType><xs:sequence>'
        '<xs:element name="a" type="xs:string" minOccurs="2" maxOccurs="3"/>'
        "</xs:sequence></xs:complexType></xs:element></xs:schema>"
    )
    assert xsd_ok(schema, "<r><a>1</a><a>2</a></r>")
    assert not xsd_ok(schema, "<r><a>1</a></r>")
    assert not xsd_ok(schema, "<r><a>1</a><a>2</a><a>3</a><a>4</a></r>")


# ---- RELAX NG edges ----


def rwrap(body: str) -> str:
    return f'<element name="doc" xmlns="{R}">{body}</element>'


def rgrammar(body: str, attrs: str = "") -> str:
    return f'<grammar xmlns="{R}" {attrs}>{body}</grammar>'


def test_rng_group_with_empty_second() -> None:
    assert rng_ok(rwrap("<group><text/><empty/></group>"), "<doc>x</doc>")


def test_rng_one_or_more_notallowed() -> None:
    assert not rng_ok(rwrap("<oneOrMore><notAllowed/></oneOrMore>"), "<doc/>")


def test_rng_nsname_inherits_scope_ns() -> None:
    schema = rgrammar('<start ns="urn:z"><element><nsName/><text/></element></start>')
    assert rng_ok(schema, '<a xmlns="urn:z">v</a>')  # nsName without ns attr inherits the in-scope ns
    assert not rng_ok(schema, "<a>v</a>")


def test_rng_attribute_with_nameclass_child() -> None:
    schema = rwrap("<attribute><name>id</name><text/></attribute><text/>")
    assert rng_ok(schema, '<doc id="1">x</doc>')
    assert not rng_ok(schema, "<doc>x</doc>")


def test_rng_data_ref_and_interleave_attrs() -> None:
    schema = rgrammar(
        '<start><element name="r"><ref name="attrs"/></element></start>'
        '<define name="attrs"><interleave>'
        '<attribute name="x"><text/></attribute>'
        '<attribute name="y"><text/></attribute>'
        "</interleave></define>",
    )
    assert rng_ok(schema, '<r x="1" y="2"/>')
    assert not rng_ok(schema, '<r x="1"/>')


def test_rng_attribute_one_or_more_and_fail() -> None:
    schema = rwrap('<oneOrMore><attribute name="a"><text/></attribute></oneOrMore><text/>')
    assert rng_ok(schema, '<doc a="1">x</doc>')
    assert not rng_ok(schema, "<doc>x</doc>")


def test_rng_recursive_attribute_ref() -> None:
    schema = rgrammar(
        '<start><element name="r"><ref name="content"/></element></start>'
        '<define name="content"><optional><attribute name="a"><text/></attribute></optional>'
        '<zeroOrMore><element name="r"><ref name="content"/></element></zeroOrMore></define>'
    )
    assert rng_ok(schema, '<r a="1"><r><r a="2"/></r></r>')


def test_rng_many_defines_growth() -> None:
    defines = "".join(f'<define name="d{n}"><element name="e{n}"><text/></element></define>' for n in range(12))
    refs = "".join(f'<ref name="d{n}"/>' for n in range(12))
    body = "".join(f"<e{n}>x</e{n}>" for n in range(12))
    schema = rgrammar(f'<start><element name="r"><group>{refs}</group></element></start>{defines}')
    assert rng_ok(schema, f"<r>{body}</r>")


def test_rng_start_requires_two_top_level() -> None:
    schema = rgrammar(
        '<start><group><element name="a"><text/></element><element name="b"><text/></element></group></start>'
    )
    result = RelaxNG(schema).validate(parse_xml("<a>x</a>"))
    assert not result.valid


def test_rng_value_with_whitespace_and_text_deriv_group() -> None:
    schema = rwrap("<group><value>hi</value></group>")
    assert rng_ok(schema, "<doc>hi</doc>")
    assert not rng_ok(schema, "<doc>bye</doc>")


def test_rng_mixed_choice_end_tag() -> None:
    schema = rwrap('<choice><element name="a"><text/></element><element name="b"><empty/></element></choice>')
    assert rng_ok(schema, "<doc><b/></doc>")


def test_validate_rejects_non_node() -> None:
    schema = XMLSchema(f'<xs:schema {XS}><xs:element name="v" type="xs:int"/></xs:schema>')
    with pytest.raises(TypeError):
        schema.validate("not a node")  # ty: ignore[invalid-argument-type]  # exercises the runtime type guard


@pytest.mark.parametrize("name", ["café", "中文", "𝔸bc"], ids=["two-byte", "three-byte", "four-byte"])  # noqa: RUF001
def test_error_path_non_ascii_names(name: str) -> None:
    schema = f'<xs:schema {XS}><xs:element name="{name}" type="xs:int"/></xs:schema>'
    result = XMLSchema(schema).validate(parse_xml(f"<{name}>bad</{name}>"))
    assert not result.valid
    assert result.errors[0].path == f"/{name}"


@pytest.mark.parametrize("name", ["café", "中文", "𝔸bc"], ids=["two-byte", "three-byte", "four-byte"])  # noqa: RUF001
def test_rng_non_ascii_attribute_name(name: str) -> None:
    schema = rwrap(f'<attribute name="{name}"><text/></attribute><text/>')
    assert rng_ok(schema, f'<doc {name}="1">x</doc>')


def test_rng_xml_prefixed_name() -> None:
    schema = f'<grammar xmlns="{R}"><start><element name="xml:space"><text/></element></start></grammar>'
    assert rng_ok(schema, "<xml:space>preserve</xml:space>")


def test_many_global_elements_growth() -> None:
    globals_ = "".join(f'<xs:element name="g{n}" type="xs:string"/>' for n in range(12))
    schema = f"<xs:schema {XS}>{globals_}</xs:schema>"
    assert xsd_ok(schema, "<g5>x</g5>")


def test_instance_comment_before_root() -> None:
    schema = f'<xs:schema {XS}><xs:element name="r" type="xs:string"/></xs:schema>'
    assert xsd_ok(schema, "<!-- lead --><r>x</r>")


@pytest.mark.parametrize(
    ("type_name", "value", "ok"),
    [
        pytest.param("xs:NMTOKEN", "", False, id="nmtoken-empty"),
        pytest.param("xs:NCName", "1abc", False, id="ncname-bad-start"),
        pytest.param("xs:Name", "a b", False, id="name-bad-middle"),
        pytest.param("xs:decimal", "12.50", True, id="decimal-both-parts"),
        pytest.param("xs:double", "1.5e3", True, id="double-frac-and-exp"),
        pytest.param("xs:time", "12:60:00", False, id="time-bad-minute"),
        pytest.param("xs:duration", "P", False, id="duration-nothing"),
        pytest.param("xs:language", "1", False, id="language-digit-start"),
        pytest.param("xs:hexBinary", "0", False, id="hex-single"),
        pytest.param("xs:base64Binary", "$$$$", False, id="base64-bad-char"),
    ],
)
def test_datatype_boundary_edges(type_name: str, value: str, ok: bool) -> None:  # noqa: FBT001  # a pytest parametrize value, not a boolean-trap call site
    schema = f'<xs:schema {XS}><xs:element name="v" type="{type_name}"/></xs:schema>'
    assert xsd_ok(schema, f"<v>{value}</v>") is ok


@pytest.mark.parametrize(
    ("type_name", "value"),
    [
        pytest.param("xs:int", "café", id="int-two-byte-value"),
        pytest.param("xs:int", "中文", id="int-three-byte-value"),
        pytest.param("xs:int", "𝔸", id="int-four-byte-value"),  # noqa: RUF001
    ],
)
def test_datatype_error_message_non_ascii_value(type_name: str, value: str) -> None:
    schema = f'<xs:schema {XS}><xs:element name="v" type="{type_name}"/></xs:schema>'
    result = XMLSchema(schema).validate(parse_xml(f"<v>{value}</v>"))
    assert not result.valid
    assert value in result.errors[0].message


@pytest.mark.parametrize(
    ("pattern", "value", "ok"),
    [
        pytest.param(r"x\r", "x", False, id="carriage-return-escape-compiled"),
        pytest.param(r"[(-\)]+", "()", True, id="class-range-escaped-hi"),
        pytest.param("(x)?y", "y", True, id="optional-group"),
    ],
)
def test_regex_more(pattern: str, value: str, ok: bool) -> None:  # noqa: FBT001  # a pytest parametrize value, not a boolean-trap call site
    assert pattern_ok(pattern, value) is ok


def test_rng_element_whitespace_before_nameclass() -> None:
    schema = f'<grammar xmlns="{R}"><start>\n  <element>\n    <anyName/>\n    <text/>\n  </element>\n</start></grammar>'
    assert rng_ok(schema, "<x>hi</x>")


def test_rng_nameclass_choice_with_whitespace() -> None:
    schema = rwrap("<element>\n  <choice>\n    <name>a</name>\n    <name>b</name>\n  </choice>\n  <text/>\n</element>")
    assert rng_ok(schema, "<doc><b>x</b></doc>")


def test_rng_nullable_ref_as_group_head() -> None:
    schema = rgrammar(
        '<start><element name="r"><group><ref name="opt"/>'
        '<element name="b"><text/></element></group></element></start>'
        '<define name="opt"><empty/></define>'
    )
    assert rng_ok(schema, "<r><b>x</b></r>")


def test_rng_nullable_ref_and_text_ref() -> None:
    schema = rgrammar(
        '<start><element name="r"><choice><ref name="a"/><ref name="b"/></choice></element></start>'
        '<define name="a"><group><ref name="empty"/><text/></group></define>'
        '<define name="b"><empty/></define>'
        '<define name="empty"><empty/></define>'
    )
    assert rng_ok(schema, "<r>text</r>")
    assert rng_ok(schema, "<r/>")


@pytest.mark.parametrize(
    ("type_name", "value", "ok"),
    [
        pytest.param("xs:double", "-1.5", True, id="double-neg-sign"),
        pytest.param("xs:double", "1.5e-3", True, id="double-signed-exp"),
        pytest.param("xs:date", "2020-06-15-05:00", True, id="date-neg-tz"),
        pytest.param("xs:dateTime", "2020-06-15T12:00:00-05:00", True, id="dateTime-neg-tz"),
        pytest.param("xs:duration", "P1DT2H", True, id="duration-day-and-time"),
    ],
)
def test_datatype_sign_and_tz(type_name: str, value: str, ok: bool) -> None:  # noqa: FBT001  # a pytest parametrize value, not a boolean-trap call site
    schema = f'<xs:schema {XS}><xs:element name="v" type="{type_name}"/></xs:schema>'
    assert xsd_ok(schema, f"<v>{value}</v>") is ok


def test_rng_empty_value_literal() -> None:
    schema = rwrap('<element name="e"><value></value></element>')
    assert not rng_ok(schema, "<doc><e>x</e></doc>")  # "x" != the empty value literal


def test_rng_whitespace_only_content() -> None:
    schema = rwrap('<optional><element name="x"><text/></element></optional>')
    assert rng_ok(schema, "<doc>   </doc>")


def test_rng_attribute_empty_value() -> None:
    schema = rwrap('<attribute name="a"><empty/></attribute><text/>')
    assert rng_ok(schema, '<doc a="">x</doc>')
    assert not rng_ok(schema, '<doc a="v">x</doc>')


def test_rng_group_with_text_and_ref_text_deriv() -> None:
    schema = rgrammar(
        '<start><element name="e"><group><ref name="t"/><empty/></group></element></start>'
        '<define name="t"><text/></define>'
    )
    assert rng_ok(schema, "<e>hello</e>")


def test_rng_choice_of_same_name_elements() -> None:
    schema = rwrap(
        "<group>"
        '<choice><element name="a"><text/></element><element name="a"><empty/></element></choice>'
        '<element name="b"><text/></element>'
        "</group>"
    )
    assert rng_ok(schema, "<doc><a>x</a><b>y</b></doc>")
    assert rng_ok(schema, "<doc><a/><b>y</b></doc>")


def test_rng_interleave_choice_branch() -> None:
    schema = rwrap(
        "<interleave>"
        '<choice><element name="a"><text/></element><element name="c"><text/></element></choice>'
        '<element name="b"><text/></element>'
        "</interleave>"
    )
    assert rng_ok(schema, "<doc><b>1</b><c>2</c></doc>")
    assert rng_ok(schema, "<doc><a>1</a><b>2</b></doc>")


@pytest.mark.parametrize(
    ("type_name", "value"),
    [
        pytest.param("xs:date", "2020-06-15Q", id="date-bad-tz-char"),
        pytest.param("xs:date", "202-06-15", id="date-short-year"),
        pytest.param("xs:duration", "1Y", id="duration-no-p"),
        pytest.param("xs:duration", "P1", id="duration-no-unit"),
        pytest.param("xs:language", "en-verylongsub", id="language-long-subtag"),
    ],
)
def test_datatype_more_invalid(type_name: str, value: str) -> None:
    schema = f'<xs:schema {XS}><xs:element name="v" type="{type_name}"/></xs:schema>'
    assert not xsd_ok(schema, f"<v>{value}</v>")


def test_xsd_empty_group_and_annotation() -> None:
    schema = (
        f"<xs:schema {XS}>"
        "<xs:annotation><xs:documentation>docs</xs:documentation></xs:annotation>"
        '<xs:group name="g"></xs:group>'
        '<xs:complexType name="t"><xs:group ref="g"/></xs:complexType>'
        '<xs:element name="r" type="t"/></xs:schema>'
    )
    assert not xsd_ok(schema, "<r><x/></r>")


def test_xsd_unknown_base_type() -> None:
    schema = (
        f'<xs:schema {XS}><xs:element name="v"><xs:simpleType>'
        '<xs:restriction base="madeUpType"><xs:minLength value="2"/></xs:restriction>'
        "</xs:simpleType></xs:element></xs:schema>"
    )
    assert xsd_ok(schema, "<v>ab</v>")
    assert not xsd_ok(schema, "<v>a</v>")


def test_xsd_attribute_ref_unknown() -> None:
    schema = (
        f'<xs:schema {XS}><xs:element name="r"><xs:complexType>'
        '<xs:attribute ref="nope"/></xs:complexType></xs:element></xs:schema>'
    )
    assert xsd_ok(schema, "<r/>")


def test_xsd_empty_complex_content() -> None:
    schema = (
        f'<xs:schema {XS}><xs:element name="r"><xs:complexType>'
        "<xs:complexContent></xs:complexContent></xs:complexType></xs:element></xs:schema>"
    )
    assert xsd_ok(schema, "<r/>")


def test_xsd_choice_and_all_with_whitespace() -> None:
    choice = (
        f'<xs:schema {XS}><xs:element name="r"><xs:complexType><xs:choice>\n'
        '  <xs:element name="a" type="xs:string"/>\n'
        '  <xs:element name="b" type="xs:string"/>\n'
        "</xs:choice></xs:complexType></xs:element></xs:schema>"
    )
    assert xsd_ok(choice, "<r><a>x</a></r>")
    grouped = (
        f'<xs:schema {XS}><xs:element name="r"><xs:complexType><xs:all>\n'
        '  <xs:element name="x" type="xs:string"/>\n'
        "</xs:all></xs:complexType></xs:element></xs:schema>"
    )
    assert xsd_ok(grouped, "<r><x>1</x></r>")


def test_xsd_restriction_with_whitespace_facets() -> None:
    schema = (
        f'<xs:schema {XS}><xs:element name="v"><xs:simpleType>\n'
        '  <xs:restriction base="xs:string">\n'
        '    <xs:minLength value="2"/>\n'
        "  </xs:restriction>\n"
        "</xs:simpleType></xs:element></xs:schema>"
    )
    assert not xsd_ok(schema, "<v>a</v>")


def test_xsd_nullable_repeated_particle() -> None:
    schema = (
        f'<xs:schema {XS}><xs:element name="r"><xs:complexType>'
        '<xs:sequence minOccurs="0" maxOccurs="unbounded">'
        '<xs:element name="a" type="xs:string" minOccurs="0"/>'
        "</xs:sequence></xs:complexType></xs:element></xs:schema>"
    )
    assert xsd_ok(schema, "<r><a>1</a></r>")
    assert xsd_ok(schema, "<r/>")


def test_rng_group_optional_head() -> None:
    schema = rwrap(
        '<group><optional><element name="x"><text/></element></optional><element name="b"><text/></element></group>'
    )
    assert rng_ok(schema, "<doc><b>y</b></doc>")
    assert rng_ok(schema, "<doc><x>1</x><b>y</b></doc>")


def test_rng_group_value_then_text() -> None:
    schema = rwrap('<element name="e"><group><value>a</value><text/></group></element>')
    assert rng_ok(schema, "<doc><e>a</e></doc>")
    assert not rng_ok(schema, "<doc><e>b</e></doc>")


def test_rng_interleave_with_choice_and_text() -> None:
    schema = rwrap(
        "<interleave>"
        "<text/>"
        '<choice><element name="a"><text/></element><element name="b"><empty/></element></choice>'
        "</interleave>"
    )
    assert rng_ok(schema, "<doc>lead <b/> tail</doc>")
    assert rng_ok(schema, "<doc><a>x</a></doc>")


def test_rng_deep_recursive_mixed_content() -> None:
    schema = rgrammar(
        '<start><ref name="section"/></start>'
        '<define name="section"><element name="section">'
        '<optional><attribute name="id"><text/></attribute></optional>'
        '<interleave><text/><zeroOrMore><ref name="section"/></zeroOrMore></interleave>'
        "</element></define>"
    )
    doc = '<section id="a">intro <section>nested <section/></section> outro</section>'
    assert rng_ok(schema, doc)


def test_language_bad_separator() -> None:
    schema = f'<xs:schema {XS}><xs:element name="v" type="xs:language"/></xs:schema>'
    assert not xsd_ok(schema, "<v>en_us</v>")


def test_regex_star_loop_revisit() -> None:
    assert pattern_ok("(ab)*", "ababab")
    assert pattern_ok("(a*)*", "aaaa")  # nested stars converge two epsilon paths onto one state


def test_rng_self_closed_value() -> None:
    assert not rng_ok(rwrap('<element name="e"><value/></element>'), "<doc><e>x</e></doc>")


def test_rng_attribute_nameclass_with_whitespace() -> None:
    schema = rwrap("<attribute>\n  <name>id</name>\n</attribute><text/>")
    assert rng_ok(schema, '<doc id="1">x</doc>')


def test_rng_text_deriv_ref_and_nullable_ref() -> None:
    schema = rgrammar(
        '<start><element name="e"><interleave><ref name="t"/><ref name="opt"/></interleave></element></start>'
        '<define name="t"><text/></define>'
        '<define name="opt"><optional><element name="x"><empty/></element></optional></define>'
    )
    assert rng_ok(schema, "<e>words</e>")
    assert rng_ok(schema, "<e>words<x/></e>")


def test_rng_unknown_pattern_element() -> None:
    schema = f'<grammar xmlns="{R}"><start><externalRef href="other.rng"/></start></grammar>'
    assert not rng_ok(schema, "<anything/>")


def test_rng_nameless_define_skipped() -> None:
    schema = rgrammar('<start><element name="r"><text/></element></start><define><empty/></define>')
    assert rng_ok(schema, "<r>x</r>")


def test_rng_text_deriv_group_nullable_first() -> None:
    schema = rwrap('<element name="e"><group><text/><value>a</value></group></element>')
    assert rng_ok(schema, "<doc><e>a</e></doc>")


def test_rng_interleave_choice_same_name() -> None:
    schema = rwrap(
        "<interleave>"
        '<choice><element name="a"><text/></element><element name="a"><empty/></element></choice>'
        '<element name="b"><text/></element>'
        "</interleave>"
    )
    assert rng_ok(schema, "<doc><a>x</a><b>y</b></doc>")
    assert rng_ok(schema, "<doc><b>y</b><a/></doc>")


def test_rng_forbidden_attribute_recursion_is_guarded() -> None:
    # left-recursion through an attribute is forbidden by RELAX NG; the engine must not
    # loop forever on it -- it treats the recursive branch as unmatchable.
    schema = rgrammar(
        '<start><element name="r"><ref name="x"/></element></start>'
        '<define name="x"><choice><empty/>'
        '<group><attribute name="a"><text/></attribute><ref name="x"/></group>'
        "</choice></define>"
    )
    assert RelaxNG(schema).validate(parse_xml('<r a="1"/>')).valid


def test_rng_forbidden_text_recursion_is_guarded() -> None:
    schema = rgrammar(
        '<start><element name="r"><ref name="x"/></element></start>'
        '<define name="x"><choice><empty/><group><ref name="x"/><text/></group></choice></define>'
    )
    assert RelaxNG(schema).validate(parse_xml("<r>hi</r>")).valid


@pytest.mark.parametrize(
    ("type_name", "value", "ok"),
    [
        pytest.param("xs:integer", "+5", True, id="integer-plus"),
        pytest.param("xs:decimal", "+1.5", True, id="decimal-plus"),
        pytest.param("xs:decimal", "12x", False, id="decimal-trailing"),
        pytest.param("xs:decimal", ".", False, id="decimal-just-dot"),
        pytest.param("xs:double", "+2", True, id="double-plus"),
        pytest.param("xs:double", "1E5", True, id="double-cap-e"),
        pytest.param("xs:double", "1e+5", True, id="double-exp-plus"),
        pytest.param("xs:date", "2020-00-15", False, id="date-month-zero"),
        pytest.param("xs:date", "2020x06-15", False, id="date-year-sep"),
        pytest.param("xs:date", "2020-06x15", False, id="date-day-sep"),
        pytest.param("xs:date", "2020-06", False, id="date-truncated"),
        pytest.param("xs:date", "2020-06-15+05x30", False, id="date-tz-colon"),
        pytest.param("xs:time", "12x00:00", False, id="time-sep1"),
        pytest.param("xs:time", "12:00x00", False, id="time-sep2"),
        pytest.param("xs:time", "12:60:00", False, id="time-minute"),
        pytest.param("xs:dateTime", "2020-06-15", False, id="dateTime-no-time"),
        pytest.param("xs:dateTime", "2020-06-15T99:00:00", False, id="dateTime-bad-time"),
        pytest.param("xs:dateTime", "2020-06-15T12:00:00+9", False, id="dateTime-bad-tz"),
        pytest.param("xs:duration", "-P1Y", True, id="duration-negative"),
        pytest.param("xs:duration", "PY", False, id="duration-no-digits"),
        pytest.param("xs:hexBinary", "0g", False, id="hex-past-f"),
        pytest.param("xs:base64Binary", "ab+/", True, id="base64-plus-slash"),
        pytest.param("xs:base64Binary", "AB=C", False, id="base64-char-after-pad"),
    ],
)
def test_datatype_condition_boundaries(type_name: str, value: str, ok: bool) -> None:  # noqa: FBT001  # parametrize value
    schema = f'<xs:schema {XS}><xs:element name="v" type="{type_name}"/></xs:schema>'
    assert xsd_ok(schema, f"<v>{value}</v>") is ok


def test_ncname_char_past_z() -> None:
    schema = f'<xs:schema {XS}><xs:element name="v" type="xs:NCName"/></xs:schema>'
    assert not xsd_ok(schema, "<v>a{b</v>")


@pytest.mark.parametrize(
    ("facets", "base", "value", "ok"),
    [
        pytest.param('<xs:whiteSpace value="replace"/>', "xs:string", "a\tb", True, id="whitespace-replace"),
        pytest.param('<xs:whiteSpace value="preserve"/>', "xs:string", "a b", True, id="whitespace-preserve"),
        pytest.param('<xs:maxLength value="4"/>', "xs:string", "ab", True, id="maxLength-pass"),
        pytest.param('<xs:minExclusive value="0"/>', "xs:int", "5", True, id="minExclusive-pass"),
        pytest.param('<xs:maxExclusive value="10"/>', "xs:int", "5", True, id="maxExclusive-pass"),
        pytest.param('<xs:totalDigits value="4"/>', "xs:decimal", "-1.2", True, id="totalDigits-signed-pass"),
        pytest.param('<xs:fractionDigits value="2"/>', "xs:decimal", "-1.2", True, id="fractionDigits-signed"),
        pytest.param('<xs:whatever value="x"/>', "xs:string", "anything", True, id="unknown-facet-ignored"),
    ],
)
def test_facet_condition_boundaries(facets: str, base: str, value: str, ok: bool) -> None:  # noqa: FBT001  # parametrize
    assert xsd_ok(restricted(facets, base), f"<v>{value}</v>") is ok


@pytest.mark.parametrize(
    ("type_name", "value", "ok"),
    [
        pytest.param("xs:boolean", "false", True, id="boolean-false"),
        pytest.param("xs:boolean", "1", True, id="boolean-one"),
        pytest.param("xs:NCName", "", False, id="ncname-empty"),
        pytest.param("xs:Name", "", False, id="name-empty"),
        pytest.param("xs:Name", "a:b", True, id="name-with-colon"),
        pytest.param("xs:QName", "1a:b", False, id="qname-bad-prefix"),
        pytest.param("xs:QName", "a:1b", False, id="qname-bad-local"),
        pytest.param("xs:integer", "-0", True, id="integer-neg-zero"),
    ],
)
def test_datatype_name_boundaries(type_name: str, value: str, ok: bool) -> None:  # noqa: FBT001  # parametrize value
    schema = f'<xs:schema {XS}><xs:element name="v" type="{type_name}"/></xs:schema>'
    assert xsd_ok(schema, f"<v>{value}</v>") is ok


@pytest.mark.parametrize(
    ("pattern", "value", "ok"),
    [
        pytest.param("[", "x", False, id="class-open-only"),
        pytest.param("[abc", "a", True, id="class-unterminated"),
        pytest.param("[a-]", "-", True, id="dash-before-close"),
        pytest.param("(ab", "ab", True, id="group-unterminated"),
        pytest.param(r"a\\", "a", False, id="backslash-at-end"),
        pytest.param("a{2}", "aa", True, id="bound-exact-close"),
        pytest.param("a{2,3}", "aaa", True, id="bound-comma-upper"),
        pytest.param("a{2,}", "aaaa", True, id="bound-comma-open"),
        pytest.param("a{2", "a{2", True, id="bound-unterminated"),
        pytest.param(r"[\d]+", "12", True, id="class-digit-builtin"),
        pytest.param(r"[\w]+", "ab_1", True, id="class-word-builtin"),
        pytest.param(r"[\s]+", " \t", True, id="class-space-builtin"),
        pytest.param(r"[\D]+", "abc", True, id="class-not-digit-builtin"),
        pytest.param(r"[\S]+", "abc", True, id="class-not-space-builtin"),
        pytest.param(r"\w+", "a-b", False, id="word-excludes-dash"),
    ],
)
def test_regex_parser_boundaries(pattern: str, value: str, ok: bool) -> None:  # noqa: FBT001  # parametrize value
    assert pattern_ok(pattern, value) is ok


@pytest.mark.parametrize(
    ("type_name", "value"),
    [
        pytest.param("xs:decimal", "", id="decimal-empty"),
        pytest.param("xs:double", "", id="double-empty"),
        pytest.param("xs:double", "12x", id="double-trailing"),
        pytest.param("xs:date", "", id="date-empty"),
        pytest.param("xs:date", "abcd-06-15", id="date-nondigit-year"),
        pytest.param("xs:dateTime", "", id="dateTime-empty"),
        pytest.param("xs:time", "12:00:99", id="time-second-high"),
        pytest.param("xs:duration", "", id="duration-empty"),
        pytest.param("xs:duration", "X", id="duration-no-p-char"),
        pytest.param("xs:integer", "", id="integer-empty"),
        pytest.param("xs:hexBinary", "xy", id="hex-nonhex"),
    ],
)
def test_datatype_empty_and_garbage(type_name: str, value: str) -> None:
    schema = f'<xs:schema {XS}><xs:element name="v" type="{type_name}"/></xs:schema>'
    assert not xsd_ok(schema, f"<v>{value}</v>")


@pytest.mark.parametrize(
    ("type_name", "value"),
    [
        pytest.param("xs:unsignedLong", "5", id="unsignedLong-ok"),
        pytest.param("xs:unsignedInt", "5", id="unsignedInt-ok"),
        pytest.param("xs:unsignedShort", "5", id="unsignedShort-ok"),
        pytest.param("xs:unsignedByte", "5", id="unsignedByte-ok"),
        pytest.param("xs:nonPositiveInteger", "-3", id="nonPositive-ok"),
        pytest.param("xs:negativeInteger", "-3", id="negative-ok"),
        pytest.param("xs:hexBinary", "abcdef", id="hex-lower"),
        pytest.param("xs:hexBinary", "ABCDEF", id="hex-upper"),
    ],
)
def test_datatype_all_subtypes_valid(type_name: str, value: str) -> None:
    schema = f'<xs:schema {XS}><xs:element name="v" type="{type_name}"/></xs:schema>'
    assert xsd_ok(schema, f"<v>{value}</v>")


@pytest.mark.parametrize(
    ("pattern", "value", "ok"),
    [
        pytest.param(r"[a\]", "a", True, id="class-backslash-end"),
        pytest.param(r"[a-\]", "z", False, id="class-range-backslash-end"),
        pytest.param("a{2,3", "a{2,3", True, id="bound-no-close"),
        pytest.param(r"a\d", "a5", True, id="atom-escape-not-end"),
        pytest.param(r"[\s]x", " x", True, id="class-space-then"),
        pytest.param(r"[\D]", "5", False, id="class-not-digit-vs-digit"),
        pytest.param(r"[\W]", "a", False, id="class-not-word-vs-word"),
        pytest.param(r"[\S]", " ", False, id="class-not-space-vs-space"),
    ],
)
def test_regex_more_boundaries(pattern: str, value: str, ok: bool) -> None:  # noqa: FBT001  # parametrize value
    assert pattern_ok(pattern, value) is ok


def test_cdata_and_char_ref_whitespace() -> None:
    schema = f'<xs:schema {XS}><xs:element name="v" type="xs:int"/></xs:schema>'
    assert xsd_ok(schema, "<v><![CDATA[42]]></v>")  # CDATA character-data child
    assert xsd_ok(schema, "<v>&#xD;42&#xA;</v>")  # carriage return + newline whitespace around the value


def test_non_xml_three_char_prefix() -> None:
    schema = (
        f'<xs:schema {XS} targetNamespace="urn:y" xmlns="urn:y" elementFormDefault="qualified">'
        '<xs:element name="r" type="xs:string"/></xs:schema>'
    )
    assert xsd_ok(schema, '<abc:r xmlns:abc="urn:y">x</abc:r>')
    assert not xsd_ok(schema, '<abc:r xmlns:abc="urn:other">x</abc:r>')  # prefix bound to the wrong namespace


def test_large_text_value_arena_block() -> None:
    schema = f'<xs:schema {XS}><xs:element name="v" type="xs:string"/></xs:schema>'
    assert xsd_ok(schema, f"<v>{'a' * 5000}</v>")  # forces a single arena allocation past the 4 kB block size


def test_element_ref_to_unknown_global() -> None:
    schema = (
        f'<xs:schema {XS}><xs:element name="r"><xs:complexType><xs:sequence>'
        '<xs:element ref="missing"/></xs:sequence></xs:complexType></xs:element></xs:schema>'
    )
    assert xsd_ok(schema, "<r><missing/></r>")  # the ref supplies the name; the missing decl validates as anyType


def test_complex_content_restriction() -> None:
    schema = (
        f"<xs:schema {XS}>"
        '<xs:complexType name="base"><xs:sequence><xs:element name="a" type="xs:string"/></xs:sequence>'
        "</xs:complexType>"
        '<xs:element name="r"><xs:complexType><xs:complexContent>'
        '<xs:restriction base="base"><xs:sequence><xs:element name="a" type="xs:string"/></xs:sequence>'
        "</xs:restriction></xs:complexContent></xs:complexType></xs:element></xs:schema>"
    )
    assert xsd_ok(schema, "<r><a>x</a></r>")


def test_attribute_form_default_qualified() -> None:
    schema = (
        f'<xs:schema {XS} attributeFormDefault="qualified"><xs:element name="r"><xs:complexType>'
        '<xs:attribute name="id" type="xs:string"/></xs:complexType></xs:element></xs:schema>'
    )
    assert xsd_ok(schema, '<r id="1"/>')


def test_global_notation_is_ignored() -> None:
    schema = (
        f"<xs:schema {XS}>"
        '<xs:notation name="jpeg" public="image/jpeg"/>'
        '<xs:element name="r" type="xs:string"/></xs:schema>'
    )
    assert xsd_ok(schema, "<r>x</r>")


def test_attribute_group_ref_unknown() -> None:
    schema = (
        f'<xs:schema {XS}><xs:element name="r"><xs:complexType>'
        '<xs:attributeGroup ref="missing"/></xs:complexType></xs:element></xs:schema>'
    )
    assert xsd_ok(schema, "<r/>")


def test_empty_attribute_value() -> None:
    schema = (
        f'<xs:schema {XS}><xs:element name="r"><xs:complexType>'
        '<xs:attribute name="a" type="xs:string"/></xs:complexType></xs:element></xs:schema>'
    )
    assert xsd_ok(schema, '<r a=""/>')


def test_self_referential_simple_type_terminates() -> None:
    schema = (
        f"<xs:schema {XS}>"
        '<xs:simpleType name="loop"><xs:restriction base="loop"><xs:minLength value="1"/></xs:restriction>'
        "</xs:simpleType>"
        '<xs:element name="v" type="loop"/></xs:schema>'
    )
    assert xsd_ok(schema, "<v>x</v>")


def test_no_namespace_facet_child() -> None:
    schema = (
        f'<xs:schema {XS}><xs:element name="v"><xs:simpleType>'
        '<xs:restriction base="xs:string"><note>plain</note>'
        '<xs:minLength value="2"/></xs:restriction></xs:simpleType></xs:element></xs:schema>'
    )
    assert xsd_ok(schema, "<v>ab</v>")
    assert not xsd_ok(schema, "<v>a</v>")


def test_unbound_prefix_is_no_namespace() -> None:
    schema = (
        f'<xs:schema {XS} targetNamespace="urn:y" xmlns="urn:y" elementFormDefault="qualified">'
        '<xs:element name="r" type="xs:string"/></xs:schema>'
    )
    # xyz is checked before abc in resolve_ns, so the prefix compare rejects xyz then matches abc
    assert xsd_ok(schema, '<abc:r xmlns:xyz="urn:z" xmlns:abc="urn:y">x</abc:r>')


def test_schema_with_foreign_no_namespace_child() -> None:
    schema = (
        f'<xs:schema {XS}><xs:element name="r"><xs:complexType><appinfo>note</appinfo>'
        '<xs:sequence><xs:element name="a" type="xs:string"/></xs:sequence>'
        "</xs:complexType></xs:element></xs:schema>"
    )
    assert xsd_ok(schema, "<r><a>x</a></r>")


def test_complex_content_mixed() -> None:
    schema = (
        f"<xs:schema {XS}>"
        '<xs:complexType name="base"><xs:sequence><xs:element name="a" type="xs:string" minOccurs="0"/>'
        "</xs:sequence></xs:complexType>"
        '<xs:element name="r"><xs:complexType><xs:complexContent mixed="true">'
        '<xs:extension base="base"/></xs:complexContent></xs:complexType></xs:element></xs:schema>"'.rstrip('"')
    )
    assert xsd_ok(schema, "<r>text <a>x</a> more</r>")


def test_simple_content_restriction_no_base() -> None:
    schema = (
        f'<xs:schema {XS}><xs:element name="r"><xs:complexType><xs:simpleContent>'
        '<xs:extension base="xs:string"/></xs:simpleContent></xs:complexType></xs:element></xs:schema>'
    )
    assert xsd_ok(schema, "<r>anything</r>")


def test_rng_cdata_and_prefixed_attribute() -> None:
    schema = rwrap('<attribute name="p:id" xmlns:p="urn:a"><text/></attribute><text/>')
    assert rng_ok(schema, '<doc xmlns:p="urn:a" p:id="1"><![CDATA[body]]></doc>')


def test_rng_data_param_without_name() -> None:
    schema = (
        f'<grammar xmlns="{R}" datatypeLibrary="{DT}"><start>'
        '<element name="e"><data type="string"><param>ignored</param></data></element></start></grammar>'
    )
    assert rng_ok(schema, "<e>hello</e>")


@pytest.mark.skipif(
    sys.implementation.name == "pypy",
    reason="RPython's own stack check trips on the C validator's recursion before its depth cap can "
    "report, raising SystemError instead; the recursion stays bounded either way",
)
def test_xsd_deep_nesting_is_bounded() -> None:
    schema = XMLSchema(
        f'<xs:schema {XS}><xs:element name="a"><xs:complexType><xs:sequence>'
        '<xs:element ref="a" minOccurs="0"/></xs:sequence></xs:complexType></xs:element></xs:schema>'
    )
    assert schema.validate(parse_xml("<a>" * 20 + "</a>" * 20)).valid
    result = schema.validate(parse_xml("<a>" * 1100 + "</a>" * 1100))
    assert not result.valid
    assert "nesting depth" in result.errors[0].message


def test_rng_deep_nesting_is_bounded() -> None:
    schema = RelaxNG(
        f'<grammar xmlns="{R}"><start><ref name="a"/></start>'
        '<define name="a"><element name="a"><optional><ref name="a"/></optional></element></define></grammar>'
    )
    assert schema.validate(parse_xml("<a>" * 20 + "</a>" * 20)).valid
    result = schema.validate(parse_xml("<a>" * 1100 + "</a>" * 1100))
    assert not result.valid
    assert "nesting depth" in result.errors[0].message


def test_long_names_and_values_hit_buffer_bounds() -> None:
    # a >256-char attribute name exercises the decode-buffer cap
    long_attr = "x" * 300
    rng_schema = rwrap(f'<attribute name="{long_attr}"><text/></attribute><text/>')
    assert rng_ok(rng_schema, f'<doc {long_attr}="1">body</doc>')
    # a >128-char value truncates in the datatype error message buffer
    xsd_schema = f'<xs:schema {XS}><xs:element name="v" type="xs:int"/></xs:schema>'
    assert not xsd_ok(xsd_schema, "<v>" + "9" * 200 + "z</v>")


def test_long_numeric_value_for_digit_facet() -> None:
    schema = (
        f'<xs:schema {XS}><xs:element name="v"><xs:simpleType>'
        '<xs:restriction base="xs:decimal"><xs:totalDigits value="5"/></xs:restriction>'
        "</xs:simpleType></xs:element></xs:schema>"
    )
    assert not xsd_ok(schema, "<v>" + "1" * 70 + "</v>")


@pytest.mark.parametrize(
    ("type_name", "value", "ok"),
    [
        pytest.param("xs:hexBinary", "AF", True, id="hex-upper-af"),
        pytest.param("xs:hexBinary", "FG", False, id="hex-g-past-f"),
        pytest.param("xs:language", "en-a1b2", True, id="language-alnum-subtag"),
    ],
)
def test_more_datatype_ranges(type_name: str, value: str, ok: bool) -> None:  # noqa: FBT001  # parametrize value
    schema = f'<xs:schema {XS}><xs:element name="v" type="{type_name}"/></xs:schema>'
    assert xsd_ok(schema, f"<v>{value}</v>") is ok


def test_regex_dot_excludes_carriage_return() -> None:
    schema = restricted('<xs:pattern value="."/>')
    assert not xsd_ok(schema, "<v>&#xD;</v>")  # . matches any char except newline and carriage return


def test_form_defaults_unqualified_and_mixed_false() -> None:
    schema = (
        f'<xs:schema {XS} elementFormDefault="unqualified" attributeFormDefault="unqualified">'
        '<xs:element name="r"><xs:complexType mixed="false"><xs:sequence>'
        '<xs:element name="a" type="xs:string"/></xs:sequence>'
        '<xs:attribute name="k" type="xs:string"/></xs:complexType></xs:element></xs:schema>'
    )
    assert xsd_ok(schema, '<r k="v"><a>x</a></r>')
    assert not xsd_ok(schema, "<r>text<a>x</a></r>")  # not mixed, so stray text is invalid


def test_base_and_element_type_in_target_namespace_prefix() -> None:
    schema = (
        f'<xs:schema {XS} targetNamespace="urn:t" xmlns:t="urn:t" xmlns="urn:t" elementFormDefault="qualified">'
        '<xs:simpleType name="small"><xs:restriction base="xs:int"><xs:maxInclusive value="9"/>'
        "</xs:restriction></xs:simpleType>"
        '<xs:simpleType name="smaller"><xs:restriction base="t:small"><xs:maxInclusive value="5"/>'
        "</xs:restriction></xs:simpleType>"
        '<xs:element name="v" type="t:smaller"/></xs:schema>'
    )
    assert xsd_ok(schema, '<v xmlns="urn:t">3</v>')
    assert not xsd_ok(schema, '<v xmlns="urn:t">7</v>')


def test_instance_attribute_starting_xmlns_but_not_declaration() -> None:
    schema = (
        f'<xs:schema {XS}><xs:element name="r"><xs:complexType>'
        '<xs:attribute name="xmlnsish" type="xs:string"/></xs:complexType></xs:element></xs:schema>'
    )
    assert xsd_ok(schema, '<r xmlnsish="v"/>')


def test_group_with_annotation_before_model() -> None:
    schema = (
        f"<xs:schema {XS}>"
        '<xs:group name="g"><xs:annotation><xs:documentation>doc</xs:documentation></xs:annotation>'
        '<xs:sequence><xs:element name="a" type="xs:string"/></xs:sequence></xs:group>'
        '<xs:complexType name="t"><xs:group ref="g"/></xs:complexType>'
        '<xs:element name="r" type="t"/></xs:schema>'
    )
    assert xsd_ok(schema, "<r><a>x</a></r>")


@pytest.mark.parametrize(
    ("pattern", "value", "ok"),
    [
        pytest.param("[a\\", "a", True, id="class-lone-backslash-at-end"),
        pytest.param("[a-\\", "a", False, id="range-backslash-at-end"),
        pytest.param("a\\", "a", False, id="atom-backslash-at-end"),
        pytest.param("a{2,x}", "a{2,x}", True, id="bound-nondigit-upper"),
        pytest.param("a{2x", "a{2x", True, id="bound-no-close-brace"),
        pytest.param("[\\s]", "x", False, id="class-space-vs-nonspace"),
    ],
)
def test_regex_parser_edge_boundaries(pattern: str, value: str, ok: bool) -> None:  # noqa: FBT001  # parametrize value
    assert pattern_ok(pattern, value) is ok


def test_rng_default_library_string_and_unknown_xsd_type() -> None:
    # default datatype library: "string" (not token) exercises the DT_STRING branch
    assert rng_ok(rwrap('<element name="s"><data type="string"/></element>'), "<doc><s>x</s></doc>")
    # an explicit non-XSD datatype library falls back to the built-in string/token library
    assert rng_ok(
        f'<element name="s" xmlns="{R}" datatypeLibrary=""><data type="token"/></element>',
        "<s>x</s>",
    )
    # the XSD library with an unrecognized type name falls back to string
    assert rng_ok(
        f'<element name="s" xmlns="{R}" datatypeLibrary="{DT}"><data type="madeUp"/></element>',
        "<s>anything</s>",
    )


def test_rng_element_without_name_class_matches_any() -> None:
    # a malformed element with neither @name nor a name-class child recovers as anyName
    schema = f'<grammar xmlns="{R}"><start><element/></start></grammar>'
    assert rng_ok(schema, "<whatever/>")  # no @name and no name-class child -> matches any empty element


def test_rng_empty_name_class_choice_matches_any() -> None:
    schema = rwrap("<oneOrMore><element><choice></choice><text/></element></oneOrMore>")
    assert rng_ok(schema, "<doc><a>1</a><b>2</b></doc>")  # an empty name-class choice matches any name


def test_rng_whitespace_between_pattern_children() -> None:
    choice = rwrap('<choice>\n  <element name="a"><text/></element>\n  <element name="b"><text/></element>\n</choice>')
    assert rng_ok(choice, "<doc><b>x</b></doc>")
    interleave = rwrap(
        '<interleave>\n  <element name="a"><text/></element>\n  <element name="b"><text/></element>\n</interleave>'
    )
    assert rng_ok(interleave, "<doc><b>1</b><a>2</a></doc>")


def test_rng_data_with_whitespace_and_nonparam_child() -> None:
    schema = (
        f'<grammar xmlns="{R}" datatypeLibrary="{DT}"><start>'
        '<element name="s"><data type="string">\n  <param name="minLength">2</param>\n</data></element>'
        "</start></grammar>"
    )
    assert rng_ok(schema, "<s>ab</s>")
    assert not rng_ok(schema, "<s>a</s>")


def test_rng_multiple_defines_dedup_and_combine() -> None:
    schema = (
        f'<grammar xmlns="{R}"><start><element name="r"><ref name="x"/></element></start>'
        '<define name="x"><element name="a"><text/></element></define>'
        '<define name="x" combine="choice"><element name="b"><text/></element></define>'
        '<define name="y"><empty/></define></grammar>'
    )
    assert rng_ok(schema, "<r><a>1</a></r>")
    assert rng_ok(schema, "<r><b>2</b></r>")


def test_schema_with_unnamespaced_sequence_is_not_recognized() -> None:
    # a <sequence> with no namespace is not the XSD content model, so the type has empty content
    schema = (
        f"<xs:schema {XS}>"
        '<xs:element name="r"><xs:complexType><sequence>'
        '<xs:element name="a" type="xs:string"/></sequence></xs:complexType></xs:element></xs:schema>'
    )
    assert xsd_ok(schema, "<r/>")
    assert not xsd_ok(schema, "<r><a>x</a></r>")


def test_simple_typed_element_with_text_and_element_child() -> None:
    schema = f'<xs:schema {XS}><xs:element name="v" type="xs:string"/></xs:schema>'
    assert not xsd_ok(schema, "<v>text<child/>more</v>")


def test_rng_mixed_text_and_element_children() -> None:
    schema = rwrap('<mixed><oneOrMore><element name="b"><text/></element></oneOrMore></mixed>')
    assert rng_ok(schema, "<doc>lead <b>x</b> mid <b>y</b> tail</doc>")


def test_rng_name_choice_matches_either() -> None:
    schema = rwrap("<oneOrMore><element><choice><name>a</name><name>b</name></choice><text/></element></oneOrMore>")
    assert rng_ok(schema, "<doc><a>1</a><b>2</b></doc>")
    assert not rng_ok(schema, "<doc><c>3</c></doc>")


def test_rng_nsname_except() -> None:
    schema = rwrap(
        '<oneOrMore><element><nsName ns="urn:k"><except><nsName ns="urn:skip"/></except></nsName>'
        "<text/></element></oneOrMore>"
    )
    assert rng_ok(schema, '<doc xmlns:p="urn:k"><p:a>1</p:a></doc>')
    assert not rng_ok(schema, '<doc xmlns:p="urn:other"><p:a>1</p:a></doc>')


def test_rng_list_with_surrounding_whitespace() -> None:
    schema = (
        f'<grammar xmlns="{R}" datatypeLibrary="{DT}"><start>'
        '<element name="nums"><list><oneOrMore><data type="int"/></oneOrMore></list></element></start></grammar>'
    )
    assert rng_ok(schema, "<nums>  1   2  3  </nums>")
    assert not rng_ok(schema, "<nums>   </nums>")  # only whitespace -> no tokens -> oneOrMore unmet


@pytest.mark.parametrize(
    ("pattern", "value"),
    [
        pytest.param("a{2,", "a{2,", id="bound-comma-then-end"),
        pytest.param("a{2,!}", "a{2,!}", id="bound-nondigit-below-zero"),
    ],
)
def test_regex_bound_upper_boundaries(pattern: str, value: str) -> None:
    assert pattern_ok(pattern, value)  # not a valid quantifier, so the braces are literal


def test_rng_value_with_leading_comment() -> None:
    schema = rwrap('<element name="e"><value><!-- pick -->on</value></element>')
    assert rng_ok(schema, "<doc><e>on</e></doc>")
    assert not rng_ok(schema, "<doc><e>off</e></doc>")


def test_rng_comment_among_element_children() -> None:
    schema = rwrap('<interleave><text/><oneOrMore><element name="b"><text/></element></oneOrMore></interleave>')
    assert rng_ok(schema, "<doc>lead <!-- note --><b>x</b> tail <b>y</b></doc>")


def test_rng_group_with_notallowed_continuation() -> None:
    schema = rwrap('<group><element name="a"><text/></element><notAllowed/></group>')
    assert not rng_ok(schema, "<doc><a>x</a></doc>")


def test_rng_name_choice_with_except_child_is_skipped() -> None:
    schema = rwrap(
        "<oneOrMore><element><choice><name>a</name><except><name>b</name></except></choice>"
        "<text/></element></oneOrMore>"
    )
    assert rng_ok(schema, "<doc><a>1</a></doc>")


def test_non_mixed_with_whitespace_element_and_comment() -> None:
    schema = (
        f'<xs:schema {XS}><xs:element name="r"><xs:complexType><xs:sequence>'
        '<xs:element name="a" type="xs:string"/></xs:sequence></xs:complexType></xs:element></xs:schema>'
    )
    assert xsd_ok(schema, "<r>\n  <!-- c --><a>x</a>\n</r>")  # whitespace + comment between elements, not mixed


def test_restriction_without_base_or_nested_type() -> None:
    schema = (
        f'<xs:schema {XS}><xs:element name="v"><xs:simpleType>'
        '<xs:restriction><xs:minLength value="2"/></xs:restriction>'
        "</xs:simpleType></xs:element></xs:schema>"
    )
    assert xsd_ok(schema, "<v>ab</v>")
    assert not xsd_ok(schema, "<v>a</v>")


def test_simple_content_extension_without_base() -> None:
    schema = (
        f'<xs:schema {XS}><xs:element name="r"><xs:complexType><xs:simpleContent>'
        '<xs:extension><xs:attribute name="u" type="xs:string"/></xs:extension>'
        "</xs:simpleContent></xs:complexType></xs:element></xs:schema>"
    )
    assert xsd_ok(schema, '<r u="x">anything</r>')


def test_complex_content_extension_unknown_base() -> None:
    schema = (
        f'<xs:schema {XS}><xs:element name="r"><xs:complexType><xs:complexContent>'
        '<xs:extension base="nope"><xs:sequence><xs:element name="a" type="xs:string"/></xs:sequence>'
        "</xs:extension></xs:complexContent></xs:complexType></xs:element></xs:schema>"
    )
    assert xsd_ok(schema, "<r><a>x</a></r>")


def test_rng_grammar_with_nameless_define_and_ref() -> None:
    schema = (
        f'<grammar xmlns="{R}"><start><element name="r"><ref name="x"/></element></start>'
        "<define><empty/></define>"  # a nameless define is skipped when resolving refs
        '<define name="x"><text/></define></grammar>'
    )
    assert rng_ok(schema, "<r>body</r>")


def test_rng_attribute_whitespace_value_against_data() -> None:
    schema = (
        f'<grammar xmlns="{R}" datatypeLibrary="{DT}"><start>'
        '<element name="r"><attribute name="n"><data type="int"/></attribute></element></start></grammar>'
    )
    assert rng_ok(schema, '<r n="5"/>')
    assert not rng_ok(schema, '<r n="   "/>')  # whitespace is not a valid int


def test_rng_whitespace_only_between_elements() -> None:
    schema = rwrap('<oneOrMore><element name="b"><text/></element></oneOrMore>')
    assert rng_ok(schema, "<doc><b>x</b>   <b>y</b></doc>")  # whitespace between elements is insignificant


def test_rng_nsname_except_now_enforced() -> None:
    schema = rwrap(
        '<oneOrMore><element><nsName ns="urn:k"><except><name>skip</name></except></nsName>'
        "<text/></element></oneOrMore>"
    )
    assert rng_ok(schema, '<doc><p:ok xmlns:p="urn:k">1</p:ok></doc>')  # in urn:k, not excepted
    assert not rng_ok(schema, '<doc><p:x xmlns:p="urn:other">1</p:x></doc>')  # wrong namespace


def test_xsd_attribute_no_type() -> None:
    schema = (
        f'<xs:schema {XS}><xs:element name="r"><xs:complexType>'
        '<xs:attribute name="a"/></xs:complexType></xs:element></xs:schema>'
    )
    assert xsd_ok(schema, '<r a="anything"/>')


def test_extension_without_base_attr() -> None:
    schema = (
        f'<xs:schema {XS}><xs:element name="r"><xs:complexType><xs:complexContent>'
        '<xs:extension><xs:sequence><xs:element name="a" type="xs:string"/></xs:sequence></xs:extension>'
        "</xs:complexContent></xs:complexType></xs:element></xs:schema>"
    )
    assert xsd_ok(schema, "<r><a>x</a></r>")


def test_simple_content_without_derivation() -> None:
    schema = (
        f'<xs:schema {XS}><xs:element name="r"><xs:complexType>'
        "<xs:simpleContent></xs:simpleContent></xs:complexType></xs:element></xs:schema>"
    )
    assert xsd_ok(schema, "<r>anything</r>")


def test_xsd_attribute_group_without_ref_is_skipped() -> None:
    schema = (
        f'<xs:schema {XS}><xs:element name="r"><xs:complexType>'
        "<xs:attributeGroup/>"  # no ref: nothing to pull in
        '<xs:attribute name="a" type="xs:string"/></xs:complexType></xs:element></xs:schema>'
    )
    assert xsd_ok(schema, '<r a="x"/>')


def test_rng_define_dedup_finds_on_later_iteration() -> None:
    # defines a, b, then b again: the dedup scan skips a (index 0) then matches b (index 1)
    schema = (
        f'<grammar xmlns="{R}"><start><element name="r"><ref name="b"/></element></start>'
        '<define name="a"><empty/></define>'
        '<define name="b"><text/></define>'
        '<define name="c"><empty/></define>'
        '<define name="b" combine="choice"><text/></define></grammar>'
    )
    assert rng_ok(schema, "<r>x</r>")


@pytest.mark.parametrize(
    ("type_name", "value", "ok"),
    [
        pytest.param("xs:double", ".5", True, id="double-leading-dot"),
        pytest.param("xs:double", "12", True, id="double-integer-only"),
        pytest.param("xs:hexBinary", "::", False, id="hex-below-A"),
        pytest.param("xs:double", "1e5z", False, id="double-exp-trailing-nondigit"),
    ],
)
def test_datatype_final_boundaries(type_name: str, value: str, ok: bool) -> None:  # noqa: FBT001  # parametrize value
    schema = f'<xs:schema {XS}><xs:element name="v" type="{type_name}"/></xs:schema>'
    assert xsd_ok(schema, f"<v>{value}</v>") is ok
