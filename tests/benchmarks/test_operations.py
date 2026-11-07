from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest
from bench.ci import benchmarks
from bench.timing import Mutating

if TYPE_CHECKING:
    from collections.abc import Callable

    from pytest_codspeed import BenchmarkFixture


@pytest.mark.parametrize(
    ("operation", "load"),
    [pytest.param(run, load, id=name) for name, run, load in benchmarks()],
)
def test_feature(benchmark: BenchmarkFixture, operation: object, load: Callable[[], object]) -> None:
    try:
        case = load()
    except OSError as exc:
        # this body runs only under --codspeed (conftest skips it otherwise), so a missing corpus is a hard error
        msg = f"bench corpus submodule not checked out: {exc}; run: git submodule update --init tools/bench-data"
        raise RuntimeError(msg) from exc
    if isinstance(operation, Mutating):
        benchmark.pedantic(operation.run, setup=lambda: ((operation.setup(case),), {}))
    else:
        benchmark(cast("Callable[[object], object]", operation), case)
