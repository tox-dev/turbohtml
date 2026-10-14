"""RSS 1.0 channel sequences."""

from __future__ import annotations

from typing import Final

import pytest

from turbohtml.extract import feed

RDF_START: Final = (
    '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
    'xmlns:r="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
    'xmlns:s="http://purl.org/rss/1.0/" xmlns="http://purl.org/rss/1.0/">'
)
ITEM_A: Final = '<item rdf:about="https://example.org/a"><title>A</title></item>'
ITEM_B: Final = '<item rdf:about="https://example.org/b"><title>B</title></item>'


@pytest.mark.parametrize(
    ("sequence", "items", "expected"),
    [
        (
            '<rdf:Seq><rdf:li rdf:resource="https://example.org/b"/>\n<rdf:li rdf:resource="https://example.org/a"/></rdf:Seq>',
            ITEM_A + ITEM_B,
            ("B", "A"),
        ),
        (
            '<rdf:Seq><rdf:li rdf:resource="https://example.org/a"/>\n<rdf:li rdf:resource="https://example.org/b"/></rdf:Seq>',
            ITEM_A + ITEM_B,
            ("A", "B"),
        ),
        (
            '<rdf:Seq><r:li r:resource="https://example.org/b"/><r:li r:resource="https://example.org/a"/></rdf:Seq>',
            ITEM_A + ITEM_B,
            ("B", "A"),
        ),
        (
            (
                '<rdf:Seq xmlns:RDF="urn:other"><RDF:li resource="https://example.org/a"/>'
                '<rdf:li rdf:resource="https://example.org/b"/></rdf:Seq>'
            ),
            ITEM_A + ITEM_B,
            ("B",),
        ),
        (
            '<rdf:Seq><rdf:li rdf:resource="https://example.org/b"/><rdf:li rdf:resource="https://example.org/a"/></rdf:Seq>',
            ITEM_A.replace("rdf:about", "r:about") + ITEM_B,
            ("B", "A"),
        ),
        (
            (
                '<rdf:Seq><rdf:li rdf:resource="https://example.org/a"/><rdf:li rdf:resource="https://example.org/b"/>'
                '<rdf:li rdf:resource="https://example.org/missing"/></rdf:Seq>'
            ),
            ITEM_A + ITEM_B,
            ("A", "B"),
        ),
        (
            '<rdf:Seq><rdf:li rdf:resource="https://example.org/a"/><rdf:li rdf:resource="https://example.org/b"/></rdf:Seq>',
            ITEM_A + "<item><title>No ID</title></item>" + ITEM_B,
            ("A", "B"),
        ),
        (
            '<rdf:Seq><rdf:li rdf:resource="https://example.org/a"/><rdf:li rdf:resource="https://example.org/b"/></rdf:Seq>',
            ITEM_A + ITEM_B + '<x:item xmlns:x="urn:other"><x:title>Wrong</x:title></x:item>',
            ("A", "B"),
        ),
        (
            '<rdf:Seq><rdf:li resource="https://example.org/b"/><rdf:li resource="https://example.org/a"/></rdf:Seq>',
            ITEM_A + ITEM_B,
            ("B", "A"),
        ),
        (
            '<r:Seq><r:li r:resource="https://example.org/b"/><r:li r:resource="https://example.org/a"/></r:Seq>',
            ITEM_A + ITEM_B,
            ("B", "A"),
        ),
        ('<rdf:Seq><rdf:li rdf:resource="https://example.org/b"/></rdf:Seq>', ITEM_A + ITEM_B, ("B",)),
        (
            '<rdf:Seq><rdf:li rdf:resource="https://example.org/missing"/><rdf:li rdf:resource="https://example.org/a"/></rdf:Seq>',
            ITEM_A + ITEM_B,
            ("A",),
        ),
        ("<rdf:Seq/>", ITEM_A + ITEM_B, ()),
        ("", ITEM_A + ITEM_B, ("A", "B")),
        ('<x:Seq xmlns:x="urn:other"><x:li x:resource="https://example.org/b"/></x:Seq>', ITEM_A + ITEM_B, ("A", "B")),
        (
            '<rdf:Seq><x:li xmlns:x="urn:other" resource="https://example.org/a"/><rdf:li rdf:resource="https://example.org/b"/></rdf:Seq>',
            ITEM_A + ITEM_B,
            ("B",),
        ),
        (
            '<rdf:Seq><rdf:li x:resource="https://example.org/a" xmlns:x="urn:other"/><rdf:li rdf:resource="https://example.org/b"/></rdf:Seq>',
            ITEM_A + ITEM_B,
            ("B",),
        ),
        (
            '<rdf:Seq><rdf:li/><rdf:li rdf:resource=""/><rdf:li rdf:resource="https://example.org/b"/></rdf:Seq>',
            ITEM_A + '<item rdf:about=""><title>Empty ID</title></item>' + ITEM_B,
            ("Empty ID", "B"),
        ),
        (
            '<rdf:Seq><rdf:li rdf:resource="https://example.org/b"/><rdf:li rdf:resource="https://example.org/a"/></rdf:Seq>',
            ITEM_A + '<x:item xmlns:x="urn:other"><x:title>Wrong</x:title></x:item>' + ITEM_B,
            ("B", "A"),
        ),
        (
            '<rdf:Seq><rdf:li rdf:resource="https://example.org/b"/><rdf:li rdf:resource="https://example.org/a"/></rdf:Seq>',
            ITEM_A + '<item rdf:about=""><title>Empty ID</title></item>' + "<item><title>No ID</title></item>" + ITEM_B,
            ("B", "A"),
        ),
        (
            (
                '<rdf:Seq><rdf:li resourcx="https://example.org/a" '
                'x:resourcx="https://example.org/a" xmlns:x="urn:other"/>'
                '<rdf:li rdf:resource="https://example.org/b"/></rdf:Seq>'
            ),
            ITEM_A + ITEM_B,
            ("B",),
        ),
        (
            '<rdf:Seq><rdf:li rdf:resourcx="https://example.org/a"/><rdf:li rdf:resource="https://example.org/b"/></rdf:Seq>',
            ITEM_A + ITEM_B,
            ("B",),
        ),
        (
            '<rdf:Seq><rdf:li rdf:resource="https://example.org/a"/><rdf:li rdf:resource="https://example.org/b"/></rdf:Seq>',
            ITEM_A.replace("rdf:about", "rdf:aboux") + ITEM_B,
            ("B",),
        ),
        (
            '<rdf:Seq><rdf:li rdf:resource="https://example.org/a"/><rdf:li rdf:resource="https://example.org/b"/></rdf:Seq>',
            ITEM_A
            + ITEM_B
            + '<item rdf:about="https://example.org/unlisted" xml:lang="en"><title>Unlisted</title></item>',
            ("A", "B"),
        ),
    ],
    ids=(
        "rdf-resource-reversed",
        "rdf-resource-in-order",
        "aliased-li-in-canonical-sequence",
        "foreign-cased-prefix",
        "aliased-about-in-canonical-sequence",
        "sequence-longer-than-items",
        "missing-about-before-listed",
        "foreign-item-after-listed",
        "unprefixed-resource",
        "alias-prefixes",
        "unlisted-item",
        "missing-target",
        "empty-sequence",
        "missing-sequence",
        "foreign-sequence",
        "foreign-li",
        "foreign-resource",
        "empty-resource",
        "foreign-item",
        "missing-about",
        "wrong-resource-name",
        "wrong-prefixed-resource-name",
        "wrong-prefixed-about-name",
        "unlisted-item-with-extra-attribute",
    ),
)
def test_feed_rdf_sequence(sequence: str, items: str, expected: tuple[str, ...]) -> None:
    xml: Final = (
        RDF_START
        + '<channel rdf:about="https://example.org/"><items>\n'
        + sequence
        + "\n</items></channel>\n"
        + items
        + "\n</rdf:RDF>"
    )
    result = feed(xml)
    assert result is not None
    assert tuple(entry.title for entry in result.entries) == expected


