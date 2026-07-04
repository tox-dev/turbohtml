"""
Representative training workload for the profile-guided build of the C extension.

Drives every turbohtml operation the project tracks over its real corpus, so the profile the compiler collects reflects
the whole hot surface -- parse, tokenize, select, text-content, serialize, minify and the rest -- rather than one
document. The cases come from :func:`bench.ci.benchmarks`, the same registry the CodSpeed gate measures over the same
vendored html5lib-python spec, War-and-Peace text, and pinned stylesheet and JS corpora, so the profile is collected
against exactly the inputs whose regressions CI watches. A case whose corpus is not checked out (or cannot be fetched
offline) is skipped, so a build without the optional submodules still trains on whatever is present.

Each operation gets an equal slice of training time rather than an equal iteration count. A whole-document parse runs
orders of magnitude more instructions per call than a read-path query like text-content, so a flat iteration count
buries the cheap operations' hot blocks far below the profile's global maximum: the compiler reads them as cold and
leaves their code layout on a knife-edge that flips a few percent between non-deterministic rebuilds (the text-content
CodSpeed flake, spike #478). Budgeting by time instead repeats a cheap operation into the thousands and an expensive one
only a handful of times, so every benchmarked hot path clears the profile's hot cutoff decisively and holds its layout
across rebuilds. The read-path operations share one cached parse, so a warm-up call primes it and the timing and
repetitions then measure the query alone, not a re-parse.

Run against an extension built with ``-Db_pgo=generate``; each measured call writes into the instrumented counters that
the following ``-Db_pgo=use`` build reads back. See :mod:`pgo_build` for the two-phase driver.
"""

from __future__ import annotations

import sys
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Final, cast

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench.ci import benchmarks  # the bench package sits beside this script, added to the path above
from bench.timing import Mutating

if TYPE_CHECKING:
    from collections.abc import Callable

_BUDGET_SECONDS: Final[float] = 0.2  # per operation: a cheap read-path op repeats into the thousands and lands hot
_MIN_ITERATIONS: Final[int] = 8  # even the slowest op (the full spec parse) still leaves several profile samples
_MAX_ITERATIONS: Final[int] = 50_000  # bound the cheapest ops so the instrumented training run stays quick
_CALIBRATION_CALLS: Final[int] = 4  # average a few warm calls so timer jitter cannot starve one op of repetitions


def _invoke(operation: object, case: object) -> None:
    """Run one operation over its case, rebuilding a fresh tree first for a mutating operation."""
    if isinstance(operation, Mutating):
        operation.run(operation.setup(case))
    else:
        cast("Callable[[object], object]", operation)(case)


def _iterations(operation: object, case: object) -> int:
    """Time a few warm calls and return how many repetitions fill :data:`_BUDGET_SECONDS`, clamped to the bounds."""
    start = perf_counter()
    for _ in range(_CALIBRATION_CALLS):
        _invoke(operation, case)
    per_call = (perf_counter() - start) / _CALIBRATION_CALLS
    if per_call <= 0.0:  # below the timer's resolution, so the op is cheap enough to run the whole budget
        return _MAX_ITERATIONS
    return min(_MAX_ITERATIONS, max(_MIN_ITERATIONS, round(_BUDGET_SECONDS / per_call)))


def train() -> tuple[int, int]:
    """Exercise every operation over its real corpus for an equal time slice; return the trained and skipped counts."""
    trained = skipped = 0
    for _name, operation, load in benchmarks():
        try:
            case = load()
        except OSError:  # corpus submodule absent, or a pinned upstream file could not be fetched offline
            skipped += 1
            continue
        _invoke(operation, case)  # prime the shared parse cache so the timing and repetitions measure only the hot op
        for _ in range(_iterations(operation, case)):
            _invoke(operation, case)
        trained += 1
    return trained, skipped


def main() -> None:
    """Run the training workload and report the coverage so a bare-checkout build's thin profile is visible."""
    trained, skipped = train()
    print(f"pgo training: exercised {trained} operations, skipped {skipped} for a missing corpus")


if __name__ == "__main__":
    main()


__all__ = [
    "train",
]
