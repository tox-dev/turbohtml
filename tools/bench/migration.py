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
from typing import TYPE_CHECKING, Final

from bench import operations
from bench.notes import NOTES

if TYPE_CHECKING:
    from collections.abc import Iterable

_SIZE_OPS: Final = frozenset({"minify", "minify-css", "minify-js"})
# a memory op leads with peak resident bytes, so its rows are two cells wide like a size op's
_MEMORY_OPS: Final = frozenset({"find-cold", "parse-dense", "rewrite"})
_WIDE_OPS: Final = _SIZE_OPS | _MEMORY_OPS

# Why a variant repeats the sibling's figure when the library is measured in more than one configuration: the
# operation runs the same code whichever one is chosen, so benchmarking it twice would publish a duplicate column.
_SHARED_WITH_OTHER_VARIANT: Final = "same in both configurations, so measured once"

# A competitor module's stem maps to its migration page's slug by turning ``_`` into ``-``; these libraries
# name the two differently, so the map pins them. A stem with no ``docs/migration/<slug>.rst`` is skipped, so
# columns that only appear inside another library's table (cchardet, cssmin, css_html_js_minify) fall out.
_SLUG_OVERRIDES: Final = {
    "beautifulsoup4": "beautifulsoup",
    # the two tree-builder backends share one migration page, so a reader sees what the choice costs
    "beautifulsoup4_lxml": "beautifulsoup",
    "linkify_it": "linkify-it-py",
    "metadata_parser": "metadata_parser",
}


def _merge_caveat(note: str | None, *, shared: bool) -> str | None:
    """Join an operation note with the measured-once caveat when a row repeats one figure across configurations."""
    if not shared:
        return note
    return _SHARED_WITH_OTHER_VARIANT if note is None else f"{note}; {_SHARED_WITH_OTHER_VARIANT}"


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


def _rows(
    variants: list[dict[str, str]], stats: dict[str, dict[str, float]]
) -> tuple[list[list[str | float]], list[list[float | None]], list[str | None]]:
    """
    Build a library's rows and their spread across every shared operation-case, prefixing labels that span ops.

    ``variants`` holds one ``{op: label}`` per competitor module sharing this page, so a library benchmarked in more
    than one configuration (BeautifulSoup over each of its tree builders) columns them side by side. A variant that
    does not measure an operation shows the sibling's shared figure rather than dropping the row for the ones that do.
    """
    covered = {operation for variant in variants for operation in variant}
    labels = [next(iter(variant.values())) for variant in variants]
    # a leading metric only makes sense when every shared operation carries one; a library that spans a memory
    # operation and timing-only ones would otherwise emit rows of two different widths into one table
    wide = bool(_leading_metric(covered))
    rows: list[list[str | float]] = []
    spread: list[list[float | None]] = []
    caveats: list[str | None] = []
    for operation, meta in ((op, operations.OPERATIONS[op]) for op in operations.OPERATIONS if op in covered):
        for case in _case_names(operation, stats):
            turbo = stats.get(f"{operation}|{case}|turbohtml")
            others = [
                stats.get(f"{operation}|{case}|{variant[operation]}") if operation in variant else None
                for variant in variants
            ]
            if turbo is None or all(other is None for other in others):
                continue
            # a case name is an authored RST fragment (an XPath expression arrives already wrapped in ``code``
            # backticks), so it passes through verbatim; escaping it here would double up on that markup
            row_label = f"{meta.title} — {case}" if len(covered) > 1 else case
            # a configuration that skips an operation runs the same code as the one measuring it, so its cells show
            # the shared measurement and the row's note says the figure was measured once
            measured = next(other for other in others if other is not None)
            shared = any(other is None for other in others)
            filled = [other if other is not None else measured for other in others]
            row: list[str | float]
            if wide:
                lead = "size" if operation in _SIZE_OPS else "peak"
                row = [row_label, turbo[lead], turbo["mean"]]
                noise: list[float | None] = [None, None, turbo.get("cv")]
                for other in filled:
                    row += [other[lead], other["mean"]]
                    noise += [None, other.get("cv")]
            else:
                row = [row_label, turbo["mean"]]
                noise = [None, turbo.get("cv")]
                for other in filled:
                    row.append(other["mean"])
                    noise.append(other.get("cv"))
            rows.append(row)
            spread.append(noise)
            # a library page mixes operations, so a caveat belongs on the rows of the operation it describes; a row
            # repeating one measurement across configurations says so beside any operation note
            caveats.append(
                _merge_caveat(
                    next((NOTES[operation][label] for label in labels if label in NOTES.get(operation, {})), None),
                    shared=shared,
                )
            )
    return rows, spread, caveats


