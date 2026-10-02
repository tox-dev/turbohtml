"""
The super-linear scorer's verdicts against a cost model on a mocked thread clock.

Each run advances the clock by a cost that depends only on the input length, so every growth figure is exact on every
platform; the real-clock negative control lives in ``test_fuzz_amplify_cli.py`` and at the start of every lane run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest
from fuzz.amplify import Bound, Score, Shape, measure, score

if TYPE_CHECKING:
    from collections.abc import Callable

    from pytest_mock import MockerFixture

_SHAPE: Final = Shape("repeat", "ab")
_MILLISECOND: Final = 0.001


@pytest.fixture
def clock(mocker: MockerFixture) -> list[float]:
    now = [0.0]
    mocker.patch("time.thread_time", side_effect=lambda: now[0])
    return now


def _costing(clock: list[float], cost: Callable[[int], float]) -> Callable[[str], str | None]:
    def run(text: str) -> None:
        clock[0] += cost(len(text))

    return run


@pytest.mark.parametrize(
    ("cost", "growth"),
    [
        pytest.param(lambda size: (size / 1024) * _MILLISECOND, (2.0, 2.0, 2.0), id="linear"),
        pytest.param(lambda size: (size / 1024) ** 2 * _MILLISECOND, (4.0, 4.0, 4.0), id="quadratic"),
        pytest.param(lambda _: _MILLISECOND, (1.0, 1.0, 1.0), id="constant"),
    ],
)
def test_measure_growth_per_doubling(clock: list[float], cost: Callable[[int], float], growth: tuple[float]) -> None:
    assert measure(_costing(clock, cost), _SHAPE).growth == pytest.approx(growth)


def test_score_flags_a_quadratic_run(clock: list[float]) -> None:
    assert score(_costing(clock, lambda size: (size / 1024) ** 2 * _MILLISECOND), _SHAPE).super_linear is True


def test_score_passes_a_linear_run(clock: list[float]) -> None:
    assert score(_costing(clock, lambda size: (size / 1024) * _MILLISECOND), _SHAPE).super_linear is False


def test_score_passes_a_slope_that_stops_at_a_cap(clock: list[float]) -> None:
    def capped(size: int) -> float:  # quadratic up to 2 KiB, then linear: every doubling has to grow, not just one
        return (min(size, 2048) / 1024) ** 2 * (size / min(size, 2048)) * _MILLISECOND

    result = score(_costing(clock, capped), _SHAPE)
    assert (result.super_linear, result.growth) == (False, pytest.approx((4.0, 2.0, 2.0)))


def test_score_pools_retests_so_a_one_off_slope_fades(clock: list[float]) -> None:
    # a measurement returns from 8 KiB to 1 KiB once per extra round of its best of three, so the third such return
    # starts the first retest
    previous, returns = [0], [0]

    def slow_first_pass(size: int) -> float:  # quadratic in the first measurement, then 1.5x linear
        returns[0] += previous[0] == 8192 and size == 1024
        previous[0] = size
        return (size / 1024) * 1.5 * _MILLISECOND if returns[0] >= 3 else (size / 1024) ** 2 * _MILLISECOND

    # pooled per size: 1 KiB keeps the first pass's 1 ms, the rest the retest's 3, 6 and 12 ms
    result = score(_costing(clock, slow_first_pass), _SHAPE)
    assert (result.super_linear, result.growth) == (False, pytest.approx((3.0, 2.0, 2.0)))


@pytest.mark.parametrize(
    ("cost", "sizes", "super_linear"),
    [
        pytest.param(lambda _: 0.6, (1024,), True, id="first-size"),
        pytest.param(lambda size: (size / 1024) ** 3 * _MILLISECOND, (1024, 2048, 4096, 8192), True, id="cubic-at-8k"),
        pytest.param(lambda size: size / 1024 * 0.2, (1024, 2048, 4096), False, id="linear-at-4k"),
    ],
)
def test_score_stops_at_a_call_past_half_a_second(
    clock: list[float], cost: Callable[[int], float], sizes: tuple[int, ...], *, super_linear: bool
) -> None:
    result = score(_costing(clock, cost), _SHAPE)
    assert (result.too_long, result.sizes, result.super_linear) == (True, sizes, super_linear)


def test_score_spans_doublings_from_the_base(clock: list[float]) -> None:
    result = score(_costing(clock, lambda size: (size / 1024) * _MILLISECOND), _SHAPE, base=4096)
    assert result.sizes == (4096, 8192, 16384, 32768)


def test_measure_records_output_sizes(clock: list[float]) -> None:
    def tripling(text: str) -> str:
        clock[0] += _MILLISECOND
        return text * 3

    result = measure(tripling, Shape("repeat", "é"))
    assert (result.characters, result.outputs) == ((512, 1024, 2048, 4096), (1536, 3072, 6144, 12288))


@pytest.mark.parametrize("error", [pytest.param(ValueError, id="value"), pytest.param(RecursionError, id="depth")])
def test_measure_times_a_rejected_input(clock: list[float], error: type[Exception]) -> None:
    def rejecting(text: str) -> None:
        clock[0] += len(text) / 1024 * _MILLISECOND
        raise error

    result = measure(rejecting, _SHAPE)
    assert (result.outputs, result.growth) == ((None, None, None, None), pytest.approx((2.0, 2.0, 2.0)))


@pytest.mark.usefixtures("clock")
def test_measure_propagates_an_unexpected_error() -> None:
    def broken(text: str) -> None:
        raise TypeError(text[:0])

    with pytest.raises(TypeError):
        measure(broken, _SHAPE)


@pytest.mark.parametrize(
    ("growth", "super_linear"),
    [
        pytest.param((2.5, 2.5, 2.5), True, id="at-threshold"),
        pytest.param((4.0, 4.0, 4.0), True, id="quadratic"),
        pytest.param((4.0, 2.49, 4.0), False, id="one-doubling-under"),
        pytest.param((2.0, 2.0, 2.0), False, id="linear"),
    ],
)
def test_score_super_linear_needs_every_doubling(growth: tuple[float, ...], *, super_linear: bool) -> None:
    seconds = [1.0]
    for value in growth:
        seconds.append(seconds[-1] * value)
    result = Score((1024, 2048, 4096, 8192), tuple(seconds), (1024, 2048, 4096, 8192), (None,) * 4)
    assert result.super_linear is super_linear


def test_score_pooled_keeps_the_faster_sample_per_size() -> None:
    first = Score((1, 2), (3.0, 1.0), (1, 2), (None, None))
    assert first.pooled(Score((1, 2), (2.0, 5.0), (1, 2), (None, None))).seconds == (2.0, 1.0)


@pytest.mark.parametrize(
    ("output", "holds"),
    [
        pytest.param(None, True, id="raised"),
        pytest.param(30, True, id="at-bound"),
        pytest.param(31, False, id="over-bound"),
    ],
)
def test_bound_holds(output: int | None, *, holds: bool) -> None:
    assert Bound(2.0, 10).holds(10, output) is holds


@pytest.mark.parametrize(
    ("bound", "within"),
    [
        pytest.param(None, True, id="unbounded-target"),
        pytest.param(Bound(3.0, 0), True, id="every-size-fits"),
        pytest.param(Bound(1.0, 70), False, id="largest-size-overflows"),
    ],
)
def test_score_within_checks_every_size(bound: Bound | None, *, within: bool) -> None:
    result = Score((10, 20, 40), (1.0, 2.0, 4.0), (10, 20, 40), (30, 60, 120))
    assert result.within(bound) is within
