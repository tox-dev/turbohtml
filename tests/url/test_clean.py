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
        pytest.param("http:example.org/x", "http://example.org/x", id="host-after-scheme"),
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
        pytest.param("https://test.org/example/&\x00>", "https://test.org/example/", id="trailing-control-slash-amp"),
        pytest.param(
            "https://test.org/example/&?\x00>", "https://test.org/example/", id="trailing-control-empty-query"
        ),
        pytest.param(
            "https://test.org/example/&#\x00>", "https://test.org/example/", id="trailing-control-empty-fragment"
        ),
        pytest.param(
            "https://test.org/example/&?#\x00>", "https://test.org/example/", id="trailing-control-empty-both"
        ),
        pytest.param("https://test.org/example/x?#\x00>", "https://test.org/example/x", id="trailing-control-no-amp"),
        pytest.param("https://test.org/example/&x#\x00>", "https://test.org/example/&x", id="trailing-control-path"),
        pytest.param("https://test.org/example/&?x\x00>", "https://test.org/example/&?x", id="trailing-control-query"),
        pytest.param(
            "https://test.org/example/x?\x00>", "https://test.org/example/x", id="trailing-control-no-amp-query"
        ),
        pytest.param(
            "https://test.org/example/&x\x00>", "https://test.org/example/&x", id="trailing-control-path-only"
        ),
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
    ("url", "expected"),
    [
        pytest.param("https://example.org/&?&", "https://example.org/", id="empty-query-amp"),
        pytest.param("https://example.org//&?&&", "https://example.org//", id="empty-query-amps"),
        pytest.param("https://example.org/&?&#", "https://example.org/", id="empty-query-fragment"),
        pytest.param("https://example.org/&?foo=1", "https://example.org/&?foo=1", id="nonempty-query"),
        pytest.param("https://example.org/&?#keep", "https://example.org/&#keep", id="nonempty-fragment"),
        pytest.param("https://example.org/a&?&", "https://example.org/a&", id="nonslash-amp-kept"),
    ],
)
def test_clean_dangling_amp_after_query_cleanup(url: str, expected: str) -> None:
    assert ((cleaned := clean_url(url)), clean_url(cleaned) if cleaned is not None else None) == (expected, expected)


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
    "url",
    [
        pytest.param("https://[:::1]/", id="extra-compression-colon"),
        pytest.param("https://[::g]/", id="non-hex-group"),
        pytest.param("https://[1:2:3]/", id="too-few-groups"),
        pytest.param("https://[::1]x/", id="suffix-after-bracket"),
    ],
)
def test_clean_rejects_invalid_ipv6_hosts(url: str) -> None:
    assert clean_url(url) is None


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param("HTTP://LOCALHOST:/x", None, id="dotless-empty-port"),
        pytest.param("HTTP://LOCALHOST:80/x", None, id="dotless-http-default"),
        pytest.param("HTTPS://LOCALHOST:443/x", None, id="dotless-https-default"),
        pytest.param("HTTP://LOCALHOST:0080/x", None, id="dotless-padded-default"),
        pytest.param("HTTP://user.name@LOCALHOST:/x", None, id="userinfo-dot-is-not-host-dot"),
        pytest.param("HTTP://\u00ad:8000/x", None, id="mapped-empty-host"),
        pytest.param("HTTP://LOCALHOST:08000/a/../x", "http://localhost:8000/x", id="nondefault-port"),
        pytest.param("HTTP://LOCALHOST:80x/a/../x", None, id="invalid-port"),
        pytest.param("HTTP://[0:0:0:0:0:0:0:1]:80/a/../x", "http://[::1]/x", id="ipv6-default-port"),
        pytest.param("HTTP://[::1]:/a/../x", "http://[::1]/x", id="ipv6-empty-port"),
        pytest.param("HTTP://EXAMPLE.ORG:80/a/../x", "http://example.org/x", id="dotted-default-port"),
        pytest.param("HTTP://EXAMPLE.ORG:/a/../x", "http://example.org/x", id="dotted-empty-port"),
        pytest.param("HTTP://2130706433:80/a/../x", "http://127.0.0.1/x", id="ipv4-integer-host"),
        pytest.param("HTTP://EXAMPLE。ORG:80/x", "http://example.org/x", id="ideographic-dot"),
        pytest.param("HTTP://EXAMPLE\uff0eORG:/x", "http://example.org/x", id="fullwidth-dot"),
        pytest.param("HTTP://EXAMPLE｡ORG:80/x", "http://example.org/x", id="halfwidth-dot"),
        pytest.param("HTTP://user:pw@LOCALHOST:80/a/../x", "http://user:pw@localhost/x", id="userinfo-colon-kept"),
        pytest.param("HTTP://m\xfcnchen.de:80/a/../x", "http://xn--mnchen-3ya.de/x", id="latin1-host"),
        pytest.param("HTTP://\xe9:pw@LOCALHOST:80/a/../x", "http://%C3%A9:pw@localhost/x", id="latin1-userinfo-colon"),
        pytest.param("HTTP://\xe9@LOCALHOST:80/a/../x", None, id="latin1-userinfo-no-colon"),
        pytest.param("HTTP://\u0100:pw@LOCALHOST:80/a/../x", "http://%C4%80:pw@localhost/x", id="bmp-userinfo-colon"),
        pytest.param("HTTP://\u0100@LOCALHOST:80/a/../x", None, id="bmp-userinfo-no-colon"),
        pytest.param(
            "HTTP://\U00010000:pw@LOCALHOST:80/a/../x", "http://%F0%90%80%80:pw@localhost/x", id="astral-userinfo-colon"
        ),
        pytest.param("HTTP://\U00010000@LOCALHOST:80/a/../x", None, id="astral-userinfo-no-colon"),
        pytest.param("HTTP://a\u202eb/a/../x", None, id="bmp-host-low-byte-dot"),
        pytest.param("HTTP://a\U0001002eb/a/../x", None, id="astral-host-low-byte-dot"),
        pytest.param("HTTP://\u203a@LOCALHOST:80/a/../x", None, id="bmp-userinfo-low-byte-colon"),
        pytest.param("HTTP://\U0001003a@LOCALHOST:80/a/../x", None, id="astral-userinfo-low-byte-colon"),
        pytest.param("HTTP://a\ufffeb.EXAMPLE:80/a/../x", None, id="wide-host-dot"),
        pytest.param(
            "HTTP://u\x00:pw@LOCALHOST:80/a/../x", "http://u%00:pw@localhost/x", id="nul-before-userinfo-colon"
        ),
        pytest.param("HTTP://a\x00b.EXAMPLE:80/a/../x", None, id="nul-before-host-dot"),
        pytest.param("HTTP://:8000/a/../x", None, id="empty-host"),
    ],
)
def test_clean_canonical_host_shape(url: str, expected: str | None) -> None:
    assert ((cleaned := clean_url(url)), clean_url(cleaned) if cleaned is not None else None) == (expected, expected)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param("HTTP://LOCALHOST:/a/../x", "http://localhost/x", id="empty-port"),
        pytest.param("HTTPS://LOCALHOST:443/a/../x", "https://localhost/x", id="default-port"),
    ],
)
def test_normalize_keeps_dotless_canonical_host(url: str, expected: str) -> None:
    assert ((normalized := normalize_url(url)), normalize_url(normalized)) == (expected, expected)


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
        pytest.param("http://\udce9:p@a.com/", None, id="surrogate-in-username"),
        pytest.param("http://u:\udce9@a.com/", None, id="surrogate-in-password"),
        pytest.param("http://\udce9.com/", None, id="surrogate-in-host"),
    ],
)
def test_clean_handles_lone_surrogates(url: str, expected: str | None) -> None:
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
        pytest.param("http://x.com:0080/", "http://x.com/", id="leading-zeros-read-as-the-default"),
        pytest.param("http://x.com:0081/", "http://x.com:81/", id="leading-zeros-fall-away"),
        pytest.param("ftp://x.com", "ftp://x.com/", id="special-ftp-empty-path-is-root"),
        pytest.param("ws://x.com", "ws://x.com/", id="special-ws-empty-path-is-root"),
        pytest.param("wss://x.com", "wss://x.com/", id="special-wss-empty-path-is-root"),
        pytest.param("foo://x.com", "foo://x.com", id="non-special-empty-path-stays-empty"),
        pytest.param("http:/x", "http://x/", id="a-netloc-scheme-with-one-slash"),
        pytest.param("http:x", "http://x/", id="a-netloc-scheme-without-slashes"),
        pytest.param("http:///x", "http://x/", id="a-netloc-scheme-with-three-slashes"),
        pytest.param("http:////x", "http://x/", id="a-netloc-scheme-with-four-slashes"),
        pytest.param("http:example.com/path?x=1", "http://example.com/path?x=1", id="host-after-scheme"),
        pytest.param("http:", "http://", id="a-netloc-scheme-alone-gets-the-marker"),
        pytest.param("http:?q=1", "http://?q=1", id="hostless-query"),
        pytest.param("http:#f", "http://#f", id="hostless-fragment"),
        pytest.param("gopher:/a", "gopher:/a", id="non-special-hostless-path-keeps-its-shape"),
        pytest.param("git:/a", "git:/a", id="git-hostless-path-keeps-its-shape"),
        pytest.param("gopher:", "gopher:", id="non-special-empty-path-has-no-authority"),
        pytest.param("mailto://", "mailto://", id="non-special-empty-authority"),
        pytest.param("data://", "data://", id="data-empty-authority"),
        pytest.param("urn://", "urn://", id="urn-empty-authority"),
        pytest.param("mailto://?x=1", "mailto://?x=1", id="empty-authority-with-query"),
        pytest.param("custom:///", "custom:///", id="non-special-empty-authority-root"),
        pytest.param("data:/.//", "data:/.//", id="hostless-double-slash-path-keeps-its-guard"),
        pytest.param("data:/path/..//", "data:/.//", id="dot-segments-expose-a-hostless-double-slash"),
        pytest.param("data:/.//x", "data:/.//x", id="hostless-double-slash-path-with-segment"),
        pytest.param("data:////", "data:////", id="explicit-empty-authority-keeps-double-slash-path"),
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
        pytest.param("http://x.com//", "http://x.com/", id="a-slash-run-keeps-root"),
        pytest.param("http://x.com/a//", "http://x.com/a", id="a-slash-run-after-segment"),
        pytest.param("file://server//", "file://server/", id="a-file-root"),
        pytest.param("foo://server//", "foo://server/", id="a-non-special-root"),
        pytest.param("http://x.com/", "http://x.com/", id="the-root-stays"),
        pytest.param("http://x.com/a", "http://x.com/a", id="no-slash-to-trim"),
    ],
)
def test_trailing_slash_off(url: str, expected: str) -> None:
    options: Final = UrlCleaning(trailing_slash=False)
    assert ((normalized := normalize_url(url, options)), normalize_url(normalized, options)) == (expected, expected)


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


