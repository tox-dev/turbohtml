from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Final

import pytest

from turbohtml import parse_xml
from turbohtml.validate import RelaxNG, SchemaValidationError, ValidationResult, XMLSchema

if TYPE_CHECKING:
    from collections.abc import Callable


from typing import TYPE_CHECKING, cast

from bench.ci import benchmarks
from bench.core import OPERATIONS
from bench.operations import INPUTS

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


def test_many_namespace_declarations_keep_the_xs_binding() -> None:
    # Nine xmlns declarations grow the in-scope namespace stack past its initial capacity; xmlns:xs comes first,
    # so a grow that failed to copy the existing bindings would drop the xs binding and stop recognizing xs:element.
    dummies = " ".join(f'xmlns:p{index}="urn:p{index}"' for index in range(8))
    schema = f'<xs:schema {XS} {dummies}><xs:element name="a" type="xs:string"/></xs:schema>'
    assert check(schema, "<a>x</a>").valid
    assert not check(schema, "<a><child/></a>").valid


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


def xsd_ok(schema: str, xml: str) -> bool:
    return XMLSchema(schema).validate(parse_xml(xml)).valid


def edge_restricted(facets: str, base: str = "xs:string") -> str:
    return (
        f'<xs:schema {XS}><xs:element name="v"><xs:simpleType>'
        f'<xs:restriction base="{base}">{facets}</xs:restriction></xs:simpleType></xs:element></xs:schema>'
    )


def pattern_ok(pattern: str, value: str) -> bool:
    return xsd_ok(edge_restricted(f'<xs:pattern value="{pattern}"/>'), f"<v>{value}</v>")


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
def test_datatype_edges(type_name: str, value: str, ok: bool) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]  # a pytest parametrize value, not a boolean-trap call site
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
def test_regex_edges(pattern: str, value: str, ok: bool) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]  # a pytest parametrize value, not a boolean-trap call site
    assert pattern_ok(pattern, value) is ok


def test_enumeration_numeric_and_growth() -> None:
    facets = "".join(f'<xs:enumeration value="{n}"/>' for n in range(6))
    assert xsd_ok(edge_restricted(facets, "xs:int"), "<v>3</v>")
    assert not xsd_ok(edge_restricted(facets, "xs:int"), "<v>9</v>")


def test_multiple_patterns_growth() -> None:
    facets = "".join(f'<xs:pattern value="[a-z]{{{n}}}"/>' for n in range(1, 6))
    # every pattern must match; only a 1-char lowercase string satisfies length 1..5 simultaneously? no -- all lengths
    assert not xsd_ok(edge_restricted(facets), "<v>abc</v>")


def test_length_exact_mismatch() -> None:
    assert not xsd_ok(edge_restricted('<xs:length value="2"/>'), "<v>abcd</v>")


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


@pytest.mark.parametrize("verdict_only", [pytest.param(False, id="report"), pytest.param(True, id="verdict")])
def test_validate_rejects_non_node(*, verdict_only: bool) -> None:
    schema = XMLSchema(f'<xs:schema {XS}><xs:element name="v" type="xs:int"/></xs:schema>')
    validate = schema.is_valid if verdict_only else schema.validate
    with pytest.raises(TypeError):
        validate("not a node")  # ty: ignore[invalid-argument-type]  # exercises the runtime type guard


@pytest.mark.parametrize("name", ["café", "中文", "𝔸bc"], ids=["two-byte", "three-byte", "four-byte"])  # ruff:ignore[ambiguous-unicode-character-string]
def test_error_path_non_ascii_names(name: str) -> None:
    schema = f'<xs:schema {XS}><xs:element name="{name}" type="xs:int"/></xs:schema>'
    result = XMLSchema(schema).validate(parse_xml(f"<{name}>bad</{name}>"))
    assert not result.valid
    assert result.errors[0].path == f"/{name}"


@pytest.mark.parametrize(
    ("document", "valid"),
    [
        pytest.param("<g31>x</g31>", True, id="found-after-collision"),
        pytest.param("<missing>x</missing>", False, id="absent"),
    ],
)
def test_many_global_elements_growth(document: str, *, valid: bool) -> None:
    globals_ = "".join(f'<xs:element name="g{number}" type="xs:string"/>' for number in (*range(11), 31))
    schema = f"<xs:schema {XS}>{globals_}</xs:schema>"
    assert xsd_ok(schema, document) is valid


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
def test_datatype_boundary_edges(type_name: str, value: str, ok: bool) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]  # a pytest parametrize value, not a boolean-trap call site
    schema = f'<xs:schema {XS}><xs:element name="v" type="{type_name}"/></xs:schema>'
    assert xsd_ok(schema, f"<v>{value}</v>") is ok


