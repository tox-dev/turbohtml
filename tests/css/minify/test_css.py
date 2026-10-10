"""CSS minification with pinned tdewolff/minify and native regression cases.

Malformed corpus inputs retain exact-output checks but can lack stable round-trip results.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess  # ruff:ignore[suspicious-subprocess-import]
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

import pytest
from bench.operations import INPUTS

from turbohtml import clean
from turbohtml.clean import CSSMinify, minify_css, minify_css_inline

if TYPE_CHECKING:
    from collections.abc import Callable

    from _pytest.mark.structures import ParameterSet

_NEWLY = CSSMinify(baseline=2021)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("a{color:#ffffff}", "a{color:#fff}", id="hex-shorten-6-to-3"),
        pytest.param("a{color:#AABBCC}", "a{color:#abc}", id="hex-lowercase-and-shorten"),
        pytest.param("a{color:#ff0000}", "a{color:red}", id="hex-to-shorter-name"),
        pytest.param("a{color:rgb(255,0,0)}", "a{color:red}", id="rgb-to-name"),
        pytest.param("a{color:rgba(0,0,0,0)}", "a{color:#0000}", id="rgba-zero-to-hex"),
        pytest.param("a{color:hsl(0,100%,50%)}", "a{color:red}", id="hsl-to-name"),
        pytest.param("a{color:WHITE}", "a{color:#fff}", id="name-to-shorter-hex"),
        pytest.param("a{color:rebeccapurple}", "a{color:#639}", id="rebeccapurple-to-hex"),
        pytest.param("a{color:lightslategray}", "a{color:#789}", id="lightslategray-to-hex"),
        pytest.param("a{color:lightslateblue}", "a{color:lightslateblue}", id="non-css-color-name-untouched"),
        pytest.param("a{margin:0px}", "a{margin:0}", id="drop-unit-on-zero-length"),
        pytest.param("a{margin:0.500px}", "a{margin:.5px}", id="trim-number-zeros"),
        pytest.param("a{width:+5px}", "a{width:5px}", id="drop-leading-plus"),
        pytest.param("a{width:5000em}", "a{width:5e3em}", id="scientific-when-shorter"),
        pytest.param("a{z-index:1000}", "a{z-index:1000}", id="no-scientific-on-plain-int"),
        pytest.param("a{margin:1px 2px 1px 2px}", "a{margin:1px 2px}", id="collapse-mirrored-box"),
        pytest.param(
            "a{margin-top:1px;margin-right:2px;margin-bottom:3px;margin-left:4px}",
            "a{margin:1px 2px 3px 4px}",
            id="merge-longhands-to-shorthand",
        ),
        pytest.param(
            "a{outline-width:1px;outline-style:solid;outline-color:red}",
            "a{outline:1px solid red}",
            id="merge-outline",
        ),
        pytest.param(
            "a{outline-color:red;outline-style:solid;outline-width:1px}",
            "a{outline:1px solid red}",
            id="merge-outline-canonical-order",
        ),
        pytest.param(
            "a{outline-width:inherit;outline-style:inherit;outline-color:inherit}",
            "a{outline:inherit}",
            id="merge-outline-wide-keyword",
        ),
        pytest.param(
            "a{outline-width:inherit;outline-style:solid;outline-color:red}",
            "a{outline-width:inherit;outline-style:solid;outline-color:red}",
            id="no-merge-outline-mixed-wide",
        ),
        pytest.param(
            "a{outline-width:inherit;outline-style:initial;outline-color:unset}",
            "a{outline-width:inherit;outline-style:initial;outline-color:unset}",
            id="no-merge-outline-differing-wide",
        ),
        pytest.param(
            "a{outline-width:1px!important;outline-style:solid;outline-color:red}",
            "a{outline-width:1px!important;outline-style:solid;outline-color:red}",
            id="no-merge-outline-importance-mismatch",
        ),
        pytest.param(
            "a{outline-width:var(--w);outline-style:solid;outline-color:red}",
            "a{outline-width:var(--w);outline-style:solid;outline-color:red}",
            id="no-merge-outline-with-var",
        ),
        pytest.param(
            "a{outline-width:1px;outline-style:solid}",
            "a{outline-width:1px;outline-style:solid}",
            id="no-merge-outline-missing-color",
        ),
        pytest.param(
            "a{outline-style:solid;outline-color:red}",
            "a{outline-style:solid;outline-color:red}",
            id="no-merge-outline-missing-width",
        ),
        pytest.param(
            "a{outline-width:1px;outline-color:red}",
            "a{outline-width:1px;outline-color:red}",
            id="no-merge-outline-missing-style",
        ),
        pytest.param(
            "a{border-top-left-radius:1px;border-top-right-radius:1px;border-bottom-right-radius:1px;"
            "border-bottom-left-radius:1px}",
            "a{border-radius:1px}",
            id="merge-border-radius-collapsed",
        ),
        pytest.param(
            "a{border-top-left-radius:1px;border-top-right-radius:2px;border-bottom-right-radius:3px;"
            "border-bottom-left-radius:4px}",
            "a{border-radius:1px 2px 3px 4px}",
            id="merge-border-radius-four-values",
        ),
        pytest.param(
            "a{border-top-left-radius:calc(100% - 1px);border-top-right-radius:calc(100% - 1px);"
            "border-bottom-right-radius:calc(100% - 1px);border-bottom-left-radius:calc(100% - 1px)}",
            "a{border-radius:calc(100% - 1px)}",
            id="merge-border-radius-calc-corner",
        ),
        pytest.param(
            "a{border-top-left-radius:1px 2px;border-top-right-radius:1px 2px;border-bottom-right-radius:1px 2px;"
            "border-bottom-left-radius:1px 2px}",
            "a{border-top-left-radius:1px 2px;border-top-right-radius:1px 2px;border-bottom-right-radius:1px 2px;"
            "border-bottom-left-radius:1px 2px}",
            id="no-merge-elliptical-border-radius",
        ),
        pytest.param(
            "a{border-top-left-radius:1px;border-top-right-radius:1px;border-bottom-right-radius:1px;"
            "border-bottom-left-radius:1px;border-start-start-radius:9px}",
            "a{border-top-left-radius:1px;border-top-right-radius:1px;border-bottom-right-radius:1px;"
            "border-bottom-left-radius:1px;border-start-start-radius:9px}",
            id="no-merge-border-radius-with-logical-corner",
        ),
        pytest.param("a{color:red;color:red}", "a{color:red}", id="dedup-identical-collapses"),
        pytest.param("a{width:calc(1px + 2px)}", "a{width:3px}", id="calc-add"),
        pytest.param("a{width:calc(10px / 2)}", "a{width:5px}", id="calc-divide"),
        pytest.param("a{width:calc(100% / 3)}", "a{width:calc(100% / 3)}", id="calc-non-terminating-kept"),
        pytest.param("a{width:calc(1px + 1px + 1px)}", "a{width:3px}", id="calc-chain"),
        pytest.param("a{transform:translate(1px,0)}", "a{transform:translate(1px)}", id="transform-drop-zero-arg"),
        pytest.param("a{transform:scale(2,2)}", "a{transform:scale(2)}", id="transform-collapse-equal-args"),
        pytest.param(
            "a{background:#ffffff url(x.png) no-repeat}",
            "a{background:#fff url(x.png)no-repeat}",
            id="background-shorthand",
        ),
        pytest.param("a{--custom:  1px  }", "a{--custom:1px}", id="custom-property-trimmed-not-rewritten"),
        pytest.param("a{color:red!important}", "a{color:red!important}", id="important-kept"),
        pytest.param("/* drop me */a{color:red}", "a{color:red}", id="strip-normal-comment"),
        pytest.param("/*! keep me */a{color:red}", "/*! keep me */a{color:red}", id="keep-bang-comment"),
        pytest.param(".x{color:red}.y{color:red}", ".x,.y{color:red}", id="merge-identical-bodies-to-selector-list"),
        pytest.param("a{color:red}a{background:blue}", "a{color:red;background:blue}", id="merge-same-selector-bodies"),
        pytest.param(
            "a{color:red}b{margin:0}a{font-size:2px}",
            "a{color:red;font-size:2px}b{margin:0}",
            id="merge-nonadjacent-same-selector",
        ),
        pytest.param(
            ".a{color:red}.c{margin:0}.b{color:red}",
            ".a,.b{color:red}.c{margin:0}",
            id="merge-nonadjacent-identical-body",
        ),
        pytest.param("a{color:red}a{margin:0}a{padding:0}", "a{color:red;margin:0;padding:0}", id="merge-triple-run"),
        pytest.param(
            "a{color:red}b{background:url(x)}a{font-size:2px}",
            "a{color:red;font-size:2px}b{background:url(x)}",
            id="merge-nonadjacent-past-url-value",
        ),
        pytest.param(
            "a{color:red}b{margin:0;padding:0}a{font-size:2px}",
            "a{color:red;font-size:2px}b{margin:0;padding:0}",
            id="merge-nonadjacent-past-multi-declaration",
        ),
        pytest.param(
            "a{color:red}b{color:blue}a{color:green}",
            "a{color:red}b{color:blue}a{color:green}",
            id="no-merge-conflicting-property",
        ),
        pytest.param(
            "a{color:red}b{margin:0}a{margin-top:1px}",
            "a{color:red}b{margin:0}a{margin-top:1px}",
            id="no-merge-shorthand-blocks-longhand",
        ),
        pytest.param(
            "a{color:red}b{margin-top:0}a{margin:1px}",
            "a{color:red}b{margin-top:0}a{margin:1px}",
            id="no-merge-longhand-blocks-shorthand",
        ),
        pytest.param(
            "a{margin:0}b{margin-top:1px}a{margin:2px}",
            "a{margin:0}b{margin-top:1px}a{margin:2px}",
            id="no-merge-shorthand-vs-longhand-blocks",
        ),
        pytest.param(
            "a{color:red}b{all:unset}a{font-size:2px}",
            "a{color:red}b{all:unset}a{font-size:2px}",
            id="no-merge-across-all",
        ),
        pytest.param(
            'a{color:red}x{content:"a;b"}a{font-size:2px}',
            'a{color:red}x{content:"a;b"}a{font-size:2px}',
            id="no-merge-across-double-quoted-body",
        ),
        pytest.param(
            "a{color:red}x{content:'a;b'}a{font-size:2px}",
            "a{color:red}x{content:'a;b'}a{font-size:2px}",
            id="no-merge-across-single-quoted-body",
        ),
        pytest.param(
            "a{color:red}b{c:d;& e{f:g}}a{margin:0}",
            "a{color:red}b{c:d;& e{f:g}}a{margin:0}",
            id="no-merge-across-nested-rule",
        ),
        pytest.param(
            'a{color:red}b{margin:0}a{content:"x;y"}',
            'a{color:red}b{margin:0}a{content:"x;y"}',
            id="no-merge-when-moved-body-is-opaque",
        ),
        pytest.param(
            "a{color:red}/*!x*/a{margin:0}",
            "a{color:red}/*!x*/a{margin:0}",
            id="no-merge-across-bang-comment",
        ),
        pytest.param(
            "@media screen and (min-width:100px){a{color:red}}",
            "@media screen and (min-width:100px){a{color:red}}",
            id="at-media-block",
        ),
        pytest.param("@media screen{}", "", id="drop-empty-media"),
        pytest.param("@supports (x:y){}", "", id="drop-empty-supports"),
        pytest.param("@container x{}", "", id="drop-empty-container"),
        pytest.param("a{}@media print{b{}}", "", id="drop-media-emptied-by-nested"),
        pytest.param("@media screen{a{color:red}}", "@media screen{a{color:red}}", id="keep-non-empty-media"),
        pytest.param("@layer x{}", "@layer x{}", id="keep-empty-layer"),
        pytest.param("@keyframes x{}", "@keyframes x{}", id="keep-empty-keyframes"),
        pytest.param('@import "x"', '@import"x";', id="keep-import-statement"),
        pytest.param("{--x:\n}", "", id="selector-less-custom-property"),
        pytest.param("{color:red}", "", id="selector-less-declaration"),
        pytest.param("  \t {color:red}", "", id="whitespace-only-selector"),
        pytest.param("{}", "", id="selector-less-empty-body"),
    ],
)
def test_minify_css(source: str, expected: str) -> None:
    assert minify_css(source) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("p {font-family: #f00}", "p{font-family:#f00}", id="font-family"),
        pytest.param("p {font-family: #ff0000}", "p{font-family:#ff0000}", id="long-font-family-hash"),
        pytest.param("p {opacity: #f00}", "p{opacity:#f00}", id="non-color-property"),
        pytest.param("p {unknown: #F00}", "p{unknown:#F00}", id="unknown-property-case"),
        pytest.param("p {background: #ff0000}", "p{background:red}", id="background-color"),
        pytest.param("p {border: #ff0000}", "p{border:red}", id="border-color"),
    ],
)
def test_minify_css_hash_color_context(source: str, expected: str) -> None:
    assert minify_css(source) == expected


@pytest.mark.parametrize(
    "property_name",
    [
        "accent-color",
        "background",
        "background-color",
        "border",
        "border-color",
        "border-top-color",
        "border-right-color",
        "border-bottom-color",
        "border-left-color",
        "border-top",
        "border-right",
        "border-bottom",
        "border-left",
        "border-block",
        "border-inline",
        "border-block-start",
        "border-block-end",
        "border-inline-start",
        "border-inline-end",
        "box-shadow",
        "color",
        "caret-color",
        "column-rule-color",
        "column-rule",
        "fill",
        "flood-color",
        "lighting-color",
        "outline-color",
        "outline",
        "stroke",
        "stop-color",
        "scrollbar-color",
        "text-decoration-color",
        "text-emphasis-color",
        "text-decoration",
        "text-emphasis",
        "text-shadow",
    ],
)
def test_minify_css_color_property_hash(property_name: str) -> None:
    assert minify_css(f"p{{{property_name}:#ff0000}}") == f"p{{{property_name}:red}}"


@pytest.mark.parametrize("count", [pytest.param(40, id="stack"), pytest.param(300, id="heap")])
def test_minify_css_many_unique_rules(count: int) -> None:
    source = "".join(f".c{index}{{--p{index}:{index + 1}px}}" for index in range(count))
    assert minify_css(source) == source


def test_minify_css_many_rules_merge_same_selector() -> None:
    middle = "".join(f".c{index}{{--p{index}:{index + 1}px}}" for index in range(40))
    assert minify_css(f".target{{color:red}}{middle}.target{{background:blue}}") == (
        f".target{{color:red;background:blue}}{middle}"
    )


def test_minify_css_many_rules_merge_same_body() -> None:
    middle = "".join(f".c{index}{{--p{index}:{index + 1}px}}" for index in range(40))
    assert minify_css(f".first{{color:red}}{middle}.last{{color:red}}") == f".first,.last{{color:red}}{middle}"


def test_minify_css_many_rules_keep_blocked_merge() -> None:
    middle = "".join(f".c{index}{{--p{index}:{index + 1}px}}" for index in range(40))
    source = f".target{{color:red}}{middle}.block{{color:blue}}.target{{color:green}}"
    assert minify_css(source) == source


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("[:;a", id="unclosed-bracket"),
        pytest.param("c:d[;e]", id="semicolon-in-bracket"),
        pytest.param("c:d\\;e", id="escaped-semicolon"),
    ],
)
def test_minify_css_merge_scan_stays_in_body(filler_rules: str, body: str) -> None:
    # the last body piece the merge scan splits off holds no ':'; reading on for one ran past the end of the body
    assert minify_css(f"b{{g:h}}{filler_rules}b{{{body}") == f"b{{g:h;{body}}}{filler_rules}"


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("c:d[;e];color:red", id="semicolon-in-bracket"),
        pytest.param("c:d);color:red", id="stray-paren"),
        pytest.param("c:d\\(;color:red", id="escaped-paren"),
        pytest.param("c:url(x(y);color:red", id="paren-in-url"),
        pytest.param("c:url(x;y);color:red", id="semicolon-in-url"),
        pytest.param("c:url(x\\));color:red", id="escaped-paren-in-url"),
        pytest.param("c:f(\\();color:red", id="escaped-paren-in-function"),
        pytest.param("c:f(g(h);i);color:red", id="nested-functions"),
        pytest.param("c\\:d:e;color:red", id="escaped-colon-in-name"),
        pytest.param("--x:\\(;color:red", id="custom-property"),
        pytest.param("c:d\\(!important;color:red", id="important-before"),
        pytest.param("c:d\\(;color:red!important", id="important-conflict"),
        pytest.param("c/*;(*/:d\\(;color:red", id="comment-in-name"),
        pytest.param('c:"(;";color:red', id="string"),
        pytest.param("c/*/;(*/:d\\(;color:red", id="comment-opening-with-slash"),
        pytest.param("c/*\\([)]}:;/*/:d\\(;color:red", id="delimiters-in-comment"),
        pytest.param("c:a/;color:red", id="slash"),
        pytest.param("color:red;c:a/", id="slash-at-end"),
        pytest.param("c:url(a[b);color:red", id="bracket-in-url"),
        pytest.param("c:f(x});color:red", id="brace-closing-paren"),
        pytest.param("c:a]b;color:red", id="stray-bracket"),
        pytest.param("c:a:b;color:red", id="second-colon"),
        pytest.param("c:xé;color:red", id="non-ascii"),
        pytest.param("@x y;color:red", id="at-statement"),
        pytest.param("color:red;@x y", id="at-statement-at-end"),
        pytest.param("(a:b);color:red", id="paren-in-name"),
        pytest.param("c:nil(x);color:red", id="function-ending-in-l"),
        pytest.param(";".join(f"--p{index}:{index}" for index in range(40)) + ";color:red", id="many-properties"),
    ],
)
def test_minify_css_merge_scan_sees_every_property(filler_rules: str, body: str) -> None:
    # .u sets color, so folding the last .t back into the first would let .u's red win over green
    source: Final = f".t{{color:blue}}{filler_rules}.u{{{body}}}.t{{color:green}}"
    assert minify_css(source) == source


def test_minify_css_merge_sees_property_after_comment(filler_rules: str) -> None:
    source = f".t{{color:blue}}{filler_rules}.u{{c:d\\(/*;*/;color:red}}.t{{color:green}}"
    assert minify_css(source) == f".t{{color:blue}}{filler_rules}.u{{c:d\\(;color:red}}.t{{color:green}}"


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("c:d\\;color:red", id="escaped-semicolon"),
        pytest.param("c:d[;color:red]", id="semicolon-in-bracket"),
        pytest.param("c:url(x;color:red)", id="semicolon-in-url"),
        pytest.param("c:f(\\;color:red)", id="escaped-semicolon-in-function"),
        pytest.param("--color:red", id="custom-property"),
    ],
)
def test_minify_css_merge_passes_rule_without_property(filler_rules: str, body: str) -> None:
    # `color:red` sits inside one declaration's value or custom property name, so .u leaves color alone
    source = f".t{{color:blue}}{filler_rules}.u{{{body}}}.t{{color:green}}"
    assert minify_css(source) == f".t{{color:blue;color:green}}{filler_rules}.u{{{body}}}"


@pytest.mark.parametrize(
    ("first", "blocker", "decoded", "last"),
    [
        pytest.param("color:blue", "colo\\r:red", "color:red", "color:green", id="escaped-letter"),
        pytest.param("color:blue", "\\61ll:initial", "all:initial", "color:green", id="escaped-all"),
        pytest.param("margin-top:1px", "marg\\in:0", "margin:0", "margin-top:2px", id="escaped-shorthand"),
        pytest.param("margin:1px", "margin-t\\op:0", "margin-top:0", "margin:2px", id="escaped-longhand"),
        pytest.param("--\u00e9:blue", "--\\e9:red", "--\u00e9:red", "--\u00e9:green", id="non-ascii-escape"),
    ],
)
def test_minify_css_merge_reads_escaped_property_names(
    filler_rules: str, first: str, blocker: str, decoded: str, last: str
) -> None:
    # .u sets the property .t sets, spelled through an escape, so the last .t cannot fold back past it
    source: Final = f".t{{{first}}}{filler_rules}.u{{{blocker}}}.t{{{last}}}"
    assert minify_css(source) == f".t{{{first}}}{filler_rules}.u{{{decoded}}}.t{{{last}}}"


@pytest.mark.parametrize(
    ("body", "decoded"),
    [
        pytest.param("c\\olorr:red", "colorr:red", id="other-name"),
        pytest.param("--\\e9:red", "--\u00e9:red", id="non-ascii-escape"),
        pytest.param("\\31 color:red", "\\31 color:red", id="name-keeping-an-escape"),
    ],
)
def test_minify_css_merge_passes_escaped_property_name(filler_rules: str, body: str, decoded: str) -> None:
    source: Final = f".t{{color:blue}}{filler_rules}.u{{{body}}}.t{{color:green}}"
    assert minify_css(source) == f".t{{color:blue;color:green}}{filler_rules}.u{{{decoded}}}"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("a{\\63 a:red}", "a{ca:red}", id="hex-digit-after"),
        pytest.param("a{\\63 A:red}", "a{ca:red}", id="upper-case-hex-digit-after"),
        pytest.param("a{\\63\ta:red}", "a{ca:red}", id="tab"),
        pytest.param("a{\\63\r\na:red}", "a{ca:red}", id="crlf"),
        pytest.param("a{\\63 z:red}", "a{cz:red}", id="other-letter-after"),
    ],
)
def test_minify_css_property_name_hex_escape_space(source: str, expected: str) -> None:
    assert minify_css(source) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("a{\\2d-X:1}", "a{--X:1}", id="hex-escape"),
        pytest.param("a{\\2d -X:1}", "a{--X:1}", id="hex-escape-space"),
        pytest.param("a{-\\2d X:1}", "a{--X:1}", id="second-hyphen-escaped"),
        pytest.param("a{\\-\\-X:1}", "a{--X:1}", id="escaped-hyphens"),
        pytest.param("a{\\2d-X: 0px }", "a{--X:0px}", id="value-kept-raw"),
        pytest.param("a{\\2dX:1}", "a{-x:1}", id="one-hyphen"),
        pytest.param("a{-\\X:1}", "a{-x:1}", id="escaped-letter-after-hyphen"),
        pytest.param("a{\\58-Y:1}", "a{x-y:1}", id="escaped-letter-before-hyphen"),
    ],
)
def test_minify_css_escaped_custom_property_name(source: str, expected: str) -> None:
    assert minify_css(source) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("a{color:rgb(0 0 0 .5)}", "a{color:rgb(0 0 0 .5)}", id="modern-alpha-without-slash"),
        pytest.param("a{color:rgb(0 0 0 0)}", "a{color:rgb(0 0 0 0)}", id="modern-alpha-without-slash-all-zero"),
        pytest.param("a{color:rgb(255,0,0 0)}", "a{color:rgb(255,0,0 0)}", id="comma-then-space"),
        pytest.param("a{color:rgb(0,0 0/.5)}", "a{color:rgb(0,0 0/.5)}", id="comma-and-slash-mixed"),
        pytest.param("a{color:rgb(0 0/0)}", "a{color:rgb(0 0/0)}", id="slash-with-three-values"),
        pytest.param("a{color:rgb(0/0 0 0)}", "a{color:rgb(0/0 0 0)}", id="slash-not-before-alpha"),
        pytest.param("a{color:rgb(0 0 0//.5)}", "a{color:rgb(0 0 0//.5)}", id="two-slashes"),
        pytest.param("a{color:rgb(0,0,0)}", "a{color:#000}", id="legacy-folds"),
        pytest.param("a{color:rgb(0 0 0)}", "a{color:#000}", id="modern-folds"),
        pytest.param("a{color:rgb(0 0 0/.5)}", "a{color:rgb(0 0 0/.5)}", id="modern-alpha-slash-kept"),
        pytest.param("a{color:rgb(0 0 0/0)}", "a{color:#0000}", id="modern-transparent-folds"),
        pytest.param("a{color:rgba(0,0,0,0)}", "a{color:#0000}", id="legacy-transparent-folds"),
    ],
)
def test_minify_css_rgb_argument_shape(source: str, expected: str) -> None:
    # rgb()/hsl() fold only when the arguments match a legal shape (legacy commas or modern spaces with one slash before
    # the alpha); an illegal shape is kept verbatim, never rebuilt into a valid color (#1033)
    assert minify_css(source) == expected
    assert minify_css(expected) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("a{col or:red}", "", id="two-idents"),
        pytest.param("a{foo bar:red}", "", id="two-idents-longer"),
        pytest.param("a{x/ *y:1;color:red}b{c:d}", "a{color:red}b{c:d}", id="slash-star-not-joined-to-comment"),
        pytest.param("a{\\63  a:red}", "", id="hex-escape-double-space"),
        pytest.param("a{\\63x z:red}", "", id="hex-escape-not-at-name-end"),
        pytest.param("a{\\z z:red}", "", id="non-hex-escape-then-space"),
        pytest.param("a{ab z:1}", "", id="hex-byte-end-is-not-an-escape"),
        pytest.param("a{\\000063 z:red}", "a{cz:red}", id="six-digit-hex-escape-space"),
        pytest.param("a{\\0000631 z:red}", "", id="hex-escape-past-six-digits-then-space"),
        pytest.param("a{*zoom:1px}", "a{*zoom:1px}", id="star-hack-kept"),
        pytest.param("a{_color:red}", "a{_color:red}", id="underscore-hack-kept"),
    ],
)
def test_minify_css_property_name_whitespace_not_fused(source: str, expected: str) -> None:
    # a declaration name is one ident; whitespace that is not a hex escape's single consumed space joins two tokens,
    # so the invalid declaration is dropped rather than fused into a different valid name (#1052)
    assert minify_css(source) == expected
    assert minify_css(expected) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(".a\\ {color:red}", ".a\\ {color:red}", id="selector-escaped-space"),
        pytest.param("\\ {color:red}", "\\ {color:red}", id="selector-only-escaped-space"),
        pytest.param("@x \\ ;", "@x \\ ;", id="at-prelude-escaped-space"),
        pytest.param(".a\\  b{x:1}", ".a\\  b{x:1}", id="escaped-space-then-descendant"),
        pytest.param(".a\\\\ {x:1}", ".a\\\\{x:1}", id="escaped-backslash-trailing-space-trimmed"),
        pytest.param("a b{x:1}", "a b{x:1}", id="descendant-space-kept"),
        pytest.param("a > b{x:1}", "a>b{x:1}", id="combinator-space-trimmed"),
    ],
)
def test_minify_css_escaped_space_kept(source: str, expected: str) -> None:
    # a backslash-space escapes U+0020, a name code point, so it survives minification (#1061)
    assert minify_css(source) == expected
    assert minify_css(expected) == expected


_ESCAPED_NAME: Final[list[ParameterSet]] = [
    pytest.param("a{e:\\7b x}", "a{e:\\{x}", id="leading-brace"),
    pytest.param("a{e:\\fec0}", "a{e:\ufec0}", id="non-bom-final-byte"),
    pytest.param("a{e:1E3p\\78}", "a{e:1e3px}", id="uppercase-exponent-unit"),
    pytest.param("a{e:x\\\ry}", "a{e:x\\\ry}", id="escaped-carriage-return"),
    pytest.param("a{e:x\\\fy}", "a{e:x\\\fy}", id="escaped-form-feed"),
    pytest.param("a{e:\\61\rb}", "a{e:ab}", id="escape-terminated-by-carriage-return"),
    pytest.param("a{e:\\5fx}", "a{e:_x}", id="leading-underscore"),
    pytest.param("a{e:-\\5f}", "a{e:-_}", id="hyphen-underscore"),
    pytest.param("a{e:1\\65-}", "a{e:1e-}", id="unit-e-hyphen"),
    pytest.param("a{e:\\61", "a{e:a}", id="escape-at-eof"),
    pytest.param("a{e:\\61\r", "a{e:a}", id="escape-terminated-at-eof"),
    pytest.param("@\\61 bcdefg;", "@abcdefg;", id="seven-letter-at-keyword"),
    pytest.param("a{colo\\r:red}", "a{color:red}", id="property"),
    pytest.param("a{e:\\72 ed}", "a{e:red}", id="value"),
    pytest.param("a{color:\\72 ed}", "a{color:red}", id="color-keyword"),
    pytest.param("a{color:#\\66 00}", "a{color:red}", id="hash-color"),
    pytest.param(".\\61\\62{x:1}", ".ab{x:1}", id="class"),
    pytest.param("#\\66 00{x:1}", "#f00{x:1}", id="id"),
    pytest.param("D\\49V{x:1}", "div{x:1}", id="type-selector"),
    pytest.param("[d\\61ta-x=\\61]{x:1}", "[data-x=a]{x:1}", id="attribute"),
    pytest.param("@m\\65 dia screen{a{x:1}}", "@media screen{a{x:1}}", id="at-keyword"),
    pytest.param("a{e:x !imp\\6frtant}", "a{e:x!important}", id="important"),
    pytest.param("a{e:u\\72l(x)}", "a{e:url(x)}", id="url-function"),
    pytest.param("a{--x:\\72 ed}", "a{--x:red}", id="custom-property-value"),
    pytest.param("a{e:\\E9 t\\E9}", "a{e:\u00e9t\u00e9}", id="non-ascii"),
    pytest.param("a{e:\\1F600}", "a{e:\U0001f600}", id="astral"),
    pytest.param("a{e:\\\u00e9}", "a{e:\u00e9}", id="escaped-non-ascii"),
    pytest.param("a{e:x\\0 y}", "a{e:x\ufffdy}", id="zero"),
    pytest.param("a{e:x\\d800 y}", "a{e:x\ufffdy}", id="surrogate"),
    pytest.param("a{e:x\\110000 y}", "a{e:x\ufffdy}", id="past-unicode"),
    pytest.param("a{e:x\\\x00y}", "a{e:x\ufffdy}", id="escaped-null"),
    pytest.param("a{e:x\\00000a y}", "a{e:x\\ay}", id="six-digit-line-feed"),
    pytest.param("a{e:x\\00000d}", "a{e:x\\d }", id="carriage-return-ends-name"),
    pytest.param("a{e:x\\9 y}", "a{e:x\\9y}", id="tab"),
    pytest.param("a{e:x\\7f y}", "a{e:x\\7fy}", id="delete"),
    pytest.param("a{e:\\61\\62}", "a{e:ab}", id="adjacent"),
    pytest.param("a{e:a\\31 b}", "a{e:a1b}", id="digit-inside"),
    pytest.param("a{e:\\31 a}", "a{e:\\31 a}", id="leading-digit-before-hex-letter"),
    pytest.param("a{e:\\31 x}", "a{e:\\31x}", id="leading-digit-before-other-letter"),
    pytest.param("a{e:\\31\\32}", "a{e:\\31 2}", id="leading-digit-before-digit"),
    pytest.param("a{e:\\31}", "a{e:\\31 }", id="lone-digit"),
    pytest.param(".\\31{x:1}", ".\\31{x:1}", id="lone-digit-class"),
    pytest.param("a{e:-\\31}", "a{e:\\-1}", id="hyphen-digit"),
    pytest.param("a{e:\\2d}", "a{e:\\-}", id="lone-hyphen"),
    pytest.param("a{e:\\2d x}", "a{e:-x}", id="hyphen-letter"),
    pytest.param("a{e:\\-\\-}", "a{e:\\--}", id="two-hyphens"),
    pytest.param("a{e:\\-\\->b}", "a{e:\\-->b}", id="two-hyphens-before-greater-than"),
    pytest.param("a{e:x\\,y}", "a{e:x\\,y}", id="delimiter"),
    pytest.param("a{e:x\\2c y}", "a{e:x\\,y}", id="hex-delimiter"),
    pytest.param("a{e:\\\\}", "a{e:\\\\}", id="backslash"),
    pytest.param("a{e:\\feff}", "a{e:\\feff }", id="byte-order-mark"),
    pytest.param("a{e:\\3c\\2fstyle}", "a{e:\\<\\/style}", id="end-tag"),
    pytest.param("#\\31 23{x:1}", "#\\31 23{x:1}", id="id-hash-leading-digit"),
    pytest.param("a{e:#-\\31}", "a{e:#\\-1}", id="id-hash-hyphen-digit"),
    pytest.param("a{e:#1\\32 3}", "a{e:#123}", id="unrestricted-hash"),
    pytest.param("@\\31 x;", "@\\31x;", id="at-keyword-leading-digit"),
    pytest.param("a{e:1p\\78}", "a{e:1px}", id="unit"),
    pytest.param("a{e:1\\2e 5}", "a{e:1\\.5}", id="unit-dot"),
    pytest.param("a{e:1\\25}", "a{e:1\\%}", id="unit-percent"),
    pytest.param("a{e:1\\31 x}", "a{e:1\\31x}", id="unit-leading-digit"),
    pytest.param("a{e:1\\65}", "a{e:1e}", id="unit-e"),
    pytest.param("a{e:1\\65 2}", "a{e:1\\65 2}", id="unit-e-digit"),
    pytest.param("a{e:1E\\31}", "a{e:1\\45 1}", id="unit-upper-e-digit"),
    pytest.param("a{e:1\\65 -2}", "a{e:1\\65-2}", id="unit-e-hyphen-digit"),
    pytest.param("a{e:1\\65 -x}", "a{e:1e-x}", id="unit-e-hyphen-letter"),
    pytest.param("a{e:1e3\\65 2}", "a{e:1e3e2}", id="unit-e-digit-after-exponent"),
    pytest.param("a{e:\\31  a}", "a{e:\\31  a}", id="whitespace-after-escape-space"),
    pytest.param("a{e:a\\31  b}", "a{e:a1 b}", id="whitespace-after-inner-escape-space"),
    pytest.param(".\\31  a{x:1}", ".\\31  a{x:1}", id="descendant-after-escape-space"),
    pytest.param("a{e:\\31/**/a}", "a{e:\\31  a}", id="comment-after-escape"),
    pytest.param('@ch\\61rset "x";a{b:c}', '@ch\\61rset"x";a{b:c}', id="charset"),
    pytest.param("@1\\78;a{b:c}", "@1\\78;a{b:c}", id="at-keyword-not-an-ident"),
    pytest.param("a{e:x\\\ny}", "a{e:x\\\ny}", id="escaped-newline"),
]


@pytest.mark.parametrize(("source", "expected"), _ESCAPED_NAME)
def test_minify_css_decodes_escaped_name(source: str, expected: str) -> None:
    assert minify_css(source) == expected


@pytest.mark.parametrize(("source", "expected"), _ESCAPED_NAME)
def test_minify_css_decoded_name_is_a_fixed_point(source: str, expected: str) -> None:
    assert minify_css(minify_css(source)) == expected


_POSITION_DELIMITERS: Final[list[ParameterSet]] = [
    pytest.param("left ~ .5px", "left~.5px", id="tilde"),
    pytest.param("left / .5px", "left/.5px", id="slash"),
    pytest.param("left + .5px", "left+ .5px", id="plus"),
    pytest.param("left top,right ~ .5px", "left top,right~.5px", id="whole-value"),
    pytest.param("left 0 top .5px", "0 .5px", id="edge-offsets"),
    pytest.param("left top,right bottom", "0 0,100% 100%", id="layers"),
    pytest.param("calc(10% + 1px) center", "calc(10% + 1px)center", id="function"),
]


@pytest.mark.parametrize(("value", "expected"), _POSITION_DELIMITERS)
def test_minify_css_background_position_delimiters(value: str, expected: str) -> None:
    assert minify_css(f"a{{background-position:{value}}}") == f"a{{background-position:{expected}}}"


@pytest.mark.parametrize(("value", "expected"), _POSITION_DELIMITERS)
def test_minify_css_background_position_delimiters_are_a_fixed_point(value: str, expected: str) -> None:
    assert minify_css(minify_css(f"a{{background-position:{value}}}")) == f"a{{background-position:{expected}}}"


@pytest.mark.parametrize(("value", "expected"), _POSITION_DELIMITERS)
def test_minify_css_background_position_delimiters_preserve_cascade(value: str, expected: str) -> None:
    assert minify_css(f"a{{background-position:10px 20px;background-position:{value}}}") == (
        f"a{{background-position:10px 20px;background-position:{expected}}}"
    )


_NEGATIVE_AFTER_NUMBER: Final[list[ParameterSet]] = [
    pytest.param("a{e:1e-128 -1e-128}", "a{e:1e-128-1e-128}", id="signed-exponent-boundaries"),
    pytest.param("a{e:1 -.}", "a{e:1 -.}", id="bare-minus-dot"),
    pytest.param("a{e:1 -.x}", "a{e:1 -.x}", id="minus-dot-before-ident"),
    pytest.param("a{background-position:1 -f(x)}", "a{background-position:1 -f(x)}", id="negative-function-name"),
    pytest.param("a{e:1 -1}", "a{e:1-1}", id="number"),
    pytest.param("a{e:1 -.5}", "a{e:1-.5}", id="fraction"),
    pytest.param("a{e:1e3 -1}", "a{e:1000-1}", id="after-exponent"),
    pytest.param("a{margin:0 -1px}", "a{margin:0-1px}", id="dimension"),
    pytest.param("a{e:1px -1}", "a{e:1px -1}", id="after-dimension"),
    pytest.param("a{e:1% -1}", "a{e:1% -1}", id="after-percentage"),
    pytest.param("a{e:a -1}", "a{e:a -1}", id="after-ident"),
    pytest.param("a{e:1 -a}", "a{e:1 -a}", id="ident"),
    pytest.param("a{background-position:1px -1px}", "a{background-position:1px -1px}", id="position-length"),
    pytest.param("a{background-position:1 -1}", "a{background-position:1-1}", id="position-number"),
    pytest.param("a{e:f(1-1)}", "a{e:f(1-1)}", id="function"),
    pytest.param("a{e:f(1 -1)}", "a{e:f(1 -1)}", id="function-whitespace"),
    pytest.param("a{e:f(1px -1)}", "a{e:f(1px -1)}", id="function-after-dimension"),
]


@pytest.mark.parametrize(("source", "expected"), _NEGATIVE_AFTER_NUMBER)
def test_minify_css_negative_after_number(source: str, expected: str) -> None:
    assert minify_css(source) == expected


@pytest.mark.parametrize(("source", "expected"), _NEGATIVE_AFTER_NUMBER)
def test_minify_css_negative_after_number_is_a_fixed_point(source: str, expected: str) -> None:
    assert minify_css(minify_css(source)) == expected


# past 32 rules the merge pass finds repeated selectors and bodies through a hash table before scanning back
@pytest.fixture(params=[pytest.param(1, id="short-list"), pytest.param(40, id="hashed-list")])
def filler_rules(request: pytest.FixtureRequest) -> str:
    return "".join(f".c{index}{{--p{index}:{index + 1}px}}" for index in range(request.param))


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("a{{b:c}}", "a{{b:c}}", id="nested-rule-without-selector"),
        pytest.param("a{b:}", "", id="declaration-without-value"),
    ],
)
def test_minify_css_empty_run(source: str, expected: str) -> None:
    assert minify_css(source) == expected


# an empty prelude is not a selector list, so a top-level rule with one is invalid and dropped rather than merged in
_EMPTY_SELECTOR_RULE: Final[list[ParameterSet]] = [
    pytest.param("a{color:red}{color:red}", "a{color:red}", id="after-rule-same-body"),
    pytest.param("{color:red}", "", id="lone"),
    pytest.param("a{color:red}{margin:0}", "a{color:red}", id="after-rule-other-body"),
    pytest.param(" {color:red}a{margin:0}", "a{margin:0}", id="before-rule"),
    pytest.param("@media all{ {color:red}a{margin:0}}", "@media all{a{margin:0}}", id="nested-in-media"),
]


@pytest.mark.parametrize(("source", "expected"), _EMPTY_SELECTOR_RULE)
def test_minify_css_empty_selector_rule(source: str, expected: str) -> None:
    assert minify_css(source) == expected


@pytest.mark.parametrize(("source", "expected"), _EMPTY_SELECTOR_RULE)
def test_minify_css_empty_selector_rule_is_a_fixed_point(source: str, expected: str) -> None:
    assert minify_css(minify_css(source)) == expected


def test_minify_css_same_selector_merge_is_bounded() -> None:
    # folding a whole same-selector run into one rule re-renders a body that grows per rule, O(rules^2) in time and
    # arena memory; the capped merge keeps the largest output rule the same size however many rules come in
    large: Final = _max_decls_per_rule(16 * 256)
    assert _max_decls_per_rule(4 * 256) == large
    assert large < 4 * 256


def _max_decls_per_rule(rule_count: int) -> int:
    out: Final = minify_css("".join(f"a{{--p{index}:{index}}}" for index in range(rule_count)))
    return max(body.count(":") for body in re.findall(r"\{([^}]*)\}", out))


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("a{transform:rotate(0deg)}", "a{transform:rotate(0deg)}", id="keep-zero-angle-unit"),
        pytest.param("a{transform:rotate(0grad)}", "a{transform:rotate(0grad)}", id="keep-zero-grad-unit"),
        pytest.param("a{filter:hue-rotate(0rad)}", "a{filter:hue-rotate(0rad)}", id="keep-zero-rad-in-function"),
        pytest.param(
            "a{transform:rotate(calc(45deg - 45deg))}", "a{transform:rotate(0deg)}", id="calc-keeps-angle-unit"
        ),
        pytest.param('[data-x="123"]{x:1}', '[data-x="123"]{x:1}', id="attr-keep-quotes-leading-digit"),
        pytest.param('[a="Foo"]{x:1}', "[a=Foo]{x:1}", id="attr-unquote-uppercase-ident"),
        pytest.param('[a="--x"]{x:1}', "[a=--x]{x:1}", id="attr-unquote-custom-ident"),
        pytest.param("a{background:url('a\x01b')}", "a{background:url('a\x01b')}", id="url-keep-quotes-control-char"),
        pytest.param('a{src:local("123")}', 'a{src:local("123")}', id="local-keep-quotes-non-ident"),
        pytest.param('a{src:local("Arial")}', "a{src:local(Arial)}", id="local-unquote-ident"),
        pytest.param(
            "a{--Foo:1;--foo:2;color:var(--Foo)}",
            "a{--Foo:1;--foo:2;color:var(--Foo)}",
            id="custom-property-name-case-preserved",
        ),
        pytest.param(
            "a{transition:all .5s!important,color 1s}",
            "a{transition:all .5s!important,color 1s}",
            id="important-not-last-keeps-value",
        ),
        pytest.param("a{c:fn(x !important)}", "a{c:fn(x !important)}", id="important-inside-function-kept"),
        pytest.param("a{color:rgb(0 254.5 0)}", "a{color:rgb(0 254.5 0)}", id="no-fold-inexact-modern-channel"),
        pytest.param("a{color:rgba(0,0,0,-1)}", "a{color:#0000}", id="negative-alpha-clamps-to-hex"),
        pytest.param("a{color:rgba(255,0,0,2)}", "a{color:red}", id="alpha-over-one-clamps-opaque"),
        pytest.param("a{color:hsl(120,100%,50%)}", "a{color:#0f0}", id="fold-exact-hsl"),
        pytest.param("a{color:rgb(50%,0,0)}", "a{color:rgb(50%,0,0)}", id="no-fold-inexact-percentage"),
        pytest.param("a{color:rgba(1,2,3,.5)}", "a{color:rgb(1,2,3,.5)}", id="rgba-alias-to-rgb"),
        pytest.param("a{width:calc(1px+ 2px)}", "a{width:calc(1px+ 2px)}", id="calc-one-sided-operator-not-folded"),
        pytest.param(
            "@supports (background:url(x)){a{c:d}}",
            "@supports(background:url(x)){a{c:d}}",
            id="supports-url-not-unwrapped",
        ),
        pytest.param(
            "@keyframes k{from{opacity:0}to{opacity:1}}",
            "@keyframes k{0%{opacity:0}to{opacity:1}}",
            id="keyframes-from-to-percent",
        ),
        pytest.param('a{font-family:"serif"}', 'a{font-family:"serif"}', id="keep-quotes-generic-family"),
        pytest.param(
            "@media (min-width:1px) and (max-width:2px){a{x:1}}",
            "@media(min-width:1px)and (max-width:2px){a{x:1}}",
            id="media-drop-space-before-and",
        ),
        pytest.param(
            "@supports (a:b) or (c:d){a{x:1}}", "@supports(a:b)or (c:d){a{x:1}}", id="supports-drop-space-before-or"
        ),
        pytest.param("@media (a:b) c{x:1}", "@media(a:b) c{x:1}", id="media-keep-space-before-non-combinator"),
        pytest.param("@media (a:b),(c:d){a{x:1}}", "@media(a:b),(c:d){a{x:1}}", id="media-comma-after-paren"),
        pytest.param("@media (a:b)0{x:1}", "@media(a:b)0{x:1}", id="media-non-ident-after-paren"),
        pytest.param(
            "@media (min-width:1px){a{x:1}}@media (min-width:1px){b{y:2}}",
            "@media(min-width:1px){a{x:1}b{y:2}}",
            id="merge-adjacent-identical-media",
        ),
        pytest.param(
            "@media screen{a{x:1}}@media print{b{y:2}}",
            "@media screen{a{x:1}}@media print{b{y:2}}",
            id="no-merge-different-media",
        ),
        pytest.param(
            "@media (a:b){x{y:1}}c{d:e}@media (a:b){z{w:1}}",
            "@media(a:b){x{y:1}}c{d:e}@media(a:b){z{w:1}}",
            id="no-merge-media-across-rule",
        ),
        pytest.param("@media{a{x:1}}@media{b{y:2}}", "@media{a{x:1}b{y:2}}", id="merge-media-empty-prelude"),
        pytest.param(
            "@media (a:b){x{y:1}}@media (c:d){z{w:1}}",
            "@media(a:b){x{y:1}}@media(c:d){z{w:1}}",
            id="no-merge-media-same-length-prelude",
        ),
        pytest.param(
            "@layer a{x{y:1}}@layer a{z{w:1}}", "@layer a{x{y:1}}@layer a{z{w:1}}", id="no-merge-layer-blocks"
        ),
        pytest.param(
            "@media-foo{a{b:c}}@media-foo{d{e:f}}",
            "@media-foo{a{b:c}}@media-foo{d{e:f}}",
            id="no-merge-media-prefixed-keyword",
        ),
        pytest.param('@import "a";@import "b";', '@import"a";@import"b";', id="no-merge-at-statements"),
        pytest.param("@x{}@media (a:b){c{d:e}}", "@x{}@media(a:b){c{d:e}}", id="short-at-block-before-media"),
        pytest.param("a::before{x:1}", "a:before{x:1}", id="legacy-pseudo-before-single-colon"),
        pytest.param("a::after{x:1}", "a:after{x:1}", id="legacy-pseudo-after-single-colon"),
        pytest.param("a::first-line{x:1}", "a:first-line{x:1}", id="legacy-pseudo-first-line"),
        pytest.param("a::first-letter{x:1}", "a:first-letter{x:1}", id="legacy-pseudo-first-letter"),
        pytest.param("a::selection{x:1}", "a::selection{x:1}", id="non-legacy-pseudo-keeps-double-colon"),
        pytest.param("*:hover{x:1}", ":hover{x:1}", id="drop-universal-before-pseudo-class"),
        pytest.param("*.foo{x:1}", ".foo{x:1}", id="drop-universal-before-class"),
        pytest.param("*::before{x:1}", ":before{x:1}", id="drop-universal-before-pseudo-element"),
        pytest.param("*#i{x:1}", "#i{x:1}", id="drop-universal-before-id"),
        pytest.param("*[a]{x:1}", "[a]{x:1}", id="drop-universal-before-attr"),
        pytest.param("*,*::before{x:1}", "*,:before{x:1}", id="keep-standalone-universal"),
        pytest.param("a>*{x:1}", "a>*{x:1}", id="keep-child-universal"),
        pytest.param("a:hover{x:1}", "a:hover{x:1}", id="single-colon-pseudo-class-kept"),
        pytest.param("a:{x:1}", "a:{x:1}", id="trailing-colon-no-pseudo"),
        pytest.param("[href=a:b]{x:1}", "[href=a:b]{x:1}", id="colon-inside-attr-selector"),
        pytest.param("[a*=b]{x:1}", "[a*=b]{x:1}", id="star-inside-attr-selector"),
        pytest.param("a:: c{y:1}", "a:: c{y:1}", id="double-colon-without-pseudo-name"),
        pytest.param("a:[b]{x:1}", "a:[b]{x:1}", id="colon-then-non-colon-delim"),
        pytest.param("*+b{x:1}", "*+b{x:1}", id="star-then-combinator-kept"),
        pytest.param("* p{x:1}", "* p{x:1}", id="descendant-universal-kept"),
        pytest.param(
            "a{flex-direction:var(--d);flex-wrap:wrap}",
            "a{flex-direction:var(--d);flex-wrap:wrap}",
            id="no-merge-pair-first-has-var",
        ),
        pytest.param(
            "a{align-content:center;justify-content:inherit}",
            "a{align-content:center;justify-content:inherit}",
            id="no-merge-pair-second-wide-only",
        ),
        pytest.param(
            "a{align-content:inherit;justify-content:initial}",
            "a{align-content:inherit;justify-content:initial}",
            id="no-merge-pair-different-wide-keywords",
        ),
        pytest.param("a{flex-direction:row}", "a{flex-direction:row}", id="no-merge-pair-single-longhand"),
        pytest.param(
            "a{flex-direction:row!important;flex-wrap:wrap}",
            "a{flex-direction:row!important;flex-wrap:wrap}",
            id="no-merge-pair-importance-mismatch",
        ),
        pytest.param(
            "a{align-content:inherit;justify-content:center}",
            "a{align-content:inherit;justify-content:center}",
            id="no-merge-pair-one-wide-keyword",
        ),
        pytest.param("a{flex-wrap:wrap;flex-direction:row}", "a{flex-flow:row wrap}", id="merge-pair-reversed-order"),
        pytest.param("a{flex-direction:row;flex-wrap:wrap}", "a{flex-flow:row wrap}", id="merge-flex-flow"),
        pytest.param(
            "a{align-content:center;justify-content:center}", "a{place-content:center}", id="merge-place-content-equal"
        ),
        pytest.param(
            "a{align-content:start;justify-content:end}", "a{place-content:start end}", id="merge-place-content-pair"
        ),
        pytest.param(
            "a{align-items:center;justify-items:center}", "a{place-items:center}", id="merge-place-items-equal"
        ),
        pytest.param(
            "a{align-items:start;justify-items:legacy}", "a{place-items:start legacy}", id="merge-place-items-pair"
        ),
        pytest.param("a{align-self:center;justify-self:center}", "a{place-self:center}", id="merge-place-self-equal"),
        pytest.param(
            "a{align-self:auto;justify-self:stretch}", "a{place-self:auto stretch}", id="merge-place-self-pair"
        ),
        pytest.param(
            "a{align-content:inherit;justify-content:inherit}",
            "a{place-content:inherit}",
            id="merge-pair-wide-keyword",
        ),
        pytest.param(
            "a{flex-direction:row;flex-wrap:var(--w)}",
            "a{flex-direction:row;flex-wrap:var(--w)}",
            id="no-merge-pair-with-var",
        ),
        pytest.param("a{color:transparent}", "a{color:#0000}", id="transparent-to-hex"),
        pytest.param("a{color:lightgrey}", "a{color:#d3d3d3}", id="british-grey-to-hex"),
        pytest.param(
            "a{color:hsla(210,var(--s),50%,1)}", "a{color:hsla(210,var(--s),50%,1)}", id="color-func-var-kept"
        ),
        pytest.param(r"@a\,b{c:d}", r"@a\,b{c:d}", id="escaped-at-keyword-kept"),
        pytest.param("/*a*b*/x{y:1}", "x{y:1}", id="comment-with-lone-star"),
        pytest.param("a{x:1}/* unterminated", "a{x:1}", id="unterminated-comment"),
        pytest.param("@a\\", "@a\ufffd;", id="escaped-at-keyword-backslash-at-eof"),
        pytest.param("x{}#a\\", "#a\ufffd", id="escaped-hash-backslash-at-eof"),
        pytest.param("a{x:1!important/*c*/}", "a{x:1!important}", id="important-trailing-comment"),
        pytest.param("a{x:1 !/*c*/important}", "a{x:1!important}", id="important-comment-between-bang"),
        pytest.param(r"#a\9 b{x:1}", r"#a\9 b{x:1}", id="escaped-hash-selector-kept"),
        pytest.param("a{background:url('a\x7fb')}", "a{background:url('a\x7fb')}", id="url-keep-quotes-del-char"),
        pytest.param("a{color:rgb(18,52,86)}", "a{color:#123456}", id="fold-to-six-digit-hex"),
        pytest.param("a{color:rgb(17,171,0)}", "a{color:#11ab00}", id="six-digit-hex-first-pair-equal"),
        pytest.param("a{color:rgb(17,17,171)}", "a{color:#1111ab}", id="six-digit-hex-two-pairs-equal"),
        pytest.param("a{x:1 *important}", "a{x:1*important}", id="trailing-important-after-non-bang-delim"),
        pytest.param("a{color:rgba(0,1,0,0)}", "a{color:rgb(0,1,0,0)}", id="non-zero-green-not-transparent"),
        pytest.param("a{color:rgba(0,0,1,0)}", "a{color:rgb(0,0,1,0)}", id="non-zero-blue-not-transparent"),
        pytest.param("a{width:calc(1px +(2px))}", "a{width:calc(1px +(2px))}", id="calc-no-space-after-operator"),
        pytest.param("a{width:calc(1px +)}", "a{width:calc(1px +)}", id="calc-operator-at-end"),
        pytest.param('[a="-9"]{x:1}', '[a="-9"]{x:1}', id="attr-keep-quotes-dash-digit"),
        pytest.param("a{color:red! important}", "a{color:red!important}", id="important-space-after-bang"),
        pytest.param("a{x:1 important}", "a{x:1 important}", id="trailing-important-without-bang"),
        pytest.param(r"@-webkit-keyframes k{from{x:1}}", r"@-webkit-keyframes k{0%{x:1}}", id="webkit-keyframes-from"),
        pytest.param(r"@-moz-keyframes k{0%{x:1}}", r"@-moz-keyframes k{0%{x:1}}", id="moz-keyframes"),
        pytest.param(r"@-o-keyframes k{to{x:1}}", r"@-o-keyframes k{to{x:1}}", id="o-keyframes"),
        pytest.param(
            "a{background:url(a.png);background:url(a.svg)}",
            "a{background:url(a.png);background:url(a.svg)}",
            id="dedup-keeps-different-value-fallback",
        ),
        pytest.param(
            "a{display:-webkit-box;display:flex}",
            "a{display:-webkit-box;display:flex}",
            id="dedup-keeps-prefixed-fallback",
        ),
        pytest.param("a{x:1;x:2;x:3}", "a{x:1;x:2;x:3}", id="dedup-keeps-every-differing-value"),
        pytest.param(
            "a{background:red;background:linear-gradient(red,blue)}",
            "a{background:red;background:linear-gradient(red,blue)}",
            id="dedup-keeps-shorthand-fallback",
        ),
        pytest.param("a{background:red;background:red}", "a{background:red}", id="dedup-drops-identical-shorthand"),
        pytest.param(
            "a{background-color:red;background:url(a.svg)}",
            "a{background:url(a.svg)}",
            id="dedup-drops-covered-longhand",
        ),
        pytest.param(
            "@media screen and/*x*/(min-width:0){a{b:1}}",
            "@media screen and (min-width:0){a{b:1}}",
            id="at-prelude-comment-becomes-space",
        ),
        pytest.param("@media not/*x*/all{a{b:1}}", "@media not all{a{b:1}}", id="at-prelude-comment-keeps-negation"),
        pytest.param(
            "/*! Copyright 2024   Foo    Bar\n * All rights reserved.\n */a{color:red}",
            "/*! Copyright 2024   Foo    Bar\n * All rights reserved.\n */a{color:red}",
            id="bang-comment-body-kept-verbatim",
        ),
        pytest.param(
            "a{width:calc(100% - 30px - 0)}", "a{width:calc(100% - 30px - 0)}", id="calc-keeps-unitless-zero-type-error"
        ),
        pytest.param("a{width:calc(100% - 0px)}", "a{width:100%}", id="calc-folds-zero-length"),
        pytest.param(
            "a{border-color:currentColor red}",
            "a{border-color:currentcolor red}",
            id="border-color-currentcolor-list-kept",
        ),
        pytest.param(
            "a{border-color:red currentColor}",
            "a{border-color:red currentcolor}",
            id="border-color-currentcolor-list-kept-trailing",
        ),
        pytest.param(
            "a{border-color:currentColor}", "a{border-color:initial}", id="border-color-currentcolor-sole-to-initial"
        ),
        pytest.param(
            "@font-face{unicode-range:U+0-10FFFF}", "@font-face{unicode-range:U+0-10FFFF}", id="unicode-range-full-kept"
        ),
        pytest.param(
            "a{row-gap:20px;grid:auto/auto}", "a{row-gap:20px;grid:auto/auto}", id="grid-does-not-reset-row-gap"
        ),
        pytest.param(
            "a{column-gap:20px;grid:auto/auto}",
            "a{column-gap:20px;grid:auto/auto}",
            id="grid-does-not-reset-column-gap",
        ),
        pytest.param(
            "a{grid-row-gap:20px;grid:auto/auto}",
            "a{grid-row-gap:20px;grid:auto/auto}",
            id="grid-does-not-reset-grid-row-gap",
        ),
        pytest.param(
            "a{grid-template-rows:1px;grid:auto/auto}", "a{grid:auto/auto}", id="grid-resets-grid-template-rows"
        ),
        pytest.param(
            "a{margin-top:1px;margin-top:1px;margin-right:2px;margin-bottom:3px;margin-left:4px}",
            "a{margin:1px 2px 3px 4px}",
            id="identical-longhand-duplicate-dropped-then-box-merges",
        ),
        pytest.param("a{flex:0px}", "a{flex:0px}", id="flex-lone-zero-basis-keeps-unit"),
        pytest.param("a{flex:0px 1}", "a{flex:0px 1}", id="flex-leading-zero-basis-keeps-unit"),
        pytest.param("a{flex:1 0px}", "a{flex:1 0px}", id="flex-trailing-zero-basis-keeps-unit"),
        pytest.param("a{flex:1 1 0px}", "a{flex:1 1 0}", id="flex-zero-basis-after-two-factors-drops-unit"),
        pytest.param("a{flex:0 0 0px}", "a{flex:0 0 0}", id="flex-zero-length-basis-not-omitted"),
        pytest.param("a{flex:1 1 0em}", "a{flex:1 1 0}", id="flex-zero-em-basis-drops-unit"),
        pytest.param("a{flex:1 1 0%}", "a{flex:1}", id="flex-zero-percent-basis-omitted"),
        pytest.param("a{flex:2 2 0s}", "a{flex:2 2 0s}", id="flex-zero-time-basis-kept"),
        pytest.param("a{flex:2 2 5px}", "a{flex:2 2 5px}", id="flex-nonzero-basis-kept"),
        pytest.param("a{flex:0px 1 2}", "a{flex:0px 1 2}", id="flex-basis-before-factors-keeps-unit"),
        pytest.param("a{flex:1 var(--s) 0px}", "a{flex:1 var(--s)0px}", id="flex-var-shrink-keeps-basis-unit"),
        pytest.param("a{flex-basis:0px}", "a{flex-basis:0}", id="flex-basis-zero-length-drops-unit"),
        pytest.param("a{flex-basis:0%}", "a{flex-basis:0%}", id="flex-basis-zero-percent-kept"),
        # a dropped comment or space must not let two tokens re-tokenize as one (CSS Syntax 3 §9.1): a value keeps a
        # space, a selector or custom property keeps an empty comment, since a space there changes the meaning
        pytest.param("a/**/b{c:d}", "a/**/b{c:d}", id="selector-comment-between-names"),
        pytest.param("#a/**/b{c:d}", "#a/**/b{c:d}", id="selector-comment-after-hash"),
        pytest.param("a/**/.b{c:d}", "a.b{c:d}", id="selector-comment-before-class-dropped"),
        pytest.param("a{--x:a/**/b}", "a{--x:a/**/b}", id="custom-property-comment-between-names"),
        pytest.param("a{--x:1/**/px}", "a{--x:1/**/px}", id="custom-property-comment-before-unit"),
        pytest.param("a{--x:-/**/-}", "a{--x:-/**/-}", id="custom-property-comment-between-dashes"),
        pytest.param("a{--x:/**/a}", "a{--x:a}", id="custom-property-leading-comment-dropped"),
        pytest.param("a{--x:1/**/%}", "a{--x:1/**/%}", id="custom-property-comment-before-percent"),
        pytest.param("a{--x:1/**/.5}", "a{--x:1/**/.5}", id="custom-property-comment-before-fraction"),
        pytest.param("a{--x:-/**/.5}", "a{--x:-/**/.5}", id="custom-property-comment-minus-before-fraction"),
        pytest.param("a{--x:+/**/1}", "a{--x:+/**/1}", id="custom-property-comment-plus-before-digit"),
        pytest.param("a{--x:+/**/.5}", "a{--x:+/**/.5}", id="custom-property-comment-plus-before-fraction"),
        pytest.param("a{--x:+/**/x}", "a{--x:+x}", id="custom-property-comment-plus-before-name-dropped"),
        pytest.param('a{--x:a/**/"b"}', 'a{--x:a"b"}', id="custom-property-comment-before-string-dropped"),
        pytest.param("a{b:x/**/(1)}", "a{b:x (1)}", id="value-comment-before-paren"),
        pytest.param("a{b:x (1)}", "a{b:x (1)}", id="value-space-before-paren"),
        pytest.param("a{b:1 (2)}", "a{b:1(2)}", id="value-number-before-paren-glued"),
        pytest.param("a{b:f(x/**/y)}", "a{b:f(x y)}", id="function-comment-between-names"),
        pytest.param("a{b:1 . 5}", "a{b:1. 5}", id="value-dot-before-digit"),
        pytest.param("a{b:a + 1}", "a{b:a+ 1}", id="value-plus-before-digit"),
        pytest.param("a{b:a - .5}", "a{b:a - .5}", id="value-minus-before-fraction"),
        pytest.param("a{b:x - 1}", "a{b:x - 1}", id="value-minus-before-digit"),
        pytest.param("a{b:# a}", "a{b:# a}", id="value-hash-delim-before-name"),
        pytest.param("a{b:@ a}", "a{b:@ a}", id="value-at-delim-before-name"),
        pytest.param("a{width:1 %}", "a{width:1 %}", id="value-number-before-percent"),
        pytest.param("a{b:1 .5}", "a{b:1 .5}", id="value-number-before-fraction"),
        pytest.param("a{b:1 .}", "a{b:1.}", id="value-number-before-lone-dot"),
        pytest.param("a{b:c / *d}", "a{b:c/ *d}", id="value-slash-before-star"),
        # inside an HTML <style>, `</` followed by `style` ends the element, so minifying never joins `<` and `/`
        pytest.param("a{ b : < /style }", "a{b:< /style}", id="less-than-slash-space-kept"),
        pytest.param("a{b:<\n/style}", "a{b:< /style}", id="less-than-slash-newline-becomes-space"),
        pytest.param("a{b:</**//style}", "a{b:< /style}", id="less-than-slash-comment-becomes-space"),
        pytest.param("a{b:</**//**//style}", "a{b:< /style}", id="less-than-slash-comments-become-space"),
        pytest.param("a{--x:</**//style}", "a{--x:< /style}", id="less-than-slash-custom-property"),
        pytest.param("a </**//style{b:c}", "a < /style{b:c}", id="less-than-slash-selector"),
        pytest.param("a{b:c(</**//style)}", "a{b:c(< /style)}", id="less-than-slash-function-argument"),
        pytest.param("a{b:calc(1px </**//style)}", "a{b:calc(1px < /style)}", id="less-than-slash-calc"),
        pytest.param("a{b:</**/x}", "a{b:<x}", id="less-than-comment-before-other-token"),
        pytest.param("a{b:</*x", "a{b:<}", id="less-than-comment-at-end-of-input"),
        # a stylesheet whose minified form would spell a `</style` its source kept apart comes back unchanged
        pytest.param('a{b:"</\\\nstyle"}', 'a{b:"</\\\nstyle"}', id="string-continuation-would-join-end-tag"),
        pytest.param('a{b:"<\\\n/STYLE"}', 'a{b:"<\\\n/STYLE"}', id="string-continuation-would-join-upper-end-tag"),
        pytest.param("a{b:c;</**//style:d}", "a{b:c;</**//style:d}", id="kept-declaration-would-join-end-tag"),
        pytest.param('a{b:"x\\\ny"}', 'a{b:"xy"}', id="string-continuation-dropped"),
        pytest.param('a{b:"</stylo"}', 'a{b:"</stylo"}', id="end-tag-prefix-of-other-name"),
        pytest.param('a { b : "</style" }', 'a{b:"</style"}', id="end-tag-from-source-still-minified"),
        # an @import/@namespace url() with a <url-modifier> keeps its function form (CSS Values 4 §4.5.4): moving the
        # modifier outside the url() would make @import read it as a media query and @namespace as the prefix
        pytest.param('@import url("a.css" screen);', '@import url("a.css" screen);', id="import-url-modifier-kept"),
        pytest.param('@namespace url("" e);', '@namespace url("" e);', id="namespace-url-modifier-kept"),
        pytest.param("@import url('a.css' x);", "@import url('a.css' x);", id="import-url-modifier-single-quote-kept"),
        pytest.param("@import url(a b);", "@import url(a b);", id="import-bare-url-modifier-kept"),
        pytest.param('@import url("a.css");', '@import"a.css";', id="import-url-no-modifier-unwrapped"),
        pytest.param("@import url(a.css);", '@import"a.css";', id="import-bare-url-unwrapped"),
        pytest.param("@import url();", '@import"";', id="import-empty-url-unwrapped"),
        # a `*` universal or a combinator before an attribute selector keeps its descendant-combinator space (#1034): a
        # `[` opens a fresh attribute, so a `*` before it is the universal selector, not the `*=` operator
        pytest.param("* [lang]{c:d}", "* [lang]{c:d}", id="universal-descendant-attribute"),
        pytest.param("* .a{c:d}", "* .a{c:d}", id="universal-descendant-class"),
        pytest.param("a ~ [x]{c:d}", "a~[x]{c:d}", id="sibling-combinator-before-attribute"),
        pytest.param("[ x]{c:d}", "[x]{c:d}", id="attribute-leading-space-dropped"),
        # an ident ending in an escaped delimiter extends over a following name code point (#1034): `\!` then `U` is the
        # single ident `\!U`, so the space before `U+5` is significant
        pytest.param("a{e:\\! U+5}", "a{e:\\! U+5}", id="escaped-delim-ident-before-urange"),
        pytest.param("a{e:\\! x}", "a{e:\\! x}", id="escaped-delim-ident-before-ident"),
        pytest.param("a{e:\\! (x)}", "a{e:\\! (x)}", id="escaped-delim-ident-before-paren"),
        pytest.param("a{b:var(--x) y}", "a{b:var(--x)y}", id="function-before-name-glued"),
        # a `(` or `[` the input leaves open is closed before the `}` that ends the rule (CSS Syntax 3 §5.4.7), so the
        # brace stays outside the block and re-minifying adds nothing
        pytest.param("a{d:(", "a{d:()}", id="close-open-paren-at-eof"),
        pytest.param("a{d:[1", "a{d:[1]}", id="close-open-bracket-at-eof"),
        pytest.param("a{d:(}", "a{d:(})}", id="brace-inside-open-paren-at-eof"),
        pytest.param("a{d:(a[b", "a{d:(a[b])}", id="close-nested-open-blocks-at-eof"),
        pytest.param("a{d:(]", "a{d:(])}", id="close-open-paren-over-stray-bracket"),
        pytest.param("a{d:)}", "a{d:)}", id="keep-stray-close-paren"),
        pytest.param("a{d:())", "a{d:())}", id="keep-stray-close-after-balanced-block"),
        pytest.param("a{d:(1)}", "a{d:(1)}", id="keep-balanced-bare-block"),
        pytest.param("a{d:f(1", "a{d:f(1)}", id="close-open-function-at-eof"),
        pytest.param("a{d:[1 + 2", "a{d:[1+ 2]}", id="close-open-bracket-past-operator"),
        pytest.param("a{d:[x*y", "a{d:[x*y]}", id="close-open-bracket-past-glued-operator"),
        pytest.param("a{d:[x)", "a{d:[x)]}", id="close-open-bracket-over-stray-paren"),
        pytest.param("a{d:(f(x)", "a{d:(f(x))}", id="close-open-paren-around-function"),
        pytest.param("a{--d:(", "a{--d:()}", id="custom-property-close-open-paren"),
        pytest.param("a{--d:[1", "a{--d:[1]}", id="custom-property-close-open-bracket"),
        pytest.param("a{--d:f(1", "a{--d:f(1)}", id="custom-property-close-open-function"),
        pytest.param("a{--d:(a[b", "a{--d:(a[b])}", id="custom-property-close-nested-blocks"),
        pytest.param("a{--d:[1 + 2", "a{--d:[1 + 2]}", id="custom-property-close-bracket-past-operator"),
        pytest.param("a{--d:(x)}", "a{--d:(x)}", id="custom-property-keep-balanced-block"),
        pytest.param("a{--d:(]", "a{--d:(])}", id="custom-property-close-paren-over-stray-bracket"),
        pytest.param("a{--d:([)]", "a{--d:([)])}", id="custom-property-close-paren-after-mismatched-close"),
        pytest.param(
            "a{--d:" + "(" * 65, "a{--d:" + "(" * 65 + ")" * 65 + "}", id="custom-property-close-past-mask-depth"
        ),
        pytest.param(
            "a{--d:" + "[" * 65,
            "a{--d:" + "[" * 65 + "]" * 65 + "}",
            id="custom-property-close-brackets-past-mask-depth",
        ),
        pytest.param("a{--d:[)", "a{--d:[)]}", id="custom-property-close-bracket-over-stray-paren"),
        pytest.param("a{--d:)}", "a{--d:)}", id="custom-property-keep-stray-close-paren"),
        pytest.param("a{--d:]", "a{--d:]}", id="custom-property-keep-stray-close-bracket"),
        # a "!important" inside a block still open at the end of the input is block content (CSS Syntax 3 §5.4.4 reads
        # the last two component values, and the open block is one), so the closer goes after it
        pytest.param("a{d:(x !important", "a{d:(x!important)}", id="important-inside-open-block-at-eof"),
        pytest.param("a{--d:(x !important", "a{--d:(x !important)}", id="custom-property-important-inside-open-block"),
        pytest.param("a{d:f(x !important", "a{d:f(x !important)}", id="important-inside-open-function-at-eof"),
        pytest.param("a{d:(x) !important", "a{d:(x)!important}", id="important-after-closed-block-at-eof"),
    ],
)
def test_minify_css_spec_fixes(source: str, expected: str) -> None:
    assert minify_css(source) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param('["x"] { c : d }', '["x"]{c:d}', id="quoted-name"),
        pytest.param(".a*.b { color : red }", ".a*.b{color:red}", id="universal-after-class"),
        pytest.param("a*:hover { color : red }", "a*:hover{color:red}", id="universal-after-type"),
        pytest.param('["x"="y"] { c : d }', '["x"=y]{c:d}', id="quoted-name-with-value"),
        pytest.param('[x=foo "i"] { c : d }', '[x=foo "i"]{c:d}', id="trailing-string-after-ident"),
        pytest.param('[x="foo" "i"] { c : d }', '[x=foo "i"]{c:d}', id="trailing-string-after-string"),
        pytest.param('[x = /**/ "foo"] { c : d }', "[x=foo]{c:d}", id="value-after-trivia"),
        pytest.param('[svg|x~="foo"] { c : d }', "[svg|x~=foo]{c:d}", id="qualified-attribute-value"),
        pytest.param("svg|*.a { c : d }", "svg|*.a{c:d}", id="qualified-universal"),
        pytest.param("*|*.a { c : d }", "*|*.a{c:d}", id="any-namespace-universal"),
        pytest.param("|*.a { c : d }", "|*.a{c:d}", id="empty-namespace-universal"),
        pytest.param(".a/**/*.b { c : d }", ".a*.b{c:d}", id="comment-is-not-compound-boundary"),
        pytest.param(".a /**/ *.b { c : d }", ".a .b{c:d}", id="space-is-compound-boundary"),
        pytest.param(".a >*.b { c : d }", ".a>.b{c:d}", id="child-compound-boundary"),
        pytest.param(".a +*.b { c : d }", ".a+.b{c:d}", id="adjacent-compound-boundary"),
        pytest.param(".a ~*.b { c : d }", ".a~.b{c:d}", id="sibling-compound-boundary"),
        pytest.param(".a,*.b { c : d }", ".a,.b{c:d}", id="selector-list-boundary"),
        pytest.param(".a * .b { c : d }", ".a * .b{c:d}", id="standalone-universal"),
        pytest.param(":is(.a*.b,.c) { c : d }", ":is(.a*.b,.c){c:d}", id="invalid-is-arm"),
        pytest.param(":where(a*:hover,.c) { c : d }", ":where(a*:hover,.c){c:d}", id="invalid-where-arm"),
        pytest.param(":is(*.a) { c : d }", ":is(*.a){c:d}", id="is-functional-start"),
        pytest.param(":where(*.a) { c : d }", ":where(*.a){c:d}", id="where-functional-start"),
        pytest.param(":not(*.a) { c : d }", ":not(*.a){c:d}", id="not-functional-start"),
        pytest.param(":has(*.a) { c : d }", ":has(*.a){c:d}", id="has-functional-start"),
        pytest.param(":is( /**/ *.a) { c : d }", ":is(*.a){c:d}", id="is-functional-leading-trivia"),
        pytest.param(":/**/is(*.a) { c : d }", ":is(*.a){c:d}", id="comment-before-function-name"),
        pytest.param(":unknown(*.a) { c : d }", ":unknown(*.a){c:d}", id="unknown-functional-start"),
        pytest.param(":unknown( *.a) { c : d }", ":unknown(*.a){c:d}", id="unknown-functional-leading-space"),
        pytest.param(".is(*.a) { c : d }", ".is(*.a){c:d}", id="function-without-pseudo-colon"),
        pytest.param("(*.a) { c : d }", "(*.a){c:d}", id="non-function-parenthesis"),
        pytest.param(":is/**/(*.a) { c : d }", ":is/**/(*.a){c:d}", id="comment-splits-function-token"),
        pytest.param(":is (*.a) { c : d }", ":is (*.a){c:d}", id="space-splits-function-token"),
        pytest.param("a is(*.a) { c : d }", "a is(*.a){c:d}", id="function-name-after-whitespace"),
        pytest.param("[x]*.b { c : d }", "[x]*.b{c:d}", id="universal-after-attribute"),
        pytest.param(".a:is(*.b) { c : d }", ".a:is(*.b){c:d}", id="function-inside-compound"),
        pytest.param("*.a { c : d }", ".a{c:d}", id="valid-leading-universal"),
    ],
)
def test_minify_css_selector_roles(source: str, expected: str) -> None:
    result: Final = minify_css(source)
    assert (result, minify_css(result)) == (expected, expected)


_BACKSLASH_AT_EOF: Final[list[ParameterSet]] = [
    pytest.param("a{e:f\\", "a{e:f\ufffd}", id="ident"),
    pytest.param("a{e:\\", "a{e:\ufffd}", id="lone"),
    pytest.param("a{e:#f\\", "a{e:#f\ufffd}", id="hash"),
    pytest.param("a{e:f(\\", "a{e:f(\ufffd)}", id="function-argument"),
    pytest.param("@media x\\", "@media x\ufffd;", id="at-rule-prelude"),
    pytest.param('a{e:"abc\\', 'a{e:"abc"}', id="double-quoted-string"),
    pytest.param("a{e:'abc\\", "a{e:'abc'}", id="single-quoted-string"),
    pytest.param('a{e:"abc\\\\', 'a{e:"abc\\\\"}', id="string-escaped-backslash"),
    pytest.param("a{e:f\\\\", "a{e:f\\\\}", id="escaped-backslash"),
    pytest.param("a{e:url('x\\", "a{e:url(x)}", id="single-quoted-url"),
    pytest.param('a{e:url( "x\\', "a{e:url(x)}", id="quoted-url-after-whitespace"),
    pytest.param('a{e:url("a"b\\', 'a{e:url("a"b\ufffd)}', id="url-string-then-ident"),
    pytest.param("a{e:f}/*! c \\", "a{e:f}/*! c \\", id="comment"),
]


@pytest.mark.parametrize(("source", "expected"), _BACKSLASH_AT_EOF)
def test_minify_css_backslash_at_eof(source: str, expected: str) -> None:
    assert minify_css(source) == expected


@pytest.mark.parametrize(("source", "expected"), _BACKSLASH_AT_EOF)
def test_minify_css_backslash_at_eof_is_a_fixed_point(source: str, expected: str) -> None:
    assert minify_css(minify_css(source)) == expected


_OPEN_AT_EOF: Final[list[ParameterSet]] = [
    pytest.param("a{e:url(x", "a{e:url(x)}", id="url"),
    pytest.param('a{e:url("x', "a{e:url(x)}", id="url-open-string"),
    pytest.param('a{e:url("x"', "a{e:url(x)}", id="url-closed-string"),
    pytest.param("a{e:url(", "a{e:url()}", id="url-empty"),
    pytest.param("a{e:url(x\\)", "a{e:url(x\\))}", id="url-escaped-paren"),
    pytest.param("a{e:url(x\\\\)", "a{e:url(x\\\\)}", id="url-escaped-backslash"),
    pytest.param('a{e:url("x)', 'a{e:url("x)")}', id="url-paren-in-string"),
    pytest.param('a{e:url("x\\")', 'a{e:url("x\\")")}', id="url-escaped-quote"),
    pytest.param("a{e:f(url(x", "a{e:f(url(x))}", id="url-in-function"),
    pytest.param("@import url(x", '@import"x";', id="import-url"),
    pytest.param('a{e:"x\\"', 'a{e:"x\\""}', id="string-escaped-quote"),
    pytest.param('a{e:"x\\\\"', 'a{e:"x\\\\"}', id="string-escaped-backslash"),
    pytest.param('a{e:"', 'a{e:""}', id="lone-quote"),
]


@pytest.mark.parametrize(("source", "expected"), _OPEN_AT_EOF)
def test_minify_css_closes_token_open_at_eof(source: str, expected: str) -> None:
    assert minify_css(source) == expected


@pytest.mark.parametrize(("source", "expected"), _OPEN_AT_EOF)
def test_minify_css_closes_token_open_at_eof_is_a_fixed_point(source: str, expected: str) -> None:
    assert minify_css(minify_css(source)) == expected


# a custom property's value is any token sequence, {} blocks included (CSS Variables 1 §2), so a `{` after `--name:`
# opens a block in the value instead of a nested rule (CSS Syntax 3 §5.5.5), closed at the end of input (§5.5.9)
_CUSTOM_PROPERTY_BLOCK: Final[list[ParameterSet]] = [
    pytest.param("a{--d:{1}}", "a{--d:{1}}", id="number"),
    pytest.param("a{--d:{a b}}", "a{--d:{a b}}", id="idents"),
    pytest.param("a{--d:{color:red}}", "a{--d:{color:red}}", id="declaration"),
    pytest.param("a{--d:x{1}y}", "a{--d:x{1}y}", id="between-tokens"),
    pytest.param("a{--d:{;}}", "a{--d:{;}}", id="semicolon-inside"),
    pytest.param("a{--d:{1};color:red}", "a{--d:{1};color:red}", id="declaration-after"),
    pytest.param("a{--d: { a  b } }", "a{--d:{ a b }}", id="whitespace-collapsed"),
    pytest.param("a{--d :{1}}", "a{--d:{1}}", id="space-before-colon"),
    pytest.param("a{--d/**/:{1}}", "a{--d/**/:{1}}", id="comment-before-colon"),
    pytest.param("a{\\2d-d:{1}}", "a{--d:{1}}", id="escaped-name"),
    pytest.param("a{--d:hover{color:red}}", "a{--d:hover{color:red}}", id="reads-like-nested-rule"),
    pytest.param("a{--d x{color:red}}", "a{--d x{color:red}}", id="no-colon-nested-rule"),
    pytest.param("a{-d:{c d}}", "a{-d:{}}", id="standard-property-nested-rule"),
    pytest.param("a{--d:{1", "a{--d:{1}}", id="open-at-eof"),
    pytest.param("a{--d:{{1", "a{--d:{{1}}}", id="nested-open-at-eof"),
    pytest.param("a{--d:{(1)[2]}", "a{--d:{(1)[2]}}", id="closed-blocks-at-eof"),
    pytest.param("a{--d:{1!important", "a{--d:{1!important}}", id="important-inside-open-block"),
    pytest.param("a{--d:{1} ! important", "a{--d:{1}!important}", id="important-after-closed-block"),
    pytest.param("a{--d:(1) ! important", "a{--d:(1)!important}", id="important-after-closed-paren"),
    pytest.param("a{--d:[1] ! important", "a{--d:[1]!important}", id="important-after-closed-bracket"),
    pytest.param("a{--d:{(1)!important", "a{--d:{(1)!important}}", id="important-after-paren-in-open-block"),
    pytest.param("a{--d:{[1]!important", "a{--d:{[1]!important}}", id="important-after-bracket-in-open-block"),
    pytest.param("a{--d:){1!important", "a{--d:){1!important}}", id="important-in-open-block-after-stray-close"),
    pytest.param("a{--d:){1", "a{--d:){1}}", id="stray-close-then-open-at-eof"),
]


@pytest.mark.parametrize(("source", "expected"), _CUSTOM_PROPERTY_BLOCK)
def test_minify_css_custom_property_block(source: str, expected: str) -> None:
    assert minify_css(source) == expected


@pytest.mark.parametrize(("source", "expected"), _CUSTOM_PROPERTY_BLOCK)
def test_minify_css_custom_property_block_is_a_fixed_point(source: str, expected: str) -> None:
    assert minify_css(minify_css(source)) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("--d:{1}", "--d:{1}", id="block"),
        pytest.param("--d:{1", "--d:{1}", id="open-at-eof"),
        pytest.param("--d:{1!important", "--d:{1!important}", id="important-inside-open-block"),
    ],
)
def test_minify_css_inline_custom_property_block(source: str, expected: str) -> None:
    assert minify_css_inline(source) == expected


# a stylesheet ending in a statement at-rule keeps its `;`, so another sheet appended to it starts a new rule instead of
# becoming the at-rule's block (CSS Syntax 3 §5.5.2); blocks its prelude leaves open close first (§5.5.9)
_FINAL_AT_STATEMENT: Final[list[ParameterSet]] = [
    pytest.param("@x ;", "@x;", id="whitespace-only-prelude"),
    pytest.param("@x/**/;", "@x;", id="comment-only-prelude"),
    pytest.param('@import "a";', '@import"a";', id="import"),
    pytest.param('@import "a"', '@import"a";', id="import-without-semicolon"),
    pytest.param('@namespace svg "x"', '@namespace svg"x";', id="namespace"),
    pytest.param('@charset "utf-8";', '@charset "utf-8";', id="charset"),
    pytest.param("@layer a, b;", "@layer a,b;", id="layer-statement"),
    pytest.param('a{b:c}@import "a"', 'a{b:c}@import"a";', id="after-rule"),
    pytest.param('/*! c */@import "a"', '/*! c */@import"a";', id="after-bang-comment"),
    pytest.param('@import "a";/*! c */', '@import"a";/*! c */', id="before-bang-comment"),
    pytest.param("@layer a{}", "@layer a{}", id="block-form"),
    pytest.param("@media x{@layer a;}", "@media x{@layer a}", id="nested"),
    pytest.param("@x (a", "@x(a);", id="open-paren-in-prelude"),
    pytest.param("@x [a", "@x [a];", id="open-bracket-in-prelude"),
    pytest.param("@x ({", "@x({});", id="open-brace-in-prelude"),
    pytest.param("@x ({))", "@x({));", id="open-brace-closed-by-paren-in-prelude"),
    pytest.param("@x (]", "@x(]);", id="open-paren-over-stray-bracket-in-prelude"),
    pytest.param("@x [a](b", "@x [a](b);", id="closed-bracket-before-open-paren-in-prelude"),
    pytest.param("@x ](b", "@x ](b);", id="stray-bracket-before-open-paren-in-prelude"),
    pytest.param("@media x{@x (a", "@media x{@x(a)}", id="nested-open-paren-in-prelude"),
    pytest.param("@x (}", "@x(});", id="close-brace-in-open-paren-in-prelude"),
    pytest.param("@x (};a{b:c}", "@x(};a{b:c}", id="prelude-ending-in-close-brace"),
    pytest.param('x;@import "a"', 'x@import"a"', id="after-stray-text"),
    pytest.param('x;/*! c */@import "a"', 'x/*! c */@import"a"', id="after-stray-text-and-bang-comment"),
]


@pytest.mark.parametrize(("source", "expected"), _FINAL_AT_STATEMENT)
def test_minify_css_final_at_statement(source: str, expected: str) -> None:
    assert minify_css(source) == expected


@pytest.mark.parametrize(("source", "expected"), _FINAL_AT_STATEMENT)
def test_minify_css_final_at_statement_is_a_fixed_point(source: str, expected: str) -> None:
    assert minify_css(minify_css(source)) == expected


def test_minify_css_final_at_statement_survives_concatenation() -> None:
    assert minify_css(minify_css('@import "a"') + "b{c:d}") == '@import"a";b{c:d}'


# a closed string ends at its quote and nothing reads past one (CSS Syntax 3 §4.3.5), so an at-rule prelude needs no
# space on either side of it; @charset keeps its space, as the encoding sniff matches the bytes `@charset "` (§3.2)
_AT_PRELUDE_STRING: Final[list[ParameterSet]] = [
    pytest.param('@import "a";', '@import"a";', id="import"),
    pytest.param("@import 'a';", "@import'a';", id="import-single-quote"),
    pytest.param("@import url(a.css);", '@import"a.css";', id="import-url-unwrapped"),
    pytest.param("@import url(a) screen;", '@import"a"screen;', id="import-url-unwrapped-media-query"),
    pytest.param('@import "a" screen;', '@import"a"screen;', id="import-media-query"),
    pytest.param('@import "a" (min-width:1px);', '@import"a"(min-width:1px);', id="import-media-feature"),
    pytest.param('@import "a" url(b c);', '@import"a"url(b c);', id="import-url-after-string"),
    pytest.param('@namespace svg "x";', '@namespace svg"x";', id="namespace-prefix"),
    pytest.param('@namespace "x";', '@namespace"x";', id="namespace-default"),
    pytest.param("@namespace svg url(x);", '@namespace svg"x";', id="namespace-url-unwrapped"),
    pytest.param("@supports x url(y){b{c:d}}", "@supports x url(y){b{c:d}}", id="tested-url-keeps-space"),
    pytest.param('@x "a" "b";', '@x"a""b";', id="adjacent-strings"),
    pytest.param('@charset "utf-8";a{b:c}', '@charset "utf-8";a{b:c}', id="charset-keeps-space"),
    pytest.param("@charset 'x';", "@charset 'x';", id="charset-single-quote-keeps-space"),
    pytest.param('@import "a"/**/screen;', '@import"a"screen;', id="comment-after-string"),
    pytest.param("@import 'a' screen;", "@import'a'screen;", id="single-quote-before-ident"),
    pytest.param("@import 'a' (x);", "@import'a'(x);", id="single-quote-before-paren"),
    pytest.param("@import 'a' url(b c);", "@import'a'url(b c);", id="single-quote-before-url"),
    pytest.param('@x url(a) "b";', '@x url(a)"b";', id="string-after-tested-url"),
    pytest.param('@import url(a b) "c";', '@import url(a b)"c";', id="string-after-url-with-modifier"),
    pytest.param('@x a\\" b;', '@x a\\" b;', id="escaped-quote-ident-keeps-space"),
    pytest.param('@x a\\" (b);', '@x a\\" (b);', id="escaped-quote-ident-before-paren-keeps-space"),
    pytest.param('@x a\\" url(b);', '@x a\\" url(b);', id="escaped-quote-ident-before-url-keeps-space"),
]


@pytest.mark.parametrize(("source", "expected"), _AT_PRELUDE_STRING)
def test_minify_css_at_prelude_string(source: str, expected: str) -> None:
    assert minify_css(source) == expected


@pytest.mark.parametrize(("source", "expected"), _AT_PRELUDE_STRING)
def test_minify_css_at_prelude_string_is_a_fixed_point(source: str, expected: str) -> None:
    assert minify_css(minify_css(source)) == expected


# a string cut short keeps its newline (CSS Syntax 3 §4.3.5), which separates it from the next token
@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param('@x "a\n "b";', '@x"a\n"b";', id="before-string"),
        pytest.param('@x "a\n (b);', '@x"a\n (b);', id="before-paren"),
    ],
)
def test_minify_css_at_prelude_cut_string_keeps_newline(source: str, expected: str) -> None:
    assert minify_css(source) == expected


# A newline ends a string as a <bad-string-token> (CSS Syntax 3 §4.3.5). No declaration value or selector admits one, so
# the declaration or rule holding it is dropped, as a browser drops it; the rules around it stay. An at-rule prelude
# keeps it with its newline: `@media all,"a` still matches `all` (Media Queries 4 §3.2 reads the bad query as `not
# all`), and a prelude whose grammar fails as a whole is dropped by the browser either way.
_STRING_CUT_BY_NEWLINE: Final[list[ParameterSet]] = [
    pytest.param('a{e:"\n}', "", id="lone-quote"),
    pytest.param('a{e:f("\n)}', "", id="lone-quote-in-function"),
    pytest.param('a{e:"x\\"\n}', "", id="escaped-quote-before-newline"),
    pytest.param('a::before{content:"x"}a::before{content:"\n}', 'a:before{content:"x"}', id="earlier-value-wins"),
    pytest.param('a{x:"a\n;color:red}b{c:d}', "a{color:red}b{c:d}", id="next-declaration-kept"),
    pytest.param('a{color:red;x:"a\n}b{c:d}', "a{color:red}b{c:d}", id="next-rule-kept"),
    pytest.param("a{x:'a\n;color:red}", "a{color:red}", id="single-quote"),
    pytest.param('a{x:"a\r\n;color:red}', "a{color:red}", id="crlf"),
    pytest.param('a{x:"a\r;color:red}', "a{color:red}", id="carriage-return"),
    pytest.param('a{x:"a\f;color:red}', "a{color:red}", id="form-feed"),
    pytest.param('a{x:"a\n', "", id="last-declaration"),
    pytest.param('a{x:f("a\n);color:blue}', "a{color:blue}", id="in-function"),
    pytest.param('a{b:url("x\n);color:red}e{color:blue}', "a{color:red}e{color:blue}", id="in-url"),
    pytest.param('a{b:url(x"\n);color:red}e{color:blue}', "a{color:red}e{color:blue}", id="in-unquoted-url"),
    pytest.param('a{"x\n:red;color:blue}', "a{color:blue}", id="in-property-name"),
    pytest.param('a{--x:"a\n;color:red}', "a{color:red}", id="custom-property"),
    pytest.param('a{--x:{"a\n};color:red}', "a{color:red}", id="custom-property-block"),
    pytest.param('a{--x:/**/ {b:"a\n};color:red}', "a{color:red}", id="custom-property-block-after-space"),
    pytest.param('a{--x:{b:c};y:"a\n}', "a{--x:{b:c}}", id="custom-property-block-kept"),
    pytest.param('a{color:{"a\n};color:red}', "a{color:red}", id="standard-property-block"),
    pytest.param('a{color: /**/{"a\n};color:red}', "a{color:red}", id="standard-property-block-after-space"),
    pytest.param('@font-face{font-family:"a\n;src:url(x)}', "@font-face{src:url(x)}", id="at-rule-declaration"),
    pytest.param('a{color:red;&:hover{x:"a\n;color:blue}}', "a{color:red;&:hover{color:blue}}", id="nested-rule-body"),
    pytest.param('a{color:red;b"c\n{color:blue}}', "a{color:red}", id="nested-rule-prelude"),
    pytest.param('a{b[c]{x:"a\n;color:blue}}', "a{b[c]{color:blue}}", id="nested-rule-attribute-prelude"),
    pytest.param('a{{x:"a\n}c:d}', "a{{};c:d}", id="nested-rule-empty-prelude"),
    pytest.param('a"b\n{c:d}e{f:g}', "e{f:g}", id="selector"),
    pytest.param('a[b="c\n]{color:red}e{color:blue}', "e{color:blue}", id="attribute-selector"),
    pytest.param('"a\n;b{c:d}', "", id="top-level-semicolon-joins-prelude"),
    pytest.param('"a\nb{c:d}', "", id="top-level-prelude"),
    pytest.param('@keyframes k{"a\nfrom{color:red}to{color:blue}}', "@keyframes k{to{color:blue}}", id="keyframe"),
    pytest.param('@x "a\n;b{c:d}', '@x"a\n;b{c:d}', id="at-statement-prelude"),
    pytest.param('@import "a\n;b{c:d}', '@import"a\n;b{c:d}', id="import-prelude"),
    pytest.param('@media all,"a\n{b{color:red}}', '@media all,"a\n{b{color:red}}', id="media-query-list"),
    pytest.param('@media (x:"a\n),print{b{color:red}}', '@media(x:"a\n),print{b{color:red}}', id="media-feature"),
    pytest.param('"x\n', '"x\n', id="stray-segment"),
    pytest.param('"x\n ', '"x\n', id="stray-segment-trailing-space"),
    pytest.param('"x" ', '"x"', id="stray-segment-closed"),
]


@pytest.mark.parametrize(("source", "expected"), _STRING_CUT_BY_NEWLINE)
def test_minify_css_string_cut_by_newline(source: str, expected: str) -> None:
    assert minify_css(source) == expected


@pytest.mark.parametrize(("source", "expected"), _STRING_CUT_BY_NEWLINE)
def test_minify_css_string_cut_by_newline_is_a_fixed_point(source: str, expected: str) -> None:
    assert minify_css(minify_css(source)) == expected


def test_minify_css_inline_string_cut_by_newline() -> None:
    assert minify_css_inline('color:red;x:"a\n;--y:{"b\n}') == "color:red"


# A top-level ';' or '}' is a prelude component value, so a qualified rule carrying one has an invalid selector list
# and is dropped with its block (CSS Syntax 3 §5.4.3); with no block the stray text is kept as recovery.
_STRAY_TOP_TOKEN: Final[list[ParameterSet]] = [
    pytest.param("}p{color:red}", "", id="brace-before-rule"),
    pytest.param("x;p{color:red}", "", id="semicolon-before-rule"),
    pytest.param("/}*a{color:red}b{color:blue}", "b{color:blue}", id="brace-splits-comment"),
    pytest.param("a{x:1}}b{x:1}", "a{x:1}", id="brace-between-rules"),
    pytest.param("a;b{x:1}", "", id="semicolon-joins-prelude"),
    pytest.param("x;a(b){y:1}", "", id="paren-in-invalid-prelude"),
    pytest.param("x;a[b]{y:1}", "", id="bracket-in-invalid-prelude"),
    pytest.param("x;a({}){y:1}", "", id="braces-in-invalid-prelude"),
    pytest.param("x;p{color:red", "", id="invalid-prelude-unterminated-block"),
    pytest.param("}", "", id="lone-brace"),
    pytest.param("a{x:1}}", "a{x:1}", id="trailing-brace"),
    pytest.param("a;", "a", id="stray-no-block-kept"),
]


@pytest.mark.parametrize(("source", "expected"), _STRAY_TOP_TOKEN)
def test_minify_css_stray_top_token(source: str, expected: str) -> None:
    assert minify_css(source) == expected


@pytest.mark.parametrize(("source", "expected"), _STRAY_TOP_TOKEN)
def test_minify_css_stray_top_token_is_a_fixed_point(source: str, expected: str) -> None:
    assert minify_css(minify_css(source)) == expected


# A rewrite that a calc() or color fold enables applies in the same call: the color fold reads the folded arguments,
# the shorthand handlers see a folded hex, number or dimension as such, and a comment (no token, CSS Syntax 3 §4.3.2)
# does not hide a legacy pseudo-element.
_ONE_CALL_REWRITE: Final[list[ParameterSet]] = [
    pytest.param("a{color:rgb(calc(1),0,0)}", "a{color:#010000}", id="calc-in-rgb"),
    pytest.param("a{color:hsl(calc(120),100%,50%)}", "a{color:#0f0}", id="calc-in-hsl"),
    pytest.param("a{color:rgba(calc(255),0,0,calc(1))}", "a{color:red}", id="calc-in-alpha"),
    pytest.param(
        "a{background:linear-gradient(rgb(calc(1),0,0),red)}",
        "a{background:linear-gradient(#010000,red)}",
        id="calc-in-nested-color",
    ),
    pytest.param("a{color:rgb(calc(var(--a)),0,0)}", "a{color:rgb(calc(var(--a)),0,0)}", id="calc-kept-in-color"),
    pytest.param("a{color:rgb(calc(1),0)}", "a{color:rgb(1,0)}", id="calc-in-color-of-no-shape"),
    pytest.param("a{background:rgba(0,0,0,0) url(x)}", "a{background:url(x)}", id="folded-transparent"),
    pytest.param("a{background:calc(2px) 50%/10px}", "a{background:2px/10px}", id="folded-length"),
    pytest.param("a{box-shadow:calc(0px) 0 0 rgba(0,0,0,0)}", "a{box-shadow:0 0 #0000}", id="folded-zero"),
    pytest.param("a{background-position:calc(0%) calc(0%)}", "a{background-position:0 0}", id="folded-percentage"),
    pytest.param("a{font:calc(400) 1em x}", "a{font:1em x}", id="folded-number"),
    pytest.param("a{background:rgb(255,0,0) 0 0}", "a{background:red}", id="folded-keyword"),
    pytest.param("a{x:rgb(1,0,0) (y)}", "a{x:#010000(y)}", id="folded-hex-before-block"),
    pytest.param("a{x:rgb(255,0,0) (y)}", "a{x:red (y)}", id="folded-keyword-before-block"),
    pytest.param("a{background-position:calc(50%) calc(50%)}", "a{background-position:50%}", id="folded-dimension"),
    pytest.param("a::/**/before{color:red}", "a:before{color:red}", id="comment-before-pseudo-element"),
    pytest.param("a:/**/:before{color:red}", "a:before{color:red}", id="comment-between-colons"),
    pytest.param("a:/**/:/**/first-letter{color:red}", "a:first-letter{color:red}", id="comments-around-colon"),
    pytest.param("a::/**/selection{color:red}", "a::selection{color:red}", id="comment-before-modern-pseudo"),
    pytest.param("a:/**/hover{color:red}", "a:hover{color:red}", id="comment-before-pseudo-class"),
    pytest.param("a:/**/.b{color:red}", "a:.b{color:red}", id="comment-before-delimiter"),
    pytest.param("a::{color:red}", "a::{color:red}", id="colons-without-name"),
    pytest.param("a:/**/{color:red}", "a:{color:red}", id="colon-before-comment"),
]


@pytest.mark.parametrize(("source", "expected"), _ONE_CALL_REWRITE)
def test_minify_css_one_call_rewrite(source: str, expected: str) -> None:
    assert minify_css(source) == expected


@pytest.mark.parametrize(("source", "expected"), _ONE_CALL_REWRITE)
def test_minify_css_one_call_rewrite_is_a_fixed_point(source: str, expected: str) -> None:
    assert minify_css(minify_css(source)) == expected


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("abs(0px)", id="abs"),
        pytest.param("acos(0deg)", id="acos"),
        pytest.param("asin(0px)", id="asin"),
        pytest.param("atan(0px)", id="atan"),
        pytest.param("atan2(0px,1px)", id="atan2"),
        pytest.param("clamp(0px,1vw,2px)", id="clamp"),
        pytest.param("cos(0px)", id="cos"),
        pytest.param("exp(0px)", id="exp"),
        pytest.param("hypot(0px,3px)", id="hypot"),
        pytest.param("log(0px)", id="log"),
        pytest.param("max(0px,1vw)", id="max"),
        pytest.param("min(0px,1vw)", id="min"),
        pytest.param("mod(0px,1px)", id="mod"),
        pytest.param("pow(0px,2)", id="pow"),
        pytest.param("rem(0px,1px)", id="rem"),
        pytest.param("round(0px,1px)", id="round"),
        pytest.param("ROUND(up,0px,1px)", id="round-upper-case"),
        pytest.param("sign(0px)", id="sign"),
        pytest.param("sin(0px)", id="sin"),
        pytest.param("sqrt(0px)", id="sqrt"),
        pytest.param("tan(0px)", id="tan"),
    ],
)
def test_minify_css_math_function_keeps_zero_unit(value: str) -> None:
    assert minify_css(f"a{{width:{value}}}") == f"a{{width:{value}}}"


_HASH_FILLER = "".join(f"--v{index}:{index};" for index in range(40))


@pytest.mark.parametrize(
    ("tail", "kept"),
    [
        pytest.param(
            "background:url(a.png);background:url(a.svg)",
            "background:url(a.png);background:url(a.svg)",
            id="same-name-fallback-kept",
        ),
        pytest.param("color:red;color:red", "color:red", id="same-name-identical-dropped"),
        pytest.param(
            "background:red;background:url(a.svg)", "background:red;background:url(a.svg)", id="shorthand-fallback-kept"
        ),
        pytest.param("background:red;background:red", "background:red", id="shorthand-identical-dropped"),
        pytest.param(
            "background-color:red;background:url(a.svg)", "background:url(a.svg)", id="covered-longhand-dropped"
        ),
    ],
)
def test_minify_css_hash_dedup_is_value_safe(tail: str, kept: str) -> None:
    # more than 32 declarations force the hash-based dedup path, which must apply the same value-safety rule.
    assert minify_css(f"a{{{_HASH_FILLER}{tail}}}") == f"a{{{_HASH_FILLER}{kept}}}"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("color:#ffffff", "color:#fff", id="hex-shorten"),
        pytest.param("margin:0px", "margin:0", id="drop-unit"),
        pytest.param("color:red ; padding:0.5px", "color:red;padding:.5px", id="strip-spacing"),
        pytest.param("width:calc(2px*3)", "width:6px", id="calc"),
    ],
)
def test_minify_css_inline(source: str, expected: str) -> None:
    assert minify_css_inline(source) == expected


@pytest.mark.parametrize(
    ("source", "widely", "newly"),
    [
        pytest.param(
            "a{top:0;right:0;bottom:0;left:0}", "a{top:0;right:0;bottom:0;left:0}", "a{inset:0}", id="inset-merge"
        ),
        pytest.param(
            "a{overflow-x:hidden;overflow-y:auto}",
            "a{overflow-x:hidden;overflow-y:auto}",
            "a{overflow:hidden auto}",
            id="overflow-merge",
        ),
        pytest.param(
            "a{row-gap:1em;column-gap:2em}",
            "a{row-gap:1em;column-gap:2em}",
            "a{gap:1em 2em}",
            id="gap-merge-row-column",
        ),
        pytest.param(
            "a{margin-inline-start:1px;margin-inline-end:2px}",
            "a{margin-inline-start:1px;margin-inline-end:2px}",
            "a{margin-inline:1px 2px}",
            id="margin-inline-merge",
        ),
        pytest.param(
            "a{padding-block-start:1px;padding-block-end:1px}",
            "a{padding-block-start:1px;padding-block-end:1px}",
            "a{padding-block:1px}",
            id="padding-block-merge-collapsed",
        ),
        pytest.param(
            "a{inset-inline-start:1px;inset-inline-end:2px}",
            "a{inset-inline-start:1px;inset-inline-end:2px}",
            "a{inset-inline:1px 2px}",
            id="inset-inline-merge",
        ),
        pytest.param(
            "a{top:0;right:0;bottom:0}",
            "a{top:0;right:0;bottom:0}",
            "a{top:0;right:0;bottom:0}",
            id="inset-three-not-merged",
        ),
    ],
)
def test_minify_css_baseline(source: str, widely: str, newly: str) -> None:
    assert minify_css(source) == widely
    assert minify_css(source, _NEWLY) == newly


def test_minify_css_baseline_year_is_a_threshold() -> None:
    source = "a{top:0;right:0;bottom:0;left:0}"
    assert minify_css(source, CSSMinify(baseline=2020)) == source
    assert minify_css(source, CSSMinify(baseline=2021)) == "a{inset:0}"


def test_logical_shorthand_kept_when_physical_alias_present() -> None:
    # margin-inline-start aliases margin-left by writing mode, so the shorthand could reorder against it: no merge.
    source = "a{margin-left:9px;margin-inline-start:1px;margin-inline-end:2px}"
    assert minify_css(source, CSSMinify(baseline=2021)) == source


def test_minify_css_inline_takes_baseline() -> None:
    assert minify_css_inline("top:0;right:0;bottom:0;left:0", _NEWLY) == "inset:0"


# each shape nests one bracket kind, paired with the deepest count the minifier still descends into; the unterminated
# shapes never close, so the bail reaches end-of-input with no matching brace to consume
_NESTING_SHAPES: Final = """
import sys
import threading

