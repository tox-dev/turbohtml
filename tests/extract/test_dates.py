"""dates: publication-date extraction over the meta, JSON-LD, time, URL, and visible-text signals."""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING, Final

import pytest

from turbohtml import parse
from turbohtml._html import _date_scan, _date_scan_all, _date_url
from turbohtml.extract import DateExtraction, PublicationDate, dates

if TYPE_CHECKING:
    from collections.abc import Callable
from typing import cast

from bench.ci import benchmarks


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


@pytest.mark.parametrize("count", [1, 17, 1000], ids=["single", "growth", "collisions"])
@pytest.mark.parametrize("original", [True, False], ids=["published", "modified"])
def test_date_tally_ties(count: int, *, original: bool) -> None:
    start: Final = date(2000, 1, 1)
    source: Final = "<p>" + " ".join((start + timedelta(days=index)).isoformat() for index in range(count)) + "</p>"
    expected: Final = start if original else start + timedelta(days=count - 1)
    assert dates(source, DateExtraction(original=original)) == PublicationDate(expected.isoformat(), "text")


@pytest.mark.parametrize("original", [True, False], ids=["published", "modified"])
def test_date_tally_frequency_precedes_date_order(*, original: bool) -> None:
    start: Final = date(2000, 1, 1)
    source: Final = (
        "<p>"
        + " ".join((start + timedelta(days=index)).isoformat() for index in range(100))
        + " 2000-02-01 2000-02-01</p>"
    )
    assert dates(source, DateExtraction(original=original)) == PublicationDate("2000-02-01", "text")


def test_date_tally_repeated_date() -> None:
    assert dates("<p>" + "2000-01-01 " * 1000 + "</p>") == PublicationDate("2000-01-01", "text")


_WINDOW = (1995, 1, 1, 2100, 1, 1)


def _run(html: str, *, extensive: bool = True) -> tuple[int, int, int, str] | None:
    """Call the entry point wanting a modification date, in the fixed window, from a 2026 vantage point."""
    return parse(html)._dates(2, 2026, *_WINDOW, extensive)


def test_the_entry_point_names_the_winning_signal() -> None:
    assert _run('<link rel="canonical" href="http://x.com/2016/12/23/a.html">') == (2016, 12, 23, "url")


def test_the_entry_point_answers_none_without_a_date() -> None:
    assert _run("<p>nothing</p>") is None


def test_extensive_off_skips_the_text_stage() -> None:
    assert _run("<p>2016-12-23</p>", extensive=False) is None


@pytest.mark.parametrize(
    "args",
    [
        pytest.param((2, 2026, 1995, 1, 1, 2100, 1), id="too-few"),
        pytest.param((2, 2026, 1995, 1, 1, 2100, 1, 1, True, 0), id="too-many"),
        pytest.param((2.5, 2026, 1995, 1, 1, 2100, 1, 1, True), id="want-is-a-float"),
    ],
)
def test_the_entry_point_rejects_bad_arguments(args: tuple[object, ...]) -> None:
    with pytest.raises(TypeError):
        parse("")._dates(*args)  # ty: ignore[invalid-argument-type]  # the argument check is the point


def test_a_json_ld_block_too_deep_to_decode_propagates() -> None:
    # malformed JSON is skipped, but a block that overflows the decoder's recursion budget is an error the caller
    # sees; the depth is well past every interpreter's budget so the error is the same everywhere
    html = '<script type="application/ld+json">' + "[" * 1_000_000 + "</script>"
    with pytest.raises(RecursionError):
        dates(html)


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
    assert (found.date if found else None) == expected


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
    assert (found.date if found else None) == expected


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
    html = "<body>" + " ".join(days) + " " + days[7] + "</body>"
    assert dates(html) == PublicationDate("2016-01-08", "text")


def test_the_text_stage_ignores_dates_outside_the_window() -> None:
    html = "<body>1990-01-01 1990-01-01 2016-01-13</body>"
    assert dates(html) == PublicationDate("2016-01-13", "text")


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


_YEAR = 2026
"""The pivot a two-digit year expands against, fixed so the two-digit-year cases stay stable over time."""

