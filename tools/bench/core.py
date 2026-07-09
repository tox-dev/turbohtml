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
from turbohtml.conformance import check as _check_conformance
from turbohtml.convert import css_specificity as _css_specificity
from turbohtml.convert import css_to_xpath as _css_to_xpath
from turbohtml.cssom import computed_style as _computed_style
from turbohtml.detect import detect as _detect_encoding
from turbohtml.detect import normalize as _normalize
from turbohtml.extract import boilerplate as _extract_boilerplate
from turbohtml.extract import clean_url as _clean_url
from turbohtml.extract import dates as _extract_dates
from turbohtml.extract import extract_links as _extract_links
from turbohtml.extract import normalize_url as _normalize_url
from turbohtml.migration.markupsafe import Markup as _Markup
from turbohtml.migration.markupsafe import escape as _markup_escape
from turbohtml.migration.stdlib import HTMLParser as _TurboHTMLParser
from turbohtml.query import Query as _Query
from turbohtml.rewrite import Element as _RewriteElement
from turbohtml.rewrite import rewrite as _rewrite
from turbohtml.saxparse import SaxHandler as _SaxHandler
from turbohtml.saxparse import sax_parse as _sax_parse
from turbohtml.transform import Transform as _Transform
from turbohtml.treebuild import parse_into as _parse_into
from turbohtml.validate import RelaxNG as _RelaxNG
from turbohtml.validate import XMLSchema as _XMLSchema

if TYPE_CHECKING:
    from collections.abc import Callable

_SANITIZER = _clean.Sanitizer(_clean.Policy.relaxed())
_SANITIZER_TEMPLATES = _clean.Sanitizer(replace(_clean.Policy.relaxed(), strip_template_markers=True))
_SANITIZER_STYLES = _clean.Sanitizer(
    replace(
        _clean.Policy.relaxed(),
        attributes={"*": frozenset({"style", "title", "lang", "dir"})},
        css_properties=frozenset({"color", "text-align", "font-size"}),
        allowed_styles={"*": {"color": [r"^#[0-9a-f]{3,6}$", r"^rgb\("], "text-align": [r"^left$|^center$|^right$"]}},
    )
)
# rename the deprecated presentational tags a real feed still carries to their modern equivalents in the same walk,
# sanitize-html's transformTags; the added class exercises the attribute-injection path alongside the rename
_RELAXED = _clean.Policy.relaxed()
_SANITIZER_TRANSFORM = _clean.Sanitizer(
    replace(
        _RELAXED,
        attributes={**_RELAXED.attributes, "div": frozenset({"class"})},
        transform_tags={
            "b": "strong",
            "i": "em",
            "big": "span",
            "tt": "code",
            "strike": "s",
            "font": "span",
            "center": _clean.Transform("div", {"class": "center"}),
        },
    )
)
# allow id/name broadly and keep the form controls that carry the clobbering-prone values, so isolation prefixes most
# attributes rather than skipping a value it never touches
_SANITIZER_NAMED_PROPS = _clean.Sanitizer(
    replace(
        _RELAXED,
        attributes={**_RELAXED.attributes, "*": frozenset({"id", "name", "title", "lang", "dir"})},
        tags=_RELAXED.tags | {"form", "input"},
        isolate_named_props=True,
    )
)
# keep an app's own x-* custom elements and their data-* attributes by predicate, DOMPurify's CUSTOM_ELEMENT_HANDLING,
# so the walk runs the tag and attribute matchers on most elements rather than escaping every unlisted one
_SANITIZER_CUSTOM = _clean.Sanitizer(
    replace(
        _RELAXED,
        custom_element_check=re.compile(r"^x-").search,
        custom_attribute_check=lambda _tag, name: name.startswith("data-"),
    )
)
_SANITIZER_XML = _clean.Sanitizer(replace(_clean.Policy.relaxed(), xml=True))
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
_XML = turbohtml.Html(xml=True)  # the XML/XHTML serialization config, reused across the timed calls


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


