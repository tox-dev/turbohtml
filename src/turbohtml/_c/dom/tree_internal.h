/* Tree-builder internals shared between the parser (tree.c) and the standalone
   serialization translation units. The arena, the borrowed-input readers, the
   lazy text-span realization and the void-element predicate live here as
   `static inline` so every including unit keeps its own inlined copy -- the
   serialize hot path pays no cross-TU call for need_text() or is_void_atom(). */

#ifndef TURBOHTML_DOM_TREE_INTERNAL_H
#define TURBOHTML_DOM_TREE_INTERNAL_H

#include "core/node_map.h"
#include "dom/tree.h"

#include "core/fuzzing.h"
#include "core/hash.h" /* th_fnv1a_bytes for th_attr_lookup_inline */

#include <stddef.h>
#include <string.h>
#if defined(_MSC_VER)
#include <intrin.h>
#endif

#ifdef FUZZING_BUILD_MODE_UNSAFE_FOR_PRODUCTION
/* One aligned slot, so a short input opens several blocks and walks the doubling path. */
#define ARENA_BLOCK ((Py_ssize_t)16)
#define ARENA_SMALL_BLOCK ((Py_ssize_t)16)
#else
#define ARENA_BLOCK ((Py_ssize_t)64 * 1024)
/* The first block of a tree made for one constructed element or text node (see th_tree_new_small): it fits Python's
   small-object allocator, where such a tree, a few hundred bytes, would otherwise cost a 64 KiB allocation. */
#define ARENA_SMALL_BLOCK ((Py_ssize_t)(512 - sizeof(arena_block)))
#endif
#define ARENA_MAX_BLOCK ((Py_ssize_t)256 * 1024)

/* Keep the one-time span realization out of line so need_text() stays small
   enough to inline into the serialize/text walks (see the header comment). Its
   arena_alloc grew a geometric-growth branch, and inlining that whole cold path
   into need_text pushed the accessor past the caller's inline budget. */
#if defined(_MSC_VER)
#define TH_NOINLINE __declspec(noinline)
#elif defined(__GNUC__) || defined(__clang__)
#define TH_NOINLINE __attribute__((noinline))
#else
#define TH_NOINLINE
#endif

#if defined(__GNUC__) || defined(__clang__)
#define TH_MAYBE_UNUSED __attribute__((unused))
#else
#define TH_MAYBE_UNUSED
#endif

typedef struct arena_block {
    struct arena_block *next;
    Py_ssize_t used;
    Py_ssize_t cap;
    char data[];
} arena_block;

/* One interned dynamic attribute name (a name outside the static attr_atom.h
   table). The bytes are arena-owned UTF-8, NUL-terminated. */
typedef struct {
    const char *name;
    uint32_t name_len;
    uint32_t hash;
} th_attr_record;

/* One host<->shadow-root link recorded by attach_shadow. A shadow root is off the
   light tree (parent NULL), so the link is the only path between a host and its
   shadow root and back; the table is grown lazily and freed with the tree. */
typedef struct {
    th_node *host;
    th_node *root;
} th_shadow_link;

/* What maybe_clone_option needs from one select, kept while the tree is built:
   the enabled descendant selectedcontent elements (the clone destinations), the
   option the select shows, the optgroup option_is_disabled last looked at and whether
   it carries the disabled attribute, so the next option of that group skips the search. */
typedef struct {
    th_node *select;
    th_node **targets; /* the enabled descendant selectedcontent elements, the clone destinations */
    th_node *selected; /* the option the select shows, NULL while it has none */
    const th_node *optgroup;
    Py_ssize_t target_count;
    Py_ssize_t target_cap;
    uint8_t filled; /* leading targets holding the shown option's copy, at most MAX_CLONE_TARGETS */
    uint8_t multiple;
    uint8_t disabled; /* an option, selectedcontent or second select above the select disables its targets */
    uint8_t optgroup_disabled;
} th_select_cache;

/* The select caches of one parse, allocated when the first select gains a selectedcontent
   so a tree without one carries a single NULL pointer and its options skip the lookup. */