from turbohtml.clean import minify_css

SHAPES = {
    "blocks": (lambda n: "@media screen{" * n + "a{color:red}" + "}" * n, 98),
    "declarations": (lambda n: "a{" + "&{" * n + "color:red" + "}" * n + "}", 98),
    "functions": (lambda n: "a{b:" + "f(" * n + "1.0px" + ")" * n + "}", 99),
    "calc": (lambda n: "a{b:calc(" + "(" * n + "1px" + ")" * n + ")}", 98),
    "nested-calc": (lambda n: "a{b:" + "calc(" * n + "1px" + ")" * n + "}", 99),
    "blocks-unterminated": (lambda n: "@media screen{" * n, 98),
    "declarations-unterminated": (lambda n: "a{" + "&{" * n, 98),
}
if sys.argv[1] == "past-cap":
    for name, (build, _) in SHAPES.items():
        out = minify_css(build(50000))
        print(name, minify_css(out) == out)
else:
    threading.stack_size(128 * 1024)
    results = {}
    worker = threading.Thread(
        target=lambda: results.update((name, minify_css(build(deepest))) for name, (build, deepest) in SHAPES.items())
    )
    worker.start()
    worker.join()
    for name, (build, deepest) in SHAPES.items():
        print(name, results[name] == minify_css(build(deepest)))
