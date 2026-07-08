"""Conformance tests for the C ``_url_to_ascii`` engine behind :mod:`turbohtml.extract._urls`.

``_url_to_ascii`` runs the WHATWG domain-to-ASCII step -- Unicode IDNA ``ToASCII``, that is UTS #46 with
``Transitional_Processing=false`` and ``UseSTD3ASCIIRules=false`` -- over the Unicode 16.0.0 tables generated into
``_c/data/idna_table.h``. The authoritative oracle is the Unicode ``IdnaTestV2.txt`` vector file, read from the
``unicodetools`` submodule pinned to tag ``final-16.0-20240912`` (16.0 data lives under its ``dev`` directory); every
row's ``toAsciiN`` column is checked against the engine. A missing submodule is a setup error, not a skip: the module
raises so a dev without the checkout is told to init it (CI inits submodules). The punycode core is
pinned separately to the RFC 3492 sample strings, and the remaining branches (an ASCII fast path, an already-encoded
``xn--`` label, an unpaired surrogate, the ignored and combining-mark mapping outcomes) get their own cases.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from turbohtml._html import _url_to_ascii
from turbohtml.extract import normalize_url

_VECTORS = (
    Path(__file__).parents[2]
    / "conformance"
    / "unicodetools"
    / "unicodetools"
    / "data"
    / "idna"
    / "dev"
    / "IdnaTestV2.txt"
)
if not _VECTORS.exists():  # pragma: no cover
    _HINT = (
        "submodule tests/conformance/unicodetools not checked out; "
        "run: git submodule update --init tests/conformance/unicodetools"
    )
    raise RuntimeError(_HINT)


def _unescape(value: str) -> str:
    """Resolve the ``\\uXXXX`` and ``\\x{XXXX}`` escapes IdnaTestV2 uses for characters it would rather not display."""
    value = re.sub(r"\\u([0-9A-Fa-f]{4})", lambda match: chr(int(match[1], 16)), value)
    return re.sub(r"\\x\{([0-9A-Fa-f]+)\}", lambda match: chr(int(match[1], 16)), value)


def _has_surrogate(text: str) -> bool:
    """Whether *text* carries an unpaired surrogate, the ill-formed input the spec lets an implementation skip."""
    return any(0xD800 <= ord(char) <= 0xDFFF for char in text)


def _expected_ascii(columns: list[str], source: str) -> str:
    """The ``toAsciiN`` result for a row: an explicit value, or the toUnicode value it defers to when blank.

    turbohtml takes the stricter posture UTS #46 explicitly permits ("implementations may be more strict"): a
    punycode label that decodes to only ASCII is a step-4 (P4) validity failure -- the RUSTSEC-2024-0421 equivalence
    bypass -- so the engine keeps it verbatim instead of emitting the lenient decoded form the vector's output column
    records. That form is always the mapped (lowercased) source here, since every such row is pure ASCII.
    """
    ascii_column = columns[3]
    unicode_column = columns[1]
    decoded_unicode = _unescape(unicode_column)
    rejected = (
        not ascii_column
        and unicode_column not in {"", '""'}
        and decoded_unicode.isascii()
        and any(label.lower().startswith("xn--") and len(label) > 4 for label in source.split("."))
    )
    if rejected:
        return source.lower()
    if ascii_column:  # no row spells an empty toAsciiN as ""; an empty result always defers to the toUnicode column
        return _unescape(ascii_column)
    if unicode_column in {"", '""'}:
        return source if not unicode_column else ""
    return decoded_unicode


def _vectors() -> list[tuple[str, str]]:
    """The ``(source, expected_ascii)`` pairs of every well-formed IdnaTestV2 row, surrogate rows dropped."""
    pairs: list[tuple[str, str]] = []
    for raw in _VECTORS.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        columns = [part.strip() for part in line.split(";")]
        source = _unescape(columns[0])
        if source and _has_surrogate(source):
            continue
        pairs.append((source, _expected_ascii(columns, source)))
    return pairs


def test_idna_conformance_covers_every_wellformed_vector() -> None:
    """The engine reproduces the ``toAsciiN`` column of every well-formed Unicode IdnaTestV2 vector."""
    mismatches = [
        (source, expected, _url_to_ascii(source))
        for source, expected in _vectors()
        if _url_to_ascii(source) != expected
    ]
    assert mismatches == []


# RFC 3492 section 7.1 sample strings whose label is all lowercase (ToASCII lowercases first, so the mixed-case samples
# would encode a different label); each pins the punycode of a real script against the encoder.
_RFC_SAMPLES = {
    "ليهمابتكلموشعربي؟": "xn--egbpdaj6bu4bxfgehfvwxn",
    "他们为什么不说中文": "xn--ihqwcrb4cv8a8dqg056pqjye",
    "hello": "hello",
}


@pytest.mark.parametrize(
    ("label", "encoded"),
    [pytest.param(label, encoded, id=encoded) for label, encoded in _RFC_SAMPLES.items()],
)
def test_rfc3492_sample_labels_encode_to_their_punycode(label: str, encoded: str) -> None:
    """A single-label host encodes to the RFC 3492 punycode, and re-running the engine on it is idempotent."""
    assert _url_to_ascii(label) == encoded
    assert _url_to_ascii(encoded) == encoded


@pytest.mark.parametrize(
    ("host", "ascii_host"),
    [
        pytest.param("example.com", "example.com", id="ascii-passthrough"),
        pytest.param("Example.COM", "example.com", id="ascii-uppercase-mapped"),
        pytest.param("münchen.de", "xn--mnchen-3ya.de", id="german-umlaut"),
        pytest.param("faß.de", "xn--fa-hia.de", id="ess-zett-nontransitional"),
        pytest.param("xn--fa-hia.de", "xn--fa-hia.de", id="already-xn--"),
        pytest.param("日本語。jp", "xn--wgv71a119e.jp", id="ideographic-full-stop"),
        pytest.param("☃.net", "xn--n3h.net", id="mixed-symbol-and-ascii"),
        pytest.param("bar.☁.example", "bar.xn--l3h.example", id="middle-unicode-label"),
        pytest.param("", "", id="empty"),
    ],
)
def test_domain_to_ascii(host: str, ascii_host: str) -> None:
    """Representative registered names map to the ASCII form the URL standard's host parser produces."""
    assert _url_to_ascii(host) == ascii_host


