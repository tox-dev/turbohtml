"""Differential XSLT: turbohtml's transform output against lxml (``etree.XSLT``) on real stylesheets.

lxml ships no wheels for the free-threaded builds, 3.15, or Windows, so this whole module is skipped there and lives in
its own file (added to ``[tool.coverage] run.omit``) so the skip never drops the coverage gate. lxml's ``html`` method
pretty-prints and its ``xml`` method emits a declaration, so element output is compared after collapsing insignificant
whitespace and dropping the declaration; ``text`` output is compared verbatim.
"""

from __future__ import annotations

import re

import pytest

import turbohtml
from turbohtml.transform import transform

etree = pytest.importorskip("lxml.etree")

_NS = 'xmlns:xsl="http://www.w3.org/1999/XSL/Transform"'


def _sheet(body: str, *, method: str = "xml") -> str:
    return f'<xsl:stylesheet version="1.0" {_NS}><xsl:output method="{method}"/>{body}</xsl:stylesheet>'


def _canon(text: str) -> str:
    text = re.sub(r"<\?xml[^>]*\?>\s*", "", text)
    return re.sub(r">\s+<", "><", text.strip())


def _lxml(sheet: str, source: str, params: dict[str, str]) -> str:
    result = etree.XSLT(etree.fromstring(sheet.encode()))(etree.fromstring(source.encode()), **params)
    return str(result)


_CATALOG = (
    '<catalog><book id="b1" cat="fiction"><title>Dune</title><price>9.99</price></book>'
    '<book id="b2" cat="science"><title>Cosmos</title><price>12.50</price></book>'
    '<book id="b3" cat="fiction"><title>1984</title><price>8.00</price></book></catalog>'
)

_CASES = [
    pytest.param(
        "<r><name>World</name></r>",
        _sheet('<xsl:template match="/">Hi <xsl:value-of select="//name"/></xsl:template>', method="text"),
        {},
        "text",
        id="value-of-text",
    ),
    pytest.param(
        _CATALOG,
        _sheet(
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
        _CATALOG,
        _sheet(
            '<xsl:template match="/"><out>'
            '<xsl:for-each select="catalog/book">'
            '<item n="{position()}" id="{@id}"/></xsl:for-each></out></xsl:template>'
        ),
        {},
        "xml",
        id="for-each-position",
    ),
    pytest.param(
        _CATALOG,
        _sheet(
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
        _CATALOG,
        _sheet(
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
        _sheet(
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
        _sheet(
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
        _sheet(
            '<xsl:template match="@*|node()"><xsl:copy>'
            '<xsl:apply-templates select="@*|node()"/></xsl:copy></xsl:template>'
        ),
        {},
        "xml",
        id="identity",
    ),
    pytest.param(
        "<doc><s/><s/><s/></doc>",
        _sheet(
            '<xsl:template match="/"><list>'
            '<xsl:for-each select="doc/s"><n><xsl:number format="i"/></n></xsl:for-each></list></xsl:template>'
        ),
        {},
        "xml",
        id="number-roman",
    ),
    pytest.param(
        "<doc><v>1234.5</v></doc>",
        _sheet(
            '<xsl:template match="/"><out>'
            "<xsl:value-of select=\"format-number(doc/v, '#,##0.00')\"/></out></xsl:template>"
        ),
        {},
        "xml",
        id="format-number",
    ),
    pytest.param(
        "<doc/>",
        _sheet(
            '<xsl:param name="who" select="\'anon\'"/>'
            '<xsl:template match="/"><greeting><xsl:value-of select="$who"/></greeting></xsl:template>'
        ),
        {"who": "'Alice'"},
        "xml",
        id="top-level-param",
    ),
    pytest.param(
        _CATALOG,
        _sheet(
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


@pytest.mark.parametrize(("source", "sheet", "params", "method"), _CASES)
def test_transform_matches_lxml(source: str, sheet: str, params: dict[str, str], method: str) -> None:
    mine = transform(turbohtml.parse_xml(sheet), turbohtml.parse_xml(source), **params)
    theirs = _lxml(sheet, source, params)
    if method == "text":
        assert mine == theirs
    else:
        assert _canon(mine) == _canon(theirs)
