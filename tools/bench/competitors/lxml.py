"""lxml: ElementTree-style constructor build, lxml.builder's nested ``E``, and the construct/serialize breakdown."""

from __future__ import annotations

import functools
from html import escape
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import urljoin

import lxml.etree as lxml_etree  # ty: ignore[unresolved-import]  # C extension, ships no type stubs
from lxml import html as lxml_html
from lxml.builder import E

from bench.timing import Mutating
from bench.tree_text import PRESERVE_TAGS, SPACE_RUN

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


def _parse_encoded(case: tuple[str, bytes]) -> None:
    lxml_html.document_fromstring(case[1], parser=lxml_html.HTMLParser(encoding=case[0]))


def stream(text: str) -> None:
    """Include parser setup and closing in the 4,096-character feed workload."""
    parser: Final = lxml_etree.HTMLParser()
    for start in range(0, len(text), 4096):
        parser.feed(text[start : start + 4096])
    parser.close()


def fragment(text: str) -> None:
    """Parse a fragment with lxml.html's fromstring."""
    lxml_html.fromstring(text)


def parse_xml(text: str) -> None:
    """Parse a whole XML document with lxml's libxml2-backed XML parser (etree.XMLParser)."""
    lxml_html.etree.fromstring(text.encode())


_VALIDATORS: dict[str, Any] = {}


def validate(case: tuple[str, str]) -> None:
    """Validate a parsed XML document against an XSD schema with lxml's etree.XMLSchema (compiled once)."""
    schema, document = case
    etree = lxml_html.etree
    validator = _VALIDATORS.get(schema)
    if validator is None:
        validator = _VALIDATORS[schema] = etree.XMLSchema(etree.fromstring(schema.encode()))
    validator.validate(etree.fromstring(document.encode()))


def _compile_pattern(schema: str) -> lxml_etree.XMLSchema:
    return lxml_etree.XMLSchema(lxml_etree.fromstring(schema.encode()))


def _validate_rng(case: tuple[str, str]) -> None:
    _rng_schema(case[0]).validate(lxml_etree.fromstring(case[1].encode()))


@functools.cache
def _rng_schema(schema: str) -> lxml_etree.RelaxNG:
    return lxml_etree.RelaxNG(lxml_etree.fromstring(schema.encode()))


def _compile_rng(schema: str) -> lxml_etree.RelaxNG:
    return lxml_etree.RelaxNG(lxml_etree.fromstring(schema.encode()))


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


def _select_relative(case: tuple[str, str]) -> None:
    _parsed(case[1]).cssselect(case[0])


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


def rewrite(text: str) -> None:
    """Full parse, mutate, serialize -- the DOM round-trip the streamer skips: rel=nofollow, lazy img, drop comments."""
    tree = lxml_html.document_fromstring(text)
    for anchor in tree.xpath("//a[@href]"):
        anchor.set("rel", "nofollow")
    for image in tree.findall(".//img"):
        image.set("loading", "lazy")
    lxml_html.etree.strip_elements(tree, lxml_html.etree.Comment, with_tail=False)
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
    """Collect the cleaned, absolutized, deduplicated page links, the work turbohtml's extract_links does."""
    # not iterlinks(): that yields every URL-bearing attribute in the document, so img/script/link sources land in
    # a set the reference builds from anchors alone
    seen: dict[str, None] = {}
    for href in lxml_html.document_fromstring(text).xpath("//a/@href"):
        seen[urljoin(_LINKS_BASE, href)] = None
    _ = list(seen)


def find_text(text: str) -> None:
    """Collect every element whose collected text contains the marker with lxml's XPath contains()."""
    _parsed(text).xpath('//*[contains(., "test")]')


def _find_text_exact(case: tuple[str, str]) -> None:
    _parsed(case[0]).xpath("//div[string(.) = $expected]", expected=case[1])


def _find_attr_presence(case: tuple[str, bool]) -> None:
    _parsed(case[0]).xpath("//p[@data-x]" if case[1] else "//p[not(@data-x)]")


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


_EXSLT_NS = {
    "re": "http://exslt.org/regular-expressions",
    "set": "http://exslt.org/sets",
    "str": "http://exslt.org/strings",
}
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


def _xpath_scaling(case: tuple[str, str]) -> None:
    _parsed(case[1]).xpath(case[0], namespaces=_EXSLT_NS)


