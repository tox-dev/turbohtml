"""
Render one operation's speedup table from a merged stats mapping.

Standard library only: the orchestrator imports this to print, so it must not pull in turbohtml or any competitor. A
benchmark name is ``"<operation>|<case>|<label>"`` and a stat is ``{"mean": seconds, "stdev": seconds}``; turbohtml is
the baseline every competitor column divides into. Each cell shows the mean, the relative standard deviation as ``±N%``
so a reader can tell a real gap from run-to-run noise, and (for competitors) the slowdown factor against turbohtml.

With ``--table-json DIR`` on the CLI, each rendered operation is also written to ``DIR/<operation>.json`` in the feed
the docs' ``bench-table`` directive consumes: the label, parties, metrics, and rows of raw mean seconds. The directive
derives the readable units and the ratios, so refreshing a docs table is copying the emitted feed over the committed
one.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from bench import operations

if TYPE_CHECKING:
    from pathlib import Path

_SCALE: dict[str, float] = {"ns": 1e9, "us": 1e6, "ms": 1e3}
_TURBO_COL = 18
_COMPETITOR_COL = 26

TABLE_JSON_DIR: Path | None = None


def _labels(operation: str, stats: dict[str, dict[str, float]]) -> list[str]:
    """Return the labels present for an operation, turbohtml first, then competitors in first-seen order."""
    seen: list[str] = []
    for key in stats:
        operation_name, label = key.split("|", 1)[0], key.rsplit("|", 1)[1]
        if operation_name == operation and label not in seen:
            seen.append(label)
    return ["turbohtml", *(label for label in seen if label != "turbohtml")]


def _case_names(operation: str, stats: dict[str, dict[str, float]]) -> list[str]:
    """Return the operation's case names in run order, recovered from the turbohtml baseline keys."""
    names: list[str] = []
    for key in stats:
        operation_name, rest = key.split("|", 1)
        case, label = rest.rsplit("|", 1)
        if operation_name == operation and label == "turbohtml" and case not in names:
            names.append(case)
    return names


def _cell(stat: dict[str, float], scale: float, unit: str) -> str:
    """Format one measurement as ``<mean> <unit> ±<relative stdev>%``."""
    relative = stat["stdev"] / stat["mean"] * 100 if stat["mean"] else 0.0
    return f"{stat['mean'] * scale:8.1f} {unit} ±{relative:3.0f}%"


def render(operation: str, stats: dict[str, dict[str, float]]) -> None:
    """Print the table for one operation: turbohtml against each competitor with its slowdown factor."""
    meta = operations.OPERATIONS[operation]
    scale, competitors = _SCALE[meta.unit], _labels(operation, stats)[1:]
    print()
    header = f"{meta.title:32} {'turbohtml':>{_TURBO_COL}}" + "".join(
        f"{label:>{_COMPETITOR_COL}}" for label in competitors
    )
    print(header)
    for case_name in _case_names(operation, stats):
        if (turbo := stats.get(f"{operation}|{case_name}|turbohtml")) is None:
            continue
        row = f"{case_name:32} {_cell(turbo, scale, meta.unit):>{_TURBO_COL}}"
        for label in competitors:
            if (other := stats.get(f"{operation}|{case_name}|{label}")) is None:
                row += f"{'-':>{_COMPETITOR_COL}}"
            else:
                cell = f"{_cell(other, scale, meta.unit)} {other['mean'] / turbo['mean']:5.1f}x"
                row += f"{cell:>{_COMPETITOR_COL}}"
        print(row)
    if TABLE_JSON_DIR is not None:
        _emit_table_json(operation, stats, TABLE_JSON_DIR)


def _cells(stat: dict[str, float] | None, *, size_op: bool) -> list[float | None]:
    """Return a party's cells for one case: ``[size, time]`` for a minify op, else ``[time]``; blanks when absent."""
    if stat is None:
        return [None, None] if size_op else [None]
    return [stat["size"], stat["mean"]] if size_op else [stat["mean"]]


def _emit_table_json(operation: str, stats: dict[str, dict[str, float]], directory: Path) -> None:
    """Write one operation's raw means (plus size for a minify op) as the docs' bench-table feed DIR/<op>.json."""
    competitors = _labels(operation, stats)[1:]
    size_op = operation in operations.SIZE_OPS
    rows: list[list[str | float | None]] = []
    for case_name in _case_names(operation, stats):
        if (turbo := stats.get(f"{operation}|{case_name}|turbohtml")) is None:
            continue
        row: list[str | float | None] = [case_name, *_cells(turbo, size_op=size_op)]
        for label in competitors:
            row += _cells(stats.get(f"{operation}|{case_name}|{label}"), size_op=size_op)
        rows.append(row)
    feed = {
        "label": operations.OPERATIONS[operation].title,
        "parties": ["turbohtml", *competitors],
        "metrics": ["size", "time"] if size_op else [],
        "rows": rows,
    }
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{operation}.json").write_text(json.dumps(feed, indent=2, ensure_ascii=False) + "\n", "utf-8")
