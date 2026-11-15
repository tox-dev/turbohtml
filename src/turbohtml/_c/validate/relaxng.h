/* RELAX NG (XML syntax) validation by James Clark's derivative algorithm ("An
   algorithm for RELAX NG validation"). The schema compiles to a pattern algebra --
   element, attribute, group, choice, interleave, oneOrMore, text, empty, data, value,
   list, ref -- and validation takes the derivative of the pattern with respect to each
   start tag, attribute, text run, and end tag. Smart constructors absorb notAllowed and
   empty so the pattern stays small; interleave and the repetition operators fall out of
   the derivative without any backtracking. Included once into validate/schema.c; every
   definition is static. */

#ifndef TURBOHTML_VALIDATE_RELAXNG_H
#define TURBOHTML_VALIDATE_RELAXNG_H

#include "core/work_steps.h"

/* The step bodies inline into the hot walk and into their memo functions. A debug build keeps one copy, as
   CSS_FORCEINLINE does, because gcc counts a body's branches once per copy and the coverage gate needs them all. */
#if defined(_MSC_VER)
#define RNG_FORCEINLINE __forceinline
#elif defined(__OPTIMIZE__)
#define RNG_FORCEINLINE inline __attribute__((always_inline))
#else
#define RNG_FORCEINLINE inline
#endif

/* The memo helpers keep their keys out of the recursive frames, so a level of the walk costs what it cost before. */
#if defined(_MSC_VER)
#define RNG_NOINLINE __declspec(noinline)
#elif defined(__GNUC__) || defined(__clang__)
#define RNG_NOINLINE __attribute__((noinline))
#else
#define RNG_NOINLINE
#endif

enum {
    P_EMPTY,
    P_NOTALLOWED,
    P_TEXT,
    P_CHOICE,
    P_INTERLEAVE,
    P_GROUP,
    P_ONEMORE,
    P_ELEMENT,
    P_ATTRIBUTE,
    P_DATA,
    P_VALUE,
    P_LIST,
    P_AFTER,
    P_REF,
};

enum { NC_ANY, NC_NAME, NC_NS, NC_CHOICE, NC_EXCEPT };

typedef struct nameclass {
    int type;
    const Py_UCS4 *uri;
    Py_ssize_t uri_len;
    const Py_UCS4 *local;
    Py_ssize_t local_len;
    struct nameclass *a, *b;
    uint64_t hash; /* NC_NAME only: nc_name_hash, so the name sets never rehash a long name */
    uint64_t seq;  /* wildcard only: the treap node count when the class was made, a run-stable identity */
} nameclass;

struct nsnode {
    const nameclass *name;
    struct nsnode *left, *right;
    Py_ssize_t size; /* nodes in this treap; equal treaps share one node, so one size */
    uint64_t id;     /* creation order, so table probes depend on the input and not on the heap address */
};

struct pattern {
    int type;
    int nullable;
    pattern *p1, *p2;
    /* A choice built during validation is a node of a binary trie over its leaves' creation numbers (see trie_union)
       and keeps its trie fields where the patterns that name or match text keep theirs. */
    union {
        nameclass *nc;
        pattern *leaf; /* choice: one of its leaves, whose number carries the bits all of them share */
    };
    union {
        int def_index;
        int bit; /* choice: the highest bit in which a leaf number of p1 differs from one of p2 */
    };
    int datatype_id;
    int value_ws;
    uint32_t seq; /* creation number within the compile or the validation, so a trie's shape follows the input alone */
    facetset *facets;
    const Py_UCS4 *value;
    union {
        Py_ssize_t value_len;
        Py_ssize_t leaves; /* choice: how many leaves it holds; 0 until a merge first reads it */
    };
};

/* Per-validation hash-consing: a structurally identical derivative shares one node, so a choice drops a duplicate
   branch by pointer and the residual stays bounded on an ambiguous schema (Clark, "Avoiding exponential blowup"; jing
   PatternInterner, MSV ExpressionPool). */
typedef struct patintern {
    pattern **slots;
    size_t cap; /* power of two, 0 until first grow */
    size_t count;
    unsigned shift; /* 64 - log2(cap): the slot is the hash's top bits, where the multiply mixes every operand bit */
} patintern;

static RNG_FORCEINLINE pattern *pat_new(th_schema *schema, int type) {
    pattern *pattern = arena_alloc(&schema->mem, sizeof(*pattern));
    if (pattern == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return NULL;       /* GCOVR_EXCL_LINE */
    }
    memset(pattern, 0, sizeof(*pattern));
    pattern->type = type;
    pattern->seq = (uint32_t)schema->pattern_count++;
    pattern->nullable =
        type == P_REF || type == P_CHOICE || type == P_GROUP || type == P_INTERLEAVE || type == P_ONEMORE
            ? -1
            : type == P_EMPTY || type == P_TEXT;
    return pattern;
}

static pattern *intern_find(const patintern *table, int type, pattern *p1, pattern *p2);
static void intern_insert(th_schema *schema, patintern *table, pattern *node);

static pattern *pat_binary(th_schema *schema, int type, pattern *p1, pattern *p2) {
    if (schema->intern != NULL) {
        pattern *found = intern_find(schema->intern, type, p1, p2);
        if (found != NULL) {
            return found;
        }
    }
    pattern *node = pat_new(schema, type);
    if (node == NULL) {              /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return schema->p_notallowed; /* GCOVR_EXCL_LINE */
    }
    node->p1 = p1;
    node->p2 = p2;
    if (type != P_AFTER && p1->nullable >= 0 && p2->nullable >= 0) {
        node->nullable = type == P_CHOICE ? p1->nullable || p2->nullable : p1->nullable && p2->nullable;
    }
    if (schema->intern != NULL) {
        intern_insert(schema, schema->intern, node);
    }
    return node;
}

static uint64_t pat_key_hash(int type, pattern *p1, pattern *p2);

static pattern *intern_find(const patintern *table, int type, pattern *p1, pattern *p2) {
    if (table->cap == 0) {
        return NULL;
    }
    size_t slot = (size_t)(pat_key_hash(type, p1, p2) >> table->shift);
    while (table->slots[slot] != NULL) {
        pattern *node = table->slots[slot];
        if (node->type == type && node->p1 == p1 && node->p2 == p2) {
            return node;
        }
        slot = (slot + 1) & (table->cap - 1);
    }
    return NULL;
}

/* Arena pointers share their high and low bits, so p2 is rotated onto the half of p1 that varies, and one Fibonacci
   multiply spreads the key into the top bits the slot takes. The probe lengths no longer follow the heap layout. */
static uint64_t pat_key_hash(int type, pattern *p1, pattern *p2) {
    uint64_t key = (uint64_t)(uintptr_t)p1 ^ (((uint64_t)(uintptr_t)p2 << 32) | ((uint64_t)(uintptr_t)p2 >> 32)) ^
                   (uint64_t)(unsigned)type;
    return key * UINT64_C(0x9E3779B97F4A7C15);
}

static void intern_insert(th_schema *schema, patintern *table, pattern *node) {
    if (table->cap == 0 || table->count * 2 >= table->cap) {
        size_t cap = table->cap ? table->cap * 2 : 64;
        unsigned shift = table->cap ? table->shift - 1 : 58;
        pattern **slots = arena_alloc(&schema->mem, cap * sizeof(pattern *));
        if (slots == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return;          /* GCOVR_EXCL_LINE: skipping the insert only loses dedup, not correctness */
        }
        memset(slots, 0, cap * sizeof(pattern *));
        for (size_t index = 0; index < table->cap; index++) {
            pattern *existing = table->slots[index];
            if (existing == NULL) {
                continue;
            }
            size_t slot = (size_t)(pat_key_hash(existing->type, existing->p1, existing->p2) >> shift);
            while (slots[slot] != NULL) {
                slot = (slot + 1) & (cap - 1);
            }
            slots[slot] = existing;
        }
        table->slots = slots;
        table->cap = cap;
        table->shift = shift;
    }
    size_t slot = (size_t)(pat_key_hash(node->type, node->p1, node->p2) >> table->shift);
    while (table->slots[slot] != NULL) {
        slot = (slot + 1) & (table->cap - 1);
    }
    table->slots[slot] = node;
    table->count++;
}

/* Each path that reaches a shared node re-derives it, so the memo keeps one result per node for each event: a start
   tag name, an attribute, a start tag close or a text run. An event has a single name, and a list token is kept apart
   from the text around it by its address and length, so a node and a value together name one result. */
enum { MEMO_NULLABLE, MEMO_TEXT, MEMO_OPEN, MEMO_ATT, MEMO_CLOSE };

/* A probe costs more than a plain step, so an event walks its first steps as a tree. */
#ifdef FUZZING_BUILD_MODE_UNSAFE_FOR_PRODUCTION
/* With no free steps the memo engages on the first step, so the fuzzing build exercises it on small schemas. */
#define MEMO_FREE_STEPS 0
#else
#define MEMO_FREE_STEPS 1024
#endif

/* Leaves a choice built during validation may hold for each element of the schema, and at least the floor. A residual
   with two levels of nested ambiguity stays under it; one that doubles with each define passes it within a few. */
#define RNG_CHOICE_PER_NODE 24
#define RNG_CHOICE_FLOOR 256

/* The schema document holds each define once, including those a grammar builds only during validation. The first
   merge wide enough to need the limit computes it, so a validation that never builds a wide choice pays nothing. */
static RNG_NOINLINE Py_ssize_t rng_choice_limit(th_schema *schema) {
    if (schema->choice_limit == 0) {
        Py_ssize_t nodes = schema_count_elements(th_tree_document(schema->tree));
        schema->choice_limit =
            nodes * RNG_CHOICE_PER_NODE > RNG_CHOICE_FLOOR ? nodes * RNG_CHOICE_PER_NODE : RNG_CHOICE_FLOOR;
    }
    return schema->choice_limit;
}

/* Work units an event may spend for each alternative the choice limit allows. A merge costs a unit for each trie node
   it visits and a memoized derivative costs one, so an event stays within a multiple of the schema size even when its
   residual holds many nodes that each stay under the choice limit, or merges a wide choice once per place. The nested
   optional places that stay under the choice limit spend up to five units per alternative. */
#define RNG_EVENT_WORK_PER_LEAF 8

static RNG_NOINLINE int rng_event_over(th_schema *schema) {
    if (schema->event_work <= rng_choice_limit(schema) * RNG_EVENT_WORK_PER_LEAF) {
        return 0;
    }
    schema->choice_limit = -1;
    schema->mem.no_memory = 1;
    return 1;
}

/* Charges the event in progress; true once it has spent more than the schema allows, which marks the arena like a
   merge past the choice limit. Under the floor the limit cannot be reached, so the common event never computes it. */
static RNG_FORCEINLINE int rng_event_spend(th_schema *schema, Py_ssize_t units) {
    schema->event_work += units;
    return schema->event_work > (Py_ssize_t)RNG_CHOICE_FLOOR * RNG_EVENT_WORK_PER_LEAF && rng_event_over(schema);
}

typedef struct memo_key {
    pattern *p;
    const Py_UCS4 *value;
    Py_ssize_t len;
    const Py_UCS4 *name; /* the element or attribute name a start tag or attribute derivative is taken for */
    size_t op;
} memo_key;

typedef struct memo_entry {
    memo_key key;
    pattern *derived;
    size_t event; /* a slot counts as taken only while its event number matches the memo's current one */
} memo_entry;

typedef struct patmemo {
    memo_entry *slots;
    size_t cap;   /* power of two, 0 until first grow */
    size_t count; /* live entries; zeroed when the event number advances */
    size_t event;
    pattern *stopped; /* NotAllowed once the validation ran past its work limit, the answer to every later lookup */
} patmemo;

/* Moving to a new event number frees every slot without clearing them. */
static void memo_reset(patmemo *memo) {
    memo->event++;
    memo->count = 0;
}

/* An event that stays within its free steps never reads the memo, so the memo is emptied only when it is needed. */
static void memo_begin_event(th_schema *schema) {
    schema->memo_budget = schema->memo_free;
    schema->event_work = 0;
}

/* Every derivative call takes one step off the budget, so only the first call past the free steps sees -1. The memo
   still holds an earlier event's entries until then; nullability holds across events, the other entries do not. A list
   token engages only the token memo, so the text that holds the list keeps its entries; each token empties the token
   memo before it starts. */
static void memo_engage(th_schema *schema) {
    if (schema->memo_budget == -1) {
        memo_reset(schema->memo);
    }
}

/* Slots come from the low bits, which a multiply computes from the input's low bits alone. Arena addresses have
   none there, so the hash folds the high half down first. */
static uint64_t memo_key_hash(memo_key key) {
    uint64_t hash = UINT64_C(1469598103934665603);
    hash = (hash ^ (uint64_t)key.op) * UINT64_C(1099511628211);
    hash = (hash ^ (uint64_t)(uintptr_t)key.p) * UINT64_C(1099511628211);
    hash = (hash ^ (uint64_t)(uintptr_t)key.value) * UINT64_C(1099511628211);
    hash = (hash ^ (uint64_t)key.len) * UINT64_C(1099511628211);
    hash = (hash ^ (uint64_t)(uintptr_t)key.name) * UINT64_C(1099511628211);
    return hash ^ (hash >> 32);
}

/* One comparison of all five fields, so a probe that meets another key takes one branch, not one per field. */
static RNG_FORCEINLINE int memo_key_equal(memo_key stored, memo_key key) {
    return ((stored.op ^ key.op) | ((uintptr_t)stored.p ^ (uintptr_t)key.p) |
            ((uintptr_t)stored.value ^ (uintptr_t)key.value) | ((size_t)stored.len ^ (size_t)key.len) |
            ((uintptr_t)stored.name ^ (uintptr_t)key.name)) == 0;
}

static RNG_NOINLINE pattern *memo_find(const patmemo *memo, size_t op, pattern *p, const Py_UCS4 *value, Py_ssize_t len,
                                       const Py_UCS4 *name) {
    memo_key key = {p, value, len, name, op};
    if (memo->stopped != NULL) {
        return memo->stopped;
    }
    if (memo->cap == 0) {
        return NULL;
    }
    size_t slot = (size_t)memo_key_hash(key) & (memo->cap - 1);
    while (memo->slots[slot].event == memo->event) {
        if (memo_key_equal(memo->slots[slot].key, key)) {
            return memo->slots[slot].derived;
        }
        slot = (slot + 1) & (memo->cap - 1);
    }
    return NULL;
}

static void memo_insert(const patmemo *memo, memo_entry *slots, size_t cap, memo_key key, pattern *derived) {
    size_t slot = (size_t)memo_key_hash(key) & (cap - 1);
    while (slots[slot].event == memo->event) {
        slot = (slot + 1) & (cap - 1);
    }
    slots[slot] = (memo_entry){key, derived, memo->event};
}

/* Growing is rare, so it stays out of line and the insert path inlines into the derivative walk. */
static RNG_NOINLINE int memo_grow(th_schema *schema) {
    patmemo *memo = schema->memo;
    size_t cap = memo->cap ? memo->cap * 2 : 64;
    memo_entry *slots = arena_alloc(&schema->mem, cap * sizeof(memo_entry));
    if (slots == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return 0;        /* GCOVR_EXCL_LINE: without the grow the entry is not cached; that costs time, not a result */
    }
    memset(slots, 0, cap * sizeof(memo_entry));
    for (size_t index = 0; index < memo->cap; index++) {
        const memo_entry *existing = &memo->slots[index];
        if (existing->event == memo->event) {
            memo_insert(memo, slots, cap, existing->key, existing->derived);
        }
    }
    memo->slots = slots;
    memo->cap = cap;
    return 1;
}

static RNG_NOINLINE void memo_add(th_schema *schema, size_t op, pattern *p, const Py_UCS4 *value, Py_ssize_t len,
                                  const Py_UCS4 *name, pattern *derived) {
    /* A walk that only revisits cached nodes adds nothing, so each new entry is one unit of the event's work. Past the
       limit both memos answer NotAllowed, so the derivatives still in progress stop at their next lookup. */
    if (rng_event_spend(schema, 1)) {
        schema->event_memo->stopped = schema->token_memo->stopped = schema->p_notallowed;
        return;
    }
    memo_key key = {p, value, len, name, op};
    patmemo *memo = schema->memo;
    if (memo->count * 2 >= memo->cap && !memo_grow(schema)) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return;                                               /* GCOVR_EXCL_LINE: skipping the insert loses the memo */
    }
    memo_insert(memo, memo->slots, memo->cap, key, derived);
    memo->count++;
}

/* The patterns a derivative answers without recursing: a leaf, or an element or attribute the derivative does not
   enter. The wrapper answers them before the step budget, so they cost no step and no stack frame: the parent that
   reached one already paid a step for it. */
#define RNG_LEAF_TYPES                                                                                                 \
    ((1u << P_EMPTY) | (1u << P_NOTALLOWED) | (1u << P_TEXT) | (1u << P_DATA) | (1u << P_VALUE) | (1u << P_LIST))
#define RNG_TEXT_REJECTS ((1u << P_EMPTY) | (1u << P_NOTALLOWED) | (1u << P_ELEMENT) | (1u << P_ATTRIBUTE))
#define RNG_OPEN_REJECTS (RNG_LEAF_TYPES | (1u << P_ATTRIBUTE))
#define RNG_ATT_REJECTS (RNG_LEAF_TYPES | (1u << P_ELEMENT))
#define RNG_CLOSE_KEEPS (RNG_LEAF_TYPES | (1u << P_ELEMENT))

static RNG_NOINLINE pattern *pat_choice_merge(th_schema *schema, pattern *p1, pattern *p2);

/* Smart constructors: absorb notAllowed / empty so derivatives stay bounded. */
static pattern *pat_choice(th_schema *schema, pattern *p1, pattern *p2) {
    if (p1->type == P_NOTALLOWED) {
        return p2;
    }
    if (p2->type == P_NOTALLOWED) {
        return p1;
    }
    if (schema->intern == NULL) { /* compiling: interning only exists during validation */
        return pat_binary(schema, P_CHOICE, p1, p2);
    }
    /* hot path: two leaves split on their highest differing bit, which puts the lower number in p1, so this is the
       trie node a merge would build; the merge fills in its trie fields if it ever reads it */
    if (p1->type != P_CHOICE && p2->type != P_CHOICE) {
        if (p1 == p2) {
            return p1;
        }
        return p1->seq < p2->seq ? pat_binary(schema, P_CHOICE, p1, p2) : pat_binary(schema, P_CHOICE, p2, p1);
    }
    return pat_choice_merge(schema, p1, p2);
}

typedef pattern *(*pat_join_fn)(th_schema *schema, pattern *p1, pattern *p2);

/* Up to this many operands join as a chain: the shape common content models keep, and the one a choice grows by a
   single node whose tail it shares with the choice it grew from. */
#define RNG_CHAIN_JOIN 8

/* Join `len` operands of one associative operator, in order: a few as a left-leaning chain, more as a balanced tree.
   The derivatives recurse once per pattern level, so a chain as deep as a long operand list would let a wide container
   or a choice with many live alternatives overflow the stack. Overwrites `parts`. */
static pattern *pat_fold(th_schema *schema, pat_join_fn join, pattern **parts, Py_ssize_t len) {
    if (len <= RNG_CHAIN_JOIN) {
        pattern *joined = parts[0];
        for (Py_ssize_t index = 1; index < len; index++) {
            joined = join(schema, joined, parts[index]);
        }
        return joined;
    }
    while (len > 1) {
        Py_ssize_t out = 0;
        for (Py_ssize_t index = 0; index + 1 < len; index += 2) {
            parts[out++] = join(schema, parts[index], parts[index + 1]);
        }
        if (len & 1) {
            parts[out++] = parts[len - 1];
        }
        len = out;
    }
    return parts[0];
}

