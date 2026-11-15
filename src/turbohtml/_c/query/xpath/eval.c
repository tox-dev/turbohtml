/* Evaluates a compiled program against a tree: walks the axes, filters by predicate,
   converts between the four XPath value types, and compares and combines results. */

#include "core/common.h"
#include "core/node_map.h"
#include "core/stack.h"
#include "core/vec.h"
#include "core/work_steps.h"
#include "dom/tree.h"
#include "dom/tree_internal.h"
#include "query/xpath/internal.h"
#include "query/xpath/regex.h"
#include "query/xpath/xpath.h"

#include <math.h>
#include <string.h>

/* The pattern walk calls the step helpers too, and with a second caller link-time optimization stopped inlining them
   into the forward path's step loop, so they are forced inline; a debug build keeps one copy, which the coverage gates
   need. */
#if defined(_MSC_VER)
#define XP_STEP_INLINE __forceinline
#elif defined(__OPTIMIZE__)
#define XP_STEP_INLINE inline __attribute__((always_inline))
#else
#define XP_STEP_INLINE
#endif

typedef struct {
    th_node_attr attribute;
    size_t references;
    Py_ssize_t ordinal;
    Py_ssize_t name_len;
    char name[];
} xp_attribute;

static xp_attribute *attribute_snapshot(Py_ssize_t attr) {
    uintptr_t address = (uintptr_t)attr & ~(uintptr_t)1;
    xp_attribute *snapshot;
    memcpy(&snapshot, &address, sizeof(xp_attribute *));
    return snapshot;
}

static xp_attribute *item_snapshot(xp_item item) {
    return xp_is_attribute(item.attr) && ((uintptr_t)item.attr & 1) ? attribute_snapshot(item.attr) : NULL;
}

const th_node_attr *xp_item_attribute(struct th_tree *tree, xp_item item) {
    (void)tree;
    return (uintptr_t)item.attr & 1 ? &attribute_snapshot(item.attr)->attribute : &item.node->attrs[item.attr >> 1];
}

const char *xp_item_attr_name(struct th_tree *tree, xp_item item, Py_ssize_t *len) {
    if ((uintptr_t)item.attr & 1) {
        xp_attribute *snapshot = attribute_snapshot(item.attr);
        *len = snapshot->name_len;
        return snapshot->name;
    }
    return th_attr_name(tree, item.node->attrs[item.attr >> 1].name_atom, len);
}

int xp_item_equal(xp_item left, xp_item right) {
    if (left.node != right.node) {
        return 0;
    }
    if (!xp_is_attribute(left.attr) || !xp_is_attribute(right.attr) ||
        !(((uintptr_t)left.attr | (uintptr_t)right.attr) & 1)) {
        return left.attr == right.attr;
    }
    return xp_item_attribute(NULL, left)->name_atom == xp_item_attribute(NULL, right)->name_atom;
}

uintptr_t xp_item_hash(xp_item item) {
    uintptr_t address = (uintptr_t)item.node;
    uintptr_t attr = xp_is_attribute(item.attr) ? xp_item_attribute(NULL, item)->name_atom : (uintptr_t)item.attr;
    return (address >> 4) ^ (address >> 13) ^ attr;
}

static void item_release(xp_item item) {
    xp_attribute *snapshot = item_snapshot(item);
    if (snapshot != NULL && --snapshot->references == 0) {
        PyMem_Free(snapshot);
    }
}

static int visit_set(const xp_nodeset *set, int (*visitor)(void *, struct th_node *), void *data) {
    for (Py_ssize_t index = 0; index < set->len; index++) {
        if (set->items[index].node != NULL) {
            TH_WORK_STEP();
            if (visitor(data, set->items[index].node) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                return -1;                                   /* GCOVR_EXCL_LINE */
            }
        }
    }
    return 0;
}

const xp_live_frame *xp_live_previous(const xp_live_frame *frame) {
    return frame->previous;
}

uint64_t xp_live_id(const xp_live_frame *frame) {
    return frame->id;
}

Py_ssize_t xp_live_append_count(const xp_live_frame *frame) {
    return frame->append != NULL ? frame->append->len : 0;
}

int xp_visit_frame_append(const xp_live_frame *frame, Py_ssize_t start, int (*visitor)(void *, struct th_node *),
                          void *data) {
    for (Py_ssize_t index = start; index < xp_live_append_count(frame); index++) {
        if (visitor(data, frame->append->items[index].node) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;                                             /* GCOVR_EXCL_LINE */
        }
    }
    return 0;
}

Py_ssize_t xp_live_result_count(const xp_live_frame *frame) {
    return frame->result_count;
}

/* A frame's results only grow (a function call stores its arguments one by one), so a caller that has seen the first
   `start` of them visits the rest. */
int xp_visit_frame_results(const xp_live_frame *frame, Py_ssize_t start, int (*visitor)(void *, struct th_node *),
                           void *data) {
    for (Py_ssize_t index = start; index < frame->result_count; index++) {
        if (frame->results[index].kind == XP_NODESET) {
            if (visit_set(&frame->results[index].nodes, visitor, data) < 0) { /* GCOVR_EXCL_BR_LINE: alloc failure */
                return -1;                                                    /* GCOVR_EXCL_LINE */
            }
        }
    }
    return 0;
}

int xp_visit_frame_nodes(const xp_live_frame *frame, int (*visitor)(void *, struct th_node *), void *data) {
    if (frame->node != NULL) {
        if (visitor(data, frame->node) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;                        /* GCOVR_EXCL_LINE */
        }
    }
    for (int index = 0; index < 2; index++) {
        if (frame->sets[index] != NULL) {
            if (visit_set(frame->sets[index], visitor, data) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                return -1;                                          /* GCOVR_EXCL_LINE */
            }
        }
    }
    if (xp_visit_frame_results(frame, 0, visitor, data) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                                             /* GCOVR_EXCL_LINE */
    }
    for (Py_ssize_t index = 0; frame->vars != NULL && index < frame->vars->len; index++) {
        const xp_result *value = &frame->vars->items[index].value;
        if (value->kind == XP_NODESET) {
            if (visit_set(&value->nodes, visitor, data) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                return -1;                                     /* GCOVR_EXCL_LINE */
            }
        }
    }
    return xp_visit_frame_append(frame, 0, visitor, data);
}

#if defined(_MSC_VER)
#define TH_NOINLINE __declspec(noinline)
#elif defined(__GNUC__) || defined(__clang__)
#define TH_NOINLINE __attribute__((noinline))
#else
#define TH_NOINLINE
#endif

/* Growth stays out of line so ordinary result appends can inline. */
TH_NOINLINE int ns_grow(xp_nodeset *ns) {
    size_t cap;
    size_t bytes;
    int grew = th_grow_cap((size_t)ns->cap + 1, (size_t)ns->cap, 8, sizeof(xp_item), &cap, &bytes);
    if (!grew) {   /* GCOVR_EXCL_BR_LINE: size overflow needs a length no allocation could hold */
        return -1; /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
    }
    xp_item *grown = PyMem_Realloc(ns->items, bytes);
    if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
        return -1;       /* GCOVR_EXCL_LINE */
    }
    ns->items = grown;
    ns->cap = (Py_ssize_t)cap;
    return 0;
}

int ns_push(xp_nodeset *ns, struct th_node *node, Py_ssize_t attr) {
    if (ns_append(ns, node, attr) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                       /* GCOVR_EXCL_LINE */
    }
    xp_attribute *snapshot = item_snapshot((xp_item){node, attr});
    if (snapshot != NULL) {
        snapshot->references++;
        ns->snapshots = 1;
    }
    return 0;
}

static TH_NOINLINE void release_snapshots(const xp_nodeset *ns) {
    for (Py_ssize_t index = 0; index < ns->len; index++) {
        item_release(ns->items[index]);
    }
}

void xp_nodeset_free(xp_nodeset *ns) {
    if (ns->snapshots) {
        release_snapshots(ns);
    }
    ns->snapshots = 0;
    PyMem_Free(ns->items);
    ns->items = NULL;
    ns->len = ns->cap = 0;
}

static int push_attribute(xp_nodeset *set, xp_ctx *ctx, struct th_node *node, Py_ssize_t index) {
    if (ctx->live == NULL) {
        return ns_append(set, node, index << 1);
    }
    Py_ssize_t name_len;
    const char *name = th_attr_name(ctx->tree, node->attrs[index].name_atom, &name_len);
    xp_attribute *snapshot = PyMem_Malloc(sizeof(*snapshot) + (size_t)name_len);
    if (snapshot == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;          /* GCOVR_EXCL_LINE */
    }
    snapshot->attribute = node->attrs[index];
    snapshot->references = 1;
    snapshot->ordinal = index;
    snapshot->name_len = name_len;
    memcpy(snapshot->name, name, (size_t)name_len);
    xp_item item = {node, (Py_ssize_t)((uintptr_t)snapshot | 1)};
    int rc = ns_push(set, node, item.attr);
    item_release(item);
    return rc;
}

/* Resolve a name test to a static tag atom, or TH_TAG_UNKNOWN for a name no known
   HTML tag carries (non-ASCII, mixed case, or simply unknown); such a name then
   matches only unknown-atom elements with that exact spelling. */
static uint16_t resolve_tag_atom(const Py_UCS4 *name, Py_ssize_t len) {
    char buf[64];
    if (len >= (Py_ssize_t)sizeof(buf)) { /* a name this long is no known tag; the parser never makes an empty one */
        return TH_TAG_UNKNOWN;
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        if (name[index] >= 0x80) {
            return TH_TAG_UNKNOWN;
        }
        buf[index] = (char)name[index];
    }
    return th_tag_lookup(buf, len);
}

static uint32_t resolve_attr_atom(struct th_tree *tree, const Py_UCS4 *name, Py_ssize_t len) {
    char buf[128];
    if (len >= (Py_ssize_t)sizeof(buf)) { /* no attribute name is this long; the parser never makes an empty one */
        return UINT32_MAX;
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        if (name[index] >= 0x80) {
            return UINT32_MAX;
        }
        buf[index] = (char)name[index];
    }
    return th_attr_lookup(tree, buf, len);
}

int xp_single_attribute_atom(const xp_program *prog, struct th_tree *tree, uint32_t *atom) {
    const xn *path = &prog->nodes[prog->root];
    if (path->kind != XN_PATH || path->absolute || path->second >= 0) {
        return 0;
    }
    const xn *step = &prog->nodes[path->first];
    if (step->axis != AX_ATTRIBUTE || step->test != NT_NAME || step->prefix_len != 0 || step->first >= 0 ||
        step->next >= 0) {
        return 0;
    }
    *atom = resolve_attr_atom(tree, step->str, step->str_len);
    return 1;
}

/* A step's name test resolved once before the axis walk: the local part (the suffix
   after any "prefix:"), its interned atoms, and the namespace constraint a bound prefix
   adds. With no prefix the test matches by local name in any namespace, as before. */
typedef struct {
    uint16_t atom;      /* tag atom of the local name (element axes), else 0 */
    uint32_t attr_atom; /* attr atom of the local name (attribute axis), else UINT32_MAX */
    const Py_UCS4 *local;
    Py_ssize_t local_len;
    int has_ns;         /* the test carried a namespace prefix */
    int ns_unmatchable; /* the bound URI names no namespace an HTML tree's elements carry */
    int xml_ns;         /* the prefix binds the XML namespace (xml:lang, xml:space) */
    int foreign_only;   /* an attribute test that only a foreign element of an HTML tree can satisfy */
    uint8_t want_ns;    /* enum th_ns an element must carry when has_ns and not unmatchable */
    xp_name_test_fn name_test;
    void *name_test_ctx;
    const Py_UCS4 *qualified;
    Py_ssize_t qualified_len;
    int strict_no_ns;
    int first;    /* the step only has to find a node: eval_first_step */
    int unsorted; /* the contexts come from a path's filter base, in whatever order it gave them */
    /* one walk from one node with no predicate: a reverse axis hands its nodes back in document order, so the path
       needs no sort; any other step keeps the proximity order the sort or the predicates expect */
    int document_order;
    /* an XML tree with namespace declarations compares the namespace and local name ids of its index (ns_index), with
       the ids the step's name resolves to once */
    struct xp_ns_index *ns_index;
    uint32_t want_local_id;
    uint32_t want_ns_id;
    const Py_UCS4 *uri;
    Py_ssize_t uri_len;
} step_match;

static int ucs4_eq_ascii(const Py_UCS4 *text, Py_ssize_t text_len, const char *ascii, Py_ssize_t ascii_len) {
    if (text_len != ascii_len) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < text_len; index++) {
        if (text[index] != (Py_UCS4)(unsigned char)ascii[index]) {
            return 0;
        }
    }
    return 1;
}

/* XML name tests. An unprefixed test matches only an element or attribute in no namespace, and a prefixed one matches a
   name whose own prefix binds the test's URI, as XPath 1.0 defines them. A tree that never held an xmlns declaration
   puts every name in no namespace, so its tests keep the plain compare. Any other XML tree carries a namespace index:
   each element and attribute records interned ids for its namespace and local name, filled lazily by one pass over the
   tree and rebuilt after a mutation, so a name test compares integers per node. */

static const Py_UCS4 xp_xml_uri[] = {'h', 't', 't', 'p', ':', '/', '/', 'w', 'w', 'w', '.', 'w',
                                     '3', '.', 'o', 'r', 'g', '/', 'X', 'M', 'L', '/', '1', '9',
                                     '9', '8', '/', 'n', 'a', 'm', 'e', 's', 'p', 'a', 'c', 'e'};
#define XP_XML_URI_LEN ((Py_ssize_t)(sizeof(xp_xml_uri) / sizeof(xp_xml_uri[0])))
#define XP_XML_URI_UTF8 "http://www.w3.org/XML/1998/namespace"

#define NS_NONE 0u                     /* no namespace */
#define NS_UNBOUND (UINT32_MAX - 1)    /* a prefix no declaration binds, or a foreign element */
#define NS_IMPOSSIBLE (UINT32_MAX - 2) /* a test whose URI is empty: nothing matches it */
#define NS_UNSET UINT32_MAX            /* a slot the index has not filled */

typedef struct {
    uint32_t ns;         /* interned namespace id, NS_NONE or NS_UNBOUND */
    uint32_t local;      /* interned local name id, NS_UNSET while unfilled */
    uint32_t attrs;      /* base of this element's attribute pairs */
    uint32_t attr_count; /* how many attribute pairs follow base */
} ns_slot;

typedef struct {
    uint32_t ns;
    uint32_t local;
} ns_pair;

typedef struct {
    uint32_t atom;     /* the declaration attribute's atom */
    uint32_t previous; /* the id it bound before this element, NS_UNSET if none */
} ns_undo;

typedef struct {
    char *bytes;
    Py_ssize_t len;
    uint32_t hash;
} ns_string;

/* The namespace index of one tree, owned by the tree and freed with it. slots are by node creation sequence; an element
   the index has not visited keeps NS_UNSET there, so a node added since the last fill is filled on first use. The
   strings table interns UTF-8 names and URIs to ids that stay valid for the tree's lifetime. */
struct xp_ns_index {
    struct th_tree *tree;
    uint64_t attr_version;
    uint64_t unlinks;
    ns_slot *slots;
    size_t slot_capacity;
    ns_pair *pairs;
    size_t pair_count;
    size_t pair_capacity;
    ns_string *strings;
    size_t string_count;
    size_t string_capacity;
    uint32_t *buckets;
    size_t bucket_capacity;
    uint32_t *latest; /* by declaration atom: the id bound by the nearest open declaration, NS_UNSET if none */
    size_t latest_capacity;
    ns_undo *undo;
    size_t undo_count;
    size_t undo_capacity;
    size_t *marks; /* undo_count when each open element was entered */
    size_t mark_count;
    size_t mark_capacity;
    char *scratch;
    size_t scratch_capacity;
    char *decls;
    size_t decls_capacity;
    uint32_t default_atom;
    uint32_t xml_id;
};

static Py_ssize_t xml_utf8(const Py_UCS4 *src, Py_ssize_t len, char *out) {
    Py_ssize_t pos = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_UCS4 ch = src[index];
        if (ch < 0x80) {
            out[pos++] = (char)ch;
        } else if (ch < 0x800) {
            out[pos++] = (char)(0xC0 | (ch >> 6));
            out[pos++] = (char)(0x80 | (ch & 0x3F));
        } else if (ch < 0x10000) {
            out[pos++] = (char)(0xE0 | (ch >> 12));
            out[pos++] = (char)(0x80 | ((ch >> 6) & 0x3F));
            out[pos++] = (char)(0x80 | (ch & 0x3F));
        } else {
            out[pos++] = (char)(0xF0 | (ch >> 18));
            out[pos++] = (char)(0x80 | ((ch >> 12) & 0x3F));
            out[pos++] = (char)(0x80 | ((ch >> 6) & 0x3F));
            out[pos++] = (char)(0x80 | (ch & 0x3F));
        }
    }
    return pos;
}

static char *ns_scratch(struct xp_ns_index *index, size_t needed) {
    if (needed > index->scratch_capacity) {
        char *grown = PyMem_Realloc(index->scratch, needed);
        if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return NULL;     /* GCOVR_EXCL_LINE */
        }
        index->scratch = grown;
        index->scratch_capacity = needed;
    }
    return index->scratch;
}

static uint32_t ns_hash(const char *bytes, Py_ssize_t len) {
    uint32_t hash = 2166136261u;
    for (Py_ssize_t at = 0; at < len; at++) {
        hash = (hash ^ (unsigned char)bytes[at]) * 16777619u;
    }
    return hash;
}

static int ns_rehash(struct xp_ns_index *index, size_t capacity) {
    uint32_t *buckets = PyMem_Calloc(capacity, sizeof(uint32_t));
    if (buckets == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;         /* GCOVR_EXCL_LINE */
    }
    for (size_t id = 0; id < index->string_count; id++) {
        size_t slot = index->strings[id].hash & (capacity - 1);
        while (buckets[slot] != 0) {
            slot = (slot + 1) & (capacity - 1);
        }
        buckets[slot] = (uint32_t)(id + 1);
    }
    PyMem_Free(index->buckets);
    index->buckets = buckets;
    index->bucket_capacity = capacity;
    return 0;
}

/* The id of a UTF-8 name, interned on first use; 0 out of memory. Ids start at 1, so 0 is never a real id. */
static uint32_t ns_intern(struct xp_ns_index *index, const char *bytes, Py_ssize_t len) {
    uint32_t hash = ns_hash(bytes, len);
    if ((index->string_count + 1) * 2 > index->bucket_capacity) {
        size_t capacity = index->bucket_capacity == 0 ? 16 : index->bucket_capacity * 2;
        if (ns_rehash(index, capacity) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return 0;                         /* GCOVR_EXCL_LINE */
        }
    }
    size_t mask = index->bucket_capacity - 1;
    size_t slot = hash & mask;
    while (index->buckets[slot] != 0) {
        const ns_string *known = &index->strings[index->buckets[slot] - 1];
        if (known->len == len && memcmp(known->bytes, bytes, (size_t)len) == 0) {
            return index->buckets[slot];
        }
        slot = (slot + 1) & mask;
    }
    if (index->string_count == index->string_capacity) {
        size_t capacity = index->string_capacity == 0 ? 16 : index->string_capacity * 2;
        ns_string *grown = PyMem_Realloc(index->strings, capacity * sizeof(ns_string));
        if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return 0;        /* GCOVR_EXCL_LINE */
        }
        index->strings = grown;
        index->string_capacity = capacity;
    }
    char *copy = PyMem_Malloc((size_t)len);
    if (copy == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return 0;       /* GCOVR_EXCL_LINE */
    }
    memcpy(copy, bytes, (size_t)len);
    index->strings[index->string_count] = (ns_string){copy, len, hash};
    index->string_count++;
    index->buckets[slot] = (uint32_t)index->string_count;
    return (uint32_t)index->string_count;
}

/* The id of a code point name, interned as its UTF-8 bytes; 0 out of memory. */
static uint32_t ns_intern_ucs4(struct xp_ns_index *index, const Py_UCS4 *text, Py_ssize_t len) {
    char *bytes = ns_scratch(index, (size_t)len * 4 + 1);
    if (bytes == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return 0;        /* GCOVR_EXCL_LINE */
    }
    return ns_intern(index, bytes, xml_utf8(text, len, bytes));
}

static int ns_reserve_slots(struct xp_ns_index *index, size_t needed) {
    if (needed <= index->slot_capacity) {
        return 0;
    }
    size_t capacity = needed * 2;
    ns_slot *grown = PyMem_Realloc(index->slots, capacity * sizeof(ns_slot));
    if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;       /* GCOVR_EXCL_LINE */
    }
    for (size_t at = index->slot_capacity; at < capacity; at++) {
        grown[at] = (ns_slot){.ns = NS_NONE, .local = NS_UNSET};
    }
    index->slots = grown;
    index->slot_capacity = capacity;
    return 0;
}

static int ns_reserve_pairs(struct xp_ns_index *index, size_t needed) {
    if (needed <= index->pair_capacity) {
        return 0;
    }
    size_t capacity = needed * 2;
    ns_pair *grown = PyMem_Realloc(index->pairs, capacity * sizeof(ns_pair));
    if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;       /* GCOVR_EXCL_LINE */
    }
    index->pairs = grown;
    index->pair_capacity = capacity;
    return 0;
}

static uint32_t ns_latest(const struct xp_ns_index *index, uint32_t atom) {
    if (atom == UINT32_MAX || atom >= index->latest_capacity) {
        return NS_UNSET;
    }
    return index->latest[atom];
}

static int ns_bind(struct xp_ns_index *index, uint32_t atom, uint32_t id) {
    if (atom >= index->latest_capacity) {
        size_t capacity = (size_t)atom * 2 + 16;
        uint32_t *grown = PyMem_Realloc(index->latest, capacity * sizeof(uint32_t));
        if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;       /* GCOVR_EXCL_LINE */
        }
        for (size_t at = index->latest_capacity; at < capacity; at++) {
            grown[at] = NS_UNSET;
        }
        index->latest = grown;
        index->latest_capacity = capacity;
    }
    if (index->undo_count == index->undo_capacity) {
        size_t capacity = index->undo_capacity == 0 ? 16 : index->undo_capacity * 2;
        ns_undo *grown = PyMem_Realloc(index->undo, capacity * sizeof(ns_undo));
        if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;       /* GCOVR_EXCL_LINE */
        }
        index->undo = grown;
        index->undo_capacity = capacity;
    }
    index->undo[index->undo_count++] = (ns_undo){atom, index->latest[atom]};
    index->latest[atom] = id;
    return 0;
}

static void ns_undo_to(struct xp_ns_index *index, size_t mark) {
    while (index->undo_count > mark) {
        const ns_undo *entry = &index->undo[--index->undo_count];
        index->latest[entry->atom] = entry->previous;
    }
}

static int ns_open(struct xp_ns_index *index) {
    if (index->mark_count == index->mark_capacity) {
        size_t capacity = index->mark_capacity == 0 ? 16 : index->mark_capacity * 2;
        size_t *grown = PyMem_Realloc(index->marks, capacity * sizeof(size_t));
        if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;       /* GCOVR_EXCL_LINE */
        }
        index->marks = grown;
        index->mark_capacity = capacity;
    }
    index->marks[index->mark_count++] = index->undo_count;
    return 0;
}

static void ns_close(struct xp_ns_index *index) {
    ns_undo_to(index, index->marks[--index->mark_count]);
}

static int ns_is_declaration(const char *name, Py_ssize_t len) {
    return (len == 5 && memcmp(name, "xmlns", 5) == 0) || (len > 6 && memcmp(name, "xmlns:", 6) == 0);
}

static uint32_t ns_default_id(const struct xp_ns_index *index) {
    uint32_t bound = ns_latest(index, index->default_atom);
    return bound == NS_UNSET ? NS_NONE : bound;
}

/* The id a prefix binds in scope: xml by definition, else the nearest open declaration, NS_UNBOUND when none. */
static int ns_prefixed_id(struct xp_ns_index *index, struct th_tree *tree, const char *prefix, Py_ssize_t prefix_len,
                          uint32_t *id) {
    if (prefix_len == 3 && memcmp(prefix, "xml", 3) == 0) {
        *id = index->xml_id;
        return 0;
    }
    static const char xmlns_colon[6] = {'x', 'm', 'l', 'n', 's', ':'};
    size_t needed = (size_t)prefix_len + 6;
    if (needed > index->decls_capacity) {
        char *grown = PyMem_Realloc(index->decls, needed);
        if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;       /* GCOVR_EXCL_LINE */
        }
        index->decls = grown;
        index->decls_capacity = needed;
    }
    memcpy(index->decls, xmlns_colon, sizeof(xmlns_colon));
    memcpy(index->decls + 6, prefix, (size_t)prefix_len);
    uint32_t bound = ns_latest(index, th_attr_lookup(tree, index->decls, (Py_ssize_t)needed));
    *id = bound == NS_UNSET ? NS_UNBOUND : bound;
    return 0;
}

