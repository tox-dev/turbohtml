"""
Pivot the per-operation benchmark matrix into one migration feed per competitor library.

Each competitor's migration guide compares turbohtml against that one library across every operation both
implement, on identical inputs. Where the per-operation development tables (``report._emit_table_json``)
slice the matrix by operation with every competitor as a column, this slices it by competitor: for each
library it collects every ``(operation, case)`` both it and turbohtml measured and writes
``docs/migration/bench/<slug>.json`` with the two columns ``turbohtml`` and the library.

The cells are raw mean seconds, the same shape the directive reads. A library that runs one operation keeps
the bare case label the hand-written feeds used; a library spanning several prefixes each row with the
operation title so mixed-magnitude rows stay legible (the directive derives the unit per row). The three
minify operations also carry an output-size column, which the matrix measures alongside the time.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Final

from bench import operations

_SIZE_OPS: Final = frozenset({"minify", "minify-css", "minify-js"})

# A competitor module's stem maps to its migration page's slug by turning ``_`` into ``-``; these libraries
# name the two differently, so the map pins them. A stem with no ``docs/migration/<slug>.rst`` is skipped, so
# columns that only appear inside another library's table (cchardet, cssmin, css_html_js_minify) fall out.
_SLUG_OVERRIDES: Final = {
    "beautifulsoup4": "beautifulsoup",
    "linkify_it": "linkify-it-py",
    "metadata_parser": "metadata_parser",
}


def _slug(stem: str) -> str:
    """Return the migration page slug for a competitor module stem."""
    return _SLUG_OVERRIDES.get(stem, stem.replace("_", "-"))


def discover_labels(competitor_dir: Path) -> dict[str, dict[str, str]]:
    """Read every competitor module's ``{op_key: party label}`` from source without importing the library."""
    found: dict[str, dict[str, str]] = {}
    for path in sorted(competitor_dir.glob("*.py")):
        if path.stem == "__init__":
            continue
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if not (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)):
                continue
            if node.targets[0].id == "OPERATIONS" and isinstance(node.value, ast.Dict):
                found[path.stem] = {
                    key.value: value.elts[1].value
                    for key, value in zip(node.value.keys, node.value.values, strict=True)
                    if isinstance(key, ast.Constant)
                    and isinstance(key.value, str)
                    and isinstance(value, ast.Tuple)
                    and isinstance(value.elts[1], ast.Constant)
                    and isinstance(value.elts[1].value, str)
                }
    return found


def _case_names(operation: str, stats: dict[str, dict[str, float]]) -> list[str]:
    """Return the operation's case names in run order, recovered from the turbohtml baseline keys."""
    names: list[str] = []
    for key in stats:  # a case name can itself contain "|" (the XPath union operator), so split off the ends
        operation_name, rest = key.split("|", 1)
        case, label = rest.rsplit("|", 1)
        if operation_name == operation and label == "turbohtml" and case not in names:
            names.append(case)
    return names


def _rst_safe(label: str) -> str:
    """Escape the RST inline-markup starters (an XPath case can carry ``*`` or ``|``); the directive parses labels."""
    return label.replace("\\", "\\\\").replace("*", "\\*").replace("|", "\\|").replace("`", "\\`")


def _rows(op_labels: dict[str, str], stats: dict[str, dict[str, float]]) -> list[list[str | float | None]]:
    """Build a library's rows across every shared operation-case; prefix the label with the op title if it spans ops."""
    prefixed = len(op_labels) > 1
    rows: list[list[str | float | None]] = []
    for operation, meta in ((op, operations.OPERATIONS[op]) for op in operations.OPERATIONS if op in op_labels):
        label = op_labels[operation]
        for case in _case_names(operation, stats):
            turbo = stats.get(f"{operation}|{case}|turbohtml")
            other = stats.get(f"{operation}|{case}|{label}")
            if turbo is None or other is None:
                continue
            row_label = _rst_safe(f"{meta.title} — {case}" if prefixed else case)
            if operation in _SIZE_OPS:
                rows.append([row_label, turbo["size"], turbo["mean"], other["size"], other["mean"]])
            else:
                rows.append([row_label, turbo["mean"], other["mean"]])
    return rows


def _caption(path: Path) -> str:
    """Reuse a committed feed's caption (``stylesheet``, ``JavaScript library``, ...); default to ``operation``."""
    return json.loads(path.read_text(encoding="utf-8")).get("label", "operation") if path.exists() else "operation"


def emit_migration_feeds(
    stats: dict[str, dict[str, float]], competitor_dir: Path, directory: Path, docs_root: Path
) -> list[str]:
    """
    Write one ``docs/migration/bench/<slug>.json`` per library with a migration page; return the slugs skipped.

    ``stats`` is the merged ``{"op|case|label": {"mean", "size"?}}`` across every operation. A library whose page
    is absent, or that produced no shared measurement, is skipped and reported.
    """
    skipped: list[str] = []
    for stem, op_labels in discover_labels(competitor_dir).items():
        slug = _slug(stem)
        if not (docs_root / "migration" / f"{slug}.rst").exists():
            continue
        if not (rows := _rows(op_labels, stats)):
            skipped.append(slug)
            continue
        feed = {
            "label": _caption(directory / f"{slug}.json"),
            "parties": ["turbohtml", next(iter(op_labels.values()))],
            "metrics": ["size", "time"] if op_labels.keys() <= _SIZE_OPS else [],
            "rows": rows,
        }
        (directory / f"{slug}.json").write_text(json.dumps(feed, indent=2, ensure_ascii=False) + "\n", "utf-8")
    return skipped


def stats_from_feeds(feeds_dir: Path) -> dict[str, dict[str, float]]:
    """Reconstruct ``{"op|case|party": {"mean", "size"?}}`` from a directory of per-operation ``report`` feeds."""
    stats: dict[str, dict[str, float]] = {}
    for path in feeds_dir.glob("*.json"):
        feed = json.loads(path.read_text(encoding="utf-8"))
        width = 2 if feed.get("metrics") == ["size", "time"] else 1
        for row in feed["rows"]:
            for index, party in enumerate(feed["parties"]):
                cells = row[1 + index * width : 1 + index * width + width]
                if width == 2 and isinstance(cells[-1], (int, float)):
                    stats[f"{path.stem}|{row[0]}|{party}"] = {"mean": cells[1], "stdev": 0.0, "size": cells[0]}
                elif width == 1 and isinstance(cells[0], (int, float)):
                    stats[f"{path.stem}|{row[0]}|{party}"] = {"mean": cells[0], "stdev": 0.0}
    return stats


def main() -> None:
    """Regenerate migration feeds from per-operation feeds. Args: FEEDS_DIR OUT_DIR DOCS_ROOT COMPETITOR_DIR."""
    feeds_dir, out_dir, docs_root, competitor_dir = (Path(argument) for argument in sys.argv[1:5])
    if skipped := emit_migration_feeds(stats_from_feeds(feeds_dir), competitor_dir, out_dir, docs_root):
        print(f"pages with no fresh measurement, left as committed: {skipped}")


__all__ = ["discover_labels", "emit_migration_feeds", "stats_from_feeds"]


if __name__ == "__main__":
    main()
