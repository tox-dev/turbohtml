from __future__ import annotations

from typing import Final

import pytest

from turbohtml import _html as _engine
from turbohtml._html import DetectStream, codec_label, decode, detect, detect_language


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
    assert codec_label(name) == expected


def test_codec_label_needs_a_str() -> None:
    with pytest.raises(TypeError):
        codec_label(b"whatwg-utf-8")  # ty: ignore[invalid-argument-type]  # the argument check is the point


def test_an_escape_driven_codec_decodes_nothing_to_nothing() -> None:
    # ISO-2022-JP is the one decoder the all-ASCII fast path skips, so empty input reaches its multi-byte entry;
    # the codec machinery short-circuits empty bytes itself, so the C entry point is called directly
    assert not decode(b"", "iso-2022-jp")


def test_detect_of_nothing_is_none() -> None:
    assert detect(b"", None) is None


def test_an_unfed_stream_closes_to_none() -> None:
    stream = DetectStream(None)
    stream.feed(b"")
    assert stream.close() is None


def test_a_fed_stream_closes_to_a_result() -> None:
    stream = DetectStream(None)
    stream.feed("Привет".encode("cp1251"))
    result = stream.close()
    assert result is not None
    assert result[0] == "windows-1251"


def test_the_language_threshold_blanks_a_faint_match() -> None:
    text = "the quick brown fox jumps over the lazy dog"
    full = detect_language(text, None, frozenset(), 0.0)
    assert full[0] == "eng"
    assert detect_language(text, None, frozenset(), 1.1) == (None, 0.0, None, None)


def test_the_language_entry_needs_a_threshold() -> None:
    with pytest.raises(TypeError):
        detect_language("text", None, frozenset())  # ty: ignore[missing-argument]  # the arity check is the point


def test_feeding_a_closed_stream_is_an_error() -> None:
    stream = DetectStream(None)
    stream.feed(b"caf\xe9")
    stream.close()
    with pytest.raises(ValueError, match="closed"):
        stream.feed(b"more")


def test_closing_a_closed_stream_is_an_error() -> None:
    stream = DetectStream(None)
    stream.close()
    with pytest.raises(ValueError, match="closed"):
        stream.close()


def test_the_stream_takes_only_a_tld() -> None:
    with pytest.raises(TypeError):
        DetectStream(None, "extra")  # ty: ignore[too-many-positional-arguments]  # rejected at runtime


def test_the_stream_rejects_a_non_string_tld() -> None:
    with pytest.raises(TypeError):
        DetectStream(7)  # ty: ignore[invalid-argument-type]  # rejected at runtime


def test_detect_rejects_a_non_string_tld() -> None:
    with pytest.raises(TypeError):
        detect(b"abc", 7)  # ty: ignore[invalid-argument-type]  # rejected at runtime


def test_the_stream_feeds_only_bytes() -> None:
    with pytest.raises(TypeError):
        DetectStream(None).feed("not bytes")  # ty: ignore[invalid-argument-type]  # rejected at runtime


_ENTRY_NAMES: Final[tuple[str, str]] = ("_normalize", "_is_normalized")


@pytest.mark.parametrize("name", _ENTRY_NAMES)
@pytest.mark.parametrize("args", [pytest.param((0,), id="one"), pytest.param((0, "a", "b"), id="three")])
def test_engine_entry_takes_exactly_two_arguments(name: str, args: tuple[int | str, ...]) -> None:
    with pytest.raises(TypeError, match="function takes exactly 2 arguments"):
        getattr(_engine, name)(*args)


@pytest.mark.parametrize("name", _ENTRY_NAMES)
def test_engine_entry_rejects_keyword_arguments(name: str) -> None:
    with pytest.raises(TypeError, match=f"{name}\\(\\) takes no keyword arguments"):
        getattr(_engine, name)(0, text="x")


@pytest.mark.parametrize("name", _ENTRY_NAMES)
@pytest.mark.parametrize(
    ("form", "error", "message"),
    [
        # a 32-bit C long (Windows) overflows on the long conversion before the int range check
        pytest.param(
            2**31,
            OverflowError,
            "signed integer is greater than maximum|Python int too large to convert to C long",
            id="above-int",
        ),
        pytest.param(
            -(2**31) - 1,
            OverflowError,
            "signed integer is less than minimum|Python int too large to convert to C long",
            id="below-int",
        ),
        pytest.param(2**63, OverflowError, "Python int too large to convert to C long", id="above-long"),
        pytest.param(-(2**70), OverflowError, "Python int too large to convert to C long", id="below-long"),
        # PyPy words the conversion error as "expected integer, got <type> object"
        pytest.param(
            "NFC", TypeError, "'str' object cannot be interpreted as an integer|expected integer, got str", id="str"
        ),
        pytest.param(
            None,
            TypeError,
            "'NoneType' object cannot be interpreted as an integer|expected integer, got NoneType",
            id="none",
        ),
    ],
)
def test_engine_entry_rejects_a_form_code_that_is_not_an_int_in_range(
    name: str, form: int | str | None, error: type[Exception], message: str
) -> None:
    with pytest.raises(error, match=message):
        getattr(_engine, name)(form, "é")


@pytest.mark.parametrize("name", _ENTRY_NAMES)
@pytest.mark.parametrize("form", [pytest.param(2**31 - 1, id="int-max"), pytest.param(-(2**31), id="int-min")])
def test_engine_entry_accepts_the_int_extremes(name: str, form: int) -> None:
    assert isinstance(getattr(_engine, name)(form, "é"), (str, bool))