/* Binds the declarations of one element, then fills its slot and attribute pairs unless an earlier fill did. */
static int ns_enter(struct xp_ns_index *index, struct th_tree *tree, struct th_node *node) {
    th_node_attr *attrs;
    Py_ssize_t count = th_node_attributes(node, &attrs);
    for (Py_ssize_t at = 0; at < count; at++) {
        Py_ssize_t name_len;
        const char *name = th_attr_name(tree, attrs[at].name_atom, &name_len);
        if (!ns_is_declaration(name, name_len)) {
            continue;
        }
        uint32_t id = NS_NONE;
        if (attrs[at].value_len > 0) {
            id = ns_intern_ucs4(index, attrs[at].value, attrs[at].value_len);
            if (id == 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                return -1; /* GCOVR_EXCL_LINE */
            }
        }
        if (ns_bind(index, attrs[at].name_atom, id) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;                                     /* GCOVR_EXCL_LINE */
        }
    }
    if (node->seq < index->slot_capacity && index->slots[node->seq].local != NS_UNSET) {
        return 0;
    }
    if (ns_reserve_slots(index, (size_t)node->seq + 1) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                                            /* GCOVR_EXCL_LINE */
    }
    ns_slot *slot = &index->slots[node->seq];
    char *qname = ns_scratch(index, (size_t)node->text_len * 4 + 1);
    if (qname == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;       /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t qname_len = xml_utf8(node->text, node->text_len, qname);
    const char *colon = memchr(qname, ':', (size_t)qname_len);
    const char *local = colon == NULL ? qname : colon + 1;
    Py_ssize_t local_len = qname_len - (local - qname);
    uint32_t ns = NS_NONE;
    if (colon == NULL) {
        ns = ns_default_id(index);
    } else {
        if (ns_prefixed_id(index, tree, qname, colon - qname, &ns) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;                                                    /* GCOVR_EXCL_LINE */
        }
    }
    uint32_t local_id = ns_intern(index, local, local_len);
    if (local_id == 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;       /* GCOVR_EXCL_LINE */
    }
    if (ns_reserve_pairs(index, index->pair_count + (size_t)count) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                                                        /* GCOVR_EXCL_LINE */
    }
    uint32_t base = (uint32_t)index->pair_count;
    for (Py_ssize_t at = 0; at < count; at++) {
        Py_ssize_t name_len;
        const char *name = th_attr_name(tree, attrs[at].name_atom, &name_len);
        const char *attr_colon = memchr(name, ':', (size_t)name_len);
        const char *attr_local = name;
        Py_ssize_t attr_local_len = name_len;
        ns_pair pair = {NS_NONE, 0};
        if (attr_colon != NULL) {
            attr_local = attr_colon + 1;
            attr_local_len = name_len - (attr_local - name);
            int status = ns_prefixed_id(index, tree, name, attr_colon - name, &pair.ns);
            if (status < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                return -1;    /* GCOVR_EXCL_LINE */
            }
        }
        pair.local = ns_intern(index, attr_local, attr_local_len);
        if (pair.local == 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;         /* GCOVR_EXCL_LINE */
        }
        index->pairs[index->pair_count++] = pair;
    }
    slot->ns = ns;
    slot->local = local_id;
    slot->attrs = base;
    slot->attr_count = (uint32_t)count;
    return 0;
}

/* One pass over the subtree at top, binding declarations on the way down and undoing them on the way up. */
static int ns_walk(struct xp_ns_index *index, struct th_tree *tree, struct th_node *top) {
    struct th_node *node = top;
    for (;;) {
        TH_WORK_STEP();
        if (node->type == TH_NODE_ELEMENT) {
            if (ns_open(index) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                return -1;            /* GCOVR_EXCL_LINE */
            }
            if (ns_enter(index, tree, node) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                return -1;                         /* GCOVR_EXCL_LINE */
            }
        }
        if (node->first_child != NULL) {
            node = node->first_child;
            continue;
        }
        for (;;) {
            if (node->type == TH_NODE_ELEMENT) {
                ns_close(index);
            }
            if (node == top) {
                return 0;
            }
            if (node->next_sibling != NULL) {
                node = node->next_sibling;
                break;
            }
            node = node->parent;
        }
    }
}

static int ns_fill(struct xp_ns_index *index, struct th_tree *tree, struct th_node *top) {
    index->default_atom = th_attr_lookup(tree, "xmlns", 5);
    index->mark_count = 0;
    index->undo_count = 0;
    int status = ns_walk(index, tree, top);
    ns_undo_to(index, 0);
    index->mark_count = 0;
    return status;
}

/* A node the index has not filled: fill the whole tree below its top, which every later node under that top reuses. */
static TH_NOINLINE int ns_ensure(struct xp_ns_index *index, struct th_node *node) {
    struct th_node *top = node;
    while (top->parent != NULL) {
        top = top->parent;
    }
    if (ns_fill(index, index->tree, top) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                              /* GCOVR_EXCL_LINE */
    }
    return 0;
}

static XP_STEP_INLINE int ns_element_matches(const step_match *match, struct th_node *node) {
    struct xp_ns_index *index = match->ns_index;
    if (node->seq >= index->slot_capacity || index->slots[node->seq].local == NS_UNSET) {
        if (ns_ensure(index, node) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;                    /* GCOVR_EXCL_LINE */
        }
    }
    const ns_slot *slot = &index->slots[node->seq];
    return slot->local == match->want_local_id && slot->ns == match->want_ns_id;
}

static int ns_attribute_matches(const step_match *match, struct th_node *node, Py_ssize_t attr) {
    struct xp_ns_index *index = match->ns_index;
    th_node_attr *attrs;
    Py_ssize_t count = th_node_attributes(node, &attrs);
    if (node->seq >= index->slot_capacity || index->slots[node->seq].local == NS_UNSET ||
        index->slots[node->seq].attr_count != (uint32_t)count) {
        if (node->seq < index->slot_capacity) {
            index->slots[node->seq].local = NS_UNSET;
        }
        if (ns_ensure(index, node) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;                    /* GCOVR_EXCL_LINE */
        }
    }
    const ns_slot *slot = &index->slots[node->seq];
    const ns_pair *pair = &index->pairs[slot->attrs + (size_t)attr];
    return pair->local == match->want_local_id && pair->ns == match->want_ns_id;
}

/* The namespace index of tree, created on first use and brought up to date: a mutation since the last fill empties
   its slots (lazily refilled) and its attribute pairs. NULL out of memory. */
struct xp_ns_index *xp_ns_index_for(struct th_tree *tree) {
    struct xp_ns_index *index = tree->ns_index;
    if (index == NULL) {
        index = PyMem_Calloc(1, sizeof(struct xp_ns_index));
        if (index == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return NULL;     /* GCOVR_EXCL_LINE */
        }
        index->tree = tree;
        tree->ns_index = index;
        index->xml_id = ns_intern(index, XP_XML_URI_UTF8, (Py_ssize_t)sizeof(XP_XML_URI_UTF8) - 1);
        if (index->xml_id == 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return NULL;          /* GCOVR_EXCL_LINE */
        }
    }
    uint64_t attr_version = th_tree_attr_version(tree);
    uint64_t unlinks = th_tree_unlinks();
    if (index->attr_version != attr_version || index->unlinks != unlinks) {
        for (size_t at = 0; at < index->slot_capacity; at++) {
            index->slots[at].local = NS_UNSET;
        }
        index->pair_count = 0;
        index->attr_version = attr_version;
        index->unlinks = unlinks;
    }
    return index;
}

void xp_ns_index_free(struct xp_ns_index *index) {
    if (index == NULL) {
        return;
    }
    for (size_t id = 0; id < index->string_count; id++) {
        PyMem_Free(index->strings[id].bytes);
    }
    PyMem_Free(index->strings);
    PyMem_Free(index->buckets);
    PyMem_Free(index->slots);
    PyMem_Free(index->pairs);
    PyMem_Free(index->latest);
    PyMem_Free(index->undo);
    PyMem_Free(index->marks);
    PyMem_Free(index->scratch);
    PyMem_Free(index->decls);
    PyMem_Free(index);
}

static XP_STEP_INLINE int element_name_matches(struct th_node *node, const step_match *match) {
    if (match->name_test != NULL) {
        return match->name_test(match->name_test_ctx, node, -1, match->qualified, match->qualified_len);
    }
    if (match->ns_index != NULL) {
        return ns_element_matches(match, node);
    }
    /* An HTML element with a builtin atom is spelled as that atom, so the atoms decide.
       Every other element compares its spelling: an XML tree interns every element as
       TH_TAG_UNKNOWN, and a foreign element keeps the case the tree builder gave it, so
       the builtin foreignobject atom names an SVG element spelled foreignObject. */
    if (node->atom != TH_TAG_UNKNOWN && node->ns == TH_NS_HTML) {
        if (node->atom != match->atom) {
            return 0;
        }
    } else if (node->text_len != match->local_len ||
               memcmp(node->text, match->local, (size_t)match->local_len * sizeof(Py_UCS4)) != 0) {
        return 0;
    }
    if (match->strict_no_ns && node->ns != TH_NS_HTML) {
        return 0;
    }
    if (!match->has_ns) {
        return 1;
    }
    return !match->ns_unmatchable && node->ns == match->want_ns;
}

/* Whether a node satisfies a step's node test on an element-principal axis. */
static XP_STEP_INLINE int node_test_matches(struct th_node *node, const xn *step, const step_match *match) {
    switch (step->test) {
    case NT_NAME:
        return node->type == TH_NODE_ELEMENT ? element_name_matches(node, match) : 0;
    case NT_STAR:
        return node->type == TH_NODE_ELEMENT;
    case NT_TEXT:
        return node->type == TH_NODE_TEXT;
    case NT_COMMENT:
        return node->type == TH_NODE_COMMENT;
    case NT_PI:
        return node->type == TH_NODE_PI;
    default: /* NT_NODE */
        return 1;
    }
}

/* Pre-order successor over the whole tree, or NULL past the last node. */
struct th_node *document_next(struct th_node *node) {
    if (node->first_child != NULL) {
        return node->first_child;
    }
    while (node->parent != NULL && node->next_sibling == NULL) {
        node = node->parent;
    }
    return node->next_sibling;
}

/* Pre-order predecessor over the whole tree, or NULL before the first node. */
static struct th_node *document_previous(struct th_node *node) {
    if (node->prev_sibling == NULL) {
        return node->parent;
    }
    node = node->prev_sibling;
    while (node->last_child != NULL) {
        node = node->last_child;
    }
    return node;
}

/* Reverse the items in [from, out->len) in place (turns the document-order run a
   forward walk produced into the reverse-document proximity order). */
static TH_NOINLINE void reverse_items(xp_nodeset *out, Py_ssize_t from) {
    Py_ssize_t high = out->len - 1;
    while (from < high) {
        xp_item swap = out->items[from];
        out->items[from] = out->items[high];
        out->items[high] = swap;
        from++;
        high--;
    }
}

/* Push node when it passes the step's node test. Returns 0, or -1 on allocation
   failure (which cannot be forced from a test). */
static int emit_if_match(xp_nodeset *out, struct th_node *node, const xn *step, const step_match *match) {
    TH_WORK_STEP();
    int matched = node_test_matches(node, step, match);
    if (matched <= 0) {
        return matched;
    }
    return ns_append(out, node, -1);
}

static int apply_descendant_step(xp_nodeset *out, struct th_node *root, const xn *step, const step_match *match,
                                 const xp_nodeset *contexts, Py_ssize_t *covered) {
    while (*covered < contexts->len && contexts->items[*covered].node == root) {
        (*covered)++;
    }
    struct th_node *node = step->axis == AX_DESCENDANT_OR_SELF ? root : root->first_child;
    for (; node != NULL; node = preorder_next(node, root)) {
        while (*covered < contexts->len && contexts->items[*covered].node == node) {
            (*covered)++;
        }
        if (emit_if_match(out, node, step, match) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;                                   /* GCOVR_EXCL_LINE */
        }
    }
    return 0;
}

static XP_STEP_INLINE int apply_step(xp_nodeset *out, struct th_node *ctx, enum xp_axis axis, const xn *step,
                                     const step_match *match, xp_ctx *context) {
    switch (axis) {
    case AX_ATTRIBUTE: {
        /* node() and * both match every attribute; a name test matches by atom; a
           prefixed name test matches only an xml:-prefixed attribute in the XML
           namespace, which an XML tree or a foreign element carries (the HTML parser
           leaves xml:lang on an HTML element in no namespace); text()/comment()/
           processing-instruction() match no attribute. A non-element context node
           carries no attribute storage, so the axis yields nothing. */
        th_node_attr *attrs;
        Py_ssize_t attr_count = th_node_attributes(ctx, &attrs);
        int eligible = !match->foreign_only || ctx->ns != TH_NS_HTML;
        for (Py_ssize_t index = 0; index < attr_count; index++) {
            int hit = step->test == NT_STAR || step->test == NT_NODE ||
                      (step->test == NT_NAME && eligible && attrs[index].name_atom == match->attr_atom);
            if (step->test == NT_NAME && match->name_test != NULL) {
                hit = match->name_test(match->name_test_ctx, ctx, index, match->qualified, match->qualified_len);
                if (hit < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                    return -1; /* GCOVR_EXCL_LINE */
                }
            }
            if (step->test == NT_NAME && match->ns_index != NULL) {
                hit = ns_attribute_matches(match, ctx, index);
                if (hit < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                    return -1; /* GCOVR_EXCL_LINE */
                }
            }
            if (hit) {
                if (push_attribute(out, context, ctx, index) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                    return -1;                                      /* GCOVR_EXCL_LINE */
                }
            }
        }
        return 0;
    }
    case AX_SELF:
        return emit_if_match(out, ctx, step, match);
    case AX_CHILD:
        for (struct th_node *child = ctx->first_child; child != NULL; child = child->next_sibling) {
            if (emit_if_match(out, child, step, match) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                return -1;                                    /* GCOVR_EXCL_LINE */
            }
        }
        return 0;
    case AX_DESCENDANT_OR_SELF:
        if (emit_if_match(out, ctx, step, match) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;                                  /* GCOVR_EXCL_LINE */
        }
        TH_FALLTHROUGH; /* descendant-or-self is self plus the descendant walk */
    case AX_DESCENDANT:
        for (struct th_node *node = ctx->first_child; node != NULL; node = preorder_next(node, ctx)) {
            if (emit_if_match(out, node, step, match) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                return -1;                                   /* GCOVR_EXCL_LINE */
            }
        }
        return 0;
    case AX_PARENT:
        return ctx->parent != NULL ? emit_if_match(out, ctx->parent, step, match) : 0;
    case AX_ANCESTOR_OR_SELF:
    case AX_ANCESTOR: {
        Py_ssize_t from = out->len;
        struct th_node *node = axis == AX_ANCESTOR_OR_SELF ? ctx : ctx->parent;
        for (; node != NULL; node = node->parent) {
            if (emit_if_match(out, node, step, match) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                return -1;                                   /* GCOVR_EXCL_LINE */
            }
        }
        if (match->document_order) {
            reverse_items(out, from);
        }
        return 0;
    }
    case AX_FOLLOWING_SIBLING:
        for (struct th_node *node = ctx->next_sibling; node != NULL; node = node->next_sibling) {
            if (emit_if_match(out, node, step, match) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                return -1;                                   /* GCOVR_EXCL_LINE */
            }
        }
        return 0;
    case AX_FOLLOWING: {
        /* everything after ctx's whole subtree, in document order */
        struct th_node *start = NULL;
        for (struct th_node *up = ctx; up != NULL && start == NULL; up = up->parent) {
            start = up->next_sibling;
        }
        for (struct th_node *node = start; node != NULL; node = document_next(node)) {
            if (emit_if_match(out, node, step, match) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                return -1;                                   /* GCOVR_EXCL_LINE */
            }
        }
        return 0;
    }
    case AX_PRECEDING: {
        /* everything before ctx in document order except its ancestors, walked backward
           so it lands in the axis's reverse-document proximity order; that walk meets the
           ancestors nearest first, so the next one due is the only node to test against */
        struct th_node *ancestor = ctx->parent;
        Py_ssize_t from = out->len;
        for (struct th_node *node = document_previous(ctx); node != NULL; node = document_previous(node)) {
            TH_WORK_STEP();
            if (node == ancestor) {
                ancestor = node->parent;
                continue;
            }
            if (emit_if_match(out, node, step, match) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                return -1;                                   /* GCOVR_EXCL_LINE */
            }
        }
        if (match->document_order) {
            reverse_items(out, from);
        }
        return 0;
    }
    case AX_NAMESPACE: {
        /* Every element carries the implicit `xml` namespace node, the only namespace
           node an HTML tree exposes; it is the item with attr == -2. The node test
           matches it for *, node(), and the xml prefix, but not text()/comment()/pi(). */
        int matches = step->test == NT_STAR || step->test == NT_NODE ||
                      (step->test == NT_NAME && step->str_len == 3 && step->str[0] == 'x' && step->str[1] == 'm' &&
                       step->str[2] == 'l');
        if (ctx->type == TH_NODE_ELEMENT && matches) {
            return ns_append(out, ctx, -2);
        }
        return 0;
    }
    default: { /* AX_PRECEDING_SIBLING */
        Py_ssize_t from = out->len;
        for (struct th_node *node = ctx->prev_sibling; node != NULL; node = node->prev_sibling) {
            if (emit_if_match(out, node, step, match) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                return -1;                                   /* GCOVR_EXCL_LINE */
            }
        }
        if (match->document_order) {
            reverse_items(out, from);
        }
        return 0;
    }
    }
}

/* An attribute or namespace node has no children and no siblings; its parent is the
   owner element, and in document order it follows the owner and precedes the owner's
   children (XPath 1.0 §5). Only node() matches it on the self axis, whose principal
   node type is element. */
static int apply_owned_step(xp_nodeset *out, xp_item item, const xn *step, const step_match *match) {
    switch (step->axis) {
    case AX_SELF:
    case AX_DESCENDANT_OR_SELF:
        return step->test == NT_NODE ? ns_push(out, item.node, item.attr) : 0;
    case AX_ANCESTOR_OR_SELF:
        if (step->test == NT_NODE && ns_push(out, item.node, item.attr) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;                                                         /* GCOVR_EXCL_LINE */
        }
        TH_FALLTHROUGH; /* the owner element is the nearest ancestor */
    case AX_ANCESTOR:
        return apply_step(out, item.node, AX_ANCESTOR_OR_SELF, step, match, NULL);
    case AX_PARENT:
        return apply_step(out, item.node, AX_SELF, step, match, NULL);
    case AX_FOLLOWING:
        if (apply_step(out, item.node, AX_DESCENDANT, step, match, NULL) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;                                                          /* GCOVR_EXCL_LINE */
        }
        return apply_step(out, item.node, AX_FOLLOWING, step, match, NULL);
    case AX_PRECEDING:
        return apply_step(out, item.node, AX_PRECEDING, step, match, NULL);
    default: /* the child, descendant, sibling, attribute, and namespace axes are empty */
        return 0;
    }
}

/* Push the first node the step selects from item, where apply_step and apply_owned_step push them all, for the axes
   that take a walk (match->first). Each walk starts at the node nearest to the context, so a match next to it is found
   at once. */
static int apply_first_step(xp_nodeset *out, xp_item item, const xn *step, const step_match *match) {
    struct th_node *ctx = item.node;
    struct th_node *node = NULL;
    enum xp_axis axis = step->axis;
    Py_ssize_t before = out->len;
    int owned = item.attr != -1;
    if (owned) {
        /* as in apply_owned_step: only node() matches the attribute or namespace node itself, it has no children
           and no siblings, and its owner element is its nearest ancestor */
        switch (axis) {
        case AX_DESCENDANT_OR_SELF:
        case AX_ANCESTOR_OR_SELF:
            if (step->test == NT_NODE) {
                return ns_push(out, item.node, item.attr);
            }
            if (axis == AX_DESCENDANT_OR_SELF) {
                return 0;
            }
            break;
        case AX_ANCESTOR:
            axis = AX_ANCESTOR_OR_SELF;
            break;
        case AX_FOLLOWING:
        case AX_PRECEDING:
            break;
        default:
            return 0;
        }
    }
    switch (axis) {
    case AX_CHILD:
    case AX_FOLLOWING_SIBLING:
        node = axis == AX_CHILD ? ctx->first_child : ctx->next_sibling;
        for (; node != NULL && out->len == before; node = node->next_sibling) {
            if (emit_if_match(out, node, step, match) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                return -1;                                   /* GCOVR_EXCL_LINE */
            }
        }
        return 0;
    case AX_PRECEDING_SIBLING:
        for (node = ctx->prev_sibling; node != NULL && out->len == before; node = node->prev_sibling) {
            if (emit_if_match(out, node, step, match) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                return -1;                                   /* GCOVR_EXCL_LINE */
            }
        }
        return 0;
    case AX_DESCENDANT:
    case AX_DESCENDANT_OR_SELF:
        node = axis == AX_DESCENDANT ? ctx->first_child : ctx;
        for (; node != NULL && out->len == before; node = preorder_next(node, ctx)) {
            if (emit_if_match(out, node, step, match) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                return -1;                                   /* GCOVR_EXCL_LINE */
            }
        }
        return 0;
    case AX_ANCESTOR:
    case AX_ANCESTOR_OR_SELF:
        node = axis == AX_ANCESTOR ? ctx->parent : ctx;
        for (; node != NULL && out->len == before; node = node->parent) {
            if (emit_if_match(out, node, step, match) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                return -1;                                   /* GCOVR_EXCL_LINE */
            }
        }
        return 0;
    case AX_FOLLOWING:
        /* what follows an attribute or namespace node starts with its owner's children, and what follows a node
           starts after its whole subtree */
        node = owned ? ctx->first_child : NULL;
        for (struct th_node *up = ctx; up != NULL && node == NULL; up = up->parent) {
            node = up->next_sibling;
        }
        for (; node != NULL && out->len == before; node = document_next(node)) {
            if (emit_if_match(out, node, step, match) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                return -1;                                   /* GCOVR_EXCL_LINE */
            }
        }
        return 0;
    default: { /* AX_PRECEDING */
        /* the walk back through the document meets the ancestors nearest first, so the next one due is the only
           node to leave out */
        struct th_node *ancestor = ctx->parent;
        for (node = document_previous(ctx); node != NULL && out->len == before; node = document_previous(node)) {
            if (node == ancestor) {
                ancestor = node->parent;
                continue;
            }
            if (emit_if_match(out, node, step, match) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                return -1;                                   /* GCOVR_EXCL_LINE */
            }
        }
        return 0;
    }
    }
}

/* The last step of a path that is only asked whether it selects a node: stop at the first one. Collecting the whole
   axis for every candidate of an outer step, to read nothing but the length of the set, is quadratic on a long sibling
   list. It stays out of line, and off apply_step, so that the ordinary step compiles as it did. */
static TH_NOINLINE int eval_first_step(const xn *step, const step_match *match, xp_ctx *ctx, const xp_nodeset *current,
                                       xp_nodeset *following) {
    (void)ctx; /* only the operation limit of a fuzz build reads it */
    for (Py_ssize_t index = 0; index < current->len && following->len == 0; index++) {
        if (apply_first_step(following, current->items[index], step, match) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;                                                             /* GCOVR_EXCL_LINE */
        }
#ifdef TH_OPERATION_LIMIT
        if (xp_charge(ctx, 1 + (size_t)following->len) < 0) {
            return -3;
        }
#endif
    }
    return 0;
}

/* A node's address through MurmurHash3's 64-bit finalizer: the key of the evaluator's node tables. core/node_map.h
   hashes the creation sequence instead, and a document chooses which sequences a node-set holds, so members picked to
   share one stretch of such a table make every probe a walk of the whole run. It does not choose where the allocator
   put the tree. */
static uint64_t node_address_hash(const struct th_node *node) {
    uint64_t key = (uint64_t)(uintptr_t)node;
    key ^= key >> 33;
    key *= UINT64_C(0xFF51AFD7ED558CCD);
    key ^= key >> 33;
    key *= UINT64_C(0xC4CEB9FE1A85EC53);
    key ^= key >> 33;
    return key;
}

/* The nodes one step's walks have passed, open addressed and at most half full. */
typedef struct {
    const struct th_node **slots;
    size_t capacity;
    size_t count;
} xp_seen;

static size_t seen_slot(const xp_seen *seen, const struct th_node *node) {
    size_t slot = (size_t)node_address_hash(node) & (seen->capacity - 1);
    while (seen->slots[slot] != NULL && seen->slots[slot] != node) {
        slot = (slot + 1) & (seen->capacity - 1);
    }
    return slot;
}

static int seen_has(const xp_seen *seen, const struct th_node *node) {
    return seen->capacity != 0 && seen->slots[seen_slot(seen, node)] != NULL;
}

/* Add a node that seen_has just reported absent. */
static int seen_add(xp_seen *seen, const struct th_node *node) {
    if ((seen->count + 1) * 2 > seen->capacity) {
        xp_seen grown = {NULL, seen->capacity == 0 ? 64 : seen->capacity * 2, seen->count};
        grown.slots = PyMem_Calloc(grown.capacity, sizeof(*grown.slots));
        if (grown.slots == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;             /* GCOVR_EXCL_LINE */
        }
        for (size_t index = 0; index < seen->capacity; index++) {
            if (seen->slots[index] != NULL) {
                grown.slots[seen_slot(&grown, seen->slots[index])] = seen->slots[index];
            }
        }
        PyMem_Free(seen->slots);
        *seen = grown;
    }
    seen->slots[seen_slot(seen, node)] = node;
    seen->count++;
    return 0;
}

/* The next node along an axis that follows a single link of the tree. */
static struct th_node *axis_link(const struct th_node *node, enum xp_axis axis) {
    switch (axis) {
    case AX_FOLLOWING_SIBLING:
        return node->next_sibling;
    case AX_PRECEDING_SIBLING:
        return node->prev_sibling;
    default: /* AX_ANCESTOR, AX_ANCESTOR_OR_SELF */
        return node->parent;
    }
}

/* An ancestor or sibling axis for one of several contexts. Each walk follows its link to the end, so this one stops at
   the first node an earlier context's walk passed: everything beyond it is in out already. */
static int apply_linked_step(xp_nodeset *out, xp_item item, const xn *step, const step_match *match, xp_seen *seen) {
    int ancestors = step->axis == AX_ANCESTOR || step->axis == AX_ANCESTOR_OR_SELF;
    struct th_node *node = item.node;
    Py_ssize_t from = out->len;
    if (item.attr != -1) {
        /* as in apply_owned_step: no siblings, and the owner element is the nearest ancestor */
        if (!ancestors) {
            return 0;
        }
    } else if (step->axis != AX_ANCESTOR_OR_SELF) {
        node = axis_link(node, step->axis);
    }
    for (; node != NULL && !seen_has(seen, node); node = axis_link(node, step->axis)) {
        if (seen_add(seen, node) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;                  /* GCOVR_EXCL_LINE */
        }
        if (emit_if_match(out, node, step, match) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;                                   /* GCOVR_EXCL_LINE */
        }
    }
    /* No predicate reads the proximity order, so a backward run is left in document order, the attribute after its
       owner: sort_unique then has nothing to reorder when the contexts came in that order too. */
    if (step->axis != AX_FOLLOWING_SIBLING) {
        reverse_items(out, from);
    }
    if (item.attr != -1 && step->axis == AX_ANCESTOR_OR_SELF && step->test == NT_NODE) {
        return ns_push(out, item.node, item.attr);
    }
    return 0;
}

/* Drop from the items one context or walk just added, [from, set->len), the nodes an earlier one already contributed,
   and remember the rest. An attribute or namespace node is its context's own and leads them, so it neither repeats
   nor moves. */
static int drop_repeats(xp_nodeset *set, Py_ssize_t from, xp_seen *seen) {
    Py_ssize_t write_pos = from;
    for (Py_ssize_t read_pos = from; read_pos < set->len; read_pos++) {
        xp_item item = set->items[read_pos];
        if (item.attr == -1) {
            if (seen_has(seen, item.node)) {
                continue;
            }
            if (seen_add(seen, item.node) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                return -1;                       /* GCOVR_EXCL_LINE */
            }
        }
        set->items[write_pos++] = item;
    }
    set->len = write_pos;
    return 0;
}

/* The following or preceding axis for several contexts at once. Each walk runs to an end of the document, so it also
   yields every node that a context it passes on the way would. Contexts in document order let one walk pass all the
   others. One that no walk passed (an extension function can return them unordered, and a variable can hold nodes of
   several trees) gets a walk of its own, less the nodes an earlier walk yielded. */
static int apply_document_step(xp_nodeset *out, const xn *step, const step_match *match, const xp_nodeset *contexts,
                               xp_seen *seen) {
    const xp_item *items = contexts->items;
    if (step->axis == AX_PRECEDING) {
        Py_ssize_t left = contexts->len;
        while (left > 0) {
            /* backward from the last context left; the axis leaves out the context and its ancestors, and the walk
               meets those nearest first, so the next one due is the only node to test against */
            Py_ssize_t from = out->len;
            struct th_node *skip = items[left - 1].node;
            for (struct th_node *node = skip; node != NULL; node = document_previous(node)) {
                while (left > 0 && items[left - 1].node == node) {
                    left--;
                }
                if (node == skip) {
                    skip = node->parent;
                } else if (emit_if_match(out, node, step, match) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                    return -1;                                          /* GCOVR_EXCL_LINE */
                }
            }
            reverse_items(out, from); /* into document order, which sort_unique keeps without sorting */
            if (left > 0 || seen->count > 0) {
                if (drop_repeats(out, from, seen) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                    return -1;                           /* GCOVR_EXCL_LINE */
                }
            }
        }
        return 0;
    }
    Py_ssize_t index = 0;
    while (index < contexts->len) {
        xp_item item = items[index++];
        /* a context inside this one ends no later, so its walk covers this one's */
        struct th_node *inner = index < contexts->len && item.attr == -1 ? items[index].node : NULL;
        while (inner != NULL && inner != item.node) {
            inner = inner->parent;
        }
        if (inner != NULL) {
            continue;
        }
        /* after the whole subtree of a node; after the owner element of an attribute or namespace node, as for the
           owner's other ones */
        while (index < contexts->len && items[index].node == item.node) {
            index++;
        }
        Py_ssize_t from = out->len;
        struct th_node *node = item.attr == -1 ? NULL : document_next(item.node);
        for (struct th_node *up = item.node; item.attr == -1 && up != NULL && node == NULL; up = up->parent) {
            node = up->next_sibling;
        }
        for (; node != NULL; node = document_next(node)) {
            while (index < contexts->len && items[index].node == node) {
                index++;
            }
            if (emit_if_match(out, node, step, match) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                return -1;                                   /* GCOVR_EXCL_LINE */
            }
        }
        if (index < contexts->len || seen->count > 0) {
            if (drop_repeats(out, from, seen) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                return -1;                           /* GCOVR_EXCL_LINE */
            }
        }
    }
    return 0;
}

/* A table keyed on the node's address. th_node_map starts its probe from the creation sequence, which the document
   chooses, so a document can crowd its members into one run of slots. Addresses are not the document's to choose, and
   the mix below spreads the low bits the allocator leaves zero. */
typedef struct {
    const struct th_node *node;
    union {
        Py_ssize_t value;         /* a sibling position */
        struct th_node *root;     /* a memoized root (root_store) */
        const th_node_attr *attr; /* a memoized language declaration (declaration_store) */
    };
} addr_entry;

typedef struct {
    addr_entry *entries;
    size_t capacity;
    size_t count;
} addr_map;

static size_t addr_slot(const addr_map *map, const struct th_node *node) {
    size_t slot = (size_t)node_address_hash(node) & (map->capacity - 1);
    while (map->entries[slot].node != NULL && map->entries[slot].node != node) {
        TH_WORK_STEP();
        slot = (slot + 1) & (map->capacity - 1);
    }
    return slot;
}

static Py_ssize_t addr_map_find(const addr_map *map, const struct th_node *node) {
    return map->capacity == 0 ? 0 : map->entries[addr_slot(map, node)].value;
}

/* Values are positive indices; zero marks an absent node. Callers insert each node once. */
static int addr_map_insert(addr_map *map, const struct th_node *node, Py_ssize_t value) {
    if (map->count + 1 > map->capacity / 2) {
        size_t capacity;
        size_t bytes;
        const int grew = th_grow_cap((map->count + 1) * 2, map->capacity, 16, sizeof(addr_entry), &capacity, &bytes);
        if (!grew) {   /* GCOVR_EXCL_BR_LINE: allocation size overflow */
            return -1; /* GCOVR_EXCL_LINE: allocation size overflow */
        }
        addr_map grown = {PyMem_Calloc(1, bytes), capacity, map->count};
        if (grown.entries == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;               /* GCOVR_EXCL_LINE */
        }
        for (size_t index = 0; index < map->capacity; index++) {
            if (map->entries[index].node != NULL) {
                grown.entries[addr_slot(&grown, map->entries[index].node)] = map->entries[index];
            }
        }
        PyMem_Free(map->entries);
        *map = grown;
    }
    map->entries[addr_slot(map, node)] = (addr_entry){node, {value}};
    map->count++;
    return 0;
}

/* Point node at value, adding it when the map does not hold it yet. */
static int addr_map_set(addr_map *map, const struct th_node *node, Py_ssize_t value) {
    if (map->capacity != 0 && map->entries[addr_slot(map, node)].node == node) {
        map->entries[addr_slot(map, node)].value = value;
        return 0;
    }
    return addr_map_insert(map, node, value);
}

static struct th_node *known_root(const addr_map *roots, const struct th_node *node) {
    return roots->capacity == 0 ? NULL : roots->entries[addr_slot(roots, node)].root;
}

/* Point node at root in the roots map, replacing any root it held. Out of memory, node stays out of the map, which
   costs later climbs the steps it would have saved. */
static void root_store(addr_map *roots, const struct th_node *node, struct th_node *root) {
    if (known_root(roots, node) == NULL) {
        if (addr_map_insert(roots, node, 0) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return;                                /* GCOVR_EXCL_LINE */
        }
    }
    roots->entries[addr_slot(roots, node)].root = root;
}

/* The positions map holds the sibling positions the evaluation has numbered, keyed by child. A parent's children are
   numbered together the first time two of them need ordering, so a set that keeps meeting one long sibling list walks
   it once per evaluation. The roots map holds the root a deep climb reached for each node it passed (climb_memoized),
   as of `unlinks`. langs is the evaluation's lang() memo when its caller passes none (xp_lang_memoized). */
struct xp_caches {
    xr_cache regex;
    addr_map positions;
    addr_map roots;
    uint64_t unlinks;
    xp_lang_memo *langs;
};

static xp_caches *caches_get(xp_caches **caches) {
    if (*caches == NULL) {
        *caches = PyMem_Calloc(1, sizeof(xp_caches));
    }
    return *caches;
}

xr_cache *xp_regex_cache(xp_ctx *ctx) {
    xp_caches *caches = caches_get(ctx->caches);
    if (caches == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
        return NULL;      /* GCOVR_EXCL_LINE */
    }
    return &caches->regex;
}

static Py_ssize_t growth_spent(xp_caches *const *caches) {
    return *caches == NULL ? 0 : (*caches)->regex.grown;
}

static void growth_restore(xp_caches **caches, Py_ssize_t spent) {
    if (*caches != NULL) {
        (*caches)->regex.grown = spent;
    }
}

TH_NOINLINE void xp_order_forget(xp_caches *caches) {
    if (caches != NULL && caches->positions.entries != NULL) {
        PyMem_Free(caches->positions.entries);
        caches->positions = (addr_map){0};
    }
}

static Py_ssize_t known_position(xp_caches *const *caches, struct th_node *node) {
    return *caches == NULL ? 0 : addr_map_find(&(*caches)->positions, node);
}

/* Ancestors a root climb passes before it consults the root memo, so the memo stays off the path of ordinary depths. */
#define XP_ROOT_CLIMB 32

/* The next node a climb from node visits: the root the memo holds for it, else its parent. */
static struct th_node *climb_step(const addr_map *roots, struct th_node *node) {
    struct th_node *known = known_root(roots, node);
    return known != NULL ? known : node->parent;
}

/* Climbs from `from`, a node XP_ROOT_CLIMB ancestors below the context, to the root through the root memo, then points
   every node visited at that root. A climb through a deep tree thus walks each node about once per evaluation, however
   many context nodes lie beneath it. A Python callback may move nodes, so the memo is kept only while no node has left
   its parent (th_tree_unlinks); a memoized root that has since gained a parent is climbed on from. */
static struct th_node *climb_memoized(xp_ctx *ctx, struct th_node *document, struct th_node *from) {
    xp_caches *caches = caches_get(ctx->caches);
    if (caches == NULL) {                                  /* GCOVR_EXCL_BR_LINE: allocation failure */
        while (from != document && from->parent != NULL) { /* GCOVR_EXCL_LINE */
            from = from->parent;                           /* GCOVR_EXCL_LINE */
        } /* GCOVR_EXCL_LINE */
        return from; /* GCOVR_EXCL_LINE */
    }
    uint64_t unlinks = th_tree_unlinks();
    if (caches->unlinks != unlinks) {
        PyMem_Free(caches->roots.entries);
        caches->roots = (addr_map){0};
        caches->unlinks = unlinks;
    }
    struct th_node *top = from;
    while (top != document && top->parent != NULL) {
        TH_WORK_STEP();
        top = climb_step(&caches->roots, top);
    }
    for (struct th_node *walked = from; walked != top;) {
        TH_WORK_STEP();
        struct th_node *next = climb_step(&caches->roots, walked);
        if (next != top) {
            root_store(&caches->roots, walked, top);
        }
        walked = next;
    }
    return top;
}

/* lang()'s answers past its first ancestors: each node a climb passed maps to the attribute declaring its language, or
   to the parentless top a climb that met no declaration reached. They are read off the tree's attributes and parent
   links, so the memo is kept only while no attribute was written (th_tree_attr_version; a write can also move the
   array an answer points into) and no node has left its parent (th_tree_unlinks). Python code between two lang() calls
   (an extension function, a finalizer the pins release) bumps one or the other when it edits either. Every other link
   gives a parentless node a parent: the declarations below a top stay where they were, but one can now sit above it,
   so a top that has gained a parent answers only for the nodes up to it. */
struct xp_lang_memo {
    addr_map declared;
    addr_map tops;
    uint64_t attr_version;
    uint64_t unlinks;
};

void xp_lang_memo_free(xp_lang_memo *memo) {
    if (memo != NULL) {
        PyMem_Free(memo->declared.entries);
        PyMem_Free(memo->tops.entries);
        PyMem_Free(memo);
    }
}

static const th_node_attr *known_declaration(const addr_map *declared, const struct th_node *node) {
    return declared->capacity == 0 ? NULL : declared->entries[addr_slot(declared, node)].attr;
}

/* Point node, which the map does not hold, at the attribute declaring its language. Out of memory, node stays out of
   the map, which costs later climbs the steps it would have saved. */
static void declaration_store(addr_map *declared, const struct th_node *node, const th_node_attr *attr) {
    if (addr_map_insert(declared, node, 0) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return;                                   /* GCOVR_EXCL_LINE */
    }
    declared->entries[addr_slot(declared, node)].attr = attr;
}

/* The memo of ctx's caller, else of its evaluation, emptied if the tree changed since it was filled; NULL out of
   memory. */
static xp_lang_memo *lang_memo(xp_ctx *ctx) {
    xp_lang_memo **slot = ctx->langs;
    if (slot == NULL) {
        xp_caches *caches = caches_get(ctx->caches);
        if (caches == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return NULL;      /* GCOVR_EXCL_LINE */
        }
        slot = &caches->langs;
    }
    if (*slot == NULL) {
        *slot = PyMem_Calloc(1, sizeof(xp_lang_memo));
        if (*slot == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return NULL;     /* GCOVR_EXCL_LINE */
        }
    }
    xp_lang_memo *memo = *slot;
    uint64_t attr_version = th_tree_attr_version(ctx->tree);
    uint64_t unlinks = th_tree_unlinks();
    if (memo->attr_version != attr_version || memo->unlinks != unlinks) {
        PyMem_Free(memo->declared.entries);
        PyMem_Free(memo->tops.entries);
        *memo = (xp_lang_memo){.attr_version = attr_version, .unlinks = unlinks};
    }
    return memo;
}

/* Climbs from `from` to the first node that declares a language, is the top, or has its answer in the memo, then
   points every node it passed at that answer. A predicate that asks for every node of a deep tree, or of one below an
   element with many attributes, thus reads each attribute and follows each parent link about once per memo. */
const th_node_attr *xp_lang_memoized(xp_ctx *ctx, struct th_node *from, uint32_t xml_lang_atom) {
    xp_lang_memo *memo = lang_memo(ctx);
    if (memo == NULL) {                             /* GCOVR_EXCL_BR_LINE: allocation failure */
        for (; from != NULL; from = from->parent) { /* GCOVR_EXCL_LINE */
            const th_node_attr *declared = xp_node_lang_attr(ctx->tree, from, xml_lang_atom); /* GCOVR_EXCL_LINE */
            if (declared != NULL) {                                                           /* GCOVR_EXCL_LINE */
                return declared;                                                              /* GCOVR_EXCL_LINE */
            }
        } /* GCOVR_EXCL_LINE */
        return NULL; /* GCOVR_EXCL_LINE */
    }
    const th_node_attr *declared = NULL;
    struct th_node *top = NULL;
    struct th_node *node = from;
    for (;;) {
        TH_WORK_STEP();
        declared = known_declaration(&memo->declared, node);
        if (declared != NULL) {
            break;
        }
        struct th_node *known = known_root(&memo->tops, node);
        if (known != NULL && known->parent == NULL) {
            top = known;
            break;
        }
        if (known != NULL) {
            node = known->parent;
            continue;
        }
        declared = xp_node_lang_attr(ctx->tree, node, xml_lang_atom);
        if (declared != NULL || node->parent == NULL) {
            top = node;
            node = node->parent;
            break;
        }
        node = node->parent;
    }
    for (struct th_node *walked = from; walked != node;) {
        TH_WORK_STEP();
        struct th_node *known = known_root(&memo->tops, walked);
        struct th_node *next = known != NULL ? known->parent : walked->parent;
        if (declared != NULL) {
            declaration_store(&memo->declared, walked, declared);
        } else {
            root_store(&memo->tops, walked, top);
        }
        walked = next;
    }
    return declared;
}

struct th_node *tree_root(xp_ctx *ctx) {
    xp_root *root = ctx->root;
    if (root->node != NULL && (root->fixed || !root->mixed)) {
        return root->node;
    }
    struct th_node *document = th_tree_document(ctx->tree);
    struct th_node *node = ctx->node;
    if (root->mixed) {
        for (int budget = XP_ROOT_CLIMB; node != document && node->parent != NULL; node = node->parent) {
            TH_WORK_STEP();
            if (--budget < 0) {
                return climb_memoized(ctx, document, node);
            }
        }
        return node;
    }
    while (node != document && node->parent != NULL) {
        TH_WORK_STEP();
        node = node->parent;
    }
    /* A parsed fragment's root is where `/` stops, yet the parent axis goes on to the document node above it, which
       is its own root: that one tree has two. */
    root->mixed |= document != NULL && document->parent != NULL;
    if (!root->mixed) {
        root->node = node;
    }
    return node;
}

/* The node's depth, or -1 once the walk spends *budget. */
static Py_ssize_t node_depth(struct th_node *node, Py_ssize_t *budget) {
    Py_ssize_t remaining = *budget;
    for (struct th_node *up = node->parent; up != NULL; up = up->parent) {
        TH_WORK_STEP();
        if (--remaining < 0) {
            *budget = 0;
            return -1;
        }
    }
    Py_ssize_t depth = *budget - remaining;
    *budget = remaining;
    return depth;
}

/* The order of two children of one parent: from their positions once the evaluation has numbered that parent's
   children, else walking forward from left until right or the end of the list. The budget the caller shares across a
   whole sort is what keeps a long list from paying that walk for every pair. */
static int sibling_order(xp_caches **caches, struct th_node *left, struct th_node *right, Py_ssize_t *budget) {
    Py_ssize_t left_position = known_position(caches, left);
    if (left_position != 0) {
        return left_position < known_position(caches, right) ? -1 : 1;
    }
    Py_ssize_t steps = *budget;
    struct th_node *ahead = left->next_sibling;
    while (ahead != right && ahead != NULL && steps > 0) {
        TH_WORK_STEP();
        ahead = ahead->next_sibling;
        steps--;
    }
    *budget = steps;
    if (ahead == right) {
        return -1;
    }
    if (ahead == NULL && left->parent == NULL) {
        /* A parentless node has no siblings, so its tree is not the right node's: the set's ranks decide. */
        return XP_ORDER_UNKNOWN;
    }
    return ahead == NULL ? 1 : XP_ORDER_UNKNOWN;
}

/* Negative when left precedes right in document (pre-order); positive otherwise; XP_ORDER_UNKNOWN once the walks spend
   the budget or the nodes lie in separate trees. Never called with left == right. */
static int node_before(xp_caches **caches, struct th_node *left, struct th_node *right, Py_ssize_t *budget) {
    struct th_node *left_parent = left->parent;
    struct th_node *right_parent = right->parent;
    if (right_parent == left) {
        return -1;
    }
    if (left_parent == right) {
        return 1;
    }
    struct th_node *walk_left = left;
    struct th_node *walk_right = right;
    /* Most pairs a sort meets are cousins a level or two apart: find their sibling ancestors without the walks to
       the root that the general case takes. */
    int near = left_parent != right_parent && left_parent != NULL && right_parent != NULL;
    if (near && left_parent->parent == right_parent) {
        walk_left = left_parent;
    } else if (near && right_parent->parent == left_parent) {
        walk_right = right_parent;
    } else if (near && left_parent->parent == right_parent->parent) {
        walk_left = left_parent;
        walk_right = right_parent;
    } else if (left_parent != right_parent) {
        Py_ssize_t dx = node_depth(left, budget);
        Py_ssize_t dy = dx < 0 ? -1 : node_depth(right, budget);
        if (dy < 0) {
            return XP_ORDER_UNKNOWN;
        }
        for (Py_ssize_t index = dx; index > dy; index--) {
            walk_left = walk_left->parent;
        }
        for (Py_ssize_t index = dy; index > dx; index--) {
            walk_right = walk_right->parent;
        }
        if (walk_left == walk_right) {
            return dx < dy ? -1 : 1; /* the shallower original node is an ancestor of the other */
        }
        while (walk_left->parent != walk_right->parent) {
            walk_left = walk_left->parent;
            walk_right = walk_right->parent;
        }
    }
    if (walk_left->next_sibling == walk_right) {
        return -1;
    }
    if (walk_right->next_sibling == walk_left) {
        return 1;
    }
    return sibling_order(caches, walk_left, walk_right, budget);
}

/* Out of line, so the sort's own calls of node_before inline as they did. */
TH_NOINLINE int xp_node_order(xp_caches **caches, struct th_node *left, struct th_node *right, Py_ssize_t *budget) {
    return node_before(caches, left, right, budget);
}

/* Order items sharing a node: the node itself first, then its attributes, then its
   namespace node, mirroring document order within an element. */
Py_ssize_t xp_item_rank(Py_ssize_t attr) {
    if (attr == -1) {
        return 0; /* the node */
    }
    if (attr == -2) {
        return PY_SSIZE_T_MAX; /* the namespace node, after the attributes */
    }
    xp_attribute *snapshot = item_snapshot((xp_item){NULL, attr});
    return (snapshot == NULL ? attr >> 1 : snapshot->ordinal) + 1;
}

static int same_node_order(const xp_item *left, const xp_item *right) {
    if (left->attr == right->attr) {
        return 0;
    }
    Py_ssize_t left_rank = xp_item_rank(left->attr);
    Py_ssize_t right_rank = xp_item_rank(right->attr);
    if (left_rank != right_rank) {
        return left_rank < right_rank ? -1 : 1;
    }
    uint32_t left_name = xp_item_attribute(NULL, *left)->name_atom;
    uint32_t right_name = xp_item_attribute(NULL, *right)->name_atom;
    return left_name < right_name ? -1 : 1;
}

static int item_order(xp_caches **caches, const xp_item *left, const xp_item *right, Py_ssize_t *budget) {
    TH_WORK_STEP();
    if (left->node == right->node) {
        return same_node_order(left, right);
    }
    /* inline for the next sibling, which a sorted step result meets at nearly every pair */
    return left->node->next_sibling == right->node ? -1 : node_before(caches, left->node, right->node, budget);
}

/* An item with its node's preorder rank among the set. */
typedef struct {
    xp_item item;
    Py_ssize_t rank;
} ranked_item;

/* item_order's answer for every pair, read from the ranks. The ranks number the trees one after another, so nodes of
   separate trees order by their tree's place in the set; a comparator that answered 1 both ways for them would leave
   qsort's result to the libc. */
static TH_NOINLINE int ranked_order(const void *pa, const void *pb) {
    const ranked_item *left = pa;
    const ranked_item *right = pb;
    TH_WORK_STEP();
    if (left->item.node == right->item.node) {
        return same_node_order(&left->item, &right->item);
    }
    return left->rank < right->rank ? -1 : 1;
}

typedef struct {
    struct th_node *node;
    Py_ssize_t parent;
    Py_ssize_t child;
    Py_ssize_t sibling;
    Py_ssize_t rank;
} order_vertex;

typedef struct {
    order_vertex *items;
    Py_ssize_t len;
    Py_ssize_t cap;
    addr_map index;
} order_skeleton;

typedef struct {
    Py_ssize_t position;
    Py_ssize_t vertex;
} order_child;

static int child_order(const void *pa, const void *pb) {
    const order_child *left = pa;
    const order_child *right = pb;
    return (left->position > right->position) - (left->position < right->position);
}

/* One copy of the map insert for the ranking path, which only unusual sets reach. Inlined at each site, its growth
   counts against the LTO unit-growth budget and pushes unrelated hot inlines (the selector matcher's) over it. */
static TH_NOINLINE int rank_map_insert(addr_map *map, struct th_node *node, Py_ssize_t value) {
    return addr_map_insert(map, node, value);
}

static Py_ssize_t skeleton_add(order_skeleton *skeleton, struct th_node *node) {
    if (skeleton->len == skeleton->cap) {
        size_t cap;
        size_t bytes;
        int grew =
            th_grow_cap((size_t)skeleton->len + 1, (size_t)skeleton->cap, 16, sizeof(order_vertex), &cap, &bytes);
        order_vertex *grown = grew ? PyMem_Realloc(skeleton->items, bytes) : NULL; /* GCOVR_EXCL_BR_LINE: overflow */
        if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;       /* GCOVR_EXCL_LINE */
        }
        skeleton->items = grown;
        skeleton->cap = (Py_ssize_t)cap;
    }
    if (rank_map_insert(&skeleton->index, node, skeleton->len + 1) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;                                                        /* GCOVR_EXCL_LINE */
    }
    skeleton->items[skeleton->len] = (order_vertex){node, -1, -1, -1, 0};
    return skeleton->len++;
}

