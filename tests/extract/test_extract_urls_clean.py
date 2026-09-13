"""clean_url: the scrub pass, the web-URL validation, the language filter, and the UrlCleaning config itself."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest

from turbohtml import parse
from turbohtml._html import _url_clean, _url_normalize
from turbohtml.extract import UrlCleaning, clean_url, extract_links, normalize_url

if TYPE_CHECKING:
    from collections.abc import Callable


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param("  https://www.dwds.de ", "https://www.dwds.de/", id="surrounding-whitespace"),
        pytest.param("https://www.dwds\t.de/x\n", "https://www.dwds.de/x", id="embedded-tab-and-newline"),
        pytest.param("\x00\x1fhttps://example.org/\x07", "https://example.org/", id="control-characters"),
        pytest.param("https://exam ple.org/pa th", "https://example.org/path", id="embedded-spaces-removed"),
        pytest.param("<![CDATA[https://www.dwds.de]]>", "https://www.dwds.de/", id="cdata-wrapper"),
        pytest.param('https://example.org/abc"more', "https://example.org/abc", id="quote-truncates"),
        pytest.param("https://example.org/abc<b>x", "https://example.org/abc", id="tag-truncates"),
        pytest.param("https://example.org/abc>x", "https://example.org/abc", id="closing-angle-truncates"),
        pytest.param(
            "https://www.dwds.de/test?param=test&amp;other=test",
            "https://www.dwds.de/test?other=test&param=test",
            id="amp-entity-undone",
        ),
        pytest.param("https://test.org/example/&", "https://test.org/example/", id="trailing-slash-amp"),
        pytest.param("https://test.org/a&b", "https://test.org/a&b", id="interior-amp-kept"),
        pytest.param(
            "https://example.org:443/file.html?p=100&abc=1#frag",
            "https://example.org/file.html?abc=1&p=100#frag",
            id="full-normalization-applies",
        ),
    ],
)
def test_clean_scrubs_markup_damage(url: str, expected: str) -> None:
    assert clean_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        pytest.param("ftp://example.org/x", id="non-web-scheme"),
        pytest.param("mailto:someone@example.org", id="mailto"),
        pytest.param("javascript:alert(1)", id="javascript"),
        pytest.param("not a url", id="no-scheme"),
        pytest.param("relative/path.html", id="relative"),
        pytest.param("http://", id="empty-host"),
        pytest.param("http://localhost/x", id="dotless-host"),
        pytest.param("http://[::1/x", id="unsplittable"),
        pytest.param("", id="empty-string"),
    ],
)
def test_clean_rejects_non_web_urls(url: str) -> None:
    assert clean_url(url) is None


@pytest.mark.parametrize(
    "url",
    [
        pytest.param("http://localhost:8000/x", id="dotless-host-with-port"),
        pytest.param("http://[::1]/x", id="ipv6-host"),
    ],
)
def test_clean_accepts_colon_hosts(url: str) -> None:
    assert clean_url(url) == url


@pytest.mark.parametrize(
    ("url", "language", "expected"),
    [
        pytest.param("http://test.org/de/page", "de", "http://test.org/de/page", id="path-segment-match"),
        pytest.param("http://test.org/de/page", "en", None, id="path-segment-mismatch"),
        pytest.param("http://test.org/en-us/page", "en", "http://test.org/en-us/page", id="regioned-segment-match"),
        pytest.param("http://test.org/de-at/page", "en", None, id="regioned-segment-mismatch"),
        pytest.param("http://test.org/js/app", "en", "http://test.org/js/app", id="non-language-code-ignored"),
        pytest.param("http://test.org/docs/de/x", "en", "http://test.org/docs/de/x", id="only-leading-segment-checked"),
        pytest.param("http://test.org/page", "en", "http://test.org/page", id="no-marker-passes"),
        pytest.param("http://test.org/?lang=de&page=2", "en", None, id="lang-param-mismatch"),
        pytest.param("http://test.org/?lang=EN&page=2", "en", "http://test.org/?lang=EN&page=2", id="lang-param-match"),
        pytest.param(
            "http://test.org/?language=deutsch", "de", "http://test.org/?language=deutsch", id="language-word-match"
        ),
        pytest.param("http://test.org/?lang=&page=2", "de", "http://test.org/?lang=&page=2", id="empty-lang-passes"),
        pytest.param("http://test.org/?lang", "de", "http://test.org/?lang", id="valueless-lang-passes"),
    ],
)
def test_clean_language_filter(url: str, language: str, expected: str | None) -> None:
    assert clean_url(url, UrlCleaning(language=language)) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param("http://de.example.org/x", None, id="language-subdomain-mismatch"),
        pytest.param("http://en.example.org/x", "http://en.example.org/x", id="language-subdomain-match"),
        pytest.param("http://www.example.org/x", "http://www.example.org/x", id="long-label-ignored"),
        pytest.param("http://vx.example.org/x", "http://vx.example.org/x", id="non-language-label-ignored"),
    ],
)
def test_clean_strict_checks_language_subdomains(url: str, expected: str | None) -> None:
    assert clean_url(url, UrlCleaning(strict=True, language="en")) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param("http://a.com/\udce9", None, id="surrogate-in-path"),
        pytest.param("http://a.com/p?q=\udce9", None, id="surrogate-in-query"),
        pytest.param("http://a.com/p#\udce9", None, id="surrogate-in-fragment"),
        pytest.param("http://\udce9.com/", "http://\udce9.com/", id="surrogate-in-host-survives"),
    ],
)
def test_clean_handles_lone_surrogates(url: str, expected: str | None) -> None:
    # an unencodable URL is not fetchable, so clean_url returns None (as courlan does) rather than raising; a
    # surrogate in the host never reaches the percent-encoder, so that URL still cleans
    assert clean_url(url) == expected


def test_clean_rejects_non_str() -> None:
    with pytest.raises(TypeError, match="url must be a str"):
        clean_url(123)  # ty: ignore[invalid-argument-type]  # a non-str exercises the TypeError guard


def test_default_options_shared_across_calls() -> None:
    assert clean_url("https://example.org/x?utm_source=a") == clean_url(
        "https://example.org/x?utm_source=a", UrlCleaning()
    )


def test_language_must_be_iso_639_1() -> None:
    with pytest.raises(ValueError, match="ISO 639-1"):
        UrlCleaning(language="german")


_KNOBS: Final = (False, True, False, None, frozenset(), frozenset({"id"}), frozenset({"lang"}))


@pytest.mark.parametrize(
    ("entry", "args"),
    [
        pytest.param(_url_normalize, ("http://x.com", *_KNOBS[:-1]), id="normalize-too-few"),
        pytest.param(_url_clean, ("http://x.com", *_KNOBS), id="clean-too-few"),
        pytest.param(_url_normalize, ("http://x.com", *_KNOBS[:3], 5, *_KNOBS[4:]), id="allow-not-iterable"),
        pytest.param(_url_normalize, ("http://x.com", *_KNOBS[:3], {1}, *_KNOBS[4:]), id="allow-holds-an-int"),
        pytest.param(_url_normalize, ("http://x.com", *_KNOBS[:4], {1}, *_KNOBS[5:]), id="deny-holds-an-int"),
        pytest.param(
            _url_clean,
            ("http://x.com", *_KNOBS[:3], {1}, *_KNOBS[4:], None, frozenset()),
            id="clean-allow-holds-an-int",
        ),
    ],
)
def test_the_entry_points_reject_bad_arguments(entry: Callable[..., object], args: tuple[object, ...]) -> None:
    with pytest.raises(TypeError):
        entry(*args)


def test_extract_links_rejects_a_parameter_name_that_is_not_a_str() -> None:
    with pytest.raises(TypeError, match="query parameter names must be str"):
        extract_links("", "https://a.com/", UrlCleaning(query_deny=frozenset({1})))  # ty: ignore[invalid-argument-type]


def test_the_document_entry_point_rejects_too_few_arguments() -> None:
    external_only = False
    with pytest.raises(TypeError):
        parse("")._extract_links(None, external_only)  # ty: ignore[missing-argument]  # the arity check is the point


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param("http://x.com:443/", "http://x.com:443/", id="another-scheme-default-is-kept"),
        pytest.param(
            "http://x.com:99999999999999999999/", "http://x.com:99999999999999999999/", id="an-overflowing-port"
        ),
        pytest.param("http://x.com:0080/", "http://x.com/", id="leading-zeros-read-as-the-default"),
        pytest.param("http://x.com:0081/", "http://x.com:81/", id="leading-zeros-fall-away"),
        pytest.param("ftp://x.com", "ftp://x.com", id="a-non-web-scheme-keeps-its-empty-path"),
        pytest.param("http:/x", "http:///x", id="a-netloc-scheme-without-an-authority-gets-the-marker"),
        pytest.param("http:x", "http:x", id="a-netloc-scheme-with-a-relative-path-keeps-its-shape"),
        pytest.param("http:", "http://", id="a-netloc-scheme-alone-gets-the-marker"),
        pytest.param("mailto:x@y.com", "mailto:x@y.com", id="an-opaque-scheme"),
        pytest.param("/a/b?utm_source=x#top", "/a/b#top", id="a-relative-reference"),
        pytest.param("////x", "////x", id="an-empty-authority-before-a-double-slash-path"),
    ],
)
def test_normalize_reassembles_every_shape(url: str, expected: str) -> None:
    assert normalize_url(url) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param("http://x.com//", "http://x.com", id="a-slash-run-is-trimmed-whole"),
        pytest.param("http://x.com/", "http://x.com/", id="the-root-stays"),
        pytest.param("http://x.com/a", "http://x.com/a", id="no-slash-to-trim"),
    ],
)
def test_trailing_slash_off(url: str, expected: str) -> None:
    assert normalize_url(url, UrlCleaning(trailing_slash=False)) == expected


def test_a_fragment_key_with_a_lone_surrogate_cannot_be_encoded() -> None:
    with pytest.raises(UnicodeEncodeError):
        normalize_url("http://x.com/#\ud800=1")


def test_clean_drops_a_fragment_key_with_a_lone_surrogate() -> None:
    assert clean_url("http://x.com/#\ud800=1") is None


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param('<a href=" https://test.com/a\t">x</a>', {"https://test.com/a"}, id="href-is-trimmed"),
        pytest.param(
            '<a href="https://test.com/a" rel="noopener">x</a>', {"https://test.com/a"}, id="an-eight-letter-rel"
        ),
        pytest.param(
            '<a href="https://test.com/a" rel="\xa0nofollow\xa0\xfcgc ">x</a>', set(), id="unicode-spaced-nofollow"
        ),
        pytest.param('<a href="https://test.com/a" rel="ugc\tNOFOLLOW">x</a>', set(), id="tab-separated-nofollow"),
        pytest.param('<a href="https://test.com/a" rel="ugc ">x</a>', {"https://test.com/a"}, id="rel-ends-in-a-space"),
    ],
)
def test_anchor_attributes_are_read_the_way_html_defines_them(html: str, expected: set[str]) -> None:
    assert extract_links(html, "https://test.com/") == expected


def test_a_longer_hreflang_subtag_is_another_language() -> None:
    html = '<a href="https://test.com/a" hreflang="deu">x</a>'
    assert extract_links(html, "https://test.com/", UrlCleaning(language="de")) == set()


def test_an_unsplittable_base_raises() -> None:
    with pytest.raises(ValueError, match="Invalid IPv6 URL"):
        extract_links('<a href="https://a.example/">x</a>', "http://[::1", external_only=True)


def test_a_base_element_that_cannot_resolve_raises() -> None:
    with pytest.raises(ValueError, match="Invalid IPv6 URL"):
        extract_links('<base href="http://[::1"><a href="p">x</a>', "https://a.com/")


def test_a_relative_href_against_an_unsplittable_base_element_raises() -> None:
    with pytest.raises(ValueError, match="Invalid IPv6 URL"):
        extract_links('<base href="http://[::1"><a href="p">x</a>')


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param("HTTPS://WWW.DWDS.DE/", "https://www.dwds.de/", id="scheme-and-host-lowercased"),
        pytest.param(
            "https://" + ".".join(("A" * 63, "B" * 63, "C" * 63, "D" * 49, "example", "org")) + "/",
            "https://" + ".".join(("a" * 63, "b" * 63, "c" * 63, "d" * 49, "example", "org")) + "/",
            id="maximum-dns-host-length",
        ),
        pytest.param("https://\u212a.Example.ORG/", "https://k.example.org/", id="lowercase-narrows-host-to-ascii"),
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
        pytest.param("http://münchen..de/x", "http://xn--mnchen-3ya..de/x", id="empty-label-still-encoded"),
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
        pytest.param("http://x.org/a..b/c", "http://x.org/a..b/c", id="literal-dot-inside-segment"),
        pytest.param("http://x.org/café", "http://x.org/caf%C3%A9", id="undotted-latin1-path"),
        pytest.param("http://x.org/東京", "http://x.org/%E6%9D%B1%E4%BA%AC", id="undotted-bmp-path"),
        pytest.param("http://x.org/😀", "http://x.org/%F0%9F%98%80", id="undotted-astral-path"),
        pytest.param("http://x.org/café/a/../b", "http://x.org/caf%C3%A9/b", id="dotted-latin1-path"),
        pytest.param("http://x.org/東京/a/../b", "http://x.org/%E6%9D%B1%E4%BA%AC/b", id="dotted-bmp-path"),
        pytest.param("http://x.org/😀/a/../b", "http://x.org/%F0%9F%98%80/b", id="dotted-astral-path"),
        pytest.param("http://x.org/a%/b", "http://x.org/a%/b", id="stray-percent-in-path"),
        pytest.param("http://x.org/a/%2x/b", "http://x.org/a/%2x/b", id="invalid-percent-in-path"),
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
        pytest.param(
            "http://test.org/?café=1",
            "http://test.org/?caf%C3%A9=1",
            id="unescaped-latin1-key",
        ),
        pytest.param(
            "http://test.org/?東京=1",
            "http://test.org/?%E6%9D%B1%E4%BA%AC=1",
            id="unescaped-bmp-key",
        ),
        pytest.param(
            "http://test.org/?😀=1",
            "http://test.org/?%F0%9F%98%80=1",
            id="unescaped-astral-key",
        ),
        pytest.param("http://test.org/?café%61=1", "http://test.org/?caf%C3%A9%61=1", id="escaped-latin1-key"),
        pytest.param("http://test.org/?東%61京=1", "http://test.org/?%E6%9D%B1%61%E4%BA%AC=1", id="escaped-bmp-key"),
        pytest.param("http://test.org/?😀%61=1", "http://test.org/?%F0%9F%98%80%61=1", id="escaped-astral-key"),
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


@pytest.mark.parametrize(
    ("url", "options", "expected"),
    [
        pytest.param(
            "http://x.org/p?id=200&foo=bar&name=wired",
            UrlCleaning(query_allow=frozenset({"id", "name"})),
            "http://x.org/p?id=200&name=wired",
            id="allow-keeps-only-listed",
        ),
        pytest.param(
            "http://x.org/p?utm_source=kept&id=1",
            UrlCleaning(query_allow=frozenset({"utm_source"})),
            "http://x.org/p?utm_source=kept",
            id="allowed-tracker-survives",
        ),
        pytest.param(
            "http://x.org/p?ID=2&Name=x",
            UrlCleaning(query_allow=frozenset({"id"})),
            "http://x.org/p?ID=2",
            id="allow-matches-case-insensitively",
        ),
        pytest.param(
            "http://x.org/p?id=200&foo=bar&name=wired",
            UrlCleaning(query_deny=frozenset({"id", "foo"})),
            "http://x.org/p?name=wired",
            id="deny-drops-listed",
        ),
        pytest.param(
            "http://x.org/p?utm_source=x&keep=1&drop=2",
            UrlCleaning(query_deny=frozenset({"drop"})),
            "http://x.org/p?keep=1",
            id="deny-composes-with-tracker-removal",
        ),
        pytest.param(
            "http://x.org/p?page=2&post=abc&id=1",
            UrlCleaning(strict=True, query_deny=frozenset({"post"})),
            "http://x.org/p?id=1&page=2",
            id="deny-composes-with-strict",
        ),
        pytest.param(
            "http://x.org/p?b=2&a=1#mtm_campaign=doc&keep=1",
            UrlCleaning(query_deny=frozenset({"keep"})),
            "http://x.org/p?a=1&b=2",
            id="fragment-scrub-honors-deny",
        ),
    ],
)
def test_query_filters(url: str, options: UrlCleaning, expected: str) -> None:
    assert normalize_url(url, options) == expected


def test_strict_and_allow_are_mutually_exclusive() -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        UrlCleaning(strict=True, query_allow=frozenset({"id"}))


def test_w3lib_preset_drops_the_fragment() -> None:
    assert UrlCleaning.w3lib() == UrlCleaning(strip_fragment=True)


def test_w3lib_preset_canonicalizes_like_canonicalize_url() -> None:
    url = "http://www.example.com/do?c=3&b=5&b=2&a=50#frag"
    assert normalize_url(url, UrlCleaning.w3lib()) == "http://www.example.com/do?a=50&b=2&b=5&c=3"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param("https://x.org/p?wickedclid=1&keep=2", "https://x.org/p?keep=2", id="clid-suffix-dropped"),
        pytest.param("https://x.org/p?lucid=1", "https://x.org/p?lucid=1", id="near-miss-lucid-kept"),
        pytest.param("https://x.org/p?ref_source=1", "https://x.org/p", id="tracker-word-underscore"),
        pytest.param("https://x.org/p?reference=1", "https://x.org/p?reference=1", id="longer-word-kept"),
    ],
)
def test_tracker_suffix_and_word_boundaries(url: str, expected: str) -> None:
    assert clean_url(url) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param("https://x.org/a/%2E%2E/b", "https://x.org/b", id="uppercase-escape-parent"),
        pytest.param("https://x.org/a/%2E/b", "https://x.org/a/b", id="uppercase-escape-self"),
        pytest.param("https://x.org/a/../../b", "https://x.org/b", id="parent-past-root-clamps"),
        # a trailing dot segment resolves to the directory, so the slash survives (path state, spec 4.4)
        pytest.param("https://x.org/a/b/..", "https://x.org/a/", id="trailing-parent-keeps-slash"),
        pytest.param("https://x.org/a/.", "https://x.org/a/", id="trailing-self-keeps-slash"),
        pytest.param("https://x.org/...", "https://x.org/...", id="triple-dot-is-a-segment"),
    ],
)
def test_dot_segment_resolution(url: str, expected: str) -> None:
    assert clean_url(url) == expected
