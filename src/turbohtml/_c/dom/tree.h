/* WHATWG HTML tree construction: pure C, no Python objects.

   This file is the algorithm half of the parser: it drives the tokenizer state
   machine (tokenizer/statemachine.h), applies the WHATWG tree-construction rules, and
   builds a node tree. Like the tokenizer it creates no PyObjects; the Python
   layer in dom/node.c walks the finished C tree and wraps only the
   nodes the caller touches.

   Nodes are bump-allocated from an arena owned by the tree and freed in one
   shot, so there is no per-node malloc/free and ownership is trivial. Element
   identity is an interned integer atom (tag_atom.h), so every scope and
   category test in the algorithm is an integer compare, never a strcmp. */

#ifndef TURBOHTML_TREEBUILDER_H
#define TURBOHTML_TREEBUILDER_H

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include "data/attr_atom.h"
#include "data/tag_atom.h"
#include "tokenizer/statemachine.h"

enum th_node_type {
    TH_NODE_DOCUMENT,
    TH_NODE_ELEMENT,
    TH_NODE_TEXT,
    TH_NODE_COMMENT,
    TH_NODE_DOCTYPE,
    TH_NODE_CONTENT, /* a <template>'s content document fragment */
    TH_NODE_PI,      /* a processing instruction (construction only; the parser folds
                        <? ... > into a comment and foreign CDATA into text) */
    TH_NODE_CDATA,   /* a CDATA section (construction only, same reason) */
};

/* A doctype node reuses its (element-only) tag_flags field to record which
   identifiers the source actually supplied. The serialized text writes an empty
   string for both a missing and a present-but-empty identifier, so these flags
   are the only way to tell `<!DOCTYPE x SYSTEM "s">` (public missing) from
   `<!DOCTYPE x PUBLIC "" "s">` (public present but empty). */
#define TH_DOCTYPE_HAS_PUBLIC 0x40u
#define TH_DOCTYPE_HAS_SYSTEM 0x80u

/* An element node reuses a spare tag_flags bit (the category bits from the atom
   table only occupy 0x01..0x10) to record that the source actually closed it with
   an end tag, as opposed to the parser closing it implicitly or at EOF. The
   sanitizer's escape mode reads it to reproduce a disallowed element as visible
   text without fabricating a `</tag>` the author never wrote. */
#define TH_ELEM_CLOSED_BY_END_TAG 0x20u

/* An attribute on an element node. The name is interned to an atom: a static
   compile-time id for common names (attr_atom.h), or a per-tree dynamic id for
   the rest. attr_record() recovers the name bytes for serialization and
   Element.attrs, so every name match the finder and selector do is an integer
   compare, never a strcmp. */
typedef struct {
    uint32_t name_atom;
    Py_UCS4 *value; /* code points, value_len of them; NULL when valueless */
    Py_ssize_t value_len;
} th_node_attr;

/* A tree node. Pointer-linked siblings + first/last child + parent, the layout
   every fast parser (lexbor, Go x/net/html, html5ever) converges on. Text and
   comment payloads are code-point buffers; elements carry an interned atom plus
   the original tag spelling for serialization of non-atom (unknown) tags. */
typedef struct th_node th_node;
enum th_ns {
    TH_NS_HTML,
    TH_NS_SVG,
    TH_NS_MATHML,
};

struct th_node {
    enum th_node_type type;
    uint16_t atom;     /* TH_TAG_* for elements, else TH_TAG_UNKNOWN */
    uint8_t tag_flags; /* category bitmask from the atom table */
    uint8_t ns;        /* enum th_ns: HTML / SVG / MathML */
    th_node *parent;
    th_node *first_child;
    th_node *last_child;
    th_node *prev_sibling;
    th_node *next_sibling;
    /* element: tag name; text/comment: payload; doctype: name */
    Py_UCS4 *text;
    Py_ssize_t text_len;
    th_node_attr *attrs;
    Py_ssize_t attr_count;
};