#if defined(_MSC_VER)
#include <intrin.h>
static RNG_FORCEINLINE int trie_high_bit(uint64_t bits) {
    unsigned long index;
    _BitScanReverse64(&index, bits);
    return (int)index;
}
#else
#define trie_high_bit(bits) (63 - __builtin_clzll(bits))
#endif

/* A choice built during validation is a binary trie over the creation numbers of its leaves, the non-choice patterns
   it holds (Okasaki and Gill, "Fast Mergeable Integer Maps"): a node splits its leaves on the highest bit in which
   their numbers differ, p1 holding those with a 0 there. One leaf set has one shape, so hash-consing makes it one node,
   and two choices merge by walking only where their leaves interleave: adding one leaf to a choice of n rebuilds a
   path of at most 32 nodes and shares the rest, where a sorted list would rebuild all n. The trie is at most 32 levels
   deep, so the derivatives that recurse into it stay off the deep stack a long chain would take. */
static RNG_FORCEINLINE uint64_t trie_key(const pattern *p) {
    return (p->type == P_CHOICE ? p->leaf : p)->seq;
}

static RNG_FORCEINLINE Py_ssize_t trie_size(const pattern *p) {
    return p->type == P_CHOICE ? p->leaves : 1;
}

/* Joins two tries whose leaves differ above both of their split bits. A two-leaf choice the hot path in pat_choice
   built is this node already, so the join only fills in its trie fields. */
static pattern *trie_join(th_schema *schema, pattern *p, pattern *q) {
    int bit = trie_high_bit(trie_key(p) ^ trie_key(q));
    if ((trie_key(p) >> bit) & 1) {
        pattern *swap = p;
        p = q;
        q = swap;
    }
    pattern *node = pat_binary(schema, P_CHOICE, p, q);
    if (node->type == P_CHOICE && node->leaves == 0) { /* GCOVR_EXCL_BR_LINE: only arena OOM yields NotAllowed */
        node->leaf = p->type == P_CHOICE ? p->leaf : p;
        node->bit = bit;
        node->leaves = trie_size(p) + trie_size(q);
    }
    return node;
}

/* Each call is one unit of `work`, which the caller charges to the event. */
static pattern *trie_union(th_schema *schema, pattern *s, pattern *t, Py_ssize_t *work) {
    ++*work;
    if (s == t) {
        return s;
    }
    int s_bit = s->type == P_CHOICE ? s->bit : -1;
    int t_bit = t->type == P_CHOICE ? t->bit : -1;
    if (s_bit < t_bit) {
        pattern *swap = s;
        s = t;
        t = swap;
        int swap_bit = s_bit;
        s_bit = t_bit;
        t_bit = swap_bit;
    }
    uint64_t shared = trie_key(s) ^ trie_key(t);
    if (s_bit < 0 || (shared >> s_bit) > 1) { /* two leaves, or t lies outside the bits s's leaves share */
        return trie_join(schema, s, t);
    }
    if (s_bit == t_bit) {
        return trie_join(schema, trie_union(schema, s->p1, t->p1, work), trie_union(schema, s->p2, t->p2, work));
    }
    if ((trie_key(t) >> s_bit) & 1) {
        return trie_join(schema, s->p1, trie_union(schema, s->p2, t, work));
    }
    return trie_join(schema, trie_union(schema, s->p1, t, work), s->p2);
}

/* A two-leaf choice the hot path built becomes a trie the first time a merge meets it; a choice compiled from the
   schema is not interned, so it is rebuilt as a trie each time. */
static pattern *trie_of(th_schema *schema, pattern *p, Py_ssize_t *work) {
    if (p->type != P_CHOICE || p->leaves > 0) {
        return p;
    }
    return trie_union(schema, trie_of(schema, p->p1, work), trie_of(schema, p->p2, work), work);
}

/* The memo derives each node once per event, but a content model whose places double with each define still leaves a
   residual choice with one leaf per place. A merge is the only step that grows a choice past two leaves, so past the
   limit it yields NotAllowed and marks the arena, and validate() raises; a limit of -1 marks that this happened. A
   merge also charges the event its work, which bounds what one event spends on choices that each stay under the
   limit. */
static RNG_NOINLINE pattern *pat_choice_merge(th_schema *schema, pattern *p1, pattern *p2) {
    Py_ssize_t work = 0;
    pattern *merged = trie_union(schema, trie_of(schema, p1, &work), trie_of(schema, p2, &work), &work);
    TH_WORK_STEPS_ADD(work);
    if (rng_event_spend(schema, work)) {
        return schema->p_notallowed;
    }
    /* the limit is never below RNG_CHOICE_FLOOR, so a smaller choice needs no check */
    if (trie_size(merged) > RNG_CHOICE_FLOOR && trie_size(merged) > rng_choice_limit(schema)) {
        schema->choice_limit = -1;
        schema->mem.no_memory = 1;
        return schema->p_notallowed;
    }
    return merged;
}

static pattern *pat_group(th_schema *schema, pattern *p1, pattern *p2) {
    if (p1->type == P_NOTALLOWED || p2->type == P_NOTALLOWED) {
        return schema->p_notallowed;
    }
    if (p1->type == P_EMPTY) {
        return p2;
    }
    if (p2->type == P_EMPTY) {
        return p1;
    }
    return pat_binary(schema, P_GROUP, p1, p2);
}

static pattern *pat_interleave(th_schema *schema, pattern *p1, pattern *p2) {
    if (p1->type == P_NOTALLOWED || p2->type == P_NOTALLOWED) {
        return schema->p_notallowed;
    }
    if (p1->type == P_EMPTY) {
        return p2;
    }
    if (p2->type == P_EMPTY) {
        return p1;
    }
    return pat_binary(schema, P_INTERLEAVE, p1, p2);
}

static pattern *pat_after(th_schema *schema, pattern *p1, pattern *p2) {
    if (p1->type == P_NOTALLOWED) {
        return schema->p_notallowed;
    }
    /* a NotAllowed continuation is absorbed by the group/choice constructor that builds
       p2 before it reaches here, so this guard is defensive symmetry with the p1 case */
    if (p2->type == P_NOTALLOWED) {  /* GCOVR_EXCL_BR_LINE */
        return schema->p_notallowed; /* GCOVR_EXCL_LINE */
    }
    return pat_binary(schema, P_AFTER, p1, p2);
}

static pattern *pat_onemore(th_schema *schema, pattern *p1) {
    if (p1->type == P_NOTALLOWED) {
        return schema->p_notallowed;
    }
    if (schema->intern != NULL) {
        pattern *found = intern_find(schema->intern, P_ONEMORE, p1, NULL);
        if (found != NULL) {
            return found;
        }
    }
    pattern *node = pat_new(schema, P_ONEMORE);
    if (node == NULL) {              /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return schema->p_notallowed; /* GCOVR_EXCL_LINE */
    }
    node->p1 = p1;
    node->nullable = p1->nullable;
    if (schema->intern != NULL) {
        intern_insert(schema, schema->intern, node);
    }
    return node;
}

/* ---- pattern compilation ---- */

static pattern *rng_resolve(th_schema *schema, int def_index);

static uint64_t def_hash(const Py_UCS4 *name, Py_ssize_t len) {
    uint64_t hash = UINT64_C(1469598103934665603);
    for (Py_ssize_t index = 0; index < len; index++) {
        hash ^= name[index];
        hash *= UINT64_C(1099511628211);
    }
    return hash;
}

static uint64_t nc_name_hash(const nameclass *nc) {
    return def_hash(nc->uri, nc->uri_len) * UINT64_C(0x9E3779B97F4A7C15) ^ def_hash(nc->local, nc->local_len);
}

static void def_slot_add(def_vec *defines, Py_ssize_t index) {
    def_entry *entry = &defines->items[index];
    size_t slot = (size_t)def_hash(entry->name, entry->len) & (defines->slot_cap - 1);
    while (defines->slots[slot] != 0) {
        slot = (slot + 1) & (defines->slot_cap - 1);
    }
    defines->slots[slot] = (size_t)index + 1;
}

static int def_index(th_schema *schema) {
    def_vec *defines = &schema->defines;
    size_t cap = defines->slot_cap == 0 ? 32 : defines->slot_cap * 2;
    size_t *slots = arena_alloc(&schema->mem, cap * sizeof(size_t));
    if (slots == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return -1;       /* GCOVR_EXCL_LINE */
    }
    memset(slots, 0, cap * sizeof(size_t));
    defines->slots = slots;
    defines->slot_cap = cap;
    for (Py_ssize_t index = 0; index < defines->len; index++) {
        def_slot_add(defines, index);
    }
    return 0;
}

static Py_ssize_t def_find(const def_vec *defines, const Py_UCS4 *name, Py_ssize_t len) {
    if (defines->slots == NULL) {
        for (Py_ssize_t index = 0; index < defines->len; index++) {
            if (u_eq_u(defines->items[index].name, defines->items[index].len, name, len)) {
                return index;
            }
        }
        return -1;
    }
    size_t slot = (size_t)def_hash(name, len) & (defines->slot_cap - 1);
    while (defines->slots[slot] != 0) {
        Py_ssize_t index = (Py_ssize_t)defines->slots[slot] - 1;
        if (u_eq_u(defines->items[index].name, defines->items[index].len, name, len)) {
            return index;
        }
        slot = (slot + 1) & (defines->slot_cap - 1);
    }
    return -1;
}

static int def_part_add(th_schema *schema, def_entry *entry, th_node *node) {
    if (entry->first == NULL) {
        entry->first = node;
        return 0;
    }
    def_part *part = arena_alloc(&schema->mem, sizeof(*part));
    if (part == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return -1;      /* GCOVR_EXCL_LINE */
    }
    part->node = node;
    part->next = NULL;
    if (entry->last == NULL) {
        entry->extra = part;
    } else {
        entry->last->next = part;
    }
    entry->last = part;
    return 0;
}

static int def_add(th_schema *schema, const th_node_attr *name, th_node *node) {
    def_vec *defines = &schema->defines;
    Py_ssize_t index = def_find(defines, name->value, name->value_len);
    if (index >= 0) {
        return def_part_add(schema, &defines->items[index], node);
    }
    if (defines->len == defines->cap) {
        Py_ssize_t cap = defines->cap ? defines->cap * 2 : TH_INITIAL_CAPACITY(8);
        def_entry *items = arena_alloc(&schema->mem, (size_t)cap * sizeof(def_entry));
        if (items == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return -1;       /* GCOVR_EXCL_LINE */
        }
        if (defines->len > 0) {
            memcpy(items, defines->items, (size_t)defines->len * sizeof(def_entry));
        }
        defines->items = items;
        defines->cap = cap;
    }
    index = defines->len++;
    defines->items[index] = (def_entry){
        .name = name->value,
        .len = name->value_len,
        .first = node,
    };
    if ((defines->slots == NULL && defines->len == 8) ||
        (defines->slots != NULL && (size_t)defines->len * 2 > defines->slot_cap)) {
        return def_index(schema);
    }
    if (defines->slots != NULL) {
        def_slot_add(defines, index);
    }
    return 0;
}

/* The RELAX NG `ns` in scope at `node` (inherited from the nearest ancestor-or-self
   ns attribute), or (NULL, 0) for no namespace. */
static void rng_scope_ns(th_tree *tree, th_node *node, const Py_UCS4 **uri, Py_ssize_t *uri_len) {
    for (th_node *walk = node; walk != NULL; walk = walk->parent) {
        if (walk->type != TH_NODE_ELEMENT) {
            continue;
        }
        const th_node_attr *attr = attr_exact(tree, walk, "ns", 2);
        if (attr != NULL) {
            *uri = attr->value;
            *uri_len = attr->value_len;
            return;
        }
    }
    *uri = NULL;
    *uri_len = 0;
}

static nameclass *nc_new(th_schema *schema, int type) {
    nameclass *nc = arena_alloc(&schema->mem, sizeof(*nc));
    if (nc == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return NULL;  /* GCOVR_EXCL_LINE */
    }
    memset(nc, 0, sizeof(*nc));
    nc->type = type;
    return nc;
}

/* A choice or except name class over two parts; NULL when a part or the node itself could not be allocated. */
static nameclass *nc_pair(th_schema *schema, int type, nameclass *left, nameclass *right) {
    if (left == NULL || right == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM */
        return NULL;                     /* GCOVR_EXCL_LINE */
    }
    nameclass *nc = nc_new(schema, type);
    if (nc == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM */
        return NULL;  /* GCOVR_EXCL_LINE */
    }
    nc->a = left;
    nc->b = right;
    return nc;
}

/* A name class for a QName carried by @name or a <name> element. */
static nameclass *nc_from_qname(th_schema *schema, th_node *owner, const Py_UCS4 *name, Py_ssize_t name_len,
                                int is_attr) {
    th_tree *tree = schema->tree;
    nameclass *nc = nc_new(schema, NC_NAME);
    if (nc == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return NULL;  /* GCOVR_EXCL_LINE */
    }
    const Py_UCS4 *local, *prefix;
    Py_ssize_t local_len = 0, prefix_len = 0;
    split_prefix(name, name_len, &local, &local_len, &prefix, &prefix_len);
    nc->local = local;
    nc->local_len = local_len;
    if (prefix_len > 0) {
        resolve_ns(tree, owner, prefix, prefix_len, &nc->uri, &nc->uri_len);
    } else if (!is_attr) {
        rng_scope_ns(tree, owner, &nc->uri, &nc->uri_len);
    }
    nc->hash = nc_name_hash(nc);
    return nc;
}

/* The first element child of `node`, skipping character data, or NULL. */
static th_node *first_element_child(th_node *node) {
    th_node *child = node->first_child;
    while (child != NULL && child->type != TH_NODE_ELEMENT) {
        child = child->next_sibling;
    }
    return child;
}

static nameclass *rng_build_nameclass(th_schema *schema, th_node *node);

static nameclass *rng_nameclass_choice_children(th_schema *schema, th_node *node) {
    nameclass *combined = NULL;
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        if (child->type != TH_NODE_ELEMENT) {
            continue;
        }
        if (is_schema_el(schema, child, RNG_NS, "except")) {
            continue;
        }
        nameclass *part = rng_build_nameclass(schema, child);
        combined = combined == NULL ? part : nc_pair(schema, NC_CHOICE, combined, part);
        if (combined == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM */
            return NULL;        /* GCOVR_EXCL_LINE */
        }
    }
    return combined == NULL ? nc_new(schema, NC_ANY) : combined;
}

static nameclass *rng_build_nameclass(th_schema *schema, th_node *node) {
    th_tree *tree = schema->tree;
    if (node == NULL) { /* a malformed <element>/<attribute> with no name class matches any name */
        return nc_new(schema, NC_ANY);
    }
    if (is_schema_el(schema, node, RNG_NS, "name")) {
        Py_ssize_t len = 0;
        const Py_UCS4 *text = element_text_raw(tree, node, &len);
        return nc_from_qname(schema, node, text, len, 0);
    }
    if (is_schema_el(schema, node, RNG_NS, "anyName")) {
        th_node *except = first_schema_child(schema, node, RNG_NS, "except");
        if (except == NULL) {
            return nc_new(schema, NC_ANY);
        }
        return nc_pair(schema, NC_EXCEPT, nc_new(schema, NC_ANY), rng_nameclass_choice_children(schema, except));
    }
    if (is_schema_el(schema, node, RNG_NS, "nsName")) {
        nameclass *nc = nc_new(schema, NC_NS);
        if (nc == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM */
            return NULL;  /* GCOVR_EXCL_LINE */
        }
        const th_node_attr *ns = attr_exact(tree, node, "ns", 2);
        if (ns == NULL) {
            rng_scope_ns(tree, node, &nc->uri, &nc->uri_len);
        } else {
            nc->uri = ns->value;
            nc->uri_len = ns->value_len;
        }
        th_node *except = first_schema_child(schema, node, RNG_NS, "except");
        if (except == NULL) {
            return nc;
        }
        return nc_pair(schema, NC_EXCEPT, nc, rng_nameclass_choice_children(schema, except));
    }
    /* choice of name classes */
    return rng_nameclass_choice_children(schema, node);
}

static int nc_contains(const nameclass *nc, const qname *name) {
    switch (nc->type) {
    case NC_ANY:
        return 1;
    case NC_NAME:
        if (!u_eq_u(nc->local, nc->local_len, name->local, name->local_len)) {
            return 0;
        }
        return u_eq_u(nc->uri, nc->uri_len, name->uri, name->uri_len);
    case NC_NS:
        return u_eq_u(nc->uri, nc->uri_len, name->uri, name->uri_len);
    case NC_CHOICE:
        /* rng_nameclass_choice_children builds a left-leaning chain as long as the name list, so walk that spine */
        for (; nc->type == NC_CHOICE; nc = nc->a) {
            if (nc_contains(nc->b, name)) {
                return 1;
            }
        }
        return nc_contains(nc, name);
    default: /* NC_EXCEPT */
        if (!nc_contains(nc->a, name)) {
            return 0;
        }
        return !nc_contains(nc->b, name);
    }
}

static int rng_datatype_id(th_schema *schema, th_node *node, int default_datatype) {
    th_tree *tree = schema->tree;
    const th_node_attr *type = attr_exact(tree, node, "type", 4);
    const th_node_attr *library = NULL;
    for (th_node *walk = node; walk != NULL && library == NULL; walk = walk->parent) {
        if (walk->type == TH_NODE_ELEMENT) {
            library = attr_exact(tree, walk, "datatypeLibrary", 15);
        }
    }
    int xsd_library = 0;
    if (library != NULL) {
        xsd_library = u_eq_ascii(library->value, library->value_len, XSD_DT_NS);
    }
    if (type == NULL) {
        return default_datatype;
    }
    if (!xsd_library) {
        return u_eq_ascii(type->value, type->value_len, "token") ? DT_TOKEN : DT_STRING;
    }
    int builtin = dt_lookup(type->value, type->value_len);
    return builtin == DT_UNKNOWN ? DT_STRING : builtin;
}

static pattern *rng_build(th_schema *schema, th_node *node);
static nameclass *rng_attr_known_class(const th_schema *schema, const th_node *node);
static void rng_check_href(th_node *node);
static int rng_check_interleave_node(th_schema *schema, th_node *interleave);

/* Join the pattern elements from `first` on into one pattern (`none` when there are none). The first few join as they
   are built, with no buffer; a wide container makes that chain the first operand of a balanced join of the rest. */
