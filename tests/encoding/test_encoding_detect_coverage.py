"""Branch-coverage drivers for the content detector (issue #182).

Each input here is crafted to walk a particular arm of a scoring state machine (the windows-1252
ordinal detector and the five CJK scorers). The corpora are deliberately degenerate -- an exhaustive
sweep of a codec's byte pairs, or a single adjacency -- so the winning label is often *not* the codec
that produced the bytes; a uniform sweep carries none of the structure real text does. Every case
pins the exact label the detector reports for that input so a change in the scoring machine that
shifts the winner fails here instead of passing silently (the golden real-text cases live in
``test_encoding_detect``).
"""

from __future__ import annotations

import pytest

from turbohtml import parse


def _detected(raw: bytes) -> str | None:
    return parse(raw, detect_encoding=True).encoding


def _two_byte_corpus(codec: str) -> bytes:
    # Every lead/trail pair the codec decodes cleanly, concatenated. Adjacent valid
    # pairs stay valid, so one parse walks every (lead, scalar) the codec can emit.
    out = bytearray()
    for lead in range(0x81, 0xFF):
        for trail in range(0x40, 0xFF):
            pair = bytes((lead, trail))
            try:
                pair.decode(codec)
            except UnicodeDecodeError:
                continue
            out += pair
    return bytes(out)


@pytest.mark.parametrize(
    ("codec", "expected"),
    [
        pytest.param("gb18030", "windows-1252", id="gb18030-sweep-scores-nothing-falls-back"),
        pytest.param("big5", "Big5", id="big5-sweep-detected"),
        pytest.param("shift_jis", "windows-1252", id="shift-jis-sweep-scores-nothing-falls-back"),
        pytest.param("euc_jp", "GBK", id="euc-jp-sweep-scores-gbk-higher"),
        pytest.param("euc_kr", "Big5", id="euc-kr-sweep-scores-big5-higher"),
    ],
)
def test_cjk_two_byte_branches(codec: str, expected: str) -> None:
    # Drives the kana, kanji/hanzi/hangul, punctuation, PUA, adjacency, pending, and long-word
    # branches of one CJK scorer. An exhaustive pair sweep carries no natural structure, so only
    # the big5 sweep clears its own threshold; the others lose to the fallback or a rival scorer
    # (the euc-kr sweep's Hanja-after-Hangul penalties sink it below Big5).
    assert _detected(_two_byte_corpus(codec)) == expected


def test_gb18030_four_byte_branches() -> None:
    # The four-byte sequences reach GBK's written==2 paths: a BMP extension scalar, an
    # astral ideograph (high surrogate 0xD840), plane-15 PUA (0xDB80), and an astral
    # non-ideograph (emoji), covering the non-EUC, PUA, and other arms.
    chars = "㐀\U00020000\U000f0000\U0001f600"
    assert _detected(chars.encode("gb18030")) == "windows-1252"


def test_gbk_euro_and_pua() -> None:
    # The euro sign and a private-use scalar the gb18030 codec does emit (as two-byte
    # sequences), covering the euro and PUA-penalty arms of the GBK scorer.
    assert _detected("\u20ac\u7684\ue000\u7684".encode("gb18030")) == "Big5"


def test_gbk_pua_ideograph() -> None:
    # The GB18030-required PUA mappings chardetng treats as ideographs, not private use, in the bytes the WHATWG
    # gb18030 decoder actually emits them from (U+E816, U+E831, U+E83B, U+E855), preceded by an ASCII letter for the
    # Latin-adjacency arm and followed by a non-ideograph PUA scalar (U+E4C6) for the else arm.
    assert _detected(b"a\xfe\x51\xfe\x6c\xfe\x76\xfe\x91\xa1\x40") == "windows-1252"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("ｶﾞ", "ISO-8859-5", id="voicable-katakana-plus-mark"),  # KA + voiced sound mark
        pytest.param("ｱﾞ", "ISO-8859-5", id="non-voicable-katakana-plus-mark"),  # A + voiced sound mark
    ],
)
def test_shift_jis_half_width_katakana_voicing(text: str, expected: str) -> None:
    # A voicing mark after a voicable katakana scores; after a non-voicable one it is implausible.
    # Both arms of the half-width-katakana state machine run; the sweep's winner is EUC-KR either way.
    assert _detected(text.encode("shift_jis")) == expected


