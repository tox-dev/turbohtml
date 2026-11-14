from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import TYPE_CHECKING, Final, cast

import pytest
from bench.operations import INPUTS

from turbohtml import parse_xml
from turbohtml.validate import RelaxNG, SchemaValidationError, ValidationResult

if TYPE_CHECKING:
    from collections.abc import Callable

    from work_steps import WorkSteps

    from turbohtml import Document

_RELAXNG_NS: Final[str] = "http://relaxng.org/ns/structure/1.0"
_DT: Final[str] = "http://www.w3.org/2001/XMLSchema-datatypes"


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


def check(schema: str, xml: str) -> ValidationResult:
    return RelaxNG(schema).validate(parse_xml(xml))


def wrap(body: str, *, attrs: str = "") -> str:
    return f'<element name="doc" xmlns="{_RELAXNG_NS}" {attrs}>{body}</element>'


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


@pytest.mark.parametrize(
    ("value", "text", "ok"),
    [
        pytest.param('<value type="string">a b</value>', "a b", True, id="string-equal"),
        pytest.param('<value type="string">a b</value>', "a  b", False, id="string-differs"),
        pytest.param(f'<value type="normalizedString" datatypeLibrary="{_DT}">a b</value>', "a\tb", True, id="replace"),
        pytest.param(
            f'<value type="normalizedString" datatypeLibrary="{_DT}">a b</value>', "a\tc", False, id="replace-bad"
        ),
        pytest.param(
            f'<value type="normalizedString" datatypeLibrary="{_DT}">a b</value>', "a b ", False, id="replace-long"
        ),
        pytest.param("<value> a \n b </value>", "\t a  b\n", True, id="collapse-both-sides"),
        pytest.param("<value>a b</value>", "ab", False, id="collapse-missing-space"),
        pytest.param("<value>ab</value>", "a b", False, id="collapse-extra-space"),
        pytest.param("<value>a</value>", "a b", False, id="collapse-longer"),
        pytest.param("<value>ab</value>", "a", False, id="collapse-shorter"),
    ],
)
def test_value_whitespace(value: str, text: str, ok: bool) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]  # a pytest parametrize value, not a boolean-trap call site
    assert check(wrap(f'<element name="s">{value}</element>'), f"<doc><s>{text}</s></doc>").valid is ok


def test_data_with_xsd_library() -> None:
    schema = grammar('<start><element name="n"><data type="int"/></element></start>', attrs=f'datatypeLibrary="{_DT}"')
    assert check(schema, "<n>42</n>").valid
    assert not check(schema, "<n>notint</n>").valid


def grammar(body: str, *, attrs: str = "") -> str:
    return f'<grammar xmlns="{_RELAXNG_NS}" {attrs}>{body}</grammar>'


def test_data_param_facets() -> None:
    schema = grammar(
        '<start><element name="s"><data type="string">'
        '<param name="minLength">2</param><param name="maxLength">4</param>'
        '<param name="pattern">[a-z]+</param>'
        "</data></element></start>",
        attrs=f'datatypeLibrary="{_DT}"',
    )
    assert check(schema, "<s>abc</s>").valid
    assert not check(schema, "<s>a</s>").valid  # too short
    assert not check(schema, "<s>abcde</s>").valid  # too long
    assert not check(schema, "<s>AB</s>").valid  # pattern


def test_data_param_pattern_too_complex_raises() -> None:
    pattern = f"{'(' * 50000}a{')' * 50000}"
    schema = grammar(
        f'<start><element name="s"><data type="string"><param name="pattern">{pattern}</param></data>'
        "</element></start>",
        attrs=f'datatypeLibrary="{_DT}"',
    )
    with pytest.raises(ValueError, match="nests groups deeper than 250 levels at offset 250"):
        RelaxNG(schema)


def test_list_pattern() -> None:
    schema = grammar(
        '<start><element name="nums"><list><oneOrMore><data type="int"/></oneOrMore></list></element></start>',
        attrs=f'datatypeLibrary="{_DT}"',
    )
    assert check(schema, "<nums>1 2 3</nums>").valid
    assert not check(schema, "<nums>1 x 3</nums>").valid


_NESTED_VALUE: Final[str] = (
    '<start><element name="doc"><list><oneOrMore><list><oneOrMore><value>x</value></oneOrMore></list>'
    "</oneOrMore></list></element></start>"
)
_NESTED_REF: Final[str] = (
    '<start><element name="doc"><list><oneOrMore><list><ref name="t"/></list></oneOrMore></list></element></start>'
    '<define name="t"><choice><value>x</value><value>y</value></choice></define>'
)


@pytest.mark.parametrize(
    ("body", "xml", "ok"),
    [
        pytest.param(_NESTED_VALUE, "<doc>x x</doc>", True, id="value-all-x"),
        pytest.param(_NESTED_VALUE, "<doc>x y</doc>", False, id="value-one-bad"),
        pytest.param(_NESTED_VALUE, "<doc/>", False, id="value-empty"),
        pytest.param(_NESTED_REF, "<doc>x y</doc>", True, id="ref-choice"),
        pytest.param(_NESTED_REF, "<doc>x z</doc>", False, id="ref-outside-choice"),
    ],
)
def test_nested_list(body: str, xml: str, ok: bool) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]  # a pytest parametrize value, not a boolean-trap call site
    assert check(grammar(body), xml).valid is ok


def test_nested_list_past_free_steps() -> None:
    tokens = " ".join(["x"] * 400)
    assert check(grammar(_NESTED_VALUE), f"<doc>{tokens}</doc>").valid
    assert not check(grammar(_NESTED_VALUE), f"<doc>{tokens} y</doc>").valid


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


def test_ref_to_unknown_define_raises() -> None:
    schema = grammar('<start><element name="r"><ref name="missing"/></element></start>')
    with pytest.raises(ValueError, match="has no matching define"):
        RelaxNG(schema)


def test_malformed_schema_raises() -> None:
    with pytest.raises(ValueError, match="malformed schema"):
        RelaxNG(f'<grammar xmlns="{_RELAXNG_NS}"><start></grammar>')


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


def test_prefix_resolution_skips_a_decoy_attribute() -> None:
    # the attribute is exactly as long as "xmlns:" plus the prefix but is not a namespace declaration, so the
    # resolver's name comparison has to reject it rather than matching on length alone
    schema = '<x:element xmlnsyy="decoy" xmlns:x="http://relaxng.org/ns/structure/1.0" name="r"><x:text/></x:element>'
    assert RelaxNG(schema).validate(parse_xml("<r>hi</r>")).valid


def rwrap(body: str) -> str:
    return f'<element name="doc" xmlns="{_RELAXNG_NS}">{body}</element>'


def rgrammar(body: str, attrs: str = "") -> str:
    return f'<grammar xmlns="{_RELAXNG_NS}" {attrs}>{body}</grammar>'


def test_rng_group_with_empty_second() -> None:
    assert rng_ok(rwrap("<group><text/><empty/></group>"), "<doc>x</doc>")


def rng_ok(schema: str, xml: str) -> bool:
    return RelaxNG(schema).validate(parse_xml(xml)).valid


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
    numbers = range(21)
    defines = "".join(
        f'<define name="d{number}"><element name="e{number}"><text/></element></define>' for number in numbers
    )
    refs = "".join(f'<ref name="d{number}"/>' for number in numbers)
    body = "".join(f"<e{number}>x</e{number}>" for number in numbers)
    schema = rgrammar(f'<start><element name="r"><group>{refs}</group></element></start>{defines}')
    assert rng_ok(schema, f"<r>{body}</r>")


def test_rng_many_defines_missing_ref() -> None:
    defines = "".join(f'<define name="d{number}"><empty/></define>' for number in (*range(10), 19, 20))
    with pytest.raises(ValueError, match="has no matching define"):
        RelaxNG(rgrammar(f'<start><ref name="missing"/></start>{defines}'))


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


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("café", id="two-byte"),
        pytest.param("中文", id="three-byte"),
        pytest.param("𝔸bc", id="four-byte"),  # ruff:ignore[ambiguous-unicode-character-string]  # sample text
    ],
)
def test_rng_non_ascii_attribute_name(name: str) -> None:
    schema = rwrap(f'<attribute name="{name}"><text/></attribute><text/>')
    assert rng_ok(schema, f'<doc {name}="1">x</doc>')


def test_rng_xml_prefixed_name() -> None:
    schema = f'<grammar xmlns="{_RELAXNG_NS}"><start><element name="xml:space"><text/></element></start></grammar>'
    assert rng_ok(schema, "<xml:space>preserve</xml:space>")


def test_rng_element_whitespace_before_nameclass() -> None:
    schema = (
        f'<grammar xmlns="{_RELAXNG_NS}"><start>\n  <element>\n    <anyName/>\n'
        f"    <text/>\n  </element>\n</start></grammar>"
    )
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


