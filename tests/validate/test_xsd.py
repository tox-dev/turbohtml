"""XSD 1.0 validation: datatypes, facets, occurrence, references, namespaces."""

from __future__ import annotations

import pytest

from turbohtml import parse_xml
from turbohtml.validate import SchemaValidationError, ValidationResult, XMLSchema

XS = 'xmlns:xs="http://www.w3.org/2001/XMLSchema"'


def check(schema: str, xml: str) -> ValidationResult:
    return XMLSchema(schema).validate(parse_xml(xml))


def typed(type_name: str) -> str:
    return f'<xs:schema {XS}><xs:element name="v" type="{type_name}"/></xs:schema>'


def restricted(facets: str, base: str = "xs:string") -> str:
    return (
        f'<xs:schema {XS}><xs:element name="v"><xs:simpleType>'
        f'<xs:restriction base="{base}">{facets}</xs:restriction></xs:simpleType></xs:element></xs:schema>'
    )


@pytest.mark.parametrize(
    ("type_name", "value"),
    [
        pytest.param("xs:string", "anything at all", id="string"),
        pytest.param("xs:normalizedString", "a b", id="normalizedString"),
        pytest.param("xs:token", "  a   b  ", id="token"),
        pytest.param("xs:boolean", "true", id="boolean-true"),
        pytest.param("xs:boolean", "0", id="boolean-0"),
        pytest.param("xs:decimal", "-3.14", id="decimal"),
        pytest.param("xs:integer", "-42", id="integer"),
        pytest.param("xs:int", "2147483647", id="int-max"),
        pytest.param("xs:long", "-9223372036854775808", id="long-min"),
        pytest.param("xs:short", "-32768", id="short"),
        pytest.param("xs:byte", "127", id="byte"),
        pytest.param("xs:nonNegativeInteger", "0", id="nonNegativeInteger"),
        pytest.param("xs:positiveInteger", "1", id="positiveInteger"),
        pytest.param("xs:nonPositiveInteger", "0", id="nonPositiveInteger"),
        pytest.param("xs:negativeInteger", "-1", id="negativeInteger"),
        pytest.param("xs:unsignedLong", "18446744073709551615", id="unsignedLong-max"),
        pytest.param("xs:unsignedInt", "4294967295", id="unsignedInt"),
        pytest.param("xs:unsignedShort", "65535", id="unsignedShort"),
        pytest.param("xs:unsignedByte", "255", id="unsignedByte"),
        pytest.param("xs:float", "1.5e10", id="float"),
        pytest.param("xs:double", "INF", id="double-inf"),
        pytest.param("xs:double", "-INF", id="double-neg-inf"),
        pytest.param("xs:double", "NaN", id="double-nan"),
        pytest.param("xs:date", "2020-01-31", id="date"),
        pytest.param("xs:date", "-0044-03-15Z", id="date-bce-tz"),
        pytest.param("xs:dateTime", "2020-01-31T12:00:00.5Z", id="dateTime"),
        pytest.param("xs:time", "23:59:59+01:00", id="time-tz"),
        pytest.param("xs:duration", "-P1Y2M3DT4H5M6S", id="duration"),
        pytest.param("xs:anyURI", "http://x/ y", id="anyURI"),
        pytest.param("xs:QName", "xs:foo", id="qname"),
        pytest.param("xs:NCName", "foo_bar", id="ncname"),
        pytest.param("xs:Name", ":foo.bar-1", id="name"),
        pytest.param("xs:NMTOKEN", "123:abc", id="nmtoken"),
        pytest.param("xs:language", "en-US", id="language"),
        pytest.param("xs:hexBinary", "0aFF", id="hexBinary"),
        pytest.param("xs:base64Binary", "aGVsbG8=", id="base64Binary"),
        pytest.param("xs:anySimpleType", "éè", id="anySimpleType-unicode"),
    ],
)
def test_builtin_datatype_valid(type_name: str, value: str) -> None:
    assert check(typed(type_name), f"<v>{value}</v>").valid


