"""The standalone ``turbohtml.detect`` surface (issue #315): a ``bytes -> encoding`` guess.

These exercise the public result shape and every path through the WHATWG-then-chardetng pipeline -- an explicit BOM or
``<meta>`` declaration, the structural UTF-8 / ISO-2022-JP resolutions, pure ASCII, the heuristic single-byte and CJK
competitions, and the empty stream -- independent of the HTML parser.
"""

from __future__ import annotations

import pytest

from turbohtml.detect import EncodingMatch, detect, detect_all


def test_utf8_is_resolved_structurally() -> None:
    match = detect("café résumé Москва 日本語".encode())
    assert match.encoding == "UTF-8"
    assert match.confidence == pytest.approx(0.99)
    assert match.language is None


def test_pure_ascii_reports_ascii_with_certainty() -> None:
    match = detect(b"<p>hello world</p>")
    assert match == EncodingMatch("ascii", 1.0, None)


def test_bom_wins_with_full_confidence() -> None:
    match = detect(b"\xef\xbb\xbf<p>x</p>")
    assert match == EncodingMatch("UTF-8", 1.0, None)


def test_meta_declaration_wins_with_full_confidence() -> None:
    match = detect(b"<meta charset=iso-8859-2><p>caf\xc3\xa9</p>")
    assert match == EncodingMatch("ISO-8859-2", 1.0, None)


def test_iso_2022_jp_is_resolved_structurally() -> None:
    match = detect("こんにちは世界".encode("iso-2022-jp"))
    assert match.encoding == "ISO-2022-JP"
    assert match.confidence == pytest.approx(0.99)
    assert match.language == "Japanese"


def test_empty_stream_detects_nothing() -> None:
    assert detect(b"") == EncodingMatch(None, 0.0, None)
    assert detect_all(b"") == []


@pytest.mark.parametrize(
    ("text", "codec", "encoding", "language"),
    [
        pytest.param("Привет мир как дела сегодня хорошо", "windows-1251", "windows-1251", "Russian", id="cyrillic"),
        pytest.param("Καλημέρα κόσμε τι κάνεις σήμερα φίλε", "iso-8859-7", "windows-1253", "Greek", id="greek"),
        pytest.param("שלום עולם מה שלומך היום בבית", "windows-1255", "windows-1255", "Hebrew", id="hebrew"),
        pytest.param("مرحبا بالعالم كيف حالك اليوم", "windows-1256", "windows-1256", "Arabic", id="arabic"),
        pytest.param("こんにちは世界、今日はいい天気ですね", "shift_jis", "Shift_JIS", "Japanese", id="shift-jis"),
        pytest.param("你好世界今天天气很好啊朋友们", "gbk", "GBK", "Chinese", id="gbk"),
        pytest.param("안녕하세요 세계 오늘 날씨가 좋네요", "euc-kr", "EUC-KR", "Korean", id="euc-kr"),
        pytest.param("你好世界今天天氣很好朋友們", "big5", "Big5", "Chinese", id="big5"),
    ],
)
def test_heuristic_picks_the_legacy_encoding(text: str, codec: str, encoding: str, language: str | None) -> None:
    match = detect(text.encode(codec))
    assert match.encoding == encoding
    assert match.language == language
    assert 0.5 < match.confidence < 0.99


def test_heuristic_confidence_grows_with_evidence() -> None:
    short = detect("Привет мир".encode("windows-1251")).confidence
    long = detect(("Привет мир как дела " * 20).encode("windows-1251")).confidence
    assert long > short


def test_detect_all_ranks_alternatives_best_first() -> None:
    matches = detect_all("Привет мир как дела сегодня".encode("windows-1251"))
    assert matches[0].encoding == "windows-1251"
    assert "windows-1252" in {match.encoding for match in matches}
    assert matches == sorted(matches, key=lambda match: match.confidence, reverse=True)


def test_detect_all_for_a_declaration_is_a_single_match() -> None:
    assert detect_all(b"\xef\xbb\xbf<p>x</p>") == [EncodingMatch("UTF-8", 1.0, None)]


def test_windows_1252_fallback_sits_at_the_confidence_midpoint() -> None:
    # the windows-1252 default carries no positive evidence, so it reports exactly 0.5
    ranked = detect_all("Привет мир".encode("windows-1251"))
    fallback = next(match for match in ranked if match.encoding == "windows-1252")
    assert fallback.confidence == pytest.approx(0.5)


def test_accepts_bytearray_and_memoryview() -> None:
    raw = "Привет мир как дела".encode("windows-1251")
    assert detect(bytearray(raw)).encoding == "windows-1251"
    assert detect(memoryview(raw)).encoding == "windows-1251"


def test_non_bytes_input_is_rejected() -> None:
    with pytest.raises(TypeError):
        detect("a string is not bytes")  # ty: ignore[invalid-argument-type]