def test_extract_links_keeps_unicode_whitespace_in_base_path() -> None:
    assert extract_links('<base href="\u2028"><a href="?x=1">x</a>', "https://test.com/dir/page") == {
        "https://test.com/dir/%E2%80%A8?x=1"
    }


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        pytest.param("\\x", "http://site.org/x", id="root-backslash"),
        pytest.param("\\\\example.com\\p", "http://example.com/p", id="authority-backslashes"),
    ],
)
def test_extract_links_resolves_special_backslashes(href: str, expected: str) -> None:
    assert extract_links(f'<a href="{href}">x</a>', "http://site.org/dir/page") == {expected}


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
        pytest.param(
            "http://site.org/dir/page", "http:\\/example.com\\p", "http://example.com/p", id="reverse-mixed-opener"
        ),
        pytest.param("https://site.org/dir/page", "https:\\\\example.com\\p", "https://example.com/p", id="https"),
        pytest.param(
            "http://site.org/dir/page",
            "http:example.com/p",
            "http://site.org/dir/example.com/p",
            id="same-scheme-relative",
        ),
        pytest.param(
            "http://site.org/dir/page", "http:/example.com/p", "http://site.org/example.com/p", id="single-slash"
        ),
    ],
)
def test_extract_links_resolves_absolute_special_backslashes(base: str, href: str, expected: str) -> None:
    assert extract_links(f'<a href="{href}">x</a>', base) == {expected}


@pytest.mark.parametrize(
    "href",
    [
        pytest.param("hxtp://example.com/p", id="second-scheme-letter"),
        pytest.param("htxp://example.com/p", id="third-scheme-letter"),
        pytest.param("httx://example.com/p", id="fourth-scheme-letter"),
        pytest.param("httpx://example.com/p", id="missing-scheme-colon"),
        pytest.param("https:/", id="missing-authority"),
    ],
)
def test_extract_links_rejects_invalid_web_authorities(href: str) -> None:
    assert extract_links(
        f'<a href="{href}">x</a><a href="http://good.example/p">y</a>', "http://site.org/dir/page"
    ) == {"http://good.example/p"}


