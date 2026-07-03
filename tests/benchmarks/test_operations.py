"""One CodSpeed benchmark per turbohtml operation, driven off the shared ``bench`` registry.

The cases come from :func:`bench.ci.benchmarks`, which walks the same :data:`bench.core.OPERATIONS` the pyperf suite
times over the same real corpora, so every operation the project tracks gets an instruction-count regression gate in CI
and adding an operation adds a benchmark. The benchmarks run only under ``--codspeed`` (see ``conftest.py``): the CI
benchmark job passes it, and locally it is opt-in. Mutating operations rebuild their tree through the pedantic ``setup``
hook, which runs untimed before each measured call, so the number is the mutation and not the parse. A case whose corpus
is not checked out (or cannot be fetched) skips rather than fails.
"""

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
    except OSError as exc:  # corpus submodule not checked out, or the pinned upstream file could not be fetched
        pytest.skip(f"corpus unavailable: {exc}")
    if isinstance(operation, Mutating):
        benchmark.pedantic(operation.run, setup=lambda: ((operation.setup(case),), {}))
    else:
        benchmark(cast("Callable[[object], object]", operation), case)
