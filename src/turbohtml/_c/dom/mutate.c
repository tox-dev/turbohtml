/* Build and edit the node tree by hand: create elements/text/comments and
   rearrange them, the C side of the construction and mutation API.

   These functions back the mutable Python Node API (dom/node.c, dom/element.c):
   th_tree_new starts an empty arena-owned tree, the th_tree_make_* builders add
   nodes, and the th_node_* primitives set data, edit attributes, and relink the
   tree. They share the arena, node allocator and sibling linkers with the parser
   (tree.c) through dom/tree_internal.h, so a hand-built node is indistinguishable
   from a parsed one. */

#include "dom/tree.h"
#include "dom/tree_internal.h" /* arena_alloc, need_text, node_new, node_append/remove/insert_before, intern_attr_dynamic */
#include "dom/observe.h"       /* th_mo_* mutation-record hooks */
#include "dom/verify.h"

#include "core/ascii.h" /* lower_ascii for the foreign case-insensitive attribute scan */
#include "core/vec.h"   /* th_grow_cap for the shadow-table growth */
#include "core/work_steps.h"
#include "tokenizer/xml_names.h"

#include <string.h>

#if defined(_MSC_VER)
#define TH_NOINLINE __declspec(noinline)
#elif defined(__GNUC__) || defined(__clang__)
#define TH_NOINLINE __attribute__((noinline))
#else
#define TH_NOINLINE
#endif

/* An empty tree for programmatically constructed nodes: the arena grows on the
   first allocation and can_span stays 0, so a node's text is always owned rather
   than a borrowed span. */
th_tree *th_tree_new(void) {
    return PyMem_Calloc(1, sizeof(th_tree));
}

void th_tree_set_quirks(th_tree *tree, int quirks) {
    tree->quirks = quirks;
}

int th_tree_is_xml(const th_tree *tree) {
    return tree->xml;
}

int th_tree_has_xml_ns(const th_tree *tree) {
    return tree->has_xml_ns;
}

void th_tree_set_xml(th_tree *tree, int xml) {
    tree->xml = xml;
    tree->has_xml_ns = 0;
}

/* Construct an empty document-fragment node in tree's arena: the container the
   Range content operations (dom/range.c) fill and hand back. */
th_node *th_tree_make_fragment(th_tree *tree) {
    return node_new(tree, TH_NODE_CONTENT);
}

/* Shadow DOM linkage. A shadow root is a fragment node flagged TH_SHADOW_ROOT and held
   off the light tree; the per-tree shadow table is the only path from a host to its
   shadow root and back. The slot-assignment and flattened-tree algorithms that read
   these live in dom/shadow.c, which reaches the table only through these accessors. */
int th_node_is_shadow_root(const th_node *node) {
    return node->type == TH_NODE_CONTENT && (node->tag_flags & TH_SHADOW_ROOT) != 0;
}

int th_shadow_mode(const th_node *root) {
    return (root->tag_flags & TH_SHADOW_CLOSED) != 0;
}

