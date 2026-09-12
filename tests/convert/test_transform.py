from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

import pytest

import turbohtml
from turbohtml import parse_xml
from turbohtml._html import _xslt_transform
from turbohtml.transform import Transform, transform

if TYPE_CHECKING:
    from collections.abc import Callable

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from bench import core
from bench.ci import benchmarks
from bench.operations import INPUTS

_NS = 'xmlns:xsl="http://www.w3.org/1999/XSL/Transform"'


def _sheet(body: str, *, method: str = "text", declare: str = "", prefix: str = "xsl") -> turbohtml.Document:
    """Wrap a template body in a stylesheet parsed as XML, with the given output method.

    The ``xml`` method suppresses the XML declaration so content comparisons stay clean; one test builds its own sheet
    to check the declaration is emitted by default.
    """
    if method == "xml":
        output = f'<{prefix}:output method="xml" omit-xml-declaration="yes"/>'
    elif method:
        output = f'<{prefix}:output method="{method}"/>'
    else:
        output = ""
    text = (
        f'<{prefix}:stylesheet version="1.0" xmlns:{prefix}="http://www.w3.org/1999/XSL/Transform" {declare}>'
        f"{output}{body}</{prefix}:stylesheet>"
    )
    return turbohtml.parse_xml(text)


def _run(source: str, body: str, *, method: str = "text", prefix: str = "xsl", **params: str) -> str:
    """Parse a source document and body stylesheet and return the transform result."""
    return Transform(_sheet(body, method=method, prefix=prefix))(turbohtml.parse_xml(source), **params)


def _collapse(text: str) -> str:
    """Drop whitespace between tags so content compares without a serializer's layout."""
    return re.sub(r">\s+<", "><", text.strip())


def _canon(text: str) -> str:
    """Drop the XML declaration and inter-tag whitespace, as the conformance harness compares."""
    return _collapse(re.sub(r"^<\?xml\s[^>]*\?>\s*", "", text))


def test_transform_value_of_reads_string_value() -> None:
    result = _run(
        "<r><name>World</name></r>", '<xsl:template match="/">Hi <xsl:value-of select="//name"/></xsl:template>'
    )
    assert result == "Hi World"


def test_transform_apply_templates_default_children() -> None:
    body = (
        '<xsl:template match="/"><xsl:apply-templates/></xsl:template>'
        '<xsl:template match="item">[<xsl:value-of select="."/>]</xsl:template>'
    )
    assert _run("<list><item>a</item><item>b</item></list>", body) == "[a][b]"


def test_transform_apply_templates_select_expression() -> None:
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="//item"/></xsl:template>'
        '<xsl:template match="item"><xsl:value-of select="."/>|</xsl:template>'
    )
    assert _run("<list><g><item>a</item></g><item>b</item></list>", body) == "a|b|"


def test_transform_for_each_iterates_in_document_order() -> None:
    body = '<xsl:template match="/"><xsl:for-each select="r/n"><xsl:value-of select="."/></xsl:for-each></xsl:template>'
    assert _run("<r><n>1</n><n>2</n><n>3</n></r>", body) == "123"


def test_transform_position_and_last_track_the_context_list() -> None:
    body = (
        '<xsl:template match="/"><xsl:for-each select="r/n">'
        '<xsl:value-of select="position()"/>/<xsl:value-of select="last()"/>,</xsl:for-each></xsl:template>'
    )
    assert _run("<r><n/><n/></r>", body) == "1/2,2/2,"


def test_transform_if_true_and_false() -> None:
    body = (
        '<xsl:template match="/"><xsl:if test="r/n &gt; 3">big</xsl:if>'
        '<xsl:if test="r/n &lt; 3">small</xsl:if></xsl:template>'
    )
    assert _run("<r><n>5</n></r>", body) == "big"


@pytest.mark.parametrize(
    ("value", "expected"),
    [pytest.param("5", "big", id="when"), pytest.param("1", "small", id="otherwise")],
)
def test_transform_choose_when_otherwise(value: str, expected: str) -> None:
    body = (
        '<xsl:template match="/"><xsl:choose><xsl:when test="r/n &gt; 3">big</xsl:when>'
        "<xsl:otherwise>small</xsl:otherwise></xsl:choose></xsl:template>"
    )
    assert _run(f"<r><n>{value}</n></r>", body) == expected


def test_transform_choose_without_otherwise_falls_through() -> None:
    body = '<xsl:template match="/">[<xsl:choose><xsl:when test="false()">x</xsl:when></xsl:choose>]</xsl:template>'
    assert _run("<r/>", body) == "[]"


def test_transform_call_template_with_params_and_recursion() -> None:
    body = (
        '<xsl:template match="/"><xsl:call-template name="rep">'
        '<xsl:with-param name="s" select="\'ab\'"/>'
        '<xsl:with-param name="c" select="3"/></xsl:call-template></xsl:template>'
        '<xsl:template name="rep"><xsl:param name="s"/><xsl:param name="c"/>'
        '<xsl:if test="$c &gt; 0"><xsl:value-of select="$s"/>'
        '<xsl:call-template name="rep"><xsl:with-param name="s" select="$s"/>'
        '<xsl:with-param name="c" select="$c - 1"/></xsl:call-template></xsl:if></xsl:template>'
    )
    assert _run("<r/>", body) == "ababab"


def test_transform_param_uses_declared_default_when_unpassed() -> None:
    body = (
        '<xsl:template match="/"><xsl:call-template name="t"/></xsl:template>'
        '<xsl:template name="t"><xsl:param name="x" select="\'default\'"/><xsl:value-of select="$x"/></xsl:template>'
    )
    assert _run("<r/>", body) == "default"


def test_transform_variable_select_and_reference() -> None:
    body = '<xsl:template match="/"><xsl:variable name="v" select="2 + 3"/><xsl:value-of select="$v"/></xsl:template>'
    assert _run("<r/>", body) == "5"


def test_transform_variable_result_tree_fragment_string_value() -> None:
    body = (
        '<xsl:template match="/"><xsl:variable name="v"><a>x</a>'
        '</xsl:variable><xsl:value-of select="$v"/></xsl:template>'
    )
    assert _run("<r/>", body) == "x"


def test_transform_empty_variable_is_empty_string() -> None:
    body = '<xsl:template match="/"><xsl:variable name="v"/>[<xsl:value-of select="$v"/>]</xsl:template>'
    assert _run("<r/>", body) == "[]"


def test_transform_top_level_param_default_and_override() -> None:
    body = (
        '<xsl:param name="who" select="\'anon\'"/><xsl:template match="/"><xsl:value-of select="$who"/></xsl:template>'
    )
    assert _run("<r/>", body) == "anon"
    assert _run("<r/>", body, who="'Alice'") == "Alice"


def test_transform_global_variable_visible_in_templates() -> None:
    body = '<xsl:variable name="g" select="\'G\'"/><xsl:template match="/"><xsl:value-of select="$g"/></xsl:template>'
    assert _run("<r/>", body) == "G"


def test_transform_deep_variable_scope_exceeds_inline_storage() -> None:
    declarations = "".join(f'<xsl:variable name="v{index}" select="{index}"/>' for index in range(20))
    body = f'<xsl:template match="/">{declarations}<xsl:value-of select="$v19 + $v0"/></xsl:template>'
    assert _run("<r/>", body) == "19"


def test_transform_xml_output_declares_by_default() -> None:
    sheet = _sheet(
        '<xsl:output method="xml"/><xsl:template match="/"><out><xsl:value-of select="//v"/></out></xsl:template>',
        method="",
    )
    assert transform(sheet, turbohtml.parse_xml("<r><v>hi</v></r>")) == '<?xml version="1.0"?>\n<out>hi</out>'


def test_transform_xml_output_can_omit_declaration() -> None:
    assert _run("<r/>", '<xsl:template match="/"><out/></xsl:template>', method="xml") == "<out/>"


def test_transform_html_output_leaves_void_elements_open() -> None:
    body = '<xsl:template match="/"><div><br/></div></xsl:template>'
    assert _run("<r/>", body, method="html") == "<div><br></div>"


def test_transform_literal_result_element_attribute_value_template() -> None:
    body = '<xsl:template match="/"><a href="/{//r/@id}">x</a></xsl:template>'
    assert _run('<r id="7"/>', body, method="xml") == '<a href="/7">x</a>'


def test_transform_attribute_value_template_escapes_braces() -> None:
    body = '<xsl:template match="/"><a title="{{literal}}">x</a></xsl:template>'
    assert _run("<r/>", body, method="xml").endswith('<a title="{literal}">x</a>')


def test_transform_xsl_element_and_attribute() -> None:
    body = (
        '<xsl:template match="/"><xsl:element name="wrap"><xsl:attribute name="k">v</xsl:attribute>'
        "body</xsl:element></xsl:template>"
    )
    assert _run("<r/>", body, method="xml").endswith('<wrap k="v">body</wrap>')


def test_transform_xsl_element_name_is_a_value_template() -> None:
    body = '<xsl:template match="/"><xsl:element name="{//tag}">x</xsl:element></xsl:template>'
    assert _run("<r><tag>box</tag></r>", body, method="xml").endswith("<box>x</box>")


def test_transform_xsl_text_emits_verbatim() -> None:
    body = '<xsl:template match="/"><xsl:text>  spaced  </xsl:text></xsl:template>'
    assert _run("<r/>", body) == "  spaced  "


def test_transform_whitespace_only_literal_text_is_stripped() -> None:
    body = '<xsl:template match="/">\n   <xsl:value-of select="//v"/>\n</xsl:template>'
    assert _run("<r><v>x</v></r>", body) == "x"


@pytest.mark.parametrize(
    "select",
    [pytest.param("a/b", id="element"), pytest.param("a/b/text()", id="text")],
)
def test_transform_copy_of_deep_copies(select: str) -> None:
    body = f'<xsl:template match="/"><out><xsl:copy-of select="{select}"/></out></xsl:template>'
    result = _collapse(_run('<a><b id="1">t</b></a>', body, method="xml"))
    assert "t" in result


def test_transform_copy_of_string_value() -> None:
    body = '<xsl:template match="/"><xsl:copy-of select="1 + 2"/></xsl:template>'
    assert _run("<r/>", body) == "3"


def test_transform_copy_of_result_tree_fragment_copies_nodes() -> None:
    body = (
        '<xsl:variable name="frag"><b>bold</b></xsl:variable>'
        '<xsl:template match="/"><out><xsl:copy-of select="$frag"/></out></xsl:template>'
    )
    assert _collapse(_run("<r/>", body, method="xml")) == "<out><b>bold</b></out>"


def test_transform_identity_copy() -> None:
    body = (
        '<xsl:template match="@*|node()"><xsl:copy><xsl:apply-templates select="@*|node()"/></xsl:copy></xsl:template>'
    )
    sheet = _sheet('<xsl:output method="xml" omit-xml-declaration="yes"/>' + body, method="")
    source = '<a x="1"><b>t</b><!--c--></a>'
    assert transform(sheet, turbohtml.parse_xml(source)) == source


def test_transform_copy_of_comment_and_pi() -> None:
    body = (
        '<xsl:template match="/"><out>'
        '<xsl:copy-of select="//comment()|//processing-instruction()"/></out></xsl:template>'
    )
    result = _run("<r><!--note--><?pi data?></r>", body, method="xml")
    assert "<!--note-->" in result
    assert "<?pi data?>" in result


def test_transform_xsl_copy_of_root_context() -> None:
    body = (
        '<xsl:template match="/"><xsl:copy><xsl:apply-templates/></xsl:copy>'
        '</xsl:template><xsl:template match="v"><xsl:value-of select="."/></xsl:template>'
    )
    sheet = _sheet('<xsl:output method="xml" omit-xml-declaration="yes"/>' + body, method="")
    assert transform(sheet, turbohtml.parse_xml("<r><v>x</v></r>")) == "x"


def test_transform_comment_and_processing_instruction_instructions() -> None:
    body = (
        '<xsl:template match="/"><out><xsl:comment>hi</xsl:comment>'
        '<xsl:processing-instruction name="go">data</xsl:processing-instruction></out></xsl:template>'
    )
    result = _run("<r/>", body, method="xml")
    assert "<!--hi-->" in result
    assert "<?go data?>" in result


@pytest.mark.parametrize(
    ("data_type", "order", "expected"),
    [
        pytest.param("text", "ascending", "ABab", id="text-asc"),
        pytest.param("number", "descending", "403025", id="number-desc"),
    ],
)
def test_transform_sort_data_type_and_order(data_type: str, order: str, expected: str) -> None:
    if data_type == "text":
        body = (
            f'<xsl:template match="/"><xsl:for-each select="r/n"><xsl:sort select="." order="{order}"/>'
            '<xsl:value-of select="."/></xsl:for-each></xsl:template>'
        )
        assert _run("<r><n>b</n><n>A</n><n>a</n><n>B</n></r>", body) == expected
    else:
        body = (
            '<xsl:template match="/"><xsl:for-each select="r/n">'
            f'<xsl:sort select="@age" data-type="{data_type}" order="{order}"/>'
            '<xsl:value-of select="@age"/></xsl:for-each></xsl:template>'
        )
        assert _run('<r><n age="30"/><n age="25"/><n age="40"/></r>', body) == expected


@pytest.mark.parametrize(
    ("select", "keys", "ascending", "descending"),
    [
        pytest.param("number(@key)", ("2", "-1", "0"), "bca", "acb", id="integer"),
        pytest.param("number(@key)", ("2.5", "1.25", "1.5"), "bca", "acb", id="fraction"),
        pytest.param("number(@key)", ("-0", "0", "1"), "abc", "cab", id="signed-zero-ties"),
        pytest.param(
            "number(@key)",
            ("9007199254740992", "9007199254740991", "-9007199254740991"),
            "cba",
            "abc",
            id="integer-boundary",
        ),
        pytest.param("number(@key)", ("1", "bad", "bad"), "bca", "abc", id="nan-stability"),
        pytest.param("number(@key) div 0", ("1", "-1", "0"), "abc", "abc", id="infinity-string-coercion"),
        pytest.param("@key = 'true'", ("true", "false", "true"), "abc", "abc", id="boolean-string-coercion"),
        pytest.param("string(@key)", ("2", "-1", "0"), "bca", "acb", id="string-key"),
        pytest.param("string(@key)", ("", "bad", "1"), "abc", "cab", id="string-nan-stability"),
    ],
)
@pytest.mark.parametrize("reverse", [False, True], ids=["ascending", "descending"])
def test_transform_sort_numeric_expression_coercion(
    select: str, keys: tuple[str, str, str], ascending: str, descending: str, *, reverse: bool
) -> None:
    source: Final = (
        "<r>" + "".join(f'<n id="{name}" key="{key}"/>' for name, key in zip("abc", keys, strict=True)) + "</r>"
    )
    order: Final = "descending" if reverse else "ascending"
    body: Final = (
        '<xsl:template match="/"><xsl:for-each select="r/n">'
        f'<xsl:sort select="{select}" data-type="number" order="{order}"/>'
        '<xsl:value-of select="@id"/></xsl:for-each></xsl:template>'
    )
    assert _run(source, body) == (descending if reverse else ascending)


def test_transform_sort_multiple_keys() -> None:
    body = (
        '<xsl:template match="/"><xsl:for-each select="r/n">'
        '<xsl:sort select="@g"/><xsl:sort select="." data-type="number"/>'
        '<xsl:value-of select="@g"/><xsl:value-of select="."/>,</xsl:for-each></xsl:template>'
    )
    source = '<r><n g="b">2</n><n g="a">2</n><n g="a">1</n></r>'
    assert _run(source, body) == "a1,a2,b2,"


@pytest.mark.parametrize(
    ("fmt", "expected"),
    [
        pytest.param("1", "1,2,3,", id="decimal"),
        pytest.param("01", "01,02,03,", id="padded"),
        pytest.param("a", "a,b,c,", id="alpha-lower"),
        pytest.param("A", "A,B,C,", id="alpha-upper"),
        pytest.param("i", "i,ii,iii,", id="roman-lower"),
        pytest.param("I", "I,II,III,", id="roman-upper"),
    ],
)
def test_transform_number_formats(fmt: str, expected: str) -> None:
    body = (
        f'<xsl:template match="/"><xsl:for-each select="r/n">'
        f'<xsl:number format="{fmt}"/>,</xsl:for-each></xsl:template>'
    )
    assert _run("<r><n/><n/><n/></r>", body) == expected


def test_transform_number_value_expression() -> None:
    body = '<xsl:template match="/"><xsl:number value="21 + 21" format="1"/></xsl:template>'
    assert _run("<r/>", body) == "42"


def test_transform_number_alpha_wraps_past_z() -> None:
    body = '<xsl:template match="/"><xsl:number value="28" format="a"/></xsl:template>'
    assert _run("<r/>", body) == "ab"


def test_transform_key_lookup_string_and_count() -> None:
    body = (
        '<xsl:key name="k" match="item" use="@cat"/>'
        "<xsl:template match=\"/\"><xsl:value-of select=\"count(key('k','x'))\"/></xsl:template>"
    )
    assert _run('<r><item cat="x"/><item cat="y"/><item cat="x"/></r>', body) == "2"


def test_transform_key_use_over_node_set_argument() -> None:
    body = (
        '<xsl:key name="k" match="item" use="@cat"/>'
        '<xsl:template match="/"><xsl:value-of select="count(key(\'k\', r/want/@cat))"/></xsl:template>'
    )
    assert _run('<r><want cat="x"/><item cat="x"/><item cat="x"/><item cat="y"/></r>', body) == "2"


@pytest.mark.parametrize(
    ("picture", "value", "expected"),
    [
        pytest.param("#,##0.00", "1234.5", "1,234.50", id="grouped-decimal"),
        pytest.param("000", "-42", "-042", id="zero-padded-negative"),
        pytest.param("0%", "0.5", "50%", id="percent"),
        pytest.param("#.##", "3.14159", "3.14", id="rounded"),
        pytest.param("0.0;(0.0)", "-7", "(7.0)", id="negative-subpicture"),
    ],
)
def test_transform_format_number(picture: str, value: str, expected: str) -> None:
    body = f'<xsl:template match="/"><xsl:value-of select="format-number({value}, \'{picture}\')"/></xsl:template>'
    assert _run("<r/>", body) == expected


def test_transform_current_function() -> None:
    body = (
        '<xsl:template match="/"><xsl:for-each select="r/n">'
        '<xsl:value-of select="current()/@id"/></xsl:for-each></xsl:template>'
    )
    assert _run('<r><n id="1"/><n id="2"/></r>', body) == "12"


def test_transform_generate_id_is_stable_per_node() -> None:
    body = '<xsl:template match="/"><xsl:value-of select="generate-id(//n) = generate-id(//n)"/></xsl:template>'
    assert _run("<r><n/></r>", body) == "true"


def test_transform_generate_id_empty_for_empty_node_set() -> None:
    body = '<xsl:template match="/">[<xsl:value-of select="generate-id(//missing)"/>]</xsl:template>'
    assert _run("<r/>", body) == "[]"


@pytest.mark.parametrize(
    ("prop", "expected"),
    [
        pytest.param("xsl:version", "1", id="version"),
        pytest.param("xsl:vendor", "turbohtml", id="vendor"),
        pytest.param("xsl:vendor-url", "https://github.com/tox-dev/turbohtml", id="vendor-url"),
        pytest.param("other", "", id="unknown"),
    ],
)
def test_transform_system_property(prop: str, expected: str) -> None:
    body = f'<xsl:template match="/"><xsl:value-of select="system-property(\'{prop}\')"/></xsl:template>'
    assert _run("<r/>", body) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [pytest.param("key", "true", id="known"), pytest.param("nope", "false", id="unknown")],
)
def test_transform_function_available(name: str, expected: str) -> None:
    body = f'<xsl:template match="/"><xsl:value-of select="function-available(\'{name}\')"/></xsl:template>'
    assert _run("<r/>", body) == expected


def test_transform_document_and_unparsed_entity_uri_are_empty() -> None:
    body = (
        '<xsl:template match="/">[<xsl:value-of select="count(document(\'x\'))"/>'
        "<xsl:value-of select=\"unparsed-entity-uri('e')\"/>]</xsl:template>"
    )
    assert _run("<r/>", body) == "[0]"


def test_transform_custom_xslt_prefix() -> None:
    body = '<t:template match="/">ok</t:template>'
    assert _run("<r/>", body, prefix="t") == "ok"


def test_transform_default_xslt_namespace() -> None:
    sheet = turbohtml.parse_xml(
        '<stylesheet version="1.0" xmlns="http://www.w3.org/1999/XSL/Transform">'
        '<output method="text"/><template match="/"><value-of select="\'ok\'"/></template></stylesheet>'
    )
    assert transform(sheet, turbohtml.parse_xml("<r/>")) == "ok"


def test_transform_default_namespace_template_param() -> None:
    sheet = turbohtml.parse_xml(
        '<stylesheet version="1.0" xmlns="http://www.w3.org/1999/XSL/Transform">'
        '<output method="text"/><template match="/"><param name="value" select="\'ok\'"/>'
        '<value-of select="$value"/></template></stylesheet>'
    )
    assert transform(sheet, turbohtml.parse_xml("<r/>")) == "ok"


def test_transform_default_namespace_sort() -> None:
    sheet = turbohtml.parse_xml(
        '<stylesheet version="1.0" xmlns="http://www.w3.org/1999/XSL/Transform"><output method="text"/>'
        '<template match="/"><for-each select="r/n"><sort select="." data-type="number" order="descending"/>'
        '<value-of select="."/></for-each></template></stylesheet>'
    )
    assert transform(sheet, turbohtml.parse_xml("<r><n>1</n><n>2</n></r>")) == "21"


def test_transform_descendant_xslt_prefix_binding() -> None:
    body = (
        '<xsl:template match="/"><t:value-of xmlns:t="http://www.w3.org/1999/XSL/Transform" '
        "select=\"'ok'\"/></xsl:template>"
    )
    assert _run("<r/>", body) == "ok"


def test_transform_rebound_xslt_prefix_is_literal() -> None:
    body = (
        '<xsl:template match="/"><out xmlns:xsl="urn:literal"><xsl:value-of select="\'wrong\'"/></out></xsl:template>'
    )
    assert "<xsl:value-of" in _run("<r/>", body, method="xml")


def test_transform_unqualified_import_is_literal() -> None:
    body = '<import/><xsl:template match="/">ok</xsl:template>'
    assert _run("<r/>", body) == "ok"


def test_transform_conflict_resolution_prefers_specific_pattern() -> None:
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="r/*"/></xsl:template>'
        '<xsl:template match="*">generic</xsl:template>'
        '<xsl:template match="special">specific</xsl:template>'
    )
    assert _run("<r><special/></r>", body) == "specific"


def test_transform_conflict_resolution_explicit_priority_wins() -> None:
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="r/x"/></xsl:template>'
        '<xsl:template match="x" priority="2">high</xsl:template>'
        '<xsl:template match="x" priority="1">low</xsl:template>'
    )
    assert _run("<r><x/></r>", body) == "high"


def test_transform_conflict_resolution_document_order_breaks_ties() -> None:
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="r/x"/></xsl:template>'
        '<xsl:template match="x">first</xsl:template>'
        '<xsl:template match="x">second</xsl:template>'
    )
    assert _run("<r><x/></r>", body) == "second"


def test_transform_union_pattern_matches_each_alternative() -> None:
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="r/*"/></xsl:template>'
        '<xsl:template match="a|b">hit</xsl:template>'
    )
    assert _run("<r><a/><b/><c/></r>", body) == "hithit"


def test_transform_mode_isolates_templates() -> None:
    body = (
        '<xsl:template match="/">'
        '<xsl:apply-templates select="//n"/>|<xsl:apply-templates select="//n" mode="m"/></xsl:template>'
        '<xsl:template match="n">plain</xsl:template>'
        '<xsl:template match="n" mode="m">moded</xsl:template>'
    )
    assert _run("<r><n/></r>", body) == "plain|moded"


def test_transform_builtin_rule_copies_text_and_recurses() -> None:
    assert (
        _run("<r>hello <b>bold</b></r>", '<xsl:template match="/"><xsl:apply-templates/></xsl:template>')
        == "hello bold"
    )


def test_transform_builtin_rule_copies_attribute_value() -> None:
    body = '<xsl:template match="/"><xsl:apply-templates select="//n/@id"/></xsl:template>'
    assert _run('<r><n id="A"/></r>', body) == "A"


def test_transform_builtin_rule_ignores_comment() -> None:
    body = '<xsl:template match="/"><xsl:apply-templates select="//comment()"/></xsl:template>'
    assert not _run("<r><!--x--></r>", body)


def test_transform_attribute_matching_template() -> None:
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="//n/@id"/></xsl:template>'
        '<xsl:template match="@id">[<xsl:value-of select="."/>]</xsl:template>'
    )
    assert _run('<r><n id="A"/></r>', body) == "[A]"


def test_transform_message_non_terminating_is_discarded() -> None:
    body = '<xsl:template match="/"><xsl:message>note</xsl:message>done</xsl:template>'
    assert _run("<r/>", body) == "done"


def test_transform_apply_templates_with_param() -> None:
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="//n">'
        '<xsl:with-param name="p" select="\'X\'"/></xsl:apply-templates></xsl:template>'
        '<xsl:template match="n"><xsl:param name="p" select="\'-\'"/><xsl:value-of select="$p"/></xsl:template>'
    )
    assert _run("<r><n/></r>", body) == "X"


def test_transform_reusable_compiled_stylesheet() -> None:
    convert = Transform(_sheet('<xsl:template match="/"><xsl:value-of select="//v"/></xsl:template>'))
    assert convert(turbohtml.parse_xml("<r><v>1</v></r>")) == "1"
    assert convert(turbohtml.parse_xml("<r><v>2</v></r>")) == "2"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        pytest.param('<xsl:template match="/"><xsl:value-of/></xsl:template>', "value-of requires", id="value-of"),
        pytest.param('<xsl:template match="/"><xsl:for-each/></xsl:template>', "for-each requires", id="for-each"),
        pytest.param('<xsl:template match="/"><xsl:copy-of/></xsl:template>', "copy-of requires", id="copy-of"),
        pytest.param('<xsl:template match="/"><xsl:if/></xsl:template>', "requires a test", id="if"),
        pytest.param('<xsl:template match="/"><xsl:element/></xsl:template>', "element requires", id="element"),
        pytest.param(
            '<xsl:template match="/"><xsl:call-template name="missing"/></xsl:template>',
            "undeclared template",
            id="call-missing",
        ),
        pytest.param(
            "<xsl:template match=\"/\"><xsl:value-of select=\"key('k','x')\"/></xsl:template>",
            "undeclared key",
            id="key-missing",
        ),
    ],
)
def test_transform_reports_stylesheet_errors(body: str, message: str) -> None:
    with pytest.raises((ValueError, RuntimeError), match=message):
        _run("<r/>", body)


def test_transform_message_terminate_raises() -> None:
    body = '<xsl:template match="/"><xsl:message terminate="yes">stop</xsl:message></xsl:template>'
    with pytest.raises(RuntimeError, match="stop"):
        _run("<r/>", body)


