"""dates: publication-date extraction over the meta, JSON-LD, time, URL, and visible-text signals."""

from __future__ import annotations

from datetime import date

import pytest

from turbohtml.extract import DateExtraction, PublicationDate, dates


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param(
            '<meta property="article:published_time" content="2016-12-23T10:00:00Z">',
            PublicationDate("2016-12-23", "meta"),
            id="meta-published",
        ),
        pytest.param(
            '<meta name="lastmod" content="2017-02-01">',
            PublicationDate("2017-02-01", "meta"),
            id="meta-modified",
        ),
        pytest.param(
            '<meta name="date" content="2016-06-07">',
            PublicationDate("2016-06-07", "meta"),
            id="meta-name-date",
        ),
        pytest.param(
            '<meta itemprop="datePublished" datetime="2015-04-09">',
            PublicationDate("2015-04-09", "meta"),
            id="meta-itemprop-datetime",
        ),
        pytest.param(
            '<meta pubdate="pubdate" content="2014-03-08">',
            PublicationDate("2014-03-08", "meta"),
            id="meta-pubdate-attr",
        ),
        pytest.param(
            '<script type="application/ld+json">{"datePublished":"2016-05-01"}</script>',
            PublicationDate("2016-05-01", "json-ld"),
            id="json-ld-published",
        ),
        pytest.param(
            '<script type="application/ld+json">{"@graph":[{"dateModified":"2018-09-09"}]}</script>',
            PublicationDate("2018-09-09", "json-ld"),
            id="json-ld-graph-nested",
        ),
        pytest.param(
            '<time datetime="2018-03-03">March</time>',
            PublicationDate("2018-03-03", "time"),
            id="time-datetime",
        ),
        pytest.param(
            "<time>2019-07-14</time>",
            PublicationDate("2019-07-14", "time"),
            id="time-text-only",
        ),
        pytest.param(
            '<span class="entry-date">April 5, 2017</span>',
            PublicationDate("2017-04-05", "time"),
            id="date-classed-element",
        ),
        pytest.param(
            '<link rel="canonical" href="http://x.com/2016/12/23/post.html">',
            PublicationDate("2016-12-23", "url"),
            id="url-canonical",
        ),
        pytest.param(
            '<meta property="og:url" content="http://x.com/2020/06/07/story">',
            PublicationDate("2020-06-07", "url"),
            id="url-og-url",
        ),
        pytest.param(
            "<body><p>Filed under news. Published July 4, 2016 by staff on July 4, 2016.</p></body>",
            PublicationDate("2016-07-04", "text"),
            id="text-modal-date",
        ),
    ],
)
def test_dates_signal_sources(html: str, expected: PublicationDate) -> None:
    assert dates(html) == expected


def test_dates_returns_none_without_any_date() -> None:
    assert dates("<html><body><p>No date anywhere here.</p></body></html>") is None


def test_dates_returns_none_for_a_bodyless_fragment() -> None:
    assert dates("<title>2016-01-01</title>") is None


def test_url_signal_outranks_meta() -> None:
    html = '<link rel="canonical" href="http://x.com/2016/12/23/a.html"><meta name="date" content="2011-11-11">'
    assert dates(html) == PublicationDate("2016-12-23", "url")


def test_url_without_a_date_pattern_is_ignored() -> None:
    html = '<link rel="canonical" href="http://x.com/story"><meta name="date" content="2011-11-11">'
    assert dates(html) == PublicationDate("2011-11-11", "meta")


def test_meta_outranks_json_ld() -> None:
    html = (
        '<meta name="date" content="2016-06-07">'
        '<script type="application/ld+json">{"datePublished":"2001-01-01"}</script>'
    )
    assert dates(html) == PublicationDate("2016-06-07", "meta")


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        pytest.param(False, PublicationDate("2017-02-01", "meta"), id="default-prefers-modified"),
        pytest.param(True, PublicationDate("2016-12-23", "meta"), id="original-prefers-published"),
    ],
)
def test_original_flag_routes_published_against_modified(expected: PublicationDate, *, original: bool) -> None:
    html = (
        '<meta property="article:published_time" content="2016-12-23">'
        '<meta property="article:modified_time" content="2017-02-01">'
    )
    assert dates(html, DateExtraction(original=original)) == expected


def test_off_role_meta_is_the_reserve_when_no_wanted_role_exists() -> None:
    html = '<meta property="article:published_time" content="2016-12-23">'
    assert dates(html, DateExtraction(original=False)) == PublicationDate("2016-12-23", "meta")


def test_updated_class_marks_a_modification_date() -> None:
    html = '<span class="last-updated">2019-05-06</span><span class="published">2018-01-02</span>'
    assert dates(html, DateExtraction(original=True)) == PublicationDate("2018-01-02", "time")
    assert dates(html, DateExtraction(original=False)) == PublicationDate("2019-05-06", "time")


def test_output_format_is_applied() -> None:
    html = '<meta name="date" content="2016-06-07">'
    assert dates(html, DateExtraction(output_format="%d/%m/%Y")) == PublicationDate("07/06/2016", "meta")


def test_extensive_search_off_skips_visible_text() -> None:
    html = "<body><p>Posted May 3, 2013 here.</p></body>"
    assert dates(html) == PublicationDate("2013-05-03", "text")
    assert dates(html, DateExtraction(extensive_search=False)) is None


@pytest.mark.parametrize(
    ("min_date", "expected"),
    [
        pytest.param(None, None, id="before-default-1995-floor"),
        pytest.param(date(1990, 1, 1), PublicationDate("1993-08-08", "meta"), id="lowered-min-admits-it"),
    ],
)
def test_min_date_floor(min_date: date | None, expected: PublicationDate | None) -> None:
    assert dates('<meta name="date" content="1993-08-08">', DateExtraction(min_date=min_date)) == expected


