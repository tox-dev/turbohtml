"""
The Article record :meth:`turbohtml.Node.article` returns.

The content scoring and metadata harvesting all live in the C core (``turbohtml._html``), exposed as the
:meth:`~turbohtml.Node.article` method. The core builds one :class:`Article` per call; importing this module registers
the record type with it.
"""

from __future__ import annotations

from typing import NamedTuple

from ._html import Element, _register_article  # Element stays importable so autodoc resolves it


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
