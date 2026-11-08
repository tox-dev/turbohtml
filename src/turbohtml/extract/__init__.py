"""
turbohtml.extract: pull content and data out of HTML.

Home for the extraction result types and (as the campaign lands) the content/date/url helpers that replace
``trafilatura``/``readability-lxml``/``newspaper3k``/``goose3``/``justext``/``htmldate``/``courlan``/``extruct``.
The extraction itself runs through the node methods (``node.article()``, ``node.links()``, ``node.tables()``,
``node.structured_data()``); the records they return are re-exported here for discoverability and also stay
importable from the package root.

:func:`boilerplate` adds the per-paragraph view those methods do not give: it segments a page into paragraph units
and marks each one good or boilerplate, the call shape ``justext`` and ``boilerpy3`` expose. The classification is a
thin layer over the same C main-content scoring :meth:`turbohtml.Node.main_content` runs, refined per paragraph by
the length and link-density thresholds a frozen :class:`Extraction` config carries.

The URL helpers live here directly: :func:`clean_url`, :func:`normalize_url`, and :func:`extract_links`, configured
by a frozen :class:`UrlCleaning`, replace ``courlan`` and the ``w3lib.url`` canonicalization surface.

:func:`microdata` gives the ``microdata`` library's ``get_items(html)`` entry point over the same C walk
:meth:`turbohtml.Document.microdata` runs, returning the :class:`MicrodataItem` records whose ``.get``/``.get_all``/
``.json`` accessors mirror that library's ``Item``. :func:`opengraph` gives the ``opengraph`` library's
``OpenGraph(html=...)`` shape over :meth:`turbohtml.Document.opengraph`: an :class:`OpenGraph` mapping of the
``og:``-stripped keys with an :meth:`~OpenGraph.is_valid` check.

:func:`dates` recovers a page's publication or modification date from its ``<meta>`` tags, JSON-LD, ``<time>``
elements, and URL, the standalone entry point ``htmldate`` exposes; a frozen :class:`DateExtraction` carries its
knobs and it returns a :class:`PublicationDate` naming the signal the date came from.

:func:`feed` normalizes an RSS 2.0, Atom 1.0, or RDF/RSS-1.0 document into one :class:`Feed` of :class:`Entry`
records, the ``feedparser.parse`` entry point over :meth:`turbohtml.Document.feed`.
"""

from __future__ import annotations

from turbohtml._html import Element

from ._article import Article
from ._dates import DateExtraction, PublicationDate, dates
from ._feed import Entry, Feed
from ._links import Link
from ._paragraphs import Extraction, Paragraph, boilerplate
from ._shorthands import feed, microdata, opengraph
from ._structured_data import MicrodataItem, OpenGraph, RdfaItem, StructuredData
from ._urls import UrlCleaning, clean_url, extract_links, normalize_url

__all__ = [
    "Article",
    "DateExtraction",
    "Element",
    "Entry",
    "Extraction",
    "Feed",
    "Link",
    "MicrodataItem",
    "OpenGraph",
    "Paragraph",
    "PublicationDate",
    "RdfaItem",
    "StructuredData",
    "UrlCleaning",
    "boilerplate",
    "clean_url",
    "dates",
    "extract_links",
    "feed",
    "microdata",
    "normalize_url",
    "opengraph",
]
