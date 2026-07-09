"""
turbohtml.detect: standalone character-encoding detection over bytes.

:func:`detect` answers the question ``chardet``, ``charset-normalizer``, and ``cchardet`` exist for -- "what encoding
are these bytes?" -- without an HTML parser in the call path. It runs the same C pipeline :func:`turbohtml.parse`
uses for ``bytes`` input: the WHATWG sniff first (a byte-order mark, then a ``<meta>`` prescan of the first 1024
bytes), then a content detector that validates UTF-8 structurally and otherwise scores the CJK and single-byte
candidates on character-pair frequencies, then the spec's windows-1252 fallback. A non-mark input therefore yields the
same encoding whether you detect it standalone or parse it with ``detect_encoding=True``, with two divergences. A
byte-order mark is reported here with its own label (``UTF-8-SIG`` and the UTF-16/UTF-32 marks) so a caller can strip
it, where the spec-locked parse path keeps the plain WHATWG name. And a ``<meta>`` charset past the prescan's 1024-byte
window is invisible here, because these functions read bytes and have no tree to consult, where :func:`turbohtml.parse`
redoes the parse against what it declares.

A result is an :class:`EncodingMatch` with the WHATWG canonical name, a confidence, and the language the frequency
model matched, mirroring the ``chardet.detect`` dict shape as a typed record. :func:`detect_all` ranks every
surviving candidate, :class:`EncodingDetector` accumulates a stream chunk by chunk like chardet's
``UniversalDetector``, and a frozen :class:`Detection` config carries the knobs (a confidence floor and
encoding/language constraints).

:func:`detect_language` answers the separate question ``whatlang``, ``resiliparse``, and ``trafilatura`` exist for --
"what natural language is this text?" -- from the visible text rather than an ``<html lang>`` attribute. It runs a
character-trigram model: find the dominant Unicode script, then rank the languages sharing it by how closely the
text's most frequent character trigrams match each language's embedded profile. It returns a :class:`LanguageMatch`
(ISO 639-3 code, confidence, script, English name); a frozen :class:`LanguageDetection` config carries a confidence
floor and language constraints.

:func:`normalize` runs Unicode normalization (UAX #15) in all four forms -- NFC, NFD, NFKC, NFKD -- the transform
:func:`unicodedata.normalize` does whole strings, reimplemented in C over pinned tables generated from the same
``unicodedata`` so the two agree exactly. A quick check returns already-normalized text untouched, and
:func:`is_normalized` answers the membership question without building the normalized copy.
"""

from __future__ import annotations

import codecs
import string
from dataclasses import dataclass
from typing import Final, Literal

from ._html import _decode, _detect, _detect_language, _DetectStream, _is_normalized, _normalize

__all__ = [
    "Detection",
    "EncodingDetector",
    "EncodingMatch",
    "LanguageDetection",
    "LanguageMatch",
    "NormalizationForm",
    "detect",
    "detect_all",
    "detect_language",
    "is_normalized",
    "normalize",
]

NormalizationForm = Literal["NFC", "NFD", "NFKC", "NFKD"]

_FORMS: Final[dict[str, int]] = {"NFC": 0, "NFD": 1, "NFKC": 2, "NFKD": 3}

# The names a byte-order mark reports, keyed the way CPython normalizes a codec name (lowercased, every non-alphanumeric
# byte an underscore). The spec's label table does not carry them, and CPython's UTF-8 and UTF-16 decoders already agree
# with it byte for byte, so these delegate rather than going through the native decoders.
_BOM_CODECS: Final[dict[str, str]] = {
    "utf_8_sig": "utf-8-sig",
    "utf_16le": "utf-16-le",
    "utf_16be": "utf-16-be",
    "utf_32le": "utf-32-le",
    "utf_32be": "utf-32-be",
}


def _refuse_encode(text: str, errors: str = "strict", /) -> tuple[bytes, int]:  # noqa: ARG001
    """Refuse to encode: the generated tables are decode-side only, and the spec's encoders are a separate algorithm."""
    msg = "a whatwg-* codec decodes only; encode with the CPython codec of your choice"
    raise UnicodeError(msg)