"""
_SHAPE_NAMES: Final = (
    "blocks",
    "declarations",
    "functions",
    "calc",
    "nested-calc",
    "blocks-unterminated",
    "declarations-unterminated",
)


@pytest.mark.parametrize(
    "mode",
    [
        # without the cap the recursive grammar, calc and function parsers overflow the C stack
        pytest.param("past-cap", id="past-cap-on-the-main-thread"),
        # a 128 KiB thread holds the deepest supported nesting only while each level's C frames stay small
        pytest.param("at-cap", id="at-cap-on-a-128-kib-thread"),
    ],
)
def test_minify_css_survives_deep_nesting(mode: str) -> None:
    # a regression aborts the interpreter, so each mode runs every shape in one child
    result = subprocess.run(  # ruff:ignore[subprocess-without-shell-equals-true]
        [sys.executable, "-c", _NESTING_SHAPES, mode], capture_output=True, text=True, timeout=120, check=False
    )
    assert (result.returncode, result.stdout) == (0, "".join(f"{name} True\n" for name in _SHAPE_NAMES)), result.stderr


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            "@media screen{" * 98 + "a{color:red}" + "}" * 98,
            "@media screen{" * 98 + "a{color:red}" + "}" * 98,
            id="blocks-at-cap",
        ),
        pytest.param("@media screen{" * 99 + "a{color:red}" + "}" * 99, "", id="blocks-past-cap"),
        pytest.param(
            "a{" + "&{" * 98 + "color:red" + "}" * 98 + "}",
            "a{" + "&{" * 98 + "color:red" + "}" * 98 + "}",
            id="declarations-at-cap",
        ),
        pytest.param(
            "a{" + "&{" * 99 + "color:red" + "}" * 99 + "}", "a{" + "&{" * 99 + "}" * 100, id="declarations-past-cap"
        ),
        pytest.param(
            "a{b:" + "f(" * 99 + "1.0px" + ")" * 99 + "}",
            "a{b:" + "f(" * 99 + "1px" + ")" * 99 + "}",
            id="functions-at-cap",
        ),
        pytest.param(
            "a{b:" + "f(" * 100 + "1.0px" + ")" * 100 + "}",
            "a{b:" + "f(" * 100 + "1.0px" + ")" * 100 + "}",
            id="functions-past-cap",
        ),
        pytest.param("a{b:calc(" + "(" * 98 + "1px" + ")" * 98 + ")}", "a{b:1px}", id="calc-at-cap"),
        pytest.param(
            "a{b:calc(" + "(" * 99 + "1px" + ")" * 99 + ")}",
            "a{b:calc(" + "(" * 99 + "1px" + ")" * 99 + ")}",
            id="calc-past-cap",
        ),
        pytest.param("a{b:" + "calc(" * 99 + "1px" + ")" * 99 + "}", "a{b:1px}", id="nested-calc-at-cap"),
        pytest.param(
            "a{b:" + "calc(" * 100 + "1px" + ")" * 100 + "}",
            "a{b:" + "calc(" * 100 + "1px" + ")" * 100 + "}",
            id="nested-calc-past-cap",
        ),
    ],
)
def test_minify_css_nesting_cap_boundary(source: str, expected: str) -> None:
    assert minify_css(source) == expected


def test_empty_input() -> None:
    assert (minify_css(""), minify_css_inline("")) == ("", "")


def test_public_api_is_exported() -> None:
    assert clean.minify_css is minify_css
    assert clean.minify_css_inline is minify_css_inline
    assert clean.CSSMinify is CSSMinify
    assert {"minify_css", "minify_css_inline", "CSSMinify"} <= set(clean.__all__)


def test_non_str_argument_raises_type_error() -> None:
    with pytest.raises(TypeError, match="argument must be str"):
        minify_css(123)  # ty: ignore[invalid-argument-type]  # intentional non-str exercises the C str guard


def test_non_int_baseline_raises_type_error() -> None:
    with pytest.raises(TypeError):
        # a non-int baseline reaches the C argument parser, which rejects it
        minify_css("a{}", CSSMinify(baseline="newest"))  # ty: ignore[invalid-argument-type]


_DIMENSION_UNIT: Final[list[ParameterSet]] = [
    pytest.param("a{e:1px\u00e9}", "a{e:1px\u00e9}", id="non-ascii"),
    pytest.param("a{e:1\u00e9}", "a{e:1\u00e9}", id="non-ascii-first"),
    pytest.param("a{e:1p\\78}", "a{e:1px}", id="escape"),
    pytest.param("a{e:1x2}", "a{e:1x2}", id="digit"),
    pytest.param("a{e:1_x}", "a{e:1_x}", id="underscore"),
    pytest.param("a{e:1-x}", "a{e:1-x}", id="hyphen"),
    pytest.param("a{e:1--x}", "a{e:1--x}", id="two-hyphens"),
    pytest.param("a{e:1-\\78}", "a{e:1-x}", id="hyphen-escape"),
    pytest.param("a{e:1px-2px}", "a{e:1px-2px}", id="hyphen-dimension"),
    pytest.param("a{e:1px\\", "a{e:1px\ufffd}", id="backslash-at-eof"),
    pytest.param("a{e:1\\", "a{e:1\ufffd}", id="unit-backslash-at-eof"),
    pytest.param("a{e:1-1}", "a{e:1-1}", id="hyphen-digit"),
    pytest.param("a{e:1-}", "a{e:1 -}", id="hyphen-delimiter"),
    pytest.param("a{e:1-", "a{e:1 -}", id="hyphen-at-eof"),
    pytest.param("a{e:1\\\n}", "a{e:1 \\\n}", id="escaped-line-feed"),
    pytest.param("a{e:1\\\r}", "a{e:1 \\\r}", id="escaped-carriage-return"),
    pytest.param("a{e:1\\\f}", "a{e:1 \\\f}", id="escaped-form-feed"),
    pytest.param("a{e:f(1px+1px)}", "a{e:f(1px 1px)}", id="signed-dimension-after-dimension"),
    pytest.param("a{e:f(1+1)}", "a{e:f(1 1)}", id="signed-number-after-number"),
    pytest.param("a{e:f(+1)}", "a{e:f(1)}", id="signed-number-first"),
    pytest.param("a{e:f(1,+1)}", "a{e:f(1,1)}", id="signed-number-after-comma"),
    # a unit that reads as an exponent (e/E then an optional sign and a digit) must not fuse with a shortened number
    pytest.param("a{e:1e1e3px}", "a{e:1e1e3px}", id="exponent-unit"),
    pytest.param("a{e:1e1e3}", "a{e:1e1e3}", id="exponent-unit-bare"),
    pytest.param("a{e:1E1E3px}", "a{e:1E1E3px}", id="exponent-unit-upper"),
    pytest.param("a{e:1e1e-3px}", "a{e:1e1e-3px}", id="exponent-unit-negative"),
    pytest.param("a{e:1e5e3px}", "a{e:1e5e3px}", id="exponent-unit-keeps-scientific"),
    pytest.param("a{e:1ex}", "a{e:1ex}", id="exponent-like-unit-x-height"),
    pytest.param("a{e:1e-}", "a{e:1e-}", id="e-unit-trailing-hyphen"),
    pytest.param("a{e:1e}", "a{e:1e}", id="e-unit-bare"),
]


@pytest.mark.parametrize(("source", "expected"), _DIMENSION_UNIT)
def test_minify_css_dimension_unit(source: str, expected: str) -> None:
    assert minify_css(source) == expected


@pytest.mark.parametrize(("source", "expected"), _DIMENSION_UNIT)
def test_minify_css_dimension_unit_is_a_fixed_point(source: str, expected: str) -> None:
    assert minify_css(minify_css(source)) == expected


# shortening a number (dropping a zero unit or a sign) must keep a separator so it does not read as the neighboring
# token, in an at-rule prelude and inside calc(), as the declaration-value path already does
_NUMBER_BOUNDARY: Final[list[ParameterSet]] = [
    pytest.param("@x 0px.5;", "@x 0 .5;", id="at-prelude-zero-unit-before-fraction"),
    pytest.param("@x a.-0;", "@x a. 0;", id="at-prelude-dot-before-signed-zero"),
    pytest.param("@x (a)0px.5{b:c}", "@x(a)0 .5{b:c}", id="at-prelude-with-block"),
    pytest.param("a{width:calc(0-0)}", "a{width:calc(0 0)}", id="calc-minus-zero"),
    pytest.param("a{width:calc(0+0)}", "a{width:calc(0 0)}", id="calc-plus-zero"),
    pytest.param("a{width:calc(1px+2px)}", "a{width:calc(1px 2px)}", id="calc-dim-plus-dim"),
    pytest.param("a{width:calc(5-.5)}", "a{width:calc(5-.5)}", id="calc-digit-before-neg-fraction"),
]


@pytest.mark.parametrize(("source", "expected"), _NUMBER_BOUNDARY)
def test_minify_css_number_boundary(source: str, expected: str) -> None:
    assert minify_css(source) == expected


@pytest.mark.parametrize(("source", "expected"), _NUMBER_BOUNDARY)
def test_minify_css_number_boundary_is_a_fixed_point(source: str, expected: str) -> None:
    assert minify_css(minify_css(source)) == expected


def test_lone_surrogate_raises_encode_error() -> None:
    # a lone surrogate has no UTF-8 form, so the engine cannot take its byte view
    with pytest.raises(UnicodeEncodeError):
        minify_css("a{content:'\ud800'}")


_GOLDEN: Final[list[list[str]]] = json.loads(
    (Path(__file__).parent / "css_minify_golden.json").read_text(encoding="utf-8")
)

# Malformed/invalid inputs with no closing delimiter or balance: error recovery keeps the broken tail verbatim, which
# is not a fixed point under re-minification. Output is still deterministic and pinned; only round-trip safety is moot.
_UNSTABLE: Final[frozenset[str]] = frozenset({
    "a{width:calc((1px + 2px}", "a{width:calc((1px}", "a{width:calc((", "a{width:calc((1px+2px",
    "a{color:rgba(10 20 30 .5)}",
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


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            "@media screen{a{x:1}}@media screen{b{x:2}}@media screen{c{x:3}}",
            "@media screen{a{x:1}b{x:2}c{x:3}}",
            id="media-run",
        ),
        pytest.param(
            "@media all{a{color:red}}@media all{a{margin:0}}",
            "@media all{a{color:red;margin:0}}",
            id="media-merge-inner-same-selector",
        ),
        pytest.param(
            "@media all{a{x:1}}@media all{b{x:1}}",
            "@media all{a,b{x:1}}",
            id="media-merge-inner-same-body",
        ),
        pytest.param(
            "@media screen{a{x:1}} @media screen{b{x:1}}",
            "@media screen{a,b{x:1}}",
            id="media-merge-across-whitespace",
        ),
        pytest.param(
            "@media screen{a{x:1}}/*x*/@media screen{b{x:1}}",
            "@media screen{a,b{x:1}}",
            id="media-merge-across-plain-comment",
        ),
        pytest.param(
            "@media screen{a{x:1}}@media screen",
            "@media screen{a{x:1}}@media screen;",
            id="media-followed-by-blockless-at-eof",
        ),
        pytest.param(
            "@media screen{a{x:1}}@media screen;",
            "@media screen{a{x:1}}@media screen;",
            id="media-followed-by-at-statement",
        ),
        pytest.param("@media screen{a{x:1}}/*", "@media screen{a{x:1}}", id="media-followed-by-unterminated-comment"),
        pytest.param(
            "@media print{a{x:1}}@media screen{a{x:1}}@media screen{b{x:1}}",
            "@media print{a{x:1}}@media screen{a,b{x:1}}",
            id="media-merge-after-different-query",
        ),
        pytest.param(
            "@media screen{a{x:1}}@supports (x:y){b{x:1}}",
            "@media screen{a{x:1}}@supports(x:y){b{x:1}}",
            id="media-followed-by-other-at-rule",
        ),
        pytest.param(
            "@media screen{a{x:1}}@layer x{a{x:1}}@layer x{b{x:1}}",
            "@media screen{a{x:1}}@layer x{a{x:1}}@layer x{b{x:1}}",
            id="media-followed-by-same-length-at-rule",
        ),
        pytest.param("a{x:1}b{x:1}c{x:1}", "a,b,c{x:1}", id="selector-run"),
        pytest.param("a{x:1}b{x:1}a,b{x:1}", "a,b{x:1}", id="growing-list-equality"),
        pytest.param("a{x:1}b{x:1}a{x:1}", "a,b,a{x:1}", id="repeated-selector"),
        pytest.param("a{x:1}b{y:1}c{x:1}b{x:1}", "a,c{x:1}b{y:1;x:1}", id="earlier-selector-claim"),
        pytest.param("a{x:1}b{x:1}/*!keep*/c{x:1}", "a,b{x:1}/*!keep*/c{x:1}", id="selector-comment-barrier"),
        pytest.param("a{x:1}b{x:1}c{y:1}", "a,b{x:1}c{y:1}", id="different-declaration-body"),
        pytest.param(
            "@media screen{a{x:1}}q{x:2}@media screen{b{x:3}}",
            "@media screen{a{x:1}}q{x:2}@media screen{b{x:3}}",
            id="media-qualified-rule-barrier",
        ),
        pytest.param(
            "@media screen{a{x:1}}@media speech{b{x:2}}",
            "@media screen{a{x:1}}@media speech{b{x:2}}",
            id="media-equal-length-different-prelude",
        ),
        pytest.param(
            "@media screen{a{x:1}}/*!keep*/@media screen{b{x:2}}",
            "@media screen{a{x:1}}/*!keep*/@media screen{b{x:2}}",
            id="media-comment-barrier",
        ),
        pytest.param("@media a{x{y:z}}.a{}@media a{b{y:z}}", "@media a{x,b{y:z}}", id="media-merge-across-empty-rule"),
        pytest.param(
            "@media a{x{y:z}}{c:d}@media a{b{y:z}}", "@media a{x,b{y:z}}", id="media-merge-across-invalid-rule"
        ),
        pytest.param(
            "@media a{x{y:z}}@media b{}@media a{b{y:z}}", "@media a{x,b{y:z}}", id="media-merge-across-empty-media"
        ),
        pytest.param(
            "@media a{x{y:z}}@media b{.e{}}@media a{b{y:z}}",
            "@media a{x,b{y:z}}",
            id="media-merge-across-emptied-media",
        ),
        pytest.param(
            "@media a{x{y:z}}@supports (q:r){}@media a{b{y:z}}",
            "@media a{x,b{y:z}}",
            id="media-merge-across-empty-supports",
        ),
        pytest.param(
            "@media a{x{y:z}}.a{}@media a{x{q:r}}", "@media a{x{y:z;q:r}}", id="media-merge-across-empty-same-selector"
        ),
        pytest.param(
            "@media a{x{y:z}}.a{}@media a{b{y:z}}.b{}@media a{c{y:z}}",
            "@media a{x,b,c{y:z}}",
            id="media-merge-chain-across-empty-rules",
        ),
        pytest.param(
            "@media a{x{y:z}}.a{}@media a{b{y:z}}@media c{d{y:z}}",
            "@media a{x,b{y:z}}@media c{d{y:z}}",
            id="media-merge-across-empty-rule-then-other-query",
        ),
        pytest.param(
            "@media a{x{y:z}}.a{}@media a{b{y:z}}@font-face{f:g}",
            "@media a{x,b{y:z}}@font-face{f:g}",
            id="media-merge-across-empty-rule-then-other-at-rule",
        ),
        pytest.param(
            "@media a{x{y:z}}.a{}@media a{b{y:z}}@media c{d{y:z}}.a{}@media c{f{y:z}}",
            "@media a{x,b{y:z}}@media c{d,f{y:z}}",
            id="media-merge-two-runs-across-empty-rules",
        ),
        pytest.param(
            "@media a{@import b}.a{}@media a{@import c}",
            "@media a{@import b;@import c}",
            id="media-merge-across-empty-rule-after-at-statement",
        ),
        pytest.param(
            "@supports (q:r){@media a{x{y:z}}.a{}@media a{b{y:z}}}",
            "@supports(q:r){@media a{x,b{y:z}}}",
            id="media-merge-across-empty-rule-nested",
        ),
        pytest.param("@media a{x{y:z}}.a{}@media a{}", "@media a{x{y:z}}", id="media-empty-twin-across-empty-rule"),
        pytest.param(
            "@media a{@media b{x{y:z}}}@media a{@media b{q{y:z}}}",
            "@media a{@media b{x,q{y:z}}}",
            id="media-merge-nested-twins-of-merged-blocks",
        ),
        pytest.param(".a{}@media a{x{y:z}}", "@media a{x{y:z}}", id="media-after-leading-empty-rule"),
        pytest.param(
            '@import "x";.a{}@media screen{a{x:1}}',
            '@import"x";@media screen{a{x:1}}',
            id="media-after-empty-rule-after-short-node",
        ),
        pytest.param(
            "@media a{x{y:z}}.a{}@media bb{y{y:z}}",
            "@media a{x{y:z}}@media bb{y{y:z}}",
            id="media-different-length-prelude-across-empty-rule",
        ),
        pytest.param(
            "@media screen{a{x:1}}.a{}@media speech{b{x:2}}",
            "@media screen{a{x:1}}@media speech{b{x:2}}",
            id="media-equal-length-different-prelude-across-empty-rule",
        ),
        pytest.param(
            "@media a{x{y:z}}.a{}/*!k*/@media a{b{y:z}}",
            "@media a{x{y:z}}/*!k*/@media a{b{y:z}}",
            id="media-comment-barrier-after-empty-rule",
        ),
    ],
)
def test_minify_css_batch_merge_order(source: str, expected: str) -> None:
    assert minify_css(source) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            "@media all{a{color:red}}@media all{a{margin:0}}",
            "@media all{a{color:red;margin:0}}",
            id="same-selector",
        ),
        pytest.param("@media all{a{x:1}}@media all{b{x:1}}", "@media all{a,b{x:1}}", id="same-body"),
        pytest.param("@media a{x{y:z}}.a{}@media a{b{y:z}}", "@media a{x,b{y:z}}", id="across-empty-rule"),
    ],
)
def test_minify_css_merged_media_is_a_fixed_point(source: str, expected: str) -> None:
    assert minify_css(source) == expected
    assert minify_css(minify_css(source)) == expected


@pytest.mark.parametrize(
    "case_index",
    [
        pytest.param(0, id="media10"),
        pytest.param(1, id="media100"),
        pytest.param(2, id="media1000"),
        pytest.param(3, id="selectors10"),
        pytest.param(4, id="selectors100"),
        pytest.param(5, id="selectors1000"),
        pytest.param(6, id="different-media"),
        pytest.param(7, id="comments"),
    ],
)
def test_minify_css_merge_shared_inputs(case_index: int) -> None:
    source: Final = cast("str", INPUTS["minify-css-merges"]()[case_index][1])
    count: Final = (10, 100, 1_000)[case_index % 3]
    expected: Final = (
        "@media screen{" + ",".join(f".a{index}" for index in range(count)) + "{color:red}}"
        if case_index < 3
        else ",".join(f".a{index}" for index in range(count)) + "{color:red}"
        if case_index < 6
        else source
    )
    assert minify_css(source) == expected


@pytest.mark.parametrize(
    "case", [pytest.param(0, id="long-values"), pytest.param(1, id="short-values"), pytest.param(2, id="single-pair")]
)
def test_minify_css_disjoint_rule_benchmark(case: int) -> None:
    source: Final = cast("str", INPUTS["minify-css-conflicts"]()[case][1])
    first_pair: Final = source[: source.index("}", source.index("}") + 1) + 1]
    assert minify_css(source) == first_pair


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            "a{color:red}b{margin:0}a{margin-left:1px}",
            "a{color:red}b{margin:0}a{margin-left:1px}",
            id="shorthand-barrier",
        ),
        pytest.param(
            "a{color:red}b{margin-left:1px}a{margin:0}",
            "a{color:red}b{margin-left:1px}a{margin:0}",
            id="longhand-barrier",
        ),
        pytest.param(
            "a{color:red}b{all:initial}a{width:1px}", "a{color:red}b{all:initial}a{width:1px}", id="all-barrier"
        ),
        pytest.param(
            'a{color:red}b{content:";"}a{width:1px}', 'a{color:red}b{content:";"}a{width:1px}', id="string-barrier"
        ),
        pytest.param("a{--X:1}b{--x:2}a{height:3px}", "a{--X:1;height:3px}b{--x:2}", id="custom-case-sensitive"),
        pytest.param(
            'a{color:red}b{width:1px}a{content:";"}',
            'a{color:red}b{width:1px}a{content:";"}',
            id="incoming-string-barrier",
        ),
        pytest.param(
            "a{color:red}b{width:1px}a{all:initial}", "a{color:red}b{width:1px}a{all:initial}", id="incoming-all"
        ),
        pytest.param(
            "a{color:red}b{width:1px}a{width:2px}", "a{color:red}b{width:1px}a{width:2px}", id="same-property"
        ),
        pytest.param(
            "a{color:red}b{margin:0}a{border-color:red}",
            "a{color:red;border-color:red}b{margin:0}",
            id="disjoint-shorthands",
        ),
        pytest.param(
            "a{--first:1}b{--second:2}c{--third:3}a{--last:4}",
            "a{--first:1;--last:4}b{--second:2}c{--third:3}",
            id="reuse-across-two-barriers",
        ),
        pytest.param(
            "a{--first:1}b{--other:url(data:image/png;base64,AAAA)}a{--mine:2}",
            "a{--first:1;--mine:2}b{--other:url(data:image/png;base64,AAAA)}",
            id="url-semicolon",
        ),
        pytest.param(
            "a{color:red}b{width:1px}a{margin:0}",
            "a{color:red;margin:0}b{width:1px}",
            id="incoming-shorthand-unrelated-barrier",
        ),
    ],
)
def test_minify_css_large_rule_conflict_barriers(source: str, expected: str) -> None:
    prefix: Final = "".join(f".pad{index}{{--pad{index}:{index}}}" for index in range(32))
    assert minify_css(prefix + source) == prefix + expected


@pytest.mark.oracle
@pytest.mark.parametrize(
    ("operation", "case"),
    [
        pytest.param(operation, case, id=f"{operation}-{case}")
        for operation, cases in (("minify-css-merges", (0, 2, 3, 5, 6, 7)), ("minify-css-conflicts", (0, 1, 2)))
        for case in cases
    ],
)
@pytest.mark.parametrize(
    ("module", "executable"),
    [
        pytest.param("bench.core", None, id="turbohtml"),
        pytest.param("bench.competitors.rcssmin", None, id="rcssmin"),
        pytest.param("bench.competitors.cssmin", None, id="cssmin"),
        pytest.param("bench.competitors.csscompressor", None, id="csscompressor"),
        pytest.param("bench.competitors.css_html_js_minify", None, id="css-html-js-minify"),
        pytest.param("bench.competitors.lightningcss", None, id="lightningcss"),
        pytest.param("bench.competitors.esbuild", "esbuild", id="esbuild"),
        pytest.param("bench.competitors.tdewolff", "minify", id="tdewolff"),
    ],
)
def test_merge_benchmark_preserves_fixture_cascade(
    operation: str, case: int, module: str, executable: str | None
) -> None:
    if executable is not None and shutil.which(executable) is None:
        pytest.skip(f"{executable} not available")
    minimize: Final = cast("Callable[[str], str]", pytest.importorskip(module).minify_css)
    source: Final = cast("str", INPUTS[operation]()[case][1])
    expected: Final[dict[tuple[str, str], dict[str, str]]]
    if operation == "minify-css-merges":
        count: Final = (10, 100, 1_000, 10, 100, 1_000, 100, 100)[case]
        expected = {
            (("screen" if index % 2 else "print") if case == 6 else "screen" if case < 3 else "all", f".a{index}"): {
                "color": "red"
            }
            for index in range(count)
        }
    else:
        properties, value_length = ((32, 128), (32, 1), (2, 1))[case]
        expected = {
            ("all", f".{name}"): {f"--{name}{index}": f"f({value * value_length})" for index in range(properties)}
            for name, value in (("a", "x"), ("b", "y"))
        }
    assert _merge_fixture_cascade(minimize(source)) == expected


@pytest.mark.oracle
def _merge_fixture_cascade(source: str, media: str = "all") -> dict[tuple[str, str], dict[str, str]]:
    parser: Final = pytest.importorskip("tinycss2")
    colors: Final = pytest.importorskip("tinycss2.color3")
    result: Final[dict[tuple[str, str], dict[str, str]]] = {}
    for rule_index, rule in enumerate(parser.parse_stylesheet(source, skip_comments=True, skip_whitespace=True)):
        if rule.type == "at-rule":
            if rule.lower_at_keyword == "charset":
                assert (rule_index, media, rule.content) == (0, "all", None)
                assert parser.serialize(rule.prelude).strip().lower() == '"utf-8"'
                continue
            assert rule.lower_at_keyword == "media"
            assert rule.content is not None
            condition: Final = parser.serialize(rule.prelude).strip()
            assert media == "all"
            assert condition in {"screen", "print"}
            for key, nested_properties in _merge_fixture_cascade(parser.serialize(rule.content), condition).items():
                result.setdefault(key, {}).update(nested_properties)
            continue
        assert rule.type == "qualified-rule"
        declarations: Final[dict[str, str]] = {}
        for declaration in parser.parse_declaration_list(rule.content, skip_comments=True, skip_whitespace=True):
            assert declaration.type == "declaration"
            assert not declaration.important
            value = parser.serialize(declaration.value).strip()
            if declaration.lower_name == "color":
                assert colors.parse_color(value) == (1.0, 0.0, 0.0, 1.0)
                value = "red"
            else:
                assert re.fullmatch(r"--[ab][0-9]+", declaration.name)
            declarations[declaration.name] = value
        for raw_selector in parser.serialize(rule.prelude).split(","):
            selector: Final = raw_selector.strip()
            assert re.fullmatch(r"\.[ab][0-9]*", selector)
            result.setdefault((media, selector), {}).update(declarations)
    return result


@pytest.mark.parametrize("count", [8, 64, 65, 1024])
@pytest.mark.parametrize("reverse", [False, True], ids=["sorted", "reversed"])
def test_minify_css_unicode_range_order(count: int, *, reverse: bool) -> None:
    ranges: Final = [f"U+{index * 2:X}" for index in range(count)]
    source: Final = ",".join(reversed(ranges) if reverse else ranges)
    assert minify_css("@font-face{unicode-range:" + source + "}") == (
        "@font-face{unicode-range:" + ",".join(ranges) + "}"
    )


@pytest.mark.parametrize(
    ("ranges", "expected"),
    [
        pytest.param(("U+108-11F", "U+100-10F"), "U+100-11F", id="overlapping"),
        pytest.param(("U+110-11F", "U+100-10F"), "U+100-11F", id="adjacent"),
        pytest.param(("U+1??", "U+100-17F", "U+100-1FF"), "U+1??", id="wildcard-and-equal-starts"),
    ],
)
def test_minify_css_unicode_range_union(ranges: tuple[str, ...], expected: str) -> None:
    assert minify_css("@font-face{unicode-range:" + ",".join(ranges * 40) + "}") == (
        "@font-face{unicode-range:" + expected + "}"
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("width:calc(0e10000px + 1px)", "width:1px", id="zero-positive-exponent"),
        pytest.param("width:calc(0e-10000px + 1px)", "width:1px", id="zero-negative-exponent"),
        pytest.param("width:calc(0e999999999999999999999px + 1px)", "width:1px", id="zero-overflowing-exponent"),
        pytest.param("width:-0e999999999999999999999px", "width:0", id="negative-zero-overflowing-exponent"),
        pytest.param("width:calc(1e+2px + 2e+1px)", "width:120px", id="ordinary-arithmetic"),
        pytest.param("width:calc(1e-2px + 2e-2px)", "width:.03px", id="negative-exponent-arithmetic"),
        pytest.param("width:calc(.000000000000000001e18px + 1px)", "width:2px", id="exponent-cancels-fraction"),
        pytest.param("width:calc(1e18px + 1px)", "width:1000000000000000001px", id="maximum-rational-integer-power"),
        pytest.param("width:calc(1e-18px)", "width:1e-18px", id="maximum-rational-fraction-power"),
        pytest.param("z-index:1e4", "z-index:1e4", id="integer-role-exponent"),
        pytest.param("x:1e127", "x:1" + "0" * 127, id="maximum-integer-expansion"),
        pytest.param("x:1e-127", "x:." + "0" * 126 + "1", id="maximum-fraction-expansion"),
        pytest.param("x:" + "0" * 127 + "1", "x:1", id="maximum-mantissa"),
    ],
)
def test_number_exponent_bounds(source: str, expected: str) -> None:
    assert minify_css_inline(source) == expected


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("1e10000", id="oversized-unitless-expansion"),
        pytest.param("1e128", id="integer-expansion-boundary"),
        pytest.param("1e-128", id="fraction-expansion-boundary"),
        pytest.param("1e10000px", id="large-dimension"),
        pytest.param("1e9223372036854775808px", id="exponent-add-overflow"),
        pytest.param("1e92233720368547758070px", id="exponent-multiply-overflow"),
        pytest.param("10e9223372036854775807px", id="scale-add-overflow"),
        pytest.param(".01e-9223372036854775807px", id="scale-subtract-overflow"),
        pytest.param("1e2147483648px", id="exponent-int-upper-bound"),
        pytest.param("1e-2147483648px", id="exponent-int-lower-bound"),
        pytest.param("1" * 129, id="long-mantissa"),
        pytest.param("0" * 128 + "1", id="nonzero-after-buffer-boundary"),
        pytest.param("." + "0" * 128 + "1", id="long-fraction"),
        pytest.param("calc(1e19px + 1px)", id="rational-power-upper-bound"),
        pytest.param("calc(99e17px + 1px)", id="rational-numerator-overflow"),
        pytest.param("calc(1e-19px + 1px)", id="rational-power-lower-bound"),
        pytest.param("calc(.01e-9223372036854775807px + 1px)", id="rational-power-subtract-overflow"),
        pytest.param("calc(1e9223372036854775808px + 1px)", id="rational-exponent-overflow"),
    ],
)
def test_number_outside_formatter_bounds_keeps_whole_token(value: str) -> None:
    assert minify_css_inline(f" x: {value} ; ") == f"x:{value}"


@pytest.mark.parametrize("depth", [pytest.param(4, id="small"), pytest.param(64, id="deep")])
def test_nested_function_spacing(depth: int) -> None:
    argument: Final = "var(--x, " * depth + "1px" + ")" * depth
    expected: Final = "width:" + "var(--x," * depth + "1px" + ")" * depth
    assert (minify_css(f"a {{ width: {argument}; }}"), minify_css_inline(f" width: {argument}; ")) == (
        f"a{{{expected}}}",
        expected,
    )