def test_feed_rdf_sequence_resolves_xml_base() -> None:
    xml: Final = (
        RDF_START.replace("<rdf:RDF ", '<rdf:RDF xml:base="https://example.org/base/" ')
        + '<channel rdf:about="https://example.org/"><items><rdf:Seq>'
        '<rdf:li rdf:resource="a"/><rdf:li rdf:resource="https://example.org/other/b"/>'
        "</rdf:Seq></items></channel>"
        '<item rdf:about="https://example.org/base/a"><title>A</title></item>'
        '<item xml:base="https://example.org/other/" rdf:about="b"><title>B</title></item></rdf:RDF>'
    )
    result = feed(xml)
    assert result is not None
    assert tuple(entry.title for entry in result.entries) == ("A", "B")


def test_feed_rdf_sequence_resolves_empty_reference_to_xml_base() -> None:
    xml: Final = (
        RDF_START.replace("<rdf:RDF ", '<rdf:RDF xml:base="https://example.org/feed" ')
        + '<channel rdf:about="https://example.org/"><items><rdf:Seq>'
        '<rdf:li rdf:resource=""/></rdf:Seq></items></channel>'
        '<item rdf:about="https://example.org/feed"><title>Feed item</title></item></rdf:RDF>'
    )
    result = feed(xml)
    assert result is not None
    assert tuple(entry.title for entry in result.entries) == ("Feed item",)