_SCAN_CASES = [
    pytest.param("2016-12-23", (2016, 12, 23), id="iso-hyphen"),
    pytest.param("2016/1/2", (2016, 1, 2), id="iso-slash-single"),
    pytest.param("2016.12.23T09:00", (2016, 12, 23), id="iso-dot-time"),
    pytest.param("12020-01-02", (2020, 1, 2), id="iso-embedded-after-digit"),
    pytest.param("2016-01-09", (2016, 1, 9), id="iso-month-two-zero"),
    pytest.param("2016.5.6", (2016, 5, 6), id="iso-month1-dot-sep"),
    pytest.param("2016-02-30", None, id="iso-invalid-calendar"),
    pytest.param("2020-02-29", (2020, 2, 29), id="iso-leap-valid"),
    pytest.param("2019-02-29", None, id="iso-leap-invalid"),
    pytest.param("2000-02-29", (2000, 2, 29), id="iso-leap-div-400"),
    pytest.param("2016-12-31", (2016, 12, 31), id="iso-day-31"),
    pytest.param("2016-12-39", (2016, 12, 3), id="iso-day-39"),
    pytest.param("1989-12-31", None, id="iso-year-below-range"),
    pytest.param("2100-01-01", None, id="iso-year-above-range"),
    pytest.param("2016-12x-05", None, id="iso-month2-nonsep-falls-to-month1"),
    pytest.param("2016-0a-05", None, id="iso-month-nondigit"),
    pytest.param("2016-00-01", None, id="iso-month-double-zero"),
    pytest.param("2016-01-a", None, id="iso-day-nondigit"),
    pytest.param("2016-01-0x", None, id="iso-day-zero"),
    pytest.param("2016-12-0x", None, id="iso-day2-nondigit"),
    pytest.param("2016-12-2x", (2016, 12, 2), id="iso-day2-second-nondigit"),
    pytest.param("2016-12-00", None, id="iso-day-double-zero"),
    pytest.param("2016-12-", None, id="iso-trailing-sep"),
    pytest.param("20160523", (2016, 5, 23), id="compact-eight"),
    pytest.param("20169523", None, id="compact-eight-bad-month"),
    pytest.param("1990123", (1990, 12, 3), id="compact-seven-wide-month"),
    pytest.param("2016120", (2016, 1, 20), id="compact-seven-wide-month-bad-day"),
    pytest.param("2016919", (2016, 9, 19), id="compact-seven-narrow-month"),
    pytest.param("2016003", None, id="compact-seven-zero-month"),
    pytest.param("199012", (1990, 1, 2), id="compact-six"),
    pytest.param("199000", None, id="compact-six-zero-month"),
    pytest.param("201605234", None, id="compact-nine-too-long"),
    pytest.param("18010101", None, id="compact-bad-year"),
    pytest.param("20160230", None, id="compact-invalid-calendar"),
    pytest.param("120160523", None, id="compact-preceded-by-digit"),
    pytest.param("08.05.2012", (2012, 5, 8), id="dmy-dot"),
    pytest.param("23/12/2016", (2016, 12, 23), id="dmy-slash"),
    pytest.param("05.13.2013", (2013, 5, 13), id="dmy-swapped-month"),
    pytest.param("01.02.13", (2013, 2, 1), id="dmy-two-digit-2000s"),
    pytest.param("01.02.97", (1997, 2, 1), id="dmy-two-digit-1900s"),
    pytest.param("1.2.26", (2026, 2, 1), id="dmy-pivot-equal"),
    pytest.param("1.2.27", (1927, 2, 1), id="dmy-pivot-above"),
    pytest.param("31.02.2016", None, id="dmy-invalid"),
    pytest.param("13.15.2020", None, id="dmy-both-impossible"),
    pytest.param("32.01.2020", None, id="dmy-day-too-big"),
    pytest.param("05.00.2020", None, id="dmy-month-zero"),
    pytest.param("5.1x.2020", None, id="dmy-month-nondigit"),
    pytest.param("1/2/999", (999, 2, 1), id="dmy-three-digit-year"),
    pytest.param("1/2/20205", None, id="dmy-five-digit-year"),
    pytest.param("12-05-2020", (2020, 5, 12), id="dmy-hyphen"),
    pytest.param("1-2-3", None, id="dmy-one-year-digit"),
    pytest.param("2016-12-19", (2016, 12, 19), id="iso-day-19"),
    pytest.param("2016-12-1/9", (2016, 12, 1), id="iso-day2-sub-zero-second"),
    pytest.param("20161299", None, id="compact-eight-bad-day"),
    pytest.param("2016900", None, id="compact-seven-narrow-bad-day"),
    pytest.param("199010", None, id="compact-six-bad-day"),
    pytest.param("2016-2-30 real 2018-04-05", None, id="iso-then-invalid-first"),
    pytest.param("", None, id="empty"),
    pytest.param("   ", None, id="whitespace"),
    pytest.param("no date here", None, id="no-date"),
    pytest.param("Published July 4, 2016 today.", None, id="english-month-first"),
    pytest.param("Am 4. Juli 2016 hier.", None, id="german-day-first"),
    pytest.param("Le 4 juillet 2016 ici.", None, id="french-day-first"),
    pytest.param("Il 4 luglio 2016 qui.", None, id="italian-day-first"),
    pytest.param("Posted 4th of July 2016.", None, id="english-ordinal-of"),
    pytest.param("25th December 2020", None, id="day-first-ordinal"),
    pytest.param("July 4th 2016", None, id="month-first-ordinal"),
    pytest.param("1st January 2000", None, id="ordinal-st"),
    pytest.param("22nd March 2020", None, id="ordinal-nd"),
    pytest.param("3rd April 2019", None, id="ordinal-rd"),
    pytest.param("July 4th", None, id="ordinal-then-end"),
    pytest.param("5 of", None, id="of-then-end"),
    pytest.param("December 25, 2020", None, id="month-first-two-digit-day"),
    pytest.param("Jan. 5, 2019", None, id="month-first-dot"),
    pytest.param("Février 3, 2015", None, id="accented-french"),
    pytest.param("AOÛT 15, 2018", None, id="accented-upper-august"),
    pytest.param("märz 7 2011", None, id="accented-german-lower"),
    pytest.param("MÄRZ 7 2011", None, id="accented-german-upper"),
    pytest.param("3 décembre 2011", None, id="accented-december"),
    pytest.param("enero 1 2020", None, id="spanish"),
    pytest.param("gennaio 1 2020", None, id="italian-name"),
    pytest.param("Sept 9, 2021", None, id="unsupported-sept-abbrev"),
    pytest.param("September 9, 2021", None, id="full-september"),
    pytest.param("July 39, 2016", None, id="invalid-written-day"),
    pytest.param("July 0, 2016", None, id="written-day-zero"),
    pytest.param("July 4, 1899", None, id="written-year-out-of-range"),
    pytest.param("januaryfoo 5 2020", None, id="longer-name-then-fail"),
    pytest.param("Seen 2014-01-01 once and 2018-01-01 once.", (2014, 1, 1), id="modal-two-dates"),
    pytest.param("Impossible 2016-02-30 but real 2018-04-05.", None, id="invalid-iso-then-valid"),
    pytest.param("European 08.05.2012 date in prose.", (2012, 5, 8), id="dmy-in-prose"),
    pytest.param("July\t4\t2016", None, id="tab-separated"),
    pytest.param("July\x01 4 2016", None, id="control-char-in-gap"),
    pytest.param("\u00d7 July 4 2016", None, id="mult-sign-before-month"),
    pytest.param("July ! 2016", None, id="month-then-nonday"),
    pytest.param("July", None, id="month-at-end"),
    pytest.param("July ", None, id="month-space-at-end"),
    pytest.param("5 May 2016", None, id="day-first-single"),
    pytest.param("12 May 2016", None, id="day-first-two-digit"),
    pytest.param("5 x 2016", None, id="day-first-then-nonmonth"),
    pytest.param("abc 5", None, id="day-at-end"),
    pytest.param("3 May 2016", None, id="of-not-consumed"),
    pytest.param("5 Jan. 2019", None, id="day-first-month-dot"),
    pytest.param("5 January, 2019", None, id="day-first-month-comma"),
    pytest.param("5 Jan2019", None, id="day-first-no-space"),
    pytest.param("5 Jan 1899", None, id="day-first-year-out-of-range"),
    pytest.param("July 5s 2016", None, id="ordinal-s-not-t"),
    pytest.param("July 5nz 2016", None, id="ordinal-n-not-d"),
    pytest.param("July 5rz 2016", None, id="ordinal-r-not-d"),
    pytest.param("July 5tz 2016", None, id="ordinal-t-not-h"),
    pytest.param("5 ofx Feb 2019", None, id="of-not-space"),
    pytest.param("5 Jan", None, id="day-first-month-at-end"),
]

