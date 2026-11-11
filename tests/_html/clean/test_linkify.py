from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from turbohtml._html import _linkify_scan
from turbohtml._html import linkify_find as _linkify_find
from turbohtml._html import linkify_fold as _linkify_fold
from turbohtml._html import phone_e164 as _phone_e164
from turbohtml._html import phone_regions as _phone_regions

if TYPE_CHECKING:
    from collections.abc import Callable


@pytest.mark.parametrize(
    ("text", "parse_email", "bare_domains", "spans"),
    [
        pytest.param("http://example.com", False, False, [(0, 18, 3)], id="scheme-url"),
        pytest.param("HTTP://EXAMPLE.COM", False, False, [(0, 18, 3)], id="uppercase-scheme-and-host"),
        pytest.param("ftp://example.com/file", False, False, [(0, 22, 3)], id="ftp-scheme"),
        pytest.param("svn+ssh://example.com", False, False, [(0, 21, 3)], id="scheme-with-plus"),
        pytest.param("view-source://example.com", False, False, [(0, 25, 3)], id="scheme-with-hyphen"),
        pytest.param("a.b://example.com", False, False, [(0, 17, 3)], id="scheme-with-dot"),
        pytest.param("nothttp//example.com", False, True, [(9, 20, 0)], id="no-colon-slash-slash-falls-to-bare"),
        pytest.param("foo/example.com", False, True, [(4, 15, 0)], id="single-slash-keeps-bare-domain"),
        pytest.param("mailto:bob", False, False, [], id="scheme-without-slashes-no-match"),
        pytest.param(":no scheme here", False, False, [], id="leading-colon-no-scheme"),
        pytest.param("xhttp://example.com", False, False, [(0, 19, 3)], id="arbitrary-scheme-matches-like-bleach"),
        pytest.param("日http://example.com", False, False, [], id="non-ascii-before-scheme-blocks"),
        pytest.param("http://.example.com", False, False, [], id="host-starts-with-dot"),
        pytest.param("http://-example.com", False, False, [], id="host-starts-with-hyphen"),
        pytest.param("http://localhost/x", False, False, [(0, 18, 3)], id="host-without-dot"),
        pytest.param("x@example.com", False, True, [], id="bare-blocked-by-at-on-left"),
        pytest.param(".example.com", False, True, [], id="bare-blocked-by-dot-on-left"),
        pytest.param("see example.com here", False, True, [(4, 15, 0)], id="bare-domain"),
        pytest.param("see example.com here", False, False, [], id="bare-domain-disabled"),
        pytest.param("example.unknowntld nope", False, True, [], id="bare-domain-unknown-tld"),
        pytest.param("a.co", False, True, [(0, 4, 0)], id="bare-two-char-tld"),
        pytest.param("x.y nope", False, True, [], id="bare-one-char-tld-rejected"),
        pytest.param("example.123 here", False, True, [], id="numeric-tld-rejected"),
        pytest.param("foo.дом nope", False, True, [], id="non-ascii-tld-rejected"),
        pytest.param("://example.com", False, False, [], id="scheme-has-no-letters"),
        pytest.param("1http://example.com", False, False, [], id="scheme-starts-with-digit"),
        pytest.param("ends in colon foo:", False, False, [], id="colon-at-end-no-room"),
        pytest.param("http:/example.com", False, False, [], id="single-slash-scheme"),
        pytest.param("http://a.bc- x", False, False, [(0, 11, 3)], id="host-ends-before-a-trailing-hyphen"),
        pytest.param("trailing-.com", False, True, [], id="label-ends-with-hyphen"),
        pytest.param("-leading.com here", False, True, [(1, 12, 0)], id="leading-hyphen-trimmed"),
        pytest.param("a--b.com here", False, True, [(0, 8, 0)], id="double-hyphen-inside-label"),
        pytest.param("food.example.com", False, True, [(0, 16, 0)], id="multi-label-host"),
        pytest.param("дом.example.com", False, True, [(0, 15, 0)], id="non-ascii-label"),
        pytest.param("xn--p1ai.example.com", False, True, [(0, 20, 0)], id="punycode-label-not-tld"),
        pytest.param("ru.xn--p1ai stuff", False, True, [(0, 11, 0)], id="punycode-tld"),
        pytest.param("dot at end example. nope", False, True, [], id="trailing-dot-no-following-label"),
        pytest.param("a@example.com", True, False, [(0, 13, 1)], id="email"),
        pytest.param("a@example.com", False, False, [], id="email-disabled"),
        pytest.param("user.name+tag@example.com", True, False, [(0, 25, 1)], id="email-dotted-local-part"),
        pytest.param("!#$%&'*+-/=?^_`{|}~a@example.com", True, False, [(0, 32, 1)], id="email-special-local-chars"),
        pytest.param("ü2@example.com", True, False, [(0, 14, 1)], id="email-non-ascii-and-digit-local"),
        pytest.param(".bad@example.com", True, False, [(1, 16, 1)], id="email-local-cannot-start-with-dot"),
        pytest.param("x .name@example.com", True, False, [(3, 19, 1)], id="email-dot-after-non-local-stops"),
        pytest.param("@example.com", True, False, [], id="email-no-local-part"),
        pytest.param("a@@example.com", True, False, [], id="email-double-at"),
        pytest.param("ab@cd@example.com", True, False, [], id="email-second-at-blocks-on-left"),
        pytest.param("a@b nope", True, False, [], id="email-no-dotted-host"),
        pytest.param("word http://example.com", False, False, [(5, 23, 3)], id="space-then-url-not-blocked"),
        pytest.param("end. http://example.com", False, False, [(5, 23, 3)], id="not-blocked-after-dot-space"),
        pytest.param("http://example.com/a(b)c", False, False, [(0, 24, 3)], id="balanced-round-in-path"),
        pytest.param("http://example.com/a[b]c", False, False, [(0, 24, 3)], id="balanced-square-in-path"),
        pytest.param("http://example.com/a)b", False, False, [(0, 20, 3)], id="unbalanced-round-stops"),
        pytest.param("http://example.com/a]b", False, False, [(0, 20, 3)], id="unbalanced-square-stops"),
        pytest.param("http://example.com/a{b}c", False, False, [(0, 24, 3)], id="balanced-curly-in-path"),
        pytest.param("http://example.com/a}b", False, False, [(0, 20, 3)], id="unbalanced-curly-stops"),
        pytest.param(
            "{http://example.com/foo_bar}", False, False, [(1, 27, 3)], id="brace-scoped-trailing-brace-trimmed"
        ),
        pytest.param("http://example.com/foo'", False, False, [(0, 22, 3)], id="trailing-apostrophe-trimmed"),
        pytest.param("http://example.com/o'reilly", False, False, [(0, 27, 3)], id="apostrophe-mid-path-kept"),
        pytest.param("(http://example.com/path)", False, False, [(1, 24, 3)], id="link-in-parens"),
        pytest.param("http://example.com/path.", False, False, [(0, 23, 3)], id="path-trailing-dot-trimmed"),
        pytest.param("http://example.com/a,b,", False, False, [(0, 22, 3)], id="path-trailing-comma-trimmed"),
        pytest.param("http://example.com/p!?:;", False, False, [(0, 20, 3)], id="path-trailing-punct-run-trimmed"),
        # '*' is an RFC 3986 sub-delim that bleach and linkify_it keep, so a trailing one stays in the link
        pytest.param("http://example.com/path*", False, False, [(0, 24, 3)], id="path-trailing-star-kept"),
        pytest.param("http://example.com:notaport/x", False, False, [(0, 18, 3)], id="colon-not-a-port-ends-host"),
        pytest.param("http://example.com:8080", False, False, [(0, 23, 3)], id="port-at-end-of-string"),
        pytest.param("http://example.com?q=1", False, False, [(0, 22, 3)], id="query-led-tail"),
        pytest.param("http://example.com no tail", False, False, [(0, 18, 3)], id="host-only-no-tail"),
        pytest.param("see http://example.com.", False, False, [(4, 22, 3)], id="host-trailing-dot-trimmed"),
        pytest.param('http://example.com/a"b', False, False, [(0, 20, 3)], id="tail-stops-at-quote"),
        pytest.param("http://example.com/a`b", False, False, [(0, 20, 3)], id="tail-stops-at-backtick"),
        pytest.param("http://example.com/a b", False, False, [(0, 20, 3)], id="tail-stops-at-space"),
        pytest.param("http://example.com/a\x7fb", False, False, [(0, 20, 3)], id="tail-stops-at-del"),
        pytest.param("http://user:pass@host.com/x", True, False, [(0, 27, 3)], id="userinfo-url"),
        pytest.param("http://u@example.com", False, False, [(0, 20, 3)], id="userinfo-at-only"),
        pytest.param("http://a.b@example.com", False, False, [(0, 22, 3)], id="userinfo-is-a-valid-host"),
        pytest.param("http://1.2.3.4/path", False, False, [(0, 19, 3)], id="schemeful-ipv4"),
        pytest.param("at 1.2.3.4 here", False, True, [], id="bare-ipv4-needs-tld"),
        pytest.param("http://example.com#frag", False, False, [(0, 23, 3)], id="fragment-after-host"),
        pytest.param("http://example.com:8080?q=1", False, False, [(0, 27, 3)], id="port-then-query-no-userinfo"),
        pytest.param("http://example.com:8080#f", False, False, [(0, 25, 3)], id="port-then-fragment-no-userinfo"),
        pytest.param("http://example.com:8080 x", False, False, [(0, 23, 3)], id="port-then-space-no-userinfo"),
        pytest.param("see EXAMPLE.COM here", False, True, [(4, 15, 0)], id="bare-domain-uppercase-tld"),
        pytest.param("https://cdn_1.example.org/x", False, False, [(0, 27, 3)], id="underscore-in-scheme-host"),
        pytest.param("http://_dmarc.example.com/", False, False, [(0, 26, 3)], id="underscore-leading-scheme-host"),
        pytest.param("cdn_1.example.org/x", False, True, [(0, 19, 0)], id="underscore-in-bare-host"),
        pytest.param("_dmarc.example.com here", False, True, [(0, 18, 0)], id="underscore-leading-bare-host"),
    ],
)
def test_scanner_spans(
    text: str,
    parse_email: bool,  # ruff:ignore[boolean-type-hint-positional-argument]  # a pytest parametrize value, not a boolean-trap call site
    bare_domains: bool,  # ruff:ignore[boolean-type-hint-positional-argument]  # a pytest parametrize value, not a boolean-trap call site
    spans: list[tuple[int, int, int]],
    with_hrefs: Callable[[str, list[tuple[int, int, int]]], list[tuple[int, int, int, str, None]]],
) -> None:
    assert _linkify_scan(text, parse_email, bare_domains) == with_hrefs(text, spans)


