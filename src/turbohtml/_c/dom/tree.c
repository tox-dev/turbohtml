/* WHATWG HTML tree construction -- see dom/tree.h.

   Implements the linear document path (initial → before html → before head →
   in head → after head → in body → text → after body), generic nesting, void
   elements, implied end tags, the full in-body block/heading/list end-tag
   rules, active formatting elements with reconstruction and the adoption agency
   algorithm (misnested <b>/<i>/<a>). The remaining structural modes -- table
   foster-parenting, foreign content (SVG/MathML), select, templates and
   frameset -- fall back to generic insertion for now; each is a follow-up gated
   by the html5lib tree-construction suite, whose pass rate this engine grows
   toward 100%. */

#include "dom/tree.h"
#include "dom/tree_internal.h" /* arena, struct th_tree, arena_alloc, need_text, is_void_atom */

#include "core/ascii.h"
#include "core/common.h" /* SWAR lane probes for the serializer's clean-run scan */
#include "core/vec.h"    /* th_grow_cap overflow-safe buffer growth */

#include <string.h>

/* Doctype quirks tables and the foreign-content step: static helper collections that
   compile into this translation unit. foreign.h forward-declares the tree-builder
   statics it shares (defined below), so both include cleanly at the top. */
#include "dom/quirks.h"
#include "dom/foreign.h"

