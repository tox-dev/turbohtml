"""Dublin Core title fallbacks in RSS feeds."""

from __future__ import annotations

from typing import Final

import pytest

from turbohtml import parse
from turbohtml.extract import feed


@pytest.mark.parametrize(
    ("item_fields", "expected"),
    [
        pytest.param("<dc:title>DC title</dc:title>", "DC title", id="dublin-core"),
        pytest.param(
            '<alt:title xmlns:alt="http://purl.org/dc/elements/1.1/">Alias title</alt:title>',
            "Alias title",
            id="prefix-alias",
        ),
        pytest.param(
            '<title xmlns="http://purl.org/dc/elements/1.1/">Default title</title>',
            "Default title",
            id="default-namespace",
        ),
        pytest.param("<dc:title>DC title</dc:title><title>Core title</title>", "Core title", id="core-precedence"),
        pytest.param(
            '<title xmlns="http://purl.org/dc/elements/1.1/">DC title</title><title>Core title</title>',
            "Core title",
            id="default-namespace-before-core",
        ),
        pytest.param("<title> </title><dc:title>DC title</dc:title>", "DC title", id="empty-core"),
        pytest.param(
            '<alt:title xmlns:alt="urn:other">Wrong title</alt:title>',
            None,
            id="foreign-namespace",
        ),
    ],
)
def test_feed_rss_item_dc_title(item_fields: str, expected: str | None) -> None:
    xml: Final = (
        '<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><title>Feed</title>'
        f"<item><description>Body</description>{item_fields}</item></channel></rss>"
    )
    result = feed(xml)
    assert result is not None
    assert result.entries[0].title == expected


@pytest.mark.parametrize(
    ("channel_fields", "expected"),
    [
        pytest.param("<dc:title>DC title</dc:title>", "DC title", id="dublin-core"),
        pytest.param(
            '<alt:title xmlns:alt="http://purl.org/dc/elements/1.1/">Alias title</alt:title>',
            "Alias title",
            id="prefix-alias",
        ),
        pytest.param(
            '<title xmlns="http://purl.org/dc/elements/1.1/">Default title</title>',
            "Default title",
            id="default-namespace",
        ),
        pytest.param("<dc:title>DC title</dc:title><title>Core title</title>", "Core title", id="core-precedence"),
        pytest.param(
            '<title xmlns="http://purl.org/dc/elements/1.1/">DC title</title><title>Core title</title>',
            "Core title",
            id="default-namespace-before-core",
        ),
        pytest.param(
            '<title xmlns="http://purl.org/dc/elements/1.1/">DC title</title>\n'
            "<description>Body</description><title>Core title</title>",
            "Core title",
            id="intervening-content-before-core",
        ),
        pytest.param(
            '<description>Body</description>\n<title xmlns="http://purl.org/dc/elements/1.1/">DC title</title>',
            "DC title",
            id="preceding-content-before-dublin-core",
        ),
        pytest.param("<title> </title><dc:title>DC title</dc:title>", "DC title", id="empty-core"),
    ],
)
def test_feed_rss_channel_dc_title(channel_fields: str, expected: str) -> None:
    xml: Final = (
        f'<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"><channel>{channel_fields}</channel></rss>'
    )
    result = feed(xml)
    assert result is not None
    assert result.title == expected


@pytest.mark.parametrize("field", ["title", "entry_title"])
def test_feed_atom_title_does_not_use_dublin_core_fallback(field: str) -> None:
    xml: Final = (
        '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:dc="http://purl.org/dc/elements/1.1/">'
        "<dc:title>DC title</dc:title><entry><dc:title>Entry title</dc:title></entry></feed>"
    )
    result = feed(xml)
    assert result is not None
    assert (result.title if field == "title" else result.entries[0].title) is None


def test_feed_rdf_item_dc_title() -> None:
    xml: Final = (
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
        'xmlns="http://purl.org/rss/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<channel><title>Feed</title></channel><item rdf:about="urn:item">'
        "<description>Body</description><dc:title>DC title</dc:title></item></rdf:RDF>"
    )
    result = feed(xml)
    assert result is not None
    assert result.entries[0].title == "DC title"


def test_feed_html_rss_title_with_attribute() -> None:
    result = parse('<rss><channel><title data-x="1">Feed</title></channel></rss>').feed()
    assert result is not None
    assert result.title == "Feed"


def test_feed_rss_description_with_attribute() -> None:
    result = feed('<rss version="2.0"><channel><description data-x="1">Body</description></channel></rss>')
    assert result is not None
    assert result.description == "Body"