static pattern *rng_build_joined(th_schema *schema, th_node *first, pat_join_fn join, pattern *none) {
    pattern *joined = none;
    Py_ssize_t count = 0;
    th_node *rest = first;
    for (; rest != NULL && count < RNG_CHAIN_JOIN; rest = rest->next_sibling) {
        if (rest->type == TH_NODE_ELEMENT) {
            joined = join(schema, joined, rng_build(schema, rest));
            count++;
        }
    }
    Py_ssize_t wide = 0;
    for (th_node *child = rest; child != NULL; child = child->next_sibling) {
        wide += child->type == TH_NODE_ELEMENT;
    }
    if (wide == 0) {
        return joined;
    }
    pattern **parts = arena_alloc(&schema->mem, (size_t)(wide + 1) * sizeof(pattern *));
    if (parts == NULL) {             /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return schema->p_notallowed; /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t len = 0;
    parts[len++] = joined;
    for (; rest != NULL; rest = rest->next_sibling) {
        if (rest->type == TH_NODE_ELEMENT) {
            parts[len++] = rng_build(schema, rest);
        }
    }
    return pat_fold(schema, join, parts, len);
}

/* Group the pattern children of a container into a single pattern (Empty when none). `skip`, when set, is the name
   class an element or attribute names itself by: its first element child. */
static pattern *rng_build_children(th_schema *schema, th_node *node, th_node *skip) {
    return rng_build_joined(schema, skip == NULL ? node->first_child : skip->next_sibling, pat_group, schema->p_empty);
}

static pattern *rng_build_choice_children(th_schema *schema, th_node *node) {
    return rng_build_joined(schema, node->first_child, pat_choice, schema->p_notallowed);
}

static pattern *rng_build(th_schema *schema, th_node *node) {
    th_tree *tree = schema->tree;
    if (is_schema_el(schema, node, RNG_NS, "empty")) {
        return schema->p_empty;
    }
    if (is_schema_el(schema, node, RNG_NS, "text")) {
        return schema->p_text;
    }
    if (is_schema_el(schema, node, RNG_NS, "notAllowed")) {
        return schema->p_notallowed;
    }
    if (is_schema_el(schema, node, RNG_NS, "element")) {
        pattern *node_pat = pat_new(schema, P_ELEMENT);
        if (node_pat == NULL) {          /* GCOVR_EXCL_BR_LINE: arena OOM */
            return schema->p_notallowed; /* GCOVR_EXCL_LINE */
        }
        const th_node_attr *name = attr_exact(tree, node, "name", 4);
        th_node *skip = NULL;
        if (name != NULL) {
            node_pat->nc = nc_from_qname(schema, node, name->value, name->value_len, 0);
        } else {
            th_node *nc_child = first_element_child(node);
            node_pat->nc = rng_build_nameclass(schema, nc_child);
            skip = nc_child;
        }
        if (node_pat->nc == NULL) {      /* GCOVR_EXCL_BR_LINE: arena OOM */
            return schema->p_notallowed; /* GCOVR_EXCL_LINE */
        }
        node_pat->p1 = rng_build_children(schema, node, skip);
        return node_pat;
    }
    if (is_schema_el(schema, node, RNG_NS, "attribute")) {
        pattern *node_pat = pat_new(schema, P_ATTRIBUTE);
        if (node_pat == NULL) {          /* GCOVR_EXCL_BR_LINE: arena OOM */
            return schema->p_notallowed; /* GCOVR_EXCL_LINE */
        }
        const th_node_attr *name = attr_exact(tree, node, "name", 4);
        th_node *skip = name == NULL ? first_element_child(node) : NULL;
        node_pat->nc = rng_attr_known_class(schema, node);
        if (node_pat->nc == NULL) {
            node_pat->nc = name != NULL ? nc_from_qname(schema, node, name->value, name->value_len, 1)
                                        : rng_build_nameclass(schema, skip);
        }
        if (node_pat->nc == NULL) {      /* GCOVR_EXCL_BR_LINE: arena OOM */
            return schema->p_notallowed; /* GCOVR_EXCL_LINE */
        }
        int has_content = 0;
        for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
            if (child->type == TH_NODE_ELEMENT && child != skip) {
                has_content = 1;
            }
        }
        /* an attribute with no content pattern defaults to <text/> (RELAX NG 3.6.2) */
        node_pat->p1 = has_content ? rng_build_children(schema, node, skip) : schema->p_text;
        return node_pat;
    }
    if (is_schema_el(schema, node, RNG_NS, "group")) {
        return rng_build_children(schema, node, NULL);
    }
    if (is_schema_el(schema, node, RNG_NS, "choice")) {
        return rng_build_choice_children(schema, node);
    }
    if (is_schema_el(schema, node, RNG_NS, "interleave")) {
        /* compile time only: validation builds defines lazily, after the scan has checked them */
        if (schema->intern == NULL && rng_check_interleave_node(schema, node) < 0) {
            return schema->p_notallowed;
        }
        return rng_build_joined(schema, node->first_child, pat_interleave, schema->p_empty);
    }
    if (is_schema_el(schema, node, RNG_NS, "optional")) {
        return pat_choice(schema, rng_build_children(schema, node, NULL), schema->p_empty);
    }
    if (is_schema_el(schema, node, RNG_NS, "zeroOrMore")) {
        return pat_choice(schema, pat_onemore(schema, rng_build_children(schema, node, NULL)), schema->p_empty);
    }
    if (is_schema_el(schema, node, RNG_NS, "oneOrMore")) {
        return pat_onemore(schema, rng_build_children(schema, node, NULL));
    }
    if (is_schema_el(schema, node, RNG_NS, "mixed")) {
        return pat_interleave(schema, rng_build_children(schema, node, NULL), schema->p_text);
    }
    if (is_schema_el(schema, node, RNG_NS, "list")) {
        pattern *node_pat = pat_new(schema, P_LIST);
        if (node_pat == NULL) {          /* GCOVR_EXCL_BR_LINE: arena OOM */
            return schema->p_notallowed; /* GCOVR_EXCL_LINE */
        }
        node_pat->p1 = rng_build_children(schema, node, NULL);
        return node_pat;
    }
    if (is_schema_el(schema, node, RNG_NS, "data")) {
        pattern *node_pat = pat_new(schema, P_DATA);
        if (node_pat == NULL) {          /* GCOVR_EXCL_BR_LINE: arena OOM */
            return schema->p_notallowed; /* GCOVR_EXCL_LINE */
        }
        node_pat->datatype_id = rng_datatype_id(schema, node, DT_STRING);
        facetset *facets = arena_alloc(&schema->mem, sizeof(*facets));
        if (facets == NULL) {            /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return schema->p_notallowed; /* GCOVR_EXCL_LINE */
        }
        facetset_init(facets, node_pat->datatype_id);
        for (th_node *param = node->first_child; param != NULL; param = param->next_sibling) {
            if (is_schema_el(schema, param, RNG_NS, "param")) {
                const th_node_attr *pname = attr_exact(tree, param, "name", 4);
                Py_ssize_t vlen = 0;
                const Py_UCS4 *value = element_text_raw(tree, param, &vlen);
                if (pname != NULL) {
                    facet_add(&schema->mem, facets, pname->value, pname->value_len, value, vlen);
                }
            } else if (is_schema_el(schema, param, RNG_NS, "except")) {
                node_pat->p1 = rng_build_choice_children(schema, param);
            }
        }
        node_pat->facets = facets;
        return node_pat;
    }
    if (is_schema_el(schema, node, RNG_NS, "value")) {
        pattern *node_pat = pat_new(schema, P_VALUE);
        if (node_pat == NULL) {          /* GCOVR_EXCL_BR_LINE: arena OOM */
            return schema->p_notallowed; /* GCOVR_EXCL_LINE */
        }
        node_pat->datatype_id = rng_datatype_id(schema, node, DT_TOKEN);
        Py_ssize_t raw_len = 0;
        const Py_UCS4 *raw = element_text_raw(tree, node, &raw_len);
        node_pat->value_ws = dt_default_ws(node_pat->datatype_id);
        node_pat->value = dt_normalize_ws_arena(&schema->mem, node_pat->value_ws, raw, raw_len, &node_pat->value_len);
        return node_pat;
    }
    if (is_schema_el(schema, node, RNG_NS, "ref")) {
        const th_node_attr *name = attr_exact(tree, node, "name", 4);
        if (name == NULL) { /* 4.10; the grammar scan rejects this earlier, so only the short form gets here */
            PyErr_SetString(PyExc_ValueError, "RELAX NG <ref> is missing the required name attribute");
            return schema->p_notallowed;
        }
        Py_ssize_t index = def_find(&schema->defines, name->value, name->value_len);
        if (index >= 0) {
            pattern *node_pat = pat_new(schema, P_REF);
            if (node_pat == NULL) {          /* GCOVR_EXCL_BR_LINE: arena OOM */
                return schema->p_notallowed; /* GCOVR_EXCL_LINE */
            }
            node_pat->def_index = (int)index;
            return node_pat;
        }
        PyErr_SetString(PyExc_ValueError, "RELAX NG <ref> has no matching define");
        return schema->p_notallowed;
    }
    rng_check_href(node);
    return schema->p_notallowed;
}

static void rng_check_href(th_node *node) {
    if (node->type != TH_NODE_ELEMENT) {
        return;
    }
    const Py_UCS4 *local, *prefix;
    Py_ssize_t local_len = 0, prefix_len = 0;
    split_prefix(node->text, node->text_len, &local, &local_len, &prefix, &prefix_len);
    /* rng_resolve_resources already replaced every reference that carries an href */
    if (u_eq_ascii(local, local_len, "externalRef") || u_eq_ascii(local, local_len, "include")) {
        PyErr_SetString(PyExc_ValueError, "RELAX NG resource reference is missing the required href attribute");
    }
}

static pattern *rng_combine(th_schema *schema, const def_entry *entry);
static int rng_combines_by_interleave(th_schema *schema, th_node *component);

static pattern *rng_resolve(th_schema *schema, int def_index) {
    def_entry *entry = &schema->defines.items[def_index];
    if (entry->built != NULL) {
        return entry->built;
    }
    entry->built = schema->p_empty; /* placeholder guards direct build recursion */
    entry->built = rng_combine(schema, entry);
    return entry->built;
}

/* Section 4.17 merges same-named components under one operator for the name: choice unless one says interleave. */
static pattern *rng_combine(th_schema *schema, const def_entry *entry) {
    if (entry->extra == NULL) {
        return rng_build_children(schema, entry->first, NULL);
    }
    Py_ssize_t count = 1;
    for (const def_part *extra = entry->extra; extra != NULL; extra = extra->next) {
        count++;
    }
    pattern **parts = arena_alloc(&schema->mem, (size_t)count * sizeof(pattern *));
    if (parts == NULL) {             /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return schema->p_notallowed; /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t len = 0;
    pat_join_fn join = pat_choice;
    th_node *define = entry->first;
    def_part *part = entry->extra;
    for (;;) {
        parts[len++] = rng_build_children(schema, define, NULL);
        if (rng_combines_by_interleave(schema, define)) {
            join = pat_interleave;
        }
        if (part == NULL) {
            break;
        }
        define = part->node;
        part = part->next;
    }
    return pat_fold(schema, join, parts, len);
}

static int rng_combines_by_interleave(th_schema *schema, th_node *component) {
    const th_node_attr *combine = attr_exact(schema->tree, component, "combine", 7);
    return combine != NULL && u_eq_ascii(combine->value, combine->value_len, "interleave");
}

/* ---- derivatives ---- */

static RNG_FORCEINLINE int rng_nullable_step(th_schema *schema, pattern *p);

static RNG_NOINLINE int rng_nullable_memo(th_schema *schema, pattern *p);
static RNG_NOINLINE pattern *rng_text_memo(th_schema *schema, pattern *p, const Py_UCS4 *value, Py_ssize_t len);
static RNG_NOINLINE pattern *rng_start_tag_open_memo(th_schema *schema, pattern *p, const qname *name);
static RNG_NOINLINE pattern *rng_att_memo(th_schema *schema, pattern *p, const qname *name, const Py_UCS4 *value,
                                          Py_ssize_t len);
static RNG_NOINLINE pattern *rng_start_tag_close_memo(th_schema *schema, pattern *p);

/* Each derivative is a wrapper that inlines into its caller and a walk that does not. The wrapper answers a leaf, or a
   known nullability, where the caller stands. Only a pattern that recurses pays a call and a step of the budget. */
static RNG_NOINLINE int rng_nullable_walk(th_schema *schema, pattern *p);

static RNG_FORCEINLINE int rng_nullable(th_schema *schema, pattern *p) {
    if (p->nullable >= 0) {
        return p->nullable;
    }
    return rng_nullable_walk(schema, p);
}

static RNG_NOINLINE int rng_nullable_walk(th_schema *schema, pattern *p) {
    TH_WORK_STEP();
    if (--schema->memo_budget >= 0) {
        return rng_nullable_step(schema, p);
    }
    return rng_nullable_memo(schema, p);
}

static RNG_FORCEINLINE int rng_nullable_step(th_schema *schema, pattern *p) {
    switch (p->type) {
    case P_CHOICE:
        return rng_nullable(schema, p->p1) || rng_nullable(schema, p->p2);
    case P_GROUP:
    case P_INTERLEAVE:
        return rng_nullable(schema, p->p1) && rng_nullable(schema, p->p2);
    case P_ONEMORE:
        return rng_nullable(schema, p->p1);
    default: /* P_REF; the compile-time 4.19 check rules out a ref that recurses here without an element */
        return rng_nullable(schema, rng_resolve(schema, p->def_index));
    }
}

static int rng_datatype_ok(th_schema *schema, int datatype_id, facetset *facets, const Py_UCS4 *value, Py_ssize_t len) {
    Py_ssize_t norm_len = 0;
    const Py_UCS4 *norm = dt_normalize_ws(schema, facets->ws, value, len, &norm_len);
    if (!dt_check_lexical(datatype_id, norm, norm_len)) {
        return 0;
    }
    if (facets->has_min_length && norm_len < facets->min_length) {
        return 0;
    }
    if (facets->has_max_length && norm_len > facets->max_length) {
        return 0;
    }
    if ((facets->has_min_inclusive || facets->has_max_inclusive || facets->has_min_exclusive ||
         facets->has_max_exclusive) &&
        dt_is_numeric(datatype_id)) {
        double number = dt_to_double(norm, norm_len);
        if (facets->has_min_inclusive && !(number >= facets->min_inclusive)) {
            return 0;
        }
        if (facets->has_max_inclusive && !(number <= facets->max_inclusive)) {
            return 0;
        }
        if (facets->has_min_exclusive && !(number > facets->min_exclusive)) {
            return 0;
        }
        if (facets->has_max_exclusive && !(number < facets->max_exclusive)) {
            return 0;
        }
    } else if (dt_is_ordered(datatype_id)) {
        if (facets->has_min_inclusive &&
            !facet_bound_ok(datatype_id, norm, norm_len, facets->range_text[RANGE_MIN_INCLUSIVE], 1, 1)) {
            return 0;
        }
        if (facets->has_max_inclusive &&
            !facet_bound_ok(datatype_id, norm, norm_len, facets->range_text[RANGE_MAX_INCLUSIVE], -1, 1)) {
            return 0;
        }
        if (facets->has_min_exclusive &&
            !facet_bound_ok(datatype_id, norm, norm_len, facets->range_text[RANGE_MIN_EXCLUSIVE], 1, 0)) {
            return 0;
        }
        if (facets->has_max_exclusive &&
            !facet_bound_ok(datatype_id, norm, norm_len, facets->range_text[RANGE_MAX_EXCLUSIVE], -1, 0)) {
            return 0;
        }
    }
    for (Py_ssize_t index = 0; index < facets->pattern_count; index++) {
        if (!regex_full_match(schema, facets->patterns[index].ptr, facets->patterns[index].len, norm, norm_len)) {
            return 0;
        }
    }
    return 1;
}

static int rng_is_whitespace(const Py_UCS4 *value, Py_ssize_t len) {
    for (Py_ssize_t index = 0; index < len; index++) {
        if (!is_xml_space(value[index])) {
            return 0;
        }
    }
    return 1;
}

/* Whether `value` normalized per `ws` equals `norm`, which the compiler normalized: every list token and every value
   test would otherwise copy its text into the arena first. */
static int rng_ws_equal(int ws, const Py_UCS4 *value, Py_ssize_t len, const Py_UCS4 *norm, Py_ssize_t norm_len) {
    if (ws == 0) {
        return u_eq_u(value, len, norm, norm_len);
    }
    Py_ssize_t out = 0;
    int pending = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_UCS4 codepoint = value[index];
        if (is_xml_space(codepoint)) {
            if (ws == 2) {
                pending = out > 0;
                continue;
            }
            codepoint = ' ';
        } else if (pending) {
            if (out == norm_len || norm[out] != ' ') {
                return 0;
            }
            out++;
            pending = 0;
        }
        if (out == norm_len || norm[out] != codepoint) {
            return 0;
        }
        out++;
    }
    return out == norm_len;
}

static RNG_FORCEINLINE pattern *rng_text_deriv(th_schema *schema, pattern *p, const Py_UCS4 *value, Py_ssize_t len);

static int rng_value_match(th_schema *schema, pattern *p, const Py_UCS4 *value, Py_ssize_t len) {
    if (rng_nullable(schema, rng_text_deriv(schema, p, value, len))) {
        return 1;
    }
    return rng_is_whitespace(value, len) && rng_nullable(schema, p);
}

static RNG_FORCEINLINE pattern *rng_text_deriv_step(th_schema *schema, pattern *p, const Py_UCS4 *value,
                                                    Py_ssize_t len);

static RNG_NOINLINE pattern *rng_text_walk(th_schema *schema, pattern *p, const Py_UCS4 *value, Py_ssize_t len);

static RNG_FORCEINLINE pattern *rng_text_deriv(th_schema *schema, pattern *p, const Py_UCS4 *value, Py_ssize_t len) {
    if ((RNG_TEXT_REJECTS >> p->type) & 1) {
        return schema->p_notallowed;
    }
    if (p->type == P_VALUE) { /* a leaf, so like the rejects it takes no step */
        return rng_ws_equal(p->value_ws, value, len, p->value, p->value_len) ? schema->p_empty : schema->p_notallowed;
    }
    return rng_text_walk(schema, p, value, len);
}

static RNG_NOINLINE pattern *rng_text_walk(th_schema *schema, pattern *p, const Py_UCS4 *value, Py_ssize_t len) {
    TH_WORK_STEP();
    if (--schema->memo_budget >= 0) {
        return rng_text_deriv_step(schema, p, value, len);
    }
    return rng_text_memo(schema, p, value, len);
}

/* Each token is an event of its own, with its own free steps and the token memo: in the text's memo the derivatives of
   every token would stay until the text event ends, and on the text's budget a long list would take the memo path for
   tokens that never revisit a node. A token that runs past its free steps starts the next ones on the memo, and the
   text pays the steps its tokens took. Each token also has the work limit of an event, and the text pays the mean of
   its tokens' work, so the lists one text reaches share an allowance of one event per token. */
static RNG_NOINLINE pattern *rng_list_deriv(th_schema *schema, pattern *p, const Py_UCS4 *value, Py_ssize_t len) {
    pattern *derived = p->p1;
    Py_ssize_t budget = schema->memo_budget;
    Py_ssize_t steps = schema->memo_free;
    Py_ssize_t spent = 0;
    Py_ssize_t work = 0;
    Py_ssize_t tokens = 0;
    Py_ssize_t index = 0;
    Py_ssize_t text_work = schema->event_work;
    schema->memo = schema->token_memo;
    while (index < len) {
        while (index < len && is_xml_space(value[index])) {
            index++;
        }
        Py_ssize_t start = index;
        while (index < len && !is_xml_space(value[index])) {
            index++;
        }
        if (index > start) {
            memo_reset(schema->memo);
            schema->memo_budget = steps;
            schema->event_work = 0;
            derived = rng_text_deriv(schema, derived, value + start, index - start);
            spent += steps - schema->memo_budget;
            work += schema->event_work;
            tokens++;
            if (schema->memo_budget < 0) {
                steps = 0;
            }
            /* NotAllowed takes no step for the tokens after it, so the rest of the list cannot change the result. */
            if (derived == schema->p_notallowed) {
                break;
            }
        }
    }
    schema->memo = schema->event_memo;
    schema->event_work = text_work + (tokens > 0 ? work / tokens : 0);
    /* Stopping at 0 lets the text's next step see -1 and empty the memo of an earlier event. */
    schema->memo_budget = budget < 0 ? budget : spent < budget ? budget - spent : 0;
    return rng_nullable(schema, derived) ? schema->p_empty : schema->p_notallowed;
}