def xpath(case: tuple[str, str]) -> None:
    """Evaluate one XPath feature class with lxml's libxml2 engine, by case kind."""
    kind, text = case
    if (call := _XPATH_CALLS.get(kind)) is None:
        # libxml2 is an XPath 1.0 engine; the XPath 2.0 string functions turbohtml adds have nothing to run on here,
        # so the worker records this sentence as the empty cell's reason rather than a bare KeyError token
        unsupported = "lxml's libxml2 is XPath 1.0, without the XPath 2.0 string functions"
        raise NotImplementedError(unsupported)
    call(_parsed(text), text)


@functools.cache
def _xslt_sheet(sheet: str):  # ruff:ignore[missing-return-type-private-function]  # lxml.etree is untyped
    """Keep XML parsing outside compile timing."""
    return lxml_etree.fromstring(sheet.encode())


def transform_compile(case: tuple[str, str]) -> None:
    """Measure libxslt construction without source evaluation."""
    sheet, _source = case
    lxml_etree.XSLT(_xslt_sheet(sheet))


@functools.cache
def _xslt_compiled(sheet: str, source: str):  # ruff:ignore[missing-return-type-private-function]  # lxml has no types
    """Keep construction and source parsing outside application timing."""
    document: Final = lxml_etree.fromstring(sheet.encode())
    if any(
        "current()" in number.get(attribute, "")
        for number in document.iter("{http://www.w3.org/1999/XSL/Transform}number")
        for attribute in ("count", "from")
    ):
        # XSLT 1.0 section 12.4 forbids current() in patterns; libxslt gives this fixture different semantics.
        unsupported: Final = "XSLT 1.0 forbids current() in patterns; libxslt numbering differs from turbohtml"
        raise NotImplementedError(unsupported)
    return lxml_etree.XSLT(document), lxml_etree.fromstring(source.encode())


def transform(case: tuple[str, str]) -> lxml_etree._XSLTResultTree:
    """Apply a compiled XSLT 1.0 stylesheet to a parsed source with lxml's libxslt engine."""
    sheet, source = case
    compiled, document = _xslt_compiled(sheet, source)
    return compiled(document)


def _transform_compile_run(case: tuple[str, str]) -> lxml_etree._XSLTResultTree:
    sheet, source = case
    return lxml_etree.XSLT(_xslt_sheet(sheet))(_xslt_sheet(source))


def transform_reuse(case: tuple[str, str]) -> None:
    """Match turbohtml's ten-application reuse workload."""
    sheet, source = case
    compiled, document = _xslt_compiled(sheet, source)
    for _ in range(10):
        compiled(document)


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


def _serialize_inner(text: str) -> str:
    body: Final = _body(text)
    return escape(body.text or "", quote=False) + "".join(
        lxml_html.tostring(child, encoding="unicode") for child in body
    )


@functools.cache
def _body(text: str) -> HtmlElement:
    return _parsed(text).find("body")


def _encode_inner(text: str) -> bytes:
    return _serialize_inner(text).encode()


def _serialize_inner_indent(text: str) -> str:
    body: Final = _body(text)
    return escape(body.text or "", quote=False) + "".join(
        lxml_html.tostring(child, encoding="unicode", pretty_print=True) for child in body
    )


def _encode_inner_indent(text: str) -> bytes:
    return _serialize_inner_indent(text).encode()


def _collapse_whitespace(tree: HtmlElement) -> None:
    for node in tree.iter():
        if any(parent.tag in PRESERVE_TAGS for parent in node.iterancestors()):
            continue
        if isinstance(node.tag, str) and node.tag not in PRESERVE_TAGS and node.text:
            node.text = SPACE_RUN.sub(" ", node.text)
        if node.tail:
            node.tail = SPACE_RUN.sub(" ", node.tail)


def _transform_tree(tree: HtmlElement) -> None:
    _strip_comments(tree)
    _collapse_whitespace(tree)


def _strip_comments(tree: HtmlElement) -> None:
    lxml_etree.strip_elements(tree, lxml_etree.Comment, with_tail=False)


