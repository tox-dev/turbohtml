"""extract_links: anchors from the parsed DOM, base resolution, filters, and variant deduplication."""

from __future__ import annotations

from html import escape
from typing import Final
from urllib.parse import urljoin

import pytest

from turbohtml import Element, Link, Text, parse, parse_fragment
from turbohtml._html import _registrable_domain
from turbohtml.extract import UrlCleaning, extract_links

_BASE = "https://test.com/dir/"


def test_relative_links_resolve_against_the_base() -> None:
    assert extract_links('<a href="page.html">x</a>', _BASE) == {"https://test.com/dir/page.html"}


def test_base_element_wins_over_the_fetch_url() -> None:
    html = '<base href="https://cdn.test/assets/"><a href="p">x</a>'
    assert extract_links(html, _BASE) == {"https://cdn.test/assets/p"}


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
    ("base", "href", "external"),
    [
        pytest.param("https://www.test.com/", "https://test.com/x", False, id="same-host"),
        pytest.param("https://www.test.com/", "https://www.test.com/x", False, id="www-twin"),
        pytest.param("https://www.test.com/", "https://blog.test.com/x", False, id="subdomain"),
        pytest.param("https://a.test.com/", "https://b.test.com/x", False, id="sibling-subdomains-share-etld1"),
        pytest.param("https://www.test.com/", "https://test.com.evil.example/x", True, id="host-suffix-attack"),
        pytest.param("https://www.test.com/", "https://other.net/x", True, id="other-host"),
        pytest.param("https://spam.example.co.uk/", "https://example.co.uk/x", False, id="multi-suffix-same-site"),
        pytest.param("https://a.co.uk/", "https://b.co.uk/x", True, id="multi-suffix-distinct-registrants"),
        pytest.param("https://foo.github.io/", "https://bar.github.io/x", True, id="private-suffix-github-io"),
        pytest.param("https://sub.tv.com/", "https://other.tv.com/x", False, id="tld-like-label-is-not-a-suffix"),
        pytest.param("https://www.ck/", "https://other.ck/x", True, id="wildcard-and-exception-suffix"),
        pytest.param("http://192.0.2.1/", "http://198.51.100.2/x", True, id="ip-hosts-compare-whole"),
    ],
)
def test_external_boundary_is_the_registrable_domain(base: str, href: str, *, external: bool) -> None:
    links = extract_links(f'<a href="{href}">x</a>', base, external_only=True)
    assert links == ({href} if external else set())


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        pytest.param("россия.рф", "россия.рф", id="cyrillic-unknown-suffix"),
        pytest.param("例え.jp", "例え.jp", id="cjk-label-ascii-suffix"),
        pytest.param("рф", "рф", id="bare-cyrillic-label"),
    ],
)
def test_registrable_domain_handles_a_raw_non_ascii_host(host: str, expected: str) -> None:
    # the internal entrypoint is reachable from Python without _ascii_host punycoding, so a non-ASCII first code
    # point must not index past the ASCII-sized suffix tables; it has no known suffix and stays its own domain
    assert _registrable_domain(host) == expected


def test_external_boundary_punycodes_a_unicode_base_host() -> None:
    html = '<a href="https://xn--mnchen-3ya.de/keep">x</a><a href="https://other.example/leave">y</a>'
    links = extract_links(html, "https://münchen.de/", external_only=True)
    assert links == {"https://other.example/leave"}


def test_external_boundary_with_a_hostless_base_keeps_every_absolute_link() -> None:
    html = '<a href="https://a.example/x">x</a>'
    assert extract_links(html, "mailto:me@example.org", external_only=True) == {"https://a.example/x"}


def test_external_only_requires_a_base() -> None:
    with pytest.raises(ValueError, match="external_only requires a base_url"):
        extract_links('<a href="https://a.example/">x</a>', external_only=True)


@pytest.mark.parametrize(
    ("hreflang", "language", "kept"),
    [
        pytest.param("de-DE", "de", True, id="regioned-match"),
        pytest.param("de-DE", "en", False, id="regioned-mismatch"),
        pytest.param("DE", "de", True, id="case-insensitive"),
        pytest.param("x-default", "en", True, id="x-default-always-passes"),
        pytest.param("", "en", True, id="empty-hreflang-passes"),
    ],
)
def test_hreflang_gates_anchors_under_a_language_filter(hreflang: str, language: str, *, kept: bool) -> None:
    html = f'<a href="https://test.com/example" hreflang="{hreflang}">x</a>'
    links = extract_links(html, _BASE, UrlCleaning(language=language))
    assert links == ({"https://test.com/example"} if kept else set())


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


