from __future__ import annotations

from datetime import date, timedelta
from typing import Final, cast

import pytest
from bench.ci import benchmarks

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


def test_first_off_role_meta_is_preserved() -> None:
    html = (
        '<meta property="article:published_time" content="2016-12-23">'
        '<meta property="article:published_time" content="2017-02-01">'
    )
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


@pytest.mark.parametrize(
    "html",
    [
        pytest.param('<meta name="date" content><meta name="date" content="2016-06-07">', id="valueless-content"),
        pytest.param('<meta name="author" content="Ada"><meta name="date" content="2016-06-07">', id="unrelated-key"),
    ],
)
def test_meta_skips_invalid_candidates(html: str) -> None:
    assert dates(html) == PublicationDate("2016-06-07", "meta")


@pytest.mark.parametrize(
    "html",
    [
        pytest.param(
            '<script type="application/ld+json">{ broken</script>'
            '<script type="application/ld+json">{"datePublished":"2016-05-01"}</script>',
            id="invalid-block",
        ),
        pytest.param('<script type="application/ld+json">[{"datePublished":"2016-05-01"}]</script>', id="list"),
        pytest.param(
            '<script type="application/ld+json">["just a string", {"datePublished":"2016-05-01"}]</script>',
            id="scalar-list-item",
        ),
        pytest.param(
            '<script type="application/ld+json">{"items":[{"datePublished":"2016-05-01"}]}</script>',
            id="nested-list",
        ),
    ],
)
def test_json_ld_finds_valid_candidate(html: str) -> None:
    assert dates(html) == PublicationDate("2016-05-01", "json-ld")


def test_out_of_window_json_ld_candidate_is_skipped() -> None:
    # the only date predates the default 1995 floor, so the stage yields nothing rather than returning it
    html = '<script type="application/ld+json">{"datePublished":"1990-01-01"}</script>'
    assert dates(html, DateExtraction(extensive_search=False)) is None


def test_first_off_role_json_ld_date_is_the_reserve() -> None:
    # want=publication, two modification dates, so the first is held as the reserve and the second does not replace it
    html = '<script type="application/ld+json">[{"dateModified":"2016-01-01"},{"dateModified":"2017-01-01"}]</script>'
    assert dates(html, DateExtraction(original=True, extensive_search=False)) == PublicationDate(
        "2016-01-01", "json-ld"
    )


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


@pytest.mark.parametrize(
    "count", [pytest.param(1, id="single"), pytest.param(17, id="growth"), pytest.param(1000, id="collisions")]
)
def test_date_tally_ties_keeps_the_original(count: int) -> None:
    start: Final = date(2000, 1, 1)
    source: Final = f"<p>{' '.join((start + timedelta(days=index)).isoformat() for index in range(count))}</p>"
    assert dates(source, DateExtraction(original=True)) == PublicationDate(start.isoformat(), "text")


@pytest.mark.parametrize(
    "count", [pytest.param(1, id="single"), pytest.param(17, id="growth"), pytest.param(1000, id="collisions")]
)
def test_date_tally_ties_keeps_the_last_modified(count: int) -> None:
    start: Final = date(2000, 1, 1)
    source: Final = f"<p>{' '.join((start + timedelta(days=index)).isoformat() for index in range(count))}</p>"
    expected: Final = start + timedelta(days=count - 1)
    assert dates(source, DateExtraction(original=False)) == PublicationDate(expected.isoformat(), "text")


@pytest.mark.parametrize("original", [pytest.param(True, id="published"), pytest.param(False, id="modified")])
def test_date_tally_frequency_precedes_date_order(*, original: bool) -> None:
    start: Final = date(2000, 1, 1)
    source: Final = (
        f"<p>{' '.join((start + timedelta(days=index)).isoformat() for index in range(100))} 2000-02-01 2000-02-01</p>"
    )
    assert dates(source, DateExtraction(original=original)) == PublicationDate("2000-02-01", "text")


def test_date_tally_repeated_date() -> None:
    assert dates(f"<p>{'2000-01-01 ' * 1000}</p>") == PublicationDate("2000-01-01", "text")


