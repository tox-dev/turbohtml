"""
turbohtml.extract: pull content and data out of HTML.

Home for the extraction result types and (as the campaign lands) the content/date/url helpers that replace
``trafilatura``/``readability-lxml``/``newspaper3k``/``goose3``/``justext``/``htmldate``/``courlan``/``extruct``.
The extraction itself runs through the node methods (``node.article()``, ``node.links()``, ``node.tables()``,
``node.structured_data()``); the records they return are re-exported here for discoverability and also stay
importable from the package root.

:func:`dates` is the standalone publication-date entry point that replaces ``htmldate.find_date``. It reuses the same
declared-metadata scoring as :meth:`~turbohtml.Node.article` -- the ``<time>``, ``article:published_time`` meta, and
common date metas the article harvester already reads -- and layers JSON-LD, last-modified metas, and URL-pattern dates
on top so a single call answers *when was this published* without inferring a date the page never declared.
"""

from __future__ import annotations

import re
from contextlib import suppress
from dataclasses import dataclass, fields
from datetime import date, datetime
from typing import TYPE_CHECKING

from ._article import Article
from ._html import Document, parse
from ._links import Link
from ._structured_data import MicrodataItem, StructuredData

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ._structured_data import JSONValue


@dataclass(frozen=True)
class DateExtraction:
    """
    How :func:`dates` chooses and formats a document's publication date.

    Build one and reuse it across threads; every field has a default, so ``DateExtraction()`` reproduces the
    no-argument call. The knobs mirror ``htmldate.find_date`` -- ``original`` is its ``original_date``,
    ``output_format`` its ``outputformat``, and ``min_date``/``max_date`` its window -- bundled into one config the way
    :class:`~turbohtml.Markdown` and :class:`~turbohtml.clean.Policy` bundle theirs.

    :param original: prefer the first-published date over the last-modified date, the way htmldate's ``original_date``
        does. ``False`` (the default) returns the most recent date the page declares, ``True`` the earliest.
    :param output_format: the :meth:`~datetime.date.strftime` pattern the returned string is rendered with.
    :param min_date: drop any candidate earlier than this date, or ``None`` to leave the lower bound open.
    :param max_date: drop any candidate later than this date, or ``None`` to leave the upper bound open.
    :param extensive_search: also mine the canonical/OpenGraph URL for a ``/YYYY/MM/DD/`` date when the markup declares
        none; turning it off keeps only the structured ``<meta>``, JSON-LD, and ``<time>`` signals.
    """

    original: bool = False
    output_format: str = "%Y-%m-%d"
    min_date: date | None = None
    max_date: date | None = None
    extensive_search: bool = True

    def __post_init__(self) -> None:
        """Reject an empty validity window up front, so the search never has to."""
        if self.min_date is not None and self.max_date is not None and self.min_date > self.max_date:
            msg = "min_date must not be after max_date"
            raise ValueError(msg)

    @classmethod
    def published(cls) -> DateExtraction:
        """
        Prefer the original publication date over later modifications, htmldate's ``original_date=True`` mode.

        :returns: a config that returns the earliest declared date.
        """
        return cls(original=True)

    @classmethod
    def fast(cls) -> DateExtraction:
        """
        Skip the URL-pattern fallback and read only the structured ``<meta>``/JSON-LD/``<time>`` signals.

        :returns: a config that never inspects the page URL.
        """
        return cls(extensive_search=False)

    def _unpack(self) -> dict[str, object]:
        """Flatten to the search's keyword names, emitting only values that differ from its defaults."""
        default = _DATE_DEFAULT
        return {
            field_.name: value
            for field_ in fields(self)
            if (value := getattr(self, field_.name)) != getattr(default, field_.name)
        }


_DATE_DEFAULT = DateExtraction()

