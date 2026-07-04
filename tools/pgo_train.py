"""
Representative training workload for the profile-guided build of the C extension.

Drives every turbohtml operation the project tracks over its real corpus, so the profile the compiler collects reflects
the whole hot surface -- parse, tokenize, select, serialize, minify and the rest -- rather than one document. The cases
come from :func:`bench.ci.benchmarks`, the same registry the CodSpeed gate measures over the same vendored
html5lib-python spec, War-and-Peace text, and pinned stylesheet and JS corpora, so the profile is collected against
exactly the inputs whose regressions CI watches. A case whose corpus is not checked out (or cannot be fetched offline)
is skipped, so a build without the optional submodules still trains on whatever is present.

Run against an extension built with ``-Db_pgo=generate``; each measured call writes into the instrumented counters that
the following ``-Db_pgo=use`` build reads back. See :mod:`pgo_build` for the two-phase driver.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench.ci import benchmarks  # the bench package sits beside this script, added to the path above
from bench.timing import Mutating

if TYPE_CHECKING:
    from collections.abc import Callable

_ITERATIONS: Final[int] = 8  # weight the hot paths without dragging the build; the spec parse dominates


def train(iterations: int = _ITERATIONS) -> tuple[int, int]:
    """Exercise every operation over its real corpus ``iterations`` times; return the trained and skipped counts."""
    trained = skipped = 0
    for _name, operation, load in benchmarks():
        try:
            case = load()
        except OSError:  # corpus submodule absent, or a pinned upstream file could not be fetched offline
            skipped += 1
            continue
        for _ in range(iterations):
            if isinstance(operation, Mutating):
                operation.run(operation.setup(case))
            else:
                cast("Callable[[object], object]", operation)(case)
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
