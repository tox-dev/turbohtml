from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest

from turbohtml import DocumentFragment, Text, parse_xml
from turbohtml.validate import RelaxNG, SchemaValidationError, XMLSchema

if TYPE_CHECKING:
    from collections.abc import Callable

    from traced_bytes import RetainedBytes

    from turbohtml import Document, Node


_XS: Final[str] = 'xmlns:xs="http://www.w3.org/2001/XMLSchema"'


_RELAXNG_NS: Final[str] = "http://relaxng.org/ns/structure/1.0"


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
                f'<grammar xmlns="{_RELAXNG_NS}"><start><ref name="a"/></start>'
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
            f'<grammar xmlns="{_RELAXNG_NS}"><start>{"<optional>" * 1200}'
            f"<empty/>{'</optional>' * 1200}</start></grammar>",
            id="relax-ng",
        ),
    ],
)
def test_deep_schema_is_rejected_before_compilation(schema_type: type[XMLSchema | RelaxNG], source: str) -> None:
    with pytest.raises(RecursionError, match="schema compilation"):
        schema_type(source)


@pytest.mark.parametrize(
    "schema",
    [
        pytest.param(XMLSchema(f'<xs:schema {_XS}><xs:element name="r"/></xs:schema>'), id="xsd"),
        pytest.param(RelaxNG(f'<element xmlns="{_RELAXNG_NS}"><anyName/><text/></element>'), id="relax-ng"),
    ],
)
@pytest.mark.parametrize(
    "node",
    [
        # a parsed text run stays a source offset until it is read; one with a reference owns its buffer
        pytest.param(lambda: parse_xml("<r>x</r>").children[0].children[0], id="text-span"),
        pytest.param(lambda: parse_xml("<r>&#114;</r>").children[0].children[0], id="text-decoded"),
        pytest.param(lambda: Text("r"), id="text-built"),
        pytest.param(lambda: parse_xml("<r><![CDATA[r]]></r>").children[0].children[0], id="cdata"),
        pytest.param(lambda: parse_xml("<r><!--r--></r>").children[0].children[0], id="comment"),
        pytest.param(lambda: parse_xml("<r><?r x?></r>").children[0].children[0], id="processing-instruction"),
        pytest.param(lambda: parse_xml("<!DOCTYPE r><r/>").children[0], id="doctype"),
        pytest.param(DocumentFragment, id="fragment"),
    ],
)
@pytest.mark.parametrize("method", [pytest.param("validate", id="report"), pytest.param("is_valid", id="verdict")])
def test_validate_rejects_a_node_that_is_not_an_element(
    schema: XMLSchema | RelaxNG, node: Callable[[], Node], *, method: str
) -> None:
    validate = getattr(schema, method)
    with pytest.raises(TypeError, match="requires a Document or an Element"):
        validate(node())


def test_validate_each_child_rejects_the_text_between_elements() -> None:
    schema = XMLSchema(f'<xs:schema {_XS}><xs:element name="row" type="xs:string"/></xs:schema>')
    first, line_break, second = parse_xml("<rows><row>1</row>\n<row>2</row></rows>").children[0].children
    assert schema.is_valid(first)
    with pytest.raises(TypeError, match="requires a Document or an Element"):
        schema.is_valid(line_break)
    assert schema.is_valid(second)


def test_long_names_and_values_hit_buffer_bounds() -> None:
    # a >256-char attribute name exercises the decode-buffer cap
    long_attr = "x" * 300
    rng_schema = rwrap(f'<attribute name="{long_attr}"><text/></attribute><text/>')
    assert rng_ok(rng_schema, f'<doc {long_attr}="1">body</doc>')
    # a >128-char value truncates in the datatype error message buffer
    xsd_schema = f'<xs:schema {_XS}><xs:element name="v" type="xs:int"/></xs:schema>'
    assert not xsd_ok(xsd_schema, f"<v>{'9' * 200}z</v>")


def rwrap(body: str) -> str:
    return f'<element name="doc" xmlns="{_RELAXNG_NS}">{body}</element>'


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
    schema_type: type[XMLSchema | RelaxNG], source: str, valid_markup: str, retained_bytes: RetainedBytes
) -> None:
    _assert_validation_buffers_released(
        schema_type(source), parse_xml(valid_markup), valid=True, retained_bytes=retained_bytes
    )


