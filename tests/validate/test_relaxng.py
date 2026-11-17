from __future__ import annotations

import json
import subprocess  # ruff:ignore[suspicious-subprocess-import]  # the tests run a fixed command in a subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from functools import cache, partial
from typing import TYPE_CHECKING, Final, cast

import pytest
from bench.operations import INPUTS

from turbohtml import parse_xml
from turbohtml.validate import RelaxNG, SchemaValidationError, ValidationResult

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from traced_bytes import PeakBytes
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


@pytest.mark.parametrize(
    ("datatype", "text", "ok"),
    [
        pytest.param("ID", "abc", True, id="ID"),
        pytest.param("ID", "1abc", False, id="ID-digit-start"),
        pytest.param("NOTATION", "a:b:c", False, id="NOTATION-two-colons"),
        pytest.param("gYearMonth", "2000-12", True, id="gYearMonth"),
        pytest.param("gYearMonth", "2000-13", False, id="gYearMonth-month"),
        pytest.param("gDay", "---31", True, id="gDay"),
        pytest.param("gDay", "---32", False, id="gDay-day"),
    ],
)
def test_data_xsd_scalar_builtins(datatype: str, text: str, ok: bool) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]  # a pytest parametrize value, not a boolean-trap call site
    schema = grammar(
        f'<start><element name="n"><data type="{datatype}"/></element></start>', attrs=f'datatypeLibrary="{_DT}"'
    )
    assert check(schema, f"<n>{text}</n>").valid is ok


def grammar(body: str, *, attrs: str = "") -> str:
    return f'<grammar xmlns="{_RELAXNG_NS}" {attrs}>{body}</grammar>'


@pytest.mark.parametrize(
    ("datatype", "params", "value", "expected"),
    [
        pytest.param("NMTOKENS", "", "a b", True, id="nmtokens"),
        pytest.param("NMTOKENS", "", "", False, id="nmtokens-needs-an-item"),
        pytest.param("NMTOKENS", "", "a 1!", False, id="nmtokens-item-rejects"),
        pytest.param("IDREFS", "", "a 1b", False, id="idrefs-item-is-an-ncname"),
        pytest.param("ENTITIES", "", "a b", True, id="entities"),
        pytest.param("NMTOKENS", '<param name="maxLength">2</param>', "a b c", False, id="max-length-counts-items"),
        pytest.param("NMTOKENS", '<param name="maxLength">2</param>', "ab", True, id="max-length-one-item"),
        pytest.param("NMTOKENS", '<param name="minLength">2</param>', "ab", False, id="min-length-counts-items"),
    ],
)
def test_data_list_types(datatype: str, params: str, value: str, *, expected: bool) -> None:
    schema = grammar(
        f'<start><element name="r"><data type="{datatype}">{params}</data></element></start>',
        attrs=f'datatypeLibrary="{_DT}"',
    )
    assert check(schema, f"<r>{value}</r>").valid is expected


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


def test_data_param_repeated_patterns() -> None:
    # equal pattern texts compile once, and each param that repeats one finds that compiled form
    def data(*patterns: str) -> str:
        params = "".join(f'<param name="pattern">{pattern}</param>' for pattern in patterns)
        return f'<data type="string">{params}</data>'

    schema = grammar(
        f'<start><element name="r"><element name="s">{data("[a-c]+", "a.*", "[a-c]+", "a.*", ".{2}")}</element>'
        f'<element name="e">{data("", "a?", "")}</element></element></start>',
        attrs=f'datatypeLibrary="{_DT}"',
    )
    assert check(schema, "<r><s>ab</s><e/></r>").valid
    assert not check(schema, "<r><s>bb</s><e/></r>").valid  # a.*
    assert not check(schema, "<r><s>ab</s><e>a</e></r>").valid  # the empty pattern


def test_data_param_pattern_too_complex_raises() -> None:
    pattern = f"{'(' * 50000}a{')' * 50000}"
    schema = grammar(
        f'<start><element name="s"><data type="string"><param name="pattern">{pattern}</param></data>'
        "</element></start>",
        attrs=f'datatypeLibrary="{_DT}"',
    )
    with pytest.raises(ValueError, match="nests groups deeper than 250 levels at offset 250"):
        RelaxNG(schema)


def test_data_param_patterns_share_the_state_budget() -> None:
    data = "".join(
        f'<element name="{name}"><data type="string"><param name="pattern">a{{{count}}}</param></data></element>'
        for name, count in (("s", 1500000), ("t", 600000))
    )
    schema = grammar(f'<start><element name="r">{data}</element></start>', attrs=f'datatypeLibrary="{_DT}"')
    with pytest.raises(ValueError, match=r"^schema patterns need more than 2000000 NFA states between them; lower"):
        RelaxNG(schema)


def test_data_param_class_of_many_ranges_matches_in_linear_steps(work_steps: WorkSteps) -> None:
    def build(members: int) -> Callable[[], object]:
        pattern: Final = f"[{'a-c' * members}]*"
        schema: Final = RelaxNG(
            grammar(
                f'<start><element name="s"><data type="string"><param name="pattern">{pattern}</param></data>'
                "</element></start>",
                attrs=f'datatypeLibrary="{_DT}"',
            )
        )
        document: Final = parse_xml(f"<s>{'b' * members}</s>")
        return partial(schema.is_valid, document)

    work_steps.assert_linear(build, (500, 2000))


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
        pytest.param(
            "<choice><name>a</name><choice><nsName ns=''><except><name>skip</name></except></nsName></choice></choice>",
            "<doc><ok>x</ok></doc>",
            True,
            id="nested-choice-except-ok",
        ),
        pytest.param(
            "<choice><name>a</name><choice><nsName ns=''><except><name>skip</name></except></nsName></choice></choice>",
            "<doc><skip>x</skip></doc>",
            False,
            id="nested-choice-except-hit",
        ),
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


def test_prefix_resolution_skips_a_same_length_declaration_of_another_prefix() -> None:
    # xmlns:z is as long as xmlns:x and binds another prefix, so the resolver has to compare names, not lengths
    schema = (
        '<x:element xmlns:z="urn:decoy" xmlns:x="http://relaxng.org/ns/structure/1.0" name="r"><x:text/></x:element>'
    )
    assert RelaxNG(schema).validate(parse_xml("<r>hi</r>")).valid


def test_default_namespace_skips_a_same_length_decoy_attribute() -> None:
    # label is as long as xmlns and is not a namespace declaration, so the default namespace is found past it
    schema = (
        '<grammar label="v" xmlns="http://relaxng.org/ns/structure/1.0">'
        '<start><element name="r"><text/></element></start></grammar>'
    )
    assert RelaxNG(schema).validate(parse_xml("<r>hi</r>")).valid


@pytest.mark.parametrize(
    ("schema", "xml", "expected"),
    [
        pytest.param(
            grammar(
                '<start><element name="root" ns="urn:a"><element><name ns="urn:b">c</name><empty/></element>'
                "</element></start>"
            ),
            '<root xmlns="urn:a"><c xmlns="urn:b"/></root>',
            True,
            id="redeclared-default",
        ),
        pytest.param(
            grammar(
                '<start><element name="root" ns="urn:a"><element><name ns="urn:b">c</name><empty/></element>'
                "</element></start>"
            ),
            '<root xmlns="urn:a"><c/></root>',
            False,
            id="redeclared-default-not-inherited",
        ),
        pytest.param(
            grammar(
                '<start><element name="root" ns="urn:a"><element name="wrap" ns=""><element name="c">'
                "<empty/></element></element></element></start>"
            ),
            '<root xmlns="urn:a"><wrap xmlns=""><c/></wrap></root>',
            True,
            id="undeclared-default",
        ),
        pytest.param(
            grammar(
                '<start><element name="root"><element name="p:x" xmlns:p="urn:b"><empty/></element>'
                '<element name="p:y" xmlns:p="urn:a"><empty/></element></element></start>'
            ),
            '<root xmlns:p="urn:a"><p:x xmlns:p="urn:b"/><p:y/></root>',
            True,
            id="prefix-restored-after-sibling",
        ),
        pytest.param(
            grammar(
                '<start><element name="root"><element name="p:x" xmlns:p="urn:b"><empty/></element>'
                '<element name="p:y" xmlns:p="urn:a"><empty/></element></element></start>'
            ),
            '<root xmlns:p="urn:a"><p:x/><p:y xmlns:p="urn:b"/></root>',
            False,
            id="prefix-redeclared-after-sibling",
        ),
    ],
)
def test_namespace_declarations_scope_to_their_element(schema: str, xml: str, *, expected: bool) -> None:
    assert check(schema, xml).valid is expected


def test_colon_attribute_is_no_declaration() -> None:
    # the parser rejects a bare "xmlns:", but the DOM stores one, and it must not bind the default namespace
    document: Final = parse_xml("<root><c/></root>")
    root: Final = document.root
    assert root is not None
    root.attrs["xmlns:"] = "urn:a"
    schema: Final = grammar('<start><element name="root"><element name="c"><empty/></element></element></start>')
    assert RelaxNG(schema).validate(document).valid


def test_element_below_the_root_sees_declarations_above_it() -> None:
    # validating an element on its own still resolves its name against the declaration on the document root
    element: Final = parse_xml('<a xmlns="urn:x"><b>v</b></a>').children[0].children[0]
    schema: Final = grammar('<start><element name="b" ns="urn:x"><text/></element></start>')
    assert RelaxNG(schema).validate(element).valid


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
            rgrammar(
                '<start><element name="r"><ref name="a"/></element></start>'
                '<define name="a"><optional><element name="x"><ref name="b"/></element></optional></define>'
                '<define name="a" combine="choice"><ref name="b"/></define>'
                '<define name="b"><optional><ref name="a"/></optional></define>'
            ),
            "define 'a' references itself with no element in between",
            id="4.19-loop-first-met-inside-an-element",
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
            rwrap('<group><ref name="missing"/></group><attribute name="a"/>'),
            "<ref> has no matching define",
            id="undefined-ref-beside-an-attribute",
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
    assert isinstance(RelaxNG(_chained_unions(190)), RelaxNG)
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
    work_steps.assert_linear(lambda n: partial(RelaxNG, _interleave_chain(n)), (45, 180), exponent=1.3)


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