def test_a_deeply_nested_json_ld_block_is_skipped_not_raised() -> None:
    # a JSON-LD block nested past the 400-level cap is skipped like any invalid block, so date extraction over it
    # returns normally rather than letting the decoder's RecursionError escape
    html = f'<script type="application/ld+json">{"[" * 1_000_000}</script>'
    assert dates(html) is None


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param('<link rel="canonical">', None, id="canonical-without-href"),
        pytest.param('<link href="/2016/12/23/">', None, id="link-without-rel"),
        pytest.param('<link rel="stylesheet" href="/2016/12/23/a.css">', None, id="not-canonical"),
        pytest.param('<meta name="og:url" content="http://x.com/2016/12/23/">', "2016-12-23", id="og-url-by-name"),
        pytest.param('<meta property="og:title" content="/2016/12/23/">', None, id="another-og-key"),
        pytest.param('<meta property="og:url" content="/story">', None, id="og-url-without-a-date"),
        pytest.param(
            '<link rel="canonical" href="/1990/01/01/"><meta name="date" content="2011-11-11">',
            "2011-11-11",
            id="url-date-out-of-window-falls-through",
        ),
    ],
)
def test_the_url_stage_reads_only_a_canonical_or_og_url(html: str, expected: str | None) -> None:
    found = dates(html, DateExtraction(extensive_search=False))
    assert (found and found.date) == expected


@pytest.mark.parametrize(
    ("block", "expected"),
    [
        pytest.param('{"datePublished": 20160101}', None, id="a-number-is-not-a-date"),
        pytest.param('{"datePublished": "soon"}', None, id="a-string-without-a-date"),
        pytest.param('{"datePublished": "1990-01-01"}', None, id="out-of-window"),
        pytest.param('{"@graph": [{"dateModified": "2016-01-02"}]}', "2016-01-02", id="inside-a-graph"),
        pytest.param(
            '{"name": "x", "author": {"name": "y"}, "@graph": [{"dateModified": "2016-01-03"}]}',
            "2016-01-03",
            id="after-a-scalar-and-a-dateless-object",
        ),
        pytest.param('[1, "two", {"dateModified": "2016-01-04"}]', "2016-01-04", id="after-list-scalars"),
    ],
)
def test_the_json_ld_stage_walks_the_decoded_blocks(block: str, expected: str | None) -> None:
    found = dates(f'<script type="application/ld+json">{block}</script>', DateExtraction(extensive_search=False))
    assert (found and found.date) == expected


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param('<span id="post-date">2016-01-05</span>', PublicationDate("2016-01-05", "time"), id="id-marker"),
        pytest.param(
            '<span itemprop="dateCreated">2016-01-06</span>',
            PublicationDate("2016-01-06", "time"),
            id="itemprop-marker",
        ),
        pytest.param(
            '<time datetime="soon">2016-01-07</time>', None, id="datetime-without-a-date-is-not-read-from-text"
        ),
        pytest.param(
            '<time pubdate datetime="2016-01-08">x</time>',
            PublicationDate("2016-01-08", "time"),
            id="pubdate-attribute",
        ),
        pytest.param('<span id="revised-on">2016-01-09</span>', None, id="id-with-a-role-word-but-no-marker"),
        pytest.param(
            '<span id="date" class="modified">2016-01-10</span>',
            PublicationDate("2016-01-10", "time"),
            id="class-modified",
        ),
        pytest.param('<span class="posted">1990-01-10</span>', None, id="out-of-window"),
        pytest.param(
            '<p class="intro" id="main" itemprop="name">2016-01-11</p>', None, id="attributes-without-a-marker"
        ),
        pytest.param(
            "<time>2016-01-15 then 2016-01-16</time>", PublicationDate("2016-01-15", "time"), id="first-text-date-wins"
        ),
        pytest.param(
            '<span id="date-updated">2016-01-17</span>', PublicationDate("2016-01-17", "time"), id="id-modified"
        ),
        pytest.param(
            "<time>January 2, 2015 then 03/04/2014 then 2016-01-18</time>",
            PublicationDate("2016-01-18", "time"),
            id="iso-pattern-priority",
        ),
        pytest.param(
            "<time>January 2, 2015 then 03/04/2014 then 04/05/2013</time>",
            PublicationDate("2014-04-03", "time"),
            id="numeric-pattern-priority",
        ),
        pytest.param(
            "<time>January 2, 2015 then February 3, 2014</time>",
            PublicationDate("2015-01-02", "time"),
            id="first-written-date",
        ),
        pytest.param(
            "<time>2016-02-30 then 2016-01-19</time>",
            PublicationDate("2016-01-19", "time"),
            id="invalid-calendar-before-match",
        ),
        pytest.param("<time>1990-01-01 then 2016-01-20</time>", None, id="first-outside-window"),
    ],
)
def test_the_time_stage_reads_marked_elements(html: str, expected: PublicationDate | None) -> None:
    assert dates(html, DateExtraction(extensive_search=False)) == expected


@pytest.mark.parametrize(
    "html",
    [
        pytest.param('<span class="posted">2016-01-11</span><span class="updated">2016-01-12</span>', id="class"),
        pytest.param('<span id="posted-on">2016-01-11</span><span id="date-updated">2016-01-12</span>', id="id"),
    ],
)
def test_a_published_marker_is_the_reserve_when_a_modification_is_wanted(html: str) -> None:
    assert dates(html) == PublicationDate("2016-01-12", "time")
    assert dates(html, DateExtraction(original=True)) == PublicationDate("2016-01-11", "time")