typedef struct th_tree th_tree;

/* Parse a whole document. kind/data/length are a borrowed PyUnicode buffer that
   must outlive the returned tree (its slice text points into it). positions
   records each element's source line/column (read via th_node_source_position) at
   the cost of two trailing words per element; pass 0 to skip it. Returns NULL only
   on allocation failure (no Python error is set). */
th_tree *th_tree_parse(int kind, const void *data, Py_ssize_t length, int positions);

/* Create an empty tree to own programmatically constructed nodes. Returns NULL on
   allocation failure (no Python error is set). */
th_tree *th_tree_new(void);

/* Construct a text/comment/doctype/cdata node (by enum th_node_type) owning a copy
   of the data code points in the tree's arena. NULL on allocation failure. */
th_node *th_tree_make_data_node(th_tree *tree, int type, const Py_UCS4 *data, Py_ssize_t len);

/* Construct a processing-instruction node owning target and data (packed with the
   split point in attr_count). NULL on allocation failure. */
th_node *th_tree_make_pi(th_tree *tree, const Py_UCS4 *target, Py_ssize_t target_len, const Py_UCS4 *data,
                         Py_ssize_t data_len);

/* Construct an element owning a copy of the tag name, with attr_count empty
   attribute slots; fill each with th_tree_set_attr. NULL on allocation failure. */
th_node *th_tree_make_element(th_tree *tree, const Py_UCS4 *tag, Py_ssize_t tag_len, uint16_t atom,
                              Py_ssize_t attr_count);

/* Fill attribute slot index on a constructed element (has_value 0 means a
   valueless attribute). Returns 0, or -1 on allocation failure. */
int th_tree_set_attr(th_tree *tree, th_node *node, Py_ssize_t index, const char *name, Py_ssize_t name_len,
                     const Py_UCS4 *value, Py_ssize_t value_len, int has_value);

/* Upsert an attribute by name (replace value or append a slot); has_value 0 makes
   it valueless. Returns 0, or -1 on allocation failure. */
int th_node_attr_set(th_tree *tree, th_node *node, const char *name, Py_ssize_t name_len, const Py_UCS4 *value,
                     Py_ssize_t value_len, int has_value);

/* An element's attribute array and count; (NULL, 0) for every non-element node. Only
   an element carries attribute storage: a text-node span reuses attr_count to hold a
   source offset with attrs == NULL, so a reader that walks arbitrary nodes (the XPath
   attribute axis, lang()) must gate on the element type -- which it does by reaching
   the storage only through this accessor (issues #401, #422). */
static inline Py_ssize_t th_node_attributes(const th_node *node, th_node_attr **attrs) {
    if (node->type == TH_NODE_ELEMENT) {
        *attrs = node->attrs;
        return node->attr_count;
    }
    *attrs = NULL;
    return 0;
}

/* The index of node's attribute named `name` (UTF-8, caller-lowercased), or -1.
   Foreign elements can store a case-adjusted name (e.g. MathML definitionURL), so
   a missed atom match falls back to a case-insensitive scan for non-HTML nodes. */
Py_ssize_t th_node_attr_find(th_tree *tree, th_node *node, const char *name, Py_ssize_t name_len);

/* Remove the named attribute; returns 1 if one was removed, else 0. */
int th_node_attr_del(th_tree *tree, th_node *node, const char *name, Py_ssize_t name_len);

/* Replace a node's character data with a copy of len code points. Returns 0, or -1
   on allocation failure. */
int th_node_set_data(th_tree *tree, th_node *node, const Py_UCS4 *data, Py_ssize_t len);

/* Structural-edit primitives. remove detaches a node from its parent; append and
   insert_before link it (ref==NULL appends); contains reports whether ancestor is
   node or one of its ancestors; copy_node deep-copies a subtree into another tree
   (NULL on allocation failure). */