@pytest.mark.parametrize(
    "href",
    [
        pytest.param("///example.com/p", id="three-slashes"),
        pytest.param("////example.com/p", id="four-slashes"),
        pytest.param("//\\example.com/p", id="third-backslash"),
        pytest.param("///\\//\\//example.com/p", id="mixed-extra-slashes"),
    ],
)
def test_extract_links_resolves_extra_special_slashes(href: str) -> None:
    assert extract_links(f'<a href="{href}">x</a>', "http://site.org/dir/page") == {"http://example.com/p"}


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        pytest.param("/", "http://site.org/", id="root-path"),
        pytest.param("//example.com/p", "http://example.com/p", id="two-slashes"),
        pytest.param("/\\/example.com/p", "http://example.com/p", id="mixed-three-slashes"),
    ],
)
def test_extract_links_keeps_special_slash_boundaries(href: str, expected: str) -> None:
    assert extract_links(f'<a href="{href}">x</a>', "http://site.org/dir/page") == {expected}


def test_a_longer_hreflang_subtag_is_another_language() -> None:
    html = '<a href="https://test.com/a" hreflang="deu">x</a>'
    assert extract_links(html, "https://test.com/", UrlCleaning(language="de")) == set()


def test_an_unsplittable_base_raises() -> None:
    with pytest.raises(ValueError, match="Invalid IPv6 URL"):
        extract_links('<a href="https://a.example/">x</a>', "http://[::1", external_only=True)


def test_a_relative_link_against_an_unsplittable_base_raises() -> None:
    with pytest.raises(ValueError, match="Invalid IPv6 URL"):
        extract_links('<a href="p">x</a>', "http://[::1")


def test_an_unsplittable_relative_link_raises() -> None:
    with pytest.raises(ValueError, match="Invalid IPv6 URL"):
        extract_links('<a href="///[bad">x</a>', "https://a.com/")


def test_a_base_element_that_cannot_resolve_uses_fallback() -> None:
    assert extract_links('<base href="http://[::1"><a href="p">x</a>', "https://a.com/") == {"https://a.com/p"}


def test_a_relative_href_against_an_unsplittable_base_element_is_dropped() -> None:
    assert extract_links('<base href="http://[::1"><a href="p">x</a>') == set()


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
        pytest.param("http://[::1]:80/", "http://[::1]/", id="ipv6-default-port"),
        pytest.param("http://[::1]:8080/", "http://[::1]:8080/", id="ipv6-other-port"),
        pytest.param("http://[::1]:/", "http://[::1]/", id="ipv6-empty-port"),
        pytest.param("http://user:pw@Example.ORG/x", "http://user:pw@example.org/x", id="userinfo-colon-preserved"),
        pytest.param("http://user:pa ss@Example.ORG/x", "http://user:pa%20ss@example.org/x", id="password-space"),
        pytest.param("http://us:er:pa@Example.ORG/x", "http://us:er%3Apa@example.org/x", id="password-colon"),
        pytest.param("http://a@b@Example.ORG/x", "http://a%40b@example.org/x", id="repeated-at-sign"),
        pytest.param("http://user:@Example.ORG/x", "http://user@example.org/x", id="empty-password"),
        pytest.param("http://:@Example.ORG/x", "http://example.org/x", id="empty-credentials"),
        pytest.param("http://u%3As:p%40w@Example.ORG/x", "http://u%3As:p%40w@example.org/x", id="existing-escapes"),
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
        pytest.param("http://x.org/a^b", "http://x.org/a%5Eb", id="caret-encoded-in-path"),
        pytest.param("http://x.org/a{b}", "http://x.org/a%7Bb%7D", id="curly-braces-encoded-in-path"),
        pytest.param("http://x.org/x?q=a b#f g", "http://x.org/x?q=a%20b#f%20g", id="query-and-fragment-encoded"),
        pytest.param("foo://x.org/x?q=a'b", "foo://x.org/x?q=a'b", id="non-special-query-apostrophe-kept"),
        pytest.param("foo://x.org/x?q=☃'b", "foo://x.org/x?q=%E2%98%83'b", id="non-ascii-query-apostrophe-kept"),
        pytest.param("http://x.org/x?q=a'b", "http://x.org/x?q=a%27b", id="special-query-apostrophe-encoded"),
        pytest.param("ftp://x.org/x?q=a'b", "ftp://x.org/x?q=a%27b", id="ftp-query-apostrophe-encoded"),
        pytest.param("file:///x?q=a'b", "file:///x?q=a%27b", id="file-query-apostrophe-encoded"),
        pytest.param("ws://x.org/x?q=a'b", "ws://x.org/x?q=a%27b", id="ws-query-apostrophe-encoded"),
        pytest.param("wss://x.org/x?q=a'b", "wss://x.org/x?q=a%27b", id="wss-query-apostrophe-encoded"),
        pytest.param("foo://x.org/x?q=a%27b", "foo://x.org/x?q=a%27b", id="existing-query-escape-kept"),
        pytest.param("http://x.org/x#a{b}", "http://x.org/x#a{b}", id="fragment-set-looser-than-path"),
        pytest.param("mailto:someone@example.org", "mailto:someone@example.org", id="opaque-scheme-untouched"),
        pytest.param("foo:hello world", "foo:hello world", id="opaque-path-keeps-interior-space"),
        pytest.param("foo:hello<>world", "foo:hello<>world", id="opaque-path-keeps-angle-brackets"),
        pytest.param("foo:hello`{world}", "foo:hello`{world}", id="opaque-path-keeps-printable-ascii"),
        pytest.param("foo:hello ?x", "foo:hello%20?x", id="opaque-path-encodes-space-before-query"),
        pytest.param("foo:hello #x", "foo:hello%20#x", id="opaque-path-encodes-space-before-fragment"),
        pytest.param("foo:hello%2f", "foo:hello%2F", id="opaque-path-normalizes-escape"),
        pytest.param("foo:a\x01b", "foo:a%01b", id="opaque-path-encodes-control"),
        pytest.param("foo:é", "foo:%C3%A9", id="opaque-path-encodes-non-ascii"),
        pytest.param("foo:", "foo:", id="empty-opaque-path"),
        pytest.param("foo:hello ?", "foo:hello%20", id="empty-query-marker-keeps-path-encoding"),
        pytest.param("foo:/hello world", "foo:/hello%20world", id="hierarchical-path-encodes-space"),
        pytest.param("foo:/a/../b", "foo:/b", id="hostless-non-special-dot-segment"),
        pytest.param("foo:/a/%2e%2e/b?x=1#f", "foo:/b?x=1#f", id="hostless-escaped-dot-segment"),
        pytest.param("mailto:/a/../b", "mailto:/b", id="hostless-mailto-dot-segment"),
        pytest.param("foo:a/../b", "foo:a/../b", id="opaque-dot-segment-untouched"),
        pytest.param("page.html?b=2&a=1", "page.html?a=1&b=2", id="relative-url-keeps-shape"),
    ],
)
def test_normalize_spec_behaviors(url: str, expected: str) -> None:
    assert normalize_url(url) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param("http://[u@example.org/x", "http://%5Bu@example.org/x", id="open-in-username"),
        pytest.param("http://]u@example.org/x", "http://%5Du@example.org/x", id="close-in-username"),
        pytest.param("http://[u]@example.org/x", "http://%5Bu%5D@example.org/x", id="paired-in-username"),
        pytest.param("http://u]@[::1]/x", "http://u%5D@[::1]/x", id="close-in-username-ipv6-host"),
    ],
)
def test_normalize_encodes_userinfo_brackets(url: str, expected: str) -> None:
    assert normalize_url(url) == expected


