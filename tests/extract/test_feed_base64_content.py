from __future__ import annotations

from typing import Final

import pytest

from turbohtml.extract import feed


@pytest.mark.parametrize(
    ("media_type", "body", "expected"),
    [
        pytest.param("application/octet-stream", "SGVsbG8=", b"Hello", id="octet-stream"),
        pytest.param("application/octet-stream", "/wAA", b"\xff\x00\x00", id="octet-stream-binary"),
        pytest.param("application/octet-stream", "+w==", b"\xfb", id="octet-stream-plus"),
        pytest.param("application/octet-stream", "AA==", b"\x00", id="octet-stream-nul"),
        pytest.param("application/octet-stream", "QUI=", b"AB", id="octet-stream-two-bytes"),
        pytest.param("APPLICATION/JSON", "eyJ4IjoxfQ==", b'{"x":1}', id="json-uppercase-type"),
        pytest.param("application/octet-stream", " SGVs\nbG8= ", b"Hello", id="whitespace-around-body"),
        pytest.param("application/octet-stream", "SGVs\tbG8=", b"Hello", id="tab-inside-body"),
        pytest.param("application/octet-stream; charset=binary", "SGVsbG8=", b"Hello", id="parameter-ignored"),
        pytest.param("invalid; charset=binary", "SGVsbG8=", "SGVsbG8=", id="invalid-type-kept"),
        pytest.param("application/octet-stream", "@@@@", "@@@@", id="invalid-alphabet"),
        pytest.param("application/octet-stream", "SGVsbG8", "SGVsbG8", id="missing-padding"),
        pytest.param("application/octet-stream", "SGV=sbG8", "SGV=sbG8", id="padding-inside"),
        pytest.param("application/octet-stream", "A===", "A===", id="excess-padding"),
        pytest.param("application/octet-stream", "AAA===", "AAA===", id="excess-padding-after-full-group"),
        pytest.param("application/octet-stream", "AA=A", "AA=A", id="padding-then-data"),
        pytest.param("application/octet-stream", "é", "é", id="non-ascii"),
        pytest.param("text/plain", "SGVsbG8=", "SGVsbG8=", id="text-plain"),
        pytest.param("TEXT/PLAIN", "SGVsbG8=", "SGVsbG8=", id="text-plain-uppercase"),
        pytest.param("application/xml", "SGVsbG8=", "SGVsbG8=", id="application-xml"),
        pytest.param("image/svg+xml", "SGVsbG8=", "SGVsbG8=", id="svg-xml"),
        pytest.param("xml", "SGVsbG8=", "SGVsbG8=", id="bare-xml"),
    ],
)
def test_feed_atom_base64_content(media_type: str, body: str, expected: bytes | str) -> None:
    xml: Final = (
        f'<feed xmlns="http://www.w3.org/2005/Atom"><entry><content type="{media_type}">{body}</content></entry></feed>'
    )
    result = feed(xml)
    assert result is not None
    assert result.entries[0].content == expected


def test_feed_atom_empty_binary_content_is_absent() -> None:
    result = feed(
        '<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
        '<content type="application/octet-stream"/><summary>short</summary></entry></feed>'
    )
    assert result is not None
    assert result.entries[0].content is None


def test_feed_atom_content_module_precedes_binary_content() -> None:
    result = feed(
        '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:content="http://purl.org/rss/1.0/modules/content/">'
        "<entry><content:encoded>legacy</content:encoded>"
        '<content type="application/octet-stream">SGVsbG8=</content></entry></feed>'
    )
    assert result is not None
    assert result.entries[0].content == "legacy"
