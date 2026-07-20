"""RELAX NG (XML syntax) validation: patterns, name classes, interleave, datatypes."""

from __future__ import annotations

import pytest

from turbohtml import parse_xml
from turbohtml.validate import RelaxNG, SchemaValidationError, ValidationResult

R = "http://relaxng.org/ns/structure/1.0"
DT = "http://www.w3.org/2001/XMLSchema-datatypes"


def check(schema: str, xml: str) -> ValidationResult:
    return RelaxNG(schema).validate(parse_xml(xml))


def wrap(body: str, *, attrs: str = "") -> str:
    return f'<element name="doc" xmlns="{R}" {attrs}>{body}</element>'


def grammar(body: str, *, attrs: str = "") -> str:
    return f'<grammar xmlns="{R}" {attrs}>{body}</grammar>'


@pytest.mark.parametrize(
    ("body", "xml", "ok"),
    [
        pytest.param("<empty/>", "<doc/>", True, id="empty-ok"),
        pytest.param("<empty/>", "<doc>x</doc>", False, id="empty-bad"),
        pytest.param("<text/>", "<doc>hello</doc>", True, id="text-ok"),
        pytest.param('<element name="a"><text/></element>', "<doc><a>x</a></doc>", True, id="single-element"),
        pytest.param('<element name="a"><text/></element>', "<doc><b>x</b></doc>", False, id="wrong-element"),
        pytest.param('<optional><element name="a"><text/></element></optional>', "<doc/>", True, id="optional-absent"),
        pytest.param(
            '<optional><element name="a"><text/></element></optional>',
            "<doc><a>x</a></doc>",
            True,
            id="optional-present",
        ),
        pytest.param(
            '<oneOrMore><element name="a"><text/></element></oneOrMore>', "<doc/>", False, id="oneOrMore-empty"
        ),
        pytest.param(
            '<oneOrMore><element name="a"><text/></element></oneOrMore>',
            "<doc><a>1</a><a>2</a></doc>",
            True,
            id="oneOrMore-many",
        ),
        pytest.param(
            '<zeroOrMore><element name="a"><text/></element></zeroOrMore>', "<doc/>", True, id="zeroOrMore-empty"
        ),
        pytest.param(
            '<group><element name="a"><text/></element><element name="b"><text/></element></group>',
            "<doc><a>1</a><b>2</b></doc>",
            True,
            id="group",
        ),
        pytest.param(
            '<choice><element name="a"><text/></element><element name="b"><text/></element></choice>',
            "<doc><b>2</b></doc>",
            True,
            id="choice",
        ),
        pytest.param("<notAllowed/>", "<doc/>", False, id="notAllowed"),
    ],
)
def test_pattern(body: str, xml: str, ok: bool) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]  # a pytest parametrize value, not a boolean-trap call site
    assert check(wrap(body), xml).valid is ok


def test_interleave_any_order() -> None:
    schema = wrap('<interleave><element name="a"><text/></element><element name="b"><text/></element></interleave>')
    assert check(schema, "<doc><a>1</a><b>2</b></doc>").valid
    assert check(schema, "<doc><b>2</b><a>1</a></doc>").valid
    assert not check(schema, "<doc><a>1</a></doc>").valid
    assert not check(schema, "<doc><a>1</a><b>2</b><a>3</a></doc>").valid


def test_interleave_with_text_via_mixed() -> None:
    schema = wrap('<mixed><element name="a"><text/></element></mixed>')
    assert check(schema, "<doc>before <a>x</a> after</doc>").valid


def test_attribute() -> None:
    schema = wrap('<attribute name="id"><text/></attribute><text/>')
    assert check(schema, '<doc id="1">x</doc>').valid
    assert not check(schema, "<doc>x</doc>").valid  # missing required attribute


def test_optional_attribute() -> None:
    schema = wrap('<optional><attribute name="id"><text/></attribute></optional>')
    assert check(schema, "<doc/>").valid
    assert check(schema, '<doc id="1"/>').valid


def test_value_pattern() -> None:
    schema = wrap('<element name="s"><value>on</value></element>')
    assert check(schema, "<doc><s>on</s></doc>").valid
    assert not check(schema, "<doc><s>off</s></doc>").valid


def test_data_with_xsd_library() -> None:
    schema = grammar('<start><element name="n"><data type="int"/></element></start>', attrs=f'datatypeLibrary="{DT}"')
    assert check(schema, "<n>42</n>").valid
    assert not check(schema, "<n>notint</n>").valid


def test_data_param_facets() -> None:
    schema = grammar(
        '<start><element name="s"><data type="string">'
        '<param name="minLength">2</param><param name="maxLength">4</param>'
        '<param name="pattern">[a-z]+</param>'
        "</data></element></start>",
        attrs=f'datatypeLibrary="{DT}"',
    )
    assert check(schema, "<s>abc</s>").valid
    assert not check(schema, "<s>a</s>").valid  # too short
    assert not check(schema, "<s>abcde</s>").valid  # too long
    assert not check(schema, "<s>AB</s>").valid  # pattern