@pytest.mark.parametrize(
    ("type_name", "value"),
    [
        pytest.param("xs:boolean", "yes", id="boolean"),
        pytest.param("xs:decimal", "1.2.3", id="decimal"),
        pytest.param("xs:integer", "1.0", id="integer"),
        pytest.param("xs:int", "2147483648", id="int-overflow"),
        pytest.param("xs:short", "40000", id="short-overflow"),
        pytest.param("xs:byte", "-129", id="byte-underflow"),
        pytest.param("xs:positiveInteger", "0", id="positiveInteger"),
        pytest.param("xs:negativeInteger", "0", id="negativeInteger"),
        pytest.param("xs:unsignedByte", "256", id="unsignedByte"),
        pytest.param("xs:nonNegativeInteger", "-1", id="nonNegativeInteger"),
        pytest.param("xs:double", "1.0e", id="double-bad-exp"),
        pytest.param("xs:float", "abc", id="float"),
        pytest.param("xs:date", "2020-13-01", id="date-bad-month"),
        pytest.param("xs:date", "2020-01-40", id="date-bad-day"),
        pytest.param("xs:dateTime", "2020-01-01 12:00:00", id="dateTime-space"),
        pytest.param("xs:time", "25:00:00", id="time-bad-hour"),
        pytest.param("xs:duration", "P", id="duration-empty"),
        pytest.param("xs:QName", "a:b:c", id="qname"),
        pytest.param("xs:NCName", "has:colon", id="ncname"),
        pytest.param("xs:NMTOKEN", "has space", id="nmtoken"),
        pytest.param("xs:language", "toolonglang", id="language"),
        pytest.param("xs:hexBinary", "0aF", id="hexBinary-odd"),
        pytest.param("xs:base64Binary", "a===", id="base64-badpad"),
    ],
)
def test_builtin_datatype_invalid(type_name: str, value: str) -> None:
    result = check(typed(type_name), f"<v>{value}</v>")
    assert not result.valid
    assert result.errors[0].type == "datatype"


@pytest.mark.parametrize(
    ("facets", "base", "value", "ok"),
    [
        pytest.param('<xs:enumeration value="a"/><xs:enumeration value="b"/>', "xs:string", "a", True, id="enum-ok"),
        pytest.param('<xs:enumeration value="a"/>', "xs:string", "c", False, id="enum-bad"),
        pytest.param('<xs:pattern value="[A-Z]{2}\\d+"/>', "xs:string", "AB12", True, id="pattern-ok"),
        pytest.param('<xs:pattern value="[A-Z]{2}\\d+"/>', "xs:string", "A1", False, id="pattern-bad"),
        pytest.param('<xs:length value="3"/>', "xs:string", "abc", True, id="length-ok"),
        pytest.param('<xs:length value="3"/>', "xs:string", "ab", False, id="length-bad"),
        pytest.param('<xs:minLength value="2"/>', "xs:string", "a", False, id="minLength-bad"),
        pytest.param('<xs:maxLength value="2"/>', "xs:string", "abc", False, id="maxLength-bad"),
        pytest.param('<xs:minInclusive value="0"/>', "xs:int", "0", True, id="minInclusive-ok"),
        pytest.param('<xs:minInclusive value="1"/>', "xs:int", "0", False, id="minInclusive-bad"),
        pytest.param('<xs:maxInclusive value="10"/>', "xs:int", "11", False, id="maxInclusive-bad"),
        pytest.param('<xs:minExclusive value="0"/>', "xs:int", "0", False, id="minExclusive-bad"),
        pytest.param('<xs:maxExclusive value="10"/>', "xs:int", "10", False, id="maxExclusive-bad"),
        pytest.param('<xs:totalDigits value="3"/>', "xs:decimal", "1234", False, id="totalDigits-bad"),
        pytest.param('<xs:fractionDigits value="1"/>', "xs:decimal", "1.23", False, id="fractionDigits-bad"),
        pytest.param('<xs:fractionDigits value="2"/>', "xs:decimal", "1.2", True, id="fractionDigits-ok"),
        pytest.param('<xs:whiteSpace value="collapse"/>', "xs:string", "  a  ", True, id="whiteSpace-collapse"),
    ],
)
def test_facets(facets: str, base: str, value: str, ok: bool) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]  # a pytest parametrize value, not a boolean-trap call site
    assert check(restricted(facets, base), f"<v>{value}</v>").valid is ok


