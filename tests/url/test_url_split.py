"""Conformance tests for the C ``_url_split`` primitive behind :mod:`turbohtml._urls`.

The split follows the WHATWG basic URL parser's component boundaries, which coincide with ``urllib.parse.urlsplit``
for every reasonable URL, so the bulk of the coverage is a differential against urllib over a generated corpus. The one
deliberate divergence -- urllib rejects a bracketed host whose content is not a valid IPv4/IPv6 address, while this step
defers host-content validation to a later stage -- is asserted on its own, as are the host-kind tags and the degenerate
authorities that exercise the parser's remaining branches.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest

from turbohtml._html import _url_split

_REGNAME, _IPV4, _IPV6 = 0, 1, 2

_SCHEMES = ("http", "https", "ftp", "HTTP", "mailto", "", "javascript", "ws", "wss", "file")
_AUTHORITIES = (
    "",
    "example.com",
    "Example.COM",
    "user@host",
    "user:pass@host",
    "host:8080",
    "host:",
    "host:080",
    "host:abc",
    "1.2.3.4",
    "1.2.3.4:9",
    "[::1]",
    "[::1]:443",
    "[::1]:",
    "[2001:db8::1]:8080",
    "user@[::1]:80",
    "münchen.de",
    "sub.example.co.uk",
    "a",
    "a:b:c",
    "@host",
    "user@@host",
)
_PATHS = ("", "/", "/a/b", "/a/../b")
_QUERIES = ("", "?a=1", "?a=1&b=2")
_FRAGMENTS = ("", "#f")


def _corpus() -> list[str]:
    urls = [
        f"{scheme}:" * bool(scheme) + (f"//{auth}" * bool(auth or path.startswith("//"))) + path + query + fragment
        for scheme in _SCHEMES
        for auth in _AUTHORITIES
        for path in _PATHS
        for query in _QUERIES
        for fragment in _FRAGMENTS
    ]
    urls += ["  http://x.com/y  ", "http://h\tost/p", "\x01\x02http://a.com", "HTTP://X", "//netonly/p", "a\nb\rc://x"]
    urls += [f"http://h.com/x{query}{fragment}" for query in ("", "?", "?a=1") for fragment in ("", "#", "#a=1")]
    return sorted(set(urls))


def test_url_split_matches_urllib_over_corpus() -> None:
    for url in _corpus():
        reference = urlsplit(url)
        scheme, netloc, path, query, fragment, _userinfo, host, *_rest = _url_split(url)
        assert (scheme, netloc, path, query, fragment) == (
            reference.scheme,
            reference.netloc,
            reference.path,
            reference.query,
            reference.fragment,
        ), url
        assert host.lower() == (reference.hostname or ""), url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param(
            "http://user:pass@Example.COM:8080/p?a=1#f",
            (
                "http",
                "user:pass@Example.COM:8080",
                "/p",
                "a=1",
                "f",
                "user:pass",
                "Example.COM",
                "8080",
                True,
                _REGNAME,
            ),
            id="full-regname",
        ),
        pytest.param(
            "https://[2001:DB8::1]:443/x",
            ("https", "[2001:DB8::1]:443", "/x", "", "", "", "2001:DB8::1", "443", True, _IPV6),
            id="ipv6-with-port",
        ),
        pytest.param(
            "http://1.2.3.4/x",
            ("http", "1.2.3.4", "/x", "", "", "", "1.2.3.4", "", False, _IPV4),
            id="ipv4-no-port",
        ),
        pytest.param(
            "mailto:a@b.com",
            ("mailto", "", "a@b.com", "", "", "", "", "", False, _REGNAME),
            id="opaque-no-authority",
        ),
        pytest.param(
            "",
            ("", "", "", "", "", "", "", "", False, _REGNAME),
            id="empty",
        ),
    ],
)
def test_url_split_reports_components(url: str, expected: tuple[object, ...]) -> None:
    assert _url_split(url) == expected


@pytest.mark.parametrize(
    ("url", "kind"),
    [
        pytest.param("http://example.com/", _REGNAME, id="regname"),
        pytest.param("http://münchen.de/", _REGNAME, id="unicode-regname"),
        pytest.param("http://1.2.3.4/", _IPV4, id="ipv4"),
        pytest.param("http://[::1]/", _IPV6, id="ipv6"),
    ],
)
def test_url_split_classifies_host(url: str, kind: int) -> None:
    assert _url_split(url)[9] == kind


@pytest.mark.parametrize(
    "url",
    [
        pytest.param("http://[::1/x", id="open-only"),
        pytest.param("http://a]b/x", id="close-only"),
    ],
)
def test_url_split_rejects_unbalanced_brackets(url: str) -> None:
    with pytest.raises(ValueError, match="Invalid IPv6 URL"):
        _url_split(url)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param("\x00\x1f http://x/p", ("http", "x", "/p"), id="strip-leading-control-and-space"),
        pytest.param("http://münchen.de/p", ("http", "münchen.de", "/p"), id="two-byte-input"),
        pytest.param("http://x/\U0001f600", ("http", "x", "/\U0001f600"), id="four-byte-input"),
        pytest.param("ht\ttp://x/p", ("http", "x", "/p"), id="drop-tab"),
        pytest.param("ht\ntp://x/p", ("http", "x", "/p"), id="drop-newline"),
        pytest.param("ht\rtp://x/p", ("http", "x", "/p"), id="drop-carriage-return"),
    ],
)
def test_url_split_preprocesses_input(url: str, expected: tuple[str, str, str]) -> None:
    assert _url_split(url)[:3] == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param("ht!p://x", ("", "", "ht!p://x"), id="non-scheme-char-before-colon-is-no-scheme"),
        pytest.param("1http://x", ("", "", "1http://x"), id="digit-lead-is-no-scheme"),
        pytest.param("~http://x", ("", "", "~http://x"), id="above-z-lead-is-no-scheme"),
        pytest.param("_http://x", ("", "", "_http://x"), id="between-Z-and-a-lead-is-no-scheme"),
        pytest.param("abc", ("", "", "abc"), id="bare-scheme-chars-without-colon-is-path"),
        pytest.param("a+b-1.c://x", ("a+b-1.c", "x", ""), id="scheme-uses-plus-minus-dot-digit"),
        pytest.param("http://", ("http", "", ""), id="empty-authority"),
        pytest.param("http://:80/x", ("http", ":80", "/x"), id="empty-host-with-port"),
        pytest.param("http://[::1]x/p", ("http", "[::1]x", "/p"), id="ipv6-trailing-non-colon"),
        pytest.param("http://]@[foo/p", ("http", "]@[foo", "/p"), id="bracket-open-without-close-in-hostinfo"),
    ],
)
def test_url_split_edge_authorities(url: str, expected: tuple[str, str, str]) -> None:
    assert _url_split(url)[:3] == expected


def test_url_split_empty_host_with_port_has_no_host() -> None:
    _scheme, _netloc, _path, _query, _fragment, _userinfo, host, port, has_port, kind = _url_split("http://:80/x")
    assert (host, port, has_port, kind) == ("", "80", True, _REGNAME)


def test_url_split_ipv6_open_without_close_keeps_tail_as_host() -> None:
    assert _url_split("http://]@[foo/p")[6] == "foo"
