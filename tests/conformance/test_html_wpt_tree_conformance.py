from __future__ import annotations

from typing import TYPE_CHECKING, cast

from turbohtml import _html, parse  # public serializers cannot reproduce WPT's namespace dump

if TYPE_CHECKING:
    from wpt_tree_corpus import (
        WptHtmlTreeCase,
        WptHtmlTreeCorpus,
        WptHtmlTreeError,
        WptHtmlTreeExclusion,
        WptHtmlTreeInput,
    )


def test_wpt_corpus_provenance_and_denominators(wpt_html_tree_corpus: WptHtmlTreeCorpus) -> None:
    assert {
        "source": wpt_html_tree_corpus["source"],
        "revision": wpt_html_tree_corpus["revision"],
        "files": len(wpt_html_tree_corpus["files"]),
        "source_cases": sum(wpt_html_tree_corpus["fixture_counts"].values()),
        "applicable_cases": sum(wpt_html_tree_corpus["applicable_fixture_counts"].values()),
        "error_adjustments": len(wpt_html_tree_corpus["error_adjustments"]),
        "exclusions": len(wpt_html_tree_corpus["exclusions"]),
    } == {
        "source": (
            "https://github.com/web-platform-tests/wpt/tree/"
            "4830edb033cb486fd0cd6f85b5e937cfc718704d/html/syntax/parsing/resources"
        ),
        "revision": "4830edb033cb486fd0cd6f85b5e937cfc718704d",
        "files": 61,
        "source_cases": 1_920,
        "applicable_cases": 1_916,
        "error_adjustments": 8,
        "exclusions": 4,
    }


def test_wpt_corpus_records_normative_sources(wpt_html_tree_corpus: WptHtmlTreeCorpus) -> None:
    assert {
        "error_specs": {item["spec"] for item in wpt_html_tree_corpus["error_adjustments"]},
        "script_specs": {item["spec"] for item in wpt_html_tree_corpus["exclusions"]},
        "script_fixtures": {item["fixture"] for item in wpt_html_tree_corpus["exclusions"]},
    } == {
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


def test_wpt_tree(wpt_html_tree_corpus: WptHtmlTreeCorpus) -> None:
    cases = _applicable(wpt_html_tree_corpus)
    failures = [case for case in cases if _tree(case) != case["document"]]
    assert not failures, f"{len(failures)}/{len(cases)} tree mismatches: {failures[:5]!r}"


def _applicable(corpus: WptHtmlTreeCorpus) -> list[WptHtmlTreeCase]:
    exclusions = {(item["file"], item["data"], item["context"], item["scripting"]) for item in corpus["exclusions"]}
    return [
        case
        for case in corpus["cases"]
        if (case["file"], case["data"], case["context"], case["scripting"]) not in exclusions
    ]


def _tree(case: WptHtmlTreeInput | WptHtmlTreeExclusion) -> str:
    if (context := case["context"]) is not None:
        result = _html._parse_fragment(case["data"], context, bool(case["scripting"]))
    else:
        result = _html._parse_tree(case["data"], bool(case["scripting"]))
    return result.rstrip("\n")


def test_wpt_tree_count(wpt_html_tree_corpus: WptHtmlTreeCorpus) -> None:
    cases = _applicable(wpt_html_tree_corpus)
    assert sum(_tree(case) == case["document"] for case in cases) == 1_916


def test_wpt_exact_document_errors(wpt_html_tree_corpus: WptHtmlTreeCorpus) -> None:
    cases = [
        case
        for case in _applicable(wpt_html_tree_corpus)
        if case["context"] is None and case["spec_errors"] is not None
    ]
    failures = [
        case for case in cases if not _errors_match(cast("list[WptHtmlTreeError]", case["spec_errors"]), _errors(case))
    ]
    assert not failures, f"{len(failures)}/{len(cases)} error mismatches: {failures[:5]!r}"


def _errors_match(expected: list[WptHtmlTreeError], actual: list[WptHtmlTreeError]) -> bool:
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


def _errors(case: WptHtmlTreeInput) -> list[WptHtmlTreeError]:
    return [
        {"code": error.code, "line": error.line, "col": error.col + 1, "end_line": None, "end_col": None}
        for error in parse(case["data"], scripting=bool(case["scripting"])).errors
    ]


def test_wpt_script_exclusion_without_javascript(wpt_html_tree_corpus: WptHtmlTreeCorpus) -> None:
    exclusions = wpt_html_tree_corpus["exclusions"]
    failures = [exclusion for exclusion in exclusions if _tree(exclusion) != exclusion["document"]]
    assert not failures, f"{len(failures)}/{len(exclusions)} unexpected matches: {failures!r}"
