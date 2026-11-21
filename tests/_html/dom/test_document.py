from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, NoReturn

import pytest

from turbohtml import Document, DocumentFragment, Element, Text, parse
from turbohtml._html import _reconstruct, _reconstruct_tree

if TYPE_CHECKING:
    from work_steps import WorkSteps

    from turbohtml import Node


@pytest.mark.parametrize("ns", [pytest.param(-1, id="below"), pytest.param(99, id="above")])
def test_reconstruct_rejects_out_of_range_namespace(ns: int) -> None:
    kind = _kind(Element("div"))
    # a crafted payload must not index the namespaces table out of bounds
    with pytest.raises(ValueError, match="namespace out of range"):
        _reconstruct(kind, ("div", {}, ns), [])


def _kind(node: Node) -> int:
    reduced = node.__reduce__()
    assert isinstance(reduced, tuple)
    return reduced[1][0][0][0]  # the kind of the first (kind, data, child count) record


def test_reconstruct_rejects_malformed_arguments() -> None:
    with pytest.raises(TypeError):
        _reconstruct("not a triple")  # ty: ignore[missing-argument, invalid-argument-type]  # deliberately malformed


def test_reconstruct_accepts_names_the_constructor_rejects() -> None:
    kind = _kind(Element("div"))
    # _reconstruct is the trusted pickle hook, so it rebuilds whatever the parser stored,
    # including a tag name like "a<b" that the validating public Element() rejects (issue #83)
    node = _reconstruct(kind, ("a<b", {}), [])
    assert isinstance(node, Element)
    assert node.tag == "a<b"


def test_reconstruct_propagates_a_construction_failure() -> None:
    kind = _kind(Element("div"))
    # a genuinely broken payload still fails: a non-mapping attrs value cannot build an element,
    # and reconstruction surfaces that TypeError instead of returning a half-built node
    with pytest.raises(TypeError):
        _reconstruct(kind, ("div", 123), [])


@pytest.mark.parametrize(
    ("records", "error", "message"),
    [
        pytest.param([], ValueError, "one tree", id="empty"),
        pytest.param([("element", ("div", {}), 1)], ValueError, "one tree", id="truncated"),
        pytest.param(
            [("element", ("div", {}), 0), ("element", ("p", {}), 0)], ValueError, "one tree", id="trailing-record"
        ),
        pytest.param([("element", ("div", {}), -1)], ValueError, "negative child count", id="negative-count"),
        pytest.param(
            [("element", ("div", {}), 1), ("element", ("p", 123), 0)],
            TypeError,
            "attrs must be a mapping",
            id="child-construction-fails",
        ),
        pytest.param(
            [("element", ("div", {}), 1), ("document", 0, 0)],
            TypeError,
            "Document cannot be inserted",
            id="unlinkable-child",
        ),
    ],
)
def test_reconstruct_tree_rejects_malformed_records(
    records: list[tuple[str, tuple[str, dict[str, str] | int] | int, int]], error: type[Exception], message: str
) -> None:
    kinds = {"element": _kind(Element("div")), "document": _kind(parse(""))}
    with pytest.raises(error, match=message):
        _reconstruct_tree([(kinds[name], data, count) for name, data, count in records], 0)


@pytest.mark.parametrize(
    ("records", "message"),
    [
        pytest.param("x", "list", id="records-not-a-list"),
        pytest.param(["x"], "argument must be", id="record-not-a-triple"),
    ],
)
def test_reconstruct_tree_rejects_a_malformed_payload_shape(records: str | list[str], message: str) -> None:
    with pytest.raises(TypeError, match=message):
        _reconstruct_tree(records, 0)  # ty: ignore[invalid-argument-type]  # crafted payloads on purpose


def test_reconstruct_accepts_a_legacy_markup_document() -> None:
    node = _reconstruct(_kind(parse("")), "<p>x</p>", [])
    assert isinstance(node, Document)
    assert node.html == "<html><head></head><body><p>x</p></body></html>"


def test_reconstruct_accepts_a_legacy_markup_xml_document() -> None:
    node = _reconstruct(_kind(parse("")), "<R><Q/></R>", [], 1)
    assert node.html == "<R><Q></Q></R>"


def test_reconstruct_rejects_an_unreadable_quirks_flag() -> None:
    with pytest.raises(RuntimeError, match="no truth value"):
        _reconstruct(_kind(parse("")), _Unbooleanable(), [])


class _Unbooleanable:
    def __bool__(self) -> NoReturn:
        msg = "no truth value"
        raise RuntimeError(msg)


