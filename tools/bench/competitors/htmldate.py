"""htmldate: the standalone publication-date finder turbohtml.extract.dates replaces."""

from __future__ import annotations

import htmldate

REQUIREMENTS = ("htmldate>=1.10",)


def date(text: str) -> None:
    """Find the publication date with htmldate, parsing the page and scoring its date signals."""
    htmldate.find_date(text)


OPERATIONS = {"date": (date, "htmldate")}