def test_normalize_rejects_unclosed_host_bracket_after_userinfo() -> None:
    with pytest.raises(ValueError, match="Invalid IPv6 URL"):
        normalize_url("http://]u@[::1/x")


@pytest.mark.parametrize("url", ["http://[::1]x/", "git://[::1]\\.."])
def test_normalize_rejects_ipv6_host_tail(url: str) -> None:
    with pytest.raises(ValueError, match="Invalid IPv6 URL"):
        normalize_url(url)


def test_clean_accepts_unpaired_userinfo_bracket() -> None:
    assert clean_url("http://[u@example.org/x") == "http://%5Bu@example.org/x"


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


@pytest.mark.parametrize("normalizer", [normalize_url, clean_url], ids=["normalize", "clean"])
@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            "https://example.org/#a=o'k&b=2",
            "https://example.org/#a=o'k&b=2",
            id="apostrophe-kept",
        ),
        pytest.param(
            "https://example.org/#a=`&b=2",
            "https://example.org/#a=%60&b=2",
            id="backtick-encoded",
        ),
        pytest.param(
            "https://example.org/#a=foo#bar&b=2",
            "https://example.org/#a=foo#bar&b=2",
            id="hash-kept",
        ),
        pytest.param(
            "https://example.org/#a=o%27k&b=2",
            "https://example.org/#a=o%27k&b=2",
            id="existing-escape-kept",
        ),
        pytest.param(
            "https://example.org/#b=2&a=1",
            "https://example.org/#a=1&b=2",
            id="pairs-sorted",
        ),
        pytest.param(
            "https://example.org/#utm_source=x&a=1",
            "https://example.org/#a=1",
            id="tracker-removed",
        ),
    ],
)
def test_query_shaped_fragment_uses_fragment_encoding(
    normalizer: Callable[[str], str | None], source: str, expected: str
) -> None:
    assert normalizer(source) == expected


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


@pytest.mark.parametrize(
    "url",
    [
        pytest.param("http://[::1/x", id="explicit-authority"),
        pytest.param("http:[::1/x", id="inferred-authority"),
    ],
)
def test_normalize_rejects_unsplittable_input(url: str) -> None:
    with pytest.raises(ValueError, match="Invalid IPv6 URL"):
        normalize_url(url)


