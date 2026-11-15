from __future__ import annotations

from functools import partial
from html import escape
from typing import TYPE_CHECKING, Final
from urllib.parse import urljoin

import pytest

from turbohtml import Element, Link, Text, parse, parse_fragment
from turbohtml.extract import UrlCleaning, extract_links
from turbohtml.mutations import MutationObserver

if TYPE_CHECKING:
    from collections.abc import Callable

    from work_steps import WorkSteps

_BASE: Final[str] = "https://test.com/dir/"


def test_relative_links_resolve_against_the_base() -> None:
    assert extract_links('<a href="page.html">x</a>', _BASE) == {"https://test.com/dir/page.html"}


def test_base_element_wins_over_the_fetch_url() -> None:
    html = '<base href="https://cdn.test/assets/"><a href="p">x</a>'
    assert extract_links(html, _BASE) == {"https://cdn.test/assets/p"}


def test_overlong_base_element_leaves_the_fetch_url_the_base() -> None:
    html = f'<base href="https://cdn.test/{"b" * (2048 + 1)}/"><a href="p">x</a><a href="q">y</a>'
    assert extract_links(html, _BASE) == {"https://test.com/dir/p", "https://test.com/dir/q"}


def test_relative_links_without_a_base_are_dropped() -> None:
    assert extract_links('<a href="page.html">x</a><a href="https://a.example/x">y</a>') == {"https://a.example/x"}


def test_area_href_counts_as_a_link() -> None:
    assert extract_links('<map><area href="https://test.com/map"></map>', _BASE) == {"https://test.com/map"}


@pytest.mark.parametrize(
    "html",
    [
        pytest.param('<img src="https://test.com/i.png">', id="img-src"),
        pytest.param('<link href="https://test.com/style.css" rel="stylesheet">', id="link-element"),
        pytest.param('<script src="https://test.com/app.js"></script>', id="script-src"),
        pytest.param('<a name="anchor">no href</a>', id="anchor-without-href"),
    ],
)
def test_non_page_links_are_ignored(html: str) -> None:
    assert extract_links(html, _BASE) == set()


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param('<a href="https://test.com/a" rel="nofollow ugc">x</a>', set(), id="nofollow-token"),
        pytest.param('<a href="https://test.com/a" rel="NoFollow">x</a>', set(), id="nofollow-case-insensitive"),
        pytest.param('<a href="https://test.com/a" rel="ugc">x</a>', {"https://test.com/a"}, id="other-rel-kept"),
        pytest.param(
            '<a href="https://test.com/rel/nofollow-guide">x</a>',
            {"https://test.com/rel/nofollow-guide"},
            id="nofollow-in-url-not-rel",
        ),
    ],
)
def test_nofollow_anchors_are_skipped(html: str, expected: set[str]) -> None:
    assert extract_links(html, _BASE) == expected


def test_tracker_query_is_cleaned_from_extracted_links() -> None:
    html = '<a href="https://test.com/p?utm_source=x&id=2">x</a>'
    assert extract_links(html, _BASE) == {"https://test.com/p?id=2"}


def test_unusable_hrefs_are_dropped() -> None:
    html = '<a href="javascript:void(0)">x</a><a href="mailto:a@b.example">y</a><a href="https://ok.example/">z</a>'
    assert extract_links(html, _BASE) == {"https://ok.example/"}


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        pytest.param("HTTP://LOCALHOST:80/x", set(), id="dotless-default-port"),
        pytest.param("HTTPS://LOCALHOST:/x", set(), id="dotless-empty-port"),
        pytest.param("HTTP://EXAMPLE.ORG:80/a/../x", {"http://example.org/x"}, id="dotted-default-port"),
        pytest.param("HTTP://[0:0:0:0:0:0:0:1]:80/x", {"http://[::1]/x"}, id="ipv6-default-port"),
        pytest.param("HTTP://LOCALHOST:08000/x", {"http://localhost:8000/x"}, id="dotless-nondefault-port"),
    ],
)
def test_links_keep_canonical_web_host_shape(href: str, expected: set[str]) -> None:
    assert extract_links(f'<a href="{href}">x</a>') == expected


def test_scheme_and_slash_variants_deduplicate_to_the_first_seen() -> None:
    html = (
        '<a href="https://test.org/example">a</a>'
        '<a href="https://test.org/example/">b</a>'
        '<a href="http://test.org/example">c</a>'
    )
    assert extract_links(html, _BASE) == {"https://test.org/example"}


def test_external_only_keeps_other_sites() -> None:
    html = '<a href="/internal">i</a><a href="https://other.net/x">e</a>'
    assert extract_links(html, _BASE, external_only=True) == {"https://other.net/x"}


@pytest.mark.parametrize(
    ("base", "href"),
    [
        pytest.param("https://www.test.com/", "https://test.com/x", id="same-host"),
        pytest.param("https://www.test.com/", "https://www.test.com/x", id="www-twin"),
        pytest.param("https://www.test.com/", "https://blog.test.com/x", id="subdomain"),
        pytest.param("https://a.test.com/", "https://b.test.com/x", id="sibling-subdomains-share-etld1"),
        pytest.param("https://spam.example.co.uk/", "https://example.co.uk/x", id="multi-suffix-same-site"),
        pytest.param("https://sub.tv.com/", "https://other.tv.com/x", id="tld-like-label-is-not-a-suffix"),
        pytest.param("https://www.maple-trade.org./", "https://blog.maple-trade.org./x", id="trailing-dot-subdomains"),
        pytest.param("https://www.maple-trade.org./", "https://maple-trade.org./x", id="trailing-dot-apex"),
        pytest.param(
            "https://spam.maple-trade.com.au./", "https://maple-trade.com.au./x", id="trailing-dot-multi-suffix"
        ),
        pytest.param(
            "https://www.maple-trade.org../", "https://blog.maple-trade.org../x", id="repeated-trailing-dots-subdomains"
        ),
    ],
)
def test_external_boundary_drops_the_href(base: str, href: str) -> None:
    links = extract_links(f'<a href="{href}">x</a>', base, external_only=True)
    assert links == set()


@pytest.mark.parametrize(
    ("base", "href"),
    [
        pytest.param("https://www.test.com/", "https://test.com.evil.example/x", id="host-suffix-attack"),
        pytest.param("https://www.test.com/", "https://other.net/x", id="other-host"),
        pytest.param("https://a.co.uk/", "https://b.co.uk/x", id="multi-suffix-distinct-registrants"),
        pytest.param("https://foo.github.io/", "https://bar.github.io/x", id="private-suffix-github-io"),
        pytest.param("https://www.ck/", "https://other.ck/x", id="wildcard-and-exception-suffix"),
        pytest.param("http://192.0.2.1/", "http://198.51.100.2/x", id="ip-hosts-compare-whole"),
        pytest.param("https://harbor.com.au./", "https://other.com.au./x", id="trailing-dot-distinct-registrants"),
        pytest.param("https://foo.pages.dev./", "https://bar.pages.dev./x", id="trailing-dot-private-suffix"),
        pytest.param(
            "https://www.city.kawasaki.jp./", "https://other.kawasaki.jp./x", id="trailing-dot-wildcard-exception"
        ),
        pytest.param(
            "https://www.maple-trade.org./", "https://maple-trade.org.attacker.net./x", id="trailing-dot-suffix-attack"
        ),
        pytest.param("https://www.maple-trade.org/", "https://www.maple-trade.org./x", id="trailing-dot-only-on-link"),
        pytest.param(
            "https://harbor.com.au../", "https://other.com.au../x", id="repeated-trailing-dots-distinct-registrants"
        ),
        pytest.param("https://a..com.au/", "https://b..com.au/x", id="empty-label-left-of-the-suffix"),
    ],
)
def test_external_boundary_keeps_the_href(base: str, href: str) -> None:
    links = extract_links(f'<a href="{href}">x</a>', base, external_only=True)
    assert links == {href}


def test_external_boundary_punycodes_a_unicode_base_host() -> None:
    html = '<a href="https://xn--mnchen-3ya.de/keep">x</a><a href="https://other.example/leave">y</a>'
    links = extract_links(html, "https://münchen.de/", external_only=True)
    assert links == {"https://other.example/leave"}


def test_external_boundary_with_a_hostless_base_keeps_every_absolute_link() -> None:
    html = '<a href="https://a.example/x">x</a>'
    assert extract_links(html, "mailto:me@example.org", external_only=True) == {"https://a.example/x"}


def test_external_only_canonicalizes_ipv4_hosts_before_comparing() -> None:
    # a browser reads 0x7f.0.0.1 and 127.1 as 127.0.0.1, so they are internal to the loopback base and drop
    html: Final = '<a href="http://0x7f.0.0.1/a">1</a><a href="http://127.1/b">2</a><a href="http://8.8.8.8/c">3</a>'
    assert extract_links(html, "http://127.0.0.1/", external_only=True) == {"http://8.8.8.8/c"}


def test_external_only_reports_hosts_whose_dots_only_show_once_canonical() -> None:
    # a browser opens these as mirror.test and 192.168.1.1; the spelled host holding no dot must not hide the link
    html: Final = (
        '<a href="http://mirror%2Etest/a">1</a><a href="http://mirror\u3002test/b">2</a>'
        '<a href="http://3232235777/c">3</a><a href="http://intranet/d">4</a>'
    )
    assert extract_links(html, "http://example.com/", external_only=True) == {
        "http://mirror.test/a",
        "http://mirror.test/b",
        "http://192.168.1.1/c",
    }


