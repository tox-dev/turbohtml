from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Final

import pytest
from docutils import nodes
from docutils.core import publish_doctree
from docutils.parsers.rst import directives

_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "docs" / "_ext"))

from bench_table import BenchTable  # ruff:ignore[module-import-not-at-top-of-file]  # imports after the sys.path insert that makes the docs extension importable

directives.register_directive("bench-table", BenchTable)

_SHARED: Final[str] = "shared caveat"
_OTHER: Final[str] = "other caveat"
_FEED: Final[dict[str, str | list[str] | list[list[str | float]] | dict[str, str]]] = {
    "label": "operation",
    "parties": ["turbohtml", "other"],
    "metrics": [],
    "rows": [["alpha", 1e-6, 2e-6], ["beta", 1e-6, 2e-6], ["gamma", 1e-6, 2e-6], ["delta", 1e-6, 2e-6]],
    # rows 1 and 2 share one caveat, row 3 carries a second, row 0 carries none
    "row_notes": {"1": _SHARED, "2": _SHARED, "3": _OTHER},
}


@pytest.fixture
def doctree(tmp_path: Path) -> nodes.document:
    # Render the synthetic feed through the directive and return the parsed document.
    (tmp_path / "feed.json").write_text(json.dumps(_FEED), encoding="utf-8")
    page = tmp_path / "page.rst"
    page.write_text(".. bench-table::\n    :file: feed.json\n", encoding="utf-8")
    return publish_doctree(page.read_text(encoding="utf-8"), source_path=str(page))


@pytest.mark.parametrize(
    ("index", "expected"),
    [
        pytest.param(0, [], id="unnoted-row-has-no-superscript"),
        pytest.param(1, ["1"], id="first-shared-row-marked"),
        pytest.param(2, ["1"], id="second-shared-row-reuses-ordinal"),
        pytest.param(3, ["2"], id="distinct-caveat-gets-next-ordinal"),
    ],
)
def test_row_note_superscript_marks_only_noted_rows(doctree: nodes.document, index: int, expected: list[str]) -> None:
    # Return the superscript ordinals carried by each body row's label cell, in row order.
    body = next(iter(next(iter(doctree.findall(nodes.table))).findall(nodes.tbody)))
    rows = [[sup.astext() for sup in row.children[0].findall(nodes.superscript)] for row in body.findall(nodes.row)]
    assert rows[index] == expected


def test_row_note_legend_spells_each_distinct_caveat(doctree: nodes.document) -> None:
    # Return each spelled-out legend line; the directive emits exactly one container, the legend under the table.
    legend = next(doctree.findall(nodes.container))
    lines = [paragraph.astext() for paragraph in legend.findall(nodes.paragraph)]
    assert lines == [f"1 {_SHARED}", f"2 {_OTHER}"]