@pytest.mark.parametrize(
    ("schema", "message"),
    [
        pytest.param(
            rgrammar(
                '<start><element name="r"><ref name="x"/></element></start>'
                '<define name="x"><choice><empty/>'
                '<group><attribute name="a"><text/></attribute><ref name="x"/></group>'
                "</choice></define>"
            ),
            "define 'x' references itself with no element in between",
            id="4.19-recursion-through-attribute",
        ),
        pytest.param(
            rgrammar(
                '<start><element name="r"><ref name="x"/></element></start>'
                '<define name="x"><choice><empty/><group><ref name="x"/><text/></group></choice></define>'
            ),
            "define 'x' references itself with no element in between",
            id="4.19-recursion-before-text",
        ),
        pytest.param(
            rgrammar('<start><ref name="a"/></start><define name="a"><ref name="a"/></define>'),
            "define 'a' references itself with no element in between",
            id="4.19-self-reference",
        ),
        pytest.param(
            rgrammar(
                '<start><ref name="a"/></start>'
                '<define name="a"><ref name="b"/></define><define name="b"><ref name="a"/></define>'
            ),
            "references itself with no element in between",
            id="4.19-mutual-reference",
        ),
        pytest.param(
            rgrammar('<start><ref/></start><define name="x"><empty/></define>'),
            "<ref> is missing the required name attribute",
            id="4.10-nameless-ref-in-grammar",
        ),
        pytest.param(
            rwrap("<ref/>"), "<ref> is missing the required name attribute", id="4.10-nameless-ref-short-form"
        ),
        pytest.param(
            rgrammar(
                '<start><element name="r"><interleave>'
                '<element name="e"><empty/></element><element name="e"><empty/></element>'
                "</interleave></element></start>"
            ),
            "same element name in more than one branch",
            id="7.4-interleave-conflict-in-grammar",
        ),
        pytest.param(
            rwrap(f"""<interleave>{'<optional><element name="e"><empty/></element></optional>' * 3}</interleave>"""),
            "same element name in more than one branch",
            id="7.4-overlapping-interleave",
        ),
        pytest.param(
            rwrap("<interleave><text/><group><text/></group></interleave>"),
            "text in more than one branch",
            id="7.4-interleave-double-text",
        ),
        pytest.param(
            rwrap(
                "<interleave>"
                '<element name="a"><interleave>'
                '<element name="e"><empty/></element><element name="e"><empty/></element>'
                "</interleave></element>"
                '<element name="b"><empty/></element>'
                "</interleave>"
            ),
            "same element name in more than one branch",
            id="7.4-conflict-inside-a-valid-interleave-branch",
        ),
        pytest.param(
            rwrap(
                '<element name="x"><interleave>'
                '<element name="e"><empty/></element><element name="e"><empty/></element>'
                "</interleave></element>"
            ),
            "same element name in more than one branch",
            id="7.4-conflict-nested-in-an-element",
        ),
        pytest.param(
            rwrap('<interleave><element><anyName/><empty/></element><element name="b"><empty/></element></interleave>'),
            "same element name in more than one branch",
            id="7.4-wildcard-branch-holds-a-name",
        ),
        pytest.param(
            rwrap(f"<interleave>{'<optional><element><anyName/><empty/></element></optional>' * 14}</interleave>"),
            "same element name in more than one branch",
            id="7.4-wildcard-branches",
        ),
        pytest.param(
            rwrap(
                '<interleave><element name="a" ns="urn:x"><empty/></element>'
                '<element><nsName ns="urn:x"/><empty/></element></interleave>'
            ),
            "same element name in more than one branch",
            id="7.4-namespace-branch-holds-a-name",
        ),
        pytest.param(
            rwrap(
                '<interleave><element><nsName ns="urn:x"><except><name>b</name></except></nsName><empty/></element>'
                '<element><nsName ns="urn:x"/><empty/></element></interleave>'
            ),
            "same element name in more than one branch",
            id="7.4-namespace-branches-share-an-unnamed-name",
        ),
        pytest.param(
            rwrap(
                "<interleave><element><anyName><except><name>a</name></except></anyName><empty/></element>"
                '<element><nsName ns="urn:x"/><empty/></element></interleave>'
            ),
            "same element name in more than one branch",
            id="7.4-wildcard-except-still-holds-a-namespace",
        ),
        pytest.param(
            rwrap(
                "<interleave><element><choice><name>a</name><name>b</name></choice><empty/></element>"
                "<element><choice><name>c</name><name>b</name></choice><empty/></element></interleave>"
            ),
            "same element name in more than one branch",
            id="7.4-name-choices-share-a-name",
        ),
        pytest.param(
            rgrammar(
                "".join((
                    '<start><element name="r"><interleave>',
                    *(f'<ref name="d{index}"/>' for index in range(14)),
                    "</interleave></element></start>",
                    *(
                        f'<define name="d{index}"><optional><element name="a"><empty/></element></optional></define>'
                        for index in range(14)
                    ),
                ))
            ),
            "same element name in more than one branch",
            id="7.4-conflict-through-refs",
        ),
        pytest.param(
            rgrammar(
                '<start><element name="r"><interleave>'
                '<ref name="outer"/><element name="z"><empty/></element>'
                "</interleave></element></start>"
                '<define name="outer"><optional><ref name="inner"/></optional></define>'
                '<define name="inner"><element name="a"><empty/></element></define>'
                '<define name="inner" combine="choice"><element name="z"><empty/></element></define>'
            ),
            "same element name in more than one branch",
            id="7.4-conflict-through-a-ref-chain",
        ),
        pytest.param(
            rgrammar(
                '<start><element name="r"><interleave><ref name="t"/><text/></interleave></element></start>'
                '<define name="t"><text/></define>'
            ),
            "text in more than one branch",
            id="7.4-double-text-through-a-ref",
        ),
        pytest.param(
            rwrap(f"<interleave>{'<optional><mixed><empty/></mixed></optional>' * 2}</interleave>"),
            "text in more than one branch",
            id="7.4-double-text-in-mixed",
        ),
        pytest.param(
            rgrammar(
                '<start><element name="r"><ref name="c"/></element></start>'
                f"""{'<define name="c" combine="interleave"><optional><element name="a"/></optional></define>' * 14}"""
            ),
            "same element name in more than one branch",
            id="7.4-conflict-in-combine-interleave",
        ),
        pytest.param(
            rgrammar(
                '<start><element name="r"><ref name="c"/></element></start>'
                '<define name="c"><text/></define>'
                '<define name="c" combine="interleave"><optional><text/></optional></define>'
            ),
            "text in more than one branch",
            id="7.4-double-text-in-combine-interleave",
        ),
        pytest.param(
            rgrammar('<start combine="interleave"><element name="r"><empty/></element></start>' * 2),
            "same element name in more than one branch",
            id="7.4-conflict-in-combined-start",
        ),
        pytest.param(
            rgrammar(
                '<start><element name="r"><interleave>'
                '<ref name="x"/><element name="b"><empty/></element>'
                "</interleave></element></start>"
                '<define name="x"><ref name="x"/></define>'
            ),
            "define 'x' references itself with no element in between",
            id="4.19-self-reference-in-an-interleave-branch",
        ),
        pytest.param(
            rwrap("<interleave><ref/><text/></interleave>"),
            "<ref> is missing the required name attribute",
            id="4.10-nameless-ref-in-an-interleave-branch",
        ),
        pytest.param(
            rwrap('<interleave><ref name="missing"/><text/></interleave>'),
            "<ref> has no matching define",
            id="undefined-ref-in-an-interleave-branch",
        ),
        pytest.param(
            rwrap(
                "<interleave><element><anyName/><empty/></element>"
                "<element><choice><name>a</name><name>b</name></choice><empty/></element></interleave>"
            ),
            "same element name in more than one branch",
            id="7.4-any-branch-holds-a-choice-name",
        ),
        pytest.param(
            rwrap(
                '<interleave><element><choice><name>a</name><nsName ns="urn:x"/></choice><empty/></element>'
                '<element><nsName ns="urn:x"/><empty/></element></interleave>'
            ),
            "same element name in more than one branch",
            id="7.4-wildcard-inside-a-choice-overlaps",
        ),
        pytest.param(
            rwrap(
                "<interleave><element><choice><name>a</name><name>b</name></choice><empty/></element>"
                "<element><anyName/><empty/></element></interleave>"
            ),
            "same element name in more than one branch",
            id="7.4-choice-branch-held-by-a-later-any-branch",
        ),
        pytest.param(
            rgrammar(
                '<start><element name="r"><interleave><ref name="p"/><ref name="p"/></interleave></element></start>'
                '<define name="p"><element name="a"><empty/></element></define>'
            ),
            "same element name in more than one branch",
            id="7.4-conflict-through-a-shared-define",
        ),
        pytest.param(
            rgrammar(
                '<start><element name="r"><ref name="d0"/></element></start>'
                '<define name="d0"><interleave><element name="x"><empty/></element>'
                '<ref name="d1"/></interleave></define>'
                '<define name="d1"><interleave><element name="y"><empty/></element>'
                '<ref name="d2"/></interleave></define>'
                '<define name="d2"><element name="x"><empty/></element></define>'
            ),
            "same element name in more than one branch",
            id="7.4-conflict-at-the-end-of-a-ref-chain",
        ),
    ],
)
def test_rng_forbidden_grammar_rejected_at_compile(schema: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        RelaxNG(schema)


def _except_against_group(count: int) -> str:
    return rgrammar(
        f'<start><element name="r"><interleave>{_except_group_branches(count)}</interleave></element></start>'
    )


def _adversarial_interleaves(names: int) -> str:
    left = "".join(f'<element name="p{index}"><empty/></element>' for index in range(names))
    right = "".join(f'<element name="q{index}"><empty/></element>' for index in range(names))
    count = 200
    branches = "".join(f'<interleave><ref name="p"/><ref name="v{index}"/></interleave>' for index in range(count))
    variants = "".join(
        f'<define name="v{index}"><interleave><element name="s{index}"><empty/></element>'
        f'<ref name="q"/></interleave></define>'
        for index in range(count)
    )
    return rgrammar(
        f'<start><element name="r"><group>{branches}</group></element></start>'
        f'<define name="p"><group>{left}</group></define><define name="q"><group>{right}</group></define>'
        f"{variants}"
    )


def _except_group_branches(count: int) -> str:
    names = "".join(f"<name>q{index}</name>" for index in range(count))
    group = "".join(f'<element name="q{index}"><empty/></element>' for index in range(count))
    return f"<element><anyName><except>{names}</except></anyName><empty/></element><group>{group}</group>"


def _nested_interleave_budget(count: int) -> str:
    return rgrammar(
        '<start><element name="r"><interleave><interleave>'
        f"{_except_group_branches(count)}"
        '</interleave><element name="z"><empty/></element></interleave></element></start>'
    )


def _define_interleave_budget(count: int) -> str:
    return rgrammar(
        '<start><element name="r"><interleave><ref name="d"/><element name="z"><empty/></element>'
        "</interleave></element></start>"
        f'<define name="d"><interleave>{_except_group_branches(count)}</interleave></define>'
    )


def _combine_part_budget(count: int) -> str:
    names = "".join(f"<name>r{index}</name>" for index in range(count))
    group = "".join(f'<element name="r{index}"><empty/></element>' for index in range(count))
    return rgrammar(
        '<start><element name="r"><ref name="c"/></element></start>'
        f'<define name="c"><group>{group}</group></define>'
        f'<define name="c" combine="interleave"><element><anyName><except>{names}</except></anyName>'
        "<empty/></element></define>"
    )


def _superset_unions(count: int) -> str:
    base = "".join(f'<element name="a{index}"><empty/></element>' for index in range(2000))
    defines = "".join(
        f'<define name="b{index}"><choice><ref name="a"/><element name="x{index}"><empty/></element></choice></define>'
        for index in range(count)
    )
    refs = "".join(f'<ref name="b{index}"/>' for index in range(count))
    return rgrammar(
        f'<start><element name="r"><interleave><choice>{refs}</choice><element name="z"><empty/></element>'
        "</interleave></element></start>"
        f'<define name="a"><group>{base}</group></define>{defines}'
    )


def _wildcards_against_exact_names(count: int) -> str:
    classes = "".join(f'<element><nsName ns="urn:w{index}"/><empty/></element>' for index in range(count))
    exact = "".join(f'<element name="x{index}"><empty/></element>' for index in range(count))
    return rgrammar(
        '<start><element name="r"><interleave>'
        f"<group>{classes}</group><group>{exact}</group>"
        "</interleave></element></start>"
    )


def _superset_wild_unions(count: int) -> str:
    base = "".join(f'<element><nsName ns="urn:a{index}"/><empty/></element>' for index in range(2000))
    defines = "".join(
        f'<define name="b{index}"><choice><ref name="a"/><group><element><nsName ns="urn:b{index}"/><empty/></element>'
        f'<element name="x{index}"><empty/></element></group></choice></define>'
        for index in range(count)
    )
    refs = "".join(f'<ref name="b{index}"/>' for index in range(count))
    return rgrammar(
        f'<start><element name="r"><interleave><choice>{refs}</choice><element name="z"><empty/></element>'
        "</interleave></element></start>"
        f'<define name="a"><group>{base}</group></define>{defines}'
    )


def _combine_part_nested_budget(count: int) -> str:
    group = "".join(f'<element name="r{index}"><empty/></element>' for index in range(count))
    return rgrammar(
        '<start><element name="r"><ref name="c"/></element></start>'
        f'<define name="c"><group>{group}</group></define>'
        f'<define name="c" combine="interleave"><interleave>{_except_group_branches(count)}</interleave></define>'
    )


def _except_wildcard_pair(names: int) -> str:
    listed = "".join(f'<name ns="urn:a">x{index}</name>' for index in range(names))
    big = (
        '<define name="big"><element><anyName><except><choice><nsName ns="urn:a"/>'
        f"{listed}</choice></except></anyName><empty/></element></define>"
    )
    branch = '<interleave><element><nsName ns="urn:a"/><empty/></element><ref name="big"/></interleave>'
    return rgrammar(f'<start><element name="doc"><group>{branch}</group></element></start>{big}')


def _wildcards_outnumber_exact(count: int) -> str:
    classes = "".join(f'<element><nsName ns="urn:w{index}"/><empty/></element>' for index in range(count + 1))
    exact = "".join(f'<element name="x{index}"><empty/></element>' for index in range(count))
    return rgrammar(
        '<start><element name="r"><interleave>'
        f"<group>{exact}</group><group>{classes}</group>"
        "</interleave></element></start>"
    )


@pytest.mark.parametrize(
    "builder",
    [
        pytest.param(_adversarial_interleaves, id="shared-define-against-variants"),
        pytest.param(_except_against_group, id="except-class-against-exact-group"),
        pytest.param(_wildcards_outnumber_exact, id="wildcards-outnumber-exact-names"),
        pytest.param(_nested_interleave_budget, id="nested-interleave-branch"),
        pytest.param(_define_interleave_budget, id="interleave-in-a-referenced-define"),
        pytest.param(_combine_part_budget, id="combined-interleave-part"),
        pytest.param(_superset_unions, id="unions-of-supersets"),
        pytest.param(_superset_wild_unions, id="unions-of-wildcard-supersets"),
        pytest.param(_wildcards_against_exact_names, id="wildcard-classes-against-exact-names"),
        pytest.param(_combine_part_nested_budget, id="nested-interleave-in-a-combined-part"),
        pytest.param(_except_wildcard_pair, id="except-class-against-a-wildcard"),
    ],
)
def test_rng_interleave_work_budget_rejects_large_sets(builder: Callable[[int], str], work_steps: WorkSteps) -> None:
    schema = builder(20000)

    def reject() -> None:
        with pytest.raises(ValueError, match="too large to check interleave branches"):
            RelaxNG(schema)

    # nc_holds and nc_overlap tick per visit while the budget counts units, so visits can pass the limit: 1.30x at most
    assert 0 < work_steps(reject) <= 2 * (1 << 25)


def test_rng_shared_unions_stay_accepted() -> None:
    assert isinstance(RelaxNG(_shared_unions(2000)), RelaxNG)
    schema = RelaxNG(_shared_unions(3))
    assert schema.validate(parse_xml(f"<doc>{'<x0/><q/>' * 3}</doc>")).valid
    assert not schema.validate(parse_xml("<doc><x0/></doc>")).valid


def _shared_unions(count: int) -> str:
    left = "".join(f'<element name="x{index}"><empty/></element>' for index in range(count))
    right = "".join(f'<element name="y{index}"><empty/></element>' for index in range(count))
    defines = "".join(
        f'<define name="z{index}"><choice><ref name="x"/><ref name="y"/></choice></define>' for index in range(count)
    )
    uses = "".join(
        f'<interleave><ref name="z{index}"/><element name="q"><empty/></element></interleave>' for index in range(count)
    )
    return rgrammar(
        f'<start><element name="doc"><group>{uses}</group></element></start>'
        f'<define name="x"><choice>{left}</choice></define><define name="y"><choice>{right}</choice></define>{defines}'
    )


def test_rng_chained_choice_unions_of_shared_defines_stay_accepted() -> None:
    assert isinstance(RelaxNG(_chained_unions(2000)), RelaxNG)
    schema = RelaxNG(_chained_unions(3))
    assert schema.validate(parse_xml("<r><b2/><z/></r>")).valid
    assert not schema.validate(parse_xml("<r><z/></r>")).valid


def _chained_unions(depth: int) -> str:
    chained = "".join(
        f'<define name="d{index}"><choice><ref name="d{index - 1}"/><ref name="e{index - 1}"/>'
        f'<element name="a{index}"><empty/></element></choice></define>'
        f'<define name="e{index}"><choice><ref name="e{index - 1}"/><ref name="d{index - 1}"/>'
        f'<element name="b{index}"><empty/></element></choice></define>'
        for index in range(1, depth + 1)
    )
    start = (
        f'<start><element name="r"><interleave><ref name="d{depth}"/>'
        '<element name="z"><empty/></element></interleave></element></start>'
        '<define name="d0"><element name="a0"><empty/></element></define>'
        '<define name="e0"><element name="b0"><empty/></element></define>'
    )
    return rgrammar(f"{start}{chained}")


def test_rng_same_structure_wildcards_scale_linearly(work_steps: WorkSteps) -> None:
    def wildcards(count: int) -> str:
        alts = "".join(f'<element><nsName ns="urn:a"/><attribute name="v{index}"/></element>' for index in range(count))
        return rwrap(f'<interleave><choice>{alts}</choice><element name="b"><empty/></element></interleave>')

    work_steps.assert_linear(lambda n: partial(RelaxNG, wildcards(n)), (2000, 8000), exponent=1.3)


def test_rng_chain_of_interleaves_scales_linearly(work_steps: WorkSteps) -> None:
    work_steps.assert_linear(lambda n: partial(RelaxNG, _interleave_chain(n)), (500, 2000), exponent=1.3)


def _interleave_chain(count: int) -> str:
    defines = "".join(_chain_define(index) for index in range(count))
    start = '<start><element name="r"><ref name="d0"/></element></start>'
    return rgrammar(f'{start}{defines}<define name="d{count}"><empty/></define>')


def _chain_define(index: int) -> str:
    elements = "".join(f'<element name="e{index}_{slot}"><empty/></element>' for slot in range(10))
    return (
        f'<define name="d{index}"><interleave><group>{elements}</group><ref name="d{index + 1}"/></interleave></define>'
    )


def test_rng_referenced_define_with_attribute_and_value() -> None:
    schema = RelaxNG(
        rgrammar(
            '<start><element name="r"><interleave><ref name="p"/>'
            '<element name="q"><empty/></element></interleave></element></start>'
            '<define name="p"><group><attribute name="x"/><value>v</value></group></define>'
        )
    )
    assert schema.validate(parse_xml('<r x="1">v<q/></r>')).valid
    assert not schema.validate(parse_xml("<r><q/></r>")).valid


def test_rng_identical_wildcard_classes_stay_accepted() -> None:
    alts = "".join('<element><nsName ns="urn:a"/><empty/></element>' for _ in range(2000))
    schema = rwrap(
        f'<interleave><optional><choice>{alts}</choice></optional><element name="b"><empty/></element></interleave>'
    )
    assert rng_ok(schema, "<doc><b/></doc>")


@pytest.mark.parametrize(
    ("schema", "xml", "valid"),
    [
        pytest.param(
            rgrammar(
                '<start><ref name="a"/></start>'
                '<define name="a"><element name="a"><optional><ref name="b"/></optional></element></define>'
                '<define name="b"><element name="b"><optional><ref name="a"/></optional></element></define>'
            ),
            "<a><b><a/></b></a>",
            True,
            id="4.19-reference-cycle-through-element",
        ),
        pytest.param(
            rgrammar('<start><element name="r"><empty/></element></start><define name="a"><ref name="a"/></define>'),
            "<r/>",
            True,
            id="4.19-cycle-unreachable-from-start",
        ),
        pytest.param(
            rwrap(
                '<oneOrMore><choice><element name="a"><empty/></element>'
                '<group><element name="a"><empty/></element><element name="a"><empty/></element></group>'
                "</choice></oneOrMore>"
            ),
            f"<doc>{'<a/>' * 400}</doc>",
            True,
            id="ambiguous-choice-stays-bounded",
        ),
        pytest.param(
            rwrap(
                "<interleave><element><anyName><except><name>b</name></except></anyName><empty/></element>"
                '<element name="b"><empty/></element></interleave>'
            ),
            "<doc><z/><b/></doc>",
            True,
            id="7.4-wildcard-branch-excepts-the-other-name",
        ),
        pytest.param(
            rwrap(
                '<interleave><element><anyName><except><nsName ns="urn:x"/></except></anyName><empty/></element>'
                '<element><nsName ns="urn:x"/><empty/></element></interleave>'
            ),
            '<doc><a xmlns="urn:x"/><b/></doc>',
            True,
            id="7.4-wildcard-branch-excepts-the-other-namespace",
        ),
        pytest.param(
            rwrap(
                '<interleave><element><nsName ns="urn:x"/><empty/></element>'
                '<element><nsName ns="urn:y"/><empty/></element><element name="a"><empty/></element></interleave>'
            ),
            '<doc><a xmlns="urn:y"/><a/><b xmlns="urn:x"/></doc>',
            True,
            id="7.4-namespace-branches-in-different-namespaces",
        ),
        pytest.param(
            rwrap(
                "<interleave><element><choice><name>a</name><name>b</name></choice><empty/></element>"
                "<element><choice><name>c</name><name>d</name></choice><empty/></element></interleave>"
            ),
            "<doc><d/><a/></doc>",
            True,
            id="7.4-name-choices-with-different-names",
        ),
        pytest.param(
            rgrammar(
                '<start><element name="r"><interleave>'
                '<group><ref name="a"/><ref name="a"/></group><ref name="b"/><ref name="c"/>'
                "</interleave></element></start>"
                '<define name="a"><element name="a"><empty/></element></define>'
                '<define name="b"><element name="b"><optional><ref name="a"/></optional></element></define>'
                '<define name="c"><element name="c"><empty/></element></define>'
                '<define name="c" combine="choice"><element name="d"><empty/></element></define>'
            ),
            "<r><a/><d/><b><a/></b><a/></r>",
            True,
            id="7.4-refs-to-distinct-elements",
        ),
        pytest.param(
            rgrammar(
                "".join((
                    '<start><element name="r"><interleave><group>',
                    *(f'<ref name="d{index}"/>' for index in range(9)),
                    '</group><element name="z"><empty/></element></interleave></element></start>',
                    *(
                        f'<define name="d{index}"><element name="a{index}"><empty/></element></define>'
                        for index in range(9)
                    ),
                ))
            ),
            "<r><a0/><a1/><z/><a2/><a3/><a4/><a5/><a6/><a7/><a8/></r>",
            True,
            id="7.4-branch-with-many-refs",
        ),
        pytest.param(
            rgrammar(
                '<start><element name="r"><ref name="c"/></element></start>'
                '<define name="c"><element name="a"><empty/></element><text/></define>'
                '<define name="c" combine="choice"><element name="a"><text/></element><text/></define>'
            ),
            "<r><a>x</a>y</r>",
            True,
            id="7.4-combine-choice-shares-names-and-text",
        ),
        pytest.param(
            rwrap(
                '<interleave><element name="a"><empty/></element>'
                '<element><choice><name ns="urn:x">a</name><name>b</name></choice><empty/></element></interleave>'
            ),
            "<doc><a/><b/></doc>",
            True,
            id="7.4-name-differs-only-by-namespace",
        ),
        pytest.param(
            rwrap(
                '<interleave><element><choice><name>a</name><nsName ns="urn:x"/></choice><empty/></element>'
                '<element name="b"><empty/></element></interleave>'
            ),
            "<doc><a/><b/></doc>",
            True,
            id="7.4-wildcard-inside-a-choice-is-not-a-name",
        ),
        pytest.param(
            rwrap(
                "<interleave><element><choice><name>a</name><name>a</name></choice><empty/></element>"
                '<element name="b"><empty/></element></interleave>'
            ),
            "<doc><a/><b/></doc>",
            True,
            id="7.4-repeated-name-in-a-choice",
        ),
        pytest.param(
            rwrap(
                f"""<interleave><optional><choice><element><nsName ns="urn:x"/><empty/></element>"""
                f"""<group>{'<element><nsName ns="urn:x"/><empty/></element>' * 3}</group></choice></optional>"""
                '<element name="z"><empty/></element></interleave>'
            ),
            "<doc><z/></doc>",
            True,
            id="7.4-identical-wildcard-classes-in-a-choice",
        ),
        pytest.param(
            rgrammar(
                '<start><element name="doc"><group>'
                '<interleave><zeroOrMore><ref name="a"/></zeroOrMore>'
                '<zeroOrMore><ref name="b"/></zeroOrMore></interleave>'
                '<interleave><zeroOrMore><ref name="a"/></zeroOrMore>'
                '<zeroOrMore><ref name="b"/></zeroOrMore></interleave>'
                '<interleave><zeroOrMore><ref name="a"/></zeroOrMore>'
                '<zeroOrMore><ref name="b"/></zeroOrMore></interleave>'
                "</group></element></start>"
                '<define name="a"><element name="a"><empty/></element></define>'
                '<define name="b"><element name="b"><empty/></element></define>'
            ),
            "<doc><a/><b/></doc>",
            True,
            id="7.4-shared-pair-of-defines-in-three-interleaves",
        ),
        pytest.param(
            rgrammar(
                '<start><element name="r"><ref name="c"/></element></start>'
                '<define name="c" combine="choice"><element name="a"><empty/></element></define>'
                '<define name="c"><element name="b"><empty/></element></define>'
            ),
            "<r><b/></r>",
            True,
            id="7.4-later-combined-part-without-an-attribute",
        ),
        pytest.param(
            rwrap(
                '<interleave><group>\n  <element name="a"><empty/></element>\n  </group>'
                '<element name="b"><empty/></element></interleave>'
            ),
            "<doc><a/><b/></doc>",
            True,
            id="7.4-group-branch-with-whitespace",
        ),
        pytest.param(
            rwrap(
                '<interleave><element name="a" ns="urn:x"><empty/></element>'
                '<element name="a" ns="urn:y"><empty/></element></interleave>'
            ),
            '<doc><a xmlns="urn:x"/><a xmlns="urn:y"/></doc>',
            True,
            id="7.4-same-local-name-different-namespace",
        ),
        pytest.param(
            rgrammar(
                '<start><element name="r"><attribute name="a"><ref name="t"/></attribute></element></start>'
                '<define name="t"><text/></define>'
            ),
            '<r a="hello"/>',
            True,
            id="attribute-content-ref-matches-text",
        ),
        pytest.param(
            rgrammar('<start><element name="r"><f:ref xmlns:f="urn:y"/><text/></element></start>'),
            "<r>x</r>",
            True,
            id="foreign-ref-is-ignored-annotation",
        ),
    ],
)
def test_rng_restricted_grammar_compiles_and_validates(schema: str, xml: str, *, valid: bool) -> None:
    assert rng_ok(schema, xml) is valid


def _crossed_chain(leaf: str, start: str, combine: str, *, count: int) -> str:
    defines = "".join(
        f'<define name="d{index}"><{combine}><ref name="d{index + 1}"/><ref name="e{index + 1}"/></{combine}></define>'
        f'<define name="e{index}"><{combine}><ref name="e{index + 1}"/><ref name="d{index + 1}"/></{combine}></define>'
        for index in range(count)
    )
    return rgrammar(
        f'<start><element name="r">{start}</element></start>{defines}'
        f'<define name="d{count}">{leaf}</define><define name="e{count}">{leaf}</define>'
    )


_INTEGER_LIST_LEAF: Final[str] = f'<optional><data type="integer" datatypeLibrary="{_DT}"/></optional>'
_NAMED_VALUE_ATTRIBUTE: Final[str] = '<attribute name="k"><value>v</value></attribute>'


@pytest.mark.parametrize(
    ("schema", "xml", "valid"),
    [
        pytest.param(
            _crossed_chain('<data type="token"/>', '<ref name="d0"/>', "choice", count=10),
            "<r>one two</r>",
            True,
            id="token-data-accepts-words",
        ),
        pytest.param(
            _crossed_chain(f'<data type="integer" datatypeLibrary="{_DT}"/>', '<ref name="d0"/>', "choice", count=10),
            "<r>one</r>",
            False,
            id="integer-data-rejects-word",
        ),
        pytest.param(
            _crossed_chain(_INTEGER_LIST_LEAF, '<list><ref name="d0"/></list>', "group", count=10),
            "<r>1 2 3</r>",
            True,
            id="integer-list-accepted",
        ),
        pytest.param(
            _crossed_chain(_INTEGER_LIST_LEAF, '<list><ref name="d0"/></list>', "group", count=10),
            "<r>1 2 3 x</r>",
            False,
            id="integer-list-rejected-at-last-token",
        ),
        pytest.param(
            _crossed_chain(_NAMED_VALUE_ATTRIBUTE, '<ref name="d0"/>', "choice", count=10),
            '<r m="v"/>',
            False,
            id="attribute-fails-on-name",
        ),
        pytest.param(
            _crossed_chain(_NAMED_VALUE_ATTRIBUTE, '<ref name="d0"/>', "choice", count=10),
            '<r k="v"/>',
            True,
            id="attribute-accepted-with-name-and-value",
        ),
        pytest.param(
            _crossed_chain("<empty/>", '<ref name="d0"/>', "choice", count=10),
            "<r>x</r>",
            False,
            id="empty-choice-rejects-text",
        ),
    ],
)
def test_rng_shared_subpatterns_decide_each_event(schema: str, xml: str, *, valid: bool) -> None:
    assert rng_ok(schema, xml) is valid


def test_rng_attribute_after_rejected_attribute_is_invalid() -> None:
    assert not rng_ok(rwrap("<empty/>"), '<doc a="1" b="2"/>')


def test_rng_nullable_memo_rejects_blank_value_attribute() -> None:
    schema = _crossed_chain("<value>q</value>", '<attribute name="k"><ref name="d0"/></attribute>', "choice", count=11)
    assert not rng_ok(schema, '<r k=" "/>')


def test_rng_overlapping_attributes_scale_linearly(work_steps: WorkSteps) -> None:
    document = parse_xml("<doc a0='1' a1='2' a2='3'/>")
    pattern = '<optional><attribute><nsName ns=""/></attribute></optional>'
    work_steps.assert_linear(lambda n: partial(RelaxNG(rwrap(pattern * n)).validate, document), (12, 20))


def _ladder(leaf: str, *, combine: str) -> str:
    return _crossed_chain(leaf, '<ref name="d0"/>', combine, count=11)


def _distinct_choice_ladder(count: int) -> str:
    defines = "".join(
        f'<define name="d{index}"><choice><ref name="d{index + 1}"/>'
        f'<group><ref name="d{index + 1}"/><empty/></group></choice></define>'
        for index in range(count)
    )
    return rgrammar(
        f'<start><element name="r"><ref name="d0"/></element></start>{defines}'
        f'<define name="d{count}"><element name="a"><empty/></element></define>'
    )


_DATA_EXCEPT: Final[str] = '<data type="token"><except><value>x</value></except></data>'
_INTEGER: Final[str] = f'<data type="integer" datatypeLibrary="{_DT}"/>'
_INTERLEAVE_ELEMENTS: Final[str] = (
    '<interleave><element name="b"><empty/></element><element name="c"><empty/></element></interleave>'
)
_INTERLEAVE_ATTRIBUTES: Final[str] = '<interleave><attribute name="b"/><attribute name="c"/></interleave>'
_ONE_OR_MORE_ELEMENTS: Final[str] = '<oneOrMore><element name="d"><empty/></element></oneOrMore>'
_ATTRIBUTE_VALUE: Final[str] = '<attribute name="a"><value>x</value></attribute>'

_TAILS: Final[tuple[tuple[str, str, bool, str], ...]] = (
    (_DATA_EXCEPT, "<r>y</r>", True, "data-except-accepted"),
    (_DATA_EXCEPT, "<r>x</r>", False, "data-except-rejected"),
    ("<list><oneOrMore><value>x</value></oneOrMore></list>", "<r>  x x </r>", True, "list-accepted"),
    ("<list><oneOrMore><value>x</value></oneOrMore></list>", "<r>y</r>", False, "list-rejected"),
    ("<choice><text/><empty/></choice>", "<r>x</r>", True, "choice-text-accepted"),
    ("<group><text/><empty/></group>", "<r>x</r>", True, "group-text-accepted"),
    (_INTERLEAVE_ELEMENTS, "<r><c/><b/></r>", True, "interleave-accepted"),
    (_INTERLEAVE_ELEMENTS, "<r><b/><b/></r>", False, "interleave-rejected"),
    (_ONE_OR_MORE_ELEMENTS, "<r><d/><d/></r>", True, "one-or-more-accepted"),
    (_ONE_OR_MORE_ELEMENTS, "<r/>", False, "one-or-more-missing"),
    (_ATTRIBUTE_VALUE, '<r a="x"/>', True, "attribute-accepted"),
    (_ATTRIBUTE_VALUE, '<r a="y"/>', False, "attribute-value-rejected"),
    (_ATTRIBUTE_VALUE, '<r b="1"/>', False, "attribute-name-rejected"),
    (
        '<element name="a"><element name="c"><empty/></element></element>',
        "<r><a><c/></a></r>",
        True,
        "element-with-child-accepted",
    ),
    ('<element name="a"><empty/></element>', "<r><b/></r>", False, "element-name-rejected"),
    (
        '<group><element name="z"><empty/></element><text/></group>',
        "<r><z/></r>",
        True,
        "group-element-first-accepted",
    ),
    (
        '<group><element name="z"><empty/></element><text/></group>',
        "<r>x</r>",
        False,
        "group-element-first-missing",
    ),
    ("<value>x</value>", "<r>x</r>", True, "value-accepted"),
    ("<value>x</value>", "<r>y</r>", False, "value-rejected"),
    ("<empty/>", "<r/>", True, "empty-accepted"),
    ('<data type="token"/>', "<r>y</r>", True, "data-token-accepted"),
    (_INTEGER, "<r>seven</r>", False, "data-datatype-rejected"),
    ("<interleave><text/><empty/></interleave>", "<r>x</r>", True, "interleave-text-accepted"),
    (_INTERLEAVE_ATTRIBUTES, '<r b="1" c="2"/>', True, "interleave-attributes-accepted"),
    (_INTERLEAVE_ATTRIBUTES, '<r b="1"/>', False, "interleave-attributes-missing"),
    ('<oneOrMore><attribute name="d"/></oneOrMore>', '<r d="1"/>', True, "one-or-more-attributes-accepted"),
    ('<oneOrMore><attribute name="d"/></oneOrMore>', "<r/>", False, "one-or-more-attributes-missing"),
    ("<notAllowed/>", "<r/>", False, "not-allowed-tail"),
)


@pytest.mark.parametrize("first", [pytest.param(True, id="tail-first"), pytest.param(False, id="tail-last")])
@pytest.mark.parametrize(("tail", "xml", "valid"), [pytest.param(*row[:3], id=row[3]) for row in _TAILS])
def test_rng_shared_define_memo_paths_after_ladder(tail: str, xml: str, *, valid: bool, first: bool) -> None:
    assert rng_ok(_with_tail(tail, first=first), xml) is valid


def _with_tail(tail: str, *, first: bool) -> str:
    walk = f'<ref name="d0"/>{tail}' if first else f'{tail}<ref name="d0"/>'
    return _crossed_chain("<text/>", f"<group>{walk}</group>", "group", count=11)


@pytest.mark.parametrize(
    ("schema", "xml", "valid"),
    [
        pytest.param(_distinct_choice_ladder(11), "<r><a/></r>", True, id="distinct-choice-accepted"),
        pytest.param(_distinct_choice_ladder(11), "<r><b/></r>", False, id="distinct-choice-name-mismatch"),
        pytest.param(_distinct_choice_ladder(11), "<r/>", False, id="distinct-choice-missing"),
        pytest.param(
            _ladder('<element name="a"><empty/></element>', combine="choice"),
            "<r><a/></r>",
            True,
            id="choice-element-accepted",
        ),
        pytest.param(
            _ladder('<element name="a"><empty/></element>', combine="choice"),
            "<r><b/></r>",
            False,
            id="choice-element-name-mismatch",
        ),
        pytest.param(_ladder("<empty/>", combine="group"), "<r/>", True, id="group-empty-accepted"),
        pytest.param(_ladder("<empty/>", combine="group"), "<r>x</r>", False, id="group-empty-text-rejected"),
        pytest.param(_ladder("<empty/>", combine="oneOrMore"), "<r/>", True, id="one-or-more-empty-accepted"),
        pytest.param(_ladder(_DATA_EXCEPT, combine="choice"), "<r>y</r>", True, id="choice-data-except-accepted"),
        pytest.param(_ladder(_DATA_EXCEPT, combine="choice"), "<r>x</r>", False, id="choice-data-except-rejected"),
        pytest.param(_ladder(_INTEGER, combine="choice"), "<r>7</r>", True, id="choice-data-datatype-accepted"),
        pytest.param(_ladder(_INTEGER, combine="choice"), "<r>seven</r>", False, id="choice-data-datatype-rejected"),
        pytest.param(
            _ladder('<optional><attribute name="a"/></optional>', combine="group"),
            '<r b="1"/>',
            False,
            id="group-attribute-name-mismatch",
        ),
        pytest.param(
            _ladder(f"<optional>{_ATTRIBUTE_VALUE}</optional>", combine="group"),
            '<r a="x"/>',
            True,
            id="group-attribute-value-accepted",
        ),
        pytest.param(
            _ladder(f"<optional>{_ATTRIBUTE_VALUE}</optional>", combine="group"),
            '<r a="y"/>',
            False,
            id="group-attribute-value-rejected",
        ),
        pytest.param(
            _crossed_chain("<optional><value>x</value></optional>", '<list><ref name="d0"/></list>', "group", count=11),
            "<r>  x   x  </r>",
            True,
            id="list-surrounding-whitespace-accepted",
        ),
        pytest.param(
            _crossed_chain("<optional><value>x</value></optional>", '<list><ref name="d0"/></list>', "group", count=11),
            "<r>   </r>",
            True,
            id="list-whitespace-only-accepted",
        ),
    ],
)
def test_rng_shared_define_memo_paths(schema: str, xml: str, *, valid: bool) -> None:
    assert rng_ok(schema, xml) is valid


@pytest.mark.parametrize("first", [pytest.param(True, id="ladder-first"), pytest.param(False, id="element-first")])
@pytest.mark.parametrize(
    ("xml", "valid"),
    [
        pytest.param("<r><a/></r>", True, id="element-accepted"),
        pytest.param("<r><a/>x</r>", True, id="element-then-text-accepted"),
        pytest.param("<r>x</r>", False, id="element-missing"),
    ],
)
def test_rng_interleaved_ladder_after_nodes(xml: str, *, valid: bool, first: bool) -> None:
    assert rng_ok(_interleaved_ladder(first=first), xml) is valid


def _interleaved_ladder(*, first: bool) -> str:
    walk = '<ref name="d0"/><element name="a"><empty/></element>'
    if not first:
        walk = '<element name="a"><empty/></element><ref name="d0"/>'
    return _crossed_chain("<text/>", f"<interleave>{walk}</interleave>", "group", count=11)


@pytest.mark.parametrize(
    ("leaf", "content", "xml"),
    [
        pytest.param("<empty/>", '<ref name="d0"/>', "<r/>", id="start-tag-close"),
        pytest.param('<optional><attribute name="k"/></optional>', '<ref name="d0"/>', '<r k="1"/>', id="attribute"),
        pytest.param("<text/>", '<ref name="d0"/>', "<r>y</r>", id="text"),
        pytest.param("<text/>", '<ref name="d0"/>', "<r><z/></r>", id="start-tag-open"),
        pytest.param("<text/>", '<attribute name="k"><ref name="d0"/></attribute>', '<r k="y"/>', id="nullable"),
        pytest.param(_INTEGER_LIST_LEAF, '<list><ref name="d0"/></list>', "<r>1 2 3</r>", id="list-tokens"),
    ],
)
def test_rng_shared_defines_scale_linearly(leaf: str, content: str, xml: str, work_steps: WorkSteps) -> None:
    document = parse_xml(xml)
    work_steps.assert_linear(
        lambda n: partial(RelaxNG(_crossed_chain(leaf, content, "group", count=n)).validate, document), (12, 20)
    )


@pytest.mark.parametrize(
    ("schema", "xml"),
    [
        pytest.param(
            rwrap(
                "".join(f'<optional><element name="e{index}"><empty/></element></optional>' for index in range(1100))
            ),
            "<doc><e550/></doc>",
            id="elements",
        ),
        pytest.param(
            rwrap("".join(f'<optional><attribute name="a{index}"/></optional>' for index in range(1100))),
            '<doc a550="1"/>',
            id="attributes",
        ),
        pytest.param(rwrap("<optional><text/></optional>" * 1100), "<doc>x</doc>", id="text"),
        pytest.param(
            rwrap('<optional><data type="token"><except><value>x</value></except></data></optional>' * 1100),
            "<doc>y</doc>",
            id="data-with-except",
        ),
    ],
)
def test_rng_long_optional_sequence_validates(schema: str, xml: str) -> None:
    assert rng_ok(schema, xml)


def test_rng_cdata_and_prefixed_attribute() -> None:
    schema = rwrap('<attribute name="p:id" xmlns:p="urn:a"><text/></attribute><text/>')
    assert rng_ok(schema, '<doc xmlns:p="urn:a" p:id="1"><![CDATA[body]]></doc>')


def test_rng_data_param_without_name() -> None:
    schema = (
        f'<grammar xmlns="{_RELAXNG_NS}" datatypeLibrary="{_DT}"><start>'
        '<element name="e"><data type="string"><param>ignored</param></data></element></start></grammar>'
    )
    assert rng_ok(schema, "<e>hello</e>")


def test_rng_default_library_string_and_unknown_xsd_type() -> None:
    # default datatype library: "string" (not token) exercises the DT_STRING branch
    assert rng_ok(rwrap('<element name="s"><data type="string"/></element>'), "<doc><s>x</s></doc>")
    # an explicit non-XSD datatype library falls back to the built-in string/token library
    assert rng_ok(
        f'<element name="s" xmlns="{_RELAXNG_NS}" datatypeLibrary=""><data type="token"/></element>',
        "<s>x</s>",
    )
    # the XSD library with an unrecognized type name falls back to string
    assert rng_ok(
        f'<element name="s" xmlns="{_RELAXNG_NS}" datatypeLibrary="{_DT}"><data type="madeUp"/></element>',
        "<s>anything</s>",
    )


def test_rng_element_without_name_class_matches_any() -> None:
    # a malformed element with neither @name nor a name-class child recovers as anyName
    schema = f'<grammar xmlns="{_RELAXNG_NS}"><start><element/></start></grammar>'
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
        f'<grammar xmlns="{_RELAXNG_NS}" datatypeLibrary="{_DT}"><start>'
        '<element name="s"><data type="string">\n  <param name="minLength">2</param>\n</data></element>'
        "</start></grammar>"
    )
    assert rng_ok(schema, "<s>ab</s>")
    assert not rng_ok(schema, "<s>a</s>")


