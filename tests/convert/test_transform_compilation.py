"""Construction owns stylesheet state; calls own source evaluation state."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Final

import pytest

import turbohtml
from turbohtml.transform import Transform

if TYPE_CHECKING:
    from pathlib import Path

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