typedef struct {
    th_select_cache *caches;
    Py_ssize_t count;
    Py_ssize_t cap;
    th_node_map index; /* select node -> 1-based position in caches */
    /* the last select looked up and its index value (0 while it has no cache): its options and its selectedcontent
       ask for the same select in a row, so most lookups skip the hash probe */
    const th_node *last_select;
    Py_ssize_t last_slot;
    uint32_t fresh_seq; /* the tree's node_seq when the newest cache was made: no later node has a cache */
    uint8_t recheck;    /* a shown option may have left its select, so the finish walks every cache */
} th_select_state;

typedef struct th_attr_names th_attr_names;

struct xp_ns_index;
void xp_ns_index_free(struct xp_ns_index *index);

/* A set of TH_TAG_* atoms as a bitmask, for the scope searches that skip stack blocks holding none of their atoms. */
#define TH_ATOM_WORDS ((TH_TAG_XMP + 64) / 64)
typedef struct {
    uint64_t bits[TH_ATOM_WORDS];
} th_atom_set;

/* The open-element stack entries in one TH_MAX_TREE_DEPTH-sized block: how many of each atom they hold, and the atoms
   held at all. A search skips a block whose atoms miss its set, so a deep stack costs a search about one block per
   TH_MAX_TREE_DEPTH entries instead of one per entry. */
typedef struct {
    uint16_t count[TH_TAG_XMP + 1];
    th_atom_set present;
} th_stack_block;

