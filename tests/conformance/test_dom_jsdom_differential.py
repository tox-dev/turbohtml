"""Differential validation of turbohtml's browser-JS DOM APIs against jsdom.

The authoritative suites for Range/StaticRange (#552), TreeWalker/NodeIterator (#550), and the Shadow DOM tree model
(#553/#549) are the WPT ``dom/ranges``, ``dom/traversal``, and ``shadow-dom`` directories -- testharness.js browser
tests that assert against a live DOM and cannot run against a Python library. jsdom passes almost all of those WPT
suites, so it is the cross-language reference: the same operation sequence is replayed through jsdom (the Node runner
``tools/bench/node/dom_differential_runner.js``) and through turbohtml, and the two results must agree byte- and
structure-exact. The op vocabulary is authored once and mirrored line-for-line on both sides; a divergence is a real
disagreement, never a runner asymmetry. Node addressing is a child-index path from ``documentElement`` -- both parsers
build the same WHATWG tree (the premise ``test_treebuilder_differential`` already pins), so the paths line up.

Two boundaries on what jsdom can oracle, documented rather than silently skipped:

- Declarative Shadow DOM (``<template shadowrootmode>``, #549) is unsupported by jsdom 29: it exposes neither
  ``Document.parseHTMLUnsafe`` nor ``Element.setHTMLUnsafe``, and ``new JSDOM`` leaves the ``<template>`` in the light
  tree. jsdom is the non-conformant party here (it predates the DOM Parsing spec's DSD algorithm), so those cases are
  validated against a spec-literal oracle derived from the WHATWG "attach a shadow root" steps, not against jsdom.
- The full WPT trees are ~1.5 GB checked out; vendoring them as a submodule would blow the ``git submodule update``
  scope the coverage/CI gates depend on staying fast (see the datasets-as-submodule note). The corpus below instead
  encodes the concrete input shapes and operation sequences those WPT tests exercise, diffed against the same oracle
  jsdom that passes them.

``MutationObserver`` (#554, "synchronous mutation-callback tables") is still open and exposes no public API, so there
is nothing to diff; it is out of scope here.
"""

from __future__ import annotations

import json
import shutil
import subprocess  # ruff:ignore[suspicious-subprocess-import]
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

import pytest

