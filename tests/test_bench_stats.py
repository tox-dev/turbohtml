from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "tools"))

from bench.stats import (  # ruff:ignore[module-import-not-at-top-of-file]
    NOISY_CV,
    Summary,
    geometric_mean,
    ratio_variation,
    summarize,
)


def test_summarize_of_nothing_is_none() -> None:
    # A metric with no rounds has no summary to report.
    assert summarize([]) is None


def test_median_is_the_estimate_not_the_mean() -> None:
    # One scheduling spike must not drag the estimate the way it drags the mean.
    assert _summary([10.0, 10.0, 10.0, 10.0, 100.0]).median == pytest.approx(10.0)


def _summary(samples: list[float]) -> Summary:
    # Summarize rounds that are known to exist, narrowing away the None the empty case returns.
    summary = summarize(samples)
    assert summary is not None
    return summary


def test_median_ignores_round_count() -> None:
    # Runs measured with different round counts stay comparable, which best-of-n would not.
    assert _summary([10.0, 12.0, 14.0]).median == pytest.approx(_summary([10.0, 12.0, 14.0] * 3).median)


@pytest.mark.parametrize(
    ("samples", "expected"),
    [
        pytest.param([100.0, 100.0, 100.0], False, id="identical-rounds-are-clean"),
        pytest.param([100.0, 101.0, 99.0], False, id="one-percent-spread-is-usable"),
        pytest.param([100.0, 140.0, 80.0], True, id="wide-spread-is-noisy"),
    ],
)
def test_noisy_flags_only_wide_spread(samples: list[float], *, expected: bool) -> None:
    # The noise gauge separates a usable measurement from one the machine dominates.
    assert _summary(samples).noisy is expected


def test_cv_is_dimensionless() -> None:
    # Scaling every round leaves the noise level unchanged, so the threshold means the same at any magnitude.
    small = _summary([1.0, 2.0, 3.0])
    large = _summary([1000.0, 2000.0, 3000.0])
    assert small.cv == pytest.approx(large.cv)


def test_outliers_are_counted_but_kept() -> None:
    # A spike is reported and still counted in the rounds, since dropping it would bias an A/B.
    summary = _summary([10.0, 10.1, 10.2, 10.3, 500.0])
    assert (summary.outliers, summary.rounds) == (1, 5)


def test_geometric_mean_of_nothing_is_none() -> None:
    # No usable ratio leaves no aggregate to report.
    assert geometric_mean([]) is None


def test_geometric_mean_is_reference_invariant() -> None:
    # Inverting every ratio inverts the aggregate, so neither build is favored by table direction.
    ratios = [0.5, 2.0, 1.25]
    assert _aggregate(ratios) == pytest.approx(1 / _aggregate([1 / ratio for ratio in ratios]))


def _aggregate(ratios: list[float]) -> float:
    # Aggregate ratios that are known usable, narrowing away the None the empty case returns.
    aggregate = geometric_mean(ratios)
    assert aggregate is not None
    return aggregate


def test_noisy_threshold_matches_the_documented_level() -> None:
    # The gate's cutoff is the one the docstring names, so prose and behavior cannot drift apart.
    assert pytest.approx(0.05) == NOISY_CV


def test_ratio_uncertainty_exceeds_each_input() -> None:
    # A ratio is never firmer than the measurements under it, so its spread outgrows either side alone.
    assert ratio_variation(0.04, 0.03) > 0.04


def test_ratio_uncertainty_adds_in_quadrature() -> None:
    # Independent errors combine in quadrature, not linearly, so 4% over 3% is 5% and not 7%.
    assert ratio_variation(0.04, 0.03) == pytest.approx(0.05)


def test_exact_denominator_leaves_the_numerator_spread() -> None:
    # Dividing by a number with no spread cannot add any, so the ratio inherits the measured side.
    assert ratio_variation(0.06, 0.0) == pytest.approx(0.06)
