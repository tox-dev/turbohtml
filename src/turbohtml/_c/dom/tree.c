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
#include "core/hash.h"   /* th_name_hash keyed hash for the dynamic attribute-name table */
#include "core/vec.h"    /* th_grow_cap overflow-safe buffer growth */
#include "core/work_steps.h"

#include <string.h>

/* Doctype quirks tables and the foreign-content step: static helper collections that
   compile into this translation unit. foreign.h forward-declares the tree-builder
   statics it shares (defined below), so both include cleanly at the top. */
#include "dom/quirks.h"
#include "dom/foreign.h"

static void buf_write_ucs4(Py_UCS4 *out, const th_buf *buf) {
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
}

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
    buf_write_ucs4(out, buf);
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

/* The atom and category flags of a fragment context's lowercased name, TH_TAG_UNKNOWN for an empty one. */
static uint16_t intern_atom(const th_buf *name, uint8_t *out_flags) {
    uint16_t atom = name->len == 0 ? TH_TAG_UNKNOWN : th_tag_lookup(name->data, name->len);
    *out_flags = th_tag_flags(atom);
    return atom;
}

/* The token's interned tag atom, cached on the token by the run loop once per
   token so every "which tag is this" comparison is a field read, not a re-intern. */
static uint16_t tok_atom(const th_token *tok) {
    return tok->atom;
}

/* 1 when the run of filled slots through slot is longer than TH_FAST_RUN_LIMIT. A
   lookup walks at most the run its home falls in, so bounding every run bounds hits
   and misses alike. Every run was within the limit before this seat, so the walks back
   and ahead each cover at most the limit: neither is a counted work step. */
static int attr_run_long(const th_tree *tree, uint32_t slot) {
    uint32_t mask = tree->attr_slot_mask;
    uint32_t length = 1;
    for (uint32_t probe = (slot - 1) & mask; tree->attr_slots[probe] != 0; probe = (probe - 1) & mask) {
        length++;
    }
    for (uint32_t probe = (slot + 1) & mask; tree->attr_slots[probe] != 0; probe = (probe + 1) & mask) {
        length++;
    }
    return length > TH_FAST_RUN_LIMIT;
}

/* Replace the slots with cap of them and seat every record under its stored hash,
   which is th_name_hash when keyed, else FNV-1a. Doubling cannot lengthen the longest
   run: the records of a run in the new slots have their homes inside it, so they
   already formed a run at least as long in the old ones. A failure flags the parse
   as arena_alloc does. */
