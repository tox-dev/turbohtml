"""normalize_url: the WHATWG-normalization core plus the query/fragment canonicalization layered on it."""

from __future__ import annotations

import pytest

from turbohtml.extract import UrlCleaning, clean_url, normalize_url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param("HTTPS://WWW.DWDS.DE/", "https://www.dwds.de/", id="scheme-and-host-lowercased"),
        pytest.param("http://www.example.org:80/test.html", "http://www.example.org/test.html", id="http-default-port"),
        pytest.param("https://example.org:443/x", "https://example.org/x", id="https-default-port"),
        pytest.param("http://example.org:8080/x", "http://example.org:8080/x", id="other-port-kept"),
        pytest.param("http://example.org:0080/x", "http://example.org/x", id="port-leading-zeros"),
        pytest.param("http://example.org:/x", "http://example.org/x", id="empty-port-dropped"),
        pytest.param("http://example.org:80x/x", "http://example.org:80x/x", id="garbage-port-kept"),
        pytest.param("http://[::1]:80/", "http://[::1]/", id="ipv6-default-port"),
        pytest.param("http://[::1]:8080/", "http://[::1]:8080/", id="ipv6-other-port"),
        pytest.param("http://[::1]:/", "http://[::1]/", id="ipv6-empty-port"),
        pytest.param("http://user:pw@Example.ORG/x", "http://user:pw@example.org/x", id="userinfo-kept-verbatim"),
        pytest.param("http://münchen.de", "http://xn--mnchen-3ya.de/", id="unicode-host-to-punycode"),
        pytest.param("http://münchen..de/x", "http://münchen..de/x", id="unencodable-host-kept-lowercase"),
        pytest.param(
            "http://www.example.org:80?p=123", "http://www.example.org/?p=123", id="empty-path-serialized-as-root"
        ),
        pytest.param("http://x.org/a/b/../c", "http://x.org/a/c", id="double-dot-segment"),
        pytest.param("http://x.org/./a", "http://x.org/a", id="single-dot-segment"),
        pytest.param("http://x.org/a/%2e%2e/c", "http://x.org/c", id="percent-encoded-dot-segment"),
        pytest.param("http://x.org/a/c/..", "http://x.org/a/", id="trailing-double-dot-keeps-slash"),
        pytest.param("http://x.org/a/.", "http://x.org/a/", id="trailing-single-dot-keeps-slash"),
        pytest.param("http://x.org/../a", "http://x.org/a", id="leading-double-dot-stays-at-root"),
        pytest.param("http://x.org//double//slash", "http://x.org//double//slash", id="repeated-slashes-kept"),
        pytest.param("http://x.org/r%c3%a9sum%c3%a9", "http://x.org/r%C3%A9sum%C3%A9", id="escape-hex-uppercased"),
        pytest.param("http://x.org/a b", "http://x.org/a%20b", id="space-percent-encoded"),
        pytest.param("http://x.org/ab'c!$&,;=", "http://x.org/ab'c!$&,;=", id="path-safe-punctuation-kept"),
        pytest.param("http://x.org/a{b}", "http://x.org/a%7Bb%7D", id="curly-braces-encoded-in-path"),
        pytest.param("http://x.org/x?q=a b#f g", "http://x.org/x?q=a%20b#f%20g", id="query-and-fragment-encoded"),
        pytest.param("http://x.org/x#a{b}", "http://x.org/x#a{b}", id="fragment-set-looser-than-path"),
        pytest.param("mailto:someone@example.org", "mailto:someone@example.org", id="opaque-scheme-untouched"),
        pytest.param("page.html?b=2&a=1", "page.html?a=1&b=2", id="relative-url-keeps-shape"),
    ],
)
def test_normalize_spec_behaviors(url: str, expected: str) -> None:
    assert normalize_url(url) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param("http://test.org/?utm_source=rss&utm_medium=rss", "http://test.org/", id="utm-family"),
        pytest.param("http://test.org/?s_cid=123&clickid=1", "http://test.org/", id="cid-and-clickid"),
        pytest.param("http://test.org/?aftr_source=0", "http://test.org/", id="generic-source-suffix"),
        pytest.param("http://test.org/?fb_ref=0", "http://test.org/", id="fb-ref"),
        pytest.param("http://test.org/?this_affiliate=0", "http://test.org/", id="generic-affiliate"),
        pytest.param("http://test.org/?gclid=1&page=2", "http://test.org/?page=2", id="clid-suffix-family"),
        pytest.param("http://test.org/?%75tm_source=1", "http://test.org/", id="percent-encoded-tracker-name"),
        pytest.param("http://test.net/foo?testid=1", "http://test.net/foo?testid=1", id="id-inside-word-kept"),
        pytest.param("http://test.org/?refresh=1", "http://test.org/?refresh=1", id="ref-inside-word-kept"),
        pytest.param(
            "http://test.net/foo.html?testid=1&post=abc&page=2",
            "http://test.net/foo.html?page=2&post=abc&testid=1",
            id="query-sorted-by-key",
        ),
        pytest.param("http://test.org/?a=1&&b=2", "http://test.org/?a=1&b=2", id="empty-pair-dropped"),
    ],
)
def test_normalize_query_cleaning(url: str, expected: str) -> None:
    assert normalize_url(url) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param("http://test.org/#page2", "http://test.org/#page2", id="content-fragment-kept"),
        pytest.param("http://test.org/#partnerid=123", "http://test.org/", id="tracker-fragment-dropped"),
        pytest.param(
            "http://test.org/#mtm_campaign=doc&mtm_keyword=demo&catpage=3",
            "http://test.org/#catpage=3",
            id="query-shaped-fragment-scrubbed",
        ),
        pytest.param("http://test.org/#delta=4", "http://test.org/#delta=4", id="non-tracker-pair-kept"),
        pytest.param(
            "http://test.net/foo.html#:~:text=night-,vision",
            "http://test.net/foo.html#:~:text=night-,vision",
            id="text-fragment-kept",
        ),
    ],
)
def test_normalize_fragment_cleaning(url: str, expected: str) -> None:
    assert normalize_url(url) == expected


