"""Pinned behavior of the CSS minifier (minify_css / minify_css_inline) and its public API.

Each case fixes one value-safe transform to an explicit expected string. The exact output of the whole corpus, and the
round-trip-safety property, are enforced at scale in test_css_minify_corpus.py.
"""

from __future__ import annotations

import pytest

from turbohtml import clean
from turbohtml.clean import Baseline, CSSMinify, minify_css, minify_css_inline

_NEWLY = CSSMinify(baseline=Baseline.NEWLY_AVAILABLE)


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
        pytest.param("a{color:red;color:blue}", "a{color:blue}", id="dedup-later-wins"),
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
        pytest.param("/*! keep me */a{color:red}", "/*!keep me*/a{color:red}", id="keep-bang-comment"),
        pytest.param(".x{color:red}.y{color:red}", ".x,.y{color:red}", id="merge-identical-bodies-to-selector-list"),
        pytest.param("a{color:red}a{background:blue}", "a{color:red;background:blue}", id="merge-same-selector-bodies"),
        pytest.param(
            "@media screen and (min-width:100px){a{color:red}}",
            "@media screen and (min-width:100px){a{color:red}}",
            id="at-media-block",
        ),
    ],
)
def test_minify_css(source: str, expected: str) -> None:
    assert minify_css(source) == expected


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
    ],
)
def test_minify_css_spec_fixes(source: str, expected: str) -> None:
    assert minify_css(source) == expected


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


def test_minify_css_inline_takes_baseline() -> None:
    assert minify_css_inline("top:0;right:0;bottom:0;left:0", _NEWLY) == "inset:0"


def test_empty_input() -> None:
    assert (minify_css(""), minify_css_inline("")) == ("", "")


def test_public_api_is_exported() -> None:
    assert clean.minify_css is minify_css
    assert clean.minify_css_inline is minify_css_inline
    assert (clean.Baseline, clean.CSSMinify) == (Baseline, CSSMinify)
    assert {"minify_css", "minify_css_inline", "Baseline", "CSSMinify"} <= set(clean.__all__)


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
