/* RSS 2.0 / Atom 1.0 / RDF(RSS 1.0) feed extraction over a parsed tree, the C engine behind Document.feed() (wired into
   the document method table in dom/document.c) and the turbohtml.extract.feed(xml) facade.

   feedparser is the canonical Python feed library and htmlparser2's parseFeed the compact minimal model; both normalize
   an RSS <item>, an Atom <entry>, and an RDF item into one shape. This follows htmlparser2's minimal-first design (a
   dozen fields, first-present-wins precedence) with feedparser's field precedence for the cases that matter (the
   content:encoded/content/summary/description chain, guid-as-permalink link, the Atom rel="alternate" link selection).

   The facade parses a feed with parse_xml and falls back to the HTML tree builder when it is not well-formed, so the
   walk reads either tree. A field's value is its Text and CDATA character data, except an Atom
   XHTML construct, which keeps the markup inside its XHTML div. HTML also makes <link> void, so an RSS/RDF
   <link>URL</link> leaves the URL as the void element's next
   text sibling, where the XML tree keeps it as the element's own text.

   The walk runs under the per-tree critical section so a concurrent mutation cannot relink the tree mid-walk, and hands
   the gathered plain str/None fields to the Feed and Entry NamedTuple classes the thin Python facade defines and
   registers on import (turbohtml._feed), so the typed result classes live in Python while every walk stays in C.
 */

#include "core/ascii.h"
#include "core/common.h"
#include "core/node_map.h"
#include "core/work_steps.h"

#include "tokenizer/binding.h" /* Py_BEGIN_CRITICAL_SECTION shim for the GIL/pre-3.13 build */
#include "dom/nodes.h"
#include "serialize/xml_text.h"

#include <stdlib.h>
#include <string.h>

static const char DC_URI[] = "http://purl.org/dc/elements/1.1/";
static const char CONTENT_URI[] = "http://purl.org/rss/1.0/modules/content/";
static const char RDF_URI[] = "http://www.w3.org/1999/02/22-rdf-syntax-ns#";
static const char RSS_URI[] = "http://purl.org/rss/1.0/";
static const char ATOM_URI[] = "http://www.w3.org/2005/Atom";
static const char XHTML_URI[] = "http://www.w3.org/1999/xhtml";
static const Py_UCS4 RSS_URI_UCS4[] = {'h', 't', 't', 'p', ':', '/', '/', 'p', 'u', 'r', 'l', '.',
                                       'o', 'r', 'g', '/', 'r', 's', 's', '/', '1', '.', '0', '/'};
static const Py_UCS4 ATOM_URI_UCS4[] = {'h', 't', 't', 'p', ':', '/', '/', 'w', 'w', 'w', '.', 'w', '3', '.',
                                        'o', 'r', 'g', '/', '2', '0', '0', '5', '/', 'A', 't', 'o', 'm'};

/* One candidate element tag for a normalized field: the name and its length, tried in precedence order. */
typedef struct {
    const char *name;
    Py_ssize_t len;
} feed_tag;

static int ucs4_ieq(const Py_UCS4 *value, Py_ssize_t len, const char *lit, Py_ssize_t lit_len) {
    if (len != lit_len) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        if (lower_ascii(value[index]) != (Py_UCS4)(unsigned char)lit[index]) {
            return 0;
        }
    }
    return 1;
}

/* Whether element `node`'s tag name equals the ASCII literal [name, name + len). */
static int tag_is(const th_node *node, const char *name, Py_ssize_t len) {
    return ucs4_ieq(node->text, node->text_len, name, len);
}

static int ucs4_eq_ascii_len(const Py_UCS4 *value, Py_ssize_t len, const char *lit, Py_ssize_t lit_len) {
    if (value == NULL || len != lit_len) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        if (value[index] != (Py_UCS4)(unsigned char)lit[index]) {
            return 0;
        }
    }
    return 1;
}

static int ucs4_eq_ascii(const Py_UCS4 *value, Py_ssize_t len, const char *lit) {
    return ucs4_eq_ascii_len(value, len, lit, (Py_ssize_t)strlen(lit));
}

/* The atom of the attribute that declares a namespace prefix: `xmlns:` and the prefix, which is either the UTF-8
   [bytes, bytes + prefix_len) or, when `bytes` is NULL, the code points [text, text + prefix_len). UINT32_MAX when no
   element of the tree carries that attribute, so nothing binds the prefix. Names are interned, which makes finding the
   declaration an integer compare per attribute. Returns -1 on allocation failure. */
static int xmlns_atom(th_tree *tree, const char *bytes, const Py_UCS4 *text, Py_ssize_t prefix_len, uint32_t *atom) {
    char buffer[64];
    char *name = buffer;
    size_t needed = (size_t)prefix_len * (bytes != NULL ? 1 : 4) + 6;
    if (needed > sizeof(buffer)) {
        name = PyMem_Malloc(needed);
        if (name == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure */
            PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation failure */
            return -1;        /* GCOVR_EXCL_LINE: allocation failure */
        }
    }
    memcpy(name, "xmlns:", 6);
    Py_ssize_t len = 6;
    if (bytes != NULL) {
        memcpy(name + len, bytes, (size_t)prefix_len);
        len += prefix_len;
    }
    for (Py_ssize_t index = 0; bytes == NULL && index < prefix_len; index++) {
        Py_UCS4 character = text[index];
        if (character < 0x80) {
            name[len++] = (char)character;
        } else if (character < 0x800) {
            name[len++] = (char)(0xC0 | (character >> 6));
            name[len++] = (char)(0x80 | (character & 0x3F));
        } else if (character < 0x10000) {
            name[len++] = (char)(0xE0 | (character >> 12));
            name[len++] = (char)(0x80 | ((character >> 6) & 0x3F));
            name[len++] = (char)(0x80 | (character & 0x3F));
        } else {
            name[len++] = (char)(0xF0 | (character >> 18));
            name[len++] = (char)(0x80 | ((character >> 12) & 0x3F));
            name[len++] = (char)(0x80 | ((character >> 6) & 0x3F));
            name[len++] = (char)(0x80 | (character & 0x3F));
        }
    }
    *atom = th_attr_lookup(tree, name, len);
    if (name != buffer) {
        PyMem_Free(name);
    }
    return 0;
}

/* The atom that declares the namespace of element `node`, whose tag has a prefix of `prefix_len` code points: plain
   `xmlns` when it has none. Returns -1 on allocation failure. */
static int tag_xmlns_atom(th_tree *tree, const th_node *node, Py_ssize_t prefix_len, uint32_t *atom) {
    if (prefix_len == 0) {
        *atom = th_attr_lookup(tree, "xmlns", 5);
        return 0;
    }
    return xmlns_atom(tree, NULL, node->text, prefix_len, atom);
}

/* Elements with more attributes than this are looked up through a table; a smaller one is scanned. */
enum { NS_LINEAR_ATTRS = 8 };

typedef struct {
    uint32_t atom;
    const th_node_attr *attr;
} ns_attr_entry;

typedef struct {
    ns_attr_entry *entries;
    Py_ssize_t count;
} ns_attr_table;

/* The attribute tables of one feed walk, built on first lookup and freed with the walk. `tables_of` maps an element to
   its table's index plus one, so a climb through a wide element costs a binary search per ancestor. */
typedef struct {
    th_tree *tree;
    th_node_map tables_of;
    ns_attr_table *tables;
    size_t table_count;
    size_t table_capacity;
} ns_index;

static void ns_index_free(ns_index *index) {
    for (size_t table = 0; table < index->table_count; table++) {
        PyMem_Free(index->tables[table].entries);
    }
    PyMem_Free(index->tables);
    PyMem_Free(index->tables_of.entries);
}

static int ns_entry_compare(const void *left, const void *right) {
    uint32_t left_atom = ((const ns_attr_entry *)left)->atom;
    uint32_t right_atom = ((const ns_attr_entry *)right)->atom;
    return (left_atom > right_atom) - (left_atom < right_atom);
}

static const th_node_attr *ns_table_find(const ns_attr_table *table, uint32_t atom) {
    Py_ssize_t low = 0;
    Py_ssize_t high = table->count;
    while (low < high) {
        Py_ssize_t middle = low + (high - low) / 2;
        if (table->entries[middle].atom < atom) {
            low = middle + 1;
        } else {
            high = middle;
        }
    }
    return table->entries[low].atom == atom ? table->entries[low].attr : NULL;
}

/* The table of `node`'s attributes, sorted by name atom so each lookup is a binary search. A sentinel past the last
   entry lets the search end on an entry without a bounds check. NULL on allocation failure. */
static ns_attr_table *ns_index_table(ns_index *index, const th_node *node) {
    Py_ssize_t known = th_node_map_find(&index->tables_of, node);
    if (known != 0) {
        return &index->tables[known - 1];
    }
    if (index->table_count == index->table_capacity) {
        size_t table_capacity;
        size_t table_bytes;
        const int grew = th_grow_cap(index->table_count + 1, index->table_capacity, 8, sizeof(ns_attr_table),
                                     &table_capacity, &table_bytes);
        if (!grew) {          /* GCOVR_EXCL_BR_LINE: allocation size overflow */
            PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
            return NULL;      /* GCOVR_EXCL_LINE */
        }
        ns_attr_table *grown = PyMem_Realloc(index->tables, table_bytes);
        if (grown == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure */
            PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation failure */
            return NULL;      /* GCOVR_EXCL_LINE: allocation failure */
        }
        index->tables = grown;
        index->table_capacity = table_capacity;
    }
    ns_attr_entry *entries = PyMem_Malloc(sizeof(*entries) * ((size_t)node->attr_count + 1));
    if (entries == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        PyErr_NoMemory();  /* GCOVR_EXCL_LINE: allocation failure */
        return NULL;       /* GCOVR_EXCL_LINE: allocation failure */
    }
    for (Py_ssize_t offset = 0; offset < node->attr_count; offset++) {
        entries[offset] = (ns_attr_entry){node->attrs[offset].name_atom, &node->attrs[offset]};
    }
    qsort(entries, (size_t)node->attr_count, sizeof(*entries), ns_entry_compare);
    entries[node->attr_count] = (ns_attr_entry){UINT32_MAX, NULL};
    const int inserted = th_node_map_insert(&index->tables_of, node, (Py_ssize_t)index->table_count + 1);
    if (inserted < 0) {      /* GCOVR_EXCL_BR_LINE: allocation failure */
        PyMem_Free(entries); /* GCOVR_EXCL_LINE: allocation failure */
        PyErr_NoMemory();    /* GCOVR_EXCL_LINE: allocation failure */
        return NULL;         /* GCOVR_EXCL_LINE: allocation failure */
    }
    index->tables[index->table_count] = (ns_attr_table){entries, node->attr_count};
    return &index->tables[index->table_count++];
}

/* The first attribute of `node` named by `atom`, NULL when it has none. Returns -1 on allocation failure. */
static int ns_own_attr(ns_index *index, const th_node *node, uint32_t atom, const th_node_attr **attr) {
    if (node->attr_count > NS_LINEAR_ATTRS) {
        ns_attr_table *table = ns_index_table(index, node);
        if (table == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;       /* GCOVR_EXCL_LINE: allocation failure */
        }
        *attr = ns_table_find(table, atom);
        return 0;
    }
    *attr = NULL;
    for (Py_ssize_t offset = 0; offset < node->attr_count; offset++) {
        TH_WORK_STEP();
        if (node->attrs[offset].name_atom == atom) {
            *attr = &node->attrs[offset];
            break;
        }
    }
    return 0;
}

/* The nearest attribute named by `atom` on `node` or an ancestor, NULL when none is in scope. Returns -1 on allocation
   failure. */
static int ns_climb_attr(ns_index *index, const th_node *node, uint32_t atom, const th_node_attr **attr) {
    *attr = NULL;
    for (const th_node *ancestor = node; atom != UINT32_MAX && ancestor != NULL; ancestor = ancestor->parent) {
        TH_WORK_STEP();
        if (ancestor->type != TH_NODE_ELEMENT) {
            continue;
        }
        if (ns_own_attr(index, ancestor, atom, attr) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;                                      /* GCOVR_EXCL_LINE: allocation failure */
        }
        if (*attr != NULL) {
            return 0;
        }
    }
    return 0;
}

