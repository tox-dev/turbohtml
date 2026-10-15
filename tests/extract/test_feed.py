"""feed: extract.feed() normalizes RSS 2.0, Atom 1.0, and RDF/RSS-1.0 documents into one Feed of Entry records."""

from __future__ import annotations

from typing import Final, cast

import pytest
from bench.operations import INPUTS

from turbohtml import Text, parse, parse_xml
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

ATOM_PREFIXED: Final = """<a:feed xmlns:a="http://www.w3.org/2005/Atom">
<a:title>Feed</a:title><a:link href="https://example.org/"/><a:subtitle>Subtitle</a:subtitle>
<a:updated>2026-07-06</a:updated><a:entry><a:id>urn:entry</a:id><a:title>Entry</a:title>
<a:link href="https://example.org/entry"/><a:summary>Summary</a:summary><a:content>Body</a:content>
<a:author><a:name>Alice</a:name></a:author></a:entry></a:feed>"""

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

RDF_PREFIXED: Final = """<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:s="http://purl.org/rss/1.0/">
<s:channel><s:title>Feed</s:title><s:link>https://example.org/</s:link><s:description>Summary</s:description></s:channel>
<s:item rdf:about="https://example.org/item"><s:title>Item</s:title><s:link>https://example.org/item</s:link>
<s:description>Entry summary</s:description></s:item></rdf:RDF>"""


def parse_feed(xml: str) -> Feed:
    """Parse a feed the test knows is well-formed, narrowing the ``Feed | None`` return for the assertions."""
    result = feed(xml)
    assert result is not None
    return result


def test_feed_rss_module_prefix_aliases() -> None:
    xml: Final = (
        '<rss xmlns:x="http://purl.org/rss/1.0/modules/content/" '
        'xmlns:d="http://purl.org/dc/elements/1.1/"><channel>'
        '<d:date>updated</d:date><item id="entry"><d:date>published</d:date>'
        "<d:description>summary</d:description><d:creator>author</d:creator>"
        "<x:encoded>body</x:encoded></item></channel></rss>"
    )
    assert parse_feed(xml) == Feed(
        "rss", None, None, None, "updated", (Entry(None, None, None, None, "published", "summary", "body", "author"),)
    )


@pytest.mark.parametrize(
    ("local", "uri", "field"),
    [
        ("date", "http://purl.org/dc/elements/1.1/", "published"),
        ("description", "http://purl.org/dc/elements/1.1/", "summary"),
        ("creator", "http://purl.org/dc/elements/1.1/", "author"),
        ("encoded", "http://purl.org/rss/1.0/modules/content/", "content"),
    ],
)
def test_feed_rss_default_module_namespace(local: str, uri: str, field: str) -> None:
    xml: Final = f'<rss><channel><item><{local} xmlns="{uri}">value</{local}></item></channel></rss>'
    assert getattr(parse_feed(xml).entries[0], field) == "value"


def test_feed_rss_inherited_default_module_namespace() -> None:
    xml: Final = (
        '<rss><channel><item xmlns="http://purl.org/dc/elements/1.1/"><date>published</date></item></channel></rss>'
    )
    assert parse_feed(xml).entries[0].published == "published"


def test_feed_rss_channel_default_dc_date() -> None:
    xml: Final = '<rss><channel><date xmlns="http://purl.org/dc/elements/1.1/">updated</date></channel></rss>'
    assert parse_feed(xml).updated == "updated"


def test_feed_rss_default_module_namespace_after_other_attribute() -> None:
    xml: Final = (
        '<rss><channel><item><date title="other" xmlns="http://purl.org/dc/elements/1.1/">'
        "published</date></item></channel></rss>"
    )
    assert parse_feed(xml).entries[0].published == "published"


def test_feed_rss_shadowed_default_module_namespace() -> None:
    xml: Final = (
        '<rss><channel><item xmlns="http://purl.org/dc/elements/1.1/">'
        '<date xmlns="urn:other">wrong</date></item></channel></rss>'
    )
    assert parse_feed(xml).entries[0].published is None


def test_feed_rss_default_dc_description_precedence() -> None:
    xml: Final = (
        '<rss><channel><item><description xmlns="http://purl.org/dc/elements/1.1/">dc</description>'
        "<description>rss</description></item></channel></rss>"
    )
    assert parse_feed(xml).entries[0].summary == "rss"


