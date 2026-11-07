from __future__ import annotations

from typing import Final

import pytest

from turbohtml.extract import feed

_XML_CONTENT: Final[str] = '<doc xmlns="urn:data" value="a&amp;b"><b>hello &amp; bye</b><empty/></doc>'


@pytest.mark.parametrize(
    ("media_type", "expected"),
    [
        ("application/xml", _XML_CONTENT),
        ("text/xml", _XML_CONTENT),
        ("application/thing+xml", _XML_CONTENT),
        ("APPLICATION/XML", _XML_CONTENT),
        ("application/xml; charset=utf-8", _XML_CONTENT),
        ("application/json", "hello & bye"),
        ("xml", "hello & bye"),
        ("application/xm", "hello & bye"),
        ("application/xyl", "hello & bye"),
        ("application/aml", "hello & bye"),
        ("application/xma", "hello & bye"),
        ("application/axml", "hello & bye"),
    ],
)
def test_feed_atom_xml_media_content_retains_markup(media_type: str, expected: str) -> None:
    xml: Final = (
        '<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
        f'<content type="{media_type}"><doc xmlns="urn:data" value="a&amp;b">'
        "<b>hello &amp; bye</b><empty/></doc></content></entry></feed>"
    )
    result = feed(xml)
    assert result is not None
    assert result.entries[0].content == expected