static int namespace_is(ns_index *index, th_node *node, Py_ssize_t prefix_len, const char *uri) {
    uint32_t atom;
    if (tag_xmlns_atom(index->tree, node, prefix_len, &atom) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                                                  /* GCOVR_EXCL_LINE: allocation failure */
    }
    const th_node_attr *attr;
    if (ns_climb_attr(index, node, atom, &attr) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                                     /* GCOVR_EXCL_LINE: allocation failure */
    }
    return attr != NULL && ucs4_eq_ascii(attr->value, attr->value_len, uri);
}

static int default_namespace_at_node(th_tree *tree, th_node *node, const char *uri, int inherited) {
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        th_node_attr *attr = &node->attrs[index];
        Py_ssize_t len;
        const char *name = th_attr_name(tree, attr->name_atom, &len);
        if (len == 5 && memcmp(name, "xmlns", 5) == 0) {
            const Py_UCS4 *expected = uri == ATOM_URI ? ATOM_URI_UCS4 : RSS_URI_UCS4;
            Py_ssize_t expected_len = uri == ATOM_URI ? 27 : 24;
            return attr->value_len == expected_len &&
                   memcmp(attr->value, expected, (size_t)expected_len * sizeof(Py_UCS4)) == 0;
        }
    }
    return inherited;
}

static int default_namespace_is(th_tree *tree, th_node *node, const char *uri, int missing_is_match) {
    for (th_node *ancestor = node; ancestor != NULL; ancestor = ancestor->parent) {
        if (ancestor->type != TH_NODE_ELEMENT) {
            continue;
        }
        int match = default_namespace_at_node(tree, ancestor, uri, -1);
        if (match >= 0) {
            return match;
        }
    }
    return missing_is_match;
}

static int empty_namespace_at_node(th_tree *tree, th_node *node, int inherited) {
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        th_node_attr *attr = &node->attrs[index];
        Py_ssize_t len;
        const char *name = th_attr_name(tree, attr->name_atom, &len);
        if (len == 5 && memcmp(name, "xmlns", 5) == 0) {
            return attr->value_len == 0;
        }
    }
    return inherited;
}

static int empty_namespace_is_or_missing(th_tree *tree, th_node *node) {
    for (th_node *ancestor = node; ancestor != NULL; ancestor = ancestor->parent) {
        if (ancestor->type == TH_NODE_ELEMENT) {
            int match = empty_namespace_at_node(tree, ancestor, -1);
            if (match >= 0) {
                return match;
            }
        }
    }
    return 1;
}

static int namespace_bytes_is(ns_index *index, th_node *node, const char *prefix, Py_ssize_t prefix_len,
                              const char *uri) {
    uint32_t atom;
    if (xmlns_atom(index->tree, prefix, NULL, prefix_len, &atom) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                                                      /* GCOVR_EXCL_LINE: allocation failure */
    }
    const th_node_attr *attr;
    if (ns_climb_attr(index, node, atom, &attr) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                                     /* GCOVR_EXCL_LINE: allocation failure */
    }
    return attr != NULL && ucs4_eq_ascii(attr->value, attr->value_len, uri);
}

static int qualified_tag_is(ns_index *index, th_node *node, const char *local, const char *uri) {
    Py_ssize_t local_len = (Py_ssize_t)strlen(local);
    if (node->text_len == local_len && ucs4_eq_ascii(node->text, local_len, local)) {
        return namespace_is(index, node, 0, uri);
    }
    Py_ssize_t colon = node->text_len - local_len - 1;
    return colon > 0 && node->text[colon] == ':' && ucs4_eq_ascii(node->text + colon + 1, local_len, local)
               ? namespace_is(index, node, colon, uri)
               : 0;
}

static int feed_tag_is(ns_index *index, th_node *node, const char *name, Py_ssize_t len, const char *uri,
                       int inherited) {
    th_tree *tree = index->tree;
    if (th_tree_is_xml(tree) ? ucs4_eq_ascii_len(node->text, node->text_len, name, len) : tag_is(node, name, len)) {
        return !th_tree_is_xml(tree) || (inherited && node->attr_count == 0) ||
               (uri == NULL ? empty_namespace_at_node(tree, node, inherited)
                            : default_namespace_at_node(tree, node, uri, inherited));
    }
    if (uri == NULL || node->text_len <= len + 1) {
        return 0;
    }
    Py_ssize_t colon = node->text_len - len - 1;
    return node->text[colon] == ':' && th_tree_is_xml(tree) && ucs4_eq_ascii_len(node->text + colon + 1, len, name, len)
               ? namespace_is(index, node, colon, uri)
               : 0;
}

static th_node *child_named(th_tree *tree, th_node *parent, const char *name, Py_ssize_t len) {
    for (th_node *child = parent->first_child; child != NULL; child = child->next_sibling) {
        if (child->type == TH_NODE_ELEMENT &&
            (th_tree_is_xml(tree) ? ucs4_eq_ascii_len(child->text, child->text_len, name, len)
                                  : tag_is(child, name, len)) &&
            (!th_tree_is_xml(tree) || child->attr_count == 0 || empty_namespace_at_node(tree, child, 1))) {
            return child;
        }
    }
    return NULL;
}

static th_node *feed_child_named(ns_index *index, th_node *parent, const char *name, Py_ssize_t len, const char *uri,
                                 int inherited, int *error) {
    if (uri == NULL) {
        return child_named(index->tree, parent, name, len);
    }
    for (th_node *child = parent->first_child; child != NULL; child = child->next_sibling) {
        if (child->type != TH_NODE_ELEMENT) {
            continue;
        }
        int match = feed_tag_is(index, child, name, len, uri, inherited);
        if (match < 0) { /* GCOVR_EXCL_BR_LINE: namespace_is fails only on allocation */
            *error = 1;  /* GCOVR_EXCL_LINE: allocation failure */
            return NULL; /* GCOVR_EXCL_LINE: allocation failure */
        }
        if (match) {
            return child;
        }
    }
    return NULL;
}

/* A UCS4 run as a new str with leading and trailing ASCII whitespace stripped. NULL only on the excluded
   allocation-failure path. */
static PyObject *ucs4_trimmed(const Py_UCS4 *value, Py_ssize_t len) {
    Py_ssize_t start = 0;
    Py_ssize_t end = len;
    while (start < end && is_space(value[start])) {
        start++;
    }
    while (end > start && is_space(value[end - 1])) {
        end--;
    }
    return ucs4_to_str(value + start, end - start);
}

/* The whitespace-trimmed character data of an element as a new str (empty when it holds only whitespace): its Text and
   CDATA descendants in document order. An XML feed wraps markup-bearing fields in CDATA
   (<description><![CDATA[<p>...</p>]]></description>), and the field's value is that markup verbatim, as an escaped
   description's is. NULL only on the excluded allocation-failure path. */
static PyObject *node_text_trimmed(th_tree *tree, th_node *node) {
    sbuf buffer = {NULL, 0, 0, 0, 0};
    for (th_node *child = node->first_child; child != NULL; child = preorder_next(child, node)) {
        if (child->type != TH_NODE_TEXT && child->type != TH_NODE_CDATA) {
            continue;
        }
        Py_ssize_t len;
        Py_UCS4 *data = th_node_data(tree, child, &len);
        if (data == NULL) {    /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            buffer.failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
            break;             /* GCOVR_EXCL_LINE */
        }
        sbuf_put_ucs4(&buffer, data, len);
        PyMem_Free(data);
    }
    if (buffer.failed) {         /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyMem_Free(buffer.data); /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *result = ucs4_trimmed(buffer.data, buffer.len);
    PyMem_Free(buffer.data);
    return result;
}

/* One declaration a walk of XHTML content entered: the element carrying it and the binding it shadows. */
typedef struct {
    const th_node *node;
    uint32_t atom;
    const th_node_attr *previous;
} xhtml_ns_frame;

/* The binding a walk of XHTML content holds for one `xmlns` attribute atom: the declaration in scope, NULL while it
   is not resolved. A free slot has the atom TH_ATTR_UNKNOWN. */
typedef struct {
    uint32_t atom;
    const th_node_attr *attr;
} xhtml_ns_slot;

/* The namespace declarations in scope at the element a walk of XHTML content stands on, by attribute atom as the
   xsl:number index in query/xslt.c keeps them. Entering an element shadows what it redeclares and leaving it restores
   that, so resolving a name costs one lookup instead of a climb through every ancestor's attributes; a binding the
   content inherits from its div or above is looked for once and kept. The open-addressing table holds only the atoms
   the content uses, so a small construct stays cheap in a feed with many attribute names. */
typedef struct {
    ns_index *index;
    th_node *div;
    xhtml_ns_slot *slots;
    size_t slot_capacity;
    size_t slot_count;
    xhtml_ns_frame *frames;
    size_t frame_count;
    size_t frame_capacity;
} xhtml_ns;

/* The slot holding `atom`, or the free slot it would take. */
static xhtml_ns_slot *xhtml_ns_slot_of(const xhtml_ns *scope, uint32_t atom) {
    size_t slot = th_hash_slot(th_fibonacci_hash(atom), scope->slot_capacity);
    while (scope->slots[slot].atom != TH_ATTR_UNKNOWN && scope->slots[slot].atom != atom) {
        slot = (slot + 1) & (scope->slot_capacity - 1);
    }
    return &scope->slots[slot];
}

/* The slot of `atom`, claimed unresolved on first sight; the table grows at half load, as th_node_map does. Returns
   NULL on allocation failure. */
static xhtml_ns_slot *xhtml_ns_claim(xhtml_ns *scope, uint32_t atom) {
    if (scope->slot_count >= scope->slot_capacity / 2) {
        size_t capacity;
        size_t bytes;
        const int grew = th_grow_cap((scope->slot_count + 1) * 2, scope->slot_capacity, 16, sizeof(xhtml_ns_slot),
                                     &capacity, &bytes);
        if (!grew) {          /* GCOVR_EXCL_BR_LINE: allocation size overflow */
            PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
            return NULL;      /* GCOVR_EXCL_LINE */
        }
        xhtml_ns_slot *grown = PyMem_Calloc(1, bytes);
        if (grown == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure */
            PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
            return NULL;      /* GCOVR_EXCL_LINE */
        }
        xhtml_ns_slot *slots = scope->slots;
        size_t slot_capacity = scope->slot_capacity;
        scope->slots = grown;
        scope->slot_capacity = capacity;
        for (size_t index = 0; index < slot_capacity; index++) {
            if (slots[index].atom != TH_ATTR_UNKNOWN) {
                *xhtml_ns_slot_of(scope, slots[index].atom) = slots[index];
            }
        }
        PyMem_Free(slots);
    }
    xhtml_ns_slot *slot = xhtml_ns_slot_of(scope, atom);
    if (slot->atom == TH_ATTR_UNKNOWN) {
        slot->atom = atom;
        scope->slot_count++;
    }
    return slot;
}

static int xhtml_ns_enter(xhtml_ns *scope, const th_node *node) {
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        const th_node_attr *attr = &node->attrs[index];
        Py_ssize_t len;
        const char *name = th_attr_name(scope->index->tree, attr->name_atom, &len);
        if (len < 5 || memcmp(name, "xmlns", 5) != 0 || (len != 5 && name[5] != ':')) {
            continue;
        }
        xhtml_ns_slot *slot = xhtml_ns_claim(scope, attr->name_atom);
        if (slot == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;      /* GCOVR_EXCL_LINE: allocation failure */
        }
        if (scope->frame_count == scope->frame_capacity) {
            size_t capacity;
            size_t bytes;
            const int grew = th_grow_cap(scope->frame_count + 1, scope->frame_capacity, 8, sizeof(xhtml_ns_frame),
                                         &capacity, &bytes);
            if (!grew) {          /* GCOVR_EXCL_BR_LINE: allocation size overflow */
                PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
                return -1;        /* GCOVR_EXCL_LINE */
            }
            xhtml_ns_frame *grown = PyMem_Realloc(scope->frames, bytes);
            if (grown == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure */
                PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
                return -1;        /* GCOVR_EXCL_LINE */
            }
            scope->frames = grown;
            scope->frame_capacity = capacity;
        }
        scope->frames[scope->frame_count++] = (xhtml_ns_frame){node, attr->name_atom, slot->attr};
        slot->attr = attr;
    }
    return 0;
}

