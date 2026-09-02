"""The Document._dates C entry point: the stage order, the signal it names, and the argument contract."""

from __future__ import annotations

from datetime import date

import pytest

from turbohtml import parse
from turbohtml.extract import DateExtraction, PublicationDate, dates

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
