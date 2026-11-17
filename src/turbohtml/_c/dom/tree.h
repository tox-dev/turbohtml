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

/* The deepest element nesting the DOM holds. The open-element stack is never capped, so the insertion modes and the
   end tags follow the spec at any depth; a node that would nest deeper attaches after its deepest allowed ancestor
   instead (cap_insertion_point), the way Blink caps its DOM at kMaximumHTMLParserDOMTreeDepth. */
#define TH_MAX_TREE_DEPTH 512

enum th_node_type {
    TH_NODE_DOCUMENT,
    TH_NODE_ELEMENT,
    TH_NODE_TEXT,
    TH_NODE_COMMENT,
    TH_NODE_DOCTYPE,
    TH_NODE_CONTENT, /* a <template>'s content document fragment */
    TH_NODE_PI,
    TH_NODE_CDATA, /* a CDATA section (construction only; the HTML parser folds foreign CDATA into text) */
};

/* A doctype node reuses its (element-only) tag_flags field to record which
   identifiers the source actually supplied. The serialized text writes an empty
   string for both a missing and a present-but-empty identifier, so these flags
   are the only way to tell `<!DOCTYPE x SYSTEM "s">` (public missing) from
   `<!DOCTYPE x PUBLIC "" "s">` (public present but empty). */
#define TH_DOCTYPE_HAS_PUBLIC 0x40u
#define TH_DOCTYPE_HAS_SYSTEM 0x80u

#define TH_PI_XML_INVALID 0x01u

/* An element node reuses a spare tag_flags bit (the category bits from the atom
   table only occupy 0x01..0x10) to record that the source actually closed it with
   an end tag, as opposed to the parser closing it implicitly or at EOF. The
   sanitizer's escape mode reads it to reproduce a disallowed element as visible
   text without fabricating a `</tag>` the author never wrote. An element built
   through the API has no source, so every non-void one carries it. */
#define TH_ELEM_CLOSED_BY_END_TAG 0x20u

/* The start-tag counterpart: an element the tree builder created without a source
   start tag (the implied html/head/body, the skeleton an empty document gets at EOF,
   a table's tbody/tr/colgroup, a cloned formatting element, and the p or br a stray
   end tag creates, which also carries TH_ELEM_CLOSED_BY_END_TAG). Bit 0x40 is free
   on elements because only doctype nodes read TH_DOCTYPE_HAS_PUBLIC. Escape mode
   reads it so it never renders a `<tag>` the author never wrote. */
#define TH_ELEM_IMPLIED 0x40u

/* Detached ElementTree views need a parent for their tail, hidden from element ancestry. */
#define TH_ELEM_TAIL_HOLDER 0x80u

/* A TH_NODE_CONTENT node reuses two otherwise-unused tag_flags bits to record that
   it is a shadow root (attach_shadow) rather than a template's content fragment.
   TH_SHADOW_ROOT marks the node so node_wrap types it as ShadowRoot; TH_SHADOW_CLOSED
   carries the mode (set is closed, clear is open). A content node the parser builds
   for a template leaves both clear, so it keeps wrapping as the bare Node. The bits
   are only ever read for a content node, where the element category and doctype flags
   never apply. */
#define TH_SHADOW_ROOT 0x01u
#define TH_SHADOW_CLOSED 0x02u

/* Two further content-node bits carry a declarative shadow root's flags, set by the
   tree builder from a `<template shadowrootmode>` element's shadowrootdelegatesfocus /
   shadowrootclonable attributes; both clear on a shadow root the mutation API creates. */
#define TH_SHADOW_DELEGATES_FOCUS 0x04u
#define TH_SHADOW_CLONABLE 0x08u

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
    uint8_t type;
    uint8_t attr_capacity_shift; /* zero for exact-sized arrays; read it through th_node_attr_capacity */
    uint16_t atom;               /* TH_TAG_* for elements, else TH_TAG_UNKNOWN */
    uint8_t tag_flags;           /* category bitmask from the atom table */
    uint8_t ns;                  /* enum th_ns: HTML / SVG / MathML */
    uint16_t binding_id;
    /* Per-tree creation sequence, stamped as each node is allocated (document order for
       a parsed tree). It is a stable node identity independent of the heap address, so
       the query engine can hash nodes (match sets, dispatch cache, node maps) into probe
       sequences that depend only on the input, not on where the allocator placed the
       tree. */
    uint32_t seq;
    uint8_t css_name_safety;
    /* MathML annotation-xml only: set by insert_foreign from the start tag's encoding attribute. Valid only during
       tree construction: clones and DOM attribute edits do not update it. */
    uint8_t html_integration;
    /* set once an enabled selectedcontent is reachable below this node, never cleared: the adoption agency only
       lists a moved subtree's targets with a select cache when that subtree sets it (th_node_mark_ancestors) */
    uint8_t reaches_selectedcontent;
    /* Tree builder state: set while the element is an entry of the list of active formatting elements. The list
       outlives the stack of open elements, so membership is read here instead of searched. Only the tree builder
       and the streaming rewriter write it. */
    uint8_t afe_listed : 1;
    /* Character data only: no other node reads this run of the arena buffer, so a Range edit may rewrite it in place.
       Unset until such an edit takes a private copy, because a parsed node can share its buffer (a select's
       selectedcontent mirror reuses the option's text). The bit fields share their byte with afe_listed, which only the
       tree builder writes; this keeps the node at 96 bytes. */
    uint8_t text_exclusive : 1;
    /* Text only: this text, written alone as a script's content, leaves a re-parse outside the script data double
       escaped state, so the serializer skips that scan (ser_end_script_double_escape). The tree builder sets it on
       the text of a script its end tag closed, the serializer after a scan under the tree's lock, and th_node_set_data,
       th_node_delete_data and normalize clear it. A whitespace collapse (clean/transforms.c) keeps it: it leaves one
       space of each run, and the scan's answer depends on whitespace only as a tag-name end and a break in a "-" run,
       which one space still is. */
    uint8_t script_text_closes : 1;
    /* An attribute value buffer of this element is also the value of another element: a clone takes its source's
       slots, values included, rather than copy them. A value is never written in place, and a deep copy into another
       tree looks these up in its th_copy_memo instead of copying each one again. */
    uint8_t attr_values_shared;
    /* Tree builder state: set once the element leaves the stack of open elements, which the streaming rewriter reads
       to follow a th_shadow. */
    uint8_t left_stack;
    /* Character data: log2 of a capacity the buffer at text has, zero when none is known. insert_text, th_node_set_data
       and normalize write within it in place, so a node that shares its buffer with another, or whose text no longer
       starts at its buffer, must keep it zero. */
    uint8_t text_capacity_shift;
    /* Element: whether its name is an XML QName, worked out by the first canonicalization that needs it: 0 not yet, 1
       yes, 2 no. A rename resets it. */
    uint8_t xml_name_wellformed;
    /* Valid while attr_values_shared: the seq of the element whose values the sharing started from, the same for
       every element sharing them, so the selector memo hashes a shared value alike from each (th_node_values_seq). */
    uint32_t values_seq;
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

_Static_assert(sizeof(void *) != 8 || sizeof(th_node) == 96, "a 64-bit parsed node is 96 bytes");

/* Set in attr_capacity_shift while dom/mutate.c keeps a hash index behind the attribute array. Bit 0x40 rather than
   0x80 lets th_node_attr_capacity mask it with 63, which x86-64 shifts apply for free. An indexed array has at least
   ATTR_INDEX_MIN slots, so the flag never sits on a zero shift and the zero test can read the byte unmasked. */
#define TH_ATTR_INDEXED 0x40

static inline size_t th_node_attr_capacity(const th_node *node) {
    const unsigned shift = node->attr_capacity_shift;
    return shift ? (size_t)1 << (shift & (TH_ATTR_INDEXED - 1)) : 0;
}

/* Mark src's attribute values as shared by a clone or copy, which takes values_seq from src afterwards. */
static inline void th_node_share_values(th_node *src) {
    if (!src->attr_values_shared) {
        src->values_seq = src->seq;
        src->attr_values_shared = 1;
    }
}

/* A sequence that every element pointing at the same attribute value buffers reports alike. */
static inline uint32_t th_node_values_seq(const th_node *node) {
    return node->attr_values_shared ? node->values_seq : node->seq;
}

