"""Validate unescape against the html5lib-tests character-reference corpus.

html5lib-tests is the shared HTML conformance suite used across implementations.
Its tokenizer ``*.test`` files encode, for each input, the token stream a spec
tokenizer must emit. turbohtml.unescape implements the same character-reference
resolution as :func:`python:html.unescape` (the Data-state algorithm), so for
every entity case here it must equal the standard library, and it must equal the
spec's character output wherever the standalone unescape semantics coincide with
full tokenization. Each case is generated as its own parametrized test.

The corpus lives in the ``tests/html5lib-tests`` git submodule; run
``git submodule update --init`` if these files are missing.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import TYPE_CHECKING

import turbohtml

if TYPE_CHECKING:
    import pytest

_TOKENIZER = Path(__file__).parents[1] / "html5lib-tests" / "tokenizer"
_FILES = ["entities.test", "namedEntities.test", "numericEntities.test"]

# CI always checks out the submodule (actions/checkout submodules: true); this guard fires only locally
if not _TOKENIZER.is_dir() or not any(_TOKENIZER.glob("*.test")):  # pragma: no cover
    msg = "submodule tests/html5lib-tests not checked out; run: git submodule update --init tests/html5lib-tests"
    raise RuntimeError(msg)


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Generate one test per html5lib character-reference case."""
    values: list[tuple[str, str]] = []
    ids: list[str] = []
    for filename in _FILES:
        suite = filename.removesuffix(".test")
        for case in json.loads((_TOKENIZER / filename).read_text(encoding="utf-8"))["tests"]:
            tokens = case["output"]
            if any(token[0] != "Character" for token in tokens):
                continue  # inputs that tokenize to tags/EOF are outside unescape's remit
            values.append((case["input"], "".join(token[1] for token in tokens)))
            ids.append(f"{suite}:{case['description']}")
    metafunc.parametrize(("text", "spec_output"), values, ids=ids)


def test_unescape_matches_html5lib(text: str, spec_output: str) -> None:
    result = turbohtml.unescape(text)
    assert result == html.unescape(text)  # turbohtml targets html.unescape semantics
    if html.unescape(text) == spec_output:  # where unescape coincides with full tokenization
        assert result == spec_output
