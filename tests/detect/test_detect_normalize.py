"""normalize()/is_normalized() (issue #543): the four Unicode normalization forms in C, pinned to Unicode 16.0.0."""

# slash, bare combining marks); driving those exact characters is the whole point of the suite.

from __future__ import annotations

import unicodedata
from itertools import product

import pytest

from turbohtml.detect import NormalizationForm, is_normalized, normalize

_FORMS: tuple[NormalizationForm, ...] = ("NFC", "NFD", "NFKC", "NFKD")

# Fixed (source, NFC, NFD, NFKC, NFKD) vectors at Unicode 16.0.0, the version turbohtml's tables pin. They hold on every
# interpreter regardless of its own Unicode version, and drive every arm of the C engine: canonical decomposition and
# recomposition (e-acute both ways), a singleton (OHM -> OMEGA, ANGSTROM -> A-ring), compatibility decompositions
# (ligatures, superscript, fraction, Roman numeral, math bold, halfwidth katakana), a composition-excluded precomposed
# character (U+0958, U+0344), cross-class mark reordering, and every Hangul shape (L/V/T jamo, LV and LVT syllables, an
# LVT that gains no trailer, an LV that gains no vowel), plus an astral start and a code point past every table range.
_VECTORS = [
    ("abc", "abc", "abc", "abc", "abc"),
    ("caf\u00e9", "caf\u00e9", "cafe\u0301", "caf\u00e9", "cafe\u0301"),
    ("\u00e9", "\u00e9", "e\u0301", "\u00e9", "e\u0301"),
    ("e\u0301", "\u00e9", "e\u0301", "\u00e9", "e\u0301"),
    ("\u00c5", "\u00c5", "A\u030a", "\u00c5", "A\u030a"),
    ("\u212b", "\u00c5", "A\u030a", "\u00c5", "A\u030a"),
    ("\u2126", "\u03a9", "\u03a9", "\u03a9", "\u03a9"),
    ("\u01ed", "\u01ed", "o\u0328\u0304", "\u01ed", "o\u0328\u0304"),
    ("\ufb01", "\ufb01", "\ufb01", "fi", "fi"),
    ("\ufb03", "\ufb03", "\ufb03", "ffi", "ffi"),
    ("\u00b2", "\u00b2", "\u00b2", "2", "2"),
    ("\u00bd", "\u00bd", "\u00bd", "1\u20442", "1\u20442"),
    ("\u2160", "\u2160", "\u2160", "I", "I"),
    ("\U0001d400", "\U0001d400", "\U0001d400", "A", "A"),
    ("\uff76", "\uff76", "\uff76", "\u30ab", "\u30ab"),
    ("\u0958", "\u0915\u093c", "\u0915\u093c", "\u0915\u093c", "\u0915\u093c"),
    ("\u0344", "\u0308\u0301", "\u0308\u0301", "\u0308\u0301", "\u0308\u0301"),
    ("\u1e0d\u0307", "\u1e0d\u0307", "d\u0323\u0307", "\u1e0d\u0307", "d\u0323\u0307"),
    ("q\u0323\u0307", "q\u0323\u0307", "q\u0323\u0307", "q\u0323\u0307", "q\u0323\u0307"),
    ("a\u0301\u0316", "\u00e1\u0316", "a\u0316\u0301", "\u00e1\u0316", "a\u0316\u0301"),
    ("a\u0316\u0301", "\u00e1\u0316", "a\u0316\u0301", "\u00e1\u0316", "a\u0316\u0301"),
    ("\uac00", "\uac00", "\u1100\u1161", "\uac00", "\u1100\u1161"),
    ("\uac01", "\uac01", "\u1100\u1161\u11a8", "\uac01", "\u1100\u1161\u11a8"),
    ("\u1100\u1161", "\uac00", "\u1100\u1161", "\uac00", "\u1100\u1161"),
    ("\uac00\u11a8", "\uac01", "\u1100\u1161\u11a8", "\uac01", "\u1100\u1161\u11a8"),
    ("\u1100\u1161\u11a8", "\uac01", "\u1100\u1161\u11a8", "\uac01", "\u1100\u1161\u11a8"),
    ("\uac01\u11a8", "\uac01\u11a8", "\u1100\u1161\u11a8\u11a8", "\uac01\u11a8", "\u1100\u1161\u11a8\u11a8"),
    ("\uac00\u1161", "\uac00\u1161", "\u1100\u1161\u1161", "\uac00\u1161", "\u1100\u1161\u1161"),
    ("\U0001d400\u0301", "\U0001d400\u0301", "\U0001d400\u0301", "\u00c1", "A\u0301"),
    ("\u1e9b\u0323", "\u1e9b\u0323", "\u017f\u0323\u0307", "\u1e69", "s\u0323\u0307"),
    ("", "", "", "", ""),
    ("\U0010ffff", "\U0010ffff", "\U0010ffff", "\U0010ffff", "\U0010ffff"),
    ("\u1fef", "`", "`", "`", "`"),
    ("\u03d3", "\u03d3", "\u03d2\u0301", "\u038e", "\u03a5\u0301"),
]

# Combining sequences whose exact output shifts across Unicode versions but whose invariants do not, so they validate
# the engine's reordering, composition, and blocking on every interpreter. The empty prefix yields mark-leading
# sequences, so a lower-class mark can bubble to the front during reordering.
_STARTERS = ("", "a", "e", "\u00c5", "\u1100", "\uac00", "\u2126", "\ufb01", "o")
_MARKS = ("\u0301", "\u0316", "\u0323", "\u0308", "\u1161", "\u11a8", "\u0f71", "\u093c", "\u0344")