@pytest.mark.parametrize(
    ("type_name", "value"),
    [
        pytest.param("xs:int", "café", id="int-two-byte-value"),
        pytest.param("xs:int", "中文", id="int-three-byte-value"),
        pytest.param("xs:int", "𝔸", id="int-four-byte-value"),  # ruff:ignore[ambiguous-unicode-character-string]
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
def test_regex_more(pattern: str, value: str, ok: bool) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]  # a pytest parametrize value, not a boolean-trap call site
    assert pattern_ok(pattern, value) is ok


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
def test_datatype_sign_and_tz(type_name: str, value: str, ok: bool) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]  # a pytest parametrize value, not a boolean-trap call site
    schema = f'<xs:schema {XS}><xs:element name="v" type="{type_name}"/></xs:schema>'
    assert xsd_ok(schema, f"<v>{value}</v>") is ok


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


def test_language_bad_separator() -> None:
    schema = f'<xs:schema {XS}><xs:element name="v" type="xs:language"/></xs:schema>'
    assert not xsd_ok(schema, "<v>en_us</v>")


def test_regex_star_loop_revisit() -> None:
    assert pattern_ok("(ab)*", "ababab")
    assert pattern_ok("(a*)*", "aaaa")  # nested stars converge two epsilon paths onto one state


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
def test_datatype_condition_boundaries(type_name: str, value: str, ok: bool) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]  # parametrize value
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
def test_facet_condition_boundaries(facets: str, base: str, value: str, ok: bool) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]  # parametrize
    assert xsd_ok(edge_restricted(facets, base), f"<v>{value}</v>") is ok


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
def test_datatype_name_boundaries(type_name: str, value: str, ok: bool) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]  # parametrize value
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
def test_regex_parser_boundaries(pattern: str, value: str, ok: bool) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]  # parametrize value
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
def test_regex_more_boundaries(pattern: str, value: str, ok: bool) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]  # parametrize value
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
def test_more_datatype_ranges(type_name: str, value: str, ok: bool) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]  # parametrize value
    schema = f'<xs:schema {XS}><xs:element name="v" type="{type_name}"/></xs:schema>'
    assert xsd_ok(schema, f"<v>{value}</v>") is ok


def test_regex_dot_excludes_carriage_return() -> None:
    schema = edge_restricted('<xs:pattern value="."/>')
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
def test_regex_parser_edge_boundaries(pattern: str, value: str, ok: bool) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]  # parametrize value
    assert pattern_ok(pattern, value) is ok


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


@pytest.mark.parametrize(
    ("pattern", "value"),
    [
        pytest.param("a{2,", "a{2,", id="bound-comma-then-end"),
        pytest.param("a{2,!}", "a{2,!}", id="bound-nondigit-below-zero"),
    ],
)
def test_regex_bound_upper_boundaries(pattern: str, value: str) -> None:
    assert pattern_ok(pattern, value)  # not a valid quantifier, so the braces are literal


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


@pytest.mark.parametrize(
    ("type_name", "value", "ok"),
    [
        pytest.param("xs:double", ".5", True, id="double-leading-dot"),
        pytest.param("xs:double", "12", True, id="double-integer-only"),
        pytest.param("xs:hexBinary", "::", False, id="hex-below-A"),
        pytest.param("xs:double", "1e5z", False, id="double-exp-trailing-nondigit"),
    ],
)
def test_datatype_final_boundaries(type_name: str, value: str, ok: bool) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]  # parametrize value
    schema = f'<xs:schema {XS}><xs:element name="v" type="{type_name}"/></xs:schema>'
    assert xsd_ok(schema, f"<v>{value}</v>") is ok


@pytest.fixture
def facet_schema() -> Callable[[str], XMLSchema]:
    def make(declarations: str) -> XMLSchema:
        return XMLSchema(f'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">{declarations}</xs:schema>')

    return make


