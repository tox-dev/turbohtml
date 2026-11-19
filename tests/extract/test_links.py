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
    html: Final[str] = (
        '<a href="http://0x7f.0.0.1/a">1</a><a href="http://127.1/b">2</a><a href="http://8.8.8.8/c">3</a>'
    )
    assert extract_links(html, "http://127.0.0.1/", external_only=True) == {"http://8.8.8.8/c"}


def test_external_only_reports_hosts_whose_dots_only_show_once_canonical() -> None:
    # a browser opens these as mirror.test and 192.168.1.1; the spelled host holding no dot must not hide the link
    html: Final[str] = (
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
    html: Final[str] = '<a href="http://evil.example\\@good.example/">x</a>'
    assert extract_links(html, "http://good.example/", external_only=True) == {"http://evil.example/@good.example/"}


def test_external_only_follows_a_same_scheme_base_href_ending_in_colon() -> None:
    # an empty port makes the base name other.test, so a browser sends the page's relative links there
    html: Final[str] = '<base href="https://other.test:"><a href="/login">x</a><a href="reset">y</a>'
    expected: Final[set[str]] = {"https://other.test/login", "https://other.test/reset"}
    assert extract_links(html, "https://good.example/news/today") == expected
    assert extract_links(html, "https://good.example/news/today", external_only=True) == expected


def test_external_only_ignores_a_base_inside_template_contents() -> None:
    # a browser takes its base from the live <base>, not the inert one in <template>, so /login leaves the site
    html: Final[str] = (
        '<template><base href="http://good.example/"></template>'
        '<base href="http://evil.example/"><a href="/login">x</a>'
    )
    assert extract_links(html, "http://good.example/page", external_only=True) == {"http://evil.example/login"}


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


@pytest.mark.parametrize(
    "href",
    [
        pytest.param("https://%64e.test.com/page", id="percent-encoded-label"),
        pytest.param("https://\uff44\uff45.test.com/page", id="fullwidth-label"),
        pytest.param("https://de\uff0etest.com/page", id="fullwidth-separator"),
    ],
)
def test_strict_language_filter_screens_the_canonical_host(href: str) -> None:
    html = f'<a href="{href}">x</a>'
    assert extract_links(html, _BASE, UrlCleaning(strict=True, language="en")) == set()


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
    style: Final[Element] = Element("style")
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
        pytest.param('<template><meta http-equiv="refresh" content="5;url=x"></template>', id="inside-template"),
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


@pytest.mark.parametrize(
    ("html", "attribute"),
    [
        pytest.param('<a href="u">x</a>', "href", id="attribute"),
        pytest.param("<style>a{b:url(u)}</style>", None, id="style"),
    ],
)
def test_link_record_hashes_and_compares_like_its_tuple(html: str, attribute: str | None) -> None:
    (link,) = parse_fragment(html).links()
    twin = (link.element, attribute, link.url)
    assert (hash(link) == hash(twin), link == twin, {twin: 1}[link], len({link, twin})) == (True, True, 1, 1)


def test_link_record_is_a_link_with_named_tuple_methods() -> None:
    (link,) = parse_fragment('<a href="u">x</a>').links()
    replaced = link._replace(url="v")
    assert (type(link) is Link, type(replaced) is Link, replaced.url, link.url) == (True, True, "v", "u")
    assert link._asdict() == {"element": link.element, "attribute": "href", "url": "u"}
    assert repr(link) == f"Link(element={link.element!r}, attribute='href', url='u')"


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
            'url("https://example.com/remote.test/x.png")',
            id="escaped-backslash",
        ),
        pytest.param("url('a\\'b.png')", "url('https://example.com/dir/a\\'b.png')", id="escaped-quote"),
        pytest.param("url(a\\)b.png)", "url(https://example.com/dir/a\\)b.png)", id="bare-escaped-parenthesis"),
        pytest.param("url(\\61 /b\\ c.png)", "url(https://example.com/dir/a/b%20c.png)", id="bare-escaped-space"),
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
    html: Final[str] = '<style>a{background:url("x<\\/style>y")}</style>'
    assert _resolved(html) == '<style>a{background:url("https://example.com/dir/x<\\/style>y")}</style>'


def test_css_comments_and_strings_are_not_resolved() -> None:
    html: Final[str] = "<style>/* url(hidden.png) */a{content:'url(fake.png)';background:url(real.png)}</style>"
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
        pytest.param(_RESOLVE_BASE, "///elsewhere.test", "https://elsewhere.test/", id="host-only"),
        pytest.param(_RESOLVE_BASE, "///elsewhere.test?q#f", "https://elsewhere.test/?q#f", id="host-then-query"),
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
        pytest.param(_HTTPS, "ftp:///elsewhere.test/x", "ftp://elsewhere.test/x", id="foreign-special-scheme"),
        pytest.param(_HTTPS, "WSS:////elsewhere.test/x", "wss://elsewhere.test/x", id="foreign-uppercase-four-slashes"),
        pytest.param(_HTTPS, "http:///elsewhere.test?q#f", "http://elsewhere.test/?q#f", id="foreign-host-then-query"),
        pytest.param(_HTTPS, "ftp:///", "ftp:///", id="foreign-special-no-host"),
        pytest.param(_HTTPS, "sftp:///elsewhere.test/x", "sftp:///elsewhere.test/x", id="foreign-non-special-verbatim"),
        pytest.param(_HTTPS, "file:///elsewhere.test/x", "file:///elsewhere.test/x", id="foreign-file-verbatim"),
        pytest.param(
            "foo://f.test/a", "ws:///elsewhere.test/x", "ws://elsewhere.test/x", id="non-special-base-foreign"
        ),
        pytest.param(
            "file:///srv/www/page.html", "wss:///elsewhere.test/x", "wss://elsewhere.test/x", id="file-base-foreign"
        ),
        pytest.param(_HTTPS, "ftp:", "ftp:", id="foreign-scheme-no-slashes"),
        pytest.param(_HTTPS, "ftp:elsewhere.test/x", "ftp://elsewhere.test/x", id="foreign-special-no-slash"),
        pytest.param(_HTTPS, "ftp:/elsewhere.test/x", "ftp://elsewhere.test/x", id="foreign-special-one-slash"),
        pytest.param(_HTTPS, "ftp:\\elsewhere.test/x", "ftp://elsewhere.test/x", id="foreign-special-one-backslash"),
        pytest.param(_HTTPS, "ftp:elsewhere.test\\x", "ftp://elsewhere.test/x", id="foreign-special-backslash-path"),
        pytest.param(
            _HTTPS, "https://elsewhere.test/x\\y", "https://elsewhere.test/x/y", id="same-scheme-backslash-path"
        ),
        pytest.param(_HTTPS, "x\\y", "https://example.com/dir/x/y", id="relative-backslash-path"),
        pytest.param(_HTTPS, "WSS://elsewhere.test/x", "wss://elsewhere.test/x", id="foreign-uppercase-scheme-host"),
        pytest.param(_HTTPS, "ftp://elsewhere.test\\x", "ftp://elsewhere.test/x", id="foreign-backslash-in-path"),
        pytest.param(_HTTPS, "ftp://\\elsewhere.test/x", "ftp://elsewhere.test/x", id="foreign-backslash-authority"),
        pytest.param(_HTTPS, "ftp://?q", "ftp://?q", id="foreign-no-host"),
        pytest.param(_HTTPS, "ftp://#f", "ftp://#f", id="foreign-no-host-fragment"),
        pytest.param(_HTTPS, "ftp://", "ftp://", id="foreign-special-authority-only"),
        pytest.param(_HTTPS, "ftp://elsewhere.test/x", "ftp://elsewhere.test/x", id="foreign-special-host-kept"),
        pytest.param(_HTTPS, "ftp:///elsewhere.test:21/x", "ftp://elsewhere.test/x", id="foreign-default-port-dropped"),
        pytest.param(_HTTPS, "ftp:\\\\\\elsewhere.test/x", "ftp://elsewhere.test/x", id="foreign-backslash-authority"),
    ],
)
def test_resolve_links_extra_authority_slash_boundaries(base: str, href: str, expected: str) -> None:
    assert _resolved_href(href, base) == expected


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        pytest.param("file://h", "file://h/", id="host-gets-root-path"),
        pytest.param("file://h?q", "file://h/?q", id="host-query-gets-root-path"),
        pytest.param("file://h#f", "file://h/#f", id="host-fragment-gets-root-path"),
        pytest.param("file://localhost/a", "file:///a", id="localhost-dropped"),
        pytest.param("file://LOCALHOST/a", "file:///a", id="upper-case-localhost-dropped"),
        pytest.param("file:///a/b", "file:///a/b", id="empty-host-kept"),
        pytest.param("file:a/b", "file:///a/b", id="no-slashes-gets-empty-host"),
        pytest.param("file:", "file:///", id="scheme-only-gets-root"),
        pytest.param("file:/a", "file:///a", id="one-slash-gets-empty-host"),
        pytest.param("file://C:/x", "file:///C:/x", id="drive-letter-host-moved-to-path"),
        pytest.param("file:c:\\x", "file:///c:/x", id="drive-letter-backslash"),
        pytest.param("file://h/a/../b", "file://h/b", id="dot-segments-removed"),
        pytest.param("FILE://H/a", "file://h/a", id="scheme-and-host-lowercased"),
        pytest.param("file://h:99999/a", "file://h:99999/a", id="out-of-range-port-kept-as-written"),
    ],
)
def test_resolve_links_reads_a_file_link_as_the_url_standard_does(href: str, expected: str) -> None:
    assert _resolved_href(href, _HTTPS) == expected


