from __future__ import annotations

from typing import Final

import pytest

from turbohtml.extract import feed


@pytest.mark.parametrize(
    ("links", "expected"),
    [
        pytest.param('<link rel="self" href="https://example.org/feed"/>', None, id="self"),
        pytest.param('<link rel="enclosure" href="https://example.org/media"/>', None, id="enclosure"),
        pytest.param(
            '<link rel="self" href="https://example.org/feed"/><link rel="alternate" href="https://example.org/page"/>',
            "https://example.org/page",
            id="short-alternate-after-self",
        ),
        pytest.param(
            (
                '<link rel="self" href="https://example.org/feed"/>'
                '<link rel="http://www.iana.org/assignments/relation/alternate" href="https://example.org/page"/>'
            ),
            "https://example.org/page",
            id="full-alternate-after-self",
        ),
        pytest.param(
            (
                '<link rel="self" href="https://example.org/feed"/>'
                '<link rel="HTTP://WWW.IANA.ORG/assignments/relation/alternate" href="https://example.org/page"/>'
            ),
            None,
            id="full-alternate-uppercase-authority",
        ),
        pytest.param(
            '<link rel="http://www.iana.org/assignments/relation/ALTERNATE" href="https://example.org/page"/>',
            None,
            id="full-alternate-uppercase-path",
        ),
        pytest.param('<link rel="alternate">https://example.org/page</link>', None, id="alternate-without-href"),
        pytest.param('<link href=" "/>', None, id="whitespace-only-href"),
        pytest.param('<link href="https://example.org/page"/>', "https://example.org/page", id="absent-rel"),
        pytest.param('<link rel="ALTERNATE" href="https://example.org/page"/>', None, id="uppercase-rel"),
        pytest.param('<link rel="" href="https://example.org/page"/>', None, id="empty-rel"),
    ],
)
@pytest.mark.parametrize("target", ["feed", "entry"])
def test_feed_atom_link_relation(links: str, expected: str | None, target: str) -> None:
    xml: Final = (
        '<feed xmlns="http://www.w3.org/2005/Atom"><id>urn:feed</id><title>Feed</title>'
        "<updated>2020-01-01T00:00:00Z</updated>"
        + links
        + "<entry><id>urn:entry</id><title>Entry</title><updated>2020-01-01T00:00:00Z</updated>"
        "<content>Body</content>" + links + "</entry></feed>"
    )
    result = feed(xml)
    assert result is not None
    assert (result.link if target == "feed" else result.entries[0].link) == expected


@pytest.mark.parametrize(
    ("base", "link", "expected"),
    [
        pytest.param(
            ' xml:base="https://example.org/base/page?old=1"',
            '<link href=""/>',
            "https://example.org/base/page?old=1",
            id="empty-with-base",
        ),
        pytest.param(
            ' xml:base="https://example.org/base/page?old=1"',
            '<link xml:base="../other/" href=""/>',
            "https://example.org/other/",
            id="empty-with-nested-base",
        ),
        pytest.param("", '<link href=""/>', "", id="empty-without-base"),
        pytest.param(
            ' xml:base="https://example.org/base/page?old=1"',
            '<link href="#"/>',
            "https://example.org/base/page?old=1#",
            id="empty-fragment",
        ),
        pytest.param(
            ' xml:base="https://example.org/base/page?old=1"',
            '<link href="?"/>',
            "https://example.org/base/page?",
            id="empty-query",
        ),
    ],
)
@pytest.mark.parametrize("target", ["feed", "entry"])
def test_feed_atom_link_empty_reference(base: str, link: str, expected: str, target: str) -> None:
    xml: Final = (
        f'<feed xmlns="http://www.w3.org/2005/Atom"{base}>'
        + link
        + "<entry><content>Body</content>"
        + link
        + "</entry></feed>"
    )
    result = feed(xml)
    assert result is not None
    assert (result.link if target == "feed" else result.entries[0].link) == expected
