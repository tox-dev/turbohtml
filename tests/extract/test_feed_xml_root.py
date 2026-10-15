from __future__ import annotations

from typing import Final

import pytest

from turbohtml import parse, parse_xml
from turbohtml.extract import feed

RSS: Final = (
    '<rss version="2.0"><channel><title>Feed</title><link>https://example.test/</link>'
    "<description>News</description></channel></rss>"
)
ATOM: Final = (
    '<feed xmlns="http://www.w3.org/2005/Atom"><id>urn:feed</id><title>Feed</title>'
    "<updated>2020-01-01T00:00:00Z</updated><author><name>Writer</name></author></feed>"
)
RDF: Final = (
    '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/">'
    '<channel rdf:about="https://example.test/"><title>Feed</title><link>https://example.test/</link>'
    "<description>News</description></channel>"
    '<item rdf:about="https://example.test/1"><title>One</title><link>https://example.test/1</link></item>'
    "</rdf:RDF>"
)


@pytest.mark.parametrize(
    "body", [pytest.param(RSS, id="rss2"), pytest.param(ATOM, id="atom"), pytest.param(RDF, id="rss1")]
)
def test_feed_xml_nested_root_is_ignored(body: str) -> None:
    assert feed(f"<wrapper>{body}</wrapper>") is None


@pytest.mark.parametrize(
    "body", [pytest.param(RSS, id="rss2"), pytest.param(ATOM, id="atom"), pytest.param(RDF, id="rss1")]
)
def test_feed_xml_document_nested_root_is_ignored(body: str) -> None:
    assert parse_xml(f"<wrapper>{body}</wrapper>").feed() is None


@pytest.mark.parametrize(
    "body", [pytest.param(RSS, id="rss2"), pytest.param(ATOM, id="atom"), pytest.param(RDF, id="rss1")]
)
def test_feed_html_embedded_root_is_found(body: str) -> None:
    result = parse(f"<wrapper>{body}</wrapper>").feed()
    assert result is not None
    assert result.title == "Feed"


def test_feed_xml_document_root_after_prolog_is_found() -> None:
    result = feed(f"<?xml version='1.0'?><!--comment--><?check data?>{ATOM}")
    assert result is not None
    assert result.title == "Feed"


def test_feed_xml_document_without_root_is_ignored() -> None:
    document = parse_xml(f"<!--comment-->{RSS}")
    root = document.root
    assert root is not None
    root.extract()
    assert document.feed() is None


def test_feed_malformed_xml_uses_html_discovery() -> None:
    result = feed("<wrapper><rss><channel><title>Feed</title></channel></rss>")
    assert result is not None
    assert result.title == "Feed"
