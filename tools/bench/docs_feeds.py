"""
Turn the per-operation feeds ``--table-json`` writes into the feeds ``docs/development/performance.rst`` consumes.

:mod:`bench.report` names each emitted feed after its operation, but the performance guide groups tables by section and
names them for the section (``querying-2.json``, ``building-3.json``). Without a mapping the two never line up, so
refreshing the guide meant copying files across by hand -- which is how the committed feeds drifted out of date and lost
competitor columns. ``TABLES`` is that mapping, and it is the only place the guide's filenames are written down.

Most tables draw on one operation and copy its feed straight across. Three predate a later split of their operation and
still present the combined view the guide describes, so they name their rows and, where one row's columns come from
different operations, the operation behind each column; a column an operation does not measure carries
:data:`_NO_EQUIVALENT` the way the directive's legend expects. This mirrors :mod:`bench.migration`, which does the same
job for the per-library migration feeds.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from bench.notes import NOTES

_NO_EQUIVALENT: Final = "no equivalent operation"


@dataclass(frozen=True)
class Combined:
    """A guide table assembled from several operations: its caption, columns, rows, and where each cell comes from."""

    label: str
    parties: tuple[str, ...]
    rows: tuple[tuple[str, str], ...]  # (case name, the operation supplying that row)
    # column label -> (operation, that operation's column) for tables whose columns span operations
    columns: dict[str, tuple[str, str]] = field(default_factory=dict)


# Every table in the performance guide, keyed by the filename it is committed under. A string is the operation whose
# feed is copied across unchanged; a Combined spells out a table the guide keeps whole across split operations.
TABLES: Final[dict[str, str | Combined]] = {
    "article-extraction": "article",
    "boilerplate-classification": "boilerplate",
    "building": "build",
    "building-2": "construct",
    "building-3": "emit",
    "building-4": "build-e",
    "css-minification": "minify-css",
    "css-specificity": "specificity",
    "date-extraction": "date",
    "editing": "edit",
    "editing-2": "class-edit",
    "editing-3": "set-html",
    "editing-4": "set-text",
    "editing-5": "strip-remove",
    "editing-6": "strip-tags",
    "encoding-detection": "encoding",
    "escaping": "escape",
    "extraction": "extract-attr",
    "extraction-2": "extract-text",
    "extraction-3": "extract-url",
    "fluent-chaining": "chain",
    "fragment-parsing": "fragment",
    "html-parser-adapter": "htmlparser",
    "js-minification": "minify-js",
    "legacy-decoding": "decode",
    "link-filtering": "links-filter",
    "linkify": "linkify",
    "linkify-2": "detect",
    "linkify-3": "phone",
    "linkify-4": "phone-parse",
    "linkify-5": "phone-format",
    "links": "links-extract",
    "links-2": "links-absolutize",
    "links-3": "links-rewrite",
    "markup-escaping": "markup",
    "markup-escaping-2": "markup-op",
    "matching": "match",
    "minifying": "minify",
    "parsing": "parse",
    "querying": "find",
    "querying-2": "select",
    "querying-3": "select-has",
    "querying-4": "find-text",
    "querying-5": "xpath",
    "querying-6": "escape-identifier",
    "sanitize": "sanitize",
    "sanitize-templates": "sanitize-templates",
    "serializing": "serialize",
    "structured-data": "structured",
    "tables": "tables",
    "text-content": "text-content",
    "tokenizing": "tokenize",
    "tree-navigation": "navigate",
    "xslt": "transform",
    "xslt-compile": "transform-compile",
    "xslt-reuse": "transform-reuse",
    "unescaping": "unescape",
    "url-cleaning": "urls-clean",
    "markdown": Combined(
        label="HTML to Markdown",
        parties=("turbohtml", "markdownify", "html2text"),
        rows=(
            ("article (2 KiB)", "markdown"),
            ("list (4 KiB)", "markdown"),
            ("table (4 KiB)", "markdown"),
            ("configured (4 KiB)", "markdown"),
            ("google_doc (4 KiB)", "markdown-google"),
        ),
    ),
    "node-paths": Combined(
        label="node path for every element",
        parties=("turbohtml css_path", "turbohtml xpath_path", "lxml getpath"),
        rows=(
            ("daring fireball (10 kB)", "path"),
            ("ars technica (56 kB)", "path"),
            ("mozilla blog (95 kB)", "path"),
            ("whatwg spec (235 kB)", "path"),
        ),
        columns={
            "turbohtml css_path": ("path", "turbohtml"),
            "turbohtml xpath_path": ("path-xpath", "turbohtml"),
            "lxml getpath": ("path", "lxml getpath"),
        },
    ),
    "text-content-2": Combined(
        label="layout-aware text",
        parties=("turbohtml", "inscriptis", "html-text", "resiliparse"),
        rows=(
            ("article (2 KiB)", "text-render"),
            ("table (4 KiB)", "text-render"),
            ("collapsed (2 KiB)", "text-collapsed"),
            ("main (4 KiB)", "text-main"),
            ("annotated (4 KiB)", "text-annotated"),
        ),
    ),
}


def _cell(feed: dict, party: str, case: str, key: str = "rows") -> float | str | None:
    """
    Return what one party recorded for one case, or None when the operation has no such column or row.

    ``key`` selects the array to read: the measurements, or the spread that says what they are worth. Both share a
    layout, so one lookup serves each.
    """
    if party not in feed["parties"]:
        return None
    source = feed.get(key)
    if not source:
        return None
    index = feed["parties"].index(party)
    width = 2 if feed["metrics"] == ["size", "time"] else 1
    offset = 1 if width == 2 else 0
    for position, row in enumerate(feed["rows"]):
        if row[0] == case:
            if position >= len(source):
                return None
            return source[position][1 + index * width + offset]
    return None


def _combine(spec: Combined, feeds: dict[str, dict]) -> dict:
    """Assemble one guide table from the operations its rows and columns name, spread included."""
    rows: list[list[float | str | None]] = []
    spread: list[list[float | None]] = []
    for case, row_operation in spec.rows:
        cells: list[float | str | None] = [case]
        noise: list[float | None] = [None]  # leading slot mirrors the case label, as in the per-operation feeds
        for party in spec.parties:
            operation, column = spec.columns.get(party, (row_operation, party))
            value = _cell(feeds[operation], column, case)
            cells.append(_NO_EQUIVALENT if value is None else value)
            variation = _cell(feeds[operation], column, case, key="spread")
            noise.append(variation if isinstance(variation, (int, float)) else None)
        rows.append(cells)
        spread.append(noise)
    # a combined table draws each column from its own operation, so a note follows the operation that column came from
    notes: dict[str, str] = {}
    for party in spec.parties:
        operation, column = spec.columns.get(party, (spec.rows[0][1], party))
        if (note := NOTES.get(operation, {}).get(column)) is not None:
            notes[party] = note
    return {
        "label": spec.label,
        "parties": list(spec.parties),
        "metrics": [],
        "rows": rows,
        "spread": spread,
        "notes": notes,
    }


def emit_docs_feeds(feeds_dir: Path, out_dir: Path) -> list[str]:
    """Write every performance-guide feed from the per-operation feeds; return the tables missing an operation."""
    out_dir.mkdir(parents=True, exist_ok=True)
    missing: list[str] = []
    for name, spec in TABLES.items():
        wanted = (
            (spec,)
            if isinstance(spec, str)
            else tuple(dict.fromkeys(op for _, op in spec.rows))
            + tuple(operation for operation, _ in spec.columns.values())
        )
        feeds: dict[str, dict] = {}
        for operation in dict.fromkeys(wanted):
            path = feeds_dir / f"{operation}.json"
            if not path.exists():
                missing.append(name)
                break
            feeds[operation] = json.loads(path.read_text(encoding="utf-8"))
        else:
            if isinstance(spec, str):
                feed = feeds[spec]
                feed["notes"] = {party: NOTES[spec][party] for party in feed["parties"] if party in NOTES.get(spec, {})}
            else:
                feed = _combine(spec, feeds)
            (out_dir / f"{name}.json").write_text(json.dumps(feed, indent=2, ensure_ascii=False) + "\n", "utf-8")
    return missing


def main() -> None:
    """Regenerate the guide's feeds. Args: FEEDS_DIR OUT_DIR, where FEEDS_DIR holds the ``--table-json`` output."""
    feeds_dir, out_dir = (Path(argument) for argument in sys.argv[1:3])
    if missing := emit_docs_feeds(feeds_dir, out_dir):
        print(f"tables with no fresh measurement, left as committed: {sorted(set(missing))}")


__all__ = ["TABLES", "Combined", "emit_docs_feeds"]


if __name__ == "__main__":
    main()