@pytest.mark.parametrize("count", [1, 9, 32], ids=["single", "growth", "many-types"])
def test_compiled_facet_type_lookup(facet_schema: Callable[[str], XMLSchema], count: int) -> None:
    types: Final = "".join(
        f'<xs:simpleType name="type{index}"><xs:restriction base="xs:int">'
        f'<xs:minInclusive value="{index}"/><xs:maxInclusive value="{index}"/>'
        "</xs:restriction></xs:simpleType>"
        for index in range(count)
    )
    elements: Final = "".join(f'<xs:element name="value{index}" type="type{index}"/>' for index in range(count))
    schema: Final = facet_schema(
        types
        + '<xs:element name="root"><xs:complexType><xs:sequence>'
        + elements
        + "</xs:sequence></xs:complexType></xs:element>"
    )
    documents: Final = [
        parse_xml(
            "<root>" + "".join(f"<value{index}>{index + offset}</value{index}>" for index in range(count)) + "</root>"
        )
        for offset in (0, 1, 0)
    ]
    assert [schema.validate(document).valid for document in documents] == [True, False, True]


@pytest.mark.parametrize("count", [40, 41, 42], ids=["before-cutoff", "last-builtin", "after-cutoff"])
@pytest.mark.parametrize("location", ["element", "attribute"])
def test_compiled_facet_inheritance_cutoff(facet_schema: Callable[[str], XMLSchema], count: int, location: str) -> None:
    types: Final = "".join(
        f'<xs:simpleType name="type{index}"><xs:restriction base="'
        + (f"type{index + 1}" if index + 1 < count else "xs:int")
        + '"/></xs:simpleType>'
        for index in range(count)
    )
    declaration: Final = (
        '<xs:element name="value" type="type0"/>'
        if location == "element"
        else '<xs:element name="value"><xs:complexType><xs:attribute name="number" type="type0"/>'
        "</xs:complexType></xs:element>"
    )
    schema: Final = facet_schema(types + declaration)
    document: Final = parse_xml("<value>abc</value>" if location == "element" else '<value number="abc"/>')
    assert schema.validate(document).valid is (count == 42)


@pytest.mark.parametrize("concurrent", [False, True], ids=["sequential", "concurrent"])
def test_compiled_facet_enumeration_reuse(facet_schema: Callable[[str], XMLSchema], *, concurrent: bool) -> None:
    schema: Final = facet_schema(
        '<xs:simpleType name="base"><xs:restriction base="xs:token">'
        '<xs:enumeration value="abc"/></xs:restriction></xs:simpleType>'
        '<xs:simpleType name="derived"><xs:restriction base="base">'
        '<xs:enumeration value="def"/><xs:minLength value="3"/></xs:restriction></xs:simpleType>'
        '<xs:element name="value" type="derived"/>'
    )
    documents: Final = [parse_xml(f"<value>{value}</value>") for value in (" abc ", "def", "ghi", "ab") * 8]
    if concurrent:
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(schema.validate, documents))
    else:
        results = [schema.validate(document) for document in documents]
    assert [result.valid for result in results] == [True, True, False, False] * 8


def test_compiled_facet_cyclic_inheritance(facet_schema: Callable[[str], XMLSchema]) -> None:
    schema: Final = facet_schema(
        '<xs:simpleType name="first"><xs:restriction base="second">'
        '<xs:pattern value="[a-z]+"/></xs:restriction></xs:simpleType>'
        '<xs:simpleType name="second"><xs:restriction base="first">'
        '<xs:pattern value="abc"/></xs:restriction></xs:simpleType>'
        '<xs:element name="value" type="first"/>'
    )
    assert [schema.validate(parse_xml(f"<value>{value}</value>")).valid for value in ("abc", "def", "abc1")] == [
        True,
        False,
        False,
    ]


@pytest.mark.parametrize(
    "derivation",
    [
        pytest.param('<xs:list itemType="xs:int"/>', id="list"),
        pytest.param('<xs:union memberTypes="xs:int xs:double"/>', id="union"),
    ],
)
def test_compiled_facet_string_fallback(facet_schema: Callable[[str], XMLSchema], derivation: str) -> None:
    schema: Final = facet_schema(
        '<xs:simpleType name="item"><xs:restriction><xs:simpleType>'
        + derivation
        + '</xs:simpleType><xs:maxLength value="3"/></xs:restriction></xs:simpleType>'
        '<xs:element name="root"><xs:complexType><xs:sequence><xs:element name="value" type="item"/>'
        '</xs:sequence><xs:attribute name="code" type="item"/></xs:complexType></xs:element>'
    )
    assert [
        schema.validate(parse_xml(f'<root code="{value}"><value>{value}</value></root>')).valid
        for value in ("abc", "abcd")
    ] == [True, False]


