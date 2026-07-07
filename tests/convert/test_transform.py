"""XSLT 1.0 transformation: every instruction, conflict resolution, and output method through the public API."""

from __future__ import annotations

import re

import pytest

import turbohtml
from turbohtml._html import _xslt_transform
from turbohtml.transform import Transform, transform

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
    return transform(_sheet(body, method=method, prefix=prefix), turbohtml.parse_xml(source), **params)


def _collapse(text: str) -> str:
    """Drop whitespace between tags so content compares without a serializer's layout."""
    return re.sub(r">\s+<", "><", text.strip())


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
    assert "<?pi data>" in result


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
    assert "<?go data>" in result


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


def test_transform_too_many_sort_keys_raises() -> None:
    sorts = "".join('<xsl:sort select="."/>' for _ in range(9))
    body = f'<xsl:template match="/"><xsl:for-each select="r/n">{sorts}x</xsl:for-each></xsl:template>'
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
    assert "<?pi x>" in _run("<r><?pi x?></r>", body, method="xml")


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
    assert "<out><in/></out>" in result or "<out ><in/></out>" in result


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


def test_transform_bad_arguments_raise_type_error() -> None:
    with pytest.raises(TypeError):
        _xslt_transform("not a node", turbohtml.parse_xml("<r/>"))  # ty: ignore[invalid-argument-type]  # wrong type on purpose


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
        '<xsl:with-param name="a" select="1"/><xsl:with-param name="b" select="@("/></xsl:call-template></xsl:template>'
        '<xsl:template name="t"/>'
    )
    with pytest.raises(ValueError, match="xslt"):
        _run("<r/>", body)


def test_transform_param_default_bad_expression_raises() -> None:
    body = (
        '<xsl:template match="/"><xsl:call-template name="t"/></xsl:template>'
        '<xsl:template name="t"><xsl:param name="p" select="@("/><xsl:value-of select="$p"/></xsl:template>'
    )
    with pytest.raises(ValueError, match="xslt"):
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
    sheet = _sheet('<xsl:template match="/">x</xsl:template>')
    with pytest.raises(TypeError):
        _xslt_transform(sheet, "not a node")  # ty: ignore[invalid-argument-type]  # wrong type on purpose


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
    assert "<?go x>" in _run("<r><?go x?></r>", body, method="xml")


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
        '<xsl:template match="n"><xsl:param name="p" select="@("/><xsl:value-of select="$p"/></xsl:template>'
    )
    with pytest.raises(ValueError, match="xslt"):
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
    assert "<?go x>" in _run("<r><?go x?></r>", body, method="xml")


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
    assert _run("<r/>", body) == "7"


def test_transform_number_format_digit_then_symbol() -> None:
    body = '<xsl:template match="/"><xsl:number value="7" format="0#"/></xsl:template>'
    assert _run("<r/>", body) == "7"


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
    assert "<out><inner>x</inner></out>" in result


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
