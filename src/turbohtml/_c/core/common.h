/* Internal header shared by the turbohtml._html translation units.

   The module is split per feature for readability (escape.c, unescape.c) but
   compiled into a single _html extension. Each feature file implements one
   public entry point declared here; core/module.c wires them into the module. */

#ifndef TURBOHTML_H
#define TURBOHTML_H

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <stdint.h>

/* Mark an intentional switch fall-through. gcc turns on -Wimplicit-fallthrough with
   -Wextra and only honors the fallthrough attribute across versions; the C23
   [[fallthrough]] spelling is unavailable under c_std=c11, and MSVC has no C
   equivalent and does not raise the warning. */
#if defined(__GNUC__) || defined(__clang__)
#define TH_FALLTHROUGH __attribute__((fallthrough))
#else
#define TH_FALLTHROUGH ((void)0)
#endif

/* Implemented in escape.c. Signature matches METH_VARARGS | METH_KEYWORDS. */
PyObject *turbohtml_escape(PyObject *module, PyObject *args, PyObject *kwds);

/* Implemented in unescape.c. Signature matches METH_O. */
PyObject *turbohtml_unescape(PyObject *module, PyObject *arg);

/* Implemented in css.c, the value-safe CSS minifier. Both match METH_VARARGS: (source, baseline). */
PyObject *turbohtml_minify_css(PyObject *module, PyObject *args);
PyObject *turbohtml_minify_css_inline(PyObject *module, PyObject *args);

/* Implemented in markup.c, the markupsafe-compatible escape surface. escape,
   escape_silent, soft_str, and _register_markup all match METH_O. */
PyObject *turbohtml_markup_escape(PyObject *module, PyObject *s);
PyObject *turbohtml_markup_escape_silent(PyObject *module, PyObject *s);
PyObject *turbohtml_markup_soft_str(PyObject *module, PyObject *s);
PyObject *turbohtml_register_markup(PyObject *module, PyObject *type);

/* Implemented in dom/node.c. _register_xpath_string stores the str subclass that
   smart_strings xpath() results carry; signature matches METH_O. */
PyObject *turbohtml_register_xpath_string(PyObject *module, PyObject *type);

/* Implemented in dom/document.c, next to the parse_bytes sniffing pipeline it
   reuses. _detect runs the encoding sniff (BOM, <meta> prescan, content detection)
   over a byte buffer without parsing; the turbohtml.detect facade shapes its
   (winner, certain, ranked scores, bom) tuple into EncodingMatch results. METH_O. */
PyObject *turbohtml_detect_encoding(PyObject *module, PyObject *arg);
PyObject *turbohtml_detect_language(PyObject *module, PyObject *args);

/* Implemented in unicode/normalize.c. _normalize(form, text) applies one of the
   four Unicode normalization forms (form 0..3 = NFC, NFD, NFKC, NFKD) to text;
   _is_normalized(form, text) tests membership. Both match METH_VARARGS. */
PyObject *turbohtml_normalize(PyObject *module, PyObject *args);
PyObject *turbohtml_is_normalized(PyObject *module, PyObject *args);

/* Implemented in css/select/to_xpath.c, which reuses the selector parser.
   _css_to_xpath(selector, prefix) translates a CSS selector list to an
   equivalent XPath 1.0 expression; matches METH_VARARGS. */
PyObject *turbohtml_css_to_xpath(PyObject *module, PyObject *args);
PyObject *turbohtml_css_specificity(PyObject *module, PyObject *args);

/* Implemented in css/cssom/cssom.c: the CSS Object Model cascade (issue #546).
   _css_parse_declarations(text) and _css_parse_rules(text) parse a declaration block
   and a whole stylesheet (both METH_O); _css_computed_style(element) resolves the
   cascade for one element and returns its computed longhands (METH_O). */
PyObject *turbohtml_css_parse_declarations(PyObject *module, PyObject *text);
PyObject *turbohtml_css_parse_rules(PyObject *module, PyObject *text);
PyObject *turbohtml_css_computed_style(PyObject *module, PyObject *arg);

/* Implemented in dom/element.c: stores the SelectorSyntaxError type the selector
   and XPath parsers raise on a malformed expression (METH_O); turbohtml._selectors
   registers it on import. */
PyObject *turbohtml_register_selector_error(PyObject *module, PyObject *type);

/* Implemented in linkify.c. _linkify_scan finds URL/email spans in a text run;
   _linkify_find adds the detector's custom TLD and scheme-less scheme config.
   Both signatures match METH_VARARGS. */
PyObject *turbohtml_linkify_scan(PyObject *module, PyObject *args);
PyObject *turbohtml_linkify_find(PyObject *module, PyObject *args);