def test_max_date_rejects_a_future_stamp() -> None:
    html = '<meta name="date" content="2099-12-31">'
    assert dates(html) is None
    assert dates(html, DateExtraction(max_date=date(2100, 1, 1))) == PublicationDate("2099-12-31", "meta")


def test_config_rejects_a_min_after_max() -> None:
    with pytest.raises(ValueError, match="after max_date"):
        DateExtraction(min_date=date(2020, 1, 1), max_date=date(2019, 1, 1))


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        pytest.param("2016-12-23", "2016-12-23", id="iso-hyphen"),
        pytest.param("2016/12/23", "2016-12-23", id="iso-slash"),
        pytest.param("2016.12.23T09:00", "2016-12-23", id="iso-dot-with-time"),
        pytest.param("20160523", "2016-05-23", id="compact-eight-digit"),
        pytest.param("08.05.2012", "2012-05-08", id="european-day-first"),
        pytest.param("23/12/2016", "2016-12-23", id="european-slash"),
        pytest.param("05.13.2013", "2013-05-13", id="month-day-swapped-back"),
        pytest.param("01.02.13", "2013-02-01", id="two-digit-year-2000s"),
        pytest.param("01.02.97", "1997-02-01", id="two-digit-year-1900s"),
    ],
)
def test_numeric_date_spellings(content: str, expected: str) -> None:
    assert dates(f'<meta name="date" content="{content}">', DateExtraction(max_date=date(2100, 1, 1))) == (
        PublicationDate(expected, "meta")
    )


def test_an_impossible_date_is_rejected() -> None:
    assert dates('<meta name="date" content="2016-02-30">') is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("Published July 4, 2016 today.", "2016-07-04", id="english-month-first"),
        pytest.param("Am 4. Juli 2016 hier.", "2016-07-04", id="german-day-first"),
        pytest.param("Le 4 juillet 2016 ici.", "2016-07-04", id="french-day-first"),
        pytest.param("Il 4 luglio 2016 qui.", "2016-07-04", id="italian-day-first"),
        pytest.param("Posted 4th of July 2016.", "2016-07-04", id="english-ordinal-of"),
    ],
)
def test_written_out_months(text: str, expected: str) -> None:
    assert dates(f"<body><p>{text}</p></body>") == PublicationDate(expected, "text")


def test_text_ties_break_by_original_or_recent() -> None:
    html = "<body><p>Seen 2014-01-01 once and 2018-01-01 once.</p></body>"
    assert dates(html, DateExtraction(original=True)) == PublicationDate("2014-01-01", "text")
    assert dates(html, DateExtraction(original=False)) == PublicationDate("2018-01-01", "text")


def test_multi_valued_class_list_is_matched() -> None:
    assert dates('<span class="post meta-date">2017-08-09</span>') == PublicationDate("2017-08-09", "time")


def test_valueless_meta_content_is_skipped() -> None:
    html = '<meta name="date" content><meta name="date" content="2016-06-07">'
    assert dates(html) == PublicationDate("2016-06-07", "meta")


def test_invalid_json_ld_block_is_skipped() -> None:
    html = (
        '<script type="application/ld+json">{ broken</script>'
        '<script type="application/ld+json">{"datePublished":"2016-05-01"}</script>'
    )
    assert dates(html) == PublicationDate("2016-05-01", "json-ld")


def test_json_ld_list_of_objects() -> None:
    html = '<script type="application/ld+json">[{"datePublished":"2016-05-01"}]</script>'
    assert dates(html) == PublicationDate("2016-05-01", "json-ld")


def test_scalar_item_in_json_ld_list_is_walked_without_a_date() -> None:
    html = '<script type="application/ld+json">["just a string", {"datePublished":"2016-05-01"}]</script>'
    assert dates(html) == PublicationDate("2016-05-01", "json-ld")


def test_a_non_date_meta_key_is_ignored() -> None:
    html = '<meta name="author" content="Ada"><meta name="date" content="2016-06-07">'
    assert dates(html) == PublicationDate("2016-06-07", "meta")


def test_json_ld_date_nested_in_a_list_value() -> None:
    html = '<script type="application/ld+json">{"items":[{"datePublished":"2016-05-01"}]}</script>'
    assert dates(html) == PublicationDate("2016-05-01", "json-ld")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("Impossible 2016-02-30 but real 2018-04-05.", "2018-04-05", id="invalid-iso-skipped"),
        pytest.param("European 08.05.2012 date in prose.", "2012-05-08", id="numeric-day-first-in-text"),
        pytest.param(
            "Impossible 31.02.2016 but real 05.06.2018.", "2018-06-05", id="invalid-numeric-day-first-skipped"
        ),
        pytest.param("Bad February 30, 2016 but June 7, 2018.", "2018-06-07", id="invalid-written-month-skipped"),
    ],
)
def test_visible_text_date_parsing(text: str, expected: str) -> None:
    assert dates(f"<body><p>{text}</p></body>") == PublicationDate(expected, "text")


def test_time_element_without_a_date_is_skipped() -> None:
    html = "<time>no date</time><time datetime='2018-03-03'>x</time>"
    assert dates(html) == PublicationDate("2018-03-03", "time")


def test_title_attribute_dates_a_marked_element() -> None:
    assert dates('<span class="pubdate" title="2017-10-11">ages ago</span>') == PublicationDate("2017-10-11", "time")
