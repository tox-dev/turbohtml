"""
Measure one benchmark case's peak resident memory in a fresh process.

Run as ``python -m bench.memory`` with the target, operation, and case index in the environment. A fresh process does
import plus one run of the op, then prints its high-water resident memory in bytes -- the honest "memory to do this
task" number, no baseline subtraction, so the tree a full-parse peer builds and the streaming rewriter does not shows
up as the gap. It lives in its own process so the timed run's repeated allocations never pollute the high-water mark,
and imports stdlib plus bench only (never turbohtml at module top) so the one measured op is all that is on the clock.
"""

from __future__ import annotations

import importlib
import os
import sys

from bench import operations
from bench.timing import Mutating


def _peak_bytes() -> int:
    """Return this process's peak resident set size in bytes; ``ru_maxrss`` reports bytes on macOS, KiB on Linux."""
    import resource  # noqa: PLC0415  # POSIX-only; imported here so a Windows import of the module stays clean

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak if sys.platform == "darwin" else peak * 1024


def main() -> None:
    """Run one case of the operation once for the target, then print the process's peak resident memory in bytes."""
    target = os.environ["BENCH_TARGET"]
    operation = os.environ["BENCH_OPERATION"]
    case_index = int(os.environ["BENCH_CASE"])
    module = importlib.import_module("bench.core" if target == "core" else f"bench.competitors.{target}")
    func = module.OPERATIONS[operation][0]
    _case_name, arg = operations.INPUTS[operation]()[case_index]
    func.run(func.setup(arg)) if isinstance(func, Mutating) else func(arg)
    print(_peak_bytes())


if __name__ == "__main__":
    main()