def _crossed_chain(leaf: str, start: str, combine: str, *, count: int, extra: str = "") -> str:
    defines = "".join(
        f'<define name="d{index}"><{combine}><ref name="d{index + 1}"/><ref name="e{index + 1}"/></{combine}></define>'
        f'<define name="e{index}"><{combine}><ref name="e{index + 1}"/><ref name="d{index + 1}"/></{combine}></define>'
        for index in range(count)
    )
    return rgrammar(
        f'<start><element name="r">{start}</element></start>{defines}'
        f'<define name="d{count}">{leaf}</define><define name="e{count}">{leaf}</define>{extra}'
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


def _named_attributes(count: int) -> str:
    return "".join(f'<attribute name="a{index}"/>' for index in range(count))


def _namespace_attributes(count: int) -> str:
    return "".join(
        f'<oneOrMore><attribute><nsName ns="urn:u{index}"/></attribute></oneOrMore>' for index in range(count)
    )


_OVERLAPPING_ATTRIBUTES: Final[str] = "attributes whose names overlap"
_UNBOUNDED_ATTRIBUTE: Final[str] = "anyName or nsName attribute must be inside oneOrMore or zeroOrMore"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        pytest.param('<attribute name="bar"/><attribute name="bar"/>', _OVERLAPPING_ATTRIBUTES, id="same-name-twice"),
        pytest.param(
            '<oneOrMore><attribute><nsName ns="urn:a"/></attribute></oneOrMore>' * 2,
            _OVERLAPPING_ATTRIBUTES,
            id="nsname-twice",
        ),
        pytest.param(
            '<attribute name="bar"/><oneOrMore><attribute><anyName/></attribute></oneOrMore>',
            _OVERLAPPING_ATTRIBUTES,
            id="anyname-after-named",
        ),
        pytest.param(
            '<interleave><attribute name="bar"/><attribute name="bar"/></interleave>',
            _OVERLAPPING_ATTRIBUTES,
            id="interleave-same-name",
        ),
        pytest.param(
            "<oneOrMore><attribute><anyName/></attribute></oneOrMore>"
            "<oneOrMore><attribute><nsName/></attribute></oneOrMore>",
            _OVERLAPPING_ATTRIBUTES,
            id="two-wildcards-in-group",
        ),
        pytest.param("<attribute><anyName/></attribute>", _UNBOUNDED_ATTRIBUTE, id="anyname-outside-one-or-more"),
        pytest.param(
            '<optional><attribute><nsName ns="urn:a"/></attribute></optional>',
            _UNBOUNDED_ATTRIBUTE,
            id="optional-nsname",
        ),
        pytest.param(
            '<zeroOrMore><element name="x"><attribute><anyName/></attribute></element></zeroOrMore>',
            _UNBOUNDED_ATTRIBUTE,
            id="anyname-in-element-under-repetition",
        ),
        pytest.param(
            f'{_named_attributes(5)}<attribute name="a0"/>', _OVERLAPPING_ATTRIBUTES, id="same-name-after-many"
        ),
        pytest.param(
            f"{_named_attributes(5)}<oneOrMore><attribute><anyName/></attribute></oneOrMore>",
            _OVERLAPPING_ATTRIBUTES,
            id="anyname-after-many",
        ),
        pytest.param(
            f"{_named_attributes(4)}<attribute><choice><name>x</name><name>a2</name></choice></attribute>",
            _OVERLAPPING_ATTRIBUTES,
            id="name-choice-after-many",
        ),
        pytest.param(
            f'<choice>{_named_attributes(6)}</choice><attribute name="a5"/>',
            _OVERLAPPING_ATTRIBUTES,
            id="wide-choice-then-same-name",
        ),
    ],
)
def test_rng_attribute_restrictions_reject_at_compile(body: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        RelaxNG(rwrap(body))


@pytest.mark.parametrize(
    ("body", "xml", "valid"),
    [
        pytest.param(
            '<attribute name="bar"/><oneOrMore><attribute><anyName><except><name>bar</name></except></anyName>'
            "</attribute></oneOrMore>",
            '<doc bar="1" baz="2"/>',
            True,
            id="anyname-except-the-named-attribute",
        ),
        pytest.param(
            '<attribute name="bar"/><oneOrMore><attribute><anyName><except><name>bar</name></except></anyName>'
            "</attribute></oneOrMore>",
            '<doc bar="1"/>',
            False,
            id="anyname-except-needs-one-more",
        ),
        pytest.param(
            '<oneOrMore><attribute><nsName ns="http://a"/></attribute></oneOrMore>'
            '<oneOrMore><attribute><nsName ns="http://b"/></attribute></oneOrMore>',
            '<doc xmlns:p="http://a" xmlns:q="http://b" p:x="1" q:y="2"/>',
            True,
            id="namespaces-differ",
        ),
        pytest.param(
            '<choice><attribute name="bar"/><attribute name="bar"/></choice>',
            '<doc bar="1"/>',
            True,
            id="choice-same-name",
        ),
        pytest.param(
            "<oneOrMore><group><attribute><anyName/></attribute></group></oneOrMore>",
            '<doc x="1" y="2"/>',
            True,
            id="group-under-one-or-more",
        ),
        pytest.param(
            _named_attributes(6), '<doc a0="1" a1="1" a2="1" a3="1" a4="1" a5="1"/>', True, id="many-distinct"
        ),
        pytest.param(f"<choice>{_named_attributes(6)}</choice>", '<doc a3="1"/>', True, id="wide-choice"),
        pytest.param(
            "<attribute><choice><name>a</name><name>b</name></choice></attribute>",
            '<doc b="1"/>',
            True,
            id="name-choice-outside-one-or-more",
        ),
        pytest.param(
            '<attribute name="a"/><div name="a"/><parentRef name="a"/>',
            '<doc a="1"/>',
            False,
            id="stray-div-and-parent-ref",
        ),
        pytest.param(
            '<oneOrMore><attribute><nsName ns="urn:u"/></attribute></oneOrMore><attribute name="z"/>',
            '<doc xmlns:p="urn:u" p:a="1" z="2"/>',
            True,
            id="wildcard-then-name",
        ),
        pytest.param(
            f'<attribute name="z"/><group>{_named_attributes(5)}</group>',
            '<doc z="1" a0="1" a1="1" a2="1" a3="1" a4="1"/>',
            True,
            id="name-then-wide-group",
        ),
        pytest.param(
            f'<choice>{_namespace_attributes(5)}</choice><attribute name="z"/>',
            '<doc xmlns:p="urn:u1" p:a="1" z="2"/>',
            True,
            id="wide-wildcard-choice-then-name",
        ),
        pytest.param(
            f'<attribute name="z"/><choice>{_namespace_attributes(5)}</choice>',
            '<doc xmlns:p="urn:u1" p:a="1" z="2"/>',
            True,
            id="name-then-wide-wildcard-choice",
        ),
    ],
)
def test_rng_attribute_restrictions_allow_spec_shapes(body: str, xml: str, *, valid: bool) -> None:
    assert rng_ok(rwrap(body), xml) is valid


def test_rng_attribute_check_leaves_missing_ref_to_build() -> None:
    with pytest.raises(ValueError, match="no matching define"):
        RelaxNG(rwrap('<attribute name="a"/><ref name="missing"/>'))


def test_rng_shared_attribute_define_in_wide_scopes() -> None:
    wide = '<attribute name="x"/><attribute name="y"/><attribute name="z"/><ref name="common"/>'
    schema = rgrammar(
        f'<start><element name="doc">{wide}<element name="item">{wide}</element></element></start>'
        '<define name="common"><attribute name="id"/><attribute name="lang"/></define>'
    )
    attributes = 'x="1" y="2" z="3" id="4" lang="5"'
    assert rng_ok(schema, f"<doc {attributes}><item {attributes}/></doc>")


def test_rng_attribute_overlap_through_define_scanned_earlier() -> None:
    schema = rgrammar(
        '<start><element name="doc"><choice><ref name="common"/><ref name="other"/></choice>'
        '<element name="item"><ref name="common"/><ref name="other"/></element></element></start>'
        '<define name="common"><attribute name="id"/></define><define name="other"><attribute name="id"/></define>'
    )
    with pytest.raises(ValueError, match=_OVERLAPPING_ATTRIBUTES):
        RelaxNG(schema)


def test_rng_attribute_check_compiles_linearly(work_steps: WorkSteps) -> None:
    work_steps.assert_linear(lambda n: partial(RelaxNG, rwrap(_named_attributes(n))), (100, 200))


def test_rng_attribute_through_ref_overlaps_sibling() -> None:
    schema = rgrammar(
        '<start><element name="doc"><attribute name="bar"/><ref name="again"/></element></start>'
        '<define name="again"><attribute name="bar"/></define>'
    )
    with pytest.raises(ValueError, match=_OVERLAPPING_ATTRIBUTES):
        RelaxNG(schema)


def test_rng_attribute_overlap_inside_referenced_define_rejected() -> None:
    schema = rgrammar(
        '<start><element name="doc"><ref name="pair"/></element></start>'
        '<define name="pair"><group><attribute name="bar"/><attribute name="bar"/></group></define>'
    )
    with pytest.raises(ValueError, match=_OVERLAPPING_ATTRIBUTES):
        RelaxNG(schema)


_COMBINED_ATTRIBUTES: Final[str] = (
    '<start><element name="doc"><ref name="pair"/></element></start>'
    '<define name="pair"><attribute name="bar"/></define>'
    '<define name="pair" combine="interleave"><attribute name="{}"/></define>'
)


def test_rng_interleave_combined_attributes_with_distinct_names_validate() -> None:
    assert rng_ok(rgrammar(_COMBINED_ATTRIBUTES.format("baz")), '<doc bar="1" baz="2"/>')


@pytest.mark.parametrize(
    "schema",
    [
        pytest.param(_COMBINED_ATTRIBUTES.format("bar"), id="combine-on-later-part"),
        pytest.param(
            '<start><element name="doc"><ref name="pair"/></element></start>'
            '<define name="pair" combine="interleave"><attribute name="bar"/></define>'
            '<define name="pair"><attribute name="bar"/></define>',
            id="combine-on-first-part",
        ),
    ],
)
def test_rng_interleave_combined_attributes_with_same_name_rejected(schema: str) -> None:
    with pytest.raises(ValueError, match=_OVERLAPPING_ATTRIBUTES):
        RelaxNG(rgrammar(schema))


def test_rng_combined_part_with_overlapping_group_rejected() -> None:
    schema = rgrammar(
        '<start><element name="doc"><ref name="pair"/></element></start>'
        '<define name="pair"><attribute name="bar"/></define>'
        '<define name="pair" combine="interleave"><group><attribute name="x"/><attribute name="x"/></group></define>'
    )
    with pytest.raises(ValueError, match=_OVERLAPPING_ATTRIBUTES):
        RelaxNG(schema)


def test_rng_anyname_attribute_through_ref_follows_one_or_more() -> None:
    define = '<define name="any"><attribute><anyName/></attribute></define>'
    repeated = f'<start><element name="doc"><oneOrMore><ref name="any"/></oneOrMore></element></start>{define}'
    assert rng_ok(rgrammar(repeated), '<doc x="1" y="2"/>')
    bare = f'<start><element name="doc"><ref name="any"/></element></start>{define}'
    with pytest.raises(ValueError, match=_UNBOUNDED_ATTRIBUTE):
        RelaxNG(rgrammar(bare))


def test_rng_anyname_attribute_define_reused_inside_zero_or_more_validates() -> None:
    assert rng_ok(_reused_any_attribute('<zeroOrMore><ref name="any"/></zeroOrMore>'), '<doc x="1"/>')


def _reused_any_attribute(second: str) -> str:
    # the second choice branch takes the record the first one built
    return rgrammar(
        f'<start><element name="doc"><choice><oneOrMore><ref name="any"/></oneOrMore>{second}</choice></element>'
        '</start><define name="any"><attribute><anyName/></attribute></define>'
    )


def test_rng_anyname_attribute_define_reused_bare_rejected() -> None:
    with pytest.raises(ValueError, match=_UNBOUNDED_ATTRIBUTE):
        RelaxNG(_reused_any_attribute('<ref name="any"/>'))


def test_rng_distinct_attributes_scale_linearly(work_steps: WorkSteps) -> None:
    validator = RelaxNG(rwrap("".join(f'<optional><attribute name="k{index}"/></optional>' for index in range(60))))
    work_steps.assert_linear(lambda n: partial(validator.validate, parse_xml(_distinct_attributes(n))), (20, 40))


def _distinct_attributes(count: int) -> str:
    attributes = " ".join(f'k{index}="1"' for index in range(count))
    return f"<doc {attributes}/>"


@pytest.mark.parametrize("tail", ["b", "c"])
def test_rng_repeated_start_tag_hits_keep_each_tail(tail: str) -> None:
    assert rng_ok(_two_tail_ladder(11), f"<r><a/><{tail}/></r>")


def _two_tail_ladder(count: int) -> str:
    defines = "".join(
        f'<define name="d{index}"><group><ref name="d{index + 1}"/><ref name="e{index + 1}"/></group></define>'
        f'<define name="e{index}"><group><ref name="e{index + 1}"/><ref name="d{index + 1}"/></group></define>'
        for index in range(count)
    )
    start = (
        '<choice><group><ref name="l"/><element name="b"><empty/></element></group>'
        '<group><ref name="l"/><element name="c"><empty/></element></group></choice>'
    )
    return rgrammar(
        f'<start><element name="r">{start}</element></start>{defines}'
        f'<define name="d{count}"><text/></define><define name="e{count}"><text/></define>'
        '<define name="l"><group><ref name="d0"/><ref name="s"/></group></define>'
        '<define name="s"><choice><element name="a"><empty/></element><element name="q"><empty/></element></choice>'
        "</define>"
    )


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
            _ladder('<optional><attribute name="a"/></optional>', combine="choice"),
            '<r b="1"/>',
            False,
            id="choice-attribute-name-mismatch",
        ),
        pytest.param(
            _ladder(f"<optional>{_ATTRIBUTE_VALUE}</optional>", combine="choice"),
            '<r a="x"/>',
            True,
            id="choice-attribute-value-accepted",
        ),
        pytest.param(
            _ladder(f"<optional>{_ATTRIBUTE_VALUE}</optional>", combine="choice"),
            '<r a="y"/>',
            False,
            id="choice-attribute-value-rejected",
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
    ("leaf", "content", "xml", "combine"),
    [
        pytest.param("<empty/>", '<ref name="d0"/>', "<r/>", "group", id="start-tag-close"),
        pytest.param(
            '<optional><attribute name="k"/></optional>', '<ref name="d0"/>', '<r k="1"/>', "choice", id="attribute"
        ),
        pytest.param("<text/>", '<ref name="d0"/>', "<r>y</r>", "group", id="text"),
        pytest.param("<text/>", '<ref name="d0"/>', "<r><z/></r>", "group", id="start-tag-open"),
        pytest.param(
            "<text/>", '<attribute name="k"><ref name="d0"/></attribute>', '<r k="y"/>', "group", id="nullable"
        ),
        pytest.param(_INTEGER_LIST_LEAF, '<list><ref name="d0"/></list>', "<r>1 2 3</r>", "group", id="list-tokens"),
    ],
)
def test_rng_shared_defines_scale_linearly(
    leaf: str, content: str, xml: str, combine: str, work_steps: WorkSteps
) -> None:
    document = parse_xml(xml)
    work_steps.assert_linear(
        lambda n: partial(RelaxNG(_crossed_chain(leaf, content, combine, count=n)).validate, document), (12, 20)
    )


_OPTIONAL_V: Final[str] = '<optional><element name="v"><empty/></element></optional>'
_CROSSED_STARTS: Final[list[pytest.ParameterSet]] = [
    pytest.param('<ref name="d0"/>', id="alone"),
    pytest.param('<interleave><ref name="d0"/><element name="a"><empty/></element></interleave>', id="interleaved"),
]


@pytest.mark.parametrize("start", _CROSSED_STARTS)
@pytest.mark.parametrize("combine", ["group", "oneOrMore"])
def test_rng_crossed_places_scale_linearly(start: str, combine: str, work_steps: WorkSteps) -> None:
    document = parse_xml("<r><v/><v/><v/></r>")
    work_steps.assert_linear(
        lambda n: partial(_validate_until_raised, RelaxNG(_crossed_places(start, combine, n)), document), (9, 11)
    )


def _validate_until_raised(schema: RelaxNG, document: Document) -> None:
    with suppress(ValueError):
        schema.validate(document)


def _crossed_places(start: str, combine: str, count: int) -> str:
    # Unreferenced elements raise the limits that grow with the schema, so the places outgrow the memo first.
    unused = "".join(f'<element name="u{index}"><empty/></element>' for index in range(250))
    return _crossed_chain(
        _OPTIONAL_V, start, combine, count=count, extra=f'<define name="unused"><choice>{unused}</choice></define>'
    )


@pytest.mark.parametrize(
    ("start", "tail"),
    [
        pytest.param('<ref name="d0"/>', "", id="alone"),
        pytest.param(
            '<interleave><ref name="d0"/><element name="a"><empty/></element></interleave>', "<a/>", id="interleaved"
        ),
    ],
)
def test_rng_crossed_group_places_accept_up_to_their_count(start: str, tail: str) -> None:
    schema = RelaxNG(_crossed_places(start, "group", 11))
    assert schema.validate(parse_xml(f"<r>{'<v/>' * 2048}{tail}</r>")).valid
    assert not schema.validate(parse_xml(f"<r>{'<v/>' * 2049}{tail}</r>")).valid


@pytest.mark.parametrize(
    ("start", "valid"),
    [
        pytest.param('<ref name="d0"/>', True, id="alone"),
        pytest.param(
            '<interleave><ref name="d0"/><element name="a"><empty/></element></interleave>', False, id="interleaved"
        ),
    ],
)
def test_rng_crossed_repeats_stay_under_the_work_limit(start: str, *, valid: bool) -> None:
    # each repeat absorbs the rounds its places restart, so the residual stays small
    assert RelaxNG(_crossed_places(start, "oneOrMore", 24)).validate(parse_xml("<r><v/><v/><v/></r>")).valid is valid


@pytest.mark.parametrize(
    ("xml", "valid"),
    [
        pytest.param("<r><v/><v/><v/></r>", True, id="places-accepted"),
        pytest.param("<r><v/><w/></r>", False, id="unknown-element"),
    ],
)
@pytest.mark.parametrize("combine", ["group", "oneOrMore"])
def test_rng_crossed_places_below_the_work_limit_decide(combine: str, xml: str, *, valid: bool) -> None:
    assert rng_ok(_crossed_places('<ref name="d0"/>', combine, 6), xml) is valid


def test_rng_repeated_optional_places_scale_linearly(work_steps: WorkSteps) -> None:
    document = parse_xml("<doc><v/><v/><v/></doc>")
    work_steps.assert_linear(
        lambda n: partial(_validate_until_raised, RelaxNG(rwrap(_OPTIONAL_V * n)), document), (200, 800)
    )