@pytest.mark.parametrize(
    ("base", "href", "expected"),
    [
        pytest.param("file:///srv/page.html", "file://h", "file://h/", id="file-base-host-gets-root-path"),
        pytest.param("file:///srv/page.html", "file:x", "file:///srv/x", id="file-base-relative-kept"),
    ],
)
def test_resolve_links_reads_a_file_link_under_a_file_base(base: str, href: str, expected: str) -> None:
    assert _resolved_href(href, base) == expected


@pytest.mark.parametrize(
    ("base", "href", "expected"),
    [
        pytest.param("http:opaque", "x", "http://opaque/x", id="relative"),
        pytest.param("http:opaque", "/x", "http://opaque/x", id="root-relative"),
        pytest.param("http:opaque", "../x", "http://opaque/x", id="dot-segment"),
        pytest.param("http:opaque", "?q", "http://opaque/?q", id="query"),
        pytest.param("http:opaque", "#f", "http://opaque/#f", id="fragment"),
        pytest.param("http:opaque", "//h/x", "http://h/x", id="network-path"),
        pytest.param("http:a/b", "x", "http://a/x", id="path-after-host"),
        pytest.param("http:opaque#old", "x", "http://opaque/x", id="old-fragment-dropped"),
        pytest.param("https:opaque?q#old", "#f", "https://opaque/?q#f", id="query-kept-fragment-replaced"),
        pytest.param("http:/a", "x", "http://a/x", id="one-slash-host"),
        pytest.param("http:///opaque", "x", "http://opaque/x", id="extra-slashes-host"),
        pytest.param("http:///opaque", "#f", "http://opaque/#f", id="extra-slashes-fragment"),
        pytest.param("http:////opaque/a", "x", "http://opaque/x", id="four-slashes-path-replaced"),
    ],
)
def test_resolve_links_reads_the_host_of_a_special_base_without_authority(base: str, href: str, expected: str) -> None:
    assert _resolved_href(href, base) == expected


@pytest.mark.parametrize(
    ("base", "href"),
    [
        pytest.param("http:", "x", id="special-base-without-host"),
        pytest.param("http:/", "x", id="special-base-one-slash"),
        pytest.param("https://h:99999/a", "x", id="port-out-of-range"),
        pytest.param("https://[h/a", "x", id="unbalanced-ipv6"),
    ],
)
def test_resolve_links_leaves_links_under_a_base_the_url_standard_rejects_as_written(base: str, href: str) -> None:
    assert _resolved_href(href, base) == href


def test_resolve_links_keeps_an_over_length_base_as_written() -> None:
    base = f"https://H/{'a/' * 1100}"
    assert _resolved_href("x", base) == f"{base}x"


@pytest.mark.parametrize(
    ("base", "href", "expected"),
    [
        pytest.param("https://H.Example/a/b", "x", "https://h.example/a/x", id="host-lowercased"),
        pytest.param("HTTPS://H/", "x", "https://h/x", id="scheme-and-host-lowercased"),
        pytest.param("https://h:443/a", "x", "https://h/x", id="default-port-dropped"),
        pytest.param("https://h:80/x/./y/..", "z", "https://h:80/x/z", id="other-port-kept-dot-segments-removed"),
        pytest.param("https://h/a/../b/", "x", "https://h/b/x", id="dot-segments-in-base-path"),
        pytest.param("https://\u00fc.example/a", "x", "https://xn--tda.example/x", id="non-ascii-host-punycoded"),
        pytest.param("https://[::1]:443/a", "x", "https://[::1]/x", id="ipv6-default-port-dropped"),
        pytest.param("foo://H/a", "x", "foo://H/x", id="non-special-host-kept"),
        pytest.param("file:a/b", "x", "file:///a/x", id="file-base-without-authority"),
        pytest.param("a/b", "x", "a/x", id="relative-base-joined-as-written"),
    ],
)
def test_resolve_links_normalizes_the_base_before_joining(base: str, href: str, expected: str) -> None:
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


# WHATWG removes dot segments (%2e in any case included) from an absolute special-scheme URL and drops its default or
# empty port (https://url.spec.whatwg.org/#port-state), which urljoin leaves as written.
@pytest.mark.parametrize(
    ("href", "expected"),
    [
        pytest.param("https://host/a/../b", "https://host/b", id="dot-dot"),
        pytest.param("https://host/a/./b", "https://host/a/b", id="dot"),
        pytest.param("https://host/a/%2E%2e/b", "https://host/b", id="encoded-dot-dot"),
        pytest.param("https://host/a/.%2e/b", "https://host/b", id="mixed-dot-dot"),
        pytest.param("https://host/a/..", "https://host/", id="dot-dot-to-root"),
        pytest.param("https://host:443/p", "https://host/p", id="https-default-port"),
        pytest.param("https://host:0443/p", "https://host/p", id="https-default-port-leading-zeros"),
        pytest.param("https://host:/p", "https://host/p", id="empty-port"),
        pytest.param("https://host:0080/p", "https://host:80/p", id="leading-zeros-rewritten"),
        pytest.param("https://host:8443/p", "https://host:8443/p", id="non-default-port-kept"),
        pytest.param("https://u:p@host:443/p", "https://u:p@host/p", id="userinfo-default-port"),
        pytest.param("https://[::1]:443/p", "https://[::1]/p", id="ipv6-default-port"),
        pytest.param("http://host:80/p", "http://host/p", id="http-default-port"),
        pytest.param("ws://host:80/p", "ws://host/p", id="ws-default-port"),
        pytest.param("wss://host:443/p", "wss://host/p", id="wss-default-port"),
        pytest.param("ftp://host:21/p", "ftp://host/p", id="ftp-default-port"),
        pytest.param("https://HOST.Example/Path", "https://host.example/Path", id="host-lowercased"),
        pytest.param("https://U:P@HOST.example/p", "https://U:P@host.example/p", id="userinfo-case-kept"),
        pytest.param("https://[::A]:443/p", "https://[::a]/p", id="ipv6-lowercased"),
    ],
)
def test_resolve_links_normalizes_absolute_special_urls(href: str, expected: str) -> None:
    assert _resolved_href(href, _HTTPS) == expected