def test_external_only_attributes_backslash_authority_to_the_real_host() -> None:
    # a browser resolves evil.example\@good.example to evil.example, so the link leaves the base's site and is kept
    html: Final = '<a href="http://evil.example\\@good.example/">x</a>'
    assert extract_links(html, "http://good.example/", external_only=True) == {"http://evil.example/@good.example/"}


def test_external_only_follows_a_same_scheme_base_href_ending_in_colon() -> None:
    # an empty port makes the base name other.test, so a browser sends the page's relative links there
    html: Final = '<base href="https://other.test:"><a href="/login">x</a><a href="reset">y</a>'
    expected: Final = {"https://other.test/login", "https://other.test/reset"}
    assert extract_links(html, "https://good.example/news/today") == expected
    assert extract_links(html, "https://good.example/news/today", external_only=True) == expected


def test_external_only_requires_a_base() -> None:
    with pytest.raises(ValueError, match="external_only requires a base_url"):
        extract_links('<a href="https://a.example/">x</a>', external_only=True)


@pytest.mark.parametrize(
    ("hreflang", "language"),
    [
        pytest.param("de-DE", "en", id="regioned-mismatch"),
    ],
)
def test_hreflang_drops_the_anchor(hreflang: str, language: str) -> None:
    html = f'<a href="https://test.com/example" hreflang="{hreflang}">x</a>'
    links = extract_links(html, _BASE, UrlCleaning(language=language))
    assert links == set()


@pytest.mark.parametrize(
    ("hreflang", "language"),
    [
        pytest.param("de-DE", "de", id="regioned-match"),
        pytest.param("DE", "de", id="case-insensitive"),
        pytest.param("x-default", "en", id="x-default-always-passes"),
        pytest.param("", "en", id="empty-hreflang-passes"),
    ],
)
def test_hreflang_keeps_the_anchor(hreflang: str, language: str) -> None:
    html = f'<a href="https://test.com/example" hreflang="{hreflang}">x</a>'
    links = extract_links(html, _BASE, UrlCleaning(language=language))
    assert links == {"https://test.com/example"}


def test_language_filter_still_screens_the_urls_themselves() -> None:
    html = '<a href="https://test.com/de/page">x</a><a href="https://test.com/en/page">y</a>'
    assert extract_links(html, _BASE, UrlCleaning(language="en")) == {"https://test.com/en/page"}


def test_without_a_language_filter_hreflang_is_ignored() -> None:
    html = '<a href="https://test.com/example" hreflang="de-DE">x</a>'
    assert extract_links(html, _BASE) == {"https://test.com/example"}


def test_repeated_hrefs_are_cleaned_once_and_collapse() -> None:
    html = '<a href="/nav?utm_source=x">a</a>' * 3
    assert extract_links(html, _BASE) == {"https://test.com/nav"}


def test_empty_document_yields_no_links() -> None:
    assert extract_links("", _BASE) == set()


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


def _urls(html: str) -> list[str]:
    return [link.url for link in parse_fragment(html).links()]


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        pytest.param("5; url=next.html", ["next.html"], id="standard"),
        pytest.param('0;url="dq.html"', ["dq.html"], id="double-quoted-url"),
        pytest.param("0;url='sq.html' rest", ["sq.html"], id="quoted-url-ends-at-its-quote"),
        pytest.param("0;url='open.html", ["open.html"], id="unclosed-quote-runs-to-the-end"),
        pytest.param("0;url=' in.html '", ["in.html"], id="whitespace-inside-quotes-is-trimmed"),
        pytest.param("0;'bare.html'", ["bare.html"], id="quoted-url-without-keyword"),
        pytest.param("5; url=", [], id="empty-url-after-equals"),
        pytest.param("url=only.html", [], id="no-time-is-no-refresh"),
        pytest.param(" ", [], id="blank"),
        pytest.param(" .5;url=dot.html", ["dot.html"], id="time-may-start-with-a-dot"),
        pytest.param("5x; url=no.html", [], id="junk-after-time-is-no-refresh"),
        pytest.param("5; URL = spaced.html", ["spaced.html"], id="spaced-around-equals"),
        pytest.param("5; url=a.html more ", ["a.html more"], id="unquoted-url-runs-to-the-end"),
        pytest.param("5", [], id="no-url"),
        pytest.param("5 ", [], id="no-url-after-whitespace"),
        pytest.param("5;", [], id="no-url-after-separator"),
        pytest.param("0;next.html", ["next.html"], id="keyword-is-optional"),
        pytest.param("0,next.html", ["next.html"], id="comma-separator"),
        pytest.param("0 next.html", ["next.html"], id="whitespace-separator"),
        pytest.param("1.2.3;next.html", ["next.html"], id="repeated-dot-after-delay-is-discarded"),
        pytest.param("0 ; next.html", ["next.html"], id="whitespace-around-separator"),
        pytest.param("0;;next.html", [";next.html"], id="only-one-separator-is-skipped"),
        pytest.param("0;/one?url=/two", ["/one?url=/two"], id="later-keyword-is-part-of-the-url"),
        pytest.param("0;url=/three /four", ["/three /four"], id="space-inside-unquoted-url"),
        pytest.param("5; urlx=x.html", ["urlx=x.html"], id="url-not-followed-by-equals"),
        pytest.param("5; curl=x.html", ["curl=x.html"], id="url-is-tail-of-identifier"),
        pytest.param("0;url", ["url"], id="url-keyword-at-end"),
        pytest.param("0;url=''", [], id="empty-quoted-url"),
    ],
)
def test_meta_refresh_content_parsing(content: str, expected: list[str]) -> None:
    assert _urls(f'<meta http-equiv="refresh" content="{escape(content, quote=True)}">') == expected


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
        pytest.param("/* url(hidden.png) */a{background:url(real.png)}", ["real.png"], id="comment"),
        pytest.param("/* *x url(hidden.png) */url(real.png)", ["real.png"], id="comment-with-star"),
        pytest.param("/**/url(real.png)", ["real.png"], id="empty-comment"),
        pytest.param("/*", [], id="bare-unclosed-comment"),
        pytest.param("/", [], id="trailing-slash"),
        pytest.param("a/b", [], id="slash-without-comment"),
        pytest.param("a{background:url(real.png)}/* url(hidden.png)", ["real.png"], id="unclosed-comment"),
        pytest.param('a{content:"url(hidden.png)";background:url(real.png)}', ["real.png"], id="string"),
        pytest.param(
            'a{content:"url(hidden.png)\\\ncontinued";background:url(real.png)}', ["real.png"], id="escaped-newline"
        ),
        pytest.param(
            'a{content:"url(hidden.png)\\\r\ncontinued";background:url(real.png)}', ["real.png"], id="escaped-crlf"
        ),
        pytest.param('a{content:"bad\nbackground:url(real.png)}', ["real.png"], id="bad-string-newline"),
        pytest.param('a{content:"bad\rbackground:url(real.png)}', ["real.png"], id="bad-string-cr"),
        pytest.param('a{content:"bad\fbackground:url(real.png)}', ["real.png"], id="bad-string-form-feed"),
        pytest.param('a{content:"url(hidden.png)', [], id="unclosed-string"),
        pytest.param('a{content:"url(hidden.png)\\', [], id="trailing-string-escape"),
        pytest.param("'url(hidden.png)' url(real.png)", ["real.png"], id="single-quoted-string"),
        pytest.param('url("bad\n) url(real.png)', ["real.png"], id="bad-url-string"),
        pytest.param('@import "bad\nurl(real.png)', ["real.png"], id="bad-import-string"),
        pytest.param("éurl(hidden.png) url(real.png)", ["real.png"], id="non-ascii-identifier-prefix"),
        pytest.param("aurl(hidden.png) URL(real.png)", ["real.png"], id="ascii-identifier-prefix"),
        pytest.param("a_url(hidden.png) a-url(hidden.png) url(real.png)", ["real.png"], id="identifier-punctuation"),
        pytest.param("Aurl(hidden.png) 1url(hidden.png) url(real.png)", ["real.png"], id="uppercase-and-digit-prefix"),
        pytest.param("!url(a.png) url(real.png)", ["a.png", "real.png"], id="punctuation-before-url-is-not-identifier"),
        pytest.param("u @import 'a.css'", ["a.css"], id="short-ident-before-import"),
        pytest.param("a@import 'hidden.css'", [], id="import-in-identifier"),
        pytest.param(
            'a{content:"skip \\" url(hidden.png)";background:url(real.png)}', ["real.png"], id="escaped-quote"
        ),
        pytest.param('@import "a.css";a{background:url(real.png)}', ["a.css", "real.png"], id="import-then-url"),
        pytest.param(
            'a{background:url("a)b.png");mask:url(real.png)}', ["a)b.png", "real.png"], id="url-with-parenthesis"
        ),
        pytest.param(
            'a{background:url("a\\"b.png");mask:url(real.png)}', ['a"b.png', "real.png"], id="url-with-escaped-quote"
        ),
    ],
)
def test_style_element_css_parsing(css: str, expected: list[str]) -> None:
    assert _urls(f"<style>{css}</style>") == expected


