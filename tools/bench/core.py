"""
turbohtml's own timing for every operation: the shared baseline each competitor divides into.

This module imports turbohtml and nothing else, so it loads only in the turbohtml-only ``core`` venv. ``OPERATIONS``
maps each operation to ``(timing function, label)``; the function takes the same case input the competitor receives.
"""

from __future__ import annotations

import functools
import re
import subprocess
import sys
from collections import deque
from dataclasses import replace
from typing import TYPE_CHECKING, Final, cast

import turbohtml
from bench.timing import Mutating
from turbohtml import Markdown as _Markdown
from turbohtml import Range as _Range
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
from turbohtml.detect import EncodingDetector as _EncodingDetector
from turbohtml.detect import detect as _detect_encoding
from turbohtml.detect import detect_language as _detect_language
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
from turbohtml.query import escape_identifier as _escape_identifier
from turbohtml.rewrite import Element as _RewriteElement
from turbohtml.rewrite import rewrite as _rewrite
from turbohtml.saxparse import SaxHandler as _SaxHandler
from turbohtml.saxparse import iter_events as _iter_events
from turbohtml.saxparse import sax_parse as _sax_parse
from turbohtml.transform import Transform as _Transform
from turbohtml.treebuild import parse_into as _parse_into
from turbohtml.validate import RelaxNG as _RelaxNG
from turbohtml.validate import XMLSchema as _XMLSchema

if TYPE_CHECKING:
    from collections.abc import Callable

    from turbohtml import Node

_SANITIZER = _clean.Sanitizer(_clean.Policy.relaxed())
_SANITIZER_ATTRIBUTES: Final = _clean.Sanitizer(
    _clean.Policy(tags=frozenset({"p"}), attribute_prefixes=frozenset({"data-"}))
)
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
_FIND_TEXT_OVERLAP_PATTERN: Final[re.Pattern[str]] = re.compile("a" * 4096 + "b")
_CSS = "div a[href]"  # a descendant combinator with an attribute test, common in scrapers
_HAS = "div:has(a)"  # the :has() relational pseudo-class
_STRIP = "code, a, q"  # a bulk set of tags to drop or unwrap
_SET_HTML = "<p>Updated <a href='/x'>link</a> and <b>bold</b>.</p><ul><li>one</li><li>two</li></ul>"
_SET_TEXT = "Replacement text, escaped & verbatim."
_DETECTOR = _LinkDetector()
_PHONE_DETECTORS: Final[dict[str, _LinkDetector]] = {
    "valid": _LinkDetector(phones=_clean.PhoneNumbers(regions=("US",))),
    "possible": _LinkDetector(phones=_clean.PhoneNumbers(regions=("US",), require_valid=False)),
    "regions-8": _LinkDetector(phones=_clean.PhoneNumbers(regions=("US", "GB", "DE", "IN", "BR", "JP", "FR", "AU"))),
}
_PHONE_STYLES: Final[dict[str, _clean.PhoneFormat]] = {style.value: style for style in _clean.PhoneFormat}
_PHONE_PARSED: Final[dict[tuple[str, str], _clean.PhoneNumber]] = {}  # the format op times formatting, not the parse
_LINKER: Final[_clean.Linker] = _clean.Linker()
_LINKER_SKIP: Final[_clean.Linker] = _clean.Linker(_clean.Linkify(skip_tags=("code",)))
_LINKER_CALLBACKS: Final[_clean.Linker] = _clean.Linker(
    _clean.Linkify(callbacks=(_clean.nofollow, _clean.target_blank), process_existing=True)
)
_ANNOTATION_RULES = {"h1": ["heading"], "b": ["emphasis"], "a": ["link"]}
_INNER_INDENT: Final = turbohtml.Html(layout=turbohtml.Indent())
_INNER_MINIFY: Final = turbohtml.Html(layout=turbohtml.Minify())
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


def _shadow_slot(case: tuple[int, str]) -> None:
    _slotted(case).assigned_nodes()


@functools.cache
def _slotted(case: tuple[int, str]) -> turbohtml.Element:
    host: Final[turbohtml.Element] = turbohtml.Element("div")
    host.set_inner_html(case[1])
    root: Final[turbohtml.ShadowRoot] = host.attach_shadow("open")
    root.set_inner_html('<slot name="unused"></slot>' * case[0] + '<slot name="target"></slot>')
    return root.select('slot[name="target"]')[0]


def startup(mode: str) -> str:
    """Include interpreter startup so cached imports cannot hide wheel initialization costs."""
    return subprocess.run(
        [sys.executable, "-c", "import turbohtml; print(turbohtml.__version__)"]
        if mode == "import"
        else [sys.executable, "-m", "turbohtml", "minify"],
        input="<p>a  b</p><!-- c -->",
        capture_output=True,
        text=True,
        check=True,
    ).stdout


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


def _is_valid(case: tuple[str, str]) -> None:
    schema, document = case
    if (validator := _VALIDATORS.get(schema)) is None:
        validator = _VALIDATORS[schema] = _XMLSchema(schema)
    validator.is_valid(turbohtml.parse_xml(document))


_RNG_VALIDATORS: dict[str, _RelaxNG] = {}


def validate_rng(case: tuple[str, str]) -> None:
    """Validate a parsed XML document against a RELAX NG schema (compiled once, keyed by source)."""
    schema, document = case
    validator = _RNG_VALIDATORS.get(schema)
    if validator is None:
        validator = _RNG_VALIDATORS[schema] = _RelaxNG(schema)
    validator.validate(turbohtml.parse_xml(document))


def _is_valid_rng(case: tuple[str, str]) -> None:
    schema, document = case
    if (validator := _RNG_VALIDATORS.get(schema)) is None:
        validator = _RNG_VALIDATORS[schema] = _RelaxNG(schema)
    validator.is_valid(turbohtml.parse_xml(document))


