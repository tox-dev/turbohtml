from __future__ import annotations

from typing import Final

import pytest

from turbohtml.extract import feed

_RDF_START: Final[str] = (
    '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
    'xmlns:r="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
    'xmlns:s="http://purl.org/rss/1.0/" xmlns="http://purl.org/rss/1.0/">'
)
_ITEM_A: Final[str] = '<item rdf:about="https://example.org/a"><title>A</title></item>'
_ITEM_B: Final[str] = '<item rdf:about="https://example.org/b"><title>B</title></item>'


@pytest.mark.parametrize(
    ("sequence", "items", "expected"),
    [
        pytest.param(
            '<rdf:Seq><rdf:li rdf:resource="https://example.org/b"/>\n<rdf:li rdf:resource="https://example.org/a"/></rdf:Seq>',
            _ITEM_A + _ITEM_B,
            ("B", "A"),
            id="rdf-resource-reversed",
        ),
        pytest.param(
            '<rdf:Seq><rdf:li rdf:resource="https://example.org/a"/>\n<rdf:li rdf:resource="https://example.org/b"/></rdf:Seq>',
            _ITEM_A + _ITEM_B,
            ("A", "B"),
            id="rdf-resource-in-order",
        ),
        pytest.param(
            '<rdf:Seq><r:li r:resource="https://example.org/b"/><r:li r:resource="https://example.org/a"/></rdf:Seq>',
            _ITEM_A + _ITEM_B,
            ("B", "A"),
            id="aliased-li-in-canonical-sequence",
        ),
        pytest.param(
            '<rdf:Seq xmlns:RDF="urn:other"><RDF:li resource="https://example.org/a"/>'
            '<rdf:li rdf:resource="https://example.org/b"/></rdf:Seq>',
            _ITEM_A + _ITEM_B,
            ("B",),
            id="foreign-cased-prefix",
        ),
        pytest.param(
            '<rdf:Seq><rdf:li rdf:resource="https://example.org/b"/><rdf:li rdf:resource="https://example.org/a"/></rdf:Seq>',
            _ITEM_A.replace("rdf:about", "r:about") + _ITEM_B,
            ("B", "A"),
            id="aliased-about-in-canonical-sequence",
        ),
        pytest.param(
            '<rdf:Seq><rdf:li rdf:resource="https://example.org/a"/><rdf:li rdf:resource="https://example.org/b"/>'
            '<rdf:li rdf:resource="https://example.org/missing"/></rdf:Seq>',
            _ITEM_A + _ITEM_B,
            ("A", "B"),
            id="sequence-longer-than-items",
        ),
        pytest.param(
            '<rdf:Seq><rdf:li rdf:resource="https://example.org/a"/><rdf:li rdf:resource="https://example.org/b"/></rdf:Seq>',
            f"{_ITEM_A}<item><title>No ID</title></item>{_ITEM_B}",
            ("A", "B"),
            id="missing-about-before-listed",
        ),
        pytest.param(
            '<rdf:Seq><rdf:li rdf:resource="https://example.org/a"/><rdf:li rdf:resource="https://example.org/b"/></rdf:Seq>',
            f'{_ITEM_A}{_ITEM_B}<x:item xmlns:x="urn:other"><x:title>Wrong</x:title></x:item>',
            ("A", "B"),
            id="foreign-item-after-listed",
        ),
        pytest.param(
            '<rdf:Seq><rdf:li resource="https://example.org/b"/><rdf:li resource="https://example.org/a"/></rdf:Seq>',
            _ITEM_A + _ITEM_B,
            ("B", "A"),
            id="unprefixed-resource",
        ),
        pytest.param(
            '<r:Seq><r:li r:resource="https://example.org/b"/><r:li r:resource="https://example.org/a"/></r:Seq>',
            _ITEM_A + _ITEM_B,
            ("B", "A"),
            id="alias-prefixes",
        ),
        pytest.param(
            '<rdf:Seq><rdf:li rdf:resource="https://example.org/b"/></rdf:Seq>',
            _ITEM_A + _ITEM_B,
            ("B",),
            id="unlisted-item",
        ),
        pytest.param(
            '<rdf:Seq><rdf:li rdf:resource="https://example.org/missing"/><rdf:li rdf:resource="https://example.org/a"/></rdf:Seq>',
            _ITEM_A + _ITEM_B,
            ("A",),
            id="missing-target",
        ),
        pytest.param("<rdf:Seq/>", _ITEM_A + _ITEM_B, (), id="empty-sequence"),
        pytest.param("", _ITEM_A + _ITEM_B, ("A", "B"), id="missing-sequence"),
        pytest.param(
            '<x:Seq xmlns:x="urn:other"><x:li x:resource="https://example.org/b"/></x:Seq>',
            _ITEM_A + _ITEM_B,
            ("A", "B"),
            id="foreign-sequence",
        ),
        pytest.param(
            '<rdf:Seq><x:li xmlns:x="urn:other" resource="https://example.org/a"/><rdf:li rdf:resource="https://example.org/b"/></rdf:Seq>',
            _ITEM_A + _ITEM_B,
            ("B",),
            id="foreign-li",
        ),
        pytest.param(
            '<rdf:Seq><rdf:li x:resource="https://example.org/a" xmlns:x="urn:other"/><rdf:li rdf:resource="https://example.org/b"/></rdf:Seq>',
            _ITEM_A + _ITEM_B,
            ("B",),
            id="foreign-resource",
        ),
        pytest.param(
            '<rdf:Seq><rdf:li/><rdf:li rdf:resource=""/><rdf:li rdf:resource="https://example.org/b"/></rdf:Seq>',
            f'{_ITEM_A}<item rdf:about=""><title>Empty ID</title></item>{_ITEM_B}',
            ("Empty ID", "B"),
            id="empty-resource",
        ),
        pytest.param(
            '<rdf:Seq><rdf:li rdf:resource="https://example.org/b"/><rdf:li rdf:resource="https://example.org/a"/></rdf:Seq>',
            f'{_ITEM_A}<x:item xmlns:x="urn:other"><x:title>Wrong</x:title></x:item>{_ITEM_B}',
            ("B", "A"),
            id="foreign-item",
        ),
        pytest.param(
            '<rdf:Seq><rdf:li rdf:resource="https://example.org/b"/><rdf:li rdf:resource="https://example.org/a"/></rdf:Seq>',
            f'{_ITEM_A}<item rdf:about=""><title>Empty ID</title></item><item><title>No ID</title></item>{_ITEM_B}',
            ("B", "A"),
            id="missing-about",
        ),
        pytest.param(
            '<rdf:Seq><rdf:li resourcx="https://example.org/a" '
            'x:resourcx="https://example.org/a" xmlns:x="urn:other"/>'
            '<rdf:li rdf:resource="https://example.org/b"/></rdf:Seq>',
            _ITEM_A + _ITEM_B,
            ("B",),
            id="wrong-resource-name",
        ),
        pytest.param(
            '<rdf:Seq><rdf:li rdf:resourcx="https://example.org/a"/><rdf:li rdf:resource="https://example.org/b"/></rdf:Seq>',
            _ITEM_A + _ITEM_B,
            ("B",),
            id="wrong-prefixed-resource-name",
        ),
        pytest.param(
            '<rdf:Seq><rdf:li rdf:resource="https://example.org/a"/><rdf:li rdf:resource="https://example.org/b"/></rdf:Seq>',
            _ITEM_A.replace("rdf:about", "rdf:aboux") + _ITEM_B,
            ("B",),
            id="wrong-prefixed-about-name",
        ),
        pytest.param(
            '<rdf:Seq><rdf:li rdf:resource="https://example.org/a"/><rdf:li rdf:resource="https://example.org/b"/></rdf:Seq>',
            f"{_ITEM_A}{_ITEM_B}"
            '<item rdf:about="https://example.org/unlisted" xml:lang="en"><title>Unlisted</title></item>',
            ("A", "B"),
            id="unlisted-item-with-extra-attribute",
        ),
    ],
)
def test_feed_rdf_sequence(sequence: str, items: str, expected: tuple[str, ...]) -> None:
    xml: Final = (
        f'{_RDF_START}<channel rdf:about="https://example.org/"><items>\n{sequence}\n</items></channel>\n{items}'
        "\n</rdf:RDF>"
    )
    result = feed(xml)
    assert result is not None
    assert tuple(entry.title for entry in result.entries) == expected


