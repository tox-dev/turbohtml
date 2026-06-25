"""The incremental :class:`turbohtml.detect.Detector`, mirroring chardet's ``UniversalDetector``."""

from __future__ import annotations

from turbohtml.detect import Detection, Detector

_CYRILLIC = "Привет мир как дела сегодня хорошо".encode("windows-1251")


def test_feed_then_close_resolves_the_buffered_stream() -> None:
    detector = Detector()
    detector.feed(_CYRILLIC[:10])
    detector.feed(_CYRILLIC[10:])
    match = detector.close()
    assert match.encoding == "windows-1251"


def test_result_is_none_until_closed() -> None:
    detector = Detector()
    detector.feed(_CYRILLIC)
    assert detector.result is None
    closed = detector.close()
    assert detector.result is closed


def test_close_is_idempotent_and_feed_after_close_is_ignored() -> None:
    detector = Detector()
    detector.feed(_CYRILLIC)
    first = detector.close()
    detector.feed("日本語".encode())  # ignored: the stream is already resolved
    assert detector.close() is first


def test_reset_clears_the_buffer_for_a_fresh_stream() -> None:
    detector = Detector()
    detector.feed(_CYRILLIC)
    detector.close()
    detector.reset()
    assert detector.result is None
    detector.feed("こんにちは世界".encode("shift_jis"))
    assert detector.close().encoding == "Shift_JIS"


def test_close_with_no_data_detects_nothing() -> None:
    assert Detector().close().encoding is None


def test_detector_applies_its_config() -> None:
    detector = Detector(Detection(minimum_confidence=0.99))
    detector.feed(_CYRILLIC)
    assert detector.close().encoding is None
