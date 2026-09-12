from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Final, cast

import pytest
from bench.operations import INPUTS

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


def test_prefix_resolution_skips_a_decoy_attribute() -> None:
    # the attribute is exactly as long as "xmlns:" plus the prefix but is not a namespace declaration, so the
    # resolver's name comparison has to reject it rather than matching on length alone
    schema = '<x:element xmlnsyy="decoy" xmlns:x="http://relaxng.org/ns/structure/1.0" name="r"><x:text/></x:element>'
    assert RelaxNG(schema).validate(parse_xml("<r>hi</r>")).valid


def rng_ok(schema: str, xml: str) -> bool:
    return RelaxNG(schema).validate(parse_xml(xml)).valid


def rwrap(body: str) -> str:
    return f'<element name="doc" xmlns="{R}">{body}</element>'


def rgrammar(body: str, attrs: str = "") -> str:
    return f'<grammar xmlns="{R}" {attrs}>{body}</grammar>'


def test_rng_group_with_empty_second() -> None:
    assert rng_ok(rwrap("<group><text/><empty/></group>"), "<doc>x</doc>")


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
    assert not rng_ok(rgrammar(f'<start><ref name="missing"/></start>{defines}'), "<r/>")


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


@pytest.mark.parametrize("name", ["café", "中文", "𝔸bc"], ids=["two-byte", "three-byte", "four-byte"])  # ruff:ignore[ambiguous-unicode-character-string]
def test_rng_non_ascii_attribute_name(name: str) -> None:
    schema = rwrap(f'<attribute name="{name}"><text/></attribute><text/>')
    assert rng_ok(schema, f'<doc {name}="1">x</doc>')


def test_rng_xml_prefixed_name() -> None:
    schema = f'<grammar xmlns="{R}"><start><element name="xml:space"><text/></element></start></grammar>'
    assert rng_ok(schema, "<xml:space>preserve</xml:space>")


def test_rng_element_whitespace_before_nameclass() -> None:
    schema = f'<grammar xmlns="{R}"><start>\n  <element>\n    <anyName/>\n    <text/>\n  </element>\n</start></grammar>'
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


def test_rng_unknown_pattern_element() -> None:
    schema = f'<grammar xmlns="{R}"><start><externalRef href="other.rng"/></start></grammar>'
    assert not rng_ok(schema, "<anything/>")


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


def test_rng_forbidden_attribute_recursion_is_guarded() -> None:
    # left-recursion through an attribute is forbidden by RELAX NG; the engine must not
    # loop forever on it -- it treats the recursive branch as unmatchable.
    schema = rgrammar(
        '<start><element name="r"><ref name="x"/></element></start>'
        '<define name="x"><choice><empty/>'
        '<group><attribute name="a"><text/></attribute><ref name="x"/></group>'
        "</choice></define>"
    )
    assert RelaxNG(schema).validate(parse_xml('<r a="1"/>')).valid


def test_rng_forbidden_text_recursion_is_guarded() -> None:
    schema = rgrammar(
        '<start><element name="r"><ref name="x"/></element></start>'
        '<define name="x"><choice><empty/><group><ref name="x"/><text/></group></choice></define>'
    )
    assert RelaxNG(schema).validate(parse_xml("<r>hi</r>")).valid


def test_rng_cdata_and_prefixed_attribute() -> None:
    schema = rwrap('<attribute name="p:id" xmlns:p="urn:a"><text/></attribute><text/>')
    assert rng_ok(schema, '<doc xmlns:p="urn:a" p:id="1"><![CDATA[body]]></doc>')


def test_rng_data_param_without_name() -> None:
    schema = (
        f'<grammar xmlns="{R}" datatypeLibrary="{DT}"><start>'
        '<element name="e"><data type="string"><param>ignored</param></data></element></start></grammar>'
    )
    assert rng_ok(schema, "<e>hello</e>")


def test_rng_default_library_string_and_unknown_xsd_type() -> None:
    # default datatype library: "string" (not token) exercises the DT_STRING branch
    assert rng_ok(rwrap('<element name="s"><data type="string"/></element>'), "<doc><s>x</s></doc>")
    # an explicit non-XSD datatype library falls back to the built-in string/token library
    assert rng_ok(
        f'<element name="s" xmlns="{R}" datatypeLibrary=""><data type="token"/></element>',
        "<s>x</s>",
    )
    # the XSD library with an unrecognized type name falls back to string
    assert rng_ok(
        f'<element name="s" xmlns="{R}" datatypeLibrary="{DT}"><data type="madeUp"/></element>',
        "<s>anything</s>",
    )