struct th_tree {
    struct xp_ns_index *ns_index; /* the XPath namespace index of an XML tree, built on first namespaced query */
    arena_block *arena;
    th_node *document;
    th_node **open; /* stack of open elements */
    Py_ssize_t open_len;
    uint64_t stack_version, scope_version;
    uint16_t scope_atom;
    uint16_t ctx_atom; /* the fragment context element's atom (reset uses it) */
    int scope_result;
    int open_p_count;
    int open_templates; /* templates on the stack: each one's content fragment adds a DOM level */
    int depth_near_cap; /* the depth bound has reached the cap once; set and never cleared, so an insertion checks only
                           then */
    int depth_capped;   /* the DOM cap moved a node, so the tree is flatter than the markup it came from */
    Py_ssize_t stack_slack; /* entries removed from the middle of the stack: each can stay an ancestor of the elements
                               above it, one DOM level the stack no longer counts */
    Py_ssize_t open_cap;
    th_stack_block *open_blocks; /* per-block summaries of open, built once the stack passes one block; NULL before */
    Py_ssize_t open_block_cap;
    Py_ssize_t push_check_at; /* a push to this many entries or more runs the depth and block checks (see stack_push) */
    int stack_tracked;        /* a template or block summary is on the stack, so a pop checks them */
    Py_ssize_t nest_bound;    /* see th_tree_nest_bound; under TH_HAS_MEMO_DEPTH while no bound is known */
    th_node **afe;            /* active formatting elements; NULL entry is a scope marker */
    Py_ssize_t afe_len;
    uint64_t *afe_hashes;
    size_t afe_hash_count, afe_hash_capacity;
    uint8_t afe_hash_valid;
    uint8_t seq_wrapped; /* node_seq has wrapped to 0: a seq no longer numbers one node of the tree */
    int8_t source_ascii; /* data[0:length] is ASCII (1) or not (-1); 0 until a text read first needs it */
    uint32_t node_seq;   /* next value for node_new to stamp onto th_node::seq; fills the
                            padding after afe_hash_valid so th_tree stays <= 512 bytes */
    Py_ssize_t afe_cap;
    th_node *head;          /* the <head> element once inserted */
    th_node *fragment_root; /* the html root in fragment parsing; NULL otherwise */
    th_node *form;          /* the form element pointer: nested forms are ignored */
    int *tmpl;              /* stack of template insertion modes (enum mode as int) */
    Py_ssize_t tmpl_len;
    Py_ssize_t tmpl_cap;
    int frameset_ok;        /* a <frameset> may still replace an empty body */
    int drop_newline;       /* drop a single leading LF after pre/listing/textarea */
    Py_ssize_t text_offset; /* leading code points of a reprocessed text token already consumed */
    int foster;             /* when set, inserts are foster-parented out of a table */
    /* set on the rare tree-construction branches whose result its own serialization can re-parse differently (see
       th_tree_reparse_hazard); it fills the padding before the next pointer, so th_tree stays at 512 bytes */
    int reparse_hazard;
    /* the end-tag token currently being processed, or NULL; stack_pop flags the
       element it closes with TH_ELEM_CLOSED_BY_END_TAG so the sanitizer can tell a
       source-closed element from a parser-closed one */
    const th_token *closing_end_tag;
    int quirks;             /* quirks mode: a <table> no longer closes an open <p> */
    int scripting;          /* the WHATWG scripting flag: noscript is a rawtext element when set */
    int declarative_shadow; /* allow a <template shadowrootmode> to attach a shadow root to its parent */
    int has_nul;            /* the input contains a U+0000; otherwise text needs no NUL filtering */
    int xml;                /* parsed under XML rules: tag and attribute names are case-sensitive */
    int has_xml_ns;
    int has_prefix_decl; /* an attribute name xmlns:P was ever interned; canonicalization renders such declarations */
    int can_span;        /* input is borrowed and outlives the tree: text nodes may be
                             zero-copy spans into it instead of materialized copies */
    int track_positions; /* record each element's source line/col in trailing node slots */
    int track_locations; /* record each element's granular source spans (implies track_positions) */
    int kind;            /* borrowed input storage */
    int small_arena;     /* made by th_tree_new_small: the arena starts with an ARENA_SMALL_BLOCK */
    /* attribute slots copied onto formatting-element clones so far, which clone_formatting bounds; it fills the
       padding before data, so th_tree stays at 512 bytes */
    uint32_t clone_attr_slots;
    const void *data;
    void *owned_data; /* normalized one-shot input retained when source spans need it */
    Py_ssize_t length;
    int failed;              /* an allocation failed; abandon the parse */
    int input_errors_merged; /* set once the preprocessing errors are folded into errors */
    /* Dynamic intern table for attribute names outside attr_atom.h: the record
       for atom d is attr_recs[d - TH_ATTR__DYNAMIC_BASE]; attr_slots maps a name
       hash to its record. Grown only as uncommon names appear; read-only after
       the parse, so no lock is needed under the free-threaded build. */
    th_attr_record *attr_recs;
    uint32_t attr_rec_count;
    uint32_t attr_rec_cap;
    uint32_t *attr_slots; /* slot -> record index + 1; 0 marks an empty slot */
    uint64_t attr_version;
    uint64_t id_version;
    /* set while the handle keeps a slot cache, cleared by a write or removal of slot=, or of name= on a <slot>: the
       attribute edits that change slot assignment */
    int slot_cache_valid;
    uint32_t attr_slot_mask;
    /* attr_slots while the name table is on FNV-1a; NULL while it is not built and once it has moved to
       th_name_hash for good, so the one test the lookup made for an unbuilt table also sends a keyed one
       out of line */
    uint32_t *attr_fast;
    /* Encoding declarations from <meta> elements, in document order, so a bytes parse
       can run the WHATWG "changing the encoding while parsing" step once the tree is
       built. Grown lazily: a document without a <meta> charset never allocates. */
    th_meta_label *meta_labels;
    Py_ssize_t meta_label_count;
    Py_ssize_t meta_label_cap;
    /* Shadow roots attached through the mutation API, grown lazily by attach_shadow.
       Empty (NULL) for every parsed or shadow-free tree. */
    th_shadow_link *shadows;
    th_node_map shadow_index;
    Py_ssize_t shadow_count;
    Py_ssize_t shadow_cap;
    /* The registry of live MutationObservers watching this tree, allocated on the first
       observe() and freed with the last observer. Each observer is owned by its
       MutationObserver Python object (dom/observe.c), which holds a handle reference
       keeping the tree alive, so the registry is gone by the time the tree is freed.
       NULL for every observer-free tree. */
    struct th_mo_registry *observers;
    /* Live NodeIterators rooted in this tree, each owned by its NodeIterator Python
       object (dom/traversal.c), which holds a handle reference keeping the tree alive,
       so the array is empty by the time the tree is freed. NULL when none exist. */
    struct th_node_iterator **node_iterators;
    Py_ssize_t node_iterator_count;
    Py_ssize_t node_iterator_cap;
    /* Head of the doubly linked list of live Ranges whose boundaries sit in this tree (dom/range.c). Each Range owns
       its link and holds a handle reference keeping the tree alive, so the list is empty by the time the tree is freed.
       NULL when none exist, so every text edit and removal tests one pointer. */
    struct th_live_range *live_ranges;
    /* WHATWG parse errors collected during the parse, in document order. The
       tokenizer fills it through this sink while the tree builder adds its own
       construction errors. The preprocessing errors, which depend on the input alone,
       are folded in on the first read rather than at parse time: a document nobody
       asks for errors from should not pay to find them. */
    th_error_sink errors;
    /* Per-select state for cloning a selected option into selectedcontent, released when
       parsing closes. One pointer keeps th_tree at 512 bytes, inside pymalloc's
       small-object limit, so every parse still allocates its tree from pymalloc. */
    th_select_state *select_state;
    /* Attribute names of the elements merge_attrs searches by hash, allocated on the first such merge. */
    th_attr_names *attr_names;
    /* What a failed element construction hands the insertion modes in place of NULL: never linked into the
       document, it absorbs the rest of the failing token's pushes and edits until tree->failed ends the drain, so
       stack_push and the modes need no NULL checks. ctx_atom sits beside scope_atom to keep th_tree at 512 bytes. */
    th_node *sink;
};

