"""The whole-page shorthands over the Document methods: microdata, opengraph, and feed."""

from __future__ import annotations

from typing import TYPE_CHECKING

from turbohtml import HTMLParseError, parse, parse_xml

if TYPE_CHECKING:
    from ._feed import Feed
    from ._structured_data import MicrodataItem, OpenGraph


def microdata(html: str, base_url: str | None = None, /) -> list[MicrodataItem]:
    """
    Extract a page's top-level HTML Microdata items, the successor to ``microdata.get_items(html)``.

    Each returned :class:`~turbohtml.MicrodataItem` carries the ``itemtype`` as
    :attr:`~turbohtml.MicrodataItem.type`, the ``itemid`` as :attr:`~turbohtml.MicrodataItem.id`, and the ``itemprop``
    values under :attr:`~turbohtml.MicrodataItem.properties`, with :meth:`~turbohtml.MicrodataItem.get`,
    :meth:`~turbohtml.MicrodataItem.get_all`, and :meth:`~turbohtml.MicrodataItem.json` matching the library's ``Item``.
    Shorthand for :meth:`turbohtml.Document.microdata` when a page string is all you hold.

    :param html: the page markup.
    :param base_url: when given, the URL each relative URL-valued property (an ``a``/``area``/``link`` href, a media
        ``src``, an ``object`` data) is resolved against, the ``microdata`` library's ``base_url``; a ``<base href>``
        refines it. ``None`` (the default) returns every value verbatim.
    :returns: one :class:`~turbohtml.MicrodataItem` per top-level ``itemscope`` element, in document order.
    :raises ValueError: if ``base_url`` is given and is not a valid absolute URL.
    """
    return parse(html).microdata(base_url)


def opengraph(html: str, base_url: str | None = None, /) -> OpenGraph:
    """
    Extract a page's Open Graph metadata, the successor to ``opengraph.OpenGraph(html=...)``.

    Reads the ``og:`` ``<meta>`` tags :meth:`turbohtml.Document.opengraph` gathers, with the ``twitter:`` tags dropped
    and the ``og:`` prefix stripped from each key so ``og["title"]`` reads the ``og:title`` tag. Shorthand for
    :meth:`turbohtml.Document.opengraph` when a page string is all you hold.

    :param html: the page markup.
    :param base_url: when given, the URL each relative URL-valued property (``og:url``, ``og:image``, ``og:video``,
        ...) is resolved against, extruct's and metadata_parser's absolutization; a ``<base href>`` refines it.
        ``None`` (the default) returns every value verbatim.
    :returns: an :class:`OpenGraph` mapping of the prefix-stripped ``og:`` properties, empty when the page has none.
    :raises ValueError: if ``base_url`` is given and is not a valid absolute URL.
    """
    return parse(html).opengraph(base_url)


def feed(xml: str, /) -> Feed | None:
    """
    Parse an RSS 2.0, Atom 1.0, or RDF/RSS-1.0 document into a :class:`Feed`, the ``feedparser.parse`` successor.

    Detects the format from the root element and maps each format's spelling of a field onto one shape: the feed's
    ``title``, ``link``, ``description``, and ``updated``, and its ``entries``, each :class:`Entry` carrying ``title``,
    ``link``, ``id``, ``updated``/``published``, ``summary``/``content``, and ``author``. Timestamps are returned
    verbatim, not parsed.

    A well-formed feed parses under :func:`turbohtml.parse_xml`, so CDATA sections and nested elements such as a channel
    ``<image>`` keep their XML meaning. An Atom XML root must declare the ``http://www.w3.org/2005/Atom`` namespace; an
    RSS 2.0 XML root must declare ``version="2.0"``. A feed that is not well-formed XML falls back to the HTML tree
    builder's recovery. Shorthand for :meth:`turbohtml.Document.feed` on either tree.

    :param xml: the feed document.
    :returns: the parsed :class:`Feed`, or ``None`` when ``xml`` has no ``<rss>``, ``<feed>``, or ``<rdf:RDF>`` root.
    """
    try:
        document = parse_xml(xml)
    except HTMLParseError:
        document = parse(xml)
    return document.feed()


microdata.__module__ = "turbohtml.extract"  # documented and imported from turbohtml.extract, not this module
opengraph.__module__ = "turbohtml.extract"
feed.__module__ = "turbohtml.extract"


__all__ = [
    "feed",
    "microdata",
    "opengraph",
]
