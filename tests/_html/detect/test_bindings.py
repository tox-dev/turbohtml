from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, TypeAlias

import pytest

from turbohtml import _html as _engine
from turbohtml._html import DetectStream, codec_label, decode, detect, detect_language

if TYPE_CHECKING:
    from turbohtml.detect import Detection


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


def test_the_language_threshold_blanks_a_faint_match() -> None:
    text = "the quick brown fox jumps over the lazy dog"
    full = detect_language(text, None, frozenset(), 0.0)
    assert full[0] == "eng"
    assert detect_language(text, None, frozenset(), 1.1) == (None, 0.0, None, None)


def test_the_language_entry_needs_a_threshold() -> None:
    with pytest.raises(TypeError):
        detect_language("text", None, frozenset())  # ty: ignore[missing-argument]  # the arity check is the point


def test_detect_rejects_a_non_string_tld() -> None:
    with pytest.raises(TypeError):
        detect(b"abc", 7)  # ty: ignore[invalid-argument-type]  # rejected at runtime


_RAW: TypeAlias = tuple[str | None, bool, list[tuple[str, int]], bool] | None


@dataclass(frozen=True, slots=True, kw_only=True)
class _Options:
    tld: str | int | None


def test_an_unfed_stream_ranks_the_no_match_row() -> None:
    calls: Final[_Calls] = _Calls()
    stream: Final[DetectStream] = DetectStream()
    stream.feed(b"")
    assert stream._close({}, calls.settle, calls.match) == "matched None"  # the C entry point
    assert calls.matched == [(None, None)]


class _Calls:
    def __init__(self) -> None:
        self.settled: list[tuple[str | None, bool, bool]] = []
        self.matched: list[tuple[_RAW, Detection | _Options | None]] = []

    def settle(self, winner: str | None, certain: bool, bom: bool) -> str:  # ruff: ignore[boolean-type-hint-positional-argument]  # the C detector calls it
        self.settled.append((winner, certain, bom))
        return f"settled {winner}"

    def match(self, raw: _RAW, options: Detection | _Options | None) -> str:
        self.matched.append((raw, options))
        return f"matched {raw[0] if raw else None}"


def test_a_settled_default_stream_answers_from_the_cache() -> None:
    calls: Final[_Calls] = _Calls()
    cache: Final[dict[int, str]] = {}
    for _ in range(2):
        stream = DetectStream()
        stream.feed(b"plain ascii")
        assert stream._close(cache, calls.settle, calls.match) == "settled None"  # the C entry point
    assert (calls.settled, calls.matched) == ([(None, False, False)], [])


def test_a_scored_default_stream_ranks_its_scores() -> None:
    calls: Final[_Calls] = _Calls()
    stream: Final[DetectStream] = DetectStream()
    stream.feed("Привет, как дела?".encode("cp1251"))
    assert stream._close({}, calls.settle, calls.match) == "matched windows-1251"  # the C entry point
    assert calls.settled == []


def test_a_stream_with_options_ranks_even_a_settled_answer() -> None:
    calls: Final[_Calls] = _Calls()
    options: Final[_Options] = _Options(tld=None)
    stream: Final[DetectStream] = DetectStream(options)  # ty: ignore[invalid-argument-type]  # only tld is read
    stream.feed(b"plain ascii")
    assert stream._close({}, calls.settle, calls.match) == "matched None"  # the C entry point
    assert calls.matched == [((None, False, [], False), options)]


def test_the_stream_reads_the_tld_of_its_options() -> None:
    calls: Final[_Calls] = _Calls()
    stream: Final[DetectStream] = DetectStream(_Options(tld="jp"))  # ty: ignore[invalid-argument-type]  # only tld is read
    stream.feed(b"\xc1\xc2")
    assert stream._close({}, calls.settle, calls.match) == "matched EUC-JP"  # the C entry point


def test_a_closed_stream_keeps_its_answer_and_ignores_more_bytes() -> None:
    calls: Final[_Calls] = _Calls()
    stream: Final[DetectStream] = DetectStream()
    stream.feed(b"caf\xe9")
    assert not stream.done
    first: Final[str] = stream._close({}, calls.settle, calls.match)  # the C entry point
    stream.feed(b"more")
    assert stream.done
    assert stream.result is first
    assert stream._close({}, calls.settle, calls.match) is first  # the C entry point
    assert len(calls.matched) == 1


