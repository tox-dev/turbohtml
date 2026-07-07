"""
turbohtml's own timing for every operation: the shared baseline each competitor divides into.

This module imports turbohtml and nothing else, so it loads only in the turbohtml-only ``core`` venv. ``OPERATIONS``
maps each operation to ``(timing function, label)``; the function takes the same case input the competitor receives.
"""

from __future__ import annotations

import functools
import re
from dataclasses import replace
from typing import TYPE_CHECKING, cast

import turbohtml
from bench.timing import Mutating
from turbohtml import Markdown as _Markdown
from turbohtml import clean as _clean
from turbohtml import query as _query
from turbohtml.build import E
from turbohtml.clean import LinkDetector as _LinkDetector
from turbohtml.clean import linkify as _linkify
from turbohtml.clean import minify as _minify
from turbohtml.convert import css_specificity as _css_specificity
from turbohtml.convert import css_to_xpath as _css_to_xpath
from turbohtml.detect import detect as _detect_encoding
from turbohtml.extract import boilerplate as _extract_boilerplate
from turbohtml.extract import clean_url as _clean_url
from turbohtml.extract import dates as _extract_dates
from turbohtml.extract import extract_links as _extract_links
from turbohtml.extract import normalize_url as _normalize_url
from turbohtml.migration.markupsafe import Markup as _Markup
from turbohtml.migration.markupsafe import escape as _markup_escape
from turbohtml.migration.stdlib import HTMLParser as _TurboHTMLParser
from turbohtml.query import Query as _Query

if TYPE_CHECKING:
    from collections.abc import Callable

_SANITIZER = _clean.Sanitizer(_clean.Policy.relaxed())
_SANITIZER_TEMPLATES = _clean.Sanitizer(replace(_clean.Policy.relaxed(), strip_template_markers=True))
_LINKS_BASE = "https://example.com/base/"
_URL_HINT_BASE = "http://site.com/"
_FIND_TEXT_PATTERN = re.compile(r"test")  # ubiquitous in the wpt corpus, so the predicate does real work
_CSS = "div a[href]"  # a descendant combinator with an attribute test, common in scrapers
_HAS = "div:has(a)"  # the :has() relational pseudo-class
_STRIP = "code, a, q"  # a bulk set of tags to drop or unwrap
_SET_HTML = "<p>Updated <a href='/x'>link</a> and <b>bold</b>.</p><ul><li>one</li><li>two</li></ul>"
_SET_TEXT = "Replacement text, escaped & verbatim."
_DETECTOR = _LinkDetector()
_ANNOTATION_RULES = {"h1": ["heading"], "b": ["emphasis"], "a": ["link"]}


def build(count: int) -> None:
    """Build a ``<ul>`` of rows with turbohtml's element constructors and serialize it (the aggregate workload)."""
    ul = turbohtml.Element("ul")
    for index in range(count):
        li = turbohtml.Element("li", {"class": "item", "data-i": str(index)})
        li.text = f"item {index}"
        ul.append(li)
    _ = ul.html


def build_e(count: int) -> None:
    """Build the same ``<ul>`` with the terse :data:`turbohtml.build.E` factory and serialize it."""
    rows = [E.li({"class": "item", "data-i": str(index)}, f"item {index}") for index in range(count)]
    _ = E.ul(*rows).serialize()


def construct(count: int) -> None:
    """Construct ``count`` elements with attributes and text, in isolation from serialization."""
    for index in range(count):
        element = turbohtml.Element("li", {"class": "item", "data-i": str(index)})
        element.text = f"item {index}"


@functools.cache
def _tree(count: int) -> turbohtml.Element:
    """Return a built ``<ul>`` of ``count`` rows, cached so ``serialize`` times only the emit step."""
    ul = turbohtml.Element("ul")
    for index in range(count):
        li = turbohtml.Element("li", {"class": "item", "data-i": str(index)})
        li.text = f"item {index}"
        ul.append(li)
    return ul


def emit(count: int) -> None:
    """Emit a pre-built ``count``-row tree, in isolation from construction."""
    _ = _tree(count).html


def parse(text: str) -> None:
    """Parse a whole document into a navigable tree through turbohtml.parse()."""
    turbohtml.parse(text)


def fragment(text: str) -> None:
    """Parse a fragment in its container context with turbohtml.parse_fragment."""
    turbohtml.parse_fragment(text, context="tbody")


def escape(text: str) -> None:
    """Escape text with turbohtml.escape."""
    turbohtml.escape(text)


def unescape(text: str) -> None:
    """Resolve character references with turbohtml.unescape."""
    turbohtml.unescape(text)


