from __future__ import annotations

import gc
from typing import TYPE_CHECKING, Final

import pytest

from turbohtml import parse_xml
from turbohtml.validate import RelaxNG, XMLSchema

if TYPE_CHECKING:
    from types import ModuleType


XS = 'xmlns:xs="http://www.w3.org/2001/XMLSchema"'


R = "http://relaxng.org/ns/structure/1.0"


def xsd_ok(schema: str, xml: str) -> bool:
    return XMLSchema(schema).validate(parse_xml(xml)).valid


def rng_ok(schema: str, xml: str) -> bool:
    return RelaxNG(schema).validate(parse_xml(xml)).valid


def rwrap(body: str) -> str:
    return f'<element name="doc" xmlns="{R}">{body}</element>'


@pytest.mark.parametrize(
    "schema",
    [
        pytest.param(
            XMLSchema(
                f'<xs:schema {XS}><xs:element name="a"><xs:complexType><xs:sequence>'
                '<xs:element ref="a" minOccurs="0"/></xs:sequence></xs:complexType></xs:element></xs:schema>'
            ),
            id="xsd",
        ),
        pytest.param(
            RelaxNG(
                f'<grammar xmlns="{R}"><start><ref name="a"/></start>'
                '<define name="a"><element name="a"><optional><ref name="a"/></optional></element></define></grammar>'
            ),
            id="relax-ng",
        ),
    ],
)
@pytest.mark.parametrize("verdict_only", [pytest.param(False, id="report"), pytest.param(True, id="verdict")])
def test_deep_nesting_is_rejected_before_validation(schema: XMLSchema | RelaxNG, *, verdict_only: bool) -> None:
    validate = schema.is_valid if verdict_only else schema.validate
    assert bool(validate(parse_xml("<a>" * 20 + "</a>" * 20)))
    with pytest.raises(RecursionError, match="schema validation"):
        validate(parse_xml("<a>" * 1200 + "</a>" * 1200))


@pytest.mark.parametrize(
    ("schema_type", "source"),
    [
        pytest.param(
            XMLSchema,
            f'<xs:schema {XS}><xs:element name="a"><xs:complexType>'
            + "<xs:sequence>" * 1200
            + '<xs:element name="b"/>'
            + "</xs:sequence>" * 1200
            + "</xs:complexType></xs:element></xs:schema>",
            id="xsd",
        ),
        pytest.param(
            RelaxNG,
            f'<grammar xmlns="{R}"><start>'
            + "<optional>" * 1200
            + "<empty/>"
            + "</optional>" * 1200
            + "</start></grammar>",
            id="relax-ng",
        ),
    ],
)
def test_deep_schema_is_rejected_before_compilation(schema_type: type[XMLSchema | RelaxNG], source: str) -> None:
    with pytest.raises(RecursionError, match="schema compilation"):
        schema_type(source)


def test_long_names_and_values_hit_buffer_bounds() -> None:
    # a >256-char attribute name exercises the decode-buffer cap
    long_attr = "x" * 300
    rng_schema = rwrap(f'<attribute name="{long_attr}"><text/></attribute><text/>')
    assert rng_ok(rng_schema, f'<doc {long_attr}="1">body</doc>')
    # a >128-char value truncates in the datatype error message buffer
    xsd_schema = f'<xs:schema {XS}><xs:element name="v" type="xs:int"/></xs:schema>'
    assert not xsd_ok(xsd_schema, "<v>" + "9" * 200 + "z</v>")


@pytest.mark.parametrize(
    ("schema_type", "source"),
    [
        pytest.param(
            XMLSchema,
            '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"><xs:element name="value"><xs:simpleType>'
            '<xs:restriction base="xs:string"><xs:pattern value="[a-z]+"/></xs:restriction>'
            "</xs:simpleType></xs:element></xs:schema>",
            id="xsd-pattern",
        ),
        pytest.param(
            RelaxNG,
            '<element xmlns="http://relaxng.org/ns/structure/1.0" name="value" '
            'datatypeLibrary="http://www.w3.org/2001/XMLSchema-datatypes">'
            '<data type="string"><param name="pattern">[a-z]+</param></data></element>',
            id="rng-pattern",
        ),
        pytest.param(
            RelaxNG,
            '<grammar xmlns="http://relaxng.org/ns/structure/1.0"><start><ref name="root"/></start>'
            '<define name="root"><element name="value"><oneOrMore><element name="item"><text/>'
            "</element></oneOrMore></element></define></grammar>",
            id="rng-definition",
        ),
    ],
)
@pytest.mark.parametrize("valid", [pytest.param(True, id="valid"), pytest.param(False, id="invalid")])
def test_schema_releases_validation_buffers(
    schema_type: type[XMLSchema | RelaxNG], source: str, *, valid: bool
) -> None:
    # PyPy has no tracemalloc; its collector also uses a different reclamation schedule.
    tracing: Final[ModuleType] = pytest.importorskip("tracemalloc")
    schema: Final = schema_type(source)
    document: Final = parse_xml(
        ("<value><item>text</item></value>" if "oneOrMore" in source else "<value>text</value>")
        if valid
        else "<value>123</value>"
    )
    tracing.start()
    try:
        for _ in range(10):
            assert schema.validate(document).valid is valid
        gc.collect()
        before: Final[int] = tracing.get_traced_memory()[0]
        for _ in range(100):
            assert schema.validate(document).valid is valid
        gc.collect()
        assert tracing.get_traced_memory()[0] - before < 32_768
    finally:
        tracing.stop()