@pytest.mark.parametrize(
    "url",
    [
        pytest.param("http://a.com/\udce9", id="path"),
        pytest.param("http://\udce9:p@a.com/", id="username"),
        pytest.param("http://u:\udce9@a.com/", id="password"),
        pytest.param("foo:\udce9", id="opaque"),
    ],
)
def test_normalize_rejects_lone_surrogate(url: str) -> None:
    # a lone surrogate has no UTF-8 form, so the component cannot be percent-encoded and the URL is not serializable
    with pytest.raises(ValueError, match="cannot be percent-encoded"):
        normalize_url(url)


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
        pytest.param("https://example.org:443/a", id="default-port"),
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


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param("http://2130706433/", "http://127.0.0.1/", id="decimal"),
        pytest.param("http://0x7f.0.0.1/", "http://127.0.0.1/", id="hex-label"),
        pytest.param("http://0X7F.0.0.1/", "http://127.0.0.1/", id="uppercase-hex-label"),
        pytest.param("http://0177.0.0.1/", "http://127.0.0.1/", id="octal-label"),
        pytest.param("http://127.1/", "http://127.0.0.1/", id="short-form"),
        pytest.param("http://1.256/", "http://1.0.1.0/", id="two-part-wide-last"),
        pytest.param("http://1.2.3.4/", "http://1.2.3.4/", id="dotted-quad-unchanged"),
        pytest.param("http://1.2.3.4./", "http://1.2.3.4/", id="trailing-dot-dropped"),
        pytest.param("http://0x.0.0.0/", "http://0.0.0.0/", id="empty-after-hex-prefix"),
        pytest.param("http://12345/", "http://0.0.48.57/", id="bare-number-fills-octets"),
        pytest.param("http://[0:0:0:0:0:0:0:1]/", "http://[::1]/", id="expanded-loopback"),
        pytest.param("http://[2001:db8::1]/", "http://[2001:db8::1]/", id="already-compressed"),
        pytest.param(
            "http://[2001:0db8:0000:0000:0000:0000:0000:0001]/", "http://[2001:db8::1]/", id="leading-zeros-and-run"
        ),
        pytest.param("http://[2001:DB8::AB]/", "http://[2001:db8::ab]/", id="uppercase-hex-lowercased"),
        pytest.param("http://[1:2:3:4:5:6:7:8]/", "http://[1:2:3:4:5:6:7:8]/", id="no-zero-run"),
        pytest.param("http://[1:0:2:0:3:0:4:0]/", "http://[1:0:2:0:3:0:4:0]/", id="isolated-zeros-not-compressed"),
        pytest.param("http://[fe80:0:0:0:0:0:0:0]/", "http://[fe80::]/", id="trailing-zero-run"),
        pytest.param("http://[2001:db8:0:0:1:0:0:1]/", "http://[2001:db8::1:0:0:1]/", id="first-longest-run-wins"),
        pytest.param("http://[::]/", "http://[::]/", id="all-zeros"),
        pytest.param("http://[::ffff:192.168.0.1]/", "http://[::ffff:c0a8:1]/", id="embedded-ipv4"),
        pytest.param("http://[2001:db8:0:0:0:0:192.0.2.1]/", "http://[2001:db8::c000:201]/", id="full-ipv4-tail"),
        pytest.param("http://[::ffff:0.0.0.0]/", "http://[::ffff:0:0]/", id="zero-ipv4-tail"),
        pytest.param("http://[::ffff:255.255.255.255]/", "http://[::ffff:ffff:ffff]/", id="max-ipv4-tail"),
        pytest.param("http://good%2eexample/", "http://good.example/", id="percent-dot"),
        pytest.param(
            "http://trusted.example%2eattacker.example/", "http://trusted.example.attacker.example/", id="split-label"
        ),
        pytest.param("http://caf%C3%A9.example/", "http://xn--caf-dma.example/", id="percent-utf8-to-punycode"),
        pytest.param("file://12/p", "file://0.0.0.12/p", id="file-numeric-host"),
    ],
)
def test_normalize_url_canonicalizes_hosts(url: str, expected: str) -> None:
    # the WHATWG host parser percent-decodes the host, reads one ending in a number as IPv4 in any of its four
    # notations, and serializes IPv6 without leading zeros and with the first longest zero run compressed
    assert normalize_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        pytest.param("http://999.999.999.999/", id="octet-over-255"),
        pytest.param("http://256.1.1.1/", id="first-octet-over-255"),
        pytest.param("http://1.2.3.256/", id="last-octet-over-limit"),
        pytest.param("http://1.2.3.4.5/", id="five-parts"),
        pytest.param("http://1.2.3.4.5.6/", id="too-many-parts"),
        pytest.param("http://0888.1.1.1/", id="bad-octal-digit"),
        pytest.param("http://99999999999/", id="number-over-32-bits"),
        pytest.param("http://foo.0x1/", id="non-numeric-leading-part"),
        pytest.param("http://1..2.3/", id="empty-interior-part"),
        pytest.param("http://[::ffff:192.168.00.1]/", id="ipv4-tail-leading-zero"),
        pytest.param("http://[::ffff:256.1.1.1]/", id="ipv4-tail-out-of-range"),
        pytest.param("http://[1:2:3:4:5:6:7:1.2.3.4]/", id="ipv4-tail-too-many-pieces"),
        pytest.param("http://[::ffff:1.2.3]/", id="ipv4-tail-too-few-parts"),
        pytest.param("http://[::ffff:1.2.3.]/", id="ipv4-tail-empty-part"),
        pytest.param("http://[::ffff:1.2.3.-4]/", id="ipv4-tail-negative-part"),
        pytest.param("http://[::ffff:1.2.3.x]/", id="ipv4-tail-letter-part"),
        pytest.param("http://[::ffff:1.2.3:4]/", id="ipv4-tail-missing-dot"),
        pytest.param("http://[::ffff:1.2.3.4x]/", id="ipv4-tail-extra-suffix"),
        pytest.param("http://[::1x]/", id="trailing-non-hex"),
        pytest.param("http://[1:2:3]/", id="too-few-groups"),
        pytest.param("http://[:::1]/", id="double-compressor"),
        pytest.param("http://[:1]/", id="leading-single-colon"),
        pytest.param("http://[:]/", id="lone-colon"),
        pytest.param("http://[]/", id="empty-brackets"),
        pytest.param("http://[::g]/", id="non-hex-group"),
        pytest.param("http://[::1:]/", id="trailing-colon"),
        pytest.param("http://[1:2:3:4:5:6:7:8:9]/", id="nine-groups"),
        pytest.param("file://[example]/", id="file-invalid-ipv6"),
    ],
)
def test_normalize_url_keeps_hosts_it_cannot_parse(url: str) -> None:
    assert normalize_url(url) == url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param("https://example.org/a ", "https://example.org/a", id="trailing-space"),
        pytest.param("https://example.org/a\x00", "https://example.org/a", id="trailing-null"),
        pytest.param(" \x01https://example.org/a\x1f ", "https://example.org/a", id="both-edges"),
        pytest.param("https://example.org/a ?x=1", "https://example.org/a%20?x=1", id="interior-space"),
        pytest.param("https://example.org/a#f ", "https://example.org/a#f", id="fragment-edge"),
        pytest.param("foo:hello ", "foo:hello", id="opaque-path-edge"),
        pytest.param("https://example.org/a\x7f", "https://example.org/a%7F", id="del-not-c0"),
    ],
)
def test_normalize_url_trims_input_edges(url: str, expected: str) -> None:
    assert normalize_url(url) == expected


@pytest.mark.parametrize(
    "host",
    [
        pytest.param(f"a%{code:02X}b.example", id=f"encoded-{code:02x}")
        for code in (*range(0x21), 0x23, 0x25, 0x2F, 0x3A, 0x3C, 0x3E, 0x3F, 0x40, 0x5B, 0x5C, 0x5D, 0x5E, 0x7C, 0x7F)
    ]
    + [
        pytest.param("\uff05\uff10\uff10", id="mapped-null-escape"),
        pytest.param("\uff05\uff14\uff11", id="mapped-letter-escape"),
        pytest.param("a\uff03b.example", id="mapped-fragment-delimiter"),
        pytest.param("a\uff1ab.example", id="mapped-port-delimiter"),
        pytest.param("a\uff20b.example", id="mapped-userinfo-delimiter"),
    ],
)
def test_normalize_rejects_forbidden_domain(host: str) -> None:
    with pytest.raises(ValueError, match="host contains a forbidden domain code point"):
        normalize_url(f"http://{host}/")


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("//\uff05\uff10\uff10", id="mapped-null"),
        pytest.param("http://o%23", id="fragment"),
        pytest.param("http://o%3F", id="query"),
        pytest.param("//\uff05\uff14\uff11", id="mapped-escape"),
        pytest.param("file://%3A", id="port"),
        pytest.param("//%09t", id="tab"),
    ],
)
def test_normalize_rejects_saved_reparse_findings(source: str) -> None:
    with pytest.raises(ValueError, match="host contains a forbidden domain code point"):
        normalize_url(source)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("http://good%2eexample/", "http://good.example/", id="decoded-dot"),
        pytest.param("http://%41.example/", "http://a.example/", id="decoded-letter"),
        pytest.param("http://caf%C3%A9.example/", "http://xn--caf-dma.example/", id="decoded-unicode"),
        pytest.param("http://a\u0085b.example/", "http://a\u0085b.example/", id="retained-c1-fallback"),
        pytest.param("http://a\u2028b.example/", "http://a\u2028b.example/", id="retained-disallowed-fallback"),
        pytest.param("file:///", "file:///", id="empty-file-host"),
        pytest.param("http://[::1]/", "http://[::1]/", id="ipv6"),
        pytest.param("http://1g/", "http://1g/", id="numeric-prefix-fallback"),
        pytest.param("http://-a/", "http://-a/", id="leading-hyphen-domain"),
    ],
)
def test_normalize_host_valid_and_fallback_forms(source: str, expected: str) -> None:
    normalized: Final = normalize_url(source)
    assert (normalized, normalize_url(normalized)) == (expected, expected)


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("https://user@", id="special-user"),
        pytest.param("https://@/path", id="special-empty-user"),
        pytest.param("git://user@?query", id="opaque-user"),
        pytest.param("git://@", id="opaque-empty-user"),
    ],
)
def test_normalize_rejects_credentials_without_host(source: str) -> None:
    with pytest.raises(ValueError, match="missing host after credentials"):
        normalize_url(source)