static RNG_FORCEINLINE pattern *rng_text_deriv_step(th_schema *schema, pattern *p, const Py_UCS4 *value,
                                                    Py_ssize_t len) {
    switch (p->type) {
    case P_CHOICE:
        return pat_choice(schema, rng_text_deriv(schema, p->p1, value, len), rng_text_deriv(schema, p->p2, value, len));
    case P_INTERLEAVE:
        return pat_choice(schema, pat_interleave(schema, rng_text_deriv(schema, p->p1, value, len), p->p2),
                          pat_interleave(schema, p->p1, rng_text_deriv(schema, p->p2, value, len)));
    case P_GROUP: {
        pattern *left = pat_group(schema, rng_text_deriv(schema, p->p1, value, len), p->p2);
        if (rng_nullable(schema, p->p1)) {
            return pat_choice(schema, left, rng_text_deriv(schema, p->p2, value, len));
        }
        return left;
    }
    case P_AFTER:
        return pat_after(schema, rng_text_deriv(schema, p->p1, value, len), p->p2);
    case P_ONEMORE:
        return pat_group(schema, rng_text_deriv(schema, p->p1, value, len), pat_choice(schema, p, schema->p_empty));
    case P_TEXT:
        return p;
    case P_DATA:
        if (!rng_datatype_ok(schema, p->datatype_id, p->facets, value, len)) {
            return schema->p_notallowed;
        }
        return p->p1 != NULL && rng_nullable(schema, rng_text_deriv(schema, p->p1, value, len)) ? schema->p_notallowed
                                                                                                : schema->p_empty;
    case P_LIST:
        /* 7.1 forbids a list inside a list, but the compiler accepts one. It splits a token into that same token, so
           it stays in the token's event. */
        if (schema->memo == schema->token_memo) {
            return rng_nullable(schema, rng_text_deriv(schema, p->p1, value, len)) ? schema->p_empty
                                                                                   : schema->p_notallowed;
        }
        return rng_list_deriv(schema, p, value, len);
    case P_REF: /* the compile-time 4.19 check rules out a ref that recurses here without an element */
        return rng_text_deriv(schema, rng_resolve(schema, p->def_index), value, len);
    default: /* a failed start tag leaves NotAllowed where the text event starts */
        return schema->p_notallowed;
    }
}

/* applyAfter over an After/Choice tree: replace each After tail t with either
   group(t, tail) (mode 0, for group/oneOrMore) or after(t, tail) (mode 1, for after). */
static pattern *rng_apply_after(th_schema *schema, pattern *p, pattern *tail, int as_after);
static pattern *rng_apply_after_interleave_left(th_schema *schema, pattern *p, const qname *name);
static pattern *rng_apply_after_interleave_right(th_schema *schema, pattern *p, const qname *name);

static RNG_FORCEINLINE pattern *rng_start_tag_open_step(th_schema *schema, pattern *p, const qname *name);

static RNG_NOINLINE pattern *rng_start_tag_open_walk(th_schema *schema, pattern *p, const qname *name);

static RNG_FORCEINLINE pattern *rng_start_tag_open(th_schema *schema, pattern *p, const qname *name) {
    if ((RNG_OPEN_REJECTS >> p->type) & 1) {
        return schema->p_notallowed;
    }
    if (p->type == P_ELEMENT) {
        return nc_contains(p->nc, name) ? pat_after(schema, p->p1, schema->p_empty) : schema->p_notallowed;
    }
    return rng_start_tag_open_walk(schema, p, name);
}

static RNG_NOINLINE pattern *rng_start_tag_open_walk(th_schema *schema, pattern *p, const qname *name) {
    TH_WORK_STEP();
    if (--schema->memo_budget >= 0) {
        return rng_start_tag_open_step(schema, p, name);
    }
    return rng_start_tag_open_memo(schema, p, name);
}

static RNG_FORCEINLINE pattern *rng_start_tag_open_step(th_schema *schema, pattern *p, const qname *name) {
    switch (p->type) {
    case P_CHOICE:
        return pat_choice(schema, rng_start_tag_open(schema, p->p1, name), rng_start_tag_open(schema, p->p2, name));
    case P_ELEMENT:
        return nc_contains(p->nc, name) ? pat_after(schema, p->p1, schema->p_empty) : schema->p_notallowed;
    case P_INTERLEAVE:
        return pat_choice(schema, rng_apply_after_interleave_left(schema, p, name),
                          rng_apply_after_interleave_right(schema, p, name));
    case P_ONEMORE: {
        pattern *derived = rng_start_tag_open(schema, p->p1, name);
        return rng_apply_after(schema, derived, pat_choice(schema, p, schema->p_empty), 0);
    }
    case P_GROUP: {
        pattern *left = rng_apply_after(schema, rng_start_tag_open(schema, p->p1, name), p->p2, 0);
        if (rng_nullable(schema, p->p1)) {
            return pat_choice(schema, left, rng_start_tag_open(schema, p->p2, name));
        }
        return left;
    }
    case P_AFTER:
        return rng_apply_after(schema, rng_start_tag_open(schema, p->p1, name), p->p2, 1);
    case P_REF:
        return rng_start_tag_open(schema, rng_resolve(schema, p->def_index), name);
    default:
        return schema->p_notallowed;
    }
}

static pattern *rng_apply_after(th_schema *schema, pattern *p, pattern *tail, int as_after) {
    if (p->type == P_AFTER) {
        pattern *combined = as_after ? pat_after(schema, p->p2, tail) : pat_group(schema, p->p2, tail);
        return pat_after(schema, p->p1, combined);
    }
    if (p->type == P_CHOICE) {
        return pat_choice(schema, rng_apply_after(schema, p->p1, tail, as_after),
                          rng_apply_after(schema, p->p2, tail, as_after));
    }
    return schema->p_notallowed;
}

/* applyAfter over `derived` (the derivative of one interleave branch), re-wrapping each
   After tail so the sibling `other` interleaves with the continuation. `derived` is
   already the start-tag derivative, so this never re-opens it. */
static pattern *rng_apply_after_interleave(th_schema *schema, pattern *derived, pattern *other, int other_on_right) {
    if (derived->type == P_AFTER) {
        pattern *combined =
            other_on_right ? pat_interleave(schema, derived->p2, other) : pat_interleave(schema, other, derived->p2);
        return pat_after(schema, derived->p1, combined);
    }
    if (derived->type == P_CHOICE) {
        return pat_choice(schema, rng_apply_after_interleave(schema, derived->p1, other, other_on_right),
                          rng_apply_after_interleave(schema, derived->p2, other, other_on_right));
    }
    return schema->p_notallowed;
}

static pattern *rng_apply_after_interleave_left(th_schema *schema, pattern *p, const qname *name) {
    return rng_apply_after_interleave(schema, rng_start_tag_open(schema, p->p1, name), p->p2, 1);
}

static pattern *rng_apply_after_interleave_right(th_schema *schema, pattern *p, const qname *name) {
    return rng_apply_after_interleave(schema, rng_start_tag_open(schema, p->p2, name), p->p1, 0);
}

static RNG_FORCEINLINE pattern *rng_att_deriv_step(th_schema *schema, pattern *p, const qname *name,
                                                   const Py_UCS4 *value, Py_ssize_t len);

static RNG_NOINLINE pattern *rng_att_walk(th_schema *schema, pattern *p, const qname *name, const Py_UCS4 *value,
                                          Py_ssize_t len);

static RNG_FORCEINLINE pattern *rng_att_deriv(th_schema *schema, pattern *p, const qname *name, const Py_UCS4 *value,
                                              Py_ssize_t len) {
    if ((RNG_ATT_REJECTS >> p->type) & 1) {
        return schema->p_notallowed;
    }
    return rng_att_walk(schema, p, name, value, len);
}

static RNG_NOINLINE pattern *rng_att_walk(th_schema *schema, pattern *p, const qname *name, const Py_UCS4 *value,
                                          Py_ssize_t len) {
    TH_WORK_STEP();
    if (--schema->memo_budget >= 0) {
        return rng_att_deriv_step(schema, p, name, value, len);
    }
    return rng_att_memo(schema, p, name, value, len);
}

static RNG_FORCEINLINE pattern *rng_att_deriv_step(th_schema *schema, pattern *p, const qname *name,
                                                   const Py_UCS4 *value, Py_ssize_t len) {
    switch (p->type) {
    case P_CHOICE:
        return pat_choice(schema, rng_att_deriv(schema, p->p1, name, value, len),
                          rng_att_deriv(schema, p->p2, name, value, len));
    case P_GROUP:
        return pat_choice(schema, pat_group(schema, rng_att_deriv(schema, p->p1, name, value, len), p->p2),
                          pat_group(schema, p->p1, rng_att_deriv(schema, p->p2, name, value, len)));
    case P_INTERLEAVE:
        return pat_choice(schema, pat_interleave(schema, rng_att_deriv(schema, p->p1, name, value, len), p->p2),
                          pat_interleave(schema, p->p1, rng_att_deriv(schema, p->p2, name, value, len)));
    case P_AFTER:
        return pat_after(schema, rng_att_deriv(schema, p->p1, name, value, len), p->p2);
    case P_ONEMORE:
        return pat_group(schema, rng_att_deriv(schema, p->p1, name, value, len),
                         pat_choice(schema, p, schema->p_empty));
    case P_ATTRIBUTE:
        return nc_contains(p->nc, name) && rng_value_match(schema, p->p1, value, len) ? schema->p_empty
                                                                                      : schema->p_notallowed;
    case P_REF: /* the compile-time 4.19 check rules out a ref that recurses here without an element */
        return rng_att_deriv(schema, rng_resolve(schema, p->def_index), name, value, len);
    default:
        return schema->p_notallowed;
    }
}

static RNG_FORCEINLINE pattern *rng_start_tag_close_step(th_schema *schema, pattern *p);

static RNG_NOINLINE pattern *rng_start_tag_close_walk(th_schema *schema, pattern *p);

static RNG_FORCEINLINE pattern *rng_start_tag_close(th_schema *schema, pattern *p) {
    if ((RNG_CLOSE_KEEPS >> p->type) & 1) {
        return p;
    }
    return rng_start_tag_close_walk(schema, p);
}

static RNG_NOINLINE pattern *rng_start_tag_close_walk(th_schema *schema, pattern *p) {
    TH_WORK_STEP();
    if (--schema->memo_budget >= 0) {
        return rng_start_tag_close_step(schema, p);
    }
    return rng_start_tag_close_memo(schema, p);
}

static RNG_FORCEINLINE pattern *rng_start_tag_close_step(th_schema *schema, pattern *p) {
    switch (p->type) {
    case P_CHOICE:
        return pat_choice(schema, rng_start_tag_close(schema, p->p1), rng_start_tag_close(schema, p->p2));
    case P_GROUP:
        return pat_group(schema, rng_start_tag_close(schema, p->p1), rng_start_tag_close(schema, p->p2));
    case P_INTERLEAVE:
        return pat_interleave(schema, rng_start_tag_close(schema, p->p1), rng_start_tag_close(schema, p->p2));
    case P_ONEMORE:
        return pat_onemore(schema, rng_start_tag_close(schema, p->p1));
    case P_AFTER:
        return pat_after(schema, rng_start_tag_close(schema, p->p1), p->p2);
    case P_ATTRIBUTE:
        return schema->p_notallowed;
    case P_REF: /* the compile-time 4.19 check rules out a ref that recurses here without an element */
        return rng_start_tag_close(schema, rng_resolve(schema, p->def_index));
    default:
        return p;
    }
}

/* Nullability is no pattern, so the memo stores it as Empty or NotAllowed. */
static RNG_NOINLINE int rng_nullable_memo(th_schema *schema, pattern *p) {
    memo_engage(schema);
    pattern *found = memo_find(schema->memo, MEMO_NULLABLE, p, NULL, 0, NULL);
    if (found != NULL) {
        schema->memo_free = MEMO_FREE_STEPS; /* a memo hit is a revisit: keep the budget */
        return found == schema->p_empty;
    }
    int nullable = rng_nullable_step(schema, p);
    memo_add(schema, MEMO_NULLABLE, p, NULL, 0, NULL, nullable ? schema->p_empty : schema->p_notallowed);
    return nullable;
}

static RNG_NOINLINE pattern *rng_text_memo(th_schema *schema, pattern *p, const Py_UCS4 *value, Py_ssize_t len) {
    memo_engage(schema);
    pattern *derived = memo_find(schema->memo, MEMO_TEXT, p, value, len, NULL);
    if (derived != NULL) {
        schema->memo_free = MEMO_FREE_STEPS; /* a memo hit is a revisit: keep the budget */
        return derived;
    }
    derived = rng_text_deriv_step(schema, p, value, len);
    memo_add(schema, MEMO_TEXT, p, value, len, NULL, derived);
    return derived;
}

static RNG_NOINLINE pattern *rng_start_tag_open_memo(th_schema *schema, pattern *p, const qname *name) {
    memo_engage(schema);
    pattern *derived = memo_find(schema->memo, MEMO_OPEN, p, NULL, 0, name->local);
    if (derived != NULL) {
        schema->memo_free = MEMO_FREE_STEPS; /* a memo hit is a revisit: keep the budget */
        return derived;
    }
    derived = rng_start_tag_open_step(schema, p, name);
    memo_add(schema, MEMO_OPEN, p, NULL, 0, name->local, derived);
    return derived;
}

static RNG_NOINLINE pattern *rng_att_memo(th_schema *schema, pattern *p, const qname *name, const Py_UCS4 *value,
                                          Py_ssize_t len) {
    memo_engage(schema);
    pattern *derived = memo_find(schema->memo, MEMO_ATT, p, value, len, name->local);
    if (derived != NULL) {
        schema->memo_free = MEMO_FREE_STEPS; /* a memo hit is a revisit: keep the budget */
        return derived;
    }
    derived = rng_att_deriv_step(schema, p, name, value, len);
    memo_add(schema, MEMO_ATT, p, value, len, name->local, derived);
    return derived;
}

static RNG_NOINLINE pattern *rng_start_tag_close_memo(th_schema *schema, pattern *p) {
    memo_engage(schema);
    pattern *derived = memo_find(schema->memo, MEMO_CLOSE, p, NULL, 0, NULL);
    if (derived != NULL) {
        schema->memo_free = MEMO_FREE_STEPS; /* a memo hit is a revisit: keep the budget */
        return derived;
    }
    derived = rng_start_tag_close_step(schema, p);
    memo_add(schema, MEMO_CLOSE, p, NULL, 0, NULL, derived);
    return derived;
}

static pattern *rng_end_tag_deriv(th_schema *schema, pattern *p) {
    if (p->type == P_CHOICE) {
        return pat_choice(schema, rng_end_tag_deriv(schema, p->p1), rng_end_tag_deriv(schema, p->p2));
    }
    if (p->type == P_AFTER) {
        return rng_nullable(schema, p->p1) ? p->p2 : schema->p_notallowed;
    }
    return schema->p_notallowed;
}

/* An element whose children are still being derived over. rng_child_element keeps these on an explicit stack
   (walk_stack_reserve) instead of recursing once per instance level. */
typedef struct {
    th_node *element;
    th_node *child;     /* the next child to derive over, NULL once the list is consumed */
    Py_ssize_t mark;    /* path length to restore at the end tag */
    Py_ssize_t before;  /* error count at the start tag: unchanged at the end tag, no failure in here is reported yet */
    Py_ssize_t ns_mark; /* namespace bindings to unbind at the end tag */
} rng_frame;

/* Derive over an element's child list, ignoring whitespace-only text between elements
   and allowing insignificant whitespace when the content is a single text run. A list with child elements is only
   queued on the frame: rng_next_child derives over it one child at a time. */
static pattern *rng_children_deriv(valctx *ctx, pattern *p, rng_frame *frame) {
    th_schema *schema = ctx->schema;
    th_node *element = frame->element;
    Py_ssize_t element_children = 0, text_children = 0;
    for (th_node *child = element->first_child; child != NULL; child = child->next_sibling) {
        if (child->type == TH_NODE_ELEMENT) {
            element_children++;
        } else if (is_chardata(child)) {
            text_children++;
        }
    }
    if (element_children == 0 && text_children == 0) {
        memo_begin_event(schema);
        return pat_choice(schema, p, rng_text_walk(schema, p, EMPTY_UCS4, 0));
    }
    if (element_children == 0) {
        Py_ssize_t len = 0;
        const Py_UCS4 *text = element_text(ctx, element, &len);
        memo_begin_event(schema);
        pattern *derived = rng_text_walk(schema, p, text, len);
        return rng_is_whitespace(text, len) ? pat_choice(schema, p, derived) : derived;
    }
    frame->child = element->first_child;
    return p;
}

/* Derive *p over the frame's queued children up to the next child element and return that element for the caller
   to derive over, or NULL once the list is consumed. */
static th_node *rng_next_child(valctx *ctx, pattern **p, rng_frame *frame) {
    th_schema *schema = ctx->schema;
    th_tree *tree = ctx->tree;
    while (frame->child != NULL) {
        th_node *child = frame->child;
        frame->child = child->next_sibling;
        if (child->type == TH_NODE_ELEMENT) {
            return child;
        }
        if (is_chardata(child)) {
            Py_ssize_t len = child->text_len;
            const Py_UCS4 *text = len == 0 ? EMPTY_UCS4 : th_node_realize_text(tree, child);
            if (text == NULL) {            /* GCOVR_EXCL_BR_LINE: text realization allocation failure */
                ctx->failed = 1;           /* GCOVR_EXCL_LINE */
                PyErr_NoMemory();          /* GCOVR_EXCL_LINE */
                *p = schema->p_notallowed; /* GCOVR_EXCL_LINE */
                return NULL;               /* GCOVR_EXCL_LINE */
            }
            if (!rng_is_whitespace(text, len)) {
                memo_begin_event(schema);
                *p = rng_text_walk(schema, *p, text, len);
            }
        }
    }
    return NULL;
}

/* Derive the pattern over an element's start tag and attributes, and over its content when that has no child
   elements. A start tag the pattern refuses is reported here and queues no children, so the walk goes straight to
   the end tag, which passes NotAllowed on without a second report. */
static pattern *rng_start_tag(valctx *ctx, pattern *p, th_node *element, rng_frame *frame) {
    th_schema *schema = ctx->schema;
    th_tree *tree = ctx->tree;
    Py_ssize_t ns_mark = ns_scope_enter(schema, tree, element);
    qname name = node_qname_scoped(schema, element->text, element->text_len, 0);
    *frame = (rng_frame){element, NULL, ctx->path.len, ctx->error_count, ns_mark};
    if (path_push(&ctx->path, name.local, name.local_len) < 0) { /* GCOVR_EXCL_BR_LINE: path OOM is unforceable */
        ctx->failed = 1;                                         /* GCOVR_EXCL_LINE */
        PyErr_NoMemory();                                        /* GCOVR_EXCL_LINE */
        return schema->p_notallowed;                             /* GCOVR_EXCL_LINE */
    }
    memo_begin_event(schema);
    pattern *opened = rng_start_tag_open_walk(schema, p, &name);
    if (opened->type == P_NOTALLOWED) {
        char buffer[256];
        report(ctx, element, "structure", "element '%s' is not allowed here",
               name_utf8(element->text, element->text_len, buffer, sizeof(buffer)));
        return schema->p_notallowed;
    }
    pattern *after_attrs = opened;
    for (Py_ssize_t index = 0; index < element->attr_count; index++) {
        Py_ssize_t alen = 0;
        const char *abytes = th_attr_name(tree, element->attrs[index].name_atom, &alen);
        if (is_xmlns_decl(abytes, alen)) {
            continue;
        }
        Py_UCS4 *scratch = arena_alloc(&schema->mem, (size_t)(alen + 1) * sizeof(Py_UCS4));
        if (scratch == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            continue;          /* GCOVR_EXCL_LINE */
        }
        Py_ssize_t nlen = utf8_to_ucs4(abytes, alen, scratch);
        qname aname = node_qname_scoped(schema, scratch, nlen, 1);
        const Py_UCS4 *value = element->attrs[index].value;
        if (value == NULL) {    /* GCOVR_EXCL_BR_LINE: an XML attribute always has a value */
            value = EMPTY_UCS4; /* GCOVR_EXCL_LINE */
        } /* GCOVR_EXCL_LINE: llvm miscredits the closing brace of the unexecuted guard */
        memo_begin_event(schema);
        after_attrs = rng_att_walk(schema, after_attrs, &aname, value, element->attrs[index].value_len);
    }
    memo_begin_event(schema);
    pattern *closed = rng_start_tag_close_walk(schema, after_attrs);
    return rng_children_deriv(ctx, closed, frame);
}