def _urls(html: str) -> list[str]:
    return [link.url for link in parse_fragment(html).links()]


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


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        pytest.param("5; url=next.html", ["next.html"], id="standard"),
        pytest.param('0;url="dq.html"', ["dq.html"], id="double-quoted-url"),
        pytest.param("5; url=", [], id="empty-url-after-equals"),
        pytest.param("url=only.html", ["only.html"], id="url-first"),
        pytest.param("5; URL = spaced.html", ["spaced.html"], id="spaced-around-equals"),
        pytest.param("5; url=a.html ignored", ["a.html"], id="unquoted-url-ends-at-whitespace"),
        pytest.param("5", [], id="no-url-keyword"),
        pytest.param("5; urlx=no.html", [], id="url-not-followed-by-equals"),
        pytest.param("5; curl=no.html", [], id="url-is-tail-of-identifier"),
        pytest.param("0;url", [], id="url-keyword-at-end"),
        pytest.param("0;url=''", [], id="empty-quoted-url"),
    ],
)
def test_meta_refresh_content_parsing(content: str, expected: list[str]) -> None:
    assert _urls(f"<meta http-equiv='refresh' content='{content}'>") == expected


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
    ],
)
def test_style_element_css_parsing(css: str, expected: list[str]) -> None:
    assert _urls(f"<style>{css}</style>") == expected


def test_whitespace_only_space_list_yields_nothing() -> None:
    assert _urls('<object archive="    "></object>') == []


def _enumerated_urls(html: str) -> list[tuple[str, str | None, str]]:
    return [(link.element.tag, link.attribute, link.url) for link in parse_fragment(html).links()]


def test_anchor_href_is_enumerated() -> None:
    assert _enumerated_urls('<a href="a/b.html">x</a>') == [("a", "href", "a/b.html")]


def test_many_elements_grow_the_snapshot() -> None:
    # The pure-C element snapshot starts at 16 slots; >16 elements exercise its regrow.
    html = "".join(f'<a href="p{index}">{index}</a>' for index in range(40))
    assert _enumerated_urls(html) == [("a", "href", f"p{index}") for index in range(40)]


def test_value_is_reported_with_surrounding_whitespace_trimmed() -> None:
    assert _enumerated_urls('<a href="  a/b.html  ">x</a>') == [("a", "href", "a/b.html")]


def test_carriage_return_from_char_ref_is_trimmed() -> None:
    # &#13; injects a real U+000D past the input preprocessor's CR->LF fold, and CR is
    # HTML ASCII whitespace, so "strip leading/trailing ASCII whitespace" must drop it.
    assert _enumerated_urls('<a href="&#13;a/b.html&#13;">x</a>') == [("a", "href", "a/b.html")]


def test_whitespace_only_url_attribute_is_skipped() -> None:
    assert _enumerated_urls('<a href="   ">x</a>') == []


def test_valueless_url_attribute_is_skipped() -> None:
    assert _enumerated_urls("<a href>x</a>") == []


def test_non_link_attribute_is_ignored() -> None:
    assert _enumerated_urls('<a class="c" href="u">x</a>') == [("a", "href", "u")]


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


def test_ping_is_a_whitespace_separated_list() -> None:
    assert _enumerated_urls('<a href="h" ping="  p1   p2 p3 ">x</a>') == [
        ("a", "href", "h"),
        ("a", "ping", "p1"),
        ("a", "ping", "p2"),
        ("a", "ping", "p3"),
    ]


def test_ping_only_counts_on_anchor_and_area() -> None:
    assert _enumerated_urls('<div ping="p1 p2">x</div>') == []
    assert ("area", "ping", "p1") in _enumerated_urls('<map><area ping="p1 p2"></map>')


def test_archive_is_a_whitespace_list_on_object_and_applet() -> None:
    assert _enumerated_urls('<object archive="j1.jar j2.jar"></object>') == [
        ("object", "archive", "j1.jar"),
        ("object", "archive", "j2.jar"),
    ]
    assert ("applet", "archive", "j1.jar") in _enumerated_urls('<applet archive="j1.jar"></applet>')


def test_archive_is_ignored_off_object_and_applet() -> None:
    assert _enumerated_urls('<div archive="j1.jar j2.jar"></div>') == []


