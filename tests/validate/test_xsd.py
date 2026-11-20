from __future__ import annotations

import json
import re
import subprocess  # ruff:ignore[suspicious-subprocess-import]  # the test runs a fixed command in a subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import TYPE_CHECKING, Final, cast

import pytest
from bench.ci import benchmarks
from bench.core import OPERATIONS
from bench.operations import INPUTS

from turbohtml import Node, parse, parse_xml
from turbohtml.validate import RelaxNG, SchemaValidationError, ValidationResult, XMLSchema

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

    import lxml.etree
    from traced_bytes import PeakBytes
    from work_steps import WorkSteps

    from turbohtml import Document


_XS: Final[str] = 'xmlns:xs="http://www.w3.org/2001/XMLSchema"'


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
        pytest.param("xs:ID", "abc", id="ID"),
        pytest.param("xs:IDREF", "_x-1.y", id="IDREF"),
        pytest.param("xs:ENTITY", "a.b", id="ENTITY"),
        pytest.param("xs:NOTATION", "xs:foo", id="NOTATION"),
        pytest.param("xs:gYear", "-0044Z", id="gYear-bce-tz"),
        pytest.param("xs:gYear", "2000-01:00", id="gYear-offset"),
        pytest.param("xs:gYearMonth", "2000-12", id="gYearMonth"),
        pytest.param("xs:gMonth", "--12+01:00", id="gMonth-offset"),
        pytest.param("xs:gMonthDay", "--02-29", id="gMonthDay-leap-day"),
        pytest.param("xs:gDay", "---31Z", id="gDay"),
    ],
)
def test_builtin_datatype_valid(type_name: str, value: str) -> None:
    assert check(typed(type_name), f"<v>{value}</v>").valid


def check(schema: str, xml: str) -> ValidationResult:
    return XMLSchema(schema).validate(parse_xml(xml))


def typed(type_name: str) -> str:
    return f'<xs:schema {_XS}><xs:element name="v" type="{type_name}"/></xs:schema>'


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
        pytest.param("xs:ID", "1abc", id="ID-digit-start"),
        pytest.param("xs:IDREF", "a:b", id="IDREF-colon"),
        pytest.param("xs:ENTITY", "has space", id="ENTITY-space"),
        pytest.param("xs:ENTITY", "", id="ENTITY-empty"),
        pytest.param("xs:NOTATION", "a:b:c", id="NOTATION-two-colons"),
        pytest.param("xs:NOTATION", "a:", id="NOTATION-empty-local"),
        pytest.param("xs:gYear", "", id="gYear-empty"),
        pytest.param("xs:gYear", "20", id="gYear-short"),
        pytest.param("xs:gYear", "2000-01", id="gYear-month"),
        pytest.param("xs:gYearMonth", "20-01", id="gYearMonth-short-year"),
        pytest.param("xs:gYearMonth", "2000", id="gYearMonth-no-month"),
        pytest.param("xs:gYearMonth", "2000/12", id="gYearMonth-separator"),
        pytest.param("xs:gYearMonth", "2000-13", id="gYearMonth-month"),
        pytest.param("xs:gYearMonth", "2000-12x", id="gYearMonth-bad-timezone"),
        pytest.param("xs:gMonth", "--1", id="gMonth-short"),
        pytest.param("xs:gMonth", "x-12", id="gMonth-no-dash"),
        pytest.param("xs:gMonth", "-x12", id="gMonth-second-dash"),
        pytest.param("xs:gMonth", "--00", id="gMonth-zero"),
        pytest.param("xs:gMonth", "--12x", id="gMonth-bad-timezone"),
        pytest.param("xs:gMonthDay", "--02", id="gMonthDay-short"),
        pytest.param("xs:gMonthDay", "x-02-29", id="gMonthDay-no-dash"),
        pytest.param("xs:gMonthDay", "-x02-29", id="gMonthDay-second-dash"),
        pytest.param("xs:gMonthDay", "--02x29", id="gMonthDay-separator"),
        pytest.param("xs:gMonthDay", "--13-01", id="gMonthDay-month"),
        pytest.param("xs:gMonthDay", "--02-32", id="gMonthDay-day"),
        pytest.param("xs:gMonthDay", "--02-30", id="gMonthDay-february-30"),
        pytest.param("xs:gMonthDay", "--04-31", id="gMonthDay-april-31"),
        pytest.param("xs:gMonthDay", "--02-29x", id="gMonthDay-bad-timezone"),
        pytest.param("xs:gDay", "--15", id="gDay-short"),
        pytest.param("xs:gDay", "x--15", id="gDay-first-dash"),
        pytest.param("xs:gDay", "-x-15", id="gDay-second-dash"),
        pytest.param("xs:gDay", "--x15", id="gDay-third-dash"),
        pytest.param("xs:gDay", "---32", id="gDay-day"),
        pytest.param("xs:gDay", "---15x", id="gDay-bad-timezone"),
        pytest.param("xs:IDREFS", "1a b", id="IDREFS-invalid-item"),
        pytest.param("xs:ENTITIES", "a 1b", id="ENTITIES-invalid-item"),
    ],
)
def test_builtin_datatype_invalid(type_name: str, value: str) -> None:
    result = check(typed(type_name), f"<v>{value}</v>")
    assert not result.valid
    assert result.errors[0].type == "datatype"


@pytest.mark.parametrize(
    ("type_name", "value", "valid"),
    [
        pytest.param("xs:date", "2023-02-28", True, id="date-february-last-day"),
        pytest.param("xs:date", "2023-02-30", False, id="date-february-30"),
        pytest.param("xs:date", "2023-04-31", False, id="date-april-31"),
        pytest.param("xs:date", "2023-12-31", True, id="date-december-31"),
        pytest.param("xs:date", "2024-02-29", True, id="date-leap-day"),
        pytest.param("xs:date", "2023-02-29", False, id="date-common-year-february-29"),
        pytest.param("xs:date", "1900-02-29", False, id="date-century-not-leap"),
        pytest.param("xs:date", "2000-02-29", True, id="date-400-year-leap"),
        pytest.param("xs:date", "0000-02-29", True, id="date-year-zero-leap"),
        pytest.param("xs:date", "-0004-02-29", True, id="date-bce-leap"),
        pytest.param("xs:date", "-0001-02-29", False, id="date-bce-common"),
        pytest.param("xs:date", f"{'0' * 70}2024-02-29", True, id="date-padded-leap"),
        pytest.param("xs:date", f"{'0' * 70}2023-02-29", False, id="date-padded-common"),
        pytest.param("xs:date", f"1{'0' * 400}-02-29", True, id="date-long-leap"),
        pytest.param("xs:date", f"1{'0' * 399}1-02-29", False, id="date-long-common"),
        pytest.param("xs:date", "2023-02-30Z", False, id="date-zoned-february-30"),
        pytest.param("xs:dateTime", "2023-02-30T12:00:00", False, id="dateTime-february-30"),
        pytest.param("xs:dateTime", "2023-09-31T00:00:00+01:00", False, id="dateTime-september-31"),
        pytest.param("xs:dateTime", "2024-02-29T23:59:59Z", True, id="dateTime-leap-day"),
        pytest.param("xs:dateTime", "2023-02-29T00:00:00", False, id="dateTime-common-year-february-29"),
    ],
)
def test_builtin_date_calendar_day(type_name: str, value: str, *, valid: bool) -> None:
    assert check(typed(type_name), f"<v>{value}</v>").valid is valid


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
        pytest.param('<xs:minInclusive value="-2.5"/>', "xs:double", "NaN", False, id="minInclusive-nan"),
        pytest.param('<xs:maxInclusive value="7.5"/>', "xs:double", "NaN", False, id="maxInclusive-nan"),
        pytest.param('<xs:minExclusive value="3"/>', "xs:float", "NaN", False, id="minExclusive-nan"),
        pytest.param('<xs:maxExclusive value="9"/>', "xs:float", "NaN", False, id="maxExclusive-nan"),
        pytest.param('<xs:minInclusive value="-2.5"/>', "xs:double", "INF", True, id="minInclusive-infinity"),
        pytest.param('<xs:maxInclusive value="7.5"/>', "xs:double", "-INF", True, id="maxInclusive-negative-infinity"),
        pytest.param('<xs:maxInclusive value="7.5"/>', "xs:double", "INF", False, id="maxInclusive-infinity"),
        pytest.param('<xs:minExclusive value="3"/>', "xs:float", "-INF", False, id="minExclusive-negative-infinity"),
        pytest.param('<xs:whiteSpace value="collapse"/>', "xs:double", "NaN", True, id="unbounded-nan"),
        pytest.param('<xs:minInclusive value="NaN"/>', "xs:double", "NaN", True, id="minInclusive-nan-bound-nan"),
        pytest.param('<xs:minInclusive value="NaN"/>', "xs:float", "NaN", True, id="minInclusive-nan-bound-float-nan"),
        pytest.param('<xs:maxInclusive value="NaN"/>', "xs:double", "NaN", True, id="maxInclusive-nan-bound-nan"),
        pytest.param('<xs:minExclusive value="NaN"/>', "xs:double", "NaN", False, id="minExclusive-nan-bound-nan"),
        pytest.param('<xs:maxExclusive value="NaN"/>', "xs:float", "NaN", False, id="maxExclusive-nan-bound-nan"),
        pytest.param('<xs:minExclusive value="3"/>', "xs:double", "5", True, id="minExclusive-double-above"),
        pytest.param('<xs:maxExclusive value="9"/>', "xs:float", "2", True, id="maxExclusive-float-below"),
        pytest.param('<xs:minInclusive value="NaN"/>', "xs:double", "5", False, id="minInclusive-nan-bound-finite"),
        pytest.param('<xs:maxInclusive value="NaN"/>', "xs:double", "INF", False, id="maxInclusive-nan-bound-inf"),
        pytest.param(
            '<xs:minInclusive value="NaN"/><xs:maxInclusive value="5"/>',
            "xs:double",
            "NaN",
            False,
            id="nan-min-inclusive-and-finite-max-inclusive",
        ),
        pytest.param(
            '<xs:minInclusive value="NaN"/><xs:maxInclusive value="NaN"/>',
            "xs:double",
            "NaN",
            True,
            id="nan-min-and-max-inclusive",
        ),
        pytest.param('<xs:totalDigits value="3"/>', "xs:decimal", "1234", False, id="totalDigits-bad"),
        pytest.param('<xs:fractionDigits value="1"/>', "xs:decimal", "1.23", False, id="fractionDigits-bad"),
        pytest.param('<xs:fractionDigits value="2"/>', "xs:decimal", "1.2", True, id="fractionDigits-ok"),
        pytest.param('<xs:whiteSpace value="collapse"/>', "xs:string", "  a  ", True, id="whiteSpace-collapse"),
        pytest.param(
            '<xs:maxLength value="9007199254740993"/>', "xs:string", "abc", True, id="maxLength-beyond-double"
        ),
        pytest.param(
            '<xs:minLength value="9007199254740993"/>', "xs:string", "abc", False, id="minLength-beyond-double"
        ),
        pytest.param('<xs:length value="4294967299"/>', "xs:string", "abc", False, id="length-beyond-int"),
        pytest.param(
            '<xs:maxLength value="99999999999999999999"/>', "xs:string", "abc", True, id="maxLength-beyond-ssize"
        ),
        pytest.param(
            '<xs:minLength value="-99999999999999999999"/>',
            "xs:string",
            "abc",
            True,
            id="minLength-negative-beyond-ssize",
        ),
        pytest.param('<xs:maxLength value=" 3 "/>', "xs:string", "abc", True, id="maxLength-surrounded-by-space"),
        pytest.param('<xs:maxLength value="+3"/>', "xs:string", "abc", True, id="maxLength-plus-sign"),
        pytest.param('<xs:maxLength value="3.7"/>', "xs:string", "abcd", False, id="maxLength-fraction-ignored"),
        pytest.param('<xs:maxLength value="abcd"/>', "xs:string", "ab", False, id="maxLength-not-a-number"),
        pytest.param('<xs:maxLength value="   "/>', "xs:string", "abc", False, id="maxLength-blank-reads-zero"),
        pytest.param('<xs:minLength value="-1"/>', "xs:string", "", True, id="minLength-negative"),
        pytest.param(
            '<xs:totalDigits value="9007199254740993"/>', "xs:decimal", "1" * 20, True, id="totalDigits-beyond-double"
        ),
        pytest.param(
            '<xs:fractionDigits value="9007199254740993"/>',
            "xs:decimal",
            "1.5",
            True,
            id="fractionDigits-beyond-double",
        ),
    ],
)
def test_facets(facets: str, base: str, value: str, ok: bool) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]  # a pytest parametrize value, not a boolean-trap call site
    assert check(restricted(facets, base), f"<v>{value}</v>").valid is ok


def restricted(facets: str, base: str = "xs:string") -> str:
    return (
        f'<xs:schema {_XS}><xs:element name="v"><xs:simpleType>'
        f'<xs:restriction base="{base}">{facets}</xs:restriction></xs:simpleType></xs:element></xs:schema>'
    )


@pytest.mark.parametrize(
    ("base", "facet", "bound", "value", "ok"),
    [
        pytest.param("xs:date", "minInclusive", "1990-01-01", "1989-12-31", False, id="date-min-inclusive-below"),
        pytest.param("xs:date", "minInclusive", "1990-01-01", "1990-01-01", True, id="date-min-inclusive-equal"),
        pytest.param("xs:date", "minInclusive", "1990-01-01", "2777-07-07", True, id="date-min-inclusive-above"),
        pytest.param("xs:date", "maxInclusive", "2060-12-31", "0950-05-05", True, id="date-max-inclusive-below"),
        pytest.param("xs:date", "maxInclusive", "2060-12-31", "2060-12-31", True, id="date-max-inclusive-equal"),
        pytest.param("xs:date", "maxInclusive", "2060-12-31", "2777-07-07", False, id="date-max-inclusive-above"),
        pytest.param("xs:date", "minExclusive", "1990-01-01", "1989-12-31", False, id="date-min-exclusive-below"),
        pytest.param("xs:date", "minExclusive", "1990-01-01", "1990-01-01", False, id="date-min-exclusive-equal"),
        pytest.param("xs:date", "minExclusive", "1990-01-01", "1990-01-02", True, id="date-min-exclusive-above"),
        pytest.param("xs:date", "maxExclusive", "2060-12-31", "2060-12-30", True, id="date-max-exclusive-below"),
        pytest.param("xs:date", "maxExclusive", "2060-12-31", "2060-12-31", False, id="date-max-exclusive-equal"),
        pytest.param("xs:date", "maxExclusive", "2060-12-31", "2061-01-01", False, id="date-max-exclusive-above"),
        pytest.param("xs:date", "maxInclusive", "2024-02-29", "2024-03-01", False, id="date-leap-day"),
        pytest.param("xs:date", "maxInclusive", "2100-02-28", "2100-03-01", False, id="date-century-not-leap"),
        pytest.param("xs:date", "minInclusive", "-0044-03-15", "-0045-01-01", False, id="date-bce-below"),
        pytest.param("xs:date", "minInclusive", "-0044-03-15", "0001-01-01", True, id="date-bce-above"),
        pytest.param("xs:date", "maxInclusive", "2060-12-31", f"{'0' * 70}2777-07-07", False, id="date-padded-above"),
        pytest.param("xs:date", "maxInclusive", "2060-12-31", f"{'0' * 70}2029-01-01", True, id="date-padded-below"),
        pytest.param("xs:date", "maxInclusive", "2060-12-31", f"1{'0' * 70}-01-01", False, id="date-long-year"),
        pytest.param("xs:date", "maxInclusive", "2060-12-31", f"1{'0' * 400}-01-01", False, id="date-huge-year"),
        pytest.param("xs:date", "maxInclusive", " 2060-12-31\n", "2060-12-31", True, id="date-bound-space"),
        pytest.param("xs:date", "maxInclusive", " 2060-12-31\n", "2061-01-01", False, id="date-bound-space-above"),
        pytest.param("xs:date", "maxInclusive", "soon", "1990-01-01", False, id="date-bound-not-a-date"),
        pytest.param("xs:date", "maxInclusive", "", "1990-01-01", False, id="date-bound-empty"),
        pytest.param("xs:date", "minInclusive", "1990-01-01Z", "1990-01-01-02:00", True, id="date-zone-west"),
        pytest.param("xs:date", "minInclusive", "1990-01-01Z", "1990-01-01+02:00", False, id="date-zone-east"),
        pytest.param("xs:date", "minInclusive", "1990-01-01", "1990-01-02Z", True, id="date-zoned-above-unzoned"),
        pytest.param("xs:date", "maxInclusive", "1990-01-02Z", "1990-01-01", True, id="date-unzoned-below-zoned"),
        pytest.param("xs:date", "minInclusive", "1990-01-01", "1990-01-01Z", False, id="date-zoned-unordered-min"),
        pytest.param("xs:date", "maxInclusive", "1990-01-01", "1990-01-01Z", False, id="date-zoned-unordered-max"),
        pytest.param(
            "xs:dateTime", "maxInclusive", "2060-12-31T23:59:59", "2777-07-07T00:00:00", False, id="dateTime-above"
        ),
        pytest.param(
            "xs:dateTime", "maxInclusive", "2060-12-31T23:59:59", "2060-12-31T23:59:59", True, id="dateTime-equal"
        ),
        pytest.param(
            "xs:dateTime", "maxInclusive", "2060-12-31T23:59:59", "2060-12-31T23:59:59.000", True, id="dateTime-zeros"
        ),
        pytest.param(
            "xs:dateTime",
            "maxInclusive",
            "2060-12-31T23:59:59",
            "2060-12-31T23:59:59.0000000000000000001",
            False,
            id="dateTime-fraction-above",
        ),
        pytest.param(
            "xs:dateTime",
            "minInclusive",
            "1990-01-01T00:00:00.5",
            "1990-01-01T00:00:00.25",
            False,
            id="dateTime-fraction",
        ),
        pytest.param(
            "xs:dateTime",
            "minInclusive",
            "1990-01-01T00:00:00.5",
            "1990-01-01T00:00:00",
            False,
            id="dateTime-no-fraction",
        ),
        pytest.param(
            "xs:dateTime",
            "maxInclusive",
            "1990-01-01T12:00:00.5Z",
            "1990-01-01T13:00:00.49+01:00",
            True,
            id="dateTime-zone",
        ),
        pytest.param(
            "xs:dateTime",
            "maxExclusive",
            "2000-01-16T12:00:00Z",
            "2000-01-15T12:00:00",
            True,
            id="dateTime-unzoned-below",
        ),
        pytest.param(
            "xs:dateTime", "maxExclusive", "2000-01-01T12:00:00Z", "2000-01-01T12:00:00", False, id="dateTime-unordered"
        ),
        pytest.param("xs:time", "maxInclusive", "17:00:00", "23:59:59", False, id="time-above"),
        pytest.param("xs:time", "minInclusive", "09:00:00", "12:00:00", True, id="time-inside"),
        pytest.param("xs:time", "maxInclusive", "17:00:00", "17:00:00.001", False, id="time-fraction-above"),
        pytest.param("xs:time", "minInclusive", "09:00:00Z", "10:30:00+02:00", False, id="time-zone-below"),
        pytest.param("xs:time", "minInclusive", "09:00:00Z", "08:00:00-02:00", True, id="time-zone-above"),
        pytest.param("xs:duration", "maxInclusive", "P30D", "P77Y", False, id="duration-above"),
        pytest.param("xs:duration", "maxInclusive", "P30D", "P29DT23H59M59S", True, id="duration-below"),
        pytest.param("xs:duration", "maxInclusive", "P1Y", "P12M", True, id="duration-year-is-twelve-months"),
        pytest.param("xs:duration", "maxInclusive", "P1D", "PT24H", True, id="duration-day-is-24-hours"),
        pytest.param("xs:duration", "maxInclusive", "P1D", "PT1441M", False, id="duration-minutes-above"),
        pytest.param("xs:duration", "maxExclusive", "P32D", "P1M", True, id="duration-month-below-32-days"),
        pytest.param("xs:duration", "minExclusive", "P27D", "P1M", True, id="duration-month-above-27-days"),
        pytest.param("xs:duration", "maxInclusive", "P30D", "P1M", False, id="duration-month-unordered-max"),
        pytest.param("xs:duration", "minInclusive", "P30D", "P1M", False, id="duration-month-unordered-min"),
        pytest.param("xs:duration", "minInclusive", "P0D", "-PT1S", False, id="duration-negative"),
        pytest.param("xs:duration", "minInclusive", "-P1M", "-P27D", True, id="duration-negative-month"),
        pytest.param("xs:duration", "maxInclusive", "P30D", f"P{'0' * 70}100Y", False, id="duration-padded-above"),
        pytest.param("xs:duration", "maxInclusive", "P30D", f"P{'0' * 70}29D", True, id="duration-padded-below"),
        pytest.param("xs:duration", "maxInclusive", "P30D", f"P{'9' * 400}Y", False, id="duration-huge-years"),
        pytest.param("xs:duration", "minInclusive", "P30D", f"P{'9' * 400}D", True, id="duration-huge-days"),
        pytest.param("xs:duration", "maxInclusive", f"P{'9' * 400}D", f"P{'9' * 500}D", False, id="duration-both-huge"),
        pytest.param("xs:duration", "maxInclusive", "PT2S", "PT1.5S", True, id="duration-fraction-below"),
        pytest.param("xs:duration", "maxExclusive", "PT1.5S", "PT1.5S", False, id="duration-fraction-equal"),
        pytest.param("xs:duration", "maxInclusive", "PT1.5S", "PT1.50S", True, id="duration-fraction-trailing-zero"),
        pytest.param("xs:duration", "minExclusive", "PT1.5S", "PT1.25S", False, id="duration-fraction-under-bound"),
        pytest.param("xs:duration", "minExclusive", "PT1.25S", "PT1.5S", True, id="duration-fraction-over-bound"),
        pytest.param(
            "xs:duration",
            "maxInclusive",
            "PT0.1S",
            "PT0.10000000000000001S",
            False,
            id="duration-fraction-beyond-double",
        ),
        pytest.param("xs:duration", "minInclusive", "-PT1S", "-PT1.5S", False, id="duration-negative-fraction-below"),
        pytest.param("xs:duration", "maxInclusive", "-PT1S", "-PT1.5S", True, id="duration-negative-fraction-above"),
        pytest.param("xs:duration", "maxInclusive", "PT0S", "-PT0.5S", True, id="duration-negative-fraction-vs-zero"),
        pytest.param("xs:duration", "minInclusive", "PT0S", "-PT0.0S", True, id="duration-negative-zero-fraction"),
        pytest.param("xs:duration", "maxInclusive", "PT0.5S", "-PT0.0S", True, id="duration-negative-zero-below"),
        pytest.param("xs:duration", "maxExclusive", "PT0S", "-PT0.0S", False, id="duration-negative-zero-equal"),
        pytest.param(
            "xs:duration", "minInclusive", "-PT0.0S", "PT0.5S", True, id="duration-positive-above-negative-zero"
        ),
        pytest.param("xs:duration", "minExclusive", "PT1.50S", "PT1.5S", False, id="duration-fraction-equal-padded"),
        pytest.param(
            "xs:date", "maxInclusive", f"{2**30}-01-01", f"{2**30}-01-02", False, id="date-beyond-double-above"
        ),
        pytest.param(
            "xs:date", "maxInclusive", f"{2**30}-01-01", f"{2**30 - 1}-12-31", True, id="date-beyond-double-below"
        ),
        pytest.param(
            "xs:date", "maxInclusive", f"{2**30}-01-01", f"{2**30}-01-01", False, id="date-beyond-double-equal"
        ),
        pytest.param(
            "xs:dateTime",
            "maxInclusive",
            f"{2**30}-01-01T00:00:00Z",
            f"{2**30}-01-01T00:00:01Z",
            False,
            id="dateTime-beyond-double-above",
        ),
        pytest.param(
            "xs:duration", "maxInclusive", f"PT{2**54}S", f"PT{2**53}S", True, id="duration-beyond-double-below"
        ),
        pytest.param(
            "xs:duration", "maxInclusive", f"PT{2**53}S", f"PT{2**53}S", False, id="duration-beyond-double-equal"
        ),
        pytest.param(
            "xs:duration", "maxInclusive", f"PT{2**53}S", f"PT{2**53 + 1}S", False, id="duration-beyond-double-above"
        ),
        pytest.param("xs:duration", "maxInclusive", "forever", "P1D", False, id="duration-bound-not-a-duration"),
        pytest.param("xs:gYear", "minInclusive", "1990", "1989", False, id="gYear-below"),
        pytest.param("xs:gYear", "minInclusive", "1990", "1990", True, id="gYear-equal"),
        pytest.param("xs:gYear", "maxExclusive", "2000", "2000", False, id="gYear-equal-exclusive"),
        pytest.param("xs:gYear", "minInclusive", "-0044", "-0045", False, id="gYear-bce-below"),
        pytest.param("xs:gYear", "minInclusive", "-0044", "0000", True, id="gYear-bce-zero-above"),
        pytest.param("xs:gYear", "maxInclusive", "-0044", "0000", False, id="gYear-zero-above-bce-bound"),
        pytest.param("xs:gYear", "maxInclusive", "2000-05:00", "2000-14:00", False, id="gYear-zoned-above-utc"),
        pytest.param("xs:gYear", "minInclusive", "2000-05:00", "2000-14:00", True, id="gYear-zoned-later-utc"),
        pytest.param("xs:gYear", "minInclusive", "1990", "1990+14:00", False, id="gYear-unzoned-indeterminate"),
        pytest.param("xs:gYearMonth", "maxInclusive", "2000-02", "2000-02", True, id="gYearMonth-equal"),
        pytest.param("xs:gYearMonth", "minExclusive", "1999-12", "2000-01", True, id="gYearMonth-next-month"),
        pytest.param("xs:gYearMonth", "maxInclusive", "2000-02", "2000-03", False, id="gYearMonth-after"),
        pytest.param("xs:gMonth", "minInclusive", "--12", "--01", False, id="gMonth-below"),
        pytest.param("xs:gMonth", "maxInclusive", "--01", "--12", False, id="gMonth-above"),
        pytest.param("xs:gMonth", "maxInclusive", "--06", "--01Z", True, id="gMonth-zoned-far-below"),
        pytest.param("xs:gMonth", "maxInclusive", "--02", "--02", True, id="gMonth-leap-february"),
        pytest.param("xs:gMonthDay", "minInclusive", "--02-28", "--02-29", True, id="gMonthDay-leap-day-above"),
        pytest.param("xs:gMonthDay", "maxInclusive", "--02-28", "--02-29", False, id="gMonthDay-leap-day-above-bound"),
        pytest.param("xs:gMonthDay", "maxExclusive", "--12-31", "--12-31", False, id="gMonthDay-equal-exclusive"),
        pytest.param("xs:gDay", "maxInclusive", "---31", "---30", True, id="gDay-below"),
        pytest.param("xs:gDay", "minExclusive", "---31", "---01", False, id="gDay-below-exclusive"),
        pytest.param("xs:gDay", "minInclusive", "---15", "---16+14:00", False, id="gDay-indeterminate-offset"),
        pytest.param("xs:gDay", "maxInclusive", "---15", "---15-13:00", False, id="gDay-indeterminate-same-day"),
        pytest.param("xs:gDay", "maxInclusive", "---01", "---01", True, id="gDay-equal"),
        pytest.param("xs:gMonth", "maxInclusive", "--13", "--01", False, id="gMonth-bound-not-a-gMonth"),
    ],
)
def test_ordered_range_facets(base: str, facet: str, bound: str, value: str, ok: bool) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]  # a pytest parametrize value, not a boolean-trap call site
    assert check(restricted(f'<xs:{facet} value="{bound}"/>', base), f"<v>{value}</v>").valid is ok


@pytest.mark.parametrize(
    ("value", "messages"),
    [
        pytest.param("2035-03-20", [], id="inside"),
        pytest.param("1950-01-01", ["value is below minInclusive", "value is not above minExclusive"], id="below"),
        pytest.param("2070-01-01", ["value is above maxInclusive", "value is not below maxExclusive"], id="above"),
    ],
)
def test_ordered_range_facets_inherited(value: str, messages: list[str]) -> None:
    schema = (
        f"<xs:schema {_XS}>"
        '<xs:simpleType name="window"><xs:restriction base="xs:date">'
        '<xs:minInclusive value="1990-01-01"/><xs:maxInclusive value="2060-12-31"/>'
        '<xs:minExclusive value="1989-12-31"/><xs:maxExclusive value="2061-01-01"/>'
        "</xs:restriction></xs:simpleType>"
        '<xs:element name="v"><xs:simpleType><xs:restriction base="window"><xs:pattern value=".*"/></xs:restriction>'
        "</xs:simpleType></xs:element></xs:schema>"
    )
    result = check(schema, f"<v>{value}</v>")
    assert [(error.type, error.message) for error in result.errors] == [("facet", message) for message in messages]


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
        pytest.param(r"a\{2\}", "a{2}", True, id="escaped-braces"),
        pytest.param("[{}]", "{", True, id="braces-in-class"),
        pytest.param(r"a\]", "a]", True, id="escaped-closing-bracket"),
        pytest.param("[a-a]", "a", True, id="class-range-equal-endpoints"),
        pytest.param(r"[\t-\n]", "\t", True, id="class-range-escaped-endpoints"),
        pytest.param("[-a]", "-", True, id="class-leading-hyphen"),
        pytest.param(r"[\^a]", "^", True, id="class-escaped-caret"),
        pytest.param(r"[\[a]", "[", True, id="class-escaped-opening-bracket"),
        pytest.param("[^0-9]+", "abc", True, id="class-negate"),
        pytest.param("[a-z-[aeiou]]", "b", True, id="class-subtract-consonant"),
        pytest.param("[a-z-[aeiou]]", "a", False, id="class-subtract-vowel"),
        pytest.param("[a-z-[aeiou]]", "1", False, id="class-subtract-outside-base"),
        pytest.param("a[b-c-[c]]", "ab", True, id="class-subtract-with-literal-prefix"),
        pytest.param("a[b-c-[c]]", "ac", False, id="class-subtract-with-excluded-suffix"),
        pytest.param("a[b-c-[c]]", "xb", False, id="class-subtract-with-mismatched-prefix"),
        pytest.param("[^a-z-[0-9]]", "!", True, id="negated-class-subtract-punctuation"),
        pytest.param("[^a-z-[0-9]]", "1", False, id="negated-class-subtract-digit"),
        pytest.param("[a-z-[aeiou-[e]]]", "e", True, id="nested-class-subtract-restores-letter"),
        pytest.param("[a-z-[aeiou-[e]]]", "a", False, id="nested-class-subtract-vowel"),
        pytest.param(r"[\w-[\d]]", "a", True, id="builtin-class-subtract-letter"),
        pytest.param(r"[\w-[\d]]", "1", False, id="builtin-class-subtract-digit"),
        pytest.param(r"[\d-[0-9]]", "\u0660", True, id="unicode-digit-subtract-ascii"),
        pytest.param(r"[\d-[0-9]]", "1", False, id="ascii-digit-subtracted"),
        pytest.param("[a-cb-d]", "d", True, id="class-overlapping-ranges-match-their-union"),
        pytest.param("[a-cb-d]", "e", False, id="class-overlapping-ranges-reject-past-their-union"),
        pytest.param("[a-bc-d]", "c", True, id="class-adjacent-ranges-join"),
        pytest.param("[a-bd-e]", "c", False, id="class-ranges-with-a-gap-keep-the-gap"),
        pytest.param("[a-zc-d]", "q", True, id="class-range-inside-a-wider-range"),
        pytest.param("[za-c]", "b", True, id="class-members-out-of-order"),
        pytest.param("[^a-cb-d]", "c", False, id="negated-class-overlapping-ranges-reject-the-union"),
        pytest.param("[^a-cb-d]", "e", True, id="negated-class-overlapping-ranges-accept-past-the-union"),
        pytest.param("[a-z-[b-dc-e]]", "a", True, id="class-subtract-overlapping-ranges-keeps-outside"),
        pytest.param("[a-z-[b-dc-e]]", "c", False, id="class-subtract-overlapping-ranges-drops-inside"),
        pytest.param(
            r"[\p{IsBasicLatin}\P{IsBasicLatin}]", "中", True, id="complementary-blocks-cover-every-character"
        ),
        pytest.param(r"\p{Lu}", "A", True, id="category-uppercase"),
        pytest.param(r"\p{Lu}", "a", False, id="category-uppercase-rejects-lowercase"),
        pytest.param(r"a\p{Lu}", "bA", False, id="category-with-mismatched-prefix"),
        pytest.param(r"\p{Lu}|a", "A", True, id="category-alternative"),
        pytest.param(r"\p{Lu}|a", "a", True, id="category-literal-alternative"),
        pytest.param(r"\p{Lu}|a", "b", False, id="category-alternatives-reject"),
        pytest.param(r"\p{Lu}?", "a", False, id="optional-category-reject"),
        pytest.param(r"\P{Lu}", "a", True, id="complement-category-lowercase"),
        pytest.param(r"\P{Lu}", "A", False, id="complement-category-uppercase"),
        pytest.param(r"\p{L}", "中", True, id="category-all-letters"),
        pytest.param(r"\p{M}", "\u0300", True, id="category-all-marks"),
        pytest.param(r"\p{N}", "²", True, id="category-all-numbers"),
        pytest.param(r"\p{P}", "_", True, id="category-all-punctuation"),
        pytest.param(r"\p{Z}", " ", True, id="category-all-separators"),
        pytest.param(r"\p{S}", "😀", True, id="category-all-symbols"),
        pytest.param(r"\p{C}", "\ue000", True, id="category-all-other"),
        pytest.param(r"\p{Cn}", "\u0378", True, id="category-unassigned"),
        pytest.param(r"\p{Lu}", "\U0001d400", True, id="category-supplementary-uppercase"),
        pytest.param(r"[\p{Lu}a]", "a", True, id="category-class-union-literal"),
        pytest.param(r"[\p{Lu}a]", "A", True, id="category-class-union-property"),
        pytest.param(r"[\p{Lu}a]", "b", False, id="category-class-union-outside"),
        pytest.param(r"[\p{Lu}\d]", "1", True, id="category-class-union-builtin"),
        pytest.param(r"[^\p{Lu}]", "A", False, id="category-negated-class"),
        pytest.param(r"[^a\p{Lu}]", "b", True, id="category-negated-class-with-range"),
        pytest.param(r"[^a\p{Lu}]", "a", False, id="category-negated-class-range-rejects"),
        pytest.param(r"[^a\p{Lu}]", "A", False, id="category-negated-class-property-rejects"),
        pytest.param(r"[\p{L}-[\p{Lu}]]", "a", True, id="category-subtract-uppercase"),
        pytest.param(r"[\p{L}-[\p{Lu}]]", "A", False, id="category-subtract-uppercase-rejects"),
        pytest.param(r"[\p{L}-[\p{Lu}]]", "1", False, id="category-subtract-outside-base"),
        pytest.param(r"[\P{Lu}]", "a", True, id="complement-category-in-class"),
        pytest.param(r"\p{IsBasicLatin}", "A", True, id="block-basic-latin"),
        pytest.param(r"\p{IsBasicLatin}", "é", False, id="block-basic-latin-outside"),
        pytest.param(r"\P{IsBasicLatin}", "é", True, id="complement-block-outside"),
        pytest.param(r"\P{IsBasicLatin}", "A", False, id="complement-block-inside"),
        pytest.param(r"\p{IsLatin-1Supplement}", "é", True, id="block-latin-1-hyphen"),
        pytest.param(r"\p{IsLatin-1Supplement}", "A", False, id="block-latin-1-before"),
        pytest.param(r"\P{IsLatin-1Supplement}", "A", True, id="complement-block-before"),
        pytest.param(r"\P{IsLatin-1Supplement}", "Ā", True, id="complement-block-after"),
        pytest.param(r"\p{IsCJKUnifiedIdeographsExtensionB}", "\U00020000", True, id="block-supplementary-start"),
        pytest.param(r"\p{IsCJKUnifiedIdeographsExtensionB}", "\U0002a6df", True, id="block-supplementary-end"),
        pytest.param(r"\p{IsCJKUnifiedIdeographsExtensionB}", "\U0002a700", False, id="block-supplementary-next"),
        pytest.param(r"\p{IsSupplementaryPrivateUseArea-B}", "\U00100000", True, id="block-highest-range"),
        pytest.param(r"\P{IsSupplementaryPrivateUseArea-B}", "A", True, id="complement-highest-range"),
        pytest.param(r"[\p{IsBasicLatin}]", "A", True, id="block-in-class"),
        pytest.param(r"[a\p{IsGreekandCoptic}]", "\u03b1", True, id="block-class-union"),
        pytest.param(r"[^\p{IsBasicLatin}]", "é", True, id="block-negated-class"),
        pytest.param(r"[\P{IsBasicLatin}A]", "A", True, id="complement-block-class-union"),
        pytest.param(r"[\P{IsBasicLatin}A]", "B", False, id="complement-block-class-outside"),
        pytest.param(r"[\p{L}-[\p{IsBasicLatin}]]", "é", True, id="category-minus-block"),
        pytest.param(r"[\p{L}-[\p{IsBasicLatin}]]", "A", False, id="category-minus-block-rejects"),
        pytest.param(r"\p{IsGreek}", "\u03b1", True, id="legacy-greek-block"),
        pytest.param(r"\P{IsGreek}", "A", True, id="legacy-greek-complement"),
        pytest.param(r"\p{IsCombiningMarksforSymbols}", "\u20d0", True, id="legacy-combining-block"),
        pytest.param(r"\p{IsPrivateUse}", "\ue000", True, id="legacy-private-use-bmp"),
        pytest.param(r"\p{IsPrivateUse}", "\U000f0000", True, id="legacy-private-use-plane-15"),
        pytest.param(r"\p{IsPrivateUse}", "\U00100000", True, id="legacy-private-use-plane-16"),
        pytest.param(r"\p{IsPrivateUse}", "\uf900", False, id="legacy-private-use-gap"),
        pytest.param(r"\P{IsPrivateUse}", "\uf900", True, id="legacy-private-use-complement-gap"),
        pytest.param(r"\P{IsPrivateUse}", "\ue000", False, id="legacy-private-use-complement-range"),
        pytest.param(r"\d\s\w", "1 a", True, id="builtin-classes"),
        pytest.param(r"\d", "\u0660", True, id="digit-class-arabic-indic"),
        pytest.param(r"\d", "\u0966", True, id="digit-class-devanagari"),
        pytest.param(r"\d", "\U0001d7ce", True, id="digit-class-supplementary"),
        pytest.param(r"\d", "²", False, id="digit-class-other-number"),
        pytest.param(r"\d", "中", False, id="digit-class-empty-page"),
        pytest.param(r"\D", "\u0660", False, id="nondigit-class-arabic-indic"),
        pytest.param(r"\D", "²", True, id="nondigit-class-other-number"),
        pytest.param(r"[\d]", "\u0966", True, id="digit-class-in-brackets"),
        pytest.param(r"[\d]", "²", False, id="digit-class-in-brackets-other-number"),
        pytest.param(r"[\D]", "\u0966", False, id="nondigit-class-in-brackets"),
        pytest.param(r"[\D]", "²", True, id="nondigit-class-in-brackets-other-number"),
        pytest.param(r"\w", "ª", True, id="word-class-non-name-letter"),
        pytest.param(r"\w", "°", True, id="word-class-symbol"),
        pytest.param(r"\w", "$", True, id="word-class-ascii-symbol"),
        pytest.param(r"\w", "_", False, id="word-class-ascii-punctuation"),
        pytest.param(r"\w", "\u0300", True, id="word-class-combining-mark"),
        pytest.param(r"\w", "\u00a0", False, id="word-class-separator"),
        pytest.param(r"\w", "\u037e", False, id="word-class-unicode-punctuation"),
        pytest.param(r"\w", "\ue000", False, id="word-class-private-use"),
        pytest.param(r"\w", "中", True, id="word-class-full-page"),
        pytest.param(r"\w", "😀", True, id="word-class-emoji"),
        pytest.param(r"\W", "_", True, id="nonword-class-punctuation"),
        pytest.param(r"\W", "$", False, id="nonword-class-symbol"),
        pytest.param(r"[\w]", "°", True, id="word-class-in-brackets"),
        pytest.param(r"[\w]", "_", False, id="word-class-in-brackets-punctuation"),
        pytest.param(r"\i", "a", True, id="name-start-letter"),
        pytest.param(r"\i", ":", True, id="name-start-colon"),
        pytest.param(r"\i", "_", True, id="name-start-underscore"),
        pytest.param(r"\i", "1", False, id="name-start-digit"),
        pytest.param(r"\i", "\u0300", False, id="name-start-combining-mark"),
        pytest.param(r"\i", "中", True, id="name-start-cjk"),
        pytest.param(r"\i", "😀", True, id="name-start-supplementary"),
        pytest.param(r"\I", "1", True, id="not-name-start-digit"),
        pytest.param(r"\I", "a", False, id="not-name-start-letter"),
        pytest.param(r"\c", "1", True, id="name-char-digit"),
        pytest.param(r"\c", "-", True, id="name-char-hyphen"),
        pytest.param(r"\c", "\u0300", True, id="name-char-combining-mark"),
        pytest.param(r"\c", "\u037e", False, id="name-char-greek-question-mark"),
        pytest.param(r"\C", "\u037e", True, id="not-name-char-punctuation"),
        pytest.param(r"\C", "a", False, id="not-name-char-letter"),
        pytest.param(r"[\i]", ":", True, id="name-start-in-brackets"),
        pytest.param(r"[\i]", "1", False, id="name-start-in-brackets-digit"),
        pytest.param(r"[\c]", "-", True, id="name-char-in-brackets"),
        pytest.param(r"[\c]", "\u037e", False, id="name-char-in-brackets-punctuation"),
        pytest.param(r"[\I]", "1", True, id="not-name-start-in-brackets"),
        pytest.param(r"[\I]", "a", False, id="not-name-start-in-brackets-letter"),
        pytest.param(r"[\C]", "\u037e", True, id="not-name-char-in-brackets"),
        pytest.param(r"[\C]", "a", False, id="not-name-char-in-brackets-letter"),
        pytest.param(r"[\d.]+", "3.14", True, id="class-with-builtin"),
        pytest.param(r"\D+", "abc", True, id="not-digit"),
        pytest.param("colou?r", "color", True, id="optional-u"),
        pytest.param("a*", "", True, id="star-empty"),
        pytest.param(r"a\.b", "a.b", True, id="escaped-dot"),
        pytest.param("(){999999999999}", "", True, id="huge-count-of-empty-group"),
        pytest.param("((){999999999999}){999999999999}", "", True, id="nested-huge-counts-of-empty-group"),
        pytest.param("((a{0}){999999999999}){999999999999}", "", True, id="nested-huge-counts-of-zero-repeat"),
        pytest.param("(()*){999999999999}b", "b", True, id="huge-count-of-starred-empty-group"),
        pytest.param("a{0}", "", True, id="zero-repeat-empty"),
        pytest.param("a{0,0}b", "ab", False, id="zero-repeat-drops-its-atom"),
        pytest.param("()+a()?", "a", True, id="empty-groups-around-a-character"),
        pytest.param("()|a", "", True, id="empty-group-branch"),
        pytest.param("((a{1}){1}b{1,1}){2}", "abab", True, id="single-repeat-is-its-atom"),
        pytest.param("a{1,}b{1,2}", "aabb", True, id="repeat-from-one"),
    ],
)
def test_regex_pattern(pattern: str, value: str, ok: bool) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]  # a pytest parametrize value, not a boolean-trap call site
    assert check(restricted(f'<xs:pattern value="{pattern}"/>'), f"<v>{value}</v>").valid is ok