def test_reconstruct_rejects_a_non_node_child() -> None:
    with pytest.raises(TypeError, match="must be a node"):
        _reconstruct(_kind(Element("div")), ("div", {}), ["x"])  # ty: ignore[invalid-argument-type]  # passes a wrong type on purpose to check the runtime rejection


def test_reconstruct_rejects_a_document_child() -> None:
    with pytest.raises(TypeError, match="Document cannot be inserted"):
        _reconstruct(_kind(Element("div")), ("div", {}), [parse("")])


@pytest.mark.parametrize(
    ("payload", "error", "message"),
    [
        pytest.param(("div", 0), TypeError, "Element", id="host-not-element"),
        pytest.param((Element("div"), 2), ValueError, "mode out of range", id="mode-out-of-range"),
    ],
)
def test_reconstruct_rejects_a_malformed_shadow_payload(
    payload: tuple[str | Element, int], error: type[Exception], message: str
) -> None:
    kind = _kind(Element("div").attach_shadow("open"))
    with pytest.raises(error, match=message):
        _reconstruct(kind, payload, [])


def test_reconstruct_rejects_a_host_that_already_has_a_shadow_root() -> None:
    host = Element("div")
    host.attach_shadow("open")
    with pytest.raises(ValueError, match="already has a shadow root"):
        _reconstruct(_kind(Element("p").attach_shadow("open")), (host, 0), [])


@pytest.mark.parametrize("per_level", [pytest.param(1, id="elements"), pytest.param(2, id="templates")])
def test_rebuilding_a_deep_chain_stays_linear(per_level: int, work_steps: WorkSteps) -> None:
    work_steps.assert_linear(lambda depth: partial(_rebuild_chain, depth, per_level), (500, 2000))


def _rebuild_chain(depth: int, per_level: int) -> None:
    element, fragment = _kind(Element("div")), _kind(DocumentFragment())
    node = _reconstruct(element, ("i", {}), [])
    for _ in range(depth):
        if per_level == 2:
            node = _reconstruct(element, ("template", {}), [_reconstruct(fragment, None, [node])])
        else:
            node = _reconstruct(element, ("d", {}), [node])


def test_reconstruct_copies_a_child_that_cannot_take_a_parent_in_place() -> None:
    element = _kind(Element("div"))
    outer = Element("div", {}, [Element("p", {}, [Text("x")]), Text("y")])
    moved = _reconstruct(element, ("section", {}), [outer.children[0]])
    assert (moved.html, outer.html) == ("<section><p>x</p></section>", "<div>y</div>")
    document = parse("<p><b>x</b></p>")
    paragraph = document.select_one("p")
    assert paragraph is not None
    moved = _reconstruct(element, ("section", {}), [paragraph])
    assert moved.html == "<section><p><b>x</b></p></section>"
    assert document.html == "<html><head></head><body></body></html>"
    moved = _reconstruct(element, ("Section", {}), [Element("p", {}, [Text("x")])], 1)
    assert moved.html == "<Section><p>x</p></Section>"


def test_reconstruct_copies_a_shadow_root_child() -> None:
    host = Element("div", {}, [Element("a")])
    root = host.attach_shadow("open")
    root.append(Element("p", {}, [Text("x")]))
    moved = _reconstruct(_kind(Element("div")), ("section", {}), [root])
    kept = host.shadow_root
    assert kept is not None
    assert (moved.html, host.html) == ("<section><p>x</p></section>", "<div><a></a></div>")
    assert (kept.parent, kept.html) == (None, "<p>x</p>")


def test_reconstruct_copies_a_rooted_fragment_into_a_fragment() -> None:
    fragment = _kind(DocumentFragment())
    outer = _reconstruct(fragment, None, [_reconstruct(fragment, None, [Element("p", {}, [Text("x")])]), Element("b")])
    assert [type(child) for child in outer.children] == [DocumentFragment, Element]
    assert outer.html == "<p>x</p><b></b>"


@pytest.mark.parametrize(
    ("ns", "expected", "urls"),
    [pytest.param(0, (5.0, "x"), ["x"], id="html"), pytest.param(1, None, [], id="svg")],
)
def test_meta_refresh_reads_only_an_html_meta(ns: int, expected: tuple[float, str] | None, urls: list[str]) -> None:
    # the parser never nests a foreign <meta>, so only a rebuilt payload puts one under the document
    meta = _reconstruct(_kind(Element("div")), ("meta", {"http-equiv": "refresh", "content": "5;url=x"}, ns), [])
    head = _reconstruct(_kind(Element("div")), ("head", {}), [meta])
    document = _reconstruct(_kind(parse("")), None, [_reconstruct(_kind(Element("div")), ("html", {}), [head])])
    assert isinstance(document, Document)
    assert document.meta_refresh() == expected
    assert [link.url for link in document.links()] == urls