def _leading_metric(ops: Iterable[str]) -> list[str]:
    """Name the metric column a library's table leads with, empty when every shared operation is timing only."""
    names = set(ops)
    if names <= _SIZE_OPS:
        return ["size", "time"]
    if names <= _MEMORY_OPS:
        return ["memory", "time"]
    return []


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
    by_slug: dict[str, list[dict[str, str]]] = {}
    for stem, op_labels in discover_labels(competitor_dir).items():
        by_slug.setdefault(_slug(stem), []).append(op_labels)
    for slug, variants in by_slug.items():
        if not (docs_root / "migration" / f"{slug}.rst").exists():
            continue
        rows, spread, caveats = _rows(variants, stats)
        if not rows:
            skipped.append(slug)
            continue
        labels = [next(iter(variant.values())) for variant in variants]
        feed = {
            "label": _caption(directory / f"{slug}.json"),
            "parties": ["turbohtml", *labels],
            "metrics": _leading_metric({operation for variant in variants for operation in variant}),
            "rows": rows,
            "spread": spread,
            # keyed by row rather than by column: a library page mixes operations, and a caveat that belongs to one
            # of them would otherwise read as covering every row the library appears in
            "row_notes": {str(index): note for index, note in enumerate(caveats) if note is not None},
        }
        (directory / f"{slug}.json").write_text(json.dumps(feed, indent=2, ensure_ascii=False) + "\n", "utf-8")
    return skipped


def stats_from_feeds(feeds_dir: Path) -> dict[str, dict[str, float]]:
    """Reconstruct ``{"op|case|party": {"mean", "size"?}}`` from a directory of per-operation ``report`` feeds."""
    stats: dict[str, dict[str, float]] = {}
    for path in feeds_dir.glob("*.json"):
        feed = json.loads(path.read_text(encoding="utf-8"))
        width = 2 if len(feed.get("metrics") or []) == 2 else 1
        spread = feed.get("spread") or []
        for position, row in enumerate(feed["rows"]):
            noise = spread[position] if position < len(spread) else None
            for index, party in enumerate(feed["parties"]):
                start = 1 + index * width
                cells = row[start : start + width]
                # the timing's coefficient of variation travels with the value, so the migration table can show it
                variation = noise[start + width - 1] if noise else None
                variation = variation if isinstance(variation, (int, float)) else 0.0
                if width == 2 and isinstance(cells[-1], (int, float)):
                    lead = "size" if feed.get("metrics", [""])[0] == "size" else "peak"
                    stats[f"{path.stem}|{row[0]}|{party}"] = {"mean": cells[1], "cv": variation, lead: cells[0]}
                elif width == 1 and isinstance(cells[0], (int, float)):
                    stats[f"{path.stem}|{row[0]}|{party}"] = {"mean": cells[0], "cv": variation}
    return stats


def main() -> None:
    """Regenerate migration feeds from per-operation feeds. Args: FEEDS_DIR OUT_DIR DOCS_ROOT COMPETITOR_DIR."""
    feeds_dir, out_dir, docs_root, competitor_dir = (Path(argument) for argument in sys.argv[1:5])
    if skipped := emit_migration_feeds(stats_from_feeds(feeds_dir), competitor_dir, out_dir, docs_root):
        # to stderr so a redirected sweep still surfaces it: a page silently losing its table is the failure to catch
        message = f"migration: {len(skipped)} page(s) with no shared measurement, left as committed: {skipped}"
        print(message, file=sys.stderr)


__all__ = ["discover_labels", "emit_migration_feeds", "stats_from_feeds"]


if __name__ == "__main__":
    main()