@pytest.mark.parametrize(
    ("text", "url_schemes", "spans"),
    [
        pytest.param("http://example.com", ("http", "https", "ftp"), [(0, 18, 3)], id="allowed-scheme-matches"),
        pytest.param("ftp://example.com/file", ("http",), [], id="scheme-not-in-allowlist-skipped"),
        pytest.param("javascript://example.com", ("http", "https", "ftp"), [], id="javascript-scheme-excluded"),
        pytest.param("HTTP://example.com", ("http",), [(0, 18, 3)], id="allowlist-is-case-insensitive"),
        pytest.param("xhttp://example.com", (), [], id="empty-allowlist-matches-nothing"),
    ],
)
def test_scanner_url_schemes_restrict_authority(
    text: str,
    url_schemes: tuple[str, ...],
    spans: list[tuple[int, int, int]],
    with_hrefs: Callable[[str, list[tuple[int, int, int]]], list[tuple[int, int, int, str, None]]],
) -> None:
    # a non-None url_schemes tuple restricts scheme://host matching to that allowlist; omitting it matches any scheme
    assert _linkify_scan(text, False, False, (), url_schemes) == with_hrefs(text, spans)  # ruff:ignore[boolean-positional-value-in-call]  # positional-only C binding


def test_scanner_omitting_url_schemes_matches_any_scheme() -> None:
    assert _linkify_scan("xyzzy://example.com", False, False) == [(0, 19, 3, "xyzzy://example.com", None, False)]  # ruff:ignore[boolean-positional-value-in-call]  # positional C binding