/* The next node of a pre-order walk bounded to root's subtree, or NULL once the walk is done. */
static inline th_node *preorder_next(th_node *current, th_node *root) {
    if (current->first_child != NULL) {
        return current->first_child;
    }
    /* current can be NULL here if the cursor node was detached (extract()) mid-walk:
       its parent chain no longer reaches root, so end the walk instead of dereferencing. */
    while (current != NULL && current != root) {
        if (current->next_sibling != NULL) {
            return current->next_sibling;
        }
        current = current->parent;
    }
    return NULL;
}

/* Reject an unsafe recursive consumer before it allocates output, invokes callbacks, or mutates the tree. */
static inline int th_node_check_max_depth(th_node *root, Py_ssize_t limit, const char *operation) {
    th_node *node = root;
    Py_ssize_t depth = 0;
    for (;;) {
        if (depth >= limit) {
            PyErr_Format(PyExc_RecursionError, "%s does not support trees nested %zd levels or deeper", operation,
                         limit);
            return -1;
        }
        if (node->first_child != NULL) {
            node = node->first_child;
            depth++;
            continue;
        }
        while (node != root && node->next_sibling == NULL) {
            node = node->parent;
            depth--;
        }
        if (node == root) {
            return 0;
        }
        node = node->next_sibling;
    }
}

typedef struct th_tree th_tree;

/* One character-encoding declaration a <meta> element carried, recorded in document
   order while the tree is built. text is the charset attribute's value, or -- when
   from_content is set -- the whole http-equiv="content-type" content value, whose
   charset= token still has to be extracted. A <meta> that carries both records its
   charset value first. Both are the raw declaration: resolving a label to an encoding
   is dom/document.c's job, because only the first label that resolves decides, exactly
   as the prescan's fall-through does. */
typedef struct {
    const char *text;
    int from_content;
} th_meta_label;

/* The <meta> encoding declarations the parse saw, in document order, and how many.
   NULL with a zero count for a document that declares none. Owned by the tree, so
   read it before th_tree_free. */
const th_meta_label *th_tree_meta_labels(const th_tree *tree, Py_ssize_t *count);

/* One source span parse5's sourceCodeLocationInfo reports: 1-based start/end line,
   0-based start/end column, and the code-point start/end offset into the
   newline-normalized source. The half-open [start_offset, end_offset) covers the
   construct; line/col address its endpoints. */
typedef struct {
    Py_ssize_t start_line, start_col, start_offset;
    Py_ssize_t end_line, end_col, end_offset;
} th_src_span;

/* One attribute's source span, keyed by the interned name atom so it survives a
   later mutation of the element's attribute array. */
typedef struct {
    uint32_t name_atom;
    th_src_span span;
} th_src_attr;

/* The granular source location of a parsed element: the start-tag span, the
   end-tag span (has_end_tag 0 when the source never closed it -- a void, a
   self-closed, or an implicitly/EOF-closed element), and one span per start-tag
   attribute. Arena-allocated and hung off an element only when a parse requests
   source locations. */
typedef struct {
    th_src_span start_tag;
    th_src_span end_tag;
    int has_end_tag;
    /* the start tag ended in "/>", which closes a foreign element on the spot (an HTML element ignores it) */
    int self_closing;
    /* set once the mutation API changes the element's attributes, so the lossless
       serializer (th_node_serialize_source) rewrites the start tag from the current
       attributes instead of re-emitting its now-stale source bytes. */
    int start_dirty;
    /* set once the element is renamed, so a source-closed element gets a canonical
       end tag for its new name rather than the stale source bytes */
    int end_dirty;
    Py_ssize_t attr_count;
    th_src_attr *attrs;
} th_src_loc;

/* Parse a whole document. kind/data/length are a borrowed PyUnicode buffer that
   must outlive the returned tree (its slice text points into it). positions
   records each element's source line/column (read via th_node_source_position) at
   the cost of two trailing words per element; pass 0 to skip it. locations adds
   the granular start/end-tag and per-attribute spans (read via
   th_node_source_location) and implies positions. scripting is the WHATWG
   scripting flag: when set, noscript is a raw-text element (its content is raw
   text, serialized unescaped). declarative_shadow is the WHATWG "allow declarative
   shadow roots" flag: when set, a `<template shadowrootmode>` attaches a shadow root
   to its parent instead of building a normal template content fragment. Returns NULL
   only on allocation failure (no Python error is set). */
th_tree *th_tree_parse(int kind, const void *data, Py_ssize_t length, int positions, int locations, int scripting,
                       int declarative_shadow);

/* Parse a whole document under XML 1.0 well-formedness rather than the HTML tree
   builder: self-closing honored on every element, case-sensitive names, CDATA
   sections, processing instructions, the five predefined plus numeric entities,
   and namespace-prefix validation. kind/data/length are a borrowed PyUnicode
   buffer that must outlive the returned tree (text nodes span into it). The first
   well-formedness violation is recorded on the tree's error sink and parsing
   stops; a caller reads th_tree_errors and raises. Returns NULL only on
   allocation failure (no Python error is set). */
th_tree *th_tree_parse_xml(int kind, const void *data, Py_ssize_t length);

/* Every writer of a tag or attribute name shares one rule, so a name the DOM API refuses cannot reach the output
   through another API. Returns 0, or -1 with ValueError set. */
int th_validate_markup_name(PyObject *name, int is_attr);

/* Create an empty tree to own programmatically constructed nodes. Returns NULL on
   allocation failure (no Python error is set). */
th_tree *th_tree_new(void);

/* Whether the tree was parsed under XML rules, so name lookups are case-sensitive
   rather than folded to the HTML parser's lowercased names. */
int th_tree_is_xml(const th_tree *tree);
/* Whether an XML tree ever held an xmlns declaration. Without one every element is in no namespace, so a name test
   needs no namespace resolution. */
int th_tree_has_xml_ns(const th_tree *tree);
/* Whether the tree builder reads this attribute of an element in ns while building, so changing it can change the
   tree. Every other attribute only reaches the DOM. */
int th_builder_reads_attr(uint8_t ns, uint16_t tag, const char *name, Py_ssize_t len);

/* Carry the XML mode onto a tree built by copying nodes out of an XML one, so the
   copy keeps case-sensitive name lookups. */
void th_tree_set_xml(th_tree *tree, int xml);

/* Empty character data built or edited through the API points here rather than at NULL, so the find(text=)
   scan can memcpy every Text node without a per-node length check (memcpy takes no NULL even at length 0). */
extern const Py_UCS4 th_empty_text[1];

/* Construct a text/comment/doctype/cdata node (by enum th_node_type) owning a copy
   of the data code points in the tree's arena. NULL on allocation failure. */
th_node *th_tree_make_data_node(th_tree *tree, int type, const Py_UCS4 *data, Py_ssize_t len);

/* Construct an empty document-fragment node (a TH_NODE_CONTENT) in the tree's
   arena: the container the Range content operations fill. NULL on allocation
   failure. */
th_node *th_tree_make_fragment(th_tree *tree);

/* Materialize a character-data node's borrowed source span into an owned buffer in
   place and return node->text, so a caller can slice the code points directly. */
Py_UCS4 *th_node_realize_text(th_tree *tree, th_node *node);

/* Construct a processing-instruction node owning target and data (packed with the
   split point in attr_count). NULL on allocation failure. */
th_node *th_tree_make_pi(th_tree *tree, const Py_UCS4 *target, Py_ssize_t target_len, const Py_UCS4 *data,
                         Py_ssize_t data_len);

int th_pi_target_invalid_xml(const Py_UCS4 *target, Py_ssize_t target_len);

/* Rename an element in place: tag is the spelling to copy, or NULL for a known atom
   that points at its table entry. -1 on allocation failure, leaving it unchanged. */
int th_node_rename(th_tree *tree, th_node *node, const Py_UCS4 *tag, Py_ssize_t tag_len, uint16_t atom);

/* Construct an element owning a copy of the tag name, with attr_count empty
   attribute slots; fill each with th_tree_set_attr. NULL on allocation failure. */
th_node *th_tree_make_element(th_tree *tree, const Py_UCS4 *tag, Py_ssize_t tag_len, uint16_t atom,
                              Py_ssize_t attr_count);

/* Fill attribute slot index on a constructed element (has_value 0 means a
   valueless attribute). Returns 0, or -1 on allocation failure. */
int th_tree_set_attr(th_tree *tree, th_node *node, Py_ssize_t index, const char *name, Py_ssize_t name_len,
                     const Py_UCS4 *value, Py_ssize_t value_len, int has_value);

