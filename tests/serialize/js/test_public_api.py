"""The public ``turbohtml.clean.minify_js`` entry point and its ``JSMinify`` options.

These exercise the configuration surface end to end: each ``JSMinify`` toggle maps to
one optional pass (folding, mangling) over the always-on whitespace minification, the
function fails loudly on input the parser cannot handle, and the options object is a
frozen, comparable value.
"""

from __future__ import annotations

import pytest

import turbohtml
from turbohtml import Html, Minify, _html
from turbohtml.clean import JSMinify, minify_js

_SOURCE = "function f(){var longName=true;return longName}"


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        pytest.param(None, "function f(){return!0}", id="default-is-full"),
        pytest.param(JSMinify(), "function f(){return!0}", id="explicit-full"),
        pytest.param(JSMinify(mangle=False), "function f(){var longName=!0;return longName}", id="fold-keep-names"),
        pytest.param(JSMinify(fold=False), "function f(){return true}", id="mangle-no-fold"),
        pytest.param(
            JSMinify(mangle=False, fold=False),
            "function f(){var longName=true;return longName}",
            id="whitespace-only",
        ),
    ],
)
def test_minify_js_options(options: JSMinify | None, expected: str) -> None:
    assert minify_js(_SOURCE, options) == expected


def test_minify_js_raises_on_unhandled_syntax() -> None:
    with pytest.raises(ValueError, match="at offset"):
        minify_js("function (")


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        # the never-fail contract: an unparsable script comes back byte-exact instead of raising
        pytest.param("function (", "function (", id="unparsable-verbatim"),
        # leniency only changes the failure path; a parsable script still minifies
        pytest.param(_SOURCE, "function f(){return!0}", id="valid-still-minifies"),
    ],
)
def test_minify_js_passthrough(source: str, expected: str) -> None:
    assert minify_js(source, on_error="passthrough") == expected


def test_minify_js_rejects_unknown_on_error() -> None:
    with pytest.raises(ValueError, match="on_error must be 'raise' or 'passthrough', not 'skip'"):
        minify_js(_SOURCE, on_error="skip")  # ty: ignore[invalid-argument-type]  # bad mode on purpose


def test_minify_js_rejects_non_string() -> None:
    with pytest.raises(TypeError, match="source must be a str"):
        minify_js(123)  # ty: ignore[invalid-argument-type]  # wrong type on purpose, to test the guard


def test_low_level_binding_requires_all_arguments() -> None:
    # the public wrapper always passes (source, fold, mangle, on_error); the seam rejects a short call
    with pytest.raises(TypeError):
        _html._minify_js("x")  # ty: ignore[missing-argument]  # too few args on purpose


def _serialize_script(html: str) -> str:
    return turbohtml.parse(html).serialize(Html(layout=Minify(minify_js=JSMinify())))


def test_inline_script_keeps_bang_comment() -> None:
    # the license header survives minification of an inline <script>, as it does for the standalone call
    out = _serialize_script("<script>/*! lib v1 */\nfunction plus(a,b){return a+b}</script>")
    assert out == "<script>/*! lib v1 */function plus(b,a){return b+a}</script>"


def test_inline_script_unparsable_falls_back_verbatim() -> None:
    # a script the parser cannot handle is emitted unchanged (the errlen==0 opt-out), so one bad
    # <script> never breaks document serialization the way the standalone call's ValueError would
    assert _serialize_script("<script>function (</script>") == "<script>function (</script>"


@pytest.mark.parametrize(
    ("options", "text"),
    [
        pytest.param(JSMinify(), "JSMinify(mangle=True, fold=True)", id="defaults"),
        pytest.param(JSMinify(mangle=False), "JSMinify(mangle=False, fold=True)", id="no-mangle"),
    ],
)
def test_jsminify_repr(options: JSMinify, text: str) -> None:
    assert repr(options) == text


def test_jsminify_is_frozen_and_comparable() -> None:
    assert JSMinify() == JSMinify(mangle=True, fold=True)
    assert JSMinify(fold=False) != JSMinify()
    with pytest.raises(AttributeError):
        JSMinify().mangle = False  # ty: ignore[invalid-assignment]  # frozen on purpose