# Every scheme with a port, empty, zero-padded, default, invalid or out-of-range, and every path that holds a dot-like
# segment, against the WHATWG URL parser; a rejected reference stays as written.
@pytest.mark.parametrize(
    ("href", "expected"),
    [
        pytest.param("https://h:443/p", "https://h/p", id="https-h-443-p"),
        pytest.param("https://h:80/p", "https://h:80/p", id="https-h-80-p"),
        pytest.param("https://h:21/p", "https://h:21/p", id="https-h-21-p"),
        pytest.param("https://h:/p", "https://h/p", id="https-h-p"),
        pytest.param("https://h:0443/p", "https://h/p", id="https-h-0443-p"),
        pytest.param("https://h:0080/p", "https://h:80/p", id="https-h-0080-p"),
        pytest.param("https://h:8080/p", "https://h:8080/p", id="https-h-8080-p"),
        pytest.param("https://h:x/p", "https://h:x/p", id="https-h-x-p"),
        pytest.param("https://h:99999/p", "https://h:99999/p", id="https-h-99999-p"),
        pytest.param("https://h:443", "https://h/", id="https-h-443"),
        pytest.param("https://h:443?q#f", "https://h/?q#f", id="https-h-443-q-f"),
        pytest.param("https://h1/p", "https://h1/p", id="https-h1-p"),
        pytest.param("https://h:1", "https://h:1/", id="https-h-1"),
        pytest.param("https://u:1@h:443/p", "https://u:1@h/p", id="https-u-1-h-443-p"),
        pytest.param("https://[::1]:443/p", "https://[::1]/p", id="https-1-443-p"),
        pytest.param("https://h/a/%2/b", "https://h/a/%2/b", id="https-h-a-2-b"),
        pytest.param("https://h/a/...", "https://h/a/...", id="https-h-a"),
        pytest.param("https://h/%2e", "https://h/", id="https-h-2e"),
        pytest.param("https://h/a/%2e.", "https://h/", id="https-h-a-2e"),
        pytest.param("https://h/a/.%2e", "https://h/", id="https-h-a-2e"),
        pytest.param("https://h/a/./b/../c", "https://h/a/c", id="https-h-a-b-c"),
        pytest.param("http://h:443/p", "http://h:443/p", id="http-h-443-p"),
        pytest.param("http://h:80/p", "http://h/p", id="http-h-80-p"),
        pytest.param("http://h:21/p", "http://h:21/p", id="http-h-21-p"),
        pytest.param("http://h:/p", "http://h/p", id="http-h-p"),
        pytest.param("http://h:0443/p", "http://h:443/p", id="http-h-0443-p"),
        pytest.param("http://h:0080/p", "http://h/p", id="http-h-0080-p"),
        pytest.param("http://h:8080/p", "http://h:8080/p", id="http-h-8080-p"),
        pytest.param("http://h:x/p", "http://h:x/p", id="http-h-x-p"),
        pytest.param("http://h:99999/p", "http://h:99999/p", id="http-h-99999-p"),
        pytest.param("http://h:443", "http://h:443/", id="http-h-443"),
        pytest.param("http://h:443?q#f", "http://h:443/?q#f", id="http-h-443-q-f"),
        pytest.param("http://h1/p", "http://h1/p", id="http-h1-p"),
        pytest.param("http://h:1", "http://h:1/", id="http-h-1"),
        pytest.param("http://u:1@h:443/p", "http://u:1@h:443/p", id="http-u-1-h-443-p"),
        pytest.param("http://[::1]:443/p", "http://[::1]:443/p", id="http-1-443-p"),
        pytest.param("http://h/a/%2/b", "http://h/a/%2/b", id="http-h-a-2-b"),
        pytest.param("http://h/a/...", "http://h/a/...", id="http-h-a"),
        pytest.param("http://h/%2e", "http://h/", id="http-h-2e"),
        pytest.param("http://h/a/%2e.", "http://h/", id="http-h-a-2e"),
        pytest.param("http://h/a/.%2e", "http://h/", id="http-h-a-2e"),
        pytest.param("http://h/a/./b/../c", "http://h/a/c", id="http-h-a-b-c"),
        pytest.param("ws://h:443/p", "ws://h:443/p", id="ws-h-443-p"),
        pytest.param("ws://h:80/p", "ws://h/p", id="ws-h-80-p"),
        pytest.param("ws://h:21/p", "ws://h:21/p", id="ws-h-21-p"),
        pytest.param("ws://h:/p", "ws://h/p", id="ws-h-p"),
        pytest.param("ws://h:0443/p", "ws://h:443/p", id="ws-h-0443-p"),
        pytest.param("ws://h:0080/p", "ws://h/p", id="ws-h-0080-p"),
        pytest.param("ws://h:8080/p", "ws://h:8080/p", id="ws-h-8080-p"),
        pytest.param("ws://h:x/p", "ws://h:x/p", id="ws-h-x-p"),
        pytest.param("ws://h:99999/p", "ws://h:99999/p", id="ws-h-99999-p"),
        pytest.param("ws://h:443", "ws://h:443/", id="ws-h-443"),
        pytest.param("ws://h:443?q#f", "ws://h:443/?q#f", id="ws-h-443-q-f"),
        pytest.param("ws://h1/p", "ws://h1/p", id="ws-h1-p"),
        pytest.param("ws://h:1", "ws://h:1/", id="ws-h-1"),
        pytest.param("ws://u:1@h:443/p", "ws://u:1@h:443/p", id="ws-u-1-h-443-p"),
        pytest.param("ws://[::1]:443/p", "ws://[::1]:443/p", id="ws-1-443-p"),
        pytest.param("ws://h/a/%2/b", "ws://h/a/%2/b", id="ws-h-a-2-b"),
        pytest.param("ws://h/a/...", "ws://h/a/...", id="ws-h-a"),
        pytest.param("ws://h/%2e", "ws://h/", id="ws-h-2e"),
        pytest.param("ws://h/a/%2e.", "ws://h/", id="ws-h-a-2e"),
        pytest.param("ws://h/a/.%2e", "ws://h/", id="ws-h-a-2e"),
        pytest.param("ws://h/a/./b/../c", "ws://h/a/c", id="ws-h-a-b-c"),
        pytest.param("wss://h:443/p", "wss://h/p", id="wss-h-443-p"),
        pytest.param("wss://h:80/p", "wss://h:80/p", id="wss-h-80-p"),
        pytest.param("wss://h:21/p", "wss://h:21/p", id="wss-h-21-p"),
        pytest.param("wss://h:/p", "wss://h/p", id="wss-h-p"),
        pytest.param("wss://h:0443/p", "wss://h/p", id="wss-h-0443-p"),
        pytest.param("wss://h:0080/p", "wss://h:80/p", id="wss-h-0080-p"),
        pytest.param("wss://h:8080/p", "wss://h:8080/p", id="wss-h-8080-p"),
        pytest.param("wss://h:x/p", "wss://h:x/p", id="wss-h-x-p"),
        pytest.param("wss://h:99999/p", "wss://h:99999/p", id="wss-h-99999-p"),
        pytest.param("wss://h:443", "wss://h/", id="wss-h-443"),
        pytest.param("wss://h:443?q#f", "wss://h/?q#f", id="wss-h-443-q-f"),
        pytest.param("wss://h1/p", "wss://h1/p", id="wss-h1-p"),
        pytest.param("wss://h:1", "wss://h:1/", id="wss-h-1"),
        pytest.param("wss://u:1@h:443/p", "wss://u:1@h/p", id="wss-u-1-h-443-p"),
        pytest.param("wss://[::1]:443/p", "wss://[::1]/p", id="wss-1-443-p"),
        pytest.param("wss://h/a/%2/b", "wss://h/a/%2/b", id="wss-h-a-2-b"),
        pytest.param("wss://h/a/...", "wss://h/a/...", id="wss-h-a"),
        pytest.param("wss://h/%2e", "wss://h/", id="wss-h-2e"),
        pytest.param("wss://h/a/%2e.", "wss://h/", id="wss-h-a-2e"),
        pytest.param("wss://h/a/.%2e", "wss://h/", id="wss-h-a-2e"),
        pytest.param("wss://h/a/./b/../c", "wss://h/a/c", id="wss-h-a-b-c"),
        pytest.param("ftp://h:443/p", "ftp://h:443/p", id="ftp-h-443-p"),
        pytest.param("ftp://h:80/p", "ftp://h:80/p", id="ftp-h-80-p"),
        pytest.param("ftp://h:21/p", "ftp://h/p", id="ftp-h-21-p"),
        pytest.param("ftp://h:/p", "ftp://h/p", id="ftp-h-p"),
        pytest.param("ftp://h:0443/p", "ftp://h:443/p", id="ftp-h-0443-p"),
        pytest.param("ftp://h:0080/p", "ftp://h:80/p", id="ftp-h-0080-p"),
        pytest.param("ftp://h:8080/p", "ftp://h:8080/p", id="ftp-h-8080-p"),
        pytest.param("ftp://h:x/p", "ftp://h:x/p", id="ftp-h-x-p"),
        pytest.param("ftp://h:99999/p", "ftp://h:99999/p", id="ftp-h-99999-p"),
        pytest.param("ftp://h:443", "ftp://h:443/", id="ftp-h-443"),
        pytest.param("ftp://h:443?q#f", "ftp://h:443/?q#f", id="ftp-h-443-q-f"),
        pytest.param("ftp://h1/p", "ftp://h1/p", id="ftp-h1-p"),
        pytest.param("ftp://h:1", "ftp://h:1/", id="ftp-h-1"),
        pytest.param("ftp://u:1@h:443/p", "ftp://u:1@h:443/p", id="ftp-u-1-h-443-p"),
        pytest.param("ftp://[::1]:443/p", "ftp://[::1]:443/p", id="ftp-1-443-p"),
        pytest.param("ftp://h/a/%2/b", "ftp://h/a/%2/b", id="ftp-h-a-2-b"),
        pytest.param("ftp://h/a/...", "ftp://h/a/...", id="ftp-h-a"),
        pytest.param("ftp://h/%2e", "ftp://h/", id="ftp-h-2e"),
        pytest.param("ftp://h/a/%2e.", "ftp://h/", id="ftp-h-a-2e"),
        pytest.param("ftp://h/a/.%2e", "ftp://h/", id="ftp-h-a-2e"),
        pytest.param("ftp://h/a/./b/../c", "ftp://h/a/c", id="ftp-h-a-b-c"),
        pytest.param("https://a:1b1/p", "https://a:1b1/p", id="https-a-1b1-p"),
        pytest.param("https://h:65535/p", "https://h:65535/p", id="https-h-65535-p"),
        pytest.param("https://h:65536/p", "https://h:65536/p", id="https-h-65536-p"),
        pytest.param("https://h:0/p", "https://h:0/p", id="https-h-0-p"),
        pytest.param("https://h:00443/p", "https://h/p", id="https-h-00443-p"),
        pytest.param("https://h:21x/p", "https://h:21x/p", id="https-h-21x-p"),
        pytest.param("https://h:/", "https://h/", id="https-h"),
        pytest.param("https://h/a/.x", "https://h/a/.x", id="https-h-a-x"),
        pytest.param("https://h/a/%2ex", "https://h/a/%2ex", id="https-h-a-2ex"),
        pytest.param("https://h/a/%2", "https://h/a/%2", id="https-h-a-2"),
        pytest.param("https://h/a/%", "https://h/a/%", id="https-h-a"),
        pytest.param("https://h/a/%2e%2", "https://h/a/%2e%2", id="https-h-a-2e-2"),
        pytest.param("https://h/a/%3e", "https://h/a/%3e", id="https-h-a-3e"),
        pytest.param("https://h/a/%2f", "https://h/a/%2f", id="https-h-a-2f"),
        pytest.param("https://h/a/%zz", "https://h/a/%zz", id="https-h-a-zz"),
        pytest.param("https://h/a/%2e%2e%2e", "https://h/a/%2e%2e%2e", id="https-h-a-2e-2e-2e"),
        pytest.param("https://h/a/%2e%2e%2e/b", "https://h/a/%2e%2e%2e/b", id="https-h-a-2e-2e-2e-b"),
        pytest.param("https://h/a/..%", "https://h/a/..%", id="https-h-a"),
        pytest.param("https://h/a/%%2e", "https://h/a/%%2e", id="https-h-a-2e"),
        pytest.param("https://h/a/.%2e/b", "https://h/b", id="https-h-a-2e-b"),
        pytest.param("https://h/a/%2E/b", "https://h/a/b", id="https-h-a-2e-b"),
        pytest.param("https://h\\p", "https://h/p", id="https-h-p"),
        pytest.param("https://h\\p/q", "https://h/p/q", id="https-h-p-q"),
        pytest.param("https://h/a\\b", "https://h/a/b", id="https-h-a-b"),
        pytest.param("HTTPS://HOST1/p", "https://host1/p", id="https-host1-p"),
        pytest.param("HTTPS://HOST:00/p", "https://host:0/p", id="https-host-00-p"),
        pytest.param("HTTPS://HOST:0/p", "https://host:0/p", id="https-host-0-p"),
        pytest.param("HTTPS://HOST:443/a/../b", "https://host/b", id="https-host-443-a-b"),
        pytest.param("HTTPS://u:P@HOST/p", "https://u:P@host/p", id="https-u-p-host-p"),
        pytest.param("https://h:1/x", "https://h:1/x", id="https-h-1-x"),
        pytest.param("https://h/.xyz/q", "https://h/.xyz/q", id="https-h-xyz-q"),
        pytest.param("https://h/a/..?q", "https://h/?q", id="https-h-a-q"),
        pytest.param("https://h/.x?q", "https://h/.x?q", id="https-h-x-q"),
        pytest.param("https://h/a/%2e%2e%2e%2e", "https://h/a/%2e%2e%2e%2e", id="https-h-a-2e-2e-2e-2e"),
        pytest.param("https://h/a/..../x", "https://h/a/..../x", id="https-h-a-x"),
        pytest.param("https://h/.x#f", "https://h/.x#f", id="https-h-x-f"),
        pytest.param("https://h/..#f", "https://h/#f", id="https-h-f"),
        pytest.param("https://h#f", "https://h/#f", id="https-h-f"),
        pytest.param("https://h?q", "https://h/?q", id="https-h-q"),
        pytest.param("https://h/a/%2?q", "https://h/a/%2?q", id="https-h-a-2-q"),
        pytest.param("https://h/a/.%2e?q", "https://h/?q", id="https-h-a-2e-q"),
        pytest.param("http://h:65536/p", "http://h:65536/p", id="http-h-65536-p"),
        pytest.param("https://h/p?q#f", "https://h/p?q#f", id="https-h-p-q-f"),
        pytest.param("https://h?#f", "https://h/?#f", id="https-h-f"),
        pytest.param("file://h:21/p", "file://h:21/p", id="file-h-21-p"),
        pytest.param("https://h/p?Į", "https://h/p?%C4%AE", id="https-h-p"),
    ],
)
def test_resolve_links_special_urls_match_whatwg(href: str, expected: str) -> None:
    assert _resolved_href(href, _HTTPS) == expected


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        pytest.param("https://h/a b?q x#f g", "https://h/a%20b?q%20x#f%20g", id="space-in-path-query-fragment"),
        pytest.param("https://a b:c d@h/p", "https://a%20b:c%20d@h/p", id="space-in-userinfo"),
        pytest.param("https://h/a\u2028b", "https://h/a%E2%80%A8b", id="line-separator-in-path"),
        pytest.param("https://h/a\u00a0b", "https://h/a%C2%A0b", id="nbsp-in-path"),
        pytest.param("https://h/a\tb", "https://h/ab", id="tab-removed"),
        pytest.param("foo://h/a b", "foo://h/a%20b", id="space-in-non-special-path"),
        pytest.param("https://h:99999/a b", "https://h:99999/a b", id="unparsable-kept-as-written"),
        pytest.param("https://a b/", "https://a b/", id="space-in-host-kept-as-written"),
        pytest.param("wss:a b", "wss:a b", id="space-in-slashless-host-kept-as-written"),
        pytest.param("https:///a b", "https:///a b", id="space-in-host-after-slashes-kept-as-written"),
        pytest.param("a b", "https://example.com/dir/a%20b", id="relative-path"),
        pytest.param("/a b", "https://example.com/a%20b", id="root-relative-path"),
        pytest.param("../a b", "https://example.com/a%20b", id="parent-relative-path"),
        pytest.param("//h/a b", "https://h/a%20b", id="scheme-relative"),
        pytest.param("https:h/a b", "https://example.com/dir/h/a%20b", id="same-scheme-without-slashes"),
        pytest.param("a/ü", "https://example.com/dir/a/%C3%BC", id="relative-non-ascii"),
        pytest.param("#a b", "https://example.com/dir/#a%20b", id="fragment-only"),
        pytest.param("?q x", "https://example.com/dir/?q%20x", id="query-only"),
        pytest.param("mailto:ü", "mailto:%C3%BC", id="foreign-scheme-non-ascii"),
        pytest.param("foo:į", "foo:%C4%AF", id="foreign-scheme-code-point-with-slash-low-byte"),
        pytest.param("foo:/a b", "foo:/a%20b", id="foreign-scheme-path"),
        pytest.param("file:a b", "file:///a%20b", id="file-no-slashes"),
        pytest.param("file:///a b", "file:///a%20b", id="file-empty-host"),
    ],
)
def test_resolve_links_encodes_whitespace_in_a_link(href: str, expected: str) -> None:
    assert _resolved_href(href, _HTTPS) == expected


