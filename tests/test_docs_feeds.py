from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Final

import pytest

_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "tools"))

from bench.docs_feeds import TABLES, Combined  # ruff:ignore[module-import-not-at-top-of-file]  # imports after the sys.path insert that makes the bench package importable
from bench.operations import OPERATIONS  # ruff:ignore[module-import-not-at-top-of-file]  # imports after the sys.path insert that makes the bench package importable

_GUIDE: Final[Path] = _ROOT / "docs" / "development" / "performance.rst"
_REFERENCED = sorted(set(re.findall(r"bench/([\w-]+)\.json", _GUIDE.read_text(encoding="utf-8"))))


def _operations(table: str | Combined) -> tuple[str, ...]:
    # Return every operation a table draws a cell from.
    if isinstance(table, str):
        return (table,)
    return tuple(operation for _, operation in table.rows) + tuple(operation for operation, _ in table.columns.values())


@pytest.mark.parametrize("name", [pytest.param(name, id=name) for name in _REFERENCED])
def test_guide_table_has_a_generator_entry(name: str) -> None:
    # Every table the guide renders is one the generator knows how to write.
    assert name in TABLES


@pytest.mark.parametrize("name", [pytest.param(name, id=name) for name in sorted(TABLES)])
def test_generator_entry_is_rendered_by_the_guide(name: str) -> None:
    # The generator writes no feed the guide has stopped referencing.
    assert name in _REFERENCED


@pytest.mark.parametrize(
    "operation",
    [
        pytest.param(operation, id=f"{name}-{operation}")
        for name, table in sorted(TABLES.items())
        for operation in _operations(table)
    ],
)
def test_generator_entry_names_a_real_operation(operation: str) -> None:
    # A table cannot source a cell from an operation the bench no longer runs.
    assert operation in OPERATIONS
