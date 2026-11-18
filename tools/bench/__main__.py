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
from bench.orchestrator import SweepOptions, physical_cpus, run


def _pop_value(arguments: list[str], flag: str) -> str | None:
    # Remove ``flag VALUE`` from the arguments and return VALUE, or None when the flag is absent.
    if flag not in arguments:
        return None
    position = arguments.index(flag)
    try:
        value = arguments[position + 1]
    except IndexError as error:
        msg = f"{flag} takes a value"
        raise SystemExit(msg) from error
    del arguments[position : position + 2]
    return value


def main() -> None:
    # Parse the command names plus any pyperf passthrough options and run the matching reports.
    arguments = sys.argv[1:]
    if "--table-json" in arguments:
        # also write each rendered table as the docs' bench-table JSON feed into the named directory
        position = arguments.index("--table-json")
        report.TABLE_JSON_DIR = Path(arguments[position + 1])
        del arguments[position : position + 2]
    # the directory each finished run is kept in, so a stopped sweep continues where it left off
    resume = _pop_value(arguments, "--resume")
    # re-measure only the named targets, e.g. one library whose build changed
    targets = _pop_value(arguments, "--targets")
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
            "usage: python -m bench [--table-json DIR] [--resume DIR] [--targets a,b] [--pgo] [--jobs N] "
            "<core|all|interpreters|package|operation...> [pyperf options...]"
        )
        raise SystemExit(msg)
    run(
        commands,
        SweepOptions(
            pyperf_args=tuple(arguments[len(commands) :]),
            pgo=pgo,
            jobs=jobs,
            resume=None if resume is None else Path(resume),
            targets=None if targets is None else frozenset(targets.split(",")),
        ),
    )


if __name__ == "__main__":
    main()