@pytest.mark.parametrize(
    ("xml", "valid"),
    [
        pytest.param("<doc><v/><v/><v/></doc>", True, id="places-accepted"),
        pytest.param("<doc><v/><w/></doc>", False, id="unknown-element"),
    ],
)
def test_rng_repeated_optional_places_below_the_work_limit_decide(xml: str, *, valid: bool) -> None:
    assert rng_ok(rwrap(_OPTIONAL_V * 200), xml) is valid


def test_rng_repeated_optional_places_accept_up_to_their_count() -> None:
    schema = RelaxNG(rwrap(_OPTIONAL_V * 800))
    assert schema.validate(parse_xml(f"<doc>{'<v/>' * 800}</doc>")).valid
    assert not schema.validate(parse_xml(f"<doc>{'<v/>' * 801}</doc>")).valid


@pytest.mark.parametrize(
    ("xml", "valid"),
    [
        pytest.param('<r><v a="1"/><v/>tail</r>', True, id="elements-then-text"),
        pytest.param(f"<r>{'<v/>' * 40}</r>", True, id="many-elements"),
        pytest.param('<r><v b="1"/></r>', False, id="unknown-attribute"),
        pytest.param("<r><w/></r>", False, id="unknown-element"),
        pytest.param("<r><v>x</v></r>", False, id="text-inside-empty"),
    ],
)
def test_rng_many_places_for_one_element_decide(xml: str, *, valid: bool) -> None:
    leaf = '<optional><element name="v"><optional><attribute name="a"/></optional></element></optional>'
    schema = _crossed_chain(leaf, '<group><ref name="d0"/><text/></group>', "group", count=6)
    assert rng_ok(schema, xml) is valid


def _nested_places(outer: int, inner: int) -> str:
    leaf = '<optional><element name="v"><optional><attribute name="a"/></optional></element></optional>'
    holder = '<optional><element name="a"><ref name="i0"/></element></optional>'
    return rgrammar(
        f'<start><element name="r"><ref name="o0"/></element></start>{_doubling("o", outer, holder)}'
        f"{_doubling('i', inner, leaf)}"
    )


def _doubling(prefix: str, count: int, last: str) -> str:
    defines = "".join(
        f'<define name="{prefix}{index}"><ref name="{prefix}{index + 1}"/><ref name="{prefix}{index + 1}"/></define>'
        for index in range(count)
    )
    return f'{defines}<define name="{prefix}{count}">{last}</define>'


@pytest.mark.parametrize(
    ("xml", "valid"),
    [
        pytest.param("<r><a><v/></a></r>", True, id="inner-element"),
        pytest.param('<r><a><v a="1"/><v/></a><a/></r>', True, id="siblings-at-both-levels"),
        pytest.param("<r><a><w/></a></r>", False, id="unknown-inner-element"),
        pytest.param("<r><v/></r>", False, id="inner-element-outside"),
    ],
)
def test_rng_nested_places_below_the_limit_decide(xml: str, *, valid: bool) -> None:
    assert rng_ok(_nested_places(6, 3), xml) is valid


@pytest.mark.parametrize(
    ("schema", "at_limit", "past_limit"),
    [
        pytest.param(
            _nested_places(7, 4),
            f"<r>{'<a><v/></a>' * 128}</r>",
            f"<r>{'<a><v/></a>' * 129}</r>",
            id="many-outer-places",
        ),
        pytest.param(
            _nested_places(4, 7),
            f"<r><a>{'<v/>' * 128}</a></r>",
            f"<r><a>{'<v/>' * 129}</a></r>",
            id="many-inner-places",
        ),
    ],
)
def test_rng_nested_places_accept_up_to_their_count(schema: str, at_limit: str, past_limit: str) -> None:
    assert RelaxNG(schema).validate(parse_xml(at_limit)).valid
    assert not RelaxNG(schema).validate(parse_xml(past_limit)).valid


_DOUBLING_SHAPES: Final[list[pytest.ParameterSet]] = [
    pytest.param("<group>{r}{r}</group>", lambda depth: 1 << depth, id="group"),
    pytest.param("<group><group>{r}</group><group>{r}</group></group>", lambda depth: 1 << depth, id="nested-group"),
    pytest.param("<optional><group>{r}{r}</group></optional>", lambda depth: 1 << depth, id="optional-group"),
    pytest.param(
        "<choice><group>{r}{r}</group><group>{r}{r}</group></choice>", lambda depth: 1 << depth, id="choice-of-groups"
    ),
]
_DOUBLED_SHAPES: Final[list[pytest.ParameterSet]] = [
    *_DOUBLING_SHAPES,
    pytest.param("<choice>{r}{r}</choice>", lambda _depth: 1, id="choice"),
    pytest.param("<optional>{r}</optional>", lambda _depth: 1, id="optional"),
    pytest.param("<oneOrMore>{r}{r}</oneOrMore>", lambda _depth: None, id="one-or-more"),
    pytest.param("<zeroOrMore>{r}{r}</zeroOrMore>", lambda _depth: None, id="zero-or-more"),
]


@pytest.mark.parametrize(("body", "bound"), _DOUBLED_SHAPES)
@pytest.mark.parametrize("depth", [1, 2, 3, 4])
def test_rng_doubled_places_match_their_count(body: str, bound: Callable[[int], int | None], depth: int) -> None:
    schema = RelaxNG(_doubled_grammar(body, depth))
    limit = bound(depth)
    for count in range(2**depth + 2):
        assert schema.validate(parse_xml(f"<r>{'<e/>' * count}</r>")).valid is (limit is None or count <= limit)


def _doubled_grammar(body: str, depth: int) -> str:
    defines = ['<define name="d0"><optional><element name="e"><empty/></element></optional></define>']
    for level in range(1, depth + 1):
        ref = f'<ref name="d{level - 1}"/>'
        defines.append(f'<define name="d{level}">{body.format(r=ref)}</define>')
    return rgrammar(f'<start><element name="r"><ref name="d{depth}"/></element></start>{"".join(defines)}')


@pytest.mark.parametrize(("body", "bound"), _DOUBLING_SHAPES)
def test_rng_doubled_places_at_depth_eight_accept_their_count(body: str, bound: Callable[[int], int | None]) -> None:
    schema = RelaxNG(_doubled_grammar(body, 8))
    assert bound(8) == 256
    assert schema.validate(parse_xml(f"<r>{'<e/>' * 256}</r>")).valid
    assert not schema.validate(parse_xml(f"<r>{'<e/>' * 257}</r>")).valid


def test_rng_doubled_group_chain_scales_linearly(work_steps: WorkSteps) -> None:
    schema = _doubled_grammar("<group>{r}{r}</group>", 10)
    work_steps.assert_linear(
        lambda n: partial(RelaxNG(schema).validate, parse_xml(f"<r>{'<e/>' * n}</r>")), (256, 1024)
    )


@pytest.mark.parametrize("depth", [5, 7])
def test_rng_block_family_past_the_alternative_cap_raises(depth: int) -> None:
    # 2**depth places of a block of 200 optionals hold more alternatives than the cap allows
    with pytest.raises(ValueError, match="too ambiguous"):
        RelaxNG(_block_grammar(depth, 200)).validate(parse_xml(f"<r><e/>{'<f/>' * 200}</r>"))


def _block_grammar(depth: int, tail: int) -> str:
    optional = '<optional><element name="{}"><empty/></element></optional>'
    defines = [f'<define name="d0"><group>{optional.format("e")}{optional.format("f") * tail}</group></define>']
    for level in range(1, depth + 1):
        ref = f'<ref name="d{level - 1}"/>'
        defines.append(f'<define name="d{level}"><group>{ref}{ref}</group></define>')
    return rgrammar(f'<start><element name="r"><ref name="d{depth}"/></element></start>{"".join(defines)}')


def test_rng_merge_past_the_choice_cap_raises() -> None:
    # 2**12 optional places of an element and its optional sibling: one merge passes the cap while under the work budget
    leaf = '<element name="e"><empty/></element><optional><element name="f"><empty/></element></optional>'
    defines = [f'<define name="d0"><group>{leaf}</group></define>']
    for level in range(1, 13):
        ref = f'<ref name="d{level - 1}"/>'
        defines.append(f'<define name="d{level}"><optional><group>{ref}{ref}</group></optional></define>')
    schema = RelaxNG(rgrammar(f'<start><element name="r"><ref name="d12"/></element></start>{"".join(defines)}'))
    with pytest.raises(ValueError, match="too ambiguous"):
        schema.validate(parse_xml("<r><e/><e/></r>"))


def test_rng_too_many_nested_places_scale_linearly(work_steps: WorkSteps) -> None:
    document = parse_xml("<r><a><v/></a></r>")
    # The steps until the raise stay bounded, but the event that passes the limit moves with n, so a near pair of
    # sizes can differ by that event alone.
    work_steps.assert_linear(
        lambda n: partial(_validate_until_raised, RelaxNG(_nested_places(6, n)), document), (5, 10)
    )


@pytest.mark.parametrize(
    ("xml", "valid"),
    [
        pytest.param("<r><b/></r>", True, id="one-level"),
        pytest.param("<r><a><b/><b/></a><a/></r>", True, id="two-levels"),
        pytest.param("<r><a><c/></a></r>", False, id="unknown-element"),
    ],
)
def test_rng_two_levels_of_optional_places_validate(xml: str, *, valid: bool) -> None:
    inner = '<optional><element name="b"><empty/></element></optional>' * 24
    outer = '<optional><element name="a"><ref name="b"/></element></optional>' * 24
    schema = rgrammar(
        f'<start><element name="r"><ref name="b"/>{outer}</element></start><define name="b">{inner}</define>'
    )
    assert rng_ok(schema, xml) is valid


@pytest.mark.parametrize(
    ("xml", "valid"),
    [
        pytest.param("<r/>", True, id="empty"),
        pytest.param("<r><e399/><e0/></r>", True, id="first-and-last"),
        pytest.param("<r><x/></r>", False, id="unknown-element"),
    ],
)
def test_rng_wide_define_validates(xml: str, *, valid: bool) -> None:
    alternatives = "".join(f'<element name="e{index}"><empty/></element>' for index in range(400))
    schema = rgrammar(
        '<start><element name="r"><zeroOrMore><ref name="any"/></zeroOrMore></element></start>'
        f'<define name="any"><choice>{alternatives}</choice></define>'
    )
    assert rng_ok(schema, xml) is valid


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        pytest.param("t c19", True, id="last-value-of-last-choice"),
        pytest.param("t a0", True, id="first-value"),
        pytest.param("t", True, id="optional-absent"),
        pytest.param("t d3", False, id="unknown-value"),
    ],
)
@pytest.mark.parametrize(
    ("holder", "document"),
    [
        pytest.param("{}", "<doc>{}</doc>", id="element"),
        pytest.param('<attribute name="t">{}</attribute>', '<doc t="{}"/>', id="attribute"),
    ],
)
def test_rng_list_choice_of_optional_choices(holder: str, document: str, value: str, *, valid: bool) -> None:
    alternatives = "".join(
        f"<group><value>t</value><optional><choice><value>a{index}</value><value>b{index}</value>"
        f"<value>c{index}</value></choice></optional></group>"
        for index in range(20)
    )
    content = holder.format(f"<list><choice>{alternatives}</choice></list>")
    assert rng_ok(rwrap(content), document.format(value)) is valid


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


def test_rng_routing_leaves_the_compiled_schema_unchanged(work_steps: WorkSteps) -> None:
    # the first token takes enough steps to start routing, which adopts what it derived: the compiled <optional>
    values: Final = "".join(f"<value>v{index}</value>" for index in range(100))
    optional: Final = "<optional><value>a</value><value>b</value><empty/></optional>"
    schema: Final = RelaxNG(wrap(f"<list><choice>{values}</choice>{optional}</list>"))
    document: Final = parse_xml("<doc>v5 a b</doc>")
    steps: Final = [work_steps(partial(schema.validate, document)) for _ in range(2)]
    assert steps[0] > 0
    assert steps[1] == steps[0]
    assert schema.is_valid(document)


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


@pytest.mark.parametrize(
    ("datatype", "values"),
    [
        pytest.param("date", ("0950-05-05", "2024-06-15", "2777-07-07"), id="date"),
        pytest.param(
            "dateTime", ("2024-06-15T11:59:59.9", "2024-06-15T12:00:00", "2024-06-15T12:00:00.1"), id="dateTime"
        ),
        pytest.param("time", ("13:59:59+02:00", "12:00:00Z", "10:00:01-02:00"), id="time"),
        pytest.param("duration", ("PT719H", "P30D", "P77Y"), id="duration"),
    ],
)
@pytest.mark.parametrize(
    ("facet", "expected"),
    [
        pytest.param("minInclusive", (False, True, True), id="min-inclusive"),
        pytest.param("maxInclusive", (True, True, False), id="max-inclusive"),
        pytest.param("minExclusive", (False, False, True), id="min-exclusive"),
        pytest.param("maxExclusive", (True, False, False), id="max-exclusive"),
    ],
)
def test_rng_ordered_bound(datatype: str, values: tuple[str, str, str], facet: str, expected: tuple[bool, ...]) -> None:
    validator: Final = RelaxNG(
        f'<element xmlns="{_RELAXNG_NUMERIC_BOUNDS_NAMESPACE}" datatypeLibrary="{_LIBRARY}" name="doc">'
        f'<data type="{datatype}"><param name="{facet}"> {values[1]} </param></data></element>'
    )
    assert tuple(validator.is_valid(parse_xml(f"<doc>{value}</doc>")) for value in values) == expected


@pytest.mark.parametrize(
    ("datatype", "bound", "value"),
    [
        pytest.param("date", "2024-06-15", "2024-06-15Z", id="timezone-against-none"),
        pytest.param("duration", "P30D", "P1M", id="month-against-days"),
        pytest.param("date", "soon", "2024-06-15", id="bound-not-a-date"),
        pytest.param("date", "", "2024-06-15", id="bound-empty"),
    ],
)
@pytest.mark.parametrize("facet", ["minInclusive", "maxInclusive", "minExclusive", "maxExclusive"])
def test_rng_ordered_bound_unordered(datatype: str, bound: str, value: str, facet: str) -> None:
    validator: Final = RelaxNG(
        f'<element xmlns="{_RELAXNG_NUMERIC_BOUNDS_NAMESPACE}" datatypeLibrary="{_LIBRARY}" name="doc">'
        f'<data type="{datatype}"><param name="{facet}">{bound}</param></data></element>'
    )
    assert not validator.is_valid(parse_xml(f"<doc>{value}</doc>"))


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
        pytest.param("double", '<param name="minInclusive">NaN</param>', "NaN", True, id="nan-bound-nan-value"),
        pytest.param("double", '<param name="maxInclusive">NaN</param>', "NaN", True, id="nan-max-bound-nan-value"),
        pytest.param("double", '<param name="minExclusive">NaN</param>', "NaN", False, id="nan-exclusive-bound"),
        pytest.param(
            "double",
            '<param name="minInclusive">NaN</param><param name="maxInclusive">5</param>',
            "NaN",
            False,
            id="nan-bound-and-finite-bound",
        ),
        pytest.param(
            "decimal",
            '<param name="minInclusive">0</param>',
            f"-0.{'0' * 400}1",
            False,
            id="decimal-tiny-negative-below-zero",
        ),
        pytest.param(
            "decimal",
            '<param name="maxInclusive">0</param>',
            f"0.{'0' * 400}1",
            False,
            id="decimal-tiny-positive-above-zero",
        ),
        pytest.param(
            "decimal",
            f'<param name="maxInclusive">{"9" * 400}</param>',
            f"1{'0' * 400}",
            False,
            id="decimal-overflow-above-bound",
        ),
        pytest.param(
            "integer",
            '<param name="maxInclusive">9007199254740992</param>',
            "9007199254740993",
            False,
            id="integer-past-double-above-bound",
        ),
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