static void xhtml_ns_leave(xhtml_ns *scope, const th_node *node) {
    while (scope->frame_count != 0 && scope->frames[scope->frame_count - 1].node == node) {
        const xhtml_ns_frame *frame = &scope->frames[--scope->frame_count];
        xhtml_ns_slot_of(scope, frame->atom)->attr = frame->previous;
    }
}

static void xhtml_ns_free(xhtml_ns *scope) {
    PyMem_Free(scope->slots);
    PyMem_Free(scope->frames);
}

/* The declaration stored under `atom` that is in scope at the element the walk stands on, NULL when there is none;
   `unbound` keeps that answer, so a prefix nothing declares is not searched for again. Returns -1 on allocation
   failure. */
static int xhtml_ns_binding(xhtml_ns *scope, uint32_t atom, const th_node_attr **binding) {
    static const th_node_attr unbound = {0};
    *binding = NULL;
    if (atom == UINT32_MAX) {
        return 0;
    }
    xhtml_ns_slot *slot = xhtml_ns_claim(scope, atom);
    if (slot == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;      /* GCOVR_EXCL_LINE: allocation failure */
    }
    if (slot->attr == NULL) {
        const th_node_attr *inherited;
        const int climbed = ns_climb_attr(scope->index, scope->div, atom, &inherited);
        if (climbed < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;     /* GCOVR_EXCL_LINE: allocation failure */
        }
        slot->attr = inherited != NULL ? inherited : &unbound;
    }
    if (slot->attr != &unbound) {
        *binding = slot->attr;
    }
    return 0;
}

static int xhtml_namespace_is(xhtml_ns *scope, const th_node *node, Py_ssize_t prefix_len) {
    uint32_t atom;
    const th_node_attr *attr;
    /* GCOVR_EXCL_BR_START: allocation failure */
    if (tag_xmlns_atom(scope->index->tree, node, prefix_len, &atom) < 0 || xhtml_ns_binding(scope, atom, &attr) < 0) {
        return -1; /* GCOVR_EXCL_LINE */
    }
    /* GCOVR_EXCL_BR_STOP */
    return attr != NULL && ucs4_eq_ascii(attr->value, attr->value_len, XHTML_URI);
}

static int xhtml_element(xhtml_ns *scope, th_node *node, th_node *element) {
    *element = *node;
    for (Py_ssize_t index = 0; index < node->text_len; index++) {
        if (node->text[index] != ':') {
            continue;
        }
        int xhtml = xhtml_namespace_is(scope, node, index);
        if (xhtml < 0) { /* GCOVR_EXCL_BR_LINE: namespace lookup fails only on allocation */
            return -1;   /* GCOVR_EXCL_LINE: allocation failure */
        }
        if (xhtml) {
            element->text += index + 1;
            element->text_len -= index + 1;
        }
        return xhtml;
    }
    return xhtml_namespace_is(scope, node, 0);
}

/* Returns 1 when nothing binds the prefix, -1 on allocation failure, and 2 when the copies (counted in *copied) would
   outgrow the allowance the output funds. A NULL copied leaves an inherited declaration out. */
static int xhtml_inherit_prefix(sbuf *out, xhtml_ns *scope, th_node *node, const char *prefix, Py_ssize_t prefix_len,
                                Py_ssize_t *copied) {
    if (prefix_len == 3 && memcmp(prefix, "xml", 3) == 0) {
        return 0;
    }
    uint32_t atom;
    const th_node_attr *attr;
    /* GCOVR_EXCL_BR_START: allocation failure */
    if (xmlns_atom(scope->index->tree, prefix, NULL, prefix_len, &atom) < 0 ||
        xhtml_ns_binding(scope, atom, &attr) < 0) {
        return -1; /* GCOVR_EXCL_LINE */
    }
    /* GCOVR_EXCL_BR_STOP */
    if (attr == NULL || attr->value == NULL || attr->value_len == 0) {
        return 1;
    }
    /* a binding `node` declares itself is written with its other attributes; one it inherits is repeated here */
    if ((uintptr_t)attr - (uintptr_t)node->attrs >= (uintptr_t)node->attr_count * sizeof(*attr) && copied != NULL) {
        Py_ssize_t start = out->len;
        if (!xml_ns_copy_funded(*copied, start, prefix_len + attr->value_len + 10, TH_XML_NS_FEED_BASE)) {
            return 2;
        }
        sbuf_puts(out, " xmlns:");
        sbuf_put_utf8(out, prefix, prefix_len);
        sbuf_puts(out, "=\"");
        sbuf_put_xml_text(out, attr->value, attr->value_len, 1);
        sbuf_putc(out, '"');
        *copied += out->len - start; /* what was written: escaping can lengthen the value */
    }
    return 0;
}

enum { XHTML_INLINE_PREFIXES = 8 };

typedef struct {
    const char *names[XHTML_INLINE_PREFIXES];
    Py_ssize_t lengths[XHTML_INLINE_PREFIXES];
    Py_ssize_t inline_count;
    PyObject *overflow;
} xhtml_prefix_set;

/* 1 when the key was new, 0 when the set already held it, -1 on allocation failure. */
static int xhtml_prefix_set_add(PyObject *set, const char *prefix, Py_ssize_t prefix_len) {
    PyObject *key = PyBytes_FromStringAndSize(prefix, prefix_len);
    if (key == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;     /* GCOVR_EXCL_LINE: allocation failure */
    }
    Py_ssize_t before = PySet_GET_SIZE(set);
    TH_WORK_STEP();
    int added = PySet_Add(set, key);
    Py_DECREF(key);
    if (added < 0) { /* GCOVR_EXCL_BR_LINE: a bytes insert only fails on allocation failure */
        return -1;   /* GCOVR_EXCL_LINE: allocation failure */
    }
    return PySet_GET_SIZE(set) > before;
}

/* 1 when the start tag already carried this prefix, 0 the first time, -1 on allocation failure. The first eight
   distinct prefixes are compared in place, so an ordinary start tag allocates nothing. A tag with more moves them into
   a set once, which keeps the work linear in its attribute count. */
static int xhtml_prefix_seen(xhtml_prefix_set *handled, const char *prefix, Py_ssize_t prefix_len) {
    for (Py_ssize_t index = 0; index < handled->inline_count; index++) {
        TH_WORK_STEP();
        if (handled->lengths[index] == prefix_len && memcmp(handled->names[index], prefix, (size_t)prefix_len) == 0) {
            return 1;
        }
    }
    if (handled->overflow == NULL && handled->inline_count < XHTML_INLINE_PREFIXES) {
        handled->names[handled->inline_count] = prefix;
        handled->lengths[handled->inline_count] = prefix_len;
        handled->inline_count++;
        return 0;
    }
    if (handled->overflow == NULL) {
        handled->overflow = PySet_New(NULL);
        if (handled->overflow == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;                   /* GCOVR_EXCL_LINE: allocation failure */
        }
        for (Py_ssize_t index = 0; index < handled->inline_count; index++) {
            int added = xhtml_prefix_set_add(handled->overflow, handled->names[index], handled->lengths[index]);
            if (added < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                return -1;   /* GCOVR_EXCL_LINE: allocation failure */
            }
        }
        handled->inline_count = 0;
    }
    int added = xhtml_prefix_set_add(handled->overflow, prefix, prefix_len);
    if (added < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;   /* GCOVR_EXCL_LINE: allocation failure */
    }
    return !added;
}

static int xhtml_open_tag(sbuf *out, xhtml_ns *scope, th_node *node, th_node *element, Py_ssize_t *copied) {
    th_tree *tree = scope->index->tree;
    sbuf_putc(out, '<');
    sbuf_put_ucs4(out, element->text, element->text_len);
    Py_ssize_t tag_prefix_len = 0;
    const char *tag_prefix = NULL;
    PyObject *tag_prefix_owner = NULL;
    if (element->text == node->text) {
        for (Py_ssize_t index = 0; index < element->text_len; index++) {
            if (element->text[index] == ':') {
                tag_prefix_owner = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, element->text, index);
                if (tag_prefix_owner == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                    return -1;                  /* GCOVR_EXCL_LINE: allocation failure */
                }
                tag_prefix = PyUnicode_AsUTF8AndSize(tag_prefix_owner, &tag_prefix_len);
                if (tag_prefix == NULL) {        /* GCOVR_EXCL_BR_LINE: allocation failure */
                    Py_DECREF(tag_prefix_owner); /* GCOVR_EXCL_LINE: allocation failure */
                    return -1;                   /* GCOVR_EXCL_LINE: allocation failure */
                }
                int unbound = xhtml_inherit_prefix(out, scope, node, tag_prefix, tag_prefix_len, copied);
                if (unbound != 0) {
                    Py_DECREF(tag_prefix_owner);
                    return unbound;
                }
                break;
            }
        }
    }
    xhtml_prefix_set handled;
    handled.inline_count = 0;
    handled.overflow = NULL;
    for (Py_ssize_t index = 0; index < element->attr_count; index++) {
        th_node_attr *attr = &element->attrs[index];
        Py_ssize_t name_len;
        const char *name = th_attr_name(tree, attr->name_atom, &name_len);
        const char *colon = memchr(name, ':', (size_t)name_len);
        if (colon != NULL && !(name_len >= 6 && memcmp(name, "xmlns:", 6) == 0)) {
            Py_ssize_t prefix_len = colon - name;
            int already_bound = tag_prefix_len == prefix_len && memcmp(tag_prefix, name, (size_t)prefix_len) == 0;
            if (!already_bound) {
                int seen = xhtml_prefix_seen(&handled, name, prefix_len);
                if (seen < 0) {                   /* GCOVR_EXCL_BR_LINE: allocation failure */
                    Py_XDECREF(handled.overflow); /* GCOVR_EXCL_LINE: allocation failure */
                    Py_XDECREF(tag_prefix_owner); /* GCOVR_EXCL_LINE: allocation failure */
                    return -1;                    /* GCOVR_EXCL_LINE: allocation failure */
                }
                int unbound = seen ? 0 : xhtml_inherit_prefix(out, scope, node, name, prefix_len, copied);
                if (unbound != 0) {
                    Py_XDECREF(handled.overflow);
                    Py_XDECREF(tag_prefix_owner);
                    return unbound;
                }
            }
        }
        sbuf_putc(out, ' ');
        sbuf_put_utf8(out, name, name_len);
        sbuf_puts(out, "=\"");
        sbuf_put_xml_text(out, attr->value, attr->value_len, 1);
        sbuf_putc(out, '"');
    }
    Py_XDECREF(handled.overflow);
    Py_XDECREF(tag_prefix_owner);
    return 0;
}

static void xhtml_close_tag(sbuf *out, th_node *element) {
    sbuf_puts(out, "</");
    sbuf_put_ucs4(out, element->text, element->text_len);
    sbuf_putc(out, '>');
}

static int xhtml_void_tag(th_node *node) {
    static const feed_tag VOID[] = {{"area", 4},   {"base", 4},  {"basefont", 8}, {"bgsound", 7}, {"br", 2},
                                    {"col", 3},    {"embed", 5}, {"hr", 2},       {"img", 3},     {"input", 5},
                                    {"keygen", 6}, {"link", 4},  {"meta", 4},     {"param", 5},   {"source", 6},
                                    {"track", 5},  {"wbr", 3}};
    for (size_t index = 0; index < sizeof(VOID) / sizeof(VOID[0]); index++) {
        if (tag_is(node, VOID[index].name, VOID[index].len)) {
            return 1;
        }
    }
    return 0;
}