@pytest.mark.parametrize(
    ("category", "value"),
    [
        pytest.param("Cc", "\t", id="control"),
        pytest.param("Cf", "\u00ad", id="format"),
        pytest.param("Cn", "\u0378", id="unassigned"),
        pytest.param("Co", "\ue000", id="private-use"),
        pytest.param("Ll", "a", id="lowercase"),
        pytest.param("Lm", "\u02b0", id="modifier-letter"),
        pytest.param("Lo", "\u00aa", id="other-letter"),
        pytest.param("Lt", "\u01c5", id="titlecase"),
        pytest.param("Lu", "A", id="uppercase"),
        pytest.param("Mc", "\u0903", id="spacing-mark"),
        pytest.param("Me", "\u0488", id="enclosing-mark"),
        pytest.param("Mn", "\u0300", id="nonspacing-mark"),
        pytest.param("Nd", "0", id="decimal-digit"),
        pytest.param("Nl", "\u16ee", id="letter-number"),
        pytest.param("No", "²", id="other-number"),
        pytest.param("Pc", "_", id="connector-punctuation"),
        pytest.param("Pd", "-", id="dash-punctuation"),
        pytest.param("Pe", ")", id="close-punctuation"),
        pytest.param("Pf", "»", id="final-punctuation"),
        pytest.param("Pi", "«", id="initial-punctuation"),
        pytest.param("Po", "!", id="other-punctuation"),
        pytest.param("Ps", "(", id="open-punctuation"),
        pytest.param("Sc", "$", id="currency-symbol"),
        pytest.param("Sk", "^", id="modifier-symbol"),
        pytest.param("Sm", "+", id="math-symbol"),
        pytest.param("So", "¦", id="other-symbol"),
        pytest.param("Zl", "\u2028", id="line-separator"),
        pytest.param("Zp", "\u2029", id="paragraph-separator"),
        pytest.param("Zs", " ", id="space-separator"),
    ],
)
def test_regex_category_property(category: str, value: str) -> None:
    assert check(restricted(f'<xs:pattern value="\\p{{{category}}}"/>'), f"<v>{value}</v>").valid


@pytest.mark.parametrize(
    "pattern",
    [
        pytest.param("[z-a]", id="reversed-ascii"),
        pytest.param("[😀-A]", id="reversed-supplementary"),
        pytest.param(r"[a-\n]", id="reversed-escaped-endpoint"),
        pytest.param(r"[a-\d]", id="builtin-endpoint"),
        pytest.param(r"[a-\p{Lu}]", id="category-endpoint"),
        pytest.param(r"[a-\q]", id="unknown-escape-endpoint"),
        pytest.param("[a-\\Ā]", id="nonascii-escape-endpoint"),
        pytest.param(r"[a-\]", id="reversed-closing-bracket-endpoint"),
        pytest.param("[a-\\", id="range-backslash-at-end"),
    ],
)
def test_regex_invalid_range_raises(pattern: str) -> None:
    with pytest.raises(ValueError, match=r"^schema pattern has an invalid character range at offset 1$"):
        XMLSchema(restricted(f'<xs:pattern value="{pattern}"/>'))


@pytest.mark.parametrize(
    "pattern",
    [
        pytest.param("[]", id="empty"),
        pytest.param("[^]", id="negated-empty"),
        pytest.param("[a", id="unclosed"),
        pytest.param("[abc", id="unclosed-multiple-chars"),
        pytest.param("[a\\", id="unclosed-backslash"),
        pytest.param("[a[b]", id="unescaped-opening-bracket"),
        pytest.param("[a^]", id="unescaped-caret"),
        pytest.param("[a-b-c]", id="middle-hyphen"),
        pytest.param(r"[\d-a]", id="middle-hyphen-after-builtin"),
        pytest.param(r"[\p{Lu}-a]", id="middle-hyphen-after-category"),
        pytest.param("[a-[b]c]", id="trailing-subtraction-content"),
        pytest.param("[a-[b]", id="unclosed-subtraction"),
        pytest.param("[-[a]]", id="subtraction-without-base"),
        pytest.param("[a-", id="unclosed-trailing-hyphen"),
        pytest.param("[", id="open-only"),
        pytest.param(r"[a\]", id="escaped-close-without-terminator"),
    ],
)
def test_regex_invalid_class_raises(pattern: str) -> None:
    with pytest.raises(ValueError, match=r"^schema pattern has an invalid character class at offset \d+$"):
        XMLSchema(restricted(f'<xs:pattern value="{pattern}"/>'))


@pytest.mark.parametrize(
    ("pattern", "offset"),
    [
        pytest.param(r"\q", 0, id="unknown-atom-escape"),
        pytest.param(r"a\q", 1, id="unknown-atom-escape-after-literal"),
        pytest.param(r"(a\q)", 2, id="unknown-escape-in-group"),
        pytest.param(r"[a\q]", 2, id="unknown-class-escape"),
        pytest.param(r"\u0041", 0, id="nonstandard-unicode-escape"),
        pytest.param("\\Ā", 0, id="nonascii-escape"),
        pytest.param("a\\", 1, id="trailing-backslash"),
    ],
)
def test_regex_invalid_escape_raises(pattern: str, offset: int) -> None:
    with pytest.raises(ValueError, match=rf"^schema pattern has an invalid escape at offset {offset}$"):
        XMLSchema(restricted(f'<xs:pattern value="{pattern}"/>'))


@pytest.mark.parametrize(
    ("pattern", "offset"),
    [
        pytest.param("(", 0, id="open-only"),
        pytest.param("(ab", 0, id="unclosed"),
        pytest.param("((ab)", 0, id="unclosed-outer"),
        pytest.param(")", 0, id="close-only"),
        pytest.param("a)", 1, id="unmatched-close"),
        pytest.param("a)b", 1, id="discarded-suffix"),
        pytest.param("(a))", 3, id="extra-close"),
    ],
)
def test_regex_unmatched_parenthesis_raises(pattern: str, offset: int) -> None:
    with pytest.raises(ValueError, match=rf"^schema pattern has an unmatched parenthesis at offset {offset}$"):
        XMLSchema(restricted(f'<xs:pattern value="{pattern}"/>'))


@pytest.mark.parametrize(
    ("pattern", "offset"),
    [
        pytest.param("?a", 0, id="leading-question"),
        pytest.param("*a", 0, id="leading-star"),
        pytest.param("+a", 0, id="leading-plus"),
        pytest.param("a?+", 2, id="stacked-question-plus"),
        pytest.param("a**", 2, id="stacked-stars"),
        pytest.param("a++", 2, id="stacked-pluses"),
        pytest.param("a{2", 1, id="unclosed-exact"),
        pytest.param("a{2,3", 1, id="unclosed-range"),
        pytest.param("a{2,", 1, id="unclosed-minimum"),
        pytest.param("a{2,x}", 1, id="nondigit-upper"),
        pytest.param("a{b", 1, id="nondigit-bound"),
        pytest.param("a{2,!}", 1, id="punctuation-upper"),
        pytest.param("a{2x", 1, id="missing-comma"),
        pytest.param("a{,2}", 1, id="missing-lower"),
        pytest.param("a{}", 1, id="empty-bound"),
        pytest.param("{2}", 0, id="bare-bound"),
        pytest.param("{x}", 0, id="bare-nondigit-bound"),
        pytest.param("}", 0, id="bare-close"),
        pytest.param("a}", 1, id="close-after-atom"),
    ],
)
def test_regex_invalid_quantifier_raises(pattern: str, offset: int) -> None:
    with pytest.raises(ValueError, match=rf"^schema pattern has an invalid quantifier at offset {offset}$"):
        XMLSchema(restricted(f'<xs:pattern value="{pattern}"/>'))


@pytest.mark.parametrize(
    ("pattern", "offset"),
    [
        pytest.param("]", 0, id="close-only"),
        pytest.param("a]", 1, id="close-after-literal"),
        pytest.param("a]b", 1, id="discarded-suffix"),
    ],
)
def test_regex_unmatched_bracket_raises(pattern: str, offset: int) -> None:
    with pytest.raises(ValueError, match=rf"^schema pattern has an unmatched closing bracket at offset {offset}$"):
        XMLSchema(restricted(f'<xs:pattern value="{pattern}"/>'))


_NESTING_ERROR: Final[str] = (
    "schema pattern nests groups deeper than 250 levels at offset 250; flatten the nested groups"
)
_STATES_ERROR: Final[str] = "schema pattern needs more than 2000000 NFA states; lower its repeat counts"


@pytest.mark.parametrize(
    ("pattern", "message"),
    [
        pytest.param(f"{'(' * 50000}a{')' * 50000}", _NESTING_ERROR, id="nested-groups"),
        pytest.param(f"{'(' * 251}a{')' * 251}", _NESTING_ERROR, id="nesting-one-past-cap"),
        pytest.param(
            f"[{'a-[' * 251}b{']' * 252}",
            "schema pattern nests character classes deeper than 250 levels at offset 752; flatten the class",
            id="class-subtraction-nesting-one-past-cap",
        ),
        pytest.param(r"\p{Bad}", "schema pattern has an invalid category escape at offset 3", id="invalid-category"),
        pytest.param(r"\p{Cs}", "schema pattern has an invalid category escape at offset 3", id="surrogate-category"),
        pytest.param(
            r"[\p{Bad}]", "schema pattern has an invalid category escape at offset 4", id="invalid-category-in-class"
        ),
        pytest.param(r"\p{Lu", "schema pattern has an invalid category escape at offset 3", id="open-category"),
        pytest.param(r"\p", "schema pattern has an invalid category escape at offset 2", id="bare-category"),
        pytest.param(r"\pa", "schema pattern has an invalid category escape at offset 2", id="category-without-braces"),
        pytest.param(r"\p{IsUnknown}", "schema pattern has an invalid category escape at offset 3", id="invalid-block"),
        pytest.param(
            r"\p{IxBasicLatin}", "schema pattern has an invalid category escape at offset 3", id="invalid-block-prefix"
        ),
        pytest.param(
            r"\p{IsHighSurrogates}",
            "schema pattern has an invalid category escape at offset 3",
            id="high-surrogates-block",
        ),
        pytest.param(
            r"\p{IsLowSurrogates}",
            "schema pattern has an invalid category escape at offset 3",
            id="low-surrogates-block",
        ),
        pytest.param(
            r"\p{IsHighPrivateUseSurrogates}",
            "schema pattern has an invalid category escape at offset 3",
            id="private-use-surrogates-block",
        ),
        pytest.param("(a{5000}){5000}", _STATES_ERROR, id="repeat-product"),
        pytest.param("(a{0,3000}){0,3000}", _STATES_ERROR, id="optional-repeat-product"),
        pytest.param("a{999999999999}", _STATES_ERROR, id="huge-count"),
        pytest.param("((){999999999999}a){999999999999}", _STATES_ERROR, id="huge-count-of-empty-group-per-state"),
        pytest.param(
            "a{5,2}",
            "schema pattern quantifier {5,2} at offset 1 has its minimum above its maximum",
            id="inverted-bound",
        ),
    ],
)
def test_regex_pattern_past_a_limit_raises(pattern: str, message: str) -> None:
    with pytest.raises(ValueError, match=f"^{re.escape(message)}$"):
        XMLSchema(restricted(f'<xs:pattern value="{pattern}"/>'))


@pytest.mark.parametrize(
    "patterns",
    [
        pytest.param(("a{1500000}", "b{600000}"), id="one-past-budget"),
        pytest.param((f"a{{{2_000_000 - 1}}}", "b"), id="nothing-left"),
        pytest.param(("a{1000000}", "a{999999}", "a{999998}"), id="counts-differ"),
    ],
)
def test_regex_patterns_past_the_schema_state_budget_raise(patterns: tuple[str, ...]) -> None:
    # the compiled automata stay in the schema arena, so the budget is shared by all patterns of a schema
    message = "schema patterns need more than 2000000 NFA states between them; lower their repeat counts"
    with pytest.raises(ValueError, match=f"^{re.escape(message)}$"):
        XMLSchema(restricted("".join(f'<xs:pattern value="{pattern}"/>' for pattern in patterns)))


@pytest.mark.parametrize(
    "patterns",
    [
        pytest.param(("a{999999}", "b{999999}"), id="at-budget"),
        pytest.param(("a{1000000}",) * 3, id="same-text-compiled-once"),
    ],
)
def test_regex_patterns_within_the_schema_state_budget_compile(patterns: tuple[str, ...]) -> None:
    schema = XMLSchema(restricted("".join(f'<xs:pattern value="{pattern}"/>' for pattern in patterns)))
    assert not schema.validate(parse_xml("<v>c</v>")).valid


def test_regex_patterns_of_different_sizes_share_the_match_buffers() -> None:
    # the match buffers are sized once per validate() call by the largest pattern, so a smaller pattern compiled after
    # a larger one must not shrink them
    schema = XMLSchema(
        f'<xs:schema {_XS}><xs:element name="r"><xs:complexType><xs:sequence>'
        '<xs:element name="big"><xs:simpleType><xs:restriction base="xs:string"><xs:pattern value="a{2000}"/>'
        "</xs:restriction></xs:simpleType></xs:element>"
        '<xs:element name="small"><xs:simpleType><xs:restriction base="xs:string"><xs:pattern value="b"/>'
        "</xs:restriction></xs:simpleType></xs:element>"
        "</xs:sequence></xs:complexType></xs:element></xs:schema>"
    )
    results = [
        schema.validate(parse_xml(f"<r><big>{big}</big><small>{small}</small></r>")).valid
        for big, small in (("a" * 2000, "b"), ("a" * 1999, "b"), ("a" * 2000, "c"))
    ]
    assert results == [True, False, False]


def test_xsd_child_declares_its_own_namespace() -> None:
    schema: Final[XMLSchema] = XMLSchema(
        f'<xs:schema {_XS} targetNamespace="urn:a" xmlns="urn:a" elementFormDefault="qualified">'
        '<xs:element name="root"><xs:complexType><xs:sequence>'
        '<xs:element name="c" type="xs:string"/></xs:sequence></xs:complexType></xs:element></xs:schema>'
    )
    assert schema.validate(parse_xml('<root xmlns="urn:a"><c xmlns="urn:a">x</c></root>')).valid
    assert not schema.validate(parse_xml('<root xmlns="urn:a"><c xmlns="">x</c></root>')).valid


def test_xsd_element_below_the_root_sees_declarations_above_it() -> None:
    schema: Final[XMLSchema] = XMLSchema(
        f'<xs:schema {_XS} targetNamespace="urn:x" xmlns="urn:x"><xs:element name="b" type="xs:string"/></xs:schema>'
    )
    element: Final[Node] = parse_xml('<a xmlns="urn:x"><b>v</b></a>').children[0].children[0]
    assert schema.validate(element).valid


def test_xsd_prefix_skips_a_same_length_neighbor() -> None:
    # the type's prefix "tn" must find its own declaration past "ts", which is as long and binds another namespace
    schema: Final[XMLSchema] = XMLSchema(
        f'<xs:schema {_XS} xmlns:ts="urn:s" xmlns:tn="urn:t" targetNamespace="urn:t" elementFormDefault="qualified">'
        '<xs:complexType name="T"><xs:sequence><xs:element name="c" type="xs:string"/></xs:sequence></xs:complexType>'
        '<xs:element name="root" type="tn:T"/></xs:schema>'
    )
    assert schema.validate(parse_xml('<tn:root xmlns:tn="urn:t"><tn:c>x</tn:c></tn:root>')).valid
    assert not schema.validate(parse_xml('<tn:root xmlns:tn="urn:t"><c>x</c></tn:root>')).valid


def test_xsd_many_attributes_and_children_scale_linearly(work_steps: WorkSteps) -> None:
    # each child's name resolves against the namespace declarations of the element holding it, which must cost the same
    # whatever that element's attribute count
    def build(count: int) -> Callable[[], ValidationResult]:
        attributes = "".join(f'<xs:attribute name="a{index}" type="xs:string"/>' for index in range(count))
        schema: Final[XMLSchema] = XMLSchema(
            f'<xs:schema {_XS}><xs:element name="root"><xs:complexType><xs:sequence>'
            '<xs:element name="c" type="xs:string" minOccurs="0" maxOccurs="unbounded"/>'
            f"</xs:sequence>{attributes}</xs:complexType></xs:element></xs:schema>"
        )
        attrs = "".join(f' a{index}="x"' for index in range(count))
        return partial(schema.validate, parse_xml(f"<root{attrs}>{'<c>x</c>' * count}</root>"))

    work_steps.assert_linear(build, (250, 1000))


def test_nested_repeats_of_an_empty_group_scale_linearly(work_steps: WorkSteps) -> None:
    def build(count: int) -> Callable[[], XMLSchema]:
        return partial(XMLSchema, restricted(f'<xs:pattern value="((){{{count}}}a){{{count}}}"/>'))

    work_steps.assert_linear(build, (100, 400))


@pytest.mark.parametrize(
    ("declaration", "definitions", "item"),
    [
        pytest.param("<xs:complexType>{children}<xs:sequence/></xs:complexType>", "", "<g/>", id="content-model"),
        pytest.param(
            '<xs:complexType><xs:sequence>{children}<xs:element name="x" minOccurs="0"/></xs:sequence>'
            "</xs:complexType>",
            "",
            "<g/>",
            id="sequence-children",
        ),
        pytest.param(
            '<xs:complexType><xs:choice>{children}<xs:element name="x" minOccurs="0"/></xs:choice></xs:complexType>',
            "",
            "<g/>",
            id="choice-children",
        ),
        pytest.param(
            '<xs:complexType><xs:complexContent>{children}<xs:extension base="base"/></xs:complexContent>'
            "</xs:complexType>",
            '<xs:complexType name="base"/>',
            "<g/>",
            id="complex-content",
        ),
        pytest.param(
            '<xs:complexType><xs:simpleContent>{children}<xs:extension base="xs:string"/></xs:simpleContent>'
            "</xs:complexType>",
            "",
            "<g>text</g>",
            id="simple-content",
        ),
        pytest.param(
            '<xs:complexType><xs:group ref="group"/></xs:complexType>',
            '<xs:group name="group">{children}<xs:sequence/></xs:group>',
            "<g/>",
            id="group-definition",
        ),
        pytest.param(
            '<xs:complexType><xs:attributeGroup ref="group"/></xs:complexType>',
            '<xs:attributeGroup name="group">{children}<xs:attribute name="a"/></xs:attributeGroup>',
            '<g a=""/>',
            id="attribute-group-definition",
        ),
        pytest.param(
            '<xs:complexType><xs:attribute name="a">{children}<xs:simpleType><xs:restriction base="xs:int"/>'
            "</xs:simpleType></xs:attribute></xs:complexType>",
            "",
            '<g a="1"/>',
            id="attribute-declaration",
        ),
        pytest.param("{children}<xs:complexType/>", "", "<g/>", id="element-declaration"),
    ],
)
def test_schema_children_are_not_searched_per_element(
    work_steps: WorkSteps, declaration: str, definitions: str, item: str
) -> None:
    # Each <g> reads the children of its declaration and of the definitions it reaches. With as many unrelated children
    # as there are <g> elements, a scan per <g> grows as the square of the count. The schema is compiled inside the
    # measured call as well, so the count still grows on a path whose validation counts no steps.
    def build(count: int) -> Callable[[], None]:
        children = "<ignored/>" * count
        source: Final[str] = (
            f'<xs:schema {_XS}>{definitions.format(children=children)}<xs:element name="r">'
            "<xs:complexType><xs:sequence>"
            f'<xs:element name="g" maxOccurs="unbounded">{declaration.format(children=children)}</xs:element>'
            "</xs:sequence></xs:complexType></xs:element></xs:schema>"
        )
        document: Final[Document] = parse_xml(f"<r>{item * count}</r>")

        def validate() -> None:
            assert XMLSchema(source).validate(document).valid

        return validate

    work_steps.assert_linear(build, (100, 400))


@pytest.mark.parametrize(
    ("content", "xml", "messages"),
    [
        pytest.param(
            '<xs:choice><xs:element name="b"/></xs:choice><ignored/><xs:sequence><xs:element name="a"/></xs:sequence>'
            '<xs:sequence><xs:element name="c"/></xs:sequence>',
            "<r><a/></r>",
            [],
            id="first-sequence-before-a-choice",
        ),
        pytest.param(
            '<xs:group ref="late"/><xs:all><xs:element name="a"/></xs:all>',
            "<r><b/></r>",
            ["element 'b' is not allowed here", "required element 'a' is missing"],
            id="all-before-a-group",
        ),
        pytest.param(
            '<xs:annotation/><xs:group ref="late"/><!-- the model --><xs:group ref="unused"/>',
            "<r><b/></r>",
            [],
            id="first-group",
        ),
        pytest.param(
            '<xs:attribute name="z" use="required"/><ignored/>text<xs:attributeGroup ref="attrs"/><xs:complexContent>'
            '<xs:restriction base="other"/><xs:extension base="base"/></xs:complexContent>'
            '<xs:attribute name="a" use="required"/>',
            "<r/>",
            [
                "required attribute 'z' is missing",
                "required attribute 'grouped' is missing",
                "required attribute 'inherited' is missing",
                "required attribute 'a' is missing",
            ],
            id="attribute-declarations-in-document-order",
        ),
    ],
)
def test_schema_children_found_as_the_schema_orders_them(content: str, xml: str, messages: list[str]) -> None:
    result: Final[ValidationResult] = check(
        f'<xs:schema {_XS}><xs:group name="late"><ignored/><xs:choice><xs:element name="b"/></xs:choice>'
        '<xs:sequence><xs:element name="c"/></xs:sequence></xs:group>'
        '<xs:group name="unused"><xs:sequence><xs:element name="c"/></xs:sequence></xs:group>'
        '<xs:complexType name="base"><xs:attribute name="inherited" use="required"/></xs:complexType>'
        '<xs:complexType name="other"><xs:attribute name="skipped" use="required"/></xs:complexType>'
        '<xs:attributeGroup name="attrs"><xs:attribute name="grouped" use="required"/></xs:attributeGroup>'
        f'<xs:element name="r"><xs:complexType>{content}</xs:complexType></xs:element></xs:schema>',
        xml,
    )
    assert [error.message for error in result.errors] == messages


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        pytest.param("", True, id="matches-every-pattern"),
        pytest.param("z", True, id="matches-every-pattern-by-its-first-branch"),
        pytest.param("00007", True, id="matches-one-pattern-as-an-alternative"),
        pytest.param("b", False, id="matches-no-pattern"),
    ],
)
def test_regex_many_patterns_on_one_type(value: str, *, valid: bool) -> None:
    assert check(restricted(_many_pattern_facets(240)), f"<v>{value}</v>").valid is valid


def _many_pattern_facets(count: int) -> str:
    return "".join(f'<xs:pattern value="z?|{number:05d}"/>' for number in range(count))


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        pytest.param("abc01", True, id="matches-first-pattern"),
        pytest.param("abc12", True, id="matches-second-pattern"),
        pytest.param("abc", False, id="matches-no-pattern"),
    ],
)
def test_pattern_facets_of_one_step_match_any(value: str, *, valid: bool) -> None:
    assert check(restricted(_pattern_facets(("[a-z]+0[0-9]+", "[a-z]+1[0-9]+"))), f"<v>{value}</v>").valid is valid


def _pattern_facets(patterns: tuple[str, ...]) -> str:
    return "".join(f'<xs:pattern value="{pattern}"/>' for pattern in patterns)


def test_pattern_facets_of_one_step_match_any_in_a_repeated_element() -> None:
    schema: Final[XMLSchema] = XMLSchema(
        f'<xs:schema {_XS}><xs:simpleType name="item"><xs:restriction base="xs:string">'
        f"{_pattern_facets(('[a-z]+0[0-9]+', '[a-z]+1[0-9]+'))}</xs:restriction></xs:simpleType>"
        '<xs:element name="root"><xs:complexType><xs:sequence>'
        '<xs:element name="value" type="item" maxOccurs="unbounded"/></xs:sequence></xs:complexType></xs:element>'
        "</xs:schema>"
    )
    assert schema.validate(parse_xml("<root><value>abc123</value></root>")).valid
    assert not schema.validate(parse_xml("<root><value>abc</value></root>")).valid


@pytest.mark.parametrize(
    ("base", "restriction", "value", "valid"),
    [
        pytest.param(("[a-z]{3}",), ("[a-z]{3}", "[0-9]{3}"), "abc", True, id="matches-both-steps"),
        pytest.param(("[a-z]{3}",), ("[a-z]{3}", "[0-9]{3}"), "123", False, id="restriction-alternative-fails-base"),
        pytest.param(("[a-z]{3}", "[0-9]{3}"), ("[a-z]{3}",), "123", False, id="base-alternative-fails-restriction"),
        pytest.param(("[a-z]{3}", "[0-9]{3}"), ("[a-z]{3}",), "abc", True, id="base-alternatives-pass-restriction"),
    ],
)
def test_pattern_facets_of_each_derivation_step_all_apply(
    base: tuple[str, ...], restriction: tuple[str, ...], value: str, *, valid: bool
) -> None:
    schema: Final[XMLSchema] = XMLSchema(
        f'<xs:schema {_XS}><xs:simpleType name="base"><xs:restriction base="xs:string">'
        f"{_pattern_facets(base)}</xs:restriction></xs:simpleType>"
        '<xs:element name="v"><xs:simpleType><xs:restriction base="base">'
        f"{_pattern_facets(restriction)}</xs:restriction></xs:simpleType></xs:element></xs:schema>"
    )
    assert schema.validate(parse_xml(f"<v>{value}</v>")).valid is valid


def test_regex_many_patterns_compile_in_linear_steps(work_steps: WorkSteps) -> None:
    work_steps.assert_linear(lambda count: lambda: XMLSchema(restricted(_many_pattern_facets(count))), (150, 600))


def test_regex_many_patterns_validate_in_linear_steps(work_steps: WorkSteps) -> None:
    def build(count: int) -> Callable[[], object]:
        schema: Final[XMLSchema] = XMLSchema(
            f'<xs:schema {_XS}><xs:simpleType name="t"><xs:restriction base="xs:string">'
            f"{_many_pattern_facets(count)}</xs:restriction></xs:simpleType>"
            '<xs:element name="r"><xs:complexType><xs:sequence>'
            '<xs:element name="v" type="t" maxOccurs="unbounded"/></xs:sequence></xs:complexType></xs:element>'
            "</xs:schema>"
        )
        document: Final[Document] = parse_xml(f"<r>{'<v/>' * 20}</r>")

        def validate() -> None:
            assert schema.is_valid(document)

        return validate

    work_steps.assert_linear(build, (150, 600))


@pytest.mark.parametrize(
    ("template", "value"),
    [
        pytest.param("[{}]*", "b", id="class"),
        pytest.param("[^{}]*", "z", id="negated-class"),
        pytest.param(r"[\w-[{}]]*", "z", id="class-subtracting-the-ranges"),
        pytest.param(r"[\p{{Lu}}{}]*", "b", id="class-with-category"),
    ],
)
def test_regex_class_of_many_ranges_matches_in_linear_steps(template: str, value: str, work_steps: WorkSteps) -> None:
    def build(members: int) -> Callable[[], object]:
        pattern: Final[str] = template.format("a-c" * members)
        schema: Final[XMLSchema] = XMLSchema(restricted(f'<xs:pattern value="{pattern}"/>'))
        document: Final[Document] = parse_xml(f"<v>{value * members}</v>")
        return partial(schema.is_valid, document)

    work_steps.assert_linear(build, (500, 2000))


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        pytest.param("abab", True, id="matches-a-repeated-pattern"),
        pytest.param("c", True, id="matches-a-repeated-literal"),
        pytest.param("b", False, id="matches-no-pattern"),
    ],
)
def test_regex_repeated_patterns_on_one_type(value: str, *, valid: bool) -> None:
    # equal texts compile once, and every facet that repeats one finds that compiled form
    patterns: Final[tuple[str, ...]] = ("(ab)+", "c", "(ab)+", "d", "c")
    facets: Final[str] = "".join(f'<xs:pattern value="{pattern}"/>' for pattern in patterns)
    assert check(restricted(facets), f"<v>{value}</v>").valid is valid


def test_regex_empty_pattern_matches_only_the_empty_string() -> None:
    schema: Final[XMLSchema] = XMLSchema(
        restricted('<xs:pattern value=""/><xs:pattern value="b"/><xs:pattern value=""/>')
    )
    assert [schema.is_valid(parse_xml(f"<v>{value}</v>")) for value in ("", "b", "a")] == [True, True, False]


@pytest.mark.parametrize(
    ("patterns", "message"),
    [
        pytest.param(("a", "(", "[z-a]"), "schema pattern has an unmatched parenthesis", id="shorter-first"),
        pytest.param(("[z-a]", "a", "("), "schema pattern has an invalid character range", id="longer-first"),
        pytest.param(("b{2,1}", "a", "a{3,1}"), "schema pattern quantifier {2,1} at offset 1", id="greater-first"),
    ],
)
def test_regex_first_invalid_pattern_in_document_order_is_reported(patterns: tuple[str, ...], message: str) -> None:
    facets: Final[str] = "".join(f'<xs:pattern value="{pattern}"/>' for pattern in patterns)
    with pytest.raises(ValueError, match=f"^{re.escape(message)}"):
        XMLSchema(restricted(facets))