_PUBLISHED_META = frozenset({
    "article:published_time",
    "og:article:published_time",
    "og:published_time",
    "published_time",
    "datepublished",
    "date",
    "pubdate",
    "publishdate",
    "publish-date",
    "dc.date",
    "dc.date.issued",
    "dcterms.date",
    "dcterms.created",
    "sailthru.date",
    "parsely-pub-date",
    "article:published",
    "rnews:datepublished",
    "timestamp",
})
_MODIFIED_META = frozenset({
    "article:modified_time",
    "og:updated_time",
    "og:article:modified_time",
    "datemodified",
    "dc.date.modified",
    "dcterms.modified",
    "lastmod",
    "last-modified",
    "revised",
})
_JSON_PUBLISHED = frozenset({"datepublished", "datecreated", "uploaddate"})
_JSON_MODIFIED = frozenset({"datemodified"})

_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_YMD = re.compile(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})")
_DMY = re.compile(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})")
_COMPACT = re.compile(r"\b(\d{8})\b")
_URL_DMY = re.compile(r"/(\d{4})/(\d{1,2})/(\d{1,2})(?:/|\b)")
_URL_YM = re.compile(r"/(\d{4})/(\d{1,2})/")
_TEXT_FORMATS = ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y", "%B %d %Y")


def dates(html: str | bytes, options: DateExtraction | None = None) -> str | None:
    """
    Extract a document's publication date, the standalone replacement for ``htmldate.find_date``.

    The date is read from the markup the page declares -- ``<meta>`` tags (``article:published_time`` and its kin),
    JSON-LD ``datePublished``/``dateModified``, ``<time>`` elements, and (when ``extensive_search`` is on) a
    ``/YYYY/MM/DD/`` date in the canonical or OpenGraph URL -- and validated against the configured window. It reuses
    the same declared-metadata path as :meth:`~turbohtml.Node.article`, never inferring a date from prose.

    :param html: the page markup, the same ``str`` or ``bytes`` :func:`turbohtml.parse` accepts.
    :param options: a :class:`DateExtraction` config, or ``None`` for the default (most-recent date, ``%Y-%m-%d``).
    :returns: the chosen date rendered with ``output_format``, or ``None`` when the page declares no valid date.
    """
    config = options if options is not None else _DATE_DEFAULT
    return _find_date(parse(html), config)


def _find_date(document: Document, config: DateExtraction) -> str | None:
    """Gather every declared date, drop the out-of-range ones, and return the earliest or latest as a string."""
    published: list[date] = []
    modified: list[date] = []
    generic: list[date] = []
    _collect_meta(document, published, modified)
    _collect_json_ld(document.json_ld(), published, modified)
    _collect_time(document, published, modified, generic)
    if (article_date := document.article().date) is not None:
        _add_text(generic, article_date)
    if config.extensive_search:
        _collect_url(document, published)
    pools = (published, generic, modified) if config.original else (modified, published, generic)
    pick = min if config.original else max
    for pool in pools:
        if bounded := [value for value in pool if _within(value, config.min_date, config.max_date)]:
            return pick(bounded).strftime(config.output_format)
    return None


def _collect_meta(document: Document, published: list[date], modified: list[date]) -> None:
    """Read every ``<meta>`` whose name/property/itemprop is a known publish or modify key into its pool."""
    for meta in document.find_all("meta"):
        key = meta.attr("property") or meta.attr("name") or meta.attr("itemprop")
        content = meta.attr("content")
        if key is None or content is None:
            continue
        lowered = key.strip().lower()
        if lowered in _PUBLISHED_META:
            _add_text(published, content)
        elif lowered in _MODIFIED_META:
            _add_text(modified, content)


def _collect_json_ld(values: list[JSONValue], published: list[date], modified: list[date]) -> None:
    """Walk the decoded JSON-LD blocks, routing every ``datePublished``/``dateModified`` string to its pool."""
    stack: list[JSONValue] = list(values)
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                lowered = key.lower()
                if isinstance(value, str) and lowered in _JSON_PUBLISHED:
                    _add_text(published, value)
                elif isinstance(value, str) and lowered in _JSON_MODIFIED:
                    _add_text(modified, value)
                elif isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(node, list):
            stack.extend(node)


