"""
Enumerate and absolutize the links in a parsed document.

Iterating ``<a href>`` by hand misses the URLs HTML hides elsewhere: in ``srcset`` candidate lists, in a ``<meta
http-equiv=refresh>`` redirect, in ``ping``/``archive`` lists, and -- the case no hand-rolled walk catches -- inside
CSS ``url()`` and ``@import`` in a ``style`` attribute or a ``<style>`` sheet. :func:`links` finds every one of them in
a single C tree walk; :func:`resolve_links` rewrites them absolute against a base URL, and :func:`rewrite_links` applies
an arbitrary function.

The walk and the in-place rewrite live in C (``turbohtml._html``); this module is the thin typed facade. URL resolution
itself is :func:`urllib.parse.urljoin` from the standard library, so RFC 3986 is not reinvented.
"""

from __future__ import annotations

from itertools import starmap
from typing import TYPE_CHECKING, NamedTuple
from urllib.parse import urljoin

from ._html import Element, _links, _rewrite_links

if TYPE_CHECKING:
    from collections.abc import Callable

    from ._html import Node


class Link(NamedTuple):
    """
    One link found in a document.

    ``element`` owns the link, ``attribute`` is the attribute carrying it (``None`` for a ``<style>`` sheet's text), and
    ``url`` is the link exactly as it appears in the source.
    """

    element: Element
    attribute: str | None
    url: str


def links(node: Node) -> list[Link]:
    """Return every link in ``node`` and its descendants, in document order."""
    return list(starmap(Link, _links(node)))


def resolve_links(node: Node, base_url: str) -> None:
    """Rewrite every link in ``node`` to an absolute URL resolved against ``base_url``, in place."""
    _rewrite_links(node, lambda url: urljoin(base_url, url))


def rewrite_links(node: Node, replace: Callable[[str], str | None]) -> None:
    """Replace every link in ``node`` in place with ``replace(url)``; a ``None`` result leaves the link unchanged."""
    _rewrite_links(node, replace)


__all__ = [
    "Link",
    "links",
    "resolve_links",
    "rewrite_links",
]