@pytest.mark.parametrize(
    ("href", "base", "expected"),
    [
        pytest.param("#a b", "mailto:x", "mailto:x#a%20b", id="fragment-on-opaque-base"),
        pytest.param("ht\ttps://h/", "mailto:x", "https://h/", id="tab-in-scheme-on-opaque-base"),
        pytest.param("https://h/a\tb", "file:///home/u/x.html", "https://h/ab", id="tab-on-file-base"),
        pytest.param("http://h/a/..\n/b", "file:///home/u/x.html", "http://h/b", id="newline-on-file-base"),
        pytest.param("C\t:/x", "file:///C:/dir/f", "c:/x", id="tab-in-scheme-on-file-base"),
        pytest.param(
            "javascript:alert(1);\nalert(2)", _HTTPS, "javascript:alert(1);alert(2)", id="newline-in-javascript"
        ),
        pytest.param(
            "data:image/png;base64,iVBO\nRw0K", _HTTPS, "data:image/png;base64,iVBORw0K", id="newline-in-data"
        ),
        pytest.param("ja\tvascript:x", _HTTPS, "javascript:x", id="tab-in-foreign-scheme"),
        pytest.param("foo:a?c\td#e\tf", _HTTPS, "foo:a?cd#ef", id="tab-in-foreign-query-and-fragment"),
    ],
)
def test_resolve_links_removes_tab_and_newline_from_a_link(href: str, base: str, expected: str) -> None:
    assert _resolved_href(href, base) == expected


