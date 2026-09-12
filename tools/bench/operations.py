"""
Operation metadata and shared inputs: the single source of truth for what is benchmarked.

``OPERATIONS`` (title plus time unit) is pure data the orchestrator and renderer read in any environment. ``INPUTS``
holds the cases lazily -- a callable per operation returning ``(case name, input)`` pairs -- so corpora load only inside
a worker that asks for them, never when the orchestrator imports this module. Both turbohtml's core timing and every
competitor consume the identical input for an operation, so a speedup is a like-for-like ratio. ``build``-family cases
are integer row counts; the rest are HTML strings or corpus documents.
"""

from __future__ import annotations

import string
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from textwrap import dedent
from typing import TYPE_CHECKING, Final

from bench import corpus

if TYPE_CHECKING:
    from collections.abc import Callable

_ROWS = (("100 rows", 100), ("1k rows", 1_000), ("10k rows", 10_000))

_SOCIAL_HEAD = dedent("""\
    <head>
      <meta property="og:title" content="Widget">
      <meta property="og:type" content="product">
      <meta property="og:image" content="https://x/i.png">
      <meta property="og:description" content="A small widget">
      <meta name="twitter:card" content="summary">
      <meta name="twitter:site" content="@x">
    </head>""")

_STRUCTURED_PAGE = dedent("""\
    <head>
      <meta property="og:title" content="Widget">
      <meta property="og:type" content="product">
      <meta property="og:image" content="https://x/i.png">
      <meta name="twitter:card" content="summary">
    </head>
    <body>
      <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Widget", "sku": "W-1",
         "offers": {"@type": "Offer", "price": "9.99", "priceCurrency": "USD", "availability": "InStock"}}
      </script>
      <div itemscope itemtype="https://schema.org/Product">
        <span itemprop="name">Widget</span>
        <meta itemprop="sku" content="W-1">
        <div itemprop="offers" itemscope itemtype="https://schema.org/Offer">
          <span itemprop="price">9.99</span>
          <meta itemprop="priceCurrency" content="USD">
          <link itemprop="availability" href="https://schema.org/InStock">
        </div>
      </div>
    </body>""")

_MICRODATA_REFS: Final[str] = " ".join(f"r{index}" for index in range(1_000))
_MICRODATA_ITEMREF: Final = (
    '<div itemscope itemref="'
    + _MICRODATA_REFS
    + '"></div>'
    + "<i></i>" * 4_000
    + "".join(f"<meta id=r{index} itemprop=p content=x>" for index in range(1_000))
)

_FEED_ITEM = dedent("""\
    <item>
      <title>Release {index}: what changed</title>
      <link>https://blog.example/posts/{index}</link>
      <guid isPermaLink="false">tag:blog.example,2026:{index}</guid>
      <pubDate>Tue, 07 Jul 2026 09:00:00 GMT</pubDate>
      <dc:creator>A. Writer</dc:creator>
      <description>Short summary of release {index}.</description>
      <content:encoded>&lt;p&gt;The full body of release {index}, with details.&lt;/p&gt;</content:encoded>
    </item>
""")

_FEED_XML = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"'
    ' xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel>\n'
    "<title>Example Engineering Blog</title>\n"
    "<link>https://blog.example/</link>\n"
    "<description>Notes from the Example engineering team.</description>\n"
    "<lastBuildDate>Tue, 07 Jul 2026 09:00:00 GMT</lastBuildDate>\n"
    + "".join(_FEED_ITEM.format(index=index) for index in range(30))
    + "</channel></rss>"
)

_XML_ITEM = (
    '<book id="b{index}" xmlns:dc="http://purl.org/dc/elements/1.1/">'
    "<dc:title>Book number {index} &amp; friends</dc:title>"
    "<dc:creator>Author {index}</dc:creator>"
    '<price currency="USD">{index}.99</price>'
    "<description><![CDATA[Plain <text> with & symbols]]></description>"
    "<tags><tag>alpha</tag><tag>beta</tag><tag>gamma</tag></tags>"
    "</book>"
)

# A well-formed namespaced catalog both parse_xml and lxml.etree accept: 400 records with
# prefixes, attributes, entities and CDATA, sized to clear the CodSpeed heap-jitter floor.
_XML_DOC = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    "<!-- generated catalog -->\n"
    '<catalog xmlns="urn:example:catalog">'
    + "".join(_XML_ITEM.format(index=index) for index in range(400))
    + "</catalog>"
)

_SHADOW_CARD = (
    '<article class="card">'
    '<template shadowrootmode="open" shadowrootclonable shadowrootdelegatesfocus>'
    '<style>:host{{display:block}}</style><header><slot name="title">Untitled {index}</slot></header>'
    '<section><slot>No description</slot></section><footer><slot name="meta"></slot></footer>'
    "</template>"
    '<span slot="title">Card {index}</span>'
    '<p>Body copy for card number {index} with some inline <a href="/item/{index}">detail</a> text.</p>'
    '<span slot="meta">tag-{index}</span>'
    "</article>"
)

# A document of declarative shadow hosts: 200 web-component cards whose <template shadowrootmode> attaches an open
# shadow root (with slots and the delegatesfocus/clonable flags) to each host, sized to clear the CodSpeed jitter floor.
_SHADOW_DOC = (
    "<!doctype html><html><head><title>Component gallery</title></head><body><main>"
    + "".join(_SHADOW_CARD.format(index=index) for index in range(200))
    + "</main></body></html>"
)

# A single-namespace records catalog and a matching XSD, sized like the parse corpus so the
# validation walk clears the CodSpeed heap-jitter floor. Both turbohtml and lxml validate it.
_VALIDATE_RECORD = (
    '<record id="r{index}">'
    "<name>Item {index}</name>"
    "<qty>{index}</qty>"
    "<price>{index}.99</price>"
    "<tags><tag>alpha</tag><tag>beta</tag></tags>"
    "</record>"
)
_VALIDATE_DOC = (
    '<?xml version="1.0"?>'
    '<catalog xmlns="urn:example:records">'
    + "".join(_VALIDATE_RECORD.format(index=index) for index in range(400))
    + "</catalog>"
)
_VALIDATE_XSD = (
    '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:example:records"'
    ' xmlns="urn:example:records" elementFormDefault="qualified">'
    '<xs:element name="catalog"><xs:complexType><xs:sequence>'
    '<xs:element name="record" maxOccurs="unbounded"><xs:complexType><xs:sequence>'
    '<xs:element name="name" type="xs:string"/>'
    '<xs:element name="qty" type="xs:nonNegativeInteger"/>'
    '<xs:element name="price" type="xs:decimal"/>'
    '<xs:element name="tags"><xs:complexType><xs:sequence>'
    '<xs:element name="tag" type="xs:string" maxOccurs="unbounded"/>'
    "</xs:sequence></xs:complexType></xs:element>"
    "</xs:sequence>"
    '<xs:attribute name="id" type="xs:string" use="required"/>'
    "</xs:complexType></xs:element>"
    "</xs:sequence></xs:complexType></xs:element></xs:schema>"
)
_VALIDATE_GLOBAL_XSD: Final = (
    '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'
    + "".join(f'<xs:element name="element{index}" type="xs:string"/>' for index in range(512))
    + "".join(
        f'<xs:simpleType name="Type{index}"><xs:restriction base="xs:string"/></xs:simpleType>' for index in range(512)
    )
    + '<xs:simpleType name="TargetType"><xs:restriction base="xs:string"/></xs:simpleType>'
    '<xs:element name="target" type="TargetType"/></xs:schema>'
)
_VALIDATE_GLOBAL_DOC: Final = "<target>value</target>"
# the same records catalog described in RELAX NG (XML syntax): grammar/define/ref, an interleave over the record's four
# child patterns, oneOrMore, and XSD datatypes. RelaxNG drives a separate C compiler and validator (relaxng.h) that the
# XSD validate op never reaches, so this is the only bench that exercises the RELAX NG engine.
_VALIDATE_RNG = (
    '<grammar xmlns="http://relaxng.org/ns/structure/1.0"'
    ' datatypeLibrary="http://www.w3.org/2001/XMLSchema-datatypes" ns="urn:example:records">'
    '<start><ref name="catalog"/></start>'
    '<define name="catalog"><element name="catalog"><oneOrMore><ref name="record"/></oneOrMore></element></define>'
    '<define name="record"><element name="record">'
    '<attribute name="id" ns=""><data type="string"/></attribute>'
    "<interleave>"
    '<element name="name"><text/></element>'
    '<element name="qty"><data type="nonNegativeInteger"/></element>'
    '<element name="price"><data type="decimal"/></element>'
    '<element name="tags"><oneOrMore><element name="tag"><text/></element></oneOrMore></element>'
    "</interleave>"
    "</element></define>"
    "</grammar>"
)
_COMPILE_RNG: Final = (
    '<grammar xmlns="http://relaxng.org/ns/structure/1.0"><start><ref name="target"/></start>'
    + "".join(f'<define name="definition{index}"><empty/></define>' for index in range(1_023))
    + '<define name="target"><element name="target"><text/></element></define></grammar>'
)

_SANITIZE_TEMPLATES = dedent("""\
    <article class=card>
      <h1>{{ post.title }}</h1>
      <p data-id="${post.id}">By {{ author.name }} on <% post.date %> in {{ post.section }}.</p>
      <ul>
        <li>{{ item.label }}: ${item.value}</li>
        <li>Rating <% stars %> out of {{ max }}</li>
      </ul>
    </article>
""")

_SANITIZE_POST = dedent("""\
    <div class=post>
      <h1>Title</h1>
      <p>Some <a href='http://example.com'>link</a> and <b>bold</b> text with
        <img src=http://x/i.png onerror=alert(1)> and <script>evil()</script>.</p>
      <ul><li>one</li><li>two</li></ul>
    </div>""")

# dense in the deprecated presentational tags a transform map renames (b/i/center/font/tt/big/strike), so the walk hits
# the rename + attribute-injection path on most elements rather than passing them through unchanged
_SANITIZE_LEGACY = dedent("""\
    <center><font size=4><b>Heading</b></font></center>
    <p><i>Intro</i> with <tt>inline code</tt>, <strike>struck</strike>, and <big>emphatic</big> text.</p>
    <blockquote><b>Quote</b> from <i>an author</i> with a <font color=red>colored</font> aside.</blockquote>
    <ul><li><b>one</b></li><li><i>two</i></li><li><tt>three</tt></li></ul>""")

# dense in id/name attributes whose values collide with document/form properties (DOM clobbering), so the named-prop
# isolation prefixes most attributes rather than skipping past a value it never touches
_SANITIZE_NAMED = dedent("""\
    <form name="config" id="settings">
      <input name="attributes" id="body"><input name="method" id="location">
      <input name="submit" id="cookie"><input name="nodeName" id="documentElement">
      <a id="forms" name="images" href="http://example.com/x">links</a>
      <a id="anchors" name="scripts" href="http://example.com/y">scripts</a>
    </form>""")

# dense in an app's own x-* custom elements and data-* attributes, so the custom-element matcher and attribute matcher
# run on most elements rather than short-circuiting on a policy that keeps only the standard allowlist
_SANITIZE_CUSTOM = dedent("""\
    <x-shell data-theme="dark">
      <x-card data-id="1" onclick="steal()"><b>Title</b> and <x-badge data-kind="new">new</x-badge></x-card>
      <x-card data-id="2"><a href="javascript:evil()">bad</a> then <x-rating data-stars="4">****</x-rating> ok.</x-card>
      <x-list><x-item data-i="1">one</x-item><x-item data-i="2">two</x-item><x-item data-i="3">three</x-item></x-list>
    </x-shell>""")

# rich in style attributes with a mix of pattern-matching and rejected values, so the allowed_styles scrub does real
# per-declaration regex work rather than short-circuiting on an empty rule
_SANITIZE_STYLES = dedent("""\
    <div style="color: #ff0000; text-align: center; position: fixed">
      <p style="color: rgb(1, 2, 3); font-size: 40px">Styled
        <span style="text-align: left; color: green">inline</span> text.</p>
      <p style="color: #abc; text-align: justify">More <b style="color: #123456">bold</b> content.</p>
    </div>""")


@dataclass(frozen=True)
class Operation:
    """One benchmarked operation: its display title and the time unit (``ns``, ``us``, ``ms``) its table prints in."""

    title: str
    unit: str


# The minify operations shrink their input, so their tables carry an output-size column alongside time. Their
# functions return the minified text; the worker records its byte length once (deterministic) beside the timing.
SIZE_OPS: Final[frozenset[str]] = frozenset({
    "minify",
    "minify-css",
    "minify-css-conflicts",
    "minify-css-merges",
    "minify-js",
    "minify-js-integers",
    "minify-js-names",
    "minify-js-sequences",
    "minify-js-guards",
    "minify-js-propagation",
    "minify-js-single-use",
    "minify-js-unlink",
    "minify-js-unused-declarations",
    "minify-js-var-initialization",
})

# Peak RSS runs in a fresh process so allocator reuse from pyperf's timed loops cannot hide the retained tree or buffer.
MEMORY_OPS: Final[frozenset[str]] = frozenset({
    "attribute-grow",
    "find-cold",
    "parse-dense",
    "parse-xml-attrs",
    "rewrite",
})