_WIDE: Final[int] = 4_000
_DEEP: Final[int] = 380  # a schema nested under the 400-level depth cap once its own wrappers count
_WIDE_START: Final[str] = '<start><element name="doc"><ref name="wide"/></element></start>'
_NESTED_GROUP_TAIL: Final[str] = f"{'<text/>' * 300}</group>"
_SAME_NAME_ELEMENT: Final[str] = '<element name="a"><value>w{}</value></element>'
_OPTIONAL_ATTRIBUTE: Final[str] = '<optional><attribute name="a{}"/></optional>'
_COMBINE_CHOICE: Final[str] = '<define name="wide" combine="choice"><element name="e{}"><empty/></element></define>'
_COMBINE_INTERLEAVE: Final[str] = (
    '<define name="wide" combine="interleave"><element name="e{}"><empty/></element></define>'
)


def _repeat(item: str, count: int = _WIDE) -> str:
    return "".join(item.format(index) for index in range(count))


@pytest.mark.parametrize(
    ("schema", "document", "outcomes"),
    [
        pytest.param(wrap("<text/>" * _WIDE), "<doc>x</doc>", ("True\n",), id="group"),
        pytest.param(
            wrap(f"{'<group>' * 20}{_NESTED_GROUP_TAIL * 20}"),
            "<doc>x</doc>",
            ("True\n", "RecursionError\n"),
            id="nested-groups-first",
        ),
        pytest.param(wrap(f"<choice>{'<text/>' * _WIDE}</choice>"), "<doc>x</doc>", ("True\n",), id="choice"),
        pytest.param(
            wrap(f"<choice>{_repeat('<value>w{}</value>')}</choice>"),
            f"<doc>w{_WIDE - 1}</doc>",
            ("True\n",),
            id="choice-of-values",
        ),
        pytest.param(
            wrap(f"<choice>{_repeat('<value>w{}</value>')}</choice>"),
            f"<doc>w{_WIDE}</doc>",
            ("False\n",),
            id="choice-of-values-mismatch",
        ),
        pytest.param(
            wrap(f"<choice>{_repeat(_SAME_NAME_ELEMENT)}</choice>"),
            f"<doc><a>w{_WIDE - 1}</a></doc>",
            ("True\n",),
            id="choice-of-same-name-elements",
        ),
        pytest.param(
            wrap(f"<interleave>{_repeat(_OPTIONAL_ATTRIBUTE)}</interleave>"),
            f'<doc a{_WIDE - 1}="x" a0="y"/>',
            ("True\n",),
            id="interleave",
        ),
        pytest.param(
            f'<element xmlns="{_RELAXNG_NS}"><choice>{_repeat("<name>n{}</name>")}</choice><text/></element>',
            "<n0>x</n0>",
            ("True\n",),
            id="name-class-choice",
        ),
        pytest.param(
            f'<element xmlns="{_RELAXNG_NS}"><choice>{_repeat("<name>n{}</name>")}</choice><text/></element>',
            f"<n{_WIDE}>x</n{_WIDE}>",
            ("False\n",),
            id="name-class-choice-mismatch",
        ),
        pytest.param(
            grammar(f"{_WIDE_START}{_repeat(_COMBINE_CHOICE)}"),
            f"<doc><e{_WIDE - 1}/></doc>",
            ("True\n",),
            id="combine-choice",
        ),
        pytest.param(
            grammar(f"{_WIDE_START}{_repeat(_COMBINE_INTERLEAVE)}"),
            "<doc>x</doc>",
            ("False\n",),
            id="combine-interleave",
        ),
    ],
)
def test_rng_wide_pattern_fits_a_small_thread_stack(schema: str, document: str, *, outcomes: tuple[str, ...]) -> None:
    # the patterns of one container join into a balanced tree, so the derivative recursion grows with the log of their
    # count; a subprocess turns a stack overflow into a failed assertion instead of a killed worker. A case whose
    # nesting needs more C stack than the thread has may instead raise RecursionError.
    result: Final = _run_on_small_stack(schema, document)
    assert result.returncode == 0, result.stderr
    assert result.stdout in outcomes, result.stderr