@pytest.mark.parametrize(
    ("pattern", "value", "outcomes"),
    [
        pytest.param("a{150000}", "a" * 150000, ("True\n",), id="large-repeat"),
        pytest.param("a" * 200000, "a" * 200000, ("True\n",), id="long-literal"),
        pytest.param(f"a{'|a' * 100000}", "a", ("True\n",), id="long-alternation"),
        # the one pattern that recurses once per group: a debug build may need more stack than the thread leaves
        pytest.param(f"{'(' * 250}a{')' * 250}", "a", ("True\n", "RecursionError\n"), id="nesting-at-cap"),
    ],
)
def test_regex_large_pattern_compiles_and_matches(pattern: str, value: str, outcomes: tuple[str, ...]) -> None:
    # compiling and matching both walk these patterns without recursing per piece, so they fit the 128 KiB thread
    # stack uv's musl CPython starts threads with; a subprocess turns a regression into a clean failure instead of a
    # killed worker, and the input goes in on stdin because Linux caps a single command-line argument at 128 KiB
    code = (
        "import json, sys, threading\nfrom turbohtml import parse_xml\nfrom turbohtml.validate import XMLSchema\n"
        "data = json.load(sys.stdin)\nthreading.stack_size(128 * 1024)\n"
        "def run():\n    try:\n        print(XMLSchema(data['schema']).validate(parse_xml(data['xml'])).valid)\n"
        "    except RecursionError:\n        print('RecursionError')\n"
        "worker = threading.Thread(target=run)\n"
        "worker.start()\nworker.join()\n"
    )
    result = subprocess.run(  # ruff:ignore[subprocess-without-shell-equals-true]  # argv is a fixed list, so no shell is involved
        [sys.executable, "-c", code],
        input=json.dumps({"schema": restricted(f'<xs:pattern value="{pattern}"/>'), "xml": f"<v>{value}</v>"}),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout in outcomes, result.stderr


def test_sequence_and_occurs() -> None:
    schema = (
        f'<xs:schema {_XS}><xs:element name="r"><xs:complexType><xs:sequence>'
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
        f'<xs:schema {_XS}><xs:element name="r"><xs:complexType><xs:choice>'
        '<xs:element name="a" type="xs:string"/><xs:element name="b" type="xs:string"/>'
        "</xs:choice></xs:complexType></xs:element></xs:schema>"
    )
    assert check(schema, "<r><a>x</a></r>").valid
    assert check(schema, "<r><b>x</b></r>").valid
    assert not check(schema, "<r><a>x</a><b>y</b></r>").valid


_RUN_OF_THREE: Final[str] = (
    '<xs:sequence><xs:element name="a"/><xs:element name="a"/><xs:element name="a"/></xs:sequence>'
)
_RUN_OF_TWO: Final[str] = '<xs:sequence><xs:element name="a"/><xs:element name="a"/></xs:sequence>'
_ONE: Final[str] = '<xs:element name="a"/>'


@pytest.mark.parametrize(
    ("alternatives", "children", "valid"),
    [
        pytest.param(_RUN_OF_THREE + _ONE, "ab", True, id="shorter-alternative-listed-last"),
        pytest.param(_RUN_OF_THREE + _ONE, "aaab", True, id="longer-alternative-listed-first"),
        pytest.param(_RUN_OF_THREE + _ONE, "aab", False, id="length-between-the-alternatives"),
        pytest.param(_RUN_OF_THREE + _ONE, "b", False, id="no-alternative"),
        pytest.param(_RUN_OF_THREE + _ONE + _RUN_OF_TWO, "aab", True, id="alternative-fills-the-gap"),
        pytest.param(_RUN_OF_THREE + _ONE + _RUN_OF_TWO, "aaaab", False, id="longer-than-every-alternative"),
    ],
)
def test_choice_alternatives_of_different_lengths(alternatives: str, children: str, *, valid: bool) -> None:
    # the alternatives stop at child positions that arrive out of order and leave holes between them, and the particle
    # after the choice must resume from exactly those positions
    schema = (
        f'<xs:schema {_XS}><xs:element name="r"><xs:complexType><xs:sequence>'
        f'<xs:choice>{alternatives}</xs:choice><xs:element name="b"/>'
        "</xs:sequence></xs:complexType></xs:element></xs:schema>"
    )
    assert check(schema, f"<r>{''.join(f'<{name}/>' for name in children)}</r>").valid is valid


_WILD_DECLARATION: Final[str] = '<xs:element name="b" type="xs:int"/>'


@pytest.mark.parametrize(
    ("particle", "children", "valid"),
    [
        pytest.param('<xs:any processContents="skip"/>', "<a/>", False, id="required-needs-an-element"),
        pytest.param('<xs:any processContents="skip"/>', "<a/><c/>", True, id="required-consumes-one"),
        pytest.param('<xs:any processContents="skip"/>', "<a/><c/><d/>", False, id="required-consumes-one-only"),
        pytest.param('<xs:any minOccurs="0" processContents="skip"/>', "<a/>", True, id="optional-may-be-absent"),
        pytest.param('<xs:any maxOccurs="2" processContents="skip"/>', "<a/><c/><d/>", True, id="max-occurs-two"),
        pytest.param(
            '<xs:any maxOccurs="2" processContents="skip"/>', "<a/><c/><d/><e/>", False, id="max-occurs-two-exceeded"
        ),
        pytest.param(
            '<xs:any namespace="##other" processContents="skip"/>',
            "<a/><c/>",
            False,
            id="other-rejects-absent-namespace",
        ),
        pytest.param(
            '<xs:any namespace="##other" processContents="skip"/>',
            "<a/><x:c xmlns:x='urn:x'/>",
            True,
            id="other-admits-other-namespace",
        ),
        pytest.param(
            '<xs:any namespace="##local" processContents="skip"/>',
            "<a/><x:c xmlns:x='urn:x'/>",
            False,
            id="local-rejects-other-namespace",
        ),
        pytest.param(
            '<xs:any namespace="##local" processContents="skip"/>', "<a/><c/>", True, id="local-admits-absent"
        ),
        pytest.param("<xs:any/>", "<a/><b>1</b>", True, id="strict-finds-global-declaration"),
        pytest.param("<xs:any/>", "<a/><b>x</b>", False, id="strict-validates-global-declaration"),
        pytest.param("<xs:any/>", "<a/><c/>", False, id="strict-needs-global-declaration"),
        pytest.param('<xs:any processContents="skip"/>', "<a/><b>x</b>", True, id="skip-validates-nothing"),
        pytest.param('<xs:any processContents="lax"/>', "<a/><b>x</b>", False, id="lax-validates-declaration"),
        pytest.param('<xs:any processContents="lax"/>', "<a/><c/>", True, id="lax-without-declaration"),
    ],
)
def test_wildcard_particle_in_sequence(particle: str, children: str, *, valid: bool) -> None:
    schema = (
        f'<xs:schema {_XS}><xs:element name="r"><xs:complexType><xs:sequence><xs:element name="a"/>{particle}'
        f"</xs:sequence></xs:complexType></xs:element>{_WILD_DECLARATION}</xs:schema>"
    )
    assert check(schema, f"<r>{children}</r>").valid is valid


@pytest.mark.parametrize(
    ("children", "valid"),
    [
        pytest.param("<a/>", True, id="element-alternative"),
        pytest.param("<x:c xmlns:x='urn:x'/>", True, id="wildcard-alternative"),
        pytest.param("", False, id="no-alternative"),
        pytest.param("<c/>", False, id="absent-namespace-not-admitted"),
    ],
)
def test_wildcard_alternative_in_choice(children: str, *, valid: bool) -> None:
    schema = (
        f'<xs:schema {_XS}><xs:element name="r"><xs:complexType><xs:choice><xs:element name="a"/>'
        '<xs:any namespace="##other" processContents="skip"/></xs:choice></xs:complexType></xs:element></xs:schema>'
    )
    assert check(schema, f"<r>{children}</r>").valid is valid


@pytest.mark.parametrize(
    ("children", "valid"),
    [
        pytest.param("<x:c xmlns:x='urn:x'/><b>1</b>", True, id="second-wildcard-takes-the-declared-child"),
        pytest.param("<x:c xmlns:x='urn:x'/><b>x</b>", False, id="second-wildcard-validates-the-child"),
        pytest.param(
            "<x:c xmlns:x='urn:x'/><x:b xmlns:x='urn:x'>x</x:b>",
            False,
            id="second-wildcard-claims-the-namespaced-child",
        ),
    ],
)
def test_wildcard_pair_claims_each_child_once(children: str, *, valid: bool) -> None:
    # the first wildcard admits the namespaced child, but the second consumes it, so its strict declaration applies
    schema = (
        f'<xs:schema {_XS}><xs:element name="r"><xs:complexType><xs:sequence>'
        '<xs:any namespace="##other" processContents="skip"/><xs:any processContents="strict"/>'
        f"</xs:sequence></xs:complexType></xs:element>{_WILD_DECLARATION}</xs:schema>"
    )
    assert check(schema, f"<r>{children}</r>").valid is valid


@pytest.mark.parametrize(
    ("wildcard", "attributes", "valid"),
    [
        pytest.param('<xs:anyAttribute processContents="skip"/>', 'x="1" y="zz"', True, id="skip-admits-any"),
        pytest.param('<xs:anyAttribute namespace="##other"/>', 'y="1"', False, id="other-rejects-absent-namespace"),
        pytest.param('<xs:anyAttribute namespace="##local" processContents="skip"/>', 'y="1"', True, id="local-admits"),
        pytest.param('<xs:anyAttribute processContents="strict"/>', 'x="12"', True, id="strict-validates-declaration"),
        pytest.param('<xs:anyAttribute processContents="strict"/>', 'x="zz"', False, id="strict-rejects-bad-value"),
        pytest.param('<xs:anyAttribute processContents="strict"/>', 'y="1"', False, id="strict-needs-declaration"),
        pytest.param('<xs:anyAttribute processContents="lax"/>', 'x="zz"', False, id="lax-validates-declaration"),
        pytest.param('<xs:anyAttribute processContents="lax"/>', 'y="zz"', True, id="lax-without-declaration"),
        pytest.param("", 'y="1"', False, id="no-wildcard"),
    ],
)
def test_any_attribute_admits_undeclared_attributes(wildcard: str, attributes: str, *, valid: bool) -> None:
    schema = (
        f'<xs:schema {_XS}><xs:attribute name="x" type="xs:int"/><xs:element name="r"><xs:complexType>'
        f"<xs:sequence/>{wildcard}</xs:complexType></xs:element></xs:schema>"
    )
    assert check(schema, f"<r {attributes}/>").valid is valid


@pytest.mark.parametrize(
    ("namespace", "child", "valid"),
    [
        pytest.param("##any", "<x:c xmlns:x='urn:x'/>", True, id="any-admits-another-namespace"),
        pytest.param("##targetNamespace", "<t:c xmlns:t='urn:t'/>", True, id="target-admits-target"),
        pytest.param("##targetNamespace", "<c/>", False, id="target-rejects-absent"),
        pytest.param("##other", "<t:c xmlns:t='urn:t'/>", False, id="other-rejects-target"),
        pytest.param("##other", "<x:c xmlns:x='urn:x'/>", True, id="other-admits-third-namespace"),
        pytest.param("urn:x", "<x:c xmlns:x='urn:x'/>", True, id="uri-admits-its-namespace"),
        pytest.param("urn:x", "<t:c xmlns:t='urn:t'/>", False, id="uri-rejects-other-namespace"),
        pytest.param("urn:x ##local", "<c/>", True, id="list-admits-local"),
        pytest.param("  ##local  ", "<c/>", True, id="list-ignores-surrounding-space"),
    ],
)
def test_wildcard_namespace_constraint_in_target_namespace(namespace: str, child: str, *, valid: bool) -> None:
    schema = (
        f'<xs:schema {_XS} targetNamespace="urn:t" xmlns:t="urn:t"><xs:element name="r"><xs:complexType><xs:sequence>'
        f'<xs:element name="a"/><xs:any namespace="{namespace}" processContents="skip"/>'
        "</xs:sequence></xs:complexType></xs:element></xs:schema>"
    )
    assert check(schema, f"<t:r xmlns:t='urn:t'><a/>{child}</t:r>").valid is valid


@pytest.mark.parametrize(("processing", "valid"), [("strict", False), ("lax", True)])
def test_any_attribute_in_target_namespace_finds_no_global_attribute(processing: str, *, valid: bool) -> None:
    # a global attribute is in the target namespace, and an unprefixed attribute is in no namespace
    schema = (
        f'<xs:schema {_XS} targetNamespace="urn:t" xmlns:t="urn:t"><xs:attribute name="x" type="xs:int"/>'
        '<xs:element name="r"><xs:complexType><xs:sequence/>'
        f'<xs:anyAttribute processContents="{processing}"/></xs:complexType></xs:element></xs:schema>'
    )
    assert check(schema, "<t:r xmlns:t='urn:t' x='zz'/>").valid is valid


@pytest.mark.parametrize(
    ("particles", "children", "valid"),
    [
        pytest.param(
            '<xs:any minOccurs="0" processContents="strict"/><xs:element name="b" type="xs:int"/>',
            "<b>1</b>",
            True,
            id="named-element-beside-wildcard",
        ),
        pytest.param(
            '<xs:any minOccurs="0" processContents="strict"/><xs:element name="b" type="xs:int"/>',
            "<b>x</b>",
            False,
            id="named-element-declaration-wins",
        ),
    ],
)
def test_wildcard_before_named_element(particles: str, children: str, *, valid: bool) -> None:
    schema = (
        f'<xs:schema {_XS}><xs:element name="r"><xs:complexType><xs:sequence>{particles}</xs:sequence>'
        "</xs:complexType></xs:element></xs:schema>"
    )
    assert check(schema, f"<r>{children}</r>").valid is valid


def test_any_attribute_of_base_and_extension_admits_undeclared_attributes() -> None:
    schema = (
        f'<xs:schema {_XS}><xs:complexType name="base"><xs:sequence/>'
        '<xs:anyAttribute processContents="skip"/></xs:complexType>'
        '<xs:element name="r"><xs:complexType><xs:complexContent><xs:extension base="base"><xs:sequence/>'
        '<xs:anyAttribute processContents="skip"/></xs:extension></xs:complexContent></xs:complexType></xs:element>'
        "</xs:schema>"
    )
    assert check(schema, '<r y="1" z="2"/>').valid


@pytest.mark.parametrize(
    ("namespace", "attribute", "processing", "valid"),
    [
        pytest.param("##any", "x:a='1'", "strict", False, id="any-strict-needs-declaration"),
        pytest.param("##any", "x:a='1'", "lax", True, id="any-lax-admits-undeclared"),
        pytest.param("##any", "x:a='1'", "skip", True, id="any-skip-admits-undeclared"),
        pytest.param("##any", "t:g='zz'", "skip", True, id="any-skip-ignores-declaration"),
        pytest.param("##any", "t:g='zz'", "lax", False, id="any-lax-validates-declaration"),
        pytest.param("##any", "t:g='12'", "strict", True, id="any-strict-admits-valid-declaration"),
        pytest.param("##any", "xml:lang='en'", "strict", False, id="any-strict-xml-needs-declaration"),
        pytest.param("##any", "xml:lang='en'", "skip", True, id="any-skip-xml-admitted"),
        pytest.param("##other", "x:a='1'", "skip", True, id="other-admits-foreign-namespace"),
        pytest.param("##other", "t:g='zz'", "skip", False, id="other-rejects-target-namespace"),
        pytest.param("##other", "a='1'", "skip", False, id="other-rejects-absent-namespace"),
        pytest.param("##local", "a='1'", "skip", True, id="local-admits-unprefixed"),
        pytest.param("##local", "x:a='1'", "skip", False, id="local-rejects-prefixed"),
        pytest.param("##targetNamespace", "t:g='12'", "strict", True, id="target-strict-admits-declaration"),
        pytest.param("##targetNamespace", "t:g='zz'", "lax", False, id="target-lax-validates-declaration"),
        pytest.param("##targetNamespace", "x:a='1'", "skip", False, id="target-rejects-foreign-namespace"),
        pytest.param("urn:x", "x:a='1'", "skip", True, id="uri-admits-its-namespace"),
        pytest.param("urn:x", "t:g='zz'", "skip", False, id="uri-rejects-other-namespace"),
        pytest.param("urn:y urn:x", "x:a='1'", "strict", False, id="uri-list-admits-listed-namespace-strict"),
        pytest.param("##any", "g='zz'", "lax", True, id="any-lax-unprefixed-ignores-target-declaration"),
    ],
)
def test_prefixed_attribute_follows_any_attribute_namespace_and_processing(
    namespace: str, attribute: str, processing: str, *, valid: bool
) -> None:
    schema = (
        f'<xs:schema {_XS} targetNamespace="urn:t" xmlns:t="urn:t"><xs:attribute name="g" type="xs:int"/>'
        '<xs:element name="r"><xs:complexType><xs:sequence/>'
        f'<xs:anyAttribute namespace="{namespace}" processContents="{processing}"/></xs:complexType>'
        "</xs:element></xs:schema>"
    )
    assert check(schema, f"<t:r xmlns:t='urn:t' xmlns:x='urn:x' {attribute}/>").valid is valid


@pytest.mark.parametrize(
    ("declaration", "attribute", "valid"),
    [
        pytest.param('<xs:attribute ref="t:g"/>', "t:g='12'", True, id="ref-valid-value"),
        pytest.param('<xs:attribute ref="t:g"/>', "t:g='zz'", False, id="ref-invalid-value"),
        pytest.param('<xs:attribute ref="t:g" use="required"/>', "", False, id="ref-required-missing"),
        pytest.param('<xs:attribute ref="t:g" use="required"/>', "t:g='12'", True, id="ref-required-present"),
        pytest.param('<xs:attribute ref="t:g" use="prohibited"/>', "t:g='12'", False, id="ref-prohibited-present"),
        pytest.param('<xs:attribute name="l" type="xs:int" form="qualified"/>', "t:l='12'", True, id="form-qualified"),
        pytest.param(
            '<xs:attribute name="l" type="xs:int" form="qualified"/>', "t:l='zz'", False, id="form-qualified-value"
        ),
        pytest.param(
            '<xs:attribute name="l" type="xs:int" form="qualified"/>', "l='12'", False, id="form-qualified-rejects-bare"
        ),
        pytest.param('<xs:attribute name="l" type="xs:int"/>', "l='12'", True, id="unqualified-by-default"),
        pytest.param('<xs:attribute name="l" type="xs:int"/>', "t:l='12'", False, id="unqualified-rejects-prefixed"),
        pytest.param(
            '<xs:attribute name="l" type="xs:int" form="unqualified"/>',
            "t:l='12'",
            False,
            id="form-unqualified-rejects-prefixed",
        ),
    ],
)
def test_declared_prefixed_attribute_is_validated(declaration: str, attribute: str, *, valid: bool) -> None:
    schema = (
        f'<xs:schema {_XS} targetNamespace="urn:t" xmlns:t="urn:t"><xs:attribute name="g" type="xs:int"/>'
        f'<xs:element name="r"><xs:complexType><xs:sequence/>{declaration}</xs:complexType></xs:element></xs:schema>'
    )
    assert check(schema, f"<t:r xmlns:t='urn:t' {attribute}/>").valid is valid


def test_attribute_form_default_qualifies_local_declarations() -> None:
    schema = (
        f'<xs:schema {_XS} targetNamespace="urn:t" xmlns:t="urn:t" attributeFormDefault="qualified">'
        '<xs:element name="r"><xs:complexType><xs:sequence/><xs:attribute name="l" type="xs:int"/>'
        "</xs:complexType></xs:element></xs:schema>"
    )
    assert check(schema, "<t:r xmlns:t='urn:t' t:l='12'/>").valid
    assert not check(schema, "<t:r xmlns:t='urn:t' l='12'/>").valid


@pytest.mark.parametrize(
    ("attribute", "valid"),
    [
        pytest.param("t:g='12'", True, id="wildcard-declaration-valid"),
        pytest.param("t:g='zz'", False, id="wildcard-declaration-invalid"),
    ],
)
def test_wide_type_validates_wildcard_admitted_global_declaration(attribute: str, *, valid: bool) -> None:
    declarations = "".join(f'<xs:attribute name="item{index}" type="xs:string"/>' for index in range(32))
    schema = (
        f'<xs:schema {_XS} targetNamespace="urn:t" xmlns:t="urn:t"><xs:attribute name="g" type="xs:int"/>'
        f'<xs:element name="r"><xs:complexType><xs:sequence/>{declarations}'
        '<xs:anyAttribute namespace="urn:t urn:x" processContents="lax"/></xs:complexType></xs:element></xs:schema>'
    )
    items = " ".join(f'item{index}="text"' for index in range(32))
    assert check(schema, f"<t:r xmlns:t='urn:t' xmlns:x='urn:x' {items} x:da='1' {attribute}/>").valid is valid


@pytest.mark.parametrize(
    ("attribute", "valid"),
    [
        pytest.param("t:g='12'", True, id="declared-prefixed-valid"),
        pytest.param("t:g='zz'", False, id="declared-prefixed-invalid"),
        pytest.param("", False, id="declared-prefixed-required-missing"),
        pytest.param("t:g='12' t:item0='text'", False, id="same-local-name-in-another-namespace"),
        pytest.param("x:g='zz' t:g='12'", True, id="same-local-name-in-another-namespace-probed-first"),
        pytest.param("g='zz' t:g='12'", True, id="same-local-name-without-namespace-probed-first"),
    ],
)
def test_wide_type_matches_prefixed_declaration(attribute: str, *, valid: bool) -> None:
    declarations = "".join(f'<xs:attribute name="item{index}" type="xs:string"/>' for index in range(32))
    schema = (
        f'<xs:schema {_XS} targetNamespace="urn:t" xmlns:t="urn:t"><xs:attribute name="g" type="xs:int"/>'
        f'<xs:element name="r"><xs:complexType><xs:sequence/>{declarations}'
        '<xs:attribute ref="t:g" use="required"/><xs:anyAttribute namespace="urn:x ##local" processContents="skip"/>'
        "</xs:complexType></xs:element></xs:schema>"
    )
    items = " ".join(f'item{index}="text"' for index in range(32))
    assert check(schema, f"<t:r xmlns:t='urn:t' xmlns:x='urn:x' {items} {attribute}/>").valid is valid


@pytest.mark.parametrize(
    ("attribute", "valid"),
    [
        pytest.param("xsi:type='xs:int'", True, id="type"),
        pytest.param("xsi:nil='true'", True, id="nil"),
        pytest.param("xsi:schemaLocation='urn:x a.xsd'", True, id="schema-location"),
        pytest.param("xsi:noNamespaceSchemaLocation='a.xsd'", True, id="no-namespace-schema-location"),
        pytest.param("xsi:other='1'", False, id="other-xsi-attribute-is-checked"),
        pytest.param("xmlns:x='urn:x'", True, id="namespace-declaration-exempt"),
        pytest.param("xsi:kind='1'", False, id="same-length-xsi-attribute-is-checked"),
        pytest.param("xml:lang='en'", False, id="xml-attribute-is-checked"),
    ],
)
def test_exempt_and_checked_namespace_attributes(attribute: str, *, valid: bool) -> None:
    schema = (
        f'<xs:schema {_XS}><xs:element name="r"><xs:complexType><xs:sequence/></xs:complexType>'
        "</xs:element></xs:schema>"
    )
    assert check(schema, f"<r xmlns:xsi='http://www.w3.org/2001/XMLSchema-instance' {attribute}/>").valid is valid


def test_prefix_bound_only_in_an_earlier_sibling_has_no_namespace() -> None:
    schema = (
        f'<xs:schema {_XS}><xs:element name="html"><xs:complexType><xs:sequence>'
        '<xs:element name="head" minOccurs="0"><xs:complexType/></xs:element>'
        '<xs:element name="body"><xs:complexType><xs:sequence>'
        '<xs:element name="c" minOccurs="0" maxOccurs="unbounded"><xs:complexType>'
        '<xs:anyAttribute namespace="##local" processContents="skip"/></xs:complexType></xs:element>'
        "</xs:sequence></xs:complexType></xs:element>"
        "</xs:sequence></xs:complexType></xs:element></xs:schema>"
    )
    assert (
        XMLSchema(schema)
        .validate(parse("<html><head></head><body><c xmlns:p='urn:p'></c><c p:a='1'></c></body></html>"))
        .valid
    )


def test_unbound_prefixed_attribute_does_not_match_a_declaration() -> None:
    declarations = "".join(f'<xs:attribute name="item{index}" type="xs:string"/>' for index in range(32))
    schema = (
        f'<xs:schema {_XS}><xs:attribute name="g" type="xs:int"/>'
        f'<xs:element name="html"><xs:complexType><xs:sequence>'
        '<xs:any minOccurs="0" maxOccurs="unbounded" processContents="skip"/></xs:sequence>'
        f"{declarations}"
        '<xs:attribute ref="g" use="required"/></xs:complexType></xs:element></xs:schema>'
    )
    items = " ".join(f'item{index}="text"' for index in range(32))
    assert not XMLSchema(schema).validate(parse(f"<html {items} p:g='12'></html>")).valid


@pytest.mark.parametrize(
    ("wildcard", "valid"),
    [
        pytest.param('<xs:anyAttribute namespace="##local" processContents="skip"/>', True, id="local-skip-admits"),
        pytest.param('<xs:anyAttribute namespace="##any" processContents="skip"/>', True, id="any-skip-admits"),
        pytest.param('<xs:anyAttribute namespace="##local" processContents="lax"/>', True, id="local-lax-undeclared"),
        pytest.param(
            '<xs:anyAttribute namespace="##local" processContents="strict"/>', False, id="local-strict-undeclared"
        ),
        pytest.param('<xs:anyAttribute namespace="##other" processContents="skip"/>', False, id="other-rejects-absent"),
        pytest.param('<xs:anyAttribute namespace="urn:p" processContents="skip"/>', False, id="uri-rejects-absent"),
        pytest.param("", False, id="no-wildcard-rejects"),
    ],
)
def test_unbound_prefixed_attribute_is_a_no_namespace_attribute(wildcard: str, *, valid: bool) -> None:
    schema = (
        f'<xs:schema {_XS}><xs:element name="html"><xs:complexType><xs:sequence>'
        '<xs:any minOccurs="0" maxOccurs="unbounded" processContents="skip"/></xs:sequence>'
        f"{wildcard}</xs:complexType></xs:element></xs:schema>"
    )
    assert XMLSchema(schema).validate(parse("<html p:a='1'></html>")).valid is valid


@pytest.mark.parametrize(
    ("attributes", "valid"),
    [
        pytest.param("a='12'", True, id="unprefixed-declaration"),
        pytest.param("a='12' p:a='zz'", False, id="prefixed-name-is-not-the-declaration"),
    ],
)
def test_unbound_prefixed_attribute_never_takes_a_declaration(attributes: str, *, valid: bool) -> None:
    schema = (
        f'<xs:schema {_XS}><xs:element name="html"><xs:complexType><xs:sequence>'
        '<xs:any minOccurs="0" maxOccurs="unbounded" processContents="skip"/></xs:sequence>'
        '<xs:attribute name="a" type="xs:int"/></xs:complexType></xs:element></xs:schema>'
    )
    assert XMLSchema(schema).validate(parse(f"<html {attributes}></html>")).valid is valid


@pytest.mark.parametrize(
    ("schema", "document", "valid"),
    [
        pytest.param(
            f'<xs:schema {_XS} xmlns:o="urn:other"><xs:element name="v"><xs:complexType>'
            '<xs:anyAttribute namespace="urn:ok" processContents="skip"/></xs:complexType></xs:element></xs:schema>',
            '<v xmlns:o="urn:other" o:a="1"/>',
            False,
            id="namespace-outside-the-constraint",
        ),
        pytest.param(
            f'<xs:schema {_XS} xmlns:k="urn:ok"><xs:element name="v"><xs:complexType>'
            '<xs:anyAttribute namespace="urn:ok" processContents="skip"/></xs:complexType></xs:element></xs:schema>',
            '<v xmlns:k="urn:ok" k:a="1"/>',
            True,
            id="namespace-inside-the-constraint",
        ),
        pytest.param(
            f'<xs:schema {_XS} xmlns:o="urn:other"><xs:element name="v"><xs:complexType>'
            '<xs:anyAttribute namespace="##other" processContents="skip"/></xs:complexType></xs:element></xs:schema>',
            '<v xmlns:o="urn:other" o:a="1"/>',
            True,
            id="other-admits-third-namespace",
        ),
        pytest.param(
            f'<xs:schema {_XS} xmlns:o="urn:other"><xs:element name="v"><xs:complexType/></xs:element></xs:schema>',
            '<v xmlns:o="urn:other" o:a="1"/>',
            False,
            id="no-wildcard-rejects-prefixed",
        ),
        pytest.param(
            f'<xs:schema {_XS} xmlns:p="urn:p" targetNamespace="urn:p"><xs:attribute name="n" type="xs:integer"/>'
            '<xs:element name="v"><xs:complexType><xs:attribute ref="p:n"/></xs:complexType></xs:element></xs:schema>',
            '<p:v xmlns:p="urn:p" p:n="bad"/>',
            False,
            id="declared-prefixed-bad-value",
        ),
        pytest.param(
            f'<xs:schema {_XS} xmlns:p="urn:p" targetNamespace="urn:p"><xs:attribute name="n" type="xs:integer"/>'
            '<xs:element name="v"><xs:complexType><xs:attribute ref="p:n"/></xs:complexType></xs:element></xs:schema>',
            '<p:v xmlns:p="urn:p" p:n="12"/>',
            True,
            id="declared-prefixed-valid-value",
        ),
        pytest.param(
            f'<xs:schema {_XS}><xs:element name="v"><xs:complexType>'
            '<xs:anyAttribute processContents="strict"/></xs:complexType></xs:element></xs:schema>',
            '<v zz="1"/>',
            False,
            id="unprefixed-strict-needs-declaration",
        ),
    ],
)
def test_prefixed_attribute_verdicts_match_xmllint(schema: str, document: str, *, valid: bool) -> None:
    assert check(schema, document).valid is valid


@pytest.mark.parametrize(("attribute", "valid"), [("x:a='zz'", True), ("b='1'", False)])
def test_first_admitting_any_attribute_applies(attribute: str, *, valid: bool) -> None:
    schema = (
        f'<xs:schema {_XS}><xs:complexType name="base"><xs:sequence/>'
        '<xs:anyAttribute namespace="##local" processContents="strict"/></xs:complexType>'
        '<xs:element name="r"><xs:complexType><xs:complexContent><xs:extension base="base"><xs:sequence/>'
        '<xs:anyAttribute namespace="urn:x" processContents="skip"/></xs:extension></xs:complexContent>'
        "</xs:complexType>"
        "</xs:element></xs:schema>"
    )
    assert check(schema, f"<r xmlns:x='urn:x' {attribute}/>").valid is valid


_REPEATED_MODELS: Final[list[pytest.ParameterSet]] = [
    pytest.param(
        '<xs:choice minOccurs="0" maxOccurs="unbounded"><xs:element name="a"/><xs:element name="b"/></xs:choice>',
        id="choice",
    ),
    pytest.param('<xs:sequence><xs:element name="a" minOccurs="0" maxOccurs="unbounded"/></xs:sequence>', id="element"),
    pytest.param(
        '<xs:sequence><xs:any minOccurs="0" maxOccurs="unbounded" processContents="skip"/></xs:sequence>',
        id="wildcard",
    ),
]


@pytest.mark.parametrize("model", _REPEATED_MODELS)
def test_repeated_particle_scales_linearly(model: str, work_steps: WorkSteps) -> None:
    def build(count: int) -> Callable[[], ValidationResult]:
        schema: Final[XMLSchema] = XMLSchema(_repeated_schema(model))
        return partial(schema.validate, parse_xml(f"<r>{'<a/>' * count}</r>"))

    work_steps.assert_linear(build, (300, 1200))


def _repeated_schema(model: str) -> str:
    return f'<xs:schema {_XS}><xs:element name="r"><xs:complexType>{model}</xs:complexType></xs:element></xs:schema>'


@pytest.mark.parametrize("model", _REPEATED_MODELS)
def test_repeated_particle_peak_memory_is_linear(model: str, peak_bytes: PeakBytes) -> None:
    # the position sets of every repetition are reused, so the peak must not grow with the number of children
    script: Final[str] = (
        "import sys, tracemalloc\nfrom turbohtml import parse_xml\nfrom turbohtml.validate import XMLSchema\n"
        "schema = XMLSchema(sys.argv[1])\ndocument = parse_xml(sys.argv[2])\n"
        "tracemalloc.start()\nschema.validate(document)\nprint(tracemalloc.get_traced_memory()[1])\n"
    )
    document: Final[str] = f"<r>{'<a/>' * 3000}</r>"
    assert peak_bytes(script, (_repeated_schema(model), document)) < 64 * len(document)


_A_ELEMENT: Final[str] = '<xs:element name="a"/>'
_AMBIGUOUS_REPEATS: Final[list[pytest.ParameterSet]] = [
    pytest.param(
        f'<xs:sequence maxOccurs="unbounded">{_A_ELEMENT}<xs:element name="a" minOccurs="0"/></xs:sequence>',
        id="element-then-optional-element",
    ),
    pytest.param(
        f'<xs:choice maxOccurs="unbounded">{_A_ELEMENT}<xs:sequence>{_A_ELEMENT * 2}</xs:sequence></xs:choice>',
        id="element-or-pair",
    ),
    pytest.param(
        f'<xs:choice maxOccurs="unbounded">{_A_ELEMENT}<xs:sequence>{_A_ELEMENT * 50}</xs:sequence></xs:choice>',
        id="element-or-run-of-fifty",
    ),
]


@pytest.mark.parametrize("model", _AMBIGUOUS_REPEATS)
def test_ambiguous_repeated_particle_scales_linearly(model: str, work_steps: WorkSteps) -> None:
    def build(count: int) -> Callable[[], ValidationResult]:
        schema: Final[XMLSchema] = XMLSchema(_repeated_schema(model))
        return partial(schema.validate, parse_xml(f"<r>{'<a/>' * count}</r>"))

    work_steps.assert_linear(build, (300, 1200))


@pytest.mark.parametrize(
    ("count", "valid"),
    [
        pytest.param(1, False, id="one-is-too-few"),
        pytest.param(2, True, id="two-by-the-run"),
        pytest.param(3, True, id="three-by-the-run-after-an-empty-prefix"),
    ],
)
def test_optional_prefix_before_repeated_run(count: int, *, valid: bool) -> None:
    run = '<xs:element name="a" minOccurs="2" maxOccurs="unbounded"/>'
    schema = (
        f'<xs:schema {_XS}><xs:element name="r"><xs:complexType><xs:sequence>'
        f'<xs:sequence minOccurs="0" maxOccurs="1">{run}</xs:sequence>{run}'
        "</xs:sequence></xs:complexType></xs:element></xs:schema>"
    )
    assert check(schema, f"<r>{'<a/>' * count}</r>").valid is valid


@pytest.mark.parametrize(
    ("count", "valid"),
    [
        pytest.param(1, False, id="one-is-too-few"),
        pytest.param(2, False, id="two-is-too-few"),
        pytest.param(3, True, id="three-is-enough"),
    ],
)
def test_run_of_at_least_three(count: int, *, valid: bool) -> None:
    schema = (
        f'<xs:schema {_XS}><xs:element name="r"><xs:complexType><xs:sequence>'
        '<xs:sequence minOccurs="3" maxOccurs="unbounded"><xs:element name="a"/></xs:sequence>'
        "</xs:sequence></xs:complexType></xs:element></xs:schema>"
    )
    assert check(schema, f"<r>{'<a/>' * count}</r>").valid is valid


def test_nullable_pair_repeated_three_times_rejects_odd_count() -> None:
    schema = (
        f'<xs:schema {_XS}><xs:element name="r"><xs:complexType><xs:sequence>'
        '<xs:sequence minOccurs="3" maxOccurs="unbounded">'
        f'<xs:sequence minOccurs="0">{_A_ELEMENT * 2}</xs:sequence>'
        "</xs:sequence></xs:sequence></xs:complexType></xs:element></xs:schema>"
    )
    assert not check(schema, "<r><a/><a/><a/></r>").valid


_WIDE_CHILDREN: Final[str] = "".join(f'<xs:element name="e{index}"/>' for index in range(200))
_WIDE_ATTRIBUTES: Final[str] = "".join(f'<xs:attribute name="a{index}"/>' for index in range(200))
_EMPTY_ATTRIBUTES: Final[str] = " ".join(f'a{index}=""' for index in range(32))


@pytest.mark.parametrize(
    ("content", "instance"),
    [
        pytest.param(
            f'<xs:choice minOccurs="0" maxOccurs="0">{_WIDE_CHILDREN}</xs:choice>',
            "<v/>",
            id="child-declarations-unused",
        ),
        pytest.param(
            f'<xs:sequence><xs:element name="first"/><xs:choice minOccurs="0" maxOccurs="0">{_WIDE_CHILDREN}'
            "</xs:choice></xs:sequence>",
            "<v><first/></v>",
            id="child-declarations",
        ),
        pytest.param(_WIDE_ATTRIBUTES, "<v/>", id="attribute-declarations"),
        pytest.param(_WIDE_ATTRIBUTES, f"<v {_EMPTY_ATTRIBUTES}/>", id="attribute-name-index"),
    ],
)
def test_wide_type_costs_no_memory_per_instance(content: str, instance: str) -> None:
    # The arena keeps every allocation until validate() returns, so a per-instance copy of the type's declarations
    # grows with the instance count. maxOccurs="0" keeps the content-model matcher, which allocates for itself, out of
    # the wide choice. PyPy has no tracemalloc; the lists are C and the CPython run covers them.
    tracing: Final[ModuleType] = pytest.importorskip("tracemalloc")
    schema: Final[XMLSchema] = XMLSchema(
        f'<xs:schema {_XS}><xs:element name="root"><xs:complexType><xs:sequence>'
        '<xs:element name="section" maxOccurs="unbounded"><xs:complexType><xs:sequence>'
        f'<xs:element name="v" maxOccurs="unbounded"><xs:complexType>{content}</xs:complexType></xs:element>'
        "</xs:sequence></xs:complexType></xs:element>"
        "</xs:sequence></xs:complexType></xs:element></xs:schema>"
    )
    document: Final[Document] = parse_xml(f"<root>{f'<section>{instance * 50}</section>' * 30}</root>")
    tracing.start()
    try:
        assert schema.validate(document).valid
        peak: Final[int] = tracing.get_traced_memory()[1]
    finally:
        tracing.stop()
    assert peak < 4_000_000, f"validating 1500 instances peaked at {peak} bytes"


def test_child_declarations_outlive_a_sibling_subtree() -> None:
    # validating <a> refills the one lookup list with the declarations of its own children before <b> is reached
    schema: Final[str] = (
        f'<xs:schema {_XS}><xs:element name="r"><xs:complexType><xs:sequence>'
        '<xs:element name="a"><xs:complexType><xs:sequence><xs:element name="x"/><xs:element name="y"/>'
        '</xs:sequence></xs:complexType></xs:element><xs:element name="b" type="xs:int"/>'
        "</xs:sequence></xs:complexType></xs:element></xs:schema>"
    )
    assert check(schema, "<r><a><x/><y/></a><b>1</b></r>").valid
    assert [error.message for error in check(schema, "<r><a><x/><y/></a><b>x</b></r>").errors] == [
        "value 'x' is not a valid int"
    ]


def test_attribute_name_index_is_rebuilt_for_each_element() -> None:
    # <b> reuses the buffers <a> indexed its 200 declarations in, and must not find a0 there
    narrow: Final[str] = "".join(f'<xs:attribute name="b{index}"/>' for index in range(32))
    schema: Final[str] = (
        f'<xs:schema {_XS}><xs:element name="r"><xs:complexType><xs:sequence>'
        f'<xs:element name="a"><xs:complexType>{_WIDE_ATTRIBUTES}</xs:complexType></xs:element>'
        f'<xs:element name="b"><xs:complexType>{narrow}</xs:complexType></xs:element>'
        "</xs:sequence></xs:complexType></xs:element></xs:schema>"
    )
    attributes_a: Final[str] = " ".join(f'a{index}=""' for index in range(64))
    attributes_b: Final[str] = " ".join(f'b{index}=""' for index in range(32))
    assert check(schema, f"<r><a {attributes_a}/><b {attributes_b}/></r>").valid
    undeclared: Final[ValidationResult] = check(schema, f'<r><a {attributes_a}/><b {attributes_b} a0=""/></r>')
    assert [error.message for error in undeclared.errors] == ["attribute 'a0' is not declared"]


_OPTIONAL_A: Final[str] = '<xs:element name="a" minOccurs="0"/>'
_OPTIONAL_PAIR: Final[str] = f'<xs:sequence minOccurs="0">{_A_ELEMENT * 2}</xs:sequence>'
_EMPTIABLE_BODIES: Final[list[pytest.ParameterSet]] = [
    pytest.param(_OPTIONAL_A, id="optional-element"),
    pytest.param(f'<xs:sequence minOccurs="0">{_A_ELEMENT}</xs:sequence>', id="optional-sequence"),
    pytest.param(
        '<xs:choice minOccurs="0"><xs:element name="a"/><xs:element name="b"/></xs:choice>', id="optional-choice"
    ),
    pytest.param("<xs:sequence/>", id="empty-sequence"),
]
_REPEAT_OCCURS: Final[list[pytest.ParameterSet]] = [
    pytest.param('minOccurs="2" maxOccurs="unbounded"', id="at-least-two"),
    pytest.param('minOccurs="2" maxOccurs="2"', id="exactly-two"),
    pytest.param('minOccurs="3" maxOccurs="5"', id="three-to-five"),
    pytest.param('minOccurs="1000" maxOccurs="unbounded"', id="a-thousand-or-more"),
]


@pytest.mark.parametrize("occurs", _REPEAT_OCCURS)
@pytest.mark.parametrize("body", _EMPTIABLE_BODIES)
def test_emptiable_repeated_particle_accepts_no_children(occurs: str, body: str) -> None:
    assert check(_repeated_schema(f"<xs:sequence {occurs}>{body}</xs:sequence>"), "<r/>").valid


@pytest.mark.parametrize(
    ("occurs", "count", "valid"),
    [
        pytest.param('minOccurs="2" maxOccurs="2"', 2, True, id="two-of-two"),
        pytest.param('minOccurs="2" maxOccurs="2"', 3, False, id="three-above-two"),
        pytest.param('minOccurs="3" maxOccurs="5"', 5, True, id="five-of-five"),
        pytest.param('minOccurs="3" maxOccurs="5"', 6, False, id="six-above-five"),
        pytest.param('minOccurs="2" maxOccurs="unbounded"', 7, True, id="unbounded-run"),
    ],
)
def test_optional_element_repeated_within_occurs(occurs: str, count: int, *, valid: bool) -> None:
    schema = _repeated_schema(f"<xs:sequence {occurs}>{_OPTIONAL_A}</xs:sequence>")
    assert check(schema, f"<r>{'<a/>' * count}</r>").valid is valid


@pytest.mark.parametrize(
    ("count", "valid"),
    [
        pytest.param(0, True, id="no-pair"),
        pytest.param(1, False, id="one-of-a-pair"),
        pytest.param(2, True, id="one-pair"),
        pytest.param(3, False, id="pair-and-one"),
        pytest.param(4, True, id="two-pairs"),
        pytest.param(5, False, id="two-pairs-and-one"),
    ],
)
def test_optional_pair_repeated_twice(count: int, *, valid: bool) -> None:
    model = f'<xs:sequence minOccurs="2" maxOccurs="2">{_OPTIONAL_PAIR}</xs:sequence>'
    assert check(_repeated_schema(model), f"<r>{'<a/>' * count}</r>").valid is valid


@pytest.mark.parametrize("count", [0, 1, 3])
def test_minimum_above_maximum_matches_nothing(count: int) -> None:
    model = '<xs:sequence minOccurs="3" maxOccurs="2"><xs:element name="a"/></xs:sequence>'
    assert not check(_repeated_schema(model), f"<r>{'<a/>' * count}</r>").valid


def test_all_any_order_and_missing() -> None:
    schema = (
        f'<xs:schema {_XS}><xs:element name="r"><xs:complexType><xs:all>'
        '<xs:element name="x" type="xs:string"/>'
        '<xs:element name="y" type="xs:string" minOccurs="0"/>'
        "</xs:all></xs:complexType></xs:element></xs:schema>"
    )
    assert check(schema, "<r><y>1</y><x>2</x></r>").valid
    assert check(schema, "<r><x>2</x></r>").valid
    assert not check(schema, "<r><y>1</y></r>").valid  # missing required x
    assert not check(schema, "<r><x>1</x><z>2</z></r>").valid  # undeclared z


_ALL_MEMBERS: Final[str] = (
    f'<xs:schema {_XS} targetNamespace="urn:t" xmlns:t="urn:t"><xs:element name="r"><xs:complexType><xs:all>'
    '<xs:element name="long" minOccurs="0"/>'
    '<xs:element name="b"/>'
    '<xs:element ref="t:a"/>'
    '<xs:element name="a" minOccurs="0"/>'
    '<xs:element name="b" type="xs:int"/>'
    '<xs:element name="long"/>'
    "</xs:all></xs:complexType></xs:element>"
    '<xs:element name="a" type="xs:int"/></xs:schema>'
)


@pytest.mark.parametrize(
    ("children", "messages"),
    [
        pytest.param("<long/><a>x</a><t:a>1</t:a><b>x</b>", [], id="any-order-first-declaration-of-a-name"),
        pytest.param("<t:a>x</t:a><b/><long/>", ["value 'x' is not a valid int"], id="namespace-tells-members-apart"),
        pytest.param(
            "",
            [
                "required element 'long' is missing",
                "required element 'b' is missing",
                "required element 'a' is missing",
            ],
            id="name-declared-twice-is-required-when-either-is",
        ),
        pytest.param(
            "<b/><zz/><b/>",
            [
                "element 'zz' is not allowed here",
                "element 'b' is not allowed here",
                "required element 'long' is missing",
                "required element 'a' is missing",
            ],
            id="repeated-child-is-not-allowed",
        ),
        pytest.param(
            '<t:long/><a xmlns="urn:u"/><a/><long/>',
            [
                "element 't:long' is not allowed here",
                "element 'a' is not allowed here",
                "required element 'b' is missing",
                "required element 'a' is missing",
            ],
            id="same-local-name-other-namespace",
        ),
    ],
)
def test_all_matches_members_by_name(children: str, messages: list[str]) -> None:
    result = check(_ALL_MEMBERS, f'<t:r xmlns:t="urn:t">{children}</t:r>')
    assert [error.message for error in result.errors] == messages


def test_all_without_members_allows_no_child() -> None:
    schema: Final[str] = (
        f'<xs:schema {_XS}><xs:element name="r"><xs:complexType><xs:all/></xs:complexType></xs:element></xs:schema>'
    )
    assert check(schema, "<r/>").valid
    assert [error.message for error in check(schema, "<r><x/></r>").errors] == ["element 'x' is not allowed here"]


_ALL_CONTENT_MEMBERS: Final[str] = '<xs:element name="a"/><xs:element name="b"/><xs:element name="c" minOccurs="0"/>'
_ALL_CONTENT_SCHEMAS: Final[tuple[tuple[str, str], ...]] = (
    (
        "direct",
        (
            f'<xs:schema {_XS}><xs:element name="r"><xs:complexType><xs:all>{_ALL_CONTENT_MEMBERS}</xs:all>'
            "</xs:complexType></xs:element></xs:schema>"
        ),
    ),
    (
        "named-group",
        (
            f'<xs:schema {_XS}><xs:group name="g"><xs:all>{_ALL_CONTENT_MEMBERS}</xs:all></xs:group>'
            '<xs:element name="r"><xs:complexType><xs:group ref="g"/></xs:complexType></xs:element></xs:schema>'
        ),
    ),
    (
        "extension",
        (
            f'<xs:schema {_XS}><xs:complexType name="base"/><xs:element name="r"><xs:complexType>'
            '<xs:complexContent><xs:extension base="base">'
            f"<xs:all>{_ALL_CONTENT_MEMBERS}</xs:all></xs:extension></xs:complexContent>"
            "</xs:complexType></xs:element></xs:schema>"
        ),
    ),
    (
        "restriction",
        (
            f'<xs:schema {_XS}><xs:element name="r"><xs:complexType><xs:complexContent>'
            f'<xs:restriction base="xs:anyType"><xs:all>{_ALL_CONTENT_MEMBERS}</xs:all></xs:restriction>'
            "</xs:complexContent></xs:complexType></xs:element></xs:schema>"
        ),
    ),
)


@pytest.mark.parametrize("schema", [pytest.param(text, id=form) for form, text in _ALL_CONTENT_SCHEMAS])
@pytest.mark.parametrize(
    ("children", "messages"),
    [
        pytest.param("<a/><b/>", [], id="declaration-order"),
        pytest.param("<b/><a/>", [], id="reverse-order"),
        pytest.param("<c/><b/><a/>", [], id="optional-member-first"),
        pytest.param("<b/>", ["required element 'a' is missing"], id="required-missing"),
        pytest.param("<a/><b/><zz/>", ["element 'zz' is not allowed here"], id="undeclared"),
    ],
)
def test_all_content_matches_members_in_any_order(schema: str, children: str, messages: list[str]) -> None:
    assert [error.message for error in check(schema, f"<r>{children}</r>").errors] == messages


@pytest.mark.parametrize(
    "model",
    [
        pytest.param('<xs:sequence><xs:group ref="g"/></xs:sequence>', id="group-ref-in-sequence"),
        pytest.param('<xs:choice><xs:group ref="g"/></xs:choice>', id="group-ref-in-choice"),
        pytest.param(f"<xs:sequence><xs:all>{_ALL_CONTENT_MEMBERS}</xs:all></xs:sequence>", id="all-in-sequence"),
        pytest.param(
            '<xs:simpleContent><xs:extension base="xs:string"><xs:group ref="g"/></xs:extension></xs:simpleContent>',
            id="group-ref-in-simple-content",
        ),
    ],
)
def test_all_outside_complex_type_content_raises(model: str) -> None:
    schema = (
        f'<xs:schema {_XS}><xs:group name="g"><xs:all>{_ALL_CONTENT_MEMBERS}</xs:all></xs:group>'
        f'<xs:element name="r"><xs:complexType>{model}</xs:complexType></xs:element></xs:schema>'
    )
    with pytest.raises(ValueError, match="an xs:all group may only be the content model of a complex type"):
        XMLSchema(schema)


def test_all_base_empty_extension_counts_each_member_once() -> None:
    schema = (
        f'<xs:schema {_XS}><xs:complexType name="base"><xs:all>'
        '<xs:element name="a" minOccurs="0" maxOccurs="2"/><xs:element name="b"/></xs:all></xs:complexType>'
        '<xs:element name="r"><xs:complexType><xs:complexContent><xs:extension base="base"/>'
        "</xs:complexContent></xs:complexType></xs:element></xs:schema>"
    )
    assert check(schema, "<r><a/><b/></r>").valid
    assert not check(schema, "<r><a/><a/><b/></r>").valid


def test_optional_all_group_reference_when_members_are_present() -> None:
    schema = (
        f'<xs:schema {_XS}><xs:group name="g"><xs:all><xs:element name="a"/><xs:element name="b"/></xs:all></xs:group>'
        '<xs:element name="r"><xs:complexType><xs:group ref="g" minOccurs="0"/></xs:complexType></xs:element>'
        "</xs:schema>"
    )
    assert [error.message for error in check(schema, "<r><b/></r>").errors] == ["required element 'a' is missing"]
    assert check(schema, "<r><b/><a/></r>").valid


def test_all_group_beside_sequence_content() -> None:
    schema = (
        f'<xs:schema {_XS}><xs:group name="g"><xs:all><xs:element name="a"/><xs:element name="b"/></xs:all></xs:group>'
        '<xs:group name="s"><xs:sequence><xs:element name="c"/></xs:sequence></xs:group>'
        '<xs:element name="all"><xs:complexType><xs:group ref="g"/></xs:complexType></xs:element>'
        '<xs:element name="seq"><xs:complexType><xs:group ref="s"/></xs:complexType></xs:element>'
        '<xs:element name="direct"><xs:complexType><xs:sequence><xs:element name="d"/></xs:sequence>'
        "</xs:complexType></xs:element></xs:schema>"
    )
    assert check(schema, "<all><b/><a/></all>").valid
    assert check(schema, "<seq><c/></seq>").valid
    assert not check(schema, "<seq><b/></seq>").valid
    assert check(schema, "<direct><d/></direct>").valid


def _type_schema(content: str) -> str:
    return f'<xs:schema {_XS}><xs:element name="r"><xs:complexType>{content}</xs:complexType></xs:element></xs:schema>'


_AB: Final[str] = '<xs:element name="a"/><xs:element name="b"/>'
_A_REPEATED: Final[str] = "element 'a' is not allowed here"
_NAMED_GROUP: Final[str] = (
    f'<xs:schema {_XS}><xs:group name="g"><xs:all>{_AB}</xs:all></xs:group>'
    '<xs:element name="r"><xs:complexType><xs:group ref="g"/></xs:complexType></xs:element></xs:schema>'
)
_EXTENDED_ALL: Final[str] = (
    f'<xs:schema {_XS}><xs:complexType name="base"><xs:attribute name="x"/></xs:complexType>'
    '<xs:element name="r"><xs:complexType><xs:complexContent><xs:extension base="base">'
    f"<xs:all>{_AB}</xs:all></xs:extension></xs:complexContent></xs:complexType></xs:element></xs:schema>"
)

_REPEATED_CHILD: Final[list[pytest.ParameterSet]] = [
    pytest.param(_type_schema(f"<xs:all>{_AB}</xs:all>"), "<r><a/><a/><b/></r>", [_A_REPEATED], id="repeated-child"),
    pytest.param(
        _type_schema(f"<xs:all>{_AB}</xs:all>"),
        "<r><a/><b/><a/></r>",
        [_A_REPEATED],
        id="repeated-after-other-children",
    ),
    pytest.param(
        _type_schema('<xs:all><xs:element name="a" minOccurs="0"/><xs:element name="b"/></xs:all>'),
        "<r><b/><a/><a/></r>",
        [_A_REPEATED],
        id="optional-child-repeated",
    ),
    pytest.param(
        _type_schema(f"<xs:all>{_AB}</xs:all>"),
        "<r><a/><b/><a/><a/></r>",
        [_A_REPEATED, _A_REPEATED],
        id="every-repeat-reported",
    ),
    pytest.param(
        _type_schema(f"<xs:all>{_AB}</xs:all>"),
        "<r><a/><a/></r>",
        [_A_REPEATED, "required element 'b' is missing"],
        id="repeat-does-not-stand-in-for-missing-member",
    ),
    pytest.param(
        _type_schema('<xs:all><xs:element name="a" maxOccurs="0"/><xs:element name="b"/></xs:all>'),
        "<r><a/><b/></r>",
        [_A_REPEATED],
        id="member-with-max-occurs-zero",
    ),
    pytest.param(_NAMED_GROUP, "<r><a/><a/><b/></r>", [_A_REPEATED], id="named-group-repeated-child"),
    pytest.param(
        _NAMED_GROUP.replace('<xs:element name="a"/>', '<xs:element name="a" maxOccurs="2"/>'),
        "<r><a/><a/><b/></r>",
        [_A_REPEATED],
        id="named-group-member-max-occurs",
    ),
    pytest.param(_EXTENDED_ALL, "<r><a/><a/><b/></r>", [_A_REPEATED], id="complex-content-extension"),
]


@pytest.mark.parametrize(("schema", "children", "messages"), _REPEATED_CHILD)
def test_all_repeated_child_is_not_allowed(schema: str, children: str, messages: list[str]) -> None:
    assert [error.message for error in check(schema, children).errors] == messages


def test_all_each_member_once_is_valid() -> None:
    schema = _type_schema('<xs:all><xs:element name="a" minOccurs="0"/><xs:element name="b"/></xs:all>')
    assert check(schema, "<r><b/><a/></r>").valid
    assert check(schema, "<r><b/></r>").valid


_ALL_BASE: Final[str] = f'<xs:complexType name="base"><xs:all>{_AB}</xs:all></xs:complexType>'
_GROUP_ALL_BASE: Final[str] = (
    f'<xs:group name="g"><xs:all>{_AB}</xs:all></xs:group>'
    '<xs:complexType name="base"><xs:group ref="g"/></xs:complexType>'
)


def _extension_schema(base: str, derived: str) -> str:
    return (
        f'<xs:schema {_XS}>{base}<xs:element name="r"><xs:complexType><xs:complexContent>'
        f'<xs:extension base="base">{derived}</xs:extension></xs:complexContent></xs:complexType></xs:element>'
        "</xs:schema>"
    )


@pytest.mark.parametrize(
    "schema",
    [
        pytest.param(_extension_schema(_ALL_BASE, ""), id="direct-base"),
        pytest.param(_extension_schema(_GROUP_ALL_BASE, ""), id="group-base"),
        pytest.param(
            f'<xs:schema {_XS}>{_ALL_BASE}<xs:complexType name="middle"><xs:complexContent>'
            '<xs:extension base="base"/></xs:complexContent></xs:complexType>'
            '<xs:element name="r"><xs:complexType><xs:complexContent><xs:extension base="middle"/>'
            "</xs:complexContent></xs:complexType></xs:element></xs:schema>",
            id="empty-middle-type",
        ),
    ],
)
@pytest.mark.parametrize(
    ("children", "messages"),
    [
        pytest.param("<a/><b/>", [], id="declaration-order"),
        pytest.param("<b/><a/>", [], id="reverse-order"),
        pytest.param("<b/>", ["required element 'a' is missing"], id="required-missing"),
        pytest.param("<a/><b/><zz/>", ["element 'zz' is not allowed here"], id="undeclared"),
    ],
)
def test_all_base_extended_without_particles_matches_members(schema: str, children: str, messages: list[str]) -> None:
    assert [error.message for error in check(schema, f"<r>{children}</r>").errors] == messages


@pytest.mark.parametrize(
    ("occurs", "children", "messages"),
    [
        pytest.param(' minOccurs="0"', "", [], id="optional-empty"),
        pytest.param(' minOccurs="0"', "<a/><b/>", [], id="optional-filled"),
        pytest.param(
            "",
            "",
            ["required element 'a' is missing", "required element 'b' is missing"],
            id="required-empty",
        ),
        pytest.param("", "<b/><a/>", [], id="required-filled"),
    ],
)
def test_all_base_group_reference_occurrence_applies(occurs: str, children: str, messages: list[str]) -> None:
    base = (
        f'<xs:group name="g"><xs:all>{_AB}</xs:all></xs:group>'
        f'<xs:complexType name="base"><xs:group ref="g"{occurs}/></xs:complexType>'
    )
    assert [error.message for error in check(_extension_schema(base, ""), f"<r>{children}</r>").errors] == messages


@pytest.mark.parametrize(
    "schema",
    [
        pytest.param(_extension_schema(_ALL_BASE, f"<xs:all>{_AB}</xs:all>"), id="all-direct-base"),
        pytest.param(_extension_schema(_GROUP_ALL_BASE, f"<xs:all>{_AB}</xs:all>"), id="all-group-base"),
        pytest.param(
            _extension_schema(_ALL_BASE, '<xs:sequence><xs:element name="c"/></xs:sequence>'),
            id="sequence-direct-base",
        ),
        pytest.param(
            _extension_schema(_GROUP_ALL_BASE, '<xs:choice><xs:element name="c"/></xs:choice>'),
            id="choice-group-base",
        ),
        pytest.param(
            f'<xs:schema {_XS}>{_ALL_BASE}<xs:complexType name="middle"><xs:complexContent>'
            '<xs:extension base="base"/></xs:complexContent></xs:complexType>'
            '<xs:element name="r"><xs:complexType><xs:complexContent><xs:extension base="middle">'
            '<xs:sequence><xs:element name="c"/></xs:sequence></xs:extension></xs:complexContent>'
            "</xs:complexType></xs:element></xs:schema>",
            id="particles-below-empty-middle-type",
        ),
    ],
)
def test_all_base_extended_with_particles_raises(schema: str) -> None:
    with pytest.raises(ValueError, match="an xs:all content model cannot be extended with particles"):
        XMLSchema(schema)


_SEQUENCE_BASE: Final[str] = f'<xs:complexType name="base"><xs:sequence>{_AB}</xs:sequence></xs:complexType>'


@pytest.mark.parametrize(
    "schema",
    [
        pytest.param(
            f'<xs:schema {_XS}>{_SEQUENCE_BASE}<xs:complexType name="middle"><xs:complexContent>'
            f'<xs:extension base="base"><xs:all>{_AB}</xs:all></xs:extension></xs:complexContent></xs:complexType>'
            '<xs:element name="r" type="middle"/></xs:schema>',
            id="direct-all-over-sequence",
        ),
        pytest.param(
            f'<xs:schema {_XS}>{_SEQUENCE_BASE}<xs:group name="g"><xs:all>{_AB}</xs:all></xs:group>'
            '<xs:complexType name="middle"><xs:complexContent><xs:extension base="base"><xs:group ref="g"/>'
            "</xs:extension></xs:complexContent></xs:complexType>"
            '<xs:element name="r" type="middle"/></xs:schema>',
            id="group-all-over-sequence",
        ),
    ],
)
def test_all_extension_of_base_with_content_raises(schema: str) -> None:
    with pytest.raises(ValueError, match="an xs:all content model cannot extend a base type with content"):
        XMLSchema(schema)


def test_extension_of_builtin_base_chain_compiles() -> None:
    schema = (
        f'<xs:schema {_XS}><xs:complexType name="a"><xs:complexContent><xs:extension base="xs:anyType"/>'
        '</xs:complexContent></xs:complexType><xs:complexType name="b"><xs:complexContent>'
        '<xs:extension base="a"/></xs:complexContent></xs:complexType>'
        '<xs:element name="r" type="b"/></xs:schema>'
    )
    assert check(schema, "<r/>").valid


def test_extension_of_restriction_chain_compiles() -> None:
    schema = (
        f'<xs:schema {_XS}><xs:complexType name="a"><xs:complexContent><xs:restriction base="xs:anyType"/>'
        '</xs:complexContent></xs:complexType><xs:complexType name="b"><xs:complexContent>'
        '<xs:extension base="a"/></xs:complexContent></xs:complexType>'
        '<xs:element name="r" type="b"/></xs:schema>'
    )
    assert check(schema, "<r/>").valid


def test_empty_extension_of_sequence_beside_all_base() -> None:
    schema = (
        f'<xs:schema {_XS}><xs:complexType name="base"><xs:all><xs:element name="a"/></xs:all></xs:complexType>'
        '<xs:complexType name="seq"><xs:sequence><xs:element name="x"/></xs:sequence></xs:complexType>'
        '<xs:complexType name="t"><xs:complexContent><xs:extension base="seq"/></xs:complexContent></xs:complexType>'
        '<xs:element name="ext"><xs:complexType><xs:complexContent><xs:extension base="base"/>'
        "</xs:complexContent></xs:complexType></xs:element>"
        '<xs:element name="r" type="t"/></xs:schema>'
    )
    assert check(schema, "<r><x/></r>").valid
    assert not check(schema, "<r/>").valid


def test_empty_complex_content_beside_all_base() -> None:
    schema = (
        f'<xs:schema {_XS}><xs:complexType name="base"><xs:all><xs:element name="a"/></xs:all></xs:complexType>'
        '<xs:element name="ext"><xs:complexType><xs:complexContent><xs:extension base="base"/>'
        "</xs:complexContent></xs:complexType></xs:element>"
        '<xs:element name="r"><xs:complexType><xs:complexContent/></xs:complexType></xs:element></xs:schema>'
    )
    assert check(schema, "<r/>").valid


def test_sequence_extension_chain_compiles() -> None:
    schema = (
        f'<xs:schema {_XS}><xs:complexType name="a"><xs:sequence><xs:element name="x"/></xs:sequence></xs:complexType>'
        '<xs:complexType name="b"><xs:complexContent><xs:extension base="a"><xs:sequence>'
        '<xs:element name="y"/></xs:sequence></xs:extension></xs:complexContent></xs:complexType>'
        '<xs:element name="r"><xs:complexType><xs:complexContent><xs:extension base="b"><xs:sequence>'
        '<xs:element name="z"/></xs:sequence></xs:extension></xs:complexContent></xs:complexType></xs:element>'
        "</xs:schema>"
    )
    assert isinstance(XMLSchema(schema), XMLSchema)


@pytest.mark.parametrize(
    ("positions", "line"),
    [
        pytest.param(True, 4, id="tracked"),
        pytest.param(False, 0, id="untracked"),
    ],
)
def test_all_missing_member_reports_its_element_line(*, positions: bool, line: int) -> None:
    # the schema's xs:all node carries no source position, so the line must come from the element that lacks the member
    schema = (
        f'<xs:schema {_XS}><xs:element name="html"><xs:complexType><xs:all>'
        '<xs:element name="head" minOccurs="0"/><xs:element name="body"><xs:complexType><xs:all>'
        '<xs:element name="section"><xs:complexType><xs:all><xs:element name="h1"/><xs:element name="p"/>'
        "</xs:all></xs:complexType></xs:element>"
        "</xs:all></xs:complexType></xs:element></xs:all></xs:complexType></xs:element></xs:schema>"
    )
    document = parse(
        "<!doctype html>\n<html>\n<body>\n<section><p></p></section>\n</body>\n</html>", positions=positions
    )
    errors = XMLSchema(schema).validate(document).errors
    assert [(error.message, error.path, error.line) for error in errors] == [
        ("required element 'h1' is missing", "/html/body/section", line)
    ]


_OPTIONAL_ALL: Final[str] = f'<xs:all minOccurs="0">{_AB}</xs:all>'
_OPTIONAL_GROUP_REF: Final[str] = (
    f'<xs:schema {_XS}><xs:group name="g"><xs:all>{_AB}</xs:all></xs:group>'
    '<xs:element name="r"><xs:complexType><xs:group ref="g" minOccurs="0"/></xs:complexType></xs:element></xs:schema>'
)
_OPTIONAL_EXTENDED_ALL: Final[str] = (
    f'<xs:schema {_XS}><xs:complexType name="base">{_OPTIONAL_ALL}</xs:complexType>'
    '<xs:element name="r"><xs:complexType><xs:complexContent><xs:extension base="base"/>'
    "</xs:complexContent></xs:complexType></xs:element></xs:schema>"
)


@pytest.mark.parametrize(
    ("schema", "children", "messages"),
    [
        pytest.param(_type_schema(_OPTIONAL_ALL), "<r/>", [], id="direct-empty"),
        pytest.param(_type_schema(_OPTIONAL_ALL), "<r><b/><a/></r>", [], id="direct-all-present"),
        pytest.param(
            _type_schema(_OPTIONAL_ALL),
            "<r><a/></r>",
            ["required element 'b' is missing"],
            id="direct-partial-still-requires-members",
        ),
        pytest.param(_OPTIONAL_GROUP_REF, "<r/>", [], id="group-ref-empty"),
        pytest.param(_OPTIONAL_EXTENDED_ALL, "<r/>", [], id="extension-of-optional-all-empty"),
        pytest.param(
            _type_schema(
                f'<xs:complexContent><xs:restriction base="xs:anyType">{_OPTIONAL_ALL}</xs:restriction>'
                "</xs:complexContent>"
            ),
            "<r/>",
            [],
            id="restriction-empty",
        ),
    ],
)
def test_optional_all_accepts_empty_content(schema: str, children: str, messages: list[str]) -> None:
    assert [error.message for error in check(schema, children).errors] == messages


_ALL_GROUP_MEMBERS: Final[str] = '<xs:element name="a"/><xs:element name="b"/>'
_ALL_GROUP_DEF: Final[str] = f'<xs:group name="g"><xs:all>{_ALL_GROUP_MEMBERS}</xs:all></xs:group>'


@pytest.mark.parametrize(
    "schema",
    [
        pytest.param(
            f'<xs:schema {_XS}>{_ALL_GROUP_DEF}<xs:element name="r"><xs:complexType>'
            '<xs:group ref="g" maxOccurs="2"/></xs:complexType></xs:element></xs:schema>',
            id="group-ref-twice",
        ),
        pytest.param(
            f'<xs:schema {_XS}>{_ALL_GROUP_DEF}<xs:element name="r"><xs:complexType>'
            '<xs:group ref="g" maxOccurs="unbounded"/></xs:complexType></xs:element></xs:schema>',
            id="group-ref-unbounded",
        ),
        pytest.param(
            f'<xs:schema {_XS}><xs:element name="r"><xs:complexType>'
            f'<xs:all maxOccurs="2">{_ALL_GROUP_MEMBERS}</xs:all></xs:complexType></xs:element></xs:schema>',
            id="direct-all-twice",
        ),
        pytest.param(
            f'<xs:schema {_XS}><xs:group name="h"><xs:all maxOccurs="2">{_ALL_GROUP_MEMBERS}</xs:all></xs:group>'
            '<xs:element name="r"><xs:complexType><xs:group ref="h"/></xs:complexType></xs:element></xs:schema>',
            id="group-definition-all-twice",
        ),
    ],
)
def test_all_group_occurring_more_than_once_raises(schema: str) -> None:
    with pytest.raises(ValueError, match="an xs:all group may occur only once"):
        XMLSchema(schema)


def test_all_group_occurring_once_compiles() -> None:
    schema = (
        f'<xs:schema {_XS}>{_ALL_GROUP_DEF}<xs:element name="r"><xs:complexType>'
        '<xs:group ref="g" minOccurs="0" maxOccurs="1"/></xs:complexType></xs:element></xs:schema>'
    )
    assert check(schema, "<r><a/><b/></r>").valid
    assert check(schema, "<r/>").valid


@pytest.mark.parametrize(
    ("model", "compositor"),
    [
        pytest.param(f'<xs:all minOccurs="0">{_AB}</xs:all>', "all", id="all-min-occurs"),
        pytest.param(f'<xs:all maxOccurs="1">{_AB}</xs:all>', "all", id="all-max-occurs"),
        pytest.param('<xs:sequence minOccurs="0"><xs:element name="a"/></xs:sequence>', "sequence", id="sequence-min"),
        pytest.param('<xs:sequence maxOccurs="2"><xs:element name="a"/></xs:sequence>', "sequence", id="sequence-max"),
        pytest.param('<xs:choice minOccurs="0"><xs:element name="a"/></xs:choice>', "choice", id="choice-min-occurs"),
        pytest.param(
            '<xs:choice maxOccurs="unbounded"><xs:element name="a"/></xs:choice>', "choice", id="choice-max-occurs"
        ),
    ],
)
def test_named_group_model_occurs_raises(model: str, compositor: str) -> None:
    schema = (
        f'<xs:schema {_XS}><xs:group name="g">{model}</xs:group>'
        '<xs:element name="r"><xs:complexType><xs:group ref="g"/></xs:complexType></xs:element></xs:schema>'
    )
    message = f"xs:{compositor} in an xs:group cannot declare minOccurs or maxOccurs"
    with pytest.raises(ValueError, match=f"^{message}; set them on the xs:group reference$"):
        XMLSchema(schema)


def test_named_group_nested_particle_occurs_compiles() -> None:
    schema = (
        f'<xs:schema {_XS}><xs:group name="g"><xs:sequence>'
        '<xs:sequence minOccurs="0"><xs:element name="a"/></xs:sequence></xs:sequence></xs:group>'
        '<xs:element name="r"><xs:complexType><xs:group ref="g"/></xs:complexType></xs:element></xs:schema>'
    )
    assert check(schema, "<r/>").valid
    assert check(schema, "<r><a/></r>").valid


def test_repeated_sequence_group_compiles() -> None:
    schema = (
        f'<xs:schema {_XS}><xs:group name="s"><xs:sequence><xs:element name="c"/></xs:sequence></xs:group>'
        '<xs:element name="r"><xs:complexType><xs:group ref="s" maxOccurs="2"/></xs:complexType></xs:element>'
        "</xs:schema>"
    )
    assert check(schema, "<r><c/><c/></r>").valid


@pytest.mark.parametrize(
    "count", [pytest.param(1, id="one"), pytest.param(2, id="two"), pytest.param(12, id="grown-table")]
)
def test_all_group_of_each_type_keeps_its_members(count: int) -> None:
    types: Final[str] = "".join(
        f'<xs:complexType name="t{index}"><xs:all><xs:element name="m{index}"/></xs:all></xs:complexType>'
        for index in range(count)
    )
    elements: Final[str] = "".join(f'<xs:element name="e{index}" type="t{index}"/>' for index in range(count))
    schema: Final[XMLSchema] = XMLSchema(
        f'<xs:schema {_XS}>{types}<xs:element name="r"><xs:complexType><xs:sequence>{elements}</xs:sequence>'
        "</xs:complexType></xs:element></xs:schema>"
    )
    own: Final[str] = "".join(f"<e{index}><m{index}/></e{index}>" for index in range(count))
    other: Final[str] = "".join(f"<e{index}><m{index + 1}/></e{index}>" for index in range(count))
    assert schema.validate(parse_xml(f"<r>{own}</r>")).valid
    assert [error.message for error in schema.validate(parse_xml(f"<r>{other}</r>")).errors] == [
        message
        for index in range(count)
        for message in (f"element 'm{index + 1}' is not allowed here", f"required element 'm{index}' is missing")
    ]


_LINEAR_SIZES: Final[tuple[int, int]] = (320, 1280)


def test_all_children_naming_the_last_member_scale_linearly(work_steps: WorkSteps) -> None:
    def build(count: int) -> Callable[[], ValidationResult]:
        last = f"<item{count - 1}/>"
        return partial(_all_schema(_optional_members(count)).validate, parse_xml(f"<r>{last * count}</r>"))

    work_steps.assert_linear(build, _LINEAR_SIZES)
    # every child after the first repeats its member
    assert len(build(_LINEAR_SIZES[1])().errors) == 1_000


def _all_schema(members: str) -> XMLSchema:
    return XMLSchema(
        f'<xs:schema {_XS}><xs:element name="r"><xs:complexType><xs:all>{members}</xs:all>'
        "</xs:complexType></xs:element></xs:schema>"
    )


def _optional_members(count: int) -> str:
    return "".join(f'<xs:element name="item{index}" minOccurs="0"/>' for index in range(count))


def test_all_children_naming_no_member_scale_linearly(work_steps: WorkSteps) -> None:
    def build(count: int) -> Callable[[], ValidationResult]:
        return partial(_all_schema(_optional_members(count)).validate, parse_xml(f"<r>{'<zz/>' * count}</r>"))

    work_steps.assert_linear(build, _LINEAR_SIZES)
    # validate() lists at most 1,000 of the errors
    assert len(build(_LINEAR_SIZES[1])().errors) == 1_000


def test_all_repeated_children_scale_linearly(work_steps: WorkSteps) -> None:
    def build(count: int) -> Callable[[], ValidationResult]:
        return partial(_all_schema(_optional_members(count)).validate, parse_xml(f"<r>{'<item0/>' * count}</r>"))

    work_steps.assert_linear(build, _LINEAR_SIZES)
    # validate() lists at most 1,000 of the errors
    assert len(build(_LINEAR_SIZES[1])().errors) == 1_000


def test_all_groups_scale_linearly(work_steps: WorkSteps) -> None:
    def build(count: int) -> Callable[[], ValidationResult]:
        schema = XMLSchema(
            f'<xs:schema {_XS}><xs:element name="r"><xs:complexType><xs:sequence>'
            '<xs:element name="b" maxOccurs="unbounded"><xs:complexType><xs:all>'
            f"{_optional_members(count)}</xs:all></xs:complexType></xs:element>"
            "</xs:sequence></xs:complexType></xs:element></xs:schema>"
        )
        return partial(schema.validate, parse_xml(f"<r>{'<b><item0/></b>' * count}</r>"))

    work_steps.assert_linear(build, _LINEAR_SIZES)
    assert build(_LINEAR_SIZES[1])().valid


def test_named_type_and_group_ref() -> None:
    schema = (
        f"<xs:schema {_XS}>"
        '<xs:group name="g"><xs:sequence><xs:element name="a" type="xs:string"/></xs:sequence></xs:group>'
        '<xs:complexType name="t"><xs:group ref="g"/><xs:attribute name="k" type="xs:string"/></xs:complexType>'
        '<xs:element name="r" type="t"/></xs:schema>'
    )
    assert check(schema, '<r k="v"><a>x</a></r>').valid
    assert not check(schema, "<r><b>x</b></r>").valid


def test_element_ref() -> None:
    schema = (
        f"<xs:schema {_XS}>"
        '<xs:element name="item" type="xs:string"/>'
        '<xs:element name="r"><xs:complexType><xs:sequence>'
        '<xs:element ref="item" maxOccurs="unbounded"/>'
        "</xs:sequence></xs:complexType></xs:element></xs:schema>"
    )
    assert check(schema, "<r><item>a</item><item>b</item></r>").valid


def test_attributes_use_fixed_default_prohibited() -> None:
    schema = (
        f'<xs:schema {_XS}><xs:element name="r"><xs:complexType>'
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
        f"<xs:schema {_XS}>"
        '<xs:attribute name="gid" type="xs:int"/>'
        '<xs:attributeGroup name="ag"><xs:attribute ref="gid" use="required"/></xs:attributeGroup>'
        '<xs:element name="r"><xs:complexType><xs:attributeGroup ref="ag"/></xs:complexType></xs:element>'
        "</xs:schema>"
    )
    assert check(schema, '<r gid="5"/>').valid
    assert not check(schema, "<r/>").valid


@pytest.mark.parametrize("namespaces", [pytest.param(False, id="attributes"), pytest.param(True, id="namespaces")])
@pytest.mark.parametrize("count", [pytest.param(0, id="none"), pytest.param(3, id="few"), pytest.param(40, id="many")])
def test_declaration_is_read_among_ignored_attributes(count: int, *, namespaces: bool) -> None:
    extra: Final[str] = _ignored_attributes(count, namespaces=namespaces)
    schema: Final[XMLSchema] = XMLSchema(
        f'<xs:schema {_XS} xmlns:t="http://www.w3.org/2001/XMLSchema"{extra}>'
        f'<xs:element{extra} name="r"><xs:complexType{extra}><xs:sequence{extra}>'
        f'<xs:element{extra} name="item" type="t:int" minOccurs="0" maxOccurs="unbounded"/>'
        f'</xs:sequence><xs:attribute{extra} name="a" type="t:int" use="required" fixed="7"/>'
        "</xs:complexType></xs:element></xs:schema>"
    )
    documents: Final[list[str]] = [
        '<r a="7"/>',
        '<r a="7"><item>1</item></r>',
        "<r><item>1</item></r>",
        '<r a="8"/>',
        '<r a="7"><item>x</item></r>',
        '<r a="7"><other/></r>',
    ]
    assert [schema.is_valid(parse_xml(document)) for document in documents] == [True, True, False, False, False, False]


def _ignored_attributes(count: int, *, namespaces: bool = False) -> str:
    # names that share a prefix with the properties XSD reads, so a sorted list must order them apart from those
    near_misses: Final[tuple[str, ...]] = (
        "name2",
        "names",
        "ref2",
        "refs",
        "typ",
        "types",
        "use2",
        "uses",
        "fixed2",
        "value",
        "values",
        "a",
    )
    names: Final[list[str]] = [*near_misses, *(f"z{index}" for index in range(count))][:count]
    if namespaces:
        return "".join(f' xmlns:{name}="urn:{name}"' for name in names)
    return "".join(f' {name}="0"' for name in names)


@pytest.mark.parametrize(
    "schema",
    [
        pytest.param(
            f'<xs:schema {_XS}><xs:element name="a" xmlns:xs="urn:other"/><xs:element name="v" type="xs:int"/>'
            "</xs:schema>",
            id="sibling-after-a-shadowing-element",
        ),
        pytest.param(
            '<schema xmlns="http://www.w3.org/2001/XMLSchema"><element name="v" type="int"/></schema>',
            id="default-namespace",
        ),
    ],
)
def test_qname_prefix_resolves_in_its_scope(schema: str) -> None:
    checked: Final[XMLSchema] = XMLSchema(schema)
    assert checked.is_valid(parse_xml("<v>1</v>"))
    assert not checked.is_valid(parse_xml("<v>x</v>"))


@pytest.mark.parametrize(
    "schema",
    [
        pytest.param(
            f'<xs:schema {_XS} xmlns:t="http://www.w3.org/2001/XMLSchema">'
            '<xs:element name="v" type="t:int" xmlns:t="urn:other"/></xs:schema>',
            id="shadowed-on-the-declaration",
        ),
        pytest.param(f'<xs:schema {_XS}><xs:element name="v" type="q:int"/></xs:schema>', id="unbound-prefix"),
    ],
)
def test_qname_prefix_out_of_scope_does_not_resolve(schema: str) -> None:
    with pytest.raises(ValueError, match="does not resolve"):
        XMLSchema(schema)


def test_declaration_properties_scale_linearly(work_steps: WorkSteps) -> None:
    def validation(count: int) -> Callable[[], object]:
        schema: Final[XMLSchema] = XMLSchema(_declaration_schema(count))
        return partial(schema.is_valid, parse_xml(f"<r>{'<g/>' * count}</r>"))

    work_steps.assert_linear(validation, (250, 1000))


def _declaration_schema(count: int) -> str:
    return (
        f'<xs:schema {_XS}><xs:element name="r"><xs:complexType><xs:sequence>'
        '<xs:element name="g" minOccurs="0" maxOccurs="unbounded"><xs:complexType>'
        f'<xs:attribute{_ignored_attributes(count)} name="x" type="xs:int"/>'
        "</xs:complexType></xs:element></xs:sequence></xs:complexType></xs:element></xs:schema>"
    )


def test_namespace_declarations_scale_linearly(work_steps: WorkSteps) -> None:
    def validation(count: int) -> Callable[[], object]:
        declarations: Final[str] = _ignored_attributes(count, namespaces=True)
        schema: Final[XMLSchema] = XMLSchema(
            f'<xs:schema {_XS} xmlns:t="http://www.w3.org/2001/XMLSchema"><xs:element name="r"><xs:complexType>'
            '<xs:sequence><xs:element name="g" minOccurs="0" maxOccurs="unbounded"><xs:complexType>'
            f'<xs:attribute{declarations} name="x" type="t:int"/>'
            "</xs:complexType></xs:element></xs:sequence></xs:complexType></xs:element></xs:schema>"
        )
        return partial(schema.is_valid, parse_xml(f"<r>{'<g/>' * count}</r>"))

    work_steps.assert_linear(validation, (250, 1000))


def test_facet_names_compile_linearly(work_steps: WorkSteps) -> None:
    def compilation(count: int) -> Callable[[], object]:
        facets: Final[str] = "".join(f'<xs:enumeration value="v{index}"/>' for index in range(count))
        return partial(
            XMLSchema,
            f'<xs:schema {_XS}><xs:simpleType name="t"><xs:restriction{_ignored_attributes(count)} base="xs:string">'
            f"{facets}</xs:restriction></xs:simpleType></xs:schema>",
        )

    work_steps.assert_linear(compilation, (250, 1000))


def test_schema_namespace_scope_compiles_linearly(work_steps: WorkSteps) -> None:
    def compilation(count: int) -> Callable[[], object]:
        declarations: Final[str] = _ignored_attributes(count, namespaces=True)
        elements: Final[str] = "".join(f'<xs:element name="e{index}"/>' for index in range(count))
        return partial(XMLSchema, f"<xs:schema {_XS}{declarations}>{elements}</xs:schema>")

    work_steps.assert_linear(compilation, (250, 1000))


def test_complex_content_extension() -> None:
    schema = (
        f"<xs:schema {_XS}>"
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
        f'<xs:schema {_XS}><xs:element name="r"><xs:complexType><xs:simpleContent>'
        '<xs:extension base="xs:int"><xs:attribute name="u" type="xs:string"/></xs:extension>'
        "</xs:simpleContent></xs:complexType></xs:element></xs:schema>"
    )
    assert check(schema, '<r u="x">42</r>').valid
    assert not check(schema, '<r u="x">notint</r>').valid
    assert not check(schema, '<r u="x"><child/></r>').valid


_PERCENT: Final[str] = (
    '<xs:simpleType name="percent"><xs:restriction base="xs:integer">'
    '<xs:minInclusive value="0"/><xs:maxInclusive value="100"/></xs:restriction></xs:simpleType>'
)
_PERCENT_WITH_UNIT: Final[str] = (
    '<xs:complexType name="measure"><xs:simpleContent>'
    '<xs:extension base="percent"><xs:attribute name="unit" type="xs:string"/></xs:extension>'
    "</xs:simpleContent></xs:complexType>"
)
_SPACED_CODE: Final[str] = (
    '<xs:simpleType name="code"><xs:restriction base="xs:string">'
    '<xs:whiteSpace value="collapse"/><xs:enumeration value="a b"/></xs:restriction></xs:simpleType>'
)
_CURRENCY: Final[str] = (
    '<xs:simpleType name="currency"><xs:restriction base="xs:string">'
    '<xs:enumeration value="EUR"/><xs:enumeration value="USD"/></xs:restriction></xs:simpleType>'
)
_TENTH: Final[str] = '<xs:maxInclusive value="10"/>'
_LOWER: Final[str] = '<xs:pattern value="[a-z]+"/>'
_FROM_FIVE: Final[str] = (
    '<xs:simpleType><xs:restriction base="percent"><xs:minInclusive value="5"/></xs:restriction></xs:simpleType>'
)
_MONEY: Final[str] = (
    '<xs:complexType name="money"><xs:simpleContent><xs:extension base="currency">'
    '<xs:attribute name="cents" type="xs:int"/></xs:extension></xs:simpleContent></xs:complexType>'
)
_FROM_U: Final[str] = (
    '<xs:simpleType><xs:restriction base="currency"><xs:pattern value="U.*"/></xs:restriction></xs:simpleType>'
)
_THREE_LETTERS: Final[str] = '<xs:pattern value="[A-Z]{3}"/>'


@pytest.mark.parametrize(
    ("types", "derivation", "value", "valid"),
    [
        pytest.param(_PERCENT, '<xs:extension base="percent"/>', "50", True, id="extends-simple-type-in-range"),
        pytest.param(_PERCENT, '<xs:extension base="percent"/>', "-5000", False, id="extends-simple-type-below"),
        pytest.param(_PERCENT, '<xs:extension base="percent"/>', "abc", False, id="extends-simple-type-lexical"),
        pytest.param(
            _PERCENT, f'<xs:restriction base="percent">{_TENTH}</xs:restriction>', "10", True, id="own-facet-met"
        ),
        pytest.param(
            _PERCENT, f'<xs:restriction base="percent">{_TENTH}</xs:restriction>', "50", False, id="own-facet-broken"
        ),
        pytest.param(
            _PERCENT, f'<xs:restriction base="percent">{_TENTH}</xs:restriction>', "-1", False, id="base-facet-broken"
        ),
        pytest.param("", f'<xs:restriction base="xs:int">{_TENTH}</xs:restriction>', "50", False, id="builtin-base"),
        pytest.param("", '<xs:extension base="xs:int"/>', " 50 ", True, id="builtin-base-whitespace"),
        pytest.param(
            "", f'<xs:restriction base="xs:string">{_LOWER}</xs:restriction>', "bob", True, id="own-pattern-met"
        ),
        pytest.param(
            "", f'<xs:restriction base="xs:string">{_LOWER}</xs:restriction>', "BOB!", False, id="own-pattern-broken"
        ),
        pytest.param(_CURRENCY, '<xs:extension base="currency"/>', "USD", True, id="extends-enumeration-member"),
        pytest.param(_CURRENCY, '<xs:extension base="currency"/>', "XXX", False, id="extends-enumeration-other"),
        pytest.param(_SPACED_CODE, '<xs:extension base="code"/>', " a  b ", True, id="base-whitespace-met"),
        pytest.param(_SPACED_CODE, '<xs:extension base="code"/>', "a c", False, id="base-whitespace-broken"),
        pytest.param(
            _PERCENT + _PERCENT_WITH_UNIT,
            '<xs:extension base="measure"/>',
            "100",
            True,
            id="extends-complex-type-in-range",
        ),
        pytest.param(
            _PERCENT + _PERCENT_WITH_UNIT,
            '<xs:extension base="measure"/>',
            "101",
            False,
            id="extends-complex-type-above",
        ),
        pytest.param(
            _PERCENT + _PERCENT_WITH_UNIT,
            f'<xs:restriction base="measure">{_TENTH}</xs:restriction>',
            "50",
            False,
            id="restricts-complex-type-own-facet",
        ),
        pytest.param(
            _PERCENT + _PERCENT_WITH_UNIT,
            f'<xs:restriction base="measure">{_TENTH}</xs:restriction>',
            "-1",
            False,
            id="restricts-complex-type-base-facet",
        ),
        pytest.param(
            _PERCENT + _PERCENT_WITH_UNIT,
            f'<xs:restriction base="measure">{_FROM_FIVE}{_TENTH}</xs:restriction>',
            "7",
            True,
            id="inline-type-beside-base-met",
        ),
        pytest.param(
            _PERCENT + _PERCENT_WITH_UNIT,
            f'<xs:restriction base="measure">{_FROM_FIVE}{_TENTH}</xs:restriction>',
            "4",
            False,
            id="inline-type-beside-base-broken",
        ),
        pytest.param(
            _PERCENT + _PERCENT_WITH_UNIT,
            f'<xs:restriction base="measure">{_FROM_FIVE}{_TENTH}</xs:restriction>',
            "11",
            False,
            id="facet-after-inline-type-broken",
        ),
        pytest.param(
            _CURRENCY + _MONEY,
            f'<xs:restriction base="money">{_FROM_U}{_THREE_LETTERS}</xs:restriction>',
            "USD",
            True,
            id="inline-type-beside-base-lists-met",
        ),
        pytest.param(
            _CURRENCY + _MONEY,
            f'<xs:restriction base="money">{_FROM_U}{_THREE_LETTERS}</xs:restriction>',
            "EUR",
            False,
            id="inline-type-pattern-broken",
        ),
        pytest.param(
            _CURRENCY + _MONEY,
            f'<xs:restriction base="money">{_FROM_U}{_THREE_LETTERS}</xs:restriction>',
            "UAH",
            False,
            id="base-enumeration-beside-inline-type-broken",
        ),
    ],
)
def test_simple_content_keeps_the_facets_of_its_base(types: str, derivation: str, value: str, *, valid: bool) -> None:
    # the text of a simple-content element was checked against the built-in type at the root of its base alone, so
    # giving a restricted value an attribute dropped every facet on it
    schema = (
        f'<xs:schema {_XS}>{types}<xs:element name="r"><xs:complexType>'
        f"<xs:simpleContent>{derivation}</xs:simpleContent></xs:complexType></xs:element></xs:schema>"
    )
    assert check(schema, f"<r>{value}</r>").valid is valid


def test_simple_content_base_type_named_by_an_element() -> None:
    schema = f'<xs:schema {_XS}>{_PERCENT}{_PERCENT_WITH_UNIT}<xs:element name="r" type="measure"/></xs:schema>'
    assert check(schema, '<r unit="%">50</r>').valid
    assert not check(schema, '<r unit="%">500</r>').valid


def test_mixed_content() -> None:
    schema = (
        f'<xs:schema {_XS}><xs:element name="r"><xs:complexType mixed="true"><xs:sequence>'
        '<xs:element name="a" type="xs:string" minOccurs="0"/>'
        "</xs:sequence></xs:complexType></xs:element></xs:schema>"
    )
    assert check(schema, "<r>text <a>x</a> more</r>").valid
    non_mixed = (
        f'<xs:schema {_XS}><xs:element name="r"><xs:complexType><xs:sequence>'
        '<xs:element name="a" type="xs:string" minOccurs="0"/>'
        "</xs:sequence></xs:complexType></xs:element></xs:schema>"
    )
    assert not check(non_mixed, "<r>text<a>x</a></r>").valid


def test_empty_content_type() -> None:
    schema = f'<xs:schema {_XS}><xs:element name="r"><xs:complexType/></xs:element></xs:schema>'
    assert check(schema, "<r/>").valid
    assert not check(schema, "<r><child/></r>").valid


def test_simple_type_restriction_chain() -> None:
    schema = (
        f"<xs:schema {_XS}>"
        '<xs:simpleType name="small"><xs:restriction base="xs:int"><xs:maxInclusive value="9"/>'
        "</xs:restriction></xs:simpleType>"
        '<xs:element name="v" type="small"/></xs:schema>'
    )
    assert check(schema, "<v>5</v>").valid
    assert not check(schema, "<v>50</v>").valid


def test_target_namespace_qualified() -> None:
    schema = (
        f'<xs:schema {_XS} targetNamespace="urn:x" xmlns="urn:x" elementFormDefault="qualified">'
        '<xs:element name="r"><xs:complexType><xs:sequence>'
        '<xs:element name="a" type="xs:string"/></xs:sequence></xs:complexType></xs:element></xs:schema>'
    )
    assert check(schema, '<r xmlns="urn:x"><a>x</a></r>').valid
    assert not check(schema, "<r><a>x</a></r>").valid  # wrong namespace


def test_no_namespace_root_matches_a_no_namespace_declaration() -> None:
    # both sides carry the absent namespace as a NULL, empty URI that memcmp may not receive
    assert check(typed("xs:string"), "<v>x</v>") == ValidationResult(valid=True, errors=())


def test_many_namespace_declarations_keep_the_xs_binding() -> None:
    # Nine xmlns declarations grow the in-scope namespace stack past its initial capacity; xmlns:xs comes first,
    # so a grow that failed to copy the existing bindings would drop the xs binding and stop recognizing xs:element.
    dummies = " ".join(f'xmlns:p{index}="urn:p{index}"' for index in range(8))
    schema = f'<xs:schema {_XS} {dummies}><xs:element name="a" type="xs:string"/></xs:schema>'
    assert check(schema, "<a>x</a>").valid
    assert not check(schema, "<a><child/></a>").valid


def test_root_errors() -> None:
    schema = f'<xs:schema {_XS}><xs:element name="known" type="xs:string"/></xs:schema>'
    assert not check(schema, "<unknown/>").valid


def test_malformed_schema_raises() -> None:
    with pytest.raises(ValueError, match="malformed schema"):
        XMLSchema(f"<xs:schema {_XS}><xs:element name=></xs:schema>")


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
        f'<xs:schema {_XS}><xs:element name="r"><xs:complexType><xs:sequence>'
        '<xs:element name="n" type="xs:int"/></xs:sequence></xs:complexType></xs:element></xs:schema>'
    )
    result = check(schema, "<r><n>x</n></r>")
    assert not result.valid
    assert result.errors[0].path == "/r/n"