/* The node's position among its siblings, or -1 on allocation failure. */
static Py_ssize_t sibling_position(xp_caches **caches, struct th_node *node) {
    Py_ssize_t position = known_position(caches, node);
    if (position == 0) {
        xp_caches *owner = caches_get(caches);
        if (owner == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;       /* GCOVR_EXCL_LINE */
        }
        Py_ssize_t index = 0;
        for (struct th_node *child = node->parent->first_child; child != NULL; child = child->next_sibling) {
            TH_WORK_STEP();
            if (rank_map_insert(&owner->positions, child, ++index) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                return -1;                                                /* GCOVR_EXCL_LINE */
            }
        }
        position = addr_map_find(&owner->positions, node);
    }
    return position;
}

/* Put the children of every skeleton vertex that has several in sibling order. */
static int order_children(xp_caches **caches, order_skeleton *skeleton) {
    order_child *children = PyMem_Malloc((size_t)skeleton->len * sizeof(*children));
    if (children == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;          /* GCOVR_EXCL_LINE */
    }
    order_vertex *vertices = skeleton->items;
    for (Py_ssize_t vertex = 0; vertex < skeleton->len; vertex++) {
        if (vertices[vertex].child < 0 || vertices[vertices[vertex].child].sibling < 0) {
            continue;
        }
        Py_ssize_t count = 0;
        for (Py_ssize_t child = vertices[vertex].child; child >= 0; child = vertices[child].sibling) {
            Py_ssize_t position = sibling_position(caches, vertices[child].node);
            if (position < 0) {       /* GCOVR_EXCL_BR_LINE: allocation failure */
                PyMem_Free(children); /* GCOVR_EXCL_LINE */
                return -1;            /* GCOVR_EXCL_LINE */
            }
            children[count++] = (order_child){position, child};
        }
        qsort(children, (size_t)count, sizeof(*children), child_order);
        vertices[vertex].child = children[0].vertex;
        for (Py_ssize_t index = 0; index < count; index++) {
            vertices[children[index].vertex].sibling = index + 1 < count ? children[index + 1].vertex : -1;
        }
    }
    PyMem_Free(children);
    return 0;
}