/* Implemented in url/url.c. _url_split(url) breaks a URL into (scheme, netloc,
   path, query, fragment, userinfo, host, port, has_port, host_kind) the way the
   WHATWG basic parser bounds the components, the split step the _urls.py cleaner
   delegates instead of urllib.parse.urlsplit. METH_O. _url_percent_encode(text,
   set_id) and _url_percent_decode(text) are the component percent-coders the
   cleaner reaches instead of urllib.parse.quote/unquote; encode raises
   UnicodeEncodeError on a lone surrogate (the shim rewraps it as ValueError). */
PyObject *turbohtml_url_split(PyObject *module, PyObject *arg);
PyObject *turbohtml_url_percent_encode(PyObject *module, PyObject *args);
PyObject *turbohtml_url_percent_decode(PyObject *module, PyObject *arg);

/* Implemented in url/url.c. th_url_join resolves a (possibly relative) reference against a base URL, the RFC 3986 5.3
   reference transform urllib.parse.urljoin runs; base_url() and the extraction methods call it directly, and
   _url_join(base, target) exposes it to the extract_links shim. Both arguments are borrowed str references; returns a
   new reference, NULL with a ValueError set when a component cannot be split (an unbalanced IPv6 bracket). */
PyObject *th_url_join(PyObject *base, PyObject *target);
PyObject *turbohtml_url_join(PyObject *module, PyObject *args);

/* Implemented in url/idna.c. th_url_to_ascii runs the WHATWG domain-to-ASCII step (Unicode IDNA ToASCII, UTS #46 with
   Transitional_Processing=false and UseSTD3ASCIIRules=false): UTS #46 mapping, NFC, and per-label punycode. _urls.py
   reaches _url_to_ascii(host) for its registered-name hosts instead of the IDNA-2003 str.encode("idna") codec; the host
   is a borrowed str, the result a new ASCII str, NULL with a ValueError when a label holds a code point punycode cannot
   encode (an unpaired surrogate). METH_O. */
PyObject *th_url_to_ascii(PyObject *host);
PyObject *turbohtml_url_to_ascii(PyObject *module, PyObject *arg);

/* Implemented in url/registrable.c. _registrable_domain(host) returns a lowercased
   host's registrable domain (eTLD+1) from the shipped IANA and Public Suffix List
   tables, the site boundary behind extract_links(external_only=True). METH_O. */
PyObject *turbohtml_registrable_domain(PyObject *module, PyObject *arg);

/* Implemented in sanitize.c. _sanitize filters a parsed fragment in place against
   a policy; signature matches METH_VARARGS. turbohtml_node_borrow is implemented
   in dom/node.c and lends sanitize.c the tree+node a Python element wraps. */
struct th_tree;
struct th_node;
int turbohtml_node_borrow(PyObject *module, PyObject *obj, struct th_tree **tree, struct th_node **node);
PyObject *turbohtml_sanitize(PyObject *module, PyObject *args);

/* Implemented in annotation.c, the inscriptis annotation output processors over
   the (text, spans) pair Node.to_annotated_text() returns. annotation_surface
   groups the matched substrings by label; annotation_tags weaves them back into
   the text as inline <label>...</label> markup. Both match METH_VARARGS. */
PyObject *turbohtml_annotation_surface(PyObject *module, PyObject *args);
PyObject *turbohtml_annotation_tags(PyObject *module, PyObject *args);

/* Implemented in links.c, the engine behind Node.links()/rewrite_links()/
   resolve_links(). Each takes the wrapping node (owner, for the per-tree handle
   and the Element wrappers) and the already-derived tree+root the thin C methods
   in dom/node.c hand over: turbohtml_node_links enumerates every link-bearing
   location as Link records, turbohtml_node_rewrite_links replaces each via a
   callback, turbohtml_node_resolve_links absolutizes them against a base URL with
   urllib.parse.urljoin. _register_links stores the Link record type (METH_O).
   turbohtml_node_handle and turbohtml_node_wrap_in (dom/node.c) lend links.c the
   per-tree handle for the critical section and the node->Element wrapper. */
PyObject *turbohtml_node_handle(PyObject *obj);
PyObject *turbohtml_node_wrap_in(PyObject *owner, struct th_node *node);
PyObject *turbohtml_register_links(PyObject *module, PyObject *type);

/* Implemented in dom/node.c: stores the Article record type Node.article() builds
   (METH_O); turbohtml._article registers it on import. */
PyObject *turbohtml_register_article(PyObject *module, PyObject *type);

/* Implemented in dom/node.c: stores the SourceLocation and SourceSpan record types
   Element.source_location builds (METH_VARARGS); turbohtml._locations registers
   them on import. */
PyObject *turbohtml_register_locations(PyObject *module, PyObject *args);

