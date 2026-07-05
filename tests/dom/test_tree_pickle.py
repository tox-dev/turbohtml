"""Pickling round-trips a node and its subtree, exactly, for every node type."""

from __future__ import annotations

import pickle  # noqa: S403  # round-tripping our own trusted payloads

import pytest

from turbohtml import CData, Comment, Doctype, Document, Element, Node, ProcessingInstruction, Text, parse
from turbohtml._html import _reconstruct  # the private pickle hook


def _roundtrip(node: Node) -> Node:
    return pickle.loads(pickle.dumps(node))  # noqa: S301  # our own trusted payload


@pytest.mark.parametrize(
    "node",
    [
        Text("a & b <ok>"),
        Comment("a note"),
        CData("x < y & z"),
        ProcessingInstruction("xml-stylesheet", 'href="a.css"'),
        ProcessingInstruction("php", ""),
    ],
    ids=["text", "comment", "cdata", "pi", "pi-empty"],
)
def test_leaf_nodes_round_trip(node: Text | Comment | CData | ProcessingInstruction) -> None:
    clone = _roundtrip(node)
    assert clone is not node
    assert type(clone) is type(node)
    assert clone.html == node.html


def test_element_with_attributes_and_children_round_trips() -> None:
    element = Element("div", {"class": ["card", "lg"], "id": "x", "hidden": None})
    heading = Element("h2")
    heading.text = "Title"
    element.append(heading)
    element.append(Text("tail"))
    clone = _roundtrip(element)
    assert clone.html == '<div class="card lg" id="x" hidden=""><h2>Title</h2>tail</div>'


def test_parser_produced_invalid_tag_name_round_trips() -> None:
    # the tokenizer keeps a '<' in a tag name ("a<b" from "<a<b>"), a name Element() rejects; pickle
    # reconstruction must rebuild it through the trusted builder, not the validating constructor (issue #83).
    element = next(node for node in parse("<a<b>x").descendants if isinstance(node, Element) and "<" in node.tag)
    assert element.tag == "a<b"
    clone = _roundtrip(element)
    assert isinstance(clone, Element)
    assert clone.tag == "a<b"
    assert clone.html == element.html


def test_nested_pi_and_cdata_survive_pickling() -> None:
    # serialize-and-reparse would fold these; pickling carries the child list
    host = Element("host")
    host.append(ProcessingInstruction("t", "d"))
    host.append(CData("raw"))
    assert _roundtrip(host).html == "<host><?t d><![CDATA[raw]]></host>"


@pytest.mark.parametrize("tag", ["style", "xmp", "iframe"])
def test_rawtext_element_keeps_literal_text_across_pickle(tag: str) -> None:
    # a raw-text element serializes its text verbatim; the round-trip must not start escaping it (issue #86)
    element = parse(f"<{tag}>x < y</{tag}>").find(tag)
    assert isinstance(element, Element)
    assert element.html == f"<{tag}>x < y</{tag}>"
    assert _roundtrip(element).html == element.html


def test_document_round_trips() -> None:
    doc = parse("<!DOCTYPE html><title>Hi</title><p id=a>x</p><!--c-->")
    clone = _roundtrip(doc)
    assert isinstance(clone, Document)
    assert clone.html == doc.html


@pytest.mark.parametrize(
    ("markup", "public_id", "system_id"),
    [
        pytest.param("<!DOCTYPE html>", None, None, id="name-only"),
        pytest.param(
            '<!DOCTYPE html PUBLIC "-//W3C//DTD HTML 4.01//EN" "http://www.w3.org/TR/html4/strict.dtd">',
            "-//W3C//DTD HTML 4.01//EN",
            "http://www.w3.org/TR/html4/strict.dtd",
            id="public-and-system",
        ),
        # a missing sibling identifier must stay None across the round-trip, not collapse to ""
        pytest.param('<!DOCTYPE html PUBLIC "p">', "p", None, id="public-only-missing-system"),
        pytest.param('<!DOCTYPE html SYSTEM "s">', None, "s", id="system-only-missing-public"),
        pytest.param('<!DOCTYPE html PUBLIC "p" "">', "p", "", id="public-and-empty-system"),
        # an embedded quote in a single-quoted identifier must survive the round-trip (part of #478)
        pytest.param("<!DOCTYPE html PUBLIC 'pub\"lic' 'sys\"tem'>", 'pub"lic', 'sys"tem', id="embedded-quotes"),
    ],
)
def test_doctype_round_trips(markup: str, public_id: str | None, system_id: str | None) -> None:
    doctype = parse(markup).children[0]
    assert isinstance(doctype, Doctype)
    clone = _roundtrip(doctype)
    assert isinstance(clone, Doctype)
    assert clone.name == "html"
    assert clone.public_id == public_id
    assert clone.system_id == system_id


def test_pickled_element_is_independent() -> None:
    element = Element("p")
    element.append(Text("x"))
    clone = _roundtrip(element)
    assert isinstance(clone, Element)
    clone.append(Element("b"))  # editing the clone must not touch the original
    assert element.html == "<p>x</p>"
    assert clone.html == "<p>x<b></b></p>"


@pytest.mark.parametrize(
    ("html", "container"),
    [
        pytest.param("<svg><rect></rect></svg>", "svg", id="svg"),
        pytest.param("<math><mi>x</mi></math>", "math", id="mathml"),
    ],
)
def test_foreign_element_round_trip_keeps_namespace(html: str, container: str) -> None:
    parent = parse(html).find(container)
    assert parent is not None
    child = parent.children[0]
    assert isinstance(child, Element)
    clone = _roundtrip(child)
    assert isinstance(clone, Element)
    assert clone.namespace == child.namespace  # not reset to Namespace.HTML on the round-trip (issue #85)


@pytest.mark.parametrize("ns", [-1, 99], ids=["below", "above"])
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
    reduced = Element("div").__reduce__()
    kind = reduced[1][0]  # the (kind, data, children) payload pickle would store
    # a genuinely broken payload still fails: a non-mapping attrs value cannot build an element,
    # and reconstruction surfaces that TypeError instead of returning a half-built node
    with pytest.raises(TypeError):
        _reconstruct(kind, ("div", 123), [])  # ty: ignore[invalid-argument-type]  # malformed attrs payload