def test_compiled_facets_with_schema_comments(facet_schema: Callable[[str], XMLSchema]) -> None:
    schema: Final = facet_schema(
        '<!--declaration--><xs:element name="value"><xs:simpleType><!--restriction-->'
        '<xs:restriction base="xs:int"><xs:minInclusive value="3"/>'
        "</xs:restriction></xs:simpleType></xs:element>"
    )
    assert [schema.validate(parse_xml(f"<value>{value}</value>")).valid for value in (3, 2)] == [True, False]


@pytest.fixture
def pattern_schema() -> Callable[[str, str], XMLSchema | RelaxNG]:
    def make(kind: str, facets: str) -> XMLSchema | RelaxNG:
        if kind == "rng":
            return RelaxNG(
                '<element xmlns="http://relaxng.org/ns/structure/1.0" name="value" '
                'datatypeLibrary="http://www.w3.org/2001/XMLSchema-datatypes"><data type="token">'
                f"{facets}</data></element>"
            )
        restriction: Final = f'<xs:restriction base="xs:token">{facets}</xs:restriction>'
        if kind == "inherited":
            return XMLSchema(
                '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'
                f'<xs:simpleType name="base">{restriction}</xs:simpleType>'
                '<xs:element name="value"><xs:simpleType><xs:restriction base="base">'
                '<xs:pattern value="abc[0-9]+"/></xs:restriction></xs:simpleType></xs:element></xs:schema>'
            )
        return XMLSchema(
            '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"><xs:element name="value">'
            f"<xs:simpleType>{restriction}</xs:simpleType></xs:element></xs:schema>"
        )

    return make


@pytest.mark.parametrize("kind", ["inline", "inherited", "rng"])
@pytest.mark.parametrize("concurrent", [False, True], ids=["sequential", "concurrent"])
def test_compiled_patterns_reuse(
    pattern_schema: Callable[[str, str], XMLSchema | RelaxNG], kind: str, *, concurrent: bool
) -> None:
    facets: Final = (
        '<param name="pattern">[a-z]+[0-9]+</param><param name="pattern">abc[0-9]+</param>'
        if kind == "rng"
        else '<xs:pattern value="[a-z]+[0-9]+"/><xs:pattern value="abc[0-9]+"/>'
    )
    schema: Final = pattern_schema(kind, facets)
    documents: Final = [parse_xml(f"<value>{value}</value>") for value in (" abc123 ", "abc", "def123", "abc0") * 8]
    if concurrent:
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(schema.validate, documents))
    else:
        results = [schema.validate(document) for document in documents]
    assert [result.valid for result in results] == [True, False, False, True] * 8


@pytest.mark.parametrize(
    ("kind", "pattern", "values", "expected"),
    [
        pytest.param("inline", "", ("", "a", ""), [True, False, True], id="xsd-empty"),
        pytest.param("inline", "(a?)*b", ("aaab", "aaa", "b"), [True, False, True], id="epsilon-revisit"),
        pytest.param("rng", "", ("", "a", ""), [False, False, False], id="rng-empty"),
        pytest.param("inline", "(ab|cd)*", ("abcd", "abc", ""), [True, False, True], id="xsd-epsilon-cycle"),
        pytest.param("rng", "(ab|cd)*", ("abcd", "abc", ""), [True, False, False], id="rng-epsilon-cycle"),
        pytest.param("inline", "é+[0-9]?", ("éé1", "ee1", "é"), [True, False, True], id="xsd-unicode"),
        pytest.param("rng", "é+[0-9]?", ("éé1", "ee1", "é"), [True, False, True], id="rng-unicode"),
    ],
)
def test_compiled_pattern_outputs(
    pattern_schema: Callable[[str, str], XMLSchema | RelaxNG],
    kind: str,
    pattern: str,
    values: tuple[str, str, str],
    expected: list[bool],
) -> None:
    facet: Final = f'<param name="pattern">{pattern}</param>' if kind == "rng" else f'<xs:pattern value="{pattern}"/>'
    schema: Final = pattern_schema(kind, facet)
    assert [schema.validate(parse_xml(f"<value>{value}</value>")).valid for value in values] == expected


