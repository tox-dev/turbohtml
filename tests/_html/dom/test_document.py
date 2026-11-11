from __future__ import annotations

from typing import TYPE_CHECKING, NoReturn

import pytest

from turbohtml import Document, Element, parse
from turbohtml._html import _reconstruct

if TYPE_CHECKING:
    from turbohtml import Node


@pytest.mark.parametrize("ns", [pytest.param(-1, id="below"), pytest.param(99, id="above")])
def test_reconstruct_rejects_out_of_range_namespace(ns: int) -> None:
    reduced = Element("div").__reduce__()
    assert isinstance(reduced, tuple)
    kind = reduced[1][0]  # the element kind from the (kind, data, children) payload
    # a crafted payload must not index the namespaces table out of bounds
    with pytest.raises(ValueError, match="namespace out of range"):
        _reconstruct(kind, ("div", {}, ns), [])


def test_reconstruct_rejects_malformed_arguments() -> None:
    with pytest.raises(TypeError):
        _reconstruct("not a triple")  # ty: ignore[missing-argument, invalid-argument-type]  # deliberately malformed


def test_reconstruct_accepts_names_the_constructor_rejects() -> None:
    reduced = Element("div").__reduce__()
    assert isinstance(reduced, tuple)
    kind = reduced[1][0]  # the (kind, data, children) payload pickle would store
    # _reconstruct is the trusted pickle hook, so it rebuilds whatever the parser stored,
    # including a tag name like "a<b" that the validating public Element() rejects (issue #83)
    node = _reconstruct(kind, ("a<b", {}), [])
    assert isinstance(node, Element)
    assert node.tag == "a<b"


def test_reconstruct_propagates_a_construction_failure() -> None:
    kind = Element("div").__reduce__()[1][0]  # the (kind, data, children) payload pickle would store
    # a genuinely broken payload still fails: a non-mapping attrs value cannot build an element,
    # and reconstruction surfaces that TypeError instead of returning a half-built node
    with pytest.raises(TypeError):
        _reconstruct(kind, ("div", 123), [])  # ty: ignore[invalid-argument-type]  # malformed attrs payload


def test_reconstruct_accepts_a_legacy_markup_document() -> None:
    node = _reconstruct(_kind(parse("")), "<p>x</p>", [])
    assert isinstance(node, Document)
    assert node.html == "<html><head></head><body><p>x</p></body></html>"


def _kind(node: Node) -> int:
    reduced = node.__reduce__()
    assert isinstance(reduced, tuple)
    return reduced[1][0]


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