/* Derive the pattern left by an element's content over its end tag. */
static pattern *rng_end_tag(valctx *ctx, pattern *content, const rng_frame *frame) {
    th_node *element = frame->element;
    pattern *ended = rng_end_tag_deriv(ctx->schema, content);
    /* endTagDeriv yields NotAllowed exactly when this element's own content failed; a
       non-nullable residual is just the continuation the parent's siblings must match. */
    if (ended->type == P_NOTALLOWED && ctx->error_count == frame->before) {
        char buffer[256];
        report(ctx, element, "structure", "content of element '%s' does not match the schema",
               name_utf8(element->text, element->text_len, buffer, sizeof(buffer)));
    }
    ctx->path.len = frame->mark;
    ns_scope_leave(ctx->schema, frame->ns_mark);
    return ended;
}

/* Derive the pattern over the children an element queued below the shallow levels, each one's children in turn, on the
   explicit stack. A start tag is taken on the way in and its end tag once that element's children are consumed, the
   order childDeriv recurses in. */
static pattern *rng_walk(valctx *ctx, pattern *p, const rng_frame *base) {
    th_schema *schema = ctx->schema;
    rng_frame *open = NULL;
    Py_ssize_t depth = 0, cap = 0;
    open = walk_stack_reserve(&schema->mem, open, depth, &cap, sizeof(rng_frame));
    if (open == NULL) {              /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return schema->p_notallowed; /* GCOVR_EXCL_LINE: the validate entry raises the MemoryError */
    }
    open[depth++] = *base;
    for (;;) {
        th_node *element = rng_next_child(ctx, &p, &open[depth - 1]);
        if (element != NULL) {
            open = walk_stack_reserve(&schema->mem, open, depth, &cap, sizeof(rng_frame));
            if (open == NULL) {              /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
                return schema->p_notallowed; /* GCOVR_EXCL_LINE: the validate entry raises the MemoryError */
            }
            p = rng_start_tag(ctx, p, element, &open[depth++]);
            continue;
        }
        p = rng_end_tag(ctx, p, &open[depth - 1]);
        if (--depth == 0) {
            return p;
        }
    }
}

/* Derive the pattern over one child element, localizing any failure to it. Levels up to TH_VALIDATE_SHALLOW_DEPTH
   recurse on the C stack; deeper levels continue in rng_walk. */
static pattern *rng_child_element(valctx *ctx, pattern *p, th_node *element, Py_ssize_t depth) {
    rng_frame frame;
    p = rng_start_tag(ctx, p, element, &frame);
    if (depth >= TH_VALIDATE_SHALLOW_DEPTH) {
        return rng_walk(ctx, p, &frame);
    }
    while ((element = rng_next_child(ctx, &p, &frame)) != NULL) {
        p = rng_child_element(ctx, p, element, depth + 1);
    }
    return rng_end_tag(ctx, p, &frame);
}

/* ---- compile & entry ---- */

/* The elements the grammar scan has yet to walk, and the deepest pattern level it has reached since the define it is
   expanding began. A grammar with few elements keeps the queue on the stack. */
typedef struct {
    th_node **items;
    Py_ssize_t len, cap;
    int peak;
    int attrs; /* the content scanned since the last reset holds an attribute pattern, refs followed */
    th_node *local[16];
} nodevec;

static int rng_scan(th_schema *schema, th_node *container, int depth, nodevec *pending);
static int rng_check_combine(th_schema *schema, def_entry *entry);
static int rng_check_scope(th_schema *schema, th_node *element);

/* An element's 7.3 check runs once its content is scanned, and only when the scan met an attribute in it: by then
   every define it reaches is free of ref cycles and nests no deeper than TH_VALIDATE_MAX_DEPTH, which bounds the
   check's recursion. */
static int rng_scan_queue(th_schema *schema, def_entry *starts, nodevec *pending) {
    if (rng_check_combine(schema, starts) < 0 || rng_scan(schema, starts->first, 0, pending) < 0) {
        return -1;
    }
    for (def_part *part = starts->extra; part != NULL; part = part->next) {
        if (rng_scan(schema, part->node, 0, pending) < 0) {
            return -1;
        }
    }
    for (Py_ssize_t index = 0; index < pending->len; index++) {
        th_node *element = pending->items[index];
        pending->attrs = 0;
        if (rng_scan(schema, element, 0, pending) < 0 || (pending->attrs && rng_check_scope(schema, element) < 0)) {
            return -1;
        }
    }
    return 0;
}

/* Scans the start components, then each element they queue, and each element those queue in turn. */
static RNG_NOINLINE int rng_scan_grammar(th_schema *schema, def_entry *starts) {
    nodevec pending;
    pending.items = pending.local;
    pending.len = 0;
    pending.cap = TH_INITIAL_CAPACITY(16);
    pending.peak = 0;
    pending.attrs = 0;
    int scanned = rng_scan_queue(schema, starts, &pending);
    if (pending.items != pending.local) {
        PyMem_Free(pending.items);
    }
    return scanned;
}

static int rng_is_reference(th_schema *schema, th_node *node);
static int rng_check_unused_refs(th_schema *schema, th_node *container);
static void rng_prune_annotations(th_schema *schema, th_node *container);

static int rng_resolve_resources(th_schema *schema, PyObject *base_url, PyObject *include_root);
static int rng_local_is(th_node *node, const char *local);
static int rng_grammar_components(th_schema *schema, th_node *container, def_entry *starts);
static int rng_check_scopes(th_schema *schema, th_node *node);

static int rng_compile(th_schema *schema, PyObject *base_url, PyObject *include_root) {
    qname root_name = schema_direct_qname(schema, schema->root);
    if (!u_eq_ascii(root_name.uri, root_name.uri_len, RNG_NS)) {
        PyErr_SetString(PyExc_ValueError, "RELAX NG schema root must use the structure namespace");
        return 0;
    }
    schema->rng_references = (u_eq_ascii(root_name.local, root_name.local_len, "include") ||
                              u_eq_ascii(root_name.local, root_name.local_len, "externalRef")) &&
                             attr_exact(schema->tree, schema->root, "href", 4) != NULL;
    rng_prune_annotations(schema, schema->root);
    if (rng_resolve_resources(schema, base_url, include_root) < 0) {
        return 0;
    }
    schema->p_empty = pat_new(schema, P_EMPTY);
    schema->p_notallowed = pat_new(schema, P_NOTALLOWED);
    schema->p_text = pat_new(schema, P_TEXT);
    /* GCOVR_EXCL_BR_START: arena OOM */
    if (schema->p_empty == NULL || schema->p_notallowed == NULL || schema->p_text == NULL) {
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
        return 0;         /* GCOVR_EXCL_LINE */
    }
    /* GCOVR_EXCL_BR_STOP */
    if (!rng_local_is(schema->root, "grammar")) {
        /* no defines, so no ref cycles; rng_build checks 4.10 and 7.4 inline instead of a second walk */
        if (schema->rng_attributes && rng_check_scopes(schema, schema->root) < 0) {
            return 0;
        }
        schema->start = rng_build(schema, schema->root);
        return PyErr_Occurred() ? 0 : 1;
    }
    def_entry starts = {0};
    if (rng_grammar_components(schema, schema->root, &starts) < 0) {
        return 0;
    }
    if (starts.first == NULL) {
        PyErr_SetString(PyExc_ValueError, "grammar has no start element");
        return 0;
    }
    for (Py_ssize_t index = 0; index < schema->defines.len; index++) {
        schema->defines.items[index].scan_height = -1;
    }
    if (rng_scan_grammar(schema, &starts) < 0) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < schema->defines.len; index++) {
        def_entry *entry = &schema->defines.items[index];
        if (entry->scan_height != -1) {
            continue;
        }
        if (rng_check_unused_refs(schema, entry->first) < 0) {
            return 0;
        }
        for (def_part *part = entry->extra; part != NULL; part = part->next) {
            if (rng_check_unused_refs(schema, part->node) < 0) {
                return 0;
            }
        }
    }
    schema->start = rng_combine(schema, &starts);
    return PyErr_Occurred() ? 0 : 1;
}

/* Gather the start and define components of a grammar, looking through div (4.11, and the divs 4.7 makes of each
   include). Returns 0, or -1 with an exception set. */
static int rng_grammar_components(th_schema *schema, th_node *container, def_entry *starts) {
    for (th_node *child = container->first_child; child != NULL; child = child->next_sibling) {
        if (rng_local_is(child, "define")) {
            const th_node_attr *name = attr_exact(schema->tree, child, "name", 4);
            if (name != NULL && def_add(schema, name, child) < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM */
                PyErr_NoMemory();                                   /* GCOVR_EXCL_LINE */
                return -1;                                          /* GCOVR_EXCL_LINE */
            }
        } else if (rng_local_is(child, "start")) {
            if (def_part_add(schema, starts, child) < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
                PyErr_NoMemory();                          /* GCOVR_EXCL_LINE */
                return -1;                                 /* GCOVR_EXCL_LINE */
            }
        } else if (rng_local_is(child, "div")) {
            if (rng_grammar_components(schema, child, starts) < 0) {
                return -1;
            }
        } else {
            rng_check_href(child);
            if (PyErr_Occurred()) {
                return -1;
            }
        }
    }
    return 0;
}

/* Annotation pruning (4.1) leaves only RELAX NG elements, so the local name alone identifies a component and the
   compiler skips a namespace lookup per child. */
static int rng_local_is(th_node *node, const char *local) {
    if (node->type != TH_NODE_ELEMENT) {
        return 0;
    }
    const Py_UCS4 *name;
    const Py_UCS4 *prefix;
    Py_ssize_t name_len = 0;
    Py_ssize_t prefix_len = 0;
    split_prefix(node->text, node->text_len, &name, &name_len, &prefix, &prefix_len);
    return u_eq_ascii(name, name_len, local);
}

/* An include or externalRef that names a resource; one without href stays for the 4.5 missing-href rejection. */
static int rng_is_reference(th_schema *schema, th_node *node) {
    return (is_schema_el(schema, node, RNG_NS, "include") || is_schema_el(schema, node, RNG_NS, "externalRef")) &&
           attr_exact(schema->tree, node, "href", 4) != NULL;
}

/* Section 4.1 removes annotation subtrees before pattern and name-class construction. */
static void rng_prune_annotations(th_schema *schema, th_node *container) {
    th_node *child = container->first_child;
    while (child != NULL) {
        th_node *next = child->next_sibling;
        if (child->type == TH_NODE_ELEMENT) {
            qname name = schema_direct_qname(schema, child);
            if (!u_eq_ascii(name.uri, name.uri_len, RNG_NS)) {
                th_node_remove(child);
            } else {
                /* the length picks the one name to compare: include, attribute or externalRef */
                switch (name.local_len) {
                case 7:
                case 11:
                    schema->rng_references |= (u_eq_ascii(name.local, name.local_len, "include") ||
                                               u_eq_ascii(name.local, name.local_len, "externalRef")) &&
                                              attr_exact(schema->tree, child, "href", 4) != NULL;
                    break;
                case 9:
                    schema->rng_attributes |= u_eq_ascii(name.local, 9, "attribute");
                    break;
                default:
                    break;
                }
                rng_prune_annotations(schema, child);
            }
        }
        child = next;
    }
}

/* Section 4.18 resolves names before 4.19 removes unused definitions. */
static int rng_check_unused_refs(th_schema *schema, th_node *container) {
    for (th_node *child = container->first_child; child != NULL; child = child->next_sibling) {
        if (child->type != TH_NODE_ELEMENT) {
            continue;
        }
        if (is_schema_el(schema, child, RNG_NS, "ref")) {
            const th_node_attr *name = attr_exact(schema->tree, child, "name", 4);
            if (name == NULL) {
                PyErr_SetString(PyExc_ValueError, "RELAX NG <ref> is missing the required name attribute");
                return -1;
            }
            if (def_find(&schema->defines, name->value, name->value_len) < 0) {
                PyErr_SetString(PyExc_ValueError, "RELAX NG <ref> has no matching define");
                return -1;
            }
        }
        rng_check_href(child);
        if (PyErr_Occurred() || rng_check_unused_refs(schema, child) < 0) {
            return -1;
        }
    }
    return 0;
}

static RNG_FORCEINLINE int rng_scan_node(th_schema *schema, th_node *node, int depth, nodevec *pending);

/* Checks 4.10, 4.19 and 7.4 on reachable patterns only: an unreachable <define> is never built, so it can neither
   crash nor blow up, and dead definitions add no compile cost. The walk follows each <ref> into its define and stops
   at an <element>, left in `pending` for a walk of its own: a derivative descends no further either, so the level
   `pending->peak` records, counted through every <ref>, bounds the recursion of both. A container raises it to the
   level of its pattern children, which an empty one has none of. -1 is a failure. */
static int rng_scan(th_schema *schema, th_node *container, int depth, nodevec *pending) {
    for (th_node *child = container->first_child; child != NULL; child = child->next_sibling) {
        if (child->type != TH_NODE_ELEMENT) {
            continue;
        }
        if (depth >= pending->peak) {
            pending->peak = depth + 1;
        }
        if (rng_scan_node(schema, child, depth, pending) < 0) {
            return -1;
        }
    }
    return 0;
}

enum { RNG_SCAN_OTHER, RNG_SCAN_REF, RNG_SCAN_INTERLEAVE, RNG_SCAN_ELEMENT };

static RNG_FORCEINLINE int rng_scan_kind(th_node *node);
static RNG_NOINLINE int rng_scan_ref(th_schema *schema, th_node *ref, int depth, nodevec *pending);
static RNG_FORCEINLINE int nodevec_push(nodevec *vec, th_node *node);

static RNG_FORCEINLINE int rng_local_name_is(const Py_UCS4 *name, Py_ssize_t len, const char *local,
                                             Py_ssize_t local_len);

static RNG_FORCEINLINE int rng_scan_node(th_schema *schema, th_node *node, int depth, nodevec *pending) {
    switch (rng_scan_kind(node)) {
    case RNG_SCAN_REF:
        return rng_scan_ref(schema, node, depth + 1, pending);
    case RNG_SCAN_INTERLEAVE:
        return rng_check_interleave_node(schema, node) < 0 ? -1 : rng_scan(schema, node, depth + 1, pending);
    case RNG_SCAN_ELEMENT:
        return nodevec_push(pending, node);
    default:
        pending->attrs |= schema->rng_attributes && rng_local_name_is(node->text, node->text_len, "attribute", 9);
        return rng_scan(schema, node, depth + 1, pending);
    }
}

static int rng_scan_define(th_schema *schema, Py_ssize_t def_index, int depth, nodevec *pending);

static RNG_NOINLINE int rng_scan_ref(th_schema *schema, th_node *ref, int depth, nodevec *pending) {
    const th_node_attr *name = attr_exact(schema->tree, ref, "name", 4);
    if (name == NULL) {
        PyErr_SetString(PyExc_ValueError, "RELAX NG <ref> is missing the required name attribute");
        return -1;
    }
    Py_ssize_t index = def_find(&schema->defines, name->value, name->value_len);
    if (index < 0) {
        PyErr_SetString(PyExc_ValueError, "RELAX NG <ref> has no matching define");
        return -1;
    }
    int height = rng_scan_define(schema, index, depth, pending);
    if (height < 0) {
        return -1;
    }
    if (depth + height > pending->peak) {
        pending->peak = depth + height;
    }
    return 0;
}

/* Whether the local part of `name` (after its first colon, as split_prefix splits it) is `local`. Matching the end
   first leaves the colon search to the few names that end in `local` and are longer. */
static RNG_FORCEINLINE int rng_local_name_is(const Py_UCS4 *name, Py_ssize_t len, const char *local,
                                             Py_ssize_t local_len) {
    if (len < local_len || !u_eq_ascii(name + len - local_len, local_len, local)) {
        return 0;
    }
    Py_ssize_t colon = len - local_len - 1;
    if (colon < 0) {
        return 1;
    }
    for (Py_ssize_t index = 0; index < colon; index++) {
        if (name[index] == ':') {
            return 0;
        }
    }
    return name[colon] == ':';
}

static RNG_FORCEINLINE int rng_scan_kind(th_node *node) {
    if (rng_local_name_is(node->text, node->text_len, "ref", 3)) {
        return RNG_SCAN_REF;
    }
    if (rng_local_name_is(node->text, node->text_len, "interleave", 10)) {
        return RNG_SCAN_INTERLEAVE;
    }
    if (rng_local_name_is(node->text, node->text_len, "element", 7)) {
        return RNG_SCAN_ELEMENT;
    }
    return RNG_SCAN_OTHER;
}

/* RELAX NG 4.19: meeting a define again while its expansion is under way is a ref loop with no <element> between,
   which libxml2 ("Detected a cycle in %s references") and jing/MSV reject too. A define expands once; a later <ref>
   adds the nesting recorded then to its own, so a chain of defines cannot nest what the derivatives descend deeper
   than the TH_VALIDATE_MAX_DEPTH the schema tree itself is held to. */
static int rng_scan_define(th_schema *schema, Py_ssize_t def_index, int depth, nodevec *pending) {
    def_entry *entry = &schema->defines.items[def_index];
    char buffer[256];
    if (entry->scan_height == -2) {
        PyErr_Format(PyExc_ValueError, "RELAX NG define '%s' references itself with no element in between",
                     name_utf8(entry->name, entry->len, buffer, sizeof(buffer)));
        return -1;
    }
    if (entry->scan_height == -1 && depth <= TH_VALIDATE_MAX_DEPTH) {
        entry->scan_height = -2;
        if (rng_check_combine(schema, entry) < 0) {
            return -1;
        }
        int outer = pending->peak;
        int outer_attrs = pending->attrs;
        pending->peak = depth;
        pending->attrs = 0;
        int scanned = rng_scan(schema, entry->first, depth, pending);
        for (def_part *part = entry->extra; scanned == 0 && part != NULL; part = part->next) {
            scanned = rng_scan(schema, part->node, depth, pending);
        }
        if (scanned < 0) {
            return -1;
        }
        int height = pending->peak - depth;
        pending->peak = outer;
        entry->scan_height = height;
        entry->scan_attrs = pending->attrs;
        pending->attrs = outer_attrs;
    }
    if (entry->scan_height < 0 || depth + entry->scan_height > TH_VALIDATE_MAX_DEPTH) {
        PyErr_Format(PyExc_RecursionError,
                     "RELAX NG <ref> to '%s' nests patterns %d levels or deeper with no element in between",
                     name_utf8(entry->name, entry->len, buffer, sizeof(buffer)), TH_VALIDATE_MAX_DEPTH);
        return -1;
    }
    pending->attrs |= entry->scan_attrs;
    return entry->scan_height;
}