def test_list_pattern() -> None:
    schema = grammar(
        '<start><element name="nums"><list><oneOrMore><data type="int"/></oneOrMore></list></element></start>',
        attrs=f'datatypeLibrary="{DT}"',
    )
    assert check(schema, "<nums>1 2 3</nums>").valid
    assert not check(schema, "<nums>1 x 3</nums>").valid


def test_default_datatype_token() -> None:
    schema = wrap('<element name="t"><data type="token"/></element>')
    assert check(schema, "<doc><t>  spaced  </t></doc>").valid


def test_define_ref_and_recursion() -> None:
    schema = grammar(
        '<start><ref name="node"/></start>'
        '<define name="node"><element name="node">'
        '<optional><ref name="node"/></optional>'
        "</element></define>"
    )
    assert check(schema, "<node><node><node/></node></node>").valid
    assert not check(schema, "<node><other/></node>").valid


def test_combine_choice() -> None:
    schema = grammar(
        '<start><element name="r"><ref name="opt"/></element></start>'
        '<define name="opt" combine="choice"><element name="a"><text/></element></define>'
        '<define name="opt" combine="choice"><element name="b"><text/></element></define>'
    )
    assert check(schema, "<r><a>x</a></r>").valid
    assert check(schema, "<r><b>x</b></r>").valid


def test_combine_interleave() -> None:
    schema = grammar(
        '<start><element name="r"><ref name="parts"/></element></start>'
        '<define name="parts" combine="interleave"><element name="a"><text/></element></define>'
        '<define name="parts" combine="interleave"><element name="b"><text/></element></define>'
    )
    assert check(schema, "<r><b>2</b><a>1</a></r>").valid
    assert not check(schema, "<r><a>1</a></r>").valid


@pytest.mark.parametrize(
    ("nameclass", "xml", "ok"),
    [
        pytest.param("<anyName/>", "<doc><anything>x</anything></doc>", True, id="anyName"),
        pytest.param(
            "<anyName><except><name>skip</name></except></anyName>",
            "<doc><ok>x</ok></doc>",
            True,
            id="anyName-except-ok",
        ),
        pytest.param(
            "<anyName><except><name>skip</name></except></anyName>",
            "<doc><skip>x</skip></doc>",
            False,
            id="anyName-except-hit",
        ),
        pytest.param("<choice><name>a</name><name>b</name></choice>", "<doc><b>x</b></doc>", True, id="name-choice"),
    ],
)
def test_name_classes(nameclass: str, xml: str, ok: bool) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]  # a pytest parametrize value, not a boolean-trap call site
    schema = wrap(f"<oneOrMore><element>{nameclass}<text/></element></oneOrMore>")
    assert check(schema, xml).valid is ok


def test_nsname() -> None:
    schema = wrap('<oneOrMore><element><nsName ns="urn:x"/><text/></element></oneOrMore>', attrs='xmlns:p="ignored"')
    assert check(schema, '<doc xmlns:p="urn:x"><p:a>1</p:a><p:b>2</p:b></doc>').valid
    assert not check(schema, "<doc><a>1</a></doc>").valid


def test_named_namespace_via_ns_attr() -> None:
    schema = grammar(
        '<start><element name="a" ns="urn:x"><text/></element></start>',
    )
    assert check(schema, '<a xmlns="urn:x">v</a>').valid
    assert not check(schema, "<a>v</a>").valid


def test_prefixed_name_in_schema() -> None:
    schema = grammar(
        '<start><element name="p:a" xmlns:p="urn:x"><text/></element></start>',
    )
    assert check(schema, '<a xmlns="urn:x">v</a>').valid


def test_direct_element_root_short_form() -> None:
    assert check(wrap("<text/>"), "<doc>hi</doc>").valid


def test_missing_start_raises() -> None:
    with pytest.raises(ValueError, match="no start"):
        RelaxNG(grammar('<define name="x"><empty/></define>'))


def test_ref_to_unknown_define_never_matches() -> None:
    schema = grammar('<start><element name="r"><ref name="missing"/></element></start>')
    assert not check(schema, "<r/>").valid


def test_malformed_schema_raises() -> None:
    with pytest.raises(ValueError, match="malformed schema"):
        RelaxNG(f'<grammar xmlns="{R}"><start></grammar>')


def test_empty_grammar_raises() -> None:
    with pytest.raises(ValueError, match="no start"):
        RelaxNG(grammar(""))


def test_result_bool_and_assert_valid() -> None:
    schema = wrap("<text/>")
    assert RelaxNG(schema).is_valid(parse_xml("<doc>x</doc>"))
    RelaxNG(schema).assert_valid(parse_xml("<doc>x</doc>"))
    with pytest.raises(SchemaValidationError):
        RelaxNG(wrap('<element name="a"><text/></element>')).assert_valid(parse_xml("<doc/>"))


def test_error_location_localized() -> None:
    schema = wrap('<element name="a"><element name="b"><text/></element></element>')
    result = check(schema, "<doc><a><wrong>x</wrong></a></doc>")
    assert not result.valid
    assert result.errors[0].path == "/doc/a/wrong"


def test_schema_from_parsed_document() -> None:
    schema_doc = parse_xml(wrap("<text/>"))
    assert RelaxNG(schema_doc).is_valid(parse_xml("<doc>x</doc>"))