def test_feed_rss_first_default_dc_description_wins() -> None:
    xml: Final = (
        '<rss><channel><item><description xmlns="http://purl.org/dc/elements/1.1/">first</description>'
        '<description xmlns="http://purl.org/dc/elements/1.1/">second</description></item></channel></rss>'
    )
    assert parse_feed(xml).entries[0].summary == "first"


def test_feed_rss_module_prefix_shadowing() -> None:
    xml: Final = (
        '<rss xmlns:d="http://purl.org/dc/elements/1.1/"><channel><item '
        'xmlns:d="urn:other"><d:creator>wrong</d:creator></item></channel></rss>'
    )
    assert parse_feed(xml).entries[0].author is None


def test_feed_rss_module_prefix_shadowing_after_cached_binding() -> None:
    xml: Final = (
        '<rss xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><item>'
        "<dc:date>published</dc:date>"
        '<dc:creator xmlns:dc="urn:other">wrong</dc:creator>'
        "</item></channel></rss>"
    )
    assert parse_feed(xml).entries[0].author is None


def test_feed_rss_wrong_content_namespace() -> None:
    xml: Final = (
        '<rss xmlns:content="urn:other"><channel><item><content:encoded>wrong</content:encoded></item></channel></rss>'
    )
    assert parse_feed(xml).entries[0].content is None


def test_feed_rss_namespace_uri_is_case_sensitive() -> None:
    xml: Final = (
        '<rss xmlns:d="HTTP://purl.org/dc/elements/1.1/"><channel><item>'
        "<d:creator>wrong</d:creator></item></channel></rss>"
    )
    assert parse_feed(xml).entries[0].author is None


def test_feed_rss_first_namespaced_field_wins() -> None:
    xml: Final = (
        '<rss xmlns:a="http://purl.org/dc/elements/1.1/" '
        'xmlns:b="http://purl.org/dc/elements/1.1/"><channel><item>'
        "<a:creator>first</a:creator><b:creator>second</b:creator></item></channel></rss>"
    )
    assert parse_feed(xml).entries[0].author == "first"


def test_feed_rss_removed_namespace_declaration() -> None:
    document: Final = parse_xml(
        '<rss xmlns:d="http://purl.org/dc/elements/1.1/"><channel><item>'
        "<d:creator>author</d:creator></item></channel></rss>"
    )
    del document.select("rss")[0].attrs["xmlns:d"]
    assert cast("Feed", document.feed()).entries[0].author is None


def test_feed_rss_valueless_namespace_declaration() -> None:
    document: Final = parse_xml(
        '<rss xmlns:d="http://purl.org/dc/elements/1.1/"><channel><item>'
        "<d:creator>author</d:creator></item></channel></rss>"
    )
    document.select("rss")[0].attrs["xmlns:d"] = None
    assert cast("Feed", document.feed()).entries[0].author is None


def test_feed_rss_unicode_module_prefix() -> None:
    xml: Final = (
        '<rss xmlns:é="http://purl.org/rss/1.0/modules/content/"><channel><item>'
        "<é:encoded>body</é:encoded></item></channel></rss>"
    )
    assert parse_feed(xml).entries[0].content == "body"


def test_feed_rss_unicode_module_prefix_after_other_declarations() -> None:
    xml: Final = (
        '<rss xmlns:éé="urn:other" xmlns:é="urn:other" '
        'xmlns:ø="http://purl.org/rss/1.0/modules/content/"><channel><item>'
        "<ø:encoded>body</ø:encoded></item></channel></rss>"
    )
    assert parse_feed(xml).entries[0].content == "body"


def test_feed_rss_canonical_module_names() -> None:
    xml: Final = (
        '<rss xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><item>'
        "<dc:date>published</dc:date><dc:description>summary</dc:description>"
        "<dc:creator>author</dc:creator><content:encoded>body</content:encoded>"
        "</item></channel></rss>"
    )
    assert parse_feed(xml).entries[0] == Entry(None, None, None, None, "published", "summary", "body", "author")


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


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("title", "Feed"),
        ("link", "https://example.org/"),
        ("description", "Subtitle"),
        ("updated", "2026-07-06"),
    ],
)
def test_feed_atom_prefixed_feed_fields(field: str, expected: str) -> None:
    assert getattr(parse_feed(ATOM_PREFIXED), field) == expected


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("id", "urn:entry"),
        ("title", "Entry"),
        ("link", "https://example.org/entry"),
        ("summary", "Summary"),
        ("content", "Body"),
        ("author", "Alice"),
    ],
)
def test_feed_atom_prefixed_entry_fields(field: str, expected: str) -> None:
    assert getattr(parse_feed(ATOM_PREFIXED).entries[0], field) == expected