static inline void *arena_alloc(th_tree *tree, Py_ssize_t size) {
#ifdef FUZZING_BUILD_MODE_UNSAFE_FOR_PRODUCTION
    Py_ssize_t requested = size;
    size += TH_FUZZ_ARENA_GAP;
#endif
    size = (size + 15) & ~(Py_ssize_t)15; /* 16-byte align */
    arena_block *block = tree->arena;
    if (block == NULL || block->used + size > block->cap) {
        Py_ssize_t cap = ARENA_BLOCK;
        if (tree->small_arena) {
            cap = ARENA_SMALL_BLOCK;
        }
        if (block != NULL) {
            cap = block->cap < ARENA_MAX_BLOCK ? block->cap * 2 : ARENA_MAX_BLOCK;
        }
        if (cap < size) {
            cap = size;
        }
        arena_block *fresh = PyMem_Malloc(sizeof(arena_block) + (size_t)cap);
        if (fresh == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            tree->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
            return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        }
        fresh->next = tree->arena;
        fresh->used = 0;
        fresh->cap = cap;
#ifdef FUZZING_BUILD_MODE_UNSAFE_FOR_PRODUCTION
        /* Poison the fresh block and unpoison each allocation, as lexbor's mraw does, so a read past one reports
           (https://github.com/lexbor/lexbor/blob/f4cbbcd91359a0ec9499e3ce7e263de629482d61/source/lexbor/core/mraw.c#L182-L217).
         */
        TH_FUZZ_POISON(fresh->data, (size_t)cap);
#endif
        tree->arena = fresh;
        block = fresh;
    }
    void *result = block->data + block->used;
    block->used += size;
#ifdef FUZZING_BUILD_MODE_UNSAFE_FOR_PRODUCTION
    TH_FUZZ_UNPOISON(result, (size_t)requested);
#endif
    return result;
}

/* Copy the input span [off, off+length) into a freshly arena-allocated UCS4
   array, widening the borrowed input to code points. Shared by slice
   materialization and the lazy realization of zero-copy text spans. */
