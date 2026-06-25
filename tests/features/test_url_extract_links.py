from __future__ import annotations

from turbohtml.extract import UrlCleaning, extract_links

_PAGE = (
    "<html><body>"
    "<a href='/p/1?utm_source=ad'>one</a>"
    "<a href='/p/1?utm_source=other'>dup</a>"
    "<a href='https://other.example.org/y'>external</a>"
    "<a href='mailto:hi@example.com'>mail</a>"
    "<p style='background:url(hero.png)'>x</p>"
    "</body></html>"
)


def test_extract_links_keeps_internal_absolutized_cleaned_and_deduplicated() -> None:
    links = extract_links(_PAGE, "https://example.com/blog/")
    assert links == {"https://example.com/p/1", "https://example.com/blog/hero.png"}


def test_external_only_keeps_only_other_hosts() -> None:
    assert extract_links(_PAGE, "https://example.com/blog/", external_only=True) == {"https://other.example.org/y"}


def test_without_a_base_url_no_host_filter_runs_and_relative_links_drop() -> None:
    assert extract_links(_PAGE) == {"https://other.example.org/y"}


def test_options_apply_to_each_link() -> None:
    page = "<a href='https://example.com/dir/?utm_source=ad&id=3#frag'>x</a>"
    links = extract_links(page, "https://example.com/", options=UrlCleaning.aggressive())
    assert links == {"https://example.com/dir/?id=3"}


def test_links_that_scrub_to_nothing_are_skipped() -> None:
    assert extract_links("<a href='<p>'>x</a>") == set()


def test_empty_document_yields_no_links() -> None:
    assert extract_links("", "https://example.com/") == set()