def tokenize(text: str) -> None:
    """Consume turbohtml's token stream so lazy Token construction is included."""
    for _ in turbohtml.tokenize(text):
        pass


@functools.cache
def _parsed(text: str) -> turbohtml.Document:
    """Return a document parsed once, cached so the read-path operations time only the query."""
    return turbohtml.parse(text)


def find(text: str) -> None:
    """Collect every anchor with turbohtml's find_all."""
    _parsed(text).find_all("a")


def select(text: str) -> None:
    """Run the CSS selector with turbohtml's select."""
    _parsed(text).select(_CSS)


def select_has(text: str) -> None:
    """Run the :has() relational selector with turbohtml's select."""
    _parsed(text).select(_HAS)


def match(text: str) -> None:
    """Test every anchor against a selector with the soupsieve-shaped turbohtml.query matcher."""
    matcher = _query.compile(_CSS)
    for anchor in _parsed(text).find_all("a"):
        matcher.match(anchor)


def find_text(text: str) -> None:
    """Collect every element whose collected text matches the regex with turbohtml's find_all."""
    _parsed(text).find_all(text=_FIND_TEXT_PATTERN)


def text_content(text: str) -> None:
    """Collect the document's visible text with turbohtml's text property."""
    _ = _parsed(text).text


def serialize(text: str) -> None:
    """Serialize a parsed document back to HTML with turbohtml's html property."""
    _ = _parsed(text).html


def minify(text: str) -> str:
    """Minify an HTML document with turbohtml, parsing then serializing through the round-trip-safe Minify layout."""
    return _minify(text)


def minify_js(source: str) -> str:
    """Minify a JavaScript source with turbohtml's native lex-parse-optimize-print minifier."""
    return _clean.minify_js(source)


def edit(document: turbohtml.Document) -> None:
    """Tag every link with rel=nofollow on a freshly parsed tree through turbohtml's live attribute mapping."""
    for anchor in document.find_all("a"):
        anchor.attrs["rel"] = "nofollow"


def class_edit(text: str) -> None:
    """Add then drop a class token on every link with turbohtml's classList mutators."""
    for anchor in _parsed(text).find_all("a"):
        anchor.add_class("seen").remove_class("seen")


def strip_remove(text: str) -> None:
    """Drop every code/a/q subtree with turbohtml's bulk remove, then serialize."""
    _ = turbohtml.parse(text).remove(_STRIP).html


def strip_tags(text: str) -> None:
    """Unwrap every code/a/q element keeping its content with turbohtml's strip_tags, then serialize."""
    _ = turbohtml.parse(text).strip_tags(_STRIP).html


def set_html(document: turbohtml.Document) -> None:
    """Replace a freshly parsed body's children by reparsing a fragment in context with turbohtml's set_inner_html."""
    document.find_all("body")[0].set_inner_html(_SET_HTML)


def set_text(document: turbohtml.Document) -> None:
    """Replace a freshly parsed body's children with one verbatim text node through turbohtml's set_text."""
    document.find_all("body")[0].set_text(_SET_TEXT)


def navigate(text: str) -> None:
    """Walk every descendant node with turbohtml's descendants iterator."""
    for _node in _parsed(text).descendants:
        pass


def chain(text: str) -> None:
    """Run a fluent jQuery-style chain with turbohtml's Query wrapper."""
    _Query(_parsed(text))("a").filter("[href]").eq(0).add_class("seen").attr("href")


def links_extract(text: str) -> None:
    """Collect every link with turbohtml's links()."""
    _parsed(text).links()


def links_absolutize(document: turbohtml.Document) -> None:
    """Resolve every relative link on a freshly parsed tree against a base with turbohtml's resolve_links()."""
    document.resolve_links(_LINKS_BASE)


def links_rewrite(text: str) -> None:
    """Rewrite every link through a callback with turbohtml's rewrite_links()."""
    _parsed(text).rewrite_links(lambda url: url)


def socialcard(text: str) -> None:
    """Read the OpenGraph/Twitter card tags with turbohtml (parse plus one C walk)."""
    turbohtml.parse(text).opengraph()


def structured(text: str) -> None:
    """Extract JSON-LD, Microdata, and OpenGraph with turbohtml in one C walk."""
    turbohtml.parse(text).structured_data()


def microdata(text: str) -> None:
    """Extract only the page's Microdata items with turbohtml, the like-for-like of a Microdata-only reader."""
    turbohtml.parse(text).microdata()


def sanitize(text: str) -> None:
    """Sanitize with turbohtml's relaxed policy, reusing a prebuilt sanitizer."""
    _SANITIZER.sanitize(text)