OPERATIONS: dict[str, Operation] = {
    "radio-group": Operation("change a radio group selection", "us"),
    "form-data-fieldsets": Operation("collect form controls around disabled fieldsets", "us"),
    "build": Operation("build a list (constructors)", "us"),
    "build-e": Operation("build a list (terse builders)", "us"),
    "construct": Operation("construct N elements (no serialize)", "us"),
    "emit": Operation("emit a built tree", "us"),
    "shadow": Operation("attach a shadow tree with slots and flatten", "us"),
    "shadow-slot": Operation("collect children assigned to a late shadow slot", "us"),
    "shadow-assignment": Operation("flatten uniquely named shadow slots", "us"),
    "shadow-fallback": Operation("flatten nested fallback slots", "us"),
    "startup": Operation("start a fresh Python process", "ms"),
    "parse": Operation("parse to a tree", "us"),
    "parse-formatting": Operation("parse under a formatting ancestor", "us"),
    "parse-foster": Operation("parse foster-parented text", "us"),
    "parse-crlf": Operation("parse normalized newlines", "us"),
    "parse-nul": Operation("parse text beside a NUL", "us"),
    "parse-afe": Operation("parse nested formatting attributes", "us"),
    "parse-scope": Operation("parse ignored block end tags", "us"),
    "parse-dense": Operation("parse a node-dense document", "ms"),
    "parse-xml": Operation("parse XML to a tree", "us"),
    "parse-xml-append": Operation("parse XML with distinct attribute names", "us"),
    "parse-xml-attrs": Operation("parse XML with growing attribute counts", "us"),
    "parse-xml-values": Operation("parse XML attribute values", "us"),
    "parse-xml-text": Operation("parse XML text runs", "us"),
    "parse-xml-prefixes": Operation("parse XML namespace attributes", "us"),
    "parse-xml-names": Operation("parse growing XML names", "ms"),
    "is-valid": Operation("XSD validation verdict", "us"),
    "is-valid-rng": Operation("RELAX NG validation verdict", "us"),
    "validate": Operation("validate a document against an XSD schema", "us"),
    "validate-rng": Operation("validate a document against a RELAX NG schema", "us"),
    "validate-rng-reuse": Operation("validate repeated RELAX NG content models", "us"),
    "compile-rng-reuse": Operation("compile repeated RELAX NG content models", "us"),
    "validate-attributes": Operation("validate XSD attribute declarations", "us"),
    "validate-numeric-facets": Operation("validate numeric values with optional bounds", "us"),
    "validate-facets": Operation("validate inherited facet metadata", "us"),
    "compile-facets": Operation("compile inherited facet metadata", "us"),
    "validate-pattern-reuse": Operation("validate repeated pattern facets", "us"),
    "compile-pattern": Operation("compile pattern facets", "us"),
    "validate-pattern": Operation("validate growing regex character classes", "us"),
    "compile-rng": Operation("compile a RELAX NG schema", "us"),
    "parse-scripting": Operation("parse to a tree (scripting on)", "us"),
    "parse-locations": Operation("parse to a tree (source locations)", "us"),
    "parse-shadow": Operation("parse declarative shadow roots", "us"),
    "fragment": Operation("parse a fragment", "us"),
    "escape": Operation("escape", "us"),
    "unescape": Operation("unescape", "us"),
    "tokenize": Operation("tokenize", "us"),
    "html-options": Operation("serialize with reused options", "us"),
    "text-options": Operation("render text with reused options", "us"),
    "canonical-options": Operation("canonicalize with reused options", "us"),
    "token-attributes": Operation("read token attributes", "us"),
    "tokenize-attributes": Operation("tokenize and read attributes", "us"),
    "find": Operation("find every anchor", "us"),
    "find-cold": Operation("query a cold 10,000-element tree", "us"),
    "select": Operation("select div a[href]", "us"),
    "select-has": Operation("select div:has(a)", "us"),
    "select-default": Operation("select default form controls", "us"),
    "select-nth": Operation("select sibling positions in wide trees", "ms"),
    "select-relative": Operation("select child and sibling relationships", "us"),
    "xpath-wide": Operation("order XPath results in wide trees", "ms"),
    "xpath-distinct": Operation("deduplicate XPath string values", "us"),
    "xpath-set": Operation("compare XPath node-set membership", "us"),
    "xpath-compare": Operation("compare XPath values", "us"),
    "node-equals": Operation("compare element attributes", "us"),
    "xpath-id-nodes": Operation("resolve XPath ID argument nodes", "us"),
    "xpath-replace": Operation("replace XPath string literals", "us"),
    "xpath-concat": Operation("concatenate XPath node strings", "us"),
    "xpath-translate": Operation("translate XPath characters", "us"),
    "xpath-order": Operation("compare numeric XPath node sets", "us"),
    "computed-style-deep": Operation("compute styles through nested ancestors", "ms"),
    "microdata-wide": Operation("extract properties from one wide item", "ms"),
    "microdata-empty-scope": Operation("traverse an item without properties", "ms"),
    "structured-empty": Operation("extract metadata from an unannotated tree", "ms"),
    "article-wide": Operation("score many article candidates", "ms"),
    "article-deep": Operation("score nested article candidates", "us"),
    "path-wide": Operation("build CSS paths for wide sibling lists", "ms"),
    "path-xpath-wide": Operation("build XPath paths for wide sibling lists", "ms"),
    "path-cold": Operation("build CSS paths on a fresh tree", "ms"),
    "path-xpath-cold": Operation("build XPath paths on a fresh tree", "ms"),
    "path-one-cold": Operation("build one CSS path on a fresh tree", "us"),
    "path-xpath-one-cold": Operation("build one XPath path on a fresh tree", "us"),
    "path-class-edit": Operation("build CSS paths after class edits", "ms"),
    "computed-style-filter": Operation("reject CSS rules before traversal predicates", "ms"),
    "computed-style-filter-cold": Operation("compile CSS predicate order and resolve one element", "ms"),
    "computed-style-specificity": Operation("compute styles across matching selector alternatives", "ms"),
    "computed-style-specificity-cold": Operation("compile styles and resolve the first element", "ms"),
    "computed-style-selectors": Operation("reuse computed-style selector matches", "ms"),
    "computed-style-selectors-reverse": Operation("compute styles in reverse document order", "ms"),
    "computed-style": Operation("computed style for every element", "us"),
    "computed-style-dense": Operation("computed style over a property-dense sheet", "us"),
    "match": Operation("match each anchor against div a[href]", "us"),
    "find-text": Operation("find by text content", "us"),
    "find-text-exact": Operation("find by exact descendant text", "us"),
    "find-attr-presence": Operation("find by attribute presence", "us"),
    "find-text-overlap": Operation("find by overlapping literal regex", "us"),
    "text-content": Operation("collect visible text", "us"),
    "parse-inner": Operation("parse and serialize body children", "us"),
    "parse-inner-encode": Operation("parse and encode body children", "us"),
    "serialize-inner": Operation("serialize body children", "us"),
    "serialize-inner-indent": Operation("serialize indented body children", "us"),
    "serialize-inner-minify": Operation("serialize minified body children", "us"),
    "encode-inner": Operation("encode body children as UTF-8", "us"),
    "encode-inner-indent": Operation("encode indented body children as UTF-8", "us"),
    "encode-inner-minify": Operation("encode minified body children as UTF-8", "us"),
    "iterate-inner": Operation("consume body children chunks", "us"),
    "iterate-inner-indent": Operation("consume indented body children chunks", "us"),
    "transform-dispatch": Operation("compose identity callbacks", "ns"),
    "collapse-whitespace": Operation("collapse DOM text whitespace", "us"),
    "strip-comments": Operation("remove DOM comments", "us"),
    "whitespace-roundtrip": Operation("normalize DOM whitespace then serialize", "us"),
    "transform-tree": Operation("remove comments then collapse whitespace", "us"),
    "serialize": Operation("serialize a parsed tree", "us"),
    "conformance": Operation("check HTML5 authoring conformance", "us"),
    "serialize-xml": Operation("serialize a parsed tree to XML", "us"),
    "canonicalize": Operation("canonicalize a parsed tree (c14n)", "us"),
    "canonicalize-attrs": Operation("canonicalize an element with many attributes", "ms"),
    "canonicalize-deep": Operation("canonicalize deep trees with and without xlink attributes (c14n)", "us"),
    "lossless-serialize": Operation("edit then re-emit untouched bytes (to_source)", "us"),
    "minify": Operation("minify a document", "us"),
    "edit": Operation("tag every link rel=nofollow", "us"),
    "class-edit": Operation("class add/remove on every link", "us"),
    "strip-remove": Operation("drop tags with content (remove)", "us"),
    "strip-tags": Operation("unwrap tags keep content (strip_tags)", "us"),
    "set-html": Operation("replace body inner HTML", "us"),
    "set-text": Operation("replace body text", "us"),
    "observe": Operation("observe a subtree through many edits", "us"),
    "query-root-groups": Operation("select descendants across connected root groups", "us"),
    "query-roots": Operation("select within ordered root groups", "us"),
    "query-closest": Operation("collect closest matching ancestors", "us"),
    "node-closest": Operation("find a node's closest matching ancestor", "us"),
    "query-parents": Operation("collect selected nodes' parents", "us"),
    "query-siblings": Operation("collect selected nodes' siblings", "us"),
    "prune-shared": Operation("prune shared ancestors", "us"),
    "observe-registrations": Operation("reject unrelated observer registrations", "us"),
    "navigate": Operation("walk every descendant", "us"),
    "treewalk": Operation("walk every element (TreeWalker)", "us"),
    "chain": Operation("fluent jQuery-style chain", "us"),
    "range-clone": Operation("clone a Range over the body", "us"),
    "range-boundary": Operation("construct a Range at a child boundary", "us"),
    "range-contained": Operation("clone a Range of complete children", "us"),
    "range-partial": Operation("clone a Range with a partial element", "us"),
    "links-extract": Operation("extract every link", "us"),
    "links-absolutize": Operation("absolutize every link", "us"),
    "links-rewrite": Operation("rewrite every link", "us"),
    "socialcard": Operation("social-card extraction", "us"),
    "structured": Operation("structured-data extraction", "us"),
    "microdata": Operation("Microdata item extraction", "us"),
    "microdata-itemref": Operation("resolve Microdata item references", "ms"),
    "syndication": Operation("RSS/Atom feed parsing", "us"),
    "sanitize": Operation("sanitize", "us"),
    "sanitize-disallowed": Operation("sanitize disallowed tags", "us"),
    "sanitize-templates": Operation("sanitize (template-safe)", "us"),
    "sanitize-named-props": Operation("sanitize (named-prop isolation)", "us"),
    "sanitize-report": Operation("sanitize with audit trail", "us"),
    "sanitize-node": Operation("sanitize a parsed tree", "us"),
    "sanitize-attributes": Operation("sanitize attribute allowlists", "us"),
    "sanitize-styles": Operation("sanitize (style allowlist)", "us"),
    "sanitize-transform": Operation("sanitize (tag transform)", "us"),
    "sanitize-custom-elements": Operation("sanitize (custom elements)", "us"),
    "sanitize-xml": Operation("sanitize (XML/XHTML output)", "us"),
    "markup": Operation("markupsafe-compatible escape", "ns"),
    "markup-op": Operation("Markup operations", "ns"),
    "linkify": Operation("linkify HTML", "us"),
    "linkify-node": Operation("linkify a parsed tree", "us"),
    "linkify-traversal": Operation("linkify with native tree traversal", "us"),
    "detect": Operation("detect links in text", "us"),
    "phone": Operation("detect phone numbers in text", "us"),
    "phone-parse": Operation("parse held phone numbers", "us"),
    "phone-format": Operation("format phone numbers", "us"),
    "markdown-wrap": Operation("wrap short Markdown words", "us"),
    "markdown": Operation("HTML to Markdown", "us"),
    "markdown-google": Operation("Google Docs export to Markdown", "us"),
    "tables": Operation("extract table grids", "us"),
    "tables-wide": Operation("extract a wide table grid", "us"),
    "tables-spans": Operation("extract repeated table span text", "us"),
    "article": Operation("article extraction", "us"),
    "boilerplate": Operation("paragraph boilerplate classification", "us"),
    "date-tally": Operation("score visible date candidates", "us"),
    "date": Operation("publication-date extraction", "us"),
    "text-render": Operation("layout-aware text", "us"),
    "text-collapsed": Operation("collapsed word stream", "us"),
    "text-main": Operation("main-content text", "us"),
    "text-annotated": Operation("annotated layout text", "us"),
    "extract-attr": Operation("extract @href per match", "us"),
    "extract-text": Operation("extract text per match", "us"),
    "extract-url": Operation("extract URL hints", "us"),
    "htmlparser": Operation("feed and dispatch a page", "us"),
    "sax": Operation("SAX parse a page (no tree)", "us"),
    "sax-records": Operation("iterate typed SAX records", "us"),
    "sax-records-callback": Operation("dispatch SAX callbacks", "us"),
    "treebuild": Operation("parse into a custom tree (no DOM)", "us"),
    "rewrite": Operation("streaming rewrite a page (no tree)", "us"),
    "rewrite-attributes": Operation("add custom attributes", "us"),
    "path": Operation("css_path for every element", "us"),
    "path-xpath": Operation("xpath_path for every element", "us"),
    "translate": Operation("CSS selector to XPath 1.0", "us"),
    "specificity": Operation("CSS selector specificity", "us"),
    "xpath": Operation("XPath feature surface (9.6 kB)", "us"),
    "xpath-id": Operation("resolve 1,000 XPath id tokens", "us"),
    "transform": Operation("XSLT transform a catalog (120 rows)", "us"),
    "transform-compile": Operation("compile an XSLT stylesheet with 300 templates", "us"),
    "transform-reuse": Operation("apply one compiled 300-template stylesheet ten times", "us"),
    "transform-sort": Operation("XSLT sort node sets", "ms"),
    "transform-key": Operation("build XSLT key indexes", "us"),
    "transform-dense": Operation("XSLT transform an instruction-dense sheet", "us"),
    "transform-names-compile": Operation("compile XSLT declaration indexes", "us"),
    "transform-names": Operation("resolve XSLT declaration names", "us"),
    "transform-rules": Operation("dispatch XSLT template rules", "us"),
    "transform-number": Operation("XSLT number nodes", "us"),
    "minify-css": Operation("minify CSS", "us"),
    "minify-css-conflicts": Operation("merge CSS rules across disjoint declarations", "us"),
    "minify-css-merges": Operation("batch CSS rule merges", "us"),
    "minify-js": Operation("minify a JS library", "ms"),
    "minify-js-names": Operation("minify JavaScript with function parameters", "us"),
    "minify-js-integers": Operation("print JavaScript integer arrays", "us"),
    "minify-js-unlink": Operation("remove mixed JavaScript declarators", "us"),
    "minify-js-unused-declarations": Operation("remove unused JavaScript declarators", "us"),
    "minify-js-var-initialization": Operation("check JavaScript var initialization order", "us"),
    "minify-js-sequences": Operation("minify expression sequences", "us"),
    "minify-js-propagation": Operation("propagate repeated JavaScript literal reads", "us"),
    "minify-js-single-use": Operation("check single-use JavaScript initializers", "us"),
    "minify-js-guards": Operation("minify guard return chains", "us"),
    "stream": Operation("push-parse a page in chunks", "us"),
    "encoding-result": Operation("construct the winning encoding result", "us"),
    "encoding-result-stream": Operation("construct the streamed encoding result", "us"),
    "encoding": Operation("detect a byte stream's encoding", "us"),
    "decode": Operation("decode a legacy byte stream", "us"),
    "normalize": Operation("normalize text to Unicode NFC", "us"),
    "normalize-dom": Operation("merge adjacent text nodes", "ns"),
    "attribute-grow": Operation("set element attributes", "us"),
    "normalize-marks": Operation("normalize long combining-mark runs", "ms"),
    "detect-language": Operation("detect a text's natural language", "us"),
    "detect-language-long": Operation("count trigrams in long prose", "ms"),
    "escape-identifier": Operation("escape 1,000 raw CSS identifiers", "us"),
    "idna": Operation("normalize 4,100 URLs with Unicode hosts", "ms"),
    "urls-clean": Operation("clean and normalize 100 URLs", "us"),
    "links-filter": Operation("extract filtered page links", "us"),
    "links-external": Operation("extract links outside the base site", "ms"),
    "serialize-attributes": Operation("serialize HTML attribute order", "us"),
    "serialize-named": Operation("serialize HTML named entities", "us"),
    "transform-text": Operation("XSLT text emission", "us"),
}


def _sax_record_cases() -> tuple[tuple[str, object], ...]:
    return (
        ("4,096 small elements", "<!DOCTYPE html>" + "<p>x</p>" * 4096),
        ("one small element", "<p>x</p>"),
    )


def _parse_cases() -> tuple[tuple[str, object], ...]:
    """Return the corpus documents the parse suite runs over (loaded from the html5lib-python submodule)."""
    return (
        *((name, corpus.corpus_text(relative, encoding)) for name, relative, encoding in corpus.CORPUS_FILES),
        ("common tags (13 kB)", "<div><span>x</span></div>" * 500),
    )


def _dispatch_cases() -> tuple[tuple[str, object], ...]:
    return tuple((f"{count} stages", count) for count in (0, 1, 4, 16))


def _collapse_cases() -> tuple[tuple[str, object], ...]:
    return (
        *_readpath_cases(),
        ("unchanged text (1 MiB)", "<p>" + "x" * 1_048_576 + "</p>"),
        ("whitespace runs (1 MiB)", "<p>" + "x  " * 349_525 + "</p>"),
    )


def _readpath_cases() -> tuple[tuple[str, object], ...]:
    """
    Return the pages the read-path operations parse once then query.

    Real saved web pages (a blog, a news article, a product blog) spanning 10-95 kB, plus the whatwg spec at 235 kB.
    The wpt fixtures they replace are CSS layout tests with no nested ``div``/``a`` or links, so ``div a[href]``,
    ``div:has(a)``, and the link/edit/chain/extract operations matched nothing and timed empty walks; these carry the
    real structure those operations exist to traverse.
    """
    pages = [(name, corpus.large_text(filename, url)) for name, filename, url in corpus.REAL_PAGES]
    label, relative, encoding = corpus.CORPUS_FILES[5]  # whatwg spec (235 kB), the large content page
    pages.append((label, corpus.corpus_text(relative, encoding)))
    return tuple(pages)


def _radio_group_cases() -> tuple[tuple[str, tuple[int, int, int, str]], ...]:
    return (
        ("document radios, index build and 64 changes", (4096, 1, 64, "document")),
        ("small form, one unindexed change", (0, 1, 1, "plain")),
        ("one form among 256, index build and 64 changes", (0, 256, 64, "select")),
        ("eight changes with index invalidation", (256, 1, 8, "mutate")),
    )


