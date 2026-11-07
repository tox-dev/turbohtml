from __future__ import annotations

import sys
from pathlib import Path
from typing import Final

import pytest

_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "tools"))

from bench.migration import discover_labels  # ruff:ignore[module-import-not-at-top-of-file]  # imports after the sys.path insert that makes the bench package importable
from bench.notes import NOTES  # ruff:ignore[module-import-not-at-top-of-file]  # imports after the sys.path insert that makes the bench package importable
from bench.operations import OPERATIONS  # ruff:ignore[module-import-not-at-top-of-file]  # imports after the sys.path insert that makes the bench package importable

_COMPETITORS: Final[Path] = _ROOT / "tools" / "bench" / "competitors"
_PAIRS: Final[list[tuple[str, str]]] = [(operation, party) for operation, parties in NOTES.items() for party in parties]


@pytest.mark.parametrize("operation", sorted(NOTES), ids=sorted(NOTES))
def test_noted_operation_is_benchmarked(operation: str) -> None:
    assert operation in OPERATIONS


@pytest.mark.parametrize(("operation", "party"), _PAIRS, ids=[f"{op}-{party}" for op, party in _PAIRS])
def test_noted_party_publishes_that_operation(operation: str, party: str) -> None:
    published = {
        label
        for op_labels in discover_labels(_COMPETITORS).values()
        for op, label in op_labels.items()
        if op == operation
    }
    assert party in published


@pytest.mark.parametrize(("operation", "party"), _PAIRS, ids=[f"{op}-{party}" for op, party in _PAIRS])
def test_note_says_what_differs(operation: str, party: str) -> None:
    # a note that only asserts incomparability tells a reader nothing they could check
    assert len(NOTES[operation][party]) > 40
