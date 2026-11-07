from __future__ import annotations

from typing import Final

import pytest

from turbohtml import Element, Markdown, Text, parse_fragment, parse_xml

_ROWS: Final[int] = 40
_DEPTH: Final[int] = 4


@pytest.mark.parametrize(
    ("method", "arguments"),
    [
        pytest.param("to_text", (), id="text"),
        pytest.param("to_markdown", (), id="markdown"),
        pytest.param("to_markdown", (Markdown(tables=Markdown.Tables(pad=True)),), id="markdown-padded"),
        pytest.param("to_markdown", (Markdown(tables=Markdown.Tables(mode="strip")),), id="markdown-strip"),
        pytest.param("to_annotated_text", ({"td": ["cell"]},), id="annotated-text"),
    ],
)
def test_nested_row_groups_render_like_a_flat_table(method: str, arguments: tuple[object, ...]) -> None:
    assert getattr(_table(_DEPTH), method)(*arguments) == getattr(_table(1), method)(*arguments)


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


def test_fragment_nested_row_groups_render_every_row() -> None:
    body = "".join(f"<tr><td>{index}</td></tr>" for index in range(_ROWS))
    fragment = parse_fragment(f"<table>{'<tbody>' * _DEPTH}{body}{'</tbody>' * _DEPTH}</table>", "div")
    assert fragment.to_text() == "\n".join(str(index) for index in range(_ROWS))


def test_parse_xml_nested_row_groups_keep_every_cell() -> None:
    body = "".join(f"<tr><td>{index}</td></tr>" for index in range(_ROWS))
    document = parse_xml(f"<table>{'<tbody>' * _DEPTH}{body}{'</tbody>' * _DEPTH}</table>")
    assert document.to_text() == "".join(str(index) for index in range(_ROWS))