def test_feed_rdf_sequence_resolves_xml_base() -> None:
    xml: Final = (
        f"""{_RDF_START.replace("<rdf:RDF ", '<rdf:RDF xml:base="https://example.org/base/" ')}"""
        """<channel rdf:about="https://example.org/"><items><rdf:Seq><rdf:li rdf:resource="a"/><rdf:li """
        """rdf:resource="https://example.org/other/b"/></rdf:Seq></items></channel><item """
        """rdf:about="https://example.org/base/a"><title>A</title></item><item xml:base="https://example.org/other/" """
        """rdf:about="b"><title>B</title></item></rdf:RDF>"""
    )
    result = feed(xml)
    assert result is not None
    assert tuple(entry.title for entry in result.entries) == ("A", "B")


def test_feed_rdf_sequence_resolves_empty_reference_to_xml_base() -> None:
    xml: Final = (
        f"""{_RDF_START.replace("<rdf:RDF ", '<rdf:RDF xml:base="https://example.org/feed" ')}"""
        """<channel rdf:about="https://example.org/"><items><rdf:Seq><rdf:li """
        """rdf:resource=""/></rdf:Seq></items></channel><item """
        """rdf:about="https://example.org/feed"><title>Feed item</title></item></rdf:RDF>"""
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
        f"{_RDF_START.replace('<rdf:RDF ', '<r:RDF ')}"
        '<channel rdf:about="https://example.org/"><items><rdf:Seq><rdf:li '
        'rdf:resource="https://example.org/b"/><rdf:li '
        f'rdf:resource="https://example.org/a"/></rdf:Seq></items></channel>{_ITEM_A}{_ITEM_B}</r:RDF>'
    )
    result = feed(xml)
    assert result is not None
    assert tuple(entry.title for entry in result.entries) == ("B", "A")