@pytest.mark.parametrize(
    "document",
    [
        pytest.param("<r><a>1</a></r>", id="first"),
        pytest.param("<r><b>2</b></r>", id="second"),
        pytest.param("<r><c>3</c></r>", id="third"),
    ],
)
def test_rng_multiple_defines_dedup_and_combine(document: str) -> None:
    schema = (
        f'<grammar xmlns="{_RELAXNG_NS}"><start><element name="r"><ref name="x"/></element></start>'
        '<define name="x"><element name="a"><text/></element></define>'
        '<define name="x" combine="choice"><element name="b"><text/></element></define>'
        '<define name="x" combine="choice"><element name="c"><text/></element></define>'
        '<define name="y"><empty/></define></grammar>'
    )
    assert rng_ok(schema, document)


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
        f'<grammar xmlns="{_RELAXNG_NS}" datatypeLibrary="{_DT}"><start>'
        '<element name="nums"><list><oneOrMore><data type="int"/></oneOrMore></list></element></start></grammar>'
    )
    assert rng_ok(schema, "<nums>  1   2  3  </nums>")
    assert not rng_ok(schema, "<nums>   </nums>")  # only whitespace -> no tokens -> oneOrMore unmet


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


def test_rng_grammar_with_nameless_define_and_ref() -> None:
    schema = (
        f'<grammar xmlns="{_RELAXNG_NS}"><start><element name="r"><ref name="x"/></element></start>'
        "<define><empty/></define>"  # a nameless define is skipped when resolving refs
        '<define name="x"><text/></define></grammar>'
    )
    assert rng_ok(schema, "<r>body</r>")


