"""
Publication-date extraction for :mod:`turbohtml.extract`, the ``htmldate.find_date`` counterpart.

:func:`dates` reads the same signals ``htmldate`` reads -- ``<meta>`` publication/modification tags, JSON-LD
``datePublished``/``dateModified``, ``<time>`` elements, a date pattern in the canonical URL, and (as a last resort)
visible text -- but off the parsed DOM and the :meth:`~turbohtml.Document.structured_data` engine rather than a
second parse. Each signal is a stage tried in htmldate's priority order; the first stage that yields a bounded date
wins, and within a stage the :class:`DateExtraction` ``original`` flag routes a publication date against a
modification date. The date parsing is standard-library only (no ``dateparser`` dependency): ISO 8601, the common
numeric spellings, an 8-digit stamp, and a compact multilingual month vocabulary.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from itertools import starmap
from typing import TYPE_CHECKING, Final, Literal, NamedTuple

from ._html import Document, _date_scan, _date_scan_all, _date_url, parse

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "DateExtraction",
    "PublicationDate",
    "dates",
]

Signal = Literal["url", "meta", "json-ld", "time", "text"]
"""Which engine a :class:`PublicationDate` came from, in htmldate's stage-priority order."""

_Role = Literal["published", "modified", "generic"]

_PUBLISHED: Final[_Role] = "published"
_MODIFIED: Final[_Role] = "modified"
_GENERIC: Final[_Role] = "generic"

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

_PUBLISHED_KEYS: Final[frozenset[str]] = frozenset({
    *("article.created", "article.published", "article:published", "article:published_time", "article_date_original"),
    *("bt:pubdate", "citation_date", "citation_publication_date", "created", "date", "date_published", "datecreated"),
    *("dateposted", "datepublished", "dc.date", "dc.date.created", "dc.date.issued", "dc.date.publication"),
    *("dcterms.created", "dcterms.date", "dcterms.issued", "og:article:published_time", "og:published_time"),
    *("og:pubdate", "parsely-pub-date", "pdate", "pubdate", "publish-date", "publish_date", "published_date"),
    *("published_time", "publisheddate", "publication_date", "rnews:datepublished", "sailthru.date", "timestamp"),
    *("datecreated", "datepublished", "pubyear"),  # itemprop spellings
})
"""Meta ``name``/``property``/``itemprop`` keys that mark a publication date, drawn from htmldate's vocabulary."""

_MODIFIED_KEYS: Final[frozenset[str]] = frozenset({
    *("article:modified", "article:modified_time", "article:post_modified", "datemodified", "dc.modified"),
    *("dcterms.modified", "lastdate", "lastmod", "lastmodified", "last-modified", "modified", "modified_time"),
    *("modificationdate", "og:article:modified_time", "og:modified_time", "og:updated_time", "revision_date"),
    *("updated_time", "dateupdate"),  # itemprop spelling
})
"""Meta keys that mark a modification date."""

# Elements that tend to carry a date, drawn from htmldate's class/id vocabulary but kept to the high-precision
# markers (skipping its broad 'info'/'author'/'footer' catch-alls, which pull in unrelated dates).
_DATE_MARKUP: Final = ", ".join((
    "time",
    *(f"[class*={token} i]" for token in ("date", "datum", "publish", "posted", "pubdate", "timestamp", "byline")),
    *(f"[class*={token} i]" for token in ("dateline", "entry-date", "updated", "modified", "created")),
    *(f"[id*={token} i]" for token in ("date", "publish", "posted", "timestamp")),
    "[itemprop*=date i]",
))
_PUBLISHED_MARKERS: Final = ("publish", "posted", "pubdate", "entry-date", "dateline", "created", "byline")
_MODIFIED_MARKERS: Final = ("updated", "modified", "lastmod", "revised")


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
    document = parse(html)
    window = _Window(active.min_date or _EARLIEST, active.max_date or _today())
    want = _PUBLISHED if active.original else _MODIFIED
    stages: list[tuple[Signal, Callable[[], list[tuple[_Role, date]]]]] = [
        ("url", lambda: _url_candidates(document)),
        ("meta", lambda: _meta_candidates(document)),
        ("json-ld", lambda: _json_candidates(document)),
        ("time", lambda: _time_candidates(document)),
    ]
    if active.extensive_search:  # the whole-body text scan is the slow path; only reach it when the markup is silent
        stages.append(("text", lambda: _text_candidates(document, window, original=active.original)))
    for signal, produce in stages:
        if found := _pick(produce(), want, window, active.output_format, signal):
            return found
    return None