def test_transform_bad_select_expression_raises() -> None:
    with pytest.raises(ValueError, match="value-of select"):
        _run("<r/>", '<xsl:template match="/"><xsl:value-of select="@("/></xsl:template>')


def test_transform_for_each_on_non_node_set_raises() -> None:
    with pytest.raises(ValueError, match="not a node-set"):
        _run("<r/>", '<xsl:template match="/"><xsl:for-each select="1 + 1">x</xsl:for-each></xsl:template>')


def test_transform_params_must_be_a_dict() -> None:
    sheet = _sheet('<xsl:template match="/">x</xsl:template>')
    with pytest.raises(TypeError, match="dict or None"):
        _xslt_transform(sheet, turbohtml.parse_xml("<r/>"), ["not", "a", "dict"])  # ty: ignore[invalid-argument-type]  # wrong type on purpose


def test_transform_stylesheet_without_root_element_raises() -> None:
    root = turbohtml.parse_xml("<r><!--c--></r>").root
    assert root is not None
    comment = root.children[0]
    with pytest.raises(ValueError, match="no root element"):
        transform(comment, turbohtml.parse_xml("<r/>"))


def test_transform_too_many_union_alternatives_raises() -> None:
    match = "|".join(f"e{index}" for index in range(70))
    body = f'<xsl:template match="{match}">x</xsl:template>'
    with pytest.raises(ValueError, match="too many alternatives"):
        _run("<r/>", body)


@pytest.mark.parametrize("instruction", ["for-each", "apply-templates"])
def test_transform_too_many_sort_keys_raises(instruction: str) -> None:
    sorts = "".join('<xsl:sort select="."/>' for _ in range(9))
    body = f'<xsl:template match="/"><xsl:{instruction} select="r/n">{sorts}</xsl:{instruction}></xsl:template>'
    with pytest.raises(ValueError, match="too many sort keys"):
        _run("<r><n/></r>", body)


def test_transform_too_many_parameters_raises() -> None:
    params = "".join(f'<xsl:with-param name="p{index}" select="{index}"/>' for index in range(17))
    body = (
        f'<xsl:template match="/"><xsl:call-template name="t">{params}</xsl:call-template></xsl:template>'
        '<xsl:template name="t">x</xsl:template>'
    )
    with pytest.raises(ValueError, match="too many parameters"):
        _run("<r/>", body)


def test_transform_recursion_depth_is_bounded() -> None:
    body = (
        '<xsl:template match="/"><xsl:call-template name="loop"/></xsl:template>'
        '<xsl:template name="loop"><xsl:call-template name="loop"/></xsl:template>'
    )
    with pytest.raises(RecursionError, match="nesting too deep"):
        _run("<r/>", body)


def test_transform_deep_recursive_named_template_raises_cleanly() -> None:
    body = (
        '<xsl:template match="/"><xsl:call-template name="rec"><xsl:with-param name="c" select="100000"/>'
        "</xsl:call-template></xsl:template>"
        '<xsl:template name="rec"><xsl:param name="c"/><xsl:if test="$c &gt; 0">.'
        '<xsl:call-template name="rec"><xsl:with-param name="c" select="$c - 1"/>'
        "</xsl:call-template></xsl:if></xsl:template>"
    )
    with pytest.raises(RecursionError, match="nesting too deep"):
        _run("<r/>", body)


def test_transform_deep_apply_templates_recursion_raises_cleanly() -> None:
    source = "<r>" + "<n>" * 600 + "x" + "</n>" * 600 + "</r>"
    body = (
        '<xsl:template match="/"><xsl:apply-templates/></xsl:template>'
        '<xsl:template match="n">[<xsl:apply-templates/>]</xsl:template>'
    )
    with pytest.raises(RecursionError, match="nesting too deep"):
        _run(source, body)


def test_transform_anchored_patterns() -> None:
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="//n"/></xsl:template>'
        '<xsl:template match="/r/n">rooted</xsl:template>'
    )
    assert _run("<r><n/></r>", body) == "rooted"


def test_transform_default_priority_wildcard_versus_node_test() -> None:
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="r/node()"/></xsl:template>'
        '<xsl:template match="node()">N</xsl:template>'
        '<xsl:template match="*">S</xsl:template>'
    )
    assert _run("<r><a/>t</r>", body) == "SN"


def test_transform_attribute_with_non_ascii_names() -> None:
    body = (
        '<xsl:template match="/"><x>'
        '<xsl:attribute name="café">2</xsl:attribute>'
        '<xsl:attribute name="中">3</xsl:attribute>'
        '<xsl:attribute name="a\U0001f600b">4</xsl:attribute></x></xsl:template>'
    )
    result = _run("<r/>", body, method="xml")
    assert 'café="2"' in result
    assert '中="3"' in result
    assert 'a\U0001f600b="4"' in result


def test_transform_element_name_uppercase_and_non_ascii() -> None:
    body = (
        '<xsl:template match="/"><out><xsl:element name="DIV">a</xsl:element>'
        '<xsl:element name="café">b</xsl:element></out></xsl:template>'
    )
    result = _run("<r/>", body, method="html")
    assert "<DIV>a</DIV>" in result
    assert "<café>b</café>" in result


def test_transform_element_name_over_length_cap() -> None:
    name = "e" * 70
    body = f'<xsl:template match="/"><xsl:element name="{name}">x</xsl:element></xsl:template>'
    assert _run("<r/>", body, method="xml") == f"<{name}>x</{name}>"


def test_transform_template_matching_many_nodes_grows_match_set() -> None:
    items = "".join(f"<i>{index}</i>" for index in range(30))
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="r/i"/></xsl:template>'
        '<xsl:template match="i"><xsl:value-of select="."/>,</xsl:template>'
    )
    assert _run(f"<r>{items}</r>", body) == "".join(f"{index}," for index in range(30))


def test_transform_key_with_many_distinct_values_grows_table() -> None:
    items = "".join(f'<i k="v{index}">{index}</i>' for index in range(30))
    body = (
        '<xsl:key name="k" match="i" use="@k"/>'
        "<xsl:template match=\"/\"><xsl:value-of select=\"key('k','v17')\"/></xsl:template>"
    )
    assert _run(f"<r>{items}</r>", body) == "17"


def test_transform_key_deduplicates_a_node_under_one_value() -> None:
    body = (
        '<xsl:key name="k" match="i" use="t"/>'
        "<xsl:template match=\"/\"><xsl:value-of select=\"count(key('k','same'))\"/></xsl:template>"
    )
    assert _run("<r><i><t>same</t><t>same</t></i></r>", body) == "1"


@pytest.mark.parametrize(
    ("match", "use", "source", "wanted", "expected"),
    [
        pytest.param("i", "'same'", '<r><i id="a"/><i id="b"/><i id="c"/></r>', "same", "abc", id="scalar-key"),
        pytest.param(
            "i",
            "t",
            '<r><i id="a"><t>x</t><t>y</t><t>x</t></i><i id="b"><t>x</t><t>x</t></i></r>',
            "x",
            "ab",
            id="interleaved-use-values",
        ),
        pytest.param(
            "i",
            "@a | @b",
            '<r><i id="a" a="x" b="x"/><i id="b" a="x" b="y"/></r>',
            "x",
            "ab",
            id="duplicate-attribute-values",
        ),
        pytest.param(
            "/r/i | /r/i/@a | /r/i/@b",
            "'same'",
            '<r><i id="a" a="x" b="y"/><i id="b" a="x" b="y"/></r>',
            "same",
            "ab",
            id="element-and-attribute-match-owners",
        ),
        pytest.param(
            "id('c a b a')",
            "'same'",
            '<r><i id="a"/><i id="b"/><i id="c"/></r>',
            "same",
            "abc",
            id="id-pattern-order-and-duplicates",
        ),
        pytest.param("i", "t", '<r><i id="a"/><i id="b"><t>x</t></i></r>', "x", "b", id="empty-use-node-set"),
        pytest.param(
            "i",
            "t",
            '<r><i id="a"><t/></i><i id="b"><t/><t/></i></r>',
            "",
            "ab",
            id="empty-key-string",
        ),
    ],
)
def test_transform_key_bucket_duplicate_order(match: str, use: str, source: str, wanted: str, expected: str) -> None:
    body: Final = (
        f'<xsl:key name="k" match="{match}" use="{use}"/>'
        f'<xsl:template match="/"><xsl:for-each select="key(&quot;k&quot;,&quot;{wanted}&quot;)">'
        '<xsl:value-of select="@id"/></xsl:for-each></xsl:template>'
    )
    assert _run(source, body) == expected


def test_transform_key_string_use_expression() -> None:
    body = (
        '<xsl:key name="k" match="i" use="string(@n)"/>'
        "<xsl:template match=\"/\"><xsl:value-of select=\"key('k','2')/@id\"/></xsl:template>"
    )
    assert _run('<r><i n="1" id="a"/><i n="2" id="b"/></r>', body) == "b"


def test_transform_union_pattern_predicate_with_pipe_literal() -> None:
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="r/*"/></xsl:template>'
        "<xsl:template match=\"a[@x='|'] | b\">hit </xsl:template>"
    )
    assert _run('<r><a x="|"/><a x="o"/><b/></r>', body) == "hit hit "


@pytest.mark.parametrize(
    ("match", "expected"),
    [
        pytest.param("text()", "T", id="text-node-test"),
        pytest.param("comment()", "C", id="comment-node-test"),
    ],
)
def test_transform_node_test_default_priority(match: str, expected: str) -> None:
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="r/node()"/></xsl:template>'
        '<xsl:template match="node()"/>'
        f'<xsl:template match="{match}">{expected}</xsl:template>'
    )
    assert _run("<r>x<!--c--></r>", body) == expected


@pytest.mark.parametrize(
    ("picture", "value", "expected"),
    [
        pytest.param("0", "-5", "-5", id="negative-default-minus"),
        pytest.param("0‰", "0.5", "500‰", id="per-mille"),
        pytest.param("0.##", "1.5", "1.5", id="trailing-fraction-trimmed"),
        pytest.param("#", "0", "0", id="hash-zero"),
        pytest.param("$#,##0.00", "1000", "$1,000.00", id="currency-prefix"),
        pytest.param("0.00", "0", "0.00", id="required-fraction"),
    ],
)
def test_transform_format_number_pictures(picture: str, value: str, expected: str) -> None:
    body = f'<xsl:template match="/"><xsl:value-of select="format-number({value}, \'{picture}\')"/></xsl:template>'
    assert _run("<r/>", body) == expected


def test_transform_element_available() -> None:
    body = '<xsl:template match="/"><xsl:value-of select="element-available(\'x\')"/></xsl:template>'
    assert _run("<r/>", body) == "false"


def test_transform_mode_mismatch_falls_to_builtin() -> None:
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="//n" mode="a"/></xsl:template>'
        '<xsl:template match="n" mode="b">B</xsl:template>'
    )
    assert _run("<r><n>text</n></r>", body) == "text"


def test_transform_avt_expression_with_quoted_brace() -> None:
    body = "<xsl:template match=\"/\"><a v=\"{concat('x','}')}\">t</a></xsl:template>"
    assert _run("<r/>", body, method="xml") == '<a v="x}">t</a>'


def test_transform_copy_attribute_at_root_is_dropped() -> None:
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="//n/@id"/></xsl:template>'
        '<xsl:template match="@id"><xsl:copy/>done</xsl:template>'
    )
    assert _run('<r><n id="A"/></r>', body) == "done"


def test_transform_sort_string_prefix_tiebreak() -> None:
    body = (
        '<xsl:template match="/"><xsl:for-each select="r/n"><xsl:sort select="."/>'
        '<xsl:value-of select="."/>,</xsl:for-each></xsl:template>'
    )
    assert _run("<r><n>ba</n><n>b</n><n>ab</n></r>", body) == "ab,b,ba,"


def test_transform_number_over_mixed_siblings_counts_same_name() -> None:
    body = '<xsl:template match="/"><xsl:for-each select="r/b"><xsl:number/>,</xsl:for-each></xsl:template>'
    assert _run("<r><a/><b/><a/><b/></r>", body) == "1,2,"


def test_transform_number_alternating_names_numbers_each_name_separately() -> None:
    # numbering walks the run carrying the previous answer forward, so alternating names must not let a <b>'s number
    # continue from the <a> before it
    body = (
        '<xsl:template match="/"><xsl:for-each select="r/*"><xsl:value-of select="name()"/>'
        "<xsl:number/>,</xsl:for-each></xsl:template>"
    )
    assert _run("<r><a/><bb/><a/><bb/><a/></r>", body) == "a1,bb1,a2,bb2,a3,"


def test_transform_number_with_count_pattern_over_a_run() -> None:
    # an explicit count set fixes the criteria for the whole run, the other way the carried-forward answer is reused
    body = '<xsl:template match="/"><xsl:for-each select="r/n"><xsl:number count="n"/>,</xsl:for-each></xsl:template>'
    assert _run("<r><n/><n/><n/></r>", body) == "1,2,3,"


def test_transform_number_mixing_counted_and_default_over_one_run() -> None:
    # the same run numbered with and without a count pattern: the answer carried forward under one criterion must
    # not answer for the other
    body = (
        '<xsl:template match="/"><xsl:for-each select="r/n">'
        '<xsl:number/>-<xsl:number count="n"/>,</xsl:for-each></xsl:template>'
    )
    assert _run("<r><n/><n/><n/></r>", body) == "1-1,2-2,3-3,"


def test_transform_number_two_count_patterns_over_one_run() -> None:
    # count="r" resolves to the ancestor <r>, which has no preceding <r> siblings, so it stays 1 while count="n"
    # advances; a memo held for one pattern must not answer for the other
    body = (
        '<xsl:template match="/"><xsl:for-each select="r/n">'
        '<xsl:number count="n"/>-<xsl:number count="r"/>,</xsl:for-each></xsl:template>'
    )
    assert _run("<r><n/><n/><n/></r>", body) == "1-1,2-1,3-1,"


def test_transform_number_interleaved_count_patterns_stay_separate() -> None:
    # two xsl:number instructions alternating over one run: each must count under its own pattern, so a number
    # carried forward for one cannot answer for the other
    body = (
        '<xsl:template match="/"><xsl:for-each select="r/*">'
        '<xsl:number count="a"/>-<xsl:number count="b"/>,</xsl:for-each></xsl:template>'
    )
    assert _run("<r><a/><b/><a/><b/></r>", body) == "1-,-1,2-,-2,"


def test_transform_number_alternating_same_length_names() -> None:
    # the names are the same length, so telling them apart is the name comparison itself rather than the length
    body = (
        '<xsl:template match="/"><xsl:for-each select="r/*"><xsl:value-of select="name()"/>'
        "<xsl:number/>,</xsl:for-each></xsl:template>"
    )
    assert _run("<r><a/><b/><a/><b/><a/></r>", body) == "a1,b1,a2,b2,a3,"


def test_transform_number_multiple_levels_numbers_each_depth() -> None:
    body = (
        '<xsl:template match="/"><xsl:for-each select="//c">'
        '<xsl:number level="multiple" count="a|b|c"/>,</xsl:for-each></xsl:template>'
    )
    assert _run("<r><a><b><c/><c/></b><b><c/></b></a><a><b><c/></b></a></r>", body) == "1.1.1,1.1.2,1.2.1,2.1.1,"


def test_transform_number_count_pattern_over_mixed_siblings() -> None:
    # one instruction walking a mixed run: each node carries the previous answer forward, and a sibling the count
    # pattern does not match must add nothing to it
    body = '<xsl:template match="/"><xsl:for-each select="r/*"><xsl:number count="b"/>,</xsl:for-each></xsl:template>'
    assert _run("<r><a/><b/><a/><b/></r>", body) == ",1,,2,"


def test_transform_number_on_attribute_is_one() -> None:
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="//n/@id"/></xsl:template>'
        '<xsl:template match="@id"><xsl:number/></xsl:template>'
    )
    assert _run('<r><n id="x"/></r>', body) == "1"


def test_transform_number_without_format_attribute() -> None:
    body = '<xsl:template match="/"><xsl:for-each select="r/n"><xsl:number/></xsl:for-each></xsl:template>'
    assert _run("<r><n/><n/></r>", body) == "12"


def test_transform_many_templates_grow_rule_array() -> None:
    templates = "".join(f'<xsl:template match="e{index}">{index},</xsl:template>' for index in range(20))
    apply = '<xsl:template match="/"><xsl:apply-templates select="r/*"/></xsl:template>'
    elements = "".join(f"<e{index}/>" for index in range(20))
    assert _run(f"<r>{elements}</r>", apply + templates) == "".join(f"{index}," for index in range(20))


def test_transform_many_global_variables() -> None:
    declarations = "".join(f'<xsl:variable name="g{index}" select="{index}"/>' for index in range(12))
    body = f'{declarations}<xsl:template match="/"><xsl:value-of select="$g11"/></xsl:template>'
    assert _run("<r/>", body) == "11"


def test_transform_param_declaration_after_leading_whitespace() -> None:
    body = (
        '<xsl:template match="/"><xsl:call-template name="t"/></xsl:template>'
        '<xsl:template name="t">\n  <xsl:param name="p" select="\'d\'"/><xsl:value-of select="$p"/></xsl:template>'
    )
    assert _run("<r/>", body) == "d"


def test_transform_message_terminate_no_is_discarded() -> None:
    body = '<xsl:template match="/"><xsl:message terminate="no">note</xsl:message>ok</xsl:template>'
    assert _run("<r/>", body) == "ok"


def test_transform_local_variable_shadows_global() -> None:
    body = (
        '<xsl:variable name="v" select="\'global\'"/>'
        '<xsl:template match="/"><xsl:variable name="v" select="\'local\'"/><xsl:value-of select="$v"/></xsl:template>'
    )
    assert _run("<r/>", body) == "local"


def test_transform_foreign_prefixed_literal_element() -> None:
    body = '<xsl:template match="/"><out xmlns:svg="http://www.w3.org/2000/svg"><svg:rect/></out></xsl:template>'
    result = _run("<r/>", body, method="xml")
    assert "<svg:rect" in result


def test_transform_document_returns_empty_node_set() -> None:
    body = '<xsl:template match="/"><xsl:value-of select="count(document(\'a.xml\'))"/></xsl:template>'
    assert _run("<r/>", body) == "0"


@pytest.mark.parametrize(
    ("expr", "message"),
    [
        pytest.param("key('k')", "two arguments", id="key-arity"),
        pytest.param("generate-id(1)", "wants a node-set", id="generate-id-type"),
        pytest.param("format-number(1)", "at least two", id="format-number-arity"),
    ],
)
def test_transform_extension_function_errors(expr: str, message: str) -> None:
    body = (
        f'<xsl:key name="k" match="n" use="."/><xsl:template match="/"><xsl:value-of select="{expr}"/></xsl:template>'
    )
    with pytest.raises((ValueError, TypeError), match=message):
        _run("<r><n>a</n></r>", body)


def test_transform_avt_with_bad_expression_raises() -> None:
    with pytest.raises(ValueError, match="attribute value template"):
        _run("<r/>", '<xsl:template match="/"><a href="{@(}">x</a></xsl:template>', method="xml")


def test_transform_avt_expression_evaluation_error_raises() -> None:
    with pytest.raises(ValueError, match="unbound"):
        _run("<r/>", '<xsl:template match="/"><a href="{$undefined}">x</a></xsl:template>', method="xml")


@pytest.mark.parametrize(
    "body",
    [
        pytest.param('<xsl:template match="/"><xsl:apply-templates select="@("/></xsl:template>', id="apply-select"),
        pytest.param('<xsl:template match="/"><xsl:copy-of select="@("/></xsl:template>', id="copy-of-select"),
        pytest.param(
            '<xsl:template match="/"><xsl:for-each select="@(">x</xsl:for-each></xsl:template>', id="for-each-select"
        ),
        pytest.param('<xsl:template match="/"><xsl:if test="@(">x</xsl:if></xsl:template>', id="if-test"),
        pytest.param('<xsl:template match="/"><xsl:number value="@("/></xsl:template>', id="number-value"),
        pytest.param(
            '<xsl:template match="/"><xsl:variable name="v" select="@("/><xsl:value-of select="$v"/></xsl:template>',
            id="variable-select",
        ),
        pytest.param(
            '<xsl:template match="/"><xsl:for-each select="//n"><xsl:sort select="@("/></xsl:for-each></xsl:template>',
            id="sort-select",
        ),
    ],
)
def test_transform_malformed_expressions_raise(body: str) -> None:
    with pytest.raises(ValueError, match="xslt"):
        _run("<r><n/></r>", body)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        pytest.param(
            '<xsl:template match="/"><xsl:call-template name="t"/>'
            '</xsl:template><xsl:template name="t"><xsl:param/></xsl:template>',
            "param requires",
            id="param-name",
        ),
        pytest.param(
            '<xsl:template match="/"><xsl:apply-templates>'
            '<xsl:with-param select="1"/></xsl:apply-templates></xsl:template>',
            "with-param requires",
            id="with-param-name",
        ),
        pytest.param(
            '<xsl:template match="/"><out><xsl:attribute>x</xsl:attribute></out></xsl:template>',
            "attribute requires",
            id="attr-name",
        ),
        pytest.param(
            '<xsl:template match="/"><xsl:processing-instruction>x</xsl:processing-instruction></xsl:template>',
            "processing-instruction requires",
            id="pi-name",
        ),
        pytest.param("<xsl:variable/>", "requires a name", id="global-variable-name"),
        pytest.param('<xsl:key match="n" use="."/>', "requires name", id="key-name"),
    ],
)
def test_transform_missing_required_attributes(body: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _run("<r><n/></r>", body)


def test_transform_bad_match_pattern_raises() -> None:
    with pytest.raises(ValueError, match="match pattern"):
        _run("<r/>", '<xsl:template match="n[">x</xsl:template>')


def test_transform_bad_key_use_raises() -> None:
    with pytest.raises(ValueError, match="key use"):
        _run("<r/>", '<xsl:key name="k" match="n" use="@("/><xsl:template match="/">x</xsl:template>')


def test_transform_copy_of_bad_select_raises() -> None:
    with pytest.raises(ValueError, match="copy-of select"):
        _run("<r/>", '<xsl:template match="/"><xsl:copy-of select="@("/></xsl:template>')


def test_transform_call_template_with_param_default_and_recursion_deep() -> None:
    body = (
        '<xsl:template match="/"><xsl:call-template name="c">'
        '<xsl:with-param name="n" select="20"/></xsl:call-template></xsl:template>'
        '<xsl:template name="c"><xsl:param name="n"/>'
        '<xsl:if test="$n &gt; 0"><xsl:value-of select="$n"/>,<xsl:call-template name="c">'
        '<xsl:with-param name="n" select="$n - 1"/></xsl:call-template></xsl:if></xsl:template>'
    )
    assert _run("<r/>", body).startswith("20,19,")


def test_transform_id_pattern_and_slashes() -> None:
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="//n"/></xsl:template>'
        '<xsl:template match="//n">deep</xsl:template>'
    )
    assert _run("<r><g><n/></g></r>", body) == "deep"


@pytest.mark.parametrize(
    "body",
    [
        pytest.param('<xsl:template match="/"><xsl:value-of select="$undef"/></xsl:template>', id="value-of"),
        pytest.param('<xsl:template match="/"><xsl:apply-templates select="$undef"/></xsl:template>', id="apply"),
        pytest.param(
            '<xsl:template match="/"><xsl:for-each select="$undef">x</xsl:for-each></xsl:template>', id="for-each"
        ),
        pytest.param('<xsl:template match="/"><xsl:if test="$undef">x</xsl:if></xsl:template>', id="if"),
        pytest.param('<xsl:template match="/"><xsl:copy-of select="$undef"/></xsl:template>', id="copy-of"),
        pytest.param('<xsl:template match="/"><xsl:number value="$undef"/></xsl:template>', id="number-value"),
        pytest.param(
            '<xsl:template match="/"><xsl:variable name="v" select="$undef"/>'
            '<xsl:value-of select="$v"/></xsl:template>',
            id="variable",
        ),
        pytest.param('<xsl:template match="/"><a href="{$undef}"/></xsl:template>', id="avt"),
        pytest.param(
            '<xsl:template match="/"><xsl:for-each select="//n">'
            '<xsl:sort select="$undef"/></xsl:for-each></xsl:template>',
            id="sort",
        ),
        pytest.param(
            '<xsl:template match="/"><xsl:call-template name="t"><xsl:with-param name="p" select="$undef"/>'
            '</xsl:call-template></xsl:template><xsl:template name="t"/>',
            id="with-param",
        ),
    ],
)
def test_transform_evaluation_errors_propagate(body: str) -> None:
    with pytest.raises(ValueError, match=r"unbound|expression"):
        _run("<r><n/><n/></r>", body, method="xml")


def test_transform_key_use_evaluation_error() -> None:
    body = (
        '<xsl:key name="k" match="n" use="$undef"/><xsl:template match="/">'
        "<xsl:value-of select=\"key('k','x')\"/></xsl:template>"
    )
    with pytest.raises(ValueError, match="unbound"):
        _run("<r><n/></r>", body)


def test_transform_global_variable_evaluation_error() -> None:
    body = (
        '<xsl:variable name="g" select="$missing"/><xsl:template match="/"><xsl:value-of select="$g"/></xsl:template>'
    )
    with pytest.raises(ValueError, match="unbound"):
        _run("<r/>", body)


def test_transform_pattern_with_surrounding_whitespace() -> None:
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="//n"/></xsl:template>'
        '<xsl:template match=" n "> hit </xsl:template>'
    )
    assert _run("<r><n/></r>", body) == " hit "


def test_transform_processing_instruction_node_test_priority() -> None:
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="r/node()"/></xsl:template>'
        '<xsl:template match="node()"/>'
        '<xsl:template match="processing-instruction()">P</xsl:template>'
    )
    assert _run("<r><?go x?></r>", body) == "P"


def test_transform_key_lookup_miss_and_name_mismatch() -> None:
    body = (
        '<xsl:key name="k1" match="i" use="@a"/><xsl:key name="k2" match="i" use="@b"/>'
        "<xsl:template match=\"/\">[<xsl:value-of select=\"count(key('k2','no'))\"/>]"
        "[<xsl:value-of select=\"key('k2','yes')/@id\"/>]</xsl:template>"
    )
    assert _run('<r><i a="x" b="yes" id="Z"/></r>', body) == "[0][Z]"


def test_transform_generate_id_no_argument_and_node_set() -> None:
    body = '<xsl:template match="/"><xsl:value-of select="generate-id() = generate-id(//n)"/></xsl:template>'
    assert _run("<r><n/></r>", body) == "false"


def test_transform_format_number_without_trailing_zero_to_trim() -> None:
    body = '<xsl:template match="/"><xsl:value-of select="format-number(1.25, \'0.##\')"/></xsl:template>'
    assert _run("<r/>", body) == "1.25"


def test_transform_key_use_over_many_nodes_grows_bucket() -> None:
    items = "".join('<i k="same"/>' for _ in range(8))
    body = (
        '<xsl:key name="k" match="i" use="@k"/>'
        "<xsl:template match=\"/\"><xsl:value-of select=\"count(key('k','same'))\"/></xsl:template>"
    )
    assert _run(f"<r>{items}</r>", body) == "8"