def test_ess_zett_diverges_from_the_idna2003_codec() -> None:
    """UTS #46 keeps ess-zett (``faß`` -> ``xn--fa-hia``); the old IDNA-2003 codec folded it to ``fass``."""
    assert _url_to_ascii("faß.de") == "xn--fa-hia.de"
    assert "faß.de".encode("idna").decode("ascii") == "fass.de"


def test_soft_hyphen_is_ignored() -> None:
    """A U+00AD SOFT HYPHEN is an ignored code point, dropped before the label is even considered non-ASCII."""
    assert _url_to_ascii("ex­ample.com") == "example.com"


def test_decomposed_label_composes_to_the_precomposed_form() -> None:
    """A base plus a combining acute (U+0301) normalizes to the precomposed a-acute (U+00E1) label."""
    assert _url_to_ascii("a\u0301.com") == "xn--1ca.com"
    assert _url_to_ascii("\u00e1.com") == "xn--1ca.com"


def test_combining_marks_are_canonically_reordered() -> None:
    """Marks out of canonical order (acute U+0301 before dot-below U+0323) sort by class into one NFC label."""
    assert _url_to_ascii("q\u0301\u0323.com") == _url_to_ascii("q\u0323\u0301.com")
    assert _url_to_ascii("q\u0301\u0323.com") == "xn--q-xbb5h.com"


def test_hangul_jamo_sequence_composes_to_a_syllable() -> None:
    """Leading, vowel, and trailing jamo compose arithmetically to the precomposed syllable's host."""
    assert _url_to_ascii("\u1100\u1161\u11a8.kr") == _url_to_ascii("\uac01.kr")
    assert _url_to_ascii("\u1100\u1161.kr") == _url_to_ascii("\uac00.kr")
    assert _url_to_ascii("\u1100\u1161\u11a8.kr") == "xn--p39a.kr"


@pytest.mark.parametrize(
    "label",
    [
        pytest.param("xn--b", id="truncated-integer"),
        pytest.param("xn---", id="lone-delimiter"),
        pytest.param("xn--@a", id="non-digit-below-a"),
        pytest.param("xn--a}", id="non-digit-above-z"),
        pytest.param("xn--to913369t", id="integer-value-overflow"),
        pytest.param("xn--xf26gk7zx", id="code-point-out-of-range"),
        pytest.param("xn--tl0cub1", id="decodes-to-a-surrogate"),
    ],
)
def test_malformed_xn_label_is_kept_verbatim(label: str) -> None:
    """An ``xn--`` label the punycode decoder rejects is left untouched (an advisory error the cleaner ignores)."""
    assert _url_to_ascii(f"{label}.example") == f"{label}.example"


# A label whose punycode delta would overflow the 32-bit accumulator: a long run of one code point, then a distant one.
# The first case trips the pre-multiply guard, the second lands the accumulator just under the limit so the following
# increments trip the per-code-point guard.
_OVERFLOW_LABELS = ["\u3400" * 2000 + "\U0010ffff", "\u3400" * 2000 + "\U00109436"]


@pytest.mark.parametrize("label", _OVERFLOW_LABELS, ids=["pre-multiply-guard", "increment-guard"])
def test_punycode_delta_overflow_is_rejected(label: str) -> None:
    """A label whose encoding would overflow the punycode accumulator raises rather than emit a wrong host."""
    with pytest.raises(ValueError, match="cannot be encoded to ASCII"):
        _url_to_ascii(f"{label}.example")


