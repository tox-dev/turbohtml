"""The incremental EncodingDetector: feed/close/reset over a byte stream, mirroring chardet's UniversalDetector."""

from __future__ import annotations

import pytest

from turbohtml import _html
from turbohtml.detect import Detection, EncodingDetector, EncodingMatch, detect

_RUSSIAN_1251 = "Программирование помогает понять структуру вычислительных систем сегодня здесь.".encode("cp1251")


def test_chunked_feeds_equal_a_one_shot_detect() -> None:
    detector = EncodingDetector()
    detector.feed(_RUSSIAN_1251[:7])
    detector.feed(_RUSSIAN_1251[7:])
    assert not detector.done
    assert detector.result is None
    assert detector.close() == detect(_RUSSIAN_1251)
    assert detector.done


def test_a_leading_bom_finishes_the_stream_early() -> None:
    detector = EncodingDetector()
    detector.feed(b"\xef\xbb\xbf")
    assert detector.done
    detector.feed("Ω".encode("cp1253"))  # ignored: the mark already decided the stream
    assert detector.close() == EncodingMatch("UTF-8-SIG", 1.0, None, bom=True, codec="whatwg-utf-8-sig")


def test_a_bom_split_across_feeds_still_finishes_early() -> None:
    detector = EncodingDetector()
    detector.feed(b"\xef\xbb")
    assert not detector.done
    detector.feed(b"\xbftail")
    assert detector.done


def test_utf_32le_mark_waits_for_the_pair_that_rules_out_utf_16le() -> None:
    # FF FE alone could be UTF-16LE or the start of the UTF-32LE mark FF FE 00 00, so the stream is
    # not done until the next pair resolves it; here it does, to UTF-32LE
    detector = EncodingDetector()
    detector.feed(b"\xff\xfe")
    assert not detector.done
    detector.feed(b"\x00\x00")
    assert detector.done
    assert detector.close() == EncodingMatch("UTF-32LE", 1.0, None, bom=True, codec="whatwg-utf-32le")


@pytest.mark.parametrize(
    ("chunk", "encoding"),
    [
        pytest.param(b"\xff\xfeh\x00", "UTF-16LE", id="utf-16le-non-zero-pair"),
        pytest.param(b"\x00\x00\xfe\xff", "UTF-32BE", id="utf-32be"),
    ],
)
def test_a_resolved_mark_finishes_early(chunk: bytes, encoding: str) -> None:
    # a mark that a single chunk resolves (FF FE + non-00 00 is UTF-16LE, 00 00 FE FF is UTF-32BE)
    # finishes the stream at once
    detector = EncodingDetector()
    detector.feed(chunk)
    assert detector.done
    assert detector.close() == EncodingMatch(encoding, 1.0, None, bom=True, codec=f"whatwg-{encoding.casefold()}")


def test_close_caches_its_result() -> None:
    detector = EncodingDetector()
    detector.feed(b"plain ascii")
    assert detector.close() is detector.close()
    assert detector.result == EncodingMatch("windows-1252", 1.0, None, codec="whatwg-windows-1252")


def test_close_without_a_feed_reports_no_encoding() -> None:
    assert EncodingDetector().close() == EncodingMatch(None, 0.0, None)


def test_reset_starts_a_fresh_stream() -> None:
    detector = EncodingDetector()
    detector.feed(b"\xff\xfeh\x00")
    detector.close()
    detector.reset()
    assert detector.result is None
    assert not detector.done
    detector.feed(_RUSSIAN_1251)
    assert detector.close().encoding == "windows-1251"


def test_detector_honors_its_options() -> None:
    detector = EncodingDetector(Detection.chardet())
    detector.feed(b"\x81\n")
    assert detector.close() == EncodingMatch(None, 0.0, None)


