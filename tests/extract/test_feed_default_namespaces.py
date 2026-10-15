"""Default namespace boundaries in RSS and Atom feed extraction."""

from __future__ import annotations

from typing import Final

import pytest

from turbohtml import parse, parse_xml
from turbohtml.extract import feed

ATOM: Final = "http://www.w3.org/2005/Atom"
RDF: Final = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RSS: Final = "http://purl.org/rss/1.0/"
DC: Final = "http://purl.org/dc/elements/1.1/"


@pytest.mark.parametrize("uri", ["urn:other", "http://www.w3.org/2005/Xtom"])
def test_feed_foreign_default_namespace_is_not_atom_root(uri: str) -> None:
    assert feed(f'<feed xmlns="{uri}"><title>Wrong</title></feed>') is None


@pytest.mark.parametrize(
    "xml",
    [
        "<feed><title>Wrong</title></feed>",
        '<feed xmlns=""><title>Wrong</title></feed>',
        f'<feed xmlns:a="{ATOM}"><title>Wrong</title></feed>',
    ],
    ids=["missing-default", "empty-default", "unrelated-prefix"],
)
def test_feed_atom_root_requires_atom_namespace(xml: str) -> None:
    assert feed(xml) is None


def test_feed_prefixed_atom_root_is_recognized() -> None:
    result = feed(f'<a:feed xmlns:a="{ATOM}"><a:title>Right</a:title></a:feed>')
    assert result is not None
    assert result.title == "Right"


@pytest.mark.parametrize(
    ("field", "wrong", "right"),
    [
        ("title", '<title xmlns="urn:other">wrong</title>', "<title>right</title>"),
        ("description", '<subtitle xmlns="urn:other">wrong</subtitle>', "<subtitle>right</subtitle>"),
        ("updated", '<updated xmlns="urn:other">wrong</updated>', "<updated>right</updated>"),
        ("link", '<link xmlns="urn:other" href="wrong"/>', '<link href="right"/>'),
    ],
)
def test_feed_atom_foreign_default_field_is_skipped(field: str, wrong: str, right: str) -> None:
    xml: Final = f'<feed xmlns="{ATOM}">{wrong}{right}</feed>'
    result = parse_xml(xml).feed()
    assert result is not None
    assert getattr(result, field) == "right"


def test_feed_atom_same_length_foreign_field_is_skipped() -> None:
    xml: Final = (
        f'<feed xmlns="{ATOM}"><title xmlns="http://www.w3.org/2005/Xtom">Wrong</title><title>Right</title></feed>'
    )
    result = parse_xml(xml).feed()
    assert result is not None
    assert result.title == "Right"


def test_feed_atom_foreign_default_entry_is_skipped() -> None:
    xml: Final = (
        f'<feed xmlns="{ATOM}"><entry xmlns="urn:other"><title>Wrong</title></entry>'
        "<entry><title>Right</title></entry></feed>"
    )
    result = parse_xml(xml).feed()
    assert result is not None
    assert tuple(entry.title for entry in result.entries) == ("Right",)


def test_feed_atom_child_restores_default_namespace() -> None:
    xml: Final = (
        f'<feed xmlns="{ATOM}" xmlns:a="{ATOM}">'
        f'<a:entry xmlns="urn:other"><title xmlns="{ATOM}">Right</title></a:entry></feed>'
    )
    result = parse_xml(xml).feed()
    assert result is not None
    assert result.entries[0].title == "Right"


def test_feed_atom_field_restores_default_namespace() -> None:
    xml: Final = (
        f'<a:feed xmlns:a="{ATOM}" xmlns="urn:other"><title>wrong</title><title xmlns="{ATOM}">Right</title></a:feed>'
    )
    result = parse_xml(xml).feed()
    assert result is not None
    assert result.title == "Right"


@pytest.mark.parametrize(
    ("field", "tag"),
    [
        ("title", "title"),
        ("summary", "summary"),
        ("content", "content"),
        ("updated", "updated"),
        ("published", "published"),
        ("id", "id"),
    ],
)
def test_feed_atom_foreign_default_entry_field_is_skipped(field: str, tag: str) -> None:
    xml: Final = (
        f'<feed xmlns="{ATOM}"><entry><{tag} xmlns="urn:other">wrong</{tag}><{tag}>right</{tag}></entry></feed>'
    )
    result = parse_xml(xml).feed()
    assert result is not None
    assert getattr(result.entries[0], field) == "right"


def test_feed_atom_foreign_default_author_name_is_skipped() -> None:
    xml: Final = (
        f'<feed xmlns="{ATOM}"><entry><author><name xmlns="urn:other">wrong</name>'
        "<name>right</name></author></entry></feed>"
    )
    result = parse_xml(xml).feed()
    assert result is not None
    assert result.entries[0].author == "right"


def test_feed_atom_field_with_other_attribute_inherits_default_namespace() -> None:
    result = parse_xml(f'<feed xmlns="{ATOM}"><title class="x">Right</title></feed>').feed()
    assert result is not None
    assert result.title == "Right"


def test_feed_html_fallback_keeps_unprefixed_fields() -> None:
    result = parse("<feed><title>Feed</title><entry><title>Entry</title></entry>").feed()
    assert result is not None
    assert result.entries[0].title == "Entry"


def test_feed_rdf_foreign_default_channel_is_skipped() -> None:
    xml: Final = (
        f'<rdf:RDF xmlns:rdf="{RDF}" xmlns="{RSS}">'
        '<channel xmlns="urn:other"><title>Wrong</title></channel>'
        "<channel><title>Right</title></channel></rdf:RDF>"
    )
    result = parse_xml(xml).feed()
    assert result is not None
    assert result.title == "Right"


def test_feed_rdf_foreign_default_item_is_skipped() -> None:
    xml: Final = (
        f'<rdf:RDF xmlns:rdf="{RDF}" xmlns="{RSS}">'
        '<item xmlns="urn:other"><title>Wrong</title></item>'
        "<item><title>Right</title></item></rdf:RDF>"
    )
    result = parse_xml(xml).feed()
    assert result is not None
    assert tuple(entry.title for entry in result.entries) == ("Right",)


def test_feed_rdf_foreign_default_item_field_is_skipped() -> None:
    xml: Final = (
        f'<rdf:RDF xmlns:rdf="{RDF}" xmlns="{RSS}"><item>'
        '<title xmlns="urn:other">wrong</title><title>right</title>'
        "</item></rdf:RDF>"
    )
    result = parse_xml(xml).feed()
    assert result is not None
    assert result.entries[0].title == "right"


def test_feed_first_dublin_core_date_wins() -> None:
    xml: Final = (
        f'<rss xmlns:dc="{DC}"><channel><item><dc:date>first</dc:date><dc:date>second</dc:date></item></channel></rss>'
    )
    result = parse_xml(xml).feed()
    assert result is not None
    assert result.entries[0].published == "first"