def shadow(count: int) -> None:
    """
    Attach an open shadow root with a named and a default slot to a host of ``count`` light children, then flatten.

    Exercises the whole shadow-DOM tree model: element construction, attach_shadow, the fragment parse that installs
    the slots, and the flattened-tree walk that resolves every child's assigned slot (the O(count) slot-assignment
    algorithm) into the composed child list.
    """
    host = turbohtml.Element("div")
    for index in range(count):
        attrs = {"slot": "header"} if index % 4 == 0 else None
        host.append(turbohtml.Element("span", attrs, [turbohtml.Text(f"item {index}")]))
    root = host.attach_shadow("open")
    root.set_inner_html('<slot name="header">title</slot><slot>body</slot>')
    _ = list(host.flattened_children)


def parse(text: str) -> None:
    """Parse a whole document into a navigable tree through turbohtml.parse()."""
    turbohtml.parse(text)


def parse_xml(text: str) -> None:
    """Parse a whole XML document into a navigable tree through turbohtml.parse_xml()."""
    turbohtml.parse_xml(text)


_VALIDATORS: dict[str, _XMLSchema] = {}


def validate(case: tuple[str, str]) -> None:
    """Validate a parsed XML document against an XSD schema (compiled once, keyed by source)."""
    schema, document = case
    validator = _VALIDATORS.get(schema)
    if validator is None:
        validator = _VALIDATORS[schema] = _XMLSchema(schema)
    validator.validate(turbohtml.parse_xml(document))


_RNG_VALIDATORS: dict[str, _RelaxNG] = {}


def validate_rng(case: tuple[str, str]) -> None:
    """Validate a parsed XML document against a RELAX NG schema (compiled once, keyed by source)."""
    schema, document = case
    validator = _RNG_VALIDATORS.get(schema)
    if validator is None:
        validator = _RNG_VALIDATORS[schema] = _RelaxNG(schema)
    validator.validate(turbohtml.parse_xml(document))


def parse_scripting(text: str) -> None:
    """Parse with the WHATWG scripting flag on, so <noscript> content tokenizes as raw text."""
    turbohtml.parse(text, scripting=True)


def parse_locations(text: str) -> None:
    """Parse recording the granular start/end-tag and per-attribute source spans (parse5's sourceCodeLocationInfo)."""
    turbohtml.parse(text, source_locations=True)


def parse_shadow(text: str) -> None:
    """Parse a document whose <template shadowrootmode> elements attach declarative shadow roots to their parents."""
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


def computed_style(text: str) -> None:
    """Resolve the CSSOM computed style of every element in the parsed, styled document."""
    for node in _parsed(text).descendants:
        if isinstance(node, turbohtml.Element):
            _computed_style(node)


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


def conformance(text: str) -> None:
    """Run the HTML5 authoring-conformance checks over a parsed document."""
    _check_conformance(_parsed(text))


def serialize_xml(text: str) -> None:
    """Serialize a parsed document to well-formed XML/XHTML with turbohtml's Html(xml=True) option."""
    _ = _parsed(text).serialize(_XML)


def canonicalize(text: str) -> None:
    """Canonicalize a parsed document to Canonical XML (c14n) with turbohtml's canonicalize method."""
    _ = _parsed(text).canonicalize()


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


def _parse_source_locations(text: str) -> turbohtml.Document:
    """Parse a fresh tree recording source locations, the setup a lossless re-emit needs."""
    return turbohtml.parse(text, source_locations=True)


def lossless_serialize(document: turbohtml.Document) -> str:
    """Tag every link rel=nofollow, then re-emit with to_source so only the edited start tags rebuild."""
    for anchor in document.find_all("a"):
        anchor.attrs["rel"] = "nofollow"
    return document.to_source()


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