@pytest.mark.parametrize(
    ("css", "expected"),
    [
        pytest.param(
            'url("http://one.example\\40 two.example/")', ["http://one.example@two.example/"], id="hex-then-space"
        ),
        pytest.param("url('\\68ttp://a.example/')", ["http://a.example/"], id="hex-then-non-hex"),
        pytest.param('url("\\00006a1.png")', ["j1.png"], id="hex-takes-six-digits"),
        pytest.param('url("a\\2E\tpng")', ["a.png"], id="hex-upper-case-then-tab"),
        pytest.param('url("a\\0 b\\d800 c\\110000 d")', ["a\ufffdb\ufffdc\ufffdd"], id="hex-outside-unicode"),
        pytest.param('url("a\\41")', ["aA"], id="hex-ends-the-string"),
        pytest.param("url(a\\41", ["aA"], id="hex-ends-the-input"),
        pytest.param("url(a\\", ["a�"], id="bare-backslash-ends-the-input"),
        pytest.param("url(a  ", ["a"], id="bare-whitespace-ends-the-input"),
        pytest.param('url("a\\1f600 .png")', ["a\U0001f600.png"], id="hex-above-the-surrogates"),
        pytest.param('url("\\\\remote.test/x.png")', ["\\remote.test/x.png"], id="escaped-backslash"),
        pytest.param('url("a\\\nb.png")', ["ab.png"], id="line-continuation"),
        pytest.param('url("\\\n")', [], id="only-a-line-continuation"),
        pytest.param('url("a.png\\', ["a.png"], id="backslash-ends-the-input"),
        pytest.param('@import "a\\2e css"', ["a.css"], id="import-string"),
        pytest.param('@\\69mport "x.css"', ["x.css"], id="escaped-import-keyword"),
        pytest.param('@\\49MPORT "x.css"', ["x.css"], id="escaped-import-keyword-upper-case"),
        pytest.param("@\\69mport url(a.css)", ["a.css"], id="escaped-import-keyword-then-url"),
        pytest.param("url(a\\)b.png) url(real.png)", ["a)b.png", "real.png"], id="bare-escaped-parenthesis"),
        pytest.param("url(a\\ b.png)", ["a b.png"], id="bare-escaped-space"),
        pytest.param("url(a\\41 ) url(real.png)", ["aA", "real.png"], id="bare-hex-then-space"),
        pytest.param("\\75 rl(a.png)", ["a.png"], id="function-name-as-hex-escape"),
        pytest.param("u\\rl(a.png)", ["a.png"], id="function-name-with-escaped-letter"),
        pytest.param("UR\\4c(a.png)", ["a.png"], id="function-name-ends-on-escape"),
    ],
)
def test_css_url_escapes_are_decoded(css: str, expected: list[str]) -> None:
    # the URL a browser fetches is the decoded one: "one.example\40 two.example" names the host two.example
    assert _urls(f"<style>{css}</style>") == expected


@pytest.mark.parametrize(
    "css",
    [
        pytest.param("url(a b.png)", id="whitespace-inside"),
        pytest.param('url(a"b.png)', id="double-quote-inside"),
        pytest.param("url(a'b.png)", id="single-quote-inside"),
        pytest.param("url(a(b.png)", id="parenthesis-inside"),
        pytest.param("url(a\x01b.png)", id="non-printable-inside"),
        pytest.param("url(a\x7fb.png)", id="delete-inside"),
        pytest.param("url(a\\\nb.png)", id="backslash-before-newline"),
        pytest.param("url(a b \\) c.png)", id="escaped-parenthesis-in-the-remnant"),
        pytest.param("\\url(a b @import 'c.css' url('d.png')", id="escaped-name-with-strings-in-the-remnant"),
    ],
)
def test_css_bad_url_token_is_no_link_and_ends_at_its_parenthesis(css: str) -> None:
    # CSS discards a malformed unquoted url() up to the next ")", so nothing inside it is a link a browser follows
    assert _urls(f"<style>a{{x:{css};y:url(real.png)}}</style>") == ["real.png"]


@pytest.mark.parametrize(
    "css",
    [
        pytest.param("url(a b", id="whitespace-then-end-of-input"),
        pytest.param("#url(a.png)", id="hash-token"),
        pytest.param("@url(a.png)", id="at-keyword"),
        pytest.param("a\\.url(a.png)", id="escape-in-a-longer-name"),
        pytest.param("\\+\\75rl(a.png)", id="escapes-in-a-longer-name"),
        pytest.param("\\\nurl", id="backslash-before-newline-outside-a-string"),
        pytest.param("ur\\6c", id="escaped-name-without-parenthesis"),
        pytest.param("\\", id="backslash-ends-the-input-outside-a-string"),
        pytest.param("\\75rl", id="escaped-name-ends-the-input"),
        pytest.param("\\61rl(a.png)", id="escaped-name-of-another-function"),
        pytest.param("\\75xl(a.png)", id="escaped-name-with-another-second-letter"),
        pytest.param("ur\\6d(a.png)", id="escaped-name-with-another-last-letter"),
        pytest.param("#\\75rl(a.png)", id="escaped-name-after-hash"),
        pytest.param("@\\75rl(a.png)", id="escaped-name-after-at"),
        pytest.param("\\75rl;", id="escaped-name-before-another-token"),
        pytest.param("\\75rl\\78(a.png)", id="escaped-name-longer-than-url"),
        pytest.param('\\69mport "a.css"', id="escaped-import-without-at-sign"),
        pytest.param(' \\69mport "a.css"', id="escaped-import-after-space"),
        pytest.param('@\\61mport "a.css"', id="escaped-import-with-other-first-letter"),
        pytest.param('@\\69mpor "a.css"', id="escaped-import-shorter"),
        pytest.param('\\40import "a.css"', id="escaped-at-sign-is-no-at-keyword"),
    ],
)
def test_css_text_that_is_no_url_token_holds_no_link(css: str) -> None:
    assert _urls(f"<style>{css}</style>") == []


@pytest.mark.parametrize(
    "style",
    [
        pytest.param("font-family:url(a b url('\\29 ;visibility:hidden;y:'))", id="string-in-a-bad-url-remnant"),
        pytest.param("font-family:\\url(a b @import '\\29 ;visibility:hidden;y:')", id="import-in-a-bad-url-remnant"),
        pytest.param('font-family:#url(a"c);visibility:hidden;y:")', id="hash-token"),
        pytest.param('font-family:@url(a"c);visibility:hidden;y:")', id="at-keyword"),
        pytest.param("font-family:a\\.url(url(b c);visibility:hidden;y:))", id="url-ending-an-escaped-name"),
        pytest.param("font-family:a\\.url((/../b);visibility:hidden;y:)", id="block-after-an-escaped-name"),
    ],
)
def test_resolve_links_leaves_css_alone_where_css_reads_no_url_token(style: str) -> None:
    # rewriting text the scanner takes for a URL where CSS reads something else moves where that construct ends, which
    # turns the declarations the page hid inside it into live ones
    root = parse_fragment("<p></p>")
    paragraph = root.find("p")
    assert paragraph is not None
    paragraph.attrs["style"] = style
    root.resolve_links(_RESOLVE_BASE)
    assert paragraph.attrs["style"] == style


@pytest.mark.parametrize(
    ("css", "expected"),
    [
        pytest.param('a{content:"url(hidden)\\\r\ncontinued";background:url(real)}', ["real"], id="escaped-crlf"),
        pytest.param('a{content:"url(hidden)\\\rcontinued";background:url(real)}', ["real"], id="escaped-cr"),
        pytest.param('a{content:"url(hidden)\\\r', [], id="trailing-escaped-cr"),
        pytest.param('a{background:url("a\\41\r\nb")}', ["aAb"], id="hex-escape-ended-by-crlf"),
        pytest.param('a{background:url("a\\41\rb")}', ["aAb"], id="hex-escape-ended-by-cr"),
        pytest.param("a{background:url(a\\41\r", ["aA"], id="hex-escape-ended-by-trailing-cr"),
        pytest.param('a{background:url("a\\\fb")}', ["ab"], id="escaped-form-feed"),
        pytest.param('a{content:"bad\rbackground:url(real)}', ["real"], id="bad-string-cr"),
        pytest.param('a{content:"bad\fbackground:url(real)}', ["real"], id="bad-string-form-feed"),
    ],
)
def test_style_text_css_boundaries(css: str, expected: list[str]) -> None:
    style: Final = Element("style")
    style.append(Text(css))
    assert [link.url for link in style.links()] == expected