/* The name and the value of one constructed attribute slot, for a caller that reads a value only for the names
   it keeps. Each returns 0, or -1 on allocation failure. */
int th_tree_set_attr_name(th_tree *tree, th_node *node, Py_ssize_t index, const char *name, Py_ssize_t name_len);
int th_tree_set_attr_value(th_tree *tree, th_node *node, Py_ssize_t index, const Py_UCS4 *value, Py_ssize_t value_len,
                           int has_value);

/* An element with this many attributes or more folds its names and drops repeated ones through a sort
   (th_node_attr_mark_repeats), not a pairwise scan. */
#define TH_WIDE_ATTR_COUNT 32

/* Mark with TH_ATTR_UNKNOWN every attribute whose name atom repeats an earlier one, keeping the first of each.
   Returns 0, or -1 on allocation failure. th_node_attr_drop_marked then removes the marked attributes. */
int th_node_attr_mark_repeats(th_node *node);
void th_node_attr_drop_marked(th_node *node);

/* Build a complete HTML5 document skeleton in the (empty) tree: a document node
   holding a <!DOCTYPE html>, then <html> owning <head> and <body>. A non-NULL lang
   sets html's lang attribute; a non-NULL charset adds a <meta charset> as head's
   first child; a non-NULL title adds a <title> after it, carrying the text when
   title_len is positive (an empty title stays childless). The head and body
   elements are returned through out_head/out_body for the caller to fill; the
   document node is stored as tree->document and returned. NULL on allocation
   failure. */
th_node *th_tree_build_shell(th_tree *tree, const Py_UCS4 *lang, Py_ssize_t lang_len, const Py_UCS4 *title,
                             Py_ssize_t title_len, const Py_UCS4 *charset, Py_ssize_t charset_len, th_node **out_head,
                             th_node **out_body);

/* Upsert an attribute by name (replace value or append a slot); has_value 0 makes
   it valueless. Returns 0, or -1 on allocation failure. */
int th_node_attr_set(th_tree *tree, th_node *node, const char *name, Py_ssize_t name_len, const Py_UCS4 *value,
                     Py_ssize_t value_len, int has_value);
/* th_node_attr_set for a value that is already the value of another element's attribute in this tree's arena: the
   slot borrows that buffer instead of holding a copy, as the reconstructions of one start tag do (node_clone). A value
   is never written in place and the arena keeps it until the tree is freed, so the holders need no bookkeeping. */
int th_node_attr_set_shared(th_tree *tree, th_node *node, const char *name, Py_ssize_t name_len, const Py_UCS4 *value,
                            Py_ssize_t value_len);
/* The caller has already established that this attribute name is absent. */
int th_node_attr_append(th_tree *tree, th_node *node, const char *name, Py_ssize_t name_len, const Py_UCS4 *value,
                        Py_ssize_t value_len);

/* An element's attribute array and count; (NULL, 0) for every non-element node. */
static inline Py_ssize_t th_node_attributes(const th_node *node, th_node_attr **attrs) {
    if (node->type == TH_NODE_ELEMENT) {
        *attrs = node->attrs;
        return node->attr_count;
    }
    *attrs = NULL;
    return 0;
}

/* The index of node's attribute named `name` (UTF-8, caller-lowercased), or -1;
   always -1 for a non-element node, which has no attribute storage to index.
   Foreign elements can store a case-adjusted name (e.g. MathML definitionURL), so
   a missed atom match falls back to a case-insensitive scan for non-HTML nodes. */
Py_ssize_t th_node_attr_find(th_tree *tree, th_node *node, const char *name, Py_ssize_t name_len);
/* An element with this many attributes keeps a hash index of their names behind the array (dom/mutate.c). */
#define TH_ATTR_INDEX_MIN 32
/* The index of the attribute whose name_atom is exactly atom on an element with TH_ATTR_INDEX_MIN or more attributes,
   or -1; -2 on allocation failure. It answers from the element's attribute index, built here when the element has
   none, so repeated lookups on one element do not rescan its array. */
Py_ssize_t th_node_attr_find_atom(th_tree *tree, th_node *node, uint32_t atom);

/* Remove the named attribute; returns 1 if one was removed, else 0. */
int th_node_attr_del(th_tree *tree, th_node *node, const char *name, Py_ssize_t name_len);
/* Remove every attribute of an element, counted as an attribute write that may drop an id. The slot cache is the
   caller's to drop, as handle_drop_slots does. */
void th_node_attrs_clear(th_tree *tree, th_node *node);
/* Remove the attribute at index (0 <= index < attr_count). A caller that already holds the position skips the name
   lookup th_node_attr_del() pays. */
void th_node_attr_del_at(th_tree *tree, th_node *node, Py_ssize_t index);

/* Replace a node's character data with a copy of len code points. Returns 0, or -1
   with MemoryError set on allocation failure. */
int th_node_set_data(th_tree *tree, th_node *node, const Py_UCS4 *data, Py_ssize_t len);

/* Remove the [from, to) code-point run from a node's character data, storing none of the text
   that stays once the node has a buffer of its own. Returns 0, or -1 with MemoryError set on
   allocation failure. */
int th_node_delete_data(th_tree *tree, th_node *node, Py_ssize_t from, Py_ssize_t to);

/* Structural-edit primitives. remove detaches a node from its parent; append and
   insert_before link it (ref==NULL appends); contains reports whether ancestor is
   node or one of its ancestors; copy_node deep-copies a subtree into another tree
   (NULL on allocation failure). remove skips the DOM NodeIterator pre-removing steps,
   so it is for nodes no caller holds an iterator on (a parse, sanitize, schema or
   XSLT result tree, or a fragment built in the same call) and for XSLT's whitespace
   strip, which puts the nodes back before it returns; remove_silent runs the steps
   without queuing a mutation record. A link that can nest a node deeper than before
   needs th_tree_note_deepened. */
void th_node_remove(th_node *child);
void th_node_remove_silent(th_tree *tree, th_node *child);
void th_node_append_child(th_node *parent, th_node *child);
void th_node_insert_before(th_node *parent, th_node *child, th_node *ref);

/* The same three edits, but each queues a childList mutation record for the tree's
   MutationObservers (dom/observe.c). The binding layer routes user-driven structural
   edits through these so observation covers the public Node/Element mutation API. */
void th_node_remove_observed(th_tree *tree, th_node *child);
void th_node_append_child_observed(th_tree *tree, th_node *parent, th_node *child);
void th_node_insert_before_observed(th_tree *tree, th_node *parent, th_node *child, th_node *ref);

/* The DOM live-range update rules (dom/range.c), run by the edits above and by normalize while the tree has a live
   Range. replace_data moves the boundaries of node after its data changes count code points at offset by data_len;
   removed and inserted update the boundaries of a child that was just unlinked or linked; split moves the boundaries
   past offset of node to tail, the new node after it; merged moves the boundaries of a text node normalize folds
   into into, which starts at offset. */
struct th_live_range;
int th_tree_has_live_ranges(const th_tree *tree);
struct th_live_range **th_tree_live_ranges_ptr(th_tree *tree);
void th_node_insert_before_live(th_tree *tree, th_node *parent, th_node *child, th_node *ref);
void th_live_replace_data(th_tree *tree, th_node *node, Py_ssize_t offset, Py_ssize_t count, Py_ssize_t data_len);
void th_live_removed(th_tree *tree, th_node *child);
void th_live_inserted(th_tree *tree, th_node *child);
void th_live_split(th_tree *tree, th_node *node, Py_ssize_t offset, th_node *tail);
void th_live_merged(th_tree *tree, th_node *part, th_node *into, Py_ssize_t offset);

/* The MutationObserver registry (dom/observe.c) lives in the tree's fields, but that
   engine cannot include the tree-internal header (its Node bindings clash with the
   tree-builder's link inlines), so it reaches the registry through these accessors.
   th_tree_has_observers gates the record-capture work on the mutation hot path, and
   th_tree_has_iterators lets a removal loop skip the NodeIterator steps. */
struct th_mo_registry;
int th_tree_has_observers(const th_tree *tree);
int th_tree_has_iterators(const th_tree *tree);
struct th_mo_registry **th_tree_observers_ptr(th_tree *tree);

int th_node_contains(th_tree *tree, th_node *ancestor, th_node *node);

/* A NodeIterator's two node pointers (DOM "node pointer": a node plus its pointer-before flag), registered on the
   iterator's tree so every removal through the observed mutation API runs the DOM NodeIterator pre-remove steps on
   them. candidate is the traverse step's cursor and NULL outside a traverse. */