def _search(name: str) -> codecs.CodecInfo | None:
    """
    Resolve a ``whatwg-*`` codec name.

    These codecs exist because no safe name already does: ``bytes.decode("big5")`` reaches CPython's Big5, a strict
    subset of the spec's, ``koi8-u`` reaches KOI8-U where the spec means KOI8-RU, and ``x-mac-cyrillic`` reaches no
    codec at all.

    The name arrives lowercased and underscored up to Python 3.14 and verbatim from 3.15 on, so normalize it here rather
    than trust either. Underscoring is lossy -- ``shift_jis`` and ``shift-jis`` collapse -- so both spellings of the
    label are offered to the C lookup, which knows every spec alias.
    """
    normalized = name.lower().replace("-", "_")
    if not normalized.startswith("whatwg_"):
        return None
    normalized = normalized.removeprefix("whatwg_")
    if (delegate := _BOM_CODECS.get(normalized)) is not None:
        return codecs.CodecInfo(_refuse_encode, codecs.lookup(delegate).decode, name=name)
    label = next((form for form in (normalized, normalized.replace("_", "-")) if _decodable(form)), None)
    if label is None:
        return None

    def decode(data: bytes, errors: str = "strict", /) -> tuple[str, int]:  # noqa: ARG001
        return _decode(data, label), len(data)

    # CodecInfo's decoder is typed against _typeshed.ReadableBuffer, which Sphinx cannot import when it walks the
    # annotations at doc-build time; bytes is what the codecs machinery ever passes a decode function.
    return codecs.CodecInfo(_refuse_encode, decode, name=name)  # ty: ignore[invalid-argument-type]


def _decodable(label: str) -> bool:
    """Report whether the C label table knows *label*, so the native decoder can be reached through it."""
    try:
        _decode(b"", label)
    except LookupError:
        return False
    return True


codecs.register(_search)


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
    ``None`` when the input is empty or every candidate was ruled out. A leading byte-order mark reports the mark's own
    label instead: ``"UTF-8-SIG"`` for a UTF-8 mark (so a caller can decode with the ``utf-8-sig`` codec to strip it),
    and ``"UTF-16LE"`` / ``"UTF-16BE"`` / ``"UTF-32LE"`` / ``"UTF-32BE"`` for the UTF-16 and UTF-32 marks, which a mark
    identifies unambiguously with no heuristic.

    ``codec`` is the name to hand :meth:`bytes.decode`; ``encoding`` is not. A WHATWG name and the CPython codec that
    answers to it are different encodings: ``bytes.decode("big5")`` reaches a strict subset of the spec's Big5,
    ``koi8-u`` reaches KOI8-U where the spec means KOI8-RU, and ``x-mac-cyrillic`` reaches no codec at all. So
    ``codec`` names a ``whatwg-*`` codec this module registers, whose decoder is the one :func:`turbohtml.parse`
    uses, and ``data.decode(match.codec)`` reproduces the parser's text. It is ``None`` with a ``None`` encoding.
    Those codecs decode only: encoding to a legacy charset is a separate spec algorithm turbohtml omits.

    ``confidence`` is 1.0 for a certain result (a byte-order mark, a ``<meta>`` declaration, structurally valid UTF-8,
    escape-driven ISO-2022-JP, or pure ASCII), the candidate's share of the positive frequency scores for a
    content-scored result, and 0.0 for the windows-1252 fallback chosen with no positive evidence. ``language`` names
    the language the winning frequency model targets (``"Russian"`` for windows-1251, ``"Japanese"`` for Shift_JIS,
    ...); it is ``None`` for UTF-8 and the Latin encodings whose model spans several languages. ``bom`` is true only
    when a byte-order mark decided the result, telling a caller the decoded text still carries the mark unless the
    codec strips it.
    """

    encoding: str | None
    confidence: float
    language: str | None
    bom: bool = False
    codec: str | None = None


_NO_MATCH: Final = EncodingMatch(None, 0.0, None)


# Every character a DNS label may hold once it is lower-cased and Punycode-encoded (RFC 1123, plus RFC 3492's "xn--").
_TLD_ALPHABET: Final[frozenset[str]] = frozenset(string.ascii_lowercase + string.digits + "-")


@dataclass(frozen=True, slots=True)
class Detection:
    """
    Options for :func:`detect`, :func:`detect_all`, and :class:`EncodingDetector`.

    :param threshold: the confidence floor; a candidate below it is dropped, and when every candidate falls below it
        the result's ``encoding`` is ``None``. The default 0.0 always answers, like ``chardet.detect``.
    :param language: prefer this language when the evidence is ambiguous: candidates whose frequency model targets it
        (the value :class:`EncodingMatch` reports as its language) rank ahead of the rest, provided they scored
        positively.
    :param allowed: when set, only these encodings (WHATWG names, any case) may be returned.
    :param excluded: these encodings are never returned; mutually exclusive with ``allowed``.
    :param tld: the rightmost DNS label of the host that served the bytes (``"jp"``, ``"ru"``, ``"xn--p1ai"``), which
        narrows the candidates the way a browser does. Give it in lower-case ASCII, without the leading dot, and in
        Punycode for an internationalized domain. A two-letter label the classifier does not carry reads as Western
        European; a generic label such as ``"com"`` carries no hint, and neither does the default ``None``.
    """

    threshold: float = 0.0
    language: str | None = None
    allowed: frozenset[str] | None = None
    excluded: frozenset[str] = frozenset()
    tld: str | None = None

    def __post_init__(self) -> None:
        """Reject an out-of-range threshold, the contradictory allowed+excluded combination, and a malformed TLD."""
        if not 0.0 <= self.threshold <= 1.0:
            msg = f"threshold must be within [0.0, 1.0], got {self.threshold}"
            raise ValueError(msg)
        if self.allowed is not None and self.excluded:
            msg = "allowed and excluded are mutually exclusive"
            raise ValueError(msg)
        # a whole hostname, a leading dot, or an upper-case letter would otherwise read as no hint at all rather
        # than as the mistake it is; chardetng panics on the same labels rather than classify them
        if self.tld is not None and not (self.tld and set(self.tld) <= _TLD_ALPHABET):
            msg = f"tld must be the rightmost DNS label in lower-case ASCII, got {self.tld!r}"
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
    :returns: the best match; its ``encoding`` is ``None`` when the input is empty or every candidate was ruled out. A
        leading byte-order mark reports its own label (``UTF-8-SIG``, ``UTF-16LE``/``BE``, ``UTF-32LE``/``BE``) with
        ``bom`` set, so a caller can strip it.
    :raises TypeError: when ``data`` is not a bytes-like object.
    """
    return _matches(data, options or _DEFAULT)[0]


