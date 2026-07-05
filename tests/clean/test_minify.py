"""Tests for turbohtml.clean.minify, the one-call HTML minifier over the Minify layout."""

from __future__ import annotations

import pytest

from turbohtml import Minify
from turbohtml.clean import Minify as CleanMinify
from turbohtml.clean import minify

_DOC = "<html><head><title>Hi</title></head><body><p class='lead'>one</p>  <p>two</p><!--note--></body></html>"


def test_minify_collapses_whitespace_and_omits_tags() -> None:
    assert minify(_DOC) == "<title>Hi</title><p class=lead>one</p> <p>two"


def test_minify_none_matches_default_options() -> None:
    assert minify(_DOC) == minify(_DOC, Minify())


def test_clean_reexports_minify_config() -> None:
    assert CleanMinify is Minify


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        pytest.param(Minify(omit_optional_tags=False), True, id="keep-optional-tags"),
        pytest.param(Minify(collapse_whitespace=False), True, id="keep-whitespace"),
        pytest.param(Minify(unquote_attributes=False), True, id="keep-quotes"),
        pytest.param(Minify(strip_comments=False), True, id="keep-comments"),
    ],
)
def test_minify_options_thread_through(options: Minify, *, expected: bool) -> None:
    assert (minify(_DOC, options) != minify(_DOC)) is expected


def test_minify_keep_comments_retains_comment() -> None:
    assert "<!--note-->" in minify(_DOC, Minify(strip_comments=False))


def test_minify_keep_optional_tags_retains_html_and_body() -> None:
    out = minify(_DOC, Minify(omit_optional_tags=False))
    assert out.startswith("<html><head>")
    assert "<body>" in out


def test_minify_keep_quotes_retains_attribute_quotes() -> None:
    assert 'class="lead"' in minify(_DOC, Minify(unquote_attributes=False))


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(_DOC, id="document"),
        pytest.param("<pre>  keep   spaces  </pre>", id="preformatted"),
        pytest.param("<p>one</p><script>let x = 1 + 2;</script>", id="raw-text"),
        pytest.param("<table><tbody><tr><td>a</td><td>b</td></tr></tbody></table>", id="table"),
        pytest.param("<!doctype html><html><body><p>x</p></body></html>", id="doctyped"),
        pytest.param("", id="empty"),
    ],
)
def test_minify_is_idempotent(source: str) -> None:
    once = minify(source)
    assert minify(once) == once


def test_minify_shrinks_documents() -> None:
    big = "<!doctype html><html><body>" + "<p class='x'>  text  </p>\n" * 200 + "</body></html>"
    assert len(minify(big)) < len(big)
