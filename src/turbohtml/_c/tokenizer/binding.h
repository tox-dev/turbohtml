/* Internal glue between the state machine and the Python types.

   tokenizer/token.c defines the Token type and the TokenType enum; tokenizer/tokenizer.c
   defines the Tokenizer type, its token iterator, and the tokenize() helper;
   core/module.c creates the module and calls the register functions. All three
   share the per-module state declared here, which owns the heap types so the
   module stays compatible with sub-interpreters and the free-threaded build. */

#ifndef TURBOHTML_TOKENIZER_PY_H
#define TURBOHTML_TOKENIZER_PY_H

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include "core/pycompat.h"
#include "tokenizer/statemachine.h"

/* Py_BEGIN_CRITICAL_SECTION arrived in 3.13 for the free-threaded build; on a GIL
   build it is a brace no-op, and before 3.13 (always GIL) we define the same no-op,
   so the tokenizer's per-object locking compiles on every supported interpreter
   while only the free-threaded build pays for a real lock. PyPy's cpyext defines
   neither, and takes the same no-op for the same reason: it holds the GIL. */
#ifndef Py_BEGIN_CRITICAL_SECTION
#define Py_BEGIN_CRITICAL_SECTION(op) {
#define Py_END_CRITICAL_SECTION() }
#endif

typedef struct {
    PyObject *token_type;             /* Token */
    PyObject *tokenizer_type;         /* Tokenizer */
    PyObject *iter_type;              /* the iterator returned by feed()/close()/tokenize() */
    PyObject *kind_enum;              /* TokenType (enum.IntEnum) */
    PyObject *kinds[6];               /* cached TokenType members, indexed by enum th_kind */
    PyObject *node_type;              /* Node (the sealed-hierarchy base) */
    PyObject *element_type;           /* Element */
    PyObject *text_type;              /* Text */
    PyObject *comment_type;           /* Comment */
    PyObject *doctype_type;           /* Doctype */
    PyObject *pi_type;                /* ProcessingInstruction */
    PyObject *cdata_type;             /* CData */
    PyObject *document_type;          /* Document */
    PyObject *shadow_root_type;       /* ShadowRoot (a shadow tree's document-fragment-like root) */
    PyObject *parser_type;            /* IncrementalParser (push parse to a tree) */
    PyObject *parse_error_type;       /* ParseError (a collected WHATWG parse error) */
    PyObject *parse_error_exc;        /* HTMLParseError (raised by parse(strict=True)) */
    PyObject *handle_type;            /* _TreeHandle (owns th_tree + the input str) */
    PyObject *attrs_type;             /* _Attrs (the live mutable view of an element's attributes) */
    PyObject *walker_type;            /* _NodeIterator (descendants / ancestors / siblings) */
    PyObject *tree_walker_type;       /* TreeWalker (the DOM cursor traversal object) */
    PyObject *node_iterator_type;     /* NodeIterator (the DOM flat filtered traversal object) */
    PyObject *string_walker_type;     /* _StringIterator (strings / stripped_strings) */
    PyObject *serialize_iter_type;    /* _SerializeIterator (serialize_iter chunk stream) */
    PyObject *sax_events_type;        /* _SaxEvents (the O(depth) event walk behind saxparse) */
    PyObject *rewrite_handle_type;    /* _RewriteHandle (the node handle a streaming rewrite handler edits) */
    PyObject *namespace_enum;         /* Namespace (enum.Enum) */
    PyObject *namespaces[3];          /* cached Namespace members, indexed by enum th_ns */
    PyObject *axis_enum;              /* Axis (enum.Enum) for find()/find_all() */
    PyObject *axes[7];                /* cached Axis members, indexed by enum th_axis */
    PyObject *formatter_enum;         /* Formatter (enum.Enum) for serialize()/encode() */
    PyObject *formatters[3];          /* cached Formatter members, indexed by enum th_formatter */
    PyObject *minify_type;            /* Minify (a serialize(layout=...) mode) */
    PyObject *indent_type;            /* Indent (a serialize(layout=...) mode) */
    PyObject *pattern_type;           /* re.Pattern, to recognize a compiled-regex filter */
    PyObject *re_compile;             /* re.compile, to turn a str pattern into a program for re()/re_first() */
    PyObject *markup_type;            /* turbohtml.markup.Markup, stamped onto escape() results */
    PyObject *xpath_string_type;      /* turbohtml._xpath.XPathString, for smart_strings xpath() results */
    PyObject *xpath_type;             /* XPath, the precompiled reusable expression object */
    PyObject *selector_error;         /* turbohtml.SelectorSyntaxError, raised on a malformed CSS selector or XPath */
    PyObject *link_type;              /* turbohtml._links.Link, the (element, attribute, url) record links() yields */
    PyObject *json_ld_parser;         /* turbohtml._structured_data._parse_json_ld, the JSON-LD text parser */
    PyObject *microdata_item_type;    /* turbohtml._structured_data.MicrodataItem, one Microdata item record */
    PyObject *rdfa_item_type;         /* turbohtml._structured_data.RdfaItem, one RDFa resource record */
    PyObject *structured_data_type;   /* turbohtml._structured_data.StructuredData, the combined-format record */
    PyObject *opengraph_type;         /* turbohtml._structured_data.OpenGraph, the record Document.opengraph() yields */
    PyObject *feed_type;              /* turbohtml._feed.Feed, the record Document.feed() yields */
    PyObject *entry_type;             /* turbohtml._feed.Entry, one feed item/entry record */
    PyObject *article_type;           /* turbohtml._article.Article, the record Node.article() yields */
    PyObject *source_location_type;   /* turbohtml._locations.SourceLocation, Element.source_location's record */
    PyObject *source_span_type;       /* turbohtml._locations.SourceSpan, one tag/attribute span */
    PyObject *js_minify_type;         /* turbohtml._minify.JSMinify, the Minify(minify_js=...) script-pass config */
    PyObject *css_minify_type;        /* turbohtml._cssmin.CSSMinify, the Minify(minify_css=...) style-pass config */
    PyObject *markdown_config_type;   /* turbohtml._render.Markdown, the to_markdown() options type */
    PyObject *plaintext_config_type;  /* turbohtml._render.PlainText, the to_text()/to_annotated_text() options type */
    PyObject *html_config_type;       /* turbohtml._render.Html, the serialize()/encode() options type */
    PyObject *canonical_config_type;  /* turbohtml._render.Canonical, the canonicalize() options type */
    PyObject *range_type;             /* Range, the live DOM Range (dom/range.c) */
    PyObject *static_range_type;      /* StaticRange, the immutable boundary-point snapshot */
    PyObject *mutation_observer_type; /* MutationObserver, the synchronous tree-mutation watcher (dom/observe.c) */
    PyObject *mutation_record_type;   /* turbohtml.mutations.MutationRecord, one delivered change record */
    /* A freelist of node wrappers: find_all()/select()/iteration mint and drop one
       NodeObject per visited node, and every node subtype shares sizeof(NodeObject)
       (the payload lives in the C th_node), so one pool re-stamps ob_type on reuse.
       The head is a NodeObject* (held as PyObject* since NodeObject is declared
       later); the link rides in each pooled object's unused node field. Built only
       on the GIL build, where the GIL serializes access; the free-threaded build
       skips it (a shared pool would race) and keeps the tp_alloc/tp_free path. */
    PyObject *node_freelist;
    int node_freelist_len;
} module_state;

