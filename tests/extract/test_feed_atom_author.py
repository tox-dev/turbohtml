from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest

from turbohtml.extract import feed

if TYPE_CHECKING:
    from types import ModuleType


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
    xml: Final = f'<feed xmlns="http://www.w3.org/2005/Atom"><id>urn:feed</id><title>Feed</title><updated>2020-01-01T00:00:00Z</updated>{feed_author}<entry><id>urn:entry</id><title>Entry</title><updated>2020-01-01T00:00:00Z</updated><content>Body</content>{entry_fields}</entry></feed>'
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


def test_feed_atom_entries_do_not_copy_inherited_author() -> None:
    # PyPy lacks tracemalloc and reclaims objects on a different schedule.
    tracing: Final[ModuleType] = pytest.importorskip("tracemalloc")
    contributor: Final = "Contributor " * 2_500
    entries: Final = "<entry><id>urn:entry</id></entry>" * 400
    inherited: Final = (
        f'<feed xmlns="http://www.w3.org/2005/Atom"><author><name>{contributor}</name></author>{entries}</feed>'
    )
    own: Final = (
        f'<feed xmlns="http://www.w3.org/2005/Atom"><entry><author><name>{contributor}</name></author>'
        f"<id>urn:entry</id></entry>{entries}</feed>"
    )
    result = feed(inherited)
    assert result is not None
    assert {entry.author for entry in result.entries} == {contributor.strip()}
    # A copy kept per inheriting entry would grow the peak with the entry count, while the control keeps one copy.
    assert _peak_bytes(tracing, inherited) <= 4 * _peak_bytes(tracing, own)


def _peak_bytes(tracing: ModuleType, xml: str) -> int:
    tracing.start()
    try:
        feed(xml)
        return tracing.get_traced_memory()[1]
    finally:
        tracing.stop()
