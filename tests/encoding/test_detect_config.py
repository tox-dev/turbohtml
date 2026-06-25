"""The :class:`turbohtml.detect.Detection` config: validation, the chardet preset, and the candidate filters."""

from __future__ import annotations

import pytest

from turbohtml.detect import Detection, detect, detect_all

_CYRILLIC = "Привет мир как дела сегодня хорошо".encode("windows-1251")


def test_default_config_filters_nothing() -> None:
    assert Detection()._unpack() == {}
    assert detect_all(_CYRILLIC) == detect_all(_CYRILLIC, Detection())


def test_unpack_emits_only_non_default_fields() -> None:
    assert Detection(language="Russian")._unpack() == {"language": "Russian"}
    assert Detection(minimum_confidence=0.2)._unpack() == {"minimum_confidence": 0.2}


def test_chardet_preset_applies_the_020_floor() -> None:
    assert Detection.chardet() == Detection(minimum_confidence=0.20)


@pytest.mark.parametrize(
    "config",
    [
        pytest.param({"minimum_confidence": 1.5}, id="confidence-too-high"),
        pytest.param({"minimum_confidence": -0.1}, id="confidence-too-low"),
        pytest.param({"language": "Klingon"}, id="unknown-language"),
        pytest.param({"allowed": frozenset({"NOT-AN-ENCODING"})}, id="unknown-allowed"),
        pytest.param({"excluded": frozenset({"NOT-AN-ENCODING"})}, id="unknown-excluded"),
        pytest.param({"allowed": frozenset({"UTF-8"}), "excluded": frozenset({"UTF-8"})}, id="allow-deny-overlap"),
    ],
)
def test_invalid_config_is_rejected(config: dict[str, object]) -> None:
    with pytest.raises(ValueError, match=r"confidence|language|encoding"):
        Detection(**config)  # ty: ignore[invalid-argument-type]


def test_a_fully_specified_config_is_accepted() -> None:
    config = Detection(
        minimum_confidence=0.5,
        language="Russian",
        allowed=frozenset({"windows-1251"}),
        excluded=frozenset({"UTF-8"}),
    )
    assert config.minimum_confidence == pytest.approx(0.5)


def test_allowed_keeps_only_listed_encodings() -> None:
    matches = detect_all(_CYRILLIC, Detection(allowed=frozenset({"windows-1251"})))
    assert [match.encoding for match in matches] == ["windows-1251"]


def test_allowed_can_surface_a_lower_ranked_candidate() -> None:
    matches = detect_all(_CYRILLIC, Detection(allowed=frozenset({"windows-1252"})))
    assert [match.encoding for match in matches] == ["windows-1252"]


def test_excluded_drops_the_listed_encoding() -> None:
    matches = detect_all(_CYRILLIC, Detection(excluded=frozenset({"windows-1252"})))
    assert "windows-1252" not in {match.encoding for match in matches}
    assert "windows-1251" in {match.encoding for match in matches}


def test_language_hint_keeps_only_that_language() -> None:
    matches = detect_all(_CYRILLIC, Detection(language="Russian"))
    assert {match.language for match in matches} == {"Russian"}


def test_minimum_confidence_drops_weak_candidates() -> None:
    matches = detect_all(_CYRILLIC, Detection(minimum_confidence=0.7))
    assert all(match.confidence >= 0.7 for match in matches)
    assert "windows-1252" not in {match.encoding for match in matches}


def test_filtering_everything_out_detects_nothing() -> None:
    match = detect(_CYRILLIC, Detection(minimum_confidence=0.99))
    assert match.encoding is None
