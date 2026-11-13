from __future__ import annotations

from typing import TYPE_CHECKING

from turbohtml._html import _parse_fragment, _parse_tree

if TYPE_CHECKING:
    from wpt_tree_corpus import WptHtmlTreeCase, WptHtmlTreeCorpus, WptHtmlTreeExclusion, WptHtmlTreeInput


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
        result = _parse_fragment(case["data"], context, bool(case["scripting"]))
    else:
        result = _parse_tree(case["data"], bool(case["scripting"]))
    return result.rstrip("\n")


def test_wpt_tree_count(wpt_html_tree_corpus: WptHtmlTreeCorpus) -> None:
    cases = _applicable(wpt_html_tree_corpus)
    assert sum(_tree(case) == case["document"] for case in cases) == 1_916


def test_wpt_script_exclusion_without_javascript(wpt_html_tree_corpus: WptHtmlTreeCorpus) -> None:
    exclusions = wpt_html_tree_corpus["exclusions"]
    failures = [exclusion for exclusion in exclusions if _tree(exclusion) != exclusion["document"]]
    assert not failures, f"{len(failures)}/{len(exclusions)} unexpected matches: {failures!r}"