def compile_rng(schema: str) -> None:
    """Measure schema construction rather than the cached validation path."""
    _RelaxNG(schema)


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


def _render_options_setup(case: tuple[str, bool]) -> Callable[[], str | bytes]:
    node: Final = turbohtml.Element("p", children=[turbohtml.Text("x")])
    kind, supplied = case
    if kind == "html":
        return functools.partial(node.serialize, turbohtml.Html()) if supplied else node.serialize
    if kind == "text":
        return functools.partial(node.to_text, turbohtml.PlainText()) if supplied else node.to_text
    return functools.partial(node.canonicalize, turbohtml.Canonical()) if supplied else node.canonicalize


def _render_options(render: Callable[[], str | bytes]) -> str | bytes:
    return render()


def _token_attribute_setup(text: str) -> turbohtml.Token:
    return next(iter(turbohtml.tokenize(text)))


def _token_attributes(token: turbohtml.Token) -> list[tuple[str, str]] | None:
    return token.attrs


def _tokenize_attributes(text: str) -> None:
    for token in turbohtml.tokenize(text):
        _ = token.attrs


@functools.cache
def _parsed(text: str) -> turbohtml.Document:
    """Return a document parsed once, cached so the read-path operations time only the query."""
    return turbohtml.parse(text)


def _whole(text: str) -> turbohtml.Document:
    """
    Parse the string afresh, for an operation whose competitors take a string rather than a tree.

    turbohtml exposes these as node methods, so timing them off the cached tree would compare a conversion against
    every competitor's parse plus conversion. The comparison these tables make is end to end, string in and result
    out, so the parse belongs inside the measurement on both sides.
    """
    return turbohtml.parse(text)


def find(text: str) -> None:
    """Collect every anchor with turbohtml's find_all."""
    _parsed(text).find_all("a")


def _find_cold(case: tuple[str, turbohtml.Document]) -> None:
    query_kind, document = case
    if query_kind == "find":
        document.find("a")
    elif query_kind == "all":
        document.find_all("a")
    else:
        document.find_all("a", limit=1)


def _find_cold_setup(case: tuple[str, str]) -> tuple[str, turbohtml.Document]:
    query_kind, source = case
    return query_kind, turbohtml.parse(source)


def select(text: str) -> None:
    """Run the CSS selector with turbohtml's select."""
    _parsed(text).select(_CSS)


def select_has(text: str) -> None:
    """Run the :has() relational selector with turbohtml's select."""
    _parsed(text).select(_HAS)


def _select_scaling(case: tuple[str, str]) -> None:
    _parsed(case[1]).select(case[0])


_XPATH_REPLACE_DOCUMENT: Final = turbohtml.parse("<p></p>")


def _xpath_replace(case: tuple[str, str, str, str]) -> str | list[turbohtml.Element | str]:
    text, search, replacement, _expected = case
    return _XPATH_REPLACE_DOCUMENT.xpath(
        "str:replace($text, $search, $replacement)", text=text, search=search, replacement=replacement
    )


def _xpath_scaling(case: tuple[str, str]) -> None:
    _parsed(case[1]).xpath(case[0])


def computed_style(text: str) -> None:
    """Resolve the CSSOM computed style of every element in the parsed, styled document."""
    for node in _parsed(text).descendants:
        if isinstance(node, turbohtml.Element):
            _computed_style(node)


def _computed_style_reverse(text: str) -> None:
    for node in reversed(tuple(_parsed(text).descendants)):
        if isinstance(node, turbohtml.Element):
            _computed_style(node)


def _computed_style_first(text: str) -> None:
    _computed_style(turbohtml.parse(text).select("div")[0])


def match(text: str) -> None:
    """Test every anchor against a selector with the soupsieve-shaped turbohtml.query matcher."""
    matcher = _query.compile(_CSS)
    for anchor in _parsed(text).find_all("a"):
        matcher.match(anchor)


def find_text(text: str) -> None:
    """Collect every element whose collected text matches the regex with turbohtml's find_all."""
    _parsed(text).find_all(text=_FIND_TEXT_PATTERN)


def find_text_exact(case: tuple[str, str]) -> None:
    """Reuse the parsed tree to isolate exact descendant-text matching."""
    _parsed(case[0]).find_all("div", text=case[1])


def find_attr_presence(case: tuple[str, bool]) -> None:
    """Reuse the parsed tree to isolate presence checks from parsing attribute values."""
    _parsed(case[0]).find_all("p", attrs={"data-x": case[1]})


def find_text_overlap(text: str) -> None:
    """Exercise the overlap that makes a naive literal substring scan quadratic."""
    _parsed(text).find(text=_FIND_TEXT_OVERLAP_PATTERN)


def text_content(text: str) -> None:
    """Collect the document's visible text with turbohtml's text property."""
    _ = _parsed(text).text


def serialize(text: str) -> None:
    """Serialize a parsed document back to HTML with turbohtml's html property."""
    _ = _parsed(text).html


def _serialize_attributes(case: tuple[str, bool]) -> str:
    return _parsed(case[0]).serialize(turbohtml.Html(sort_attributes=case[1]))


def _parse_inner(text: str) -> str:
    return turbohtml.parse(text).find_all("body")[0].serialize(inner=True)


def _parse_inner_encode(text: str) -> bytes:
    return turbohtml.parse(text).find_all("body")[0].encode(inner=True)


def _serialize_inner(text: str) -> None:
    _parsed_body(text).serialize(inner=True)


def _serialize_inner_indent(text: str) -> None:
    _parsed_body(text).serialize(_INNER_INDENT, inner=True)


def _serialize_inner_minify(text: str) -> None:
    _parsed_body(text).serialize(_INNER_MINIFY, inner=True)


def _encode_inner(text: str) -> None:
    _parsed_body(text).encode(inner=True)


def _encode_inner_indent(text: str) -> None:
    _parsed_body(text).encode(options=_INNER_INDENT, inner=True)


