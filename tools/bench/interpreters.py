"""
Pivot the benchmark matrix by interpreter: the same turbohtml, the same corpus, one column per interpreter.

Where the per-operation tables slice the matrix by operation with every competitor as a column, this slices it by the
interpreter underneath. cpyext charges PyPy a boundary crossing per object, so an operation that crosses once and then
works inside C amortizes that cost over a whole document, while one that crosses per node pays it per node. Only a
measurement separates the two, and ``docs/explanation/interpreters.rst`` renders this feed rather than quoting figures
that go stale the next time the C core moves.

Run it with ``tox -e bench -- interpreters``, optionally with pyperf options (``--fast`` while iterating,
``--rigorous`` for the committed feed). It provisions one turbohtml-only venv per interpreter, builds the working tree
in each, and writes ``docs/explanation/bench/interpreters.json``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Final

from bench import operations, orchestrator
from bench.report import rst_safe

if TYPE_CHECKING:
    from collections.abc import Iterator

_FEED: Final[Path] = Path(__file__).resolve().parents[2] / "docs" / "explanation" / "bench" / "interpreters.json"

# Each label carries "turbohtml" so the bench-table directive anchors its ratios on a turbohtml column, and the first
# entry is the baseline the others are read against. The value is the uv Python spec the venv is built on.
INTERPRETERS: Final[tuple[tuple[str, str], ...]] = (
    ("turbohtml on CPython 3.14", "3.14"),
    ("turbohtml on PyPy 3.11", "pypy3.11"),
)

# One operation per shape the cpyext boundary charges for: a whole-document parse that crosses once, two string
# transforms, two tree-to-string walks, a query that stays inside C, and a traversal that wraps every node it visits.
OPERATIONS: Final[tuple[str, ...]] = (
    "parse",
    "escape",
    "unescape",
    "serialize",
    "text-content",
    "select",
    "navigate",
)


def build(workdir: Path, pyperf_args: tuple[str, ...]) -> None:
    """Measure every operation under every interpreter and write the docs feed."""
    measured = _measure(workdir, pyperf_args)
    _FEED.parent.mkdir(parents=True, exist_ok=True)
    feed = {
        "label": "operation",
        "parties": [party for party, _ in INTERPRETERS],
        "metrics": [],
        "rows": list(_rows(measured)),
    }
    _FEED.write_text(json.dumps(feed, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {_FEED}: {len(feed['rows'])} rows")


def _measure(workdir: Path, pyperf_args: tuple[str, ...]) -> dict[str, dict[str, dict[str, float | str]]]:
    """Return ``{operation: {case: {party: mean seconds, or the message naming why the cell is empty}}}``."""
    measured: dict[str, dict[str, dict[str, float | str]]] = {operation: {} for operation in OPERATIONS}
    for party, interpreter in INTERPRETERS:
        # the working tree, not the built wheel: a wheel carries one interpreter's ABI tag and cannot install elsewhere
        python = orchestrator.venv_python(
            workdir, interpreter.replace(".", "-"), (str(orchestrator.REPO_ROOT),), interpreter=interpreter
        )
        for operation in OPERATIONS:
            for key, entry in orchestrator.run_worker(python, "core", operation, workdir, pyperf_args).items():
                case = key.split("|")[1]
                measured[operation].setdefault(case, {})[party] = entry.get(
                    "mean", entry.get("error", "no measurement")
                )
    return measured


def _rows(measured: dict[str, dict[str, dict[str, float | str]]]) -> Iterator[list[str | float | None]]:
    """One row per operation-case; the label carries the operation title because the table spans several."""
    for operation in OPERATIONS:
        for case, cells in measured[operation].items():
            label = rst_safe(f"{operations.OPERATIONS[operation].title} — {case}")
            yield [label, *(cells.get(party) for party, _ in INTERPRETERS)]


__all__ = ["INTERPRETERS", "OPERATIONS", "build"]