def test_feed_atom_wrong_prefixed_root_namespace() -> None:
    xml: Final = '<a:feed xmlns:a="urn:other"><a:title>wrong</a:title></a:feed>'
    assert feed(xml) is None


def test_feed_atom_unicode_prefix() -> None:
    xml: Final = '<é:feed xmlns:é="http://www.w3.org/2005/Atom"><é:entry><é:title>Entry</é:title></é:entry></é:feed>'
    assert parse_feed(xml).entries[0].title == "Entry"


def test_feed_atom_entry_uses_other_prefix() -> None:
    xml: Final = (
        '<a:feed xmlns:a="http://www.w3.org/2005/Atom" xmlns:b="http://www.w3.org/2005/Atom">'
        "<b:entry><b:title>Entry</b:title></b:entry></a:feed>"
    )
    assert parse_feed(xml).entries[0].title == "Entry"


def test_feed_atom_wrong_prefixed_entry_namespace() -> None:
    xml: Final = (
        '<a:feed xmlns:a="http://www.w3.org/2005/Atom" xmlns:b="urn:other">'
        "<b:entry><b:title>wrong</b:title></b:entry><a:entry><a:title>right</a:title></a:entry></a:feed>"
    )
    assert [entry.title for entry in parse_feed(xml).entries] == ["right"]


def test_feed_atom_prefixed_author_name_namespace_shadowing() -> None:
    xml: Final = (
        '<a:feed xmlns:a="http://www.w3.org/2005/Atom"><a:entry><a:author>'
        '<a:name xmlns:a="urn:other">wrong</a:name><a:name>right</a:name>'
        "</a:author></a:entry></a:feed>"
    )
    assert parse_feed(xml).entries[0].author == "right"


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


def test_feed_rdf_namespace_aliases() -> None:
    xml: Final = (
        '<r:RDF xmlns:r="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
        'xmlns="http://purl.org/rss/1.0/"><channel><title>Feed</title></channel>'
        '<item r:about="https://example.org/item"><title>Item</title></item></r:RDF>'
    )
    assert parse_feed(xml) == Feed(
        "rdf",
        "Feed",
        None,
        None,
        None,
        (Entry("Item", None, "https://example.org/item", None, None, None, None, None),),
    )


def test_feed_rdf_wrong_root_namespace() -> None:
    xml: Final = '<rdf:RDF xmlns:rdf="urn:other"><channel><title>Feed</title></channel></rdf:RDF>'
    assert feed(xml) is None


def test_feed_rdf_wrong_about_namespace() -> None:
    xml: Final = (
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
        'xmlns:r="urn:other"><item r:about="wrong"><title>Item</title></item></rdf:RDF>'
    )
    assert parse_feed(xml).entries[0].id is None


def test_feed_rdf_about_namespace_shadowing() -> None:
    xml: Final = (
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<item xmlns:rdf="urn:other" rdf:about="wrong"><title>Item</title></item></rdf:RDF>'
    )
    assert parse_feed(xml).entries[0].id is None


def test_feed_rdf_unicode_about_prefix() -> None:
    xml: Final = (
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
        'xmlns:é="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<item é:about="https://example.org/item"><title>Item</title></item></rdf:RDF>'
    )
    assert parse_feed(xml).entries[0].id == "https://example.org/item"


def test_feed_rdf_removed_about_namespace() -> None:
    document: Final = parse_xml(
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<item xmlns:r="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
        'xmlns:q="urn:other" r:about="wrong" /></rdf:RDF>'
    )
    del document.select("item")[0].attrs["xmlns:r"]
    assert cast("Feed", document.feed()).entries[0].id is None


def test_feed_rdf_valueless_about() -> None:
    document: Final = parse_xml(
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><item rdf:about="wrong" /></rdf:RDF>'
    )
    document.select("item")[0].attrs["rdf:about"] = None
    assert cast("Feed", document.feed()).entries[0].id is None