def test_rng_attribute_whitespace_value_against_data() -> None:
    schema = (
        f'<grammar xmlns="{_RELAXNG_NS}" datatypeLibrary="{_DT}"><start>'
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


def test_rng_define_dedup_finds_on_later_iteration() -> None:
    # defines a, b, then b again: the dedup scan skips a (index 0) then matches b (index 1)
    schema = (
        f'<grammar xmlns="{_RELAXNG_NS}"><start><element name="r"><ref name="b"/></element></start>'
        '<define name="a"><empty/></define>'
        '<define name="b"><text/></define>'
        '<define name="c"><empty/></define>'
        '<define name="b" combine="choice"><text/></define></grammar>'
    )
    assert rng_ok(schema, "<r>x</r>")


@pytest.mark.parametrize("name", [pytest.param("node", id="unqualified"), pytest.param("xml:node", id="xml-prefix")])
def test_shared_schema_validates_distinct_documents(name: str) -> None:
    schema: Final = RelaxNG(
        '<grammar xmlns="http://relaxng.org/ns/structure/1.0"><start><ref name="node"/></start>'
        f'<define name="node"><element><name>{name}</name><zeroOrMore><ref name="node"/></zeroOrMore>'
        "</element></define></grammar>"
    )
    children: Final = (name, "other") * 16
    documents: Final = [parse_xml(f"<{name}><{child}/></{name}>") for child in children]
    with ThreadPoolExecutor(max_workers=4) as executor:
        results: Final = list(executor.map(schema.validate, documents))
    assert [result.valid for result in results] == [child == name for child in children]


@pytest.mark.parametrize(
    "index",
    [
        pytest.param(0, id="optional-group"),
        pytest.param(1, id="optional-interleave"),
        pytest.param(2, id="small-group"),
        pytest.param(3, id="recursive"),
    ],
)
def test_reuse_benchmark_output(index: int) -> None:
    source, document = cast("tuple[str, str]", INPUTS["validate-rng-reuse"]()[index][1])
    assert RelaxNG(source).validate(parse_xml(document)).errors == ()


@pytest.mark.parametrize(
    ("pattern", "documents"),
    [
        pytest.param(
            "<value><![CDATA[]]></value>",
            ("<doc><![CDATA[]]></doc>", "<doc>x</doc>"),
            id="empty-cdata",
        ),
        pytest.param(
            '<mixed><zeroOrMore><element name="child"><empty/></element></zeroOrMore></mixed>',
            ("<doc>a<![CDATA[]]><child/>b</doc>", "<doc><other/></doc>"),
            id="mixed-text",
        ),
    ],
)
def test_repeated_validation_preserves_character_data(pattern: str, documents: tuple[str, str]) -> None:
    schema: Final = RelaxNG(rwrap(pattern))
    parsed: Final = tuple(map(parse_xml, documents))
    assert [schema.validate(document).valid for _ in range(3) for document in parsed] == [True, False] * 3


@pytest.mark.oracle
@pytest.mark.parametrize(
    ("schema", "doc"),
    [
        pytest.param(f'<element name="a" xmlns="{_RELAXNG_NS}"><text/></element>', "<a>hi</a>", id="text-valid"),
        pytest.param(
            f'<element name="a" xmlns="{_RELAXNG_NS}"><element name="b"><text/></element></element>',
            "<a><c>x</c></a>",
            id="wrong-child-invalid",
        ),
        pytest.param(
            f'<element name="p" xmlns="{_RELAXNG_NS}"><interleave>'
            '<element name="a"><text/></element><element name="b"><text/></element></interleave></element>',
            "<p><b>2</b><a>1</a></p>",
            id="interleave-any-order-valid",
        ),
        pytest.param(
            f'<element name="p" xmlns="{_RELAXNG_NS}"><interleave>'
            '<element name="a"><text/></element><element name="b"><text/></element></interleave></element>',
            "<p><a>1</a></p>",
            id="interleave-missing-invalid",
        ),
        pytest.param(
            f'<element name="r" xmlns="{_RELAXNG_NS}">'
            f'<oneOrMore><element name="i"><text/></element></oneOrMore></element>',
            "<r><i>1</i><i>2</i></r>",
            id="oneOrMore-valid",
        ),
        pytest.param(
            f'<element name="r" xmlns="{_RELAXNG_NS}">'
            f'<oneOrMore><element name="i"><text/></element></oneOrMore></element>',
            "<r/>",
            id="oneOrMore-empty-invalid",
        ),
        pytest.param(
            f'<element name="e" xmlns="{_RELAXNG_NS}" datatypeLibrary="http://www.w3.org/2001/XMLSchema-datatypes">'
            '<data type="int"/></element>',
            "<e>7</e>",
            id="data-int-valid",
        ),
        pytest.param(
            f'<element name="e" xmlns="{_RELAXNG_NS}" datatypeLibrary="http://www.w3.org/2001/XMLSchema-datatypes">'
            '<data type="int"/></element>',
            "<e>seven</e>",
            id="data-int-invalid",
        ),
        pytest.param(
            f'<element name="r" xmlns="{_RELAXNG_NS}"><attribute name="id"><text/></attribute><text/></element>',
            '<r id="1">x</r>',
            id="attribute-valid",
        ),
        pytest.param(
            f'<element name="r" xmlns="{_RELAXNG_NS}"><attribute name="id"><text/></attribute><text/></element>',
            "<r>x</r>",
            id="attribute-missing-invalid",
        ),
    ],
)
def test_relaxng_matches_lxml(schema: str, doc: str) -> None:
    etree: Final = pytest.importorskip("lxml.etree", exc_type=ImportError)
    validator: Final = etree.RelaxNG(etree.fromstring(schema.encode()))
    assert RelaxNG(schema).validate(parse_xml(doc)).valid == validator.validate(etree.fromstring(doc.encode()))


@pytest.mark.oracle
@pytest.mark.parametrize(
    "index",
    [
        pytest.param(0, id="optional-group"),
        pytest.param(1, id="optional-interleave"),
        pytest.param(2, id="small-group"),
        pytest.param(3, id="recursive"),
    ],
)
def test_lxml_rng_reuse_benchmark_output(index: int) -> None:
    etree: Final = pytest.importorskip("lxml.etree", exc_type=ImportError)
    source, document = cast("tuple[str, str]", INPUTS["validate-rng-reuse"]()[index][1])
    schema: Final = etree.RelaxNG(etree.fromstring(source.encode()))
    assert [
        schema.validate(etree.fromstring(text.encode()))
        for text in (document, document.replace("</", "<unexpected/></", 1))
    ] == [True, False]


@pytest.mark.parametrize(
    ("combinator", "left", "right", "expected"),
    [
        pytest.param("choice", "<empty/>", '<element name="x"><empty/></element>', True, id="choice-left-empty"),
        pytest.param("choice", '<element name="x"><empty/></element>', "<empty/>", True, id="choice-right-empty"),
        pytest.param(
            "choice",
            '<element name="x"><empty/></element>',
            '<element name="y"><empty/></element>',
            False,
            id="choice-neither-empty",
        ),
        pytest.param("group", "<empty/>", "<empty/>", True, id="group-both-empty"),
        pytest.param("group", '<element name="x"><empty/></element>', "<empty/>", False, id="group-left-required"),
        pytest.param("group", "<empty/>", '<element name="x"><empty/></element>', False, id="group-right-required"),
        pytest.param("interleave", "<empty/>", "<empty/>", True, id="interleave-empty"),
        pytest.param("oneOrMore", "<empty/>", "<empty/>", True, id="one-or-more-empty"),
    ],
)
def test_rng_reference_nullability(combinator: str, left: str, right: str, *, expected: bool) -> None:
    schema: Final = RelaxNG(
        '<grammar xmlns="http://relaxng.org/ns/structure/1.0"><start><element name="r">'
        '<attribute name="a"><list>'
        f'<{combinator}><ref name="left"/><ref name="right"/></{combinator}>'
        "</list></attribute></element></start>"
        f'<define name="left">{left}</define><define name="right">{right}</define></grammar>'
    )
    assert schema.validate(parse_xml('<r a=""/>')).valid is expected


@pytest.mark.parametrize(
    "document",
    [
        pytest.param("<doc><value>bad</value></doc>", id="datatype"),
        pytest.param("<doc><unexpected/></doc>", id="nested-structure"),
        pytest.param("<wrong/>", id="root-structure"),
    ],
)
def test_is_valid_reuses_schema_after_failure(document: str) -> None:
    schema = RelaxNG(wrap(f'<element name="value"><data type="int" datatypeLibrary="{_DT}"/></element>'))
    documents = (document, "<doc><value>7</value></doc>", document)
    assert [schema.is_valid(parse_xml(xml)) for xml in documents] == [False, True, False]


@pytest.mark.parametrize(
    ("index", "expected"),
    [
        pytest.param(0, False, id="many-errors"),
        pytest.param(1, True, id="valid"),
        pytest.param(2, False, id="one-error"),
    ],
)
def test_validation_verdict_benchmark(index: int, *, expected: bool) -> None:
    source, document = cast("tuple[str, str]", INPUTS["is-valid-rng"]()[index][1])
    assert RelaxNG(source).is_valid(parse_xml(document)) is expected


_RNG: Final[str] = "http://relaxng.org/ns/structure/1.0"


@pytest.mark.parametrize(
    "make_source",
    [pytest.param(lambda schema: schema, id="text"), pytest.param(parse_xml, id="node")],
)
@pytest.mark.parametrize(
    "schema",
    [
        pytest.param(f'<externalRef xmlns="{_RNG}"/>', id="short-external"),
        pytest.param(f'<include xmlns="{_RNG}"/>', id="short-include"),
        pytest.param(f'<element xmlns="{_RNG}" name="foo"><externalRef/></element>', id="element-external"),
        pytest.param(
            f'<grammar xmlns="{_RNG}"><start><element name="foo"><externalRef/></element></start></grammar>',
            id="grammar-external",
        ),
        pytest.param(
            f'<grammar xmlns="{_RNG}"><include/><start><element name="foo"><empty/></element></start></grammar>',
            id="grammar-include",
        ),
        pytest.param(
            f'<grammar xmlns="{_RNG}"><div><include/></div><start><element name="foo"><empty/></element></start>'
            "</grammar>",
            id="div-include",
        ),
        pytest.param(
            f'<rng:grammar xmlns:rng="{_RNG}"><rng:include/><rng:start><rng:element name="foo">'
            "<rng:empty/></rng:element></rng:start></rng:grammar>",
            id="prefixed-include",
        ),
        pytest.param(
            f'<rng:element xmlns:rng="{_RNG}" name="foo"><rng:externalRef/></rng:element>',
            id="prefixed-external",
        ),
        pytest.param(
            f'<element xmlns="{_RNG}" xmlns:doc="urn:annotation" name="foo"><externalRef doc:href="x"/></element>',
            id="foreign-href-attribute",
        ),
        pytest.param(
            f'<grammar xmlns="{_RNG}"><start><element name="foo"><empty/></element></start>'
            '<define name="unused"><externalRef/></define></grammar>',
            id="unused-definition-external",
        ),
    ],
)
def test_relaxng_missing_href_rejects_compilation(schema: str, *, make_source: Callable[[str], str | Document]) -> None:
    source: Final = make_source(schema)
    for _ in range(2):
        with pytest.raises(ValueError, match="required href attribute"):
            RelaxNG(source)


@pytest.mark.parametrize(
    "annotation",
    [
        pytest.param("<doc:include/>", id="foreign-include"),
        pytest.param("<doc:externalRef/>", id="foreign-external"),
        pytest.param("<doc:annotation><externalRef/></doc:annotation>", id="foreign-subtree"),
    ],
)
def test_relaxng_missing_href_ignores_foreign_annotations(annotation: str) -> None:
    schema: Final = (
        f'<grammar xmlns="{_RNG}" xmlns:doc="urn:annotation">\n<!-- annotation -->'
        f'{annotation}<start><element name="foo"><empty/></element></start></grammar>'
    )
    validator: Final = RelaxNG(schema)
    assert [validator.validate(parse_xml(document)).valid for document in ("<foo/>", "<wrong/>")] == [True, False]


def test_relaxng_missing_href_preserves_ordinary_patterns() -> None:
    validator: Final = RelaxNG(f'<element xmlns="{_RNG}" name="foo"><text/></element>')
    assert [validator.validate(parse_xml(document)).valid for document in ("<foo>text</foo>", "<wrong/>")] == [
        True,
        False,
    ]


_NAMESPACE: Final[str] = 'xmlns="http://relaxng.org/ns/structure/1.0"'


@pytest.mark.parametrize(
    "count", [pytest.param(0, id="empty"), pytest.param(1, id="linear"), pytest.param(12, id="hashed")]
)
@pytest.mark.parametrize(
    "pattern",
    [
        pytest.param('<ref name="missing"/>', id="start"),
        pytest.param('<element name="root"><ref name="missing"/></element>', id="element"),
    ],
)
def test_relaxng_undefined_reference_rejects_compilation(count: int, pattern: str) -> None:
    definitions: Final = "".join(f'<define name="entry{index}"><empty/></define>' for index in range(count))
    schema: Final = f"<grammar {_NAMESPACE}><start>{pattern}</start>{definitions}</grammar>"
    for _ in range(2):
        with pytest.raises(ValueError, match="has no matching define"):
            RelaxNG(schema)


def test_relaxng_undefined_short_reference_rejects_compilation() -> None:
    for _ in range(2):
        with pytest.raises(ValueError, match="has no matching define"):
            RelaxNG(f'<element {_NAMESPACE} name="root"><ref name="missing"/></element>')


@pytest.mark.parametrize("count", [pytest.param(0, id="linear"), pytest.param(12, id="hashed")])
def test_relaxng_forward_reference_preserves_recursive_validation(count: int) -> None:
    definitions: Final = "".join(f'<define name="unused{index}"><empty/></define>' for index in range(count))
    schema: Final = (
        f'<grammar {_NAMESPACE}><start><ref name="entry"/></start>'
        '<define name="entry"><element name="root"><optional><ref name="entry"/></optional></element></define>'
        f"{definitions}</grammar>"
    )
    validator: Final = RelaxNG(schema)
    assert [
        [validator.is_valid(parse_xml(document)) for document in ("<root/>", "<root><root/></root>", "<wrong/>")]
        for _ in range(2)
    ] == [[True, True, False], [True, True, False]]


@pytest.mark.parametrize(
    "pattern",
    [
        pytest.param('<ref name="missing"/>', id="unknown"),
        pytest.param("<ref/>", id="absent-name"),
        pytest.param('<element name="dead"><ref name="missing"/></element>', id="nested"),
    ],
)
def test_relaxng_undefined_unused_reference_rejects_compilation(pattern: str) -> None:
    schema: Final = (
        f'<grammar {_NAMESPACE}><start><element name="root"><empty/></element></start>'
        f'<define name="unused">{pattern}</define></grammar>'
    )
    with pytest.raises(ValueError, match="RELAX NG <ref>"):
        RelaxNG(schema)


@pytest.mark.parametrize(
    "definitions",
    [
        pytest.param(
            '<define name="unused"><!--annotation--><ref name="unused"/></define>',
            id="unused-cycle",
        ),
        pytest.param(
            '<define name="unused" combine="choice"><empty/></define>'
            '<define name="unused" combine="choice"><ref name="unused"/></define>',
            id="combined-unused-cycle",
        ),
    ],
)
def test_relaxng_unused_reference_cycle_preserves_reachable_validation(definitions: str) -> None:
    schema: Final = (
        f'<grammar {_NAMESPACE}><start><element name="root"><empty/></element></start>{definitions}</grammar>'
    )
    validator: Final = RelaxNG(schema)
    assert [validator.is_valid(parse_xml(document)) for document in ("<root/>", "<other/>")] == [True, False]


def test_relaxng_undefined_combined_unused_reference_rejects_compilation() -> None:
    schema: Final = (
        f'<grammar {_NAMESPACE}><start><element name="root"><empty/></element></start>'
        '<define name="unused" combine="choice"><empty/></define>'
        '<define name="unused" combine="choice"><ref name="missing"/></define></grammar>'
    )
    with pytest.raises(ValueError, match="has no matching define"):
        RelaxNG(schema)


def test_relaxng_unused_foreign_annotation_is_ignored() -> None:
    schema: Final = (
        f'<grammar {_NAMESPACE} xmlns:doc="urn:documentation">'
        '<start><element name="root"><empty/></element></start>'
        '<define name="unused"><doc:annotation><ref name="missing"/></doc:annotation><empty/></define></grammar>'
    )
    assert RelaxNG(schema).is_valid(parse_xml("<root/>"))


def test_relaxng_annotation_normalization_preserves_public_source() -> None:
    source: Final = parse_xml(
        f'<element {_NAMESPACE} xmlns:doc="urn:documentation" name="root">'
        '<empty/><doc:annotation><ref name="missing"/></doc:annotation></element>'
    )
    serialized: Final = source.serialize()
    validator: Final = RelaxNG(source)
    assert (source.serialize(), validator.is_valid(parse_xml("<root/>"))) == (serialized, True)


_RELAXNG_EMPTY_CONTENT_NAMESPACE: Final[str] = "http://relaxng.org/ns/structure/1.0"


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("", id="absent"),
        pytest.param("<!--comment-->", id="comment"),
        pytest.param("<?probe value?>", id="processing-instruction"),
    ],
)
@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        pytest.param('<data type="string"/>', True, id="data-string"),
        pytest.param('<data type="token"/>', True, id="data-token"),
        pytest.param('<value type="string"></value>', True, id="value-string"),
        pytest.param('<value type="token"></value>', True, id="value-token"),
        pytest.param('<value type="string">x</value>', False, id="nonempty-value"),
        pytest.param(
            '<data datatypeLibrary="http://www.w3.org/2001/XMLSchema-datatypes" type="integer"/>',
            False,
            id="integer",
        ),
        pytest.param("<empty/>", True, id="empty-pattern"),
        pytest.param('<element name="child"><empty/></element>', False, id="required-child"),
        pytest.param('<optional><element name="child"><empty/></element></optional>', True, id="optional-child"),
        pytest.param('<list><zeroOrMore><data type="token"/></zeroOrMore></list>', True, id="empty-list"),
        pytest.param(
            '<attribute name="required"><text/></attribute><data type="string"/>', False, id="required-attribute"
        ),
        pytest.param(
            '<optional><attribute name="optional"><text/></attribute></optional><data type="string"/>',
            True,
            id="optional-attribute",
        ),
    ],
)
def test_rng_empty_content(pattern: str, content: str, *, expected: bool) -> None:
    validator: Final = RelaxNG(f'<element xmlns="{_RELAXNG_EMPTY_CONTENT_NAMESPACE}" name="doc">{pattern}</element>')
    assert tuple(validator.validate(parse_xml(f"<doc>{content}</doc>")).valid for _ in range(2)) == (expected, expected)


