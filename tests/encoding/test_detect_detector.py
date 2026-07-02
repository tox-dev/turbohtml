"""The incremental Detector: feed/close/reset over a byte stream, mirroring chardet's UniversalDetector."""

from __future__ import annotations

from turbohtml.detect import Detection, Detector, EncodingMatch, detect

_RUSSIAN_1251 = "Программирование помогает понять структуру вычислительных систем сегодня здесь.".encode("cp1251")


def test_chunked_feeds_equal_a_one_shot_detect() -> None:
    detector = Detector()
    detector.feed(_RUSSIAN_1251[:7])
    detector.feed(_RUSSIAN_1251[7:])
    assert not detector.done
    assert detector.result is None
    assert detector.close() == detect(_RUSSIAN_1251)
    assert detector.done


def test_a_leading_bom_finishes_the_stream_early() -> None:
    detector = Detector()
    detector.feed(b"\xef\xbb\xbf")
    assert detector.done
    detector.feed("Ω".encode("cp1253"))  # ignored: the mark already decided the stream
    assert detector.close() == EncodingMatch("UTF-8", 1.0, None)


def test_a_bom_split_across_feeds_still_finishes_early() -> None:
    detector = Detector()
    detector.feed(b"\xef\xbb")
    assert not detector.done
    detector.feed(b"\xbftail")
    assert detector.done


def test_close_caches_its_result() -> None:
    detector = Detector()
    detector.feed(b"plain ascii")
    assert detector.close() is detector.close()
    assert detector.result == EncodingMatch("ascii", 1.0, None)


def test_close_without_a_feed_reports_no_encoding() -> None:
    assert Detector().close() == EncodingMatch(None, 0.0, None)


def test_reset_starts_a_fresh_stream() -> None:
    detector = Detector()
    detector.feed(b"\xff\xfeh\x00")
    detector.close()
    detector.reset()
    assert detector.result is None
    assert not detector.done
    detector.feed(_RUSSIAN_1251)
    assert detector.close().encoding == "windows-1251"


def test_detector_honors_its_options() -> None:
    detector = Detector(Detection.chardet())
    detector.feed(b"\x81\n")
    assert detector.close() == EncodingMatch(None, 0.0, None)