/* A longer queue moves to the heap, not the schema arena: it lives only as long as the scan. */
static RNG_NOINLINE int nodevec_grow_push(nodevec *vec, th_node *node) {
    int local = vec->items == vec->local;
    size_t bytes = (size_t)vec->cap * 2 * sizeof(th_node *);
    th_node **items = local ? PyMem_Malloc(bytes) : PyMem_Realloc(vec->items, bytes);
    if (items == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
        return -1;        /* GCOVR_EXCL_LINE */
    }
    if (local) {
        memcpy(items, vec->local, (size_t)vec->len * sizeof(th_node *));
    }
    vec->items = items;
    vec->cap *= 2;
    vec->items[vec->len++] = node;
    return 0;
}

static RNG_FORCEINLINE int nodevec_push(nodevec *vec, th_node *node) {
    if (vec->len == vec->cap) {
        return nodevec_grow_push(vec, node);
    }
    vec->items[vec->len++] = node;
    return 0;
}

/* RELAX NG 7.4 compares the names each interleave branch matches. A branch keeps two persistent treaps: its exact
   names, keyed by hash, and its wildcard and choice name classes, keyed by a hash of their structure. A define's set
   is built once and shared by every ref. Equal nodes are shared, so equal sets have one root. A merge copies the
   smaller side into the larger. Work is charged to the schema's budget: a name class comparison by the classes it
   may visit, a pair of classes by half the square of their combined size, since the overlap test visits each leaf
   against both classes. Treap nodes are capped separately, so memory stays bounded however the budget is spent. */
#define RNG_INTERLEAVE_WORK_LIMIT (1 << 25)
#define RNG_INTERLEAVE_NODE_LIMIT (1 << 21)

static int nc_overlap(const nameclass *left, const nameclass *right, Py_ssize_t *work);
static int nc_name_eq(const nameclass *left, const nameclass *right);
static int nc_holds(const nameclass *nc, const nameclass *probe, Py_ssize_t *work);
static nameclass *rng_pattern_nameclass(th_schema *schema, th_node *node, int is_attr);
static int rng_names_node(th_schema *schema, th_node *node, nameset *out);
static int rng_names_define(th_schema *schema, def_entry *entry, nameset *out);
static int rng_names_interleave(th_schema *schema, th_node *interleave, nameset *out, int keep);
static def_entry *rng_names_ref_define(th_schema *schema, th_node *node);

/* One unit is a treap node visited or created, a tree node walked, a checked-interleave probe, or a name class
   comparison's size. The limit leaves room for the 20,000-interleave grammars the tests build, and it bounds the nodes
   a hostile schema can make the sets allocate. */
static int rng_spend(th_schema *schema, Py_ssize_t units) {
    schema->rng_work += units;
    if (schema->rng_work <= RNG_INTERLEAVE_WORK_LIMIT) {
        return 0;
    }
    PyErr_SetString(PyExc_ValueError, "RELAX NG grammar too large to check interleave branches");
    return -1;
}

/* Size of a name class: its choice and except nodes, each comparison against it is charged this. */
static Py_ssize_t nc_size(const nameclass *nc) {
    Py_ssize_t size = 1;
    for (; nc->type == NC_CHOICE || nc->type == NC_EXCEPT; nc = nc->a) {
        size += 1 + nc_size(nc->b);
    }
    return size;
}

/* A wildcard or choice class's hash covers its structure, so the treap built over them is the same on every run. */
static uint64_t nc_struct_hash(const nameclass *nc) {
    uint64_t hash = 0;
    for (; nc->type == NC_CHOICE || nc->type == NC_EXCEPT; nc = nc->a) {
        hash = (hash ^ nc_struct_hash(nc->b)) * UINT64_C(0x100000001B3) + (uint64_t)nc->type;
    }
    uint64_t leaf = nc->type == NC_NS ? def_hash(nc->uri, nc->uri_len) : nc->type == NC_NAME ? nc->hash : 0;
    return (hash ^ leaf) * UINT64_C(0x100000001B3) + (uint64_t)nc->type;
}

static uint64_t ns_key(const nameclass *nc) {
    return nc->hash;
}

static uint64_t ns_priority(uint64_t key);

/* A wildcard's priority mixes in its sequence number, so classes of one structure do not line up in a list. */
static uint64_t ns_node_priority(const nameclass *nc) {
    uint64_t key = ns_key(nc);
    return ns_priority(nc->type == NC_NAME ? key : key ^ (nc->seq * UINT64_C(0x9E3779B97F4A7C15)));
}

static uint64_t ns_priority(uint64_t key) {
    uint64_t mix = (key ^ (key >> 30)) * UINT64_C(0xBF58476D1CE4E5B9);
    mix = (mix ^ (mix >> 27)) * UINT64_C(0x94D049BB133111EB);
    return mix ^ (mix >> 31);
}

/* GCOVR_EXCL_START: two distinct names with equal 64-bit hashes cannot be built from a schema */
static int nc_text_order(const Py_UCS4 *left, Py_ssize_t left_len, const Py_UCS4 *right, Py_ssize_t right_len) {
    Py_ssize_t common = left_len < right_len ? left_len : right_len;
    for (Py_ssize_t index = 0; index < common; index++) {
        if (left[index] != right[index]) {
            return left[index] < right[index] ? -1 : 1;
        }
    }
    return (left_len > right_len) - (left_len < right_len);
}
/* GCOVR_EXCL_STOP */

/* Orders a class against a treap node by key. Two exact names with one hash are told apart by their text; equal
   wildcard keys are two classes of one structure, told apart by sequence number, so one class is one entry. */
static int ns_order(const nameclass *name, const nsnode *node) {
    uint64_t key = ns_key(name), other = ns_key(node->name);
    if (key != other) {
        return key < other ? -1 : 1;
    }
    if (name->type != NC_NAME) {
        return name == node->name ? 0 : (name->seq < node->name->seq ? -1 : 1);
    }
    if (nc_name_eq(name, node->name)) { /* GCOVR_EXCL_BR_LINE: unequal names need a hash collision */
        return 0;
    }
    /* GCOVR_EXCL_START: a 64-bit hash collision of two different names cannot be built from a schema */
    int order = nc_text_order(name->uri, name->uri_len, node->name->uri, node->name->uri_len);
    return order != 0 ? order : nc_text_order(name->local, name->local_len, node->name->local, node->name->local_len);
    /* GCOVR_EXCL_STOP */
}

static nsnode *ns_alloc(th_schema *schema) {
    schema->rng_nodes++;
    /* GCOVR_EXCL_START: needs about two million distinct nodes, beyond test-sized schemas */
    if (schema->rng_nodes > RNG_INTERLEAVE_NODE_LIMIT) {
        PyErr_SetString(PyExc_ValueError, "RELAX NG grammar too large to check interleave branches");
        return NULL;
    }
    /* GCOVR_EXCL_STOP */
    nsnode *node = arena_alloc(&schema->names_mem, sizeof(*node));
    if (node == NULL) {   /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
        return NULL;      /* GCOVR_EXCL_LINE */
    }
    node->id = (uint64_t)schema->rng_nodes;
    return node;
}

/* 1 when the class is in the treap, 0 when not, -1 on a budget or arena failure. */
/* A comparison of two exact names reads their text when their hashes agree, so it costs their length. */
static int ns_name_spend(th_schema *schema, const nameclass *name, const nameclass *other) {
    if (name->type != NC_NAME || other->hash != name->hash) {
        return 0;
    }
    return rng_spend(schema, name->local_len + name->uri_len);
}

static int ns_contains(th_schema *schema, const nsnode *node, const nameclass *name) {
    while (node != NULL) {
        if (rng_spend(schema, 1) < 0) {
            return -1;
        }
        if (ns_name_spend(schema, name, node->name) < 0) { /* GCOVR_EXCL_BR_LINE: name compare budget */
            return -1;                                     /* GCOVR_EXCL_LINE */
        }
        int order = ns_order(name, node);
        if (order == 0) {
            return 1;
        }
        node = order < 0 ? node->left : node->right;
    }
    return 0;
}

/* Splits the treap into the nodes ordered before `name` and the rest, copying the path it takes. */
static Py_ssize_t ns_size(const nsnode *node) {
    return node == NULL ? 0 : node->size;
}

static Py_ssize_t ns_count(const nameset *set) {
    return ns_size(set->names.root) + ns_size(set->wild.root);
}

/* Two classes name the same member of a set: equal exact names, or one wildcard class. */
static int ns_same(const nameclass *left, const nameclass *right) {
    if (left->type == NC_NAME && right->type == NC_NAME) {
        return nc_name_eq(left, right);
    }
    return left == right;
}

/* A wildcard is told apart by its sequence number, not by its structure: classes of one structure share a key, and
   hashing the key alone would put every such singleton in one probe run. */
static size_t ns_table_slot(const nameclass *name, const nsnode *left, const nsnode *right, size_t mask) {
    uint64_t identity = name->type == NC_NAME ? name->hash : name->seq;
    uint64_t hash = identity * UINT64_C(0x9E3779B97F4A7C15);
    hash ^= (left == NULL ? 0 : left->id) * UINT64_C(0xBF58476D1CE4E5B9);
    hash ^= (right == NULL ? 0 : right->id) * UINT64_C(0x94D049BB133111EB);
    hash ^= hash >> 31;
    hash *= UINT64_C(0xD6E8FEB86659FD93);
    return (size_t)(hash ^ (hash >> 29)) & mask;
}

static int ns_table_grow(th_schema *schema) {
    size_t cap = schema->rng_table_cap ? schema->rng_table_cap * 2 : 16;
    nsnode **table = arena_alloc(&schema->names_mem, cap * sizeof(nsnode *));
    if (table == NULL) {  /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
        return -1;        /* GCOVR_EXCL_LINE */
    }
    memset(table, 0, cap * sizeof(nsnode *));
    for (size_t index = 0; index < schema->rng_table_cap; index++) {
        nsnode *node = schema->rng_table[index];
        if (node == NULL) {
            continue;
        }
        size_t slot = ns_table_slot(node->name, node->left, node->right, cap - 1);
        while (table[slot] != NULL) {
            slot = (slot + 1) & (cap - 1);
        }
        table[slot] = node;
    }
    schema->rng_table = table;
    schema->rng_table_cap = cap;
    return 0;
}

/* The canonical node for `name` over these children. An equal node already built is returned, so equal treaps share
   one root and a union meets shared subtrees as the same pointer. */
static nsnode *ns_make(th_schema *schema, const nameclass *name, nsnode *left, nsnode *right) {
    TH_WORK_STEP();
    if (rng_spend(schema, 1) < 0) { /* GCOVR_EXCL_BR_LINE: budget exhausted in make; reachable, hard to pin */
        return NULL;                /* GCOVR_EXCL_LINE */
    }
    /* GCOVR_EXCL_BR_START: arena OOM is unforceable */
    if (schema->rng_table_len * 2 >= schema->rng_table_cap && ns_table_grow(schema) < 0) {
        return NULL; /* GCOVR_EXCL_LINE */
    }
    /* GCOVR_EXCL_BR_STOP */
    size_t mask = schema->rng_table_cap - 1;
    size_t slot = ns_table_slot(name, left, right, mask);
    while (schema->rng_table[slot] != NULL) {
        nsnode *node = schema->rng_table[slot];
        if (ns_name_spend(schema, name, node->name) < 0) { /* GCOVR_EXCL_BR_LINE: name compare budget */
            return NULL;                                   /* GCOVR_EXCL_LINE */
        }
        if (node->left == left && node->right == right && ns_same(node->name, name)) {
            return node;
        }
        slot = (slot + 1) & mask;
    }
    nsnode *node = ns_alloc(schema);
    if (node == NULL) { /* GCOVR_EXCL_BR_LINE: the node cap or arena OOM, see ns_alloc */
        return NULL;    /* GCOVR_EXCL_LINE */
    }
    node->name = name;
    node->left = left;
    node->right = right;
    node->size = 1 + ns_size(left) + ns_size(right);
    schema->rng_table[slot] = node;
    schema->rng_table_len++;
    return node;
}

/* Splits the treap at `name` into the nodes ordered before it and those ordered after. A node equal to `name` is
   dropped, which lets a union split the other side without keeping its duplicate. */
static int ns_split(th_schema *schema, nsnode *node, const nameclass *name, nsnode **below, nsnode **above) {
    TH_WORK_STEP();
    if (node == NULL) {
        *below = NULL;
        *above = NULL;
        return 0;
    }
    if (rng_spend(schema, 1) < 0) { /* GCOVR_EXCL_BR_LINE: budget exhausted in a split; reachable, hard to pin */
        return -1;                  /* GCOVR_EXCL_LINE */
    }
    int order = ns_order(name, node);
    if (order == 0) {
        if (ns_name_spend(schema, name, node->name) < 0) { /* GCOVR_EXCL_BR_LINE: name compare budget */
            return -1;                                     /* GCOVR_EXCL_LINE */
        }
        *below = node->left;
        *above = node->right;
        return 0;
    }
    nsnode *inner = NULL;
    if (order > 0) {
        if (ns_split(schema, node->right, name, &inner, above) < 0) { /* GCOVR_EXCL_BR_LINE: deeper split */
            return -1;                                                /* GCOVR_EXCL_LINE */
        }
        nsnode *kept = ns_make(schema, node->name, node->left, inner);
        if (kept == NULL) { /* GCOVR_EXCL_BR_LINE: budget in make, see ns_make */
            return -1;      /* GCOVR_EXCL_LINE */
        }
        *below = kept;
    } else {
        if (ns_split(schema, node->left, name, below, &inner) < 0) { /* GCOVR_EXCL_BR_LINE: deeper split */
            return -1;                                               /* GCOVR_EXCL_LINE */
        }
        nsnode *kept = ns_make(schema, node->name, inner, node->right);
        if (kept == NULL) { /* GCOVR_EXCL_BR_LINE: budget in make, see ns_make */
            return -1;      /* GCOVR_EXCL_LINE */
        }
        *above = kept;
    }
    return 0;
}

/* The union of two treaps as one canonical treap. A subtree both sides hold is the same node on each, so the recursion
   stops there: a union of sets that differ in a few names costs about the size of the difference. */
static int ns_union(th_schema *schema, nsnode *first, nsnode *second, nsnode **out) {
    TH_WORK_STEP();
    if (first == NULL) {
        *out = second;
        return 0;
    }
    if (second == NULL || first == second) {
        *out = first;
        return 0;
    }
    if (rng_spend(schema, 1) < 0) { /* GCOVR_EXCL_BR_LINE: budget exhausted in a union; reachable, hard to pin */
        return -1;                  /* GCOVR_EXCL_LINE */
    }
    if (ns_node_priority(first->name) < ns_node_priority(second->name)) {
        nsnode *swap = first;
        first = second;
        second = swap;
    }
    nsnode *below = NULL, *above = NULL;
    if (ns_split(schema, second, first->name, &below, &above) < 0) { /* GCOVR_EXCL_BR_LINE: budget in a split */
        return -1;                                                   /* GCOVR_EXCL_LINE */
    }
    nsnode *left = NULL, *right = NULL;
    if (ns_union(schema, first->left, below, &left) < 0) { /* GCOVR_EXCL_BR_LINE: deeper union */
        return -1;                                         /* GCOVR_EXCL_LINE */
    }
    if (ns_union(schema, first->right, above, &right) < 0) { /* GCOVR_EXCL_BR_LINE: deeper union */
        return -1;                                           /* GCOVR_EXCL_LINE */
    }
    nsnode *joined = ns_make(schema, first->name, left, right);
    if (joined == NULL) { /* GCOVR_EXCL_BR_LINE: budget in make, see ns_make */
        return -1;        /* GCOVR_EXCL_LINE */
    }
    *out = joined;
    return 0;
}

typedef int (*ns_visit)(th_schema *schema, const nsnode *node, void *context);

/* Visits the treap in order. A visit returns 0 to go on, 1 to stop with a conflict, -1 on failure. */
static int ns_each(th_schema *schema, const nsnode *node, ns_visit visit, void *context) {
    if (node == NULL) {
        return 0;
    }
    if (rng_spend(schema, 1) < 0) { /* GCOVR_EXCL_BR_LINE: budget exhausted in a walk; reachable, hard to pin */
        return -1;                  /* GCOVR_EXCL_LINE */
    }
    int status = ns_each(schema, node->left, visit, context);
    if (status == 0) {
        status = visit(schema, node, context);
    }
    if (status == 0) {
        status = ns_each(schema, node->right, visit, context);
    }
    return status;
}

/* A class held as the probe of a walk: its name for an exact class, the wildcard for a wildcard class. */
struct nc_probe {
    const nameclass *nc;
};

static int ns_wild_holds_visit(th_schema *schema, const nsnode *node, void *context) {
    const struct nc_probe *probe = context;
    if (rng_spend(schema, nc_size(node->name) + nc_size(probe->nc)) < 0) {
        return -1;
    }
    Py_ssize_t work = 0;
    int holds = nc_holds(node->name, probe->nc, &work);
    if (rng_spend(schema, work) < 0) { /* GCOVR_EXCL_BR_LINE: budget in a name compare; reachable, hard to pin */
        return -1;                     /* GCOVR_EXCL_LINE */
    }
    return holds;
}

static int ns_exact_holds_visit(th_schema *schema, const nsnode *node, void *context) {
    const struct nc_probe *probe = context;
    if (rng_spend(schema, nc_size(probe->nc) + nc_size(node->name)) < 0) {
        return -1;
    }
    Py_ssize_t work = 0;
    int holds = nc_holds(probe->nc, node->name, &work);
    if (rng_spend(schema, work) < 0) { /* GCOVR_EXCL_BR_LINE: budget in a name compare; reachable, hard to pin */
        return -1;                     /* GCOVR_EXCL_LINE */
    }
    return holds;
}

static int ns_wild_pair_visit(th_schema *schema, const nsnode *node, void *context) {
    const struct nc_probe *probe = context;
    Py_ssize_t size = nc_size(probe->nc) + nc_size(node->name);
    if (rng_spend(schema, size * size / 2) < 0) {
        return -1;
    }
    Py_ssize_t work = 0;
    int overlap = nc_overlap(probe->nc, node->name, &work);
    if (rng_spend(schema, work) < 0) { /* GCOVR_EXCL_BR_LINE: budget in a name compare; reachable, hard to pin */
        return -1;                     /* GCOVR_EXCL_LINE */
    }
    return overlap;
}

/* An exact name of the smaller side against the larger side: the same name, or a wildcard that holds it. */
static int ns_exact_visit(th_schema *schema, const nsnode *node, void *context) {
    const nameset *large = context;
    int status = ns_contains(schema, large->names.root, node->name);
    if (status != 0) {
        return status;
    }
    struct nc_probe probe = {node->name};
    return ns_each(schema, large->wild.root, ns_wild_holds_visit, &probe);
}

/* A wildcard of the smaller side against the larger side's exact names and wildcards. */
static int ns_wild_side_visit(th_schema *schema, const nsnode *node, void *context) {
    const nameset *large = context;
    struct nc_probe probe = {node->name};
    int status = ns_each(schema, large->names.root, ns_exact_holds_visit, &probe);
    if (status != 0) {
        return status;
    }
    return ns_each(schema, large->wild.root, ns_wild_pair_visit, &probe);
}

/* Whether a name belongs to both sets: exact names of the smaller side are looked up in the larger one and tested
   against its wildcards, and its wildcards are tested against the larger side's exact names and wildcards. 1 on an
   overlap, 0 when none, -1 on failure. */
static int rng_names_conflict(th_schema *schema, nameset *small, nameset *large) {
    int status = ns_each(schema, small->names.root, ns_exact_visit, large);
    if (status != 0) {
        return status;
    }
    return ns_each(schema, small->wild.root, ns_wild_side_visit, large);
}

/* Whether the conflict test already found these two sets disjoint. The verdict depends only on the sets, so a shared
   pair of defines is tested once however many interleaves join it. */