/* Implemented in dom/node.c: stores the Markdown/PlainText/Html config types in
   module state so to_markdown()/to_text()/serialize() can isinstance-check the
   options they are handed (METH_VARARGS); turbohtml._render registers them on
   import. */
PyObject *turbohtml_register_render_configs(PyObject *module, PyObject *args);
PyObject *turbohtml_node_links(PyObject *owner, struct th_tree *tree, struct th_node *root);
PyObject *turbohtml_node_rewrite_links(PyObject *owner, struct th_tree *tree, struct th_node *root, PyObject *replace);
PyObject *turbohtml_node_resolve_links(PyObject *owner, struct th_tree *tree, struct th_node *root, PyObject *base_url);

/* Implemented in extract/structured_data.c, the engine behind the Document.structured_data()/json_ld()/opengraph()/
   microdata() methods (wired into the document method table in dom/document.c). Each gathers one structured-data format
   from the document in a pure-C tree walk under the per-tree critical section. structured_data()/opengraph()/
   microdata() match METH_VARARGS | METH_KEYWORDS for their optional base_url that absolutizes URL-valued fields;
   json_ld() matches METH_NOARGS, gathering the raw <script type=application/ld+json> texts and parsing them through the
   Python facade. structured_data() and microdata() hand their gathered fields to the StructuredData / MicrodataItem
   record classes the facade defines. _register_structured_data (METH_VARARGS) stores the JSON-LD parser and those two
   classes. */
PyObject *turbohtml_document_structured_data(PyObject *self, PyObject *args, PyObject *kwargs);
PyObject *turbohtml_document_json_ld(PyObject *self, PyObject *unused);
PyObject *turbohtml_document_opengraph(PyObject *self, PyObject *args, PyObject *kwargs);
PyObject *turbohtml_document_microdata(PyObject *self, PyObject *args, PyObject *kwargs);
PyObject *turbohtml_document_rdfa(PyObject *self, PyObject *args, PyObject *kwargs);
PyObject *turbohtml_document_dublin_core(PyObject *self, PyObject *unused);
PyObject *turbohtml_register_structured_data(PyObject *module, PyObject *args);

/* Implemented in extract/feed.c, the engine behind Document.feed() (wired into the document method table in
   dom/document.c). Normalizes an RSS 2.0, Atom 1.0, or RDF/RSS-1.0 document into one Feed record in a pure-C tree walk
   under the per-tree critical section, handing the gathered fields to the Feed / Entry NamedTuple classes the facade
   defines. _register_feed (METH_VARARGS) stores those two classes. */
PyObject *turbohtml_document_feed(PyObject *self, PyObject *unused);
PyObject *turbohtml_register_feed(PyObject *module, PyObject *args);

/* Implemented in dom/document.c, the URL-resolution routine base_url() runs, shared with the extraction methods so
   their base_url reuses it rather than reinventing RFC 3986 resolution. th_url_resolve joins a (possibly relative)
   target onto a base and percent-encodes the result; th_document_base_url resolves the document's <base href> against a
   fallback. Both return a new reference, NULL with a ValueError set when a component cannot be split. */
PyObject *th_url_resolve(PyObject *base, PyObject *target);
PyObject *th_document_base_url(PyObject *self, PyObject *fallback);

/* Implemented in tables.c, the engine behind Element.rows()/records() and Node.tables(). Each takes the wrapping node
   (owner, for the per-tree handle) and the already-derived tree+node the thin C methods in dom/element.c and dom/node.c
   hand over: turbohtml_element_table_rows reads one table as list[list[str]] with rowspan/colspan expanded,
   turbohtml_element_table_records keys the first row over the rest as list[dict], and turbohtml_node_tables returns
   rows() for every table in the subtree. */
PyObject *turbohtml_element_table_rows(PyObject *owner, struct th_tree *tree, struct th_node *table);
PyObject *turbohtml_element_table_records(PyObject *owner, struct th_tree *tree, struct th_node *table);
PyObject *turbohtml_node_tables(PyObject *owner, struct th_tree *tree, struct th_node *root);

/* Implemented in extract/dates.c, the pure date-string parser turbohtml._dates
   delegates instead of the datetime module and its re patterns. _date_scan(text,
   year) returns the first numeric date (ISO 8601, an 8-digit stamp, or a
   day-month-year spelling) as a (year, month, day) tuple or None; _date_scan_all
   returns every ISO, day-month-year, and written-out date for the text stage's
   frequency scoring; both match METH_VARARGS and take the current year as the pivot
   _correct_year expands a two-digit year against. _date_url(url) returns the
   /YYYY/MM/DD/ date a URL path carries (METH_O). */
PyObject *turbohtml_date_scan(PyObject *module, PyObject *args);
PyObject *turbohtml_date_scan_all(PyObject *module, PyObject *args);
PyObject *turbohtml_date_url(PyObject *module, PyObject *url);