@pytest.mark.parametrize(
    ("pattern", "value", "ok"),
    [
        pytest.param(".", "x", True, id="dot"),
        pytest.param(".", "\n", False, id="dot-no-newline"),
        pytest.param("a|b|c", "b", True, id="alternation"),
        pytest.param("(ab)+", "abab", True, id="group-plus"),
        pytest.param("ab?c", "ac", True, id="optional"),
        pytest.param("a{2,4}", "aaa", True, id="bounded"),
        pytest.param("a{2,4}", "a", False, id="bounded-too-few"),
        pytest.param("a{2,}", "aaaaa", True, id="unbounded-lower"),
        pytest.param("a{3}", "aaa", True, id="exact"),
        pytest.param("[a-c]+", "abc", True, id="class-range"),
        pytest.param("[^0-9]+", "abc", True, id="class-negate"),
        pytest.param(r"\d\s\w", "1 a", True, id="builtin-classes"),
        pytest.param(r"[\d.]+", "3.14", True, id="class-with-builtin"),
        pytest.param(r"\D+", "abc", True, id="not-digit"),
        pytest.param("colou?r", "color", True, id="optional-u"),
        pytest.param("a*", "", True, id="star-empty"),
        pytest.param(r"a\.b", "a.b", True, id="escaped-dot"),
    ],
)
def test_regex_pattern(pattern: str, value: str, ok: bool) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]  # a pytest parametrize value, not a boolean-trap call site
    assert check(restricted(f'<xs:pattern value="{pattern}"/>'), f"<v>{value}</v>").valid is ok


def test_sequence_and_occurs() -> None:
    schema = (
        f'<xs:schema {XS}><xs:element name="r"><xs:complexType><xs:sequence>'
        '<xs:element name="a" type="xs:string"/>'
        '<xs:element name="b" type="xs:string" minOccurs="0"/>'
        '<xs:element name="c" type="xs:string" maxOccurs="unbounded"/>'
        "</xs:sequence></xs:complexType></xs:element></xs:schema>"
    )
    assert check(schema, "<r><a>1</a><c>2</c></r>").valid
    assert check(schema, "<r><a>1</a><b>2</b><c>3</c><c>4</c></r>").valid
    assert not check(schema, "<r><c>1</c></r>").valid  # missing required a
    assert not check(schema, "<r><a>1</a></r>").valid  # missing required c


def test_choice() -> None:
    schema = (
        f'<xs:schema {XS}><xs:element name="r"><xs:complexType><xs:choice>'
        '<xs:element name="a" type="xs:string"/><xs:element name="b" type="xs:string"/>'
        "</xs:choice></xs:complexType></xs:element></xs:schema>"
    )
    assert check(schema, "<r><a>x</a></r>").valid
    assert check(schema, "<r><b>x</b></r>").valid
    assert not check(schema, "<r><a>x</a><b>y</b></r>").valid


def test_all_any_order_and_missing() -> None:
    schema = (
        f'<xs:schema {XS}><xs:element name="r"><xs:complexType><xs:all>'
        '<xs:element name="x" type="xs:string"/>'
        '<xs:element name="y" type="xs:string" minOccurs="0"/>'
        "</xs:all></xs:complexType></xs:element></xs:schema>"
    )
    assert check(schema, "<r><y>1</y><x>2</x></r>").valid
    assert check(schema, "<r><x>2</x></r>").valid
    assert not check(schema, "<r><y>1</y></r>").valid  # missing required x
    assert not check(schema, "<r><x>1</x><z>2</z></r>").valid  # undeclared z


