"""Namespace boundaries in RSS 2.0 feed extraction."""

from __future__ import annotations

from typing import Final

import pytest

from turbohtml import parse, parse_xml
from turbohtml.extract import feed


@pytest.mark.parametrize("uri", ["urn:other", "http://purl.org/rss/1.0/"])
def test_feed_rss_foreign_default_namespace_is_not_root(uri: str) -> None:
    assert feed(f'<rss xmlns="{uri}"><channel><title>Wrong</title></channel></rss>') is None


def test_feed_rss_root_restores_empty_namespace() -> None:
    result = feed('<wrapper xmlns="urn:other"><rss xmlns=""><channel><title>Right</title></channel></rss></wrapper>')
    assert result is not None
    assert result.title == "Right"


def test_feed_rss_foreign_default_channel_is_skipped() -> None:
    xml: Final = (
        '<rss><channel xmlns="urn:other"><title>Wrong</title></channel><channel><title>Right</title></channel></rss>'
    )
    result = parse_xml(xml).feed()
    assert result is not None
    assert result.title == "Right"


def test_feed_rss_channel_with_other_attribute_keeps_core_namespace() -> None:
    result = feed('<rss><channel class="x"><title>Right</title></channel></rss>')
    assert result is not None
    assert result.title == "Right"


def test_feed_rss_foreign_default_item_is_skipped() -> None:
    xml: Final = (
        '<rss><channel><item xmlns="urn:other"><title>Wrong</title></item>'
        "<item><title>Right</title></item></channel></rss>"
    )
    result = parse_xml(xml).feed()
    assert result is not None
    assert tuple(entry.title for entry in result.entries) == ("Right",)


@pytest.mark.parametrize(
    ("field", "tag"),
    [
        ("title", "title"),
        ("link", "link"),
        ("description", "description"),
        ("updated", "pubDate"),
        ("updated", "lastBuildDate"),
    ],
)
def test_feed_rss_foreign_default_channel_field_is_skipped(field: str, tag: str) -> None:
    xml: Final = f'<rss><channel><{tag} xmlns="urn:other">wrong</{tag}><{tag}>right</{tag}></channel></rss>'
    result = parse_xml(xml).feed()
    assert result is not None
    assert getattr(result, field) == "right"


@pytest.mark.parametrize(
    ("field", "tag"),
    [
        ("title", "title"),
        ("link", "link"),
        ("id", "guid"),
        ("updated", "updated"),
        ("published", "pubDate"),
        ("summary", "description"),
        ("content", "content"),
        ("author", "author"),
    ],
)
def test_feed_rss_foreign_default_item_field_is_skipped(field: str, tag: str) -> None:
    xml: Final = (
        f'<rss><channel><item><{tag} xmlns="urn:other">wrong</{tag}><{tag}>right</{tag}></item></channel></rss>'
    )
    result = parse_xml(xml).feed()
    assert result is not None
    assert getattr(result.entries[0], field) == "right"


def test_feed_rss_child_explicit_empty_namespace_is_core() -> None:
    result = feed('<rss><channel><title xmlns="">Right</title></channel></rss>')
    assert result is not None
    assert result.title == "Right"


def test_feed_rss_child_other_attribute_keeps_core_namespace() -> None:
    result = feed('<rss><channel><title xml:lang="en">Right</title></channel></rss>')
    assert result is not None
    assert result.title == "Right"


def test_feed_rss_child_five_letter_attribute_keeps_core_namespace() -> None:
    result = feed('<rss><channel><title class="x">Right</title></channel></rss>')
    assert result is not None
    assert result.title == "Right"


def test_feed_html_rss_foreign_xmlns_keeps_field() -> None:
    result = parse('<rss><channel><title xmlns="urn:other">Right</title></channel></rss>').feed()
    assert result is not None
    assert result.title == "Right"