# WHATWG reads a numeric host of a special scheme as an IPv4 address (decimal, octal or hex, one to four parts) and
# writes it dotted (https://url.spec.whatwg.org/#concept-ipv4-parser).
@pytest.mark.parametrize(
    ("href", "expected"),
    [
        pytest.param("https://123/p", "https://0.0.0.123/p", id="decimal-integer"),
        pytest.param("https://0x7f.1/p", "https://127.0.0.1/p", id="hex-then-decimal"),
        pytest.param("https://127.1/p", "https://127.0.0.1/p", id="two-parts"),
        pytest.param("https://0177.0.0.1/p", "https://127.0.0.1/p", id="octal-part"),
        pytest.param("https://0XFF.1/p", "https://255.0.0.1/p", id="upper-hex-prefix"),
        pytest.param("https://1.2.3.4./p", "https://1.2.3.4/p", id="trailing-dot"),
        pytest.param("https://0x/p", "https://0.0.0.0/p", id="bare-hex-prefix"),
        pytest.param("https://1.0x/p", "https://1.0.0.0/p", id="empty-hex-last-part"),
        pytest.param("https://1.0X/p", "https://1.0.0.0/p", id="upper-empty-hex-last-part"),
        pytest.param("https://0x7f.0x/p", "https://127.0.0.0/p", id="empty-hex-part"),
        pytest.param("https://4294967295/p", "https://255.255.255.255/p", id="largest-integer"),
        pytest.param("https://123.456/p", "https://123.0.1.200/p", id="last-part-fills-two-octets"),
        pytest.param("https://127.0.0.1/p", "https://127.0.0.1/p", id="dotted-quad-unchanged"),
        pytest.param("https://0x7f.0.0.1:443/p", "https://127.0.0.1/p", id="default-port-dropped"),
        pytest.param("https://123:8443/p", "https://0.0.0.123:8443/p", id="port-kept"),
        pytest.param("https://u:P@123/p", "https://u:P@0.0.0.123/p", id="userinfo-kept"),
        pytest.param("https://%31%32%33/p", "https://0.0.0.123/p", id="percent-encoded-digits"),
        pytest.param("https://%30x7f/p", "https://0.0.0.127/p", id="percent-encoded-hex-prefix"),
        pytest.param("https://\uff11\uff12\uff13.4/p", "https://123.0.0.4/p", id="fullwidth-digits"),
        pytest.param(
            "https://\uff10\uff38\uff43\uff10\uff0e\uff10\uff12\uff15\uff10\uff0e\uff10\uff11/p",
            "https://192.168.0.1/p",
            id="fullwidth-hex",
        ),
        pytest.param("http://123:80/p", "http://0.0.0.123/p", id="http-default-port-dropped"),
        pytest.param("ftp://123:21/p", "ftp://0.0.0.123/p", id="ftp-default-port-dropped"),
        pytest.param("//0x7f.1/p", "https://127.0.0.1/p", id="scheme-relative"),
    ],
)
def test_resolve_links_writes_numeric_hosts_as_ipv4(href: str, expected: str) -> None:
    assert _resolved_href(href, _HTTPS) == expected


