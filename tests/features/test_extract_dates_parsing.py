"""Date-string parsing inside :func:`turbohtml.extract.dates`, driven through a single ``<meta>`` value."""

from __future__ import annotations

import pytest

from turbohtml import dates


def _page(value: str) -> str:
    return f"<head><meta name=date content='{value}'></head>"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("2021-05-12", "2021-05-12", id="iso-date"),
        pytest.param("2019-07-08T10:00:00Z", "2019-07-08", id="iso-datetime"),
        pytest.param("2021-9-8", "2021-09-08", id="numeric-ymd-unpadded"),
        pytest.param("2021/05/12", "2021-05-12", id="numeric-ymd-slash"),
        pytest.param("13/05/2021", "2021-05-13", id="numeric-dmy-day-first"),
        pytest.param("05/25/2021", "2021-05-25", id="numeric-mdy-fallback"),
        pytest.param("20210512", "2021-05-12", id="compact-yyyymmdd"),
        pytest.param("May 6, 2024", "2024-05-06", id="text-month-day-year"),
        pytest.param("6 May 2024", "2024-05-06", id="text-day-month-year"),
    ],
)
def test_parse_formats(value: str, expected: str) -> None:
    assert dates(_page(value)) == expected


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("", id="empty"),
        pytest.param("   ", id="whitespace"),
        pytest.param("2021-02-30", id="impossible-iso-day"),
        pytest.param("not a date at all", id="prose"),
        pytest.param("99999999", id="compact-out-of-range"),
        pytest.param("32/13/2021", id="numeric-both-orders-invalid"),
    ],
)
def test_unparseable_values_yield_no_date(value: str) -> None:
    assert dates(_page(value)) is None