def test_whitespace_only_space_list_yields_nothing() -> None:
    assert _urls('<object archive="    "></object>') == []


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        pytest.param('<a href="a/b.html">x</a>', [("a", "href", "a/b.html")], id="anchor-href-is-enumerated"),
        pytest.param(
            '<a href="  a/b.html  ">x</a>',
            [("a", "href", "a/b.html")],
            id="value-is-reported-with-surrounding-whitespace-trimmed",
        ),
        # &#13; injects a real U+000D past the input preprocessor's CR->LF fold, and CR is
        # HTML ASCII whitespace, so "strip leading/trailing ASCII whitespace" must drop it.
        # 13;a/b.html&#13;">x</a>') == [("a", "href", "a/b.html")]
        pytest.param(
            '<a href="&#13;a/b.html&#13;">x</a>',
            [("a", "href", "a/b.html")],
            id="carriage-return-from-char-ref-is-trimmed",
        ),
        pytest.param('<a class="c" href="u">x</a>', [("a", "href", "u")], id="non-link-attribute-is-ignored"),
        pytest.param(
            '<a href="h" ping="  p1   p2 p3 ">x</a>',
            [
                ("a", "href", "h"),
                ("a", "ping", "p1"),
                ("a", "ping", "p2"),
                ("a", "ping", "p3"),
            ],
            id="ping-is-a-whitespace-separated-list",
        ),
        pytest.param('<div archive="j1.jar j2.jar"></div>', [], id="archive-is-ignored-off-object-and-applet"),
        pytest.param('<input placeholder="javascript:not-a-url">', [], id="placeholder-is-not-imagesrcset"),
        pytest.param(
            '<meta http-equiv="refresh" content="5; url=next.html">',
            [("meta", "content", "next.html")],
            id="meta-refresh-url-is-enumerated",
        ),
        pytest.param(
            '<meta http-equiv="REFRESH" content="0;URL=&#39;n.html&#39;">',
            [("meta", "content", "n.html")],
            id="meta-refresh-url-is-case-insensitive-and-unquotes",
        ),
        pytest.param(
            "<p style='background:url(\"bg.png\")'></p>",
            [("p", "style", "bg.png")],
            id="css-url-double-quoted-in-single-quoted-attribute",
        ),
        pytest.param("<style></style>", [], id="empty-style-element-has-no-link"),
        pytest.param('<style>@import "a.css";</style>', [("style", None, "a.css")], id="css-import-double-quoted"),
        pytest.param(
            "<style>@import url(a.css);</style>",
            [("style", None, "a.css")],
            id="css-import-without-string-defers-to-url-form",
        ),
    ],
)
def test_enumerated_urls(markup: str, expected: list[tuple[str, str | None, str]]) -> None:
    assert _enumerated_urls(markup) == expected


def _enumerated_urls(html: str) -> list[tuple[str, str | None, str]]:
    return [(link.element.tag, link.attribute, link.url) for link in parse_fragment(html).links()]


def test_many_elements_grow_the_snapshot() -> None:
    # The pure-C element snapshot starts at 16 slots; >16 elements exercise its regrow.
    html = "".join(f'<a href="p{index}">{index}</a>' for index in range(40))
    assert _enumerated_urls(html) == [("a", "href", f"p{index}") for index in range(40)]


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param('<a href="   ">x</a>', id="whitespace"),
        pytest.param("<a href>x</a>", id="valueless"),
    ],
)
def test_empty_url_attribute_is_skipped(markup: str) -> None:
    assert _enumerated_urls(markup) == []


@pytest.mark.parametrize(
    ("tag", "attr"),
    [
        pytest.param("img", "src", id="src"),
        pytest.param("blockquote", "cite", id="cite"),
        pytest.param("object", "data", id="data"),
        pytest.param("form", "action", id="action"),
        pytest.param("video", "poster", id="poster"),
        pytest.param("img", "longdesc", id="longdesc"),
        pytest.param("button", "formaction", id="formaction"),
        pytest.param("table", "background", id="background"),
    ],
)
def test_single_url_attributes_are_enumerated(tag: str, attr: str) -> None:
    found = _enumerated_urls(f'<{tag} {attr}="u/v">t</{tag}>')
    assert (tag, attr, "u/v") in found


def test_svg_xlink_href_is_enumerated() -> None:
    found = _enumerated_urls('<svg><use xlink:href="i.svg#g"></use></svg>')
    assert ("use", "xlink:href", "i.svg#g") in found


@pytest.mark.parametrize(
    "attr",
    [pytest.param("rel", id="len3"), pytest.param("type", id="len4"), pytest.param("height", id="len6")],
)
def test_same_length_non_url_attributes_are_ignored(attr: str) -> None:
    assert _enumerated_urls(f'<input {attr}="not-a-url">') == []


def test_ping_only_counts_on_anchor_and_area() -> None:
    assert _enumerated_urls('<div ping="p1 p2">x</div>') == []
    assert ("area", "ping", "p1") in _enumerated_urls('<map><area ping="p1 p2"></map>')


def test_archive_is_a_whitespace_list_on_object_and_applet() -> None:
    assert _enumerated_urls('<object archive="j1.jar j2.jar"></object>') == [
        ("object", "archive", "j1.jar"),
        ("object", "archive", "j2.jar"),
    ]
    assert ("applet", "archive", "j1.jar") in _enumerated_urls('<applet archive="j1.jar"></applet>')


@pytest.mark.parametrize("attr", [pytest.param("srcset", id="srcset"), pytest.param("imagesrcset", id="imagesrcset")])
def test_srcset_candidates_are_enumerated(attr: str) -> None:
    found = _enumerated_urls(f'<img {attr}="a.png 1x, b.png 2x , c.png">')
    assert [url for _, _, url in found] == ["a.png", "b.png", "c.png"]


def test_srcset_with_a_trailing_comma_and_varied_whitespace() -> None:
    # the trailing comma exercises the separator skip running to the end, and \t/\n/\f the whitespace set
    found = _enumerated_urls('<img srcset="a.png\t1x,\nb.png\x0c2x,">')
    assert [url for _, _, url in found] == ["a.png", "b.png"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("a.png,b.png 2x", ["a.png,b.png"], id="internal-comma"),
        pytest.param(
            "data:image/gif;base64,R0lGODlh 1x, b.png 2x", ["data:image/gif;base64,R0lGODlh", "b.png"], id="data-url"
        ),
        pytest.param("a.png, b.png 2x", ["a.png", "b.png"], id="trailing-comma"),
        pytest.param("a.png,, b.png 2x", ["a.png", "b.png"], id="multiple-trailing-commas"),
        pytest.param("a.png,b.png,", ["a.png,b.png"], id="internal-and-trailing-comma"),
        pytest.param(
            "hero.jpg (w,h),https://cdn.test/hero.jpg 1x",
            ["https://cdn.test/hero.jpg"],
            id="comma-in-parens",
        ),
        pytest.param("a.png 1x (x,y) z,b.png 2x", ["b.png"], id="comma-in-later-parens"),
        pytest.param("a.png ((x),b.png 2x", ["b.png"], id="parens-do-not-nest"),
        pytest.param("a.png x),b.png 2x", ["b.png"], id="stray-close-paren"),
        pytest.param("a.png (x,b.png 2x", [], id="unclosed-parens-run-to-the-end"),
        pytest.param("a.jpg 1w 2w, b.jpg 1x", ["b.jpg"], id="duplicate-width"),
        pytest.param("a.jpg 1x 1.5x, b.jpg 1x", ["b.jpg"], id="duplicate-density"),
        pytest.param("a.jpg 3w 2x, b.jpg 1x", ["b.jpg"], id="width-then-density"),
        pytest.param("a.jpg 2x 3w, b.jpg 1x", ["b.jpg"], id="density-then-width"),
        pytest.param("a.jpg 1w 2x, b.jpg 1x", ["b.jpg"], id="width-with-density"),
        pytest.param("a.jpg 0w, b.jpg 1x", ["b.jpg"], id="zero-width"),
        pytest.param("a.jpg 0.0x, b.jpg 0x", ["a.jpg", "b.jpg"], id="zero-density"),
        pytest.param("a.jpg -0x, b.jpg -0.0e5x", ["a.jpg", "b.jpg"], id="negative-zero-density"),
        pytest.param("a.jpg -0.5x, b.jpg 1x", ["b.jpg"], id="negative-fraction-density"),
        pytest.param("a.jpg -x, b.jpg 1x", ["b.jpg"], id="bare-sign-density"),
        pytest.param("a.jpg .x, b.jpg 1x", ["b.jpg"], id="bare-dot-density"),
        pytest.param("a.jpg -1x, b.jpg 1x", ["b.jpg"], id="negative-density"),
        pytest.param("a.jpg +1x, b.jpg 1x", ["b.jpg"], id="signed-density"),
        pytest.param("a.jpg .5x, b.jpg -.5x", ["a.jpg"], id="density-without-integer-part"),
        pytest.param("a.jpg 5.x, b.jpg 1x", ["b.jpg"], id="density-without-fraction-digits"),
        pytest.param("a.jpg 1ex, b.jpg 1x", ["b.jpg"], id="exponent-without-digits"),
        pytest.param("a.jpg 1.5w, b.jpg 1x", ["b.jpg"], id="fractional-width"),
        pytest.param("a.jpg fx, b.jpg 1x", ["b.jpg"], id="non-numeric-density"),
        pytest.param("a.jpg x, b.jpg 1x", ["b.jpg"], id="bare-unit"),
        pytest.param("a.jpg 2h, b.jpg 1x", ["b.jpg"], id="height-without-width"),
        pytest.param("a.jpg 1w 2h 3h, b.jpg 1x", ["b.jpg"], id="duplicate-height"),
        pytest.param("a.jpg 2h 1x, b.jpg 1x", ["b.jpg"], id="height-with-density"),
        pytest.param("a.jpg 1y, b.jpg 1x", ["b.jpg"], id="unknown-unit"),
        pytest.param("a.jpg 1X, b.jpg 1x", ["b.jpg"], id="uppercase-unit"),
        pytest.param("a.jpg 1w 2h, b.jpg 1x", ["a.jpg", "b.jpg"], id="width-with-height"),
        pytest.param("a.jpg\t01w\n2h, b.jpg 1x", ["a.jpg", "b.jpg"], id="integer-with-leading-zero"),
        pytest.param("a.jpg 1.5x, b.jpg 1e2x", ["a.jpg", "b.jpg"], id="exponent-density"),
        pytest.param("a.jpg 1E+2x, b.jpg 0.5x", ["a.jpg", "b.jpg"], id="signed-exponent-density"),
        pytest.param("a.jpg 1e-2x, b.jpg 1x", ["a.jpg", "b.jpg"], id="negative-exponent-density"),
        pytest.param("a.jpg 1.5e2x, b.jpg 1x", ["a.jpg", "b.jpg"], id="fraction-then-exponent-density"),
        pytest.param("a.jpg w, b.jpg 1x", ["b.jpg"], id="bare-width-unit"),
        pytest.param("a.jpg 0h, b.jpg 1x", ["b.jpg"], id="zero-height"),
        pytest.param("a.jpg 1x 2h, b.jpg 1x", ["b.jpg"], id="density-then-height"),
        pytest.param("a.jpg 1ax, b.jpg 1x", ["b.jpg"], id="density-with-trailing-letter"),
        pytest.param("a.jpg 1.5ax, b.jpg 1x", ["b.jpg"], id="fraction-with-trailing-letter"),
        pytest.param("a.jpg 1e2ax, b.jpg 1x", ["b.jpg"], id="exponent-with-trailing-letter"),
        pytest.param("a.jpg 640w, b.jpg 1280w", ["a.jpg", "b.jpg"], id="widths"),
        pytest.param("a.jpg 1x , b.jpg 2x\n", ["a.jpg", "b.jpg"], id="space-after-descriptor"),
        pytest.param("a.jpg 2x, b.jpg 12", ["a.jpg"], id="digits-without-unit-at-the-end"),
        pytest.param("a.jpg 12 , b.jpg 1x", ["b.jpg"], id="digits-without-unit"),
        pytest.param("a.jpg 1xe, b.jpg 1x", ["b.jpg"], id="letter-after-unit"),
        pytest.param("a.jpg 640w 1x, b.jpg 1x", ["b.jpg"], id="second-descriptor-after-width"),
        pytest.param("a.jpg 0640w, b.jpg 00w", ["a.jpg"], id="width-with-leading-zero"),
    ],
)
def test_srcset_url_boundaries(value: str, expected: list[str]) -> None:
    found = _enumerated_urls(f'<img srcset="{value}">')
    assert [url for _, _, url in found] == expected


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param('<meta http-equiv="refresh" content="5">', id="no-url-keyword"),
        pytest.param('<meta name="x" content="url=y">', id="not-refresh"),
        pytest.param('<meta http-equiv="refresh">', id="no-content"),
    ],
)
def test_meta_without_refresh_url_has_no_link(markup: str) -> None:
    assert _enumerated_urls(markup) == []