static int attr_slots_rebuild(th_tree *tree, uint32_t cap, int keyed) {
    uint32_t *slots = PyMem_Calloc(cap, sizeof(uint32_t));
    if (slots == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        tree->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    uint32_t mask = cap - 1;
    for (uint32_t index = 0; index < tree->attr_rec_count; index++) {
        uint32_t probe = tree->attr_recs[index].hash & mask;
        while (slots[probe] != 0) {
            TH_WORK_STEP();
            probe = (probe + 1) & mask;
        }
        slots[probe] = index + 1;
    }
    PyMem_Free(tree->attr_slots);
    tree->attr_slots = slots;
    tree->attr_fast = keyed ? NULL : slots;
    tree->attr_slot_mask = mask;
    return 0;
}

/* Move the table to th_name_hash for the rest of the tree's life: FNV-1a is fixed,
   so a document can pick names that share one run of slots, and once a run grows
   past TH_FAST_RUN_LIMIT every record is rehashed under a key the document cannot
   see and seated again. */
static TH_NOINLINE int attr_table_rekey(th_tree *tree) {
    for (uint32_t index = 0; index < tree->attr_rec_count; index++) {
        th_attr_record *rec = &tree->attr_recs[index];
        rec->hash = th_name_hash(rec->name, (Py_ssize_t)rec->name_len);
    }
    return attr_slots_rebuild(tree, tree->attr_slot_mask + 1, 1);
}

/* Ensure the dynamic table can take one more record: grow the record array, and
   build or grow the hash slots past half load (rehashing the records): every insert
   past TH_FAST_RUN_LIMIT records measures the run it joined, and runs stay short at
   half load where they grow long at 3/4. A
   failure flags the parse as arena_alloc does, so the builder drops the tree instead
   of keeping an attribute with no atom. */
static int attr_table_reserve(th_tree *tree) {
    if (tree->attr_rec_count == tree->attr_rec_cap) {
        size_t cap;
        size_t bytes;
        int grew = th_grow_cap((size_t)tree->attr_rec_cap + 1, (size_t)tree->attr_rec_cap, 8, sizeof(th_attr_record),
                               &cap, &bytes);
        if (!grew) {          /* GCOVR_EXCL_BR_LINE: size overflow needs a length no allocation could hold */
            tree->failed = 1; /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
            return -1;        /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
        }
        th_attr_record *recs = PyMem_Realloc(tree->attr_recs, bytes);
        if (recs == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            tree->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
            return -1;        /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        }
        tree->attr_recs = recs;
        tree->attr_rec_cap = (uint32_t)cap;
    }
    if (tree->attr_slots == NULL || tree->attr_rec_count + 1 > (tree->attr_slot_mask + 1) / 2) {
        int keyed = tree->attr_slots != NULL && tree->attr_fast == NULL;
        return attr_slots_rebuild(tree, tree->attr_slots == NULL ? 16 : (tree->attr_slot_mask + 1) * 2, keyed);
    }
    return 0;
}

/* Add a name the table does not hold, hashed to hash under the table's current hash.
   Out of line, since a tree adds each name once and looks it up for every element. */
TH_NOINLINE uint32_t intern_attr_insert(th_tree *tree, const char *bytes, Py_ssize_t len, uint32_t hash) {
    if (len > 6 && memcmp(bytes, "xmlns:", 6) == 0) {
        tree->has_prefix_decl = 1;
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
        TH_WORK_STEP();
        probe = (probe + 1) & mask;
    }
    tree->attr_slots[probe] = index + 1;
    if (tree->attr_fast != NULL && tree->attr_rec_count > TH_FAST_RUN_LIMIT && attr_run_long(tree, probe)) {
        if (attr_table_rekey(tree) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return TH_ATTR_UNKNOWN;       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    return TH_ATTR__DYNAMIC_BASE + index;
}

/* intern_attr_dynamic for a table that is not built yet, which starts on FNV-1a, or
   one on th_name_hash. Out of line, so the FNV-1a lookup keeps its registers. */
TH_NOINLINE uint32_t intern_attr_slow(th_tree *tree, const char *bytes, Py_ssize_t len) {
    if (tree->attr_slots == NULL) {
        return intern_attr_insert(tree, bytes, len, th_fnv1a_bytes(bytes, len));
    }
    uint32_t hash = th_name_hash(bytes, len);
    uint32_t found = dynamic_slot(tree, bytes, len, hash);
    return found != 0 ? TH_ATTR__DYNAMIC_BASE + (found - 1) : intern_attr_insert(tree, bytes, len, hash);
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
    TH_WORK_STEP();
    if (atom < TH_ATTR__DYNAMIC_BASE) {
        const th_attr_entry *entry = &th_attr_table[atom - 1];
        *out_len = entry->name_len;
        return entry->name;
    }
    const th_attr_record *rec = &tree->attr_recs[atom - TH_ATTR__DYNAMIC_BASE];
    *out_len = rec->name_len;
    return rec->name;
}

/* th_attr_lookup's dynamic step for a table on th_name_hash
   (prototype in dom/tree_internal.h). Out of line, so th_attr_lookup_inline keeps only
   the FNV-1a step. */
TH_NOINLINE uint32_t th_attr_lookup_slow(th_tree *tree, const char *bytes, Py_ssize_t len) {
    uint32_t found = dynamic_slot(tree, bytes, len, th_name_hash(bytes, len));
    return found != 0 ? TH_ATTR__DYNAMIC_BASE + (found - 1) : UINT32_MAX;
}

/* Resolve a query attribute name (UTF-8 bytes) to the atom an element would carry
   for it, without interning: a static atom, a dynamic atom already seen in this
   tree, or UINT32_MAX when no element in the tree has that name. Out of line, so
   callers that resolve a name once per query keep their registers. */
TH_NOINLINE uint32_t th_attr_lookup(th_tree *tree, const char *bytes, Py_ssize_t len) {
    return th_attr_lookup_inline(tree, bytes, len);
}

uint32_t th_tree_attr_generation(const th_tree *tree) {
    return tree->attr_rec_count;
}

uint64_t th_tree_attr_version(const th_tree *tree) {
    return tree->attr_version;
}

uint64_t th_tree_id_version(const th_tree *tree) {
    return tree->id_version;
}

void th_tree_note_element_edit(th_tree *tree) {
    tree->id_version++;
}

int th_tree_slot_cache_valid(const th_tree *tree) {
    return tree->slot_cache_valid;
}

void th_tree_set_slot_cache(th_tree *tree, int valid) {
    tree->slot_cache_valid = valid;
}

int th_tree_quirks(const th_tree *tree) {
    return tree->quirks;
}

int th_doctype_is_quirky(th_node *node) {
    th_token tok = {0};
    Py_ssize_t name_len = 0;
    while (name_len < node->text_len && node->text[name_len] != ' ') {
        name_len++;
    }
    tok.name = (th_buf){node->text, name_len, 0, PyUnicode_4BYTE_KIND};
    const Py_UCS4 *public_id;
    const Py_UCS4 *system_id;
    Py_ssize_t public_len;
    Py_ssize_t system_len;
    if (th_node_doctype_ids(node, &public_id, &public_len, &system_id, &system_len)) {
        tok.has_public_id = (node->tag_flags & TH_DOCTYPE_HAS_PUBLIC) != 0;
        tok.has_system_id = (node->tag_flags & TH_DOCTYPE_HAS_SYSTEM) != 0;
        tok.public_id = (th_buf){(void *)public_id, public_len, 0, PyUnicode_4BYTE_KIND};
        tok.system_id = (th_buf){(void *)system_id, system_len, 0, PyUnicode_4BYTE_KIND};
    }
    return doctype_is_quirky(&tok);
}

int th_tree_scripting(const th_tree *tree) {
    return tree->scripting;
}

int th_tree_reparse_hazard(const th_tree *tree) {
    return tree->reparse_hazard;
}

int th_tree_depth_capped(const th_tree *tree) {
    return tree->depth_capped;
}

Py_ssize_t th_tree_nest_bound(const th_tree *tree) {
    return tree->nest_bound;
}

int th_tree_nests_deep(const th_tree *tree) {
    return (tree->nest_bound & TH_NEST_CAPPED) == 0;
}

void th_tree_note_deepened(th_tree *tree) {
    tree->nest_bound &= TH_NEST_OBSERVED;
}

int th_tree_failed(const th_tree *tree) {
    return tree->failed;
}

/* Fold the preprocessing errors into the tokenizer's, once. They depend only on the input the
   tree still borrows, so finding them at the first read costs a parse that never reads errors
   nothing at all. */
int th_tree_ensure_input_errors(th_tree *tree, int kind, const void *data, Py_ssize_t length) {
    if (tree->input_errors_merged) {
        return 0;
    }
    th_error_sink preprocessing = {0};
    /* the merge leaves the tree's errors untouched on failure, so a later read can retry */
    th_input_stream_errors(kind, data, length, &preprocessing);
    int failed = th_error_sink_merge(&tree->errors, &preprocessing) < 0;
    th_error_sink_free(&preprocessing);
    tree->input_errors_merged = !failed;
    return -failed;
}

const th_parse_error *th_tree_errors(const th_tree *tree, Py_ssize_t *out_count) {
    *out_count = tree->errors.len;
    return tree->errors.items;
}

int th_node_text_is_blank(th_tree *tree, th_node *node) {
    const Py_UCS4 *text = need_text(tree, node); /* realize a zero-copy span before scanning */
    if (text == NULL && node->text_len > 0) {    /* GCOVR_EXCL_BR_LINE: lazy text allocation failure */
        return -1;                               /* GCOVR_EXCL_LINE: allocation failure */
    }
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
    return atom == TH_TAG_UNKNOWN ? 0 : th_tag_table[atom - 1].flags;
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

const th_src_loc *th_node_source_location(th_tree *tree, th_node *node) {
    if (!tree->track_locations || node->type != TH_NODE_ELEMENT) {
        return NULL; /* untracked tree or a node type that reserves no location slot */
    }
    return *node_loc(node); /* NULL for a synthetic element with no source start tag */
}

const void *th_tree_source_data(th_tree *tree, int *kind, int *has_nul) {
    *kind = tree->kind;
    *has_nul = tree->has_nul;
    return tree->data;
}

/* A shallow element clone (same atom/name/attrs, no children) for the adoption
   agency and active-formatting-element reconstruction. */
static th_node *node_clone(th_tree *tree, th_node *src, int with_attrs);

static th_node *current_node(th_tree *tree) {
    return tree->open_len > 0 ? tree->open[tree->open_len - 1] : tree->document;
}

/* The open stack grows past the depth cap, and a search must see all of it: the table and scope walks must find an
   element below the cap or stop at a boundary below it. A block summary per TH_MAX_TREE_DEPTH entries lets a search
   skip the blocks that hold none of its atoms, so a search costs about one block per cap's worth of nesting. */
#define STACK_BLOCK TH_MAX_TREE_DEPTH

static void atom_set_add(th_atom_set *set, uint16_t atom) {
    set->bits[atom / 64] |= (uint64_t)1 << (atom % 64);
}

static int atom_sets_meet(const th_atom_set *left, const th_atom_set *right) {
    for (int word = 0; word < TH_ATOM_WORDS; word++) {
        if (left->bits[word] & right->bits[word]) {
            return 1;
        }
    }
    return 0;
}

static void block_add(th_stack_block *block, const th_node *node) {
    if (block->count[node->atom]++ == 0) {
        atom_set_add(&block->present, node->atom);
    }
}

static void block_drop(th_stack_block *block, const th_node *node) {
    if (--block->count[node->atom] == 0) {
        block->present.bits[node->atom / 64] &= ~((uint64_t)1 << (node->atom % 64));
    }
}

static int blocks_reserve(th_tree *tree, Py_ssize_t count) {
    if (count <= tree->open_block_cap) {
        return 1;
    }
    Py_ssize_t cap = tree->open_block_cap * 2 > count ? tree->open_block_cap * 2 : count;
    th_stack_block *grown = PyMem_Realloc(tree->open_blocks, (size_t)cap * sizeof(th_stack_block));
    if (grown == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        tree->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        return 0;         /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    memset(grown + tree->open_block_cap, 0, (size_t)(cap - tree->open_block_cap) * sizeof(th_stack_block));
    tree->open_blocks = grown;
    tree->open_block_cap = cap;
    return 1;
}

/* Recount the blocks from the one holding index `from` up to the top of the stack or of the old stack, whichever is
   higher. A stack that never passed one block has no summaries to recount. */
static void blocks_recount(th_tree *tree, Py_ssize_t from, Py_ssize_t old_len) {
    if (tree->open_blocks == NULL) {
        return;
    }
    Py_ssize_t first = from / STACK_BLOCK;
    Py_ssize_t last_old = (old_len - 1) / STACK_BLOCK;
    Py_ssize_t last = (tree->open_len - 1) / STACK_BLOCK;
    Py_ssize_t top = last_old > last ? last_old : last;
    memset(&tree->open_blocks[first], 0, (size_t)(top - first + 1) * sizeof(th_stack_block));
    for (Py_ssize_t index = first * STACK_BLOCK; index < tree->open_len; index++) {
        block_add(&tree->open_blocks[index / STACK_BLOCK], tree->open[index]);
    }
}

enum stack_scan {
    SCAN_ATOM,    /* an element with the atom */
    SCAN_SCOPE,   /* the atom, or a default scope boundary */
    SCAN_LIST,    /* the atom, or a list-item scope boundary */
    SCAN_TABLE,   /* the atom, or a table-scope boundary */
    SCAN_BUTTON,  /* a p, or a button-scope boundary */
    SCAN_HEADING, /* a heading, or a default scope boundary */
    SCAN_RESET,   /* a table-context or body atom, or the fragment context atom */
};

static void scope_boundary_atoms(th_atom_set *set) {
    static const uint16_t boundary[] = {
        TH_TAG_APPLET,
        TH_TAG_CAPTION,
        TH_TAG_HTML,
        TH_TAG_MARQUEE,
        TH_TAG_OBJECT,
        TH_TAG_SELECT,
        TH_TAG_TABLE,
        TH_TAG_TD,
        TH_TAG_TEMPLATE,
        TH_TAG_TH,
        TH_TAG_MI,
        TH_TAG_MO,
        TH_TAG_MN,
        TH_TAG_MS,
        TH_TAG_MTEXT,
        TH_TAG_ANNOTATION_XML,
        TH_TAG_FOREIGNOBJECT,
        TH_TAG_DESC,
        TH_TAG_TITLE,
    };
    for (size_t index = 0; index < sizeof(boundary) / sizeof(boundary[0]); index++) {
        atom_set_add(set, boundary[index]);
    }
}

/* The highest index at or below `index` whose block may hold an atom the scan reads, or -1. A search visits every entry
   from there down, so a block that holds none of its atoms is skipped whole; the entries it visits are checked as
   before. */
static Py_ssize_t stack_scan_blocks(const th_tree *tree, Py_ssize_t index, enum stack_scan kind, uint16_t atom) {
    th_atom_set wanted = {{0}};
    switch (kind) {
    case SCAN_ATOM:
        atom_set_add(&wanted, atom);
        break;
    case SCAN_SCOPE:
        scope_boundary_atoms(&wanted);
        atom_set_add(&wanted, atom);
        break;
    case SCAN_LIST:
        scope_boundary_atoms(&wanted);
        atom_set_add(&wanted, TH_TAG_OL);
        atom_set_add(&wanted, TH_TAG_UL);
        atom_set_add(&wanted, atom);
        break;
    case SCAN_TABLE:
        atom_set_add(&wanted, TH_TAG_TABLE);
        atom_set_add(&wanted, TH_TAG_TEMPLATE);
        atom_set_add(&wanted, TH_TAG_HTML);
        atom_set_add(&wanted, atom);
        break;
    case SCAN_BUTTON:
        scope_boundary_atoms(&wanted);
        atom_set_add(&wanted, TH_TAG_P);
        atom_set_add(&wanted, TH_TAG_BUTTON);
        break;
    case SCAN_HEADING:
        scope_boundary_atoms(&wanted);
        atom_set_add(&wanted, TH_TAG_H1);
        atom_set_add(&wanted, TH_TAG_H2);
        atom_set_add(&wanted, TH_TAG_H3);
        atom_set_add(&wanted, TH_TAG_H4);
        atom_set_add(&wanted, TH_TAG_H5);
        atom_set_add(&wanted, TH_TAG_H6);
        break;
    default: /* SCAN_RESET */
        atom_set_add(&wanted, TH_TAG_TD);
        atom_set_add(&wanted, TH_TAG_TH);
        atom_set_add(&wanted, TH_TAG_TR);
        atom_set_add(&wanted, TH_TAG_TBODY);
        atom_set_add(&wanted, TH_TAG_THEAD);
        atom_set_add(&wanted, TH_TAG_TFOOT);
        atom_set_add(&wanted, TH_TAG_CAPTION);
        atom_set_add(&wanted, TH_TAG_COLGROUP);
        atom_set_add(&wanted, TH_TAG_TABLE);
        atom_set_add(&wanted, TH_TAG_TEMPLATE);
        atom_set_add(&wanted, TH_TAG_HEAD);
        atom_set_add(&wanted, TH_TAG_FRAMESET);
        atom_set_add(&wanted, TH_TAG_HTML);
        atom_set_add(&wanted, TH_TAG_BODY);
        atom_set_add(&wanted, atom);
        break;
    }
    while (index >= 0) {
        if (atom_sets_meet(&tree->open_blocks[index / STACK_BLOCK].present, &wanted)) {
            return index;
        }
        index = index / STACK_BLOCK * STACK_BLOCK - 1;
    }
    return -1;
}

/* A shallow stack has no block summaries, so every search reads all of it and needs no call per entry. */
static inline Py_ssize_t stack_scan(const th_tree *tree, Py_ssize_t index, enum stack_scan kind, uint16_t atom) {
    return tree->open_blocks == NULL ? index : stack_scan_blocks(tree, index, kind, atom);
}

/* An open element nests at most this many levels deep: each stack entry is one level, and each template's content
   fragment is one more. The stack alone undercounts a document of nested templates, which the DOM cap sees. */
static Py_ssize_t stack_depth_bound(const th_tree *tree) {
    return tree->open_len + tree->open_templates;
}

static int is_html_template(const th_node *node) {
    return node->ns == TH_NS_HTML && node->atom == TH_TAG_TEMPLATE;
}

static void note_stack_depth(th_tree *tree) {
    if (stack_depth_bound(tree) >= TH_HAS_MEMO_DEPTH) {
        tree->nest_bound &= TH_NEST_CAPPED;
    }
}

/* A template's content fragment is one more level, so a template that enters the stack adds to the depth bound. */
static void note_template_pushed(th_tree *tree) {
    tree->open_templates++;
    tree->stack_tracked = 1;
    note_stack_depth(tree);
    if (stack_depth_bound(tree) + 1 >= TH_MAX_TREE_DEPTH) {
        tree->depth_near_cap = 1;
    }
}

/* The block summaries are built once the stack passes STACK_LAZY_BLOCKS entries. From then on every push and pop
   updates them, so the push check runs on every push. */
#define STACK_LAZY_BLOCKS 64

static int stack_blocks_push(th_tree *tree, th_node *node) {
    Py_ssize_t index = tree->open_len - 1;
    int building = tree->open_blocks == NULL;
    if (!blocks_reserve(tree, index / STACK_BLOCK + 1)) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return 0;                                         /* GCOVR_EXCL_LINE: allocation failure */
    }
    if (building) {
        blocks_recount(tree, 0, index);
        tree->stack_tracked = 1;
        tree->push_check_at = 0;
    } else {
        block_add(&tree->open_blocks[index / STACK_BLOCK], node);
    }
    return 1;
}

/* The checks a push makes once the stack reaches push_check_at: the nest bound and the depth cap from the same depth
   and the block summaries. A push to a shallow stack skips them with one compare. */
static inline int stack_push_checks(th_tree *tree, th_node *node) {
    if (tree->open_len < tree->push_check_at) {
        return 1;
    }
    if (tree->open_blocks == NULL && tree->open_len < TH_HAS_MEMO_DEPTH) {
        tree->push_check_at = TH_HAS_MEMO_DEPTH; /* also how a zeroed tree sets its first gate */
        return 1;
    }
    tree->nest_bound &= TH_NEST_CAPPED;
    if (stack_depth_bound(tree) + 1 >= TH_MAX_TREE_DEPTH) {
        tree->depth_near_cap = 1;
    }
    if (tree->open_blocks != NULL || tree->open_len > STACK_LAZY_BLOCKS) {
        return stack_blocks_push(tree, node);
    }
    return 1;
}

static int stack_push(th_tree *tree, th_node *node) {
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
    if (node != NULL && node->ns == TH_NS_HTML && node->atom == TH_TAG_P) { /* GCOVR_EXCL_BR_LINE: alloc failure */
        tree->open_p_count++;
    }
    if (!stack_push_checks(tree, node)) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return 0;                         /* GCOVR_EXCL_LINE: allocation failure */
    }
    tree->stack_version++;
    return 1;
}

static void maybe_clone_option(th_tree *tree, th_node *option);
static int name_matches(const th_node *node, const th_token *token, int fold);

static void stack_pop(th_tree *tree) {
    if (tree->open_len > 0) { /* GCOVR_EXCL_BR_LINE: only reached with a non-empty stack */
        tree->open_len--;
        tree->stack_version++;
        th_node *popped = tree->open[tree->open_len];
        popped->left_stack = 1;
        if (popped->ns == TH_NS_HTML && popped->atom == TH_TAG_P) {
            tree->open_p_count--;
        }
        if (tree->stack_tracked) {
            if (is_html_template(popped)) {
                tree->open_templates--;
            }
            if (tree->open_blocks != NULL) {
                block_drop(&tree->open_blocks[tree->open_len / STACK_BLOCK], popped);
            }
        }
        /* record that an end tag named this element (as opposed to an implied or
           EOF close) so escape-mode sanitizing reproduces the author's `</tag>` */
        if (tree->closing_end_tag != NULL && name_matches(popped, tree->closing_end_tag, popped->ns != TH_NS_HTML)) {
            popped->tag_flags |= TH_ELEM_CLOSED_BY_END_TAG;
            if (tree->track_locations) {
                th_src_loc *loc = *node_loc(popped); /* NULL for a synthetic element the source never opened */
                const th_token *end = tree->closing_end_tag;
                if (loc != NULL) {
                    loc->end_tag =
                        (th_src_span){end->line, end->col, end->src_off, end->end_line, end->end_col, end->end_off};
                    loc->has_end_tag = 1;
                }
            }
        }
        if (popped->ns == TH_NS_HTML && popped->atom == TH_TAG_OPTION) {
            /* "when an option element is popped off the stack ... run maybe
               clone an option into selectedcontent" */
            maybe_clone_option(tree, popped);
        }
    }
}

/* Stamp an element's end-tag span from the end-tag token the source closed it
   with. stack_pop already does this for elements it pops by name; body, html,
   and form are closed by insertion-mode switches or an out-of-order removal that
   never reach that path, so their handlers call this directly. */
static void record_end_tag_location(th_tree *tree, th_node *node, const th_token *end) {
    if (!tree->track_locations) {
        return;
    }
    th_src_loc *loc = *node_loc(node); /* NULL for a synthetic element the source never opened */
    if (loc != NULL) {
        loc->end_tag = (th_src_span){end->line, end->col, end->src_off, end->end_line, end->end_col, end->end_off};
        loc->has_end_tag = 1;
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

static int find_in_scope(th_tree *tree, uint16_t atom) {
    for (Py_ssize_t index = stack_scan(tree, tree->open_len - 1, SCAN_SCOPE, atom); index >= 0;
         index = stack_scan(tree, index - 1, SCAN_SCOPE, atom)) {
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

static int has_in_scope(th_tree *tree, uint16_t atom) {
    if (tree->scope_version != tree->stack_version || tree->scope_atom != atom) {
        tree->scope_result = find_in_scope(tree, atom);
        tree->scope_atom = atom;
        tree->scope_version = tree->stack_version;
    }
    return tree->scope_result;
}

/* List-item scope: the default boundaries plus ol and ul. */
static int has_in_list_item_scope(th_tree *tree, uint16_t atom) {
    /* The html element bounds the stack walk. */
    for (Py_ssize_t index = stack_scan(tree, tree->open_len - 1, SCAN_LIST, atom); index >= 0; /* GCOVR_EXCL_BR_LINE */
         index = stack_scan(tree, index - 1, SCAN_LIST, atom)) {
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
    while (tree->open_len > 1) {
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
    while (tree->open_len > 1) { /* GCOVR_EXCL_BR_LINE: every caller checks the atom is on the stack first */
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
    while (tree->open_len > 1) {
        th_node *node = current_node(tree);
        uint16_t atom = node->atom;
        /* the open_len > 1 bound keeps the html root out of this walk, so html is never a boundary here */
        if (node->ns == TH_NS_HTML /* GCOVR_EXCL_BR_LINE */ &&
            (atom == atom_a || atom == atom_b || atom == atom_c || atom == TH_TAG_TEMPLATE)) {
            return; /* template is also a table-context boundary */
        }
        stack_pop(tree);
    }
}

/* Is an HTML element with this atom on the stack (table scope is "up to the
   nearest HTML table, template or html")? */
static int has_in_table_scope(th_tree *tree, uint16_t atom) {
    /* The html element bounds the stack walk. */
    for (Py_ssize_t index = stack_scan(tree, tree->open_len - 1, SCAN_TABLE, atom); index >= 0; /* GCOVR_EXCL_BR_LINE */
         index = stack_scan(tree, index - 1, SCAN_TABLE, atom)) {
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
static void insertion_location_place(th_tree *tree, th_node *target, th_node **parent, th_node **before) {
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

/* Whether node has at least limit ancestors. The walk stops at limit steps, so it costs at most limit. */
static int has_ancestors_at_least(const th_node *node, Py_ssize_t limit) {
    Py_ssize_t count = 0;
    for (const th_node *up = node->parent; up != NULL; up = up->parent) {
        if (++count >= limit) {
            return 1;
        }
    }
    return 0;
}

/* The open-element stack is never refused, but the DOM stops at TH_MAX_TREE_DEPTH levels: an append under a parent with
   that many ancestors goes to the parent's parent instead (Blink attaches the same way). Nodes the capped parent placed
   after itself were created after it, so they stay in front of the new node: the insertion point is after them. A
   sibling created before the parent, such as a table a reconstructed element was foster-parented before, still bounds
   the run. Foster parenting inserts before a table, which is shallower than the cap, so only appends can need the move.
   A template's content fragment counts as its template. A stack whose depth bound stays below the cap cannot reach it,
   which skips the walk. */
static TH_NOINLINE void cap_insertion_walk(th_tree *tree, th_node **parent, th_node **before) {
    if (!has_ancestors_at_least(*parent, TH_MAX_TREE_DEPTH)) {
        return;
    }
    tree->depth_capped = 1;
    th_node *anchor = (*parent)->type == TH_NODE_CONTENT ? (*parent)->parent : *parent;
    *parent = anchor->parent;
    *before = anchor->next_sibling;
    while (*before != NULL && (*before)->seq > anchor->seq) {
        *before = (*before)->next_sibling;
    }
}

static void insertion_location_target(th_tree *tree, th_node *target, th_node **parent, th_node **before) {
    insertion_location_place(tree, target, parent, before);
    if (tree->depth_near_cap && *before == NULL && stack_depth_bound(tree) + 1 >= TH_MAX_TREE_DEPTH) {
        cap_insertion_walk(tree, parent, before);
    }
}

static void insertion_location(th_tree *tree, th_node **parent, th_node **before) {
    insertion_location_target(tree, current_node(tree), parent, before);
}

/* Record the start-tag and per-attribute source spans a location-tracking tree
   hangs off the element, from the token the tokenizer stamped. The end-tag span is
   left absent until the source closes the element (record_end_tag_location). NULL
   on allocation failure. */
static th_src_loc *build_source_location(th_tree *tree, th_node *node, const th_token *token) {
    th_src_loc *loc =
        arena_alloc(tree, (Py_ssize_t)sizeof(th_src_loc) + token->attr_count * (Py_ssize_t)sizeof(th_src_attr));
    if (loc == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;   /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    loc->start_tag =
        (th_src_span){token->line, token->col, token->src_off, token->end_line, token->end_col, token->end_off};
    loc->end_tag = (th_src_span){0};
    loc->has_end_tag = 0;
    loc->self_closing = token->self_closing;
    loc->start_dirty = 0;
    loc->end_dirty = 0;
    loc->attr_count = token->attr_count;
    loc->attrs = token->attr_count > 0 ? (th_src_attr *)(loc + 1) : NULL;
    if (token->attr_count > 0) {
        for (Py_ssize_t index = 0; index < token->attr_count; index++) {
            const th_attr *src = &token->attrs[index];
            const th_attr_loc *src_loc = &token->attr_locs[index];
            loc->attrs[index].name_atom = intern_attr(tree, &src->name); /* idempotent with insert_element's */
            loc->attrs[index].span = (th_src_span){src_loc->name_line, src_loc->name_col, src_loc->name_off,
                                                   src_loc->end_line,  src_loc->end_col,  src_loc->end_off};
        }
    }
    *node_loc(node) = loc;
    return loc;
}

/* Build an element node from a start-tag token -- atom, category flags, source
   position/spans, name and attributes -- without placing it in the tree. insert_element
   appends it at the appropriate place; the declarative-shadow path keeps it off the
   light tree, only on the stack of open elements. NULL on allocation failure. */
static th_node *build_element(th_tree *tree, const th_token *token) {
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
    if (tree->track_locations) {
        /* the record's allocation failure is unreachable from a test */
        if (build_source_location(tree, node, token) == NULL) { /* GCOVR_EXCL_BR_LINE */
            return NULL;                                        /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    if (node->atom == TH_TAG_UNKNOWN) {
        node->text = buf_to_ucs4(tree, &token->name, &node->text_len);
    } else {
        node->text = (Py_UCS4 *)th_tag_wide_name(node->atom);
        node->text_len = th_tag_table[node->atom - 1].name_len;
    }
    if (token->attr_count > 0) {
        Py_ssize_t value_len = 0;
        for (Py_ssize_t index = 0; index < token->attr_count; index++) {
            value_len += token->attrs[index].has_value ? token->attrs[index].value.len : 0;
        }
        Py_ssize_t attrs_size = token->attr_count * (Py_ssize_t)sizeof(th_node_attr);
        node->attrs = arena_alloc(tree, attrs_size + value_len * (Py_ssize_t)sizeof(Py_UCS4));
        if (node->attrs == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;           /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        }
        Py_UCS4 *value = (Py_UCS4 *)((char *)node->attrs + attrs_size);
        node->attr_count = token->attr_count;
        for (Py_ssize_t index = 0; index < token->attr_count; index++) {
            const th_attr *src = &token->attrs[index];
            th_node_attr *dst = &node->attrs[index];
            dst->name_atom = intern_attr(tree, &src->name);
            dst->value_len = src->has_value ? src->value.len : 0;
            dst->value = dst->value_len > 0 ? value : NULL;
            if (dst->value != NULL) {
                buf_write_ucs4(value, &src->value);
                value += dst->value_len;
            }
        }
    }
    return node;
}

/* The start tag's attribute with this lowercased ASCII name (tokenizer names are
   already lowercased), or NULL. Used for the boolean shadowroot* attributes, to
   read shadowrootmode's value, and to read a <meta> encoding declaration. A wider name
   stores its code units in more than one byte, so its bytes can equal the literal's
   without the name being it; only a one-byte name is compared. */
static const th_attr *token_attr(const th_token *token, const char *name, Py_ssize_t name_len) {
    for (Py_ssize_t index = 0; index < token->attr_count; index++) {
        const th_attr *attr = &token->attrs[index];
        if (attr->name.len == name_len && attr->name.kind == PyUnicode_1BYTE_KIND &&
            memcmp(attr->name.data, name, (size_t)name_len) == 0) {
            return attr;
        }
    }
    return NULL;
}

/* Whether a token buffer equals an ASCII literal, ASCII case-insensitively (an
   enumerated attribute value like shadowrootmode's matches case-insensitively). */
static int buf_iequals_ascii(const th_buf *buf, const char *ascii, Py_ssize_t ascii_len) {
    if (buf->len != ascii_len) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < ascii_len; index++) {
        Py_UCS4 ch = buf_read(buf, index);
        if (ch >= 'A' && ch <= 'Z') {
            ch += 32;
        }
        if (ch != (Py_UCS4)(unsigned char)ascii[index]) {
            return 0;
        }
    }
    return 1;
}

/* An attribute value as a lowercased, NUL-terminated ASCII string in the arena. No
   encoding label and no charset= token of a content value holds a non-ASCII code
   point, so one is written as 0x7f: a byte no label contains, which therefore cannot
   make an unrelated value resolve. Lowercasing here is what lets dom/document.c reuse
   the prescan's case-sensitive charset= scan on a content value. */
static const char *meta_value_ascii(th_tree *tree, const th_buf *value) {
    char *out = arena_alloc(tree, value->len + 1);
    if (out == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;   /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    for (Py_ssize_t index = 0; index < value->len; index++) {
        Py_UCS4 ch = buf_read(value, index);
        out[index] = ch < 0x80 ? (char)lower_ascii(ch) : (char)0x7f;
    }
    out[value->len] = '\0';
    return out;
}

static void append_meta_label(th_tree *tree, const th_buf *value, int from_content) {
    if (tree->meta_label_count == tree->meta_label_cap) {
        Py_ssize_t cap = tree->meta_label_cap == 0 ? TH_INITIAL_CAPACITY(4) : tree->meta_label_cap * 2;
        th_meta_label *grown = PyMem_Realloc(tree->meta_labels, (size_t)cap * sizeof(*grown));
        if (grown == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            tree->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
            return;           /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        }
        tree->meta_labels = grown;
        tree->meta_label_cap = cap;
    }
    const char *text = meta_value_ascii(tree, value);
    if (text == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return;         /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    tree->meta_labels[tree->meta_label_count].text = text;
    tree->meta_labels[tree->meta_label_count].from_content = from_content;
    tree->meta_label_count++;
}

/* ASCII case-insensitive equality of an attribute name with a literal. Names a rewrite handler supplies are not
   lowercased, and the tree builder reads them lowercased. */
static int name_iequals_ascii(const char *name, Py_ssize_t len, const char *ascii) {
    if (len != (Py_ssize_t)strlen(ascii)) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        char c = name[index];
        if (c >= 'A' && c <= 'Z') {
            c = (char)(c - 'A' + 'a');
        }
        if (c != ascii[index]) {
            return 0;
        }
    }
    return 1;
}

int th_builder_reads_attr(uint8_t ns, uint16_t tag, const char *name, Py_ssize_t len) {
    if (ns == TH_NS_HTML) {
        switch (tag) {
        case TH_TAG_INPUT:
            return name_iequals_ascii(name, len, "type");
        case TH_TAG_OPTION:
            return name_iequals_ascii(name, len, "disabled") || name_iequals_ascii(name, len, "selected");
        case TH_TAG_OPTGROUP:
        case TH_TAG_SELECTEDCONTENT:
            return name_iequals_ascii(name, len, "disabled");
        case TH_TAG_SELECT:
            return name_iequals_ascii(name, len, "multiple");
        case TH_TAG_TEMPLATE:
            return name_iequals_ascii(name, len, "shadowrootmode") ||
                   name_iequals_ascii(name, len, "shadowrootdelegatesfocus") ||
                   name_iequals_ascii(name, len, "shadowrootclonable");
        default:
            return 0;
        }
    }
    if (ns == TH_NS_MATHML && tag == TH_TAG_ANNOTATION_XML) {
        return name_iequals_ascii(name, len, "encoding");
    }
    if (tag == TH_TAG_FONT) {
        return name_iequals_ascii(name, len, "color") || name_iequals_ascii(name, len, "face") ||
               name_iequals_ascii(name, len, "size");
    }
    return 0;
}

/* Record a <meta> element's encoding declarations in the order the tree-construction rule tries them: its charset
   value, then -- for an http-equiv="content-type" element -- its content value, whose charset= token still needs
   extracting. A value that names no encoding falls through to the next declaration rather than ending the search,
   so every declaration is kept, in document order. Other <meta> elements declare nothing. */
static void record_meta_label(th_tree *tree, const th_token *token) {
    const th_attr *charset = token_attr(token, "charset", 7);
    if (charset != NULL && charset->has_value) {
        append_meta_label(tree, &charset->value, 0);
    }
    const th_attr *pragma = token_attr(token, "http-equiv", 10);
    if (pragma == NULL || !pragma->has_value || !buf_iequals_ascii(&pragma->value, "content-type", 12)) {
        return;
    }
    const th_attr *content = token_attr(token, "content", 7);
    if (content != NULL && content->has_value) {
        append_meta_label(tree, &content->value, 1);
    }
}

static th_node *insert_element(th_tree *tree, const th_token *token) {
    th_node *node = build_element(tree, token);
    if (node == NULL) {    /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return tree->sink; /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    th_node *parent, *before;
    insertion_location(tree, &parent, &before);
    node_insert_before(parent, node, before);
    /* the one place every HTML <meta> lands, whatever insertion mode routed it here;
       foreign content goes through insert_foreign, where a <meta> declares nothing */
    if (token->atom == TH_TAG_META) {
        record_meta_label(tree, token);
    }
    return node;
}

static th_node *insert_void_element(th_tree *tree, th_token *token) {
    token->self_closing_acknowledged = 1;
    return insert_element(tree, token);
}

const th_meta_label *th_tree_meta_labels(const th_tree *tree, Py_ssize_t *count) {
    *count = tree->meta_label_count;
    return tree->meta_labels;
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

/* Attribute names of the elements merge_attrs looks up by hash, one open-addressed set per tree of
   (element, name atom) keys. The key takes the element's creation sequence, not its address, so the probe
   order depends only on the input. Key 0 marks an empty slot; an element's marker key (atom 0 is never a
   real name) records that its names were loaded. */
struct th_attr_names {
    uint64_t *keys;
    size_t capacity;
    size_t count;
};

/* Elements with at most this many attributes are searched linearly, which is cheaper than hashing. */
enum { TH_MERGE_SCAN_LIMIT = 8 };

static uint64_t attr_name_key(const th_node *node, uint32_t atom) {
    return (((uint64_t)node->seq + 1) << 32) | atom;
}

static size_t attr_name_slot(uint64_t key, size_t capacity) {
    return th_hash_slot(th_fibonacci_hash(th_fibonacci_hash((uint32_t)(key >> 32)) ^ (uint32_t)key), capacity);
}

static void attr_name_place(uint64_t *keys, size_t capacity, uint64_t key) {
    size_t slot = attr_name_slot(key, capacity);
    while (keys[slot] != 0) {
        TH_WORK_STEP();
        slot = (slot + 1) & (capacity - 1);
    }
    keys[slot] = key;
}

static int attr_name_present(const th_attr_names *names, uint64_t key) {
    if (names->count == 0) {
        return 0;
    }
    size_t slot = attr_name_slot(key, names->capacity);
    while (names->keys[slot] != 0) {
        TH_WORK_STEP();
        if (names->keys[slot] == key) {
            return 1;
        }
        slot = (slot + 1) & (names->capacity - 1);
    }
    return 0;
}

static int attr_name_add(th_tree *tree, th_attr_names *names, uint64_t key) {
    if (2 * (names->count + 1) > names->capacity) {
        size_t capacity;
        size_t bytes;
        /* GCOVR_EXCL_BR_START: allocation-size overflow */
        if (!th_grow_cap(2 * (names->count + 1), names->capacity, 16, sizeof(uint64_t), &capacity, &bytes) ||
            bytes > PY_SSIZE_T_MAX) {
            /* GCOVR_EXCL_BR_STOP */
            tree->failed = 1; /* GCOVR_EXCL_LINE: allocation-size overflow */
            return -1;        /* GCOVR_EXCL_LINE: allocation-size overflow */
        }
        uint64_t *keys = arena_alloc(tree, (Py_ssize_t)bytes);
        if (keys == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;      /* GCOVR_EXCL_LINE: allocation failure cannot be forced from a test */
        }
        memset(keys, 0, bytes);
        for (size_t index = 0; index < names->capacity; index++) {
            if (names->keys[index] != 0) {
                attr_name_place(keys, capacity, names->keys[index]);
            }
        }
        names->keys = keys;
        names->capacity = capacity;
    }
    attr_name_place(names->keys, names->capacity, key);
    names->count++;
    return 0;
}

/* The tree's name set with the element's current names loaded. An element is loaded once, on the first merge
   that finds it past the linear limit, so later merges only add the names they insert. */
static th_attr_names *attr_names_for(th_tree *tree, const th_node *node) {
    if (tree->attr_names == NULL) {
        tree->attr_names = arena_alloc(tree, (Py_ssize_t)sizeof(th_attr_names));
        if (tree->attr_names == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;                /* GCOVR_EXCL_LINE: allocation failure cannot be forced from a test */
        }
        *tree->attr_names = (th_attr_names){NULL, 0, 0};
    }
    th_attr_names *names = tree->attr_names;
    const uint64_t loaded = attr_name_key(node, TH_ATTR_UNKNOWN);
    if (!attr_name_present(names, loaded)) {
        if (attr_name_add(tree, names, loaded) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return NULL;                              /* GCOVR_EXCL_LINE: allocation failure */
        }
        for (Py_ssize_t index = 0; index < node->attr_count; index++) {
            const uint64_t key = attr_name_key(node, node->attrs[index].name_atom);
            if (attr_name_add(tree, names, key) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                return NULL;                           /* GCOVR_EXCL_LINE: allocation failure */
            }
        }
    }
    return names;
}

static int merge_name_present(const th_attr_names *names, const th_node *node, uint32_t atom) {
    if (names != NULL) {
        return attr_name_present(names, attr_name_key(node, atom));
    }
    for (Py_ssize_t inner_index = 0; inner_index < node->attr_count; inner_index++) {
        TH_WORK_STEP();
        if (node->attrs[inner_index].name_atom == atom) {
            return 1;
        }
    }
    return 0;
}

/* Merge a start tag's attributes onto an existing element, keeping only the
   ones whose name is not already present (the <html>/<body> re-open rule).
   The fragment root is skipped: the standard discards that html element along
   with anything merged onto it, while parse_fragment() hands it back as the
   caller's context element, whose attributes the markup must not choose. */
static void merge_attrs(th_tree *tree, th_node *node, const th_token *token) {
    if (token->attr_count == 0 || node == tree->fragment_root) {
        return;
    }
    th_attr_names *names = NULL;
    if (node->attr_count > TH_MERGE_SCAN_LIMIT) {
        names = attr_names_for(tree, node);
        if (names == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return;          /* GCOVR_EXCL_LINE: allocation failure cannot be forced from a test */
        }
    }
    Py_ssize_t add = 0;
    for (Py_ssize_t index = 0; index < token->attr_count; index++) {
        uint32_t atom = intern_attr(tree, &token->attrs[index].name);
        if (!merge_name_present(names, node, atom)) {
            add++;
        }
    }
    if (add == 0) {
        return;
    }
    /* Grow geometrically, as node_attr_write does: the arena keeps every superseded array until the tree is freed,
       so an exact-size copy per re-opening tag would retain memory quadratic in the number of such tags. */
    size_t capacity = th_node_attr_capacity(node);
    size_t needed = (size_t)node->attr_count + (size_t)add;
    th_node_attr *merged = node->attrs;
    if (needed > capacity) {
        size_t bytes;
        /* GCOVR_EXCL_BR_START: allocation-size overflow */
        if (!th_grow_cap(needed, capacity, 2, sizeof(th_node_attr), &capacity, &bytes) || bytes > PY_SSIZE_T_MAX) {
            /* GCOVR_EXCL_BR_STOP */
            tree->failed = 1; /* GCOVR_EXCL_LINE: allocation-size overflow */
            return;           /* GCOVR_EXCL_LINE: allocation-size overflow */
        }
        merged = arena_alloc(tree, (Py_ssize_t)bytes);
        if (merged == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return;           /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        }
        if (node->attr_count > 0) { /* an element without attributes has a NULL attrs array */
            TH_WORK_STEPS_ADD(node->attr_count);
            memcpy(merged, node->attrs, (size_t)node->attr_count * sizeof(th_node_attr));
        }
        node->attr_capacity_shift = 0;
        for (size_t slots = capacity; slots > 1; slots >>= 1) {
            node->attr_capacity_shift++;
        }
    }
    Py_ssize_t at = node->attr_count;
    for (Py_ssize_t index = 0; index < token->attr_count; index++) {
        const th_attr *src = &token->attrs[index];
        uint32_t atom = intern_attr(tree, &src->name);
        if (merge_name_present(names, node, atom)) {
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
    /* an index behind the array would describe the attributes from before this merge, so it is dropped */
    node->attr_capacity_shift &= (uint8_t)~TH_ATTR_INDEXED;
    if (names != NULL) {
        for (Py_ssize_t index = node->attr_count; index < at; index++) {
            const uint64_t key = attr_name_key(node, merged[index].name_atom);
            if (attr_name_add(tree, names, key) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                return;                                /* GCOVR_EXCL_LINE: allocation failure */
            }
        }
    }
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

/* The shadowrootmode enumerated state of a <template> start tag: 0 none (absent or an
   invalid value), 1 open, 2 closed. Only open/closed make a declarative shadow root. */
static int shadowrootmode_state(const th_token *token) {
    const th_attr *attr = token_attr(token, "shadowrootmode", 14);
    if (attr == NULL || !attr->has_value) {
        return 0;
    }
    if (buf_iequals_ascii(&attr->value, "open", 4)) {
        return 1;
    }
    if (buf_iequals_ascii(&attr->value, "closed", 6)) {
        return 2;
    }
    return 0;
}

/* Whether an element may host a shadow root (DOM: a valid shadow host name -- one of
   the flow-content elements attachShadow accepts, or a custom element name, which the
   parser lowercases so an interior hyphen is the whole remaining test). host_atom is
   passed separately so the fragment root can be tested under its context element's
   atom rather than the html it stands in for. */
static int is_valid_shadow_host(const th_node *host, uint16_t host_atom) {
    if (host->ns != TH_NS_HTML) {
        return 0;
    }
    static const uint16_t hostable[] = {
        TH_TAG_ARTICLE, TH_TAG_ASIDE, TH_TAG_BLOCKQUOTE, TH_TAG_BODY, TH_TAG_DIV,     TH_TAG_FOOTER,
        TH_TAG_H1,      TH_TAG_H2,    TH_TAG_H3,         TH_TAG_H4,   TH_TAG_H5,      TH_TAG_H6,
        TH_TAG_HEADER,  TH_TAG_MAIN,  TH_TAG_NAV,        TH_TAG_P,    TH_TAG_SECTION, TH_TAG_SPAN,
    };
    for (size_t index = 0; index < sizeof(hostable) / sizeof(hostable[0]); index++) {
        if (host_atom == hostable[index]) {
            return 1;
        }
    }
    if (host_atom != TH_TAG_UNKNOWN) {
        return 0;
    }
    /* a valid custom element name has an interior hyphen; the parser has already
       lowercased the tag name, so a hyphen past the first byte is the whole test */
    for (Py_ssize_t index = 1; index < host->text_len; index++) {
        if (host->text[index] == '-') {
            return 1;
        }
    }
    return 0;
}

/* The host a declarative `<template shadowrootmode>` attaches its shadow root to, per
   the tree-construction steps: the tree must allow declarative shadow roots, the token
   must carry an open/closed shadowrootmode, and the adjusted current node must be a
   shadow-hostable element other than the topmost open element that does not already host
   a shadow root. Sets *closed from the mode; returns NULL to insert a normal template. */
static th_node *declarative_shadow_host(th_tree *tree, const th_token *token, int *closed) {
    if (!tree->declarative_shadow) {
        return NULL;
    }
    int state = shadowrootmode_state(token);
    if (state == 0) {
        return NULL;
    }
    /* The adjusted current node is the host: the context element in the single-element
       fragment case (the root only stands in for it, so test it under the context atom),
       otherwise the current node. The spec's "not the topmost element" guard needs no
       separate test here -- a document's topmost open element is always <html>, which
       is not a valid shadow host, and the fragment's topmost is handled above. */
    th_node *host = current_node(tree);
    uint16_t host_atom = host->atom;
    if (tree->fragment_root != NULL && tree->open_len == 1) {
        host = tree->fragment_root;
        host_atom = tree->ctx_atom;
    }
    if (!is_valid_shadow_host(host, host_atom) || th_element_shadow_root(tree, host) != NULL) {
        return NULL;
    }
    *closed = state == 2;
    return host;
}

/* Build the template element off the light tree (only on the stack of open elements),
   attach a shadow root to host carrying the shadowrootdelegatesfocus / shadowrootclonable
   flags, and redirect the template's content into that shadow root so subsequent
   insertions populate the shadow tree. tree->sink on allocation failure. */
static th_node *insert_declarative_template(th_tree *tree, const th_token *token, th_node *host, int closed) {
    th_node *tmpl = build_element(tree, token);
    if (tmpl == NULL) {    /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return tree->sink; /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    th_node *shadow = th_element_attach_shadow(tree, host, closed);
    if (shadow == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return tree->sink; /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    if (token_attr(token, "shadowrootdelegatesfocus", 24) != NULL) {
        shadow->tag_flags |= TH_SHADOW_DELEGATES_FOCUS;
    }
    if (token_attr(token, "shadowrootclonable", 18) != NULL) {
        shadow->tag_flags |= TH_SHADOW_CLONABLE;
    }
    /* the shadow root is the template's content: content parses straight into it, while
       it stays parentless (off the light tree) so the flattened-tree walks still root
       every shadow node at the shadow root rather than the discarded template */
    tmpl->first_child = shadow;
    tmpl->last_child = shadow;
    return tmpl;
}

/* Insert a <template> element. A declarative `<template shadowrootmode>` attaches a
   shadow root to its parent; every other template gets a content document-fragment
   child that subsequent insertions are redirected into. */
static th_node *insert_template(th_tree *tree, const th_token *token) {
    int closed = 0;
    th_node *host = declarative_shadow_host(tree, token, &closed);
    if (host != NULL) {
        return insert_declarative_template(tree, token, host, closed);
    }
    th_node *node = insert_element(tree, token);
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
        Py_UCS4 *prev_text = need_text(tree, prev);
        if (prev_text == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return;              /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        /* The capacity lives on the node, not in one slot for the tree: text can alternate between two open text
           nodes (in a table, whitespace stays inside while other text is foster-parented before it), and a node that
           forgot its capacity would copy its whole payload into a fresh arena buffer on every append. */
        size_t capacity = prev->text_capacity_shift ? (size_t)1 << prev->text_capacity_shift : 0;
        if ((size_t)len > SIZE_MAX - (size_t)prev->text_len) { /* GCOVR_EXCL_BR_LINE: allocation-size overflow */
            tree->failed = 1;                                  /* GCOVR_EXCL_LINE: allocation-size overflow */
            return;                                            /* GCOVR_EXCL_LINE: allocation-size overflow */
        }
        size_t needed = (size_t)prev->text_len + (size_t)len;
        if (needed > capacity) {
            size_t bytes;
            /* GCOVR_EXCL_BR_START: allocation-size overflow */
            if (!th_grow_cap(needed, capacity, 16, sizeof(Py_UCS4), &capacity, &bytes) || bytes > PY_SSIZE_T_MAX) {
                /* GCOVR_EXCL_BR_STOP */
                tree->failed = 1; /* GCOVR_EXCL_LINE: allocation-size overflow */
                return;           /* GCOVR_EXCL_LINE: allocation-size overflow */
            }
            Py_UCS4 *merged = arena_alloc(tree, (Py_ssize_t)bytes);
            if (merged == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                return;           /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            TH_WORK_STEPS_ADD(prev->text_len);
            memcpy(merged, prev_text, (size_t)prev->text_len * sizeof(Py_UCS4));
            prev->text = merged;
            prev->text_capacity_shift = 0;
            for (size_t slots = capacity; slots > 1; slots >>= 1) { /* th_grow_cap doubles, so a power of two */
                prev->text_capacity_shift++;
            }
        }
        memcpy(prev->text + prev->text_len, text, (size_t)len * sizeof(Py_UCS4));
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
   guarantees the run has no NUL and the input outlives
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
    text_set_span(node, off);
    node->text_len = len;
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

static void insert_pi(th_tree *tree, const th_token *token, th_node *parent) {
    th_node *node = node_new(tree, TH_NODE_PI);
    if (node == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return;         /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    node->text_len = token->name.len + 1 + token->text.len;
    node->text = arena_alloc(tree, node->text_len * (Py_ssize_t)sizeof(Py_UCS4));
    if (node->text == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return;               /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    buf_write_ucs4(node->text, &token->name);
    node->text[token->name.len] = ' ';
    buf_write_ucs4(node->text + token->name.len + 1, &token->text);
    node->attr_count = token->name.len;
    if (parent != NULL) {
        node_append(parent, node);
        return;
    }
    th_node *target, *before;
    insertion_location(tree, &target, &before);
    node_insert_before(target, node, before);
}

static void insert_comment_or_pi(th_tree *tree, const th_token *token, th_node *parent) {
    if (token->kind == TH_PI) {
        insert_pi(tree, token, parent);
    } else {
        insert_comment(tree, token, parent);
    }
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
    /* an allocation failure set tree->failed, which ends the drain after this token; until then every mode sees an
       empty run instead of reading through the NULL copy */
    if (out == NULL) { /* GCOVR_EXCL_BR_LINE: a text token's run is never empty, so NULL means allocation failure */
        *out_len = 0;  /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;   /* GCOVR_EXCL_LINE: allocation-failure path */
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

static th_node *node_clone(th_tree *tree, th_node *src, int with_attrs) {
    th_node *node = node_new(tree, TH_NODE_ELEMENT);
    if (node == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    node->atom = src->atom;
    /* a clone never has a source start tag of its own, and any end tag that closes it is recorded when it pops */
    node->tag_flags = (uint8_t)((src->tag_flags & ~TH_ELEM_CLOSED_BY_END_TAG) | TH_ELEM_IMPLIED);
    node->text = src->text;
    node->text_len = src->text_len;
    if (with_attrs && src->attr_count > 0) {
        /* Mutations and sanitization can rewrite a reconstructed element's attribute slots. */
        node->attrs = arena_alloc(tree, src->attr_count * (Py_ssize_t)sizeof(th_node_attr));
        if (node->attrs == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return NULL;           /* GCOVR_EXCL_LINE */
        }
        memcpy(node->attrs, src->attrs, (size_t)src->attr_count * sizeof(th_node_attr));
        /* the slots are the clone's own, the values they point at are not */
        th_node_share_values(src);
        node->attr_values_shared = 1;
        node->values_seq = src->values_seq;
        node->attr_count = src->attr_count;
    }
    if (tree->track_positions) {
        /* an adoption-agency clone stands in for the same source start tag, so it
           keeps the original's position */
        const uint32_t *src_pos = node_pos(src);
        node_pos(node)[0] = src_pos[0];
        node_pos(node)[1] = src_pos[1];
    }
    return node;
}

/* Reconstructing the active formatting elements and the adoption agency re-create an element the document wrote
   once, and each clone owns a copy of its source's attribute slots. A few bytes of input buy a clone (text after the
   </p> that closed a <b>), so a <b> with k attributes ahead of k paragraphs would copy k^2 slots. A clone is itself a
   node, so one with at most TH_CLONE_ATTRS_PER_NODE attributes costs a constant per node and is copied whole without
   being counted; that keeps the common clone to one compare. Larger copies are held to a flat allowance plus that
   share per node the tree has made. Past the bound the clone still takes its place in the tree, the stack and the
   list, keeping the structure the standard builds, but it carries no attributes, and neither do the clones later made
   from it. */
#define TH_CLONE_ATTR_ALLOWANCE 65536
#define TH_CLONE_ATTRS_PER_NODE 16

static TH_NOINLINE th_node *clone_formatting_counted(th_tree *tree, th_node *src) {
    uint64_t copied = tree->clone_attr_slots + (uint64_t)src->attr_count;
    uint64_t bound = TH_CLONE_ATTR_ALLOWANCE + TH_CLONE_ATTRS_PER_NODE * ((uint64_t)tree->node_seq + 1);
    int with_attrs = copied <= bound && copied <= UINT32_MAX; /* GCOVR_EXCL_BR_LINE: no test copies 2^32 slots */
    if (with_attrs) {
        tree->clone_attr_slots = (uint32_t)copied;
    }
    return node_clone(tree, src, with_attrs);
}

static inline th_node *clone_formatting(th_tree *tree, th_node *src) {
    if (TH_UNLIKELY(src->attr_count > TH_CLONE_ATTRS_PER_NODE)) {
        return clone_formatting_counted(tree, src);
    }
    return node_clone(tree, src, 1);
}

/* Does the element carry an attribute with this interned name? */
static int node_has_attr(const th_node *node, uint32_t atom) {
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        TH_WORK_STEP();
        if (node->attrs[index].name_atom == atom) {
            return 1;
        }
    }
    return 0;
}

static int is_html_element(const th_node *node, uint16_t atom) {
    return node->type == TH_NODE_ELEMENT && node->ns == TH_NS_HTML && node->atom == atom;
}

/* An option is disabled by its own disabled attribute or by sitting directly in a
   disabled optgroup ("a disabled option"), so "reset a select's selectedness" skips it.
   The options of one optgroup close in turn and share it as their parent, and the parser
   never changes an open optgroup's attributes, so the select caches the last answer
   instead of searching the attributes once per option. */
static int option_is_disabled(th_select_cache *cache, const th_node *option) {
    if (node_has_attr(option, TH_ATTR_DISABLED)) {
        return 1;
    }
    th_node *parent = option->parent;
    if (!is_html_element(parent, TH_TAG_OPTGROUP)) {
        return 0;
    }
    if (cache->optgroup != parent) {
        cache->optgroup = parent;
        cache->optgroup_disabled = (uint8_t)node_has_attr(parent, TH_ATTR_DISABLED);
    }
    return cache->optgroup_disabled;
}

/* An HTML select, option, or selectedcontent element: a selectedcontent below one
   of these, other than its nearest select, is disabled. */
static int disables_selectedcontent(const th_node *node) {
    if (node->type != TH_NODE_ELEMENT || node->ns != TH_NS_HTML) {
        return 0;
    }
    switch (node->atom) {
    case TH_TAG_SELECT:
    case TH_TAG_OPTION:
    case TH_TAG_SELECTEDCONTENT:
        return 1;
    default:
        return 0;
    }
}

static int select_disabled_by_ancestor(const th_node *select) {
    for (const th_node *ancestor = select->parent; ancestor != NULL; ancestor = ancestor->parent) {
        if (disables_selectedcontent(ancestor)) {
            return 1;
        }
    }
    return 0;
}

/* The select's target walk does not descend into these, so a selectedcontent beneath one is not reachable. */
static int reach_stops_at(const th_node *node) {
    return node->type == TH_NODE_CONTENT || disables_selectedcontent(node);
}

/* Marks node and its ancestors up to the next reach_stops_at boundary, for a flagged child just attached under node. */
void th_node_mark_ancestors(th_node *node) {
    while (node != NULL && !node->reaches_selectedcontent && !reach_stops_at(node)) {
        TH_WORK_STEP();
        node->reaches_selectedcontent = 1;
        node = node->parent;
    }
}

/* An enabled selectedcontent is flagged once the parser has created it, when its namespace is final. */
static void selectedcontent_flagged(th_node *node) {
    if (!node_has_attr(node, TH_ATTR_DISABLED)) {
        node->reaches_selectedcontent = 1;
        th_node_mark_ancestors(node->parent);
    }
}

/* The selectedcontent elements one select mirrors its shown option into. The HTML
   Standard mirrors it into every enabled one, and a valid select holds one, in its
   button. Uncapped, N of them under one select copy an option of M nodes N times, so
   the tree grows as N x M from N + M of input. A target past the cap keeps the content
   the source gave it, as a disabled one does. */
#define MAX_CLONE_TARGETS 16

/* Record one clone destination, growing the per-select buffer as needed. */
static void select_cache_add_target(th_tree *tree, th_select_cache *cache, th_node *target) {
    if (cache->target_count == MAX_CLONE_TARGETS) {
        return;
    }
    if (cache->target_count == cache->target_cap) {
        size_t cap;
        size_t bytes;
        int grew =
            th_grow_cap((size_t)cache->target_count + 1, (size_t)cache->target_cap, 4, sizeof(th_node *), &cap, &bytes);
        if (!grew) {          /* GCOVR_EXCL_BR_LINE: size overflow cannot be forced from a test */
            tree->failed = 1; /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
            return;           /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
        }
        th_node **grown = PyMem_Realloc(cache->targets, bytes);
        if (grown == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            tree->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
            return;           /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        }
        cache->targets = grown;
        cache->target_cap = (Py_ssize_t)cap;
    }
    cache->targets[cache->target_count++] = target;
}

static th_node *option_nearest_select(th_node *option);

/* Collect the select's enabled descendant selectedcontent elements ("update a select's
   descendant selectedcontent elements"): every selectedcontent whose nearest ancestor
   select is this one (not nested under an inner select/option/selectedcontent) and that
   carries no disabled attribute. The same walk returns the option the select shows, as
   the selectedness setting algorithm picks it from the options so far: the last selected
   one, else the first enabled one. */
static th_node *select_cache_fill(th_tree *tree, th_select_cache *cache) {
    th_node *root = cache->select;
    th_node *first_enabled = NULL;
    th_node *last_selected = NULL;
    th_node *node = root->first_child;
    while (node != NULL) {
        TH_WORK_STEP();
        if (disables_selectedcontent(node)) {
            if (node->atom == TH_TAG_SELECTEDCONTENT) {
                if (!node_has_attr(node, TH_ATTR_DISABLED)) {
                    select_cache_add_target(tree, cache, node);
                }
            } else if (node->atom == TH_TAG_OPTION && (node->parent == root || option_nearest_select(node) == root)) {
                if (node_has_attr(node, TH_ATTR_SELECTED)) {
                    last_selected = node;
                } else if (first_enabled == NULL && !option_is_disabled(cache, node)) {
                    first_enabled = node;
                }
            }
        } else if (node->type != TH_NODE_CONTENT && node->first_child != NULL) {
            /* a template's content fragment holds its own selectedcontent, not the select's */
            node = node->first_child;
            continue;
        }
        while (node != root && node->next_sibling == NULL) {
            TH_WORK_STEP();
            node = node->parent;
        }
        node = node == root ? NULL : node->next_sibling;
    }
    cache->disabled = (uint8_t)select_disabled_by_ancestor(root);
    return last_selected != NULL ? last_selected : first_enabled;
}

/* The select's position in the caches, 0 when it has no cache. */
static Py_ssize_t select_cache_slot(th_select_state *state, const th_node *select) {
    if (select != state->last_select) {
        state->last_select = select;
        /* a select created after the newest cache has none, so only an older one needs the hash probe */
        state->last_slot = select->seq >= state->fresh_seq ? 0 : th_node_map_find(&state->index, select);
    }
    return state->last_slot;
}

/* Creates the select's cache when it gains its first selectedcontent, lists its enabled selectedcontent elements and
   reads the option it shows from the options it holds so far. */
static th_select_cache *select_cache_insert(th_tree *tree, th_node *select) {
    if (tree->select_state == NULL) {
        tree->select_state = PyMem_Calloc(1, sizeof(th_select_state));
        if (tree->select_state == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            tree->failed = 1;             /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
            return NULL;                  /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        }
    }
    th_select_state *state = tree->select_state;
    if (state->count == state->cap) {
        size_t cap;
        size_t bytes;
        int grew = th_grow_cap((size_t)state->count + 1, (size_t)state->cap, 8, sizeof(th_select_cache), &cap, &bytes);
        if (!grew) {          /* GCOVR_EXCL_BR_LINE: size overflow cannot be forced from a test */
            tree->failed = 1; /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
            return NULL;      /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
        }
        th_select_cache *grown = PyMem_Realloc(state->caches, bytes);
        if (grown == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            tree->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
            return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        }
        state->caches = grown;
        state->cap = (Py_ssize_t)cap;
    }
    /* counted only once indexed: a failed insert must leave no uninitialized cache for select_cache_free */
    Py_ssize_t slot = state->count + 1;
    if (th_node_map_insert(&state->index, select, slot) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
        tree->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    state->count = slot;
    state->fresh_seq = tree->node_seq;
    state->last_select = select;
    state->last_slot = slot;
    th_select_cache *cache = &state->caches[slot - 1];
    cache->select = select;
    cache->targets = NULL;
    cache->target_count = 0;
    cache->target_cap = 0;
    cache->filled = 0;
    cache->optgroup = NULL;
    cache->optgroup_disabled = 0;
    cache->multiple = (uint8_t)node_has_attr(select, TH_ATTR_MULTIPLE);
    cache->selected = select_cache_fill(tree, cache);
    return cache;
}

/* The select whose target walk reaches node: the nearest boundary at or above it, when that boundary is a select. */
static th_node *select_reaching(th_node *node) {
    for (; node != NULL; node = node->parent) {
        TH_WORK_STEP();
        if (reach_stops_at(node)) {
            return is_html_element(node, TH_TAG_SELECT) ? node : NULL;
        }
    }
    return NULL;
}

/* Appends the targets inside a block that reached the select, in document order, up to the cap. A flagged node
   is the only kind that can hold a target, so the walk skips every other subtree. */
static void select_cache_collect(th_tree *tree, th_select_cache *cache, th_node *block) {
    th_node *node = block->first_child;
    while (node != NULL && cache->target_count < MAX_CLONE_TARGETS) {
        TH_WORK_STEP();
        if (node->reaches_selectedcontent && is_html_element(node, TH_TAG_SELECTEDCONTENT)) {
            select_cache_add_target(tree, cache, node);
        } else {
            th_node *down = node->reaches_selectedcontent ? node->first_child : NULL;
            if (down != NULL) {
                node = down;
                continue;
            }
        }
        while (node != block && node->next_sibling == NULL) {
            TH_WORK_STEP();
            node = node->parent;
        }
        node = node == block ? NULL : node->next_sibling;
    }
}

/* A flagged block the adoption agency moved into the reach of a select. A block that reached a select before the
   move still reaches it, and its targets are already listed. Only a block that a gate removed from the open
   elements was out of reach before, so only that case is handled here. */
static void select_cache_block_gained(th_tree *tree, th_node *block) {
    th_node *select = select_reaching(block);
    if (select == NULL) {
        return;
    }
    th_select_state *state = tree->select_state;
    Py_ssize_t slot = state != NULL ? select_cache_slot(state, select) : 0;
    if (slot == 0) {
        select_cache_insert(tree, select);
        return;
    }
    select_cache_collect(tree, &state->caches[slot - 1], block);
}

static void select_cache_free(th_tree *tree) {
    th_select_state *state = tree->select_state;
    if (state != NULL) {
        for (Py_ssize_t index = 0; index < state->count; index++) {
            PyMem_Free(state->caches[index].targets);
        }
        PyMem_Free(state->caches);
        PyMem_Free(state->index.entries);
        PyMem_Free(state);
        tree->select_state = NULL;
    }
}

static th_node *copy_node_in_tree(th_tree *tree, th_node *source) {
    if (source->type == TH_NODE_ELEMENT) {
        th_node *copy = node_clone(tree, source, 1);
        if (copy != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
            copy->ns = source->ns;
        }
        return copy;
    }
    th_node *copy = node_new(tree, source->type);
    if (copy != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
        copy->text = need_text(tree, source);
        copy->text_len = source->text_len;
        source->text_capacity_shift = 0;    /* the buffer is shared now, so neither node may write into it */
        if (source->type != TH_NODE_TEXT) { /* compact text nodes omit attr_count */
            copy->attr_count = source->attr_count;
        }
    }
    return copy;
}

/* Deep-copy src's children into dst without consuming one C stack frame per level. */
static void deep_copy_children(th_tree *tree, const th_node *src, th_node *dst) {
    tree->nest_bound = 0; /* the copy can sit deeper than the source the stack measured */
    const th_node *from = src;
    th_node *copy = dst;
    for (;;) {
        th_node *parent;
        if (from->first_child != NULL) {
            from = from->first_child;
            parent = copy;
        } else {
            while (from != src && from->next_sibling == NULL) {
                from = from->parent;
                copy = copy->parent;
            }
            if (from == src) {
                return;
            }
            from = from->next_sibling;
            parent = copy->parent;
        }
        /* one copy site, so the copy inlines into the walk */
        copy = copy_node_in_tree(tree, (th_node *)from);
        if (copy == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return;         /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        }
        node_append(parent, copy);
    }
}

/* The HTML Standard's "nearest ancestor select" for element: walking ancestors
   nearest first, a datalist, hr, or option returns null, a second optgroup returns
   null, and the first select is the answer. An option nested in another option (or
   below a datalist/hr) therefore has no select and is never cloned, so a chain of
   nested options stays linear instead of each level recopying the one below. */
static th_node *option_nearest_select(th_node *option) {
    th_node *ancestor_optgroup = NULL;
    for (th_node *ancestor = option->parent; ancestor != NULL; ancestor = ancestor->parent) {
        if (ancestor->type == TH_NODE_CONTENT) {
            /* a template's content (or a shadow root) is a separate document fragment, so
               an option inside it has no nearest ancestor select in this tree */
            return NULL;
        }
        if (ancestor->type != TH_NODE_ELEMENT || ancestor->ns != TH_NS_HTML) {
            continue;
        }
        switch (ancestor->atom) {
        case TH_TAG_DATALIST:
        case TH_TAG_HR:
        case TH_TAG_OPTION:
            return NULL;
        case TH_TAG_OPTGROUP:
            if (ancestor_optgroup != NULL) {
                return NULL;
            }
            ancestor_optgroup = ancestor;
            break;
        case TH_TAG_SELECT:
            return ancestor;
        default:
            break;
        }
    }
    return NULL;
}

/* The option the select shows, or NULL once a clone into a selectedcontent removed it
   (an option written inside the target) or the adoption agency moved it out. */
static th_node *select_cache_shown_option(const th_select_cache *cache) {
    th_node *option = cache->selected;
    return option != NULL && option_nearest_select(option) == cache->select ? option : NULL;
}

/* Removes the options of select that a copy carries into target. The copy lifts them out of the option they were
   nested in, which kept them off the select's list; reparsing the output would pop them inside the target and let
   them replace its content, so the target keeps the rest of the copy without them. */
static void target_drop_options(th_node *select, th_node *target) {
    th_node *node = target->first_child;
    while (node != NULL) {
        TH_WORK_STEP();
        int option = is_html_element(node, TH_TAG_OPTION);
        if (!option && node->first_child != NULL) {
            node = node->first_child;
            continue;
        }
        th_node *dropped = option && option_nearest_select(node) == select ? node : NULL;
        while (node != target && node->next_sibling == NULL) {
            TH_WORK_STEP();
            node = node->parent;
        }
        node = node == target ? NULL : node->next_sibling;
        if (dropped != NULL) {
            node_remove(dropped);
        }
    }
}

/* Replaces the children of the unfilled targets with a copy of option. Children the parser gave a target are
   dropped, not kept beside the copy: browsers keep them, but then reparsing the output changes the tree, so this
   departs from browser output on purpose. */
static void replace_target_children(th_tree *tree, th_select_cache *cache, th_node *option) {
    for (Py_ssize_t index = cache->filled; index < cache->target_count; index++) {
        th_node *target = cache->targets[index];
        while (target->first_child != NULL) {
            node_remove(target->first_child);
        }
        deep_copy_children(tree, option, target);
    }
    cache->filled = (uint8_t)cache->target_count;
}

/* Makes option the shown option when the selectedness setting algorithm picks it, and returns its select's cache;
   NULL when it is not picked. Only a select that holds a selectedcontent has a cache: the rest have nothing to copy
   into, and a cache made later reads the shown option from the tree, so callers skip the call while no select has
   one. */
static th_select_cache *select_cache_pick(th_select_state *state, th_node *option) {
    /* an option whose parent is the select looked up last belongs to it, without the ancestor walk */
    th_node *select = option->parent;
    if (select != state->last_select) {
        select = option_nearest_select(option);
        if (select == NULL) {
            return NULL;
        }
    }
    Py_ssize_t slot = select_cache_slot(state, select);
    if (slot == 0) {
        return NULL;
    }
    th_select_cache *cache = &state->caches[slot - 1];
    if (cache->multiple) {
        return NULL;
    }
    /* the selectedness setting algorithm: the last selected option wins, and the first
       enabled one is selected only while no option is */
    if (!node_has_attr(option, TH_ATTR_SELECTED) &&
        (option_is_disabled(cache, option) || select_cache_shown_option(cache) != NULL)) {
        return NULL;
    }
    cache->selected = option;
    cache->filled = 0;
    return cache;
}

/* "Update descendant selectedcontent elements for an option": when the option the
   select shows is popped, its children are mirrored into the select's enabled
   selectedcontent elements. The copy waits for select_cache_finish, which makes it once
   for the option shown last, unless the option sits inside a selectedcontent: there the
   copy removes it at once and the parser appends what follows to the copy. */
static void maybe_clone_option(th_tree *tree, th_node *option) {
    if (tree->select_state == NULL) {
        return;
    }
    th_select_cache *cache = select_cache_pick(tree->select_state, option);
    if (cache == NULL || cache->disabled) {
        return;
    }
    for (const th_node *ancestor = option->parent; ancestor != cache->select; ancestor = ancestor->parent) {
        TH_WORK_STEP();
        if (is_html_element(ancestor, TH_TAG_SELECTEDCONTENT)) {
            replace_target_children(tree, cache, option);
            /* the copy removed it, so a reparse never lists it and picks from the options left; the finish does the
               same */
            if (option_nearest_select(option) != cache->select) {
                tree->select_state->recheck = 1;
            }
            return;
        }
    }
}

/* The selectedcontent post-connection steps: a selectedcontent inserted under a select
   it is enabled for joins the select's targets, while the cap leaves room. It stays
   pending until select_cache_finish, which runs after the parser has appended the
   element's own children and replaces them. A browser keeps those children beside the
   copy; dropping them keeps reparsed output unchanged. A select with no cache yet builds
   one here, which lists the new element with the rest. The walk stops at an ancestor
   option, so a chain of nested options stays O(1) each. */
static void selectedcontent_inserted(th_tree *tree, th_node *selectedcontent) {
    /* selectedcontent_flagged set the flag exactly when the element carries no disabled attribute */
    if (!selectedcontent->reaches_selectedcontent) {
        return;
    }
    th_node *select = selectedcontent->parent;
    while (select != NULL && !is_html_element(select, TH_TAG_SELECT)) {
        /* a template's content fragment holds its own selectedcontent, not the select's */
        if (select->type == TH_NODE_CONTENT || disables_selectedcontent(select)) {
            return;
        }
        select = select->parent;
    }
    if (select == NULL) {
        return;
    }
    th_select_state *state = tree->select_state;
    Py_ssize_t slot = state != NULL ? select_cache_slot(state, select) : 0;
    if (slot == 0) {
        select_cache_insert(tree, select);
        return;
    }
    th_select_cache *cache = &state->caches[slot - 1];
    if (cache->target_count == MAX_CLONE_TARGETS) {
        return;
    }
    select_cache_add_target(tree, cache, selectedcontent);
}

/* The option the select shows as a parse of its current subtree would pick it: the last selected one, else the
   first enabled one. Only the select's own options count, as in option_nearest_select. */
static th_node *select_scan_option(th_select_cache *cache) {
    th_node *root = cache->select;
    th_node *first_enabled = NULL;
    th_node *last_selected = NULL;
    th_node *node = root->first_child;
    while (node != NULL) {
        TH_WORK_STEP();
        if (is_html_element(node, TH_TAG_OPTION)) {
            if (option_nearest_select(node) == root) {
                if (node_has_attr(node, TH_ATTR_SELECTED)) {
                    last_selected = node;
                } else if (first_enabled == NULL && !option_is_disabled(cache, node)) {
                    first_enabled = node;
                }
            }
        } else if (node->type != TH_NODE_CONTENT && !is_html_element(node, TH_TAG_SELECT) &&
                   node->first_child != NULL) {
            node = node->first_child;
            continue;
        }
        while (node != root && node->next_sibling == NULL) {
            TH_WORK_STEP();
            node = node->parent;
        }
        node = node == root ? NULL : node->next_sibling;
    }
    return last_selected != NULL ? last_selected : first_enabled;
}

/* Whether option sits inside one of the select's targets, where a copy into that target removes it. */
static int option_in_target(const th_select_cache *cache, const th_node *option) {
    for (const th_node *ancestor = option->parent; ancestor != cache->select; ancestor = ancestor->parent) {
        TH_WORK_STEP();
        if (is_html_element(ancestor, TH_TAG_SELECTEDCONTENT)) {
            for (Py_ssize_t index = 0; index < cache->target_count; index++) {
                if (cache->targets[index] == ancestor) {
                    return 1;
                }
            }
        }
    }
    return 0;
}

/* Runs once the parse has ended and gives each pending target the copy of the shown option. After the adoption
   agency moved nodes or an option removed itself with its copy, the shown option is read from the tree again, as a
   reparse of the output picks it. A pick inside a target removes itself and every option the targets held, and its
   copy leaves out the options nested in it, so the second read finds an option outside the targets, or none. */
static void select_cache_finish(th_tree *tree) {
    th_select_state *state = tree->select_state;
    if (state == NULL) {
        return;
    }
    /* read once: the copies below allocate nodes, so the compiler would reload these after every one */
    const uint8_t recheck = state->recheck;
    const th_select_cache *end = state->caches + state->count;
    for (th_select_cache *cache = state->caches; cache != end; cache++) {
        if (cache->disabled || cache->multiple) {
            continue;
        }
        /* only the adoption agency and a copy that removed its option take the shown option out of its select, and
           both ask for the recheck */
        if (!recheck) {
            if (cache->selected != NULL && cache->filled != cache->target_count) {
                replace_target_children(tree, cache, cache->selected);
            }
            continue;
        }
        for (;;) {
            th_node *option = select_scan_option(cache);
            if (option == NULL) {
                break;
            }
            /* an option inside a target is copied even when it was shown already: the adoption agency can bring it
               back after its copy removed it */
            int in_target = option_in_target(cache, option);
            if (option != cache->selected || in_target) {
                cache->selected = option;
                cache->filled = 0;
            }
            if (cache->filled == cache->target_count) {
                break;
            }
            replace_target_children(tree, cache, option);
            if (!in_target) {
                break;
            }
            for (Py_ssize_t target = 0; target < cache->target_count; target++) {
                target_drop_options(cache->select, cache->targets[target]);
            }
        }
    }
}

/* The afe_hash() of each list entry, cached in the same allocation right after the entries so th_tree keeps its size:
   0 for an entry pushed while the hash set was unused, never read for a marker. */
static inline uint64_t *afe_keys(const th_tree *tree) {
    return (uint64_t *)(tree->afe + tree->afe_cap);
}

/* Remove the active-formatting-elements entry at index, shifting the tail down. */
static void afe_remove_at(th_tree *tree, Py_ssize_t index) {
    tree->afe[index]->afe_listed = 0;
    size_t tail = (size_t)(tree->afe_len - index - 1);
    if (tail > 0) {
        memmove(&tree->afe[index], &tree->afe[index + 1], tail * sizeof(th_node *));
        uint64_t *keys = afe_keys(tree);
        memmove(&keys[index], &keys[index + 1], tail * sizeof(uint64_t));
    }
    tree->afe_len--;
    if (tree->afe_len == 0) {
        tree->afe_hash_valid = 0;
    }
}

/* Remove the open-elements-stack entry at index, shifting the tail down. */
/* No template is removed here: the callers remove a formatting element, a form, an a, or the elements between a
   formatting element and its furthest block, which hold no special element and so no template. */
static void stack_remove_at(th_tree *tree, Py_ssize_t index) {
    tree->open[index]->left_stack = 1;
    Py_ssize_t old_len = tree->open_len;
    memmove(&tree->open[index], &tree->open[index + 1], (size_t)(tree->open_len - index - 1) * sizeof(th_node *));
    tree->open_len--;
    blocks_recount(tree, index, old_len);
    tree->stack_version++;
}

static uint64_t afe_hash(const th_node *node) {
    uint64_t hash = UINT64_C(14695981039346656037) ^ node->atom;
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        const th_node_attr *attr = &node->attrs[index];
        TH_WORK_STEPS_ADD(attr->value_len);
        hash = (hash ^ attr->name_atom) * UINT64_C(1099511628211);
        hash = (hash ^ (uint64_t)attr->value_len) * UINT64_C(1099511628211);
        for (Py_ssize_t offset = 0; offset < attr->value_len; offset++) {
            hash = (hash ^ attr->value[offset]) * UINT64_C(1099511628211);
        }
    }
    return hash | 1;
}

/* Below this many entries after the last marker a push compares against them directly and leaves the hash set unused:
   hashing every pushed element costs more than scanning a short segment, and few documents list more. */
#define AFE_SCAN_LIMIT 8
#define AFE_HASH_MIN_CAPACITY 32

/* Whether at least AFE_SCAN_LIMIT entries follow the last marker. */
static inline int afe_segment_is_long(const th_tree *tree) {
    if (tree->afe_len < AFE_SCAN_LIMIT) {
        return 0;
    }
    for (Py_ssize_t index = tree->afe_len - AFE_SCAN_LIMIT; index < tree->afe_len; index++) {
        if (tree->afe[index] == NULL) {
            return 0;
        }
    }
    return 1;
}

/* Index of the first entry after the last marker: the only entries the Noah's Ark scan reads. */
static Py_ssize_t afe_segment_start(const th_tree *tree) {
    Py_ssize_t index = tree->afe_len;
    while (index > 0 && tree->afe[index - 1] != NULL) {
        index--;
    }
    return index;
}

static int afe_hash_insert(th_tree *tree, uint64_t key);

/* Refill the set with the keys of the entries from start on, hashing only those pushed while the set was unused. The
   table first shrinks to the smallest size that holds them under a quarter load (its buffer keeps the allocation), so
   a reseed takes time in the length of the segment, not in the largest table the parse has needed. */
static int afe_hash_reseed(th_tree *tree, Py_ssize_t start) {
    size_t live = (size_t)(tree->afe_len - start);
    while (tree->afe_hash_capacity > AFE_HASH_MIN_CAPACITY && tree->afe_hash_capacity / 8 > live) {
        tree->afe_hash_capacity /= 2;
    }
    if (tree->afe_hash_capacity > 0) {
        TH_WORK_STEPS_ADD(tree->afe_hash_capacity);
        memset(tree->afe_hashes, 0, tree->afe_hash_capacity * sizeof(uint64_t));
    }
    tree->afe_hash_count = 0;
    uint64_t *keys = afe_keys(tree);
    for (Py_ssize_t index = start; index < tree->afe_len; index++) {
        if (keys[index] == 0) {
            keys[index] = afe_hash(tree->afe[index]);
        }
        if (afe_hash_insert(tree, keys[index]) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;                                /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    return 0;
}

static int afe_hash_insert(th_tree *tree, uint64_t key) {
    if (tree->afe_hash_count >= tree->afe_hash_capacity / 2) {
        /* An entry that leaves the list keeps its key in the set. Once such keys fill half the table, reseed it from
           the segment instead of growing it, so the table and its probe runs stay bounded by the segment and not by
           every element the list has held. The reseed leaves the count under half the shrunk table, so it grows
           nothing. */
        if (tree->afe_hash_count > 64) {
            Py_ssize_t start = afe_segment_start(tree);
            if (tree->afe_hash_count > 2 * (size_t)(tree->afe_len - start) + 64) {
                if (afe_hash_reseed(tree, start) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                    return -1;                          /* GCOVR_EXCL_LINE: allocation-failure path */
                }
            }
        }
        if (tree->afe_hash_count >= tree->afe_hash_capacity / 2) {
            size_t capacity;
            size_t bytes;
            /* GCOVR_EXCL_BR_START: allocation-size overflow */
            if (!th_grow_cap(tree->afe_hash_capacity + 1, tree->afe_hash_capacity, AFE_HASH_MIN_CAPACITY,
                             sizeof(uint64_t), &capacity, &bytes)) {
                tree->failed = 1; /* GCOVR_EXCL_LINE: allocation-size overflow */
                return -1;        /* GCOVR_EXCL_LINE: allocation-size overflow */
            }
            /* GCOVR_EXCL_BR_STOP */
            uint64_t *hashes = PyMem_Calloc(capacity, sizeof(uint64_t));
            if (hashes == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                tree->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
                return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            for (size_t index = 0; index < tree->afe_hash_capacity; index++) {
                uint64_t hash = tree->afe_hashes[index];
                if (hash != 0) {
                    size_t slot = (size_t)hash & (capacity - 1);
                    while (hashes[slot] != 0) {
                        TH_WORK_STEP();
                        slot = (slot + 1) & (capacity - 1);
                    }
                    hashes[slot] = hash;
                }
            }
            PyMem_Free(tree->afe_hashes);
            tree->afe_hashes = hashes;
            tree->afe_hash_capacity = capacity;
        }
    }
    size_t slot = (size_t)key & (tree->afe_hash_capacity - 1);
    while (tree->afe_hashes[slot] != 0) {
        TH_WORK_STEP();
        if (tree->afe_hashes[slot] == key) {
            return 1;
        }
        slot = (slot + 1) & (tree->afe_hash_capacity - 1);
    }
    tree->afe_hashes[slot] = key;
    tree->afe_hash_count++;
    return 0;
}

/* Make room for one more entry in a full list, moving the cached keys up behind the larger entry array. */
static int afe_grow(th_tree *tree) {
    size_t full = (size_t)tree->afe_cap;
    size_t cap;
    size_t bytes;
    int grew = th_grow_cap(full + 1, full, 16, sizeof(th_node *) + sizeof(uint64_t), &cap, &bytes);
    if (!grew) {          /* GCOVR_EXCL_BR_LINE: size overflow needs a length no allocation could hold */
        tree->failed = 1; /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
        return 0;         /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
    }
    th_node **grown = PyMem_Realloc(tree->afe, bytes);
    if (grown == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        tree->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        return 0;         /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    if (full > 0) {
        memmove(grown + cap, grown + full, full * sizeof(uint64_t));
    }
    tree->afe = grown;
    tree->afe_cap = (Py_ssize_t)cap;
    return 1;
}

/* The hash set comes into use (afe_hash_valid) once the segment after the last marker is long, and leaves it when the
   list clears back to a marker or empties. In use, it holds the key of every entry after the last marker, plus keys of
   entries that have left the list or sit before that marker, so a miss proves the segment holds no equal entry and a
   hit runs the exact comparison. */
static int afe_push(th_tree *tree, th_node *node) {
    uint64_t key = 0;
    int seen = 1;
    if (tree->afe_hash_valid || afe_segment_is_long(tree)) {
        if (!tree->afe_hash_valid) {
            if (afe_hash_reseed(tree, afe_segment_start(tree)) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                return 0;                                             /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            tree->afe_hash_valid = 1;
        }
        key = afe_hash(node);
        seen = afe_hash_insert(tree, key);
        if (seen < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return 0;   /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    /* A short segment, or a hash hit, runs the full duplicate comparison. */
    if (seen) {
        /* Noah's Ark: at most three earlier entries with the same name+attributes
           may precede a new one before the oldest is dropped. */
        int matches = 0;
        Py_ssize_t earliest = -1;
        for (Py_ssize_t index = tree->afe_len - 1; index >= 0; index--) {
            TH_WORK_STEP();
            th_node *entry = tree->afe[index];
            if (entry == NULL) {
                break; /* stop at the marker */
            }
            if (entry->atom == node->atom && entry->attr_count == node->attr_count) {
                int same = 1;
                for (Py_ssize_t aidx = 0; aidx < node->attr_count && same; aidx++) {
                    if (entry->attrs[aidx].name_atom != node->attrs[aidx].name_atom ||
                        entry->attrs[aidx].value_len != node->attrs[aidx].value_len ||
                        (node->attrs[aidx].value_len > 0 &&
                         memcmp(entry->attrs[aidx].value, node->attrs[aidx].value,
                                (size_t)node->attrs[aidx].value_len * sizeof(Py_UCS4)) != 0)) {
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
    }
    if (tree->afe_len == tree->afe_cap) {
        if (!afe_grow(tree)) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return 0;          /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        }
    }
    afe_keys(tree)[tree->afe_len] = key;
    tree->afe[tree->afe_len++] = node;
    node->afe_listed = 1;
    return 1;
}

/* A clone at index keeps the key of the entry it stands in for, unless clone_formatting left its attributes off: then
   it hashes apart from its source, and the slot (which a reseed reads) and the set in use (which must hold the key of
   every entry after the last marker) both take its own key. A failed insert has set tree->failed. */
static TH_NOINLINE void afe_rekey(th_tree *tree, Py_ssize_t index) {
    uint64_t key = afe_hash(tree->afe[index]);
    afe_keys(tree)[index] = key;
    if (tree->afe_hash_valid) {
        (void)afe_hash_insert(tree, key);
    }
}

static inline void afe_key_clone(th_tree *tree, Py_ssize_t index, const th_node *source) {
    if (TH_UNLIKELY(tree->afe[index]->attr_count != source->attr_count)) {
        afe_rekey(tree, index);
    }
}

/* Put a clone in the list slot of the entry it stands in for. */
static void afe_replace_at(th_tree *tree, Py_ssize_t index, th_node *clone) {
    th_node *source = tree->afe[index];
    source->afe_listed = 0;
    tree->afe[index] = clone;
    clone->afe_listed = 1;
    afe_key_clone(tree, index, source);
}

static void afe_push_marker(th_tree *tree) {
    if (tree->afe_len == tree->afe_cap) {
        if (!afe_grow(tree)) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return;            /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        }
    }
    tree->afe[tree->afe_len++] = NULL;
}

static void afe_clear_to_marker(th_tree *tree) {
    tree->afe_hash_valid = 0;
    /* afe_clear_to_marker always runs with a marker on the list */
    while (tree->afe_len > 0 /* GCOVR_EXCL_BR_LINE */) {
        th_node *entry = tree->afe[--tree->afe_len];
        if (entry == NULL) {
            return;
        }
        entry->afe_listed = 0;
    }
}

/* The list outlives the stack of open elements, so a search for an element that has left it would read every entry
   the markers left behind. Membership comes from the flag, and the search runs only for a listed element. */
static Py_ssize_t afe_index_of(th_tree *tree, th_node *node) {
    if (!node->afe_listed) {
        return -1;
    }
    Py_ssize_t index = tree->afe_len - 1;
    while (tree->afe[index] != node) {
        TH_WORK_STEP();
        index--;
    }
    return index;
}

static Py_ssize_t stack_index_of(th_tree *tree, th_node *node) {
    for (Py_ssize_t index = tree->open_len - 1; index >= 0; index--) {
        if (tree->open[index] == node) {
            return index;
        }
    }
    return -1;
}

/* Scans down from the top and skips foreign elements, as pop_until_atom does: a foreign template is not the template
   the rules look for. */
static Py_ssize_t stack_index_of_atom(th_tree *tree, uint16_t atom) {
    for (Py_ssize_t index = stack_scan(tree, tree->open_len - 1, SCAN_ATOM, atom); index >= 0;
         index = stack_scan(tree, index - 1, SCAN_ATOM, atom)) {
        if (tree->open[index]->ns == TH_NS_HTML && tree->open[index]->atom == atom) {
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

static int afe_on_stack(th_tree *tree, th_node *node) {
    if (tree->afe_stack_hint >= 0 && tree->afe_stack_hint < tree->open_len &&
        tree->open[tree->afe_stack_hint] == node) {
        return 1;
    }
    tree->afe_stack_hint = stack_index_of(tree, node);
    return tree->afe_stack_hint >= 0;
}

/* The most active formatting elements one reconstruction reopens: the entries nearest the end of the list, the ones
   that apply most closely to what follows. The standard reopens every entry that has left the stack, and nothing
   else bounds the run, so unclosed start tags that differ (Noah's Ark drops only the
   repeats) cost each later block one clone apiece: entries x blocks nodes. Real markup leaves a handful open across
   a block. */
#define TH_MAX_RECONSTRUCTED_FORMATTING 16

/* Reconstruct the active formatting elements (re-open any that fell off the
   stack of open elements) per the spec, up to TH_MAX_RECONSTRUCTED_FORMATTING of them. */
static void reconstruct_afe(th_tree *tree) {
    if (tree->afe_len == 0) {
        return;
    }
    Py_ssize_t index = tree->afe_len - 1;
    if (tree->afe[index] == NULL || afe_on_stack(tree, tree->afe[index])) {
        return;
    }
    while (index > 0) {
        index--;
        if (tree->afe[index] == NULL || afe_on_stack(tree, tree->afe[index])) {
            index++;
            break;
        }
    }
    Py_ssize_t excess = tree->afe_len - index - TH_MAX_RECONSTRUCTED_FORMATTING;
    if (excess > 0) {
        /* An entry passed over but left listed would be reopened by a later pass, and the list would outgrow the
           stack, so the outermost ones leave the list. Every entry from index on is off the stack, so no open
           element loses its entry. */
        for (Py_ssize_t dropped = index; dropped < index + excess; dropped++) {
            tree->afe[dropped]->afe_listed = 0;
        }
        memmove(&tree->afe[index], &tree->afe[index + excess],
                (size_t)TH_MAX_RECONSTRUCTED_FORMATTING * sizeof(th_node *));
        uint64_t *keys = afe_keys(tree);
        memmove(&keys[index], &keys[index + excess], (size_t)TH_MAX_RECONSTRUCTED_FORMATTING * sizeof(uint64_t));
        tree->afe_len -= excess;
    }
    for (; index < tree->afe_len; index++) {
        th_node *clone = clone_formatting(tree, tree->afe[index]);
        if (clone == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return;          /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        }
        /* A reconstructed element is placed at the appropriate insertion location,
           so it is foster-parented out of a table when foster parenting is active. */
        th_node *parent, *before;
        insertion_location(tree, &parent, &before);
        node_insert_before(parent, clone, before);
        stack_push(tree, clone);
        afe_replace_at(tree, index, clone);
        /* an a or nobr start tag closes an open one in scope, so a clone the output nests in its namesake moves out */
        tree->reparse_hazard |= (clone->atom == TH_TAG_A) | (clone->atom == TH_TAG_NOBR);
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
    if (cur->atom == atom && !cur->afe_listed) {
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
        /* moving the furthest block under a formatting clone can nest it where its own start tag would not */
        tree->reparse_hazard = 1;
        tree->nest_bound = 0;
        /* the stack bottom, html or the fragment root, is below every formatting element; without one, the document
           takes its place */
        th_node *common = fmt_stack > 0 /* GCOVR_EXCL_BR_LINE */ ? tree->open[fmt_stack - 1] : tree->document;
        Py_ssize_t bookmark = afe_index_of(tree, fmt);
        th_node *old_select = furthest->reaches_selectedcontent ? select_reaching(furthest->parent) : NULL;
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
                /* not in the list: remove from the stack and continue; an option removed this way is never popped, so
                   it is picked here, the selection the reparse of its select would make */
                if (tree->select_state != NULL && is_html_element(node, TH_TAG_OPTION)) {
                    select_cache_pick(tree->select_state, node);
                }
                stack_remove_at(tree, node_idx);
                continue;
            }
            th_node *clone = clone_formatting(tree, node);
            if (clone == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                return 1;        /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
            }
            afe_replace_at(tree, node_afe, clone);
            node->left_stack = 1; /* the clone takes its place on the stack */
            tree->open[node_idx] = clone;
            tree->stack_version++;
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
        th_node *fmt_clone = clone_formatting(tree, fmt);
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
        if (tree->select_state != NULL) {
            tree->select_state->recheck = 1;
        }
        if (furthest->reaches_selectedcontent && old_select == NULL) {
            select_cache_block_gained(tree, furthest);
        }
        /* remove fmt from the list, insert the clone at the bookmark */
        Py_ssize_t fi = afe_index_of(tree, fmt);
        if (fi < bookmark) {
            bookmark--;
        }
        /* a clone with fmt's attributes hashes as fmt did, so it takes over fmt's key */
        uint64_t fmt_key = afe_keys(tree)[fi];
        /* removing fmt frees the slot the clone takes below, so the list needs no room */
        afe_remove_at(tree, fi);
        /* GCOVR_EXCL_START: the bookmark is kept within [0, afe_len] by the loop above; these are defensive clamps */
        if (bookmark < 0) {
            bookmark = 0;
        }
        if (bookmark > tree->afe_len) {
            bookmark = tree->afe_len;
        }
        /* GCOVR_EXCL_STOP */
        size_t moved = (size_t)(tree->afe_len - bookmark);
        memmove(&tree->afe[bookmark + 1], &tree->afe[bookmark], moved * sizeof(th_node *));
        tree->afe[bookmark] = fmt_clone;
        fmt_clone->afe_listed = 1;
        uint64_t *keys = afe_keys(tree);
        memmove(&keys[bookmark + 1], &keys[bookmark], moved * sizeof(uint64_t));
        keys[bookmark] = fmt_key;
        tree->afe_len++;
        afe_key_clone(tree, bookmark, fmt);
        /* remove fmt from the stack, insert the clone below furthest */
        Py_ssize_t fs = stack_index_of(tree, fmt);
        stack_remove_at(tree, fs);
        Py_ssize_t furthest_now = stack_index_of(tree, furthest);
        if (!stack_push(tree, fmt_clone)) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return 1;                       /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        }
        memmove(&tree->open[furthest_now + 2], &tree->open[furthest_now + 1],
                (size_t)(tree->open_len - furthest_now - 2) * sizeof(th_node *));
        tree->open[furthest_now + 1] = fmt_clone;
        blocks_recount(tree, furthest_now + 1, tree->open_len);
        tree->stack_version++;
    }
    return 1;
}

/* Has a p in button scope, with <button> also a scope boundary. */
static int has_p_in_button_scope(th_tree *tree) {
    if (tree->open_p_count == 0) {
        return 0;
    }
    Py_ssize_t index = stack_scan(tree, tree->open_len - 1, SCAN_BUTTON, TH_TAG_P);
    for (; index >= 0; index = stack_scan(tree, index - 1, SCAN_BUTTON, TH_TAG_P)) { /* GCOVR_EXCL_BR_LINE */
        th_node *node = tree->open[index];
        if (node->ns == TH_NS_HTML && node->atom == TH_TAG_P) {
            return 1;
        }
        if (is_scope_boundary(node) || (node->ns == TH_NS_HTML && /* GCOVR_EXCL_BR_LINE: foreign uses its own mode */
                                        node->atom == TH_TAG_BUTTON)) {
            return 0;
        }
    }
    return 0; /* GCOVR_EXCL_LINE: the stack contains a p or a nearer scope boundary */
}

/* The spec's "close a p element" step: when a p is in button scope, generate
   implied end tags (except p) and pop the stack through that p. */
static void close_p_in_button_scope(th_tree *tree) {
    if (has_p_in_button_scope(tree)) {
        generate_implied_end_tags(tree, TH_TAG_P);
        pop_until_atom(tree, TH_TAG_P);
    }
}

/* close_p_in_button_scope for a list-item start tag whose scan stopped at the special element at `barrier` (-1 when it
   found its item instead). Closing the p can pop that element too; the output then closes it explicitly, so a re-parse
   scan runs past it to the list item this one now nests in. */
static void close_p_before_list_item(th_tree *tree, Py_ssize_t barrier) {
    if (has_p_in_button_scope(tree)) {
        generate_implied_end_tags(tree, TH_TAG_P);
        pop_until_atom(tree, TH_TAG_P);
        tree->reparse_hazard |= barrier >= tree->open_len;
    }
}

static int is_heading_atom(uint16_t atom) {
    return atom == TH_TAG_H1 || atom == TH_TAG_H2 || atom == TH_TAG_H3 || atom == TH_TAG_H4 || atom == TH_TAG_H5 ||
           atom == TH_TAG_H6;
}

/* HTML h1-h6 only */
static int is_heading(const th_node *node) {
    return node->ns == TH_NS_HTML && is_heading_atom(node->atom);
}

/* a foreign context root named select takes the foreign rules, so only an HTML select context counts */
static int is_select_fragment(const th_tree *tree) {
    return tree->fragment_root != NULL && tree->fragment_root->ns == TH_NS_HTML && tree->ctx_atom == TH_TAG_SELECT;
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
    /* the html element is a special element, so any-other-end-tag stops before the stack bottom; the root is never
       the match, although a fragment root carries its context's name */
    for (Py_ssize_t index = tree->open_len - 1; index > 0; index--) {
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
static void tmpl_push(th_tree *tree, int record) {
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
    tree->tmpl[tree->tmpl_len++] = record;
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
    return (enum mode)tree->tmpl[tree->tmpl_len - 1];
}

static void stack_push_template(th_tree *tree, th_node *node) {
    stack_push(tree, node);
    note_template_pushed(tree);
    afe_push_marker(tree);
    tmpl_push(tree, M_IN_TEMPLATE);
}

/* Reset the insertion mode appropriately: pick the mode implied by the current
   stack of open elements (used when leaving cells, captions, selects, …). */
static enum mode reset_insertion_mode(th_tree *tree) {
    /* context resolves the bottom element */
    uint16_t context = tree->fragment_root != NULL ? tree->ctx_atom : TH_TAG_UNKNOWN;
    Py_ssize_t index = stack_scan(tree, tree->open_len - 1, SCAN_RESET, context);
    for (; index >= 0; index = stack_scan(tree, index - 1, SCAN_RESET, context)) { /* GCOVR_EXCL_BR_LINE: html bounds */
        th_node *node = tree->open[index];
        int last = (index == 0);
        uint16_t atom;
        if (last && tree->fragment_root != NULL) {
            /* the reset steps name HTML elements, so a foreign root matches none and the mode stays in body */
            atom = node->ns == TH_NS_HTML ? tree->ctx_atom : TH_TAG_UNKNOWN;
        } else if (node->ns != TH_NS_HTML) {
            /* the reset algorithm names HTML-namespace elements only, so a foreign
               element (an SVG/MathML td/tr/table/html/...) matches no step; skip it */
            continue;
        } else {
            atom = node->atom;
        }
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
static th_node *insert_implicit(th_tree *tree, uint16_t atom, uint8_t flags);

static th_node *insert_implicit(th_tree *tree, uint16_t atom, uint8_t flags) {
    th_node *node = node_new(tree, TH_NODE_ELEMENT);
    if (node == NULL) {    /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return tree->sink; /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    node->text = (Py_UCS4 *)th_tag_wide_name(atom);
    node->text_len = th_tag_table[atom - 1].name_len;
    node->atom = atom;
    node->tag_flags = flags | TH_ELEM_IMPLIED;
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

/* Push a raw-text or RCDATA element and return the insertion mode for its text. Out of line, each of the ten text-mode
   entries stays a call and a store. */
static TH_NOINLINE enum mode stack_push_text(th_tree *tree, th_node *node) {
    stack_push(tree, node);
    return M_TEXT;
}

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
    if (tok->kind == TH_COMMENT || tok->kind == TH_PI) {
        insert_comment_or_pi(tree, tok, tree->document);
        return TH_DRAIN_NEXT;
    }
    tree->quirks = 1; /* no doctype before content */
    dc->mode = M_BEFORE_HTML;
    return TH_DRAIN_REPROCESS;
}

static enum th_drain drain_before_html(th_tree *tree, th_token *tok, th_insert *dc) {
    if (tok->kind == TH_COMMENT || tok->kind == TH_PI) {
        insert_comment_or_pi(tree, tok, tree->document);
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
        stack_push(tree, html);
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
        th_node *html = insert_implicit(tree, TH_TAG_HTML, TH_TAG_SPECIAL | TH_TAG_SCOPING);
        stack_push(tree, html);
    }
    dc->mode = M_BEFORE_HEAD;
    return TH_DRAIN_REPROCESS;
}

static enum th_drain drain_before_head(th_tree *tree, th_token *tok, th_insert *dc) {
    if (tok->kind == TH_COMMENT || tok->kind == TH_PI) {
        insert_comment_or_pi(tree, tok, NULL);
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
        stack_push(tree, head);
        tree->head = head;
        dc->mode = M_IN_HEAD;
        return TH_DRAIN_NEXT;
    }
    if (tok->kind == TH_END_TAG) {
        uint16_t end_atom = tok_atom(tok);
        if (end_atom != TH_TAG_HEAD && end_atom != TH_TAG_BODY && end_atom != TH_TAG_HTML && end_atom != TH_TAG_BR) {
            return TH_DRAIN_NEXT; /* any other end tag before head is a parse error and ignored */
        }
    }
    tree->head = insert_implicit(tree, TH_TAG_HEAD, TH_TAG_SPECIAL);
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
    if (tok->kind == TH_COMMENT || tok->kind == TH_PI) {
        insert_comment_or_pi(tree, tok, NULL);
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
            dc->mode = stack_push_text(tree, node);
            th_tok_switch(dc->sm, (enum th_initial_state)model);
            dc->original_mode = M_IN_HEAD;
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_BASE || atom == TH_TAG_BASEFONT || atom == TH_TAG_BGSOUND || atom == TH_TAG_LINK ||
            atom == TH_TAG_META) {
            insert_void_element(tree, tok); /* void metadata: inserted, not pushed */
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_NOSCRIPT) {
            th_node *node = insert_element(tree, tok);
            dc->mode = stack_push_text(tree, node);
            if (tree->scripting) {
                /* with scripting on, noscript follows the generic raw-text path */
                th_tok_switch(dc->sm, TH_INIT_RAWTEXT);
                dc->original_mode = M_IN_HEAD;
                return TH_DRAIN_NEXT;
            }
            dc->mode = M_IN_HEAD_NOSCRIPT;
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_TEMPLATE) {
            th_node *node = insert_template(tree, tok);
            tree->frameset_ok = 0; /* a template start tag sets frameset-ok to "not ok" */
            stack_push(tree, node);
            note_template_pushed(tree);
            afe_push_marker(tree);
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
    if (tok->kind == TH_COMMENT || tok->kind == TH_PI) {
        insert_comment_or_pi(tree, tok, NULL);
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
            insert_void_element(tree, tok); /* void metadata */
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_STYLE || atom == TH_TAG_NOFRAMES) {
            th_node *node = insert_element(tree, tok);
            dc->mode = stack_push_text(tree, node);
            th_tok_switch(dc->sm, TH_INIT_RAWTEXT);
            dc->original_mode = M_IN_HEAD_NOSCRIPT;
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
    if (tok->kind == TH_COMMENT || tok->kind == TH_PI) {
        insert_comment_or_pi(tree, tok, NULL);
        return TH_DRAIN_NEXT;
    }
    if (tok->kind == TH_START_TAG && tok_atom(tok) == TH_TAG_HTML) {
        merge_attrs(tree, tree->open[0], tok);
        return TH_DRAIN_NEXT;
    }
    if (tok->kind == TH_START_TAG && tok_atom(tok) == TH_TAG_BODY) {
        th_node *body = insert_element(tree, tok);
        stack_push(tree, body);
        tree->frameset_ok = 0; /* an explicit body can't be replaced by a frameset */
        dc->mode = M_IN_BODY;
        return TH_DRAIN_NEXT;
    }
    if (tok->kind == TH_START_TAG && tok_atom(tok) == TH_TAG_FRAMESET) {
        th_node *fs = insert_element(tree, tok);
        stack_push(tree, fs);
        dc->mode = M_IN_FRAMESET;
        return TH_DRAIN_NEXT;
    }
    if (tok->kind == TH_START_TAG && tok_atom(tok) == TH_TAG_HEAD) {
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
                    tree->frameset_ok = 0; /* a template start tag sets frameset-ok to "not ok" */
                    /* the head is removed from under the template on the
                       stack; the template itself stays open */
                    tree->open[tree->open_len - 1] = node;
                    note_template_pushed(tree);
                    blocks_recount(tree, tree->open_len - 1, tree->open_len);
                    tree->stack_version++;
                    afe_push_marker(tree);
                    tmpl_push(tree, M_IN_TEMPLATE);
                    dc->mode = M_IN_TEMPLATE;
                    return TH_DRAIN_NEXT;
                }
                th_node *node = is_void_atom(atom) ? insert_void_element(tree, tok) : insert_element(tree, tok);
                stack_pop(tree); /* remove head again */
                if (atom == TH_TAG_TITLE || atom == TH_TAG_STYLE || atom == TH_TAG_SCRIPT || atom == TH_TAG_NOFRAMES) {
                    dc->mode = stack_push_text(tree, node);
                    th_tok_switch(dc->sm, (enum th_initial_state)content_model_for(atom, flags, tree->scripting));
                    dc->original_mode = M_AFTER_HEAD;
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
        th_node *body = insert_implicit(tree, TH_TAG_BODY, TH_TAG_SPECIAL);
        stack_push(tree, body);
    }
    tree->frameset_ok = 1; /* the implied body resets frameset-ok to "ok" */
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
            stack_push_template(tree, insert_template(tree, tok));
            tree->frameset_ok = 0; /* a template start tag sets frameset-ok to "not ok" */
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_BASE || atom == TH_TAG_BASEFONT || atom == TH_TAG_BGSOUND || atom == TH_TAG_LINK ||
            atom == TH_TAG_META) {
            insert_void_element(tree, tok); /* void head content */
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_TITLE || atom == TH_TAG_STYLE || atom == TH_TAG_SCRIPT || atom == TH_TAG_NOFRAMES) {
            th_node *node = insert_element(tree, tok);
            dc->mode = stack_push_text(tree, node);
            th_tok_switch(dc->sm, (enum th_initial_state)content_model_for(atom, flags, tree->scripting));
            dc->original_mode = M_IN_TEMPLATE;
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
    if (tok->kind == TH_COMMENT || tok->kind == TH_PI) {
        insert_comment_or_pi(tree, tok, NULL);
        return TH_DRAIN_NEXT;
    }
    if (tok->kind == TH_START_TAG) {
        uint16_t atom = tok_atom(tok);
        if (atom == TH_TAG_FRAMESET) {
            th_node *fs = insert_element(tree, tok);
            stack_push(tree, fs);
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_FRAME) {
            insert_void_element(tree, tok); /* void */
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_NOFRAMES) {
            th_node *node = insert_element(tree, tok);
            dc->mode = stack_push_text(tree, node);
            th_tok_switch(dc->sm, TH_INIT_RAWTEXT);
            dc->original_mode = M_IN_FRAMESET;
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
    if (tok->kind == TH_COMMENT || tok->kind == TH_PI) {
        insert_comment_or_pi(tree, tok, NULL);
        return TH_DRAIN_NEXT;
    }
    if (tok->kind == TH_START_TAG && tok_atom(tok) == TH_TAG_NOFRAMES) {
        th_node *node = insert_element(tree, tok);
        dc->mode = stack_push_text(tree, node);
        th_tok_switch(dc->sm, TH_INIT_RAWTEXT);
        dc->original_mode = M_AFTER_FRAMESET;
        return TH_DRAIN_NEXT;
    }
    if (tok->kind == TH_END_TAG && tok_atom(tok) == TH_TAG_HTML) {
        dc->mode = M_AFTER_AFTER_FRAMESET; /* only comments and whitespace remain */
    }
    return TH_DRAIN_NEXT;
}

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

static int slice_can_span(const th_tree *tree, const th_token *token) {
    return token->is_slice && tree->can_span &&
           (!tree->has_nul ||
            !chunk_has_nul(tree->kind, (const uint8_t *)tree->data + token->src_start * tree->kind, token->src_len));
}

static enum th_drain drain_in_body(th_tree *tree, th_token *tok, th_insert *dc) {
    if (tok->kind == TH_TEXT) {
        if (slice_can_span(tree, tok)) {
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
    if (tok->kind == TH_COMMENT || tok->kind == TH_PI) {
        insert_comment_or_pi(tree, tok, NULL);
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
                stack_push(tree, fs);
                dc->mode = M_IN_FRAMESET;
            }
            return TH_DRAIN_NEXT;
        }
        /* block and heading elements close an open p and are inserted
           WITHOUT reconstructing the active formatting elements */
        if (is_heading_atom(atom)) {
            close_p_in_button_scope(tree);
            if (is_heading(current_node(tree))) {
                stack_pop(tree); /* nested headings don't stack */
            }
            th_node *node = insert_element(tree, tok);
            stack_push(tree, node);
            return TH_DRAIN_NEXT;
        }
        if (is_p_closing_block(atom) && atom != TH_TAG_PLAINTEXT && atom != TH_TAG_PRE &&
            atom != TH_TAG_LISTING) { /* pre/listing also drop a leading newline below */
            close_p_in_button_scope(tree);
            th_node *node = insert_element(tree, tok);
            stack_push(tree, node);
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_TABLE) {
            if (!tree->quirks && has_p_in_button_scope(tree)) {
                generate_implied_end_tags(tree, TH_TAG_P);
                pop_until_atom(tree, TH_TAG_P);
            }
            th_node *node = insert_element(tree, tok);
            stack_push(tree, node);
            dc->mode = M_IN_TABLE;
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_SELECT) {
            tree->reparse_hazard = 1; /* ignored tags inside select can become elements after serialization */
            /* select content is plain in-body content now; a nested
               select start closes the open one instead of nesting */
            if (is_select_fragment(tree)) {
                return TH_DRAIN_NEXT; /* fragment case: ignored */
            }
            if (has_in_scope(tree, TH_TAG_SELECT)) {
                pop_until_atom(tree, TH_TAG_SELECT);
                return TH_DRAIN_NEXT;
            }
            reconstruct_afe(tree);
            th_node *node = insert_element(tree, tok);
            stack_push(tree, node);
            tree->frameset_ok = 0;
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_SVG || atom == TH_TAG_MATH) {
            reconstruct_afe(tree);
            th_node *node = insert_foreign(tree, tok, atom == TH_TAG_SVG ? TH_NS_SVG : TH_NS_MATHML);
            if (!tok->self_closing) {
                stack_push(tree, node);
            }
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_LI) {
            Py_ssize_t barrier = -1;
            /* html bounds the stack walk */
            for (Py_ssize_t index = tree->open_len - 1; index >= 0; index--) { /* GCOVR_EXCL_BR_LINE */
                uint16_t open_atom = tree->open[index]->atom;
                /* only an HTML li starts the walk, as pop_until_atom skips foreign elements */
                if (tree->open[index]->ns == TH_NS_HTML && open_atom == TH_TAG_LI) {
                    generate_implied_end_tags(tree, TH_TAG_LI);
                    pop_until_atom(tree, TH_TAG_LI);
                    break;
                }
                if ((tree->open[index]->tag_flags & TH_TAG_SPECIAL) && open_atom != TH_TAG_ADDRESS &&
                    open_atom != TH_TAG_DIV && open_atom != TH_TAG_P) {
                    barrier = index;
                    break;
                }
            }
            close_p_before_list_item(tree, barrier);
            th_node *node = insert_element(tree, tok);
            stack_push(tree, node);
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_DD || atom == TH_TAG_DT) {
            Py_ssize_t barrier = -1;
            /* html bounds the stack walk */
            for (Py_ssize_t index = tree->open_len - 1; index >= 0; index--) { /* GCOVR_EXCL_BR_LINE */
                uint16_t open_atom = tree->open[index]->atom;
                if (tree->open[index]->ns == TH_NS_HTML && (open_atom == TH_TAG_DD || open_atom == TH_TAG_DT)) {
                    generate_implied_end_tags(tree, open_atom);
                    pop_until_atom(tree, open_atom);
                    break;
                }
                if ((tree->open[index]->tag_flags & TH_TAG_SPECIAL) && open_atom != TH_TAG_ADDRESS &&
                    open_atom != TH_TAG_DIV && open_atom != TH_TAG_P) {
                    barrier = index;
                    break;
                }
            }
            close_p_before_list_item(tree, barrier);
            th_node *node = insert_element(tree, tok);
            stack_push(tree, node);
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_HR) {
            close_p_in_button_scope(tree);
            if (has_in_scope(tree, TH_TAG_SELECT) || is_select_fragment(tree)) {
                /* in a select (or a select-context fragment) close the open option/optgroup
                   first, so the hr lands at select level rather than inside the option */
                generate_implied_end_tags(tree, TH_TAG_UNKNOWN);
            }
            insert_void_element(tree, tok); /* void */
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_BUTTON) {
            if (has_in_scope(tree, TH_TAG_BUTTON)) {
                generate_implied_end_tags(tree, TH_TAG_UNKNOWN);
                pop_until_atom(tree, TH_TAG_BUTTON);
            }
            reconstruct_afe(tree);
            th_node *node = insert_element(tree, tok);
            stack_push(tree, node);
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_OPTION || atom == TH_TAG_OPTGROUP) {
            tree->reparse_hazard = 1; /* nested option markup can reopen as elements after serialization */
            if (has_in_scope(tree, TH_TAG_SELECT) || is_select_fragment(tree)) {
                /* inside a select (or a select-context fragment, where no select node
                   is on the stack), implied end tags close open option and (for an
                   optgroup start) optgroup elements */
                generate_implied_end_tags(tree, atom == TH_TAG_OPTION ? TH_TAG_OPTGROUP : TH_TAG_UNKNOWN);
            } else if (current_node(tree)->atom == TH_TAG_OPTION) {
                stack_pop(tree);
            }
            reconstruct_afe(tree);
            th_node *node = insert_element(tree, tok);
            stack_push(tree, node);
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_RB || atom == TH_TAG_RTC) {
            if (has_in_scope(tree, TH_TAG_RUBY)) {
                generate_implied_end_tags(tree, TH_TAG_UNKNOWN);
            }
            th_node *node = insert_element(tree, tok);
            stack_push(tree, node);
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_RP || atom == TH_TAG_RT) {
            if (has_in_scope(tree, TH_TAG_RUBY)) {
                generate_implied_end_tags(tree, TH_TAG_RTC);
            }
            th_node *node = insert_element(tree, tok);
            stack_push(tree, node);
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_BASE || atom == TH_TAG_BASEFONT || atom == TH_TAG_BGSOUND || atom == TH_TAG_LINK ||
            atom == TH_TAG_META) {
            insert_void_element(tree, tok); /* head metadata: in-head rules, no AFE reconstruction */
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_FORM) {
            int in_template = stack_index_of_atom(tree, TH_TAG_TEMPLATE) >= 0;
            if (tree->form != NULL && !in_template) {
                return TH_DRAIN_NEXT; /* a nested form is ignored */
            }
            close_p_in_button_scope(tree);
            th_node *node = insert_element(tree, tok);
            stack_push(tree, node);
            if (!in_template) {
                tree->form = node;
            }
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_INPUT) {
            if (is_select_fragment(tree)) {
                return TH_DRAIN_NEXT;
            }
            if (has_in_scope(tree, TH_TAG_SELECT)) {
                pop_until_atom(tree, TH_TAG_SELECT);
            }
            reconstruct_afe(tree);
            insert_void_element(tree, tok); /* void */
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_IMAGE) {
            /* the famous quirk: <image> becomes <img> */
            reconstruct_afe(tree);
            th_node *node = insert_void_element(tree, tok);
            node->atom = TH_TAG_IMG;
            node->text = (Py_UCS4 *)th_tag_wide_name(TH_TAG_IMG);
            node->text_len = th_tag_table[TH_TAG_IMG - 1].name_len;
            return TH_DRAIN_NEXT; /* img is void */
        }
        if (atom == TH_TAG_PLAINTEXT) {
            tree->reparse_hazard = 1; /* the serialized </plaintext> re-parses as text, as does everything after it */
            close_p_in_button_scope(tree);
            th_node *node = insert_element(tree, tok);
            stack_push(tree, node);
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
            dc->mode = stack_push_text(tree, node);
            if (atom == TH_TAG_TEXTAREA) {
                tree->drop_newline = 1; /* a leading LF in a textarea is ignored */
            }
            th_tok_switch(dc->sm, (enum th_initial_state)model);
            /* when fostered out of a table the in-body rules run but the real insertion
               mode is still the table mode, so the text mode must return there, not to
               in body, or the table's later rows would be dropped */
            dc->original_mode = dc->foster_pending ? dc->foster_return : M_IN_BODY;
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_PRE || atom == TH_TAG_LISTING) {
            close_p_in_button_scope(tree);
            th_node *node = insert_element(tree, tok);
            stack_push(tree, node);
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
                if (!adoption_agency(tree, TH_TAG_NOBR)) {
                    any_other_end_tag(tree, TH_TAG_NOBR, tok);
                }
                reconstruct_afe(tree);
            }
            th_node *node = insert_element(tree, tok);
            stack_push(tree, node);
            afe_push(tree, node);
            return TH_DRAIN_NEXT;
        }
        if (flags & TH_TAG_FORMATTING) {
            reconstruct_afe(tree);
            th_node *node = insert_element(tree, tok);
            stack_push(tree, node);
            afe_push(tree, node);
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_APPLET || atom == TH_TAG_MARQUEE || atom == TH_TAG_OBJECT) {
            reconstruct_afe(tree);
            th_node *node = insert_element(tree, tok);
            stack_push(tree, node);
            afe_push_marker(tree);
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_TEMPLATE) {
            stack_push_template(tree, insert_template(tree, tok));
            tree->frameset_ok = 0; /* a template start tag sets frameset-ok to "not ok" */
            dc->mode = M_IN_TEMPLATE;
            return TH_DRAIN_NEXT;
        }
        reconstruct_afe(tree);
        th_node *node = is_void_atom(atom) ? insert_void_element(tree, tok) : insert_element(tree, tok);
        if (is_void_atom(atom)) {
            return TH_DRAIN_NEXT; /* void: inserted, not pushed (a stray "/" on any
                      other element is ignored in HTML content) */
        }
        stack_push(tree, node);
        /* every HTML selectedcontent the parser creates lands here, so its select learns of it as it arrives */
        if (atom == TH_TAG_SELECTEDCONTENT) {
            selectedcontent_flagged(node);
            selectedcontent_inserted(tree, node);
        }
        return TH_DRAIN_NEXT;
    }
    /* only an end tag reaches here: text/comment/start break above, a DOCTYPE is ignored before the switch */
    if (tok->kind == TH_END_TAG) { /* GCOVR_EXCL_BR_LINE: the non-end-tag branch is unreachable */
        uint8_t flags = tok->tag_flags;
        uint16_t atom = tok->atom;
        if (atom == TH_TAG_BODY || atom == TH_TAG_HTML) {
            if (!has_in_scope(tree, TH_TAG_BODY)) {
                /* parse error, ignored: with no body in scope (a caption/object/applet/
                   marquee keeps it out) the mode must not switch to "after body" */
                return TH_DRAIN_NEXT;
            }
            if (atom == TH_TAG_BODY) {
                /* body is in scope here, so it is on the stack and the lookup finds it */
                record_end_tag_location(tree, tree->open[stack_index_of_atom(tree, TH_TAG_BODY)], tok);
            }
            dc->mode = M_AFTER_BODY;
            if (atom == TH_TAG_HTML) {
                return TH_DRAIN_REPROCESS; /* drain_after_body stamps html's end tag */
            }
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_P) {
            if (!has_p_in_button_scope(tree)) {
                /* an empty p that the stray </p> itself closes */
                insert_implicit(tree, TH_TAG_P, TH_TAG_SPECIAL | TH_ELEM_CLOSED_BY_END_TAG);
            } else {
                generate_implied_end_tags(tree, TH_TAG_P);
                pop_until_atom(tree, TH_TAG_P);
            }
            return TH_DRAIN_NEXT;
        }
        if (is_heading_atom(atom)) {
            int in_scope = 0;
            /* html bounds the stack walk */
            for (Py_ssize_t index = stack_scan(tree, tree->open_len - 1, SCAN_HEADING, TH_TAG_UNKNOWN); index >= 0;
                 index = stack_scan(tree, index - 1, SCAN_HEADING, TH_TAG_UNKNOWN)) {
                if (is_heading(tree->open[index])) {
                    in_scope = 1;
                    break;
                }
                if (is_scope_boundary(tree->open[index])) {
                    break;
                }
            }
            if (in_scope) {
                generate_implied_end_tags(tree, TH_TAG_UNKNOWN);
                while (tree->open_len > 1) { /* GCOVR_EXCL_BR_LINE: the heading is in scope when this runs, so
                                                the walk stops above the root */
                    th_node *node = current_node(tree);
                    stack_pop(tree);
                    if (is_heading(node)) {
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
                for (Py_ssize_t index = node != NULL ? stack_scan(tree, tree->open_len - 1, SCAN_SCOPE, node->atom)
                                                     : -1;
                     index >= 0; index = stack_scan(tree, index - 1, SCAN_SCOPE, node->atom)) {
                    if (tree->open[index] == node) {
                        in_scope = 1;
                        break;
                    }
                    if (is_scope_boundary(tree->open[index])) {
                        break;
                    }
                }
                if (!in_scope) {
                    /* the form stays open with its pointer cleared, so a later <form> nests inside it */
                    if (node != NULL) {
                        tree->reparse_hazard = 1;
                    }
                    return TH_DRAIN_NEXT; /* parse error, ignored */
                }
                generate_implied_end_tags(tree, TH_TAG_UNKNOWN);
                record_end_tag_location(tree, node, tok); /* stamped here: the removal below skips stack_pop */
                /* the form is removed from wherever it sits on the stack */
                /* the form element is on the stack when this runs, so it */
                for (Py_ssize_t index = tree->open_len - 1; index >= 0; index--) { /* GCOVR_EXCL_BR_LINE */
                    if (tree->open[index] == node) {
                        /* removed from under still-open descendants, which a later <form> then nests inside */
                        if (index != tree->open_len - 1) {
                            tree->reparse_hazard = 1;
                        }
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
            if (adoption_agency(tree, atom)) {
                return TH_DRAIN_NEXT;
            }
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
            insert_implicit(tree, TH_TAG_BR, TH_TAG_SPECIAL | TH_ELEM_CLOSED_BY_END_TAG);
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
        if (slice_can_span(tree, tok)) {
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
    /* the end tag closes the raw-text/RCDATA element on top of the stack */
    th_node *element = current_node(tree);
    /* the tokenizer reads a script's end tag only outside the double escaped state, so a re-parse of its text,
       which the inserts merge into one node, ends there too and the serializer can skip its scan; the end of input
       pops a script elsewhere, so the one it ends inside keeps the scan */
    if (element->atom == TH_TAG_SCRIPT && element->first_child != NULL) {
        element->first_child->script_text_closes = 1;
    }
    stack_pop(tree);
    dc->mode = dc->original_mode;
    return TH_DRAIN_NEXT;
}

static enum th_drain drain_in_table(th_tree *tree, th_token *tok, th_insert *dc) {
    if (tok->kind == TH_TEXT) {
        Py_ssize_t len;
        Py_UCS4 *text = token_text(tree, tok, &len);
        /* a pre/listing/textarea opened in table content drops its leading newline here
           too: the token after the start tag is this text, whatever table mode foster
           parenting left active, so a round trip does not grow the newline (#1037) */
        if (tree->drop_newline && len > 0 /* GCOVR_EXCL_BR_LINE */ && text[0] == '\n') {
            text++;
            len--;
        }
        tree->drop_newline = 0;
        if (all_whitespace_or_nul(text, len)) {
            if (tree->afe_len > 0) {
                uint16_t atom = current_node(tree)->atom;
                if (atom != TH_TAG_TABLE && atom != TH_TAG_TBODY && atom != TH_TAG_TEMPLATE && atom != TH_TAG_TFOOT &&
                    atom != TH_TAG_THEAD && atom != TH_TAG_TR) {
                    tree->foster = 1;
                    reconstruct_afe(tree);
                    tree->foster = 0;
                }
            }
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
    if (tok->kind == TH_COMMENT || tok->kind == TH_PI) {
        insert_comment_or_pi(tree, tok, NULL);
        dc->mode = dc->table_origin;
        return TH_DRAIN_NEXT;
    }
    if (tok->kind == TH_START_TAG) {
        uint16_t atom = tok->atom;
        if (atom == TH_TAG_CAPTION) {
            clear_to(tree, TH_TAG_TABLE, TH_TAG_TABLE, TH_TAG_TABLE);
            th_node *node = insert_element(tree, tok);
            stack_push(tree, node);
            afe_push_marker(tree);
            dc->mode = M_IN_CAPTION;
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_COLGROUP) {
            clear_to(tree, TH_TAG_TABLE, TH_TAG_TABLE, TH_TAG_TABLE);
            th_node *node = insert_element(tree, tok);
            stack_push(tree, node);
            dc->mode = M_IN_COLUMN_GROUP;
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_COL) {
            clear_to(tree, TH_TAG_TABLE, TH_TAG_TABLE, TH_TAG_TABLE);
            th_node *cg = insert_implicit(tree, TH_TAG_COLGROUP, TH_TAG_SPECIAL);
            stack_push(tree, cg);
            dc->mode = M_IN_COLUMN_GROUP;
            return TH_DRAIN_REPROCESS;
        }
        if (atom == TH_TAG_TBODY || atom == TH_TAG_THEAD || atom == TH_TAG_TFOOT) {
            clear_to(tree, TH_TAG_TABLE, TH_TAG_TABLE, TH_TAG_TABLE);
            th_node *node = insert_element(tree, tok);
            stack_push(tree, node);
            dc->mode = M_IN_TABLE_BODY;
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_TD || atom == TH_TAG_TH || atom == TH_TAG_TR) {
            clear_to(tree, TH_TAG_TABLE, TH_TAG_TABLE, TH_TAG_TABLE);
            {
                th_node *tb = insert_implicit(tree, TH_TAG_TBODY, TH_TAG_SPECIAL);
                stack_push(tree, tb);
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
            dc->mode = stack_push_text(tree, node);
            th_tok_switch(dc->sm, (enum th_initial_state)model);
            dc->original_mode = dc->table_origin;
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_INPUT && input_is_hidden(tok)) {
            /* it stays only while its type attribute does: an attribute filter dropping it makes the output foster it
             */
            tree->reparse_hazard = 1;
            insert_void_element(tree, tok); /* a hidden input stays in the table, no fostering */
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
        /* anything else: foster-parent this one token under in-body rules; the output nests it where it landed,
           which a re-parse in the plain mode for that position treats differently (and in a template, the token never
           left) */
        tree->foster = 1;
        tree->reparse_hazard = 1;
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
        if (current_node(tree)->atom != TH_TAG_COLGROUP) {
            /* a fragment or template root ignores each non-whitespace character and stays in this mode, so the
               whitespace after it is still inserted */
            insert_whitespace_only(tree, text, len);
            return TH_DRAIN_NEXT;
        }
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
    if (tok->kind == TH_COMMENT || tok->kind == TH_PI) {
        insert_comment_or_pi(tree, tok, NULL);
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
        insert_void_element(tree, tok); /* col is void */
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
            stack_push(tree, node);
            dc->mode = M_IN_ROW;
            return TH_DRAIN_NEXT;
        }
        if (atom == TH_TAG_TD || atom == TH_TAG_TH) {
            clear_to(tree, TH_TAG_TBODY, TH_TAG_TFOOT, TH_TAG_THEAD);
            {
                th_node *row = insert_implicit(tree, TH_TAG_TR, TH_TAG_SPECIAL);
                stack_push(tree, row);
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
            stack_push(tree, node);
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
    if (tok->kind == TH_COMMENT || tok->kind == TH_PI) {
        /* open[0] is always the html element in this mode, never the document */
        th_node *root = tree->open_len > 0 ? tree->open[0] : tree->document; /* GCOVR_EXCL_BR_LINE */
        insert_comment_or_pi(tree, tok, root);
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
        if (tree->open_len > 0) { /* GCOVR_EXCL_BR_LINE: html sits at open[0] throughout this mode */
            record_end_tag_location(tree, tree->open[0], tok);
        }
        dc->mode = M_AFTER_AFTER_BODY;
        return TH_DRAIN_NEXT;
    }
    dc->mode = M_IN_BODY;
    return TH_DRAIN_REPROCESS;
}

static enum th_drain drain_after_after_body(th_tree *tree, th_token *tok, th_insert *dc) {
    if (tok->kind == TH_COMMENT || tok->kind == TH_PI) {
        /* a fragment has no document node to attach to; use its root */
        insert_comment_or_pi(tree, tok, tree->fragment_root != NULL ? tree->fragment_root : tree->document);
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
    if (tok->kind == TH_COMMENT || tok->kind == TH_PI) {
        insert_comment_or_pi(tree, tok, tree->document);
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
            dc->mode = stack_push_text(tree, node);
            th_tok_switch(dc->sm, TH_INIT_RAWTEXT);
            dc->original_mode = M_AFTER_AFTER_FRAMESET;
        }
    }
    return TH_DRAIN_NEXT; /* everything else is a parse error and ignored */
}

/* Run one token through tree construction: the foreign-content dispatch, then the current insertion mode, reprocessing
   it as the modes direct. run_drain feeds it the tokens it pulls; th_shadow_step feeds it the streaming rewriter's. */
static TH_TREE_INLINE void drain_token(th_tree *tree, th_insert *dc, th_token *tok) {
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
    /* the machine interned a tag's name; every comparison and insert below reads tok->atom / tok->tag_flags */
    if (tok->kind != TH_START_TAG && tok->kind != TH_END_TAG) {
        tok->atom = TH_TAG_UNKNOWN;
        tok->tag_flags = 0;
    }
    /* the element(s) this end tag pops are the ones the source closed explicitly */
    tree->closing_end_tag = tok->kind == TH_END_TAG ? tok : NULL;
    if (use_foreign_rules(tree, tok) && foreign_step(tree, tok)) {
        goto token_done; /* handled under foreign-content rules */
    }
    /* template tags are handled uniformly across the table/select modes (the
       spec routes them through in-head); in-body/in-head/in-template keep
       their own handlers for the active-formatting interactions */
    if (tok->kind == TH_END_TAG && dc->mode >= M_IN_HEAD && tok_atom(tok) == TH_TAG_TEMPLATE) {
        if (stack_index_of_atom(tree, TH_TAG_TEMPLATE) >= 0) {
            generate_implied_end_tags(tree, TH_TAG_UNKNOWN);
            /* an element still open inside the template may have pushed its own formatting marker, which the clear
               below leaves behind, shielding an outer a/nobr the output's explicit end tags no longer shield */
            tree->reparse_hazard |= current_node(tree)->atom != TH_TAG_TEMPLATE;
            pop_until_atom(tree, TH_TAG_TEMPLATE);
            afe_clear_to_marker(tree);
            tmpl_pop(tree);
            dc->mode = reset_insertion_mode(tree);
        }
        goto token_done;
    }
    if (tok->kind == TH_START_TAG &&
        (dc->mode == M_IN_TABLE || dc->mode == M_IN_TABLE_BODY || dc->mode == M_IN_ROW || dc->mode == M_IN_CELL ||
         dc->mode == M_IN_COLUMN_GROUP || dc->mode == M_IN_CAPTION) &&
        tok_atom(tok) == TH_TAG_TEMPLATE) {
        stack_push_template(tree, insert_template(tree, tok));
        tree->frameset_ok = 0; /* a template start tag sets frameset-ok to "not ok" */
        dc->mode = M_IN_TEMPLATE;
        goto token_done;
    }
    /* "in select in table": a select opened in a table context does not get its own
       mode here, so a table-family start tag would nest inside the still-open select.
       Per the spec it pops the select, resets the insertion mode, and reprocesses, so
       the table element becomes a sibling of the closed select (#90). */
    if (tok->kind == TH_START_TAG && (dc->mode == M_IN_TABLE || dc->mode == M_IN_TABLE_BODY || dc->mode == M_IN_ROW ||
                                      dc->mode == M_IN_CELL || dc->mode == M_IN_CAPTION)) {
        uint16_t atom = tok_atom(tok);
        if ((atom == TH_TAG_CAPTION || atom == TH_TAG_TABLE || atom == TH_TAG_TBODY || atom == TH_TAG_TFOOT ||
             atom == TH_TAG_THEAD || atom == TH_TAG_TR || atom == TH_TAG_TD || atom == TH_TAG_TH) &&
            has_in_scope(tree, TH_TAG_SELECT)) {
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
        return;
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
token_done:
    if (tok->kind == TH_START_TAG && tok->self_closing && !tok->self_closing_acknowledged) {
        th_error_sink_push(&tree->errors, "non-void-html-element-start-tag-with-trailing-solidus", tok->line, tok->col);
    }
}

/* The mode the next token runs in: a fostered token ran under the in-body rules, and the table mode it came from takes
   over again at the next token. */
static enum mode effective_mode(const th_insert *dc) {
    return dc->foster_pending ? dc->foster_return : dc->mode;
}

/* Whether source[from, to) spells an end tag that closes raw-text noscript content: `</noscript` in any letter case
   followed by whitespace, '/' or '>'. */
static int source_ends_noscript(const th_tree *tree, Py_ssize_t from, Py_ssize_t to) {
    static const char name[] = "</noscript";
    Py_ssize_t name_len = (Py_ssize_t)sizeof(name) - 1;
    for (Py_ssize_t index = from; index + name_len < to; index++) {
        TH_WORK_STEP();
        Py_ssize_t at = 0;
        while (at < name_len && lower_ascii(PyUnicode_READ(tree->kind, tree->data, index + at)) == (Py_UCS4)name[at]) {
            at++;
        }
        Py_UCS4 next = PyUnicode_READ(tree->kind, tree->data, index + name_len);
        if (at == name_len && (is_space(next) || next == '/' || next == '>')) {
            return 1;
        }
    }
    return 0;
}

/* Whether the open elements under a just-pushed noscript leave nothing the minified markup before it could rebuild
   differently: all HTML, and no table whose misplaced content the parser moves. An open template needs no check here:
   its marker keeps the active formatting list non-empty, which already rules out a cut. */
static int noscript_cut_stack(const th_tree *tree) {
    for (Py_ssize_t index = 0; index < tree->open_len - 1; index++) {
        const th_node *open = tree->open[index];
        if (open->ns != TH_NS_HTML || open->atom == TH_TAG_TABLE) {
            return 0;
        }
    }
    return 1;
}

/* Record the noscript a start tag just built, given the open-element count and node sequence from before the token.
   The parse ignores the tag in some modes, which leaves no node to record. The span stays open when the tag left the
   builder where a raw-text reading does, one element deeper, so its close can be compared against the state saved
   here. That state is also where a cut can start: no active formatting element to reconstruct, and a form pointer that
   the minified markup sets alike. */
static void noscript_opened(th_tree *tree, th_insert *dc, th_noscript_track *track, Py_ssize_t start,
                            Py_ssize_t open_before, uint32_t seq_before) {
    th_node *top = current_node(tree);
    int created = tree->node_seq != seq_before;
    int pushed = created && top->seq == tree->node_seq - 1 && top->ns == TH_NS_HTML;
    if (!pushed) {
        return;
    }
    if (track->count == track->cap) {
        size_t cap;
        size_t bytes;
        int grew = th_grow_cap((size_t)track->count + 1, (size_t)track->cap, 4, sizeof(th_noscript_span), &cap, &bytes);
        if (!grew) {           /* GCOVR_EXCL_BR_LINE: size overflow needs a length no allocation could hold */
            track->failed = 1; /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
            return;            /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
        }
        th_noscript_span *grown = PyMem_Realloc(track->spans, bytes);
        if (grown == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            track->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
            return;            /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        }
        track->spans = grown;
        track->cap = (Py_ssize_t)cap;
    }
    th_noscript_span *span = &track->spans[track->count];
    span->node = top;
    span->seq = tree->node_seq - 1;
    span->start = start;
    span->content = th_tok_position(dc->sm);
    span->end = -1;
    span->stable = 0;
    span->cut = 0;
    span->html = tree->open[0];
    span->html_attrs = span->html->attr_count;
    span->body = tree->open[1]->atom == TH_TAG_BODY ? tree->open[1] : NULL;
    span->body_attrs = span->body != NULL ? span->body->attr_count : 0;
    if (track->open < 0 && tree->open_len == open_before + 1) {
        track->open = track->count;
        track->open_len = open_before;
        track->mode = dc->mode == M_IN_HEAD_NOSCRIPT ? M_IN_HEAD : effective_mode(dc);
        track->afe_len = tree->afe_len;
        track->afe_last = tree->afe_len > 0 ? tree->afe[tree->afe_len - 1] : NULL;
        track->form = tree->form;
        track->frameset_ok = tree->frameset_ok;
        track->html_attrs = span->html_attrs;
        track->body = span->body;
        track->body_attrs = span->body_attrs;
        span->cut = tree->afe_len == 0 && (tree->form == NULL || !tree->form->left_stack) && noscript_cut_stack(tree);
    }
    track->count++;
}

/* The open noscript left the stack: it is stable when the token that closed it is the first `</noscript` after its
   start tag and the builder is back where the raw-text reading puts it -- the same open elements, active formatting
   elements, form pointer, insertion mode and html/body attributes, and the element sits where the cap lets it hold
   its content. A cleared frameset-ok is kept apart, since it only
   matters to a later <frameset>. Every comparison is evaluated, so the result does not depend on which one fails. */
static void noscript_closed(th_tree *tree, th_insert *dc, th_noscript_track *track, Py_ssize_t end) {
    th_noscript_span *span = &track->spans[track->open];
    if (track->frameset_ok && !tree->frameset_ok) {
        track->frameset_span = track->open;
    }
    track->open = -1;
    span->end = end;
    th_node *afe_last = tree->afe_len > 0 ? tree->afe[tree->afe_len - 1] : NULL;
    Py_ssize_t body_attrs = track->body != NULL ? track->body->attr_count : 0;
    span->stable = (end >= span->content) & (tree->open_len == track->open_len) & (tree->afe_len == track->afe_len) &
                   (afe_last == track->afe_last) & (tree->form == track->form) &
                   ((int)effective_mode(dc) == track->mode) & (tree->open[0]->attr_count == track->html_attrs) &
                   (body_attrs == track->body_attrs);
    if (span->stable && has_ancestors_at_least(span->node, TH_MAX_TREE_DEPTH)) {
        span->stable = 0;
    }
    if (span->stable) {
        span->stable = !source_ends_noscript(tree, span->content, end);
    }
}

/* drain_token with the noscript bookkeeping around it, taken only by a parse that records spans. */
static TH_NOINLINE void drain_tracked(th_tree *tree, th_insert *dc, th_token *tok, th_noscript_track *track) {
    int opens = tok->kind == TH_START_TAG && tok->atom == TH_TAG_NOSCRIPT;
    int ends = tok->kind == TH_END_TAG && tok->atom == TH_TAG_NOSCRIPT;
    Py_ssize_t offset = tok->src_off; /* stamped on every tag token, read before drain_token reuses the token */
    Py_ssize_t open_before = tree->open_len;
    uint32_t seq_before = tree->node_seq;
    if (tok->kind == TH_START_TAG && tok->atom == TH_TAG_FRAMESET && track->frameset_span >= 0) {
        track->spans[track->frameset_span].stable = 0;
    }
    if (tok->kind == TH_START_TAG && tok->atom == TH_TAG_FRAMESET && track->open >= 0 && tree->frameset_ok) {
        /* a frameset that replaces the body detaches the open noscript, which minify would never copy */
        track->spans[track->open].cut = 0;
    }
    drain_token(tree, dc, tok);
    if (track->open >= 0 && track->spans[track->open].node->left_stack) {
        noscript_closed(tree, dc, track, ends ? offset : -1);
    }
    if (opens) {
        /* a noscript inside an open one gets a span too, so every noscript element the walk meets has one */
        noscript_opened(tree, dc, track, offset, open_before, seq_before);
    }
}

/* Drive tree construction over the tokens the tokenizer can produce now,
   returning when it stalls (TH_STEP_NEED_MORE), reaches EOF (TH_STEP_DONE), or
   fails. The insertion-mode state is restored from run_state on entry and saved
   back on exit so a later call resumes the WHATWG algorithm in place. A non-NULL track records noscript spans; the
   parses without one get their own copy of the loop with no check for it. */
static TH_TREE_INLINE void run_drain_with(th_tree *tree, th_tokenizer *sm, th_run_state *run_state,
                                          th_noscript_track *track) {
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
        /* only a noscript or frameset tag, or a token while a noscript is open, changes the record; drain_tracked reads
           the kind, as a text token can carry a tag's stale atom */
        if (track != NULL && (track->open >= 0 || tok->atom == TH_TAG_NOSCRIPT || tok->atom == TH_TAG_FRAMESET)) {
            drain_tracked(tree, dc, tok, track);
        } else {
            drain_token(tree, dc, tok);
        }
    }
    /* the tokenizer reports its allocation failures as TH_STEP_ERROR, which would otherwise end the drain as if the
       input had ended and return a partial tree, and a parse error the sink dropped would leave the list incomplete */
    tree->failed |= th_tok_failed(sm) || th_error_sink_failed(&tree->errors); /* GCOVR_EXCL_BR_LINE: allocation */
    run_state->mode = dc->mode;
    run_state->original_mode = dc->original_mode;
    run_state->foster_return = dc->foster_return;
    run_state->foster_pending = dc->foster_pending;
}

static void run_drain(th_tree *tree, th_tokenizer *sm, th_run_state *run_state) {
    run_drain_with(tree, sm, run_state, NULL);
}

/* Stop parsing at EOF: pop the remaining open elements, which runs the
   option-into-selectedcontent clone for a still-open selected option, then settles
   the late selectedcontent elements, whose children are complete only now. */
static void run_close(th_tree *tree) {
    /* EOF closes the remaining open elements; none was closed by an end tag. The
       last token buffer may still hold a partial tag the tokenizer never emitted
       (EOF in a tag name), so clear the end-tag pointer before popping or an
       element whose name matches that leftover gets a span the source never wrote. */
    tree->closing_end_tag = NULL;
    while (tree->open_len > 0) {
        stack_pop(tree);
    }
    select_cache_finish(tree);
    select_cache_free(tree);
}

/* Feed the input to the tokenizer (borrowing when there is no CR to normalize)
   and point tree->data at the tokenizer's authoritative input base. */
static int setup_input(th_tree *tree, th_tokenizer *sm, int kind, const void *data, Py_ssize_t length) {
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
    }
    tree->can_span = 1;
    th_tok_close(sm);
    tree->data = th_tok_input_data(sm, &tree->kind);
    return has_cr;
}

static void retain_normalized_source(th_tree *tree, th_tokenizer *sm, int normalized) {
    if (normalized) {
        tree->owned_data = th_tok_take_input(sm, &tree->kind, &tree->length);
        tree->data = tree->owned_data;
    }
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
static th_node *make_named(th_tree *tree, uint16_t atom, uint8_t flags) {
    th_node *node = node_new(tree, TH_NODE_ELEMENT);
    if (node == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    node->atom = atom;
    node->tag_flags = flags | TH_ELEM_IMPLIED;
    node->text = (Py_UCS4 *)th_tag_wide_name(atom);
    node->text_len = th_tag_table[atom - 1].name_len;
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
        head = make_named(tree, TH_TAG_HEAD, TH_TAG_SPECIAL);
        if (head == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return;         /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        }
        /* the implied head is appended, after any comments already in html */
        node_append(html, head);
    }
    if (child_with_atom(html, TH_TAG_BODY) == NULL) {
        th_node *body = make_named(tree, TH_TAG_BODY, TH_TAG_SPECIAL);
        if (body == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return;         /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        }
        node_append(html, body);
    }
}

/* The adoption agency and a selectedcontent copy drop the nest bound, since they can nest a node deeper than its start
   tag would. Measure the finished document once instead, so that a page with no node TH_DEEP_NEST deep keeps
   TH_NEST_CAPPED for its selector queries. */
static void bound_parsed_nesting(th_tree *tree) {
    th_node *const root = tree->document;
    th_node *node = root;
    Py_ssize_t ancestors = 0; /* of node's children */
    for (;;) {
        if (node->first_child != NULL) {
            if (++ancestors >= TH_DEEP_NEST) {
                return;
            }
            node = node->first_child;
            continue;
        }
        while (node != root && node->next_sibling == NULL) {
            node = node->parent;
            ancestors--;
        }
        if (node == root) {
            break;
        }
        node = node->next_sibling;
    }
    tree->nest_bound = TH_NEST_CAPPED;
}

/* At EOF a document always has html, head and body (the missing-element rules of
   the early insertion modes), so synthesize any the parse left out. */
static void finalize_document(th_tree *tree) {
    th_node *html = child_with_atom(tree->document, TH_TAG_HTML);
    if (html == NULL) {
        html = make_named(tree, TH_TAG_HTML, TH_TAG_SPECIAL | TH_TAG_SCOPING);
        if (html == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return;         /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        }
        node_append(tree->document, html);
    }
    ensure_head_body(tree, html);
    if (tree->nest_bound == 0) {
        bound_parsed_nesting(tree);
    }
}

/* Allocate a fresh document tree ready for token-driven construction (the shared
   start of one-shot and streaming parses). positions enables per-element source
   line/col tracking; locations adds the granular spans and implies positions. NULL
   on allocation failure. */
static th_tree *tree_new_document(int positions, int locations) {
    th_tree *tree = PyMem_Calloc(1, sizeof(th_tree));
    if (tree == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    tree->nest_bound = TH_HAS_MEMO_DEPTH | TH_NEST_CAPPED;
    tree->track_positions = positions || locations; /* set before any element node_new */
    tree->track_locations = locations;
    tree->document = node_new(tree, TH_NODE_DOCUMENT);
    tree->sink = node_new(tree, TH_NODE_ELEMENT);
    if (tree->document == NULL || tree->sink == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        th_tree_free(tree);                             /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;                                    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    tree->frameset_ok = 1;
    return tree;
}

th_tree *th_tree_parse(int kind, const void *data, Py_ssize_t length, int positions, int locations, int scripting,
                       int declarative_shadow) {
    th_tree *tree = tree_new_document(positions, locations);
    if (tree == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    tree->scripting = scripting;
    tree->declarative_shadow = declarative_shadow;
    tree->kind = kind;
    tree->data = data;
    tree->length = length;

    th_tokenizer *sm = th_tok_new();
    if (sm == NULL) {       /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(tree); /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    th_tok_set_error_sink(sm, &tree->errors);
    th_tok_capture_locations(sm, locations);
    int normalized = setup_input(tree, sm, kind, data, length);
    th_run_state run_state;
    run_state_init(&run_state, M_INITIAL);
    run_drain(tree, sm, &run_state);
    run_close(tree);
    retain_normalized_source(tree, sm, normalized);
    th_tok_free(sm);
    finalize_document(tree);

    if (tree->failed) {     /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(tree); /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    return tree;
}

th_tree *th_tree_parse_noscript(int kind, const void *data, Py_ssize_t length, th_noscript_track *track) {
    th_tree *tree = tree_new_document(1, 0);
    if (tree == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    tree->declarative_shadow = 1;
    tree->kind = kind;
    tree->data = data;
    tree->length = length;
    th_tokenizer *sm = th_tok_new();
    if (sm == NULL) {       /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(tree); /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    th_tok_set_error_sink(sm, &tree->errors);
    int normalized = setup_input(tree, sm, kind, data, length);
    track->open = -1;
    track->frameset_span = -1;
    th_run_state run_state;
    run_state_init(&run_state, M_INITIAL);
    run_drain_with(tree, sm, &run_state, track);
    run_close(tree);
    track->open = -1; /* EOF closed it, which no raw-text reading matches: it stays unstable */
    for (Py_ssize_t index = 0; index < track->count; index++) {
        th_noscript_span *span = &track->spans[index];
        span->cut &= (span->html->attr_count == span->html_attrs) &
                     (span->body == NULL || span->body->attr_count == span->body_attrs);
    }
    retain_normalized_source(tree, sm, normalized);
    th_tok_free(sm);
    finalize_document(tree);
    if (tree->failed || track->failed) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(tree);              /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;                     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return tree;
}

void th_noscript_track_free(th_noscript_track *track) {
    PyMem_Free(track->spans);
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

int th_annotation_xml_is_html(const th_node *node) {
    return is_html_annotation_xml(node);
}

th_tree *th_tree_parse_fragment(int kind, const void *data, Py_ssize_t length, const char *context,
                                Py_ssize_t context_len, int positions, int locations, int scripting,
                                int declarative_shadow) {
    return th_tree_parse_fragment_ex(kind, data, length, context, context_len, positions, locations, scripting,
                                     declarative_shadow, 0);
}

th_tree *th_tree_parse_fragment_ex(int kind, const void *data, Py_ssize_t length, const char *context,
                                   Py_ssize_t context_len, int positions, int locations, int scripting,
                                   int declarative_shadow, int html_integration) {
    th_tree *tree = PyMem_Calloc(1, sizeof(th_tree));
    if (tree == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    tree->scripting = scripting;
    tree->declarative_shadow = declarative_shadow;
    tree->kind = kind;
    tree->data = data;
    tree->length = length;
    tree->track_positions = positions || locations; /* set before any element node_new */
    tree->track_locations = locations;
    tree->document = node_new(tree, TH_NODE_DOCUMENT);
    tree->sink = node_new(tree, TH_NODE_ELEMENT);
    if (tree->document == NULL || tree->sink == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        th_tree_free(tree);                             /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;                                    /* GCOVR_EXCL_LINE: allocation-failure path */
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
    /* the root carries the context's element name, which for SVG takes the same mixed case a parsed element gets */
    const char *mixed = ctx_ns == TH_NS_SVG ? svg_adjust_name(lower, lower_len) : NULL;
    const char *root_name = mixed != NULL ? mixed : lower;
    for (Py_ssize_t index = 0; index < lower_len; index++) {
        root->text[index] = (Py_UCS4)(unsigned char)root_name[index];
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
        root->html_integration = html_integration;
    }

    th_tokenizer *sm = th_tok_new();
    if (sm == NULL) {       /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(tree); /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    th_tok_capture_locations(sm, locations);
    /* the context element's content model selects the tokenizer's start state */
    int model = ctx_ns == TH_NS_HTML ? content_model_for(ctx_atom, ctx_flags, tree->scripting) : -1;
    if (model >= 0) {
        th_tok_set_initial(sm, (enum th_initial_state)model, NULL, 0);
    }
    int normalized = setup_input(tree, sm, kind, data, length);
    th_run_state run_state;
    run_state_init(&run_state, ctx_ns == TH_NS_HTML ? fragment_mode(ctx_atom) : M_IN_BODY);
    run_drain(tree, sm, &run_state);
    run_close(tree);
    retain_normalized_source(tree, sm, normalized);
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
    if (tree == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure before tree creation */
        return;         /* GCOVR_EXCL_LINE */
    }
    xp_ns_index_free(tree->ns_index);
    arena_block *block = tree->arena;
    while (block != NULL) {
        arena_block *next = block->next;
#ifdef FUZZING_BUILD_MODE_UNSAFE_FOR_PRODUCTION
        /* pymalloc hands a small freed block to its next caller without clearing the poison */
        TH_FUZZ_UNPOISON(block->data, (size_t)block->cap);
#endif
        PyMem_Free(block);
        block = next;
    }
    PyMem_Free(tree->open);
    PyMem_Free(tree->open_blocks);
    PyMem_Free(tree->afe);
    PyMem_Free(tree->afe_hashes);
    PyMem_Free(tree->tmpl);
    PyMem_Free(tree->attr_slots);
    PyMem_Free(tree->attr_recs);
    PyMem_Free(tree->shadows);
    PyMem_Free(tree->shadow_index.entries);
    select_cache_free(tree);
    PyMem_Free(tree->meta_labels);
    PyMem_Free(tree->owned_data);
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
    /* The preprocessing errors, swept per chunk because no chunk outlives the feed that brought it, and merged into
       the tree's tokenizer errors at finish the way th_tree_ensure_input_errors does for a whole buffer. */
    th_error_sink preprocessing;
    th_input_scan scan;
};

th_stream *th_stream_new(int positions, int locations) {
    th_stream *stream = PyMem_Calloc(1, sizeof(th_stream));
    if (stream == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    stream->tree = tree_new_document(positions, locations);
    stream->sm = th_tok_new();
    if (stream->tree == NULL || stream->sm == NULL) { /* GCOVR_EXCL_BR_LINE: alloc cannot be forced */
        th_stream_free(stream);                       /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;                                  /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    stream->tree->declarative_shadow = 1; /* a streaming document parse navigates like the browser */
    th_tok_set_error_sink(stream->sm, &stream->tree->errors);
    th_input_scan_init(&stream->scan);
    th_tok_capture_locations(stream->sm, locations);
    run_state_init(&stream->run_state, M_INITIAL);
    return stream;
}

/* Point the tree's slice base at the tokenizer's current input storage, which a
   feed may have moved by compaction or reallocation. Slice tokens emitted during
   the drain that follows resolve against this base before the next th_tok_next. */
static void stream_sync_input(th_stream *stream) {
    stream->tree->data = th_tok_input_data(stream->sm, &stream->tree->kind);
}

int th_stream_feed(th_stream *stream, int kind, const void *data, Py_ssize_t length) {
    if (!stream->tree->has_nul) {
        stream->tree->has_nul = chunk_has_nul(kind, data, length);
    }
    th_input_stream_errors_chunk(&stream->scan, kind, data, length, &stream->preprocessing);
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
    /* the source locations index the uncompacted input, and to_source() reads it after the tokenizer is gone */
    retain_normalized_source(stream->tree, stream->sm, stream->tree->track_locations);
    finalize_document(stream->tree);
    stream->tree->failed |= th_error_sink_merge(&stream->tree->errors, &stream->preprocessing) < 0;
    th_error_sink_free(&stream->preprocessing);
    if (stream->tree->failed) { /* GCOVR_EXCL_BR_LINE: only an allocation failure sets failed */
        return NULL;            /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_tree *tree = stream->tree;
    stream->tree = NULL; /* the caller owns the tree now; th_stream_free must not touch it */
    return tree;
}

void th_stream_free(th_stream *stream) {
    th_error_sink_free(&stream->preprocessing); /* a stream abandoned before finish still owns the errors it swept */
    if (stream->sm != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on a partially-built stream from th_stream_new */
        th_tok_free(stream->sm);
    }
    if (stream->tree != NULL) { /* still owned: a stream freed before finish, or a failed allocation */
        th_tree_free(stream->tree);
    }
    PyMem_Free(stream);
}

/* A document tree built alongside another consumer's tokenizer: tree construction runs on the tokens that consumer
   pulls and steers its tokenizer as it would its own, so the consumer reads the input exactly as a parse does. While
   the consumer reads on without it, the tree waits at `synced`, and th_shadow_catch_up feeds it the input it missed. */
struct th_shadow {
    th_tree *tree;
    th_insert dc;
    th_tokenizer *sm;
    Py_ssize_t synced; /* input offset the tree has read up to, at line:col */
    Py_ssize_t line;
    Py_ssize_t col;
    arena_block *counted; /* the newest arena block already in allocated */
    Py_ssize_t allocated; /* arena bytes the tree holds */
    Py_ssize_t budget;    /* allocated past which th_shadow_compact copies the tree */
    th_node gone;         /* stands in for an element the tree let go of: it has left the stack */
    int mirror;           /* sm is the shadow's own, fed by th_shadow_mirror_feed, and its input moves */
    int built;            /* the last token th_shadow_step read built a node */
    int discarded;        /* the last token th_shadow_step read detached the body, as a frameset replacing it does */
    int body_open;        /* the tree has opened a body, by the token at body_line:body_col */
    Py_ssize_t body_line;
    Py_ssize_t body_col;
};

/* Arena growth a shadow tree may reach before its first copy, and on top of three times what the last copy kept. */
#define SHADOW_SLACK ((Py_ssize_t)256 * 1024)
/* Set on an element of the tree being copied once it has its copy, which prev_sibling then points at. */
#define SHADOW_COPIED 0x80

static th_tree *shadow_tree(th_tokenizer *sm, int mirror) {
    th_tree *tree = tree_new_document(1, 0); /* the start tag positions pair the elements with the consumer's */
    if (tree == NULL) {                      /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;                         /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    /* the settings of a document parse that runs scripts, as a browser does */
    tree->scripting = 1;
    tree->declarative_shadow = 1;
    /* the consumer's input is final and outlives the tree, so text can stay a span of it, but a mirror's tokenizer
       drops what it has read on the next feed; any slice may hold a NUL */
    tree->data = th_tok_input_data(sm, &tree->kind);
    tree->can_span = !mirror;
    tree->has_nul = 1;
    return tree;
}

static void shadow_count_arena(th_shadow *shadow) {
    for (arena_block *block = shadow->tree->arena; block != shadow->counted; block = block->next) {
        shadow->allocated += block->cap;
    }
    shadow->counted = shadow->tree->arena;
}

static th_shadow *shadow_new(th_tokenizer *sm, int mirror) {
    th_shadow *shadow = PyMem_Calloc(1, sizeof(th_shadow));
    if (shadow == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    shadow->mirror = mirror;
    shadow->tree = shadow_tree(sm, mirror);
    if (shadow->tree == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyMem_Free(shadow);     /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;            /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    shadow->sm = sm;
    shadow->dc = (th_insert){
        .sm = sm,
        .mode = M_INITIAL,
        .original_mode = M_IN_BODY,
        .foster_return = M_IN_TABLE,
        .table_origin = M_IN_TABLE,
    };
    shadow->line = 1;
    shadow->gone.left_stack = 1;
    shadow_count_arena(shadow);
    shadow->budget = shadow->allocated + SHADOW_SLACK;
    return shadow;
}

th_shadow *th_shadow_new(th_tokenizer *sm) {
    return shadow_new(sm, 0);
}

static void shadow_take(th_shadow *shadow, th_token *token) {
    th_tree *tree = shadow->tree;
    drain_token(tree, &shadow->dc, token);
    tree->closing_end_tag = NULL;
    tree->errors.len = 0; /* nobody reads the shadow's parse errors, and keeping them would grow with the document */
    if (!shadow->body_open && tree->open_len > 1 && tree->open[1]->atom == TH_TAG_BODY) {
        shadow->body_open = 1;
        shadow->body_line = token->line;
        shadow->body_col = token->col;
    }
}

/* Copy one node of src into dest, under parent when it has one. A template's content goes with it: a content
   fragment as its child, the shadow root of a declarative one parentless, as the tree builder makes them. */
static th_node *shadow_copy(th_tree *dest, th_tree *src, th_node *node, th_node *parent) {
    th_node *copy = th_tree_copy_node_shallow(dest, src, node);
    if (copy == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    copy->html_integration = node->html_integration;
    copy->afe_listed = node->afe_listed;
    copy->left_stack = node->left_stack;
    if (node->type == TH_NODE_ELEMENT) {
        node_pos(copy)[0] = node_pos(node)[0];
        node_pos(copy)[1] = node_pos(node)[1];
    }
    node->left_stack |= SHADOW_COPIED;
    node->prev_sibling = copy;
    if (parent != NULL) {
        node_append(parent, copy);
    }
    if (node->ns == TH_NS_HTML && node->atom == TH_TAG_TEMPLATE) { /* any other node is TH_TAG_UNKNOWN */
        th_node *content = node->first_child;
        int attached = content->parent == node;
        th_node *inner = shadow_copy(dest, src, content, attached ? copy : NULL);
        if (inner == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (!attached) {
            copy->first_child = inner;
            copy->last_child = inner;
        }
    }
    return copy;
}

/* The copy of node, made with its ancestors when it has none yet. */
static th_node *shadow_copy_rooted(th_tree *dest, th_tree *src, th_node *node) {
    if (node->left_stack & SHADOW_COPIED) {
        return node->prev_sibling;
    }
    th_node *parent = NULL;
    if (node->parent != NULL) {
        parent = shadow_copy_rooted(dest, src, node->parent);
        if (parent == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    return shadow_copy(dest, src, node, parent);
}

static th_node *shadow_copy_detached(th_tree *dest, th_tree *src, th_node *node) {
    if (node->left_stack & SHADOW_COPIED) {
        return node->prev_sibling;
    }
    return shadow_copy(dest, src, node, NULL);
}

/* Replace the tree by a copy of what tree construction can still reach: the open elements with their ancestors, the
   active formatting elements, the head and form element pointers and the template insertion modes. Everything
   else (closed elements, text, comments) can no longer change how a later token is read. Each of the `count` node
   pointers at refs, `stride` bytes apart, is moved to its element's copy, or to a stand-in that has left the stack. */
static int shadow_compact(th_shadow *shadow, th_node **refs, Py_ssize_t count, size_t stride) {
    th_tree *src = shadow->tree;
    th_tree *dest = shadow_tree(shadow->sm, shadow->mirror);
    if (dest == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    src->document->left_stack |= SHADOW_COPIED;
    src->document->prev_sibling = dest->document;
    for (Py_ssize_t index = 0; index < src->open_len; index++) {
        TH_WORK_STEP();
        th_node *copy = shadow_copy_rooted(dest, src, src->open[index]);
        if (copy == NULL || !stack_push(dest, copy)) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            th_tree_free(dest);                        /* GCOVR_EXCL_LINE: allocation-failure path */
            return -1;                                 /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (is_html_template(copy)) {
            note_template_pushed(dest);
        }
    }
    for (Py_ssize_t index = 0; index < src->afe_len; index++) {
        TH_WORK_STEP();
        th_node *entry = src->afe[index];
        th_node *copy = entry == NULL ? NULL : shadow_copy_detached(dest, src, entry);
        if (dest->afe_len == dest->afe_cap && !afe_grow(dest)) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            th_tree_free(dest);                                  /* GCOVR_EXCL_LINE: allocation-failure path */
            return -1;                                           /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        afe_keys(dest)[dest->afe_len] = 0; /* hashed again once the hash set needs it */
        dest->afe[dest->afe_len++] = copy;
    }
    dest->head = src->head == NULL ? NULL : shadow_copy_detached(dest, src, src->head);
    dest->form = src->form == NULL ? NULL : shadow_copy_detached(dest, src, src->form);
    for (Py_ssize_t index = 0; index < src->tmpl_len; index++) {
        tmpl_push(dest, src->tmpl[index]);
    }
    dest->stack_version = src->stack_version;
    dest->scope_version = src->stack_version - 1;
    dest->open_p_count = src->open_p_count;
    dest->afe_stack_hint = -1;
    dest->frameset_ok = src->frameset_ok;
    dest->drop_newline = src->drop_newline;
    dest->foster = src->foster;
    dest->quirks = src->quirks;
    for (Py_ssize_t index = 0; index < count; index++) {
        th_node **ref = (th_node **)((char *)refs + (size_t)index * stride);
        if (*ref != NULL) {
            *ref = (*ref)->left_stack & SHADOW_COPIED ? (*ref)->prev_sibling : &shadow->gone;
        }
    }
    if (dest->failed) {     /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(dest); /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;          /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_tree_free(src);
    shadow->tree = dest;
    shadow->counted = NULL;
    shadow->allocated = 0;
    shadow_count_arena(shadow);
    shadow->budget = 3 * shadow->allocated + SHADOW_SLACK;
    return 0;
}

int th_shadow_compact(th_shadow *shadow, th_node **refs, Py_ssize_t count, size_t stride) {
    if (shadow->tree->arena != shadow->counted) {
        shadow_count_arena(shadow);
    }
    return shadow->allocated > shadow->budget ? shadow_compact(shadow, refs, count, stride) : 0;
}

/* Replay the input from the shadow's synced offset up to offset. With to_body, the replay stops after the token that
   opens a body, parked there. Returns 1 when it stopped at a body, 0 when it reached offset, or -1 on allocation
   failure. */
static int shadow_catch_up(th_shadow *shadow, Py_ssize_t offset, th_node **refs, Py_ssize_t count, size_t stride,
                           int to_body) {
    th_tree *tree = shadow->tree;
    th_tokenizer *replay = th_tok_new();
    if (replay == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    /* the tree stopped after a tag and the consumer is at one, so the replay starts and ends in the data state */
    th_tok_borrow_input(replay, tree->kind, tree->data, offset);
    th_tok_seek(replay, shadow->synced, shadow->line, shadow->col);
    th_tok_close(replay);
    TH_WORK_STEPS_ADD(offset - shadow->synced);
    shadow->dc.sm = replay;
    th_token *token;
    int failed = 0;
    int stopped = 0;
    while (th_tok_set_cdata(replay, shadow->tree->open_len > 0 && current_node(shadow->tree)->ns != TH_NS_HTML),
           th_tok_next(replay, &token) == TH_STEP_TOKEN) {
        shadow_take(shadow, token);
        /* GCOVR_EXCL_BR_START: allocation failure cannot be forced from a test */
        if (shadow->tree->failed || th_shadow_compact(shadow, refs, count, stride) < 0) {
            failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
            break;      /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        /* GCOVR_EXCL_BR_STOP */
        if (to_body && shadow->body_open) {
            th_shadow_park(shadow, token);
            stopped = 1;
            break;
        }
    }
    failed |= th_tok_failed(replay); /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
    th_tok_free(replay);
    shadow->dc.sm = shadow->sm;
    if (!stopped) {
        shadow->synced = offset;
    }
    return failed ? -1 : stopped; /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
}

int th_shadow_catch_up(th_shadow *shadow, Py_ssize_t offset, th_node **refs, Py_ssize_t count, size_t stride) {
    return shadow_catch_up(shadow, offset, refs, count, stride, 0);
}

int th_shadow_catch_body(th_shadow *shadow, Py_ssize_t offset, th_node **refs, Py_ssize_t count, size_t stride) {
    return shadow_catch_up(shadow, offset, refs, count, stride, 1);
}

int th_shadow_step(th_shadow *shadow, th_token *token) {
    th_tree *tree = shadow->tree;
    enum th_kind kind = token->kind;
    if (kind == TH_CHARREF) {
        token->kind = TH_TEXT; /* the decoded reference, as a parse reads it inside its text run */
    }
    th_node *body = tree->open_len > 1 && tree->open[1]->atom == TH_TAG_BODY ? tree->open[1] : NULL;
    uint32_t seq_before = tree->node_seq;
    shadow_take(shadow, token);
    shadow->built = tree->node_seq != seq_before;
    shadow->discarded = body != NULL && body->parent == NULL;
    token->kind = kind;
    /* the tag the shadow started at put <html> on the stack, and nothing takes it off */
    th_tok_set_cdata(shadow->sm, current_node(tree)->ns != TH_NS_HTML);
    return tree->failed ? -1 : 0; /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
}

int th_shadow_built(const th_shadow *shadow) {
    return shadow->built;
}

int th_shadow_discarded(const th_shadow *shadow) {
    return shadow->discarded;
}

int th_shadow_body(const th_shadow *shadow, Py_ssize_t *line, Py_ssize_t *col) {
    *line = shadow->body_line;
    *col = shadow->body_col;
    return shadow->body_open;
}

int th_shadow_parkable(const th_shadow *shadow) {
    switch (shadow->dc.mode) {
    case M_TEXT:
    case M_IN_FRAMESET:
    case M_AFTER_FRAMESET:
    case M_AFTER_AFTER_FRAMESET:
        return 0;
    default:
        /* the tokenizer is in its data state: a <plaintext> never ends what kept the tree from parking */
        return shadow->tree->tmpl_len == 0;
    }
}

void th_shadow_park(th_shadow *shadow, const th_token *token) {
    int kind;
    const void *data = th_tok_input_data(shadow->sm, &kind);
    Py_ssize_t line = token->line;
    Py_ssize_t col = token->col;
    Py_ssize_t end = token->src_off + token->src_span;
    for (Py_ssize_t index = token->src_off; index < end; index++) {
        if (PyUnicode_READ(kind, data, index) == '\n') {
            line++;
            col = 0;
        } else {
            col++;
        }
    }
    shadow->synced = end;
    shadow->line = line;
    shadow->col = col;
}

th_node *const *th_shadow_open(const th_shadow *shadow, Py_ssize_t *count) {
    *count = shadow->tree->open_len;
    return shadow->tree->open;
}

void th_shadow_free(th_shadow *shadow) {
    if (shadow == NULL) {
        return;
    }
    if (shadow->mirror) {
        th_tok_free(shadow->sm);
    }
    th_tree_free(shadow->tree);
    PyMem_Free(shadow);
}

th_shadow *th_shadow_mirror_new(void) {
    th_tokenizer *sm = th_tok_new();
    if (sm == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;  /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_shadow *mirror = shadow_new(sm, 1);
    if (mirror == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        /* GCOVR_EXCL_START: allocation-failure path */
        th_tok_free(sm); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    /* GCOVR_EXCL_STOP */
    return mirror;
}

int th_shadow_mirror_feed(th_shadow *mirror, const Py_UCS4 *data, Py_ssize_t length, th_node **refs, Py_ssize_t count,
                          size_t stride) {
    th_tok_feed(mirror->sm, PyUnicode_4BYTE_KIND, data, length);
    mirror->tree->data = th_tok_input_data(mirror->sm, &mirror->tree->kind);
    th_token *token;
    while (th_tok_set_cdata(mirror->sm, th_shadow_foreign(mirror)), th_tok_next(mirror->sm, &token) == TH_STEP_TOKEN) {
        shadow_take(mirror, token);
        /* GCOVR_EXCL_BR_START: allocation failure cannot be forced from a test */
        if (mirror->tree->failed || (count >= 0 && th_shadow_compact(mirror, refs, count, stride) < 0)) {
            return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        /* GCOVR_EXCL_BR_STOP */
    }
    return th_tok_failed(mirror->sm) ? -1 : 0; /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
}

enum th_initial_state th_shadow_mirror_model(const th_shadow *mirror, const th_buf **end_tag) {
    return th_tok_content_model(mirror->sm, end_tag);
}

int th_shadow_foreign(const th_shadow *shadow) {
    return shadow->tree->open_len > 0 && shadow->tree->open[shadow->tree->open_len - 1]->ns != TH_NS_HTML;
}
