"""extract_links: anchors from the parsed DOM, base resolution, filters, and variant deduplication."""

from __future__ import annotations

import pytest

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
    ("href", "external"),
    [
        pytest.param("https://test.com/x", False, id="same-host"),
        pytest.param("https://www.test.com/x", False, id="www-twin"),
        pytest.param("https://blog.test.com/x", False, id="subdomain"),
        pytest.param("https://test.com.evil.example/x", True, id="host-suffix-attack"),
        pytest.param("https://other.net/x", True, id="other-host"),
    ],
)
def test_external_boundary_is_the_www_less_host(href: str, *, external: bool) -> None:
    links = extract_links(f'<a href="{href}">x</a>', "https://www.test.com/", external_only=True)
    assert links == ({href} if external else set())


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