@pytest.mark.parametrize(
    ("style", "expected"),
    [
        pytest.param("background:url(bg.png)", ["bg.png"], id="unquoted"),
        pytest.param("background:url('bg.png')", ["bg.png"], id="single-quoted"),
        pytest.param("background:url(  sp.png  )", ["sp.png"], id="inner-whitespace"),
        pytest.param("background:url()", [], id="empty-url"),
        pytest.param("color:red", [], id="no-url"),
        pytest.param("background:burl(no)", [], id="not-a-url-function"),
        pytest.param("a:url(one);b:url(two)", ["one", "two"], id="two-urls"),
    ],
)
def test_css_url_in_style_attribute(style: str, expected: list[str]) -> None:
    found = _enumerated_urls(f'<p style="{style}"></p>')
    assert [url for _, _, url in found] == expected


def test_style_element_text_is_scanned_as_css() -> None:
    found = _enumerated_urls("<style>a{background:url(bg.png)} @import 'theme.css'; @media x {}</style>")
    assert [(link[0], link[1], link[2]) for link in found] == [
        ("style", None, "bg.png"),
        ("style", None, "theme.css"),
    ]


def test_style_with_a_non_text_child_skips_it() -> None:
    # a parsed <style> is always rawtext, but a programmatic tree can give it an element child to skip
    style = Element("style")
    style.append(Element("span"))
    style.append(Text("a{background:url(bg.png)}"))
    assert [link.url for link in style.links()] == ["bg.png"]


def test_many_links_in_one_value_grow_the_span_buffer() -> None:
    found = _enumerated_urls(f'<img srcset="{", ".join(f"img{index}.png {index}x" for index in range(1, 13))}">')
    assert [url for _, _, url in found] == [f"img{index}.png" for index in range(1, 13)]


def test_links_is_returned_in_document_order() -> None:
    found = _enumerated_urls('<a href="1"><img src="2"></a><a href="3">x</a>')
    assert [url for _, _, url in found] == ["1", "2", "3"]


def test_links_on_a_whole_document() -> None:
    found = parse('<html><body><a href="u">x</a></body></html>').links()
    assert [(link.element.tag, link.url) for link in found] == [("a", "u")]


def test_link_is_a_named_tuple() -> None:
    (link,) = parse_fragment('<a href="u">x</a>').links()
    assert isinstance(link, Link)
    assert link == (link.element, "href", "u")
    assert link.element.attrs["href"] == "u"


_RESOLVE_BASE: Final[str] = "https://example.com/dir/page.html"


_HTTPS: Final[str] = "https://example.com/dir/"


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        pytest.param('<a href="a/b.html">x</a>', '<a href="https://example.com/dir/a/b.html">x</a>', id="relative"),
        pytest.param('<a href="/top.html">x</a>', '<a href="https://example.com/top.html">x</a>', id="root-relative"),
        pytest.param('<a href="https://other.test/x">y</a>', '<a href="https://other.test/x">y</a>', id="absolute"),
        pytest.param('<a href="#sec">y</a>', '<a href="https://example.com/dir/page.html#sec">y</a>', id="fragment"),
    ],
)
def test_href_resolution(markup: str, expected: str) -> None:
    assert _resolved(markup) == expected


def _resolved(html: str, base: str = _RESOLVE_BASE) -> str:
    root = parse_fragment(html)
    root.resolve_links(base)
    return root.inner_html


def test_srcset_candidates_are_each_resolved() -> None:
    out = _resolved('<img srcset="a.png 1x, b.png 2x">')
    assert out == '<img srcset="https://example.com/dir/a.png 1x, https://example.com/dir/b.png 2x">'


def test_srcset_url_with_comma_is_resolved_as_one_link() -> None:
    out = _resolved('<img srcset="a,b 1x, c 2x">')
    assert out == '<img srcset="https://example.com/dir/a,b 1x, https://example.com/dir/c 2x">'


def test_ping_list_entries_are_each_resolved() -> None:
    out = _resolved('<a href="h" ping="p1 p2">x</a>')
    assert 'ping="https://example.com/dir/p1 https://example.com/dir/p2"' in out


def test_css_url_in_style_attribute_is_resolved() -> None:
    out = _resolved('<p style="background:url(bg.png)"></p>')
    assert out == '<p style="background:url(https://example.com/dir/bg.png)"></p>'


def test_css_url_in_style_element_is_resolved() -> None:
    root = parse_fragment("<style>a{background:url(bg.png)} @import 'theme.css';</style>")
    root.resolve_links(_RESOLVE_BASE)
    assert root.inner_html == (
        "<style>a{background:url(https://example.com/dir/bg.png)} @import 'https://example.com/dir/theme.css';</style>"
    )


@pytest.mark.parametrize(
    ("style", "expected"),
    [
        pytest.param('url("p\\\t")', 'url("https://example.com/dir/p")', id="escaped-tab"),
        pytest.param(
            'url("\\\\remote.test/x.png")',
            'url("https://example.com/dir/\\\\remote.test/x.png")',
            id="escaped-backslash",
        ),
        pytest.param("url('a\\'b.png')", "url('https://example.com/dir/a\\'b.png')", id="escaped-quote"),
        pytest.param("url(a\\)b.png)", "url(https://example.com/dir/a\\)b.png)", id="bare-escaped-parenthesis"),
        pytest.param("url(\\61 /b\\ c.png)", "url(https://example.com/dir/a/b\\20 c.png)", id="bare-escaped-space"),
        pytest.param(
            'url("https://other.test/\\61")', 'url("https://other.test/\\61")', id="absolute-stays-as-written"
        ),
        pytest.param('url("\\\n")', 'url("\\\n")', id="only-a-line-continuation"),
    ],
)
def test_css_url_is_resolved_from_its_decoded_form(style: str, expected: str) -> None:
    root = parse_fragment("<p></p>")
    paragraph = root.find("p")
    assert paragraph is not None
    paragraph.attrs["style"] = f'cursor:{style},auto;font-family:"),auto;visibility:hidden;y:"'
    root.resolve_links(_RESOLVE_BASE)
    # the text after the URL stays inside the string it was in: no escape is left to swallow the closing quote
    assert paragraph.attrs["style"] == f'cursor:{expected},auto;font-family:"),auto;visibility:hidden;y:"'


def test_escaped_import_keyword_is_resolved() -> None:
    assert (
        _resolved('<style>@\\69mport "a.css";</style>') == '<style>@\\69mport "https://example.com/dir/a.css";</style>'
    )


def test_resolved_css_url_cannot_close_its_style_element() -> None:
    # "<\/style" is how a stylesheet spells the text "</style"; decoded and written back bare it would end the element
    html: Final = '<style>a{background:url("x<\\/style>y")}</style>'
    assert _resolved(html) == '<style>a{background:url("https://example.com/dir/x<\\/style>y")}</style>'


