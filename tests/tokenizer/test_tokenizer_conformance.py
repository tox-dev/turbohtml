"""Run the tokenizer against the html5lib-tests conformance suite.

Each ``.test`` file holds JSON cases with an input string and the expected token
stream (and, for some, a non-Data initial state and a last start tag). The
private ``_tokenize_states`` hook drives the state machine in the requested
state without the public tokenizer's tag-driven content switching, which is the
contract the suite assumes. Parse-error reporting is out of scope here: only the
token stream is compared.

Every case runs at all three input storage widths (the tokenizer core is
stamped per PyUnicode kind): the token stream must be invariant to how the
input happens to be stored.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from turbohtml import _html

TOKENIZER_DIR = Path(__file__).parents[1] / "html5lib-tests" / "tokenizer"

# CI always checks out the submodule (actions/checkout submodules: true); this guard fires only locally
if not TOKENIZER_DIR.is_dir() or not any(TOKENIZER_DIR.glob("*.test")):  # pragma: no cover
    msg = "submodule tests/html5lib-tests not checked out; run: git submodule update --init tests/html5lib-tests"
    raise RuntimeError(msg)

_DOUBLE_ESCAPE = re.compile(r"\\u([0-9A-Fa-f]{4})")


def _decode_double(text: str) -> str:
    return _DOUBLE_ESCAPE.sub(lambda match: chr(int(match.group(1), 16)), text)


def _decode_token(token: list[Any]) -> list[Any]:
    return [_decode_double(item) if isinstance(item, str) else item for item in token]


def _load_cases() -> list[Any]:
    cases = []
    for path in sorted(TOKENIZER_DIR.glob("*.test")):
        document = json.loads(path.read_text(encoding="utf-8"))
        for index, test in enumerate(document.get("tests", [])):
            double_escaped = test.get("doubleEscaped", False)
            text = _decode_double(test["input"]) if double_escaped else test["input"]
            expected = [
                _decode_token(token) if double_escaped else list(token)
                for token in test["output"]
                if token != "ParseError"  # noqa: S105  # a token-stream marker, not a password
            ]
            last_start_tag = test.get("lastStartTag")
            for state in test.get("initialStates", ["Data state"]):
                identifier = f"{path.stem}-{index}-{state.replace(' ', '_')}"
                cases.append(pytest.param(text, state, last_start_tag, expected, id=identifier))
    return cases


@pytest.mark.parametrize(("text", "state", "last_start_tag", "expected"), _load_cases())
def test_tokenizer_conformance(
    text: str, state: str, last_start_tag: str | None, expected: list[Any], storage_kind: int
) -> None:
    actual = _html._tokenize_states(text, state, last_start_tag, storage_kind)
    assert [list(token) for token in actual] == expected