/* Climb from every distinct node one level per round, joining a climb to the first vertex another has reached, until
   one climb is left or every climb stands on a parentless node. That visits only the nodes between the set and the
   point where its paths join, not whole root paths. Returns how many climbs are left, their tops in heads, or -1 on
   allocation failure. */
static Py_ssize_t climb(order_skeleton *skeleton, Py_ssize_t *heads, const ranked_item *items, Py_ssize_t len) {
    Py_ssize_t active = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        if (addr_map_find(&skeleton->index, items[index].item.node) == 0) {
            heads[active] = skeleton_add(skeleton, items[index].item.node);
            if (heads[active++] < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                return -1;             /* GCOVR_EXCL_LINE */
            }
        }
    }
    for (int climbing = 1; active > 1 && climbing;) {
        climbing = 0;
        Py_ssize_t kept = 0;
        for (Py_ssize_t index = 0; index < active; index++) {
            TH_WORK_STEP();
            Py_ssize_t below = heads[index];
            struct th_node *parent = skeleton->items[below].node->parent;
            if (parent == NULL) {
                heads[kept++] = below;
                continue;
            }
            climbing = 1;
            Py_ssize_t above = addr_map_find(&skeleton->index, parent) - 1;
            if (above < 0) {
                above = skeleton_add(skeleton, parent);
                if (above < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                    return -1;   /* GCOVR_EXCL_LINE */
                }
                heads[kept++] = above;
            }
            skeleton->items[below].parent = above;
            skeleton->items[below].sibling = skeleton->items[above].child;
            skeleton->items[above].child = below;
        }
        active = kept;
    }
    return active;
}

/* Number the skeleton under each top in preorder, the tops one after another in the order the climb reached them. */
static void number_skeleton(order_skeleton *skeleton, const Py_ssize_t *heads, Py_ssize_t active) {
    order_vertex *vertices = skeleton->items;
    Py_ssize_t rank = 0;
    for (Py_ssize_t index = 0; index < active; index++) {
        Py_ssize_t top = heads[index];
        Py_ssize_t vertex = top;
        for (;;) {
            TH_WORK_STEP();
            vertices[vertex].rank = rank++;
            if (vertices[vertex].child >= 0) {
                vertex = vertices[vertex].child;
                continue;
            }
            while (vertex != top && vertices[vertex].sibling < 0) {
                vertex = vertices[vertex].parent;
            }
            if (vertex == top) {
                break;
            }
            vertex = vertices[vertex].sibling;
        }
    }
}

/* Fill each item's rank. Costs the skeleton between the set and where its paths join, plus one walk of each
   sibling list the evaluation has not numbered yet, instead of a walk per compared pair. */
static int rank_items(xp_caches **caches, ranked_item *items, Py_ssize_t len) {
    order_skeleton skeleton = {0};
    Py_ssize_t *heads = PyMem_Malloc((size_t)len * sizeof(*heads));
    Py_ssize_t active = heads == NULL ? -1 : climb(&skeleton, heads, items, len); /* GCOVR_EXCL_BR_LINE: alloc */
    int rc = active < 0 ? -1 : order_children(caches, &skeleton);                 /* GCOVR_EXCL_BR_LINE: alloc */
    if (rc == 0) {                                                                /* GCOVR_EXCL_BR_LINE: alloc */
        number_skeleton(&skeleton, heads, active);
        for (Py_ssize_t index = 0; index < len; index++) {
            const order_vertex *vertex = &skeleton.items[addr_map_find(&skeleton.index, items[index].item.node) - 1];
            items[index].rank = vertex->rank;
        }
    }
    PyMem_Free(heads);
    PyMem_Free(skeleton.items);
    PyMem_Free(skeleton.index.entries);
    return rc;
}

/* The items of both sets, target's first, ranked together; NULL on allocation failure. */
static TH_NOINLINE ranked_item *rank_sets(xp_caches **caches, const xp_nodeset *target, const xp_nodeset *source) {
    Py_ssize_t len = target->len + source->len;
    ranked_item *ranked = PyMem_Malloc((size_t)len * sizeof(*ranked));
    if (ranked == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;      /* GCOVR_EXCL_LINE */
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        ranked[index].item = index < target->len ? target->items[index] : source->items[index - target->len];
    }
    if (rank_items(caches, ranked, len) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        PyMem_Free(ranked);                    /* GCOVR_EXCL_LINE */
        return NULL;                           /* GCOVR_EXCL_LINE */
    }
    return ranked;
}

/* Put ns in document order from ranks when the bounded walks found it out of order or could not settle its order. */
static int sort_ranked(xp_caches **caches, xp_nodeset *ns, int unsorted) {
    const xp_nodeset none = {0};
    ranked_item *ranked = rank_sets(caches, ns, &none);
    if (ranked == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;        /* GCOVR_EXCL_LINE */
    }
    for (Py_ssize_t index = 1; !unsorted && index < ns->len; index++) {
        unsorted = ranked_order(&ranked[index - 1], &ranked[index]) > 0;
    }
    if (unsorted) {
        qsort(ranked, (size_t)ns->len, sizeof(*ranked), ranked_order);
        for (Py_ssize_t index = 0; index < ns->len; index++) {
            ns->items[index] = ranked[index].item;
        }
    }
    PyMem_Free(ranked);
    return 0;
}

Py_ssize_t xp_first_out_of_order(xp_caches **caches, const xp_nodeset *set, Py_ssize_t start) {
    const xp_nodeset none = {0};
    ranked_item *ranked = rank_sets(caches, set, &none);
    if (ranked == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
        return -1;        /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t previous = start;
    Py_ssize_t index = start + 1;
    for (; index < set->len; index++) {
        if (set->items[index].attr != -1 || set->items[index].node == set->items[previous].node) {
            continue;
        }
        if (ranked_order(&ranked[previous], &ranked[index]) > 0) {
            break;
        }
        previous = index;
    }
    PyMem_Free(ranked);
    return index;
}

static int sort_unique(xp_caches **caches, xp_nodeset *ns) {
    if (ns->len < 2) {
        return 0;
    }
    if (ns->snapshots) {
        size_t capacity = 8;
        while (capacity / 2 < (size_t)ns->len) {
            capacity *= 2;
        }
        xp_item *entries = PyMem_Calloc(capacity, sizeof(*entries));
        if (entries == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;         /* GCOVR_EXCL_LINE */
        }
        Py_ssize_t write = 0;
        for (Py_ssize_t index = 0; index < ns->len; index++) {
            xp_item item = ns->items[index];
            size_t slot = xp_item_hash(item) & (capacity - 1);
            while (entries[slot].node != NULL && !xp_item_equal(entries[slot], item)) {
                slot = (slot + 1) & (capacity - 1);
            }
            if (entries[slot].node == NULL) {
                entries[slot] = item;
                ns->items[write++] = item;
            } else {
                item_release(item);
            }
        }
        PyMem_Free(entries);
        ns->len = write;
    }
    /* Forward axes already produce tree order; preserve it without sorting again. */
    Py_ssize_t budget = xp_order_budget(ns->len);
    for (Py_ssize_t index = 1; index < ns->len; index++) {
        int order_found = item_order(caches, &ns->items[index - 1], &ns->items[index], &budget);
        if (order_found > 0) {
            if (sort_ranked(caches, ns, order_found != XP_ORDER_UNKNOWN) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                return -1;                                                      /* GCOVR_EXCL_LINE */
            }
            break;
        }
    }
    if (ns->snapshots) {
        return 0;
    }
    Py_ssize_t write_pos = 1;
    for (Py_ssize_t read_pos = 1; read_pos < ns->len; read_pos++) {
        if (ns->items[read_pos].node != ns->items[write_pos - 1].node ||
            ns->items[read_pos].attr != ns->items[write_pos - 1].attr) {
            ns->items[write_pos++] = ns->items[read_pos];
        }
    }
    ns->len = write_pos;
    return 0;
}

void xp_result_free(xp_result *result) {
    if (result->kind == XP_STRING) {
        PyMem_Free(result->string);
        result->string = NULL;
    } else if (result->kind == XP_NODESET) {
        xp_nodeset_free(&result->nodes);
    }
}

void result_bool(xp_result *result, int value) {
    memset(result, 0, sizeof(*result));
    result->kind = XP_BOOLEAN;
    result->boolean = value != 0;
}

void result_number(xp_result *result, double value) {
    memset(result, 0, sizeof(*result));
    result->kind = XP_NUMBER;
    result->number = value;
}

void result_string(xp_result *result, Py_UCS4 *owned, Py_ssize_t len) {
    memset(result, 0, sizeof(*result));
    result->kind = XP_STRING;
    result->string = owned;
    result->string_len = len;
}

Py_UCS4 *ucs4_dup(const Py_UCS4 *src, Py_ssize_t len) {
    Py_UCS4 *buf = PyMem_Malloc((size_t)len * sizeof(Py_UCS4));
    if (buf == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
        return NULL;   /* GCOVR_EXCL_LINE */
    }
    if (len) {
        memcpy(buf, src, (size_t)len * sizeof(Py_UCS4));
    }
    return buf;
}

Py_UCS4 *ucs4_from_ascii(const char *src, Py_ssize_t length, Py_ssize_t *len) {
    *len = length;
    Py_UCS4 *buffer = PyMem_Malloc((size_t)length * sizeof(Py_UCS4));
    if (buffer == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
        return NULL;      /* GCOVR_EXCL_LINE */
    }
    for (Py_ssize_t index = 0; index < length; index++) {
        buffer[index] = (Py_UCS4)(unsigned char)src[index];
    }
    return buffer;
}

Py_UCS4 *item_string(struct th_tree *tree, xp_item item, Py_ssize_t *len) {
    if (item.attr == -2) {
        return ucs4_from_ascii(XP_XML_NS_URI, sizeof(XP_XML_NS_URI) - 1, len);
    }
    if (xp_is_attribute(item.attr)) {
        const th_node_attr *attr_record = xp_item_attribute(tree, item);
        *len = attr_record->value == NULL ? 0 : attr_record->value_len;
        return ucs4_dup(attr_record->value, *len);
    }
    if (item.node->type == TH_NODE_TEXT || item.node->type == TH_NODE_COMMENT) {
        return th_node_data(tree, item.node, len);
    }
    return th_node_text(tree, item.node, len);
}

/* Clinger's fast path: a digit run whose mantissa fits 2^53 with at most 22 digits after the point is
   mantissa / 10^fraction_digits, two exact doubles divided with a single rounding. */
#define XP_EXACT_MANTISSA (UINT64_C(1) << 53)
#define XP_EXACT_FRACTION_DIGITS 22
static const double XP_POWERS_OF_TEN[] = {1e0,  1e1,  1e2,  1e3,  1e4,  1e5,  1e6,  1e7,  1e8,  1e9,  1e10, 1e11,
                                          1e12, 1e13, 1e14, 1e15, 1e16, 1e17, 1e18, 1e19, 1e20, 1e21, 1e22};

/* A decimal run too long for the exact fast path, converted by CPython's correctly
   rounded strtod. The run is ASCII digits and at most one '.', which Python's float
   grammar accepts, so only an allocation failure can make the conversion fail. */
static double decimal_value_slow(const Py_UCS4 *digits, Py_ssize_t len) {
    char *ascii = PyMem_Malloc((size_t)len + 1);
    if (ascii == NULL) {    /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
        return (double)NAN; /* GCOVR_EXCL_LINE */
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        ascii[index] = (char)digits[index];
    }
    ascii[len] = '\0';
    double value = PyOS_string_to_double(ascii, NULL, NULL);
    PyMem_Free(ascii);
    if (value == -1.0 && PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: only an allocation failure */
        PyErr_Clear();                       /* GCOVR_EXCL_LINE */
        return (double)NAN;                  /* GCOVR_EXCL_LINE */
    }
    return value;
}

double xp_decimal_value(const Py_UCS4 *digits, Py_ssize_t len) {
    uint64_t mantissa = 0;
    Py_ssize_t fraction_digits = 0;
    int after_dot = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        if (digits[index] == '.') {
            after_dot = 1;
            continue;
        }
        mantissa = mantissa * 10 + (digits[index] - '0');
        fraction_digits += after_dot;
        if (mantissa > XP_EXACT_MANTISSA || fraction_digits > XP_EXACT_FRACTION_DIGITS) {
            return decimal_value_slow(digits, len);
        }
    }
    return (double)mantissa / XP_POWERS_OF_TEN[fraction_digits];
}

/* XPath number parse: optional leading/trailing whitespace around an optional sign,
   digits, and attr_record fractional part; anything else is NaN. */
double parse_number(const Py_UCS4 *text, Py_ssize_t len) {
    Py_ssize_t index = 0;
    while (index < len && xp_is_space(text[index])) {
        index++;
    }
    Py_ssize_t end = len;
    while (end > index && xp_is_space(text[end - 1])) {
        end--;
    }
    if (index == end) {
        return (double)NAN;
    }
    int sign = 1;
    if (text[index] == '-') {
        sign = -1;
        index++;
    }
    uint64_t mantissa = 0;
    Py_ssize_t fraction_digits = 0;
    int exact = 1;
    int seen_digit = 0;
    int after_dot = 0;
    for (Py_ssize_t cursor = index; cursor < end; cursor++) {
        Py_UCS4 ch = text[cursor];
        if (ch == '.' && !after_dot) {
            after_dot = 1;
        } else if (ch >= '0' && ch <= '9') {
            seen_digit = 1;
            if (exact) {
                mantissa = mantissa * 10 + (ch - '0');
                fraction_digits += after_dot;
                exact = mantissa <= XP_EXACT_MANTISSA && fraction_digits <= XP_EXACT_FRACTION_DIGITS;
            }
        } else {
            return (double)NAN;
        }
    }
    if (!seen_digit) {
        return (double)NAN;
    }
    if (!exact) {
        return sign * decimal_value_slow(text + index, end - index);
    }
    return sign * (fraction_digits == 0 ? (double)mantissa : (double)mantissa / XP_POWERS_OF_TEN[fraction_digits]);
}

/* Render a finite non-integer (or a large integer past the %lld fast path) as the plain
   decimal XPath 1.0 §4.2 requires: the fewest significant digits that round-trip the
   double, never an exponent. PyOS_double_to_string with the 'r' code gives the shortest
   round-tripping digits (the same repr Python's float uses); this expands its mantissa
   and decimal exponent into positional notation. Returns the written length, or -1 with
   a Python exception set on allocation failure. */
static Py_ssize_t decimal_expand(double value, char *out) {
    char *repr = PyOS_double_to_string(value, 'r', 0, 0, NULL);
    if (repr == NULL) { /* GCOVR_EXCL_BR_LINE: the repr allocation cannot be forced to fail */
        return -1;      /* GCOVR_EXCL_LINE */
    }
    char digits[32];
    Py_ssize_t written = 0;
    const char *cursor = repr;
    if (*cursor == '-') {
        out[written++] = '-';
        cursor++;
    }
    Py_ssize_t digit_count = 0;
    Py_ssize_t point = -1; /* index in digits of the decimal point, or the end when absent */
    for (; *cursor != '\0' && *cursor != 'e'; cursor++) { /* the 'r' code always spells the exponent lowercase */
        if (*cursor == '.') {
            point = digit_count;
        } else {
            digits[digit_count++] = *cursor;
        }
    }
    if (point < 0) {
        point = digit_count;
    }
    Py_ssize_t exponent = *cursor == '\0' ? 0 : (Py_ssize_t)strtol(cursor + 1, NULL, 10);
    PyMem_Free(repr);
    Py_ssize_t split = point + exponent; /* digits left of the point after applying the exponent */
    if (split <= 0) {
        out[written++] = '0';
        out[written++] = '.';
        for (Py_ssize_t index = 0; index < -split; index++) {
            out[written++] = '0';
        }
        for (Py_ssize_t index = 0; index < digit_count; index++) {
            out[written++] = digits[index];
        }
    } else if (split >= digit_count) {
        for (Py_ssize_t index = 0; index < digit_count; index++) {
            out[written++] = digits[index];
        }
        for (Py_ssize_t index = 0; index < split - digit_count; index++) {
            out[written++] = '0';
        }
    } else {
        for (Py_ssize_t index = 0; index < split; index++) {
            out[written++] = digits[index];
        }
        out[written++] = '.';
        for (Py_ssize_t index = split; index < digit_count; index++) {
            out[written++] = digits[index];
        }
    }
    return written;
}

static int format_number(double value, Py_UCS4 **out, Py_ssize_t *out_len) {
    char buf[512]; /* the widest finite double expands to ~325 decimal digits */
    Py_ssize_t len;
    if (isnan(value)) { /* GCOVR_EXCL_BR_LINE: dead type-dispatch arm of the isnan macro */
        len = snprintf(buf, sizeof(buf), "NaN");
    } else if (isinf(value)) { /* GCOVR_EXCL_BR_LINE: dead type-dispatch arm of the isinf macro */
        len = snprintf(buf, sizeof(buf), "%s", value < 0 ? "-Infinity" : "Infinity");
    } else if (fabs(value) < 1e15 && value == (double)(long long)value) {
        len = snprintf(buf, sizeof(buf), "%lld", (long long)value);
    } else {
        len = decimal_expand(value, buf);
        if (len < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1; /* GCOVR_EXCL_LINE */
        }
    }
    Py_UCS4 *buffer = PyMem_Malloc((size_t)len * sizeof(Py_UCS4));
    if (buffer == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
        return -1;        /* GCOVR_EXCL_LINE */
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        buffer[index] = (Py_UCS4)(unsigned char)buf[index];
    }
    *out = buffer;
    *out_len = len;
    return 0;
}

int to_boolean(struct th_tree *tree, const xp_result *value) {
    switch (value->kind) {
    case XP_BOOLEAN:
        return value->boolean;
    case XP_NUMBER:
        return value->number != 0 && !isnan(value->number);
    case XP_STRING:
        return value->string_len > 0;
    default: /* XP_NODESET */
        (void)tree;
        return value->nodes.len > 0;
    }
}

/* The string-value of value, freshly allocated; *len receives the length. */
Py_UCS4 *to_string(struct th_tree *tree, const xp_result *value, Py_ssize_t *len) {
    switch (value->kind) {
    case XP_STRING:
        *len = value->string_len;
        return ucs4_dup(value->string, value->string_len);
    case XP_BOOLEAN: {
        const char *literal = value->boolean ? "true" : "false";
        *len = (Py_ssize_t)strlen(literal);
        Py_UCS4 *buffer = PyMem_Malloc((size_t)*len * sizeof(Py_UCS4));
        if (buffer == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
            return NULL;      /* GCOVR_EXCL_LINE */
        }
        for (Py_ssize_t index = 0; index < *len; index++) {
            buffer[index] = (Py_UCS4)(unsigned char)literal[index];
        }
        return buffer;
    }
    case XP_NUMBER: {
        Py_UCS4 *buffer = NULL;
        if (format_number(value->number, &buffer, len) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            return NULL;                                      /* GCOVR_EXCL_LINE */
        }
        return buffer;
    }
    default: /* XP_NODESET: string-value of the first node in document order */
        if (value->nodes.len == 0) {
            *len = 0;
            return ucs4_dup(NULL, 0);
        }
        return item_string(tree, value->nodes.items[0], len);
    }
}

double to_number(struct th_tree *tree, const xp_result *value) {
    if (value->kind == XP_NUMBER) {
        return value->number;
    }
    if (value->kind == XP_BOOLEAN) {
        return value->boolean ? 1.0 : 0.0;
    }
    Py_ssize_t len;
    Py_UCS4 *text = to_string(tree, value, &len);
    if (text == NULL) {     /* GCOVR_EXCL_BR_LINE: alloc */
        return (double)NAN; /* GCOVR_EXCL_LINE */
    }
    double number = parse_number(text, len);
    PyMem_Free(text);
    return number;
}

/* Apply each predicate in the XN_PRED chain from pred_head up to stop (-1 for the whole chain) to set, in place, in
   proximity order. held is the depth the caller's frames weigh beneath the predicate on top of the level its own
   expression counts. */
static int apply_predicates(const xp_program *prog, int32_t pred_head, int32_t stop, xp_ctx *ctx, xp_nodeset *set,
                            int held) {
    for (int32_t pr = pred_head; pr != stop; pr = prog->nodes[pr].next) {
        int32_t expr = prog->nodes[pr].first;
        const xn *predicate = &prog->nodes[expr];
        static const Py_UCS4 last[] = {'l', 'a', 's', 't'};
        if (predicate->kind == XN_NUM || (predicate->kind == XN_FUNC && predicate->first < 0 &&
                                          predicate->str_len == 4 && memcmp(predicate->str, last, sizeof(last)) == 0)) {
            double position = predicate->kind == XN_NUM ? predicate->num : (double)set->len;
            Py_ssize_t selected = position >= 1 && position <= (double)set->len && floor(position) == position
                                      ? (Py_ssize_t)position - 1
                                      : -1;
            xp_item item = selected >= 0 ? set->items[selected] : (xp_item){NULL, -1};
            if (set->snapshots) {
                for (Py_ssize_t index = 0; index < set->len; index++) {
                    if (index != selected) {
                        item_release(set->items[index]);
                    }
                    set->items[index] = (xp_item){NULL, -1};
                }
            }
            set->len = selected >= 0;
            if (set->len) {
                set->items[0] = item;
            }
            continue;
        }
        Py_ssize_t size = set->len;
        Py_ssize_t write_pos = 0;
        for (Py_ssize_t index = 0; index < set->len; index++) {
            xp_ctx pctx = {
                ctx->tree,
                set->items[index].node,
                set->items[index].attr,
                index + 1,
                size,
                ctx->feature,
                ctx->vars,
                ctx->namespaces,
                ctx->extension,
                ctx->extension_ctx,
                ctx->depth + held,
                ctx->caches,
                ctx->live,
                ctx->before_python,
                ctx->name_test,
                ctx->name_test_ctx,
                ctx->strict_no_ns,
                ctx->root,
                ctx->langs,
#ifdef TH_OPERATION_LIMIT
                ctx->operations,
#endif
            };
            /* the strings a predicate builds are freed with its value, so the next node starts from what the evaluation
               had spent before this one */
            Py_ssize_t spent = growth_spent(ctx->caches);
            xp_result value;
            int rc = eval_condition(prog, expr, &pctx, &value);
            if (rc < 0) {
                return rc;
            }
            int keep = value.kind == XP_NUMBER ? (double)(index + 1) == value.number : to_boolean(ctx->tree, &value);
            xp_result_free(&value);
            growth_restore(ctx->caches, spent);
            if (keep) {
                set->items[write_pos] = set->items[index];
                if (set->snapshots && write_pos != index) {
                    set->items[index] = (xp_item){NULL, -1};
                }
                write_pos++;
            } else if (set->snapshots) {
                item_release(set->items[index]);
                set->items[index] = (xp_item){NULL, -1};
            }
        }
        set->len = write_pos;
    }
    return 0;
}

/* Resolve a name-test prefix to the element namespace it constrains. On success fills
   match->want_ns (the enum th_ns an element must carry) and match->ns_unmatchable (set
   when the bound URI names no namespace an HTML tree's elements live in, e.g. a custom
   or the XML URI). Returns 0, or -1 when the prefix has no binding. The XML prefix is
   implicitly bound even without a mapping, matching lxml. */