# A host the IPv4 parser rejects keeps its authority as written, port included; a name that only looks numeric
# is a name.
@pytest.mark.parametrize(
    "href",
    [
        pytest.param("https://1.2.3.4.5/p", id="five-parts"),
        pytest.param("https://1.2.3.4.5:443/p", id="five-parts-keeps-port"),
        pytest.param("https://256.1.1.1/p", id="octet-out-of-range"),
        pytest.param("https://4294967296/p", id="integer-overflow"),
        pytest.param("https://0x100000000/p", id="hex-overflow"),
        pytest.param("https://1.2.3.4a.5/p", id="non-number-part"),
        pytest.param("https://1..2/p", id="empty-part"),
        pytest.param("https://a%20b.123:443/p", id="forbidden-code-point-keeps-port"),
        pytest.param("https://x\u2028.com:443/p", id="failed-mapping-keeps-port"),
        pytest.param("https://a%/p", id="trailing-percent"),
        pytest.param("https://a\u2028/p", id="non-ascii-last-code-point"),
        pytest.param("https://1.0x7g/p", id="last-label-not-hex"),
        pytest.param("https://1.0b/p", id="last-label-zero-without-x"),
        pytest.param("https://:80x/p", id="empty-host-keeps-authority"),
        pytest.param("https://:443/p", id="empty-host-default-port-kept"),
        pytest.param("https://1.2.3.4../p", id="empty-last-label-is-a-name"),
    ],
)
def test_resolve_links_keeps_a_host_the_ipv4_parser_rejects(href: str) -> None:
    assert _resolved_href(href, _HTTPS) == href


@pytest.mark.parametrize(
    ("base", "expected"),
    [
        pytest.param("http://0x/p", "http://0.0.0.0/x", id="bare-hex-prefix-host"),
        pytest.param("https://1.0x/p", "https://1.0.0.0/x", id="empty-hex-last-part-host"),
    ],
)
def test_resolve_links_writes_a_numeric_base_host_as_ipv4(base: str, expected: str) -> None:
    assert _resolved_href("x", base) == expected


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        pytest.param("https://%41.com:443/p", "https://a.com/p", id="escaped-name-decoded"),
        pytest.param("https://A%42.com/p", "https://ab.com/p", id="escaped-and-upper-case"),
        pytest.param("https://%61%62.com/p", "https://ab.com/p", id="escaped-letters"),
        pytest.param("https://x.%41/p", "https://x.a/p", id="escaped-last-label"),
        pytest.param("https://%41.%42.%43/p", "https://a.b.c/p", id="escaped-every-label"),
        pytest.param("https://a%2E/p", "https://a./p", id="escaped-dot-kept-as-trailing-dot"),
        pytest.param("https://%2e%2e.com/p", "https://...com/p", id="escaped-dot-in-label"),
        pytest.param("https://u%41@%41.com:443/p", "https://u%41@a.com/p", id="userinfo-kept"),
        pytest.param("https://%41%42/p", "https://ab/p", id="single-label"),
        pytest.param("https://%41.com/a/../b", "https://a.com/b", id="escaped-host-with-dot-segment"),
    ],
)
def test_resolve_links_decodes_a_percent_encoded_registered_name(href: str, expected: str) -> None:
    assert _resolved_href(href, _HTTPS) == expected


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        pytest.param("https://\u4f8b.jp/p", "https://xn--fsq.jp/p", id="cjk-label"),
        pytest.param("https://EXAMPLE.\u4f8b/p", "https://example.xn--fsq/p", id="upper-case-with-cjk-label"),
        pytest.param("https://M\u00dcNCHEN.de/p", "https://xn--mnchen-3ya.de/p", id="upper-case-umlaut"),
        pytest.param("https://\u212a.example/p", "https://k.example/p", id="kelvin-sign-folds"),
        pytest.param("https://\u00e9t\u00e9.fr/p", "https://xn--t-9fab.fr/p", id="accented-label"),
        pytest.param("https://a\u00adb.com/p", "https://ab.com/p", id="soft-hyphen-removed"),
        pytest.param("https://\uff21\uff22.com/p", "https://ab.com/p", id="fullwidth-letters"),
        pytest.param("https://\u4f8b.\u4f8b/p", "https://xn--fsq.xn--fsq/p", id="cjk-every-label"),
        pytest.param("https://\u0130.com/p", "https://xn--i-9bb.com/p", id="dotted-capital-i"),
        pytest.param("https://\u00df.de/p", "https://xn--zca.de/p", id="sharp-s"),
        pytest.param("https://\u4f8b.jp:443/p", "https://xn--fsq.jp/p", id="default-port-dropped"),
        pytest.param("https://\u4f8b.jp:8080/p", "https://xn--fsq.jp:8080/p", id="port-kept"),
        pytest.param("https://\u4f8b.jp/a/../b", "https://xn--fsq.jp/b", id="dot-segment"),
        pytest.param("https://\u4f8b.jp:/p", "https://xn--fsq.jp/p", id="empty-port"),
        pytest.param("//\u4f8b.jp/p", "https://xn--fsq.jp/p", id="scheme-relative"),
        pytest.param("https://\u4f8b.xn--fiqs8s/p", "https://xn--fsq.xn--fiqs8s/p", id="existing-ace-label-kept"),
        pytest.param("https://%E4%BE%8B.jp/p", "https://xn--fsq.jp/p", id="percent-encoded-cjk"),
        pytest.param("https://%c3%a9.com/p", "https://xn--9ca.com/p", id="percent-encoded-accent"),
    ],
)
def test_resolve_links_punycodes_an_internationalized_host(href: str, expected: str) -> None:
    assert _resolved_href(href, _HTTPS) == expected


@pytest.mark.parametrize("href", ["https://%20.com/p", "https://%zz.com/p", "https://a%2Fb.com/p"])
def test_resolve_links_keeps_an_escaped_name_with_a_forbidden_code_point(href: str) -> None:
    assert _resolved_href(href, _HTTPS) == href


