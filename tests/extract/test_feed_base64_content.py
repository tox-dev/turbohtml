"""Base64 content in Atom feeds."""

from __future__ import annotations

from typing import Final

import pytest

from turbohtml.extract import feed


@pytest.mark.parametrize(
    ("media_type", "body", "expected"),
    [
        ("application/octet-stream", "SGVsbG8=", b"Hello"),
        ("application/octet-stream", "/wAA", b"\xff\x00\x00"),
        ("application/octet-stream", "+w==", b"\xfb"),
        ("application/octet-stream", "AA==", b"\x00"),
        ("application/octet-stream", "QUI=", b"AB"),
        ("APPLICATION/JSON", "eyJ4IjoxfQ==", b'{"x":1}'),
        ("application/octet-stream", " SGVs\nbG8= ", b"Hello"),
        ("application/octet-stream", "SGVs\tbG8=", b"Hello"),
        ("application/octet-stream; charset=binary", "SGVsbG8=", b"Hello"),
        ("invalid; charset=binary", "SGVsbG8=", "SGVsbG8="),
        ("application/octet-stream", "@@@@", "@@@@"),
        ("application/octet-stream", "SGVsbG8", "SGVsbG8"),
        ("application/octet-stream", "SGV=sbG8", "SGV=sbG8"),
        ("application/octet-stream", "A===", "A==="),
        ("application/octet-stream", "AAA===", "AAA==="),
        ("application/octet-stream", "AA=A", "AA=A"),
        ("application/octet-stream", "é", "é"),
        ("text/plain", "SGVsbG8=", "SGVsbG8="),
        ("TEXT/PLAIN", "SGVsbG8=", "SGVsbG8="),
        ("application/xml", "SGVsbG8=", "SGVsbG8="),
        ("image/svg+xml", "SGVsbG8=", "SGVsbG8="),
        ("xml", "SGVsbG8=", "SGVsbG8="),
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
