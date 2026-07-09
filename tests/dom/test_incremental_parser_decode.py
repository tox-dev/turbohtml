"""IncrementalParser decodes bytes chunks with the same WHATWG decoder parse(bytes) uses."""

from __future__ import annotations

import pytest

from turbohtml import IncrementalParser, parse


def _streamed(raw: bytes, encoding: str, chunk: int) -> str:
    parser = IncrementalParser(encoding=encoding)
    for start in range(0, len(raw), chunk):
        parser.feed(raw[start : start + chunk])
    return parser.close().text


@pytest.mark.parametrize(
    ("encoding", "raw"),
    [
        pytest.param("big5", b"<p>\x87\x40\x88\x62\x98\x40", id="big5-combination-and-astral"),
        pytest.param("euc-kr", b"<p>\x81\x41\xb0\xa1", id="euc-kr"),
        pytest.param("shift_jis", b"<p>\x81\x60\x80", id="shift_jis"),
        pytest.param("euc-jp", b"<p>\xa1\xc1\x8f\xa1\xa1", id="euc-jp-jis0212"),
        pytest.param("gb18030", b"<p>\x80\x81\x30\x81\x30\xa3\xa0", id="gb18030-four-byte"),
        pytest.param("iso-2022-jp", b"<p>\x1b(I\x21\x1b(B", id="iso-2022-jp-katakana"),
        pytest.param("x-user-defined", b"<p>\x80\xff", id="x-user-defined"),
        pytest.param("koi8-u", b"<p>\xae\xbe", id="koi8-u"),
        pytest.param("utf-8", "<p>日本語".encode(), id="utf-8"),
        pytest.param("utf-16le", "<p>日本語".encode("utf-16-le"), id="utf-16le"),
        pytest.param("utf-16be", "<p>日本語".encode("utf-16-be"), id="utf-16be"),
    ],
)
@pytest.mark.parametrize("chunk", [pytest.param(1, id="one-byte"), pytest.param(2, id="two-byte")])
def test_a_sequence_split_across_chunks_decodes_as_one_piece(encoding: str, raw: bytes, chunk: int) -> None:
    # the decoder holds back the trailing bytes of a sequence the next chunk may complete, and its ISO-2022-JP mode
    # survives the boundary even though its bytes do not
    assert _streamed(raw, encoding, chunk) == parse(raw, encoding=encoding).text


def test_a_label_alias_reports_its_canonical_name() -> None:
    parser = IncrementalParser(encoding="iso-8859-1")
    parser.feed(b"caf\xe9")
    assert parser.close().encoding == "windows-1252"


def test_an_unsupported_label_raises_on_the_first_bytes_chunk() -> None:
    parser = IncrementalParser(encoding="latin-1")  # a CPython alias the spec does not name
    with pytest.raises(LookupError, match="unknown encoding: latin-1"):
        parser.feed(b"x")


def test_the_replacement_encoding_yields_one_replacement_char_for_the_whole_stream() -> None:
    parser = IncrementalParser(encoding="hz-gb-2312")
    parser.feed(b"<p>abc")
    parser.feed(b"def")
    assert parser.close().text == "�"