static int atom_xml_media_type(const Py_UCS4 *value, Py_ssize_t len) {
    Py_ssize_t media_len = 0;
    while (media_len < len && value[media_len] != ';') {
        media_len++;
    }
    return media_len >= 4 && (value[media_len - 4] == '/' || value[media_len - 4] == '+') &&
           (value[media_len - 3] | 0x20) == 'x' && (value[media_len - 2] | 0x20) == 'm' &&
           (value[media_len - 1] | 0x20) == 'l';
}

static int atom_base64_media_type(const Py_UCS4 *value, Py_ssize_t len) {
    for (Py_ssize_t position = 0; position < len && value[position] != ';'; position++) {
        if (value[position] == '/') {
            return !ucs4_ieq(value, position, "text", 4);
        }
    }
    return 0;
}

static int atom_base64_digit(Py_UCS4 character) {
    if (character >= 'A' && character <= 'Z') {
        return (int)(character - 'A');
    }
    if (character >= 'a' && character <= 'z') {
        return (int)(character - 'a') + 26;
    }
    if (character >= '0' && character <= '9') {
        return (int)(character - '0') + 52;
    }
    if (character == '+') {
        return 62;
    }
    if (character == '/') {
        return 63;
    }
    return -1;
}

static PyObject *atom_base64_content(PyObject *value) {
    int kind = PyUnicode_KIND(value);
    void *data = PyUnicode_DATA(value);
    Py_ssize_t symbols = 0;
    int padding = 0;
    int ended = 0;
    for (Py_ssize_t position = 0; position < PyUnicode_GET_LENGTH(value); position++) {
        Py_UCS4 character = PyUnicode_READ(kind, data, position);
        if (character <= ' ') {
            continue;
        }
        if (ended) {
            return Py_NewRef(value);
        }
        if (character == '=') {
            if (symbols % 4 < 2) {
                return Py_NewRef(value);
            }
            padding++;
        } else if (padding != 0 || atom_base64_digit(character) < 0) {
            return Py_NewRef(value);
        }
        symbols++;
        if (padding != 0 && symbols % 4 == 0) {
            ended = 1;
        }
    }
    if (symbols % 4 != 0) {
        return Py_NewRef(value);
    }
    Py_ssize_t size = symbols / 4 * 3 - padding;
    PyObject *decoded = PyBytes_FromStringAndSize(NULL, size);
    if (decoded == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;       /* GCOVR_EXCL_LINE: allocation failure */
    }
    char *out = PyBytes_AS_STRING(decoded);
    Py_ssize_t written = 0;
    unsigned int group = 0;
    int position_in_group = 0;
    for (Py_ssize_t position = 0; position < PyUnicode_GET_LENGTH(value); position++) {
        Py_UCS4 character = PyUnicode_READ(kind, data, position);
        if (character <= ' ') {
            continue;
        }
        group = group << 6 | (unsigned int)(character == '=' ? 0 : atom_base64_digit(character));
        if (++position_in_group == 4) {
            out[written++] = (char)(group >> 16);
            if (written < size) {
                out[written++] = (char)(group >> 8);
            }
            if (written < size) {
                out[written++] = (char)group;
            }
            group = 0;
            position_in_group = 0;
        }
    }
    return decoded;
}

static PyObject *atom_xhtml_text(ns_index *ns, th_node *node, int binary) {
    th_tree *tree = ns->tree;
    if (!th_tree_is_xml(tree) || node->attr_count == 0) {
        return node_text_trimmed(tree, node);
    }
    Py_ssize_t type = th_node_attr_find(tree, node, "type", 4);
    if (type < 0) {
        return node_text_trimmed(tree, node);
    }
    if (atom_xml_media_type(node->attrs[type].value, node->attrs[type].value_len)) {
        Py_ssize_t len;
        Py_UCS4 *value = th_node_inner_xml_construct(tree, node, &len);
        if (value == NULL && PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: NULL with no exception is allocation failure */
            /* the inherited namespace declarations outgrew their allowance: keep the markup, which is the content
               here, and leave the copies out rather than fail the feed or flatten it to text */
            PyErr_Clear();
            value = th_node_inner_xml_local(tree, node, &len);
        }
        if (value == NULL) {         /* GCOVR_EXCL_BR_LINE: serialization fails only on allocation from here */
            return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation failure */
        }
        PyObject *result = ucs4_trimmed(value, len);
        PyMem_Free(value);
        return result;
    }
    if (binary && atom_base64_media_type(node->attrs[type].value, node->attrs[type].value_len)) {
        PyObject *value = node_text_trimmed(tree, node);
        if (value == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return NULL;     /* GCOVR_EXCL_LINE: allocation failure */
        }
        if (PyUnicode_GET_LENGTH(value) == 0) {
            return value;
        }
        PyObject *decoded = atom_base64_content(value);
        Py_DECREF(value);
        return decoded;
    }
    if (!ucs4_eq_ascii(node->attrs[type].value, node->attrs[type].value_len, "xhtml")) {
        return node_text_trimmed(tree, node);
    }
    th_node *div = NULL;
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        if (child->type == TH_NODE_ELEMENT) {
            div = child;
            break;
        }
    }
    if (div == NULL) {
        return node_text_trimmed(tree, node);
    }
    int valid = qualified_tag_is(ns, div, "div", XHTML_URI);
    if (valid < 0) { /* GCOVR_EXCL_BR_LINE: namespace lookup fails only on allocation */
        return NULL; /* GCOVR_EXCL_LINE: allocation failure */
    }
    if (!valid) {
        return node_text_trimmed(tree, node);
    }
    sbuf out = {NULL, 0, 0, 0, 0};
    xhtml_ns scope = {ns, div, NULL, 0, 0, NULL, 0, 0};
    Py_ssize_t copied = 0;
    Py_ssize_t *spend = &copied;
    th_serialize_opts opts = {0};
    opts.xml = 1;
    for (th_node *child = div->first_child; child != NULL;) {
        if (child->type == TH_NODE_ELEMENT) {
            th_node element;
            /* GCOVR_EXCL_BR_START: allocation failure */
            int xhtml = xhtml_ns_enter(&scope, child) < 0 ? -1 : xhtml_element(&scope, child, &element);
            if (xhtml < 0) {
                xhtml_ns_free(&scope); /* GCOVR_EXCL_LINE */
                PyMem_Free(out.data);  /* GCOVR_EXCL_LINE */
                return NULL;           /* GCOVR_EXCL_LINE */
            }
            /* GCOVR_EXCL_BR_STOP */
            int opened = xhtml_open_tag(&out, &scope, child, &element, spend);
            if (opened == 2) {
                /* the inherited namespace declarations outgrew their allowance: start over and leave the copies out,
                   as for XML content */
                xhtml_ns_free(&scope);
                scope = (xhtml_ns){ns, div, NULL, 0, 0, NULL, 0, 0};
                out.len = 0;
                spend = NULL;
                child = div->first_child;
                continue;
            }
            if (opened != 0) {
                xhtml_ns_free(&scope);
                PyMem_Free(out.data);
                return opened < 0 ? NULL : node_text_trimmed(tree, node); /* GCOVR_EXCL_BR_LINE: allocation failure */
            }
            if (child->first_child != NULL) {
                sbuf_putc(&out, '>');
                child = child->first_child;
                continue;
            }
            if (xhtml && !xhtml_void_tag(&element)) {
                sbuf_putc(&out, '>');
                xhtml_close_tag(&out, &element);
            } else {
                sbuf_puts(&out, "/>");
            }
            xhtml_ns_leave(&scope, child);
        }
        if (child->type == TH_NODE_TEXT || child->type == TH_NODE_CDATA) {
            Py_ssize_t len;
            Py_UCS4 *data = th_node_data(tree, child, &len);
            if (data == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure */
                xhtml_ns_free(&scope);   /* GCOVR_EXCL_LINE: allocation failure */
                PyMem_Free(out.data);    /* GCOVR_EXCL_LINE: allocation failure */
                return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation failure */
            }
            sbuf_put_xml_text(&out, data, len, 0);
            PyMem_Free(data);
        } else if (child->type == TH_NODE_COMMENT || child->type == TH_NODE_PI) {
            Py_ssize_t len;
            Py_UCS4 *data = th_node_serialize(tree, child, &opts, NULL, 0, &len);
            if (data == NULL) {          /* GCOVR_EXCL_BR_LINE: serialization fails only on allocation */
                xhtml_ns_free(&scope);   /* GCOVR_EXCL_LINE: allocation failure */
                PyMem_Free(out.data);    /* GCOVR_EXCL_LINE: allocation failure */
                return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation failure */
            }
            sbuf_put_ucs4(&out, data, len);
            PyMem_Free(data);
        }
        while (child->next_sibling == NULL && child->parent != div) {
            child = child->parent;
            th_node element;
            if (xhtml_element(&scope, child, &element) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                xhtml_ns_free(&scope);                        /* GCOVR_EXCL_LINE: allocation failure */
                PyMem_Free(out.data);                         /* GCOVR_EXCL_LINE: allocation failure */
                return NULL;                                  /* GCOVR_EXCL_LINE: allocation failure */
            }
            xhtml_close_tag(&out, &element);
            xhtml_ns_leave(&scope, child);
        }
        child = child->next_sibling;
    }
    xhtml_ns_free(&scope);
    if (out.failed) {            /* GCOVR_EXCL_BR_LINE: allocation failure */
        PyMem_Free(out.data);    /* GCOVR_EXCL_LINE: allocation failure */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation failure */
    }
    PyObject *result = ucs4_trimmed(out.data, out.len);
    PyMem_Free(out.data);
    return result;
}

/* The first non-empty trimmed text among `parent`'s direct children named by the precedence-ordered `tags`, or None
   when none is present with a non-empty value (or when `parent` is NULL, a feed whose channel the parser dropped). NULL
   only on the excluded allocation-failure path. */
