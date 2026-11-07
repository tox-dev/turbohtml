"""Finite XML islands pin lexical node kinds and attachment."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

from turbohtml import CData, Comment, Document, Element, Html, Node, Text, parse_xml

if TYPE_CHECKING:
    import random
    from collections.abc import Callable


def xml_island_generate(rng: random.Random) -> str:
    return f"{rng.choice(_MODES)}:{rng.choice(tuple(_COMMENTS))}:{rng.choice(tuple(_CDATA))}:{rng.randint(1, 3)}"


def xml_island_seeds() -> list[str]:
    # Cover the finite productions without sampling gaps.
    return [
        f"{mode}:{comment}:{cdata}:{count}"
        for mode in _MODES
        for comment in _COMMENTS
        for cdata in _CDATA
        for count in range(1, 4)
    ]


def xml_island_controls() -> dict[str, bool]:
    # Reject content-preserving kind loss and changed comment attachment.
    return {
        "CDATA converted to Text": xml_island_check(
            "inside:plain:plain:1", lambda _node: "<Root><!--note-->Value&lt;&amp;</Root>"
        )
        is not None,
        "comment moved into Root": xml_island_check(
            "outside:plain:plain:1",
            lambda _node: "<!--lead &amp;--><Root><!--note--><![CDATA[Value]]>&lt;&amp;<!--tail--></Root>",
        )
        is not None,
        "CDATA reference decoded": xml_island_check(
            "inside:plain:markup:1", lambda node: node.serialize(Html(xml=True)).replace("&amp;]]>", "&]]>")
        )
        is not None,
    }


def xml_island_check(
    case: str,
    serialize: Callable[[Node], str] | None = None,
    read: Callable[[str], Document] = parse_xml,
) -> str | None:
    # Keep literal node kinds independent of round-trip agreement.
    if (match := _CASE.fullmatch(case)) is None:
        raise UnsupportedXmlIslandCaseError(case)
    source, expected = _document(*match.groups())
    root: Final = read(source)
    if _snapshot(root) != expected:
        return "parsed XML islands differ from literals"
    printed: Final = _serialize(root, serialize)
    reparsed: Final = read(printed)
    if _snapshot(reparsed) != expected:
        return "serialized XML islands differ from literals"
    return None if _serialize(reparsed, serialize) == printed else "XML island serialization changes on repeat"


class UnsupportedXmlIslandCaseError(ValueError):
    # Separate malformed programs from failures of generated XML.
    ...


def _document(mode: str, comment: str, cdata: str, count: str) -> tuple[str, tuple[tuple[str, str, str], ...]]:
    encoded_comment, decoded_comment = _COMMENTS[comment]
    encoded_cdata, decoded_cdata = _CDATA[cdata]
    group: Final = f"<!--{encoded_comment}--><![CDATA[{encoded_cdata}]]>&lt;&amp;"
    prefix: Final = "<!--lead &amp;-->" if mode == "outside" else ""
    suffix: Final = "<!--tail-->" if mode == "outside" else ""
    return f"{prefix}<Root>{group * int(count)}</Root>{suffix}", (
        *([("Comment", "lead &amp;", "Document")] if mode == "outside" else []),
        ("Element", "Root", "Document"),
        *[
            record
            for _ in range(int(count))
            for record in [
                ("Comment", decoded_comment, "Root"),
                ("CData", decoded_cdata, "Root"),
                ("Text", "<&", "Root"),
            ]
        ],
        *([("Comment", "tail", "Document")] if mode == "outside" else []),
    )


def _snapshot(root: Document) -> tuple[tuple[str, str, str], ...]:
    records: Final[list[tuple[str, str, str]]] = []
    for node in root.descendants:
        parent: Final = node.parent
        parent_tag: Final = parent.tag if isinstance(parent, Element) else "Document"
        if isinstance(node, Element):
            records.append(("Element", node.tag, parent_tag))
        elif isinstance(node, (Comment, CData, Text)):
            records.append((type(node).__name__, node.data, parent_tag))
        else:
            records.append(("other", type(node).__name__, parent_tag))
    return tuple(records)


def _serialize(root: Node, serialize: Callable[[Node], str] | None) -> str:
    return root.serialize(Html(xml=True)) if serialize is None else serialize(root)


_CASE: Final = re.compile(r"(inside|outside):(plain|markup|lines):(plain|markup|lines):([1-3])")
_MODES: Final = ("inside", "outside")
_COMMENTS: Final = {"plain": ("note", "note"), "markup": ("<x> &amp;", "<x> &amp;"), "lines": ("a\r\nb\rc", "a\nb\nc")}
_CDATA: Final = {"plain": ("Value", "Value"), "markup": ("<x>&amp;", "<x>&amp;"), "lines": ("a\r\nb\rc", "a\nb\nc")}

__all__ = [
    "UnsupportedXmlIslandCaseError",
    "xml_island_check",
    "xml_island_controls",
    "xml_island_generate",
    "xml_island_seeds",
]