void th_node_remove(th_node *child);
void th_node_append_child(th_node *parent, th_node *child);
void th_node_insert_before(th_node *parent, th_node *child, th_node *ref);
int th_node_contains(th_node *ancestor, th_node *node);
th_node *th_tree_copy_node(th_tree *dest, th_tree *src, th_node *src_node);

/* DOM normalize over a subtree: merge each run of adjacent Text children into one
   node and drop empty Text nodes, recursing into every element. */
void th_node_normalize(th_tree *tree, th_node *root);

/* Parse an HTML fragment as if set as the innerHTML of the given context element
   (e.g. "td", or "svg path"). The returned tree serializes the context root's
   children. context is a NUL-free ASCII name; context_len its length. positions
   records element source line/column as in th_tree_parse. */
th_tree *th_tree_parse_fragment(int kind, const void *data, Py_ssize_t length, const char *context,
                                Py_ssize_t context_len, int positions);

/* Whether context names a real element the public parse_fragment() accepts: a known
   HTML tag, or any explicitly namespaced (svg/math) foreign element. */
int th_tree_fragment_context_known(const char *context, Py_ssize_t context_len);

void th_tree_free(th_tree *tree);

/* --- streaming (push) parse --- */

/* A push parser driving the tokenizer and tree builder incrementally, so a
   document can be fed in chunks without ever holding the whole source: the
   tokenizer reclaims its consumed input on each feed and the insertion-mode
   state persists across feeds. */
typedef struct th_stream th_stream;

/* Create an empty push parser. positions records element source line/column as in
   th_tree_parse. Returns NULL on allocation failure (no Python error is set). */
th_stream *th_stream_new(int positions);

/* Append a chunk of code points (a borrowed PyUnicode buffer, copied) and build
   as far as the now-available tokens allow. Returns 0, or -1 on allocation
   failure. */
int th_stream_feed(th_stream *stream, int kind, const void *data, Py_ssize_t length);

/* Signal end of input, flush the remaining tokens, apply the EOF
   missing-element rules, and hand the finished tree to the caller (who must
   th_tree_free it). The stream no longer owns the tree afterwards. Returns NULL
   on allocation failure. */
th_tree *th_stream_finish(th_stream *stream);

/* Free the parser and, unless th_stream_finish already handed it off, its
   in-progress tree. */
void th_stream_free(th_stream *stream);

/* Serialize the tree in the html5lib tree-construction "#document" format (one
   "| " indented line per node) into a freshly PyMem-allocated UCS4 buffer;
   *out_len receives the length. Returns NULL on allocation failure. Used by the
   conformance harness to diff against the .dat expectations. */
Py_UCS4 *th_tree_serialize(th_tree *tree, Py_ssize_t *out_len);

/* --- navigable-tree accessors for the public Python Node API --- */

/* The document (root) node; its children are the doctype/comments and <html>.
   For a fragment the children of the context root are exposed instead. */
th_node *th_tree_document(th_tree *tree);

/* The count of dynamic attribute names interned into this tree's table. It only
   grows, never shrinks, so a change signals that a name a compiled selector once
   resolved as absent may now exist: a monotonic generation for invalidating a
   cached compiled selector whose attribute atoms were resolved against the tree. */
uint32_t th_tree_attr_generation(const th_tree *tree);

/* The WHATWG parse errors collected during the parse, in document order, and
   their count via *out_count. The array (and its static code strings) lives as
   long as the tree; it is empty for a programmatically built or well-formed tree. */
const th_parse_error *th_tree_errors(const th_tree *tree, Py_ssize_t *out_count);

/* Whether the tree was parsed in quirks mode (no doctype or a quirky one). In
   quirks mode CSS class and ID selectors match ASCII case-insensitively
   (Selectors-4 §6.1/§6.2); programmatic trees default to no-quirks. */
int th_tree_quirks(const th_tree *tree);

/* Whether a text node holds only HTML ASCII whitespace (or nothing), realizing a
   zero-copy span on demand. The :empty selector uses it to ignore the document
   white space Selectors-4 §13.2 permits inside an otherwise empty element. */
