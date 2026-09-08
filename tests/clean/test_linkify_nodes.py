"""linkify_node: linking an already parsed tree in place, and the string form over a node."""

from __future__ import annotations

import pytest

from turbohtml import Document, parse, parse_fragment
from turbohtml.clean import Linker, Linkify, linkify, linkify_node

_TEXT = "<p>See https://example.com and <a href='/x'>kept</a></p>"


def test_node_form_links_in_place_and_returns_the_node() -> None:
    root = parse_fragment(_TEXT)
    assert linkify_node(root) is root
    assert root.inner_html == linkify(_TEXT)


def test_a_document_links_its_body_text() -> None:
    document = parse(_TEXT)
    linked = linkify_node(document)
    assert isinstance(linked, Document)
    assert linked.serialize().count("<a ") == 2


def test_the_string_form_over_a_node_leaves_it_untouched() -> None:
    root = parse_fragment(_TEXT)
    before = root.inner_html
    assert linkify(root) == linkify(_TEXT)
    assert root.inner_html == before


def test_options_apply_to_the_node_form() -> None:
    root = parse_fragment("<p>mail bob@example.com</p>")
    linkify_node(root, Linkify(parse_email=True))
    assert 'href="mailto:bob@example.com"' in root.inner_html


def test_a_reusable_linker_offers_the_node_form() -> None:
    linker = Linker()
    root = parse_fragment(_TEXT)
    assert linker.linkify_node(root).inner_html == linker.linkify(_TEXT)


def test_the_node_form_refuses_a_str() -> None:
    with pytest.raises(TypeError, match="pass a str to linkify instead"):
        linkify_node(_TEXT)  # ty: ignore[invalid-argument-type]  # the argument check is the point


@pytest.mark.parametrize("entry", [linkify, linkify_node], ids=["linkify", "linkify_node"])
def test_a_foreign_object_is_rejected(entry: object) -> None:
    with pytest.raises(TypeError):
        entry(42)  # ty: ignore[call-non-callable]  # the argument check is the point