@pytest.mark.parametrize(
    "body",
    [
        pytest.param('<out><xsl:comment><xsl:value-of select="$u"/></xsl:comment></out>', id="comment-body"),
        pytest.param(
            '<out><xsl:processing-instruction name="p"><xsl:value-of select="$u"/></xsl:processing-instruction></out>',
            id="pi-body",
        ),
        pytest.param(
            '<out><xsl:attribute name="a"><xsl:value-of select="$u"/></xsl:attribute></out>', id="attribute-body"
        ),
        pytest.param('<xsl:element name="{$u}"/>', id="element-name"),
        pytest.param(
            '<xsl:element name="e"><xsl:attribute name="{$u}">v</xsl:attribute></xsl:element>', id="attribute-name"
        ),
        pytest.param('<xsl:processing-instruction name="{$u}"/>', id="pi-name"),
        pytest.param('<xsl:copy-of select="$u"/>', id="copy-of"),
        pytest.param('<out><xsl:copy><xsl:value-of select="$u"/></xsl:copy></out>', id="copy-body"),
        pytest.param('<xsl:choose><xsl:when test="$u">x</xsl:when></xsl:choose>', id="when-test"),
        pytest.param('<xsl:message terminate="yes"><xsl:value-of select="$u"/></xsl:message>', id="message-body"),
        pytest.param(
            '<xsl:variable name="v"><xsl:value-of select="$u"/></xsl:variable><xsl:value-of select="$v"/>',
            id="rtf-body",
        ),
        pytest.param(
            '<xsl:apply-templates select="//n"><xsl:sort select="$u"/></xsl:apply-templates>', id="apply-sort"
        ),
    ],
)
def test_transform_nested_evaluation_errors_propagate(body: str) -> None:
    with pytest.raises((ValueError, RuntimeError)):
        _run("<r><n/><n/></r>", f'<xsl:template match="/">{body}</xsl:template>', method="xml")


def test_transform_avt_escaped_close_brace() -> None:
    body = '<xsl:template match="/"><a v="a}}b">x</a></xsl:template>'
    assert _run("<r/>", body, method="xml") == '<a v="a}b">x</a>'


def test_transform_copy_of_attributes() -> None:
    body = '<xsl:template match="/"><out><xsl:copy-of select="//n/@*"/></out></xsl:template>'
    result = _run('<r><n a="1" b="2"/></r>', body, method="xml")
    assert 'a="1"' in result
    assert 'b="2"' in result


def test_transform_attribute_instruction_at_root_is_ignored() -> None:
    body = '<xsl:template match="/">[<xsl:attribute name="a">v</xsl:attribute>]</xsl:template>'
    assert _run("<r/>", body) == "[]"


def test_transform_copy_of_processing_instruction() -> None:
    body = '<xsl:template match="/"><out><xsl:copy-of select="//processing-instruction()"/></out></xsl:template>'
    assert "<?pi x?>" in _run("<r><?pi x?></r>", body, method="xml")


def test_transform_sort_without_select_uses_context() -> None:
    body = (
        '<xsl:template match="/"><xsl:for-each select="r/n"><xsl:sort/>'
        '<xsl:value-of select="."/></xsl:for-each></xsl:template>'
    )
    assert _run("<r><n>b</n><n>a</n></r>", body) == "ab"


def test_transform_sort_single_node_is_noop() -> None:
    body = (
        '<xsl:template match="/"><xsl:for-each select="r/n"><xsl:sort select="."/>'
        '<xsl:value-of select="."/></xsl:for-each></xsl:template>'
    )
    assert _run("<r><n>only</n></r>", body) == "only"


def test_transform_sort_numeric_with_non_numeric_values() -> None:
    body = (
        '<xsl:template match="/"><xsl:for-each select="r/n"><xsl:sort select="." data-type="number"/>'
        '<xsl:value-of select="."/>,</xsl:for-each></xsl:template>'
    )
    assert _run("<r><n>3</n><n>x</n><n>1</n></r>", body) == "x,1,3,"


def test_transform_default_priority_function_pattern() -> None:
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="r/*"/></xsl:template>'
        '<xsl:template match="id(\'never\')" priority="0.5">ID</xsl:template>'
        '<xsl:template match="*">S</xsl:template>'
    )
    assert _run("<r><a/></r>", body) == "S"


def test_transform_key_pattern_anchored() -> None:
    body = (
        '<xsl:key name="k" match="n" use="@id"/>'
        "<xsl:template match=\"/\"><xsl:apply-templates select=\"key('k','a')\"/></xsl:template>"
        '<xsl:template match="n">hit </xsl:template>'
    )
    assert _run('<r><n id="a"/><n id="b"/><n id="a"/></r>', body) == "hit hit "


def test_transform_match_pattern_uses_undeclared_key_raises() -> None:
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="r/n"/></xsl:template>'
        "<xsl:template match=\"key('none','x')\">x</xsl:template>"
        '<xsl:template match="n">n</xsl:template>'
    )
    with pytest.raises(ValueError, match="undeclared key"):
        _run("<r><n/></r>", body)


def test_transform_apply_templates_to_attribute_children_are_empty() -> None:
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="//n/@id"/></xsl:template>'
        '<xsl:template match="@id">[<xsl:apply-templates/>]</xsl:template>'
    )
    assert _run('<r><n id="A"/></r>', body) == "[]"


def test_transform_literal_element_default_namespace_declaration() -> None:
    body = '<xsl:template match="/"><out xmlns="urn:x"><in/></out></xsl:template>'
    result = _run("<r/>", body, method="xml")
    # XSLT 1.0 section 7.1.1: the default-namespace node is copied to the output element and the
    # child inherits it (declared once), as libxslt/lxml emit it.
    assert '<out xmlns="urn:x"><in/></out>' in result


def test_transform_stylesheet_with_top_level_comment() -> None:
    text = (
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<!--a comment--><xsl:output method="text"/>'
        '<xsl:template match="/">ok</xsl:template></xsl:stylesheet>'
    )
    assert transform(turbohtml.parse_xml(text), turbohtml.parse_xml("<r/>")) == "ok"


def test_transform_stylesheet_body_comment_is_ignored() -> None:
    body = '<xsl:template match="/"><!--skip me-->kept</xsl:template>'
    assert _run("<r/>", body) == "kept"


def test_transform_many_named_templates() -> None:
    named = "".join(f'<xsl:template name="t{index}">{index},</xsl:template>' for index in range(12))
    calls = "".join(f'<xsl:call-template name="t{index}"/>' for index in range(12))
    body = f'<xsl:template match="/">{calls}</xsl:template>{named}'
    assert _run("<r/>", body) == "".join(f"{index}," for index in range(12))


def test_transform_many_keys() -> None:
    keys = "".join(f'<xsl:key name="k{index}" match="i" use="@a{index}"/>' for index in range(8))
    body = f"{keys}<xsl:template match=\"/\"><xsl:value-of select=\"count(key('k5','v'))\"/></xsl:template>"
    assert _run('<r><i a5="v"/></r>', body) == "1"


def test_transform_top_level_param_bad_expression_raises() -> None:
    body = '<xsl:param name="p" select="\'d\'"/><xsl:template match="/"><xsl:value-of select="$p"/></xsl:template>'
    with pytest.raises(ValueError, match="parameter expression"):
        _run("<r/>", body, p="@(")


def test_transform_large_match_and_key_sets_force_hash_collisions() -> None:
    items = "".join(f'<i k="v{index}">{index}</i>' for index in range(100))
    body = (
        '<xsl:key name="k" match="i" use="@k"/>'
        '<xsl:template match="/"><xsl:apply-templates select="r/i"/></xsl:template>'
        '<xsl:template match="i"><xsl:value-of select="count(key(\'k\', @k))"/></xsl:template>'
    )
    assert _run(f"<r>{items}</r>", body) == "1" * 100


def test_transform_union_with_double_quote_literal() -> None:
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="r/*"/></xsl:template>'
        '<xsl:template match="a[@x=&quot;|&quot;] | b">hit </xsl:template>'
    )
    assert _run('<r><a x="|"/><b/></r>', body) == "hit hit "


def test_transform_predicate_with_union_inside() -> None:
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="r/*"/></xsl:template>'
        '<xsl:template match="a[b|c]">hit </xsl:template>'
    )
    assert _run("<r><a><b/></a><a><c/></a><a/></r>", body) == "hit hit "


def test_transform_processing_instruction_literal_pattern_priority() -> None:
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="r/node()"/></xsl:template>'
        '<xsl:template match="node()"/>'
        "<xsl:template match=\"processing-instruction('go')\">P</xsl:template>"
    )
    # The reused XPath engine matches processing-instruction() regardless of the literal target.
    assert _run("<r><?go x?><?stop y?></r>", body) == "PP"


def test_transform_unknown_function_raises() -> None:
    with pytest.raises(ValueError, match="function"):
        _run("<r/>", '<xsl:template match="/"><xsl:value-of select="nosuchfunc()"/></xsl:template>')


def test_transform_key_with_empty_table_lookup_misses() -> None:
    body = (
        '<xsl:key name="k" match="absent" use="@x"/>'
        "<xsl:template match=\"/\">[<xsl:value-of select=\"count(key('k','v'))\"/>]</xsl:template>"
    )
    assert _run("<r><n/></r>", body) == "[0]"


def test_transform_id_pattern_with_space_before_paren() -> None:
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="r/*"/></xsl:template>'
        '<xsl:template match="id (\'never\')" priority="0.5">ID</xsl:template>'
        '<xsl:template match="*">S</xsl:template>'
    )
    assert _run("<r><a/></r>", body) == "S"


def test_transform_pattern_with_variable_reference_errors() -> None:
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="r/n"/></xsl:template>'
        '<xsl:template match="n[$undef]">x</xsl:template>'
        '<xsl:template match="n">n</xsl:template>'
    )
    with pytest.raises(ValueError, match="unbound"):
        _run("<r><n/></r>", body)


def test_transform_second_sort_key_bad_select_raises() -> None:
    body = (
        '<xsl:template match="/"><xsl:for-each select="//n">'
        '<xsl:sort select="."/><xsl:sort select="@("/></xsl:for-each></xsl:template>'
    )
    with pytest.raises(ValueError, match="sort"):
        _run("<r><n/><n/></r>", body)


def test_transform_sort_numeric_two_non_numeric_values() -> None:
    body = (
        '<xsl:template match="/"><xsl:for-each select="r/n"><xsl:sort select="." data-type="number"/>'
        '<xsl:value-of select="."/>,</xsl:for-each></xsl:template>'
    )
    assert _run("<r><n>x</n><n>y</n></r>", body) == "x,y,"


def test_transform_second_with_param_bad_select_raises() -> None:
    body = (
        '<xsl:template match="/"><xsl:call-template name="t">'
        '<xsl:with-param name="a" select="1"/><xsl:with-param name="b" select="$undef"/>'
        "</xsl:call-template></xsl:template>"
        '<xsl:template name="t"/>'
    )
    with pytest.raises(ValueError, match="unbound"):
        _run("<r/>", body)


def test_transform_param_default_bad_expression_raises() -> None:
    body = (
        '<xsl:template match="/"><xsl:call-template name="t"/></xsl:template>'
        '<xsl:template name="t"><xsl:param name="p" select="$undef"/><xsl:value-of select="$p"/></xsl:template>'
    )
    with pytest.raises(ValueError, match="unbound"):
        _run("<r/>", body)


def test_transform_with_param_node_set_value() -> None:
    body = (
        '<xsl:template match="/"><xsl:call-template name="t">'
        '<xsl:with-param name="p" select="//n"/></xsl:call-template></xsl:template>'
        '<xsl:template name="t"><xsl:param name="p"/><xsl:value-of select="count($p)"/></xsl:template>'
    )
    assert _run("<r><n/><n/></r>", body) == "2"


def test_transform_apply_templates_node_set_param() -> None:
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="//n">'
        '<xsl:with-param name="p" select="//n"/></xsl:apply-templates></xsl:template>'
        '<xsl:template match="n"><xsl:param name="p"/><xsl:value-of select="count($p)"/></xsl:template>'
    )
    assert _run("<r><n/><n/></r>", body) == "22"


def test_transform_call_template_without_name_raises() -> None:
    with pytest.raises(ValueError, match="call-template requires"):
        _run("<r/>", '<xsl:template match="/"><xsl:call-template/></xsl:template>')


def test_transform_apply_templates_non_node_set_select_raises() -> None:
    with pytest.raises(ValueError, match="not a node-set"):
        _run("<r/>", '<xsl:template match="/"><xsl:apply-templates select="1 + 1"/></xsl:template>')


def test_transform_apply_templates_bad_sort_select_raises() -> None:
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="//n">'
        '<xsl:sort select="@("/></xsl:apply-templates></xsl:template>'
    )
    with pytest.raises(ValueError, match="sort"):
        _run("<r><n/></r>", body)


def test_transform_variable_without_name_raises() -> None:
    with pytest.raises(ValueError, match="variable requires"):
        _run("<r/>", '<xsl:template match="/"><xsl:variable select="1"/></xsl:template>')


def test_transform_key_with_bad_match_pattern_raises() -> None:
    body = '<xsl:key name="k" match="n[" use="@x"/><xsl:template match="/">x</xsl:template>'
    with pytest.raises(ValueError, match="match pattern"):
        _run("<r/>", body)


def test_transform_top_level_param_evaluation_error_raises() -> None:
    body = '<xsl:param name="p" select="\'d\'"/><xsl:template match="/"><xsl:value-of select="$p"/></xsl:template>'
    with pytest.raises(ValueError, match="unbound"):
        _run("<r/>", body, p="$undef")


def test_transform_missing_arguments_raise() -> None:
    with pytest.raises(TypeError):
        _xslt_transform()  # ty: ignore[missing-argument]  # too few args on purpose


def test_transform_non_node_source_raises() -> None:
    convert = Transform(_sheet('<xsl:template match="/">x</xsl:template>'))
    with pytest.raises(TypeError):
        convert("not a node")  # ty: ignore[invalid-argument-type]  # wrong type on purpose


def test_transform_unknown_xsl_element_instantiates_nothing() -> None:
    body = '<xsl:template match="/">a<xsl:fallback/>b</xsl:template>'
    assert _run("<r/>", body) == "ab"


def test_transform_second_with_param_missing_name_raises() -> None:
    body = (
        '<xsl:template match="/"><xsl:call-template name="t">'
        '<xsl:with-param name="a" select="1"/><xsl:with-param select="2"/></xsl:call-template></xsl:template>'
        '<xsl:template name="t"/>'
    )
    with pytest.raises(ValueError, match="with-param requires"):
        _run("<r/>", body)


def test_transform_element_with_empty_name() -> None:
    body = '<xsl:template match="/"><out><xsl:element name="{//none}">x</xsl:element></out></xsl:template>'
    assert "x" in _run("<r/>", body, method="xml")


def test_transform_html_source_with_template_content() -> None:
    source = turbohtml.parse("<html><body><template><p>hi</p></template></body></html>")
    sheet = _sheet('<xsl:template match="/"><xsl:apply-templates/></xsl:template>')
    assert transform(sheet, source) == "hi"


def test_transform_whitespace_only_match_pattern_raises() -> None:
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="r/n"/></xsl:template>'
        '<xsl:template match="  ">ws</xsl:template><xsl:template match="n">n</xsl:template>'
    )
    with pytest.raises(ValueError, match="match pattern"):
        _run("<r><n/></r>", body)


def test_transform_three_char_relative_pattern() -> None:
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="//abc"/></xsl:template>'
        '<xsl:template match="abc">hit</xsl:template>'
    )
    assert _run("<r><abc/></r>", body) == "hit"


def test_transform_number_on_text_nodes() -> None:
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="r/text()"/></xsl:template>'
        '<xsl:template match="text()"><xsl:number/>-</xsl:template>'
    )
    assert _run("<r>a<!--c-->b</r>", body) == "1-2-"


def test_transform_key_node_set_argument_miss() -> None:
    body = (
        '<xsl:key name="k" match="i" use="@c"/>'
        '<xsl:template match="/"><xsl:value-of select="count(key(\'k\', r/want/@c))"/></xsl:template>'
    )
    assert _run('<r><want c="none"/><i c="x"/></r>', body) == "0"


def test_transform_avt_with_double_quote_expression() -> None:
    body = '<xsl:template match="/"><a v="{concat(&quot;p&quot;, &quot;q&quot;)}">t</a></xsl:template>'
    assert _run("<r/>", body, method="xml") == '<a v="pq">t</a>'


def test_transform_avt_lone_close_brace_literal() -> None:
    body = '<xsl:template match="/"><a v="x}">t</a></xsl:template>'
    assert _run("<r/>", body, method="xml") == '<a v="x}">t</a>'


def test_transform_attribute_template_with_non_dot_select() -> None:
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="//n/@id"/></xsl:template>'
        '<xsl:template match="@id"><xsl:value-of select="\'lit\'"/></xsl:template>'
    )
    assert _run('<r><n id="A"/></r>', body) == "lit"


def test_transform_copy_of_attributes_at_root_are_dropped() -> None:
    body = '<xsl:template match="/"><xsl:copy-of select="//n/@*"/>done</xsl:template>'
    assert _run('<r><n a="1"/></r>', body) == "done"


def test_transform_copy_of_rtf_variable_with_others_in_scope() -> None:
    body = (
        '<xsl:template match="/"><xsl:variable name="a" select="1"/>'
        '<xsl:variable name="frag"><b>x</b></xsl:variable>'
        '<out><xsl:copy-of select="$frag"/></out></xsl:template>'
    )
    assert _collapse(_run("<r/>", body, method="xml")) == "<out><b>x</b></out>"


def test_transform_copy_of_processing_instruction_identity() -> None:
    body = '<xsl:template match="/"><out><xsl:copy-of select="//processing-instruction()"/></out></xsl:template>'
    assert "<?go x?>" in _run("<r><?go x?></r>", body, method="xml")


def test_transform_sort_equal_length_equal_keys() -> None:
    body = (
        '<xsl:template match="/"><xsl:for-each select="r/n"><xsl:sort select="."/>'
        '<xsl:value-of select="@id"/></xsl:for-each></xsl:template>'
    )
    assert _run('<r><n id="1">aa</n><n id="2">aa</n></r>', body) == "12"


def test_transform_number_empty_format_attribute() -> None:
    body = '<xsl:template match="/"><xsl:for-each select="r/n"><xsl:number format=""/></xsl:for-each></xsl:template>'
    assert _run("<r><n/><n/></r>", body) == "12"


def test_transform_xsl_text_with_comment_child() -> None:
    body = '<xsl:template match="/"><xsl:text>hi<!--c--></xsl:text></xsl:template>'
    assert _run("<r/>", body) == "hi"


def test_transform_stylesheet_without_output_declaration_defaults_to_xml() -> None:
    sheet = turbohtml.parse_xml(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:template match="/"><out/></xsl:template></xsl:stylesheet>'
    )
    assert transform(sheet, turbohtml.parse_xml("<r/>")) == '<?xml version="1.0"?>\n<out/>'


def test_transform_omit_xml_declaration_no_keeps_declaration() -> None:
    sheet = _sheet(
        '<xsl:output method="xml" omit-xml-declaration="no"/><xsl:template match="/"><out/></xsl:template>',
        method="",
    )
    assert transform(sheet, turbohtml.parse_xml("<r/>")) == '<?xml version="1.0"?>\n<out/>'


def test_transform_stylesheet_with_leading_comment_before_root() -> None:
    sheet = turbohtml.parse_xml(
        '<!--lead--><xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:output method="text"/><xsl:template match="/">ok</xsl:template></xsl:stylesheet>'
    )
    assert transform(sheet, turbohtml.parse_xml("<r/>")) == "ok"


def test_transform_more_format_number_fraction_cases() -> None:
    cases = {
        "1.0": "1",
        "1.50": "1.5",
        "1.234": "1.23",
        "10": "10",
    }
    for value, expected in cases.items():
        body = f'<xsl:template match="/"><xsl:value-of select="format-number({value}, \'#.##\')"/></xsl:template>'
        assert _run("<r/>", body) == expected


def test_transform_attribute_template_single_char_select() -> None:
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="//n/@id"/></xsl:template>'
        '<xsl:template match="@id">[<xsl:value-of select="p"/>]</xsl:template>'
    )
    assert _run('<r><n id="A"/></r>', body) == "[]"


def test_transform_copy_of_non_rtf_variable() -> None:
    body = (
        '<xsl:template match="/"><xsl:variable name="v" select="//n"/>'
        '<out><xsl:copy-of select="$v"/></out></xsl:template>'
    )
    assert _collapse(_run("<r><n>x</n></r>", body, method="xml")) == "<out><n>x</n></out>"


def test_transform_sort_explicit_text_data_type() -> None:
    body = (
        '<xsl:template match="/"><xsl:for-each select="r/n"><xsl:sort select="." data-type="text"/>'
        '<xsl:value-of select="."/></xsl:for-each></xsl:template>'
    )
    assert _run("<r><n>b</n><n>a</n></r>", body) == "ab"


def test_transform_text_output_of_nested_elements() -> None:
    body = '<xsl:template match="/"><wrap><inner>hi</inner> there</wrap></xsl:template>'
    assert _run("<r/>", body) == "hi there"


def test_transform_error_during_multi_node_apply_templates() -> None:
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="//n"/></xsl:template>'
        '<xsl:template match="n"><xsl:value-of select="$undef"/></xsl:template>'
    )
    with pytest.raises(ValueError, match="unbound"):
        _run("<r><n/><n/></r>", body)


def test_transform_error_during_builtin_recursion() -> None:
    body = (
        '<xsl:template match="/"><xsl:apply-templates/></xsl:template>'
        '<xsl:template match="n"><xsl:value-of select="$undef"/></xsl:template>'
    )
    with pytest.raises(ValueError, match="unbound"):
        _run("<r><n/><n/></r>", body)


def test_transform_error_during_multi_node_for_each() -> None:
    body = (
        '<xsl:template match="/"><xsl:for-each select="r/n">'
        '<xsl:value-of select="$undef"/></xsl:for-each></xsl:template>'
    )
    with pytest.raises(ValueError, match="unbound"):
        _run("<r><n/><n/></r>", body)


def test_transform_avt_lone_open_brace_raises() -> None:
    with pytest.raises(ValueError, match="attribute value template"):
        _run("<r/>", '<xsl:template match="/"><a v="x{"/></xsl:template>', method="xml")


def test_transform_avt_unclosed_expression() -> None:
    body = '<xsl:template match="/"><a v="{//n"/></xsl:template>'
    assert _run("<r><n>y</n></r>", body, method="xml") == '<a v="y"/>'


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        pytest.param("key", "hit", id="key-name"),
        pytest.param("idx", "hit", id="id-prefix-name"),
    ],
)
def test_transform_element_named_like_a_function(tag: str, expected: str) -> None:
    body = (
        f'<xsl:template match="/"><xsl:apply-templates select="//{tag}"/></xsl:template>'
        f'<xsl:template match="{tag}">{expected}</xsl:template>'
    )
    assert _run(f"<r><{tag}/></r>", body) == expected


def test_transform_matched_template_bad_param_default_raises() -> None:
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="//n"/></xsl:template>'
        '<xsl:template match="n"><xsl:param name="p" select="$undef"/><xsl:value-of select="$p"/></xsl:template>'
    )
    with pytest.raises(ValueError, match="unbound"):
        _run("<r><n/></r>", body)


def test_transform_choose_with_comment_between_branches() -> None:
    body = (
        '<xsl:template match="/"><xsl:choose><xsl:when test="false()">w</xsl:when>'
        "<!--gap--><xsl:otherwise>o</xsl:otherwise></xsl:choose></xsl:template>"
    )
    assert _run("<r/>", body) == "o"


@pytest.mark.parametrize(
    "attrs",
    [
        pytest.param('name="k" use="."', id="missing-match"),
        pytest.param('name="k" match="n"', id="missing-use"),
    ],
)
def test_transform_key_missing_required_attribute(attrs: str) -> None:
    body = f'<xsl:key {attrs}/><xsl:template match="/">x</xsl:template>'
    with pytest.raises(ValueError, match="key requires"):
        _run("<r><n/></r>", body)


def test_transform_avt_literal_close_brace_mid_string() -> None:
    body = '<xsl:template match="/"><a v="a}b"/></xsl:template>'
    assert _run("<r/>", body, method="xml") == '<a v="a}b"/>'


def test_transform_copy_of_rtf_variable_not_first_in_scope() -> None:
    body = (
        '<xsl:template match="/"><xsl:variable name="a"><x>A</x></xsl:variable>'
        '<xsl:variable name="b"><y>B</y></xsl:variable>'
        '<out><xsl:copy-of select="$a"/></out></xsl:template>'
    )
    assert _collapse(_run("<r/>", body, method="xml")) == "<out><x>A</x></out>"


def test_transform_xsl_copy_of_processing_instruction() -> None:
    body = (
        '<xsl:template match="/"><out><xsl:apply-templates select="//processing-instruction()"/></out></xsl:template>'
        '<xsl:template match="processing-instruction()"><xsl:copy/></xsl:template>'
    )
    assert "<?go x?>" in _run("<r><?go x?></r>", body, method="xml")


def test_transform_sort_mixed_length_keys() -> None:
    body = (
        '<xsl:template match="/"><xsl:for-each select="r/n"><xsl:sort select="."/>'
        '<xsl:value-of select="."/>,</xsl:for-each></xsl:template>'
    )
    assert _run("<r><n>b</n><n>aa</n><n>a</n><n>ab</n></r>", body) == "a,aa,ab,b,"


def test_transform_number_over_different_length_name_siblings() -> None:
    body = '<xsl:template match="/"><xsl:for-each select="r/ab"><xsl:number/>,</xsl:for-each></xsl:template>'
    assert _run("<r><a/><ab/><a/><ab/></r>", body) == "1,2,"


def test_transform_number_format_digits_then_letter() -> None:
    body = '<xsl:template match="/"><xsl:number value="5" format="0a"/></xsl:template>'
    assert _run("<r/>", body) == "5"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("<r><n>a</n><n>aa</n></r>", "a,aa,", id="short-first"),
        pytest.param("<r><n>aa</n><n>a</n></r>", "a,aa,", id="long-first"),
    ],
)
def test_transform_sort_prefix_length_ordering(source: str, expected: str) -> None:
    body = (
        '<xsl:template match="/"><xsl:for-each select="r/n"><xsl:sort select="."/>'
        '<xsl:value-of select="."/>,</xsl:for-each></xsl:template>'
    )
    assert _run(source, body) == expected


def test_transform_number_format_starting_with_symbol() -> None:
    body = '<xsl:template match="/"><xsl:number value="7" format="#"/></xsl:template>'
    # "#" is a leading separator (a prefix) with no format token, so the number follows it.
    assert _run("<r/>", body) == "#7"


def test_transform_number_format_digit_then_symbol() -> None:
    body = '<xsl:template match="/"><xsl:number value="7" format="0#"/></xsl:template>'
    # The "0" is the format token and the trailing "#" is a suffix.
    assert _run("<r/>", body) == "7#"