def test_euc_jp_jis0212_and_kana() -> None:
    # JIS X 0212 (the 0x8F plane) reaches the other-kanji arm; the near-obsolete kana
    # wi/we reach their dedicated score.
    assert _detected("ゐゑゐゑ漢字".encode("euc_jp")) == "Big5"


@pytest.mark.parametrize(
    ("text", "codec", "expected"),
    [
        pytest.param("a\U00020000", "gb18030", "windows-1252", id="astral-ideograph-after-ascii"),
        pytest.param("\U00030000", "gb18030", "windows-1250", id="astral-non-ideograph"),
        pytest.param("a㐀", "gb18030", "windows-1252", id="bmp-extension-after-ascii"),
        pytest.param("a\ue78d", "gb18030", "windows-1252", id="pua-ideograph-after-ascii"),
    ],
)
def test_gbk_astral_and_extension_adjacency(text: str, codec: str, expected: str) -> None:
    # GBK's written==2 (four-byte GB18030) arms: an astral ideograph after an ASCII letter (the
    # adjacency penalty), an astral non-ideograph (the other arm), plus the BMP extension-A and
    # PUA-ideograph arms following an ASCII letter.
    assert _detected(text.encode(codec)) == expected


@pytest.mark.parametrize(
    ("text", "codec", "expected"),
    [
        pytest.param("aあ", "shift_jis", "windows-1252", id="kana-after-ascii-sjis"),
        pytest.param("漢a", "euc_jp", "EUC-KR", id="ascii-after-kanji-eucjp"),
        pytest.param("aｱ", "euc_jp", "windows-1252", id="halfwidth-katakana-after-ascii"),
        pytest.param("aあ", "euc_jp", "GBK", id="kana-after-ascii-eucjp"),
        pytest.param("丂丂漢字", "euc_jp", "EUC-KR", id="jis-x-0212-plane"),
        pytest.param("ｶﾞ", "euc_jp", "Shift_JIS", id="voicable-halfwidth-katakana-eucjp"),
        pytest.param("ﾊﾟ", "euc_jp", "Shift_JIS", id="handakuten-halfwidth-katakana-eucjp"),
    ],
)
def test_cjk_latin_adjacency_arms(text: str, codec: str, expected: str) -> None:
    # The Latin-adjacency penalty arms the homogeneous corpus never reaches: a CJK scalar touching
    # an ASCII letter on either side, the JIS X 0212 plane, and both voicing-mark arms, across the
    # scorers. Each short input still resolves to a single winning label.
    assert _detected(text.encode(codec)) == expected


def test_iso_2022_jp_invalid_is_not_detected() -> None:
    # an escape that opens an invalid ISO-2022-JP designator: detection falls through to
    # the windows-1252 fallback rather than claiming ISO-2022-JP
    assert _detected(b"plain text\x1b$Z more text") == "windows-1252"


def test_hebrew_visual_order_detected() -> None:
    # The visual/logical tiebreak: this right-to-left Hebrew has its punctuation in
    # plausible visual positions, so it scores higher as ISO-8859-8 than as windows-1255.
    visual = "שלום לכולם זהו משפט בעברית עם סימני פיסוק רבים, נקודות. ועוד!"[::-1]
    assert _detected(visual.encode("iso-8859-8")) == "ISO-8859-8"
    # the visual candidate is still scored, but prefixing logical-order clauses (a Hebrew
    # word then a period) gives the logical candidate the more plausible punctuation, so
    # the tiebreak does not fire and windows-1255 is kept -- the other arm of the compare
    logical_lead = "מילה. דבר. ועוד. שורה. טקסט. "
    assert _detected((logical_lead + visual).encode("iso-8859-8")) == "windows-1255"
    # Hebrew-range bytes that read as Cyrillic win as windows-1251: the visual candidate
    # is alive and scored, but it neither outscores the winner nor trails windows-1255,
    # so the tiebreak's score/label guard short-circuits before the punctuation compare
    cyrillic_winner = bytes.fromhex("f5f1edf8ea20f2eeebe9e720f6f8e720f2e920efea")
    assert _detected(cyrillic_winner) == "windows-1251"


def test_latin_lower_to_upper_case_transitions() -> None:
    # the Latin candidate penalizes an implausible lowercase->uppercase transition unless the pair
    # is plain ASCII: "aB" is the ASCII pair (no penalty), "éB" is not (penalty), exercising both
    # arms of that case-state branch. The short mixed run ultimately scores highest as Shift_JIS.
    assert _detected("aBcD éB àC normal latin text here".encode("cp1252")) == "Shift_JIS"