def test_compiled_pattern_diagnostic(pattern_schema: Callable[[str, str], XMLSchema | RelaxNG]) -> None:
    schema: Final = pattern_schema("inline", '<xs:pattern value="[a-z]+"/>')
    assert [
        [(error.type, error.message) for error in schema.validate(parse_xml(f"<value>{value}</value>")).errors]
        for value in ("123", "abc", "123")
    ] == [
        [("facet", "value does not match the required pattern")],
        [],
        [("facet", "value does not match the required pattern")],
    ]


@pytest.mark.parametrize(
    "extra",
    [
        pytest.param("<xs:pattern/>", id="missing-value"),
        pytest.param('<pattern xmlns="urn:foreign" value="[0-9]+"/>', id="foreign-namespace"),
    ],
)
def test_compiled_pattern_ignores_inapplicable_facets(
    pattern_schema: Callable[[str, str], XMLSchema | RelaxNG], extra: str
) -> None:
    schema: Final = pattern_schema("inline", extra + '<xs:pattern value="[a-z]+"/>')
    assert [schema.validate(parse_xml(f"<value>{value}</value>")).valid for value in ("abc", "123")] == [True, False]


@pytest.mark.parametrize("kind", [pytest.param("xsd", id="xsd"), pytest.param("rng", id="relax-ng")])
@pytest.mark.parametrize("negated", [pytest.param(False, id="positive"), pytest.param(True, id="negated")])
@pytest.mark.parametrize(
    ("codepoint", "inside"),
    [
        pytest.param(0x400, True, id="first"),
        pytest.param(0x47F, True, id="last"),
        pytest.param(0x480, False, id="outside"),
    ],
)
def test_growing_pattern_class(kind: str, *, negated: bool, codepoint: int, inside: bool) -> None:
    pattern: Final[str] = "[" + ("^" if negated else "") + "".join(chr(index) for index in range(0x400, 0x480)) + "]"
    schema: Final[XMLSchema | RelaxNG]
    if kind == "xsd":
        schema = XMLSchema(
            '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"><xs:element name="value"><xs:simpleType>'
            f'<xs:restriction base="xs:string"><xs:pattern value="{pattern}"/></xs:restriction>'
            "</xs:simpleType></xs:element></xs:schema>"
        )
    else:
        schema = RelaxNG(
            '<element xmlns="http://relaxng.org/ns/structure/1.0" name="value" '
            'datatypeLibrary="http://www.w3.org/2001/XMLSchema-datatypes"><data type="string">'
            f'<param name="pattern">{pattern}</param></data></element>'
        )
    assert schema.validate(parse_xml(f"<value>{chr(codepoint)}</value>")).valid is (inside != negated)


@pytest.mark.parametrize(
    ("index", "expected"),
    [
        pytest.param(0, [True, False, False], id="derived-type"),
        pytest.param(1, [True, True, False], id="builtin-type"),
    ],
)
def test_facet_benchmark_output(index: int, expected: list[bool]) -> None:
    source, document = cast("tuple[str, str]", INPUTS["validate-facets"]()[index][1])
    schema: Final = XMLSchema(source)
    assert [
        schema.validate(parse_xml(text)).valid
        for text in (document, document.replace("abc123", "ab"), "<root><other/></root>")
    ] == expected


def test_facet_compile_benchmark_output() -> None:
    compile_schema: Final = cast("Callable[[str], XMLSchema]", OPERATIONS["compile-facets"][0])
    schema: Final = compile_schema(cast("str", INPUTS["compile-facets"]()[0][1]))
    assert [schema.validate(parse_xml(f"<root><value>{value}</value></root>")).valid for value in ("abc123", "ab")] == [
        True,
        False,
    ]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        pytest.param("validate-pattern-reuse", [True, False], id="two-patterns"),
        pytest.param("validate-pattern-plain", [True, True], id="no-patterns"),
    ],
)
def test_pattern_benchmark_output(name: str, expected: list[bool]) -> None:
    _, _, load = next(benchmark for benchmark in benchmarks() if benchmark[0] == name)
    source, document = cast("tuple[str, str]", load())
    schema: Final = XMLSchema(source)
    assert [
        schema.validate(parse_xml(text)).valid for text in (document, document.replace("abc123", "abc"))
    ] == expected