static int resolve_step_ns(xp_ctx *ctx, const Py_UCS4 *prefix, Py_ssize_t prefix_len, step_match *match) {
    const Py_UCS4 *uri = NULL;
    Py_ssize_t uri_len = 0;
    int found = 0;
    if (ctx->namespaces != NULL) {
        for (Py_ssize_t index = 0; index < ctx->namespaces->len; index++) {
            const xp_namespace *binding = &ctx->namespaces->items[index];
            if (binding->prefix_len == prefix_len &&
                memcmp(binding->prefix, prefix, (size_t)prefix_len * sizeof(Py_UCS4)) == 0) {
                uri = binding->uri;
                uri_len = binding->uri_len;
                found = 1;
                break;
            }
        }
    }
    if (!found) {
        if (ucs4_eq_ascii(prefix, prefix_len, XP_XML_NS_PREFIX, sizeof(XP_XML_NS_PREFIX) - 1)) {
            match->ns_unmatchable = 1; /* no element in an HTML tree is in the xml namespace */
            match->xml_ns = 1;
            match->uri = xp_xml_uri;
            match->uri_len = XP_XML_URI_LEN;
            return 0;
        }
        return -1;
    }
    match->uri = uri;
    match->uri_len = uri_len;
    if (ucs4_eq_ascii(uri, uri_len, XP_XML_NS_URI, sizeof(XP_XML_NS_URI) - 1)) {
        match->ns_unmatchable = 1;
        match->xml_ns = 1;
    } else if (ucs4_eq_ascii(uri, uri_len, XP_SVG_NS_URI, sizeof(XP_SVG_NS_URI) - 1)) {
        match->want_ns = TH_NS_SVG;
    } else if (ucs4_eq_ascii(uri, uri_len, XP_MATHML_NS_URI, sizeof(XP_MATHML_NS_URI) - 1)) {
        match->want_ns = TH_NS_MATHML;
    } else if (uri_len == 0) {
        match->want_ns = TH_NS_HTML;
    } else {
        match->ns_unmatchable = 1;
    }
    return 0;
}

/* The atom of an XML-namespace attribute: both trees store it under its xml:-prefixed name. Out of line, so the name
   buffer stays out of the frame each location path keeps on the stack while its predicates recurse. */
static TH_NOINLINE uint32_t resolve_xml_attr_atom(struct th_tree *tree, const Py_UCS4 *local, Py_ssize_t local_len) {
    Py_UCS4 qualified[128] = {'x', 'm', 'l', ':'};
    uint32_t atom = UINT32_MAX;
    if (local_len < (Py_ssize_t)(sizeof(qualified) / sizeof(qualified[0])) - 4) {
        memcpy(qualified + 4, local, (size_t)local_len * sizeof(Py_UCS4));
        atom = resolve_attr_atom(tree, qualified, local_len + 4);
    }
    return atom;
}

/* Resolve a step's name test once: the local part after any prefix, its interned atoms,
   and the namespace the prefix binds. Returns 0, or -3 (with *feature set) for an
   unbound prefix. */
static int build_step_match(xp_ctx *ctx, const xn *step, step_match *match) {
    memset(match, 0, sizeof(*match));
    match->attr_atom = UINT32_MAX;
    match->local = step->str;
    match->local_len = step->str_len;
    match->qualified = step->str;
    match->qualified_len = step->str_len;
    match->name_test = ctx->name_test;
    match->name_test_ctx = ctx->name_test_ctx;
    match->strict_no_ns = ctx->strict_no_ns;
    if (step->test != NT_NAME) {
        return 0;
    }
    if (step->prefix_len > 0) {
        match->has_ns = 1;
        match->local = step->str + step->prefix_len + 1;
        match->local_len = step->str_len - step->prefix_len - 1;
        if (ctx->name_test == NULL && resolve_step_ns(ctx, step->str, step->prefix_len, match) < 0) {
            *ctx->feature = "undefined namespace prefix";
            return -3;
        }
        if (ctx->name_test != NULL && ctx->name_test(ctx->name_test_ctx, NULL, -1, step->str, step->str_len) < 0) {
            return -1;
        }
    }
    if (ctx->name_test == NULL && th_tree_has_xml_ns(ctx->tree)) {
        match->ns_index = xp_ns_index_for(ctx->tree);
        if (match->ns_index == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;                 /* GCOVR_EXCL_LINE */
        }
        match->want_local_id = ns_intern_ucs4(match->ns_index, match->local, match->local_len);
        if (match->want_local_id == 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;                   /* GCOVR_EXCL_LINE */
        }
        if (!match->has_ns) {
            match->want_ns_id = NS_NONE;
        } else if (match->uri_len == 0) {
            match->want_ns_id = NS_IMPOSSIBLE;
        } else {
            match->want_ns_id = ns_intern_ucs4(match->ns_index, match->uri, match->uri_len);
            if (match->want_ns_id == 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                return -1;                /* GCOVR_EXCL_LINE */
            }
        }
    }
    if (step->axis == AX_ATTRIBUTE && match->xml_ns) {
        match->attr_atom = resolve_xml_attr_atom(ctx->tree, match->local, match->local_len);
        match->foreign_only = !th_tree_is_xml(ctx->tree);
    } else if (step->axis == AX_ATTRIBUTE) {
        /* a prefix that binds any other namespace names no attribute a tree carries */
        if (!match->has_ns) {
            match->attr_atom = resolve_attr_atom(ctx->tree, match->local, match->local_len);
        }
    } else {
        match->atom = resolve_tag_atom(match->local, match->local_len);
    }
    return 0;
}

static int eval_path_step(const xp_program *prog, const xn *step, const step_match *match, xp_ctx *ctx,
                          xp_nodeset *current, xp_nodeset *following);
static int eval_path_step_inner(const xp_program *prog, const xn *step, const step_match *match, xp_ctx *ctx,
                                xp_nodeset *current, xp_nodeset *following, xp_nodeset *accepted, xp_seen *seen);

static int eval_path_inner(const xp_program *prog, int32_t path_idx, xp_ctx *ctx, xp_nodeset *out, xp_nodeset *current,
                           xp_nodeset *following, int exists) {
    const xn *root = &prog->nodes[path_idx];
    xp_item start;
    xp_nodeset initial;
    xp_nodeset *contexts = current;
    if (root->second >= 0) {
        xp_result base;
        int rc = eval_held(prog, root->second, ctx, &base, 0);
        if (rc < 0) {
            return rc;
        }
        if (base.kind != XP_NODESET) {
            xp_result_free(&base);
            *ctx->feature = "a path step on a non-node-set";
            return -4;
        }
        *current = base.nodes; /* take ownership */
    } else {
        start = (xp_item){root->absolute ? tree_root(ctx) : ctx->node, root->absolute ? -1 : ctx->attr};
        if (root->first < 0) {
            if (ns_push(current, start.node, start.attr) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                return -1;                                      /* GCOVR_EXCL_LINE */
            }
        } else {
            initial = (xp_nodeset){.items = &start, .len = 1};
            contexts = &initial;
        }
    }
    for (int32_t si = root->first; si >= 0; si = prog->nodes[si].next) {
        const xn *step = &prog->nodes[si];
        step_match match;
        int resolved = build_step_match(ctx, step, &match);
        if (resolved < 0) {
            xp_nodeset_free(current);
            xp_nodeset_free(following);
            return resolved;
        }
        /* A path that is only asked whether it selects a node needs one node of its last step, unless a predicate
           could reject the node found. Self, parent, attribute and namespace take no walk that could be cut short. */
        match.first = exists && step->next < 0 && step->first < 0 && step->axis != AX_SELF && step->axis != AX_PARENT &&
                      step->axis != AX_ATTRIBUTE && step->axis != AX_NAMESPACE;
        if (following->snapshots) {
            for (Py_ssize_t index = 0; index < following->len; index++) {
                item_release(following->items[index]);
            }
            following->snapshots = 0;
        }
        following->len = 0;
        /* One axis walk from one node, with no predicate to run code between its nodes, cannot repeat a node, and
           apply_step hands a reverse axis back in document order for it. */
        int single_walk = contexts->len == 1 && contexts->items[0].attr == -1 && step->first < 0;
        match.document_order = single_walk;
        match.unsorted = si == root->first && root->second >= 0;
        int stepped = eval_path_step(prog, step, &match, ctx, contexts, following);
        if (stepped < 0) {
            xp_nodeset_free(current);
            xp_nodeset_free(following);
            return stepped;
        }
        xp_nodeset swap = *current;
        *current = *following;
        *following = swap;
        contexts = current;
        if (!single_walk && sort_unique(ctx->caches, current) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            xp_nodeset_free(current);                                /* GCOVR_EXCL_LINE */
            xp_nodeset_free(following);                              /* GCOVR_EXCL_LINE */
            return -1;                                               /* GCOVR_EXCL_LINE */
        }
    }
    xp_nodeset_free(following);
    *out = *current;
    return 0;
}

static int eval_path_step(const xp_program *prog, const xn *step, const step_match *match, xp_ctx *ctx,
                          xp_nodeset *current, xp_nodeset *following) {
    xp_seen seen = {0};
    int rc;
    if (ctx->live == NULL) {
        rc = eval_path_step_inner(prog, step, match, ctx, current, following, NULL, &seen);
    } else {
        xp_nodeset accepted = {0};
        xp_live_frame frame = {.sets = {current}, .append = &accepted};
        xp_live_enter(ctx, &frame);
        rc = eval_path_step_inner(prog, step, match, ctx, current, following, &accepted, &seen);
        xp_live_leave(ctx, &frame);
    }
    PyMem_Free(seen.slots);
    return rc;
}

/* The predicates from first to the end of a step's chain, applied to the nodes [before, following->len) it just
   appended. */
static int filter_slice(const xp_program *prog, int32_t first, xp_ctx *ctx, xp_nodeset *following, Py_ssize_t before,
                        xp_nodeset *accepted) {
    xp_nodeset slice = {following->items == NULL ? NULL : following->items + before, following->len - before, 0,
                        following->snapshots};
    int rc;
    if (accepted != NULL) {
        *accepted = *following;
        accepted->len = before;
        xp_live_frame frame = {.sets = {&slice}};
        xp_live_enter(ctx, &frame);
        rc = apply_predicates(prog, first, -1, ctx, &slice, XP_HELD_DEPTH + XP_PREDICATE_DEPTH);
        xp_live_leave(ctx, &frame);
    } else {
        rc = apply_predicates(prog, first, -1, ctx, &slice, XP_HELD_DEPTH + XP_PREDICATE_DEPTH);
    }
    if (rc < 0) {
        return rc;
    }
    following->len = before + slice.len;
    return 0;
}

/* A step with a positional predicate from several contexts. Along the axes other than child, attribute, self, parent
   and namespace, the contexts' axes are stretches of walks they share: suffixes of a sibling list or of the document,
   prefixes of a sibling list, nested subtrees, root paths, or the document before a context less its root path. A
   predicate reads a position as an offset into its context's stretch, so the step walks once, works out from each
   stretch's length which positions survive, and marks them, instead of walking and testing the stretch once per
   context, which is quadratic on a long list or a deep path. The offsets cover predicates that test the node before
   and after the ones that test the position, and position tests that offset_formula (parser.c) accepts. Any other
   chain is evaluated per context over its stretch, for at most XP_EACH_LIMIT nodes. */
#define XP_EACH_LIMIT 10000000
#define XP_VIEW_NONE (-1) /* the context reaches no node along the axis */
#define XP_VIEW_SELF (-2) /* the context reaches only itself: an attribute's descendant-or-self::node() */

typedef struct {
    Py_ssize_t lo;
    Py_ssize_t hi;
} xp_span;

/* Positions from 1, as sorted runs that neither overlap nor touch. */
typedef struct {
    xp_span *items;
    Py_ssize_t len;
    Py_ssize_t cap;
} xp_spans;

/* Append the run [lo, hi], which starts no earlier than the last one, merging the two when they meet. */
static int spans_add(xp_spans *spans, Py_ssize_t lo, Py_ssize_t hi) {
    if (lo > hi) {
        return 0;
    }
    if (spans->len > 0 && spans->items[spans->len - 1].hi >= lo - 1) {
        if (spans->items[spans->len - 1].hi < hi) {
            spans->items[spans->len - 1].hi = hi;
        }
        return 0;
    }
    if (spans->len == spans->cap) {
        Py_ssize_t cap = spans->cap == 0 ? 4 : spans->cap * 2;
        xp_span *grown = PyMem_Realloc(spans->items, (size_t)cap * sizeof(xp_span));
        if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;       /* GCOVR_EXCL_LINE */
        }
        spans->items = grown;
        spans->cap = cap;
    }
    spans->items[spans->len++] = (xp_span){lo, hi};
    return 0;
}

static Py_ssize_t spans_count(const xp_spans *spans) {
    Py_ssize_t count = 0;
    for (Py_ssize_t index = 0; index < spans->len; index++) {
        count += spans->items[index].hi - spans->items[index].lo + 1;
    }
    return count;
}

/* out gets the positions in both left and right, or with both unset in either. */
static int spans_merge(const xp_spans *left, const xp_spans *right, int both, xp_spans *out) {
    Py_ssize_t li = 0;
    Py_ssize_t ri = 0;
    while (both ? li < left->len && ri < right->len : li < left->len || ri < right->len) {
        xp_span span;
        if (both) {
            xp_span one = left->items[li];
            xp_span other = right->items[ri];
            span = (xp_span){one.lo > other.lo ? one.lo : other.lo, one.hi < other.hi ? one.hi : other.hi};
            if (one.hi < other.hi) {
                li++;
            } else {
                ri++;
            }
        } else {
            int from_left = ri == right->len || (li < left->len && left->items[li].lo <= right->items[ri].lo);
            span = from_left ? left->items[li++] : right->items[ri++];
        }
        if (spans_add(out, span.lo, span.hi) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;                              /* GCOVR_EXCL_LINE */
        }
    }
    return 0;
}

static int spans_invert(const xp_spans *spans, Py_ssize_t size, xp_spans *out) {
    Py_ssize_t next = 1;
    for (Py_ssize_t index = 0; index < spans->len; index++) {
        if (spans_add(out, next, spans->items[index].lo - 1) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;                                              /* GCOVR_EXCL_LINE */
        }
        next = spans->items[index].hi + 1;
    }
    return spans_add(out, next, size);
}

/* out gets the members of outer at the positions inner counts among them. */
static int spans_within(const xp_spans *outer, const xp_spans *inner, xp_spans *out) {
    Py_ssize_t at = 0;
    Py_ssize_t before = 0; /* members of outer's runs ahead of at */
    for (Py_ssize_t index = 0; index < inner->len; index++) {
        Py_ssize_t lo = inner->items[index].lo;
        Py_ssize_t hi = inner->items[index].hi;
        while (lo <= hi) { /* inner counts no further than outer holds, so a run is left while lo is */
            xp_span span = outer->items[at];
            Py_ssize_t past = before + span.hi - span.lo + 1;
            if (lo <= past) {
                Py_ssize_t end = hi < past ? hi : past;
                Py_ssize_t shift = span.lo - before - 1;
                if (spans_add(out, lo + shift, end + shift) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                    return -1;                                     /* GCOVR_EXCL_LINE */
                }
                lo = end + 1;
            }
            if (lo > past) {
                before = past;
                at++;
            }
        }
    }
    return 0;
}

/* One operation between position() and the comparison it feeds. */
typedef struct {
    uint8_t kind;  /* XN_ADD, XN_SUB, XN_MUL, XN_DIV or XN_NEG */
    uint8_t right; /* position() is in the right operand */
    double operand;
} xp_hop;

/* The value the chain gives at position, computed as the evaluator does; hops[0] is the outermost operation. */
static double hops_apply(const xp_hop *hops, int count, Py_ssize_t position) {
    double value = (double)position;
    for (int index = count - 1; index >= 0; index--) {
        double operand = hops[index].operand;
        switch (hops[index].kind) {
        case XN_ADD:
            value = value + operand;
            break;
        case XN_SUB:
            value = hops[index].right ? operand - value : value - operand;
            break;
        case XN_MUL:
            value = value * operand;
            break;
        case XN_DIV:
            value = value / operand;
            break;
        default: /* XN_NEG */
            value = -value;
            break;
        }
    }
    return value;
}

static int number_passes(double value, int op, double bound) {
    switch (op) {
    case XN_LT:
        return value < bound;
    case XN_LE:
        return value <= bound;
    case XN_GT:
        return value > bound;
    default: /* XN_GE */
        return value >= bound;
    }
}

/* The first position in [1, size] from which the chain's value passes op against bound, or size + 1. */
static Py_ssize_t first_passing(const xp_hop *hops, int count, Py_ssize_t size, int op, double bound) {
    Py_ssize_t low = 1;
    Py_ssize_t high = size + 1;
    while (low < high) {
        TH_WORK_STEP();
        Py_ssize_t middle = low + (high - low) / 2;
        if (number_passes(hops_apply(hops, count, middle), op, bound)) {
            high = middle;
        } else {
            low = middle + 1;
        }
    }
    return low;
}

/* The positions whose chain value compares to bound under op. The value only rises or only falls with the position,
   so the positions below, equal to and above bound are three runs, which two searches find. */
static int compare_spans(const xp_hop *hops, int count, Py_ssize_t size, int op, double bound, xp_spans *out) {
    int nan_bound = isnan(bound);
    if (nan_bound) {
#ifdef XP_EQ_VIA_PYTHON
        return 0;
#else
        return op == XN_NE ? spans_add(out, 1, size) : 0;
#endif
    }
    int rising = hops_apply(hops, count, 1) <= hops_apply(hops, count, size);
    Py_ssize_t below_end = first_passing(hops, count, size, rising ? XN_GE : XN_LT, bound);
    Py_ssize_t above_end = first_passing(hops, count, size, rising ? XN_GT : XN_LE, bound);
#ifndef XP_EQ_VIA_PYTHON
    Py_ssize_t equal_lo = rising ? below_end : above_end;
    Py_ssize_t equal_hi = (rising ? above_end : below_end) - 1;
#endif
    switch (op) {
#ifndef XP_EQ_VIA_PYTHON
    case XN_EQ:
        return spans_add(out, equal_lo, equal_hi);
    case XN_NE:
        if (spans_add(out, 1, equal_lo - 1) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;                             /* GCOVR_EXCL_LINE */
        }
        return spans_add(out, equal_hi + 1, size);
#endif
    case XN_LT:
        return rising ? spans_add(out, 1, below_end - 1) : spans_add(out, below_end, size);
    case XN_LE:
        return rising ? spans_add(out, 1, above_end - 1) : spans_add(out, above_end, size);
    case XN_GT:
        return rising ? spans_add(out, above_end, size) : spans_add(out, 1, above_end - 1);
    default: /* XN_GE */
        return rising ? spans_add(out, below_end, size) : spans_add(out, 1, below_end - 1);
    }
}

/* A part of a position test that reads neither the node nor the position. An error answers 1 instead, which sends the
   step to the per-context evaluation: it raises the error only where evaluating every node would have. */
static int fixed_value(const xp_program *prog, int32_t index, xp_ctx *pctx, int condition, xp_result *value) {
    int rc = condition ? eval_condition(prog, index, pctx, value) : eval_expr(prog, index, pctx, value);
    if (rc < 0) {
        PyErr_Clear();
        return 1;
    }
    return 0;
}

/* Collect the operations of a position chain (position_chain in parser.c) from its top down to position(). A chain
   whose operand is not finite, or whose factor or divisor is zero, can turn back or yield NaN: that answers 1. */
static int collect_hops(const xp_program *prog, int32_t index, xp_ctx *pctx, xp_hop *hops, int *count) {
    *count = 0;
    for (const xn *node = &prog->nodes[index]; node->kind != XN_FUNC;) {
        if (node->kind == XN_NEG) {
            hops[(*count)++] = (xp_hop){XN_NEG, 0, 0.0};
            node = &prog->nodes[node->first];
            continue;
        }
        int right = !prog->nodes[node->first].positional;
        xp_result value;
        int rc = fixed_value(prog, right ? node->first : node->second, pctx, 0, &value);
        if (rc != 0) {
            return rc;
        }
        double operand = to_number(pctx->tree, &value);
        xp_result_free(&value);
        int finite = isfinite(operand);
        if (!finite || ((node->kind == XN_MUL || node->kind == XN_DIV) && operand == 0)) {
            return 1;
        }
        hops[(*count)++] = (xp_hop){(uint8_t)node->kind, (uint8_t)right, operand};
        node = &prog->nodes[right ? node->second : node->first];
    }
    return 0;
}

/* The positions among pctx->size nodes that a position test (offset_formula in parser.c) keeps: 0, 1 when it needs
   the per-context evaluation after all, or a negative error. top is set for the predicate's own expression, where a
   number selects the position equal to it. */
static int formula_spans(const xp_program *prog, int32_t index, xp_ctx *pctx, int top, xp_spans *out) {
    const xn *node = &prog->nodes[index];
    Py_ssize_t size = pctx->size;
    if (!node->positional) {
        xp_result value;
        int rc = fixed_value(prog, index, pctx, 1, &value);
        if (rc != 0) {
            return rc;
        }
        int numeric = top && value.kind == XP_NUMBER;
        double position = numeric ? value.number : 0;
        int keep = !numeric && to_boolean(pctx->tree, &value);
        xp_result_free(&value);
        if (numeric) {
            return position >= 1 && position <= (double)size && floor(position) == position
                       ? spans_add(out, (Py_ssize_t)position, (Py_ssize_t)position)
                       : 0;
        }
        return keep ? spans_add(out, 1, size) : 0;
    }
    if (node->kind == XN_AND || node->kind == XN_OR) {
        xp_spans left = {0};
        xp_spans right = {0};
        int rc = formula_spans(prog, node->first, pctx, 0, &left);
        if (rc == 0) {
            rc = formula_spans(prog, node->second, pctx, 0, &right);
        }
        if (rc == 0) {
            rc = spans_merge(&left, &right, node->kind == XN_AND, out);
        }
        PyMem_Free(left.items);
        PyMem_Free(right.items);
        return rc;
    }
    if (node->kind == XN_FUNC) {
        if (node->str[0] != 'n') { /* boolean() rather than not() */
            return formula_spans(prog, node->first, pctx, 0, out);
        }
        xp_spans inner = {0};
        int rc = formula_spans(prog, node->first, pctx, 0, &inner);
        if (rc == 0) {
            rc = spans_invert(&inner, size, out);
        }
        PyMem_Free(inner.items);
        return rc;
    }
    int left = prog->nodes[node->first].positional;
    xp_result value;
    int rc = fixed_value(prog, left ? node->second : node->first, pctx, 0, &value);
    if (rc != 0) {
        return rc;
    }
    /* a node-set compares member by member, and a boolean makes = and != compare truth values */
#ifdef XP_EQ_VIA_PYTHON
    int scalar = value.kind == XP_NUMBER || value.kind == XP_STRING || value.kind == XP_BOOLEAN;
#else
    int scalar = value.kind == XP_NUMBER || value.kind == XP_STRING ||
                 (value.kind == XP_BOOLEAN && node->kind != XN_EQ && node->kind != XN_NE);
#endif
    double bound = scalar ? to_number(pctx->tree, &value) : 0;
    xp_result_free(&value);
    if (!scalar) {
        return 1;
    }
    xp_hop hops[XP_OFFSET_DEPTH + 1];
    int count;
    rc = collect_hops(prog, left ? node->first : node->second, pctx, hops, &count);
    if (rc != 0) {
        return rc;
    }
    int op = node->kind;
    if (!left) { /* bound op chain is chain op' bound */
#ifdef XP_EQ_VIA_PYTHON
        op = op == XN_LT ? XN_GT : op == XN_LE ? XN_GE : op == XN_GT ? XN_LT : XN_LE;
#else
        op = op == XN_LT ? XN_GT : op == XN_LE ? XN_GE : op == XN_GT ? XN_LT : op == XN_GE ? XN_LE : op;
#endif
    }
    return compare_spans(hops, count, size, op, bound, out);
}

typedef struct {
    const xp_program *prog;
    const xn *step;
    const step_match *match;
    xp_ctx *ctx;
    xp_nodeset *following;
    xp_nodeset *accepted;
    xp_seen *seen;
    int32_t tests;    /* the first predicate that reads the position */
    int32_t trailing; /* the first predicate after those, or -1 */
    int each;         /* evaluate every predicate per context, over the context's stretch */
    size_t spent;     /* nodes handed to that evaluation */
} xp_overlap;

static int overlap_charge(xp_overlap *state, Py_ssize_t nodes) {
    state->spent += (size_t)nodes;
    if (state->spent <= XP_EACH_LIMIT) {
        return 0;
    }
    *state->ctx->feature = "a positional predicate from several context nodes reached more than 10,000,000 nodes";
    return -3;
}

/* The positions among size nodes that the step's position tests keep, as runs into out. node is any node of the
   stretch: the tests read neither it nor the position outside position(). */
static int view_positions(const xp_overlap *state, struct th_node *node, Py_ssize_t size, xp_spans *out) {
    xp_ctx pctx = *state->ctx;
    pctx.node = node;
    pctx.attr = -1;
    pctx.pos = 1;
    /* the parts of a test run below its operators; charging the deepest any can sit keeps an expression that would
       nest past the limit from evaluating here, and the per-context evaluation then raises as it should */
    pctx.depth += XP_HELD_DEPTH + XP_PREDICATE_DEPTH + 2 * XP_OFFSET_DEPTH;
    Py_ssize_t spent = growth_spent(pctx.caches);
    out->len = 0;
    int rc = spans_add(out, 1, size);
    for (int32_t pr = state->tests; rc == 0 && pr != state->trailing && out->len > 0;
         pr = state->prog->nodes[pr].next) {
        xp_spans kept = {0};
        xp_spans within = {0};
        pctx.size = spans_count(out);
        rc = formula_spans(state->prog, state->prog->nodes[pr].first, &pctx, 1, &kept);
        if (rc == 0) {
            rc = spans_within(out, &kept, &within);
        }
        PyMem_Free(kept.items);
        PyMem_Free(out->items);
        *out = within;
    }
    growth_restore(pctx.caches, spent);
    return rc;
}