def test_feed_rdf_other_prefixed_attribute_before_about() -> None:
    xml: Final = (
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<item rdf:other="wrong" rdf:about="https://example.org/item" /></rdf:RDF>'
    )
    assert parse_feed(xml).entries[0].id == "https://example.org/item"


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("title", "Feed"),
        ("link", "https://example.org/"),
        ("description", "Summary"),
    ],
)
def test_feed_rdf_prefixed_channel_fields(field: str, expected: str) -> None:
    assert getattr(parse_feed(RDF_PREFIXED), field) == expected


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("title", "Item"),
        ("link", "https://example.org/item"),
        ("id", "https://example.org/item"),
        ("summary", "Entry summary"),
    ],
)
def test_feed_rdf_prefixed_item_fields(field: str, expected: str) -> None:
    assert getattr(parse_feed(RDF_PREFIXED).entries[0], field) == expected


def test_feed_rdf_prefixed_item_namespace_shadowing() -> None:
    xml: Final = (
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:s="http://purl.org/rss/1.0/">'
        '<s:item xmlns:s="urn:other"><s:title>wrong</s:title></s:item>'
        "<s:item><s:title>right</s:title></s:item></rdf:RDF>"
    )
    assert tuple(entry.title for entry in parse_feed(xml).entries) == ("right",)


def test_feed_rdf_prefixed_field_namespace_shadowing() -> None:
    xml: Final = (
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:s="http://purl.org/rss/1.0/">'
        '<s:item><s:title xmlns:s="urn:other">wrong</s:title><s:title>right</s:title></s:item></rdf:RDF>'
    )
    assert parse_feed(xml).entries[0].title == "right"


def test_feed_rdf_first_prefixed_field_wins() -> None:
    xml: Final = (
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:s="http://purl.org/rss/1.0/">'
        "<s:item><s:title>first</s:title><s:title>second</s:title></s:item></rdf:RDF>"
    )
    assert parse_feed(xml).entries[0].title == "first"


def test_feed_rdf_wrong_prefixed_local_name() -> None:
    xml: Final = (
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:s="http://purl.org/rss/1.0/">'
        "<s:iteX/><s:item><s:title>right</s:title></s:item></rdf:RDF>"
    )
    assert tuple(entry.title for entry in parse_feed(xml).entries) == ("right",)


def test_feed_rdf_html_fallback_keeps_literal_names() -> None:
    document: Final = parse("<rdf:rdf><s:item><s:title>wrong</s:title></s:item></rdf:rdf>")
    result: Final = document.feed()
    assert result is not None
    assert result.entries == ()


def test_feed_rdf_unicode_rss_prefix() -> None:
    xml: Final = (
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:é="http://purl.org/rss/1.0/">'
        "<é:channel><é:title>Feed</é:title></é:channel><é:item><é:title>Item</é:title></é:item></rdf:RDF>"
    )
    assert parse_feed(xml).entries[0].title == "Item"


def test_feed_rdf_first_channel_across_rss_prefixes() -> None:
    xml: Final = (
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
        'xmlns:s="http://purl.org/rss/1.0/" xmlns="http://purl.org/rss/1.0/">'
        "<s:channel><s:title>first</s:title></s:channel>"
        "<channel><title>second</title></channel></rdf:RDF>"
    )
    assert parse_feed(xml).title == "first"


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


def test_feed_atom_self_link_is_not_permalink() -> None:
    result = parse_feed(
        '<feed xmlns="http://www.w3.org/2005/Atom"><title>t</title>'
        '<link href="http://example.com/self" rel="self"/></feed>'
    )
    assert result.link is None


def test_feed_atom_two_secondary_links_no_permalink() -> None:
    xml = (
        '<feed xmlns="http://www.w3.org/2005/Atom"><title>t</title>'
        '<link href="http://example.com/a" rel="self"/>'
        '<link href="http://example.com/b" rel="edit"/></feed>'
    )
    assert parse_feed(xml).link is None


def test_feed_atom_link_without_rel_is_alternate() -> None:
    result = parse_feed(
        '<feed xmlns="http://www.w3.org/2005/Atom"><title>t</title><link href="http://example.com/x"/></feed>'
    )
    assert result.link == "http://example.com/x"


def test_feed_atom_link_valueless_rel_is_not_alternate() -> None:
    result = parse_feed(
        '<feed xmlns="http://www.w3.org/2005/Atom"><title>t</title><link href="http://example.com/x" rel/></feed>'
    )
    assert result.link is None


