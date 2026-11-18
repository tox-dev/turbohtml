"""
CLI entry point: ``python -m bench [--pgo] [--jobs N] <command>...``.

Commands: ``core`` (turbohtml baseline for every operation), ``all`` (every operation table), ``interpreters`` (the same
turbohtml under each interpreter it supports, written straight to its docs feed), one or more operation names
(cross-package tables, rendered in the order given), or a package name (that package's own report). Only operation
names may be combined. Anything after the names is forwarded verbatim to pyperf in every worker (e.g. ``--rigorous``,
``--affinity=2``).

``--pgo``, before the command so pyperf never sees it, builds the turbohtml ``core`` baseline with the shipped release
recipe -- the two-phase profile-guided, link-time-optimized build of :mod:`pgo_build` -- so the baseline measures
release-representative numbers; omit it (the default) for a plain wheel that builds far faster while iterating. The
orchestrator provisions one isolated venv per target, so this entry point itself needs only uv on PATH -- no turbohtml,
no competitor.
"""

from __future__ import annotations

import sys
from itertools import takewhile
from pathlib import Path

from bench import report
from bench.orchestrator import physical_cpus, run


def main() -> None:
    # Parse the command names plus any pyperf passthrough options and run the matching reports.
    arguments = sys.argv[1:]
    if "--table-json" in arguments:
        # also write each rendered table as the docs' bench-table JSON feed into the named directory
        position = arguments.index("--table-json")
        report.TABLE_JSON_DIR = Path(arguments[position + 1])
        del arguments[position : position + 2]
    pgo = "--pgo" in arguments  # kept out of the pyperf passthrough: it selects how the core wheel is built
    if pgo:
        arguments.remove("--pgo")
    jobs = 1
    if "--jobs" in arguments:
        position = arguments.index("--jobs")
        try:
            jobs = int(arguments[position + 1])
        except (IndexError, ValueError) as error:
            msg = "--jobs takes the number of runs to execute at once"
            raise SystemExit(msg) from error
        del arguments[position : position + 2]
        cores = len(physical_cpus())
        if not 1 <= jobs <= cores:
            msg = f"--jobs must be between 1 and {cores}, the physical cores this machine has"
            raise SystemExit(msg)
        if jobs > 1 and any(argument.partition("=")[0] == "--affinity" for argument in arguments):
            msg = (
                "--affinity cannot be combined with --jobs above 1: each parallel run is already pinned to its own core"
            )
            raise SystemExit(msg)
    commands = tuple(takewhile(lambda argument: not argument.startswith("-"), arguments))
    if not commands:
        msg = (
            "usage: python -m bench [--table-json DIR] [--pgo] [--jobs N] "
            "<core|all|interpreters|package|operation...> [pyperf options...]"
        )
        raise SystemExit(msg)
    run(commands, tuple(arguments[len(commands) :]), pgo=pgo, jobs=jobs)


if __name__ == "__main__":
    main()