def test_scanner_rejects_non_str_text() -> None:
    with pytest.raises(TypeError):
        _linkify_scan(123, False, False)  # ruff:ignore[boolean-positional-value-in-call]  # ty: ignore[invalid-argument-type]  # the C arg check is the point


def test_scanner_rejects_non_tuple_url_schemes() -> None:
    with pytest.raises(TypeError):
        _linkify_scan("http://x.com", False, False, (), ["http"])  # ruff:ignore[boolean-positional-value-in-call]  # ty: ignore[invalid-argument-type]  # positional C API; invalid input


def test_find_rejects_non_str_text() -> None:
    with pytest.raises(TypeError):
        _linkify_find(123, True, True, (), (), ("http",), None, False)  # ruff:ignore[boolean-positional-value-in-call]  # ty: ignore[invalid-argument-type]  # positional C API; invalid input


def test_find_rejects_non_tuple_tlds() -> None:
    with pytest.raises(TypeError):
        _linkify_find("text", True, True, ["corp"], (), ("http",), None, False)  # ruff:ignore[boolean-positional-value-in-call]  # ty: ignore[invalid-argument-type]  # positional C API; invalid input


@pytest.mark.parametrize(
    ("values", "kind", "expected"),
    [
        pytest.param(["Pre", "code", "pre"], 0, ("code", "pre"), id="lowercased-deduplicated-sorted"),
        pytest.param([".Dev", "dev", "app"], 1, ("app", "dev"), id="a-leading-dot-falls-off-a-tld"),
        pytest.param(["Git:", "ssh::", "git"], 2, ("git", "ssh"), id="trailing-colons-fall-off-a-scheme"),
        pytest.param([" Fax ", "tel", "fax"], 3, ("fax", "tel"), id="words-are-stripped"),
        pytest.param([], 0, (), id="nothing"),
        pytest.param(["."], 1, ("",), id="a-lone-dot-is-an-empty-tld"),
        pytest.param([""], 1, ("",), id="an-empty-tld"),
        pytest.param([":", "::"], 2, ("",), id="lone-colons-are-an-empty-scheme"),
    ],
)
def test_fold(values: list[str], kind: int, expected: tuple[str, ...]) -> None:
    assert _linkify_fold(values, "field", kind) == expected


