"""Atom entry author inheritance."""

from __future__ import annotations

from typing import Final

import pytest

from turbohtml.extract import feed


@pytest.mark.parametrize(
    ("feed_author", "entry_fields", "expected"),
    [
        pytest.param("<author><name>Feed</name></author>", "", "Feed", id="feed-author"),
        pytest.param("", "<source><author><name>Source</name></author></source>", "Source", id="source-author"),
        pytest.param(
            "<author><name>Feed</name></author>",
            "<source><author><name>Source</name></author></source>",
            "Source",
            id="source-before-feed",
        ),
        pytest.param(
            "<author><name>Feed</name></author>",
            "<source><author><name>Source</name></author></source><author><name>Entry</name></author>",
            "Entry",
            id="entry-before-source",
        ),
        pytest.param(
            "<author><name>Feed</name></author>",
            "<source><title>Original feed</title></source>",
            "Feed",
            id="source-without-author",
        ),
        pytest.param(
            "<author><name>Feed</name></author>",
            "<source><author><name> </name></author></source>",
            "Feed",
            id="empty-source-author",
        ),
        pytest.param("", "<source><title>Original feed</title></source>", None, id="no-author"),
        pytest.param(
            "<author><name>Feed</name></author>",
            '<source><foreign:author xmlns:foreign="urn:other"><foreign:name>Wrong</foreign:name>'
            "</foreign:author></source>",
            "Feed",
            id="foreign-source-author",
        ),
        pytest.param(
            '<foreign:author xmlns:foreign="urn:other"><foreign:name>Wrong</foreign:name></foreign:author>',
            "",
            None,
            id="foreign-feed-author",
        ),
    ],
)
def test_feed_atom_entry_author_inheritance(feed_author: str, entry_fields: str, expected: str | None) -> None:
    xml: Final = (
        '<feed xmlns="http://www.w3.org/2005/Atom"><id>urn:feed</id><title>Feed</title>'
        "<updated>2020-01-01T00:00:00Z</updated>"
        + feed_author
        + "<entry><id>urn:entry</id><title>Entry</title><updated>2020-01-01T00:00:00Z</updated>"
        "<content>Body</content>" + entry_fields + "</entry></feed>"
    )
    result = feed(xml)
    assert result is not None
    assert result.entries[0].author == expected


def test_feed_atom_entry_author_prefixed_source() -> None:
    xml: Final = (
        '<a:feed xmlns:a="http://www.w3.org/2005/Atom"><a:entry><a:source>'
        "<a:author><a:name>Source</a:name></a:author></a:source></a:entry></a:feed>"
    )
    result = feed(xml)
    assert result is not None
    assert result.entries[0].author == "Source"


@pytest.mark.parametrize(
    "entry_attributes",
    [
        pytest.param("", id="entry-inherits-foreign"),
        pytest.param(' xmlns="http://www.w3.org/2005/Atom"', id="entry-restores-atom"),
    ],
)
def test_feed_atom_entry_inherits_prefixed_feed_author_namespace(entry_attributes: str) -> None:
    xml: Final = (
        '<a:feed xmlns:a="http://www.w3.org/2005/Atom" xmlns="urn:other">'
        "<a:author><name>Wrong</name><a:name>Right</a:name></a:author>"
        f"<a:entry{entry_attributes}><a:title>Entry</a:title></a:entry></a:feed>"
    )
    result = feed(xml)
    assert result is not None
    assert result.entries[0].author == "Right"


def test_feed_atom_entry_inherits_feed_author_restored_default_namespace() -> None:
    xml: Final = (
        '<a:feed xmlns:a="http://www.w3.org/2005/Atom" xmlns="urn:other">'
        '<a:author xmlns="http://www.w3.org/2005/Atom"><name>Right</name></a:author>'
        "<a:entry><a:title>Entry</a:title></a:entry></a:feed>"
    )
    result = feed(xml)
    assert result is not None
    assert result.entries[0].author == "Right"
