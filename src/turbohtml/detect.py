"""
turbohtml.detect: standalone character-encoding detection over bytes.

:func:`detect` answers the question ``chardet``, ``charset-normalizer``, and ``cchardet`` exist for -- "what encoding
are these bytes?" -- without an HTML parser in the call path. It runs the same C pipeline :func:`turbohtml.parse`
uses for ``bytes`` input: the WHATWG sniff first (a byte-order mark, then a ``<meta>`` prescan of the first 1024
bytes), then the content detector ported from Firefox's `chardetng <https://github.com/hsivonen/chardetng>`__, then
the spec's windows-1252 fallback. The same input therefore always yields the same encoding whether you detect it
standalone or parse it with ``detect_encoding=True``.

A result is an :class:`EncodingMatch` with the WHATWG canonical name, a confidence, and the language the frequency
model matched, mirroring the ``chardet.detect`` dict shape as a typed record. :func:`detect_all` ranks every
surviving candidate, :class:`Detector` accumulates a stream chunk by chunk like chardet's ``UniversalDetector``, and
a frozen :class:`Detection` config carries the knobs (a confidence floor and encoding/language constraints).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from ._html import _detect

__all__ = [
    "Detection",
    "Detector",
    "EncodingMatch",
    "detect",
    "detect_all",
]

_LANGUAGES: Final[dict[str, str]] = {
    "big5": "Chinese",
    "euc-jp": "Japanese",
    "euc-kr": "Korean",
    "gbk": "Chinese",
    "ibm866": "Russian",
    "iso-2022-jp": "Japanese",
    "iso-8859-5": "Russian",
    "iso-8859-6": "Arabic",
    "iso-8859-7": "Greek",
    "iso-8859-8": "Hebrew",
    "koi8-u": "Russian",
    "shift_jis": "Japanese",
    "windows-1251": "Russian",
    "windows-1253": "Greek",
    "windows-1254": "Turkish",
    "windows-1255": "Hebrew",
    "windows-1256": "Arabic",
    "windows-1258": "Vietnamese",
    "windows-874": "Thai",
}
"""The language each frequency-scored candidate models; multi-language Latin encodings stay unmapped."""


@dataclass(frozen=True, slots=True)
class EncodingMatch:
    """
    One detection result: ``chardet.detect``'s ``{"encoding", "confidence", "language"}`` dict as a typed record.

    ``encoding`` is the WHATWG canonical name (the same string :attr:`turbohtml.Document.encoding` reports), or
    ``None`` when the input is empty or every candidate was ruled out. ``confidence`` is 1.0 for a certain result (a
    byte-order mark, a ``<meta>`` declaration, structurally valid UTF-8, escape-driven ISO-2022-JP, or pure ASCII),
    the candidate's share of the positive frequency scores for a content-scored result, and 0.0 for the windows-1252
    fallback chosen with no positive evidence. ``language`` names the language the winning frequency model targets
    (``"Russian"`` for windows-1251, ``"Japanese"`` for Shift_JIS, ...); it is ``None`` for UTF-8, ASCII, and the
    Latin encodings whose model spans several languages.
    """

    encoding: str | None
    confidence: float
    language: str | None


_NO_MATCH: Final = EncodingMatch(None, 0.0, None)


@dataclass(frozen=True, slots=True)
class Detection:
    """
    Options for :func:`detect`, :func:`detect_all`, and :class:`Detector`.

    :param threshold: the confidence floor; a candidate below it is dropped, and when every candidate falls below it
        the result's ``encoding`` is ``None``. The default 0.0 always answers, like ``chardet.detect``.
    :param language: prefer this language when the evidence is ambiguous: candidates whose frequency model targets it
        (the value :class:`EncodingMatch` reports as its language) rank ahead of the rest, provided they scored
        positively.
    :param allowed: when set, only these encodings (WHATWG names, any case) may be returned.
    :param excluded: these encodings are never returned; mutually exclusive with ``allowed``.
    """

    threshold: float = 0.0
    language: str | None = None
    allowed: frozenset[str] | None = None
    excluded: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        """Reject an out-of-range threshold and the contradictory allowed+excluded combination."""
        if not 0.0 <= self.threshold <= 1.0:
            msg = f"threshold must be within [0.0, 1.0], got {self.threshold}"
            raise ValueError(msg)
        if self.allowed is not None and self.excluded:
            msg = "allowed and excluded are mutually exclusive"
            raise ValueError(msg)

    @classmethod
    def chardet(cls) -> Detection:
        """Chardet's ``UniversalDetector`` mode: report ``None`` under its 0.2 minimum confidence."""
        return cls(threshold=0.2)


_DEFAULT: Final = Detection()


def detect(data: bytes, options: Detection | None = None, /) -> EncodingMatch:
    """
    Detect the character encoding of a byte string, the ``chardet.detect`` / ``cchardet.detect`` successor.

    :param data: the bytes to sniff; HTML input also honors a ``<meta>`` charset declaration.
    :param options: the detection options; defaults to :class:`Detection` (always answer, no constraints).
    :returns: the best match; its ``encoding`` is ``None`` when the input is empty or every candidate was ruled out.
    """
    return _matches(data, options or _DEFAULT)[0]