@pytest.mark.parametrize("attr", [pytest.param("srcset", id="srcset"), pytest.param("imagesrcset", id="imagesrcset")])
def test_srcset_candidates_are_enumerated(attr: str) -> None:
    found = _enumerated_urls(f'<img {attr}="a.png 1x, b.png 2x , c.png">')
    assert [url for _, _, url in found] == ["a.png", "b.png", "c.png"]


def test_srcset_with_a_trailing_comma_and_varied_whitespace() -> None:
    # the trailing comma exercises the separator skip running to the end, and \t/\n/\f the whitespace set
    found = _enumerated_urls('<img srcset="a.png\t1x,\nb.png\x0c2x,">')
    assert [url for _, _, url in found] == ["a.png", "b.png"]


def test_srcset_candidate_ending_directly_at_a_comma() -> None:
    # no descriptor or space before the comma, so the URL run stops on the comma itself
    found = _enumerated_urls('<img srcset="a.png,b.png 2x">')
    assert [url for _, _, url in found] == ["a.png", "b.png"]


def test_placeholder_is_not_imagesrcset() -> None:
    assert _enumerated_urls('<input placeholder="javascript:not-a-url">') == []


def test_meta_refresh_url_is_enumerated() -> None:
    assert _enumerated_urls('<meta http-equiv="refresh" content="5; url=next.html">') == [
        ("meta", "content", "next.html")
    ]


def test_meta_refresh_url_is_case_insensitive_and_unquotes() -> None:
    assert _enumerated_urls('<meta http-equiv="REFRESH" content="0;URL=&#39;n.html&#39;">') == [
        ("meta", "content", "n.html")
    ]


def test_meta_refresh_without_url_keyword_has_no_link() -> None:
    assert _enumerated_urls('<meta http-equiv="refresh" content="5">') == []


def test_meta_without_refresh_is_not_enumerated() -> None:
    assert _enumerated_urls('<meta name="x" content="url=y">') == []


def test_meta_refresh_without_content_has_no_link() -> None:
    assert _enumerated_urls('<meta http-equiv="refresh">') == []


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


def test_css_url_double_quoted_in_single_quoted_attribute() -> None:
    assert _enumerated_urls("<p style='background:url(\"bg.png\")'></p>") == [("p", "style", "bg.png")]


def test_style_element_text_is_scanned_as_css() -> None:
    found = _enumerated_urls("<style>a{background:url(bg.png)} @import 'theme.css'; @media x {}</style>")
    assert [(link[0], link[1], link[2]) for link in found] == [
        ("style", None, "bg.png"),
        ("style", None, "theme.css"),
    ]


def test_empty_style_element_has_no_link() -> None:
    assert _enumerated_urls("<style></style>") == []


def test_style_with_a_non_text_child_skips_it() -> None:
    # a parsed <style> is always rawtext, but a programmatic tree can give it an element child to skip
    style = Element("style")
    style.append(Element("span"))
    style.append(Text("a{background:url(bg.png)}"))
    assert [link.url for link in style.links()] == ["bg.png"]


def test_css_import_double_quoted() -> None:
    assert _enumerated_urls('<style>@import "a.css";</style>') == [("style", None, "a.css")]


def test_css_import_without_string_defers_to_url_form() -> None:
    assert _enumerated_urls("<style>@import url(a.css);</style>") == [("style", None, "a.css")]


def test_many_links_in_one_value_grow_the_span_buffer() -> None:
    candidates = ", ".join(f"img{index}.png {index}x" for index in range(1, 13))
    found = _enumerated_urls(f'<img srcset="{candidates}">')
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


_RESOLVE_BASE = "https://example.com/dir/page.html"
_HTTPS = "https://example.com/dir/"


def _resolved(html: str, base: str = _RESOLVE_BASE) -> str:
    root = parse_fragment(html)
    root.resolve_links(base)
    return root.inner_html


def test_relative_href_becomes_absolute() -> None:
    assert _resolved('<a href="a/b.html">x</a>') == '<a href="https://example.com/dir/a/b.html">x</a>'


def test_root_relative_href_resolves_against_origin() -> None:
    assert _resolved('<a href="/top.html">x</a>') == '<a href="https://example.com/top.html">x</a>'


def test_absolute_href_is_left_unchanged() -> None:
    assert _resolved('<a href="https://other.test/x">y</a>') == '<a href="https://other.test/x">y</a>'