def _encode_inner_minify(text: str) -> None:
    _parsed_body(text).encode(options=_INNER_MINIFY, inner=True)


def _iterate_inner(text: str) -> None:
    deque(_parsed_body(text).serialize_iter(inner=True), maxlen=0)


def _iterate_inner_indent(text: str) -> None:
    deque(_parsed_body(text).serialize_iter(_INNER_INDENT, inner=True), maxlen=0)


def _transform_dispatch(count: int) -> None:
    _dispatch_pipeline(count)()


@functools.cache
def _dispatch_pipeline(count: int) -> Callable[[], turbohtml.Node]:
    return functools.partial(_clean.transform_node, turbohtml.parse_fragment("<p>x</p>"), *(_identity_node,) * count)


def _identity_node(node: turbohtml.Node) -> turbohtml.Node:
    return node


@functools.cache
def _parsed_body(text: str) -> turbohtml.Element:
    return _parsed(text).find_all("body")[0]


def _whitespace_roundtrip(document: turbohtml.Document) -> str:
    _clean.collapse_whitespace_node(document)
    return document.serialize()


def _transform_tree(document: turbohtml.Document) -> None:
    _clean.transform_node(document, _clean.strip_comments_node, _clean.collapse_whitespace_node)


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


def sanitize_attributes(text: str) -> None:
    """Reuse the policy to isolate attribute handling from sanitizer construction."""
    _SANITIZER_ATTRIBUTES.sanitize(text)


def sanitize_templates(text: str) -> None:
    """Sanitize with SAFE_FOR_TEMPLATES on, collapsing template markers as the C walk keeps each node."""
    _SANITIZER_TEMPLATES.sanitize(text)


def sanitize_named_props(text: str) -> None:
    """Sanitize with SANITIZE_NAMED_PROPS on, prefixing each kept id/name value in the same C walk."""
    _SANITIZER_NAMED_PROPS.sanitize(text)


def sanitize_report(text: str) -> None:
    """Sanitize with the audit trail on, exercising the removed-node collection."""
    _SANITIZER.sanitize_report(text)


def sanitize_node(node: Node) -> Node:
    """Sanitize an already parsed subtree with the relaxed policy, the parse-once pipeline's step."""
    return _SANITIZER.sanitize_node(node)


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


def linkify_node(node: Node) -> Node:
    """Fresh-tree setup keeps previously linked input out of later iterations."""
    return _LINKER.linkify_node(node)


def linkify_traversal(case: tuple[str, str]) -> None:
    """Reuse compiled options so the benchmark isolates traversal."""
    kind, text = case
    if kind == "skip":
        _LINKER_SKIP.linkify(text)
    elif kind == "callbacks":
        _LINKER_CALLBACKS.linkify(text)
    else:
        _LINKER.linkify(text)


def detect(case: tuple[str, str]) -> None:
    """Scan plain text for links with turbohtml's Detector: find the spans or test for any link."""
    kind, text = case
    if kind == "find":
        _DETECTOR.find(text)
    else:
        _DETECTOR.has_link(text)


def phone(case: tuple[str, str]) -> None:
    """Scan plain text for phone numbers with turbohtml's LinkDetector: one mode per case, or the presence test."""
    mode, text = case
    if mode == "has":
        _PHONE_DETECTORS["valid"].has_link(text)
    else:
        _PHONE_DETECTORS[mode].find(text)


def phone_parse(case: tuple[str, tuple[tuple[str, str], ...]]) -> None:
    """Read each held string as one number with turbohtml's PhoneNumber.parse, at the case's leniency."""
    mode, held = case
    for region, text in held:
        _clean.PhoneNumber.parse(text, regions=(region,), require_valid=mode == "valid")


def phone_format(case: tuple[str, tuple[tuple[str, str], ...]]) -> None:
    """Write each number in the case's layout with turbohtml's PhoneNumber.format."""
    style, held = case
    for region, text in held:
        if (number := _PHONE_PARSED.get((region, text))) is None:
            number = _PHONE_PARSED[region, text] = _clean.PhoneNumber.parse(text, regions=(region,))
        number.format(_PHONE_STYLES[style])


def markdown(case: tuple[str, str]) -> None:
    """Convert HTML to Markdown with turbohtml, with the default or the fully-configured option surface."""
    kind, text = case
    if kind == "configured":
        _whole(text).to_markdown(
            _Markdown(
                inline=_Markdown.Inline(strong="__", emphasis="_"),
                links=_Markdown.Links(style="reference"),
                tables=_Markdown.Tables(pad=True),
                escaping=_Markdown.Escaping(mode="all"),
            )
        )
    else:
        _whole(text).to_markdown()


def markdown_wrap(case: tuple[int, str]) -> None:
    """Reuse the tree and options to exclude their construction from wrapping measurements."""
    config, document = _markdown_wrap_case(case)
    document.to_markdown(config)


@functools.cache
def _markdown_wrap_case(case: tuple[int, str]) -> tuple[_Markdown, turbohtml.Document]:
    width, source = case
    return _Markdown(wrapping=_Markdown.Wrapping(width=width)), _whole(source)


def markdown_google(text: str) -> None:
    """Convert a Google Docs export to Markdown with turbohtml's google_doc mode."""
    _whole(text).to_markdown(_Markdown.google_doc())


def tables(case: tuple[str, str]) -> None:
    """Extract table grids with turbohtml: every table as rows, or the first table keyed by its header."""
    kind, text = case
    if kind == "rows":
        _whole(text).tables()
    elif (table := _whole(text).find("table")) is not None:
        table.records()


def article(text: str) -> None:
    """Extract the content body and metadata with turbohtml in one C pass."""
    _whole(text).article()


def boilerplate(text: str) -> None:
    """Classify every paragraph good or boilerplate with turbohtml's layer over the C main-content scoring."""
    _extract_boilerplate(text)


