"""The TLD hint on the Detection config: what a host's rightmost DNS label tells the detector."""

from __future__ import annotations

import pytest

from turbohtml.detect import Detection, EncodingDetector, detect, detect_all

# Czech in ISO-8859-2. The hint decides on text like this, where the Central European encodings score alike and the
# Cyrillic and Western TLDs disagree about whether ISO-8859-2 or windows-1252 should read it.
_CZECH: bytes = "Příliš žluťoučký kůň úpěl ďábelské ódy".encode("iso-8859-2")

# The same text, short enough that no candidate scores well and the TLD's own encoding is left to answer.
_CZECH_SHORT: bytes = "Příliš žluťoučký kůň úpěl".encode("iso-8859-2")

# The two Chinese scripts, which nothing but a TLD separates at this length.
_TRADITIONAL: bytes = "繁體中文字元測試內容".encode("big5")
_SIMPLIFIED: bytes = "天地玄黄宇宙洪荒日月盈昃".encode("gb18030")

# Byte soup that kills one candidate of a sibling pair and leaves the other scoring, so the TLD falls back on its
# sibling script. Big5 dies on the first, ISO-8859-2 on the second; chardetng answers as asserted below.
_NO_TRADITIONAL: bytes = bytes.fromhex("e098bcf194a9")
_NO_CENTRAL_ISO: bytes = bytes.fromhex("a699dd")

# Byte soup too short to hold a two-letter word in either script. ISO-8859-6 is native to .sa and windows-1256 to
# .my without being what either domain expects, so each one scores there and nowhere else.
_SHORT_ARABIC_ISO: bytes = bytes.fromhex("bfed")
_SHORT_ARABIC_WINDOWS: bytes = bytes.fromhex("d6a59ebd")


def _ranked(raw: bytes, tld: str | None = None) -> set[str | None]:
    """The encodings that scored, which is not every encoding ``detect_all`` reports: the winner leads it either way."""
    return {match.encoding for match in detect_all(raw, Detection(tld=tld))[1:]}


@pytest.mark.parametrize(
    ("tld", "encoding"),
    [
        pytest.param(None, "ISO-8859-2", id="no-hint"),
        pytest.param("com", "ISO-8859-2", id="a-generic-label-hints-nothing"),
        pytest.param("cz", "ISO-8859-2", id="the-native-encoding-keeps-its-score"),
        pytest.param("pl", "ISO-8859-2", id="a-sibling-central-label-agrees"),
        pytest.param("ru", "windows-1252", id="cyrillic-zeroes-the-central-candidates"),
        pytest.param("de", "windows-1252", id="a-western-label-does-too"),
        pytest.param("zz", "windows-1252", id="an-unlisted-country-code-reads-as-western"),
        pytest.param("edu", "windows-1252", id="edu-reads-as-western"),
        pytest.param("xn--unlisted", "ISO-8859-2", id="an-unlisted-punycode-label-hints-nothing"),
        pytest.param("longlabel", "ISO-8859-2", id="a-label-that-is-not-punycode-hints-nothing"),
        pytest.param("xn--p1a", "ISO-8859-2", id="a-label-too-short-to-be-punycode-hints-nothing"),
        pytest.param("th", "ISO-8859-2", id="a-script-absent-from-the-bytes-penalizes-nothing"),
    ],
)
def test_the_tld_narrows_the_candidates(tld: str | None, encoding: str) -> None:
    assert detect(_CZECH, Detection(tld=tld)).encoding == encoding


def test_the_tlds_own_encoding_answers_when_nothing_outscores_it() -> None:
    # windows-1251 finds no Cyrillic word here, so it never scores. It wins because a Cyrillic TLD zeroes the
    # Central candidates, and this text is too short for the Western one to stay ahead of a default.
    assert detect(_CZECH_SHORT, Detection(tld="ru")).encoding == "windows-1251"


def test_a_punycode_label_classifies_like_the_ascii_one() -> None:
    assert detect(_CZECH_SHORT, Detection(tld="xn--p1ai")) == detect(_CZECH_SHORT, Detection(tld="ru"))


def test_a_traditional_tld_picks_traditional_over_simplified() -> None:
    assert detect(_SIMPLIFIED, Detection(tld="tw")).encoding == "Big5"


def test_a_simplified_tld_picks_simplified_over_traditional() -> None:
    assert detect(_TRADITIONAL, Detection(tld="cn")).encoding == "GBK"