/* The per-context evaluation of the stretch the context just appended at before: every predicate, as over
   apply_step's output, then only the nodes no earlier context kept. */
static int each_filter(xp_overlap *state, Py_ssize_t before) {
    xp_nodeset *following = state->following;
    int rc = overlap_charge(state, following->len - before);
#ifdef TH_OPERATION_LIMIT
    if (rc == 0 && xp_charge(state->ctx, 1 + (size_t)(following->len - before)) < 0) {
        rc = -3;
    }
#endif
    if (rc == 0) {
        rc = filter_slice(state->prog, state->step->first, state->ctx, following, before, state->accepted);
    }
    if (rc == 0 && drop_repeats(following, before, state->seen) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        /* GCOVR_EXCL_START: allocation-failure path */
        rc = -1; /* GCOVR_EXCL_LINE */
    }
    /* GCOVR_EXCL_STOP */
    return rc;
}

/* Whether item passes the node test and, unless the evaluation is per context, the predicates ahead of the position
   tests: 1 or 0, or a negative error. */
static int judge_item(const xp_overlap *state, xp_item item) {
    TH_WORK_STEP();
    int matched = item.attr == -1 ? node_test_matches(item.node, state->step, state->match) : 1;
    if (matched <= 0 || state->each || state->tests == state->step->first) {
        return matched;
    }
    xp_nodeset one = {&item, 1, 1, 0};
    int rc = apply_predicates(state->prog, state->step->first, state->tests, state->ctx, &one,
                              XP_HELD_DEPTH + XP_PREDICATE_DEPTH);
    return rc < 0 ? rc : (int)one.len;
}

/* Clear the flag in keep of each node of seq that the predicates ahead of the position tests reject. */
static int filter_flagged(const xp_overlap *state, xp_nodeset *seq, uint8_t *keep) {
    xp_nodeset tested = {0};
    int rc = 0;
    for (Py_ssize_t index = 0; rc == 0 && index < seq->len; index++) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        if (keep[index]) {
            rc = ns_append(&tested, seq->items[index].node, -1);
        }
    }
    if (rc == 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        rc = apply_predicates(state->prog, state->step->first, state->tests, state->ctx, &tested,
                              XP_HELD_DEPTH + XP_PREDICATE_DEPTH);
    }
    Py_ssize_t passed = 0;
    for (Py_ssize_t index = 0; rc == 0 && index < seq->len; index++) {
        if (keep[index]) {
            keep[index] = passed < tested.len && tested.items[passed].node == seq->items[index].node;
            passed += keep[index];
        }
    }
    PyMem_Free(tested.items);
    return rc;
}

/* Entries of a stack, bottom first, with the runs of positions marked on them: a run [lo, hi] counts once in
   starts[lo] and in ends[hi], and the entry popped off the top hands what still covers the one beneath it on (acc). */
typedef struct {
    Py_ssize_t *items;
    Py_ssize_t *acc;
    Py_ssize_t *starts;
    Py_ssize_t *ends;
    Py_ssize_t len;
    Py_ssize_t cap;
} xp_marks;

static int marks_push(xp_marks *marks, Py_ssize_t value) {
    if (marks->len == marks->cap) {
        Py_ssize_t cap = marks->cap == 0 ? 16 : marks->cap * 2;
        Py_ssize_t **arrays[] = {&marks->items, &marks->acc, &marks->starts, &marks->ends};
        for (size_t index = 0; index < sizeof(arrays) / sizeof(arrays[0]); index++) {
            Py_ssize_t *grown = PyMem_Realloc(*arrays[index], (size_t)cap * sizeof(Py_ssize_t));
            if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                return -1;       /* GCOVR_EXCL_LINE */
            }
            *arrays[index] = grown;
        }
        marks->cap = cap;
    }
    marks->items[marks->len] = value;
    marks->acc[marks->len] = marks->starts[marks->len] = marks->ends[marks->len] = 0;
    marks->len++;
    return 0;
}

/* Pop the top entry, answering how many runs cover it. */
static Py_ssize_t marks_pop(xp_marks *marks) {
    Py_ssize_t top = --marks->len;
    Py_ssize_t cover = marks->acc[top] + marks->ends[top];
    if (top > 0) {
        marks->acc[top - 1] += cover - marks->starts[top];
    }
    return cover;
}

/* How many entries hold at most value; the entries ascend. */
static Py_ssize_t marks_upto(const xp_marks *marks, Py_ssize_t value) {
    Py_ssize_t low = 0;
    Py_ssize_t high = marks->len;
    while (low < high) {
        TH_WORK_STEP();
        Py_ssize_t middle = low + (high - low) / 2;
        if (marks->items[middle] <= value) {
            low = middle + 1;
        } else {
            high = middle;
        }
    }
    return low;
}

static void marks_free(xp_marks *marks) {
    PyMem_Free(marks->items);
    PyMem_Free(marks->acc);
    PyMem_Free(marks->starts);
    PyMem_Free(marks->ends);
}

/* Advance a preorder walk from node: into its first child, else to the next sibling of node or of its nearest
   ancestor that has one. climbed counts the nodes whose subtree ended on the way, node first. The walk ends (NULL) at
   the end of the tree, or once bound's subtree ends when bound is set. */
static struct th_node *preorder_advance(struct th_node *node, const struct th_node *bound, Py_ssize_t *climbed) {
    *climbed = 0;
    if (node->first_child != NULL) {
        return node->first_child;
    }
    for (;;) {
        (*climbed)++;
        if (node == bound) {
            return NULL;
        }
        if (node->next_sibling != NULL) {
            return node->next_sibling;
        }
        node = node->parent;
        if (node == NULL) {
            return NULL;
        }
    }
}

/* The stretches of a sibling, following or descendant step: context index's axis is seq[lo, hi), in document order;
   a preceding-sibling step reads it backward. */
typedef struct {
    xp_nodeset seq;
    Py_ssize_t *lo;
    Py_ssize_t *hi;
} xp_ranges;

#define XP_VIEW_LEAD (-3) /* sibling axes: the first context of its list, which walks it */
#define XP_VIEW_WAIT (-4) /* sibling axes: a later context of a list, which the first one's walk meets */

/* One walk of a sibling list from (or, backward, up to) its first context, which meets the list's other contexts in
   document order. */
static int sibling_ranges(xp_overlap *state, const xp_nodeset *contexts, xp_ranges *ranges) {
    int forward = state->step->axis == AX_FOLLOWING_SIBLING;
    Py_ssize_t count = contexts->len;
    xp_nodeset *seq = &ranges->seq;
    Py_ssize_t *next = PyMem_Malloc((size_t)count * sizeof(Py_ssize_t));
    addr_map tails = {0};
    int rc = next == NULL ? -1 : 0;                                 /* GCOVR_EXCL_BR_LINE: allocation failure */
    for (Py_ssize_t index = 0; rc == 0 && index < count; index++) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        xp_item item = contexts->items[index];
        struct th_node *parent = item.node->parent;
        next[index] = -1;
        /* an attribute, namespace or parentless node has no siblings */
        ranges->lo[index] = item.attr != -1 || parent == NULL ? XP_VIEW_NONE : XP_VIEW_LEAD;
        if (ranges->lo[index] == XP_VIEW_LEAD) {
            Py_ssize_t tail = addr_map_find(&tails, parent);
            if (tail != 0) {
                next[tail - 1] = index;
                ranges->lo[index] = XP_VIEW_WAIT;
            }
            rc = addr_map_set(&tails, parent, index + 1);
        }
    }
    for (Py_ssize_t index = 0; rc == 0 && index < count; index++) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        struct th_node *own = contexts->items[index].node;
        Py_ssize_t start = seq->len;
        Py_ssize_t member = next[index];
        if (ranges->lo[index] == XP_VIEW_LEAD) {
            if (forward) {
                ranges->lo[index] = start;
                for (struct th_node *node = own->next_sibling; rc == 0 && node != NULL; /* GCOVR_EXCL_BR_LINE */
                     node = node->next_sibling) {
                    rc = emit_if_match(seq, node, state->step, state->match);
                    if (member >= 0 && contexts->items[member].node == node) {
                        ranges->lo[member] = seq->len;
                        member = next[member];
                    }
                }
            } else {
                member = index;
                /* the walk meets every context of the list, so it ends at the last one */
                for (struct th_node *node = own->parent->first_child; rc == 0 && member >= 0; /* GCOVR_EXCL_BR_LINE */
                     node = node->next_sibling) {
                    if (contexts->items[member].node == node) {
                        ranges->lo[member] = start;
                        ranges->hi[member] = seq->len;
                        member = next[member];
                    }
                    if (member >= 0) {
                        rc = emit_if_match(seq, node, state->step, state->match);
                    }
                }
            }
            for (Py_ssize_t met = index; forward && met != member; met = next[met]) {
                ranges->hi[met] = seq->len;
            }
        }
    }
    PyMem_Free(next);
    PyMem_Free(tails.entries);
    return rc;
}

/* Preorder walks for following and descendant steps. A following walk starts at the first context left and runs to
   the end of its tree; each context's stretch starts where its subtree ends, or, for an attribute or namespace node,
   after its owner element. A descendant walk covers the subtree of the first context left, and each context inside
   it takes its own subtree. The contexts are sorted, so each tree takes one walk. */
static int tree_ranges(xp_overlap *state, const xp_nodeset *contexts, xp_ranges *ranges) {
    const xn *step = state->step;
    int following = step->axis == AX_FOLLOWING;
    int self = step->axis == AX_DESCENDANT_OR_SELF;
    Py_ssize_t only = self && step->test == NT_NODE ? XP_VIEW_SELF : XP_VIEW_NONE; /* an attribute's stretch */
    Py_ssize_t count = contexts->len;
    xp_nodeset *seq = &ranges->seq;
    xp_marks open = {0};
    int rc = 0;
    for (Py_ssize_t index = 0; rc == 0 && index < count;) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        if (!following && contexts->items[index].attr != -1) {
            ranges->lo[index++] = only;
            continue;
        }
        Py_ssize_t first = index;
        struct th_node *root = contexts->items[index].node;
        for (struct th_node *node = root; rc == 0 && node != NULL;) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            TH_WORK_STEP();
            int matched = node_test_matches(node, step, state->match);
            if (index < count && contexts->items[index].node == node && contexts->items[index].attr == -1) {
                ranges->lo[index] = seq->len + (!self && matched > 0);
                rc = marks_push(&open, index++);
            }
            if (matched < 0) { /* GCOVR_EXCL_BR_LINE: the name test resolved its prefix up front; allocation */
                rc = -1;       /* GCOVR_EXCL_LINE */
            } else if (matched > 0 && rc == 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                rc = ns_append(seq, node, -1);
            }
            /* the node's attribute and namespace items */
            while (index < count && contexts->items[index].node == node) {
                ranges->lo[index++] = following ? seq->len : only;
            }
            Py_ssize_t climbed;
            struct th_node *advance = preorder_advance(node, following ? NULL : root, &climbed);
            for (; climbed > 0; climbed--, node = node->parent) {
                if (open.len > 0 && contexts->items[open.items[open.len - 1]].node == node) {
                    Py_ssize_t closed = open.items[--open.len];
                    *(following ? &ranges->lo[closed] : &ranges->hi[closed]) = seq->len;
                }
            }
            node = advance;
        }
        for (Py_ssize_t walked = first; following && walked < index; walked++) {
            ranges->hi[walked] = seq->len;
        }
    }
    marks_free(&open);
    return rc;
}

/* Turn the stretches into the step's result: per context, or by marking the positions each stretch keeps. */
static int range_views(xp_overlap *state, const xp_nodeset *contexts, xp_ranges *ranges, xp_nodeset *found) {
    int backward = state->step->axis == AX_PRECEDING_SIBLING;
    xp_nodeset *seq = &ranges->seq;
    Py_ssize_t count = contexts->len;
    int rc = 0;
    if (state->each) {
        xp_live_frame frame = {.sets = {seq}};
        xp_live_enter(state->ctx, &frame);
        for (Py_ssize_t index = 0; rc == 0 && index < count; index++) {
            Py_ssize_t lo = ranges->lo[index];
            Py_ssize_t before = state->following->len;
            if (lo == XP_VIEW_SELF) {
                rc = ns_push(state->following, contexts->items[index].node, contexts->items[index].attr);
            }
            for (Py_ssize_t at = lo; rc == 0 && at >= 0 && at < ranges->hi[index]; at++) { /* GCOVR_EXCL_BR_LINE */
                Py_ssize_t pick = backward ? ranges->hi[index] - 1 - (at - lo) : at;
                rc = ns_append(state->following, seq->items[pick].node, -1);
            }
            if (rc == 0 && lo != XP_VIEW_NONE) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                rc = each_filter(state, before);
            }
        }
        xp_live_leave(state->ctx, &frame);
        return rc;
    }
    Py_ssize_t *marks = PyMem_Calloc((size_t)seq->len + 1, sizeof(Py_ssize_t));
    rc = marks == NULL ? -1 : 0;                         /* GCOVR_EXCL_BR_LINE: allocation failure */
    if (rc == 0 && state->tests != state->step->first) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        /* the predicates ahead of the position tests run once on each node some stretch holds, and the stretches
           shrink to the nodes they keep */
        uint8_t *keep = PyMem_Malloc((size_t)seq->len + 1);
        rc = keep == NULL ? -1 : 0;                                     /* GCOVR_EXCL_BR_LINE: allocation failure */
        for (Py_ssize_t index = 0; rc == 0 && index < count; index++) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            if (ranges->lo[index] >= 0) {
                marks[ranges->lo[index]]++;
                marks[ranges->hi[index]]--;
            }
        }
        Py_ssize_t open = 0;
        for (Py_ssize_t at = 0; rc == 0 && at < seq->len; at++) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            open += marks[at];
            keep[at] = open > 0;
        }
        if (rc == 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            rc = filter_flagged(state, seq, keep);
        }
        Py_ssize_t write = 0;
        for (Py_ssize_t at = 0; rc == 0 && at <= seq->len; at++) {
            marks[at] = write; /* now where the node at `at` moves to */
            if (at < seq->len && keep[at]) {
                seq->items[write++] = seq->items[at];
            }
        }
        for (Py_ssize_t index = 0; rc == 0 && index < count; index++) {
            if (ranges->lo[index] >= 0) {
                ranges->lo[index] = marks[ranges->lo[index]];
                ranges->hi[index] = marks[ranges->hi[index]];
            }
        }
        if (rc == 0) {
            memset(marks, 0, ((size_t)seq->len + 1) * sizeof(Py_ssize_t));
            seq->len = write;
        }
        PyMem_Free(keep);
    }
    xp_spans spans = {0};
    for (Py_ssize_t index = 0; rc == 0 && index < count; index++) {
        xp_item item = contexts->items[index];
        Py_ssize_t lo = ranges->lo[index];
        Py_ssize_t hi = ranges->hi[index];
        Py_ssize_t size = lo == XP_VIEW_SELF ? judge_item(state, item) : lo >= 0 ? hi - lo : 0;
        if (size < 0) {
            rc = (int)size;
        } else if (size > 0) {
            rc = view_positions(state, lo == XP_VIEW_SELF ? item.node : seq->items[lo].node, size, &spans);
        }
        for (Py_ssize_t run = 0; rc == 0 && size > 0 && run < spans.len; run++) {
            xp_span span = spans.items[run];
            if (lo == XP_VIEW_SELF) {
                rc = ns_push(found, item.node, item.attr);
            } else if (backward) {
                marks[hi - span.hi]++;
                marks[hi - span.lo + 1]--;
            } else {
                marks[lo + span.lo - 1]++;
                marks[lo + span.hi]--;
            }
        }
    }
    Py_ssize_t open = 0;
    for (Py_ssize_t at = 0; rc == 0 && at < seq->len; at++) {
        open += marks[at];
        if (open > 0) {
            rc = ns_append(found, seq->items[at].node, -1);
        }
    }
    PyMem_Free(spans.items);
    PyMem_Free(marks);
    return rc;
}

/* An ancestor step. The contexts come in document order, so the path from the root to each one shares its top with
   the path to the one before: a climb stops at the first node on that path, which then drops what lies below, and a
   node it drops is below no later context. Each path node is tested once, when it first falls in a stretch; the
   tested ones that pass (kept) carry the runs the position tests mark, until they leave the path. */
static int chain_views(xp_overlap *state, const xp_nodeset *contexts, xp_nodeset *found) {
    const xn *step = state->step;
    xp_nodeset path = {0};
    xp_nodeset climb = {0};
    addr_map where = {0}; /* node -> its index on path + 1, or 0 once off it */
    xp_marks kept = {0};  /* path indices */
    xp_spans spans = {0};
    Py_ssize_t judged = 0; /* path[0, judged) are tested */
    xp_live_frame frame = {.sets = {&path}};
    int rc = 0;
    if (state->each) {
        xp_live_enter(state->ctx, &frame);
    }
    for (Py_ssize_t index = 0; rc == 0 && index < contexts->len; index++) {
        xp_item item = contexts->items[index];
        Py_ssize_t stay = 0;
        climb.len = 0;
        for (struct th_node *node = item.node; rc == 0 && node != NULL; /* GCOVR_EXCL_BR_LINE: allocation failure */
             node = node->parent) {
            TH_WORK_STEP();
            stay = addr_map_find(&where, node);
            if (stay > 0) {
                break;
            }
            rc = ns_append(&climb, node, -1);
        }
        while (rc == 0 && path.len > stay) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            path.len--;
            where.entries[addr_slot(&where, path.items[path.len].node)].value = 0;
            /* only the offsets mark runs, so a per-context evaluation pops none that cover */
            if (kept.len > 0 && kept.items[kept.len - 1] == path.len && marks_pop(&kept) > 0) {
                rc = ns_append(found, path.items[path.len].node, -1);
            }
        }
        judged = judged < path.len ? judged : path.len;
        for (Py_ssize_t up = climb.len - 1; rc == 0 && up >= 0; up--) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            rc = ns_append(&path, climb.items[up].node, -1);
            if (rc == 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                rc = addr_map_set(&where, climb.items[up].node, path.len);
            }
        }
        /* a Python call pins a frame's sets once per activation, so the changed path takes a fresh one */
        xp_live_leave(state->ctx, &frame);
        xp_live_enter(state->ctx, &frame);
        /* an ancestor step leaves the context node itself out of its stretch */
        Py_ssize_t limit = step->axis == AX_ANCESTOR && item.attr == -1 ? path.len - 1 : path.len;
        for (; rc == 0 && judged < limit; judged++) {
            rc = judge_item(state, path.items[judged]);
            if (rc > 0) {
                rc = marks_push(&kept, judged);
            }
        }
        /* an attribute or namespace node is its own nearest ancestor-or-self, ahead of its owner element */
        int own = step->axis == AX_ANCESTOR_OR_SELF && item.attr != -1 && step->test == NT_NODE;
        if (rc == 0 && state->each) {
            Py_ssize_t before = state->following->len;
            if (own) {
                rc = ns_push(state->following, item.node, item.attr);
            }
            for (Py_ssize_t at = kept.len - 1; rc == 0 && at >= 0; at--) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                rc = ns_append(state->following, path.items[kept.items[at]].node, -1);
            }
            if (rc == 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                rc = each_filter(state, before);
            }
            continue;
        }
        if (rc == 0 && own) {
            own = judge_item(state, item);
            rc = own < 0 ? own : 0;
        }
        Py_ssize_t size = own + kept.len;
        if (rc == 0 && size > 0) {
            rc = view_positions(state, item.node, size, &spans);
        }
        for (Py_ssize_t run = 0; rc == 0 && size > 0 && run < spans.len; run++) {
            Py_ssize_t lo = spans.items[run].lo;
            Py_ssize_t hi = spans.items[run].hi;
            if (own && lo == 1) {
                rc = ns_push(found, item.node, item.attr);
                lo++;
            }
            if (lo <= hi) { /* proximity counts from the context up, the stack from the root down */
                kept.starts[kept.len - (hi - own)]++;
                kept.ends[kept.len - (lo - own)]++;
            }
        }
    }
    while (rc == 0 && kept.len > 0) {
        Py_ssize_t top = kept.items[kept.len - 1];
        if (marks_pop(&kept) > 0) {
            rc = ns_append(found, path.items[top].node, -1);
        }
    }
    if (state->each) {
        xp_live_leave(state->ctx, &frame);
    }
    PyMem_Free(spans.items);
    marks_free(&kept);
    PyMem_Free(where.entries);
    PyMem_Free(climb.items);
    PyMem_Free(path.items);
    return rc;
}

/* The smallest index d below at whose node is the k-th of the stretch, counting from the document's start: kept
   counts the nodes ahead of each index, less those on the context's own root path. */
static Py_ssize_t nth_preceding(const Py_ssize_t *ahead, const xp_marks *path, Py_ssize_t at, Py_ssize_t k) {
    Py_ssize_t low = 0;
    Py_ssize_t high = at - 1;
    while (low < high) {
        TH_WORK_STEP();
        Py_ssize_t middle = low + (high - low) / 2;
        if (ahead[middle + 1] - marks_upto(path, middle) >= k) {
            high = middle;
        } else {
            low = middle + 1;
        }
    }
    return low;
}

/* A preceding step. One preorder walk from the root to the last context records the nodes passing the node test
   (seq) and where each context comes; a node falls in some stretch exactly when a context comes after its subtree
   ended. A second walk then keeps the root path of each context: its stretch is everything before it that is kept,
   less the kept nodes on that path, so a run of positions is a range of seq less a range of the path. */
static int preceding_views(xp_overlap *state, const xp_nodeset *contexts, xp_nodeset *found) {
    const xn *step = state->step;
    Py_ssize_t count = contexts->len;
    Py_ssize_t *at = PyMem_Malloc((size_t)count * sizeof(Py_ssize_t)); /* each context's place in seq */
    xp_nodeset seq = {0};
    xp_marks ended = {0}; /* per seq index: how many contexts came before its subtree ended */
    xp_marks open = {0};  /* the open nodes of a walk: their seq index, or -1 */
    xp_marks path = {0};  /* the kept nodes on the current root path, by seq index */
    xp_spans spans = {0};
    uint8_t *keep = NULL;
    Py_ssize_t *ahead = NULL;
    Py_ssize_t *marks = NULL;
    xp_live_frame frame = {.sets = {&seq}};
    int rc = at == NULL ? -1 : 0;                           /* GCOVR_EXCL_BR_LINE: allocation failure */
    for (Py_ssize_t index = 0; rc == 0 && index < count;) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        Py_ssize_t first = index;
        struct th_node *root = contexts->items[index].node;
        while (root->parent != NULL) {
            TH_WORK_STEP();
            root = root->parent;
        }
        seq.len = ended.len = open.len = 0;
        for (struct th_node *node = root; rc == 0 && node != NULL;) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            while (index < count && contexts->items[index].node == node) {
                at[index++] = seq.len;
            }
            if (index == count) {
                break;
            }
            TH_WORK_STEP();
            int matched = node_test_matches(node, step, state->match);
            /* the name test resolved its prefix up front, so only allocation fails here */
            rc = matched < 0 ? -1 : marks_push(&open, matched > 0 ? seq.len : -1); /* GCOVR_EXCL_BR_LINE */
            if (rc == 0 && matched > 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                rc = ns_append(&seq, node, -1);
                if (rc == 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                    rc = marks_push(&ended, PY_SSIZE_T_MAX);
                }
            }
            Py_ssize_t climbed;
            struct th_node *advance = preorder_advance(node, NULL, &climbed);
            for (; rc == 0 && climbed > 0; climbed--) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                Py_ssize_t closed = open.items[--open.len];
                if (closed >= 0) {
                    ended.items[closed] = index - first;
                }
            }
            node = advance;
        }
        PyMem_Free(keep);
        PyMem_Free(ahead);
        PyMem_Free(marks);
        keep = PyMem_Malloc((size_t)seq.len + 1);
        ahead = PyMem_Malloc(((size_t)seq.len + 1) * sizeof(Py_ssize_t));
        marks = PyMem_Calloc((size_t)seq.len + 2, sizeof(Py_ssize_t));
        if (rc == 0 && (keep == NULL || ahead == NULL || marks == NULL)) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            /* GCOVR_EXCL_START: allocation-failure path */
            rc = -1; /* GCOVR_EXCL_LINE */
        }
        /* GCOVR_EXCL_STOP */
        for (Py_ssize_t d = 0; rc == 0 && d < seq.len; d++) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            keep[d] = ended.items[d] < index - first;
            ended.items[d] = 0; /* from here on: the runs that cover the node but not its context's stretch */
        }
        if (rc == 0 && !state->each && state->tests != step->first) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            rc = filter_flagged(state, &seq, keep);
        }
        for (Py_ssize_t d = 0; rc == 0 && d <= seq.len; d++) {
            ahead[d] = d == 0 ? 0 : ahead[d - 1] + keep[d - 1];
        }
        if (state->each) {
            xp_live_enter(state->ctx, &frame);
        }
        Py_ssize_t next = 0; /* the seq index the second walk reaches next */
        open.len = path.len = 0;
        /* the walk meets every context of the tree, so it ends at the last one */
        for (struct th_node *node = root; rc == 0;) {
            for (; rc == 0 && first < index && contexts->items[first].node == node; first++) {
                Py_ssize_t context = first;
                xp_item item = contexts->items[context];
                Py_ssize_t size = ahead[at[context]] - path.len;
                if (state->each) {
                    Py_ssize_t before = state->following->len;
                    Py_ssize_t skip = path.len - 1;
                    for (Py_ssize_t d = at[context] - 1; rc == 0 && d >= 0; d--) { /* GCOVR_EXCL_BR_LINE: allocation */
                        if (skip >= 0 && path.items[skip] == d) {
                            skip--;
                        } else if (keep[d]) {
                            rc = ns_append(state->following, seq.items[d].node, -1);
                        }
                    }
                    if (rc == 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                        rc = each_filter(state, before);
                    }
                    continue;
                }
                if (size > 0) {
                    rc = view_positions(state, item.node, size, &spans);
                }
                for (Py_ssize_t run = 0; rc == 0 && size > 0 && run < spans.len; run++) {
                    /* proximity counts back from the context, seq forward from the root */
                    Py_ssize_t lo = nth_preceding(ahead, &path, at[context], size - spans.items[run].hi + 1);
                    Py_ssize_t hi = nth_preceding(ahead, &path, at[context], size - spans.items[run].lo + 1);
                    marks[lo]++;
                    marks[hi + 1]--;
                    Py_ssize_t below = marks_upto(&path, lo - 1);
                    Py_ssize_t above = marks_upto(&path, hi) - 1;
                    if (below <= above) { /* the context's own ancestors in that range are not in its stretch */
                        path.starts[below]++;
                        path.ends[above]++;
                    }
                }
            }
            if (first == index || rc != 0) { /* a failed predicate ends the walk; the marks below would overwrite rc */
                break;
            }
            Py_ssize_t here = next < seq.len && seq.items[next].node == node ? next++ : -1;
            rc = marks_push(&open, here);
            if (rc == 0 && here >= 0 && keep[here]) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                rc = marks_push(&path, here);
            }
            Py_ssize_t climbed;
            struct th_node *advance = preorder_advance(node, NULL, &climbed);
            for (; rc == 0 && climbed > 0; climbed--) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                Py_ssize_t closed = open.items[--open.len];
                if (closed >= 0 && keep[closed]) {
                    ended.items[closed] = marks_pop(&path);
                }
            }
            node = advance;
        }
        if (state->each) {
            xp_live_leave(state->ctx, &frame);
        }
        Py_ssize_t cover = 0;
        for (Py_ssize_t d = 0; rc == 0 && !state->each && d < seq.len; d++) {
            cover += marks[d];
            if (keep[d] && cover - ended.items[d] > 0) {
                rc = ns_append(found, seq.items[d].node, -1);
            }
        }
    }
    PyMem_Free(keep);
    PyMem_Free(ahead);
    PyMem_Free(marks);
    PyMem_Free(spans.items);
    marks_free(&path);
    marks_free(&open);
    marks_free(&ended);
    PyMem_Free(seq.items);
    PyMem_Free(at);
    return rc;
}

