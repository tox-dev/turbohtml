/* Shadow DOM tree model: attach_shadow / ShadowRoot, the host<->root linkage, <slot>
   assignment (named + default slots, assigned_nodes / assigned_elements /
   assigned_slot), and the flattened-tree traversal.

   A shadow root is a document-fragment-like TH_NODE_CONTENT node held off the light
   tree -- it is never a child of any node, so the light DOM's walks and serialization
   never reach it. The per-tree shadow table (tree_internal.h) is the only path between
   a host element and its shadow root and back. The slot-assignment algorithms follow
   the DOM Living Standard (find a slot, find slotables, find flattened slotables); they
   scan on demand, and once a handle has read a large shadow tree a few times, read a
   slot table per shadow root that the handle keeps until the next edit that can change
   an assignment, so a later light-DOM edit is always reflected. Every algorithm here is
   pure C over th_node; the bindings hold the host's per-tree critical section and wrap
   the resulting node arrays. */

#include "dom/nodes.h"

#include "core/hash.h"     /* th_name_hash keyed hash for the slot-name index */
#include "core/node_map.h" /* shadow root to slot table position */
#include "core/vec.h"      /* th_grow_cap overflow-safe growth */

/* A grow-on-demand array of node pointers the assignment/flatten walks accumulate
   into, wrapped into a Python list by the binding once the walk finishes. failed is
   set on an allocation failure so the caller reports it after freeing the buffer. */
typedef union {
    th_node *node;
    PyObject *wrapper;
} nodevec_item;

typedef struct {
    nodevec_item *items;
    Py_ssize_t len;
    Py_ssize_t cap;
    int failed;
} nodevec;

static void nodevec_push(nodevec *vec, th_node *node) {
    if (vec->failed) { /* GCOVR_EXCL_BR_LINE: only set on an unforceable allocation failure */
        return;        /* GCOVR_EXCL_LINE: allocation-failure path */
    } /* GCOVR_EXCL_LINE: closes the allocation-failure-only branch */
    if (vec->len == vec->cap) {
        size_t cap, bytes;
        /* the requested length cannot overflow size_t, so the grow guard never trips */
        int fits = th_grow_cap((size_t)vec->len + 1, (size_t)vec->cap, 8, sizeof(nodevec_item), &cap, &bytes);
        if (!fits) {         /* GCOVR_EXCL_BR_LINE: overflow-guard path, unreachable from a test */
            vec->failed = 1; /* GCOVR_EXCL_LINE: overflow-guard path */
            return;          /* GCOVR_EXCL_LINE: overflow-guard path */
        }
        nodevec_item *items = PyMem_Realloc(vec->items, bytes);
        if (items == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            vec->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
            return;          /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        vec->items = items;
        vec->cap = (Py_ssize_t)cap;
    }
    vec->items[vec->len++].node = node;
}

/* Whether node is an HTML <slot> element (the shadow tree's insertion point). */
static int is_slot(const th_node *node) {
    return node->type == TH_NODE_ELEMENT && node->ns == TH_NS_HTML && node->atom == TH_TAG_SLOT;
}

/* Whether node can be assigned to a slot: an element or a text node (DOM slotable). */
static int is_slottable(const th_node *node) {
    return node->type == TH_NODE_ELEMENT || node->type == TH_NODE_TEXT;
}

/* The value run of an element's named attribute, or the empty run when the attribute
   is absent or valueless (both are the empty name for slot matching). */
static void named_value(th_node *node, uint32_t atom, const Py_UCS4 **value, Py_ssize_t *len) {
    const th_node_attr *attr = find_node_attr(node, atom);
    if (attr != NULL && attr->value != NULL) {
        *value = attr->value;
        *len = attr->value_len;
    } else {
        *value = NULL;
        *len = 0;
    }
}

/* Whether two code-point runs are byte-for-byte equal (slot names match case-sensitively). */
static int runs_equal(const Py_UCS4 *left, Py_ssize_t left_len, const Py_UCS4 *right, Py_ssize_t right_len) {
    if (left_len != right_len) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < left_len; index++) {
        if (left[index] != right[index]) {
            return 0;
        }
    }
    return 1;
}

/* Every SLOT_SCAN_PERIOD-th scan of a handle counts the nodes it visits, and one that visits at least SLOT_LONG_SCAN
   counts toward the slot cache, which starts after SLOT_CACHE_SCANS of them. Repeated reads of a large shadow tree
   then cost one walk between edits, while a small tree, or a large one read a few times, keeps the plain scan and
   never allocates; the other scans pay one decrement for the sampling. */
#define SLOT_SCAN_PERIOD 64
#define SLOT_LONG_SCAN 32
#define SLOT_CACHE_SCANS 2

/* The first slot named want in tree order. steps is NULL or a constant at each call, so the plain copy counts nothing;
   otherwise the walk adds the nodes it visits to *steps. */
static inline th_node *find_named_slot(th_node *shadow, const Py_UCS4 *want, Py_ssize_t want_len, size_t *steps) {
    for (th_node *node = shadow->first_child; node != NULL; node = preorder_next(node, shadow)) {
        TH_WORK_STEP();
        if (steps != NULL) {
            (*steps)++;
        }
        if (is_slot(node)) {
            const Py_UCS4 *name = NULL;
            Py_ssize_t name_len = 0;
            named_value(node, TH_ATTR_NAME, &name, &name_len);
            if (runs_equal(name, name_len, want, want_len)) {
                return node;
            }
        }
    }
    return NULL;
}

/* Collect the slotables assigned to slot, a slot of the shadow tree under root, by scanning the host's children from
   child, the first one: the ones whose assigned slot is this one, in order. steps is as for find_named_slot.
   (DOM: find slotables.) */
static inline void scan_slotables(th_node *slot, th_node *root, th_node *child, nodevec *vec, size_t *steps) {
    while (child != NULL && !is_slottable(child)) {
        TH_WORK_STEP();
        if (steps != NULL) {
            (*steps)++;
        }
        child = child->next_sibling;
    }
    if (child == NULL) {
        return;
    }
    const Py_UCS4 *name = NULL;
    Py_ssize_t name_len = 0;
    named_value(slot, TH_ATTR_NAME, &name, &name_len);
    int checked_slot = 0;
    for (; child != NULL; child = child->next_sibling) {
        TH_WORK_STEP();
        if (steps != NULL) {
            (*steps)++;
        }
        if (!is_slottable(child)) {
            continue;
        }
        const Py_UCS4 *want = NULL;
        Py_ssize_t want_len = 0;
        if (child->type == TH_NODE_ELEMENT) {
            named_value(child, TH_ATTR_SLOT, &want, &want_len);
        }
        if (runs_equal(name, name_len, want, want_len)) {
            if (!checked_slot) {
                if (find_named_slot(root, name, name_len, steps) != slot) {
                    return;
                }
                checked_slot = 1;
            }
            nodevec_push(vec, child);
        }
    }
}