@pytest.mark.parametrize(
    ("pattern", "content", "expected"),
    [
        pytest.param('<value type="string"></value>', " ", False, id="string-whitespace"),
        pytest.param('<value type="token"></value>', " ", True, id="token-whitespace"),
        pytest.param('<data type="string"/>', "text", True, id="string-text"),
        pytest.param('<data type="token"/>', "<child/>", False, id="data-child"),
        pytest.param('<element name="child"><empty/></element>', "<child/>", True, id="required-child"),
        pytest.param("<empty/>", "text", False, id="empty-text"),
    ],
)
def test_rng_empty_content_controls(pattern: str, content: str, *, expected: bool) -> None:
    validator: Final = RelaxNG(f'<element xmlns="{_RELAXNG_EMPTY_CONTENT_NAMESPACE}" name="doc">{pattern}</element>')
    assert tuple(validator.validate(parse_xml(f"<doc>{content}</doc>")).valid for _ in range(2)) == (expected, expected)


_XSD: Final[str] = "http://www.w3.org/2001/XMLSchema-datatypes"


@pytest.mark.parametrize(
    "form",
    [
        pytest.param(("{}", "<doc>{}</doc>"), id="text"),
        pytest.param(('<attribute name="content">{}</attribute>', '<doc content="{}"/>'), id="attribute"),
    ],
)
@pytest.mark.parametrize(
    ("attributes", "literal", "content", "expected"),
    [
        pytest.param(("", ""), "alpha beta", "alpha beta", True, id="exact"),
        pytest.param(("", ""), "alpha beta", " alpha  beta ", True, id="collapse-spaces"),
        pytest.param(("", ""), "alpha beta", "&#x9;alpha&#xA; beta&#xD;", True, id="xml-whitespace"),
        pytest.param(("", ""), " alpha&#x9; beta ", "alpha beta", True, id="schema-whitespace"),
        pytest.param(("", ""), " alpha&#x9; beta ", " alpha  beta ", True, id="both-whitespace"),
        pytest.param(("", f'datatypeLibrary="{_XSD}"'), "alpha beta", " alpha  beta ", True, id="reset-xsd"),
        pytest.param(("", 'datatypeLibrary="urn:unknown"'), "alpha beta", " alpha  beta ", True, id="reset-unknown"),
        pytest.param(("", ""), "alpha beta", "alphabeta", False, id="missing-space"),
        pytest.param(("", ""), "alpha beta", "alpha gamma", False, id="different-token"),
        pytest.param(("", ""), "alpha beta", "alpha&#xA0;beta", False, id="nonbreaking-space"),
        pytest.param(("", ""), "alpha&#xA0;beta", "alpha&#xA0;beta", True, id="literal-nonbreaking-space"),
        pytest.param(('type="string"', ""), "alpha beta", "alpha beta", True, id="string-exact"),
        pytest.param(('type="string"', ""), "alpha beta", " alpha  beta ", False, id="string-spacing"),
        pytest.param(('type="token"', ""), "alpha beta", " alpha  beta ", True, id="explicit-token"),
        pytest.param(
            ('type="token"', f'datatypeLibrary="{_XSD}"'), "alpha beta", " alpha  beta ", True, id="xsd-token"
        ),
    ],
)
def test_relaxng_value_default_token(
    *, form: tuple[str, str], attributes: tuple[str, str], literal: str, content: str, expected: bool
) -> None:
    wrap, document_template = form
    value: Final = f"<value {attributes[0]}>{literal}</value>"
    pattern: Final = wrap.format(value)
    value_ns = "http://relaxng.org/ns/structure/1.0"
    validator: Final = RelaxNG(f'<element xmlns="{value_ns}" name="doc" {attributes[1]}>{pattern}</element>')
    document: Final = document_template.format(content)
    assert tuple(validator.validate(parse_xml(document)).valid for _ in range(2)) == (expected, expected)


