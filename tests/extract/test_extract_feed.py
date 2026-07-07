"""feed: extract.feed() normalizes RSS 2.0, Atom 1.0, and RDF/RSS-1.0 documents into one Feed of Entry records."""

from __future__ import annotations

import pytest

from turbohtml import parse
from turbohtml._feed import Entry, Feed
from turbohtml.extract import feed

RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<title>RSS Title</title>
<link>http://example.com/</link>
<description>RSS description</description>
<lastBuildDate>Mon, 06 Jul 2026 00:00:00 GMT</lastBuildDate>
<item>
  <title>Item One</title>
  <link>http://example.com/1</link>
  <guid>urn:1</guid>
  <pubDate>Sun, 05 Jul 2026 00:00:00 GMT</pubDate>
  <description>Item summary</description>
  <content:encoded>&lt;p&gt;full body&lt;/p&gt;</content:encoded>
  <author>writer@example.com</author>
</item>
<item>
  <title>Item Two</title>
  <link>http://example.com/2</link>
</item>
</channel></rss>"""

ATOM = """<feed xmlns="http://www.w3.org/2005/Atom">
<title>Atom Title</title>
<link href="http://example.com/self" rel="self"/>
<link href="http://example.com/" rel="alternate"/>
<subtitle>Atom subtitle</subtitle>
<updated>2026-07-06T00:00:00Z</updated>
<entry>
  <title>Entry One</title>
  <link href="http://example.com/e1" rel="alternate" type="text/html"/>
  <id>urn:e1</id>
  <updated>2026-07-06T01:00:00Z</updated>
  <published>2026-07-05T01:00:00Z</published>
  <summary>Entry summary</summary>
  <content type="html">&lt;p&gt;body&lt;/p&gt;</content>
  <author><name>Jane Roe</name><email>jane@example.com</email></author>
