from __future__ import annotations

import pytest

from turbohtml._html import DetectStream as _DetectStream
from turbohtml._html import codec_label as _codec_label
from turbohtml._html import decode as _decode
from turbohtml._html import detect as _detect
from turbohtml._html import detect_language as _detect_language


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        pytest.param("whatwg_utf_16le", (True, "utf-16-le"), id="a-byte-order-mark-name-delegates"),
        pytest.param("WHATWG-UTF-8-SIG", (True, "utf-8-sig"), id="case-and-dashes-fold"),
        pytest.param("whatwg_windows_1252", (False, "windows-1252"), id="an-underscored-label-tries-its-dash"),
        pytest.param("whatwg-shift_jis", (False, "shift_jis"), id="an-underscore-label-stays"),
        pytest.param("whatwg-koi8-ru", (False, "koi8-ru"), id="a-spec-only-label"),
        pytest.param("whatwg-nonsense", None, id="an-unknown-label"),
        pytest.param("utf-8", None, id="no-prefix"),
        pytest.param("whatwg_", None, id="the-bare-prefix"),
        pytest.param(f"whatwg-{'x' * 80}", None, id="longer-than-any-label"),
    ],
)
def test_codec_label(name: str, expected: tuple[bool, str] | None) -> None:
    assert _codec_label(name) == expected


def test_codec_label_needs_a_str() -> None:
    with pytest.raises(TypeError):
        _codec_label(b"whatwg-utf-8")  # ty: ignore[invalid-argument-type]  # the argument check is the point


def test_an_escape_driven_codec_decodes_nothing_to_nothing() -> None:
    # ISO-2022-JP is the one decoder the all-ASCII fast path skips, so empty input reaches its multi-byte entry;
    # the codec machinery short-circuits empty bytes itself, so the C entry point is called directly
    assert not _decode(b"", "iso-2022-jp")


def test_detect_of_nothing_is_none() -> None:
    assert _detect(b"", None) is None


def test_an_unfed_stream_closes_to_none() -> None:
    stream = _DetectStream(None)
    stream.feed(b"")
    assert stream.close() is None


def test_a_fed_stream_closes_to_a_result() -> None:
    stream = _DetectStream(None)
    stream.feed("Привет".encode("cp1251"))
    result = stream.close()
    assert result is not None
    assert result[0] == "windows-1251"


def test_the_language_threshold_blanks_a_faint_match() -> None:
    text = "the quick brown fox jumps over the lazy dog"
    full = _detect_language(text, None, frozenset(), 0.0)
    assert full[0] == "eng"
    assert _detect_language(text, None, frozenset(), 1.1) == (None, 0.0, None, None)


def test_the_language_entry_needs_a_threshold() -> None:
    with pytest.raises(TypeError):
        _detect_language("text", None, frozenset())  # ty: ignore[missing-argument]  # the arity check is the point


def test_feeding_a_closed_stream_is_an_error() -> None:
    stream = _DetectStream(None)
    stream.feed(b"caf\xe9")
    stream.close()
    with pytest.raises(ValueError, match="closed"):
        stream.feed(b"more")


def test_closing_a_closed_stream_is_an_error() -> None:
    stream = _DetectStream(None)
    stream.close()
    with pytest.raises(ValueError, match="closed"):
        stream.close()


def test_the_stream_takes_only_a_tld() -> None:
    with pytest.raises(TypeError):
        _DetectStream(None, "extra")  # ty: ignore[too-many-positional-arguments]  # rejected at runtime


def test_the_stream_rejects_a_non_string_tld() -> None:
    with pytest.raises(TypeError):
        _DetectStream(7)  # ty: ignore[invalid-argument-type]  # rejected at runtime


def test_detect_rejects_a_non_string_tld() -> None:
    with pytest.raises(TypeError):
        _detect(b"abc", 7)  # ty: ignore[invalid-argument-type]  # rejected at runtime


def test_the_stream_feeds_only_bytes() -> None:
    with pytest.raises(TypeError):
        _DetectStream(None).feed("not bytes")  # ty: ignore[invalid-argument-type]  # rejected at runtime
