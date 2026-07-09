"""The WHATWG decoders: the byte-to-code-point tables, and the spec's error handling."""

# ruff: noqa: RUF001  # the expected text is deliberately fullwidth or Cyrillic; that is what these bytes decode to

from __future__ import annotations

import pytest

import turbohtml.detect  # noqa: F401  # importing registers the whatwg-* codecs these tests decode through
from turbohtml._html import _decode as _decode_binding


def _decode(data: bytes, label: str) -> str:
    # decode through the registered whatwg-<label> codec rather than the private binding, so the tests exercise the
    # same C decoder over the public surface a caller reaches with bytes.decode
    return data.decode(f"whatwg-{label}")


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
        _decode_binding(b"", "no-such-encoding")


def test_decode_binding_rejects_a_non_bytes_first_argument() -> None:
    # the _decode binding parses (bytes, str); a str where the bytes buffer belongs fails PyArg_ParseTuple, the one
    # path the whatwg-* codec (which always hands it real bytes) cannot reach
    with pytest.raises(TypeError):
        _decode_binding("not bytes", "gbk")  # ty: ignore[invalid-argument-type]  # the wrong type is the point


@pytest.mark.parametrize(
    ("label", "data", "text"),
    [
        pytest.param("big5", b"\x81\xa1", "�", id="big5-lead-plus-in-range-trail-that-is-a-table-hole"),
        pytest.param("big5", b"\x81\x40", "�@", id="big5-pointer-below-the-index-base-then-ascii-pushback"),
        pytest.param("euc-kr", b"\x81\x80", "�", id="euc-kr-in-range-trail-that-is-a-table-hole"),
        pytest.param("shift_jis", b"\x81\xad", "�", id="shift-jis-in-range-trail-that-is-a-table-hole"),
        pytest.param("euc-jp", b"\xa2\xaf", "�", id="euc-jp-jis0208-in-range-pair-that-is-a-table-hole"),
        pytest.param("euc-jp", b"\x8f\xa1\xa1", "�", id="euc-jp-jis0212-in-range-pair-that-is-a-table-hole"),
    ],
)
def test_an_in_range_pair_that_maps_to_a_table_hole_is_one_replacement(label: str, data: bytes, text: str) -> None:
    # the pointer is inside the index's bounds, so the range guard passes; the table entry is zero, so the decoder
    # errors -- the false side of `pointer < size && table[pointer] != 0`
    assert _decode(data, label) == text


@pytest.mark.parametrize(
    ("data", "text"),
    [
        pytest.param(b"\xe0\x40", "漾", id="lead-in-the-0xe0-0xfc-block"),
        pytest.param(b"\xa0", "�", id="byte-between-the-katakana-and-lead-blocks"),
        pytest.param(b"\xfd", "�", id="byte-that-starts-no-sequence"),
    ],
)
def test_shift_jis_lead_byte_classes(data: bytes, text: str) -> None:
    # the second disjunct of the lead test (0xE0..0xFC) and the fall-through for a byte that is neither ASCII, single
    # katakana, nor a lead
    assert _decode(data, "shift_jis") == text


@pytest.mark.parametrize(
    ("data", "text"),
    [
        pytest.param(b"\x8e\xa1", "｡", id="0x8e-half-width-katakana"),
        pytest.param(b"\x8e\x20", "� ", id="0x8e-then-a-byte-below-the-katakana-range"),
        pytest.param(b"\x8e\xe0", "�", id="0x8e-then-a-byte-above-the-katakana-range"),
        pytest.param(b"\x8f\xb0\xa1", "丂", id="0x8f-jis0212-plane"),
        pytest.param(b"\x8f\x20", "� ", id="0x8f-then-a-byte-below-the-plane-range"),
        pytest.param(b"\x8f\xff", "�", id="0x8f-then-a-byte-above-the-plane-range"),
        pytest.param(b"\xa1\xff", "�", id="jis0208-lead-then-a-trail-above-its-range"),
    ],
)
def test_euc_jp_single_shift_bytes(data: bytes, text: str) -> None:
    # the 0x8E (JIS X 0201 katakana) and 0x8F (JIS X 0212 plane) single-shift leads, plus the leads followed by a byte
    # each range rejects: the katakana and plane range edges, and a two-byte pair whose trail is out of range
    assert _decode(data, "euc-jp") == text


