"""
The Link record :meth:`turbohtml.Node.links` yields.

The enumeration and rewriting all live in the C core (``turbohtml._html``), exposed as the
:meth:`~turbohtml.Node.links`, :meth:`~turbohtml.Node.rewrite_links`, and :meth:`~turbohtml.Node.resolve_links` methods.
The core builds one :class:`Link` per link it finds; importing this module registers the record type with it.
"""

from __future__ import annotations

from typing import NamedTuple

from ._html import Element, _register_links  # Element stays importable so autodoc resolves it


class Link(NamedTuple):
    """
    One link found in a document.

    ``element`` owns the link, ``attribute`` is the attribute carrying it (``None`` for a ``<style>`` sheet's text), and
    ``url`` is the link exactly as it appears in the source.
    """

    element: Element
    attribute: str | None
    url: str


_register_links(Link)