def _collect_time(document: Document, published: list[date], modified: list[date], generic: list[date]) -> None:
    """Read every ``<time>`` element, routing it by its ``pubdate``/``itemprop`` hint to the matching pool."""
    for element in document.find_all("time"):
        value = element.attr("datetime") or element.text
        itemprop = (element.attr("itemprop") or "").strip().lower()
        if element.attr("pubdate") is not None or itemprop == "datepublished":
            _add_text(published, value)
        elif itemprop == "datemodified":
            _add_text(modified, value)
        else:
            _add_text(generic, value)


def _collect_url(document: Document, published: list[date]) -> None:
    """Mine the canonical and OpenGraph URLs for a ``/YYYY/MM/DD/`` (or ``/YYYY/MM/``) publication date."""
    published.extend(found for url in _candidate_urls(document) if (found := _date_from_url(url)) is not None)


def _candidate_urls(document: Document) -> Iterable[str]:
    """Yield the document's own URLs: the ``rel=canonical`` link href and the ``og:url`` meta content."""
    for link in document.find_all("link"):
        rel = link.attr("rel")
        href = link.attr("href")
        if rel is not None and href is not None and "canonical" in rel.lower().split():
            yield href
    for meta in document.find_all("meta"):
        if meta.attr("property") == "og:url" and (content := meta.attr("content")) is not None:
            yield content


def _date_from_url(url: str) -> date | None:
    """Pull a date out of a ``/YYYY/MM/DD/`` or ``/YYYY/MM/`` URL path, the latter dated to the first of the month."""
    if (match := _URL_DMY.search(url)) is not None:
        return _safe_date(int(match[1]), int(match[2]), int(match[3]))
    if (match := _URL_YM.search(url)) is not None:
        return _safe_date(int(match[1]), int(match[2]), 1)
    return None


def _add_text(pool: list[date], text: str) -> None:
    """Parse ``text`` to a date and append it to ``pool`` when it reads as one."""
    if (parsed := _parse_date(text)) is not None:
        pool.append(parsed)


def _parse_date(value: str) -> date | None:
    """Parse one declared date string -- ISO, slash/dot numeric, compact, or month-name -- to a :class:`date`."""
    text = value.strip()
    if not text:
        return None
    if _ISO_DATE.fullmatch(text[:10]):
        with suppress(ValueError):
            return date.fromisoformat(text[:10])
        return None
    return _parse_numeric(text) or _parse_textual(text)


def _parse_numeric(text: str) -> date | None:
    """Parse a numeric date: ``YYYY-MM-DD`` order, day-first (then month-first) ``DD/MM/YYYY``, or compact 8-digit."""
    if (match := _YMD.search(text)) is not None:
        return _safe_date(int(match[1]), int(match[2]), int(match[3]))
    if (match := _DMY.search(text)) is not None:
        year, first, second = int(match[3]), int(match[1]), int(match[2])
        return _safe_date(year, second, first) or _safe_date(year, first, second)
    if (match := _COMPACT.search(text)) is not None:
        digits = match[1]
        return _safe_date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
    return None


def _parse_textual(text: str) -> date | None:
    """Parse a month-name date (``May 6, 2024`` / ``6 May 2024``) by trying each known textual format in turn."""
    for text_format in _TEXT_FORMATS:
        with suppress(ValueError):
            return datetime.strptime(text, text_format).date()  # noqa: DTZ007  # a calendar date, no time zone
    return None


def _safe_date(year: int, month: int, day: int) -> date | None:
    """Build a :class:`date`, returning ``None`` when the components do not form a real calendar day."""
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _within(value: date, min_date: date | None, max_date: date | None) -> bool:
    """Report whether ``value`` falls inside the (possibly open-ended) validity window."""
    return (min_date is None or value >= min_date) and (max_date is None or value <= max_date)


__all__ = [
    "Article",
    "DateExtraction",
    "Link",
    "MicrodataItem",
    "StructuredData",
    "dates",
]
