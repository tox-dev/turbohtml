from __future__ import annotations

import gc
from typing import TYPE_CHECKING, Final

import pytest

from turbohtml import parse_xml
from turbohtml.validate import RelaxNG, XMLSchema

if TYPE_CHECKING:
    from types import ModuleType

    from turbohtml import Document


_XS: Final[str] = 'xmlns:xs="http://www.w3.org/2001/XMLSchema"'


RELAXNG_NS: Final[str] = "http://relaxng.org/ns/structure/1.0"


@pytest.mark.parametrize(
    "schema",
    [
        pytest.param(
            XMLSchema(
                f'<xs:schema {_XS}><xs:element name="a"><xs:complexType><xs:sequence>'
                '<xs:element ref="a" minOccurs="0"/></xs:sequence></xs:complexType></xs:element></xs:schema>'
            ),
            id="xsd",
        ),
        pytest.param(
            RelaxNG(
                f'<grammar xmlns="{RELAXNG_NS}"><start><ref name="a"/></start>'
                '<define name="a"><element name="a"><optional><ref name="a"/></optional></element></define></grammar>'
            ),
            id="relax-ng",
        ),
    ],
)
@pytest.mark.parametrize("method", [pytest.param("validate", id="report"), pytest.param("is_valid", id="verdict")])
def test_deep_nesting_is_rejected_before_validation(schema: XMLSchema | RelaxNG, *, method: str) -> None:
    validate = getattr(schema, method)
    assert bool(validate(parse_xml("<a>" * 20 + "</a>" * 20)))
    with pytest.raises(RecursionError, match="schema validation"):
        validate(parse_xml("<a>" * 1200 + "</a>" * 1200))


@pytest.mark.parametrize(
    ("schema_type", "source"),
    [
        pytest.param(
            XMLSchema,
            (
                f'<xs:schema {_XS}><xs:element name="a"><xs:complexType>{"<xs:sequence>" * 1200}<xs:element name="b"/>'
                f"{'</xs:sequence>' * 1200}</xs:complexType></xs:element></xs:schema>"
            ),
            id="xsd",
        ),
        pytest.param(
            RelaxNG,
            f'<grammar xmlns="{RELAXNG_NS}"><start>{"<optional>" * 1200}'
            f"<empty/>{'</optional>' * 1200}</start></grammar>",
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
    xsd_schema = f'<xs:schema {_XS}><xs:element name="v" type="xs:int"/></xs:schema>'
    assert not xsd_ok(xsd_schema, f"<v>{'9' * 200}z</v>")


def rwrap(body: str) -> str:
    return f'<element name="doc" xmlns="{RELAXNG_NS}">{body}</element>'


def rng_ok(schema: str, xml: str) -> bool:
    return RelaxNG(schema).validate(parse_xml(xml)).valid


def xsd_ok(schema: str, xml: str) -> bool:
    return XMLSchema(schema).validate(parse_xml(xml)).valid


@pytest.mark.parametrize(
    ("schema_type", "source", "valid_markup"),
    [
        pytest.param(
            XMLSchema,
            '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"><xs:element name="value"><xs:simpleType>'
            '<xs:restriction base="xs:string"><xs:pattern value="[a-z]+"/></xs:restriction>'
            "</xs:simpleType></xs:element></xs:schema>",
            "<value>text</value>",
            id="xsd-pattern",
        ),
        pytest.param(
            RelaxNG,
            '<element xmlns="http://relaxng.org/ns/structure/1.0" name="value" '
            'datatypeLibrary="http://www.w3.org/2001/XMLSchema-datatypes">'
            '<data type="string"><param name="pattern">[a-z]+</param></data></element>',
            "<value>text</value>",
            id="rng-pattern",
        ),
        pytest.param(
            RelaxNG,
            '<grammar xmlns="http://relaxng.org/ns/structure/1.0"><start><ref name="root"/></start>'
            '<define name="root"><element name="value"><oneOrMore><element name="item"><text/>'
            "</element></oneOrMore></element></define></grammar>",
            "<value><item>text</item></value>",
            id="rng-definition",
        ),
    ],
)
def test_schema_releases_validation_buffers_for_valid_documents(
    schema_type: type[XMLSchema | RelaxNG], source: str, valid_markup: str
) -> None:
    _assert_validation_buffers_released(schema_type(source), parse_xml(valid_markup), valid=True)


def _assert_validation_buffers_released(schema: XMLSchema | RelaxNG, document: Document, *, valid: bool) -> None:
    # PyPy has no tracemalloc; its collector also uses a different reclamation schedule.
    tracing: Final[ModuleType] = pytest.importorskip("tracemalloc")
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
def test_schema_releases_validation_buffers_for_invalid_documents(
    schema_type: type[XMLSchema | RelaxNG], source: str
) -> None:
    _assert_validation_buffers_released(schema_type(source), parse_xml("<value>123</value>"), valid=False)