/* Copy a th_buf's code points into an arena UCS4 array. */
static Py_UCS4 *buf_to_ucs4(th_tree *tree, const th_buf *buf, Py_ssize_t *out_len) {
    *out_len = buf->len;
    if (buf->len == 0) {
        return NULL;
    }
    Py_UCS4 *out = arena_alloc(tree, buf->len * (Py_ssize_t)sizeof(Py_UCS4));
    if (out == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;   /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    /* Hoist the width dispatch out of the loop: a 1-byte buffer (every ASCII tag
       name) widens in a tight loop, a 4-byte one is a memcpy. */
    if (buf->kind == PyUnicode_1BYTE_KIND) {
        const uint8_t *bytes = (const uint8_t *)buf->data;
        for (Py_ssize_t index = 0; index < buf->len; index++) {
            out[index] = bytes[index];
        }
    } else if (buf->kind == PyUnicode_4BYTE_KIND) {
        memcpy(out, buf->data, (size_t)buf->len * sizeof(Py_UCS4));
    } else {
        const uint16_t *units = (const uint16_t *)buf->data;
        for (Py_ssize_t index = 0; index < buf->len; index++) {
            out[index] = units[index];
        }
    }
    return out;
}

/* Read one code point of the (width-tagged) input buffer. */
static inline Py_UCS4 input_read(const th_tree *tree, Py_ssize_t index) {
    if (tree->kind == PyUnicode_1BYTE_KIND) {
        return ((const uint8_t *)tree->data)[index];
    }
    if (tree->kind == PyUnicode_2BYTE_KIND) {
        return ((const uint16_t *)tree->data)[index];
    }
    return ((const uint32_t *)tree->data)[index];
}

static uint16_t intern_atom(const th_buf *name, uint8_t *out_flags) {
    *out_flags = 0;
    if (name->kind != PyUnicode_1BYTE_KIND || name->len == 0) { /* GCOVR_EXCL_BR_LINE: name is never empty */
        return TH_TAG_UNKNOWN;                                  /* non-ASCII / empty tag names are never atoms */
    }
    const unsigned char *str = (const unsigned char *)name->data;
    /* dispatch on the first byte to one small bucket, then linear-scan it: the
       length pre-check rejects most candidates before any memcmp, and the
       compared tail skips the shared first byte */
    // NOLINTNEXTLINE(clang-analyzer-core.uninitialized.Assign): a tag-name buffer is always initialized
    unsigned first = str[0];
    int index = th_tag_first[first];
    int end = th_tag_first[first + 1];
    for (; index < end; index++) {
        const th_tag_entry *entry = &th_tag_table[index];
        if (entry->name_len != name->len) {
            continue;
        }
        /* Compare the tail (the first byte is the bucket) with an inline loop:
           tag names are short, so this beats a libc memcmp call's overhead. */
        const unsigned char *en = (const unsigned char *)entry->name + 1;
        int eq = 1;
        for (Py_ssize_t pos = 1; pos < name->len; pos++) {
            if (str[pos] != en[pos - 1]) {
                eq = 0;
                break;
            }
        }
        if (eq) {
            *out_flags = entry->flags;
            return entry->atom;
        }
    }
    return TH_TAG_UNKNOWN;
}

/* The token's interned tag atom, cached on the token by the run loop once per
   token so every "which tag is this" comparison is a field read, not a re-intern. */
static uint16_t tok_atom(const th_token *tok) {
    return tok->atom;
}

static uint32_t fnv1a(const char *bytes, Py_ssize_t len) {
    uint32_t hash = 2166136261u;
    for (Py_ssize_t index = 0; index < len; index++) {
        hash ^= (unsigned char)bytes[index];
        hash *= 16777619u;
    }
    return hash;
}

/* Ensure the dynamic table can take one more record: grow the record array, and
   build or grow the hash slots past a 3/4 load factor (rehashing the records). */
static int attr_table_reserve(th_tree *tree) {
    if (tree->attr_rec_count == tree->attr_rec_cap) {
        size_t cap;
        size_t bytes;
        int grew = th_grow_cap((size_t)tree->attr_rec_cap + 1, (size_t)tree->attr_rec_cap, 8, sizeof(th_attr_record),
                               &cap, &bytes);
        if (!grew) {   /* GCOVR_EXCL_BR_LINE: size overflow needs a length no allocation could hold */
            return -1; /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
        }
        th_attr_record *recs = PyMem_Realloc(tree->attr_recs, bytes);
        if (recs == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;      /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        }
        tree->attr_recs = recs;
        tree->attr_rec_cap = (uint32_t)cap;
    }
    if (tree->attr_slots == NULL || tree->attr_rec_count + 1 > (tree->attr_slot_mask + 1) * 3 / 4) {
        uint32_t new_cap = tree->attr_slots == NULL ? 16 : (tree->attr_slot_mask + 1) * 2;
        uint32_t *slots = PyMem_Calloc(new_cap, sizeof(uint32_t));
        if (slots == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        uint32_t mask = new_cap - 1;
        for (uint32_t index = 0; index < tree->attr_rec_count; index++) {
            uint32_t probe = tree->attr_recs[index].hash & mask;
            while (slots[probe] != 0) {
                probe = (probe + 1) & mask;
            }
            slots[probe] = index + 1;
        }
        PyMem_Free(tree->attr_slots);
        tree->attr_slots = slots;
        tree->attr_slot_mask = mask;
    }
    return 0;
}

/* The record index + 1 for an interned name, or 0 when it is not in the table.
   The name comparison is by bytes, so a hash collision is not a special case. */
static uint32_t dynamic_slot(th_tree *tree, const char *bytes, Py_ssize_t len, uint32_t hash) {
    if (tree->attr_slots == NULL) {
        return 0;
    }
    uint32_t mask = tree->attr_slot_mask;
    for (uint32_t probe = hash & mask; tree->attr_slots[probe] != 0; probe = (probe + 1) & mask) {
        const th_attr_record *rec = &tree->attr_recs[tree->attr_slots[probe] - 1];
        if (rec->name_len == (uint32_t)len && memcmp(rec->name, bytes, (size_t)len) == 0) {
            return tree->attr_slots[probe];
        }
    }
    return 0;
}

/* Intern UTF-8 name bytes into the per-tree dynamic table, returning the atom
   (prototype in dom/tree_internal.h; the construction API in mutate.c reuses it). */
uint32_t intern_attr_dynamic(th_tree *tree, const char *bytes, Py_ssize_t len) {
    uint32_t hash = fnv1a(bytes, len);
    uint32_t found = dynamic_slot(tree, bytes, len, hash);
    if (found != 0) {
        return TH_ATTR__DYNAMIC_BASE + (found - 1);
    }
    if (attr_table_reserve(tree) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return TH_ATTR_UNKNOWN;         /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    char *stored = arena_alloc(tree, len + 1);
    if (stored == NULL) {       /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return TH_ATTR_UNKNOWN; /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    memcpy(stored, bytes, (size_t)len);
    stored[len] = '\0';
    uint32_t index = tree->attr_rec_count++;
    tree->attr_recs[index] = (th_attr_record){stored, (uint32_t)len, hash};
    uint32_t mask = tree->attr_slot_mask;
    uint32_t probe = hash & mask;
    while (tree->attr_slots[probe] != 0) {
        probe = (probe + 1) & mask;
    }
    tree->attr_slots[probe] = index + 1;
    return TH_ATTR__DYNAMIC_BASE + index;
}

/* UTF-8 encode a non-ASCII attribute name into the arena (NUL-terminated). Only
   the rare wide-name path needs this; ASCII names intern from their bytes. */
static char *attr_name_utf8(th_tree *tree, const th_buf *name, Py_ssize_t *out_len) {
    char *out = arena_alloc(tree, name->len * 4 + 1);
    if (out == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;   /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t at = 0;
    for (Py_ssize_t index = 0; index < name->len; index++) {
        Py_UCS4 ch = buf_read(name, index);
        if (ch < 0x80) {
            out[at++] = (char)ch;
        } else if (ch < 0x800) {
            out[at++] = (char)(0xC0 | (ch >> 6));
            out[at++] = (char)(0x80 | (ch & 0x3F));
        } else if (ch < 0x10000) {
            out[at++] = (char)(0xE0 | (ch >> 12));
            out[at++] = (char)(0x80 | ((ch >> 6) & 0x3F));
            out[at++] = (char)(0x80 | (ch & 0x3F));
        } else {
            out[at++] = (char)(0xF0 | (ch >> 18));
            out[at++] = (char)(0x80 | ((ch >> 12) & 0x3F));
            out[at++] = (char)(0x80 | ((ch >> 6) & 0x3F));
            out[at++] = (char)(0x80 | (ch & 0x3F));
        }
    }
    out[at] = '\0';
    *out_len = at;
    return out;
}

static int buf_ascii(const char *bytes, Py_ssize_t len) {
    for (Py_ssize_t index = 0; index < len; index++) {
        if ((unsigned char)bytes[index] >= 0x80) {
            return 0;
        }
    }
    return 1;
}

/* Intern an attribute name to its atom: a static compile-time id for a common
   name, else a per-tree dynamic id. A 1-byte buffer holds Latin-1 code points,
   so only an all-ASCII one is already UTF-8; any high byte (and every wider
   buffer) is re-encoded so the stored name round-trips through UTF-8. */
static uint32_t intern_attr(th_tree *tree, const th_buf *name) {
    if (name->kind == PyUnicode_1BYTE_KIND) {
        const char *bytes = (const char *)name->data;
        uint32_t atom = th_attr_atom(bytes, (size_t)name->len);
        if (atom != TH_ATTR_UNKNOWN) {
            return atom;
        }
        if (buf_ascii(bytes, name->len)) {
            return intern_attr_dynamic(tree, bytes, name->len);
        }
    }
    Py_ssize_t len;
    char *bytes = attr_name_utf8(tree, name, &len);
    if (bytes == NULL) {        /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return TH_ATTR_UNKNOWN; /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return intern_attr_dynamic(tree, bytes, len);
}

/* The interned name's bytes for an atom: the static table for a common atom, the
   per-tree records otherwise. NUL-terminated; *out_len receives the length. */
const char *th_attr_name(th_tree *tree, uint32_t atom, Py_ssize_t *out_len) {
    if (atom < TH_ATTR__DYNAMIC_BASE) {
        const th_attr_entry *entry = &th_attr_table[atom - 1];
        *out_len = entry->name_len;
        return entry->name;
    }
    const th_attr_record *rec = &tree->attr_recs[atom - TH_ATTR__DYNAMIC_BASE];
    *out_len = rec->name_len;
    return rec->name;
}

/* Resolve a query attribute name (UTF-8 bytes) to the atom an element would carry
   for it, without interning: a static atom, a dynamic atom already seen in this
   tree, or UINT32_MAX when no element in the tree has that name. */
uint32_t th_attr_lookup(th_tree *tree, const char *bytes, Py_ssize_t len) {
    if (len == 0) {
        return UINT32_MAX;
    }
    uint32_t atom = th_attr_atom(bytes, (size_t)len);
    if (atom != TH_ATTR_UNKNOWN) {
        return atom;
    }
    uint32_t found = dynamic_slot(tree, bytes, len, fnv1a(bytes, len));
    return found != 0 ? TH_ATTR__DYNAMIC_BASE + (found - 1) : UINT32_MAX;
}

uint32_t th_tree_attr_generation(const th_tree *tree) {
    return tree->attr_rec_count;
}

int th_tree_quirks(const th_tree *tree) {
    return tree->quirks;
}

int th_tree_scripting(const th_tree *tree) {
    return tree->scripting;
}

Py_ssize_t th_tree_max_depth(const th_tree *tree) {
    return tree->max_depth;
}

const th_parse_error *th_tree_errors(const th_tree *tree, Py_ssize_t *out_count) {
    *out_count = tree->errors.len;
    return tree->errors.items;
}

int th_node_text_is_blank(th_tree *tree, th_node *node) {
    const Py_UCS4 *text = need_text(tree, node); /* realize a zero-copy span before scanning */
    for (Py_ssize_t index = 0; index < node->text_len; index++) {
        if (!is_space(text[index])) {
            return 0;
        }
    }
    return 1;
}

/* Resolve a lowercased tag name (UTF-8 bytes) to its atom, or TH_TAG_UNKNOWN for
   a name outside the table (the caller then compares the tag name as text). */
uint16_t th_tag_lookup(const char *bytes, Py_ssize_t len) {
    unsigned first = (unsigned char)bytes[0];
    int stop = th_tag_first[first + 1];
    for (int index = th_tag_first[first]; index < stop; index++) {
        const th_tag_entry *entry = &th_tag_table[index];
        if (entry->name_len == len && memcmp(entry->name + 1, bytes + 1, (size_t)(len - 1)) == 0) {
            return entry->atom;
        }
    }
    return TH_TAG_UNKNOWN;
}

/* The category bitmask for an atom (0 for TH_TAG_UNKNOWN), so a constructed or
   reconstructed element carries the same flags the parser derives from the name. */
uint8_t th_tag_flags(uint16_t atom) {
    for (int index = 0; index < th_tag_count; index++) {
        if (th_tag_table[index].atom == atom) {
            return th_tag_table[index].flags;
        }
    }
    return 0;
}

int th_tag_is_void(uint16_t atom) {
    return is_void_atom(atom);
}

/* node_pos, node_new and the node_append/node_remove/node_insert_before linkers
   live in dom/tree_internal.h, shared with the construction/mutation API (mutate.c). */

int th_node_source_position(th_tree *tree, th_node *node, Py_ssize_t *line, Py_ssize_t *col) {
    if (!tree->track_positions || node->type != TH_NODE_ELEMENT) {
        return 0; /* untracked tree or a node type that reserves no trailing slots */
    }
    uint32_t *pos = node_pos(node);
    if (pos[0] == 0) {
        return 0; /* a synthetic element with no source start tag */
    }
    *line = (Py_ssize_t)pos[0];
    *col = (Py_ssize_t)pos[1];
    return 1;
}

/* A shallow element clone (same atom/name/attrs, no children) for the adoption
   agency and active-formatting-element reconstruction. */
static th_node *node_clone(th_tree *tree, const th_node *src);

static th_node *current_node(th_tree *tree) {
    return tree->open_len > 0 ? tree->open[tree->open_len - 1] : tree->document;
}

static int stack_push(th_tree *tree, th_node *node) {
    if (tree->open_len >= TH_MAX_TREE_DEPTH) {
        /* Runaway nesting: leave the element in the DOM (it was already inserted under
           the deepest open element) but do not descend into it, so subsequent start
           tags become its siblings. This bounds tree depth the way browsers do, which
           keeps the recursive tree walks off a deep C stack and caps the O(depth^2)
           parse cost of a long run of start tags. */
        return 1;
    }
    if (tree->open_len == tree->open_cap) {
        size_t cap;
        size_t bytes;
        int grew =
            th_grow_cap((size_t)(tree->open_cap + 1), (size_t)tree->open_cap, 16, sizeof(th_node *), &cap, &bytes);
        if (!grew) {          /* GCOVR_EXCL_BR_LINE: size overflow needs a length no allocation could hold */
            tree->failed = 1; /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
            return 0;         /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
        }
        th_node **grown = PyMem_Realloc(tree->open, bytes);
        if (grown == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            tree->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
            return 0;         /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        }
        tree->open = grown;
        tree->open_cap = (Py_ssize_t)cap;
    }
    tree->open[tree->open_len++] = node;
    if (tree->open_len > tree->max_depth) {
        tree->max_depth = tree->open_len;
    }
    return 1;
}

static void maybe_clone_option(th_tree *tree, th_node *option);
static int name_matches(const th_node *node, const th_token *token, int fold);

static void stack_pop(th_tree *tree) {
    if (tree->open_len > 0) { /* GCOVR_EXCL_BR_LINE: only reached with a non-empty stack */
        tree->open_len--;
        th_node *popped = tree->open[tree->open_len];
        /* record that an end tag named this element (as opposed to an implied or
           EOF close) so escape-mode sanitizing reproduces the author's `</tag>` */
        if (tree->closing_end_tag != NULL && name_matches(popped, tree->closing_end_tag, popped->ns != TH_NS_HTML)) {
            popped->tag_flags |= TH_ELEM_CLOSED_BY_END_TAG;
        }
        if (popped->ns == TH_NS_HTML && popped->atom == TH_TAG_OPTION) {
            /* "when an option element is popped off the stack ... run maybe
               clone an option into selectedcontent" */
            maybe_clone_option(tree, popped);
        }
    }
}

/* Is an element with this atom on the stack, stopping at a scope boundary? */
/* The default-scope boundary: HTML scoping elements plus the MathML/SVG
   integration-point and scoping elements (a foreign subtree is its own scope). */
static int is_scope_boundary(const th_node *node) {
    if (node->ns == TH_NS_HTML) {
        return (node->tag_flags & TH_TAG_SCOPING) != 0;
    }
    if (node->ns == TH_NS_MATHML) {
        return node->atom == TH_TAG_MI || node->atom == TH_TAG_MO || node->atom == TH_TAG_MN ||
               node->atom == TH_TAG_MS || node->atom == TH_TAG_MTEXT || node->atom == TH_TAG_ANNOTATION_XML;
    }
    return node->atom == TH_TAG_FOREIGNOBJECT || node->atom == TH_TAG_DESC || node->atom == TH_TAG_TITLE;
}

static int has_in_scope(th_tree *tree, uint16_t atom) {
    for (Py_ssize_t index = tree->open_len - 1; index >= 0; index--) {
        th_node *node = tree->open[index];
        if (node->ns == TH_NS_HTML && node->atom == atom) {
            return 1;
        }
        if (is_scope_boundary(node)) {
            return 0;
        }
    }
    return 0;
}

/* List-item scope: the default boundaries plus ol and ul. */
static int has_in_list_item_scope(th_tree *tree, uint16_t atom) {
    /* The html element bounds the stack walk. */
    for (Py_ssize_t index = tree->open_len - 1; index >= 0; index--) { /* GCOVR_EXCL_BR_LINE */
        th_node *node = tree->open[index];
        /* Foreign content runs under the foreign rules before any list-item scope check. */
        if (node->ns == TH_NS_HTML /* GCOVR_EXCL_BR_LINE */ && node->atom == atom) {
            return 1;
        }
        if (is_scope_boundary(node) ||
            /* The ol/ul list-scope boundary is only ever tested on html elements. */
            (node->ns == TH_NS_HTML /* GCOVR_EXCL_BR_LINE */ && (node->atom == TH_TAG_OL || node->atom == TH_TAG_UL))) {
            return 0;
        }
    } /* GCOVR_EXCL_BR_LINE: the stack always bottoms out at an html scope boundary, so the loop never falls through */
    return 0; /* GCOVR_EXCL_LINE: same -- has_in_list_item_scope always returns inside the loop */
}

static int is_implied_end_atom(uint16_t atom) {
    switch (atom) {
    case TH_TAG_DD:
    case TH_TAG_DT:
    case TH_TAG_LI:
    case TH_TAG_OPTION:
    case TH_TAG_OPTGROUP:
    case TH_TAG_P:
    case TH_TAG_RB:
    case TH_TAG_RP:
    case TH_TAG_RT:
    case TH_TAG_RTC:
        return 1;
    default:
        return 0;
    }
}

static void generate_implied_end_tags(th_tree *tree, uint16_t except_atom) {
    while (tree->open_len > 0) { /* GCOVR_EXCL_BR_LINE: the html element is never an implied-end-tag element, so the
                                    stack never empties here */
        uint16_t atom = current_node(tree)->atom;
        if (atom == except_atom || !is_implied_end_atom(atom)) {
            return;
        }
        stack_pop(tree);
    }
}

/* Pop elements until (and including) an HTML element with this atom; a foreign
   element with the same local name (e.g. a MathML td) never matches. */
static void pop_until_atom(th_tree *tree, uint16_t atom) {
    while (tree->open_len > 0) { /* GCOVR_EXCL_BR_LINE: pop_until_atom is only called with the target element in scope,
                                    so the stack never empties */
        th_node *node = current_node(tree);
        stack_pop(tree);
        if (node->ns == TH_NS_HTML && node->atom == atom) {
            return;
        }
    }
}

/* Pop until the current node is an HTML element with one of the given atoms
   (exclusive); used by the table modes to "clear the stack back to a
   table / body / row context". */
static void clear_to(th_tree *tree, uint16_t atom_a, uint16_t atom_b, uint16_t atom_c) {
    while (tree->open_len > 0) { /* GCOVR_EXCL_BR_LINE: a table-context boundary is always on the open stack, so the
                                    loop never empties it */
        th_node *node = current_node(tree);
        uint16_t atom = node->atom;
        /* A table-context boundary is always an html element. */
        if (node->ns == TH_NS_HTML /* GCOVR_EXCL_BR_LINE */ &&
            (atom == atom_a || atom == atom_b || atom == atom_c || atom == TH_TAG_HTML || atom == TH_TAG_TEMPLATE)) {
            return; /* template is also a table-context boundary */
        }
        stack_pop(tree);
    }
}

/* Is an HTML element with this atom on the stack (table scope is "up to the
   nearest HTML table, template or html")? */
static int has_in_table_scope(th_tree *tree, uint16_t atom) {
    /* The html element bounds the stack walk. */
    for (Py_ssize_t index = tree->open_len - 1; index >= 0; index--) { /* GCOVR_EXCL_BR_LINE */
        th_node *node = tree->open[index];
        if (node->ns != TH_NS_HTML) {
            continue;
        }
        if (node->atom == atom) {
            return 1;
        }
        if (node->atom == TH_TAG_TABLE || node->atom == TH_TAG_TEMPLATE || node->atom == TH_TAG_HTML) {
            return 0;
        }
    } /* GCOVR_EXCL_BR_LINE: the stack always bottoms out at an html table-scope boundary */
    return 0; /* GCOVR_EXCL_LINE: same -- has_in_table_scope always returns inside the loop */
}

/* The appropriate place for inserting a node: normally the current node, but
   when foster parenting is active and the current node is a table-context
   element, the location just before the nearest table (templates deferred). */
static void insertion_location_target(th_tree *tree, th_node *target, th_node **parent, th_node **before) {
    *before = NULL;
    /* content goes into a template's content fragment, not the element itself;
       an html template always carries that content node as its first child */
    if (target->ns == TH_NS_HTML && target->atom == TH_TAG_TEMPLATE) {
        *parent = target->first_child;
        return;
    }
    if (tree->foster && (target->atom == TH_TAG_TABLE || target->atom == TH_TAG_TBODY || target->atom == TH_TAG_TFOOT ||
                         target->atom == TH_TAG_THEAD || target->atom == TH_TAG_TR)) {
        for (Py_ssize_t index = tree->open_len - 1; index >= 0; index--) {
            th_node *open = tree->open[index];
            if (open->ns != TH_NS_HTML) { /* GCOVR_EXCL_BR_LINE: no foreign element sits above a fostered table */
                continue; /* GCOVR_EXCL_LINE: no foreign element sits above the fostered table on the stack */
            }
            /* a template above the last table wins: fostering appends into its
               content fragment (always its first child) instead of leaving the table */
            if (open->atom == TH_TAG_TEMPLATE) {
                *parent = open->first_child;
                return;
            }
            if (open->atom == TH_TAG_TABLE) { /* GCOVR_EXCL_BR_LINE: an open table always has a parent */
                if (open->parent != NULL) {   /* GCOVR_EXCL_BR_LINE: an open table always has a parent */
                    *parent = open->parent;
                    *before = open;
                } else {
                    /* GCOVR_EXCL_START: open table always has a parent */
                    *parent = index > 0 ? tree->open[index - 1] : tree->document;
                    /* GCOVR_EXCL_STOP */
                }
                return;
            }
        }
        /* no table is on the stack: a table-section fragment context (tbody/table/
           tr/...). The appropriate place is then the first element on the stack, the
           fragment root, so the content fosters out as a sibling of the open row. */
        *parent = tree->open[0];
        return;
    }
    *parent = target;
}

static void insertion_location(th_tree *tree, th_node **parent, th_node **before) {
    insertion_location_target(tree, current_node(tree), parent, before);
}

static th_node *insert_element(th_tree *tree, const th_token *token) {
    th_node *node = node_new(tree, TH_NODE_ELEMENT);
    if (node == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    node->atom = token->atom;
    node->tag_flags = token->tag_flags;
    if (tree->track_positions) {
        /* the start tag's source position; a line or column past 4G (a
           multi-gigabyte minified document) saturates, which no real input reaches */
        node_pos(node)[0] = token->line > UINT32_MAX ? UINT32_MAX : (uint32_t)token->line; /* GCOVR_EXCL_BR_LINE */
        node_pos(node)[1] = token->col > UINT32_MAX ? UINT32_MAX : (uint32_t)token->col;   /* GCOVR_EXCL_BR_LINE */
    }
    node->text = buf_to_ucs4(tree, &token->name, &node->text_len);
    if (token->attr_count > 0) {
        node->attrs = arena_alloc(tree, token->attr_count * (Py_ssize_t)sizeof(th_node_attr));
        if (node->attrs == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;           /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        }
        node->attr_count = token->attr_count;
        for (Py_ssize_t index = 0; index < token->attr_count; index++) {
            const th_attr *src = &token->attrs[index];
            th_node_attr *dst = &node->attrs[index];
            dst->name_atom = intern_attr(tree, &src->name);
            dst->value = src->has_value ? buf_to_ucs4(tree, &src->value, &dst->value_len) : NULL;
            if (!src->has_value) {
                dst->value_len = 0;
            }
        }
    }
    th_node *parent, *before;
    insertion_location(tree, &parent, &before);
    node_insert_before(parent, node, before);
    return node;
}

/* Build a doctype node's serialized text: the name, plus ` "public" "system"`
   when either identifier is present (html5lib's #document format). */
static Py_UCS4 *build_doctype_text(th_tree *tree, const th_token *tok, Py_ssize_t *out_len) {
    int has_ids = tok->has_public_id || tok->has_system_id;
    Py_ssize_t pub = tok->has_public_id ? tok->public_id.len : 0;
    Py_ssize_t sys = tok->has_system_id ? tok->system_id.len : 0;
    Py_ssize_t total = tok->name.len + (has_ids ? pub + sys + 6 : 0);
    *out_len = total;
    if (total == 0) {
        return NULL;
    }
    Py_UCS4 *out = arena_alloc(tree, total * (Py_ssize_t)sizeof(Py_UCS4));
    if (out == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;   /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    Py_ssize_t at = 0;
    for (Py_ssize_t index = 0; index < tok->name.len; index++) {
        out[at++] = buf_read(&tok->name, index);
    }
    if (has_ids) {
        out[at++] = ' ';
        out[at++] = '"';
        for (Py_ssize_t index = 0; index < pub; index++) {
            out[at++] = buf_read(&tok->public_id, index);
        }
        out[at++] = '"';
        out[at++] = ' ';
        out[at++] = '"';
        for (Py_ssize_t index = 0; index < sys; index++) {
            out[at++] = buf_read(&tok->system_id, index);
        }
        out[at++] = '"';
    }
    return out;
}

/* Merge a start tag's attributes onto an existing element, keeping only the
   ones whose name is not already present (the <html>/<body> re-open rule). */
static void merge_attrs(th_tree *tree, th_node *node, const th_token *token) {
    if (token->attr_count == 0) {
        return;
    }
    Py_ssize_t add = 0;
    for (Py_ssize_t index = 0; index < token->attr_count; index++) {
        uint32_t atom = intern_attr(tree, &token->attrs[index].name);
        int present = 0;
        for (Py_ssize_t inner_index = 0; inner_index < node->attr_count; inner_index++) {
            if (node->attrs[inner_index].name_atom == atom) {
                present = 1;
                break;
            }
        }
        if (!present) {
            add++;
        }
    }
    if (add == 0) {
        return;
    }
    th_node_attr *merged = arena_alloc(tree, (node->attr_count + add) * (Py_ssize_t)sizeof(th_node_attr));
    if (merged == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return;           /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    memcpy(merged, node->attrs, (size_t)node->attr_count * sizeof(th_node_attr));
    Py_ssize_t at = node->attr_count;
    for (Py_ssize_t index = 0; index < token->attr_count; index++) {
        const th_attr *src = &token->attrs[index];
        uint32_t atom = intern_attr(tree, &src->name);
        int present = 0;
        for (Py_ssize_t inner_index = 0; inner_index < node->attr_count; inner_index++) {
            if (node->attrs[inner_index].name_atom == atom) {
                present = 1;
                break;
            }
        }
        if (present) {
            continue;
        }
        // NOLINTNEXTLINE(clang-analyzer-security.ArrayBound): merged holds one slot per source attribute
        th_node_attr *dst = &merged[at++];
        dst->name_atom = atom;
        dst->value = src->has_value ? buf_to_ucs4(tree, &src->value, &dst->value_len) : NULL;
        if (!src->has_value) {
            dst->value_len = 0;
        }
    }
    node->attrs = merged;
    node->attr_count += add;
}

/* True for the "in body" start tags that flip frameset-ok to "not ok": a
   frameset can no longer replace the body once one of these has been seen. The
   list is exactly the spec's, not "everything that is real content". */
static int atom_breaks_frameset(uint16_t atom) {
    switch (atom) {
    case TH_TAG_AREA:
    case TH_TAG_BR:
    case TH_TAG_EMBED:
    case TH_TAG_IMG:
    case TH_TAG_IMAGE:
    case TH_TAG_KEYGEN:
    case TH_TAG_WBR:
    case TH_TAG_HR:
    case TH_TAG_TEXTAREA:
    case TH_TAG_XMP:
    case TH_TAG_IFRAME:
    case TH_TAG_SELECT:
    case TH_TAG_TABLE:
    case TH_TAG_APPLET:
    case TH_TAG_MARQUEE:
    case TH_TAG_OBJECT:
    case TH_TAG_PRE:
    case TH_TAG_LISTING:
    case TH_TAG_LI:
    case TH_TAG_DD:
    case TH_TAG_DT:
    case TH_TAG_BUTTON:
    case TH_TAG_BODY:
        return 1;
    default:
        return 0;
    }
}

/* <input> breaks frameset-ok unless its type attribute is "hidden" (ASCII
   case-insensitive); a hidden input is invisible so a frameset may still win. */
static int input_is_hidden(const th_token *token) {
    for (Py_ssize_t index = 0; index < token->attr_count; index++) {
        const th_attr *attr = &token->attrs[index];
        if (attr->name.len != 4 || attr->name.kind != PyUnicode_1BYTE_KIND || memcmp(attr->name.data, "type", 4) != 0 ||
            !attr->has_value) {
            continue;
        }
        const th_buf *value = &attr->value;
        if (value->len != 6 || value->kind != PyUnicode_1BYTE_KIND) {
            return 0;
        }
        static const char hidden[6] = {'h', 'i', 'd', 'd', 'e', 'n'};
        const unsigned char *bytes = value->data;
        for (int pos = 0; pos < 6; pos++) {
            unsigned char character = bytes[pos];
            if (character >= 'A' && character <= 'Z') {
                character = (unsigned char)(character + 32);
            }
            if (character != (unsigned char)hidden[pos]) {
                return 0;
            }
        }
        return 1;
    }
    return 0;
}

/* Insert a <template> element and give it a content document-fragment child
   that subsequent insertions are redirected into. */
static th_node *insert_template(th_tree *tree, const th_token *token) {
    th_node *node = insert_element(tree, token);
    if (node == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    th_node *content = node_new(tree, TH_NODE_CONTENT);
    if (content == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return node;       /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    node_append(node, content);
    return node;
}

static void insert_text(th_tree *tree, Py_UCS4 *text, Py_ssize_t len) {
    if (len == 0) {
        return;
    }
    /* U+0000 characters are dropped in HTML content (a parse error); skip the
       per-run scan entirely unless the document actually contains a NUL */
    Py_ssize_t nul = len;
    if (tree->has_nul) {
        nul = 0;
        while (nul < len && text[nul] != 0) {
            nul++;
        }
    }
    if (nul < len) {
        Py_ssize_t write_pos = nul;
        for (Py_ssize_t read_pos = nul; read_pos < len; read_pos++) {
            if (text[read_pos] != 0) {
                text[write_pos++] = text[read_pos];
            }
        }
        len = write_pos;
        if (len == 0) {
            return;
        }
    }
    th_node *parent, *before;
    insertion_location(tree, &parent, &before);
    /* merge into the immediately preceding text node at the same location */
    th_node *prev = before != NULL ? before->prev_sibling : parent->last_child;
    if (prev != NULL && prev->type == TH_NODE_TEXT) {
        Py_UCS4 *prev_text = need_text(tree, prev); /* realize prev if it was a span */
        Py_UCS4 *merged = arena_alloc(tree, (prev->text_len + len) * (Py_ssize_t)sizeof(Py_UCS4));
        if (merged == NULL || prev_text == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
            return;                                /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        memcpy(merged, prev_text, (size_t)prev->text_len * sizeof(Py_UCS4));
        memcpy(merged + prev->text_len, text, (size_t)len * sizeof(Py_UCS4));
        prev->text = merged;
        prev->text_len += len;
        return;
    }
    th_node *node = node_new(tree, TH_NODE_TEXT);
    if (node == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return;         /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    node->text = text;
    node->text_len = len;
    node_insert_before(parent, node, before);
}

/* Insert a text run as a zero-copy span into input[off .. off+len). The caller
   guarantees the run has no NUL (tree->has_nul is false) and the input outlives
   the tree (tree->can_span). An adjacent text node is the rare case: realize the
   span and fall back to the merging insert_text. */
static void insert_text_span(th_tree *tree, Py_ssize_t off, Py_ssize_t len) {
    if (len == 0) {
        return;
    }
    th_node *parent, *before;
    insertion_location(tree, &parent, &before);
    /* text is inserted at the end of its parent, so there is no before-sibling */
    th_node *prev = before != NULL /* GCOVR_EXCL_BR_LINE */ ? before->prev_sibling : parent->last_child;
    if (prev != NULL && prev->type == TH_NODE_TEXT) {
        Py_UCS4 *text = copy_input_span(tree, off, len);
        if (text != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
            insert_text(tree, text, len);
        }
        return;
    }
    th_node *node = node_new(tree, TH_NODE_TEXT);
    if (node == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return;         /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    node->text = NULL; /* span marker */
    node->text_len = len;
    node->attr_count = off; /* the span's start offset into the input */
    node_insert_before(parent, node, before);
}

static void insert_comment(th_tree *tree, const th_token *token, th_node *parent) {
    th_node *node = node_new(tree, TH_NODE_COMMENT);
    if (node == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return;         /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    node->text = buf_to_ucs4(tree, &token->text, &node->text_len);
    if (parent != NULL) {
        node_append(parent, node);
        return;
    }
    th_node *target, *before;
    insertion_location(tree, &target, &before);
    node_insert_before(target, node, before);
}

/* Resolve a token's text (slice or owned buffer) into an arena UCS4 array. */
static Py_UCS4 *token_text(th_tree *tree, const th_token *token, Py_ssize_t *out_len) {
    Py_UCS4 *out;
    if (token->is_slice) {
        Py_ssize_t length = token->src_len;
        *out_len = length;
        /* A slice text token always has a positive source length. */
        out = length == 0 /* GCOVR_EXCL_BR_LINE */ ? NULL : copy_input_span(tree, token->src_start, length);
    } else {
        out = buf_to_ucs4(tree, &token->text, out_len);
    }
    /* a mode that consumed a leading slice of this text token and reprocessed
       the remainder set text_offset; hand back only the remainder */
    /* the consumed prefix never exceeds the run length */
    if (tree->text_offset > 0 && tree->text_offset <= *out_len) { /* GCOVR_EXCL_BR_LINE */
        out += tree->text_offset;
        *out_len -= tree->text_offset;
    }
    return out;
}

static th_node *node_clone(th_tree *tree, const th_node *src) {
    th_node *node = node_new(tree, TH_NODE_ELEMENT);
    if (node == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    node->atom = src->atom;
    node->tag_flags = src->tag_flags;
    node->text = src->text;
    node->text_len = src->text_len;
    node->attrs = src->attrs; /* attributes are immutable arena data; share them */
    node->attr_count = src->attr_count;
    if (tree->track_positions) {
        /* an adoption-agency clone stands in for the same source start tag, so it
           keeps the original's position */
        const uint32_t *src_pos = node_pos((th_node *)src);
        node_pos(node)[0] = src_pos[0];
        node_pos(node)[1] = src_pos[1];
    }
    return node;
}

/* Does the element carry an attribute with this interned name? */
static int node_has_attr(const th_node *node, uint32_t atom) {
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        if (node->attrs[index].name_atom == atom) {
            return 1;
        }
    }
    return 0;
}

/* The first HTML element with this atom in a depth-first walk of parent's
   subtree, not descending into nested selects. */
static th_node *first_descendant_atom(th_node *parent, uint16_t atom) {
    for (th_node *child = parent->first_child; child != NULL; child = child->next_sibling) {
        if (child->type == TH_NODE_ELEMENT && child->ns == TH_NS_HTML) {
            if (child->atom == atom) {
                return child;
            }
            /* GCOVR_EXCL_START: a nested <select> start closes the open select, so a select never contains one */
            if (child->atom == TH_TAG_SELECT) {
                continue;
            }
            /* GCOVR_EXCL_STOP */
        }
        th_node *found = first_descendant_atom(child, atom);
        if (found != NULL) {
            return found;
        }
    }
    return NULL;
}

/* Deep-copy src's children, appending the copies to dst. */
static void deep_copy_children(th_tree *tree, const th_node *src, th_node *dst) {
    for (th_node *child = src->first_child; child != NULL; child = child->next_sibling) {
        th_node *copy;
        if (child->type == TH_NODE_ELEMENT) {
            copy = node_clone(tree, child);
            if (copy != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
                copy->ns = child->ns;
            }
        } else {
            copy = node_new(tree, child->type);
            if (copy != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
                /* Realize a zero-copy span before sharing the payload pointer. */
                copy->text = need_text(tree, (th_node *)child);
                copy->text_len = child->text_len;
            }
        }
        if (copy == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return;         /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        }
        node_append(dst, copy);
        deep_copy_children(tree, child, copy);
    }
}

/* "Maybe clone an option into selectedcontent": when a selected option (the
   selected attribute, or the select's first option by default) is popped, its
   children are mirrored into the select's selectedcontent element. */
static void maybe_clone_option(th_tree *tree, th_node *option) {
    th_node *select = option->parent;
    while (select != NULL &&
           /* an option always has a select ancestor, so the walk never reaches a non-element node */
           !(select->type == TH_NODE_ELEMENT /* GCOVR_EXCL_BR_LINE */ && select->ns == TH_NS_HTML &&
             select->atom == TH_TAG_SELECT)) {
        select = select->parent;
    }
    if (select == NULL || node_has_attr(select, TH_ATTR_MULTIPLE)) {
        return;
    }
    if (!node_has_attr(option, TH_ATTR_SELECTED) && first_descendant_atom(select, TH_TAG_OPTION) != option) {
        return;
    }
    th_node *sc = first_descendant_atom(select, TH_TAG_SELECTEDCONTENT);
    if (sc == NULL || node_has_attr(sc, TH_ATTR_DISABLED)) {
        return;
    }
    while (sc->first_child != NULL) {
        node_remove(sc->first_child);
    }
    deep_copy_children(tree, option, sc);
}

/* Remove the active-formatting-elements entry at index, shifting the tail down. */
static void afe_remove_at(th_tree *tree, Py_ssize_t index) {
    memmove(&tree->afe[index], &tree->afe[index + 1], (size_t)(tree->afe_len - index - 1) * sizeof(th_node *));
    tree->afe_len--;
}

/* Remove the open-elements-stack entry at index, shifting the tail down. */
static void stack_remove_at(th_tree *tree, Py_ssize_t index) {
    memmove(&tree->open[index], &tree->open[index + 1], (size_t)(tree->open_len - index - 1) * sizeof(th_node *));
    tree->open_len--;
}

static int afe_push(th_tree *tree, th_node *node) {
    /* Noah's Ark: at most three earlier entries with the same name+attributes
       may precede a new one before the oldest is dropped. */
    int matches = 0;
    Py_ssize_t earliest = -1;
    for (Py_ssize_t index = tree->afe_len - 1; index >= 0; index--) {
        th_node *entry = tree->afe[index];
        if (entry == NULL) {
            break; /* stop at the marker */
        }
        if (entry->atom == node->atom && entry->attr_count == node->attr_count) {
            int same = 1;
            for (Py_ssize_t aidx = 0; aidx < node->attr_count && same; aidx++) {
                if (entry->attrs[aidx].name_atom != node->attrs[aidx].name_atom ||
                    entry->attrs[aidx].value_len != node->attrs[aidx].value_len ||
                    memcmp(entry->attrs[aidx].value, node->attrs[aidx].value,
                           (size_t)node->attrs[aidx].value_len * sizeof(Py_UCS4)) != 0) {
                    same = 0;
                }
            }
            if (same) {
                matches++;
                earliest = index;
            }
        }
    }
    if (matches >= 3) {
        afe_remove_at(tree, earliest);
    }
    if (tree->afe_len == tree->afe_cap) {
        size_t cap;
        size_t bytes;
        int grew = th_grow_cap((size_t)(tree->afe_cap + 1), (size_t)tree->afe_cap, 16, sizeof(th_node *), &cap, &bytes);
        if (!grew) {          /* GCOVR_EXCL_BR_LINE: size overflow needs a length no allocation could hold */
            tree->failed = 1; /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
            return 0;         /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
        }
        th_node **grown = PyMem_Realloc(tree->afe, bytes);
        if (grown == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            tree->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
            return 0;         /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        }
        tree->afe = grown;
        tree->afe_cap = (Py_ssize_t)cap;
    }
    tree->afe[tree->afe_len++] = node;
    return 1;
}

static void afe_push_marker(th_tree *tree) {
    if (tree->afe_len == tree->afe_cap) {
        size_t cap;
        size_t bytes;
        int grew = th_grow_cap((size_t)(tree->afe_cap + 1), (size_t)tree->afe_cap, 16, sizeof(th_node *), &cap, &bytes);
        if (!grew) {          /* GCOVR_EXCL_BR_LINE: size overflow needs a length no allocation could hold */
            tree->failed = 1; /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
            return;           /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
        }
        th_node **grown = PyMem_Realloc(tree->afe, bytes);
        if (grown == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            tree->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
            return;           /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        }
        tree->afe = grown;
        tree->afe_cap = (Py_ssize_t)cap;
    }
    tree->afe[tree->afe_len++] = NULL;
}

static void afe_clear_to_marker(th_tree *tree) {
    /* afe_clear_to_marker always runs with a marker on the list */
    while (tree->afe_len > 0 /* GCOVR_EXCL_BR_LINE */) {
        th_node *entry = tree->afe[--tree->afe_len];
        if (entry == NULL) {
            return;
        }
    }
}

static Py_ssize_t afe_index_of(th_tree *tree, th_node *node) {
    for (Py_ssize_t index = tree->afe_len - 1; index >= 0; index--) {
        if (tree->afe[index] == node) {
            return index;
        }
    }
    return -1;
}

static Py_ssize_t stack_index_of(th_tree *tree, th_node *node) {
    for (Py_ssize_t index = tree->open_len - 1; index >= 0; index--) {
        if (tree->open[index] == node) {
            return index;
        }
    }
    return -1;
}

static Py_ssize_t stack_index_of_atom(th_tree *tree, uint16_t atom) {
    for (Py_ssize_t index = tree->open_len - 1; index >= 0; index--) {
        if (tree->open[index]->atom == atom) {
            return index;
        }
    }
    return -1;
}

/* The latest active formatting element with this atom after the last marker, or
   NULL. */
static th_node *afe_find_atom(th_tree *tree, uint16_t atom) {
    for (Py_ssize_t index = tree->afe_len - 1; index >= 0; index--) {
        th_node *entry = tree->afe[index];
        if (entry == NULL) {
            return NULL;
        }
        if (entry->atom == atom) {
            return entry;
        }
    }
    return NULL;
}

/* Reconstruct the active formatting elements (re-open any that fell off the
   stack of open elements) per the spec. */
static void reconstruct_afe(th_tree *tree) {
    if (tree->afe_len == 0) {
        return;
    }
    Py_ssize_t index = tree->afe_len - 1;
    if (tree->afe[index] == NULL || stack_index_of(tree, tree->afe[index]) >= 0) {
        return;
    }
    while (index > 0) {
        index--;
        if (tree->afe[index] == NULL || stack_index_of(tree, tree->afe[index]) >= 0) {
            index++;
            break;
        }
    }
    for (; index < tree->afe_len; index++) {
        th_node *clone = node_clone(tree, tree->afe[index]);
        if (clone == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return;          /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        }
        /* A reconstructed element is placed at the appropriate insertion location,
           so it is foster-parented out of a table when foster parenting is active. */
        th_node *parent, *before;
        insertion_location(tree, &parent, &before);
        node_insert_before(parent, clone, before);
        stack_push(tree, clone);
        tree->afe[index] = clone;
    }
}

static int is_special_atom(uint16_t atom, uint8_t flags) {
    (void)atom;
    return (flags & TH_TAG_SPECIAL) != 0;
}

/* The adoption agency algorithm: repair misnested formatting elements such as
   <b><i></b></i>. Returns 1 when it handled the end tag, 0 to fall through to
   the "any other end tag" path. */
static int adoption_agency(th_tree *tree, uint16_t atom) {
    /* if the current node is a matching formatting element not in the list, pop it */
    th_node *cur = current_node(tree);
    if (cur->atom == atom && afe_index_of(tree, cur) < 0) {
        stack_pop(tree);
        return 1;
    }
    for (int outer = 0; outer < 8; outer++) {
        th_node *fmt = afe_find_atom(tree, atom);
        if (fmt == NULL) {
            return 0; /* no entry: treat as any other end tag */
        }
        Py_ssize_t fmt_stack = stack_index_of(tree, fmt);
        if (fmt_stack < 0) {
            /* not on the stack: parse error, remove from the list */
            Py_ssize_t fi = afe_index_of(tree, fmt);
            afe_remove_at(tree, fi);
            return 1;
        }
        /* in scope? The scope set includes the MathML/SVG integration points, so a
           foreign scope boundary between the current node and fmt keeps fmt out of
           scope and the end tag is ignored rather than running adoption. */
        int in_scope = 0;
        /* The html element bounds the stack walk. */
        for (Py_ssize_t index = tree->open_len - 1; index >= 0; index--) { /* GCOVR_EXCL_BR_LINE */
            if (tree->open[index] == fmt) {
                in_scope = 1;
                break;
            }
            if (is_scope_boundary(tree->open[index])) {
                break;
            }
        }
        if (!in_scope) {
            return 1;
        }
        /* furthest block: topmost special element below fmt on the stack */
        th_node *furthest = NULL;
        Py_ssize_t furthest_idx = -1;
        for (Py_ssize_t index = fmt_stack + 1; index < tree->open_len; index++) {
            if (is_special_atom(tree->open[index]->atom, tree->open[index]->tag_flags)) {
                furthest = tree->open[index];
                furthest_idx = index;
                break;
            }
        }
        if (furthest == NULL) {
            /* pop up to and including fmt, remove fmt from the list */
            while (tree->open_len > fmt_stack) {
                stack_pop(tree);
            }
            Py_ssize_t fi = afe_index_of(tree, fmt);
            afe_remove_at(tree, fi);
            return 1;
        }
        th_node *common = tree->open[fmt_stack - 1];
        Py_ssize_t bookmark = afe_index_of(tree, fmt);
        th_node *node, *last = furthest;
        Py_ssize_t node_idx = furthest_idx;
        /* The inner loop runs until it reaches the formatting element; the
           counter only governs when a still-listed node is dropped (lexbor
           state 14). An earlier fixed 3-iteration cap misnested deep formatting
           elements such as <b><em><foo><foo><foo>. */
        int inner_counter = 0;
        /* the formatting element always sits below the furthest block */
        while (node_idx > 0 /* GCOVR_EXCL_BR_LINE */) {
            inner_counter++;
            node_idx--;
            node = tree->open[node_idx];
            if (node == fmt) {
                break;
            }
            Py_ssize_t node_afe = afe_index_of(tree, node);
            if (inner_counter > 3 && node_afe >= 0) {
                afe_remove_at(tree, node_afe);
                if (node_afe < bookmark) {
                    bookmark--;
                }
                node_afe = -1;
            }
            if (node_afe < 0) {
                /* not in the list: remove from the stack and continue */
                stack_remove_at(tree, node_idx);
                continue;
            }
            th_node *clone = node_clone(tree, node);
            if (clone == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                return 1;        /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
            }
            tree->afe[node_afe] = clone;
            tree->open[node_idx] = clone;
            node = clone;
            if (last == furthest) {
                bookmark = node_afe + 1;
            }
            node_remove(last);
            node_append(node, last);
            last = node;
        }
        node_remove(last);
        /* insert last at the appropriate place with common as override target
           (template content redirect and foster parenting both apply) */
        {
            th_node *parent, *before;
            insertion_location_target(tree, common, &parent, &before);
            node_insert_before(parent, last, before);
        }
        th_node *fmt_clone = node_clone(tree, fmt);
        if (fmt_clone == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return 1;            /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        }
        /* move furthest block's children to the fmt clone, then append it */
        th_node *child = furthest->first_child;
        while (child != NULL) {
            th_node *next = child->next_sibling;
            node_remove(child);
            node_append(fmt_clone, child);
            child = next;
        }
        node_append(furthest, fmt_clone);
        /* remove fmt from the list, insert the clone at the bookmark */
        Py_ssize_t fi = afe_index_of(tree, fmt);
        if (fi < bookmark) {
            bookmark--;
        }
        afe_remove_at(tree, fi);
        /* GCOVR_EXCL_START: the list just shrank by one (fmt removed) before this single
           re-insert, so afe_len < afe_cap here and the grow never fires */
        if (tree->afe_len == tree->afe_cap) {
            size_t cap;
            size_t bytes;
            int grew =
                th_grow_cap((size_t)(tree->afe_cap + 1), (size_t)tree->afe_cap, 16, sizeof(th_node *), &cap, &bytes);
            if (!grew) {
                tree->failed = 1;
                return 1;
            }
            th_node **grown = PyMem_Realloc(tree->afe, bytes);
            if (grown == NULL) {
                tree->failed = 1;
                return 1;
            }
            tree->afe = grown;
            tree->afe_cap = (Py_ssize_t)cap;
        }
        /* GCOVR_EXCL_STOP */
        /* GCOVR_EXCL_START: the bookmark is kept within [0, afe_len] by the loop above; these are defensive clamps */
        if (bookmark < 0) {
            bookmark = 0;
        }
        if (bookmark > tree->afe_len) {
            bookmark = tree->afe_len;
        }
        /* GCOVR_EXCL_STOP */
        memmove(&tree->afe[bookmark + 1], &tree->afe[bookmark], (size_t)(tree->afe_len - bookmark) * sizeof(th_node *));
        tree->afe[bookmark] = fmt_clone;
        tree->afe_len++;
        /* remove fmt from the stack, insert the clone below furthest */
        Py_ssize_t fs = stack_index_of(tree, fmt);
        stack_remove_at(tree, fs);
        Py_ssize_t furthest_now = stack_index_of(tree, furthest);
        if (!stack_push(tree, NULL)) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return 1;                  /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        }
        memmove(&tree->open[furthest_now + 2], &tree->open[furthest_now + 1],
                (size_t)(tree->open_len - furthest_now - 2) * sizeof(th_node *));
        tree->open[furthest_now + 1] = fmt_clone;
    }
    return 1;
}

/* has_in_scope but with <button> also a scope boundary (the "button scope"). */
static int has_in_button_scope(th_tree *tree, uint16_t atom) {
    for (Py_ssize_t index = tree->open_len - 1; index >= 0; index--) {
        th_node *node = tree->open[index];
        if (node->ns == TH_NS_HTML && node->atom == atom) {
            return 1;
        }
        if (is_scope_boundary(node) || (node->ns == TH_NS_HTML && node->atom == TH_TAG_BUTTON)) {
            return 0;
        }
    }
    return 0;
}

/* The spec's "close a p element" step: when a p is in button scope, generate
   implied end tags (except p) and pop the stack through that p. */
static void close_p_in_button_scope(th_tree *tree) {
    if (has_in_button_scope(tree, TH_TAG_P)) {
        generate_implied_end_tags(tree, TH_TAG_P);
        pop_until_atom(tree, TH_TAG_P);
    }
}

static int is_heading_atom(uint16_t atom) {
    return atom == TH_TAG_H1 || atom == TH_TAG_H2 || atom == TH_TAG_H3 || atom == TH_TAG_H4 || atom == TH_TAG_H5 ||
           atom == TH_TAG_H6;
}

/* Start tags that close an open <p> in button scope before being inserted. */
static int is_p_closing_block(uint16_t atom) {
    switch (atom) {
    case TH_TAG_ADDRESS:
    case TH_TAG_ARTICLE:
    case TH_TAG_ASIDE:
    case TH_TAG_BLOCKQUOTE:
    case TH_TAG_CENTER:
    case TH_TAG_DETAILS:
    case TH_TAG_DIALOG:
    case TH_TAG_DIR:
    case TH_TAG_DIV:
    case TH_TAG_DL:
    case TH_TAG_FIELDSET:
    case TH_TAG_FIGCAPTION:
    case TH_TAG_FIGURE:
    case TH_TAG_FOOTER:
    case TH_TAG_HEADER:
    case TH_TAG_HGROUP:
    case TH_TAG_MAIN:
    case TH_TAG_MENU:
    case TH_TAG_NAV:
    case TH_TAG_OL:
    case TH_TAG_P:
    case TH_TAG_SEARCH:
    case TH_TAG_SECTION:
    case TH_TAG_SUMMARY:
    case TH_TAG_UL:
    case TH_TAG_H1:
    case TH_TAG_H2:
    case TH_TAG_H3:
    case TH_TAG_H4:
    case TH_TAG_H5:
    case TH_TAG_H6:
    case TH_TAG_PRE:
    case TH_TAG_LISTING:
    case TH_TAG_PLAINTEXT:
        return 1;
    default:
        return 0;
    }
}

/* End tags that close a block element in scope (closing implied end tags such
   as an open <p> on the way). */
static int is_block_end_atom(uint16_t atom) {
    switch (atom) {
    case TH_TAG_ADDRESS:
    case TH_TAG_ARTICLE:
    case TH_TAG_ASIDE:
    case TH_TAG_BLOCKQUOTE:
    case TH_TAG_BUTTON:
    case TH_TAG_CENTER:
    case TH_TAG_DETAILS:
    case TH_TAG_DIALOG:
    case TH_TAG_DIR:
    case TH_TAG_DIV:
    case TH_TAG_DL:
    case TH_TAG_FIELDSET:
    case TH_TAG_FIGCAPTION:
    case TH_TAG_FIGURE:
    case TH_TAG_FOOTER:
    case TH_TAG_HEADER:
    case TH_TAG_HGROUP:
    case TH_TAG_LISTING:
    case TH_TAG_MAIN:
    case TH_TAG_MENU:
    case TH_TAG_NAV:
    case TH_TAG_OL:
    case TH_TAG_PRE:
    case TH_TAG_SEARCH:
    case TH_TAG_SECTION:
    case TH_TAG_SELECT:
    case TH_TAG_SUMMARY:
    case TH_TAG_UL:
        return 1;
    default:
        return 0;
    }
}

/* Does this element's tag name equal the end-tag token's name? The token name
   is already lowercase; fold lets a mixed-case foreign element name (an SVG
   foreignObject matching </foreignobject>) compare case-insensitively, while an
   HTML element (already lowercase) passes fold=0. */
static int name_matches(const th_node *node, const th_token *token, int fold) {
    if (node->text_len != token->name.len) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < node->text_len; index++) {
        Py_UCS4 folded = fold ? lower_ascii(node->text[index]) : node->text[index];
        if (folded != buf_read(&token->name, index)) {
            return 0;
        }
    }
    return 1;
}

/* "Any other end tag" in body: walk the stack from the current node; pop to a
   match (after implied end tags), or stop at a special element. Unknown
   elements (atom 0) match by name so <foo></foo> closes correctly. */
static void any_other_end_tag(th_tree *tree, uint16_t atom, const th_token *token) {
    /* the html element is a special element, so any-other-end-tag stops before the stack bottom */
    for (Py_ssize_t index = tree->open_len - 1; index >= 0 /* GCOVR_EXCL_BR_LINE */; index--) {
        th_node *node = tree->open[index];
        int match =
            node->ns == TH_NS_HTML && (atom != TH_TAG_UNKNOWN ? node->atom == atom : name_matches(node, token, 0));
        if (match) {
            generate_implied_end_tags(tree, atom);
            while (tree->open_len > index) {
                stack_pop(tree);
            }
            return;
        }
        if (node->tag_flags & TH_TAG_SPECIAL) {
            return; /* ignore the end tag */
        }
    }
}

enum mode {
    M_INITIAL,
    M_BEFORE_HTML,
    M_BEFORE_HEAD,
    M_IN_HEAD,
    M_IN_HEAD_NOSCRIPT,
    M_AFTER_HEAD,
    M_IN_BODY,
    M_TEXT,
    M_IN_TABLE,
    M_IN_CAPTION,
    M_IN_COLUMN_GROUP,
    M_IN_TABLE_BODY,
    M_IN_ROW,
    M_IN_CELL,
    M_IN_TEMPLATE,
    M_IN_FRAMESET,
    M_AFTER_FRAMESET,
    M_AFTER_BODY,
    M_AFTER_AFTER_BODY,
    M_AFTER_AFTER_FRAMESET,
};

/* The stack of template insertion modes: a <template> pushes one, the
   in-template mode replaces its top as content selects a sub-mode, and reset
   returns the top so a closing sub-context resumes the template correctly. */
static void tmpl_push(th_tree *tree, enum mode mode) {
    if (tree->tmpl_len == tree->tmpl_cap) {
        size_t cap;
        size_t bytes;
        int grew = th_grow_cap((size_t)(tree->tmpl_cap + 1), (size_t)tree->tmpl_cap, 8, sizeof(int), &cap, &bytes);
        if (!grew) {          /* GCOVR_EXCL_BR_LINE: size overflow needs a length no allocation could hold */
            tree->failed = 1; /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
            return;           /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
        }
        int *grown = PyMem_Realloc(tree->tmpl, bytes);
        if (grown == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            tree->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
            return;           /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        }
        tree->tmpl = grown;
        tree->tmpl_cap = (Py_ssize_t)cap;
    }
    tree->tmpl[tree->tmpl_len++] = (int)mode;
}

static void tmpl_pop(th_tree *tree) {
    if (tree->tmpl_len > 0) { /* GCOVR_EXCL_BR_LINE: tmpl stack non-empty here */
        tree->tmpl_len--;
    }
}

/* Replace the current template insertion mode (pop + push). */
static void tmpl_set(th_tree *tree, enum mode mode) {
    /* tmpl_set runs only while a template insertion mode is on the stack. */
    if (tree->tmpl_len > 0 /* GCOVR_EXCL_BR_LINE */) {
        tree->tmpl[tree->tmpl_len - 1] = (int)mode;
    }
}

/* tmpl_top runs only for a template element, which carries a pushed mode. */
static enum mode tmpl_top(th_tree *tree) {
    return tree->tmpl_len > 0 /* GCOVR_EXCL_BR_LINE */ ? (enum mode)tree->tmpl[tree->tmpl_len - 1] : M_IN_BODY;
}

/* Reset the insertion mode appropriately: pick the mode implied by the current
   stack of open elements (used when leaving cells, captions, selects, …). */
static enum mode reset_insertion_mode(th_tree *tree) {
    /* context resolves the bottom element */
    for (Py_ssize_t index = tree->open_len - 1; index >= 0; index--) { /* GCOVR_EXCL_BR_LINE */
        th_node *node = tree->open[index];
        int last = (index == 0);
        /* Fragment case: the bottom of the stack stands in for the context. */
        uint16_t atom = last && tree->fragment_root != NULL ? tree->ctx_atom : node->atom;
        /* the frameset arm is unreachable; the remaining arms are exercised by line coverage */
        switch (atom) /* GCOVR_EXCL_BR_LINE */ {
        case TH_TAG_TD:
        case TH_TAG_TH:
            if (!last) {
                return M_IN_CELL;
            }
            break;
        case TH_TAG_TR:
            return M_IN_ROW;
        case TH_TAG_TBODY:
        case TH_TAG_THEAD:
        case TH_TAG_TFOOT:
            return M_IN_TABLE_BODY;
        case TH_TAG_CAPTION:
            return M_IN_CAPTION;
        case TH_TAG_COLGROUP:
            return M_IN_COLUMN_GROUP;
        case TH_TAG_TABLE:
            return M_IN_TABLE;
        case TH_TAG_TEMPLATE:
            return tmpl_top(tree);
        case TH_TAG_HEAD:
            if (!last) {
                return M_IN_HEAD;
            }
            break;
        case TH_TAG_BODY:
            return M_IN_BODY;
        case TH_TAG_FRAMESET:     /* GCOVR_EXCL_LINE: a frameset is never on the stack when reset is invoked */
            return M_IN_FRAMESET; /* GCOVR_EXCL_LINE: a frameset is never on the stack when reset is invoked */
        case TH_TAG_HTML:
            /* a head element exists by the time reset reaches the html element */
            return tree->head == NULL /* GCOVR_EXCL_BR_LINE */ ? M_BEFORE_HEAD : M_AFTER_HEAD;
        default:
            break;
        }
        if (last) {
            return M_IN_BODY;
        }
    }
    return M_IN_BODY; /* GCOVR_EXCL_LINE: reset always runs with the html root present and returns on the last iteration
                       */
}

/* The frameset modes keep a text token's whitespace characters and drop the
   rest (per-character, not per-token); compacts in place and inserts. */
static void insert_whitespace_only(th_tree *tree, Py_UCS4 *text, Py_ssize_t len) {
    Py_ssize_t write_pos = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_UCS4 character = text[index];
        if (is_space(character)) {
            text[write_pos++] = character;
        }
    }
    insert_text(tree, text, write_pos);
}

/* All-whitespace text per the tree-construction "space characters" set. */
static int all_whitespace(const Py_UCS4 *text, Py_ssize_t len) {
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_UCS4 character = text[index];
        if (!is_space(character)) {
            return 0;
        }
    }
    return 1;
}

/* Like all_whitespace, but a U+0000 counts as ignorable: in "in table text" a NUL is
   a parse error that is dropped, so an otherwise-whitespace run is still inserted in
   the table rather than foster-parented out of it. */
static int all_whitespace_or_nul(const Py_UCS4 *text, Py_ssize_t len) {
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_UCS4 character = text[index];
        if (character != 0 && !is_space(character)) {
            return 0;
        }
    }
    return 1;
}

/* The content model a start tag switches the tokenizer into, or -1 for none. */
static int content_model_for(uint16_t atom, uint8_t flags, int scripting) {
    if (atom == TH_TAG_SCRIPT) {
        return TH_INIT_SCRIPT_DATA;
    }
    if (atom == TH_TAG_NOSCRIPT) {
        return scripting ? TH_INIT_RAWTEXT : -1; /* rawtext with scripting on, else parsed as markup */
    }
    if (flags & TH_TAG_RCDATA) {
        return TH_INIT_RCDATA;
    }
    if (flags & TH_TAG_RAWTEXT) {
        return TH_INIT_RAWTEXT;
    }
    if (atom == TH_TAG_PLAINTEXT) {
        return TH_INIT_PLAINTEXT;
    }
    return -1;
}

/* Forward declaration: a fabricated start tag for implicit html/head/body. */
static th_node *insert_implicit(th_tree *tree, const char *name, uint16_t atom, uint8_t flags);

static th_node *insert_implicit(th_tree *tree, const char *name, uint16_t atom, uint8_t flags) {
    th_node *node = node_new(tree, TH_NODE_ELEMENT);
    if (node == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    Py_ssize_t len = (Py_ssize_t)strlen(name);
    node->text = arena_alloc(tree, len * (Py_ssize_t)sizeof(Py_UCS4));
    if (node->text == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;          /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        node->text[index] = (Py_UCS4)name[index];
    }
    node->text_len = len;
    node->atom = atom;
    node->tag_flags = flags;
    th_node *parent, *before;
    insertion_location(tree, &parent, &before);
    node_insert_before(parent, node, before);
    return node;
}

/* The driver: pull tokens, dispatch by mode, reprocess on mode change. */
/* The insertion-mode state that must persist across a streaming parser's feeds.
   A one-shot parse keeps it on the stack for one run_drain; a streaming parse
   stores it in th_stream so a feed that suspends mid-document resumes exactly
   where it left off. table_origin is reset at the top of every token iteration,
   so it never crosses a feed and stays a run_drain local. */
typedef struct {
    enum mode mode;
    enum mode original_mode;
    enum mode foster_return;
    int foster_pending;
} th_run_state;

static void run_state_init(th_run_state *run_state, enum mode start_mode) {
    run_state->mode = start_mode;
    run_state->original_mode = M_IN_BODY;
    run_state->foster_return = M_IN_TABLE;
    run_state->foster_pending = 0;
}

/* What one insertion-mode handler reads and updates for the token it is handed:
   the tokenizer (to switch its scanner state) plus the insertion-mode state. The
   four mode fields mirror th_run_state; table_origin is per-token and never crosses
   a feed. A handler returns TH_DRAIN_NEXT to advance to the next token, or
   TH_DRAIN_REPROCESS to run the same token again under the mode it just set. */
typedef struct {
    th_tokenizer *sm;
    enum mode mode;
    enum mode original_mode;
    enum mode foster_return;
    enum mode table_origin;
    int foster_pending;
} th_insert;

enum th_drain { TH_DRAIN_NEXT, TH_DRAIN_REPROCESS };

static enum th_drain drain_initial(th_tree *tree, th_token *tok, th_insert *dc) {
    if (tok->kind == TH_TEXT) {
        Py_ssize_t len;
        Py_UCS4 *text = token_text(tree, tok, &len);
        if (all_whitespace(text, len)) {
            return TH_DRAIN_NEXT; /* ignore leading whitespace */
        }
        tree->quirks = 1; /* no doctype before content */
        dc->mode = M_BEFORE_HTML;
        return TH_DRAIN_REPROCESS;
    }
    if (tok->kind == TH_DOCTYPE) {
        th_node *node = node_new(tree, TH_NODE_DOCTYPE);
        if (node != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
            node->text = build_doctype_text(tree, tok, &node->text_len);
            node->attr_count = tok->has_public_id ? tok->public_id.len : 0; /* public-id split point */
            node->tag_flags = (uint8_t)((tok->has_public_id ? TH_DOCTYPE_HAS_PUBLIC : 0) |
                                        (tok->has_system_id ? TH_DOCTYPE_HAS_SYSTEM : 0));
            node_append(tree->document, node);
        }
        tree->quirks = doctype_is_quirky(tok);
        dc->mode = M_BEFORE_HTML;
        return TH_DRAIN_NEXT;
    }
    if (tok->kind == TH_COMMENT) {
        insert_comment(tree, tok, tree->document);
        return TH_DRAIN_NEXT;
    }
    tree->quirks = 1; /* no doctype before content */
    dc->mode = M_BEFORE_HTML;
    return TH_DRAIN_REPROCESS;
}

static enum th_drain drain_before_html(th_tree *tree, th_token *tok, th_insert *dc) {
    if (tok->kind == TH_COMMENT) {
        insert_comment(tree, tok, tree->document);
        return TH_DRAIN_NEXT;
    }
    if (tok->kind == TH_TEXT) {
        Py_ssize_t len;
        Py_UCS4 *text = token_text(tree, tok, &len);
        if (all_whitespace(text, len)) {
            return TH_DRAIN_NEXT;
        }
    }
    if (tok->kind == TH_START_TAG && tok_atom(tok) == TH_TAG_HTML) {
        th_node *html = insert_element(tree, tok);
        if (html != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
            stack_push(tree, html);
        }
        dc->mode = M_BEFORE_HEAD;
        return TH_DRAIN_NEXT;
    }
    if (tok->kind == TH_END_TAG) {
        uint16_t end_atom = tok_atom(tok);
        if (end_atom != TH_TAG_HEAD && end_atom != TH_TAG_BODY && end_atom != TH_TAG_HTML && end_atom != TH_TAG_BR) {
            return TH_DRAIN_NEXT; /* any other end tag before html is a parse error and ignored */
        }
    }
    {
        th_node *html = insert_implicit(tree, "html", TH_TAG_HTML, TH_TAG_SPECIAL | TH_TAG_SCOPING);
        if (html != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
            stack_push(tree, html);
        }
    }
    dc->mode = M_BEFORE_HEAD;
    return TH_DRAIN_REPROCESS;
}

static enum th_drain drain_before_head(th_tree *tree, th_token *tok, th_insert *dc) {
    if (tok->kind == TH_COMMENT) {
        insert_comment(tree, tok, NULL);
        return TH_DRAIN_NEXT;
    }
    if (tok->kind == TH_TEXT) {
        Py_ssize_t len;
        Py_UCS4 *text = token_text(tree, tok, &len);
        Py_ssize_t ws = 0;
        while (ws < len && (is_space(text[ws]))) {
            ws++;
        }
        if (ws == len) {
            return TH_DRAIN_NEXT; /* whitespace only: ignored before the head */
        }
        tree->text_offset += ws; /* the whitespace prefix is dropped, not moved */
    }
    if (tok->kind == TH_START_TAG && tok_atom(tok) == TH_TAG_HTML) {
        /* a redundant html start tag is processed via the in-body rules
           (merge attributes onto the open html), leaving the mode intact */
        if (tree->open_len > 0 && tree->open[0]->atom == TH_TAG_HTML && /* GCOVR_EXCL_BR_LINE */
            stack_index_of_atom(tree, TH_TAG_TEMPLATE) < 0) {
            merge_attrs(tree, tree->open[0], tok);
        }
        return TH_DRAIN_NEXT;
    }
    if (tok->kind == TH_START_TAG && tok_atom(tok) == TH_TAG_HEAD) {
        th_node *head = insert_element(tree, tok);
        if (head != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
            stack_push(tree, head);
            tree->head = head;
        }
        dc->mode = M_IN_HEAD;
        return TH_DRAIN_NEXT;
    }
    if (tok->kind == TH_END_TAG) {
        uint16_t end_atom = tok_atom(tok);
        if (end_atom != TH_TAG_HEAD && end_atom != TH_TAG_BODY && end_atom != TH_TAG_HTML && end_atom != TH_TAG_BR) {
            return TH_DRAIN_NEXT; /* any other end tag before head is a parse error and ignored */
        }
    }
    tree->head = insert_implicit(tree, "head", TH_TAG_HEAD, TH_TAG_SPECIAL);
    stack_push(tree, tree->head);
    dc->mode = M_IN_HEAD;
    return TH_DRAIN_REPROCESS;
}

static enum th_drain drain_in_head(th_tree *tree, th_token *tok, th_insert *dc) {
    if (tok->kind == TH_TEXT) {
        Py_ssize_t len;
        Py_UCS4 *text = token_text(tree, tok, &len);
        Py_ssize_t ws = 0;
        while (ws < len && (is_space(text[ws]))) {
            ws++;
        }
        if (ws > 0) {
            insert_text(tree, text, ws); /* leading whitespace stays in the head */
        }
        if (ws == len) {
            return TH_DRAIN_NEXT;
        }
        tree->text_offset += ws; /* reprocess only the remainder after the head */
        stack_pop(tree);         /* pop head */
        dc->mode = M_AFTER_HEAD;
        return TH_DRAIN_REPROCESS;
    }
    if (tok->kind == TH_COMMENT) {
        insert_comment(tree, tok, NULL);
        return TH_DRAIN_NEXT;
    }
    if (tok->kind == TH_START_TAG) {
        uint8_t flags = tok->tag_flags;
        uint16_t atom = tok->atom;
        if (atom == TH_TAG_HTML) {
            /* a redundant html start tag merges attributes via the in-body
               rules; the head insertion mode is left untouched */
            if (tree->open_len > 0 && tree->open[0]->atom == TH_TAG_HTML && /* GCOVR_EXCL_BR_LINE */
                stack_index_of_atom(tree, TH_TAG_TEMPLATE) < 0) {
                merge_attrs(tree, tree->open[0], tok);
            }
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_HEAD) {
            return TH_DRAIN_NEXT; /* a duplicate head start tag is a parse error, ignored */
        }
        /* only the head's own raw-text/RCDATA elements switch to text
           mode here; textarea/xmp/iframe/etc belong in the body */
        if (atom == TH_TAG_TITLE || atom == TH_TAG_STYLE || atom == TH_TAG_SCRIPT || atom == TH_TAG_NOFRAMES) {
            int model = content_model_for(atom, flags, tree->scripting);
            th_node *node = insert_element(tree, tok);
            if (node != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
                stack_push(tree, node);
            }
            th_tok_switch(dc->sm, (enum th_initial_state)model);
            dc->original_mode = M_IN_HEAD;
            dc->mode = M_TEXT;
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_BASE || atom == TH_TAG_BASEFONT || atom == TH_TAG_BGSOUND || atom == TH_TAG_LINK ||
            atom == TH_TAG_META) {
            insert_element(tree, tok); /* void metadata: inserted, not pushed */
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_NOSCRIPT) {
            th_node *node = insert_element(tree, tok);
            if (node != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
                stack_push(tree, node);
            }
            if (tree->scripting) {
                /* with scripting on, noscript follows the generic raw-text path */
                th_tok_switch(dc->sm, TH_INIT_RAWTEXT);
                dc->original_mode = M_IN_HEAD;
                dc->mode = M_TEXT;
                return TH_DRAIN_NEXT;
            }
            dc->mode = M_IN_HEAD_NOSCRIPT;
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_TEMPLATE) {
            th_node *node = insert_template(tree, tok);
            if (node != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
                stack_push(tree, node);
                afe_push_marker(tree);
            }
            tmpl_push(tree, M_IN_TEMPLATE);
            dc->mode = M_IN_TEMPLATE;
            return TH_DRAIN_NEXT;
        }
        /* anything else belongs after the head */
        stack_pop(tree);
        dc->mode = M_AFTER_HEAD;
        return TH_DRAIN_REPROCESS;
    }
    /* only an end tag reaches here: text/comment/start break above, a DOCTYPE is ignored before the switch */
    if (tok->kind == TH_END_TAG) { /* GCOVR_EXCL_BR_LINE: the non-end-tag branch is unreachable */
        uint16_t end_atom = tok_atom(tok);
        if (end_atom == TH_TAG_HEAD) {
            stack_pop(tree);
            dc->mode = M_AFTER_HEAD;
            return TH_DRAIN_NEXT;
        }
        if (end_atom != TH_TAG_BODY && end_atom != TH_TAG_HTML && end_atom != TH_TAG_BR) {
            return TH_DRAIN_NEXT; /* any other end tag in head is a parse error, ignored */
        }
    }
    stack_pop(tree);
    dc->mode = M_AFTER_HEAD;
    return TH_DRAIN_REPROCESS;
}

static enum th_drain drain_in_head_noscript(th_tree *tree, th_token *tok, th_insert *dc) {
    if (tok->kind == TH_END_TAG) {
        uint16_t end_atom = tok_atom(tok);
        if (end_atom == TH_TAG_NOSCRIPT) {
            stack_pop(tree); /* pop noscript */
            dc->mode = M_IN_HEAD;
            return TH_DRAIN_NEXT;
        }
        if (end_atom != TH_TAG_BR) {
            return TH_DRAIN_NEXT; /* any other end tag is a parse error, ignored */
        }
    }
    if (tok->kind == TH_START_TAG && tok_atom(tok) == TH_TAG_HTML) {
        /* process via the in-body rules (merges attributes) */
        /* open stack always holds html at index 0 */
        if (tree->open_len > 0 && tree->open[0]->atom == TH_TAG_HTML && /* GCOVR_EXCL_BR_LINE */
            stack_index_of_atom(tree, TH_TAG_TEMPLATE) < 0) {
            merge_attrs(tree, tree->open[0], tok);
        }
        return TH_DRAIN_NEXT;
    }
    if (tok->kind == TH_COMMENT) {
        insert_comment(tree, tok, NULL);
        return TH_DRAIN_NEXT;
    }
    if (tok->kind == TH_TEXT) {
        Py_ssize_t len;
        Py_UCS4 *text = token_text(tree, tok, &len);
        if (all_whitespace(text, len)) {
            insert_text(tree, text, len);
            return TH_DRAIN_NEXT;
        }
    }
    if (tok->kind == TH_START_TAG) {
        uint16_t atom = tok_atom(tok);
        if (atom == TH_TAG_BASEFONT || atom == TH_TAG_BGSOUND || atom == TH_TAG_LINK || atom == TH_TAG_META) {
            insert_element(tree, tok); /* void metadata */
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_STYLE || atom == TH_TAG_NOFRAMES) {
            th_node *node = insert_element(tree, tok);
            if (node != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
                stack_push(tree, node);
            }
            th_tok_switch(dc->sm, TH_INIT_RAWTEXT);
            dc->original_mode = M_IN_HEAD_NOSCRIPT;
            dc->mode = M_TEXT;
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_HEAD || atom == TH_TAG_NOSCRIPT) {
            return TH_DRAIN_NEXT; /* ignore */
        }
    }
    /* anything else: pop the noscript (parse error) and reprocess in head */
    stack_pop(tree);
    dc->mode = M_IN_HEAD;
    return TH_DRAIN_REPROCESS;
}

static enum th_drain drain_after_head(th_tree *tree, th_token *tok, th_insert *dc) {
    if (tok->kind == TH_TEXT) {
        Py_ssize_t len;
        Py_UCS4 *text = token_text(tree, tok, &len);
        Py_ssize_t ws = 0;
        while (ws < len && is_space(text[ws])) {
            ws++;
        }
        if (ws > 0) {
            insert_text(tree, text, ws); /* leading whitespace goes under html, between head and body */
        }
        if (ws == len) {
            return TH_DRAIN_NEXT;
        }
        tree->text_offset += ws; /* the body is created below; reprocess only the remainder there */
    }
    if (tok->kind == TH_COMMENT) {
        insert_comment(tree, tok, NULL);
        return TH_DRAIN_NEXT;
    }
    if (tok->kind == TH_START_TAG && tok_atom(tok) == TH_TAG_BODY) {
        th_node *body = insert_element(tree, tok);
        if (body != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
            stack_push(tree, body);
        }
        tree->frameset_ok = 0; /* an explicit body can't be replaced by a frameset */
        dc->mode = M_IN_BODY;
        return TH_DRAIN_NEXT;
    }
    if (tok->kind == TH_START_TAG && tok_atom(tok) == TH_TAG_FRAMESET) {
        th_node *fs = insert_element(tree, tok);
        if (fs != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
            stack_push(tree, fs);
        }
        dc->mode = M_IN_FRAMESET;
        return TH_DRAIN_NEXT;
    }
    if (tok->kind == TH_START_TAG) {
        uint8_t flags = tok->tag_flags;
        uint16_t atom = tok->atom;
        /* head-content tags re-enter the head element, are processed
           there, and leave the head off the stack again */
        if (atom == TH_TAG_BASE || atom == TH_TAG_BASEFONT || atom == TH_TAG_BGSOUND || atom == TH_TAG_LINK ||
            atom == TH_TAG_META || atom == TH_TAG_TITLE || atom == TH_TAG_STYLE || atom == TH_TAG_SCRIPT ||
            atom == TH_TAG_NOFRAMES || atom == TH_TAG_TEMPLATE) {
            /* after-head is entered only once a head element exists */
            if (tree->head != NULL /* GCOVR_EXCL_BR_LINE */) {
                stack_push(tree, tree->head); /* insert into the head element */
                if (atom == TH_TAG_TEMPLATE) {
                    th_node *node = insert_template(tree, tok);
                    /* the head is removed from under the template on the
                       stack; the template itself stays open */
                    tree->open[tree->open_len - 1] = node;
                    if (node != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
                        afe_push_marker(tree);
                        tmpl_push(tree, M_IN_TEMPLATE);
                        dc->mode = M_IN_TEMPLATE;
                    } else {
                        stack_pop(tree); /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
                    }
                    return TH_DRAIN_NEXT;
                }
                th_node *node = insert_element(tree, tok);
                stack_pop(tree); /* remove head again */
                if (atom == TH_TAG_TITLE || atom == TH_TAG_STYLE || atom == TH_TAG_SCRIPT || atom == TH_TAG_NOFRAMES) {
                    if (node != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
                        stack_push(tree, node);
                    }
                    th_tok_switch(dc->sm, (enum th_initial_state)content_model_for(atom, flags, tree->scripting));
                    dc->original_mode = M_AFTER_HEAD;
                    dc->mode = M_TEXT;
                }
            }
            return TH_DRAIN_NEXT;
        }
    }
    if (tok->kind == TH_END_TAG) {
        uint16_t end_atom = tok_atom(tok);
        if (end_atom != TH_TAG_BODY && end_atom != TH_TAG_HTML && end_atom != TH_TAG_BR) {
            return TH_DRAIN_NEXT; /* any other end tag after head is a parse error, ignored */
        }
    }
    {
        th_node *body = insert_implicit(tree, "body", TH_TAG_BODY, TH_TAG_SPECIAL);
        if (body != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
            stack_push(tree, body);
        }
    }
    dc->mode = M_IN_BODY;
    return TH_DRAIN_REPROCESS;
}

static enum th_drain drain_in_template(th_tree *tree, th_token *tok, th_insert *dc) {
    if (tok->kind == TH_END_TAG) {
        return TH_DRAIN_NEXT; /* </template> is handled by the dispatcher above; other end tags are ignored */
    }
    if (tok->kind == TH_START_TAG) {
        uint8_t flags = tok->tag_flags;
        uint16_t atom = tok->atom;
        if (atom == TH_TAG_TEMPLATE) {
            th_node *node = insert_template(tree, tok);
            if (node != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
                stack_push(tree, node);
                afe_push_marker(tree);
            }
            tmpl_push(tree, M_IN_TEMPLATE);
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_BASE || atom == TH_TAG_BASEFONT || atom == TH_TAG_BGSOUND || atom == TH_TAG_LINK ||
            atom == TH_TAG_META) {
            insert_element(tree, tok); /* void head content */
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_TITLE || atom == TH_TAG_STYLE || atom == TH_TAG_SCRIPT || atom == TH_TAG_NOFRAMES) {
            th_node *node = insert_element(tree, tok);
            if (node != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
                stack_push(tree, node);
            }
            th_tok_switch(dc->sm, (enum th_initial_state)content_model_for(atom, flags, tree->scripting));
            dc->original_mode = M_IN_TEMPLATE;
            dc->mode = M_TEXT;
            return TH_DRAIN_NEXT;
        }
        /* table-section tags select the matching table sub-mode (updating
           the template insertion stack so a later reset resumes here);
           everything else is body content */
        if (atom == TH_TAG_CAPTION || atom == TH_TAG_COLGROUP || atom == TH_TAG_TBODY || atom == TH_TAG_TFOOT ||
            atom == TH_TAG_THEAD) {
            tmpl_set(tree, M_IN_TABLE);
            dc->mode = M_IN_TABLE;
            return TH_DRAIN_REPROCESS;
        }
        if (atom == TH_TAG_COL) {
            tmpl_set(tree, M_IN_COLUMN_GROUP);
            dc->mode = M_IN_COLUMN_GROUP;
            return TH_DRAIN_REPROCESS;
        }
        if (atom == TH_TAG_TR) {
            tmpl_set(tree, M_IN_TABLE_BODY);
            dc->mode = M_IN_TABLE_BODY;
            return TH_DRAIN_REPROCESS;
        }
        if (atom == TH_TAG_TD || atom == TH_TAG_TH) {
            tmpl_set(tree, M_IN_ROW);
            dc->mode = M_IN_ROW;
            return TH_DRAIN_REPROCESS;
        }
        /* any other start tag switches the template to body content */
        tmpl_set(tree, M_IN_BODY);
        dc->mode = M_IN_BODY;
        return TH_DRAIN_REPROCESS;
    }
    /* characters and comments run the in-body rules, then return here */
    dc->foster_pending = 1;
    dc->foster_return = M_IN_TEMPLATE;
    dc->mode = M_IN_BODY;
    return TH_DRAIN_REPROCESS;
}

static enum th_drain drain_in_frameset(th_tree *tree, th_token *tok, th_insert *dc) {
    if (tok->kind == TH_TEXT) {
        Py_ssize_t len;
        Py_UCS4 *text = token_text(tree, tok, &len);
        insert_whitespace_only(tree, text, len);
        return TH_DRAIN_NEXT;
    }
    if (tok->kind == TH_COMMENT) {
        insert_comment(tree, tok, NULL);
        return TH_DRAIN_NEXT;
    }
    if (tok->kind == TH_START_TAG) {
        uint16_t atom = tok_atom(tok);
        if (atom == TH_TAG_FRAMESET) {
            th_node *fs = insert_element(tree, tok);
            if (fs != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
                stack_push(tree, fs);
            }
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_FRAME) {
            insert_element(tree, tok); /* void */
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_NOFRAMES) {
            th_node *node = insert_element(tree, tok);
            if (node != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
                stack_push(tree, node);
            }
            th_tok_switch(dc->sm, TH_INIT_RAWTEXT);
            dc->original_mode = M_IN_FRAMESET;
            dc->mode = M_TEXT;
            return TH_DRAIN_NEXT;
        }
        return TH_DRAIN_NEXT;
    }
    /* a DOCTYPE is ignored before the switch, so the kind!=end-tag branch here is dead */
    if (tok->kind == TH_END_TAG && tok_atom(tok) == TH_TAG_FRAMESET) { /* GCOVR_EXCL_BR_LINE */
        if (current_node(tree)->atom != TH_TAG_HTML) {
            stack_pop(tree);
        }
        if (tree->fragment_root == NULL && current_node(tree)->atom != TH_TAG_FRAMESET) {
            dc->mode = M_AFTER_FRAMESET;
        }
        return TH_DRAIN_NEXT;
    }
    return TH_DRAIN_NEXT;
}

static enum th_drain drain_after_frameset(th_tree *tree, th_token *tok, th_insert *dc) {
    if (tok->kind == TH_TEXT) {
        Py_ssize_t len;
        Py_UCS4 *text = token_text(tree, tok, &len);
        insert_whitespace_only(tree, text, len);
        return TH_DRAIN_NEXT;
    }
    if (tok->kind == TH_COMMENT) {
        insert_comment(tree, tok, NULL);
        return TH_DRAIN_NEXT;
    }
    if (tok->kind == TH_START_TAG && tok_atom(tok) == TH_TAG_NOFRAMES) {
        th_node *node = insert_element(tree, tok);
        if (node != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
            stack_push(tree, node);
        }
        th_tok_switch(dc->sm, TH_INIT_RAWTEXT);
        dc->original_mode = M_AFTER_FRAMESET;
        dc->mode = M_TEXT;
        return TH_DRAIN_NEXT;
    }
    if (tok->kind == TH_END_TAG && tok_atom(tok) == TH_TAG_HTML) {
        dc->mode = M_AFTER_AFTER_FRAMESET; /* only comments and whitespace remain */
    }
    return TH_DRAIN_NEXT;
}

static enum th_drain drain_in_body(th_tree *tree, th_token *tok, th_insert *dc) {
    if (tok->kind == TH_TEXT) {
        if (tok->is_slice && tree->can_span && !tree->has_nul) {
            /* zero-copy: scan the input span directly for the frameset
               and leading-newline rules, then store a span node */
            Py_ssize_t off = tok->src_start + tree->text_offset;
            Py_ssize_t len = tok->src_len - tree->text_offset;
            /* a text token always has a positive length */
            if (tree->drop_newline && len > 0 /* GCOVR_EXCL_BR_LINE */ && input_read(tree, off) == '\n') {
                off++;
                len--;
            }
            tree->drop_newline = 0;
            reconstruct_afe(tree);
            for (Py_ssize_t index = 0; index < len; index++) {
                Py_UCS4 character = input_read(tree, off + index);
                if (!is_space(character)) {
                    tree->frameset_ok = 0;
                    break;
                }
            }
            insert_text_span(tree, off, len);
            return TH_DRAIN_NEXT;
        }
        Py_ssize_t len;
        Py_UCS4 *text = token_text(tree, tok, &len);
        /* a text token always has a positive length */
        if (tree->drop_newline && len > 0 /* GCOVR_EXCL_BR_LINE */ && text[0] == '\n') {
            text++;
            len--;
        }
        tree->drop_newline = 0;
        reconstruct_afe(tree);
        /* a U+0000 is dropped and, like whitespace, does not clear
           frameset-ok; only a real visible character does */
        for (Py_ssize_t index = 0; index < len; index++) {
            Py_UCS4 character = text[index];
            if (!is_space(character) && character != 0) {
                tree->frameset_ok = 0;
                break;
            }
        }
        insert_text(tree, text, len);
        return TH_DRAIN_NEXT;
    }
    if (tok->kind == TH_COMMENT) {
        insert_comment(tree, tok, NULL);
        return TH_DRAIN_NEXT;
    }
    if (tok->kind == TH_START_TAG) {
        uint8_t flags = tok->tag_flags;
        uint16_t atom = tok->atom;
        if (atom_breaks_frameset(atom) || (atom == TH_TAG_INPUT && !input_is_hidden(tok))) {
            tree->frameset_ok = 0;
        }
        if (atom == TH_TAG_HTML) {
            /* open stack always holds html at index 0 */
            if (tree->open_len > 0 && tree->open[0]->atom == TH_TAG_HTML && /* GCOVR_EXCL_BR_LINE */
                stack_index_of_atom(tree, TH_TAG_TEMPLATE) < 0) {
                merge_attrs(tree, tree->open[0], tok);
            }
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_BODY) {
            if (tree->open_len > 1 && tree->open[1]->atom == TH_TAG_BODY &&
                stack_index_of_atom(tree, TH_TAG_TEMPLATE) < 0) {
                merge_attrs(tree, tree->open[1], tok);
            }
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_HEAD || atom == TH_TAG_CAPTION || atom == TH_TAG_COL || atom == TH_TAG_COLGROUP ||
            atom == TH_TAG_FRAME || atom == TH_TAG_TBODY || atom == TH_TAG_TD || atom == TH_TAG_TFOOT ||
            atom == TH_TAG_TH || atom == TH_TAG_THEAD || atom == TH_TAG_TR) {
            return TH_DRAIN_NEXT; /* stray table-structure tags are parse errors, ignored */
        }
        if (atom == TH_TAG_FRAMESET) {
            /* replaces an empty body when no non-whitespace content yet */
            if (tree->frameset_ok && tree->open_len > 1 && tree->open[1]->atom == TH_TAG_BODY) {
                node_remove(tree->open[1]);
                while (tree->open_len > 1) {
                    stack_pop(tree);
                }
                th_node *fs = insert_element(tree, tok);
                if (fs != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
                    stack_push(tree, fs);
                }
                dc->mode = M_IN_FRAMESET;
            }
            return TH_DRAIN_NEXT;
        }
        /* block and heading elements close an open p and are inserted
           WITHOUT reconstructing the active formatting elements */
        if (is_heading_atom(atom)) {
            close_p_in_button_scope(tree);
            if (is_heading_atom(current_node(tree)->atom)) {
                stack_pop(tree); /* nested headings don't stack */
            }
            th_node *node = insert_element(tree, tok);
            if (node != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
                stack_push(tree, node);
            }
            return TH_DRAIN_NEXT;
        }
        if (is_p_closing_block(atom) && atom != TH_TAG_PLAINTEXT && atom != TH_TAG_PRE &&
            atom != TH_TAG_LISTING) { /* pre/listing also drop a leading newline below */
            close_p_in_button_scope(tree);
            th_node *node = insert_element(tree, tok);
            if (node != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
                stack_push(tree, node);
            }
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_TABLE) {
            if (!tree->quirks && has_in_button_scope(tree, TH_TAG_P)) {
                generate_implied_end_tags(tree, TH_TAG_P);
                pop_until_atom(tree, TH_TAG_P);
            }
            th_node *node = insert_element(tree, tok);
            if (node != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
                stack_push(tree, node);
            }
            dc->mode = M_IN_TABLE;
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_SELECT) {
            /* select content is plain in-body content now; a nested
               select start closes the open one instead of nesting */
            if (tree->fragment_root != NULL && tree->ctx_atom == TH_TAG_SELECT) {
                return TH_DRAIN_NEXT; /* fragment case: ignored */
            }
            if (has_in_scope(tree, TH_TAG_SELECT)) {
                pop_until_atom(tree, TH_TAG_SELECT);
                return TH_DRAIN_NEXT;
            }
            reconstruct_afe(tree);
            th_node *node = insert_element(tree, tok);
            if (node != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
                stack_push(tree, node);
            }
            tree->frameset_ok = 0;
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_SVG || atom == TH_TAG_MATH) {
            reconstruct_afe(tree);
            th_node *node = insert_foreign(tree, tok, atom == TH_TAG_SVG ? TH_NS_SVG : TH_NS_MATHML);
            if (node != NULL && !tok->self_closing) { /* GCOVR_EXCL_BR_LINE: insert_foreign returns NULL only on
                                                         allocation failure */
                stack_push(tree, node);
            }
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_LI) {
            /* html bounds the stack walk */
            for (Py_ssize_t index = tree->open_len - 1; index >= 0; index--) { /* GCOVR_EXCL_BR_LINE */
                uint16_t open_atom = tree->open[index]->atom;
                if (open_atom == TH_TAG_LI) {
                    generate_implied_end_tags(tree, TH_TAG_LI);
                    pop_until_atom(tree, TH_TAG_LI);
                    break;
                }
                if ((tree->open[index]->tag_flags & TH_TAG_SPECIAL) && open_atom != TH_TAG_ADDRESS &&
                    open_atom != TH_TAG_DIV && open_atom != TH_TAG_P) {
                    break;
                }
            }
            close_p_in_button_scope(tree);
            th_node *node = insert_element(tree, tok);
            if (node != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
                stack_push(tree, node);
            }
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_DD || atom == TH_TAG_DT) {
            /* html bounds the stack walk */
            for (Py_ssize_t index = tree->open_len - 1; index >= 0; index--) { /* GCOVR_EXCL_BR_LINE */
                uint16_t open_atom = tree->open[index]->atom;
                if (open_atom == TH_TAG_DD || open_atom == TH_TAG_DT) {
                    generate_implied_end_tags(tree, open_atom);
                    pop_until_atom(tree, open_atom);
                    break;
                }
                if ((tree->open[index]->tag_flags & TH_TAG_SPECIAL) && open_atom != TH_TAG_ADDRESS &&
                    open_atom != TH_TAG_DIV && open_atom != TH_TAG_P) {
                    break;
                }
            }
            close_p_in_button_scope(tree);
            th_node *node = insert_element(tree, tok);
            if (node != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
                stack_push(tree, node);
            }
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_HR) {
            close_p_in_button_scope(tree);
            if (has_in_scope(tree, TH_TAG_SELECT) || (tree->fragment_root != NULL && tree->ctx_atom == TH_TAG_SELECT)) {
                /* in a select (or a select-context fragment) close the open option/optgroup
                   first, so the hr lands at select level rather than inside the option */
                generate_implied_end_tags(tree, TH_TAG_UNKNOWN);
            }
            insert_element(tree, tok); /* void */
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_BUTTON) {
            if (has_in_scope(tree, TH_TAG_BUTTON)) {
                generate_implied_end_tags(tree, TH_TAG_UNKNOWN);
                pop_until_atom(tree, TH_TAG_BUTTON);
            }
            reconstruct_afe(tree);
            th_node *node = insert_element(tree, tok);
            if (node != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
                stack_push(tree, node);
            }
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_OPTION || atom == TH_TAG_OPTGROUP) {
            if (has_in_scope(tree, TH_TAG_SELECT) || (tree->fragment_root != NULL && tree->ctx_atom == TH_TAG_SELECT)) {
                /* inside a select (or a select-context fragment, where no select node
                   is on the stack), implied end tags close open option and (for an
                   optgroup start) optgroup elements */
                generate_implied_end_tags(tree, atom == TH_TAG_OPTION ? TH_TAG_OPTGROUP : TH_TAG_UNKNOWN);
            } else if (current_node(tree)->atom == TH_TAG_OPTION) {
                stack_pop(tree);
            }
            reconstruct_afe(tree);
            th_node *node = insert_element(tree, tok);
            if (node != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
                stack_push(tree, node);
            }
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_RB || atom == TH_TAG_RTC) {
            if (has_in_scope(tree, TH_TAG_RUBY)) {
                generate_implied_end_tags(tree, TH_TAG_UNKNOWN);
            }
            th_node *node = insert_element(tree, tok);
            if (node != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
                stack_push(tree, node);
            }
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_RP || atom == TH_TAG_RT) {
            if (has_in_scope(tree, TH_TAG_RUBY)) {
                generate_implied_end_tags(tree, TH_TAG_RTC);
            }
            th_node *node = insert_element(tree, tok);
            if (node != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
                stack_push(tree, node);
            }
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_BASE || atom == TH_TAG_BASEFONT || atom == TH_TAG_BGSOUND || atom == TH_TAG_LINK ||
            atom == TH_TAG_META) {
            insert_element(tree, tok); /* head metadata: in-head rules, no AFE reconstruction */
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_FORM) {
            int in_template = stack_index_of_atom(tree, TH_TAG_TEMPLATE) >= 0;
            if (tree->form != NULL && !in_template) {
                return TH_DRAIN_NEXT; /* a nested form is ignored */
            }
            close_p_in_button_scope(tree);
            th_node *node = insert_element(tree, tok);
            if (node != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
                stack_push(tree, node);
                if (!in_template) {
                    tree->form = node;
                }
            }
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_INPUT || atom == TH_TAG_KEYGEN) {
            /* an input or keygen closes an open select entirely (and is
               ignored outright in a select-context fragment) */
            if (tree->fragment_root != NULL && tree->ctx_atom == TH_TAG_SELECT) {
                return TH_DRAIN_NEXT;
            }
            if (has_in_scope(tree, TH_TAG_SELECT)) {
                pop_until_atom(tree, TH_TAG_SELECT);
            }
            reconstruct_afe(tree);
            insert_element(tree, tok); /* void */
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_IMAGE) {
            /* the famous quirk: <image> becomes <img> */
            reconstruct_afe(tree);
            th_node *node = insert_element(tree, tok);
            if (node != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
                node->atom = TH_TAG_IMG;
                static const char img[] = "img";
                node->text = arena_alloc(tree, 3 * (Py_ssize_t)sizeof(Py_UCS4));
                if (node->text != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
                    for (int index = 0; index < 3; index++) {
                        node->text[index] = (Py_UCS4)img[index];
                    }
                    node->text_len = 3;
                }
            }
            return TH_DRAIN_NEXT; /* img is void */
        }
        if (atom == TH_TAG_PLAINTEXT) {
            close_p_in_button_scope(tree);
            th_node *node = insert_element(tree, tok);
            if (node != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
                stack_push(tree, node);
            }
            th_tok_switch(dc->sm, TH_INIT_PLAINTEXT);
            return TH_DRAIN_NEXT; /* PLAINTEXT runs to EOF; no end tag returns from it */
        }
        int model = content_model_for(atom, flags, tree->scripting);
        if (model >= 0) { /* rawtext / rcdata / script: no reconstruction */
            if (atom == TH_TAG_XMP) {
                close_p_in_button_scope(tree);
                reconstruct_afe(tree);
                tree->frameset_ok = 0;
            }
            th_node *node = insert_element(tree, tok);
            if (node != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
                stack_push(tree, node);
            }
            if (atom == TH_TAG_TEXTAREA) {
                tree->drop_newline = 1; /* a leading LF in a textarea is ignored */
            }
            th_tok_switch(dc->sm, (enum th_initial_state)model);
            /* when fostered out of a table the in-body rules run but the real insertion
               mode is still the table mode, so the text mode must return there, not to
               in body, or the table's later rows would be dropped */
            dc->original_mode = dc->foster_pending ? dc->foster_return : M_IN_BODY;
            dc->mode = M_TEXT;
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_PRE || atom == TH_TAG_LISTING) {
            close_p_in_button_scope(tree);
            th_node *node = insert_element(tree, tok);
            if (node != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
                stack_push(tree, node);
            }
            tree->drop_newline = 1; /* a leading LF in pre/listing is ignored */
            tree->frameset_ok = 0;
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_A) {
            th_node *existing = afe_find_atom(tree, TH_TAG_A);
            if (existing != NULL) {
                adoption_agency(tree, TH_TAG_A);
                Py_ssize_t ai = afe_index_of(tree, existing);
                if (ai >= 0) {
                    afe_remove_at(tree, ai);
                }
                Py_ssize_t si = stack_index_of(tree, existing);
                if (si >= 0) {
                    stack_remove_at(tree, si);
                }
            }
        }
        if (atom == TH_TAG_NOBR) {
            reconstruct_afe(tree);
            if (has_in_scope(tree, TH_TAG_NOBR)) {
                adoption_agency(tree, TH_TAG_NOBR);
                reconstruct_afe(tree);
            }
            th_node *node = insert_element(tree, tok);
            if (node != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
                stack_push(tree, node);
                afe_push(tree, node);
            }
            return TH_DRAIN_NEXT;
        }
        if (flags & TH_TAG_FORMATTING) {
            reconstruct_afe(tree);
            th_node *node = insert_element(tree, tok);
            if (node != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
                stack_push(tree, node);
                afe_push(tree, node);
            }
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_APPLET || atom == TH_TAG_MARQUEE || atom == TH_TAG_OBJECT) {
            reconstruct_afe(tree);
            th_node *node = insert_element(tree, tok);
            if (node != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
                stack_push(tree, node);
            }
            afe_push_marker(tree);
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_TEMPLATE) {
            th_node *node = insert_template(tree, tok);
            if (node != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
                stack_push(tree, node);
                afe_push_marker(tree);
            }
            tmpl_push(tree, M_IN_TEMPLATE);
            dc->mode = M_IN_TEMPLATE;
            return TH_DRAIN_NEXT;
        }
        reconstruct_afe(tree);
        th_node *node = insert_element(tree, tok);
        if (is_void_atom(atom)) {
            return TH_DRAIN_NEXT; /* void: inserted, not pushed (a stray "/" on any
                      other element is ignored in HTML content) */
        }
        if (node != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
            stack_push(tree, node);
        }
        return TH_DRAIN_NEXT;
    }
    /* only an end tag reaches here: text/comment/start break above, a DOCTYPE is ignored before the switch */
    if (tok->kind == TH_END_TAG) { /* GCOVR_EXCL_BR_LINE: the non-end-tag branch is unreachable */
        uint8_t flags = tok->tag_flags;
        uint16_t atom = tok->atom;
        if (atom == TH_TAG_BODY || atom == TH_TAG_HTML) {
            dc->mode = M_AFTER_BODY;
            if (atom == TH_TAG_HTML) {
                return TH_DRAIN_REPROCESS;
            }
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_P) {
            if (!has_in_button_scope(tree, TH_TAG_P)) {
                insert_implicit(tree, "p", TH_TAG_P, TH_TAG_SPECIAL); /* empty p, immediately closed */
            } else {
                generate_implied_end_tags(tree, TH_TAG_P);
                pop_until_atom(tree, TH_TAG_P);
            }
            return TH_DRAIN_NEXT;
        }
        if (is_heading_atom(atom)) {
            int in_scope = 0;
            /* html bounds the stack walk */
            for (Py_ssize_t index = tree->open_len - 1; index >= 0; index--) { /* GCOVR_EXCL_BR_LINE */
                if (is_heading_atom(tree->open[index]->atom)) {
                    in_scope = 1;
                    break;
                }
                if (tree->open[index]->tag_flags & TH_TAG_SCOPING) {
                    break;
                }
            }
            if (in_scope) {
                generate_implied_end_tags(tree, TH_TAG_UNKNOWN);
                while (tree->open_len > 0) { /* GCOVR_EXCL_BR_LINE: the heading is in scope when this runs, so
                                                the stack never empties */
                    th_node *node = current_node(tree);
                    stack_pop(tree);
                    if (is_heading_atom(node->atom)) {
                        break;
                    }
                }
            }
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_LI || atom == TH_TAG_DD || atom == TH_TAG_DT) {
            if (atom == TH_TAG_LI ? has_in_list_item_scope(tree, atom) : has_in_scope(tree, atom)) {
                generate_implied_end_tags(tree, atom);
                pop_until_atom(tree, atom);
            }
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_FORM) {
            if (stack_index_of_atom(tree, TH_TAG_TEMPLATE) < 0) {
                th_node *node = tree->form;
                tree->form = NULL;
                int in_scope = 0;
                /* the html element is a scope boundary, so the walk always breaks before the stack bottom */
                for (Py_ssize_t index = tree->open_len - 1; node != NULL && index >= 0 /* GCOVR_EXCL_BR_LINE */;
                     index--) {
                    if (tree->open[index] == node) {
                        in_scope = 1;
                        break;
                    }
                    if (is_scope_boundary(tree->open[index])) {
                        break;
                    }
                }
                if (!in_scope) {
                    return TH_DRAIN_NEXT; /* parse error, ignored */
                }
                generate_implied_end_tags(tree, TH_TAG_UNKNOWN);
                /* the form is removed from wherever it sits on the stack */
                /* the form element is on the stack when this runs, so it */
                for (Py_ssize_t index = tree->open_len - 1; index >= 0; index--) { /* GCOVR_EXCL_BR_LINE */
                    if (tree->open[index] == node) {
                        stack_remove_at(tree, index);
                        break;
                    }
                }
                /* a template on the stack always has a form in scope when this end tag runs */
            } else if (has_in_scope(tree, TH_TAG_FORM) /* GCOVR_EXCL_BR_LINE */) {
                generate_implied_end_tags(tree, TH_TAG_UNKNOWN);
                pop_until_atom(tree, TH_TAG_FORM);
            }
            return TH_DRAIN_NEXT;
        }
        if (is_block_end_atom(atom)) {
            if (has_in_scope(tree, atom)) {
                generate_implied_end_tags(tree, TH_TAG_UNKNOWN);
                pop_until_atom(tree, atom);
            }
            return TH_DRAIN_NEXT;
        }
        if (flags & TH_TAG_FORMATTING) {
            adoption_agency(tree, atom);
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_APPLET || atom == TH_TAG_MARQUEE || atom == TH_TAG_OBJECT) {
            if (has_in_scope(tree, atom)) {
                generate_implied_end_tags(tree, TH_TAG_UNKNOWN);
                pop_until_atom(tree, atom);
                afe_clear_to_marker(tree);
            }
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_BR) {
            /* </br> acts as a <br> start tag (attributes dropped) */
            reconstruct_afe(tree);
            insert_implicit(tree, "br", TH_TAG_BR, TH_TAG_SPECIAL);
            tree->frameset_ok = 0;
            return TH_DRAIN_NEXT;
        }
        any_other_end_tag(tree, atom, tok);
        return TH_DRAIN_NEXT;
    }
    return TH_DRAIN_NEXT; /* GCOVR_EXCL_LINE: in body handles every text/comment/start/end token in a branch
              that breaks, and a DOCTYPE is ignored before the switch, so nothing reaches here */
}

static enum th_drain drain_text(th_tree *tree, th_token *tok, th_insert *dc) {
    if (tok->kind == TH_TEXT) {
        /* a nul anywhere in the input disables span sharing */
        if (tok->is_slice && tree->can_span && !tree->has_nul /* GCOVR_EXCL_BR_LINE */) {
            Py_ssize_t off = tok->src_start + tree->text_offset;
            Py_ssize_t len = tok->src_len - tree->text_offset;
            /* a text token always has a positive length */
            if (tree->drop_newline && len > 0 /* GCOVR_EXCL_BR_LINE */ && input_read(tree, off) == '\n') {
                off++;
                len--;
            }
            tree->drop_newline = 0;
            insert_text_span(tree, off, len);
            return TH_DRAIN_NEXT;
        }
        Py_ssize_t len;
        Py_UCS4 *text = token_text(tree, tok, &len);
        /* a text token always has a positive length */
        if (tree->drop_newline && len > 0 /* GCOVR_EXCL_BR_LINE */ && text[0] == '\n') {
            text++;
            len--;
        }
        tree->drop_newline = 0;
        insert_text(tree, text, len);
        return TH_DRAIN_NEXT;
    }
    /* end tag (or EOF) closes the raw-text/RCDATA element */
    stack_pop(tree);
    dc->mode = dc->original_mode;
    return TH_DRAIN_NEXT;
}

static enum th_drain drain_in_table(th_tree *tree, th_token *tok, th_insert *dc) {
    if (tok->kind == TH_TEXT) {
        Py_ssize_t len;
        Py_UCS4 *text = token_text(tree, tok, &len);
        if (all_whitespace_or_nul(text, len)) {
            insert_text(tree, text, len);
        } else {
            /* non-space character: foster-parent it out of the table */
            tree->foster = 1;
            reconstruct_afe(tree);
            insert_text(tree, text, len);
            tree->foster = 0;
        }
        dc->mode = dc->table_origin;
        return TH_DRAIN_NEXT;
    }
    if (tok->kind == TH_COMMENT) {
        insert_comment(tree, tok, NULL);
        dc->mode = dc->table_origin;
        return TH_DRAIN_NEXT;
    }
    if (tok->kind == TH_START_TAG) {
        uint16_t atom = tok->atom;
        if (atom == TH_TAG_CAPTION) {
            clear_to(tree, TH_TAG_TABLE, TH_TAG_TABLE, TH_TAG_TABLE);
            afe_push_marker(tree);
            th_node *node = insert_element(tree, tok);
            if (node != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
                stack_push(tree, node);
            }
            dc->mode = M_IN_CAPTION;
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_COLGROUP) {
            clear_to(tree, TH_TAG_TABLE, TH_TAG_TABLE, TH_TAG_TABLE);
            th_node *node = insert_element(tree, tok);
            if (node != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
                stack_push(tree, node);
            }
            dc->mode = M_IN_COLUMN_GROUP;
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_COL) {
            clear_to(tree, TH_TAG_TABLE, TH_TAG_TABLE, TH_TAG_TABLE);
            {
                th_node *cg = insert_implicit(tree, "colgroup", TH_TAG_COLGROUP, TH_TAG_SPECIAL);
                if (cg != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
                    stack_push(tree, cg);
                }
            }
            dc->mode = M_IN_COLUMN_GROUP;
            return TH_DRAIN_REPROCESS;
        }
        if (atom == TH_TAG_TBODY || atom == TH_TAG_THEAD || atom == TH_TAG_TFOOT) {
            clear_to(tree, TH_TAG_TABLE, TH_TAG_TABLE, TH_TAG_TABLE);
            th_node *node = insert_element(tree, tok);
            if (node != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
                stack_push(tree, node);
            }
            dc->mode = M_IN_TABLE_BODY;
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_TD || atom == TH_TAG_TH || atom == TH_TAG_TR) {
            clear_to(tree, TH_TAG_TABLE, TH_TAG_TABLE, TH_TAG_TABLE);
            {
                th_node *tb = insert_implicit(tree, "tbody", TH_TAG_TBODY, TH_TAG_SPECIAL);
                if (tb != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
                    stack_push(tree, tb);
                }
            }
            dc->mode = M_IN_TABLE_BODY;
            return TH_DRAIN_REPROCESS;
        }
        if (atom == TH_TAG_TABLE) {
            /* a nested table closes the current one, then reprocesses */
            if (has_in_table_scope(tree, TH_TAG_TABLE)) {
                pop_until_atom(tree, TH_TAG_TABLE);
                dc->mode = reset_insertion_mode(tree);
                return TH_DRAIN_REPROCESS;
            }
            dc->mode = dc->table_origin;
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_STYLE || atom == TH_TAG_SCRIPT) {
            uint8_t f2 = tok->tag_flags;
            uint16_t a2 = tok->atom;
            int model = content_model_for(a2, f2, tree->scripting);
            th_node *node = insert_element(tree, tok);
            if (node != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
                stack_push(tree, node);
            }
            th_tok_switch(dc->sm, (enum th_initial_state)model);
            dc->original_mode = dc->table_origin;
            dc->mode = M_TEXT;
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_INPUT && input_is_hidden(tok)) {
            insert_element(tree, tok); /* a hidden input stays in the table, no fostering */
            dc->mode = dc->table_origin;
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_FORM) {
            /* a form in a table is inserted in place and closed at once,
               unless one is already open or a template is on the stack */
            if (tree->form == NULL && stack_index_of_atom(tree, TH_TAG_TEMPLATE) < 0) {
                tree->form = insert_element(tree, tok);
            }
            dc->mode = dc->table_origin;
            return TH_DRAIN_NEXT;
        }
        /* anything else: foster-parent this one token under in-body rules */
        tree->foster = 1;
        dc->foster_pending = 1;
        dc->foster_return = dc->table_origin;
        dc->mode = M_IN_BODY;
        return TH_DRAIN_REPROCESS;
    }
    /* only an end tag reaches here: text/comment/start break or foster above, a DOCTYPE is ignored before it */
    if (tok->kind == TH_END_TAG) { /* GCOVR_EXCL_BR_LINE: the non-end-tag branch is unreachable */
        uint16_t atom = tok_atom(tok);
        if (atom == TH_TAG_TABLE) {
            if (has_in_table_scope(tree, TH_TAG_TABLE)) {
                pop_until_atom(tree, TH_TAG_TABLE);
                dc->mode = reset_insertion_mode(tree);
            } else {
                dc->mode = dc->table_origin;
            }
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_BODY || atom == TH_TAG_HTML || atom == TH_TAG_TBODY || atom == TH_TAG_TFOOT ||
            atom == TH_TAG_THEAD || atom == TH_TAG_TR || atom == TH_TAG_TD || atom == TH_TAG_TH ||
            atom == TH_TAG_CAPTION || atom == TH_TAG_COL || atom == TH_TAG_COLGROUP) {
            dc->mode = dc->table_origin;
            return TH_DRAIN_NEXT; /* ignore stray table-related end tags */
        }
        tree->foster = 1;
        dc->foster_pending = 1;
        dc->foster_return = dc->table_origin;
        dc->mode = M_IN_BODY;
        return TH_DRAIN_REPROCESS;
    }
    return TH_DRAIN_NEXT; /* GCOVR_EXCL_LINE: in table handles every text/comment/start/end token in a branch
              that breaks, and a DOCTYPE is ignored before the switch, so nothing reaches here */
}

static enum th_drain drain_in_caption(th_tree *tree, th_token *tok, th_insert *dc) {
    uint16_t atom = tok_atom(tok);
    if (tok->kind == TH_END_TAG && atom == TH_TAG_CAPTION) {
        if (has_in_table_scope(tree, TH_TAG_CAPTION)) {
            generate_implied_end_tags(tree, TH_TAG_UNKNOWN);
            pop_until_atom(tree, TH_TAG_CAPTION);
            afe_clear_to_marker(tree);
            dc->mode = M_IN_TABLE;
        }
        return TH_DRAIN_NEXT;
    }
    if ((tok->kind == TH_START_TAG && (atom == TH_TAG_CAPTION || atom == TH_TAG_COL || atom == TH_TAG_COLGROUP ||
                                       atom == TH_TAG_TBODY || atom == TH_TAG_TD || atom == TH_TAG_TFOOT ||
                                       atom == TH_TAG_TH || atom == TH_TAG_THEAD || atom == TH_TAG_TR)) ||
        (tok->kind == TH_END_TAG && atom == TH_TAG_TABLE)) {
        if (has_in_table_scope(tree, TH_TAG_CAPTION)) {
            generate_implied_end_tags(tree, TH_TAG_UNKNOWN);
            pop_until_atom(tree, TH_TAG_CAPTION);
            afe_clear_to_marker(tree);
            dc->mode = M_IN_TABLE;
            return TH_DRAIN_REPROCESS;
        }
        return TH_DRAIN_NEXT;
    }
    dc->foster_pending = 1;
    dc->foster_return = M_IN_CAPTION;
    dc->mode = M_IN_BODY;
    return TH_DRAIN_REPROCESS;
}

static enum th_drain drain_in_column_group(th_tree *tree, th_token *tok, th_insert *dc) {
    if (tok->kind == TH_TEXT) {
        Py_ssize_t len;
        Py_UCS4 *text = token_text(tree, tok, &len);
        Py_ssize_t ws = 0;
        while (ws < len && (is_space(text[ws]))) {
            ws++;
        }
        if (ws > 0) {
            insert_text(tree, text, ws); /* the whitespace prefix stays in the colgroup */
        }
        if (ws == len) {
            return TH_DRAIN_NEXT;
        }
        tree->text_offset += ws; /* reprocess only the remainder below */
    }
    if (tok->kind == TH_COMMENT) {
        insert_comment(tree, tok, NULL);
        return TH_DRAIN_NEXT;
    }
    if (tok->kind == TH_START_TAG && tok_atom(tok) == TH_TAG_HTML) {
        /* a stray html start tag uses the in-body rules: merge attributes onto
           the open html and leave the stack (so the colgroup stays open) */
        if (tree->open_len > 0 && tree->open[0]->atom == TH_TAG_HTML && /* GCOVR_EXCL_BR_LINE */
            stack_index_of_atom(tree, TH_TAG_TEMPLATE) < 0) {
            merge_attrs(tree, tree->open[0], tok);
        }
        return TH_DRAIN_NEXT;
    }
    if (tok->kind == TH_START_TAG && tok_atom(tok) == TH_TAG_COL) {
        insert_element(tree, tok); /* col is void */
        return TH_DRAIN_NEXT;
    }
    if (tok->kind == TH_END_TAG && tok_atom(tok) == TH_TAG_COLGROUP) {
        if (current_node(tree)->atom == TH_TAG_COLGROUP) {
            stack_pop(tree);
            dc->mode = M_IN_TABLE;
        }
        return TH_DRAIN_NEXT;
    }
    /* anything else: only a real colgroup can be popped to fall back to
       in-table; otherwise (fragment/template root) the token is ignored */
    if (current_node(tree)->atom != TH_TAG_COLGROUP) {
        return TH_DRAIN_NEXT;
    }
    stack_pop(tree);
    dc->mode = M_IN_TABLE;
    return TH_DRAIN_REPROCESS;
}

static enum th_drain drain_in_table_body(th_tree *tree, th_token *tok, th_insert *dc) {
    if (tok->kind == TH_START_TAG) {
        uint16_t atom = tok_atom(tok);
        if (atom == TH_TAG_TR) {
            clear_to(tree, TH_TAG_TBODY, TH_TAG_TFOOT, TH_TAG_THEAD);
            th_node *node = insert_element(tree, tok);
            if (node != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
                stack_push(tree, node);
            }
            dc->mode = M_IN_ROW;
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_TD || atom == TH_TAG_TH) {
            clear_to(tree, TH_TAG_TBODY, TH_TAG_TFOOT, TH_TAG_THEAD);
            {
                th_node *row = insert_implicit(tree, "tr", TH_TAG_TR, TH_TAG_SPECIAL);
                if (row != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
                    stack_push(tree, row);
                }
            }
            dc->mode = M_IN_ROW;
            return TH_DRAIN_REPROCESS;
        }
        if (atom == TH_TAG_CAPTION || atom == TH_TAG_COL || atom == TH_TAG_COLGROUP || atom == TH_TAG_TBODY ||
            atom == TH_TAG_TFOOT || atom == TH_TAG_THEAD) {
            if (has_in_table_scope(tree, TH_TAG_TBODY) || has_in_table_scope(tree, TH_TAG_THEAD) ||
                has_in_table_scope(tree, TH_TAG_TFOOT)) {
                clear_to(tree, TH_TAG_TBODY, TH_TAG_TFOOT, TH_TAG_THEAD);
                stack_pop(tree);
                dc->mode = M_IN_TABLE;
                return TH_DRAIN_REPROCESS;
            }
            return TH_DRAIN_NEXT;
        }
    }
    if (tok->kind == TH_END_TAG) {
        uint16_t atom = tok_atom(tok);
        if (atom == TH_TAG_TBODY || atom == TH_TAG_THEAD || atom == TH_TAG_TFOOT) {
            if (has_in_table_scope(tree, atom)) {
                clear_to(tree, TH_TAG_TBODY, TH_TAG_TFOOT, TH_TAG_THEAD);
                stack_pop(tree);
                dc->mode = M_IN_TABLE;
            }
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_TABLE) {
            if (has_in_table_scope(tree, TH_TAG_TBODY) || has_in_table_scope(tree, TH_TAG_THEAD) ||
                has_in_table_scope(tree, TH_TAG_TFOOT)) {
                clear_to(tree, TH_TAG_TBODY, TH_TAG_TFOOT, TH_TAG_THEAD);
                stack_pop(tree);
                dc->mode = M_IN_TABLE;
                return TH_DRAIN_REPROCESS;
            }
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_BODY || atom == TH_TAG_CAPTION || atom == TH_TAG_COL || atom == TH_TAG_COLGROUP ||
            atom == TH_TAG_HTML || atom == TH_TAG_TD || atom == TH_TAG_TH || atom == TH_TAG_TR) {
            return TH_DRAIN_NEXT; /* ignore */
        }
    }
    dc->table_origin = dc->mode;
    dc->mode = M_IN_TABLE;
    return TH_DRAIN_REPROCESS;
}

static enum th_drain drain_in_row(th_tree *tree, th_token *tok, th_insert *dc) {
    if (tok->kind == TH_START_TAG) {
        uint16_t atom = tok_atom(tok);
        if (atom == TH_TAG_TD || atom == TH_TAG_TH) {
            clear_to(tree, TH_TAG_TR, TH_TAG_TR, TH_TAG_TR);
            th_node *node = insert_element(tree, tok);
            if (node != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
                stack_push(tree, node);
            }
            afe_push_marker(tree);
            dc->mode = M_IN_CELL;
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_CAPTION || atom == TH_TAG_COL || atom == TH_TAG_COLGROUP || atom == TH_TAG_TBODY ||
            atom == TH_TAG_TFOOT || atom == TH_TAG_THEAD || atom == TH_TAG_TR) {
            if (has_in_table_scope(tree, TH_TAG_TR)) {
                clear_to(tree, TH_TAG_TR, TH_TAG_TR, TH_TAG_TR);
                stack_pop(tree);
                dc->mode = M_IN_TABLE_BODY;
                return TH_DRAIN_REPROCESS;
            }
            return TH_DRAIN_NEXT;
        }
    }
    if (tok->kind == TH_END_TAG) {
        uint16_t atom = tok_atom(tok);
        if (atom == TH_TAG_TR) {
            if (has_in_table_scope(tree, TH_TAG_TR)) {
                clear_to(tree, TH_TAG_TR, TH_TAG_TR, TH_TAG_TR);
                stack_pop(tree);
                dc->mode = M_IN_TABLE_BODY;
            }
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_TABLE) {
            if (has_in_table_scope(tree, TH_TAG_TR)) {
                clear_to(tree, TH_TAG_TR, TH_TAG_TR, TH_TAG_TR);
                stack_pop(tree);
                dc->mode = M_IN_TABLE_BODY;
                return TH_DRAIN_REPROCESS;
            }
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_TBODY || atom == TH_TAG_THEAD || atom == TH_TAG_TFOOT) {
            if (has_in_table_scope(tree, atom)) {
                clear_to(tree, TH_TAG_TR, TH_TAG_TR, TH_TAG_TR);
                stack_pop(tree);
                dc->mode = M_IN_TABLE_BODY;
                return TH_DRAIN_REPROCESS;
            }
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_BODY || atom == TH_TAG_CAPTION || atom == TH_TAG_COL || atom == TH_TAG_COLGROUP ||
            atom == TH_TAG_HTML || atom == TH_TAG_TD || atom == TH_TAG_TH) {
            return TH_DRAIN_NEXT; /* ignore */
        }
    }
    dc->table_origin = dc->mode;
    dc->mode = M_IN_TABLE;
    return TH_DRAIN_REPROCESS;
}

static enum th_drain drain_in_cell(th_tree *tree, th_token *tok, th_insert *dc) {
    if (tok->kind == TH_END_TAG) {
        uint16_t atom = tok_atom(tok);
        if (atom == TH_TAG_TD || atom == TH_TAG_TH) {
            if (has_in_table_scope(tree, atom)) {
                generate_implied_end_tags(tree, TH_TAG_UNKNOWN);
                pop_until_atom(tree, atom);
                afe_clear_to_marker(tree);
                dc->mode = M_IN_ROW;
            }
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_TABLE || atom == TH_TAG_TBODY || atom == TH_TAG_TFOOT || atom == TH_TAG_THEAD ||
            atom == TH_TAG_TR) {
            if (has_in_table_scope(tree, atom)) {
                uint16_t close = has_in_table_scope(tree, TH_TAG_TD) ? TH_TAG_TD : TH_TAG_TH;
                generate_implied_end_tags(tree, TH_TAG_UNKNOWN);
                pop_until_atom(tree, close);
                afe_clear_to_marker(tree);
                dc->mode = M_IN_ROW;
                return TH_DRAIN_REPROCESS;
            }
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_BODY || atom == TH_TAG_CAPTION || atom == TH_TAG_COL || atom == TH_TAG_COLGROUP ||
            atom == TH_TAG_HTML) {
            return TH_DRAIN_NEXT; /* ignore */
        }
    }
    if (tok->kind == TH_START_TAG) {
        uint16_t atom = tok_atom(tok);
        if (atom == TH_TAG_CAPTION || atom == TH_TAG_COL || atom == TH_TAG_COLGROUP || atom == TH_TAG_TBODY ||
            atom == TH_TAG_TD || atom == TH_TAG_TFOOT || atom == TH_TAG_TH || atom == TH_TAG_THEAD ||
            atom == TH_TAG_TR) {
            uint16_t close = has_in_table_scope(tree, TH_TAG_TD) ? TH_TAG_TD : TH_TAG_TH;
            if (has_in_table_scope(tree, close)) {
                generate_implied_end_tags(tree, TH_TAG_UNKNOWN);
                pop_until_atom(tree, close);
                afe_clear_to_marker(tree);
                dc->mode = M_IN_ROW;
                return TH_DRAIN_REPROCESS;
            }
            return TH_DRAIN_NEXT;
        }
    }
    dc->foster_pending = 1;
    dc->foster_return = M_IN_CELL;
    dc->mode = M_IN_BODY;
    return TH_DRAIN_REPROCESS;
}

static enum th_drain drain_after_body(th_tree *tree, th_token *tok, th_insert *dc) {
    if (tok->kind == TH_COMMENT) {
        /* open[0] is always the html element in this mode, never the document */
        th_node *root = tree->open_len > 0 ? tree->open[0] : tree->document; /* GCOVR_EXCL_BR_LINE */
        insert_comment(tree, tok, root);
        return TH_DRAIN_NEXT;
    }
    if (tok->kind == TH_TEXT) {
        Py_ssize_t len;
        Py_UCS4 *text = token_text(tree, tok, &len);
        if (all_whitespace(text, len)) {
            /* whitespace runs the in-body rules without leaving after-body */
            dc->foster_pending = 1;
            dc->foster_return = M_AFTER_BODY;
            dc->mode = M_IN_BODY;
            return TH_DRAIN_REPROCESS;
        }
    }
    if (tok->kind == TH_END_TAG && tok_atom(tok) == TH_TAG_HTML) {
        dc->mode = M_AFTER_AFTER_BODY;
        return TH_DRAIN_NEXT;
    }
    dc->mode = M_IN_BODY;
    return TH_DRAIN_REPROCESS;
}

static enum th_drain drain_after_after_body(th_tree *tree, th_token *tok, th_insert *dc) {
    if (tok->kind == TH_COMMENT) {
        /* a fragment has no document node to attach to; use its root */
        insert_comment(tree, tok, tree->fragment_root != NULL ? tree->fragment_root : tree->document);
        return TH_DRAIN_NEXT;
    }
    if (tok->kind == TH_TEXT) {
        Py_ssize_t len;
        Py_UCS4 *text = token_text(tree, tok, &len);
        if (all_whitespace(text, len)) {
            /* whitespace runs the in-body rules without leaving this mode */
            dc->foster_pending = 1;
            dc->foster_return = M_AFTER_AFTER_BODY;
            dc->mode = M_IN_BODY;
            return TH_DRAIN_REPROCESS;
        }
    }
    dc->mode = M_IN_BODY;
    return TH_DRAIN_REPROCESS;
}

static enum th_drain drain_after_after_frameset(th_tree *tree, th_token *tok, th_insert *dc) {
    if (tok->kind == TH_COMMENT) {
        insert_comment(tree, tok, tree->document);
        return TH_DRAIN_NEXT;
    }
    if (tok->kind == TH_TEXT) {
        Py_ssize_t len;
        Py_UCS4 *text = token_text(tree, tok, &len);
        insert_whitespace_only(tree, text, len);
        return TH_DRAIN_NEXT;
    }
    if (tok->kind == TH_START_TAG) {
        uint16_t atom = tok_atom(tok);
        if (atom == TH_TAG_HTML) {
            /* in-body rules: merge attributes into the existing html */
            /* open stack always holds html at index 0 */
            if (tree->open_len > 0 && tree->open[0]->atom == TH_TAG_HTML) { /* GCOVR_EXCL_BR_LINE */
                merge_attrs(tree, tree->open[0], tok);
            }
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_NOFRAMES) {
            th_node *node = insert_element(tree, tok);
            if (node != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
                stack_push(tree, node);
            }
            th_tok_switch(dc->sm, TH_INIT_RAWTEXT);
            dc->original_mode = M_AFTER_AFTER_FRAMESET;
            dc->mode = M_TEXT;
        }
    }
    return TH_DRAIN_NEXT; /* everything else is a parse error and ignored */
}

/* Drive tree construction over the tokens the tokenizer can produce now,
   returning when it stalls (TH_STEP_NEED_MORE), reaches EOF (TH_STEP_DONE), or
   fails. The insertion-mode state is restored from run_state on entry and saved
   back on exit so a later call resumes the WHATWG algorithm in place. */
static void run_drain(th_tree *tree, th_tokenizer *sm, th_run_state *run_state) {
    /* foster_return: a table mode foster-parents a stray token by processing it
       once under in-body rules and then returning to the table mode. table_origin:
       the row/section modes delegate unhandled tokens to the in-table rules without
       changing the insertion mode, so the delegating mode is remembered here. */
    th_insert local = {
        .sm = sm,
        .mode = run_state->mode,
        .original_mode = run_state->original_mode,
        .foster_return = run_state->foster_return,
        .foster_pending = run_state->foster_pending,
        .table_origin = M_IN_TABLE,
    };
    th_insert *dc = &local;
    th_token *tok;

    /* the <![CDATA[ decision depends on the adjusted current node, so the
       tokenizer is told before every pull whether it is foreign */
    /* tree->failed is only set on allocation failure */
    while (!tree->failed /* GCOVR_EXCL_BR_LINE */ &&
           (th_tok_set_cdata(sm, tree->open_len > 0 && current_node(tree)->ns != TH_NS_HTML),
            th_tok_next(sm, &tok) == TH_STEP_TOKEN)) {
        if (dc->foster_pending) {
            /* The one fostered token was processed under in-body rules; restore
               the table/cell mode only if those rules did not themselves switch
               the insertion mode (a fostered <select>/<table> legitimately
               does, and that switch must survive). */
            if (dc->mode == M_IN_BODY) {
                dc->mode = dc->foster_return;
            }
            tree->foster = 0;
            dc->foster_pending = 0;
        }
        dc->table_origin = M_IN_TABLE;
        tree->text_offset = 0; /* a fresh token: any consumed-prefix offset is stale */
        /* the pre/listing/textarea leading-LF skip applies only to the token
           immediately following the start tag: a text token consumes it in its
           own branch, so any other intervening token (a comment or element) must
           clear the flag here or a later newline would be dropped too */
        if (tree->drop_newline && tok->kind != TH_TEXT) {
            tree->drop_newline = 0;
        }
    reprocess:;
        /* Intern the tag name once; every comparison and insert below reads
           tok->atom / tok->tag_flags instead of re-interning the same name. */
        if (tok->kind == TH_START_TAG || tok->kind == TH_END_TAG) {
            tok->atom = intern_atom(&tok->name, &tok->tag_flags);
        } else {
            tok->atom = TH_TAG_UNKNOWN;
            tok->tag_flags = 0;
        }
        /* the element(s) this end tag pops are the ones the source closed explicitly */
        tree->closing_end_tag = tok->kind == TH_END_TAG ? tok : NULL;
        if (use_foreign_rules(tree, tok) && foreign_step(tree, tok)) {
            continue; /* handled under foreign-content rules */
        }
        /* template tags are handled uniformly across the table/select modes (the
           spec routes them through in-head); in-body/in-head/in-template keep
           their own handlers for the active-formatting interactions */
        if (tok->kind == TH_END_TAG && dc->mode >= M_IN_HEAD && tok_atom(tok) == TH_TAG_TEMPLATE) {
            if (stack_index_of_atom(tree, TH_TAG_TEMPLATE) >= 0) {
                generate_implied_end_tags(tree, TH_TAG_UNKNOWN);
                pop_until_atom(tree, TH_TAG_TEMPLATE);
                afe_clear_to_marker(tree);
                tmpl_pop(tree);
                dc->mode = reset_insertion_mode(tree);
            }
            continue;
        }
        if (tok->kind == TH_START_TAG &&
            (dc->mode == M_IN_TABLE || dc->mode == M_IN_TABLE_BODY || dc->mode == M_IN_ROW || dc->mode == M_IN_CELL ||
             dc->mode == M_IN_COLUMN_GROUP || dc->mode == M_IN_CAPTION) &&
            tok_atom(tok) == TH_TAG_TEMPLATE) {
            th_node *node = insert_template(tree, tok);
            if (node != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
                stack_push(tree, node);
                afe_push_marker(tree);
            }
            tmpl_push(tree, M_IN_TEMPLATE);
            dc->mode = M_IN_TEMPLATE;
            continue;
        }
        /* "in select in table": a select opened in a table context does not get its own
           mode here, so a table-family start tag would nest inside the still-open select.
           Per the spec it pops the select, resets the insertion mode, and reprocesses, so
           the table element becomes a sibling of the closed select (#90). */
        if (tok->kind == TH_START_TAG && has_in_scope(tree, TH_TAG_SELECT) &&
            (dc->mode == M_IN_TABLE || dc->mode == M_IN_TABLE_BODY || dc->mode == M_IN_ROW || dc->mode == M_IN_CELL ||
             dc->mode == M_IN_CAPTION)) {
            uint16_t atom = tok_atom(tok);
            if (atom == TH_TAG_CAPTION || atom == TH_TAG_TABLE || atom == TH_TAG_TBODY || atom == TH_TAG_TFOOT ||
                atom == TH_TAG_THEAD || atom == TH_TAG_TR || atom == TH_TAG_TD || atom == TH_TAG_TH) {
                pop_until_atom(tree, TH_TAG_SELECT);
                dc->mode = reset_insertion_mode(tree);
                goto reprocess;
            }
        }
        /* a DOCTYPE in any insertion mode other than "initial" is a parse error that is
           ignored without changing the insertion mode (foreign content already handled
           its own DOCTYPE above); only the initial mode builds the doctype node */
        if (tok->kind == TH_DOCTYPE && dc->mode != M_INITIAL) {
            th_error_sink_push(&tree->errors, "unexpected-doctype", tok->line, tok->col);
            continue;
        }
        /* every insertion mode has an explicit case, so the compiler default arm is unreachable */
        enum th_drain action = TH_DRAIN_NEXT;
        switch (dc->mode) /* GCOVR_EXCL_BR_LINE */ {
        case M_INITIAL:
            action = drain_initial(tree, tok, dc);
            break;
        case M_BEFORE_HTML:
            action = drain_before_html(tree, tok, dc);
            break;
        case M_BEFORE_HEAD:
            action = drain_before_head(tree, tok, dc);
            break;
        case M_IN_HEAD:
            action = drain_in_head(tree, tok, dc);
            break;
        case M_IN_HEAD_NOSCRIPT:
            action = drain_in_head_noscript(tree, tok, dc);
            break;
        case M_AFTER_HEAD:
            action = drain_after_head(tree, tok, dc);
            break;
        case M_IN_TEMPLATE:
            action = drain_in_template(tree, tok, dc);
            break;
        case M_IN_FRAMESET:
            action = drain_in_frameset(tree, tok, dc);
            break;
        case M_AFTER_FRAMESET:
            action = drain_after_frameset(tree, tok, dc);
            break;
        case M_IN_BODY:
            action = drain_in_body(tree, tok, dc);
            break;
        case M_TEXT:
            action = drain_text(tree, tok, dc);
            break;
        case M_IN_TABLE:
            action = drain_in_table(tree, tok, dc);
            break;
        case M_IN_CAPTION:
            action = drain_in_caption(tree, tok, dc);
            break;
        case M_IN_COLUMN_GROUP:
            action = drain_in_column_group(tree, tok, dc);
            break;
        case M_IN_TABLE_BODY:
            action = drain_in_table_body(tree, tok, dc);
            break;
        case M_IN_ROW:
            action = drain_in_row(tree, tok, dc);
            break;
        case M_IN_CELL:
            action = drain_in_cell(tree, tok, dc);
            break;
        case M_AFTER_BODY:
            action = drain_after_body(tree, tok, dc);
            break;
        case M_AFTER_AFTER_BODY:
            action = drain_after_after_body(tree, tok, dc);
            break;
        case M_AFTER_AFTER_FRAMESET:
            action = drain_after_after_frameset(tree, tok, dc);
            break;
        }
        if (action == TH_DRAIN_REPROCESS) {
            goto reprocess;
        }
    }
    run_state->mode = dc->mode;
    run_state->original_mode = dc->original_mode;
    run_state->foster_return = dc->foster_return;
    run_state->foster_pending = dc->foster_pending;
}

/* Stop parsing at EOF: pop the remaining open elements, which runs the
   option-into-selectedcontent clone for a still-open selected option. */
static void run_close(th_tree *tree) {
    while (tree->open_len > 0) {
        stack_pop(tree);
    }
}

/* Feed the input to the tokenizer (borrowing when there is no CR to normalize)
   and point tree->data at the tokenizer's authoritative input base. */
static void setup_input(th_tree *tree, th_tokenizer *sm, int kind, const void *data, Py_ssize_t length) {
    /* hoist the width check out of the per-character loop: a 1-byte buffer uses
       libc's vectorized memchr, the wide buffers a tight typed scan */
    int has_cr = 0;
    if (kind == PyUnicode_1BYTE_KIND) {
        has_cr = memchr(data, '\r', (size_t)length) != NULL;
        tree->has_nul = memchr(data, '\0', (size_t)length) != NULL;
    } else if (kind == PyUnicode_2BYTE_KIND) {
        const uint16_t *units = (const uint16_t *)data;
        for (Py_ssize_t index = 0; index < length; index++) {
            has_cr |= units[index] == '\r';
            tree->has_nul |= units[index] == 0;
        }
    } else {
        const uint32_t *units = (const uint32_t *)data;
        for (Py_ssize_t index = 0; index < length; index++) {
            has_cr |= units[index] == '\r';
            tree->has_nul |= units[index] == 0;
        }
    }
    if (has_cr) {
        th_tok_feed(sm, kind, data, length);
    } else {
        /* borrowed input is not copied and outlives the tree (the caller holds
           the string for the whole parse), so text nodes can be zero-copy spans */
        th_tok_borrow_input(sm, kind, data, length);
        tree->can_span = 1;
    }
    th_tok_close(sm);
    tree->data = th_tok_input_data(sm, &tree->kind);
}

/* The first element child of parent with this atom, or NULL. */
static th_node *child_with_atom(th_node *parent, uint16_t atom) {
    for (th_node *child = parent->first_child; child != NULL; child = child->next_sibling) {
        if (child->type == TH_NODE_ELEMENT && child->atom == atom) {
            return child;
        }
    }
    return NULL;
}

/* Create a fabricated structural element with the given lowercase name/atom. */
static th_node *make_named(th_tree *tree, const char *name, Py_ssize_t len, uint16_t atom, uint8_t flags) {
    th_node *node = node_new(tree, TH_NODE_ELEMENT);
    if (node == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    node->atom = atom;
    node->tag_flags = flags;
    node->text = arena_alloc(tree, len * (Py_ssize_t)sizeof(Py_UCS4));
    if (node->text == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;          /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        node->text[index] = (Py_UCS4)name[index];
    }
    node->text_len = len;
    return node;
}

/* At EOF the early insertion modes give an html element a head and a body unless a
   frameset took the body's place; synthesize whichever the parse left out. The html
   element is the document's <html> child or, for an html-context fragment, the
   fragment root that stands in for it. */
static void ensure_head_body(th_tree *tree, th_node *html) {
    if (child_with_atom(html, TH_TAG_FRAMESET) != NULL) {
        return; /* a frameset takes the place of the body */
    }
    th_node *head = child_with_atom(html, TH_TAG_HEAD);
    if (head == NULL) {
        head = make_named(tree, "head", 4, TH_TAG_HEAD, TH_TAG_SPECIAL);
        if (head == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return;         /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        }
        /* the implied head is appended, after any comments already in html */
        node_append(html, head);
    }
    if (child_with_atom(html, TH_TAG_BODY) == NULL) {
        th_node *body = make_named(tree, "body", 4, TH_TAG_BODY, TH_TAG_SPECIAL);
        if (body == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return;         /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        }
        node_append(html, body);
    }
}

/* At EOF a document always has html, head and body (the missing-element rules of
   the early insertion modes), so synthesize any the parse left out. */
static void finalize_document(th_tree *tree) {
    th_node *html = child_with_atom(tree->document, TH_TAG_HTML);
    if (html == NULL) {
        html = make_named(tree, "html", 4, TH_TAG_HTML, TH_TAG_SPECIAL | TH_TAG_SCOPING);
        if (html == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return;         /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        }
        node_append(tree->document, html);
    }
    ensure_head_body(tree, html);
}

/* Allocate a fresh document tree ready for token-driven construction (the shared
   start of one-shot and streaming parses). positions enables per-element source
   line/col tracking. NULL on allocation failure. */
static th_tree *tree_new_document(int positions) {
    th_tree *tree = PyMem_Calloc(1, sizeof(th_tree));
    if (tree == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    tree->track_positions = positions; /* set before any element node_new */
    tree->document = node_new(tree, TH_NODE_DOCUMENT);
    if (tree->document == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(tree);       /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        return NULL;              /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    tree->frameset_ok = 1;
    return tree;
}

th_tree *th_tree_parse(int kind, const void *data, Py_ssize_t length, int positions, int scripting) {
    th_tree *tree = tree_new_document(positions);
    if (tree == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    tree->scripting = scripting;
    tree->kind = kind;
    tree->data = data;
    tree->length = length;

    th_tokenizer *sm = th_tok_new();
    if (sm == NULL) {       /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(tree); /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    th_tok_set_error_sink(sm, &tree->errors);
    setup_input(tree, sm, kind, data, length);
    th_run_state run_state;
    run_state_init(&run_state, M_INITIAL);
    run_drain(tree, sm, &run_state);
    run_close(tree);
    th_tok_free(sm);
    finalize_document(tree);

    if (tree->failed) {     /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(tree); /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    return tree;
}

/* The insertion mode a fragment starts in, given its context element's atom. */
static enum mode fragment_mode(uint16_t ctx) {
    switch (ctx) {
    case TH_TAG_TD:
    case TH_TAG_TH:
        return M_IN_CELL;
    case TH_TAG_TR:
        return M_IN_ROW;
    case TH_TAG_TBODY:
    case TH_TAG_THEAD:
    case TH_TAG_TFOOT:
        return M_IN_TABLE_BODY;
    case TH_TAG_CAPTION:
        return M_IN_CAPTION;
    case TH_TAG_COLGROUP:
        return M_IN_COLUMN_GROUP;
    case TH_TAG_TABLE:
        return M_IN_TABLE;
    case TH_TAG_TEMPLATE:
        return M_IN_TEMPLATE;
    case TH_TAG_FRAMESET:
        return M_IN_FRAMESET;
    case TH_TAG_HTML:
        return M_BEFORE_HEAD; /* an html context fragment builds head and body */
    default:
        return M_IN_BODY;
    }
}

/* The case-folding buffer for a fragment context element name; a longer name is
   truncated, which only affects the interned atom (every real context name fits). */
#define MAX_CONTEXT_NAME 32

th_tree *th_tree_parse_fragment(int kind, const void *data, Py_ssize_t length, const char *context,
                                Py_ssize_t context_len, int positions, int scripting) {
    th_tree *tree = PyMem_Calloc(1, sizeof(th_tree));
    if (tree == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    tree->scripting = scripting;
    tree->kind = kind;
    tree->data = data;
    tree->length = length;
    tree->track_positions = positions; /* set before any element node_new */
    tree->document = node_new(tree, TH_NODE_DOCUMENT);
    if (tree->document == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(tree);       /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        return NULL;              /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    tree->frameset_ok = 1;

    /* intern the context element name (ignoring an "svg "/"math " ns prefix) */
    const char *local = context;
    Py_ssize_t local_len = context_len;
    uint8_t ctx_ns = TH_NS_HTML;
    if (context_len > 4 && memcmp(context, "svg ", 4) == 0) {
        local = context + 4;
        local_len = context_len - 4;
        ctx_ns = TH_NS_SVG;
    } else if (context_len > 5 && memcmp(context, "math ", 5) == 0) {
        local = context + 5;
        local_len = context_len - 5;
        ctx_ns = TH_NS_MATHML;
    }
    /* intern on the lowercased name: a context of "svg foreignObject" must
       resolve to the foreignobject atom for the integration-point checks */
    char lower[MAX_CONTEXT_NAME];
    Py_ssize_t lower_len = local_len < (Py_ssize_t)sizeof(lower) ? local_len : (Py_ssize_t)sizeof(lower);
    for (Py_ssize_t index = 0; index < lower_len; index++) {
        char character = local[index];
        lower[index] = character >= 'A' && character <= 'Z' ? (char)(character + 32) : character;
    }
    th_buf ctx_name = {lower, lower_len, 0, PyUnicode_1BYTE_KIND};
    uint8_t ctx_flags;
    uint16_t ctx_atom = intern_atom(&ctx_name, &ctx_flags);

    th_node *root = node_new(tree, TH_NODE_ELEMENT);
    if (root == NULL) {     /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(tree); /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    /* The root acts as the context element for the algorithm (its atom drives
       every scope and insertion test), but it is named after the context so the
       public parse_fragment() can hand it back as that element. Only the
       serializer and the navigable API read this name; the algorithm never
       does, so the spelling is free to differ from the html atom. */
    root->atom = TH_TAG_HTML;
    root->tag_flags = TH_TAG_SPECIAL | TH_TAG_SCOPING;
    root->text = arena_alloc(tree, lower_len * (Py_ssize_t)sizeof(Py_UCS4));
    if (root->text == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(tree);   /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        return NULL;          /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    for (Py_ssize_t index = 0; index < lower_len; index++) {
        root->text[index] = (Py_UCS4)(unsigned char)lower[index];
    }
    root->text_len = lower_len;
    node_append(tree->document, root);
    if (!stack_push(tree, root)) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(tree);        /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        return NULL;               /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    tree->fragment_root = root;
    tree->ctx_atom = ctx_atom;
    if (ctx_atom == TH_TAG_TEMPLATE) {
        tree->head = root;              /* keep reset logic happy */
        tmpl_push(tree, M_IN_TEMPLATE); /* a template context starts in "in template" */
    }
    /* a foreign context element makes the root act as that element's namespace,
       so the foreign-content rules apply to the fragment's content */
    if (ctx_ns != TH_NS_HTML) {
        root->ns = ctx_ns;
        root->atom = ctx_atom;
    }

    th_tokenizer *sm = th_tok_new();
    if (sm == NULL) {       /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(tree); /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    /* the context element's content model selects the tokenizer's start state */
    int model = ctx_ns == TH_NS_HTML ? content_model_for(ctx_atom, ctx_flags, tree->scripting) : -1;
    if (model >= 0) {
        th_tok_set_initial(sm, (enum th_initial_state)model, NULL, 0);
    }
    setup_input(tree, sm, kind, data, length);
    th_run_state run_state;
    run_state_init(&run_state, ctx_ns == TH_NS_HTML ? fragment_mode(ctx_atom) : M_IN_BODY);
    run_drain(tree, sm, &run_state);
    run_close(tree);
    th_tok_free(sm);

    /* an html-context fragment starts in "before head"; at EOF the same
       missing-element rules a document runs synthesize the head and body, with
       the fragment root standing in for the <html> element */
    if (ctx_atom == TH_TAG_HTML) {
        ensure_head_body(tree, tree->fragment_root);
    }

    if (tree->failed) {     /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(tree); /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    return tree;
}

/* Whether a public parse_fragment() context names a real element. A bare name must
   intern to a known HTML atom: an unknown one (a typo like "zzz") would otherwise
   parse in "in body" mode under a garbage root, silently yielding the wrong tree.
   An explicitly namespaced foreign context (svg/math) is always accepted, since
   those element registries are open-ended. */
int th_tree_fragment_context_known(const char *context, Py_ssize_t context_len) {
    if ((context_len > 4 && memcmp(context, "svg ", 4) == 0) || (context_len > 5 && memcmp(context, "math ", 5) == 0)) {
        return 1;
    }
    char lower[MAX_CONTEXT_NAME];
    Py_ssize_t lower_len = context_len < (Py_ssize_t)sizeof(lower) ? context_len : (Py_ssize_t)sizeof(lower);
    for (Py_ssize_t index = 0; index < lower_len; index++) {
        char character = context[index];
        lower[index] = character >= 'A' && character <= 'Z' ? (char)(character + 32) : character;
    }
    th_buf ctx_name = {lower, lower_len, 0, PyUnicode_1BYTE_KIND};
    uint8_t ctx_flags;
    return intern_atom(&ctx_name, &ctx_flags) != TH_TAG_UNKNOWN;
}

void th_tree_free(th_tree *tree) {
    arena_block *block = tree->arena;
    while (block != NULL) {
        arena_block *next = block->next;
        PyMem_Free(block);
        block = next;
    }
    PyMem_Free(tree->open);
    PyMem_Free(tree->afe);
    PyMem_Free(tree->tmpl);
    PyMem_Free(tree->attr_slots);
    PyMem_Free(tree->attr_recs);
    th_error_sink_free(&tree->errors);
    PyMem_Free(tree);
}

/* A push parser: a document tree under construction, the resumable tokenizer
   feeding it, and the insertion-mode state that lets a feed suspend mid-document
   and the next resume in place. th_tok_feed copies each chunk and reclaims its
   consumed prefix, so the input side stays bounded across an arbitrarily long
   stream; the tree itself owns every node it has built so far. */
struct th_stream {
    th_tree *tree;
    th_tokenizer *sm;
    th_run_state run_state;
};

th_stream *th_stream_new(int positions) {
    th_stream *stream = PyMem_Calloc(1, sizeof(th_stream));
    if (stream == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    stream->tree = tree_new_document(positions);
    stream->sm = th_tok_new();
    if (stream->tree == NULL || stream->sm == NULL) { /* GCOVR_EXCL_BR_LINE: alloc cannot be forced */
        th_stream_free(stream);                       /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;                                  /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    run_state_init(&stream->run_state, M_INITIAL);
    return stream;
}

/* Point the tree's slice base at the tokenizer's current input storage, which a
   feed may have moved by compaction or reallocation. Slice tokens emitted during
   the drain that follows resolve against this base before the next th_tok_next. */
static void stream_sync_input(th_stream *stream) {
    stream->tree->data = th_tok_input_data(stream->sm, &stream->tree->kind);
}

/* Whether a fed chunk holds a U+0000. The whole-input scan setup_input does has no
   place in a streaming parse, so each chunk is scanned and the result folded into
   the tree's has_nul flag, which gates the text builder's NUL dropping. */
static int chunk_has_nul(int kind, const void *data, Py_ssize_t length) {
    if (kind == PyUnicode_1BYTE_KIND) {
        return memchr(data, '\0', (size_t)length) != NULL;
    }
    if (kind == PyUnicode_2BYTE_KIND) {
        const uint16_t *units = data;
        for (Py_ssize_t index = 0; index < length; index++) {
            if (units[index] == 0) {
                return 1;
            }
        }
        return 0;
    }
    const uint32_t *units = data;
    for (Py_ssize_t index = 0; index < length; index++) {
        if (units[index] == 0) {
            return 1;
        }
    }
    return 0;
}

int th_stream_feed(th_stream *stream, int kind, const void *data, Py_ssize_t length) {
    stream->tree->has_nul |= chunk_has_nul(kind, data, length);
    th_tok_feed(stream->sm, kind, data, length);
    stream_sync_input(stream);
    run_drain(stream->tree, stream->sm, &stream->run_state);
    if (stream->tree->failed) { /* GCOVR_EXCL_BR_LINE: only an allocation failure sets failed */
        return -1;              /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return 0;
}

th_tree *th_stream_finish(th_stream *stream) {
    th_tok_close(stream->sm);
    stream_sync_input(stream);
    run_drain(stream->tree, stream->sm, &stream->run_state);
    run_close(stream->tree);
    finalize_document(stream->tree);
    if (stream->tree->failed) { /* GCOVR_EXCL_BR_LINE: only an allocation failure sets failed */
        return NULL;            /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_tree *tree = stream->tree;
    stream->tree = NULL; /* the caller owns the tree now; th_stream_free must not touch it */
    return tree;
}

void th_stream_free(th_stream *stream) {
    if (stream->sm != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on a partially-built stream from th_stream_new */
        th_tok_free(stream->sm);
    }
    if (stream->tree != NULL) { /* still owned: a stream freed before finish, or a failed allocation */
        th_tree_free(stream->tree);
    }
    PyMem_Free(stream);
}