def test_transform_output_without_method_attribute() -> None:
    sheet = _sheet('<xsl:output indent="yes"/><xsl:template match="/"><out/></xsl:template>', method="")
    assert transform(sheet, turbohtml.parse_xml("<r/>")) == '<?xml version="1.0"?>\n<out/>'


def test_transform_root_with_extra_namespace_and_short_attribute() -> None:
    sheet = turbohtml.parse_xml(
        '<xsl:stylesheet version="1.0" id="s" xmlns:xsl="http://www.w3.org/1999/XSL/Transform"'
        ' xmlns:ex="urn:example"><xsl:output method="text"/><xsl:template match="/">ok</xsl:template></xsl:stylesheet>'
    )
    assert transform(sheet, turbohtml.parse_xml("<r/>")) == "ok"


def test_transform_partial_parameter_override() -> None:
    body = (
        '<xsl:param name="a" select="\'da\'"/><xsl:param name="b" select="\'db\'"/>'
        '<xsl:template match="/"><xsl:value-of select="$a"/>,<xsl:value-of select="$b"/></xsl:template>'
    )
    assert _run("<r/>", body, a="'A'") == "A,db"


def test_transform_top_level_strip_space_is_ignored() -> None:
    body = '<xsl:strip-space elements="*"/><xsl:template match="/">ok</xsl:template>'
    assert _run("<r/>", body) == "ok"


def test_transform_large_template_match_set() -> None:
    elements = "".join(f"<e>{index}</e>" for index in range(400))
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="r/e"/></xsl:template>'
        '<xsl:template match="e"><xsl:value-of select="."/>,</xsl:template>'
    )
    assert _run(f"<r>{elements}</r>", body) == "".join(f"{index}," for index in range(400))


def test_transform_stylesheet_passed_as_root_element() -> None:
    root = _sheet('<xsl:template match="/">ok</xsl:template>').root
    assert root is not None
    assert transform(root, turbohtml.parse_xml("<r/>")) == "ok"


def test_transform_apply_templates_with_sort_and_param() -> None:
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="r/n">'
        '<xsl:sort select="."/><xsl:with-param name="p" select="\'P\'"/></xsl:apply-templates></xsl:template>'
        '<xsl:template match="n"><xsl:param name="p"/>'
        '<xsl:value-of select="$p"/><xsl:value-of select="."/>,</xsl:template>'
    )
    assert _run("<r><n>b</n><n>a</n></r>", body) == "Pa,Pb,"


def test_transform_apply_templates_failing_sort_raises() -> None:
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="//n">'
        '<xsl:sort select="$undef"/></xsl:apply-templates></xsl:template>'
        '<xsl:template match="n"><xsl:value-of select="."/></xsl:template>'
    )
    with pytest.raises(ValueError, match="unbound"):
        _run("<r><n/><n/></r>", body)


def test_transform_literal_element_with_namespace_declaration() -> None:
    body = '<xsl:template match="/"><out xmlns:ex="urn:example"><inner>x</inner></out></xsl:template>'
    result = _run("<r/>", body, method="xml")
    # XSLT 1.0 section 7.1.1 copies every in-scope namespace node to the literal result element,
    # even an unreferenced prefix, matching libxslt/lxml.
    assert '<out xmlns:ex="urn:example"><inner>x</inner></out>' in result


def test_transform_literal_element_namespace_child_inherits_parent() -> None:
    body = '<xsl:template match="/"><a xmlns:p="urn:1"><p:b/></a></xsl:template>'
    assert _collapse(_run("<r/>", body, method="xml")) == '<a xmlns:p="urn:1"><p:b/></a>'


def test_transform_literal_element_namespace_rebinding_redeclares() -> None:
    body = '<xsl:template match="/"><a xmlns:p="urn:1"><p:b xmlns:p="urn:2"/></a></xsl:template>'
    assert _collapse(_run("<r/>", body, method="xml")) == '<a xmlns:p="urn:1"><p:b xmlns:p="urn:2"/></a>'


def test_transform_exclude_result_prefixes_drops_only_listed() -> None:
    body = '<xsl:template match="/"><out/></xsl:template>'
    declare = 'xmlns:keep="urn:k" xmlns:drop="urn:d" exclude-result-prefixes="drop"'
    result = transform(_sheet(body, method="xml", declare=declare), turbohtml.parse_xml("<r/>"))
    assert _collapse(result) == '<out xmlns:keep="urn:k"/>'


def test_transform_exclude_result_prefixes_default_namespace() -> None:
    body = '<xsl:template match="/"><out/></xsl:template>'
    declare = 'xmlns="urn:def" exclude-result-prefixes="#default"'
    result = transform(_sheet(body, method="xml", declare=declare), turbohtml.parse_xml("<r/>"))
    assert _collapse(result) == "<out/>"


def test_transform_namespace_dedup_skips_non_matching_ancestor_attributes() -> None:
    # The output parent carries an unrelated attribute and a same-length but different xmlns, so
    # scope lookup scans past both and the child still declares its own prefix.
    body = '<xsl:template match="/"><a foo="1" xmlns:q="urn:q"><p:b xmlns:p="urn:1"/></a></xsl:template>'
    result = _collapse(_run("<r/>", body, method="xml"))
    assert result == '<a xmlns:q="urn:q" foo="1"><p:b xmlns:p="urn:1"/></a>'


def test_transform_namespace_rebinding_to_shorter_uri_redeclares() -> None:
    body = '<xsl:template match="/"><a xmlns:p="urn:11"><p:b xmlns:p="urn:2"/></a></xsl:template>'
    assert _collapse(_run("<r/>", body, method="xml")) == '<a xmlns:p="urn:11"><p:b xmlns:p="urn:2"/></a>'


def test_transform_exclude_result_prefixes_multiple_whitespace_separated() -> None:
    body = '<xsl:template match="/"><out/></xsl:template>'
    declare = 'xmlns:keep="urn:k" xmlns:drop="urn:d" xmlns:drop2="urn:d2" exclude-result-prefixes=" drop  drop2 "'
    result = transform(_sheet(body, method="xml", declare=declare), turbohtml.parse_xml("<r/>"))
    assert _collapse(result) == '<out xmlns:keep="urn:k"/>'


def test_transform_literal_element_keeps_non_xsl_prefixed_attribute() -> None:
    body = '<xsl:template match="/"><out abc:x="1" xmlns:abc="urn:a"/></xsl:template>'
    assert _collapse(_run("<r/>", body, method="xml")) == '<out xmlns:abc="urn:a" abc:x="1"/>'


def test_transform_html_method_omits_namespace_nodes() -> None:
    body = '<xsl:template match="/"><out xmlns:x="urn:x"/></xsl:template>'
    assert "xmlns" not in _run("<r/>", body, method="html")


def test_transform_literal_element_strips_xsl_directive_attribute() -> None:
    body = '<xsl:template match="/"><out xsl:exclude-result-prefixes="foo">t</out></xsl:template>'
    assert _collapse(_run("<r/>", body, method="xml")) == "<out>t</out>"


@pytest.mark.parametrize(
    ("fmt", "expected"),
    [
        pytest.param("1. ", "1. ", id="decimal-suffix"),
        pytest.param("(1)", "(1)", id="prefix-and-suffix"),
        pytest.param("~", "~1", id="above-z-separator-is-prefix"),
        pytest.param("001", "001", id="decimal-min-width"),
        pytest.param("a", "a", id="alpha-lower"),
        pytest.param("i", "i", id="roman-lower"),
    ],
)
def test_transform_number_format_tokens(fmt: str, expected: str) -> None:
    body = f'<xsl:template match="/"><xsl:number value="1" format="{fmt}"/></xsl:template>'
    assert _run("<r/>", body) == expected


@pytest.mark.parametrize(
    ("value", "fmt", "expected"),
    [
        pytest.param("0", "i", "0", id="roman-zero-falls-to-decimal"),
        pytest.param("-3", "a", "-3", id="alpha-negative-falls-to-decimal"),
        pytest.param("5000", "I", "5000", id="roman-over-4999-falls-to-decimal"),
    ],
)
def test_transform_number_out_of_range_falls_back_to_decimal(value: str, fmt: str, expected: str) -> None:
    body = f'<xsl:template match="/"><xsl:number value="{value}" format="{fmt}"/></xsl:template>'
    assert _run("<r/>", body) == expected


def test_transform_literal_element_with_long_attribute_name() -> None:
    body = '<xsl:template match="/"><td colspan="2">x</td></xsl:template>'
    assert _run("<r/>", body, method="xml") == '<td colspan="2">x</td>'


def test_transform_many_equal_priority_templates_document_order() -> None:
    templates = "".join(f'<xsl:template match="x">{index}</xsl:template>' for index in range(6))
    body = f'<xsl:template match="/"><xsl:apply-templates select="r/x"/></xsl:template>{templates}'
    # All six rules share priority 0, so the qsort tiebreak orders them by document
    # position; the last one declared wins.
    assert _run("<r><x/></r>", body) == "5"


def test_transform_equal_priority_tie_break_across_a_higher_priority_rule() -> None:
    # Two equal-priority rules (a, c) separated by a higher-priority one (b): the sort
    # compares the pair with the later-declared rule as the left operand, exercising the
    # other arm of the document-position tiebreak that a plain reversal never reaches.
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="r/*"/></xsl:template>'
        '<xsl:template match="a" priority="5">A</xsl:template>'
        '<xsl:template match="b" priority="9">B</xsl:template>'
        '<xsl:template match="c" priority="5">C</xsl:template>'
    )
    assert _run("<r><a/><b/><c/></r>", body) == "ABC"


def test_transform_strip_space_removes_whitespace_only_text() -> None:
    body = '<xsl:strip-space elements="p"/><xsl:template match="/"><xsl:apply-templates select="//p"/></xsl:template>'
    assert _run("<r><p>  <b>x</b>  </p></r>", body) == "x"


def test_transform_preserve_space_keeps_whitespace() -> None:
    body = (
        '<xsl:strip-space elements="*"/><xsl:preserve-space elements="p"/>'
        '<xsl:template match="/"><xsl:apply-templates select="//p"/></xsl:template>'
    )
    assert _run("<r><p> <b>x</b> </p></r>", body) == " x "


def test_transform_strip_space_honors_xml_space_preserve() -> None:
    body = '<xsl:strip-space elements="p"/><xsl:template match="/"><xsl:apply-templates select="//p"/></xsl:template>'
    assert _run('<r><p xml:space="preserve"> <b>x</b> </p></r>', body) == " x "


def test_transform_strip_space_prefixed_wildcard() -> None:
    body = '<xsl:strip-space elements="n:*"/><xsl:template match="/"><xsl:apply-templates/></xsl:template>'
    result = _run('<doc><n:p xmlns:n="urn:n"> <b>x</b> </n:p></doc>', body)
    assert result == "x"


def test_transform_strip_space_leaves_source_tree_unchanged() -> None:
    source = turbohtml.parse_xml("<r><p>  <b>x</b>  </p></r>")
    root = source.root
    assert root is not None
    body = '<xsl:strip-space elements="p"/><xsl:template match="/"><xsl:apply-templates select="//p"/></xsl:template>'
    before = len(root.children[0].children)
    transform(_sheet(body), source)
    assert len(root.children[0].children) == before


def test_transform_strip_space_requires_elements_attribute() -> None:
    with pytest.raises(ValueError, match="requires an elements attribute"):
        _run("<r/>", '<xsl:strip-space/><xsl:template match="/">x</xsl:template>')


def test_transform_attribute_set_applied_to_literal_element() -> None:
    body = (
        '<xsl:attribute-set name="s"><xsl:attribute name="a">1</xsl:attribute></xsl:attribute-set>'
        '<xsl:template match="/"><out xsl:use-attribute-sets="s">x</out></xsl:template>'
    )
    assert _collapse(_run("<r/>", body, method="xml")) == '<out a="1">x</out>'


def test_transform_attribute_set_uses_rebound_xslt_prefix() -> None:
    body = (
        '<xsl:attribute-set name="s"><xsl:attribute name="a">1</xsl:attribute></xsl:attribute-set>'
        '<xsl:template match="/"><out xmlns:t="http://www.w3.org/1999/XSL/Transform" '
        't:aaaaaaaaaaaaaaaaaa="ignored" t:use-attribute-sets="s"/></xsl:template>'
    )
    assert _collapse(_run("<r/>", body, method="xml")) == '<out a="1"/>'


def test_transform_unbound_attribute_prefix_is_literal() -> None:
    body = (
        '<xsl:template match="/"><out xmlns:t="http://www.w3.org/1999/XSL/Transform" xmlns:u="urn:literal" '
        'u:use-attribute-sets="s"/></xsl:template>'
    )
    sheet = _sheet(body, method="xml")
    out = sheet.find("out")
    assert out is not None
    del out.attrs["xmlns:u"]
    assert 'u:use-attribute-sets="s"' in transform(sheet, turbohtml.parse_xml("<r/>"))


def test_transform_attribute_set_own_attribute_overrides_set() -> None:
    body = (
        '<xsl:attribute-set name="s"><xsl:attribute name="a">1</xsl:attribute></xsl:attribute-set>'
        '<xsl:template match="/"><out a="2" xsl:use-attribute-sets="s"/></xsl:template>'
    )
    assert _collapse(_run("<r/>", body, method="xml")) == '<out a="2"/>'


def test_transform_attribute_set_chains_via_use_attribute_sets() -> None:
    body = (
        '<xsl:attribute-set name="base"><xsl:attribute name="a">1</xsl:attribute></xsl:attribute-set>'
        '<xsl:attribute-set name="s" use-attribute-sets="base">'
        '<xsl:attribute name="b">2</xsl:attribute></xsl:attribute-set>'
        '<xsl:template match="/"><out xsl:use-attribute-sets="s"/></xsl:template>'
    )
    assert _collapse(_run("<r/>", body, method="xml")) == '<out a="1" b="2"/>'


def test_transform_attribute_set_on_xsl_element() -> None:
    body = (
        '<xsl:attribute-set name="s"><xsl:attribute name="a">1</xsl:attribute></xsl:attribute-set>'
        '<xsl:template match="/"><xsl:element name="out" use-attribute-sets="s"/></xsl:template>'
    )
    assert _collapse(_run("<r/>", body, method="xml")) == '<out a="1"/>'


def test_transform_attribute_set_on_xsl_copy() -> None:
    body = (
        '<xsl:attribute-set name="s"><xsl:attribute name="a">1</xsl:attribute></xsl:attribute-set>'
        '<xsl:template match="/"><xsl:apply-templates select="r"/></xsl:template>'
        '<xsl:template match="r"><xsl:copy use-attribute-sets="s"/></xsl:template>'
    )
    assert _collapse(_run("<r/>", body, method="xml")) == '<r a="1"/>'


def test_transform_attribute_set_requires_name() -> None:
    body = (
        '<xsl:attribute-set><xsl:attribute name="a">1</xsl:attribute></xsl:attribute-set>'
        '<xsl:template match="/">x</xsl:template>'
    )
    with pytest.raises(ValueError, match="attribute-set requires a name"):
        _run("<r/>", body)


def test_transform_namespace_alias_remaps_result_namespace() -> None:
    body = (
        '<xsl:namespace-alias stylesheet-prefix="a" result-prefix="xsl"/>'
        '<xsl:template match="/"><a:out/></xsl:template>'
    )
    declare = 'xmlns:a="urn:alias"'
    result = _collapse(transform(_sheet(body, method="xml", declare=declare), turbohtml.parse_xml("<r/>")))
    assert result == '<a:out xmlns:a="http://www.w3.org/1999/XSL/Transform"/>'


def test_transform_namespace_alias_requires_both_prefixes() -> None:
    body = '<xsl:namespace-alias stylesheet-prefix="a"/><xsl:template match="/">x</xsl:template>'
    with pytest.raises(ValueError, match="stylesheet-prefix and result-prefix"):
        transform(_sheet(body, declare='xmlns:a="urn:a"'), turbohtml.parse_xml("<r/>"))


def test_transform_namespace_alias_undeclared_result_prefix() -> None:
    body = '<xsl:namespace-alias stylesheet-prefix="a" result-prefix="zz"/><xsl:template match="/">x</xsl:template>'
    with pytest.raises(ValueError, match="result-prefix is not a declared namespace"):
        transform(_sheet(body, declare='xmlns:a="urn:a"'), turbohtml.parse_xml("<r/>"))


def test_transform_attribute_with_namespace_generates_prefix() -> None:
    body = (
        '<xsl:template match="/"><out>'
        '<xsl:attribute name="thing" namespace="urn:z">v</xsl:attribute></out></xsl:template>'
    )
    assert _collapse(_run("<r/>", body, method="xml")) == '<out xmlns:ns_1="urn:z" ns_1:thing="v"/>'


def test_transform_attribute_with_namespace_reuses_existing_prefix() -> None:
    body = (
        '<xsl:template match="/"><out xmlns:z="urn:z">'
        '<xsl:attribute name="thing" namespace="urn:z">v</xsl:attribute></out></xsl:template>'
    )
    assert _collapse(_run("<r/>", body, method="xml")) == '<out xmlns:z="urn:z" z:thing="v"/>'


def test_transform_attribute_empty_namespace_is_ignored() -> None:
    body = '<xsl:template match="/"><out><xsl:attribute name="a" namespace="">v</xsl:attribute></out></xsl:template>'
    assert _collapse(_run("<r/>", body, method="xml")) == '<out a="v"/>'


def test_transform_number_multiple_levels() -> None:
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="//sec"/></xsl:template>'
        '<xsl:template match="sec">[<xsl:number level="multiple" count="sec" format="1.1"/>]</xsl:template>'
    )
    result = _run("<doc><sec><sec/><sec/></sec><sec/></doc>", body)
    assert result == "[1][1.1][1.2][2]"


def test_transform_number_level_any() -> None:
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="//x"/></xsl:template>'
        '<xsl:template match="x">[<xsl:number level="any" count="x"/>]</xsl:template>'
    )
    assert _run("<doc><g><x/><x/></g><x/></doc>", body) == "[1][2][3]"


def test_transform_number_level_any_from_resets() -> None:
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="//x"/></xsl:template>'
        '<xsl:template match="x">[<xsl:number level="any" from="g" count="x"/>]</xsl:template>'
    )
    assert _run("<doc><x/><g><x/><x/></g></doc>", body) == "[1][1][2]"


def test_transform_number_single_with_count_and_from() -> None:
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="//x"/></xsl:template>'
        '<xsl:template match="x">[<xsl:number level="single" from="g" count="x"/>]</xsl:template>'
    )
    assert _run("<doc><g><x/><x/></g></doc>", body) == "[1][2]"


def test_transform_number_single_from_excludes_when_no_match() -> None:
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="//x"/></xsl:template>'
        '<xsl:template match="x">[<xsl:number level="single" from="none" count="y"/>]</xsl:template>'
    )
    assert _run("<doc><x/></doc>", body) == "[]"


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        pytest.param("3", "1.234.567", id="size-3"),
        pytest.param("1", "1.2.3.4.5.6.7", id="size-1"),
        pytest.param("0", "1234567", id="size-0-disables"),
        pytest.param("-1", "1234567", id="negative-disables"),
        pytest.param("99", "1234567", id="too-large-no-split"),
        pytest.param("bad", "1234567", id="non-numeric-disables"),
    ],
)
def test_transform_number_grouping(size: str, expected: str) -> None:
    body = (
        f'<xsl:template match="/"><xsl:number value="1234567" grouping-separator="." '
        f'grouping-size="{size}"/></xsl:template>'
    )
    assert _run("<r/>", body) == expected


def test_transform_number_grouping_needs_separator() -> None:
    body = '<xsl:template match="/"><xsl:number value="1234" grouping-size="3"/></xsl:template>'
    assert _run("<r/>", body) == "1234"


def test_transform_number_multi_token_format_reuses_last() -> None:
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="//s"/></xsl:template>'
        '<xsl:template match="s">[<xsl:number level="multiple" count="s" format="A-1"/>]</xsl:template>'
    )
    assert _run("<d><s><s><s/></s></s></d>", body) == "[A][A-1][A-1-1]"


def test_transform_cdata_section_elements_wraps_text() -> None:
    body = '<xsl:output cdata-section-elements="d"/><xsl:template match="/"><d>&lt;x></d></xsl:template>'
    assert _canon(transform(_sheet(body, method=""), turbohtml.parse_xml("<r/>"))) == "<d><![CDATA[<x>]]></d>"


def test_transform_cdata_from_literal_cdata_in_stylesheet() -> None:
    body = '<xsl:output cdata-section-elements="d"/><xsl:template match="/"><d><![CDATA[<x>]]></d></xsl:template>'
    assert _canon(transform(_sheet(body, method=""), turbohtml.parse_xml("<r/>"))) == "<d><![CDATA[<x>]]></d>"


def test_transform_html_method_auto_selected_for_html_root() -> None:
    body = '<xsl:template match="/"><html><head><title>t</title></head><body>x</body></html></xsl:template>'
    result = _collapse(transform(_sheet(body, method=""), turbohtml.parse_xml("<r/>")))
    assert not result.startswith("<?xml")
    assert '<meta charset="UTF-8">' in result


def test_transform_html_auto_select_skipped_for_non_html_root() -> None:
    body = '<xsl:template match="/"><doc>x</doc></xsl:template>'
    assert transform(_sheet(body, method=""), turbohtml.parse_xml("<r/>")).startswith("<?xml")


def test_transform_html_auto_select_skipped_when_namespaced() -> None:
    body = '<xsl:template match="/"><html xmlns="urn:x">x</html></xsl:template>'
    assert transform(_sheet(body, method=""), turbohtml.parse_xml("<r/>")).startswith("<?xml")


def test_transform_html_auto_select_skipped_after_significant_text() -> None:
    body = '<xsl:template match="/">lead<html>x</html></xsl:template>'
    assert transform(_sheet(body, method=""), turbohtml.parse_xml("<r/>")).startswith("<?xml")


def test_transform_html_auto_select_ignores_leading_whitespace() -> None:
    body = '<xsl:template match="/"><xsl:text> </xsl:text><html><body>x</body></html></xsl:template>'
    assert not transform(_sheet(body, method=""), turbohtml.parse_xml("<r/>")).startswith("<?xml")


def test_transform_fallback_runs_for_extension_element() -> None:
    body = '<xsl:template match="/"><e:go><xsl:fallback>done</xsl:fallback></e:go></xsl:template>'
    declare = 'xmlns:e="urn:ext" extension-element-prefixes="e"'
    assert transform(_sheet(body, declare=declare), turbohtml.parse_xml("<r/>")) == "done"


def test_transform_default_namespace_fallback_runs_for_extension_element() -> None:
    sheet = turbohtml.parse_xml(
        '<stylesheet version="1.0" xmlns="http://www.w3.org/1999/XSL/Transform" xmlns:e="urn:ext" '
        'extension-element-prefixes="e"><output method="text"/><template match="/">'
        "<e:go><fallback>done</fallback></e:go></template></stylesheet>"
    )
    assert transform(sheet, turbohtml.parse_xml("<r/>")) == "done"


def test_transform_default_namespace_does_not_skip_literal_param() -> None:
    sheet = turbohtml.parse_xml(
        '<stylesheet version="1.0" xmlns="http://www.w3.org/1999/XSL/Transform"><output method="text"/>'
        '<template match="/"><param xmlns="urn:literal">kept</param></template></stylesheet>'
    )
    assert transform(sheet, turbohtml.parse_xml("<r/>")) == "kept"


def test_transform_default_namespace_stops_after_error() -> None:
    sheet = turbohtml.parse_xml(
        '<stylesheet version="1.0" xmlns="http://www.w3.org/1999/XSL/Transform"><output method="text"/>'
        '<template match="/"><value-of select="$missing"/><text>unreachable</text></template></stylesheet>'
    )
    with pytest.raises(ValueError, match="unbound"):
        transform(sheet, turbohtml.parse_xml("<r/>"))


def test_transform_extension_element_without_fallback_yields_nothing() -> None:
    body = '<xsl:template match="/">[<e:go/>]</xsl:template>'
    declare = 'xmlns:e="urn:ext" extension-element-prefixes="e"'
    assert transform(_sheet(body, declare=declare), turbohtml.parse_xml("<r/>")) == "[]"


def test_transform_simplified_stylesheet() -> None:
    sheet = turbohtml.parse_xml(
        '<out xsl:version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<v><xsl:value-of select="r/n"/></v></out>'
    )
    assert _canon(transform(sheet, turbohtml.parse_xml("<r><n>7</n></r>"))) == "<out><v>7</v></out>"


def test_transform_simplified_stylesheet_resolves_root_namespace() -> None:
    sheet = turbohtml.parse_xml(
        '<l:out a="1" xmlns:l="urn:literal" xmlns:t="http://www.w3.org/1999/XSL/Transform" '
        'xmlns:u="http://www.w3.org/1999/XSL/Transform" t:version="1.0">'
        "<t:value-of select=\"'ok'\"/></l:out>"
    )
    assert ">ok</l:out>" in transform(sheet, turbohtml.parse_xml("<r/>"))


def test_transform_default_namespace_ignores_similar_root_attribute() -> None:
    sheet = turbohtml.parse_xml(
        '<stylesheet aaaaa="x" version="1.0" xmlns="http://www.w3.org/1999/XSL/Transform">'
        '<output method="text"/><template match="/">ok</template></stylesheet>'
    )
    assert transform(sheet, turbohtml.parse_xml("<r/>")) == "ok"


def test_transform_import_loads_external_templates(tmp_path: Path) -> None:
    (tmp_path / "base.xsl").write_text(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:template match="a">[<xsl:value-of select="."/>]</xsl:template></xsl:stylesheet>',
        encoding="utf-8",
    )
    main = tmp_path / "main.xsl"
    main.write_text(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:import href="base.xsl"/>'
        '<xsl:template match="/"><xsl:apply-templates select="r/a"/></xsl:template></xsl:stylesheet>',
        encoding="utf-8",
    )
    sheet = turbohtml.parse_xml(main.read_text(encoding="utf-8"))
    result = transform(sheet, turbohtml.parse_xml("<r><a>x</a></r>"), base_url=str(main), import_root=tmp_path)
    assert _canon(result) == "[x]"


def test_transform_import_can_be_disabled(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="xsl:import is disabled"):
        Transform(_import_sheet(), base_url=str(tmp_path / "main.xsl"), allow_imports=False)


def test_transform_import_disabled_allows_self_contained_stylesheet() -> None:
    result = Transform(_sheet('<xsl:template match="/">ok</xsl:template>'), allow_imports=False)(
        turbohtml.parse_xml("<r/>")
    )
    assert result == "ok"


@pytest.mark.parametrize(
    "href_kind",
    [
        pytest.param("parent", id="parent traversal"),
        pytest.param("absolute", id="absolute path"),
        pytest.param("file_url", id="file URL"),
    ],
)
def test_transform_import_root_rejects_path_escape(tmp_path: Path, href_kind: str) -> None:
    root = tmp_path / "styles"
    root.mkdir()
    outside = tmp_path / "outside.xsl"
    outside.write_text('<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform"/>', encoding="utf-8")
    href = {"parent": "../outside.xsl", "absolute": str(outside), "file_url": outside.as_uri()}[href_kind]
    with pytest.raises(ValueError, match="path escapes import_root"):
        transform(
            _import_sheet(href),
            turbohtml.parse_xml("<r/>"),
            base_url=str(root / "main.xsl"),
            import_root=root,
        )


