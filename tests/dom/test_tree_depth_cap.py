"""The tree builder caps element nesting so pathologically deep input cannot overflow
the C stack or cost O(depth^2), and the recursive tree walks carry their own backstop
for a tree built past that cap through the mutation API."""

from __future__ import annotations

import copy

import pytest

from turbohtml import Document, Element, Text, parse
from turbohtml.clean import sanitize

# Well above the 512 parse cap and the 1024 walk-cap, small enough that an unguarded
# recursive walk would already overflow an ASan stack: the fuzzer aborted at 20000.
_PATHOLOGICAL = 20000


def _max_element_depth(node: Element | Document) -> int:
    deepest = 0
    stack: list[tuple[object, int]] = [(node, 0)]
    while stack:
        current, depth = stack.pop()
        here = depth + 1 if isinstance(current, Element) else depth
        deepest = max(deepest, here)
        stack.extend((child, here) for child in getattr(current, "children", []) or [])
    return deepest


def _nested(tag: str, levels: int) -> Element:
    root = Element(tag)
    node = root
    for _ in range(levels):
        child = Element(tag)
        node.append(child)
        node = child
    node.append(Text("X"))
    return root


def test_deeply_nested_parse_caps_element_depth() -> None:
    doc = parse("<div>" * _PATHOLOGICAL)
    assert _max_element_depth(doc) <= 520  # bounded near the 512 cap, not ~20000


def test_deeply_nested_parse_keeps_every_element() -> None:
    doc = parse("<div>" * _PATHOLOGICAL)
    assert len(doc.find_all("div")) == _PATHOLOGICAL  # capped elements survive as siblings


def test_capped_parse_round_trips_through_serialization() -> None:
    doc = parse("<div>" * _PATHOLOGICAL)
    assert doc.html.count("<div>") == _PATHOLOGICAL
    assert doc.html.count("</div>") == _PATHOLOGICAL


def test_sanitize_of_deeply_nested_input_does_not_overflow() -> None:
    result = sanitize("<div>" * _PATHOLOGICAL)  # F1 repro: parse + recursive sanitize walk
    assert result.count("&lt;div&gt;") == _PATHOLOGICAL  # div is escaped, not kept, but every one survives


@pytest.mark.parametrize("levels", [pytest.param(400, id="under-cap"), pytest.param(511, id="at-cap")])
def test_nesting_below_the_cap_is_left_untouched(levels: int) -> None:
    doc = parse("<div>" * levels)
    assert _max_element_depth(doc) == levels + 2  # html + body + every div, fully nested


def test_text_walk_backstops_a_tree_past_the_cap() -> None:
    assert not _nested("div", 3000).text  # walk stops before the deepest text, no overflow


def test_text_walk_reads_a_tree_within_the_cap() -> None:
    assert _nested("div", 400).text == "X"  # the backstop never fires below the cap


@pytest.mark.parametrize("tag", [pytest.param("div", id="block"), pytest.param("b", id="inline")])
def test_markdown_walk_backstops_a_tree_past_the_cap(tag: str) -> None:
    assert not _nested(tag, 3000).to_markdown()  # both block and inline recursion are bounded


def test_markdown_walk_renders_a_tree_within_the_cap() -> None:
    assert "X" in _nested("b", 400).to_markdown()  # the backstop never fires below the cap


def test_clone_walk_backstops_a_tree_past_the_cap() -> None:
    clone = copy.deepcopy(_nested("div", 3000))
    assert not clone.text  # the copy is truncated rather than recursing into the deep source


def test_clone_walk_copies_a_tree_within_the_cap() -> None:
    clone = copy.deepcopy(_nested("div", 400))
    assert clone.text == "X"


def test_normalize_walk_backstops_a_tree_past_the_cap() -> None:
    root = _nested("div", 3000)
    root.normalize()  # must not overflow walking the deep tree
    assert isinstance(root, Element)


def test_normalize_walk_merges_text_within_the_cap() -> None:
    element = Element("div")
    element.append(Text("a"))
    element.append(Text("b"))
    element.normalize()
    assert element.text == "ab"
