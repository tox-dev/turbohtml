"""htmldate: the standalone publication-date finder turbohtml.extract.dates replaces."""

from __future__ import annotations

import htmldate

REQUIREMENTS = ("htmldate>=1.10",)


def date(text: str) -> None:
    # Find the publication date with htmldate, parsing the page and scoring its date signals.
    htmldate.find_date(text)


def date_tally(text: str) -> str | None:
    # Prefer the latest date to match turbohtml's default scoring policy.
    return htmldate.find_date(text, original_date=False)


OPERATIONS = {"date": (date, "htmldate"), "date-tally": (date_tally, "htmldate")}
