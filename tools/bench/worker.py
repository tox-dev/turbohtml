"""
Time one (target, operation) under pyperf inside an isolated venv, then write the means to disk.

Run as ``python -m bench.worker`` with the target, operation, and output path in the environment. The target is
``core`` (turbohtml's baseline) or a competitor module name; only that one module -- and therefore that one library --
is imported, which is what keeps each venv isolated.
"""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

import pyperf

from bench import operations


def main() -> None:
    """Benchmark every case of the operation for the target; write ``{name: mean}`` from the pyperf manager."""
    target = os.environ["BENCH_TARGET"]
    operation = os.environ["BENCH_OPERATION"]
    module = importlib.import_module("bench.core" if target == "core" else f"bench.competitors.{target}")
    func, label = module.OPERATIONS[operation]
    runner = pyperf.Runner()
    args = runner.parse_args()
    args.inherit_environ = [
        *(args.inherit_environ or []),
        "PYTHONPATH",
        "BENCH_TARGET",
        "BENCH_OPERATION",
        "BENCH_OUT",
    ]
    means: dict[str, float] = {}
    for case_name, arg in operations.INPUTS[operation]():
        name = f"{operation}|{case_name}|{label}"
        if (result := runner.bench_func(name, func, arg)) is not None and result.get_nvalue():
            means[name] = result.mean()
    if not args.worker:  # the pyperf manager, holding every value -- not a per-value worker
        Path(os.environ["BENCH_OUT"]).write_text(json.dumps(means), encoding="utf-8")


if __name__ == "__main__":
    main()