</entry>
</feed>"""

RDF = """<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/">
<channel rdf:about="http://example.com/">
<title>RDF Title</title>
<link>http://example.com/</link>
<description>RDF description</description>
<dc:date>2026-07-06</dc:date>
</channel>
<item rdf:about="http://example.com/1">
<title>RDF Item</title>
<link>http://example.com/1</link>
<description>RDF item summary</description>
<dc:date>2026-07-06</dc:date>
<dc:creator>Bob Loblaw</dc:creator>
</item>
</rdf:RDF>"""


def parse_feed(xml: str) -> Feed:
    """Parse a feed the test knows is well-formed, narrowing the ``Feed | None`` return for the assertions."""
    result = feed(xml)
    assert result is not None
    return result


def test_feed_rss_shape() -> None:
    result = parse_feed(RSS)
    assert result.type == "rss"
    assert (result.title, result.link, result.description) == ("RSS Title", "http://example.com/", "RSS description")
    assert result.updated == "Mon, 06 Jul 2026 00:00:00 GMT"
    assert result.entries == (
        Entry(
            "Item One",
            "http://example.com/1",
            "urn:1",
            None,
            "Sun, 05 Jul 2026 00:00:00 GMT",
            "Item summary",
            "<p>full body</p>",
            "writer@example.com",
        ),
        Entry("Item Two", "http://example.com/2", None, None, None, None, None, None),
    )


def test_feed_atom_shape() -> None:
    result = parse_feed(ATOM)
    assert result.type == "atom"
    assert (result.title, result.link, result.description) == ("Atom Title", "http://example.com/", "Atom subtitle")
    assert result.updated == "2026-07-06T00:00:00Z"
    assert result.entries == (
        Entry(
            "Entry One",
            "http://example.com/e1",
            "urn:e1",
            "2026-07-06T01:00:00Z",
            "2026-07-05T01:00:00Z",
            "Entry summary",
            "<p>body</p>",
            "Jane Roe",
        ),
    )


def test_feed_rdf_shape() -> None:
    result = parse_feed(RDF)
    assert result.type == "rdf"
    assert (result.title, result.link, result.description) == ("RDF Title", "http://example.com/", "RDF description")
    assert result.updated == "2026-07-06"
    assert result.entries == (
        Entry(
            "RDF Item",
            "http://example.com/1",
            "http://example.com/1",
            None,
            "2026-07-06",
            "RDF item summary",
            None,
            "Bob Loblaw",
        ),
    )


@pytest.mark.parametrize(
    ("xml", "expected"),
    [
        pytest.param("<html><body>not a feed</body></html>", None, id="html-is-not-a-feed"),
        pytest.param("<p>bare text</p>", None, id="fragment-is-not-a-feed"),
        pytest.param("", None, id="empty-is-not-a-feed"),
    ],
)
def test_feed_non_feed_returns_none(xml: str, expected: None) -> None:
    assert feed(xml) is expected


def test_feed_document_method_matches_facade() -> None:
    assert parse(RSS).feed() == feed(RSS)


def test_feed_rss_without_channel_is_empty() -> None:
    result = parse_feed('<rss version="2.0"></rss>')
    assert result == Feed("rss", None, None, None, None, ())


def test_feed_atom_self_link_only_falls_back() -> None:
    result = parse_feed('<feed><title>t</title><link href="http://example.com/self" rel="self"/></feed>')
    assert result.link == "http://example.com/self"


def test_feed_atom_two_secondary_links_no_alternate() -> None:
    xml = (
        "<feed><title>t</title>"
        '<link href="http://example.com/a" rel="self"/>'
        '<link href="http://example.com/b" rel="edit"/></feed>'
    )
    assert parse_feed(xml).link == "http://example.com/a"


def test_feed_atom_link_without_rel_is_alternate() -> None:
    result = parse_feed('<feed><title>t</title><link href="http://example.com/x"/></feed>')
    assert result.link == "http://example.com/x"


def test_feed_atom_link_valueless_rel_is_alternate() -> None:
    result = parse_feed('<feed><title>t</title><link href="http://example.com/x" rel/></feed>')
    assert result.link == "http://example.com/x"


@pytest.mark.parametrize(
    ("link_markup", "expected"),
    [
        pytest.param('<link href=""/>', None, id="empty-href-and-no-text"),
        pytest.param('<link href="   "/>', None, id="whitespace-href-and-no-text"),
        pytest.param("<link href/>", None, id="valueless-href-and-no-text"),
        pytest.param("<link>   </link>", None, id="whitespace-only-text"),
        pytest.param("<link><title>x</title>", None, id="void-link-followed-by-element"),
    ],
)
def test_feed_link_edge_cases(link_markup: str, expected: None) -> None:
    result = parse_feed(f"<rss><channel>{link_markup}</channel></rss>")
    assert result.link is expected


def test_feed_link_is_last_child() -> None:
    result = parse_feed("<rss><channel><title>t</title><link></channel></rss>")
    assert result.link is None


@pytest.mark.parametrize(
    ("guid_markup", "expected"),
    [
        pytest.param("<guid>http://example.com/g</guid>", "http://example.com/g", id="bare-guid-is-permalink"),
        pytest.param(
            '<guid isPermaLink="true">http://example.com/g</guid>', "http://example.com/g", id="permalink-true"
        ),
        pytest.param("<guid isPermaLink>http://example.com/g</guid>", "http://example.com/g", id="permalink-valueless"),
        pytest.param('<guid isPermaLink="false">urn:x</guid>', None, id="permalink-false-not-a-link"),
        pytest.param("<guid></guid>", None, id="empty-guid-not-a-link"),
    ],
)
def test_feed_guid_permalink_link_fallback(guid_markup: str, expected: str | None) -> None:
    result = parse_feed(f"<rss><channel><item><title>t</title>{guid_markup}</item></channel></rss>")
    assert result.entries[0].link == expected


def test_feed_explicit_link_beats_guid_permalink() -> None:
    xml = (
        "<rss><channel><item><title>t</title>"
        "<link>http://example.com/real</link>"
        "<guid>http://example.com/g</guid></item></channel></rss>"
    )
    assert parse_feed(xml).entries[0].link == "http://example.com/real"


def test_feed_empty_field_falls_through_to_next_source() -> None:
    xml = (
        "<rss><channel><item><title>t</title>"
        "<summary></summary><description>real summary</description></item></channel></rss>"
    )
    assert parse_feed(xml).entries[0].summary == "real summary"


def test_feed_missing_field_is_none() -> None:
    result = parse_feed("<rss><channel><item><title>only title</title></item></channel></rss>")
    entry = result.entries[0]
    assert (entry.summary, entry.content, entry.published, entry.updated, entry.author, entry.id) == (
        None,
        None,
        None,
        None,
        None,
        None,
    )


def test_feed_author_empty_falls_back_to_dc_creator() -> None:
    xml = (
        "<rss><channel><item><title>t</title>"
        "<author></author><dc:creator>Fallback Author</dc:creator></item></channel></rss>"
    )
    assert parse_feed(xml).entries[0].author == "Fallback Author"


def test_feed_rss_author_without_name_uses_own_text() -> None:
    xml = "<rss><channel><item><title>t</title><author>plain@example.com</author></item></channel></rss>"
    assert parse_feed(xml).entries[0].author == "plain@example.com"


def test_feed_atom_id_without_guid() -> None:
    result = parse_feed("<feed><title>t</title><entry><title>e</title><id>urn:only-id</id></entry></feed>")
    assert result.entries[0].id == "urn:only-id"


def test_feed_entry_without_any_id_is_none() -> None:
    result = parse_feed("<feed><title>t</title><entry><title>e</title></entry></feed>")
    assert result.entries[0].id is None


def test_feed_rdf_item_with_valueless_about_has_no_id() -> None:
    xml = (
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        "<item rdf:about><title>t</title></item></rdf:RDF>"
    )
    assert parse_feed(xml).entries[0].id is None


def test_feed_ignores_non_item_children() -> None:
    xml = "<rss><channel><title>t</title><image>logo</image><item><title>real</title></item></channel></rss>"
    result = parse_feed(xml)
    assert [entry.title for entry in result.entries] == ["real"]