_SCAN_ALL_CASES = [
    pytest.param("2016-12-23", [(2016, 12, 23)], id="iso-hyphen"),
    pytest.param("2016/1/2", [(2016, 1, 2)], id="iso-slash-single"),
    pytest.param("2016.12.23T09:00", [(2016, 12, 23)], id="iso-dot-time"),
    pytest.param("12020-01-02", [(2020, 1, 2)], id="iso-embedded-after-digit"),
    pytest.param("2016-01-09", [(2016, 1, 9)], id="iso-month-two-zero"),
    pytest.param("2016.5.6", [(2016, 5, 6)], id="iso-month1-dot-sep"),
    pytest.param("2016-02-30", [], id="iso-invalid-calendar"),
    pytest.param("2020-02-29", [(2020, 2, 29)], id="iso-leap-valid"),
    pytest.param("2019-02-29", [], id="iso-leap-invalid"),
    pytest.param("2000-02-29", [(2000, 2, 29)], id="iso-leap-div-400"),
    pytest.param("2016-12-31", [(2016, 12, 31)], id="iso-day-31"),
    pytest.param("2016-12-39", [(2016, 12, 3)], id="iso-day-39"),
    pytest.param("1989-12-31", [], id="iso-year-below-range"),
    pytest.param("2100-01-01", [], id="iso-year-above-range"),
    pytest.param("2016-12x-05", [], id="iso-month2-nonsep-falls-to-month1"),
    pytest.param("2016-0a-05", [], id="iso-month-nondigit"),
    pytest.param("2016-00-01", [], id="iso-month-double-zero"),
    pytest.param("2016-01-a", [], id="iso-day-nondigit"),
    pytest.param("2016-01-0x", [], id="iso-day-zero"),
    pytest.param("2016-12-0x", [], id="iso-day2-nondigit"),
    pytest.param("2016-12-2x", [(2016, 12, 2)], id="iso-day2-second-nondigit"),
    pytest.param("2016-12-00", [], id="iso-day-double-zero"),
    pytest.param("2016-12-", [], id="iso-trailing-sep"),
    pytest.param("20160523", [], id="compact-eight"),
    pytest.param("20169523", [], id="compact-eight-bad-month"),
    pytest.param("1990123", [], id="compact-seven-wide-month"),
    pytest.param("2016120", [], id="compact-seven-wide-month-bad-day"),
    pytest.param("2016919", [], id="compact-seven-narrow-month"),
    pytest.param("2016003", [], id="compact-seven-zero-month"),
    pytest.param("199012", [], id="compact-six"),
    pytest.param("199000", [], id="compact-six-zero-month"),
    pytest.param("201605234", [], id="compact-nine-too-long"),
    pytest.param("18010101", [], id="compact-bad-year"),
    pytest.param("20160230", [], id="compact-invalid-calendar"),
    pytest.param("120160523", [], id="compact-preceded-by-digit"),
    pytest.param("08.05.2012", [(2012, 5, 8)], id="dmy-dot"),
    pytest.param("23/12/2016", [(2016, 12, 23)], id="dmy-slash"),
    pytest.param("05.13.2013", [(2013, 5, 13)], id="dmy-swapped-month"),
    pytest.param("01.02.13", [(2013, 2, 1)], id="dmy-two-digit-2000s"),
    pytest.param("01.02.97", [(1997, 2, 1)], id="dmy-two-digit-1900s"),
    pytest.param("1.2.26", [(2026, 2, 1)], id="dmy-pivot-equal"),
    pytest.param("1.2.27", [(1927, 2, 1)], id="dmy-pivot-above"),
    pytest.param("31.02.2016", [], id="dmy-invalid"),
    pytest.param("13.15.2020", [], id="dmy-both-impossible"),
    pytest.param("32.01.2020", [], id="dmy-day-too-big"),
    pytest.param("05.00.2020", [], id="dmy-month-zero"),
    pytest.param("5.1x.2020", [], id="dmy-month-nondigit"),
    pytest.param("1/2/999", [(999, 2, 1)], id="dmy-three-digit-year"),
    pytest.param("1/2/20205", [], id="dmy-five-digit-year"),
    pytest.param("12-05-2020", [(2020, 5, 12)], id="dmy-hyphen"),
    pytest.param("1-2-3", [], id="dmy-one-year-digit"),
    pytest.param("2016-12-19", [(2016, 12, 19)], id="iso-day-19"),
    pytest.param("2016-12-1/9", [(2016, 12, 1)], id="iso-day2-sub-zero-second"),
    pytest.param("20161299", [], id="compact-eight-bad-day"),
    pytest.param("2016900", [], id="compact-seven-narrow-bad-day"),
    pytest.param("199010", [], id="compact-six-bad-day"),
    pytest.param("2016-2-30 real 2018-04-05", [(2018, 4, 5)], id="iso-then-invalid-first"),
    pytest.param("", [], id="empty"),
    pytest.param("   ", [], id="whitespace"),
    pytest.param("no date here", [], id="no-date"),
    pytest.param("Published July 4, 2016 today.", [(2016, 7, 4)], id="english-month-first"),
    pytest.param("Am 4. Juli 2016 hier.", [(2016, 7, 4)], id="german-day-first"),
    pytest.param("Le 4 juillet 2016 ici.", [(2016, 7, 4)], id="french-day-first"),
    pytest.param("Il 4 luglio 2016 qui.", [(2016, 7, 4)], id="italian-day-first"),
    pytest.param("Posted 4th of July 2016.", [(2016, 7, 4)], id="english-ordinal-of"),
    pytest.param("25th December 2020", [(2020, 12, 25)], id="day-first-ordinal"),
    # The date grammar's inter-token whitespace is the Perl \s the ported regex used, which keeps the
    # vertical tab (0x0B) that HTML "ASCII whitespace" drops; a VT between tokens still separates them.
    pytest.param("25th\x0bDecember\x0b2020", [(2020, 12, 25)], id="vertical-tab-separated"),
    pytest.param("July 4th 2016", [(2016, 7, 4)], id="month-first-ordinal"),
    pytest.param("1st January 2000", [(2000, 1, 1)], id="ordinal-st"),
    pytest.param("22nd March 2020", [(2020, 3, 22)], id="ordinal-nd"),
    pytest.param("3rd April 2019", [(2019, 4, 3)], id="ordinal-rd"),
    pytest.param("July 4th", [], id="ordinal-then-end"),
    pytest.param("5 of", [], id="of-then-end"),
    pytest.param("December 25, 2020", [(2020, 12, 25)], id="month-first-two-digit-day"),
    pytest.param("Jan. 5, 2019", [(2019, 1, 5)], id="month-first-dot"),
    pytest.param("Février 3, 2015", [(2015, 2, 3)], id="accented-french"),
    pytest.param("AOÛT 15, 2018", [(2018, 8, 15)], id="accented-upper-august"),
    pytest.param("märz 7 2011", [(2011, 3, 7)], id="accented-german-lower"),
    pytest.param("MÄRZ 7 2011", [(2011, 3, 7)], id="accented-german-upper"),
    pytest.param("3 décembre 2011", [(2011, 12, 3)], id="accented-december"),
    pytest.param("enero 1 2020", [(2020, 1, 1)], id="spanish"),
    pytest.param("gennaio 1 2020", [(2020, 1, 1)], id="italian-name"),
    pytest.param("Sept 9, 2021", [], id="unsupported-sept-abbrev"),
    pytest.param("September 9, 2021", [(2021, 9, 9)], id="full-september"),
    pytest.param("July 39, 2016", [], id="invalid-written-day"),
    pytest.param("July 0, 2016", [], id="written-day-zero"),
    pytest.param("July 4, 1899", [], id="written-year-out-of-range"),
    pytest.param("januaryfoo 5 2020", [], id="longer-name-then-fail"),
    pytest.param("Seen 2014-01-01 once and 2018-01-01 once.", [(2014, 1, 1), (2018, 1, 1)], id="modal-two-dates"),
    pytest.param("Impossible 2016-02-30 but real 2018-04-05.", [(2018, 4, 5)], id="invalid-iso-then-valid"),
    pytest.param("European 08.05.2012 date in prose.", [(2012, 5, 8)], id="dmy-in-prose"),
    pytest.param("July\t4\t2016", [(2016, 7, 4)], id="tab-separated"),
    pytest.param("July\x01 4 2016", [], id="control-char-in-gap"),
    pytest.param("\u00d7 July 4 2016", [(2016, 7, 4)], id="mult-sign-before-month"),
    pytest.param("July ! 2016", [], id="month-then-nonday"),
    pytest.param("July", [], id="month-at-end"),
    pytest.param("July ", [], id="month-space-at-end"),
    pytest.param("5 May 2016", [(2016, 5, 5)], id="day-first-single"),
    pytest.param("12 May 2016", [(2016, 5, 12)], id="day-first-two-digit"),
    pytest.param("5 x 2016", [], id="day-first-then-nonmonth"),
    pytest.param("abc 5", [], id="day-at-end"),
    pytest.param("3 May 2016", [(2016, 5, 3)], id="of-not-consumed"),
    pytest.param("5 Jan. 2019", [(2019, 1, 5)], id="day-first-month-dot"),
    pytest.param("5 January, 2019", [(2019, 1, 5)], id="day-first-month-comma"),
    pytest.param("5 Jan2019", [], id="day-first-no-space"),
    pytest.param("5 Jan 1899", [], id="day-first-year-out-of-range"),
    pytest.param("July 5s 2016", [], id="ordinal-s-not-t"),
    pytest.param("July 5nz 2016", [], id="ordinal-n-not-d"),
    pytest.param("July 5rz 2016", [], id="ordinal-r-not-d"),
    pytest.param("July 5tz 2016", [], id="ordinal-t-not-h"),
    pytest.param("5 ofx Feb 2019", [], id="of-not-space"),
    pytest.param("5 Jan", [], id="day-first-month-at-end"),
]