def observe(document: turbohtml.Document) -> None:
    """Watch a whole subtree and drain the records a batch of link edits queues with turbohtml's MutationObserver."""
    observer = turbohtml.MutationObserver()
    observer.observe(document, child_list=True, attributes=True, subtree=True)
    for anchor in document.find_all("a"):
        anchor.attrs["data-seen"] = "1"
    observer.take_records()


def navigate(text: str) -> None:
    """Walk every descendant node with turbohtml's descendants iterator."""
    for _node in _parsed(text).descendants:
        pass


def treewalk(text: str) -> None:
    """Walk every element of a parsed document through a filtered TreeWalker cursor."""
    walker = turbohtml.TreeWalker(_parsed(text), turbohtml.NodeFilter.SHOW_ELEMENT)
    while walker.next_node() is not None:
        pass


def chain(text: str) -> None:
    """Run a fluent jQuery-style chain with turbohtml's Query wrapper."""
    _Query(_parsed(text))("a").filter("[href]").eq(0).add_class("seen").attr("href")


def range_clone(text: str) -> None:
    """Clone a DOM Range spanning the whole <body> subtree with turbohtml's Range.clone_contents."""
    body = _parsed(text).find_all("body")[0]
    span = turbohtml.Range(body, 0)
    span.set_end(body, len(body.children))
    span.clone_contents()


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


def syndication(text: str) -> None:
    """Normalize an RSS/Atom feed into a Feed of Entry records with turbohtml (parse plus one C walk)."""
    turbohtml.parse(text).feed()


def sanitize(text: str) -> None:
    """Sanitize with turbohtml's relaxed policy, reusing a prebuilt sanitizer."""
    _SANITIZER.sanitize(text)


def sanitize_templates(text: str) -> None:
    """Sanitize with SAFE_FOR_TEMPLATES on, collapsing template markers as the C walk keeps each node."""
    _SANITIZER_TEMPLATES.sanitize(text)


def sanitize_named_props(text: str) -> None:
    """Sanitize with SANITIZE_NAMED_PROPS on, prefixing each kept id/name value in the same C walk."""
    _SANITIZER_NAMED_PROPS.sanitize(text)


def sanitize_report(text: str) -> None:
    """Sanitize with the audit trail on, exercising the removed-node collection."""
    _SANITIZER.sanitize_report(text)


def sanitize_styles(text: str) -> None:
    """Sanitize with a per-property style value allowlist, exercising the allowed_styles scrub path."""
    _SANITIZER_STYLES.sanitize(text)


def sanitize_transform(text: str) -> None:
    """Sanitize with a tag-transform map, renaming deprecated presentational tags in the same C walk."""
    _SANITIZER_TRANSFORM.sanitize(text)


def sanitize_custom_elements(text: str) -> None:
    """Sanitize keeping an app's x-* custom elements and their data-* attributes through the predicate path."""
    _SANITIZER_CUSTOM.sanitize(text)


def sanitize_xml(text: str) -> None:
    """Sanitize to well-formed XML/XHTML, exercising the XML serializer's self-close and escaping path."""
    _SANITIZER_XML.sanitize(text)


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


class _SaxCounter(_SaxHandler):
    """A SAX handler whose start-tag callback does the same minimal work as the html.parser counters."""

    def __init__(self) -> None:
        """Start the running tally."""
        self.work = 0

    def start_element(self, tag: str, attrs: tuple[tuple[str, str | None], ...]) -> None:
        """Tally a start tag and its attribute count."""
        self.work += len(tag) + len(attrs)


def sax(text: str) -> None:
    """Parse the whole document into SAX events, dispatched to a counting handler, retaining no tree."""
    _sax_parse(text, _SaxCounter())


class _Node:
    """A compact tree node a turbohtml.treebuild builder materializes: a tag and its children, no navigable Node."""

    __slots__ = ("children", "tag")

    def __init__(self, tag: str) -> None:
        self.tag = tag
        self.children: list[_Node] = []


