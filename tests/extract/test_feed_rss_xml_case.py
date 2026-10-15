from __future__ import annotations

from typing import Final

import pytest

from turbohtml import parse, parse_xml
from turbohtml.extract import feed

RDF: Final = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RSS: Final = "http://purl.org/rss/1.0/"


def test_feed_rss_xml_root_case() -> None:
    assert feed("<RSS version='2.0'><channel><title>Wrong</title></channel></RSS>") is None


def test_feed_rss_html_root_case() -> None:
    result: Final = parse("<RSS><CHANNEL><TITLE>Title</TITLE></CHANNEL></RSS>").feed()
    assert result is not None
    assert result.title == "Title"


def test_feed_rss_xml_channel_case() -> None:
    result: Final = feed(
        "<rss version='2.0'><CHANNEL><title>Wrong</title></CHANNEL><channel><title>Right</title></channel></rss>"
    )
    assert result is not None
    assert result.title == "Right"


def test_feed_rss_xml_item_case() -> None:
    result: Final = feed(
        "<rss version='2.0'><channel><ITEM><title>Wrong</title></ITEM><item><title>Right</title></item></channel></rss>"
    )
    assert result is not None
    assert tuple(entry.title for entry in result.entries) == ("Right",)


@pytest.mark.parametrize(
    ("name", "field"),
    [
        pytest.param("title", "title", id="title"),
        pytest.param("description", "description", id="description"),
        pytest.param("lastBuildDate", "updated", id="lastBuildDate"),
        pytest.param("pubDate", "updated", id="pubDate"),
    ],
)
def test_feed_rss_xml_channel_field_case(name: str, field: str) -> None:
    xml: Final = (
        f"<rss version='2.0'><channel><{name.upper()}>Wrong</{name.upper()}><{name}>Right</{name}></channel></rss>"
    )
    result: Final = feed(xml)
    assert result is not None
    assert getattr(result, field) == "Right"


def test_feed_rss_xml_channel_link_case() -> None:
    result: Final = feed("<rss version='2.0'><channel><LINK>Wrong</LINK><link>Right</link></channel></rss>")
    assert result is not None
    assert result.link == "Right"


def test_feed_rss_xml_lastbuilddate_case() -> None:
    result: Final = feed(
        "<rss version='2.0'><channel><lastbuilddate>Wrong</lastbuilddate>"
        "<lastBuildDate>Right</lastBuildDate></channel></rss>"
    )
    assert result is not None
    assert result.updated == "Right"


@pytest.mark.parametrize(
    ("name", "field"),
    [
        pytest.param("title", "title", id="title"),
        pytest.param("description", "summary", id="description"),
        pytest.param("guid", "id", id="guid"),
        pytest.param("pubDate", "published", id="pubDate"),
    ],
)
def test_feed_rss_xml_item_field_case(name: str, field: str) -> None:
    xml: Final = (
        f"<rss version='2.0'><channel><item><{name.upper()}>Wrong</{name.upper()}>"
        f"<{name}>Right</{name}></item></channel></rss>"
    )
    result: Final = feed(xml)
    assert result is not None
    assert getattr(result.entries[0], field) == "Right"


def test_feed_rss_xml_item_link_case() -> None:
    result: Final = feed(
        "<rss version='2.0'><channel><item><LINK>Wrong</LINK><link>Right</link></item></channel></rss>"
    )
    assert result is not None
    assert result.entries[0].link == "Right"


def test_feed_rdf_xml_channel_case() -> None:
    xml: Final = (
        f'<rdf:RDF xmlns:rdf="{RDF}" xmlns="{RSS}">'
        "<CHANNEL><title>Wrong</title></CHANNEL><channel><title>Right</title></channel></rdf:RDF>"
    )
    result: Final = feed(xml)
    assert result is not None
    assert result.title == "Right"


def test_feed_rdf_xml_item_case() -> None:
    xml: Final = (
        f'<rdf:RDF xmlns:rdf="{RDF}" xmlns="{RSS}">'
        "<ITEM><title>Wrong</title></ITEM><item><title>Right</title></item></rdf:RDF>"
    )
    result: Final = feed(xml)
    assert result is not None
    assert tuple(entry.title for entry in result.entries) == ("Right",)


def test_feed_rdf_xml_channel_title_case() -> None:
    xml: Final = (
        f'<rdf:RDF xmlns:rdf="{RDF}" xmlns="{RSS}">'
        "<channel><TITLE>Wrong</TITLE><title>Right</title></channel></rdf:RDF>"
    )
    result: Final = feed(xml)
    assert result is not None
    assert result.title == "Right"


def test_feed_rdf_xml_item_title_case() -> None:
    xml: Final = (
        f'<rdf:RDF xmlns:rdf="{RDF}" xmlns="{RSS}"><item><TITLE>Wrong</TITLE><title>Right</title></item></rdf:RDF>'
    )
    result: Final = feed(xml)
    assert result is not None
    assert result.entries[0].title == "Right"


def test_feed_rdf_xml_prefixed_item_title_case() -> None:
    xml: Final = (
        f'<rdf:RDF xmlns:rdf="{RDF}" xmlns:s="{RSS}">'
        "<s:item><s:TITLE>Wrong</s:TITLE><s:title>Right</s:title></s:item></rdf:RDF>"
    )
    result: Final = feed(xml)
    assert result is not None
    assert result.entries[0].title == "Right"


def test_feed_rdf_html_item_title_case() -> None:
    result: Final = parse("<rdf:RDF><ITEM><TITLE>Title</TITLE></ITEM></rdf:RDF>").feed()
    assert result is not None
    assert result.entries[0].title == "Title"


def test_feed_rdf_xml_pubdate_case() -> None:
    xml: Final = (
        f'<rdf:RDF xmlns:rdf="{RDF}" xmlns="{RSS}">'
        "<item><PUBDATE>Wrong</PUBDATE><pubDate>Right</pubDate></item></rdf:RDF>"
    )
    result: Final = parse_xml(xml).feed()
    assert result is not None
    assert result.entries[0].published == "Right"