def test_normalize_accepts_credentials_with_host() -> None:
    assert normalize_url("git://user@host/path") == "git://user@host/path"


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("http://a%23b.example/", id="decoded-fragment"),
        pytest.param("http://a\uff05\uff14\uff11.example/", id="mapped-escape"),
    ],
)
def test_clean_rejects_forbidden_domain(source: str) -> None:
    assert clean_url(source) is None


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("file://\u00ad/p", id="file-unicode"),
        pytest.param("file://%C2%AD/p", id="file-encoded"),
        pytest.param("http://%C2%AD/p", id="web-encoded"),
    ],
)
def test_normalize_rejects_host_mapped_to_empty(source: str) -> None:
    with pytest.raises(ValueError, match="host maps to an empty domain"):
        normalize_url(source)


def test_clean_rejects_host_mapped_to_empty() -> None:
    assert clean_url("http://%C2%AD/p") is None


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("http://192.168.0.257/", id="ipv4-octet-out-of-range"),
        pytest.param("http://foo.09/", id="invalid-octal-last-label"),
        pytest.param("http://foo.09./", id="invalid-octal-trailing-dot"),
        pytest.param("http://foo.0x/", id="hex-last-label-after-domain"),
        pytest.param("http://foo.0xa/", id="hex-digit-last-label-after-domain"),
        pytest.param("http://0x/", id="incomplete-hex-host"),
        pytest.param("https://example.com%80/", id="invalid-utf8-domain"),
        pytest.param("http://\ufdd0zyx.com/", id="noncharacter-domain"),
    ],
)
def test_clean_rejects_unparseable_web_host(source: str) -> None:
    assert clean_url(source) is None


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("http://ExAmPlE.Org/", "http://example.org/", id="mixed-case"),
        pytest.param("http://a0.com/", "http://a0.com/", id="digit"),
        pytest.param("http://a-b.com/", "http://a-b.com/", id="hyphen"),
        pytest.param("http://a_b.com/", "http://a_b.com/", id="underscore"),
        pytest.param("http://a..com/", "http://a..com/", id="empty-interior-label"),
    ],
)
def test_clean_canonicalizes_simple_host(source: str, expected: str) -> None:
    assert clean_url(source) == expected


def test_clean_rejects_encoded_space_in_host() -> None:
    assert clean_url("http://a%20b.com/") is None


def test_clean_keeps_brace_in_host() -> None:
    assert clean_url("http://a.com{/") == "http://a.com{/"


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("http://./", id="single-dot-host"),
        pytest.param("http://foo.-/", id="hyphen-label"),
        pytest.param("http://foo.-1/", id="hyphen-before-digit"),
        pytest.param("http://foo.a1/", id="letter-before-digit"),
        pytest.param("http://foo.a/", id="single-hex-letter"),
        pytest.param("http://foo.0a/", id="nonhex-zero-label"),
        pytest.param("http://foo.0xg/", id="nonhex-last-label"),
        pytest.param("http://foo.0xz1/", id="nonhex-inside-label"),
    ],
)
def test_clean_keeps_non_numeric_last_label(source: str) -> None:
    assert clean_url(source) == source


def test_clean_keeps_empty_last_label_before_trailing_dot() -> None:
    assert clean_url("http://foo../") == "http://foo../"


def test_external_links_reject_forbidden_base_domain() -> None:
    with pytest.raises(ValueError, match="host contains a forbidden domain code point"):
        extract_links("<a href='https://valid.example/'>link</a>", "http://a%23b.example/", external_only=True)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param("file:", "file:///", id="empty"),
        pytest.param("file://", "file:///", id="empty-authority"),
        pytest.param("file:\\", "file:///", id="single-backslash"),
        pytest.param("file:\\\\", "file:///", id="double-backslash"),
        pytest.param("file:\\\\\\", "file:///", id="three-backslashes"),
        pytest.param("file:\\\\\\\\", "file:////", id="four-backslashes"),
        pytest.param("file:/\\", "file:///", id="mixed-opener"),
        pytest.param("file:\\/", "file:///", id="reversed-opener"),
        pytest.param("FILE://SERVER/share\\a?x=1#part", "file://server/share/a?x=1#part", id="unc-path"),
        pytest.param("file:\\\\SERVER\\share\\a?x=1#part", "file://server/share/a?x=1#part", id="unc-backslash-opener"),
        pytest.param("file:///C:/a\\b?x=1#part", "file:///C:/a/b?x=1#part", id="absolute-drive"),
        pytest.param("FILE:///a/%5C/b?x=1#part", "file:///a/%5C/b?x=1#part", id="encoded-backslash"),
        pytest.param("FILE:///a/b?x=1#part", "file:///a/b?x=1#part", id="forward-slashes"),
        pytest.param("file:///a\\\u0100", "file:///a/%C4%80", id="two-byte-unicode"),
        pytest.param("file:///a\\\U0001f600", "file:///a/%F0%9F%98%80", id="four-byte-unicode"),
        pytest.param("file:///a\\\x00b", "file:///a/%00b", id="embedded-null"),
        pytest.param("file:test", "file:///test", id="hostless-path"),
        pytest.param("file:..", "file:///", id="hostless-parent"),
        pytest.param("file:.//p", "file:////p", id="hostless-double-slash"),
        pytest.param("file:C|/m/", "file:///C:/m/", id="hostless-drive-bar"),
        pytest.param("file:\\C|/m/", "file:///C:/m/", id="hostless-backslash-drive-bar"),
        pytest.param("file:C||/m/", "file:///C||/m/", id="hostless-non-drive"),
        pytest.param("file://server\\C|/m", "file://server/C:/m", id="hosted-backslash-drive-bar"),
        pytest.param("FIZZ://EXAMPLE.ORG/a\\b?x=1#part", "fizz://EXAMPLE.ORG/a\\b?x=1#part", id="f-prefixed-non-file"),
        pytest.param("HTTPS://EXAMPLE.ORG/a\\b?x=1#part", "https://example.org/a/b?x=1#part", id="web-path-policy"),
        pytest.param(
            "CUSTOM://EXAMPLE.ORG/a\\b?x=1#part", "custom://EXAMPLE.ORG/a\\b?x=1#part", id="custom-path-policy"
        ),
    ],
)
def test_normalize_file_path(url: str, expected: str) -> None:
    normalized: Final = normalize_url(url)
    assert (normalized, normalize_url(normalized)) == (expected, expected)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param("file:///C|/foo", "file:///C:/foo", id="absolute-drive"),
        pytest.param("file:///c|", "file:///c:", id="lowercase-drive"),
        pytest.param("file:/C|/foo", "file:///C:/foo", id="single-slash"),
        pytest.param("file:///C|\\foo", "file:///C:/foo", id="backslash-separator"),
        pytest.param("file:///C|?x=1", "file:///C:?x=1", id="query-after-drive"),
        pytest.param("file:///C|/\u0100", "file:///C:/%C4%80", id="two-byte-unicode"),
        pytest.param("file:///C|/\U0001f600", "file:///C:/%F0%9F%98%80", id="four-byte-unicode"),
        pytest.param("file:///C|foo", "file:///C|foo", id="longer-segment"),
        pytest.param("file:///1|/foo", "file:///1|/foo", id="non-letter"),
        pytest.param("FILE:ab|\\foo", "file:///ab|/foo", id="relative-file-path"),
    ],
)
def test_normalize_file_drive_separator(url: str, expected: str) -> None:
    normalized: Final = normalize_url(url)
    assert (normalized, normalize_url(normalized)) == (expected, expected)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param("file://localhost/C:/foo", "file:///C:/foo", id="localhost"),
        pytest.param("file://LOCALHOST/x", "file:///x", id="uppercase"),
        pytest.param("file://%6cocalhost/x", "file:///x", id="encoded"),
        pytest.param("file://localhost./x", "file://localhost./x", id="trailing-dot"),
        pytest.param("file://localhosx/x", "file://localhosx/x", id="same-length-host"),
    ],
)
def test_normalize_file_localhost_host(url: str, expected: str) -> None:
    normalized: Final = normalize_url(url)
    assert (normalized, normalize_url(normalized)) == (expected, expected)


