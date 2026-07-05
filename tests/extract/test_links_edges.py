from __future__ import annotations

import pytest

from turbohtml import parse_fragment


def _urls(html: str) -> list[str]:
    return [link.url for link in parse_fragment(html).links()]


@pytest.mark.parametrize(
    "attr",
    [
        pytest.param("title", id="len5-default"),
        pytest.param("tabindex", id="len8-non-url"),
        pytest.param("spellcheck", id="len10-non-url"),
        pytest.param("autocapitalize", id="other-length"),
    ],
)
def test_non_url_attributes_of_varied_length_are_ignored(attr: str) -> None:
    assert _urls(f'<span {attr}="not-a-url">t</span>') == []


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        pytest.param("5; url=next.html", ["next.html"], id="standard"),
        pytest.param('0;url="dq.html"', ["dq.html"], id="double-quoted-url"),
        pytest.param("5; url=", [], id="empty-url-after-equals"),
        pytest.param("url=only.html", ["only.html"], id="url-first"),
        pytest.param("5; URL = spaced.html", ["spaced.html"], id="spaced-around-equals"),
        pytest.param("5; url=a.html ignored", ["a.html"], id="unquoted-url-ends-at-whitespace"),
        pytest.param("5", [], id="no-url-keyword"),
        pytest.param("5; urlx=no.html", [], id="url-not-followed-by-equals"),
        pytest.param("5; curl=no.html", [], id="url-is-tail-of-identifier"),
        pytest.param("0;url", [], id="url-keyword-at-end"),
        pytest.param("0;url=''", [], id="empty-quoted-url"),
    ],
)
def test_meta_refresh_content_parsing(content: str, expected: list[str]) -> None:
    assert _urls(f"<meta http-equiv='refresh' content='{content}'>") == expected


@pytest.mark.parametrize(
    "http_equiv",
    [pytest.param("content-type", id="wrong-keyword"), pytest.param("default", id="same-length-keyword")],
)
def test_meta_with_non_refresh_http_equiv_has_no_link(http_equiv: str) -> None:
    assert _urls(f"<meta http-equiv='{http_equiv}' content='url=x.html'>") == []


def test_meta_refresh_with_valueless_content_has_no_link() -> None:
    assert _urls("<meta http-equiv=refresh content>") == []


@pytest.mark.parametrize(
    ("css", "expected"),
    [
        pytest.param("@import 'a.css'", ["a.css"], id="import-single"),
        pytest.param('@import "a.css"', ["a.css"], id="import-double"),
        pytest.param("@import url(a.css)", ["a.css"], id="import-url-form"),
        pytest.param("@import ", [], id="import-no-target"),
        pytest.param("@import ''", [], id="import-empty-string"),
        pytest.param("@import 'unclosed", ["unclosed"], id="import-unclosed-string-runs-to-end"),
        pytest.param("@media screen {}", [], id="other-at-rule"),
        pytest.param("background:url('unclosed", ["unclosed"], id="unclosed-quote-runs-to-end"),
        pytest.param("background:url(", [], id="empty-url-at-end"),
        pytest.param("a-b_c:url(z.png)", ["z.png"], id="dash-and-underscore-are-identifier-bytes"),
    ],
)
def test_style_element_css_parsing(css: str, expected: list[str]) -> None:
    assert _urls(f"<style>{css}</style>") == expected


def test_whitespace_only_space_list_yields_nothing() -> None:
    assert _urls('<object archive="    "></object>') == []
