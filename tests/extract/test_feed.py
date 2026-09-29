"""feed: extract.feed() normalizes RSS 2.0, Atom 1.0, and RDF/RSS-1.0 documents into one Feed of Entry records."""

from __future__ import annotations

from typing import Final, cast

import pytest
from bench.operations import INPUTS

from turbohtml import parse, parse_xml
from turbohtml.extract import Entry, Feed, feed

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


_RSS: Final = Feed(
    "rss",
    "Example Engineering Blog",
    "https://blog.example/",
    "Notes from the Example engineering team.",
    "Tue, 07 Jul 2026 09:00:00 GMT",
    tuple(
        Entry(
            f"Release {index}: what changed",
            f"https://blog.example/posts/{index}",
            f"tag:blog.example,2026:{index}",
            None,
            "Tue, 07 Jul 2026 09:00:00 GMT",
            f"Short summary of release {index}.",
            f"<p>The full body of release {index}, with details.</p>",
            "A. Writer",
        )
        for index in range(30)
    ),
)


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        pytest.param(0, _RSS, id="rss"),
        pytest.param(1, _RSS, id="rss-extensions"),
        pytest.param(
            2,
            Feed(
                "atom",
                "Example",
                None,
                None,
                None,
                tuple(
                    Entry(
                        f"Entry {index}",
                        f"https://example.com/{index}",
                        f"urn:{index}",
                        "2026-07-06",
                        None,
                        f"Summary {index}",
                        "Full body",
                        "Writer",
                    )
                    for index in range(30)
                ),
            ),
            id="atom",
        ),
    ],
)
def test_feed_benchmark_output(case: int, expected: Feed) -> None:
    assert feed(cast("str", INPUTS["syndication"]()[case][1])) == expected


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        pytest.param(
            "<dc:creator>creator</dc:creator><author>author</author><content>plain</content>"
            "<content:encoded>encoded</content:encoded><dc:description>dc</dc:description>"
            "<description>description</description><summary>summary</summary><dc:date>dc-date</dc:date>"
            "<pubdate>pubdate</pubdate><published>published</published><lastbuilddate>last</lastbuilddate>"
            '<updated>updated</updated><id>id</id><guid isPermaLink="false">guid</guid><title>title</title>',
            ("title", None, "guid", "updated", "published", "summary", "encoded", "author"),
            id="field-name-precedence",
        ),
        pytest.param(
            "<title> </title><title>later</title><summary></summary><summary>later</summary>"
            "<description>fallback</description>",
            (None, None, None, None, None, "fallback", None, None),
            id="first-empty-occurrence",
        ),
        pytest.param(
            "<guid></guid><guid>later</guid><id>identifier</id>",
            (None, None, "identifier", None, None, None, None, None),
            id="first-guid-controls-fallback",
        ),
        pytest.param(
            '<link href="self" rel="self"/><link href="first" rel="alternate"/><link href="second"/>',
            (None, "first", None, None, None, None, None, None),
            id="first-alternate-link",
        ),
        pytest.param(
            '<link>rss-link</link><link href="alternate" rel="alternate"/>',
            (None, "rss-link", None, None, None, None, None, None),
            id="rss-text-before-atom-link",
        ),
        pytest.param(
            "<author><name></name>ignored</author><author>later</author><dc:creator>creator</dc:creator>",
            (None, None, None, None, None, None, None, "creator"),
            id="nested-empty-author-name",
        ),
        pytest.param(
            "text<!--comment--><extension><title>nested</title></extension><updatxx>fake</updatxx>"
            "<xxxxxxx>ignored</xxxxxxx><x>ignored</x><title>direct</title>",
            ("direct", None, None, None, None, None, None, None),
            id="only-direct-exact-tags",
        ),
    ],
)
def test_entry_field_precedence(fields: str, expected: tuple[str | None, ...]) -> None:
    result: Final = feed(f"<rss><channel><item>{fields}</item></channel></rss>")
    assert result is not None
    assert result.entries == (expected,)