int th_node_text_is_blank(th_tree *tree, th_node *node);

/* Materialize one text/comment/doctype node's own character data (realizing a
   zero-copy span on demand) into a freshly PyMem-allocated UCS4 buffer.
   *out_len receives the length; returns NULL on allocation failure. */
Py_UCS4 *th_node_data(th_tree *tree, th_node *node, Py_ssize_t *out_len);

/* The concatenated character data of every Text descendant of node, in document
   order. PyMem-allocated; *out_len receives the length. NULL on failure. */
Py_UCS4 *th_node_text(th_tree *tree, th_node *node, Py_ssize_t *out_len);

/* Gather node's concatenated descendant text into a caller-sized, reusable UCS4
   buffer (no allocation, no str), for the find(text=) literal/exact C scan. */
void th_node_collect_text(th_tree *tree, th_node *node, Py_UCS4 *buf);

/* Serialize node and its subtree as HTML (the WHATWG fragment serialization).
   For the document node this is the whole-document markup. PyMem-allocated;
   *out_len receives the length. NULL on failure. */
Py_UCS4 *th_node_html(th_tree *tree, th_node *node, Py_ssize_t *out_len);

/* Serialize only node's children (its inner HTML), WHATWG-conformant and
   compact. PyMem-allocated; *out_len receives the length. NULL on failure. */
Py_UCS4 *th_node_inner_html(th_tree *tree, th_node *node, Py_ssize_t *out_len);

/* Output-shaping options shared by serialize() and encode(): the escape formatter
   plus two opt-in normalizations that leave the tree untouched. Every field is
   off (zero / NULL) by default, so the common serialize costs nothing. */
typedef struct {
    int formatter;       /* escape policy: 0 WHATWG, 1 minimal, 2 named entities */
    int sort_attributes; /* emit each start tag's attributes in ascending name order */
    int inject_meta;     /* ensure <head> declares <meta charset=charset> */
    const char *charset; /* ASCII encoding label for the injected/normalized meta */
    Py_ssize_t charset_len;
} th_serialize_opts;

/* Serialize node and its subtree under opts. When indent is non-NULL it is the
   per-level whitespace unit for pretty output; NULL emits the compact form. PyMem-
   allocated; *out_len receives the length. NULL on failure. */
Py_UCS4 *th_node_serialize(th_tree *tree, th_node *node, const th_serialize_opts *opts, const Py_UCS4 *indent,
                           Py_ssize_t indent_len, Py_ssize_t *out_len);

/* The minification transforms serialize(minify=...) toggles, each round-trip safe
   (the minified bytes reparse to the same tree). */
typedef struct {
    int collapse_whitespace; /* fold ASCII-whitespace runs to a single space outside pre/textarea/listing */
    int omit_optional_tags;  /* drop the start/end tags the WHATWG optional-tag rules allow */
    int unquote_attributes;  /* drop redundant attribute quotes and write empty values as bare names */
    int strip_comments;      /* skip comment nodes */
    int minify_js;           /* minify inline <script> JavaScript (a parse failure falls back to verbatim) */
    int minify_js_fold;      /* run the JS constant-folding / dead-code pass */
    int minify_js_mangle;    /* run the JS identifier-renaming pass */
} th_minify_opts;

/* Serialize node and its subtree minified under minify and the output options.
   PyMem-allocated; *out_len receives the length. NULL on failure. */
Py_UCS4 *th_node_minify(th_tree *tree, th_node *node, const th_minify_opts *minify, const th_serialize_opts *opts,
                        Py_ssize_t *out_len);

/* The Markdown export configuration, a union of the markdownify and html2text
   knobs with one name per concept. The Python binding fills it from keyword
   arguments starting from th_markdown_default_opts(). String markers are
   borrowed ASCII whose argument objects the binding keeps alive for the call. */
