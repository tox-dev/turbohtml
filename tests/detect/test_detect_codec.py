"""EncodingMatch.codec: the name that decodes, as opposed to the WHATWG name that labels."""

from __future__ import annotations

import pytest

from turbohtml import parse
from turbohtml.detect import detect


@pytest.mark.parametrize(
    ("data", "encoding", "codec"),
    [
        pytest.param(b'<meta charset="x-mac-cyrillic">\xd0', "x-mac-cyrillic", "whatwg-x-mac-cyrillic", id="mac"),
        pytest.param(b'<meta charset="hz-gb-2312">x', "replacement", "whatwg-replacement", id="replacement"),
        pytest.param(b'<meta charset="big5">\x87\x40', "Big5", "whatwg-big5", id="big5"),
        pytest.param(b'<meta charset="shift_jis">\x80', "Shift_JIS", "whatwg-shift_jis", id="shift_jis"),
        pytest.param(b"\xef\xbb\xbfhi", "UTF-8-SIG", "whatwg-utf-8-sig", id="utf-8-bom"),
        pytest.param(b"\xff\xfeh\x00", "UTF-16LE", "whatwg-utf-16le", id="utf-16le-bom"),
    ],
)
def test_codec_names_a_registered_decoder(data: bytes, encoding: str, codec: str) -> None:
    match = detect(data)
    assert match.encoding == encoding
    assert match.codec == codec


@pytest.mark.parametrize(
    ("data", "text"),
    [
        pytest.param(b'<meta charset="x-mac-cyrillic">\xd0', "\u2013", id="x-mac-cyrillic-has-no-cpython-codec"),
        pytest.param(b'<meta charset="hz-gb-2312">x', "\ufffd", id="replacement-has-no-cpython-codec"),
    ],
)
def test_the_whatwg_name_alone_cannot_be_decoded(data: bytes, text: str) -> None:
    match = detect(data)
    assert match.encoding is not None
    assert match.codec is not None
    with pytest.raises(LookupError):
        data.decode(match.encoding)
    assert data.decode(match.codec).endswith(text)  # the codec always can, and decodes as the parser does


@pytest.mark.parametrize(
    ("data", "text"),
    [
        pytest.param(b'<meta charset="koi8-u">\xae', "ў", id="koi8-u-is-koi8-ru"),
        pytest.param(b'<meta charset="big5">\x87\x40', "䏰", id="big5-index-is-a-superset"),
        pytest.param(b'<meta charset="gbk">\x80', "€", id="gbk-euro"),
    ],
)
def test_decoding_through_codec_reproduces_what_the_parser_saw(data: bytes, text: str) -> None:
    match = detect(data)
    assert match.codec is not None
    assert data.decode(match.codec).endswith(text)


@pytest.mark.parametrize(
    ("data", "text"),
    [
        pytest.param(b"\xef\xbb\xbfhi", "hi", id="utf-8-sig-strips-the-mark"),
        pytest.param(b"\xff\xfeh\x00", "\ufeffh", id="utf-16le-keeps-the-mark"),
    ],
)
def test_a_byte_order_mark_codec_delegates_to_cpython(data: bytes, text: str) -> None:
    # CPython's UTF-8 and UTF-16 decoders match the spec, so the whatwg-* name resolves straight to them
    match = detect(data)
    assert match.codec is not None
    assert data.decode(match.codec) == text


def test_a_whatwg_codec_refuses_to_encode() -> None:
    # the generated tables are decode-side only; encoding to a legacy charset is a separate spec algorithm
    with pytest.raises(UnicodeError, match="decodes only"):
        "x".encode("whatwg-big5")


def test_an_unknown_whatwg_codec_is_not_registered() -> None:
    # non-empty input: CPython answers b"".decode(anything) with "" before it ever resolves the codec
    with pytest.raises(LookupError):
        b"x".decode("whatwg-no-such-encoding")


def test_the_no_match_sentinel_has_no_codec() -> None:
    match = detect(b"")
    assert match.encoding is None
    assert match.codec is None


def test_pure_ascii_agrees_with_the_parser() -> None:
    # "ascii" is not an encoding the spec names; its label resolves to windows-1252, which decodes ASCII identically
    assert detect(b"plain ascii").encoding == "windows-1252"
    assert parse(b"plain ascii", detect_encoding=True).encoding == "windows-1252"