def test_css_comments_and_strings_are_not_resolved() -> None:
    html: Final = "<style>/* url(hidden.png) */a{content:'url(fake.png)';background:url(real.png)}</style>"
    assert _resolved(html) == (
        "<style>/* url(hidden.png) */a{content:'url(fake.png)';"
        "background:url(https://example.com/dir/real.png)}</style>"
    )


def test_meta_refresh_url_is_resolved() -> None:
    out = _resolved('<meta http-equiv="refresh" content="5; url=next.html">')
    assert out == '<meta http-equiv="refresh" content="5; url=https://example.com/dir/next.html">'


def test_meta_refresh_url_without_keyword_is_resolved() -> None:
    out = _resolved('<meta http-equiv="refresh" content="0;next.html">')
    assert out == '<meta http-equiv="refresh" content="0;https://example.com/dir/next.html">'


def test_resolve_on_a_whole_document_returns_none() -> None:
    doc = parse('<html><body><a href="a.html">x</a></body></html>')
    assert doc.resolve_links(_RESOLVE_BASE) is None
    (link,) = doc.links()
    assert link.url == "https://example.com/dir/a.html"


def test_a_longer_replacement_grows_the_value() -> None:
    out = _resolved('<a href="x">t</a>', "https://example.com/very/deep/path/")
    assert out == '<a href="https://example.com/very/deep/path/x">t</a>'


@pytest.mark.parametrize(
    "href",
    [
        pytest.param("\\\\evil.test/x", id="double-backslash"),
        pytest.param("/\\evil.test/x", id="slash-backslash"),
        pytest.param("\\/evil.test/x", id="backslash-slash"),
    ],
)
def test_resolve_links_attributes_backslash_scheme_relative_to_the_real_host(href: str) -> None:
    # unlike stdlib urljoin, resolve_links ends a special-scheme authority at '\', as a browser does
    assert _resolved_href(href, _RESOLVE_BASE) == "https://evil.test/x"


def _resolved_href(href: str, base: str) -> str:
    root = parse_fragment(f'<a href="{escape(href, quote=True)}">x</a>')
    root.resolve_links(base)
    (link,) = root.links()
    return link.url


@pytest.mark.parametrize(
    "href",
    [
        pytest.param("///elsewhere.test/x", id="three-slashes"),
        pytest.param("////elsewhere.test/x", id="four-slashes"),
        pytest.param("/\\/elsewhere.test/x", id="slash-backslash-slash"),
        pytest.param("//\\elsewhere.test/x", id="third-backslash"),
        pytest.param("\\\\\\elsewhere.test/x", id="three-backslashes"),
        pytest.param("///\\//\\//elsewhere.test/x", id="mixed-extra-slashes"),
        pytest.param("https:///elsewhere.test/x", id="same-scheme-three-slashes"),
        pytest.param("HTTPS:///elsewhere.test/x", id="same-scheme-uppercase"),
        pytest.param("https:/\\/elsewhere.test/x", id="same-scheme-slash-backslash-slash"),
        pytest.param("https://\\elsewhere.test/x", id="same-scheme-third-backslash"),
    ],
)
def test_resolve_links_attributes_extra_authority_slashes_to_the_real_host(href: str) -> None:
    # unlike stdlib urljoin, resolve_links skips the slashes after a special-scheme "//" before the host, as a browser
    # does, so the link does not land as a path on the page's own host
    assert _resolved_href(href, _RESOLVE_BASE) == "https://elsewhere.test/x"


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        pytest.param(
            '<img src="///elsewhere.test/i" srcset="///elsewhere.test/a 1x, ////elsewhere.test/b 2x">',
            '<img src="https://elsewhere.test/i" srcset="https://elsewhere.test/a 1x, https://elsewhere.test/b 2x">',
            id="src-and-srcset",
        ),
        pytest.param(
            '<form action="///elsewhere.test/post"></form>',
            '<form action="https://elsewhere.test/post"></form>',
            id="action",
        ),
        pytest.param(
            '<meta http-equiv="refresh" content="0; url=///elsewhere.test/next">',
            '<meta http-equiv="refresh" content="0; url=https://elsewhere.test/next">',
            id="meta-refresh",
        ),
        pytest.param(
            '<p style="background:url(///elsewhere.test/bg.png)"></p>',
            '<p style="background:url(https://elsewhere.test/bg.png)"></p>',
            id="css-url",
        ),
    ],
)
def test_resolve_links_skips_extra_authority_slashes_in_every_location(markup: str, expected: str) -> None:
    assert _resolved(markup) == expected


@pytest.mark.parametrize(
    ("base", "href", "expected"),
    [
        pytest.param("ftp://f.test/g/", "///elsewhere.test/x", "ftp://elsewhere.test/x", id="ftp-base"),
        pytest.param("ws://w.test/a", "ws:///elsewhere.test/x", "ws://elsewhere.test/x", id="ws-base-same-scheme"),
        pytest.param(_RESOLVE_BASE, "///elsewhere.test", "https://elsewhere.test", id="host-only"),
        pytest.param(_RESOLVE_BASE, "///elsewhere.test?q#f", "https://elsewhere.test?q#f", id="host-then-query"),
        pytest.param(
            _RESOLVE_BASE, "///u@elsewhere.test:8443/x", "https://u@elsewhere.test:8443/x", id="userinfo-and-port"
        ),
        pytest.param(_RESOLVE_BASE, "///[::1]/x", "https://[::1]/x", id="ipv6-host"),
        pytest.param(_RESOLVE_BASE, "///", "https://example.com/", id="no-host"),
        pytest.param(_RESOLVE_BASE, "///?q", "https://example.com/?q", id="no-host-before-query"),
        pytest.param(_RESOLVE_BASE, "https:///", "https://example.com/", id="same-scheme-no-host"),
        pytest.param(
            _RESOLVE_BASE, "///[elsewhere.test/x", "https://example.com/[elsewhere.test/x", id="unclosed-ipv6-host"
        ),
        pytest.param(
            _RESOLVE_BASE, "/elsewhere.test/x", "https://example.com/elsewhere.test/x", id="one-slash-is-a-path"
        ),
        pytest.param(
            _RESOLVE_BASE, "https:/elsewhere.test", "https://example.com/elsewhere.test", id="same-scheme-one-slash"
        ),
        pytest.param("file:///srv/www/page.html", "///etc/hosts", "file:///etc/hosts", id="file-base-empty-host"),
        pytest.param("sftp://s.test/a/b", "///c/d", "sftp://s.test/c/d", id="non-special-base"),
    ],
)
def test_resolve_links_keeps_extra_authority_slash_boundaries(base: str, href: str, expected: str) -> None:
    assert _resolved_href(href, base) == expected


@pytest.mark.parametrize(
    "html",
    [
        pytest.param('<a href="">x</a>', id="empty-href"),
        pytest.param("<style></style>", id="empty-style"),
        pytest.param('<meta http-equiv="refresh" content="5">', id="refresh-no-url"),
    ],
)
def test_nothing_to_resolve_is_a_no_op(html: str) -> None:
    assert _resolved(html) == html


def test_resolve_links_requires_a_string_base() -> None:
    with pytest.raises(TypeError, match="base URL string"):
        parse_fragment('<a href="a">x</a>').resolve_links(None)  # ty: ignore[invalid-argument-type]  # passes a wrong type on purpose to check the runtime rejection