enum th_md_heading { TH_MD_HEADING_ATX, TH_MD_HEADING_ATX_CLOSED, TH_MD_HEADING_SETEXT };
enum th_md_code { TH_MD_CODE_FENCED, TH_MD_CODE_INDENTED };
enum th_md_link { TH_MD_LINK_INLINE, TH_MD_LINK_REFERENCE };
enum th_md_image { TH_MD_IMAGE_MARKDOWN, TH_MD_IMAGE_ALT, TH_MD_IMAGE_IGNORE, TH_MD_IMAGE_HTML };
enum th_md_table { TH_MD_TABLE_MARKDOWN, TH_MD_TABLE_STRIP, TH_MD_TABLE_HTML };
enum th_md_escape { TH_MD_ESCAPE_MINIMAL, TH_MD_ESCAPE_ALL };
enum th_md_break { TH_MD_BREAK_SPACES, TH_MD_BREAK_BACKSLASH };
enum th_md_doc_strip { TH_MD_DOC_STRIP, TH_MD_DOC_LSTRIP, TH_MD_DOC_RSTRIP, TH_MD_DOC_NONE };
enum th_md_table_header { TH_MD_HEADER_FIRST, TH_MD_HEADER_DETECT, TH_MD_HEADER_NONE };
/* No filter, a strip denylist (listed tags lose their markup), or a convert
   allowlist (only listed tags keep their markup). */
enum th_md_filter { TH_MD_FILTER_NONE, TH_MD_FILTER_STRIP, TH_MD_FILTER_CONVERT };

/* Words in the tag-filter bitset: 256 bits cover every interned tag atom (there
   are ~150, all < 256) with headroom, so a set membership test needs no bound. */
#define TH_MD_FILTER_WORDS 4

typedef struct {
    int heading_style;          /* enum th_md_heading */
    const char *bullets;        /* unordered markers cycled by depth, e.g. "-*+" */
    const char *strong;         /* bold wrapper, e.g. "**" */
    const char *emphasis;       /* italic wrapper, e.g. "*" */
    const char *strikethrough;  /* del/s wrapper, e.g. "~~" */
    int keep_emphasis;          /* 0 strips bold/italic/strike markup, emitting text only */
    int keep_strikethrough;     /* 0 hides del/s content entirely */
    int code_block_style;       /* enum th_md_code */
    const char *code_language;  /* default fence language when none is on the element */
    const char *code_mark_open; /* non-NULL wraps code blocks, e.g. "[code]" */
    const char *code_mark_close;
    int link_style;          /* enum th_md_link */
    int autolink;            /* emit <url> when the text equals an absolute href */
    int link_title;          /* 1 uses the href as the title when none is given */
    int ignore_links;        /* emit link text only, no markup */
    int skip_internal_links; /* drop href="#..." fragment links */
    int image_mode;          /* enum th_md_image */
    const char *default_image_alt;
    const char *base_url;   /* prefix resolved onto a relative link/image href */
    int table_mode;         /* enum th_md_table */
    int table_header;       /* enum th_md_table_header */
    int pad_tables;         /* align columns to a common width */
    const char *quote_open; /* <q> wrappers */
    const char *quote_close;
    int escape_mode; /* enum th_md_escape */
    int escape_asterisks;
    int escape_underscores;
    int line_break;                           /* enum th_md_break */
    int block_spacing_single;                 /* one newline between blocks instead of a blank line */
    int wrap_width;                           /* word-wrap column, 0 disables */
    int wrap_list_items;                      /* extend word wrapping into list-item text */
    int wrap_links;                           /* 0 keeps a link/image construct unbroken across a wrap */
    int transliterate;                        /* fold common non-ASCII typography in prose to ASCII */
    int document_strip;                       /* enum th_md_doc_strip */
    const char *sub;                          /* <sub> wrapper */
    const char *sup;                          /* <sup> wrapper */
    int google_doc;                           /* read inline-CSS styling the way a Google Docs export encodes it */
    int google_list_indent;                   /* px of margin-left per list-nesting level (>= 1); divides margin-left */
    int hide_strikethrough;                   /* in google_doc mode, drop text a CSS line-through struck */
    int tag_filter;                           /* enum th_md_filter selecting how filter_tags reads */
    uint64_t filter_tags[TH_MD_FILTER_WORDS]; /* atoms named by strip (denylist) or convert (allowlist) */
    /* Per-tag converter hook: a registered tag's built-in rendering is replaced by a
       Python callable receiving the element and its rendered child Markdown. The
       engine builds the element through wrap_node so it need not know the binding. */
    PyObject *converters;                                       /* borrowed dict {tag name: callable}; NULL disables */
    PyObject *(*wrap_node)(void *wrap_node_ctx, th_node *node); /* build an Element for a node; new ref or NULL */
    void *wrap_node_ctx;                                        /* opaque, handed to wrap_node */
} md_opts;