def test_schema_from_parsed_document() -> None:
    validator = XMLSchema(parse_xml(typed("xs:int")))
    assert validator.is_valid(parse_xml("<v>7</v>"))


def test_validate_element_directly() -> None:
    element = parse_xml("<wrap><v>7</v></wrap>").children[0].children[0]
    assert XMLSchema(typed("xs:int")).is_valid(element)


@pytest.mark.parametrize(
    ("type_name", "value", "ok"),
    [
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
        pytest.param("xs:duration", "PT1.5S", True, id="duration-fraction-seconds"),
        pytest.param("xs:duration", "P1DT2H3M4.25S", True, id="duration-fraction-after-all-fields"),
        pytest.param("xs:duration", "-PT0.5S", True, id="duration-negative-fraction"),
        pytest.param("xs:duration", "PT0.0S", True, id="duration-zero-fraction"),
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
    schema = f'<xs:schema {_XS}><xs:element name="v" type="{type_name}"/></xs:schema>'
    assert xsd_ok(schema, f"<v>{value}</v>") is ok


def xsd_ok(schema: str, xml: str) -> bool:
    return XMLSchema(schema).validate(parse_xml(xml)).valid


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
        pytest.param("a{0}b", "b", True, id="repeat-zero"),
        pytest.param("a{0,2}", "aa", True, id="repeat-zero-two"),
        pytest.param("(a|)b", "b", True, id="empty-alternative"),
    ],
)
def test_regex_edges(pattern: str, value: str, ok: bool) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]  # a pytest parametrize value, not a boolean-trap call site
    assert pattern_ok(pattern, value) is ok


