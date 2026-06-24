"""
Render one operation's speedup table from a merged means mapping.

Standard library only: the orchestrator imports this to print, so it must not pull in turbohtml or any competitor. A
benchmark name is ``"<operation>|<case>|<label>"`` and a mean is seconds; turbohtml is the baseline every competitor
column divides into.
"""

from __future__ import annotations

from bench import operations

_SCALE: dict[str, float] = {"ns": 1e9, "us": 1e6, "ms": 1e3}


def _labels(operation: str, means: dict[str, float]) -> list[str]:
    """Return the labels present for an operation, turbohtml first, then competitors in first-seen order."""
    seen: list[str] = []
    for key in means:
        operation_name, _, label = key.split("|")
        if operation_name == operation and label not in seen:
            seen.append(label)
    return ["turbohtml", *(label for label in seen if label != "turbohtml")]


def _case_names(operation: str, means: dict[str, float]) -> list[str]:
    """Return the operation's case names in run order, recovered from the turbohtml baseline keys."""
    names: list[str] = []
    for key in means:
        operation_name, case, label = key.split("|")
        if operation_name == operation and label == "turbohtml" and case not in names:
            names.append(case)
    return names


def render(operation: str, means: dict[str, float]) -> None:
    """Print the table for one operation: turbohtml against each competitor with its slowdown factor."""
    meta = operations.OPERATIONS[operation]
    scale, competitors = _SCALE[meta.unit], _labels(operation, means)[1:]
    print()
    header = f"{meta.title:32} {'turbohtml':>12}" + "".join(f"{label:>20}" for label in competitors)
    print(header)
    for case_name in _case_names(operation, means):
        if (turbo := means.get(f"{operation}|{case_name}|turbohtml")) is None:
            continue
        row = f"{case_name:32} {turbo * scale:8.1f} {meta.unit}"
        for label in competitors:
            other = means.get(f"{operation}|{case_name}|{label}")
            row += f" {other * scale:8.1f} {meta.unit} {other / turbo:5.1f}x" if other is not None else f"{'-':>20}"
        print(row)