def test_named_type_and_group_ref() -> None:
    schema = (
        f"<xs:schema {XS}>"
        '<xs:group name="g"><xs:sequence><xs:element name="a" type="xs:string"/></xs:sequence></xs:group>'
        '<xs:complexType name="t"><xs:group ref="g"/><xs:attribute name="k" type="xs:string"/></xs:complexType>'
        '<xs:element name="r" type="t"/></xs:schema>'
    )
    assert check(schema, '<r k="v"><a>x</a></r>').valid
    assert not check(schema, "<r><b>x</b></r>").valid


def test_element_ref() -> None:
    schema = (
        f"<xs:schema {XS}>"
        '<xs:element name="item" type="xs:string"/>'
        '<xs:element name="r"><xs:complexType><xs:sequence>'
        '<xs:element ref="item" maxOccurs="unbounded"/>'
        "</xs:sequence></xs:complexType></xs:element></xs:schema>"
    )
    assert check(schema, "<r><item>a</item><item>b</item></r>").valid


def test_attributes_use_fixed_default_prohibited() -> None:
    schema = (
        f'<xs:schema {XS}><xs:element name="r"><xs:complexType>'
        '<xs:attribute name="req" type="xs:int" use="required"/>'
        '<xs:attribute name="fix" type="xs:string" fixed="Z"/>'
        '<xs:attribute name="no" use="prohibited"/>'
        "</xs:complexType></xs:element></xs:schema>"
    )
    assert check(schema, '<r req="1"/>').valid
    assert check(schema, '<r req="1" fix="Z"/>').valid
    assert not check(schema, "<r/>").valid  # missing required
    assert not check(schema, '<r req="1" fix="Q"/>').valid  # wrong fixed
    assert not check(schema, '<r req="1" no="x"/>').valid  # prohibited
    assert not check(schema, '<r req="1" extra="x"/>').valid  # undeclared
    assert not check(schema, '<r req="notint"/>').valid  # bad attr type


def test_attribute_group_and_ref() -> None:
    schema = (
        f"<xs:schema {XS}>"
        '<xs:attribute name="gid" type="xs:int"/>'
        '<xs:attributeGroup name="ag"><xs:attribute ref="gid" use="required"/></xs:attributeGroup>'
        '<xs:element name="r"><xs:complexType><xs:attributeGroup ref="ag"/></xs:complexType></xs:element>'
        "</xs:schema>"
    )
    assert check(schema, '<r gid="5"/>').valid
    assert not check(schema, "<r/>").valid


def test_complex_content_extension() -> None:
    schema = (
        f"<xs:schema {XS}>"
        '<xs:complexType name="base"><xs:sequence><xs:element name="a" type="xs:string"/></xs:sequence>'
        '<xs:attribute name="ba" type="xs:string"/></xs:complexType>'
        '<xs:element name="r"><xs:complexType><xs:complexContent><xs:extension base="base">'
        '<xs:sequence><xs:element name="b" type="xs:string"/></xs:sequence>'
        "</xs:extension></xs:complexContent></xs:complexType></xs:element></xs:schema>"
    )
    assert check(schema, '<r ba="k"><a>1</a><b>2</b></r>').valid
    assert not check(schema, "<r><b>2</b></r>").valid  # base element a missing


def test_simple_content_extension_and_mixed() -> None:
    schema = (
        f'<xs:schema {XS}><xs:element name="r"><xs:complexType><xs:simpleContent>'
        '<xs:extension base="xs:int"><xs:attribute name="u" type="xs:string"/></xs:extension>'
        "</xs:simpleContent></xs:complexType></xs:element></xs:schema>"
    )
    assert check(schema, '<r u="x">42</r>').valid
    assert not check(schema, '<r u="x">notint</r>').valid
    assert not check(schema, '<r u="x"><child/></r>').valid