@pytest.mark.parametrize(
    ("first", "second"),
    [
        pytest.param("\u1100", "\u1100", id="leading-then-leading"),
        pytest.param("\uac01", "\u11a8", id="lvt-syllable-then-trailing"),
        pytest.param("\uac00", "\u0301", id="lv-syllable-then-non-trailing"),
    ],
)
def test_non_composing_jamo_pairs_stay_separate(first: str, second: str) -> None:
    """A jamo pair outside the composable ranges is not fused; the host still round-trips through the engine."""
    encoded = _url_to_ascii(f"{first}{second}.kr")
    assert encoded.startswith("xn--")
    assert _url_to_ascii(encoded) == encoded


@pytest.mark.parametrize(
    "host",
    [
        pytest.param("xn.example", id="too-short-for-xn"),
        pytest.param("xtra.example", id="second-char-not-n"),
        pytest.param("xnat.example", id="third-char-not-hyphen"),
        pytest.param("xn-a.example", id="fourth-char-not-hyphen"),
    ],
)
def test_ascii_label_that_only_resembles_xn_passes_through(host: str) -> None:
    """An ASCII label that is not the four-code-point ``xn--`` prefix is copied verbatim, never decoded."""
    assert _url_to_ascii(host) == host


def test_unpaired_surrogate_host_is_rejected() -> None:
    """A label carrying an unpaired surrogate has no scalar value to encode, so the engine raises ``ValueError``."""
    with pytest.raises(ValueError, match="cannot be encoded to ASCII"):
        _url_to_ascii("a\ud800b.example")


def test_normalize_url_punycodes_a_unicode_host() -> None:
    """The public normalizer routes a Unicode host through the engine and falls back on an unencodable one."""
    assert normalize_url("http://münchen.de/weg") == "http://xn--mnchen-3ya.de/weg"
    assert normalize_url("http://a\ud800b.de/") == "http://a\ud800b.de/"


@pytest.mark.parametrize(
    "label",
    [
        pytest.param("xn--abc-", id="basic-run-then-delimiter"),
        pytest.param("xn--ascii-", id="whole-label-basic"),
        pytest.param("xn--0900-", id="digits-only-basic"),
        pytest.param("xn--a-", id="single-char-basic"),
    ],
)
def test_punycode_label_decoding_to_only_ascii_is_kept_verbatim(label: str) -> None:
    """A punycode label that decodes to no non-ASCII code point is invalid (RUSTSEC-2024-0421 / CVE-2024-12224); the
    engine keeps it verbatim rather than emit the bare-ASCII decoding that would forge an IDNA equivalence."""
    assert _url_to_ascii(f"{label}.example") == f"{label}.example"


def test_all_ascii_decode_does_not_forge_an_idna_equivalence() -> None:
    """The RUSTSEC-2024-0421 bypass: ``xn--ascii-`` beside a real IDN label must not canonicalize to the host its
    bare-ASCII decoding would, or a raw-vs-normalized host check is defeated."""
    forged = _url_to_ascii("xn--ascii-.日本")
    genuine = _url_to_ascii("ascii.日本")
    assert forged != genuine
    assert forged == "xn--ascii-.xn--wgv71a"


def test_empty_punycode_label_decodes_to_an_empty_label() -> None:
    """A bare ``xn--`` decodes to the empty string, the one all-ASCII decode the conformance vectors keep (an empty
    label cannot forge a host), so it maps to ``""`` rather than being held verbatim."""
    assert not _url_to_ascii("xn--")
    assert _url_to_ascii("xn--.example") == ".example"


# A punycode payload of many high-value base-36 digits: the decode accumulator ``i += digit * w`` and its weight
# ``w *= base - t`` must fail on the RFC 3492 overflow bound (Libidn2 CVE-2017-14062) rather than wrap and mis-decode.
@pytest.mark.parametrize(
    "label",
    [
        pytest.param("xn--" + "z" * 64, id="long-high-digit-run"),
        pytest.param("xn--a-" + "9" * 64, id="basic-run-then-high-digits"),
        pytest.param("xn--99999999999", id="all-digit-accumulator"),
    ],
)
def test_punycode_decode_accumulator_overflow_is_rejected(label: str) -> None:
    """A crafted digit run that would overflow the punycode decode accumulator is rejected, leaving the label
    verbatim, never a wrapped-around host."""
    assert _url_to_ascii(f"{label}.example") == f"{label}.example"


@pytest.mark.parametrize(
    "label",
    [
        pytest.param("é" * 512, id="max-expansion-non-ascii"),
        pytest.param("a" * 512 + "é", id="long-basic-run-with-tail"),
        pytest.param("xn--" + "a" * 512 + "-", id="long-basic-punycode-run"),
    ],
)
def test_boundary_length_label_round_trips_without_overrun(label: str) -> None:
    """A label near the buffer-sizing bounds encodes (or is kept verbatim) without an out-of-bounds write, and the
    result is idempotent under a second pass -- the check ASan exercises for the OpenSSL CVE-2022-3602 write class."""
    encoded = _url_to_ascii(f"{label}.example")
    assert encoded.endswith(".example")
    assert _url_to_ascii(encoded) == encoded
