"""
Render one operation's speedup table from a merged stats mapping.

Standard library only: the orchestrator imports this to print, so it must not pull in turbohtml or any competitor. A
benchmark name is ``"<operation>|<case>|<label>"`` and a stat is ``{"mean": seconds, "stdev": seconds}``; turbohtml is
the baseline every competitor column divides into. Each cell shows the mean, the relative standard deviation as ``±N%``
so a reader can tell a real gap from run-to-run noise, and (for competitors) the slowdown factor against turbohtml.

With ``--table-json DIR`` on the CLI, each rendered operation is also written to ``DIR/<operation>.json`` in the feed
the docs' ``bench-table`` directive consumes: the label, parties, metrics, and rows of raw mean seconds. A competitor
that threw on an input writes its message in place of the number, so the table can name why that cell is empty. The
directive derives the readable units and the ratios, so refreshing a docs table is copying the emitted feed over the
committed one.
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

# a party's entry for one case: a measurement (``mean``/``stdev``, plus ``size`` for a minify op or ``peak`` for a
# memory op), or the message a competitor threw on this input under the ``error`` key
_Stat = dict[str, float | str]

TABLE_JSON_DIR: Path | None = None


def _labels(operation: str, stats: dict[str, _Stat]) -> list[str]:
    """Return the labels present for an operation, turbohtml first, then competitors in first-seen order."""
    seen: list[str] = []
    for key in stats:
        operation_name, label = key.split("|", 1)[0], key.rsplit("|", 1)[1]
        if operation_name == operation and label not in seen:
            seen.append(label)
    return ["turbohtml", *(label for label in seen if label != "turbohtml")]


def _case_names(operation: str, stats: dict[str, _Stat]) -> list[str]:
    """Return the operation's case names in run order, recovered from the turbohtml baseline keys."""
    names: list[str] = []
    for key in stats:
        operation_name, rest = key.split("|", 1)
        case, label = rest.rsplit("|", 1)
        if operation_name == operation and label == "turbohtml" and case not in names:
            names.append(case)
    return names


def _cell(stat: _Stat, scale: float, unit: str) -> str:
    """Format one measurement as ``<mean> <unit> ±<relative stdev>%``; the caller passes only measured stats."""
    mean, stdev = float(stat["mean"]), float(stat["stdev"])
    relative = stdev / mean * 100 if mean else 0.0
    return f"{mean * scale:8.1f} {unit} ±{relative:3.0f}%"


def _memory_cell(stat: _Stat) -> str:
    """Format one measured case's peak resident memory as ``<N.N MB>``; appended for a memory op only."""
    return f"{float(stat['peak']) / 1e6:6.1f} MB"


def render(operation: str, stats: dict[str, _Stat]) -> None:
    """Print the table for one operation: turbohtml against each competitor with its slowdown factor."""
    meta = operations.OPERATIONS[operation]
    scale, competitors = _SCALE[meta.unit], _labels(operation, stats)[1:]
    memory_op = operation in operations.MEMORY_OPS
    print()
    header = f"{meta.title:32} {'turbohtml':>{_TURBO_COL}}" + "".join(
        f"{label:>{_COMPETITOR_COL}}" for label in competitors
    )
    print(header)
    for case_name in _case_names(operation, stats):
        if (turbo := stats.get(f"{operation}|{case_name}|turbohtml")) is None:
            continue
        turbo_cell = _cell(turbo, scale, meta.unit)
        if memory_op:  # peak RSS beside the timing, the streaming rewriter's headline advantage
            turbo_cell = f"{turbo_cell} {_memory_cell(turbo)}"
        row = f"{case_name:32} {turbo_cell:>{_TURBO_COL}}"
        for label in competitors:
            other = stats.get(f"{operation}|{case_name}|{label}")
            if other is None:
                row += f"{'-':>{_COMPETITOR_COL}}"
            elif isinstance(reason := other.get("error"), str):
                row += f"{reason[:_COMPETITOR_COL]:>{_COMPETITOR_COL}}"
            else:
                cell = f"{_cell(other, scale, meta.unit)} {float(other['mean']) / float(turbo['mean']):5.1f}x"
                if memory_op:
                    cell = f"{cell} {_memory_cell(other)}"
                row += f"{cell:>{_COMPETITOR_COL}}"
        print(row)
    if TABLE_JSON_DIR is not None:
        _emit_table_json(operation, stats, TABLE_JSON_DIR)


def _extra_metric(operation: str) -> str | None:
    """Return the leading non-time metric key: ``size`` for a minify op, ``peak`` for a memory op, else none."""
    if operation in operations.SIZE_OPS:
        return "size"
    if operation in operations.MEMORY_OPS:
        return "peak"
    return None


def _cells(stat: _Stat | None, *, extra: str | None) -> list[float | str | None]:
    """
    Return a party's cells for one case: ``[extra, time]`` when a leading metric applies, else ``[time]``.

    A missing measurement carries its reason across every metric column: the thrown message when the competitor
    errored on this input (``{"error": ...}``), else ``None`` for a party the run did not reach at all.
    """
    if stat is None:
        return [None, None] if extra else [None]
    if (reason := stat.get("error")) is not None:
        return [reason, reason] if extra else [reason]
    return [stat[extra], stat["mean"]] if extra else [stat["mean"]]


def _emit_table_json(operation: str, stats: dict[str, _Stat], directory: Path) -> None:
    """Write one operation's raw means (plus size or peak memory) as the docs' bench-table feed DIR/<op>.json."""
    competitors = _labels(operation, stats)[1:]
    extra = _extra_metric(operation)
    rows: list[list[str | float | None]] = []
    for case_name in _case_names(operation, stats):
        if (turbo := stats.get(f"{operation}|{case_name}|turbohtml")) is None:
            continue
        row: list[str | float | None] = [case_name, *_cells(turbo, extra=extra)]
        for label in competitors:
            row += _cells(stats.get(f"{operation}|{case_name}|{label}"), extra=extra)
        rows.append(row)
    metrics = [{"size": "size", "peak": "memory"}[extra], "time"] if extra else []
    feed = {
        "label": operations.OPERATIONS[operation].title,
        "parties": ["turbohtml", *competitors],
        "metrics": metrics,
        "rows": rows,
    }
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{operation}.json").write_text(json.dumps(feed, indent=2, ensure_ascii=False) + "\n", "utf-8")