# The samples exercise every structural answer the stream can reach: an escape-driven
# ISO-2022-JP run, a multi-byte UTF-8 run, a CJK run whose sequences straddle the feeds, and a
# pure-ASCII run that carries no evidence at all.
_SAMPLES = [
    pytest.param("日本語のテキスト".encode("iso-2022-jp"), id="iso-2022-jp"),
    pytest.param("café naïve 日本語".encode(), id="utf-8"),
    pytest.param(b"plain ascii only", id="ascii"),
    pytest.param("中文简体测试".encode("gbk"), id="gbk"),
    pytest.param("日本語のテキスト".encode("shift_jis"), id="shift_jis"),
    pytest.param("한국어 텍스트".encode("euc-kr"), id="euc-kr"),
    pytest.param("中文字元測試".encode("big5"), id="big5"),
    pytest.param("Příliš žluťoučký kůň".encode("windows-1250"), id="windows-1250"),
    pytest.param("Съешь же ещё этих".encode("windows-1251"), id="windows-1251"),
]


@pytest.mark.parametrize("raw", _SAMPLES)
@pytest.mark.parametrize("size", [pytest.param(size, id=f"chunk-{size}") for size in (1, 2, 3, 5, 8)])
def test_chunk_boundaries_never_change_the_answer(raw: bytes, size: int) -> None:
    # a multi-byte sequence, an escape, and the two bytes the scoring looks back at all straddle
    # these boundaries; the detector carries each across the feed
    detector = EncodingDetector()
    for start in range(0, len(raw), size):
        detector.feed(raw[start : start + size])
    assert detector.close() == detect(raw)


def test_a_long_stream_still_answers_what_one_shot_does() -> None:
    # the candidates carry state, not bytes, so 4096 feeds cost what one does and answer the same
    chunk = "Съешь же ещё этих мягких".encode("windows-1251")
    detector = EncodingDetector()
    for _ in range(4096):
        detector.feed(chunk)
    assert detector.close() == detect(chunk * 4096)


def test_feeding_a_closed_stream_is_an_error() -> None:
    stream = _html._DetectStream()
    stream.feed(b"caf\xe9")
    stream.close()
    with pytest.raises(ValueError, match="closed"):
        stream.feed(b"more")


def test_closing_a_closed_stream_is_an_error() -> None:
    stream = _html._DetectStream()
    stream.close()
    with pytest.raises(ValueError, match="closed"):
        stream.close()


def test_the_stream_takes_no_arguments() -> None:
    with pytest.raises(TypeError):
        _html._DetectStream("utf-8")  # ty: ignore[too-many-positional-arguments]  # rejected at runtime


def test_the_stream_feeds_only_bytes() -> None:
    with pytest.raises(TypeError):
        _html._DetectStream().feed("not bytes")  # ty: ignore[invalid-argument-type]  # rejected at runtime


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(b"\x1b", id="escape-alone"),
        pytest.param(b"\x1b$", id="escape-truncated"),
        pytest.param(b"text\x1b(", id="escape-truncated-after-text"),
    ],
)
def test_a_stream_ending_mid_escape_is_not_iso_2022_jp(raw: bytes) -> None:
    # the escape never completes, so the structural ISO-2022-JP answer is off the table
    detector = EncodingDetector()
    for byte in raw:
        detector.feed(bytes([byte]))
    assert detector.close() == detect(raw)
    assert detect(raw).encoding != "ISO-2022-JP"


@pytest.mark.parametrize("shift", [pytest.param(0x0E, id="shift-out"), pytest.param(0x0F, id="shift-in")])
def test_a_shift_code_before_the_escape_rules_out_iso_2022_jp(shift: int) -> None:
    # the decoder's ASCII state rejects both shift codes, so the escape that follows cannot
    # rescue the stream, and the scan stays dead through every later feed
    raw = bytes([ord("a"), shift, ord("b")]) + "日本語".encode("iso-2022-jp")
    assert detect(raw).encoding != "ISO-2022-JP"
    detector = EncodingDetector()
    for byte in raw:
        detector.feed(bytes([byte]))
    assert detector.close() == detect(raw)