def date(text: str) -> None:
    """Recover the publication date with turbohtml, scoring the meta, JSON-LD, time, and URL signals off the DOM."""
    _extract_dates(text)


def text_render(text: str) -> None:
    """Render layout-aware visible text with turbohtml's to_text, walking the tree in C."""
    _whole(text).to_text()


def text_collapsed(text: str) -> None:
    """Join turbohtml's stripped_strings into the collapsed, layout-free word stream."""
    " ".join(_whole(text).stripped_strings)


def text_main(text: str) -> None:
    """Extract the boilerplate-stripped main text with turbohtml's main_text in one C pass."""
    _whole(text).main_text()


def text_annotated(text: str) -> None:
    """Render annotated layout text with turbohtml, recording spans for matching elements in C."""
    _whole(text).to_annotated_text(_ANNOTATION_RULES)


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


def _sax_records(text: str) -> None:
    deque(_iter_events(text), maxlen=0)


class _Node:
    """A compact tree node a turbohtml.treebuild builder materializes: a tag and its children, no navigable Node."""

    __slots__ = ("children", "tag")

    def __init__(self, tag: str) -> None:
        self.tag = tag
        self.children: list[_Node] = []


class _NodeBuilder:
    """Retarget the parser at :class:`_Node`; parse_into binds each method per instance, hence the PLR6301 waivers."""

    def create_document(self) -> _Node:  # ruff:ignore[no-self-use]
        return _Node("#document")

    def create_doctype(self, name: str, public_id: str | None, system_id: str | None) -> _Node:  # ruff:ignore[unused-method-argument, no-self-use]
        return _Node("#doctype")

    def create_element(self, name: str, namespace: str, attrs: tuple[tuple[str, str | None], ...]) -> _Node:  # ruff:ignore[unused-method-argument, no-self-use]
        return _Node(name)

    def create_text(self, data: str) -> _Node:  # ruff:ignore[unused-method-argument, no-self-use]
        return _Node("#text")

    def create_comment(self, data: str) -> _Node:  # ruff:ignore[unused-method-argument, no-self-use]
        return _Node("#comment")

    def create_pi(self, target: str, data: str) -> _Node:  # ruff:ignore[unused-method-argument, no-self-use]
        return _Node("#pi")

    def append(self, parent: _Node, child: _Node) -> None:  # ruff:ignore[no-self-use]
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


def rewrite_attributes(case: tuple[int, str]) -> None:
    """Exclude attribute-name preparation from steady-state timing."""
    _rewrite(case[1], elements=(("x", _rewrite_attribute_handler(case[0])),))


@functools.cache
def _rewrite_attribute_handler(count: int) -> Callable[[_RewriteElement], None]:
    names: Final = tuple(f"a{index}" for index in range(count))

    def add(element: _RewriteElement) -> None:
        for name in names:
            element.set_attribute(name, "x")

    return add


def css_path(text: str) -> None:
    """Exclude parsing from CSS path timing."""
    _css_paths(_parsed(text))


def _css_paths(document: turbohtml.Document) -> None:
    for node in document.descendants:
        if isinstance(node, turbohtml.Element):
            node.css_path()


def xpath_path(text: str) -> None:
    """Exclude parsing from XPath path timing."""
    _xpath_paths(_parsed(text))


def _xpath_paths(document: turbohtml.Document) -> None:
    for node in document.descendants:
        if isinstance(node, turbohtml.Element):
            node.xpath_path()


def _last_path_element(text: str) -> turbohtml.Element:
    return turbohtml.parse(text).select("li")[-1]


def _css_paths_after_class_edits(document: turbohtml.Document) -> None:
    for node in document.select("li"):
        node.attrs["class"] = "marked"
        node.css_path()


@functools.cache
def _xslt_sheet(sheet: str) -> turbohtml.Document:
    """Keep XML parsing outside compile timing."""
    return turbohtml.parse_xml(sheet)


def transform_compile(case: tuple[str, str]) -> None:
    """Measure construction without source evaluation."""
    sheet, _source = case
    _Transform(_xslt_sheet(sheet))


@functools.cache
def _xslt_compiled(sheet: str, source: str) -> tuple[_Transform, turbohtml.Document]:
    """Keep construction and source parsing outside application timing."""
    return _Transform(turbohtml.parse_xml(sheet)), turbohtml.parse_xml(source)


def transform(case: tuple[str, str]) -> str:
    """Apply a compiled XSLT 1.0 stylesheet to a parsed source document with turbohtml.transform."""
    sheet, source = case
    compiled, document = _xslt_compiled(sheet, source)
    return compiled(document)


def transform_reuse(case: tuple[str, str]) -> None:
    """Apply one compiled stylesheet ten times."""
    sheet, source = case
    compiled, document = _xslt_compiled(sheet, source)
    for _ in range(10):
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
_ID_REUSE = turbohtml.XPath("id('" + " ".join(f"r{index}" for index in range(1_000)) + "')")


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


def xpath_id(text: str) -> None:
    """Resolve a large precompiled id() token set against a cached parsed document."""
    _ID_REUSE(_parsed(text))


def minify_css(css: str) -> str:
    """Minify a stylesheet with turbohtml's value-safe CSS minifier."""
    return _clean.minify_css(css)


def stream(text: str) -> None:
    """Push a document through IncrementalParser in 4 kB chunks, the streaming build the one-shot parse skips."""
    parser = turbohtml.IncrementalParser()
    for start in range(0, len(text), 4096):
        parser.feed(text[start : start + 4096])
    parser.close()


def _encoding_stream(data: bytes) -> None:
    detector: Final = _EncodingDetector()
    detector.feed(data)
    detector.close()


def encoding(data: bytes) -> None:
    """Detect a byte stream's character encoding with turbohtml's C sniffing pipeline."""
    _detect_encoding(data)


