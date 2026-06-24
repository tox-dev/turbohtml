"""
CLI entry point: ``python -m bench <command>``.

Commands: ``core`` (turbohtml baseline for every operation), ``all`` (every operation table), an operation name
(cross-package table), or a package name (that package's own report). The orchestrator provisions one isolated venv per
target, so this entry point itself needs only uv on PATH -- no turbohtml, no competitor.
"""

from __future__ import annotations

import sys

from bench import orchestrator


def main() -> None:
    """Parse the single command argument and run the matching report."""
    if len(sys.argv) != 2:
        msg = "usage: python -m bench <core|all|operation|package>"
        raise SystemExit(msg)
    orchestrator.run(sys.argv[1])


if __name__ == "__main__":
    main()