def _assert_validation_buffers_released(
    schema: XMLSchema | RelaxNG, document: Document, *, valid: bool, retained_bytes: RetainedBytes
) -> None:
    for _ in range(10):
        assert schema.validate(document).valid is valid
    before: Final[int] = retained_bytes()
    for _ in range(100):
        assert schema.validate(document).valid is valid
    assert retained_bytes() - before < 32_768


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
    schema_type: type[XMLSchema | RelaxNG], source: str, retained_bytes: RetainedBytes
) -> None:
    _assert_validation_buffers_released(
        schema_type(source), parse_xml("<value>123</value>"), valid=False, retained_bytes=retained_bytes
    )


_NAME_LIMIT: Final[int] = 64
_ERROR_LIMIT: Final[int] = 1_000
_ELLIPSIS: Final[str] = "\N{HORIZONTAL ELLIPSIS}"


@pytest.mark.parametrize("schema_type", [pytest.param(XMLSchema, id="xsd"), pytest.param(RelaxNG, id="relax-ng")])
@pytest.mark.parametrize(
    ("char", "length", "kept", "suffix"),
    [
        pytest.param("n", _NAME_LIMIT, _NAME_LIMIT, "", id="at-limit"),
        pytest.param("n", _NAME_LIMIT + 1, _NAME_LIMIT, _ELLIPSIS, id="past-limit"),
        pytest.param("n", 200, _NAME_LIMIT, _ELLIPSIS, id="long"),
        pytest.param("\U0001d538", _NAME_LIMIT + 1, _NAME_LIMIT, _ELLIPSIS, id="four-byte"),
    ],
)
def test_error_path_clips_long_element_names(
    schema_type: type[XMLSchema | RelaxNG], char: str, length: int, kept: int, suffix: str
) -> None:
    name: Final = char * length
    result: Final = _item_list(schema_type, name).validate(parse_xml(f"<{name}><item>x</item></{name}>"))
    assert [error.path for error in result.errors] == [f"/{char * kept}{suffix}/item"]


def _item_list(schema_type: type[XMLSchema | RelaxNG], name: str) -> XMLSchema | RelaxNG:
    if schema_type is XMLSchema:
        return XMLSchema(
            f'<xs:schema {_XS}><xs:element name="{name}"><xs:complexType><xs:sequence>'
            '<xs:element name="item" type="xs:int" minOccurs="0" maxOccurs="unbounded"/>'
            "</xs:sequence></xs:complexType></xs:element></xs:schema>"
        )
    return RelaxNG(
        f'<element name="{name}" xmlns="{_RELAXNG_NS}" datatypeLibrary="http://www.w3.org/2001/XMLSchema-datatypes">'
        '<zeroOrMore><element name="item"><data type="int"/></element></zeroOrMore></element>'
    )


@pytest.mark.parametrize("schema_type", [pytest.param(XMLSchema, id="xsd"), pytest.param(RelaxNG, id="relax-ng")])
@pytest.mark.parametrize(
    ("invalid", "collected"),
    [
        pytest.param(_ERROR_LIMIT - 1, _ERROR_LIMIT - 1, id="below-limit"),
        pytest.param(_ERROR_LIMIT, _ERROR_LIMIT, id="at-limit"),
        pytest.param(_ERROR_LIMIT + 1, _ERROR_LIMIT, id="past-limit"),
        pytest.param(_ERROR_LIMIT * 5 + 7, _ERROR_LIMIT, id="far-past-limit"),
    ],
)
def test_validate_collects_errors_up_to_the_limit(
    schema_type: type[XMLSchema | RelaxNG], invalid: int, collected: int
) -> None:
    schema: Final = _item_list(schema_type, "list")
    items: Final = [f"<item>x{index}</item>" for index in range(invalid)]
    document: Final = parse_xml(f"<list>{''.join(items)}</list>")
    prefix: Final = parse_xml(f"<list>{''.join(items[:collected])}</list>")
    result: Final = schema.validate(document)
    assert not result.valid
    assert not schema.is_valid(document)
    assert len(result.errors) == collected
    assert result.errors == schema.validate(prefix).errors
    with pytest.raises(SchemaValidationError) as raised:
        schema.assert_valid(document)
    assert raised.value.errors == result.errors