typedef struct {
    const Py_UCS4 *name;
    Py_ssize_t name_len;
    th_node *slot;
    nodevec assigned;
} slot_bucket;

typedef struct {
    th_node *root;
    slot_bucket *buckets;
    size_t cap;
} slot_index;

static void slot_index_empty(slot_index *index) {
    for (size_t position = 0; position < index->cap; position++) {
        PyMem_Free(index->buckets[position].assigned.items);
    }
}

static void slot_index_clear(slot_index *index) {
    slot_index_empty(index);
    PyMem_Free(index->buckets);
    *index = (slot_index){0};
}

/* The home slot of name: FNV-1a over the code points, the hash the index starts on (core/hash.h), or th_name_hash
   when keyed, which a fill moves the index to since the page picks its slot names. */
static size_t slot_index_home(const slot_index *index, const Py_UCS4 *name, Py_ssize_t name_len, int keyed) {
    size_t hash = 2166136261U;
    if (keyed) {
        hash = th_name_hash(name, name_len * (Py_ssize_t)sizeof(Py_UCS4));
    } else {
        for (Py_ssize_t position = 0; position < name_len; position++) {
            hash = (hash ^ name[position]) * 16777619U;
        }
    }
    return hash & (index->cap - 1);
}

/* The slot of name from its home: its bucket, or the empty one that ends its run. */
static size_t slot_index_walk(const slot_index *index, size_t position, const Py_UCS4 *name, Py_ssize_t name_len) {
    TH_WORK_STEP();
    while (index->buckets[position].slot != NULL &&
           !runs_equal(name, name_len, index->buckets[position].name, index->buckets[position].name_len)) {
        TH_WORK_STEP();
        position = (position + 1) & (index->cap - 1);
    }
    return position;
}

/* The slot of name in a keyed index. Out of line, so the FNV-1a lookup keeps its registers. */
static TH_NOINLINE size_t slot_index_find_keyed(const slot_index *index, const Py_UCS4 *name, Py_ssize_t name_len) {
    return slot_index_walk(index, slot_index_home(index, name, name_len, 1), name, name_len);
}

/* The bucket of name, or NULL when the fill on FNV-1a would walk past TH_FAST_PROBE_LIMIT slots to reach it. Every
   later lookup is for a slot's own name, which walks no further than the fill did to seat it, so the fill's walks bound
   them all. The count starts on the first step past the home, so a name seated there pays nothing for the bound. */
static inline slot_bucket *slot_index_fill_bucket(slot_index *index, const Py_UCS4 *name, Py_ssize_t name_len,
                                                  int keyed) {
    size_t position = slot_index_home(index, name, name_len, keyed);
    TH_WORK_STEP();
    if (index->buckets[position].slot == NULL ||
        runs_equal(name, name_len, index->buckets[position].name, index->buckets[position].name_len)) {
        return &index->buckets[position];
    }
    size_t steps_left = TH_FAST_PROBE_LIMIT + 1;
    do {
        TH_WORK_STEP();
        position = (position + 1) & (index->cap - 1);
        if (!keyed && --steps_left == 0) {
            return NULL;
        }
    } while (index->buckets[position].slot != NULL &&
             !runs_equal(name, name_len, index->buckets[position].name, index->buckets[position].name_len));
    return &index->buckets[position];
}

/* Seat every slot of the root and assign the host's slotables, under th_name_hash when keyed, else FNV-1a. 1 when a
   walk on FNV-1a went past the limit, which stops the fill, so a fill costs at most the limit per name; -1 on
   allocation failure. keyed is a constant at each call, so each copy computes one hash. */
static inline int slot_index_fill(th_tree *tree, slot_index *index, int keyed) {
    for (th_node *node = index->root->first_child; node != NULL; node = preorder_next(node, index->root)) {
        if (is_slot(node)) {
            const Py_UCS4 *name = NULL;
            Py_ssize_t name_len = 0;
            named_value(node, TH_ATTR_NAME, &name, &name_len);
            slot_bucket *bucket = slot_index_fill_bucket(index, name, name_len, keyed);
            if (bucket == NULL) {
                return 1;
            }
            if (bucket->slot == NULL) {
                bucket->name = name;
                bucket->name_len = name_len;
                bucket->slot = node;
            }
        }
    }
    for (th_node *child = th_shadow_host(tree, index->root)->first_child; child != NULL; child = child->next_sibling) {
        if (!is_slottable(child)) {
            continue;
        }
        const Py_UCS4 *name = NULL;
        Py_ssize_t name_len = 0;
        if (child->type == TH_NODE_ELEMENT) {
            named_value(child, TH_ATTR_SLOT, &name, &name_len);
        }
        slot_bucket *bucket = slot_index_fill_bucket(index, name, name_len, keyed);
        if (bucket == NULL) {
            return 1;
        }
        if (bucket->slot != NULL) {
            nodevec_push(&bucket->assigned, child);
            if (bucket->assigned.failed) { /* GCOVR_EXCL_BR_LINE: nodevec fails only on allocation failure */
                return -1;                 /* GCOVR_EXCL_LINE: allocation-failure path */
            } /* GCOVR_EXCL_LINE: closes the allocation-failure-only branch */
        }
    }
    return 0;
}

/* slot_index_fill under th_name_hash, after the FNV-1a fill went past the limit. Out of line, so the FNV-1a fill keeps
   its registers. */
static TH_NOINLINE int slot_index_fill_keyed(th_tree *tree, slot_index *index) {
    slot_index_empty(index);
    memset(index->buckets, 0, index->cap * sizeof(slot_bucket));
    return slot_index_fill(tree, index, 1);
}

static TH_NOINLINE int slot_index_build(th_tree *tree, slot_index *index) {
    size_t count = 0;
    for (th_node *node = index->root->first_child; node != NULL; node = preorder_next(node, index->root)) {
        count += is_slot(node);
    }
    size_t cap = 8;
    while (count >= cap / 2) {
        if (cap > SIZE_MAX / 2 / sizeof(slot_bucket)) { /* GCOVR_EXCL_BR_LINE: unforceable allocation overflow */
            return -1;                                  /* GCOVR_EXCL_LINE: allocation-overflow path */
        } /* GCOVR_EXCL_LINE: closes the allocation-overflow-only branch */
        cap *= 2;
    }
    index->buckets = PyMem_Calloc(cap, sizeof(slot_bucket));
    if (index->buckets == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;                /* GCOVR_EXCL_LINE: allocation-failure path */
    } /* GCOVR_EXCL_LINE: closes the allocation-failure-only branch */
    index->cap = cap;
    const int filled = slot_index_fill(tree, index, 0);
    return filled > 0 ? slot_index_fill_keyed(tree, index) : filled;
}

