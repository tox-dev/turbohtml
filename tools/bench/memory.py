"""
Measure one benchmark case's peak resident memory in a fresh process.

Run as ``python -m bench.memory`` with the target, operation, and case index in the environment. A fresh process does
import plus one run of the op, then prints its high-water resident memory in bytes -- the honest "memory to do this
task" number, no baseline subtraction, so the tree a full-parse peer builds and the streaming rewriter does not shows up
as the gap.

It lives in its own process so the timed run's repeated allocations never pollute the high-water mark, and imports
stdlib plus bench only (never turbohtml at module top) so the one measured op is all that is on the clock.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

from bench.operations import INPUTS
from bench.timing import Mutating


def _peak_bytes() -> int:
    # Return this process's peak resident set size in bytes. On Linux ``ru_maxrss`` is no use here: execve folds the
    # spawning process's resident set into it, and subprocess spawns through vfork, so every case would report the
    # size of the pyperf worker that launched it. VmHWM belongs to the memory map execve built, so it counts this
    # process only.
    if sys.platform == "linux":
        for line in Path("/proc/self/status").read_text(encoding="ascii").splitlines():
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) * 1024
    import resource  # ruff:ignore[import-outside-top-level]  # POSIX-only; imported here so a Windows import of the module stays clean

    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # bytes on macOS


def main() -> None:
    # Run one case of the operation once for the target, then print the process's peak resident memory in bytes.
    target = os.environ["BENCH_TARGET"]
    operation = os.environ["BENCH_OPERATION"]
    case_index = int(os.environ["BENCH_CASE"])
    module = importlib.import_module("bench.core" if target == "core" else f"bench.competitors.{target}")
    func = module.OPERATIONS[operation][0]
    _case_name, arg = INPUTS[operation]()[case_index]
    func.run(func.setup(arg)) if isinstance(func, Mutating) else func(arg)
    print(_peak_bytes())


if __name__ == "__main__":
    main()
