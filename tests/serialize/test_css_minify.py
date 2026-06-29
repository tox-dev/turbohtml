"""Pinned behavior of the CSS minifier (minify_css / minify_css_inline) and its public API.

Each case fixes one value-safe transform to an explicit expected string. The exact output of the whole corpus, and the
round-trip-safety property, are enforced at scale in test_css_minify_corpus.py.
"""

from __future__ import annotations

import pytest

from turbohtml import clean
from turbohtml.clean import minify_css, minify_css_inline


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("a{color:#ffffff}", "a{color:#fff}", id="hex-shorten-6-to-3"),
        pytest.param("a{color:#AABBCC}", "a{color:#abc}", id="hex-lowercase-and-shorten"),
        pytest.param("a{color:#ff0000}", "a{color:red}", id="hex-to-shorter-name"),
        pytest.param("a{color:rgb(255,0,0)}", "a{color:red}", id="rgb-to-name"),
        pytest.param("a{color:rgba(0,0,0,0)}", "a{color:transparent}", id="rgba-zero-to-transparent"),
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
        pytest.param("color:#ffffff", "color:#fff", id="hex-shorten"),
        pytest.param("margin:0px", "margin:0", id="drop-unit"),
        pytest.param("color:red ; padding:0.5px", "color:red;padding:.5px", id="strip-spacing"),
        pytest.param("width:calc(2px*3)", "width:6px", id="calc"),
    ],
)
def test_minify_css_inline(source: str, expected: str) -> None:
    assert minify_css_inline(source) == expected


def test_empty_input() -> None:
    assert (minify_css(""), minify_css_inline("")) == ("", "")


def test_public_api_is_exported() -> None:
    assert clean.minify_css is minify_css
    assert clean.minify_css_inline is minify_css_inline
    assert {"minify_css", "minify_css_inline"} <= set(clean.__all__)


def test_non_str_argument_raises_type_error() -> None:
    with pytest.raises(TypeError, match="argument must be str"):
        minify_css(123)  # ty: ignore[invalid-argument-type]  # intentional non-str exercises the C str guard


def test_lone_surrogate_raises_encode_error() -> None:
    # a lone surrogate has no UTF-8 form, so the engine cannot take its byte view
    with pytest.raises(UnicodeEncodeError):
        minify_css("a{content:'\ud800'}")