def test_transform_import_root_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "styles"
    root.mkdir()
    outside = tmp_path / "outside.xsl"
    outside.write_text('<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform"/>', encoding="utf-8")
    link = root / "linked.xsl"
    try:
        link.symlink_to(outside)
    except OSError as error:  # pragma: no cover - Windows may deny symlink creation
        pytest.skip(f"symlinks unavailable: {error}")
    with pytest.raises(ValueError, match="path escapes import_root"):
        transform(
            _import_sheet("linked.xsl"), turbohtml.parse_xml("<r/>"), base_url=str(root / "main.xsl"), import_root=root
        )


@pytest.mark.parametrize(
    "replacement",
    [
        pytest.param(
            "file", marks=pytest.mark.skipif(os.name == "nt", reason="POSIX parent component errors"), id="regular file"
        ),
        pytest.param("symlink", id="directory symlink"),
    ],
)
def test_transform_import_root_blocks_parent_swap(
    import_swap_paths: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, replacement: str
) -> None:
    root, inside, outside = import_swap_paths
    path_type = type(root)
    is_relative_to = path_type.is_relative_to

    def check_root(path: Path, other: Path) -> bool:
        monkeypatch.undo()
        allowed = is_relative_to(path, other)
        inside.rename(root / "saved")
        if replacement == "file":  # pragma: no cover - Windows skips parent-file swaps
            inside.write_text("outside", encoding="utf-8")
        else:
            try:
                inside.symlink_to(outside, target_is_directory=True)
            except OSError as error:  # pragma: no cover - Windows may deny symlink creation
                pytest.skip(f"symlinks unavailable: {error}")
        return allowed

    monkeypatch.setattr(path_type, "is_relative_to", check_root)
    with pytest.raises(ValueError, match="xsl:import path escapes import_root"):
        Transform(_import_sheet("inside/base.xsl"), base_url=str(root / "main.xsl"), import_root=root)


def test_transform_import_root_blocks_file_swap(
    import_swap_paths: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, inside, outside = import_swap_paths
    path_type = type(root)
    is_relative_to = path_type.is_relative_to

    def check_root(path: Path, other: Path) -> bool:
        monkeypatch.undo()
        allowed = is_relative_to(path, other)
        (inside / "base.xsl").rename(inside / "saved.xsl")
        try:
            (inside / "base.xsl").symlink_to(outside / "base.xsl")
        except OSError as error:  # pragma: no cover - Windows may deny symlink creation
            pytest.skip(f"symlinks unavailable: {error}")
        return allowed

    monkeypatch.setattr(path_type, "is_relative_to", check_root)
    with pytest.raises(ValueError, match="xsl:import path escapes import_root"):
        Transform(_import_sheet("inside/base.xsl"), base_url=str(root / "main.xsl"), import_root=root)


@pytest.fixture
def import_swap_paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "styles"
    inside = root / "inside"
    outside = tmp_path / "outside"
    inside.mkdir(parents=True)
    outside.mkdir()
    for folder, value in ((inside, "inside"), (outside, "outside")):
        (folder / "base.xsl").write_text(
            '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
            f'<xsl:template match="/">{value}</xsl:template></xsl:stylesheet>',
            encoding="utf-8",
        )
    return root, inside, outside


def test_transform_import_root_reads_large_file(tmp_path: Path) -> None:
    (tmp_path / "base.xsl").write_text(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:template match="/">ok</xsl:template></xsl:stylesheet>' + " " * 65536,
        encoding="utf-8",
    )
    result = Transform(_import_sheet(), base_url=str(tmp_path / "main.xsl"), import_root=tmp_path)(
        turbohtml.parse_xml("<r/>")
    )
    assert _canon(result) == "ok"


def test_transform_import_root_rejects_directory(tmp_path: Path) -> None:
    (tmp_path / "base.xsl").mkdir()
    with pytest.raises(OSError, match=r"base\.xsl"):
        Transform(_import_sheet(), base_url=str(tmp_path / "main.xsl"), import_root=tmp_path)


def test_transform_import_root_rejects_invalid_utf8(tmp_path: Path) -> None:
    (tmp_path / "base.xsl").write_bytes(b"\xff")
    with pytest.raises(UnicodeDecodeError):
        Transform(_import_sheet(), base_url=str(tmp_path / "main.xsl"), import_root=tmp_path)


@pytest.mark.skipif(os.name == "nt", reason="POSIX filesystem root")
def test_transform_import_accepts_filesystem_root(tmp_path: Path) -> None:  # pragma: no cover - Windows path semantics
    (tmp_path / "base.xsl").write_text(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:template match="/">ok</xsl:template></xsl:stylesheet>',
        encoding="utf-8",
    )
    result = Transform(_import_sheet(), base_url=str(tmp_path / "main.xsl"), import_root=Path("/"))(
        turbohtml.parse_xml("<r/>")
    )
    assert _canon(result) == "ok"


@pytest.mark.parametrize(
    ("href", "content", "error", "match"),
    [
        pytest.param("x", None, FileNotFoundError, r"[/\\]x", id="missing file"),
        pytest.param("imported.xsl", "<broken>", turbohtml.HTMLParseError, "xml-premature-eof", id="malformed XML"),
    ],
)
def test_transform_import_reports_file_error(
    tmp_path: Path, href: str, content: str | None, error: type[Exception], match: str
) -> None:
    if content is not None:
        (tmp_path / href).write_text(content, encoding="utf-8")
    with pytest.raises(error, match=match):
        Transform(_import_sheet(href), base_url=str(tmp_path / "main.xsl"), import_root=tmp_path)


def test_transform_import_rejects_malformed_url() -> None:
    with pytest.raises(ValueError, match="Invalid IPv6 URL"):
        Transform(_import_sheet(), base_url="http://[")


def test_transform_import_rejects_invalid_root(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match=r"os\.PathLike"):
        Transform(_import_sheet(), base_url=str(tmp_path / "main.xsl"), import_root=cast("str | Path", object()))


def test_transform_import_rejects_missing_root(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="missing"):
        Transform(_import_sheet(), base_url=str(tmp_path / "main.xsl"), import_root=tmp_path / "missing")


@pytest.mark.parametrize(
    ("href", "relative"),
    [
        pytest.param("C:/base.xsl", ("C:", "base.xsl"), id="slash"),
        pytest.param(r"C:\base.xsl", (r"C:\base.xsl",), id="backslash"),
    ],
)
@pytest.mark.skipif(os.name == "nt", reason="Windows drive paths are native on Windows")
def test_transform_import_accepts_windows_drive_path_on_posix(
    tmp_path: Path, href: str, relative: tuple[str, ...]
) -> None:  # pragma: no cover - Windows treats drive paths as native
    imported = tmp_path.joinpath(*relative)
    imported.parent.mkdir(exist_ok=True)
    imported.write_text(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:template match="/">ok</xsl:template></xsl:stylesheet>',
        encoding="utf-8",
    )
    result = Transform(_import_sheet(href), base_url=str(tmp_path / "main.xsl"), import_root=tmp_path)(
        turbohtml.parse_xml("<r/>")
    )
    assert _canon(result) == "ok"


def test_transform_import_rejects_windows_drive_relative_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="href must be a local path or file URL"):
        Transform(_import_sheet("C:base.xsl"), base_url=str(tmp_path / "main.xsl"))


def _import_sheet(href: str = "base.xsl") -> turbohtml.Document:
    return turbohtml.parse_xml(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        f'<xsl:import href="{href}"/></xsl:stylesheet>'
    )


def test_transform_import_ignores_foreign_same_length_prefix() -> None:
    sheet = turbohtml.parse_xml(
        '<xsl:stylesheet version="1.0" xmlns:bad="urn:bad" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl-import href="missing.xsl"/><bad:import href="missing.xsl"/>'
        '<xsl:template match="/">ok</xsl:template></xsl:stylesheet>'
    )
    assert _canon(transform(sheet, turbohtml.parse_xml("<r/>"))) == "ok"


def test_transform_import_detects_default_prefix_without_root_attributes() -> None:
    sheet = turbohtml.parse_xml(
        '<root><xsl:import xmlns:xsl="http://www.w3.org/1999/XSL/Transform" href="missing.xsl"/></root>'
    )
    with pytest.raises(ValueError, match="needs a base_url"):
        Transform(sheet)


def test_transform_rejects_non_node_stylesheet() -> None:
    with pytest.raises(TypeError):
        Transform(cast("turbohtml.Node", "not a node"))


def test_transform_import_rejects_self_cycle(tmp_path: Path) -> None:
    main = tmp_path / "main.xsl"
    main.write_text(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:import href="main.xsl"/></xsl:stylesheet>',
        encoding="utf-8",
    )
    path = main.resolve()
    with pytest.raises(ValueError, match=rf"^{re.escape(f'circular xsl:import: {path} -> {path}')}$"):
        transform(
            turbohtml.parse_xml(main.read_text(encoding="utf-8")), turbohtml.parse_xml("<r/>"), base_url=str(main)
        )


def test_transform_import_rejects_indirect_cycle(tmp_path: Path) -> None:
    main = tmp_path / "main.xsl"
    imported = tmp_path / "imported.xsl"
    namespace = 'version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform"'
    main.write_text(f'<xsl:stylesheet {namespace}><xsl:import href="imported.xsl"/></xsl:stylesheet>', encoding="utf-8")
    imported.write_text(f'<xsl:stylesheet {namespace}><xsl:import href="main.xsl"/></xsl:stylesheet>', encoding="utf-8")
    chain = " -> ".join(str(path.resolve()) for path in (main, imported, main))
    with pytest.raises(ValueError, match=rf"^{re.escape(f'circular xsl:import: {chain}')}$"):
        transform(
            turbohtml.parse_xml(main.read_text(encoding="utf-8")), turbohtml.parse_xml("<r/>"), base_url=str(main)
        )


def test_transform_import_allows_repeated_completed_path(tmp_path: Path) -> None:
    imported = tmp_path / "imported.xsl"
    imported.write_text(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:template match="a">ok</xsl:template></xsl:stylesheet>',
        encoding="utf-8",
    )
    main = tmp_path / "main.xsl"
    main.write_text(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:import href="imported.xsl"/><xsl:import href="imported.xsl"/>'
        '<xsl:template match="/"><xsl:apply-templates select="r/a"/></xsl:template></xsl:stylesheet>',
        encoding="utf-8",
    )
    result = transform(
        turbohtml.parse_xml(main.read_text(encoding="utf-8")), turbohtml.parse_xml("<r><a/></r>"), base_url=str(main)
    )
    assert _canon(result) == "ok"


def test_transform_default_namespace_import(tmp_path: Path) -> None:
    namespace = 'xmlns="http://www.w3.org/1999/XSL/Transform"'
    (tmp_path / "base.xsl").write_text(
        f'<stylesheet version="1.0" {namespace}><template match="a">[<value-of select="."/>]</template></stylesheet>',
        encoding="utf-8",
    )
    main = tmp_path / "main.xsl"
    main.write_text(
        f'<stylesheet version="1.0" {namespace}><import href="base.xsl"/>'
        '<template match="/"><apply-templates select="r/a"/></template></stylesheet>',
        encoding="utf-8",
    )
    result = transform(
        turbohtml.parse_xml(main.read_text(encoding="utf-8")),
        turbohtml.parse_xml("<r><a>x</a></r>"),
        base_url=str(main),
    )
    assert _canon(result) == "[x]"


def test_transform_import_resolves_its_own_xslt_prefix(tmp_path: Path) -> None:
    (tmp_path / "base.xsl").write_text(
        '<t:stylesheet version="1.0" xmlns:t="http://www.w3.org/1999/XSL/Transform">'
        '<t:template match="a">[<t:value-of select="."/>]</t:template></t:stylesheet>',
        encoding="utf-8",
    )
    main = tmp_path / "main.xsl"
    main.write_text(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:import href="base.xsl"/><xsl:template match="/">'
        '<xsl:apply-templates select="r/a"/></xsl:template></xsl:stylesheet>',
        encoding="utf-8",
    )
    sheet = turbohtml.parse_xml(main.read_text(encoding="utf-8"))
    assert _canon(transform(sheet, turbohtml.parse_xml("<r><a>x</a></r>"), base_url=str(main))) == "[x]"


def test_transform_import_resolves_file_url_base(tmp_path: Path) -> None:
    (tmp_path / "base.xsl").write_text(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:template match="a">[<xsl:value-of select="."/>]</xsl:template></xsl:stylesheet>',
        encoding="utf-8",
    )
    main = tmp_path / "main.xsl"
    main.write_text(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:import href="base.xsl"/>'
        '<xsl:template match="/"><xsl:apply-templates select="r/a"/></xsl:template></xsl:stylesheet>',
        encoding="utf-8",
    )
    sheet = turbohtml.parse_xml(main.read_text(encoding="utf-8"))
    result = transform(sheet, turbohtml.parse_xml("<r><a>x</a></r>"), base_url=main.as_uri())
    assert _canon(result) == "[x]"


@pytest.mark.parametrize("authority", ["", "localhost"], ids=["empty host", "localhost"])
def test_transform_import_resolves_file_url_href(tmp_path: Path, authority: str) -> None:
    base = tmp_path / "base.xsl"
    base.write_text(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:template match="a">[<xsl:value-of select="."/>]</xsl:template></xsl:stylesheet>',
        encoding="utf-8",
    )
    main = tmp_path / "main.xsl"
    main.write_text(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        f'<xsl:import href="{base.as_uri().replace("file://", "file://" + authority)}"/>'
        '<xsl:template match="/"><xsl:apply-templates select="r/a"/></xsl:template></xsl:stylesheet>',
        encoding="utf-8",
    )
    sheet = turbohtml.parse_xml(main.read_text(encoding="utf-8"))
    result = transform(sheet, turbohtml.parse_xml("<r><a>x</a></r>"), base_url=str(main))
    assert _canon(result) == "[x]"


def test_transform_import_rejects_remote_base_url() -> None:
    sheet = turbohtml.parse_xml(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:import href="base.xsl"/></xsl:stylesheet>'
    )
    with pytest.raises(ValueError, match="base_url must be a local path or file URL"):
        transform(sheet, turbohtml.parse_xml("<r/>"), base_url="https://example.com/main.xsl")


@pytest.mark.parametrize("href", ["https://example.com/base.xsl", "//example.com/base.xsl"], ids=["URL", "authority"])
def test_transform_import_rejects_remote_href(tmp_path: Path, href: str) -> None:
    main = tmp_path / "main.xsl"
    main.write_text(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        f'<xsl:import href="{href}"/></xsl:stylesheet>',
        encoding="utf-8",
    )
    sheet = turbohtml.parse_xml(main.read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="href must be a local path or file URL"):
        transform(sheet, turbohtml.parse_xml("<r/>"), base_url=str(main))


def test_transform_import_rejects_remote_file_url() -> None:
    sheet = turbohtml.parse_xml(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:import href="base.xsl"/></xsl:stylesheet>'
    )
    with pytest.raises(ValueError, match="base_url file URL must point to a local path"):
        transform(sheet, turbohtml.parse_xml("<r/>"), base_url="file://example.com/main.xsl")


def test_transform_import_precedence_importer_wins(tmp_path: Path) -> None:
    (tmp_path / "base.xsl").write_text(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:template match="a">base</xsl:template></xsl:stylesheet>',
        encoding="utf-8",
    )
    main = tmp_path / "main.xsl"
    main.write_text(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:import href="base.xsl"/>'
        '<xsl:template match="/"><xsl:apply-templates select="r/a"/></xsl:template>'
        '<xsl:template match="a">main</xsl:template></xsl:stylesheet>',
        encoding="utf-8",
    )
    sheet = turbohtml.parse_xml(main.read_text(encoding="utf-8"))
    assert _canon(transform(sheet, turbohtml.parse_xml("<r><a/></r>"), base_url=str(main))) == "main"


def test_transform_import_nested(tmp_path: Path) -> None:
    (tmp_path / "leaf.xsl").write_text(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:template match="a">leaf</xsl:template></xsl:stylesheet>',
        encoding="utf-8",
    )
    (tmp_path / "mid.xsl").write_text(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:import href="leaf.xsl"/></xsl:stylesheet>',
        encoding="utf-8",
    )
    main = tmp_path / "main.xsl"
    main.write_text(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:import href="mid.xsl"/>'
        '<xsl:template match="/"><xsl:apply-templates select="r/a"/></xsl:template></xsl:stylesheet>',
        encoding="utf-8",
    )
    sheet = turbohtml.parse_xml(main.read_text(encoding="utf-8"))
    result = transform(sheet, turbohtml.parse_xml("<r><a/></r>"), base_url=str(main), import_root=tmp_path)
    assert _canon(result) == "leaf"


def test_transform_import_without_base_url_errors() -> None:
    sheet = turbohtml.parse_xml(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:import href="base.xsl"/></xsl:stylesheet>'
    )
    with pytest.raises(ValueError, match="needs a base_url"):
        transform(sheet, turbohtml.parse_xml("<r/>"))


def test_transform_import_missing_href_errors(tmp_path: Path) -> None:
    main = tmp_path / "main.xsl"
    main.write_text(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform"><xsl:import/></xsl:stylesheet>',
        encoding="utf-8",
    )
    sheet = turbohtml.parse_xml(main.read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="href attribute"):
        transform(sheet, turbohtml.parse_xml("<r/>"), base_url=str(main))


def test_transform_nested_import_missing_href_errors(tmp_path: Path) -> None:
    (tmp_path / "base.xsl").write_text(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform"><xsl:import/></xsl:stylesheet>',
        encoding="utf-8",
    )
    main = tmp_path / "main.xsl"
    main.write_text(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:import href="base.xsl"/></xsl:stylesheet>',
        encoding="utf-8",
    )
    sheet = turbohtml.parse_xml(main.read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="requires an href"):
        transform(sheet, turbohtml.parse_xml("<r/>"), base_url=str(main))


def test_transform_prebuilt_with_imports_reused(tmp_path: Path) -> None:
    (tmp_path / "base.xsl").write_text(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:template match="a">[<xsl:value-of select="."/>]</xsl:template></xsl:stylesheet>',
        encoding="utf-8",
    )
    main = tmp_path / "main.xsl"
    main.write_text(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:import href="base.xsl"/>'
        '<xsl:template match="/"><xsl:apply-templates select="r/a"/></xsl:template></xsl:stylesheet>',
        encoding="utf-8",
    )
    convert = Transform(turbohtml.parse_xml(main.read_text(encoding="utf-8")), base_url=str(main))
    assert _canon(convert(turbohtml.parse_xml("<r><a>1</a></r>"))) == "[1]"
    assert _canon(convert(turbohtml.parse_xml("<r><a>2</a></r>"))) == "[2]"


def test_transform_cdata_multiple_named_elements_and_non_match() -> None:
    body = (
        '<xsl:output cdata-section-elements="a b"/><xsl:template match="/"><r><a>&lt;x></a><c>y</c></r></xsl:template>'
    )
    result = _canon(transform(_sheet(body, method=""), turbohtml.parse_xml("<r/>")))
    assert result == "<r><a><![CDATA[<x>]]></a><c>y</c></r>"


def test_transform_attribute_set_two_names_with_trailing_space() -> None:
    body = (
        '<xsl:attribute-set name="s"><xsl:attribute name="a">1</xsl:attribute></xsl:attribute-set>'
        '<xsl:attribute-set name="t"><xsl:attribute name="b">2</xsl:attribute></xsl:attribute-set>'
        '<xsl:template match="/"><out xsl:use-attribute-sets="s t "/></xsl:template>'
    )
    assert _collapse(_run("<r/>", body, method="xml")) == '<out a="1" b="2"/>'


_POISON = '<xsl:attribute-set name="bad"><xsl:attribute name="{$undef}">v</xsl:attribute></xsl:attribute-set>'


def test_transform_attribute_set_error_propagates_through_literal() -> None:
    body = f'{_POISON}<xsl:template match="/"><out xsl:use-attribute-sets="bad"/></xsl:template>'
    with pytest.raises(ValueError, match="unbound"):
        _run("<r/>", body, method="xml")


def test_transform_attribute_set_error_propagates_through_element() -> None:
    body = f'{_POISON}<xsl:template match="/"><xsl:element name="out" use-attribute-sets="bad"/></xsl:template>'
    with pytest.raises(ValueError, match="unbound"):
        _run("<r/>", body, method="xml")


def test_transform_attribute_set_error_propagates_through_copy() -> None:
    body = (
        f"{_POISON}"
        '<xsl:template match="/"><xsl:apply-templates select="r"/></xsl:template>'
        '<xsl:template match="r"><xsl:copy use-attribute-sets="bad"/></xsl:template>'
    )
    with pytest.raises(ValueError, match="unbound"):
        _run("<r/>", body, method="xml")


def test_transform_attribute_set_error_propagates_through_chain() -> None:
    body = (
        f"{_POISON}"
        '<xsl:attribute-set name="s" use-attribute-sets="bad"/>'
        '<xsl:template match="/"><out xsl:use-attribute-sets="s"/></xsl:template>'
    )
    with pytest.raises(ValueError, match="unbound"):
        _run("<r/>", body, method="xml")


def test_transform_attribute_with_prefixed_name_and_namespace() -> None:
    body = (
        '<xsl:template match="/"><out>'
        '<xsl:attribute name="p:thing" namespace="urn:z">v</xsl:attribute></out></xsl:template>'
    )
    assert _collapse(_run("<r/>", body, method="xml")) == '<out xmlns:ns_1="urn:z" ns_1:thing="v"/>'


def test_transform_attribute_namespace_bad_avt_errors() -> None:
    body = (
        '<xsl:template match="/"><out><xsl:attribute name="a" namespace="{$undef}">v</xsl:attribute>'
        "</out></xsl:template>"
    )
    with pytest.raises(ValueError, match="unbound"):
        _run("<r/>", body, method="xml")


def test_transform_number_grouping_size_sign_only() -> None:
    body = '<xsl:template match="/"><xsl:number value="1234" grouping-separator="." grouping-size="+"/></xsl:template>'
    assert _run("<r/>", body) == "1234"


def test_transform_number_count_too_many_alternatives() -> None:
    pattern = "|".join(f"n{index}" for index in range(80))
    body = f'<xsl:template match="/"><xsl:number level="any" count="{pattern}"/></xsl:template>'
    with pytest.raises(ValueError, match="too many alternatives"):
        _run("<r/>", body)


def test_transform_number_bad_count_pattern_errors() -> None:
    body = '<xsl:template match="/"><xsl:number level="any" count="[[["/></xsl:template>'
    with pytest.raises(ValueError, match="pattern"):
        _run("<r/>", body)


def test_transform_number_bad_from_pattern_errors() -> None:
    body = '<xsl:template match="/"><xsl:number level="any" count="x" from="[[["/></xsl:template>'
    with pytest.raises(ValueError, match="pattern"):
        _run("<r/>", body)


def test_transform_number_multiple_from_bounds_the_ancestor_walk() -> None:
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="//x"/></xsl:template>'
        '<xsl:template match="x">[<xsl:number level="multiple" from="g" count="x"/>]</xsl:template>'
    )
    assert _run("<doc><g><x/><x/></g></doc>", body) == "[1][2]"


def test_transform_number_single_from_before_count_yields_empty() -> None:
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="//x"/></xsl:template>'
        '<xsl:template match="x">[<xsl:number level="single" from="doc" count="zzz"/>]</xsl:template>'
    )
    assert _run("<doc><x/></doc>", body) == "[]"


def test_transform_namespace_alias_default_result_prefix() -> None:
    body = (
        '<xsl:namespace-alias stylesheet-prefix="a" result-prefix="#default"/>'
        '<xsl:template match="/"><a:out/></xsl:template>'
    )
    declare = 'xmlns:a="urn:alias" xmlns="urn:default"'
    result = _canon(transform(_sheet(body, method="xml", declare=declare), turbohtml.parse_xml("<r/>")))
    assert 'xmlns:a="urn:default"' in result


def test_transform_strip_space_multi_name_list_with_trailing_space() -> None:
    body = (
        '<xsl:strip-space elements="p q "/><xsl:template match="/"><xsl:apply-templates select="//q"/></xsl:template>'
    )
    assert _run("<r><q> <b>x</b> </q></r>", body) == "x"


def test_transform_preserve_space_requires_elements_attribute() -> None:
    with pytest.raises(ValueError, match="requires an elements attribute"):
        _run("<r/>", '<xsl:preserve-space/><xsl:template match="/">x</xsl:template>')


def test_transform_html_auto_select_ignores_leading_comment() -> None:
    body = '<xsl:template match="/"><xsl:comment>c</xsl:comment><html><body>x</body></html></xsl:template>'
    assert not transform(_sheet(body, method=""), turbohtml.parse_xml("<r/>")).startswith("<?xml")


def test_transform_fallback_body_with_literal_element() -> None:
    body = '<xsl:template match="/"><e:go><xsl:fallback><doc>ok</doc></xsl:fallback></e:go></xsl:template>'
    declare = 'xmlns:e="urn:ext" extension-element-prefixes="e" exclude-result-prefixes="e"'
    result = _canon(transform(_sheet(body, method="xml", declare=declare), turbohtml.parse_xml("<r/>")))
    assert result == "<doc>ok</doc>"


def test_transform_import_with_malformed_declaration_errors(tmp_path: Path) -> None:
    (tmp_path / "base.xsl").write_text(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        "<xsl:strip-space/></xsl:stylesheet>",
        encoding="utf-8",
    )
    main = tmp_path / "main.xsl"
    main.write_text(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:import href="base.xsl"/>'
        '<xsl:template match="/">x</xsl:template></xsl:stylesheet>',
        encoding="utf-8",
    )
    sheet = turbohtml.parse_xml(main.read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="requires an elements attribute"):
        transform(sheet, turbohtml.parse_xml("<r/>"), base_url=str(main))


def test_transform_attribute_namespace_generates_when_element_has_other_attrs() -> None:
    body = (
        '<xsl:template match="/"><out other="1">'
        '<xsl:attribute name="thing" namespace="urn:z">v</xsl:attribute></out></xsl:template>'
    )
    assert _collapse(_run("<r/>", body, method="xml")) == '<out other="1" xmlns:ns_1="urn:z" ns_1:thing="v"/>'


def test_transform_fallback_body_error_propagates() -> None:
    body = (
        '<xsl:template match="/"><e:go><xsl:fallback><xsl:value-of select="$undef"/></xsl:fallback></e:go>'
        "</xsl:template>"
    )
    declare = 'xmlns:e="urn:ext" extension-element-prefixes="e"'
    with pytest.raises(ValueError, match="unbound"):
        transform(_sheet(body, declare=declare), turbohtml.parse_xml("<r/>"))


