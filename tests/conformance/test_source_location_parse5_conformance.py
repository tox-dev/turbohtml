"""Validate ``parse(source_locations=True)`` against parse5's own location-info corpus.

parse5 is the reference implementation of ``sourceCodeLocationInfo``, the model :attr:`turbohtml.Node.source_location`
mirrors. Its repository ships five real-world documents under ``test/data/location-info`` (CERN, a DX portal, the parse5
GitHub page, the WHATWG HTML spec, and a MediaWiki article) that its own suite parses with location info on. This module
runs the same five documents through both parsers and compares every element's start-tag span, end-tag span, and
per-attribute spans offset-for-offset.

parse5 is JavaScript, so it runs through a committed Node runner (``tools/bench/node/parse5_location_runner.js``) over
stdin, the way the parse5 speed baseline and the DOMPurify oracle do. The runner emits, for every element parse5 gives a
start tag, a span as ``[startLine, startCol, startOffset, endLine, endCol, endOffset]``. parse5 columns are 1-based and
turbohtml's are 0-based, so the comparison subtracts one from each parse5 column; lines and code-point offsets match
directly. Both sides key elements by start-tag start offset, an unambiguous anchor that pairs the two trees without
walking them in lockstep. parse5 defaults its scripting flag on -- ``<noscript>`` content is raw text, not elements --
so turbohtml parses with ``scripting=True`` to line the two trees up; every element one produces the other produces at
the same offset.

The suite is a git submodule (``tests/conformance/parse5``); the runner needs Node with parse5 installed
(``npm install`` in ``tools/bench/node``). The two absences differ: a missing submodule is a checkout mistake, so the
module raises rather than hides the gap, while a missing Node binary is an environment turbohtml cannot assume, so the
tests skip the way the ``*_differential.py`` oracles do. This file is in ``[tool.coverage] run.omit``, so the skip keeps
the coverage gate environment-independent.
"""

from __future__ import annotations

import json
import shutil
import subprocess  # noqa: S404  # drives the committed parse5 Node runner over a fixed argv, not external input
from operator import itemgetter
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

import pytest

from turbohtml import Element, parse

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from turbohtml._locations import SourceSpan

_SUBMODULE = Path(__file__).parent / "parse5"
_DATA = _SUBMODULE / "test" / "data" / "location-info"
_RUNNER = Path(__file__).parents[2] / "tools" / "bench" / "node" / "parse5_location_runner.js"
_CORPORA = ("cern", "dx", "github-parse5", "whatwg-html", "wiki-42")

Span = tuple[int, int, int, int, int, int]

# The parse5 corpus is a vendored submodule, so its absence is a checkout mistake, not a runtime the environment may
# lack -- error loudly rather than skip. Node is a runtime tool, so a missing binary stays a legitimate skip below.
if not (_DATA / "cern" / "data.html").exists():  # pragma: no cover
    message = (
        "submodule tests/conformance/parse5 not checked out; run: git submodule update --init tests/conformance/parse5"
    )
    raise RuntimeError(message)

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


class _Located(TypedDict):
    """One element's spans, both engines normalized to turbohtml's convention and keyed by start-tag start offset."""

    tag: str
    start_tag: Span
    end_tag: Span | None
    attrs: dict[str, Span]


def _corpus(name: str) -> str:
    """The newline-normalized document, the source both parsers' offsets index into."""
    return (_DATA / name / "data.html").read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")


def _p5_span(raw: list[int] | None) -> Span | None:
    """Adapt a parse5 span to turbohtml's convention: columns drop from 1-based to 0-based, lines and offsets stay."""
    if raw is None:
        return None
    start_line, start_col, start_off, end_line, end_col, end_off = raw
    return (start_line, start_col - 1, start_off, end_line, end_col - 1, end_off)


def _parse5(html: str) -> dict[int, _Located]:
    """Run the document through parse5, one record per located element; skips if parse5 is not installed."""
    try:
        proc = subprocess.run(  # noqa: S603  # fixed argv, not external input; the stdin is test corpus
            ["node", str(_RUNNER)],  # noqa: S607  # node is resolved from PATH, the committed runner from the repo
            input=html,
            capture_output=True,
            text=True,
            check=True,
            timeout=120,
        )
    except subprocess.CalledProcessError as error:
        pytest.skip(f"parse5 Node runner failed (npm install in tools/bench/node?): {error.stderr.strip()}")
    located: dict[int, _Located] = {}
    for element in json.loads(proc.stdout)["elements"]:
        start_tag = _p5_span(element["startTag"])
        assert start_tag is not None  # the runner only emits elements parse5 gives a start tag
        located[element["key"]] = {
            "tag": element["tag"],
            "start_tag": start_tag,
            "end_tag": _p5_span(element["endTag"]),
            "attrs": {name.lower(): span for name, raw in element["attrs"].items() if (span := _p5_span(raw))},
        }
    return located


def _tb_span(span: SourceSpan) -> Span:
    return (span.start_line, span.start_col, span.start_offset, span.end_line, span.end_col, span.end_offset)


def _turbohtml(html: str) -> dict[int, _Located]:
    """Every located element keyed by its start-tag start offset, matching scripting to parse5's default."""
    document = parse(html, source_locations=True, scripting=True)
    located: dict[int, _Located] = {}
    for element in document.descendants:
        if not isinstance(element, Element):
            continue
        location = element.source_location
        if location is None:
            continue
        located.setdefault(
            location.start_tag.start_offset,
            {
                "tag": element.tag,
                "start_tag": _tb_span(location.start_tag),
                "end_tag": _tb_span(location.end_tag) if location.end_tag is not None else None,
                "attrs": {name.lower(): _tb_span(span) for name, span in location.attrs.items()},
            },
        )
    return located


@pytest.fixture(scope="module", params=_CORPORA, ids=_CORPORA)
def paired(request: pytest.FixtureRequest) -> tuple[dict[int, _Located], dict[int, _Located]]:
    """One corpus parsed by both engines, cached so parse5's subprocess runs once per document."""
    html = _corpus(request.param)
    return _parse5(html), _turbohtml(html)


def _mismatched(
    expected: Mapping[int, _Located], actual: Mapping[int, _Located], span_of: Callable[[_Located], object]
) -> dict[int, tuple[object, object]]:
    """Every shared element whose extracted span differs between the two engines."""
    return {
        key: (span_of(expected[key]), span_of(actual[key]))
        for key in expected.keys() & actual.keys()
        if span_of(expected[key]) != span_of(actual[key])
    }


def test_every_element_aligns_between_the_two_parsers(
    paired: tuple[dict[int, _Located], dict[int, _Located]],
) -> None:
    expected, actual = paired
    assert expected  # the corpus is non-trivial, so a passing comparison is not vacuous
    assert set(actual) == set(expected)


def test_start_tag_spans_match_parse5(paired: tuple[dict[int, _Located], dict[int, _Located]]) -> None:
    expected, actual = paired
    assert _mismatched(expected, actual, itemgetter("start_tag")) == {}


def test_end_tag_spans_match_parse5(paired: tuple[dict[int, _Located], dict[int, _Located]]) -> None:
    expected, actual = paired
    assert _mismatched(expected, actual, itemgetter("end_tag")) == {}


def test_attribute_spans_match_parse5(paired: tuple[dict[int, _Located], dict[int, _Located]]) -> None:
    expected, actual = paired
    assert _mismatched(expected, actual, itemgetter("attrs")) == {}