class _Window(NamedTuple):
    """The inclusive date bounds a candidate must fall within to be accepted."""

    earliest: date
    latest: date

    def holds(self, moment: date) -> bool:
        """Whether the moment lies within the window."""
        return self.earliest <= moment <= self.latest


def _pick(
    candidates: list[tuple[_Role, date]], want: _Role, window: _Window, output_format: str, signal: Signal
) -> PublicationDate | None:
    """Return the wanted-role (or generic) candidate a stage offers, falling back to the first off-role one."""
    reserve: PublicationDate | None = None
    for role, moment in candidates:
        if not window.holds(moment):
            continue
        found = PublicationDate(moment.strftime(output_format), signal)
        if role in {want, _GENERIC}:
            return found
        reserve = reserve or found
    return reserve


def _url_candidates(document: Document) -> list[tuple[_Role, date]]:
    """Read a date pattern from the canonical link or ``og:url``, the fastest and most trustworthy signal."""
    canonical = document.find("link", attrs={"rel": "canonical"})
    sources = (canonical.attrs.get("href") if canonical else None, _meta_value(document, "og:url"))
    return [(_GENERIC, moment) for url in sources if isinstance(url, str) and (moment := _url_date(url))]


def _url_date(url: str) -> date | None:
    """Extract the ``/YYYY/MM/DD/`` date a URL path often carries."""
    parts = _date_url(url)
    return date(*parts) if parts else None


def _meta_candidates(document: Document) -> list[tuple[_Role, date]]:
    """Publication and modification dates from ``<meta>`` tags, keyed by name/property/itemprop/http-equiv/pubdate."""
    found: list[tuple[_Role, date]] = []
    for element in document.find_all("meta"):
        attributes = element.attrs
        content = attributes.get("content") or attributes.get("datetime")
        if not isinstance(content, str):
            continue
        keys = [attributes.get(name) for name in ("name", "property", "itemprop", "http-equiv")]
        if str(attributes.get("pubdate")).lower() == "pubdate":
            keys.append("pubdate")
        if (role := _role_of(keys)) is not None and (moment := _scan(content)):
            found.append((role, moment))
    return found


def _json_candidates(document: Document) -> list[tuple[_Role, date]]:
    """JSON-LD ``datePublished``/``dateModified`` values, from the structured-data engine's decoded blocks."""
    found: list[tuple[_Role, date]] = []
    for block in document.structured_data().json_ld:
        _walk_json(block, found)
    return found


def _time_candidates(document: Document) -> list[tuple[_Role, date]]:
    """
    Dates in temporal HTML markup: ``<time>`` elements and elements whose class/id/itemprop marks them as a date.

    A ``<time datetime>`` is the canonical spelling, but many pages date an article in a ``<span class="published">``
    or ``<p class="entry-date">`` instead, the class/id vocabulary ``htmldate`` scans. Each element contributes the
    date in its ``datetime``/``title`` attribute or its first text date, with a publication/modification role read
    from a ``pubdate`` attribute or an ``updated``/``modified`` marker in its class.
    """
    found: list[tuple[_Role, date]] = []
    for element in document.select(_DATE_MARKUP):
        attributes = element.attrs
        raw = attributes.get("datetime") or attributes.get("title")
        moment = _scan(raw) if isinstance(raw, str) else _first_date(element.text)
        if moment is None:
            continue
        marker = (
            f"{_token(attributes.get('class'))} {_token(attributes.get('id'))} {_token(attributes.get('itemprop'))}"
        )
        if "pubdate" in attributes or any(word in marker for word in _PUBLISHED_MARKERS):
            found.append((_PUBLISHED, moment))
        elif any(word in marker for word in _MODIFIED_MARKERS):
            found.append((_MODIFIED, moment))
        else:
            found.append((_GENERIC, moment))
    return found


