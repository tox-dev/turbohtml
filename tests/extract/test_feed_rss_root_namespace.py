from __future__ import annotations

import pytest

from turbohtml import parse, parse_xml
from turbohtml.extract import feed


@pytest.mark.parametrize(
    "namespace",
    ["urn:foreign", "http://purl.org/rss/1.0/", "http://www.w3.org/2005/Atom"],
)
def test_feed_rss_foreign_default_namespace_is_ignored(namespace: str) -> None:
    assert feed(f'<rss version="2.0" xmlns="{namespace}"><channel><title>Feed</title></channel></rss>') is None


def test_feed_rss_foreign_default_namespace_document_is_ignored() -> None:
    assert parse_xml('<rss xmlns="urn:foreign"><channel><title>Feed</title></channel></rss>').feed() is None


def test_feed_rss_foreign_root_with_unqualified_channel_is_ignored() -> None:
    assert feed('<rss xmlns="urn:foreign"><channel xmlns=""><title>Feed</title></channel></rss>') is None


def test_feed_rss_empty_default_namespace_is_found() -> None:
    result = feed('<rss xmlns=""><channel><title>Feed</title></channel></rss>')
    assert result is not None
    assert result.title == "Feed"


def test_feed_rss_missing_default_namespace_is_found() -> None:
    result = feed("<rss><channel><title>Feed</title></channel></rss>")
    assert result is not None
    assert result.title == "Feed"


def test_feed_rss_unrelated_prefixed_namespace_is_found() -> None:
    result = feed('<rss xmlns:x="urn:foreign"><channel><title>Feed</title></channel></rss>')
    assert result is not None
    assert result.title == "Feed"


def test_feed_rss_prefixed_foreign_root_is_ignored() -> None:
    assert feed('<x:rss xmlns:x="urn:foreign"><x:channel><x:title>Feed</x:title></x:channel></x:rss>') is None


def test_feed_rss_html_foreign_default_namespace_is_found() -> None:
    result = parse('<rss xmlns="urn:foreign"><channel><title>Feed</title></channel></rss>').feed()
    assert result is not None
    assert result.title == "Feed"