def decode(case: tuple[str, bytes]) -> None:
    """Decode a legacy byte stream with turbohtml's WHATWG decoders, reached through the codecs detect registers."""
    label, data = case
    data.decode(f"whatwg-{label}")


def detect_language(text: str) -> None:
    """Detect a string's natural language with turbohtml's trigram scorer."""
    _detect_language(text)


def _set_attributes(case: tuple[turbohtml.Element, tuple[str, ...]]) -> None:
    attrs: Final = case[0].attrs
    for name in case[1]:
        attrs[name] = "after"


def _attribute_tree(case: tuple[int, bool]) -> tuple[turbohtml.Element, tuple[str, ...]]:
    root: Final = turbohtml.Element("div")
    names: Final = tuple(f"data-{index}" for index in range(case[0]))
    if case[1]:
        for name in names:
            root.attrs[name] = "before"
    return root, names


def _normalization_tree(case: tuple[int, str]) -> turbohtml.Element:
    root: Final = turbohtml.Element("p")
    if not case[1]:
        root.append(turbohtml.Text("word"))
    root.extend(turbohtml.Text(case[1]) for _ in range(case[0]))
    return root


def normalize(text: str) -> None:
    """Normalize text to Unicode NFC with turbohtml's C engine (quick-checked, then decompose/reorder/compose)."""
    _normalize("NFC", text)


def escape_identifier(idents: tuple[str, ...]) -> None:
    """Escape each raw identifier for use in a selector with turbohtml's CSSOM escape."""
    for ident in idents:
        _escape_identifier(ident)


def idna(urls: tuple[str, ...]) -> None:
    """Run the domain-to-ASCII conversion over a batch of distinct Unicode hosts."""
    for url in urls:
        _normalize_url(url)


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


def links_external(text: str) -> None:
    """Collect links outside the base URL's registrable domain."""
    _extract_links(text, "https://www.example.co.uk/", external_only=True)


def _node_equals(case: tuple[int, str]) -> bool:
    left, right = _equality_pair(*case)
    return left.equals(right)


