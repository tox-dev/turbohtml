"""
Publication-date extraction for :mod:`turbohtml.extract`, the ``htmldate.find_date`` counterpart.

:func:`dates` reads the same signals ``htmldate`` reads -- ``<meta>`` publication/modification tags, JSON-LD
``datePublished``/``dateModified``, ``<time>`` elements, a date pattern in the canonical URL, and (as a last resort)
visible text -- but off the parsed DOM and the :meth:`~turbohtml.Document.structured_data` engine rather than a
second parse. Each signal is a stage tried in htmldate's priority order; the first stage that yields a bounded date
wins, and within a stage the :class:`DateExtraction` ``original`` flag routes a publication date against a
modification date. The stages and the date parsing run in C (``Document._dates``); this module holds the options,
the result record, and the window and output formatting the :mod:`datetime` module owns. The date parsing needs no
``dateparser``: ISO 8601, the common numeric spellings, an 8-digit stamp, and a compact multilingual month vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Final, Literal, NamedTuple

from turbohtml._html import parse

__all__ = [
    "DateExtraction",
    "PublicationDate",
    "dates",
]

Signal = Literal["url", "meta", "json-ld", "time", "text"]
"""Which engine a :class:`PublicationDate` came from, in htmldate's stage-priority order."""

_EARLIEST: Final = date(1995, 1, 1)
"""The default lower bound, htmldate's ``MIN_DATE``: the web's date metadata does not predate it."""


class PublicationDate(NamedTuple):
    """
    A date :func:`dates` recovered and the signal it read it from.

    ``date`` is the formatted string (``output_format``, ISO ``YYYY-MM-DD`` by default). ``signal`` names the engine:
    ``"meta"`` a publication/modification ``<meta>`` tag, ``"json-ld"`` a JSON-LD ``datePublished``/``dateModified``,
    ``"time"`` a ``<time>`` element, ``"url"`` a date pattern in the canonical URL, ``"text"`` visible page text.
    """

    date: str
    signal: Signal


@dataclass(frozen=True, slots=True)
class DateExtraction:
    """
    Options for :func:`dates`, mirroring ``htmldate.find_date``'s knobs.

    :param original: prefer the first-published date over the last-modified one, ``htmldate``'s ``original_date``.
        The default (``False``) prefers the modification date, the most recent the page reports.
    :param output_format: an :meth:`~datetime.date.strftime` format for the returned string; the default is
        ISO ``%Y-%m-%d``.
    :param min_date: the earliest acceptable date; a candidate before it is skipped. Defaults to 1995-01-01, the floor
        ``htmldate`` uses.
    :param max_date: the latest acceptable date; a candidate after it is skipped. Defaults to today, so a stray future
        stamp never wins.
    :param extensive_search: scan visible page text when no metadata carries a date, ``htmldate``'s
        ``extensive_search``. With ``False`` only the structured signals (meta, JSON-LD, time, URL) are read.
    """

    original: bool = False
    output_format: str = "%Y-%m-%d"
    min_date: date | None = None
    max_date: date | None = None
    extensive_search: bool = True

    def __post_init__(self) -> None:
        """Reject a min/max window whose bounds cross, the one contradiction the fields can express."""
        if self.min_date is not None and self.max_date is not None and self.min_date > self.max_date:
            msg = f"min_date {self.min_date.isoformat()} is after max_date {self.max_date.isoformat()}"
            raise ValueError(msg)


_DEFAULT: Final = DateExtraction()


def dates(html: str, options: DateExtraction | None = None, /) -> PublicationDate | None:
    """
    Find a document's publication (or, by default, modification) date, the ``htmldate.find_date`` counterpart.

    The signals are tried in htmldate's order -- a date in the canonical URL, then publication/modification
    ``<meta>`` tags, then JSON-LD, then ``<time>`` elements, then (with ``extensive_search``) visible text -- and the
    first that yields a date inside the ``[min_date, max_date]`` window wins. Within a stage the ``original`` flag
    picks a publication date over a modification date, or the reverse.

    :param html: the page markup.
    :param options: the extraction knobs; defaults to :class:`DateExtraction` (modification date, ISO output, the
        1995-to-today window, text search on).
    :returns: the date and the signal it came from, or ``None`` when no bounded date is found.
    """
    active = options or _DEFAULT
    today = _today()
    earliest = active.min_date or _EARLIEST
    latest = active.max_date or today
    found = parse(html)._dates(  # ruff:ignore[private-member-access]  # the Document type's private C entry point
        1 if active.original else 2,
        today.year,
        earliest.year,
        earliest.month,
        earliest.day,
        latest.year,
        latest.month,
        latest.day,
        active.extensive_search,
    )
    if found is None:
        return None
    year, month, day, signal = found
    return PublicationDate(date(year, month, day).strftime(active.output_format), signal)


def _today() -> date:
    """Today's date in UTC, the default upper bound so a stray future stamp never wins."""
    return datetime.now(timezone.utc).date()