class _NodeBuilder:
    """Retarget the parser at :class:`_Node`; parse_into binds each method per instance, hence the PLR6301 waivers."""

    def create_document(self) -> _Node:  # noqa: PLR6301
        return _Node("#document")

    def create_doctype(self, name: str, public_id: str | None, system_id: str | None) -> _Node:  # noqa: ARG002, PLR6301
        return _Node("#doctype")

    def create_element(self, name: str, namespace: str, attrs: tuple[tuple[str, str | None], ...]) -> _Node:  # noqa: ARG002, PLR6301
        return _Node(name)

    def create_text(self, data: str) -> _Node:  # noqa: ARG002, PLR6301
        return _Node("#text")

    def create_comment(self, data: str) -> _Node:  # noqa: ARG002, PLR6301
        return _Node("#comment")

    def create_pi(self, data: str) -> _Node:  # noqa: ARG002, PLR6301
        return _Node("#pi")

    def append(self, parent: _Node, child: _Node) -> None:  # noqa: PLR6301
        parent.children.append(child)


def treebuild(text: str) -> None:
    """Retarget the parser at a custom tree, materializing it directly and never building a navigable Node."""
    _parse_into(text, _NodeBuilder())


def _rewrite_rel(element: _RewriteElement) -> None:
    """Mark a link rel=nofollow as the streaming rewriter visits it."""
    element.set_attribute("rel", "nofollow")


def _rewrite_lazy(element: _RewriteElement) -> None:
    """Defer an image's load as the streaming rewriter visits it."""
    element.set_attribute("loading", "lazy")


def _rewrite_strip_comment(comment: _RewriteElement) -> None:
    """Drop a comment as the streaming rewriter visits it."""
    comment.remove()


def rewrite(text: str) -> None:
    """Rewrite the whole document in one streaming pass -- retag links, lazy-load images, drop comments -- no tree."""
    _rewrite(
        text,
        elements=(("a[href]", _rewrite_rel), ("img", _rewrite_lazy)),
        comments=_rewrite_strip_comment,
    )


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


@functools.cache
def _xslt_compiled(sheet: str, source: str) -> tuple[_Transform, turbohtml.Document]:
    """Compile the stylesheet and parse the source once, so the op times only the transformation."""
    return _Transform(turbohtml.parse_xml(sheet)), turbohtml.parse_xml(source)


def transform(case: tuple[str, str]) -> None:
    """Apply a compiled XSLT 1.0 stylesheet to a parsed source document with turbohtml.transform."""
    sheet, source = case
    compiled, document = _xslt_compiled(sheet, source)
    compiled(document)


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


def stream(text: str) -> None:
    """Push a document through IncrementalParser in 4 kB chunks, the streaming build the one-shot parse skips."""
    parser = turbohtml.IncrementalParser()
    for start in range(0, len(text), 4096):
        parser.feed(text[start : start + 4096])
    parser.close()


def encoding(data: bytes) -> None:
    """Detect a byte stream's character encoding with turbohtml's C sniffing pipeline."""
    _detect_encoding(data)


def decode(case: tuple[str, bytes]) -> None:
    """Decode a legacy byte stream with turbohtml's WHATWG decoders, reached through the codecs detect registers."""
    label, data = case
    data.decode(f"whatwg-{label}")


