"""Benchmark JustHTML only where its public semantics match the shared operation."""

from __future__ import annotations

import functools
from typing import Final, cast

from justhtml import Element, JustHTML, Node, matches
from justhtml.parser.context import FragmentContext
from justhtml.transforms import Linkify

from bench.timing import Mutating

# The orchestrator discovers REQUIREMENTS and OPERATIONS without importing competitor dependencies.
REQUIREMENTS = ("justhtml>=3.11",)

_STRIP_SELECTOR: Final[str] = "code, a, q"


def parse(text: str) -> None:
    JustHTML(text, sanitize=False)


def fragment(text: str) -> None:
    JustHTML(text, sanitize=False, fragment_context=FragmentContext("tbody"))


def find(text: str) -> None:
    _parsed(text).query("a")


def select(text: str) -> None:
    _parsed(text).query("div a[href]")


def match(text: str) -> None:
    for anchor in _elements(_parsed(text), "a"):
        matches(anchor, "div a[href]")


def text_content(text: str) -> None:
    _parsed(text).to_text(separator="", strip=False)


def serialize(text: str) -> None:
    _parsed(text).to_html(pretty=False)


def navigate(text: str) -> None:
    stack = [_parsed(text)]
    while stack:
        node = stack.pop()
        if node.children:
            stack.extend(reversed(node.children))


def extract_attr(text: str) -> None:
    for anchor in _elements(_parsed(text), "a"):
        anchor.attrs.get("href")


def extract_text(text: str) -> None:
    for anchor in _elements(_parsed(text), "a"):
        anchor.to_text(separator="", strip=False)


def class_edit(text: str) -> None:
    for anchor in _elements(_parsed(text), "a"):
        original = anchor.attrs.get("class")
        anchor.attrs["class"] = f"{original or ''} seen".strip()
        if original is None:
            del anchor.attrs["class"]
        else:
            anchor.attrs["class"] = original


def strip_remove(text: str) -> None:
    root = _fresh(text)
    for node in _elements(root, _STRIP_SELECTOR):
        if node.parent is not None:
            node.parent.remove_child(node)
    root.to_html(pretty=False)


def strip_tags(text: str) -> None:
    root = _fresh(text)
    for node in reversed(_elements(root, _STRIP_SELECTOR)):
        if (parent := node.parent) is not None:
            for child in list(node.children):
                parent.insert_before(child, node)
            parent.remove_child(node)
    root.to_html(pretty=False)


def sanitize(text: str) -> None:
    JustHTML(text).root.to_html(pretty=False)


def linkify(text: str) -> None:
    JustHTML(text, sanitize=False, transforms=[Linkify()]).root.to_html(pretty=False)


def edit(root: Node) -> None:
    for anchor in _elements(root, "a"):
        anchor.attrs["rel"] = "nofollow"


@functools.cache
def _parsed(text: str) -> Node:
    return JustHTML(text, sanitize=False).root


def _fresh(text: str) -> Node:
    return JustHTML(text, sanitize=False).root


def _elements(root: Node, selector: str) -> list[Element]:
    return cast("list[Element]", root.query(selector))


OPERATIONS = {
    "parse": (parse, "JustHTML"),
    "fragment": (fragment, "JustHTML"),
    "find": (find, "JustHTML"),
    "select": (select, "JustHTML"),
    "match": (match, "JustHTML"),
    "text-content": (text_content, "JustHTML"),
    "serialize": (serialize, "JustHTML"),
    "navigate": (navigate, "JustHTML"),
    "extract-attr": (extract_attr, "JustHTML"),
    "extract-text": (extract_text, "JustHTML"),
    "class-edit": (class_edit, "JustHTML"),
    "strip-remove": (strip_remove, "JustHTML"),
    "strip-tags": (strip_tags, "JustHTML"),
    "sanitize": (sanitize, "JustHTML"),
    "linkify": (linkify, "JustHTML"),
    "edit": (Mutating(_fresh, edit), "JustHTML"),
}

__all__ = ["OPERATIONS", "REQUIREMENTS"]
