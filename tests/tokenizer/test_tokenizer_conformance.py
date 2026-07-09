"""Run the tokenizer against the html5lib-tests conformance suite.

Each ``.test`` file holds JSON cases with an input string, the expected token stream, and
the WHATWG parse errors the input must raise (and, for some, a non-Data initial state and a
last start tag). The private ``_tokenize_states`` hook drives the state machine in the
requested state without the public tokenizer's tag-driven content switching, which is the
contract the suite assumes, and returns the errors alongside the tokens.

Columns need translating. ``ParseError.col`` is 0-based, matching ``Token.col``, where the
suite counts from 1; and the suite counts a code point outside the BMP as the two UTF-16
code units JavaScript would see, where a Python column counts it once. So positions are
compared on BMP-only inputs, and the error codes and their order are compared everywhere.

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
            errors = [(error["code"], error["line"], error["col"]) for error in test.get("errors", [])]
            last_start_tag = test.get("lastStartTag")
            for state in test.get("initialStates", ["Data state"]):
                identifier = f"{path.stem}-{index}-{state.replace(' ', '_')}"
                cases.append((text, state, last_start_tag, expected, errors, identifier))
    return cases


def _token_cases() -> list[Any]:
    return [pytest.param(text, state, tag, tokens, id=name) for text, state, tag, tokens, _, name in _load_cases()]


def _error_cases() -> list[Any]:
    return [pytest.param(text, state, tag, errors, id=name) for text, state, tag, _, errors, name in _load_cases()]


@pytest.mark.parametrize(("text", "state", "last_start_tag", "expected"), _token_cases())
def test_tokenizer_conformance(
    text: str, state: str, last_start_tag: str | None, expected: list[Any], storage_kind: int
) -> None:
    actual, _ = _html._tokenize_states(text, state, last_start_tag, storage_kind)
    assert [list(token) for token in actual] == expected


@pytest.mark.parametrize(("text", "state", "last_start_tag", "errors"), _error_cases())
def test_tokenizer_parse_errors(
    text: str, state: str, last_start_tag: str | None, errors: list[Any], storage_kind: int
) -> None:
    _, raised = _html._tokenize_states(text, state, last_start_tag, storage_kind)
    assert [code for code, _, _ in raised] == [code for code, _, _ in errors]
    if all(ord(character) <= 0xFFFF for character in text):
        assert [(code, line, col + 1) for code, line, col in raised] == errors
