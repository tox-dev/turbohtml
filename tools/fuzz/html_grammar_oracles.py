"""Finite sibling productions pin parsed multiplicity and decoded contents."""

from __future__ import annotations

import json
import re
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, cast

from turbohtml import Element, Node, Text, parse_fragment

if TYPE_CHECKING:
    import random
    from collections.abc import Callable


def html_sibling_generate(rng: random.Random) -> str:
    # Bound siblings and choose terminal text without recursion.
    return (
        f"{rng.choice(_TAGS)}:{rng.choice(_ATTRIBUTES)}:{rng.choice(tuple(_QUOTES))}:"
        f"{rng.randint(1, 4)}:{rng.choice(tuple(_LEAVES))}"
    )


def html_sibling_seeds() -> list[str]:
    # Sweep structural, attribute and terminal productions.
    return [
        f"{tag}:{attribute}:{quote}:{count}:{leaf}"
        for tag in _TAGS
        for attribute in _ATTRIBUTES
        for quote in _QUOTES
        for count in range(1, 5)
        for leaf in _LEAVES
    ]


def html_sibling_controls() -> dict[str, bool]:
    # Require multiplicity and literal attribute/text decoding.
    return {
        "dropped identical sibling": html_sibling_check(
            "span:title:bare:2:empty", lambda node: "" if node.previous_sibling is None else node.serialize()
        )
        is not None,
        "changed attribute": html_sibling_check("span:title:bare:1:empty", lambda _node: '<span title="wrong"></span>')
        is not None,
        "encoded text": html_sibling_check(
            "span:title:bare:1:named", lambda node: node.serialize().replace("&amp;", "&amp;amp;")
        )
        is not None,
    }


def html_sibling_check(
    case: str,
    serialize: Callable[[Node], str] = Node.serialize,
    read: Callable[[str], Element] = parse_fragment,
) -> str | None:
    # Compare generated literals before and after serialization.
    if (match := _CASE.fullmatch(case)) is None:
        raise UnsupportedHtmlSiblingCaseError(case)
    tag, attribute, quote, count, leaf = match.groups()
    encoded, decoded, encoded_attribute, decoded_attribute = _LEAVES[leaf]
    delimiter: Final = _QUOTES[quote]
    source: Final = f"<{tag} {attribute}={delimiter}{encoded_attribute}{delimiter}>{encoded}</{tag}>" * int(count)
    expected: Final = json.dumps([
        record
        for _ in range(int(count))
        for record in [
            ("element", tag, ((attribute, decoded_attribute),), "div"),
            *([("text", decoded, tag)] if decoded else []),
        ]
    ])
    root: Final = read(source)
    if _snapshot(root) != expected:
        return "parsed sibling tree differs from literals"
    printed: Final = "".join(serialize(node) for node in root.children)
    reparsed: Final = read(printed)
    if _snapshot(reparsed) != expected:
        return "serialized sibling tree differs from literals"
    return (
        None
        if "".join(serialize(node) for node in reparsed.children) == printed
        else "sibling serialization changes on repeat"
    )


class UnsupportedHtmlSiblingCaseError(ValueError):
    # Separate malformed programs from failures of valid generated trees.
    ...


def _snapshot(root: Element) -> str:
    records: Final[
        list[tuple[str, str, str] | tuple[str, str, tuple[tuple[str, str | list[str] | None], ...], str]]
    ] = []
    for node in root.descendants:
        parent: Element = cast("Element", node.parent)
        if isinstance(node, Element):
            records.append(("element", node.tag, tuple(sorted(node.attrs.items())), parent.tag))
        elif isinstance(node, Text):
            records.append(("text", node.data, parent.tag))
        else:
            records.append(("other", type(node).__name__, parent.tag))
    return json.dumps(records)


_CASE: Final[re.Pattern[str]] = re.compile(
    r"""
    (?P<tag>span|em|code):                      # the element holding the attribute
    (?P<attribute>title|data-x):                # the attribute name
    (?P<quote>single|double|bare):              # the quote style of the value
    (?P<count>[1-4]):                           # a digit from 1 to 4
    (?P<leaf>empty|plain|named|numeric)         # the kind of character reference
    """,
    re.VERBOSE,
)
_TAGS: Final[tuple[str, ...]] = ("span", "em", "code")
_ATTRIBUTES: Final[tuple[str, ...]] = ("title", "data-x")
_QUOTES: Final[MappingProxyType[str, str]] = MappingProxyType({"single": "'", "double": '"', "bare": ""})
_LEAVES: Final[MappingProxyType[str, tuple[str, str, str, str]]] = MappingProxyType({
    "empty": ("", "", "Token", "Token"),
    "plain": ("MiXeD", "MiXeD", "Token", "Token"),
    "named": ("&amp;&lt;&gt;", "&<>", "&quot;&apos;&amp;", "\"'&"),
    "numeric": ("&#65;&#x42;&#67;", "ABC", "&#x26;&#34;", '&"'),
})

__all__ = [
    "UnsupportedHtmlSiblingCaseError",
    "html_sibling_check",
    "html_sibling_controls",
    "html_sibling_generate",
    "html_sibling_seeds",
]