th_node *th_element_attach_shadow(th_tree *tree, th_node *host, int mode) {
    th_node *root = th_tree_make_fragment(tree);
    if (root == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    root->tag_flags = (uint8_t)(TH_SHADOW_ROOT | (mode ? TH_SHADOW_CLOSED : 0u));
    if (tree->shadow_count == tree->shadow_cap) {
        size_t cap, bytes;
        /* the count cannot overflow size_t, so the grow guard never trips */
        int fits = th_grow_cap((size_t)tree->shadow_count + 1, (size_t)tree->shadow_cap, 4, sizeof(th_shadow_link),
                               &cap, &bytes);
        if (!fits) {     /* GCOVR_EXCL_BR_LINE: overflow-guard path, unreachable from a test */
            return NULL; /* GCOVR_EXCL_LINE: overflow-guard path */
        }
        th_shadow_link *grown = PyMem_Realloc(tree->shadows, bytes);
        if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        tree->shadows = grown;
        tree->shadow_cap = (Py_ssize_t)cap;
    }
    if (th_node_map_reserve(&tree->shadow_index, tree->shadow_index.count + 2) < 0) { /* GCOVR_EXCL_BR_LINE: OOM */
        return NULL;                                                                  /* GCOVR_EXCL_LINE */
    }
    (void)th_node_map_insert(&tree->shadow_index, host, tree->shadow_count + 1);
    (void)th_node_map_insert(&tree->shadow_index, root, tree->shadow_count + 1);
    tree->shadows[tree->shadow_count].host = host;
    tree->shadows[tree->shadow_count].root = root;
    tree->shadow_count++;
    return root;
}

th_node *th_element_shadow_root(th_tree *tree, th_node *host) {
    if (host->type != TH_NODE_ELEMENT) {
        return NULL;
    }
    Py_ssize_t index = th_node_map_find(&tree->shadow_index, host);
    return index == 0 ? NULL : tree->shadows[index - 1].root;
}

th_node *th_shadow_host(th_tree *tree, th_node *root) {
    return tree->shadows[th_node_map_find(&tree->shadow_index, root) - 1].host;
}

/* Materialize a character-data node's text in place (a parsed text node borrows a
   source span until realized) and return the owned code-point buffer, so the Range
   operations can slice node->text directly. */
Py_UCS4 *th_node_realize_text(th_tree *tree, th_node *node) {
    return need_text(tree, node);
}

const Py_UCS4 th_empty_text[1] = {0};

/* Construct a text/comment/doctype node owning a copy of data in tree's arena. */
th_node *th_tree_make_data_node(th_tree *tree, int type, const Py_UCS4 *data, Py_ssize_t len) {
    th_node *node = node_new(tree, (enum th_node_type)type);
    if (node == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    node->text = (Py_UCS4 *)th_empty_text;
    if (len > 0) {
        Py_UCS4 *owned = arena_alloc(tree, len * (Py_ssize_t)sizeof(Py_UCS4));
        if (owned == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        memcpy(owned, data, (size_t)len * sizeof(Py_UCS4));
        node->text = owned;
        node->text_len = len;
    }
    return node;
}

TH_NOINLINE int th_pi_target_invalid_xml(const Py_UCS4 *target, Py_ssize_t target_len) {
    if (target_len == 3 && (target[0] | 0x20) == 'x' && (target[1] | 0x20) == 'm' && (target[2] | 0x20) == 'l') {
        return 1;
    }
    if (target[0] == ':' || !is_name_start(target[0])) {
        return 1;
    }
    for (Py_ssize_t index = 1; index < target_len; index++) {
        if (target[index] == ':' || !is_name_char(target[index])) {
            return 1;
        }
    }
    return 0;
}

/* Construct a processing-instruction node. The target, a space, and the data are
   packed into one buffer (target_len marks the split, kept in attr_count) so the
   node carries both halves; serialization writes "<?" + buffer + ">". */
th_node *th_tree_make_pi(th_tree *tree, const Py_UCS4 *target, Py_ssize_t target_len, const Py_UCS4 *data,
                         Py_ssize_t data_len) {
    th_node *node = node_new(tree, TH_NODE_PI);
    if (node == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t total = target_len + 1 + data_len;
    Py_UCS4 *owned = arena_alloc(tree, total * (Py_ssize_t)sizeof(Py_UCS4));
    if (owned == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    memcpy(owned, target, (size_t)target_len * sizeof(Py_UCS4));
    owned[target_len] = ' ';
    memcpy(owned + target_len + 1, data, (size_t)data_len * sizeof(Py_UCS4));
    node->text = owned;
    node->text_len = total;
    node->attr_count = target_len;
    if (th_pi_target_invalid_xml(target, target_len)) {
        node->tag_flags = TH_PI_XML_INVALID;
    }
    return node;
}

/* Intern a UTF-8 attribute name to its atom (static table, else the tree's
   dynamic table), the construction-side counterpart of intern_attr. Forced into node_attr_store, which the XML
   builder calls for every attribute; th_attr_intern_utf8 leaves the other callers to the inliner. */
static TH_TREE_INLINE uint32_t th_attr_intern_utf8_inline(th_tree *tree, const char *bytes, Py_ssize_t len) {
    uint32_t atom = th_attr_atom(bytes, (size_t)len);
    if (atom != TH_ATTR_UNKNOWN) {
        return atom;
    }
    return intern_attr_dynamic(tree, bytes, len);
}

static uint32_t th_attr_intern_utf8(th_tree *tree, const char *bytes, Py_ssize_t len) {
    return th_attr_intern_utf8_inline(tree, bytes, len);
}

static int is_xml_namespace_decl(const char *name, Py_ssize_t len) {
    return (len == 5 && memcmp(name, "xmlns", 5) == 0) || (len > 6 && memcmp(name, "xmlns:", 6) == 0);
}

/* Construct an element node, with attr_count empty attribute slots to fill with
   th_tree_set_attr. A NULL known tag selects the generated immutable spelling. */
int th_node_rename(th_tree *tree, th_node *node, const Py_UCS4 *tag, Py_ssize_t tag_len, uint16_t atom) {
    if (tag != NULL) {
        Py_UCS4 *owned = arena_alloc(tree, tag_len * (Py_ssize_t)sizeof(Py_UCS4));
        if (owned == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        memcpy(owned, tag, (size_t)tag_len * sizeof(Py_UCS4));
        node->text = owned;
        node->text_len = tag_len;
    } else {
        node->text = (Py_UCS4 *)th_tag_wide_name(atom);
        node->text_len = th_tag_table[atom - 1].name_len;
    }
    node->atom = atom;
    node->css_name_safety = 0;
    node->xml_name_wellformed = 0;
    th_src_loc *loc = tree->track_locations ? *node_loc(node) : NULL;
    if (loc != NULL) {
        loc->start_dirty = 1;
        loc->end_dirty = loc->has_end_tag;
    }
    /* the category bits follow the new atom; the bits recording how the source opened
       and closed the element stay, as its position in the source is unchanged */
    node->tag_flags = th_tag_flags(atom) | (node->tag_flags & (TH_ELEM_CLOSED_BY_END_TAG | TH_ELEM_IMPLIED));
    return 0;
}

th_node *th_tree_make_element(th_tree *tree, const Py_UCS4 *tag, Py_ssize_t tag_len, uint16_t atom,
                              Py_ssize_t attr_count) {
    th_node *node = node_new(tree, TH_NODE_ELEMENT);
    if (node == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    node->atom = atom;
    node->tag_flags = th_tag_flags(atom); /* so a constructed/unpickled raw-text element serializes literally */
    /* A built element has no source: its markup is its serialization, which closes every non-void element, so escape
       mode must reproduce that end tag. The XML parser, the one caller that reads source, records its own. */
    if (!is_void_atom(atom)) {
        node->tag_flags |= TH_ELEM_CLOSED_BY_END_TAG;
    }
    if (atom == TH_TAG_UNKNOWN || tag != NULL) {
        Py_UCS4 *owned = arena_alloc(tree, tag_len * (Py_ssize_t)sizeof(Py_UCS4));
        if (owned == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        memcpy(owned, tag, (size_t)tag_len * sizeof(Py_UCS4));
        node->text = owned;
        node->text_len = tag_len;
    } else {
        node->text = (Py_UCS4 *)th_tag_wide_name(atom);
        node->text_len = th_tag_table[atom - 1].name_len;
    }
    if (attr_count > 0) {
        node->attrs = arena_alloc(tree, attr_count * (Py_ssize_t)sizeof(th_node_attr));
        if (node->attrs == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;           /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        memset(node->attrs, 0, (size_t)attr_count * sizeof(th_node_attr));
        node->attr_count = attr_count;
    }
    return node;
}

/* Fill the name of attribute slot index on a constructed element. Returns 0, or -1 on allocation failure. */
int th_tree_set_attr_name(th_tree *tree, th_node *node, Py_ssize_t index, const char *name, Py_ssize_t name_len) {
    th_node_attr *attr = &node->attrs[index];
    attr->name_atom = th_attr_intern_utf8(tree, name, name_len);
    if (attr->name_atom == TH_ATTR_UNKNOWN) { /* GCOVR_EXCL_BR_LINE: interning fails only to allocate */
        return -1;                            /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (tree->xml && is_xml_namespace_decl(name, name_len)) {
        tree->has_xml_ns = 1;
    }
    return 0;
}

/* Fill the value of attribute slot index whose name is set. has_value 0 makes a valueless attribute (value NULL);
   otherwise the value is owned in the arena, with an empty value kept distinct from a valueless one. */
int th_tree_set_attr_value(th_tree *tree, th_node *node, Py_ssize_t index, const Py_UCS4 *value, Py_ssize_t value_len,
                           int has_value) {
    if (!has_value) {
        return 0;
    }
    th_node_attr *attr = &node->attrs[index];
    Py_UCS4 *owned = arena_alloc(tree, (value_len ? value_len : 1) * (Py_ssize_t)sizeof(Py_UCS4));
    if (owned == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    memcpy(owned, value, (size_t)value_len * sizeof(Py_UCS4));
    attr->value = owned;
    attr->value_len = value_len;
    return 0;
}

/* Fill attribute slot index on a constructed element (see th_tree_set_attr_name and th_tree_set_attr_value). */
int th_tree_set_attr(th_tree *tree, th_node *node, Py_ssize_t index, const char *name, Py_ssize_t name_len,
                     const Py_UCS4 *value, Py_ssize_t value_len, int has_value) {
    int rc = th_tree_set_attr_name(tree, node, index, name, name_len);
    if (rc < 0) {  /* GCOVR_EXCL_BR_LINE: interning fails only to allocate */
        return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return th_tree_set_attr_value(tree, node, index, value, value_len, has_value);
}

/* Flag a located element's start tag as changed, so the lossless serializer rewrites
   it from the current attributes rather than re-emitting the stale source span. A
   no-op unless the tree tracks source locations and the element carries one (a
   synthetic or hand-built element has none). */
static void mark_start_dirty(th_tree *tree, th_node *node) {
    if (!tree->track_locations) {
        return;
    }
    th_src_loc *loc = *node_loc(node);
    if (loc != NULL) {
        loc->start_dirty = 1;
    }
}

/* A large element keeps a hash index of its attribute names, so a write that has to find its name does not rescan the
   array: n writes, reads or removals by name on one element cost n lookups, not n * n / 2 comparisons. The array and
   the index share one arena block, and TH_ATTR_INDEXED in attr_capacity_shift marks it. The block holds a head slot,
   the attribute slots, as many spare slots, then the index. A removal near the front advances attrs by one slot
   instead of moving the attributes after it, and the head slot in front of attrs counts how far (node_attr_head). The
   spare slots keep the capacity counted from an advanced attrs inside the block, so every writer checks for room as
   on a plain array, and stays inside the block even after an editor drops TH_ATTR_INDEXED. Each entry is twice the
   attribute's slot plus one, the slot being its position plus the head, modulo the capacity: advancing attrs leaves
   the entries of the attributes it did not move as they are, and the at most capacity attributes still map to
   distinct slots. The second link of a name with an uppercase letter also carries ATTR_INDEX_FOLDED, and the slot
   records the bucket of that second link, so a removal finds the word that holds either entry by walking its chain
   from a known bucket, without hashing the name again. node_attr_find_indexed trusts the index only while its count
   equals attr_count. Removal keeps the two in step, every other editor of attrs either only lowers attr_count or works
   on an array without an index, so the index is rebuilt on the next lookup. */
#define ATTR_INDEX_MIN TH_ATTR_INDEX_MIN
#define ATTR_INDEX_MAX ((size_t)1 << 30) /* twice a position, the flag and the 1 have to fit 32 bits */
#define ATTR_INDEX_FOLDED 1u
#define ATTR_INDEX_STALE UINT32_MAX
#define ATTR_INDEX_UNLINKED UINT32_MAX /* the folded bucket of a slot whose name has no folded entry */

/* The index code runs only on elements with ATTR_INDEX_MIN or more attributes, so GCC compiles it once instead of
   cloning it per call site: the clones grew the extension past LTO's unit-wide inlining limit, which then dropped
   inlines in unrelated hot code such as the XPath evaluator. */
#if defined(__GNUC__) && !defined(__clang__)
#define ATTR_INDEX_FN static __attribute__((noinline, noclone))
#else
#define ATTR_INDEX_FN static TH_NOINLINE
#endif

typedef struct {
    uint32_t count;   /* attributes indexed, or ATTR_INDEX_STALE */
    uint32_t links[]; /* per slot: two bucket heads, then two chain links, then one folded bucket */
} attr_index;

/* How many slots removals have advanced attrs past the first attribute slot of an indexed array's block. */
static inline uint32_t node_attr_head(const th_node *node) {
    return node->attrs[-1].name_atom;
}

static inline attr_index *node_attr_index(const th_node *node) {
    return (attr_index *)(node->attrs - node_attr_head(node) + 2 * th_node_attr_capacity(node));
}

/* The index slot of the attribute at position. */
static inline size_t node_attr_slot(const th_node *node, size_t position) {
    return (node_attr_head(node) + position) & (th_node_attr_capacity(node) - 1);
}

/* The position of the attribute whose index entry is entry. */
static inline Py_ssize_t node_attr_entry_position(const th_node *node, uint32_t entry) {
    return (Py_ssize_t)(((entry - 1) / 2 - node_attr_head(node)) & (th_node_attr_capacity(node) - 1));
}

/* The word of links that holds entry, found by walking the chain that starts at bucket. */
static inline uint32_t *attr_index_holder(uint32_t *links, size_t buckets, size_t bucket, uint32_t entry) {
    uint32_t *holder = &links[bucket];
    while (*holder != entry) {
        holder = &links[buckets + *holder - 1];
    }
    return holder;
}

/* Take the entries of the attribute in slot, whose name is atom, out of their chains. */
static inline void attr_index_unlink(uint32_t *links, size_t buckets, size_t slot, uint32_t atom, int folds) {
    uint32_t *next = links + buckets;
    const uint32_t entry = (uint32_t)(2 * slot + 1);
    *attr_index_holder(links, buckets, atom & (buckets - 1), entry) = next[entry - 1];
    if (folds && next[buckets + slot] != ATTR_INDEX_UNLINKED) {
        *attr_index_holder(links, buckets, next[buckets + slot], entry + ATTR_INDEX_FOLDED) = next[entry];
    }
}

/* Hand the entries of the attribute in slot, whose name is atom, to the free slot moved, keeping their chain places. */
static inline void attr_index_move(uint32_t *links, size_t buckets, size_t slot, uint32_t atom, size_t moved,
                                   int folds) {
    uint32_t *next = links + buckets;
    const uint32_t entry = (uint32_t)(2 * slot + 1);
    const uint32_t target = (uint32_t)(2 * moved + 1);
    *attr_index_holder(links, buckets, atom & (buckets - 1), entry) = target;
    next[target - 1] = next[entry - 1];
    if (folds) {
        const uint32_t folded = next[buckets + slot];
        next[buckets + moved] = folded;
        if (folded != ATTR_INDEX_UNLINKED) {
            *attr_index_holder(links, buckets, folded, entry + ATTR_INDEX_FOLDED) = target + ATTR_INDEX_FOLDED;
            next[target] = next[entry];
        }
    }
}

/* The case-insensitive pass of th_node_attr_find applies to node, so the index chains folded names for it only. */
static inline int attr_index_folds(const th_tree *tree, const th_node *node) {
    return !tree->xml && node->ns != TH_NS_HTML;
}

/* Whether the name stored under atom is name once ASCII-lowercased. */
static inline int attr_name_folds_to(th_tree *tree, uint32_t atom, const char *name, Py_ssize_t name_len) {
    Py_ssize_t stored_len;
    const char *stored = th_attr_name(tree, atom, &stored_len);
    if (stored_len != name_len) {
        return 0;
    }
    Py_ssize_t offset = 0;
    while (offset < name_len && lower_ascii((Py_UCS4)(unsigned char)stored[offset]) == (Py_UCS4)name[offset]) {
        offset++;
    }
    return offset == name_len;
}

/* FNV-1a of a name's ASCII-lowercased bytes; *cased reports whether lowercasing changed any. */
static uint32_t attr_name_folded_hash(const char *name, Py_ssize_t name_len, int *cased) {
    uint32_t hash = 2166136261u;
    *cased = 0;
    for (Py_ssize_t offset = 0; offset < name_len; offset++) {
        const uint32_t byte = (unsigned char)name[offset];
        *cased |= lower_ascii(byte) != byte;
        hash = (hash ^ lower_ascii(byte)) * 16777619u;
    }
    return hash;
}

/* Entries go to the head of their chain, so a chain runs from the highest position down. */
ATTR_INDEX_FN void attr_index_add(th_tree *tree, const th_node *node, attr_index *index) {
    TH_WORK_STEP();
    const size_t buckets = 2 * th_node_attr_capacity(node);
    uint32_t *next = index->links + buckets;
    const size_t slot = node_attr_slot(node, index->count);
    const uint32_t entry = (uint32_t)(slot * 2 + 1);
    const uint32_t atom = node->attrs[index->count++].name_atom;
    next[entry - 1] = index->links[atom & (buckets - 1)];
    index->links[atom & (buckets - 1)] = entry;
    if (attr_index_folds(tree, node)) {
        Py_ssize_t name_len;
        const char *name = th_attr_name(tree, atom, &name_len);
        int cased;
        const size_t bucket = attr_name_folded_hash(name, name_len, &cased) & (buckets - 1);
        next[buckets + slot] = cased ? (uint32_t)bucket : ATTR_INDEX_UNLINKED;
        if (cased) {
            next[entry] = index->links[bucket];
            index->links[bucket] = entry + ATTR_INDEX_FOLDED;
        }
    }
}

/* Rebuilds a stale index first. Chains run from the highest position down, so the last match met is the lowest, the
   one a scan of the array returns. */
static inline Py_ssize_t node_attr_find_exact_indexed(th_tree *tree, const th_node *node, uint32_t atom) {
    attr_index *index = node_attr_index(node);
    const size_t buckets = 2 * th_node_attr_capacity(node);
    const uint32_t *next = index->links + buckets;
    if (index->count != (uint32_t)node->attr_count) {
        memset(index->links, 0, buckets * sizeof(uint32_t));
        index->count = 0;
        while (index->count < (uint32_t)node->attr_count) {
            attr_index_add(tree, node, index);
        }
    }
    Py_ssize_t found = -1;
    for (uint32_t entry = index->links[atom & (buckets - 1)]; entry != 0; entry = next[entry - 1]) {
        TH_WORK_STEP();
        const Py_ssize_t position = node_attr_entry_position(node, entry);
        if (((entry - 1) & ATTR_INDEX_FOLDED) == 0 && node->attrs[position].name_atom == atom) {
            found = position;
        }
    }
    return found;
}

ATTR_INDEX_FN Py_ssize_t node_attr_find_indexed(th_tree *tree, const th_node *node, const char *name,
                                                Py_ssize_t name_len, uint32_t atom) {
    Py_ssize_t found = node_attr_find_exact_indexed(tree, node, atom);
    if (found >= 0 || !attr_index_folds(tree, node)) {
        return found;
    }
    const attr_index *index = node_attr_index(node);
    const size_t buckets = 2 * th_node_attr_capacity(node);
    const uint32_t *next = index->links + buckets;
    int cased;
    const size_t bucket = attr_name_folded_hash(name, name_len, &cased) & (buckets - 1);
    for (uint32_t entry = index->links[bucket]; entry != 0; entry = next[entry - 1]) {
        TH_WORK_STEP();
        const Py_ssize_t position = node_attr_entry_position(node, entry);
        if (((entry - 1) & ATTR_INDEX_FOLDED) != 0 &&
            attr_name_folds_to(tree, node->attrs[position].name_atom, name, name_len)) {
            found = position;
        }
    }
    return found;
}

/* Move node's attributes into an array with room for one more, followed by an index when indexed is set. Returns 0, or
   -1 on allocation failure. */
static inline int node_attr_reserve(th_tree *tree, th_node *node, int indexed) {
    size_t capacity = th_node_attr_capacity(node);
    size_t bytes;
    int fits = th_grow_cap((size_t)node->attr_count + 1, capacity, 2, sizeof(th_node_attr), &capacity, &bytes);
    if (indexed) {
        /* per slot: the attribute, a spare slot for removals to advance attrs into, and five index words */
        const size_t slot_bytes = 2 * sizeof(th_node_attr) + 5 * sizeof(uint32_t);
        const size_t fixed_bytes = sizeof(th_node_attr) + sizeof(attr_index); /* the head slot and the index count */
        /* GCOVR_EXCL_BR_START: allocation-size overflow */
        fits = fits && capacity <= ATTR_INDEX_MAX && capacity <= ((size_t)PY_SSIZE_T_MAX - fixed_bytes) / slot_bytes;
        /* GCOVR_EXCL_BR_STOP */
        bytes = fixed_bytes + capacity * slot_bytes;
    }
    if (!fits || bytes > PY_SSIZE_T_MAX) { /* GCOVR_EXCL_BR_LINE: allocation-size overflow */
        PyErr_NoMemory();                  /* GCOVR_EXCL_LINE: allocation-size overflow */
        return -1;                         /* GCOVR_EXCL_LINE: allocation-size overflow */
    }
    th_node_attr *grown = arena_alloc(tree, (Py_ssize_t)bytes);
    if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (indexed) {
        grown++->name_atom = 0;
    }
    if (node->attr_count > 0) {
        memcpy(grown, node->attrs, (size_t)node->attr_count * sizeof(th_node_attr));
    }
    node->attrs = grown;
    /* th_grow_cap doubles from two slots, so the shift starts at 1 and a new element's first array takes no loop */
    uint8_t shift = 1;
    for (size_t slots = capacity; slots > 2; slots >>= 1) {
        shift++;
    }
    node->attr_capacity_shift = shift;
    if (indexed) {
        node->attr_capacity_shift = (uint8_t)(shift | TH_ATTR_INDEXED);
        node_attr_index(node)->count = ATTR_INDEX_STALE;
    }
    return 0;
}

/* Indexed growth stays out of line, so that the writers below inline only the plain growth every new element takes. */
ATTR_INDEX_FN int node_attr_reserve_indexed(th_tree *tree, th_node *node) {
    return node_attr_reserve(tree, node, 1);
}

/* The lookup of an element with ATTR_INDEX_MIN or more attributes: indexes the array on its first write, then answers
   from the index. Returns the position, -1 for a miss, or -2 on allocation failure. */
ATTR_INDEX_FN Py_ssize_t node_attr_find_large(th_tree *tree, th_node *node, const char *name, Py_ssize_t name_len,
                                              uint32_t atom) {
    if (!(node->attr_capacity_shift & TH_ATTR_INDEXED)) {
        if (node_attr_reserve_indexed(tree, node) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -2;                                   /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    return node_attr_find_indexed(tree, node, name, name_len, atom);
}

/* Shared by both writers. */
static inline uint32_t node_attr_begin(th_tree *tree, th_node *node, const char *name, Py_ssize_t name_len) {
    tree->attr_version++;
    mark_start_dirty(tree, node);
    uint32_t atom = th_attr_intern_utf8_inline(tree, name, name_len);
    tree->id_version += atom == TH_ATTR_ID;
    if (tree->slot_cache_valid) {
        tree->slot_cache_valid = (atom != TH_ATTR_SLOT) & ((atom != TH_ATTR_NAME) | (node->atom != TH_TAG_SLOT));
    }
    return atom;
}

/* Replaces the value of a present name, or appends it. Kept apart from th_node_attr_append rather than inlined into
   both: a shared store would hold the duplicate and index branches, which the appender never takes. With borrow the
   slot takes value itself, a buffer of this tree's arena that is already another element's attribute value, rather
   than a copy. */
static TH_NOINLINE int node_attr_write(th_tree *tree, th_node *node, const char *name, Py_ssize_t name_len,
                                       const Py_UCS4 *value, Py_ssize_t value_len, int has_value, int borrow) {
    uint32_t atom = node_attr_begin(tree, node, name, name_len);
    Py_UCS4 *owned = borrow ? (Py_UCS4 *)value : NULL;
    if (has_value && !borrow) {
        owned = arena_alloc(tree, (value_len ? value_len : 1) * (Py_ssize_t)sizeof(Py_UCS4));
        if (owned == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (value_len > 0) { /* an empty value may arrive as a NULL pointer */
            memcpy(owned, value, (size_t)value_len * sizeof(Py_UCS4));
        }
    }
    Py_ssize_t existing;
    /* a small array is scanned even when an index is left over from when it was large */
    if (node->attr_count >= ATTR_INDEX_MIN) {
        existing = node_attr_find_large(tree, node, name, name_len, atom);
        if (existing < -1) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    } else {
        existing = th_node_attr_find(tree, node, name, name_len);
    }
    /* testing right after interning changed how LTO inlined the lookup and cost a class edit 150 instructions */
    if (atom == TH_ATTR_UNKNOWN) { /* GCOVR_EXCL_BR_LINE: interning fails only to allocate */
        return -1;                 /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (tree->xml && is_xml_namespace_decl(name, name_len)) {
        tree->has_xml_ns = 1;
    }
    if (existing >= 0) {
        th_mo_attr_changed(tree, node, atom, node->attrs[existing].value, node->attrs[existing].value_len, 1);
        node->attrs[existing].value = owned;
        node->attrs[existing].value_len = has_value ? value_len : 0;
        return 0;
    }
    th_mo_attr_changed(tree, node, atom, NULL, 0, 0);
    /* growth keeps an array indexed or plain, so the flag read here also holds after it */
    const int indexed = node->attr_capacity_shift & TH_ATTR_INDEXED;
    if ((size_t)node->attr_count >= th_node_attr_capacity(node)) {
        const int grown = indexed ? node_attr_reserve_indexed(tree, node) : node_attr_reserve(tree, node, 0);
        if (grown < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;   /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    node->attrs[node->attr_count].name_atom = atom;
    node->attrs[node->attr_count].value = owned;
    node->attrs[node->attr_count].value_len = has_value ? value_len : 0;
    if (indexed) {
        /* an index out of step here (the array has just moved, or this is an append after a removal) is pinned stale,
           so that adding cannot bring its count level with attr_count again */
        attr_index *index = node_attr_index(node);
        if (index->count == (uint32_t)node->attr_count) {
            attr_index_add(tree, node, index);
        } else {
            index->count = ATTR_INDEX_STALE;
        }
    }
    node->attr_count++;
    return 0;
}

int th_node_attr_set(th_tree *tree, th_node *node, const char *name, Py_ssize_t name_len, const Py_UCS4 *value,
                     Py_ssize_t value_len, int has_value) {
    return node_attr_write(tree, node, name, name_len, value, value_len, has_value, 0);
}

int th_node_attr_set_shared(th_tree *tree, th_node *node, const char *name, Py_ssize_t name_len, const Py_UCS4 *value,
                            Py_ssize_t value_len) {
    return node_attr_write(tree, node, name, name_len, value, value_len, 1, 1);
}

/* out of line: inlined into the XML parser's attribute loop it cost that loop more than the call. The XML parser is the
   only appender and its tree is always XML, so the namespace check needs no tree->xml test. */
TH_NOINLINE int th_node_attr_append(th_tree *tree, th_node *node, const char *name, Py_ssize_t name_len,
                                    const Py_UCS4 *value, Py_ssize_t value_len) {
    uint32_t atom = node_attr_begin(tree, node, name, name_len);
    Py_UCS4 *owned = arena_alloc(tree, (value_len ? value_len : 1) * (Py_ssize_t)sizeof(Py_UCS4));
    if (owned == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (value_len > 0) { /* an empty value may arrive as a NULL pointer */
        memcpy(owned, value, (size_t)value_len * sizeof(Py_UCS4));
    }
    if (atom == TH_ATTR_UNKNOWN) { /* GCOVR_EXCL_BR_LINE: interning fails only to allocate */
        return -1;                 /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (is_xml_namespace_decl(name, name_len)) {
        tree->has_xml_ns = 1;
    }
    th_mo_attr_changed(tree, node, atom, NULL, 0, 0);
    if ((size_t)node->attr_count >= th_node_attr_capacity(node)) {
        if (node_attr_reserve(tree, node, 0) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;                              /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    node->attrs[node->attr_count].name_atom = atom;
    node->attrs[node->attr_count].value = owned;
    node->attrs[node->attr_count].value_len = value_len;
    node->attr_count++;
    return 0;
}

/* Room for len code points of node's character data, keeping its first keep code points: the node's own buffer when it
   is large enough, else a new one. The arena frees nothing while the tree lives, so a node that took a new buffer on
   every write would keep each value it ever held. text_capacity_shift records what the node's buffer holds, and a
   buffer that has to grow again doubles, so a run of writes allocates a small multiple of its largest value in total.
   The first new buffer is exact, as one write needs no more. An element records none: an element clone shares its
   source's name buffer, so a rename must not write into it. NULL on allocation failure. */
static Py_UCS4 *text_room(th_tree *tree, th_node *node, Py_ssize_t len, Py_ssize_t keep) {
    size_t capacity = node->text_capacity_shift ? (size_t)1 << node->text_capacity_shift : 0;
    if ((size_t)len <= capacity) {
        return node->text;
    }
    size_t slots = ((size_t)len + 3) & ~(size_t)3; /* the arena rounds to 16 bytes anyway */
    size_t bytes = slots * sizeof(Py_UCS4);
    /* GCOVR_EXCL_BR_START: allocation-size overflow */
    if (capacity > 0 &&
        (!th_grow_cap((size_t)len, capacity, 16, sizeof(Py_UCS4), &slots, &bytes) || bytes > PY_SSIZE_T_MAX)) {
        /* GCOVR_EXCL_BR_STOP */
        return NULL; /* GCOVR_EXCL_LINE: allocation-size overflow */
    }
    Py_UCS4 *grown = arena_alloc(tree, (Py_ssize_t)bytes);
    if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    TH_WORK_STEPS_ADD(slots);
    if (keep > 0) {
        memcpy(grown, node->text, (size_t)keep * sizeof(Py_UCS4));
    }
    node->text = grown;
    if (node->type != TH_NODE_ELEMENT) {
        /* the largest power of two within slots: exact for a doubled buffer, a lower bound for an exact one */
#if defined(_MSC_VER)
        unsigned long high;
        _BitScanReverse64(&high, (unsigned long long)slots);
        node->text_capacity_shift = (uint8_t)high;
#else
        node->text_capacity_shift = (uint8_t)(63 - __builtin_clzll((unsigned long long)slots));
#endif
    }
    return grown;
}

/* Replace a node's character data with a copy of len code points. Returns 0, or -1 with MemoryError set on allocation
   failure. */
int th_node_set_data(th_tree *tree, th_node *node, const Py_UCS4 *data, Py_ssize_t len) {
    const Py_UCS4 *old = node->text_len > 0 ? need_text(tree, node) : NULL;
    if (old == NULL && node->text_len > 0) { /* GCOVR_EXCL_BR_LINE: lazy text OOM */
        PyErr_NoMemory();                    /* GCOVR_EXCL_LINE: allocation failure */
        return -1;                           /* GCOVR_EXCL_LINE: allocation failure */
    }
    th_mo_char_data_changed(tree, node, old, node->text_len);
    node->script_text_closes = 0;
    if (len == 0) {
        if (node->text_capacity_shift == 0) {
            node->text = (Py_UCS4 *)th_empty_text;
        }
        node->text_len = 0; /* a buffer with room stays for the next value */
        return 0;
    }
    Py_UCS4 *room = text_room(tree, node, len, 0);
    if (room == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    memmove(room, data, (size_t)len * sizeof(Py_UCS4)); /* data may point into this node's own buffer */
    node->text_len = len;
    return 0;
}

/* Remove the [from, to) code-point run from a node's character data without copying the text that stays: the arena
   keeps every buffer until the tree is freed, so a copy per edit grows with the number of edits. A run at either end
   narrows the node's view of its buffer; a run in the middle closes up in place once the node owns its buffer, which
   costs one copy on the first such edit. Returns 0, or -1 with MemoryError set on allocation failure. */
int th_node_delete_data(th_tree *tree, th_node *node, Py_ssize_t from, Py_ssize_t to) {
    const Py_UCS4 *old = node->text_len > 0 ? need_text(tree, node) : NULL;
    if (old == NULL && node->text_len > 0) { /* GCOVR_EXCL_BR_LINE: lazy text OOM */
        PyErr_NoMemory();                    /* GCOVR_EXCL_LINE: allocation failure */
        return -1;                           /* GCOVR_EXCL_LINE: allocation failure */
    }
    th_mo_char_data_changed(tree, node, old, node->text_len);
    node->script_text_closes = 0;
    Py_ssize_t remaining = node->text_len - to;
    if (remaining == 0) {
        node->text_len = from;
    } else if (from == 0) {
        node->text += to;
        node->text_len = remaining;
        node->text_capacity_shift = 0; /* the capacity counts from the start of the buffer, which the node left */
    } else {
        if (!node->text_exclusive) {
            Py_UCS4 *owned = arena_alloc(tree, (from + remaining) * (Py_ssize_t)sizeof(Py_UCS4));
            if (owned == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
                return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            memcpy(owned, old, (size_t)from * sizeof(Py_UCS4));
            node->text = owned;
            node->text_exclusive = 1;
            node->text_capacity_shift = 0; /* owned is exact-sized */
        }
        memmove(node->text + from, old + to, (size_t)remaining * sizeof(Py_UCS4));
        node->text_len = from + remaining;
    }
    if (node->text_len == 0) {
        node->text = (Py_UCS4 *)th_empty_text;
        node->text_capacity_shift = 0;
    }
    return 0;
}

/* node_attr_find_large without the case-folded fallback. */
Py_ssize_t th_node_attr_find_atom(th_tree *tree, th_node *node, uint32_t atom) {
    if (!(node->attr_capacity_shift & TH_ATTR_INDEXED)) {
        if (node_attr_reserve_indexed(tree, node) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -2;                                   /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    return node_attr_find_exact_indexed(tree, node, atom);
}

/* th_node_attr_find on an element with ATTR_INDEX_MIN or more attributes: node_attr_find_large, or -2 with no error set
   when the index could not be allocated, so the reader falls back to scanning. */
ATTR_INDEX_FN Py_ssize_t node_attr_find_wide(th_tree *tree, th_node *node, const char *name, Py_ssize_t name_len,
                                             uint32_t atom) {
    const Py_ssize_t found = node_attr_find_large(tree, node, name, name_len, atom);
    if (found < -1) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        /* GCOVR_EXCL_START: allocation-failure path */
        PyErr_Clear(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    /* GCOVR_EXCL_STOP */
    return found;
}

Py_ssize_t th_node_attr_find(th_tree *tree, th_node *node, const char *name, Py_ssize_t name_len) {
    /* Node.re(attr=...) and the XSLT lookups pass any node here; only an element has attrs and attr_count. */
    if (node->type != TH_NODE_ELEMENT) {
        return -1;
    }
    uint32_t atom = th_attr_lookup_inline(tree, name, name_len);
    /* unlikely, so the scan small elements take keeps its code layout */
    /* -2 when the node has no index or it could not be allocated; the scan below takes over */
    Py_ssize_t found = -2;
    if (TH_UNLIKELY(node->attr_count >= ATTR_INDEX_MIN)) {
        found = node_attr_find_wide(tree, node, name, name_len, atom);
    }
    if (found >= -1) {
        return found;
    }
    if (atom != UINT32_MAX) {
        for (Py_ssize_t index = 0; index < node->attr_count; index++) {
            TH_WORK_STEP();
            if (node->attrs[index].name_atom == atom) {
                return index;
            }
        }
    }
    /* A foreign element can store a case-adjusted attribute name (definitionURL)
       whose atom differs from the lowercased probe, so match case-insensitively
       against the stored names; the probe is already lowercased by the caller. XML
       keeps names case-sensitive, so its exact-atom match above is the only pass. */
    if (!tree->xml && node->ns != TH_NS_HTML) {
        for (Py_ssize_t index = 0; index < node->attr_count; index++) {
            TH_WORK_STEP();
            if (attr_name_folds_to(tree, node->attrs[index].name_atom, name, name_len)) {
                return index;
            }
        }
    }
    return -1;
}

/* Close the gap at index of an indexed array by moving the shorter side, the attributes before it one slot up or the
   ones after it one slot down, so removing from either end moves nothing. A current index follows each moved
   attribute, relinked before the move while the array still holds it at its old slot; a stale one stays stale for the
   next lookup to rebuild. Once removals from the front have advanced attrs through every spare slot, the array moves
   back to the start of its block instead: that many removals have passed since it last started there, so the move
   costs each of them one step. */
ATTR_INDEX_FN void node_attr_drop_indexed(th_tree *tree, th_node *node, Py_ssize_t index) {
    attr_index *table = node_attr_index(node);
    const size_t head = node_attr_head(node);
    const Py_ssize_t after = node->attr_count - 1 - index;
    const int lead = index < after;
    if (lead && head == th_node_attr_capacity(node)) {
        TH_WORK_STEPS_ADD(index + after);
        th_node_attr *start = node->attrs - head;
        memmove(start, node->attrs, (size_t)index * sizeof(th_node_attr));
        memmove(start + index, node->attrs + index + 1, (size_t)after * sizeof(th_node_attr));
        node->attrs = start;
        start[-1].name_atom = 0;
        table->count = ATTR_INDEX_STALE;
        return;
    }
    TH_WORK_STEPS_ADD(lead ? index : after);
    if (table->count == (uint32_t)node->attr_count) {
        uint32_t *links = table->links;
        const size_t buckets = 2 * th_node_attr_capacity(node);
        const th_node_attr *attrs = node->attrs;
        const int folds = attr_index_folds(tree, node);
        attr_index_unlink(links, buckets, node_attr_slot(node, (size_t)index), attrs[index].name_atom, folds);
        if (lead) {
            for (size_t position = (size_t)index; position > 0; position--) {
                attr_index_move(links, buckets, node_attr_slot(node, position - 1), attrs[position - 1].name_atom,
                                node_attr_slot(node, position), folds);
            }
        } else {
            for (size_t position = (size_t)index + 1; position < (size_t)node->attr_count; position++) {
                attr_index_move(links, buckets, node_attr_slot(node, position), attrs[position].name_atom,
                                node_attr_slot(node, position - 1), folds);
            }
        }
        table->count--;
    } else {
        table->count = ATTR_INDEX_STALE;
    }
    if (lead) {
        memmove(node->attrs + 1, node->attrs, (size_t)index * sizeof(th_node_attr));
        node->attrs++;
        node->attrs[-1].name_atom = (uint32_t)head + 1;
    } else {
        memmove(node->attrs + index, node->attrs + index + 1, (size_t)after * sizeof(th_node_attr));
    }
}

/* Drop the attribute at index, keeping the order of the rest; the caller has advanced attr_version. */
static void node_attr_drop(th_tree *tree, th_node *node, Py_ssize_t index) {
    TH_WORK_STEP();
    mark_start_dirty(tree, node);
    th_mo_attr_changed(tree, node, node->attrs[index].name_atom, node->attrs[index].value, node->attrs[index].value_len,
                       1);
    tree->id_version += node->attrs[index].name_atom == TH_ATTR_ID;
    if (tree->slot_cache_valid) {
        const uint32_t atom = node->attrs[index].name_atom;
        tree->slot_cache_valid = (atom != TH_ATTR_SLOT) & ((atom != TH_ATTR_NAME) | (node->atom != TH_TAG_SLOT));
    }
    if (node->attr_capacity_shift & TH_ATTR_INDEXED) {
        node_attr_drop_indexed(tree, node, index);
    } else {
        for (Py_ssize_t shift = index; shift + 1 < node->attr_count; shift++) {
            TH_WORK_STEP();
            node->attrs[shift] = node->attrs[shift + 1];
        }
    }
    node->attr_count--;
}

int th_node_attr_del(th_tree *tree, th_node *node, const char *name, Py_ssize_t name_len) {
    tree->attr_version++;
    Py_ssize_t index = th_node_attr_find(tree, node, name, name_len);
    if (index < 0) {
        return 0;
    }
    node_attr_drop(tree, node, index);
    return 1;
}

void th_node_attrs_clear(th_tree *tree, th_node *node) {
    tree->attr_version++;
    tree->id_version++;
    node->attr_count = 0;
}

uint64_t th_unlinks;

uint64_t th_tree_unlinks(void) {
#if defined(_MSC_VER)
    return *(volatile uint64_t *)&th_unlinks; /* an aligned 64-bit load is atomic on x64 and arm64 */
#else
    return __atomic_load_n(&th_unlinks, __ATOMIC_RELAXED);
#endif
}

void th_node_attr_del_at(th_tree *tree, th_node *node, Py_ssize_t index) {
    tree->attr_version++;
    node_attr_drop(tree, node, index);
}

void th_node_remove(th_node *child) {
    node_remove(child);
}

void th_node_append_child(th_node *parent, th_node *child) {
    node_append(parent, child);
}

void th_node_insert_before(th_node *parent, th_node *child, th_node *ref) {
    node_insert_before(parent, child, ref);
}

/* The MutationObserver registry accessors dom/observe.c reaches the tree fields through. */
int th_tree_has_observers(const th_tree *tree) {
    return tree->observers != NULL;
}

int th_tree_has_iterators(const th_tree *tree) {
    return tree->node_iterator_count > 0;
}

struct th_mo_registry **th_tree_observers_ptr(th_tree *tree) {
    return &tree->observers;
}

/* The observing counterparts the binding layer (dom/element.c) routes user mutations
   through: they relink exactly as above and queue a childList mutation record for the
   tree's observers. A removal reads the siblings while child is still linked, then
   detaches; an insertion links first, so child's fresh siblings are the record's. */
int th_tree_add_node_iterator(th_tree *tree, th_node_iterator *iterator) {
    if (tree->node_iterator_count == tree->node_iterator_cap) {
        Py_ssize_t cap = tree->node_iterator_cap == 0 ? TH_INITIAL_CAPACITY(4) : tree->node_iterator_cap * 2;
        th_node_iterator **grown = PyMem_Realloc(tree->node_iterators, (size_t)cap * sizeof(th_node_iterator *));
        if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        tree->node_iterators = grown;
        tree->node_iterator_cap = cap;
    }
    tree->node_iterators[tree->node_iterator_count++] = iterator;
    return 0;
}

void th_tree_remove_node_iterator(th_tree *tree, th_node_iterator *iterator) {
    /* the iterator is always registered, so the scan always breaks and never runs to the end */
    for (Py_ssize_t index = 0; index < tree->node_iterator_count; index++) { /* GCOVR_EXCL_BR_LINE */
        if (tree->node_iterators[index] == iterator) {
            tree->node_iterators[index] = tree->node_iterators[--tree->node_iterator_count];
            break;
        }
    }
    if (tree->node_iterator_count == 0) {
        PyMem_Free(tree->node_iterators);
        tree->node_iterators = NULL;
        tree->node_iterator_cap = 0;
    }
}

#ifdef FUZZING_BUILD_MODE_UNSAFE_FOR_PRODUCTION
static int is_inclusive_ancestor_of(const th_node *ancestor, const th_node *node) {
    for (const th_node *walk = node; walk != NULL; walk = walk->parent) {
        if (walk == ancestor) {
            return 1;
        }
    }
    return 0;
}
#endif

/* Whether removed is an inclusive ancestor of node but not of root, the removals the steps below act on. node sits
   inside root (the steps below keep it there, and th_tree_verify counts an iterator that breaks this), so the climb
   from it always meets root, and ends there rather than at the top of the tree: reaching root first means removed is
   root, above it, or off node's path. Each step up also advances a pre-order walk of removed's descendants, and the
   climb ends when they run out: a node still below removed after that many steps would need it to hold more descendants
   than it has. The test so costs no more than the nodes the removal unlinks, however deep the document nests root or
   node (the XML parser sets no nesting limit). */
static int removal_holds_pointer(const th_node_iterator *iterator, th_node *removed, const th_node *node) {
    th_node *descendant = removed->first_child;
    for (const th_node *walk = node; walk != iterator->root; walk = walk->parent) {
        TH_WORK_STEP();
        if (walk == removed) {
            return 1;
        }
        if (descendant == NULL) {
            return 0;
        }
        descendant = preorder_next(descendant, removed);
    }
    return 0;
}

/* The DOM "adjust a node pointer" steps for removed, which is about to leave its parent: a pointer into the removed
   subtree moves to the first node after that subtree within root when it pointed before its node, else (or when no
   such node exists) to the node just before the subtree, pointing after it. */
static void adjust_node_pointer(const th_node_iterator *iterator, th_node *removed, th_node **node, int *before) {
    if (!removal_holds_pointer(iterator, removed, *node)) {
        return;
    }
    if (*before) {
        /* every unlink from a tree a caller can reach runs these steps (XSLT's whitespace strip unlinks raw, but puts
           the nodes back under the same lock, with no Python call in between), so the walk meets root */
        for (th_node *walk = removed; walk != iterator->root; walk = walk->parent) {
            if (walk->next_sibling != NULL) {
                *node = walk->next_sibling;
                return;
            }
        }
    }
    th_node *previous = removed->prev_sibling;
    if (previous == NULL) {
        *node = removed->parent;
    } else {
        while (previous->last_child != NULL) {
            previous = previous->last_child;
        }
        *node = previous;
    }
    *before = 0;
}

/* Out of line: most trees hold no live iterator, so the removal loops of normalize, the sanitizer and the query edits
   keep a single count test on their hot path. */
static TH_NOINLINE void run_pre_removing_steps(th_tree *tree, th_node *child) {
    for (Py_ssize_t index = 0; index < tree->node_iterator_count; index++) {
        th_node_iterator *iterator = tree->node_iterators[index];
        adjust_node_pointer(iterator, child, &iterator->reference, &iterator->reference_before);
        if (iterator->candidate != NULL) {
            adjust_node_pointer(iterator, child, &iterator->candidate, &iterator->candidate_before);
        }
    }
}

void th_node_remove_silent(th_tree *tree, th_node *child) {
    if (tree->node_iterator_count > 0) {
        run_pre_removing_steps(tree, child);
    }
    node_remove(child);
}

void th_node_remove_observed(th_tree *tree, th_node *child) {
    if (child->parent != NULL) {
        th_mo_child_removed(tree, child->parent, child, child->prev_sibling, child->next_sibling);
    }
    th_node_remove_silent(tree, child);
}

void th_tree_note_observed(th_tree *tree) {
    tree->nest_bound |= TH_NEST_OBSERVED;
}

/* The most nodes link_keeps_capped measures in a linked subtree: one built from a few elements keeps the bound at a
   cost no larger than the link's own, and a larger one drops it, so that moving it about stays O(1) a move. */
#define TH_NEST_MEASURE_MAX 64

/* How many levels child's subtree reaches below child, or -1 when it holds more than TH_NEST_MEASURE_MAX nodes. */
static Py_ssize_t subtree_levels(const th_node *child) {
    const th_node *node = child;
    Py_ssize_t level = 0;
    Py_ssize_t levels = 0;
    for (int visited = 0; visited < TH_NEST_MEASURE_MAX; visited++) {
        if (node->first_child != NULL) {
            node = node->first_child;
            level++;
            levels = level > levels ? level : levels;
            continue;
        }
        while (node != child && node->next_sibling == NULL) {
            node = node->parent;
            level--;
        }
        if (node == child) {
            return levels;
        }
        node = node->next_sibling;
    }
    return -1;
}

/* Whether linking child under parent leaves TH_NEST_CAPPED standing: a childless node that is not an element does,
   and so does a subtree subtree_levels measures whose nodes all stay short of TH_DEEP_NEST ancestors. */
static int link_keeps_capped(const th_node *parent, const th_node *child) {
    if (child->first_child == NULL && child->type != TH_NODE_ELEMENT) {
        return 1;
    }
    Py_ssize_t ancestors = subtree_levels(child);
    if (ancestors < 0) {
        return 0;
    }
    for (const th_node *ancestor = parent; ancestor != NULL; ancestor = ancestor->parent) {
        if (++ancestors >= TH_DEEP_NEST) {
            return 0;
        }
    }
    return 1;
}

/* Out of line: a tree reaches it only while it holds a nest bound or may have an observer, so any other insertion
   pays the single test it paid for the observer check alone. The insertion drops the bound, keeps TH_NEST_CAPPED
   while link_keeps_capped, and keeps the observer bit only while an observer is registered. */
static TH_NOINLINE void child_inserted_hooked(th_tree *tree, th_node *parent, th_node *child) {
    const Py_ssize_t capped = (tree->nest_bound & TH_NEST_CAPPED) != 0 && link_keeps_capped(parent, child);
    tree->nest_bound = (capped ? TH_NEST_CAPPED : 0) | (tree->observers != NULL ? TH_NEST_OBSERVED : 0);
    th_mo_child_inserted(tree, parent, child);
}

void th_node_append_child_observed(th_tree *tree, th_node *parent, th_node *child) {
    node_append(parent, child);
    if (tree->nest_bound != 0) {
        child_inserted_hooked(tree, parent, child);
    }
}

/* Whether node lies in one of tree's arena blocks. A node of another tree linked in here points into an arena that can
   be freed under this one, so the walk counts it rather than follow it. Only a corrupt tree reaches the not-found and
   over-budget paths below; tests/test_fuzz_self_test.py builds such trees in the fuzz build. */
static int arena_owns(const th_tree *tree, const th_node *node) {
    /* the loop runs out of blocks for a corrupt tree alone */
    for (const arena_block *block = tree->arena; block != NULL; block = block->next) { /* GCOVR_EXCL_BR_LINE */
        /* one unsigned compare: an address below data wraps to an offset past used */
        if ((uintptr_t)node - (uintptr_t)block->data < (uintptr_t)block->used) {
            return 1;
        }
    } /* GCOVR_EXCL_LINE: corrupt tree only */
    return 0; /* GCOVR_EXCL_LINE: corrupt tree only */
}

/* Whether the walk may step onto next. A node from another arena counts as an identity violation, and a step past
   the budget as a link violation: the tree stamped at most node_seq nodes, so a longer walk is going round a cycle. */
static int verify_step(const th_tree *tree, const th_node *next, Py_ssize_t *budget, th_tree_violations *found) {
    /* GCOVR_EXCL_START: corrupt tree only */
    if (!arena_owns(tree, next)) {
        found->identities++;
        return 0;
    }
    if (--*budget < 0) {
        found->links++;
        return 0;
    }
    /* GCOVR_EXCL_STOP */
    return 1;
}

void th_tree_verify(th_tree *tree, th_node *start, Py_ssize_t (*visit)(void *context, th_node *node), void *context,
                    th_tree_violations *found) {
    found->links = 0;
    found->identities = 0;
    found->iterators = 0;
    found->versions = 0;
    Py_ssize_t budget = (Py_ssize_t)tree->node_seq;
    if (!verify_step(tree, start, &budget, found)) { /* GCOVR_EXCL_BR_LINE: corrupt tree only */
        return;                                      /* GCOVR_EXCL_LINE: corrupt tree only */
    }
    th_node *top = start;
    while (top->parent != NULL && verify_step(tree, top->parent, &budget, found)) { /* GCOVR_EXCL_BR_LINE */
        top = top->parent;
    }
    found->links += (top->prev_sibling != NULL) + (top->next_sibling != NULL);
    budget = (Py_ssize_t)tree->node_seq - 1; /* the walk below visits each node under top once */
    for (th_node *node = top;;) {
        th_node *previous = NULL;
        th_node *child = node->first_child;
        /* the walk climbs back through parent links, so it stops at a child whose link points elsewhere */
        while (child != NULL && verify_step(tree, child, &budget, found) && /* GCOVR_EXCL_BR_LINE */
               child->parent == node) {                                     /* GCOVR_EXCL_BR_LINE */
            found->links += child->prev_sibling != previous;
            previous = child;
            child = child->next_sibling;
        }
        /* GCOVR_EXCL_START: corrupt tree only */
        if (child != NULL) {
            found->links += budget >= 0 && arena_owns(tree, child);
            return;
        }
        /* GCOVR_EXCL_STOP */
        found->links += node->last_child != previous;
        found->identities += (node->seq >= tree->node_seq) + visit(context, node);
        if (node->first_child != NULL) {
            node = node->first_child;
            continue;
        }
        while (node != top && node->next_sibling == NULL) {
            node = node->parent;
        }
        if (node == top) {
            break;
        }
        node = node->next_sibling;
    }
#ifdef FUZZING_BUILD_MODE_UNSAFE_FOR_PRODUCTION
    /* the DOM NodeIterator pre-removing steps keep both pointers inside root; only the fuzz escape hook breaks that */
    for (Py_ssize_t index = 0; index < tree->node_iterator_count; index++) {
        const th_node_iterator *iterator = tree->node_iterators[index];
        found->iterators += !is_inclusive_ancestor_of(iterator->root, iterator->reference);
        if (iterator->candidate != NULL) {
            found->iterators += !is_inclusive_ancestor_of(iterator->root, iterator->candidate);
        }
    }
#endif
}

/* Whether node sits in the sibling run [first, last] (never when first is NULL). */
static int in_run(const th_node *node, const th_node *first, const th_node *last) {
    if (first == NULL) {
        return 0;
    }
    for (const th_node *walk = first;; walk = walk->next_sibling) {
        if (walk == node) {
            return 1;
        }
        if (walk == last) {
            return 0;
        }
    }
}

/* Whether a child of type sits between from (inclusive) and until (exclusive; NULL runs to the end), skipping the
   replaced run. */
static int has_child_between(th_node *from, const th_node *until, enum th_node_type type, const th_node *run_first,
                             const th_node *run_last) {
    for (th_node *walk = from; walk != until; walk = walk->next_sibling) {
        if (walk->type == type && !in_run(walk, run_first, run_last)) {
            return 1;
        }
    }
    return 0;
}

const char *th_pre_insert_error(th_node *parent, th_node *const *nodes, Py_ssize_t count, th_node *child,
                                th_node *run_first, th_node *run_last) {
    Py_ssize_t elements = 0;
    Py_ssize_t doctypes = 0;
    Py_ssize_t texts = 0;
    for (Py_ssize_t index = 0; index < count; index++) {
        elements += nodes[index]->type == TH_NODE_ELEMENT;
        doctypes += nodes[index]->type == TH_NODE_DOCTYPE;
        texts += nodes[index]->type == TH_NODE_TEXT || nodes[index]->type == TH_NODE_CDATA;
    }
    /* several nodes are gathered into a fragment first, and a fragment cannot hold a doctype */
    if (doctypes > 0 && (parent->type != TH_NODE_DOCUMENT || count > 1)) {
        return "a doctype can only be a child of a Document";
    }
    if (parent->type != TH_NODE_DOCUMENT) {
        return NULL;
    }
    if (texts > 0) {
        return "a Document cannot hold a Text node";
    }
    if (elements > 1) {
        return "a Document can hold only one element";
    }
    /* a replacement goes where the replaced run starts; an insertion before child */
    th_node *after = run_first != NULL ? run_last->next_sibling : child;
    th_node *before = run_first != NULL ? run_first : child;
    if (elements == 1) {
        if (has_child_between(parent->first_child, NULL, TH_NODE_ELEMENT, run_first, run_last)) {
            return "a Document can hold only one element";
        }
        if (has_child_between(after, NULL, TH_NODE_DOCTYPE, run_first, run_last)) {
            return "a Document's element must come after its doctype";
        }
    }
    if (doctypes == 1) {
        if (has_child_between(parent->first_child, NULL, TH_NODE_DOCTYPE, run_first, run_last)) {
            return "a Document can hold only one doctype";
        }
        if (has_child_between(parent->first_child, before, TH_NODE_ELEMENT, run_first, run_last)) {
            return "a Document's doctype must come before its element";
        }
    }
    return NULL;
}

void th_node_insert_before_observed(th_tree *tree, th_node *parent, th_node *child, th_node *ref) {
    node_insert_before(parent, child, ref);
    if (tree->nest_bound != 0) {
        child_inserted_hooked(tree, parent, child);
    }
}

/* Whether ancestor is a host-including inclusive ancestor of node (DOM pre-insert), the test that rejects making a
   node a descendant of itself. The walk crosses from a shadow root to its host, so a host cannot move into its own
   shadow tree. */
int th_node_contains(th_tree *tree, th_node *ancestor, th_node *node) {
    for (th_node *walk = node; walk != NULL;
         walk = walk->parent == NULL && th_node_is_shadow_root(walk) ? th_shadow_host(tree, walk) : walk->parent) {
        if (walk == ancestor) {
            return 1;
        }
    }
    return 0;
}

/* Whether two attributes carry the same name. Names resolve to their interned
   bytes, so a per-tree dynamic atom in one tree matches the same spelling in the
   other (the numeric atoms differ across trees). */
static int attr_name_equal(th_tree *left_tree, const th_node_attr *left, th_tree *right_tree,
                           const th_node_attr *right) {
    Py_ssize_t left_len, right_len;
    const char *left_name = th_attr_name(left_tree, left->name_atom, &left_len);
    const char *right_name = th_attr_name(right_tree, right->name_atom, &right_len);
    return left_len == right_len && memcmp(left_name, right_name, (size_t)left_len) == 0;
}

/* Whether two attributes carry the same value. A valueless attribute (NULL value,
   zero length) is the empty string per the DOM, so `disabled` equals `disabled=""`. */
static int attr_value_equal(const th_node_attr *left, const th_node_attr *right) {
    return left->value_len == right->value_len &&
           (left->value_len == 0 || memcmp(left->value, right->value, (size_t)left->value_len * sizeof(Py_UCS4)) == 0);
}

static int attrs_equal_indexed(th_tree *left_tree, th_node *left, th_tree *right_tree, th_node *right,
                               Py_ssize_t start);

static int attrs_equal(th_tree *left_tree, th_node *left, th_tree *right_tree, th_node *right) {
    if (left->attr_count != right->attr_count) {
        return 0;
    }
    int can_index = left->attr_count >= 32 &&
                    /* GCOVR_EXCL_BR_START: allocation size overflow */
                    (size_t)left->attr_count <= SIZE_MAX / (4 * sizeof(Py_ssize_t));
    /* GCOVR_EXCL_BR_STOP */
    Py_ssize_t comparisons = 0;
    for (Py_ssize_t index = 0; index < left->attr_count; index++) {
        const th_node_attr *want = &left->attrs[index];
        Py_ssize_t other = 0;
        for (; other < right->attr_count; other++) {
            if (attr_name_equal(left_tree, want, right_tree, &right->attrs[other])) {
                if (!attr_value_equal(want, &right->attrs[other])) {
                    return 0;
                }
                break;
            }
        }
        if (other == right->attr_count) {
            return 0;
        }
        if (can_index) {
            comparisons += other + 1;
            if (comparisons >= left->attr_count * 2) {
                can_index = 0;
                const int indexed = attrs_equal_indexed(left_tree, left, right_tree, right, index + 1);
                if (indexed >= 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                    return indexed;
                }
            } /* GCOVR_EXCL_LINE: allocation failure fallback */
        }
    }
    return 1;
}

static int attrs_equal_indexed(th_tree *left_tree, th_node *left, th_tree *right_tree, th_node *right,
                               Py_ssize_t start) {
    size_t capacity = 64;
    while (capacity < (size_t)right->attr_count * 2) {
        capacity *= 2;
    }
    Py_ssize_t *slots = PyMem_Calloc(capacity, sizeof(Py_ssize_t));
    if (slots == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;       /* GCOVR_EXCL_LINE: fall back to the allocation-free comparison */
    }
    for (Py_ssize_t index = 0; index < right->attr_count; index++) {
        /* an element never carries one name twice, so every name takes a fresh slot */
        size_t slot = ((size_t)right->attrs[index].name_atom * 2654435761U) & (capacity - 1);
        while (slots[slot] != 0) {
            slot = (slot + 1) & (capacity - 1);
        }
        slots[slot] = index + 1;
    }
    int equal = 1;
    for (Py_ssize_t index = start; index < left->attr_count; index++) {
        const th_node_attr *want = &left->attrs[index];
        uint32_t atom = want->name_atom;
        if (left_tree != right_tree) {
            Py_ssize_t name_len;
            const char *name = th_attr_name(left_tree, atom, &name_len);
            atom = th_attr_lookup(right_tree, name, name_len);
        }
        size_t slot = ((size_t)atom * 2654435761U) & (capacity - 1);
        while (slots[slot] != 0 && right->attrs[slots[slot] - 1].name_atom != atom) {
            slot = (slot + 1) & (capacity - 1);
        }
        if (slots[slot] == 0 || !attr_value_equal(want, &right->attrs[slots[slot] - 1])) {
            equal = 0;
            break;
        }
    }
    PyMem_Free(slots);
    return equal;
}

/* Whether two nodes' own character data match, realizing a borrowed text span first;
   -1 when that realization fails. */
static int data_equal(th_tree *left_tree, th_node *left, th_tree *right_tree, th_node *right) {
    if (left->text_len != right->text_len) {
        return 0;
    }
    if (left->text_len == 0) {
        return 1;
    }
    const Py_UCS4 *left_text = need_text(left_tree, left);
    const Py_UCS4 *right_text = need_text(right_tree, right);
    if (left_text == NULL || right_text == NULL) { /* GCOVR_EXCL_BR_LINE: lazy text OOM */
        return -1;                                 /* GCOVR_EXCL_LINE: allocation failure */
    }
    return memcmp(left_text, right_text, (size_t)left->text_len * sizeof(Py_UCS4)) == 0;
}

static int node_data_equals(th_tree *left_tree, th_node *left, th_tree *right_tree, th_node *right) {
    if (left->type != right->type) {
        return 0;
    }
    switch ((enum th_node_type)left->type) { /* GCOVR_EXCL_BR_LINE: node types are exhaustive */
    case TH_NODE_ELEMENT:
        if (left->ns != right->ns) {
            return 0;
        }
        if (!data_equal(left_tree, left, right_tree, right)) {
            return 0;
        }
        if (!attrs_equal(left_tree, left, right_tree, right)) {
            return 0;
        }
        break;
    case TH_NODE_DOCTYPE:
        /* tag_flags records whether the source supplied a public/system id, which the
           id text alone cannot express (a missing and an empty id both serialize empty). */
        if (left->tag_flags != right->tag_flags) {
            return 0;
        }
        if (!data_equal(left_tree, left, right_tree, right)) {
            return 0;
        }
        break;
    case TH_NODE_PI:
        /* attr_count holds the packed target/data split point. */
        if (left->attr_count != right->attr_count) {
            return 0;
        }
        if (!data_equal(left_tree, left, right_tree, right)) {
            return 0;
        }
        break;
    case TH_NODE_TEXT:
    case TH_NODE_COMMENT:
    case TH_NODE_CDATA: /* only character data borrows a span, so only it can fail */
        return data_equal(left_tree, left, right_tree, right);
    case TH_NODE_DOCUMENT:
    case TH_NODE_CONTENT:
        break; /* a document / template-content fragment compares purely by its children */
    }
    return 1;
}

/* Node.equals compares the two shapes in lockstep, using parent links to return from a subtree instead of the C stack.
 */
int th_node_equals(th_tree *left_tree, th_node *left, th_tree *right_tree, th_node *right) {
    th_node *left_root = left;
    th_node *right_root = right;
    for (;;) {
        int equal = node_data_equals(left_tree, left, right_tree, right);
        if (equal <= 0) {
            return equal;
        }
        if ((left->first_child == NULL) != (right->first_child == NULL)) {
            return 0;
        }
        if (left->first_child != NULL) {
            left = left->first_child;
            right = right->first_child;
            continue;
        }
        while (left != left_root && left->next_sibling == NULL) {
            if (right->next_sibling != NULL) {
                return 0;
            }
            left = left->parent;
            right = right->parent;
        }
        if (left == left_root) {
            return right == right_root;
        }
        if (right->next_sibling == NULL) {
            return 0;
        }
        left = left->next_sibling;
        right = right->next_sibling;
    }
}

/* The ASCII-lowercased atom for an attribute name stored in tree, re-interned when folding changes it. Returns
   TH_ATTR_UNKNOWN on allocation failure. */
static uint32_t fold_attr_atom(th_tree *tree, uint32_t atom) {
    Py_ssize_t name_len;
    const char *name = th_attr_name(tree, atom, &name_len);
    Py_ssize_t first_upper = 0;
    while (first_upper < name_len && !(name[first_upper] >= 'A' && name[first_upper] <= 'Z')) {
        first_upper++;
    }
    if (first_upper == name_len) {
        return atom;
    }
    char *folded = PyMem_Malloc((size_t)name_len);
    if (folded == NULL) {       /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return TH_ATTR_UNKNOWN; /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (Py_ssize_t index = 0; index < name_len; index++) {
        char ch = name[index];
        folded[index] = ch >= 'A' && ch <= 'Z' ? (char)(ch + 32) : ch;
    }
    uint32_t folded_atom = th_attr_intern_utf8(tree, folded, name_len);
    PyMem_Free(folded);
    return folded_atom;
}

/* One attribute of an element being folded: the atom of its folded name and its position among the attributes. */
typedef struct {
    uint32_t atom;
    Py_ssize_t index;
} folded_attr_key;

static int folded_attr_key_less(const folded_attr_key *left, const folded_attr_key *right) {
    return left->atom < right->atom || (left->atom == right->atom && left->index < right->index);
}

static void sift_folded_attr_key(folded_attr_key *keys, Py_ssize_t root, Py_ssize_t end) {
    for (Py_ssize_t child = 2 * root + 1; child < end; child = 2 * root + 1) {
        TH_WORK_STEP();
        if (child + 1 < end && folded_attr_key_less(&keys[child], &keys[child + 1])) {
            child++;
        }
        if (!folded_attr_key_less(&keys[root], &keys[child])) {
            return;
        }
        folded_attr_key top = keys[root];
        keys[root] = keys[child];
        keys[child] = top;
        root = child;
    }
}

/* Heapsort rather than qsort: some C libraries' qsort (MSVC's) is quadratic on a crafted order of its input, while
   heapsort is n log n for every order and needs no hash table that a document could make collide. */
static void heapsort_folded_attr_keys(folded_attr_key *keys, Py_ssize_t count) {
    for (Py_ssize_t root = count / 2 - 1; root >= 0; root--) {
        sift_folded_attr_key(keys, root, count);
    }
    for (Py_ssize_t end = count - 1; end > 0; end--) {
        folded_attr_key top = keys[0];
        keys[0] = keys[end];
        keys[end] = top;
        sift_folded_attr_key(keys, 0, end);
    }
}

/* Mark with TH_ATTR_UNKNOWN each attribute whose name atom repeats an earlier attribute's, keeping the first of each.
   Comparing every name against each one kept before it is quadratic in the attribute count, and an XML document puts
   any number of attributes on one element; sorting the atoms brings the attributes of one name together instead,
   without a hash table whose collisions a document could choose. Returns 0, or -1 on allocation failure. */
int th_node_attr_mark_repeats(th_node *node) {
    folded_attr_key *keys = PyMem_Malloc((size_t)node->attr_count * sizeof(*keys));
    if (keys == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        keys[index] = (folded_attr_key){node->attrs[index].name_atom, index};
    }
    heapsort_folded_attr_keys(keys, node->attr_count);
    for (Py_ssize_t index = 1; index < node->attr_count; index++) {
        TH_WORK_STEP();
        if (keys[index].atom == keys[index - 1].atom) {
            node->attrs[keys[index].index].name_atom = TH_ATTR_UNKNOWN; /* no attribute holds the absent atom */
        }
    }
    PyMem_Free(keys);
    return 0;
}

void th_node_attr_drop_marked(th_node *node) {
    Py_ssize_t kept = 0;
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        if (node->attrs[index].name_atom != TH_ATTR_UNKNOWN) {
            node->attrs[kept++] = node->attrs[index];
        }
    }
    node->attr_count = kept;
}

/* Fold the attribute names of an element with many attributes, keeping the first of each folded name. Returns 0, or -1
   on allocation failure. */
static int fold_wide_attrs(th_tree *dest, th_node *node) {
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        uint32_t atom = fold_attr_atom(dest, node->attrs[index].name_atom);
        if (atom == TH_ATTR_UNKNOWN) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;                 /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        node->attrs[index].name_atom = atom;
    }
    if (th_node_attr_mark_repeats(node) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;                             /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_node_attr_drop_marked(node);
    return 0;
}

/* Rewrite a copied element for a tree of the other kind. An XML tree keeps every element as an unknown atom under its
   spelled name, so an HTML element moving in drops the atom that made it raw text (a <script> would otherwise
   serialize "a<b" unescaped). An HTML tree stores and matches HTML element and attribute names in ASCII lowercase, and
   an XML tree's elements all carry the HTML namespace, so an XML element moving in is folded, keeping the first of two
   attributes that fold to one name, as the HTML tokenizer does. Either way a parser clone keeps its implied flag, so
   the serializers still budget the attributes it repeats. Returns 0, or -1 on allocation failure. */
static int convert_element_kind(th_tree *dest, th_node *node) {
    if (dest->xml) {
        node->atom = TH_TAG_UNKNOWN;
        node->tag_flags = (uint8_t)(th_tag_flags(TH_TAG_UNKNOWN) | (node->tag_flags & TH_ELEM_IMPLIED));
        return 0;
    }
    char ascii[64];
    int is_ascii = node->text_len <= (Py_ssize_t)sizeof(ascii);
    for (Py_ssize_t index = 0; index < node->text_len; index++) {
        if (node->text[index] >= 'A' && node->text[index] <= 'Z') {
            node->text[index] += 32;
        }
        if (node->text[index] >= 0x80) {
            is_ascii = 0;
        } else if (is_ascii) {
            ascii[index] = (char)node->text[index];
        }
    }
    node->atom = is_ascii ? th_tag_lookup(ascii, node->text_len) : TH_TAG_UNKNOWN;
    node->tag_flags = (uint8_t)(th_tag_flags(node->atom) | (node->tag_flags & TH_ELEM_IMPLIED));
    if (node->attr_count >= TH_WIDE_ATTR_COUNT) {
        return fold_wide_attrs(dest, node);
    }
    Py_ssize_t kept = 0;
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        uint32_t atom = fold_attr_atom(dest, node->attrs[index].name_atom);
        if (atom == TH_ATTR_UNKNOWN) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;                 /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        Py_ssize_t earlier = 0;
        while (earlier < kept && node->attrs[earlier].name_atom != atom) {
            TH_WORK_STEP();
            earlier++;
        }
        if (earlier == kept) {
            node->attrs[kept] = node->attrs[index];
            node->attrs[kept++].name_atom = atom;
        }
    }
    node->attr_count = kept;
    return 0;
}

/* Within one source identity an address names one value: the source keeps each attribute value in its arena until the
   handle is freed, and two live values never start at one address (the parser stores an empty value as NULL, and
   every other value gets its own allocation). The address and the identity are therefore enough to find a copy. */
struct th_copied_value {
    uint64_t source;     /* the memo's source identity when the copy was made */
    const Py_UCS4 *from; /* an attribute value in the source arena */
    Py_UCS4 *to;         /* its copy in the destination arena */
};

static struct th_copied_value *copied_value_slot(const th_copy_memo *memo, uint64_t source, const Py_UCS4 *from) {
    uint64_t key = (uint64_t)(uintptr_t)from ^ (source * UINT64_C(0xC2B2AE3D27D4EB4F));
    size_t slot = (size_t)(((key * UINT64_C(0x9E3779B97F4A7C15)) >> 32) & (memo->capacity - 1));
    struct th_copied_value *entry = &memo->entries[slot];
    while (entry->from != NULL && !(entry->source == source && entry->from == from)) {
        slot = (slot + 1) & (memo->capacity - 1);
        entry = &memo->entries[slot];
    }
    return entry;
}

static Py_UCS4 *copied_value_find(const th_copy_memo *memo, const Py_UCS4 *from) {
    if (memo->capacity == 0) {
        return NULL;
    }
    const struct th_copied_value *entry = copied_value_slot(memo, memo->source, from);
    return entry->from == NULL ? NULL : entry->to;
}

/* Remember that the value at from was copied to to; find has just missed it. Sharing only saves memory, so a memo
   that cannot grow just stops recording. */
static void copied_value_record(th_copy_memo *memo, const Py_UCS4 *from, Py_UCS4 *to) {
    if (memo->count >= memo->capacity / 2) {
        /* the live table bounds its capacity far below SIZE_MAX, and PyMem_Calloc refuses a byte size that wraps */
        size_t capacity = memo->capacity ? memo->capacity * 2 : 16;
        th_copy_memo grown = *memo;
        grown.entries = PyMem_Calloc(capacity, sizeof(*memo->entries));
        grown.capacity = capacity;
        if (grown.entries == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return;                  /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        for (size_t slot = 0; slot < memo->capacity; slot++) {
            const struct th_copied_value *entry = &memo->entries[slot];
            if (entry->from != NULL) {
                *copied_value_slot(&grown, entry->source, entry->from) = *entry;
            }
        }
        PyMem_Free(memo->entries);
        *memo = grown;
    }
    *copied_value_slot(memo, memo->source, from) = (struct th_copied_value){memo->source, from, to};
    memo->count++;
}

/* The value buffer for the copy in dest of the attribute from of src_node, or NULL on allocation failure. The tree
   builder lets every reconstruction of an unclosed formatting element point at the attribute values of its start tag
   (node_clone), so a buffer per value of every copied element costs value length x clones for an input of value
   length + clones. The copies keep shared what their sources share: in the same tree the copy points at the source's
   value as a clone does, in another tree each value of an element that shares its values is copied once per memo
   and source. A value is never written in place, so sharing is as safe in the copy as in the source. */
static Py_UCS4 *copy_attr_value(th_tree *dest, const th_tree *src, const th_node *src_node, const th_node_attr *from,
                                th_copy_memo *memo) {
    if (dest == src) {
        return from->value;
    }
    int shareable = src_node->attr_values_shared;
    Py_UCS4 *value = shareable ? copied_value_find(memo, from->value) : NULL;
    if (value == NULL) {
        value = arena_alloc(dest, (from->value_len ? from->value_len : 1) * (Py_ssize_t)sizeof(Py_UCS4));
        if (value == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        memcpy(value, from->value, (size_t)from->value_len * sizeof(Py_UCS4));
        if (shareable) {
            copied_value_record(memo, from->value, value);
        }
    }
    return value;
}

/* Copy one node without its children, materializing borrowed text and re-interning per-tree attribute atoms. */
static th_node *copy_node_shallow(th_tree *dest, th_tree *src, th_node *src_node, th_copy_memo *memo) {
    TH_WORK_STEP();
    th_node *node = node_new(dest, src_node->type);
    if (node == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    node->atom = src_node->atom;
    node->ns = src_node->ns;
    node->tag_flags = src_node->tag_flags;
    node->text = (Py_UCS4 *)th_empty_text;
    if (src_node->text_len > 0) {
        const Py_UCS4 *text = need_text(src, src_node);
        if (text == NULL) { /* GCOVR_EXCL_BR_LINE: lazy text OOM */
            return NULL;    /* GCOVR_EXCL_LINE: allocation failure */
        }
        Py_UCS4 *owned = arena_alloc(dest, src_node->text_len * (Py_ssize_t)sizeof(Py_UCS4));
        if (owned == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        TH_WORK_STEPS_ADD(src_node->text_len);
        memcpy(owned, text, (size_t)src_node->text_len * sizeof(Py_UCS4));
        node->text = owned;
        node->text_len = src_node->text_len;
    }
    if (src_node->type == TH_NODE_PI || src_node->type == TH_NODE_DOCTYPE) {
        node->attr_count = src_node->attr_count; /* PI target/data or doctype public-id split point */
    }
    if (src_node->type == TH_NODE_ELEMENT && src_node->attr_count > 0) {
        node->attrs = arena_alloc(dest, src_node->attr_count * (Py_ssize_t)sizeof(th_node_attr));
        if (node->attrs == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;           /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        memset(node->attrs, 0, (size_t)src_node->attr_count * sizeof(th_node_attr));
        node->attr_count = src_node->attr_count;
        if (dest == src) {
            th_node_share_values(src_node); /* a copy in the same tree points at the source's values */
        }
        node->attr_values_shared = src_node->attr_values_shared;
        node->values_seq = src_node->values_seq;
        for (Py_ssize_t index = 0; index < src_node->attr_count; index++) {
            const th_node_attr *from = &src_node->attrs[index];
            uint32_t atom = from->name_atom;
            if (atom >= TH_ATTR__DYNAMIC_BASE) {
                Py_ssize_t name_len;
                const char *name = th_attr_name(src, atom, &name_len);
                atom = th_attr_intern_utf8(dest, name, name_len);
                if (atom == TH_ATTR_UNKNOWN) { /* GCOVR_EXCL_BR_LINE: interning fails only to allocate */
                    return NULL;               /* GCOVR_EXCL_LINE: allocation-failure path */
                }
            }
            node->attrs[index].name_atom = atom;
            if (from->value != NULL) {
                Py_UCS4 *value = copy_attr_value(dest, src, src_node, from, memo);
                if (value == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                    return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
                }
                node->attrs[index].value = value;
                node->attrs[index].value_len = from->value_len;
            }
        }
    }
    return node;
}

th_node *th_tree_copy_node_shallow(th_tree *dest, th_tree *src, th_node *src_node) {
    th_copy_memo memo = {0};
    th_node *copy = copy_node_shallow(dest, src, src_node, &memo);
    th_copy_memo_free(&memo);
    return copy;
}

static th_node *copy_node_iterative(th_tree *dest, th_tree *src, th_node *src_node, th_copy_memo *memo) {
    th_tree_note_deepened(dest);
    th_node *root = copy_node_shallow(dest, src, src_node, memo);
    if (root == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_node *from = src_node;
    th_node *copy = root;
    for (;;) {
        if (from->first_child != NULL) {
            from = from->first_child;
            th_node *child = copy_node_shallow(dest, src, from, memo);
            if (child == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            node_append(copy, child);
            copy = child;
            continue;
        }
        while (from != src_node && from->next_sibling == NULL) {
            from = from->parent;
            copy = copy->parent;
        }
        if (from == src_node) {
            return root;
        }
        from = from->next_sibling;
        th_node *sibling = copy_node_shallow(dest, src, from, memo);
        if (sibling == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        node_append(copy->parent, sibling);
        copy = sibling;
    }
}

/* below TH_HAS_MEMO_DEPTH, so a copy nests that deep only through copy_node_iterative, which drops the nest bound */
#define TH_COPY_RECURSION_LIMIT TH_DEPTH_LIMIT(TH_HAS_MEMO_DEPTH - 1)

static th_node *copy_node_at(th_tree *dest, th_tree *src, th_node *src_node, int depth, th_copy_memo *memo) {
    if (depth == TH_COPY_RECURSION_LIMIT) {
        return copy_node_iterative(dest, src, src_node, memo);
    }
    th_node *node = copy_node_shallow(dest, src, src_node, memo);
    if (node == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (th_node *child = src_node->first_child; child != NULL; child = child->next_sibling) {
        th_node *copy = copy_node_at(dest, src, child, depth + 1, memo);
        if (copy == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        node_append(node, copy);
    }
    return node;
}

th_node *th_tree_copy_node_sharing(th_tree *dest, th_tree *src, th_node *src_node, th_copy_memo *memo) {
    if (dest->xml) {
        dest->has_xml_ns |= src->has_xml_ns;
    }
    /* a shadow root is never a light child, so only the root of a copy can carry the flag without a table entry */
    if (!th_node_is_shadow_root(src_node)) {
        return copy_node_at(dest, src, src_node, 0, memo);
    }
    th_node *copy = copy_node_at(dest, src, src_node, 0, memo);
    if (copy == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    copy->tag_flags = 0;
    return copy;
}

th_node *th_tree_copy_node(th_tree *dest, th_tree *src, th_node *src_node) {
    th_copy_memo memo = {0};
    th_node *copy = th_tree_copy_node_sharing(dest, src, src_node, &memo);
    th_copy_memo_free(&memo);
    return copy;
}

int th_tree_has_shadows(const th_tree *tree) {
    return tree->shadow_count != 0;
}

th_node *th_node_next_including_shadow(th_tree *tree, th_node *node, th_node *root) {
    th_node *shadow = th_element_shadow_root(tree, node);
    if (shadow != NULL) {
        return shadow;
    }
    if (node->first_child != NULL) {
        return node->first_child;
    }
    while (node != root) {
        if (th_node_is_shadow_root(node)) {
            node = th_shadow_host(tree, node);
            if (node->first_child != NULL) {
                return node->first_child;
            }
        } else {
            if (node->next_sibling != NULL) {
                return node->next_sibling;
            }
            node = node->parent;
        }
    }
    return NULL;
}

static th_node *copy_adopted_shadows(th_tree *dest, th_tree *src, th_node *src_node, th_copy_memo *memo) {
    th_node *root = th_tree_copy_node_sharing(dest, src, src_node, memo);
    if (root == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;    /* GCOVR_EXCL_LINE */
    }
    th_node *from = src_node;
    th_node *copy = root;
    do {
        th_node *shadow = th_element_shadow_root(src, from);
        if (shadow != NULL) {
            th_node *adopted = th_element_attach_shadow(dest, copy, th_shadow_mode(shadow));
            if (adopted == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                return NULL;       /* GCOVR_EXCL_LINE */
            }
            adopted->tag_flags = shadow->tag_flags;
            for (th_node *child = shadow->first_child; child != NULL; child = child->next_sibling) {
                th_node *child_copy = th_tree_copy_node_sharing(dest, src, child, memo);
                if (child_copy == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                    return NULL;          /* GCOVR_EXCL_LINE */
                }
                node_append(adopted, child_copy);
            }
        }
        from = th_node_next_including_shadow(src, from, src_node);
        copy = th_node_next_including_shadow(dest, copy, root);
    } while (from != NULL);
    return root;
}

th_node *th_tree_adopt_copy_sharing(th_tree *dest, th_tree *src, th_node *src_node, th_copy_memo *memo) {
    th_node *copy = src->shadow_count == 0 ? th_tree_copy_node_sharing(dest, src, src_node, memo)
                                           : copy_adopted_shadows(dest, src, src_node, memo);
    if (copy == NULL || src->xml == dest->xml) { /* GCOVR_EXCL_BR_LINE: the copy is NULL on OOM only */
        return copy;
    }
    for (th_node *node = copy; node != NULL; node = th_node_next_including_shadow(dest, node, copy)) {
        if (node->type == TH_NODE_ELEMENT && convert_element_kind(dest, node) < 0) { /* GCOVR_EXCL_BR_LINE: OOM only */
            return NULL; /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    return copy;
}

uint32_t th_tree_node_count(const th_tree *tree) {
    return tree->node_seq;
}

int th_tree_seq_wrapped(const th_tree *tree) {
    return tree->seq_wrapped;
}

void th_tree_set_document(th_tree *tree, th_node *document) {
    tree->document = document;
}

th_tree *th_tree_new_rooted(enum th_node_type type, int xml, int quirks) {
    th_tree *tree = th_tree_new();
    if (tree == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    tree->xml = xml;
    tree->quirks = quirks;
    tree->document = node_new(tree, type);
    if (tree->document == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(tree);       /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;              /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return tree;
}

/* Copy a document into an independent tree while its caller holds the source-tree lock. */
th_tree *th_tree_copy_document(th_tree *src) {
    th_tree *dest = th_tree_new();
    if (dest == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    dest->xml = src->xml;
    dest->quirks = src->quirks;
    dest->document = th_tree_copy_node(dest, src, src->document);
    if (dest->document == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(dest);       /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;              /* GCOVR_EXCL_LINE */
    }
    return dest;
}

static inline void normalize_unlink(th_tree *tree, th_node *child, int iterators) {
    if (iterators) {
        th_node_remove_silent(tree, child);
    } else {
        node_remove(child);
    }
}

static int normalize_children(th_tree *tree, th_node *root, int iterators) {
    for (th_node *child = root->first_child; child != NULL;) {
        th_node *next = child->next_sibling;
        if (child->type == TH_NODE_TEXT) {
            if (child->text_len == 0) {
                normalize_unlink(tree, child, iterators);
                child = next;
                continue;
            }
            th_node *end = next;
            Py_ssize_t merged_len = child->text_len;
            while (end != NULL && end->type == TH_NODE_TEXT) {
                th_node *after = end->next_sibling;
                if (end->text_len == 0) {
                    normalize_unlink(tree, end, iterators);
                } else {
                    merged_len += end->text_len;
                }
                end = after;
            }
            next = child->next_sibling;
            if (merged_len > child->text_len) {
                if (need_text(tree, child) == NULL) { /* GCOVR_EXCL_BR_LINE: lazy text OOM */
                    return -1;                        /* GCOVR_EXCL_LINE: allocation failure */
                }
                /* the merged text goes behind the first node's own when its buffer has room, so normalizing after
                   each append copies only the appended text */
                Py_UCS4 *merged = text_room(tree, child, merged_len, child->text_len);
                if (merged == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                    return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
                }
                Py_ssize_t offset = child->text_len;
                for (th_node *part = child->next_sibling; part != end; part = part->next_sibling) {
                    const Py_UCS4 *text = need_text(tree, part);
                    if (text == NULL) { /* GCOVR_EXCL_BR_LINE: text realization fails only on allocation failure */
                        return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
                    }
                    memcpy(merged + offset, text, (size_t)part->text_len * sizeof(Py_UCS4));
                    offset += part->text_len;
                }
                child->text_len = merged_len;
                child->script_text_closes = 0;
            }
            while (next != end) {
                th_node *after = next->next_sibling;
                normalize_unlink(tree, next, iterators);
                next = after;
            }
        }
        child = next;
    }
    return 0;
}

static int normalize_subtree(th_tree *tree, th_node *root, int iterators);

/* No Python code runs while normalize walks, so the iterator count cannot change mid-call: each literal below lets the
   compiler clone the walk, and a tree with no live iterator unlinks without testing for one. */
int th_node_normalize(th_tree *tree, th_node *root) {
    if (th_tree_has_iterators(tree)) {
        return normalize_subtree(tree, root, 1);
    }
    return normalize_subtree(tree, root, 0);
}

static int normalize_subtree(th_tree *tree, th_node *root, int iterators) {
    th_node *node = root;
    for (;;) {
        if (node == root || node->type == TH_NODE_ELEMENT) {
            if (normalize_children(tree, node, iterators) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                return -1;                                       /* GCOVR_EXCL_LINE: allocation failure */
            }
        }
        if (node->first_child != NULL) {
            node = node->first_child;
            continue;
        }
        while (node != root && node->next_sibling == NULL) {
            node = node->parent;
        }
        if (node == root) {
            return 0;
        }
        node = node->next_sibling;
    }
}

/* Construct one shell element (html/head/body/meta/title) from its ASCII tag name,
   with no attribute slots. Every shell name fits the small stack buffer, so the
   UCS4 widening never overflows. NULL on allocation failure. */
static th_node *shell_element(th_tree *tree, const char *name, Py_ssize_t len, uint16_t atom) {
    Py_UCS4 tag[8]; /* the longest shell tag is "title" (5 code points) */
    for (Py_ssize_t index = 0; index < len; index++) {
        tag[index] = (Py_UCS4)name[index];
    }
    return th_tree_make_element(tree, tag, len, atom, 0);
}

th_node *th_tree_build_shell(th_tree *tree, const Py_UCS4 *lang, Py_ssize_t lang_len, const Py_UCS4 *title,
                             Py_ssize_t title_len, const Py_UCS4 *charset, Py_ssize_t charset_len, th_node **out_head,
                             th_node **out_body) {
    static const Py_UCS4 doctype_html[] = {'h', 't', 'm', 'l'};
    th_node *document = node_new(tree, TH_NODE_DOCUMENT);
    th_node *doctype = th_tree_make_data_node(tree, TH_NODE_DOCTYPE, doctype_html, 4);
    th_node *html = shell_element(tree, "html", 4, TH_TAG_HTML);
    th_node *head = shell_element(tree, "head", 4, TH_TAG_HEAD);
    th_node *body = shell_element(tree, "body", 4, TH_TAG_BODY);
    /* allocation failure cannot be forced from a test */
    if (document == NULL || doctype == NULL || html == NULL || head == NULL || body == NULL) { /* GCOVR_EXCL_BR_LINE */
        return NULL; /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    tree->document = document;
    node_append(document, doctype);
    node_append(document, html);
    node_append(html, head);
    node_append(html, body);
    if (lang != NULL && th_node_attr_set(tree, html, "lang", 4, lang, lang_len, 1) < 0) { /* GCOVR_EXCL_BR_LINE: OOM */
        return NULL; /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (charset != NULL) {
        th_node *meta = shell_element(tree, "meta", 4, TH_TAG_META);
        if (meta == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (th_node_attr_set(tree, meta, "charset", 7, charset, charset_len, 1) < 0) { /* GCOVR_EXCL_BR_LINE: OOM */
            return NULL; /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        node_append(head, meta);
    }
    if (title != NULL) {
        th_node *title_el = shell_element(tree, "title", 5, TH_TAG_TITLE);
        if (title_el == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        node_append(head, title_el);
        if (title_len > 0) {
            th_node *text = th_tree_make_data_node(tree, TH_NODE_TEXT, title, title_len);
            if (text == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            node_append(title_el, text);
        }
    }
    *out_head = head;
    *out_body = body;
    return document;
}
