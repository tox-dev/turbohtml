"""
The Link record :meth:`turbohtml.Node.links` yields.

The enumeration and rewriting all live in the C core (``turbohtml._html``), exposed as the
:meth:`~turbohtml.Node.links`, :meth:`~turbohtml.Node.rewrite_links`, and :meth:`~turbohtml.Node.resolve_links` methods.
The core builds one :class:`Link` per link it finds; importing this module registers the record type with it.
"""

from __future__ import annotations

from typing import NamedTuple

from ._native import (
    Element,
)
from ._native import (
    register_links as _register_links,
)


class Link(NamedTuple):
    """
    One link found in a document.

    :param element: the element the link was found on.
    :param attribute: the attribute carrying the link, or ``None`` for a ``<style>`` sheet's text.
    :param url: the link exactly as it appears in the source, before any resolution.
    """

    element: Element
    """the element the link was found on."""
    attribute: str | None
    """the attribute carrying the link, or ``None`` for a ``<style>`` sheet's text."""
    url: str
    """the link exactly as it appears in the source, before any resolution."""


_register_links(Link)


__all__ = [
    "Link",
]