def test_feed_empty_metadata_uses_fallback() -> None:
    assert feed(
        "<rss><channel><title> </title><description> </description><subtitle>fallback</subtitle>"
        "<updated> </updated><lastbuilddate>date</lastbuilddate></channel></rss>"
    ) == ("rss", None, None, "fallback", "date", ())


@pytest.mark.parametrize("case", [0, 1, 2], ids=["rss", "rss-extensions", "atom"])
@pytest.mark.oracle
def test_feed_benchmark_entry_values(case: int) -> None:
    feedparser: Final = pytest.importorskip("feedparser")
    source: Final = cast("str", INPUTS["syndication"]()[case][1])
    oracle: Final = feedparser.parse(source)
    entries: Final = tuple(
        Entry(
            cast("str | None", entry.get("title")),
            cast("str | None", entry.get("link")),
            cast("str | None", entry.get("id")),
            cast("str | None", entry.get("updated")),
            cast("str | None", entry.get("published")),
            cast("str | None", entry.get("summary")),
            cast("list[dict[str, str]]", entry.get("content", [{"value": ""}]))[0]["value"],
            cast("str | None", entry.get("author")),
        )
        for entry in map(dict, oracle.entries)
    )
    header: Final = dict(oracle.feed)
    assert feed(source) == Feed(
        "atom" if oracle.version.startswith("atom") else "rss",
        cast("str | None", header.get("title")),
        cast("str | None", header.get("link")),
        cast("str | None", header.get("subtitle")),
        cast("str | None", header.get("updated")),
        entries,
    )


XML_RSS: Final = """<?xml version="1.0"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel>
<image><title>Logo title</title><url>https://example.com/logo.png</url><link>https://example.com/logo</link></image>
<title>Feed title</title>
<link>https://example.com/</link>
<item>
<title>Item</title>
<link>https://example.com/1</link>
<guid isPermaLink="false">urn:1</guid>
<description><![CDATA[<p>hi <a href="/x">x</a></p>]]></description>
<content:encoded><![CDATA[<p>full</p>]]></content:encoded>
</item>
</channel></rss>"""

XML_RSS_FEED: Final = Feed(
    "rss",
    "Feed title",
    "https://example.com/",
    None,
    None,
    (Entry("Item", "https://example.com/1", "urn:1", None, None, '<p>hi <a href="/x">x</a></p>', "<p>full</p>", None),),
)


def test_feed_well_formed_rss_parses_as_xml() -> None:
    assert feed(XML_RSS) == XML_RSS_FEED


def test_feed_xml_document_method() -> None:
    assert parse_xml(XML_RSS).feed() == XML_RSS_FEED


def test_feed_xml_guid_permalink_false_is_not_a_link() -> None:
    xml = '<rss><channel><item><guid isPermaLink="false">urn:x</guid></item></channel></rss>'
    assert parse_xml(xml).feed() == Feed(
        "rss", None, None, None, None, (Entry(None, None, "urn:x", None, None, None, None, None),)
    )


def test_feed_atom_summary_cdata() -> None:
    xml = '<feed xmlns="http://www.w3.org/2005/Atom"><entry><summary><![CDATA[<b>s</b>]]></summary></entry></feed>'
    assert feed(xml) == Feed(
        "atom", None, None, None, None, (Entry(None, None, None, None, None, "<b>s</b>", None, None),)
    )


def test_feed_malformed_xml_falls_back_to_html() -> None:
    assert feed("<rss><channel><title>T&nbsp;U</title><link>https://example.com/</channel>") == Feed(
        "rss", "T\xa0U", "https://example.com/", None, None, ()
    )


def test_feed_field_text_spans_nested_elements() -> None:
    xml = "<rss><channel><item><description>a <b>bold</b> c</description></item></channel></rss>"
    assert feed(xml) == Feed(
        "rss", None, None, None, None, (Entry(None, None, None, None, None, "a bold c", None, None),)
    )