def test_a_text_tie_breaks_the_same_way_whichever_date_comes_first() -> None:
    html = "<body><p>Seen 2018-01-01 once and 2014-01-01 once.</p></body>"
    assert dates(html, DateExtraction(original=True)) == PublicationDate("2014-01-01", "text")
    assert dates(html) == PublicationDate("2018-01-01", "text")


def test_the_text_stage_tallies_more_dates_than_its_first_allocation() -> None:
    days = [date(2016, 1, day).isoformat() for day in range(1, 21)]
    html = f"<body>{' '.join(days)} {days[7]}</body>"
    assert dates(html) == PublicationDate("2016-01-08", "text")


def test_the_text_stage_ignores_dates_outside_the_window() -> None:
    html = "<body>1990-01-01 1990-01-01 2016-01-13</body>"
    assert dates(html) == PublicationDate("2016-01-13", "text")


_TEXT_NESTING: Final[int] = 8
_MARKUP_ONLY: Final[DateExtraction] = DateExtraction(extensive_search=False)
_FOUND: Final[PublicationDate] = PublicationDate("2020-01-02", "time")


def _nested(depth: int, opener: str = "<time>") -> str:
    # every level but the innermost opens with a date before the window, so the walk has to reach the innermost
    outer = f"{opener}1990-01-01 " * (depth - 1)
    return f"{outer}{opener}2020-01-02"


@pytest.mark.parametrize(
    "depth",
    [pytest.param(1, id="single"), pytest.param(2, id="pair"), pytest.param(_TEXT_NESTING, id="at-limit")],
)
def test_nested_date_elements_each_read_their_text(depth: int) -> None:
    assert dates(_nested(depth), _MARKUP_ONLY) == _FOUND


@pytest.mark.parametrize(
    "opener",
    [
        pytest.param("<time>", id="time"),
        pytest.param("<div class=date>", id="class"),
        pytest.param("<div id=posted>", id="id"),
        pytest.param("<span itemprop=date>", id="itemprop"),
    ],
)
def test_an_element_nested_past_the_limit_does_not_read_its_text(opener: str) -> None:
    assert dates(_nested(_TEXT_NESTING + 1, opener), _MARKUP_ONLY) is None


def test_an_attribute_still_dates_an_element_nested_past_the_limit() -> None:
    html = f'{"<time>1990-01-01 " * _TEXT_NESTING}<time datetime="2020-01-02">'
    assert dates(html, _MARKUP_ONLY) == _FOUND


def test_an_element_dated_by_its_attribute_does_not_count_as_nesting() -> None:
    html = '<time datetime="1990-01-01">' * (_TEXT_NESTING + 1) + _nested(_TEXT_NESTING)
    assert dates(html, _MARKUP_ONLY) == _FOUND


def test_nesting_is_counted_per_element_not_per_page() -> None:
    closed = "<time>1990-01-01 " * _TEXT_NESTING + "</time>" * _TEXT_NESTING
    assert dates(f"<div>{closed}</div>{closed}<time>2020-01-02</time>", _MARKUP_ONLY) == _FOUND
    assert dates(f"{closed}<p>then</p>{_nested(_TEXT_NESTING)}", _MARKUP_ONLY) == _FOUND


def test_the_text_stage_still_reads_an_element_nested_past_the_limit() -> None:
    assert dates(_nested(_TEXT_NESTING + 1)) == PublicationDate("2020-01-02", "text")


_GOOD: Final[str] = '<meta name="date" content="2016-06-07">'
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


def test_http_equiv_last_modified_is_a_modification_key() -> None:
    html = '<meta http-equiv="last-modified" content="2019-05-06">'
    assert dates(html) == PublicationDate("2019-05-06", "meta")


def test_a_date_key_meta_without_content_or_datetime_is_skipped() -> None:
    # the date key is recognized but there is no content and only a valueless datetime, so nothing is dated here
    assert dates(f"<meta name=date datetime>{_GOOD}") == PublicationDate("2016-06-07", "meta")


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
    assert result == (expected and PublicationDate(expected, "meta"))


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        pytest.param("date-tally", "2002-09-26", id="distinct"),
        pytest.param("date-tally-repeated", "2000-01-01", id="repeated"),
    ],
)
def test_date_tally_benchmark_output(name: str, expected: str) -> None:
    _, _, load = next(benchmark for benchmark in benchmarks() if benchmark[0] == name)
    assert dates(cast("str", load())) == PublicationDate(expected, "text")