_URL_CASES = [
    pytest.param("http://x.com/2016/12/23/post.html", (2016, 12, 23), id="url-slash"),
    pytest.param("http://x.com/2020_06_07/story", (2020, 6, 7), id="url-underscore"),
    pytest.param("http://x.com/2020-06-07/story", (2020, 6, 7), id="url-hyphen"),
    pytest.param("http://x.com/2020/6/7/", (2020, 6, 7), id="url-single-digit"),
    pytest.param("http://x.com/2020_6_7/", (2020, 6, 7), id="url-underscore-single"),
    pytest.param("http://x.com/2020-6-7/", (2020, 6, 7), id="url-hyphen-single"),
    pytest.param("http://x.com/2020/06/23", (2020, 6, 23), id="url-two-digit-at-end"),
    pytest.param("http://x.com/2020/06/078", None, id="url-two-digit-then-digit"),
    pytest.param("http://x.com/2020/6/7", (2020, 6, 7), id="url-single-at-end"),
    pytest.param("http://x.com/2020/06/07", (2020, 6, 7), id="url-two-digit-day-at-end"),
    pytest.param("http://x.com/2020_06_07", (2020, 6, 7), id="url-no-trailer-underscore"),
    pytest.param("http://x.com/2020-06-07", (2020, 6, 7), id="url-no-trailer-hyphen"),
    pytest.param("http://x.com/story", None, id="url-no-date"),
    pytest.param("http://x.com/12020/06/07/", None, id="url-preceded-by-digit"),
    pytest.param("http://x.com/2020/13/07/", None, id="url-bad-month"),
    pytest.param("http://x.com/2020/13/1", None, id="url-bad-month-single"),
    pytest.param("http://x.com/2020/6-7", (2020, 6, 7), id="url-mixed-sep"),
    pytest.param("http://x.com/2020/6/78", None, id="url-day-followed-by-digit"),
    pytest.param("http://x.com/2020/02/30/", None, id="url-invalid-calendar"),
    pytest.param("http://x.com/2020", None, id="url-year-at-end"),
    pytest.param("http://x.com/2020/6", None, id="url-month-at-end"),
    pytest.param("http://x.com/2020/x/07/", None, id="url-month-nondigit"),
    pytest.param("http://x.com/2020/6x7", None, id="url-month-nonsep"),
    pytest.param("", None, id="url-empty"),
    pytest.param("http://x.com/a/2018/04/05/b", (2018, 4, 5), id="url-embedded"),
    pytest.param("http://x.com/2020xx", None, id="url-year-then-nonsep"),
    pytest.param("http://x.com/2020/06x07", None, id="url-month-then-nonsep"),
    pytest.param("http://x.com/2020/06/", None, id="url-trailing-slash-no-day"),
]