def sanitize_templates(text: str) -> None:
    """Sanitize with SAFE_FOR_TEMPLATES on, collapsing template markers as the C walk keeps each node."""
    _SANITIZER_TEMPLATES.sanitize(text)


def sanitize_report(text: str) -> None:
    """Sanitize with the audit trail on, exercising the removed-node collection."""
    _SANITIZER.sanitize_report(text)


def markup(text: str) -> None:
    """Escape a string into a Markup with turbohtml's markupsafe-compatible escape."""
    _markup_escape(text)


_MARKUP_TEMPLATE = _Markup("<li>{}</li><span>{}</span>")
_MARKUP_JOINER = _Markup(", ")


@functools.cache
def _markup_of(text: str) -> _Markup:
    """Return a Markup wrapping the text, cached so the Markup operations time only the method call."""
    return _Markup(text)


def markup_op(case: tuple[str, object]) -> None:
    """Run one Markup method (striptags/unescape/format/join) on turbohtml's Markup, by case kind."""
    kind, payload = case
    if kind == "striptags":
        _markup_of(cast("str", payload)).striptags()
    elif kind == "unescape":
        _markup_of(cast("str", payload)).unescape()
    elif kind == "format":
        _MARKUP_TEMPLATE.format(*cast("tuple[str, ...]", payload))
    else:
        _MARKUP_JOINER.join(cast("tuple[str, ...]", payload))


def linkify(text: str) -> None:
    """Auto-link URLs and emails in HTML with turbohtml, parsing and rewriting the tree."""
    _linkify(text)


def detect(case: tuple[str, str]) -> None:
    """Scan plain text for links with turbohtml's Detector: find the spans or test for any link."""
    kind, text = case
    if kind == "find":
        _DETECTOR.find(text)
    else:
        _DETECTOR.has_link(text)


def markdown(case: tuple[str, str]) -> None:
    """Convert HTML to Markdown with turbohtml, with the default or the fully-configured option surface."""
    kind, text = case
    if kind == "configured":
        _parsed(text).to_markdown(
            _Markdown(
                inline=_Markdown.Inline(strong="__", emphasis="_"),
                links=_Markdown.Links(style="reference"),
                tables=_Markdown.Tables(pad=True),
                escaping=_Markdown.Escaping(mode="all"),
            )
        )
    else:
        _parsed(text).to_markdown()


def markdown_google(text: str) -> None:
    """Convert a Google Docs export to Markdown with turbohtml's google_doc mode."""
    _parsed(text).to_markdown(_Markdown.google_doc())


def tables(case: tuple[str, str]) -> None:
    """Extract table grids with turbohtml: every table as rows, or the first table keyed by its header."""
    kind, text = case
    if kind == "rows":
        _parsed(text).tables()
    elif (table := _parsed(text).find("table")) is not None:
        table.records()


def article(text: str) -> None:
    """Extract the content body and metadata with turbohtml in one C pass."""
    _parsed(text).article()


def boilerplate(text: str) -> None:
    """Classify every paragraph good or boilerplate with turbohtml's layer over the C main-content scoring."""
    _extract_boilerplate(text)


def date(text: str) -> None:
    """Recover the publication date with turbohtml, scoring the meta, JSON-LD, time, and URL signals off the DOM."""
    _extract_dates(text)


def text_render(text: str) -> None:
    """Render layout-aware visible text with turbohtml's to_text, walking the tree in C."""
    _parsed(text).to_text()


def text_collapsed(text: str) -> None:
    """Join turbohtml's stripped_strings into the collapsed, layout-free word stream."""
    " ".join(_parsed(text).stripped_strings)


def text_main(text: str) -> None:
    """Extract the boilerplate-stripped main text with turbohtml's main_text in one C pass."""
    _parsed(text).main_text()


def text_annotated(text: str) -> None:
    """Render annotated layout text with turbohtml, recording spans for matching elements in C."""
    _parsed(text).to_annotated_text(_ANNOTATION_RULES)


def extract_attr(text: str) -> None:
    """Read every anchor's href by selecting once and reading attr off each node."""
    for anchor in _parsed(text).select("a"):
        anchor.attr("href")


def extract_text(text: str) -> None:
    """Read every anchor's visible text by selecting once and reading text off each node."""
    for anchor in _parsed(text).select("a"):
        _ = anchor.text


def extract_url(case: tuple[str, str]) -> None:
    """Read a document's own URL hint with turbohtml, parsing the string: the base URL or the meta refresh."""
    kind, text = case
    if kind == "base":
        turbohtml.parse(text).base_url(_URL_HINT_BASE)
    else:
        turbohtml.parse(text).meta_refresh(_URL_HINT_BASE)