def detect_all(data: bytes, options: Detection | None = None, /) -> list[EncodingMatch]:
    """
    Detect the character encoding and rank every plausible candidate, the ``chardet.detect_all`` successor.

    :param data: the bytes to sniff; HTML input also honors a ``<meta>`` charset declaration.
    :param options: the detection options; defaults to :class:`Detection` (always answer, no constraints).
    :returns: the matches best first, :func:`detect`'s result leading; ``[EncodingMatch(None, 0.0, None)]`` when the
        input is empty or every candidate was ruled out. A leading byte-order mark collapses the ranking to one match
        carrying its own label and ``bom``.
    :raises TypeError: when ``data`` is not a bytes-like object.
    """
    return _matches(data, options or _DEFAULT)


class EncodingDetector:
    """
    Incremental detection over a byte stream, mirroring chardet's ``UniversalDetector``.

    Call :meth:`feed` with each chunk, :meth:`close` for the result, and :meth:`reset` to reuse the instance on
    another stream. :attr:`done` turns true as soon as the result cannot change (a leading byte-order mark, or
    :meth:`close`), so a reader loop can stop early. Every candidate carries its own state across feeds, so the
    detector holds a fixed amount of memory whatever the stream's length; the one buffer it keeps is the leading
    1024 bytes the byte-order-mark check and the ``<meta>`` prescan read, which the spec bounds. The result equals
    :func:`detect` of the concatenated bytes, whatever the chunk boundaries. An instance is not thread-safe; use one
    per stream.

    :param options: the detection options; defaults to :class:`Detection` (always answer, no constraints).
    """

    done: bool
    """Whether the result can no longer change: a leading byte-order mark was seen, or :meth:`close` ran."""

    def __init__(self, options: Detection | None = None, /) -> None:
        """Start an empty stream with the given options."""
        self._options = options or _DEFAULT
        self._stream = _DetectStream(self._options.tld)
        self._head = b""
        self._fed = False
        self._result: EncodingMatch | None = None
        self.done = False

    @property
    def result(self) -> EncodingMatch | None:
        """The match :meth:`close` computed, or ``None`` while the stream is still open."""
        return self._result

    def feed(self, data: bytes) -> None:
        """
        Detect over one chunk of the stream; ignored once :attr:`done`.

        :param data: the next bytes of the stream.
        """
        if self.done or not data:
            return
        self._fed = True
        self._stream.feed(data)
        if len(self._head) < 4:
            self._head = (self._head + bytes(data))[:4]
        if self._head.startswith((b"\xef\xbb\xbf", b"\xfe\xff", b"\x00\x00\xfe\xff", b"\xff\xfe\x00\x00")):
            self.done = True  # a fully-resolved byte-order mark decides the stream, whatever follows
        elif self._head.startswith(b"\xff\xfe") and len(self._head) >= 4:
            self.done = True  # FF FE is UTF-16LE now that the next pair rules out the FF FE 00 00 UTF-32LE mark

    def close(self) -> EncodingMatch:
        """Read the detector's answer, cache it, and return it."""
        if self._result is None:
            shaped = _shape(self._stream.close()) if self._fed else ([], False)
            self._result = _rank(shaped, self._options)[0]
            self.done = True
        return self._result

    def reset(self) -> None:
        """Forget the stream and the result so the instance can start over."""
        self._stream = _DetectStream(self._options.tld)
        self._head = b""
        self._fed = False
        self._result = None
        self.done = False


