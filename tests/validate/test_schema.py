from __future__ import annotations

import copy
import subprocess  # ruff:ignore[suspicious-subprocess-import]  # runs a fixed argv in a child interpreter, no shell
import sys
from typing import TYPE_CHECKING, Final

import pytest

from turbohtml import Document, DocumentFragment, Text, parse_xml
from turbohtml.clean import Policy, sanitize_node
from turbohtml.validate import RelaxNG, SchemaValidationError, ValidationResult, XMLSchema

if TYPE_CHECKING:
    from collections.abc import Callable

    from traced_bytes import RetainedBytes

    from turbohtml import Node


_XS: Final[str] = 'xmlns:xs="http://www.w3.org/2001/XMLSchema"'


_RELAXNG_NS: Final[str] = "http://relaxng.org/ns/structure/1.0"


_RECURSIVE_XSD: Final[str] = (
    f'<xs:schema {_XS}><xs:element name="a"><xs:complexType><xs:sequence>'
    '<xs:element ref="a" minOccurs="0"/></xs:sequence></xs:complexType></xs:element></xs:schema>'
)
_RECURSIVE_RNG: Final[str] = (
    f'<grammar xmlns="{_RELAXNG_NS}"><start><ref name="a"/></start>'
    '<define name="a"><element name="a"><optional><ref name="a"/></optional></element></define></grammar>'
)