_CODEPOINTS = [cp for cp in range(0x110000) if not 0xD800 <= cp <= 0xDFFF]


@pytest.mark.parametrize(
    ("source", "nfc", "nfd", "nfkc", "nfkd"),
    [pytest.param(*row, id=ascii(row[0])[:24]) for row in _VECTORS],
)
def test_fixed_vectors_at_unicode_16(source: str, nfc: str, nfd: str, nfkc: str, nfkd: str) -> None:
    assert (
        normalize("NFC", source),
        normalize("NFD", source),
        normalize("NFKC", source),
        normalize("NFKD", source),
    ) == (nfc, nfd, nfkc, nfkd)


@pytest.mark.parametrize("form", _FORMS)
def test_normalization_is_idempotent_over_the_whole_range(form: NormalizationForm) -> None:
    unstable = [cp for cp in _CODEPOINTS if (once := normalize(form, chr(cp))) != normalize(form, once)]
    assert unstable == []


@pytest.mark.parametrize("form", _FORMS)
def test_is_normalized_equals_the_unchanged_test_over_the_whole_range(form: NormalizationForm) -> None:
    wrong = [cp for cp in _CODEPOINTS if is_normalized(form, chr(cp)) is not (normalize(form, chr(cp)) == chr(cp))]
    assert wrong == []


@pytest.mark.parametrize(
    ("compose", "decompose"),
    [("NFC", "NFD"), ("NFKC", "NFKD")],
)
def test_compose_equals_composing_the_decomposition_over_the_whole_range(
    compose: NormalizationForm, decompose: NormalizationForm
) -> None:
    off = [cp for cp in _CODEPOINTS if normalize(compose, chr(cp)) != normalize(compose, normalize(decompose, chr(cp)))]
    assert off == []


@pytest.mark.parametrize("form", _FORMS)
def test_two_mark_sequences_hold_the_invariants(form: NormalizationForm) -> None:
    corpus = [start + first + second for start in _STARTERS for first, second in product(_MARKS, repeat=2)]
    unstable = [text for text in corpus if (once := normalize(form, text)) != normalize(form, once)]
    inconsistent = [text for text in corpus if is_normalized(form, text) is not (normalize(form, text) == text)]
    assert (unstable, inconsistent) == ([], [])


@pytest.mark.parametrize("form", _FORMS)
def test_three_mark_sequences_are_idempotent(form: NormalizationForm) -> None:
    corpus = ["a" + "".join(combo) for combo in product(_MARKS, repeat=3)]
    unstable = [text for text in corpus if (once := normalize(form, text)) != normalize(form, once)]
    assert unstable == []


def test_already_normalized_returns_the_same_object() -> None:
    text = "the quick brown fox"
    assert normalize("NFC", text) is text


@pytest.mark.parametrize(
    ("form", "text"),
    [
        pytest.param("NFC", "\u0301", id="maybe-lone-mark"),
        pytest.param("NFC", "abc", id="yes-ascii"),
    ],
)
def test_normalized_inputs_report_true(form: NormalizationForm, text: str) -> None:
    assert is_normalized(form, text) is True


@pytest.mark.parametrize(
    ("form", "text"),
    [
        pytest.param("NFC", "e\u0301", id="maybe-decomposed"),
        pytest.param("NFD", "\u00e9", id="no-precomposed"),
        pytest.param("NFD", "a\u0301\u0316", id="out-of-order"),
    ],
)
def test_unnormalized_inputs_report_false(form: NormalizationForm, text: str) -> None:
    assert is_normalized(form, text) is False


@pytest.mark.parametrize("bad", ["nfc", "NFKC ", "", "NFE", "nfd"])
def test_invalid_form_raises_value_error(bad: str) -> None:
    with pytest.raises(ValueError, match="invalid normalization form"):
        normalize(bad, "x")  # ty: ignore[invalid-argument-type]  # exercises the form guard
    with pytest.raises(ValueError, match="invalid normalization form"):
        is_normalized(bad, "x")  # ty: ignore[invalid-argument-type]  # exercises the form guard


def test_non_string_text_raises_type_error() -> None:
    with pytest.raises(TypeError):
        normalize("NFC", 123)  # ty: ignore[invalid-argument-type]  # exercises the C str guard
    with pytest.raises(TypeError):
        is_normalized("NFC", b"bytes")  # ty: ignore[invalid-argument-type]  # exercises the C str guard


# Extra cross-check against the running interpreter's unicodedata. It runs only where that interpreter's Unicode matches
# the pinned tables (3.14+ ships 16.0.0); on an older interpreter the comparison would flag legitimate version
# differences, so the test is skipped there and its body cannot be measured -- hence the coverage exclusion.
@pytest.mark.skipif(
    unicodedata.unidata_version != "16.0.0", reason="runtime unicodedata differs from the pinned Unicode 16.0.0 tables"
)
@pytest.mark.parametrize("form", _FORMS)
def test_matches_runtime_unicodedata(form: NormalizationForm) -> None:  # pragma: no cover
    mismatches = [
        cp
        for cp in _CODEPOINTS
        if normalize(form, chr(cp)) != unicodedata.normalize(form, chr(cp))
        or is_normalized(form, chr(cp)) is not unicodedata.is_normalized(form, chr(cp))
    ]
    assert mismatches == []
