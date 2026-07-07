"""
The typed result classes behind :meth:`turbohtml.Document.feed` and :func:`turbohtml.extract.feed`.

The walk that detects the feed format (RSS 2.0, Atom 1.0, or RDF/RSS-1.0) and normalizes an ``<item>``/``<entry>``/RDF
item into one shape lives in the C core (``turbohtml._html``), exposed as :meth:`~turbohtml.Document.feed`. The C pass
assembles the plain ``str``/``None`` fields and hands them to the :class:`Feed` and :class:`Entry` records defined here,
so the typed, read-only result shapes live in Python while every walk stays in C. Importing this module registers both
classes with the core.
"""

from __future__ import annotations

from typing import NamedTuple

from ._html import _register_feed

__all__ = ["Entry", "Feed"]


class Entry(NamedTuple):
    """
    One feed item, the normalized shape an RSS ``<item>``, an Atom ``<entry>``, and an RDF item share.

    Every field is the first present value across the format's spellings, or ``None`` when the entry carries none.

    :param title: the entry title.
    :param link: the entry's permalink -- an Atom ``<link rel="alternate">`` href, an RSS/RDF ``<link>`` URL, or a
        permalink ``<guid>`` when the entry has no ``<link>``.
    :param id: the stable identifier: an RSS ``<guid>``, an Atom ``<id>``, or an RDF item's ``rdf:about``.
    :param updated: the last-modified timestamp (Atom ``<updated>``, RSS ``<lastBuildDate>``), verbatim.
    :param published: the publication timestamp (Atom ``<published>``, RSS ``<pubDate>``, RDF ``<dc:date>``), verbatim.
    :param summary: the short description (Atom ``<summary>``, RSS ``<description>``, ``<dc:description>``).
    :param content: the full body (RSS ``<content:encoded>``, Atom ``<content>``), preferred over ``summary``.
    :param author: the author display name (an Atom ``<author><name>``, an RSS ``<author>``, or a ``<dc:creator>``).
    """

    title: str | None
    link: str | None
    id: str | None
    updated: str | None
    published: str | None
    summary: str | None
    content: str | None
    author: str | None


class Feed(NamedTuple):
    """
    A parsed syndication feed, the record :meth:`turbohtml.Document.feed` and :func:`turbohtml.extract.feed` return.

    :param type: the detected format, one of ``"rss"``, ``"atom"``, or ``"rdf"``.
    :param title: the feed title.
    :param link: the feed's site link.
    :param description: the feed description (RSS ``<description>``, Atom ``<subtitle>``).
    :param updated: the feed's last-modified timestamp, verbatim.
    :param entries: the feed's items in document order.
    """

    type: str
    title: str | None
    link: str | None
    description: str | None
    updated: str | None
    entries: tuple[Entry, ...]


_register_feed(Feed, Entry)