@pytest.mark.parametrize(
    ("links", "expected"),
    [
        pytest.param('<link href="first"/>', "first", id="missing-rel"),
        pytest.param('<link href="first" rel/>', "first", id="valueless-rel"),
        pytest.param(
            '<link rel="self" href="first"/><link rel="edit" href="second"/>',
            "first",
            id="first-secondary-fallback",
        ),
        pytest.param(
            '<link rel="self" href="first"/><link rel="ALTERNATE" href="second"/>',
            "second",
            id="uppercase-alternate",
        ),
    ],
)
def test_feed_rss_link_href_relations(links: str, expected: str) -> None:
    assert parse_feed(f"<rss><channel>{links}</channel></rss>").link == expected


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
    result = parse_feed(
        '<feed xmlns="http://www.w3.org/2005/Atom"><title>t</title><entry><title>e</title><id>urn:only-id</id></entry></feed>'
    )
    assert result.entries[0].id == "urn:only-id"


def test_feed_entry_without_any_id_is_none() -> None:
    result = parse_feed(
        '<feed xmlns="http://www.w3.org/2005/Atom"><title>t</title><entry><title>e</title></entry></feed>'
    )
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


def test_feed_atom_nested_xml_bases_resolve_links() -> None:
    xml = (
        '<feed xmlns="http://www.w3.org/2005/Atom" xml:base="https://example.org/a/">'
        '<link href="b"/><entry xml:base="../posts/"><link href="c#part"/></entry></feed>'
    )
    result = parse_feed(xml)
    assert (result.link, result.entries[0].link) == ("https://example.org/a/b", "https://example.org/posts/c#part")


def test_feed_atom_link_xml_base_overrides_parent() -> None:
    xml = (
        '<feed xmlns="http://www.w3.org/2005/Atom" xml:base="https://example.org/a/">'
        '<link xml:base="https://cdn.example/b/" href="c"/></feed>'
    )
    assert parse_feed(xml).link == "https://cdn.example/b/c"


def test_feed_rss_xml_base_resolves_link_text() -> None:
    xml = '<rss xml:base="https://example.org/a/"><channel><link>b</link><item><link>c</link></item></channel></rss>'
    result = parse_feed(xml)
    assert (result.link, result.entries[0].link) == ("https://example.org/a/b", "https://example.org/a/c")


def test_feed_rdf_xml_base_resolves_link_text() -> None:
    xml = (
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xml:base="https://example.org/a/">'
        "<channel><link>b</link></channel><item><link>c</link></item></rdf:RDF>"
    )
    result = parse_feed(xml)
    assert (result.link, result.entries[0].link) == ("https://example.org/a/b", "https://example.org/a/c")


def test_feed_atom_relative_xml_base_keeps_relative_link() -> None:
    xml = '<feed xmlns="http://www.w3.org/2005/Atom" xml:base="../a/"><link href="b"/></feed>'
    assert parse_feed(xml).link == "a/b"


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        pytest.param("/b", "https://example.org/b", id="absolute-path"),
        pytest.param("v1:b", "v1:b", id="digit-in-scheme"),
        pytest.param("a+b:c", "a+b:c", id="plus-in-scheme"),
        pytest.param("a.b:c", "a.b:c", id="dot-in-scheme"),
    ],
)
def test_feed_xml_base_respects_reference_schemes(href: str, expected: str) -> None:
    xml = f'<feed xmlns="http://www.w3.org/2005/Atom" xml:base="https://example.org/a/"><link href="{href}"/></feed>'
    assert parse_feed(xml).link == expected


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


@pytest.mark.parametrize(
    ("field", "markup", "expected"),
    [
        pytest.param("title", "Hello <b>world</b>", "Hello <b>world</b>", id="title"),
        pytest.param("summary", "A <p>summary</p>", "A <p>summary</p>", id="summary"),
        pytest.param("content", "A <p>body</p>", "A <p>body</p>", id="content"),
    ],
)
def test_feed_atom_xhtml_preserves_markup(field: str, markup: str, expected: str) -> None:
    xml: Final = (
        '<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
        f'<{field} type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml">{markup}</div></{field}>'
        "</entry></feed>"
    )
    assert getattr(parse_feed(xml).entries[0], field) == expected


def test_feed_atom_xhtml_feed_title() -> None:
    xml: Final = (
        '<feed xmlns="http://www.w3.org/2005/Atom"><title type="xhtml">'
        '<div xmlns="http://www.w3.org/1999/xhtml">Hello <b>world</b></div></title></feed>'
    )
    assert parse_feed(xml).title == "Hello <b>world</b>"


