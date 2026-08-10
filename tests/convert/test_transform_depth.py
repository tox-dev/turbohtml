from __future__ import annotations

from typing import Final

from turbohtml import parse_xml
from turbohtml.transform import Transform

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