def pattern_ok(pattern: str, value: str) -> bool:
    return xsd_ok(edge_restricted(f'<xs:pattern value="{pattern}"/>'), f"<v>{value}</v>")


def edge_restricted(facets: str, base: str = "xs:string") -> str:
    return (
        f'<xs:schema {_XS}><xs:element name="v"><xs:simpleType>'
        f'<xs:restriction base="{base}">{facets}</xs:restriction></xs:simpleType></xs:element></xs:schema>'
    )


def test_enumeration_numeric_and_growth() -> None:
    facets = "".join(f'<xs:enumeration value="{n}"/>' for n in range(6))
    assert xsd_ok(edge_restricted(facets, "xs:int"), "<v>3</v>")
    assert not xsd_ok(edge_restricted(facets, "xs:int"), "<v>9</v>")


def test_multiple_patterns_growth() -> None:
    facets = "".join(f'<xs:pattern value="[a-z]{{{n}}}"/>' for n in range(1, 6))
    assert xsd_ok(edge_restricted(facets), "<v>abc</v>")
    assert not xsd_ok(edge_restricted(facets), "<v>abcdef</v>")


def test_length_exact_mismatch() -> None:
    assert not xsd_ok(edge_restricted('<xs:length value="2"/>'), "<v>abcd</v>")


def test_many_elements_edecl_growth() -> None:
    parts = "".join(f'<xs:element name="e{n}" type="xs:string"/>' for n in range(12))
    body = "".join(f"<e{n}>x</e{n}>" for n in range(12))
    schema = (
        f'<xs:schema {_XS}><xs:element name="r"><xs:complexType><xs:sequence>{parts}'
        f"</xs:sequence></xs:complexType></xs:element></xs:schema>"
    )
    assert xsd_ok(schema, f"<r>{body}</r>")


def test_schema_with_whitespace_and_comment() -> None:
    schema = (
        f"<xs:schema {_XS}>\n  <!-- a comment -->\n"
        '  <xs:element name="r"><xs:complexType><xs:sequence>\n'
        '    <xs:element name="a" type="xs:string"/>\n'
        "  </xs:sequence></xs:complexType></xs:element>\n</xs:schema>"
    )
    assert xsd_ok(schema, "<r><a>x</a></r>")


def test_simple_type_list_no_restriction() -> None:
    schema = (
        f'<xs:schema {_XS}><xs:element name="v"><xs:simpleType>'
        '<xs:list itemType="xs:int"/></xs:simpleType></xs:element></xs:schema>'
    )
    assert xsd_ok(schema, "<v>1 2 3</v>")


def test_restriction_nested_simpletype() -> None:
    schema = (
        f'<xs:schema {_XS}><xs:element name="v"><xs:simpleType><xs:restriction>'
        '<xs:simpleType><xs:restriction base="xs:int"/></xs:simpleType>'
        '<xs:maxInclusive value="9"/>'
        "</xs:restriction></xs:simpleType></xs:element></xs:schema>"
    )
    assert xsd_ok(schema, "<v>5</v>")
    assert not xsd_ok(schema, "<v>50</v>")


def test_restriction_with_foreign_facet_child() -> None:
    schema = (
        f'<xs:schema {_XS} xmlns:x="urn:x"><xs:element name="v"><xs:simpleType>'
        '<xs:restriction base="xs:string"><x:note>ignored</x:note>'
        '<xs:minLength value="2"/></xs:restriction></xs:simpleType></xs:element></xs:schema>'
    )
    assert xsd_ok(schema, "<v>ab</v>")
    assert not xsd_ok(schema, "<v>a</v>")


def test_named_simpletype_attribute() -> None:
    schema = (
        f"<xs:schema {_XS}>"
        '<xs:simpleType name="ranged"><xs:restriction base="xs:int"><xs:maxInclusive value="5"/>'
        "</xs:restriction></xs:simpleType>"
        '<xs:element name="r"><xs:complexType>'
        '<xs:attribute name="a" type="ranged"/></xs:complexType></xs:element></xs:schema>'
    )
    assert xsd_ok(schema, '<r a="3"/>')
    assert not xsd_ok(schema, '<r a="9"/>')


def test_inline_simpletype_attribute() -> None:
    schema = (
        f'<xs:schema {_XS}><xs:element name="r"><xs:complexType>'
        '<xs:attribute name="a"><xs:simpleType><xs:restriction base="xs:int">'
        '<xs:maxInclusive value="5"/></xs:restriction></xs:simpleType></xs:attribute>'
        "</xs:complexType></xs:element></xs:schema>"
    )
    assert xsd_ok(schema, '<r a="3"/>')
    assert not xsd_ok(schema, '<r a="9"/>')


def test_simple_content_restriction() -> None:
    schema = (
        f'<xs:schema {_XS}><xs:element name="r"><xs:complexType><xs:simpleContent>'
        '<xs:restriction base="xs:int"/></xs:simpleContent></xs:complexType></xs:element></xs:schema>'
    )
    assert xsd_ok(schema, "<r>42</r>")
    assert not xsd_ok(schema, "<r>x</r>")


def test_instance_with_foreign_prefixed_attribute() -> None:
    schema = (
        f'<xs:schema {_XS}><xs:element name="r"><xs:complexType>'
        '<xs:attribute name="a" type="xs:string"/></xs:complexType></xs:element></xs:schema>'
    )
    xml = '<r xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" a="v" xsi:nil="true"/>'
    assert xsd_ok(schema, xml)


_XSI: Final[str] = 'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
_SIMPLE_ROUTES: Final[list[pytest.ParameterSet]] = [
    pytest.param(f'<xs:schema {_XS}><xs:element name="v" type="xs:int"/></xs:schema>', "<v{attrs}>5</v>", id="builtin"),
    pytest.param(
        f'<xs:schema {_XS}><xs:simpleType name="n"><xs:restriction base="xs:int"/></xs:simpleType>'
        '<xs:element name="v" type="n"/></xs:schema>',
        "<v{attrs}>5</v>",
        id="named",
    ),
    pytest.param(
        f'<xs:schema {_XS}><xs:element name="v"><xs:simpleType><xs:restriction base="xs:int"/></xs:simpleType>'
        "</xs:element></xs:schema>",
        "<v{attrs}>5</v>",
        id="inline",
    ),
    pytest.param(
        f'<xs:schema {_XS}><xs:element name="w"><xs:complexType><xs:sequence>'
        '<xs:element name="g" type="xs:int"/></xs:sequence></xs:complexType></xs:element></xs:schema>',
        "<w><g{attrs}>5</g></w>",
        id="local",
    ),
    pytest.param(
        f'<xs:schema {_XS}><xs:element name="g" type="xs:int"/><xs:element name="w"><xs:complexType>'
        '<xs:sequence><xs:element ref="g"/></xs:sequence></xs:complexType></xs:element></xs:schema>',
        "<w><g{attrs}>5</g></w>",
        id="ref",
    ),
]
_SIMPLE_REJECTED_ATTRS: Final[list[pytest.ParameterSet]] = [
    pytest.param(' a="1"', id="undeclared"),
    pytest.param(f' {_XSI} xsi:foo="1"', id="xsi-other-local-name"),
    pytest.param(' xmlns:x="urn:x" x:nil="false"', id="other-namespace-nil"),
    pytest.param(' xml:lang="en"', id="xml-namespace"),
]
_SIMPLE_ALLOWED_ATTRS: Final[list[pytest.ParameterSet]] = [
    pytest.param(' xmlns:x="urn:x"', id="namespace-declaration"),
    pytest.param(' xmlns=""', id="default-namespace-declaration"),
    pytest.param(f' {_XSI} xsi:type="xs:int"', id="xsi-type"),
    pytest.param(f' {_XSI} xsi:nil="false"', id="xsi-nil"),
    pytest.param(f' {_XSI} xsi:schemaLocation="urn:x a.xsd"', id="xsi-schemaLocation"),
    pytest.param(f' {_XSI} xsi:noNamespaceSchemaLocation="a.xsd"', id="xsi-noNamespaceSchemaLocation"),
    pytest.param(' xmlns:i="http://www.w3.org/2001/XMLSchema-instance" i:nil="false"', id="xsi-under-other-prefix"),
]


@pytest.mark.parametrize(("schema", "template"), _SIMPLE_ROUTES)
@pytest.mark.parametrize("attrs", _SIMPLE_REJECTED_ATTRS)
def test_simple_typed_element_rejects_attribute(schema: str, template: str, attrs: str) -> None:
    errors = XMLSchema(schema).validate(parse_xml(template.format(attrs=attrs))).errors
    assert [error.message for error in errors] == ["simple-typed element must not have attributes"]


@pytest.mark.parametrize(("schema", "template"), _SIMPLE_ROUTES)
@pytest.mark.parametrize("attrs", _SIMPLE_ALLOWED_ATTRS)
def test_simple_typed_element_keeps_allowed_attributes(schema: str, template: str, attrs: str) -> None:
    assert xsd_ok(schema, template.format(attrs=attrs))


def test_extension_without_own_particle() -> None:
    schema = (
        f"<xs:schema {_XS}>"
        '<xs:complexType name="base"><xs:sequence><xs:element name="a" type="xs:string"/></xs:sequence>'
        "</xs:complexType>"
        '<xs:element name="r"><xs:complexType><xs:complexContent>'
        '<xs:extension base="base"><xs:attribute name="k" type="xs:string"/></xs:extension>'
        "</xs:complexContent></xs:complexType></xs:element></xs:schema>"
    )
    assert xsd_ok(schema, '<r k="v"><a>x</a></r>')


def _extension_chain(middle: str) -> str:
    return (
        f"<xs:schema {_XS}>"
        '<xs:complexType name="base"><xs:sequence><xs:element name="a" type="xs:int"/></xs:sequence></xs:complexType>'
        f'<xs:complexType name="middle"><xs:complexContent>{middle}</xs:complexContent></xs:complexType>'
        '<xs:complexType name="derived"><xs:complexContent><xs:extension base="middle">'
        '<xs:sequence><xs:element name="c" type="xs:int"/></xs:sequence>'
        "</xs:extension></xs:complexContent></xs:complexType>"
        '<xs:element name="r" type="derived"/></xs:schema>'
    )


_EXTENDED_TWICE: Final[str] = _extension_chain(
    '<xs:extension base="base"><xs:sequence><xs:element name="b" type="xs:int"/></xs:sequence></xs:extension>'
)
_EXTENDED_OVER_BARE_STEP: Final[str] = _extension_chain('<xs:extension base="base"/>')
_EXTENDED_OVER_RESTRICTION: Final[str] = _extension_chain(
    '<xs:restriction base="base"><xs:sequence><xs:element name="b" type="xs:int"/></xs:sequence></xs:restriction>'
)


@pytest.mark.parametrize(
    ("schema", "xml", "valid"),
    [
        pytest.param(_EXTENDED_TWICE, "<r><a>1</a><b>2</b><c>3</c></r>", True, id="twice-whole-chain"),
        pytest.param(_EXTENDED_TWICE, "<r><b>2</b><c>3</c></r>", False, id="twice-root-content-missing"),
        pytest.param(_EXTENDED_TWICE, "<r><a>1</a><c>3</c></r>", False, id="twice-middle-content-missing"),
        pytest.param(_EXTENDED_TWICE, "<r><b>2</b><a>1</a><c>3</c></r>", False, id="twice-out-of-order"),
        pytest.param(_EXTENDED_TWICE, "<r><a>x</a><b>2</b><c>3</c></r>", False, id="twice-root-content-typed"),
        pytest.param(_EXTENDED_OVER_BARE_STEP, "<r><a>1</a><c>3</c></r>", True, id="bare-step-whole-chain"),
        pytest.param(_EXTENDED_OVER_BARE_STEP, "<r><c>3</c></r>", False, id="bare-step-root-content-missing"),
        pytest.param(_EXTENDED_OVER_RESTRICTION, "<r><b>2</b><c>3</c></r>", True, id="restriction-replaces-its-base"),
        pytest.param(_EXTENDED_OVER_RESTRICTION, "<r><a>1</a><b>2</b><c>3</c></r>", False, id="restriction-ends-chain"),
    ],
)
def test_extension_chain_keeps_every_base(schema: str, xml: str, valid: bool) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]  # parametrize value
    assert xsd_ok(schema, xml) is valid


@pytest.mark.parametrize(
    ("xml", "valid"),
    [
        pytest.param("<r><a/><b/><c/></r>", True, id="sequence-group-after-base"),
        pytest.param("<r><a/><b/></r>", False, id="sequence-group-missing"),
    ],
)
def test_sequence_group_extends_base_with_content(xml: str, valid: bool) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]  # parametrize value
    sequence_group: str = '<xs:group name="s"><xs:sequence><xs:element name="c"/></xs:sequence></xs:group>'
    schema = (
        f"<xs:schema {_XS}>{_SEQUENCE_BASE}{sequence_group}"
        '<xs:complexType name="middle"><xs:complexContent><xs:extension base="base"><xs:group ref="s"/>'
        "</xs:extension></xs:complexContent></xs:complexType>"
        '<xs:element name="r" type="middle"/></xs:schema>'
    )
    assert xsd_ok(schema, xml) is valid


def test_simple_typed_element_with_child() -> None:
    schema = f'<xs:schema {_XS}><xs:element name="v" type="xs:int"/></xs:schema>'
    assert not xsd_ok(schema, "<v><child/></v>")


_INLINE_STRING: Final[str] = (
    '<xs:simpleType><xs:restriction base="xs:string"><xs:pattern value="[a-z]+"/></xs:restriction></xs:simpleType>'
)


@pytest.mark.parametrize(
    ("schema", "document"),
    [
        pytest.param(
            f'<xs:schema {_XS}><xs:element name="v">{_INLINE_STRING}</xs:element></xs:schema>',
            "<v>bob<child>x</child></v>",
            id="root",
        ),
        pytest.param(
            f'<xs:schema {_XS}><xs:element name="r"><xs:complexType><xs:sequence>'
            f'<xs:element name="v">{_INLINE_STRING}</xs:element>'
            "</xs:sequence></xs:complexType></xs:element></xs:schema>",
            "<r><v>bob<child>x</child></v></r>",
            id="sequence-particle",
        ),
        pytest.param(
            f'<xs:schema {_XS}><xs:element name="v">{_INLINE_STRING}</xs:element>'
            '<xs:element name="r"><xs:complexType><xs:sequence><xs:element ref="v"/></xs:sequence>'
            "</xs:complexType></xs:element></xs:schema>",
            "<r><v>bob<child>x</child></v></r>",
            id="ref",
        ),
    ],
)
def test_inline_simple_typed_element_with_child(schema: str, document: str) -> None:
    result = check(schema, document)
    assert [(error.type, error.message) for error in result.errors] == [
        ("structure", "simple-typed element must not contain child elements"),
    ]


def test_inline_simple_typed_element_keeps_comment_child() -> None:
    schema = f'<xs:schema {_XS}><xs:element name="v">{_INLINE_STRING}</xs:element></xs:schema>'
    assert xsd_ok(schema, "<v>bob<!--note--></v>")


def test_occurs_multi_digit_and_bounds() -> None:
    schema = (
        f'<xs:schema {_XS}><xs:element name="r"><xs:complexType><xs:sequence>'
        '<xs:element name="a" type="xs:string" minOccurs="2" maxOccurs="3"/>'
        "</xs:sequence></xs:complexType></xs:element></xs:schema>"
    )
    assert xsd_ok(schema, "<r><a>1</a><a>2</a></r>")
    assert not xsd_ok(schema, "<r><a>1</a></r>")
    assert not xsd_ok(schema, "<r><a>1</a><a>2</a><a>3</a><a>4</a></r>")


@pytest.mark.parametrize("method", [pytest.param("validate", id="report"), pytest.param("is_valid", id="verdict")])
def test_validate_rejects_non_node(*, method: str) -> None:
    validate = getattr(XMLSchema(f'<xs:schema {_XS}><xs:element name="v" type="xs:int"/></xs:schema>'), method)
    with pytest.raises(TypeError):
        validate("not a node")


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("café", id="two-byte"),
        pytest.param("中文", id="three-byte"),
        pytest.param("𝔸bc", id="four-byte"),  # ruff:ignore[ambiguous-unicode-character-string]  # sample text
    ],
)
def test_error_path_non_ascii_names(name: str) -> None:
    schema = f'<xs:schema {_XS}><xs:element name="{name}" type="xs:int"/></xs:schema>'
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
    schema = f"<xs:schema {_XS}>{globals_}</xs:schema>"
    assert xsd_ok(schema, document) is valid


def test_instance_comment_before_root() -> None:
    schema = f'<xs:schema {_XS}><xs:element name="r" type="xs:string"/></xs:schema>'
    assert xsd_ok(schema, "<!-- lead --><r>x</r>")


@pytest.mark.parametrize(
    ("type_name", "value", "ok"),
    [
        pytest.param("xs:NMTOKEN", "", False, id="nmtoken-empty"),
        pytest.param("xs:NCName", "1abc", False, id="ncname-bad-start"),
        pytest.param("xs:NCName", ":root", False, id="ncname-colon-start"),
        pytest.param("xs:Name", "a b", False, id="name-bad-middle"),
        pytest.param("xs:decimal", "12.50", True, id="decimal-both-parts"),
        pytest.param("xs:double", "1.5e3", True, id="double-frac-and-exp"),
        pytest.param("xs:time", "12:60:00", False, id="time-bad-minute"),
        pytest.param("xs:duration", "P", False, id="duration-nothing"),
        pytest.param("xs:language", "1", False, id="language-digit-start"),
        pytest.param("xs:language", "~", False, id="language-punctuation-start"),
        pytest.param("xs:hexBinary", "0", False, id="hex-single"),
        pytest.param("xs:base64Binary", "$$$$", False, id="base64-bad-char"),
    ],
)
def test_datatype_boundary_edges(type_name: str, value: str, ok: bool) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]  # a pytest parametrize value, not a boolean-trap call site
    schema = f'<xs:schema {_XS}><xs:element name="v" type="{type_name}"/></xs:schema>'
    assert xsd_ok(schema, f"<v>{value}</v>") is ok


@pytest.mark.parametrize("type_name", ["xs:Name", "xs:NCName", "xs:QName"])
@pytest.mark.parametrize(
    ("codepoint", "expected"),
    [
        pytest.param(0x80, False, id="below-first-range"),
        pytest.param(0xC0, True, id="first-range"),
        pytest.param(0x37E, False, id="greek-question-mark"),
        pytest.param(0x37F, True, id="after-greek-gap"),
        pytest.param(0xFDCF, True, id="before-noncharacter-gap"),
        pytest.param(0xFDD0, False, id="noncharacter-gap"),
        pytest.param(0xEFFFF, True, id="last-supplementary-name"),
        pytest.param(0xF0000, False, id="above-last-range"),
    ],
)
def test_datatype_name_start_ranges(*, type_name: str, codepoint: int, expected: bool) -> None:
    assert XMLSchema(typed(type_name)).is_valid(parse_xml(f"<v>{chr(codepoint)}</v>")) is expected


@pytest.mark.parametrize("type_name", ["xs:Name", "xs:NCName", "xs:NMTOKEN", "xs:QName"])
@pytest.mark.parametrize(
    ("codepoint", "expected"),
    [
        pytest.param(0xB7, True, id="middle-dot"),
        pytest.param(0x300, True, id="combining-mark"),
        pytest.param(0x203F, True, id="connector-punctuation"),
        pytest.param(0x37E, False, id="greek-question-mark"),
        pytest.param(0xF0000, False, id="above-last-range"),
    ],
)
def test_datatype_name_char_ranges(*, type_name: str, codepoint: int, expected: bool) -> None:
    assert XMLSchema(typed(type_name)).is_valid(parse_xml(f"<v>a{chr(codepoint)}</v>")) is expected


@pytest.mark.parametrize(
    ("type_name", "value"),
    [
        pytest.param("xs:int", "café", id="int-two-byte-value"),
        pytest.param("xs:int", "中文", id="int-three-byte-value"),
        pytest.param("xs:int", "𝔸", id="int-four-byte-value"),  # ruff:ignore[ambiguous-unicode-character-string]  # non-Latin sample text is the input under test
    ],
)
def test_datatype_error_message_non_ascii_value(type_name: str, value: str) -> None:
    schema = f'<xs:schema {_XS}><xs:element name="v" type="{type_name}"/></xs:schema>'
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
    schema = f'<xs:schema {_XS}><xs:element name="v" type="{type_name}"/></xs:schema>'
    assert xsd_ok(schema, f"<v>{value}</v>") is ok


@pytest.mark.parametrize(
    ("type_name", "value"),
    [
        pytest.param("xs:date", "2020-06-15Q", id="date-bad-tz-char"),
        pytest.param("xs:date", "202-06-15", id="date-short-year"),
        pytest.param("xs:duration", "1Y", id="duration-no-p"),
        pytest.param("xs:duration", "PT.5S", id="duration-fraction-no-integer-digit"),
        pytest.param("xs:duration", "PT1.S", id="duration-fraction-no-fraction-digit"),
        pytest.param("xs:duration", "PT1.5", id="duration-fraction-no-unit"),
        pytest.param("xs:duration", "PT1,5S", id="duration-fraction-comma"),
        pytest.param("xs:duration", "PT1.5.5S", id="duration-fraction-two-points"),
        pytest.param("xs:duration", "P1.5S", id="duration-fraction-seconds-outside-t"),
        pytest.param("xs:duration", "P1.5Y", id="duration-fraction-on-year"),
        pytest.param("xs:duration", "P1.5D", id="duration-fraction-on-day"),
        pytest.param("xs:duration", "PT1.5M", id="duration-fraction-on-minute"),
        pytest.param("xs:duration", "PT1.5H", id="duration-fraction-on-hour"),
        pytest.param("xs:duration", "P1", id="duration-no-unit"),
        pytest.param("xs:language", "en-verylongsub", id="language-long-subtag"),
    ],
)
def test_datatype_more_invalid(type_name: str, value: str) -> None:
    schema = f'<xs:schema {_XS}><xs:element name="v" type="{type_name}"/></xs:schema>'
    assert not xsd_ok(schema, f"<v>{value}</v>")


def test_xsd_empty_group_and_annotation() -> None:
    schema = (
        f"<xs:schema {_XS}>"
        "<xs:annotation><xs:documentation>docs</xs:documentation></xs:annotation>"
        '<xs:group name="g"></xs:group>'
        '<xs:complexType name="t"><xs:group ref="g"/></xs:complexType>'
        '<xs:element name="r" type="t"/></xs:schema>'
    )
    assert not xsd_ok(schema, "<r><x/></r>")


def test_xsd_empty_complex_content() -> None:
    schema = (
        f'<xs:schema {_XS}><xs:element name="r"><xs:complexType>'
        "<xs:complexContent></xs:complexContent></xs:complexType></xs:element></xs:schema>"
    )
    assert xsd_ok(schema, "<r/>")


