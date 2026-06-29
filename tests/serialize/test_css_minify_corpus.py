"""Golden-snapshot and round-trip safety of the CSS minifier over tdewolff's corpus plus a branch-coverage corpus.

``data/css_minify_golden.json`` is a reviewed snapshot of the validated engine: one ``[source, stylesheet, inline]``
row per input, spanning tdewolff's real TestCSS/TestCSSInline cases and inputs engineered to exercise every branch of
the C engine. Pinning the exact output of each makes a regression in any value-engine or structural branch change an
output and fail here -- an idempotence check alone would pass even if a transform silently became a no-op.

A handful of tdewolff's cases are malformed past recovery (an unterminated string or ``url(``, an unbalanced
``calc(``), so error recovery has no fixed point; those are pinned for output but excluded from the round-trip check.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

import pytest

from turbohtml.clean import minify_css, minify_css_inline

_GOLDEN: Final[list[list[str]]] = json.loads(
    (Path(__file__).parent / "data" / "css_minify_golden.json").read_text(encoding="utf-8")
)

# Malformed/invalid inputs with no closing delimiter or balance: error recovery keeps the broken tail verbatim, which
# is not a fixed point under re-minification. Output is still deterministic and pinned; only round-trip safety is moot.
_UNSTABLE: Final[frozenset[str]] = frozenset({
    "a{a:)'''", "{d:url( \n  \n\t0", "{d:urL(     '0", '{-ms-filter:"',
    "a{width:calc((1px + 2px}", "a{width:calc((1px}", "a{width:calc((", "a{width:calc((1px+2px",
    'a{x:"abc\\', "a{x:url(", 'a{src:local("', "a{color:rgba(10 20 30 .5)}", "a{flex:1 0 %}",
})  # fmt: skip


@pytest.mark.parametrize(
    ("source", "stylesheet", "inline"),
    [pytest.param(*row, id=row[0][:40]) for row in _GOLDEN],
)
def test_minify_matches_golden(source: str, stylesheet: str, inline: str) -> None:
    assert (minify_css(source), minify_css_inline(source)) == (stylesheet, inline)


@pytest.mark.parametrize(
    ("stylesheet", "inline"),
    [pytest.param(row[1], row[2], id=row[0][:40]) for row in _GOLDEN if row[0] not in _UNSTABLE],
)
def test_minify_output_is_a_fixed_point(stylesheet: str, inline: str) -> None:
    assert (minify_css(stylesheet), minify_css_inline(inline)) == (stylesheet, inline)
