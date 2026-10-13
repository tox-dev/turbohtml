from __future__ import annotations

import itertools
import random
import re
from functools import partial
from typing import TYPE_CHECKING, Final

import pytest

from turbohtml import parse, parse_fragment
from turbohtml._html import (
    _url_is_tracker,
    _url_join,
    _url_language_matches,
    _url_normalize_query,
    _url_percent_decode,
    _url_percent_encode,
    _url_remove_dot_segments,
)

if TYPE_CHECKING:
    from collections.abc import Callable
from urllib.parse import quote, unquote, urlsplit

from turbohtml._html import _url_scrub, _url_split, _url_variant_key


@pytest.mark.parametrize(
    ("html", "fallback", "expected"),
    [
        pytest.param('<base href="/sub/page">', "http://site.com/dir/", "http://site.com/sub/page", id="relative-base"),
        pytest.param('<base href="http://abs.com/x">', "http://site.com/", "http://abs.com/x", id="absolute-base"),
        pytest.param('<base href="data:text/plain,x">', "http://site.com/", "http://site.com/", id="data-base"),
        pytest.param('<base href="JaVaScRiPt:alert(1)">', "http://site.com/", "http://site.com/", id="javascript-base"),
        pytest.param(
            '<base href="data:text/plain,x"><base href="https://other.com/">',
            "http://site.com/",
            "http://site.com/",
            id="first-base-disallowed",
        ),
        pytest.param('<base href="ftp://other.com/x">', "http://site.com/", "ftp://other.com/x", id="other-scheme"),
        pytest.param('<base href="dog:x">', "http://site.com/", "dog:x", id="data-prefix-is-not-data"),
        pytest.param('<base href="just:foo">', "http://site.com/", "just:foo", id="short-javascript-prefix"),
        pytest.param("<p>no base</p>", "http://site.com/", "http://site.com/", id="no-base"),
        pytest.param("<base>", "http://site.com/", "http://site.com/", id="valueless-href"),
        pytest.param('<base href="">', "http://site.com/", "http://site.com/", id="empty-href"),
        pytest.param('<base href="  ">', "http://site.com/", "http://site.com/", id="blank-href"),
        pytest.param(
            '<base href="">', "https://site.com/p?x#old", "https://site.com/p?x", id="empty-href-drops-fragment"
        ),
        pytest.param('<base href="">', "https://site.com/p?x", "https://site.com/p?x", id="empty-href-no-fragment"),
        pytest.param('<base href="">', "http://site.com/p#old", "http://site.com/p", id="empty-href-http"),
        pytest.param('<base href="">', "https://[::1]/p#old", "https://[::1]/p", id="empty-href-ipv6"),
        pytest.param('<base href="">', "https://site.com/p\t#old", "https://site.com/p", id="empty-href-tab"),
        pytest.param('<base href="">', "https://site.com/é#old", "https://site.com/%C3%A9", id="empty-href-unicode"),
        pytest.param('<base href="">', "https://site.com/p\\q#old", "https://site.com/p/q", id="empty-href-backslash"),
        pytest.param('<base href="">', "ftp://site.com/p#old", "ftp://site.com/p", id="empty-href-ftp"),
        pytest.param('<base href="">', "a#old", "a#old", id="empty-href-short-opaque"),
        pytest.param('<base href="  ">', "git:/p#old", "git:/p", id="blank-href-drops-hierarchical-fragment"),
        pytest.param('<base href="">', "git:opaque#old", "git:opaque#old", id="empty-href-opaque-fallback"),
        pytest.param('<base href="">', "git:#old", "git:#old", id="empty-href-empty-opaque-path"),
        pytest.param("<p>no base</p>", "https://site.com/p#old", "https://site.com/p#old", id="no-base-keeps-fragment"),
        pytest.param('<base href=" b:_">', "https://example.org/dir/page", "b:_", id="opaque-leading-space"),
        pytest.param('<base href="b\n:_">', "https://example.org/dir/page", "b:_", id="opaque-internal-line-feed"),
        pytest.param('<base href="b&#13;:_">', "https://example.org/dir/page", "b:_", id="opaque-internal-return"),
        pytest.param('<base href="a:!\tx">', "https://example.org/dir/page", "a:!x", id="opaque-internal-tab"),
        pytest.param('<base href="&#31;b:_">', "https://example.org/dir/page", "b:_", id="opaque-leading-c0"),
    ],
)
def test_base_url(html: str, fallback: str, expected: str) -> None:
    assert parse(html).base_url(fallback) == expected


@pytest.mark.parametrize("fallback", ["https://[bad", "https://site.com]/p#old"])
def test_base_url_empty_href_rejects_malformed_fallback(fallback: str) -> None:
    with pytest.raises(ValueError, match="Invalid IPv6 URL"):
        parse('<base href="">').base_url(fallback)


def test_base_url_default_fallback() -> None:
    assert parse('<base href="page">').base_url() == "page"


@pytest.mark.parametrize(
    ("href", "fallback", "expected"),
    [
        pytest.param("http:", "http://site.org/a?x#old", "http://site.org/a?x", id="http"),
        pytest.param("HTTPS:", "https://site.org/a#old", "https://site.org/a", id="uppercase-https"),
        pytest.param("ftp:", "ftp://site.org/a#old", "ftp://site.org/a", id="ftp"),
        pytest.param("WS:", "ws://site.org/a#old", "ws://site.org/a", id="uppercase-ws"),
        pytest.param("wss:", "wss://site.org/a#old", "wss://site.org/a", id="wss"),
        pytest.param(
            "https:",
            "https://user:pass@example.org:443/a/b?x#old",
            "https://user:pass@example.org/a/b?x",
            id="credentials-default-port-query",
        ),
        pytest.param("https:", "https://é.example/a#old", "https://xn--9ca.example/a", id="unicode-host"),
        pytest.param("https:", "http://site.org/a#old", "http://site.org/a#old", id="foreign-scheme"),
        pytest.param("file:", "file:///a#old", "file:///a", id="file-scheme"),
        pytest.param("a:", "a:/p#old", "a:", id="non-special-scheme"),
    ],
)
def test_base_url_scheme_only_reference(href: str, fallback: str, expected: str) -> None:
    assert parse(f'<base href="{href}">').base_url(fallback) == expected


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        pytest.param("p", "https://example.org/dir/p", id="relative-segment"),
        pytest.param("/p0", "https://example.org/p0", id="rooted-segment"),
        pytest.param("/", "https://example.org/", id="root"),
        pytest.param("A_b-2", "https://example.org/dir/A_b-2", id="safe-path-characters"),
    ],
)
def test_base_url_resolves_simple_web_path(href: str, expected: str) -> None:
    assert parse(f'<base href="{href}">').base_url("https://example.org/dir/page") == expected


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
        pytest.param("http://h?a=b c", "http://x", "http://h/?a=b%20c", id="authority-then-query"),
        pytest.param("http://h#f g", "http://x", "http://h/#f%20g", id="authority-then-fragment"),
        pytest.param("http://h/p#f g", "http://x", "http://h/p#f%20g", id="fragment-without-query"),
        pytest.param("^", "https://example.org/dir/file", "https://example.org/dir/%5E", id="relative-path-caret"),
        pytest.param(
            "x?c=^#^", "https://example.org/dir/file", "https://example.org/dir/x?c=^#^", id="query-fragment-caret"
        ),
        pytest.param("hello?x", "https://site.org/a", "https://site.org/hello?x", id="relative-h-prefix-query"),
        pytest.param("http://h", "http://x", "http://h/", id="authority-only"),
        pytest.param("ab/cd", "", "ab/cd", id="relative-without-scheme"),
        pytest.param("a+bc", "", "a+bc", id="scheme-tail-without-colon"),
        pytest.param("a'bc", "", "a'bc", id="quote-in-relative-path"),
        pytest.param("+", "", "+", id="one-character-relative-path"),
        pytest.param("/x", "", "/x", id="protocol-relative-root"),
        pytest.param("http://x/a%20b?c=%7e", "http://x", "http://x/a%20b?c=%7e", id="existing-escapes-untouched"),
        pytest.param("a: x", "https://example.org/dir/page", "a: x", id="opaque-path-space"),
        pytest.param("a:x ?y", "https://example.org/dir/page", "a:x%20?y", id="opaque-path-space-before-query"),
        pytest.param("a:x  ?y", "https://example.org/dir/page", "a:x %20?y", id="opaque-path-two-spaces-before-query"),
        pytest.param("a:x?<", "https://example.org/dir/page", "a:x?%3C", id="opaque-path-nonspace-before-query"),
        pytest.param("a:?<", "https://example.org/dir/page", "a:?%3C", id="empty-opaque-path-before-query"),
        pytest.param("a:x #f", "https://example.org/dir/page", "a:x%20#f", id="opaque-path-space-before-fragment"),
        pytest.param("a: [x]", "https://example.org/dir/page", "a: [x]", id="opaque-path-brackets"),
        pytest.param("a:/ x", "https://example.org/dir/page", "a:/%20x", id="hierarchical-path-space"),
        pytest.param(
            "\u2028", "https://example.org/dir/page", "https://example.org/dir/%E2%80%A8", id="line-separator"
        ),
        pytest.param(
            "a/\u2028",
            "https://example.org/dir/page",
            "https://example.org/dir/a/%E2%80%A8",
            id="trailing-line-separator",
        ),
        pytest.param(
            "\u00a0", "https://example.org/dir/page", "https://example.org/dir/%C2%A0", id="nonbreaking-space"
        ),
        pytest.param(
            " \u2003a ", "https://example.org/dir/page", "https://example.org/dir/%E2%80%83a", id="edge-em-space"
        ),
        pytest.param("\\x", "https://example.org/dir/page", "https://example.org/x", id="special-root-backslash"),
        pytest.param(
            "\\x?q=\\", "https://example.org/dir/page", "https://example.org/x?q=\\", id="root-query-backslash"
        ),
        pytest.param("\\?x", "https://example.org/dir/page", "https://example.org/?x", id="root-backslash-query"),
        pytest.param(
            "\\x#\\", "https://example.org/dir/page", "https://example.org/x#\\", id="root-fragment-backslash"
        ),
        pytest.param(
            "\\x?q#frag", "https://example.org/dir/page", "https://example.org/x?q#frag", id="root-query-fragment"
        ),
        pytest.param(
            "\\x#frag?q", "https://example.org/dir/page", "https://example.org/x#frag?q", id="root-fragment-question"
        ),
        pytest.param("/\\host/x", "https://example.org/dir/page", "https://host/x", id="mixed-authority-backslash"),
        pytest.param("/", "https://example.org/dir/page", "https://example.org/", id="single-root-slash"),
        pytest.param("\\é", "https://example.org/dir/page", "https://example.org/%C3%A9", id="unicode-root-backslash"),
        pytest.param(
            "/\\host/é", "https://example.org/dir/page", "https://host/%C3%A9", id="unicode-authority-backslash"
        ),
        pytest.param("/é", "https://example.org/dir/page", "https://example.org/%C3%A9", id="unicode-root-slash"),
        pytest.param(
            "a\\b", "https://example.org/dir/page", "https://example.org/dir/a/b", id="special-path-backslash"
        ),
        pytest.param(
            "a\\./b", "https://example.org/dir/page", "https://example.org/dir/a/b", id="backslash-dot-segment"
        ),
        pytest.param(
            "a\\%2e/b", "https://example.org/dir/page", "https://example.org/dir/a/b", id="backslash-encoded-dot"
        ),
        pytest.param("\\\\host\\a", "https://example.org/dir/page", "https://host/a", id="special-authority-backslash"),
        pytest.param(
            "a\\b?q=\\",
            "https://example.org/dir/page",
            "https://example.org/dir/a/b?q=\\",
            id="special-query-backslash-unchanged",
        ),
        pytest.param(
            "?q=\\", "https://example.org/dir/page", "https://example.org/dir/page?q=\\", id="query-backslash-unchanged"
        ),
        pytest.param(
            "#\\", "https://example.org/dir/page", "https://example.org/dir/page#\\", id="fragment-backslash-unchanged"
        ),
        pytest.param("a:\\x", "https://example.org/dir/page", "a:\\x", id="non-special-absolute-backslash"),
        pytest.param("\\x", "a:/dir/page", "a:/dir/\\x", id="non-special-root-backslash"),
        pytest.param("\\x", "a", "\\x", id="schemeless-base-backslash"),
        pytest.param("\\x", "h!tp", "\\x", id="invalid-scheme-base-backslash"),
        pytest.param("\\x", "hxxp:opaque", "hxxp:opaque", id="other-scheme-base-backslash"),
        pytest.param("\\x", "", "\\x", id="empty-base-root-backslash"),
        pytest.param("\\x", "é:/dir/page", "%C3%A9:/dir/\\x", id="unicode-initial-base-backslash"),
        pytest.param("\\x", "hétp:/dir/page", "h%C3%A9tp:/dir/\\x", id="unicode-scheme-base-backslash"),
        pytest.param(
            "rel", "http://x/\U0001f389\udce9/", "http://x/%F0%9F%8E%89%EF%BF%BD/rel", id="high-char-and-surrogate"
        ),
    ],
)
def test_base_url_percent_encodes(href: str, fallback: str, expected: str) -> None:
    # the resolved base URL applies the WHATWG path/query/fragment percent-encode sets, so it is a valid URL string
    assert parse(f'<base href="{href}">').base_url(fallback) == expected


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        pytest.param("http://ExAmPlE.Org", "http://example.org/", id="host-case-and-empty-path"),
        pytest.param("http://example.org:80/p", "http://example.org/p", id="default-port"),
        pytest.param("http://é@example.com/", "http://%C3%A9@example.com/", id="unicode-userinfo"),
        pytest.param("http://münchen.de/", "http://xn--mnchen-3ya.de/", id="unicode-host"),
        pytest.param("http://192.168.257/", "http://192.168.1.1/", id="numeric-host"),
        pytest.param("ftp://example.org/x", "ftp://example.org/x", id="ftp-host"),
        pytest.param("ws://example.org/x", "ws://example.org/x", id="ws-host"),
        pytest.param("wss://example.org/x", "wss://example.org/x", id="wss-host"),
        pytest.param("wss:/bb", "wss://bb/", id="wss-single-slash-host"),
        pytest.param("http:/b", "http://b/", id="http-single-slash-host"),
        pytest.param("http:///host", "http://host/", id="extra-slash-before-host"),
        pytest.param("http://\\host", "http://host/", id="backslash-before-host"),
        pytest.param("http://abc.def/", "http://abc.def/", id="hex-letter-host-ending"),
        pytest.param("http://foo.0x/", "https://base.test/dir/page", id="incomplete-hex-host"),
        pytest.param("http://0x/p", "http://0.0.0.0/p", id="hex-zero-host"),
        pytest.param("http://foo.ax/p", "http://foo.ax/p", id="nonhex-x-suffix"),
        pytest.param("http://a0x/p", "http://a0x/p", id="x-after-zero-in-label"),
        pytest.param("http://foo.0xa/", "https://base.test/dir/page", id="invalid-hex-host"),
        pytest.param("http://foo.0xg/", "http://foo.0xg/", id="nonhex-host"),
        pytest.param("http://foo.-/", "http://foo.-/", id="hyphen-host-ending"),
        pytest.param("http://./", "http://./", id="single-dot-host"),
        pytest.param("http://example.org./x", "http://example.org./x", id="trailing-dot-host"),
        pytest.param("http://example.org?x", "http://example.org/?x", id="host-query-without-path"),
        pytest.param("http://foo-bar.org/", "http://foo-bar.org/", id="hyphenated-host"),
        pytest.param("http://a{b.com/", "http://a{b.com/", id="braced-host"),
        pytest.param("http://foo.a/", "http://foo.a/", id="one-letter-hex-host"),
        pytest.param("http://[::127.0.0.1]", "http://[::7f00:1]/", id="ipv6-host"),
        pytest.param("https://@@@example/", "https://%40%40@example/", id="repeated-userinfo-separators"),
        pytest.param("sc://ñ.test/p", "sc://%C3%B1.test/p", id="opaque-unicode-host"),
        pytest.param("notspecial://host/?'", "notspecial://host/?'", id="non-special-query-quote"),
        pytest.param("http://a:b@c\\", "http://a:b@c/", id="special-backslash-after-authority"),
        pytest.param("http://example.org\\\\a", "http://example.org//a", id="special-backslash-path"),
        pytest.param("non-spec:/.//path", "non-spec:/.//path", id="protective-path-marker"),
        pytest.param("non-spec:/..//path", "non-spec:/.//path", id="protective-path-after-dot-removal"),
        pytest.param("http:other/path", "http://other/path", id="foreign-absolute-special-scheme"),
        pytest.param(
            "https://example.org/p?b=2&a=1#", "https://example.org/p?b=2&a=1#", id="query-order-empty-fragment"
        ),
    ],
)
def test_base_url_canonical_authority(href: str, expected: str) -> None:
    assert parse(f'<base href="{href}">').base_url("https://base.test/dir/page") == expected