def test_feed_rdf_html_about_requires_rdf_prefix() -> None:
    result = feed('<rdf:rdf><item x:about="a" xrd:about="b"><title>Entry</title></item></rdf:rdf>')
    assert result is not None
    assert result.entries[0].id is None


def test_feed_rdf_sequence_root_prefix_alias() -> None:
    xml: Final = (
        RDF_START.replace("<rdf:RDF ", "<r:RDF ") + '<channel rdf:about="https://example.org/"><items><rdf:Seq>'
        '<rdf:li rdf:resource="https://example.org/b"/><rdf:li rdf:resource="https://example.org/a"/>'
        "</rdf:Seq></items></channel>" + ITEM_A + ITEM_B + "</r:RDF>"
    )
    result = feed(xml)
    assert result is not None
    assert tuple(entry.title for entry in result.entries) == ("B", "A")


def test_feed_rdf_sequence_root_alias_ignores_trailing_foreign_item() -> None:
    xml: Final = (
        RDF_START.replace("<rdf:RDF ", "<r:RDF ") + '<channel rdf:about="https://example.org/"><items><rdf:Seq>'
        '<rdf:li rdf:resource="https://example.org/a"/><rdf:li rdf:resource="https://example.org/b"/>'
        "</rdf:Seq></items></channel>"
        + ITEM_A
        + ITEM_B
        + '<x:item xmlns:x="urn:other"><x:title>Wrong</x:title></x:item></r:RDF>'
    )
    result = feed(xml)
    assert result is not None
    assert tuple(entry.title for entry in result.entries) == ("A", "B")


def test_feed_rdf_sequence_prefixed_rss_namespace() -> None:
    xml: Final = (
        RDF_START.replace('xmlns="http://purl.org/rss/1.0/"', 'xmlns="urn:other"')
        + '<s:channel rdf:about="https://example.org/"><s:items><rdf:Seq>'
        '<rdf:li rdf:resource="https://example.org/b"/><rdf:li rdf:resource="https://example.org/a"/>'
        "</rdf:Seq></s:items></s:channel>"
        '<s:item rdf:about="https://example.org/a"><s:title>A</s:title></s:item>'
        '<s:item rdf:about="https://example.org/b"><s:title>B</s:title></s:item></rdf:RDF>'
    )
    result = feed(xml)
    assert result is not None
    assert tuple(entry.title for entry in result.entries) == ("B", "A")


@pytest.mark.parametrize(
    "first_uri",
    ["", "#a", "abc", "a/b", "a1:x", "a+b:x", "a-b:x", "a.b:x"],
    ids=(
        "empty",
        "fragment",
        "relative-name",
        "relative-path",
        "digit-in-scheme",
        "plus-in-scheme",
        "dash-in-scheme",
        "dot-in-scheme",
    ),
)
def test_feed_rdf_sequence_uri_forms(first_uri: str) -> None:
    xml: Final = (
        RDF_START.replace("<rdf:RDF ", '<rdf:RDF xml:base="https://example.org/base/" ')
        + '<channel rdf:about="https://example.org/"><items><rdf:Seq>'
        '<rdf:li rdf:resource="https://example.org/b"/>'
        f'<rdf:li rdf:resource="{first_uri}"/></rdf:Seq></items></channel>'
        f'<item rdf:about="{first_uri}"><title>A</title></item>' + ITEM_B + "</rdf:RDF>"
    )
    result = feed(xml)
    assert result is not None
    assert tuple(entry.title for entry in result.entries) == ("B", "A")
