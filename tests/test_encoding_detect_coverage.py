"""Branch-coverage drivers for the content detector (issue #182).

The golden cases in ``test_encoding_detect`` show the detector picks the right
encoding; these feed it every reachable byte transition so the scoring state
machines (the windows-1252 ordinal detector and the five CJK scorers) are fully
exercised. Each CJK candidate disqualifies on the first byte a foreign codec cannot
decode, so a buffer of one codec's valid sequences is walked only by that codec's
candidate, driving its branches without the others interfering.
"""

from __future__ import annotations

import pytest

from turbohtml import parse


def detected(raw: bytes) -> str | None:
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


@pytest.mark.parametrize("codec", ["gb18030", "big5", "shift_jis", "euc_jp", "euc_kr"])
def test_cjk_two_byte_branches(codec: str) -> None:
    # Drives the kana, kanji/hanzi/hangul, punctuation, PUA, adjacency, pending, and
    # long-word branches of one CJK scorer; the result only has to be a stable label.
    assert detected(_two_byte_corpus(codec)) is not None


def test_gb18030_four_byte_branches() -> None:
    # The four-byte sequences reach GBK's written==2 paths: a BMP extension scalar, an
    # astral ideograph (high surrogate 0xD840), plane-15 PUA (0xDB80), and an astral
    # non-ideograph (emoji), covering the non-EUC, PUA, and other arms.
    chars = "㐀\U00020000\U000f0000\U0001f600"
    assert detected(chars.encode("gb18030")) is not None


def test_gbk_euro_and_pua() -> None:
    # The euro sign and a private-use scalar the gb18030 codec does emit (as two-byte
    # sequences), covering the euro and PUA-penalty arms of the GBK scorer.
    assert detected("€的的".encode("gb18030")) is not None


def test_gbk_pua_ideograph() -> None:
    # The GB18030-required PUA mappings chardetng treats as ideographs, not private use.
    assert detected("的".encode("gb18030")) is not None


def test_shift_jis_half_width_katakana_voicing() -> None:
    # A voicing mark after a voicable katakana scores; after a non-voicable one it is
    # implausible -- both arms of the half-width katakana state machine.
    plausible = "ｶﾞ"  # KA + voiced sound mark
    implausible = "ｱﾞ"  # A + voiced sound mark (cannot be voiced)
    assert detected(plausible.encode("shift_jis")) is not None
    assert detected(implausible.encode("shift_jis")) is not None


def test_euc_jp_jis0212_and_kana() -> None:
    # JIS X 0212 (the 0x8F plane) reaches the other-kanji arm; the near-obsolete kana
    # wi/we reach their dedicated score.
    assert detected("ゐゑゐゑ漢字".encode("euc_jp")) is not None


def test_gbk_astral_and_extension_adjacency() -> None:
    # GBK's written==2 (four-byte GB18030) arms: an astral ideograph after an ASCII
    # letter (the adjacency penalty), an astral non-ideograph (the other arm), plus the
    # BMP extension-A and PUA-ideograph arms following an ASCII letter.
    assert detected("a\U00020000".encode("gb18030")) is not None
    assert detected("\U00030000".encode("gb18030")) is not None
    assert detected("a㐀".encode("gb18030")) is not None
    assert detected("a".encode("gb18030")) is not None


def test_cjk_latin_adjacency_arms() -> None:
    # The Latin-adjacency penalty arms that the homogeneous corpus never reaches: a
    # CJK scalar touching an ASCII letter on either side, across the scorers.
    assert detected("aあ".encode("shift_jis")) is not None  # kana after ASCII
    assert detected("漢a".encode("euc_jp")) is not None  # ASCII after kanji
    assert detected("aｱ".encode("euc_jp")) is not None  # half-width katakana after ASCII
    assert detected("aあ".encode("euc_jp")) is not None  # kana after ASCII
    assert detected("丂丂漢字".encode("euc_jp")) is not None  # JIS X 0212 (the 0x8F plane)
    # voicing marks after a voicable half-width katakana score (the plausible arm); after
    # a non-voicable one they are implausible -- both arms of the EUC-JP voicing ternary
    assert detected("ｶﾞ".encode("euc_jp")) is not None  # KA + dakuten (voiced)
    assert detected("ﾊﾟ".encode("euc_jp")) is not None  # HA + handakuten


def test_iso_2022_jp_invalid_is_not_detected() -> None:
    # an escape that opens an invalid ISO-2022-JP designator: detection falls through to
    # the windows-1252 fallback rather than claiming ISO-2022-JP
    assert detected(b"plain text\x1b$Z more text") == "windows-1252"


def test_hebrew_visual_order_detected() -> None:
    # The visual/logical tiebreak: this right-to-left Hebrew has its punctuation in
    # plausible visual positions, so it scores higher as ISO-8859-8 than as windows-1255.
    visual = "שלום לכולם זהו משפט בעברית עם סימני פיסוק רבים, נקודות. ועוד!"[::-1]
    assert detected(visual.encode("iso-8859-8")) == "ISO-8859-8"
    # the visual candidate is still scored, but prefixing logical-order clauses (a Hebrew
    # word then a period) gives the logical candidate the more plausible punctuation, so
    # the tiebreak does not fire and windows-1255 is kept -- the other arm of the compare
    logical_lead = "מילה. דבר. ועוד. שורה. טקסט. "
    assert detected((logical_lead + visual).encode("iso-8859-8")) == "windows-1255"
    # Hebrew-range bytes that read as Cyrillic win as windows-1251: the visual candidate
    # is alive and scored, but it neither outscores the winner nor trails windows-1255,
    # so the tiebreak's score/label guard short-circuits before the punctuation compare
    cyrillic_winner = bytes.fromhex("f5f1edf8ea20f2eeebe9e720f6f8e720f2e920efea")
    assert detected(cyrillic_winner) == "windows-1251"


def test_latin_lower_to_upper_case_transitions() -> None:
    # the Latin candidate penalizes an implausible lowercase->uppercase transition unless
    # the pair is plain ASCII: "aB" is the ASCII pair (no penalty), "éB" is not (penalty),
    # exercising both arms of that case-state branch
    assert detected("aBcD éB àC normal latin text here".encode("cp1252")) is not None


def test_arabic_zwnj_and_ascii_run() -> None:
    # windows-1256 reserves a class for the zero-width non-joiner (byte 0x9D); an Arabic
    # text carrying one, plus a run of consecutive ASCII, exercises the ZWNJ-aware pair
    # scoring and the ASCII-pair shortcut in the Arabic/French candidate.
    arabic = "العربية"
    # a digit on each side of the ZWNJ puts an above-boundary class next to it, so both
    # the current==ZWNJ and previous==ZWNJ arms of the pair score are taken
    raw = (arabic + " 1‌2 " + arabic + "  " + arabic + " hello world " + arabic).encode("cp1256")
    assert detected(raw) is not None


def test_logical_hebrew_punctuation_marks() -> None:
    # every ASCII punctuation mark the logical-order tracker recognizes (. , : ; ? !),
    # placed after Hebrew words so each is counted as plausible punctuation
    text = "שלום. עולם, וגם: כאן; מה? נהדר! עוד מילים בעברית כדי שתהיה ארוכה דיה"
    assert detected(text.encode("iso-8859-8")) is not None


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
    assert detected(raw) is not None