@pytest.mark.parametrize(
    ("url", "options", "expected"),
    [
        pytest.param(
            "http://test.net/foo.html?testid=1&post=abc&page=2#bar",
            UrlCleaning(strict=True),
            "http://test.net/foo.html?page=2&post=abc",
            id="strict-allowlist-and-no-fragment",
        ),
        pytest.param(
            "http://test.net/foo?page=2&lang=en",
            UrlCleaning(strict=True),
            "http://test.net/foo?lang=en&page=2",
            id="strict-keeps-language-params",
        ),
        pytest.param("http://x.org/dir/", UrlCleaning(trailing_slash=False), "http://x.org/dir", id="slash-trimmed"),
        pytest.param(
            "http://x.org/", UrlCleaning(trailing_slash=False), "http://x.org/", id="root-slash-never-trimmed"
        ),
        pytest.param(
            "http://x.org/dir/?p=1", UrlCleaning(trailing_slash=False), "http://x.org/dir/?p=1", id="query-keeps-slash"
        ),
        pytest.param("http://x.org/p#frag", UrlCleaning(strip_fragment=True), "http://x.org/p", id="fragment-stripped"),
        pytest.param(
            "http://x.org/de/page?lang=fr",
            UrlCleaning(language="en"),
            "http://x.org/de/page?lang=fr",
            id="language-never-rejects-in-normalize",
        ),
    ],
)
def test_normalize_options(url: str, options: UrlCleaning, expected: str) -> None:
    assert normalize_url(url, options) == expected


def test_normalize_rejects_unsplittable_input() -> None:
    with pytest.raises(ValueError, match="Invalid IPv6 URL"):
        normalize_url("http://[::1/x")


def test_normalize_rejects_lone_surrogate() -> None:
    # a lone surrogate has no UTF-8 form, so the component cannot be percent-encoded and the URL is not serializable
    with pytest.raises(ValueError, match="cannot be percent-encoded"):
        normalize_url("http://a.com/\udce9")


def test_normalize_rejects_non_str() -> None:
    with pytest.raises(TypeError, match="url must be a str"):
        normalize_url(123)  # ty: ignore[invalid-argument-type]  # a non-str exercises the TypeError guard


@pytest.mark.parametrize(
    "options",
    [
        pytest.param(UrlCleaning(), id="default"),
        pytest.param(UrlCleaning(strict=True), id="strict"),
        pytest.param(UrlCleaning(trailing_slash=False), id="no-trailing-slash"),
        pytest.param(UrlCleaning(strip_fragment=True), id="no-fragment"),
    ],
)
@pytest.mark.parametrize(
    "url",
    [
        pytest.param("https://example.org:443/file.html?p=100&abc=1#frag", id="kitchen-sink"),
        pytest.param("http://test.org/?utm_source=x", id="tracker-only-query"),
        pytest.param("http://münchen.de/a/../b//c/?z=1&a=2#frag=1&utm_x=2", id="unicode-host-dots-and-fragment"),
    ],
)
def test_normalize_is_idempotent(url: str, options: UrlCleaning) -> None:
    once = normalize_url(url, options)
    assert normalize_url(once, options) == once


def test_clean_is_idempotent_where_normalize_flattens_the_root() -> None:
    cleaned = clean_url("http://test.org/?utm_source=x&utm_medium=y")
    assert cleaned == "http://test.org/"
    assert clean_url(cleaned) == cleaned
