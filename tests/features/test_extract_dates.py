"""The :func:`turbohtml.extract.dates` publication-date entry point across every declared source."""

from __future__ import annotations

import pytest

from turbohtml import DateExtraction, dates


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param(
            "<head><meta property=article:published_time content=2024-05-06></head>",
            "2024-05-06",
            id="meta-article-published",
        ),
        pytest.param(
            "<head><meta name=date content=2023-02-03></head>",
            "2023-02-03",
            id="meta-name-date",
        ),
        pytest.param(
            "<head><meta itemprop=datePublished content=2022-08-09></head>",
            "2022-08-09",
            id="meta-itemprop-published",
        ),
        pytest.param(
            '<script type="application/ld+json">{"datePublished": "2019-07-08T10:00:00Z"}</script>',
            "2019-07-08",
            id="json-ld-published",
        ),
        pytest.param(
            '<script type="application/ld+json">[{"dateCreated": "2017-06-05"}]</script>',
            "2017-06-05",
            id="json-ld-array-block",
        ),
        pytest.param(
            '<script type="application/ld+json">{"a": {"uploadDate": "2016-05-04"}}</script>',
            "2016-05-04",
            id="json-ld-nested-dict",
        ),
        pytest.param(
            '<script type="application/ld+json">{"a": [{"datePublished": "2015-04-03"}]}</script>',
            "2015-04-03",
            id="json-ld-nested-list",
        ),
        pytest.param(
            "<article><time datetime=2018-11-12>shown</time></article>",
            "2018-11-12",
            id="time-datetime-attr",
        ),
        pytest.param(
            "<article><time>2016-04-05</time></article>",
            "2016-04-05",
            id="time-text-fallback",
        ),
        pytest.param(
            "<time datetime=2017-01-02 pubdate>x</time>",
            "2017-01-02",
            id="time-pubdate-flag",
        ),
        pytest.param(
            "<time datetime=2014-03-04 itemprop=datePublished>x</time>",
            "2014-03-04",
            id="time-itemprop-published",
        ),
        pytest.param(
            "<link rel=canonical href=https://x.test/2021/09/14/post>",
            "2021-09-14",
            id="url-canonical-ymd",
        ),
        pytest.param(
            "<head><meta property=og:url content=https://x.test/2020/03/story></head>",
            "2020-03-01",
            id="url-og-year-month",
        ),
        pytest.param(
            "<p>no date anywhere in this body</p>",
            None,
            id="no-date",
        ),
    ],
)
def test_dates_sources(html: str, expected: str | None) -> None:
    assert dates(html) == expected


def test_dates_accepts_bytes() -> None:
    assert dates(b"<meta property=article:published_time content=2024-05-06>") == "2024-05-06"


def test_dates_default_prefers_most_recent_modification() -> None:
    html = (
        "<head><meta property=article:published_time content=2020-01-01>"
        "<meta property=article:modified_time content=2022-03-04></head>"
    )
    assert dates(html) == "2022-03-04"


def test_dates_original_prefers_first_published() -> None:
    html = (
        "<head><meta property=article:published_time content=2020-01-01>"
        "<meta property=article:modified_time content=2022-03-04></head>"
    )
    assert dates(html, DateExtraction.published()) == "2020-01-01"


def test_dates_original_falls_back_to_modified_when_unpublished() -> None:
    html = "<head><meta property=article:modified_time content=2021-06-07></head>"
    assert dates(html, DateExtraction.published()) == "2021-06-07"


def test_dates_picks_latest_within_a_pool() -> None:
    html = (
        "<head><meta property=article:published_time content=2019-01-01>"
        "<meta name=date content=2021-12-31></head>"
    )
    assert dates(html) == "2021-12-31"


def test_dates_ignores_metas_without_a_key_or_content() -> None:
    html = (
        "<head><meta charset=utf-8><meta name=date>"
        "<meta property=article:published_time content=2024-05-06></head>"
    )
    assert dates(html) == "2024-05-06"


def test_dates_ignores_unrelated_links_and_metas_for_urls() -> None:
    html = (
        "<head><link rel=stylesheet href=https://x.test/2021/09/14/style.css>"
        "<link rel=canonical>"
        "<meta property=og:title content=https://x.test/2019/01/02/post>"
        "<meta property=og:url content=https://x.test/2020/06/07/post></head>"
    )
    assert dates(html) == "2020-06-07"


def test_dates_skips_non_date_json_ld_keys() -> None:
    html = '<script type="application/ld+json">{"@type": "Article", "datePublished": "2018-03-04"}</script>'
    assert dates(html) == "2018-03-04"


def test_dates_reads_json_ld_modified() -> None:
    assert dates('<script type="application/ld+json">{"dateModified": "2021-08-09"}</script>') == "2021-08-09"


def test_dates_tolerates_scalar_json_ld_blocks() -> None:
    html = '<script type="application/ld+json">"just a string"</script><meta name=date content=2020-01-01>'
    assert dates(html) == "2020-01-01"


def test_dates_reads_time_modified() -> None:
    assert dates("<time itemprop=dateModified datetime=2021-08-09>x</time>") == "2021-08-09"


def test_dates_ignores_dateless_urls() -> None:
    html = "<head><link rel=canonical href=https://x.test/about><meta name=date content=2020-01-01></head>"
    assert dates(html) == "2020-01-01"


def test_dates_original_prefers_generic_time_over_modified() -> None:
    html = (
        "<head><meta property=article:modified_time content=2022-03-04></head>"
        "<body><time>2010-01-01</time></body>"
    )
    assert dates(html, DateExtraction.published()) == "2010-01-01"
