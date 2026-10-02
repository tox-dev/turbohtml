"""A table whose rows sit under nested row groups renders every row without writing past the row buffer.

The parser keeps a table's row groups nested when it reads a fragment or XML, and the mutation API and ``parse_xml``
can nest them to any depth, so the renderers must size their row buffer for the real total, not two tree levels.
"""

from __future__ import annotations

import pytest

from turbohtml import Element, Markdown, Text, parse_fragment, parse_xml

_ROWS = 40
_DEPTH = 4


def _table(row_groups: int) -> Element:
    table = Element("table")
    group: Element = table
    for _ in range(row_groups):
        inner = Element("tbody")
        group.append(inner)
        group = inner
    for index in range(_ROWS):
        cell = Element("td")
        cell.append(Text(str(index)))
        row = Element("tr")
        row.append(cell)
        group.append(row)
    return table


@pytest.mark.parametrize(
    ("method", "argument"),
    [
        pytest.param("to_text", None, id="text"),
        pytest.param("to_markdown", None, id="markdown"),
        pytest.param("to_markdown", Markdown(tables=Markdown.Tables(pad=True)), id="markdown-padded"),
        pytest.param("to_markdown", Markdown(tables=Markdown.Tables(mode="strip")), id="markdown-strip"),
        pytest.param("to_annotated_text", {"td": ["cell"]}, id="annotated-text"),
    ],
)
def test_nested_row_groups_render_like_a_flat_table(method: str, argument: object) -> None:
    arguments = () if argument is None else (argument,)
    assert getattr(_table(_DEPTH), method)(*arguments) == getattr(_table(1), method)(*arguments)


def test_fragment_nested_row_groups_render_every_row() -> None:
    body = "".join(f"<tr><td>{index}</td></tr>" for index in range(_ROWS))
    fragment = parse_fragment("<table>" + "<tbody>" * _DEPTH + body + "</tbody>" * _DEPTH + "</table>", "div")
    assert fragment.to_text() == "\n".join(str(index) for index in range(_ROWS))


def test_parse_xml_nested_row_groups_keep_every_cell() -> None:
    body = "".join(f"<tr><td>{index}</td></tr>" for index in range(_ROWS))
    document = parse_xml("<table>" + "<tbody>" * _DEPTH + body + "</tbody>" * _DEPTH + "</table>")
    assert document.to_text() == "".join(str(index) for index in range(_ROWS))