/* The no-argument baseline configuration (opinionated GitHub-Flavored Markdown). */
md_opts th_markdown_default_opts(void);

/* Render node and its subtree as Markdown under opt: a block-aware tree walk
   emitting headings, lists, links, emphasis, code, blockquotes, images and pipe
   tables with normal-flow whitespace collapsing. PyMem-allocated; *out_len
   receives the length. NULL on allocation failure. */
Py_UCS4 *th_node_markdown(th_tree *tree, th_node *node, const md_opts *opt, Py_ssize_t *out_len);

/* The layout-aware text export configuration (the inscriptis role): a plain-text
   rendering that keeps the visual structure, with column-aligned tables. */
enum th_text_links { TH_TEXT_LINKS_NONE, TH_TEXT_LINKS_INLINE, TH_TEXT_LINKS_FOOTNOTE };

typedef struct {
    int width;    /* word-wrap column, 0 disables */
    int links;    /* enum th_text_links */
    int images;   /* render an image as its alt text */
    int extended; /* the relaxed CSS profile (div padding, span spacing) vs strict */
    const char *default_image_alt;
    const char *cell_separator; /* between table columns, e.g. "  " */
    const char *bullet;         /* unordered list marker, e.g. "* " */
} text_opts;

/* The no-argument baseline (extended profile, links hidden, images off). */
text_opts th_text_default_opts(void);

/* Render node and its subtree as layout-aware plain text under opt: blocks
   separated by blank lines, lists indented under their bullets, and tables laid
   out as a column-aligned grid. PyMem-allocated; *out_len receives the length.
   NULL on allocation failure. */
Py_UCS4 *th_node_layout_text(th_tree *tree, th_node *node, const text_opts *opt, Py_ssize_t *out_len);

/* The dominant content element under root by the readability content-density
   heuristic (text length, comma count, tag and class/id weight, discounted by link
   density), or NULL when nothing scores as content. Pure C: the caller holds the
   per-tree critical section and wraps the node (or renders its text with
   th_node_layout_text) afterwards. The engine for Node.main_content()/main_text(). */
th_node *th_node_main_content(th_tree *tree, th_node *root);

/* The page-level article metadata Node.article() returns beside the content body:
   each field is a freshly PyMem-allocated, whitespace-normalized code-point buffer
   (NULL with a zero length when the source is absent), harvested in pure C from the
   document the content sits in -- title from <h1> / og:title / <title>, byline from
   a rel=author link / meta author / article:author, date from <time> / article:
   published_time / a common date meta, description from og:description / meta
   description, and lang from <html lang>. The caller holds the per-tree critical
   section, materializes each buffer into a str (or None), then th_article_meta_clear
   frees them. */
typedef struct {
    Py_UCS4 *title;
    Py_ssize_t title_len;
    Py_UCS4 *byline;
    Py_ssize_t byline_len;
    Py_UCS4 *date;
    Py_ssize_t date_len;
    Py_UCS4 *description;
    Py_ssize_t description_len;
    Py_UCS4 *lang;
    Py_ssize_t lang_len;
} th_article_meta;