def test_transform_html_auto_select_with_attributed_html_element() -> None:
    body = '<xsl:template match="/"><html lang="en"><body>x</body></html></xsl:template>'
    assert not transform(_sheet(body, method=""), turbohtml.parse_xml("<r/>")).startswith("<?xml")


def test_transform_namespace_alias_scans_past_same_length_prefixes() -> None:
    body = (
        '<xsl:namespace-alias stylesheet-prefix="a" result-prefix="q"/>'
        '<xsl:template match="/"><a:out xmlns:p="urn:p"/></xsl:template>'
    )
    declare = 'xmlns:a="urn:a" xmlns:p="urn:p2" xmlns:q="urn:q"'
    result = _canon(transform(_sheet(body, method="xml", declare=declare), turbohtml.parse_xml("<r/>")))
    assert 'xmlns:a="urn:q"' in result


def test_transform_cdata_token_list_with_surrounding_whitespace() -> None:
    body = '<xsl:output cdata-section-elements="  a   b  "/><xsl:template match="/"><b>&lt;y></b></xsl:template>'
    assert _canon(transform(_sheet(body, method=""), turbohtml.parse_xml("<r/>"))) == "<b><![CDATA[<y>]]></b>"


def test_transform_many_attribute_sets_grow_the_table() -> None:
    sets = "".join(
        f'<xsl:attribute-set name="s{index}"><xsl:attribute name="a{index}">{index}</xsl:attribute></xsl:attribute-set>'
        for index in range(12)
    )
    body = f'{sets}<xsl:template match="/"><out xsl:use-attribute-sets="s11"/></xsl:template>'
    assert _collapse(_run("<r/>", body, method="xml")) == '<out a11="11"/>'


def test_transform_many_namespace_aliases_grow_the_table() -> None:
    aliases = "".join(f'<xsl:namespace-alias stylesheet-prefix="p{index}" result-prefix="xsl"/>' for index in range(6))
    decls = " ".join(f'xmlns:p{index}="urn:{index}"' for index in range(6))
    body = f'{aliases}<xsl:template match="/"><p5:out/></xsl:template>'
    result = _canon(transform(_sheet(body, method="xml", declare=decls), turbohtml.parse_xml("<r/>")))
    assert 'xmlns:p5="http://www.w3.org/1999/XSL/Transform"' in result


def test_transform_strip_space_many_tokens_grow_the_table() -> None:
    tokens = " ".join(f"e{index}" for index in range(12))
    body = (
        f'<xsl:strip-space elements="{tokens}"/>'
        '<xsl:template match="/"><xsl:apply-templates select="//e11"/></xsl:template>'
    )
    assert _run("<r><e11> <b>x</b> </e11></r>", body) == "x"


def test_transform_strip_space_many_stripped_nodes_grow_the_list() -> None:
    kids = "".join(f"\n  <k>{index}</k>" for index in range(20))
    body = '<xsl:strip-space elements="r"/><xsl:template match="/"><xsl:apply-templates select="r"/></xsl:template>'
    assert _run(f"<r>{kids}\n</r>", body) == "".join(str(index) for index in range(20))


def test_transform_number_grouping_size_with_leading_digits_token() -> None:
    body = (
        '<xsl:template match="/"><xsl:number value="42" format="001" '
        'grouping-separator="," grouping-size="2"/></xsl:template>'
    )
    assert _run("<r/>", body) == "0,42"


def test_transform_number_alpha_token_uses_trailing_style() -> None:
    body = '<xsl:template match="/"><xsl:number value="3" format="xa"/></xsl:template>'
    assert _run("<r/>", body) == "c"


def test_transform_number_decimal_token_then_nondigit() -> None:
    body = '<xsl:template match="/"><xsl:number value="7" format="1x"/></xsl:template>'
    assert _run("<r/>", body) == "7"


def test_transform_number_grouping_size_negative_digit() -> None:
    body = '<xsl:template match="/"><xsl:number value="12" grouping-separator="." grouping-size="1x"/></xsl:template>'
    assert _run("<r/>", body) == "12"


def test_transform_fallback_ignores_non_fallback_children() -> None:
    body = '<xsl:template match="/"><e:go>text<xsl:fallback>ok</xsl:fallback><other/></e:go></xsl:template>'
    declare = 'xmlns:e="urn:ext" xmlns:other="urn:o" extension-element-prefixes="e"'
    assert transform(_sheet(body, declare=declare), turbohtml.parse_xml("<r/>")) == "ok"


def test_transform_cdata_mixed_length_tokens() -> None:
    body = '<xsl:output cdata-section-elements="xx y"/><xsl:template match="/"><y>&lt;z></y></xsl:template>'
    assert _canon(transform(_sheet(body, method=""), turbohtml.parse_xml("<r/>"))) == "<y><![CDATA[<z>]]></y>"


def test_transform_number_grouping_size_empty() -> None:
    body = '<xsl:template match="/"><xsl:number value="12" grouping-separator="." grouping-size=""/></xsl:template>'
    assert _run("<r/>", body) == "12"


def test_transform_number_grouping_size_below_zero_char() -> None:
    body = '<xsl:template match="/"><xsl:number value="12" grouping-separator="." grouping-size="1/2"/></xsl:template>'
    assert _run("<r/>", body) == "12"


def test_transform_attribute_namespace_reuse_skips_mismatched_declarations() -> None:
    body = (
        '<xsl:template match="/"><out foo="1" xmlns:w="urn:other" xmlns:z="urn:z">'
        '<xsl:attribute name="thing" namespace="urn:z">v</xsl:attribute></out></xsl:template>'
    )
    result = _collapse(_run("<r/>", body, method="xml"))
    assert 'z:thing="v"' in result
    assert 'xmlns:z="urn:z"' in result


def test_transform_namespace_alias_root_with_five_char_attribute() -> None:
    body = (
        '<xsl:namespace-alias stylesheet-prefix="a" result-prefix="#default"/>'
        '<xsl:template match="/"><a:out/></xsl:template>'
    )
    declare = 'width="5" xmlns:a="urn:a" xmlns="urn:default"'
    result = _canon(transform(_sheet(body, method="xml", declare=declare), turbohtml.parse_xml("<r/>")))
    assert 'xmlns:a="urn:default"' in result


def test_transform_number_single_default_element() -> None:
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="//x"/></xsl:template>'
        '<xsl:template match="x">[<xsl:number/>]</xsl:template>'
    )
    assert _run("<d><x/><x/></d>", body) == "[1][2]"


def test_transform_attribute_namespace_reuse_past_long_and_samelen_decls() -> None:
    body = (
        '<xsl:template match="/"><out colspan="1" xmlns:q="xxxxx" xmlns:z="urn:z">'
        '<xsl:attribute name="thing" namespace="urn:z">v</xsl:attribute></out></xsl:template>'
    )
    assert 'z:thing="v"' in _collapse(_run("<r/>", body, method="xml"))


def test_transform_number_multiple_with_empty_format() -> None:
    body = (
        '<xsl:template match="/"><xsl:apply-templates select="//s"/></xsl:template>'
        '<xsl:template match="s">[<xsl:number level="multiple" count="s" format=""/>]</xsl:template>'
    )
    assert _run("<d><s><s/></s></d>", body) == "[1][1.1]"


def test_transform_html_auto_select_with_five_char_attribute() -> None:
    body = '<xsl:template match="/"><html class="x"><body>y</body></html></xsl:template>'
    assert not transform(_sheet(body, method=""), turbohtml.parse_xml("<r/>")).startswith("<?xml")


def test_transform_cdata_element_with_non_text_child() -> None:
    body = '<xsl:output cdata-section-elements="d"/><xsl:template match="/"><d><e/>t</d></xsl:template>'
    result = _canon(transform(_sheet(body, method=""), turbohtml.parse_xml("<r/>")))
    assert result == "<d><e/><![CDATA[t]]></d>"


def test_transform_namespace_alias_requires_stylesheet_prefix() -> None:
    body = '<xsl:namespace-alias result-prefix="xsl"/><xsl:template match="/">x</xsl:template>'
    with pytest.raises(ValueError, match="stylesheet-prefix and result-prefix"):
        transform(_sheet(body), turbohtml.parse_xml("<r/>"))


def test_transform_strip_space_star_suffixed_non_prefix_token() -> None:
    body = (
        '<xsl:strip-space elements="x* p"/><xsl:template match="/"><xsl:apply-templates select="//p"/></xsl:template>'
    )
    assert _run("<r><p> <b>z</b> </p></r>", body) == "z"


def test_transform_transform_element_root() -> None:
    sheet = turbohtml.parse_xml(
        '<xsl:transform version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:output method="text"/><xsl:template match="/">ok</xsl:template></xsl:transform>'
    )
    assert transform(sheet, turbohtml.parse_xml("<r/>")) == "ok"


def test_transform_strip_preserve_specificity_conflict() -> None:
    body = (
        '<xsl:strip-space elements="*"/><xsl:preserve-space elements="p"/>'
        '<xsl:template match="/"><xsl:apply-templates select="//p"/></xsl:template>'
    )
    assert _run("<r><p> <b>z</b> </p></r>", body) == " z "


def test_transform_html_auto_select_whitespace_only_output_stays_xml() -> None:
    body = '<xsl:template match="/"><xsl:text>  </xsl:text></xsl:template>'
    result = transform(_sheet(body, method=""), turbohtml.parse_xml("<r/>"))
    assert result.startswith("<?xml")


def test_transform_strip_less_specific_entry_after_more_specific() -> None:
    body = (
        '<xsl:strip-space elements="p"/><xsl:strip-space elements="*"/>'
        '<xsl:template match="/"><xsl:apply-templates select="//p"/></xsl:template>'
    )
    assert _run("<r><p> <b>z</b> </p></r>", body) == "z"


def test_transform_import_strip_space_precedence(tmp_path: Path) -> None:
    (tmp_path / "base.xsl").write_text(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:preserve-space elements="p"/></xsl:stylesheet>',
        encoding="utf-8",
    )
    main = tmp_path / "main.xsl"
    main.write_text(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:output method="text"/><xsl:import href="base.xsl"/><xsl:strip-space elements="p"/>'
        '<xsl:template match="/"><xsl:apply-templates select="//p"/></xsl:template></xsl:stylesheet>',
        encoding="utf-8",
    )
    sheet = turbohtml.parse_xml(main.read_text(encoding="utf-8"))
    assert transform(sheet, turbohtml.parse_xml("<r><p> <b>z</b> </p></r>"), base_url=str(main)) == "z"


def test_transform_literal_element_other_xsl_attr_same_length_as_use_attribute_sets() -> None:
    # xsl:aaaaaaaaaaaaaaaaaa shares the length of "use-attribute-sets", so the lookup skips it by content
    body = (
        '<xsl:attribute-set name="s"><xsl:attribute name="a">1</xsl:attribute></xsl:attribute-set>'
        '<xsl:template match="/"><out xsl:aaaaaaaaaaaaaaaaaa="x" xsl:use-attribute-sets="s"/></xsl:template>'
    )
    assert _collapse(_run("<r/>", body, method="xml")) == '<out a="1"/>'


def test_transform_stylesheet_without_xsl_prefix_binding_defaults_to_xsl() -> None:
    # the document element declares no xmlns:<prefix>=XSLT-namespace, so the shim falls back to "xsl"
    # and, finding no xsl:import, treats the whole document as a simplified literal-result stylesheet
    result = transform(turbohtml.parse_xml("<greeting>hi</greeting>"), turbohtml.parse_xml("<r/>"))
    assert _canon(result) == "<greeting>hi</greeting>"


def test_transform_attribute_namespace_generates_past_long_non_xmlns_attr() -> None:
    # the element's only attribute is a >6-char non-xmlns name, so the reuse scan rejects it and mints a prefix
    body = (
        '<xsl:template match="/"><out longattr="1">'
        '<xsl:attribute name="thing" namespace="urn:z">v</xsl:attribute></out></xsl:template>'
    )
    assert _collapse(_run("<r/>", body, method="xml")) == '<out longattr="1" xmlns:ns_1="urn:z" ns_1:thing="v"/>'


@pytest.mark.parametrize(
    ("source", "patterns", "visits", "expected"),
    [
        pytest.param(
            "<root><p/><q/><p/></root>",
            'count="p"',
            (0, 3, 0, 1, 2, 0, 3),
            "0|2|0|1|1|0|2|",
            id="cached-document-root-zero",
        ),
        pytest.param(
            "<root><q/><p/><q/><section/><p/><q/><section/><q/></root>",
            'count="p" from="section"',
            (8, 1, 5, 4, 2, 7, 3, 6, 8),
            "0|0|1|0|1|0|1|1|0|",
            id="reverse-zero-reset-then-forward",
        ),
        pytest.param(
            "<root><q/><p/><q/><section/><p/><q/><section/><q/></root>",
            'count="p" from="section"',
            (1, 1, 2, 3, 4, 5, 6, 7, 8),
            "0|0|1|1|0|1|1|0|0|",
            id="repeated-zero-before-index",
        ),
        pytest.param(
            "<root><p/><q/><p/></root>",
            'count="absent"',
            (3, 1, 2, 3),
            "0|0|0|0|",
            id="empty-count-set",
        ),
        pytest.param(
            "<root><p/><q/><p/></root>",
            'count="p" from="absent"',
            (3, 1, 2, 3),
            "2|1|1|2|",
            id="empty-from-set",
        ),
        pytest.param(
            "<root><p/><p/><q/><p/></root>",
            'count="p" from="p"',
            (4, 1, 3, 2, 4),
            "1|1|1|1|1|",
            id="count-from-overlap",
        ),
        pytest.param(
            "<root><p/><q/><p/></root>",
            'count="*"',
            (1, 3, 2, 3),
            "2|4|3|4|",
            id="wildcard-includes-root",
        ),
        pytest.param(
            "<root><p/><q/><p/><section/><p/><q/></root>",
            'from="section"',
            (1, 3, 5, 2, 6, 4, 5),
            "1|2|1|1|1|1|1|",
            id="default-count-name-changes",
        ),
        pytest.param(
            "<root><p/><long/><p/><section/><long/></root>",
            'from="section"',
            (1, 3, 2, 5, 1),
            "1|2|1|1|1|",
            id="default-count-name-length-changes",
        ),
        pytest.param(
            "<root>a<!--a-->b<!--b--><section/>c<!--c--></root>",
            'from="section"',
            (1, 3, 2, 4, 6, 7, 1),
            "1|2|1|2|1|1|1|",
            id="default-count-node-kind-changes",
        ),
        pytest.param(
            '<root><p category="a"/><p category="b"/><p category="a"/></root>',
            'count="p[@category=current()/@category]"',
            (3, 1, 2, 3),
            "2|1|1|2|",
            id="dynamic-count-fallback",
        ),
        pytest.param(
            "<root><p/><q/><p/></root>",
            'count="p" from="q[true()]"',
            (1, 3, 2, 3),
            "1|1|0|1|",
            id="predicate-from-fallback",
        ),
    ],
)
def test_number_explicit_prefix_visits(
    source: str,
    patterns: str,
    visits: tuple[int, ...],
    expected: str,
    explicit_prefix_transform: Callable[[str, tuple[int, ...]], Transform],
) -> None:
    transform: Final = explicit_prefix_transform(patterns, visits)
    assert [transform(parse_xml(source)) for _ in range(2)] == [expected, expected]


def test_number_explicit_prefix_document_reuse(
    explicit_prefix_transform: Callable[[str, tuple[int, ...]], Transform],
) -> None:
    transform: Final = explicit_prefix_transform('count="p" from="section"', (1, 3, 2, 3))
    assert [
        transform(parse_xml(source))
        for source in (
            "<root><p/><p/><p/></root>",
            "<root><q/><section/><p/></root>",
            "<root><p/><section/><q/></root>",
        )
    ] == ["1|3|2|3|", "0|1|0|1|", "1|0|0|0|"]


@pytest.fixture
def explicit_prefix_transform() -> Callable[[str, tuple[int, ...]], Transform]:
    def compile_number(patterns: str, visits: tuple[int, ...]) -> Transform:
        return Transform(
            parse_xml(
                '<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="1.0">'
                '<xsl:output method="text"/><xsl:template match="/">'
                + "".join(
                    f'<xsl:for-each select="{"/" if index == 0 else f"root/node()[{index}]"}">'
                    '<xsl:call-template name="number"/></xsl:for-each>'
                    for index in visits
                )
                + '</xsl:template><xsl:template name="number">'
                f'<xsl:number level="any" {patterns}/><xsl:text>|</xsl:text>'
                "</xsl:template></xsl:stylesheet>"
            )
        )

    return compile_number


@pytest.mark.parametrize(
    ("source", "select", "instructions", "expected"),
    [
        pytest.param(
            "<root/>",
            "/|root",
            '<xsl:number count="root" from="/"/>' * 24,
            "|" + "1" * 24 + "|",
            id="single-document-boundary-separate-instructions",
        ),
        pytest.param(
            "<root><p/><q/><p/></root>",
            "root/*",
            '<xsl:number count="p"/>' * 8,
            "11111111||22222222|",
            id="single-eight-instructions",
        ),
        pytest.param(
            "<root><p/><q/><p/></root>",
            "root/*",
            '<xsl:number count="p"/><xsl:text>/</xsl:text>'
            '<xsl:number count="q"/><xsl:text>/</xsl:text><xsl:number count="p"/>',
            "1//1|/1/|2//2|",
            id="single-count-slot-replacement",
        ),
        pytest.param(
            "<root><p/><p/></root>",
            "root/p",
            '<xsl:number count="p" from="p"/><xsl:text>/</xsl:text>'
            '<xsl:number count="p" from="root"/><xsl:text>/</xsl:text>'
            '<xsl:number count="p" from="p"/>',
            "/1/|/2/|",
            id="single-from-slot-replacement",
        ),
        pytest.param("<root><p/><q/><p/></root>", "root/*", '<xsl:number level="any" count="p"/>', "1|1|2|", id="name"),
        pytest.param(
            "<root><p/><q/><p/></root>", "root/*", '<xsl:number level="any" count="*"/>', "2|3|4|", id="wildcard"
        ),
        pytest.param(
            "<root><p/><p/></root>", "root/p", '<xsl:number level="any" count="missing"/>', "0|0|", id="empty-count"
        ),
        pytest.param(
            "<root><p/><p/></root>",
            "root/p",
            '<xsl:number level="any" count="p" from="missing"/>',
            "1|2|",
            id="empty-from",
        ),
        pytest.param(
            "<root><p/><p/></root>",
            "root/p",
            '<xsl:number level="any" count="p" from="p"/>',
            "1|1|",
            id="count-from-overlap",
        ),
        pytest.param(
            "<root><section><p/><p/></section><section><p/></section></root>",
            "//p",
            '<xsl:number level="any" count="p" from="section"/>',
            "1|2|1|",
            id="section-reset",
        ),
        pytest.param(
            "<root><section><p/><p/></section><section><p/></section></root>",
            "//p",
            '<xsl:number count="p" from="section"/>',
            "1|2|1|",
            id="single",
        ),
        pytest.param(
            "<root><p><p/><p/></p></root>",
            "//p",
            '<xsl:number level="multiple" count="p" format="1.1"/>',
            "1|1.1|1.2|",
            id="multiple",
        ),
        pytest.param(
            '<root><p id="1"/><p id="2"/><p id="3"/></root>',
            "root/p",
            '<xsl:sort select="@id" data-type="number" order="descending"/><xsl:number level="any" count="p"/>',
            "3|2|1|",
            id="reverse",
        ),
        pytest.param(
            "<root><p/><p/></root>",
            "root/p",
            '<xsl:number level="any" count="p"/>' * 8,
            "11111111|22222222|",
            id="same-node-eight-instructions",
        ),
        pytest.param(
            "<root><p/><q/><p/></root>",
            "root/*",
            '<xsl:number level="any" count="p"/><xsl:text>/</xsl:text>'
            '<xsl:number level="any" count="q"/><xsl:text>/</xsl:text><xsl:number level="any" count="p"/>',
            "1/0/1|1/1/1|2/1/2|",
            id="count-slot-replacement",
        ),
        pytest.param(
            "<root><section><p/><p/></section><section><p/></section></root>",
            "//p",
            '<xsl:number level="any" count="p" from="section"/><xsl:text>/</xsl:text>'
            '<xsl:number level="any" count="p" from="root"/>',
            "1/1|2/2|1/3|",
            id="from-slot-replacement",
        ),
        pytest.param(
            '<root><p cat="a"/><p cat="b"/><p cat="a"/></root>',
            "root/p",
            '<xsl:number level="any" count="p[@cat=current()/@cat]"/>',
            "1|1|2|",
            id="dynamic-count",
        ),
        pytest.param(
            '<root><section key="a"><p cat="a"/></section><section key="b"><p cat="b"/></section>'
            '<section key="a"><p cat="a"/></section></root>',
            "//p",
            '<xsl:number level="any" count="p" from="section[@key=current()/@cat]"/>',
            "1|1|1|",
            id="dynamic-from",
        ),
        pytest.param(
            '<root><p cat="a"/><p cat="b"/><p cat="a"/></root>',
            "root/p",
            '<xsl:number level="any" count="p"/><xsl:text>/</xsl:text>'
            '<xsl:number level="any" count="p[@cat=current()/@cat]"/><xsl:text>/</xsl:text>'
            '<xsl:number level="any"/>',
            "1/1/1|2/1/2|3/2/3|",
            id="static-dynamic-default-interleaving",
        ),
        pytest.param(
            "<root><p/><q/><p/></root>", "root/*", '<xsl:number level="any" count="p|q"/>', "1|2|3|", id="union"
        ),
        pytest.param("<root><p/><p/></root>", "root/p", '<xsl:number level="any" count="root/p"/>', "1|2|", id="path"),
        pytest.param("<root><p/><p/></root>", "root/p", '<xsl:number level="any" count=" / "/>', "1|1|", id="document"),
        pytest.param(
            "<root><p/><p/></root>", "root/p", '<xsl:number level="any" count="/root/p"/>', "1|2|", id="absolute-child"
        ),
        pytest.param(
            "<root><p/><p/></root>",
            "root/p",
            """<xsl:number level="any" count="id('missing')"/>""",
            "0|0|",
            id="function-root",
        ),
        pytest.param(
            "<root><p/><p/></root>",
            "root/p",
            """<xsl:number level="any" count="id('missing')/p"/>""",
            "0|0|",
            id="expression-root",
        ),
        pytest.param(
            "<root><p/><p/></root>", "root/p", '<xsl:number level="any" count=" //p "/>', "1|2|", id="absolute-trimmed"
        ),
        pytest.param(
            "<root><p/>text<p/></root>",
            "root/p",
            '<xsl:number level="any" count="text()"/>',
            "0|1|",
            id="text-test",
        ),
        pytest.param(
            '<root><p id="a"/><p id="b"/></root>',
            "root/p/@id",
            '<xsl:number level="any" count="p"/>',
            "1|1|",
            id="attribute-context",
        ),
        pytest.param(
            "<root><p/><p/></root>",
            "root/p",
            '<xsl:number level="any" count="p" value="7"/>',
            "7|7|",
            id="value-bypass",
        ),
    ],
)
def test_number_matcher_contexts(
    source: str, select: str, instructions: str, expected: str, matcher_transform: Callable[[str, str, str], Transform]
) -> None:
    transform: Final = matcher_transform(select, instructions, "")
    assert [transform(parse_xml(source)) for _ in range(2)] == [expected, expected]


@pytest.mark.parametrize("level", ["single", "any"])
def test_number_matcher_reused_documents(level: str, matcher_transform: Callable[[str, str, str], Transform]) -> None:
    transform: Final = matcher_transform("root/*", f'<xsl:number level="{level}" count="p"/>' * 2, "")
    documents: Final = [
        parse_xml(source) for source in ("<root><p/><p/></root>", "<root><q/></root>", "<root><p/></root>")
    ]
    assert [transform(document) for document in (*documents, documents[0])] == [
        "11|22|",
        "|" if level == "single" else "00|",
        "11|",
        "11|22|",
    ]


@pytest.mark.parametrize(
    ("instructions", "expected"),
    [
        pytest.param(
            '<xsl:number level="any" count="/"/><xsl:number level="any" count="/"/>',
            ["11|11|", "11|", "11|11|11|", "11|11|"],
            id="count-document",
        ),
        pytest.param(
            '<xsl:number count="/" from="/"/><xsl:text>:</xsl:text><xsl:number count="p" from="/"/>',
            [":1|:2|", ":1|", ":1|:2|:3|", ":1|:2|"],
            id="from-document",
        ),
    ],
)
def test_number_matcher_document_pattern_reuse(
    instructions: str, expected: list[str], matcher_transform: Callable[[str, str, str], Transform]
) -> None:
    transform: Final = matcher_transform("root/p", instructions, "")
    documents: Final = [parse_xml("<root>" + "<p/>" * count + "</root>") for count in (2, 1, 3)]
    assert [transform(document) for document in (*documents, documents[0])] == expected


def test_number_matcher_whitespace_restore(matcher_transform: Callable[[str, str, str], Transform]) -> None:
    transform: Final = matcher_transform(
        "root/node()", '<xsl:number level="any" count="p"/>', '<xsl:strip-space elements="*"/>'
    )
    document: Final = parse_xml("<root> <p/> <p/> </root>")
    assert (
        [transform(document) for _ in range(2)],
        matcher_transform("root/node()", '<xsl:number level="any" count="p"/>', "")(document),
    ) == (["1|2|", "1|2|"], "0|1|1|2|2|")


@pytest.mark.parametrize("attribute", ["count", "from"])
@pytest.mark.parametrize("pattern", ["p[unknown()]", "p[$missing]", "q:p"], ids=["function", "variable", "prefix"])
def test_number_matcher_errors(
    attribute: str, pattern: str, matcher_transform: Callable[[str, str, str], Transform]
) -> None:
    transform: Final = matcher_transform(
        "root/p", f'<xsl:number count="p"/><xsl:number level="any" {attribute}="{pattern}"/>', ""
    )
    for source in ("<root><p/></root>", "<root><p/><p/></root>"):
        with pytest.raises(ValueError, match=r"^xslt: xsl:number pattern error$"):
            transform(parse_xml(source))
    assert not transform(parse_xml("<root><q/></root>"))


@pytest.fixture
def matcher_transform() -> Callable[[str, str, str], Transform]:
    def compile_matcher(select: str, instructions: str, declarations: str) -> Transform:
        return Transform(
            parse_xml(f"""<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="1.0">
                <xsl:output method="text"/>{declarations}<xsl:template match="/">
                <xsl:for-each select="{select}">{instructions}<xsl:text>|</xsl:text></xsl:for-each>
                </xsl:template></xsl:stylesheet>""")
        )

    return compile_matcher