static PyObject *field_text(ns_index *ns, th_node *parent, const feed_tag *tags, size_t count, const char *uri,
                            int inherited, int xhtml) {
    th_tree *tree = ns->tree;
    if (parent == NULL) {
        return Py_NewRef(Py_None);
    }
    for (size_t index = 0; index < count; index++) {
        th_node *child = NULL;
        if (th_tree_is_xml(tree) && tags[index].len >= 7 && memcmp(tags[index].name, "dc:", 3) == 0) {
            for (th_node *candidate = parent->first_child; candidate != NULL; candidate = candidate->next_sibling) {
                if (candidate->type != TH_NODE_ELEMENT || candidate->text_len < tags[index].len - 3) {
                    continue;
                }
                int match = qualified_tag_is(ns, candidate, tags[index].name + 3, DC_URI);
                if (match < 0) { /* GCOVR_EXCL_BR_LINE: namespace_is fails only on allocation */
                    return NULL; /* GCOVR_EXCL_LINE: allocation failure */
                }
                if (match) {
                    child = candidate;
                    break;
                }
            }
        } else {
            int error = 0;
            const char *name = tags[index].name;
            if (th_tree_is_xml(tree) && uri != ATOM_URI) {
                if (tags[index].len == 13) {
                    name = "lastBuildDate";
                } else if (tags[index].len == 7 && name[0] == 'p') {
                    name = "pubDate";
                }
            }
            child = feed_child_named(ns, parent, name, tags[index].len, uri, inherited, &error);
            if (error) {     /* GCOVR_EXCL_BR_LINE: namespace_is fails only on allocation */
                return NULL; /* GCOVR_EXCL_LINE: allocation failure */
            }
        }
        if (child == NULL) {
            continue;
        }
        PyObject *value = xhtml ? atom_xhtml_text(ns, child, 0) : node_text_trimmed(tree, child);
        if (value == NULL) { /* GCOVR_EXCL_BR_LINE: node_text_trimmed fails only on allocation */
            return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (PyUnicode_GET_LENGTH(value) > 0) {
            return value;
        }
        Py_DECREF(value);
    }
    return Py_NewRef(Py_None);
}

static PyObject *link_href(th_tree *tree, th_node *link, int atom) {
    Py_ssize_t index = th_node_attr_find(tree, link, "href", 4);
    if (index < 0 || link->attrs[index].value == NULL) {
        return NULL;
    }
    PyObject *href = ucs4_trimmed(link->attrs[index].value, link->attrs[index].value_len);
    if (href == NULL) { /* GCOVR_EXCL_BR_LINE: ucs4_trimmed fails only on allocation */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (PyUnicode_GET_LENGTH(href) == 0 && (!atom || link->attrs[index].value_len != 0)) {
        Py_DECREF(href);
        return NULL;
    }
    return href;
}

static int link_is_alternate(th_tree *tree, th_node *link) {
    Py_ssize_t index = th_node_attr_find(tree, link, "rel", 3);
    if (index < 0 || link->attrs[index].value == NULL) {
        return 1;
    }
    return ucs4_ieq(link->attrs[index].value, link->attrs[index].value_len, "alternate", 9);
}

/* Atom serialization compares relation types case-sensitively (RFC 8288 A.2). */
static int atom_link_is_alternate(th_tree *tree, th_node *link) {
    static const Py_UCS4 alternate_uri[] = U"http://www.iana.org/assignments/relation/alternate";
    Py_ssize_t index = th_node_attr_find(tree, link, "rel", 3);
    if (index < 0) {
        return 1;
    }
    if (link->attrs[index].value == NULL) {
        return 0;
    }
    return ucs4_eq_ascii(link->attrs[index].value, link->attrs[index].value_len, "alternate") ||
           (link->attrs[index].value_len == (Py_ssize_t)(sizeof(alternate_uri) / sizeof(*alternate_uri) - 1) &&
            memcmp(link->attrs[index].value, alternate_uri, sizeof(alternate_uri) - sizeof(*alternate_uri)) == 0);
}

/* The trimmed URL an RSS/RDF <link> without an href holds: its own text in an XML tree, the next text sibling in an
   HTML tree (where <link> is void, so the parser moves the URL out of it), empty when there is none. NULL only on the
   excluded allocation-failure path. */
static PyObject *link_text(th_tree *tree, th_node *link) {
    if (th_tree_is_xml(tree)) {
        return node_text_trimmed(tree, link);
    }
    th_node *sibling = link->next_sibling;
    if (sibling == NULL || sibling->type != TH_NODE_TEXT) {
        return PyUnicode_New(0, 0);
    }
    Py_ssize_t len;
    Py_UCS4 *data = th_node_data(tree, sibling, &len);
    if (data == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *text = ucs4_trimmed(data, len);
    PyMem_Free(data);
    return text;
}

static int absolute_url(PyObject *url) {
    if (PyUnicode_GET_LENGTH(url) == 0) {
        return 0;
    }
    int kind = PyUnicode_KIND(url);
    const void *data = PyUnicode_DATA(url);
    if (!is_ascii_alpha(PyUnicode_READ(kind, data, 0))) {
        return 0;
    }
    for (Py_ssize_t index = 1; index < PyUnicode_GET_LENGTH(url); index++) {
        Py_UCS4 point = PyUnicode_READ(kind, data, index);
        if (point == ':') {
            return 1;
        }
        if (!is_ascii_alpha(point) && !is_ascii_digit(point) && point != '+' && point != '-' && point != '.') {
            break;
        }
    }
    return 0;
}

static PyObject *resolve_link_xml_base(ns_index *ns, th_node *link, PyObject *url) {
    if (!th_tree_is_xml(ns->tree) || absolute_url(url)) {
        return url;
    }
    uint32_t base_atom = th_attr_lookup(ns->tree, "xml:base", 8);
    PyObject *base_uri = NULL;
    for (th_node *node = link; base_atom != UINT32_MAX && node != NULL; node = node->parent) {
        TH_WORK_STEP();
        if (node->type != TH_NODE_ELEMENT) {
            continue;
        }
        const th_node_attr *attr;
        if (ns_own_attr(ns, node, base_atom, &attr) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            Py_XDECREF(base_uri);                          /* GCOVR_EXCL_LINE: allocation failure */
            Py_DECREF(url);                                /* GCOVR_EXCL_LINE: allocation failure */
            return NULL;                                   /* GCOVR_EXCL_LINE: allocation failure */
        }
        if (attr == NULL) {
            continue;
        }
        if (attr->value_len > TH_BASE_URL_MAX) {
            continue; /* as for <base href>: every link of the feed would carry it, so one this long is ignored */
        }
        PyObject *joined = base_uri == NULL
                               ? PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, attr->value, attr->value_len)
                               : th_url_join_c14n_raw(attr->value, attr->value_len, base_uri);
        Py_XDECREF(base_uri);
        if (joined == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            Py_DECREF(url);   /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        base_uri = joined;
        if (PyUnicode_GET_LENGTH(base_uri) > TH_BASE_URL_MAX) {
            /* nested relative values that add up past the limit: stop before the next level joins onto them */
            Py_DECREF(base_uri);
            return url;
        }
    }
    if (base_uri == NULL) {
        return url;
    }
    PyObject *result = th_url_join(base_uri, url);
    Py_DECREF(base_uri);
    Py_DECREF(url);
    return result;
}

static PyObject *extract_link(ns_index *ns, th_node *parent, const char *uri, int inherited) {
    th_tree *tree = ns->tree;
    if (parent == NULL) {
        return Py_NewRef(Py_None);
    }
    PyObject *fallback = NULL;
    int atom = uri == ATOM_URI;
    for (th_node *child = parent->first_child; child != NULL; child = child->next_sibling) {
        if (child->type != TH_NODE_ELEMENT) {
            continue;
        }
        int match = feed_tag_is(ns, child, "link", 4, uri, inherited);
        if (match < 0) {          /* GCOVR_EXCL_BR_LINE: namespace_is fails only on allocation */
            Py_XDECREF(fallback); /* GCOVR_EXCL_LINE: allocation failure */
            return NULL;          /* GCOVR_EXCL_LINE: allocation failure */
        }
        if (!match) {
            continue;
        }
        if (atom && !atom_link_is_alternate(tree, child)) {
            continue;
        }
        PyObject *href = link_href(tree, child, atom);
        if (href != NULL) {
            href = resolve_link_xml_base(ns, child, href);
            if (href == NULL) {       /* GCOVR_EXCL_BR_LINE: allocation failure */
                Py_XDECREF(fallback); /* GCOVR_EXCL_LINE: allocation-failure path */
                return NULL;          /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            if (atom || link_is_alternate(tree, child)) {
                Py_XDECREF(fallback);
                return href;
            }
            if (fallback == NULL) {
                fallback = href;
            } else {
                Py_DECREF(href);
            }
            continue;
        }
        if (atom) {
            continue;
        }
        PyObject *text = link_text(tree, child);
        if (text == NULL) {       /* GCOVR_EXCL_BR_LINE: link_text fails only on allocation */
            Py_XDECREF(fallback); /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;          /* GCOVR_EXCL_LINE */
        }
        if (PyUnicode_GET_LENGTH(text) > 0) {
            Py_XDECREF(fallback);
            return resolve_link_xml_base(ns, child, text);
        }
        Py_DECREF(text);
    }
    if (fallback != NULL) {
        return fallback;
    }
    return Py_NewRef(Py_None);
}

/* Adjacent fields follow fallback precedence. */
enum {
    FEED_TITLE,
    FEED_DC_TITLE,
    FEED_GUID,
    FEED_ID,
    FEED_UPDATED,
    FEED_LAST_BUILD,
    FEED_PUBLISHED,
    FEED_PUBDATE,
    FEED_DC_DATE,
    FEED_SUMMARY,
    FEED_DESCRIPTION,
    FEED_DC_DESCRIPTION,
    FEED_ENCODED,
    FEED_CONTENT,
    FEED_AUTHOR,
    FEED_CREATOR,
    FEED_SOURCE,
    FEED_FIELD_COUNT
};

static const feed_tag ENTRY_TAGS[FEED_FIELD_COUNT] = {{"title", 5},
                                                      {"dc:title", 8},
                                                      {"guid", 4},
                                                      {"id", 2},
                                                      {"updated", 7},
                                                      {"lastbuilddate", 13},
                                                      {"published", 9},
                                                      {"pubdate", 7},
                                                      {"dc:date", 7},
                                                      {"summary", 7},
                                                      {"description", 11},
                                                      {"dc:description", 14},
                                                      {"content:encoded", 15},
                                                      {"content", 7},
                                                      {"author", 6},
                                                      {"dc:creator", 10},
                                                      {"source", 6}};

/* Fields an RSS module namespace defines, matched by local name and namespace whatever the prefix. */
static const struct {
    const char *local;
    const char *uri;
    int field;
} ENTRY_MODULES[] = {{"title", DC_URI, FEED_DC_TITLE},
                     {"date", DC_URI, FEED_DC_DATE},
                     {"description", DC_URI, FEED_DC_DESCRIPTION},
                     {"creator", DC_URI, FEED_CREATOR},
                     {"encoded", CONTENT_URI, FEED_ENCODED}};

/* The only field a tag of this length can name, before the name itself is compared; -1 for none. */
static int entry_field_by_length(const th_node *child) {
    switch (child->text_len) {
    case 2:
        return FEED_ID;
    case 4:
        return FEED_GUID;
    case 5:
        return FEED_TITLE;
    case 6:
        return lower_ascii(child->text[0]) == 's' ? FEED_SOURCE : FEED_AUTHOR;
    case 7:
        switch (lower_ascii(child->text[0])) {
        case 'u':
            return FEED_UPDATED;
        case 'p':
            return FEED_PUBDATE;
        case 'd':
            return FEED_DC_DATE;
        case 's':
            return FEED_SUMMARY;
        case 'c':
            return FEED_CONTENT;
        default:
            return -1;
        }
    case 8:
        return FEED_DC_TITLE;
    case 9:
        return FEED_PUBLISHED;
    case 10:
        return FEED_CREATOR;
    case 11:
        return FEED_DESCRIPTION;
    case 13:
        return FEED_LAST_BUILD;
    case 14:
        return FEED_DC_DESCRIPTION;
    case 15:
        return FEED_ENCODED;
    default:
        return -1;
    }
}

/* A field's tag name; XML RSS spells the two compound names in camelCase. */
static const char *entry_field_name(int field, int camel_case) {
    if (camel_case && field == FEED_LAST_BUILD) {
        return "lastBuildDate";
    }
    if (camel_case && field == FEED_PUBDATE) {
        return "pubDate";
    }
    return ENTRY_TAGS[field].name;
}

/* The first matching element wins each field. */
static void claim_field(th_node **fields, int field, th_node *child) {
    if (fields[field] == NULL) {
        fields[field] = child;
    }
}

/* Claim the field a child's unprefixed (or dc:/content:-spelled) tag names, once its namespace checks out. Returns -1
   on allocation failure. */
static int claim_named_field(ns_index *ns, th_node *child, th_node **fields, int field, const char *uri, int inherited,
                             int *dc_namespace, int *content_namespace) {
    th_tree *tree = ns->tree;
    if (!th_tree_is_xml(tree)) {
        claim_field(fields, field, child);
        return 0;
    }
    if ((field == FEED_TITLE && uri != ATOM_URI && child->attr_count != 0) || field == FEED_DESCRIPTION) {
        int match = qualified_tag_is(ns, child, field == FEED_TITLE ? "title" : "description", DC_URI);
        if (match < 0) { /* GCOVR_EXCL_BR_LINE: namespace_is fails only on allocation */
            return -1;   /* GCOVR_EXCL_LINE: allocation failure */
        }
        if (match) {
            claim_field(fields, field == FEED_TITLE ? FEED_DC_TITLE : FEED_DC_DESCRIPTION, child);
            return 0;
        }
    }
    if (field == FEED_DC_TITLE || field == FEED_DC_DATE || field == FEED_DC_DESCRIPTION || field == FEED_CREATOR ||
        field == FEED_ENCODED) {
        int *cached = field == FEED_ENCODED ? content_namespace : dc_namespace;
        int match =
            child->attr_count == 0 && *cached != -2
                ? *cached
                : namespace_is(ns, child, field == FEED_ENCODED ? 7 : 2, field == FEED_ENCODED ? CONTENT_URI : DC_URI);
        if (match < 0) { /* GCOVR_EXCL_BR_LINE: namespace_is fails only on allocation */
            return -1;   /* GCOVR_EXCL_LINE: allocation failure */
        }
        if (child->attr_count == 0) {
            *cached = match;
        }
        if (match) {
            claim_field(fields, field, child);
        }
        return 0;
    }
    if ((!inherited || child->attr_count != 0) &&
        !(uri == NULL ? empty_namespace_at_node(tree, child, inherited)
                      : default_namespace_at_node(tree, child, uri, inherited))) {
        return 0;
    }
    claim_field(fields, field, child);
    return 0;
}

/* Claim the field a prefixed child names when its prefix binds the feed's own namespace (atom:title). Returns 1 when
   it claimed one, 0 when not, -1 on allocation failure. */
static int claim_prefixed_field(ns_index *ns, th_node *child, th_node **fields, const char *uri) {
    Py_ssize_t colon = 1;
    while (colon < child->text_len && child->text[colon] != ':') {
        colon++;
    }
    if (colon >= child->text_len) {
        return 0;
    }
    int match = namespace_is(ns, child, colon, uri);
    if (match < 0) { /* GCOVR_EXCL_BR_LINE: namespace_is fails only on allocation */
        return -1;   /* GCOVR_EXCL_LINE: allocation failure */
    }
    if (!match) {
        return 0;
    }
    const Py_UCS4 *local = child->text + colon + 1;
    Py_ssize_t local_len = child->text_len - colon - 1;
    for (int index = 0; index < FEED_FIELD_COUNT; index++) {
        if (memchr(ENTRY_TAGS[index].name, ':', (size_t)ENTRY_TAGS[index].len) != NULL) {
            continue;
        }
        if (uri == ATOM_URI ? ucs4_ieq(local, local_len, ENTRY_TAGS[index].name, ENTRY_TAGS[index].len)
                            : ucs4_eq_ascii_len(local, local_len, entry_field_name(index, 1), ENTRY_TAGS[index].len)) {
            claim_field(fields, index, child);
            return 1;
        }
    }
    return 0;
}

/* Claim the module field (dc:creator, content:encoded) a child names under any prefix. Returns -1 on allocation
   failure. */
static int claim_module_field(ns_index *ns, th_node *child, th_node **fields) {
    for (size_t index = 0; index < sizeof(ENTRY_MODULES) / sizeof(ENTRY_MODULES[0]); index++) {
        int match = qualified_tag_is(ns, child, ENTRY_MODULES[index].local, ENTRY_MODULES[index].uri);
        if (match < 0) { /* GCOVR_EXCL_BR_LINE: namespace_is fails only on allocation */
            return -1;   /* GCOVR_EXCL_LINE: allocation failure */
        }
        if (match) {
            claim_field(fields, ENTRY_MODULES[index].field, child);
            return 0;
        }
    }
    return 0;
}

static int collect_entry_fields(ns_index *ns, th_node *entry, th_node **fields, const char *uri, int inherited) {
    int xml = th_tree_is_xml(ns->tree);
    int dc_namespace = -2;
    int content_namespace = -2;
    for (th_node *child = entry->first_child; child != NULL; child = child->next_sibling) {
        if (child->type != TH_NODE_ELEMENT) {
            continue;
        }
        int field = entry_field_by_length(child);
        if (field >= 0) {
            const char *name = entry_field_name(field, xml && uri != ATOM_URI);
            if (xml ? ucs4_eq_ascii_len(child->text, child->text_len, name, ENTRY_TAGS[field].len)
                    : tag_is(child, name, ENTRY_TAGS[field].len)) {
                int status =
                    claim_named_field(ns, child, fields, field, uri, inherited, &dc_namespace, &content_namespace);
                if (status < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                    return -1;    /* GCOVR_EXCL_LINE */
                }
                continue;
            }
        }
        if (!xml) {
            continue;
        }
        if (uri != NULL) {
            int found = claim_prefixed_field(ns, child, fields, uri);
            if (found < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                return -1;   /* GCOVR_EXCL_LINE */
            }
            if (found) {
                continue;
            }
        }
        if (claim_module_field(ns, child, fields) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;                                   /* GCOVR_EXCL_LINE */
        }
    }
    return 0;
}

static PyObject *entry_field_text(ns_index *ns, th_node *const *fields, size_t count, int atom, int binary) {
    th_tree *tree = ns->tree;
    for (size_t index = 0; index < count; index++) {
        if (fields[index] == NULL) {
            continue;
        }
        PyObject *value =
            atom ? atom_xhtml_text(ns, fields[index], binary && index == 1) : node_text_trimmed(tree, fields[index]);
        if (value == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return NULL;     /* GCOVR_EXCL_LINE: allocation failure */
        }
        if ((binary && PyBytes_Check(value)) || PyUnicode_GET_LENGTH(value) > 0) {
            return value;
        }
        Py_DECREF(value);
    }
    return Py_NewRef(Py_None);
}

static const th_node_attr *rdf_reference_attr(ns_index *ns, th_node *node, const char *local, Py_ssize_t local_len,
                                              int allow_unprefixed, int *error) {
    th_tree *tree = ns->tree;
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        const th_node_attr *attr = &node->attrs[index];
        if (attr->value == NULL) {
            continue;
        }
        Py_ssize_t name_len;
        const char *name = th_attr_name(tree, attr->name_atom, &name_len);
        if (allow_unprefixed && name_len == local_len && memcmp(name, local, (size_t)local_len) == 0) {
            return attr;
        }
        if (name_len <= local_len + 1 || name[name_len - local_len - 1] != ':' ||
            memcmp(name + name_len - local_len, local, (size_t)local_len) != 0) {
            continue;
        }
        if (!th_tree_is_xml(tree)) {
            if (name_len == local_len + 4 && memcmp(name, "rdf:", 4) == 0) {
                return attr;
            }
            continue;
        }
        int bound = namespace_bytes_is(ns, node, name, name_len - local_len - 1, RDF_URI);
        if (bound < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            *error = 1;  /* GCOVR_EXCL_LINE: allocation failure */
            return NULL; /* GCOVR_EXCL_LINE: allocation failure */
        }
        if (bound) {
            return attr;
        }
    }
    return NULL;
}

static PyObject *rdf_reference(ns_index *ns, th_node *node, const char *local, Py_ssize_t local_len,
                               int allow_unprefixed) {
    int error = 0;
    const th_node_attr *attr = rdf_reference_attr(ns, node, local, local_len, allow_unprefixed, &error);
    if (error) {     /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL; /* GCOVR_EXCL_LINE: allocation failure */
    }
    if (attr != NULL) {
        return ucs4_trimmed(attr->value, attr->value_len);
    }
    return Py_NewRef(Py_None);
}

/* The author display name of `parent`: an <author>'s nested <name> (the Atom form), else the <author>'s own text (an
   RSS email), else a <dc:creator>, else None. NULL only on the excluded allocation-failure path. */
static PyObject *extract_author(ns_index *ns, th_node *const *fields, const char *uri, int inherited) {
    th_tree *tree = ns->tree;
    for (size_t index = 0; index < 2; index++) {
        th_node *child = fields[index];
        if (child == NULL) {
            continue;
        }
        int error = 0;
        int author_namespace =
            uri != NULL && th_tree_is_xml(tree) ? default_namespace_at_node(tree, child, uri, inherited) : 1;
        th_node *name = feed_child_named(ns, child, "name", 4, uri, author_namespace, &error);
        if (error) {     /* GCOVR_EXCL_BR_LINE: namespace_is fails only on allocation */
            return NULL; /* GCOVR_EXCL_LINE: allocation failure */
        }
        PyObject *value = node_text_trimmed(tree, name != NULL ? name : child);
        if (value == NULL) { /* GCOVR_EXCL_BR_LINE: node_text_trimmed fails only on allocation */
            return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (PyUnicode_GET_LENGTH(value) > 0) {
            return value;
        }
        Py_DECREF(value);
    }
    return Py_NewRef(Py_None);
}

static PyObject *entry_author(ns_index *ns, th_node *source, th_node *const *fields, const char *uri, int inherited,
                              PyObject *feed_author) {
    th_tree *tree = ns->tree;
    PyObject *author = extract_author(ns, fields, uri, inherited);
    if (author == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;      /* GCOVR_EXCL_LINE: allocation failure */
    }
    if (author != Py_None || uri != ATOM_URI) {
        return author;
    }
    Py_DECREF(author);
    int error = 0;
    if (source != NULL) {
        int source_namespace = default_namespace_at_node(tree, source, ATOM_URI, inherited);
        th_node *source_author = feed_child_named(ns, source, "author", 6, ATOM_URI, source_namespace, &error);
        if (error) {     /* GCOVR_EXCL_BR_LINE: namespace lookup fails only on allocation */
            return NULL; /* GCOVR_EXCL_LINE: allocation failure */
        }
        if (source_author != NULL) {
            th_node *source_fields[2] = {source_author, NULL};
            author = extract_author(ns, source_fields, ATOM_URI, source_namespace);
            if (author == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                return NULL;      /* GCOVR_EXCL_LINE: allocation failure */
            }
            if (author != Py_None) {
                return author;
            }
            Py_DECREF(author);
        }
    }
    return Py_NewRef(feed_author);
}

/* The entry identifier: an RSS <guid> / Atom <id> element text, else the RDF item's rdf:about attribute, else None.
   NULL only on the excluded allocation-failure path. */
static PyObject *extract_id(ns_index *ns, th_node *entry, th_node *const *fields, PyObject *resolved_about) {
    if (resolved_about != NULL && fields[0] == NULL && fields[1] == NULL) {
        return Py_NewRef(resolved_about);
    }
    PyObject *value = entry_field_text(ns, fields, 2, 0, 0);
    if (value == NULL) { /* GCOVR_EXCL_BR_LINE: field_text fails only on allocation */
        return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (value != Py_None) {
        return value;
    }
    Py_DECREF(value);
    value = rdf_reference(ns, entry, "about", 5, 0);
    if (value == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return value == Py_None ? value : resolve_link_xml_base(ns, entry, value);
}

/* When an entry has no link element, an RSS <guid> that is a permalink (its isPermaLink is not "false") doubles as the
   link, mirroring feedparser. Replaces *link with the guid text when one qualifies. -1 only on the excluded
   allocation-failure path. */
static int apply_guid_permalink(th_tree *tree, th_node *guid, PyObject **link) {
    if (*link != Py_None) {
        return 0;
    }
    if (guid == NULL) {
        return 0;
    }
    /* XML keeps the attribute's spelling (isPermaLink) and matches it case-sensitively; HTML lowercases it. */
    Py_ssize_t index = th_node_attr_find(tree, guid, th_tree_is_xml(tree) ? "isPermaLink" : "ispermalink", 11);
    if (index >= 0 && guid->attrs[index].value != NULL &&
        ucs4_ieq(guid->attrs[index].value, guid->attrs[index].value_len, "false", 5)) {
        return 0;
    }
    PyObject *value = node_text_trimmed(tree, guid);
    if (value == NULL) { /* GCOVR_EXCL_BR_LINE: node_text_trimmed fails only on allocation */
        return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (PyUnicode_GET_LENGTH(value) > 0) {
        Py_SETREF(*link, value);
    } else {
        Py_DECREF(value);
    }
    return 0;
}

/* Steal `value` into slot `index` of `record`, returning -1 only on the excluded allocation-failure path (a NULL value
   a field builder could not allocate). */
static int record_set(PyObject *record, Py_ssize_t index, PyObject *value) {
    if (value == NULL) { /* GCOVR_EXCL_BR_LINE: every field builder fails only on unforceable allocation */
        return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyTuple_SET_ITEM(record, index, value);
    return 0;
}

/* The entry's link with the guid-permalink fallback applied, ready for record_set. NULL only on the excluded
   allocation-failure path. */
static PyObject *entry_link(ns_index *ns, th_node *item, th_node *guid, const char *uri, int inherited) {
    PyObject *link = extract_link(ns, item, uri, inherited);
    if (link == NULL) { /* GCOVR_EXCL_BR_LINE: extract_link fails only on allocation */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (apply_guid_permalink(ns->tree, guid, &link) < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        Py_DECREF(link);                                   /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;                                       /* GCOVR_EXCL_LINE */
    }
    return link;
}

/* Build one Entry(title, link, id, updated, published, summary, content, author) from an RSS <item>, an Atom <entry>,
   or an RDF item, each field the first present value in its precedence order. NULL only on the excluded
   allocation-failure path. */
static PyObject *build_entry(module_state *state, ns_index *ns, th_node *item, const char *uri, int inherited,
                             PyObject *resolved_about, PyObject *feed_author) {
    th_node *fields[FEED_FIELD_COUNT] = {0};
    if (collect_entry_fields(ns, item, fields, uri, inherited) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;                                                  /* GCOVR_EXCL_LINE: allocation failure */
    }
    PyObject *record = PyTuple_New(8);
    if (record == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int failed = 0;
    int atom = uri == ATOM_URI;
    failed |= record_set(record, 0, entry_field_text(ns, fields + FEED_TITLE, atom ? 1 : 2, atom, 0));
    failed |= record_set(record, 1, entry_link(ns, item, fields[FEED_GUID], uri, inherited));
    failed |= record_set(record, 2, extract_id(ns, item, fields + FEED_GUID, resolved_about));
    failed |= record_set(record, 3, entry_field_text(ns, fields + FEED_UPDATED, 2, 0, 0));
    failed |= record_set(record, 4, entry_field_text(ns, fields + FEED_PUBLISHED, 3, 0, 0));
    failed |= record_set(record, 5, entry_field_text(ns, fields + FEED_SUMMARY, 3, atom, 0));
    failed |= record_set(record, 6, entry_field_text(ns, fields + FEED_ENCODED, 2, atom, atom));
    failed |=
        record_set(record, 7, entry_author(ns, fields[FEED_SOURCE], fields + FEED_AUTHOR, uri, inherited, feed_author));
    if (failed != 0) {     /* GCOVR_EXCL_BR_LINE: a field build fails only on unforceable allocation */
        Py_DECREF(record); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;       /* GCOVR_EXCL_LINE */
    }
    if (PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: a field may carry an allocation error with its fallback value */
        Py_DECREF(record);  /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;        /* GCOVR_EXCL_LINE */
    }
    PyObject *entry = PyObject_Call(state->entry_type, record, NULL);
    Py_DECREF(record);
    return entry;
}

/* The tuple of Entry records for every direct child of `parent` named [name, name + len) (item for RSS/RDF, entry for
   Atom), an empty tuple when `parent` is NULL. NULL only on the excluded allocation-failure path. */
static PyObject *build_entries(module_state *state, ns_index *ns, th_node *parent, const char *name, Py_ssize_t len,
                               const char *uri, int inherited) {
    th_tree *tree = ns->tree;
    PyObject *list = PyList_New(0);
    if (list == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (parent != NULL) {
        int error = 0;
        th_node *feed_fields[2] = {
            uri == ATOM_URI ? feed_child_named(ns, parent, "author", 6, ATOM_URI, inherited, &error) : NULL, NULL};
        if (error) {         /* GCOVR_EXCL_BR_LINE: namespace lookup fails only on allocation */
            Py_DECREF(list); /* GCOVR_EXCL_LINE: allocation failure */
            return NULL;     /* GCOVR_EXCL_LINE: allocation failure */
        }
        /* Read once and shared by every entry that inherits it: the feed's <author> lies outside the entries, so
           reading it again per entry costs the entry count times its size. */
        PyObject *feed_author = extract_author(ns, feed_fields, ATOM_URI, inherited);
        if (feed_author == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            Py_DECREF(list);       /* GCOVR_EXCL_LINE: allocation failure */
            return NULL;           /* GCOVR_EXCL_LINE: allocation failure */
        }
        for (th_node *child = parent->first_child; child != NULL; child = child->next_sibling) {
            if (child->type != TH_NODE_ELEMENT) {
                continue;
            }
            int match = feed_tag_is(ns, child, name, len, uri, inherited);
            if (match < 0) {            /* GCOVR_EXCL_BR_LINE: namespace_is fails only on allocation */
                Py_DECREF(feed_author); /* GCOVR_EXCL_LINE: allocation failure */
                Py_DECREF(list);        /* GCOVR_EXCL_LINE: allocation failure */
                return NULL;            /* GCOVR_EXCL_LINE: allocation failure */
            }
            if (!match) {
                continue;
            }
            int entry_namespace =
                uri != NULL && th_tree_is_xml(tree) ? default_namespace_at_node(tree, child, uri, inherited) : 1;
            PyObject *entry = build_entry(state, ns, child, uri, entry_namespace, NULL, feed_author);
            if (entry == NULL) {        /* GCOVR_EXCL_BR_LINE: build_entry fails only on allocation */
                Py_DECREF(feed_author); /* GCOVR_EXCL_LINE: allocation-failure path */
                Py_DECREF(list);        /* GCOVR_EXCL_LINE: allocation-failure path */
                return NULL;            /* GCOVR_EXCL_LINE */
            }
            int append_failed = PyList_Append(list, entry) < 0;
            Py_DECREF(entry);
            if (append_failed) {        /* GCOVR_EXCL_BR_LINE: append fails only on unforceable allocation */
                Py_DECREF(feed_author); /* GCOVR_EXCL_LINE: allocation-failure path */
                Py_DECREF(list);        /* GCOVR_EXCL_LINE: allocation-failure path */
                return NULL;            /* GCOVR_EXCL_LINE */
            }
        }
        Py_DECREF(feed_author);
    }
    PyObject *entries = PyList_AsTuple(list);
    Py_DECREF(list);
    return entries;
}

static int absolute_reference(const Py_UCS4 *value, Py_ssize_t len) {
    if (len == 0 || !is_ascii_alpha(value[0])) {
        return 0;
    }
    for (Py_ssize_t index = 1; index < len; index++) {
        if (value[index] == ':') {
            return 1;
        }
        if (!is_ascii_alpha(value[index]) && !is_ascii_digit(value[index]) && value[index] != '+' &&
            value[index] != '-' && value[index] != '.') {
            return 0;
        }
    }
    return 0;
}

static PyObject *build_rdf_entries(module_state *state, ns_index *ns, th_node *root, th_node *channel,
                                   int root_namespace, int channel_namespace) {
    th_tree *tree = ns->tree;
    if (channel == NULL || !th_tree_is_xml(tree)) {
        return build_entries(state, ns, root, "item", 4, RSS_URI, root_namespace);
    }
    int error = 0;
    th_node *items = feed_child_named(ns, channel, "items", 5, RSS_URI, channel_namespace, &error);
    if (error) {     /* GCOVR_EXCL_BR_LINE: namespace lookup fails only on allocation */
        return NULL; /* GCOVR_EXCL_LINE: allocation failure */
    }
    th_node *sequence = NULL;
    if (items != NULL) {
        for (th_node *child = items->first_child; child != NULL; child = child->next_sibling) {
            if (child->type != TH_NODE_ELEMENT) {
                continue;
            }
            int match = qualified_tag_is(ns, child, "Seq", RDF_URI);
            if (match < 0) { /* GCOVR_EXCL_BR_LINE: namespace lookup fails only on allocation */
                return NULL; /* GCOVR_EXCL_LINE: allocation failure */
            }
            if (match) {
                sequence = child;
                break;
            }
        }
    }
    if (sequence == NULL) {
        return build_entries(state, ns, root, "item", 4, RSS_URI, root_namespace);
    }
    int canonical = root_namespace && ucs4_eq_ascii(root->text, root->text_len, "rdf:RDF") &&
                    ucs4_eq_ascii(sequence->text, sequence->text_len, "rdf:Seq");
    PyObject *ordered = PyList_New(0);
    if (ordered == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;       /* GCOVR_EXCL_LINE: allocation failure */
    }
    th_node *item = root->first_child;
    for (th_node *child = sequence->first_child; child != NULL; child = child->next_sibling) {
        if (child->type != TH_NODE_ELEMENT) {
            continue;
        }
        int match = canonical && child->attr_count == 1 && ucs4_eq_ascii(child->text, child->text_len, "rdf:li")
                        ? 1
                        : qualified_tag_is(ns, child, "li", RDF_URI);
        if (match < 0) {        /* GCOVR_EXCL_BR_LINE: namespace lookup fails only on allocation */
            Py_DECREF(ordered); /* GCOVR_EXCL_LINE: allocation failure */
            return NULL;        /* GCOVR_EXCL_LINE: allocation failure */
        }
        if (!match) {
            continue;
        }
        while (item != NULL) {
            if (item->type == TH_NODE_ELEMENT) {
                match = canonical && item->attr_count == 1 && ucs4_eq_ascii(item->text, item->text_len, "item")
                            ? 1
                            : feed_tag_is(ns, item, "item", 4, RSS_URI, root_namespace);
                if (match < 0) {        /* GCOVR_EXCL_BR_LINE: namespace lookup fails only on allocation */
                    Py_DECREF(ordered); /* GCOVR_EXCL_LINE: allocation failure */
                    return NULL;        /* GCOVR_EXCL_LINE: allocation failure */
                }
                if (match) {
                    break;
                }
            }
            item = item->next_sibling;
        }
        if (item == NULL) {
            goto lookup;
        }
        const th_node_attr *about = NULL;
        if (canonical && item->attr_count == 1) {
            Py_ssize_t name_len;
            const char *name = th_attr_name(tree, item->attrs[0].name_atom, &name_len);
            if (name_len == 9 && memcmp(name, "rdf:about", 9) == 0) {
                if (item->attrs[0].value != NULL) { /* GCOVR_EXCL_BR_LINE: XML attributes have values */
                    about = &item->attrs[0];
                }
            }
        }
        if (about == NULL) {
            about = rdf_reference_attr(ns, item, "about", 5, 0, &error);
        }
        const th_node_attr *resource = NULL;
        if (canonical && child->attr_count == 1) {
            Py_ssize_t name_len;
            const char *name = th_attr_name(tree, child->attrs[0].name_atom, &name_len);
            if (name_len == 12 && memcmp(name, "rdf:resource", 12) == 0) {
                if (child->attrs[0].value != NULL) { /* GCOVR_EXCL_BR_LINE: XML attributes have values */
                    resource = &child->attrs[0];
                }
            }
        }
        if (resource == NULL) {
            resource = rdf_reference_attr(ns, child, "resource", 8, 1, &error);
        }
        if (error) {            /* GCOVR_EXCL_BR_LINE: allocation failure */
            Py_DECREF(ordered); /* GCOVR_EXCL_LINE: allocation failure */
            return NULL;        /* GCOVR_EXCL_LINE: allocation failure */
        }
        if (about == NULL || resource == NULL || !absolute_reference(about->value, about->value_len) ||
            about->value_len != resource->value_len ||
            memcmp(about->value, resource->value, (size_t)about->value_len * sizeof(Py_UCS4)) != 0) {
            goto lookup;
        }
        int item_namespace = default_namespace_at_node(tree, item, RSS_URI, root_namespace);
        PyObject *entry = build_entry(state, ns, item, RSS_URI, item_namespace, NULL, Py_None);
        if (entry == NULL) {    /* GCOVR_EXCL_BR_LINE: allocation failure */
            Py_DECREF(ordered); /* GCOVR_EXCL_LINE: allocation failure */
            return NULL;        /* GCOVR_EXCL_LINE: allocation failure */
        }
        int failed = PyList_Append(ordered, entry) < 0;
        Py_DECREF(entry);
        if (failed) {           /* GCOVR_EXCL_BR_LINE: allocation failure */
            Py_DECREF(ordered); /* GCOVR_EXCL_LINE: allocation failure */
            return NULL;        /* GCOVR_EXCL_LINE: allocation failure */
        }
        item = item->next_sibling;
    }
    for (; item != NULL; item = item->next_sibling) {
        if (item->type != TH_NODE_ELEMENT) {
            continue;
        }
        int match = canonical && item->attr_count == 1 && ucs4_eq_ascii(item->text, item->text_len, "item")
                        ? 1
                        : feed_tag_is(ns, item, "item", 4, RSS_URI, root_namespace);
        if (match < 0) {        /* GCOVR_EXCL_BR_LINE: namespace lookup fails only on allocation */
            Py_DECREF(ordered); /* GCOVR_EXCL_LINE: allocation failure */
            return NULL;        /* GCOVR_EXCL_LINE: allocation failure */
        }
        if (match) {
            goto lookup;
        }
    }
    PyObject *result = PyList_AsTuple(ordered);
    Py_DECREF(ordered);
    return result;

lookup:
    Py_DECREF(ordered);
    PyObject *by_uri = PyDict_New();
    ordered = PyList_New(0);
    if (by_uri == NULL || ordered == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        goto error;                          /* GCOVR_EXCL_LINE: allocation failure */
    }
    for (th_node *item = root->first_child; item != NULL; item = item->next_sibling) {
        if (item->type != TH_NODE_ELEMENT) {
            continue;
        }
        int match = feed_tag_is(ns, item, "item", 4, RSS_URI, root_namespace);
        if (match < 0) { /* GCOVR_EXCL_BR_LINE: namespace lookup fails only on allocation */
            goto error;  /* GCOVR_EXCL_LINE: allocation failure */
        }
        if (!match) {
            continue;
        }
        PyObject *key = rdf_reference(ns, item, "about", 5, 0);
        if (key == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            goto error;    /* GCOVR_EXCL_LINE: allocation failure */
        }
        if (key == Py_None) {
            Py_DECREF(key);
            continue;
        }
        key = resolve_link_xml_base(ns, item, key);
        if (key == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            goto error;    /* GCOVR_EXCL_LINE: allocation failure */
        }
        int item_namespace = default_namespace_at_node(tree, item, RSS_URI, root_namespace);
        PyObject *entry = build_entry(state, ns, item, RSS_URI, item_namespace, key, Py_None);
        if (entry == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            Py_DECREF(key);  /* GCOVR_EXCL_LINE: allocation failure */
            goto error;      /* GCOVR_EXCL_LINE: allocation failure */
        }
        int failed = PyDict_SetItem(by_uri, key, entry) < 0;
        Py_DECREF(entry);
        Py_DECREF(key);
        if (failed) {   /* GCOVR_EXCL_BR_LINE: allocation failure */
            goto error; /* GCOVR_EXCL_LINE: allocation failure */
        }
    }
    for (th_node *child = sequence->first_child; child != NULL; child = child->next_sibling) {
        if (child->type != TH_NODE_ELEMENT) {
            continue;
        }
        int match = qualified_tag_is(ns, child, "li", RDF_URI);
        if (match < 0) { /* GCOVR_EXCL_BR_LINE: namespace lookup fails only on allocation */
            goto error;  /* GCOVR_EXCL_LINE: allocation failure */
        }
        if (!match) {
            continue;
        }
        PyObject *key = rdf_reference(ns, child, "resource", 8, 1);
        if (key == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            goto error;    /* GCOVR_EXCL_LINE: allocation failure */
        }
        if (key == Py_None) {
            Py_DECREF(key);
            continue;
        }
        key = resolve_link_xml_base(ns, child, key);
        if (key == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            goto error;    /* GCOVR_EXCL_LINE: allocation failure */
        }
        PyObject *entry = PyDict_GetItemWithError(by_uri, key);
        Py_DECREF(key);
        if (entry == NULL) {
            if (PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: a str lookup cannot raise */
                goto error;         /* GCOVR_EXCL_LINE: allocation failure */
            }
            continue;
        }
        if (PyList_Append(ordered, entry) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            goto error;                          /* GCOVR_EXCL_LINE: allocation failure */
        }
    }
    result = PyList_AsTuple(ordered);
    Py_DECREF(ordered);
    Py_DECREF(by_uri);
    return result;

/* GCOVR_EXCL_START: only allocation or namespace failure reaches cleanup */
error:
    Py_XDECREF(ordered);
    Py_XDECREF(by_uri);
    return NULL;
    /* GCOVR_EXCL_STOP */
}

/* Build the Feed(type, title, link, description, updated, entries) record rooted at `root`. RSS and RDF read their feed
   metadata from the <channel> element (absent in a malformed feed, leaving the fields None); Atom reads it from the
   feed root itself. RSS nests its <item>s in the channel, RDF makes them siblings of the channel, Atom nests its
   <entry>s in the root. NULL only on the excluded allocation-failure path. */
static PyObject *build_feed(module_state *state, ns_index *ns, th_node *root, const char *type, int root_namespace) {
    th_tree *tree = ns->tree;
    static const feed_tag TITLE[] = {{"title", 5}, {"dc:title", 8}};
    static const feed_tag DESCRIPTION[] = {{"description", 11}, {"subtitle", 8}, {"tagline", 7}};
    static const feed_tag UPDATED[] = {{"updated", 7}, {"lastbuilddate", 13}, {"pubdate", 7}, {"dc:date", 7}};
    int is_atom = strcmp(type, "atom") == 0;
    int rdf = strcmp(type, "rdf") == 0;
    const char *uri = is_atom ? ATOM_URI : rdf ? RSS_URI : NULL;
    int error = 0;
    th_node *meta = is_atom ? root : feed_child_named(ns, root, "channel", 7, uri, root_namespace, &error);
    if (error) {     /* GCOVR_EXCL_BR_LINE: namespace_is fails only on allocation */
        return NULL; /* GCOVR_EXCL_LINE: allocation failure */
    }
    int meta_namespace = meta != NULL && !is_atom && uri != NULL && th_tree_is_xml(tree)
                             ? default_namespace_at_node(tree, meta, uri, root_namespace)
                             : root_namespace;
    th_node *item_parent = is_atom || rdf ? root : meta;
    const char *item_name = is_atom ? "entry" : "item";
    Py_ssize_t item_len = is_atom ? 5 : 4;
    PyObject *record = PyTuple_New(6);
    if (record == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int failed = 0;
    failed |= record_set(record, 0, PyUnicode_FromString(type));
    failed |= record_set(record, 1, field_text(ns, meta, TITLE, is_atom ? 1 : 2, uri, meta_namespace, is_atom));
    failed |= record_set(record, 2, extract_link(ns, meta, uri, meta_namespace));
    failed |= record_set(record, 3, field_text(ns, meta, DESCRIPTION, 3, uri, meta_namespace, is_atom));
    failed |= record_set(record, 4, field_text(ns, meta, UPDATED, 4, uri, meta_namespace, 0));
    failed |= record_set(record, 5,
                         rdf ? build_rdf_entries(state, ns, root, meta, root_namespace, meta_namespace)
                             : build_entries(state, ns, item_parent, item_name, item_len, uri,
                                             is_atom ? root_namespace : meta_namespace));
    if (failed != 0) {     /* GCOVR_EXCL_BR_LINE: a field build fails only on unforceable allocation */
        Py_DECREF(record); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;       /* GCOVR_EXCL_LINE */
    }
    if (PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: a field may carry an allocation error with its fallback value */
        Py_DECREF(record);  /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;        /* GCOVR_EXCL_LINE */
    }
    PyObject *feed = PyObject_Call(state->feed_type, record, NULL);
    Py_DECREF(record);
    return feed;
}

static int rss_version_is_2(th_tree *tree, th_node *node) {
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        th_node_attr *attr = &node->attrs[index];
        Py_ssize_t len;
        const char *name = th_attr_name(tree, attr->name_atom, &len);
        if (len == 7 && memcmp(name, "version", 7) == 0) {
            return ucs4_eq_ascii(attr->value, attr->value_len, "2.0");
        }
    }
    return 0;
}

static th_node *find_feed_root(ns_index *ns, th_node *document, const char **type_out, int *root_namespace,
                               int *error) {
    th_tree *tree = ns->tree;
    int is_xml = th_tree_is_xml(tree);
    th_node *first = document->first_child;
    if (is_xml) {
        while (first != NULL && first->type != TH_NODE_ELEMENT) {
            first = first->next_sibling;
        }
    }
    for (th_node *node = first; node != NULL; node = is_xml ? NULL : preorder_next(node, document)) {
        if (node->type != TH_NODE_ELEMENT) {
            continue;
        }
        if ((th_tree_is_xml(tree) ? ucs4_eq_ascii(node->text, node->text_len, "feed") : tag_is(node, "feed", 4)) &&
            (!th_tree_is_xml(tree) || default_namespace_is(tree, node, ATOM_URI, 0))) {
            *type_out = "atom";
            *root_namespace = 1;
            return node;
        }
        if (th_tree_is_xml(tree) ? ucs4_eq_ascii(node->text, node->text_len, "rss") : tag_is(node, "rss", 3)) {
            if (th_tree_is_xml(tree) && (!empty_namespace_is_or_missing(tree, node) || !rss_version_is_2(tree, node))) {
                continue;
            }
            *type_out = "rss";
            *root_namespace = 1;
            return node;
        }
        int rdf = th_tree_is_xml(tree) ? qualified_tag_is(ns, node, "RDF", RDF_URI) : tag_is(node, "rdf:rdf", 7);
        if (rdf < 0) {   /* GCOVR_EXCL_BR_LINE: namespace_is fails only on allocation */
            *error = 1;  /* GCOVR_EXCL_LINE: allocation failure */
            return NULL; /* GCOVR_EXCL_LINE: allocation failure */
        }
        if (rdf) {
            *type_out = "rdf";
            *root_namespace = default_namespace_is(tree, node, RSS_URI, 1);
            return node;
        }
        int atom = th_tree_is_xml(tree) ? qualified_tag_is(ns, node, "feed", ATOM_URI) : 0;
        if (atom < 0) {  /* GCOVR_EXCL_BR_LINE: namespace_is fails only on allocation */
            *error = 1;  /* GCOVR_EXCL_LINE: allocation failure */
            return NULL; /* GCOVR_EXCL_LINE: allocation failure */
        }
        if (atom) {
            *type_out = "atom";
            *root_namespace = default_namespace_is(tree, node, ATOM_URI, 1);
            return node;
        }
    }
    return NULL;
}

/* Document.feed() -> Feed | None. Normalizes an RSS 2.0, Atom 1.0, or RDF/RSS-1.0 document into one Feed record, or
   None when the document carries no feed root. */
TH_NODE_API(, PyObject *, turbohtml_document_feed, (PyObject * self, PyObject *ignored), (self, ignored),
            (PyObject * self, PyObject *Py_UNUSED(ignored)), (NodeObject *)self, NULL) {
    th_tree *tree = tree_of(self);
    module_state *state = state_of(self);
    th_node *document = ((NodeObject *)self)->node;
    PyObject *result = NULL;
    th_node *root = NULL;
    const char *type = NULL;
    int root_namespace = 1;
    int error = 0;
    ns_index ns = {tree, {NULL, 0, 0}, NULL, 0, 0};
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    root = find_feed_root(&ns, document, &type, &root_namespace, &error);
    if (root != NULL) {
        result = build_feed(state, &ns, root, type, root_namespace);
    }
    ns_index_free(&ns);
    Py_END_CRITICAL_SECTION();
    if (error) {     /* GCOVR_EXCL_BR_LINE: namespace_is fails only on allocation */
        return NULL; /* GCOVR_EXCL_LINE: allocation failure */
    }
    if (root == NULL) {
        Py_RETURN_NONE;
    }
    return result; /* Feed on success, NULL with an error set only on the excluded allocation-failure path */
}

/* Store the Feed and Entry NamedTuple classes the walk builds its results from; turbohtml._feed registers both on
   import. */
PyObject *turbohtml_register_feed(PyObject *module, PyObject *args) {
    PyObject *feed = NULL;
    PyObject *entry = NULL;
    if (!PyArg_ParseTuple(args, "OO", &feed, &entry)) { /* GCOVR_EXCL_BR_LINE: the facade always registers with two */
        return NULL;                                    /* GCOVR_EXCL_LINE: argument-error path */
    }
    module_state *state = PyModule_GetState(module);
    Py_XSETREF(state->feed_type, Py_NewRef(feed));
    Py_XSETREF(state->entry_type, Py_NewRef(entry));
    Py_RETURN_NONE;
}
