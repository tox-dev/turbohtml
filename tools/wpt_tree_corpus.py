"""Read the committed WPT HTML tree-construction corpus."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict, cast


class WptHtmlTreeError(TypedDict):
    # One expected parse error and its source span.
    code: str
    line: int
    col: int
    end_line: int | None
    end_col: int | None


class WptHtmlTreeInput(TypedDict):
    # Fields identifying one WPT tree-construction input.
    file: str
    data: str
    context: str | None
    scripting: bool | None


class WptHtmlTreeCase(WptHtmlTreeInput):
    document: str
    errors: list[WptHtmlTreeError] | None
    spec_errors: list[WptHtmlTreeError] | None


class WptHtmlTreeDecision(WptHtmlTreeInput):
    # A corpus adjustment backed by fixture and specification links.
    reason: str
    spec: str
    fixture: str


class WptHtmlTreeExclusion(WptHtmlTreeDecision):
    # One input excluded because it requires script execution.
    document: str


class WptHtmlTreeCorpus(TypedDict):
    # The pinned cases, provenance, and reviewed adjustments.
    source: str
    revision: str
    files: list[str]
    fixture_counts: dict[str, int]
    applicable_fixture_counts: dict[str, int]
    error_adjustments: list[WptHtmlTreeDecision]
    exclusions: list[WptHtmlTreeExclusion]
    cases: list[WptHtmlTreeCase]


def load_wpt_html_tree() -> WptHtmlTreeCorpus:
    # Load the pinned tree-construction cases and provenance.
    path = Path(__file__).parents[1] / "tests" / "conformance" / "data" / "wpt_html_tree.json"
    return cast("WptHtmlTreeCorpus", json.loads(path.read_text(encoding="utf-8")))


__all__ = [
    "WptHtmlTreeCase",
    "WptHtmlTreeCorpus",
    "WptHtmlTreeDecision",
    "WptHtmlTreeError",
    "WptHtmlTreeExclusion",
    "WptHtmlTreeInput",
    "load_wpt_html_tree",
]
