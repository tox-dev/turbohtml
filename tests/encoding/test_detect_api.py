"""detect()/detect_all() (issue #315): the standalone surface over the same C sniff parse() runs on bytes."""

from __future__ import annotations

import pytest

from turbohtml import parse
from turbohtml.detect import EncodingMatch, detect, detect_all

_RUSSIAN_1251 = "Программирование помогает понять структуру вычислительных систем сегодня здесь.".encode("cp1251")


def test_empty_input_has_no_encoding() -> None:
    assert detect(b"") == EncodingMatch(None, 0.0, None)


def test_pure_ascii_is_certain() -> None:
    assert detect(b"<p>hello world</p>") == EncodingMatch("ascii", 1.0, None)


@pytest.mark.parametrize(
    ("raw", "encoding"),
    [
        pytest.param(b"\xef\xbb\xbfhello", "UTF-8", id="utf-8-bom"),
        pytest.param(b"\xff\xfeh\x00", "UTF-16LE", id="utf-16le-bom"),
        pytest.param(b"\xfe\xff\x00h", "UTF-16BE", id="utf-16be-bom"),
        pytest.param(b"<meta charset=iso-8859-2><p>x</p>", "ISO-8859-2", id="meta-prescan"),
    ],
)
def test_declarations_are_certain(raw: bytes, encoding: str) -> None:
    assert detect(raw) == EncodingMatch(encoding, 1.0, None)


def test_valid_utf8_is_certain() -> None:
    assert detect("café résumé Москва 日本語".encode()) == EncodingMatch("UTF-8", 1.0, None)


def test_iso_2022_jp_is_certain() -> None:
    raw = "こんにちは世界".encode("iso-2022-jp")
    assert detect(raw) == EncodingMatch("ISO-2022-JP", 1.0, "Japanese")


@pytest.mark.parametrize(
    ("text", "source", "encoding", "language"),
    [
        pytest.param(
            "Précédemment, la créativité française était très développée près de Paris ici.",
            "cp1252",
            "windows-1252",
            None,
            id="french-1252",
        ),
        pytest.param(
            "Программирование помогает понять структуру вычислительных систем сегодня здесь.",
            "cp1251",
            "windows-1251",
            "Russian",
            id="russian-1251",
        ),
        pytest.param(
            "Москва это столица России и очень большой красивый город здесь сейчас опять.",
            "koi8-r",
            "KOI8-U",
            "Russian",
            id="russian-koi8",
        ),
        pytest.param(
            "Η ελληνική γλώσσα είναι μία από τις αρχαιότερες γλώσσες στον κόσμο σήμερα εδώ.",  # noqa: RUF001
            "cp1253",
            "windows-1253",
            "Greek",
            id="greek-1253",
        ),
        pytest.param(
            "اللغة العربية لغة جميلة وغنية بالكلمات والتعابير المختلفة في العالم العربي كله.",
            "cp1256",
            "windows-1256",
            "Arabic",
            id="arabic-1256",
        ),
        pytest.param(
            "日本語のテキストをここに書きます。今日はとても良い天気ですね。",
            "shift_jis",
            "Shift_JIS",
            "Japanese",
            id="japanese-shift_jis",
        ),
        pytest.param(
            "한국어 텍스트를 여기에 작성합니다 오늘은 날씨가 정말 좋습니다 그렇죠.",
            "euc_kr",
            "EUC-KR",
            "Korean",
            id="korean-euc-kr",
        ),
        pytest.param(
            "这是一段简体中文文本用来测试编码检测今天天气非常好我们去公园散步吧。",
            "gbk",
            "GBK",
            "Chinese",
            id="simplified-gbk",
        ),
    ],
)
def test_scored_detection_recovers_encoding_and_language(text: str, source: str, encoding: str, language: str) -> None:
    match = detect(text.encode(source))
    assert match.encoding == encoding
    assert match.language == language
    assert 0.0 < match.confidence <= 1.0


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("café résumé".encode(), id="utf-8"),
        pytest.param("Программирование помогает всем".encode("cp1251"), id="windows-1251"),
        pytest.param(b"<meta charset=iso-8859-2><p>\xe1</p>", id="meta-prescan"),
        pytest.param(b"\x81\n", id="fallback"),
    ],
)
def test_agrees_with_parse_detect_encoding(raw: bytes) -> None:
    assert detect(raw).encoding == parse(raw, detect_encoding=True).encoding


def test_detect_all_leads_with_the_detect_winner() -> None:
    matches = detect_all(_RUSSIAN_1251)
    assert matches[0] == detect(_RUSSIAN_1251)


def test_detect_all_ranks_alternatives_by_confidence() -> None:
    alternatives = detect_all(_RUSSIAN_1251)[1:]
    assert alternatives
    assert [match.confidence for match in alternatives] == sorted(
        (match.confidence for match in alternatives), reverse=True
    )


def test_detect_all_confidences_share_a_unit_budget() -> None:
    assert sum(match.confidence for match in detect_all(_RUSSIAN_1251)) == pytest.approx(1.0)


def test_detect_all_reports_each_encoding_once() -> None:
    # two candidates share the windows-1252 model (one per language family); only the better one is reported
    encodings = [match.encoding for match in detect_all(_RUSSIAN_1251)]
    assert encodings.count("windows-1252") == 1


def test_hebrew_visual_tiebreak_leads_the_ranking() -> None:
    # right-to-left (visual-order) Hebrew ties windows-1255 on score; the engine's punctuation tiebreak
    # picks ISO-8859-8 and the ranking keeps that winner first even though the tie sorts windows-1255 earlier
    visual = "שלום לכולם זהו משפט בעברית עם סימני פיסוק רבים, נקודות. ועוד!"[::-1].encode("iso-8859-8")
    matches = detect_all(visual)
    assert matches[0].encoding == "ISO-8859-8"
    assert matches[1].encoding == "windows-1255"
    assert matches[0].confidence == matches[1].confidence


def test_undecodable_bytes_fall_back_to_windows_1252_with_no_confidence() -> None:
    # 0x81 followed by a newline disqualifies every candidate, so the WHATWG windows-1252 default
    # is returned with confidence 0.0: there is no positive evidence for it
    assert detect(b"\x81\n") == EncodingMatch("windows-1252", 0.0, None)


def test_non_bytes_input_is_rejected() -> None:
    with pytest.raises(TypeError):
        detect("text")  # ty: ignore[invalid-argument-type]  # str exposes no byte buffer