def normalize(text: str) -> None:
    """Normalize text to Unicode NFC with turbohtml's C engine (quick-checked, then decompose/reorder/compose)."""
    _normalize("NFC", text)


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
    "shadow": (shadow, "turbohtml"),
    "parse": (parse, "turbohtml"),
    "parse-xml": (parse_xml, "turbohtml"),
    "validate": (validate, "turbohtml"),
    "validate-rng": (validate_rng, "turbohtml"),
    "parse-scripting": (parse_scripting, "turbohtml"),
    "parse-locations": (parse_locations, "turbohtml"),
    "parse-shadow": (parse_shadow, "turbohtml"),
    "fragment": (fragment, "turbohtml"),
    "escape": (escape, "turbohtml"),
    "unescape": (unescape, "turbohtml"),
    "tokenize": (tokenize, "turbohtml"),
    "find": (find, "turbohtml"),
    "select": (select, "turbohtml"),
    "select-has": (select_has, "turbohtml"),
    "computed-style": (computed_style, "turbohtml"),
    "computed-style-dense": (computed_style, "turbohtml"),
    "match": (match, "turbohtml"),
    "find-text": (find_text, "turbohtml"),
    "text-content": (text_content, "turbohtml"),
    "serialize": (serialize, "turbohtml"),
    "conformance": (conformance, "turbohtml"),
    "serialize-xml": (serialize_xml, "turbohtml"),
    "canonicalize": (canonicalize, "turbohtml"),
    "canonicalize-deep": (canonicalize, "turbohtml"),
    "lossless-serialize": (Mutating(_parse_source_locations, lossless_serialize), "turbohtml"),
    "minify": (minify, "turbohtml"),
    "edit": (Mutating(turbohtml.parse, edit), "turbohtml"),
    "class-edit": (class_edit, "turbohtml"),
    "strip-remove": (strip_remove, "turbohtml"),
    "strip-tags": (strip_tags, "turbohtml"),
    "set-html": (Mutating(turbohtml.parse, set_html), "turbohtml"),
    "set-text": (Mutating(turbohtml.parse, set_text), "turbohtml"),
    "observe": (Mutating(turbohtml.parse, observe), "turbohtml"),
    "navigate": (navigate, "turbohtml"),
    "treewalk": (treewalk, "turbohtml"),
    "chain": (chain, "turbohtml"),
    "range-clone": (range_clone, "turbohtml"),
    "links-extract": (links_extract, "turbohtml"),
    "links-absolutize": (Mutating(turbohtml.parse, links_absolutize), "turbohtml"),
    "links-rewrite": (links_rewrite, "turbohtml"),
    "socialcard": (socialcard, "turbohtml"),
    "structured": (structured, "turbohtml"),
    "microdata": (microdata, "turbohtml"),
    "syndication": (syndication, "turbohtml"),
    "sanitize": (sanitize, "turbohtml"),
    "sanitize-templates": (sanitize_templates, "turbohtml"),
    "sanitize-named-props": (sanitize_named_props, "turbohtml"),
    "sanitize-report": (sanitize_report, "turbohtml"),
    "sanitize-styles": (sanitize_styles, "turbohtml"),
    "sanitize-transform": (sanitize_transform, "turbohtml"),
    "sanitize-custom-elements": (sanitize_custom_elements, "turbohtml"),
    "sanitize-xml": (sanitize_xml, "turbohtml"),
    "markup": (markup, "turbohtml"),
    "markup-op": (markup_op, "turbohtml"),
    "linkify": (linkify, "turbohtml"),
    "detect": (detect, "turbohtml"),
    "normalize": (normalize, "turbohtml"),
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
    "sax": (sax, "turbohtml"),
    "treebuild": (treebuild, "turbohtml"),
    "rewrite": (rewrite, "turbohtml"),
    "path": (css_path, "turbohtml"),
    "path-xpath": (xpath_path, "turbohtml"),
    "translate": (translate, "turbohtml"),
    "specificity": (specificity, "turbohtml"),
    "xpath": (xpath, "turbohtml"),
    "transform": (transform, "turbohtml"),
    "transform-dense": (transform, "turbohtml"),
    "minify-css": (minify_css, "turbohtml"),
    "minify-js": (minify_js, "turbohtml"),
    "stream": (stream, "turbohtml"),
    "encoding": (encoding, "turbohtml"),
    "decode": (decode, "turbohtml"),
    "urls-clean": (urls_clean, "turbohtml"),
    "links-filter": (links_filter, "turbohtml"),
}