def test_base_url_uses_fallback_for_special_url_without_host() -> None:
    assert parse('<base href="http: x">').base_url("https://example.org/dir/page") == "https://example.org/dir/page"


@pytest.mark.parametrize(
    ("fallback", "href"),
    [
        pytest.param("https://site.org/a?x#old", "//#new", id="empty-authority-fragment"),
        pytest.param("https://site.org/a?x#old", "///?q", id="empty-authority-query"),
        pytest.param("https://site.org/a?x#old", "\\\\?q", id="backslash-authority-query"),
        pytest.param("https://site.org/a?x#old", "/\t/#new", id="stripped-tab-authority"),
        pytest.param("https://site.org/a?x#old", "http:?q", id="foreign-missing-host"),
        pytest.param("https://site.org/a?x#old", "http:/", id="foreign-missing-host-after-slash"),
        pytest.param("https://site.org/a?x#old", "ftp:\\", id="foreign-missing-host-after-backslash"),
        pytest.param("http://[::1]:8080/a/b", r"http:\\#?x#f", id="same-scheme-empty-backslash-authority"),
        pytest.param("http://[::1]:8080/a/b", r"http:\\\?x#f", id="same-scheme-extra-backslash-authority"),
        pytest.param("https://site.org/a?x#old", "http://[x]/", id="invalid-ipv6"),
        pytest.param("https://site.org/a?x#old", "http://?q", id="empty-host-query"),
        pytest.param("https://site.org/a?x#old", "http:////", id="extra-slashes-without-host"),
        pytest.param("https://site.org/a?x#old", "http:////?q", id="extra-slashes-before-query"),
        pytest.param("https://site.org/a?x#old", "http://#f", id="empty-host-fragment"),
        pytest.param("https://site.org/a?x#old", "ws://", id="empty-ws-host"),
        pytest.param("https://site.org/a?x#old", "wss://", id="empty-wss-host"),
        pytest.param("https://site.org/a?x#old", "wss://?q", id="empty-wss-host-query"),
        pytest.param("file:///C:/a", "//[x]/", id="invalid-file-host"),
        pytest.param("git://site.org/a", "//[x]/", id="invalid-opaque-ipv6-host"),
        pytest.param("git://site.org/a", "//:x", id="missing-opaque-host-with-port"),
        pytest.param("https://site.org/a?x#old", "//:x", id="missing-special-host-with-port"),
        pytest.param("https://site.org/a", "http://other.org:80x/x", id="non-digit-port"),
        pytest.param("https://site.org/a", "http://other.org:65536/x", id="out-of-range-port"),
        pytest.param("https://site.org/a", "http://other.org:८०/x", id="non-ascii-port"),
        pytest.param("git://site.org/a", "git://other.org:80x/x", id="non-special-port"),
    ],
)
def test_base_url_uses_fallback_for_invalid_special_authority(fallback: str, href: str) -> None:
    assert parse(f'<base href="{href}">').base_url(fallback) == fallback


@pytest.mark.parametrize(
    "href",
    [
        pytest.param("http://[::1]x/", id="special-host-tail"),
        pytest.param("git://[::1]\\..", id="non-special-backslash-tail"),
        pytest.param("git://[::1]]/", id="extra-closing-bracket"),
        pytest.param("git://[::1] /", id="space-after-bracket"),
    ],
)
def test_base_url_uses_fallback_for_ipv6_host_tail(href: str) -> None:
    assert parse(f'<base href="{href}">').base_url("https://site.org/a") == "https://site.org/a"


def test_meta_refresh_canonical_authority() -> None:
    assert parse('<meta http-equiv=refresh content="0;url=http://ExAmPlE.Org:80">').meta_refresh() == (
        0.0,
        "http://example.org/",
    )


@pytest.mark.parametrize(
    ("fallback", "expected"),
    [
        pytest.param("about:blank", "about:blank#next", id="about"),
        pytest.param("mailto:user@example.org#old", "mailto:user@example.org#next", id="replaces-old-fragment"),
        pytest.param("custom:opaque?x=1", "custom:opaque?x=1#next", id="preserves-query"),
        pytest.param("a:", "a:#next", id="empty-opaque-path"),
        pytest.param("", "#next", id="empty-fallback"),
        pytest.param("1", "1#next", id="fallback-without-scheme-start"),
        pytest.param("abc", "abc#next", id="fallback-without-colon"),
        pytest.param("a!b", "a!b#next", id="fallback-with-invalid-scheme-character"),
        pytest.param("1abc:opaque", "1abc:opaque#next", id="fallback-with-digit-scheme-start"),
        pytest.param("a:/p", "a:/p#next", id="hierarchical-without-authority"),
        pytest.param("http:opaque", "http://opaque/#next", id="special-fallback"),
        pytest.param("file:opaque", "file:///opaque#next", id="file-fallback"),
    ],
)
def test_base_url_resolves_fragment_against_opaque_base(fallback: str, expected: str) -> None:
    assert parse('<base href="#next">').base_url(fallback) == expected


@pytest.mark.parametrize(
    ("fallback", "href"),
    [
        pytest.param("mailto:user@example.org", "child", id="mailto-path"),
        pytest.param("mailto:user@example.org", "child#part", id="fragment-on-relative-path"),
        pytest.param("mailto:user@example.org", "?q", id="query"),
        pytest.param("mailto:user@example.org", "", id="empty-reference"),
        pytest.param("mailto:user@example.org", "/child", id="rooted-path"),
        pytest.param("mailto:user@example.org", "//other/path", id="authority"),
        pytest.param("data:text/plain,hello", "child", id="data-path"),
        pytest.param("about:blank", "../child", id="about-parent"),
        pytest.param("custom:opaque?x#old", ".#part", id="dot-fragment"),
        pytest.param("a:", "child", id="empty-opaque-path"),
    ],
)
def test_base_url_uses_fallback_for_relative_opaque_reference(fallback: str, href: str) -> None:
    assert parse(f'<base href="{href}">').base_url(fallback) == fallback


def test_base_url_uses_absolute_url_with_opaque_fallback() -> None:
    assert parse('<base href="https://site.org/x">').base_url("mailto:user@example.org") == "https://site.org/x"


@pytest.mark.parametrize(
    ("fallback", "expected"),
    [
        pytest.param("a:é", "a:é", id="unicode-opaque-path"),
        pytest.param("a:/é", "a:/x", id="unicode-hierarchical-path"),
        pytest.param("aé:foo", "x", id="unicode-invalid-scheme"),
        pytest.param("aéfoo", "x", id="unicode-no-colon"),
        pytest.param("http:é", "http://x/", id="unicode-special-host"),
        pytest.param("file:é", "file:///x", id="unicode-file-path"),
        pytest.param("a:", "a:", id="empty-opaque-path"),
        pytest.param("a!b:foo", "x", id="ascii-invalid-scheme"),
        pytest.param("a!b:é", "x", id="invalid-scheme-character"),
    ],
)
def test_base_url_classifies_non_ascii_opaque_fallback(fallback: str, expected: str) -> None:
    assert parse('<base href="x">').base_url(fallback) == expected


@pytest.mark.parametrize("separator", ["\t", "\n", "\r"])
def test_base_url_recognizes_scheme_with_ascii_whitespace(separator: str) -> None:
    assert parse(f'<base href="a{separator}:b">').base_url("mailto:x@y") == "a:b"


