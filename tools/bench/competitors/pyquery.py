"""pyquery: jQuery-style API over lxml -- the strip, content-setter, and chain operations."""

from __future__ import annotations

import functools

from pyquery import PyQuery

REQUIREMENTS = ("pyquery>=2.0.1",)
_SET_HTML = "<p>Updated <a href='/x'>link</a> and <b>bold</b>.</p><ul><li>one</li><li>two</li></ul>"
_SET_TEXT = "Replacement text, escaped & verbatim."


@functools.cache
def _parsed(text: str) -> PyQuery:
    """Return a document parsed once, cached so the content setters time only the mutation."""
    return PyQuery(text)


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


def set_html(text: str) -> None:
    """Replace the body's children with pyquery's html(markup)."""
    _parsed(text)("body").html(_SET_HTML)


def set_text(text: str) -> None:
    """Replace the body's children with pyquery's text(value)."""
    _parsed(text)("body").text(_SET_TEXT)


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
    "strip-remove": (strip_remove, "pyquery"),
    "strip-tags": (strip_tags, "pyquery"),
    "set-html": (set_html, "pyquery"),
    "set-text": (set_text, "pyquery"),
    "chain": (chain, "pyquery"),
    "extract-attr": (extract_attr, "pyquery"),
    "extract-text": (extract_text, "pyquery"),
}