def test_feed_rdf_sequence_root_alias_ignores_trailing_foreign_item() -> None:
    xml: Final = (
        f"{_RDF_START.replace('<rdf:RDF ', '<r:RDF ')}"
        '<channel rdf:about="https://example.org/"><items><rdf:Seq><rdf:li '
        'rdf:resource="https://example.org/a"/><rdf:li '
        f'rdf:resource="https://example.org/b"/></rdf:Seq></items></channel>{_ITEM_A}{_ITEM_B}'
        '<x:item xmlns:x="urn:other"><x:title>Wrong</x:title></x:item></r:RDF>'
    )
    result = feed(xml)
    assert result is not None
    assert tuple(entry.title for entry in result.entries) == ("A", "B")


def test_feed_rdf_sequence_prefixed_rss_namespace() -> None:
    xml: Final = (
        f"""{_RDF_START.replace('xmlns="http://purl.org/rss/1.0/"', 'xmlns="urn:other"')}"""
        """<s:channel rdf:about="https://example.org/"><s:items><rdf:Seq><rdf:li """
        """rdf:resource="https://example.org/b"/><rdf:li """
        """rdf:resource="https://example.org/a"/></rdf:Seq></s:items></s:channel><s:item """
        """rdf:about="https://example.org/a"><s:title>A</s:title></s:item><s:item """
        """rdf:about="https://example.org/b"><s:title>B</s:title></s:item></rdf:RDF>"""
    )
    result = feed(xml)
    assert result is not None
    assert tuple(entry.title for entry in result.entries) == ("B", "A")


@pytest.mark.parametrize(
    "first_uri",
    [
        pytest.param("", id="empty"),
        pytest.param("#a", id="fragment"),
        pytest.param("abc", id="relative-name"),
        pytest.param("a/b", id="relative-path"),
        pytest.param("a1:x", id="digit-in-scheme"),
        pytest.param("a+b:x", id="plus-in-scheme"),
        pytest.param("a-b:x", id="dash-in-scheme"),
        pytest.param("a.b:x", id="dot-in-scheme"),
    ],
)
def test_feed_rdf_sequence_uri_forms(first_uri: str) -> None:
    xml: Final = (
        f"""{_RDF_START.replace("<rdf:RDF ", '<rdf:RDF xml:base="https://example.org/base/" ')}"""
        '<channel rdf:about="https://example.org/"><items><rdf:Seq>'
        '<rdf:li rdf:resource="https://example.org/b"/>'
        f'<rdf:li rdf:resource="{first_uri}"/></rdf:Seq></items></channel>'
        f'<item rdf:about="{first_uri}"><title>A</title></item>{_ITEM_B}</rdf:RDF>'
    )
    result = feed(xml)
    assert result is not None
    assert tuple(entry.title for entry in result.entries) == ("B", "A")