@pytest.mark.parametrize(
    ("href", "fallback", "expected"),
    [
        pytest.param("file:c:\\foo\\bar.html", "file:///tmp/mock/path", "file:///c:/foo/bar.html", id="absolute-drive"),
        pytest.param("C|/foo/bar", "file:///tmp/mock/path", "file:///C:/foo/bar", id="relative-drive"),
        pytest.param("\\\\server\\file", "file:///tmp/mock/path", "file://server/file", id="unc-share"),
        pytest.param("..", "file:///C:/", "file:///C:/", id="drive-root-parent"),
        pytest.param("/", "file:///C:/a/b", "file:///C:/", id="drive-root-slash"),
        pytest.param("C|", "file://host/dir/file", "file://host/C:", id="hosted-drive"),
        pytest.param("file://localhost/test", "file:///tmp/mock/path", "file:///test", id="localhost-authority"),
        pytest.param("file:", "file:///test?test#test", "file:///test?test", id="file-empty-target"),
        pytest.param("", "file:///test?test#test", "file:///test?test", id="empty-href-drops-fragment"),
        pytest.param("file:foo", "file://host/dir/page", "file://host/dir/foo", id="relative-file-target"),
        pytest.param("foo", "file://host/dir/page", "file://host/dir/foo", id="relative-file-path"),
        pytest.param("//d:", "file:///C:/a/b", "file:///d:", id="drive-authority"),
        pytest.param("//d:/..", "file:///C:/a/b", "file:///d:/", id="drive-authority-parent"),
        pytest.param("file://localhost//a//../..//foo", "", "file://///foo", id="file-empty-segments"),
        pytest.param("file:///other", "https://example.org/", "file:///other", id="file-from-web-base"),
        pytest.param("https://other.org/", "file:///x", "https://other.org/", id="web-from-file-base"),
        pytest.param("file:/example.com/", "https://example.org/", "file:///example.com/", id="file-from-web-path"),
        pytest.param("file:C|/m/", "", "file:///C:/m/", id="file-drive-without-base"),
        pytest.param("file:C||/m/", "", "file:///C||/m/", id="file-drive-extra-bar"),
        pytest.param("file:///w|m", "", "file:///w|m", id="bar-in-file-name"),
        pytest.param("file:///w|/m", "", "file:///w:/m", id="bar-in-drive"),
        pytest.param("/C|\\foo\\bar", "file:///tmp/mock/path", "file:///C:/foo/bar", id="rooted-drive-backslashes"),
        pytest.param("//C|/foo/bar", "file:///tmp/mock/path", "file:///C:/foo/bar", id="drive-in-authority"),
        pytest.param("//server/file", "file:///tmp/mock/path", "file://server/file", id="unc-forward-slashes"),
        pytest.param("//", "file:///tmp/mock/path", "file:///", id="empty-unc-authority"),
        pytest.param("file://test", "file:///tmp/mock/path", "file://test/", id="file-host-no-path"),
        pytest.param("file://localhost", "file:///tmp/mock/path", "file:///", id="localhost-no-path"),
        pytest.param("..", "file:///", "file:///", id="parent-of-file-root"),
        pytest.param("/", "file://h/C:/a/b", "file://h/C:/", id="hosted-drive-root"),
        pytest.param("/", "file://h/a/b", "file://h/", id="hosted-root"),
        pytest.param("?x", "file:///test?test#test", "file:///test?x", id="file-query"),
        pytest.param("#x", "file:///test?test#test", "file:///test?test#x", id="file-fragment"),
        pytest.param("file:?x", "file:///test?test#test", "file:///test?x", id="file-scheme-query"),
        pytest.param("file:#x", "file:///test?test#test", "file:///test?test#x", id="file-scheme-fragment"),
        pytest.param("C|#", "file://host/dir/file", "file://host/C:#", id="drive-fragment"),
        pytest.param("C|?", "file://host/dir/file", "file://host/C:?", id="drive-query"),
        pytest.param("C|/", "file://host/dir/file", "file://host/C:/", id="drive-trailing-slash"),
        pytest.param("C", "file://host/dir/file", "file://host/dir/C", id="drive-letter-is-relative"),
        pytest.param("C|a", "file://host/dir/file", "file://host/dir/C|a", id="bar-without-drive-slash"),
        pytest.param("/c:/foo/bar", "file://host/path", "file://host/c:/foo/bar", id="hosted-rooted-drive"),
        pytest.param("file:C:/", "file://host/", "file://host/C:/", id="hosted-file-drive"),
        pytest.param("//C:/", "file://host/", "file:///C:/", id="drive-authority-trailing-slash"),
        pytest.param("file://C|/", "", "file:///C:/", id="file-drive-authority-without-base"),
        pytest.param("file://localhost//a//../..//", "", "file://///", id="file-empty-segments-root"),
        pytest.param("/..//localhost//pig", "file://lion/", "file://lion//localhost//pig", id="rooted-parent-slashes"),
        pytest.param("file:.//p", "", "file:////p", id="file-protective-relative"),
        pytest.param("file:/.//p", "", "file:////p", id="file-protective-root"),
        pytest.param("foo", "file://host/dir/", "file://host/dir/foo", id="file-directory-base"),
        pytest.param("foo", "file://host", "file://host/foo", id="file-host-without-path"),
    ],
)
def test_base_url_file_resolution(href: str, fallback: str, expected: str) -> None:
    assert parse(f'<base href="{href}">').base_url(fallback) == expected


@pytest.mark.parametrize(
    "href",
    [
        pytest.param("file://user@host/x", id="file-userinfo"),
        pytest.param("file://host:9/x", id="file-port"),
    ],
)
def test_base_url_uses_fallback_for_invalid_file_authority(href: str) -> None:
    assert parse(f'<base href="{href}">').base_url("https://example.org/") == "https://example.org/"


@pytest.mark.parametrize(
    ("href", "fallback"),
    [
        pytest.param("file://[bad/", "file:///x", id="invalid-file-target"),
        pytest.param("x", "file://[bad/", id="invalid-file-base"),
    ],
)
def test_base_url_uses_fallback_for_unbalanced_file_authority(href: str, fallback: str) -> None:
    assert parse(f'<base href="{href}">').base_url(fallback) == fallback


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        pytest.param("a//", "https://example.org/dir/a//", id="trailing-double-slash"),
        pytest.param("a///b", "https://example.org/dir/a///b", id="middle-triple-slash"),
        pytest.param("a/..//b", "https://example.org/dir//b", id="parent-then-double-slash"),
    ],
)
def test_base_url_preserves_relative_path_slash_runs(href: str, expected: str) -> None:
    assert parse(f'<base href="{href}">').base_url("https://example.org/dir/page") == expected


@pytest.mark.parametrize(
    ("href", "fallback", "expected"),
    [
        pytest.param("i", "sc:/pa/pa", "sc:/pa/i", id="rooted-path"),
        pytest.param("i", "sc://ho/pa", "sc://ho/i", id="authority"),
        pytest.param("i", "sc:///pa/pa", "sc:///pa/i", id="empty-authority"),
        pytest.param("../i", "sc://ho/pa", "sc://ho/i", id="parent"),
        pytest.param("/i", "sc://ho/pa", "sc://ho/i", id="root"),
        pytest.param("?i", "sc://ho/pa", "sc://ho/pa?i", id="query"),
        pytest.param("#i", "sc://ho/pa", "sc://ho/pa#i", id="fragment"),
        pytest.param("x", "sc://ñ", "sc://%C3%B1/x", id="opaque-host"),
        pytest.param("a//b", "sc://ho/pa", "sc://ho/a//b", id="relative-slash-run"),
        pytest.param("///", "sc://x/", "sc:///", id="empty-authority-root"),
        pytest.param("////", "sc://x/", "sc:////", id="empty-authority-double-slash"),
        pytest.param("////x/", "sc://x/", "sc:////x/", id="empty-authority-double-slash-path"),
        pytest.param("/.//path", "non-spec:/p", "non-spec:/.//path", id="protective-root"),
        pytest.param("/..//path", "non-spec:/p", "non-spec:/.//path", id="protective-root-parent"),
        pytest.param("..//path", "non-spec:/p", "non-spec:/.//path", id="protective-relative-parent"),
        pytest.param("a/..//path", "non-spec:/p", "non-spec:/.//path", id="protective-relative-nested-parent"),
        pytest.param("", "non-spec:/..//p", "non-spec:/.//p", id="protective-empty-reference"),
        pytest.param("path", "non-spec:/..//p", "non-spec:/.//path", id="protective-base-parent"),
        pytest.param("../path", "non-spec:/.//p", "non-spec:/path", id="protective-base-parent-resolution"),
        pytest.param("gopher:q", "gopher://host/p", "gopher:q", id="same-non-special-scheme-is-absolute"),
        pytest.param("p", "sc:", "sc:", id="opaque-base-relative-target"),
        pytest.param("p", "a/b", "a/p", id="schemeless-relative-base"),
        pytest.param("x", "a", "x", id="one-character-merged-path"),
        pytest.param("sc:other", "sc:old", "sc:other", id="same-opaque-scheme-is-absolute"),
        pytest.param("p", "http:///", "http://p/", id="hostless-special-base"),
    ],
)
def test_base_url_resolves_non_special_relative(href: str, fallback: str, expected: str) -> None:
    assert parse(f'<base href="{href}">').base_url(fallback) == expected


@pytest.mark.parametrize(
    ("base", "href", "expected"),
    [
        pytest.param(
            "http://site.org/dir/page", "http:\\\\example.com\\p", "http://example.com/p", id="backslash-authority"
        ),
        pytest.param(
            "http://site.org/dir/page", "HTTP:\\\\example.com\\p", "http://example.com/p", id="uppercase-scheme"
        ),
        pytest.param("http://site.org/dir/page", "http:/\\example.com\\p", "http://example.com/p", id="mixed-opener"),
        pytest.param("https://site.org/dir/page", "https:\\\\example.com\\p", "https://example.com/p", id="https"),
        pytest.param(
            "http://site.org/dir/page", "http:\\\\example.com\\p\x01", "http://example.com/p", id="trailing-control"
        ),
        pytest.param(
            "http://site.org/dir/page\x01", "http:\\\\example.com\\p", "http://example.com/p", id="trimmed-base"
        ),
        pytest.param("http://site.org/dir/page", "http:other", "http://site.org/dir/other", id="same-scheme-relative"),
        pytest.param("http://site.org/dir/page", "http:/page", "http://site.org/page", id="single-slash"),
        pytest.param("http://site.org/dir/page", "http:\\", "http://site.org/", id="bare-backslash-root"),
        pytest.param("https://site.org/dir/page", "https:\\", "https://site.org/", id="https-backslash-root"),
        pytest.param("ftp://site.org/dir/page", "ftp:\\", "ftp://site.org/", id="ftp-backslash-root"),
        pytest.param("ws://site.org/dir/page", "ws:\\", "ws://site.org/", id="ws-backslash-root"),
        pytest.param("wss://site.org/dir/page", "WSS:\\", "wss://site.org/", id="uppercase-wss-backslash-root"),
        pytest.param("https://site.org/dir/page", "h%tp:", "https://site.org/dir/h%tp:", id="invalid-scheme-tail"),
        pytest.param("https://site.org/dir/page", "//other\\p", "https://other/p", id="network-authority-end"),
        pytest.param("https://site.org/dir/page", "https://other\\p", "https://other/p", id="web-authority-end"),
        pytest.param("git://site.org/dir/page", "https://other\\p", "https://other/p", id="web-under-git"),
        pytest.param("https://site.org/dir/page", "//é\\p", "https://xn--9ca/p", id="unicode-host"),
        pytest.param("https://site.org/dir/page", "//other\\p?q=\\", "https://other/p?q=\\", id="query-backslash"),
        pytest.param("https://site.org/dir/page", "git://other/path\\p", "git://other/path\\p", id="non-special-path"),
        pytest.param("https://site.org/dir/page", "//other/p\\q", "https://other/p/q", id="network-path-backslash"),
        pytest.param(
            "https://site.org/dir/page", "//other?x=\\q", "https://other/?x=\\q", id="network-query-backslash"
        ),
        pytest.param(
            "https://site.org/dir/page", "//other#x\\q", "https://other/#x\\q", id="network-fragment-backslash"
        ),
        pytest.param("https://site.org/dir/page", "https://other/p\\q", "https://other/p/q", id="web-path-backslash"),
        pytest.param("https://site.org/dir/page", "//other\\..", "https://other/", id="network-parent"),
        pytest.param("https://site.org/dir/page", "\\\\other\\.", "https://other/", id="double-backslash-dot"),
        pytest.param("https://site.org/dir/page", "//other\\..#f", "https://other/#f", id="network-parent-fragment"),
        pytest.param("https://site.org/dir/page", "//other?q=\\..", "https://other/?q=\\..", id="query-dot"),
        pytest.param(
            "https://site.org/dir/page", "https://other?x=\\q", "https://other/?x=\\q", id="web-query-backslash"
        ),
        pytest.param(
            "https://site.org/dir/page", "https://other#x\\q", "https://other/#x\\q", id="web-fragment-backslash"
        ),
        pytest.param("https://site.org/dir/page", "ftp://other\\p", "ftp://other/p", id="ftp-authority-end"),
        pytest.param("https://site.org/dir/page", "ws://other\\p", "ws://other/p", id="ws-authority-end"),
        pytest.param("https://site.org/dir/page", "file://other\\p", "file://other/p", id="file-authority-end"),
        pytest.param("https://site.org/dir/page", "faux:x\\p", "faux:x\\p", id="non-special-f-prefix"),
        pytest.param("https://site.org/dir/page", "web:x\\p", "web:x\\p", id="non-special-w-prefix"),
    ],
)
def test_base_url_resolves_absolute_special_backslashes(base: str, href: str, expected: str) -> None:
    assert parse(f'<base href="{href}">').base_url(base) == expected


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        pytest.param("http:///a/..", "http://a/", id="host-before-parent"),
        pytest.param("ws:///a", "ws://a/", id="short-ws-host"),
        pytest.param("ws:///.", "ws://./", id="short-ws-dot-host"),
        pytest.param("http:///.", "http://./", id="dot-host"),
        pytest.param("http:///%2e%2e/a", "http://../a", id="encoded-parent-host"),
        pytest.param("ftp:////b/..", "ftp://b/", id="four-slashes"),
        pytest.param("ws://\\b/..", "ws://b/", id="mixed-backslash"),
        pytest.param("WSS:\\\\..", "wss://../", id="uppercase-backslashes"),
        pytest.param("http:///é/..", "http://xn--9ca/", id="unicode-host"),
        pytest.param("http:///%2e?x#f", "http://./?x#f", id="query-and-fragment"),
        pytest.param("http:///[::1]/..", "http://[::1]/", id="ipv6-host"),
        pytest.param("http:////\\a/..", "http://a/", id="extra-mixed-separators"),
        pytest.param("hello:///a/..", "hello:///", id="non-special-scheme"),
    ],
)
def test_base_url_resolves_extra_special_authority_slashes(href: str, expected: str) -> None:
    assert parse(f'<base href="{href}">').base_url("https://site.org/a") == expected