/* Register the types and enum into module/state. Each returns 0 or -1. */
int token_register(PyObject *module, module_state *state);
int tokenizer_register(PyObject *module, module_state *state);
int tree_register(PyObject *module, module_state *state);
int sax_register(PyObject *module, module_state *state);
int rewrite_register(PyObject *module, module_state *state);

/* Stream a DOM-less rewrite over source: match the compiled element handlers against the
   open-element spine and drive the text/comment/doctype handlers, applying each handler's
   edits to the incrementally emitted output. Wired as the private _rewrite() behind
   turbohtml.rewrite.rewrite(). Matches METH_VARARGS:
   (source, element_handlers, text_handler, comment_handler, doctype_handler). */
PyObject *turbohtml_rewrite(PyObject *module, PyObject *args);

/* Parse markup and return a _SaxEvents iterator that walks the constructed tree in
   document order, yielding one event tuple at a time without ever handing back a
   tree. Wired as the private _sax_events() behind turbohtml.saxparse. Matches
   METH_O. */
PyObject *turbohtml_sax_events(PyObject *module, PyObject *arg);

/* Parse markup and drive a caller-supplied builder object over the constructed tree in
   document order (create_document/create_doctype/create_element/create_text/
   create_comment/create_pi/append), returning whatever the builder made its root without
   materializing a navigable tree. Wired as the private _parse_into() behind
   turbohtml.treebuild. Matches METH_VARARGS: (source, builder). */
PyObject *turbohtml_parse_into(PyObject *module, PyObject *args);
int range_register(PyObject *module, module_state *state);

/* Register the MutationObserver type (dom/observe.c). */
int observe_register(PyObject *module, module_state *state);

/* Bind the turbohtml.mutations.MutationRecord type the record drain builds instances
   of; wired as the private _register_mutation_record(). Matches METH_O. */
PyObject *turbohtml_register_mutation_record(PyObject *module, PyObject *type);

/* Free every node wrapper parked on the freelist; called from module teardown
   before the node types are cleared. A no-op on the free-threaded build. */
void th_node_freelist_clear(module_state *state);

/* Public navigable-tree entry points (dom/node.c), wired as parse() and
   parse_fragment(). parse() matches METH_O; parse_fragment() matches
   METH_VARARGS | METH_KEYWORDS. */
PyObject *turbohtml_parse(PyObject *module, PyObject *args, PyObject *kwargs);
PyObject *turbohtml_parse_xml(PyObject *module, PyObject *args, PyObject *kwargs);
PyObject *turbohtml_tree_parse_fragment(PyObject *module, PyObject *args, PyObject *kwargs);

/* Rebuild a node and its subtree from a pickle (kind, data, children) triple,
   wired as the private _reconstruct() the node __reduce__ points pickle at. */
PyObject *turbohtml_reconstruct(PyObject *module, PyObject *args);

/* Build a complete HTML5 document (doctype, <html>, <head> with an optional
   <meta charset> and <title>, <body>) from head and body node lists, wired as
   the private _build_document() behind turbohtml.build.document(). Matches
   METH_VARARGS | METH_KEYWORDS. */
PyObject *turbohtml_build_document(PyObject *module, PyObject *args, PyObject *kwds);

/* Build a Token from a freshly emitted record. Small records are copied and a
   large text run is moved out of the record (which then regrows). A slice
   record resolves lazily against source when the input is borrowed from it
   (the Token keeps source alive), and immediately against sm's own storage
   otherwise (a later feed may move it). */
PyObject *token_from_record(module_state *state, const th_tokenizer *sm, PyObject *source, th_token *record);

#endif /* TURBOHTML_TOKENIZER_PY_H */