def test_mixed_content() -> None:
    schema = (
        f'<xs:schema {XS}><xs:element name="r"><xs:complexType mixed="true"><xs:sequence>'
        '<xs:element name="a" type="xs:string" minOccurs="0"/>'
        "</xs:sequence></xs:complexType></xs:element></xs:schema>"
    )
    assert check(schema, "<r>text <a>x</a> more</r>").valid
    non_mixed = (
        f'<xs:schema {XS}><xs:element name="r"><xs:complexType><xs:sequence>'
        '<xs:element name="a" type="xs:string" minOccurs="0"/>'
        "</xs:sequence></xs:complexType></xs:element></xs:schema>"
    )
    assert not check(non_mixed, "<r>text<a>x</a></r>").valid


def test_empty_content_type() -> None:
    schema = f'<xs:schema {XS}><xs:element name="r"><xs:complexType/></xs:element></xs:schema>'
    assert check(schema, "<r/>").valid
    assert not check(schema, "<r><child/></r>").valid


def test_simple_type_restriction_chain() -> None:
    schema = (
        f"<xs:schema {XS}>"
        '<xs:simpleType name="small"><xs:restriction base="xs:int"><xs:maxInclusive value="9"/>'
        "</xs:restriction></xs:simpleType>"
        '<xs:element name="v" type="small"/></xs:schema>'
    )
    assert check(schema, "<v>5</v>").valid
    assert not check(schema, "<v>50</v>").valid


def test_target_namespace_qualified() -> None:
    schema = (
        f'<xs:schema {XS} targetNamespace="urn:x" xmlns="urn:x" elementFormDefault="qualified">'
        '<xs:element name="r"><xs:complexType><xs:sequence>'
        '<xs:element name="a" type="xs:string"/></xs:sequence></xs:complexType></xs:element></xs:schema>'
    )
    assert check(schema, '<r xmlns="urn:x"><a>x</a></r>').valid
    assert not check(schema, "<r><a>x</a></r>").valid  # wrong namespace


def test_root_errors() -> None:
    schema = f'<xs:schema {XS}><xs:element name="known" type="xs:string"/></xs:schema>'
    assert not check(schema, "<unknown/>").valid


def test_malformed_schema_raises() -> None:
    with pytest.raises(ValueError, match="malformed schema"):
        XMLSchema(f"<xs:schema {XS}><xs:element name=></xs:schema>")


def test_non_schema_root_raises() -> None:
    with pytest.raises(ValueError, match="not an xs:schema"):
        XMLSchema("<notschema/>")


def test_result_bool_and_assert_valid() -> None:
    schema = typed("xs:int")
    assert XMLSchema(schema).is_valid(parse_xml("<v>1</v>"))
    assert bool(check(schema, "<v>1</v>"))
    XMLSchema(schema).assert_valid(parse_xml("<v>1</v>"))
    with pytest.raises(SchemaValidationError):
        XMLSchema(schema).assert_valid(parse_xml("<v>bad</v>"))


def test_error_location_path() -> None:
    schema = (
        f'<xs:schema {XS}><xs:element name="r"><xs:complexType><xs:sequence>'
        '<xs:element name="n" type="xs:int"/></xs:sequence></xs:complexType></xs:element></xs:schema>'
    )
    result = check(schema, "<r><n>x</n></r>")
    assert not result.valid
    assert result.errors[0].path == "/r/n"


def test_schema_from_parsed_document() -> None:
    schema_doc = parse_xml(typed("xs:int"))
    validator = XMLSchema(schema_doc)
    assert validator.is_valid(parse_xml("<v>7</v>"))


def test_validate_element_directly() -> None:
    document = parse_xml("<wrap><v>7</v></wrap>")
    element = document.children[0].children[0]
    assert XMLSchema(typed("xs:int")).is_valid(element)