_RELAXNG_ROOT_NAMESPACE: Final[str] = "http://relaxng.org/ns/structure/1.0"


@pytest.mark.parametrize(
    "schema",
    [
        pytest.param("<thisIsJunk/>", id="jing001"),
        pytest.param('<element name="doc"><empty/></element>', id="absent-namespace"),
        pytest.param('<element xmlns="" name="doc"><empty/></element>', id="empty-default-namespace"),
        pytest.param('<element xmlns="urn:other" name="doc"><empty/></element>', id="foreign-default-namespace"),
        pytest.param(
            '<r:element xmlns:r="urn:other" name="doc"><r:empty/></r:element>', id="foreign-prefixed-namespace"
        ),
        pytest.param(
            f'<element xmlns:r="{_RELAXNG_ROOT_NAMESPACE}" name="doc"><empty/></element>', id="unused-structure-prefix"
        ),
        pytest.param('<grammar xmlns="urn:other"><start/></grammar>', id="foreign-grammar"),
        pytest.param('<xml:element name="doc"/>', id="reserved-xml-prefix"),
    ],
)
def test_relaxng_rejects_foreign_root(schema: str) -> None:
    for _ in range(2):
        with pytest.raises(ValueError, match="RELAX NG schema root must use the structure namespace"):
            RelaxNG(schema)


