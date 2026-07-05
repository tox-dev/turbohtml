"""The incremental EncodingDetector: feed/close/reset over a byte stream, mirroring chardet's UniversalDetector."""

from __future__ import annotations

import pytest

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
    assert detector.close() == EncodingMatch("UTF-8-SIG", 1.0, None, bom=True)


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
    assert detector.close() == EncodingMatch("UTF-32LE", 1.0, None, bom=True)


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
    assert detector.close() == EncodingMatch(encoding, 1.0, None, bom=True)


def test_close_caches_its_result() -> None:
    detector = EncodingDetector()
    detector.feed(b"plain ascii")
    assert detector.close() is detector.close()
    assert detector.result == EncodingMatch("ascii", 1.0, None)


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
