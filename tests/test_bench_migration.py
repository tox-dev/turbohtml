"""
Keep a library benchmarked in several configurations on one migration page.

BeautifulSoup runs over two tree builders, and its own docs recommend the one the stdlib does not ship. Measuring only
the default hides what that choice costs, so both back ends are benchmarked and share a page. The feed writer used to
key one output per library, where the second configuration silently overwrote the first; these checks fail if it
regresses to that.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Final

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "tools"))

from bench.migration import emit_migration_feeds  # ruff:ignore[module-import-not-at-top-of-file]

_MODULE = '''\
"""Fake competitor."""
REQUIREMENTS = ("nothing",)
OPERATIONS = {{{entries}}}
'''


def _competitor_dir(root: Path, modules: dict[str, dict[str, str]]) -> Path:
    """Write fake competitor modules whose OPERATIONS literal is all the label reader parses."""
    directory = root / "competitors"
    directory.mkdir(parents=True)
    for stem, op_labels in modules.items():
        entries = ", ".join(f'"{operation}": (None, "{label}")' for operation, label in op_labels.items())
        (directory / f"{stem}.py").write_text(_MODULE.format(entries=entries), "utf-8")
    return directory


def _docs_root(root: Path, slug: str) -> Path:
    """Write the migration page whose presence makes the writer emit a feed for that slug."""
    docs = root / "docs"
    (docs / "migration").mkdir(parents=True)
    (docs / "migration" / f"{slug}.rst").write_text("page\n", "utf-8")
    return docs


def _emit(tmp_path: Path, modules: dict[str, dict[str, str]], stats: dict[str, dict[str, float]]) -> dict[str, Any]:
    """Run the writer over fake competitors and return the beautifulsoup feed it wrote."""
    out = tmp_path / "out"
    out.mkdir()
    emit_migration_feeds(stats, _competitor_dir(tmp_path, modules), out, _docs_root(tmp_path, "beautifulsoup"))
    return json.loads((out / "beautifulsoup.json").read_text(encoding="utf-8"))


def test_both_backends_column_the_same_page(tmp_path: Path) -> None:
    feed = _emit(
        tmp_path,
        {"beautifulsoup4": {"parse": "bs4 (html.parser)"}, "beautifulsoup4_lxml": {"parse": "bs4 (lxml)"}},
        {
            "parse|a page|turbohtml": {"mean": 1.0, "cv": 0.01},
            "parse|a page|bs4 (html.parser)": {"mean": 4.0, "cv": 0.02},
            "parse|a page|bs4 (lxml)": {"mean": 2.0, "cv": 0.03},
        },
    )
    assert feed["parties"] == ["turbohtml", "bs4 (html.parser)", "bs4 (lxml)"]


def test_both_backends_keep_their_own_timings(tmp_path: Path) -> None:
    feed = _emit(
        tmp_path,
        {"beautifulsoup4": {"parse": "bs4 (html.parser)"}, "beautifulsoup4_lxml": {"parse": "bs4 (lxml)"}},
        {
            "parse|a page|turbohtml": {"mean": 1.0, "cv": 0.01},
            "parse|a page|bs4 (html.parser)": {"mean": 4.0, "cv": 0.02},
            "parse|a page|bs4 (lxml)": {"mean": 2.0, "cv": 0.03},
        },
    )
    assert feed["rows"] == [["a page", 1.0, 4.0, 2.0]]


def test_operation_one_backend_skips_keeps_the_row(tmp_path: Path) -> None:
    feed = _emit(
        tmp_path,
        {
            "beautifulsoup4": {"parse": "bs4 (html.parser)", "encoding": "bs4 (html.parser)"},
            "beautifulsoup4_lxml": {"parse": "bs4 (lxml)"},
        },
        {
            "parse|a page|turbohtml": {"mean": 1.0, "cv": 0.01},
            "parse|a page|bs4 (html.parser)": {"mean": 4.0, "cv": 0.02},
            "parse|a page|bs4 (lxml)": {"mean": 2.0, "cv": 0.03},
            "encoding|bytes|turbohtml": {"mean": 1.0, "cv": 0.01},
            "encoding|bytes|bs4 (html.parser)": {"mean": 8.0, "cv": 0.02},
        },
    )
    # the configuration that skips the operation shows the shared measurement, and the row's note says it was
    # measured once rather than leaving a blank or hiding the caveat
    assert ["detect a byte stream's encoding — bytes", 1.0, 8.0, 8.0] in feed["rows"]
    row_index = feed["rows"].index(["detect a byte stream's encoding — bytes", 1.0, 8.0, 8.0])
    assert feed["row_notes"][str(row_index)] == "same in both configurations, so measured once"


def test_spread_aligns_with_every_variant(tmp_path: Path) -> None:
    feed = _emit(
        tmp_path,
        {"beautifulsoup4": {"parse": "bs4 (html.parser)"}, "beautifulsoup4_lxml": {"parse": "bs4 (lxml)"}},
        {
            "parse|a page|turbohtml": {"mean": 1.0, "cv": 0.01},
            "parse|a page|bs4 (html.parser)": {"mean": 4.0, "cv": 0.02},
            "parse|a page|bs4 (lxml)": {"mean": 2.0, "cv": 0.03},
        },
    )
    assert feed["spread"] == [[None, 0.01, 0.02, 0.03]]


def test_note_uses_the_operation_label(tmp_path: Path) -> None:
    output: Final = tmp_path / "out"
    output.mkdir()
    emit_migration_feeds(
        {
            "transform-key|sample|turbohtml": {"mean": 1.0},
            "transform-key|sample|lxml.etree": {"mean": 2.0},
        },
        _competitor_dir(tmp_path, {"lxml": {"parse": "lxml", "transform-key": "lxml.etree"}}),
        output,
        _docs_root(tmp_path, "lxml"),
    )
    assert json.loads((output / "lxml.json").read_text())["row_notes"] == {
        "0": "returns an XSLT result tree; conversion to a Python string is outside timing"
    }


@pytest.mark.parametrize("operation", ["minify", "minify-css", "minify-js", "minify-js-sequences"])
def test_size_operations_keep_output_bytes_and_timing_spread(tmp_path: Path, operation: str) -> None:
    feed: Final = _emit(
        tmp_path,
        {"beautifulsoup4": {operation: "competitor"}},
        {
            f"{operation}|sample|turbohtml": {"size": 10.0, "mean": 1.0, "cv": 0.1},
            f"{operation}|sample|competitor": {"size": 20.0, "mean": 2.0, "cv": 0.2},
        },
    )
    assert (feed["metrics"], feed["rows"], feed["spread"]) == (
        ["size", "time"],
        [["sample", 10.0, 1.0, 20.0, 2.0]],
        [[None, None, 0.1, None, 0.2]],
    )