class _Counter(_TurboHTMLParser):
    """A turbohtml html.parser adapter subclass whose handler does minimal, identical work."""

    def __init__(self) -> None:
        """Start the running tally."""
        super().__init__()
        self.work = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Tally a start tag and its attribute count."""
        self.work += len(tag) + len(attrs)


def htmlparser(text: str) -> None:
    """Drive turbohtml's html.parser adapter with the counting handler."""
    parser = _Counter()
    parser.feed(text)
    parser.close()


def css_path(text: str) -> None:
    """Generate the unique CSS selector that re-finds every element with turbohtml's css_path."""
    for node in _parsed(text).descendants:
        if isinstance(node, turbohtml.Element):
            node.css_path()


def xpath_path(text: str) -> None:
    """Generate the positional XPath that re-finds every element with turbohtml's xpath_path."""
    for node in _parsed(text).descendants:
        if isinstance(node, turbohtml.Element):
            node.xpath_path()


def _count_ext(_context: object, nodes: list[object]) -> float:
    """Count the node-set; a trivial extension registered for the engine."""
    return float(len(nodes))


def _first_two_ext(_context: object, nodes: list[object]) -> list[object]:
    """Return the first two nodes as a node-set; the cheapest non-trivial node-set return."""
    return nodes[:2]


_SVG_NS = {"svg": "http://www.w3.org/2000/svg"}
_COUNT_EXTENSIONS = {(None, "ext_count"): _count_ext}
_NODESET_EXTENSIONS = {(None, "ext_first_two"): _first_two_ext}
_REUSE = turbohtml.XPath("//a[@href]")


@functools.cache
def _div_rows(text: str) -> list[object]:
    """Return the document's <div> elements, cached so the node-set variable case times only the reuse."""
    return [node for node in _parsed(text).xpath("//div") if isinstance(node, turbohtml.Element)]


_XPATH_CALLS: dict[str, Callable[..., object]] = {
    "//div": lambda doc, _text: doc.xpath("//div"),
    "//a[@href]": lambda doc, _text: doc.xpath("//a[@href]"),
    "//div//a[@href]": lambda doc, _text: doc.xpath("//div//a[@href]"),
    "/html/body/div": lambda doc, _text: doc.xpath("/html/body/div"),
    "//div//a[1]": lambda doc, _text: doc.xpath("//div//a[1]"),
    "//a[contains(@href, '/')]": lambda doc, _text: doc.xpath("//a[contains(@href, '/')]"),
    "//div[position() <= 3]": lambda doc, _text: doc.xpath("//div[position() <= 3]"),
    "//a/ancestor::div": lambda doc, _text: doc.xpath("//a/ancestor::div"),
    "//a | //span": lambda doc, _text: doc.xpath("//a | //span"),
    "//*[local-name() = 'a']": lambda doc, _text: doc.xpath("//*[local-name() = 'a']"),
    "count(//a)": lambda doc, _text: doc.xpath("count(//a)"),
    "variable": lambda doc, _text: doc.xpath("//a[@href=$href]", href="/x"),
    "re:test": lambda doc, _text: doc.xpath("//a[re:test(@href, '[0-9]')]"),
    "ends-with": lambda doc, _text: doc.xpath("//a[ends-with(@href, '/')]"),
    "string-join": lambda doc, _text: doc.xpath("string-join(//a/@href, ',')"),
    "lower-case": lambda doc, _text: doc.xpath("//a[lower-case(@href) = @href]"),
    "matches": lambda doc, _text: doc.xpath("//a[matches(@href, '[0-9]')]"),
    "replace": lambda doc, _text: doc.xpath("replace(string(//a/@href), '[0-9]+', '#')"),
    "set:distinct": lambda doc, _text: doc.xpath("set:distinct(//a)"),
    "smart_strings": lambda doc, _text: doc.xpath("//a/@href", smart_strings=True),
    "extension": lambda doc, _text: doc.xpath("ext_count(//a)", extensions=_COUNT_EXTENSIONS),
    "nodeset_extension": lambda doc, _text: doc.xpath("ext_first_two(//a)/@href", extensions=_NODESET_EXTENSIONS),
    "namespaces": lambda doc, _text: doc.xpath("//svg:rect", namespaces=_SVG_NS),
    "node_set_variable": lambda doc, text: doc.xpath("$rows/div", rows=_div_rows(text)),
    "precompiled": lambda doc, _text: _REUSE(doc),
}


def xpath(case: tuple[str, str]) -> None:
    """Evaluate one XPath feature class with turbohtml's compiled-program engine, by case kind."""
    kind, text = case
    _XPATH_CALLS[kind](_parsed(text), text)


