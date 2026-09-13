"""CSS minification with pinned tdewolff/minify and native regression cases.

Malformed corpus inputs retain exact-output checks but can lack stable round-trip results.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

import pytest
from bench.operations import INPUTS

from turbohtml import clean
from turbohtml.clean import CSSMinify, minify_css, minify_css_inline

if TYPE_CHECKING:
    from collections.abc import Callable

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
        pytest.param('@import "x"', '@import "x"', id="keep-import-statement"),
    ],
)
def test_minify_css(source: str, expected: str) -> None:
    assert minify_css(source) == expected


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
        pytest.param("a{width:calc(1px+ 2px)}", "a{width:calc(1px + 2px)}", id="calc-one-sided-operator-not-folded"),
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
        pytest.param('@import "a";@import "b";', '@import "a";@import "b"', id="no-merge-at-statements"),
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
        pytest.param("@a\\", "@a\\", id="escaped-at-keyword-backslash-at-eof"),
        pytest.param("x{}#a\\", "#a\\", id="escaped-hash-backslash-at-eof"),
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
        pytest.param("a{width:calc(1px +(2px))}", "a{width:calc(1px + (2px))}", id="calc-no-space-after-operator"),
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
    ],
)
def test_minify_css_spec_fixes(source: str, expected: str) -> None:
    assert minify_css(source) == expected


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


def test_lone_surrogate_raises_encode_error() -> None:
    # a lone surrogate has no UTF-8 form, so the engine cannot take its byte view
    with pytest.raises(UnicodeEncodeError):
        minify_css("a{content:'\ud800'}")


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


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            "@media screen{a{x:1}}@media screen{b{x:2}}@media screen{c{x:3}}",
            "@media screen{a{x:1}b{x:2}c{x:3}}",
            id="media-run",
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
    ],
)
def test_minify_css_batch_merge_order(source: str, expected: str) -> None:
    assert minify_css(source) == expected


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
        "@media screen{" + "".join(f".a{index}{{color:red}}" for index in range(count)) + "}"
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
        pytest.param("z-index:1e4", "z-index:10000", id="bounded-unitless-expansion"),
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