def test_arabic_zwnj_and_ascii_run() -> None:
    # windows-1256 reserves a class for the zero-width non-joiner (byte 0x9D); an Arabic
    # text carrying one, plus a run of consecutive ASCII, exercises the ZWNJ-aware pair
    # scoring and the ASCII-pair shortcut in the Arabic/French candidate.
    arabic = "العربية"
    # a digit on each side of the ZWNJ puts an above-boundary class next to it, so both
    # the current==ZWNJ and previous==ZWNJ arms of the pair score are taken
    raw = (arabic + " 1‌2 " + arabic + "  " + arabic + " hello world " + arabic).encode("cp1256")
    assert _detected(raw) == "windows-1256"


def test_logical_hebrew_punctuation_marks() -> None:
    # every ASCII punctuation mark the logical-order tracker recognizes (. , : ; ? !),
    # placed after Hebrew words so each is counted as plausible punctuation
    text = "שלום. עולם, וגם: כאן; מה? נהדר! עוד מילים בעברית כדי שתהיה ארוכה דיה"
    assert _detected(text.encode("iso-8859-8")) == "windows-1255"


def test_windows_1252_ordinal_state_machine() -> None:
    # The windows-1252 ordinal detector rewards Spanish/Portuguese ordinals (1o, 2a,
    # No, copyright). These segments walk every state and transition; double spaces
    # reset each one to the start state so the next is read fresh.
    segments = [
        "1º",  # digit then masculine ordinal
        "2ª",  # digit then feminine ordinal
        "Nª",  # N then feminine -> undo state
        "Nªx",  # ... then a letter (undo else arm)
        "Nº",  # N then masculine -> undo-or-digit state
        "Nº5",  # ... then a digit
        "Nºx",  # ... then a letter
        "nº",  # lowercase n then masculine
        "n.x",  # lowercase n, period, letter
        "N.º",  # N, period, masculine -> space-or-digit state
        "N.º5",  # ... then a digit
        "N.ºx",  # ... then a letter
        "N.x",  # N, period, letter
        "N.",  # N, period, then a reset space
        "N ",  # N then space
        "Nz",  # N then letter
        "n ",  # n then space
        "nz",  # n then letter
        "Mª",  # M (feminine start) then feminine
        "Mx",  # M then letter
        "M ",  # M then space
        "Dª",  # D feminine start
        "S ",  # S feminine start then space
        "©",  # copyright then reset space -> bonus
        "©x",  # copyright then letter
        "Iº",  # Roman numeral then masculine
        "Iª",  # Roman numeral then feminine
        "IIX",  # Roman stays Roman across I and X
        "Ix",  # Roman then letter
        "I ",  # Roman then space
        "IVx",  # Roman stays Roman, then letter
        "IV ",  # Roman stays Roman, then space
        "Vº",  # Roman V then masculine
        "X ",  # Roman X then space
        "33ª",  # digit stays digit, then feminine
        "3x",  # digit then letter
        "3 ",  # digit then space
        "ªx",  # bare feminine ordinal then letter
        "z",  # a plain other letter
    ]
    raw = (" " + "  ".join(segments) + " ").encode("cp1252")
    assert _detected(raw) == "windows-1252"