def minify_css(css: str) -> str:
    """Minify a stylesheet with turbohtml's value-safe CSS minifier."""
    return _clean.minify_css(css)


def encoding(data: bytes) -> None:
    """Detect a byte stream's character encoding with turbohtml's C sniffing pipeline."""
    _detect_encoding(data)


def translate(selector: str) -> None:
    """Translate one CSS selector to XPath 1.0 with turbohtml's C translator."""
    _css_to_xpath(selector)


def specificity(selector: str) -> None:
    """Weigh a CSS selector list's specificity with turbohtml's C selector parser."""
    _css_specificity(selector)


def urls_clean(case: tuple[str, tuple[str, ...]]) -> None:
    """Run turbohtml's URL scrub-and-normalize (or bare normalize) over the shared URL batch, by case kind."""
    kind, batch = case
    transform = _clean_url if kind == "clean" else _normalize_url
    for url in batch:
        transform(url)


def links_filter(text: str) -> None:
    """Collect the cleaned, deduplicated page links with turbohtml's extract_links, parsing the cold string."""
    _extract_links(text, _LINKS_BASE)


OPERATIONS: dict[str, tuple[object, str]] = {
    "build": (build, "turbohtml"),
    "build-e": (build_e, "turbohtml"),
    "construct": (construct, "turbohtml"),
    "emit": (emit, "turbohtml"),
    "parse": (parse, "turbohtml"),
    "fragment": (fragment, "turbohtml"),
    "escape": (escape, "turbohtml"),
    "unescape": (unescape, "turbohtml"),
    "tokenize": (tokenize, "turbohtml"),
    "find": (find, "turbohtml"),
    "select": (select, "turbohtml"),
    "select-has": (select_has, "turbohtml"),
    "match": (match, "turbohtml"),
    "find-text": (find_text, "turbohtml"),
    "text-content": (text_content, "turbohtml"),
    "serialize": (serialize, "turbohtml"),
    "minify": (minify, "turbohtml"),
    "edit": (Mutating(turbohtml.parse, edit), "turbohtml"),
    "class-edit": (class_edit, "turbohtml"),
    "strip-remove": (strip_remove, "turbohtml"),
    "strip-tags": (strip_tags, "turbohtml"),
    "set-html": (Mutating(turbohtml.parse, set_html), "turbohtml"),
    "set-text": (Mutating(turbohtml.parse, set_text), "turbohtml"),
    "navigate": (navigate, "turbohtml"),
    "chain": (chain, "turbohtml"),
    "links-extract": (links_extract, "turbohtml"),
    "links-absolutize": (Mutating(turbohtml.parse, links_absolutize), "turbohtml"),
    "links-rewrite": (links_rewrite, "turbohtml"),
    "socialcard": (socialcard, "turbohtml"),
    "structured": (structured, "turbohtml"),
    "microdata": (microdata, "turbohtml"),
    "sanitize": (sanitize, "turbohtml"),
    "sanitize-templates": (sanitize_templates, "turbohtml"),
    "sanitize-report": (sanitize_report, "turbohtml"),
    "markup": (markup, "turbohtml"),
    "markup-op": (markup_op, "turbohtml"),
    "linkify": (linkify, "turbohtml"),
    "detect": (detect, "turbohtml"),
    "markdown": (markdown, "turbohtml"),
    "markdown-google": (markdown_google, "turbohtml"),
    "tables": (tables, "turbohtml"),
    "article": (article, "turbohtml"),
    "boilerplate": (boilerplate, "turbohtml"),
    "date": (date, "turbohtml"),
    "text-render": (text_render, "turbohtml"),
    "text-collapsed": (text_collapsed, "turbohtml"),
    "text-main": (text_main, "turbohtml"),
    "text-annotated": (text_annotated, "turbohtml"),
    "extract-attr": (extract_attr, "turbohtml"),
    "extract-text": (extract_text, "turbohtml"),
    "extract-url": (extract_url, "turbohtml"),
    "htmlparser": (htmlparser, "turbohtml"),
    "path": (css_path, "turbohtml"),
    "path-xpath": (xpath_path, "turbohtml"),
    "translate": (translate, "turbohtml"),
    "specificity": (specificity, "turbohtml"),
    "xpath": (xpath, "turbohtml"),
    "minify-css": (minify_css, "turbohtml"),
    "minify-js": (minify_js, "turbohtml"),
    "encoding": (encoding, "turbohtml"),
    "urls-clean": (urls_clean, "turbohtml"),
    "links-filter": (links_filter, "turbohtml"),
}