typedef struct th_node_iterator {
    th_node *root;
    th_node *reference;
    th_node *candidate;
    int reference_before;
    int candidate_before;
} th_node_iterator;

/* Register or unregister a live NodeIterator; the caller holds the tree's critical section. Registering returns
   -1 on allocation failure. */
int th_tree_add_node_iterator(th_tree *tree, th_node_iterator *iterator);
void th_tree_remove_node_iterator(th_tree *tree, th_node_iterator *iterator);
/* The DOM "ensure pre-insertion validity" hierarchy rules (steps 4-6, and the replace variant): may nodes (count of
   them, gathered into one fragment when there are several) go into parent before child (NULL appends), or in place of
   the sibling run [run_first, run_last] when run_first is not NULL? A doctype belongs only under a Document, which
   holds no Text, at most one element and one doctype, the doctype first. Returns NULL when valid, else the reason. */
const char *th_pre_insert_error(th_node *parent, th_node *const *nodes, Py_ssize_t count, th_node *child,
                                th_node *run_first, th_node *run_last);
th_node *th_tree_copy_node(th_tree *dest, th_tree *src, th_node *src_node);
/* The shared attribute values (th_node.attr_values_shared) a deep copy into one tree has copied so far, keyed by the
   source tree's identity and the value's address in its arena, so that a value the source elements share is copied
   once and shared by the copies. th_tree_copy_node keeps one per call. A caller that copies sibling subtrees one by
   one zero-initializes one and passes it to each th_tree_copy_node_sharing or th_tree_adopt_copy_sharing call, and
   releases it with th_copy_memo_free. A memo that outlives a call (the destination handle's) sets source before each
   copy to an identity no other source has had: a freed source's addresses can come back in a later tree, and only the
   identity tells them apart. */
typedef struct {
    struct th_copied_value *entries; /* open addressing; a NULL source address marks a free entry */
    size_t capacity;
    size_t count;
    uint64_t source; /* the identity of the tree the copies are made from */
} th_copy_memo;
th_node *th_tree_copy_node_sharing(th_tree *dest, th_tree *src, th_node *src_node, th_copy_memo *memo);
static inline void th_copy_memo_free(th_copy_memo *memo) {
    PyMem_Free(memo->entries);
}
int th_tree_has_shadows(const th_tree *tree);
th_node *th_node_next_including_shadow(th_tree *tree, th_node *node, th_node *root);
/* A deep copy for a node the DOM moves into dest (adoption): between an XML and an HTML tree the copied elements also
   take dest's naming rules, see convert_element_kind. */
th_node *th_tree_adopt_copy_sharing(th_tree *dest, th_tree *src, th_node *src_node, th_copy_memo *memo);
/* How many nodes the tree's arena has ever held, linked or not: an upper bound on what adopting all of it copies. */
uint32_t th_tree_node_count(const th_tree *tree);
/* Whether node_seq has wrapped, after which two nodes of the tree may share a seq. */
int th_tree_seq_wrapped(const th_tree *tree);
th_node *th_tree_copy_node_shallow(th_tree *dest, th_tree *src, th_node *src_node);
th_tree *th_tree_copy_document(th_tree *src);
/* A fresh empty tree rooted at a node of type (TH_NODE_DOCUMENT, or TH_NODE_CONTENT for a fragment), with the given
   XML flag and quirks mode, for rebuilding a pickled document or fragment. NULL on allocation failure. */
th_tree *th_tree_new_rooted(enum th_node_type type, int xml, int quirks);
/* Make document, a parentless node of tree, the root th_tree_document returns, or leave the tree without one when it
   is NULL: a Document copied into an empty tree is recorded this way (the parsers and th_tree_copy_document set it
   themselves), rebuilding a pickle roots a fragment in the tree its largest child was built in, and un-roots that
   tree when the fragment turns out to be a template's contents. */
void th_tree_set_document(th_tree *tree, th_node *document);

/* Whether two subtrees are structurally equal: same node type, and for an element the
   same namespace, tag name, and attribute set (names + values, order-independent) with
   the same ordered children; for a leaf the same character data.
   The two nodes may live in different trees. Pure structure -- it never touches node
   identity (the `==` operator's domain). Returns -1 when realizing a zero-copy text
   span fails. */
int th_node_equals(th_tree *left_tree, th_node *left, th_tree *right_tree, th_node *right);

/* DOM normalize over a subtree: merge each run of adjacent Text children into one node and drop empty Text nodes.
   Returns 0, or -1 on allocation failure. */
int th_node_normalize(th_tree *tree, th_node *root);

/* Parse an HTML fragment as if set as the innerHTML of the given context element
   (e.g. "td", or "svg path"). The returned tree serializes the context root's
   children. context is a NUL-free ASCII name; context_len its length. positions
   records element source line/column, scripting sets the WHATWG scripting flag, and
   declarative_shadow allows `<template shadowrootmode>` to attach a shadow root to the
   context element, all as in th_tree_parse. */
th_tree *th_tree_parse_fragment(int kind, const void *data, Py_ssize_t length, const char *context,
                                Py_ssize_t context_len, int positions, int locations, int scripting,
                                int declarative_shadow);

/* As th_tree_parse_fragment, for a context element that is an HTML integration point: html_integration makes the
   fragment root one, the way insert_foreign marks a parsed annotation-xml. Pass th_annotation_xml_is_html of the
   context node. SVG and MathML text integration points need no flag, since the root takes their namespace and tag. */
th_tree *th_tree_parse_fragment_ex(int kind, const void *data, Py_ssize_t length, const char *context,
                                   Py_ssize_t context_len, int positions, int locations, int scripting,
                                   int declarative_shadow, int html_integration);

/* Whether node is a MathML annotation-xml whose current encoding attribute makes it an HTML integration point, decided
   as insert_foreign decides a parsed start tag. */
int th_annotation_xml_is_html(const th_node *node);

/* Whether context names a real element the public parse_fragment() accepts: a known
   HTML tag, or any explicitly namespaced (svg/math) foreign element. */
int th_tree_fragment_context_known(const char *context, Py_ssize_t context_len);

void th_tree_free(th_tree *tree);

/* A push parser driving the tokenizer and tree builder incrementally, so a
   document can be fed in chunks without ever holding the whole source: the
   tokenizer reclaims its consumed input on each feed and the insertion-mode
   state persists across feeds. */
typedef struct th_stream th_stream;

/* Create an empty push parser. positions records element source line/column and
   locations adds the granular source spans, both as in th_tree_parse. Returns NULL
   on allocation failure (no Python error is set). */
th_stream *th_stream_new(int positions, int locations);

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

/* Tree construction that follows a tokenizer someone else drives (the streaming rewriter): it switches that
   tokenizer's content model and CDATA handling exactly as a document parse with scripting on does. */
typedef struct th_shadow th_shadow;

/* An empty tree that follows sm's input from its start. sm's input must be complete and stay in place while the
   shadow lives. Returns NULL on allocation failure. */
th_shadow *th_shadow_new(th_tokenizer *sm);

/* Feed the tree the input from where it stopped up to `offset`, where a tag starts, through a tokenizer of its own;
   the tokens after it come from sm through th_shadow_step. refs are as for th_shadow_compact. Returns 0, or -1 on
   allocation failure. */
int th_shadow_catch_up(th_shadow *shadow, Py_ssize_t offset, th_node **refs, Py_ssize_t count, size_t stride);

/* Run the token sm just produced through tree construction, then set sm up for the next one. A TH_CHARREF counts as
   its decoded text. Returns 0, or -1 on allocation failure. */
int th_shadow_step(th_shadow *shadow, th_token *token);

/* Whether the token th_shadow_step read last built a node. A start tag the tree builder ignores builds none, while a
   void element it inserts and closes at once builds one that is no longer open. */
int th_shadow_built(const th_shadow *shadow);

/* Whether the token th_shadow_step read last detached the body, which a frameset does when it replaces one. */
int th_shadow_discarded(const th_shadow *shadow);

/* Whether the tree has opened a body, and if so, stores the line and column of the token that opened it. */
int th_shadow_body(const th_shadow *shadow, Py_ssize_t *line, Py_ssize_t *col);

/* th_shadow_catch_up, but the replay stops once a body is open, the shadow parked after the token that opened it.
   Returns 1 when it stopped there, 0 when it reached offset without one, or -1 on allocation failure. */
int th_shadow_catch_body(th_shadow *shadow, Py_ssize_t offset, th_node **refs, Py_ssize_t count, size_t stride);