static int rng_names_known_disjoint(const th_schema *schema, const nameset *small, const nameset *large) {
    nsnode *roots[4] = {small->names.root, small->wild.root, large->names.root, large->wild.root};
    for (size_t index = 0; index < schema->rng_disjoint_len; index++) {
        if (memcmp(schema->rng_disjoint[index].roots, roots, sizeof(roots)) == 0) {
            return 1;
        }
    }
    return 0;
}

static void rng_names_remember_disjoint(th_schema *schema, const nameset *small, const nameset *large) {
    nspair *pair = &schema->rng_disjoint[schema->rng_disjoint_next];
    nsnode *roots[4] = {small->names.root, small->wild.root, large->names.root, large->wild.root};
    memcpy(pair->roots, roots, sizeof(roots));
    schema->rng_disjoint_next = (schema->rng_disjoint_next + 1) % 8;
    if (schema->rng_disjoint_len < 8) {
        schema->rng_disjoint_len++;
    }
}

/* Folds `other` into `acc`, the smaller side into the larger. An interleave (`joined`) rejects a text or name the two
   share; a choice or group just unites them. A merge whose result is discarded (`keep` 0) checks and builds nothing.
   `attrs` names the clash for attribute sets, where a group also joins its branches. */
static int rng_names_merge(th_schema *schema, nameset *acc, nameset *other, int joined, int keep, int attrs) {
    nameset left = *acc;
    nameset right = *other;
    nameset small = right;
    nameset large = left;
    if (ns_count(&left) < ns_count(&right)) {
        small = left;
        large = right;
    }
    if (joined) {
        if (small.text && large.text) {
            PyErr_SetString(PyExc_ValueError, "RELAX NG interleave has text in more than one branch");
            return -1;
        }
        if (!rng_names_known_disjoint(schema, &small, &large)) {
            int status = rng_names_conflict(schema, &small, &large);
            if (status < 0) {
                return -1;
            }
            if (status > 0) {
                PyErr_SetString(PyExc_ValueError,
                                attrs ? "RELAX NG group or interleave has attributes whose names overlap"
                                      : "RELAX NG interleave has the same element name in more than one branch");
                return -1;
            }
            rng_names_remember_disjoint(schema, &small, &large);
        }
    }
    if (!keep) {
        return 0;
    }
    nameset merged = {0};
    merged.text = left.text | right.text;
    /* GCOVR_EXCL_BR_START: budget in a union, reachable and hard to pin */
    if (ns_union(schema, left.names.root, right.names.root, &merged.names.root) < 0) {
        return -1; /* GCOVR_EXCL_LINE */
    }
    if (ns_union(schema, left.wild.root, right.wild.root, &merged.wild.root) < 0) {
        return -1; /* GCOVR_EXCL_LINE */
    }
    /* GCOVR_EXCL_BR_STOP */
    *acc = merged;
    return 0;
}

static nsnode *rng_names_single(th_schema *schema, const nameclass *nc, nstree *tree) {
    nsnode *root = ns_make(schema, nc, NULL, NULL);
    if (root == NULL) { /* GCOVR_EXCL_BR_LINE: the node cap or the budget, see ns_alloc */
        return NULL;    /* GCOVR_EXCL_LINE */
    }
    tree->root = root;
    return root;
}

/* Checked interleaves: a table of the interleave nodes whose branches already passed the 7.4 check, so an enclosing
   set computation and the scan do not check one twice. */
static size_t rng_checked_slot(const th_node *node, size_t mask) {
    return (size_t)((uint64_t)node->seq * UINT64_C(0x9E3779B97F4A7C15) >> 32) & mask;
}

static int rng_is_checked(th_schema *schema, const th_node *node) {
    if (schema->rng_checked_cap == 0) {
        return 0;
    }
    size_t mask = schema->rng_checked_cap - 1;
    for (size_t slot = rng_checked_slot(node, mask); schema->rng_checked[slot] != NULL; slot = (slot + 1) & mask) {
        if (rng_spend(schema, 1) < 0) { /* GCOVR_EXCL_BR_LINE: budget while walking a node, hard to pin */
            return -1;                  /* GCOVR_EXCL_LINE */
        }
        if (schema->rng_checked[slot] == node) {
            return 1;
        }
    }
    return 0;
}

static int rng_mark_checked(th_schema *schema, th_node *node) {
    if ((schema->rng_checked_len + 1) * 2 > schema->rng_checked_cap) {
        size_t cap = schema->rng_checked_cap ? schema->rng_checked_cap * 2 : 16;
        th_node **table = arena_alloc(&schema->names_mem, cap * sizeof(th_node *));
        if (table == NULL) {  /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
            return -1;        /* GCOVR_EXCL_LINE */
        }
        memset(table, 0, cap * sizeof(th_node *));
        th_node **old = schema->rng_checked;
        size_t old_cap = schema->rng_checked_cap;
        schema->rng_checked = table;
        schema->rng_checked_cap = cap;
        for (size_t index = 0; index < old_cap; index++) {
            if (old[index] == NULL) {
                continue;
            }
            if (rng_spend(schema, 1) < 0) { /* GCOVR_EXCL_BR_LINE: budget exhausted while growing the checked table */
                return -1;                  /* GCOVR_EXCL_LINE */
            }
            size_t slot = rng_checked_slot(old[index], cap - 1);
            while (table[slot] != NULL) {
                slot = (slot + 1) & (cap - 1);
            }
            table[slot] = old[index];
        }
    }
    if (rng_spend(schema, 1) < 0) { /* GCOVR_EXCL_BR_LINE: budget exhausted while adding a checked entry */
        return -1;                  /* GCOVR_EXCL_LINE */
    }
    size_t slot = rng_checked_slot(node, schema->rng_checked_cap - 1);
    while (schema->rng_checked[slot] != NULL) {
        slot = (slot + 1) & (schema->rng_checked_cap - 1);
    }
    schema->rng_checked[slot] = node;
    schema->rng_checked_len++;
    return 0;
}

/* A choice of plain names is a set of exact names, so it is compared like one. */
static int nc_all_named(const nameclass *nc) {
    for (; nc->type == NC_CHOICE; nc = nc->a) {
        if (!nc_all_named(nc->b)) {
            return 0;
        }
    }
    return nc->type == NC_NAME;
}

static Py_ssize_t nc_choice_count(const nameclass *nc) {
    Py_ssize_t count = 0;
    for (; nc->type == NC_CHOICE; nc = nc->a) {
        count += nc_choice_count(nc->b);
    }
    return count + 1;
}

static Py_ssize_t nc_choice_collect(const nameclass *nc, const nameclass **names, Py_ssize_t index) {
    for (; nc->type == NC_CHOICE; nc = nc->a) {
        index = nc_choice_collect(nc->b, names, index);
    }
    names[index] = nc;
    return index + 1;
}

/* Orders exact names by hash, then by namespace and local name, so equal names are adjacent. */
static int nc_name_order(const void *left, const void *right) {
    const nameclass *first = *(const nameclass *const *)left;
    const nameclass *second = *(const nameclass *const *)right;
    if (first->hash != second->hash) {
        return first->hash < second->hash ? -1 : 1;
    }
    int order = nc_text_order(first->uri, first->uri_len, second->uri, second->uri_len);
    if (order != 0) { /* GCOVR_EXCL_BR_LINE: unequal names with equal hashes need a 64-bit collision */
        return order; /* GCOVR_EXCL_LINE */
    }
    return nc_text_order(first->local, first->local_len, second->local, second->local_len);
}

/* The treap over sorted, distinct names: names[index] with the subtrees at left[index] and right[index]. Each node is
   made through ns_make, so the result is the canonical treap of the set. */
static int rng_names_build(th_schema *schema, const nameclass **names, const Py_ssize_t *left, const Py_ssize_t *right,
                           Py_ssize_t index, nsnode **out) {
    if (index < 0) {
        *out = NULL;
        return 0;
    }
    nsnode *below = NULL, *above = NULL;
    if (rng_names_build(schema, names, left, right, left[index], &below) < 0) { /* GCOVR_EXCL_BR_LINE: deeper build */
        return -1;                                                              /* GCOVR_EXCL_LINE */
    }
    if (rng_names_build(schema, names, left, right, right[index], &above) < 0) { /* GCOVR_EXCL_BR_LINE: deeper build */
        return -1;                                                               /* GCOVR_EXCL_LINE */
    }
    nsnode *node = ns_make(schema, names[index], below, above);
    if (node == NULL) { /* GCOVR_EXCL_BR_LINE: budget or node cap, see ns_make */
        return -1;      /* GCOVR_EXCL_LINE */
    }
    *out = node;
    return 0;
}

/* The set of sorted names: one sort and one pass over the names, not one union per name. */
static int rng_names_sorted(th_schema *schema, const nameclass **names, Py_ssize_t total, Py_ssize_t *links,
                            nsnode **root) {
    Py_ssize_t bits = 1;
    for (Py_ssize_t rest = total; rest > 1; rest >>= 1) {
        bits++;
    }
    if (rng_spend(schema, total * bits) < 0) { /* GCOVR_EXCL_BR_LINE: budget in the sort, hard to pin */
        return -1;                             /* GCOVR_EXCL_LINE */
    }
    qsort(names, (size_t)total, sizeof(*names), nc_name_order);
    Py_ssize_t count = 1;
    for (Py_ssize_t index = 1; index < total; index++) {
        if (ns_name_spend(schema, names[count - 1], names[index]) < 0) { /* GCOVR_EXCL_BR_LINE: name compare budget */
            return -1;                                                   /* GCOVR_EXCL_LINE */
        }
        if (nc_name_order(&names[count - 1], &names[index]) != 0) {
            names[count++] = names[index];
        }
    }
    Py_ssize_t *left = links, *right = links + count, *stack = links + 2 * count;
    Py_ssize_t top = 0;
    for (Py_ssize_t index = 0; index < count; index++) {
        Py_ssize_t last = -1;
        left[index] = -1;
        right[index] = -1;
        while (top > 0 && ns_node_priority(names[stack[top - 1]]) < ns_node_priority(names[index])) {
            last = stack[--top];
        }
        left[index] = last;
        if (top > 0) {
            right[stack[top - 1]] = index;
        }
        stack[top++] = index;
    }
    return rng_names_build(schema, names, left, right, stack[0], root);
}

/* The exact names of a choice of plain names, as one set. */
static int rng_names_from_choice(th_schema *schema, const nameclass *nc, nsnode **root) {
    Py_ssize_t total = nc_choice_count(nc);
    const nameclass **names = PyMem_Malloc((size_t)total * sizeof(*names));
    Py_ssize_t *links = PyMem_Malloc(3 * (size_t)total * sizeof(*links));
    if (names == NULL || links == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyMem_Free(names);                /* GCOVR_EXCL_LINE */
        PyMem_Free(links);                /* GCOVR_EXCL_LINE */
        PyErr_NoMemory();                 /* GCOVR_EXCL_LINE */
        return -1;                        /* GCOVR_EXCL_LINE */
    }
    nc_choice_collect(nc, names, 0);
    int status = rng_names_sorted(schema, names, total, links, root);
    PyMem_Free(names);
    PyMem_Free(links);
    return status;
}

/* An <element>'s content is a new level and an <attribute>'s text is not interleave text. A <ref> adds its define's
   set, which is built once. Every node walked is charged, so a name-free group costs as much as one name. */
static int rng_names_node(th_schema *schema, th_node *node, nameset *out) {
    if (rng_spend(schema, 1) < 0) { /* GCOVR_EXCL_BR_LINE: budget exhausted while walking a node, hard to pin */
        return -1;                  /* GCOVR_EXCL_LINE */
    }
    if (is_schema_el(schema, node, RNG_NS, "element")) {
        nameclass *nc = rng_pattern_nameclass(schema, node, 0);
        if (nc == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM */
            return -1;    /* GCOVR_EXCL_LINE */
        }
        if (nc->type == NC_NAME) {
            return rng_names_single(schema, nc, &out->names) == NULL ? -1 : 0; /* GCOVR_EXCL_BR_LINE: arena OOM */
        }
        if (rng_spend(schema, nc_size(nc)) < 0) { /* GCOVR_EXCL_BR_LINE: budget while hashing a class, hard to pin */
            return -1;                            /* GCOVR_EXCL_LINE */
        }
        if (nc_all_named(nc)) {
            return rng_names_from_choice(schema, nc, &out->names.root) < 0 ? -1 : 0; /* GCOVR_EXCL_BR_LINE: see above */
        }
        nc->hash = nc_struct_hash(nc);
        nc->seq = (uint64_t)schema->rng_nodes;
        return rng_names_single(schema, nc, &out->wild) == NULL ? -1 : 0; /* GCOVR_EXCL_BR_LINE: arena OOM */
    }
    if (is_schema_el(schema, node, RNG_NS, "text")) {
        out->text = 1;
        return 0;
    }
    if (is_schema_el(schema, node, RNG_NS, "attribute")) {
        return 0;
    }
    if (is_schema_el(schema, node, RNG_NS, "ref")) {
        def_entry *target = rng_names_ref_define(schema, node);
        /* 4.10: the scan or the build rejects a ref that names no define */
        if (target == NULL) {
            return 0;
        }
        return rng_names_define(schema, target, out);
    }
    if (is_schema_el(schema, node, RNG_NS, "interleave")) {
        return rng_names_interleave(schema, node, out, 1);
    }
    if (is_schema_el(schema, node, RNG_NS, "mixed")) { /* 4.13: an interleave of its content with <text/> */
        out->text = 1;
    }
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        if (child->type != TH_NODE_ELEMENT) {
            continue;
        }
        nameset next = {0};
        if (rng_names_node(schema, child, &next) < 0 || rng_names_merge(schema, out, &next, 0, 1, 0) < 0) {
            return -1;
        }
    }
    return 0;
}

/* The names of the branches of an interleave, checked against each other as they are folded in. The last branch's
   merge is kept only when the caller needs the set, and the interleave is marked checked either way. */
static int rng_names_interleave(th_schema *schema, th_node *interleave, nameset *out, int keep) {
    th_node *last = NULL;
    for (th_node *branch = interleave->first_child; branch != NULL; branch = branch->next_sibling) {
        if (branch->type == TH_NODE_ELEMENT) {
            last = branch;
        }
    }
    for (th_node *branch = interleave->first_child; branch != NULL; branch = branch->next_sibling) {
        if (branch->type != TH_NODE_ELEMENT) {
            continue;
        }
        nameset next = {0};
        if (rng_names_node(schema, branch, &next) < 0 ||
            rng_names_merge(schema, out, &next, 1, keep || branch != last, 0) < 0) {
            return -1;
        }
    }
    return rng_mark_checked(schema, interleave);
}

/* A define's set joins its components like rng_combine does: when any of them combines by interleave, each is checked
   against those before it. A define on the DFS stack adds nothing; the scan rejects that cycle. */
static int rng_names_body(th_schema *schema, def_entry *entry) {
    nameset acc = {0};
    if (rng_names_node(schema, entry->first, &acc) < 0) {
        return -1;
    }
    int joined = rng_combines_by_interleave(schema, entry->first);
    for (def_part *part = entry->extra; part != NULL; part = part->next) {
        joined |= rng_combines_by_interleave(schema, part->node);
    }
    for (def_part *part = entry->extra; part != NULL; part = part->next) {
        nameset next = {0};
        if (rng_names_node(schema, part->node, &next) < 0 || rng_names_merge(schema, &acc, &next, joined, 1, 0) < 0) {
            return -1;
        }
    }
    entry->names = acc;
    entry->names_state = 2;
    return 0;
}

/* The define a ref names, or NULL when no define has that name. */
static def_entry *rng_names_ref_define(th_schema *schema, th_node *node) {
    const th_node_attr *name = attr_exact(schema->tree, node, "name", 4);
    Py_ssize_t index = name == NULL ? -1 : def_find(&schema->defines, name->value, name->value_len);
    return index < 0 ? NULL : &schema->defines.items[index];
}

/* The refs in a node's content, in the order rng_names_node reaches them. Writes them to out when out is not NULL,
   and returns the index after the last one. */
static Py_ssize_t rng_names_refs(th_schema *schema, th_node *node, def_entry **out, Py_ssize_t index) {
    if (is_schema_el(schema, node, RNG_NS, "element") || is_schema_el(schema, node, RNG_NS, "attribute")) {
        return index;
    }
    if (is_schema_el(schema, node, RNG_NS, "ref")) {
        def_entry *target = rng_names_ref_define(schema, node);
        if (target == NULL) {
            return index;
        }
        if (out != NULL) {
            out[index] = target;
        }
        return index + 1;
    }
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        if (child->type == TH_NODE_ELEMENT) {
            index = rng_names_refs(schema, child, out, index);
        }
    }
    return index;
}

typedef struct rng_names_frame {
    struct rng_names_frame *parent;
    def_entry *entry;
    def_entry **refs;
    Py_ssize_t count, next;
} rng_names_frame;