@pytest.mark.parametrize(
    ("data", "text"),
    [
        pytest.param(b"\x81\x35\xf4\x37", "", id="pointer-7457-the-range-list-cannot-express"),
        pytest.param(b"\x84\x31\xdf\x30", "�", id="four-byte-pointer-in-the-unmapped-gap"),
        pytest.param(b"\xfe\x39\xfe\x39", "�", id="four-byte-pointer-past-the-last-scalar"),
        pytest.param(b"\x81\x30\x81\x2f", "�0�/", id="fourth-byte-below-the-digit-range"),
        pytest.param(b"\x81\x30\x20", "�0 ", id="two-byte-tail-then-a-non-lead-rewinds-two-bytes"),
        pytest.param(b"\x81\x30\xff", "�0�", id="second-tail-byte-above-the-lead-range"),
        pytest.param(b"\x81\x2f", "�/", id="lead-then-ascii-non-digit-pushes-the-ascii-byte-back"),
        pytest.param(b"\x81", "�", id="lead-alone-flushes-at-end-of-stream"),
        pytest.param(b"\x81\x30", "�", id="lead-and-digit-flush-at-end-of-stream"),
        pytest.param(b"\x81\x30\x81", "�", id="lead-digit-lead-flush-at-end-of-stream"),
    ],
)
def test_gb18030_four_byte_and_error_arms(data: bytes, text: str) -> None:
    # the U+E7C7 special pointer, the two out-of-range four-byte pointers (the unmapped gap and past the last scalar),
    # the rewind when a tail byte is out of range, and the end-of-stream flush of one, two, or three pending bytes
    assert _decode(data, "gb18030") == text


@pytest.mark.parametrize(
    ("data", "text"),
    [
        pytest.param(b"", "", id="empty-input"),
        pytest.param(b"\x0e", "�", id="ascii-state-rejects-shift-out"),
        pytest.param(b"\x0f", "�", id="ascii-state-rejects-shift-in"),
        pytest.param(b"\x80", "�", id="ascii-state-rejects-a-high-byte"),
        pytest.param(b"\x1b(JX\x1b(BY", "XY", id="escape-out-of-roman-state"),
        pytest.param(b"\x1b(J\x0e", "�", id="roman-state-rejects-shift-out"),
        pytest.param(b"\x1b(J\x0f", "�", id="roman-state-rejects-shift-in"),
        pytest.param(b"\x1b(J\x80", "�", id="roman-state-rejects-a-high-byte"),
        pytest.param(b"\x1b(I\x60", "�", id="katakana-state-rejects-a-byte-above-its-range"),
        pytest.param(b"\x1b$B", "", id="lead-state-finishes-cleanly-at-end-of-stream"),
        pytest.param(b"\x1b$B\x0e", "�", id="lead-state-rejects-shift-out"),
        pytest.param(b"\x1b$B\x7f", "�", id="lead-byte-above-its-range"),
        pytest.param(b"\x1b$@\x21\x21", "　", id="escape-dollar-at-selects-jis0208"),
        pytest.param(b"\x1b$B\x22\x2f", "�", id="jis0208-in-range-pair-that-is-a-table-hole"),
        pytest.param(b"\x1b$B\x21\x1b(B", "�", id="escape-interrupts-a-trail-byte"),
        pytest.param(b"\x1b$B\x21", "�", id="trail-byte-missing-at-end-of-stream"),
        pytest.param(b"\x1b$B\x21\x7f", "�", id="trail-byte-out-of-range"),
        pytest.param(b"\x1bZ", "�Z", id="escape-start-rejects-a-byte-and-pushes-it-back"),
        pytest.param(b"\x1b", "�", id="escape-start-at-end-of-stream"),
        pytest.param(b"\x1b(B\x1b(B", "�", id="back-to-back-escapes-emit-one-error"),
        pytest.param(b"\x1b(I\x20", "�", id="katakana-state-rejects-a-byte-below-its-range"),
    ],
)
def test_iso_2022_jp_state_machine_arms(data: bytes, text: str) -> None:
    # every error and escape arm of the ASCII, Roman, Katakana, Lead, Trail, Escape-start, and Escape states, plus the
    # empty-input shortcut and the clean end-of-stream flush from the Lead state
    assert _decode(data, "iso-2022-jp") == text


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