def _form_data_fieldset_cases() -> tuple[tuple[str, str], ...]:
    return tuple(
        (
            label,
            "<form>"
            + opening
            + "<div>" * depth
            + "".join(f'<input name="n{index}" value="v{index}">' for index in range(count))
            + "</div>" * depth
            + closing
            + '<input name="tail" value="ok"></form>',
        )
        for label, opening, closing, depth, count in (
            ("disabled / 2,048 controls / depth 64", "<fieldset disabled>", "</fieldset>", 64, 2_048),
            (
                "first legend / 2,048 disabled controls / depth 64",
                '<fieldset disabled><legend><input name="allowed" value="yes"></legend>',
                "</fieldset>",
                64,
                2_048,
            ),
            ("enabled / 2,048 controls / depth 64", "<fieldset>", "</fieldset>", 64, 2_048),
            ("plain / 4 controls", "", "", 0, 4),
        )
    )


def _computed_style_selector_cases() -> tuple[tuple[str, str], ...]:
    return tuple(
        (
            label,
            f"<!doctype html><style>div{{color:blue}}div{selector}{{color:red}}</style>"
            + ("<div>" * count + leaf + "</div>" * count if nested else "<div></div>" * count),
        )
        for label, selector, count, nested, leaf in (
            ("deep missing :has / 512 elements", ":has(.hit)", 512, True, "<span></span>"),
            ("deep matching :has / 512 elements", ":has(.hit)", 512, True, '<span class="hit"></span>'),
            ("wide :nth-child / 4,096 elements", ":nth-child(odd)", 4_096, False, ""),
            ("shallow :has / 8 elements", ":has(.hit)", 8, True, '<span class="hit"></span>'),
        )
    )


def _deep_tree(depth: int, leaves: int) -> str:
    """
    Build a deep, wide, xlink-free tree: a spine of ``depth`` nested divs, each carrying ``leaves`` leaf spans.

    Canonicalization decides every element's xmlns:xlink from whether the prefix is in scope, which the c14n serializer
    resolved by walking node->root; the deep spine makes that per-element walk O(depth) and, xlink-free, the walk always
    ran to the root before short-circuiting. It is the workload the drop-the-redundant-walk change (#603) turned into a
    single local attr scan, which a shallow real page barely registers.
    """
    spine = "".join(
        f"<div class='n{level}'>" + "".join(f"<span>t{level}-{leaf}</span>" for leaf in range(leaves))
        for level in range(depth)
    )
    body = spine + "</div>" * depth
    return f"<!doctype html><html><body>{body}</body></html>"


_TOKENIZE_CASES = (
    ("typical markup", '<div class="row"><p>Tom &amp; Jerry said "hi" to <b>O\'Brien</b>!</p><br/></div>\n' * 60),
    ("text-heavy prose", "<p>" + "the quick brown fox jumps over the lazy dog " * 100 + "</p>"),
    (
        "attribute-heavy",
        '<a href="https://example.com/path?q=1" title="example" rel="noopener" target="_blank" data-x=y>link</a>\n'
        * 60,
    ),
    ("script-heavy", "<script>function f(a, b) { return a < b && b > a; }</script>\n" * 60),
    ("entity-heavy", "<p>caf&eacute; &amp; r&eacute;sum&eacute; &#127881; &lt;tag&gt;</p>\n" * 60),
)

_FRAGMENT_HTML = "<tr><td>cell</td><td><a href='/x'>link</a></td></tr>" * 40

_MARKUP_ESCAPE_CASES = (
    ("clean (8 B)", "a value!"),
    ("clean (32 B)", "The quick brown fox jumped ok"),
    ("clean (256 B)", "The quick brown fox jumps over the lazy dog. " * 6),
    ("name with ' and &", "O'Brien & Sons"),
    ("escape-heavy markup", '<a href="/x?a=1&b=2">click & go</a>' * 2),
)

_MARKUP_OPS_HTML = "<p>Hello <b>bold</b> &amp; <i>italic</i>, see <a href='/x'>caf&eacute;</a> &#127881;</p>"
_MARKUP_FORMAT_ARGS = ("<script>alert(1)</script>", "Tom & Jerry")
_MARKUP_JOIN_PARTS = ("<a href='/x'>link</a>", "Tom & Jerry", "<b>bold</b>", "plain text")

_LINKIFY_CASES = (
    ("comment (1 link, 1 email)", "Ping me at bob@example.com or see https://example.com for details."),
    ("prose (1 KiB)", "See https://example.com/path?q=1 and visit www.example.org for more. " * 15),
    ("markup (4 KiB)", '<p>Read <a href="https://kept.example">the post</a> then go to https://example.com/x. ' * 45),
)

_PHONE_PROSE = (
    "Our office moved last spring, so the front desk now answers on 650-253-0000 during business hours and the "
    "support line +44 20 7946 0958 ext. 12 after six. Invoices quote order 48213 and ship within 3 days; the "
    "warehouse (2 pages) lists 12 pallets, 4 crates and 1,240 units per lot. "
)
_PHONE_NOISE = (
    "Released 3/10/2011 at 12:30:45 from 192.168.0.1; ISBN 978-0-306-40615-7, card 4111 1111 1111 1111, ticket "
    "4711, pages 1-5 (3 pages), version 2.4.16.1, 2012-01-02 08:00, order 10293847, ref 5551234, +1 (555) 555-5555. "
)
_PHONE_MIXED = (
    "Call 650-253-0000 or (650) 253-0001, fax 011 44 20 7946 0958, mobile 07400 123456, Berlin 030 12345678, "
    "Mumbai 022 2345 6789, +55 11 96123-4567, +81 3-1234-5678, order 98211 shipped 3/10/2011; not 123-456-7890. "
)
_PHONE_ADVERSARIAL = " ".join(["1234"] * 21) + " " + "1" * 21 + " " + ".".join(["12"] * 21) + " "
_PHONE_FULLWIDTH = (
    "\uff0b\uff14\uff14 \uff12\uff10 \uff17\uff19\uff14\uff16 \uff10\uff19\uff15\uff18 or "
    "\uff16\uff15\uff10-\uff12\uff15\uff13-\uff10\uff10\uff10\uff10, then prose. "
)
_PHONE_CASES: Final[tuple[tuple[str, tuple[str, str]], ...]] = (
    ("mixed corpus, valid (1 KiB)", ("valid", _PHONE_MIXED * 5)),
    ("mixed corpus, possible (1 KiB)", ("possible", _PHONE_MIXED * 5)),
    ("mixed corpus, 8 regions (1 KiB)", ("regions-8", _PHONE_MIXED * 5)),
    ("prose (1 KiB)", ("valid", _PHONE_PROSE * 3)),
    ("prose (100 KiB)", ("valid", _PHONE_PROSE * 300)),
    ("noise: dates, prices, IPv4, cards (1 KiB)", ("valid", _PHONE_NOISE * 5)),
    ("adversarial digit runs, 1 region (1 KiB)", ("valid", _PHONE_ADVERSARIAL * 6)),
    ("adversarial digit runs, 8 regions (1 KiB)", ("regions-8", _PHONE_ADVERSARIAL * 6)),
    ("fullwidth (1 KiB)", ("valid", _PHONE_FULLWIDTH * 15)),
    ("ucs2 prose, no digits (100 KiB)", ("valid", "\u4e2d\u6587\u7684\u6587\u672c\u6bb5\u843d " * 12_800)),
    ("ucs4 prose, no digits (100 KiB)", ("valid", "\U0001f600 emoji prose here " * 4_000)),
    ("short text, 1 region (40 B)", ("valid", "call 650-253-0000 or write to us today")),
    ("short text, 8 regions (40 B)", ("regions-8", "call 650-253-0000 or write to us today")),
    ("has_link, mixed corpus (1 KiB)", ("has", _PHONE_MIXED * 5)),
)

# Twenty numbers people hold as strings, one per region and written form, so parse and format each take one
# reading of every national prefix style, extension marker and layout the tables cover.
_PHONE_HELD: Final[tuple[tuple[str, str], ...]] = (
    ("US", "+1 650-253-0000"),
    ("US", "(650) 253-0000 ext. 12"),
    ("US", "6502530000"),
    ("GB", "020 7946 0958"),
    ("GB", "+44 20 7946 0958 x12"),
    ("DE", "030 12345678"),
    ("DE", "+49 1512 3456789"),
    ("FR", "01 23 45 67 89"),
    ("IT", "06 1234 5678"),
    ("ES", "612 34 56 78"),
    ("BR", "(11) 96123-4567"),
    ("IN", "098765 43210"),
    ("JP", "03-1234-5678"),
    ("AU", "(02) 1234 5678"),
    ("AR", "011 15-2345-6789"),
    ("MX", "222 123 4567"),
    ("RU", "8 (912) 345-67-89"),
    ("CN", "131 2345 6789"),
    ("KR", "02-123-4567"),
    ("SG", "6123 4567"),
)
_LINKIFY_TRAVERSAL_CASES: Final[tuple[tuple[str, tuple[str, str]], ...]] = (
    ("text-heavy tree", ("default", "<article><p>" + "plain prose " * 8_000 + "https://example.com</p></article>")),
    ("2,000 small text nodes", ("default", "<div>" + "<span>plain</span>" * 2_000 + "</div>")),
    ("2,000 skipped nodes", ("skip", "<code><span>https://example.com</span></code>" * 2_000)),
    (
        "400 callback links",
        ("callbacks", '<a href="https://kept.example">kept</a> https://example.com ' * 200),
    ),
    ("2,000 nodes without text", ("default", "<div></div>" * 2_000)),
    ("wide text, two callbacks", ("callbacks", "<p>" + "😀 " * 32_768 + "https://example.com</p>")),
)

_FIND_COLD_BODY: Final[str] = "<span>x</span>" * 10_000
_FIND_COLD_CASES: Final[tuple[tuple[str, tuple[str, str]], ...]] = (
    ("find early hit", ("find", "<a>x</a>" + _FIND_COLD_BODY)),
    ("find late hit", ("find", _FIND_COLD_BODY + "<a>x</a>")),
    ("find miss", ("find", _FIND_COLD_BODY)),
    ("find_all", ("all", "<a>x</a>" + _FIND_COLD_BODY)),
    ("find_all limit=1", ("limit", "<a>x</a>" + _FIND_COLD_BODY)),
)

_MARKDOWN_ARTICLE = "<h2>Heading</h2><p>A <b>bold</b> <a href='/x'>link</a> and <code>code</code>.</p>" * 18
_MARKDOWN_LIST = "<ul><li>item <em>one</em></li><li>item two<ul><li>nested</li></ul></li></ul>" * 40
_MARKDOWN_TABLE = "<table><tr><th>Name</th><th>Value</th></tr><tr><td>a</td><td>1</td></tr></table>" * 35
_MARKDOWN_CONFIGURED = (
    "<h2>H</h2><p>A <b>b</b> & <a href='/x'>l</a>.</p>"
    "<table><tr><th>K</th><th>V</th></tr><tr><td>a</td><td>1</td></tr></table>"
) * 18
_MARKDOWN_GOOGLE = (
    '<p><span style="font-weight:700">Bold</span> and <span style="font-style:italic">italic</span> and '
    "<span style=\"font-family:'Courier New'\">code()</span> in a line.</p>"
) * 18

_TEXT_ARTICLE = "<h2>Heading</h2><p>A paragraph of plain prose with a <a href='/x'>link</a> in it.</p>" * 16
_TEXT_TABLE = "<table><tr><th>Region</th><th>Total</th></tr><tr><td>North</td><td>120</td></tr></table>" * 30
_TEXT_MAIN = (
    "<html><head><title>Comets</title></head><body>"
    "<nav><a href='/'>Home</a> <a href='/science'>Science</a></nav>"
    "<article><h1>Comets</h1>"
    + (
        "<p>A comet is an icy small body that, when it passes close to the Sun, warms up and releases gases, forming a "
        "glowing coma around it.</p>" * 12
    )
    + "</article><footer><p>Copyright notice, all rights reserved here.</p></footer></body></html>"
)
_TEXT_ANNOTATED = "<h1>Q3</h1><p>Up <b>12%</b> with a <a href='/x'>link</a> in prose.</p>" * 16

_URL_HINT_HTML = (
    "<html><head><base href='/sub/'>"
    "<meta http-equiv='refresh' content='5; url=next.html'>"
    "<title>Doc</title></head><body><p>Body copy.</p></body></html>"
)

_TRANSLATE_CASES = (
    ("type", "div"),
    ("compound", "div.item a[href^='https']"),
    ("structural", "ul li:nth-child(2n+1)"),
    ("complex", "nav ul > li a[href$='.pdf']:not(.external)"),
    ("group", "h1, h2, h3, section .title"),
)

_SVG_FRAGMENT = "<svg><rect/><rect/></svg>"
_XPATH_FEATURES = (
    "//div",
    "//a[@href]",
    "//div//a[@href]",
    "/html/body/div",
    "//div//a[1]",
    "//a[contains(@href, '/')]",
    "//div[position() <= 3]",
    "//a/ancestor::div",
    "//a | //span",
    "//*[local-name() = 'a']",
    "count(//a)",
)
_XPATH_PARITY = (
    ("//a[@href=$x] (variable)", "variable"),
    ("//a[re:test(@href, ...)] (EXSLT)", "re:test"),
    ("//a[ends-with(@href, ...)] (XPath 2.0)", "ends-with"),
    ("string-join(//a/@href, ...) (XPath 2.0)", "string-join"),
    ("//a[lower-case(@href) = ...] (XPath 2.0)", "lower-case"),
    ("//a[matches(@href, ...)] (XPath 2.0)", "matches"),
    ("replace(//a/@href, ...) (XPath 2.0)", "replace"),
    ("set:distinct(//a) (EXSLT)", "set:distinct"),
    ("//a/@href (smart_strings)", "smart_strings"),
    ("ext(//a) (extensions)", "extension"),
    ("ext(//a)/@href (node-set extension)", "nodeset_extension"),
    ("//svg:rect (namespaces=)", "namespaces"),
    ("$rows/div (node-set variable)", "node_set_variable"),
    ("//a[@href] (precompiled, reused)", "precompiled"),
)
_XPATH_ID_DOC: Final = "".join(f"<i id=r{index}></i>" for index in range(5_000))


def _table_html(data_rows: int) -> str:
    """Build a header plus ``data_rows`` four-column body rows, with a colspan to exercise span resolution."""
    header = "<tr><th>Region</th><th>Quarter</th><th>Revenue</th><th>Units</th></tr>"
    body = "".join(
        f"<tr><td>R{index}</td><td>Q{index % 4 + 1}</td><td colspan=2>{index * 10}</td></tr>"
        for index in range(data_rows)
    )
    return f"<table>{header}{body}</table>"


def _css_merge_inputs() -> tuple[tuple[str, str], ...]:
    return (
        *(
            (
                f"{count} identical media blocks",
                "".join(f"@media screen{{.a{index}{{color:red}}}}" for index in range(count)),
            )
            for count in (10, 100, 1_000)
        ),
        *(
            (f"{count} identical declaration bodies", "".join(f".a{index}{{color:red}}" for index in range(count)))
            for count in (10, 100, 1_000)
        ),
        (
            "100 alternating media preludes",
            "".join(f"@media {'screen' if index % 2 else 'print'}{{.a{index}{{color:red}}}}" for index in range(100)),
        ),
        ("100 rules with comment barriers", "/*!keep*/".join(f".a{index}{{color:red}}" for index in range(100))),
    )


def _css_conflict_inputs() -> tuple[tuple[str, str], ...]:
    return tuple(
        (
            f"{repeats} rule pairs, {properties} properties, {value_length} value bytes",
            (
                ".a{"
                + ";".join(f"--a{index}:f({'x' * value_length})" for index in range(properties))
                + "}"
                + ".b{"
                + ";".join(f"--b{index}:f({'y' * value_length})" for index in range(properties))
                + "}"
            )
            * repeats,
        )
        for repeats, properties, value_length in ((32, 32, 128), (32, 32, 1), (1, 2, 1))
    )