def test_a_byte_order_mark_settles_the_stream() -> None:
    calls: Final[_Calls] = _Calls()
    stream: Final[DetectStream] = DetectStream()
    stream.feed(b"\xef\xbb")
    assert not stream.done
    stream.feed(b"\xbf")
    assert stream.done
    stream.feed("Привет".encode("cp1251"))
    assert stream._close({}, calls.settle, calls.match) == "settled UTF-8-SIG"  # the C entry point
    assert calls.settled == [("UTF-8-SIG", True, True)]


def test_reset_starts_the_stream_over() -> None:
    calls: Final[_Calls] = _Calls()
    stream: Final[DetectStream] = DetectStream()
    stream.feed(b"\xef\xbb\xbf")
    stream._close({}, calls.settle, calls.match)  # the C entry point
    stream.reset()
    assert not stream.done
    assert stream.result is None
    stream.feed("Привет, как дела?".encode("cp1251"))
    assert stream._close({}, calls.settle, calls.match) == "matched windows-1251"  # the C entry point


def test_a_failing_settle_leaves_no_answer() -> None:
    def settle(winner: str | None, certain: bool, bom: bool) -> str:  # ruff: ignore[boolean-type-hint-positional-argument]  # the C detector calls it
        raise LookupError(winner, certain, bom)

    stream: Final[DetectStream] = DetectStream()
    stream.feed(b"plain ascii")
    with pytest.raises(LookupError):
        stream._close({}, settle, _Calls().match)  # the C entry point
    assert stream.result is None


@pytest.mark.parametrize(
    ("args", "message"),
    [
        pytest.param((), "takes 3 arguments", id="too-few"),
        pytest.param(([], None, None), "cache must be a dict", id="a-list-cache"),
    ],
)
def test_close_checks_its_arguments(args: tuple[list[str] | None, ...], message: str) -> None:
    with pytest.raises(TypeError, match=message):
        DetectStream()._close(*args)  # ty: ignore[invalid-argument-type]  # rejected at runtime


def test_the_stream_takes_the_chunk_by_keyword() -> None:
    stream: Final[DetectStream] = DetectStream()
    stream.feed(data=b"\xef\xbb\xbf")
    assert stream.done


@pytest.mark.parametrize(
    ("args", "kwargs"),
    [
        pytest.param((), {}, id="nothing"),
        pytest.param((b"a", b"b"), {}, id="two"),
        pytest.param((), {"chunk": b"a"}, id="another-keyword"),
        pytest.param((b"a",), {"data": b"b"}, id="positional-and-keyword"),
    ],
)
def test_the_stream_feeds_one_chunk(args: tuple[bytes, ...], kwargs: dict[str, bytes]) -> None:
    with pytest.raises(TypeError, match="exactly one argument"):
        DetectStream().feed(*args, **kwargs)


def test_the_stream_feeds_only_bytes() -> None:
    with pytest.raises(TypeError):
        DetectStream().feed("not bytes")  # ty: ignore[invalid-argument-type]  # rejected at runtime


@pytest.mark.parametrize(
    ("args", "error"),
    [
        pytest.param((None, "extra"), TypeError, id="two-arguments"),
        pytest.param(("not options",), AttributeError, id="options-without-a-tld"),
        pytest.param((_Options(tld=7),), TypeError, id="a-non-string-tld"),
    ],
)
def test_the_stream_checks_its_options(args: tuple[_Options | str | None, ...], error: type[Exception]) -> None:
    with pytest.raises(error):
        DetectStream(*args)  # ty: ignore[invalid-argument-type]  # rejected at runtime


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param(
            b"caf\xe9\xe9\x81 x",
            (
                "windows-1252",
                False,
                [("EUC-KR", -853), ("windows-1251", -110), ("KOI8-U", -304), ("IBM866", -106)],
                False,
            ),
            id="unmapped-inside-a-non-ascii-run",
        ),
        pytest.param(
            b"\xaa\xba \xe9\xe9\xe9\xe9\xe9\xe9 1\xba 2\xaa",
            (
                "windows-1255",
                False,
                [
                    ("Shift_JIS", -11),
                    ("windows-1252", 266),
                    ("windows-1251", 86),
                    ("windows-1250", 7),
                    ("ISO-8859-2", 7),
                    ("windows-1252", -642),
                    ("windows-1254", -645),
                    ("windows-874", 18),
                    ("windows-1255", 269),
                    ("ISO-8859-7", -161),
                    ("windows-1257", -425),
                    ("ISO-8859-13", -425),
                    ("KOI8-U", -145),
                    ("IBM866", -143),
                    ("windows-1258", -645),
                    ("ISO-8859-4", -361),
                    ("ISO-8859-5", 43),
                    ("ISO-8859-8", 269),
                ],
                False,
            ),
            id="ordinals-inside-a-non-ascii-run",
        ),
        pytest.param(
            b"\xc9\xc9\xe9\xc9 \xe9\xc9\xc9\xc9\xe9",
            (
                "windows-1255",
                False,
                [
                    ("windows-1252", -354),
                    ("windows-1251", -122),
                    ("windows-1250", -494),
                    ("ISO-8859-2", -494),
                    ("windows-1256", -113),
                    ("windows-1252", -964),
                    ("windows-1254", -970),
                    ("windows-874", 3),
                    ("windows-1255", 99),
                    ("windows-1253", -86),
                    ("ISO-8859-7", -86),
                    ("windows-1257", -970),
                    ("ISO-8859-13", -970),
                    ("KOI8-U", 32),
                    ("ISO-8859-6", 98),
                    ("windows-1258", -970),
                    ("ISO-8859-4", -970),
                    ("ISO-8859-5", -174),
                ],
                False,
            ),
            id="case-changes-inside-a-non-ascii-run",
        ),
    ],
)
def test_latin_candidates_score_non_ascii_runs(raw: bytes, expected: _RAW) -> None:
    # the Latin candidates walk a run of non-ASCII bytes on a path of their own; the scores are chardetng's
    assert detect(raw, None) == expected


