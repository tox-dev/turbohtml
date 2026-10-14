"""RDF item identifiers with XML bases."""

from __future__ import annotations

from typing import Final

import pytest

from turbohtml.extract import feed


@pytest.mark.parametrize(
    ("root_base", "item_base", "about", "expected"),
    [
        pytest.param(
            ' xml:base="https://example.org/base/"', "", "item", "https://example.org/base/item", id="root-base"
        ),
        pytest.param(
            ' xml:base="https://example.org/base/"',
            ' xml:base="../other/"',
            "item",
            "https://example.org/other/item",
            id="nested-base",
        ),
        pytest.param(' xml:base="https://example.org/base/"', "", "", "https://example.org/base/", id="empty"),
        pytest.param(
            ' xml:base="https://example.org/base/"',
            "",
            "https://elsewhere.example/item",
            "https://elsewhere.example/item",
            id="absolute",
        ),
        pytest.param("", "", "item", "item", id="no-base"),
    ],
)
def test_feed_rdf_item_id_resolves_about(root_base: str, item_base: str, about: str, expected: str) -> None:
    xml: Final = (
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
        f'xmlns="http://purl.org/rss/1.0/"{root_base}>'
        f'<item{item_base} rdf:about="{about}"><title>Item</title></item></rdf:RDF>'
    )
    result = feed(xml)
    assert result is not None
    assert result.entries[0].id == expected


def test_feed_rdf_item_id_uses_explicit_id() -> None:
    xml: Final = (
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
        'xmlns="http://purl.org/rss/1.0/" xml:base="https://example.org/base/">'
        '<item rdf:about="item"><id>explicit-id</id></item></rdf:RDF>'
    )
    result = feed(xml)
    assert result is not None
    assert result.entries[0].id == "explicit-id"


@pytest.mark.parametrize("identifier", ["guid", "id"])
def test_feed_rdf_sequence_id_uses_explicit_element(identifier: str) -> None:
    xml: Final = (
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
        'xmlns="http://purl.org/rss/1.0/" xml:base="https://example.org/base/">'
        '<channel><items><rdf:Seq><rdf:li rdf:resource="item"/></rdf:Seq></items></channel>'
        f'<item rdf:about="item"><{identifier}>explicit-id</{identifier}></item></rdf:RDF>'
    )
    result = feed(xml)
    assert result is not None
    assert result.entries[0].id == "explicit-id"
