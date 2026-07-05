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
        pytest.param("euc_kr", "EUC-KR", id="euc-kr-sweep-detected"),
    ],
)
def test_cjk_two_byte_branches(codec: str, expected: str) -> None:
    # Drives the kana, kanji/hanzi/hangul, punctuation, PUA, adjacency, pending, and long-word
    # branches of one CJK scorer. An exhaustive pair sweep carries no natural structure, so only
    # big5/euc-kr clear their own threshold; the others lose to the fallback or a rival scorer.
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
    # The GB18030-required PUA mappings chardetng treats as ideographs, not private use.
    assert _detected("\ue78d\ue816\ue81e\u7684".encode("gb18030")) == "GBK"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("ｶﾞ", "EUC-KR", id="voicable-katakana-plus-mark"),  # KA + voiced sound mark
        pytest.param("ｱﾞ", "EUC-KR", id="non-voicable-katakana-plus-mark"),  # A + voiced sound mark
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
        pytest.param("a\ue78d", "gb18030", "EUC-KR", id="pua-ideograph-after-ascii"),
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
        pytest.param("丂丂漢字", "euc_jp", "EUC-JP", id="jis-x-0212-plane"),
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