/* Fill meta by harvesting the article metadata fields from root (the document the
   content body belongs to). Pure C; the caller holds the per-tree critical section. */
void th_article_metadata(th_tree *tree, th_node *root, th_article_meta *meta);

/* Free every buffer th_article_metadata allocated into meta. */
void th_article_meta_clear(th_article_meta *meta);

/* One annotation rule (the inscriptis annotation_rules surface): match an element
   by tag and, optionally, by an attribute whose value contains a token, and
   attach its labels to the element's text span. any_tag matches every tag (the
   "#attr" form); attr/value NULL means no attribute condition. labels is a
   borrowed tuple of str the binding keeps alive for the call. */
typedef struct {
    uint16_t tag_atom;
    int any_tag;
    const char *attr;
    Py_ssize_t attr_len;
    const Py_UCS4 *value;
    Py_ssize_t value_len;
    PyObject *labels;
} text_rule;

/* A labeled span of the rendered text: code-point offsets [start, end) and the
   label (a borrowed str). */
typedef struct {
    Py_ssize_t start;
    Py_ssize_t end;
    PyObject *label;
} text_span;

/* Render node as layout text (as th_node_layout_text) while recording, for every
   element matching a rule, a labeled span over its rendered text. *out_spans
   receives a PyMem-allocated array of *out_span_count spans (offsets into the
   returned text); the caller frees it. Spans inside table cells are not recorded.
   NULL on allocation failure. */
Py_UCS4 *th_node_annotated_text(th_tree *tree, th_node *node, const text_opts *opt, const text_rule *rules,
                                Py_ssize_t n_rules, text_span **out_spans, Py_ssize_t *out_span_count,
                                Py_ssize_t *out_len);

/* The doctype's public and system identifiers as slices of the node's own text;
   returns 1 with the four out params set when present, 0 for a name-only doctype. */
int th_node_doctype_ids(th_node *node, const Py_UCS4 **public_id, Py_ssize_t *public_len, const Py_UCS4 **system_id,
                        Py_ssize_t *system_len);

/* The source position where a parsed element's start tag began: 1-based line and
   0-based column, the same convention th_token and html.parser's getpos use.
   Returns 1 with *line and *col set for an element that carries a position, or 0 when
   the tree was parsed without positions, the node is not an element, or it is a
   synthetic element (implied html/head/body, a fragment root, or one constructed
   by hand) with no source. */
int th_node_source_position(th_tree *tree, th_node *node, Py_ssize_t *line, Py_ssize_t *col);

/* The interned name bytes (NUL-terminated UTF-8) for an attribute's name_atom;
   *out_len receives the length. Resolves a static atom from the generated table
   and a dynamic one from the tree's intern table. */
const char *th_attr_name(th_tree *tree, uint32_t name_atom, Py_ssize_t *out_len);

/* Resolve a query attribute name (UTF-8 bytes) to the atom an element in this
   tree would carry for it, or UINT32_MAX when no element has that name. */
uint32_t th_attr_lookup(th_tree *tree, const char *bytes, Py_ssize_t len);

/* Resolve a lowercased tag name (UTF-8 bytes) to its atom, or TH_TAG_UNKNOWN.
   bytes must point at one readable byte (len >= 1); the selector parser never
   forms an empty type name. */
uint16_t th_tag_lookup(const char *bytes, Py_ssize_t len);

/* The tree-construction category bitmask for an atom (0 for TH_TAG_UNKNOWN). */
uint8_t th_tag_flags(uint16_t atom);

/* Whether an atom names an HTML void element, which the spec forbids from having
   children. The serializer uses the static-inline is_void_atom on its hot path;
   this is the cold cross-TU entry the element constructor validates against. */
int th_tag_is_void(uint16_t atom);

#endif /* TURBOHTML_TREEBUILDER_H */