def test_pattern_compile_benchmark_output() -> None:
    _, _, load = next(benchmark for benchmark in benchmarks() if benchmark[0] == "compile-pattern")
    compile_schema: Final = cast("Callable[[str], XMLSchema]", OPERATIONS["compile-pattern"][0])
    schema: Final = compile_schema(cast("str", load()))
    assert [
        schema.validate(parse_xml(f"<root><value>{value}</value></root>")).valid for value in ("abc123", "abc")
    ] == [
        True,
        False,
    ]


@pytest.mark.parametrize("name", ["validate-attributes", "validate-attributes-small"])
def test_declaration_benchmark_output(name: str) -> None:
    _, _, load = next(benchmark for benchmark in benchmarks() if benchmark[0] == name)
    source, document = cast("tuple[str, str]", load())
    assert XMLSchema(source).validate(parse_xml(document)).errors == ()


@pytest.mark.parametrize(
    ("declarations", "attributes", "expected"),
    [
        pytest.param(
            '<xs:attribute name="missing" use="required"/>',
            "",
            ["required attribute 'missing' is missing"],
            id="required",
        ),
        pytest.param(
            '<xs:attribute name="forbidden" use="prohibited"/>',
            'forbidden="x" unknown="x"',
            ["attribute 'forbidden' is prohibited", "attribute 'unknown' is not declared"],
            id="diagnostic-order",
        ),
        pytest.param(
            '<xs:attribute name="fixed" fixed="yes"/>',
            'fixed="no"',
            ["attribute 'fixed' must equal its fixed value"],
            id="fixed",
        ),
        pytest.param("", 'xmlns:f="urn:foreign" f:unknown="x"', [], id="foreign-attribute"),
        pytest.param('<xs:attribute name="item0"/>', "", [], id="duplicate-declaration"),
    ],
)
def test_wide_attribute_declaration_order(declarations: str, attributes: str, expected: list[str]) -> None:
    schema: Final = XMLSchema(
        f'<xs:schema {XS}><xs:element name="root"><xs:complexType>'
        + "".join(f'<xs:attribute name="item{index}" type="xs:string"/>' for index in range(32))
        + declarations
        + "</xs:complexType></xs:element></xs:schema>"
    )
    document: Final = parse_xml(
        "<root " + " ".join(f'item{index}="text"' for index in reversed(range(32))) + " " + attributes + "/>"
    )
    assert [error.message for error in schema.validate(document).errors] == expected


def test_wide_attribute_declarations_with_sparse_instance() -> None:
    schema: Final = XMLSchema(
        f'<xs:schema {XS}><xs:element name="root"><xs:complexType>'
        + "".join(f'<xs:attribute name="item{index}" type="xs:string"/>' for index in range(32))
        + '<xs:attribute name="required" use="required"/>'
        + "</xs:complexType></xs:element></xs:schema>"
    )
    assert [error.message for error in schema.validate(parse_xml('<root item0="value"/>')).errors] == [
        "required attribute 'required' is missing"
    ]


@pytest.mark.parametrize("index", range(4), ids=["unbounded", "bounded", "small", "string"])
def test_numeric_facet_benchmark_output(index: int) -> None:
    source, document = cast("tuple[str, str]", INPUTS["validate-numeric-facets"]()[index][1])
    assert XMLSchema(source).validate(parse_xml(document)).errors == ()