def test_fragment_only_href_resolves_against_the_page() -> None:
    assert _resolved('<a href="#sec">y</a>') == '<a href="https://example.com/dir/page.html#sec">y</a>'


def test_srcset_candidates_are_each_resolved() -> None:
    out = _resolved('<img srcset="a.png 1x, b.png 2x">')
    assert out == '<img srcset="https://example.com/dir/a.png 1x, https://example.com/dir/b.png 2x">'


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


def test_meta_refresh_url_is_resolved() -> None:
    out = _resolved('<meta http-equiv="refresh" content="5; url=next.html">')
    assert out == '<meta http-equiv="refresh" content="5; url=https://example.com/dir/next.html">'


def test_resolve_on_a_whole_document_returns_none() -> None:
    doc = parse('<html><body><a href="a.html">x</a></body></html>')
    assert doc.resolve_links(_RESOLVE_BASE) is None
    (link,) = doc.links()
    assert link.url == "https://example.com/dir/a.html"


def test_a_longer_replacement_grows_the_value() -> None:
    out = _resolved('<a href="x">t</a>', "https://example.com/very/deep/path/")
    assert out == '<a href="https://example.com/very/deep/path/x">t</a>'


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
        parse_fragment('<a href="a">x</a>').resolve_links(None)  # ty: ignore[invalid-argument-type]


def _resolved_href(href: str, base: str) -> str:
    root = parse_fragment(f'<a href="{escape(href, quote=True)}">x</a>')
    root.resolve_links(base)
    (link,) = root.links()
    return link.url


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
        pytest.param(_HTTPS, "https:///path", id="same-scheme-empty-authority-slash-rejoined"),
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


def _boom(url: str) -> str:
    msg = f"nope: {url}"
    raise ValueError(msg)


def _rewritten(html: str, replace: object) -> str:
    root = parse_fragment(html)
    root.rewrite_links(replace)  # ty: ignore[invalid-argument-type]
    return root.inner_html


def test_replacement_string_is_substituted() -> None:
    assert _rewritten('<a href="a">x</a>', lambda url: url.upper()) == '<a href="A">x</a>'


def test_returning_none_leaves_the_link_unchanged() -> None:
    assert _rewritten('<a href="a">x</a>', lambda _url: None) == '<a href="a">x</a>'


def test_returning_the_same_string_is_not_a_mutation() -> None:
    assert _rewritten('<a href="a">x</a>', lambda url: url) == '<a href="a">x</a>'


def test_only_some_candidates_change() -> None:
    out = _rewritten('<img srcset="a.png 1x, b.png 2x">', lambda url: "X" if url == "a.png" else None)
    assert out == '<img srcset="X 1x, b.png 2x">'


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


def test_rewrite_links_requires_a_callable() -> None:
    with pytest.raises(TypeError, match="expected a callable"):
        parse_fragment("<a href=a>x</a>").rewrite_links("not callable")  # ty: ignore[invalid-argument-type]


@pytest.mark.parametrize("depth", [0, 16, 128], ids=["ordinary", "nested", "beyond-dns-length"])
@pytest.mark.parametrize(
    ("base", "host", "external"),
    [
        pytest.param("example.co.uk", "example.co.uk", False, id="normal-shared"),
        pytest.param("example.co.uk", "other.co.uk", True, id="normal-distinct"),
        pytest.param("a.github.io", "b.github.io", True, id="private-distinct"),
        pytest.param("www.ck", "www.ck", False, id="exception-shared"),
        pytest.param("a.ck", "b.ck", True, id="wildcard-distinct"),
        pytest.param("example.invalid", "s.example.invalid", True, id="unknown-host-compared-whole"),
        pytest.param("münchen.de", "xn--mnchen-3ya.de", False, id="idna-shared"),
        pytest.param(
            "a.foo.001.test.code-builder-stg.platform.salesforce.com",
            "b.foo.001.test.code-builder-stg.platform.salesforce.com",
            True,
            id="deepest-wildcard-distinct",
        ),
    ],
)
def test_external_links_keep_public_suffix_boundary(base: str, host: str, *, depth: int, external: bool) -> None:
    href: Final = f"https://{'s.' * depth}{host}/x"
    assert extract_links(f'<a href="{href}">x</a>', f"https://{base}/", external_only=True) == (
        {href} if external else set()
    )
