"""
turbohtml's WHATWG decoders validated against the browsers' own encoding suite, web-platform-tests.

wpt's git history runs to 2.6 GB, so rather than vendor it as a submodule the way ``encoding_rs`` and ``chardetng`` are,
``tools/generate_wpt_encoding_corpus.py`` extracts its two machine-readable decoder oracles into
``data/wpt_encoding.json`` and records the revision it took them from. Rerun that script against a fresh checkout to
track wpt HEAD.

The corpus covers the 128 high bytes of every legacy single-byte encoding and every state and error arm of the stateful
ISO-2022-JP decoder. It is narrower than the ``encoding_rs`` differential next door, which sweeps every one- and
two-byte sequence, and it is worth running anyway: these are expectations a standards body wrote down, not another
implementation's output, so the two suites fail independently.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Final

import pytest

import turbohtml.detect  # ruff:ignore[unused-import]  # importing registers the whatwg-* codecs the cases decode through

if TYPE_CHECKING:
    from collections.abc import Iterator

    from _pytest.mark import ParameterSet

_CORPUS: Final = Path(__file__).parent / "data" / "wpt_encoding.json"

# The corpus ships with the source tree, so an absent one is a packaging bug rather than a missing oracle.
if not _CORPUS.is_file():
    _MISSING = f"{_CORPUS} is absent; regenerate it with tools/generate_wpt_encoding_corpus.py"
    raise RuntimeError(_MISSING)

_DATA: Final = json.loads(_CORPUS.read_text(encoding="utf-8"))


def _cases() -> Iterator[ParameterSet]:
    for case in _DATA["cases"]:
        label, raw = case["encoding"], case["bytes"]
        arm = case.get("arm")
        yield pytest.param(label, raw, case["text"], id=f"{label}-{arm or raw}")


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