def test_feed_atom_xhtml_feed_subtitle() -> None:
    xml: Final = (
        '<feed xmlns="http://www.w3.org/2005/Atom"><subtitle type="xhtml">'
        '<div xmlns="http://www.w3.org/1999/xhtml">A <em>subtitle</em></div></subtitle></feed>'
    )
    assert parse_feed(xml).description == "A <em>subtitle</em>"


def test_feed_atom_xhtml_prefixed_elements() -> None:
    xml: Final = (
        '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:xh="http://www.w3.org/1999/xhtml">'
        '<title type="xhtml"><xh:div>Hi <xh:b class="x">B</xh:b></xh:div></title></feed>'
    )
    assert parse_feed(xml).title == 'Hi <b class="x">B</b>'


def test_feed_atom_xhtml_with_prefixed_atom_fields() -> None:
    xml: Final = (
        '<a:feed xmlns:a="http://www.w3.org/2005/Atom" xmlns:xh="http://www.w3.org/1999/xhtml">'
        '<a:title type="xhtml"><xh:div>Hi <xh:b>there</xh:b></xh:div></a:title>'
        "</a:feed>"
    )
    assert parse_feed(xml).title == "Hi <b>there</b>"


def test_feed_atom_xhtml_inherits_foreign_prefix() -> None:
    xml: Final = (
        '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:xh="http://www.w3.org/1999/xhtml" '
        'xmlns:s="http://www.w3.org/2000/svg"><title type="xhtml"><xh:div>'
        '<s:svg><s:circle r="2"/></s:svg></xh:div></title></feed>'
    )
    assert parse_feed(xml).title == (
        '<s:svg xmlns:s="http://www.w3.org/2000/svg"><s:circle xmlns:s="http://www.w3.org/2000/svg" r="2"/></s:svg>'
    )


def test_feed_atom_xhtml_inherits_attribute_prefix() -> None:
    xml: Final = (
        '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:xlink="http://www.w3.org/1999/xlink">'
        '<title type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml">'
        '<a xlink:href="https://example.org/">link</a></div></title></feed>'
    )
    assert parse_feed(xml).title == (
        '<a xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="https://example.org/">link</a>'
    )


def test_feed_atom_xhtml_inherits_prefix_from_div() -> None:
    xml: Final = (
        '<feed xmlns="http://www.w3.org/2005/Atom"><title type="xhtml">'
        '<div xmlns="http://www.w3.org/1999/xhtml" xmlns:s="urn:s"><s:box/></div></title></feed>'
    )
    assert parse_feed(xml).title == '<s:box xmlns:s="urn:s"/>'


@pytest.mark.parametrize("binding", [None, ""], ids=["valueless", "empty"])
def test_feed_atom_xhtml_invalid_foreign_binding_falls_back(binding: str | None) -> None:
    document: Final = parse_xml(
        '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:s="urn:s"><title type="xhtml">'
        '<div xmlns="http://www.w3.org/1999/xhtml"><s:box>text</s:box></div></title></feed>'
    )
    document.select("feed")[0].attrs["xmlns:s"] = binding
    assert cast("Feed", document.feed()).title == "text"


def test_feed_atom_xhtml_removed_foreign_binding_falls_back() -> None:
    document: Final = parse_xml(
        '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:s="urn:s"><title type="xhtml">'
        '<div xmlns="http://www.w3.org/1999/xhtml"><s:box>text</s:box></div></title></feed>'
    )
    del document.select("feed")[0].attrs["xmlns:s"]
    assert cast("Feed", document.feed()).title == "text"


def test_feed_atom_xhtml_unbound_attribute_prefix_falls_back() -> None:
    document: Final = parse_xml(
        '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:s="urn:s"><title type="xhtml">'
        '<div xmlns="http://www.w3.org/1999/xhtml"><p s:key="v">text</p></div></title></feed>'
    )
    del document.select("feed")[0].attrs["xmlns:s"]
    assert cast("Feed", document.feed()).title == "text"


def test_feed_atom_xhtml_escapes_markup_text_and_attributes() -> None:
    xml: Final = (
        '<feed xmlns="http://www.w3.org/2005/Atom"><title type="xhtml">'
        '<div xmlns="http://www.w3.org/1999/xhtml"><a title="a &amp; &quot;b&quot; &lt; &gt;">x &amp; y</a></div>'
        "</title></feed>"
    )
    assert parse_feed(xml).title == '<a title="a &amp; &quot;b&quot; &lt; &gt;">x &amp; y</a>'