def _article_page(paragraphs: int) -> str:
    """Build a full page -- navigation, a scored article of ``paragraphs`` paragraphs, and a footer."""
    head = (
        "<html lang=en><head><title>Comets: A Field Guide</title>"
        "<meta name=author content='Ada Lovelace'>"
        "<meta property=article:published_time content='2024-05-06'>"
        "<meta name=description content='A short guide to comets and the tails they trail past the Sun.'></head>"
    )
    nav = "<body><nav><a href='/'>Home</a> <a href='/science'>Science</a> <a href='/space'>Space</a></nav>"
    para = (
        "<p>A comet is an icy small body that, when it passes close to the Sun, warms up, begins to release gases, "
        "and forms a glowing coma, a thin atmosphere, around it.</p>"
    )
    article = f"<article class=post><h1>Comets</h1>{para * paragraphs}</article>"
    return f"{head}{nav}{article}<footer><p>Copyright notice, all rights reserved here.</p></footer></body></html>"


_FILTER_STYLED_PAGES: Final = tuple(
    (
        f"256 rules / 128 parent-child pairs / {label}",
        "<style>"
        + (selector + "{color:red}") * 256
        + "div{color:blue}</style>"
        + '<div class="hit"><span></span></div>' * 128,
    )
    for label, selector in (
        ("irrelevant pseudo-first", ":has(span).missing"),
        ("matching pseudo-first", ":has(span).hit"),
        ("irrelevant class-first", ".missing:has(span)"),
    )
)


_SPECIFICITY_STYLED_PAGES: Final = tuple(
    (
        f"256 rules / 128 elements / {label}",
        "<style>" + (selector + "{color:red}") * 256 + "</style>" + '<div class="hit"></div>' * 128,
    )
    for label, selector in (
        (
            "nested alternatives",
            ":is(.hit," + ",".join(f"#absent{index}" for index in range(32)) + "):where(.hit),#missing",
        ),
        ("ordinary alternatives", ".hit,#missing"),
    )
)


_STYLE_SHEET = dedent("""\
    body { color: #222; font-size: 16px; line-height: 1.5; margin: 0 }
    a { color: navy; text-decoration: underline }
    a:hover { color: teal }
    nav a { font-weight: bold; text-transform: uppercase }
    article { max-width: 40em; margin: 0 auto; padding: 1em 2em }
    article h2 { font-size: 24px; border-bottom: 1px solid #ccc }
    article p { margin: 0 0 1em }
    .note { background-color: #ffd; border: 1px dashed #cc0; padding: 4px 8px }
    .note.warning { border-color: red; color: darkred }
    #lead { font-size: 18px !important; font-weight: 300 }
    ul.tags li { display: inline; margin-right: 6px; color: gray }
    footer { color: #888; font-size: 12px; text-align: center }
    """)


def _styled_page(sections: int) -> str:
    """Build a styled page: a ``<style>`` sheet plus ``sections`` repeated, class- and id-tagged content blocks."""
    section = (
        "<article><h2>Comets</h2><p id=lead>An icy small body that warms near the Sun.</p>"
        "<p class=note>Read the <a href='/space'>space</a> guide.</p>"
        "<p class='note warning'>Do not stare at the Sun.</p>"
        "<ul class=tags><li>ice</li><li>coma</li><li>tail</li></ul></article>"
    )
    nav = "<nav><a href='/'>Home</a> <a href='/science'>Science</a> <a href='/space'>Space</a></nav>"
    return (
        f"<html lang=en><head><style>{_STYLE_SHEET}</style></head><body>{nav}"
        f"{section * sections}<footer>Copyright notice</footer></body></html>"
    )


# every longhand the cascade tracks, each declared once, so resolving one rule runs css_prop_id (the name->id lookup)
# over the whole property surface -- the lookup the property table's binary search (#604) replaced a 63-row linear scan
_DENSE_DECLS = (
    "color:#123456;font-size:15px;font-style:italic;font-weight:600;font-variant:small-caps;"
    "line-height:1.4;text-align:justify;text-indent:2px;text-transform:uppercase;letter-spacing:1px;"
    "word-spacing:2px;white-space:nowrap;visibility:visible;list-style-type:square;list-style-position:inside;"
    "cursor:pointer;direction:ltr;caption-side:bottom;display:block;position:relative;top:1px;right:2px;"
    "bottom:3px;left:4px;float:left;clear:both;width:50px;height:60px;min-width:10px;min-height:20px;"
    "max-width:500px;max-height:600px;margin-top:1px;margin-right:2px;margin-bottom:3px;margin-left:4px;"
    "padding-top:1px;padding-right:2px;padding-bottom:3px;padding-left:4px;border-top-width:1px;"
    "border-right-width:2px;border-bottom-width:3px;border-left-width:4px;border-top-style:solid;"
    "border-right-style:dashed;border-bottom-style:dotted;border-left-style:double;border-top-color:#111;"
    "border-right-color:#222;border-bottom-color:#333;border-left-color:#444;background-color:#eee;"
    "background-image:none;opacity:0.9;z-index:5;overflow-x:hidden;overflow-y:scroll;vertical-align:middle;"
    "box-sizing:border-box;outline-width:1px;outline-style:solid;outline-color:#555"
)
# four rules every element matches (the universal rule plus the three classes it carries), so each element resolves the
# full longhand set four times over, the per-declaration css_prop_id lookup a utility-class framework really produces
_DENSE_SHEET = "".join(f"{selector} {{{_DENSE_DECLS}}}\n" for selector in ("*", ".u", ".v", ".w"))


def _dense_styled_page(sections: int) -> str:
    """Build a page whose stylesheet declares every longhand across four rules each of ``sections`` blocks matches."""
    section = (
        "<section class='u v w'><div class='u v w'><p class='u v w'>Text with a "
        "<a class='u v w' href='/x'>link</a> and <span class='u v w'>inline</span>.</p>"
        "<ul class='u v w'><li class='u v w'>one</li><li class='u v w'>two</li></ul></div></section>"
    )
    return f"<html><head><style>{_DENSE_SHEET}</style></head><body>{section * sections}</body></html>"


def _xpath_cases() -> tuple[tuple[str, object], ...]:
    """Return one (label, (kind, text)) pair per XPath feature over the 9.6 kB page; the namespaced row carries SVG."""
    _name, relative, encoding = corpus.CORPUS_FILES[2]
    text = corpus.corpus_text(relative, encoding)
    structural = tuple((f"``{feature}``", (feature, text)) for feature in _XPATH_FEATURES)
    parity = tuple(
        (label, (kind, text + _SVG_FRAGMENT if kind == "namespaces" else text)) for label, kind in _XPATH_PARITY
    )
    return structural + parity


_XSLT_SHEET = (
    '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
    '<xsl:output method="html"/>'
    '<xsl:key name="by-cat" match="book" use="@cat"/>'
    '<xsl:template match="/"><table>'
    '<xsl:apply-templates select="catalog/book">'
    '<xsl:sort select="price" data-type="number" order="descending"/></xsl:apply-templates>'
    "</table></xsl:template>"
    '<xsl:template match="book"><tr class="{@cat}"><td><xsl:number format="1"/></td>'
    '<td><xsl:value-of select="title"/></td>'
    "<td><xsl:value-of select=\"format-number(price, '#,##0.00')\"/></td>"
    "<td><xsl:value-of select=\"count(key('by-cat', @cat))\"/></td></tr></xsl:template>"
    "</xsl:stylesheet>"
)
_XSLT_SOURCE = (
    "<catalog>"
    + "".join(
        f'<book cat="c{index % 5}"><title>Book number {index}</title><price>{index % 97 + 0.99}</price></book>'
        for index in range(120)
    )
    + "</catalog>"
)


def _transform_text_cases() -> tuple[tuple[str, object], ...]:
    cases: Final[list[tuple[str, object]]] = []
    payload: Final = "payload " * 8
    for kind in ("builtin", "literal", "xsl:text"):
        for count in (4096, 4):
            if kind == "builtin":
                body = "<xsl:apply-templates/>"
                source = "<r>" + ("<p>" + payload + "</p>") * count + "</r>"
            else:
                body = (payload + "<!--gap-->" if kind == "literal" else "<xsl:text>" + payload + "</xsl:text>") * count
                source = "<r/>"
            sheet = (
                '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
                '<xsl:output method="text"/><xsl:template match="/">' + body + "</xsl:template></xsl:stylesheet>"
            )
            cases.append((f"{count} {kind} text nodes", (sheet, source)))
    return tuple(cases)


def _transform_key_cases() -> tuple[tuple[str, object], ...]:
    return tuple(
        (
            f"{label} ({count:,} nodes)",
            (
                (
                    '<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="1.0">'
                    f'<xsl:key name="k" match="i" use="{use}"/><xsl:output method="text"/>'
                    f'<xsl:template match="/"><xsl:value-of select="count(key(&quot;k&quot;,&quot;{wanted}&quot;))"/>'
                    "</xsl:template></xsl:stylesheet>"
                ),
                "<r>" + "".join(item.format(index=index) for index in range(count)) + "</r>",
            ),
        )
        for label, use, wanted, item, count in (
            ("shared scalar key", "'same'", "same", "<i/>", 8192),
            ("shared attribute key", "@key", "same", '<i key="same"/>', 8192),
            ("repeated node-set keys", "t", "same", "<i><t>same</t><t>other</t><t>same</t></i>", 2048),
            ("unique keys", "@key", "k0", '<i key="k{index}"/>', 8192),
            ("small shared key", "'same'", "same", "<i/>", 4),
        )
    )


def _transform_cases() -> tuple[tuple[str, object], ...]:
    """Return the one XSLT case: a real stylesheet (sort, key, number, format-number) over a 120-row catalog."""
    return (("catalog (120 rows)", (_XSLT_SHEET, _XSLT_SOURCE)),)


_XSLT_COMPILE_SHEET: Final[str] = (
    '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
    '<xsl:output method="text"/><xsl:template match="/"><xsl:call-template name="t299"/></xsl:template>'
    + "".join(
        f'<xsl:template name="t{index}"><xsl:value-of select="\'{index}\'"/></xsl:template>' for index in range(299)
    )
    + '<xsl:template name="t299">'
    + '<xsl:number count="root" from="/"/>' * 24
    + "</xsl:template>"
    + "</xsl:stylesheet>"
)


def _transform_compile_cases() -> tuple[tuple[str, object], ...]:
    """Make static analysis outweigh the one used template."""
    return (("300 templates", (_XSLT_COMPILE_SHEET, "<root/>")),)


_XSLT_SORT_SHEET: Final = (
    '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
    '<xsl:template match="/"><out><xsl:for-each select="r/n">'
    '<xsl:sort select="@key" data-type="number"/><xsl:value-of select="@key"/><xsl:text>,</xsl:text>'
    "</xsl:for-each></out></xsl:template></xsl:stylesheet>"
)


def _transform_sort_cases() -> tuple[tuple[str, object], ...]:
    return tuple(
        (
            f"{label} ({rows:,} rows)",
            (
                _XSLT_SORT_SHEET.replace('select="@key" data-type="number"', f'select="{select}" data-type="{kind}"'),
                "<r>" + "".join(f'<n key="{index * 73 % rows}"/>' for index in range(rows)) + "</r>",
            ),
        )
        for label, select, kind, sizes in (
            ("numeric sort", "@key", "number", (120, 2_000)),
            ("integer expression sort", "number(@key)", "number", (8, 2_000)),
            ("string expression numeric sort", "string(@key)", "number", (8, 2_000)),
            ("text sort", "@key", "text", (8, 2_000)),
            ("fraction expression sort", "number(@key) div 7", "number", (8, 2_000)),
        )
        for rows in sizes
    )


# one instruction unit weighted toward the elements late in the old is_xsl probe chain: copy-of, variable, number,
# comment and message (the last probe). Instantiating each dispatched through a chain of is_xsl calls that re-tested
# the xsl prefix per candidate; the classify-once switch (#605) tests it once, so a late instruction stops paying for
# the earlier probes. message is near free to run (a non-terminating one is discarded), so dispatch dominates its cost.
_XSLT_LATE_UNIT = (
    '<xsl:variable name="v{index}" select="@cat"/>'
    '<xsl:number format="1"/>'
    "<xsl:comment>c{index}</xsl:comment>"
    '<xsl:copy-of select="title"/>'
    "<xsl:message>m{index}</xsl:message>"
    "<xsl:message>n{index}</xsl:message>"
    "<xsl:message>o{index}</xsl:message>"
)
_XSLT_DENSE_SHEET = (
    '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
    '<xsl:output method="xml"/>'
    '<xsl:template match="/"><out><xsl:apply-templates select="catalog/book"/></out></xsl:template>'
    '<xsl:template match="book"><row><xsl:value-of select="title"/>'
    + "".join(_XSLT_LATE_UNIT.format(index=unit) for unit in range(8))
    + "</row></xsl:template>"
    "</xsl:stylesheet>"
)
_XSLT_DENSE_SOURCE = (
    "<catalog>"
    + "".join(
        f'<book cat="c{index % 5}"><title>Book number {index}</title><price>{index % 97 + 0.99}</price></book>'
        for index in range(200)
    )
    + "</catalog>"
)


def _transform_dense_cases() -> tuple[tuple[str, object], ...]:
    """Return the instruction-dense XSLT case: a template of 56 xsl:* instructions applied over 200 nodes."""
    return (("instruction-dense (200 nodes)", (_XSLT_DENSE_SHEET, _XSLT_DENSE_SOURCE)),)


def _transform_name_cases() -> tuple[tuple[str, object], ...]:
    return tuple(
        (
            f"{count} unused declarations per kind, 256 calls",
            (
                '<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="1.0">'
                '<xsl:output method="xml" omit-xml-declaration="yes"/>'
                + "".join(
                    f'<xsl:template name="unused{index}"/>'
                    f'<xsl:key name="unused{index}" match="unused" use="@value"/>'
                    f'<xsl:attribute-set name="unused{index}"/>'
                    for index in range(count)
                )
                + '<xsl:key name="target" match="p" use="@value"/>'
                '<xsl:attribute-set name="target"><xsl:attribute name="marker">hit</xsl:attribute></xsl:attribute-set>'
                '<xsl:template name="target"><item xsl:use-attribute-sets="target">'
                "<xsl:value-of select=\"count(key('target','v'))\"/></item></xsl:template>"
                '<xsl:template match="/"><out>'
                + '<xsl:call-template name="target"/>' * 256
                + "</out></xsl:template></xsl:stylesheet>",
                '<root><p value="v"/></root>',
            ),
        )
        for count in (256, 1)
    )


def _transform_number_cases() -> tuple[tuple[str, object], ...]:
    return (
        tuple(
            (
                (
                    f"{rows:,} {'sibling' if rows == 1 else 'siblings'} / "
                    f"{instructions} {'instruction' if instructions == 1 else 'instructions'} / {order}"
                ),
                (
                    '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
                    '<xsl:output method="text"/><xsl:template match="/"><xsl:for-each select="root/n">'
                    + ('<xsl:sort select="@id" data-type="number" order="descending"/>' if order == "reverse" else "")
                    + "<xsl:number/><xsl:text>:</xsl:text>" * instructions
                    + "<xsl:text>|</xsl:text></xsl:for-each></xsl:template></xsl:stylesheet>",
                    "<root>" + "".join(f'<n id="{index}"/>' for index in range(1, rows + 1)) + "</root>",
                ),
            )
            for rows, instructions, order in (
                (200, 1, "forward"),
                (200, 8, "forward"),
                (2_000, 1, "forward"),
                (2_000, 8, "forward"),
                (1, 8, "forward"),
                (200, 8, "reverse"),
                (200, 0, "forward"),
            )
        )
        + _transform_any_number_cases()
        + _transform_pattern_number_cases()
        + _transform_number_prefix_cases()
    )


