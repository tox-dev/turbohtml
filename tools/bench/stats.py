"""
Statistics for a measurement repeated over rounds.

A robust estimate, plus enough spread to tell a real change from machine noise. A single pyperf mean is not enough to
compare two builds. Thermal drift, background load, and memory pressure move this
harness's numbers by tens of percent between rounds, which is larger than most changes worth shipping, so a lone
before/after pair can point either way on identical code. :func:`summarize` reduces repeated rounds to a median and the
dispersion around it, and :attr:`Summary.noisy` says whether that dispersion leaves room for a verdict at all. A caller
that gates on a noisy metric is reading the machine, not the change.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Sequence

# Past this the environment rivals the effect sizes worth comparing: under about 2% is excellent, up to 5% is usable,
# beyond that the spread is the machine. Metrics above it are reported and kept out of any pass/fail decision.
NOISY_CV: Final[float] = 0.05


def summarize(samples: Sequence[float]) -> Summary | None:
    # Reduce one metric's rounds to a :class:`Summary`, or ``None`` when there is nothing to summarize.
    #
    # The median is the point estimate rather than the mean or the best round: its bias does not grow with the round
    # count, so runs measured with different counts stay comparable, and it shrugs off the one-sided spikes scheduling
    # and thermal throttling add. Outliers are counted and left in, because dropping them silently biases a comparison
    # the moment the two sides drop a different number of rounds.
    if not samples:
        return None
    mean = statistics.fmean(samples)
    deviation = statistics.stdev(samples) if len(samples) > 1 else 0.0
    low, high = _tukey_fences(samples) if len(samples) > 1 else (-math.inf, math.inf)
    return Summary(
        median=statistics.median(samples),
        minimum=min(samples),
        maximum=max(samples),
        cv=0.0 if mean == 0 else deviation / abs(mean),
        outliers=sum(1 for value in samples if value < low or value > high),
        rounds=len(samples),
    )


def _tukey_fences(samples: Sequence[float]) -> tuple[float, float]:
    # Return the ``[Q1 - 1.5*IQR, Q3 + 1.5*IQR]`` fence; values outside it are the environment, not the workload.
    lower_quartile, _, upper_quartile = statistics.quantiles(samples, n=4, method="inclusive")
    spread = upper_quartile - lower_quartile
    return lower_quartile - 1.5 * spread, upper_quartile + 1.5 * spread


@dataclass(frozen=True, slots=True, kw_only=True)
class Summary:
    # One metric measured over several rounds: the estimate, its spread, and whether the spread allows a verdict.
    median: float
    minimum: float
    maximum: float
    cv: float  # sample standard deviation over the mean, so a dimensionless noise level
    outliers: int  # rounds past the 1.5*IQR fence, counted but never dropped
    rounds: int

    @property
    def noisy(self) -> bool:
        # Whether the spread is too wide to rest a comparison on.
        return self.cv > NOISY_CV


def ratio_variation(numerator_cv: float, denominator_cv: float) -> float:
    # Return the relative uncertainty of a quotient built from two independently measured numbers.
    #
    # A ratio is only as solid as the two measurements under it, and quoting a bare ``3.4x`` hides that. To first order
    # the relative uncertainties of a quotient add in quadrature, so a 4% baseline against a 3% competitor gives a ratio
    # good to 5%: enough to separate 3.4x from 2x, not enough to separate it from 3.5x.
    return math.hypot(numerator_cv, denominator_cv)


def geometric_mean(ratios: Sequence[float]) -> float | None:
    # Reduce per-workload ``candidate / base`` ratios to one number, or ``None`` when none are usable.
    #
    # The arithmetic mean of ratios depends on which side was picked as the reference, so it can call the same pair of
    # builds a win or a loss depending on the direction the table happens to run. The geometric mean does not, which is
    # why suite-level speedups are aggregated this way.
    positive = [ratio for ratio in ratios if ratio > 0]
    if not positive:
        return None
    return math.exp(statistics.fmean(math.log(ratio) for ratio in positive))


__all__ = [
    "NOISY_CV",
    "Summary",
    "geometric_mean",
    "ratio_variation",
    "summarize",
]