typedef struct {
    const Py_UCS4 *name;
    Py_ssize_t name_len;
    th_node *slot;
    nodevec assigned;
    int used;
} slot_entry;

/* The slot names of one shadow tree, kept across reads in the handle's slot cache and filled only as far as the reads
   need. The first slot of each name is found by a pre-order walk that resumes at unwalked, so the lookups together
   visit each element once. The host's children are bucketed under the names they ask for on the first read of the
   slotables. Both kinds of entry share the table. */
typedef struct {
    th_node *root;
    slot_entry *entries;
    size_t cap;
    size_t count;
    int keyed;
    int bucketed;
    th_node *unwalked;
} slot_table;

static void slot_table_clear(slot_table *table) {
    for (size_t position = 0; position < table->cap; position++) {
        PyMem_Free(table->entries[position].assigned.items);
    }
    PyMem_Free(table->entries);
}

/* The home entry of name. FNV-1a until a probe run passes TH_FAST_PROBE_LIMIT, then th_name_hash (core/hash.h), so a
   document can pick names that share a run only for one bounded rehash. */
static size_t slot_table_home(const slot_table *table, const Py_UCS4 *name, Py_ssize_t name_len) {
    if (table->keyed) {
        return th_name_hash(name, name_len * (Py_ssize_t)sizeof(Py_UCS4)) & (table->cap - 1);
    }
    size_t hash = 2166136261U;
    for (Py_ssize_t position = 0; position < name_len; position++) {
        hash = (hash ^ name[position]) * 16777619U;
    }
    return hash & (table->cap - 1);
}

/* Set *entry to the entry of name, or to the unused one that ends its run. Returns 1, leaving *entry unset, when an
   FNV-1a run passes TH_FAST_PROBE_LIMIT before either, so the caller rehashes keyed. */
static int slot_table_probe(const slot_table *table, const Py_UCS4 *name, Py_ssize_t name_len, slot_entry **entry) {
    size_t position = slot_table_home(table, name, name_len);
    const size_t limit = table->keyed ? SIZE_MAX : TH_FAST_PROBE_LIMIT;
    for (size_t steps = 0;; steps++) {
        TH_WORK_STEP();
        slot_entry *candidate = &table->entries[position];
        if (!candidate->used || runs_equal(name, name_len, candidate->name, candidate->name_len)) {
            *entry = candidate;
            return 0;
        }
        if (steps == limit) {
            return 1;
        }
        position = (position + 1) & (table->cap - 1);
    }
}

/* Move every entry into a table of cap entries hashed under keyed. The new table is at most half full, so each run
   ends. Returns 0, or -1 on allocation failure. */
