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
    xml: Final = (
        '<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
        f'<content type="{media_type}"><doc xmlns="urn:data" value="a&amp;b">'
        "<b>hello &amp; bye</b><empty/></doc></content></entry></feed>"
    )
    result = feed(xml)
    assert result is not None
    assert result.entries[0].content == expected
