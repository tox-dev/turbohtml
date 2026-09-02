"""The <meta> date stage's C walk: key classification, the pubdate flag, and the window boundary.

These exercise the branches the C meta stage adds -- an over-long or non-ASCII key, a valueless key, the
publication-wins-over-modification precedence, the pubdate="pubdate" flag's spellings, and the inclusive window
edges -- through the public dates() API.
"""

from __future__ import annotations

from datetime import date

import pytest

from turbohtml import parse
from turbohtml.extract import DateExtraction, PublicationDate, dates

_GOOD: str = '<meta name="date" content="2016-06-07">'
"""A trailing publication meta that wins whenever the element under test contributes nothing."""


@pytest.mark.parametrize(
    "element",
    [
        pytest.param(
            '<meta name="a-really-quite-long-meta-key-name" content="2011-01-02">', id="key-longer-than-vocab"
        ),
        pytest.param('<meta name="daté" content="2011-01-02">', id="non-ascii-key"),
        pytest.param('<meta name content="2011-01-02">', id="valueless-key"),
        pytest.param('<meta name="author" content="2011-01-02">', id="non-date-key"),
        pytest.param('<meta property="og:title" content="2011-01-02">', id="off-vocab-property"),
    ],
)
def test_a_meta_that_carries_no_date_key_is_skipped(element: str) -> None:
    # each element parses a valid date but has no recognized key, so the trailing _GOOD meta is what wins
    assert dates(element + _GOOD) == PublicationDate("2016-06-07", "meta")


@pytest.mark.parametrize(
    ("pubdate", "expected"),
    [
        pytest.param('pubdate="pubdate"', PublicationDate("2014-03-08", "meta"), id="pubdate-flag-set"),
        pytest.param('pubdate="PubDate"', PublicationDate("2014-03-08", "meta"), id="pubdate-flag-any-case"),
        pytest.param('pubdate="nope"', PublicationDate("2016-06-07", "meta"), id="pubdate-other-value-ignored"),
        pytest.param("pubdate", PublicationDate("2016-06-07", "meta"), id="valueless-pubdate-ignored"),
        pytest.param(
            'pubdate="a-pubdate-value-longer-than-any-key"',
            PublicationDate("2016-06-07", "meta"),
            id="pubdate-over-long-value-ignored",
        ),
    ],
)
def test_pubdate_flag_only_marks_the_literal_value(pubdate: str, expected: PublicationDate) -> None:
    # only pubdate="pubdate" (case-insensitively) makes an otherwise keyless meta a publication date
    assert dates(f'<meta {pubdate} content="2014-03-08">{_GOOD}') == expected


def test_dates_rejects_a_non_integer_argument() -> None:
    # the private C entry point takes eight ints and a flag; a wrong type must fail the parse, not read the tree
    extensive = True
    with pytest.raises(TypeError):
        parse("")._dates("published", 2016, 1, 1, 1, 2100, 1, 1, extensive)  # ty: ignore[invalid-argument-type]  # bad type


def test_http_equiv_last_modified_is_a_modification_key() -> None:
    html = '<meta http-equiv="last-modified" content="2019-05-06">'
    assert dates(html) == PublicationDate("2019-05-06", "meta")


def test_a_date_key_meta_without_content_or_datetime_is_skipped() -> None:
    # the date key is recognized but there is no content and only a valueless datetime, so nothing is dated here
    assert dates("<meta name=date datetime>" + _GOOD) == PublicationDate("2016-06-07", "meta")


def test_publication_key_wins_over_a_modification_key_on_one_element() -> None:
    # name marks a modification date and property a publication date on the same <meta>; publication wins
    html = '<meta name="lastmod" property="article:published_time" content="2018-01-02">'
    assert dates(html, DateExtraction(original=True)) == PublicationDate("2018-01-02", "meta")


def test_a_modification_meta_is_the_reserve_when_a_publication_is_wanted() -> None:
    # want=publication, only a modification date present, so it returns as the fallback reserve
    html = '<meta name="lastmod" content="2017-02-01">'
    assert dates(html, DateExtraction(original=True)) == PublicationDate("2017-02-01", "meta")


@pytest.mark.parametrize(
    ("content", "min_date", "max_date", "expected"),
    [
        pytest.param("2017-01-01", date(2016, 6, 15), date(2100, 1, 1), "2017-01-01", id="min-later-year-admits"),
        pytest.param("2015-12-31", date(2016, 6, 15), date(2100, 1, 1), None, id="min-earlier-year-rejects"),
        pytest.param("2016-08-20", date(2016, 6, 15), date(2100, 1, 1), "2016-08-20", id="min-same-year-later-month"),
        pytest.param("2016-05-20", date(2016, 6, 15), date(2100, 1, 1), None, id="min-same-year-earlier-month"),
        pytest.param("2016-06-20", date(2016, 6, 15), date(2100, 1, 1), "2016-06-20", id="min-same-month-later-day"),
        pytest.param("2016-06-10", date(2016, 6, 15), date(2100, 1, 1), None, id="min-same-month-earlier-day"),
        pytest.param("2015-01-01", date(1990, 1, 1), date(2016, 6, 15), "2015-01-01", id="max-earlier-year-admits"),
        pytest.param("2017-01-01", date(1990, 1, 1), date(2016, 6, 15), None, id="max-later-year-rejects"),
        pytest.param("2016-05-20", date(1990, 1, 1), date(2016, 6, 15), "2016-05-20", id="max-same-year-earlier-month"),
        pytest.param("2016-08-20", date(1990, 1, 1), date(2016, 6, 15), None, id="max-same-year-later-month"),
        pytest.param("2016-06-10", date(1990, 1, 1), date(2016, 6, 15), "2016-06-10", id="max-same-month-earlier-day"),
        pytest.param("2016-06-20", date(1990, 1, 1), date(2016, 6, 15), None, id="max-same-month-later-day"),
        pytest.param("2016-06-15", date(2016, 6, 15), date(2016, 6, 15), "2016-06-15", id="both-bounds-inclusive"),
    ],
)
def test_window_boundary_is_inclusive(content: str, min_date: date, max_date: date, expected: str | None) -> None:
    result = dates(f'<meta name="date" content="{content}">', DateExtraction(min_date=min_date, max_date=max_date))
    assert result == (None if expected is None else PublicationDate(expected, "meta"))
