"""Literal parent positions expose changes between same-tag nested lists."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Final, cast

from turbohtml import Element, Node, Text, parse_fragment

if TYPE_CHECKING:
    import random
    from collections.abc import Callable


def html_list_generate(rng: random.Random) -> str:
    # Finite shapes bound depth without recursive expansion.
    return f"{rng.choice(_TAGS)}:{rng.choice(_SHAPES)}:{rng.choice(tuple(_ENDINGS))}:{rng.choice(tuple(_LEAVES))}"


def html_list_seeds() -> list[str]:
    # Exhaust productions without depending on random draws.
    return [
        f"{tag}:{shape}:{ending}:{leaf}"
        for tag in _TAGS
        for shape in _SHAPES
        for ending in _ENDINGS
        for leaf in _LEAVES
    ]


def html_list_controls() -> dict[str, bool]:
    # Detect attachment changes even when the parent tag stays equal.
    return {
        "inner item became outer sibling": html_list_check(
            "ul:nested:explicit:plain",
            lambda _node: (
                '<ul id="z"><li id="x">MiXeD<ul id="z"><li id="x">MiXeD</li></ul></li>'
                '<li id="y">MiXeD</li><li id="y">MiXeD</li></ul>'
            ),
        )
        is not None,
        "dropped sibling": html_list_check(
            "ol:flat:explicit:plain", lambda _node: '<ol id="z"><li id="x">MiXeD</li></ol>'
        )
        is not None,
        "encoded text": html_list_check(
            "ul:nested:implied:named", lambda node: node.serialize().replace("&amp;", "&amp;amp;")
        )
        is not None,
    }


def html_list_check(
    case: str,
    serialize: Callable[[Node], str] = Node.serialize,
    read: Callable[[str], Element] = parse_fragment,
) -> str | None:
    # Keep list attachment expectations independent of parsing.
    if (match := _CASE.fullmatch(case)) is None:
        raise UnsupportedHtmlListCaseError(case)
    tag, shape, ending, leaf = match.groups()
    encoded, decoded = _LEAVES[leaf]
    close: Final = _ENDINGS[ending]
    nested: Final = (
        f'<{tag} id="z"><li id="x">{encoded}{close}<li id="y">{encoded}{close}</{tag}>' if shape == "nested" else ""
    )
    source: Final = f'<{tag} id="z"><li id="x">{encoded}{nested}{close}<li id="y">{encoded}{close}</{tag}>'
    expected: Final = json.dumps([
        ("element", "html", tag, (("id", "z"),), 0),
        ("element", "html", "li", (("id", "x"),), 1),
        ("text", "", decoded, (), 2),
        *(
            [
                ("element", "html", tag, (("id", "z"),), 2),
                ("element", "html", "li", (("id", "x"),), 4),
                ("text", "", decoded, (), 5),
                ("element", "html", "li", (("id", "y"),), 4),
                ("text", "", decoded, (), 7),
            ]
            if shape == "nested"
            else []
        ),
        ("element", "html", "li", (("id", "y"),), 1),
        ("text", "", decoded, (), 9 if shape == "nested" else 4),
    ])
    root: Final = read(source)
    if _snapshot(root) != expected:
        return "parsed list tree differs from literals"
    printed: Final = "".join(serialize(node) for node in root.children)
    reparsed: Final = read(printed)
    if _snapshot(reparsed) != expected:
        return "serialized list tree differs from literals"
    return (
        None
        if "".join(serialize(node) for node in reparsed.children) == printed
        else "list serialization changes on repeat"
    )


class UnsupportedHtmlListCaseError(ValueError):
    # Separate invalid programs from failures of generated lists.
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


_CASE: Final[re.Pattern[str]] = re.compile(
    r"""
    (?P<tag>ul|ol):                          # the list element
    (?P<shape>flat|nested):                  # whether the items are nested
    (?P<ending>explicit|implied):            # the word explicit or implied
    (?P<leaf>plain|named|numeric)            # the kind of character reference
    """,
    re.VERBOSE,
)
_TAGS: Final[tuple[str, ...]] = ("ul", "ol")
_SHAPES: Final[tuple[str, ...]] = ("flat", "nested")
_ENDINGS: Final[dict[str, str]] = {"explicit": "</li>", "implied": ""}
_LEAVES: Final[dict[str, tuple[str, str]]] = {
    "plain": ("MiXeD", "MiXeD"),
    "named": ("&amp;&lt;&gt;", "&<>"),
    "numeric": ("&#65;&#x42;&#67;", "ABC"),
}

__all__ = [
    "UnsupportedHtmlListCaseError",
    "html_list_check",
    "html_list_controls",
    "html_list_generate",
    "html_list_seeds",
]