@pytest.mark.parametrize(
    ("base", "href", "expected"),
    [
        pytest.param("https://site.org/dir/page", "http:/b/..", "http://b/", id="foreign-parent"),
        pytest.param("https://site.org/dir/page", "http:/b\\..", "http://b/", id="foreign-backslash-parent"),
        pytest.param("https://site.org/dir/page", "FTP:/b\\..", "ftp://b/", id="foreign-ftp-backslash-parent"),
        pytest.param("https://site.org/dir/page", "http:/b/%2e%2e", "http://b/", id="foreign-encoded-parent"),
        pytest.param("https://site.org/dir/page", "ftp:/b/..", "ftp://b/", id="ftp-parent"),
        pytest.param("https://site.org/dir/page", "ws:/..", "ws://../", id="ws-dot-host"),
        pytest.param("https://site.org/dir/page", "wss:/b/..", "wss://b/", id="wss-parent"),
        pytest.param("https://site.org/dir/page", "http:\\b\\..", "http://b/", id="foreign-backslash"),
        pytest.param("https://site.org/dir/page", "ftp:\\é", "ftp://xn--9ca/", id="unicode-host"),
        pytest.param("https://site.org/dir/page", "https:\\b\\..", "https://site.org/", id="same-backslash"),
        pytest.param("https://site.org/dir/page", "https:/b/..", "https://site.org/", id="same-slash"),
        pytest.param("https://site.org/dir/page", "HTTP:/b/..", "http://b/", id="uppercase-scheme"),
        pytest.param("https://site.org/dir/page", "http:/b", "http://b/", id="plain-host"),
        pytest.param("https://site.org/dir/page", "http:\\b\\..?q=\\", "http://b/?q=\\", id="query-backslash"),
        pytest.param("https://site.org/dir/page", "http:/b/..#x\\f", "http://b/#x\\f", id="fragment-backslash"),
        pytest.param("https://site.org/dir/page", "http:/\\b/..", "http://b/", id="mixed-separators"),
        pytest.param("https://site.org/dir/page", "http:\\/b/%2e%2e", "http://b/", id="reversed-separators"),
        pytest.param("https://site.org/dir/page", "ftp:\\\\b/..", "ftp://b/", id="double-backslash"),
        pytest.param("ftp://site.org/dir/page", "wss:/b/..", "wss://b/", id="same-length-foreign-scheme"),
        pytest.param("ftp://site.org/dir/page", "ftp:/", "ftp://site.org/", id="short-same-scheme"),
        pytest.param("https://site.org/dir/page", "ftp:/é", "ftp://xn--9ca/", id="unicode-slash-host"),
        pytest.param("https://site.org/dir/page", "ftp:/é/..", "ftp://xn--9ca/", id="unicode-parent"),
        pytest.param("https://site.org/dir/page", "ftp:/é%2e%2e", "ftp://xn--9ca../", id="unicode-encoded-dot"),
        pytest.param("https://site.org/dir/page", "ftp://b/..", "ftp://b/", id="ftp-absolute-parent"),
        pytest.param("https://site.org/dir/page", "FTP://b/..", "ftp://b/", id="uppercase-ftp-parent"),
        pytest.param("https://site.org/dir/page", "ws://b\\a", "ws://b/a", id="ws-authority-backslash"),
        pytest.param("https://site.org/dir/page", "web://host/a", "web://host/a", id="non-special-w-prefix"),
        pytest.param("https://site.org/dir/page", "ftp:\\b", "ftp://b/", id="foreign-short-backslash"),
        pytest.param("https://site.org/dir/page", "ftp:/\\b", "ftp://b/", id="foreign-short-mixed"),
        pytest.param("https://site.org/a?x", "hello:/b", "hello:/b", id="plain-non-special-scheme"),
        pytest.param("https://site.org/dir/page", "http:/b/%", "http://b/%", id="percent-path"),
        pytest.param("https://site.org/dir/page", "hello:/b/..", "hello:/", id="non-special-prefix"),
        pytest.param("https://site.org/dir/page", "git:\\b\\..", "git:\\b\\..", id="non-special"),
    ],
)
def test_base_url_resolves_single_special_separator(base: str, href: str, expected: str) -> None:
    assert parse(f'<base href="{href}">').base_url(base) == expected


def test_base_url_resolves_dom_set_nul_before_parent_segment() -> None:
    document = parse("<base href=x>")
    document.select("base")[0].attrs["href"] = "http:/b/a\0/.."
    assert document.base_url("https://site.org/a") == "http://b/"


@pytest.mark.parametrize(
    "href",
    [
        pytest.param("///example.com/p", id="three-slashes"),
        pytest.param("////example.com/p", id="four-slashes"),
        pytest.param("//\\example.com/p", id="third-backslash"),
        pytest.param("///\\//\\//example.com/p", id="mixed-extra-slashes"),
    ],
)
def test_base_url_resolves_extra_special_slashes(href: str) -> None:
    assert parse(f'<base href="{href}">').base_url("http://site.org/dir/page") == "http://example.com/p"


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        pytest.param("//", "http://site.org/dir/page", id="empty-authority"),
        pytest.param("///", "http://site.org/dir/page", id="extra-slashes-without-host"),
        pytest.param("//example.com/p", "http://example.com/p", id="two-slashes"),
        pytest.param("http:/?x", "http://site.org/?x", id="same-scheme-root-query"),
        pytest.param("/\\/example.com/p", "http://example.com/p", id="mixed-three-slashes"),
        pytest.param("/\\example.com/é", "http://example.com/%C3%A9", id="unicode-second-backslash"),
        pytest.param("//example.com/é", "http://example.com/%C3%A9", id="unicode-two-slashes"),
        pytest.param("///example.com/é", "http://example.com/%C3%A9", id="unicode-three-slashes"),
        pytest.param("//\\example.com/é", "http://example.com/%C3%A9", id="unicode-third-backslash"),
    ],
)
def test_base_url_keeps_special_slash_boundaries(href: str, expected: str) -> None:
    assert parse(f'<base href="{href}">').base_url("http://site.org/dir/page") == expected


def test_base_url_preserves_query_backslash_after_extra_slashes() -> None:
    assert (
        parse('<base href="///example.com/p?q=\\">').base_url("http://site.org/dir/page") == "http://example.com/p?q=\\"
    )


@pytest.mark.parametrize(
    ("href", "fallback", "expected"),
    [
        pytest.param("%2e%2e/x", "https://example.org/dir/file", "https://example.org/x", id="parent"),
        pytest.param("%2E./x", "https://example.org/dir/file", "https://example.org/x", id="mixed-parent"),
        pytest.param("%2e/x", "https://example.org/dir/file", "https://example.org/dir/x", id="current"),
        pytest.param("%2e?x", "https://example.org/dir/file", "https://example.org/dir/?x", id="query"),
        pytest.param("%2e%2e/x", "https://example.org/dir/%2e%2e/file", "https://example.org/x", id="base"),
        pytest.param("x", "https://example.org/dir/%2e%2e", "https://example.org/x", id="base-terminal"),
        pytest.param("x", "https://example.org/é/%2e%2e", "https://example.org/x", id="unicode-base"),
        pytest.param("a%2eb", "https://example.org/dir/file", "https://example.org/dir/a%2eb", id="non-dot"),
        pytest.param("/a/%2e%2e/..", "https://example.org/.", "https://example.org/", id="mixed-order"),
        pytest.param(
            "https://example.org/aaaaaaaaaaaaaaa/%2e%2e/x",
            "https://example.org/dir/file",
            "https://example.org/x",
            id="long-absolute",
        ),
        pytest.param(
            "https://example.org/é/%3e/%2f/%2e%2e/x",
            "https://example.org/dir/file",
            "https://example.org/%C3%A9/%3e/x",
            id="unicode-and-other-escapes",
        ),
        pytest.param("/a/%2e%2e/x#f", "https://example.org/dir/file", "https://example.org/x#f", id="fragment"),
        pytest.param(
            "https://other.org/a/%2e/x?x= y",
            "https://example.org/dir/file",
            "https://other.org/a/x?x=%20y",
            id="dot-segments-and-query-spaces",
        ),
        pytest.param("mailto:a/%2e%2e/x", "https://example.org/dir/file", "mailto:a/%2e%2e/x", id="opaque-path"),
        pytest.param("mailto:?q=%2e", "https://example.org/dir/file", "mailto:?q=%2e", id="opaque-query"),
        pytest.param(
            "https://other.org/a%2eb/x?q=/./",
            "https://example.org/dir/file",
            "https://other.org/a%2eb/x?q=/./",
            id="encoded-name-and-query-dot",
        ),
        pytest.param("foo:/a/%2e%2e/x", "https://example.org/dir/file", "foo:/x", id="non-special-root-path"),
        pytest.param("http:b/%2e%2e", "https://example.org/dir/file", "http://b/", id="foreign-web-parent"),
        pytest.param("http:g/%2e%2e", "https://example.org/dir/file", "http://g/", id="web-fast-host"),
        pytest.param("http:g/..", "https://example.org/dir/file", "http://g/", id="literal-foreign-parent"),
        pytest.param("ftp:b/%2e%2e", "https://example.org/dir/file", "ftp://b/", id="foreign-ftp-parent"),
        pytest.param("ws:b/%2e%2e", "https://example.org/dir/file", "ws://b/", id="foreign-ws-parent"),
        pytest.param(
            "https:b/%2e%2e", "https://example.org/dir/file", "https://example.org/dir/", id="same-scheme-parent"
        ),
        pytest.param(
            "https:%2e%2e/a", "https://example.org/dir/file", "https://example.org/a", id="same-scheme-prefix"
        ),
        pytest.param(
            "https:%2e/..", "https://example.org/dir/file", "https://example.org/", id="same-scheme-encoded-dot-parent"
        ),
        pytest.param(
            "https:%2e/../x", "https://example.org/dir/file", "https://example.org/x", id="same-scheme-encoded-dot-path"
        ),
        pytest.param(
            "https:a/%2e/..", "https://example.org/dir/file", "https://example.org/dir/", id="middle-dot-parent"
        ),
        pytest.param("https:.%2e/..", "https://example.org/dir/file", "https://example.org/", id="mixed-dot-parent"),
        pytest.param(
            "HTTPS:%2E/..?q=x", "https://example.org/dir/file", "https://example.org/?q=x", id="uppercase-encoded-dot"
        ),
        pytest.param("http:%2e/..", "hxxp://site.org/a/b", "http://./", id="different-same-length-scheme"),
        pytest.param("https:%2e/..", "", "https://./", id="no-fallback-scheme"),
        pytest.param("https:%2e/..", "customlong://site.org/a/b", "https://./", id="long-foreign-scheme"),
        pytest.param("https:%2e/..", "extraverylongscheme://site.org/a/b", "https://./", id="longer-foreign-scheme"),
        pytest.param("https:%2e/..", "https://site.org/a/%2e/b", "https://site.org/", id="encoded-dot-base"),
        pytest.param("https:\\b/%2e", "https://site.org/a/b", "https://site.org/b/", id="same-scheme-backslash"),
        pytest.param(
            "https:\\b/%2e", "https://site.org/a/%2e/b", "https://site.org/b/", id="backslash-encoded-dot-base"
        ),
        pytest.param("https:/\\b/%2e", "https://site.org/a/b", "https://b/", id="mixed-authority-separators"),
        pytest.param("https:\\/b/%2e", "https://site.org/a/b", "https://b/", id="reverse-mixed-authority"),
        pytest.param("https:\\\\b/%2e", "https://site.org/a/b", "https://b/", id="double-backslash-authority"),
        pytest.param("https://b/%2e", "https://site.org/a/%2e/b", "https://b/", id="authority-with-encoded-dot-base"),
        pytest.param("ftp:\\\\b/%2e", "ftp://site.org/a/b", "ftp://b/", id="ftp-double-backslash-authority"),
        pytest.param("https://other.org/%2e", "https://site.org/a/b", "https://other.org/", id="same-scheme-authority"),
        pytest.param("hxxp:%2e/..", "hxxp://site.org/a/b", "hxxp:%2e/..", id="non-special-same-scheme"),
        pytest.param(
            "https:%20?x=%2e", "https://site.org/a/b", "https://site.org/a/%20?x=%2e", id="other-leading-escape"
        ),
        pytest.param("https:%2f?x=%2e", "https://site.org/a/b", "https://site.org/a/%2f?x=%2e", id="encoded-slash"),
        pytest.param("https:%2e#%2e", "https://site.org/a/b", "https://site.org/a/#%2e", id="fragment-after-dot"),
        pytest.param("http:%2e/a", "https://example.org/dir/file", "http://./a", id="encoded-dot-host"),
        pytest.param("http:g/%2", "https://example.org/dir/file", "http://g/%2", id="incomplete-escape"),
        pytest.param("http:g/%2f", "https://example.org/dir/file", "http://g/%2f", id="non-dot-escape"),
        pytest.param("http:g/%2e%2f", "https://example.org/dir/file", "http://g/%2e%2f", id="mixed-escape"),
        pytest.param("https://g/./%2", "https://example.org/dir/file", "https://g/%2", id="short-escape-with-dot"),
        pytest.param("https://g/./%2f", "https://example.org/dir/file", "https://g/%2f", id="other-escape-with-dot"),
        pytest.param("/./%2", "https://g/a", "https://g/%2", id="relative-short-escape"),
        pytest.param("/./%2f", "https://g/a", "https://g/%2f", id="relative-other-escape"),
        pytest.param("https://g/a?x=%2e", "https://example.org/dir/file", "https://g/a?x=%2e", id="web-query-dot"),
        pytest.param("http://g/path#%2e", "https://example.org/dir/file", "http://g/path#%2e", id="fragment-dot"),
        pytest.param("#%2e", "https://example.org/dir/file", "https://example.org/dir/file#%2e", id="fragment-only"),
        pytest.param("?x=%2e", "https://é/a", "https://xn--9ca/a?x=%2e", id="unicode-base-query-dot"),
        pytest.param(
            "https:/b/%2e%2e", "https://example.org/dir/file", "https://example.org/", id="single-slash-parent"
        ),
        pytest.param("faux:/a/%2e", "https://example.org/dir/file", "faux:/a/", id="non-special-f-prefix"),
        pytest.param("web:/a/%2e", "https://example.org/dir/file", "web:/a/", id="non-special-w-prefix"),
        pytest.param("b", "https://example.org/a?x=%2e", "https://example.org/b", id="base-query-only"),
        pytest.param("x", "mailto:%2e", "mailto:%2e", id="opaque-base"),
        pytest.param(
            "https://other.org/a/%2e%2e/x", "https://example.org/dir/file", "https://other.org/x", id="absolute"
        ),
    ],
)
def test_base_url_resolves_encoded_dot_segments(href: str, fallback: str, expected: str) -> None:
    assert parse(f'<base href="{href}">').base_url(fallback) == expected


