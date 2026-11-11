from __future__ import annotations

import json
import re
import runpy
from pathlib import Path
from typing import TYPE_CHECKING, Final, TypeAlias

import pytest

from turbohtml._html import _tokenize_states

if TYPE_CHECKING:
    from collections.abc import Callable

    from pytest_mock import MockerFixture


_TOKENIZER_DIR: Final[Path] = Path(__file__).parents[2] / "html5lib-tests" / "tokenizer"

if not _TOKENIZER_DIR.is_dir() or not any(_TOKENIZER_DIR.glob("*.test")):
    msg = "submodule tests/html5lib-tests not checked out; run: git submodule update --init tests/html5lib-tests"
    raise RuntimeError(msg)


def test_missing_tokenizer_submodule_reports_checkout_command(mocker: MockerFixture) -> None:
    is_dir = Path.is_dir
    mocker.patch.object(
        Path,
        "is_dir",
        autospec=True,
        side_effect=lambda path: path != _TOKENIZER_DIR and is_dir(path),
    )
    with pytest.raises(RuntimeError, match="git submodule update --init tests/html5lib-tests"):
        runpy.run_path(__file__)


_DOUBLE_ESCAPE: Final[re.Pattern[str]] = re.compile(r"\\u([0-9A-Fa-f]{4})")
_TokenPart: TypeAlias = str | bool | dict[str, str] | None
_ExpectedToken: TypeAlias = list[_TokenPart]
_ParseError: TypeAlias = tuple[str, int, int]
_TokenizerCase: TypeAlias = tuple[str, str, str | None, list[_ExpectedToken], list[_ParseError], str]


def _decode_double(text: str) -> str:
    return _DOUBLE_ESCAPE.sub(lambda match: chr(int(match.group(1), 16)), text)


def _decode_token(token: _ExpectedToken) -> _ExpectedToken:
    return [_decode_double(item) if isinstance(item, str) else item for item in token]


def _living_pi_expectation(
    text: str, state: str, expected: list[_ExpectedToken], errors: list[_ParseError]
) -> tuple[list[_ExpectedToken], list[_ParseError]]:
    if state != "Data state" or not text.startswith("<?"):
        return expected, errors
    if not (first := text[2:3]):
        return [], [("eof-in-processing-instruction", 1, 3)]
    pi_token_overrides: dict[str, list[_ExpectedToken]] = {
        "<?namespace>": [["ProcessingInstruction", "namespace", ""]],
        "<?foo-->": [["ProcessingInstruction", "foo--", ""]],
    }
    if first.isascii() and (first.isalpha() or first == "_"):
        if text.endswith(">"):
            return pi_token_overrides.get(text, expected), []
        return [], [("eof-in-processing-instruction", 1, len(text) + 1)]
    retained = [
        error
        for error in errors
        if error[0] not in {"control-character-in-input-stream", "unexpected-question-mark-instead-of-tag-name"}
    ]
    preprocessing = [error for error in errors if error[0] == "control-character-in-input-stream"]
    return expected, [*preprocessing, ("invalid-first-character-of-processing-instruction-target", 1, 3), *retained]


def _load_cases() -> list[_TokenizerCase]:
    cases: list[_TokenizerCase] = []
    for path in sorted(_TOKENIZER_DIR.glob("*.test")):
        for index, test in enumerate(json.loads(path.read_text(encoding="utf-8")).get("tests", [])):
            double_escaped = test.get("doubleEscaped", False)
            text = _decode_double(test["input"]) if double_escaped else test["input"]
            expected = [
                _decode_token(token) if double_escaped else list(token)
                for token in test["output"]
                if token != "ParseError"  # ruff:ignore[hardcoded-password-string]  # a token-stream marker, not a password
            ]
            errors = [(error["code"], error["line"], error["col"]) for error in test.get("errors", [])]
            last_start_tag = test.get("lastStartTag")
            for state in test.get("initialStates", ["Data state"]):
                expected, errors = _living_pi_expectation(text, state, expected, errors)
                identifier = f"{path.stem}-{index}-{state.replace(' ', '_')}"
                cases.append((text, state, last_start_tag, expected, errors, identifier))
    return cases