@pytest.mark.parametrize(
    "schema",
    [
        pytest.param(f'<element xmlns="{_RELAXNG_ROOT_NAMESPACE}" name="doc"><empty/></element>', id="default-element"),
        pytest.param(
            f'<r:element xmlns:r="{_RELAXNG_ROOT_NAMESPACE}" name="doc"><r:empty/></r:element>', id="prefixed-element"
        ),
        pytest.param(
            (
                f'<grammar xmlns="{_RELAXNG_ROOT_NAMESPACE}"><start>'
                '<element name="doc"><empty/></element></start></grammar>'
            ),
            id="default-grammar",
        ),
        pytest.param(
            f'<r:grammar xmlns:r="{_RELAXNG_ROOT_NAMESPACE}"><r:start>'
            '<r:element name="doc"><r:empty/></r:element></r:start></r:grammar>',
            id="prefixed-grammar",
        ),
    ],
)
def test_relaxng_structure_root_preserves_validation(schema: str) -> None:
    validator: Final = RelaxNG(schema)
    assert [
        (validator.validate(parse_xml("<doc/>")).valid, validator.validate(parse_xml("<other/>")).valid)
        for _ in range(2)
    ] == [(True, False), (True, False)]


_RELAXNG_DATA_EXCEPT_XSD: Final[str] = "http://www.w3.org/2001/XMLSchema-datatypes"