# resolve_links skips urljoin for an already-absolute link it would return unchanged; every case must still match the
# stdlib join it stands in for, whether the link is skipped (absolute) or rejoined (relative, or a would-be
# skip that urljoin rewrites). The href carries no surrounding whitespace, so the scanned span is the whole value.
@pytest.mark.parametrize(
    ("base", "href"),
    [
        pytest.param(_HTTPS, "https://host/p?q#f", id="same-scheme-absolute-skipped"),
        pytest.param(_HTTPS, "https://user:pw@host:8080/a;b?q#f", id="same-scheme-userinfo-port"),
        pytest.param(_HTTPS, "https://HOST.example/Path", id="same-scheme-host-case-preserved"),
        pytest.param(_HTTPS, "https://host/a/../b", id="same-scheme-dot-segments-preserved"),
        pytest.param(_HTTPS, "http://host/p", id="different-scheme-http"),
        pytest.param(_HTTPS, "mailto:a@b.test", id="different-scheme-not-relative"),
        pytest.param(_HTTPS, "data:text/plain,hi", id="different-scheme-data"),
        pytest.param(_HTTPS, "wsabc://host", id="same-length-different-scheme"),
        pytest.param(_HTTPS, "HTTPS://host/p", id="same-scheme-uppercase-rejoined"),
        pytest.param(_HTTPS, "HtTpS://host/p", id="same-scheme-mixed-case-rejoined"),
        pytest.param(_HTTPS, "https:/one-slash", id="same-scheme-single-slash-rejoined"),
        pytest.param(_HTTPS, "https:opaque", id="same-scheme-no-slashes-rejoined"),
        pytest.param(_HTTPS, "https:", id="same-scheme-bare-rejoined"),
        pytest.param(_HTTPS, "https://", id="same-scheme-empty-authority-rejoined"),
        pytest.param(_HTTPS, "https://?q", id="same-scheme-empty-authority-query-rejoined"),
        pytest.param(_HTTPS, "https://#f", id="same-scheme-empty-authority-fragment-rejoined"),
        pytest.param(_HTTPS, "/root", id="relative-root"),
        pytest.param(_HTTPS, "#frag", id="relative-fragment"),
        pytest.param(_HTTPS, "?query", id="relative-query"),
        pytest.param(_HTTPS, "path/to.html", id="relative-path-with-slash"),
        pytest.param(_HTTPS, "bareword", id="relative-bareword-no-colon"),
        pytest.param(_HTTPS, "s3://bucket/key", id="scheme-with-digit"),
        pytest.param(_HTTPS, "a+b://host", id="scheme-with-plus"),
        pytest.param(_HTTPS, "a-b://host", id="scheme-with-hyphen"),
        pytest.param(_HTTPS, "a.b://host", id="scheme-with-dot"),
        pytest.param("http://ex.test/a/b", "http://other.test/x", id="http-base-same-scheme"),
        pytest.param("ftp://f.test/g/", "ftp://h.test/x", id="netloc-base-not-http-not-skipped"),
        pytest.param("s3://n.test/", "s3://m.test/x", id="base-scheme-with-digit"),
        pytest.param("a+b://n.test/", "a+b://m.test/x", id="base-scheme-with-plus"),
        pytest.param("a-b://n.test/", "a-b://m.test/x", id="base-scheme-with-hyphen"),
        pytest.param("a.b://n.test/", "a.b://m.test/x", id="base-scheme-with-dot"),
        pytest.param("abcde://n.test/", "abcde://m.test/x", id="five-char-base-scheme-not-https"),
        pytest.param("news://n.test/", "news://m.test/x", id="four-char-base-scheme-not-http"),
        pytest.param("", "https://host/x", id="empty-base-absolute"),
        pytest.param("", "relative/x", id="empty-base-relative"),
        pytest.param("/no-scheme/base", "https://host/x", id="schemeless-base"),
        pytest.param("http", "https://host/x", id="base-scheme-without-colon"),
        pytest.param("ht tp://x/", "https://host/x", id="base-scheme-invalid-char"),
        pytest.param("abcdefghijklmnop://h/", "abcdefghijklmnop://y", id="base-scheme-too-long"),
    ],
)
def test_resolve_matches_urljoin(base: str, href: str) -> None:
    assert _resolved_href(href, base) == urljoin(base, href)


# A tab/CR/LF inside an otherwise-skippable absolute link forces the rejoin urlsplit would strip it in; a character
# reference carries the raw control byte past the input stream's CR/LF normalization so each lands in the value.
@pytest.mark.parametrize(
    ("reference", "char"),
    [
        pytest.param("&#9;", "\t", id="tab"),
        pytest.param("&#13;", "\r", id="carriage-return"),
        pytest.param("&#10;", "\n", id="line-feed"),
    ],
)
def test_same_scheme_absolute_with_internal_control_char_is_rejoined(reference: str, char: str) -> None:
    root = parse_fragment(f'<a href="https://host/a{reference}b">x</a>')
    root.resolve_links(_HTTPS)
    (link,) = root.links()
    assert link.url == urljoin(_HTTPS, f"https://host/a{char}b")


def test_replacement_string_is_substituted() -> None:
    assert _rewritten('<a href="a">x</a>', lambda url: url.upper()) == '<a href="A">x</a>'


def _rewritten(html: str, replace: Callable[[str], str | int | None]) -> str:
    root = parse_fragment(html)
    root.rewrite_links(replace)  # ty: ignore[invalid-argument-type]  # passes a wrong type on purpose to check the runtime rejection
    return root.inner_html


def test_returning_none_leaves_the_link_unchanged() -> None:
    assert _rewritten('<a href="a">x</a>', lambda _url: None) == '<a href="a">x</a>'


def test_returning_the_same_string_is_not_a_mutation() -> None:
    assert _rewritten('<a href="a">x</a>', lambda url: url) == '<a href="a">x</a>'


_CSS_HOSTILE: Final[str] = "a\"b'c(d)e f\\g\nh\x01i\x7fj</style>"


@pytest.mark.parametrize(
    ("css", "expected"),
    [
        pytest.param('url("x.png")', 'url("a\\"b\'c(d)e f\\\\g\\a h\\1 i\\7f j<\\/style>")', id="double-quoted"),
        pytest.param("url('x.png')", "url('a\"b\\'c(d)e f\\\\g\\a h\\1 i\\7f j<\\/style>')", id="single-quoted"),
        pytest.param("url(x.png)", "url(a\\\"b\\'c\\(d\\)e\\20 f\\\\g\\a h\\1 i\\7f j<\\/style>)", id="bare"),
        pytest.param("@import 'x.png'", "@import 'a\"b\\'c(d)e f\\\\g\\a h\\1 i\\7f j<\\/style>'", id="import"),
    ],
)
def test_a_css_replacement_is_written_as_css_spells_it(css: str, expected: str) -> None:
    style = Element("style")
    style.append(Text(f"a{{background:{css}}} b{{content:'after'}}"))
    style.rewrite_links(lambda _url: _CSS_HOSTILE)
    # the replacement stays one URL, and the text after it stays what it was
    assert style.text == f"a{{background:{expected}}} b{{content:'after'}}"
    assert [link.url for link in style.links()] == [_CSS_HOSTILE]


def test_an_attribute_replacement_is_written_verbatim() -> None:
    root = parse_fragment('<a href="x">t</a>')
    root.rewrite_links(lambda _url: _CSS_HOSTILE)
    (link,) = root.links()
    assert link.element.attrs["href"] == _CSS_HOSTILE


def test_only_some_candidates_change() -> None:
    out = _rewritten('<img srcset="a.png 1x, b.png 2x">', lambda url: "X" if url == "a.png" else None)
    assert out == '<img srcset="X 1x, b.png 2x">'


def test_rewrite_srcset_url_with_comma() -> None:
    out = _rewritten('<img srcset="a,b 1x, c 2x">', lambda url: "X" if url == "a,b" else None)
    assert out == '<img srcset="X 1x, c 2x">'


def test_rewrite_srcset_candidate_after_parenthesised_descriptor() -> None:
    out = _rewritten('<img srcset="hero.jpg (w,h),//cdn.test/hero.jpg 1x">', lambda url: "X" if "//" in url else None)
    assert out == '<img srcset="hero.jpg (w,h),X 1x">'


def test_rewrite_srcset_leaves_a_candidate_with_invalid_descriptors_alone() -> None:
    out = _rewritten('<img srcset="a.jpg 1w 2w, b.jpg 2x">', lambda _url: "X")
    assert out == '<img srcset="a.jpg 1w 2w, X 2x">'


def test_a_shorter_replacement_shrinks_the_value() -> None:
    assert _rewritten('<a href="a-long-url">x</a>', lambda _url: "z") == '<a href="z">x</a>'


def test_replace_receives_each_url() -> None:
    seen: list[str] = []

    def collect(url: str) -> None:
        seen.append(url)

    _rewritten('<a href="h" ping="p1 p2"><img src="i"></a>', collect)
    assert seen == ["h", "p1", "p2", "i"]


def test_a_non_string_non_none_result_is_a_type_error() -> None:
    with pytest.raises(TypeError, match="must return str or None"):
        _rewritten('<a href="a">x</a>', lambda _url: 42)


def test_an_exception_from_replace_propagates() -> None:
    with pytest.raises(ValueError, match="nope"):
        _rewritten('<a href="a">x</a>', _boom)


def _boom(url: str) -> str:
    msg = f"nope: {url}"
    raise ValueError(msg)


def test_meta_refresh_rewrite_replaces_the_target_a_browser_follows() -> None:
    seen: list[str] = []

    def gateway(url: str) -> str:
        seen.append(url)
        return "/out"

    html = (
        '<meta http-equiv="refresh" content="0;/one?url=/two">'
        '<meta http-equiv="refresh" content="0;/one">'
        '<meta http-equiv="refresh" content="0;url=/three /four">'
    )
    assert _rewritten(html, gateway) == (
        '<meta http-equiv="refresh" content="0;/out">'
        '<meta http-equiv="refresh" content="0;/out">'
        '<meta http-equiv="refresh" content="0;url=/out">'
    )
    assert seen == ["/one?url=/two", "/one", "/three /four"]


def test_an_exception_during_a_meta_refresh_rewrite_stops_the_walk() -> None:
    # the meta content fails first, so the element's remaining attributes are not visited
    with pytest.raises(ValueError, match="nope: next"):
        _rewritten('<meta http-equiv=refresh content="5; url=next.html" id=m>', _boom)


def test_an_exception_on_the_first_of_several_style_texts_stops_the_walk() -> None:
    style = Element("style")
    style.append(Text("a{background:url(one.png)}"))
    style.append(Text("b{background:url(two.png)}"))
    seen: list[str] = []

    def boom(url: str) -> str:
        seen.append(url)
        msg = "stop"
        raise ValueError(msg)

    with pytest.raises(ValueError, match="stop"):
        style.rewrite_links(boom)
    assert seen == ["one.png"]  # the second text node was never reached