def test_xsd_choice_and_all_with_whitespace() -> None:
    choice = (
        f'<xs:schema {_XS}><xs:element name="r"><xs:complexType><xs:choice>\n'
        '  <xs:element name="a" type="xs:string"/>\n'
        '  <xs:element name="b" type="xs:string"/>\n'
        "</xs:choice></xs:complexType></xs:element></xs:schema>"
    )
    assert xsd_ok(choice, "<r><a>x</a></r>")
    grouped = (
        f'<xs:schema {_XS}><xs:element name="r"><xs:complexType><xs:all>\n'
        '  <xs:element name="x" type="xs:string"/>\n'
        "</xs:all></xs:complexType></xs:element></xs:schema>"
    )
    assert xsd_ok(grouped, "<r><x>1</x></r>")


def test_xsd_restriction_with_whitespace_facets() -> None:
    schema = (
        f'<xs:schema {_XS}><xs:element name="v"><xs:simpleType>\n'
        '  <xs:restriction base="xs:string">\n'
        '    <xs:minLength value="2"/>\n'
        "  </xs:restriction>\n"
        "</xs:simpleType></xs:element></xs:schema>"
    )
    assert not xsd_ok(schema, "<v>a</v>")


def test_xsd_nullable_repeated_particle() -> None:
    schema = (
        f'<xs:schema {_XS}><xs:element name="r"><xs:complexType>'
        '<xs:sequence minOccurs="0" maxOccurs="unbounded">'
        '<xs:element name="a" type="xs:string" minOccurs="0"/>'
        "</xs:sequence></xs:complexType></xs:element></xs:schema>"
    )
    assert xsd_ok(schema, "<r><a>1</a></r>")
    assert xsd_ok(schema, "<r/>")


def test_language_bad_separator() -> None:
    schema = f'<xs:schema {_XS}><xs:element name="v" type="xs:language"/></xs:schema>'
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
    schema = f'<xs:schema {_XS}><xs:element name="v" type="{type_name}"/></xs:schema>'
    assert xsd_ok(schema, f"<v>{value}</v>") is ok


def test_ncname_char_past_z() -> None:
    schema = f'<xs:schema {_XS}><xs:element name="v" type="xs:NCName"/></xs:schema>'
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
    schema = f'<xs:schema {_XS}><xs:element name="v" type="{type_name}"/></xs:schema>'
    assert xsd_ok(schema, f"<v>{value}</v>") is ok


@pytest.mark.parametrize(
    ("pattern", "value", "ok"),
    [
        pytest.param("[a-]", "-", True, id="dash-before-close"),
        pytest.param(r"a\\", "a", False, id="backslash-at-end"),
        pytest.param("a{2}", "aa", True, id="bound-exact-close"),
        pytest.param("a{2,3}", "aaa", True, id="bound-comma-upper"),
        pytest.param("a{2,}", "aaaa", True, id="bound-comma-open"),
        pytest.param(r"[\d]+", "12", True, id="class-digit-builtin"),
        pytest.param(r"[\w]+", "ab$1", True, id="class-word-builtin"),
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
    schema = f'<xs:schema {_XS}><xs:element name="v" type="{type_name}"/></xs:schema>'
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
    schema = f'<xs:schema {_XS}><xs:element name="v" type="{type_name}"/></xs:schema>'
    assert xsd_ok(schema, f"<v>{value}</v>")


@pytest.mark.parametrize(
    ("pattern", "value", "ok"),
    [
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
    schema = f'<xs:schema {_XS}><xs:element name="v" type="xs:int"/></xs:schema>'
    assert xsd_ok(schema, "<v><![CDATA[42]]></v>")  # CDATA character-data child
    assert xsd_ok(schema, "<v>&#xD;42&#xA;</v>")  # carriage return + newline whitespace around the value


def test_non_xml_three_char_prefix() -> None:
    schema = (
        f'<xs:schema {_XS} targetNamespace="urn:y" xmlns="urn:y" elementFormDefault="qualified">'
        '<xs:element name="r" type="xs:string"/></xs:schema>'
    )
    assert xsd_ok(schema, '<abc:r xmlns:abc="urn:y">x</abc:r>')
    assert not xsd_ok(schema, '<abc:r xmlns:abc="urn:other">x</abc:r>')  # prefix bound to the wrong namespace


def test_large_text_value_arena_block() -> None:
    schema = f'<xs:schema {_XS}><xs:element name="v" type="xs:string"/></xs:schema>'
    assert xsd_ok(schema, f"<v>{'a' * 5000}</v>")  # forces a single arena allocation past the 4 kB block size


def test_complex_content_restriction() -> None:
    schema = (
        f"<xs:schema {_XS}>"
        '<xs:complexType name="base"><xs:sequence><xs:element name="a" type="xs:string"/></xs:sequence>'
        "</xs:complexType>"
        '<xs:element name="r"><xs:complexType><xs:complexContent>'
        '<xs:restriction base="base"><xs:sequence><xs:element name="a" type="xs:string"/></xs:sequence>'
        "</xs:restriction></xs:complexContent></xs:complexType></xs:element></xs:schema>"
    )
    assert xsd_ok(schema, "<r><a>x</a></r>")


def test_attribute_form_default_qualified() -> None:
    schema = (
        f'<xs:schema {_XS} attributeFormDefault="qualified"><xs:element name="r"><xs:complexType>'
        '<xs:attribute name="id" type="xs:string"/></xs:complexType></xs:element></xs:schema>'
    )
    assert xsd_ok(schema, '<r id="1"/>')


def test_global_notation_is_ignored() -> None:
    schema = (
        f"<xs:schema {_XS}>"
        '<xs:notation name="jpeg" public="image/jpeg"/>'
        '<xs:element name="r" type="xs:string"/></xs:schema>'
    )
    assert xsd_ok(schema, "<r>x</r>")


def test_empty_attribute_value() -> None:
    schema = (
        f'<xs:schema {_XS}><xs:element name="r"><xs:complexType>'
        '<xs:attribute name="a" type="xs:string"/></xs:complexType></xs:element></xs:schema>'
    )
    assert xsd_ok(schema, '<r a=""/>')


def test_self_referential_simple_type_terminates() -> None:
    schema = (
        f"<xs:schema {_XS}>"
        '<xs:simpleType name="loop"><xs:restriction base="loop"><xs:minLength value="1"/></xs:restriction>'
        "</xs:simpleType>"
        '<xs:element name="v" type="loop"/></xs:schema>'
    )
    assert xsd_ok(schema, "<v>x</v>")


def test_no_namespace_facet_child() -> None:
    schema = (
        f'<xs:schema {_XS}><xs:element name="v"><xs:simpleType>'
        '<xs:restriction base="xs:string"><note>plain</note>'
        '<xs:minLength value="2"/></xs:restriction></xs:simpleType></xs:element></xs:schema>'
    )
    assert xsd_ok(schema, "<v>ab</v>")
    assert not xsd_ok(schema, "<v>a</v>")


def test_unbound_prefix_is_no_namespace() -> None:
    schema = (
        f'<xs:schema {_XS} targetNamespace="urn:y" xmlns="urn:y" elementFormDefault="qualified">'
        '<xs:element name="r" type="xs:string"/></xs:schema>'
    )
    # xyz is checked before abc in resolve_ns, so the prefix compare rejects xyz then matches abc
    assert xsd_ok(schema, '<abc:r xmlns:xyz="urn:z" xmlns:abc="urn:y">x</abc:r>')


def test_schema_with_foreign_no_namespace_child() -> None:
    schema = (
        f'<xs:schema {_XS}><xs:element name="r"><xs:complexType><appinfo>note</appinfo>'
        '<xs:sequence><xs:element name="a" type="xs:string"/></xs:sequence>'
        "</xs:complexType></xs:element></xs:schema>"
    )
    assert xsd_ok(schema, "<r><a>x</a></r>")


def test_schema_with_relaxng_element_in_appinfo() -> None:
    schema = (
        f'<xs:schema {_XS}><xs:element name="r" type="xs:string"><xs:annotation><xs:appinfo>'
        '<rng:element xmlns:rng="http://relaxng.org/ns/structure/1.0" name="r"/>'
        "</xs:appinfo></xs:annotation></xs:element></xs:schema>"
    )
    assert xsd_ok(schema, "<r>x</r>")


def test_complex_content_mixed() -> None:
    schema = (
        f"<xs:schema {_XS}>"
        '<xs:complexType name="base"><xs:sequence><xs:element name="a" type="xs:string" minOccurs="0"/>'
        "</xs:sequence></xs:complexType>"
        '<xs:element name="r"><xs:complexType><xs:complexContent mixed="true">'
        '<xs:extension base="base"/></xs:complexContent></xs:complexType></xs:element></xs:schema>"'.rstrip('"')
    )
    assert xsd_ok(schema, "<r>text <a>x</a> more</r>")


def test_simple_content_restriction_no_base() -> None:
    schema = (
        f'<xs:schema {_XS}><xs:element name="r"><xs:complexType><xs:simpleContent>'
        '<xs:extension base="xs:string"/></xs:simpleContent></xs:complexType></xs:element></xs:schema>'
    )
    assert xsd_ok(schema, "<r>anything</r>")


def test_long_numeric_value_for_digit_facet() -> None:
    schema = (
        f'<xs:schema {_XS}><xs:element name="v"><xs:simpleType>'
        '<xs:restriction base="xs:decimal"><xs:totalDigits value="5"/></xs:restriction>'
        "</xs:simpleType></xs:element></xs:schema>"
    )
    assert not xsd_ok(schema, f"<v>{'1' * 70}</v>")


@pytest.mark.parametrize(
    ("type_name", "value", "ok"),
    [
        pytest.param("xs:hexBinary", "AF", True, id="hex-upper-af"),
        pytest.param("xs:hexBinary", "FG", False, id="hex-g-past-f"),
        pytest.param("xs:language", "en-a1b2", True, id="language-alnum-subtag"),
    ],
)
def test_more_datatype_ranges(type_name: str, value: str, ok: bool) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]  # parametrize value
    schema = f'<xs:schema {_XS}><xs:element name="v" type="{type_name}"/></xs:schema>'
    assert xsd_ok(schema, f"<v>{value}</v>") is ok


def test_regex_dot_excludes_carriage_return() -> None:
    schema = edge_restricted('<xs:pattern value="."/>')
    assert not xsd_ok(schema, "<v>&#xD;</v>")  # . matches any char except newline and carriage return


def test_form_defaults_unqualified_and_mixed_false() -> None:
    schema = (
        f'<xs:schema {_XS} elementFormDefault="unqualified" attributeFormDefault="unqualified">'
        '<xs:element name="r"><xs:complexType mixed="false"><xs:sequence>'
        '<xs:element name="a" type="xs:string"/></xs:sequence>'
        '<xs:attribute name="k" type="xs:string"/></xs:complexType></xs:element></xs:schema>'
    )
    assert xsd_ok(schema, '<r k="v"><a>x</a></r>')
    assert not xsd_ok(schema, "<r>text<a>x</a></r>")  # not mixed, so stray text is invalid


def test_base_and_element_type_in_target_namespace_prefix() -> None:
    schema = (
        f'<xs:schema {_XS} targetNamespace="urn:t" xmlns:t="urn:t" xmlns="urn:t" elementFormDefault="qualified">'
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
        f'<xs:schema {_XS}><xs:element name="r"><xs:complexType>'
        '<xs:attribute name="xmlnsish" type="xs:string"/></xs:complexType></xs:element></xs:schema>'
    )
    assert xsd_ok(schema, '<r xmlnsish="v"/>')


def test_group_with_annotation_before_model() -> None:
    schema = (
        f"<xs:schema {_XS}>"
        '<xs:group name="g"><xs:annotation><xs:documentation>doc</xs:documentation></xs:annotation>'
        '<xs:sequence><xs:element name="a" type="xs:string"/></xs:sequence></xs:group>'
        '<xs:complexType name="t"><xs:group ref="g"/></xs:complexType>'
        '<xs:element name="r" type="t"/></xs:schema>'
    )
    assert xsd_ok(schema, "<r><a>x</a></r>")


@pytest.mark.parametrize(
    ("pattern", "value", "ok"),
    [
        pytest.param("[\\s]", "x", False, id="class-space-vs-nonspace"),
    ],
)
def test_regex_parser_edge_boundaries(pattern: str, value: str, ok: bool) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]  # parametrize value
    assert pattern_ok(pattern, value) is ok


def test_schema_with_unnamespaced_sequence_is_not_recognized() -> None:
    # a <sequence> with no namespace is not the XSD content model, so the type has empty content
    schema = (
        f"<xs:schema {_XS}>"
        '<xs:element name="r"><xs:complexType><sequence>'
        '<xs:element name="a" type="xs:string"/></sequence></xs:complexType></xs:element></xs:schema>'
    )
    assert xsd_ok(schema, "<r/>")
    assert not xsd_ok(schema, "<r><a>x</a></r>")


def test_all_with_unnamespaced_element_declares_nothing() -> None:
    schema = (
        f'<xs:schema {_XS}><xs:element name="r"><xs:complexType><xs:all>'
        '<element name="a"/></xs:all></xs:complexType></xs:element></xs:schema>'
    )
    assert xsd_ok(schema, "<r/>")
    assert not xsd_ok(schema, "<r><a/></r>")


def test_simple_typed_element_with_text_and_element_child() -> None:
    schema = f'<xs:schema {_XS}><xs:element name="v" type="xs:string"/></xs:schema>'
    assert not xsd_ok(schema, "<v>text<child/>more</v>")


def test_non_mixed_with_whitespace_element_and_comment() -> None:
    schema = (
        f'<xs:schema {_XS}><xs:element name="r"><xs:complexType><xs:sequence>'
        '<xs:element name="a" type="xs:string"/></xs:sequence></xs:complexType></xs:element></xs:schema>'
    )
    assert xsd_ok(schema, "<r>\n  <!-- c --><a>x</a>\n</r>")  # whitespace + comment between elements, not mixed


def test_restriction_without_base_or_nested_type() -> None:
    schema = (
        f'<xs:schema {_XS}><xs:element name="v"><xs:simpleType>'
        '<xs:restriction><xs:minLength value="2"/></xs:restriction>'
        "</xs:simpleType></xs:element></xs:schema>"
    )
    assert xsd_ok(schema, "<v>ab</v>")
    assert not xsd_ok(schema, "<v>a</v>")


def test_simple_content_extension_without_base() -> None:
    schema = (
        f'<xs:schema {_XS}><xs:element name="r"><xs:complexType><xs:simpleContent>'
        '<xs:extension><xs:attribute name="u" type="xs:string"/></xs:extension>'
        "</xs:simpleContent></xs:complexType></xs:element></xs:schema>"
    )
    assert xsd_ok(schema, '<r u="x">anything</r>')


def test_xsd_attribute_no_type() -> None:
    schema = (
        f'<xs:schema {_XS}><xs:element name="r"><xs:complexType>'
        '<xs:attribute name="a"/></xs:complexType></xs:element></xs:schema>'
    )
    assert xsd_ok(schema, '<r a="anything"/>')


def test_extension_without_base_attr() -> None:
    schema = (
        f'<xs:schema {_XS}><xs:element name="r"><xs:complexType><xs:complexContent>'
        '<xs:extension><xs:sequence><xs:element name="a" type="xs:string"/></xs:sequence></xs:extension>'
        "</xs:complexContent></xs:complexType></xs:element></xs:schema>"
    )
    assert xsd_ok(schema, "<r><a>x</a></r>")


def test_simple_content_without_derivation() -> None:
    schema = (
        f'<xs:schema {_XS}><xs:element name="r"><xs:complexType>'
        "<xs:simpleContent></xs:simpleContent></xs:complexType></xs:element></xs:schema>"
    )
    assert xsd_ok(schema, "<r>anything</r>")


def test_xsd_attribute_group_without_ref_is_skipped() -> None:
    schema = (
        f'<xs:schema {_XS}><xs:element name="r"><xs:complexType>'
        "<xs:attributeGroup/>"  # no ref: nothing to pull in
        '<xs:attribute name="a" type="xs:string"/></xs:complexType></xs:element></xs:schema>'
    )
    assert xsd_ok(schema, '<r a="x"/>')


@pytest.mark.parametrize(
    "model",
    [
        pytest.param("<xs:sequence><xs:group/></xs:sequence>", id="sequence"),
        pytest.param("<xs:group/>", id="content-model"),
        pytest.param('<xs:group name="g"/>', id="misplaced-definition"),
        pytest.param(
            '<xs:sequence><xs:group minOccurs="0"/><xs:element name="a" type="xs:int"/></xs:sequence>',
            id="optional",
        ),
        pytest.param(
            '<xs:choice><xs:group/><xs:element name="a" type="xs:int"/></xs:choice>',
            id="choice-sibling",
        ),
        pytest.param('<xs:sequence><xs:group name="h"/></xs:sequence>', id="named-reference"),
        pytest.param(
            '<xs:group><xs:sequence minOccurs="0"><xs:element name="a"/></xs:sequence></xs:group>',
            id="unnamed-group-with-occurs",
        ),
    ],
)
def test_xsd_group_reference_without_ref_raises(model: str) -> None:
    schema = (
        f'<xs:schema {_XS}><xs:group name="g"><xs:sequence><xs:element name="a"/></xs:sequence></xs:group>'
        f'<xs:element name="r"><xs:complexType>{model}</xs:complexType></xs:element></xs:schema>'
    )
    with pytest.raises(ValueError, match="a group in a content model must have a ref attribute"):
        XMLSchema(schema)


def test_xsd_group_definition_without_ref_still_compiles() -> None:
    schema = (
        f'<xs:schema {_XS}><xs:group name="g"><xs:sequence><xs:element name="a" type="xs:int"/>'
        "</xs:sequence></xs:group>"
        '<xs:element name="r"><xs:complexType><xs:group ref="g"/></xs:complexType></xs:element></xs:schema>'
    )
    assert xsd_ok(schema, "<r><a>1</a></r>")


@pytest.mark.parametrize("xml", ["<r><e/></r>", "<r><e><a/></e></r>"])
def test_xsd_group_ref_without_model_group_matches_nothing_beside_a_group_all(xml: str) -> None:
    # the group ref to an xs:all makes validation resolve every group ref content model, the empty one included
    schema = (
        f'<xs:schema {_XS}><xs:group name="members"><xs:all><xs:element name="a"/></xs:all></xs:group>'
        '<xs:group name="empty"><xs:annotation/></xs:group>'
        '<xs:element name="r"><xs:complexType><xs:sequence>'
        '<xs:element name="h" minOccurs="0"><xs:complexType><xs:group ref="members"/></xs:complexType></xs:element>'
        '<xs:element name="e"><xs:complexType><xs:group ref="empty"/></xs:complexType></xs:element>'
        "</xs:sequence></xs:complexType></xs:element></xs:schema>"
    )
    assert not xsd_ok(schema, xml)


@pytest.mark.parametrize(
    ("type_name", "value", "ok"),
    [
        pytest.param("xs:double", ".5", True, id="double-leading-dot"),
        pytest.param("xs:hexBinary", "::", False, id="hex-below-A"),
        pytest.param("xs:double", "1e5z", False, id="double-exp-trailing-nondigit"),
    ],
)
def test_datatype_final_boundaries(type_name: str, value: str, ok: bool) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]  # parametrize value
    schema = f'<xs:schema {_XS}><xs:element name="v" type="{type_name}"/></xs:schema>'
    assert xsd_ok(schema, f"<v>{value}</v>") is ok


@pytest.fixture
def facet_schema() -> Callable[[str], XMLSchema]:
    def make(declarations: str) -> XMLSchema:
        return XMLSchema(f'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">{declarations}</xs:schema>')

    return make


@pytest.mark.parametrize(
    "count", [pytest.param(1, id="single"), pytest.param(9, id="growth"), pytest.param(32, id="many-types")]
)
def test_compiled_facet_type_lookup(facet_schema: Callable[[str], XMLSchema], count: int) -> None:
    types: Final[str] = "".join(
        f'<xs:simpleType name="type{index}"><xs:restriction base="xs:int">'
        f'<xs:minInclusive value="{index}"/><xs:maxInclusive value="{index}"/>'
        "</xs:restriction></xs:simpleType>"
        for index in range(count)
    )
    elements: Final[str] = "".join(f'<xs:element name="value{index}" type="type{index}"/>' for index in range(count))
    schema: Final[XMLSchema] = facet_schema(
        f'{types}<xs:element name="root"><xs:complexType><xs:sequence>{elements}'
        "</xs:sequence></xs:complexType></xs:element>"
    )
    documents: Final[list[Document]] = [
        parse_xml(f"<root>{''.join(f'<value{index}>{index + offset}</value{index}>' for index in range(count))}</root>")
        for offset in (0, 1, 0)
    ]
    assert [schema.validate(document).valid for document in documents] == [True, False, True]


@pytest.mark.parametrize(
    "count",
    [pytest.param(40, id="before-cutoff"), pytest.param(41, id="last-builtin"), pytest.param(42, id="after-cutoff")],
)
@pytest.mark.parametrize(
    ("declaration", "markup"),
    [
        pytest.param('<xs:element name="value" type="type0"/>', "<value>abc</value>", id="element"),
        pytest.param(
            '<xs:element name="value"><xs:complexType><xs:attribute name="number" type="type0"/>'
            "</xs:complexType></xs:element>",
            '<value number="abc"/>',
            id="attribute",
        ),
    ],
)
def test_compiled_facet_inheritance_cutoff(
    facet_schema: Callable[[str], XMLSchema], count: int, declaration: str, markup: str
) -> None:
    bases: Final[list[str]] = [f"type{index + 1}" for index in range(count - 1)] + ["xs:int"]
    types: Final[str] = "".join(
        f'<xs:simpleType name="type{index}"><xs:restriction base="{base}"/></xs:simpleType>'
        for index, base in enumerate(bases)
    )
    schema: Final[XMLSchema] = facet_schema(types + declaration)
    assert schema.validate(parse_xml(markup)).valid is (count == 42)


def _validate_sequentially(
    validate: Callable[[Document], ValidationResult], documents: list[Document]
) -> list[ValidationResult]:
    return [validate(document) for document in documents]


def _validate_concurrently(
    validate: Callable[[Document], ValidationResult], documents: list[Document]
) -> list[ValidationResult]:
    with ThreadPoolExecutor(max_workers=4) as executor:
        return list(executor.map(validate, documents))


@pytest.mark.parametrize(
    "run",
    [pytest.param(_validate_sequentially, id="sequential"), pytest.param(_validate_concurrently, id="concurrent")],
)
def test_compiled_facet_enumeration_reuse(
    facet_schema: Callable[[str], XMLSchema],
    run: Callable[[Callable[[Document], ValidationResult], list[Document]], list[ValidationResult]],
) -> None:
    schema: Final[XMLSchema] = facet_schema(
        '<xs:simpleType name="base"><xs:restriction base="xs:token">'
        '<xs:enumeration value="abc"/></xs:restriction></xs:simpleType>'
        '<xs:simpleType name="derived"><xs:restriction base="base">'
        '<xs:enumeration value="def"/><xs:minLength value="3"/></xs:restriction></xs:simpleType>'
        '<xs:element name="value" type="derived"/>'
    )
    documents: Final[list[Document]] = [
        parse_xml(f"<value>{value}</value>") for value in (" abc ", "def", "ghi", "ab") * 8
    ]
    assert [result.valid for result in run(schema.validate, documents)] == [True, True, False, False] * 8


def test_compiled_facet_cyclic_inheritance(facet_schema: Callable[[str], XMLSchema]) -> None:
    schema: Final[XMLSchema] = facet_schema(
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


_PCT: Final[str] = (
    '<xs:simpleType name="percent"><xs:restriction base="xs:int">'
    '<xs:minInclusive value="0"/><xs:maxInclusive value="90"/></xs:restriction></xs:simpleType>'
)
_INT_LIST: Final[str] = '<xs:simpleType name="ints"><xs:list itemType="xs:int"/></xs:simpleType>'
_INT_OR_WORD: Final[str] = '<xs:simpleType name="intOrWord"><xs:union memberTypes="xs:int xs:string"/></xs:simpleType>'
_INLINE_PERCENT: Final[str] = (
    '<xs:simpleType><xs:restriction base="xs:int"><xs:maxInclusive value="100"/></xs:restriction></xs:simpleType>'
)
_AUTO: Final[str] = (
    '<xs:simpleType><xs:restriction base="xs:string"><xs:enumeration value="auto"/></xs:restriction></xs:simpleType>'
)
_SIX_MEMBERS: Final[str] = '<xs:union memberTypes="xs:int xs:boolean xs:date xs:time xs:duration xs:hexBinary"/>'


@pytest.mark.parametrize(
    ("derivation", "declarations", "value", "expected"),
    [
        pytest.param("<xs:annotation/>", "", "any text", True, id="no-derivation-stays-a-string"),
        pytest.param('<xs:list itemType="percent"/>', _PCT, "50", True, id="list-item"),
        pytest.param('<xs:list itemType="percent"/>', _PCT, " 1\n2\t3  ", True, id="list-collapses"),
        pytest.param('<xs:list itemType="percent"/>', _PCT, "", True, id="list-empty"),
        pytest.param('<xs:list itemType="percent"/>', _PCT, "95", False, id="list-item-out-of-range"),
        pytest.param('<xs:list itemType="percent"/>', _PCT, "1 x 3", False, id="list-item-not-a-number"),
        pytest.param('<xs:list itemType="percent"/>', _PCT, "1 <b/>", False, id="list-markup"),
        pytest.param(f"<xs:list>{_INLINE_PERCENT}</xs:list>", "", "1 100", True, id="list-inline-item"),
        pytest.param(f"<xs:list>{_INLINE_PERCENT}</xs:list>", "", "1 101", False, id="list-inline-item-rejects"),
        pytest.param('<xs:union memberTypes="percent xs:boolean"/>', _PCT, "50", True, id="union-first"),
        pytest.param('<xs:union memberTypes="percent xs:boolean"/>', _PCT, " true ", True, id="union-second"),
        pytest.param('<xs:union memberTypes="percent xs:boolean"/>', _PCT, "95", False, id="union-no-member"),
        pytest.param('<xs:union memberTypes="percent xs:boolean"/>', _PCT, "<b/>", False, id="union-markup"),
        pytest.param(f'<xs:union memberTypes="xs:int">{_AUTO}</xs:union>', "", "auto", True, id="union-inline"),
        pytest.param(f'<xs:union memberTypes="xs:int">{_AUTO}</xs:union>', "", " auto", False, id="union-inline-ws"),
        pytest.param(f"<xs:union>{_AUTO}{_INLINE_PERCENT}</xs:union>", "", "7", True, id="union-inline-only"),
        pytest.param(f"<xs:union>{_AUTO}{_INLINE_PERCENT}</xs:union>", "", "700", False, id="union-inline-rejects"),
        pytest.param(_SIX_MEMBERS, "", "0aF1", True, id="union-sixth-member"),
        pytest.param(_SIX_MEMBERS, "", "0aF", False, id="union-six-members-reject"),
        pytest.param('<xs:union memberTypes="ints xs:boolean"/>', _INT_LIST, "1 2", True, id="union-of-list"),
        pytest.param('<xs:union memberTypes="ints xs:boolean"/>', _INT_LIST, "1 no", False, id="union-of-list-rejects"),
        pytest.param('<xs:list itemType="intOrBool"/>', "", "1 true 2", True, id="list-of-union"),
        pytest.param('<xs:list itemType="intOrBool"/>', "", "1 maybe", False, id="list-of-union-rejects"),
        pytest.param('<xs:union memberTypes="intOrBool xs:date"/>', "", "2020-01-01", True, id="union-of-union"),
        pytest.param('<xs:union memberTypes="intOrBool xs:date"/>', "", "2020", True, id="union-of-union-inner"),
        pytest.param('<xs:union memberTypes="intOrBool xs:date"/>', "", "2020-13", False, id="union-of-union-rejects"),
        pytest.param(
            '<xs:restriction base="ints"><xs:maxLength value="3"/></xs:restriction>',
            _INT_LIST,
            "3 4 5",
            True,
            id="list-max-length-counts-items",
        ),
        pytest.param(
            '<xs:restriction base="ints"><xs:maxLength value="3"/></xs:restriction>',
            _INT_LIST,
            "1 2 3 4",
            False,
            id="list-over-max-length",
        ),
        pytest.param(
            '<xs:restriction base="ints"><xs:maxLength value="3"/></xs:restriction>',
            _INT_LIST,
            "word",
            False,
            id="restricted-list-item-rejects",
        ),
        pytest.param(
            '<xs:restriction base="ints"><xs:minLength value="1"/></xs:restriction>',
            _INT_LIST,
            " ",
            False,
            id="list-under-min-length",
        ),
        pytest.param(
            '<xs:restriction base="ints"><xs:length value="2"/></xs:restriction>',
            _INT_LIST,
            "10000 20000",
            True,
            id="list-length",
        ),
        pytest.param(
            '<xs:restriction base="ints"><xs:length value="2"/></xs:restriction>',
            _INT_LIST,
            "12",
            False,
            id="list-length-rejects",
        ),
        pytest.param(
            '<xs:restriction><xs:simpleType><xs:list itemType="xs:int"/></xs:simpleType>'
            '<xs:maxLength value="3"/></xs:restriction>',
            "",
            "1000 2000 3000",
            True,
            id="inline-list-max-length-counts-items",
        ),
        pytest.param(
            '<xs:restriction base="ints"><xs:enumeration value="3 4"/><xs:pattern value="[0-9 ]+"/></xs:restriction>',
            _INT_LIST,
            " 3   4 ",
            True,
            id="list-enumeration-on-collapsed-value",
        ),
        pytest.param(
            '<xs:restriction base="ints"><xs:enumeration value="3 4"/></xs:restriction>',
            _INT_LIST,
            "4 3",
            False,
            id="list-enumeration-rejects",
        ),
        pytest.param(
            '<xs:restriction base="intOrWord"><xs:enumeration value="5"/><xs:enumeration value="a"/></xs:restriction>',
            _INT_OR_WORD,
            " 5 ",
            True,
            id="union-enumeration-as-the-accepting-member-normalizes",
        ),
        pytest.param(
            '<xs:restriction base="intOrWord"><xs:enumeration value="5"/><xs:enumeration value="a"/></xs:restriction>',
            _INT_OR_WORD,
            " a",
            False,
            id="union-enumeration-keeps-string-whitespace",
        ),
        pytest.param(
            '<xs:restriction base="intOrWord"><xs:pattern value="[a-z0-9]"/></xs:restriction>',
            _INT_OR_WORD,
            "55",
            False,
            id="union-pattern-rejects",
        ),
    ],
)
def test_list_and_union_types(derivation: str, declarations: str, value: str, *, expected: bool) -> None:
    int_or_bool: Final[str] = (
        '<xs:simpleType name="intOrBool"><xs:union memberTypes="xs:int xs:boolean"/></xs:simpleType>'
    )
    schema: Final[XMLSchema] = _variety(derivation, declarations + int_or_bool)
    body: Final[str] = value.replace("<", "&lt;")
    assert schema.is_valid(parse_xml(f"<v>{body}</v>")) is expected


def _variety(derivation: str, declarations: str = "") -> XMLSchema:
    return XMLSchema(
        f"<xs:schema {_XS}>{declarations}"
        f'<xs:element name="v"><xs:simpleType>{derivation}</xs:simpleType></xs:element></xs:schema>'
    )


@pytest.mark.parametrize(
    ("base", "facet", "restated", "value"),
    [
        pytest.param("xs:string", '<xs:length value="4"/>', '<xs:length value="2"/>', "ab", id="length"),
        pytest.param("xs:string", '<xs:minLength value="3"/>', '<xs:minLength value="1"/>', "ab", id="min-length"),
        pytest.param("xs:string", '<xs:maxLength value="1"/>', '<xs:maxLength value="2"/>', "ab", id="max-length"),
        pytest.param("xs:int", '<xs:minInclusive value="8"/>', '<xs:minInclusive value="7"/>', "7", id="min-inclusive"),
        pytest.param("xs:int", '<xs:maxInclusive value="6"/>', '<xs:maxInclusive value="7"/>', "7", id="max-inclusive"),
        pytest.param("xs:int", '<xs:minExclusive value="7"/>', '<xs:minExclusive value="6"/>', "7", id="min-exclusive"),
        pytest.param("xs:int", '<xs:maxExclusive value="7"/>', '<xs:maxExclusive value="8"/>', "7", id="max-exclusive"),
        pytest.param(
            "xs:date",
            '<xs:minInclusive value="2024-05-02"/>',
            '<xs:minInclusive value="2024-05-01"/>',
            "2024-05-01",
            id="date-bound",
        ),
        pytest.param(
            "xs:decimal", '<xs:totalDigits value="1"/>', '<xs:totalDigits value="2"/>', "3.5", id="total-digits"
        ),
        pytest.param(
            "xs:decimal",
            '<xs:fractionDigits value="0"/>',
            '<xs:fractionDigits value="1"/>',
            "3.5",
            id="fraction-digits",
        ),
    ],
)
def test_compiled_facet_scalar_inheritance(
    facet_schema: Callable[[str], XMLSchema], base: str, facet: str, restated: str, value: str
) -> None:
    # a derived type keeps each facet its base states until it states that facet itself
    schema: Final[XMLSchema] = facet_schema(
        f'<xs:simpleType name="root"><xs:restriction base="{base}">{facet}</xs:restriction></xs:simpleType>'
        '<xs:simpleType name="middle"><xs:restriction base="root"/></xs:simpleType>'
        '<xs:simpleType name="kept"><xs:restriction base="middle"/></xs:simpleType>'
        f'<xs:simpleType name="restated"><xs:restriction base="middle">{restated}</xs:restriction></xs:simpleType>'
        '<xs:element name="kept" type="kept"/><xs:element name="restated" type="restated"/>'
    )
    assert [schema.validate(parse_xml(f"<{name}>{value}</{name}>")).valid for name in ("kept", "restated")] == [
        False,
        True,
    ]


def test_compiled_facets_of_every_chain_level_apply(facet_schema: Callable[[str], XMLSchema]) -> None:
    schema: Final[XMLSchema] = facet_schema(f'{_enumerated_chain(24)}<xs:element name="value" type="t23"/>')
    assert [
        [error.message for error in schema.validate(parse_xml(f"<value>{value}</value>")).errors]
        for value in ("v0", "v23", "v24", "w0")
    ] == [
        [],
        [],
        ["value is not one of the enumerated values"],
        ["value is not one of the enumerated values"] + ["value does not match the required pattern"] * 24,
    ]


def _enumerated_chain(count: int) -> str:
    # type t{n} restricts t{n - 1}, each adding one enumeration and one pattern
    return "".join(
        f'<xs:simpleType name="t{index}"><xs:restriction base="{f"t{index - 1}" if index else "xs:token"}">'
        f'<xs:enumeration value="v{index}"/><xs:pattern value="v[0-9]+"/></xs:restriction></xs:simpleType>'
        for index in range(count)
    )


@pytest.mark.parametrize(
    "derived",
    [
        pytest.param('<xs:simpleType name="d{index}"><xs:restriction base="base"/></xs:simpleType>', id="restriction"),
        pytest.param(
            '<xs:complexType name="d{index}"><xs:simpleContent><xs:extension base="base"/></xs:simpleContent>'
            "</xs:complexType>",
            id="simple-content-extension",
        ),
        pytest.param(
            '<xs:complexType name="d{index}"><xs:simpleContent><xs:restriction base="content">'
            '<xs:simpleType><xs:restriction base="base"/></xs:simpleType></xs:restriction></xs:simpleContent>'
            "</xs:complexType>",
            id="inline-type-beside-base",
        ),
    ],
)
def test_types_derived_from_one_base_compile_in_linear_steps(work_steps: WorkSteps, derived: str) -> None:
    # every derived type used to hold a copy of the base's facets
    def text(count: int) -> str:
        enumerations = "".join(f'<xs:enumeration value="code{index}"/>' for index in range(count))
        return (
            f'<xs:schema {_XS}><xs:simpleType name="base"><xs:restriction base="xs:token">{enumerations}'
            "</xs:restriction></xs:simpleType>"
            '<xs:complexType name="content"><xs:simpleContent><xs:extension base="base"/></xs:simpleContent>'
            f"</xs:complexType>{''.join(derived.format(index=index) for index in range(count))}"
            f'<xs:element name="last" type="d{count - 1}"/></xs:schema>'
        )

    work_steps.assert_linear(lambda count: partial(XMLSchema, text(count)), (150, 600))
    schema: Final[XMLSchema] = XMLSchema(text(150))
    assert [schema.validate(parse_xml(f"<last>{value}</last>")).valid for value in (" code149 ", "code150")] == [
        True,
        False,
    ]


def test_long_derivation_chain_validates_in_linear_steps(work_steps: WorkSteps) -> None:
    def build(count: int) -> Callable[[], ValidationResult]:
        schema = XMLSchema(
            f"<xs:schema {_XS}>{_enumerated_chain(count)}"
            f'<xs:element name="r"><xs:complexType><xs:sequence><xs:element name="value" type="t{count - 1}" '
            'maxOccurs="unbounded"/></xs:sequence></xs:complexType></xs:element></xs:schema>'
        )
        values = "".join(f"<value>v{count - 1 - index % 2}</value>" for index in range(count))
        return partial(schema.validate, parse_xml(f"<r>{values}</r>"))

    work_steps.assert_linear(build, (150, 600))
    assert build(150)().valid


@pytest.mark.parametrize(
    "derivation",
    [
        pytest.param('<xs:list itemType="xs:int"/>', id="list"),
        pytest.param('<xs:union memberTypes="xs:int xs:double"/>', id="union"),
    ],
)
def test_list_and_union_types_by_name(facet_schema: Callable[[str], XMLSchema], derivation: str) -> None:
    schema: Final[XMLSchema] = facet_schema(
        f'<xs:simpleType name="item">{derivation}</xs:simpleType>'
        '<xs:complexType name="holder"><xs:simpleContent><xs:extension base="item"/></xs:simpleContent>'
        '</xs:complexType><xs:element name="root"><xs:complexType><xs:sequence><xs:element name="value" type="item"/>'
        '<xs:element name="held" type="holder"/></xs:sequence><xs:attribute name="code" type="item"/>'
        "</xs:complexType></xs:element>"
    )
    assert [
        [error.path for error in schema.validate(parse_xml(document)).errors]
        for document in (
            '<root code="1"><value>2</value><held>3</held></root>',
            '<root code="x"><value>2</value><held>3</held></root>',
            '<root code="1"><value>x</value><held>3</held></root>',
            '<root code="1"><value>2</value><held>x</held></root>',
        )
    ] == [[], ["/root"], ["/root/value"], ["/root/held"]]


def test_union_reports_one_error_for_a_value_no_member_accepts() -> None:
    schema: Final[XMLSchema] = XMLSchema(
        f'<xs:schema {_XS}><xs:simpleType name="u"><xs:union memberTypes="xs:int xs:boolean xs:date"/></xs:simpleType>'
        '<xs:element name="r"><xs:complexType><xs:sequence>'
        '<xs:element name="a" type="u"/><xs:element name="b" type="xs:int"/><xs:element name="c" type="u"/>'
        "</xs:sequence></xs:complexType></xs:element></xs:schema>"
    )
    rejected: Final[ValidationResult] = schema.validate(parse_xml("<r><a>nope</a><b>x</b><c>true</c></r>"))
    assert [(error.path, error.message) for error in rejected.errors] == [
        ("/r/a", "value 'nope' is not valid for any member type of the union"),
        ("/r/b", "value 'x' is not a valid int"),
    ]
    accepted: Final[ValidationResult] = schema.validate(parse_xml("<r><a>2020-01-01</a><b>1</b><c>true</c></r>"))
    assert (accepted.valid, list(accepted.errors)) == (True, [])


def test_list_reports_each_invalid_item() -> None:
    result: Final[ValidationResult] = _variety('<xs:list itemType="xs:int"/>').validate(parse_xml("<v>1 x 2 y</v>"))
    assert [error.message for error in result.errors] == [f"value '{item}' is not a valid int" for item in "xy"]


def _union_chain(count: int, *, reverse: bool = False) -> str:
    types: Final[list[str]] = [
        f'<xs:simpleType name="u{index}"><xs:union memberTypes="u{index + 1}"/></xs:simpleType>'
        for index in range(count - 1)
    ]
    types.append(f'<xs:simpleType name="u{count - 1}"><xs:union memberTypes="xs:int"/></xs:simpleType>')
    return "".join(reversed(types) if reverse else types)


def _union_ladder(rungs: int) -> str:
    rungs_xml: Final[str] = "".join(
        f'<xs:simpleType name="u{rung}"><xs:union memberTypes="u{rung - 1} u{rung - 1}"/></xs:simpleType>'
        for rung in range(1, rungs + 1)
    )
    return f'<xs:simpleType name="u0"><xs:union memberTypes="xs:int xs:boolean"/></xs:simpleType>{rungs_xml}'


@pytest.mark.parametrize(
    ("declarations", "type_name"),
    [
        pytest.param(_union_chain(100), "u0", id="chain-of-100"),
        pytest.param(_union_chain(100, reverse=True), "u0", id="chain-of-100-measured-from-its-end"),
        pytest.param(_union_ladder(8), "u8", id="ladder-of-512"),
    ],
)
def test_union_within_the_compile_limits(
    facet_schema: Callable[[str], XMLSchema], declarations: str, type_name: str
) -> None:
    schema: Final[XMLSchema] = facet_schema(f'{declarations}<xs:element name="v" type="{type_name}"/>')
    assert [schema.is_valid(parse_xml(f"<v>{value}</v>")) for value in ("7", "x")] == [True, False]


_NESTED_TOO_DEEP: Final[str] = "simple type nests xs:list and xs:union definitions more than 100 deep"


@pytest.mark.parametrize(
    ("declarations", "message"),
    [
        pytest.param(
            '<xs:simpleType name="t"><xs:list itemType="nope"/></xs:simpleType>',
            "type 'nope' does not resolve to a declared type",
            id="unresolved-item-type",
        ),
        pytest.param(
            '<xs:simpleType name="t"><xs:union memberTypes="xs:int no:such"/></xs:simpleType>',
            "type 'no:such' does not resolve to a declared type",
            id="unresolved-member-type",
        ),
        pytest.param(
            '<xs:simpleType name="t"><xs:union memberTypes="xs:int xs:madeUp"/></xs:simpleType>',
            "type 'xs:madeUp' does not resolve to a declared type",
            id="unknown-built-in-member-type",
        ),
        pytest.param(
            '<xs:complexType name="c"/><xs:simpleType name="t"><xs:list itemType="c"/></xs:simpleType>',
            "type 'c' does not resolve to a declared type",
            id="complex-item-type",
        ),
        pytest.param(
            '<xs:simpleType name="t"><xs:list/></xs:simpleType>',
            "xs:list requires one item type: an itemType attribute or a simpleType child",
            id="list-without-item-type",
        ),
        pytest.param(
            '<xs:simpleType name="t"><xs:list itemType="xs:int xs:date"/></xs:simpleType>',
            "xs:list requires one item type",
            id="list-with-two-item-types",
        ),
        pytest.param(
            f'<xs:simpleType name="t"><xs:list itemType="xs:int">{_AUTO}</xs:list></xs:simpleType>',
            "xs:list requires one item type",
            id="list-with-named-and-inline-item-type",
        ),
        pytest.param(
            '<xs:simpleType name="t"><xs:union memberTypes=" "><xs:annotation/></xs:union></xs:simpleType>',
            "xs:union requires a member type: a memberTypes attribute or a simpleType child",
            id="union-without-member-type",
        ),
        pytest.param(
            '<xs:simpleType name="t"><xs:union memberTypes="xs:int t"/></xs:simpleType>',
            "circular simple type definition through xs:list or xs:union",
            id="union-of-itself",
        ),
        pytest.param(
            '<xs:simpleType name="t"><xs:list itemType="r"/></xs:simpleType>'
            '<xs:simpleType name="r"><xs:restriction base="t"/></xs:simpleType>',
            "circular simple type definition through xs:list or xs:union",
            id="list-of-its-own-restriction",
        ),
        pytest.param(_union_chain(101), _NESTED_TOO_DEEP, id="chain-of-101"),
        pytest.param(_union_chain(101, reverse=True), _NESTED_TOO_DEEP, id="chain-of-101-measured-from-its-end"),
        pytest.param(
            _union_ladder(9),
            "simple type tries a value against more than 1000 union member types",
            id="ladder-of-1024",
        ),
    ],
)
def test_list_and_union_rejected_at_compile(
    facet_schema: Callable[[str], XMLSchema], declarations: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        facet_schema(declarations)


_PAIR: Final[str] = (
    '<xs:simpleType name="pair"><xs:restriction base="xs:NMTOKENS"><xs:maxLength value="2"/>'
    "</xs:restriction></xs:simpleType>"
)
_INT_OR_NMTOKENS: Final[str] = (
    '<xs:simpleType name="tokenOrInt"><xs:union memberTypes="xs:int xs:NMTOKENS"/></xs:simpleType>'
)
_PRESERVED_NMTOKENS: Final[str] = (
    '<xs:simpleType name="kept"><xs:restriction base="xs:NMTOKENS"><xs:whiteSpace value="preserve"/>'
    "</xs:restriction></xs:simpleType>"
)


@pytest.mark.parametrize(
    ("declarations", "type_name", "value", "expected"),
    [
        pytest.param("", "xs:NMTOKENS", "x-1 y.2", True, id="nmtokens"),
        pytest.param("", "xs:NMTOKENS", "  x\n 1  ", True, id="nmtokens-collapses"),
        pytest.param("", "xs:NMTOKENS", "", False, id="nmtokens-needs-an-item"),
        pytest.param("", "xs:NMTOKENS", "x 1!", False, id="nmtokens-item-rejects"),
        pytest.param("", "xs:IDREFS", "a b", True, id="idrefs"),
        pytest.param("", "xs:IDREFS", "a 1b", False, id="idrefs-item-is-an-ncname"),
        pytest.param("", "xs:ENTITIES", "a:b", False, id="entities-item-is-an-ncname"),
        pytest.param("", "xs:ENTITIES", " ", False, id="entities-needs-an-item"),
        pytest.param(_PAIR, "pair", "a b", True, id="restricted-counts-items"),
        pytest.param(_PAIR, "pair", "a b c", False, id="restricted-over-max-items"),
        pytest.param(_INT_OR_NMTOKENS, "tokenOrInt", "7", True, id="union-member-int"),
        pytest.param(_INT_OR_NMTOKENS, "tokenOrInt", "a b", True, id="union-member-nmtokens"),
        pytest.param(_INT_OR_NMTOKENS, "tokenOrInt", "", False, id="union-member-nmtokens-needs-an-item"),
        pytest.param(_PRESERVED_NMTOKENS, "kept", " a  b ", True, id="restricted-list-stays-collapsed"),
    ],
)
def test_builtin_list_types(declarations: str, type_name: str, value: str, *, expected: bool) -> None:
    schema: Final[XMLSchema] = XMLSchema(
        f'<xs:schema {_XS}>{declarations}<xs:element name="v" type="{type_name}"/></xs:schema>'
    )
    assert schema.is_valid(parse_xml(f"<v>{value}</v>")) is expected


@pytest.mark.parametrize(
    "count",
    [
        pytest.param(60, id="beyond-the-base-depth-cutoff"),
        pytest.param(150, id="long-chain"),
    ],
)
def test_restriction_chain_of_a_list_checks_items(facet_schema: Callable[[str], XMLSchema], count: int) -> None:
    links: Final[str] = "".join(
        f'<xs:simpleType name="r{index}"><xs:restriction base="r{index - 1}"/></xs:simpleType>'
        for index in range(1, count + 1)
    )
    schema: Final[XMLSchema] = facet_schema(
        f'<xs:simpleType name="r0"><xs:list itemType="xs:int"/></xs:simpleType>{links}'
        f'<xs:element name="v" type="r{count}"/>'
    )
    assert [schema.is_valid(parse_xml(f"<v>{value}</v>")) for value in ("1 2", "1 x")] == [True, False]


def test_compiled_facets_with_schema_comments(facet_schema: Callable[[str], XMLSchema]) -> None:
    schema: Final[XMLSchema] = facet_schema(
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
        restriction: Final[str] = f'<xs:restriction base="xs:token">{facets}</xs:restriction>'
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


@pytest.mark.parametrize(
    "form",
    [
        pytest.param(
            ("inline", '<xs:pattern value="abc[0-9]+"/><xs:pattern value="abc[0-9]+x"/>'),
            id="inline",
        ),
        pytest.param(
            ("inherited", '<xs:pattern value="[a-z]+[0-9]+"/><xs:pattern value="abc[0-9]+"/>'), id="inherited"
        ),
        pytest.param(
            ("rng", '<param name="pattern">[a-z]+[0-9]+</param><param name="pattern">abc[0-9]+</param>'),
            id="rng",
        ),
    ],
)
@pytest.mark.parametrize(
    "run",
    [pytest.param(_validate_sequentially, id="sequential"), pytest.param(_validate_concurrently, id="concurrent")],
)
def test_compiled_patterns_reuse(
    pattern_schema: Callable[[str, str], XMLSchema | RelaxNG],
    form: tuple[str, str],
    run: Callable[[Callable[[Document], ValidationResult], list[Document]], list[ValidationResult]],
) -> None:
    kind, facets = form
    schema: Final[XMLSchema | RelaxNG] = pattern_schema(kind, facets)
    documents: Final[list[Document]] = [
        parse_xml(f"<value>{value}</value>") for value in (" abc123 ", "abc", "def123", "abc0") * 8
    ]
    assert [result.valid for result in run(schema.validate, documents)] == [True, False, False, True] * 8


@pytest.mark.parametrize(
    ("form", "pattern", "values", "expected"),
    [
        pytest.param(("inline", '<xs:pattern value="{}"/>'), "", ("", "a", ""), [True, False, True], id="xsd-empty"),
        pytest.param(
            ("inline", '<xs:pattern value="{}"/>'),
            "(a?)*b",
            ("aaab", "aaa", "b"),
            [True, False, True],
            id="epsilon-revisit",
        ),
        pytest.param(
            ("rng", '<param name="pattern">{}</param>'), "", ("", "a", ""), [True, False, True], id="rng-empty"
        ),
        pytest.param(
            ("inline", '<xs:pattern value="{}"/>'),
            "(ab|cd)*",
            ("abcd", "abc", ""),
            [True, False, True],
            id="xsd-epsilon-cycle",
        ),
        pytest.param(
            ("rng", '<param name="pattern">{}</param>'),
            "(ab|cd)*",
            ("abcd", "abc", ""),
            [True, False, True],
            id="rng-epsilon-cycle",
        ),
        pytest.param(
            ("inline", '<xs:pattern value="{}"/>'),
            "é+[0-9]?",
            ("éé1", "ee1", "é"),
            [True, False, True],
            id="xsd-unicode",
        ),
        pytest.param(
            ("rng", '<param name="pattern">{}</param>'),
            "é+[0-9]?",
            ("éé1", "ee1", "é"),
            [True, False, True],
            id="rng-unicode",
        ),
    ],
)
def test_compiled_pattern_outputs(
    pattern_schema: Callable[[str, str], XMLSchema | RelaxNG],
    form: tuple[str, str],
    pattern: str,
    values: tuple[str, str, str],
    expected: list[bool],
) -> None:
    kind, template = form
    schema: Final[XMLSchema | RelaxNG] = pattern_schema(kind, template.format(pattern))
    assert [schema.validate(parse_xml(f"<value>{value}</value>")).valid for value in values] == expected


def test_compiled_pattern_diagnostic(pattern_schema: Callable[[str, str], XMLSchema | RelaxNG]) -> None:
    schema: Final[XMLSchema | RelaxNG] = pattern_schema("inline", '<xs:pattern value="[a-z]+"/>')
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
    schema: Final[XMLSchema | RelaxNG] = pattern_schema("inline", f'{extra}<xs:pattern value="[a-z]+"/>')
    assert [schema.validate(parse_xml(f"<value>{value}</value>")).valid for value in ("abc", "123")] == [True, False]


@pytest.mark.parametrize(
    "make_schema",
    [
        pytest.param(
            lambda pattern: XMLSchema(
                '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"><xs:element name="value"><xs:simpleType>'
                f'<xs:restriction base="xs:string"><xs:pattern value="{pattern}"/></xs:restriction>'
                "</xs:simpleType></xs:element></xs:schema>"
            ),
            id="xsd",
        ),
        pytest.param(
            lambda pattern: RelaxNG(
                '<element xmlns="http://relaxng.org/ns/structure/1.0" name="value" '
                'datatypeLibrary="http://www.w3.org/2001/XMLSchema-datatypes"><data type="string">'
                f'<param name="pattern">{pattern}</param></data></element>'
            ),
            id="relax-ng",
        ),
    ],
)
@pytest.mark.parametrize(
    ("prefix", "codepoint", "valid"),
    [
        pytest.param("", 0x400, True, id="positive-first"),
        pytest.param("", 0x47F, True, id="positive-last"),
        pytest.param("", 0x480, False, id="positive-outside"),
        pytest.param("^", 0x400, False, id="negated-first"),
        pytest.param("^", 0x47F, False, id="negated-last"),
        pytest.param("^", 0x480, True, id="negated-outside"),
    ],
)
def test_growing_pattern_class(
    make_schema: Callable[[str], XMLSchema | RelaxNG], *, prefix: str, codepoint: int, valid: bool
) -> None:
    pattern: Final[str] = f"[{prefix}{''.join(chr(index) for index in range(0x400, 0x480))}]"
    schema: Final[XMLSchema | RelaxNG] = make_schema(pattern)
    assert schema.validate(parse_xml(f"<value>{chr(codepoint)}</value>")).valid is valid


@pytest.mark.parametrize(
    ("index", "expected"),
    [
        pytest.param(0, [True, False, False], id="derived-type"),
        pytest.param(1, [True, True, False], id="builtin-type"),
    ],
)
def test_facet_benchmark_output(index: int, expected: list[bool]) -> None:
    source, document = cast("tuple[str, str]", INPUTS["validate-facets"]()[index][1])
    schema: Final[XMLSchema] = XMLSchema(source)
    assert [
        schema.validate(parse_xml(text)).valid
        for text in (document, document.replace("abc123", "ab"), "<root><other/></root>")
    ] == expected


def test_facet_compile_benchmark_output() -> None:
    compile_schema: Final[Callable[[str], XMLSchema]] = cast(
        "Callable[[str], XMLSchema]", OPERATIONS["compile-facets"][0]
    )
    schema: Final[XMLSchema] = compile_schema(cast("str", INPUTS["compile-facets"]()[0][1]))
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
    schema: Final[XMLSchema] = XMLSchema(source)
    assert [
        schema.validate(parse_xml(text)).valid for text in (document, document.replace("abc123", "abc"))
    ] == expected


def test_pattern_compile_benchmark_output() -> None:
    _, _, load = next(benchmark for benchmark in benchmarks() if benchmark[0] == "compile-pattern")
    compile_schema: Final[Callable[[str], XMLSchema]] = cast(
        "Callable[[str], XMLSchema]", OPERATIONS["compile-pattern"][0]
    )
    schema: Final[XMLSchema] = compile_schema(cast("str", load()))
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
        pytest.param(
            "", 'xmlns:f="urn:foreign" f:unknown="x"', ["attribute 'f:unknown' is not declared"], id="foreign-attribute"
        ),
        pytest.param('<xs:attribute name="item0"/>', "", [], id="duplicate-declaration"),
    ],
)
def test_wide_attribute_declaration_order(declarations: str, attributes: str, expected: list[str]) -> None:
    schema: Final[XMLSchema] = XMLSchema(
        f"""<xs:schema {_XS}><xs:element name="root"><xs:complexType>"""
        f"""{"".join(f'<xs:attribute name="item{index}" type="xs:string"/>' for index in range(32))}{declarations}"""
        "</xs:complexType></xs:element></xs:schema>"
    )
    document: Final[Document] = parse_xml(
        f"""<root {" ".join(f'item{index}="text"' for index in reversed(range(32)))} {attributes}/>"""
    )
    assert [error.message for error in schema.validate(document).errors] == expected


def test_wide_attribute_declarations_with_sparse_instance() -> None:
    schema: Final[XMLSchema] = XMLSchema(
        f"""<xs:schema {_XS}><xs:element name="root"><xs:complexType>"""
        f"""{"".join(f'<xs:attribute name="item{index}" type="xs:string"/>' for index in range(32))}"""
        """<xs:attribute name="required" use="required"/></xs:complexType></xs:element></xs:schema>"""
    )
    assert [error.message for error in schema.validate(parse_xml('<root item0="value"/>')).errors] == [
        "required attribute 'required' is missing"
    ]


@pytest.mark.parametrize(
    "index",
    [
        pytest.param(0, id="unbounded"),
        pytest.param(1, id="bounded"),
        pytest.param(2, id="small"),
        pytest.param(3, id="string"),
    ],
)
def test_numeric_facet_benchmark_output(index: int) -> None:
    source, document = cast("tuple[str, str]", INPUTS["validate-numeric-facets"]()[index][1])
    assert XMLSchema(source).validate(parse_xml(document)).errors == ()


@pytest.mark.oracle
@pytest.mark.parametrize(
    ("schema", "doc"),
    [
        pytest.param(f'<xs:schema {_XS}><xs:element name="n" type="xs:int"/></xs:schema>', "<n>42</n>", id="int-valid"),
        pytest.param(
            f'<xs:schema {_XS}><xs:element name="n" type="xs:int"/></xs:schema>', "<n>x</n>", id="int-invalid"
        ),
        pytest.param(
            f'<xs:schema {_XS}><xs:element name="d" type="xs:date"/></xs:schema>', "<d>2020-06-15</d>", id="date-valid"
        ),
        pytest.param(
            f'<xs:schema {_XS}><xs:element name="d" type="xs:date"/></xs:schema>',
            "<d>2020-13-40</d>",
            id="date-invalid",
        ),
        pytest.param(
            f'<xs:schema {_XS}><xs:element name="r"><xs:complexType><xs:sequence>'
            '<xs:element name="a" type="xs:string"/><xs:element name="b" type="xs:int" minOccurs="0"/>'
            "</xs:sequence></xs:complexType></xs:element></xs:schema>",
            "<r><a>x</a><b>3</b></r>",
            id="sequence-valid",
        ),
        pytest.param(
            f'<xs:schema {_XS}><xs:element name="r"><xs:complexType><xs:sequence>'
            '<xs:element name="a" type="xs:string"/></xs:sequence></xs:complexType></xs:element></xs:schema>',
            "<r><a>x</a><extra/></r>",
            id="sequence-extra-invalid",
        ),
        pytest.param(
            f'<xs:schema {_XS}><xs:element name="r"><xs:complexType>'
            '<xs:attribute name="id" type="xs:int" use="required"/></xs:complexType></xs:element></xs:schema>',
            "<r/>",
            id="missing-attr-invalid",
        ),
        pytest.param(
            f'<xs:schema {_XS}><xs:element name="v"><xs:simpleType>'
            '<xs:restriction base="xs:string"><xs:enumeration value="a"/><xs:enumeration value="b"/>'
            "</xs:restriction></xs:simpleType></xs:element></xs:schema>",
            "<v>c</v>",
            id="enum-invalid",
        ),
        pytest.param(
            f'<xs:schema {_XS}><xs:element name="v"><xs:simpleType>'
            '<xs:restriction base="xs:int"><xs:minInclusive value="0"/><xs:maxInclusive value="10"/>'
            "</xs:restriction></xs:simpleType></xs:element></xs:schema>",
            "<v>11</v>",
            id="range-invalid",
        ),
        pytest.param(
            f'<xs:schema {_XS} targetNamespace="urn:x" xmlns="urn:x" elementFormDefault="qualified">'
            '<xs:element name="r"><xs:complexType><xs:sequence>'
            '<xs:element name="a" type="xs:string"/></xs:sequence></xs:complexType></xs:element></xs:schema>',
            '<r xmlns="urn:x"><a>x</a></r>',
            id="namespace-valid",
        ),
    ],
)
def test_xsd_matches_lxml(schema: str, doc: str) -> None:
    etree: Final[ModuleType] = pytest.importorskip("lxml.etree", exc_type=ImportError)
    validator: Final[lxml.etree.XMLSchema] = etree.XMLSchema(etree.fromstring(schema.encode()))
    assert XMLSchema(schema).validate(parse_xml(doc)).valid == validator.validate(etree.fromstring(doc.encode()))


@pytest.mark.oracle
@pytest.mark.parametrize(
    ("index", "expected"),
    [
        pytest.param(0, [True, False], id="derived-type"),
        pytest.param(1, [True, True], id="builtin-type"),
        pytest.param(2, [True, False], id="named-attributes"),
        pytest.param(3, [True, False], id="small-named-type"),
    ],
)
def test_lxml_facet_validate_benchmark_output(index: int, expected: list[bool]) -> None:
    module: Final[ModuleType] = pytest.importorskip("bench.competitors.lxml", exc_type=ImportError)
    etree: Final[ModuleType] = pytest.importorskip("lxml.etree", exc_type=ImportError)
    source, document = cast("tuple[str, str]", INPUTS["validate-facets"]()[index][1])
    schema: Final[lxml.etree.XMLSchema] = module.OPERATIONS["compile-facets"][0](source)
    assert [
        schema.validate(etree.fromstring(text.encode())) for text in (document, document.replace("abc123", "ab"))
    ] == expected


@pytest.mark.oracle
def test_lxml_facet_compile_benchmark_output() -> None:
    module: Final[ModuleType] = pytest.importorskip("bench.competitors.lxml", exc_type=ImportError)
    etree: Final[ModuleType] = pytest.importorskip("lxml.etree", exc_type=ImportError)
    schema: Final[lxml.etree.XMLSchema] = module.OPERATIONS["compile-facets"][0](
        cast("str", INPUTS["compile-facets"]()[0][1])
    )
    assert [
        schema.validate(etree.fromstring(text.encode()))
        for text in ("<root><value>abc123</value></root>", "<root><value>ab</value></root>")
    ] == [True, False]


@pytest.mark.oracle
@pytest.mark.parametrize(
    ("name", "expected"),
    [
        pytest.param("validate-pattern-reuse", [True, False], id="two-patterns"),
        pytest.param("validate-pattern-plain", [True, True], id="no-patterns"),
    ],
)
def test_lxml_pattern_benchmark_output(name: str, expected: list[bool]) -> None:
    module: Final[ModuleType] = pytest.importorskip("bench.competitors.lxml", exc_type=ImportError)
    etree: Final[ModuleType] = pytest.importorskip("lxml.etree", exc_type=ImportError)
    _, _, load = next(benchmark for benchmark in benchmarks() if benchmark[0] == name)
    source, document = cast("tuple[str, str]", load())
    schema: Final[lxml.etree.XMLSchema] = module.OPERATIONS["compile-pattern"][0](source)
    assert [
        schema.validate(etree.fromstring(text.encode())) for text in (document, document.replace("abc123", "abc"))
    ] == expected


@pytest.mark.oracle
def test_lxml_pattern_compile_benchmark_output() -> None:
    module: Final[ModuleType] = pytest.importorskip("bench.competitors.lxml", exc_type=ImportError)
    etree: Final[ModuleType] = pytest.importorskip("lxml.etree", exc_type=ImportError)
    _, _, load = next(benchmark for benchmark in benchmarks() if benchmark[0] == "compile-pattern")
    schema: Final[lxml.etree.XMLSchema] = module.OPERATIONS["compile-pattern"][0](cast("str", load()))
    assert [
        schema.validate(etree.fromstring(text.encode()))
        for text in ("<root><value>abc123</value></root>", "<root><value>abc</value></root>")
    ] == [True, False]


@pytest.mark.oracle
@pytest.mark.parametrize(
    "index",
    [
        pytest.param(0, id="wide-attributes"),
        pytest.param(1, id="small-attributes"),
        pytest.param(2, id="sparse"),
        pytest.param(3, id="few-declarations"),
    ],
)
def test_lxml_attribute_benchmark_output(index: int) -> None:
    etree: Final[ModuleType] = pytest.importorskip("lxml.etree", exc_type=ImportError)
    source, document = cast("tuple[str, str]", INPUTS["validate-attributes"]()[index][1])
    schema: Final[lxml.etree.XMLSchema] = etree.XMLSchema(etree.fromstring(source.encode()))
    assert [
        schema.validate(etree.fromstring(text.encode()))
        for text in (document, document.replace("<root ", '<root unknown="x" '))
    ] == [index != 3, False]


@pytest.mark.oracle
@pytest.mark.parametrize(
    "index",
    [
        pytest.param(0, id="unbounded"),
        pytest.param(1, id="bounded"),
        pytest.param(2, id="small"),
        pytest.param(3, id="string"),
    ],
)
def test_lxml_numeric_facet_benchmark_output(index: int) -> None:
    etree: Final[ModuleType] = pytest.importorskip("lxml.etree", exc_type=ImportError)
    source, document = cast("tuple[str, str]", INPUTS["validate-numeric-facets"]()[index][1])
    schema: Final[lxml.etree.XMLSchema] = etree.XMLSchema(etree.fromstring(source.encode()))
    assert [
        schema.validate(etree.fromstring(text.encode()))
        for text in (document, document.replace("</root>", "<unexpected/></root>"))
    ] == [True, False]


@pytest.mark.parametrize(
    ("index", "errors"),
    [
        pytest.param(0, 0, id="wide"),
        pytest.param(1, 0, id="small"),
        pytest.param(2, 0, id="sparse"),
        pytest.param(3, 511, id="few-declarations"),
    ],
)
def test_instance_attribute_index_benchmark_output(index: int, errors: int) -> None:
    source, document = cast("tuple[str, str]", INPUTS["validate-attributes"]()[index][1])
    assert len(XMLSchema(source).validate(parse_xml(document)).errors) == errors


@pytest.mark.parametrize(
    "index",
    [
        pytest.param(0, id="named-elements"),
        pytest.param(1, id="builtin"),
        pytest.param(2, id="named-attributes"),
        pytest.param(3, id="small"),
    ],
)
def test_named_facet_probe_benchmark_output(index: int) -> None:
    source, document = cast("tuple[str, str]", INPUTS["validate-facets"]()[index][1])
    schema: Final[XMLSchema] = XMLSchema(source)
    assert [schema.validate(parse_xml(value)).valid for value in (document, document.replace("abc123", "ab"))] == [
        True,
        index == 1,
    ]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(f"1.{'0' * 80}", True, id="long-fraction-in-bound"),
        pytest.param("9" * 80, False, id="long-integer-out-of-bound"),
    ],
)
def test_xsd_long_decimal_bound(value: str, *, expected: bool) -> None:
    assert check(restricted('<xs:maxInclusive value="2"/>', "xs:decimal"), f"<v>{value}</v>").valid is expected


