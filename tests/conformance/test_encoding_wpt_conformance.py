from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Final

import pytest

import turbohtml.detect  # ruff:ignore[unused-import]  # importing registers the whatwg-* codecs the cases decode through

if TYPE_CHECKING:
    from collections.abc import Iterator

    from _pytest.mark import ParameterSet

_CORPUS: Final[Path] = Path(__file__).parent / "data" / "wpt_encoding.json"

# The corpus ships with the source tree, so an absent one is a packaging bug rather than a missing oracle.
if not _CORPUS.is_file():
    _MISSING = f"{_CORPUS} is absent; regenerate it with tools/generate_wpt_encoding_corpus.py"
    raise RuntimeError(_MISSING)

_DATA: Final = json.loads(_CORPUS.read_text(encoding="utf-8"))


def _cases() -> Iterator[ParameterSet]:
    for case in _DATA["cases"]:
        label, raw = case["encoding"], case["bytes"]
        yield pytest.param(label, raw, case["text"], id=f"{label}-{case.get('arm') or raw}")


@pytest.mark.parametrize(("label", "raw", "text"), list(_cases()))
def test_a_wpt_case_decodes_the_way_the_browsers_suite_says(label: str, raw: str, text: str | None) -> None:
    # wpt spells an unmapped single-byte slot as null, which the spec's decode entry point reports as U+FFFD
    expected = "�" if text is None else text
    assert bytes.fromhex(raw).decode(f"whatwg-{label.casefold()}") == expected


def test_the_corpus_records_the_revision_it_came_from() -> None:
    # a corpus that cannot say which wpt it mirrors cannot be refreshed against a newer one
    assert len(_DATA["revision"]) == 40
    assert _DATA["source"] == "https://github.com/web-platform-tests/wpt"
    assert len(_DATA["cases"]) > 3000
