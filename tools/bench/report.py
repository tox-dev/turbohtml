"""
Render one operation's speedup table from a merged stats mapping.

Standard library only: the orchestrator imports this to print, so it must not pull in turbohtml or any competitor. A
benchmark name is ``"<operation>|<case>|<label>"`` and a stat is ``{"mean": seconds, "stdev": seconds}``; turbohtml is
the baseline every competitor column divides into. Each cell shows the mean, the relative standard deviation as ``±N%``
so a reader can tell a real gap from run-to-run noise, and (for competitors) the slowdown factor against turbohtml.

With ``--table-json DIR`` on the CLI, each rendered operation is also written to ``DIR/<operation>.json`` in the feed
the docs' ``bench-table`` directive consumes: the label, parties, metrics, and rows of raw mean seconds. A competitor
that threw on an input writes its message in place of the number, so the table can name why that cell is empty. The
directive derives the readable units and the ratios. These files are named for the operation, which is not what the
performance guide calls its tables, so do not copy them across by hand: :mod:`bench.docs_feeds` maps the two and writes
the committed feeds, and :mod:`bench.migration` does the same for the per-library migration feeds.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .notes import NOTES
from .operations import MEMORY_OPS, OPERATIONS, SIZE_OPS
from .stats import NOISY_CV

if TYPE_CHECKING:
    from pathlib import Path

# a party's entry for one case: a measurement (``mean``/``stdev``, plus ``size`` for a minify op or ``peak`` for a
# memory op), or the message a competitor threw on this input under the ``error`` key
_Stat = dict[str, float | str]


TABLE_JSON_DIR: Path | None = None


def render(operation: str, stats: dict[str, _Stat]) -> None:
    # Print the table for one operation: turbohtml against each competitor with its slowdown factor.
    meta = OPERATIONS[operation]
    scale, competitors = {"ns": 1e9, "us": 1e6, "ms": 1e3}[meta.unit], _labels(operation, stats)[1:]
    memory_op = operation in MEMORY_OPS
    print()
    competitor_col: int = 26
    turbo_col = 18
    header = (
        f"{meta.title:32} {'turbohtml':>{turbo_col}}{''.join(f'{label:>{competitor_col}}' for label in competitors)}"
    )
    print(header)
    for case_name in _case_names(operation, stats):
        if (turbo := stats.get(f"{operation}|{case_name}|turbohtml")) is None:
            continue
        turbo_cell = _cell(turbo, scale, meta.unit)
        if memory_op:  # peak RSS beside the timing, the streaming rewriter's headline advantage
            turbo_cell = f"{turbo_cell} {_memory_cell(turbo)}"
        row = f"{case_name:32} {turbo_cell:>{turbo_col}}"
        for label in competitors:
            if (other := stats.get(f"{operation}|{case_name}|{label}")) is None:
                row += f"{'-':>{competitor_col}}"
            elif isinstance(reason := other.get("error"), str):
                row += f"{reason[:competitor_col]:>{competitor_col}}"
            else:
                cell = f"{_cell(other, scale, meta.unit)} {float(other['mean']) / float(turbo['mean']):5.1f}x"
                if memory_op:
                    cell = f"{cell} {_memory_cell(other)}"
                row += f"{cell:>{competitor_col}}"
        print(row)
    if noisy := _noisy_cells(operation, stats):
        # a run whose cells are mostly marked measured the machine, so say so once rather than per row
        print(f"  noise: {len(noisy)} cell(s) past {NOISY_CV:.0%} spread, not a basis for comparison: {noisy}")
    if TABLE_JSON_DIR is not None:
        _emit_table_json(operation, stats, TABLE_JSON_DIR)


def _labels(operation: str, stats: dict[str, _Stat]) -> list[str]:
    # Return the labels present for an operation, turbohtml first, then competitors in first-seen order.
    seen: list[str] = []
    for key in stats:
        operation_name, label = key.split("|", 1)[0], key.rsplit("|", 1)[1]
        if operation_name == operation and label not in seen:
            seen.append(label)
    return ["turbohtml", *(label for label in seen if label != "turbohtml")]


def _case_names(operation: str, stats: dict[str, _Stat]) -> list[str]:
    # Return the operation's case names in run order, recovered from the turbohtml baseline keys.
    names: list[str] = []
    for key in stats:
        operation_name, rest = key.split("|", 1)
        case, label = rest.rsplit("|", 1)
        if operation_name == operation and label == "turbohtml" and case not in names:
            names.append(case)
    return names


def _cell(stat: _Stat, scale: float, unit: str) -> str:
    # Format one measurement as ``<mean> <unit> ±<relative stdev>%``, trailing ``!`` on a spread too wide to compare.
    #
    # A number whose spread rivals the differences worth comparing reads exactly like a solid one in a table, so the
    # marker says which cells the machine dominated rather than leaving a reader to work it out from the percentage.
    marker = "!" if _coefficient_of_variation(stat) > NOISY_CV else " "
    return f"{float(stat['mean']) * scale:8.1f} {unit} ±{_coefficient_of_variation(stat) * 100:3.0f}%{marker}"


def _coefficient_of_variation(stat: _Stat) -> float:
    # Return the run-to-run spread as a fraction of the mean; the dimensionless noise level of one measurement.
    mean, stdev = float(stat["mean"]), float(stat["stdev"])
    return stdev / mean if mean else 0.0


def _memory_cell(stat: _Stat) -> str:
    # Format one measured case's peak resident memory as ``<N.N MB>``; appended for a memory op only.
    return f"{float(stat['peak']) / 1e6:6.1f} MB"


def _noisy_cells(operation: str, stats: dict[str, _Stat]) -> list[str]:
    # Return ``case/party`` for every measured cell whose spread is too wide to compare against.
    noisy: list[str] = []
    for key, stat in stats.items():
        operation_name, rest = key.split("|", 1)
        if operation_name != operation or stat.get("error") is not None or "mean" not in stat:
            continue
        if _coefficient_of_variation(stat) > NOISY_CV:
            case, label = rest.rsplit("|", 1)
            noisy.append(f"{case}/{label}")
    return noisy


def _emit_table_json(operation: str, stats: dict[str, _Stat], directory: Path) -> None:
    # Write one operation's raw means (plus size or peak memory) as the docs' bench-table feed DIR/<op>.json.
    competitors = _labels(operation, stats)[1:]
    extra = _extra_metric(operation)
    rows: list[list[str | float | None]] = []
    spread: list[list[float | None]] = []
    for case_name in _case_names(operation, stats):
        if (turbo := stats.get(f"{operation}|{case_name}|turbohtml")) is None:
            continue
        row: list[str | float | None] = [case_name, *_cells(turbo, extra=extra)]
        # the leading slot mirrors the row's case label so a spread cell shares its value's index
        noise: list[float | None] = [None, *_spread_cells(turbo, extra=extra)]
        for label in competitors:
            other = stats.get(f"{operation}|{case_name}|{label}")
            row += _cells(other, extra=extra)
            noise += _spread_cells(other, extra=extra)
        rows.append(row)
        spread.append(noise)
    metrics = [{"size": "size", "peak": "memory"}[extra], "time"] if extra else []
    feed = {
        "label": OPERATIONS[operation].title,
        "parties": ["turbohtml", *competitors],
        "metrics": metrics,
        "rows": rows,
        # one coefficient of variation per timing cell, so a published table can show what a figure is worth
        "spread": spread,
        # why a column does not compare like for like, for the parties that answer a different question
        "notes": {party: note for party, note in NOTES.get(operation, {}).items() if party in competitors},
    }
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{operation}.json").write_text(f"{json.dumps(feed, indent=2, ensure_ascii=False)}\n", "utf-8")


def _extra_metric(operation: str) -> str | None:
    # Return the leading non-time metric key: ``size`` for a minify op, ``peak`` for a memory op, else none.
    if operation in SIZE_OPS:
        return "size"
    if operation in MEMORY_OPS:
        return "peak"
    return None


def _cells(stat: _Stat | None, *, extra: str | None) -> list[float | str | None]:
    # Return a party's cells for one case: ``[extra, time]`` when a leading metric applies, else ``[time]``.
    #
    # A missing measurement carries its reason across every metric column: the thrown message when the competitor
    # errored on this input (``{"error": ...}``), else ``None`` for a party the run did not reach at all.
    if stat is None:
        return [None, None] if extra else [None]
    if (reason := stat.get("error")) is not None:
        return [reason, reason] if extra else [reason]
    return [stat[extra], stat["mean"]] if extra else [stat["mean"]]


def _spread_cells(stat: _Stat | None, *, extra: str | None) -> list[float | None]:
    # Return a party's noise level for one case, aligned with :func:`_cells`.
    #
    # Only the timing carries a spread: a minified size is a byte count and peak memory is a high-water mark, so the
    # leading metric's slot stays empty rather than inventing a dispersion for a number that has none.
    if stat is None or stat.get("error") is not None or "mean" not in stat:
        return [None, None] if extra else [None]
    variation = _coefficient_of_variation(stat)
    return [None, variation] if extra else [variation]


__all__ = [
    "render",
]