/* Once the tree has grown well past what tree construction can still reach, replace it by a copy of that part, so
   the shadow holds memory for the open elements and active formatting elements, not for the document. Each of the
   `count` element pointers at refs, `stride` bytes apart, moves to its copy, or to a stand-in that has left the
   stack. Element pointers held anywhere else are invalid afterwards. Returns 0, or -1 on allocation failure. */
int th_shadow_compact(th_shadow *shadow, th_node **refs, Py_ssize_t count, size_t stride);

/* Whether the insertion mode reads the start tags that follow as HTML does outside templates and framesets, with
   the tokenizer in its data state: a consumer can then go on alone until it meets a tag it cannot read. */
int th_shadow_parkable(const th_shadow *shadow);

/* Stop following sm after the tag `token`; th_shadow_catch_up resumes from its end. */
void th_shadow_park(th_shadow *shadow, const th_token *token);

/* The stack of open elements, outermost first; *count receives its length. */
th_node *const *th_shadow_open(const th_shadow *shadow, Py_ssize_t *count);

/* Free the shadow and its tree; NULL is a no-op. */
void th_shadow_free(th_shadow *shadow);

/* Tree construction over markup handed to it piece by piece through a tokenizer of its own, as a parse of the
   markup's concatenation reads it: the streaming rewriter feeds it what it writes, to learn how a browser reads the
   output so far. Compacts and frees as th_shadow_compact and th_shadow_free. Returns NULL on allocation failure. */
th_shadow *th_shadow_mirror_new(void);

/* Read the next length code points; the tokens they complete are built at once, a token they leave open waits for
   the next feed. With count >= 0 the tree may be compacted on the way, moving refs as th_shadow_compact does; with
   count < 0 every element pointer stays valid. Returns 0, or -1 on allocation failure. */
int th_shadow_mirror_feed(th_shadow *mirror, const Py_UCS4 *data, Py_ssize_t length, th_node **refs, Py_ssize_t count,
                          size_t stride);

/* The content model the mirror reads its next code point in (see th_tok_content_model). */
enum th_initial_state th_shadow_mirror_model(const th_shadow *mirror, const th_buf **end_tag);

/* Whether the current node is an SVG/MathML element, where "<![CDATA[" opens a section. */
int th_shadow_foreign(const th_shadow *shadow);

/* Serialize the tree in the html5lib tree-construction "#document" format (one
   "| " indented line per node) into a freshly PyMem-allocated UCS4 buffer;
   *out_len receives the length. Returns NULL on allocation failure. Used by the
   conformance harness to diff against the .dat expectations. */
Py_UCS4 *th_tree_serialize(th_tree *tree, Py_ssize_t *out_len);

/* The document (root) node; its children are the doctype/comments and <html>.
   For a fragment the children of the context root are exposed instead. */
th_node *th_tree_document(th_tree *tree);

/* The count of dynamic attribute names interned into this tree's table. It only
   grows, never shrinks, so a change signals that a name a compiled selector once
   resolved as absent may now exist: a monotonic generation for invalidating a
   cached compiled selector whose attribute atoms were resolved against the tree. */
uint32_t th_tree_attr_generation(const th_tree *tree);
uint64_t th_tree_attr_version(const th_tree *tree);
/* Moves on every write or delete of an id attribute and on every edit that can add, drop or reorder the tree's
   elements, so a cache of the tree's elements by id holds while it stands. */
uint64_t th_tree_id_version(const th_tree *tree);
/* Bump th_tree_id_version for an edit to the tree's elements. Every structural mutator calls it through
   handle_drop_index, and nothing else does, so the id version runs ahead of th_tree_attr_version by at most the
   handle's mutation_version. It shares the id counter because th_tree has no room for another one within pymalloc's
   512-byte small-object limit, and leaves the attribute version alone so the caches keyed on it, such as lang()'s,
   outlive a structural edit. */
void th_tree_note_element_edit(th_tree *tree);
/* Whether the handle's slot cache still matches the slot attributes: set when the cache starts, cleared by an attribute
   edit that changes slot assignment (see th_tree.slot_cache_valid). */
int th_tree_slot_cache_valid(const th_tree *tree);
void th_tree_set_slot_cache(th_tree *tree, int valid);

/* How many times a node has left its parent, in any tree of the process. Every other link only gives a parentless
   node a parent, which keeps each node's ancestors its ancestors, so while the count stands a node's root at some
   point is still one of its inclusive ancestors. */
uint64_t th_tree_unlinks(void);

/* The WHATWG parse errors collected during the parse, in document order, and
   their count via *out_count. The array (and its static code strings) lives as
   long as the tree; it is empty for a programmatically built or well-formed tree. */
/* Fold in the parse errors the input's own code points raise, which th_tree_parse leaves for
   the first reader. The source is passed in rather than read off the tree: th_tree_parse hands
   tree->data to the tokenizer's own buffer, which does not outlive the parse. Idempotent; call
   before th_tree_errors. Returns -1 on allocation failure, leaving the errors as they were. */
int th_tree_ensure_input_errors(th_tree *tree, int kind, const void *data, Py_ssize_t length);

const th_parse_error *th_tree_errors(const th_tree *tree, Py_ssize_t *out_count);

/* Whether the tree was parsed in quirks mode (no doctype or a quirky one). In
   quirks mode CSS class and ID selectors match ASCII case-insensitively
   (Selectors-4 §6.1/§6.2); programmatic trees default to no-quirks. */
int th_tree_quirks(const th_tree *tree);
void th_tree_set_quirks(th_tree *tree, int quirks);

/* Whether a doctype node written back as its name and identifiers alone puts a
   document in quirks mode. A parse can also force quirks through malformed doctype
   syntax the node does not record, so th_tree_quirks can hold where this does not. */
int th_doctype_is_quirky(th_node *node);

/* Whether the parse took a branch whose result its serialization can re-parse differently: a start tag a table mode
   foster-parents, the adoption agency's furthest-block repair, reconstructing an a or nobr, a form left open after its
   pointer cleared, a foreign <html>, <plaintext>, a list-item scan barrier popped with an open p, a template closed
   over open elements, or a hidden input kept in a table (it stays only while its type attribute does). Without it,
   serialize-then-parse rebuilds the tree (measured on 8M fuzzed inputs), so the sanitizer re-checks only its own
   edits; a tree not built by the parser reports 0 and gets the whole-tree pass from its caller. */
int th_tree_reparse_hazard(const th_tree *tree);
/* Whether the depth cap moved an inserted node, so the parsed tree is flatter than an uncapped parse of its markup. */
int th_tree_depth_capped(const th_tree *tree);

/* Whether the tree was parsed with the WHATWG scripting flag on (noscript is a
   raw-text element); programmatic trees default to off. innerHTML fragment parsing
   reads it so a scripting-parsed subtree keeps building noscript as raw text. */
int th_tree_scripting(const th_tree *tree);

/* The descent below a :has() anchor at which the subtree memo starts to pay. */
#define TH_HAS_MEMO_DEPTH 24
/* Set in the nest bound while the tree may have a MutationObserver, so an observed insertion takes one test to know
   whether it must queue a record or drop a bound. Alone it is below TH_HAS_MEMO_DEPTH, so it bounds nothing. */
#define TH_NEST_OBSERVED 1
/* More ancestors than any element the HTML tree builder makes can have: its open-element stack stops at
   TH_MAX_TREE_DEPTH, and an element it does not push sits one level below the deepest. */
#define TH_DEEP_NEST (TH_MAX_TREE_DEPTH + 2)
/* Set in the nest bound while no element of the tree has TH_DEEP_NEST ancestors, so an ancestor walk on it is as short
   as on any parsed page and the selector matcher runs without its ancestor memo. Alone it is below TH_HAS_MEMO_DEPTH,
   so it bounds nothing for :has(). */
#define TH_NEST_CAPPED 2
/* A depth no element of the tree reaches, known in O(1), or a value below TH_HAS_MEMO_DEPTH when none is known. Only
   a document the HTML tree builder made whose open-element stack stayed below TH_HAS_MEMO_DEPTH, and that nothing has
   deepened since, has one (TH_HAS_MEMO_DEPTH, plus TH_NEST_CAPPED, plus TH_NEST_OBSERVED once observed). Any other
   tree may nest deeper. A :has() query skips the subtree memo on a tree bounded below the memo depth, where the direct
   walk is cheap. TH_NEST_CAPPED stays set on a document the HTML tree builder made, on a parse_xml() document whose
   open-element stack stayed within TH_MAX_TREE_DEPTH, and on either after an edit that linked a node, or a subtree of a
   few nodes, whose elements stay short of TH_DEEP_NEST ancestors; a parsed fragment, Element(), a copy, a sanitizer or
   XSLT result, or a document edited any other way may nest arbitrarily deep. */