def _text_candidates(document: Document, window: _Window, *, original: bool) -> list[tuple[_Role, date]]:
    """
    Recover the extensive last resort: the date that recurs most across the page's visible text.

    Boilerplate pages carry no date metadata, so the publication date is only in the prose -- but so is every comment
    and archive link. The scan reads the whole body and takes the date that repeats most (in the byline, a permalink,
    a caption, the article dateline), the modal reasoning ``htmldate``'s reference scoring rests on. Ties break toward
    the earliest date for ``original`` and the latest otherwise, the publication-vs-modification preference the
    structured stages apply.
    """
    counts = Counter(
        moment for body in document.find_all("body") for moment in _scan_all(body.text) if window.holds(moment)
    )
    if not counts:
        return []
    peak = max(counts.values())
    finalists = [moment for moment, count in counts.items() if count == peak]
    return [(_GENERIC, min(finalists) if original else max(finalists))]


def _role_of(keys: list[str | list[str] | None]) -> _Role | None:
    """Classify a meta element's keys as a publication or modification marker, or neither."""
    lowered = {key.lower() for key in keys if isinstance(key, str)}
    if lowered & _PUBLISHED_KEYS:
        return _PUBLISHED
    if lowered & _MODIFIED_KEYS:
        return _MODIFIED
    return None


def _walk_json(node: object, found: list[tuple[_Role, date]]) -> None:
    """Collect datePublished/dateModified from a JSON-LD node, recursing through @graph and nested objects."""
    if isinstance(node, list):
        for item in node:
            _walk_json(item, found)
    elif isinstance(node, dict):
        for role, key in ((_PUBLISHED, "datePublished"), (_MODIFIED, "dateModified")):
            value = node.get(key)
            if isinstance(value, str) and (moment := _scan(value)):
                found.append((role, moment))
        for value in node.values():
            if isinstance(value, (list, dict)):
                _walk_json(value, found)


def _meta_value(document: Document, key: str) -> str | None:
    """Return the content of the first ``<meta>`` whose name or property equals key."""
    for name in ("property", "name"):
        if (element := document.find("meta", attrs={name: key})) and isinstance(
            content := element.attrs.get("content"), str
        ):
            return content
    return None


def _token(value: str | list[str] | None) -> str:
    """Return the lowercased text of a class/id/itemprop attribute, joining a multi-valued class list."""
    if isinstance(value, list):
        return " ".join(value).lower()
    return value.lower() if isinstance(value, str) else ""


def _first_date(text: str) -> date | None:
    """Return the first date of any spelling in a block of element text, written-out months included."""
    parts = _date_scan_all(text, _today().year)
    return date(*parts[0]) if parts else None


def _scan(text: str) -> date | None:
    """Parse the first numeric date in a metadata string: ISO, an 8-digit stamp, or a day-month-year spelling."""
    parts = _date_scan(text, _today().year)
    return date(*parts) if parts else None


def _scan_all(text: str) -> list[date]:
    """Every ISO, day-month-year, and written-out date in a block of visible text, for frequency scoring."""
    return list(starmap(date, _date_scan_all(text, _today().year)))


def _today() -> date:
    """Today's date in UTC, the default upper bound so a stray future stamp never wins."""
    return datetime.now(timezone.utc).date()