static int slot_table_resize(slot_table *table, size_t cap, int keyed) {
    slot_entry *entries = PyMem_Calloc(cap, sizeof(slot_entry));
    if (entries == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;         /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    slot_entry *old = table->entries;
    size_t old_cap = table->cap;
    table->entries = entries;
    table->cap = cap;
    table->keyed = keyed;
    for (size_t position = 0; position < old_cap; position++) {
        if (old[position].used) {
            size_t home = slot_table_home(table, old[position].name, old[position].name_len);
            while (table->entries[home].used) {
                TH_WORK_STEP();
                home = (home + 1) & (table->cap - 1);
            }
            table->entries[home] = old[position];
        }
    }
    PyMem_Free(old);
    return 0;
}

/* Set *entry to the entry of name, rehashing keyed once an FNV-1a run is too long. Returns 0, or -1 on allocation
   failure. The table must hold at least one entry. */
static int slot_table_seek(slot_table *table, const Py_UCS4 *name, Py_ssize_t name_len, slot_entry **entry) {
    if (slot_table_probe(table, name, name_len, entry) == 0) {
        return 0;
    }
    if (slot_table_resize(table, table->cap, 1) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                                     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    slot_table_probe(table, name, name_len, entry);
    return 0;
}

/* Set *entry to the entry of name, claimed for it when the table has none. Returns 0, or -1 on allocation failure. */
static int slot_table_claim(slot_table *table, const Py_UCS4 *name, Py_ssize_t name_len, slot_entry **entry) {
    if (table->count >= table->cap / 2) {
        size_t cap, bytes;
        /* the requested length cannot overflow size_t, so the grow guard never trips */
        int fits = th_grow_cap((table->count + 1) * 2, table->cap, 8, sizeof(slot_entry), &cap, &bytes);
        if (!fits) {   /* GCOVR_EXCL_BR_LINE: overflow-guard path, unreachable from a test */
            return -1; /* GCOVR_EXCL_LINE: overflow-guard path */
        }
        if (slot_table_resize(table, cap, table->keyed) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;                                         /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    if (slot_table_seek(table, name, name_len, entry) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                                           /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (!(*entry)->used) {
        (*entry)->used = 1;
        (*entry)->name = name;
        (*entry)->name_len = name_len;
        table->count++;
    }
    return 0;
}

/* Set *entry to the entry of name, or NULL when the table holds none. Returns 0, or -1 on allocation failure. */
static int slot_table_lookup(slot_table *table, const Py_UCS4 *name, Py_ssize_t name_len, slot_entry **entry) {
    *entry = NULL;
    if (table->cap == 0) {
        return 0;
    }
    if (slot_table_seek(table, name, name_len, entry) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                                           /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (!(*entry)->used) {
        *entry = NULL;
    }
    return 0;
}

/* The first element at or after node in the pre-order walk of root, NULL when none is left. The walk rests only on
   elements: strip_comments unlinks comments without dropping the cache, and no slot is a comment. */
static th_node *slot_walk_element(th_node *node, th_node *root) {
    while (node != NULL && node->type != TH_NODE_ELEMENT) {
        TH_WORK_STEP();
        node = preorder_next(node, root);
    }
    return node;
}

/* Set *slot to the first slot named name in tree order, NULL when there is none. *entry is the name's entry from
   slot_table_lookup on entry and the name's entry on return. The walk resumes at unwalked only while it has not met
   the name. Returns 0, or -1 on allocation failure. */
static int slot_table_find(slot_table *table, const Py_UCS4 *name, Py_ssize_t name_len, slot_entry **entry,
                           th_node **slot) {
    *slot = *entry != NULL ? (*entry)->slot : NULL;
    if (*slot != NULL || table->unwalked == NULL) {
        return 0;
    }
    while (*slot == NULL && table->unwalked != NULL) {
        TH_WORK_STEP();
        th_node *node = table->unwalked;
        if (is_slot(node)) {
            const Py_UCS4 *found = NULL;
            Py_ssize_t found_len = 0;
            named_value(node, TH_ATTR_NAME, &found, &found_len);
            slot_entry *claimed;
            if (slot_table_claim(table, found, found_len, &claimed) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            if (claimed->slot == NULL) {
                claimed->slot = node;
                if (runs_equal(found, found_len, name, name_len)) {
                    *slot = node;
                    *entry = claimed;
                }
            }
        }
        table->unwalked = slot_walk_element(preorder_next(node, table->root), table->root);
    }
    return *slot == NULL ? slot_table_lookup(table, name, name_len, entry) : 0;
}

/* The first slot named name, as slot_table_find, for a caller that has no entry yet. */
static int slot_table_first(slot_table *table, const Py_UCS4 *name, Py_ssize_t name_len, th_node **slot) {
    slot_entry *entry;
    if (slot_table_lookup(table, name, name_len, &entry) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                                              /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return slot_table_find(table, name, name_len, &entry, slot);
}

/* Bucket the host's slottable children under the slot names they ask for, in order. Returns 0, or -1 on allocation
   failure. */
static int slot_table_children(th_tree *tree, slot_table *table) {
    for (th_node *child = th_shadow_host(tree, table->root)->first_child; child != NULL; child = child->next_sibling) {
        TH_WORK_STEP();
        if (!is_slottable(child)) {
            continue;
        }
        const Py_UCS4 *name = NULL;
        Py_ssize_t name_len = 0;
        if (child->type == TH_NODE_ELEMENT) {
            named_value(child, TH_ATTR_SLOT, &name, &name_len);
        }
        slot_entry *entry;
        if (slot_table_claim(table, name, name_len, &entry) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;                                             /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        nodevec_push(&entry->assigned, child);
        if (entry->assigned.failed) { /* GCOVR_EXCL_BR_LINE: nodevec fails only on allocation failure */
            return -1;                /* GCOVR_EXCL_LINE: allocation-failure path */
        } /* GCOVR_EXCL_LINE: closes the allocation-failure-only branch */
    }
    return 0;
}

/* The slot tables of the shadow roots read since the cache started (HandleObject.slot_cache). A structural edit moves
   the handle's mutation_version past the cache's, the few mutators that relink nodes without that drop the cache
   themselves, and a write or removal of slot=, or of name= on a <slot>, marks it stale in the tree
   (th_tree_slot_cache_valid), so the next read drops a table before it can outlive the child lists or slot attributes
   it was built from. Edits pay nothing while no cache exists. */
typedef struct {
    slot_table *tables;
    Py_ssize_t count;
    Py_ssize_t cap;
    th_node_map by_root; /* shadow root -> 1-based position in tables */
    uint64_t mutation_version;
} slot_cache;

void handle_free_slots(HandleObject *handle) {
    slot_cache *cache = handle->slot_cache;
    for (Py_ssize_t position = 0; position < cache->count; position++) {
        slot_table_clear(&cache->tables[position]);
    }
    PyMem_Free(cache->tables);
    PyMem_Free(cache->by_root.entries);
    PyMem_Free(cache);
    handle->slot_cache = NULL;
    handle->slot_scans = 0;
    handle->slot_scan_wait = SLOT_SCAN_PERIOD - 1;
    th_tree_set_slot_cache(handle->tree, 0);
}

/* Whether the handle's slot cache, if it has one, still matches the tree. A stale one is dropped here, which sends the
   read back to the scan. Out of line, so a scan pays one test for the cache. */
static TH_NOINLINE int slot_cache_current(HandleObject *handle) {
    const slot_cache *cache = handle->slot_cache;
    if (cache != NULL &&
        (cache->mutation_version != handle->mutation_version || !th_tree_slot_cache_valid(handle->tree))) {
        handle_free_slots(handle);
        return 0;
    }
    return 1;
}

/* Whether this read goes through the slot cache: once the handle has run SLOT_CACHE_SCANS long scans since the cache
   was last dropped, and the cache is not stale. */
static inline int slot_cache_ready(HandleObject *handle) {
    return handle->slot_scans >= SLOT_CACHE_SCANS && slot_cache_current(handle);
}

/* Record a counted scan of steps nodes, and send the next read down the slow path at once when the cache is due, else
   after SLOT_SCAN_PERIOD - 1 plain scans. */
static void slot_note_scan(HandleObject *handle, size_t steps) {
    handle->slot_scans += steps >= SLOT_LONG_SCAN;
    handle->slot_scan_wait = handle->slot_scans >= SLOT_CACHE_SCANS ? 0 : SLOT_SCAN_PERIOD - 1;
}

/* The slot table of the shadow tree under root, created on the root's first read through the cache, or NULL on
   allocation failure. The pointer holds until the next call, which may move the array. */
static slot_table *handle_slot_table(HandleObject *handle, th_node *root) {
    slot_cache *cache = handle->slot_cache;
    if (cache == NULL) {
        cache = PyMem_Calloc(1, sizeof(slot_cache));
        if (cache == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        cache->mutation_version = handle->mutation_version;
        handle->slot_cache = cache;
        th_tree_set_slot_cache(handle->tree, 1);
    }
    Py_ssize_t position = th_node_map_find(&cache->by_root, root);
    if (position == 0) {
        if (cache->count == cache->cap) {
            size_t cap, bytes;
            /* the requested length cannot overflow size_t, so the grow guard never trips */
            int fits = th_grow_cap((size_t)cache->count + 1, (size_t)cache->cap, 4, sizeof(slot_table), &cap, &bytes);
            if (!fits) {     /* GCOVR_EXCL_BR_LINE: overflow-guard path, unreachable from a test */
                return NULL; /* GCOVR_EXCL_LINE: overflow-guard path */
            }
            slot_table *tables = PyMem_Realloc(cache->tables, bytes);
            if (tables == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            cache->tables = tables;
            cache->cap = (Py_ssize_t)cap;
        }
        if (th_node_map_insert(&cache->by_root, root, cache->count + 1) < 0) { /* GCOVR_EXCL_BR_LINE: OOM */
            return NULL;                                                       /* GCOVR_EXCL_LINE */
        }
        position = ++cache->count;
        cache->tables[position - 1] =
            (slot_table){.root = root, .unwalked = slot_walk_element(root->first_child, root)};
    }
    return &cache->tables[position - 1];
}

/* find_slot's read through the slot cache, or its counted scan. Out of line, so the plain scan keeps its registers. */
static TH_NOINLINE int find_slot_slow(HandleObject *handle, th_node *shadow, const Py_UCS4 *want, Py_ssize_t want_len,
                                      th_node **slot) {
    if (slot_cache_ready(handle)) {
        handle->slot_scan_wait = 0;
        slot_table *table = handle_slot_table(handle, shadow);
        if (table == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        return slot_table_first(table, want, want_len, slot);
    }
    size_t steps = 0;
    *slot = find_named_slot(shadow, want, want_len, &steps);
    slot_note_scan(handle, steps);
    return 0;
}

/* Set *slot to the slot slottable is assigned to, NULL when it has none. (DOM: find a slot.) Returns 0, or -1 on
   allocation failure. */
static int find_slot(HandleObject *handle, th_node *slottable, th_node **slot) {
    *slot = NULL;
    th_node *parent = slottable->parent;
    if (parent == NULL) {
        return 0;
    }
    th_node *shadow = th_element_shadow_root(handle->tree, parent);
    if (shadow == NULL) {
        return 0;
    }
    if (th_shadow_mode(shadow) != 0) {
        return 0;
    }
    const Py_UCS4 *want = NULL;
    Py_ssize_t want_len = 0;
    if (slottable->type == TH_NODE_ELEMENT) {
        named_value(slottable, TH_ATTR_SLOT, &want, &want_len);
    }
    if (--handle->slot_scan_wait >= 0) {
        *slot = find_named_slot(shadow, want, want_len, NULL);
        return 0;
    }
    return find_slot_slow(handle, shadow, want, want_len, slot);
}

/* Collect the slotables assigned to slot, a slot of the shadow tree under root, from the root's slot table: its first
   read buckets the host's children, and every read copies its name's bucket. */
static void collect_slotables_cached(HandleObject *handle, th_node *slot, th_node *root, nodevec *vec) {
    slot_table *table = handle_slot_table(handle, root);
    if (table == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        vec->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
        return;          /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (!table->bucketed) {
        if (slot_table_children(handle->tree, table) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            handle_free_slots(handle);                      /* GCOVR_EXCL_LINE: allocation-failure path */
            vec->failed = 1;                                /* GCOVR_EXCL_LINE: allocation-failure path */
            return;                                         /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        table->bucketed = 1;
    }
    const Py_UCS4 *name = NULL;
    Py_ssize_t name_len = 0;
    named_value(slot, TH_ATTR_NAME, &name, &name_len);
    slot_entry *entry;
    if (slot_table_lookup(table, name, name_len, &entry) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        vec->failed = 1;                                        /* GCOVR_EXCL_LINE: allocation-failure path */
        return;                                                 /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (entry == NULL || entry->assigned.len == 0) {
        return;
    }
    th_node *first;
    if (slot_table_find(table, name, name_len, &entry, &first) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        vec->failed = 1;                                              /* GCOVR_EXCL_LINE: allocation-failure path */
        return;                                                       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (first != slot) {
        return;
    }
    for (Py_ssize_t position = 0; position < entry->assigned.len; position++) {
        nodevec_push(vec, entry->assigned.items[position].node);
    }
}

/* Collect the slotables assigned to slot by a scan: the host's children whose assigned slot is this one, in order. A
   slot outside a shadow tree has none. (DOM: find slotables.) */
static void collect_slotables(th_tree *tree, th_node *slot, nodevec *vec) {
    th_node *root = node_root(slot);
    if (!th_node_is_shadow_root(root)) {
        return;
    }
    th_node *first = th_shadow_host(tree, root)->first_child;
    if (first != NULL) {
        scan_slotables(slot, root, first, vec, NULL);
    }
}

/* collect_slotables through the slot cache once it has started, else with a counted scan. Out of line, so the plain
   scan keeps its registers. */
static TH_NOINLINE void collect_slotables_slow(HandleObject *handle, th_node *slot, nodevec *vec) {
    handle->slot_scan_wait = SLOT_SCAN_PERIOD - 1; /* a slot with nothing to scan restarts the sampling */
    th_node *root = node_root(slot);
    if (!th_node_is_shadow_root(root)) {
        return;
    }
    th_node *first = th_shadow_host(handle->tree, root)->first_child;
    if (first == NULL) {
        return;
    }
    if (slot_cache_ready(handle)) {
        handle->slot_scan_wait = 0;
        collect_slotables_cached(handle, slot, root, vec);
        return;
    }
    size_t steps = 0;
    scan_slotables(slot, root, first, vec, &steps);
    slot_note_scan(handle, steps);
}

/* Collect the slotables assigned to slot, from the slot cache or a counted scan when the handle's sampling says so,
   else from a plain scan. tree is the handle's. */
static inline void collect_assigned(HandleObject *handle, th_tree *tree, th_node *slot, nodevec *vec) {
    if (--handle->slot_scan_wait >= 0) {
        collect_slotables(tree, slot, vec);
    } else {
        collect_slotables_slow(handle, slot, vec);
    }
}

/* Collect the slotables assigned to slot, a slot of the shadow tree under root: from the handle's slot cache once it
   has started, else from index, the flattened-children walk's own index of root, or by a scan when there is none. */
static void collect_slotables_indexed(HandleObject *handle, th_node *slot, th_node *root, nodevec *vec,
                                      slot_index *index) {
    if (index == NULL || th_shadow_host(handle->tree, root)->first_child == NULL) {
        collect_assigned(handle, handle->tree, slot, vec);
        return;
    }
    if (index->root != root) {
        if (index->buckets != NULL) {
            slot_index_clear(index);
        }
        index->root = root;
        collect_assigned(handle, handle->tree, slot, vec);
        return;
    }
    if (slot_cache_ready(handle)) {
        collect_slotables_cached(handle, slot, root, vec);
        return;
    }
    if (index->buckets == NULL) {
        /* GCOVR_EXCL_BR_START: allocation failure cannot be forced from a test */
        if (slot_index_build(handle->tree, index) < 0) {
            vec->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
            return;          /* GCOVR_EXCL_LINE: allocation-failure path */
        } /* GCOVR_EXCL_LINE: closes the allocation-failure-only branch */
        /* GCOVR_EXCL_BR_STOP */
    }
    const Py_UCS4 *name = NULL;
    Py_ssize_t name_len = 0;
    named_value(slot, TH_ATTR_NAME, &name, &name_len);
    /* The FNV-1a walk runs on a keyed index too: the index holds each name once, so finding it anywhere is the answer,
       and runs under th_name_hash are short wherever the walk starts. Only a walk that ends empty there takes the keyed
       one; an index on FNV-1a always finds its slot's name, so it never does, and a slot that owns its bucket never
       reaches the test. */
    slot_bucket *bucket =
        &index->buckets[slot_index_walk(index, slot_index_home(index, name, name_len, 0), name, name_len)];
    if (bucket->slot != slot) {
        if (bucket->slot != NULL) {
            return;
        }
        bucket = &index->buckets[slot_index_find_keyed(index, name, name_len)];
        if (bucket->slot != slot) {
            return;
        }
    }
    for (Py_ssize_t position = 0; position < bucket->assigned.len; position++) {
        nodevec_push(vec, bucket->assigned.items[position].node);
    }
}

/* Collect the assigned slotables or fallback children that one slot contributes. */
static void collect_flattened_candidates(HandleObject *handle, th_node *slot, nodevec *assigned, slot_index *index) {
    th_node *root = node_root(slot);
    if (!th_node_is_shadow_root(root)) {
        return;
    }
    collect_slotables_indexed(handle, slot, root, assigned, index);
    if (assigned->failed) { /* GCOVR_EXCL_BR_LINE: nodevec fails only on allocation failure */
        return;             /* GCOVR_EXCL_LINE: allocation-failure path */
    } /* GCOVR_EXCL_LINE: closes the allocation-failure-only branch */
    if (assigned->len == 0) {
        for (th_node *child = slot->first_child; child != NULL; child = child->next_sibling) {
            if (is_slottable(child)) {
                nodevec_push(assigned, child);
            }
        }
    }
}

/* Collect flattened slotables depth first. pending is an explicit checked stack, so nested fallback slots do not
   consume the C stack. (DOM: find flattened slotables.) */
static void collect_flattened(HandleObject *handle, th_node *slot, nodevec *vec, slot_index *slots) {
    nodevec pending = {0};
    nodevec assigned = {0};
    collect_flattened_candidates(handle, slot, &assigned, slots);
    for (Py_ssize_t index = assigned.len; index > 0; index--) {
        nodevec_push(&pending, assigned.items[index - 1].node);
    }
    if (assigned.failed) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        pending.failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
    } /* GCOVR_EXCL_LINE: closes the allocation-failure-only branch */
    while (pending.len > 0) {
        if (pending.failed || vec->failed) { /* GCOVR_EXCL_BR_LINE: nodevec fails only on allocation failure */
            break;                           /* GCOVR_EXCL_LINE: allocation-failure path */
        } /* GCOVR_EXCL_LINE: closes the allocation-failure-only branch */
        th_node *node = pending.items[--pending.len].node;
        if (is_slot(node) && th_node_is_shadow_root(node_root(node))) {
            assigned.len = 0;
            collect_flattened_candidates(handle, node, &assigned, slots);
            for (Py_ssize_t index = assigned.len; index > 0; index--) {
                nodevec_push(&pending, assigned.items[index - 1].node);
            }
            if (assigned.failed) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                pending.failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
            } /* GCOVR_EXCL_LINE: closes the allocation-failure-only branch */
        } else {
            nodevec_push(vec, node);
        }
    }
    if (pending.failed) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        vec->failed = 1;  /* GCOVR_EXCL_LINE: allocation-failure path */
    } /* GCOVR_EXCL_LINE: closes the allocation-failure-only branch */
    PyMem_Free(assigned.items);
    PyMem_Free(pending.items);
}

/* Collect node's flattened-tree children: a shadow host descends into its shadow tree,
   a shadow slot yields its flattened slotables, and any child slot is replaced by its
   flattened slotables. Every other child passes through unchanged. Out of line, so the
   caller's result vector stays in memory instead of in registers saved around every call
   for a slot. */
static TH_NOINLINE void collect_flattened_children(HandleObject *handle, th_node *node, nodevec *vec) {
    if (is_slot(node) && th_node_is_shadow_root(node_root(node))) {
        collect_flattened(handle, node, vec, NULL);
        return;
    }
    th_node *shadow = th_element_shadow_root(handle->tree, node);
    th_node *base = shadow != NULL ? shadow : node;
    slot_index slots = {0};
    for (th_node *child = base->first_child; child != NULL; child = child->next_sibling) {
        if (is_slot(child) && th_node_is_shadow_root(node_root(child))) {
            collect_flattened(handle, child, vec, &slots);
        } else {
            nodevec_push(vec, child);
        }
    }
    if (slots.buckets != NULL) { /* a host with one slot never builds the index */
        slot_index_clear(&slots);
    }
}

static PyObject *nodevec_to_list(nodevec *vec, module_state *state, PyObject *handle, int elements_only) {
    if (vec->failed) {           /* GCOVR_EXCL_BR_LINE: only set on an unforceable allocation failure */
        PyMem_Free(vec->items);  /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
#if PY_VERSION_HEX >= 0x030C0000 && !defined(Py_GIL_DISABLED) && !defined(PYPY_VERSION)
    /* CPython 3.12+ defers automatic GC until bytecode evaluation resumes. */
    if (!elements_only) {
        PyObject *list = PyList_New(vec->len);
        if (list == NULL) {         /* GCOVR_EXCL_BR_LINE: allocation failure */
            PyMem_Free(vec->items); /* GCOVR_EXCL_LINE */
            return NULL;            /* GCOVR_EXCL_LINE */
        }
        for (Py_ssize_t index = 0; index < vec->len; index++) {
            PyObject *wrapped = node_wrap_locked(state, handle, vec->items[index].node);
            if (wrapped == NULL) {      /* GCOVR_EXCL_BR_LINE: allocation failure */
                Py_DECREF(list);        /* GCOVR_EXCL_LINE */
                PyMem_Free(vec->items); /* GCOVR_EXCL_LINE */
                return NULL;            /* GCOVR_EXCL_LINE */
            }
            PyList_SET_ITEM(list, index, wrapped);
        }
        PyMem_Free(vec->items);
        return list;
    }
#endif
    /* List allocation can run GC callbacks that adopt the host into another arena. */
    Py_ssize_t count = 0;
    for (Py_ssize_t index = 0; index < vec->len; index++) {
#if PY_VERSION_HEX >= 0x030C0000 && !defined(Py_GIL_DISABLED) && !defined(PYPY_VERSION)
        if (vec->items[index].node->type != TH_NODE_ELEMENT) {
#else
        if (elements_only && vec->items[index].node->type != TH_NODE_ELEMENT) {
#endif
            continue;
        }
        PyObject *wrapped = node_wrap_locked(state, handle, vec->items[index].node);
        if (wrapped == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            goto error;        /* GCOVR_EXCL_LINE */
        }
        vec->items[count++].wrapper = wrapped;
    }
    PyObject *list = PyList_New(count);
    if (list == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        goto error;     /* GCOVR_EXCL_LINE */
    }
    for (Py_ssize_t index = 0; index < count; index++) {
        PyList_SET_ITEM(list, index, vec->items[index].wrapper);
    }
    PyMem_Free(vec->items);
    return list;
    /* GCOVR_EXCL_START: allocation failure */
error:
    for (Py_ssize_t index = 0; index < count; index++) {
        Py_DECREF(vec->items[index].wrapper);
    }
    PyMem_Free(vec->items);
    return NULL;
    /* GCOVR_EXCL_STOP */
}

TH_NODE_API(, PyObject *, element_attach_shadow, (PyObject * self, PyObject *args, PyObject *kwds), (self, args, kwds),
            (PyObject * self, PyObject *args, PyObject *kwds), (NodeObject *)self,
            args != NULL && is_node(args, state_of(self)) ? (NodeObject *)args : NULL) {
    static char *keywords[] = {"mode", NULL};
    PyObject *mode_obj = NULL;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|U:attach_shadow", keywords, &mode_obj)) {
        return NULL;
    }
    int mode = 0;
    if (mode_obj != NULL) {
        if (PyUnicode_CompareWithASCIIString(mode_obj, "open") == 0) {
            mode = 0;
        } else if (PyUnicode_CompareWithASCIIString(mode_obj, "closed") == 0) {
            mode = 1;
        } else {
            PyErr_SetString(PyExc_ValueError, "mode must be 'open' or 'closed'");
            return NULL;
        }
    }
    NodeObject *host = (NodeObject *)self;
    th_tree *tree = tree_of(self);
    PyObject *result = NULL;
    int already = 0;
    Py_BEGIN_CRITICAL_SECTION(host->handle);
    if (th_element_shadow_root(tree, host->node) != NULL) {
        already = 1;
    } else {
        th_node *root = th_element_attach_shadow(tree, host->node, mode);
        if (root != NULL) { /* GCOVR_EXCL_BR_LINE: attach only fails on an unforceable allocation */
            result = node_wrap(state_of(self), host->handle, root);
        }
    }
    Py_END_CRITICAL_SECTION();
    if (already) {
        PyErr_SetString(PyExc_ValueError, "element already has a shadow root");
        return NULL;
    }
    if (result == NULL) {        /* GCOVR_EXCL_BR_LINE: attach and wrap only fail on an unforceable allocation */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return result;
}

TH_NODE_API(, PyObject *, element_get_shadow_root, (PyObject * self, void *closure), (self, closure),
            (PyObject * self, void *Py_UNUSED(closure)), (NodeObject *)self, NULL) {
    NodeObject *node = (NodeObject *)self;
    th_tree *tree = tree_of(self);
    th_node *root;
    Py_BEGIN_CRITICAL_SECTION(node->handle);
    root = th_element_shadow_root(tree, node->node);
    Py_END_CRITICAL_SECTION();
    if (root == NULL || th_shadow_mode(root) != 0) {
        Py_RETURN_NONE;
    }
    return node_wrap(state_of(self), node->handle, root);
}

/* Shared body of assigned_nodes / assigned_elements: reject a non-slot, then collect
   the direct (or, with flatten, flattened) assignment and wrap it. */
static PyObject *slot_assigned(PyObject *self, PyObject *args, PyObject *kwds, int elements_only) {
    static char *keywords[] = {"flatten", NULL};
    int flatten = 0;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|p:assigned_nodes", keywords, &flatten)) {
        return NULL;
    }
    NodeObject *node = (NodeObject *)self;
    if (!is_slot(node->node)) {
        PyErr_SetString(PyExc_TypeError, "assigned_nodes is only valid on a <slot> element");
        return NULL;
    }
    nodevec vec = {0};
    Py_BEGIN_CRITICAL_SECTION(node->handle);
    if (flatten) {
        collect_flattened((HandleObject *)node->handle, node->node, &vec, NULL);
    } else {
        collect_assigned((HandleObject *)node->handle, tree_of(self), node->node, &vec);
    }
    Py_END_CRITICAL_SECTION();
    return nodevec_to_list(&vec, state_of(self), node->handle, elements_only);
}

TH_NODE_API(, PyObject *, element_assigned_nodes, (PyObject * self, PyObject *args, PyObject *kwds), (self, args, kwds),
            (PyObject * self, PyObject *args, PyObject *kwds), (NodeObject *)self,
            args != NULL && is_node(args, state_of(self)) ? (NodeObject *)args : NULL) {
    return slot_assigned(self, args, kwds, 0);
}

TH_NODE_API(, PyObject *, element_assigned_elements, (PyObject * self, PyObject *args, PyObject *kwds),
            (self, args, kwds), (PyObject * self, PyObject *args, PyObject *kwds), (NodeObject *)self,
            args != NULL && is_node(args, state_of(self)) ? (NodeObject *)args : NULL) {
    return slot_assigned(self, args, kwds, 1);
}

TH_NODE_API(, PyObject *, node_get_assigned_slot, (PyObject * self, void *closure), (self, closure),
            (PyObject * self, void *Py_UNUSED(closure)), (NodeObject *)self, NULL) {
    NodeObject *node = (NodeObject *)self;
    th_node *slot = NULL;
    int status = 0;
    Py_BEGIN_CRITICAL_SECTION(node->handle);
    if (is_slottable(node->node)) {
        status = find_slot((HandleObject *)node->handle, node->node, &slot);
    }
    Py_END_CRITICAL_SECTION();
    if (status < 0) {            /* GCOVR_EXCL_BR_LINE: the slot table only fails on an unforceable allocation */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return node_wrap(state_of(self), node->handle, slot);
}

TH_NODE_API(, PyObject *, node_get_flattened_children, (PyObject * self, void *closure), (self, closure),
            (PyObject * self, void *Py_UNUSED(closure)), (NodeObject *)self, NULL) {
    NodeObject *node = (NodeObject *)self;
    nodevec vec = {0};
    Py_BEGIN_CRITICAL_SECTION(node->handle);
    collect_flattened_children((HandleObject *)node->handle, node->node, &vec);
    Py_END_CRITICAL_SECTION();
    return nodevec_to_list(&vec, state_of(self), node->handle, 0);
}

PyDoc_STRVAR(shadow_root_mode_doc, "the shadow root's mode: 'open' or 'closed'");
PyDoc_STRVAR(shadow_root_host_doc, "the Element this shadow root is attached to");
PyDoc_STRVAR(shadow_root_delegates_focus_doc,
             "whether the shadow root delegates focus, from a declarative shadow root's\n"
             "shadowrootdelegatesfocus attribute (always False otherwise)");
PyDoc_STRVAR(shadow_root_clonable_doc, "whether the shadow root is clonable, from a declarative shadow root's\n"
                                       "shadowrootclonable attribute (always False otherwise)");

TH_NODE_API(static, PyObject *, shadow_root_get_mode, (PyObject * self, void *closure), (self, closure),
            (PyObject * self, void *Py_UNUSED(closure)), (NodeObject *)self, NULL) {
    return PyUnicode_FromString(th_shadow_mode(((NodeObject *)self)->node) != 0 ? "closed" : "open");
}

TH_NODE_API(static, PyObject *, shadow_root_get_delegates_focus, (PyObject * self, void *closure), (self, closure),
            (PyObject * self, void *Py_UNUSED(closure)), (NodeObject *)self, NULL) {
    return PyBool_FromLong((((NodeObject *)self)->node->tag_flags & TH_SHADOW_DELEGATES_FOCUS) != 0);
}

TH_NODE_API(static, PyObject *, shadow_root_get_clonable, (PyObject * self, void *closure), (self, closure),
            (PyObject * self, void *Py_UNUSED(closure)), (NodeObject *)self, NULL) {
    return PyBool_FromLong((((NodeObject *)self)->node->tag_flags & TH_SHADOW_CLONABLE) != 0);
}

TH_NODE_API(static, PyObject *, shadow_root_get_host, (PyObject * self, void *closure), (self, closure),
            (PyObject * self, void *Py_UNUSED(closure)), (NodeObject *)self, NULL) {
    NodeObject *node = (NodeObject *)self;
    th_tree *tree = tree_of(self);
    th_node *host;
    Py_BEGIN_CRITICAL_SECTION(node->handle);
    host = th_shadow_host(tree, node->node);
    Py_END_CRITICAL_SECTION();
    return node_wrap(state_of(self), node->handle, host);
}

static PyGetSetDef shadow_root_getset[] = {
    {"mode", shadow_root_get_mode, NULL, shadow_root_mode_doc, NULL},
    {"host", shadow_root_get_host, NULL, shadow_root_host_doc, NULL},
    {"delegates_focus", shadow_root_get_delegates_focus, NULL, shadow_root_delegates_focus_doc, NULL},
    {"clonable", shadow_root_get_clonable, NULL, shadow_root_clonable_doc, NULL},
    {NULL, NULL, NULL, NULL, NULL},
};

PyDoc_STRVAR(shadow_root_set_inner_html_doc,
             "set_inner_html(html, /)\n--\n\n"
             "Replace the shadow tree's content by parsing html as a fragment, the way\n"
             "declarative shadow DOM populates a shadow root.\n\n"
             ":param html: the markup to parse and install as the shadow content.\n"
             ":raises TypeError: if html is not a str.");

TH_NODE_API(static, PyObject *, shadow_root_set_inner_html, (PyObject * self, PyObject *html), (self, html),
            (PyObject * self, PyObject *html), (NodeObject *)self,
            html != NULL && is_node(html, state_of(self)) ? (NodeObject *)html : NULL) {
    if (!PyUnicode_Check(html)) {
        PyErr_SetString(PyExc_TypeError, "html must be a str");
        return NULL;
    }
    int scripting = th_tree_scripting(tree_of(self));
    th_tree *fragment = th_tree_parse_fragment(PyUnicode_KIND(html), PyUnicode_DATA(html), PyUnicode_GET_LENGTH(html),
                                               "div", 3, 0, 0, scripting, 1);
    if (fragment == NULL) {      /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_node *root = ((NodeObject *)self)->node;
    th_tree *dest = tree_of(self);
    int error = 0;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    handle_drop_index(((NodeObject *)self)->handle);
    while (root->first_child != NULL) {
        th_node_remove_silent(dest, root->first_child);
    }
    th_tree_note_deepened(dest);
    th_copy_memo memo = {0}; /* one for all the top-level nodes, so attribute values they share are copied once */
    for (th_node *child = th_tree_document(fragment)->first_child; child != NULL; child = child->next_sibling) {
        th_node *copy = th_tree_copy_node_sharing(dest, fragment, child, &memo);
        if (copy == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            error = 1;      /* GCOVR_EXCL_LINE: allocation-failure path */
            break;          /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        th_node_append_child(root, copy);
    }
    th_copy_memo_free(&memo);
    Py_END_CRITICAL_SECTION();
    th_tree_free(fragment);
    if (error) {                 /* GCOVR_EXCL_BR_LINE: the copy only fails on an unforceable allocation */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_RETURN_NONE;
}

PyDoc_STRVAR(shadow_root_append_doc, "append(child, /)\n--\n\n"
                                     "Add child as the last node of the shadow tree, moving a node from this tree\n"
                                     "or adopting one from another by copy, like Element.append. A DocumentFragment\n"
                                     "argument moves its children in and is left empty.");

static PyMethodDef shadow_root_methods[] = {
    /* listed again so the reference documents it on ShadowRoot as well as on DocumentFragment */
    {"append", node_append_child, METH_O, shadow_root_append_doc},
    {"set_inner_html", shadow_root_set_inner_html, METH_O, shadow_root_set_inner_html_doc},
    {NULL, NULL, 0, NULL},
};

PyDoc_STRVAR(shadow_root_doc, "A shadow root: the DocumentFragment rooting an element's shadow tree, created\n"
                              "by Element.attach_shadow. It is held off the light tree, so it never appears\n"
                              "among the host's children or in its serialization. Inserting it elsewhere moves\n"
                              "its children, as for any DocumentFragment, and leaves it attached to its host.");

static PyType_Slot shadow_root_slots[] = {
    {Py_tp_doc, (void *)shadow_root_doc},
    {Py_tp_getset, shadow_root_getset},
    {Py_tp_methods, shadow_root_methods},
    TH_SEALED_END,
};

PyDoc_STRVAR(document_fragment_append_doc,
             "append(child, /)\n--\n\n"
             "Add child as the last node of this fragment, moving a node from this tree or\n"
             "adopting one from another by copy, like Element.append. A DocumentFragment\n"
             "argument moves its children in and is left empty.\n\n"
             ":param child: the node to append.\n"
             ":raises TypeError: if child is not a node, or is a Document.\n"
             ":raises ValueError: if child contains this fragment (which would form a cycle), or\n"
             "    is a doctype.");

static PyMethodDef document_fragment_methods[] = {
    {"append", node_append_child, METH_O, document_fragment_append_doc},
    {NULL, NULL, 0, NULL},
};

/* DocumentFragment(): an empty fragment in its own tree, ready to collect nodes and be inserted as a batch. */
static PyObject *document_fragment_new(PyTypeObject *type, PyObject *args, PyObject *kwds) {
    static char *keywords[] = {NULL};
    if (!PyArg_ParseTupleAndKeywords(args, kwds, ":DocumentFragment", keywords)) {
        return NULL;
    }
    th_tree *tree = th_tree_new();
    th_node *fragment = tree == NULL ? NULL : th_tree_make_fragment(tree); /* GCOVR_EXCL_BR_LINE: OOM only */
    if (fragment == NULL) {      /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(tree);      /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return wrap_fresh_tree_node(PyType_GetModuleState(type), tree, fragment);
}

PyDoc_STRVAR(document_fragment_doc,
             "DocumentFragment()\n--\n\n"
             "A parentless container of nodes (the DOM DocumentFragment). Inserting one with\n"
             "append, insert_before, insert_after, replace_with, extend, or Range.insert_node\n"
             "moves its children into place, in order, and leaves it empty. Range.extract_contents\n"
             "and Range.clone_contents return one; ShadowRoot is a DocumentFragment with a host.");

static PyType_Slot document_fragment_slots[] = {
    {Py_tp_doc, (void *)document_fragment_doc},
    {Py_tp_new, document_fragment_new},
    {Py_tp_methods, document_fragment_methods},
    {0, NULL},
};

PyType_Spec document_fragment_spec = {
    .name = "turbohtml._html.DocumentFragment",
    .basicsize = sizeof(NodeObject),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    .slots = document_fragment_slots,
};

PyType_Spec shadow_root_spec = {
    .name = "turbohtml._html.ShadowRoot",
    .basicsize = sizeof(NodeObject),
    .flags = Py_TPFLAGS_DEFAULT | TH_SEALED,
    .slots = shadow_root_slots,
};
