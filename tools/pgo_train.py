"""
Representative training workload for the profile-guided build of the C extension.

Drives every turbohtml operation the project tracks over a representative real-world corpus, so the profile the compiler
collects reflects the whole hot surface -- parse, tokenize, select, text-content, serialize, minify and the rest -- over
the branch classes real inputs hit rather than one clean document. The cases come from :mod:`pgo_corpus`, the offline
training corpus, deliberately split from :func:`bench.ci.benchmarks` (the inputs the LTO-only CodSpeed gate measures) so
the shipped profile is tuned to real use, not overfit to the measured corpus. Every input is vendored or generated in
process, so the wheel build collects the full profile even in an offline cibuildwheel sandbox; an operation whose
vendored corpus is not checked out yields no cases and is skipped, so a bare-checkout build still trains on what is
present.

Each operation gets an equal slice of training time rather than an equal iteration count, and that slice is divided
across the operation's input set so every branch class in the set is exercised. A whole-document parse runs orders of
magnitude more instructions per call than a read-path query like text-content, so a flat iteration count buries the
cheap operations' hot blocks far below the profile's global maximum: the compiler reads them as cold and leaves their
code layout on a knife-edge that flips a few percent between non-deterministic rebuilds (the text-content CodSpeed
flake, spike #478). Budgeting by time instead repeats a cheap operation into the thousands and an expensive one only a
handful of times, so every hot path clears the profile's hot cutoff decisively and holds its layout across rebuilds. The
read-path operations share one cached parse per document, so a warm-up call primes it and the timing and repetitions
then measure the query alone, not a re-parse.

Run against an extension built with ``-Db_pgo=generate``; each measured call writes into the instrumented counters that
the following ``-Db_pgo=use`` build reads back. See :mod:`pgo_build` for the two-phase driver.
"""

from __future__ import annotations

import sys
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Final, cast

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench.timing import Mutating
from pgo_corpus import training_corpus  # the corpus module sits beside this script, added to the path above

if TYPE_CHECKING:
    from collections.abc import Callable

_BUDGET_SECONDS: Final[float] = 0.2  # per operation, split across its inputs: a cheap read-path op still lands hot
_MIN_ITERATIONS: Final[int] = 8  # even the slowest input (the full spec parse) still leaves several profile samples
_MAX_ITERATIONS: Final[int] = 50_000  # bound the cheapest inputs so the instrumented training run stays quick
_CALIBRATION_CALLS: Final[int] = 4  # average a few warm calls so timer jitter cannot starve one input of repetitions


def _invoke(operation: object, case: object) -> None:
    """Run one operation over its case, rebuilding a fresh tree first for a mutating operation."""
    if isinstance(operation, Mutating):
        operation.run(operation.setup(case))
    else:
        cast("Callable[[object], object]", operation)(case)


def _iterations(operation: object, case: object, budget: float) -> int:
    """Time a few warm calls and return how many repetitions fill ``budget`` seconds, clamped to the bounds."""
    start = perf_counter()
    for _ in range(_CALIBRATION_CALLS):
        _invoke(operation, case)
    per_call = (perf_counter() - start) / _CALIBRATION_CALLS
    if per_call <= 0.0:  # below the timer's resolution, so the input is cheap enough to run the whole budget
        return _MAX_ITERATIONS
    return min(_MAX_ITERATIONS, max(_MIN_ITERATIONS, round(budget / per_call)))


def train() -> tuple[int, int]:
    """Exercise every operation over its input set for an equal time slice; return the trained and skipped counts."""
    trained = skipped = 0
    for _name, operation, load in training_corpus():
        try:
            cases = load()
        except OSError:  # a required corpus submodule is absent, so this operation goes untrained on a bare checkout
            cases = []
        if not cases:
            skipped += 1
            continue
        budget = _BUDGET_SECONDS / len(cases)
        for case in cases:
            _invoke(operation, case)  # prime the shared parse cache so the timing and repetitions measure the hot op
            for _ in range(_iterations(operation, case, budget)):
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
