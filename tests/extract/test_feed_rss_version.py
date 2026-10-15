"""RSS 2.0 root version checks in XML and HTML trees."""

from __future__ import annotations

from typing import Final

import pytest

from turbohtml import parse, parse_xml
from turbohtml.extract import feed


@pytest.mark.parametrize(
    "root",
    [
        "<rss>",
        '<rss version="">',
        '<rss version="0.91">',
        '<rss version="2.1">',
        '<rss version="9.9">',
        '<rss VERSION="2.0">',
        '<rss xmlns:x="urn:other" x:version="2.0">',
    ],
    ids=["missing", "empty", "old-version", "new-version", "unknown-version", "uppercase-name", "prefixed-name"],
)
def test_feed_rss_xml_root_requires_version_2(root: str) -> None:
    assert feed(f"{root}<channel><title>Wrong</title></channel></rss>") is None


@pytest.mark.parametrize(
    "root",
    ['<rss version="2.0">', '<rss xmlns:x="urn:other" version="2.0">'],
    ids=["first-attribute", "later-attribute"],
)
def test_feed_rss_xml_root_accepts_version_2(root: str) -> None:
    result: Final = feed(f"{root}<channel><title>Right</title></channel></rss>")
    assert result is not None
    assert result.title == "Right"


def test_feed_rss_xml_document_method_requires_version_2() -> None:
    assert parse_xml("<rss><channel><title>Wrong</title></channel></rss>").feed() is None


def test_feed_html_root_without_version_is_recognized() -> None:
    result: Final = parse("<main><rss><channel><title>Right</title></channel></rss></main>").feed()
    assert result is not None
    assert result.title == "Right"