static int overlap_views(xp_overlap *state, const xp_nodeset *contexts, xp_nodeset *found) {
    switch (state->step->axis) {
    case AX_ANCESTOR:
    case AX_ANCESTOR_OR_SELF:
        return chain_views(state, contexts, found);
    case AX_PRECEDING:
        return preceding_views(state, contexts, found);
    default: {
        xp_ranges ranges = {{0},
                            PyMem_Calloc((size_t)contexts->len, sizeof(Py_ssize_t)),
                            PyMem_Calloc((size_t)contexts->len, sizeof(Py_ssize_t))};
        int rc = ranges.lo == NULL || ranges.hi == NULL ? -1 : 0; /* GCOVR_EXCL_BR_LINE: allocation failure */
        if (rc == 0) {                                            /* GCOVR_EXCL_BR_LINE: allocation failure */
            rc = state->step->axis == AX_FOLLOWING_SIBLING || state->step->axis == AX_PRECEDING_SIBLING
                     ? sibling_ranges(state, contexts, &ranges)
                     : tree_ranges(state, contexts, &ranges);
        }
        if (rc == 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            rc = range_views(state, contexts, &ranges, found);
        }
        PyMem_Free(ranges.seq.items);
        PyMem_Free(ranges.lo);
        PyMem_Free(ranges.hi);
        return rc;
    }
    }
}

static TH_NOINLINE int eval_positional_overlap(const xp_program *prog, const xn *step, const step_match *match,
                                               xp_ctx *ctx, xp_nodeset *current, xp_nodeset *following,
                                               xp_nodeset *accepted, xp_seen *seen) {
    /* the walks follow the contexts in document order, which only a path's first step can lack */
    if (match->unsorted && sort_unique(ctx->caches, current) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                                                  /* GCOVR_EXCL_LINE */
    }
    xp_overlap state = {.prog = prog,
                        .step = step,
                        .match = match,
                        .ctx = ctx,
                        .following = following,
                        .accepted = accepted,
                        .seen = seen,
                        .tests = step->first,
                        .trailing = -1,
                        .each = !step->offsets || ctx->live != NULL};
    while (!prog->nodes[state.tests].positional) {
        state.tests = prog->nodes[state.tests].next;
    }
    for (state.trailing = state.tests; state.trailing >= 0 && prog->nodes[state.trailing].positional;) {
        state.trailing = prog->nodes[state.trailing].next;
    }
    xp_nodeset found = {0};
    int rc = overlap_views(&state, current, &found);
    if (rc == 1) {
        xp_nodeset_free(&found);
        found = (xp_nodeset){0};
        state.each = 1;
        state.spent = 0;
        rc = overlap_views(&state, current, &found);
    }
    if (rc == 0 && !state.each) {
        xp_nodeset swap = *following;
        *following = found;
        found = swap;
        if (state.trailing >= 0) {
            rc = filter_slice(prog, state.trailing, ctx, following, 0, accepted);
        }
    }
    xp_nodeset_free(&found);
#ifdef TH_OPERATION_LIMIT
    if (rc == 0 && xp_charge(ctx, (size_t)(current->len + following->len)) < 0) {
        rc = -3;
    }
#endif
    return rc;
}

static int eval_path_step_inner(const xp_program *prog, const xn *step, const step_match *match, xp_ctx *ctx,
                                xp_nodeset *current, xp_nodeset *following, xp_nodeset *accepted, xp_seen *seen) {
    /* Along the remaining axes several contexts reach the same nodes, and appending those once per context is
       quadratic in time and memory. The contexts share their walks: with no positional predicate each predicate then
       runs once per node, and a positional one reads offsets into the shared walks (eval_positional_overlap). */
    int overlap = current->len > 1 && step->axis != AX_CHILD && step->axis != AX_ATTRIBUTE && step->axis != AX_SELF &&
                  step->axis != AX_PARENT && step->axis != AX_NAMESPACE;
    if (overlap && step->positional) {
        return eval_positional_overlap(prog, step, match, ctx, current, following, accepted, seen);
    }
    /* Mark covered contexts during the subtree walk: checking ancestry per context is quadratic on deep trees. */
    int skip_nested = overlap && (step->axis == AX_DESCENDANT || step->axis == AX_DESCENDANT_OR_SELF);
    Py_ssize_t covered = 0;
    if (match->first && !skip_nested) { /* nested contexts keep the one walk that covers them all */
        return eval_first_step(step, match, ctx, current, following);
    }
    if (overlap && (step->axis == AX_FOLLOWING || step->axis == AX_PRECEDING)) {
        if (apply_document_step(following, step, match, current, seen) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;                                                        /* GCOVR_EXCL_LINE */
        }
        if (step->first >= 0) {
            int rc = filter_slice(prog, step->first, ctx, following, 0, accepted);
            if (rc < 0) {
                return rc;
            }
        }
#ifdef TH_OPERATION_LIMIT
        if (xp_charge(ctx, (size_t)(current->len + following->len)) < 0) {
            return -3;
        }
#endif
        return 0;
    }
    for (Py_ssize_t index = 0; index < current->len; index++) {
        Py_ssize_t before = following->len;
        xp_item item = current->items[index];
        int stepped;
        if (skip_nested && item.attr == -1) {
            if (index < covered) {
                continue;
            }
            covered = index + 1;
            stepped = apply_descendant_step(following, item.node, step, match, current, &covered);
        } else if (overlap && !skip_nested) {
            stepped = apply_linked_step(following, item, step, match, seen);
        } else {
            stepped = item.attr == -1 ? apply_step(following, item.node, step->axis, step, match, ctx)
                                      : apply_owned_step(following, item, step, match);
        }
        if (stepped < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;     /* GCOVR_EXCL_LINE */
        }
#ifdef TH_OPERATION_LIMIT
        if (xp_charge(ctx, 1 + (size_t)(following->len - before)) < 0) {
            return -3;
        }
#endif
        if (step->first >= 0) {
            int rc = filter_slice(prog, step->first, ctx, following, before, accepted);
            if (rc < 0) {
                return rc;
            }
        }
    }
    return 0;
}

/* exists is set when the caller only asks whether the path selects a node: out then holds at least one of the nodes
   the path selects when there are any, and not necessarily all of them. */
static int eval_path(const xp_program *prog, int32_t path_idx, xp_ctx *ctx, xp_nodeset *out, int exists) {
    xp_nodeset current = {0};
    xp_nodeset following = {0};
    return eval_path_inner(prog, path_idx, ctx, out, &current, &following, exists);
}

/* Existential comparison of two scalar values (neither a node-set). */
static int cmp_scalar(struct th_tree *tree, int op, const xp_result *left, const xp_result *right) {
    if (op == XN_EQ || op == XN_NE) {
        int eq;
        if (left->kind == XP_BOOLEAN || right->kind == XP_BOOLEAN) {
            eq = to_boolean(tree, left) == to_boolean(tree, right);
        } else if (left->kind == XP_NUMBER || right->kind == XP_NUMBER) {
            eq = to_number(tree, left) == to_number(tree, right);
        } else {
            eq = left->string_len == right->string_len &&
                 (left->string_len == 0 ||
                  memcmp(left->string, right->string, (size_t)left->string_len * sizeof(Py_UCS4)) == 0);
        }
        return op == XN_EQ ? eq : !eq;
    }
    double left_num = to_number(tree, left);
    double right_num = to_number(tree, right);
    switch (op) {
    case XN_LT:
        return left_num < right_num;
    case XN_LE:
        return left_num <= right_num;
    case XN_GT:
        return left_num > right_num;
    default: /* XN_GE */
        return left_num >= right_num;
    }
}

/* A node-set member wrapped as left standalone string value. */
static int item_as_string(struct th_tree *tree, xp_item item, xp_result *out) {
    Py_ssize_t len;
    Py_UCS4 *text = item_string(tree, item, &len);
    if (text == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;      /* GCOVR_EXCL_LINE */
    }
    result_string(out, text, len);
    return 0;
}

static int compare_equal_sets(struct th_tree *tree, const xp_nodeset *left, const xp_nodeset *right, int *result);
static int compare_unequal_sets(struct th_tree *tree, const xp_nodeset *left, const xp_nodeset *right, int *result);
static int compare_ordered_sets(struct th_tree *tree, int op, const xp_nodeset *left, const xp_nodeset *right,
                                int *result);

static int compare(struct th_tree *tree, int op, xp_result *first, xp_result *second, int *result) {
    int a_ns = first->kind == XP_NODESET;
    int b_ns = second->kind == XP_NODESET;
    if (!a_ns && !b_ns) {
        *result = cmp_scalar(tree, op, first, second);
        return 0;
    }
    /* first node-set compared with first boolean uses the node-set's own boolean value */
    if ((a_ns && second->kind == XP_BOOLEAN) || (b_ns && first->kind == XP_BOOLEAN)) {
        *result = cmp_scalar(tree, op, first, second);
        return 0;
    }
    xp_nodeset *left = a_ns ? &first->nodes : NULL;
    xp_nodeset *right = b_ns ? &second->nodes : NULL;
    *result = 0;
    if (a_ns && b_ns) {
        if (op == XN_EQ && left->len >= 16 && right->len >= 16) {
            return compare_equal_sets(tree, left, right, result);
        }
        if (op == XN_NE && left->len > 0 && right->len > 0) {
            return compare_unequal_sets(tree, left, right, result);
        }
        if (op != XN_EQ && op != XN_NE && left->len >= 16 && right->len >= 16) {
            return compare_ordered_sets(tree, op, left, right, result);
        }
        for (Py_ssize_t index = 0; index < left->len && !*result; index++) {
            xp_result si;
            if (item_as_string(tree, left->items[index], &si) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                return -1;                                           /* GCOVR_EXCL_LINE */
            }
            for (Py_ssize_t inner_index = 0; inner_index < right->len && !*result; inner_index++) {
                xp_result sj;
                if (item_as_string(tree, right->items[inner_index], &sj) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                    xp_result_free(&si);                                        /* GCOVR_EXCL_LINE */
                    return -1;                                                  /* GCOVR_EXCL_LINE */
                }
                *result = cmp_scalar(tree, op, &si, &sj);
                xp_result_free(&sj);
            }
            xp_result_free(&si);
        }
        return 0;
    }
    xp_nodeset *ns = a_ns ? left : right;
    xp_result *other = a_ns ? second : first;
    for (Py_ssize_t index = 0; index < ns->len && !*result; index++) {
        xp_result si;
        if (item_as_string(tree, ns->items[index], &si) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;                                         /* GCOVR_EXCL_LINE */
        }
        *result = a_ns ? cmp_scalar(tree, op, &si, other) : cmp_scalar(tree, op, other, &si);
        xp_result_free(&si);
    }
    return 0;
}

static PyObject *comparison_string(struct th_tree *tree, xp_item item);

static int compare_equal_sets(struct th_tree *tree, const xp_nodeset *left, const xp_nodeset *right, int *result) {
    if (left->items[0].node == right->items[0].node && left->items[0].attr == right->items[0].attr) {
        *result = 1;
        return 0;
    }
    PyObject *first = comparison_string(tree, left->items[0]);
    if (first == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;       /* GCOVR_EXCL_LINE: allocation failure */
    }
    PyObject *seen = PySet_New(NULL);
    if (seen == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure */
        Py_DECREF(first); /* GCOVR_EXCL_LINE: allocation failure */
        return -1;        /* GCOVR_EXCL_LINE: allocation failure */
    }
    int rc = 0;
    for (Py_ssize_t index = 0; index < right->len; index++) {
        PyObject *text = comparison_string(tree, right->items[index]);
        if (text == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            rc = -1;        /* GCOVR_EXCL_LINE: allocation failure */
            break;          /* GCOVR_EXCL_LINE: allocation failure */
        }
        *result = PyObject_RichCompareBool(first, text, Py_EQ);
        if (*result) {
            Py_DECREF(text);
            break;
        }
        rc = PySet_Add(seen, text);
        Py_DECREF(text);
        if (rc < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            break;    /* GCOVR_EXCL_LINE: allocation failure */
        }
    }
    Py_DECREF(first);
    if (rc == 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        for (Py_ssize_t index = 1; index < left->len && !*result; index++) {
            PyObject *text = comparison_string(tree, left->items[index]);
            if (text == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                rc = -1;        /* GCOVR_EXCL_LINE: allocation failure */
                break;          /* GCOVR_EXCL_LINE: allocation failure */
            }
            *result = PySet_Contains(seen, text);
            Py_DECREF(text);
        }
    }
    Py_DECREF(seen);
    return rc;
}

static PyObject *comparison_string(struct th_tree *tree, xp_item item) {
    Py_ssize_t len;
    Py_UCS4 *text = item_string(tree, item, &len);
    if (text == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;    /* GCOVR_EXCL_LINE: allocation failure */
    }
    PyObject *value = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, text, len);
    PyMem_Free(text);
    return value;
}

/* Inequality is existential too: it is false only when both nonempty sets contain one shared value. */
static int compare_unequal_sets(struct th_tree *tree, const xp_nodeset *left, const xp_nodeset *right, int *result) {
    xp_result first;
    if (item_as_string(tree, left->items[0], &first) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                                          /* GCOVR_EXCL_LINE: allocation failure */
    }
    const xp_nodeset *sets[] = {right, left};
    int rc = 0;
    for (size_t side = 0; side < 2 && !*result; side++) {
        for (Py_ssize_t index = (Py_ssize_t)side; index < sets[side]->len && !*result; index++) {
            xp_result next;
            const int converted = item_as_string(tree, sets[side]->items[index], &next);
            if (converted < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                rc = -1;         /* GCOVR_EXCL_LINE: allocation failure */
                break;           /* GCOVR_EXCL_LINE: allocation failure */
            }
            *result = cmp_scalar(tree, XN_NE, &first, &next);
            xp_result_free(&next);
        }
        if (rc < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            break;    /* GCOVR_EXCL_LINE: allocation failure */
        }
    }
    xp_result_free(&first);
    return rc;
}

static int comparison_number(struct th_tree *tree, xp_item item, double *number);
static int comparison_extreme(struct th_tree *tree, const xp_nodeset *nodes, int minimum, double *value);

static int compare_ordered_sets(struct th_tree *tree, int op, const xp_nodeset *left, const xp_nodeset *right,
                                int *result) {
    xp_result first = {.kind = XP_NUMBER};
    xp_result second = {.kind = XP_NUMBER};
    const int first_rc = comparison_number(tree, left->items[0], &first.number);
    const int second_rc = comparison_number(tree, right->items[0], &second.number);
    if (first_rc < 0 || second_rc < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                       /* GCOVR_EXCL_LINE: allocation failure */
    }
    *result = cmp_scalar(tree, op, &first, &second);
    if (*result) {
        return 0;
    }
    const int less = op == XN_LT || op == XN_LE;
    const int left_rc = comparison_extreme(tree, left, less, &first.number);
    const int right_rc = comparison_extreme(tree, right, !less, &second.number);
    if (left_rc < 0 || right_rc < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                     /* GCOVR_EXCL_LINE: allocation failure */
    }
    *result = cmp_scalar(tree, op, &first, &second);
    return 0;
}

static int comparison_number(struct th_tree *tree, xp_item item, double *number) {
    xp_result text;
    if (item_as_string(tree, item, &text) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                               /* GCOVR_EXCL_LINE: allocation failure */
    }
    *number = to_number(tree, &text);
    xp_result_free(&text);
    return 0;
}

/* A matching pair exists iff the corresponding extrema compare true; NaNs cannot supply a match. */
static int comparison_extreme(struct th_tree *tree, const xp_nodeset *nodes, int minimum, double *value) {
    for (Py_ssize_t index = 1; index < nodes->len; index++) {
        double number;
        if (comparison_number(tree, nodes->items[index], &number) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;                                                   /* GCOVR_EXCL_LINE: allocation failure */
        }
        const int missing = isnan(*value); /* GCOVR_EXCL_BR_LINE: dead type-dispatch arm of the isnan macro */
        if (missing || (minimum ? number < *value : number > *value)) {
            *value = number;
        }
    }
    return 0;
}

/* Where target's item at left falls against source's item at right, as item_order answers, from ranks once the walks
   run out of budget or the items lie in separate trees; 3 on allocation failure. Out of line, as item_cmp was: inlined
   into the union that eval_expr_inner inlines, it grew that function until LTO spilled one more register on every
   evaluation. */
static TH_NOINLINE int union_order(xp_caches **caches, const xp_nodeset *target, const xp_nodeset *source,
                                   Py_ssize_t left, Py_ssize_t right, Py_ssize_t *budget, ranked_item **ranked) {
    if (*ranked == NULL) {
        int precedes = item_order(caches, &target->items[left], &source->items[right], budget);
        if (precedes != XP_ORDER_UNKNOWN) {
            return precedes;
        }
        *ranked = rank_sets(caches, target, source);
        if (*ranked == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return 3;          /* GCOVR_EXCL_LINE */
        }
    }
    return ranked_order(&(*ranked)[left], &(*ranked)[target->len + right]);
}

static int nodeset_union(xp_caches **caches, xp_nodeset *target, xp_nodeset *source) {
    /* Extension functions can return unordered nodes and duplicates. */
    if (target->snapshots || source->snapshots) {
        for (Py_ssize_t index = 0; index < source->len; index++) {
            int rc = ns_push(target, source->items[index].node, source->items[index].attr);
            if (rc < 0) {  /* GCOVR_EXCL_BR_LINE: allocation failure */
                return -1; /* GCOVR_EXCL_LINE */
            }
        }
        xp_nodeset_free(source);
        return sort_unique(caches, target);
    }
    if (sort_unique(caches, target) < 0 || sort_unique(caches, source) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;                                                            /* GCOVR_EXCL_LINE */
    }
    xp_nodeset merged = {0};
    ranked_item *ranked = NULL;
    Py_ssize_t budget = xp_order_budget(target->len + source->len);
    Py_ssize_t left = 0;
    Py_ssize_t right = 0;
    while (left < target->len || right < source->len) {
        const xp_item *item;
        if (left == target->len) {
            item = &source->items[right++];
        } else if (right == source->len) {
            item = &target->items[left++];
        } else {
            int precedes = union_order(caches, target, source, left, right, &budget, &ranked);
            if (precedes == 3) {          /* GCOVR_EXCL_BR_LINE: allocation failure */
                xp_nodeset_free(&merged); /* GCOVR_EXCL_LINE */
                return -1;                /* GCOVR_EXCL_LINE */
            }
            item = precedes <= 0 ? &target->items[left++] : &source->items[right];
            right += precedes >= 0;
        }
        if (ns_append(&merged, item->node, item->attr) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            PyMem_Free(ranked);                               /* GCOVR_EXCL_LINE: allocation failure */
            xp_nodeset_free(&merged);                         /* GCOVR_EXCL_LINE: allocation failure */
            return -1;                                        /* GCOVR_EXCL_LINE: allocation failure */
        }
    }
    PyMem_Free(ranked);
    xp_nodeset_free(target);
    xp_nodeset_free(source);
    *target = merged;
    return 0;
}

/* Deep-copy a bound variable's value so the reference owns its result. A node-set
   binding (an Element or an iterable of Elements from the caller) is copied and
   normalized to document order without duplicates, the canonical node-set form the
   rest of the evaluator assumes. */
/* Out of line: inlined, the sort's failure exit reshaped the variable lookup in eval_expr_inner and cost every string
   or number variable reference a few instructions. */
static TH_NOINLINE int copy_nodes(xp_ctx *ctx, const xp_nodeset *src, xp_nodeset *dst) {
    dst->items = PyMem_Malloc((size_t)src->len * sizeof(xp_item));
    if (dst->items == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;            /* GCOVR_EXCL_LINE */
    }
    memcpy(dst->items, src->items, (size_t)src->len * sizeof(xp_item));
    dst->len = src->len;
    dst->cap = src->len;
    if (sort_unique(ctx->caches, dst) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        xp_nodeset_free(dst);                /* GCOVR_EXCL_LINE */
        return -1;                           /* GCOVR_EXCL_LINE */
    }
    return 0;
}

static int copy_result(xp_ctx *ctx, const xp_result *src, xp_result *dst) {
    memset(dst, 0, sizeof(*dst));
    dst->kind = src->kind;
    if (src->kind == XP_STRING) {
        dst->string = ucs4_dup(src->string, src->string_len);
        if (dst->string == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;             /* GCOVR_EXCL_LINE */
        }
        dst->string_len = src->string_len;
    } else if (src->kind == XP_NUMBER) {
        dst->number = src->number;
    } else if (src->kind == XP_NODESET) {
        if (src->nodes.len > 0) {
            return copy_nodes(ctx, &src->nodes, &dst->nodes);
        }
    } else {
        dst->boolean = src->boolean;
    }
    return 0;
}

/* The parser builds `a | b | c` as a left-leaning spine of XN_UNION nodes. Walking that spine here, instead of
   recursing into eval_expr once per operand, keeps a long selector list from counting against XP_MAX_DEPTH, while
   the operands still evaluate left to right as before. */
static int eval_union(const xp_program *prog, const xn *expr, xp_ctx *ctx, xp_result *out) {
    size_t count = 1;
    int32_t leftmost = expr->first;
    while (prog->nodes[leftmost].kind == XN_UNION) {
        leftmost = prog->nodes[leftmost].first;
        count++;
    }
    int32_t *operands = PyMem_Malloc(count * sizeof(int32_t));
    if (operands == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
        return -1;          /* GCOVR_EXCL_LINE */
    }
    const xn *node = expr;
    for (size_t index = count; index > 0; index--) {
        operands[index - 1] = node->second;
        node = &prog->nodes[node->first];
    }
    xp_result left;
    int rc = eval_expr(prog, leftmost, ctx, &left);
    for (size_t index = 0; rc == 0 && index < count; index++) {
        xp_result right;
        xp_live_frame frame;
        if (ctx->live != NULL) {
            frame = (xp_live_frame){.results = &left, .result_count = 1};
            xp_live_enter(ctx, &frame);
        }
        rc = eval_expr(prog, operands[index], ctx, &right);
        xp_live_leave(ctx, &frame);
        if (rc < 0) {
            xp_result_free(&left);
            break;
        }
        if (left.kind != XP_NODESET || right.kind != XP_NODESET) {
            xp_result_free(&left);
            xp_result_free(&right);
            *ctx->feature = "a union of non-node-sets";
            rc = -4;
            break;
        }
        if (nodeset_union(ctx->caches, &left.nodes, &right.nodes) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            xp_result_free(&left);                                       /* GCOVR_EXCL_LINE */
            xp_result_free(&right);                                      /* GCOVR_EXCL_LINE: allocation failure */
            rc = -1;                                                     /* GCOVR_EXCL_LINE */
            break;                                                       /* GCOVR_EXCL_LINE */
        }
    }
    PyMem_Free(operands);
    if (rc == 0) {
        *out = left;
    }
    return rc;
}