def _transform_any_number_cases() -> tuple[tuple[str, object], ...]:
    return tuple(
        (
            f"any: {rows:,} {'node' if rows == 1 else 'nodes'} / {variant}",
            (
                '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
                '<xsl:output method="text"/><xsl:template match="/"><xsl:for-each select="root/*'
                + ("[last()]" if variant == "last-only" else "")
                + '">'
                + ('<xsl:sort select="@id" data-type="number" order="descending"/>' if variant == "reverse" else "")
                + (
                    '<xsl:number level="any"'
                    + (' count="p"' if variant == "explicit-count" else "")
                    + "/><xsl:text>:</xsl:text>"
                )
                * (8 if variant == "repeated" else 1)
                + "<xsl:text>|</xsl:text></xsl:for-each></xsl:template></xsl:stylesheet>",
                "<root>"
                + "".join(
                    ("text<!--gap-->" if variant == "mixed-nodes" else "")
                    + f'<{"q" if variant == "alternating" and index % 2 == 0 else "p"} id="{index}"/>'
                    for index in range(1, rows + 1)
                )
                + "</root>",
            ),
        )
        for rows, variant in (
            (32, "forward"),
            (1_024, "forward"),
            (1_024, "reverse"),
            (1_024, "last-only"),
            (1_024, "alternating"),
            (1_024, "mixed-nodes"),
            (1, "repeated"),
            (1_024, "explicit-count"),
            (2, "forward"),
            (8, "forward"),
        )
    )


def _transform_pattern_number_cases() -> tuple[tuple[str, object], ...]:
    return tuple(
        (
            f"any: {rows:,} {'node' if rows == 1 else 'nodes'} / {variant}",
            (
                '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
                '<xsl:output method="text"/><xsl:template match="/"><xsl:for-each select="root//p">'
                + (
                    '<xsl:sort select="@id" data-type="number" order="descending"/>'
                    if variant == "count-reverse"
                    else ""
                )
                + (
                    f'<xsl:number level="any" count="{pattern}"'
                    + (' from="section"' if variant == "count-from-sections" else "")
                    + "/><xsl:text>:</xsl:text>"
                )
                * (8 if variant == "count-repeated" else 1)
                + "<xsl:text>|</xsl:text></xsl:for-each></xsl:template></xsl:stylesheet>",
                "<root>"
                + "".join(
                    ("<section>" if variant == "count-from-sections" and index % 64 == 1 else "")
                    + f'<p id="{index}" cat="{index % 2}"/>'
                    + ("</section>" if variant == "count-from-sections" and index % 64 == 0 else "")
                    for index in range(1, rows + 1)
                )
                + "</root>",
            ),
        )
        for rows, pattern, variant in (
            (1, "p", "count-single-call"),
            (1, "p[true()]", "predicate-single-call"),
            (1, "p|q", "union-single-call"),
            (1_024, "p[true()]", "count-predicate"),
            (1_024, "p[@cat=current()/@cat]", "count-current"),
            (1_024, "p", "count-from-sections"),
            (1_024, "p", "count-reverse"),
            (1_024, "p", "count-repeated"),
            (1_024, "*", "count-wildcard"),
            (1_024, "missing", "count-empty"),
        )
    )


def _transform_number_prefix_cases() -> tuple[tuple[str, object], ...]:
    return tuple(
        (
            f"any: 1,024 nodes / {variant}",
            (
                (
                    '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
                    '<xsl:output method="text"/><xsl:template match="/">'
                    f'<xsl:for-each select="{select}"><xsl:number level="any" {attributes}/>'
                    "<xsl:text>:|</xsl:text></xsl:for-each></xsl:template></xsl:stylesheet>"
                ),
                "<root>"
                + "".join(
                    ("<section>" if sections and index % 64 == 1 else "")
                    + f'<{"q" if alternating and index % 2 == 0 else "p"} id="{index}"/>'
                    + ("</section>" if sections and index % 64 == 0 else "")
                    for index in range(1, 1_025)
                )
                + "</root>",
            ),
        )
        for variant, select, attributes, sections, alternating in (
            ("count-last-only", "root/p[last()]", 'count="p"', False, False),
            ("count-from-last-only", "(root/section/p)[last()]", 'count="p" from="section"', True, False),
            ("from-sections-alternating", "root/section/*", 'from="section"', True, True),
        )
    )


def _token_attribute_cases() -> tuple[tuple[str, object], ...]:
    return tuple(
        (f"{count} attributes", f"<p {' '.join(f'a{index}={index}' for index in range(count))}>")
        for count in (100, 0, 1, 10)
    )


def _tokenize_cases() -> tuple[tuple[str, object], ...]:
    """Return synthetic and corpus documents for the tokenization table."""
    corpus_cases = tuple(
        (name, corpus.corpus_text(relative, encoding)) for name, relative, encoding in corpus.CORPUS_FILES
    )
    large_cases = tuple((name, corpus.large_text(filename, url)) for name, filename, url in corpus.LARGE_FILES)
    return _TOKENIZE_CASES + corpus_cases + large_cases


def _minify_cases() -> tuple[tuple[str, object], ...]:
    return (
        *((name, corpus.large_text(filename, url)) for name, filename, url in corpus.STYLESHEETS),
        *(
            (
                f"{count} {'reversed' if reverse else 'sorted'} Unicode ranges",
                "@font-face{unicode-range:"
                + ",".join(f"U+{index * 2:X}" for index in (reversed(range(count)) if reverse else range(count)))
                + "}",
            )
            for count, reverse in ((8, True), (1024, False), (1024, True))
        ),
        *(
            (
                f"{count} rules, {label}",
                "".join(f".number{index}{{{value.format(index=index)}}}" for index in range(1, count + 1)),
            )
            for label, value, count in (
                ("zero exponent 10000", "width:calc(0e10000px + {index}px)", 1),
                ("zero exponent 10000", "width:calc(0e10000px + {index}px)", 128),
                ("zero exponent 2", "width:calc(0e2px + {index}px)", 128),
                ("ordinary arithmetic", "width:calc(1e2px + {index}px)", 128),
                ("negative exponent", "width:calc(1e-2px + {index}px)", 128),
                ("large dimension exponent", "width:1e10000px", 128),
                ("unitless exponent", "z-index:1e4", 128),
            )
        ),
    )


def _minify_js_cases() -> tuple[tuple[str, object], ...]:
    """Return the real-world JavaScript libraries the minify operation shrinks, a size ladder."""
    return tuple((name, corpus.large_text(filename, url)) for name, filename, url in corpus.JS_FILES)


_URL_SHAPES = (
    "https://www.example.org/dir/page-{index}.html",
    "HTTPS://EXAMPLE.ORG:443/dir/../page/{index}?utm_source=rss&utm_medium=email&id={index}",
    "https://example.org/de/beitrag-{index}?lang=de&page={index}#frag",
    " https://sub.example.org//double/{index}/&amp;x=1 ",
    "http://münchen.example/straße/{index}?b=2&a=1",
)
_URL_BATCH = tuple(shape.format(index=index) for index in range(20) for shape in _URL_SHAPES)
_IDNA_URLS: Final[tuple[str, ...]] = tuple(f"https://münchen-{index}.example/" for index in range(4100))
# The münchen workload repeats one host, so every lookup walks the same rows. These labels spread the searches across
# ranges a single Latin-1 vowel does not reach: Latin Extended, Greek (with the final sigma the mapping rewrites),
# Cyrillic, Arabic, Hebrew, Devanagari, Thai, CJK, and Hangul, whose jamo compose arithmetically rather than through
# the table. The last two labels are typed decomposed, so composition and canonical ordering run instead of falling
# through.
_IDNA_LABELS: Final[tuple[str, ...]] = (
    "münchen",
    "zürich",
    "gdańsk",
    "kraków",
    "plzeň",
    "tromsø",
    "straße",
    "ΟΔΥΣΣΕΥΣ",
    "αθήνα",
    "москва",
    "київ",
    "القاهرة",
    "ירושלים",
    "मुंबई",
    "กรุงเทพ",
    "東京",
    "北京",
    "서울",
    "cafe\u0301",  # e + combining acute, which composition folds back to the precomposed é
    "viet\u0301\u0323",  # an above mark typed before a below one, so canonical ordering has to swap them
)
_IDNA_VARIED_URLS: Final[tuple[str, ...]] = tuple(
    f"https://{_IDNA_LABELS[index % len(_IDNA_LABELS)]}-{index}.example/" for index in range(4100)
)
_EXTERNAL_LINKS_HTML: Final = "".join(
    f'<a href="https://{host}/post/{index}">link</a>'
    for index in range(300)
    for host in (f"news-{index}.example.co.uk", f"tenant-{index}.github.io", f"bucket-{index}.s3.amazonaws.com")
)

_ENCODING_ASCII = "The quick brown fox jumps over the lazy dog near the river bank early today. "
_ENCODING_FRENCH = "Précédemment, la créativité française était très développée près de Paris ici. "
_ENCODING_RUSSIAN = "Программирование помогает понять структуру вычислительных систем сегодня здесь. "
_ENCODING_JAPANESE = "日本語のテキストをここに書きます。今日はとても良い天気ですね。"


def _encoding_cases() -> tuple[tuple[str, object], ...]:
    """
    Return the byte streams the encoding-detection suite sniffs.

    Natural-language prose re-encoded into the legacy encodings the detectors compete on, plus pure ASCII (the
    short-circuit every detector special-cases) and a real saved page (the whole-document workload).
    """
    _name, filename, url = corpus.REAL_PAGES[2]  # the mozilla blog, 95 kB of real UTF-8 markup
    return (
        ("ascii (1 kB)", (_ENCODING_ASCII * 13).encode()),
        ("utf-8 russian (4 kB)", (_ENCODING_RUSSIAN * 27).encode()),
        ("windows-1251 russian (4 kB)", (_ENCODING_RUSSIAN * 50).encode("cp1251")),
        ("windows-1252 french (4 kB)", (_ENCODING_FRENCH * 50).encode("cp1252")),
        ("shift_jis japanese (4 kB)", (_ENCODING_JAPANESE * 70).encode("shift_jis")),
        ("utf-8 page (95 kB)", corpus.large_text(filename, url).encode()),
    )


def _decode_cases() -> tuple[tuple[str, object], ...]:
    """
    Return the byte streams the WHATWG decoders turn into str, each paired with the label that names its decoder.

    windows-1252 covers the single-byte tables, shift_jis and gb18030 the two- and four-byte state machines, and
    iso-2022-jp the stateful escapes. Every case encodes with the CPython codec the spec's decoder maps back to the
    prose, so a case times decoding rather than error recovery. Shift_JIS leads because CodSpeed gates the first case,
    and it is the state machine an inlined decoder regressed.
    """
    japanese = _decode_page(_ENCODING_JAPANESE)
    return (
        ("shift_jis japanese (8 kB)", ("shift_jis", japanese.encode("cp932"))),
        (
            "gb18030 astral (16 kB)",
            ("gb18030", "".join(chr(0x10000 + (index * 7919) % 0xE0000) for index in range(4096)).encode("gb18030")),
        ),
        ("windows-1252 french (9 kB)", ("windows-1252", _decode_page(_ENCODING_FRENCH).encode("cp1252"))),
        ("gb18030 japanese (8 kB)", ("gb18030", japanese.encode("gb18030"))),
        ("iso-2022-jp japanese (8 kB)", ("iso-2022-jp", japanese.encode("iso2022_jp"))),
    )


def _decode_page(text: str) -> str:
    """Wrap prose in tags, so the bytes are mostly ASCII markup: the shape of a real page in a legacy encoding."""
    rows = "".join(f'<p class="line" id="l{index}">{text}</p>' for index in range(60))
    return f"<html><body>{rows}</body></html>"


def _normalize_cases() -> tuple[tuple[str, object], ...]:
    """
    Return the strings the Unicode-normalization suite folds to NFC.

    The ASCII book matches the CodSpeed case. Already-NFC prose and a real page exercise the quick-check path, while
    the NFD-decomposed French forces the full decompose/reorder/compose pipeline.
    """
    _name, filename, url = corpus.REAL_PAGES[2]  # the mozilla blog, 95 kB of real UTF-8 markup
    french = _ENCODING_FRENCH * 50
    return (
        ("ascii book (64 KiB)", corpus.corpus("war-and-peace/2600.txt", 1 << 16)),
        ("nfc french (4 kB)", french),
        ("nfd french (4 kB)", unicodedata.normalize("NFD", french)),
        ("utf-8 page (95 kB)", corpus.large_text(filename, url)),
        ("32 KiB ASCII prefix, composable suffix", "abc " * 8192 + "e\u0301"),
        ("small ASCII prefix, composable suffix", "abc e\u0301"),
        ("4,096 composable combining pairs", "e\u0301" * 4096),
        ("4,096 Hangul jamo triples", "\u1100\u1161\u11a8" * 4096),
        ("10,000 Hebrew combining marks", "\u05d0" + "\u05b1" * 5000 + "\u05b0" * 5000),
    )


def _language_cases() -> tuple[tuple[str, object], ...]:
    return (
        ("english book (64 KiB)", corpus.corpus("war-and-peace/2600.txt", 1 << 16)),
        ("cyrillic prose (4 KiB)", _ENCODING_RUSSIAN * 50),
        ("hangul prose (4 KiB)", "한국어는 아름다운 언어이며 배우기 쉽고 재미있는 언어입니다. " * 100),
    )


def _wide_path_cases() -> tuple[tuple[str, str], ...]:
    return tuple((f"{size:,} siblings", f"<ul>{'<li>value</li>' * size}</ul>") for size in (100, 1_000, 10_000))


def _validate_rng_reuse_cases() -> tuple[tuple[str, tuple[str, str]], ...]:
    return (
        *(
            (
                f"{width} {kind} fields across {rows} rows",
                (
                    '<element xmlns="http://relaxng.org/ns/structure/1.0" name="root"><oneOrMore>'
                    '<element name="row">'
                    f"<{kind}>"
                    + "".join(
                        f'<optional><element name="field{index}"><text/></element></optional>' for index in range(width)
                    )
                    + '<element name="value"><text/></element>'
                    + f"</{kind}>"
                    + "</element></oneOrMore></element>",
                    "<root>" + "<row><value>text</value></row>" * rows + "</root>",
                ),
            )
            for kind, width, rows in (("group", 128, 64), ("interleave", 32, 64), ("group", 4, 4))
        ),
        (
            "16 recursive nodes",
            (
                (
                    '<grammar xmlns="http://relaxng.org/ns/structure/1.0"><start><ref name="node"/></start>'
                    '<define name="node"><element name="node"><optional><ref name="node"/></optional>'
                    "</element></define></grammar>"
                ),
                "<node>" * 16 + "</node>" * 16,
            ),
        ),
    )


def _validate_numeric_facet_cases() -> tuple[tuple[str, tuple[str, str]], ...]:
    return tuple(
        (
            label,
            (
                (
                    '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'
                    '<xs:element name="root"><xs:complexType><xs:sequence>'
                    '<xs:element name="value" minOccurs="0" maxOccurs="unbounded"><xs:simpleType>'
                    f'<xs:restriction base="xs:{kind}">{facets}</xs:restriction>'
                    "</xs:simpleType></xs:element></xs:sequence></xs:complexType></xs:element></xs:schema>"
                ),
                "<root>" + "<value>1234567890.1234567890123456789012</value>" * count + "</root>",
            ),
        )
        for label, kind, count, facets in (
            ("64 decimals without bounds", "decimal", 64, ""),
            (
                "64 decimals with bounds",
                "decimal",
                64,
                '<xs:minInclusive value="0"/><xs:maxInclusive value="100000000000000000000"/>',
            ),
            ("one decimal without bounds", "decimal", 1, ""),
            ("64 strings without bounds", "string", 64, ""),
        )
    )


def _validate_attribute_cases() -> tuple[tuple[str, tuple[str, str]], ...]:
    return tuple(
        (
            f"{size:,} attributes",
            (
                '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"><xs:element name="root"><xs:complexType>'
                + "".join(f'<xs:attribute name="item{index}" type="xs:string"/>' for index in range(size))
                + "</xs:complexType></xs:element></xs:schema>",
                "<root " + " ".join(f'item{index}="value"' for index in reversed(range(size))) + "/>",
            ),
        )
        for size in (512, 4)
    )