def _matches(data: bytes, options: Detection) -> list[EncodingMatch]:
    """Rank the candidates for ``data``, apply the options, and always return at least the no-match sentinel."""
    return _rank(_candidates(data, options.tld), options)


def _rank(shaped: tuple[list[tuple[str, float]], bool], options: Detection) -> list[EncodingMatch]:
    """Apply the options to shaped candidates, and always return at least the no-match sentinel."""
    ranked, had_bom = shaped
    if (allowed := options.allowed) is not None:
        permitted = {name.casefold() for name in allowed}
        ranked = [(name, confidence) for name, confidence in ranked if name.casefold() in permitted]
    if blocked := {name.casefold() for name in options.excluded}:
        ranked = [(name, confidence) for name, confidence in ranked if name.casefold() not in blocked]
    if (hint := options.language) is not None:
        preferred = [pair for pair in ranked if pair[1] > 0.0 and _LANGUAGES.get(pair[0].casefold()) == hint]
        ranked = preferred + [pair for pair in ranked if pair not in preferred]
    matches = [
        EncodingMatch(name, confidence, _LANGUAGES.get(name.casefold()), had_bom, f"whatwg-{name.casefold()}")
        for name, confidence in ranked
        if confidence >= options.threshold
    ]
    return matches or [_NO_MATCH]


def _candidates(data: bytes, tld: str | None) -> tuple[list[tuple[str, float]], bool]:
    """
    Run the C sniff and shape its output into (canonical name, confidence) pairs plus the byte-order-mark flag.

    A certain result (a byte-order mark, a declaration, a structural proof, or pure ASCII) is a single pair at
    confidence 1.0; a mark makes it so and sets the flag, so the flag is only ever true for that lone certain pair. A
    scored result normalizes each candidate's raw chardetng score to its share of the positive total, ordered
    score-descending with the C emission order breaking ties exactly as the engine's strict-max does, and the engine's
    winner moved to the front so index 0 always matches what ``parse(detect_encoding=True)`` would decode with. Two
    candidates can share one encoding (the windows-1252 model runs once per language family), so the best-scored entry
    per name wins. A stream with no non-ASCII byte carries no evidence, so it takes the spec's windows-1252 fallback,
    which decodes ASCII identically -- the answer ``parse(detect_encoding=True)`` reaches for the same bytes.
    """
    return _shape(_detect(data, tld)) if data else ([], False)


def _shape(result: tuple[str | None, bool, list[tuple[str, int]], bool]) -> tuple[list[tuple[str, float]], bool]:
    """Shape one C sniff result, from either the one-shot detect or the streaming detector."""
    winner, certain, scored, bom = result
    if certain or winner is None:
        return [(winner or "windows-1252", 1.0)], bom
    unique: dict[str, int] = {}
    for name, score in sorted(scored, key=lambda pair: -pair[1]):
        unique.setdefault(name, score)
    total = sum(score for score in unique.values() if score > 0)
    ranked = [(name, score / total if score > 0 else 0.0) for name, score in unique.items()]
    at = next((index for index, pair in enumerate(ranked) if pair[0] == winner), None)
    if at is None:  # the windows-1252 fallback won without surviving as a candidate itself
        return [(winner, 0.0), *ranked], bom
    return [ranked[at], *ranked[:at], *ranked[at + 1 :]], bom


@dataclass(frozen=True, slots=True)
class LanguageMatch:
    """
    One language-detection result: the ISO 639-3 code, a confidence, the Unicode script, and an English name.

    ``language`` is the `ISO 639-3 <https://en.wikipedia.org/wiki/ISO_639-3>`__ code the text was written in
    (``"eng"``, ``"deu"``, ``"cmn"``, ...), or ``None`` when the text carries no script the detector models (it is
    empty, or all punctuation, digits, emoji, or symbols) or every candidate for its script was ruled out by
    :class:`LanguageDetection`. ``confidence`` runs 0.0 to 1.0: it is 1.0 for a script only one modeled language uses
    (Greek, Georgian, Korean, ...) and for a clear trigram winner, and drops toward 0.0 as the top two candidates for a
    shared script (Latin, Cyrillic, Arabic, Devanagari, Hebrew) converge -- the usual signal that the text is too short
    to separate them. ``script`` is the Unicode script name the text is written in (``"Latin"``, ``"Cyrillic"``,
    ``"Han"`` reported as ``"Mandarin"``, ...), or ``None`` with a ``None`` language. ``name`` is the English name of
    the language (``"English"``, ``"German"``, ``"Mandarin"``, ...), or ``None`` with a ``None`` language.
    """

    language: str | None
    confidence: float
    script: str | None
    name: str | None = None


