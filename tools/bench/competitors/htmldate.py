"""htmldate: the trafilatura-companion library whose ``find_date`` turbohtml.dates replaces."""

from __future__ import annotations

from htmldate import find_date

REQUIREMENTS = ("htmldate>=1.9",)


def date(text: str) -> None:
    """Extract the publication date with htmldate, scanning the page for declared and inferred dates."""
    find_date(text)


OPERATIONS = {"date": (date, "htmldate")}
