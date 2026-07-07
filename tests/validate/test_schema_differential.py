"""turbohtml's XSD / RELAX NG verdicts cross-checked against lxml.

lxml is a bench dependency, not a test one, and ships no wheels for 3.15, the free-threaded builds, or Windows, so this
module importorskips itself where the oracle is absent and is omitted from the coverage gate (see ``[tool.coverage]``).
It still runs and validates wherever lxml installs, agreeing on the valid/invalid verdict for every case below.
"""

from __future__ import annotations

from typing import Any

import pytest

from turbohtml import parse_xml
from turbohtml.validate import RelaxNG, XMLSchema

XS = 'xmlns:xs="http://www.w3.org/2001/XMLSchema"'
RN = "http://relaxng.org/ns/structure/1.0"

_XSD_CASES = [
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
]

_RNG_CASES = [
    pytest.param(f'<element name="a" xmlns="{RN}"><text/></element>', "<a>hi</a>", id="text-valid"),
    pytest.param(
        f'<element name="a" xmlns="{RN}"><element name="b"><text/></element></element>',
        "<a><c>x</c></a>",
        id="wrong-child-invalid",
    ),
    pytest.param(
        f'<element name="p" xmlns="{RN}"><interleave>'
        '<element name="a"><text/></element><element name="b"><text/></element></interleave></element>',
        "<p><b>2</b><a>1</a></p>",
        id="interleave-any-order-valid",
    ),
    pytest.param(
        f'<element name="p" xmlns="{RN}"><interleave>'
        '<element name="a"><text/></element><element name="b"><text/></element></interleave></element>',
        "<p><a>1</a></p>",
        id="interleave-missing-invalid",
    ),
    pytest.param(
        f'<element name="r" xmlns="{RN}"><oneOrMore><element name="i"><text/></element></oneOrMore></element>',
        "<r><i>1</i><i>2</i></r>",
        id="oneOrMore-valid",
    ),
    pytest.param(
        f'<element name="r" xmlns="{RN}"><oneOrMore><element name="i"><text/></element></oneOrMore></element>',
        "<r/>",
        id="oneOrMore-empty-invalid",
    ),
    pytest.param(
        f'<element name="e" xmlns="{RN}" datatypeLibrary="http://www.w3.org/2001/XMLSchema-datatypes">'
        '<data type="int"/></element>',
        "<e>7</e>",
        id="data-int-valid",
    ),
    pytest.param(
        f'<element name="e" xmlns="{RN}" datatypeLibrary="http://www.w3.org/2001/XMLSchema-datatypes">'
        '<data type="int"/></element>',
        "<e>seven</e>",
        id="data-int-invalid",
    ),
    pytest.param(
        f'<element name="r" xmlns="{RN}"><attribute name="id"><text/></attribute><text/></element>',
        '<r id="1">x</r>',
        id="attribute-valid",
    ),
    pytest.param(
        f'<element name="r" xmlns="{RN}"><attribute name="id"><text/></attribute><text/></element>',
        "<r>x</r>",
        id="attribute-missing-invalid",
    ),
]


def _lxml_xsd_valid(etree: Any, schema: str, doc: str) -> bool:  # noqa: ANN401  # lxml.etree is an untyped third-party module
    validator = etree.XMLSchema(etree.fromstring(schema.encode()))
    return bool(validator.validate(etree.fromstring(doc.encode())))


def _lxml_rng_valid(etree: Any, schema: str, doc: str) -> bool:  # noqa: ANN401  # lxml.etree is an untyped third-party module
    validator = etree.RelaxNG(etree.fromstring(schema.encode()))
    return bool(validator.validate(etree.fromstring(doc.encode())))


@pytest.mark.parametrize(("schema", "doc"), _XSD_CASES)
def test_xsd_matches_lxml(schema: str, doc: str) -> None:
    etree: Any = pytest.importorskip("lxml.etree")
    assert XMLSchema(schema).validate(parse_xml(doc)).valid == _lxml_xsd_valid(etree, schema, doc)


@pytest.mark.parametrize(("schema", "doc"), _RNG_CASES)
def test_relaxng_matches_lxml(schema: str, doc: str) -> None:
    etree: Any = pytest.importorskip("lxml.etree")
    assert RelaxNG(schema).validate(parse_xml(doc)).valid == _lxml_rng_valid(etree, schema, doc)