@pytest.mark.parametrize(
    ("fallback", "href", "expected"),
    [
        pytest.param("https://site.org/a/b", "http:é\\./", "http://xn--9ca/", id="foreign-unicode-host"),
        pytest.param("git://site.org/a/b", "http:a\\../", "http://a/", id="foreign-non-special-base"),
        pytest.param("file:///C:/a/b", "https:a\\../", "https://a/", id="foreign-file-base"),
        pytest.param("https://site.org/a/b", "https:a\\../", "https://site.org/a/", id="same-scheme"),
        pytest.param("https://site.org/a/b", "https:/a\\..", "https://site.org/", id="single-slash-same-scheme"),
        pytest.param("https://site.org/a/b", "FTP://a\\..", "ftp://a/", id="absolute-ftp-parent"),
        pytest.param("https://site.org/a/b", "ws://a\\..", "ws://a/", id="absolute-ws-parent"),
        pytest.param("https://site.org/a/b", "WSS://%2e%2e\\..", "wss://../", id="absolute-encoded-host"),
        pytest.param("https://site.org/a/b", "HTTP://a\\..", "http://a/", id="absolute-http-parent"),
        pytest.param("https://site.org/a/b", "ftp://a\\..#x\\.", "ftp://a/#x\\.", id="absolute-fragment"),
        pytest.param("https://site.org/a/b", "http:\\.\\a", "http://./a", id="leading-backslash-dot-host"),
        pytest.param("https://site.org/a/b", "http:a\\..#x\\.", "http://a/#x\\.", id="fragment-backslash"),
        pytest.param("https://site.org/a/b", "http:a#x\\.", "http://a/#x\\.", id="fragment-only-backslash"),
        pytest.param("https://site.org/a/b", "http:a?x=\\.", "http://a/?x=\\.", id="query-backslash"),
        pytest.param("https://site.org/a/b", "faux:a\\..", "faux:a\\..", id="non-special-scheme"),
    ],
)
def test_base_url_resolves_special_backslash_dot(fallback: str, href: str, expected: str) -> None:
    assert parse(f'<base href="{href}">').base_url(fallback) == expected


@pytest.mark.parametrize(
    ("href", "fallback", "expected"),
    [
        pytest.param("http://example.org/a/../b", "http://site.org/", "http://example.org/b", id="absolute-parent"),
        pytest.param("//example.org/a/./b", "http://site.org/", "http://example.org/a/b", id="authority-current"),
        pytest.param("http://example.org/a/..", "http://site.org/", "http://example.org/", id="trailing-parent"),
        pytest.param(
            "http://example.org/.well-known/x", "http://site.org/", "http://example.org/.well-known/x", id="name"
        ),
        pytest.param(
            "http://example.org/a/../b?next=/../x#f/./g",
            "http://site.org/",
            "http://example.org/b?next=/../x#f/./g",
            id="query-and-fragment",
        ),
        pytest.param("foo:/a/../b", "http://site.org/", "foo:/b", id="non-special"),
        pytest.param("mailto:x/../y", "http://site.org/", "mailto:x/../y", id="opaque"),
        pytest.param("http://example.org?x=/../", "http://site.org/", "http://example.org/?x=/../", id="query-only"),
        pytest.param(
            "http://example.org/a/?next=/../",
            "http://site.org/",
            "http://example.org/a/?next=/../",
            id="path-ending-slash",
        ),
        pytest.param("#f", "http://example.org/a/../b", "http://example.org/b#f", id="fragment-inherits-base"),
        pytest.param("?q", "http://example.org/a/./b", "http://example.org/a/b?q", id="query-inherits-base"),
    ],
)
def test_base_url_resolves_literal_dot_segments(href: str, fallback: str, expected: str) -> None:
    assert parse(f'<base href="{href}">').base_url(fallback) == expected


@pytest.mark.parametrize(
    "href",
    [
        pytest.param("https://example.org/abcdefghijklmnop", id="no-escape"),
        pytest.param("https://example.org/aaaaaaaaaaaaaaa/%20/x", id="space-escape"),
        pytest.param("https://example.org/aaaaaaaaaaaaaaa/%3e/x", id="non-dot-escape"),
        pytest.param("https://example.org/aaaaaaaaaaaaaaa/%", id="trailing-percent"),
    ],
)
def test_base_url_preserves_long_non_dot_paths(href: str) -> None:
    assert parse(f'<base href="{href}">').base_url("https://example.org/dir/file") == href


def test_base_url_uses_fallback_for_unclosed_bracket_with_encoded_dot() -> None:
    assert parse('<base href="https://[bad/%2e">').base_url("https://example.org/") == "https://example.org/"


@pytest.mark.parametrize(
    ("href", "fallback", "expected"),
    [
        pytest.param(
            "http://user]@example.org/path",
            "https://site.org/x",
            "http://user%5D@example.org/path",
            id="close-in-username",
        ),
        pytest.param(
            "http://user[@example.org/path",
            "https://site.org/x",
            "http://user%5B@example.org/path",
            id="open-in-username",
        ),
        pytest.param(
            "http://[u]@example.org/a/%2e/x",
            "https://site.org/x",
            "http://%5Bu%5D@example.org/a/x",
            id="paired-with-encoded-dot",
        ),
        pytest.param(
            "//user]@example.org/path",
            "https://site.org/x",
            "https://user%5D@example.org/path",
            id="schemeless-authority",
        ),
        pytest.param(
            "child",
            "https://user]@example.org/dir/file",
            "https://user%5D@example.org/dir/child",
            id="inherited-userinfo",
        ),
        pytest.param(
            "http://user]@[::1]/x",
            "https://site.org/x",
            "http://user%5D@[::1]/x",
            id="ipv6-host",
        ),
    ],
)
def test_base_url_encodes_userinfo_brackets(href: str, fallback: str, expected: str) -> None:
    assert parse(f'<base href="{href}">').base_url(fallback) == expected


def test_base_url_uses_fallback_for_unclosed_host_bracket_after_userinfo() -> None:
    assert parse('<base href="http://user]@[::1/x">').base_url("https://site.org/x") == "https://site.org/x"


def test_meta_refresh_resolves_encoded_dot_segments() -> None:
    assert parse('<meta http-equiv=refresh content="0;url=/a/%2e%2e/..">').meta_refresh("https://example.org/.") == (
        0.0,
        "https://example.org/",
    )


def test_meta_refresh_resolves_root_backslash_before_trailing_control() -> None:
    assert parse('<meta http-equiv="refresh" content="0;url=\\x\x01">').meta_refresh(
        "https://example.org/dir/page"
    ) == (0.0, "https://example.org/x")


@pytest.mark.parametrize(
    ("href", "fallback", "expected"),
    [
        pytest.param("a\x01", "https://example.org/d/f", "https://example.org/d/a", id="relative-path"),
        pytest.param("\x01", "https://example.org/d/f", "https://example.org/d/f", id="control-only"),
        pytest.param("é\x01", "https://example.org/d/f", "https://example.org/d/%C3%A9", id="unicode-path"),
        pytest.param("?q\x02", "https://example.org/d/f", "https://example.org/d/f?q", id="query"),
        pytest.param("#f\x01", "https://example.org/d/f", "https://example.org/d/f#f", id="fragment"),
        pytest.param("https://other.org/a\x01", "https://example.org/d/f", "https://other.org/a", id="absolute"),
        pytest.param("#f", "https://example.org/d/f\x01", "https://example.org/d/f#f", id="fallback"),
        pytest.param("a\x01b", "https://example.org/d/f", "https://example.org/d/a%01b", id="interior-retained"),
    ],
)
def test_base_url_trims_trailing_c0(href: str, fallback: str, expected: str) -> None:
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
        pytest.param("5;url=\x01", (5.0, _SITE), id="control-only-url"),
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


def test_meta_refresh_unresolvable_url_raises() -> None:
    html = '<meta http-equiv=refresh content="0;url=//[bad">'
    with pytest.raises(ValueError, match="Invalid IPv6 URL"):
        parse(html).meta_refresh(_SITE)


def test_meta_refresh_rejects_relative_opaque_reference() -> None:
    with pytest.raises(ValueError, match="cannot resolve against an opaque URL"):
        parse('<meta http-equiv=refresh content="0;url=child">').meta_refresh("mailto:user@example.org")


@pytest.mark.parametrize(
    ("href", "fallback"),
    [
        pytest.param("https://", "http://site.org/a", id="two-slashes-foreign-scheme"),
        pytest.param("http:///", "http://site.org/a", id="three-slashes-same-scheme"),
        pytest.param("http:////", "http://site.org/a", id="four-slashes-same-scheme"),
        pytest.param("ws:////", "http://site.org/a", id="short-ws-missing-host"),
        pytest.param("http:////?q", "https://site.org/a", id="empty-host-query"),
        pytest.param("http:////#f", "https://site.org/a", id="empty-host-fragment"),
    ],
)
def test_meta_refresh_rejects_missing_special_host(href: str, fallback: str) -> None:
    with pytest.raises(ValueError, match="special URL requires a host"):
        parse(f'<meta http-equiv=refresh content="0;url={href}">').meta_refresh(fallback)


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


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        pytest.param("/a/b", "/a/b", id="no-dot-segments"),
        pytest.param("no-percent-no-dot", "no-percent-no-dot", id="fast-path-unchanged"),
        pytest.param("a.b/c", "a.b/c", id="dot-inside-segment-kept"),
        pytest.param("/a/./b", "/a/b", id="single-dot-dropped"),
        pytest.param("/a/../b", "/b", id="double-dot-pops-parent"),
        pytest.param("/a/%2e/b", "/a/b", id="percent-2e-lower-is-dot"),
        pytest.param("/a/%2E/b", "/a/b", id="percent-2e-upper-is-dot"),
        pytest.param("/a/%2E./b", "/b", id="mixed-escape-and-literal-is-dotdot"),
        pytest.param("/%2e%2e/b", "/b", id="escaped-dotdot-pops"),
        pytest.param("/a/.../b", "/a/.../b", id="three-dots-not-a-dot-segment"),
        pytest.param("/a/%2f/b", "/a/%2f/b", id="escape-not-2e-third-char"),
        pytest.param("/a/%3e/b", "/a/%3e/b", id="escape-not-2e-second-char"),
        pytest.param("/a/%2/b", "/a/%2/b", id="escape-too-short-for-2e"),
        pytest.param("/a/.", "/a/", id="trailing-single-dot"),
        pytest.param("/a/..", "/", id="trailing-double-dot"),
        pytest.param("%2E", "", id="lone-escaped-dot"),
    ],
)
def test_remove_dot_segments_cases(path: str, expected: str) -> None:
    assert _url_remove_dot_segments(path) == expected


def test_remove_dot_segments_rejects_non_str() -> None:
    with pytest.raises(TypeError, match="must be str"):
        _url_remove_dot_segments(123)  # ty: ignore[invalid-argument-type]  # a non-str exercises the TypeError guard


_SCHEME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+-.")
_C0_OR_SPACE = "".join(map(chr, range(0x21)))
_USES_RELATIVE = frozenset({
    *("", "ftp", "http", "gopher", "nntp", "imap", "wais", "file", "https", "shttp"),
    *("mms", "prospero", "rtsp", "rtsps", "rtspu", "sftp", "svn", "svn+ssh", "ws", "wss"),
})


def _split5(url: str) -> tuple[str | None, str | None, str, str | None, str | None]:
    """CPython 3.14's ``_urlsplit`` reduced to a ``str``: leading strip, scheme, authority, query, and fragment."""
    url = url.lstrip(_C0_OR_SPACE)
    for unsafe in "\t\r\n":
        url = url.replace(unsafe, "")
    scheme = netloc = query = fragment = None
    colon = url.find(":")
    if colon > 0 and url[0].isascii() and url[0].isalpha() and all(char in _SCHEME_CHARS for char in url[:colon]):
        scheme, url = url[:colon].lower(), url[colon + 1 :]
    if url[:2] == "//":
        delimiter = min([len(url), *(offset for char in "/?#" if (offset := url.find(char, 2)) >= 0)])
        netloc, url = url[2:delimiter], url[delimiter:]
        if ("[" in netloc) != ("]" in netloc):
            message = "Invalid IPv6 URL"
            raise ValueError(message)
    if "#" in url:
        url, fragment = url.split("#", 1)
    if "?" in url:
        url, query = url.split("?", 1)
    return scheme, netloc, url, query, fragment


def _unsplit(scheme: str | None, netloc: str | None, path: str, query: str | None, fragment: str | None) -> str:
    if netloc is not None:
        if path and path[:1] != "/":
            path = "/" + path
        path = "//" + netloc + path
    prefix = f"{scheme}:" if scheme else ""
    suffix = ("?" + query if query is not None else "") + ("#" + fragment if fragment is not None else "")
    return f"{prefix}{path}{suffix}"