def test_rng_element_without_name_class_matches_any() -> None:
    # a malformed element with neither @name nor a name-class child recovers as anyName
    schema = f'<grammar xmlns="{R}"><start><element/></start></grammar>'
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
        f'<grammar xmlns="{R}" datatypeLibrary="{DT}"><start>'
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
        f'<grammar xmlns="{R}"><start><element name="r"><ref name="x"/></element></start>'
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
        f'<grammar xmlns="{R}" datatypeLibrary="{DT}"><start>'
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
        f'<grammar xmlns="{R}"><start><element name="r"><ref name="x"/></element></start>'
        "<define><empty/></define>"  # a nameless define is skipped when resolving refs
        '<define name="x"><text/></define></grammar>'
    )
    assert rng_ok(schema, "<r>body</r>")


def test_rng_attribute_whitespace_value_against_data() -> None:
    schema = (
        f'<grammar xmlns="{R}" datatypeLibrary="{DT}"><start>'
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
        f'<grammar xmlns="{R}"><start><element name="r"><ref name="b"/></element></start>'
        '<define name="a"><empty/></define>'
        '<define name="b"><text/></define>'
        '<define name="c"><empty/></define>'
        '<define name="b" combine="choice"><text/></define></grammar>'
    )
    assert rng_ok(schema, "<r>x</r>")


@pytest.mark.parametrize("name", ["node", "xml:node"], ids=["unqualified", "xml-prefix"])
def test_shared_schema_validates_distinct_documents(name: str) -> None:
    schema: Final = RelaxNG(
        '<grammar xmlns="http://relaxng.org/ns/structure/1.0"><start><ref name="node"/></start>'
        f'<define name="node"><element><name>{name}</name><zeroOrMore><ref name="node"/></zeroOrMore>'
        "</element></define></grammar>"
    )
    documents: Final = [
        parse_xml(f"<{name}><{name}/></{name}>" if index % 2 == 0 else f"<{name}><other/></{name}>")
        for index in range(32)
    ]
    with ThreadPoolExecutor(max_workers=4) as executor:
        results: Final = list(executor.map(schema.validate, documents))
    assert [result.valid for result in results] == [index % 2 == 0 for index in range(32)]


@pytest.mark.parametrize("index", range(4), ids=["optional-group", "optional-interleave", "small-group", "recursive"])
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
        pytest.param(f'<element name="a" xmlns="{R}"><text/></element>', "<a>hi</a>", id="text-valid"),
        pytest.param(
            f'<element name="a" xmlns="{R}"><element name="b"><text/></element></element>',
            "<a><c>x</c></a>",
            id="wrong-child-invalid",
        ),
        pytest.param(
            f'<element name="p" xmlns="{R}"><interleave>'
            '<element name="a"><text/></element><element name="b"><text/></element></interleave></element>',
            "<p><b>2</b><a>1</a></p>",
            id="interleave-any-order-valid",
        ),
        pytest.param(
            f'<element name="p" xmlns="{R}"><interleave>'
            '<element name="a"><text/></element><element name="b"><text/></element></interleave></element>',
            "<p><a>1</a></p>",
            id="interleave-missing-invalid",
        ),
        pytest.param(
            f'<element name="r" xmlns="{R}"><oneOrMore><element name="i"><text/></element></oneOrMore></element>',
            "<r><i>1</i><i>2</i></r>",
            id="oneOrMore-valid",
        ),
        pytest.param(
            f'<element name="r" xmlns="{R}"><oneOrMore><element name="i"><text/></element></oneOrMore></element>',
            "<r/>",
            id="oneOrMore-empty-invalid",
        ),
        pytest.param(
            f'<element name="e" xmlns="{R}" datatypeLibrary="http://www.w3.org/2001/XMLSchema-datatypes">'
            '<data type="int"/></element>',
            "<e>7</e>",
            id="data-int-valid",
        ),
        pytest.param(
            f'<element name="e" xmlns="{R}" datatypeLibrary="http://www.w3.org/2001/XMLSchema-datatypes">'
            '<data type="int"/></element>',
            "<e>seven</e>",
            id="data-int-invalid",
        ),
        pytest.param(
            f'<element name="r" xmlns="{R}"><attribute name="id"><text/></attribute><text/></element>',
            '<r id="1">x</r>',
            id="attribute-valid",
        ),
        pytest.param(
            f'<element name="r" xmlns="{R}"><attribute name="id"><text/></attribute><text/></element>',
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
@pytest.mark.parametrize("index", range(4), ids=["optional-group", "optional-interleave", "small-group", "recursive"])
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
        pytest.param("choice", '<ref name="left"/>', "<empty/>", True, id="recursive-choice-empty"),
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
    schema = RelaxNG(wrap(f'<element name="value"><data type="int" datatypeLibrary="{DT}"/></element>'))
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