# WHATWG removes the dot segments of a path under every scheme, not only the special ones
# (https://url.spec.whatwg.org/#path-state), and keeps an opaque path (no slash after the scheme) as written. A path
# that starts with two slashes keeps its root segment, so "//" is not lost, and a URL without a host gets "/." in front.
@pytest.mark.parametrize(
    ("base", "href", "expected"),
    [
        pytest.param("foo://example.com/a/b", "/x/../y", "foo://example.com/y", id="rooted-dot-dot"),
        pytest.param("foo://example.com/a/b", "/x/./y", "foo://example.com/x/y", id="rooted-dot"),
        pytest.param("foo://example.com/a/b", "/x/%2e%2E/y", "foo://example.com/y", id="encoded-dot-dot"),
        pytest.param("foo://example.com/a/b", "/x/%2E/y", "foo://example.com/x/y", id="encoded-dot"),
        pytest.param("foo://example.com/a/b", "/x/.%2e/z", "foo://example.com/z", id="mixed-encoded-dot-dot"),
        pytest.param("foo://example.com/a/b", "x/../y", "foo://example.com/a/y", id="relative-dot-dot"),
        pytest.param("foo://example.com/a/b", "../y", "foo://example.com/y", id="relative-climbs-to-root"),
        pytest.param("foo://example.com/a/b", "./y", "foo://example.com/a/y", id="relative-dot"),
        pytest.param("foo://example.com/a/b", "/x/..", "foo://example.com/", id="dot-dot-to-root"),
        pytest.param("foo://example.com/a/b", "/x/.", "foo://example.com/x/", id="dot-to-directory"),
        pytest.param("foo://example.com/a/b", "/x/y/..?q", "foo://example.com/x/?q", id="dot-dot-before-query"),
        pytest.param("foo://example.com/a/b", "?q", "foo://example.com/a/b?q", id="query-keeps-path"),
        pytest.param("foo://example.com/a/b", "#f", "foo://example.com/a/b#f", id="fragment-keeps-path"),
        pytest.param("foo://example.com/a/b", "\\x/../y", "foo://example.com/a/y", id="backslash-is-not-a-separator"),
        pytest.param("foo://example.com/a/b", "/x/..x/y", "foo://example.com/x/..x/y", id="dot-prefixed-name"),
        pytest.param("foo://example.com/a/b", "/x/a%2e/b", "foo://example.com/x/a%2e/b", id="encoded-dot-in-name"),
        pytest.param("foo://example.com/a/b", "//host/x/../y", "foo://host/y", id="network-path"),
        pytest.param("foo://example.com/a/b", "/a/..//b", "foo://example.com//b", id="root-kept-before-slash"),
        pytest.param("foo://example.com/a/b", "/..//path", "foo://example.com//path", id="root-kept-at-start"),
        pytest.param("foo:/a/b", "/x/../y", "foo:/y", id="no-host-rooted"),
        pytest.param("foo:/a/b", "x/../y", "foo:/a/y", id="no-host-relative"),
        pytest.param("foo:/a/b", "//host/x/../y", "foo://host/y", id="no-host-network-path"),
        pytest.param("foo:a/b", "/x/../y", "/x/../y", id="opaque-base-kept"),
        pytest.param("sftp://h/a/b/c", "/x/%2e%2E/y", "sftp://h/y", id="sftp-encoded-dot-dot"),
        pytest.param("sftp://h/a/b/c", "../y", "sftp://h/a/y", id="sftp-relative-dot-dot"),
        pytest.param("sftp://h/a/b/c", "x/../y", "sftp://h/a/b/y", id="sftp-relative-dot-dot-in-directory"),
        pytest.param(_HTTPS, "sftp://h/x/../y", "sftp://h/y", id="other-scheme-absolute"),
        pytest.param(_HTTPS, "sftp://h/x/%2E/y", "sftp://h/x/y", id="other-scheme-absolute-encoded-dot"),
        pytest.param(_HTTPS, "sftp:/x/../y", "sftp:/y", id="other-scheme-no-host"),
        pytest.param(_HTTPS, "/..//path", "https://example.com//path", id="special-root-kept"),
        pytest.param(_HTTPS, "//x/..//p", "https://x//p", id="special-network-path-root-kept"),
        pytest.param("foo://example.com/a/b", "foo:/x/../y", "foo:/y", id="same-scheme-no-host"),
        pytest.param("foo://example.com/a/b", "foo://h/x/../y", "foo://h/y", id="same-scheme-absolute"),
        pytest.param("foo://example.com/a/b", "foo://UPPER:22/x/../y", "foo://UPPER:22/y", id="host-and-port-kept"),
        pytest.param("foo://example.com/a/b", "foo:x/../y", "foo:x/../y", id="same-scheme-opaque-kept"),
        pytest.param("foo://example.com/a/b", "foo://h/x/./y?q#f", "foo://h/x/y?q#f", id="query-and-fragment-kept"),
        pytest.param("foo://example.com/a/b", "foo://h", "foo://h", id="empty-path-unchanged"),
        pytest.param("foo://example.com/a/b", "foo://h/a/../../..", "foo://h/", id="climbing-past-root"),
        pytest.param("foo://example.com/a/b", "sftp://h/x/../y", "sftp://h/y", id="other-non-special-absolute"),
        pytest.param(
            "foo://example.com/a/b", "https://h/x/../y", "https://h/y", id="special-absolute-under-non-special"
        ),
        pytest.param("foo://example.com/a/b", "sftp://h/", "sftp://h/", id="non-special-trailing-slash"),
        pytest.param("non-spec:/a/b", "//x/..//p", "non-spec://x//p", id="non-special-root-kept-with-host"),
        pytest.param(
            "foo://example.com/a/b",
            "/a/../b/%2e%2e/c/./d",
            "foo://example.com/c/d",
            id="several-dot-segments",
        ),
    ],
)
def test_resolve_links_removes_dot_segments_under_any_scheme(base: str, href: str, expected: str) -> None:
    assert _resolved_href(href, base) == expected


# A fragment-only reference keeps an opaque base's path and query and takes the reference's fragment
# (https://url.spec.whatwg.org/#path-state); a reference with a path or query under an opaque base is rejected and kept.
@pytest.mark.parametrize(
    ("base", "href", "expected"),
    [
        pytest.param("foo:a/b", "#f", "foo:a/b#f", id="fragment"),
        pytest.param("foo:a/b#old", "#f", "foo:a/b#f", id="replaces-base-fragment"),
        pytest.param("foo:a/b?q#old", "#f", "foo:a/b?q#f", id="keeps-base-query"),
        pytest.param("foo:a/b?q", "#", "foo:a/b?q#", id="empty-fragment"),
        pytest.param("foo:a/b", "#a#b", "foo:a/b#a#b", id="second-hash-kept"),
        pytest.param("foo:a/b", "#f?q", "foo:a/b#f?q", id="question-mark-in-fragment"),
        pytest.param("mailto:x@x.com", "#x", "mailto:x@x.com#x", id="mailto-base"),
        pytest.param("data:,", "#", "data:,#", id="data-base"),
        pytest.param("about:blank", "#x", "about:blank#x", id="about-base"),
        pytest.param("foo:/a/b", "#f", "foo:/a/b#f", id="hierarchical-base-unchanged"),
        pytest.param("foo:a/b", "?q#f", "?q#f", id="query-rejected-kept"),
        pytest.param("foo:a/b", "x", "x", id="path-rejected-kept"),
    ],
)
def test_resolve_links_resolves_a_fragment_against_an_opaque_base(base: str, href: str, expected: str) -> None:
    assert _resolved_href(href, base) == expected


