"""
turbohtml.detect: guess the character encoding of a byte stream, no HTML parser in the call path.

The WHATWG content-detection engine that backs ``parse(detect_encoding=True)`` is exposed here directly as a
``bytes -> encoding`` surface that replaces ``chardet``, ``charset-normalizer`` and ``cchardet``. The pipeline mirrors a
browser: an explicit declaration wins first (a byte-order mark, then a ``<meta charset>`` prescan), and only a
declaration-less stream falls through to the chardetng-style heuristic, which structurally resolves UTF-8 and
ISO-2022-JP and otherwise runs the single-byte and CJK candidates in a strict-max competition that defaults to
windows-1252.

:func:`detect` returns the single best :class:`EncodingMatch`; :func:`detect_all` returns the ranked alternatives; the
incremental :class:`Detector` buffers fed chunks and resolves on :meth:`Detector.close`, mirroring chardet's
``UniversalDetector``. All three take one immutable :class:`Detection` config (a confidence threshold, a language hint,
and encoding allow/deny sets), so the same tuning applies across the surface.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import TYPE_CHECKING

from ._html import _detect

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = [
    "Detection",
    "Detector",
    "EncodingMatch",
    "detect",
    "detect_all",
]

# The natural language each detector-producible encoding implies; ``None`` for the Unicode
# transports and the Latin scripts that several languages share. The keys are the complete
# set of canonical names the detector can report, which the allow/deny validation checks
# against. A legacy single-byte or CJK encoding stands in for a script, so the language is
# that script's dominant one (every Cyrillic page reports "Russian", every CJK page its
# script's language) rather than a guess at the specific tongue.
_LANGUAGES: dict[str, str | None] = {
    "UTF-8": None,
    "UTF-16BE": None,
    "UTF-16LE": None,
    "ascii": None,
    "ISO-2022-JP": "Japanese",
    "Shift_JIS": "Japanese",
    "EUC-JP": "Japanese",
    "EUC-KR": "Korean",
    "GBK": "Chinese",
    "Big5": "Chinese",
    "windows-1252": None,
    "windows-1250": None,
    "ISO-8859-2": None,
    "windows-1251": "Russian",
    "KOI8-U": "Russian",
    "IBM866": "Russian",
    "ISO-8859-5": "Russian",
    "windows-1253": "Greek",
    "ISO-8859-7": "Greek",
    "windows-1254": "Turkish",
    "windows-1255": "Hebrew",
    "ISO-8859-8": "Hebrew",
    "ISO-8859-8-I": "Hebrew",
    "windows-1256": "Arabic",
    "ISO-8859-6": "Arabic",
    "windows-1257": None,
    "ISO-8859-13": None,
    "ISO-8859-4": None,
    "windows-1258": "Vietnamese",
    "windows-874": "Thai",
}
_KNOWN_ENCODINGS = frozenset(_LANGUAGES)
_KNOWN_LANGUAGES = frozenset(language for language in _LANGUAGES.values() if language is not None)

# A heuristic guess never claims the certainty an explicit declaration or pure ASCII does.
_STRUCTURAL_CONFIDENCE = 0.99
# Maps a chardetng evidence score to a confidence: the larger the score, the closer to
# certain, with a no-evidence score (the windows-1252 default, 0) sitting at the 0.5 midpoint.
_SCORE_SCALE = 256.0


@dataclass(frozen=True)
class EncodingMatch:
    """
    One detection result, the typed form of chardet's ``detect()`` dict.

    :param encoding: the detected encoding's canonical name (the same name :attr:`turbohtml.Document.encoding`
        reports), or
        ``None`` when nothing could be detected (an empty stream, or every candidate filtered out).
    :param confidence: how strongly the stream supports this encoding, in ``0.0..1.0``. An explicit byte-order mark or
        ``<meta charset>`` declaration and pure ASCII report ``1.0``; a structural UTF-8 / ISO-2022-JP match reports
        ``0.99``; a heuristic guess scales with the evidence and is capped below structural certainty.
    :param language: the natural language the encoding implies (e.g. ``"Russian"`` for a Cyrillic code page), or
        ``None`` for the Unicode transports and the Latin scripts shared across languages.
    """

    encoding: str | None
    confidence: float
    language: str | None


@dataclass(frozen=True)
class Detection:
    """
    How :func:`detect`, :func:`detect_all` and :class:`Detector` weigh and filter candidate encodings.

    Build one and reuse it across threads; every field has a permissive default, so ``Detection()`` ranks every
    candidate the engine finds with no threshold. Use :meth:`Detection.chardet` for the confidence floor chardet's
    ``UniversalDetector`` applies.

    :param minimum_confidence: drop any result whose confidence is below this, in ``0.0..1.0``; ``0.0`` keeps them all.
    :param language: keep only encodings implying this natural language (e.g. ``"Greek"``); ``""`` keeps every language.
    :param allowed: when given, the only canonical encoding names that may be returned; ``None`` allows all. Must be
        disjoint from ``excluded``.
    :param excluded: canonical encoding names that may never be returned.
    """

    minimum_confidence: float = 0.0
    language: str = ""
    allowed: frozenset[str] | None = None
    excluded: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        """Reject an out-of-range threshold, an unknown language or encoding name, and overlapping allow/deny sets."""
        if not 0.0 <= self.minimum_confidence <= 1.0:
            msg = f"minimum_confidence must be within 0.0..1.0, got {self.minimum_confidence!r}"
            raise ValueError(msg)
        if self.language and self.language not in _KNOWN_LANGUAGES:
            msg = f"unknown language {self.language!r}; expected one of {sorted(_KNOWN_LANGUAGES)}"
            raise ValueError(msg)
        if unknown := ((self.allowed or frozenset()) | self.excluded) - _KNOWN_ENCODINGS:
            known = sorted(_KNOWN_ENCODINGS)
            msg = f"unknown encoding name(s) {sorted(unknown)}; expected canonical names from {known}"
            raise ValueError(msg)
        if self.allowed is not None and (overlap := self.allowed & self.excluded):
            msg = f"encoding(s) {sorted(overlap)} are both allowed and excluded"
            raise ValueError(msg)

    @classmethod
    def chardet(cls) -> Detection:
        """
        Mirror chardet's ``UniversalDetector`` default: drop guesses below its 0.20 confidence floor.

        :returns: a config with ``minimum_confidence`` set to chardet's threshold.
        """
        return cls(minimum_confidence=0.20)

    def _unpack(self) -> dict[str, object]:
        """Return the fields that differ from the no-argument default, so an empty config filters nothing."""
        default = _DEFAULT
        return {
            field_.name: value
            for field_ in fields(self)
            if (value := getattr(self, field_.name)) != getattr(default, field_.name)
        }


_DEFAULT = Detection()


def _confidence(score: int) -> float:
    """Map a chardetng evidence score to a confidence in ``0.0..0.99``, monotonic and centred at 0.5 for no evidence."""
    value = 0.5 + 0.49 * score / (abs(score) + _SCORE_SCALE)
    return min(max(value, 0.0), _STRUCTURAL_CONFIDENCE)


def _matches(data: bytes | bytearray | memoryview) -> Iterator[EncodingMatch]:
    """Run the C detector and yield its results as ranked, unfiltered :class:`EncodingMatch` records."""
    method, rows = _detect(data)
    if method == "ascii":
        yield EncodingMatch("ascii", 1.0, None)
        return
    if method == "heuristic":
        for name, score in rows:
            yield EncodingMatch(name, _confidence(score), _LANGUAGES.get(name))
        return
    fixed = 1.0 if method in {"bom", "meta"} else _STRUCTURAL_CONFIDENCE
    for name, _score in rows:
        yield EncodingMatch(name, fixed, _LANGUAGES.get(name))


def detect_all(data: bytes | bytearray | memoryview, options: Detection | None = None, /) -> list[EncodingMatch]:
    """
    Rank every encoding the engine keeps for ``data``, best first, the way chardet's ``detect_all`` does.

    A declared encoding (byte-order mark or ``<meta charset>``) or a structural UTF-8 / ISO-2022-JP match yields a
    single result; a declaration-less stream yields the heuristic competition's ranked candidates. An empty stream
    yields an empty list.

    :param data: the bytes to inspect (``bytes``, ``bytearray``, or ``memoryview``).
    :param options: the :class:`Detection` config; ``None`` ranks every candidate with no threshold.
    :returns: the matching encodings, highest confidence first, after applying the config's filters.
    """
    if len(data) == 0:
        return []
    config = options or _DEFAULT
    # the defaults filter nothing (no allow-list, no deny-list, no language, a 0.0 floor), so an
    # empty config reproduces the no-argument behaviour; _unpack() carries the same non-defaults
    return [
        match
        for match in _matches(data)
        if (config.allowed is None or match.encoding in config.allowed)
        and match.encoding not in config.excluded
        and (not config.language or match.language == config.language)
        and match.confidence >= config.minimum_confidence
    ]


def detect(data: bytes | bytearray | memoryview, options: Detection | None = None, /) -> EncodingMatch:
    """
    Guess the single best encoding for ``data``, the way chardet's ``detect`` does.

    :param data: the bytes to inspect (``bytes``, ``bytearray``, or ``memoryview``).
    :param options: the :class:`Detection` config; ``None`` returns the engine's top candidate.
    :returns: the best :class:`EncodingMatch`, or one whose ``encoding`` is ``None`` when nothing survives the filters.
    """
    matches = detect_all(data, options)
    return matches[0] if matches else EncodingMatch(None, 0.0, None)


class Detector:
    """
    Incremental encoding detection over a fed byte stream, mirroring chardet's ``UniversalDetector``.

    Feed chunks with :meth:`feed`, then call :meth:`close` to resolve. The engine scores the whole stream at once, so
    the buffered bytes are detected together on close; :meth:`reset` clears the buffer to reuse the detector. Unlike
    :class:`Detection`, a detector is stateful and not safe to share across threads.

    :param options: the :class:`Detection` config applied on :meth:`close`; ``None`` uses the defaults.
    """

    def __init__(self, options: Detection | None = None, /) -> None:
        """Start an empty detector that applies ``options`` (or the defaults) when the stream is closed."""
        self._options = options
        self._chunks: list[bytes] = []
        self._result: EncodingMatch | None = None

    def feed(self, data: bytes | bytearray | memoryview) -> None:
        """Buffer another chunk of the stream; ignored once :meth:`close` has resolved, until :meth:`reset`."""
        if self._result is None:
            self._chunks.append(bytes(data))

    def close(self) -> EncodingMatch:
        """Detect the buffered stream and cache the result; repeated calls return the same :class:`EncodingMatch`."""
        if self._result is None:
            self._result = detect(b"".join(self._chunks), self._options)
        return self._result

    def reset(self) -> None:
        """Drop the buffered bytes and any cached result, so the detector can be fed a fresh stream."""
        self._chunks.clear()
        self._result = None

    @property
    def result(self) -> EncodingMatch | None:
        """The resolved :class:`EncodingMatch` after :meth:`close`, or ``None`` before the stream is closed."""
        return self._result
