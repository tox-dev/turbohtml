from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Final

import pytest

from turbohtml import Text, parse_xml
from turbohtml.transform import Transform


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("", id="empty"),
        pytest.param("plain & < >", id="ascii"),
        pytest.param("café", id="latin1"),
        pytest.param("\u0100\ufffe\ufeff", id="bmp"),
        pytest.param("\U00010000\U0010ffff\uffff", id="astral"),
        pytest.param("\ud800\udfff", id="surrogates"),
        pytest.param("\x00", id="null"),
    ],
)
@pytest.mark.parametrize("depth", [pytest.param(0, id="flat"), pytest.param(64, id="nested")])
def test_transform_text_preserves_characters(value: str, depth: int) -> None:
    sheet: Final = parse_xml(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:output method="text"/><xsl:param name="value"/><xsl:template match="/">'
        "<xsl:text>before</xsl:text><xsl:comment>excluded</xsl:comment>"
        '<xsl:processing-instruction name="excluded">data</xsl:processing-instruction>'
        + "<part>" * depth
        + '<xsl:value-of select="$value"/>'
        + "</part>" * depth
        + "<xsl:text>after</xsl:text></xsl:template></xsl:stylesheet>"
    )
    assert Transform(sheet)(parse_xml("<root/>"), value=f"'{value}'") == f"before{value}after"


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("", id="empty"),
        pytest.param("<part/>", id="element"),
        pytest.param("<xsl:comment>ignored</xsl:comment>", id="comment"),
    ],
)
def test_transform_text_empty_output(body: str) -> None:
    sheet: Final = parse_xml(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:output method="text"/><xsl:template match="/">' + body + "</xsl:template></xsl:stylesheet>"
    )
    assert not Transform(sheet)(parse_xml("<root/>"))


@pytest.mark.parametrize("value", ["ascii", "\u0100", "\U00010000"], ids=["ascii", "bmp", "astral"])
def test_transform_text_copies_empty_nodes(value: str) -> None:
    source: Final = parse_xml("<root/>")
    assert source.root is not None
    source.root.extend([Text(""), Text(value), Text("")])
    sheet: Final = parse_xml(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:output method="text"/><xsl:template match="/">'
        '<xsl:copy-of select="."/></xsl:template></xsl:stylesheet>'
    )
    assert Transform(sheet)(source) == value


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("{value}", id="literal"),
        pytest.param("<![CDATA[{value}]]>", id="cdata"),
        pytest.param("<xsl:text>{value}</xsl:text>", id="explicit"),
    ],
)
@pytest.mark.parametrize(
    "value",
    [
        pytest.param("plain", id="ascii"),
        pytest.param("café", id="latin1"),
        pytest.param("日本語", id="bmp"),
        pytest.param("\U00010000\U0010fffd", id="astral"),
    ],
)
def test_transform_literal_text_keeps_compiled_snapshot(body: str, value: str) -> None:
    sheet: Final = parse_xml(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:output method="text"/><xsl:template match="/">'
        + body.format(value=value)
        + "</xsl:template></xsl:stylesheet>"
    )
    convert: Final = Transform(sheet)
    template: Final = sheet.find("xsl:template")
    assert template is not None
    template.text = "changed"
    source: Final = parse_xml("<root/>")
    assert [convert(source), convert(source)] == [value, value]


def test_transform_literal_text_shared_compiled_snapshot() -> None:
    convert: Final = Transform(
        parse_xml(
            '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
            '<xsl:output method="text"/><xsl:param name="suffix"/><xsl:template match="/">'
            + "<xsl:text>literal 日本語</xsl:text>" * 512
            + '<xsl:value-of select="$suffix"/></xsl:template></xsl:stylesheet>'
        )
    )
    barrier: Final = Barrier(4)

    def run(index: int) -> str:
        source: Final = parse_xml("<root/>")
        barrier.wait()
        return convert(source, suffix=f"'{index}'")

    with ThreadPoolExecutor(max_workers=4) as executor:
        results: Final = list(executor.map(run, range(4)))

    assert results == [f"{'literal 日本語' * 512}{index}" for index in range(4)]
