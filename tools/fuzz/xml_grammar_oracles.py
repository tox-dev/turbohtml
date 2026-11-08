"""Finite XML productions distinguish literal and referenced whitespace."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Final

from turbohtml import Document, Element, Html, Node, Text, parse_xml

if TYPE_CHECKING:
    import random
    from collections.abc import Callable


def xml_literal_generate(rng: random.Random) -> str:
    return (
        f"{rng.choice(_PREFIXES)}:{rng.choice(_TAGS)}:{rng.choice(tuple(_QUOTES))}:"
        f"{rng.randint(1, 3)}:{rng.choice(tuple(_LEAVES))}"
    )


def xml_literal_seeds() -> list[str]:
    # Cover the finite productions without random sampling gaps.
    return [
        f"{prefix}:{tag}:{quote}:{count}:{leaf}"
        for prefix in _PREFIXES
        for tag in _TAGS
        for quote in _QUOTES
        for count in range(1, 4)
        for leaf in _LEAVES
    ]


def xml_literal_controls() -> dict[str, bool]:
    # Reject changes to names, bindings and referenced whitespace.
    return {
        "nested second child": xml_literal_check(
            "p:Leaf:double:2:plain",
            lambda _node: (
                '<Root xmlns:p="urn:p"><p:Leaf p:Key="Value" Key="Other">MiXeD'
                '<p:Leaf p:Key="Value" Key="Other">MiXeD</p:Leaf></p:Leaf></Root>'
            ),
        )
        is not None,
        "changed declaration": xml_literal_check(
            "p:Leaf:double:1:plain", lambda node: node.serialize(Html(xml=True)).replace("urn:p", "urn:wrong")
        )
        is not None,
        "changed qualified name": xml_literal_check(
            "p:Leaf:double:1:plain", lambda node: node.serialize(Html(xml=True)).replace("p:Leaf", "p:leaf")
        )
        is not None,
        "referenced whitespace folded": xml_literal_check(
            "p:Leaf:double:1:numeric",
            lambda _node: (
                '<Root xmlns:p="urn:p"><p:Leaf p:Key="a b c d" Key="Other">a&#9;b&#10;c&#13;d</p:Leaf></Root>'
            ),
        )
        is not None,
    }


def xml_literal_check(
    case: str,
    serialize: Callable[[Node], str] | None = None,
    read: Callable[[str], Document] = parse_xml,
) -> str | None:
    # Pin XML semantics independently of a serializer or parser agreement.
    if (match := _CASE.fullmatch(case)) is None:
        raise UnsupportedXmlLiteralCaseError(case)
    source, expected = _document(*match.groups())
    root: Final = read(source)
    if _snapshot(root) != expected:
        return "parsed XML tree differs from literals"
    printed: Final = _serialize(root, serialize)
    reparsed: Final = read(printed)
    if _snapshot(reparsed) != expected:
        return "serialized XML tree differs from literals"
    return None if _serialize(reparsed, serialize) == printed else "XML serialization changes on repeat"


class UnsupportedXmlLiteralCaseError(ValueError):
    # Separate malformed programs from failures of generated XML.
    ...


def _document(prefix: str, tag: str, quote: str, count: str, leaf: str) -> tuple[str, str]:
    encoded, decoded, encoded_attribute, decoded_attribute = _LEAVES[leaf]
    delimiter: Final = _QUOTES[quote]
    declaration: Final = f"xmlns:{prefix}"
    qualified: Final = f"{prefix}:{tag}"
    child: Final = (
        f"<{qualified} {prefix}:Key={delimiter}{encoded_attribute}{delimiter} Key={delimiter}Other{delimiter}>"
        f"{encoded}</{qualified}>"
    )
    source: Final = f'<Root {declaration}="urn:{prefix}">{child * int(count)}</Root>'
    return source, json.dumps([
        ("element", "Root", ((declaration, f"urn:{prefix}"),), "Document"),
        *[
            record
            for _ in range(int(count))
            for record in [
                ("element", qualified, (("Key", "Other"), (f"{prefix}:Key", decoded_attribute)), "Root"),
                ("text", decoded, qualified),
            ]
        ],
    ])


def _snapshot(root: Document) -> str:
    records: Final[
        list[tuple[str, str, str] | tuple[str, str, tuple[tuple[str, str | list[str] | None], ...], str]]
    ] = []
    for node in root.descendants:
        parent: Final = node.parent
        parent_tag: Final = parent.tag if isinstance(parent, Element) else "Document"
        if isinstance(node, Element):
            records.append(("element", node.tag, tuple(sorted(node.attrs.items())), parent_tag))
        elif isinstance(node, Text):
            records.append(("text", node.data, parent_tag))
        else:
            records.append(("other", type(node).__name__, parent_tag))
    return json.dumps(records)


def _serialize(root: Node, serialize: Callable[[Node], str] | None) -> str:
    return root.serialize(Html(xml=True)) if serialize is None else serialize(root)


_CASE: Final[re.Pattern[str]] = re.compile(r"(p|P):(Leaf|leaf):(single|double):([1-3]):(plain|escaped|literal|numeric)")
_PREFIXES: Final[tuple[str, ...]] = ("p", "P")
_TAGS: Final[tuple[str, ...]] = ("Leaf", "leaf")
_QUOTES: Final[dict[str, str]] = {"single": "'", "double": '"'}
_LEAVES: Final[dict[str, tuple[str, str, str, str]]] = {
    "plain": ("MiXeD", "MiXeD", "Value", "Value"),
    "escaped": ("&amp;&lt;&gt;", "&<>", "&quot;&apos;&amp;&lt;", "\"'&<"),
    "literal": ("a\tb\r\nc\rd", "a\tb\nc\nd", "a\tb\r\nc\rd", "a b c d"),
    "numeric": ("a&#9;b&#10;c&#13;d", "a\tb\nc\rd", "a&#9;b&#10;c&#13;d", "a\tb\nc\rd"),
}

__all__ = [
    "UnsupportedXmlLiteralCaseError",
    "xml_literal_check",
    "xml_literal_controls",
    "xml_literal_generate",
    "xml_literal_seeds",
]