def detect_all(data: bytes, options: Detection | None = None, /) -> list[EncodingMatch]:
    """
    Detect the character encoding and rank every plausible candidate, the ``chardet.detect_all`` successor.

    :param data: the bytes to sniff; HTML input also honors a ``<meta>`` charset declaration.
    :param options: the detection options; defaults to :class:`Detection` (always answer, no constraints).
    :returns: the matches best first, :func:`detect`'s result leading; ``[EncodingMatch(None, 0.0, None)]`` when the
        input is empty or every candidate was ruled out.
    """
    return _matches(data, options or _DEFAULT)


class Detector:
    """
    Incremental detection over a byte stream, mirroring chardet's ``UniversalDetector``.

    Call :meth:`feed` with each chunk, :meth:`close` for the result, and :meth:`reset` to reuse the instance on
    another stream. :attr:`done` turns true as soon as the result cannot change (a leading byte-order mark, or
    :meth:`close`), so a reader loop can stop early. Feeding buffers the chunks and :meth:`close` detects once over
    the whole stream, so the result always equals :func:`detect` of the concatenated bytes. An instance is not
    thread-safe; use one per stream.

    :param options: the detection options; defaults to :class:`Detection` (always answer, no constraints).
    """

    done: bool
    """Whether the result can no longer change: a leading byte-order mark was seen, or :meth:`close` ran."""

    def __init__(self, options: Detection | None = None, /) -> None:
        """Start an empty stream with the given options."""
        self._options = options or _DEFAULT
        self._buffer = bytearray()
        self._result: EncodingMatch | None = None
        self.done = False

    @property
    def result(self) -> EncodingMatch | None:
        """The match :meth:`close` computed, or ``None`` while the stream is still open."""
        return self._result

    def feed(self, data: bytes) -> None:
        """
        Buffer one chunk of the stream; ignored once :attr:`done`.

        :param data: the next bytes of the stream.
        """
        if self.done:
            return
        self._buffer += data
        if self._buffer.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")):
            self.done = True  # a byte-order mark decides the stream outright, whatever follows

    def close(self) -> EncodingMatch:
        """Detect over everything fed so far, cache the result, and return it."""
        if self._result is None:
            self._result = detect(bytes(self._buffer), self._options)
            self.done = True
        return self._result

    def reset(self) -> None:
        """Forget the stream and the result so the instance can start over."""
        self._buffer.clear()
        self._result = None
        self.done = False


def _matches(data: bytes, options: Detection) -> list[EncodingMatch]:
    """Rank the candidates for ``data``, apply the options, and always return at least the no-match sentinel."""
    ranked = _candidates(data)
    if (allowed := options.allowed) is not None:
        permitted = {name.casefold() for name in allowed}
        ranked = [(name, confidence) for name, confidence in ranked if name.casefold() in permitted]
    if blocked := {name.casefold() for name in options.excluded}:
        ranked = [(name, confidence) for name, confidence in ranked if name.casefold() not in blocked]
    if (hint := options.language) is not None:
        preferred = [pair for pair in ranked if pair[1] > 0.0 and _LANGUAGES.get(pair[0].casefold()) == hint]
        ranked = preferred + [pair for pair in ranked if pair not in preferred]
    matches = [
        EncodingMatch(name, confidence, _LANGUAGES.get(name.casefold()))
        for name, confidence in ranked
        if confidence >= options.threshold
    ]
    return matches or [_NO_MATCH]


def _candidates(data: bytes) -> list[tuple[str, float]]:
    """
    Run the C sniff and shape its output into (canonical name, confidence) pairs, best first.

    A certain result (declaration, structural proof, or pure ASCII) is a single pair at confidence 1.0. A scored
    result normalizes each candidate's raw chardetng score to its share of the positive total, ordered score-descending
    with the C emission order breaking ties exactly as the engine's strict-max does, and the engine's winner moved to
    the front so index 0 always matches what ``parse(detect_encoding=True)`` would decode with. Two candidates can
    share one encoding (the windows-1252 model runs once per language family), so the best-scored entry per name wins.
    """
    if not data:
        return []
    winner, certain, scored = _detect(data)
    if certain or winner is None:
        return [(winner or "ascii", 1.0)]
    unique: dict[str, int] = {}
    for name, score in sorted(scored, key=lambda pair: -pair[1]):
        unique.setdefault(name, score)
    total = sum(score for score in unique.values() if score > 0)
    ranked = [(name, score / total if score > 0 else 0.0) for name, score in unique.items()]
    at = next((index for index, pair in enumerate(ranked) if pair[0] == winner), None)
    if at is None:  # the windows-1252 fallback won without surviving as a candidate itself
        return [(winner, 0.0), *ranked]
    return [ranked[at], *ranked[:at], *ranked[at + 1 :]]