OPERATIONS = {
    "collapse-whitespace": (Mutating(lxml_html.document_fromstring, _collapse_whitespace), "lxml"),
    "transform-tree": (Mutating(lxml_html.document_fromstring, _transform_tree), "lxml"),
    "serialize-inner": (_serialize_inner, "lxml"),
    "encode-inner": (_encode_inner, "lxml"),
    "serialize-inner-indent": (_serialize_inner_indent, "lxml"),
    "encode-inner-indent": (_encode_inner_indent, "lxml"),
    "strip-comments": (Mutating(lxml_html.document_fromstring, _strip_comments), "lxml"),
    "parse": (parse, "lxml"),
    "parse-encoded": (_parse_encoded, "lxml"),
    "stream": (stream, "lxml"),
    "parse-formatting": (parse, "lxml"),
    "parse-foster": (parse, "lxml"),
    "parse-crlf": (parse, "lxml"),
    "parse-nul": (parse, "lxml"),
    "parse-afe": (parse, "lxml"),
    "parse-scope": (parse, "lxml"),
    "parse-xml": (parse_xml, "lxml.etree"),
    "parse-xml-attrs": (parse_xml, "lxml.etree"),
    "parse-xml-append": (parse_xml, "lxml.etree"),
    "parse-xml-values": (parse_xml, "lxml.etree"),
    "parse-xml-text": (parse_xml, "lxml.etree"),
    "parse-xml-prefixes": (parse_xml, "lxml.etree"),
    "validate": (validate, "lxml.etree.XMLSchema"),
    "validate-pattern-reuse": (validate, "lxml.etree.XMLSchema"),
    "compile-pattern": (_compile_pattern, "lxml.etree.XMLSchema"),
    "validate-facets": (validate, "lxml.etree.XMLSchema"),
    "validate-attributes": (validate, "lxml.etree.XMLSchema"),
    "validate-numeric-facets": (validate, "lxml.etree.XMLSchema"),
    "compile-facets": (_compile_pattern, "lxml.etree.XMLSchema"),
    "validate-pattern": (validate, "lxml.etree.XMLSchema"),
    "validate-rng": (_validate_rng, "lxml.etree.RelaxNG"),
    "validate-rng-reuse": (_validate_rng, "lxml.etree.RelaxNG"),
    "compile-rng-reuse": (_compile_rng, "lxml.etree.RelaxNG"),
    "fragment": (fragment, "lxml"),
    "build": (build, "lxml"),
    "build-e": (build_e, "lxml.builder"),
    "construct": (construct, "lxml"),
    "emit": (emit, "lxml"),
    "find": (find, "lxml"),
    "select": (select, "lxml"),
    "select-nth": (_select_relative, "lxml"),
    "select-relative": (_select_relative, "lxml"),
    "select-has": (select_has, "lxml"),
    "text-content": (text_content, "lxml"),
    "serialize": (serialize, "lxml"),
    "serialize-xml": (serialize_xml, "lxml method=xml"),
    "canonicalize": (canonicalize, "lxml method=c14n"),
    "canonicalize-deep": (canonicalize, "lxml method=c14n"),
    "extract-attr": (extract_attr, "lxml"),
    "extract-text": (extract_text, "lxml"),
    "strip-remove": (strip_remove, "lxml"),
    "strip-tags": (strip_tags, "lxml"),
    "rewrite": (rewrite, "lxml"),
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
    "find-text-exact": (_find_text_exact, "lxml"),
    "find-attr-presence": (_find_attr_presence, "lxml"),
    "socialcard": (socialcard, "lxml"),
    "extract-url": (extract_url, "lxml"),
    "path": (getpath, "lxml getpath"),
    "path-xpath": (getpath, "lxml getpath"),
    "xpath": (xpath, "lxml"),
    "xpath-distinct": (_xpath_scaling, "lxml"),
    "xpath-set": (_xpath_scaling, "lxml"),
    "xpath-compare": (_xpath_scaling, "lxml"),
    "xpath-order": (_xpath_scaling, "lxml"),
    "xpath-translate": (_xpath_scaling, "lxml"),
    "xpath-concat": (_xpath_scaling, "lxml"),
    "xpath-id-nodes": (_xpath_scaling, "lxml"),
    "transform": (transform, "lxml.etree"),
    "transform-number": (transform, "lxml.etree"),
    "transform-sort": (transform, "lxml.etree"),
    "transform-key": (transform, "lxml.etree"),
    "transform-scope": (transform, "lxml.etree"),
    "transform-namespaces": (transform, "lxml.etree"),
    "transform-namespaces-once": (_transform_compile_run, "lxml.etree"),
    "transform-rules": (transform, "lxml.etree"),
    "transform-names": (transform, "lxml.etree"),
    "transform-dense": (transform, "lxml.etree"),
    "transform-compile": (transform_compile, "lxml.etree"),
    "transform-names-compile": (transform_compile, "lxml.etree"),
    "transform-reuse": (transform_reuse, "lxml.etree"),
    "is-valid": (validate, "lxml.etree.XMLSchema"),
    "is-valid-rng": (_validate_rng, "lxml.etree.RelaxNG"),
    "transform-text": (transform, "lxml.etree"),
}
