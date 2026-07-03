from __future__ import annotations

import pytest

from turbohtml import parse


@pytest.mark.parametrize(
    ("html", "fallback", "expected"),
    [
        pytest.param('<base href="/sub/page">', "http://site.com/dir/", "http://site.com/sub/page", id="relative-base"),
        pytest.param('<base href="http://abs.com/x">', "http://site.com/", "http://abs.com/x", id="absolute-base"),
        pytest.param("<p>no base</p>", "http://site.com/", "http://site.com/", id="no-base"),
        pytest.param("<base>", "http://site.com/", "http://site.com/", id="valueless-href"),
        pytest.param('<base href="  ">', "http://site.com/", "http://site.com/", id="blank-href"),
    ],
)
def test_base_url(html: str, fallback: str, expected: str) -> None:
    assert parse(html).base_url(fallback) == expected


def test_base_url_default_fallback() -> None:
    assert parse('<base href="page">').base_url() == "page"


@pytest.mark.parametrize(
    ("href", "fallback", "expected"),
    [
        pytest.param(
            "http://example.org/sterling£", "https://example.org", "http://example.org/sterling%C2%A3", id="latin1-path"
        ),
        pytest.param(
            "http://x/p q?a=b c&e=f'#h i",
            "http://x",
            "http://x/p%20q?a=b%20c&e=f%27#h%20i",
            id="all-components-encoded",
        ),
        pytest.param("http://h?a=b c", "http://x", "http://h?a=b%20c", id="authority-then-query"),
        pytest.param("http://h#f g", "http://x", "http://h#f%20g", id="authority-then-fragment"),
        pytest.param("http://h/p#f g", "http://x", "http://h/p#f%20g", id="fragment-without-query"),
        pytest.param("http://h", "http://x", "http://h", id="authority-only"),
        pytest.param("ab/cd", "", "ab/cd", id="relative-without-scheme"),
        pytest.param("/x", "", "/x", id="protocol-relative-root"),
        pytest.param("http://x/a%20b?c=%7e", "http://x", "http://x/a%20b?c=%7e", id="existing-escapes-untouched"),
        pytest.param(
            "rel", "http://x/\U0001f389\udce9/", "http://x/%F0%9F%8E%89%EF%BF%BD/rel", id="high-char-and-surrogate"
        ),
    ],
)
def test_base_url_percent_encodes(href: str, fallback: str, expected: str) -> None:
    # the resolved base URL applies the WHATWG path/query/fragment percent-encode sets, so it is a valid URL string
    assert parse(f'<base href="{href}">').base_url(fallback) == expected


_SITE = "http://s.com/"


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        pytest.param("5; url=next", (5.0, _SITE + "next"), id="delay-and-url"),
        pytest.param("10", (10.0, _SITE), id="delay-only"),
        pytest.param("2.5;url=x", (2.5, _SITE + "x"), id="float-delay"),
        pytest.param("2.5", (2.5, _SITE), id="float-delay-only"),
        pytest.param("5;url=", (5.0, _SITE), id="url-prefix-empty-value"),
        pytest.param("5;url=£x", (5.0, _SITE + "%C2%A3x"), id="two-byte-utf8-encoded"),
        pytest.param("5;url=€page", (5.0, _SITE + "%E2%82%ACpage"), id="three-byte-utf8-encoded"),
        pytest.param("5;url=\U0001f389", (5.0, _SITE + "%F0%9F%8E%89"), id="four-byte-utf8-encoded"),
        pytest.param("  7 ; url = y ", (7.0, _SITE + "y"), id="surrounding-whitespace"),
        pytest.param("3;url='q'", (3.0, _SITE + "q"), id="single-quoted-url"),
        pytest.param("8, z", (8.0, _SITE + "z"), id="comma-separator-no-prefix"),
        pytest.param("9;next", (9.0, _SITE + "next"), id="no-url-prefix"),
        pytest.param("1;urlx", (1.0, _SITE + "urlx"), id="url-prefix-without-equals"),
        pytest.param("5;uxxx", (5.0, _SITE + "uxxx"), id="u-without-rl-prefix"),
        pytest.param("5;urxx", (5.0, _SITE + "urxx"), id="ur-without-l-prefix"),
        pytest.param("5;url", (5.0, _SITE + "url"), id="bare-url-keyword-no-equals"),
        pytest.param("5;url='", (5.0, _SITE + "'"), id="lone-quote-after-url"),
        pytest.param("6;", (6.0, _SITE), id="separator-without-url"),
        pytest.param("2;ab", (2.0, _SITE + "ab"), id="short-url-after-separator"),
        pytest.param("0;url='unbalanced", (0.0, _SITE + "'unbalanced"), id="unbalanced-quote-kept"),
        pytest.param("0;url=a:b", (0.0, "a:b"), id="opaque-scheme-no-authority"),
    ],
)
def test_meta_refresh_content(content: str, expected: tuple[float, str]) -> None:
    assert parse(f'<meta http-equiv=refresh content="{content}">').meta_refresh(_SITE) == expected


def test_base_url_rejects_non_str_fallback() -> None:
    with pytest.raises(TypeError):
        parse("<base href=x>").base_url(123)  # ty: ignore[invalid-argument-type]  # non-str exercises the TypeError path


def test_meta_refresh_rejects_non_str_fallback() -> None:
    with pytest.raises(TypeError):
        parse("<meta http-equiv=refresh content='5'>").meta_refresh(123)  # ty: ignore[invalid-argument-type]  # non-str


def test_meta_refresh_double_quoted_url() -> None:
    # a single-quoted attribute lets the url use double quotes
    html = "<meta http-equiv=refresh content='4;url=\"d\"'>"
    assert parse(html).meta_refresh(_SITE) == (4.0, _SITE + "d")


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param('<meta http-equiv=Refresh content="5;url=x">', (5.0, "x"), id="mixed-case-equiv"),
        pytest.param('<meta http-equiv="  refresh  " content="5;url=x">', (5.0, "x"), id="padded-equiv"),
        pytest.param(
            '<meta charset=utf-8><meta http-equiv=refresh content="7;url=z">', (7.0, "z"), id="skips-other-meta"
        ),
        pytest.param('<meta http-equiv=other content="8;url=z">', None, id="non-refresh-equiv"),
        pytest.param('<meta http-equiv=refresx content="9;url=z">', None, id="seven-char-non-refresh-equiv"),
        pytest.param('<meta content="5;url=z">', None, id="meta-without-http-equiv"),
        pytest.param("<meta http-equiv=refresh>", None, id="refresh-without-content"),
        pytest.param('<meta http-equiv=refresh content="abc">', None, id="no-leading-number"),
        pytest.param('<meta http-equiv=refresh content="   ">', None, id="all-whitespace-content"),
        pytest.param('<meta http-equiv="   " content="5;url=x">', None, id="all-whitespace-equiv"),
        pytest.param("<p>nothing</p>", None, id="no-meta"),
        pytest.param(
            '<noscript><meta http-equiv=refresh content="5;url=x"></noscript>', None, id="ignored-in-noscript"
        ),
    ],
)
def test_meta_refresh_selection(html: str, expected: tuple[float, str] | None) -> None:
    assert parse(html).meta_refresh() == expected