@pytest.mark.parametrize(
    ("base", "value", "expected"),
    [
        pytest.param("xs:integer", f"{'0' * 64}250", False, id="zeros-then-digits-out-of-bound"),
        pytest.param("xs:integer", f"{'0' * 90}50", True, id="zeros-then-digits-in-bound"),
        pytest.param("xs:decimal", f"{'0' * 64}250.5", False, id="decimal-zeros-then-digits-out-of-bound"),
        pytest.param("xs:double", f"5.{'0' * 80}e30", False, id="long-fraction-then-exponent-out-of-bound"),
        pytest.param("xs:float", f"5.{'0' * 80}e30", False, id="float-long-fraction-then-exponent-out-of-bound"),
        pytest.param("xs:double", f"1{'0' * 70}e-70", True, id="long-integer-then-exponent-in-bound"),
    ],
)
def test_xsd_long_number_range(base: str, value: str, *, expected: bool) -> None:
    # the digits or exponent that decide the comparison sit past the 63rd character of the value
    assert check(restricted('<xs:maxInclusive value="100"/>', base), f"<v>{value}</v>").valid is expected


_TINY_DECIMAL: Final[str] = f"0.{'0' * 400}1"
_HUGE_INTEGER: Final[str] = f"1{'0' * 400}"


@pytest.mark.parametrize(
    ("base", "facets", "value", "ok"),
    [
        pytest.param(
            "xs:decimal", '<xs:minInclusive value="0"/>', f"-{_TINY_DECIMAL}", False, id="tiny-negative-min-inclusive"
        ),
        pytest.param(
            "xs:decimal", '<xs:maxInclusive value="0"/>', _TINY_DECIMAL, False, id="tiny-positive-max-inclusive"
        ),
        pytest.param(
            "xs:decimal", '<xs:minExclusive value="0"/>', _TINY_DECIMAL, True, id="tiny-positive-min-exclusive"
        ),
        pytest.param(
            "xs:decimal", '<xs:maxExclusive value="0"/>', f"-{_TINY_DECIMAL}", True, id="tiny-negative-max-exclusive"
        ),
        pytest.param(
            "xs:decimal",
            f'<xs:maxInclusive value="{"9" * 400}"/>',
            _HUGE_INTEGER,
            False,
            id="decimal-overflow-max-inclusive",
        ),
        pytest.param(
            "xs:decimal",
            f'<xs:minExclusive value="{"9" * 400}"/>',
            _HUGE_INTEGER,
            True,
            id="decimal-overflow-min-exclusive",
        ),
        pytest.param(
            "xs:integer",
            f'<xs:maxInclusive value="{"9" * 400}"/>',
            _HUGE_INTEGER,
            False,
            id="integer-overflow-max-inclusive",
        ),
        pytest.param(
            "xs:long",
            '<xs:maxInclusive value="9223372036854775806"/>',
            "9223372036854775807",
            False,
            id="long-one-past-max-inclusive",
        ),
        pytest.param(
            "xs:integer",
            '<xs:maxInclusive value="9007199254740992"/>',
            "9007199254740993",
            False,
            id="integer-past-double-max-inclusive",
        ),
        pytest.param(
            "xs:decimal",
            '<xs:maxInclusive value="0.1"/>',
            "0.10000000000000000001",
            False,
            id="decimal-past-double-max-inclusive",
        ),
        pytest.param(
            "xs:decimal",
            '<xs:maxInclusive value="0.1"/>',
            "0.09999999999999999999",
            True,
            id="decimal-below-double-max-inclusive",
        ),
        pytest.param("xs:decimal", '<xs:minInclusive value="0"/>', "-0.0", True, id="negative-zero-is-zero"),
        pytest.param("xs:decimal", '<xs:minInclusive value="0"/>', ".5", True, id="leading-point-above-zero"),
        pytest.param("xs:decimal", '<xs:maxInclusive value="5"/>', "5.", True, id="trailing-point-equal"),
        pytest.param("xs:integer", '<xs:maxInclusive value="9"/>', "10", False, id="integer-more-digits-above"),
        pytest.param("xs:integer", '<xs:minInclusive value="9"/>', "-10", False, id="integer-negative-below"),
        pytest.param("xs:integer", '<xs:minInclusive value="10"/>', "9", False, id="integer-fewer-digits-below"),
        pytest.param("xs:integer", '<xs:maxInclusive value="99"/>', "+5", True, id="integer-plus-sign-below"),
        pytest.param("xs:integer", '<xs:maxInclusive value="7"/>', "007", True, id="integer-leading-zeros-equal"),
        pytest.param("xs:decimal", '<xs:maxInclusive value="2"/>', "3", False, id="decimal-whole-digit-above"),
        pytest.param("xs:decimal", '<xs:maxInclusive value="2"/>', "1", True, id="decimal-whole-digit-below"),
        pytest.param("xs:decimal", '<xs:maxInclusive value="2"/>', "-5.5", True, id="negative-below-positive-bound"),
        pytest.param("xs:decimal", '<xs:maxInclusive value="-2"/>', "1", False, id="positive-above-negative-bound"),
        pytest.param("xs:decimal", '<xs:maxInclusive value="-2"/>', "-1.5", False, id="negative-magnitude-above"),
        pytest.param("xs:decimal", '<xs:minInclusive value="-2"/>', "-1.5", True, id="negative-magnitude-below"),
        pytest.param("xs:decimal", '<xs:maxInclusive value="0.12"/>', "0.13", False, id="fraction-digit-above"),
        pytest.param("xs:decimal", '<xs:maxInclusive value="0.12"/>', "0.11", True, id="fraction-digit-below"),
        pytest.param("xs:decimal", '<xs:maxInclusive value="0.1"/>', "0.12", False, id="fraction-longer-above"),
        pytest.param("xs:decimal", '<xs:maxInclusive value="0.12"/>', "0.1", True, id="fraction-shorter-below"),
        pytest.param("xs:decimal", '<xs:maxInclusive value="1.50"/>', "1.5", True, id="trailing-zeros-equal"),
        pytest.param(
            "xs:decimal",
            '<xs:maxInclusive value="1.5"/>',
            "1.5000000000000000000001",
            False,
            id="long-fraction-above",
        ),
        pytest.param("xs:decimal", '<xs:maxInclusive value="abc"/>', "1", False, id="decimal-bound-not-a-number"),
    ],
)
def test_xsd_exact_decimal_range(base: str, facets: str, value: str, *, ok: bool) -> None:
    assert check(restricted(facets, base), f"<v>{value}</v>").valid is ok


@pytest.mark.parametrize(
    ("base", "facets", "value", "ok"),
    [
        pytest.param("xs:double", '<xs:minInclusive value="NaN"/>', "NaN NaN", True, id="nan-items-under-nan-bound"),
        pytest.param("xs:double", '<xs:minInclusive value="NaN"/>', "NaN 5", False, id="finite-item-under-nan-bound"),
        pytest.param("xs:double", '<xs:maxInclusive value="7.5"/>', "1 NaN", False, id="nan-item-under-bound"),
        pytest.param("xs:decimal", '<xs:maxInclusive value="0"/>', f"0 {_TINY_DECIMAL}", False, id="tiny-item-above"),
        pytest.param("xs:decimal", '<xs:minInclusive value="0"/>', f"1 -{_TINY_DECIMAL}", False, id="tiny-item-below"),
    ],
)
def test_xsd_list_item_range(base: str, facets: str, value: str, *, ok: bool) -> None:
    schema: Final[str] = (
        f'<xs:schema {_XS}><xs:simpleType name="item"><xs:restriction base="{base}">{facets}</xs:restriction>'
        '</xs:simpleType><xs:simpleType name="items"><xs:list itemType="item"/></xs:simpleType>'
        '<xs:element name="v" type="items"/></xs:schema>'
    )
    assert check(schema, f"<v>{value}</v>").valid is ok


def test_xsd_long_range_facet_value() -> None:
    schema: Final[str] = restricted(f'<xs:maxInclusive value="{"0" * 80}3"/>', "xs:integer")
    assert [check(schema, f"<v>{value}</v>").valid for value in ("3", "4")] == [True, False]


def test_xsd_long_number_buffer_grows_per_value() -> None:
    # one validate() call grows its buffer for each longer value and reuses it for shorter ones
    schema: Final[str] = (
        f'<xs:schema {_XS}><xs:element name="r"><xs:complexType><xs:sequence>'
        '<xs:element name="v" maxOccurs="unbounded"><xs:simpleType><xs:restriction base="xs:integer">'
        '<xs:maxInclusive value="100"/></xs:restriction></xs:simpleType></xs:element>'
        "</xs:sequence></xs:complexType></xs:element></xs:schema>"
    )
    in_bound: Final[str] = "".join(f"<v>{'0' * (length - 2)}50</v>" for length in (3, 64, 100, 300, 200))
    out_of_bound: Final[str] = f"{in_bound}<v>{'0' * 199}101</v>"
    assert [check(schema, f"<r>{document}</r>").valid for document in (in_bound, out_of_bound)] == [True, False]


def test_xsd_long_double_buffer_grows_per_value() -> None:
    # a double converts through the buffer one validate() call grows: to just past the value, then to double its
    # capacity once a value outgrows that, and a value that fits the grown buffer keeps it
    schema: Final[str] = (
        f'<xs:schema {_XS}><xs:element name="r"><xs:complexType><xs:sequence>'
        '<xs:element name="v" maxOccurs="unbounded"><xs:simpleType><xs:restriction base="xs:double">'
        '<xs:maxInclusive value="100"/></xs:restriction></xs:simpleType></xs:element>'
        "</xs:sequence></xs:complexType></xs:element></xs:schema>"
    )
    in_bound: Final[str] = "".join(f"<v>{'0' * (length - 1)}5</v>" for length in (71, 100, 80))
    out_of_bound: Final[str] = f"{in_bound}<v>{'0' * 149}150</v>"
    assert [check(schema, f"<r>{document}</r>").valid for document in (in_bound, out_of_bound)] == [True, False]


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


