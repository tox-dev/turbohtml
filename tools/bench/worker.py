"""
Time one (target, operation) under pyperf inside an isolated venv, then write the per-case stats to disk.

Run as ``python -m bench.worker`` with the target, operation, and output path in the environment. The target is
``core`` (turbohtml's baseline) or a competitor module name; only that one module -- and therefore that one library --
is imported, which is what keeps each venv isolated. Plain operations are measured with ``bench_func``; a
:class:`~bench.timing.Mutating` operation is measured with ``bench_time_func`` so its fresh-tree setup stays untimed.
"""

from __future__ import annotations

import gc
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pyperf

from bench.operations import INPUTS, MEMORY_OPS, SIZE_OPS, Case
from bench.timing import Mutating

if TYPE_CHECKING:
    from collections.abc import Callable


def _timed(mutating: Mutating, arg: Case) -> Callable[[int], float]:
    # Return a ``bench_time_func`` body: rebuild a fresh tree per iteration (untimed), clock only the mutation.
    def inner(loops: int) -> float:
        setup, run = mutating.setup, mutating.run
        elapsed = 0.0
        for _ in range(loops):
            # pyperf can disable automatic GC; fresh cyclic trees must not accumulate between iterations.
            gc.collect()
            tree = setup(arg)
            start = pyperf.perf_counter()
            run(tree)
            elapsed += pyperf.perf_counter() - start
            del tree
        return elapsed

    return inner


def _bench(runner: pyperf.Runner, name: str, func: object, arg: Case) -> pyperf.Benchmark | None:
    # Measure one case: a mutating operation through ``bench_time_func``, any other through ``bench_func``.
    if isinstance(func, Mutating):
        return runner.bench_time_func(name, _timed(func, arg))
    return runner.bench_func(name, func, arg)


def _peak_memory(case_index: int) -> float:
    # Spawn ``bench.memory`` in a fresh process for one case and return the peak resident bytes it prints.
    completed = subprocess.run(
        [sys.executable, "-m", "bench.memory"],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "BENCH_CASE": str(case_index)},
    )
    return float(completed.stdout.strip())


def _record_memory(operation: str, label: str, stats: dict[str, dict[str, float | str]]) -> None:
    # Add ``peak`` resident bytes to each measured case of a memory op, one fresh subprocess per case.
    for case_index, (case_name, _arg) in enumerate(INPUTS[operation]()):
        if (
            entry := stats.get(f"{operation}|{case_name}|{label}")
        ) is not None and "mean" in entry:  # skip a case that errored or was too fast to measure
            entry["peak"] = _peak_memory(case_index)


def main() -> None:
    # Benchmark every case of the operation for the target; write ``{name: {mean, stdev}}`` from the pyperf manager.
    target = os.environ["BENCH_TARGET"]
    operation = os.environ["BENCH_OPERATION"]
    module = importlib.import_module("bench.core" if target == "core" else f"bench.competitors.{target}")
    func, label = module.OPERATIONS[operation]
    runner = pyperf.Runner()
    args = runner.parse_args()
    # pyperf re-execs this module as forked worker processes that re-read these from the environment, so each must be
    # inherited or the worker cannot locate the target, operation, or output path the manager set.
    args.inherit_environ = [
        *(args.inherit_environ or []),
        "PYTHONPATH",
        "BENCH_TARGET",
        "BENCH_OPERATION",
        "BENCH_OUT",
        "BENCH_SKIP_CASES",
    ]
    stats: dict[str, dict[str, float | str]] = {}
    skipped: set[int] = set(json.loads(os.environ.get("BENCH_SKIP_CASES", "[]"))) if args.worker else set()
    os.environ["BENCH_SKIP_CASES"] = json.dumps(sorted(skipped))
    task_index = 0
    for case_index, (case_name, arg) in enumerate(INPUTS[operation]()):
        if case_index in skipped:
            continue
        name = f"{operation}|{case_name}|{label}"
        if args.worker_task is not None and task_index != args.worker_task:
            # pyperf counts registrations after rejected cases have been removed.
            _bench(runner, name, func, arg)
            task_index += 1
            continue
        try:
            func.run(func.setup(arg)) if isinstance(func, Mutating) else func(arg)
        except Exception as exc:
            if args.worker:
                raise
            skipped.add(case_index)
            os.environ["BENCH_SKIP_CASES"] = json.dumps(sorted(skipped))
            stats[name] = {"error": " ".join(str(exc).split())[:200] or type(exc).__name__}
            continue
        if (result := _bench(runner, name, func, arg)) is not None and result.get_nvalue() > 1:
            entry = {"mean": result.mean(), "stdev": result.stdev()}
            if operation in SIZE_OPS:  # deterministic output length, measured once beside the timing
                entry["size"] = float(len(func(arg).encode("utf-8")))
            stats[name] = entry
        task_index += 1
    if not args.worker:  # the pyperf manager, holding every value -- not a per-value worker
        if operation in MEMORY_OPS:  # peak RSS from a fresh child, never a forked pyperf worker's timing run
            _record_memory(operation, label, stats)
        Path(os.environ["BENCH_OUT"]).write_text(json.dumps(stats), encoding="utf-8")


__all__ = []

if __name__ == "__main__":
    main()