@pytest.mark.oracle
@pytest.mark.parametrize(
    ("schema", "doc"),
    [
        pytest.param(f'<xs:schema {XS}><xs:element name="n" type="xs:int"/></xs:schema>', "<n>42</n>", id="int-valid"),
        pytest.param(f'<xs:schema {XS}><xs:element name="n" type="xs:int"/></xs:schema>', "<n>x</n>", id="int-invalid"),
        pytest.param(
            f'<xs:schema {XS}><xs:element name="d" type="xs:date"/></xs:schema>', "<d>2020-06-15</d>", id="date-valid"
        ),
        pytest.param(
            f'<xs:schema {XS}><xs:element name="d" type="xs:date"/></xs:schema>', "<d>2020-13-40</d>", id="date-invalid"
        ),
        pytest.param(
            f'<xs:schema {XS}><xs:element name="r"><xs:complexType><xs:sequence>'
            '<xs:element name="a" type="xs:string"/><xs:element name="b" type="xs:int" minOccurs="0"/>'
            "</xs:sequence></xs:complexType></xs:element></xs:schema>",
            "<r><a>x</a><b>3</b></r>",
            id="sequence-valid",
        ),
        pytest.param(
            f'<xs:schema {XS}><xs:element name="r"><xs:complexType><xs:sequence>'
            '<xs:element name="a" type="xs:string"/></xs:sequence></xs:complexType></xs:element></xs:schema>',
            "<r><a>x</a><extra/></r>",
            id="sequence-extra-invalid",
        ),
        pytest.param(
            f'<xs:schema {XS}><xs:element name="r"><xs:complexType>'
            '<xs:attribute name="id" type="xs:int" use="required"/></xs:complexType></xs:element></xs:schema>',
            "<r/>",
            id="missing-attr-invalid",
        ),
        pytest.param(
            f'<xs:schema {XS}><xs:element name="v"><xs:simpleType>'
            '<xs:restriction base="xs:string"><xs:enumeration value="a"/><xs:enumeration value="b"/>'
            "</xs:restriction></xs:simpleType></xs:element></xs:schema>",
            "<v>c</v>",
            id="enum-invalid",
        ),
        pytest.param(
            f'<xs:schema {XS}><xs:element name="v"><xs:simpleType>'
            '<xs:restriction base="xs:int"><xs:minInclusive value="0"/><xs:maxInclusive value="10"/>'
            "</xs:restriction></xs:simpleType></xs:element></xs:schema>",
            "<v>11</v>",
            id="range-invalid",
        ),
        pytest.param(
            f'<xs:schema {XS} targetNamespace="urn:x" xmlns="urn:x" elementFormDefault="qualified">'
            '<xs:element name="r"><xs:complexType><xs:sequence>'
            '<xs:element name="a" type="xs:string"/></xs:sequence></xs:complexType></xs:element></xs:schema>',
            '<r xmlns="urn:x"><a>x</a></r>',
            id="namespace-valid",
        ),
    ],
)
def test_xsd_matches_lxml(schema: str, doc: str) -> None:
    etree: Final = pytest.importorskip("lxml.etree", exc_type=ImportError)
    validator: Final = etree.XMLSchema(etree.fromstring(schema.encode()))
    assert XMLSchema(schema).validate(parse_xml(doc)).valid == validator.validate(etree.fromstring(doc.encode()))


@pytest.mark.oracle
@pytest.mark.parametrize(
    ("operation", "index", "expected"),
    [
        pytest.param("validate-facets", 0, [True, False], id="derived-type"),
        pytest.param("validate-facets", 1, [True, True], id="builtin-type"),
        pytest.param("compile-facets", 0, [True, False], id="compile"),
        pytest.param("validate-facets", 2, [True, False], id="named-attributes"),
        pytest.param("validate-facets", 3, [True, False], id="small-named-type"),
    ],
)
def test_lxml_facet_benchmark_output(operation: str, index: int, expected: list[bool]) -> None:
    module: Final = pytest.importorskip("bench.competitors.lxml", exc_type=ImportError)
    etree: Final = pytest.importorskip("lxml.etree", exc_type=ImportError)
    if operation == "compile-facets":
        source = cast("str", INPUTS[operation]()[index][1])
        document = "<root><value>abc123</value></root>"
    else:
        source, document = cast("tuple[str, str]", INPUTS[operation]()[index][1])
    schema: Final = module.OPERATIONS["compile-facets"][0](source)
    assert [
        schema.validate(etree.fromstring(text.encode())) for text in (document, document.replace("abc123", "ab"))
    ] == expected


