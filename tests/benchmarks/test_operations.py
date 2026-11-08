from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest
from bench.ci import benchmarks
from bench.timing import Mutating

if TYPE_CHECKING:
    from collections.abc import Callable

    from bench.operations import Case
    from pytest_codspeed import BenchmarkFixture


@pytest.mark.parametrize(
    ("operation", "load"),
    [pytest.param(run, load, id=name) for name, run, load in benchmarks() if not isinstance(run, Mutating)],
)
def test_feature(benchmark: BenchmarkFixture, operation: Callable[[Case], object], load: Callable[[], Case]) -> None:
    benchmark(operation, _load_case(load))


def _load_case(load: Callable[[], Case]) -> Case:
    try:
        return load()
    except OSError as exc:
        # this body runs only under --codspeed (conftest skips it otherwise), so a missing corpus is a hard error
        msg = f"bench corpus submodule not checked out: {exc}; run: git submodule update --init tools/bench-data"
        raise RuntimeError(msg) from exc


@pytest.mark.parametrize(
    ("operation", "load"),
    [pytest.param(run, load, id=name) for name, run, load in benchmarks() if isinstance(run, Mutating)],
)
def test_mutating_feature(benchmark: BenchmarkFixture, operation: Mutating, load: Callable[[], Case]) -> None:
    case: Final = _load_case(load)
    benchmark.pedantic(operation.run, setup=lambda: ((operation.setup(case),), {}))
