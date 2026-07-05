"""clean_url: the scrub pass, the web-URL validation, the language filter, and the UrlCleaning config itself."""

from __future__ import annotations

import pytest

from turbohtml.extract import UrlCleaning, clean_url


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
