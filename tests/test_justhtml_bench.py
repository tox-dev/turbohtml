from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Final, cast

import pytest

_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "tools"))

from bench.migration import discover_labels  # ruff:ignore[module-import-not-at-top-of-file]

_COMPETITORS: Final[Path] = _ROOT / "tools" / "bench" / "competitors"
_DEVELOPMENT_FEEDS: Final[Path] = _ROOT / "docs" / "development" / "bench"
_MIGRATION_FEED: Final[Path] = _ROOT / "docs" / "migration" / "bench" / "justhtml.json"
_FEEDS: Final[dict[str, str]] = {
    "class-edit": "editing-2",
    "edit": "editing",
    "extract-attr": "extraction",
    "extract-text": "extraction-2",
    "find": "querying",
    "fragment": "fragment-parsing",
    "linkify": "linkify",
    "match": "matching",
    "navigate": "tree-navigation",
    "parse": "parsing",
    "sanitize": "sanitize",
    "select": "querying-2",
    "serialize": "serializing",
    "strip-remove": "editing-5",
    "strip-tags": "editing-6",
    "text-content": "text-content",
}


def test_justhtml_registry_contains_only_equivalent_operations() -> None:
    assert discover_labels(_COMPETITORS)["justhtml"] == dict.fromkeys(_FEEDS, "JustHTML")


@pytest.mark.parametrize(
    ("operation", "feed_name"),
    tuple(pytest.param(operation, feed_name, id=operation) for operation, feed_name in _FEEDS.items()),
)
def test_justhtml_development_feed_is_published(operation: str, feed_name: str) -> None:
    feed = _feed(_DEVELOPMENT_FEEDS / f"{feed_name}.json")
    assert "JustHTML" in cast("list[str]", feed["parties"]), operation


def test_justhtml_migration_feed_lists_both_parsers() -> None:
    assert _feed(_MIGRATION_FEED)["parties"] == ["turbohtml", "JustHTML"]


def test_justhtml_migration_feed_has_aligned_measurements() -> None:
    feed = _feed(_MIGRATION_FEED)
    rows = cast("list[list[object]]", feed["rows"])
    spread = cast("list[list[object]]", feed["spread"])
    assert (
        len(rows),
        len(spread),
        {(len(row), len(noise)) for row, noise in zip(rows, spread, strict=True)},
    ) == (61, 61, {(3, 3)})


def _feed(path: Path) -> dict[str, object]:
    return cast("dict[str, object]", json.loads(path.read_text(encoding="utf-8")))
