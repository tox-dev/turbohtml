"""
Time one (target, operation) under pyperf inside an isolated venv, then write the per-case stats to disk.

Run as ``python -m bench.worker`` with the target, operation, and output path in the environment. The target is
``core`` (turbohtml's baseline) or a competitor module name; only that one module -- and therefore that one library --
is imported, which is what keeps each venv isolated. Plain operations are measured with ``bench_func``; a
:class:`~bench.timing.Mutating` operation is measured with ``bench_time_func`` so its fresh-tree setup stays untimed.
"""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

import pyperf

from bench import operations
from bench.timing import Mutating

if TYPE_CHECKING:
    from collections.abc import Callable


def _timed(mutating: Mutating, arg: object) -> Callable[[int], float]:
    """Return a ``bench_time_func`` body: rebuild a fresh tree per iteration (untimed), clock only the mutation."""

    def inner(loops: int) -> float:
        setup, run = mutating.setup, mutating.run
        elapsed = 0.0
        for _ in range(loops):
            tree = setup(arg)
            start = pyperf.perf_counter()
            run(tree)
            elapsed += pyperf.perf_counter() - start
        return elapsed

    return inner


def _bench(runner: pyperf.Runner, name: str, func: object, arg: object) -> pyperf.Benchmark | None:
    """Measure one case: a mutating operation through ``bench_time_func``, any other through ``bench_func``."""
    if isinstance(func, Mutating):
        return runner.bench_time_func(name, _timed(func, arg))
    return runner.bench_func(name, func, arg)


def main() -> None:
    """Benchmark every case of the operation for the target; write ``{name: {mean, stdev}}`` from the pyperf manager."""
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
    ]
    stats: dict[str, dict[str, float | str]] = {}
    for case_name, arg in operations.INPUTS[operation]():
        name = f"{operation}|{case_name}|{label}"
        try:  # a stricter competitor parser (lightningcss rejects media queries the WHATWG rules recover) errors on
            func.run(func.setup(arg)) if isinstance(func, Mutating) else func(arg)  # that one input, not the whole op
        except Exception as exc:  # noqa: BLE001  # record the thrown message so the table can name why the cell is empty
            stats[name] = {"error": " ".join(str(exc).split())[:200] or type(exc).__name__}
            continue
        if (result := _bench(runner, name, func, arg)) is not None and result.get_nvalue() > 1:
            entry = {"mean": result.mean(), "stdev": result.stdev()}
            if operation in operations.SIZE_OPS:  # deterministic output length, measured once beside the timing
                entry["size"] = float(len(func(arg).encode("utf-8")))
            stats[name] = entry
    if not args.worker:  # the pyperf manager, holding every value -- not a per-value worker
        Path(os.environ["BENCH_OUT"]).write_text(json.dumps(stats), encoding="utf-8")


if __name__ == "__main__":
    main()