@pytest.mark.oracle
@pytest.mark.parametrize(
    ("name", "expected"),
    [
        pytest.param("validate-pattern-reuse", [True, False], id="two-patterns"),
        pytest.param("validate-pattern-plain", [True, True], id="no-patterns"),
        pytest.param("compile-pattern", [True, False], id="compile"),
    ],
)
def test_lxml_pattern_benchmark_output(name: str, expected: list[bool]) -> None:
    module: Final = pytest.importorskip("bench.competitors.lxml", exc_type=ImportError)
    etree: Final = pytest.importorskip("lxml.etree", exc_type=ImportError)
    _, _, load = next(benchmark for benchmark in benchmarks() if benchmark[0] == name)
    if name == "compile-pattern":
        source = cast("str", load())
        document = "<root><value>abc123</value></root>"
    else:
        source, document = cast("tuple[str, str]", load())
    schema: Final = module.OPERATIONS["compile-pattern"][0](source)
    assert [
        schema.validate(etree.fromstring(text.encode())) for text in (document, document.replace("abc123", "abc"))
    ] == expected


@pytest.mark.oracle
@pytest.mark.parametrize("index", range(4), ids=["wide-attributes", "small-attributes", "sparse", "few-declarations"])
def test_lxml_attribute_benchmark_output(index: int) -> None:
    etree: Final = pytest.importorskip("lxml.etree", exc_type=ImportError)
    source, document = cast("tuple[str, str]", INPUTS["validate-attributes"]()[index][1])
    schema: Final = etree.XMLSchema(etree.fromstring(source.encode()))
    assert [
        schema.validate(etree.fromstring(text.encode()))
        for text in (document, document.replace("<root ", '<root unknown="x" '))
    ] == [index != 3, False]


@pytest.mark.oracle
@pytest.mark.parametrize("index", range(4), ids=["unbounded", "bounded", "small", "string"])
def test_lxml_numeric_facet_benchmark_output(index: int) -> None:
    etree: Final = pytest.importorskip("lxml.etree", exc_type=ImportError)
    source, document = cast("tuple[str, str]", INPUTS["validate-numeric-facets"]()[index][1])
    schema: Final = etree.XMLSchema(etree.fromstring(source.encode()))
    assert [
        schema.validate(etree.fromstring(text.encode()))
        for text in (document, document.replace("</root>", "<unexpected/></root>"))
    ] == [True, False]


@pytest.mark.parametrize(
    ("index", "errors"), [(0, 0), (1, 0), (2, 0), (3, 511)], ids=["wide", "small", "sparse", "few-declarations"]
)
def test_instance_attribute_index_benchmark_output(index: int, errors: int) -> None:
    source, document = cast("tuple[str, str]", INPUTS["validate-attributes"]()[index][1])
    assert len(XMLSchema(source).validate(parse_xml(document)).errors) == errors


@pytest.mark.parametrize("index", range(4), ids=["named-elements", "builtin", "named-attributes", "small"])
def test_named_facet_probe_benchmark_output(index: int) -> None:
    source, document = cast("tuple[str, str]", INPUTS["validate-facets"]()[index][1])
    schema: Final = XMLSchema(source)
    assert [schema.validate(parse_xml(value)).valid for value in (document, document.replace("abc123", "ab"))] == [
        True,
        index == 1,
    ]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("1." + "0" * 80, True, id="long-fraction-in-bound"),
        pytest.param("9" * 80, False, id="long-integer-out-of-bound"),
    ],
)
def test_xsd_long_decimal_bound(value: str, *, expected: bool) -> None:
    assert check(restricted('<xs:maxInclusive value="2"/>', "xs:decimal"), f"<v>{value}</v>").valid is expected


@pytest.mark.parametrize(
    "document",
    [
        pytest.param("<v>bad</v>", id="datatype"),
        pytest.param("<v><unexpected/></v>", id="structure"),
    ],
)
def test_is_valid_reuses_schema_after_failure(document: str) -> None:
    schema = XMLSchema(typed("xs:int"))
    assert [schema.is_valid(parse_xml(xml)) for xml in (document, "<v>7</v>", document)] == [False, True, False]


@pytest.mark.parametrize(
    ("index", "expected"),
    [
        pytest.param(0, False, id="many-errors"),
        pytest.param(1, True, id="valid"),
        pytest.param(2, False, id="one-error"),
    ],
)
def test_validation_verdict_benchmark(index: int, *, expected: bool) -> None:
    source, document = cast("tuple[str, str]", INPUTS["is-valid"]()[index][1])
    assert XMLSchema(source).is_valid(parse_xml(document)) is expected