@pytest.mark.parametrize(("text", "expected"), _SCAN_CASES)
def test_date_scan_reads_the_first_numeric_date(text: str, expected: tuple[int, int, int] | None) -> None:
    assert _date_scan(text, _YEAR) == expected


@pytest.mark.parametrize(("text", "expected"), _SCAN_ALL_CASES)
def test_date_scan_all_reads_every_date(text: str, expected: list[tuple[int, int, int]]) -> None:
    assert _date_scan_all(text, _YEAR) == expected


@pytest.mark.parametrize(("url", "expected"), _URL_CASES)
def test_date_url_reads_the_path_date(url: str, expected: tuple[int, int, int] | None) -> None:
    assert _date_url(url) == expected


_WIDE_STORAGE = [
    pytest.param("★", id="ucs2"),
    pytest.param("\U0001f600", id="ucs4"),
]
"""A BMP star and an astral emoji force the argument to 2- and 4-byte storage; the plain-ASCII
cases only exercise the 1-byte code-point read. Re-running them at each width pins that a date
reads the same whatever storage the surrounding text forces."""


@pytest.mark.parametrize("wide", _WIDE_STORAGE)
@pytest.mark.parametrize(("text", "expected"), _SCAN_CASES)
def test_date_scan_is_storage_width_agnostic(text: str, expected: tuple[int, int, int] | None, wide: str) -> None:
    assert _date_scan(text + wide, _YEAR) == expected


@pytest.mark.parametrize("wide", _WIDE_STORAGE)
@pytest.mark.parametrize(("text", "expected"), _SCAN_ALL_CASES)
def test_date_scan_all_is_storage_width_agnostic(text: str, expected: list[tuple[int, int, int]], wide: str) -> None:
    assert _date_scan_all(text + wide, _YEAR) == expected


@pytest.mark.parametrize("wide", _WIDE_STORAGE)
@pytest.mark.parametrize(("url", "expected"), _URL_CASES)
def test_date_url_is_storage_width_agnostic(url: str, expected: tuple[int, int, int] | None, wide: str) -> None:
    assert _date_url(url + wide) == expected


def test_date_url_rejects_non_str() -> None:
    with pytest.raises(TypeError):
        _date_url(123)  # ty: ignore[invalid-argument-type]  # non-str on purpose


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda: _date_scan(123, _YEAR), id="date_scan"),  # ty: ignore[invalid-argument-type]  # non-str
        pytest.param(lambda: _date_scan_all(123, _YEAR), id="date_scan_all"),  # ty: ignore[invalid-argument-type]  # non-str
    ],
)
def test_scan_hooks_reject_non_str(call: Callable[[], object]) -> None:
    with pytest.raises(TypeError):
        call()


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