def _merge(bpath: str, path: str) -> str:
    """The RFC 3986 5.2.3 merge and 5.2.4 dot removal, in urljoin's segment-list form; ``path`` is never empty here."""
    base_parts = bpath.split("/")
    if base_parts[-1]:
        del base_parts[-1]  # the base's last segment is a file, so the reference replaces it
    if path[:1] == "/":
        segments = path.split("/")
    else:
        segments = base_parts + path.split("/")
        segments[1:-1] = filter(None, segments[1:-1])  # drop the empty interior segments a splice would rejoin
    resolved: list[str] = []
    for segment in segments:
        if segment == "..":
            if resolved:
                resolved.pop()
        elif segment != ".":
            resolved.append(segment)
    if segments[-1] in {".", ".."}:
        resolved.append("")
    return "/".join(resolved) or "/"


def _reference_join(base: str, url: str) -> str:
    """CPython 3.14's ``urljoin`` frozen against a ``str`` pair, the version-stable oracle the corpus differs from."""
    if not base or not url:
        return url or base
    bscheme, bnetloc, bpath, bquery, bfragment = _split5(base)
    scheme, netloc, path, query, fragment = _split5(url)
    if scheme is None:
        scheme = bscheme
    if scheme != bscheme or (scheme and scheme not in _USES_RELATIVE):
        return url  # a foreign or opaque scheme leaves the reference verbatim
    if netloc:
        return _unsplit(scheme, netloc, path, query, fragment)
    if not path:
        if query is None:
            query = bquery
            if fragment is None:
                fragment = bfragment
        return _unsplit(scheme, bnetloc, bpath, query, fragment)
    return _unsplit(scheme, bnetloc, _merge(bpath, path), query, fragment)


_BASES = (
    "http://a/b/c/d;p?q",
    "http://a/b/c/d",
    "http://a/b/c/",
    "http://a/b",
    "http://a/",
    "http://a",
    "https://h.com/dir/page.html?x=1#frag",
    "http://user:pass@h:8080/p/q?a=b#c",
    "http://a//b//c",
    "https://ex.com",
    "http://[::1]/p",
    "ftp://f/x/y",
    "file:///a/b",
    "//host/a",
    "http:opaque",
    "mailto:x@y",
    "tel:123",
    "HTTP://Up.Case/Dir/",
    "",
)
_REFERENCES = (
    "g",
    "./g",
    "g/",
    "/g",
    "//g",
    "?y",
    "g?y",
    "#s",
    "g#s",
    "g?y#s",
    ";x",
    "g;x",
    "",
    ".",
    "./",
    "..",
    "../",
    "../g",
    "../..",
    "../../",
    "../../g",
    "../../../../g",
    "/./g",
    "/../g",
    "g.",
    ".g",
    "g..",
    "..g",
    "./../g",
    "./g/.",
    "g/./h",
    "g/../h",
    "g;x=1/./y",
    "g;x=1/../y",
    "g?y/./x",
    "g#s/../x",
    "http:g",
    "http://other/x",
    "mailto:a@b",
    "javascript:void(0)",
    "//h2/p",
    "?",
    "#",
    "a//b",
    "c//d",
    "/a//b",
    "a/b/",
    "../a//b",
    "x/..",
    "x/../",
    "x/../y",
    " ",
    "   ",
    "a/b/c/../../d",
    "//",
    "///x",
    "/a/b/../../../c",
    "?q=1&r=2",
    "#a#b",
    "/",
    ".//g",
    "a\tb",
    "a\nb\rc",
    "~a:b",
    "_a:b",
    "1a:b",
    "@a:b",
)


def _corpus() -> list[tuple[str, str]]:
    pairs = [(base, reference) for base in _BASES for reference in _REFERENCES]
    alphabet = "abc/.?#=&%:@;+"
    generator = random.Random(20260704)  # ruff:ignore[suspicious-non-cryptographic-random-usage]  # fuzz corpus, not for security
    for _ in range(3000):
        reference = "".join(generator.choice(alphabet) for _ in range(generator.randint(0, 10)))
        pairs.append((generator.choice(_BASES), reference))
    pairs += [("http://[bad", "g"), ("http://a/b", "//[bad")]  # both sides must agree the split fails
    return pairs


def _join(base: str, reference: str) -> tuple[str | None, bool]:
    """The joined URL, or ``(None, True)`` when the join raised, so the oracle and C agree on the failure too."""
    try:
        return _url_join(base, reference), False
    except ValueError:
        return None, True


def _oracle(base: str, reference: str) -> tuple[str | None, bool]:
    try:
        return _reference_join(base, reference), False
    except ValueError:
        return None, True


def test_url_join_matches_reference_over_corpus() -> None:
    for base, reference in _corpus():
        assert _join(base, reference) == _oracle(base, reference), (base, reference)


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        pytest.param("g:h", "g:h", id="different-scheme-is-verbatim"),
        pytest.param("g", "http://a/b/c/g", id="relative-segment"),
        pytest.param("./g", "http://a/b/c/g", id="same-directory"),
        pytest.param("g/", "http://a/b/c/g/", id="trailing-slash"),
        pytest.param("/g", "http://a/g", id="absolute-path"),
        pytest.param("//g", "http://g", id="scheme-relative"),
        pytest.param("?y", "http://a/b/c/d;p?y", id="query-only"),
        pytest.param("g?y", "http://a/b/c/g?y", id="segment-with-query"),
        pytest.param("#s", "http://a/b/c/d;p?q#s", id="fragment-only"),
        pytest.param("g#s", "http://a/b/c/g#s", id="segment-with-fragment"),
        pytest.param(";x", "http://a/b/c/;x", id="params-segment"),
        pytest.param(".", "http://a/b/c/", id="dot"),
        pytest.param("./", "http://a/b/c/", id="dot-slash"),
        pytest.param("..", "http://a/b/", id="dot-dot"),
        pytest.param("../", "http://a/b/", id="dot-dot-slash"),
        pytest.param("../g", "http://a/b/g", id="parent-segment"),
        pytest.param("../..", "http://a/", id="grandparent"),
        pytest.param("../../g", "http://a/g", id="grandparent-segment"),
        pytest.param("../../../../g", "http://a/g", id="over-popped-clamps-to-root"),
        pytest.param("/./g", "http://a/g", id="absolute-with-dot"),
        pytest.param("/../g", "http://a/g", id="absolute-with-dot-dot"),
        pytest.param("g.", "http://a/b/c/g.", id="trailing-dot-in-name"),
        pytest.param(".g", "http://a/b/c/.g", id="leading-dot-in-name"),
        pytest.param("g/./h", "http://a/b/c/g/h", id="interior-dot"),
        pytest.param("g/../h", "http://a/b/c/h", id="interior-dot-dot"),
        pytest.param("g;x=1/../y", "http://a/b/c/y", id="params-then-parent"),
        pytest.param("g?y/./x", "http://a/b/c/g?y/./x", id="dots-inside-query-are-literal"),
        pytest.param("g#s/../x", "http://a/b/c/g#s/../x", id="dots-inside-fragment-are-literal"),
        pytest.param("http:g", "http://a/b/c/g", id="same-scheme-prefix-is-relative"),
        pytest.param("", "http://a/b/c/d;p?q", id="empty-reference-keeps-base"),
    ],
)
def test_url_join_resolves_rfc3986_examples(reference: str, expected: str) -> None:
    assert _url_join("http://a/b/c/d;p?q", reference) == expected


@pytest.mark.parametrize(
    ("base", "reference", "expected"),
    [
        pytest.param("", "g/h", "g/h", id="empty-base-returns-reference"),
        pytest.param("http://a/b", "", "http://a/b", id="empty-reference-returns-base"),
        pytest.param("http://a/b?x#y", "   ", "http://a/b?x#y", id="blank-reference-keeps-base-query-and-fragment"),
        pytest.param("//host/a/b", "c", "//host/a/c", id="scheme-less-base-keeps-authority"),
        pytest.param("http:opaque", "rel", "http:rel", id="rootless-base-path-merges-without-authority"),
        pytest.param("http://h", "g", "http://h/g", id="empty-base-path-roots-the-reference"),
        pytest.param("http://a/b/c/d", "//h2/p/q", "http://h2/p/q", id="scheme-relative-replaces-authority"),
    ],
)
def test_url_join_edge_operands(base: str, reference: str, expected: str) -> None:
    assert _url_join(base, reference) == expected


@pytest.mark.parametrize(
    ("base", "reference"),
    [
        pytest.param("mailto:x@y", "g", id="opaque-base-scheme"),
        pytest.param("http://a/b", "ftp://x/y", id="foreign-scheme"),
        pytest.param("tel:1", "tel:2", id="same-opaque-scheme"),
        pytest.param("http://a/b", "mailto:a@b", id="mailto-reference"),
    ],
)
def test_url_join_returns_reference_verbatim(base: str, reference: str) -> None:
    assert _url_join(base, reference) == reference


def test_url_join_rejects_non_str_operand() -> None:
    with pytest.raises(TypeError):
        _url_join(b"http://a", "g")  # ty: ignore[invalid-argument-type]  # both operands must be str


@pytest.mark.parametrize(
    ("base", "reference"),
    [
        pytest.param("http://[::1/x", "g", id="base-open-bracket-only"),
        pytest.param("http://a]/x", "g", id="base-close-bracket-only"),
        pytest.param("http://a/b", "//[bad", id="reference-open-bracket-only"),
        pytest.param("http://a/b", "//bad]", id="reference-close-bracket-only"),
    ],
)
def test_url_join_rejects_unbalanced_brackets(base: str, reference: str) -> None:
    with pytest.raises(ValueError, match="Invalid IPv6 URL"):
        _url_join(base, reference)


@pytest.mark.parametrize(
    ("base", "reference", "expected"),
    [
        pytest.param("http://a/", "g\th", "http://a/gh", id="reference-drops-tab"),
        pytest.param("http://a/", "g\nh\rj", "http://a/ghj", id="reference-drops-newline-and-return"),
        pytest.param("\x01\x02http://a/x", "g", "http://a/g", id="base-strips-leading-control"),
        pytest.param("  http://a/x  ", "g", "http://a/g", id="base-strips-leading-space"),
    ],
)
def test_url_join_preprocesses_operands(base: str, reference: str, expected: str) -> None:
    assert _url_join(base, reference) == expected


_LANGUAGE_PARAMS = frozenset({"lang", "language"})
_ISO = frozenset({"de", "en", "fr", "es", "ru", "nl"})
_SEGMENT = re.compile(r"([a-z]{2})(?:[-_][a-z]{2,3})?$")


def _language_oracle(query: str, path: str, hostname: str, language: str, *, strict: bool) -> bool:
    for pair in query.split("&"):
        key, separator, value = pair.partition("=")
        if separator and _url_percent_decode(key).lower() in _LANGUAGE_PARAMS:
            code = _url_percent_decode(value).lower()
            if code and not code.startswith(language):
                return False
    leading = next((segment for segment in path.lower().split("/") if segment), "")
    if (match := _SEGMENT.fullmatch(leading)) and match[1] in _ISO and match[1] != language:
        return False
    if strict:
        label = hostname.partition(".")[0]
        if len(label) == 2 and label in _ISO and label != language:
            return False
    return True


_PATHS = (
    "",
    "/",
    "/de/beitrag",
    "/en-us/page",
    "/en_us/page",
    "/fr-fra/x",
    "/de",
    "//de//x",
    "de/x",
    "/DE/UPPER",
    "/e1/x",
    "/abc/x",
    "/abcd/x",
    "/en-u1/x",
    "/en-u/x",
    "/xy-ab/x",
    "/ru/y",
    "/zz/y",
    "/en-abcd/x",
    "/%64%65/x",
    "/enxab/x",
    "/z{/x",
)
_QUERIES = (
    "",
    "x=1",
    "lang=de",
    "language=fr",
    "lang=en-US",
    "lang=",
    "lang=de&language=en",
    "LANG=fr",
    "%6c%61%6e%67=de",
)
_HOSTS = ("example.org", "de.example.org", "en.example.org", "zz.example.org", "e.org", "fr.a.b", "de", "localhost")
_LANGS = ("de", "en", "fr")


def test_language_matches_matches_oracle_over_corpus() -> None:
    for path, query, host, lang, strict in itertools.product(_PATHS, _QUERIES, _HOSTS, _LANGS, (False, True)):
        got = _url_language_matches(query, path, host, lang, strict, _LANGUAGE_PARAMS, _ISO)
        want = _language_oracle(query, path, host, lang, strict=strict)
        assert got is want, (query, path, host, lang, strict)


@pytest.mark.parametrize(
    ("markers", "strict", "expected"),
    [
        pytest.param(("", "/de/x", "h.org", "en"), False, False, id="path-segment-other-language"),
        pytest.param(("", "/en/x", "h.org", "en"), False, True, id="path-segment-same-language"),
        pytest.param(("", "/en-us/x", "h.org", "en"), False, True, id="path-subtag-two-letters"),
        pytest.param(("", "/fr-fra/x", "h.org", "en"), False, False, id="path-subtag-three-letters"),
        pytest.param(("", "/abc/x", "h.org", "en"), False, True, id="path-three-letters-not-marker"),
        pytest.param(("", "/en-u1/x", "h.org", "en"), False, True, id="path-subtag-not-letters"),
        pytest.param(("", "/e1/x", "h.org", "en"), False, True, id="path-second-char-not-letter"),
        pytest.param(("lang=de", "/x", "h.org", "en"), False, False, id="query-lang-other-language"),
        pytest.param(("lang=en-gb", "/x", "h.org", "en"), False, True, id="query-lang-prefix-matches"),
        pytest.param(("lang=", "/x", "h.org", "en"), False, True, id="query-lang-empty-value"),
        pytest.param(("", "/x", "de.example.org", "en"), True, False, id="strict-host-other-language"),
        pytest.param(("", "/x", "de.example.org", "en"), False, True, id="non-strict-ignores-host"),
        pytest.param(("", "/x", "en.example.org", "en"), True, True, id="strict-host-same-language"),
        pytest.param(("", "/x", "abc.example.org", "en"), True, True, id="strict-host-label-not-two-letters"),
        pytest.param(("", "/x", "de", "en"), True, False, id="strict-dotless-host-label"),
        pytest.param(("", "/enxab/x", "h.org", "en"), False, True, id="path-five-chars-no-separator"),
    ],
)
def test_language_matches_cases(markers: tuple[str, str, str, str], *, strict: bool, expected: bool) -> None:
    query, path, hostname, language = markers
    assert _url_language_matches(query, path, hostname, language, strict, _LANGUAGE_PARAMS, _ISO) is expected