def test_detect_settles_through_the_cache() -> None:
    calls: Final[_Calls] = _Calls()
    cache: Final[dict[int, str]] = {}
    assert detect(b"plain ascii", None, cache, calls.settle) == "settled None"
    assert detect(b"plain ascii", None, cache, calls.settle) == "settled None"
    assert calls.settled == [(None, False, False)]


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("Привет, как дела? Это тест русского текста. ".encode("windows-1251") * 20, id="cyrillic"),
        pytest.param("שלום, מה שלומך? זהו טקסט בעברית לבדיקה. ".encode("windows-1255") * 20, id="hebrew"),
        pytest.param("日本語の文章をここに書きます。カタカナも少し。".encode("shift_jis") * 60, id="shift-jis"),
    ],
)
def test_detect_through_the_cache_stops_candidates_that_cannot_win(raw: bytes) -> None:
    # past the first block, a candidate that can no longer end positive stops scoring: the winner and the positive
    # scores, all a cached detection reads, stay as they are, while a TLD hint keeps every candidate
    winner, scores = _scored(detect(raw, None))
    cached_winner, cached_scores = _scored(detect(raw, None, {}, _Calls().settle))
    assert (cached_winner, [row for row in cached_scores if row[1] > 0]) == (
        winner,
        [row for row in scores if row[1] > 0],
    )
    assert len(cached_scores) < len(scores)
    assert detect(raw, "ru", {}, _Calls().settle) == detect(raw, "ru")


def _scored(result: _RAW | str) -> tuple[str | None, list[tuple[str, int]]]:
    assert isinstance(result, tuple)
    return result[0], result[2]


@pytest.mark.parametrize(
    ("args", "message"),
    [
        pytest.param((b"a",), "takes 2 or 4 arguments", id="one"),
        pytest.param((b"a", None, [], None), "cache must be a dict", id="a-list-cache"),
    ],
)
def test_detect_checks_its_arguments(args: tuple[bytes | list[str] | None, ...], message: str) -> None:
    with pytest.raises(TypeError, match=message):
        detect(*args)  # ty: ignore[no-matching-overload]  # rejected at runtime


_ENTRY_NAMES: Final[tuple[str, str]] = ("normalize", "is_normalized")


@pytest.mark.parametrize("name", _ENTRY_NAMES)
@pytest.mark.parametrize("args", [pytest.param(("NFC",), id="one"), pytest.param(("NFC", "a", "b"), id="three")])
def test_engine_entry_takes_exactly_two_arguments(name: str, args: tuple[str, ...]) -> None:
    with pytest.raises(TypeError, match=f"{name}\\(\\) takes exactly 2 arguments \\({len(args)} given\\)"):
        getattr(_engine, name)(*args)


@pytest.mark.parametrize("name", _ENTRY_NAMES)
def test_engine_entry_rejects_keyword_arguments(name: str) -> None:
    with pytest.raises(TypeError, match=f"{name}\\(\\) takes no keyword arguments"):
        getattr(_engine, name)("NFC", text="x")
