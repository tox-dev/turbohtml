"""lxml-html-clean: the blocklist Cleaner externalized from lxml.html.clean."""

from __future__ import annotations

from typing import TYPE_CHECKING

import lxml_html_clean
from lxml import html

from bench.timing import Mutating

if TYPE_CHECKING:
    from lxml.html import HtmlElement

REQUIREMENTS = ("lxml-html-clean>=0.4.5", "lxml>=6.1.1")

_CLEANER = lxml_html_clean.Cleaner()


def sanitize(text: str) -> None:
    _CLEANER.clean_html(text)


def linkify(text: str) -> None:
    # Auto-link URLs in HTML with lxml-html-clean's autolink_html, on an lxml tree.
    lxml_html_clean.autolink_html(text)


def _fresh(text: str) -> HtmlElement:
    return html.fragment_fromstring(text, create_parent="div")


def _sanitize_node(node: HtmlElement) -> HtmlElement:
    return _CLEANER.clean_html(node)


def _linkify_node(node: HtmlElement) -> HtmlElement:
    lxml_html_clean.autolink(node, avoid_hosts=())
    return node


OPERATIONS = {
    "sanitize-node": (Mutating(setup=_fresh, run=_sanitize_node), "lxml-html-clean"),
    "linkify-node": (Mutating(setup=_fresh, run=_linkify_node), "lxml-html-clean"),
    "sanitize": (sanitize, "lxml-html-clean"),
    "linkify": (linkify, "lxml-html-clean"),
}