def test_language_matches_rejects_missing_arguments() -> None:
    lax = False
    with pytest.raises(TypeError):
        _url_language_matches("", "/x", "h.org", "en", lax)  # ty: ignore[missing-argument]


_QUERY_SET = 1
_CONTENT = frozenset({"id", "page", "post", "article_id"})
_LANGUAGE = frozenset({"lang", "language"})


def _query_oracle(query: str, allow: frozenset[str] | None, deny: frozenset[str], *, strict: bool) -> str:
    kept: list[tuple[str, str]] = []
    for pair in query.split("&"):
        if not pair:
            continue
        key = _url_percent_decode(pair.partition("=")[0]).lower()
        if key in deny:
            continue
        if allow is not None:
            dropped = key not in allow
        elif strict:
            dropped = key not in _CONTENT and key not in _LANGUAGE
        else:
            dropped = _url_is_tracker(key)
        if dropped:
            continue
        try:
            encoded = _url_percent_encode(pair, _QUERY_SET)
        except UnicodeEncodeError as exc:
            # the retired shim converted the encoder's failure to the public ValueError; the C does the same inline
            msg = f"URL component {pair!r} has a character that cannot be percent-encoded: {exc.reason}"
            raise ValueError(msg) from exc
        kept.append((key, encoded))
    return "&".join(pair for _key, pair in sorted(kept))


def _outcome(call: Callable[[], str]) -> tuple[object, ...]:
    try:
        return ("ok", call())
    except ValueError as exc:  # UnicodeEncodeError subclasses ValueError, so a lone-surrogate failure lands here too
        return ("err", type(exc).__name__, str(exc))


_NORMALIZED_QUERIES = (
    "",
    "a=1",
    "ok=1&bad=\udcff",  # a lone surrogate cannot percent-encode, so both sides must fail identically
    "a=1&b=2",
    "b=2&a=1",
    "id=1&utm_source=x",
    "utm_campaign=z&fbclid=y&keep=1",
    "a=1&a=2&a=3",
    "=empty&x=",
    "café=1&CAFÉ=2",
    "%41=%42&ref=1",
    "lang=de&page=3",
    "session=1&content=2",
    "aff_id=1&article_id=9",
    "x=%ZZ&y=1",
    "a=1&&b=2&",
    "utm_medium=e&Id=7",
    "A=1&a=2&B=3",
    "a&b&c",
)
_ALLOWS = (None, frozenset({"id", "page"}), frozenset({"UTM_SOURCE"}), frozenset())
_DENYS = (frozenset(), frozenset({"id"}), frozenset({"a", "b"}))


def test_normalize_query_matches_oracle_over_corpus() -> None:
    for query, allow, deny, strict in itertools.product(_NORMALIZED_QUERIES, _ALLOWS, _DENYS, (False, True)):
        if strict and allow is not None:
            continue
        got = _outcome(partial(_url_normalize_query, query, allow, deny, strict, _CONTENT, _LANGUAGE))
        want = _outcome(partial(_query_oracle, query, allow, deny, strict=strict))
        assert got == want, (query, allow, deny, strict)


@pytest.mark.parametrize(
    ("query", "allow", "deny", "strict", "expected"),
    [
        pytest.param("b=2&a=1", None, frozenset(), False, "a=1&b=2", id="sort-non-trackers"),
        pytest.param("utm_source=x&id=1", None, frozenset(), False, "id=1", id="drop-tracker"),
        pytest.param("id=1&page=2&x=3", None, frozenset(), True, "id=1&page=2", id="strict-keeps-content"),
        pytest.param("lang=de&x=3", None, frozenset(), True, "lang=de", id="strict-keeps-language"),
        pytest.param("id=1&page=2", frozenset({"id"}), frozenset(), False, "id=1", id="allow-keeps-only-listed"),
        pytest.param("id=1&keep=2", None, frozenset({"id"}), False, "keep=2", id="deny-drops-listed"),
        pytest.param("Id=1&PAGE=2", None, frozenset(), True, "Id=1&PAGE=2", id="key-lowercased-for-filter"),
        pytest.param("", None, frozenset(), False, "", id="empty-query"),
        pytest.param("a=1&&b=2&", None, frozenset(), False, "a=1&b=2", id="empty-pairs-dropped"),
    ],
)
def test_normalize_query_cases(
    query: str, allow: frozenset[str] | None, deny: frozenset[str], expected: str, *, strict: bool
) -> None:
    assert _url_normalize_query(query, allow, deny, strict, _CONTENT, _LANGUAGE) == expected


def test_normalize_query_surrogate_key_raises() -> None:
    # a lone-surrogate key has no UTF-8 form, so the tracker test cannot classify it and the error propagates
    lax = False
    with pytest.raises(UnicodeEncodeError):
        _url_normalize_query("\ud800=1", None, frozenset(), lax, _CONTENT, _LANGUAGE)


def test_normalize_query_surrogate_value_raises() -> None:
    # the key survives, then encoding the pair fails on the surrogate value, rewrapped as the shim's ValueError
    lax = False
    with pytest.raises(ValueError, match="cannot be percent-encoded"):
        _url_normalize_query("ok=\ud800", None, frozenset(), lax, _CONTENT, _LANGUAGE)


def test_normalize_query_rejects_non_str_query() -> None:
    lax = False
    with pytest.raises(TypeError):
        _url_normalize_query(123, None, frozenset(), lax, _CONTENT, _LANGUAGE)  # ty: ignore[invalid-argument-type]


def test_normalize_query_rejects_missing_arguments() -> None:
    lax = False
    with pytest.raises(TypeError):
        _url_normalize_query("a=1", None, frozenset(), lax)  # ty: ignore[missing-argument]


_PATH, _QUERY, _FRAGMENT = 0, 1, 2

# The safe run each set keeps raw, mirroring URL_PATH_KEEP / URL_QUERY_KEEP / URL_FRAGMENT_KEEP in _c/url/url.c; the
# unreserved characters quote() always keeps are implicit, so only the set-specific additions are listed.
_SAFE: dict[int, str] = {
    _PATH: "!$%&'()*+,-./:;=@[\\]_|~",
    _QUERY: "!$%&()*+,-./:;=?@[\\]^`{|}~",
    _FRAGMENT: "!#$%&'()*+,-./:;=?@[\\]^_{|}~",
}
_ESCAPE = re.compile(r"%[0-9a-fA-F]{2}")


def _oracle_encode(text: str, url_set: int) -> str:
    uppercased = _ESCAPE.sub(lambda match: match[0].upper(), text) if "%" in text else text
    return quote(uppercased, safe=_SAFE[url_set])


# A spread that reaches every encode/decode branch: kept and out-of-set bytes, lowercase and already-uppercase escapes,
# both malformed-escape shapes, a truncated escape at the very end, a '%' before a non-ASCII byte, and multi-byte input.
_CORPUS = (
    "",
    "a",
    "abc-._~",
    " ",
    "a b/c",
    '<>"`',
    "%2f",
    "%2F",
    "%c3%a9",
    "abc%2edef",
    "%2g",
    "%g0",
    "%",
    "a%",
    "%2",
    "100%done",
    "a%%41b",
    "é",
    "%é",
    "münchen",
    "日本語",
    "\U0001f600",
    "!$&'()*+,;=:@[]^|{}?#`~",
    "a=1&b=2",
    "\x00\x01\x1f",
)


@pytest.mark.parametrize(
    "url_set",
    [pytest.param(_PATH, id="path"), pytest.param(_QUERY, id="query"), pytest.param(_FRAGMENT, id="fragment")],
)
def test_percent_encode_matches_urllib_over_corpus(url_set: int) -> None:
    for text in _CORPUS:
        assert _url_percent_encode(text, url_set) == _oracle_encode(text, url_set), text


def test_percent_decode_matches_urllib_over_corpus() -> None:
    for text in _CORPUS:
        assert _url_percent_decode(text) == unquote(text), text


@pytest.mark.parametrize(
    ("text", "url_set", "expected"),
    [
        pytest.param("", _PATH, "", id="empty"),
        pytest.param("abc", _PATH, "abc", id="all-kept"),
        pytest.param("a b", _PATH, "a%20b", id="space-encoded"),
        pytest.param("%2f", _PATH, "%2F", id="lowercase-escape-uppercased"),
        pytest.param("%2F", _PATH, "%2F", id="uppercase-escape-kept"),
        pytest.param("%2g", _PATH, "%2g", id="second-non-hex-is-literal"),
        pytest.param("%g0", _PATH, "%g0", id="first-non-hex-is-literal"),
        pytest.param("a%", _PATH, "a%", id="trailing-percent-no-room"),
        pytest.param("%2", _PATH, "%2", id="truncated-escape"),
        pytest.param("é", _PATH, "%C3%A9", id="non-ascii-utf8"),
        pytest.param("%é", _PATH, "%%C3%A9", id="percent-before-non-ascii"),
        pytest.param("a'b", _QUERY, "a%27b", id="query-drops-apostrophe"),
        pytest.param("a'b", _FRAGMENT, "a'b", id="fragment-keeps-apostrophe"),
        pytest.param("a`b", _QUERY, "a`b", id="query-keeps-backtick"),
        pytest.param("a`b", _FRAGMENT, "a%60b", id="fragment-drops-backtick"),
    ],
)
def test_percent_encode_cases(text: str, url_set: int, expected: str) -> None:
    assert _url_percent_encode(text, url_set) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("", "", id="empty"),
        pytest.param("abc", "abc", id="no-escape"),
        pytest.param("%2F", "/", id="valid-escape"),
        pytest.param("%2f", "/", id="lowercase-escape"),
        pytest.param("%c3%a9", "é", id="utf8-two-byte"),
        pytest.param("%zz", "%zz", id="invalid-escape-literal"),
        pytest.param("%g0", "%g0", id="first-non-hex-literal"),
        pytest.param("a%", "a%", id="trailing-percent"),
        pytest.param("%2", "%2", id="truncated-escape"),
        pytest.param("%80", "�", id="lone-continuation-replaced"),
        pytest.param("é%41", "éA", id="non-ascii-then-escape"),
        pytest.param("%41é", "Aé", id="escape-then-non-ascii"),
        pytest.param("%é", "%é", id="percent-before-non-ascii"),
    ],
)
def test_percent_decode_cases(text: str, expected: str) -> None:
    assert _url_percent_decode(text) == expected


def test_percent_encode_lone_surrogate_raises() -> None:
    # a lone surrogate has no UTF-8 form, so it cannot be percent-encoded; the shim rewraps this as ValueError
    with pytest.raises(UnicodeEncodeError):
        _url_percent_encode("a\udc00b", _PATH)


def test_percent_decode_lone_surrogate_passes_through() -> None:
    # unquote leaves a raw non-ASCII code point untouched, splitting the ASCII runs around it, and so does this coder
    assert _url_percent_decode("a\udc00%41b") == "a\udc00Ab"


def test_percent_encode_rejects_non_str_text() -> None:
    with pytest.raises(TypeError):
        _url_percent_encode(123, _PATH)  # ty: ignore[invalid-argument-type]  # text must be a str


_C0_AND_SPACE = "".join(map(chr, range(0x21)))
_MARKUP_DELIMITER = re.compile(r'[<>"]')


# CPython's str whitespace (Py_UNICODE_ISSPACE), pinned because PyPy's str.split() disagrees on the C0 separators
# 0x1C-0x1F, and the C scrub follows CPython's predicate on every interpreter
_PY_WHITESPACE = frozenset(
    map(
        chr,
        (
            *range(0x09, 0x0E),
            *range(0x1C, 0x21),
            0x85,
            0xA0,
            0x1680,
            *range(0x2000, 0x200B),
            0x2028,
            0x2029,
            0x202F,
            0x205F,
            0x3000,
        ),
    )
)


def _oracle_scrub(url: str) -> str:
    remainder = "".join(char for char in url.strip(_C0_AND_SPACE) if char not in _PY_WHITESPACE)
    if remainder.startswith("<![CDATA["):
        remainder = remainder.removeprefix("<![CDATA[").removesuffix("]]>")
    remainder = _MARKUP_DELIMITER.split(remainder, maxsplit=1)[0].replace("&amp;", "&")
    trimmed: Final = remainder.rstrip(_C0_AND_SPACE)
    if trimmed != remainder:
        remainder = trimmed
        if remainder.endswith("/&?#"):
            remainder = remainder[:-2]
        elif remainder.endswith(("/&?", "/&#")):
            remainder = remainder[:-1]
    return remainder.removesuffix("&") if remainder.endswith("/&") else remainder