def test_normalize_file_root_survives_trailing_slash_option() -> None:
    assert normalize_url("FILE:", UrlCleaning(trailing_slash=False)) == "file:///"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param("file:///C:/../x", "file:///C:/x", id="drive-root-parent"),
        pytest.param("file:///C:/a/../../x", "file:///C:/x", id="drive-root-nested-parent"),
        pytest.param("file:///C:/../C:/x", "file:///C:/C:/x", id="repeated-drive-segment"),
        pytest.param("file:///C:/%2e%2e/x", "file:///C:/x", id="drive-root-escaped-parent"),
        pytest.param("file:///C:/..", "file:///C:/", id="drive-root-trailing-parent"),
        pytest.param("file:///C:", "file:///C:", id="drive-root-without-child"),
        pytest.param("file:///", "file:///", id="short-file-root"),
        pytest.param("file:///..", "file:///", id="short-parent-path"),
        pytest.param("file:///C:foo", "file:///C:foo", id="long-first-segment-without-dot"),
        pytest.param("file:///C:foo/../x", "file:///x", id="long-first-segment-with-parent"),
        pytest.param("file://server/C:/../x", "file://server/C:/x", id="unc-drive-root-parent"),
        pytest.param("file:///CC:/../x", "file:///x", id="long-segment-is-not-drive"),
        pytest.param("file:///4:/../x", "file:///x", id="digit-is-not-drive"),
        pytest.param("file:///Ca/../x", "file:///x", id="missing-drive-colon"),
        pytest.param("foo:/C:/../x", "foo:/x", id="other-scheme-does-not-protect-drive"),
        pytest.param("file:ab:c/foo", "file:///ab:c/foo", id="colon-without-leading-slash"),
    ],
)
def test_normalize_file_keeps_drive_root(url: str, expected: str) -> None:
    normalized: Final = normalize_url(url)
    assert (normalized, normalize_url(normalized)) == (expected, expected)


@pytest.mark.parametrize(
    "url",
    [
        pytest.param("file://user@server/share", id="username"),
        pytest.param("file://user:pass@server/share", id="password"),
        pytest.param("file://@server/share", id="empty-username"),
        pytest.param("file://user@localhost/share", id="local-host"),
    ],
)
def test_normalize_file_rejects_userinfo(url: str) -> None:
    with pytest.raises(ValueError, match="file URL cannot contain userinfo"):
        normalize_url(url)


def test_normalize_file_keeps_at_in_path() -> None:
    assert normalize_url("file://server/share@name") == "file://server/share@name"


@pytest.mark.parametrize(
    "url",
    [
        pytest.param("file:///a\udce9", id="unchanged-path-surrogate"),
        pytest.param("file:///a\\\udce9", id="rewritten-path-surrogate"),
    ],
)
def test_normalize_file_rejects_surrogate(url: str) -> None:
    with pytest.raises(ValueError, match="cannot be percent-encoded"):
        normalize_url(url)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param("https://example.org/a\\b", "https://example.org/a/b", id="https-path"),
        pytest.param("http://example.org\\a", "http://example.org/a", id="http-authority-boundary"),
        pytest.param("ftp://example.org/a\\b", "ftp://example.org/a/b", id="ftp-path"),
        pytest.param("ws://example.org/a\\b", "ws://example.org/a/b", id="ws-path"),
        pytest.param("wss://example.org/a\\b", "wss://example.org/a/b", id="wss-path"),
        pytest.param("https://example.org/a\\..\\b", "https://example.org/b", id="parent-segment"),
        pytest.param(
            "https://example.org/a\\b?x=\\y#z\\w",
            "https://example.org/a/b?x=\\y#z\\w",
            id="query-and-fragment-remain-separate",
        ),
        pytest.param("https://example.org/a%5Cb", "https://example.org/a%5Cb", id="encoded-reverse-solidus"),
        pytest.param("foo://example.org/a\\b", "foo://example.org/a\\b", id="non-special-path"),
    ],
)
def test_normalize_special_path_reverse_solidus(url: str, expected: str) -> None:
    normalized: Final = normalize_url(url)
    assert (normalized, normalize_url(normalized)) == (expected, expected)


def test_clean_special_path_reverse_solidus() -> None:
    assert clean_url("https://example.org/a\\b") == "https://example.org/a/b"


@pytest.mark.parametrize(
    "normalizer",
    [pytest.param(normalize_url, id="normalize"), pytest.param(clean_url, id="clean")],
)
@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param("http://example.com:0080/", "http://example.com/", id="ascii-default-port"),
        pytest.param("http://example.com:65535/", "http://example.com:65535/", id="maximum-port"),
        pytest.param("http://example.com:00081/", "http://example.com:81/", id="leading-zeros"),
    ],
)
def test_normalize_valid_port(normalizer: Callable[[str], str | None], url: str, expected: str) -> None:
    assert normalizer(url) == expected