static TH_NOINLINE TH_MAYBE_UNUSED Py_UCS4 *copy_input_span(th_tree *tree, Py_ssize_t off, Py_ssize_t length) {
    Py_UCS4 *out = arena_alloc(tree, length * (Py_ssize_t)sizeof(Py_UCS4));
    if (out == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;   /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    if (tree->kind == PyUnicode_4BYTE_KIND) {
        memcpy(out, (const Py_UCS4 *)tree->data + off, (size_t)length * sizeof(Py_UCS4));
    } else if (tree->kind == PyUnicode_1BYTE_KIND) {
        const uint8_t *bytes = (const uint8_t *)tree->data + off;
        for (Py_ssize_t index = 0; index < length; index++) {
            out[index] = bytes[index];
        }
    } else {
        const uint16_t *units = (const uint16_t *)tree->data + off;
        for (Py_ssize_t index = 0; index < length; index++) {
            out[index] = units[index];
        }
    }
    return out;
}

/* A parsed TEXT node tags its source offset into the otherwise-aligned text
   pointer. This leaves the element-only attrs fields outside its allocation. */
static inline int text_is_span(const th_node *node) {
    return node->type == TH_NODE_TEXT && ((uintptr_t)node->text & 1u) != 0;
}

static inline Py_ssize_t text_span_offset(const th_node *node) {
    return (Py_ssize_t)((uintptr_t)node->text >> 1);
}

static inline void text_set_span(th_node *node, Py_ssize_t offset) {
    uintptr_t encoded = ((uintptr_t)offset << 1) | 1u;
    _Static_assert(sizeof(node->text) == sizeof(encoded), "tagged text spans require uintptr_t-sized pointers");
    memcpy(&node->text, &encoded, sizeof(encoded));
}

static inline Py_UCS4 *need_text(th_tree *tree, th_node *node) {
    if (text_is_span(node)) {
        node->text = copy_input_span(tree, text_span_offset(node), node->text_len);
    }
    return node->text;
}

static inline int is_void_atom(uint16_t atom) {
    switch (atom) {
    case TH_TAG_AREA:
    case TH_TAG_BASE:
    case TH_TAG_BASEFONT:
    case TH_TAG_BGSOUND:
    case TH_TAG_BR:
    case TH_TAG_COL:
    case TH_TAG_EMBED:
    case TH_TAG_HR:
    case TH_TAG_IMG:
    case TH_TAG_INPUT:
    case TH_TAG_KEYGEN:
    case TH_TAG_LINK:
    case TH_TAG_META:
    case TH_TAG_PARAM:
    case TH_TAG_SOURCE:
    case TH_TAG_TRACK:
    case TH_TAG_WBR:
        return 1;
    default:
        return 0;
    }
}

/* HTML start tags that break out of foreign content back into HTML parsing. The list, and token_has_attr for the
   <font> attributes that do the same, are shared by the tree builder (foreign.h) and the streaming rewriter, which
   follows the foreign-content rules over its open-element spine. */
static inline int is_foreign_breakout(uint16_t atom) {
    switch (atom) {
    case TH_TAG_B:
    case TH_TAG_BIG:
    case TH_TAG_BLOCKQUOTE:
    case TH_TAG_BODY:
    case TH_TAG_BR:
    case TH_TAG_CENTER:
    case TH_TAG_CODE:
    case TH_TAG_DD:
    case TH_TAG_DIV:
    case TH_TAG_DL:
    case TH_TAG_DT:
    case TH_TAG_EM:
    case TH_TAG_EMBED:
    case TH_TAG_H1:
    case TH_TAG_H2:
    case TH_TAG_H3:
    case TH_TAG_H4:
    case TH_TAG_H5:
    case TH_TAG_H6:
    case TH_TAG_HEAD:
    case TH_TAG_HR:
    case TH_TAG_I:
    case TH_TAG_IMG:
    case TH_TAG_LI:
    case TH_TAG_LISTING:
    case TH_TAG_MENU:
    case TH_TAG_META:
    case TH_TAG_NOBR:
    case TH_TAG_OL:
    case TH_TAG_P:
    case TH_TAG_PRE:
    case TH_TAG_RUBY:
    case TH_TAG_S:
    case TH_TAG_SMALL:
    case TH_TAG_SPAN:
    case TH_TAG_STRONG:
    case TH_TAG_STRIKE:
    case TH_TAG_SUB:
    case TH_TAG_SUP:
    case TH_TAG_TABLE:
    case TH_TAG_TT:
    case TH_TAG_U:
    case TH_TAG_UL:
    case TH_TAG_VAR:
        return 1;
    default:
        return 0;
    }
}

static inline int token_has_attr(const th_token *token, const char *name) {
    Py_ssize_t nlen = (Py_ssize_t)strlen(name);
    for (Py_ssize_t index = 0; index < token->attr_count; index++) {
        const th_buf *aname = &token->attrs[index].name;
        if (aname->len == nlen && aname->kind == PyUnicode_1BYTE_KIND && memcmp(aname->data, name, (size_t)nlen) == 0) {
            return 1;
        }
    }
    return 0;
}

/* When the tree tracks positions, an element node carries its source line
   (1-based) and column (0-based) in two uint32 slots appended right after the
   struct, so the 80-byte node and the default arena are untouched without the
   feature and for every other node type. node_pos reaches them; only valid for an
   element of a position-tracking tree, where node_new reserved the space. A line
   of 0 marks "no source" (a synthetic element: implied html/head/body, a fragment
   root, or one built by hand), which the accessor reports as None. Shared by the
   parser (tree.c) and the construction/mutation API (mutate.c). */
static inline uint32_t *node_pos(th_node *node) {
    return (uint32_t *)((char *)node + sizeof(th_node));
}

/* A location-tracking element reserves one more trailing slot past the two
   position words: a th_src_loc pointer holding its granular spans (NULL until
   insert_element fills it, or for a synthetic element with no source tag). Only
   valid for an element of a location-tracking tree, where node_new reserved it. */
static inline th_src_loc **node_loc(th_node *node) {
    return (th_src_loc **)((char *)node + sizeof(th_node) + 2 * sizeof(uint32_t));
}

static inline th_node *node_new(th_tree *tree, enum th_node_type type) {
    int positioned = tree->track_positions && type == TH_NODE_ELEMENT;
    int located = tree->track_locations && type == TH_NODE_ELEMENT;
    const size_t fixed = type == TH_NODE_TEXT ? offsetof(th_node, attrs) : sizeof(th_node);
    th_node *node = arena_alloc(tree, (Py_ssize_t)fixed + (positioned ? 2 * (Py_ssize_t)sizeof(uint32_t) : 0) +
                                          (located ? (Py_ssize_t)sizeof(th_src_loc *) : 0));
    if (node == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    /* Zero only the fixed part: its size follows from the node type alone, a constant at
       most call sites, so the compiler inlines the stores instead of calling memset. The
       position and location slots are set below. */
    memset(node, 0, fixed);
    if (positioned) {
        node_pos(node)[0] = 0; /* line; 0 = no source until insert_element sets it */
        node_pos(node)[1] = 0; /* col */
    }
    if (located) {
        *node_loc(node) = NULL; /* filled by insert_element when the element has a source tag */
    }
    node->type = (uint8_t)type;
    node->atom = TH_TAG_UNKNOWN;
    node->seq = tree->node_seq++;
    tree->seq_wrapped |= tree->node_seq == 0;
    return node;
}

void th_node_mark_ancestors(th_node *node);

/* Inlining forced on a hot call site whose callee link-time optimization stopped inlining once the hash-flood bounds
   grew the code around it. */
#if defined(_MSC_VER)
#define TH_TREE_INLINE __forceinline
#elif defined(__OPTIMIZE__)
#define TH_TREE_INLINE inline __attribute__((always_inline))
#else
#define TH_TREE_INLINE inline
#endif

/* Forced into node_insert_before: insert_element appends every parsed node through it, and the inliner drops the
   call there once tree.c grows. node_append leaves the choice to the inliner, as the XML builder runs faster with
   its appends out of line. */
static TH_TREE_INLINE void node_append_inline(th_node *parent, th_node *child) {
    child->parent = parent;
    child->prev_sibling = parent->last_child;
    child->next_sibling = NULL;
    if (parent->last_child != NULL) {
        parent->last_child->next_sibling = child;
    } else {
        parent->first_child = child;
    }
    parent->last_child = child;
    if (child->reaches_selectedcontent) {
        th_node_mark_ancestors(parent);
    }
}

static inline void node_append(th_node *parent, th_node *child) {
    node_append_inline(parent, child);
}

/* th_tree_unlinks. Process-wide and atomic, since the unlink sites hold no tree, and trees of other threads and
   interpreters unlink concurrently. */
extern uint64_t th_unlinks;

/* Detach a node from its current parent (the adoption agency re-parents). */
static inline void node_remove(th_node *child) {
    th_node *parent = child->parent;
    if (parent == NULL) {
        return;
    }
#if defined(_MSC_VER)
    _InterlockedIncrement64((volatile __int64 *)&th_unlinks);
#else
    __atomic_fetch_add(&th_unlinks, 1, __ATOMIC_RELAXED);
#endif
    if (child->prev_sibling != NULL) {
        child->prev_sibling->next_sibling = child->next_sibling;
    } else {
        parent->first_child = child->next_sibling;
    }
    if (child->next_sibling != NULL) {
        child->next_sibling->prev_sibling = child->prev_sibling;
    } else {
        parent->last_child = child->prev_sibling;
    }
    child->parent = NULL;
    child->prev_sibling = NULL;
    child->next_sibling = NULL;
}

/* Insert child before ref among ref's siblings (ref->parent becomes the parent);
   ref==NULL appends to parent. */
static inline void node_insert_before(th_node *parent, th_node *child, th_node *ref) {
    if (ref == NULL) {
        node_append_inline(parent, child);
        return;
    }
    child->parent = parent;
    child->next_sibling = ref;
    child->prev_sibling = ref->prev_sibling;
    if (ref->prev_sibling != NULL) {
        ref->prev_sibling->next_sibling = child;
    } else {
        parent->first_child = child;
    }
    ref->prev_sibling = child;
    if (child->reaches_selectedcontent) {
        th_node_mark_ancestors(parent);
    }
}

/* The record index + 1 for an interned name, or 0 when it is not in the built table.
   The name comparison is by bytes, so a hash collision is not a special case. */
static inline uint32_t dynamic_slot(th_tree *tree, const char *bytes, Py_ssize_t len, uint32_t hash) {
    uint32_t mask = tree->attr_slot_mask;
    for (uint32_t probe = hash & mask; tree->attr_slots[probe] != 0; probe = (probe + 1) & mask) {
        TH_WORK_STEP();
        const th_attr_record *rec = &tree->attr_recs[tree->attr_slots[probe] - 1];
        if (rec->name_len == (uint32_t)len && memcmp(rec->name, bytes, (size_t)len) == 0) {
            return tree->attr_slots[probe];
        }
    }
    return 0;
}

/* intern_attr_dynamic's add of a name the table does not hold, hashed to hash under the table's current hash, and
   its step for a table that is not built yet or is on th_name_hash (dom/tree.c). */
uint32_t intern_attr_insert(th_tree *tree, const char *bytes, Py_ssize_t len, uint32_t hash);
uint32_t intern_attr_slow(th_tree *tree, const char *bytes, Py_ssize_t len);

/* Intern UTF-8 name bytes into the per-tree dynamic table, returning the atom. The parser (tree.c) and the
   construction API (mutate.c) share it; inline, as the keyed step grew the extern function past what link-time
   optimization inlines into node_attr_store. */
static inline uint32_t intern_attr_dynamic(th_tree *tree, const char *bytes, Py_ssize_t len) {
    if (tree->attr_fast == NULL) {
        return intern_attr_slow(tree, bytes, len);
    }
    uint32_t hash = th_fnv1a_bytes(bytes, len);
    uint32_t found = dynamic_slot(tree, bytes, len, hash);
    if (found != 0) {
        return TH_ATTR__DYNAMIC_BASE + (found - 1);
    }
    return intern_attr_insert(tree, bytes, len, hash);
}

/* th_attr_lookup for a table on th_name_hash (dom/tree.c). */
uint32_t th_attr_lookup_slow(th_tree *tree, const char *bytes, Py_ssize_t len);

/* th_attr_lookup with its FNV-1a step inline. th_node_attr_find resolves a name per
   element, and the keyed step grew the extern function past what link-time
   optimization inlines there. */
static inline uint32_t th_attr_lookup_inline(th_tree *tree, const char *bytes, Py_ssize_t len) {
    if (len == 0) {
        return UINT32_MAX;
    }
    uint32_t atom = th_attr_atom(bytes, (size_t)len);
    if (atom != TH_ATTR_UNKNOWN) {
        return atom;
    }
    /* hashed before the table test, so the hash loop follows the static-name scan without a branch between */
    uint32_t hash = th_fnv1a_bytes(bytes, len);
    if (tree->attr_fast == NULL) {
        return tree->attr_slots == NULL ? UINT32_MAX : th_attr_lookup_slow(tree, bytes, len);
    }
    uint32_t found = dynamic_slot(tree, bytes, len, hash);
    return found != 0 ? TH_ATTR__DYNAMIC_BASE + (found - 1) : UINT32_MAX;
}

#endif /* TURBOHTML_DOM_TREE_INTERNAL_H */