/* Implemented in tokenizer/tokenizer.c. tokenize() matches METH_VARARGS | METH_KEYWORDS;
   the internal conformance hook _tokenize_states matches METH_VARARGS. */
PyObject *turbohtml_tokenize(PyObject *module, PyObject *args, PyObject *kwargs);
PyObject *turbohtml_tokenize_states(PyObject *module, PyObject *args);

/* Implemented in dom/binding.c. The internal conformance hooks _parse_tree
   and _parse_fragment return the html5lib "#document" serialization of a parsed
   document / innerHTML fragment. */
PyObject *turbohtml_parse_tree(PyObject *module, PyObject *args);
PyObject *turbohtml_parse_fragment(PyObject *module, PyObject *args);
PyObject *turbohtml_parse_only(PyObject *module, PyObject *arg);

/* Implemented in query/xslt.c, the XSLT 1.0 processor. _xslt_transform(stylesheet,
   source, params) transforms the source document by the stylesheet's templates,
   reusing the XPath engine for every match pattern and select expression, and
   returns the serialized result string under the stylesheet's xsl:output method.
   Signature matches METH_VARARGS. */
PyObject *turbohtml_xslt_transform(PyObject *module, PyObject *args);

/* Implemented in query/xpath/functions.c. _xpath_parse compiles an XPath expression and returns a
   canonical S-expression of the parsed AST; the conformance hook the parser tests
   diff against. Signature matches METH_O. */
PyObject *turbohtml_xpath_parse(PyObject *module, PyObject *arg);

/* Implemented in serialize/js/lexdump.c. minify_js is the one minifier, exposed as
   _minify_js(source, fold, mangle) (METH_VARARGS); the _tokens / _parse hooks are not
   minifiers but conformance dumps of the token stream and the AST that the JS lexer and
   parser tests diff against (METH_O). */
PyObject *turbohtml_minify_js(PyObject *module, PyObject *args);
PyObject *turbohtml_minify_js_tokens(PyObject *module, PyObject *arg);
PyObject *turbohtml_minify_js_parse(PyObject *module, PyObject *arg);

/* Implemented in dom/formatters.c: store the JSMinify / CSSMinify config types in module state so
   Minify(minify_js=...)/Minify(minify_css=...) validation and their getters reach them with a
   pointer load rather than an import (METH_O); turbohtml._jsminify and turbohtml._cssmin register
   them on import. */
PyObject *turbohtml_register_js_minify(PyObject *module, PyObject *type);
PyObject *turbohtml_register_css_minify(PyObject *module, PyObject *type);

/* SWAR lane probes over a 64-bit word holding four UCS-2 / two UCS-4 code
   points. The has-zero test is exact as an existence test: the subtraction can
   only borrow across a lane when that lane itself is zero, so the mask is
   nonzero iff some lane equals the searched value. Lane positions inside the
   mask depend on byte order, so callers treat a nonzero mask as "somewhere in
   these lanes" and resolve the exact index with a scalar scan. */

/* Implemented in validate/schema.c, the XSD / RELAX NG schema validator behind
   turbohtml.validate (issue #539). _schema_compile(kind, source) parses and compiles a
   schema (kind 0 = XSD, 1 = RELAX NG) into a PyCapsule, raising ValueError on a
   malformed schema; _schema_validate(capsule, node) validates a parse_xml document or
   element against it, returning (valid, [errors]) where each error is a (message, path,
   line, type) tuple. Both match METH_VARARGS. */
PyObject *turbohtml_schema_compile(PyObject *module, PyObject *args);
PyObject *turbohtml_schema_validate(PyObject *module, PyObject *args);

/* Implemented in validate/conformance.c, the HTML5 authoring-conformance checker behind
   turbohtml.conformance (issue #541). _conformance_check(node) walks a parsed document or
   subtree and returns a list of (code, severity, message, line, column) findings -- the
   document-conformance requirements (img alt, obsolete markup, duplicate id, ARIA role,
   heading and section structure, document title and language) the parser does not raise
   as a ParseError. Matches METH_O. */
PyObject *turbohtml_conformance_check(PyObject *module, PyObject *arg);

#define UCS2_LANES 4
#define UCS4_LANES 2

static inline uint64_t swar_haslane16(uint64_t word, uint16_t value) {
    uint64_t lanes = word ^ (0x0001000100010001ULL * value);
    return (lanes - 0x0001000100010001ULL) & ~lanes & 0x8000800080008000ULL;
}

static inline uint64_t swar_haslane32(uint64_t word, uint32_t value) {
    uint64_t lanes = word ^ (0x0000000100000001ULL * value);
    return (lanes - 0x0000000100000001ULL) & ~lanes & 0x8000000080000000ULL;
}

#endif /* TURBOHTML_H */
