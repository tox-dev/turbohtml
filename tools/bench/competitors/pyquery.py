"""pyquery: jQuery-style API over lxml -- read-path queries, content setters, and the mutating link/class edits."""

from __future__ import annotations

import functools
from urllib.parse import urljoin

from pyquery import PyQuery

from bench.timing import Mutating

REQUIREMENTS = ("pyquery>=2.0.1",)
_SET_HTML = "<p>Updated <a href='/x'>link</a> and <b>bold</b>.</p><ul><li>one</li><li>two</li></ul>"
_SET_TEXT = "Replacement text, escaped & verbatim."
_LINKS_BASE = "https://example.com/base/"


@functools.cache
def _parsed(text: str) -> PyQuery:
    """Return a document parsed once, cached so the read-path operations time only the query."""
    return PyQuery(text)


def parse(text: str) -> None:
    """Parse a whole document into a queryable tree through PyQuery."""
    PyQuery(text)


def find(text: str) -> None:
    """Collect every anchor with a pyquery selector call."""
    _parsed(text)("a")


def select(text: str) -> None:
    """Run the descendant-attribute CSS selector with pyquery (cssselect translates it to XPath on lxml)."""
    _parsed(text)("div a[href]")


def find_text(text: str) -> None:
    """Collect every element whose text contains the marker with pyquery's ``:contains`` pseudo-class."""
    _parsed(text)(":contains('test')")


def text_content(text: str) -> None:
    """Collect the body's visible text with pyquery's text()."""
    _parsed(text)("body").text()


def serialize(text: str) -> None:
    """Serialize a parsed document back to HTML with pyquery's str()."""
    _ = str(_parsed(text))


def navigate(text: str) -> None:
    """Walk every element with a pyquery ``*`` items() set."""
    for _item in _parsed(text)("*").items():
        pass


def links_extract(text: str) -> None:
    """Collect every anchor's href by iterating a pyquery items() set."""
    _ = [item.attr("href") for item in _parsed(text)("a").items()]


def links_filter(text: str) -> None:
    """Collect the anchor hrefs and keep the on-page links, mirroring the cleaned-link filter."""
    _ = [
        href
        for item in _parsed(text)("a").items()
        if (href := item.attr("href")) and not href.startswith(("#", "javascript:"))
    ]


def edit(page: PyQuery) -> None:
    """Tag every link with rel=nofollow on a freshly parsed tree through pyquery's attr setter."""
    page("a").attr("rel", "nofollow")


def class_edit(text: str) -> None:
    """Add then drop a class token on every link with pyquery's add_class/remove_class (net no-op, cached)."""
    _parsed(text)("a").add_class("seen").remove_class("seen")


def links_absolutize(page: PyQuery) -> None:
    """Resolve every relative link on a freshly parsed tree against a base with pyquery's attr setter."""
    for item in page("a").items():
        if (href := item.attr("href")) is not None:
            item.attr("href", urljoin(_LINKS_BASE, href))


def links_rewrite(text: str) -> None:
    """Rewrite every link through an identity callback with pyquery's attr setter (cached)."""
    for item in _parsed(text)("a").items():
        if (href := item.attr("href")) is not None:
            item.attr("href", href)


def socialcard(text: str) -> None:
    """Read every meta tag's property/content on a freshly parsed tree, the card-tag scan pyquery allows."""
    document = PyQuery(text)
    for item in document("meta").items():
        item.attr("property")
        item.attr("content")


def extract_url(case: tuple[str, str]) -> None:
    """Read a freshly parsed document's own URL hint with pyquery: the base href or the meta refresh."""
    kind, text = case
    document = PyQuery(text)
    if kind == "base":
        document("base").attr("href")
    else:
        document("meta[http-equiv=refresh]").attr("content")


def strip_remove(text: str) -> None:
    """Drop every code/a/q subtree with pyquery's remove, then serialize."""
    page = PyQuery(text)
    page("code, a, q").remove()
    _ = str(page)


def strip_tags(text: str) -> None:
    """Unwrap every code/a/q element keeping its content with lxml's drop_tag under pyquery, then serialize."""
    page = PyQuery(text)
    for element in page("code, a, q"):
        element.drop_tag()
    _ = str(page)


def set_html(page: PyQuery) -> None:
    """Replace a freshly parsed body's children with pyquery's html(markup)."""
    page("body").html(_SET_HTML)


def set_text(page: PyQuery) -> None:
    """Replace a freshly parsed body's children with pyquery's text(value)."""
    page("body").text(_SET_TEXT)


def chain(text: str) -> None:
    """Run a fluent jQuery-style chain with pyquery."""
    _parsed(text)("a").filter("[href]").eq(0).add_class("seen").attr("href")


def extract_attr(text: str) -> None:
    """Read every anchor's href by iterating a pyquery ``.items()`` set."""
    for item in _parsed(text)("a").items():
        item.attr("href")


def extract_text(text: str) -> None:
    """Read every anchor's text by iterating a pyquery ``.items()`` set."""
    for item in _parsed(text)("a").items():
        item.text()


OPERATIONS = {
    "parse": (parse, "pyquery"),
    "find": (find, "pyquery"),
    "select": (select, "pyquery"),
    "find-text": (find_text, "pyquery"),
    "text-content": (text_content, "pyquery"),
    "serialize": (serialize, "pyquery"),
    "edit": (Mutating(PyQuery, edit), "pyquery"),
    "class-edit": (class_edit, "pyquery"),
    "strip-remove": (strip_remove, "pyquery"),
    "strip-tags": (strip_tags, "pyquery"),
    "set-html": (Mutating(PyQuery, set_html), "pyquery"),
    "set-text": (Mutating(PyQuery, set_text), "pyquery"),
    "navigate": (navigate, "pyquery"),
    "chain": (chain, "pyquery"),
    "links-extract": (links_extract, "pyquery"),
    "links-absolutize": (Mutating(PyQuery, links_absolutize), "pyquery"),
    "links-rewrite": (links_rewrite, "pyquery"),
    "socialcard": (socialcard, "pyquery"),
    "extract-attr": (extract_attr, "pyquery"),
    "extract-text": (extract_text, "pyquery"),
    "extract-url": (extract_url, "pyquery"),
    "links-filter": (links_filter, "pyquery"),
}