# A base with an empty path is serialized with its root "/" before a query or fragment, or a relative path, is joined
# onto it (https://url.spec.whatwg.org/#url-path-serializer).
@pytest.mark.parametrize(
    ("base", "href", "expected"),
    [
        pytest.param("https://h", "#f", "https://h/#f", id="fragment"),
        pytest.param("https://h", "?q", "https://h/?q", id="query"),
        pytest.param("https://h", "x", "https://h/x", id="relative-path"),
        pytest.param("https://u@h?z", "?q#f", "https://u@h/?q#f", id="userinfo-and-base-query"),
        pytest.param("https://h", "//k/j", "https://k/j", id="network-path"),
        pytest.param("https://h?q", "x", "https://h/x", id="query-right-after-host"),
        pytest.param("https://h#f", "x", "https://h/x", id="fragment-right-after-host"),
    ],
)
def test_resolve_links_roots_a_base_without_a_path(base: str, href: str, expected: str) -> None:
    assert _resolved_href(href, base) == expected


# A userinfo is percent-encoded with the userinfo set, the first ':' separates the username from the password, and an
# escape that is already there is kept (https://url.spec.whatwg.org/#userinfo-percent-encode-set).
@pytest.mark.parametrize(
    ("href", "expected"),
    [
        pytest.param("https://u\u00fc@h/p", "https://u%C3%BC@h/p", id="non-ascii"),
        pytest.param("https://a b@h/p", "https://a%20b@h/p", id="space"),
        pytest.param('https://a"b@h/p', "https://a%22b@h/p", id="quote"),
        pytest.param("https://a<b>@h/p", "https://a%3Cb%3E@h/p", id="angle-brackets"),
        pytest.param("https://a:b:c@h/p", "https://a:b%3Ac@h/p", id="second-colon-escaped"),
        pytest.param("https://a@b@c@h/p", "https://a%40b%40c@h/p", id="earlier-at-escaped"),
        pytest.param("https://a%20b@h/p", "https://a%20b@h/p", id="existing-escape-kept"),
        pytest.param("https://a%zz@h/p", "https://a%zz@h/p", id="invalid-escape-kept"),
        pytest.param("https://u:p q@h:443/p", "https://u:p%20q@h/p", id="password-and-default-port"),
        pytest.param("https://a;b@h/p", "https://a%3Bb@h/p", id="semicolon"),
        pytest.param("//a b@h/p", "https://a%20b@h/p", id="network-path"),
        pytest.param("foo://a b@h/p", "foo://a%20b@h/p", id="non-special-scheme"),
        pytest.param("https://a b@h/p?q#f", "https://a%20b@h/p?q#f", id="query-and-fragment-kept"),
        pytest.param("https://\u4f8b@h/p", "https://%E4%BE%8B@h/p", id="three-byte-code-point"),
        pytest.param("https://\U0001f600@h/p", "https://%F0%9F%98%80@h/p", id="four-byte-code-point"),
        pytest.param("foo://user@h/p", "foo://user@h/p", id="non-special-plain-kept"),
        pytest.param("foo://a@b@h/p", "foo://a%40b@h/p", id="non-special-earlier-at-escaped"),
        pytest.param("foo://a:b:c@h/p", "foo://a:b%3Ac@h/p", id="non-special-second-colon-escaped"),
        pytest.param("foo://u@h:80/p", "foo://u@h:80/p", id="non-special-port-kept"),
        pytest.param("foo:x", "foo:x", id="non-special-opaque-kept"),
        pytest.param("foo:/a/b", "foo:/a/b", id="non-special-path-kept"),
        pytest.param("foo://u@h?q", "foo://u@h?q", id="non-special-query-ends-authority"),
        pytest.param("foo://u@h#f", "foo://u@h#f", id="non-special-fragment-ends-authority"),
        pytest.param("foo://h:80/p", "foo://h:80/p", id="non-special-port-without-userinfo"),
        pytest.param("foo://u:p@h/p", "foo://u:p@h/p", id="non-special-username-and-password"),
        pytest.param("foo://u@Host:80/p", "foo://u@Host:80/p", id="non-special-host-case-kept"),
    ],
)
def test_resolve_links_percent_encodes_the_userinfo(href: str, expected: str) -> None:
    assert _resolved_href(href, _HTTPS) == expected


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
    href: Final[str] = f"https://{'s.' * depth}{host}/x"
    assert extract_links(f'<a href="{href}">x</a>', f"https://{base}/", external_only=True) == set()


# An unclosed formatting element is reopened in every block that follows it, and each of those clones borrows the
# attribute values of the one start tag. The walk hands the clones one result instead of a copy of it apiece.
_REOPENED: Final[str] = '<p><a href="a.html" ping="p1 p2" title="t">x<p>y<p>z'


def test_links_of_a_reopened_element_are_enumerated_on_each_clone() -> None:
    links = parse_fragment(_REOPENED).links()
    assert [(link.attribute, link.url) for link in links] == [("href", "a.html"), ("ping", "p1"), ("ping", "p2")] * 3
    assert [link.element.text for link in links] == ["x"] * 3 + ["y"] * 3 + ["z"] * 3


def test_a_reopened_element_is_resolved_on_each_clone() -> None:
    anchor: Final[str] = (
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
    html: Final[str] = f"<p>{start_tag}x<p>y<p>z"
    seen: list[str] = []
    root = parse_fragment(html)
    before: Final[str] = root.inner_html
    root.resolve_links(_RESOLVE_BASE)
    root.rewrite_links(seen.append)
    assert root.inner_html == before
    assert seen == replaced


def test_many_reopened_elements_grow_the_shared_value_table() -> None:
    # the table starts with 16 slots and grows past half load at its ninth distinct value; the formatting list reopens
    # at most 16 elements, so twelve distinct values fit in one run
    elements: Final[str] = "".join(f'<b cite="c{index}">' for index in range(12))
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
    anchor: Final[str] = '<a href="/moved/a.html" ping="/moved/p1 /moved/p2" title="t">'
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
    anchors: Final[list[Element]] = root.select("a")
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
    href: Final[str] = "x" * 25_000
    html: Final[str] = f'<p><a href="{href}">x{"<p>y" * 1_500}'
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
    href: Final[str] = f"https://{'s.' * depth}{host}/x"
    assert extract_links(f'<a href="{href}">x</a>', f"https://{base}/", external_only=True) == {href}


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        pytest.param("ftp://elsewhere\ttest/x", "ftp://elsewheretest/x", id="tab-removed"),
        pytest.param("ftp://elsewhere\rtest/x", "ftp://elsewheretest/x", id="carriage-return-removed"),
        pytest.param("ftp://elsewhere\ntest/x", "ftp://elsewheretest/x", id="line-feed-removed"),
        pytest.param("ftp:elsewhere.test\\\rx", "ftp://elsewhere.test/x", id="carriage-return-in-path-separator"),
    ],
)
def test_resolve_links_removes_newlines_inside_foreign_links(href: str, expected: str) -> None:
    root = parse_fragment("<a>x</a>")
    link = root.find("a")
    assert isinstance(link, Element)
    link.attrs["href"] = href
    root.resolve_links(_HTTPS)
    (resolved,) = root.links()
    assert resolved.url == expected
