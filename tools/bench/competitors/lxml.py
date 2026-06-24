"""lxml: ElementTree-style constructor build, lxml.builder's nested ``E``, and the construct/serialize breakdown."""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING

from lxml import html as lxml_html
from lxml.builder import E

if TYPE_CHECKING:
    from collections.abc import Callable

    from lxml.html import HtmlElement

REQUIREMENTS = ("lxml>=6.1.1",)
_SET_HTML = "<p>Updated <a href='/x'>link</a> and <b>bold</b>.</p><ul><li>one</li><li>two</li></ul>"
_LINKS_BASE = "https://example.com/base/"


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
def _parsed(text: str) -> HtmlElement:
    """Return a document parsed once, cached so the read-path operations time only the query."""
    return lxml_html.document_fromstring(text)


def find(text: str) -> None:
    """Collect every anchor with lxml's XPath findall."""
    _parsed(text).findall(".//a")


def select(text: str) -> None:
    """Run the CSS selector with lxml's cssselect."""
    _parsed(text).cssselect("div a[href]")


def select_has(text: str) -> None:
    """Run the :has() relational selector with lxml's cssselect."""
    _parsed(text).cssselect("div:has(a)")


def text_content(text: str) -> None:
    """Collect the document's visible text with lxml's text_content method."""
    _parsed(text).text_content()


def serialize(text: str) -> None:
    """Serialize a parsed document back to HTML with lxml's tostring."""
    lxml_html.tostring(_parsed(text))


def edit(text: str) -> None:
    """Tag every link with rel=nofollow through lxml's Element.set."""
    for anchor in _parsed(text).findall(".//a"):
        anchor.set("rel", "nofollow")


def class_edit(text: str) -> None:
    """Add then drop a class token on every link with lxml's classes set."""
    for anchor in _parsed(text).findall(".//a"):
        anchor.classes.add("seen")
        anchor.classes.discard("seen")


def set_html(text: str) -> None:
    """Clear the body and append a reparsed fragment, lxml's nearest inner-HTML shape."""
    body = _parsed(text).findall(".//body")[0]
    body.clear()
    for piece in lxml_html.fragments_fromstring(_SET_HTML):
        body.append(piece)


def navigate(text: str) -> None:
    """Walk every descendant element with lxml's iterdescendants iterator."""
    for _element in _parsed(text).iterdescendants():
        pass


def links_extract(text: str) -> None:
    """Collect every link with lxml's iterlinks()."""
    for _link in _parsed(text).iterlinks():
        pass


def links_absolutize(text: str) -> None:
    """Resolve every relative link against a base with lxml's make_links_absolute()."""
    _parsed(text).make_links_absolute(_LINKS_BASE)


def links_rewrite(text: str) -> None:
    """Rewrite every link through a callback with lxml's rewrite_links()."""
    _parsed(text).rewrite_links(lambda url: url)


def getpath(text: str) -> None:
    """Generate the positional XPath for every element with lxml's getroottree().getpath()."""
    tree = _parsed(text)
    root = tree.getroottree()
    for element in tree.iter():
        if isinstance(element.tag, str):  # skip the comment/PI proxies iter() also yields
            root.getpath(element)


_EXSLT_NS = {"re": "http://exslt.org/regular-expressions", "set": "http://exslt.org/sets"}
_SVG_NS = {"svg": "http://www.w3.org/2000/svg"}


def _count_ext(_context: object, nodes: list[object]) -> float:
    """Count the node-set; a trivial extension registered for the engine."""
    return float(len(nodes))


def _first_two_ext(_context: object, nodes: list[object]) -> list[object]:
    """Return the first two nodes as a node-set; the cheapest non-trivial node-set return."""
    return nodes[:2]


_COUNT_EXTENSIONS = {(None, "ext_count"): _count_ext}
_NODESET_EXTENSIONS = {(None, "ext_first_two"): _first_two_ext}
_REUSE = lxml_html.etree.XPath("//a[@href]")


@functools.cache
def _div_rows(text: str) -> object:
    """Return the document's <div> elements, cached so the node-set variable case times only the reuse."""
    return _parsed(text).xpath("//div")


_XPATH_CALLS: dict[str, Callable[..., object]] = {
    "//div": lambda tree, _text: tree.xpath("//div"),
    "//a[@href]": lambda tree, _text: tree.xpath("//a[@href]"),
    "//div//a[@href]": lambda tree, _text: tree.xpath("//div//a[@href]"),
    "/html/body/div": lambda tree, _text: tree.xpath("/html/body/div"),
    "//div//a[1]": lambda tree, _text: tree.xpath("//div//a[1]"),
    "//a[contains(@href, '/')]": lambda tree, _text: tree.xpath("//a[contains(@href, '/')]"),
    "//div[position() <= 3]": lambda tree, _text: tree.xpath("//div[position() <= 3]"),
    "//a/ancestor::div": lambda tree, _text: tree.xpath("//a/ancestor::div"),
    "//a | //span": lambda tree, _text: tree.xpath("//a | //span"),
    "//*[local-name() = 'a']": lambda tree, _text: tree.xpath("//*[local-name() = 'a']"),
    "count(//a)": lambda tree, _text: tree.xpath("count(//a)"),
    "variable": lambda tree, _text: tree.xpath("//a[@href=$href]", href="/x"),
    "re:test": lambda tree, _text: tree.xpath("//a[re:test(@href, '[0-9]')]", namespaces=_EXSLT_NS),
    "set:distinct": lambda tree, _text: tree.xpath("set:distinct(//a)", namespaces=_EXSLT_NS),
    "smart_strings": lambda tree, _text: tree.xpath("//a/@href", smart_strings=True),
    "extension": lambda tree, _text: tree.xpath("ext_count(//a)", extensions=_COUNT_EXTENSIONS),
    "nodeset_extension": lambda tree, _text: tree.xpath("ext_first_two(//a)/@href", extensions=_NODESET_EXTENSIONS),
    "namespaces": lambda tree, _text: tree.xpath("//svg:rect", namespaces=_SVG_NS),
    "node_set_variable": lambda tree, text: tree.xpath("$rows/div", rows=_div_rows(text)),
    "precompiled": lambda tree, _text: _REUSE(tree),
}


def xpath(case: tuple[str, str]) -> None:
    """Evaluate one XPath feature class with lxml's libxml2 engine, by case kind."""
    kind, text = case
    _XPATH_CALLS[kind](_parsed(text), text)


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
    "edit": (edit, "lxml"),
    "class-edit": (class_edit, "lxml"),
    "set-html": (set_html, "lxml"),
    "navigate": (navigate, "lxml"),
    "links-extract": (links_extract, "lxml"),
    "links-absolutize": (links_absolutize, "lxml"),
    "links-rewrite": (links_rewrite, "lxml"),
    "path": (getpath, "lxml getpath"),
    "path-xpath": (getpath, "lxml getpath"),
    "xpath": (xpath, "lxml"),
}