/* The parser builds `a and b and c`, and likewise `or`, as a left-leaning spine of one operator, as it does a union.
   Walking that spine instead of recursing once per operator keeps a long chain, such as the one css_to_xpath writes
   for a compound selector, from counting against XP_MAX_DEPTH. The operands still evaluate left to right and stop at
   the first one that decides the result. */
static int eval_logical(const xp_program *prog, const xn *expr, xp_ctx *ctx, xp_result *out) {
    size_t count = 1;
    int32_t leftmost = expr->first;
    while (prog->nodes[leftmost].kind == expr->kind) {
        leftmost = prog->nodes[leftmost].first;
        count++;
    }
    int32_t *operands = PyMem_Malloc((count + 1) * sizeof(int32_t));
    if (operands == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
        return -1;          /* GCOVR_EXCL_LINE */
    }
    operands[0] = leftmost;
    const xn *node = expr;
    for (size_t index = count; index > 0; index--) {
        operands[index] = node->second;
        node = &prog->nodes[node->first];
    }
    int decisive = expr->kind == XN_OR;
    int value = !decisive;
    int rc = 0;
    for (size_t index = 0; index <= count; index++) {
        xp_result operand;
        rc = eval_condition(prog, operands[index], ctx, &operand);
        if (rc < 0) {
            break;
        }
        int truth = to_boolean(ctx->tree, &operand);
        xp_result_free(&operand);
        if (truth == decisive) {
            value = decisive;
            break;
        }
    }
    PyMem_Free(operands);
    if (rc == 0) {
        result_bool(out, value);
    }
    return rc;
}

static int eval_expr_inner(const xp_program *prog, int32_t idx, xp_ctx *ctx, xp_result *out, int exists) {
    const xn *expr = &prog->nodes[idx];
    switch (expr->kind) {
    case XN_NUM:
        result_number(out, expr->num);
        return 0;
    case XN_LIT: {
        /* Copied here rather than through ucs4_dup: reading the literal from expr after the allocation keeps its
           pointer and length out of registers that this path would otherwise have to save. */
        Py_UCS4 *text = PyMem_Malloc((size_t)expr->str_len * sizeof(Py_UCS4));
        if (text == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;      /* GCOVR_EXCL_LINE */
        }
        if (expr->str_len) {
            memcpy(text, expr->str, (size_t)expr->str_len * sizeof(Py_UCS4));
        }
        result_string(out, text, expr->str_len);
        return 0;
    }
    case XN_VAR: {
        const xp_binding *binding = ctx->vars == NULL ? NULL : ctx->vars->items;
        const xp_binding *end = ctx->vars == NULL ? NULL : binding + ctx->vars->len;
        while (binding != end && (binding->name_len != expr->str_len ||
                                  memcmp(binding->name, expr->str, (size_t)expr->str_len * sizeof(Py_UCS4)) != 0)) {
            binding++;
        }
        if (binding == end) {
            *ctx->feature = "a reference to an unbound variable";
            return -3;
        }
        ctx->root->mixed |= binding->value.kind == XP_NODESET; /* the caller's nodes, under any root */
        return copy_result(ctx, &binding->value, out);
    }
    case XN_PATH: {
        xp_nodeset ns = {0};
        int rc = eval_path(prog, idx, ctx, &ns, exists);
        if (rc < 0) {
            return rc;
        }
        memset(out, 0, sizeof(*out));
        out->kind = XP_NODESET;
        out->nodes = ns;
        return 0;
    }
    case XN_FILTER: {
        xp_result primary;
        int rc = eval_expr(prog, expr->first, ctx, &primary);
        if (rc < 0) {
            return rc;
        }
        if (primary.kind != XP_NODESET) {
            xp_result_free(&primary);
            *ctx->feature = "a predicate on a non-node-set";
            return -4;
        }
        rc = sort_unique(ctx->caches, &primary.nodes);
        if (rc == 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            xp_live_frame frame;
            if (ctx->live != NULL) {
                frame = (xp_live_frame){.results = &primary, .result_count = 1};
                xp_live_enter(ctx, &frame);
            }
            rc = apply_predicates(prog, expr->second, -1, ctx, &primary.nodes, XP_PREDICATE_DEPTH);
            xp_live_leave(ctx, &frame);
        }
        if (rc < 0) {
            xp_result_free(&primary);
            return rc;
        }
        *out = primary;
        return 0;
    }
    case XN_UNION:
        return eval_union(prog, expr, ctx, out);
    case XN_OR:
    case XN_AND:
        return eval_logical(prog, expr, ctx, out);
    case XN_NEG: {
        xp_result left;
        int rc = eval_expr(prog, expr->first, ctx, &left);
        if (rc < 0) {
            return rc;
        }
        result_number(out, -to_number(ctx->tree, &left));
        xp_result_free(&left);
        return 0;
    }
    case XN_FUNC:
        return eval_function(prog, idx, ctx, out);
    default: { /* the comparison and arithmetic binary operators */
        xp_result left;
        xp_result right;
        int rc = eval_expr(prog, expr->first, ctx, &left);
        if (rc < 0) {
            return rc;
        }
        xp_live_frame frame;
        if (ctx->live != NULL) {
            frame = (xp_live_frame){.results = &left, .result_count = 1};
            xp_live_enter(ctx, &frame);
        }
        rc = eval_expr(prog, expr->second, ctx, &right);
        xp_live_leave(ctx, &frame);
        if (rc < 0) {
            xp_result_free(&left);
            return rc;
        }
        if (expr->kind <= XN_GE) { /* the default case holds only the comparison and arithmetic operators */
            int cmp = 0;
            xp_live_frame comparison = {
                .sets = {left.kind == XP_NODESET ? &left.nodes : NULL, right.kind == XP_NODESET ? &right.nodes : NULL}};
            xp_live_enter(ctx, &comparison);
            rc = xp_before_python(ctx);
            if (rc == 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                rc = compare(ctx->tree, expr->kind, &left, &right, &cmp);
            }
            xp_live_leave(ctx, &comparison);
            if (rc == 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                result_bool(out, cmp);
            }
        } else {
            double left_value = to_number(ctx->tree, &left);
            double right_value = to_number(ctx->tree, &right);
            double value = expr->kind == XN_ADD   ? left_value + right_value
                           : expr->kind == XN_SUB ? left_value - right_value
                           : expr->kind == XN_MUL ? left_value * right_value
                           : expr->kind == XN_DIV ? left_value / right_value
                                                  : fmod(left_value, right_value);
            result_number(out, value);
        }
        xp_result_free(&left);
        xp_result_free(&right);
        return rc;
    }
    }
}

TH_NOINLINE int xp_too_deep(int depth) {
#ifdef TH_STACK_FLOOR
    return depth >= XP_MAX_DEPTH || TH_FRAME_ADDRESS() < th_stack_floor();
#else
    return depth >= XP_MAX_DEPTH;
#endif
}

/* Guard every recursive descent with a depth cap so a long operator spine or a deeply
   nested group raises past the bound instead of overflowing the C stack (issue #421). */
int eval_guarded(const xp_program *prog, int32_t idx, xp_ctx *ctx, xp_result *out, int exists) {
    if (ctx->depth >= XP_STACK_CHECK_DEPTH && xp_too_deep(ctx->depth)) {
        *ctx->feature = "an expression nested too deeply";
        return -5;
    }
#ifdef TH_OPERATION_LIMIT
    if (xp_charge(ctx, 1) < 0) {
        return -3;
    }
#endif
    ctx->depth++;
    int rc;
    if (ctx->live == NULL) {
        rc = eval_expr_inner(prog, idx, ctx, out, exists);
    } else {
        xp_live_frame frame = {.node = ctx->node};
        xp_live_enter(ctx, &frame);
        rc = eval_expr_inner(prog, idx, ctx, out, exists);
        xp_live_leave(ctx, &frame);
    }
    ctx->depth--;
    return rc;
}

/* eval_guarded with the caller's frame charged in the same check and update, so a function call or a path base pays
   no instruction of its own for it. Out of line: inlined into eval_function, it grew the LTO unit enough to stop an
   unrelated inline in the selector matcher. */
TH_NOINLINE int eval_held(const xp_program *prog, int32_t idx, xp_ctx *ctx, xp_result *out, int exists) {
    if (ctx->depth + XP_HELD_DEPTH >= XP_STACK_CHECK_DEPTH && xp_too_deep(ctx->depth + XP_HELD_DEPTH)) {
        *ctx->feature = "an expression nested too deeply";
        return -5;
    }
#ifdef TH_OPERATION_LIMIT
    if (xp_charge(ctx, 1) < 0) {
        return -3;
    }
#endif
    ctx->depth += 1 + XP_HELD_DEPTH;
    int rc;
    if (ctx->live == NULL) {
        rc = eval_expr_inner(prog, idx, ctx, out, exists);
    } else {
        xp_live_frame frame = {.node = ctx->node};
        xp_live_enter(ctx, &frame);
        rc = eval_expr_inner(prog, idx, ctx, out, exists);
        xp_live_leave(ctx, &frame);
    }
    ctx->depth -= 1 + XP_HELD_DEPTH;
    return rc;
}

/* Out of line, and only called on caches that exist: inlined, it made LTO stop inlining xp_eval_at into the XSLT
   callers, which cost 6.4% on the transform-key benchmark. */
static TH_NOINLINE void xp_caches_free(xp_caches *caches) {
    xr_cache_clear(&caches->regex);
    PyMem_Free(caches->positions.entries);
    PyMem_Free(caches->roots.entries);
    xp_lang_memo_free(caches->langs);
    PyMem_Free(caches);
}

/* xp_eval_at and xp_eval_condition_at: one top-level evaluation, with exists set when the caller only converts the
   value to a boolean. */
static int eval_at(const xp_program *prog, struct th_tree *tree, const xp_item *context, struct th_node *known_root,
                   Py_ssize_t pos, Py_ssize_t size, const xp_bindings *vars, const xp_namespaces *namespaces,
                   xp_extension_fn extension, void *extension_ctx, xp_lang_memo **langs, xp_id_cache *ids,
                   xp_result *out, const char **feature, int exists) {
    if (namespaces == NULL && prog->bound.len > 0) {
        namespaces = &prog->bound;
    }
    xp_caches *caches = NULL;
#ifdef TH_OPERATION_LIMIT
    size_t operations = 0;
#endif
    xp_root root = {known_root, 0, known_root != NULL, ids, {NULL, NULL}};
    xp_ctx ctx = {
        tree,        context->node, context->attr, pos,  size, feature, vars, namespaces, extension, extension_ctx,
        0,           &caches,       NULL,          NULL, NULL, NULL,    0,    &root,      langs,
#ifdef TH_OPERATION_LIMIT
        &operations,
#endif
    };
    int rc = eval_guarded(prog, prog->root, &ctx, out, exists);
    if (caches != NULL) {
        xp_caches_free(caches);
    }
    if (root.own.index != NULL) {
        xp_id_cache_clear(&root.own);
    }
    return rc;
}

int xp_eval_at(const xp_program *prog, struct th_tree *tree, const xp_item *context, struct th_node *known_root,
               Py_ssize_t pos, Py_ssize_t size, const xp_bindings *vars, const xp_namespaces *namespaces,
               xp_extension_fn extension, void *extension_ctx, xp_lang_memo **langs, xp_id_cache *ids, xp_result *out,
               const char **feature) {
    return eval_at(prog, tree, context, known_root, pos, size, vars, namespaces, extension, extension_ctx, langs, ids,
                   out, feature, 0);
}

int xp_eval_condition_at(const xp_program *prog, struct th_tree *tree, const xp_item *context,
                         struct th_node *known_root, Py_ssize_t pos, Py_ssize_t size, const xp_bindings *vars,
                         const xp_namespaces *namespaces, xp_extension_fn extension, void *extension_ctx,
                         xp_lang_memo **langs, xp_id_cache *ids, xp_result *out, const char **feature) {
    return eval_at(prog, tree, context, known_root, pos, size, vars, namespaces, extension, extension_ctx, langs, ids,
                   out, feature, 1);
}

int xp_eval_pattern_at(const xp_program *prog, struct th_tree *tree, struct th_node *context, xp_extension_fn extension,
                       void *extension_ctx, xp_name_test_fn name_test, void *name_test_ctx, xp_result *out,
                       const char **feature) {
    xp_caches *caches = NULL;
#ifdef TH_OPERATION_LIMIT
    size_t operations = 0;
#endif
    xp_root root = {0};
    xp_ctx ctx = {
        tree,
        context,
        -1,
        1,
        1,
        feature,
        NULL,
        NULL,
        extension,
        extension_ctx,
        0,
        &caches,
        NULL,
        NULL,
        name_test,
        name_test_ctx,
        name_test == NULL,
        &root,
        NULL,
#ifdef TH_OPERATION_LIMIT
        &operations,
#endif
    };
    int rc = eval_expr(prog, prog->root, &ctx, out);
    if (caches != NULL) {
        xp_caches_free(caches);
    }
    if (root.own.index != NULL) {
        xp_id_cache_clear(&root.own);
    }
    return rc;
}

/* A walk recurses once per step of the path, so a longer one keeps the forward evaluation. */
#define XP_PATTERN_WALK_STEPS 32

/* Level n of a pattern walk stands for the path's first n steps, level 0 for the root it starts at. member records
   whether those steps select a node and within whether they select it or one of its ancestors (2 yes, 1 no, absent
   unknown). A child step whose predicate reads the position runs from the parent, once per parent: ran holds the
   parents it ran from, selected the children it selected there. */
typedef struct {
    const xn *step;
    step_match match;
    int positional;
    th_node_map member;
    th_node_map within;
    th_node_map ran;
    th_node_map selected;
} xp_walk_level;

struct xp_pattern_walk {
    const xp_program *prog;
    struct th_tree *tree;
    xp_name_test_fn name_test;
    void *name_test_ctx;
    xp_caches *caches;
    xp_root root;
#ifdef TH_OPERATION_LIMIT
    size_t operations;
#endif
    int32_t count;
    xp_walk_level levels[];
};

int xp_pattern_walks_back(const xp_program *prog) {
    const xn *path = &prog->nodes[prog->root];
    if (path->kind != XN_PATH || !path->absolute) {
        return 0;
    }
    int32_t count = 0;
    for (int32_t index = path->first; index >= 0; index = prog->nodes[index].next) {
        const xn *step = &prog->nodes[index];
        /* an attribute has no children and no descendants, so only the last step can select one */
        int fits = step->axis == AX_CHILD || (step->axis == AX_ATTRIBUTE && step->next < 0) ||
                   ((step->axis == AX_DESCENDANT || step->axis == AX_DESCENDANT_OR_SELF) &&
                    !xp_step_reads_position(prog, index));
        if (!fits || ++count > XP_PATTERN_WALK_STEPS) {
            return 0;
        }
    }
    return 1;
}

static int walk_member(const xp_program *prog, xp_pattern_walk *walk, xp_ctx *ctx, int32_t level, xp_item item);

static int walk_note(th_node_map *map, const struct th_node *node, int found) {
    if (th_node_map_insert(map, node, found + 1) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        PyErr_NoMemory();                               /* GCOVR_EXCL_LINE */
        return -1;                                      /* GCOVR_EXCL_LINE */
    }
    return found;
}

/* Whether level selects node or one of its ancestors. The climb stops at the first node with an answer and records
   the answer on every node it passed, so the climbs of one walk pass each node once. */
static int walk_within(const xp_program *prog, xp_pattern_walk *walk, xp_ctx *ctx, int32_t level,
                       struct th_node *node) {
    th_node_map *known = &walk->levels[level].within;
    struct th_node *end = NULL;
    int found = 0;
    for (struct th_node *up = node; up != NULL; up = up->parent) {
        TH_WORK_STEP();
        Py_ssize_t seen = th_node_map_find(known, up);
        if (seen != 0) {
            found = seen == 2;
            end = up;
            break;
        }
        found = walk_member(prog, walk, ctx, level, (xp_item){up, -1});
        if (found != 0) {
            if (found < 0) {
                return found;
            }
            end = up->parent;
            break;
        }
    }
    for (struct th_node *up = node; up != end; up = up->parent) {
        if (walk_note(known, up, found) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;                         /* GCOVR_EXCL_LINE */
        }
    }
    return found;
}

/* The nodes level's step selects from one context node, with its predicates applied in proximity order. */
static int walk_run(const xp_program *prog, const xp_walk_level *at, xp_ctx *ctx, struct th_node *from,
                    xp_nodeset *out) {
    *out = (xp_nodeset){0};
    /* the walk resolved the name test before it started, so only allocation can fail the axis walk */
    if (apply_step(out, from, at->step->axis, at->step, &at->match, ctx) < 0) { /* GCOVR_EXCL_BR_LINE: allocation */
        xp_nodeset_free(out);                                                   /* GCOVR_EXCL_LINE */
        return -1;                                                              /* GCOVR_EXCL_LINE */
    }
    int rc = at->step->first < 0
                 ? 0
                 : apply_predicates(prog, at->step->first, -1, ctx, out, XP_HELD_DEPTH + XP_PREDICATE_DEPTH);
    if (rc < 0) {
        xp_nodeset_free(out);
    }
    return rc;
}

/* Whether the attribute item is among those the last step, an attribute step, selects from its owner element. */
static int walk_attribute(const xp_program *prog, const xp_walk_level *at, xp_ctx *ctx, xp_item item) {
    xp_nodeset selected;
    int rc = walk_run(prog, at, ctx, item.node, &selected);
    if (rc < 0) {
        return rc;
    }
    int found = 0;
    for (Py_ssize_t index = 0; index < selected.len; index++) {
        found |= selected.items[index].attr == item.attr;
    }
    xp_nodeset_free(&selected);
    return found;
}

/* Whether a node that passes the step's node test and whose context the earlier steps select also passes the step's
   predicates: tested on the node alone, or for a positional child step looked up among the children the step
   selects from the parent. */
static int walk_predicates(const xp_program *prog, xp_walk_level *at, xp_ctx *ctx, struct th_node *node) {
    if (at->positional) {
        struct th_node *parent = node->parent;
        if (th_node_map_find(&at->ran, parent) == 0) {
            xp_nodeset selected;
            int rc = walk_run(prog, at, ctx, parent, &selected);
            if (rc < 0) {
                return rc;
            }
            for (Py_ssize_t index = 0; index < selected.len; index++) {
                if (walk_note(&at->selected, selected.items[index].node, 1) < 0) { /* GCOVR_EXCL_BR_LINE: allocation */
                    xp_nodeset_free(&selected);                                    /* GCOVR_EXCL_LINE */
                    return -1;                                                     /* GCOVR_EXCL_LINE */
                }
            }
            xp_nodeset_free(&selected);
            if (walk_note(&at->ran, parent, 1) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                return -1;                            /* GCOVR_EXCL_LINE */
            }
        }
        return th_node_map_find(&at->selected, node) != 0;
    }
    for (int32_t pred = at->step->first; pred >= 0; pred = prog->nodes[pred].next) {
        xp_ctx pctx = *ctx;
        pctx.node = node;
        pctx.depth = ctx->depth + XP_HELD_DEPTH + XP_PREDICATE_DEPTH;
        xp_result value;
        int rc = eval_condition(prog, prog->nodes[pred].first, &pctx, &value);
        if (rc < 0) {
            return rc;
        }
        int keep = to_boolean(ctx->tree, &value);
        xp_result_free(&value);
        if (!keep) {
            return 0;
        }
    }
    return 1;
}

/* Whether the path's first level steps select item. The earlier steps are matched first, so a predicate runs only on
   the nodes the forward evaluation would run it on. */
static int walk_member(const xp_program *prog, xp_pattern_walk *walk, xp_ctx *ctx, int32_t level, xp_item item) {
    TH_WORK_STEP();
    if (level == 0) {
        return item.node == walk->root.node; /* the root carries no attribute */
    }
    xp_walk_level *at = &walk->levels[level];
    enum xp_axis axis = at->step->axis;
    if (axis == AX_ATTRIBUTE) {
        if (!xp_is_attribute(item.attr)) {
            return 0;
        }
        int found = walk_member(prog, walk, ctx, level - 1, (xp_item){item.node, -1});
        return found > 0 ? walk_attribute(prog, at, ctx, item) : found;
    }
    struct th_node *node = item.node;
    if (item.attr != -1 || (axis != AX_DESCENDANT_OR_SELF && node->parent == NULL)) {
        return 0;
    }
    Py_ssize_t seen = th_node_map_find(&at->member, node);
    if (seen != 0) {
        return seen == 2;
    }
    int found = node_test_matches(node, at->step, &at->match);
    if (found > 0) {
        if (axis == AX_CHILD) {
            found = walk_member(prog, walk, ctx, level - 1, (xp_item){node->parent, -1});
        } else {
            found = walk_within(prog, walk, ctx, level - 1, axis == AX_DESCENDANT ? node->parent : node);
        }
    }
    if (found > 0) {
        found = walk_predicates(prog, at, ctx, node);
    }
    return found < 0 ? found : walk_note(&at->member, node, found);
}

static xp_ctx walk_context(xp_pattern_walk *walk, xp_item item, xp_extension_fn extension, void *extension_ctx,
                           const char **feature) {
    return (xp_ctx){
        walk->tree,
        item.node,
        item.attr,
        1,
        1,
        feature,
        NULL,
        NULL,
        extension,
        extension_ctx,
        1,
        &walk->caches,
        NULL,
        NULL,
        walk->name_test,
        walk->name_test_ctx,
        0,
        &walk->root,
        NULL,
#ifdef TH_OPERATION_LIMIT
        &walk->operations,
#endif
    };
}

void xp_pattern_walk_free(xp_pattern_walk *walk) {
    if (walk == NULL) {
        return;
    }
    if (walk->caches != NULL) {
        xp_caches_free(walk->caches);
    }
    if (walk->root.own.index != NULL) {
        xp_id_cache_clear(&walk->root.own);
    }
    for (int32_t level = 0; level <= walk->count; level++) {
        PyMem_Free(walk->levels[level].member.entries);
        PyMem_Free(walk->levels[level].within.entries);
        PyMem_Free(walk->levels[level].ran.entries);
        PyMem_Free(walk->levels[level].selected.entries);
    }
    PyMem_Free(walk);
}

int xp_pattern_walk_start(const xp_program *prog, struct th_tree *tree, struct th_node *root, xp_name_test_fn name_test,
                          void *name_test_ctx, xp_pattern_walk **out, const char **feature) {
    const xn *path = &prog->nodes[prog->root];
    int32_t count = 0;
    for (int32_t index = path->first; index >= 0; index = prog->nodes[index].next) {
        count++;
    }
    xp_pattern_walk *walk = PyMem_Calloc(1, sizeof(*walk) + (size_t)(count + 1) * sizeof(xp_walk_level));
    if (walk == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
        return -1;        /* GCOVR_EXCL_LINE */
    }
    walk->prog = prog;
    walk->tree = tree;
    walk->name_test = name_test;
    walk->name_test_ctx = name_test_ctx;
    walk->root = (xp_root){root, 0, 1, NULL, {NULL, NULL}};
    walk->count = count;
    xp_ctx ctx = walk_context(walk, (xp_item){root, -1}, NULL, NULL, feature);
    int32_t level = 1;
    for (int32_t index = path->first; index >= 0; index = prog->nodes[index].next, level++) {
        xp_walk_level *at = &walk->levels[level];
        at->step = &prog->nodes[index];
        at->positional = xp_step_reads_position(prog, index);
        int rc = build_step_match(&ctx, at->step, &at->match);
        if (rc < 0) {
            xp_pattern_walk_free(walk);
            return rc;
        }
    }
    *out = walk;
    return 0;
}

int xp_pattern_match(xp_pattern_walk *walk, xp_item item, xp_extension_fn extension, void *extension_ctx,
                     const char **feature) {
    xp_ctx ctx = walk_context(walk, item, extension, extension_ctx, feature);
    return walk_member(walk->prog, walk, &ctx, walk->count, item);
}

int xp_eval(const xp_program *prog, struct th_tree *tree, struct th_node *context, const xp_bindings *vars,
            const xp_namespaces *namespaces, xp_extension_fn extension, void *extension_ctx, xp_result *out,
            const char **feature) {
    return xp_eval_at(prog, tree, &(xp_item){context, -1}, NULL, 1, 1, vars, namespaces, extension, extension_ctx, NULL,
                      NULL, out, feature);
}

int xp_eval_snapshot(const xp_program *prog, struct th_tree *tree, struct th_node *context, const xp_bindings *vars,
                     const xp_namespaces *namespaces, xp_extension_fn extension, void *extension_ctx,
                     xp_before_python_fn before_python, xp_result *out, const char **feature) {
    xp_caches *caches = NULL;
    xp_live_registry live = {0};
#ifdef TH_OPERATION_LIMIT
    size_t operations = 0;
#endif
    xp_root root = {0};
    xp_ctx ctx = {
        tree,        context,       -1,   1,    1, feature, vars, namespaces, extension, extension_ctx, 0, &caches,
        &live,       before_python, NULL, NULL, 0, &root,   NULL,
#ifdef TH_OPERATION_LIMIT
        &operations,
#endif
    };
    xp_live_frame frame = {.node = context, .vars = vars};
    xp_live_enter(&ctx, &frame);
    int rc = eval_expr(prog, prog->root, &ctx, out);
    if (caches != NULL) {
        xp_caches_free(caches);
    }
    if (root.own.index != NULL) {
        xp_id_cache_clear(&root.own);
    }
    return rc;
}