@pytest.mark.parametrize(
    ("schema_type", "source"),
    [pytest.param("XMLSchema", _RECURSIVE_XSD, id="xsd"), pytest.param("RelaxNG", _RECURSIVE_RNG, id="relax-ng")],
)
def test_deeply_nested_instance_fits_a_small_thread_stack(schema_type: str, source: str) -> None:
    # the instance is walked with an explicit stack, so a worker thread's stack does not bound how deep the document
    # nests; a subprocess turns a crash into a failed assertion instead of a killed test run
    code = (
        "import sys, threading\nfrom turbohtml import parse_xml, validate\n"
        f"schema = validate.{schema_type}(sys.stdin.read())\n"
        "threading.stack_size(160 * 1024)\n"
        "run = lambda: print(schema.validate(parse_xml('<a>' * 350 + '</a>' * 350)).valid)\n"
        "worker = threading.Thread(target=run)\n"
        "worker.start()\nworker.join()\n"
    )
    result = subprocess.run(  # ruff:ignore[subprocess-without-shell-equals-true]  # fixed argv, input on stdin
        [sys.executable, "-c", code],
        input=source,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert (result.returncode, result.stdout) == (0, "True\n"), result.stderr


_LEVELS: Final[int] = 380  # a schema nested a few levels inside the 400-level depth cap


def _nest(open_tag: str, inner: str, close_tag: str, levels: int = _LEVELS) -> str:
    return f"{open_tag * levels}{inner}{close_tag * levels}"


def _xsd_group(index: int) -> str:
    # the chain's ten groups together nest their sequences as deep as the depth cap allows
    inner = '<xs:element name="x" type="xs:int"/>' if index == 9 else f'<xs:group ref="g{index + 1}"/>'
    return f'<xs:group name="g{index}">{_nest("<xs:sequence>", inner, "</xs:sequence>", _LEVELS // 10 - 2)}</xs:group>'


def _rng_define(index: int) -> str:
    inner = '<element name="x"><text/></element>' if index == 190 else f'<ref name="d{index + 1}"/>'
    return f'<define name="d{index}"><group>{inner}</group></define>'


def _rng_element(content: str, name: str = ' name="r"') -> str:
    return f'<element{name} xmlns="{_RELAXNG_NS}">{content}</element>'


_SIMPLE_DERIVATION: Final[str] = _nest(
    "<xs:simpleType><xs:restriction>",
    '<xs:simpleType><xs:restriction base="xs:string"><xs:pattern value="[a-z]+"/></xs:restriction></xs:simpleType>',
    '<xs:maxLength value="9"/></xs:restriction></xs:simpleType>',
    _LEVELS // 2,
)
_PATTERN_MIX: Final[str] = _nest(
    "<choice><interleave><oneOrMore><optional>",
    '<attribute name="b"/>',
    "</optional></oneOrMore></interleave></choice>",
    _LEVELS // 4,
)
_NAME_CLASS_CHOICES: Final[str] = _nest("<choice><name>a</name>", "<name>b</name><choice/>", "</choice>")
_DIV_START: Final[str] = _nest("<div>", '<start><element name="r"><ref name="d0"/></element></start>', "</div>")
_REF_CHAIN: Final[str] = "".join(_rng_define(index) for index in range(191))


@pytest.mark.parametrize(
    ("schema_type", "source"),
    [
        pytest.param(
            "XMLSchema",
            f'<xs:schema {_XS}><xs:element name="r"><xs:complexType><xs:group ref="g0"/></xs:complexType>'
            f"</xs:element>{''.join(_xsd_group(index) for index in range(10))}</xs:schema>",
            id="xsd-group-ref-chain",
        ),
        pytest.param(
            "XMLSchema",
            f'<xs:schema {_XS}><xs:element name="r">{_SIMPLE_DERIVATION}</xs:element></xs:schema>',
            id="xsd-simple-type-derivation",
        ),
        pytest.param(
            "RelaxNG",
            _rng_element(_nest('<choice><attribute name="a"/>', '<attribute name="b"/>', "</choice>")),
            id="relax-ng-attribute-choices",
        ),
        pytest.param(
            "RelaxNG", _rng_element(f"{_NAME_CLASS_CHOICES}<text/>", name=""), id="relax-ng-name-class-choices"
        ),
        pytest.param("RelaxNG", _rng_element(_PATTERN_MIX), id="relax-ng-pattern-mix"),
        pytest.param(
            "RelaxNG",
            f'<grammar xmlns="{_RELAXNG_NS}">{_DIV_START}{_REF_CHAIN}</grammar>',
            id="relax-ng-div-and-ref-chain",
        ),
    ],
)
def test_deeply_nested_schema_compiles_on_a_small_thread_stack(schema_type: str, source: str) -> None:
    # compilation walks the schema with explicit stacks, so neither its nesting nor a chain of references through it
    # costs C stack; 128 KiB is the thread stack uv's musl CPython starts threads with
    code = (
        "import sys, threading\nfrom turbohtml import validate\nsource = sys.stdin.read()\n"
        "threading.stack_size(128 * 1024)\n"
        f"run = lambda: print(type(validate.{schema_type}(source)).__name__)\n"
        "worker = threading.Thread(target=run)\n"
        "worker.start()\nworker.join()\n"
    )
    result = subprocess.run(  # ruff:ignore[subprocess-without-shell-equals-true]  # fixed argv, input on stdin
        [sys.executable, "-c", code],
        input=source,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert (result.returncode, result.stdout) == (0, f"{schema_type}\n"), result.stderr


@pytest.mark.parametrize(
    ("schema", "document", "expected"),
    [
        pytest.param(
            XMLSchema(
                f'<xs:schema {_XS}><xs:element name="a"><xs:complexType><xs:all>'
                '<xs:element ref="a" minOccurs="0"/><xs:element name="b" type="xs:int"/>'
                "</xs:all></xs:complexType></xs:element></xs:schema>"
            ),
            f"{'<a>' * 20}<b>x</b><c/>{'<b>1</b></a>' * 19}</a>",
            [
                (f"{'/a' * 20}/b", "value 'x' is not a valid int"),
                ("/a" * 20, "element 'c' is not allowed here"),
                ("/a" * 20, "element 'b' is not allowed here"),
                ("/a", "required element 'b' is missing"),
            ],
            id="xsd",
        ),
        pytest.param(
            RelaxNG(
                f'<grammar xmlns="{_RELAXNG_NS}"><start><ref name="a"/></start><define name="a"><element name="a">'
                '<optional><ref name="a"/></optional><element name="b"><text/></element></element></define></grammar>'
            ),
            f"{'<a>' * 20}{'<b>1</b></a>' * 19}</a>",
            [("/a", "content of element 'a' does not match the schema")],
            id="relax-ng",
        ),
        pytest.param(
            RelaxNG(_RECURSIVE_RNG),
            "<a><a><a><c/></a><d/></a></a>",
            [("/a/a/a/c", "element 'c' is not allowed here"), ("/a/a/d", "element 'd' is not allowed here")],
            id="relax-ng-refused-start-tag",
        ),
    ],
)
def test_errors_below_nested_elements_keep_document_order(
    schema: XMLSchema | RelaxNG, document: str, expected: list[tuple[str, str]]
) -> None:
    # an element reports what only its end can tell (a missing xs:all member, a RELAX NG content mismatch) after its
    # descendants report and under its own path, 20 levels up here; a refused start tag is reported once
    assert [(error.path, error.message) for error in schema.validate(parse_xml(document)).errors] == expected


def _xsd_content_nest(open_tag: str, close_tag: str = "</xs:sequence>") -> str:
    return (
        f'<xs:schema {_XS}><xs:element name="doc"><xs:complexType>{open_tag * _LEVELS}'
        '<xs:element name="a" type="xs:string"/>'
        f"{close_tag * _LEVELS}</xs:complexType></xs:element></xs:schema>"
    )


_UNION_MEMBERS: Final[str] = "".join(
    f'<xs:simpleType name="u{index}"><xs:union memberTypes="u{index + 1} xs:string"/></xs:simpleType>'
    for index in range(39)
)
_UNION_NEST: Final[str] = (
    f"<xs:schema {_XS}>{_UNION_MEMBERS}"
    '<xs:simpleType name="u39"><xs:restriction base="xs:string"/></xs:simpleType>'
    '<xs:element name="doc" type="u0"/></xs:schema>'
)


@pytest.mark.parametrize(
    ("source", "document", "stack_kib"),
    [
        pytest.param(_xsd_content_nest("<xs:sequence>"), "<doc><a>x</a></doc>", 128, id="sequence"),
        pytest.param(_xsd_content_nest("<xs:sequence>"), "<doc><a>x</a></doc>", 16, id="sequence-tiny-stack"),
        pytest.param(_xsd_content_nest("<xs:choice>", "</xs:choice>"), "<doc><a>x</a></doc>", 128, id="choice"),
        pytest.param(
            _xsd_content_nest('<xs:sequence minOccurs="0" maxOccurs="unbounded">'),
            "<doc><a>x</a></doc>",
            128,
            id="repeated-sequence",
        ),
        pytest.param(_UNION_NEST, "<doc>x</doc>", 16, id="union-tiny-stack"),
    ],
)
def test_deep_content_model_raises_or_validates_on_a_small_thread_stack(
    source: str, document: str, stack_kib: int
) -> None:
    # matching a content model recurses once per particle level, so on a small thread a deep enough model raises
    # RecursionError before the stack ends; the outcome is a verdict or that error, never a crashed worker
    code = (
        "import sys, threading\nfrom turbohtml import parse_xml, validate\nsource = sys.stdin.read()\n"
        f"for kib in range({stack_kib}, 129, 4):\n    try:\n        threading.stack_size(kib * 1024)\n"
        "    except ValueError:\n        continue\n    break\n"
        f"document = parse_xml({document!r})\n"
        "def run():\n    try:\n        print(validate.XMLSchema(source).validate(document).valid)\n"
        "    except RecursionError:\n        print('RecursionError')\n"
        "worker = threading.Thread(target=run)\n"
        "worker.start()\nworker.join()\n"
    )
    result = subprocess.run(  # ruff:ignore[subprocess-without-shell-equals-true]  # fixed argv, input on stdin
        [sys.executable, "-c", code],
        input=source,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout in {"True\n", "False\n", "RecursionError\n"}, result.stderr


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
    name: Final[str] = char * length
    result: Final[ValidationResult] = _item_list(schema_type, name).validate(
        parse_xml(f"<{name}><item>x</item></{name}>")
    )
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
    schema: Final[XMLSchema | RelaxNG] = _item_list(schema_type, "list")
    items: Final[list[str]] = [f"<item>x{index}</item>" for index in range(invalid)]
    document: Final[Document] = parse_xml(f"<list>{''.join(items)}</list>")
    prefix: Final[Document] = parse_xml(f"<list>{''.join(items[:collected])}</list>")
    result: Final[ValidationResult] = schema.validate(document)
    assert not result.valid
    assert not schema.is_valid(document)
    assert len(result.errors) == collected
    assert result.errors == schema.validate(prefix).errors
    with pytest.raises(SchemaValidationError) as raised:
        schema.assert_valid(document)
    assert raised.value.errors == result.errors


@pytest.mark.parametrize(
    "schema",
    [
        pytest.param(XMLSchema(f'<xs:schema {_XS}><xs:element name="entry" type="xs:string"/></xs:schema>'), id="xsd"),
        pytest.param(RelaxNG(f'<element name="entry" xmlns="{_RELAXNG_NS}"><text/></element>'), id="relaxng"),
    ],
)
@pytest.mark.parametrize(
    "duplicate",
    [
        pytest.param(copy.copy, id="copy"),
        pytest.param(copy.deepcopy, id="deepcopy"),
        pytest.param(lambda document: sanitize_node(document, Policy(tags=frozenset({"entry"}))), id="sanitize-node"),
    ],
)
def test_validate_accepts_a_document_built_by_copying(
    schema: XMLSchema | RelaxNG, duplicate: Callable[[Document], Document]
) -> None:
    # validating a Document starts at the root element of the node its tree records as the document
    document: Final[Document] = duplicate(parse_xml("<entry>x</entry>"))
    assert isinstance(document, Document)
    assert schema.validate(document).valid
    assert schema.is_valid(document)


_GOOD_XSD: Final[str] = (
    f'<xs:schema {_XS} targetNamespace="urn:first" elementFormDefault="qualified"><xs:element name="doc">'
    '<xs:complexType><xs:sequence><xs:element name="item"/></xs:sequence></xs:complexType></xs:element></xs:schema>'
)
_GOOD_RNG: Final[str] = (
    f'<element name="doc" ns="urn:first" xmlns="{_RELAXNG_NS}"><element name="item"><empty/></element></element>'
)
_BOTH_ENGINES: Final[list[pytest.ParameterSet]] = [
    pytest.param(xsd_ok, _GOOD_XSD, id="xsd"),
    pytest.param(rng_ok, _GOOD_RNG, id="relaxng"),
]
_ONE_CHAR: Final[str] = "ŷ"
_ITS_BYTES: Final[str] = "Å·"  # the UTF-8 bytes of _ONE_CHAR read as two code points
_NON_ASCII_NAMES: Final[list[pytest.ParameterSet]] = [
    pytest.param("köln", id="two-byte"),
    pytest.param("中文", id="three-byte"),
    pytest.param("\U0001d538bc", id="four-byte"),
]


@pytest.mark.parametrize("prefix", _NON_ASCII_NAMES)
@pytest.mark.parametrize(("accepts", "schema"), _BOTH_ENGINES)
def test_non_ascii_prefix_resolves_to_its_declaration(
    accepts: Callable[[str, str], bool], schema: str, prefix: str
) -> None:
    # the prefix is code points and the xmlns:prefix attribute name is UTF-8, so the two differ in length
    assert accepts(schema, f'<{prefix}:doc xmlns:{prefix}="urn:first"><{prefix}:item/></{prefix}:doc>')
    assert not accepts(schema, f'<g:doc xmlns:g="urn:first" xmlns:{prefix}="urn:second"><{prefix}:item/></g:doc>')
    assert not accepts(schema, f'<g:doc xmlns:g="urn:first"><{prefix}:item xmlns:{prefix}="urn:second"/></g:doc>')


@pytest.mark.parametrize("prefix", _NON_ASCII_NAMES)
@pytest.mark.parametrize(("accepts", "schema"), _BOTH_ENGINES)
def test_non_ascii_prefix_among_many_declarations(
    accepts: Callable[[str, str], bool], schema: str, prefix: str
) -> None:
    def document(item_ns: str) -> str:
        siblings: Final[str] = " ".join(f'xmlns:{prefix}{index}="urn:first"' for index in range(24))
        return f'<{prefix}0:doc {siblings} xmlns:{prefix}="{item_ns}"><{prefix}:item/></{prefix}0:doc>'

    assert accepts(schema, document("urn:first"))
    assert not accepts(schema, document("urn:second"))


@pytest.mark.parametrize(
    ("prefix", "other"),
    [
        pytest.param(_ITS_BYTES, _ONE_CHAR, id="code-points-equal-to-its-bytes"),
        pytest.param("ö", "õ", id="same-lead-byte"),
        pytest.param("中", "丮", id="same-leading-bytes"),
        pytest.param("ö", "öö", id="longer"),
        pytest.param("öö", "ö", id="shorter"),
        pytest.param("ab中", "abö", id="declaration-ends-inside-a-character"),
    ],
)
@pytest.mark.parametrize(("accepts", "schema"), _BOTH_ENGINES)
def test_prefix_is_not_resolved_by_the_declaration_of_another(
    accepts: Callable[[str, str], bool], schema: str, prefix: str, other: str
) -> None:
    document: Final[str] = (
        f'<g:doc xmlns:g="urn:first" xmlns:{other}="urn:first" xmlns:{prefix}="urn:second"><{prefix}:item/></g:doc>'
    )
    assert not accepts(schema, document)
    assert accepts(schema, document.replace(f'{prefix}="urn:second"', f'{prefix}="urn:first"'))


@pytest.mark.parametrize("padding", [pytest.param(0, id="few-attributes"), pytest.param(40, id="many-attributes")])
@pytest.mark.parametrize("prefix", _NON_ASCII_NAMES)
def test_schema_vocabulary_under_a_non_ascii_prefix(prefix: str, padding: int) -> None:
    # attributes ahead of the declaration move the lookup from the ancestor walk to the per-path scope
    pad: Final[str] = " ".join(f'pad{index}=""' for index in range(padding))
    xsd: Final[str] = (
        f'<{prefix}:schema {pad} xmlns:{prefix}="http://www.w3.org/2001/XMLSchema">'
        f'<{prefix}:element name="doc" type="{prefix}:int"/></{prefix}:schema>'
    )
    assert xsd_ok(xsd, "<doc>3</doc>")
    assert not xsd_ok(xsd, "<doc>three</doc>")
    rng: Final[str] = (
        f'<{prefix}:element name="doc" {pad} xmlns:{prefix}="{_RELAXNG_NS}"><{prefix}:empty/></{prefix}:element>'
    )
    assert rng_ok(rng, "<doc/>")
    assert not rng_ok(rng, "<doc>text</doc>")


@pytest.mark.parametrize("padding", [pytest.param(0, id="scanned"), pytest.param(40, id="indexed")])
def test_xsd_matches_non_ascii_attribute_names(padding: int) -> None:
    # 32 or more attributes switch both the instance and the declared side to hash tables
    names: Final[list[str]] = ["köln", "中文", "\U0001d538bc", _ITS_BYTES]
    padded: Final[list[str]] = [f"a{index}" for index in range(padding)]
    declared: Final[str] = "".join(f'<xs:attribute name="{name}" use="required"/>' for name in (*names, *padded))
    schema: Final[str] = (
        f'<xs:schema {_XS}><xs:element name="doc"><xs:complexType>{declared}</xs:complexType></xs:element></xs:schema>'
    )

    def document(*present: str) -> str:
        attributes: Final[str] = " ".join(f'{name}="1"' for name in (*present, *padded))
        return f"<doc {attributes}/>"

    assert xsd_ok(schema, document(*names))
    result: Final[ValidationResult] = XMLSchema(schema).validate(parse_xml(document(*names[:3], _ONE_CHAR)))
    assert sorted(error.message for error in result.errors) == [
        f"attribute '{_ONE_CHAR}' is not declared",
        f"required attribute '{_ITS_BYTES}' is missing",
    ]


@pytest.mark.parametrize(
    ("prefix", "other"),
    [
        pytest.param("ö", "õ", id="same-lead-byte"),
        pytest.param("ab中", "abö", id="declaration-ends-inside-a-character"),
    ],
)
def test_type_reference_resolves_past_a_declaration_with_a_shared_start(prefix: str, other: str) -> None:
    xsd: Final[str] = (
        f'<xs:schema {_XS} xmlns:{other}="urn:other" xmlns:{prefix}="urn:first" targetNamespace="urn:first" '
        'elementFormDefault="qualified"><xs:simpleType name="item"><xs:restriction base="xs:int"/></xs:simpleType>'
        f'<xs:element name="doc" nillable="false" type="{prefix}:item"/></xs:schema>'
    )
    assert xsd_ok(xsd, '<doc xmlns="urn:first">3</doc>')
    assert not xsd_ok(xsd, '<doc xmlns="urn:first">three</doc>')


def test_xsd_attribute_is_not_matched_by_a_name_ending_inside_its_last_character() -> None:
    schema: Final[str] = (
        f'<xs:schema {_XS}><xs:element name="doc"><xs:complexType><xs:attribute name="ab中"/>'
        "</xs:complexType></xs:element></xs:schema>"
    )
    assert xsd_ok(schema, '<doc ab中="1"/>')
    result: Final[ValidationResult] = XMLSchema(schema).validate(parse_xml('<doc abö="1"/>'))
    assert [error.message for error in result.errors] == ["attribute 'abö' is not declared"]