@pytest.mark.parametrize(
    "form",
    [
        pytest.param(("{}", "<doc>{}</doc>"), id="text"),
        pytest.param(('<attribute name="content">{}</attribute>', '<doc content="{}"/>'), id="attribute"),
    ],
)
@pytest.mark.parametrize(
    ("pattern", "content", "expected"),
    [
        pytest.param(
            '<data type="token"><except><value type="token">x</value></except></data>', "x", False, id="single-excluded"
        ),
        pytest.param(
            '<data type="token"><except><value type="token">x</value></except></data>', "y", True, id="single-allowed"
        ),
        pytest.param(
            '<data type="token"><except><value type="token">x</value>'
            '<value type="token">y</value><value type="token">z</value></except></data>',
            "x",
            False,
            id="multiple-first",
        ),
        pytest.param(
            '<data type="token"><except><value type="token">x</value>'
            '<value type="token">y</value><value type="token">z</value></except></data>',
            "y",
            False,
            id="multiple-middle",
        ),
        pytest.param(
            '<data type="token"><except><value type="token">x</value>'
            '<value type="token">y</value><value type="token">z</value></except></data>',
            "z",
            False,
            id="multiple-last",
        ),
        pytest.param(
            '<data type="token"><except><value type="token">x</value>'
            '<value type="token">y</value><value type="token">z</value></except></data>',
            "xyz",
            True,
            id="multiple-allowed",
        ),
        pytest.param(
            '<data type="token"><except><choice><value type="token">x</value>'
            '<value type="token">y</value></choice></except></data>',
            "y",
            False,
            id="choice-excluded",
        ),
        pytest.param(
            '<data type="token"><except><choice><value type="token">x</value>'
            '<value type="token">y</value></choice></except></data>',
            "z",
            True,
            id="choice-allowed",
        ),
        pytest.param(
            '<data type="token"><except><value type="token">alpha beta</value></except></data>',
            " alpha  beta ",
            False,
            id="token-spacing",
        ),
        pytest.param(
            '<data type="token"><except><value type="string">alpha beta</value></except></data>',
            " alpha  beta ",
            True,
            id="string-spacing",
        ),
        pytest.param(
            '<data type="string"><except><value type="string">alpha beta</value></except></data>',
            "alpha beta",
            False,
            id="string-excluded",
        ),
        pytest.param(
            (
                f'<data datatypeLibrary="{_RELAXNG_DATA_EXCEPT_XSD}" type="integer">'
                '<except><value type="integer">7</value></except></data>'
            ),
            "not-a-number",
            False,
            id="datatype-invalid",
        ),
        pytest.param(
            (
                f'<data datatypeLibrary="{_RELAXNG_DATA_EXCEPT_XSD}" type="integer">'
                '<except><value type="integer">7</value></except></data>'
            ),
            "24",
            True,
            id="datatype-allowed",
        ),
        pytest.param(
            (
                f'<data datatypeLibrary="{_RELAXNG_DATA_EXCEPT_XSD}" type="integer">'
                '<except><value type="integer">7</value></except></data>'
            ),
            "7",
            False,
            id="datatype-excluded",
        ),
        pytest.param(
            f'<data datatypeLibrary="{_RELAXNG_DATA_EXCEPT_XSD}" type="string"><param name="minLength">3</param>'
            '<except><value type="string">abc</value></except></data>',
            "xy",
            False,
            id="facet-invalid",
        ),
        pytest.param(
            f'<data datatypeLibrary="{_RELAXNG_DATA_EXCEPT_XSD}" type="string"><param name="minLength">3</param>'
            '<except><value type="string">abc</value></except></data>',
            "abcd",
            True,
            id="facet-allowed",
        ),
        pytest.param(
            f'<data datatypeLibrary="{_RELAXNG_DATA_EXCEPT_XSD}" type="string"><param name="minLength">3</param>'
            '<except><value type="string">abc</value></except></data>',
            "abc",
            False,
            id="facet-excluded",
        ),
        pytest.param(
            (
                '<data type="token"><except>'
                f'<data datatypeLibrary="{_RELAXNG_DATA_EXCEPT_XSD}" type="integer"/></except></data>'
            ),
            "7",
            False,
            id="excluded-datatype",
        ),
        pytest.param(
            (
                '<data type="token"><except>'
                f'<data datatypeLibrary="{_RELAXNG_DATA_EXCEPT_XSD}" type="integer"/></except></data>'
            ),
            "word",
            True,
            id="excluded-datatype-miss",
        ),
        pytest.param(
            '<data type="token"><except><data type="token"><except><value type="token">x</value>'
            "</except></data></except></data>",
            "x",
            True,
            id="nested-allowed",
        ),
        pytest.param(
            '<data type="token"><except><data type="token"><except><value type="token">x</value>'
            "</except></data></except></data>",
            "y",
            False,
            id="nested-excluded",
        ),
        pytest.param('<data type="token"/>', "x", True, id="without-exclusion"),
        pytest.param(
            '<data type="token"><except>\n<!--annotation--><value type="token">x</value>\n</except></data>',
            "x",
            False,
            id="comments",
        ),
        pytest.param(
            '<data type="token"><except><note xmlns="urn:annotation"/><value type="token">x</value></except></data>',
            "x",
            False,
            id="foreign-annotation",
        ),
    ],
)
def test_relaxng_data_except(*, form: tuple[str, str], pattern: str, content: str, expected: bool) -> None:
    wrap, document_template = form
    body: Final = wrap.format(pattern)
    validator: Final = RelaxNG(f'<element xmlns="{"http://relaxng.org/ns/structure/1.0"}" name="doc">{body}</element>')
    document: Final = document_template.format(content)
    assert tuple(validator.validate(parse_xml(document)).valid for _ in range(2)) == (expected, expected)


_RELAXNG_NUMERIC_BOUNDS_NAMESPACE: Final[str] = "http://relaxng.org/ns/structure/1.0"
_LIBRARY: Final[str] = "http://www.w3.org/2001/XMLSchema-datatypes"


@pytest.mark.parametrize("datatype", ["integer", "decimal", "double"])
@pytest.mark.parametrize(
    "form",
    [
        pytest.param(("{}", "<doc>{}</doc>"), id="text"),
        pytest.param(('<attribute name="value">{}</attribute>', '<doc value="{}"/>'), id="attribute"),
    ],
)
@pytest.mark.parametrize(
    ("facet", "value", "expected"),
    [
        pytest.param("minInclusive", "0", False, id="min-inclusive-below"),
        pytest.param("minInclusive", "1", True, id="min-inclusive-equal"),
        pytest.param("minInclusive", "2", True, id="min-inclusive-above"),
        pytest.param("maxInclusive", "0", True, id="max-inclusive-below"),
        pytest.param("maxInclusive", "1", True, id="max-inclusive-equal"),
        pytest.param("maxInclusive", "2", False, id="max-inclusive-above"),
        pytest.param("minExclusive", "0", False, id="min-exclusive-below"),
        pytest.param("minExclusive", "1", False, id="min-exclusive-equal"),
        pytest.param("minExclusive", "2", True, id="min-exclusive-above"),
        pytest.param("maxExclusive", "0", True, id="max-exclusive-below"),
        pytest.param("maxExclusive", "1", False, id="max-exclusive-equal"),
        pytest.param("maxExclusive", "2", False, id="max-exclusive-above"),
        pytest.param("minInclusive", "invalid", False, id="invalid-lexical"),
    ],
)
def test_rng_numeric_bound(datatype: str, facet: str, value: str, *, form: tuple[str, str], expected: bool) -> None:
    wrap, document_template = form
    pattern: Final = f'<data type="{datatype}"><param name="{facet}">1</param></data>'
    validator: Final = RelaxNG(
        f'<element xmlns="{_RELAXNG_NUMERIC_BOUNDS_NAMESPACE}" datatypeLibrary="{_LIBRARY}" name="doc">'
        f"{wrap.format(pattern)}</element>"
    )
    document: Final = document_template.format(value)
    assert tuple(validator.validate(parse_xml(document)).valid for _ in range(2)) == (expected, expected)


@pytest.mark.parametrize("facet", ["minInclusive", "maxInclusive", "minExclusive", "maxExclusive"])
def test_rng_numeric_bound_nan(facet: str) -> None:
    validator: Final = RelaxNG(
        f'<element xmlns="{_RELAXNG_NUMERIC_BOUNDS_NAMESPACE}" datatypeLibrary="{_LIBRARY}" name="doc">'
        f'<data type="double"><param name="{facet}">1</param></data></element>'
    )
    assert tuple(validator.validate(parse_xml("<doc>NaN</doc>")).valid for _ in range(2)) == (False, False)


@pytest.mark.parametrize(
    ("datatype", "params", "value", "expected"),
    [
        pytest.param("double", "", "NaN", True, id="unbounded-nan"),
        pytest.param("double", "", "INF", True, id="unbounded-positive-infinity"),
        pytest.param("double", "", "-INF", True, id="unbounded-negative-infinity"),
        pytest.param("double", "", "2", True, id="unbounded-finite"),
        pytest.param("double", "", "invalid", False, id="unbounded-invalid"),
        pytest.param(
            "double",
            '<param name="minInclusive">0</param><param name="maxInclusive">1</param>',
            "0.5",
            True,
            id="two-bounds-inside",
        ),
        pytest.param(
            "double",
            '<param name="minInclusive">0</param><param name="maxInclusive">1</param>',
            "INF",
            False,
            id="two-bounds-positive-infinity",
        ),
        pytest.param(
            "double",
            '<param name="minInclusive">0</param><param name="maxInclusive">1</param>',
            "-INF",
            False,
            id="two-bounds-negative-infinity",
        ),
        pytest.param("double", '<param name="maxInclusive">1</param>', "1e0", True, id="exponent-equal"),
        pytest.param("double", '<param name="maxInclusive">1</param>', " 0.5 ", True, id="normalized-space"),
        pytest.param(
            "date",
            '<param name="maxExclusive">2024-12-31</param>',
            "2024-01-01",
            True,
            id="date-keeps-calendar-comparison",
        ),
    ],
)
def test_rng_numeric_bound_controls(datatype: str, params: str, value: str, *, expected: bool) -> None:
    validator: Final = RelaxNG(
        f'<element xmlns="{_RELAXNG_NUMERIC_BOUNDS_NAMESPACE}" datatypeLibrary="{_LIBRARY}" name="doc">'
        f'<data type="{datatype}">{params}</data></element>'
    )
    assert tuple(validator.validate(parse_xml(f"<doc>{value}</doc>")).valid for _ in range(2)) == (expected, expected)