@pytest.mark.parametrize(
    ("patterns", "expected"),
    [
        pytest.param(
            ('count="p"',) * 8,
            "1," * 8 + "|" + "2," * 8 + "|" + "3," * 8 + "|",
            id="eight-equal-counts",
        ),
        pytest.param(
            ('count="p" from="section"',) * 8,
            "1," * 8 + "|" + "2," * 8 + "|" + "1," * 8 + "|",
            id="eight-equal-counts-and-froms",
        ),
        pytest.param(
            ('from="section"',) * 8,
            "1," * 8 + "|" + "2," * 8 + "|" + "1," * 8 + "|",
            id="eight-equal-froms",
        ),
        pytest.param(
            ('count="p"', 'count="q"', 'count="p"'),
            "1,0,1,|2,1,2,|3,1,3,|",
            id="different-count-content-same-length",
        ),
        pytest.param(
            ('count="p"', 'count="long"', 'count="p"'),
            "1,0,1,|2,0,2,|3,1,3,|",
            id="different-count-lengths",
        ),
        pytest.param(
            ('count="p" from="section"', 'count="p" from="missing"', 'count="p" from="section"'),
            "1,1,1,|2,2,2,|1,3,1,|",
            id="different-from-content-same-length",
        ),
        pytest.param(
            ('count="p" from="section"', 'count="p" from="root"', 'count="p" from="section"'),
            "1,1,1,|2,2,2,|1,3,1,|",
            id="different-from-lengths",
        ),
        pytest.param(
            ('count="p"', 'count="p[@cat=current()/@cat]"', 'count="p"'),
            "1,1,1,|2,2,2,|3,1,3,|",
            id="dynamic-count-between-static-counts",
        ),
        pytest.param(
            (
                'count="p" from="section"',
                'count="p" from="section[@cat != current()/@cat]"',
                'count="p" from="section"',
            ),
            "1,1,1,|2,2,2,|1,3,1,|",
            id="dynamic-from-between-static-froms",
        ),
        pytest.param(
            ('count="absent"', 'count="absent"', 'count="p"', 'count="absent"', 'count="absent"'),
            "0,0,1,0,0,|0,0,2,0,0,|0,0,3,0,0,|",
            id="equal-empty-sets-after-replacement",
        ),
        pytest.param(
            ('count=" p "', 'count=" p "', 'count="p"', 'count=" p "'),
            "1,1,1,1,|2,2,2,2,|3,3,3,3,|",
            id="equal-whitespace-and-distinct-slices",
        ),
        pytest.param(
            ('count="p"', 'count="p|q"', 'count="p"'),
            "1,1,1,|2,3,2,|3,4,3,|",
            id="union-between-static-counts",
        ),
    ],
)
def test_number_matcher_content(
    patterns: tuple[str, ...], expected: str, content_transform: Callable[[tuple[str, ...]], Transform]
) -> None:
    transform: Final = content_transform(patterns)
    source: Final = (
        '<root><section cat="a"/><p cat="a"/><q cat="b"/><p cat="a"/><long/><section cat="b"/><p cat="b"/></root>'
    )
    assert [transform(parse_xml(source)) for _ in range(2)] == [expected, expected]


@pytest.fixture
def content_transform() -> Callable[[tuple[str, ...]], Transform]:
    def compile_patterns(patterns: tuple[str, ...]) -> Transform:
        return Transform(
            parse_xml(
                '<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="1.0">'
                '<xsl:output method="text"/><xsl:template match="/"><xsl:for-each select="root/p">'
                + "".join(f'<xsl:number level="any" {pattern}/><xsl:text>,</xsl:text>' for pattern in patterns)
                + "<xsl:text>|</xsl:text></xsl:for-each></xsl:template></xsl:stylesheet>"
            )
        )

    return compile_patterns


_PAIR: Final = "<xsl:number/><xsl:text>:</xsl:text><xsl:number/>"


@pytest.mark.parametrize(
    ("source", "selection", "numbers", "expected"),
    [
        pytest.param("<r><n/><n/><n/></r>", "r/n", _PAIR, "1:1|2:2|3:3|", id="adjacent-siblings"),
        pytest.param("<r><a/><b/><a/><b/></r>", "r/*", _PAIR, "1:1|1:1|2:2|2:2|", id="same-length-names"),
        pytest.param("<r><a/><bb/><a/></r>", "r/*", _PAIR, "1:1|1:1|2:2|", id="different-length-names"),
        pytest.param(
            "<r><a/>x<!--first-->longer<a/><!--second--></r>",
            "r/node()",
            _PAIR,
            "1:1|1:1|1:1|2:2|2:2|2:2|",
            id="mixed-node-types",
        ),
        pytest.param("<r><n/><n/><n/><n/></r>", "r/n[position() mod 2 = 0]", _PAIR, "2:2|4:4|", id="skipped-siblings"),
        pytest.param(
            '<r><n id="1"/><n id="2"/><n id="3"/></r>',
            "r/n",
            '<xsl:sort select="@id" data-type="number" order="descending"/>' + _PAIR,
            "3:3|2:2|1:1|",
            id="reverse-siblings",
        ),
        pytest.param(
            "<r><g><n/><n/></g><g><n/><n/></g></r>",
            "r/g/n",
            _PAIR,
            "1:1|2:2|1:1|2:2|",
            id="different-parents",
        ),
        pytest.param(
            "<r><n><n/><n/></n><n><n/></n></r>",
            "r/n/n",
            '<xsl:number level="multiple" format="1.1"/><xsl:text>:</xsl:text>'
            '<xsl:number level="multiple" from="n[parent::r]" format="1.1"/>',
            "1.1:1|1.2:2|2.1:1|",
            id="multiple-levels-from-boundary",
        ),
        pytest.param(
            "<r><n/><n/></r>",
            "r/n",
            '<xsl:number from="n"/><xsl:text>:</xsl:text><xsl:number/>',
            ":1|:2|",
            id="from-excludes-current-node",
        ),
        pytest.param(
            "<r><a/><b/><a/><b/></r>",
            "r/*",
            '<xsl:number/><xsl:text>:</xsl:text><xsl:number count="a"/><xsl:text>:</xsl:text><xsl:number/>',
            "1:1:1|1::1|2:2:2|2::2|",
            id="explicit-default-interleaving",
        ),
        pytest.param(
            '<r><book cat="A"/><book cat="B"/><book cat="A"/></r>',
            "r/book",
            '<xsl:number count="book[@cat=current()/@cat]"/><xsl:text>:</xsl:text>'
            '<xsl:number count="book[@cat=current()/@cat]"/>',
            "1:1|1:1|2:2|",
            id="explicit-current-dependent-patterns",
        ),
        pytest.param(
            "<r><n/><n/></r>",
            "r/n",
            '<xsl:number/><xsl:text>:</xsl:text><xsl:number value="7"/><xsl:text>:</xsl:text><xsl:number/>',
            "1:7:1|2:7:2|",
            id="explicit-value-interleaving",
        ),
        pytest.param('<r><n id="a"/><n id="b"/></r>', "r/n/@id", _PAIR, "1:1|1:1|", id="attributes"),
    ],
)
def test_number_memo_criteria(
    source: str, selection: str, numbers: str, expected: str, compile_numbers: Callable[[str], Transform]
) -> None:
    compiled: Final = compile_numbers(
        f'<xsl:template match="/"><xsl:for-each select="{selection}">{numbers}'
        "<xsl:text>|</xsl:text></xsl:for-each></xsl:template>"
    )
    assert compiled(parse_xml(source)) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("<r><n/><n/><n/></r>", "1:1|2:2|3:3|", id="same-name"),
        pytest.param("<r><a/><b/><a/></r>", "1:1|1:1|2:2|", id="changing-name"),
    ],
)
def test_number_memo_same_instruction_target(
    source: str, expected: str, compile_numbers: Callable[[str], Transform]
) -> None:
    compiled: Final = compile_numbers(
        '<xsl:template name="number"><xsl:number/></xsl:template>'
        '<xsl:template match="/"><xsl:for-each select="r/*">'
        '<xsl:call-template name="number"/><xsl:text>:</xsl:text><xsl:call-template name="number"/>'
        "<xsl:text>|</xsl:text></xsl:for-each></xsl:template>"
    )
    assert compiled(parse_xml(source)) == expected


def test_number_memo_compiled_transform_reuse(compile_numbers: Callable[[str], Transform]) -> None:
    compiled: Final = compile_numbers(
        f'<xsl:template match="/"><xsl:for-each select="r/*">{_PAIR}'
        "<xsl:text>|</xsl:text></xsl:for-each></xsl:template>"
    )
    documents: Final = [parse_xml(source) for source in ("<r><n/><n/><n/></r>", "<r><n/></r>", "<r><a/><b/><a/></r>")]
    assert [compiled(document) for document in [*documents, documents[0]]] == [
        "1:1|2:2|3:3|",
        "1:1|",
        "1:1|1:1|2:2|",
        "1:1|2:2|3:3|",
    ]


@pytest.fixture
def compile_numbers() -> Callable[[str], Transform]:
    def compile_body(body: str) -> Transform:
        return Transform(
            parse_xml(
                '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
                f'<xsl:output method="text"/>{body}</xsl:stylesheet>'
            )
        )

    return compile_body


@pytest.mark.parametrize(
    ("context", "declarations", "instructions", "expected"),
    [
        pytest.param(
            ('<root><p id="1"/><p id="2"/><p id="3"/></root>', "root/*"),
            "",
            '<xsl:sort select="@id" data-type="number" order="descending"/><xsl:number level="any"/>',
            "3|2|1|",
            id="reverse-order",
        ),
        pytest.param(
            ("<root><p/><q/><p/></root>", "root/*"), "", '<xsl:number level="any"/>', "1|1|2|", id="mixed-default-names"
        ),
        pytest.param(
            ("<root><p/><long/><p/></root>", "root/*"),
            "",
            '<xsl:number level="any"/>',
            "1|1|2|",
            id="different-name-lengths",
        ),
        pytest.param(
            ("<root><p/><p/><p/></root>", "root/*"),
            "",
            '<xsl:number level="any" value="7"/>',
            "7|7|7|",
            id="explicit-value",
        ),
        pytest.param(
            ("<root><section><p/><p/></section><section><p/></section></root>", "//p"),
            "",
            '<xsl:number level="any" count="p" from="section"/>',
            "1|2|1|",
            id="pattern-resets",
        ),
        pytest.param(
            ("<root><p/><p/></root>", "root/p"),
            "",
            '<xsl:number level="any"/>' * 8,
            "11111111|22222222|",
            id="eight-instructions-same-node",
        ),
        pytest.param(
            ("<root><section><p/><p/></section><section><p/><p/></section></root>", "root/section/p"),
            "",
            '<xsl:number level="any"/>',
            "1|2|3|4|",
            id="different-parents",
        ),
        pytest.param(
            ("<root><section><p/><p/></section><section><p/><p/></section></root>", "root/section/p"),
            "",
            '<xsl:number level="any"/><xsl:text>/</xsl:text><xsl:number/>'
            '<xsl:text>/</xsl:text><xsl:number level="any"/>',
            "1/1/1|2/2/2|3/1/3|4/2/4|",
            id="sibling-interleaving",
        ),
        pytest.param(
            ("<root><section><p/><p/></section><section><p/><p/></section></root>", "root/section/p"),
            "",
            '<xsl:number level="any"/><xsl:text>/</xsl:text>'
            '<xsl:number level="any" count="p" from="section"/><xsl:text>/</xsl:text>'
            '<xsl:number level="any" value="7"/><xsl:text>/</xsl:text><xsl:number level="any"/>',
            "1/1/7/1|2/2/7/2|3/1/7/3|4/2/7/4|",
            id="explicit-options-interleaving",
        ),
        pytest.param(
            ("<root><section><p/><p/></section><section><p/><p/></section></root>", "root/section/p"),
            "",
            '<xsl:number level="any" count="section"/>',
            "1|1|2|2|",
            id="explicit-count-only",
        ),
        pytest.param(
            ("<root><section><p/><p/></section><section><p/><p/></section></root>", "root/section/p"),
            "",
            '<xsl:number level="any" from="section"/>',
            "1|2|1|2|",
            id="explicit-from-only",
        ),
        pytest.param(
            ("<root>one<!--a--><?go a?><p/>two<!--b--><?go b?><q/><p/></root>", "root/node()"),
            "",
            '<xsl:number level="any"/>',
            "1|1|1|1|2|2|2|1|2|",
            id="changing-node-types-and-names",
        ),
        *(
            pytest.param(
                ("<root>one<!--a--><?go a?><p/>two<!--b--><?go b?></root>", f"root/{kind}()"),
                "",
                '<xsl:number level="any"/>',
                "1|2|",
                id=f"repeated-{kind}",
            )
            for kind in ("text", "comment", "processing-instruction")
        ),
        pytest.param(
            ("<root>  <p>A</p> \n <p>B</p> </root>", "root/p/text()"),
            '<xsl:strip-space elements="*"/>',
            '<xsl:number level="any"/>',
            "1|2|",
            id="stripped-whitespace",
        ),
        pytest.param(
            ("<root>  <p>A</p> \n <p>B</p> </root>", "root/p/text()"),
            "",
            '<xsl:number level="any"/>',
            "2|4|",
            id="preserved-whitespace",
        ),
        pytest.param(
            ('<root><p name="a"/><p name="b"/></root>', "root/p/@name"),
            "",
            '<xsl:number level="any"/>',
            "1|1|",
            id="attribute-context",
        ),
    ],
)
def test_number_any_criteria(
    context: tuple[str, str],
    declarations: str,
    instructions: str,
    expected: str,
    prefix_transform: Callable[[str, str], Transform],
) -> None:
    transform: Final = prefix_transform(
        f'<xsl:for-each select="{context[1]}">{instructions}<xsl:text>|</xsl:text></xsl:for-each>', declarations
    )
    document: Final = parse_xml(context[0])
    assert [transform(document) for _ in range(2)] == [expected, expected]


@pytest.mark.parametrize(
    ("visits", "expected"),
    [
        pytest.param((1, 3, 2, 4, 1), "1|3|2|4|1|", id="cached-reverse-then-forward"),
        pytest.param((4, 2, 3, 1, 4), "4|2|3|1|4|", id="first-reverse-then-forward"),
        pytest.param((1, 1, 1, 3, 2, 4), "1|1|1|3|2|4|", id="same-node-before-index"),
    ],
)
def test_number_any_visit_order(
    visits: tuple[int, ...], expected: str, prefix_transform: Callable[[str, str], Transform]
) -> None:
    transform: Final = prefix_transform(
        "".join(
            f'<xsl:for-each select="root/p[{index}]"><xsl:number level="any"/><xsl:text>|</xsl:text></xsl:for-each>'
            for index in visits
        ),
        "",
    )
    assert transform(parse_xml("<root><p/><p/><p/><p/></root>")) == expected


@pytest.mark.parametrize("reverse", [False, True], ids=["forward", "reverse"])
def test_number_any_compiled_reuse_across_documents(
    prefix_transform: Callable[[str, str], Transform], *, reverse: bool
) -> None:
    transform: Final = prefix_transform(
        '<xsl:for-each select="root/p">'
        + ('<xsl:sort select="@id" data-type="number" order="descending"/>' if reverse else "")
        + '<xsl:number level="any"/><xsl:text>|</xsl:text></xsl:for-each>',
        "",
    )
    assert [
        transform(parse_xml(source))
        for source in (
            '<root><p id="1"/><p id="2"/><p id="3"/></root>',
            '<root><p id="1"/></root>',
            '<root><p id="1"/><p id="2"/></root>',
        )
    ] == (["3|2|1|", "1|", "2|1|"] if reverse else ["1|2|3|", "1|", "1|2|"])


@pytest.fixture
def prefix_transform() -> Callable[[str, str], Transform]:
    def compile_prefix(body: str, declarations: str) -> Transform:
        return Transform(
            parse_xml(
                '<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="1.0">'
                f'<xsl:output method="text"/>{declarations}<xsl:template match="/">{body}</xsl:template>'
                "</xsl:stylesheet>"
            )
        )

    return compile_prefix


@pytest.mark.parametrize(
    ("picture", "single", "multiple"),
    [
        pytest.param(None, "1", "1.2.3", id="default"),
        pytest.param("1", "1", "1.2.3", id="one-token"),
        pytest.param("", "1", "1.2.3", id="empty"),
        pytest.param("()", "()1", "()1.2.3", id="no-token"),
        pytest.param("(1)", "(1)", "(1.2.3)", id="prefix-suffix"),
        pytest.param("(1", "(1", "(1.2.3", id="prefix"),
        pytest.param("1)", "1)", "1.2.3)", id="suffix"),
        pytest.param("(01)", "(01)", "(01.02.03)", id="padded"),
        pytest.param("A", "A", "A.B.C", id="alphabetic"),
        pytest.param("1.1", "1", "1.2.3", id="explicit-period"),
        pytest.param("(A-1)", "(A)", "(A-2-3)", id="explicit-hyphen"),
        pytest.param("1:1/1", "1", "1:2/3", id="two-separators"),
    ],
)
def test_number_separator(
    picture: str | None,
    single: str,
    multiple: str,
    number_transform: Callable[[str | None], Transform],
) -> None:
    transform: Final = number_transform(picture)
    assert [
        transform(parse_xml(source))
        for source in (
            "<root><p><target/></p></root>",
            "<root><p><p/><p><p/><p/><p><target/></p></p></p></root>",
        )
    ] == [single, multiple]


@pytest.fixture
def number_transform() -> Callable[[str | None], Transform]:
    def compile_number(picture: str | None) -> Transform:
        formatting: Final = "" if picture is None else f' format="{picture}"'
        return Transform(
            parse_xml(
                '<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="1.0">'
                '<xsl:output method="text"/><xsl:template match="/">'
                '<xsl:for-each select="//target">'
                f'<xsl:number level="multiple" count="p"{formatting}/>'
                "</xsl:for-each></xsl:template></xsl:stylesheet>"
            )
        )

    return compile_number


@pytest.mark.parametrize(
    ("context", "patterns", "expected"),
    [
        pytest.param(
            ('<root><p cat="a"/><p cat="b"/><p cat="a"/></root>', "root/p"),
            "count=\"p[@cat='a']\"",
            "1|1|2|",
            id="static-attribute",
        ),
        pytest.param(
            ("<root><section><p/><p/></section><section><p/><p/></section></root>", "//p"),
            'count="p[1]"',
            "1|1|2|2|",
            id="first-child-per-parent",
        ),
        pytest.param(
            ("<root><section><p/><p/></section><section><p/><p/></section></root>", "//p"),
            'count="p[position()=last()]"',
            "0|1|1|2|",
            id="last-child-per-parent",
        ),
        pytest.param(
            ("<root><p/><p/><p/></root>", "root/p"),
            'count="p[position() mod 2 = 1]"',
            "1|1|2|",
            id="positional-arithmetic",
        ),
        pytest.param(
            ("<root><p/><p/><p/></root>", "root/p"),
            'count="p[true()]"',
            "1|2|3|",
            id="builtin-true",
        ),
        pytest.param(
            ('<root><p cat="a"/><p cat="b"/><p cat="a"/></root>', "root/p"),
            "count=\"p[not(@cat='b')]\"",
            "1|1|2|",
            id="builtin-not",
        ),
        pytest.param(
            ("<root><p>A</p><p> bb </p><p/></root>", "root/p"),
            'count="p[string-length(normalize-space(.)) &gt; 1]"',
            "0|1|1|",
            id="nested-builtins",
        ),
        pytest.param(
            ('<root><p cat="a"/><p cat="b"/><p cat="a"/></root>', "root/p"),
            "count=\"p|p[@cat='a']\"",
            "1|2|3|",
            id="overlapping-union",
        ),
        pytest.param(
            ('<root><p cat="a"/><p id="b"/><p/></root>', "root/p"),
            'count="p[count(@cat|@id) &gt; 0]"',
            "1|2|2|",
            id="union-inside-predicate",
        ),
        *(
            pytest.param(
                ('<root><p cat="a"/><p cat="b"/><p cat="a"/></root>', "root/p"),
                f'count="{pattern}"',
                "1|2|2|",
                id=f"dynamic-union-{index}",
            )
            for index, pattern in enumerate((
                "p[@cat='a']|p[@cat=current()/@cat]",
                "p[@cat=current()/@cat]|p[@cat='a']",
            ))
        ),
        pytest.param(
            ("<root><p/><p/></root>", "root/p"),
            'count="q|p[false()]"',
            "0|0|",
            id="empty-static-union",
        ),
        pytest.param(
            (
                (
                    '<root><section cut="yes"><p/><p/></section><section><p/></section>'
                    '<section cut="yes"><p/></section></root>'
                ),
                "//p",
            ),
            'count="p" from="section[@cut=\'yes\']"',
            "1|2|3|1|",
            id="static-from-predicate",
        ),
        pytest.param(
            ("<root>a<p>b</p>c<p>d</p></root>", "//text()"),
            'count="text()"',
            "1|2|3|4|",
            id="text-node-test",
        ),
        pytest.param(
            ('<root><p cat="a"/><p cat="b"/><p cat="a"/></root>', "root/p"),
            "count=\"p[matches(@cat, '^a$')]\"",
            "1|1|2|",
            id="regex-builtin",
        ),
    ],
)
def test_number_static_patterns(
    context: tuple[str, str],
    patterns: str,
    expected: str,
    static_pattern_transform: Callable[[str, str, str], Transform],
) -> None:
    transform: Final = static_pattern_transform(context[1], patterns, "")
    assert [transform(parse_xml(context[0])) for _ in range(2)] == [expected, expected]


@pytest.mark.parametrize(
    ("patterns", "declarations"),
    [
        pytest.param('count="p[true(1)]"', "", id="builtin-arity"),
        pytest.param('count="p[count(1)]"', "", id="builtin-argument-type"),
        pytest.param('count="p[unknown()]"', "", id="unknown-function"),
        pytest.param('count="p|unknown:p"', "", id="unknown-namespace-union"),
        pytest.param('count="p[unknown:value]"', "", id="unknown-namespace-predicate"),
        pytest.param('count="p[$flag]"', '<xsl:variable name="flag" select="true()"/>', id="local-variable"),
        pytest.param('count="p" from="section[true(1)]"', "", id="from-builtin-error"),
    ],
)
def test_number_pattern_failure_reuse(
    patterns: str, declarations: str, static_pattern_transform: Callable[[str, str, str], Transform]
) -> None:
    transform: Final = static_pattern_transform("//p", patterns, declarations)
    for source in ("<root><section><p/></section></root>", "<root><section><p/><p/></section></root>"):
        with pytest.raises(ValueError, match=r"^xslt: xsl:number pattern error$"):
            transform(parse_xml(source))
    assert not transform(parse_xml("<root><q/></root>"))


@pytest.fixture
def static_pattern_transform() -> Callable[[str, str, str], Transform]:
    def compile_patterns(select: str, patterns: str, declarations: str) -> Transform:
        return Transform(
            parse_xml(
                '<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="1.0">'
                '<xsl:output method="text"/><xsl:template match="/">'
                f'<xsl:for-each select="{select}">{declarations}'
                f'<xsl:number level="any" {patterns}/><xsl:text>|</xsl:text>'
                "</xsl:for-each></xsl:template></xsl:stylesheet>"
            )
        )

    return compile_patterns


_XSLT_NS: Final[str] = "http://www.w3.org/1999/XSL/Transform"


def _stylesheet(body: str) -> turbohtml.Document:
    return turbohtml.parse_xml(
        f'<xsl:stylesheet version="1.0" xmlns:xsl="{_XSLT_NS}"><xsl:output method="text"/>{body}</xsl:stylesheet>'
    )


@pytest.mark.parametrize(
    ("body", "message"),
    [
        pytest.param(
            '<xsl:template match="/"><xsl:value-of select="@("/></xsl:template>', "value-of select", id="select"
        ),
        pytest.param('<xsl:template match="@("/>', "pattern", id="match"),
        pytest.param('<xsl:template match="/"><xsl:number count="@("/></xsl:template>', "pattern", id="number-count"),
        pytest.param(
            '<xsl:template match="/"><out value="{@(}"/></xsl:template>', "attribute value template", id="literal-avt"
        ),
        pytest.param(
            '<xsl:template match="/"><xsl:element name="{@("/></xsl:template>',
            "attribute value template",
            id="element-name-avt",
        ),
        pytest.param(
            '<xsl:template match="/"><out><xsl:attribute name="a" namespace="{@("/></out></xsl:template>',
            "attribute value template",
            id="attribute-namespace-avt",
        ),
    ],
)
def test_transform_compile_rejects_invalid_stylesheet(body: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        Transform(_stylesheet(body))


def test_transform_compile_rejects_non_node_stylesheet() -> None:
    with pytest.raises(TypeError):
        Transform("not a node")  # ty: ignore[invalid-argument-type]  # wrong type on purpose


def test_transform_compile_reuses_documents_and_parameters() -> None:
    convert = Transform(
        _stylesheet(
            '<xsl:param name="suffix"/><xsl:template match="/">'
            '<xsl:value-of select="concat(r/value, $suffix)"/></xsl:template>'
        )
    )

    assert [
        convert(turbohtml.parse_xml("<r><value>one</value></r>"), suffix="'-1'"),
        convert(turbohtml.parse_xml("<r><value>two</value></r>"), suffix="'-2'"),
    ] == ["one-1", "two-2"]


def test_transform_compile_snapshots_stylesheet() -> None:
    stylesheet = _stylesheet('<xsl:template match="/"><xsl:value-of select="\'before\'"/></xsl:template>')
    convert = Transform(stylesheet)
    value = stylesheet.find("xsl:value-of")
    assert value is not None
    value.attrs["select"] = "'after'"

    assert convert(turbohtml.parse_xml("<r/>")) == "before"


def test_transform_compile_rejects_invalid_import(tmp_path: Path) -> None:
    (tmp_path / "imported.xsl").write_text(
        f'<xsl:stylesheet version="1.0" xmlns:xsl="{_XSLT_NS}">'
        '<xsl:template match="/"><xsl:value-of select="@("/></xsl:template></xsl:stylesheet>',
        encoding="utf-8",
    )
    stylesheet = _stylesheet('<xsl:import href="imported.xsl"/>')

    with pytest.raises(ValueError, match="value-of select"):
        Transform(stylesheet, base_url=str(tmp_path / "main.xsl"), import_root=tmp_path)


def test_transform_compile_is_thread_safe() -> None:
    convert = Transform(
        _stylesheet(
            '<xsl:param name="suffix"/><xsl:template match="/">'
            '<xsl:value-of select="concat(r/value, $suffix)"/></xsl:template>'
        )
    )
    barrier = threading.Barrier(4)

    def run(index: int) -> str:
        barrier.wait()
        return convert(turbohtml.parse_xml(f"<r><value>{index}</value></r>"), suffix=f"'-{index}'")

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(run, range(4)))

    assert results == ["0-0", "1-1", "2-2", "3-3"]


_LEVELS: Final = 1_200
_XSLT: Final = "http://www.w3.org/1999/XSL/Transform"


def test_transform_text_output_collects_deep_descendants() -> None:
    source = parse_xml(_nested_xml("bottom"))
    stylesheet = parse_xml(
        f'<xsl:stylesheet version="1.0" xmlns:xsl="{_XSLT}"><xsl:output method="text"/>'
        '<xsl:template match="/"><xsl:copy-of select="."/></xsl:template></xsl:stylesheet>'
    )

    assert Transform(stylesheet)(source) == "bottom"