def test_big5_written_two_combining_base() -> None:
    # Big5's written==2 path for the four pointers that decode to U+00CA/U+00EA plus a combining mark: the base letter
    # scores as CJK_OTHER and the drained mark is not scored again, alongside an astral scalar for the else arm.
    assert _detected(b"\x88\x62\x88\xa3\x98\x40") == "Big5"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param(b"\x81\x30\xfe\x41" * 4, "windows-1252", id="uppercase-letter-after-a-lead"),
        pytest.param(b"\x81\x30\xfe\x61" * 4, "windows-1252", id="lowercase-letter-after-a-lead"),
        pytest.param(b"a\xfe\xff" * 3, "windows-1254", id="trailing-0xff"),
        pytest.param(b"\x81\x30\xfe\x80" * 4, "windows-1252", id="high-byte-that-is-not-0xff"),
        pytest.param(b"\xfe\x20" * 4, "windows-1252", id="other-byte-after-0xfe"),
        pytest.param(b"\xa0\xff" * 4, "IBM866", id="single-byte-extension-after-0xa0"),
        pytest.param(b"\xfd\xff" * 4, "windows-1251", id="single-byte-extension-after-0xfd"),
    ],
)
def test_gbk_malformed_single_byte_extension(raw: bytes, expected: str) -> None:
    # chardetng's GBK single-byte-extension arms: a 0xA0/0xFD/0xFE lead followed by an ASCII letter, 0xFF, another
    # high byte, or another byte, reached through the four-byte-interrupted decode that lets a letter be the failing
    # byte. Each pins the label chardetng reports.
    assert _detected(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param(b"\x81\xad" * 4, "GBK", id="hole-in-the-lead-and-trail-range"),
        pytest.param(b"\x82\x40" * 4, "GBK", id="excluded-0x82-below-0xfa"),
        pytest.param(b"\x82\xfa" * 4, "GBK", id="excluded-0x82-at-0xfa"),
        pytest.param(b"\x84\xdd" * 4, "windows-1254", id="excluded-0x84-in-0xdd-0xe4"),
        pytest.param(b"\x84\xfb" * 4, "GBK", id="excluded-0x84-at-0xfb"),
        pytest.param(b"\x84\x61" * 4, "GBK", id="excluded-0x84-below-0xdd"),
        pytest.param(b"\x86\xf2" * 4, "GBK", id="excluded-0x86-in-0xf2-0xfa"),
        pytest.param(b"\x86\x40" * 4, "GBK", id="excluded-0x86-below-0xf2"),
        pytest.param(b"\x86\xfb" * 4, "GBK", id="excluded-0x86-above-0xfa"),
        pytest.param(b"\x87\x77" * 4, "Big5", id="excluded-0x87-in-0x77-0x7d"),
        pytest.param(b"\x87\x5e" * 4, "Big5", id="excluded-0x87-below-0x77"),
        pytest.param(b"\x87\x7f" * 4, "windows-1252", id="excluded-0x87-above-0x7d"),
        pytest.param(b"\xfc\xf5" * 4, "windows-1251", id="excluded-0xfc-at-0xf5"),
        pytest.param(b"\xfc\x4c" * 4, "Big5", id="excluded-0xfc-below-0xf5"),
        pytest.param(b"\xa0" * 4, "GBK", id="single-byte-0xa0"),
        pytest.param(b"\xfd" * 4, "windows-1254", id="single-byte-0xfd-or-above"),
    ],
)
def test_shift_jis_malformed_extension(raw: bytes, expected: str) -> None:
    # chardetng's Shift_JIS extension penalty: a lead/trail pair the decoder rejects but that is in range and not one
    # of the excluded MacJapanese boundary pairs (which end the candidate instead), plus the single-byte 0xA0/0xFD+.
    assert _detected(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param(b"\xa2\xaf" * 4, "IBM866", id="plane-1-general"),
        pytest.param(b"\xa8\xdf" * 4, "Big5", id="plane-1-excluded-0xa8-in-range"),
        pytest.param(b"\xa8\xe7" * 4, "Big5", id="plane-1-0xa8-above-range"),
        pytest.param(b"\xac\xf4" * 4, "Big5", id="plane-1-excluded-0xac-in-range"),
        pytest.param(b"\xac\xfd" * 4, "Big5", id="plane-1-0xac-above-range"),
        pytest.param(b"\xac\xa1" * 4, "Big5", id="plane-1-0xac-below-range"),
        pytest.param(b"\xad\xd8" * 4, "Big5", id="plane-1-excluded-0xad-in-range"),
        pytest.param(b"\xad\xfd" * 4, "Big5", id="plane-1-0xad-above-range"),
        pytest.param(b"\xad\xbf" * 4, "Big5", id="plane-1-0xad-below-range"),
        pytest.param(b"\x8f\xa1\xa1" * 4, "Shift_JIS", id="plane-2-general"),
        pytest.param(b"\x8f\xa2\xa1" * 4, "Shift_JIS", id="plane-2-excluded-0xa2"),
        pytest.param(b"\x8f\xa6\xa1" * 4, "Shift_JIS", id="plane-2-excluded-0xa6"),
        pytest.param(b"\x8f\xa7\xa1" * 4, "Shift_JIS", id="plane-2-excluded-0xa7"),
        pytest.param(b"\x8f\xa9\xa3" * 4, "Shift_JIS", id="plane-2-excluded-0xa9"),
        pytest.param(b"\x8f\xaa\xb9" * 4, "Shift_JIS", id="plane-2-excluded-0xaa"),
        pytest.param(b"\x8f\xab\xbc" * 4, "Shift_JIS", id="plane-2-excluded-0xab"),
        pytest.param(b"\x8f\xed\xe4" * 4, "Shift_JIS", id="plane-2-excluded-0xed"),
        pytest.param(b"\x8f\xfe\xf7" * 4, "windows-1252", id="plane-2-0xfe-at-or-above-0xf7"),
        pytest.param(b"\x8f\xfe\xa1" * 4, "windows-1252", id="plane-2-0xfe-below-0xf7"),
        pytest.param(b"\x8e\xe0" * 4, "Shift_JIS", id="in-pair-prev-below-0xa1"),
    ],
)
def test_euc_jp_malformed_planes(raw: bytes, expected: str) -> None:
    # chardetng's EUC-JP extension penalty: the JIS X 0208 plane-1 and JIS X 0212 plane-2 recognition tests, including
    # every prev-byte exclusion and its in-range/out-of-range edges, plus a single-shift 0x8E lead whose failing pair
    # has a prev byte below 0xA1.
    assert _detected(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param(b"\xa1\xa4" * 6, "windows-874", id="scalar-at-or-above-0xfb00"),
        pytest.param(b"\xf9\xd4" * 6, "EUC-JP", id="compatibility-ideograph-0xf900-0xfaff"),
    ],
)
def test_euc_jp_other_kanji_scalar_ranges(raw: bytes, expected: str) -> None:
    # the EUC-JP "other kanji" arm's upper edge: a fullwidth scalar at or above U+FB00 and a compatibility
    # ideograph inside U+F900..U+FAFF, the two sides of the range test.
    assert _detected(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param(b"\x81\xa1" * 4, "GBK", id="pua-penalty-trail-in-0xa1-0xfe"),
        pytest.param(b"\x81\x40" * 4, "Shift_JIS", id="pua-penalty-trail-in-0x40-0x7e"),
        pytest.param(b"\xff\xff" * 4, "windows-1256", id="pua-penalty-prev-0xff"),
        pytest.param(b"z\xfe\xff" * 3, "windows-1252", id="single-byte-extension-0xff"),
        pytest.param(b"\xa0\x20" * 4, "windows-1252", id="single-byte-extension-other"),
        pytest.param(b"\xfd\x20" * 4, "windows-1254", id="single-byte-extension-after-0xfd"),
    ],
)
def test_big5_malformed(raw: bytes, expected: str) -> None:
    # chardetng's Big5 PUA penalty (an in-range unmapped pair, on both trail ranges and a 0xFF prev byte) and the
    # single-byte-extension arms after a 0xA0/0xFD/0xFE lead.
    assert _detected(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param(b"\xfe\xa1" * 4, "Big5", id="pua-penalty-prev-0xfe"),
        pytest.param(b"\xc9\xa1" * 4 + b"\xff", "windows-1252", id="pua-penalty-prev-0xc9"),
        pytest.param(b"\x41\xfe\xa1" * 3, "windows-1252", id="pua-penalty-after-ascii"),
        pytest.param(b"\xb0\xa1\xfe\xa1" * 3, "Big5", id="pua-penalty-after-hangul"),
        pytest.param(b"\xff\x41" * 3, "windows-1252", id="scored-scalar-with-prev-byte-0xff"),
        pytest.param(b"\xa1\x7b" * 4, "ISO-8859-2", id="mac-korean-prev-0xa1"),
        pytest.param(b"\xa3\x7c" * 4, "windows-1250", id="mac-korean-prev-0xa3"),
        pytest.param(b"\x81\x80" * 4, "IBM866", id="single-byte-extension-0x80"),
        pytest.param(b"\x81\xff" * 4, "windows-1256", id="single-byte-extension-0xff"),
        pytest.param(b"\x81\x20" * 4, "windows-1252", id="single-byte-extension-other"),
    ],
)
def test_euc_kr_malformed(raw: bytes, expected: str) -> None:
    # chardetng's EUC-KR PUA penalty (prev 0xC9/0xFE, after ASCII and after Hangul), the MacKorean recognition, the
    # single-byte-extension arms, and a scored scalar whose preceding byte is 0xFF.
    assert _detected(raw) == expected