from turbohtml import (
    Comment,
    Document,
    Element,
    NodeFilter,
    NodeIterator,
    Range,
    Text,
    TreeWalker,
    parse,
    parse_fragment,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from turbohtml import Node

_NODE = shutil.which("node")
_RUNNER = Path(__file__).parents[2] / "tools" / "bench" / "node" / "dom_differential_runner.js"
_JSDOM = (_RUNNER.parent / "node_modules" / "jsdom").is_dir()

pytestmark = pytest.mark.skipif(_NODE is None or not _JSDOM, reason="node with jsdom (tools/bench/node) not installed")


def _desc(node: Node | None) -> str:
    if node is None:
        return "null"
    if isinstance(node, Element):
        ident = node.attr("id")
        slot = node.attr("slot")
        return f"E[{node.tag}{'#' + ident if ident else ''}{'@' + slot if slot else ''}]"
    if isinstance(node, Text):
        return f"T[{node.data}]"
    if isinstance(node, Comment):
        return f"C[{node.data}]"
    if isinstance(node, Document):
        return "DOC"
    return type(node).__name__


def _node_at(root: Node, path: list[int]) -> Node:
    node = root
    for index in path:
        node = node.children[index]
    return node


def _frag_html(frag: Node) -> str:
    return frag.html


def _tag_verdict(node: Node, tag: str, hit: int) -> int:
    return hit if isinstance(node, Element) and node.tag == tag else NodeFilter.FILTER_ACCEPT


def _reject_section_skip_p(node: Node) -> int:
    if isinstance(node, Element) and node.tag == "section":
        return NodeFilter.FILTER_REJECT
    if isinstance(node, Element) and node.tag == "p":
        return NodeFilter.FILTER_SKIP
    return NodeFilter.FILTER_ACCEPT


_FILTERS: dict[str, Callable[[Node], int]] = {
    "reject_nav": lambda node: _tag_verdict(node, "nav", NodeFilter.FILTER_REJECT),
    "skip_nav": lambda node: _tag_verdict(node, "nav", NodeFilter.FILTER_SKIP),
    "reject_div": lambda node: _tag_verdict(node, "div", NodeFilter.FILTER_REJECT),
    "skip_div": lambda node: _tag_verdict(node, "div", NodeFilter.FILTER_SKIP),
    "reject_ul": lambda node: _tag_verdict(node, "ul", NodeFilter.FILTER_REJECT),
    "skip_span": lambda node: _tag_verdict(node, "span", NodeFilter.FILTER_SKIP),
    "only_a": lambda node: (
        NodeFilter.FILTER_ACCEPT if isinstance(node, Element) and node.tag == "a" else NodeFilter.FILTER_SKIP
    ),
    "reject_section_skip_p": _reject_section_skip_p,
}


def _make_node(spec: dict[str, Any]) -> Node:
    if "text" in spec:
        return Text(spec["text"])
    element = Element(spec["tag"])
    if "html" in spec:
        element.set_inner_html(spec["html"])
    return element


class _RangeCall(NamedTuple):
    live: Range
    root: Node
    kase: dict[str, Any]


def _boundaries(range_obj: Range) -> dict[str, Any]:
    return {
        "start": f"{_desc(range_obj.start_container)}:{range_obj.start_offset}",
        "end": f"{_desc(range_obj.end_container)}:{range_obj.end_offset}",
    }


def _probe_boundaries(call: _RangeCall) -> dict[str, Any]:
    return {
        "collapsed": call.live.collapsed,
        "ancestor": _desc(call.live.common_ancestor_container),
        **_boundaries(call.live),
    }


def _probe_clone(call: _RangeCall) -> dict[str, Any]:
    return {"frag": _frag_html(call.live.clone_contents()), "doc": call.root.html}


def _probe_extract(call: _RangeCall) -> dict[str, Any]:
    frag = _frag_html(call.live.extract_contents())
    return {"frag": frag, "doc": call.root.html, "collapsed": call.live.collapsed}


def _probe_delete(call: _RangeCall) -> dict[str, Any]:
    call.live.delete_contents()
    return {"doc": call.root.html, "collapsed": call.live.collapsed}


def _probe_insert(call: _RangeCall) -> dict[str, Any]:
    call.live.insert_node(_make_node(call.kase["node"]))
    return {"doc": call.root.html}


def _probe_surround(call: _RangeCall) -> dict[str, Any]:
    call.live.surround_contents(_make_node(call.kase["wrapper"]))
    return {"doc": call.root.html}


def _probe_compare_boundary(call: _RangeCall) -> dict[str, Any]:
    other = Range(_node_at(call.root, call.kase["other_start"][0]), call.kase["other_start"][1])
    other.set_end(_node_at(call.root, call.kase["other_end"][0]), call.kase["other_end"][1])
    return {"value": call.live.compare_boundary_points(call.kase["how"], other)}


def _probe_compare_point(call: _RangeCall) -> dict[str, Any]:
    return {"value": call.live.compare_point(_node_at(call.root, call.kase["point"][0]), call.kase["point"][1])}


def _probe_point_in_range(call: _RangeCall) -> dict[str, Any]:
    return {"value": call.live.is_point_in_range(_node_at(call.root, call.kase["point"][0]), call.kase["point"][1])}


def _probe_intersects(call: _RangeCall) -> dict[str, Any]:
    return {"value": call.live.intersects_node(_node_at(call.root, call.kase["node_path"]))}


def _probe_select_node(call: _RangeCall) -> dict[str, Any]:
    target = Range(call.root)
    target.select_node(_node_at(call.root, call.kase["node_path"]))
    return _boundaries(target)


def _probe_select_node_contents(call: _RangeCall) -> dict[str, Any]:
    target = Range(call.root)
    target.select_node_contents(_node_at(call.root, call.kase["node_path"]))
    return _boundaries(target)


_RANGE_PROBES: dict[str, Callable[[_RangeCall], dict[str, Any]]] = {
    "boundaries": _probe_boundaries,
    "clone": _probe_clone,
    "extract": _probe_extract,
    "delete": _probe_delete,
    "insert": _probe_insert,
    "surround": _probe_surround,
    "compare_boundary": _probe_compare_boundary,
    "compare_point": _probe_compare_point,
    "point_in_range": _probe_point_in_range,
    "intersects": _probe_intersects,
    "select_node": _probe_select_node,
    "select_node_contents": _probe_select_node_contents,
}


def _run_range(kase: dict[str, Any]) -> dict[str, Any]:
    document = parse(kase["html"])
    root = document.root
    assert root is not None
    live = Range(_node_at(root, kase["start"][0]), kase["start"][1])
    live.set_end(_node_at(root, kase["end"][0]), kase["end"][1])
    return _RANGE_PROBES[kase["probe"]](_RangeCall(live, root, kase))


def _run_traversal(kase: dict[str, Any]) -> dict[str, Any]:
    document = parse(kase["html"])
    assert document.root is not None
    root = _node_at(document.root, kase["root"])
    node_filter = _FILTERS[kase["filter"]] if kase.get("filter") else None
    if kase["kind"] == "walker":
        walker = TreeWalker(root, kase["what"], node_filter)
        return {"nodes": _run_walker(walker, kase["probe"])}
    iterator = NodeIterator(root, kase["what"], node_filter)
    return {"nodes": _run_iterator(iterator, kase["probe"])}


def _run_walker(walker: TreeWalker, probe: str) -> list[str]:
    out: list[str] = []
    if probe == "forward":
        out.extend(_desc(node) for node in iter(walker.next_node, None))
    elif probe == "backward":
        while walker.next_node() is not None:
            pass
        out.extend(_desc(node) for node in iter(walker.previous_node, None))
    elif probe == "children":
        node = walker.first_child()
        while node is not None:
            out.append(_desc(node))
            node = walker.next_sibling()
    elif probe == "last_child_parents":
        node = walker.last_child()
        while node is not None:
            out.append(_desc(node))
            node = walker.parent_node()
    return out


def _run_iterator(iterator: NodeIterator, probe: str) -> list[str]:
    out: list[str] = []
    if probe == "forward":
        out.extend(_desc(node) for node in iter(iterator.next_node, None))
    elif probe == "backward":
        while iterator.next_node() is not None:
            pass
        out.extend(_desc(node) for node in iter(iterator.previous_node, None))
    return out


def _run_shadow(kase: dict[str, Any]) -> dict[str, Any]:
    host = parse_fragment(kase["host_html"]).children[0]
    assert isinstance(host, Element)
    shadow = host.attach_shadow(kase["mode"])
    shadow.set_inner_html(kase["shadow_html"])
    if kase["probe"] == "attach_mode":
        return {"reachable": host.shadow_root is not None, "mode": shadow.mode}
    slots: dict[str, Any] = {}
    for name, selector in kase["slots"].items():
        slot = shadow.select_one(selector)
        assert slot is not None
        slots[name] = {
            "nodes": [_desc(node) for node in slot.assigned_nodes()],
            "elements": [_desc(node) for node in slot.assigned_elements()],
            "flatten": [_desc(node) for node in slot.assigned_nodes(flatten=True)],
        }
    children = [_desc(child.assigned_slot) if child.assigned_slot is not None else "null" for child in host.children]
    return {"slots": slots, "children": children}


def _run_turbo(kase: dict[str, Any]) -> dict[str, Any]:
    if kase["feature"] == "range":
        return _run_range(kase)
    if kase["feature"] == "traversal":
        return _run_traversal(kase)
    return _run_shadow(kase)


_UL = "<ul><li>a</li><li>b</li><li>c</li></ul>"
_P = "<p>Hello brave World</p>"
_SPLIT = "<div><p>onetwo</p><p>threefour</p></div>"
_MIXED = "<div id=root><h2>t</h2><p>a<b>bb</b>c</p><nav><a>x</a></nav><span>s</span></div>"
_SECT = "<section id=s><p>x</p><article><p>y</p><span>z</span></article></section>"

_UL_PATH = [1, 0]
_UL_A = [1, 0, 0]
_P_TEXT = [1, 0, 0]
_SPLIT_DIV = [1, 0]
_SPLIT_T1 = [1, 0, 0, 0]
_SPLIT_T2 = [1, 0, 1, 0]

_SHOW_ELEMENT = NodeFilter.SHOW_ELEMENT
_SHOW_ALL = NodeFilter.SHOW_ALL
_SHOW_TEXT = NodeFilter.SHOW_TEXT
_SHOW_COMMENT = NodeFilter.SHOW_COMMENT

RANGE_CASES: list[dict[str, Any]] = [
    {
        "id": "range-boundaries-ul",
        "feature": "range",
        "html": _UL,
        "start": (_UL_PATH, 0),
        "end": (_UL_PATH, 2),
        "probe": "boundaries",
    },
    {
        "id": "range-boundaries-collapsed",
        "feature": "range",
        "html": _UL,
        "start": (_UL_PATH, 1),
        "end": (_UL_PATH, 1),
        "probe": "boundaries",
    },
    {
        "id": "range-boundaries-text",
        "feature": "range",
        "html": _P,
        "start": (_P_TEXT, 6),
        "end": (_P_TEXT, 11),
        "probe": "boundaries",
    },
    {
        "id": "range-clone-siblings",
        "feature": "range",
        "html": _UL,
        "start": (_UL_PATH, 0),
        "end": (_UL_PATH, 2),
        "probe": "clone",
    },
    {
        "id": "range-clone-partial-text",
        "feature": "range",
        "html": _SPLIT,
        "start": (_SPLIT_T1, 3),
        "end": (_SPLIT_T2, 5),
        "probe": "clone",
    },
    {
        "id": "range-clone-single-text",
        "feature": "range",
        "html": _P,
        "start": (_P_TEXT, 6),
        "end": (_P_TEXT, 11),
        "probe": "clone",
    },
    {
        "id": "range-extract-siblings",
        "feature": "range",
        "html": _UL,
        "start": (_UL_PATH, 0),
        "end": (_UL_PATH, 2),
        "probe": "extract",
    },
    {
        "id": "range-extract-partial-text",
        "feature": "range",
        "html": _SPLIT,
        "start": (_SPLIT_T1, 3),
        "end": (_SPLIT_T2, 5),
        "probe": "extract",
    },
    {
        "id": "range-extract-mid",
        "feature": "range",
        "html": _UL,
        "start": (_UL_PATH, 1),
        "end": (_UL_PATH, 2),
        "probe": "extract",
    },
    {
        "id": "range-delete-siblings",
        "feature": "range",
        "html": _UL,
        "start": (_UL_PATH, 1),
        "end": (_UL_PATH, 3),
        "probe": "delete",
    },
    {
        "id": "range-delete-text",
        "feature": "range",
        "html": _P,
        "start": (_P_TEXT, 6),
        "end": (_P_TEXT, 12),
        "probe": "delete",
    },
    {
        "id": "range-delete-partial-text",
        "feature": "range",
        "html": _SPLIT,
        "start": (_SPLIT_T1, 3),
        "end": (_SPLIT_T2, 5),
        "probe": "delete",
    },
    {
        "id": "range-insert-element-boundary",
        "feature": "range",
        "html": "<div><p>x</p></div>",
        "start": ([1, 0], 1),
        "end": ([1, 0], 1),
        "probe": "insert",
        "node": {"tag": "hr"},
    },
    {
        "id": "range-insert-into-text",
        "feature": "range",
        "html": _P,
        "start": (_P_TEXT, 5),
        "end": (_P_TEXT, 5),
        "probe": "insert",
        "node": {"tag": "em", "html": "Z"},
    },
    {
        "id": "range-insert-text-node",
        "feature": "range",
        "html": "<div><p>x</p></div>",
        "start": ([1, 0], 0),
        "end": ([1, 0], 0),
        "probe": "insert",
        "node": {"text": "NEW"},
    },
    {
        "id": "range-surround-text",
        "feature": "range",
        "html": "<p>one two three</p>",
        "start": ([1, 0, 0], 4),
        "end": ([1, 0, 0], 7),
        "probe": "surround",
        "wrapper": {"tag": "em"},
    },
    {
        "id": "range-surround-siblings",
        "feature": "range",
        "html": _UL,
        "start": (_UL_PATH, 0),
        "end": (_UL_PATH, 2),
        "probe": "surround",
        "wrapper": {"tag": "div"},
    },
    {
        "id": "range-cbp-start-start",
        "feature": "range",
        "html": _UL,
        "start": (_UL_PATH, 0),
        "end": (_UL_PATH, 2),
        "other_start": (_UL_PATH, 1),
        "other_end": (_UL_PATH, 3),
        "how": 0,
        "probe": "compare_boundary",
    },
    {
        "id": "range-cbp-start-end",
        "feature": "range",
        "html": _UL,
        "start": (_UL_PATH, 0),
        "end": (_UL_PATH, 2),
        "other_start": (_UL_PATH, 1),
        "other_end": (_UL_PATH, 3),
        "how": 1,
        "probe": "compare_boundary",
    },
    {
        "id": "range-cbp-end-end",
        "feature": "range",
        "html": _UL,
        "start": (_UL_PATH, 0),
        "end": (_UL_PATH, 2),
        "other_start": (_UL_PATH, 1),
        "other_end": (_UL_PATH, 3),
        "how": 2,
        "probe": "compare_boundary",
    },
    {
        "id": "range-cbp-end-start",
        "feature": "range",
        "html": _UL,
        "start": (_UL_PATH, 0),
        "end": (_UL_PATH, 2),
        "other_start": (_UL_PATH, 1),
        "other_end": (_UL_PATH, 3),
        "how": 3,
        "probe": "compare_boundary",
    },
    {
        "id": "range-cbp-equal",
        "feature": "range",
        "html": _UL,
        "start": (_UL_PATH, 1),
        "end": (_UL_PATH, 2),
        "other_start": (_UL_PATH, 1),
        "other_end": (_UL_PATH, 2),
        "how": 0,
        "probe": "compare_boundary",
    },
    {
        "id": "range-compare-point-before",
        "feature": "range",
        "html": _UL,
        "start": (_UL_PATH, 1),
        "end": (_UL_PATH, 2),
        "point": (_UL_PATH, 0),
        "probe": "compare_point",
    },
    {
        "id": "range-compare-point-inside",
        "feature": "range",
        "html": _UL,
        "start": (_UL_PATH, 0),
        "end": (_UL_PATH, 3),
        "point": (_UL_PATH, 1),
        "probe": "compare_point",
    },
    {
        "id": "range-compare-point-after",
        "feature": "range",
        "html": _UL,
        "start": (_UL_PATH, 0),
        "end": (_UL_PATH, 1),
        "point": (_UL_PATH, 2),
        "probe": "compare_point",
    },
    {
        "id": "range-compare-point-text",
        "feature": "range",
        "html": _P,
        "start": (_P_TEXT, 3),
        "end": (_P_TEXT, 9),
        "point": (_P_TEXT, 6),
        "probe": "compare_point",
    },
    {
        "id": "range-point-in-true",
        "feature": "range",
        "html": _UL,
        "start": (_UL_PATH, 0),
        "end": (_UL_PATH, 3),
        "point": (_UL_PATH, 1),
        "probe": "point_in_range",
    },
    {
        "id": "range-point-in-false",
        "feature": "range",
        "html": _UL,
        "start": (_UL_PATH, 0),
        "end": (_UL_PATH, 1),
        "point": (_UL_PATH, 2),
        "probe": "point_in_range",
    },
    {
        "id": "range-intersects-true",
        "feature": "range",
        "html": _UL,
        "start": (_UL_PATH, 0),
        "end": (_UL_PATH, 2),
        "node_path": _UL_A,
        "probe": "intersects",
    },
    {
        "id": "range-intersects-false",
        "feature": "range",
        "html": _UL,
        "start": (_UL_PATH, 2),
        "end": (_UL_PATH, 3),
        "node_path": [1, 0, 0],
        "probe": "intersects",
    },
    {
        "id": "range-select-node",
        "feature": "range",
        "html": _UL,
        "start": (_UL_PATH, 0),
        "end": (_UL_PATH, 0),
        "node_path": [1, 0, 1],
        "probe": "select_node",
    },
    {
        "id": "range-select-node-contents",
        "feature": "range",
        "html": _UL,
        "start": (_UL_PATH, 0),
        "end": (_UL_PATH, 0),
        "node_path": _UL_PATH,
        "probe": "select_node_contents",
    },
    {
        "id": "range-select-node-contents-text",
        "feature": "range",
        "html": _P,
        "start": (_P_TEXT, 0),
        "end": (_P_TEXT, 0),
        "node_path": _P_TEXT,
        "probe": "select_node_contents",
    },
]

_TRAV_ROOT = [0]
_SECT_ROOT = [0]

TRAVERSAL_CASES: list[dict[str, Any]] = [
    {
        "id": "walker-elements",
        "feature": "traversal",
        "html": _MIXED,
        "kind": "walker",
        "root": _TRAV_ROOT,
        "what": _SHOW_ELEMENT,
        "probe": "forward",
    },
    {
        "id": "walker-all",
        "feature": "traversal",
        "html": _MIXED,
        "kind": "walker",
        "root": _TRAV_ROOT,
        "what": _SHOW_ALL,
        "probe": "forward",
    },
    {
        "id": "walker-text",
        "feature": "traversal",
        "html": _MIXED,
        "kind": "walker",
        "root": _TRAV_ROOT,
        "what": _SHOW_TEXT,
        "probe": "forward",
    },
    {
        "id": "walker-element-or-comment",
        "feature": "traversal",
        "html": "<div id=root><p>a</p><!--c--><span>b</span></div>",
        "kind": "walker",
        "root": _TRAV_ROOT,
        "what": _SHOW_ELEMENT | _SHOW_COMMENT,
        "probe": "forward",
    },
    {
        "id": "walker-reject-nav",
        "feature": "traversal",
        "html": _MIXED,
        "kind": "walker",
        "root": _TRAV_ROOT,
        "what": _SHOW_ELEMENT,
        "filter": "reject_nav",
        "probe": "forward",
    },
    {
        "id": "walker-skip-nav",
        "feature": "traversal",
        "html": _MIXED,
        "kind": "walker",
        "root": _TRAV_ROOT,
        "what": _SHOW_ELEMENT,
        "filter": "skip_nav",
        "probe": "forward",
    },
    {
        "id": "walker-only-a",
        "feature": "traversal",
        "html": _MIXED,
        "kind": "walker",
        "root": _TRAV_ROOT,
        "what": _SHOW_ELEMENT,
        "filter": "only_a",
        "probe": "forward",
    },
    {
        "id": "walker-reject-nav-backward",
        "feature": "traversal",
        "html": _MIXED,
        "kind": "walker",
        "root": _TRAV_ROOT,
        "what": _SHOW_ELEMENT,
        "filter": "reject_nav",
        "probe": "backward",
    },
    {
        "id": "walker-skip-nav-backward",
        "feature": "traversal",
        "html": _MIXED,
        "kind": "walker",
        "root": _TRAV_ROOT,
        "what": _SHOW_ELEMENT,
        "filter": "skip_nav",
        "probe": "backward",
    },
    {
        "id": "walker-backward-all",
        "feature": "traversal",
        "html": _MIXED,
        "kind": "walker",
        "root": _TRAV_ROOT,
        "what": _SHOW_ALL,
        "probe": "backward",
    },
    {
        "id": "walker-children",
        "feature": "traversal",
        "html": _MIXED,
        "kind": "walker",
        "root": _TRAV_ROOT,
        "what": _SHOW_ELEMENT,
        "probe": "children",
    },
    {
        "id": "walker-last-child-parents",
        "feature": "traversal",
        "html": _MIXED,
        "kind": "walker",
        "root": _TRAV_ROOT,
        "what": _SHOW_ELEMENT,
        "probe": "last_child_parents",
    },
    {
        "id": "walker-reject-section-skip-p",
        "feature": "traversal",
        "html": _SECT,
        "kind": "walker",
        "root": _SECT_ROOT,
        "what": _SHOW_ELEMENT,
        "filter": "reject_section_skip_p",
        "probe": "forward",
    },
    {
        "id": "iter-elements",
        "feature": "traversal",
        "html": _MIXED,
        "kind": "iterator",
        "root": _TRAV_ROOT,
        "what": _SHOW_ELEMENT,
        "probe": "forward",
    },
    {
        "id": "iter-all",
        "feature": "traversal",
        "html": _MIXED,
        "kind": "iterator",
        "root": _TRAV_ROOT,
        "what": _SHOW_ALL,
        "probe": "forward",
    },
    {
        "id": "iter-text",
        "feature": "traversal",
        "html": _MIXED,
        "kind": "iterator",
        "root": _TRAV_ROOT,
        "what": _SHOW_TEXT,
        "probe": "forward",
    },
    {
        "id": "iter-reject-nav-is-skip",
        "feature": "traversal",
        "html": _MIXED,
        "kind": "iterator",
        "root": _TRAV_ROOT,
        "what": _SHOW_ELEMENT,
        "filter": "reject_nav",
        "probe": "forward",
    },
    {
        "id": "iter-skip-nav",
        "feature": "traversal",
        "html": _MIXED,
        "kind": "iterator",
        "root": _TRAV_ROOT,
        "what": _SHOW_ELEMENT,
        "filter": "skip_nav",
        "probe": "forward",
    },
    {
        "id": "iter-only-a",
        "feature": "traversal",
        "html": _MIXED,
        "kind": "iterator",
        "root": _TRAV_ROOT,
        "what": _SHOW_ELEMENT,
        "filter": "only_a",
        "probe": "forward",
    },
    {
        "id": "iter-backward",
        "feature": "traversal",
        "html": _MIXED,
        "kind": "iterator",
        "root": _TRAV_ROOT,
        "what": _SHOW_ELEMENT,
        "probe": "backward",
    },
    {
        "id": "iter-reject-nav-backward",
        "feature": "traversal",
        "html": _MIXED,
        "kind": "iterator",
        "root": _TRAV_ROOT,
        "what": _SHOW_ELEMENT,
        "filter": "reject_nav",
        "probe": "backward",
    },
    {
        "id": "iter-reject-section-skip-p",
        "feature": "traversal",
        "html": _SECT,
        "kind": "iterator",
        "root": _SECT_ROOT,
        "what": _SHOW_ELEMENT,
        "filter": "reject_section_skip_p",
        "probe": "forward",
    },
]

_HOST_SLOTS = '<my-card><h2 slot="title">Head</h2><p>body</p>loose<span slot="title">More</span></my-card>'
_SHADOW_SLOTS = '<header><slot name="title"></slot></header><main><slot></slot></main>'
_HOST_FALLBACK = '<my-el><span slot="a">A</span></my-el>'
_SHADOW_FALLBACK = '<slot name="a"></slot><slot><em>fb</em></slot>'

SHADOW_CASES: list[dict[str, Any]] = [
    {
        "id": "shadow-attach-open",
        "feature": "shadow",
        "probe": "attach_mode",
        "mode": "open",
        "host_html": "<my-el></my-el>",
        "shadow_html": "<slot></slot>",
    },
    {
        "id": "shadow-attach-closed",
        "feature": "shadow",
        "probe": "attach_mode",
        "mode": "closed",
        "host_html": "<my-el></my-el>",
        "shadow_html": "<slot></slot>",
    },
    {
        "id": "shadow-assigned-named-default",
        "feature": "shadow",
        "probe": "assigned",
        "mode": "open",
        "host_html": _HOST_SLOTS,
        "shadow_html": _SHADOW_SLOTS,
        "slots": {"title": 'slot[name="title"]', "default": "main slot"},
    },
    {
        "id": "shadow-assigned-fallback",
        "feature": "shadow",
        "probe": "assigned",
        "mode": "open",
        "host_html": _HOST_FALLBACK,
        "shadow_html": _SHADOW_FALLBACK,
        "slots": {"named": 'slot[name="a"]', "default": "slot:not([name])"},
    },
]

_DSD_OPEN = (
    "<article id=post>"
    '<template shadowrootmode="open" shadowrootclonable>'
    "<h1><slot name=title></slot></h1><slot></slot>"
    "</template>"
    '<span slot="title">Hello</span><p>World</p>'
    "</article>"
)
_DSD_CLOSED = "<div id=host><template shadowrootmode=closed><b>secret</b></template><p>light</p></div>"

DECLARATIVE_CASES: list[dict[str, Any]] = [
    {
        "id": "dsd-open-clonable",
        "html": _DSD_OPEN,
        "host_id": "post",
        "clonable": True,
        "delegates_focus": False,
        "mode": "open",
        "shadow_html": '<h1><slot name="title"></slot></h1><slot></slot>',
        "light_html": '<span slot="title">Hello</span><p>World</p>',
    },
    {
        "id": "dsd-closed",
        "html": _DSD_CLOSED,
        "host_id": "host",
        "mode": "closed",
        "light_html": "<p>light</p>",
    },
]

_CROSS = "<div><p>aa<b>bb</b>cc</p><p>dd<i>ii</i>ee</p></div>"
_CROSS_T_AA = [1, 0, 0, 0]
_CROSS_T_CC = [1, 0, 0, 2]
_CROSS_T_BB = [1, 0, 0, 1, 0]
_CROSS_T_DD = [1, 0, 1, 0]
_CROSS_T_EE = [1, 0, 1, 2]
_CMT = "<div><p>a</p><!--note--><span>b</span><p>c</p></div>"
_CMT_DIV = [1, 0]
_DEEP = (
    "<div id=a><p id=p1>x<b id=b1>y</b>z</p>"
    "<ul id=u><li id=l1>1</li><li id=l2>2<em id=e>e</em></li></ul>"
    "<span id=sp>s</span></div>"
)
_DEEP_ROOT = [1, 0]

RANGE_CASES += [
    {
        "id": "range-clone-cross-two-paragraphs",
        "feature": "range",
        "html": _CROSS,
        "start": (_CROSS_T_AA, 1),
        "end": (_CROSS_T_EE, 1),
        "probe": "clone",
    },
    {
        "id": "range-extract-cross-two-paragraphs",
        "feature": "range",
        "html": _CROSS,
        "start": (_CROSS_T_AA, 1),
        "end": (_CROSS_T_EE, 1),
        "probe": "extract",
    },
    {
        "id": "range-delete-cross-two-paragraphs",
        "feature": "range",
        "html": _CROSS,
        "start": (_CROSS_T_AA, 1),
        "end": (_CROSS_T_EE, 1),
        "probe": "delete",
    },
    {
        "id": "range-clone-across-inline",
        "feature": "range",
        "html": _CROSS,
        "start": (_CROSS_T_AA, 1),
        "end": (_CROSS_T_CC, 1),
        "probe": "clone",
    },
    {
        "id": "range-extract-across-inline",
        "feature": "range",
        "html": _CROSS,
        "start": (_CROSS_T_AA, 1),
        "end": (_CROSS_T_CC, 1),
        "probe": "extract",
    },
    {
        "id": "range-clone-into-inline",
        "feature": "range",
        "html": _CROSS,
        "start": (_CROSS_T_AA, 1),
        "end": (_CROSS_T_BB, 1),
        "probe": "clone",
    },
    {
        "id": "range-extract-into-inline",
        "feature": "range",
        "html": _CROSS,
        "start": (_CROSS_T_AA, 1),
        "end": (_CROSS_T_BB, 1),
        "probe": "extract",
    },
    {
        "id": "range-clone-from-inline-to-text",
        "feature": "range",
        "html": _CROSS,
        "start": (_CROSS_T_BB, 1),
        "end": (_CROSS_T_DD, 1),
        "probe": "clone",
    },
    {
        "id": "range-extract-from-inline-to-text",
        "feature": "range",
        "html": _CROSS,
        "start": (_CROSS_T_BB, 1),
        "end": (_CROSS_T_DD, 1),
        "probe": "extract",
    },
    {
        "id": "range-clone-over-comment",
        "feature": "range",
        "html": _CMT,
        "start": (_CMT_DIV, 0),
        "end": (_CMT_DIV, 4),
        "probe": "clone",
    },
    {
        "id": "range-extract-over-comment",
        "feature": "range",
        "html": _CMT,
        "start": (_CMT_DIV, 1),
        "end": (_CMT_DIV, 3),
        "probe": "extract",
    },
    {
        "id": "range-delete-over-comment",
        "feature": "range",
        "html": _CMT,
        "start": (_CMT_DIV, 1),
        "end": (_CMT_DIV, 3),
        "probe": "delete",
    },
    {
        "id": "range-boundaries-cross",
        "feature": "range",
        "html": _CROSS,
        "start": (_CROSS_T_BB, 1),
        "end": (_CROSS_T_DD, 1),
        "probe": "boundaries",
    },
    {
        "id": "range-cbp-cross-subtree-ss",
        "feature": "range",
        "html": _CROSS,
        "start": (_CROSS_T_AA, 1),
        "end": (_CROSS_T_CC, 1),
        "other_start": (_CROSS_T_DD, 0),
        "other_end": (_CROSS_T_EE, 1),
        "how": 0,
        "probe": "compare_boundary",
    },
    {
        "id": "range-cbp-cross-subtree-es",
        "feature": "range",
        "html": _CROSS,
        "start": (_CROSS_T_AA, 1),
        "end": (_CROSS_T_DD, 1),
        "other_start": (_CROSS_T_BB, 0),
        "other_end": (_CROSS_T_EE, 1),
        "how": 3,
        "probe": "compare_boundary",
    },
    {
        "id": "range-compare-point-deep-before",
        "feature": "range",
        "html": _DEEP,
        "start": ([1, 0, 1], 0),
        "end": ([1, 0, 1], 2),
        "point": ([1, 0, 0], 0),
        "probe": "compare_point",
    },
    {
        "id": "range-compare-point-deep-inside",
        "feature": "range",
        "html": _DEEP,
        "start": (_DEEP_ROOT, 0),
        "end": (_DEEP_ROOT, 3),
        "point": ([1, 0, 1, 1], 0),
        "probe": "compare_point",
    },
    {
        "id": "range-intersects-deep-true",
        "feature": "range",
        "html": _DEEP,
        "start": ([1, 0, 0], 0),
        "end": ([1, 0, 1], 1),
        "node_path": [1, 0, 1, 0],
        "probe": "intersects",
    },
    {
        "id": "range-intersects-deep-false",
        "feature": "range",
        "html": _DEEP,
        "start": ([1, 0, 0], 0),
        "end": ([1, 0, 0], 1),
        "node_path": [1, 0, 2],
        "probe": "intersects",
    },
    {
        "id": "range-point-in-deep",
        "feature": "range",
        "html": _DEEP,
        "start": ([1, 0, 0], 0),
        "end": ([1, 0, 2], 0),
        "point": ([1, 0, 1], 1),
        "probe": "point_in_range",
    },
    {
        "id": "range-select-node-deep",
        "feature": "range",
        "html": _DEEP,
        "start": (_DEEP_ROOT, 0),
        "end": (_DEEP_ROOT, 0),
        "node_path": [1, 0, 1, 1],
        "probe": "select_node",
    },
    {
        "id": "range-select-node-contents-inline",
        "feature": "range",
        "html": _CROSS,
        "start": (_CROSS_T_AA, 0),
        "end": (_CROSS_T_AA, 0),
        "node_path": [1, 0, 0, 1],
        "probe": "select_node_contents",
    },
    {
        "id": "range-surround-inline-run",
        "feature": "range",
        "html": "<div><a>1</a><a>2</a><a>3</a></div>",
        "start": ([1, 0], 1),
        "end": ([1, 0], 3),
        "probe": "surround",
        "wrapper": {"tag": "span"},
    },
    {
        "id": "range-insert-fragment-into-list",
        "feature": "range",
        "html": _UL,
        "start": (_UL_PATH, 1),
        "end": (_UL_PATH, 1),
        "probe": "insert",
        "node": {"tag": "li", "html": "NEW"},
    },
]

TRAVERSAL_CASES += [
    {
        "id": "walker-deep-elements",
        "feature": "traversal",
        "html": _DEEP,
        "kind": "walker",
        "root": _DEEP_ROOT,
        "what": _SHOW_ELEMENT,
        "probe": "forward",
    },
    {
        "id": "walker-deep-backward",
        "feature": "traversal",
        "html": _DEEP,
        "kind": "walker",
        "root": _DEEP_ROOT,
        "what": _SHOW_ELEMENT,
        "probe": "backward",
    },
    {
        "id": "walker-deep-children",
        "feature": "traversal",
        "html": _DEEP,
        "kind": "walker",
        "root": _DEEP_ROOT,
        "what": _SHOW_ELEMENT,
        "probe": "children",
    },
    {
        "id": "walker-deep-last-child-parents",
        "feature": "traversal",
        "html": _DEEP,
        "kind": "walker",
        "root": _DEEP_ROOT,
        "what": _SHOW_ELEMENT,
        "probe": "last_child_parents",
    },
    {
        "id": "walker-deep-reject-ul",
        "feature": "traversal",
        "html": _DEEP,
        "kind": "walker",
        "root": _DEEP_ROOT,
        "what": _SHOW_ELEMENT,
        "filter": "reject_ul",
        "probe": "forward",
    },
    {
        "id": "walker-deep-reject-ul-backward",
        "feature": "traversal",
        "html": _DEEP,
        "kind": "walker",
        "root": _DEEP_ROOT,
        "what": _SHOW_ELEMENT,
        "filter": "reject_ul",
        "probe": "backward",
    },
    {
        "id": "walker-deep-skip-span-all",
        "feature": "traversal",
        "html": _DEEP,
        "kind": "walker",
        "root": _DEEP_ROOT,
        "what": _SHOW_ALL,
        "filter": "skip_span",
        "probe": "forward",
    },
    {
        "id": "walker-deep-only-a",
        "feature": "traversal",
        "html": "<div id=a><p><a>1</a>x</p><a>2</a><span><a>3</a></span></div>",
        "kind": "walker",
        "root": [1, 0],
        "what": _SHOW_ELEMENT,
        "filter": "only_a",
        "probe": "forward",
    },
    {
        "id": "iter-deep-elements",
        "feature": "traversal",
        "html": _DEEP,
        "kind": "iterator",
        "root": _DEEP_ROOT,
        "what": _SHOW_ELEMENT,
        "probe": "forward",
    },
    {
        "id": "iter-deep-backward",
        "feature": "traversal",
        "html": _DEEP,
        "kind": "iterator",
        "root": _DEEP_ROOT,
        "what": _SHOW_ELEMENT,
        "probe": "backward",
    },
    {
        "id": "iter-deep-reject-ul-is-skip",
        "feature": "traversal",
        "html": _DEEP,
        "kind": "iterator",
        "root": _DEEP_ROOT,
        "what": _SHOW_ELEMENT,
        "filter": "reject_ul",
        "probe": "forward",
    },
    {
        "id": "iter-deep-all",
        "feature": "traversal",
        "html": _DEEP,
        "kind": "iterator",
        "root": _DEEP_ROOT,
        "what": _SHOW_ALL,
        "probe": "forward",
    },
    {
        "id": "walker-from-nonroot",
        "feature": "traversal",
        "html": _DEEP,
        "kind": "walker",
        "root": [1, 0, 1],
        "what": _SHOW_ELEMENT,
        "probe": "forward",
    },
    {
        "id": "iter-from-nonroot",
        "feature": "traversal",
        "html": _DEEP,
        "kind": "iterator",
        "root": [1, 0, 1],
        "what": _SHOW_ALL,
        "probe": "forward",
    },
]

RANGE_CASES += [
    {
        "id": "range-clone-collapsed",
        "feature": "range",
        "html": _UL,
        "start": (_UL_PATH, 1),
        "end": (_UL_PATH, 1),
        "probe": "clone",
    },
    {
        "id": "range-extract-collapsed",
        "feature": "range",
        "html": _UL,
        "start": (_UL_PATH, 1),
        "end": (_UL_PATH, 1),
        "probe": "extract",
    },
    {
        "id": "range-delete-collapsed",
        "feature": "range",
        "html": _UL,
        "start": (_UL_PATH, 1),
        "end": (_UL_PATH, 1),
        "probe": "delete",
    },
    {
        "id": "range-clone-collapsed-text",
        "feature": "range",
        "html": _P,
        "start": (_P_TEXT, 5),
        "end": (_P_TEXT, 5),
        "probe": "clone",
    },
    {
        "id": "range-boundaries-end-offset",
        "feature": "range",
        "html": _UL,
        "start": (_UL_PATH, 3),
        "end": (_UL_PATH, 3),
        "probe": "boundaries",
    },
    {
        "id": "range-extract-whole-container",
        "feature": "range",
        "html": _UL,
        "start": (_UL_PATH, 0),
        "end": (_UL_PATH, 3),
        "probe": "extract",
    },
    {
        "id": "range-clone-text-tail",
        "feature": "range",
        "html": _P,
        "start": (_P_TEXT, 12),
        "end": (_P_TEXT, 17),
        "probe": "clone",
    },
    {
        "id": "range-compare-point-at-end",
        "feature": "range",
        "html": _P,
        "start": (_P_TEXT, 0),
        "end": (_P_TEXT, 17),
        "point": (_P_TEXT, 17),
        "probe": "compare_point",
    },
    {
        "id": "range-insert-at-text-end",
        "feature": "range",
        "html": "<p>abc</p>",
        "start": ([1, 0, 0], 3),
        "end": ([1, 0, 0], 3),
        "probe": "insert",
        "node": {"tag": "br"},
    },
]

_ALL_DIFF_CASES = RANGE_CASES + TRAVERSAL_CASES + SHADOW_CASES


@pytest.fixture(scope="session")
def jsdom_results() -> dict[str, dict[str, Any]]:
    assert _NODE is not None
    payload = json.dumps(_ALL_DIFF_CASES)
    completed = subprocess.run(  # ruff:ignore[subprocess-without-shell-equals-true]
        [_NODE, str(_RUNNER)], input=payload, capture_output=True, text=True, timeout=120, check=True
    )
    return {entry["id"]: entry for entry in json.loads(completed.stdout)}


@pytest.mark.parametrize("kase", [pytest.param(case, id=case["id"]) for case in _ALL_DIFF_CASES])
def test_dom_matches_jsdom(kase: dict[str, Any], jsdom_results: dict[str, dict[str, Any]]) -> None:
    oracle = jsdom_results[kase["id"]]
    assert oracle["ok"], f"jsdom runner failed: {oracle.get('error')}"
    assert _run_turbo(kase) == oracle["result"]


@pytest.mark.parametrize("kase", [pytest.param(case, id=case["id"]) for case in DECLARATIVE_CASES])
def test_declarative_shadow_matches_spec(kase: dict[str, Any]) -> None:
    """jsdom 29 cannot parse declarative shadow roots, so the oracle is the WHATWG DSD algorithm's expected tree."""
    document = parse(kase["html"], allow_declarative_shadow_roots=True)
    host = document.find(id=kase["host_id"])
    assert host is not None
    assert host.inner_html == kase["light_html"]
    if kase["mode"] == "closed":  # a closed root is created but unreachable through Element.shadow_root, by spec
        assert host.shadow_root is None
        return
    shadow = host.shadow_root
    assert shadow is not None
    assert shadow.mode == kase["mode"]
    assert shadow.clonable == kase["clonable"]
    assert shadow.delegates_focus == kase["delegates_focus"]
    assert shadow.html == kase["shadow_html"]