# Pieces that reach every scrub branch: whitespace str.split() removes but the edge C0 strip alone would not (NBSP, NEL,
# a separator), a non-whitespace C0 control that survives in the middle, and the CDATA, markup, and escape shapes.
_PIECES = (
    "https://x.example/p",
    " ",
    "\t",
    "\n",
    "\xa0",
    "\x85",
    "\x1c",
    "\x01",
    "<![CDATA[",
    "]]>",
    "<",
    ">",
    '"',
    "&amp;",
    "/&",
    "&",
    "a",
    "//double",
    "?q=1",
    "#f",
)
_EDGE_CASES = (
    "",
    " ",
    "  https://x.example/p  ",
    "\t\nhttps://x.example/p\r\n",
    "\x01\x02https://x.example/p\x03",
    "a\xa0b\x85c",
    "a\x1cb\x1fc",
    "line break",
    "mid\x01control",
    "<![CDATA[https://x.example/p]]>",
    "<![CDATA[https://x.example/p",
    "<![CDATA[a>b",
    "<![CDATA[]]>",
    "<![CDAT",
    "https://x.example/a<script>",
    "https://x.example/a>b",
    'https://x.example/p"junk',
    "https://x.example/a&amp;b&amp;c",
    "https://x.example/path/&amp;",
    "https://x.example/path/&",
    "https://x.example/&amp;",
    "https://x.example/&\x01>",
    "https://x.example/&?\x01>",
    "https://x.example/&#\x01>",
    "https://x.example/&?#\x01>",
    "trailing&",
)


def _scrub_corpus() -> list[str]:
    seen = set(_EDGE_CASES)
    for length in range(4):
        seen.update("".join(combo) for combo in itertools.product(_PIECES, repeat=length))
    return sorted(seen)


def test_scrub_matches_oracle_over_corpus() -> None:
    for url in _scrub_corpus():
        assert _url_scrub(url) == _oracle_scrub(url), repr(url)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param("", "", id="empty"),
        pytest.param("  https://x/p  ", "https://x/p", id="edge-space-stripped"),
        pytest.param("\x01https://x/p\x02", "https://x/p", id="edge-c0-stripped"),
        pytest.param("mid\x01control", "mid\x01control", id="mid-non-ws-c0-kept"),
        pytest.param("a\xa0b", "ab", id="nbsp-whitespace-removed"),
        pytest.param("a b\tc\nd", "abcd", id="internal-whitespace-removed"),
        pytest.param("<![CDATA[https://x/p]]>", "https://x/p", id="cdata-unwrapped"),
        pytest.param("<![CDATA[https://x/p", "https://x/p", id="cdata-open-only"),
        pytest.param("<![CDATA[ab", "ab", id="cdata-content-too-short-for-suffix"),
        pytest.param("<![CDATA[a]x]", "a]x]", id="cdata-suffix-second-char-mismatch"),
        pytest.param("<![CDATA[a]]x", "a]]x", id="cdata-suffix-third-char-mismatch"),
        pytest.param("<![CDAT", "", id="cdata-too-short-truncated-at-lt"),
        pytest.param("https://x/a<b", "https://x/a", id="truncate-at-lt"),
        pytest.param("https://x/a>b", "https://x/a", id="truncate-at-gt"),
        pytest.param('https://x/a"b', "https://x/a", id="truncate-at-quote"),
        pytest.param("https://x/a&amp;b", "https://x/a&b", id="amp-unescaped"),
        pytest.param("https://x/&amp;a&amp;b", "https://x/&a&b", id="amp-unescaped-multiple"),
        pytest.param("https://x/p/&amp;", "https://x/p/", id="trailing-slash-amp-dropped"),
        pytest.param("keep&", "keep&", id="trailing-amp-not-after-slash-kept"),
        pytest.param("a&xzzzz", "a&xzzzz", id="amp-first-char-mismatch"),
        pytest.param("a&abbbb", "a&abbbb", id="amp-second-char-mismatch"),
        pytest.param("a&amxxx", "a&amxxx", id="amp-third-char-mismatch"),
        pytest.param("a&ampxy", "a&ampxy", id="amp-fourth-char-mismatch"),
    ],
)
def test_scrub_cases(url: str, expected: str) -> None:
    assert _url_scrub(url) == expected


def test_scrub_rejects_non_str() -> None:
    with pytest.raises(TypeError, match="must be str"):
        _url_scrub(123)  # ty: ignore[invalid-argument-type]  # a non-str exercises the TypeError guard


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
_SPLIT_PATHS = ("", "/", "/a/b", "/a/../b")
_SPLIT_QUERIES = ("", "?a=1", "?a=1&b=2")
_FRAGMENTS = ("", "#f")


def _split_corpus() -> list[str]:
    urls = [
        f"{scheme}:" * bool(scheme) + (f"//{auth}" * bool(auth or path.startswith("//"))) + path + query + fragment
        for scheme in _SCHEMES
        for auth in _AUTHORITIES
        for path in _SPLIT_PATHS
        for query in _SPLIT_QUERIES
        for fragment in _FRAGMENTS
    ]
    urls += ["  http://x.com/y  ", "http://h\tost/p", "\x01\x02http://a.com", "HTTP://X", "//netonly/p", "a\nb\rc://x"]
    urls += [f"http://h.com/x{query}{fragment}" for query in ("", "?", "?a=1") for fragment in ("", "#", "#a=1")]
    return sorted(set(urls))


def test_url_split_matches_urllib_after_input_trim() -> None:
    for url in _split_corpus():
        reference = urlsplit(url.rstrip(_C0_OR_SPACE))
        if reference.scheme in {"ftp", "http", "https", "ws", "wss"} and not reference.netloc:
            continue
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


_TRACKER_NAMES = frozenset({
    "clickid",
    "dclid",
    "efid",
    "epik",
    "fb_ref",
    "fb_source",
    "fbclid",
    "gbraid",
    "gclid",
    "gclsrc",
    "igsh",
    "igshid",
    "mkt_tok",
    "msclkid",
    "partnerid",
    "s_cid",
    "sc_cid",
    "ttclid",
    "twclid",
    "wbraid",
    "wickedid",
    "yclid",
    "ysclid",
})
_TRACKER_PREFIXES = ("ad_", "ads_", "ga_", "gs_", "hsa_", "itm_", "mc_", "mtm_", "oly_", "pk_", "utm_", "vero_")
_TRACKER_WORDS = re.compile(
    r"(?:^|_)(?:aff(?:i(?:liate)?)?|campaign|cl?id|keyword|kwd|medium|refer(?:r?er)?|ref|session|source|uid|xtor)(?:_|$)"
)


def _tracker_oracle(key: str) -> bool:
    return (
        key in _TRACKER_NAMES
        or key.startswith(_TRACKER_PREFIXES)
        or key.endswith("clid")
        or _TRACKER_WORDS.search(key) is not None
    )


def _tracker_corpus() -> list[str]:
    seen = (
        set(_TRACKER_NAMES)
        | set(_TRACKER_PREFIXES)
        | {
            "",
            "id",
            "page",
            "myclid",
            "reference",
            "x_ref_y",
            "campaign",
            "affiliate",
            "xtor",
            "referer",
            "referrer",
            "utm_source",
            "ad_id",
            "a_uid_b",
            "keyword",
            "kwd",
            "medium",
            "session",
            "cid",
            "clid",
            "not_a_ref",
        }
    )
    fragments = ("utm", "ref", "id", "clid", "aff", "source", "_", "campaign", "uid")
    for length in range(4):
        seen.update("".join(combo) for combo in itertools.product(fragments, repeat=length))
    return sorted(seen)


def test_is_tracker_matches_oracle_over_corpus() -> None:
    for key in _tracker_corpus():
        assert _url_is_tracker(key) == _tracker_oracle(key), repr(key)


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        pytest.param("gclid", True, id="exact-name"),
        pytest.param("utm_source", True, id="known-prefix"),
        pytest.param("myclid", True, id="clid-suffix-not-exact"),
        pytest.param("x_ref_y", True, id="tracking-word-between-underscores"),
        pytest.param("campaign", True, id="tracking-word-whole"),
        pytest.param("reference", False, id="longer-word-not-matched"),
        pytest.param("id", False, id="content-name"),
        pytest.param("", False, id="empty"),
    ],
)
def test_is_tracker_cases(key: str, *, expected: bool) -> None:
    assert _url_is_tracker(key) is expected


def test_is_tracker_rejects_non_str() -> None:
    with pytest.raises(TypeError, match="must be str"):
        _url_is_tracker(123)  # ty: ignore[invalid-argument-type]  # a non-str exercises the TypeError guard


def test_is_tracker_lone_surrogate_raises() -> None:
    # a lone-surrogate key has no UTF-8 form, so it cannot be classified and the UnicodeEncodeError propagates
    with pytest.raises(UnicodeEncodeError):
        _url_is_tracker("\ud800clid")


def _oracle_variant(url: str) -> str:
    remainder = url.partition("://")[2]
    return remainder if "?" in remainder or "#" in remainder else remainder.rstrip("/")


_VARIANT_SCHEMES = ("https://", "http://", "ftp://", "", "://", "a://b://")
_TAILS = (
    "",
    "/",
    "//",
    "x.example/p",
    "x.example/p/",
    "x.example/p//",
    "x.example/p?q=1",
    "x.example/p/#f",
    "x.example/a/b/",
    "x.example/?only",
    "x.example/#only",
    "mailto:x@y",
    "a:/b/c/",
)


def _variant_corpus() -> list[str]:
    combined = {"".join(combo) for combo in itertools.product(_VARIANT_SCHEMES, _TAILS)}
    return sorted(combined | {"", "no-scheme-path/", "://leading/", "a://b://c/d/"})


def test_variant_key_matches_oracle_over_corpus() -> None:
    for url in _variant_corpus():
        assert _url_variant_key(url) == _oracle_variant(url), repr(url)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param("", "", id="empty"),
        pytest.param("no-scheme-path/", "", id="no-scheme-empty-key"),
        pytest.param("https://x/p/", "x/p", id="trailing-slash-trimmed"),
        pytest.param("https://x/p///", "x/p", id="trailing-slashes-trimmed"),
        pytest.param("https://x/p?q=1/", "x/p?q=1/", id="query-keeps-slash"),
        pytest.param("https://x/p/#f", "x/p/#f", id="fragment-keeps-slash"),
        pytest.param("https://x/", "x", id="root-slash-trimmed"),
        pytest.param("a://b://c/", "b://c", id="first-marker-wins"),
        pytest.param("mailto:x@y", "", id="colon-not-followed-by-slash"),
        pytest.param("a:/b/c/", "", id="colon-single-slash-no-marker"),
    ],
)
def test_variant_key_cases(url: str, expected: str) -> None:
    assert _url_variant_key(url) == expected


def test_variant_key_rejects_non_str() -> None:
    with pytest.raises(TypeError, match="must be str"):
        _url_variant_key(123)  # ty: ignore[invalid-argument-type]  # a non-str exercises the TypeError guard


@pytest.mark.parametrize(
    ("url", "host", "path"),
    [
        pytest.param("http://evil.example\\@good.example/", "evil.example", "\\@good.example/", id="http-userinfo"),
        pytest.param("https://evil\\@good/", "evil", "\\@good/", id="https-userinfo"),
        pytest.param("ws://evil\\@good/", "evil", "\\@good/", id="ws-userinfo"),
        pytest.param("ftp://evil\\@good/", "evil", "\\@good/", id="ftp-userinfo"),
        pytest.param("file://evil\\@good/", "evil", "\\@good/", id="file-userinfo"),
        pytest.param("http:\\\\host\\path", "host", "\\path", id="backslash-authority-opener"),
        pytest.param("http:/\\host/p", "host", "/p", id="mixed-slash-opener"),
    ],
)
def test_url_split_ends_special_authority_at_backslash(url: str, host: str, path: str) -> None:
    # a browser ends a special-scheme authority at '\', so the host is the span before it, not the userinfo trick's
    # tail; _url_split is the only observer, since no public API reports the split host and path
    split: Final = _url_split(url)
    assert (split[6], split[2]) == (host, path)


@pytest.mark.parametrize(
    "scheme",
    [pytest.param("gopher", id="gopher"), pytest.param("mailto", id="mailto"), pytest.param("nntp", id="nntp")],
)
def test_url_split_keeps_backslash_host_for_non_special_scheme(scheme: str) -> None:
    # a non-special scheme does not treat '\' as a separator, so it stays urllib-compatible; _url_split is the only
    # observer, since no public API reports the split host
    url: Final = f"{scheme}://evil\\@good.example/"
    assert _url_split(url)[6] == urlsplit(url).hostname


@pytest.mark.parametrize(
    ("base", "reference", "expected"),
    [
        pytest.param("http://good/a/b", "\\\\evil/x", "http://evil/x", id="double-backslash"),
        pytest.param("http://good/a/b", "/\\evil/x", "http://evil/x", id="slash-backslash"),
        pytest.param("http://good/a/b", "\\/evil/x", "http://evil/x", id="backslash-slash"),
        pytest.param("ftp://good/a/b", "\\\\evil/x", "ftp://evil/x", id="special-ftp"),
        pytest.param("mailto:good", "\\\\evil/x", "\\\\evil/x", id="non-special-verbatim"),
    ],
)
def test_resolve_links_resolves_backslash_scheme_relative(base: str, reference: str, expected: str) -> None:
    fragment: Final = parse_fragment(f'<a href="{reference}"></a>')
    fragment.resolve_links(base)
    assert fragment.serialize() == f'<div><a href="{expected}"></a></div>'
