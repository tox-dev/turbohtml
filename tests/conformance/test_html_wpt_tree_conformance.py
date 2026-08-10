from __future__ import annotations

import json
from pathlib import Path
from typing import Final, TypedDict, cast

import pytest

from turbohtml import _html, parse  # public serializers cannot reproduce WPT's namespace dump


class _Error(TypedDict):
    code: str
    line: int
    col: int
    end_line: int | None
    end_col: int | None


class _Input(TypedDict):
    file: str
    data: str
    context: str | None
    scripting: bool | None


class _Case(_Input):
    document: str
    spec_errors: list[_Error] | None
    spec_document: str


class _Decision(_Input):
    reason: str
    spec: str
    fixture: str


class _Exclusion(_Decision):
    document: str


class _Corpus(TypedDict):
    source: str
    revision: str
    files: list[str]
    fixture_counts: dict[str, int]
    applicable_fixture_counts: dict[str, int]
    normative_adjustments: list[_Decision]
    error_adjustments: list[_Decision]
    exclusions: list[_Exclusion]
    cases: list[_Case]


_CORPUS: Final[_Corpus] = cast(
    "_Corpus",
    json.loads((Path(__file__).parent / "data" / "wpt_html_tree.json").read_text(encoding="utf-8")),
)
_EXCLUSION_KEYS: Final[frozenset[tuple[str, str, str | None, bool | None]]] = frozenset(
    (item["file"], item["data"], item["context"], item["scripting"]) for item in _CORPUS["exclusions"]
)
_APPLICABLE: Final[tuple[_Case, ...]] = tuple(
    case
    for case in _CORPUS["cases"]
    if (case["file"], case["data"], case["context"], case["scripting"]) not in _EXCLUSION_KEYS
)
_ERROR_CASES: Final[tuple[_Case, ...]] = tuple(
    case for case in _APPLICABLE if case["context"] is None and case["spec_errors"] is not None
)


def test_wpt_corpus_provenance_and_denominators() -> None:
    assert {
        "source": _CORPUS["source"],
        "revision": _CORPUS["revision"],
        "files": len(_CORPUS["files"]),
        "source_cases": sum(_CORPUS["fixture_counts"].values()),
        "applicable_cases": sum(_CORPUS["applicable_fixture_counts"].values()),
        "adjustments": len(_CORPUS["normative_adjustments"]),
        "error_adjustments": len(_CORPUS["error_adjustments"]),
        "exclusions": len(_CORPUS["exclusions"]),
    } == {
        "source": (
            "https://github.com/web-platform-tests/wpt/tree/"
            "4830edb033cb486fd0cd6f85b5e937cfc718704d/html/syntax/parsing/resources"
        ),
        "revision": "4830edb033cb486fd0cd6f85b5e937cfc718704d",
        "files": 61,
        "source_cases": 1_920,
        "applicable_cases": 1_916,
        "adjustments": 8,
        "error_adjustments": 8,
        "exclusions": 4,
    }


def test_wpt_corpus_records_normative_sources() -> None:
    assert {
        "tree_specs": {item["spec"] for item in _CORPUS["normative_adjustments"]},
        "tree_fixtures": {item["fixture"] for item in _CORPUS["normative_adjustments"]},
        "error_specs": {item["spec"] for item in _CORPUS["error_adjustments"]},
        "script_specs": {item["spec"] for item in _CORPUS["exclusions"]},
        "script_fixtures": {item["fixture"] for item in _CORPUS["exclusions"]},
    } == {
        "tree_specs": {"https://html.spec.whatwg.org/multipage/parsing.html#parsing-main-inforeign"},
        "tree_fixtures": {
            (
                "https://github.com/web-platform-tests/wpt/blob/"
                "4830edb033cb486fd0cd6f85b5e937cfc718704d/html/syntax/parsing/resources/"
                "foreign-fragment.dat#L565-L612"
            ),
            (
                "https://github.com/web-platform-tests/wpt/blob/"
                "4830edb033cb486fd0cd6f85b5e937cfc718704d/html/syntax/parsing/resources/tests26.dat#L395-L453"
            ),
        },
        "error_specs": {"https://html.spec.whatwg.org/multipage/parsing.html#processing-instruction-open-state"},
        "script_specs": {"https://html.spec.whatwg.org/multipage/scripting.html#script-processing-model"},
        "script_fixtures": {
            (
                "https://github.com/web-platform-tests/wpt/blob/"
                "4830edb033cb486fd0cd6f85b5e937cfc718704d/html/syntax/parsing/resources/"
                "scripted_adoption01.dat#L1-L16"
            ),
            (
                "https://github.com/web-platform-tests/wpt/blob/"
                "4830edb033cb486fd0cd6f85b5e937cfc718704d/html/syntax/parsing/resources/"
                "scripted_ark.dat#L1-L27"
            ),
            (
                "https://github.com/web-platform-tests/wpt/blob/"
                "4830edb033cb486fd0cd6f85b5e937cfc718704d/html/syntax/parsing/resources/"
                "scripted_webkit01.dat#L1-L12"
            ),
            (
                "https://github.com/web-platform-tests/wpt/blob/"
                "4830edb033cb486fd0cd6f85b5e937cfc718704d/html/syntax/parsing/resources/"
                "scripted_webkit01.dat#L14-L30"
            ),
        },
    }


@pytest.mark.parametrize(
    "case",
    tuple(pytest.param(case, id=f"{case['file']}:{index}") for index, case in enumerate(_APPLICABLE)),
)
def test_wpt_spec_correct_tree(case: _Case) -> None:
    assert _tree(case) == case["spec_document"]


def test_wpt_raw_tree_count() -> None:
    assert sum(_tree(case) == case["document"] for case in _APPLICABLE) == 1_908


@pytest.mark.parametrize(
    "case",
    tuple(pytest.param(case, id=f"{case['file']}:{index}") for index, case in enumerate(_ERROR_CASES)),
)
def test_wpt_exact_document_errors(case: _Case) -> None:
    expected = cast("list[_Error]", case["spec_errors"])
    assert _errors_match(expected, _errors(case))


@pytest.mark.parametrize(
    "exclusion",
    tuple(
        pytest.param(exclusion, id=f"{exclusion['file']}:{index}")
        for index, exclusion in enumerate(_CORPUS["exclusions"])
    ),
)
def test_wpt_script_exclusion_without_javascript(exclusion: _Exclusion) -> None:
    assert _tree(exclusion) == exclusion["document"]


def _tree(case: _Input) -> str:
    if (context := case["context"]) is not None:
        result = _html._parse_fragment(case["data"], context, bool(case["scripting"]))
    else:
        result = _html._parse_tree(case["data"], bool(case["scripting"]))
    return result.rstrip("\n")


def _errors(case: _Input) -> list[_Error]:
    return [
        {"code": error.code, "line": error.line, "col": error.col + 1, "end_line": None, "end_col": None}
        for error in parse(case["data"], scripting=bool(case["scripting"])).errors
    ]


def _errors_match(expected: list[_Error], actual: list[_Error]) -> bool:
    if len(expected) != len(actual):
        return False
    for wanted, raised in zip(expected, actual, strict=True):
        if wanted["code"] != raised["code"]:
            return False
        position = raised["line"], raised["col"]
        start = wanted["line"], wanted["col"]
        if (end_line := wanted["end_line"]) is None:
            if position != start:
                return False
        elif (end_col := wanted["end_col"]) is None or not start <= position <= (end_line, end_col):
            return False
    return True
