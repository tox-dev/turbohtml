"""parsel: Scrapy's selector library (lxml + cssselect) -- CSS/XPath queries and the ::attr/::text extraction idioms."""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING

from cssselect import HTMLTranslator
from lxml import html as _lxml_html
from parsel import Selector

if TYPE_CHECKING:
    from collections.abc import Callable

REQUIREMENTS = ("parsel>=1.11", "cssselect>=1.2")
_TRANSLATOR = HTMLTranslator()


@functools.cache
def _parsed(text: str) -> Selector:
    """Return a document parsed once, cached so the read-path operations time only the query."""
    return Selector(text=text)


def parse(text: str) -> None:
    """Parse a whole document into a selectable tree through parsel's Selector."""
    Selector(text=text)


def find(text: str) -> None:
    """Collect every anchor with parsel's css."""
    _parsed(text).css("a")


def select(text: str) -> None:
    """Run the CSS selector with parsel's css (cssselect translates it to XPath on libxml2)."""
    _parsed(text).css("div a[href]")


def select_has(text: str) -> None:
    """Run the :has() relational selector expressed as an XPath predicate (cssselect :has is version-fragile)."""
    _parsed(text).xpath("//div[.//a]")


def find_text(text: str) -> None:
    """Collect every element whose text contains the marker with an XPath contains() predicate."""
    _parsed(text).xpath('//*[contains(., "test")]')


def text_content(text: str) -> None:
    """Collect the document's visible text with an XPath text() sweep that skips script/style."""
    _parsed(text).xpath("//body//text()[not(ancestor::script or ancestor::style)]").getall()


def serialize(text: str) -> None:
    """Serialize a parsed document back to HTML with parsel's get()."""
    _parsed(text).get()


def links_extract(text: str) -> None:
    """Collect every anchor's href with parsel's ``::attr(href)`` getall."""
    _parsed(text).css("a::attr(href)").getall()


def links_filter(text: str) -> None:
    """Collect the anchor hrefs and keep the on-page links, mirroring the cleaned-link filter."""
    _ = [
        href
        for href in _parsed(text).css("a::attr(href)").getall()
        if href and not href.startswith(("#", "javascript:"))
    ]


def extract_attr(text: str) -> None:
    """Pull every anchor's href with parsel's ``::attr(href)`` getall."""
    _parsed(text).css("a::attr(href)").getall()


def extract_text(text: str) -> None:
    """Pull every anchor's text with parsel's ``::text`` getall."""
    _parsed(text).css("a::text").getall()


def extract_url(case: tuple[str, str]) -> None:
    """Read a freshly parsed document's own URL hint with parsel: the base href or the meta refresh."""
    kind, text = case
    selector = Selector(text=text)
    if kind == "base":
        selector.css("base::attr(href)").get()
    else:
        selector.css("meta[http-equiv=refresh]::attr(content)").get()


def path_xpath(text: str) -> None:
    """Generate the positional XPath that re-finds every element with lxml's getpath under parsel."""
    tree = _parsed(text).root.getroottree()
    _ = [tree.getpath(element) for element in _parsed(text).root.iter()]


def translate(selector: str) -> None:
    """Translate one CSS selector to XPath 1.0 with the cssselect HTMLTranslator parsel's css() builds on."""
    _TRANSLATOR.css_to_xpath(selector)


def _count_ext(_context: object, nodes: list[object]) -> float:
    """Count the node-set; a trivial extension registered for the engine."""
    return float(len(nodes))


def _first_two_ext(_context: object, nodes: list[object]) -> list[object]:
    """Return the first two nodes as a node-set; the cheapest non-trivial node-set return."""
    return nodes[:2]


_SVG_NS = {"svg": "http://www.w3.org/2000/svg"}
_COUNT_EXTENSIONS = {(None, "ext_count"): _count_ext}
_NODESET_EXTENSIONS = {(None, "ext_first_two"): _first_two_ext}
_PRECOMPILED = _lxml_html.etree.XPath("//a[@href]")


@functools.cache
def _div_rows(text: str) -> list[object]:
    """Return the document's <div> lxml elements, cached so the node-set variable case times only the reuse."""
    return [selector.root for selector in _parsed(text).xpath("//div")]


_XPATH_CALLS: dict[str, Callable[..., object]] = {
    "//div": lambda sel, _text: sel.xpath("//div"),
    "//a[@href]": lambda sel, _text: sel.xpath("//a[@href]"),
    "//div//a[@href]": lambda sel, _text: sel.xpath("//div//a[@href]"),
    "/html/body/div": lambda sel, _text: sel.xpath("/html/body/div"),
    "//div//a[1]": lambda sel, _text: sel.xpath("//div//a[1]"),
    "//a[contains(@href, '/')]": lambda sel, _text: sel.xpath("//a[contains(@href, '/')]"),
    "//div[position() <= 3]": lambda sel, _text: sel.xpath("//div[position() <= 3]"),
    "//a/ancestor::div": lambda sel, _text: sel.xpath("//a/ancestor::div"),
    "//a | //span": lambda sel, _text: sel.xpath("//a | //span"),
    "//*[local-name() = 'a']": lambda sel, _text: sel.xpath("//*[local-name() = 'a']"),
    "count(//a)": lambda sel, _text: sel.xpath("count(//a)"),
    "variable": lambda sel, _text: sel.xpath("//a[@href=$href]", href="/x"),
    "re:test": lambda sel, _text: sel.xpath("//a[re:test(@href, '[0-9]')]"),
    "set:distinct": lambda sel, _text: sel.xpath("set:distinct(//a)"),
    "smart_strings": lambda sel, _text: sel.xpath("//a/@href"),
    "extension": lambda sel, _text: sel.xpath("ext_count(//a)", extensions=_COUNT_EXTENSIONS),
    "nodeset_extension": lambda sel, _text: sel.xpath("ext_first_two(//a)/@href", extensions=_NODESET_EXTENSIONS),
    "namespaces": lambda sel, _text: sel.xpath("//svg:rect", namespaces=_SVG_NS),
    "node_set_variable": lambda sel, text: sel.xpath("$rows/div", rows=_div_rows(text)),
    "precompiled": lambda sel, _text: _PRECOMPILED(sel.root),
}


def xpath(case: tuple[str, str]) -> None:
    """Evaluate one XPath feature class with parsel's lxml-backed engine, by case kind."""
    kind, text = case
    _XPATH_CALLS[kind](_parsed(text), text)


OPERATIONS = {
    "parse": (parse, "parsel"),
    "find": (find, "parsel"),
    "select": (select, "parsel"),
    "select-has": (select_has, "parsel"),
    "find-text": (find_text, "parsel"),
    "text-content": (text_content, "parsel"),
    "serialize": (serialize, "parsel"),
    "links-extract": (links_extract, "parsel"),
    "links-filter": (links_filter, "parsel"),
    "extract-attr": (extract_attr, "parsel"),
    "extract-text": (extract_text, "parsel"),
    "extract-url": (extract_url, "parsel"),
    "path-xpath": (path_xpath, "parsel"),
    "translate": (translate, "parsel"),
    "xpath": (xpath, "parsel"),
}
