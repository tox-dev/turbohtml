"""lxml: ElementTree-style constructor build, lxml.builder's nested ``E``, and the construct/serialize breakdown."""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING

from lxml import html as lxml_html
from lxml.builder import E

from bench.timing import Mutating

if TYPE_CHECKING:
    from collections.abc import Callable

    from lxml.html import HtmlElement

REQUIREMENTS = ("lxml>=6.1.1", "cssselect>=1.2")  # cssselect backs lxml.html.cssselect() for the select/:has ops
_SET_HTML = "<p>Updated <a href='/x'>link</a> and <b>bold</b>.</p><ul><li>one</li><li>two</li></ul>"
_SET_TEXT = "Replacement text, escaped & verbatim."
_LINKS_BASE = "https://example.com/base/"


def parse(text: str) -> None:
    """Parse a whole document with lxml's libxml2-backed HTML parser."""
    lxml_html.document_fromstring(text)


def fragment(text: str) -> None:
    """Parse a fragment with lxml.html's fromstring."""
    lxml_html.fromstring(text)


def parse_xml(text: str) -> None:
    """Parse a whole XML document with lxml's libxml2-backed XML parser (etree.XMLParser)."""
    lxml_html.etree.fromstring(text.encode())


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


def serialize_xml(text: str) -> None:
    """Serialize a parsed document to XML with lxml's tostring(method='xml')."""
    lxml_html.tostring(_parsed(text), method="xml")


def canonicalize(text: str) -> None:
    """Canonicalize a parsed document to Canonical XML with lxml's tostring(method='c14n')."""
    lxml_html.tostring(_parsed(text), method="c14n")


def extract_attr(text: str) -> None:
    """Read every anchor's href with lxml's XPath attribute selection."""
    _parsed(text).xpath("//a/@href")


def extract_text(text: str) -> None:
    """Read every anchor's visible text by selecting once and reading text_content off each node."""
    _ = [anchor.text_content() for anchor in _parsed(text).cssselect("a")]


def strip_remove(text: str) -> None:
    """Drop every code/a/q subtree with lxml's strip_elements on a fresh parse, then serialize."""
    tree = lxml_html.document_fromstring(text)
    lxml_html.etree.strip_elements(tree, "code", "a", "q", with_tail=False)
    _ = lxml_html.tostring(tree)


def strip_tags(text: str) -> None:
    """Unwrap every code/a/q element keeping its content with lxml's strip_tags on a fresh parse, then serialize."""
    tree = lxml_html.document_fromstring(text)
    lxml_html.etree.strip_tags(tree, "code", "a", "q")
    _ = lxml_html.tostring(tree)


def edit(tree: HtmlElement) -> None:
    """Tag every link with rel=nofollow on a freshly parsed tree through lxml's Element.set."""
    for anchor in tree.findall(".//a"):
        anchor.set("rel", "nofollow")


def class_edit(text: str) -> None:
    """Add then drop a class token on every link with lxml's classes set."""
    for anchor in _parsed(text).findall(".//a"):
        anchor.classes.add("seen")
        anchor.classes.discard("seen")


def set_html(tree: HtmlElement) -> None:
    """Clear a freshly parsed body and append a reparsed fragment, lxml's nearest inner-HTML shape."""
    body = tree.findall(".//body")[0]
    body.clear()
    for piece in lxml_html.fragments_fromstring(_SET_HTML):
        body.append(piece)


def set_text(tree: HtmlElement) -> None:
    """Replace a freshly parsed body's children with one verbatim text node through lxml's clear plus .text."""
    body = tree.findall(".//body")[0]
    body.clear()
    body.text = _SET_TEXT


def navigate(text: str) -> None:
    """Walk every descendant element with lxml's iterdescendants iterator."""
    for _element in _parsed(text).iterdescendants():
        pass


def links_extract(text: str) -> None:
    """Collect every link with lxml's iterlinks()."""
    for _link in _parsed(text).iterlinks():
        pass


def links_absolutize(tree: HtmlElement) -> None:
    """Resolve every relative link on a freshly parsed tree against a base with lxml's make_links_absolute()."""
    tree.make_links_absolute(_LINKS_BASE)


def links_rewrite(text: str) -> None:
    """Rewrite every link through a callback with lxml's rewrite_links()."""
    _parsed(text).rewrite_links(lambda url: url)


def links_filter(text: str) -> None:
    """Collect the links and keep the on-page ones, filtering the hrefs lxml's iterlinks() yields."""
    _ = [link for _element, _attr, link, _pos in _parsed(text).iterlinks() if not link.startswith(("#", "javascript:"))]


def find_text(text: str) -> None:
    """Collect every element whose collected text contains the marker with lxml's XPath contains()."""
    _parsed(text).xpath('//*[contains(., "test")]')


def socialcard(text: str) -> None:
    """Read every meta property and content off a freshly parsed tree, lxml's take on card extraction."""
    for meta in lxml_html.document_fromstring(text).cssselect("meta"):
        meta.get("property")
        meta.get("content")


def extract_url(case: tuple[str, str]) -> None:
    """Read a freshly parsed document's own URL hint with lxml's XPath: the base href or the meta refresh."""
    kind, text = case
    tree = lxml_html.document_fromstring(text)
    if kind == "base":
        tree.xpath("//base/@href")
    else:
        tree.xpath("//meta[@http-equiv='refresh']/@content")


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


class _Counter:
    """An lxml parser target whose start handler does minimal, identical work to the reference counter."""

    def __init__(self) -> None:
        """Start the running tally."""
        self.work = 0

    def start(self, tag: str, attrs: dict[str, str]) -> None:
        """Tally a start tag and its attribute count."""
        self.work += len(tag) + len(attrs)

    def end(self, tag: str) -> None:
        """Ignore end tags."""

    def data(self, data: str) -> None:
        """Ignore character data."""

    def close(self) -> int:
        """Return the tally the parser hands back on close."""
        return self.work


def htmlparser(text: str) -> None:
    """Drive lxml's incremental HTMLParser with the counting target."""
    parser = lxml_html.etree.HTMLParser(target=_Counter())
    parser.feed(text)
    parser.close()


OPERATIONS = {
    "parse": (parse, "lxml"),
    "parse-xml": (parse_xml, "lxml.etree"),
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
    "serialize-xml": (serialize_xml, "lxml method=xml"),
    "canonicalize": (canonicalize, "lxml method=c14n"),
    "extract-attr": (extract_attr, "lxml"),
    "extract-text": (extract_text, "lxml"),
    "strip-remove": (strip_remove, "lxml"),
    "strip-tags": (strip_tags, "lxml"),
    "edit": (Mutating(lxml_html.document_fromstring, edit), "lxml"),
    "class-edit": (class_edit, "lxml"),
    "set-html": (Mutating(lxml_html.document_fromstring, set_html), "lxml"),
    "set-text": (Mutating(lxml_html.document_fromstring, set_text), "lxml"),
    "htmlparser": (htmlparser, "lxml"),
    "navigate": (navigate, "lxml"),
    "links-extract": (links_extract, "lxml"),
    "links-absolutize": (Mutating(lxml_html.document_fromstring, links_absolutize), "lxml"),
    "links-rewrite": (links_rewrite, "lxml"),
    "links-filter": (links_filter, "lxml"),
    "find-text": (find_text, "lxml"),
    "socialcard": (socialcard, "lxml"),
    "extract-url": (extract_url, "lxml"),
    "path": (getpath, "lxml getpath"),
    "path-xpath": (getpath, "lxml getpath"),
    "xpath": (xpath, "lxml"),
}
