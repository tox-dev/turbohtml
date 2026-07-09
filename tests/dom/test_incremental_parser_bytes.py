"""Bytes feeding for IncrementalParser: a chunk boundary may split a multi-byte
character, so the parser decodes through a stateful incremental decoder that holds
the incomplete tail back until the next chunk."""

from __future__ import annotations

import pytest

from turbohtml import IncrementalParser, parse

MULTIBYTE = "<p>café — ☃ — 🦄</p>"  # 2-, 3-, and 4-byte UTF-8 sequences


def test_utf8_byte_at_a_time_matches_decoded_str() -> None:
    raw = MULTIBYTE.encode("utf-8")
    parser = IncrementalParser()
    for index in range(len(raw)):
        parser.feed(raw[index : index + 1])  # split inside every multi-byte sequence
    assert parser.close().html == parse(MULTIBYTE).html


@pytest.mark.parametrize("chunk", [1, 2, 3, 4, 7])
def test_utf8_chunked_matches_decoded_str(chunk: int) -> None:
    raw = MULTIBYTE.encode("utf-8")
    parser = IncrementalParser()
    for start in range(0, len(raw), chunk):
        parser.feed(raw[start : start + chunk])
    assert parser.close().html == parse(MULTIBYTE).html


def test_bytes_parse_reports_its_encoding() -> None:
    # an alias resolves to its WHATWG canonical name, the same one parse(bytes) reports
    parser = IncrementalParser(encoding="iso-8859-1")
    parser.feed("héllo".encode("latin-1"))
    document = parser.close()
    assert document.encoding == "windows-1252"
    paragraph = document.find("body")
    assert paragraph is not None
    assert "héllo" in paragraph.text


def test_explicit_encoding_decodes_each_chunk() -> None:
    text = "<p>naïve façade</p>"
    raw = text.encode("latin-1")
    parser = IncrementalParser(encoding="iso-8859-1")
    for byte in raw:
        parser.feed(bytes([byte]))
    assert parser.close().html == parse(text).html


def test_mixed_str_and_bytes_chunks() -> None:
    parser = IncrementalParser()
    parser.feed("<p>")
    parser.feed("café".encode())
    parser.feed("</p>")
    document = parser.close()
    paragraph = document.find("p")
    assert paragraph is not None
    assert paragraph.text == "café"
    assert document.encoding == "UTF-8"  # a bytes chunk resolved the label


def test_bytearray_and_memoryview_are_accepted() -> None:
    parser = IncrementalParser()
    parser.feed(bytearray(b"<p>a"))  # ty: ignore[invalid-argument-type]  # any buffer is accepted at runtime
    parser.feed(memoryview(b"b</p>"))  # ty: ignore[invalid-argument-type]  # any buffer is accepted at runtime
    paragraph = parser.close().find("p")
    assert paragraph is not None
    assert paragraph.text == "ab"


def test_unknown_encoding_raises_on_first_bytes_feed() -> None:
    parser = IncrementalParser(encoding="not-a-real-codec")
    with pytest.raises(LookupError):
        parser.feed(b"<p>x</p>")


def test_surrogate_encoding_name_raises_on_first_bytes_feed() -> None:
    parser = IncrementalParser(encoding="\udc80")  # no UTF-8 form for the codec lookup
    with pytest.raises(UnicodeEncodeError):
        parser.feed(b"<p>x</p>")
