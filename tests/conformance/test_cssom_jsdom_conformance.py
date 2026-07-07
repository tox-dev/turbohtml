"""Differential conformance validation of :func:`turbohtml.cssom.computed_style` (issue #546).

The CSSOM cascade cannot be validated against the web-platform-tests css/ suite directly: those are ``testharness.js``
tests that assert on ``getComputedStyle`` inside a browser and never run against a Python library. Following the
Tier-2 (browser-JS DOM feature) pattern used for the other differential suites, this validates turbohtml against a
reference implementation instead -- jsdom's ``getComputedStyle`` (backed by cssstyle), which passes most of WPT's CSSOM
suite and resolves the same author-origin cascade turbohtml does. A Node runner
(``tools/bench/node/cssom_jsdom_runner.js``) loads each input document and returns
``getComputedStyle(el).getPropertyValue(prop)``; the Python side computes the same through turbohtml; the two are
compared over a shared corpus that exercises every cascade axis turbohtml implements -- specificity, source order,
``!important``, the style attribute, inheritance, ``inherit``/``initial``/``unset``/``revert``, initial values, and
shorthand expansion.

Two checks run per case:

- ``test_cssom_matches_spec`` asserts turbohtml's value against a spec-derived expectation for every check. It needs no
  Node, so it holds the coverage gate on the free-threaded, 3.15, and Windows cells where the oracle is absent. The
  expectations are the CSS Cascade winners (which declaration wins), not turbohtml's output read back, so they are not
  self-serving.
- ``test_cssom_matches_jsdom`` cross-checks turbohtml against jsdom for every check where jsdom is a faithful oracle. It
  skips cleanly when Node or jsdom is not installed.

Documented boundaries, argued from the spec rather than papered over:

- **Colors are not serialized.** turbohtml returns the *computed* value in the cascade sense and does not run a color
  engine, so ``blue`` stays ``blue`` where jsdom returns ``rgb(0, 0, 255)`` (:doc:`explanation </explanation/cssom>`).
  Color-valued properties are compared after normalizing both sides to an ``(r, g, b, a)`` tuple, so the cascade winner
  is still validated; only the serialization is ignored. The system color ``canvastext`` (``color``'s initial value)
  normalizes to opaque black, matching a default (light) theme.
- **No user-agent stylesheet.** turbohtml's cascade is author-origin only; each property's initial value stands in for
  the UA default (CSS Cascade 4 §6.4, and the explanation doc). jsdom applies a UA sheet (``p { display: block }`` and
  the like), so every compared property is one an author declaration governs on the target element, keeping the UA
  origin out of the diff.
- **Curated shorthand set.** turbohtml expands only the distributive shorthands its docs enumerate -- ``margin``,
  ``padding``, ``border-width``/``style``/``color``, ``overflow``. The grammar-parsed shorthands (``border``,
  ``border-top`` and siblings, ``outline``, ``font``, ``background``, ``list-style``) are out of scope and drop their
  declaration; ``test_cssom_curated_shorthand_boundary`` records that as an ``xfail`` against jsdom.
- **jsdom is non-conformant for the ``overflow`` shorthand.** jsdom's ``getComputedStyle`` does not propagate
  ``overflow`` to ``overflow-x``/``overflow-y`` (it returns the initial ``visible``); turbohtml expands it per
  CSS Overflow 3 §2.1, matching real browsers. Those longhands are validated against the spec only (``oracle=False``).
- **jsdom does not resolve ``revert``.** cssstyle returns the literal ``revert`` keyword; turbohtml resolves it to the
  unset behavior (CSS Cascade 4 §7.6). ``revert`` cases are validated against the spec only.
- **css-cascade-5 ``revert-rule`` is unsupported.** turbohtml implements the cascade-4 keyword set; ``revert-rule`` is
  left as a literal value, so no such case is asserted as passing.

Input fixtures marked ``WPT`` are transcribed from web-platform-tests ``css/css-cascade`` at commit
``ffd26826c02b5ce6d43f77e3822ac661854dd454`` (a sparse checkout of ``css/css-cascade`` and ``css/cssom``); their
expected values are the ones the WPT test itself asserts.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess  # noqa: S404 -- the runner is invoked with a fixed argv and no shell
from functools import cache
from pathlib import Path
from typing import NamedTuple

import pytest

from turbohtml import parse
from turbohtml.cssom import computed_style
from turbohtml.query import select_one

_RUNNER = Path(__file__).resolve().parents[2] / "tools" / "bench" / "node" / "cssom_jsdom_runner.js"


class Check(NamedTuple):
    """One assertion within a case: read ``prop`` off the element ``selector`` matches and check its computed value."""

    selector: str
    prop: str
    expected: str
    oracle: bool = True  # whether jsdom is a faithful reference for this property (False for overflow/revert)


class Case(NamedTuple):
    """One cascade fixture: an input document and the checks to run against it."""

    id: str
    html: str
    checks: tuple[Check, ...]


def _c(selector: str, prop: str, expected: str, *, oracle: bool = True) -> Check:
    return Check(selector, prop, expected, oracle)


CORPUS: tuple[Case, ...] = (
    # -- specificity ordering (Selectors 4 §17) --
    Case(
        "specificity-id-over-class-over-type",
        '<style>p{color:red} .x{color:green} #id{color:blue}</style><p class="x" id="id">x</p>',
        (_c("#id", "color", "blue"),),
    ),
    Case(
        "specificity-two-classes-beat-one",
        "<style>.a{color:red} .a.b{color:green}</style><p class='a b'>x</p>",
        (_c(".a", "color", "green"),),
    ),
    Case(
        "specificity-id-beats-many-classes",
        '<style>#id{color:blue} .a.b.c.d{color:green}</style><p id="id" class="a b c d">x</p>',
        (_c("#id", "color", "blue"),),
    ),
    Case(
        "specificity-attribute-equals-class",
        "<style>p[data-x]{color:green} p{color:red}</style><p data-x='1'>x</p>",
        (_c("p", "color", "green"),),
    ),
    Case(
        "specificity-negation-counts-argument",
        "<style>p{color:red} p:not(.z){color:green}</style><p>x</p>",
        (_c("p", "color", "green"),),
    ),
    Case(
        "specificity-universal-has-zero",
        "<style>*{color:red} p{color:green}</style><p>x</p>",
        (_c("p", "color", "green"),),
    ),
    Case(
        "specificity-descendant-beats-type",
        "<style>p{color:red} div p{color:green}</style><div><p>x</p></div>",
        (_c("p", "color", "green"),),
    ),
    # -- source order among equal specificity (CSS Cascade 4 §6.4.4) --
    Case(
        "source-order-last-wins",
        "<style>.a{color:red} .b{color:green}</style><p class='a b'>x</p>",
        (_c(".a", "color", "green"),),
    ),
    # -- importance (CSS Cascade 4 §6.3) --
    Case(
        "important-beats-normal",
        "<style>p{color:red !important} p{color:green}</style><p>x</p>",
        (_c("p", "color", "red"),),
    ),
    Case(
        "important-low-specificity-beats-normal-high",
        "<style>p{color:red !important} #id{color:green}</style><p id='id'>x</p>",
        (_c("#id", "color", "red"),),
    ),
    Case(
        "important-among-important-source-order",
        "<style>p{color:red !important} p{color:green !important}</style><p>x</p>",
        (_c("p", "color", "green"),),
    ),
    # -- the style attribute (CSS Cascade 4 §6.4.4) --
    Case(
        "inline-beats-id-rule",
        "<style>#id{color:red}</style><p id='id' style='color:green'>x</p>",
        (_c("#id", "color", "green"),),
    ),
    Case(
        "inline-important-beats-rule-important",
        "<style>#id{color:red !important}</style><p id='id' style='color:green !important'>x</p>",
        (_c("#id", "color", "green"),),
    ),
    Case(
        "rule-important-beats-inline-normal",
        "<style>p{color:red !important}</style><p style='color:green'>x</p>",
        (_c("p", "color", "red"),),
    ),
    # WPT css-cascade/important-vs-inline-001.html: opacity !important outranks an inline declaration.
    Case(
        "wpt-important-vs-inline-opacity",
        "<style>.outer{opacity:0.5 !important}</style><p class='outer' id='el' style='opacity:0.75'>x</p>",
        (_c("#el", "opacity", "0.5"),),
    ),
    # -- inheritance (CSS Cascade 4 §7) --
    Case(
        "inherited-property-flows-down",
        "<style>.p{color:orange}</style><div class='p'><span id='c'>x</span></div>",
        (_c("#c", "color", "orange"),),
    ),
    Case(
        "non-inherited-property-does-not-flow",
        "<style>.p{background-color:orange}</style><div class='p'><span id='c'>x</span></div>",
        (_c("#c", "background-color", "transparent"),),
    ),
    Case(
        "inherit-keyword-takes-parent-value",
        "<style>.p{background-color:navy} #c{background-color:inherit}</style>"
        "<div class='p'><span id='c'>x</span></div>",
        (_c("#c", "background-color", "navy"),),
    ),
    Case(
        "initial-keyword-takes-initial-value",
        "<style>.p{color:navy} #c{color:initial}</style><div class='p'><span id='c'>x</span></div>",
        (_c("#c", "color", "canvastext"),),
    ),
    Case(
        "unset-on-inherited-inherits",
        "<style>.p{color:navy} #c{color:red} #c{color:unset}</style><div class='p'><span id='c'>x</span></div>",
        (_c("#c", "color", "navy"),),
    ),
    Case(
        "unset-on-non-inherited-is-initial",
        "<style>#c{display:block} #c{display:unset}</style><div><span id='c'>x</span></div>",
        (_c("#c", "display", "inline"),),
    ),
    # revert collapses to unset here (no user/UA origin); jsdom does not resolve it, so spec-only.
    Case(
        "revert-on-inherited-inherits",
        "<style>.p{color:navy} #c{color:red} #c{color:revert}</style><div class='p'><span id='c'>x</span></div>",
        (_c("#c", "color", "navy", oracle=False),),
    ),
    Case(
        "revert-on-non-inherited-is-initial",
        "<style>#c{display:block} #c{display:revert}</style><div><span id='c'>x</span></div>",
        (_c("#c", "display", "inline", oracle=False),),
    ),
    # WPT css-cascade/inherit-initial.html: inherit on the root falls back to the initial value.
    Case(
        "wpt-inherit-initial-on-root",
        "<style>html{z-index:inherit;position:inherit;overflow:inherit;background-color:inherit}"
        "body{overflow:scroll;background-color:pink}</style><body>x</body>",
        (
            _c(":root", "z-index", "auto"),
            _c(":root", "position", "static"),
            _c(":root", "overflow-x", "visible", oracle=False),
            _c(":root", "overflow-y", "visible", oracle=False),
            _c(":root", "background-color", "transparent"),
        ),
    ),
    # -- initial values for the common property set (each property's spec "Initial:" line) --
    Case(
        "initial-values-unstyled-span",
        "<span id='s'>x</span>",
        (
            _c("#s", "display", "inline"),
            _c("#s", "position", "static"),
            _c("#s", "margin-top", "0"),
            _c("#s", "padding-top", "0"),
            _c("#s", "border-top-width", "medium"),
            _c("#s", "border-top-style", "none"),
            _c("#s", "background-color", "transparent"),
            _c("#s", "visibility", "visible"),
            _c("#s", "font-weight", "normal"),
            _c("#s", "opacity", "1"),
            _c("#s", "float", "none"),
            _c("#s", "box-sizing", "content-box"),
        ),
    ),
    # -- distributive shorthand expansion (CSS Box 3 §4, the 1-to-4 rule) --
    Case(
        "margin-shorthand-three-values",
        "<style>#p{margin:1px 2px 3px}</style><p id='p'>x</p>",
        (
            _c("#p", "margin-top", "1px"),
            _c("#p", "margin-right", "2px"),
            _c("#p", "margin-bottom", "3px"),
            _c("#p", "margin-left", "2px"),
        ),
    ),
    Case(
        "margin-shorthand-two-values",
        "<style>#p{margin:1px 2px}</style><p id='p'>x</p>",
        (
            _c("#p", "margin-top", "1px"),
            _c("#p", "margin-right", "2px"),
            _c("#p", "margin-bottom", "1px"),
            _c("#p", "margin-left", "2px"),
        ),
    ),
    Case(
        "padding-shorthand-four-values",
        "<style>#p{padding:1px 2px 3px 4px}</style><p id='p'>x</p>",
        (
            _c("#p", "padding-top", "1px"),
            _c("#p", "padding-right", "2px"),
            _c("#p", "padding-bottom", "3px"),
            _c("#p", "padding-left", "4px"),
        ),
    ),
    Case(
        "border-color-shorthand-two-values",
        "<style>#p{border-color:red green}</style><p id='p'>x</p>",
        (_c("#p", "border-top-color", "red"), _c("#p", "border-right-color", "green")),
    ),
    Case(
        "border-style-shorthand-two-values",
        "<style>#p{border-style:solid dashed}</style><p id='p'>x</p>",
        (_c("#p", "border-top-style", "solid"), _c("#p", "border-right-style", "dashed")),
    ),
    Case(
        "border-width-shorthand-two-values",
        "<style>#p{border-width:1px 2px}</style><p id='p'>x</p>",
        (_c("#p", "border-top-width", "1px"), _c("#p", "border-right-width", "2px")),
    ),
    # overflow shorthand: turbohtml is spec-correct (CSS Overflow 3 §2.1); jsdom does not expand it.
    Case(
        "overflow-shorthand-two-values",
        "<style>#p{overflow:scroll hidden}</style><p id='p'>x</p>",
        (_c("#p", "overflow-x", "scroll", oracle=False), _c("#p", "overflow-y", "hidden", oracle=False)),
    ),
    Case(
        "overflow-shorthand-one-value",
        "<style>#p{overflow:auto}</style><p id='p'>x</p>",
        (_c("#p", "overflow-x", "auto", oracle=False), _c("#p", "overflow-y", "auto", oracle=False)),
    ),
    # -- longhand and shorthand interleaving resolves by source order --
    Case(
        "longhand-after-shorthand-wins",
        "<style>#p{margin:5px;margin-top:9px}</style><p id='p'>x</p>",
        (_c("#p", "margin-top", "9px"), _c("#p", "margin-left", "5px")),
    ),
    Case(
        "shorthand-after-longhand-wins",
        "<style>#p{margin-top:9px;margin:5px}</style><p id='p'>x</p>",
        (_c("#p", "margin-top", "5px"),),
    ),
    # -- a spread of the inherited and non-inherited property set --
    Case(
        "assorted-inherited-properties",
        "<style>#p{font-style:italic;text-align:center;white-space:nowrap;line-height:1.5;"
        "letter-spacing:2px;direction:rtl;list-style-type:square;visibility:hidden}</style><p id='p'>x</p>",
        (
            _c("#p", "font-style", "italic"),
            _c("#p", "text-align", "center"),
            _c("#p", "white-space", "nowrap"),
            _c("#p", "line-height", "1.5"),
            _c("#p", "letter-spacing", "2px"),
            _c("#p", "direction", "rtl"),
            _c("#p", "list-style-type", "square"),
            _c("#p", "visibility", "hidden"),
        ),
    ),
    Case(
        "assorted-non-inherited-properties",
        "<style>#p{display:flex;position:absolute;float:left;opacity:0.5;z-index:5;"
        "box-sizing:border-box;width:100px;height:50px}</style><p id='p'>x</p>",
        (
            _c("#p", "display", "flex"),
            _c("#p", "position", "absolute"),
            _c("#p", "float", "left"),
            _c("#p", "opacity", "0.5"),
            _c("#p", "z-index", "5"),
            _c("#p", "box-sizing", "border-box"),
            _c("#p", "width", "100px"),
            _c("#p", "height", "50px"),
        ),
    ),
    # font-weight numeric and keyword agree with the oracle.
    Case(
        "font-weight-numeric",
        "<style>#p{font-weight:700}</style><p id='p'>x</p>",
        (_c("#p", "font-weight", "700"),),
    ),
    # a selector the engine rejects (a pseudo-element) drops its rule rather than failing the cascade.
    Case(
        "unparsable-selector-drops-rule",
        "<style>p::before{color:red} p{color:green}</style><p>x</p>",
        (_c("p", "color", "green"),),
    ),
)

# The grammar-parsed shorthands turbohtml does not expand (documented curated-set boundary): jsdom expands them, so the
# longhand stays at its initial value where jsdom reports the shorthand's component. Recorded as xfail.
CURATED_SHORTHAND_BOUNDARY: tuple[Case, ...] = (
    Case(
        "border-shorthand-not-expanded",
        "<style>#p{border:2px dashed green}</style><p id='p'>x</p>",
        (
            _c("#p", "border-top-width", "2px"),
            _c("#p", "border-top-style", "dashed"),
            _c("#p", "border-top-color", "green"),
        ),
    ),
)

_COLOR_PROPS = frozenset({
    "color",
    "background-color",
    "border-top-color",
    "border-right-color",
    "border-bottom-color",
    "border-left-color",
    "outline-color",
})
_NAMED = {
    "red": (255, 0, 0, 1.0),
    "green": (0, 128, 0, 1.0),
    "blue": (0, 0, 255, 1.0),
    "black": (0, 0, 0, 1.0),
    "white": (255, 255, 255, 1.0),
    "pink": (255, 192, 203, 1.0),
    "orange": (255, 165, 0, 1.0),
    "lime": (0, 255, 0, 1.0),
    "navy": (0, 0, 128, 1.0),
    "gray": (128, 128, 128, 1.0),
    "transparent": (0, 0, 0, 0.0),
    "canvastext": (0, 0, 0, 1.0),  # color's initial value, a system color; opaque black in a default light theme
}


def _norm(prop: str, value: str) -> object:
    """Canonicalize a computed value for comparison, folding a color to an ``(r, g, b, a)`` tuple."""
    text = value.strip().lower()
    if prop not in _COLOR_PROPS:
        return text
    if text in _NAMED:
        return _NAMED[text]
    if (match := re.match(r"rgba?\(([^)]+)\)", text)) is not None:
        parts = [piece for piece in re.split(r"[ ,/]+", match.group(1)) if piece]
        alpha = float(parts[3]) if len(parts) > 3 else 1.0
        return (int(parts[0]), int(parts[1]), int(parts[2]), alpha)
    if text.startswith("#"):
        digits = text[1:]
        if len(digits) == 3:
            digits = "".join(char * 2 for char in digits)
        return (int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16), 1.0)
    return text


def _turbo(html: str, selector: str, prop: str) -> str | None:
    element = select_one("html" if selector == ":root" else selector, parse(html))
    return None if element is None else computed_style(element).get(prop)


@cache
def _jsdom_available() -> bool:
    node = shutil.which("node")
    if node is None or not _RUNNER.exists():
        return False
    probe = subprocess.run(  # noqa: S603 -- fixed argv, no shell
        [node, "-e", "require('jsdom')"], cwd=_RUNNER.parent, capture_output=True, check=False
    )
    return probe.returncode == 0


@cache
def _jsdom_results() -> dict[str, dict[str, dict[str, str]]]:
    """Run every corpus and boundary case through jsdom once, keyed ``{case-id: {selector: {prop: value}}}``."""
    payload = []
    for case in (*CORPUS, *CURATED_SHORTHAND_BOUNDARY):
        targets: dict[str, list[str]] = {}
        for check in case.checks:
            targets.setdefault(check.selector, []).append(check.prop)
        payload.append({
            "id": case.id,
            "html": case.html,
            "targets": [{"selector": s, "props": p} for s, p in targets.items()],
        })
    node = shutil.which("node")
    assert node is not None
    completed = subprocess.run(  # noqa: S603 -- fixed argv, no shell
        [node, str(_RUNNER)], input=json.dumps(payload), cwd=_RUNNER.parent, capture_output=True, text=True, check=True
    )
    return json.loads(completed.stdout)


_SPEC_PARAMS = [
    pytest.param(case.html, check, id=f"{case.id}-{check.prop}") for case in CORPUS for check in case.checks
]
_ORACLE_PARAMS = [
    pytest.param(case.id, check, id=f"{case.id}-{check.prop}")
    for case in CORPUS
    for check in case.checks
    if check.oracle
]
_BOUNDARY_PARAMS = [
    pytest.param(case.id, check, id=f"{case.id}-{check.prop}")
    for case in CURATED_SHORTHAND_BOUNDARY
    for check in case.checks
]


@pytest.mark.parametrize(("html", "check"), _SPEC_PARAMS)
def test_cssom_matches_spec(html: str, check: Check) -> None:
    got = _turbo(html, check.selector, check.prop)
    assert got is not None, f"no element matched {check.selector!r}"
    assert _norm(check.prop, got) == _norm(check.prop, check.expected)


@pytest.mark.skipif(not _jsdom_available(), reason="node with jsdom is not installed")
@pytest.mark.parametrize(("case_id", "check"), _ORACLE_PARAMS)
def test_cssom_matches_jsdom(case_id: str, check: Check) -> None:
    reference = _jsdom_results()[case_id][check.selector].get(check.prop, "")
    if not reference:  # jsdom's getComputedStyle has no value for this property; nothing to diff
        pytest.skip(f"jsdom reports no computed {check.prop}")
    case = next(entry for entry in CORPUS if entry.id == case_id)
    got = _turbo(case.html, check.selector, check.prop)
    assert got is not None
    assert _norm(check.prop, got) == _norm(check.prop, reference)


@pytest.mark.skipif(not _jsdom_available(), reason="node with jsdom is not installed")
@pytest.mark.xfail(
    reason="turbohtml expands only the distributive shorthands; border/outline are out of the curated set",
    strict=True,
)
@pytest.mark.parametrize(("case_id", "check"), _BOUNDARY_PARAMS)
def test_cssom_curated_shorthand_boundary(case_id: str, check: Check) -> None:
    reference = _jsdom_results()[case_id][check.selector].get(check.prop, "")
    assert reference, f"jsdom reports no computed {check.prop}"
    case = next(entry for entry in CURATED_SHORTHAND_BOUNDARY if entry.id == case_id)
    got = _turbo(case.html, check.selector, check.prop)
    assert got is not None
    assert _norm(check.prop, got) == _norm(check.prop, reference)
