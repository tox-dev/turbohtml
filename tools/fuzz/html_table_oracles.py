"""Literal row groups expose matching parse and reparse insertion errors."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Final, cast

from turbohtml import Element, Node, Text, parse_fragment

if TYPE_CHECKING:
    import random
    from collections.abc import Callable


def html_table_generate(rng: random.Random) -> str:
    # Finite rows and terminal cells avoid recursive expansion.
    return f"{rng.choice(_CELLS)}:{rng.choice(_GROUPS)}:{rng.choice(_COUNTS)}:{rng.choice(tuple(_LEAVES))}"


def html_table_seeds() -> list[str]:
    # Cover row-group insertion without relying on random draws.
    return [
        f"{cell}:{group}:{count}:{leaf}"
        for cell in _CELLS
        for group in _GROUPS
        for count in _COUNTS
        for leaf in _LEAVES
    ]


def html_table_controls() -> dict[str, bool]:
    # Stable cell text does not prove correct grouping.
    return {
        "changed row group": html_table_check(
            "td:implied:1:plain", lambda node: node.serialize().replace("tbody", "thead")
        )
        is not None,
        "dropped row": html_table_check(
            "th:explicit:2:plain",
            lambda _node: (
                '<table id="z"><tbody><tr id="x"><th id="x">MiXeD</th><th id="y">MiXeD</th></tr></tbody></table>'
            ),
        )
        is not None,
        "encoded text": html_table_check(
            "td:implied:2:named", lambda node: node.serialize().replace("&amp;", "&amp;amp;")
        )
        is not None,
    }


def html_table_check(
    case: str,
    serialize: Callable[[Node], str] = Node.serialize,
    read: Callable[[str], Element] = parse_fragment,
) -> str | None:
    # Expect a tbody independently of whether source tags spell it out.
    if (match := _CASE.fullmatch(case)) is None:
        raise UnsupportedHtmlTableCaseError(case)
    cell, group, count, leaf = match.groups()
    encoded, decoded = _LEAVES[leaf]
    row_count: Final = int(count)
    row_ids = ("x", "y")
    rows: Final = "".join(
        f'<tr id="{identifier}"><{cell} id="x">{encoded}</{cell}><{cell} id="y">{encoded}</{cell}></tr>'
        for identifier in row_ids[:row_count]
    )
    source: Final = f'<table id="z">{f"<tbody>{rows}</tbody>" if group == "explicit" else rows}</table>'
    expected: Final = json.dumps([
        ("element", "html", "table", (("id", "z"),), 0),
        ("element", "html", "tbody", (), 1),
        *[
            record
            for identifier, position in zip(row_ids[:row_count], (3, 8)[:row_count], strict=True)
            for record in [
                ("element", "html", "tr", (("id", identifier),), 2),
                ("element", "html", cell, (("id", "x"),), position),
                ("text", "", decoded, (), position + 1),
                ("element", "html", cell, (("id", "y"),), position),
                ("text", "", decoded, (), position + 3),
            ]
        ],
    ])
    root: Final = read(source)
    if _snapshot(root) != expected:
        return "parsed table tree differs from literals"
    printed: Final = "".join(serialize(node) for node in root.children)
    reparsed: Final = read(printed)
    if _snapshot(reparsed) != expected:
        return "serialized table tree differs from literals"
    return (
        None
        if "".join(serialize(node) for node in reparsed.children) == printed
        else "table serialization changes on repeat"
    )


class UnsupportedHtmlTableCaseError(ValueError):
    # Separate invalid programs from failures of generated tables.
    ...


def _snapshot(root: Element) -> str:
    nodes: Final = [root, *root.descendants]
    records: Final[list[tuple[str, str, str, tuple[tuple[str, str | list[str] | None], ...], int]]] = []
    for node in nodes[1:]:
        parent: Final = nodes.index(cast("Element", node.parent))
        if isinstance(node, Element):
            records.append(("element", node.namespace.value, node.tag, tuple(sorted(node.attrs.items())), parent))
        elif isinstance(node, Text):
            records.append(("text", "", node.data, (), parent))
        else:
            records.append(("other", "", type(node).__name__, (), parent))
    return json.dumps(records)


_CASE: Final[re.Pattern[str]] = re.compile(r"(td|th):(explicit|implied):([12]):(plain|named|numeric)")
_CELLS: Final[tuple[str, ...]] = ("td", "th")
_GROUPS: Final[tuple[str, ...]] = ("explicit", "implied")
_COUNTS: Final[tuple[int, ...]] = (1, 2)
_LEAVES: Final[dict[str, tuple[str, str]]] = {
    "plain": ("MiXeD", "MiXeD"),
    "named": ("&amp;&lt;&gt;", "&<>"),
    "numeric": ("&#65;&#x42;&#67;", "ABC"),
}

__all__ = [
    "UnsupportedHtmlTableCaseError",
    "html_table_check",
    "html_table_controls",
    "html_table_generate",
    "html_table_seeds",
]