def _group_chain(count: int, *, reverse: bool, nesting: int = 0, wrapper: str = "xs:sequence") -> str:
    # each group holds its ref to the next one under `nesting` more particles
    opened, closed = f"<{wrapper}>" * nesting, f"</{wrapper}>" * nesting
    defs: Final[list[str]] = [
        f'<xs:group name="g{n}"><xs:sequence>{opened}<xs:group ref="g{n + 1}"/>{closed}</xs:sequence></xs:group>'
        for n in range(count)
    ]
    defs.append(f'<xs:group name="g{count}"><xs:sequence><xs:element name="x"/></xs:sequence></xs:group>')
    if reverse:
        defs.reverse()
    return (
        f"<xs:schema {_XS}>{''.join(defs)}"
        '<xs:element name="r"><xs:complexType><xs:group ref="g0"/></xs:complexType></xs:element></xs:schema>'
    )


def _reference_ladder(kind: str, levels: int, *, refs: int = 1, hidden: bool = False) -> str:
    opening, closing = ("<xs:sequence>", "</xs:sequence>") if kind == "group" else ("", "")
    leaf: Final[str] = '<xs:element name="x" minOccurs="0"/>' if kind == "group" else '<xs:attribute name="x"/>'
    # hidden: the references sit under an annotation, where validation does not follow them
    wrap: Final[tuple[str, str]] = ("<xs:annotation>", "</xs:annotation>") if hidden else ("", "")
    defs: Final[list[str]] = [
        f'<xs:{kind} name="g{n}">{opening}{wrap[0]}<xs:{kind} ref="g{n + 1}"/><xs:{kind} ref="g{n + 1}"/>{wrap[1]}'
        f"{closing}</xs:{kind}>"
        for n in range(levels)
    ]
    defs.append(f'<xs:{kind} name="g{levels}">{opening}{leaf}{closing}</xs:{kind}>')
    uses: Final[str] = f'<xs:{kind} ref="g0"/>' * refs
    return (
        f"<xs:schema {_XS}>{''.join(defs)}"
        f'<xs:element name="r"><xs:complexType>{opening}{uses}{closing}</xs:complexType></xs:element></xs:schema>'
    )


def _measured_hub_chain() -> str:
    # hub is measured first through its longer branch; the t chain then reaches it through that stored length
    def link(name: str, target: str) -> str:
        return f'<xs:group name="{name}"><xs:sequence><xs:group ref="{target}"/></xs:sequence></xs:group>'

    leaf: Final[str] = '<xs:sequence><xs:element name="x" minOccurs="0"/></xs:sequence>'
    return (
        f'<xs:schema {_XS}><xs:group name="hub"><xs:sequence><xs:group ref="l0"/><xs:group ref="short"/>'
        f"</xs:sequence></xs:group>{''.join(link(f'l{n}', f'l{n + 1}') for n in range(70))}"
        f'<xs:group name="l70">{leaf}</xs:group><xs:group name="short">{leaf}</xs:group>'
        f"{''.join(link(f't{n}', f't{n + 1}' if n < 39 else 'hub') for n in range(40))}"
        '<xs:element name="r"><xs:complexType><xs:group ref="t0"/></xs:complexType></xs:element></xs:schema>'
    )


def _hidden_group_chain(count: int, *, cycle: bool) -> str:
    defs: Final[list[str]] = [
        f'<xs:group name="g{n}"><xs:sequence><xs:annotation><xs:group ref="g{(n + 1) % count if cycle else n + 1}"/>'
        "</xs:annotation></xs:sequence></xs:group>"
        for n in range(count)
    ]
    defs.append(f'<xs:group name="g{count}"><xs:sequence><xs:element name="x"/></xs:sequence></xs:group>')
    return (
        f"<xs:schema {_XS}>{''.join(defs)}"
        '<xs:element name="r"><xs:complexType><xs:group ref="g0"/></xs:complexType></xs:element></xs:schema>'
    )


_EXPANSION_LIMIT: Final[int] = 20_000
_EXPANSION: Final[str] = f"complex type references expand to more than {_EXPANSION_LIMIT} particles and attributes"


def _ladder_under_each_base() -> str:
    ladder = _reference_ladder("attributeGroup", 10).partition('<xs:element name="r">')[0]
    bases = "".join(
        f'<xs:complexType name="t{n}"><xs:complexContent><xs:extension base="t{n + 1}">'
        '<xs:attributeGroup ref="g0"/></xs:extension></xs:complexContent></xs:complexType>'
        for n in range(5)
    )
    return (
        f"{ladder}{bases}"
        '<xs:complexType name="t5"><xs:attributeGroup ref="g0"/></xs:complexType>'
        '<xs:element name="r" type="t0"/></xs:schema>'
    )


_SHARED_BASE: Final[str] = (
    f"<xs:schema {_XS}>"
    '<xs:attributeGroup name="ids"><xs:attribute name="id"/><xs:attributeGroup ref="more"/></xs:attributeGroup>'
    '<xs:attributeGroup name="more"><xs:attribute name="lang"/></xs:attributeGroup>'
    '<xs:group name="head"><xs:sequence><!-- first --><xs:element name="title"/></xs:sequence></xs:group>'
    '<xs:complexType name="base"><xs:annotation/><xs:group ref="head"/><xs:attributeGroup ref="ids"/></xs:complexType>'
    '<xs:complexType name="left"><xs:complexContent><xs:extension base="base"><xs:sequence>'
    '<xs:element name="l"/></xs:sequence><xs:attributeGroup ref="more"/></xs:extension></xs:complexContent>'
    "</xs:complexType>"
    '<xs:complexType name="right"><xs:complexContent><xs:extension base="base"><xs:sequence>'
    '<xs:element name="r"/></xs:sequence></xs:extension></xs:complexContent></xs:complexType>'
    '<xs:element name="doc"><xs:complexType><xs:sequence><xs:element name="a" type="left"/>'
    '<xs:element name="b" type="right"/></xs:sequence></xs:complexType></xs:element></xs:schema>'
)


# a content model the schema spells out costs what it says however long it is; only what references add is expansion
def _wide_literal_model() -> str:
    leaves = '<xs:element name="x" minOccurs="0"/>' * (_EXPANSION_LIMIT + 1)
    return (
        f'<xs:schema {_XS}><xs:group name="tail"><xs:sequence><xs:element name="y"/></xs:sequence></xs:group>'
        f'<xs:element name="r"><xs:complexType><xs:sequence>{leaves}'
        '<xs:group ref="tail"/></xs:sequence><xs:attribute name="a"/></xs:complexType></xs:element></xs:schema>'
    )


def _identity_constraint(constraint: str) -> str:
    return (
        f'<xs:schema {_XS}><xs:element name="r"><xs:complexType><xs:sequence>'
        '<xs:element name="i" maxOccurs="unbounded"><xs:complexType><xs:attribute name="k" type="xs:string"/>'
        "</xs:complexType></xs:element></xs:sequence></xs:complexType>"
        f'<xs:{constraint} name="c"><xs:selector xpath="i"/><xs:field xpath="@k"/></xs:{constraint}>'
        "</xs:element></xs:schema>"
    )


@pytest.mark.parametrize(
    ("schema", "message"),
    [
        pytest.param(
            f'<xs:schema {_XS}><xs:complexType name="t"><xs:group ref="nope"/></xs:complexType>'
            '<xs:element name="r" type="t"/></xs:schema>',
            "group reference 'nope' does not resolve to a declared group",
            id="unresolved-group-ref",
        ),
        pytest.param(
            f'<xs:schema {_XS}><xs:element name="r"><xs:complexType>'
            '<xs:attribute ref="nope"/></xs:complexType></xs:element></xs:schema>',
            "attribute reference 'nope' does not resolve to a declared attribute",
            id="unresolved-attribute-ref",
        ),
        pytest.param(
            f'<xs:schema {_XS}><xs:element name="r"><xs:complexType><xs:sequence>'
            '<xs:element ref="missing"/></xs:sequence></xs:complexType></xs:element></xs:schema>',
            "element reference 'missing' does not resolve to a declared element",
            id="unresolved-element-ref",
        ),
        pytest.param(
            f'<xs:schema {_XS}><xs:element name="r"><xs:complexType>'
            '<xs:attributeGroup ref="missing"/></xs:complexType></xs:element></xs:schema>',
            "attribute group reference 'missing' does not resolve to a declared attribute group",
            id="unresolved-attribute-group-ref",
        ),
        pytest.param(
            f'<xs:schema {_XS}><xs:element name="qty" type="Digits"/></xs:schema>',
            "type 'Digits' does not resolve to a declared type",
            id="unresolved-element-type",
        ),
        pytest.param(
            f'<xs:schema {_XS}><xs:element name="r"><xs:complexType>'
            '<xs:attribute name="a" type="Weird"/></xs:complexType></xs:element></xs:schema>',
            "type 'Weird' does not resolve to a declared type",
            id="unresolved-attribute-type",
        ),
        pytest.param(
            f'<xs:schema {_XS}><xs:element name="v"><xs:simpleType>'
            '<xs:restriction base="madeUpType"><xs:minLength value="2"/></xs:restriction>'
            "</xs:simpleType></xs:element></xs:schema>",
            "base type 'madeUpType' does not resolve to a declared type",
            id="unresolved-restriction-base",
        ),
        pytest.param(
            f'<xs:schema {_XS}><xs:element name="r"><xs:complexType><xs:complexContent>'
            '<xs:extension base="nope"><xs:sequence><xs:element name="a" type="xs:string"/></xs:sequence>'
            "</xs:extension></xs:complexContent></xs:complexType></xs:element></xs:schema>",
            "base type 'nope' does not resolve to a declared type",
            id="unresolved-complex-content-base",
        ),
        pytest.param(
            f'<xs:schema {_XS}><xs:element name="r"><xs:complexType><xs:simpleContent>'
            '<xs:extension base="Nope"><xs:attribute name="a" type="xs:string"/></xs:extension>'
            "</xs:simpleContent></xs:complexType></xs:element></xs:schema>",
            "base type 'Nope' does not resolve to a declared type",
            id="unresolved-simple-content-base",
        ),
        pytest.param(
            f'<xs:schema {_XS}><xs:group name="g"><xs:sequence><xs:group ref="g"/></xs:sequence></xs:group>'
            '<xs:element name="r"><xs:complexType><xs:group ref="g"/></xs:complexType></xs:element></xs:schema>',
            "circular group reference to 'g'",
            id="group-cycle",
        ),
        pytest.param(
            f'<xs:schema {_XS}><xs:attributeGroup name="a"><xs:attributeGroup ref="a"/></xs:attributeGroup>'
            '<xs:element name="r"><xs:complexType><xs:attributeGroup ref="a"/></xs:complexType></xs:element>'
            "</xs:schema>",
            "circular attribute group reference to 'a'",
            id="attribute-group-cycle",
        ),
        pytest.param(
            f"<xs:schema {_XS}>"
            '<xs:complexType name="A"><xs:complexContent><xs:extension base="B"><xs:sequence/></xs:extension>'
            "</xs:complexContent></xs:complexType>"
            '<xs:complexType name="B"><xs:complexContent><xs:extension base="A"><xs:sequence/></xs:extension>'
            "</xs:complexContent></xs:complexType>"
            '<xs:element name="r" type="A"/></xs:schema>',
            "circular complex type reference to 'A'",
            id="type-derivation-cycle",
        ),
        pytest.param(
            _group_chain(100, reverse=False),
            "group reference chain exceeds 100 hops",
            id="deep-group-chain-in-document-order",
        ),
        pytest.param(
            _group_chain(100, reverse=True),
            "group reference chain exceeds 100 hops",
            id="deep-group-chain-tail-compiled-first",
        ),
        pytest.param(
            _measured_hub_chain(),
            "group reference chain exceeds 100 hops",
            id="deep-group-chain-through-measured-branches",
        ),
        pytest.param(
            _group_chain(7, reverse=False, nesting=60),
            "group reference chain nests 400 levels or deeper",
            id="deeply-nested-group-chain-in-document-order",
        ),
        pytest.param(
            _group_chain(7, reverse=True, nesting=60),
            "group reference chain nests 400 levels or deeper",
            id="deeply-nested-group-chain-tail-compiled-first",
        ),
        pytest.param(
            f'<xs:schema {_XS}><xs:group name="g">{"<xs:sequence>" * 90}<xs:element name="x"/>'
            f'{"</xs:sequence>" * 90}</xs:group><xs:element name="r"><xs:complexType>'
            f'{"<xs:sequence>" * 330}<xs:group ref="g"/>{"</xs:sequence>" * 330}</xs:complexType></xs:element>'
            "</xs:schema>",
            "group reference chain nests 400 levels or deeper",
            id="group-used-under-deep-nesting",
        ),
        pytest.param(
            f"<xs:schema {_XS}>"
            f"""{
                "".join(
                    f'<xs:complexType name="t{n}"><xs:complexContent><xs:extension base="t{n + 1}"><xs:sequence/>'
                    "</xs:extension></xs:complexContent></xs:complexType>"
                    for n in range(100)
                )
            }"""
            """<xs:complexType name="t100"><xs:sequence/></xs:complexType><xs:element name="r" """
            """type="t0"/></xs:schema>""",
            "complex type reference chain exceeds 100 hops",
            id="deep-type-extension-chain",
        ),
        pytest.param(_reference_ladder("group", 12), _EXPANSION, id="group-ladder"),
        pytest.param(_reference_ladder("attributeGroup", 12), _EXPANSION, id="attribute-group-ladder"),
        pytest.param(_reference_ladder("group", 8, refs=30), _EXPANSION, id="group-ladder-referred-to-many-times"),
        pytest.param(
            _reference_ladder("attributeGroup", 8, refs=30),
            _EXPANSION,
            id="attribute-group-ladder-referred-to-many-times",
        ),
        pytest.param(_ladder_under_each_base(), _EXPANSION, id="attribute-group-ladder-under-each-base-type"),
        pytest.param(
            _identity_constraint("unique"), "identity constraint xs:unique is not supported", id="identity-unique"
        ),
        pytest.param(_identity_constraint("key"), "identity constraint xs:key is not supported", id="identity-key"),
        pytest.param(
            f'<xs:schema {_XS}><xs:element name="r"><xs:complexType><xs:sequence>'
            '<xs:element name="ref" maxOccurs="unbounded"><xs:complexType><xs:attribute name="to" type="xs:string"/>'
            "</xs:complexType></xs:element></xs:sequence></xs:complexType>"
            '<xs:keyref name="fk" refer="pk"><xs:selector xpath="ref"/><xs:field xpath="@to"/></xs:keyref>'
            "</xs:element></xs:schema>",
            "identity constraint xs:keyref is not supported",
            id="identity-keyref",
        ),
    ],
)
def test_schema_rejected_at_compile(schema: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        XMLSchema(schema)


_ANY_TYPE_EXTENSION: Final[str] = (
    f'<xs:schema {_XS}><xs:element name="r"><xs:complexType><xs:complexContent>'
    '<xs:extension base="xs:anyType"><xs:sequence><xs:element name="a" type="xs:string"/></xs:sequence>'
    "</xs:extension></xs:complexContent></xs:complexType></xs:element></xs:schema>"
)
_SIMPLE_TYPE_BASE: Final[str] = (
    f"<xs:schema {_XS}>"
    '<xs:simpleType name="code"><xs:restriction base="xs:string"><xs:maxLength value="4"/></xs:restriction>'
    "</xs:simpleType>"
    '<xs:complexType name="t"><xs:simpleContent><xs:extension base="code">'
    '<xs:attribute name="a" type="xs:string"/></xs:extension></xs:simpleContent></xs:complexType>'
    '<xs:element name="r" type="t"/></xs:schema>'
)
_EMPTY_COMPLEX_CONTENT: Final[str] = (
    f'<xs:schema {_XS}><xs:complexType name="t"><xs:complexContent></xs:complexContent></xs:complexType>'
    '<xs:element name="r" type="t"/></xs:schema>'
)
# the documentation names the group it sits in: not a particle, so neither a branch nor a reference to follow
_ANNOTATED_GROUP: Final[str] = (
    f'<xs:schema {_XS}><xs:group name="g"><xs:{{model}}>'
    '<xs:annotation><xs:documentation><xs:group ref="g"/></xs:documentation></xs:annotation>'
    f'<xs:element name="a" type="xs:string"/></xs:{{model}}></xs:group>'
    '<xs:element name="r"><xs:complexType><xs:group ref="g"/></xs:complexType></xs:element></xs:schema>'
)


@pytest.mark.parametrize(
    ("schema", "xml", "valid"),
    [
        pytest.param(_group_chain(20, reverse=False), "<r><x/></r>", True, id="group-chain-within-limit"),
        pytest.param(_group_chain(20, reverse=False), "<r><y/></r>", False, id="group-chain-within-limit-wrong-leaf"),
        pytest.param(
            _group_chain(4, reverse=False, nesting=90), "<r><x/></r>", True, id="nested-group-chain-within-limit"
        ),
        pytest.param(
            _group_chain(4, reverse=False, nesting=90),
            "<r><y/></r>",
            False,
            id="nested-group-chain-within-limit-wrong-leaf",
        ),
        pytest.param(
            f'<xs:schema {_XS}><xs:group name="g"><xs:sequence><xs:element name="x"><xs:complexType><xs:sequence>'
            '<xs:group ref="g" minOccurs="0"/></xs:sequence></xs:complexType></xs:element></xs:sequence></xs:group>'
            '<xs:element name="r"><xs:complexType><xs:group ref="g"/></xs:complexType></xs:element></xs:schema>',
            "<r><x><x><x/></x></x></r>",
            True,
            id="group-recursing-through-an-element",
        ),
        pytest.param(
            f"<xs:schema {_XS}>"
            '<xs:group name="g0"><xs:sequence><xs:group ref="g1"/><xs:group ref="g1"/></xs:sequence></xs:group>'
            '<xs:group name="g1"><xs:sequence><xs:element name="x" type="xs:string"/></xs:sequence></xs:group>'
            '<xs:element name="r"><xs:complexType><xs:group ref="g0"/></xs:complexType></xs:element></xs:schema>',
            "<r><x>a</x><x>b</x></r>",
            True,
            id="shared-group-reference",
        ),
        pytest.param(
            f'<xs:schema {_XS}><xs:element name="r"><xs:complexType>'
            '<xs:attribute use="optional"/><xs:attribute name="keep" type="xs:string"/>'
            "</xs:complexType></xs:element></xs:schema>",
            '<r keep="x"/>',
            True,
            id="nameless-attribute-declaration-ignored",
        ),
        pytest.param(_ANY_TYPE_EXTENSION, "<r><a>x</a></r>", True, id="any-type-extension"),
        pytest.param(_ANY_TYPE_EXTENSION, "<r><b>x</b></r>", False, id="any-type-extension-wrong-child"),
        pytest.param(
            f"<xs:schema {_XS}>"
            '<xs:complexType name="inner"><xs:simpleContent><xs:extension base="xs:string">'
            '<xs:attribute name="u" type="xs:string"/></xs:extension></xs:simpleContent></xs:complexType>'
            '<xs:complexType name="outer"><xs:simpleContent><xs:extension base="inner">'
            '<xs:attribute name="v" type="xs:string"/></xs:extension></xs:simpleContent></xs:complexType>'
            '<xs:element name="r" type="outer"/></xs:schema>',
            '<r u="a" v="b">text</r>',
            True,
            id="simple-content-base-is-a-complex-type",
        ),
        pytest.param(_SIMPLE_TYPE_BASE, '<r a="x">text</r>', True, id="simple-content-base-is-a-simple-type"),
        pytest.param(_SIMPLE_TYPE_BASE, '<r a="x">toolong</r>', False, id="simple-content-base-keeps-its-facet"),
        pytest.param(_SIMPLE_TYPE_BASE, '<r a="x">longer</r>', False, id="simple-content-base-facet-applies"),
        pytest.param(_SIMPLE_TYPE_BASE, '<r a="x"><child/></r>', False, id="simple-content-rejects-a-child"),
        pytest.param(_EMPTY_COMPLEX_CONTENT, "<r/>", True, id="named-type-empty-complex-content"),
        pytest.param(_EMPTY_COMPLEX_CONTENT, "<r><x/></r>", False, id="named-type-empty-complex-content-child"),
        pytest.param(
            f'<xs:schema {_XS}><xs:complexType name="t"><xs:complexContent><xs:restriction>'
            '<xs:sequence><xs:element name="a" type="xs:string"/></xs:sequence>'
            "</xs:restriction></xs:complexContent></xs:complexType>"
            '<xs:element name="r" type="t"/></xs:schema>',
            "<r><a>x</a></r>",
            True,
            id="named-type-restriction-without-base",
        ),
        pytest.param(
            f'<xs:schema {_XS}><xs:group name="g"><!-- model --><xs:sequence>'
            '<xs:element name="x" type="xs:string"/></xs:sequence></xs:group>'
            '<xs:element name="r"><xs:complexType><xs:group ref="g"/></xs:complexType></xs:element></xs:schema>',
            "<r><x>v</x></r>",
            True,
            id="group-definition-with-a-comment-child",
        ),
        pytest.param(
            f'<xs:schema {_XS}><xs:attributeGroup name="g"><xs:complexContent><xs:extension>'
            '<xs:attributeGroup ref="g"/></xs:extension></xs:complexContent></xs:attributeGroup>'
            '<xs:element name="r"><xs:complexType><xs:attributeGroup ref="g"/></xs:complexType></xs:element>'
            "</xs:schema>",
            "<r/>",
            True,
            id="attribute-group-content-child-not-followed",
        ),
        pytest.param(
            f'<xs:schema {_XS}><xs:attributeGroup name="g"><xs:complexContent><xs:extension base="T"/>'
            "</xs:complexContent></xs:attributeGroup>"
            '<xs:complexType name="T"><xs:attributeGroup ref="g"/></xs:complexType>'
            '<xs:element name="r" type="T"/></xs:schema>',
            "<r/>",
            True,
            id="attribute-group-base-type-not-followed",
        ),
        pytest.param(
            f'<xs:schema {_XS}><xs:complexType name="T"><xs:complexContent/>'
            '<xs:simpleContent><xs:extension base="T"/></xs:simpleContent></xs:complexType>'
            '<xs:element name="r" type="T"/></xs:schema>',
            "<r/>",
            True,
            id="type-second-content-child-not-followed",
        ),
        pytest.param(
            f'<xs:schema {_XS}><xs:complexType name="T"><xs:complexContent><xs:extension base="xs:anyType">'
            '<xs:complexContent><xs:extension base="T"/></xs:complexContent>'
            "</xs:extension></xs:complexContent></xs:complexType>"
            '<xs:element name="r" type="T"/></xs:schema>',
            "<r/>",
            True,
            id="derivation-content-child-not-followed",
        ),
        pytest.param(_ANNOTATED_GROUP.format(model="sequence"), "<r><a/></r>", True, id="annotated-sequence"),
        pytest.param(_ANNOTATED_GROUP.format(model="sequence"), "<r/>", False, id="annotated-sequence-needs-child"),
        pytest.param(_ANNOTATED_GROUP.format(model="choice"), "<r><a/></r>", True, id="annotated-choice"),
        pytest.param(_ANNOTATED_GROUP.format(model="choice"), "<r/>", False, id="annotated-choice-needs-child"),
        pytest.param(
            f'<xs:schema {_XS}><xs:group name="g"><xs:choice><xs:element name="a"/>'
            '<xs:any minOccurs="0"><xs:group ref="g"/></xs:any></xs:choice></xs:group>'
            '<xs:element name="r"><xs:complexType><xs:group ref="g"/></xs:complexType></xs:element></xs:schema>',
            "<r/>",
            True,
            id="choice-wildcard-stays-an-empty-branch",
        ),
        pytest.param(
            f'<xs:schema {_XS}><xs:group name="g"><xs:sequence><xs:element name="a"/>'
            '<xs:any minOccurs="0"><xs:group ref="g"/></xs:any></xs:sequence></xs:group>'
            '<xs:element name="r"><xs:complexType><xs:group ref="g"/></xs:complexType></xs:element></xs:schema>',
            "<r><a/></r>",
            True,
            id="sequence-wildcard-not-descended",
        ),
        pytest.param(_reference_ladder("group", 8), "<r><x/><x/><x/></r>", True, id="group-ladder-within-limit"),
        pytest.param(_reference_ladder("group", 8), "<r><y/></r>", False, id="group-ladder-within-limit-wrong-leaf"),
        pytest.param(
            _reference_ladder("attributeGroup", 8), '<r x="1"/>', True, id="attribute-group-ladder-within-limit"
        ),
        pytest.param(
            _reference_ladder("attributeGroup", 8),
            '<r y="1"/>',
            False,
            id="attribute-group-ladder-within-limit-undeclared",
        ),
        # validation follows no reference under an annotation, so none of these unfold, loop, or reach the element
        pytest.param(_hidden_group_chain(9, cycle=False), "<r/>", True, id="group-chain-behind-annotation"),
        pytest.param(
            _hidden_group_chain(9, cycle=False), "<r><x/></r>", False, id="group-chain-behind-annotation-no-leaf"
        ),
        pytest.param(_hidden_group_chain(250, cycle=False), "<r/>", True, id="deep-group-chain-behind-annotation"),
        pytest.param(_hidden_group_chain(2, cycle=True), "<r/>", True, id="group-cycle-behind-annotation"),
        pytest.param(_reference_ladder("group", 12, hidden=True), "<r/>", True, id="group-ladder-behind-annotation"),
        pytest.param(
            _SHARED_BASE,
            '<doc><a id="1" lang="en"><title/><l/></a><b id="2"><title/><r/></b></doc>',
            True,
            id="references-shared-through-a-base-type",
        ),
        pytest.param(
            _SHARED_BASE,
            '<doc><a id="1"><title/><l/></a><b nope="2"><title/><r/></b></doc>',
            False,
            id="references-shared-through-a-base-type-undeclared",
        ),
        pytest.param(_wide_literal_model(), '<r a="1"><x/><y/></r>', True, id="long-literal-model-is-not-expansion"),
        # a reference under a non-particle is not followed, so it neither nests the chain nor closes a cycle
        pytest.param(
            _group_chain(7, reverse=False, nesting=60, wrapper="xs:annotation"),
            "<r/>",
            True,
            id="deeply-nested-group-chain-under-non-particles",
        ),
        pytest.param(
            f'<xs:schema {_XS}><xs:group name="g"><xs:choice><xs:annotation><xs:appinfo><xs:group ref="g"/>'
            '</xs:appinfo></xs:annotation><xs:element name="x"/></xs:choice></xs:group>'
            '<xs:element name="r"><xs:complexType><xs:group ref="g"/></xs:complexType></xs:element></xs:schema>',
            "<r><x/></r>",
            True,
            id="group-cycle-under-a-non-particle",
        ),
        pytest.param(
            f'<xs:schema {_XS}><xs:group name="g"><xs:annotation><xs:documentation>doc</xs:documentation>'
            '</xs:annotation><xs:sequence><xs:element name="x" type="xs:string"/></xs:sequence></xs:group>'
            '<xs:element name="r"><xs:complexType><xs:group ref="g"/></xs:complexType></xs:element></xs:schema>',
            "<r><x>v</x></r>",
            True,
            id="group-definition-with-an-annotation",
        ),
    ],
)
def test_schema_compiles_and_validates(schema: str, xml: str, *, valid: bool) -> None:
    assert xsd_ok(schema, xml) is valid


_XS: Final[str] = 'xmlns:xs="http://www.w3.org/2001/XMLSchema"'
_INVALID: Final[list[pytest.ParameterSet]] = [
    pytest.param('<xs:element name="v" type="xs:notAType"/>', "does not resolve", id="unknown-global-type"),
    pytest.param('<xs:element name="v" type="xs:weirdUnknownType"/>', "does not resolve", id="old-string-fallback"),
    pytest.param(
        '<xs:element name="v"><xs:complexType><xs:sequence><xs:element name="child" type="xs:notAType"/>'
        "</xs:sequence></xs:complexType></xs:element>",
        "does not resolve",
        id="unknown-local-type",
    ),
    pytest.param(
        '<xs:element name="v"><xs:complexType><xs:attribute name="key" type="xs:notAType"/>'
        "</xs:complexType></xs:element>",
        "does not resolve",
        id="unknown-attribute-type",
    ),
    pytest.param('<xs:element name="v" type=""/>', "does not resolve", id="empty-type"),
    pytest.param('<xs:element type="xs:string"/>', "requires a name", id="missing-global-name"),
    pytest.param('<xs:element name="" type="xs:string"/>', "requires a name", id="empty-global-name"),
    pytest.param('<xs:element ref="v"/>', "requires a name", id="global-reference"),
    pytest.param(
        '<xs:group><xs:sequence><xs:element name="a" type="xs:string"/></xs:sequence></xs:group>',
        "global group requires a name",
        id="unnamed-global-group",
    ),
    pytest.param(
        '<xs:group name=""><xs:sequence/></xs:group>', "global group requires a name", id="empty-global-group"
    ),
    pytest.param(
        "<xs:complexType><xs:sequence/></xs:complexType>",
        "global complexType requires a name",
        id="unnamed-complex-type",
    ),
    pytest.param(
        '<xs:simpleType><xs:restriction base="xs:string"/></xs:simpleType>',
        "global simpleType requires a name",
        id="unnamed-simple-type",
    ),
    pytest.param('<xs:attribute type="xs:string"/>', "global attribute requires a name", id="unnamed-attribute"),
    pytest.param(
        '<xs:attributeGroup><xs:attribute name="x" type="xs:string"/></xs:attributeGroup>',
        "global attributeGroup requires a name",
        id="unnamed-attribute-group",
    ),
    pytest.param(
        '<xs:simpleType name="notAType"><xs:restriction base="xs:string"/></xs:simpleType>'
        '<xs:element name="v" type="xs:notAType"/>',
        "does not resolve",
        id="wrong-namespace-collision",
    ),
]


@pytest.mark.parametrize(("declarations", "message"), _INVALID)
def test_xsd_compile_rejects_unresolved_type_and_nameless_global(declarations: str, message: str) -> None:
    for source in (f"<xs:schema {_XS}>{declarations}</xs:schema>",) * 2:
        with pytest.raises(ValueError, match=message):
            XMLSchema(source)


@pytest.mark.parametrize(
    ("type_name", "value"),
    [
        pytest.param("anyType", "plain", id="anyType"),
        pytest.param("NOTATION", "xs:known", id="notation-name"),
        pytest.param("ID", "known", id="id"),
        pytest.param("IDREF", "known", id="idref"),
        pytest.param("IDREFS", "known other", id="idrefs"),
        pytest.param("ENTITY", "known", id="entity"),
        pytest.param("ENTITIES", "known other", id="entities"),
        pytest.param("NMTOKENS", "known other", id="nmtokens"),
        pytest.param("gYearMonth", "2026-10", id="year-month"),
        pytest.param("gYear", "2026", id="year"),
        pytest.param("gMonthDay", "--10-06", id="month-day"),
        pytest.param("gDay", "---06", id="day"),
        pytest.param("gMonth", "--10", id="month"),
        pytest.param("string", "plain", id="handled-builtin"),
    ],
)
def test_xsd_compile_preserves_known_builtin_names(type_name: str, value: str) -> None:
    validator: Final[XMLSchema] = XMLSchema(
        f'<xs:schema {_XS}><xs:element name="v" type="xs:{type_name}"/></xs:schema>'
    )
    assert [validator.is_valid(parse_xml(f"<v>{value}</v>")), validator.is_valid(parse_xml("<other/>"))] == [
        True,
        False,
    ]


@pytest.mark.parametrize(
    ("declarations", "document", "expected"),
    [
        pytest.param(
            "<xs:annotation><xs:documentation>schema</xs:documentation></xs:annotation>"
            '<xs:element name="v" type="xs:string"/>',
            "<v>plain</v>",
            True,
            id="nameless-annotation",
        ),
        pytest.param(
            '<foreign xmlns="urn:annotation"/><xs:element name="v" type="xs:string"/>',
            "<v>plain</v>",
            True,
            id="foreign-annotation",
        ),
        pytest.param(
            '<xs:element name="child" type="xs:int"/><xs:element name="v"><xs:complexType>'
            '<xs:sequence><xs:element ref="child"/></xs:sequence></xs:complexType></xs:element>',
            "<v><child>7</child></v>",
            True,
            id="local-reference",
        ),
        pytest.param(
            '<xs:simpleType name="plain"><xs:restriction base="xs:integer"/></xs:simpleType>'
            '<xs:element name="v" type="plain"/>',
            "<v>bad</v>",
            False,
            id="declared-simple-type",
        ),
        pytest.param(
            '<xs:complexType name="plain"><xs:sequence><xs:element name="child" type="xs:int"/>'
            '</xs:sequence></xs:complexType><xs:element name="v" type="plain"/>',
            "<v><child>7</child></v>",
            True,
            id="declared-complex-type",
        ),
    ],
)
def test_xsd_compile_preserves_declarations(declarations: str, document: str, *, expected: bool) -> None:
    validator: Final[XMLSchema] = XMLSchema(f"<xs:schema {_XS}>{declarations}</xs:schema>")
    assert [validator.is_valid(parse_xml(document)) for _ in range(2)] == [expected, expected]


_COUNTED_REPEAT_SCHEMA: Final[str] = (
    f'<xs:schema {_XS}><xs:element name="r"><xs:complexType><xs:sequence>'
    '<xs:element name="v" maxOccurs="unbounded"><xs:simpleType><xs:restriction base="xs:string">'
    '<xs:pattern value="{pattern}"/></xs:restriction></xs:simpleType></xs:element>'
    "</xs:sequence></xs:complexType></xs:element></xs:schema>"
)


@pytest.mark.parametrize("method", ["validate", "is_valid"])
@pytest.mark.parametrize(
    ("pattern", "value"),
    [
        pytest.param("(b?){300000}", "b" * 1000, id="nullable-copies"),
        pytest.param("(b{1,300}){1,500}", "b" * 1200, id="nested-counts"),
        pytest.param(r"(\p{L}?){300000}", "b" * 1000, id="extended-copies"),
    ],
)
def test_pattern_matching_past_the_step_budget_raises(pattern: str, value: str, *, method: str) -> None:
    # a counted repeat keeps many automaton states live for every character; the call stops with an error instead of
    # stepping all of them, and a cheap document validates afterwards
    schema: Final[XMLSchema] = XMLSchema(_COUNTED_REPEAT_SCHEMA.replace("{pattern}", pattern))
    check_document = getattr(schema, method)
    element: Final[str] = f"<v>{value}</v>"
    with pytest.raises(ValueError, match="schema patterns need more than 100000000 NFA steps"):
        check_document(parse_xml(f"<r>{element}<v>b</v></r>"))
    assert bool(check_document(parse_xml("<r><v>b</v></r>")))


@pytest.mark.parametrize(
    ("pattern", "value", "valid"),
    [
        pytest.param("[a-z]+", "q" * 300_000, True, id="long-value"),
        pytest.param("[a-z]+", f"{'q' * 300_000}1", False, id="long-value-mismatch"),
        pytest.param(r"(\s*\S+\s*){1,50}", "word " * 49, True, id="counted-words"),
        pytest.param("c{0,99999}", "c" * 4000, True, id="large-count-of-one-character"),
        pytest.param("(ab|cd){1,500}x", f"{'abcd' * 200}x", True, id="counted-alternation"),
    ],
)
def test_ordinary_patterns_fit_the_step_budget(pattern: str, value: str, *, valid: bool) -> None:
    schema: Final[XMLSchema] = XMLSchema(_COUNTED_REPEAT_SCHEMA.replace("{pattern}", pattern))
    element: Final[str] = f"<v>{value}</v>"
    assert schema.is_valid(parse_xml(f"<r>{element}{element}</r>")) is valid


@pytest.mark.parametrize("padding", [pytest.param(0, id="few-attributes"), pytest.param(40, id="many-attributes")])
def test_xsd_type_prefixes_resolve_below_many_attributes(padding: int) -> None:
    # b is the XSD namespace only where an element redeclares it; elsewhere b:int names the string type declared here
    pad: Final[str] = _padding(padding)
    xsd: Final[str] = 'xmlns:b="http://www.w3.org/2001/XMLSchema"'
    schema: Final[XMLSchema] = XMLSchema(
        f'<xs:schema {pad} {_XS} xmlns:b="urn:t">'
        '<xs:simpleType name="int"><xs:restriction base="xs:string"/></xs:simpleType>'
        f'<xs:simpleType name="small"><xs:restriction base="b:int" {pad} {xsd}>'
        '<xs:maxInclusive value="5"/></xs:restriction></xs:simpleType>'
        '<xs:element name="r"><xs:complexType><xs:sequence>'
        f'<xs:element name="c" type="b:int" {pad} {xsd}/><xs:element name="d" type="b:int"/>'
        '<xs:element name="s" type="small"/></xs:sequence>'
        f'<xs:attribute name="e" type="b:int" {pad} {xsd}/><xs:attribute name="f" type="b:int"/>'
        "</xs:complexType></xs:element></xs:schema>"
    )
    assert schema.validate(parse_xml('<r e="1" f="x"><c>1</c><d>x</d><s>3</s></r>')).valid
    result: Final[ValidationResult] = schema.validate(parse_xml('<r e="x" f="x"><c>x</c><d>x</d><s>7</s></r>'))
    assert len(result.errors) == 3


def _padding(count: int) -> str:
    return " ".join(f'pad{index}=""' for index in range(count))


def test_xsd_type_prefixes_below_many_attributes_compile_linearly(work_steps: WorkSteps) -> None:
    # each type reference resolves its prefix through the attributes of xs:schema, which must not cost their count
    def build(count: int) -> Callable[[], XMLSchema]:
        body: Final[str] = "".join(f'<xs:element name="e{index}" type="xs:int"/>' for index in range(count))
        return partial(XMLSchema, f"<xs:schema {_padding(count)} {_XS}>{body}</xs:schema>")

    work_steps.assert_linear(build, (70, 280))


def test_xsd_type_prefixes_below_many_attributes_validate_linearly(work_steps: WorkSteps) -> None:
    # every instance element and attribute looks its declared type up again
    def build(count: int) -> Callable[[], ValidationResult]:
        schema: Final[XMLSchema] = XMLSchema(
            f'<xs:schema {_padding(count)} {_XS}><xs:element name="r"><xs:complexType><xs:sequence>'
            '<xs:element name="v" maxOccurs="unbounded"><xs:complexType><xs:simpleContent>'
            '<xs:extension base="xs:int"><xs:attribute name="a" type="xs:int"/></xs:extension>'
            "</xs:simpleContent></xs:complexType></xs:element>"
            "</xs:sequence></xs:complexType></xs:element></xs:schema>"
        )
        item: Final[str] = '<v a="1">2</v>'
        return partial(schema.validate, parse_xml(f"<r>{item * count}</r>"))

    work_steps.assert_linear(build, (70, 280))
