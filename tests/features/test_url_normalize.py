from __future__ import annotations

from urllib.parse import urlsplit

import pytest

from turbohtml.extract import UrlCleaning, normalize_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param("https://EXAMPLE.com/p", "https://example.com/p", id="lowercase-host"),
        pytest.param("https://example.com:443/p", "https://example.com/p", id="strip-default-https-port"),
        pytest.param("http://example.com:80/p", "http://example.com/p", id="strip-default-http-port"),
        pytest.param("https://example.com:8443/p", "https://example.com:8443/p", id="keep-nonstandard-port"),
        pytest.param("http://example.com/p?b=2&a=1", "http://example.com/p?a=1&b=2", id="sort-query"),
        pytest.param("http://example.com/a//b///c", "http://example.com/a/b/c", id="collapse-slashes"),
        pytest.param("http://example.com/../a", "http://example.com/a", id="strip-leading-parent"),
        pytest.param("http://example.com?a=1", "http://example.com/?a=1", id="query-forces-root-path"),
        pytest.param("http://example.com/p?utm_source=ad&id=9", "http://example.com/p?id=9", id="drop-tracker"),
        pytest.param("http://example.com/p#frag", "http://example.com/p#frag", id="keep-fragment"),
        pytest.param("http://example.com/p#utm_source=ad", "http://example.com/p", id="drop-tracker-fragment"),
        pytest.param("http://example.com/p#a=1&b=2", "http://example.com/p#a=1%26b=2", id="encode-fragment"),
    ],
)
def test_normalize_url_default(raw: str, expected: str) -> None:
    assert normalize_url(raw) == expected


def test_normalize_url_accepts_a_pre_split_result() -> None:
    assert normalize_url(urlsplit("https://EXAMPLE.com:443/p")) == "https://example.com/p"


def test_non_web_scheme_keeps_its_port_but_lowercases_host() -> None:
    assert normalize_url("ftp://EXAMPLE.com:80/file") == "ftp://example.com:80/file"


def test_keep_tracker_when_stripping_is_off() -> None:
    options = UrlCleaning(strip_trackers=False)
    assert normalize_url("http://example.com/p?utm_source=ad&id=9", options) == "http://example.com/p?id=9&utm_source=ad"


def test_trailing_slash_trimmed_only_when_no_query() -> None:
    options = UrlCleaning(trailing_slash=False)
    assert normalize_url("http://example.com/dir/", options) == "http://example.com/dir"
    assert normalize_url("http://example.com/dir/?a=1", options) == "http://example.com/dir/?a=1"


def test_strict_keeps_only_the_allowlist_and_drops_the_fragment() -> None:
    raw = "http://example.com/p?id=3&color=red&page=2#frag"
    assert normalize_url(raw, UrlCleaning.aggressive()) == "http://example.com/p?id=3&page=2"


def test_strip_fragment_without_strict() -> None:
    assert normalize_url("http://example.com/p?a=1#frag", UrlCleaning(strip_fragment=True)) == "http://example.com/p?a=1"