def test_feed_atom_xhtml_empty_html_elements() -> None:
    xml: Final = (
        '<feed xmlns="http://www.w3.org/2005/Atom"><title type="xhtml">'
        '<div xmlns="http://www.w3.org/1999/xhtml"><p/><br/></div></title></feed>'
    )
    assert parse_feed(xml).title == "<p></p><br/>"


def test_feed_atom_xhtml_attribute_whitespace() -> None:
    xml: Final = (
        '<feed xmlns="http://www.w3.org/2005/Atom"><title type="xhtml">'
        '<div xmlns="http://www.w3.org/1999/xhtml"><a title="a&#10;b&#9;c&#13;d">x</a></div>'
        "</title></feed>"
    )
    assert parse_feed(xml).title == '<a title="a&#10;b&#9;c&#13;d">x</a>'


@pytest.mark.parametrize(
    "invalid",
    [
        pytest.param("\x00", id="nul"),
        pytest.param("\x01", id="control"),
        pytest.param("\ud800", id="surrogate"),
        pytest.param("\ufffe", id="noncharacter-fffe"),
        pytest.param("\uffff", id="noncharacter-ffff"),
    ],
)
def test_feed_atom_xhtml_drops_xml_forbidden_text(invalid: str) -> None:
    document: Final = parse_xml(
        '<feed xmlns="http://www.w3.org/2005/Atom"><title type="xhtml">'
        '<div xmlns="http://www.w3.org/1999/xhtml"><p/></div></title></feed>'
    )
    document.select("p")[0].append(Text(f"a{invalid}b"))
    assert cast("Feed", document.feed()).title == "<p>ab</p>"


@pytest.mark.parametrize(
    "invalid",
    [
        pytest.param("\x00", id="nul"),
        pytest.param("\x01", id="control"),
        pytest.param("\ud800", id="surrogate"),
        pytest.param("\ufffe", id="noncharacter-fffe"),
        pytest.param("\uffff", id="noncharacter-ffff"),
    ],
)
def test_feed_atom_xhtml_drops_xml_forbidden_attribute(invalid: str) -> None:
    document: Final = parse_xml(
        '<feed xmlns="http://www.w3.org/2005/Atom"><title type="xhtml">'
        '<div xmlns="http://www.w3.org/1999/xhtml"><p title="ab"/></div></title></feed>'
    )
    document.select("p")[0].attrs["title"] = f"a{invalid}b"
    assert cast("Feed", document.feed()).title == '<p title="ab"></p>'


def test_feed_atom_xhtml_empty_div_is_missing() -> None:
    xml: Final = (
        '<feed xmlns="http://www.w3.org/2005/Atom"><title type="xhtml">'
        '<div xmlns="http://www.w3.org/1999/xhtml"/></title></feed>'
    )
    assert parse_feed(xml).title is None


def test_feed_atom_xhtml_without_div_keeps_text() -> None:
    xml: Final = '<feed xmlns="http://www.w3.org/2005/Atom"><title type="xhtml">plain</title></feed>'
    assert parse_feed(xml).title == "plain"


def test_feed_atom_xhtml_preserves_comments_and_pi() -> None:
    xml: Final = (
        '<feed xmlns="http://www.w3.org/2005/Atom"><title type="xhtml">'
        '<div xmlns="http://www.w3.org/1999/xhtml"><!--note--><?test value?><p>x</p></div></title></feed>'
    )
    assert parse_feed(xml).title == "<!--note--><?test value?><p>x</p>"


def test_feed_atom_xhtml_preserves_cdata() -> None:
    xml: Final = (
        '<feed xmlns="http://www.w3.org/2005/Atom"><title type="xhtml">'
        '<div xmlns="http://www.w3.org/1999/xhtml"><![CDATA[<b>& text</b>]]></div></title></feed>'
    )
    assert parse_feed(xml).title == "&lt;b&gt;&amp; text&lt;/b&gt;"


def test_feed_atom_xhtml_xml_prefix_is_implicit() -> None:
    xml: Final = (
        '<feed xmlns="http://www.w3.org/2005/Atom"><title type="xhtml">'
        '<div xmlns="http://www.w3.org/1999/xhtml"><p xml:lang="en">text</p></div></title></feed>'
    )
    assert parse_feed(xml).title == '<p xml:lang="en">text</p>'


