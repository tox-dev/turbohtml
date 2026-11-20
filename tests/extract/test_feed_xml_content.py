from __future__ import annotations

from typing import Final

import pytest

from turbohtml.extract import feed

_XML_CONTENT: Final[str] = '<doc xmlns="urn:data" value="a&amp;b"><b>hello &amp; bye</b><empty/></doc>'


@pytest.mark.parametrize(
    ("media_type", "expected"),
    [
        pytest.param("application/xml", _XML_CONTENT, id="application-xml"),
        pytest.param("text/xml", _XML_CONTENT, id="text-xml"),
        pytest.param("application/thing+xml", _XML_CONTENT, id="suffix-xml"),
        pytest.param("APPLICATION/XML", _XML_CONTENT, id="uppercase-xml"),
        pytest.param("application/xml; charset=utf-8", _XML_CONTENT, id="xml-with-charset"),
        pytest.param("application/json", "hello & bye", id="json-not-xml"),
        pytest.param("xml", "hello & bye", id="bare-xml"),
        pytest.param("application/xm", "hello & bye", id="xm-prefix"),
        pytest.param("application/xyl", "hello & bye", id="xyl-not-xml"),
        pytest.param("application/aml", "hello & bye", id="aml-not-xml"),
        pytest.param("application/xma", "hello & bye", id="xma-not-xml"),
        pytest.param("application/axml", "hello & bye", id="axml-not-xml"),
    ],
)
def test_feed_atom_xml_media_content_retains_markup(media_type: str, expected: str) -> None:
    xml: Final[str] = (
        '<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
        f'<content type="{media_type}"><doc xmlns="urn:data" value="a&amp;b">'
        "<b>hello &amp; bye</b><empty/></doc></content></entry></feed>"
    )
    result = feed(xml)
    assert result is not None
    assert result.entries[0].content == expected


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        pytest.param(
            f'<content type="application/xml">{"<p:b>x</p:b>" * 64}</content>',
            "<p:b>x</p:b>" * 64,
            id="xml-out-of-proportion",
        ),
        pytest.param(
            f'<content type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml">{"<p:b>x</p:b>" * 64}</div></content>',
            "<p:b>x</p:b>" * 64,
            id="xhtml-out-of-proportion",
        ),
        pytest.param(
            '<content type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml">{}</div></content>'.format(
                "<i p:a=''>x</i>" * 64
            ),
            '<i p:a="">x</i>' * 64,
            id="xhtml-attribute-out-of-proportion",
        ),
        pytest.param(
            '<content type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml">{}<q:b>y</q:b></div></content>'.format(
                "<p:b>x</p:b>" * 64
            ),
            f"{'x' * 64}y",
            id="xhtml-out-of-proportion-unbound-prefix",
        ),
    ],
)
def test_feed_atom_markup_drops_disproportionate_inherited_namespace_copies(content: str, expected: str) -> None:
    xml: Final[str] = (
        f'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:p="{"u" * 3000}"><entry>{content}</entry></feed>'
    )
    result = feed(xml)
    assert result is not None
    assert result.entries[0].content == expected


@pytest.mark.parametrize(
    "content",
    [
        pytest.param('<content type="application/xml"><p:b>x</p:b></content>', id="xml"),
        pytest.param(
            '<content type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml"><p:b>x</p:b></div></content>', id="xhtml"
        ),
    ],
)
def test_feed_atom_markup_keeps_inherited_namespace_copy_within_the_construct_base(content: str) -> None:
    name: Final[str] = "u" * 512
    xml: Final[str] = f'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:p="{name}"><entry>{content}</entry></feed>'
    result = feed(xml)
    assert result is not None
    assert result.entries[0].content == f'<p:b xmlns:p="{name}">x</p:b>'


@pytest.mark.parametrize(
    "content",
    [
        pytest.param('<content type="application/xml"><p:b/><p:b/></content>', id="xml"),
        pytest.param(
            '<content type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml"><p:b/><p:b/></div></content>', id="xhtml"
        ),
    ],
)
def test_feed_atom_markup_inherited_namespace_copies_stay_bounded_across_entries(content: str) -> None:
    # every entry keeps its content, so a base granted afresh to each construct must not multiply across entries
    entries: Final[int] = 2000
    xml: Final[str] = (
        f'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:p="{"u" * 3000}">'
        f"{f'<entry>{content}</entry>' * entries}</feed>"
    )
    result = feed(xml)
    assert result is not None
    assert len(result.entries) == entries
    assert sum(len(entry.content or "") for entry in result.entries) <= 64 * len(xml)


@pytest.mark.parametrize(
    "content",
    [
        pytest.param('<content type="application/xml">{}</content>', id="xml"),
        pytest.param('<content type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml">{}</div></content>', id="xhtml"),
    ],
)
@pytest.mark.parametrize("size", [1000, 2000, 3000])
def test_feed_atom_markup_inherited_namespace_copies_stay_bounded(content: str, size: int) -> None:
    xml: Final[str] = (
        f'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:p="{"u" * size}"><entry>'
        f"{content.format('<p:b/>' * size)}</entry></feed>"
    )
    result = feed(xml)
    assert result is not None
    assert len(result.entries[0].content or "") <= 64 * len(xml)