def _tokenizer_cases() -> list[pytest.ParameterSet]:
    return [pytest.param(case, id=case[-1]) for case in _load_cases()]


@pytest.mark.parametrize("case", _tokenizer_cases())
def test_tokenizer_conformance(case: _TokenizerCase, storage_kind: int) -> None:
    text: Final = case[0]
    errors: Final = case[4]
    result: Final = _tokenize_states(text, case[1], case[2], storage_kind)
    check_positions: Final = all(ord(character) <= 0xFFFF for character in text)
    assert (
        [list(token) for token in result[0]],
        [code for code, _, _ in result[1]],
        [(code, line, col + 1) for code, line, col in result[1] if check_positions],
    ) == (case[3], [code for code, _, _ in errors], [error for error in errors if check_positions])


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("x]", "x]", id="bracket-eof"),
        pytest.param("x]]", "x]]", id="double-bracket-eof"),
        pytest.param("x]y", "x]y", id="bracket-text"),
        pytest.param("x]]]>y", "x]y", id="bracket-run-close"),
        pytest.param("x]]y", "x]]y", id="double-bracket-text"),
    ],
)
def test_cdata_section_edges(text: str, expected: str, storage_kind: int) -> None:
    tokens, _ = _tokenize_states(text, "CDATA section state", None, storage_kind)
    assert tokens == [("Character", expected)]


@pytest.mark.parametrize(
    ("state", "text", "expected"),
    [
        pytest.param("PLAINTEXT state", "a\nb", "a\nb", id="plaintext-newline"),
        pytest.param("PLAINTEXT state", "a\x00b", "a�b", id="plaintext-nul"),
        pytest.param("RCDATA state", "a\x00b", "a�b", id="rcdata-nul"),
        pytest.param("RCDATA state", "a\nb", "a\nb", id="rcdata-newline"),
        pytest.param("RAWTEXT state", "a\x00b", "a�b", id="rawtext-nul"),
        pytest.param("RAWTEXT state", "a\nb", "a\nb", id="rawtext-newline"),
        pytest.param("Script data state", "a\x00b", "a�b", id="script-nul"),
        pytest.param("Script data state", "a\nb", "a\nb", id="script-newline"),
        pytest.param("CDATA section state", "a\nb]", "a\nb]", id="cdata-newline"),
    ],
)
def test_text_run_breaks_on_special_characters(state: str, text: str, expected: str, storage_kind: int) -> None:
    tokens, _ = _tokenize_states(text, state, None, storage_kind)
    assert tokens == [("Character", expected)]


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        pytest.param(("x", "Data state"), "x", id="default-last-tag"),
        pytest.param(("x", "RCDATA state", ""), "x", id="empty-last-tag"),
        pytest.param(("</xy>", "RCDATA state", "xő"), "</xy>", id="rcdata-end-tag-name-width-mismatch"),
    ],
)
def test_tokenize_states_returns_characters(args: tuple[str, ...], expected: str) -> None:
    tokens, _ = _tokenize_states(*args)  # ty: ignore[invalid-argument-type]  # variadic str args
    assert tokens == [("Character", expected)]


@pytest.mark.parametrize(
    ("call", "exc", "match"),
    [
        pytest.param(
            lambda: _tokenize_states(123, "Data state"),  # ty: ignore[invalid-argument-type]  # tests the C guard
            TypeError,
            None,
            id="states-non-str-text",
        ),
        pytest.param(
            lambda: _tokenize_states("x", "Bogus state", None),
            ValueError,
            "unknown initial state",
            id="states-unknown-state",
        ),
        pytest.param(
            lambda: _tokenize_states("x", "Data state", 5),  # ty: ignore[invalid-argument-type]  # tests the C guard
            TypeError,
            "last_start_tag",
            id="states-non-str-last-tag",
        ),
        pytest.param(
            lambda: _tokenize_states("x", "Data state", None, 3),
            ValueError,
            "storage_kind",
            id="states-bad-storage-kind",
        ),
    ],
)
def test_state_binding_rejects_bad_args(call: Callable[[], object], exc: type[Exception], match: str | None) -> None:
    with pytest.raises(exc, match=match):
        call()