def test_transform_cdata_conversion_reaches_deep_descendants() -> None:
    source = parse_xml(_nested_xml("<leaf>bottom</leaf>"))
    stylesheet = parse_xml(
        f'<xsl:stylesheet version="1.0" xmlns:xsl="{_XSLT}">'
        '<xsl:output method="xml" omit-xml-declaration="yes" cdata-section-elements="leaf"/>'
        '<xsl:template match="/"><xsl:copy-of select="."/></xsl:template></xsl:stylesheet>'
    )

    assert "<leaf><![CDATA[bottom]]></leaf>" in Transform(stylesheet)(source)


def test_transform_strip_space_reaches_deep_descendants_and_restores_source() -> None:
    source = parse_xml(_nested_xml("  <leaf>bottom</leaf>  "))
    before = source.html
    stylesheet = parse_xml(
        f'<xsl:stylesheet version="1.0" xmlns:xsl="{_XSLT}"><xsl:output method="text"/>'
        '<xsl:strip-space elements="*"/><xsl:template match="/">'
        '<xsl:value-of select="."/></xsl:template></xsl:stylesheet>'
    )

    assert Transform(stylesheet)(source) == "bottom"
    assert source.html == before


def _nested_xml(inner: str) -> str:
    return "<x>" * _LEVELS + inner + "</x>" * _LEVELS


@pytest.mark.parametrize("padding", [pytest.param(1, id="small"), pytest.param(16, id="indexed")])
@pytest.mark.parametrize(
    ("declarations", "body", "expected"),
    [
        pytest.param(
            '<xsl:template name="target">first</xsl:template><xsl:template name="target">second</xsl:template>',
            '<xsl:call-template name="target"/>' * 2,
            "<out>firstfirst</out>",
            id="first-named-template",
        ),
        pytest.param(
            '<xsl:key name="target" match="p" use="@value"/><xsl:key name="target" match="q" use="@value"/>',
            "<xsl:value-of select=\"count(key('target','v'))\"/>:<xsl:value-of select=\"key('target','v')/@kind\"/>",
            "<out>1:P</out>",
            id="first-key-declaration",
        ),
        pytest.param(
            '<xsl:attribute-set name="base"><xsl:attribute name="c">3</xsl:attribute></xsl:attribute-set>'
            '<xsl:attribute-set name="target" use-attribute-sets="base">'
            '<xsl:attribute name="a">1</xsl:attribute></xsl:attribute-set>'
            '<xsl:attribute-set name="target"><xsl:attribute name="b">2</xsl:attribute></xsl:attribute-set>',
            '<item xsl:use-attribute-sets="target"/>',
            '<out><item c="3" a="1" b="2"/></out>',
            id="attribute-set-duplicates-and-chain",
        ),
        pytest.param("", '<item xsl:use-attribute-sets="missing"/>', "<out><item/></out>", id="missing-attribute-set"),
        pytest.param(
            '<xsl:key name="target" match="p" use="@value"/>',
            "<xsl:value-of select=\"count(key('target','absent'))\"/>",
            "<out>0</out>",
            id="missing-key-value",
        ),
    ],
)
def test_transform_name_indexes(
    padding: int,
    declarations: str,
    body: str,
    expected: str,
    indexed_transform: Callable[[int, str, str], Transform],
) -> None:
    transform: Final = indexed_transform(padding, declarations, body)
    assert [
        transform(parse_xml('<root><p value="v" kind="P"/><q value="v" kind="Q"/></root>')).strip() for _ in range(2)
    ] == [expected, expected]


@pytest.mark.parametrize("padding", [pytest.param(1, id="small"), pytest.param(16, id="indexed")])
@pytest.mark.parametrize(
    ("body", "message"),
    [
        pytest.param('<xsl:call-template name="missing"/>', "undeclared template", id="template"),
        pytest.param("<xsl:value-of select=\"key('missing','v')\"/>", "undeclared key", id="key"),
    ],
)
def test_transform_name_index_missing(
    padding: int, body: str, message: str, indexed_transform: Callable[[int, str, str], Transform]
) -> None:
    transform: Final = indexed_transform(padding, "", body)
    with pytest.raises(ValueError, match=message):
        transform(parse_xml("<root/>"))


def test_transform_name_index_reuse(indexed_transform: Callable[[int, str, str], Transform]) -> None:
    transform: Final = indexed_transform(
        16,
        '<xsl:key name="target" match="p" use="@value"/>'
        "<xsl:template name=\"target\"><xsl:value-of select=\"key('target','v')/@kind\"/></xsl:template>",
        '<xsl:call-template name="target"/>',
    )
    assert [transform(parse_xml(f'<root><p value="v" kind="{kind}"/></root>')).strip() for kind in ("A", "B", "A")] == [
        "<out>A</out>",
        "<out>B</out>",
        "<out>A</out>",
    ]


@pytest.fixture
def indexed_transform() -> Callable[[int, str, str], Transform]:
    def compile_transform(padding: int, declarations: str, body: str) -> Transform:
        return Transform(
            parse_xml(
                '<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="1.0">'
                '<xsl:output method="xml" omit-xml-declaration="yes"/>'
                + "".join(
                    f'<xsl:template name="unused{index}"/>'
                    f'<xsl:key name="unused{index}" match="unused" use="@value"/>'
                    f'<xsl:attribute-set name="unused{index}"/>'
                    for index in range(padding)
                )
                + declarations
                + f'<xsl:template match="/"><out>{body}</out></xsl:template></xsl:stylesheet>'
            )
        )

    return compile_transform


@pytest.mark.parametrize(
    ("source", "instructions", "rules", "expected"),
    [
        pytest.param(
            "<root><p " + " ".join(f'a{index}="{index}"' for index in range(96)) + "/></root>",
            '<xsl:apply-templates select="root/p/@*"/>' * 2,
            "".join(f'<xsl:template match="@a{index}">{index}|</xsl:template>' for index in range(96)),
            "".join(f"{index}|" for index in range(96)) * 2,
            id="many-attribute-rules",
        ),
        pytest.param(
            "<root><p/></root>",
            (
                '<xsl:apply-templates select="root/p"/>'
                + "".join(f'<xsl:apply-templates select="root/p" mode="view{index}"/>' for index in range(64))
                + '<xsl:apply-templates select="root/p"/>'
            )
            * 2,
            '<xsl:template match="p">default|</xsl:template>'
            + "".join(f'<xsl:template match="p" mode="view{index}">{index}|</xsl:template>' for index in range(64)),
            ("default|" + "".join(f"{index}|" for index in range(64)) + "default|") * 2,
            id="many-named-and-default-modes",
        ),
        pytest.param(
            "<root>" + "<p/>" * 40 + "</root>",
            '<xsl:apply-templates select="root/p"/>' * 2,
            '<xsl:template match="p">X</xsl:template>',
            "X" * 80,
            id="growing-cache",
        ),
        pytest.param(
            "<root><p/></root>",
            '<xsl:apply-templates select="root/p"/>'
            '<xsl:apply-templates select="root/p" mode="m"/>'
            '<xsl:apply-templates select="root/p" mode="n"/>'
            '<xsl:apply-templates select="root/p" mode="m"/>'
            '<xsl:apply-templates select="root/p"/>',
            '<xsl:template match="p">D</xsl:template>'
            '<xsl:template match="p" mode="m">M</xsl:template>'
            '<xsl:template match="p" mode="n">N</xsl:template>',
            "DMNMD",
            id="mode-content-and-default",
        ),
        pytest.param(
            '<root><p a="1" b="2"/></root>',
            '<xsl:apply-templates select="root/p/@*"/>' * 2,
            '<xsl:template match="@a">A<xsl:value-of select="."/></xsl:template>'
            '<xsl:template match="@b">B<xsl:value-of select="."/></xsl:template>',
            "A1B2A1B2",
            id="attribute-indices",
        ),
        pytest.param(
            "<root><p>A<b>B</b><!--ignored--></p></root>",
            '<xsl:apply-templates select="root/p"/>' * 2,
            "",
            "ABAB",
            id="cached-builtin-misses",
        ),
        pytest.param(
            '<root><p a="A" b="B"/></root>',
            '<xsl:apply-templates select="root/p/@*"/>' * 2,
            "",
            "ABAB",
            id="cached-attribute-misses",
        ),
        pytest.param(
            "<root><p/><p/></root>",
            '<xsl:apply-templates select="root/p"/>' * 2,
            '<xsl:template match="p" priority="1">L</xsl:template>'
            '<xsl:template match="p" priority="2">H</xsl:template>',
            "HHHH",
            id="priority",
        ),
        pytest.param(
            "<root><p/><q/></root>",
            '<xsl:apply-templates select="root/*"/>' * 2,
            '<xsl:template match="p|q">A</xsl:template><xsl:template match="p|q">B</xsl:template>',
            "BBBB",
            id="union-and-position-tie",
        ),
    ],
)
def test_transform_rule_cache(
    source: str,
    instructions: str,
    rules: str,
    expected: str,
    cached_transform: Callable[[str, str], Transform],
) -> None:
    transform: Final = cached_transform(instructions, rules)
    assert [transform(parse_xml(source)) for _ in range(2)] == [expected, expected]


def test_transform_rule_cache_reuse(cached_transform: Callable[[str, str], Transform]) -> None:
    transform: Final = cached_transform(
        '<xsl:apply-templates select="root/p"/>' * 2,
        '<xsl:template match="p"><xsl:value-of select="."/></xsl:template>',
    )
    assert [transform(parse_xml(f"<root><p>{value}</p></root>")) for value in ("first", "second", "first")] == [
        "firstfirst",
        "secondsecond",
        "firstfirst",
    ]


@pytest.fixture
def cached_transform() -> Callable[[str, str], Transform]:
    def compile_transform(instructions: str, rules: str) -> Transform:
        return Transform(
            parse_xml(
                '<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="1.0">'
                '<xsl:output method="text"/>'
                + "".join(f'<xsl:template match="unused{index}"/>' for index in range(16))
                + f'<xsl:template match="/">{instructions}</xsl:template>{rules}</xsl:stylesheet>'
            )
        )

    return compile_transform


@pytest.mark.parametrize("library", ["core", "competitors.lxml"], ids=["turbohtml", "lxml"])
@pytest.mark.parametrize(
    "name",
    [
        pytest.param("transform-names", id="many-declarations"),
        pytest.param("transform-names-small", id="small"),
        pytest.param("transform-names-compile", id="compile-source"),
    ],
)
def test_name_benchmark_output(library: str, name: str) -> None:
    module: Final = pytest.importorskip(f"bench.{library}", exc_type=ImportError)
    operation: Final = module.OPERATIONS["transform-names"][0]
    _, _, load = next(benchmark for benchmark in benchmarks() if benchmark[0] == name)
    expected: Final = "<out>" + '<item marker="hit">1</item>' * 256 + "</out>"
    assert str(operation(cast("tuple[str, str]", load()))).strip() == expected


@pytest.mark.parametrize("library", ["core", "competitors.lxml"], ids=["turbohtml", "lxml"])
@pytest.mark.parametrize(
    ("case", "rows", "instructions", "step"),
    [
        pytest.param(0, 200, 1, 1, id="single-200"),
        pytest.param(1, 200, 8, 1, id="repeated-200"),
        pytest.param(2, 2_000, 1, 1, id="single-2000"),
        pytest.param(3, 2_000, 8, 1, id="repeated-2000"),
        pytest.param(4, 1, 8, 1, id="one-node"),
        pytest.param(5, 200, 8, -1, id="reverse"),
        pytest.param(6, 200, 0, 1, id="no-number"),
    ],
)
def test_number_benchmark_output(library: str, case: int, rows: int, instructions: int, step: int) -> None:
    module: Final = pytest.importorskip(f"bench.{library}", exc_type=ImportError)
    operation: Final = module.OPERATIONS["transform-number"][0]
    positions: Final = range(1, rows + 1) if step == 1 else range(rows, 0, -1)
    expected: Final = "".join(f"{position}:" * instructions + "|" for position in positions)
    assert str(operation(cast("tuple[str, str]", INPUTS["transform-number"]()[case][1]))) == expected


@pytest.mark.parametrize(
    ("name", "instructions", "rows", "step"),
    [
        pytest.param("transform-number", 8, 2_000, 1, id="repeated"),
        pytest.param("transform-number-single", 1, 2_000, 1, id="single"),
        pytest.param("transform-number-any", 1, 1_024, 1, id="any-forward"),
        pytest.param("transform-number-any-reversed", 1, 1_024, -1, id="any-reverse"),
        pytest.param("transform-number-count", 1, 1_024, 1, id="any-count"),
        pytest.param("transform-number-predicate", 1, 1_024, 1, id="any-predicate"),
    ],
)
def test_codspeed_number_benchmark_output(name: str, instructions: int, rows: int, step: int) -> None:
    _, operation, load = next(case for case in benchmarks() if case[0] == name)
    positions: Final = range(1, rows + 1) if step == 1 else range(rows, 0, -1)
    expected: Final = "".join(f"{position}:" * instructions + "|" for position in positions)
    assert cast("Callable[[tuple[str, str]], str]", operation)(cast("tuple[str, str]", load())) == expected


@pytest.mark.parametrize("library", ["core", "competitors.lxml"], ids=["turbohtml", "lxml"])
@pytest.mark.parametrize(
    ("case", "expected"),
    [
        pytest.param(7, "".join(f"{ordinal}:|" for ordinal in range(1, 33)), id="any-small"),
        pytest.param(8, "".join(f"{ordinal}:|" for ordinal in range(1, 1_025)), id="any-forward"),
        pytest.param(9, "".join(f"{ordinal}:|" for ordinal in range(1_024, 0, -1)), id="any-reverse"),
        pytest.param(10, "1024:|", id="any-last-only"),
        pytest.param(11, "".join(f"{ordinal}:|" * 2 for ordinal in range(1, 513)), id="any-alternating"),
        pytest.param(12, "".join(f"{ordinal}:|" for ordinal in range(1, 1_025)), id="any-mixed-nodes"),
        pytest.param(13, "1:" * 8 + "|", id="any-repeated"),
        pytest.param(14, "".join(f"{ordinal}:|" for ordinal in range(1, 1_025)), id="any-explicit-count"),
        pytest.param(15, "1:|2:|", id="any-two-nodes"),
        pytest.param(16, "".join(f"{ordinal}:|" for ordinal in range(1, 9)), id="any-eight-nodes"),
        pytest.param(17, "1:|", id="count-single-call"),
        pytest.param(18, "1:|", id="predicate-single-call"),
        pytest.param(19, "1:|", id="union-single-call"),
        pytest.param(20, "".join(f"{ordinal}:|" for ordinal in range(1, 1_025)), id="count-predicate"),
        pytest.param(22, "".join(f"{ordinal}:|" for ordinal in range(1, 65)) * 16, id="count-from-sections"),
        pytest.param(23, "".join(f"{ordinal}:|" for ordinal in range(1_024, 0, -1)), id="count-reverse"),
        pytest.param(24, "".join(f"{ordinal}:" * 8 + "|" for ordinal in range(1, 1_025)), id="count-repeated"),
        pytest.param(25, "".join(f"{ordinal}:|" for ordinal in range(2, 1_026)), id="count-wildcard"),
        pytest.param(26, "0:|" * 1_024, id="count-empty"),
        pytest.param(27, "1024:|", id="count-last-only"),
        pytest.param(28, "64:|", id="count-from-last-only"),
        pytest.param(29, "".join(f"{ordinal}:|" * 2 for ordinal in range(1, 33)) * 16, id="from-sections-alternating"),
    ],
)
def test_any_number_benchmark_output(library: str, case: int, expected: str) -> None:
    module: Final = pytest.importorskip(f"bench.{library}", exc_type=ImportError)
    operation: Final = module.OPERATIONS["transform-number"][0]
    assert str(operation(cast("tuple[str, str]", INPUTS["transform-number"]()[case][1]))) == expected


def test_number_benchmark_current_pattern() -> None:
    expected: Final = "".join(f"{ordinal}:|" * 2 for ordinal in range(1, 513))
    assert core.transform(cast("tuple[str, str]", INPUTS["transform-number"]()[21][1])) == expected


def test_codspeed_number_from_benchmark_output() -> None:
    _, operation, load = next(case for case in benchmarks() if case[0] == "transform-number-count-from")
    expected: Final = "".join(f"{ordinal}:|" for ordinal in range(1, 65)) * 16
    assert cast("Callable[[tuple[str, str]], str]", operation)(cast("tuple[str, str]", load())) == expected


@pytest.mark.parametrize("library", ["core", "competitors.lxml"], ids=["turbohtml", "lxml"])
def test_dense_number_benchmark_output(library: str) -> None:
    module: Final = pytest.importorskip(f"bench.{library}", exc_type=ImportError)
    operation: Final = module.OPERATIONS["transform-dense"][0]
    expected: Final = (
        '<?xml version="1.0"?>\n<out>'
        + "".join(
            f"<row>Book number {index}"
            + "".join(f"{index + 1}<!--c{unit}--><title>Book number {index}</title>" for unit in range(8))
            + "</row>"
            for index in range(200)
        )
        + "</out>"
    )
    assert str(operation(cast("tuple[str, str]", INPUTS["transform-dense"]()[0][1]))).strip() == expected


@pytest.mark.parametrize("library", ["core", "competitors.lxml"], ids=["turbohtml", "lxml"])
@pytest.mark.parametrize(
    ("name", "passes"),
    [pytest.param("transform-rules", 8, id="repeated"), pytest.param("transform-rules-single", 1, id="single")],
)
def test_rule_benchmark_output(library: str, name: str, passes: int) -> None:
    module: Final = pytest.importorskip(f"bench.{library}", exc_type=ImportError)
    operation: Final = module.OPERATIONS["transform-rules"][0]
    _, _, load = next(benchmark for benchmark in benchmarks() if benchmark[0] == name)
    expected: Final = "".join(f"{ordinal}|" for ordinal in range(1, 1_025)) * passes
    assert str(operation(cast("tuple[str, str]", load()))) == expected


def _oracle_sheet(body: str, *, method: str = "xml") -> str:
    return f'<xsl:stylesheet version="1.0" {_NS}><xsl:output method="{method}"/>{body}</xsl:stylesheet>'


_ORACLE_CATALOG: Final = (
    '<catalog><book id="b1" cat="fiction"><title>Dune</title><price>9.99</price></book>'
    '<book id="b2" cat="science"><title>Cosmos</title><price>12.50</price></book>'
    '<book id="b3" cat="fiction"><title>1984</title><price>8.00</price></book></catalog>'
)

_ORACLE_CASES: Final = [
    pytest.param(
        "<r><name>World</name></r>",
        _oracle_sheet('<xsl:template match="/">Hi <xsl:value-of select="//name"/></xsl:template>', method="text"),
        {},
        "text",
        id="value-of-text",
    ),
    pytest.param(
        _ORACLE_CATALOG,
        _oracle_sheet(
            '<xsl:template match="/"><ul>'
            '<xsl:apply-templates select="catalog/book">'
            '<xsl:sort select="title"/></xsl:apply-templates></ul></xsl:template>'
            '<xsl:template match="book"><li class="{@cat}"><xsl:value-of select="title"/></li></xsl:template>',
            method="html",
        ),
        {},
        "html",
        id="apply-templates-sort-html",
    ),
    pytest.param(
        _ORACLE_CATALOG,
        _oracle_sheet(
            '<xsl:template match="/"><out>'
            '<xsl:for-each select="catalog/book">'
            '<item n="{position()}" id="{@id}"/></xsl:for-each></out></xsl:template>'
        ),
        {},
        "xml",
        id="for-each-position",
    ),
    pytest.param(
        _ORACLE_CATALOG,
        _oracle_sheet(
            '<xsl:template match="/"><report>'
            '<xsl:for-each select="catalog/book">'
            '<xsl:sort select="price" data-type="number" order="descending"/>'
            '<row><xsl:value-of select="position()"/>:<xsl:value-of select="title"/></row>'
            "</xsl:for-each></report></xsl:template>"
        ),
        {},
        "xml",
        id="sort-number-descending",
    ),
    pytest.param(
        _ORACLE_CATALOG,
        _oracle_sheet(
            '<xsl:key name="bycat" match="book" use="@cat"/>'
            '<xsl:template match="/">'
            "<counts fiction=\"{count(key('bycat','fiction'))}\" science=\"{count(key('bycat','science'))}\"/>"
            "</xsl:template>"
        ),
        {},
        "xml",
        id="key",
    ),
    pytest.param(
        "<doc><n>5</n></doc>",
        _oracle_sheet(
            '<xsl:template match="/"><xsl:choose>'
            '<xsl:when test="doc/n &gt; 3">big</xsl:when><xsl:otherwise>small</xsl:otherwise>'
            "</xsl:choose></xsl:template>",
            method="text",
        ),
        {},
        "text",
        id="choose",
    ),
    pytest.param(
        "<r/>",
        _oracle_sheet(
            '<xsl:template match="/"><xsl:call-template name="stars">'
            '<xsl:with-param name="c" select="4"/></xsl:call-template></xsl:template>'
            '<xsl:template name="stars"><xsl:param name="c"/>'
            '<xsl:if test="$c &gt; 0">*<xsl:call-template name="stars">'
            '<xsl:with-param name="c" select="$c - 1"/></xsl:call-template></xsl:if></xsl:template>',
            method="text",
        ),
        {},
        "text",
        id="call-template-recursion",
    ),
    pytest.param(
        '<a x="1"><b>t</b><c/></a>',
        _oracle_sheet(
            '<xsl:template match="@*|node()"><xsl:copy>'
            '<xsl:apply-templates select="@*|node()"/></xsl:copy></xsl:template>'
        ),
        {},
        "xml",
        id="identity",
    ),
    pytest.param(
        "<doc><s/><s/><s/></doc>",
        _oracle_sheet(
            '<xsl:template match="/"><list>'
            '<xsl:for-each select="doc/s"><n><xsl:number format="i"/></n></xsl:for-each></list></xsl:template>'
        ),
        {},
        "xml",
        id="number-roman",
    ),
    pytest.param(
        "<doc><v>1234.5</v></doc>",
        _oracle_sheet(
            '<xsl:template match="/"><out>'
            "<xsl:value-of select=\"format-number(doc/v, '#,##0.00')\"/></out></xsl:template>"
        ),
        {},
        "xml",
        id="format-number",
    ),
    pytest.param(
        "<doc/>",
        _oracle_sheet(
            '<xsl:param name="who" select="\'anon\'"/>'
            '<xsl:template match="/"><greeting><xsl:value-of select="$who"/></greeting></xsl:template>'
        ),
        {"who": "'Alice'"},
        "xml",
        id="top-level-param",
    ),
    pytest.param(
        _ORACLE_CATALOG,
        _oracle_sheet(
            '<xsl:template match="/"><xsl:apply-templates select="catalog/book"/></xsl:template>'
            '<xsl:template match="book[@cat=\'fiction\']">F:<xsl:value-of select="title"/> </xsl:template>'
            '<xsl:template match="book">O:<xsl:value-of select="title"/> </xsl:template>',
            method="text",
        ),
        {},
        "text",
        id="conflict-resolution-predicate-priority",
    ),
]


@pytest.mark.parametrize(("source", "sheet", "params", "method"), _ORACLE_CASES)
@pytest.mark.oracle
def test_transform_matches_lxml(source: str, sheet: str, params: dict[str, str], method: str) -> None:
    mine = Transform(turbohtml.parse_xml(sheet))(turbohtml.parse_xml(source), **params)
    etree: Final = pytest.importorskip("lxml.etree")
    theirs = str(etree.XSLT(etree.fromstring(sheet.encode()))(etree.fromstring(source.encode()), **params))
    if method == "text":
        assert mine == theirs
    else:
        assert _canon(mine) == _canon(theirs)


@pytest.mark.parametrize("attribute", ["count", "from"])
@pytest.mark.oracle
def test_number_benchmark_lxml_current_pattern_unsupported(attribute: str) -> None:
    lxml: Final = pytest.importorskip("bench.competitors.lxml", exc_type=ImportError)
    sheet, source = cast("tuple[str, str]", INPUTS["transform-number"]()[21][1])
    with pytest.raises(NotImplementedError, match=r"XSLT 1\.0 forbids current\(\) in patterns"):
        lxml.transform((sheet.replace('count="', f'{attribute}="'), source))


@pytest.mark.oracle
def test_number_benchmark_lxml_current_expression() -> None:
    lxml: Final = pytest.importorskip("bench.competitors.lxml", exc_type=ImportError)
    sheet: Final = (
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:output method="text"/><xsl:template match="/"><xsl:for-each select="root/p">'
        '<xsl:number count="p"/><xsl:text>:</xsl:text><xsl:value-of select="current()/@id"/>'
        "</xsl:for-each></xsl:template></xsl:stylesheet>"
    )
    assert str(lxml.transform((sheet, '<root><p id="7"/></root>'))) == "1:7"


@pytest.mark.parametrize(
    ("index", "count"),
    [
        pytest.param(0, 4096, id="builtin-many"),
        pytest.param(1, 4, id="builtin-small"),
        pytest.param(2, 4096, id="literal-many"),
        pytest.param(3, 4, id="literal-small"),
        pytest.param(4, 4096, id="explicit-many"),
        pytest.param(5, 4, id="explicit-small"),
    ],
)
def test_transform_text_benchmark(index: int, count: int) -> None:
    case: Final = cast("tuple[str, str]", INPUTS["transform-text"]()[index][1])
    assert Transform(parse_xml(case[0]))(parse_xml(case[1])) == "payload " * (8 * count)


@pytest.mark.parametrize(
    ("location", "cdata"),
    [
        pytest.param("source", False, id="builtin"),
        pytest.param("xsl:template", False, id="literal"),
        pytest.param("xsl:template", True, id="cdata"),
        pytest.param("xsl:text", False, id="explicit"),
    ],
)
def test_transform_empty_character_data(location: str, *, cdata: bool) -> None:
    stylesheet: Final = _sheet('<xsl:template match="/"><xsl:text>kept</xsl:text><xsl:apply-templates/></xsl:template>')
    source: Final = parse_xml("<r/>")
    parent: Final = source.find("r") if location == "source" else stylesheet.find(location)
    assert parent is not None
    parent.append(turbohtml.CData("") if cdata else turbohtml.Text(""))
    assert Transform(stylesheet)(source) == "kept"


def test_transform_builtin_text_tracks_source_mutation() -> None:
    convert: Final = Transform(_sheet('<xsl:template match="/"><xsl:apply-templates/></xsl:template>'))
    source: Final = parse_xml("<r>before</r>")
    root: Final = source.find("r")
    assert root is not None
    before: Final = convert(source)
    root.text = "after é😀"
    assert (before, convert(source)) == ("before", "after é😀")


def test_transform_result_fragment_text_survives_output_growth() -> None:
    body: Final = (
        '<xsl:template match="/"><xsl:variable name="fragment"><xsl:apply-templates/></xsl:variable>'
        '<xsl:copy-of select="$fragment"/><xsl:copy-of select="$fragment"/></xsl:template>'
    )
    payload: Final = "é😀payload " * 64
    source: Final = "<r>" + ("<p>" + payload + "</p>") * 128 + "</r>"
    assert _run(source, body) == payload * 256