def _validate_pattern_reuse_cases() -> tuple[tuple[str, tuple[str, str]], ...]:
    return tuple(
        (
            label,
            (
                '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'
                '<xs:simpleType name="item"><xs:restriction base="xs:string">'
                + facets
                + '</xs:restriction></xs:simpleType><xs:element name="root"><xs:complexType><xs:sequence>'
                '<xs:element name="value" type="item" maxOccurs="unbounded"/>'
                "</xs:sequence></xs:complexType></xs:element></xs:schema>",
                "<root>" + "<value>abc123</value>" * 1000 + "</root>",
            ),
        )
        for label, facets in (
            ("1000 values, two patterns", '<xs:pattern value="[a-z]+[0-9]+"/><xs:pattern value="(abc|def)[0-9]+"/>'),
            ("1000 values, no patterns", ""),
        )
    )


def _validate_facet_cases() -> tuple[tuple[str, tuple[str, str]], ...]:
    return tuple(
        (
            label,
            (
                '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'
                + declarations
                + '<xs:element name="root"><xs:complexType><xs:sequence>'
                f'<xs:element name="value" type="{type_name}" maxOccurs="unbounded"/>'
                "</xs:sequence></xs:complexType></xs:element></xs:schema>",
                "<root>" + "<value>abc123</value>" * 1000 + "</root>",
            ),
        )
        for label, type_name, declarations in (
            (
                "1000 values, four-level derived type",
                "item",
                (
                    '<xs:simpleType name="base"><xs:restriction base="xs:token">'
                    '<xs:minLength value="3"/><xs:maxLength value="12"/></xs:restriction></xs:simpleType>'
                    '<xs:simpleType name="middle"><xs:restriction base="base">'
                    '<xs:pattern value="[a-z]+[0-9]+"/></xs:restriction></xs:simpleType>'
                    '<xs:simpleType name="choice"><xs:restriction base="middle">'
                    '<xs:enumeration value="abc123"/><xs:enumeration value="def456"/></xs:restriction></xs:simpleType>'
                    '<xs:simpleType name="item"><xs:restriction base="choice">'
                    '<xs:maxLength value="9"/></xs:restriction></xs:simpleType>'
                ),
            ),
            ("1000 values, builtin string", "xs:string", ""),
        )
    )


def _encoding_result_cases() -> tuple[tuple[str, bytes], ...]:
    return (
        ("short ambiguous legacy bytes", "déjà vu, bientôt à Paris".encode("cp1252")),
        ("short ASCII", b"A short plain message"),
        ("UTF-8 byte-order mark", b"\xef\xbb\xbfhello"),
    )


