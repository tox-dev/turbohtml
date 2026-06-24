"""lxml: ElementTree-style constructor build, lxml.builder's nested ``E``, and the construct/serialize breakdown."""

from __future__ import annotations

import functools

from lxml import html as lxml_html
from lxml.builder import E

REQUIREMENTS = ("lxml>=6.1.1",)


def parse(text: str) -> None:
    """Parse a whole document with lxml's libxml2-backed HTML parser."""
    lxml_html.document_fromstring(text)


def fragment(text: str) -> None:
    """Parse a fragment with lxml.html's fromstring."""
    lxml_html.fromstring(text)


def build(count: int) -> None:
    """Build a ``<ul>`` of rows with lxml's Element factory and ``.text``, then serialize (the aggregate workload)."""
    ul = lxml_html.Element("ul")
    for index in range(count):
        li = lxml_html.Element("li", {"class": "item", "data-i": str(index)})
        li.text = f"item {index}"
        ul.append(li)
    _ = lxml_html.tostring(ul)


def build_e(count: int) -> None:
    """Build the same ``<ul>`` with lxml.builder's nested ``E`` calls and serialize the tree."""
    rows = (E.li({"class": "item", "data-i": str(index)}, f"item {index}") for index in range(count))
    _ = lxml_html.tostring(E.ul(*rows))


def construct(count: int) -> None:
    """Construct ``count`` elements with attributes and text, in isolation from serialization."""
    for index in range(count):
        element = lxml_html.Element("li", {"class": "item", "data-i": str(index)})
        element.text = f"item {index}"


@functools.cache
def _tree(count: int) -> object:
    """Return a built ``<ul>`` of ``count`` rows, cached so ``serialize`` times only the emit step."""
    ul = lxml_html.Element("ul")
    for index in range(count):
        li = lxml_html.Element("li", {"class": "item", "data-i": str(index)})
        li.text = f"item {index}"
        ul.append(li)
    return ul


def emit(count: int) -> None:
    """Emit a pre-built ``count``-row tree with ``lxml.html.tostring``."""
    _ = lxml_html.tostring(_tree(count))


@functools.cache
def _parsed(text: str) -> object:
    """Return a document parsed once, cached so the read-path operations time only the query."""
    return lxml_html.document_fromstring(text)


def find(text: str) -> None:
    """Collect every anchor with lxml's XPath findall."""
    _parsed(text).findall(".//a")  # ty: ignore[unresolved-attribute]  # cached HtmlElement


def select(text: str) -> None:
    """Run the CSS selector with lxml's cssselect."""
    _parsed(text).cssselect("div a[href]")  # ty: ignore[unresolved-attribute]


def select_has(text: str) -> None:
    """Run the :has() relational selector with lxml's cssselect."""
    _parsed(text).cssselect("div:has(a)")  # ty: ignore[unresolved-attribute]


def text_content(text: str) -> None:
    """Collect the document's visible text with lxml's text_content method."""
    _parsed(text).text_content()  # ty: ignore[unresolved-attribute]


def serialize(text: str) -> None:
    """Serialize a parsed document back to HTML with lxml's tostring."""
    lxml_html.tostring(_parsed(text))


OPERATIONS = {
    "parse": (parse, "lxml"),
    "fragment": (fragment, "lxml"),
    "build": (build, "lxml"),
    "build-e": (build_e, "lxml.builder"),
    "construct": (construct, "lxml"),
    "emit": (emit, "lxml"),
    "find": (find, "lxml"),
    "select": (select, "lxml"),
    "select-has": (select_has, "lxml"),
    "text-content": (text_content, "lxml"),
    "serialize": (serialize, "lxml"),
}