Py_ssize_t th_tree_nest_bound(const th_tree *tree);
/* Whether the nest bound lacks TH_NEST_CAPPED, so an element of the tree may have TH_DEEP_NEST ancestors. */
int th_tree_nests_deep(const th_tree *tree);
/* Drops the tree's nest bound, keeping TH_NEST_OBSERVED, after an edit that can nest a node deeper than before. The
   observed link primitives drop it themselves, and so does a copy that nests TH_HAS_MEMO_DEPTH levels; a caller that
   links with th_node_append_child or th_node_insert_before calls it when the link can deepen the tree. */
void th_tree_note_deepened(th_tree *tree);
/* Marks the tree as possibly observed, so its observed insertions queue their records (dom/observe.c). */
void th_tree_note_observed(th_tree *tree);
int th_tree_failed(const th_tree *tree);

/* Whether a text node holds only HTML ASCII whitespace (or nothing), realizing a
   zero-copy span on demand. The :empty selector uses it to ignore the document
   white space Selectors-4 §13.2 permits inside an otherwise empty element. Returns
   -1 when realizing the span fails. */
int th_node_text_is_blank(th_tree *tree, th_node *node);

/* Materialize one text/comment/doctype node's own character data (realizing a
   zero-copy span on demand) into a freshly PyMem-allocated UCS4 buffer.
   *out_len receives the length; returns NULL on allocation failure. */
Py_UCS4 *th_node_data(th_tree *tree, th_node *node, Py_ssize_t *out_len);

/* The concatenated character data of every Text descendant of node, in document
   order. PyMem-allocated; *out_len receives the length. NULL on failure. */
Py_UCS4 *th_node_text(th_tree *tree, th_node *node, Py_ssize_t *out_len);
/* The concatenated character data of node's Text children only, in order (the
   DOM "child text content"). Every Text node has one parent, so reading it for
   each element of a walk stays linear, where th_node_text on nested elements
   reads the innermost text once per ancestor. */
Py_UCS4 *th_node_child_text(th_tree *tree, th_node *node, Py_ssize_t *out_len);
PyObject *th_node_text_string(th_tree *tree, th_node *node);

/* An element's attributes as a tuple of (name, value) pairs, shared by the clones of one start tag within a walk.
   *shared is that walk's cache: NULL before the first call, released by the caller when the walk ends. A new
   reference, or NULL with an exception set. */
PyObject *th_node_attrs_tuple(th_tree *tree, th_node *node, PyObject **shared);

/* Gather node's concatenated descendant text into a caller-sized, reusable UCS4
   buffer (no allocation, no str), for the find(text=) literal/exact C scan. Stores the
   count of code points gathered in out_len; returns -1 when realizing a zero-copy text
   span fails. */
int th_node_collect_text(th_tree *tree, th_node *node, Py_UCS4 *buf, Py_ssize_t *out_len);

/* Serialize node and its subtree as HTML (the WHATWG fragment serialization).
   For the document node this is the whole-document markup. PyMem-allocated;
   *out_len receives the length. NULL on failure. */
Py_UCS4 *th_node_html(th_tree *tree, th_node *node, Py_ssize_t *out_len);

/* Where Markdown embeds the HTML: a raw HTML block passes it through untouched, while inline content around it is
   still read as Markdown. */
typedef enum { TH_MARKDOWN_HTML_BLOCK = 1, TH_MARKDOWN_HTML_INLINE, TH_MARKDOWN_HTML_CELL } th_markdown_html;

/* th_node_html for Markdown's raw-HTML passages: an attribute name CommonMark cannot
   read turns the whole tag into literal text, so such names are dropped. Inline, a
   processing instruction and the text of a raw-text element are dropped too: CommonMark
   has no inline form of the `<?x>` spelling, and raw text holds a bare `<` the reader
   would take for the start of markup. */
Py_UCS4 *th_node_markdown_html(th_tree *tree, th_node *node, th_markdown_html context, Py_ssize_t *clone_budget,
                               Py_ssize_t *out_len);
Py_UCS4 *th_node_markdown_open_tag(th_tree *tree, th_node *node, th_markdown_html context, Py_ssize_t *clone_budget,
                                   Py_ssize_t *out_len);

typedef struct {
    const Py_UCS4 *open;
    Py_ssize_t open_len;
    const Py_UCS4 *close;
    Py_ssize_t close_len;
} th_cell_wrap;

/* An enclosing inline marker would otherwise vanish at the raw table block boundary. */
Py_UCS4 *th_node_markdown_table_html(th_tree *tree, th_node *node, const th_cell_wrap *wrap, Py_ssize_t *clone_budget,
                                     Py_ssize_t *out_len);

/* Serialize only node's children (its inner HTML), WHATWG-conformant and
   compact. PyMem-allocated; *out_len receives the length. NULL on failure. */
Py_UCS4 *th_node_inner_html(th_tree *tree, th_node *node, Py_ssize_t *out_len);

/* Serialize only node's children as well-formed XML/XHTML: empty elements self-close,
   values follow the XML escaping rules, foreign subtrees declare their namespace, and
   comments and character data are neutralized so the result always reparses. PyMem-
   allocated; *out_len receives the length. NULL on failure, with ValueError set when the
   namespace declarations the children inherit outgrow their allowance. */
Py_UCS4 *th_node_inner_xml(th_tree *tree, th_node *node, Py_ssize_t *out_len);

/* Where a serialization of an XML tree stands with the xlink prefix, so a start tag reads the binding it inherits off
   the tag written before it rather than off a scan of its ancestors. Start it at {NULL, NULL}; it stays there until a
   tag first needs the binding. */
typedef struct {
    const th_node *last;  /* the element whose start tag was written last */
    const th_node *scope; /* the outermost of last and its ancestors that carries xmlns:xlink, NULL when none does */
} th_xml_xlink_walk;
/* th_node_inner_xml with only the namespace declarations the children hold themselves: none is
   copied from above node, so the output stays proportional to the subtree, and a name bound
   above node is unbound in it. NULL on allocation failure alone. */
Py_UCS4 *th_node_inner_xml_local(th_tree *tree, th_node *node, Py_ssize_t *out_len);

/* th_node_inner_xml under the smaller base one construct of a feed gets (TH_XML_NS_FEED_BASE): a feed keeps the
   inner XML of every entry, so a base granted afresh to each must be one the entry's own markup pays for. */
Py_UCS4 *th_node_inner_xml_construct(th_tree *tree, th_node *node, Py_ssize_t *out_len);

/* Copying an ancestor's namespace declaration onto each element that uses it keeps every element readable on its own,
   but one long name above many short elements multiplies the output instead of adding to it. So the rest of the
   output funds the copies: TH_XML_NS_PER_CHAR code points of declaration for each code point written otherwise, over a
   base that lets a short fragment carry long names. The Atom declaration on an <a/> costs 9 to 1 and an OOXML
   relationships one on an <r:id/> 11 to 1, so documents in the wild stay well inside the ratio. A feed grants the
   base once per text construct, and thousands of entries would multiply a large one, so its base only has to cover
   the declarations a construct's first element may need before any other output has funded them: 1,024 code points
   hold about twenty namespace declarations of ordinary length. */
#define TH_XML_NS_BASE 65536
#define TH_XML_NS_FEED_BASE 1024
#define TH_XML_NS_PER_CHAR 32

/* What one XML serialization of a node's children has copied onto them from the namespace declarations above, the
   account serialize/xml_text.h weighs against the rest of the output. Start it at {0, 0, base}. */
typedef struct {
    Py_ssize_t copied;  /* code points of inherited declarations written so far */
    Py_ssize_t flushed; /* code points handed off in the earlier chunks of a streamed serialization */
    Py_ssize_t base;    /* code points of copies granted before any other output funds them */
} th_xml_ns_spend;

/* An element the XML output leaves out because its name is no XML name, while the walk is inside it. Its children take
   its place, so they are written against what anchor declares and carry the declarations the left-out run holds. */