def test_feed_atom_xhtml_local_prefix_is_not_duplicated() -> None:
    xml: Final = (
        '<feed xmlns="http://www.w3.org/2005/Atom"><title type="xhtml">'
        '<div xmlns="http://www.w3.org/1999/xhtml"><s:svg xmlns:s="http://www.w3.org/2000/svg"/></div>'
        "</title></feed>"
    )
    assert parse_feed(xml).title == '<s:svg xmlns:s="http://www.w3.org/2000/svg"/>'


def test_feed_atom_xhtml_multiple_attributes_share_prefix() -> None:
    xml: Final = (
        '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:x="urn:x"><title type="xhtml">'
        '<div xmlns="http://www.w3.org/1999/xhtml"><a x:first="1" x:second="2">x</a></div></title></feed>'
    )
    assert parse_feed(xml).title == '<a xmlns:x="urn:x" x:first="1" x:second="2">x</a>'


def test_feed_atom_xhtml_prefixed_tag_and_attribute_share_prefix() -> None:
    xml: Final = (
        '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:s="urn:s"><title type="xhtml">'
        '<div xmlns="http://www.w3.org/1999/xhtml"><s:box s:key="v"/></div></title></feed>'
    )
    assert parse_feed(xml).title == '<s:box xmlns:s="urn:s" s:key="v"/>'


def test_feed_atom_xhtml_prefixed_tag_and_other_attribute_prefix() -> None:
    xml: Final = (
        '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:s="urn:s" xmlns:t="urn:t">'
        '<title type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml">'
        '<s:box t:key="v"/></div></title></feed>'
    )
    assert parse_feed(xml).title == '<s:box xmlns:s="urn:s" xmlns:t="urn:t" t:key="v"/>'


def test_feed_atom_xhtml_distinguishes_equal_length_prefixes() -> None:
    xml: Final = (
        '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:s="urn:s" xmlns:t="urn:t">'
        '<title type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml">'
        '<a s:one="1" t:two="2">x</a></div></title></feed>'
    )
    assert parse_feed(xml).title == '<a xmlns:s="urn:s" s:one="1" xmlns:t="urn:t" t:two="2">x</a>'


def test_feed_atom_xhtml_attribute_name_before_prefix() -> None:
    xml: Final = (
        '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:abc="urn:abc">'
        '<title type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml">'
        '<a id="x" abc:key="v">x</a></div></title></feed>'
    )
    assert parse_feed(xml).title == '<a id="x" xmlns:abc="urn:abc" abc:key="v">x</a>'


def test_feed_atom_xhtml_other_name_before_prefix() -> None:
    xml: Final = (
        '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:x="urn:x">'
        '<title type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml">'
        '<a class="x" x:key="v">x</a></div></title></feed>'
    )
    assert parse_feed(xml).title == '<a class="x" xmlns:x="urn:x" x:key="v">x</a>'


def test_feed_atom_xhtml_type_absent_with_other_attribute() -> None:
    xml: Final = '<feed xmlns="http://www.w3.org/2005/Atom"><title id="x">plain</title></feed>'
    assert parse_feed(xml).title == "plain"


def test_feed_atom_xhtml_requires_xhtml_div() -> None:
    xml: Final = (
        '<feed xmlns="http://www.w3.org/2005/Atom"><title type="xhtml">'
        '<div xmlns="urn:other"><b>plain</b></div></title></feed>'
    )
    assert parse_feed(xml).title == "plain"


def test_feed_rss_xhtml_type_keeps_plain_text() -> None:
    xml: Final = '<rss><channel><title type="xhtml"><div><b>plain</b></div></title></channel></rss>'
    assert parse_feed(xml).title == "plain"


def test_feed_malformed_xml_falls_back_to_html() -> None:
    assert feed("<rss><channel><title>T&nbsp;U</title><link>https://example.com/</channel>") == Feed(
        "rss", "T\xa0U", "https://example.com/", None, None, ()
    )


def test_feed_field_text_spans_nested_elements() -> None:
    xml = "<rss><channel><item><description>a <b>bold</b> c</description></item></channel></rss>"
    assert feed(xml) == Feed(
        "rss", None, None, None, None, (Entry(None, None, None, None, None, "a bold c", None, None),)
    )
