from __future__ import annotations

import pytest

from turbohtml._html import _detect_rank

# One detector result: (winner, certain, [(name, score)], had_bom). The ranker shapes and filters it.
_SCORED = ("windows-1251", False, [("windows-1251", 60), ("koi8-r", 40)], False)
_LANGUAGES = {"windows-1251": "ru", "koi8-r": "ru", "windows-1252": "en"}


def test_a_certain_result_is_one_candidate() -> None:
    rows = _detect_rank(("utf-8", True, [], True), None, (), None, 0.0, _LANGUAGES)
    assert rows == [("utf-8", 1.0, None, True, "whatwg-utf-8")]


def test_no_winner_takes_the_windows_1252_fallback() -> None:
    rows = _detect_rank((None, False, [], False), None, (), None, 0.0, _LANGUAGES)
    assert rows == [("windows-1252", 1.0, "en", False, "whatwg-windows-1252")]


def test_scores_become_shares_of_the_positive_total() -> None:
    rows = _detect_rank(_SCORED, None, (), None, 0.0, _LANGUAGES)
    assert [(row[0], round(row[1], 2)) for row in rows] == [("windows-1251", 0.6), ("koi8-r", 0.4)]


def test_the_winner_leads_even_when_it_scored_lower() -> None:
    result = ("koi8-r", False, [("windows-1251", 60), ("koi8-r", 40)], False)
    assert [row[0] for row in _detect_rank(result, None, (), None, 0.0, _LANGUAGES)] == ["koi8-r", "windows-1251"]


def test_a_winner_that_never_scored_leads_at_zero() -> None:
    result = ("windows-1252", False, [("windows-1251", 60)], False)
    rows = _detect_rank(result, None, (), None, 0.0, _LANGUAGES)
    assert [(row[0], row[1]) for row in rows] == [("windows-1252", 0.0), ("windows-1251", 1.0)]


def test_one_encoding_scored_twice_keeps_its_better_score() -> None:
    result = ("windows-1251", False, [("windows-1251", 10), ("koi8-r", 40), ("windows-1251", 60)], False)
    rows = _detect_rank(result, None, (), None, 0.0, _LANGUAGES)
    assert [(row[0], round(row[1], 2)) for row in rows] == [("windows-1251", 0.6), ("koi8-r", 0.4)]


def test_a_non_positive_score_reads_as_zero_confidence() -> None:
    result = ("windows-1251", False, [("windows-1251", 60), ("koi8-r", 0)], False)
    assert [row[1] for row in _detect_rank(result, None, (), None, 0.0, _LANGUAGES)] == [1.0, 0.0]


def test_the_allowlist_drops_everything_else() -> None:
    rows = _detect_rank(_SCORED, ("KOI8-R",), (), None, 0.0, _LANGUAGES)
    assert [row[0] for row in rows] == ["koi8-r"]


_NO_MATCH = (None, 0.0, None, False, None)


def test_an_empty_allowlist_leaves_the_no_match_row() -> None:
    assert _detect_rank(_SCORED, (), (), None, 0.0, _LANGUAGES) == [_NO_MATCH]


def test_no_result_is_the_no_match_row() -> None:
    assert _detect_rank(None, None, (), None, 0.0, _LANGUAGES) == [_NO_MATCH]


def test_the_exclusions_drop_their_own() -> None:
    rows = _detect_rank(_SCORED, None, ("Windows-1251",), None, 0.0, _LANGUAGES)
    assert [row[0] for row in rows] == ["koi8-r"]


def test_the_threshold_drops_the_weak() -> None:
    rows = _detect_rank(_SCORED, None, (), None, 0.5, _LANGUAGES)
    assert [row[0] for row in rows] == ["windows-1251"]


def test_a_language_hint_floats_its_encodings_first() -> None:
    result = ("windows-1252", False, [("windows-1252", 60), ("koi8-r", 40)], False)
    rows = _detect_rank(result, None, (), "ru", 0.0, _LANGUAGES)
    assert [row[0] for row in rows] == ["koi8-r", "windows-1252"]


def test_a_language_hint_leaves_a_zero_confidence_candidate_behind() -> None:
    # a candidate the detector scored at zero carries no evidence, so the hint cannot promote it
    result = ("windows-1252", False, [("windows-1252", 60), ("koi8-r", 0)], False)
    rows = _detect_rank(result, None, (), "ru", 0.0, _LANGUAGES)
    assert [row[0] for row in rows] == ["windows-1252", "koi8-r"]


def test_a_language_hint_leaves_an_encoding_naming_no_language_behind() -> None:
    # utf-8 names no language, so the hint has nothing to compare it against and it cannot be promoted
    result = ("utf-8", False, [("utf-8", 60), ("koi8-r", 40)], False)
    rows = _detect_rank(result, None, (), "ru", 0.0, _LANGUAGES)
    assert [row[0] for row in rows] == ["koi8-r", "utf-8"]


def test_a_language_hint_no_encoding_claims_keeps_the_order() -> None:
    rows = _detect_rank(_SCORED, None, (), "zz", 0.0, _LANGUAGES)
    assert [row[0] for row in rows] == ["windows-1251", "koi8-r"]


def test_an_encoding_naming_no_language_reports_none() -> None:
    rows = _detect_rank(("utf-8", True, [], False), None, (), None, 0.0, _LANGUAGES)
    assert rows[0][2] is None


@pytest.mark.parametrize(
    "args",
    [
        pytest.param(("notatuple", None, (), None, 0.0, _LANGUAGES), id="result-is-not-a-tuple"),
        pytest.param((_SCORED, None, (), None, 0.0, "notadict"), id="languages-is-not-a-dict"),
        pytest.param((_SCORED, None, (), None, "notafloat", _LANGUAGES), id="threshold-is-not-a-number"),
        pytest.param((_SCORED, None, ()), id="too-few-arguments"),
    ],
)
def test_the_ranker_rejects_bad_arguments(args: tuple[object, ...]) -> None:
    with pytest.raises(TypeError):
        _detect_rank(*args)  # ty: ignore[invalid-argument-type]  # the argument check is the point