typedef struct {
    const th_node *node;
    const th_node *anchor; /* the nearest ancestor of node whose tag is written, or what stands above the output */
    Py_ssize_t chain;      /* index in decls of the first declaration of the left-out run that ends at node */
    Py_ssize_t own;        /* index in decls of the first declaration node carries */
} th_xml_unwrap_frame;

/* The left-out elements the walk is inside, innermost last, and the namespace declarations they carry. Start it zeroed
   with spend at {0, 0, TH_XML_NS_BASE}, and release it with th_xml_unwrap_free. */
typedef struct {
    th_xml_unwrap_frame *frames;
    Py_ssize_t len, cap;
    const th_node_attr **decls;
    Py_ssize_t decls_len, decls_cap;
    th_xml_ns_spend spend; /* the copies of the declarations onto the children, funded by the rest of the output */
} th_xml_unwrap;

void th_xml_unwrap_free(th_xml_unwrap *unwrap);

/* The attribute characters parser clones may write before other output funds more. The tree builder reopens an
   unclosed formatting element in every block after it, attributes and all, so every writer of a clone starts from this
   allowance. */
#define TH_CLONE_ALLOWANCE 256

/* The value sets an output has written a clone of, owned by the output that allocated it (serialize/buffer.h). */
struct th_clone_seen;

/* Free a seen set; NULL is a no-op. */
void th_clone_seen_free(struct th_clone_seen *seen);

/* Record values_seq as written by a clone of this output (serialize/document.c): 1 when new, 0 when present, -1 when
 * the table cannot grow. */
int th_clone_seen_add(struct th_clone_seen **seen_slot, uint32_t values_seq);

/* The namespace declarations in scope where a subtree is cut out of an XML tree (serialize/internal.h). */
typedef struct th_xml_ns_scope th_xml_ns_scope;

/* Output-shaping options shared by serialize() and encode(): the escape formatter
   plus two opt-in normalizations that leave the tree untouched. Every field is
   off (zero / NULL) by default, so the common serialize costs nothing. */
typedef struct {
    int formatter;       /* escape policy: 0 WHATWG, 1 minimal, 2 named entities */
    int sort_attributes; /* emit each start tag's attributes in ascending name order */
    int inject_meta;     /* ensure <head> declares <meta charset=charset> */
    const char *charset; /* ASCII encoding label for the injected/normalized meta */
    Py_ssize_t charset_len;
    int xml;   /* XML/XHTML syntax: self-close empty elements, XML escaping and namespace decls; drop the
                  characters, comment spellings, and attribute names XML cannot hold so the result reparses */
    int inner; /* omit the context root while retaining its serialization rules */
    th_xml_ns_scope *xml_scope;    /* set by the serializer while it writes part of an XML tree, NULL otherwise */
    int markdown;                  /* a th_markdown_html context, or 0 outside Markdown */
    int *charset_missing;          /* non-NULL when charset is a placeholder no label names: a writer sets
                                    *charset_missing instead of writing it, and the caller refuses the output */
    Py_ssize_t *clone_budget;      /* Markdown: the attribute characters parser clones may still write, or NULL */
    th_xml_xlink_walk *xml_xlink;  /* the xlink walk of an XML tree written as XML; NULL for an HTML tree */
    th_xml_ns_spend *xml_ns_spend; /* set when every xml_scope boundary child carries its own copy */
    th_xml_unwrap *xml_unwrap;     /* set by the serializer whenever xml is */
    int in_rawtext;                /* set while writing the text of a raw-text element: a script nested there ends
                                      with text, so the element that holds it decides its end tag */
    int *charset_written;          /* non-NULL when charset is windows-1252: a writer sets *charset_written when it
                                      declares the label, so the caller refuses C1 controls that label misreads */
} th_serialize_opts;

/* Serialize node and its subtree under opts. When indent is non-NULL it is the
   per-level whitespace unit for pretty output; NULL emits the compact form. PyMem-
   allocated; *out_len receives the length. NULL on failure, with ValueError set when
   opts->inner has the children inherit namespace declarations beyond their allowance. */
Py_UCS4 *th_node_serialize(th_tree *tree, th_node *node, const th_serialize_opts *opts, const Py_UCS4 *indent,
                           Py_ssize_t indent_len, Py_ssize_t *out_len);

/* Losslessly serialize node and its subtree, re-emitting the verbatim source bytes
   of every element and text run the parse left untouched and reserializing only the
   parts a mutation changed. Requires a tree parsed with source locations to preserve
   author formatting; without them every element reserializes canonically. PyMem-
   allocated; *out_len receives the length. NULL on failure. */
Py_UCS4 *th_node_serialize_source(th_tree *tree, th_node *node, Py_ssize_t *out_len);

/* Where a streaming serialization (serialize_iter) resumes between chunks: the next
   node to emit (NULL once the subtree is done), the pretty layout's current
   indentation depth, the xlink walk, the inherited namespace declarations charged
   so far, the clone credit and the value sets the earlier chunks left and the
   left-out elements the walk is inside. Start it at {root, 0, {NULL, NULL}, {0, 0, TH_XML_NS_BASE}, 0, NULL} with
   unwrap as th_xml_unwrap asks, and release unwrap with th_xml_unwrap_free and clone_seen with th_clone_seen_free. */
typedef struct {
    th_node *node;
    int depth;
    th_xml_xlink_walk xlink;
    th_xml_ns_spend xml_ns;
    Py_ssize_t clone_credit;          /* sbuf clone_credit of the next chunk: what the earlier chunks funded and kept */
    struct th_clone_seen *clone_seen; /* the value sets the earlier chunks wrote a clone of */
    th_xml_unwrap unwrap;
} th_ser_cursor;

/* Serialize the next bounded chunk of the subtree rooted at root, resuming from
   cursor and advancing it to the following resume point (cursor->node becomes NULL
   once the walk is done). indent selects the pretty layout as in th_node_serialize.
   Joining every chunk reproduces th_node_serialize's output byte for byte, since
   both drive the same per-node walk. PyMem-allocated; *out_len receives the chunk's
   length (0 only on the terminal empty chunk). NULL on failure. */
Py_UCS4 *th_node_serialize_chunk(th_tree *tree, th_node *root, const th_serialize_opts *opts, const Py_UCS4 *indent,
                                 Py_ssize_t indent_len, th_ser_cursor *cursor, Py_ssize_t *out_len);

/* A noscript element a scripting-off parse built from a start tag that a scripting-on parse reads as raw text up to the
   first `</noscript`. Offsets index the newline-normalized source. stable says both parses agree on everything outside
   the element and its content is the source slice [content, end): then copying that slice serves both parses. cut says
   the builder state at the start tag is one the minified markup before it rebuilds, so the source from start onward can
   follow that markup verbatim. node is NULL for one the depth cap kept off the stack, which is neither. */
typedef struct {
    th_node *node;
    uint32_t seq;       /* the element's th_node seq, which grows in the order the spans are recorded */
    Py_ssize_t start;   /* the start tag's '<' */
    Py_ssize_t content; /* just past the start tag */
    Py_ssize_t end;     /* the closing end tag's '<', or -1 when no end tag closed it */
    int stable;
    int cut;
    /* the html element, and the body when open, with their attribute counts at the start tag: a later start tag that
       adds attributes to either leaves the minified markup before a cut carrying them too early */
    th_node *html;
    Py_ssize_t html_attrs;
    th_node *body;
    Py_ssize_t body_attrs;
} th_noscript_span;

/* What th_tree_parse_noscript records: one span per noscript, in the order the parse built them. The rest is the
   builder's state while one is open, compared when it closes. */
typedef struct {
    th_noscript_span *spans;
    Py_ssize_t count;
    Py_ssize_t cap;
    Py_ssize_t open; /* index of the span still open, -1 for none */
    Py_ssize_t open_len;
    Py_ssize_t afe_len;
    th_node *afe_last;
    th_node *form;
    Py_ssize_t html_attrs;
    th_node *body;
    Py_ssize_t body_attrs;
    int frameset_ok;
    int mode;
    Py_ssize_t frameset_span; /* the first span whose content cleared frameset-ok, -1 for none */
    int failed;
} th_noscript_track;

/* Parse a whole document with scripting off, as th_tree_parse with positions on, recording each noscript in track
   (zero it first; th_noscript_track_free releases it). NULL only on allocation failure. */
th_tree *th_tree_parse_noscript(int kind, const void *data, Py_ssize_t length, th_noscript_track *track);
void th_noscript_track_free(th_noscript_track *track);

