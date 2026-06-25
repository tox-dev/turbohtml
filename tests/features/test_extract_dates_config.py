"""The :class:`turbohtml.DateExtraction` config: presets, validation, formatting, and the validity window."""

from __future__ import annotations

from datetime import date

import pytest

from turbohtml import DateExtraction, dates

_TWO_DATES = (
    "<head><meta property=article:published_time content=2020-01-01>"
    "<meta property=article:modified_time content=2022-03-04></head>"
)


def test_default_is_most_recent_iso() -> None:
    config = DateExtraction()
    assert config.original is False
    assert config.output_format == "%Y-%m-%d"
    assert config.extensive_search is True


def test_published_preset_sets_original() -> None:
    assert DateExtraction.published() == DateExtraction(original=True)


def test_fast_preset_disables_extensive_search() -> None:
    assert DateExtraction.fast() == DateExtraction(extensive_search=False)


def test_fast_preset_skips_url_dates() -> None:
    html = "<link rel=canonical href=https://x.test/2021/09/14/post>"
    assert dates(html, DateExtraction.fast()) is None


def test_output_format_is_applied() -> None:
    assert dates(_TWO_DATES, DateExtraction(output_format="%d.%m.%Y")) == "04.03.2022"


def test_min_date_drops_earlier_candidates() -> None:
    config = DateExtraction(original=True, min_date=date(2021, 1, 1))
    assert dates(_TWO_DATES, config) == "2022-03-04"


def test_max_date_drops_later_candidates() -> None:
    config = DateExtraction(max_date=date(2021, 1, 1))
    assert dates(_TWO_DATES, config) == "2020-01-01"


def test_window_can_drop_every_candidate() -> None:
    config = DateExtraction(min_date=date(2030, 1, 1))
    assert dates(_TWO_DATES, config) is None


def test_inverted_window_is_rejected() -> None:
    with pytest.raises(ValueError, match="min_date must not be after max_date"):
        DateExtraction(min_date=date(2022, 1, 1), max_date=date(2020, 1, 1))


def test_equal_window_bounds_are_allowed() -> None:
    bound = date(2020, 1, 1)
    config = DateExtraction(min_date=bound, max_date=bound)
    assert config.min_date == config.max_date


def test_unpack_emits_only_overrides() -> None:
    assert DateExtraction()._unpack() == {}
    assert DateExtraction(original=True)._unpack() == {"original": True}