INPUTS: dict[str, Callable[[], tuple[tuple[str, object], ...]]] = {
    "startup": lambda: (("fresh import and version", "import"), ("fresh CLI minification", "minify")),
    "radio-group": _radio_group_cases,
    "form-data-fieldsets": _form_data_fieldset_cases,
    "build": lambda: _ROWS,
    "build-e": lambda: _ROWS,
    "construct": lambda: _ROWS,
    "emit": lambda: _ROWS,
    "shadow": lambda: _ROWS,
    "shadow-slot": lambda: (
        *(
            (f"{size:,} preceding slots, {size if markup else 0:,} {kind}", (size, markup * size))
            for kind, markup in (
                ("children", '<span slot="target">value</span>'),
                ("children", ""),
                ("comments", "<!--value-->"),
            )
            for size in (100, 1_000, 10_000)
        ),
        *(
            (f"{size:,} preceding slots, 1 nonmatching child", (size, '<span slot="unused">value</span>'))
            for size in (100, 1_000, 10_000)
        ),
    ),
    "parse": _parse_cases,
    "parse-foster": lambda: tuple(
        (
            f"{count:,} table rows / fostered text runs",
            "<div><table>" + "a<tr><td>cell</td></tr>" * count + "</table></div>",
        )
        for count in (1024, 1)
    ),
    "parse-crlf": lambda: tuple(
        (
            f"1,000 paragraphs / {label}",
            f"<p>{'a' * 80}{newline}</p>" * 1000,
        )
        for label, newline in (("CRLF", "\r\n"), ("LF", "\n"))
    ),
    "parse-nul": lambda: tuple(
        (
            f"1,000 paragraphs / {label}",
            f"<p>{first}</p>" + f"<p>{'a' * 80}</p>" * 999,
        )
        for label, first in (("early NUL", "first\0a"), ("no NUL", "a" * 80))
    ),
    "parse-afe": lambda: tuple(
        (
            f"256 b elements / {variant} attributes",
            "".join(f'<b title="{index if variant == "distinct" else "same"}">' for index in range(256))
            + "a"
            + "</b>" * 256,
        )
        for variant in ("distinct", "identical")
    ),
    "parse-scope": lambda: (
        (
            "256 spans / 1,000 ignored address end tags",
            "<div>" + "<span>" * 256 + "a" + "</address>" * 1000 + "a" + "</span>" * 256 + "</div>",
        ),
    ),
    "parse-formatting": lambda: tuple(
        (
            f"{depth} spans / 1,000 samp elements",
            "<b>" + "<span>" * depth + "<samp>a</samp>" * 1000 + "</span>" * depth + "</b>",
        )
        for depth in (256, 0)
    ),
    "parse-dense": lambda: (("2.6 MB / 200k nodes", "<div><span>x</span></div>" * 100_000),),
    "parse-xml": lambda: (("catalog XML", _XML_DOC),),
    "parse-xml-attrs": lambda: tuple(
        (f"{count} attributes", "<root " + " ".join(f'a{index}="value"' for index in range(count)) + "/>")
        for count in (1, 10, 100, 1_000)
    ),
    "parse-xml-append": lambda: tuple(
        (f"{label} / value=a", "<root " + " ".join(f'a{index}="a"' for index in range(count)) + "/>")
        for count, label in ((1_000, "1,000 attributes"), (1, "1 attribute"))
    ),
    "parse-xml-values": lambda: (
        ("64 KiB clean value", '<root value="' + "a" * 65536 + '"/>'),
        ("64 KiB reference-rich source", '<root value="' + "a&amp;b " * 8192 + '"/>'),
        ("five value characters", '<root value="hello"/>'),
    ),
    "parse-xml-text": lambda: (
        ("64 KiB clean text", "<root>" + "a" * 65536 + "</root>"),
        ("64 KiB reference-rich source", "<root>" + "a&amp;b " * 8192 + "</root>"),
        ("five text characters", "<root>hello</root>"),
    ),
    "parse-xml-prefixes": lambda: tuple(
        (
            f"{count} prefixes and attributes",
            "<root "
            + " ".join(f'xmlns:p{index}="urn:{index}"' for index in range(count))
            + " "
            + " ".join(f'p{index}:value="x"' for index in range(count))
            + "/>",
        )
        for count in (128, 1)
    ),
    "parse-xml-names": lambda: (
        (
            "1k growing attributes",
            "<r>" + "".join("<e " + "a" * length + '="x"/>' for length in range(1, 1_001)) + "</r>",
        ),
    ),
    "validate": lambda: (
        ("catalog XSD + doc", (_VALIDATE_XSD, _VALIDATE_DOC)),
        ("1,024 global declarations", (_VALIDATE_GLOBAL_XSD, _VALIDATE_GLOBAL_DOC)),
        *_validation_verdict_cases(_VALIDATE_XSD)[:1],
    ),
    "validate-rng": lambda: (
        ("catalog RNG + doc", (_VALIDATE_RNG, _VALIDATE_DOC)),
        *_validation_verdict_cases(_VALIDATE_RNG)[:1],
    ),
    "validate-rng-reuse": _validate_rng_reuse_cases,
    "compile-rng-reuse": lambda: tuple((label, case[0]) for label, case in _validate_rng_reuse_cases()[::2]),
    "validate-facets": lambda: (
        *_validate_facet_cases(),
        (
            "1000 attributes, four-level derived type",
            (
                _validate_facet_cases()[0][1][0].replace(
                    '<xs:element name="value" type="item" maxOccurs="unbounded"/>',
                    '<xs:element name="value" maxOccurs="unbounded"><xs:complexType>'
                    '<xs:attribute name="data" type="item"/></xs:complexType></xs:element>',
                ),
                "<root>" + '<value data="abc123"/>' * 1000 + "</root>",
            ),
        ),
        (
            "one value, four-level derived type",
            (_validate_facet_cases()[0][1][0], "<root><value>abc123</value></root>"),
        ),
    ),
    "validate-attributes": lambda: (
        *_validate_attribute_cases(),
        ("512 declarations, one attribute", (_validate_attribute_cases()[0][1][0], '<root item0="value"/>')),
        (
            "one declaration, 512 attributes",
            (
                (
                    '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"><xs:element name="root">'
                    '<xs:complexType><xs:attribute name="item0" type="xs:string"/></xs:complexType>'
                    "</xs:element></xs:schema>"
                ),
                _validate_attribute_cases()[0][1][1],
            ),
        ),
    ),
    "validate-numeric-facets": _validate_numeric_facet_cases,
    "compile-facets": lambda: (("four-level derived type", _validate_facet_cases()[0][1][0]),),
    "validate-pattern-reuse": _validate_pattern_reuse_cases,
    "compile-pattern": lambda: (("two pattern facets", _validate_pattern_reuse_cases()[0][1][0]),),
    "validate-pattern": lambda: tuple(
        (
            f"{size:,} character alternatives",
            (
                '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"><xs:element name="value">'
                '<xs:simpleType><xs:restriction base="xs:string"><xs:pattern value="['
                + "".join(chr(0x400 + index) for index in range(size))
                + ']"/></xs:restriction></xs:simpleType></xs:element></xs:schema>',
                f"<value>{chr(0x400 + size - 1)}</value>",
            ),
        )
        for size in (32, 256, 2_048)
    ),
    "compile-rng": lambda: (("catalog grammar", _VALIDATE_RNG), ("1,024 definitions", _COMPILE_RNG)),
    "parse-scripting": _readpath_cases,  # the real pages carry <noscript>, so the scripting rawtext path runs
    "parse-locations": _readpath_cases,  # real attribute-dense pages exercise the per-attribute span stamping
    "parse-shadow": lambda: (("component gallery", _SHADOW_DOC),),
    "fragment": lambda: (("table-row fragment (2 kB)", _FRAGMENT_HTML),),
    "escape": corpus.escape_cases,
    "unescape": corpus.unescape_cases,
    "tokenize": _tokenize_cases,
    "html-options": lambda: (("reused defaults", ("html", True)), ("no options", ("html", False))),
    "text-options": lambda: (("reused defaults", ("text", True)), ("no options", ("text", False))),
    "canonical-options": lambda: (("reused defaults", ("canonical", True)), ("no options", ("canonical", False))),
    "token-attributes": _token_attribute_cases,
    "tokenize-attributes": lambda: _token_attribute_cases()[:1],
    "edit": _readpath_cases,
    "class-edit": _readpath_cases,
    "strip-remove": _readpath_cases,
    "strip-tags": _readpath_cases,
    "set-html": _readpath_cases,
    "set-text": _readpath_cases,
    "shadow-assignment": lambda: (("1000 unique", (1_000, "unique")), ("1 unique", (1, "unique"))),
    "shadow-fallback": lambda: (("1000 wide", (1_000, "wide")), ("1 wide", (1, "wide"))),
    "observe": _readpath_cases,
    "query-root-groups": lambda: (
        ("2,048 detached roots", ("detached", 2_048)),
        ("2,048 connected roots", ("connected", 2_048)),
        ("4 connected roots", ("connected", 4)),
        ("512 documents", ("documents", 512)),
    ),
    "query-roots": lambda: tuple(
        (f"{count} {order} roots", (count, order))
        for count, order in ((512, "reversed"), (512, "shuffled"), (512, "sorted"), (4, "reversed"))
    ),
    "query-closest": lambda: (
        ("1024 children, shared ancestor", (1024, True)),
        ("1024 children, distinct ancestors", (1024, False)),
    ),
    "node-closest": lambda: (("one child, cached selector", (1, True)),),
    "query-parents": lambda: (
        ("1024 children, shared parent", (1024, True)),
        ("1024 children, distinct parents", (1024, False)),
    ),
    "query-siblings": lambda: (
        ("1024 siblings, all selected", (1024, True)),
        ("1024 siblings, one selected", (1024, False)),
    ),
    "prune-shared": lambda: (("depth128,1024matches", (128, 1024)), ("depth128,1match", (128, 1))),
    "observe-registrations": lambda: (
        ("1000 wrong-kind", (1_000, "wrong-kind")),
        ("1000 unrelated", (1_000, "unrelated")),
    ),
    "navigate": _readpath_cases,
    "treewalk": _readpath_cases,
    "chain": _readpath_cases,
    "range-clone": _readpath_cases,
    "range-boundary": lambda: (("1000 start", (1_000, "start")), ("1000 end", (1_000, "end"))),
    "range-contained": lambda: (("1000 clone", (1_000, "clone")), ("1 clone", (1, "clone"))),
    "range-partial": lambda: (("1000 clone", (1_000, "clone")), ("1 clone", (1, "clone"))),
    "links-extract": _readpath_cases,
    "links-absolutize": _readpath_cases,
    "links-rewrite": _readpath_cases,
    "find": _readpath_cases,
    "find-cold": lambda: _FIND_COLD_CASES,
    "select": _readpath_cases,
    "select-has": _readpath_cases,
    "select-relative": lambda: (
        *tuple(
            (
                f"{size} nested elements, {relative}",
                (f"div:has({relative})", "<div>" * size + "<a></a>" + "</div>" * size),
            )
            for size in (8, 100)
            for relative in ("> a", "+ a", "~ a", "a")
        ),
        *tuple(
            (
                f"64 siblings with {depth} descendants, {relative}",
                (
                    f"section:has({relative})",
                    ("<section>" + "<div>" * depth + "<a></a>" + "</div>" * depth + "</section>") * 64,
                ),
            )
            for depth in (0, 64)
            for relative in ("+ a", "~ a", "+ section a")
        ),
    ),
    "select-default": lambda: (
        *(
            (
                f"{forms} forms, {prefix} spans, {buttons} buttons, {selector}",
                (
                    selector,
                    ("<form>" + "<span>x</span>" * prefix + "<button>go</button>" * buttons + "</form>") * forms,
                ),
            )
            for forms, prefix, buttons, selector in (
                (1, 4096, 128, ":default"),
                (1, 512, 64, ":default"),
                (1, 8, 4, ":default"),
                (128, 0, 1, ":default"),
                (1, 4096, 128, "button"),
            )
        ),
        (
            "1 form, 4096 spans, 128 explicit submit buttons",
            (
                ":default",
                "<form>" + "<span>x</span>" * 4096 + "<button type=submit>go</button>" * 128 + "</form>",
            ),
        ),
    ),
    "select-nth": lambda: tuple(
        (f"{selector} ({size:,} siblings)", (selector, f"<ul>{'<li class=x>value</li>' * size}</ul>"))
        for selector in ("li:nth-child(odd)", "li:nth-child(odd of .x)")
        for size in (100, 1_000, 10_000)
    ),
    "xpath-wide": lambda: tuple(
        (f"{expression} ({size:,} siblings)", (expression, f"<ul>{'<li>value</li>' * size}</ul>"))
        for expression in ("//li", "//li | //ul")
        for size in (100, 1_000, 10_000)
    ),
    "xpath-distinct": lambda: (
        *(
            (
                f"{size:,} nodes, {unique} values",
                (
                    "set:distinct(//li)",
                    "<ul>" + "".join(f"<li>value-{index % unique}</li>" for index in range(size)) + "</ul>",
                ),
            )
            for size in (100, 1_000, 10_000)
            for unique in (1, 10, size)
        ),
        ("0 nodes, 0 values", ("set:distinct(//li)", "<ul></ul>")),
        ("1 node, 1 value", ("set:distinct(//li)", "<ul><li>value</li></ul>")),
    ),
    "xpath-compare": lambda: (
        tuple(
            (
                f"{operation}, {size:,} nodes per set",
                (
                    f"//li {operation} //b",
                    "<main><ul>"
                    + "<li>a</li>" * size
                    + "</ul><section>"
                    + f"<b>{'b' if operation == '=' else 'a'}</b>" * size
                    + "</section></main>",
                ),
            )
            for operation in ("=", "!=")
            for size in (10, 100, 1_000, 2_000)
        )
        + tuple(
            (
                f"scalar equality, {size:,} characters",
                ("string(//li) = string(//b)", "<ul><li>" + "a" * size + "</li></ul><b>" + "a" * size + "</b>"),
            )
            for size in (32, 32_768)
        )
        + (("first-node equality, 1,000 nodes", ("//li = //li", "<ul>" + "<li>a</li>" * 1_000 + "</ul>")),)
    ),
    "xpath-order": lambda: ((
        *tuple(
            (
                f"{operation}, {size:,} nodes per set",
                (
                    f"//li {operation} //b",
                    "<main><ul>"
                    + f"<li>{2 if operation.startswith('<') else 1}</li>" * size
                    + "</ul><section>"
                    + f"<b>{1 if operation.startswith('<') else 2}</b>" * size
                    + "</section></main>",
                ),
            )
            for operation in ("<", "<=", ">", ">=")
            for size in (10, 100, 1000)
        ),
        (
            "first-pair match, 1,000 nodes",
            ("//li < //b", "<ul>" + "<li>1</li>" * 1000 + "</ul><section>" + "<b>2</b>" * 1000 + "</section>"),
        ),
    )),
    "node-equals": lambda: tuple(
        (f"{count:,} attribute{'s' if count != 1 else ''}, {variant}", (count, variant))
        for count, variant in (
            (1, "aligned"),
            (10, "aligned"),
            (31, "aligned"),
            (32, "aligned"),
            (100, "aligned"),
            (1000, "aligned"),
            (100, "reversed"),
            (1000, "reversed"),
            (1000, "early-value"),
            (100, "late-value"),
            (100, "disjoint"),
            (100, "rotated"),
            (1003, "duplicates"),
        )
    ),
    "xpath-id-nodes": lambda: tuple(
        (
            label,
            (
                "id(//i)/@id",
                '<main><b id="first"></b><b id="second"></b>'
                + ("<i>" + " second first second " * repetitions + "</i>") * count
                + "</main>",
            ),
        )
        for count, repetitions, label in (
            (10_000, 1, "10,000 ID argument nodes"),
            (10, 1_000, "10 long ID argument nodes"),
        )
    ),
    "xpath-replace": lambda: (
        (
            "KMP sparse matches",
            (("a" * 4096 + "b" + "a" * 64 + "x") * 8, "a" * 64 + "b" + "a" * 64, "X", ("a" * 4032 + "Xx") * 8),
        ),
        ("short ASCII", ("A short paragraph", "a", "A", "A short pArAgrAph")),
    ),
    "xpath-concat": lambda: tuple(
        (
            f"{count:,} nodes, {length} characters",
            ("str:concat(//i)", "<main>" + ("<i>" + "a" * length + "</i>") * count + "</main>"),
        )
        for count, length in ((10_000, 32), (10, 32_768))
    ),
    "xpath-translate": lambda: ((
        *tuple(
            (
                f"{size:,} map characters, 32 KiB text",
                (
                    "translate(string(//p), '" + "".join(chr(256 + index) for index in range(size)) + "', '')",
                    "<p>" + "z" * 32768 + "</p>",
                ),
            )
            for size in (32, 128, 512)
        ),
        (
            "ASCII case folding, short text",
            (
                "translate(string(//p), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')",
                "<p>A Short TITLE</p>",
            ),
        ),
        *tuple(
            (
                label,
                (f"translate(string(//p), '{source}', '{target}')", f"<p>{content}</p>"),
            )
            for label, source, target, content in (
                ("early match, 32 KiB text", string.ascii_lowercase, string.ascii_uppercase, "a" * 32768),
                ("duplicate map, 32 KiB text", "a" * 512, "b", "a" * 32768),
                ("long map, 64 character text", "a" * 8192, "b", "a" * 64),
                ("second entry, 32 KiB text", string.ascii_lowercase, string.ascii_uppercase, "b" * 32768),
                ("ASCII cycling", string.ascii_lowercase, string.ascii_uppercase, string.ascii_lowercase * 1260),
                (
                    "Unicode cycling",
                    "".join(chr(256 + index) for index in range(128)),
                    "",
                    "".join(chr(256 + index) for index in range(256)) * 128,
                ),
            )
        ),
    )),
    "xpath-set": lambda: (
        tuple(
            (
                f"{operation}, {size:,} nodes per set",
                (
                    f"set:{operation}(//li, //b)",
                    "<main><ul>" + "<li></li>" * size + "</ul><section>" + "<b></b>" * size + "</section></main>",
                ),
            )
            for operation in ("intersection", "difference", "has-same-node")
            for size in (10, 100, 1_000, 10_000)
        )
        + tuple(
            (
                f"first-node overlap, {size:,} nodes",
                ("set:has-same-node(//li, //li)", "<ul>" + "<li></li>" * size + "</ul>"),
            )
            for size in (10, 100, 1_000, 10_000)
        )
    ),
    "computed-style-deep": lambda: tuple(
        (f"{depth} ancestors", f"<style>div {{ color:red }}</style>{'<div>' * depth}x{'</div>' * depth}")
        for depth in (10, 100, 500)
    ),
    "path-wide": _wide_path_cases,
    "path-xpath-wide": _wide_path_cases,
    "path-cold": _wide_path_cases,
    "path-xpath-cold": _wide_path_cases,
    "path-one-cold": _wide_path_cases,
    "path-xpath-one-cold": _wide_path_cases,
    "path-class-edit": _wide_path_cases,
    "microdata-wide": lambda: tuple(
        (f"{size:,} properties", "<div itemscope>" + "<span itemprop=name>x</span>" * size + "</div>")
        for size in (100, 1_000, 10_000)
    ),
    "microdata-empty-scope": lambda: tuple(
        (f"{size:,} elements", f"<div itemscope>{'<span>x</span>' * size}</div>") for size in (100, 1_000, 10_000)
    ),
    "structured-empty": lambda: tuple(
        (f"{size:,} elements", f"<div>{'<span>x</span>' * size}</div>") for size in (100, 1_000, 10_000)
    ),
    "article-wide": lambda: tuple(
        (
            f"{size:,} candidates",
            "<div>"
            + ("<section><p>" + "A sentence with enough text to score. " * 3 + "</p></section>") * size
            + "</div>",
        )
        for size in (100, 1_000, 10_000)
    ),
    "article-deep": lambda: tuple(
        (
            f"{depth} nested candidates",
            ("<div><p>" + "A sentence with enough prose, and a clause, to score. " * 3 + "</p>") * depth
            + "</div>" * depth,
        )
        for depth in (10, 100, 500)
    ),
    "computed-style-filter": lambda: _FILTER_STYLED_PAGES,
    "computed-style-filter-cold": lambda: _FILTER_STYLED_PAGES,
    "computed-style-selectors": _computed_style_selector_cases,
    "computed-style-selectors-reverse": _computed_style_selector_cases,
    "computed-style-specificity": lambda: _SPECIFICITY_STYLED_PAGES,
    "computed-style-specificity-cold": lambda: _SPECIFICITY_STYLED_PAGES,
    "computed-style": lambda: (("styled page (3 kB)", _styled_page(8)), ("styled page (11 kB)", _styled_page(40))),
    "computed-style-dense": lambda: (("dense sheet (9 kB)", _dense_styled_page(20)),),
    "match": _readpath_cases,
    "find-text": _readpath_cases,
    "find-text-exact": lambda: (
        ("flat matches", ("<div>needle</div>" * 1_000, "needle")),
        ("nested matches", ("<div>" * 100 + "needle" + "</div>" * 100, "needle")),
        ("nested short expectation", ("<div>" * 100 + "x" * 10_000 + "</div>" * 100, "needle")),
        ("wide early mismatch", ("<div>x" + "<b>needle</b>" * 1_000 + "</div>", "y" + "needle" * 1_000)),
        ("wide match", ("<div>" + "<b>needle</b>" * 1_000 + "</div>", "needle" * 1_000)),
        ("wide late mismatch", ("<div>" + "<b>needle</b>" * 1_000 + "</div>", "needle" * 999 + "needlx")),
    ),
    "find-attr-presence": lambda: tuple(
        (
            f"1,000 attributes, {size:,} characters, {present}",
            (f'<p data-x="{"x" * size}">present</p><p>absent</p>' * 1_000, present),
        )
        for size in (0, 16, 4_096)
        for present in (True, False)
    ),
    "find-text-overlap": lambda: (("100 KiB overlapping miss", f"<p>{'a' * 100_000}</p>"),),
    "text-content": _readpath_cases,
    "serialize": _readpath_cases,
    "parse-inner": _readpath_cases,
    "parse-inner-encode": _readpath_cases,
    "serialize-inner": _readpath_cases,
    "serialize-inner-indent": _readpath_cases,
    "serialize-inner-minify": _readpath_cases,
    "encode-inner": _readpath_cases,
    "encode-inner-indent": _readpath_cases,
    "encode-inner-minify": _readpath_cases,
    "iterate-inner": _readpath_cases,
    "iterate-inner-indent": _readpath_cases,
    "transform-dispatch": _dispatch_cases,
    "collapse-whitespace": _collapse_cases,
    "strip-comments": _readpath_cases,
    "whitespace-roundtrip": _readpath_cases,
    "transform-tree": _readpath_cases,
    "conformance": lambda: (
        *_readpath_cases(),
        *(
            (name, '<!doctype html><html lang="en"><title>Sections</title><body>' + content + "</body></html>")
            for name, content in (
                *(
                    (
                        f"{count} heading-free nested sections",
                        "<section>" * count + "<p>Text</p>" + "</section>" * count,
                    )
                    for count in (8, 64, 512)
                ),
                ("512 heading-free sibling sections", "<section><p>Text</p></section>" * 512),
                ("512 nested sections with heading", "<section>" * 512 + "<h2>Title</h2>" + "</section>" * 512),
            )
        ),
    ),
    "serialize-xml": _readpath_cases,
    "canonicalize": _readpath_cases,
    "canonicalize-attrs": lambda: tuple(
        (
            f"{size:,} attributes",
            "<div " + " ".join(f'data-{index:05d}="x"' for index in range(size, 0, -1)) + "></div>",
        )
        for size in (100, 1_000, 10_000)
    ),
    "canonicalize-deep": lambda: (
        ("deep tree (150 deep)", _deep_tree(150, 3)),
        (
            "sparse xlink (150 deep)",
            "<svg>" + ('<g><use xlink:href="#x"/>' * 150) + "</g>" * 150 + "</svg>",
        ),
        ("shallow ordinary", "<main><p>plain text</p><div id=a>more text</div></main>"),
    ),
    "lossless-serialize": _readpath_cases,
    "minify": _readpath_cases,
    "socialcard": lambda: (
        ("head", _SOCIAL_HEAD),
        ("article 8 KiB", f"{_SOCIAL_HEAD}<body>{'<p>filler text</p>' * 400}</body>"),
    ),
    "structured": lambda: (("product", _STRUCTURED_PAGE), ("catalog 8 KiB", _STRUCTURED_PAGE * 12)),
    "microdata": lambda: (("product", _STRUCTURED_PAGE), ("catalog 8 KiB", _STRUCTURED_PAGE * 12)),
    "microdata-itemref": lambda: (
        ("references after 4,000 nodes", _MICRODATA_ITEMREF),
        (
            "reversed references",
            _MICRODATA_ITEMREF.replace(_MICRODATA_REFS, " ".join(f"r{index}" for index in range(999, -1, -1)), 1),
        ),
        (
            "interleaved references",
            _MICRODATA_ITEMREF.replace(
                _MICRODATA_REFS, " ".join(f"r{index}" for offset in (0, 1) for index in range(offset, 1_000, 2)), 1
            ),
        ),
        (
            "4 shuffled references",
            '<div itemscope itemref="r0 r2 r1 r3"></div>'
            + "".join(f'<meta id=r{index} itemprop=p content="{index}">' for index in range(4)),
        ),
    ),
    "syndication": lambda: (
        ("rss 30 items", _FEED_XML),
        (
            "rss 30 items / 128 extensions each",
            _FEED_XML.replace("<item>", "<item>" + "<extension>x</extension>" * 128),
        ),
        (
            "atom 30 entries",
            '<feed xmlns="http://www.w3.org/2005/Atom"><title>Example</title>'
            + "".join(
                f'<entry><title>Entry {index}</title><link href="https://example.com/{index}"/>'
                f"<id>urn:{index}</id><updated>2026-07-06</updated><summary>Summary {index}</summary>"
                "<content>Full body</content><author><name>Writer</name></author></entry>"
                for index in range(30)
            )
            + "</feed>",
        ),
    ),
    "sanitize": lambda: (
        ("comment", "<p>Thanks for the <a href='http://example.com'>link</a>! <script>evil()</script></p>"),
        ("post 4 KiB", _SANITIZE_POST * 20),
    ),
    "sanitize-disallowed": lambda: tuple(
        (
            f"{mode} tag with {count} attribute{'s' if count != 1 else ''}",
            (mode, "<x" + "".join(f' a{index}="value"' for index in range(count)) + ">text</x>"),
        )
        for mode in ("escape", "strip")
        for count in (1, 64, 1024)
    ),
    "sanitize-templates": lambda: (
        ("templated 4 KiB", _SANITIZE_TEMPLATES * 20),
        ("plain 64 KiB text", "<p>" + "Plain text " * 6_000 + "</p>"),
        ("plain 64 KiB attribute", '<a title="' + "Plain text " * 6_000 + '">label</a>'),
        ("late template in 64 KiB text", "<p>" + "Plain text " * 6_000 + "{{value}}</p>"),
    ),
    "sanitize-named-props": lambda: (("clobbering 4 KiB", _SANITIZE_NAMED * 11),),
    "sanitize-report": lambda: (("post 4 KiB", _SANITIZE_POST * 20),),
    "sanitize-node": lambda: (("post 4 KiB", _SANITIZE_POST * 20),),
    "sanitize-attributes": lambda: (
        ("1024 rejected attributes", "<p " + " ".join(f'a{index}="x"' for index in range(1_024)) + ">x</p>"),
        ("1024 allowed attributes", "<p " + " ".join(f'data-{index}="x"' for index in range(1_024)) + ">x</p>"),
        ("four allowed attributes", '<p data-a="x" data-b="x" data-c="x" data-d="x">x</p>'),
        ("256 elements with four allowed attributes", '<p data-a="x" data-b="x" data-c="x" data-d="x">x</p>' * 256),
    ),
    "sanitize-styles": lambda: (("styled 4 KiB", _SANITIZE_STYLES * 20),),
    "sanitize-transform": lambda: (("legacy 4 KiB", _SANITIZE_LEGACY * 13),),
    "sanitize-custom-elements": lambda: (("custom 4 KiB", _SANITIZE_CUSTOM * 11),),
    "sanitize-xml": lambda: (("post 4 KiB", _SANITIZE_POST * 20),),
    "markup": lambda: _MARKUP_ESCAPE_CASES,
    "markup-op": lambda: (
        ("striptags", ("striptags", _MARKUP_OPS_HTML)),
        ("unescape", ("unescape", _MARKUP_OPS_HTML)),
        ("format (escapes operands)", ("format", _MARKUP_FORMAT_ARGS)),
        ("join (escapes operands)", ("join", _MARKUP_JOIN_PARTS)),
    ),
    "linkify": lambda: _LINKIFY_CASES,
    "linkify-node": lambda: (
        ("markup (4 KiB)", _LINKIFY_CASES[2][1]),
        ("wide text, one link", "<p>" + "😀 " * 32_768 + "https://example.com</p>"),
        ("ASCII text, one link", "<p>" + "a " * 32_768 + "https://example.com</p>"),
        ("wide text, no links", "<p>" + "😀 " * 32_768 + "</p>"),
        ("1024 wide-text links", "<p>" + "😀 https://example.com " * 1_024 + "</p>"),
    ),
    "linkify-traversal": lambda: _LINKIFY_TRAVERSAL_CASES,
    "detect": lambda: (
        ("find comment (1 link, 1 email)", ("find", _LINKIFY_CASES[0][1])),
        ("find prose (1 KiB)", ("find", _LINKIFY_CASES[1][1])),
        ("has_link comment", ("has", _LINKIFY_CASES[0][1])),
        ("has_link prose (1 KiB)", ("has", _LINKIFY_CASES[1][1])),
        ("has_link early (220 KiB tail)", ("has", "https://example.com " + "tail " * 45_000)),
        ("find numeric prose, phones off (1 KiB)", ("find", _PHONE_MIXED * 5)),
        ("find ucs2 prose, phones off (100 KiB)", ("find", _PHONE_CASES[9][1][1])),
        ("find ucs4 prose, phones off (100 KiB)", ("find", _PHONE_CASES[10][1][1])),
    ),
    "phone": lambda: _PHONE_CASES,
    "phone-parse": lambda: (
        ("20 held numbers, valid", ("valid", _PHONE_HELD)),
        ("20 held numbers, possible", ("possible", _PHONE_HELD)),
    ),
    "phone-format": lambda: (
        ("20 numbers, international", ("international", _PHONE_HELD)),
        ("20 numbers, national", ("national", _PHONE_HELD)),
        ("20 numbers, RFC 3966", ("rfc3966", _PHONE_HELD)),
        ("20 numbers, E.164", ("e164", _PHONE_HELD)),
    ),
    "markdown": lambda: (
        ("article (2 KiB)", ("default", _MARKDOWN_ARTICLE)),
        ("list (4 KiB)", ("default", _MARKDOWN_LIST)),
        ("table (4 KiB)", ("default", _MARKDOWN_TABLE)),
        ("configured (4 KiB)", ("configured", _MARKDOWN_CONFIGURED)),
        ("8192 asterisks", ("escaped", "<p>" + "*" * 8192 + "</p>")),
        ("8192 letters", ("escaped", "<p>" + "a" * 8192 + "</p>")),
    ),
    "markdown-wrap": lambda: (
        ("10000 words, width8192", (8192, "<p>" + "aa " * 10_000 + "</p>")),
        ("10000 words, width80", (80, "<p>" + "aa " * 10_000 + "</p>")),
    ),
    "markdown-google": lambda: (("google_doc (4 KiB)", _MARKDOWN_GOOGLE),),
    "tables": lambda: (
        ("rows (10 rows)", ("rows", _table_html(10))),
        ("records (10 rows)", ("records", _table_html(10))),
        ("rows (100 rows)", ("rows", _table_html(100))),
        ("records (100 rows)", ("records", _table_html(100))),
        ("rows (1000 rows)", ("rows", _table_html(1_000))),
        ("records (1000 rows)", ("records", _table_html(1_000))),
    ),
    "tables-spans": lambda: tuple(
        (
            f"{kind} / {span} columns / {size} text characters",
            (
                kind,
                "<table><tr><th colspan="
                + str(span)
                + ">header</th></tr><tr><td colspan="
                + str(span)
                + ">"
                + "x" * size
                + "</td></tr></table>",
            ),
        )
        for kind, span, size in (("rows", 128, 4096), ("rows", 1, 16), ("records", 128, 4096))
    ),
    "tables-wide": lambda: (("10k columns", ("rows", "<table><tr>" + "<td>x</td>" * 10_000 + "</tr></table>")),),
    "article": lambda: (("post (4 KiB)", _article_page(16)), ("longform (16 KiB)", _article_page(72))),
    "boilerplate": lambda: (("post (4 KiB)", _article_page(16)), ("longform (16 KiB)", _article_page(72))),
    "date-tally": lambda: (
        (
            "1000 distinct visible dates",
            "<p>" + " ".join((date(2000, 1, 1) + timedelta(days=index)).isoformat() for index in range(1000)) + "</p>",
        ),
        ("1000 repeated visible dates", "<p>" + "2000-01-01 " * 1000 + "</p>"),
    ),
    "date": lambda: (
        ("post (4 KiB)", _article_page(16)),
        ("longform (16 KiB)", _article_page(72)),
        ("100 meta candidates", "<head>" + "<meta name=date content=2024-05-06>" * 100 + "</head>"),
    ),
    "text-render": lambda: (("article (2 KiB)", _TEXT_ARTICLE), ("table (4 KiB)", _TEXT_TABLE)),
    "text-collapsed": lambda: (("collapsed (2 KiB)", _TEXT_ARTICLE),),
    "text-main": lambda: (("main (4 KiB)", _TEXT_MAIN),),
    "text-annotated": lambda: (("annotated (4 KiB)", _TEXT_ANNOTATED),),
    "extract-attr": _readpath_cases,
    "extract-text": _readpath_cases,
    "extract-url": lambda: (
        ("base_url / get_base_url", ("base", _URL_HINT_HTML)),
        ("meta_refresh / get_meta_refresh", ("refresh", _URL_HINT_HTML)),
    ),
    "htmlparser": _readpath_cases,
    "sax": _readpath_cases,
    "sax-records": _sax_record_cases,
    "sax-records-callback": _sax_record_cases,
    "treebuild": _readpath_cases,
    "rewrite": _readpath_cases,
    "rewrite-attributes": lambda: (("1,000 new names", (1000, "<x></x>")), ("one new name", (1, "<x></x>"))),
    "path": _readpath_cases,
    "path-xpath": _readpath_cases,
    "translate": lambda: (
        *_TRANSLATE_CASES,
        *tuple(
            (f"{count} {kind} literals", ",".join([selector] * count))
            for count in (1, 16, 128)
            for kind, selector in (
                ("ID", "#identifier"),
                ("equality", '[data-x="value"]'),
                ("long equality", '[data-x="' + "value" * 32 + '"]'),
                ("mixed quotes", r'[data-x="it\27 s\22 x"]'),
                ("case folded", '[data-x="VALUE" i]'),
                ("class token", ".identifier"),
                ("dash match", '[data-x|="en"]'),
            )
        ),
    ),
    "specificity": lambda: _TRANSLATE_CASES,
    "xpath": _xpath_cases,
    "xpath-id": lambda: (("1,000 ids among 5,000 elements", _XPATH_ID_DOC),),
    "transform": _transform_cases,
    "transform-compile": _transform_compile_cases,
    "transform-reuse": _transform_compile_cases,
    "transform-sort": _transform_sort_cases,
    "transform-key": _transform_key_cases,
    "transform-dense": _transform_dense_cases,
    "transform-names": _transform_name_cases,
    "transform-names-compile": lambda: (_transform_name_cases()[0],),
    "transform-rules": lambda: tuple(
        (
            f"128 unmatched templates, 1,024 nodes, {passes} passes",
            (
                '<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="1.0">'
                '<xsl:output method="text"/>'
                + "".join(f'<xsl:template match="unused{index}" priority="0"/>' for index in range(128))
                + '<xsl:template match="/">'
                + '<xsl:apply-templates select="root/p"/>' * passes
                + '</xsl:template><xsl:template match="p" priority="-1">'
                '<xsl:value-of select="@id"/><xsl:text>|</xsl:text></xsl:template></xsl:stylesheet>',
                "<root>" + "".join(f'<p id="{index}"/>' for index in range(1, 1_025)) + "</root>",
            ),
        )
        for passes in (8, 1)
    ),
    "transform-number": _transform_number_cases,
    "minify-css": _minify_cases,
    "minify-css-conflicts": _css_conflict_inputs,
    "minify-css-merges": _css_merge_inputs,
    "minify-js": _minify_js_cases,
    "minify-js-names": lambda: (
        *tuple(
            (
                f"{count} live parameters",
                "function transform("
                + ",".join(f"argument{index}" for index in range(count))
                + "){return ["
                + ",".join(f"argument{index}" for index in range(count))
                + "]}",
            )
            for count in (8, 32, 54, 80)
        ),
        ("free-name conflicts", "function transform(value){return value+a+b+c}"),
        ("no local bindings", "console.log(document.title)"),
    ),
    "minify-js-integers": lambda: tuple(
        (
            f"{count} integers / {label}",
            "x=[" + ",".join(str((index + 1) * scale + offset) for index in range(count)) + "]",
        )
        for count, scale, offset, label in (
            (4096, 10, 1, "no trailing zeros"),
            (4096, 1000, 0, "three trailing zeros"),
            (1, 10, 1, "no trailing zeros"),
        )
    ),
    "minify-js-var-initialization": lambda: tuple(
        (
            f"{count} var pairs / {'read before initialization' if early else 'read after initialization'}",
            "function f(g){var "
            + ",".join(
                f"keep{index}=g({'value' + str(index) if early else str(index)}),value{index}={index % 10}"
                for index in range(count)
            )
            + ";return["
            + ",".join(f"keep{index}" if early else f"keep{index},value{index}" for index in range(count))
            + "]}",
        )
        for count, early in ((256, False), (1, False), (256, True))
    ),
    "minify-js-single-use": lambda: tuple(
        (
            f"{count} call initializers / {layout}",
            "function f(g){"
            + (
                "const " + ",".join(f"v{index}=g({index})" for index in range(count)) + ";"
                if layout == "one declaration"
                else "".join(f"const v{index}=g({index});" for index in range(count))
            )
            + "return["
            + ",".join(f"v{index}" for index in range(count))
            + "]}",
        )
        for count, layout in ((256, "one declaration"), (256, "separate declarations"), (1, "one declaration"))
    ),
    "minify-js-unused-declarations": lambda: tuple(
        (
            f"{count} unused retained/literal pairs",
            "function f(g){const "
            + ",".join(f"keep{index}=g({index}),value{index}={index % 10}" for index in range(count))
            + ";return[]}",
        )
        for count in (256, 1)
    ),
    "minify-js-unlink": lambda: tuple(
        (
            f"{count} retained/literal pairs / {layout}",
            "function f(g){"
            "const "
            + ("," if layout == "one declaration" else ";const ").join(
                f"keep{index}=g({index}),value{index}={index % 10}" for index in range(count)
            )
            + ";return["
            + ",".join(f"keep{index},value{index},value{index}" for index in range(count))
            + "]}",
        )
        for count, layout in ((256, "one declaration"), (256, "separate declarations"), (1, "one declaration"))
    ),
    "minify-js-propagation": lambda: tuple(
        (
            f"{count} literal bindings / {layout}",
            "function f(g){"
            + (
                "".join(f"const v{index}={index % 10};g(v{index});" for index in range(count))
                if layout == "interleaved declarations"
                else "const "
                + ",".join(f"v{index}={index % 10}" for index in range(count))
                + ";"
                + "".join(f"g(v{index});" for index in range(count))
            )
            + "return["
            + ",".join(f"v{index},v{index}" for index in range(count))
            + "]}",
        )
        for count, layout in (
            (256, "interleaved declarations"),
            (1, "interleaved declarations"),
            (256, "one declaration"),
        )
    ),
    "minify-js-guards": lambda: tuple(
        (
            f"{count} guard returns",
            "function f(x){"
            + "".join(f"if(x==={index})return g({index});" for index in range(count))
            + "return g(-1)}",
        )
        for count in (512, 1)
    ),
    "minify-js-sequences": lambda: tuple(
        (f"{count} expression statements", ";".join(f"f({index})" for index in range(count))) for count in (1000, 2)
    ),
    "stream": _readpath_cases,
    "encoding-result": _encoding_result_cases,
    "encoding-result-stream": _encoding_result_cases,
    "encoding": _encoding_cases,
    "decode": _decode_cases,
    "normalize": _normalize_cases,
    "normalize-dom": lambda: (
        *(
            (f"{count} adjacent text nodes / {len(text)} characters", (count, text))
            for count, text in (
                (1, "text"),
                (2, "text"),
                (10, "text"),
                (100, "text"),
                (1_000, "text"),
                (100, "text" * 64),
            )
        ),
        ("1000 empty tail nodes", (1_000, "")),
    ),
    "attribute-grow": lambda: tuple(
        (f"{count} {'existing' if existing else 'new'} attributes", (count, existing))
        for existing in (False, True)
        for count in (1, 10, 100, 1_000)
    ),
    "normalize-marks": lambda: tuple(
        (f"{size:,} combining marks", "a" + "\u0315" * (size // 2) + "\u0300" * (size // 2))
        for size in (100, 1_000, 10_000)
    ),
    "detect-language": _language_cases,
    "detect-language-long": lambda: tuple(
        (f"english book ({size} KiB)", corpus.corpus("war-and-peace/2600.txt", size << 10)) for size in (1, 64, 1_024)
    ),
    "escape-identifier": lambda: (
        (
            "mixed shapes (1,000)",
            tuple(
                shape.format(index)
                for index in range(200)
                for shape in ("item-{}", "{}leading-digit", "no escape {} needed?", "emoji-\U0001f642-{}", "-")
            ),
        ),
    ),
    "idna": lambda: (
        ("4,100 uncached Unicode hosts", _IDNA_URLS),
        ("4,100 varied-script hosts", _IDNA_VARIED_URLS),
    ),
    "urls-clean": lambda: (
        ("clean 100 URLs", ("clean", _URL_BATCH)),
        ("normalize 100 URLs", ("normalize", _URL_BATCH)),
        *tuple(
            (
                f"normalize 100 URLs, 100 {kind} query keys",
                ("normalize", ("https://example.org/?" + "&".join(f"{key}{index}=1" for index in range(100)),) * 100),
            )
            for kind, key in (("plain", "key"), ("escaped", "%6Bey"))
        ),
        *tuple(
            (f"normalize 100 URLs, {name}", ("normalize", ("https://example.org/" + path,) * 100))
            for name, path in (
                ("8 KiB undotted paths", "a" * 8192),
                ("short undotted paths", "products/item"),
                ("8 KiB paths with parent segments", "a" * 8192 + "/../item"),
                ("8 KiB paths with encoded parent segments", "a" * 8192 + "/%2e%2e/item"),
                ("8 KiB paths with file extensions", "a" * 8192 + ".html"),
            )
        ),
    ),
    "links-filter": _readpath_cases,
    "links-external": lambda: (
        ("900 mixed-site links", _EXTERNAL_LINKS_HTML),
        ("900 mixed-site links / 64 subdomains", _EXTERNAL_LINKS_HTML.replace("https://", "https://" + "s." * 64)),
    ),
    "serialize-attributes": lambda: tuple(
        (
            f"{count} attributes, {order}, sorting {sorting}",
            (
                "<div "
                + " ".join(
                    f'a{index:04d}="value"'
                    for index in (reversed(range(count)) if order == "reversed" else range(count))
                )
                + ">text</div>",
                sorting,
            ),
        )
        for count, order, sorting in (
            (1024, "reversed", True),
            (1024, "sorted", True),
            (8, "reversed", True),
            (1024, "reversed", False),
        )
    ),
    "is-valid": lambda: _validation_verdict_cases(_VALIDATE_XSD),
    "is-valid-rng": lambda: _validation_verdict_cases(_VALIDATE_RNG),
    "serialize-named": lambda: tuple(
        (label, "<html><head></head><body><p>" + text + "</p></body></html>")
        for label, text in (
            ("48k named characters", "é©\u03b1Ω∑€" * 8192),
            ("48k ASCII characters", "abcxyz" * 8192),
            ("48k unnamed characters", "😀" * 49152),
            ("short mixed text", "café &amp; \u03b1 😀"),
        )
    ),
    "transform-text": _transform_text_cases,
}


def _validation_verdict_cases(schema: str) -> tuple[tuple[str, tuple[str, str]], ...]:
    return (
        ("400 invalid quantities", (schema, _VALIDATE_DOC.replace("<qty>", "<qty>invalid"))),
        ("valid catalog", (schema, _VALIDATE_DOC)),
        ("one invalid quantity", (schema, _VALIDATE_DOC.replace("<qty>", "<qty>invalid", 1))),
    )
