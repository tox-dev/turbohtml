"""The WHATWG decoders: the byte-to-code-point tables, and the spec's error handling."""

# ruff: noqa: RUF001  # the expected text is deliberately fullwidth or Cyrillic; that is what these bytes decode to

from __future__ import annotations

import pytest

from turbohtml._html import _decode


@pytest.mark.parametrize(
    ("label", "data", "text", "codec"),
    [
        pytest.param("koi8-u", b"\xae\xbe", "ўЎ", "koi8_u", id="koi8-u-is-koi8-ru"),
        pytest.param("big5", b"\x87\x40", "䏰", "big5", id="big5-index-is-a-superset"),
        pytest.param("euc-kr", b"\x81\x41", "갂", "euc_kr", id="euc-kr-is-windows-949"),
        pytest.param("shift_jis", b"\x81\x60", "～", "shift_jis", id="shift_jis-is-windows-31j"),
        pytest.param("euc-jp", b"\xa1\xc1", "～", "euc_jp", id="euc-jp-tilde"),
        pytest.param("gbk", b"\xa3\xa0", "　", "gb18030", id="gb18030-is-the-2005-revision"),
        pytest.param("gbk", b"\x80", "€", "gb18030", id="gbk-0x80-is-the-euro-sign"),
        pytest.param("shift_jis", b"\x80", "\x80", "shift_jis", id="shift_jis-0x80-passes-through"),
        pytest.param("windows-1255", b"\xca", "ֺ", "cp1255", id="windows-1255-0xca"),
    ],
)
def test_whatwg_decoder_disagrees_with_the_cpython_codec(label: str, data: bytes, text: str, codec: str) -> None:
    # every one of these is a byte sequence CPython's same-named codec decodes differently or rejects outright
    assert _decode(data, label) == text
    assert data.decode(codec, errors="replace") != text


@pytest.mark.parametrize(
    ("label", "byte"),
    [
        pytest.param("windows-874", 0x81, id="windows-874"),
        pytest.param("windows-1250", 0x81, id="windows-1250"),
        pytest.param("windows-1251", 0x98, id="windows-1251"),
        pytest.param("windows-1252", 0x81, id="windows-1252"),
        pytest.param("windows-1253", 0x81, id="windows-1253"),
        pytest.param("windows-1254", 0x81, id="windows-1254"),
        pytest.param("windows-1255", 0x81, id="windows-1255"),
        pytest.param("windows-1257", 0x81, id="windows-1257"),
        pytest.param("windows-1258", 0x81, id="windows-1258"),
    ],
)
def test_unassigned_c1_bytes_decode_to_their_control(label: str, byte: int) -> None:
    # the spec's single-byte indexes map an unassigned 0x80..0x9F byte to the matching C1 control; every CPython codec
    # raises there, so errors="replace" turned each one into U+FFFD
    assert _decode(bytes([byte]), label) == chr(byte)


@pytest.mark.parametrize(
    ("label", "data", "text"),
    [
        pytest.param("big5", b"\x81\x41", "�A", id="big5-ascii-trail-is-pushed-back"),
        pytest.param("big5", b"\x81\xff", "�", id="big5-non-ascii-trail-is-consumed"),
        pytest.param("euc-kr", b"\xff\x41", "�A", id="euc-kr-ascii-trail-is-pushed-back"),
        pytest.param("shift_jis", b"\x81\x20", "� ", id="shift_jis-ascii-trail-is-pushed-back"),
        pytest.param("gbk", b"\x81\x30\x81\x41", "�0丄", id="gb18030-partial-four-byte-rewinds"),
        pytest.param("gbk", b"\xff", "�", id="gb18030-lone-invalid-byte"),
    ],
)
def test_error_handling_follows_the_spec_not_the_codec(label: str, data: bytes, text: str) -> None:
    # the spec prepends an ASCII trail byte back onto the stream and consumes a non-ASCII one, so the count and the
    # position of the U+FFFD replacements differ from any errors="replace" codec
    assert _decode(data, label) == text


@pytest.mark.parametrize(
    ("data", "text"),
    [
        pytest.param(b"\x88\x62", "Ê̄", id="capital-macron"),
        pytest.param(b"\x88\x64", "Ê̌", id="capital-caron"),
        pytest.param(b"\x88\xa3", "ê̄", id="small-macron"),
        pytest.param(b"\x88\xa5", "ê̌", id="small-caron"),
    ],
)
def test_big5_combination_pointers_decode_to_two_code_points(data: bytes, text: str) -> None:
    assert _decode(data, "big5") == text


def test_big5_astral_pointer_decodes_above_the_bmp() -> None:
    assert _decode(b"\x98\x40", "big5") == "\U00026d26"


def test_gb18030_four_byte_sequence_uses_the_range_index() -> None:
    assert _decode(b"\x81\x30\x81\x30", "gb18030") == "\x80"


def test_gb18030_four_byte_sequence_reaches_the_astral_planes() -> None:
    assert _decode(b"\x90\x30\x81\x30", "gb18030") == "\U00010000"


def test_iso_2022_jp_decodes_the_half_width_katakana_state() -> None:
    # ESC ( I is a state CPython's iso2022_jp codec does not implement at all
    assert _decode(b"\x1b(I\x21\x1b(B", "iso-2022-jp") == "｡"


def test_iso_2022_jp_roman_state_replaces_backslash_and_tilde() -> None:
    assert _decode(b"\x1b(J\x5c\x5d\x7e", "iso-2022-jp") == "¥]‾"


def test_x_user_defined_maps_high_bytes_to_the_private_use_area() -> None:
    assert _decode(b"a\x80\xff", "x-user-defined") == "a"


@pytest.mark.parametrize(
    ("data", "text"),
    [
        pytest.param(b"", "", id="empty"),
        pytest.param(b"anything", "�", id="non-empty"),
    ],
)
def test_replacement_encoding_refuses_the_whole_stream(data: bytes, text: str) -> None:
    # the stateful ISO-2022 and HZ byte streams can smuggle markup past a sanitizer, so the spec collapses them
    assert _decode(data, "replacement") == text


def test_iso_8859_8_i_shares_the_iso_8859_8_index() -> None:
    assert _decode(b"\xe0", "iso-8859-8-i") == _decode(b"\xe0", "iso-8859-8")


def test_unknown_label_raises_lookup_error() -> None:
    with pytest.raises(LookupError, match="unknown encoding: no-such-encoding"):
        _decode(b"", "no-such-encoding")


@pytest.mark.parametrize(
    ("label", "data", "text"),
    [
        pytest.param("ibm866", b"\xf8", "°", id="latin-1-range-in-a-non-latin-table"),
        pytest.param("windows-1252", b"caf\xe9", "café", id="latin-1-range-in-windows-1252"),
        pytest.param("iso-8859-5", b"\xd0", "а", id="cyrillic"),
    ],
)
def test_decoded_str_is_in_its_narrowest_form(label: str, data: bytes, text: str) -> None:
    # a str whose kind is wider than its widest code point compares unequal to its own value, because CPython's
    # equality checks the kind before the content; sizing against the table's ceiling rather than the real maximum
    # produced exactly that
    decoded = _decode(data, label)
    assert decoded == text
    assert hash(decoded) == hash(text)