def test_fold_names_the_field_in_its_error() -> None:
    with pytest.raises(TypeError, match="skip_tags entries must be str"):
        _linkify_fold(["pre", 5], "skip_tags", 0)  # ty: ignore[invalid-argument-type]  # the check is the point


def test_fold_takes_three_arguments() -> None:
    with pytest.raises(TypeError):
        _linkify_fold(["a"], "field")  # ty: ignore[missing-argument]  # the arity check is the point


def test_fold_needs_an_iterable() -> None:
    with pytest.raises(TypeError):
        _linkify_fold(5, "skip_tags", 0)  # ty: ignore[invalid-argument-type]  # the check is the point


def test_regions_strip_uppercase_and_keep_first_seen_order() -> None:
    assert _phone_regions([" gb", "US", "gb", "de "]) == ("GB", "US", "DE")


def test_a_non_ascii_region_is_left_alone() -> None:
    # `ß` would otherwise fold to South Sudan's `SS`
    assert _phone_regions(["ß", "us"]) == ("ß", "US")


def test_regions_entries_must_be_str() -> None:
    with pytest.raises(TypeError, match="regions entries must be str"):
        _phone_regions(["US", 1])  # ty: ignore[invalid-argument-type]  # the check is the point


def test_regions_need_an_iterable() -> None:
    with pytest.raises(TypeError):
        _phone_regions(5)  # ty: ignore[invalid-argument-type]  # the check is the point


@pytest.mark.parametrize(
    ("country_code", "national", "expected"),
    [
        pytest.param(44, "2079460958", "+442079460958", id="fits"),
        pytest.param(1, "2" * 14, f"+1{'2' * 14}", id="exactly-fifteen-digits"),
        pytest.param(1, "2" * 15, None, id="one-digit-too-many"),
    ],
)
def test_e164(country_code: int, national: str, expected: str | None) -> None:
    assert _phone_e164(country_code, national) == expected


def test_e164_rejects_a_non_str_number() -> None:
    with pytest.raises(TypeError):
        _phone_e164(1, 5)  # ty: ignore[invalid-argument-type]  # the check is the point


@pytest.mark.parametrize(
    ("text", "parse_email", "extra_tlds", "spans"),
    [
        pytest.param("foo.internal x", False, ("internal",), [(0, 12, 0)], id="custom-ascii-tld"),
        pytest.param("foo.internal x", False, (), [], id="absent-without-extra"),
        pytest.param("a.zzz here", False, ("foo",), [], id="custom-tld-no-match"),
        pytest.param("пример.дом ok", False, ("дом",), [(0, 10, 0)], id="custom-non-ascii-tld"),
        pytest.param("m@foo.internal", True, ("internal",), [(0, 14, 1)], id="custom-tld-email"),
        pytest.param("example.com", False, ("internal",), [(0, 11, 0)], id="builtin-tld-still-wins"),
    ],
)
def test_scanner_extra_tlds(
    text: str,
    parse_email: bool,  # ruff:ignore[boolean-type-hint-positional-argument]  # a pytest parametrize value, not a boolean-trap call site
    extra_tlds: tuple[str, ...],
    spans: list[tuple[int, int, int]],
    with_hrefs: Callable[[str, list[tuple[int, int, int]]], list[tuple[int, int, int, str, None]]],
) -> None:
    assert _linkify_scan(text, parse_email, True, extra_tlds) == with_hrefs(text, spans)  # ruff:ignore[boolean-positional-value-in-call]  # positional-only C binding under test


def test_scanner_extra_tlds_defaults_to_none() -> None:
    assert _linkify_scan("foo.internal", False, True) == []  # ruff:ignore[boolean-positional-value-in-call]  # positional-only C binding under test