/* The noscript spans a document minify reads besides its tree, passed only when none is unstable or the first unstable
   one is a cut: the walk copies each stable one's content, and the rest of the source from the cut. */
typedef struct {
    const th_noscript_span *spans;
    Py_ssize_t span_count;
} th_minify_source;

/* The minification transforms serialize(minify=...) toggles, each round-trip safe
   (the minified bytes reparse to the same tree). */
typedef struct {
    int collapse_whitespace;        /* fold ASCII-whitespace runs to a single space outside pre/textarea/listing */
    int omit_optional_tags;         /* drop the start/end tags the WHATWG optional-tag rules allow */
    int unquote_attributes;         /* drop redundant attribute quotes and write empty values as bare names */
    int strip_comments;             /* skip comment nodes */
    int minify_js;                  /* minify inline <script> JavaScript (a parse failure falls back to verbatim) */
    int minify_js_fold;             /* run the JS constant-folding / dead-code pass */
    int minify_js_mangle;           /* run the JS identifier-renaming pass */
    int minify_css;                 /* minify <style> bodies and style="" values through the CSS minifier */
    int minify_css_baseline;        /* the CSSMinify baseline bounding the output syntax, 0 for None */
    const th_minify_source *source; /* NULL outside a document minify */
} th_minify_opts;

/* Serialize node and its subtree minified under minify and the output options.
   PyMem-allocated; *out_len receives the length. NULL on failure. */
Py_UCS4 *th_node_minify(th_tree *tree, th_node *node, const th_minify_opts *minify, const th_serialize_opts *opts,
                        Py_ssize_t *out_len);

/* Canonical XML (c14n) options. version selects the algorithm (0 = c14n 1.0,
   1 = c14n 1.1), coinciding for the complete-subtree canonicalization this performs
   bar the apex's inherited xml: attributes (1.1 excludes xml:id). exclusive selects
   Exclusive XML Canonicalization, which renders only the namespaces a subtree
   visibly uses; with_comments keeps comment nodes; inclusive is the exclusive-mode
   InclusiveNamespaces prefix list (NULL / 0 count when unused). */
typedef struct {
    int version;
    int exclusive;
    int with_comments;
    const char *const *inclusive;
    Py_ssize_t inclusive_count;
} th_c14n_opts;

/* Canonicalize node and its complete subtree to Canonical XML under opts, as a
   UTF-8 infoset the caller encodes to bytes. A document node canonicalizes its
   element child plus any comment/PI siblings; any other node is the subtree apex.
   PyMem-allocated; *out_len receives the length. NULL on failure. */
Py_UCS4 *th_node_canonicalize(th_tree *tree, th_node *node, const th_c14n_opts *opts, Py_ssize_t *out_len);
Py_UCS4 *th_node_canonicalize_xml(th_tree *tree, th_node *node, const th_c14n_opts *opts, Py_ssize_t *out_len);

/* The Markdown export configuration, a union of the markdownify and html2text
   knobs with one name per concept. The Python binding fills it from keyword
   arguments starting from th_markdown_default_opts(). String markers are
   borrowed ASCII whose argument objects the binding keeps alive for the call. */
enum th_md_heading { TH_MD_HEADING_ATX, TH_MD_HEADING_ATX_CLOSED, TH_MD_HEADING_SETEXT };
enum th_md_code { TH_MD_CODE_FENCED, TH_MD_CODE_INDENTED };
enum th_md_link { TH_MD_LINK_INLINE, TH_MD_LINK_REFERENCE };
enum th_md_image { TH_MD_IMAGE_MARKDOWN, TH_MD_IMAGE_ALT, TH_MD_IMAGE_IGNORE, TH_MD_IMAGE_HTML };
enum th_md_table { TH_MD_TABLE_MARKDOWN, TH_MD_TABLE_STRIP, TH_MD_TABLE_HTML };
enum th_md_escape { TH_MD_ESCAPE_MINIMAL, TH_MD_ESCAPE_ALL, TH_MD_ESCAPE_NONE };
enum th_md_break { TH_MD_BREAK_SPACES, TH_MD_BREAK_BACKSLASH };
enum th_md_doc_strip { TH_MD_DOC_STRIP, TH_MD_DOC_LSTRIP, TH_MD_DOC_RSTRIP, TH_MD_DOC_NONE };
enum th_md_table_header { TH_MD_HEADER_FIRST, TH_MD_HEADER_DETECT, TH_MD_HEADER_NONE };
/* A pipe-table cell holds inline content only, so a table or list inside one has
   no markdown spelling: keep its source HTML (which renders as the real thing) or
   flatten it to the text it contains. */
enum th_md_cell_blocks { TH_MD_CELL_HTML, TH_MD_CELL_TEXT };
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
    int cell_blocks;        /* enum th_md_cell_blocks */
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
    int raw_list_html;                        /* default options permit ordinal-preserving HTML fallback */
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
   receives the length. NULL on allocation failure. ordinals is the owner's slot for the
   cache that lets list items render one by one; the owner frees it with
   th_markdown_ordinals_free and resets it to NULL on every structural change and
   before the tree goes away. */
Py_UCS4 *th_node_markdown(th_tree *tree, th_node *node, const md_opts *opt, void **ordinals, Py_ssize_t *out_len);
void th_markdown_ordinals_free(void *ordinals);

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
   th_node_layout_text) afterwards, after checking th_tree_failed: a text span that
   cannot be realized only drops out of the scores. The engine for
   Node.main_content()/main_text(). */
th_node *th_node_main_content(th_tree *tree, th_node *root);

/* One harvested tag/keyword: a freshly PyMem-allocated, whitespace-normalized
   code-point buffer and its length. */
typedef struct {
    Py_UCS4 *data;
    Py_ssize_t len;
} th_article_tag;

/* The page-level article metadata Node.article() returns beside the content body:
   each single-valued field is a freshly PyMem-allocated, whitespace-normalized
   code-point buffer (NULL with a zero length when the source is absent), harvested in
   pure C from the document the content sits in -- title from <h1> / og:title /
   <title>, byline from a rel=author link / meta author / article:author, date from
   <time> / article:published_time / a common date meta, description from
   og:description / meta description, lang from <html lang>, canonical from
   <link rel=canonical> / og:url, site_name from og:site_name / meta application-name,
   and image from og:image / twitter:image. tags holds each <meta name=keywords>
   value (comma-split) and each article:tag in document order, tags_count of them (an
   empty run when none appear). The caller holds the per-tree critical section,
   materializes each buffer into a str (or None) and tags into a tuple, then
   th_article_meta_clear frees them. */
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
    Py_UCS4 *canonical;
    Py_ssize_t canonical_len;
    Py_UCS4 *site_name;
    Py_ssize_t site_name_len;
    Py_UCS4 *image;
    Py_ssize_t image_len;
    th_article_tag *tags;
    Py_ssize_t tags_count;
} th_article_meta;

/* Fill meta by harvesting the article metadata fields from root (the document the
   content body belongs to). Pure C; the caller holds the per-tree critical section. On an
   allocation failure some fields stay absent, so the caller checks th_tree_failed before using meta. */
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

/* The granular source location of a parsed element, or NULL when the tree carries
   no source locations, the node is not an element, or the element has no source
   start tag (a synthetic html/head/body, a fragment root, or a hand-built one). */
const th_src_loc *th_node_source_location(th_tree *tree, th_node *node);
const void *th_tree_source_data(th_tree *tree, int *kind, int *has_nul);

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

/* Shadow DOM tree model (dom/shadow.c). A shadow root is a document-fragment-like
   TH_NODE_CONTENT node held off the light tree (it is never a child of any node) and
   linked to its host element through the per-tree shadow table. */

/* Attach a shadow root to host and record the host<->root link; mode 0 is open, 1 is
   closed. Returns the new shadow root, or NULL on allocation failure. The caller must
   have checked host has no shadow root yet. */
th_node *th_element_attach_shadow(th_tree *tree, th_node *host, int mode);

/* host's attached shadow root, or NULL when it has none. */
th_node *th_element_shadow_root(th_tree *tree, th_node *host);

/* The host element a shadow root is attached to; NULL when root is not a shadow root
   of this tree. */
th_node *th_shadow_host(th_tree *tree, th_node *root);

/* Whether node is a shadow root (a flagged content node), and its mode (0 open, 1
   closed; only meaningful for a shadow root). */
int th_node_is_shadow_root(const th_node *node);
int th_shadow_mode(const th_node *root);

#endif /* TURBOHTML_TREEBUILDER_H */