@pytest.mark.parametrize(
    "normalizer",
    [pytest.param(normalize_url, id="normalize"), pytest.param(clean_url, id="clean")],
)
@pytest.mark.parametrize(
    ("port", "suffix"),
    [
        pytest.param("0080", "", id="ascii-default-control"),
        pytest.param("0" * 20 + "80", "", id="padded-default"),
        pytest.param("0" * 4301 + "80", "", id="padded-default-past-python-limit"),
        pytest.param("0" * 4301 + "1", ":1", id="padded-nondefault-past-python-limit"),
        pytest.param("0" * 4301, ":0", id="zero-past-python-limit"),
    ],
)
def test_normalize_long_ascii_port(normalizer: Callable[[str], str | None], port: str, suffix: str) -> None:
    assert normalizer(f"http://example.com:{port}/") == f"http://example.com{suffix}/"


@pytest.mark.parametrize(
    "port",
    [
        pytest.param("80x", id="ascii-suffix"),
        pytest.param("!", id="punctuation"),
        pytest.param("८०", id="non-ascii-decimal"),
        pytest.param("①", id="non-decimal-digit"),
        pytest.param("65536", id="out-of-range"),
        pytest.param("9" * 19, id="long-out-of-range"),
        pytest.param("0" * 4301 + "9" * 20, id="padded-out-of-range"),
    ],
)
def test_normalize_rejects_invalid_port(port: str) -> None:
    with pytest.raises(ValueError, match="invalid URL port"):
        normalize_url(f"http://example.com:{port}/")


@pytest.mark.parametrize(
    "port",
    [
        pytest.param("80x", id="ascii-suffix"),
        pytest.param("८०", id="non-ascii-decimal"),
        pytest.param("65536", id="out-of-range"),
        pytest.param("9" * 4301, id="long-out-of-range"),
    ],
)
def test_clean_rejects_invalid_port(port: str) -> None:
    assert clean_url(f"http://example.com:{port}/") is None


@pytest.mark.parametrize(
    ("url", "error"),
    [
        pytest.param("http://:80/x", "URL port requires a host", id="special-empty-host"),
        pytest.param("git://:80/x", "URL port requires a host", id="opaque-empty-host"),
        pytest.param("git://example.com:80x/x", "invalid URL port", id="opaque-invalid-port"),
        pytest.param("git://example.com:65536/x", "invalid URL port", id="opaque-out-of-range"),
    ],
)
def test_normalize_rejects_invalid_port_authority(url: str, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        normalize_url(url)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("git://EXAMPLE.ORG/a", "git://EXAMPLE.ORG/a", id="ascii-case"),
        pytest.param("git://münchen.de/a", "git://m%C3%BCnchen.de/a", id="latin1-host"),
        pytest.param("git://Ā.example/a", "git://%C4%80.example/a", id="bmp-host"),
        pytest.param("git://中.example/a", "git://%E4%B8%AD.example/a", id="three-byte-host"),
        pytest.param("git://😀.example/a", "git://%F0%9F%98%80.example/a", id="astral-host"),
        pytest.param("git://0/a", "git://0/a", id="numeric-host"),
        pytest.param("git://exa%6dple.org/a", "git://exa%6dple.org/a", id="existing-escape"),
        pytest.param("git://exa%2fple.org/a", "git://exa%2fple.org/a", id="escaped-slash"),
        pytest.param("git://exa%aaple.org/a", "git://exa%aaple.org/a", id="escaped-non-ascii"),
        pytest.param("git://münchen%aa.de/a", "git://m%C3%BCnchen%aa.de/a", id="unicode-and-escape"),
        pytest.param("git://a\x01b/a", "git://a%01b/a", id="c0-control"),
        pytest.param("git://a\x7fb/a", "git://a%7Fb/a", id="del-control"),
        pytest.param("git://[0:0::1]/a", "git://[::1]/a", id="ipv6-literal"),
    ],
)
def test_normalize_opaque_host(source: str, expected: str) -> None:
    normalized: Final = normalize_url(source)
    assert (normalized, normalize_url(normalized)) == (expected, expected)


@pytest.mark.parametrize(
    "host",
    [
        pytest.param("a b", id="space"),
        pytest.param("a^b", id="caret"),
        pytest.param("a\x00b", id="null"),
    ],
)
def test_normalize_rejects_forbidden_opaque_host(host: str) -> None:
    with pytest.raises(ValueError, match="host contains a forbidden host code point"):
        normalize_url(f"git://{host}/")


def test_normalize_rejects_surrogate_opaque_host() -> None:
    with pytest.raises(ValueError, match="cannot be percent-encoded"):
        normalize_url("git://a\udcff/")


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param("file://C:/foo", "file:///C:/foo", id="colon-drive"),
        pytest.param("file://C|/foo", "file:///C:/foo", id="bar-drive"),
        pytest.param("file://C:", "file:///C:", id="drive-only"),
        pytest.param("file://C|", "file:///C:", id="bar-drive-only"),
        pytest.param("file://c:/foo", "file:///c:/foo", id="lowercase-drive"),
        pytest.param("file://ab/foo", "file://ab/foo", id="two-letter-host"),
        pytest.param("file://C|?q=1", "file:///C:?q=1", id="query-after-drive"),
        pytest.param("file://C|#part", "file:///C:#part", id="fragment-after-drive"),
    ],
)
def test_normalize_file_drive_in_authority(url: str, expected: str) -> None:
    normalized: Final = normalize_url(url)
    assert (normalized, normalize_url(normalized)) == (expected, expected)


@pytest.mark.parametrize("scheme", ["http", "fuzz", "fizz", "film"])
def test_normalize_file_drive_authority_only_for_file(scheme: str) -> None:
    url: Final = f"{scheme}://c:/foo"
    expected: Final = f"{scheme}://c/foo"
    normalized: Final = normalize_url(url)
    assert (normalized, normalize_url(normalized)) == (expected, expected)


@pytest.mark.parametrize(
    "url",
    [
        pytest.param("file://server:80/share", id="numeric-port"),
        pytest.param("file://server:/share", id="empty-port"),
        pytest.param("file://server:abc/share", id="text-port"),
        pytest.param("file://[::1]:80/share", id="ipv6-port"),
        pytest.param("file://localhost:0/share", id="localhost-port"),
    ],
)
def test_normalize_file_rejects_port(url: str) -> None:
    with pytest.raises(ValueError, match="file URL cannot have a port"):
        normalize_url(url)


def test_normalize_file_keeps_ascii_xn_host() -> None:
    assert normalize_url("file://xn--/p") == "file://xn--/p"