def _run_on_small_stack(schema: str, document: str, stack_kib: int = 128) -> subprocess.CompletedProcess[str]:
    code: Final = (
        "import json, sys, threading\nfrom turbohtml import parse_xml\nfrom turbohtml.validate import RelaxNG\n"
        "data = json.load(sys.stdin)\n"
        f"for kib in range({stack_kib}, 129, 4):\n    try:\n        threading.stack_size(kib * 1024)\n"
        "    except ValueError:\n        continue\n    break\n"
        "def run():\n    try:\n        print(RelaxNG(data['schema']).validate(parse_xml(data['xml'])).valid)\n"
        "    except RecursionError:\n        print('RecursionError')\n"
        "worker = threading.Thread(target=run)\n"
        "worker.start()\nworker.join()\n"
    )
    return subprocess.run(  # ruff:ignore[subprocess-without-shell-equals-true]  # argv is a fixed list, so no shell is involved
        [sys.executable, "-c", code],
        input=json.dumps({"schema": schema, "xml": document}),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


_LEAF_A: Final[str] = '<element name="a"><empty/></element>'
_LEAF_B: Final[str] = '<element name="b"><empty/></element>'
_CHOICE_BRANCHES: Final[str] = "".join(f'<choice><element name="a{index}"><empty/></element>' for index in range(_DEEP))
_INTERLEAVE_BRANCHES: Final[str] = "".join(
    f'<interleave><element name="a{index}"><empty/></element>' for index in range(_DEEP)
)
_GROUP_OF_A: Final[str] = f"{_LEAF_A}</group>"
_ATTRIBUTE_GROUPS: Final[str] = "".join(f'<group><attribute name="x{index}"/>' for index in range(50))


@pytest.mark.parametrize(
    ("schema", "document", "stack_kib"),
    [
        pytest.param(wrap(f"{'<group>' * _DEEP}<text/>{'</group>' * _DEEP}"), "<doc>x</doc>", 128, id="group-text"),
        pytest.param(wrap(f"{'<group>' * 50}<text/>{'</group>' * 50}"), "<doc>x</doc>", 16, id="group-text-tiny-stack"),
        pytest.param(
            wrap(f"{'<group>' * _DEEP}{_GROUP_OF_A * _DEEP}{_LEAF_B}"),
            "<doc><a/></doc>",
            128,
            id="group-element",
        ),
        pytest.param(
            wrap(f"{'<choice>' * _DEEP}<text/>{_LEAF_A}{'</choice>' * _DEEP}"),
            "<doc><a/></doc>",
            128,
            id="choice-element",
        ),
        pytest.param(
            wrap(f"{_CHOICE_BRANCHES}{_LEAF_B}{'</choice>' * _DEEP}"),
            "<doc><a0/></doc>",
            16,
            id="choice-elements-tiny-stack",
        ),
        pytest.param(
            wrap(f"{'<oneOrMore>' * _DEEP}{_LEAF_A}{'</oneOrMore>' * _DEEP}"),
            "<doc><a/></doc>",
            128,
            id="one-or-more-element",
        ),
        pytest.param(
            wrap(f"{'<oneOrMore>' * _DEEP}{_LEAF_A}{'</oneOrMore>' * _DEEP}"),
            "<doc><a/></doc>",
            16,
            id="one-or-more-element-tiny-stack",
        ),
        pytest.param(
            wrap(f"<oneOrMore><group>{'<group>' * 59}{'<text/>' * 300}{'</group>' * 60}</oneOrMore>"),
            "<doc>xy xy xy</doc>",
            128,
            id="one-or-more-wide-nest",
        ),
        pytest.param(
            wrap(f"<oneOrMore><group>{'<group>' * 59}{'<text/>' * 300}{'</group>' * 60}</oneOrMore>"),
            "<doc>xy xy xy</doc>",
            16,
            id="one-or-more-wide-nest-tiny-stack",
        ),
        pytest.param(
            wrap(f'{_INTERLEAVE_BRANCHES}<attribute name="q"/>{"</interleave>" * _DEEP}'),
            '<doc q="1"><a0/></doc>',
            16,
            id="interleave-attribute-tiny-stack",
        ),
        pytest.param(
            wrap(f"{'<group>' * 40}{_LEAF_A * 300}{'</group>' * 40}"),
            "<doc><a/></doc>",
            16,
            id="group-elements-tiny-stack",
        ),
        pytest.param(
            wrap(f"{_ATTRIBUTE_GROUPS}<empty/>{'</group>' * 50}"),
            '<doc x0="1"/>',
            16,
            id="attribute-groups-tiny-stack",
        ),
        pytest.param(
            wrap(f"{_INTERLEAVE_BRANCHES}{_LEAF_B}{'</interleave>' * _DEEP}"),
            "<doc><a0/><b/></doc>",
            128,
            id="interleave-element",
        ),
        pytest.param(
            wrap(
                f'<data type="string" datatypeLibrary="{_LIBRARY}"><param name="pattern">'
                f"{'(' * 240}a{')' * 240}</param></data>"
            ),
            "<doc>a</doc>",
            16,
            id="pattern-groups-tiny-stack",
        ),
    ],
)
def test_rng_deep_nesting_raises_or_validates_on_a_small_thread_stack(
    schema: str, document: str, stack_kib: int
) -> None:
    # a derivative level costs C stack, so on a small thread a deep enough nest raises RecursionError before the stack
    # ends; the outcome is a verdict or that error, never a crashed worker
    result: Final = _run_on_small_stack(schema, document, stack_kib)
    assert result.returncode == 0, result.stderr
    assert result.stdout in {"True\n", "False\n", "RecursionError\n"}, result.stderr


def test_rng_routed_chain_on_small_thread_stack_raises_or_validates() -> None:
    # the wide choice costs its event more than the free steps, so the next event builds the routes from the root, one
    # build per chained define; a 100-define chain overruns a 32 KiB thread, so the build reaches the stack floor
    result: Final = _run_on_small_stack(_routed_chain(100), "<root><w5/><leaf/></root>", stack_kib=32)
    assert result.returncode == 0, result.stderr
    assert result.stdout in {"True\n", "RecursionError\n"}, result.stderr


def _routed_chain(depth: int) -> str:
    wide = "".join(f'<element name="w{k}"><empty/></element>' for k in range(100))
    defs = [f'<define name="wide"><choice>{wide}</choice></define>']
    for index in range(depth):
        defs.extend((
            f'<define name="d{index}" combine="choice"><ref name="d{index + 1}"/></define>',
            f'<define name="d{index}" combine="choice"><element name="x{index}"><empty/></element></define>',
        ))
    defs.append(f'<define name="d{depth}"><element name="leaf"><empty/></element></define>')
    start = '<start><element name="root"><group><ref name="wide"/><ref name="d0"/></group></element></start>'
    return grammar(start + "".join(defs))


def test_rng_small_schema_nested_repeats_validate() -> None:
    schema = RelaxNG(
        grammar(
            '<start><ref name="x"/></start><define name="x"><element name="a">'
            '<zeroOrMore><ref name="x"/></zeroOrMore><zeroOrMore><ref name="x"/></zeroOrMore></element></define>'
        )
    )
    assert schema.validate(parse_xml("<a>" * 12 + "</a>" * 12)).valid


@cache
def _open_walk_sweep() -> subprocess.CompletedProcess[str]:
    # every nesting depth from 1 to the cap runs the start-tag walk down the repeat nest on the smallest stack the
    # platform accepts: shallow depths validate, deep ones raise RecursionError. The walk closes the nest before it
    # opens it, so the depth where the open walk alone runs out of stack lies between two depths, and the sweep crosses
    # it on any build.
    code: Final = (
        "import json, sys, threading\nfrom turbohtml import parse_xml\nfrom turbohtml.validate import RelaxNG\n"
        "data = json.load(sys.stdin)\n"
        "for kib in range(16, 129, 4):\n    try:\n        threading.stack_size(kib * 1024)\n"
        "    except ValueError:\n        continue\n    break\n"
        "outcomes = []\n"
        "def run():\n"
        "    document = parse_xml(data['xml'])\n"
        "    for depth in range(1, data['depths'] + 1):\n"
        "        nest = '<oneOrMore>' * depth + data['leaf'] + '</oneOrMore>' * depth\n"
        "        try:\n"
        '            schema = RelaxNG(f\'<element name="doc" xmlns="{data["ns"]}">{nest}</element>\')\n'
        "            outcomes.append('valid' if schema.validate(document).valid else 'invalid')\n"
        "        except RecursionError:\n"
        "            outcomes.append('RecursionError')\n"
        "worker = threading.Thread(target=run)\n"
        "worker.start()\nworker.join()\n"
        "print(' '.join(outcomes))\n"
    )
    return subprocess.run(  # ruff:ignore[subprocess-without-shell-equals-true]  # argv is a fixed list, so no shell is involved
        [sys.executable, "-c", code],
        input=json.dumps({"ns": _RELAXNG_NS, "leaf": _LEAF_A, "depths": _DEEP, "xml": "<doc><a/></doc>"}),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _open_walk_unreachable() -> bool:
    # a crash stays a failure of the test itself; only a sweep that completes without any RecursionError is skipped
    sweep: Final = _open_walk_sweep()
    return sweep.returncode == 0 and "RecursionError" not in sweep.stdout


@pytest.mark.skipif(
    _open_walk_unreachable(),
    reason="this build's stack never reaches the start-tag walk on the smallest thread stack (optimized build)",
)
def test_rng_open_walk_guard_on_small_stack() -> None:  # pragma: no cover  # only where the probe reaches the guard
    # the sweep is the parent-side probe: where it completes without RecursionError the test is skipped, and where it
    # runs, the sweep must raise RecursionError somewhere and never crash
    sweep: Final = _open_walk_sweep()
    assert sweep.returncode == 0, sweep.stderr
    outcomes: Final = sweep.stdout.split()
    assert len(outcomes) == _DEEP, sweep.stderr
    assert set(outcomes) <= {"valid", "invalid", "RecursionError"}, sweep.stdout
    assert "RecursionError" in outcomes, sweep.stdout


@cache
def _repeat_check_sweep() -> subprocess.CompletedProcess[str]:
    # json's encoder nests one C level per depth and calls the validation from its innermost level, so the stack that
    # is left at the validation shrinks with the depth; the repeat check recurses on whatever is left, so it fires once
    # the walk above it has used most of the thread's stack. Each depth validates, or raises RecursionError.
    nest: Final = "<group>" * 60 + "<text/>" * 300 + "</group>" * 60
    code: Final = (
        "import json, sys, threading\nfrom turbohtml import parse_xml\nfrom turbohtml.validate import RelaxNG\n"
        "data = json.load(sys.stdin)\n"
        "for kib in range(16, 129, 4):\n    try:\n        threading.stack_size(kib * 1024)\n"
        "    except ValueError:\n        continue\n    break\n"
        "outcomes = []\n"
        "def run():\n"
        "    schema = RelaxNG(data['schema'])\n"
        "    document = parse_xml(data['xml'])\n"
        "    for depth in range(120):\n"
        "        verdicts = []\n"
        "        def innermost(value):\n"
        "            try:\n"
        "                verdicts.append('valid' if schema.validate(document).valid else 'invalid')\n"
        "            except RecursionError:\n"
        "                verdicts.append('RecursionError')\n"
        "            return 'done'\n"
        "        value = object()\n"
        "        for _ in range(depth):\n"
        "            value = [value]\n"
        "        try:\n"
        "            json.dumps(value, default=innermost)\n"
        "            outcomes.append(f'{depth}:{verdicts[0]}')\n"
        "        except RecursionError:\n"
        "            outcomes.append(f'{depth}:JSONRecursionError')\n"
        "worker = threading.Thread(target=run)\n"
        "worker.start()\nworker.join()\n"
        "print(' '.join(outcomes))\n"
    )
    return subprocess.run(  # ruff:ignore[subprocess-without-shell-equals-true]  # argv is a fixed list, so no shell is involved
        [sys.executable, "-c", code],
        input=json.dumps({"schema": wrap(f"<oneOrMore>{nest}</oneOrMore>"), "xml": "<doc>xy xy xy</doc>"}),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _repeat_check_unreachable() -> bool:
    # a crash stays a failure of the test itself; only a sweep that completes without any RecursionError is skipped
    sweep: Final = _repeat_check_sweep()
    return sweep.returncode == 0 and "RecursionError" not in sweep.stdout


def _pypy_interpreter_overruns_stack() -> bool:
    # PyPy's json encoder recurses in its own interpreter, whose frames no turbohtml floor bounds: on the thread the
    # sweep starts with, the interpreter's frames fill the stack before the first validation check, and the process
    # dies in turbohtml's entry point. CPython's interpreter keeps no such frames, so there a crash stays a failure
    return sys.implementation.name == "pypy" and _repeat_check_sweep().returncode < 0


@pytest.mark.skipif(
    _pypy_interpreter_overruns_stack() or _repeat_check_unreachable(),
    reason="this platform never reaches the repeat check with the json sweep, or PyPy's interpreter overruns the "
    "thread stack first; the C coverage gate runs elsewhere",
)
def test_rng_repeat_guard_on_small_stack() -> None:  # pragma: no cover  # only where the probe reaches the guard
    # the sweep is the parent-side probe above: where it completes without RecursionError the test is skipped, and
    # where it runs, the repeat check must fire somewhere in the sweep and the sweep must never crash
    sweep: Final = _repeat_check_sweep()
    assert sweep.returncode == 0, sweep.stderr
    outcomes: Final = [outcome.split(":")[1] for outcome in sweep.stdout.split()]
    assert len(outcomes) == 120, sweep.stderr
    assert set(outcomes) <= {"valid", "RecursionError", "JSONRecursionError"}, sweep.stdout
    assert "RecursionError" in outcomes, sweep.stdout


def test_rng_wide_group_in_order_scales_linearly(work_steps: WorkSteps) -> None:
    # each child's start tag looks for its pattern at the bottom of the group's join, which a chain made as deep as the
    # group is wide
    def validate(count: int) -> Callable[[], object]:
        names = [f"e{index}" for index in range(count)]
        schema = RelaxNG(wrap("".join(f'<element name="{name}"><empty/></element>' for name in names)))
        return partial(schema.validate, parse_xml(f"<doc>{''.join(f'<{name}/>' for name in names)}</doc>"))

    work_steps.assert_linear(validate, (500, 2_000), exponent=1.3)


_LEAF: Final[str] = '<element name="w{}"><empty/></element>'
_LEAF_REF: Final[str] = '<ref name="d{}"/>'
_LEAF_DEFINE: Final[str] = '<define name="d{0}"><element name="w{0}"><empty/></element></define>'
_STRING_VALUE: Final[str] = '<value type="string">v{}</value>'
_ATTRIBUTE: Final[str] = '<attribute name="a{}"/>'


def _arrival(order: str, count: int) -> list[int]:
    indexes: Final = list(range(count))
    if order == "reverse":
        return indexes[::-1]
    if order == "alternating":
        return indexes[::2] + indexes[1::2]
    if order == "outside-in":
        return [
            index
            for pair in zip(indexes[: count // 2], reversed(indexes[count // 2 :]), strict=False)
            for index in pair
        ]
    return indexes


def _leaves(order: str, count: int, *, twice: bool = False) -> str:
    children: Final = "".join(f"<w{index}/>" for index in _arrival(order, count))
    return f"<doc>{children * (1 + twice)}</doc>"


def _in_slices(template: str, count: int) -> str:
    return "".join(
        template.format("".join(_LEAF.format(index) for index in range(start, start + 20)))
        for start in range(0, count, 20)
    )


@pytest.mark.parametrize("order", ["schema", "reverse", "alternating", "outside-in"])
def test_rng_wide_interleave_scales_linearly_in_any_order(order: str, work_steps: WorkSteps) -> None:
    # each start tag looked for its branch through every branch the interleave still held
    def validate(count: int) -> Callable[[], object]:
        schema = RelaxNG(wrap(f"<interleave>{_repeat(_LEAF, count)}</interleave>"))
        return partial(schema.validate, parse_xml(_leaves(order, count)))

    work_steps.assert_linear(validate, (200, 800))


def _repeated_branches(count: int) -> tuple[str, str]:
    body: Final = _repeat(f"<oneOrMore>{_LEAF}</oneOrMore>", count)
    return wrap(f"<interleave>{body}</interleave>"), _leaves("reverse", count, twice=True)


def _nested_interleaves(count: int) -> tuple[str, str]:
    body: Final = _in_slices("<interleave>{}</interleave>", count)
    return wrap(f"<interleave>{body}</interleave>"), _leaves("alternating", count)


def _nested_repeats(count: int) -> tuple[str, str]:
    body: Final = _in_slices("<oneOrMore><interleave>{}</interleave></oneOrMore>", count)
    return wrap(f"<interleave>{body}</interleave>"), _leaves("outside-in", count, twice=True)


def _optional_branches(count: int) -> tuple[str, str]:
    body: Final = _repeat(f"<optional>{_LEAF}</optional>", count)
    return wrap(f"<interleave>{body}</interleave>"), _leaves("reverse", count // 2)


def _repeated_choice(count: int) -> tuple[str, str]:
    return wrap(f"<zeroOrMore><choice>{_repeat(_LEAF, count)}</choice></zeroOrMore>"), _leaves("reverse", count)


def _optional_group(count: int) -> tuple[str, str]:
    return wrap(_repeat(f"<optional>{_LEAF}</optional>", count)), _leaves("schema", count)


def _mixed_text(count: int) -> tuple[str, str]:
    children: Final = "".join(f"<w{index}/>t" for index in _arrival("alternating", count))
    return wrap(f"<mixed><interleave>{_repeat(_LEAF, count)}</interleave></mixed>"), f"<doc>{children}</doc>"


def _referenced_branches(count: int) -> tuple[str, str]:
    start: Final = f'<start><element name="doc"><interleave>{_repeat(_LEAF_REF, count)}</interleave></element></start>'
    return grammar(f"{start}{_repeat(_LEAF_DEFINE, count)}"), _leaves("reverse", count)


def _referenced_choice(count: int) -> tuple[str, str]:
    start: Final = (
        f'<start><element name="doc"><zeroOrMore><choice>{_repeat(_LEAF_REF, count)}</choice></zeroOrMore>'
        "</element></start>"
    )
    return grammar(f"{start}{_repeat(_LEAF_DEFINE, count)}"), _leaves("alternating", count)


def _combined_branches(count: int) -> tuple[str, str]:
    defines: Final = _repeat(f'<define name="wide" combine="interleave">{_LEAF}</define>', count)
    return grammar(f"{_WIDE_START}{defines}"), _leaves("outside-in", count)


def _namespace_branches(count: int) -> tuple[str, str]:
    body: Final = _repeat('<element><nsName ns="urn:n{}"/><empty/></element>', count)
    children: Final = "".join(f'<x xmlns="urn:n{index}"/>' for index in _arrival("reverse", count))
    return wrap(f"<interleave>{body}</interleave>"), f"<doc>{children}</doc>"


def _text_values(count: int) -> tuple[str, str]:
    item: Final = f'<element name="i"><choice>{_repeat(_STRING_VALUE, count)}</choice></element>'
    children: Final = "".join(f"<i>v{index}</i>" for index in _arrival("reverse", count))
    return wrap(f"<oneOrMore>{item}</oneOrMore>"), f"<doc>{children}</doc>"


def _list_values(count: int) -> tuple[str, str]:
    tokens: Final = " ".join(f"v{index}" for index in _arrival("alternating", count))
    body: Final = f"<list><oneOrMore><choice>{_repeat('<value>v{}</value>', count)}</choice></oneOrMore></list>"
    return wrap(body), f"<doc>{tokens}</doc>"


def _attribute_values(count: int) -> tuple[str, str]:
    item: Final = (
        f'<element name="i"><attribute name="a"><choice>{_repeat("<value>v{}</value>", count)}</choice></attribute>'
        "</element>"
    )
    children: Final = "".join(f'<i a=" v{index}"/>' for index in _arrival("outside-in", count))
    return wrap(f"<oneOrMore>{item}</oneOrMore>"), f"<doc>{children}</doc>"


def _attribute_branches(count: int) -> tuple[str, str]:
    attributes: Final = " ".join(f'a{index}="x"' for index in _arrival("reverse", count))
    return wrap(f"<interleave>{_repeat(_ATTRIBUTE, count)}</interleave>"), f"<doc {attributes}/>"


def _attributes_per_element(count: int) -> tuple[str, str]:
    item: Final = f'<element name="i"><interleave>{_repeat(_OPTIONAL_ATTRIBUTE, count)}</interleave></element>'
    children: Final = "".join(f'<i a{index}="x"/>' for index in _arrival("reverse", count))
    return wrap(f"<oneOrMore>{item}</oneOrMore>"), f"<doc>{children}</doc>"


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(_repeated_branches, id="repeats-under-one-or-more"),
        pytest.param(_nested_interleaves, id="nested-interleave"),
        pytest.param(_nested_repeats, id="nested-repeated-interleave"),
        pytest.param(_optional_branches, id="optional-branches"),
        pytest.param(_repeated_choice, id="repeated-choice"),
        pytest.param(_optional_group, id="group-of-optionals"),
        pytest.param(_mixed_text, id="mixed"),
        pytest.param(_referenced_branches, id="referenced-branches"),
        pytest.param(_referenced_choice, id="referenced-choice"),
        pytest.param(_combined_branches, id="combined-define"),
        pytest.param(_namespace_branches, id="namespace-branches"),
        pytest.param(_text_values, id="text-values"),
        pytest.param(_list_values, id="list-values"),
        pytest.param(_attribute_values, id="attribute-values"),
        pytest.param(_attribute_branches, id="attribute-branches"),
        pytest.param(_attributes_per_element, id="attributes-per-element"),
    ],
)
def test_rng_wide_container_scales_linearly(build: Callable[[int], tuple[str, str]], work_steps: WorkSteps) -> None:
    def validate(count: int) -> Callable[[], object]:
        source, xml = build(count)
        schema = RelaxNG(source)
        document = parse_xml(xml)
        assert schema.validate(document).valid
        return partial(schema.validate, document)

    work_steps.assert_linear(validate, (200, 800))


_PAD_JOIN: Final[str] = '<group><element name="p{0}"><empty/></element><element name="q{0}"><empty/></element></group>'
# the joins under <pad> come first, so the warm-up interleave's route is not the first one the schema numbered
_WARM: Final[str] = (
    f'<optional><element name="warm"><interleave>{_repeat(_LEAF, 100)}</interleave></element></optional>'
)
_WARM_UP: Final[str] = f'<optional><element name="pad">{_repeat(_PAD_JOIN, 70)}</element></optional>{_WARM}'
_WARM_CHILDREN: Final[str] = f"<warm>{''.join(f'<w{index}/>' for index in reversed(range(100)))}</warm>"
_NAME_CLASS_CHOICE: Final[str] = (
    "<interleave><element><choice><name>a</name><name>a</name></choice><empty/></element>"
    '<element name="b"><empty/></element></interleave>'
)
_ANY_NAME_EXCEPT: Final[str] = (
    "<interleave><element><anyName><except><name>b</name></except></anyName><empty/></element>"
    '<element name="b"><empty/></element></interleave>'
)
_NS_NAME_EXCEPT: Final[str] = (
    '<interleave><element><nsName ns="urn:q"><except><name ns="urn:q">x</name></except></nsName><empty/></element>'
    '<element name="b"><empty/></element></interleave>'
)
_ATTRIBUTE_CHOICE: Final[str] = (
    '<element name="i"><choice><oneOrMore><attribute><anyName/></attribute></oneOrMore>'
    '<oneOrMore><attribute><nsName ns="urn:q"/></attribute></oneOrMore>'
    '<element name="c"><empty/></element></choice></element>'
)
_MIXED_BRANCH: Final[str] = (
    '<interleave><mixed><element name="m"><empty/></element></mixed><element name="b"><empty/></element></interleave>'
)
_SHARED_DEFINE: Final[str] = (
    '<group><ref name="s"/><element name="b"><empty/></element><ref name="s"/><element name="c"><empty/></element>'
    "</group>"
)
_OPTIONAL_DEFINE: Final[str] = '<define name="{}"><optional><element name="{}"><empty/></element></optional></define>'
_SEQUENCE_ITEM: Final[str] = '<element name="g{}"><empty/></element>'
_SEQUENCE: Final[str] = f"<group>{_repeat(_SEQUENCE_ITEM, 10)}</group>"
_REFERENCED_CHOICE: Final[str] = (
    '<element name="k"><group><choice><ref name="o"/><element name="c1"><empty/></element>'
    '<element name="c2"><empty/></element>'
    '</choice><element name="t"><empty/></element></group></element>'
)
_EMPTY_FIRST: Final[str] = '<group><empty/><choice><empty/><element name="a"><empty/></element></choice></group>'
_ATTRIBUTE_NAMES: Final[str] = (
    '<element name="j"><choice><attribute name="a"/><attribute name="b"/><attribute name="c"/></choice></element>'
)
_TEXT_DEFINES: Final[str] = (
    '<define name="three"><text/><text/><text/></define><define name="two"><text/><text/></define>'
)
_TEXT_REFERENCES: Final[str] = '<group><ref name="two"/><ref name="three"/><element name="b"><empty/></element></group>'
_TEXT_REFERENCES_REVERSED: Final[str] = (
    '<group><ref name="three"/><ref name="two"/><element name="b"><empty/></element></group>'
)
_VALUES: Final[str] = (
    f'<interleave><element name="v"><choice>{_repeat("<value>u{}</value>", 15)}'
    f'{_repeat(_STRING_VALUE, 15)}</choice></element><element name="b"><empty/></element></interleave>'
)
# the operand drops its attribute when the start tag closes, so the joins over it change and keep their ranges
_CLOSED_OPERAND: Final[str] = '<group><optional><attribute name="a"/></optional><ref name="x"/></group>'
_CLOSED_CHOICE: Final[str] = (
    f'<element name="i"><choice>{_CLOSED_OPERAND}<ref name="y"/><ref name="z"/></choice></element>'
)
_CLOSED_GROUP: Final[str] = (
    f'<element name="h"><group><ref name="y"/>{_CLOSED_OPERAND}<ref name="z"/></group></element>'
)


def _xyz_defines(optional: str = "") -> str:
    required: Final = '<define name="{0}"><element name="{0}"><empty/></element></define>'
    return "".join(_OPTIONAL_DEFINE.format(name, name) if name == optional else required.format(name) for name in "xyz")


@pytest.mark.parametrize(
    ("pattern", "defines", "content", "expected"),
    [
        pytest.param(_NAME_CLASS_CHOICE, "", "<b/><a/>", True, id="name-class-choice"),
        pytest.param(_NAME_CLASS_CHOICE, "", "<a/><a/>", False, id="name-class-choice-twice"),
        pytest.param(_ANY_NAME_EXCEPT, "", "<b/><z/>", True, id="any-name-except"),
        pytest.param(_ANY_NAME_EXCEPT, "", "<b/><b/>", False, id="any-name-except-excluded"),
        pytest.param(_NS_NAME_EXCEPT, "", '<b/><y xmlns="urn:q"/>', True, id="ns-name-except"),
        pytest.param(_NS_NAME_EXCEPT, "", '<b/><x xmlns="urn:q"/>', False, id="ns-name-except-excluded"),
        pytest.param(_ATTRIBUTE_CHOICE, "", '<i q="1"/>', True, id="any-attribute"),
        pytest.param(_ATTRIBUTE_CHOICE, "", '<i xmlns:p="urn:q" p:r="1"/>', True, id="ns-attribute"),
        pytest.param(_ATTRIBUTE_CHOICE, "", "<i><c/></i>", True, id="attribute-choice-element"),
        pytest.param(_ATTRIBUTE_CHOICE, "", "<i/>", False, id="attribute-choice-empty"),
        pytest.param(_ATTRIBUTE_CHOICE, "", '<i q="1"><c/></i>', False, id="attribute-choice-both"),
        pytest.param(_ATTRIBUTE_NAMES, "", '<j b="1"/>', True, id="attribute-name-choice"),
        pytest.param(_ATTRIBUTE_NAMES, "", '<j d="1"/>', False, id="attribute-name-choice-unknown"),
        pytest.param(_MIXED_BRANCH, "", "<b/>t<m/>", True, id="mixed-branch"),
        pytest.param(_MIXED_BRANCH, "", "<m/><m/>", False, id="mixed-branch-twice"),
        pytest.param(_SHARED_DEFINE, _OPTIONAL_DEFINE.format("s", "a"), "<a/><b/><c/>", True, id="shared-define"),
        pytest.param(_SHARED_DEFINE, _OPTIONAL_DEFINE.format("s", "a"), "<b/><a/><c/>", True, id="shared-define-later"),
        pytest.param(
            _SHARED_DEFINE, _OPTIONAL_DEFINE.format("s", "a"), "<a/><a/><b/><c/>", False, id="shared-define-twice"
        ),
        pytest.param(_SEQUENCE, "", "".join(f"<g{index}/>" for index in range(10)), True, id="sequence"),
        pytest.param(_SEQUENCE, "", "<g5/><g0/>", False, id="sequence-out-of-order"),
        pytest.param(
            _REFERENCED_CHOICE, _OPTIONAL_DEFINE.format("o", "o"), "<k><t/></k>", True, id="referenced-choice-empty"
        ),
        pytest.param(
            _REFERENCED_CHOICE, _OPTIONAL_DEFINE.format("o", "o"), "<k><o/><t/></k>", True, id="referenced-choice"
        ),
        pytest.param(
            _REFERENCED_CHOICE,
            _OPTIONAL_DEFINE.format("o", "o"),
            "<k><c1/><c2/><t/></k>",
            False,
            id="referenced-choice-two",
        ),
        pytest.param(_EMPTY_FIRST, "", "<a/>", True, id="empty-first"),
        pytest.param(_EMPTY_FIRST, "", "<a/><a/>", False, id="empty-first-twice"),
        pytest.param(_TEXT_REFERENCES, _TEXT_DEFINES, "x<b/>", True, id="text-defines"),
        pytest.param(_TEXT_REFERENCES, _TEXT_DEFINES, "<b/>x", False, id="text-defines-after"),
        pytest.param(_TEXT_REFERENCES_REVERSED, _TEXT_DEFINES, "x<b/>", True, id="text-defines-reversed"),
        pytest.param(_CLOSED_CHOICE, _xyz_defines(), "<i/>", False, id="closed-choice"),
        pytest.param(_CLOSED_CHOICE, _xyz_defines("x"), "<i/>", True, id="closed-choice-first-optional"),
        pytest.param(_CLOSED_CHOICE, _xyz_defines("z"), "<i/>", True, id="closed-choice-last-optional"),
        pytest.param(_CLOSED_GROUP, _xyz_defines("y"), "<h/>", False, id="closed-group"),
        pytest.param(_CLOSED_GROUP, _xyz_defines("y"), '<h a="1"><x/><z/></h>', True, id="closed-group-attribute"),
        pytest.param(_VALUES, "", "<b/><v> u7 </v>", True, id="token-value"),
        pytest.param(_VALUES, "", "<v>v3</v><b/>", True, id="string-value"),
        pytest.param(_VALUES, "", "<v> v3 </v><b/>", False, id="string-value-padded"),
        pytest.param(_VALUES, "", "<v>u15</v><b/>", False, id="unknown-value"),
    ],
)
def test_rng_routed_derivatives_decide_like_the_walk(
    pattern: str, defines: str, content: str, *, expected: bool
) -> None:
    # once an event takes many steps, the validation routes the derivatives that follow through an index of operands
    schema: Final = RelaxNG(grammar(f'<start><element name="doc">{_WARM_UP}{pattern}</element></start>{defines}'))
    verdicts: Final = [schema.is_valid(parse_xml(f"<doc>{warm}{content}</doc>")) for warm in ("", _WARM_CHILDREN)]
    assert verdicts == [expected, expected]


_X: Final[str] = '<element name="x{}"><empty/></element>'
_X_OPTIONAL: Final[str] = f"<optional>{_X}</optional>"
_X_WITH_ATTRIBUTE: Final[str] = (
    '<group><optional><attribute name="a{0}"/></optional><element name="x{0}"><empty/></element></group>'
)
_X_TEXT_HALVES: Final[str] = (
    f"<group><text/>{_repeat(_X_OPTIONAL, 4)}"
    f"<text/>{''.join(_X_OPTIONAL.format(index) for index in range(4, 8))}</group>"
)


def _xs(*indexes: int) -> str:
    return "".join(f"<x{index}/>" for index in indexes)


def _xs_but(*skipped: int) -> str:
    return _xs(*(index for index in range(20) if index not in skipped))


@pytest.mark.parametrize(
    ("pattern", "attributes", "before", "after", "expected"),
    [
        pytest.param(
            f"<interleave>{_repeat(_X, 20)}</interleave>", "", _xs(5, 3), _xs_but(3, 5), True, id="interleave"
        ),
        pytest.param(
            f"<interleave>{_repeat(_X, 20)}</interleave>", "", _xs(5, 3), _xs_but(3, 5, 7), False, id="interleave-short"
        ),
        pytest.param(f"<group>{_repeat(_X_OPTIONAL, 20)}</group>", "", _xs(2, 7), _xs(9, 15), True, id="group"),
        pytest.param(
            f"<group>{_repeat(_X_OPTIONAL, 20)}</group>", "", _xs(2, 7), _xs(9, 3), False, id="group-reordered"
        ),
        pytest.param(_X_TEXT_HALVES, "", "t", _xs(5), True, id="text-in-both-halves"),
        pytest.param(_X_TEXT_HALVES, "", "t", _xs(5, 1), False, id="text-in-both-halves-reordered"),
        pytest.param(
            f"<interleave>{_repeat(_X_WITH_ATTRIBUTE, 20)}</interleave>",
            ' a3="1"',
            _xs(5, 3),
            _xs_but(3, 5),
            True,
            id="attribute-operands",
        ),
        pytest.param(
            f"<interleave>{_repeat(_X_WITH_ATTRIBUTE, 20)}</interleave>",
            ' a3="1"',
            _xs(5, 3),
            _xs_but(3, 5, 7),
            False,
            id="attribute-operands-short",
        ),
        pytest.param(
            f"<oneOrMore><interleave>{_repeat(_X_OPTIONAL, 20)}</interleave></oneOrMore>",
            "",
            _xs(2, 7),
            _xs(9, 1),
            True,
            id="repeated-interleave",
        ),
        pytest.param(
            f"<oneOrMore><interleave>{_repeat(_X_OPTIONAL, 20)}</interleave></oneOrMore>",
            "",
            _xs(2, 7),
            _xs(9, 20),
            False,
            id="repeated-interleave-unknown",
        ),
    ],
)
def test_rng_adopted_derivatives_decide_like_the_walk(
    pattern: str, attributes: str, before: str, after: str, *, expected: bool
) -> None:
    # the derivatives taken before routing starts join plainly, and the first routed event adopts them into routes;
    # the warm-up between the two halves of the content starts routing
    schema: Final = RelaxNG(wrap(f"<interleave>{pattern}{_WARM}</interleave>"))
    verdicts: Final = [
        schema.is_valid(parse_xml(f"<doc{attributes}>{before}{warm}{after}</doc>")) for warm in ("", _WARM_CHILDREN)
    ]
    assert verdicts == [expected, expected]


_OPTIONAL_LEAF: Final[str] = f"<optional>{_LEAF}</optional>"
_Z: Final[str] = '<optional><element name="z"><empty/></element></optional>'


def _round(count: int, item: str = _OPTIONAL_LEAF, first: int = 0) -> str:
    return f"<interleave>{''.join(item.format(index) for index in range(first, first + count))}</interleave>"


def _repeated_round(count: int) -> tuple[str, str]:
    return wrap(f"<oneOrMore>{_round(count)}</oneOrMore>"), _leaves("reverse", count)


def _repeated_round_in_order(count: int) -> tuple[str, str]:
    return wrap(f"<oneOrMore>{_round(count)}</oneOrMore>"), _leaves("schema", count)


def _optional_repeated_round(count: int) -> tuple[str, str]:
    return wrap(f"<zeroOrMore>{_round(count)}</zeroOrMore>"), _leaves("alternating", count)


def _round_of_empty_choices(count: int) -> tuple[str, str]:
    round_: Final = _round(count, f"<choice>{_LEAF}<empty/></choice>")
    return wrap(f"<oneOrMore>{round_}</oneOrMore>"), _leaves("outside-in", count)


def _nested_repeated_rounds(count: int) -> tuple[str, str]:
    return wrap(f"<oneOrMore><zeroOrMore>{_round(count)}</zeroOrMore></oneOrMore>"), _leaves("reverse", count)


def _round_in_group(count: int) -> tuple[str, str]:
    return wrap(f"<oneOrMore><group>{_round(count)}{_Z}</group></oneOrMore>"), _leaves("reverse", count)


def _round_after_optional(count: int) -> tuple[str, str]:
    return wrap(f"<oneOrMore><group>{_Z}{_round(count)}</group></oneOrMore>"), _leaves("reverse", count)


def _optional_round(count: int) -> tuple[str, str]:
    return wrap(f"<oneOrMore><optional>{_round(count)}</optional></oneOrMore>"), _leaves("reverse", count)


def _rounds_in_choice_repeated(count: int) -> tuple[str, str]:
    rounds: Final = "".join(_round(count // 4, first=first) for first in range(0, count, count // 4))
    return wrap(f"<oneOrMore><choice>{rounds}</choice></oneOrMore>"), _leaves("reverse", count)


def _round_in_nested_groups(count: int) -> tuple[str, str]:
    body: Final = f"{f'<group>{_Z}' * 30}{_round(count)}{'</group>' * 30}"
    return wrap(f"<oneOrMore>{body}</oneOrMore>"), _leaves("reverse", count)


def _referenced_round(count: int) -> tuple[str, str]:
    start: Final = '<start><element name="doc"><oneOrMore><ref name="round"/></oneOrMore></element></start>'
    return grammar(f'{start}<define name="round">{_round(count)}</define>'), _leaves("reverse", count)


def _round_after_element(count: int) -> tuple[str, str]:
    body: Final = f'<element name="s"><empty/></element><zeroOrMore>{_round(count)}</zeroOrMore>'
    return wrap(body), _leaves("reverse", count).replace("<doc>", "<doc><s/>")


def _mixed_round(count: int) -> tuple[str, str]:
    children: Final = "".join(f"<w{index}/>t" for index in _arrival("reverse", count))
    return wrap(f"<oneOrMore><mixed>{_round(count)}</mixed></oneOrMore>"), f"<doc>{children}</doc>"


def _attribute_round(count: int) -> tuple[str, str]:
    attributes: Final = " ".join(f'a{index}="x"' for index in _arrival("reverse", count))
    return wrap(f"<oneOrMore>{_round(count, _OPTIONAL_ATTRIBUTE)}</oneOrMore>"), f"<doc {attributes}/>"


def _referenced_halves(count: int) -> tuple[str, str]:
    # the warm-up starts routing before <i> closes its start tag, so the references stay in its content model
    halves: Final = (
        f'<define name="first">{_round(count // 2)}</define>'
        f'<define name="second">{_round(count // 2, first=count // 2)}</define>'
    )
    item: Final = (
        '<element name="i"><oneOrMore><group><ref name="first"/><ref name="second"/></group></oneOrMore></element>'
    )
    children: Final = "".join(f"<w{index}/>" for index in _arrival("reverse", count))
    return (
        grammar(f'<start><element name="doc">{_WARM}{item}</element></start>{halves}'),
        f"<doc>{_WARM_CHILDREN}<i>{children}</i></doc>",
    )


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(_repeated_round, id="one-or-more"),
        pytest.param(_repeated_round_in_order, id="one-or-more-in-order"),
        pytest.param(_optional_repeated_round, id="zero-or-more"),
        pytest.param(_round_of_empty_choices, id="choice-with-empty"),
        pytest.param(_nested_repeated_rounds, id="nested-repeats"),
        pytest.param(_round_in_group, id="round-in-group"),
        pytest.param(_round_after_optional, id="round-after-optional"),
        pytest.param(_optional_round, id="optional-round"),
        pytest.param(_rounds_in_choice_repeated, id="rounds-in-choice"),
        pytest.param(_round_in_nested_groups, id="round-in-nested-groups"),
        pytest.param(_referenced_round, id="referenced-round"),
        pytest.param(_round_after_element, id="round-after-element"),
        pytest.param(_mixed_round, id="mixed"),
        pytest.param(_attribute_round, id="attributes"),
        pytest.param(_referenced_halves, id="referenced-halves"),
    ],
)
def test_rng_repeated_optional_round_scales_linearly(
    build: Callable[[int], tuple[str, str]], work_steps: WorkSteps
) -> None:
    # each child could continue the round or start a new one, and the residual kept one branch per place the round
    # could have started
    def validate(count: int) -> Callable[[], object]:
        source, xml = build(count)
        schema = RelaxNG(source)
        document = parse_xml(xml)
        assert schema.validate(document).valid
        return partial(schema.validate, document)

    work_steps.assert_linear(validate, (200, 800))


_X_PAIR: Final[str] = (
    '<optional><group><element name="x{0}"><empty/></element>'
    '<optional><element name="y{0}"><empty/></element></optional></group></optional>'
)
_X_REQUIRED: Final[str] = '<element name="r"><empty/></element>'
_X_ROUND: Final[str] = f"<interleave>{_repeat(_X_OPTIONAL, 20)}</interleave>"
_X_ROUND_WITH_REQUIRED: Final[str] = f"<interleave>{_repeat(_X_OPTIONAL, 20)}{_X_REQUIRED}</interleave>"
_X_SHORT_ROUND: Final[str] = f"<interleave>{_repeat(_X_OPTIONAL, 2)}{_X_REQUIRED}</interleave>"
_X_PAIRS: Final[str] = f"<oneOrMore><interleave>{_repeat(_X_PAIR, 20)}</interleave></oneOrMore>"
_X_HALVES: Final[str] = (
    f"<oneOrMore><choice><interleave>{_repeat(_X_OPTIONAL, 10)}</interleave>"
    f"<interleave>{''.join(_X_OPTIONAL.format(index) for index in range(10, 20))}</interleave></choice></oneOrMore>"
)
_X_MIXED_GROUP: Final[str] = f"<oneOrMore><mixed>{_repeat(_X_OPTIONAL, 20)}</mixed></oneOrMore>"
# the lead's remainder is no round of the repeat, and telling so walks the whole wide round
_LEAD_THEN_ROUNDS: Final[str] = (
    '<group><optional><group><element name="t"><empty/></element><interleave><optional><element name="u"><empty/>'
    '</element></optional><optional><element name="v"><empty/></element></optional></interleave></group></optional>'
    f"<zeroOrMore><interleave>{_repeat(_X_OPTIONAL, 600)}</interleave></zeroOrMore></group>"
)


@pytest.mark.parametrize(
    ("pattern", "before", "after", "expected"),
    [
        pytest.param(f"<oneOrMore>{_X_ROUND}</oneOrMore>", _xs(4, 2), _xs(9, 1, 1, 4), True, id="round"),
        pytest.param(f"<oneOrMore>{_X_ROUND}</oneOrMore>", _xs(4, 2), _xs(9, 20), False, id="round-unknown"),
        pytest.param(
            f"<oneOrMore>{_X_ROUND_WITH_REQUIRED}</oneOrMore>",
            "",
            f"{_xs(*range(9))}<r/>",
            True,
            id="required-in-order",
        ),
        pytest.param(f"<oneOrMore>{_X_SHORT_ROUND}</oneOrMore>", "", "<r/><x0/><r/>", True, id="required-last"),
        pytest.param(
            f"<oneOrMore>{_X_SHORT_ROUND}</oneOrMore>", "", "<x0/><x0/><r/>", False, id="required-last-missing"
        ),
        pytest.param(
            f"<oneOrMore><oneOrMore>{_X_ROUND}</oneOrMore></oneOrMore>", _xs(3), _xs(3, 8, 3), True, id="nested"
        ),
        pytest.param(
            f"<oneOrMore><oneOrMore>{_X_ROUND}</oneOrMore></oneOrMore>", _xs(3), _xs(8, 21), False, id="nested-unknown"
        ),
        pytest.param(_X_PAIRS, _xs(3), "<x2/><y3/><x3/>", True, id="pairs"),
        pytest.param(_X_PAIRS, _xs(3), "<x2/><y3/><y2/><y3/>", False, id="pairs-twice"),
        pytest.param(_X_PAIRS, _xs(3), "<y2/>", False, id="pairs-early"),
        pytest.param(
            f"<oneOrMore>{_X_ROUND_WITH_REQUIRED}</oneOrMore>",
            f"{_xs(1)}<r/>",
            f"{_xs(2)}<r/>{_xs(2, 3)}<r/>",
            True,
            id="required",
        ),
        pytest.param(
            f"<oneOrMore>{_X_ROUND_WITH_REQUIRED}</oneOrMore>", f"{_xs(1)}<r/>", _xs(1), False, id="required-missing"
        ),
        pytest.param(
            f"<oneOrMore><group>{_X_ROUND}{_X_REQUIRED}</group></oneOrMore>",
            f"{_xs(2)}<r/>",
            f"{_xs(5, 1)}<r/>",
            True,
            id="required-after",
        ),
        pytest.param(
            f"<oneOrMore><group>{_X_ROUND}{_X_REQUIRED}</group></oneOrMore>",
            f"{_xs(2)}<r/>",
            _xs(5, 1),
            False,
            id="required-after-missing",
        ),
        pytest.param(
            f"<oneOrMore><interleave>{_repeat(f'<zeroOrMore>{_X}</zeroOrMore>', 20)}</interleave></oneOrMore>",
            _xs(6),
            _xs(6, 6, 0),
            True,
            id="repeated-operands",
        ),
        pytest.param(_X_HALVES, _xs(3), _xs(15, 2, 12), True, id="halves"),
        pytest.param(_X_HALVES, _xs(3), _xs(20), False, id="halves-unknown"),
        pytest.param(_X_MIXED_GROUP, _xs(7), "t<x9/>t<x1/>", True, id="mixed-group"),
        pytest.param(_X_MIXED_GROUP, _xs(7), "t<x1/><x9/>", True, id="mixed-group-restart"),
        pytest.param(_LEAD_THEN_ROUNDS, "", "<t/><u/><x3/><x2/>", True, id="lead"),
        pytest.param(_LEAD_THEN_ROUNDS, "", "<t/><x3/><u/>", False, id="lead-late"),
    ],
)
def test_rng_restarted_rounds_decide_like_the_walk(pattern: str, before: str, after: str, *, expected: bool) -> None:
    # once routing starts, a repeat whose round so far is nullable and part of a round absorbs that round
    schema: Final = RelaxNG(wrap(f"<interleave>{pattern}{_WARM}</interleave>"))
    verdicts: Final = [schema.is_valid(parse_xml(f"<doc>{before}{warm}{after}</doc>")) for warm in ("", _WARM_CHILDREN)]
    assert verdicts == [expected, expected]


_ANY_ATTRIBUTES_AND_CHILDREN: Final[str] = (
    f'<grammar xmlns="{_RELAXNG_NS}"><start><element name="root">'
    "<zeroOrMore><attribute><anyName/><text/></attribute></zeroOrMore>"
    "<zeroOrMore><element><anyName/><empty/></element></zeroOrMore></element></start></grammar>"
)


def _plain_attributes_and_children(count: int) -> str:
    attrs = "".join(f' a{index}="x"' for index in range(count))
    return f"<root{attrs}>{'<c/>' * count}</root>"


def _prefixed_children(count: int) -> str:
    decls = "".join(f' xmlns:p{index}="urn:p{index}"' for index in range(count))
    return f"<root{decls}>{''.join(f'<p{index}:c/>' for index in range(count))}</root>"


def _prefixed_attributes(count: int) -> str:
    attrs = "".join(f' q:a{index}="x"' for index in range(count))
    return f'<root{attrs} xmlns:q="urn:q"/>'


@pytest.mark.parametrize(
    "shape",
    [
        pytest.param(_plain_attributes_and_children, id="plain-attributes-and-children"),
        pytest.param(_prefixed_children, id="prefixed-children"),
        pytest.param(_prefixed_attributes, id="prefixed-attributes"),
    ],
)
def test_rng_many_attributes_and_children_scale_linearly(shape: Callable[[int], str], work_steps: WorkSteps) -> None:
    # a child's name and a prefixed attribute's name resolve against the namespace declarations of the element that
    # holds them, which must cost the same whatever that element's attribute count
    validator: Final = RelaxNG(_ANY_ATTRIBUTES_AND_CHILDREN)
    work_steps.assert_linear(lambda count: partial(validator.validate, parse_xml(shape(count))), (500, 2_000))


def test_rng_wide_choice_peak_is_bounded(peak_bytes: PeakBytes, tmp_path: Path) -> None:
    # every element's derivative over a choice re-canonicalizes it; while the canonical form was a chain, that cost
    # memory quadratic in the number of alternatives
    script: Final = (
        "import sys, tracemalloc\nfrom pathlib import Path\nfrom turbohtml import parse_xml\n"
        "from turbohtml.validate import RelaxNG\n"
        "schema = RelaxNG(Path(sys.argv[1]).read_text(encoding='utf-8'))\n"
        "document = parse_xml('<doc><a>w0</a></doc>')\n"
        "tracemalloc.start()\nschema.validate(document)\nprint(tracemalloc.get_traced_memory()[1])\n"
    )
    source: Final = wrap(f"<choice>{_repeat(_SAME_NAME_ELEMENT, 1_000)}</choice>")
    schema_file: Final = tmp_path / "schema.rng"
    schema_file.write_text(source, encoding="utf-8")
    assert peak_bytes(script, (str(schema_file),)) < 64 * len(source)


@pytest.mark.parametrize("count", [2, 3, 8, 9, 10, 17])
@pytest.mark.parametrize(
    ("container", "in_order", "reversed_order", "single"),
    [
        pytest.param("group", True, False, False, id="group"),
        pytest.param("interleave", True, True, False, id="interleave"),
        pytest.param("choice", False, False, True, id="choice"),
    ],
)
def test_rng_balanced_join_keeps_operand_order(
    container: str, count: int, *, in_order: bool, reversed_order: bool, single: bool
) -> None:
    names: Final = [f"e{index}" for index in range(count)]
    patterns: Final = "\n".join(f'<element name="{name}"><empty/></element>' for name in names)
    schema: Final = RelaxNG(wrap(f"<{container}>{patterns}</{container}>"))
    children: Final = [f"<{name}/>" for name in names]
    assert schema.is_valid(parse_xml(f"<doc>{''.join(children)}</doc>")) is in_order
    assert schema.is_valid(parse_xml(f"<doc>{''.join(reversed(children))}</doc>")) is reversed_order
    assert [schema.is_valid(parse_xml(f"<doc>{child}</doc>")) for child in children] == [single] * count


@pytest.mark.parametrize(
    ("pattern", "content", "expected"),
    [
        pytest.param("<group/>", "", True, id="group-empty"),
        pytest.param("<group/>", "x", False, id="group-text"),
        pytest.param("<interleave/>", "", True, id="interleave-empty"),
        pytest.param("<interleave/>", "x", False, id="interleave-text"),
        pytest.param("<choice/>", "", False, id="choice-empty"),
        pytest.param("<choice/>", "x", False, id="choice-text"),
    ],
)
def test_rng_container_without_patterns(pattern: str, content: str, *, expected: bool) -> None:
    assert check(wrap(pattern), f"<doc>{content}</doc>").valid is expected


@pytest.mark.parametrize(
    ("first", "second", "interleaved"),
    [
        pytest.param(' combine="interleave"', ' combine="interleave"', True, id="both-say-interleave"),
        pytest.param("", ' combine="interleave"', True, id="second-says-interleave"),
        pytest.param(' combine="interleave"', "", True, id="first-says-interleave"),
        pytest.param("", ' combine="choice"', False, id="second-says-choice"),
        pytest.param(' combine="choice"', "", False, id="first-says-choice"),
    ],
)
def test_rng_combine_operator_belongs_to_the_name(first: str, second: str, *, interleaved: bool) -> None:
    # section 4.17: one component may leave combine out, wherever it stands, and the others name the operator
    schema: Final = RelaxNG(
        grammar(
            '<start><element name="doc"><ref name="part"/></element></start>'
            f'<define name="part"{first}><element name="a"><empty/></element></define>'
            f'<define name="part"{second}><element name="b"><empty/></element></define>'
        )
    )
    assert schema.is_valid(parse_xml("<doc><b/><a/></doc>")) is interleaved
    assert schema.is_valid(parse_xml("<doc><a/></doc>")) is not interleaved


def test_rng_combine_interleave_checks_a_part_without_combine() -> None:
    # the part without combine joins the others by interleave, so its name clashes with theirs under section 7.4
    schema: Final = grammar(
        '<start><element name="doc"><ref name="part"/></element></start>'
        '<define name="part" combine="interleave"><element name="a"><empty/></element></define>'
        '<define name="part"><element name="a"><empty/></element></define>'
    )
    with pytest.raises(ValueError, match="the same element name in more than one branch"):
        RelaxNG(schema)


_REF_LINK: Final[str] = '<ref name="d{}"/>'
_ELEMENT_LINK: Final[str] = '<element name="e"><optional><ref name="d{}"/></optional></element>'


@pytest.mark.parametrize(
    ("count", "link", "name"),
    [
        pytest.param(399, _REF_LINK, "d399", id="one-past-limit"),
        pytest.param(1_500, _REF_LINK, "d400", id="long-chain"),
        pytest.param(3, f"{'<oneOrMore>' * 380}{_REF_LINK}{'</oneOrMore>' * 380}", "d2", id="chain-of-nested-defines"),
    ],
)
def test_rng_reference_nesting_past_limit_raises(count: int, link: str, name: str) -> None:
    # a derivative descends through every <ref> up to the next <element>, so the 400-level limit counts them too
    message: Final = f"<ref> to '{name}' nests patterns 400 levels or deeper with no element in between"
    with pytest.raises(RecursionError, match=message):
        RelaxNG(_rng_define_chain(count, link))


def _rng_define_chain(count: int, link: str) -> str:
    defines: Final = "".join(f'<define name="d{index}">{link.format(index + 1)}</define>' for index in range(count))
    start: Final = '<start><element name="doc"><ref name="d0"/></element></start>'
    return grammar(f'{start}{defines}<define name="d{count}"><text/></define>')


@pytest.mark.parametrize(
    ("count", "link", "document"),
    [
        pytest.param(398, _REF_LINK, "<doc>x</doc>", id="chain-at-limit"),
        pytest.param(1_500, _ELEMENT_LINK, "<doc><e><e/></e></doc>", id="chain-through-elements"),
    ],
)
def test_rng_reference_nesting_within_limit_validates(count: int, link: str, document: str) -> None:
    assert check(_rng_define_chain(count, link), document).valid


def test_rng_reference_nesting_counts_a_define_expanded_earlier() -> None:
    # the scan expands `deep` at the first <ref>; the second one, 240 levels further down, adds the depth recorded then,
    # which is that of the deepest component
    with pytest.raises(RecursionError, match="<ref> to 'deep' nests patterns 400 levels or deeper"):
        RelaxNG(_rng_define_reached_twice(240))


def _rng_define_reached_twice(groups: int) -> str:
    return grammar(
        f'<start><element name="doc"><ref name="deep"/>{"<group>" * groups}<ref name="deep"/>{"</group>" * groups}'
        '</element></start><define name="deep" combine="choice"><text/></define>'
        f'<define name="deep" combine="choice">{"<oneOrMore>" * 210}<text/>{"</oneOrMore>" * 210}</define>'
    )


def test_rng_reference_nesting_within_limit_reaches_a_define_twice() -> None:
    assert check(_rng_define_reached_twice(90), "<doc>x</doc>").valid


def _prefixed_grammar(body: str) -> str:
    return f'<rng:grammar xmlns:rng="{_RNG}">{body}</rng:grammar>'


@pytest.mark.parametrize(("document", "expected"), [("<a><c/><b/></a>", True), ("<a><b/></a>", False)])
def test_rng_scan_reads_prefixed_names(document: str, *, expected: bool) -> None:
    schema: Final = _prefixed_grammar(
        '<rng:start><rng:ref name="a"/></rng:start><rng:define name="a"><rng:element name="a"><rng:interleave>'
        '<rng:element name="b"><rng:empty/></rng:element><rng:element name="c"><rng:empty/></rng:element>'
        "</rng:interleave></rng:element></rng:define>"
    )
    assert check(schema, document).valid is expected


def test_rng_scan_finds_a_prefixed_ref_loop() -> None:
    schema: Final = _prefixed_grammar(
        '<rng:start><rng:ref name="a"/></rng:start>'
        '<rng:define name="a"><rng:choice><rng:ref name="a"/><rng:empty/></rng:choice></rng:define>'
    )
    with pytest.raises(ValueError, match="define 'a' references itself with no element in between"):
        RelaxNG(schema)


@pytest.mark.parametrize(
    "schema",
    [
        pytest.param(
            grammar(
                '<start><ref name="a"/></start>'
                '<define name="a"><choice><element name="a"><empty/></element><xref name="a"/></choice></define>'
            ),
            id="unprefixed",
        ),
        pytest.param(
            _prefixed_grammar(
                '<rng:start><rng:ref name="a"/></rng:start><rng:define name="a"><rng:choice>'
                '<rng:element name="a"><rng:empty/></rng:element><rng:xref name="a"/></rng:choice></rng:define>'
            ),
            id="prefixed",
        ),
    ],
)
def test_rng_scan_name_ending_in_ref_is_not_a_ref(schema: str) -> None:
    # read as a <ref>, the self-reference would be a 4.19 loop
    assert check(schema, "<a/>").valid


@pytest.mark.parametrize(
    ("datatype", "value", "expected"),
    [
        pytest.param("date", "2023-02-28", True, id="date-february-last-day"),
        pytest.param("date", "2023-02-30", False, id="date-february-30"),
        pytest.param("date", "2023-04-31", False, id="date-april-31"),
        pytest.param("date", "2024-02-29", True, id="date-leap-day"),
        pytest.param("date", "2023-02-29", False, id="date-common-year-february-29"),
        pytest.param("date", "1900-02-29", False, id="date-century-not-leap"),
        pytest.param("date", "2000-02-29", True, id="date-400-year-leap"),
        pytest.param("date", "0000-02-29", True, id="date-year-zero-leap"),
        pytest.param("date", f"1{'0' * 399}1-02-29", False, id="date-long-common"),
        pytest.param("dateTime", "2023-02-30T12:00:00", False, id="dateTime-february-30"),
        pytest.param("dateTime", "2024-02-29T23:59:59Z", True, id="dateTime-leap-day"),
        pytest.param("dateTime", "2023-02-29T00:00:00", False, id="dateTime-common-year-february-29"),
    ],
)
def test_rng_date_calendar_day(datatype: str, value: str, *, expected: bool) -> None:
    validator: Final = RelaxNG(
        f'<element xmlns="{_RELAXNG_NUMERIC_BOUNDS_NAMESPACE}" datatypeLibrary="{_LIBRARY}" name="doc">'
        f'<data type="{datatype}"/></element>'
    )
    assert validator.is_valid(parse_xml(f"<doc>{value}</doc>")) is expected


_TOKEN_ALTERNATIVES: Final[str] = "<choice>{}</choice>".format('<data type="token"/>' * 200)
_WORDS: Final[str] = " ".join(["word"] * 4_000)


@pytest.mark.parametrize(
    ("body", "xml"),
    [
        pytest.param(f'<attribute name="a">{_TOKEN_ALTERNATIVES}</attribute>', f'<doc a="{_WORDS}"/>', id="attribute"),
        pytest.param(
            f"<list><oneOrMore>{_TOKEN_ALTERNATIVES}</oneOrMore></list>", f"<doc>{'a ' * 10_000}</doc>", id="list"
        ),
    ],
)
def test_relaxng_normalized_values_keep_validation_memory_bounded(
    peak_bytes: PeakBytes, tmp_path: Path, body: str, xml: str
) -> None:
    # every <data> normalizes the whitespace of the value it is tested against; a copy per check, kept until
    # validation ended, grew with the number of alternatives
    script: Final = (
        "import sys, tracemalloc\nfrom pathlib import Path\nfrom turbohtml import parse_xml\n"
        "from turbohtml.validate import RelaxNG\n"
        "schema = RelaxNG(Path(sys.argv[1]).read_text(encoding='utf-8'))\n"
        "document = parse_xml(Path(sys.argv[2]).read_text(encoding='utf-8'))\n"
        "tracemalloc.start()\nassert schema.validate(document).valid\nprint(tracemalloc.get_traced_memory()[1])\n"
    )
    schema_file: Final = tmp_path / "schema.rng"
    schema_file.write_text(wrap(body, attrs=f'datatypeLibrary="{_DT}"'), encoding="utf-8")
    document_file: Final = tmp_path / "document.xml"
    document_file.write_text(xml, encoding="utf-8")
    assert peak_bytes(script, (str(schema_file), str(document_file))) < 1_048_576


def test_relaxng_data_normalizes_each_value_afresh() -> None:
    # one buffer serves every normalization of a validate() call, so a value must not see what a longer one left in it
    alternatives: Final = (
        '<data type="token"><param name="maxLength">3</param></data>'
        '<data type="token"><param name="pattern">[a-z]+ [a-z]+</param></data>'
        '<data type="int"/>'
    )
    validator: Final = RelaxNG(
        wrap(
            f'<zeroOrMore><element name="e"><choice>{alternatives}</choice></element></zeroOrMore>',
            attrs=f'datatypeLibrary="{_DT}"',
        )
    )
    valid: Final = "<e>a</e><e>ab</e><e>  abcdef   ghijkl  </e><e> ab </e><e>\tx\ny\t</e><e> 12345 </e><e>  abc  </e>"
    assert validator.validate(parse_xml(f"<doc>{valid}</doc>")).valid
    assert not validator.validate(parse_xml(f"<doc>{valid}<e> abcd </e></doc>")).valid
    assert not validator.validate(parse_xml(f"<doc>{valid}<e>ab cd ef</e></doc>")).valid


def test_rng_pattern_matching_past_the_step_budget_raises() -> None:
    schema: Final = RelaxNG(
        wrap('<data type="string"><param name="pattern">(b?){300000}</param></data>', attrs=f'datatypeLibrary="{_DT}"')
    )
    with pytest.raises(ValueError, match="schema patterns need more than 100000000 NFA steps"):
        schema.validate(parse_xml(f"<doc>{'b' * 1000}</doc>"))
    assert schema.is_valid(parse_xml("<doc>b</doc>"))


@pytest.mark.parametrize("padding", [pytest.param(0, id="few-attributes"), pytest.param(40, id="many-attributes")])
@pytest.mark.parametrize(
    ("schema", "valid", "invalid"),
    [
        pytest.param(
            '<grammar {pad} ns="urn:a" xmlns="{rng}"><start><element name="r">'
            '<element name="c" ns="urn:b"><empty/></element><element name="d" xy=""><empty/></element>'
            "</element></start></grammar>",
            '<r xmlns="urn:a"><c xmlns="urn:b"/><d/></r>',
            '<r xmlns="urn:a"><c/><d/></r>',
            id="inherited-ns",
        ),
        pytest.param(
            '<element name="p:r" {pad} xmlns:p="urn:a" xmlns="{rng}">'
            '<element name="p:c" {pad} xmlns:p="urn:b"><empty/></element><element name="p:d"><empty/></element>'
            "</element>",
            '<r xmlns="urn:a"><c xmlns="urn:b"/><d/></r>',
            '<r xmlns="urn:a"><c xmlns="urn:b"/><d xmlns="urn:b"/></r>',
            id="redeclared-prefix-stays-in-its-branch",
        ),
        pytest.param(
            '<element name="r" {pad} xmlns:p="urn:a" xmlns="{rng}"><attribute name="p:x"/></element>',
            '<r xmlns:q="urn:a" q:x="1"/>',
            '<r x="1"/>',
            id="prefixed-attribute",
        ),
        pytest.param(
            '<element name="r" {pad} ns="urn:a" xmlns="{rng}">'
            "<zeroOrMore><attribute><nsName/></attribute></zeroOrMore></element>",
            '<r xmlns="urn:a" xmlns:q="urn:a" q:x="1"/>',
            '<r xmlns="urn:a" x="1"/>',
            id="ns-name",
        ),
        pytest.param(
            f'<element name="r" {{pad}} datatypeLibrary="{_DT}" xmlns="{{rng}}"><data type="int"/></element>',
            "<r>4</r>",
            "<r>four</r>",
            id="datatype-library",
        ),
    ],
)
def test_rng_names_resolve_below_many_attributes(schema: str, valid: str, invalid: str, padding: int) -> None:
    # past a few dozen attributes on the way to the root a lookup reads a per-path scope instead of the ancestors
    validator: Final = RelaxNG(schema.replace("{pad}", _padding(padding)).replace("{rng}", _RELAXNG_NS))
    assert validator.validate(parse_xml(valid)).valid
    assert not validator.validate(parse_xml(invalid)).valid


def _padding(count: int) -> str:
    return " ".join(f'p{index}=""' for index in range(count))


@pytest.mark.parametrize(
    "item",
    [
        pytest.param('<element name="e{index}"><empty/></element>', id="element-name"),
        pytest.param('<element name="e{index}"><data type="int"/></element>', id="datatype-library"),
        pytest.param('<attribute name="p:a{index}"/>', id="prefixed-attribute"),
    ],
)
def test_rng_compile_below_many_attributes_scales_linearly(work_steps: WorkSteps, item: str) -> None:
    # every name below the root resolves through the root's attributes, which must not cost their count each time
    def build(count: int) -> Callable[[], RelaxNG]:
        body: Final = "".join(item.format(index=index) for index in range(count))
        return partial(
            RelaxNG,
            grammar(
                f'<start><element name="r"><choice>{body}</choice></element></start>',
                attrs=f'{_padding(count)} datatypeLibrary="{_DT}" xmlns:p="urn:p"',
            ),
        )

    work_steps.assert_linear(build, (70, 280))


def test_rng_validate_builds_a_define_below_many_attributes_linearly(work_steps: WorkSteps) -> None:
    # validate() builds the patterns of a define again, so it resolves the names below the root's attributes too
    def build(count: int) -> Callable[[], ValidationResult]:
        body: Final = "".join(f'<element name="e{index}"><empty/></element>' for index in range(count))
        schema: Final = RelaxNG(
            f'<grammar {_padding(count)} xmlns="{_RELAXNG_NS}"><start><ref name="d"/></start>'
            f'<define name="d"><choice>{body}</choice></define></grammar>'
        )
        return partial(schema.validate, parse_xml("<e1/>"))

    work_steps.assert_linear(build, (70, 280))


@pytest.mark.parametrize(
    ("datatype", "value", "expected"),
    [
        pytest.param("integer", f"{'0' * 64}250", False, id="zeros-then-digits-out-of-bound"),
        pytest.param("integer", f"{'0' * 90}50", True, id="zeros-then-digits-in-bound"),
        pytest.param("double", f"5.{'0' * 80}e30", False, id="long-fraction-then-exponent-out-of-bound"),
        pytest.param("double", f"1{'0' * 70}e-70", True, id="long-integer-then-exponent-in-bound"),
    ],
)
def test_rng_long_number_range(datatype: str, value: str, *, expected: bool) -> None:
    # the digits or exponent that decide the comparison sit past the 63rd character of the value
    validator: Final = RelaxNG(
        f'<element xmlns="{_RELAXNG_NUMERIC_BOUNDS_NAMESPACE}" datatypeLibrary="{_LIBRARY}" name="doc">'
        f'<data type="{datatype}"><param name="maxInclusive">100</param></data>'
        "</element>"
    )
    assert tuple(validator.validate(parse_xml(f"<doc>{value}</doc>")).valid for _ in range(2)) == (expected, expected)