/* Puts a define on the stack with the refs of its components, which are sized in one pass and filled in another. */
static rng_names_frame *rng_names_push(th_schema *schema, rng_names_frame *parent, def_entry *entry) {
    Py_ssize_t count = rng_names_refs(schema, entry->first, NULL, 0);
    for (def_part *part = entry->extra; part != NULL; part = part->next) {
        count = rng_names_refs(schema, part->node, NULL, count);
    }
    rng_names_frame *frame = arena_alloc(&schema->mem, sizeof(*frame));
    def_entry **refs = arena_alloc(&schema->mem, (size_t)count * sizeof(*refs));
    if (frame == NULL || refs == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return NULL;                     /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t index = rng_names_refs(schema, entry->first, refs, 0);
    for (def_part *part = entry->extra; part != NULL; part = part->next) {
        index = rng_names_refs(schema, part->node, refs, index);
    }
    entry->names_state = 1;
    frame->parent = parent;
    frame->entry = entry;
    frame->refs = refs;
    frame->count = count;
    frame->next = 0;
    return frame;
}

/* Builds the sets of root and of the defines it reaches, each after the defines it refers to. The stack is in the
   arena, so a long chain of refs costs memory and not C stack, which a deep grammar would otherwise exhaust. */
static int rng_names_ensure(th_schema *schema, def_entry *root) {
    rng_names_frame *top = rng_names_push(schema, NULL, root);
    if (top == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return -1;     /* GCOVR_EXCL_LINE */
    }
    while (top != NULL) {
        if (top->next < top->count) {
            def_entry *child = top->refs[top->next++];
            if (child->names_state == 0) {
                rng_names_frame *frame = rng_names_push(schema, top, child);
                if (frame == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
                    return -1;       /* GCOVR_EXCL_LINE */
                }
                top = frame;
            }
            continue;
        }
        if (rng_names_body(schema, top->entry) < 0) {
            return -1;
        }
        top = top->parent;
    }
    return 0;
}

/* A ref to a define takes its set, building it first if it is not built yet. */
static int rng_names_define(th_schema *schema, def_entry *entry, nameset *out) {
    if (entry->names_state == 0 && rng_names_ensure(schema, entry) < 0) {
        return -1;
    }
    if (entry->names_state == 2) {
        *out = entry->names;
    }
    return 0;
}

/* RELAX NG 7.4: an ambiguous interleave drives the derivative into exponential memory; libxml2 ("Element or text
   conflicts in interleave") and jing/MSV reject it too. */
static int rng_check_interleave_node(th_schema *schema, th_node *interleave) {
    int checked = rng_is_checked(schema, interleave);
    if (checked != 0) {
        return checked < 0 ? -1 : 0; /* GCOVR_EXCL_BR_LINE: a checked-table probe failed on the budget */
    }
    nameset names = {0};
    return rng_names_interleave(schema, interleave, &names, 0);
}

/* The interleave rng_combine makes is under 7.4 like a written one: when any component of the name says interleave,
   every component is a branch against those before it. */
static int rng_check_combine(th_schema *schema, def_entry *entry) {
    if (entry->extra == NULL) {
        return 0;
    }
    nameset names = {0};
    return rng_names_define(schema, entry, &names);
}

/* RELAX NG 7.3 for the attributes an element's content can hold. A group or interleave may not join two attributes
   whose name classes overlap, and an anyName or nsName attribute must sit under oneOrMore or zeroOrMore inside the
   element that owns it, since an element opens a new attribute scope. A pattern's attributes stay a short list of
   nodes, and two plain names are compared by hash and text; a wider class, or a list past RNG_ATTRS_FEW, moves into a
   name set, so a set is built only where attributes meet. A ref takes its define's record, built once. */
enum { RNG_ATTR_JOIN, RNG_ATTR_CHOICE, RNG_ATTR_REPEAT, RNG_ATTR_REF, RNG_ATTR_ATTRIBUTE, RNG_ATTR_ELEMENT };

/* Pruning left only RELAX NG elements, so the local name tells the kind; its length picks the one name to compare. */
static int rng_attr_kind(th_node *node) {
    const Py_UCS4 *local, *prefix;
    Py_ssize_t local_len = 0, prefix_len = 0;
    split_prefix(node->text, node->text_len, &local, &local_len, &prefix, &prefix_len);
    switch (local_len) {
    case 3:
        return u_eq_ascii(local, 3, "ref") ? RNG_ATTR_REF : RNG_ATTR_JOIN;
    case 6:
        return u_eq_ascii(local, 6, "choice") ? RNG_ATTR_CHOICE : RNG_ATTR_JOIN;
    case 7:
        return u_eq_ascii(local, 7, "element") ? RNG_ATTR_ELEMENT : RNG_ATTR_JOIN;
    case 9:
        if (u_eq_ascii(local, 9, "attribute")) {
            return RNG_ATTR_ATTRIBUTE;
        }
        return u_eq_ascii(local, 9, "oneOrMore") ? RNG_ATTR_REPEAT : RNG_ATTR_JOIN;
    case 10:
        return u_eq_ascii(local, 10, "zeroOrMore") ? RNG_ATTR_REPEAT : RNG_ATTR_JOIN;
    default:
        return RNG_ATTR_JOIN;
    }
}

static rng_attr_class *rng_attr_class_find(const th_schema *schema, const th_node *node) {
    if (schema->rng_attr_classes_cap == 0) {
        return NULL;
    }
    size_t mask = schema->rng_attr_classes_cap - 1;
    for (size_t slot = rng_checked_slot(node, mask); schema->rng_attr_classes[slot] != NULL; slot = (slot + 1) & mask) {
        if (schema->rng_attr_classes[slot]->node == node) {
            return schema->rng_attr_classes[slot];
        }
    }
    return NULL;
}

static nameclass *rng_attr_known_class(const th_schema *schema, const th_node *node) {
    rng_attr_class *entry = rng_attr_class_find(schema, node);
    return entry == NULL ? NULL : entry->nc;
}

static void rng_attr_class_put(rng_attr_class **table, size_t cap, rng_attr_class *entry) {
    size_t slot = rng_checked_slot(entry->node, cap - 1);
    while (table[slot] != NULL) {
        slot = (slot + 1) & (cap - 1);
    }
    table[slot] = entry;
}

/* The attribute's name class, built on first use. The table and classes live in `mem`, past the compile. */
static rng_attr_class *rng_attr_class_of(th_schema *schema, th_node *node) {
    rng_attr_class *entry = rng_attr_class_find(schema, node);
    if (entry != NULL) {
        return entry;
    }
    if ((schema->rng_attr_classes_len + 1) * 2 > schema->rng_attr_classes_cap) {
        size_t cap = schema->rng_attr_classes_cap ? schema->rng_attr_classes_cap * 2 : 16;
        rng_attr_class **table = arena_alloc(&schema->mem, cap * sizeof(*table));
        if (table == NULL) {  /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
            return NULL;      /* GCOVR_EXCL_LINE */
        }
        memset(table, 0, cap * sizeof(*table));
        for (size_t index = 0; index < schema->rng_attr_classes_cap; index++) {
            if (schema->rng_attr_classes[index] != NULL) {
                rng_attr_class_put(table, cap, schema->rng_attr_classes[index]);
            }
        }
        schema->rng_attr_classes = table;
        schema->rng_attr_classes_cap = cap;
    }
    entry = arena_alloc(&schema->mem, sizeof(*entry));
    if (entry == NULL) {  /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
        return NULL;      /* GCOVR_EXCL_LINE */
    }
    memset(entry, 0, sizeof(*entry));
    entry->node = node;
    entry->nc = rng_pattern_nameclass(schema, node, 1);
    if (entry->nc == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM */
        PyErr_NoMemory();    /* GCOVR_EXCL_LINE */
        return NULL;         /* GCOVR_EXCL_LINE */
    }
    rng_attr_class_put(schema->rng_attr_classes, schema->rng_attr_classes_cap, entry);
    schema->rng_attr_classes_len++;
    return entry;
}

/* The class as a name set, built once: an exact name, a choice of exact names, or a wildcard class keyed by its
   structure and the treap count, as rng_names_node keys an element's class. */
static int rng_attr_class_set(th_schema *schema, rng_attr_class *entry) {
    if (entry->has_set) {
        return 0;
    }
    nameclass *nc = entry->nc;
    if (nc->type == NC_NAME) {
        if (rng_names_single(schema, nc, &entry->set.names) == NULL) { /* GCOVR_EXCL_BR_LINE: budget or node cap */
            return -1;                                                 /* GCOVR_EXCL_LINE */
        }
    } else {
        if (rng_spend(schema, nc_size(nc)) < 0) { /* GCOVR_EXCL_BR_LINE: budget while hashing a class, hard to pin */
            return -1;                            /* GCOVR_EXCL_LINE */
        }
        if (nc_all_named(nc)) {
            /* GCOVR_EXCL_BR_START: budget or node cap, see rng_names_sorted */
            if (rng_names_from_choice(schema, nc, &entry->set.names.root) < 0) {
                return -1; /* GCOVR_EXCL_LINE */
            }
            /* GCOVR_EXCL_BR_STOP */
        } else {
            nc->hash = nc_struct_hash(nc);
            nc->seq = (uint64_t)schema->rng_nodes;
            if (rng_names_single(schema, nc, &entry->set.wild) == NULL) { /* GCOVR_EXCL_BR_LINE: budget or node cap */
                return -1;                                                /* GCOVR_EXCL_LINE */
            }
        }
    }
    entry->has_set = 1;
    return 0;
}

static int rng_attrs_empty(const rng_attrs *attrs) {
    return attrs->count == 0 && attrs->set.names.root == NULL && attrs->set.wild.root == NULL;
}

/* Moves the listed attributes into the set. */
static int rng_attrs_spill(th_schema *schema, rng_attrs *attrs) {
    for (int index = 0; index < attrs->count; index++) {
        rng_attr_class *entry = rng_attr_class_of(schema, attrs->few[index]);
        /* GCOVR_EXCL_BR_START: arena OOM, or the budget or node cap in a union, hard to pin */
        if (entry == NULL || rng_attr_class_set(schema, entry) < 0 ||
            rng_names_merge(schema, &attrs->set, &entry->set, 0, 1, 1) < 0) {
            return -1; /* GCOVR_EXCL_LINE */
        }
        /* GCOVR_EXCL_BR_STOP */
    }
    attrs->count = 0;
    return 0;
}

/* Compares two lists name by name: 1 when two names are equal, 0 when none is, 2 when a class is wider than one name
   and the sets decide, -1 on failure. */
static int rng_attrs_pairs(th_schema *schema, const rng_attrs *acc, const rng_attrs *next) {
    const nameclass *names[RNG_ATTRS_FEW];
    for (int index = 0; index < next->count; index++) {
        rng_attr_class *entry = rng_attr_class_of(schema, next->few[index]);
        if (entry == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM */
            return -1;       /* GCOVR_EXCL_LINE */
        }
        if (entry->nc->type != NC_NAME) {
            return 2;
        }
        names[index] = entry->nc;
    }
    for (int index = 0; index < acc->count; index++) {
        rng_attr_class *entry = rng_attr_class_of(schema, acc->few[index]);
        if (entry == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM */
            return -1;       /* GCOVR_EXCL_LINE */
        }
        const nameclass *name = entry->nc;
        if (name->type != NC_NAME) {
            return 2;
        }
        for (int other = 0; other < next->count; other++) {
            TH_WORK_STEP();
            if (name->hash != names[other]->hash) {
                continue;
            }
            if (rng_spend(schema, name->local_len + name->uri_len) < 0) { /* GCOVR_EXCL_BR_LINE: name compare budget */
                return -1;                                                /* GCOVR_EXCL_LINE */
            }
            if (nc_name_eq(name, names[other])) { /* GCOVR_EXCL_BR_LINE: unequal names need a 64-bit collision */
                return 1;
            }
        }
    }
    return 0;
}

/* Folds `next` into `acc`. A group or interleave (`joined`) first rejects a name the two share; a choice unites. */
static int rng_attrs_merge(th_schema *schema, rng_attrs *acc, rng_attrs *next, int joined) {
    if (rng_attrs_empty(next)) {
        return 0;
    }
    if (rng_attrs_empty(acc)) {
        *acc = *next;
        return 0;
    }
    int status = 2;
    if (acc->set.names.root == NULL && acc->set.wild.root == NULL && next->set.names.root == NULL &&
        next->set.wild.root == NULL) {
        status = joined ? rng_attrs_pairs(schema, acc, next) : 0;
        if (status == 1) {
            PyErr_SetString(PyExc_ValueError, "RELAX NG group or interleave has attributes whose names overlap");
            return -1;
        }
        if (status < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM or the name compare budget */
            return -1;    /* GCOVR_EXCL_LINE */
        }
        if (status == 0 && acc->count + next->count <= RNG_ATTRS_FEW) {
            memcpy(acc->few + acc->count, next->few, (size_t)next->count * sizeof(th_node *));
            acc->count += next->count;
            return 0;
        }
    }
    if (rng_attrs_spill(schema, acc) < 0 || rng_attrs_spill(schema, next) < 0) { /* GCOVR_EXCL_BR_LINE: see spill */
        return -1;                                                               /* GCOVR_EXCL_LINE */
    }
    return rng_names_merge(schema, &acc->set, &next->set, joined && status == 2, 1, 1);
}

static int rng_attr_walk(th_schema *schema, th_node *node, int kind, int more, rng_attrs *out, int *bad);

/* `out` starts empty and takes the attributes of each pattern child: joined, or united under a choice. */
static int rng_attr_children(th_schema *schema, th_node *node, int more, int joined, rng_attrs *out, int *bad) {
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        if (child->type != TH_NODE_ELEMENT) {
            continue;
        }
        int kind = rng_attr_kind(child);
        if (kind == RNG_ATTR_ELEMENT) {
            continue;
        }
        rng_attrs next;
        memset(&next, 0, sizeof(next));
        if (rng_attr_walk(schema, child, kind, more, &next, bad) < 0 ||
            rng_attrs_merge(schema, out, &next, joined) < 0) {
            return -1;
        }
    }
    return 0;
}

/* A define's record. Its components join the way its element names do: by interleave when any component says so. */
static int rng_attrs_body(th_schema *schema, def_entry *entry, def_attrs *record) {
    if (rng_attr_walk(schema, entry->first, RNG_ATTR_JOIN, 0, &record->attrs, &record->bad) < 0) {
        return -1;
    }
    if (entry->extra != NULL) {
        int joined = rng_combines_by_interleave(schema, entry->first);
        for (def_part *part = entry->extra; part != NULL; part = part->next) {
            joined |= rng_combines_by_interleave(schema, part->node);
        }
        for (def_part *part = entry->extra; part != NULL; part = part->next) {
            rng_attrs next;
            memset(&next, 0, sizeof(next));
            if (rng_attr_walk(schema, part->node, RNG_ATTR_JOIN, 0, &next, &record->bad) < 0 ||
                rng_attrs_merge(schema, &record->attrs, &next, joined) < 0) {
                return -1;
            }
        }
    }
    record->built = 1;
    return 0;
}

/* The scan has already rejected a ref that names no define, so a NULL target is a grammar-less schema's ref, which
   rng_build rejects. The check runs after the scan, so the defines a ref reaches hold no cycle. */
static int rng_attr_ref(th_schema *schema, th_node *ref, int more, rng_attrs *out, int *bad) {
    def_entry *target = rng_names_ref_define(schema, ref);
    if (target == NULL) {
        return 0;
    }
    if (schema->def_attrs_table == NULL) {
        size_t bytes = (size_t)schema->defines.len * sizeof(def_attrs);
        schema->def_attrs_table = arena_alloc(&schema->names_mem, bytes);
        if (schema->def_attrs_table == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            PyErr_NoMemory();                  /* GCOVR_EXCL_LINE */
            return -1;                         /* GCOVR_EXCL_LINE */
        }
        memset(schema->def_attrs_table, 0, bytes);
    }
    def_attrs *record = &schema->def_attrs_table[target - schema->defines.items];
    if (!record->built && rng_attrs_body(schema, target, record) < 0) {
        return -1;
    }
    *out = record->attrs;
    *bad |= !more && record->bad;
    return 0;
}

/* `bad` records an anyName or nsName attribute outside oneOrMore. A define keeps it, and only a scope that refs the
   define outside oneOrMore raises it. */
static int rng_attr_walk(th_schema *schema, th_node *node, int kind, int more, rng_attrs *out, int *bad) {
    switch (kind) {
    case RNG_ATTR_ATTRIBUTE:
        if (!more && attr_exact(schema->tree, node, "name", 4) == NULL) {
            rng_attr_class *entry = rng_attr_class_of(schema, node);
            if (entry == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM */
                return -1;       /* GCOVR_EXCL_LINE */
            }
            *bad |= !nc_all_named(entry->nc);
        }
        out->few[0] = node;
        out->count = 1;
        return 0;
    case RNG_ATTR_REF:
        return rng_attr_ref(schema, node, more, out, bad);
    default:
        return rng_attr_children(schema, node, more || kind == RNG_ATTR_REPEAT, kind != RNG_ATTR_CHOICE, out, bad);
    }
}

/* The content of an element is an implicit group, so its attributes are joined and checked here. */
static int rng_check_scope(th_schema *schema, th_node *element) {
    rng_attrs names;
    memset(&names, 0, sizeof(names));
    int bad = 0;
    if (rng_attr_children(schema, element, 0, 1, &names, &bad) < 0) {
        return -1;
    }
    if (bad) {
        PyErr_SetString(PyExc_ValueError,
                        "RELAX NG anyName or nsName attribute must be inside oneOrMore or zeroOrMore");
        return -1;
    }
    return 0;
}

/* Checks every element of a grammar-less schema, which rng_build alone reaches. */
static int rng_check_scopes(th_schema *schema, th_node *node) {
    if (rng_attr_kind(node) == RNG_ATTR_ELEMENT && rng_check_scope(schema, node) < 0) {
        return -1;
    }
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        if (child->type == TH_NODE_ELEMENT && rng_check_scopes(schema, child) < 0) {
            return -1;
        }
    }
    return 0;
}

/* mirrors how rng_build picks the name class */
static nameclass *rng_pattern_nameclass(th_schema *schema, th_node *node, int is_attr) {
    const th_node_attr *name = attr_exact(schema->tree, node, "name", 4);
    if (name != NULL) {
        return nc_from_qname(schema, node, name->value, name->value_len, is_attr);
    }
    return rng_build_nameclass(schema, first_element_child(node));
}

static int nc_name_eq(const nameclass *left, const nameclass *right) {
    /* GCOVR_EXCL_BR_START: equal hashes with a different namespace need a 64-bit collision */
    return u_eq_u(left->local, left->local_len, right->local, right->local_len) &&
           u_eq_u(left->uri, left->uri_len, right->uri, right->uri_len);
    /* GCOVR_EXCL_BR_STOP */
}

/* Whether `nc` holds the name leaf `probe` stands for: its own name for a <name>, a name nothing mentions in its
   namespace for an <nsName>, and a name in a namespace nothing mentions for an <anyName>. Loops down the left spine,
   which is as long as a <choice> has names. */
static int nc_holds(const nameclass *nc, const nameclass *probe, Py_ssize_t *work) {
    TH_WORK_STEP();
    for (;; nc = nc->a) {
        switch (nc->type) {
        case NC_ANY:
            return 1;
        case NC_NAME:
            if (probe->type != NC_NAME || nc->hash != probe->hash) {
                return 0;
            }
            *work += probe->local_len + probe->uri_len;
            return nc_name_eq(nc, probe);
        case NC_NS:
            if (probe->type == NC_ANY) {
                return 0;
            }
            if (nc->uri_len == probe->uri_len) {
                *work += nc->uri_len;
            }
            return u_eq_u(nc->uri, nc->uri_len, probe->uri, probe->uri_len);
        case NC_CHOICE:
            if (nc_holds(nc->b, probe, work)) {
                return 1;
            }
            break;
        default: /* NC_EXCEPT */
            if (nc_holds(nc->b, probe, work)) {
                return 0;
            }
            break;
        }
    }
}

/* Whether both classes hold the name some leaf under `part` stands for. */
static int nc_overlap_at(const nameclass *part, const nameclass *left, const nameclass *right, Py_ssize_t *work) {
    for (; part->type == NC_CHOICE || part->type == NC_EXCEPT; part = part->a) {
        if (nc_overlap_at(part->b, left, right, work)) {
            return 1;
        }
    }
    return nc_holds(left, part, work) && nc_holds(right, part, work);
}

/* Whether some name belongs to both classes. Membership only changes at a name or namespace that one of them mentions,
   so testing one representative per leaf decides it, as jing's OverlapDetector does. The comparisons read names, and
   their length is added to `work`. */
static int nc_overlap(const nameclass *left, const nameclass *right, Py_ssize_t *work) {
    TH_WORK_STEP();
    return nc_overlap_at(left, left, right, work) | nc_overlap_at(right, left, right, work);
}

static void rng_validate_root(valctx *ctx, th_node *root) {
    th_schema *schema = ctx->schema;
    ns_scope_enter_ancestors(schema, ctx->tree, root);
    patintern *intern = arena_alloc(&schema->mem, sizeof(*intern));
    if (intern == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        ctx->failed = 1;  /* GCOVR_EXCL_LINE */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
        return;           /* GCOVR_EXCL_LINE */
    }
    memset(intern, 0, sizeof(*intern));
    schema->intern = intern;
    /* On the stack, so a validation that never needs the memo lays out its arena patterns as before. */
    patmemo memos[2] = {{NULL, 0, 0, 1, NULL}, {NULL, 0, 0, 1, NULL}};
    schema->memo = schema->event_memo = &memos[0];
    schema->token_memo = &memos[1];
    /* Heuristic threshold: a walk longer than twice the schema pattern count probably revisits a node, so the
       free steps grow with the schema. Patterns made during validation are not counted. */
    schema->memo_free = MEMO_FREE_STEPS == 0 ? 0
                        : (Py_ssize_t)schema->pattern_count * 2 > MEMO_FREE_STEPS
                            ? (Py_ssize_t)schema->pattern_count * 2
                            : MEMO_FREE_STEPS;
    Py_ssize_t before = ctx->error_count;
    pattern *result = rng_child_element(ctx, schema->start, root, 0);
    if (!rng_nullable(schema, result) && ctx->error_count == before) {
        report(ctx, root, "structure", "document does not match the schema");
    }
}

#endif /* TURBOHTML_VALIDATE_RELAXNG_H */