def test_a_traditional_tld_falls_back_on_simplified_when_no_big5_survives() -> None:
    # .tw expects Big5, which these bytes kill. chardetng then scores the page as though it came from a Simplified
    # domain, handing GBK the point the TLD's own encoding would have taken and penalizing the Latin candidate that
    # wins with no TLD at all.
    assert detect(_NO_TRADITIONAL).encoding == "windows-1252"
    assert detect(_NO_TRADITIONAL, Detection(tld="tw")).encoding == "GBK"
    assert detect(_NO_TRADITIONAL, Detection(tld="tw")) == detect(_NO_TRADITIONAL, Detection(tld="cn"))


def test_a_tld_whose_sibling_script_is_also_absent_leaves_the_bytes_alone() -> None:
    # .tw expects Big5 and would settle for GBK, and Czech text carries neither. With both gone chardetng has no
    # expectation left to flip to, so it drops the TLD rather than let a dead sibling hand GBK the point.
    assert detect(_CZECH, Detection(tld="tw")).encoding == detect(_CZECH).encoding == "ISO-8859-2"


def test_a_central_iso_tld_falls_back_on_central_windows() -> None:
    # .hu expects ISO-8859-2, which these bytes kill, so windows-1250 inherits the expectation
    assert detect(_NO_CENTRAL_ISO, Detection(tld="hu")).encoding == "windows-1250"


def test_a_caseless_candidate_survives_the_word_gate_on_its_native_tld() -> None:
    # ISO-8859-6 never sees the two-letter Arabic word the gate asks for, so it scores only where its script is
    # native. .sa expects windows-1256, so nothing injects ISO-8859-6 as that domain's default.
    assert "ISO-8859-6" in _ranked(_SHORT_ARABIC_ISO, "sa")
    assert "ISO-8859-6" not in _ranked(_SHORT_ARABIC_ISO)


def test_an_arabic_french_candidate_survives_the_word_gate_on_its_native_tld() -> None:
    # .my expects windows-1252 and counts windows-1256 as native, which is what carries it past the gate
    assert "windows-1256" in _ranked(_SHORT_ARABIC_WINDOWS, "my")
    assert "windows-1256" not in _ranked(_SHORT_ARABIC_WINDOWS)


def test_a_tld_whose_script_never_appears_stops_penalizing_the_rest() -> None:
    # .th expects windows-874, and no Thai appears in Czech text. With its expectation broken, chardetng reads the
    # label as mistaken rather than as evidence, so the bytes alone decide.
    assert detect(_CZECH, Detection(tld="th")).encoding == detect(_CZECH).encoding


def test_a_tld_whose_script_did_appear_keeps_penalizing() -> None:
    # Thai does score on Big5 bytes, so the expectation holds and Big5 pays the penalty .th levies on it
    assert detect(_TRADITIONAL, Detection(tld="th")).encoding == "windows-874"


def test_the_hint_cannot_overrule_a_structural_answer() -> None:
    # UTF-8 validity is a proof rather than a guess, and no label outvotes it
    assert detect("日本語のテキストです".encode(), Detection(tld="ru")).encoding == "UTF-8"


def test_the_hint_cannot_overrule_a_byte_order_mark() -> None:
    assert detect(b"\xff\xfe\x41\x00", Detection(tld="jp")).encoding == "UTF-16LE"


def test_the_hint_reorders_every_ranked_candidate() -> None:
    # detect_all reports the scores the winner came from, so the hint has to move the whole ranking. A runner-up
    # ranked by unadjusted scores would contradict the answer above it.
    assert detect_all(_CZECH, Detection(tld="ru"))[0].encoding == "windows-1252"


def test_the_streaming_detector_honors_the_hint() -> None:
    detector = EncodingDetector(Detection(tld="ru"))
    for start in range(0, len(_CZECH), 5):
        detector.feed(_CZECH[start : start + 5])
    assert detector.close() == detect(_CZECH, Detection(tld="ru"))


def test_reset_keeps_the_hint() -> None:
    detector = EncodingDetector(Detection(tld="ru"))
    detector.feed(_CZECH)
    detector.close()
    detector.reset()
    detector.feed(_CZECH)
    assert detector.close().encoding == "windows-1252"


@pytest.mark.parametrize(
    "tld",
    [
        pytest.param("", id="empty"),
        pytest.param("JP", id="upper-case"),
        pytest.param("example.jp", id="whole-hostname"),
        pytest.param(".jp", id="leading-dot"),
        pytest.param("рф", id="non-ascii"),
        pytest.param("jp ", id="trailing-space"),
    ],
)
def test_a_malformed_tld_is_rejected(tld: str) -> None:
    with pytest.raises(ValueError, match="rightmost DNS label"):
        Detection(tld=tld)
