from __future__ import annotations

from typing import TYPE_CHECKING

from turbohtml._html import _parse_fragment, _parse_tree

if TYPE_CHECKING:
    from wpt_tree_corpus import WptHtmlTreeCase, WptHtmlTreeCorpus


def test_tree_construction(wpt_html_tree_corpus: WptHtmlTreeCorpus) -> None:
    exclusions = {
        (item["file"], item["data"], item["context"], item["scripting"]) for item in wpt_html_tree_corpus["exclusions"]
    }
    cases = [
        case
        for case in wpt_html_tree_corpus["cases"]
        if (case["file"], case["data"], case["context"], case["scripting"]) not in exclusions
    ]
    failures = [
        f"{case['file']}: #data {case['data']!r} (context={case['context']!r}, scripting={case['scripting']})\n"
        f"expected:\n{case['document']}\ngot:\n{_build(case)}"
        for case in cases
        if _build(case) != case["document"]
    ]
    assert not failures, f"{len(failures)}/{len(cases)} failing\n\n" + "\n\n".join(failures[:5])


def _build(case: WptHtmlTreeCase) -> str:
    if (context := case["context"]) is not None:
        return _parse_fragment(case["data"], context, bool(case["scripting"])).rstrip("\n")
    return _parse_tree(case["data"], bool(case["scripting"])).rstrip("\n")