_NO_LANGUAGE: Final = LanguageMatch(None, 0.0, None)


@dataclass(frozen=True, slots=True)
class LanguageDetection:
    """
    Options for :func:`detect_language`.

    :param threshold: the confidence floor; a result below it is reported as :data:`LanguageMatch(None, 0.0, None)
        <LanguageMatch>` so a short or ambiguous input does not masquerade as a confident answer. The default 0.0
        always answers.
    :param allowed: when set, only these languages (ISO 639-3 codes) may be returned; a text whose script has no
        allowed language yields no match. Constrains every script, including the single-language ones.
    :param excluded: these languages are never returned; mutually exclusive with ``allowed``.
    """

    threshold: float = 0.0
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


_DEFAULT_LANGUAGE: Final = LanguageDetection()


def detect_language(text: str, options: LanguageDetection | None = None, /) -> LanguageMatch:
    """
    Detect the natural language a string is written in, the ``whatlang`` / ``resiliparse`` content-language successor.

    The detector finds the dominant Unicode script, then ranks the languages that share it by the similarity between
    the text's most frequent character trigrams and each language's embedded profile. It reads the visible text only;
    it does not consult an ``<html lang>`` attribute.

    :param text: the text to classify; extract it from a document first (e.g. ``node.text``).
    :param options: the detection options; defaults to :class:`LanguageDetection` (always answer, no constraints).
    :returns: the best match; its ``language`` is ``None`` when the text has no modeled script, every candidate was
        ruled out, or the confidence fell below ``threshold``.
    :raises TypeError: when ``text`` is not a ``str``.
    """
    settings = options or _DEFAULT_LANGUAGE
    language, confidence, script, name = _detect_language(text, settings.allowed, settings.excluded)
    if language is None or confidence < settings.threshold:
        return _NO_LANGUAGE
    return LanguageMatch(language, confidence, script, name)


def normalize(form: NormalizationForm, text: str, /) -> str:
    """
    Return *text* in a Unicode normalization form (UAX #15), the C successor to :func:`unicodedata.normalize`.

    The four forms pair a decomposition depth with whether the result is recomposed: ``NFD`` fully decomposes and
    canonically orders combining marks, ``NFC`` decomposes then recomposes, and the ``NFK*`` forms decompose
    compatibility equivalents too (folding ligatures, superscripts, and width variants onto their plain characters).
    ``NFC`` is what you want to compare or store user text so ``"é"`` written as a base plus a combining accent equals
    ``"é"`` written as one code point; ``NFKC`` additionally flattens presentation variants. A quick check returns
    already-normalized text (the common case) without allocating.

    :param form: the normalization form, one of ``"NFC"``, ``"NFD"``, ``"NFKC"``, ``"NFKD"``.
    :param text: the string to normalize.
    :returns: the normalized string, the same object when it was already normalized.
    :raises ValueError: when *form* is not one of the four names.
    :raises TypeError: when *text* is not a ``str``.
    """
    try:
        code = _FORMS[form]
    except KeyError:
        msg = f"invalid normalization form {form!r}"
        raise ValueError(msg) from None
    return _normalize(code, text)


def is_normalized(form: NormalizationForm, text: str, /) -> bool:
    """
    Return whether *text* is already in a Unicode normalization form, the :func:`unicodedata.is_normalized` peer.

    This decides the same question as ``normalize(form, text) == text`` but the quick check settles almost every string
    in a single scan, without building the normalized copy.

    :param form: the normalization form, one of ``"NFC"``, ``"NFD"``, ``"NFKC"``, ``"NFKD"``.
    :param text: the string to test.
    :returns: ``True`` when *text* is unchanged by :func:`normalize` for *form*.
    :raises ValueError: when *form* is not one of the four names.
    :raises TypeError: when *text* is not a ``str``.
    """
    try:
        code = _FORMS[form]
    except KeyError:
        msg = f"invalid normalization form {form!r}"
        raise ValueError(msg) from None
    return _is_normalized(code, text)
