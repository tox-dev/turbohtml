"""
The Article record :meth:`turbohtml.Node.article` returns.

The content scoring and metadata harvesting all live in the C core (``turbohtml._html``), exposed as the
:meth:`~turbohtml.Node.article` method. The core builds one :class:`Article` per call; importing this module registers
the record type with it.
"""

from __future__ import annotations

from typing import NamedTuple

from ._native import (
    Element,
)
from ._native import (
    register_article as _register_article,
)


class Article(NamedTuple):
    """
    The dominant content of a document and the metadata harvested beside it.

    ``element`` is the scored content body (``None`` when nothing reads as content) and ``text`` its layout-aware plain
    text (empty then). ``title``, ``byline``, ``date``, ``description``, ``lang``, ``canonical`` (the ``<link
    rel=canonical>`` URL or ``og:url``), ``site_name`` (``og:site_name`` or ``<meta name=application-name>``) and
    ``image`` (the ``og:image`` or ``twitter:image`` lead image) are single-valued page metadata, each
    whitespace-normalized or ``None`` when the source is absent. ``tags`` collects every ``<meta name=keywords>`` value
    (comma-split) and ``article:tag`` in document order, an empty tuple when none appear. This makes
    :meth:`~turbohtml.Node.article` a one-call replacement for trafilatura and newspaper3k.

    :param element: the scored content body, or ``None`` when nothing reads as content.
    :param text: the layout-aware plain text of the content body, empty when there is none.
    :param title: the page title, or ``None``.
    :param byline: the author byline, or ``None``.
    :param date: the publication date, or ``None``.
    :param description: the page description, or ``None``.
    :param lang: the page language, or ``None``.
    :param canonical: the canonical URL, or ``None``.
    :param site_name: the site name, or ``None``.
    :param tags: the keywords and ``article:tag`` values in document order, empty when none appear.
    :param image: the lead image URL, or ``None``.
    """

    element: Element | None
    text: str
    title: str | None
    byline: str | None
    date: str | None
    description: str | None
    lang: str | None
    canonical: str | None
    site_name: str | None
    tags: tuple[str, ...]
    image: str | None


_register_article(Article)


__all__ = [
    "Article",
]
