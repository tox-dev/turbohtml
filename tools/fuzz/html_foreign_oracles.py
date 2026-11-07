"""Literal SVG trees prevent matching parse and reparse errors from hiding changes."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Final, cast

from turbohtml import Element, Node, Text, parse_fragment

if TYPE_CHECKING:
    import random
    from collections.abc import Callable


def html_foreign_generate(rng: random.Random) -> str:
    # Finite productions cap tree depth and size.
    return (
        f"{rng.choice(_BOUNDARIES)}:{rng.choice(_IDENTIFIERS)}:"
        f"{rng.choice(tuple(_QUOTES))}:{rng.choice(tuple(_LEAVES))}"
    )


def html_foreign_seeds() -> list[str]:
    # Cover finite productions without relying on random draws.
    return [
        f"{boundary}:{identifier}:{quote}:{leaf}"
        for boundary in _BOUNDARIES
        for identifier in _IDENTIFIERS
        for quote in _QUOTES
        for leaf in _LEAVES
    ]


def html_foreign_controls() -> dict[str, bool]:
    # Expose matching errors in namespace, attachment and content.
    return {
        "HTML child became SVG": html_foreign_check(
            "foreignObject:x:double:plain", lambda node: node.serialize().replace("foreignObject", "g")
        )
        is not None,
        "SVG sibling moved inside integration point": html_foreign_check(
            "desc:x:double:plain",
            lambda _node: '<svg><desc><a id="x">MiXeD</a><g id="y"></g></desc></svg>',
        )
        is not None,
        "changed identifier": html_foreign_check(
            "title:x:double:plain", lambda node: node.serialize().replace('id="x"', 'id="y"')
        )
        is not None,
        "encoded text": html_foreign_check(
            "foreignObject:x:double:named", lambda node: node.serialize().replace("&amp;", "&amp;amp;")
        )
        is not None,
    }


def html_foreign_check(
    case: str,
    serialize: Callable[[Node], str] = Node.serialize,
    read: Callable[[str], Element] = parse_fragment,
) -> str | None:
    # Keep expected namespaces independent of parsing.
    if (match := _CASE.fullmatch(case)) is None:
        raise UnsupportedHtmlForeignCaseError(case)
    boundary, identifier, quote, leaf = match.groups()
    encoded, decoded = _LEAVES[leaf]
    sibling: Final = "y" if identifier == "x" else "x"
    delimiter: Final = _QUOTES[quote]
    source: Final = (
        f"<svg><{boundary}><a id={delimiter}{identifier}{delimiter}>{encoded}</a></{boundary}>"
        f"<g id={delimiter}{sibling}{delimiter}></g></svg>"
    )
    expected: Final = json.dumps([
        ("element", "svg", "svg", (), "html", "div"),
        ("element", "svg", boundary, (), "svg", "svg"),
        ("element", "html", "a", (("id", identifier),), "svg", boundary),
        ("text", "", decoded, (), "html", "a"),
        ("element", "svg", "g", (("id", sibling),), "svg", "svg"),
    ])
    root: Final = read(source)
    if _snapshot(root) != expected:
        return "parsed SVG integration tree differs from literals"
    printed: Final = "".join(serialize(node) for node in root.children)
    reparsed: Final = read(printed)
    if _snapshot(reparsed) != expected:
        return "serialized SVG integration tree differs from literals"
    return (
        None
        if "".join(serialize(node) for node in reparsed.children) == printed
        else "SVG integration serialization changes on repeat"
    )


class UnsupportedHtmlForeignCaseError(ValueError):
    # Separate invalid programs from failures of generated trees.
    ...


def _snapshot(root: Element) -> str:
    records: Final[list[tuple[str, str, str, tuple[tuple[str, str | list[str] | None], ...], str, str]]] = []
    for node in root.descendants:
        parent: Final = cast("Element", node.parent)
        if isinstance(node, Element):
            records.append((
                "element",
                node.namespace.value,
                node.tag,
                tuple(sorted(node.attrs.items())),
                parent.namespace.value,
                parent.tag,
            ))
        elif isinstance(node, Text):
            records.append(("text", "", node.data, (), parent.namespace.value, parent.tag))
        else:
            records.append(("other", "", type(node).__name__, (), parent.namespace.value, parent.tag))
    return json.dumps(records)


_CASE: Final = re.compile(r"(foreignObject|desc|title):(x|y):(single|double):(plain|named|numeric)")
_BOUNDARIES: Final[tuple[str, ...]] = ("foreignObject", "desc", "title")
_IDENTIFIERS: Final[tuple[str, ...]] = ("x", "y")
_QUOTES: Final[dict[str, str]] = {"single": "'", "double": '"'}
_LEAVES: Final[dict[str, tuple[str, str]]] = {
    "plain": ("MiXeD", "MiXeD"),
    "named": ("&amp;&lt;&gt;", "&<>"),
    "numeric": ("&#65;&#x42;&#67;", "ABC"),
}

__all__ = [
    "UnsupportedHtmlForeignCaseError",
    "html_foreign_check",
    "html_foreign_controls",
    "html_foreign_generate",
    "html_foreign_seeds",
]