def test_replace_moving_the_walked_node_to_another_tree_finishes_on_the_old_one() -> None:
    fragment = parse_fragment('<p><a href="/a">1</a><a href="/b">2</a></p>')
    host = Element("div")
    seen: list[str] = []

    def replace(url: str) -> str:
        seen.append(url)
        host.append(fragment)  # fragment is the only wrapper of its tree, so the move leaves the walk its last owner
        return f"{url}?t"

    fragment.rewrite_links(replace)
    assert seen == ["/a", "/b"]
    # the copy the move made left before the first replacement was written, and the walk stays on the tree it began in
    assert host.inner_html == '<div><p><a href="/a">1</a><a href="/b">2</a></p></div>'


def test_rewrite_links_requires_a_callable() -> None:
    with pytest.raises(TypeError, match="expected a callable"):
        parse_fragment("<a href=a>x</a>").rewrite_links("not callable")  # ty: ignore[invalid-argument-type]  # passes a wrong type on purpose to check the runtime rejection


@pytest.mark.parametrize(
    "depth", [pytest.param(0, id="ordinary"), pytest.param(16, id="nested"), pytest.param(128, id="beyond-dns-length")]
)
@pytest.mark.parametrize(
    ("base", "host"),
    [
        pytest.param("example.co.uk", "example.co.uk", id="normal-shared"),
        pytest.param("www.ck", "www.ck", id="exception-shared"),
        pytest.param("münchen.de", "xn--mnchen-3ya.de", id="idna-shared"),
    ],
)
def test_external_links_drop_the_public_suffix_boundary(base: str, host: str, *, depth: int) -> None:
    href: Final = f"https://{'s.' * depth}{host}/x"
    assert extract_links(f'<a href="{href}">x</a>', f"https://{base}/", external_only=True) == set()


# An unclosed formatting element is reopened in every block that follows it, and each of those clones borrows the
# attribute values of the one start tag. The walk hands the clones one result instead of a copy of it apiece.
_REOPENED: Final[str] = '<p><a href="a.html" ping="p1 p2" title="t">x<p>y<p>z'


def test_links_of_a_reopened_element_are_enumerated_on_each_clone() -> None:
    links = parse_fragment(_REOPENED).links()
    assert [(link.attribute, link.url) for link in links] == [("href", "a.html"), ("ping", "p1"), ("ping", "p2")] * 3
    assert [link.element.text for link in links] == ["x"] * 3 + ["y"] * 3 + ["z"] * 3


def test_a_reopened_element_is_resolved_on_each_clone() -> None:
    anchor: Final = (
        '<a href="https://example.com/dir/a.html" ping="https://example.com/dir/p1 https://example.com/dir/p2"'
        ' title="t">'
    )
    assert _resolved(_REOPENED) == f"<p>{anchor}x</a></p><p>{anchor}y</a></p><p>{anchor}z</a></p>"


# the start tag's element takes the ordinary path; its first clone computes the shared result, the other clones adopt it
@pytest.mark.parametrize(
    ("start_tag", "replaced"),
    [
        pytest.param('<a href="https://other.test/x">', ["https://other.test/x"] * 2, id="absolute"),
        pytest.param('<b cite="" style="color:red">', [], id="no-link"),
    ],
)
def test_a_reopened_element_with_nothing_to_rewrite_is_left_alone(start_tag: str, replaced: list[str]) -> None:
    html: Final = f"<p>{start_tag}x<p>y<p>z"
    seen: list[str] = []
    root = parse_fragment(html)
    before: Final = root.inner_html
    root.resolve_links(_RESOLVE_BASE)
    root.rewrite_links(seen.append)
    assert root.inner_html == before
    assert seen == replaced


def test_many_reopened_elements_grow_the_shared_value_table() -> None:
    # the table starts with 16 slots and grows past half load at its ninth distinct value; the formatting list reopens
    # at most 16 elements, so twelve distinct values fit in one run
    elements: Final = "".join(f'<b cite="c{index}">' for index in range(12))
    root = parse_fragment(f"<p>{elements}x<p>y<p>z")
    root.resolve_links(_HTTPS)
    assert [link.url for link in root.links()] == [f"{_HTTPS}c{index}" for index in range(12)] * 3


def test_resolved_clones_stay_independent() -> None:
    root = parse_fragment(_REOPENED)
    root.resolve_links(_RESOLVE_BASE)
    _, second, third = root.select("a")
    second.attrs["href"] = "other.html"
    assert third.attrs["href"] == "https://example.com/dir/a.html"
    root.resolve_links(_RESOLVE_BASE)
    assert second.attrs["href"] == "https://example.com/dir/other.html"


def test_resolving_a_reopened_element_records_a_mutation_per_clone() -> None:
    root = parse_fragment(_REOPENED)
    observer = MutationObserver()
    observer.observe(root, attributes=True, subtree=True, attribute_old_value=True)
    root.resolve_links(_RESOLVE_BASE)
    records = observer.take_records()
    assert [(record.attribute_name, record.old_value) for record in records] == [
        ("href", "a.html"),
        ("ping", "p1 p2"),
    ] * 3
    assert [record.target for record in records[::2]] == root.select("a")


def test_replace_receives_each_url_of_each_clone() -> None:
    seen: list[str] = []

    def prefix(url: str) -> str:
        seen.append(url)
        return f"/moved/{url}"

    out = _rewritten(_REOPENED, prefix)
    anchor: Final = '<a href="/moved/a.html" ping="/moved/p1 /moved/p2" title="t">'
    assert out == f"<p>{anchor}x</a></p><p>{anchor}y</a></p><p>{anchor}z</a></p>"
    assert seen == ["a.html", "p1", "p2"] * 2


def test_clones_adopt_the_rewrite_of_the_first_clone() -> None:
    seen: list[str] = []

    def rename(url: str) -> str:
        seen.append(url)
        return f"{url}-moved"

    root = parse_fragment('<p><a href="a.html">0<p>1<p>2<p>3')
    root.rewrite_links(rename)
    assert seen == ["a.html"] * 2
    assert [link.url for link in root.links()] == ["a.html-moved"] * 4


def test_an_exception_from_replace_on_a_clone_propagates() -> None:
    seen: list[str] = []

    def fail_on_the_clone(url: str) -> str:
        seen.append(url)
        return _boom(url) if len(seen) == 2 else url

    with pytest.raises(ValueError, match=r"nope: a\.html"):
        _rewritten('<p><a href="a.html">x<p>y<p>z', fail_on_the_clone)
    assert seen == ["a.html"] * 2


def test_a_reopened_element_without_links_reports_none() -> None:
    assert parse_fragment('<p><a href=" ">x<p>y<p>z').links() == []


def test_clones_whose_attribute_replace_removed_are_each_rewritten() -> None:
    root = parse_fragment('<p><a href="a.html">x<p>y<p>z<p>w')
    anchors: Final = root.select("a")
    seen: list[str] = []

    def drop_from_the_clone(url: str) -> None:
        seen.append(url)
        del anchors[len(seen) - 1].attrs["href"]

    root.rewrite_links(drop_from_the_clone)
    assert seen == ["a.html"] * 4
    assert [anchor.attrs.get("href") for anchor in anchors] == [None] * 4


def test_a_long_href_on_a_reopened_element_is_not_copied_per_clone() -> None:
    # without sharing, each clone would retain its own copy of the long value
    # PyPy ships no _tracemalloc; the sharing is C and covered by the CPython run.
    tracemalloc = pytest.importorskip("tracemalloc")
    href: Final = "x" * 25_000
    html: Final = f'<p><a href="{href}">x{"<p>y" * 1_500}'
    resolved, rewritten = parse(html), parse(html)
    tracemalloc.start()
    resolved.resolve_links(_HTTPS)
    rewritten.rewrite_links(lambda url: f"/moved/{url}")
    links = resolved.links()
    retained = tracemalloc.get_traced_memory()[0]
    tracemalloc.stop()
    assert [link.url for link in links] == [_HTTPS + href] * 1_501
    assert [link.url for link in rewritten.links()] == [f"/moved/{href}"] * 1_501
    assert retained < 4_000_000, f"the link walk retained {retained} bytes"


def test_rewriting_reopened_clones_is_linear_in_the_value_length(work_steps: WorkSteps) -> None:
    work_steps.assert_linear(lambda size: partial(_rewrite_reopened_clones, size), (500, 2000))


def _rewrite_reopened_clones(size: int) -> None:
    parse(f'<p><a href="/{"x" * size}">x{"<p>y" * size}').rewrite_links(lambda url: f"/moved{url}")


@pytest.mark.parametrize(
    "depth", [pytest.param(0, id="ordinary"), pytest.param(16, id="nested"), pytest.param(128, id="beyond-dns-length")]
)
@pytest.mark.parametrize(
    ("base", "host"),
    [
        pytest.param("example.co.uk", "other.co.uk", id="normal-distinct"),
        pytest.param("a.github.io", "b.github.io", id="private-distinct"),
        pytest.param("a.ck", "b.ck", id="wildcard-distinct"),
        pytest.param("example.invalid", "s.example.invalid", id="unknown-host-compared-whole"),
        pytest.param(
            "a.foo.001.test.code-builder-stg.platform.salesforce.com",
            "b.foo.001.test.code-builder-stg.platform.salesforce.com",
            id="deepest-wildcard-distinct",
        ),
    ],
)
def test_external_links_keep_the_public_suffix_boundary(base: str, host: str, *, depth: int) -> None:
    href: Final = f"https://{'s.' * depth}{host}/x"
    assert extract_links(f'<a href="{href}">x</a>', f"https://{base}/", external_only=True) == {href}