@functools.cache
def _equality_pair(count: int, variant: str) -> tuple[turbohtml.Element, turbohtml.Element]:
    if variant == "duplicates":
        duplicates: Final = {
            "".join(
                letter.upper() if mask & (1 << index) else letter for index, letter in enumerate("abcdefghij")
            ): "same"
            for mask in range(count - 3)
        }
        anchors: Final = {"first": "1", "second": "2", "third": "3"}
        return turbohtml.Element("div", anchors | duplicates), turbohtml.Element("div", duplicates | anchors)
    left: Final = turbohtml.Element("div")
    right: Final = turbohtml.Element("div")
    right.attrs["data-seed"] = ""
    del right.attrs["data-seed"]
    names: Final = [f"data-{index}" for index in range(count)]
    for name in names:
        left.attrs[name] = "é水😀"
    order: Final = names[count // 2 :] + names[: count // 2] if variant == "rotated" else names
    for name in reversed(order) if variant == "reversed" else order:
        right.attrs[name] = "é水😀"
    if variant == "early-value":
        right.attrs[names[0]] = "different"
    elif variant == "late-value":
        right.attrs[names[-1]] = "different"
    elif variant == "disjoint":
        del right.attrs[names[-1]]
        right.attrs["data-missing"] = "é水😀"
    return left, right


def _radio_group_setup(case: tuple[int, int, int, str]) -> Callable[[], tuple[bool, ...]]:
    padding, forms, repeats, mode = case
    tag: Final = "section" if mode in {"document", "mutate"} else "form"
    markup: Final = f"<{tag}>" + "<input type=radio name=choice>" * 16 + "<div></div>" * padding + f"</{tag}>"
    document: Final = turbohtml.parse(markup * forms)
    form: Final = cast("turbohtml.Element", document.children[0].children[-1].children[0])
    radios: Final = [node for node in form.children if isinstance(node, turbohtml.Element) and node.tag == "input"]

    def run() -> tuple[bool, ...]:
        if mode in {"document", "select"}:
            document.select("input")
        for index in range(repeats):
            if mode == "mutate":
                form.append(turbohtml.Element("i"))
                document.select("input")
            radios[index % len(radios)].checked = True
        return tuple(node.checked for node in radios)

    return run


def _run_radio_group(run: Callable[[], tuple[bool, ...]]) -> tuple[bool, ...]:
    return run()


def _range_boundary_setup(case: tuple[int, str]) -> Callable[[], None]:
    count, variant = case
    root: Final = turbohtml.Element("div")
    root.extend(turbohtml.Element("i") for _ in range(count))
    offset: Final = count if variant == "end" else 0

    def run() -> None:
        _Range(root, offset)

    return run


def _run_prepared(run: Callable[[], None]) -> None:
    run()


def _range_contained_setup(case: tuple[int, str]) -> Callable[[], None]:
    count, variant = case
    root: Final = turbohtml.Element("div")
    root.extend(turbohtml.Element("i") for _ in range(count))
    boundary: Final = _Range(root)
    boundary.set_end(root, count)

    def run() -> None:
        if variant == "extract":
            boundary.extract_contents()
        else:
            boundary.clone_contents()

    return run


def _range_partial_setup(case: tuple[int, str]) -> Callable[[], None]:
    count, variant = case
    root: Final = turbohtml.Element("div")
    root.set_inner_html("<section>abcdef" + "<i></i>" * count + "</section>tail")
    selected: Final = root.children[0].children[0]
    boundary: Final = _Range(root)
    boundary.set_start(root, 0)
    boundary.set_end(selected, 3)

    def run() -> None:
        if variant == "extract":
            boundary.extract_contents()
        else:
            boundary.clone_contents()

    return run


def _observe_registrations_setup(case: tuple[int, str]) -> Callable[[], None]:
    count, variant = case
    root: Final = turbohtml.Element("div")
    root.set_inner_html("<div>" * 100 + "target" + "</div>" * 100 + "<span></span>" * count)
    target: Final = root.select("div")[-1]
    observer: Final = turbohtml.MutationObserver()
    for watched in root.select("span"):
        observer.observe(watched, child_list=variant == "wrong-kind", attributes=variant != "wrong-kind", subtree=True)
    if variant == "match":
        observer.observe(root, attributes=True, subtree=True)

    def run() -> None:
        target.attrs["data-change"] = "changed"
        observer.take_records()

    return run


def _shadow_fallback_setup(case: tuple[int, str]) -> Callable[[], None]:
    host: Final = turbohtml.Element("div")
    shadow: Final = host.attach_shadow("open")
    shadow.set_inner_html("<slot>" + "<slot>fallback</slot>" * case[0] + "</slot>")
    first: Final = shadow.select("slot")[0]

    def run() -> None:
        first.assigned_nodes(flatten=True)

    return run


def _shadow_assignment_setup(case: tuple[int, str]) -> Callable[[], None]:
    host: Final = turbohtml.Element("div")
    host.set_inner_html("".join(f'<i slot="name-{index}">{index}</i>' for index in range(case[0])))
    shadow: Final = host.attach_shadow("open")
    shadow.set_inner_html("".join(f'<slot name="name-{index}">fallback</slot>' for index in range(case[0])))

    def run() -> None:
        _ = host.flattened_children

    return run


def _prune_shared_setup(case: tuple[int, int]) -> turbohtml.Element:
    depth, matches = case
    root: Final = turbohtml.Element("main")
    root.set_inner_html("<section>" * depth + "<b>keep</b><i>drop</i>" * matches + "</section>" * depth)
    return root


def _prune_shared(root: turbohtml.Element) -> None:
    root.prune("b")


def _query_roots(case: tuple[int, str]) -> None:
    _query_root_case(case).find("i")


@functools.cache
def _query_root_case(case: tuple[int, str]) -> _Query:
    count, order = case
    nodes = turbohtml.parse("<main>" + "<div><i>x</i></div>" * count + "</main>").select("div")
    if order == "reversed":
        nodes.reverse()
    elif order == "shuffled":
        nodes = nodes[::2] + nodes[1::2]
    return _Query(nodes)


def _query_closest(case: tuple[int, bool]) -> None:
    _query_parents_case(case).closest("main")


def _node_closest(case: tuple[int, bool]) -> None:
    _query_parents_case(case)[0].closest("main")


def _query_parents(case: tuple[int, bool]) -> None:
    _query_parents_case(case).parent()


@functools.cache
def _query_parents_case(case: tuple[int, bool]) -> _Query:
    count, shared = case
    text: Final = "<main>" + "<p>x</p>" * count + "</main>" if shared else "<main><p>x</p></main>" * count
    return _Query(text)("p")


def _query_siblings(case: tuple[int, bool]) -> None:
    _query_siblings_case(case).siblings()


@functools.cache
def _query_siblings_case(case: tuple[int, bool]) -> _Query:
    count, all_selected = case
    selected: Final = _Query("<main>" + "<p>x</p>" * count + "</main>")("p")
    return selected if all_selected else selected.eq(0)


def _form_data_fieldsets(text: str) -> list[tuple[str, str]]:
    return _form_data_case(text).form_data()


@functools.cache
def _form_data_case(text: str) -> turbohtml.Element:
    return turbohtml.parse(text).select("form")[0]


def _query_root_groups(case: tuple[str, int]) -> _Query:
    return _query_root_group_case(case).find("p")


@functools.cache
def _query_root_group_case(case: tuple[str, int]) -> _Query:
    kind, count = case
    if kind == "documents":
        roots = [turbohtml.parse(f"<main><p>{index}</p></main>").select("main")[0] for index in range(count)]
    else:
        roots = turbohtml.parse("".join(f"<main><p>{index}</p></main>" for index in range(count))).select("main")
        if kind == "detached":
            for root in roots:
                root.extract()
    return _Query(roots)


OPERATIONS: dict[str, tuple[object, str]] = {
    "query-root-groups": (_query_root_groups, "turbohtml"),
    "radio-group": (Mutating(_radio_group_setup, _run_radio_group), "turbohtml"),
    "form-data-fieldsets": (_form_data_fieldsets, "turbohtml"),
    "query-closest": (_query_closest, "turbohtml"),
    "query-roots": (_query_roots, "turbohtml"),
    "node-closest": (_node_closest, "turbohtml"),
    "query-parents": (_query_parents, "turbohtml"),
    "query-siblings": (_query_siblings, "turbohtml"),
    "prune-shared": (Mutating(_prune_shared_setup, _prune_shared), "turbohtml"),
    "shadow-assignment": (Mutating(_shadow_assignment_setup, _run_prepared), "turbohtml"),
    "shadow-fallback": (Mutating(_shadow_fallback_setup, _run_prepared), "turbohtml"),
    "observe-registrations": (Mutating(_observe_registrations_setup, _run_prepared), "turbohtml"),
    "range-boundary": (Mutating(_range_boundary_setup, _run_prepared), "turbohtml"),
    "range-contained": (Mutating(_range_contained_setup, _run_prepared), "turbohtml"),
    "range-partial": (Mutating(_range_partial_setup, _run_prepared), "turbohtml"),
    "node-equals": (_node_equals, "turbohtml"),
    "build": (build, "turbohtml"),
    "build-e": (build_e, "turbohtml"),
    "construct": (construct, "turbohtml"),
    "emit": (emit, "turbohtml"),
    "shadow": (shadow, "turbohtml"),
    "shadow-slot": (_shadow_slot, "turbohtml"),
    "startup": (startup, "turbohtml"),
    "parse": (parse, "turbohtml"),
    "parse-formatting": (parse, "turbohtml"),
    "parse-foster": (parse, "turbohtml"),
    "parse-crlf": (parse, "turbohtml"),
    "parse-nul": (parse, "turbohtml"),
    "parse-afe": (parse, "turbohtml"),
    "parse-scope": (parse, "turbohtml"),
    "parse-dense": (parse, "turbohtml"),
    "parse-xml": (parse_xml, "turbohtml"),
    "parse-xml-attrs": (parse_xml, "turbohtml"),
    "parse-xml-append": (parse_xml, "turbohtml"),
    "parse-xml-values": (parse_xml, "turbohtml"),
    "parse-xml-text": (parse_xml, "turbohtml"),
    "parse-xml-prefixes": (parse_xml, "turbohtml"),
    "parse-xml-names": (parse_xml, "turbohtml"),
    "validate": (validate, "turbohtml"),
    "validate-rng": (validate_rng, "turbohtml"),
    "validate-rng-reuse": (validate_rng, "turbohtml"),
    "compile-rng-reuse": (compile_rng, "turbohtml"),
    "validate-facets": (validate, "turbohtml"),
    "validate-attributes": (validate, "turbohtml"),
    "validate-numeric-facets": (validate, "turbohtml"),
    "compile-facets": (_XMLSchema, "turbohtml"),
    "validate-pattern-reuse": (validate, "turbohtml"),
    "compile-pattern": (_XMLSchema, "turbohtml"),
    "validate-pattern": (validate, "turbohtml"),
    "compile-rng": (compile_rng, "turbohtml"),
    "parse-scripting": (parse_scripting, "turbohtml"),
    "parse-locations": (parse_locations, "turbohtml"),
    "parse-shadow": (parse_shadow, "turbohtml"),
    "fragment": (fragment, "turbohtml"),
    "escape": (escape, "turbohtml"),
    "unescape": (unescape, "turbohtml"),
    "tokenize": (tokenize, "turbohtml"),
    "html-options": (Mutating(_render_options_setup, _render_options), "turbohtml"),
    "text-options": (Mutating(_render_options_setup, _render_options), "turbohtml"),
    "canonical-options": (Mutating(_render_options_setup, _render_options), "turbohtml"),
    "token-attributes": (Mutating(_token_attribute_setup, _token_attributes), "turbohtml"),
    "tokenize-attributes": (_tokenize_attributes, "turbohtml"),
    "find": (find, "turbohtml"),
    "find-cold": (Mutating(_find_cold_setup, _find_cold), "turbohtml"),
    "select": (select, "turbohtml"),
    "select-has": (select_has, "turbohtml"),
    "select-nth": (_select_scaling, "turbohtml"),
    "select-relative": (_select_scaling, "turbohtml"),
    "xpath-wide": (_xpath_scaling, "turbohtml"),
    "xpath-distinct": (_xpath_scaling, "turbohtml"),
    "xpath-set": (_xpath_scaling, "turbohtml"),
    "xpath-compare": (_xpath_scaling, "turbohtml"),
    "xpath-translate": (_xpath_scaling, "turbohtml"),
    "xpath-replace": (_xpath_replace, "turbohtml"),
    "xpath-concat": (_xpath_scaling, "turbohtml"),
    "xpath-id-nodes": (_xpath_scaling, "turbohtml"),
    "xpath-order": (_xpath_scaling, "turbohtml"),
    "computed-style-deep": (computed_style, "turbohtml"),
    "computed-style": (computed_style, "turbohtml"),
    "computed-style-selectors": (computed_style, "turbohtml"),
    "computed-style-selectors-reverse": (_computed_style_reverse, "turbohtml"),
    "computed-style-filter": (computed_style, "turbohtml"),
    "computed-style-filter-cold": (_computed_style_first, "turbohtml"),
    "computed-style-specificity": (computed_style, "turbohtml"),
    "computed-style-specificity-cold": (_computed_style_first, "turbohtml"),
    "computed-style-dense": (computed_style, "turbohtml"),
    "match": (match, "turbohtml"),
    "find-text": (find_text, "turbohtml"),
    "find-text-exact": (find_text_exact, "turbohtml"),
    "find-attr-presence": (find_attr_presence, "turbohtml"),
    "find-text-overlap": (find_text_overlap, "turbohtml"),
    "text-content": (text_content, "turbohtml"),
    "serialize": (serialize, "turbohtml"),
    "parse-inner": (_parse_inner, "turbohtml"),
    "parse-inner-encode": (_parse_inner_encode, "turbohtml"),
    "serialize-inner": (_serialize_inner, "turbohtml"),
    "serialize-inner-indent": (_serialize_inner_indent, "turbohtml"),
    "serialize-inner-minify": (_serialize_inner_minify, "turbohtml"),
    "encode-inner": (_encode_inner, "turbohtml"),
    "encode-inner-indent": (_encode_inner_indent, "turbohtml"),
    "encode-inner-minify": (_encode_inner_minify, "turbohtml"),
    "iterate-inner": (_iterate_inner, "turbohtml"),
    "iterate-inner-indent": (_iterate_inner_indent, "turbohtml"),
    "transform-dispatch": (_transform_dispatch, "turbohtml"),
    "collapse-whitespace": (Mutating(turbohtml.parse, _clean.collapse_whitespace_node), "turbohtml"),
    "strip-comments": (Mutating(turbohtml.parse, _clean.strip_comments_node), "turbohtml"),
    "whitespace-roundtrip": (Mutating(turbohtml.parse, _whitespace_roundtrip), "turbohtml"),
    "transform-tree": (Mutating(turbohtml.parse, _transform_tree), "turbohtml"),
    "conformance": (conformance, "turbohtml"),
    "serialize-xml": (serialize_xml, "turbohtml"),
    "canonicalize": (canonicalize, "turbohtml"),
    "canonicalize-attrs": (canonicalize, "turbohtml"),
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
    "microdata-wide": (microdata, "turbohtml"),
    "microdata-empty-scope": (microdata, "turbohtml"),
    "structured-empty": (structured, "turbohtml"),
    "microdata-itemref": (microdata, "turbohtml"),
    "syndication": (syndication, "turbohtml"),
    "sanitize": (sanitize, "turbohtml"),
    "sanitize-templates": (sanitize_templates, "turbohtml"),
    "sanitize-named-props": (sanitize_named_props, "turbohtml"),
    "sanitize-report": (sanitize_report, "turbohtml"),
    "sanitize-node": (Mutating(turbohtml.parse_fragment, sanitize_node), "turbohtml"),
    "sanitize-attributes": (sanitize_attributes, "turbohtml"),
    "sanitize-styles": (sanitize_styles, "turbohtml"),
    "sanitize-transform": (sanitize_transform, "turbohtml"),
    "sanitize-custom-elements": (sanitize_custom_elements, "turbohtml"),
    "sanitize-xml": (sanitize_xml, "turbohtml"),
    "markup": (markup, "turbohtml"),
    "markup-op": (markup_op, "turbohtml"),
    "linkify": (linkify, "turbohtml"),
    "linkify-node": (Mutating(turbohtml.parse_fragment, linkify_node), "turbohtml"),
    "linkify-traversal": (linkify_traversal, "turbohtml"),
    "detect": (detect, "turbohtml"),
    "phone": (phone, "turbohtml"),
    "phone-parse": (phone_parse, "turbohtml"),
    "phone-format": (phone_format, "turbohtml"),
    "normalize": (normalize, "turbohtml"),
    "normalize-dom": (Mutating(_normalization_tree, turbohtml.Element.normalize), "turbohtml"),
    "attribute-grow": (Mutating(_attribute_tree, _set_attributes), "turbohtml"),
    "normalize-marks": (normalize, "turbohtml"),
    "escape-identifier": (escape_identifier, "turbohtml"),
    "idna": (idna, "turbohtml"),
    "markdown": (markdown, "turbohtml"),
    "markdown-wrap": (markdown_wrap, "turbohtml"),
    "markdown-google": (markdown_google, "turbohtml"),
    "tables": (tables, "turbohtml"),
    "tables-wide": (tables, "turbohtml"),
    "tables-spans": (tables, "turbohtml"),
    "article": (article, "turbohtml"),
    "article-wide": (article, "turbohtml"),
    "article-deep": (article, "turbohtml"),
    "boilerplate": (boilerplate, "turbohtml"),
    "date-tally": (date, "turbohtml"),
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
    "sax-records": (_sax_records, "turbohtml"),
    "sax-records-callback": (sax, "turbohtml"),
    "treebuild": (treebuild, "turbohtml"),
    "rewrite": (rewrite, "turbohtml"),
    "rewrite-attributes": (rewrite_attributes, "turbohtml"),
    "path": (css_path, "turbohtml"),
    "path-xpath": (xpath_path, "turbohtml"),
    "path-wide": (css_path, "turbohtml"),
    "path-xpath-wide": (xpath_path, "turbohtml"),
    "path-cold": (Mutating(turbohtml.parse, _css_paths), "turbohtml"),
    "path-xpath-cold": (Mutating(turbohtml.parse, _xpath_paths), "turbohtml"),
    "path-one-cold": (Mutating(_last_path_element, turbohtml.Element.css_path), "turbohtml"),
    "path-xpath-one-cold": (Mutating(_last_path_element, turbohtml.Element.xpath_path), "turbohtml"),
    "path-class-edit": (Mutating(turbohtml.parse, _css_paths_after_class_edits), "turbohtml"),
    "translate": (translate, "turbohtml"),
    "specificity": (specificity, "turbohtml"),
    "xpath": (xpath, "turbohtml"),
    "xpath-id": (xpath_id, "turbohtml"),
    "transform": (transform, "turbohtml"),
    "transform-compile": (transform_compile, "turbohtml"),
    "transform-names-compile": (transform_compile, "turbohtml"),
    "transform-reuse": (transform_reuse, "turbohtml"),
    "transform-sort": (transform, "turbohtml"),
    "transform-dense": (transform, "turbohtml"),
    "transform-number": (transform, "turbohtml"),
    "transform-rules": (transform, "turbohtml"),
    "transform-names": (transform, "turbohtml"),
    "minify-css": (minify_css, "turbohtml"),
    "minify-css-conflicts": (minify_css, "turbohtml"),
    "minify-css-merges": (minify_css, "turbohtml"),
    "minify-js": (minify_js, "turbohtml"),
    "minify-js-names": (minify_js, "turbohtml"),
    "minify-js-integers": (minify_js, "turbohtml"),
    "minify-js-sequences": (minify_js, "turbohtml"),
    "minify-js-guards": (minify_js, "turbohtml"),
    "minify-js-propagation": (minify_js, "turbohtml"),
    "minify-js-single-use": (minify_js, "turbohtml"),
    "minify-js-unlink": (minify_js, "turbohtml"),
    "minify-js-unused-declarations": (minify_js, "turbohtml"),
    "minify-js-var-initialization": (minify_js, "turbohtml"),
    "stream": (stream, "turbohtml"),
    "encoding-result": (encoding, "turbohtml"),
    "encoding-result-stream": (_encoding_stream, "turbohtml"),
    "encoding": (encoding, "turbohtml"),
    "decode": (decode, "turbohtml"),
    "detect-language": (detect_language, "turbohtml"),
    "detect-language-long": (detect_language, "turbohtml"),
    "urls-clean": (urls_clean, "turbohtml"),
    "links-filter": (links_filter, "turbohtml"),
    "links-external": (links_external, "turbohtml"),
    "serialize-attributes": (_serialize_attributes, "turbohtml"),
    "is-valid": (_is_valid, "turbohtml"),
    "is-valid-rng": (_is_valid_rng, "turbohtml"),
}
