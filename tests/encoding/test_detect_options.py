"""The frozen Detection config: threshold, language hint, allowed/excluded encodings, and the chardet preset."""

from __future__ import annotations

import pytest

from turbohtml.detect import Detection, EncodingMatch, detect, detect_all

_NO_MATCH = EncodingMatch(None, 0.0, None)
_RUSSIAN_1251 = "Привет мир, как дела".encode("cp1251")


@pytest.mark.parametrize(
    "threshold",
    [
        pytest.param(-0.1, id="below-zero"),
        pytest.param(1.1, id="above-one"),
    ],
)
def test_out_of_range_threshold_is_rejected(threshold: float) -> None:
    with pytest.raises(ValueError, match=r"threshold must be within \[0\.0, 1\.0\]"):
        Detection(threshold=threshold)


def test_allowed_and_excluded_are_mutually_exclusive() -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        Detection(allowed=frozenset({"utf-8"}), excluded=frozenset({"gbk"}))


def test_chardet_preset_mirrors_the_minimum_threshold() -> None:
    assert Detection.chardet() == Detection(threshold=0.2)


def test_chardet_preset_drops_the_no_evidence_fallback() -> None:
    assert detect(b"\x81\n", Detection.chardet()) == _NO_MATCH


def test_chardet_preset_keeps_a_confident_result() -> None:
    assert detect(_RUSSIAN_1251, Detection.chardet()).encoding == "windows-1251"


def test_threshold_filters_detect_all() -> None:
    kept = detect_all(_RUSSIAN_1251, Detection(threshold=0.5))
    assert kept == [match for match in detect_all(_RUSSIAN_1251) if match.confidence >= 0.5]
    assert len(kept) == 1


def test_allowed_restricts_the_winner() -> None:
    assert detect(_RUSSIAN_1251, Detection(allowed=frozenset({"KOI8-U", "IBM866"}))).encoding == "KOI8-U"


def test_allowed_matches_names_case_insensitively() -> None:
    assert detect(_RUSSIAN_1251, Detection(allowed=frozenset({"koi8-u"}))).encoding == "KOI8-U"


def test_allowed_ruling_every_candidate_out_yields_no_match() -> None:
    assert detect(_RUSSIAN_1251, Detection(allowed=frozenset({"UTF-8"}))) == _NO_MATCH


def test_excluded_promotes_the_runner_up() -> None:
    runner_up = detect_all(_RUSSIAN_1251)[1]
    assert detect(_RUSSIAN_1251, Detection(excluded=frozenset({"windows-1251"}))) == runner_up


def test_excluding_a_certain_result_yields_no_match() -> None:
    assert detect(b"\xef\xbb\xbfx", Detection(excluded=frozenset({"utf-8"}))) == _NO_MATCH


def test_language_hint_prefers_the_matching_model() -> None:
    match = detect(_RUSSIAN_1251, Detection(language="Hebrew"))
    assert match.encoding == "windows-1255"
    assert match.language == "Hebrew"


def test_language_hint_without_positive_evidence_changes_nothing() -> None:
    assert detect(_RUSSIAN_1251, Detection(language="Thai")) == detect(_RUSSIAN_1251)
