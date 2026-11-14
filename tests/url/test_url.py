from __future__ import annotations

from typing import Final

import pytest

from turbohtml import parse, parse_fragment


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


@pytest.mark.parametrize(
    "separator", [pytest.param("\t", id="tab"), pytest.param("\n", id="lf"), pytest.param("\r", id="cr")]
)
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
        pytest.param("https://site.org/dir/page", "http:/.", "http://./", id="single-dot-host"),
        pytest.param("https://site.org/dir/page", "http:/./a", "http://./a", id="single-dot-host-path"),
        pytest.param("https://site.org/dir/page", "HTTP:/.?q#f", "http://./?q#f", id="uppercase-dot-host"),
        pytest.param("https://site.org/dir/page", "ftp:/.", "ftp://./", id="ftp-single-dot-host"),
        pytest.param("https://site.org/dir/page", "wss:/./a", "wss://./a", id="wss-single-dot-host-path"),
        pytest.param("https://site.org/dir/page", "http:/./é", "http://./%C3%A9", id="unicode-dot-host-path"),
        pytest.param("ftp://site.org/p", "wss:/./é", "wss://./%C3%A9", id="unicode-foreign-same-length"),
        pytest.param("http://site.org/p", "http:/./é", "http://site.org/%C3%A9", id="unicode-same-scheme-target"),
        pytest.param("http://site.org/dir/page", "http:/./a", "http://site.org/a", id="same-scheme-dot-path"),
        pytest.param("http://site.org/dir/page", "http:/./a\\..", "http://site.org/", id="same-scheme-dot-backslash"),
        pytest.param("http://é.example/p", "http:/./a", "http://xn--9ca.example/a", id="unicode-base-same-scheme"),
        pytest.param("http://é.example/p", "http:/./é", "http://xn--9ca.example/%C3%A9", id="unicode-same-scheme"),
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
        pytest.param(
            "wss:wss:b:c/%2e%2e/%2e",
            "wss://relay.example/room/42/q",
            "wss://relay.example/room/42/",
            id="remainder-repeats-scheme",
        ),
        pytest.param(
            "ftp:wss://files.test/%2e/q",
            "ftp://mirror.example/pub/x",
            "ftp://mirror.example/pub/wss://files.test/q",
            id="remainder-foreign-scheme",
        ),
        pytest.param(
            "wss:urn:lab/%2E%2E/%2e/zz",
            "wss://relay.example/room/42/a",
            "wss://relay.example/room/42/zz",
            id="remainder-opaque-scheme",
        ),
        pytest.param(
            "ws:alt:%2e%2e/%2e/t",
            "ws://gateway.example/x/y",
            "ws://gateway.example/x/alt:%2e%2e/t",
            id="remainder-colon-segment",
        ),
        pytest.param(
            "ftp:h\\i:j/%2e/k", "ftp://mirror.example/a/b", "ftp://mirror.example/a/h/i:j/k", id="colon-after-slash"
        ),
        pytest.param(
            "ftp:%2E?k=v:w", "ftp://mirror.example/pub/x", "ftp://mirror.example/pub/?k=v:w", id="colon-in-query"
        ),
        pytest.param(
            "wss:%2E#p:q", "wss://relay.example/room/9/z", "wss://relay.example/room/9/#p:q", id="colon-in-fragment"
        ),
        pytest.param(
            "ftp:%2e%2E", "ftp://mirror.example/pub/x/y", "ftp://mirror.example/pub/", id="encoded-parent-only"
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


def test_meta_refresh_tab_in_repeated_scheme_keeps_colon_segment() -> None:
    # WHATWG drops the tab before parsing, so the repeated scheme is a path segment whose colon is not a scheme
    html = '<meta http-equiv=refresh content="0;url=wss:wss\t:q:r/%2e/s">'
    assert parse(html).meta_refresh("wss://relay.example/room/42/q") == (0.0, "wss://relay.example/room/42/wss:q:r/s")


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


_SITE: Final[str] = "http://s.com/"


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        pytest.param("5; url=next", (5.0, f"{_SITE}next"), id="delay-and-url"),
        pytest.param("10", (10.0, _SITE), id="delay-only"),
        pytest.param("2.5;url=x", (2.5, f"{_SITE}x"), id="float-delay"),
        pytest.param("2.5", (2.5, _SITE), id="float-delay-only"),
        pytest.param("5;url=", (5.0, _SITE), id="url-prefix-empty-value"),
        pytest.param("5;url=£x", (5.0, f"{_SITE}%C2%A3x"), id="two-byte-utf8-encoded"),
        pytest.param("5;url=€page", (5.0, f"{_SITE}%E2%82%ACpage"), id="three-byte-utf8-encoded"),
        pytest.param("5;url=\U0001f389", (5.0, f"{_SITE}%F0%9F%8E%89"), id="four-byte-utf8-encoded"),
        pytest.param("  7 ; url = y ", (7.0, f"{_SITE}y"), id="surrounding-whitespace"),
        pytest.param("3;url='q'", (3.0, f"{_SITE}q"), id="single-quoted-url"),
        pytest.param("8, z", (8.0, f"{_SITE}z"), id="comma-separator-no-prefix"),
        pytest.param("9;next", (9.0, f"{_SITE}next"), id="no-url-prefix"),
        pytest.param("1;urlx", (1.0, f"{_SITE}urlx"), id="url-prefix-without-equals"),
        pytest.param("5;uxxx", (5.0, f"{_SITE}uxxx"), id="u-without-rl-prefix"),
        pytest.param("5;urxx", (5.0, f"{_SITE}urxx"), id="ur-without-l-prefix"),
        pytest.param("5;url", (5.0, f"{_SITE}url"), id="bare-url-keyword-no-equals"),
        pytest.param("5;url='", (5.0, f"{_SITE}'"), id="lone-quote-after-url"),
        pytest.param("6;", (6.0, _SITE), id="separator-without-url"),
        pytest.param("2;ab", (2.0, f"{_SITE}ab"), id="short-url-after-separator"),
        pytest.param("0;url='unbalanced", (0.0, f"{_SITE}'unbalanced"), id="unbalanced-quote-kept"),
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
    assert parse(html).meta_refresh(_SITE) == (4.0, f"{_SITE}d")


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
