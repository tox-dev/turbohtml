/* XSLT 1.0 transformation over the turbohtml DOM (issue #537).

   This reuses the existing XPath 1.0 engine (query/xpath) for every select
   expression and match pattern rather than growing a second path evaluator:
   the engine is the big lever, so this file is only the XSLT layer on top of
   it -- the stylesheet model, template conflict resolution, the instruction
   instantiation walk, and the result-tree serializer.

   A match pattern is tested by membership: the pattern is compiled once as an
   absolute location path and evaluated against the source document, and the set
   of nodes it selects is the set the rule matches. Conflict resolution then
   orders the matching rules by import precedence, then priority, then document
   position, exactly as XSLT 1.0 section 5.5 specifies. The XSLT-only functions
   (current, key, generate-id, format-number, system-property, ...) ride the
   XPath engine's extension hook, so the evaluator dispatches them without XSLT
   needing to touch the core function library. */

#include "core/common.h"
#include "core/hash.h"
#include "core/node_map.h"
#include "core/resource.h"
#include "core/stack.h"
#include "core/vec.h"
#include "core/work_steps.h"
#include "dom/nodes.h"
#include "dom/tree.h"
#include "tokenizer/binding.h"
#include "tokenizer/xml_names.h"
#include "query/xpath/internal.h"

#include <float.h>
#include <limits.h>
#include <math.h>
#include <stdint.h>
#include <string.h>

/* Inline every number_attr call: do_number's size otherwise tips the inliner into outlining the format lookups on the
   numbering hot path. The coverage build compiles at -O0, where a forced copy in each caller would split its branch
   counts, so it keeps one copy. */
#if defined(_MSC_VER)
#define XSLT_FORCEINLINE __forceinline
#elif defined(__OPTIMIZE__)
#define XSLT_FORCEINLINE inline __attribute__((always_inline))
#else
#define XSLT_FORCEINLINE inline
#endif

#ifndef TH_NOINLINE
#if defined(_MSC_VER)
#define TH_NOINLINE __declspec(noinline)
#elif defined(__GNUC__) || defined(__clang__)
#define TH_NOINLINE __attribute__((noinline))
#else
#define TH_NOINLINE
#endif
#endif

static PyObject *make_str(const Py_UCS4 *data, Py_ssize_t len) {
    return PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, data, len);
}

/* ---- small growable UCS-4 buffer ------------------------------------------ */

typedef struct {
    Py_UCS4 *data;
    Py_ssize_t len;
    Py_ssize_t cap;
} xb;

static int xb_reserve(xb *buf, Py_ssize_t extra) {
    if (buf->len + extra <= buf->cap) {
        return 0;
    }
    size_t cap;
    size_t bytes;
    /* Size overflow needs a length no allocation could hold. */
    int fits = th_grow_cap((size_t)buf->len + (size_t)extra, (size_t)buf->cap, 16, sizeof(Py_UCS4), &cap, &bytes);
    if (!fits) {   /* GCOVR_EXCL_BR_LINE */
        return -1; /* GCOVR_EXCL_LINE */
    }
    Py_UCS4 *grown = PyMem_Realloc(buf->data, bytes);
    if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
        return -1;       /* GCOVR_EXCL_LINE */
    }
    buf->data = grown;
    buf->cap = (Py_ssize_t)cap;
    return 0;
}

static int xb_add(xb *buf, const Py_UCS4 *src, Py_ssize_t len) {
    if (len == 0) {
        return 0;
    }
    if (xb_reserve(buf, len) < 0) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
        return -1;                  /* GCOVR_EXCL_LINE */
    }
    memcpy(buf->data + buf->len, src, (size_t)len * sizeof(Py_UCS4));
    buf->len += len;
    return 0;
}

static int xb_add_char(xb *buf, Py_UCS4 ch) {
    if (xb_reserve(buf, 1) < 0) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
        return -1;                /* GCOVR_EXCL_LINE */
    }
    buf->data[buf->len++] = ch;
    return 0;
}

static int xb_add_ascii(xb *buf, const char *src) {
    for (const char *cursor = src; *cursor != '\0'; cursor++) {
        if (xb_add_char(buf, (Py_UCS4)(unsigned char)*cursor) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;                                               /* GCOVR_EXCL_LINE */
        }
    }
    return 0;
}

static void xb_free(xb *buf) {
    PyMem_Free(buf->data);
    buf->data = NULL;
    buf->len = 0;
    buf->cap = 0;
}

/* ---- UTF-8 <-> UCS-4 name helpers ----------------------------------------- */

static Py_ssize_t write_utf8(const Py_UCS4 *src, Py_ssize_t len, char *out) {
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
    out[pos] = '\0';
    return pos;
}

static char *ucs4_to_utf8(const Py_UCS4 *src, Py_ssize_t len, Py_ssize_t *out_len) {
    char *out = PyMem_Malloc((size_t)len * 4 + 1);
    if (out == NULL) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
        return NULL;   /* GCOVR_EXCL_LINE */
    }
    *out_len = write_utf8(src, len, out);
    return out;
}

/* The interned tag atom for a result element name, so an HTML-method serialization
   treats a known void element (br, img, ...) correctly. TH_TAG_UNKNOWN for a name
   that is not a known HTML tag; the name is matched ASCII-case-insensitively. */
static uint16_t atom_for_name(const Py_UCS4 *name, Py_ssize_t len) {
    if (len > 64) {
        return TH_TAG_UNKNOWN;
    }
    char lowered[64];
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_UCS4 ch = name[index];
        if (ch >= 128) {
            return TH_TAG_UNKNOWN;
        }
        lowered[index] = (char)(ch >= 'A' && ch <= 'Z' ? ch + 32 : ch);
    }
    return th_tag_lookup(lowered, len);
}

/* Whether a code-point run begins with the ASCII keyword kw. A loop over kw rather
   than a chain of per-character && comparisons, so the prefix test is one branch. */
static int ucs4_has_prefix(const Py_UCS4 *src, Py_ssize_t len, const char *kw) {
    Py_ssize_t klen = (Py_ssize_t)strlen(kw);
    if (len < klen) { /* GCOVR_EXCL_START: the only caller passes len >= the longest keyword */
        return 0;
    } /* GCOVR_EXCL_STOP */
    for (Py_ssize_t index = 0; index < klen; index++) {
        if (src[index] != (Py_UCS4)(unsigned char)kw[index]) {
            return 0;
        }
    }
    return 1;
}

static int ucs4_ascii_eq(const Py_UCS4 *src, Py_ssize_t len, const char *kw) {
    Py_ssize_t index = 0;
    for (; index < len && kw[index] != '\0'; index++) {
        if (src[index] != (Py_UCS4)(unsigned char)kw[index]) {
            return 0;
        }
    }
    return index == len && kw[index] == '\0';
}

static int ucs4_is_ws(Py_UCS4 ch) {
    /* An array + loop instead of a chained ||, so the whitespace set is one covered
       branch rather than four fragile short-circuit arms clang inlines separately. */
    static const Py_UCS4 whitespace[] = {' ', '\t', '\r', '\n'};
    for (size_t index = 0; index < sizeof(whitespace) / sizeof(whitespace[0]); index++) {
        if (ch == whitespace[index]) {
            return 1;
        }
    }
    return 0;
}

static int ucs4_blank(const Py_UCS4 *src, Py_ssize_t len) {
    for (Py_ssize_t index = 0; index < len; index++) {
        if (!ucs4_is_ws(src[index])) {
            return 0;
        }
    }
    return 1;
}

/* Whether `name` appears in a whitespace-separated token list (the form of
   cdata-section-elements, extension-element-prefixes and the *-space element sets). */
static int name_in_token_list(const Py_UCS4 *list, Py_ssize_t list_len, const Py_UCS4 *name, Py_ssize_t name_len) {
    Py_ssize_t index = 0;
    while (index < list_len) {
        if (ucs4_is_ws(list[index])) {
            index++;
            continue;
        }
        Py_ssize_t start = index;
        while (index < list_len && !ucs4_is_ws(list[index])) {
            index++;
        }
        if (index - start == name_len && memcmp(list + start, name, (size_t)name_len * sizeof(Py_UCS4)) == 0) {
            return 1;
        }
    }
    return 0;
}

/* ---- match set: a set of (node, attr) items ------------------------------- */

typedef struct {
    const th_node *node;
    Py_ssize_t attr;
    int used;
} match_slot;

typedef struct {
    match_slot *slots;
    size_t cap;
    size_t count;
} match_set;

/* Hash a node by its creation sequence rather than its address, so match-set and
   dispatch-cache probe counts depend on the input document and not on where the
   allocator placed the tree. Scaling the attribute index by the MurmurHash3 fmix32
   multiplier keeps a node's attribute items off the sequences of the nodes after it. */
static uint32_t node_hash(const th_node *node, Py_ssize_t attr) {
    return th_fibonacci_hash(node->seq + (uint32_t)(attr + 1) * 0x85EBCA6BU);
}

/* The slot after `probe` in an item's probe sequence: the next one for the first TH_FAST_PROBE_LIMIT steps, then the
   keyed stride of th_node_probe_step, so source nodes numbered into one run of home slots do not all walk it. */
static size_t node_next(size_t probe, size_t walked, size_t mask, const th_node *node, Py_ssize_t attr, size_t salt) {
    TH_WORK_STEP();
    if (TH_UNLIKELY(walked >= TH_FAST_PROBE_LIMIT)) {
        return (probe + th_node_probe_step((((uint64_t)(attr + 1) << 32) | node->seq) ^ salt)) & mask;
    }
    return (probe + 1) & mask;
}

static int match_set_resize(match_set *set, size_t new_cap) {
    match_slot *slots = PyMem_Calloc(new_cap, sizeof(match_slot));
    if (slots == NULL) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
        return -1;       /* GCOVR_EXCL_LINE */
    }
    for (size_t index = 0; index < set->cap; index++) {
        if (set->slots[index].used) {
            size_t probe = th_hash_slot(node_hash(set->slots[index].node, set->slots[index].attr), new_cap);
            for (size_t walked = 0; slots[probe].used; walked++) {
                probe = node_next(probe, walked, new_cap - 1, set->slots[index].node, set->slots[index].attr, 0);
            }
            slots[probe] = set->slots[index];
        }
    }
    PyMem_Free(set->slots);
    set->slots = slots;
    set->cap = new_cap;
    return 0;
}

/* Size the set for `more` further items at once, so a set filled from one evaluation is allocated and zeroed once
   rather than at every doubling on the way. */
static int match_set_reserve(match_set *set, size_t more) {
    size_t cap = set->cap == 0 ? 16 : set->cap;
    while ((set->count + more) * 2 >= cap) {
        cap *= 2;
    }
    return cap == set->cap ? 0 : match_set_resize(set, cap);
}

static int match_set_add(match_set *set, const th_node *node, Py_ssize_t attr) {
    if ((set->count + 1) * 2 >= set->cap) {
        const size_t grown = set->cap == 0 ? 16 : set->cap * 2;
        if (match_set_resize(set, grown) < 0) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
            return -1;                          /* GCOVR_EXCL_LINE */
        }
    }
    size_t probe = th_hash_slot(node_hash(node, attr), set->cap);
    for (size_t walked = 0; set->slots[probe].used; walked++) {
        /* A rule's match set is built from one duplicate-free pattern evaluation, so the
           same item is never re-added; the dedup guard is defensive. */
        if (set->slots[probe].node == node && set->slots[probe].attr == attr) { /* GCOVR_EXCL_BR_LINE */
            return 0;                                                           /* GCOVR_EXCL_LINE */
        } /* GCOVR_EXCL_LINE */
        probe = node_next(probe, walked, set->cap - 1, node, attr, 0);
    }
    set->slots[probe].node = node;
    set->slots[probe].attr = attr;
    set->slots[probe].used = 1;
    set->count++;
    return 0;
}

XSLT_FORCEINLINE static int match_set_has(const match_set *set, const th_node *node, Py_ssize_t attr) {
    if (set->cap == 0) {
        return 0;
    }
    size_t probe = th_hash_slot(node_hash(node, attr), set->cap);
    for (size_t walked = 0; set->slots[probe].used; walked++) {
        if (set->slots[probe].node == node && set->slots[probe].attr == attr) {
            return 1;
        }
        probe = node_next(probe, walked, set->cap - 1, node, attr, 0);
    }
    return 0;
}

static void match_set_free(match_set *set) {
    PyMem_Free(set->slots);
    set->slots = NULL;
    set->cap = 0;
    set->count = 0;
}

/* ---- string -> node-vector map (key tables) ------------------------------- */

typedef struct {
    th_node *node;
    Py_ssize_t order;
} nodeslot;

typedef struct {
    nodeslot *slots;
    Py_ssize_t len;
    Py_ssize_t cap;
} nodevec;

/* order is the item's index in the key's match list, which build_key walks in document order. An element and its
   attribute share node, so order, not node, tells two items apart. */
static int nodevec_push(nodevec *vec, th_node *node, Py_ssize_t order) {
    /* build_key processes each source item's values together in document order. */
    if (vec->len > 0 && vec->slots[vec->len - 1].order == order) {
        return 0;
    }
    if (vec->len == vec->cap) {
        Py_ssize_t cap = vec->cap == 0 ? TH_INITIAL_CAPACITY(4) : vec->cap * 2;
        nodeslot *grown = PyMem_Realloc(vec->slots, (size_t)cap * sizeof(nodeslot));
        if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
            return -1;       /* GCOVR_EXCL_LINE */
        }
        vec->slots = grown;
        vec->cap = cap;
    }
    nodeslot *slot = &vec->slots[vec->len++];
    slot->node = node;
    slot->order = order;
    return 0;
}

typedef struct {
    Py_UCS4 *str;
    Py_ssize_t str_len;
    nodevec nodes;
    int used;
} strmap_slot;

typedef struct {
    strmap_slot *slots;
    size_t cap;
    size_t count;
    /* A str_hash map takes the inline paths while count < fast, one below half of cap. fast is 0 while the map is
       empty and once it moved to th_name_hash, so the one comparison the inline paths already made against the load
       limit also sends those maps out of line, and a map on str_hash pays nothing for the keyed mode. */
    size_t fast;
} strmap;

static size_t str_hash(const Py_UCS4 *str, Py_ssize_t len) {
    size_t value = 1469598103934665603ULL;
    for (Py_ssize_t index = 0; index < len; index++) {
        value ^= str[index];
        value *= 1099511628211ULL;
    }
    return value;
}

/* The keyed slot hash of the name index and of keyed strmaps. Its keys come from the stylesheet, so the slot is never
   str_hash: under str_hash a stylesheet could choose names that share one probe run. Out of line, so SipHash's body
   stays out of the str_hash walks that callers inline. */
static TH_NOINLINE size_t str_slot_hash(const Py_UCS4 *str, Py_ssize_t len) {
    return th_name_hash(str, len * (Py_ssize_t)sizeof(Py_UCS4));
}

static int str_eq(const Py_UCS4 *left, Py_ssize_t left_len, const Py_UCS4 *right, Py_ssize_t right_len) {
    return left_len == right_len && memcmp(left, right, (size_t)left_len * sizeof(Py_UCS4)) == 0;
}

static inline void strmap_reseat(strmap_slot *slots, size_t mask, const strmap_slot *entry, int keyed) {
    size_t probe = (keyed ? str_slot_hash(entry->str, entry->str_len) : str_hash(entry->str, entry->str_len)) & mask;
    while (slots[probe].used) {
        TH_WORK_STEP();
        probe = (probe + 1) & mask;
    }
    slots[probe] = *entry;
}

/* Move the entries into new_cap slots under th_name_hash when keyed, else str_hash. The walk starts at an empty slot,
   so a run that wraps past the end of the array is re-seated in its own order: no entry of a doubled map lands further
   from its home than it sat before, and the bound strmap_bucket keeps on how far an entry sits holds on. */
static inline int strmap_move(strmap *map, size_t new_cap, int keyed) {
    strmap_slot *slots = PyMem_Calloc(new_cap, sizeof(strmap_slot));
    if (slots == NULL) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
        return -1;       /* GCOVR_EXCL_LINE */
    }
    size_t start = 0;
    while (map->slots[start].used) {
        start++;
    }
    for (size_t index = start; index < map->cap; index++) {
        if (map->slots[index].used) {
            strmap_reseat(slots, new_cap - 1, &map->slots[index], keyed);
        }
    }
    for (size_t index = 0; index < start; index++) { /* every slot before start is filled */
        strmap_reseat(slots, new_cap - 1, &map->slots[index], keyed);
    }
    PyMem_Free(map->slots);
    map->slots = slots;
    map->cap = new_cap;
    map->fast = keyed ? 0 : new_cap / 2 - 1;
    return 0;
}

/* Out of line, so SipHash's rounds stay out of the str_hash paths. */
static TH_NOINLINE int strmap_move_keyed(strmap *map, size_t new_cap) {
    return strmap_move(map, new_cap, 1);
}

/* The slot of str in a map on th_name_hash: its entry, or the empty slot that ends its run. */
static size_t strmap_keyed_slot(const strmap *map, const Py_UCS4 *str, Py_ssize_t str_len) {
    size_t probe = str_slot_hash(str, str_len) & (map->cap - 1);
    while (map->slots[probe].used && !str_eq(map->slots[probe].str, map->slots[probe].str_len, str, str_len)) {
        TH_WORK_STEP();
        probe = (probe + 1) & (map->cap - 1);
    }
    return probe;
}

static TH_NOINLINE nodevec *strmap_seat(strmap *map, size_t probe, const Py_UCS4 *str, Py_ssize_t str_len) {
    Py_UCS4 *owned = ucs4_dup(str, str_len);
    if (owned == NULL) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
        return NULL;     /* GCOVR_EXCL_LINE */
    }
    map->slots[probe].str = owned;
    map->slots[probe].str_len = str_len;
    map->slots[probe].used = 1;
    map->count++;
    return &map->slots[probe].nodes;
}

/* The string's node-vector in a map that is empty or on th_name_hash, or NULL when absent. */
static TH_NOINLINE const nodevec *strmap_lookup_slow(const strmap *map, const Py_UCS4 *str, Py_ssize_t str_len) {
    if (map->cap == 0) {
        return NULL;
    }
    size_t probe = strmap_keyed_slot(map, str, str_len);
    return map->slots[probe].used ? &map->slots[probe].nodes : NULL;
}

/* strmap_lookup for a miss whose str_hash walk went past TH_FAST_PROBE_LIMIT: move the map to th_name_hash and look
   again. Out of line, so strmap_lookup stays small enough to inline into its callers. */
static TH_NOINLINE const nodevec *strmap_lookup_rekey(strmap *map, const Py_UCS4 *str, Py_ssize_t str_len) {
    if (strmap_move_keyed(map, map->cap) < 0) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
        return NULL;                            /* GCOVR_EXCL_LINE */
    }
    return strmap_lookup_slow(map, str, str_len);
}

/* The string's node-vector, or NULL when absent. A hit walks no further than its entry sits from its home, but a miss
   walks to the end of the run its home falls in, and the document can line up homes into one long run without any
   entry sitting far, so a miss that walked past TH_FAST_PROBE_LIMIT slots moves the map to th_name_hash (core/hash.h)
   for the rest of its life. A failed move leaves the map whole on str_hash, where the walk just found no entry. The
   home is kept only once the walk leaves it, so a lookup that ends there pays nothing for the bound. */
static inline const nodevec *strmap_lookup(strmap *map, const Py_UCS4 *str, Py_ssize_t str_len) {
    if (map->fast == 0) {
        return strmap_lookup_slow(map, str, str_len);
    }
    size_t probe = str_hash(str, str_len) & (map->cap - 1);
    if (!map->slots[probe].used) {
        return NULL;
    }
    const size_t home = probe;
    while (!str_eq(map->slots[probe].str, map->slots[probe].str_len, str, str_len)) {
        TH_WORK_STEP();
        probe = (probe + 1) & (map->cap - 1);
        if (!map->slots[probe].used) {
            return ((probe - home) & (map->cap - 1)) > TH_FAST_PROBE_LIMIT ? strmap_lookup_rekey(map, str, str_len)
                                                                           : NULL;
        }
    }
    return &map->slots[probe].nodes;
}

/* strmap_bucket for a str the str_hash walk missed after more than TH_FAST_PROBE_LIMIT slots: move the map to
   th_name_hash for good and seat str there. */
static TH_NOINLINE nodevec *strmap_bucket_rekey(strmap *map, const Py_UCS4 *str, Py_ssize_t str_len) {
    if (strmap_move_keyed(map, map->cap) < 0) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
        return NULL;                            /* GCOVR_EXCL_LINE */
    }
    return strmap_seat(map, strmap_keyed_slot(map, str, str_len), str, str_len);
}

/* The str_hash walk of strmap_bucket from probe, its home: str's node-vector, or str seated at the empty slot that ends
   the run, unless the walk to it went past TH_FAST_PROBE_LIMIT. The home is kept only once the walk leaves it, so a
   call that ends there, as most do, pays nothing for the bound. */
static inline nodevec *strmap_bucket_walk(strmap *map, const Py_UCS4 *str, Py_ssize_t str_len, size_t probe) {
    if (!map->slots[probe].used) {
        return strmap_seat(map, probe, str, str_len);
    }
    const size_t home = probe;
    while (!str_eq(map->slots[probe].str, map->slots[probe].str_len, str, str_len)) {
        TH_WORK_STEP();
        probe = (probe + 1) & (map->cap - 1);
        if (!map->slots[probe].used) {
            return ((probe - home) & (map->cap - 1)) > TH_FAST_PROBE_LIMIT ? strmap_bucket_rekey(map, str, str_len)
                                                                           : strmap_seat(map, probe, str, str_len);
        }
    }
    return &map->slots[probe].nodes;
}

/* strmap_bucket for a map that is empty, at its load limit, or on th_name_hash. Out of line, so the str_hash walk keeps
   its registers. */
static TH_NOINLINE nodevec *strmap_bucket_slow(strmap *map, const Py_UCS4 *str, Py_ssize_t str_len) {
    if (map->cap == 0) { /* most maps hold a few values, so the first one skips the move and the walk */
        map->slots = PyMem_Calloc(16, sizeof(strmap_slot));
        if (map->slots == NULL) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
            return NULL;          /* GCOVR_EXCL_LINE */
        }
        map->cap = 16;
        map->fast = 16 / 2 - 1;
        return strmap_seat(map, str_hash(str, str_len) & 15, str, str_len);
    }
    if (map->fast != 0) {
        if (strmap_move(map, map->cap * 2, 0) < 0) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
            return NULL;                             /* GCOVR_EXCL_LINE */
        }
        /* the grown map holds fewer than fast entries, so this is strmap_bucket's str_hash step */
        return strmap_bucket_walk(map, str, str_len, str_hash(str, str_len) & (map->cap - 1));
    } else if ((map->count + 1) * 2 >= map->cap) {
        if (strmap_move_keyed(map, map->cap * 2) < 0) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
            return NULL;                                /* GCOVR_EXCL_LINE */
        }
    }
    size_t probe = strmap_keyed_slot(map, str, str_len);
    return map->slots[probe].used ? &map->slots[probe].nodes : strmap_seat(map, probe, str, str_len);
}

/* Return the node-vector for a key string, creating the slot (copying str) if absent. On str_hash no entry is seated
   past TH_FAST_PROBE_LIMIT slots from its home, which bounds every walk that ends at an entry.
   NULL on allocation failure. */
XSLT_FORCEINLINE static nodevec *strmap_bucket(strmap *map, const Py_UCS4 *str, Py_ssize_t str_len) {
    if (map->count >= map->fast) {
        return strmap_bucket_slow(map, str, str_len);
    }
    return strmap_bucket_walk(map, str, str_len, str_hash(str, str_len) & (map->cap - 1));
}

/* strmap_bucket for the rare callers, out of line so their call sites do not count against inlining it into the
   hot ones. */
static TH_NOINLINE nodevec *strmap_bucket_outlined(strmap *map, const Py_UCS4 *str, Py_ssize_t str_len) {
    return strmap_bucket(map, str, str_len);
}

static void strmap_free(strmap *map) {
    for (size_t index = 0; index < map->cap; index++) {
        if (map->slots[index].used) {
            PyMem_Free(map->slots[index].str);
            PyMem_Free(map->slots[index].nodes.slots);
        }
    }
    PyMem_Free(map->slots);
    map->slots = NULL;
    map->cap = 0;
    map->count = 0;
    map->fast = 0;
}

typedef struct {
    const Py_UCS4 *name;
    Py_ssize_t length;
    Py_ssize_t first;
} xslt_name_entry;

typedef struct {
    xslt_name_entry *entries;
    size_t capacity;
    size_t budget;  /* walk steps the str_hash fill may still take, NAME_INDEX_WALKS per name */
    int keyed;      /* 1 once a fill on str_hash spent its budget or left a run past TH_FAST_PROBE_LIMIT */
    int walked_far; /* the current str_hash fill spent its budget */
} xslt_name_index;

/* An ordinary fill seats most names at or next to their homes, well inside this many steps per name; names a
   stylesheet stacked on one home spend it within a few dozen of them. */
#define NAME_INDEX_WALKS 4

/* The slot of name under str_hash: its entry, or the empty slot that ends its run. Out of line, so name_index_find
   inlines into its callers with only the keyed test. */
static TH_NOINLINE size_t name_index_slot(const xslt_name_index *index, const Py_UCS4 *name, Py_ssize_t length) {
    size_t slot = str_hash(name, length) & (index->capacity - 1);
    while (index->entries[slot].first != 0 &&
           !str_eq(index->entries[slot].name, index->entries[slot].length, name, length)) {
        TH_WORK_STEP();
        slot = (slot + 1) & (index->capacity - 1);
    }
    return slot;
}

/* name_index_find for an index on str_slot_hash. Out of line, so the str_hash lookup keeps its registers. */
static TH_NOINLINE Py_ssize_t name_index_find_keyed(const xslt_name_index *index, const Py_UCS4 *name,
                                                    Py_ssize_t length) {
    size_t slot = str_slot_hash(name, length) & (index->capacity - 1);
    while (index->entries[slot].first != 0 &&
           !str_eq(index->entries[slot].name, index->entries[slot].length, name, length)) {
        TH_WORK_STEP();
        slot = (slot + 1) & (index->capacity - 1);
    }
    return index->entries[slot].first;
}

/* After a fill on str_hash: 1 when it spent its walk budget or left a run past TH_FAST_PROBE_LIMIT, after moving the
   index to str_slot_hash empty, so the caller fills it again. The stylesheet picks its names, and a use-attribute-sets
   name that misses walks the run its home falls in. The index is read-only once filled, so this is the only point it
   can move. A run that long covers a whole aligned block of 32 slots (capacity is at least 32), so only a block whose
   every 8th slot is filled has its run measured. */
static int name_index_rekey(xslt_name_index *index) {
    if (index->keyed) {
        return 0;
    }
    size_t mask = index->capacity - 1;
    for (size_t block = 0; block < index->capacity && !index->walked_far; block += 32) {
        size_t sample = block;
        while (sample < block + 32 && index->entries[sample].first != 0) {
            sample += 8;
        }
        if (sample < block + 32) {
            continue;
        }
        size_t length = 1;
        for (size_t probe = (block - 1) & mask; length <= TH_FAST_PROBE_LIMIT && index->entries[probe].first != 0;
             probe = (probe - 1) & mask) {
            length++;
        }
        for (size_t probe = (block + 1) & mask; length <= TH_FAST_PROBE_LIMIT && index->entries[probe].first != 0;
             probe = (probe + 1) & mask) {
            length++;
        }
        index->walked_far = length > TH_FAST_PROBE_LIMIT;
    }
    if (!index->walked_far) {
        return 0;
    }
    index->keyed = 1;
    index->walked_far = 0;
    index->budget = SIZE_MAX;
    memset(index->entries, 0, index->capacity * sizeof(*index->entries));
    return 1;
}

static int name_index_reserve(xslt_name_index *index, Py_ssize_t count) {
    if (count < 16) {
        return 0;
    }
    size_t bytes;
    /* GCOVR_EXCL_BR_START: allocation size overflow */
    if (!th_grow_cap((size_t)count * 2, 0, 32, sizeof(*index->entries), &index->capacity, &bytes)) {
        return -1; /* GCOVR_EXCL_LINE */
    }
    /* GCOVR_EXCL_BR_STOP */
    index->budget = (size_t)count * NAME_INDEX_WALKS;
    index->entries = PyMem_Calloc(1, bytes);
    return index->entries == NULL ? -1 : 0; /* GCOVR_EXCL_BR_LINE: alloc */
}

/* Seat name, returning the position its slot held before, or set walked_far and seat nothing once the str_hash fill
   spends its walk budget, which bounds the fill whatever homes the stylesheet's names share. */
static inline Py_ssize_t name_index_add(xslt_name_index *index, const Py_UCS4 *name, Py_ssize_t length,
                                        Py_ssize_t position) {
    size_t slot = (index->keyed ? str_slot_hash(name, length) : str_hash(name, length)) & (index->capacity - 1);
    while (index->entries[slot].first != 0 &&
           !str_eq(index->entries[slot].name, index->entries[slot].length, name, length)) {
        TH_WORK_STEP();
        if (--index->budget == 0) {
            index->walked_far = 1;
            return 0;
        }
        slot = (slot + 1) & (index->capacity - 1);
    }
    const Py_ssize_t previous = index->entries[slot].first;
    index->entries[slot] = (xslt_name_entry){name, length, position + 1};
    return previous;
}

/* The str_hash walk runs on a keyed index too: an index holds each name once, so finding it anywhere is the answer, and
   runs under str_slot_hash are short wherever the walk starts. Only a miss there asks for the keyed walk, which keeps
   the check off the hits a transform makes. */
static Py_ssize_t name_index_find(const xslt_name_index *index, const Py_UCS4 *name, Py_ssize_t length) {
    const Py_ssize_t first = index->entries[name_index_slot(index, name, length)].first;
    return first == 0 && TH_UNLIKELY(index->keyed) ? name_index_find_keyed(index, name, length) : first;
}

/* ---- stylesheet model ----------------------------------------------------- */

typedef struct {
    Py_UCS4 *pattern;
    Py_ssize_t pattern_len;
    xp_program *prog;
    match_set matched;
    int built;
    double priority;
    int position;
    int precedence;
    th_node *body;
    Py_UCS4 *mode;
    Py_ssize_t mode_len;
    const Py_UCS4 *key; /* the element name the pattern can only match, NULL for a rule any node may match */
    Py_ssize_t key_len;
    Py_ssize_t next; /* the next rule in the chain best_rule walks for this rule, or -1 */
} xslt_rule;

typedef struct {
    Py_UCS4 *name;
    Py_ssize_t name_len;
    th_node *body;
} xslt_named;

typedef struct {
    Py_UCS4 *name;
    Py_ssize_t name_len;
    th_node *node;
    int is_param;
} xslt_global;

typedef struct {
    Py_UCS4 *name;
    Py_ssize_t name_len;
    th_node *instruction;
    xp_program *match_prog;
    xp_program *use_prog;
    strmap table;
    /* The attr of each match index whose item is an attribute or namespace node, -1 for the others; NULL while every
       match is a node itself, so a key over elements keeps two-word slots and reads no attr. */
    Py_ssize_t *attrs;
    int built;
} xslt_key;

/* The attr of the item a key slot names: its node itself (-1) unless the key matched attribute or namespace nodes. */
static inline Py_ssize_t key_attr(const xslt_key *key, const nodeslot *entry) {
    return key->attrs == NULL ? -1 : key->attrs[entry->order];
}

/* Append the items of one key bucket. A key over nodes alone takes a loop that reads no attrs. */
static int key_append(const xslt_key *key, const nodevec *bucket, xp_nodeset *out) {
    const Py_ssize_t *attrs = key->attrs;
    if (attrs == NULL) {
        for (Py_ssize_t slot = 0; slot < bucket->len; slot++) {
            if (ns_append(out, bucket->slots[slot].node, -1) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                return -1;                                          /* GCOVR_EXCL_LINE */
            }
        }
        return 0;
    }
    for (Py_ssize_t slot = 0; slot < bucket->len; slot++) {
        const nodeslot *entry = &bucket->slots[slot];
        if (ns_append(out, entry->node, attrs[entry->order]) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;                                              /* GCOVR_EXCL_LINE */
        }
    }
    return 0;
}

typedef struct {
    Py_UCS4 *name;
    Py_ssize_t name_len;
    xp_result value;
    th_node *rtf;
} var_bind;

typedef struct {
    const char *name;
    Py_ssize_t name_len;
    const Py_UCS4 *value;
    Py_ssize_t value_len;
} xslt_ns_decl;

/* One xmlns:prefix attribute of a large result element, keyed by the element and the URI it bound when recorded.
   position stays valid because result attributes are never removed; uri is the value buffer that was there then, so
   a later write that rebinds the prefix leaves a different buffer at position and marks the entry stale. */
typedef struct {
    const th_node *element; /* NULL marks a free slot */
    const Py_UCS4 *uri;
    Py_ssize_t uri_len;
    Py_ssize_t position;
    uint32_t hash;
} xslt_out_binding;

/* A named xsl:attribute-set (section 7.1.4). Its body holds xsl:attribute children; the
   precedence orders redefinitions across import boundaries (higher importer wins). */
typedef struct {
    const Py_UCS4 *name;
    Py_ssize_t name_len;
    th_node *body;
    int precedence;
    Py_ssize_t uses_start; /* this set's use-attribute-sets, resolved once to slots in engine.attrset_uses */
    Py_ssize_t uses_end;
} xslt_attrset;

typedef struct {
    Py_ssize_t *items;
    Py_ssize_t len;
    Py_ssize_t cap;
} xslt_slots;

/* One xsl:strip-space / xsl:preserve-space element-name token (section 3.4). strip marks
   the default action; specificity and precedence resolve a name that both sets cover, with
   higher import precedence, then higher specificity, winning. */
typedef struct {
    const Py_UCS4 *name;
    Py_ssize_t name_len;
    int strip;
    double specificity;
    int precedence;
} xslt_space;

/* An xsl:namespace-alias mapping (section 7.1.1): a literal result element or attribute in
   the stylesheet-prefix namespace is emitted in the result-prefix namespace instead. */
typedef struct {
    const Py_UCS4 *style_prefix;
    Py_ssize_t style_prefix_len;
    const Py_UCS4 *result_prefix;
    Py_ssize_t result_prefix_len;
    const Py_UCS4 *result_uri;
    Py_ssize_t result_uri_len;
    int precedence;
} xslt_nsalias;

typedef struct {
    const Py_UCS4 *source;
    Py_ssize_t length;
    xp_program *program;
} xslt_expr;

typedef struct {
    const Py_UCS4 *source;
    const Py_UCS4 *probed; /* the pattern a first call already answered by probing, so the next one indexes it */
    match_set matched;
} xslt_number_match;

typedef struct {
    int type;
    int ns;
    const Py_UCS4 *name;
    Py_ssize_t name_len;
    const Py_UCS4 *uri;
    Py_ssize_t uri_len;
    uint32_t uri_id; /* stands for the uri text among the names one xslt_number_walk tallies; 0 without a uri */
} xslt_number_name;

/* A namespace declaration in scope, with the integer number_uri_id gave its value. */
typedef struct {
    const th_node_attr *attr;
    uint32_t uri_id;
} xslt_number_binding;

typedef struct {
    const th_node *node;
    uint32_t atom;
    xslt_number_binding previous;
} xslt_number_scope;

/* A distinct namespace name the index has seen declared. */
typedef struct {
    const Py_UCS4 *text;
    Py_ssize_t len;
    uint32_t hash;
    uint32_t id; /* 0 marks a free slot */
} xslt_number_uri;

typedef struct {
    th_node_map positions;
    xslt_number_name *names;
    size_t name_capacity;
    xslt_number_binding *bindings;
    size_t binding_capacity;
    xslt_number_uri *uris;
    size_t uri_capacity;
    size_t uri_count;
    uint64_t uri_seed;
    xslt_number_scope *scope;
    size_t scope_count;
    size_t scope_capacity;
    char *prefix;
    size_t prefix_capacity;
    th_node *next;
} xslt_number_index;

/* The nearest declaration of one prefix at each node a climb for it has passed. */
typedef struct {
    th_node_map resolved;
    const th_node_attr **declared;
    size_t declared_count;
    size_t declared_capacity;
} xslt_prefix_memo;

typedef struct {
    xslt_prefix_memo *prefixes;
    size_t capacity;
    char *name;
    size_t name_capacity;
} xslt_declarations;

/* The nearest source ancestor-or-self that declares a namespace, per node a walk has passed. */
typedef struct {
    th_node_map resolved;
    th_node **found;
    size_t found_count;
    size_t found_capacity;
} xslt_scope_memo;

/* XSLT-namespace marks of a stylesheet tree, so a classification is a lookup, not an ancestor climb. slots maps an
   element that is in the XSLT namespace, or has an attribute that is, to 1 plus its offset in flags: one byte for
   the element, then one per attribute. */
typedef struct {
    th_node_map slots;
    uint8_t *flags;
    size_t flag_count;
    size_t flag_capacity;
} xslt_marks;

typedef struct {
    th_node_map positions;
    th_node *last;
    long value;
} xslt_number_prefix;

/* How many nodes of one expanded name a level="any" walk has passed since its last from match. */
typedef struct {
    xslt_number_name name;
    long count; /* 0 marks a free slot */
    union {
        long epoch;      /* in a level="any" walk */
        Py_ssize_t open; /* in the lineage walk: names the innermost element of the name it is inside, 0 for none */
    };
    uint32_t hash;
} xslt_number_tally;

/* The level="any" walk of a default count: it numbers every node it passes among the nodes of that node's own
   expanded name, so one pass over the document serves every name and the order the names arrive in costs nothing. */
typedef struct {
    th_node_map positions;
    th_node *last;
    xslt_number_tally *tallies;
    xslt_number_tally *recent;
    const th_node *recent_source;
    size_t tally_capacity;
    size_t tally_count;
    uint64_t seed; /* salt so chosen names cannot flood one slot of tallies */
    long epoch;    /* the from matches passed: a tally of an earlier epoch has been reset */
    int indexed;   /* whether the walk resolved its names through number_index */
    int repeated;  /* whether a call came before: the first is answered by counting backwards, without the walk */
} xslt_number_walk;

/* The state one xsl:number instruction keeps across its calls. Each instruction has its own patterns, so instructions
   that alternate over one run each keep their matches and walk instead of restarting the other's. */
/* The sibling count a level number last answered for, so the next sibling counts from it. */
typedef struct {
    const th_node *node;
    long value;
} xslt_number_memo;

/* The count of each node of a sibling run, keyed by node. indexed records whether default names were resolved through
   number_index when the counts were filled. */
typedef struct {
    th_node_map counts;
    int indexed;
} xslt_number_siblings;

/* Where a level="single" or level="multiple" climb from a node stops: its nearest ancestor-or-self that the count or
   the from pattern matches. A climb through an ordinary document stops within a few nodes and keeps nothing; once one
   passes XSLT_NUMBER_CLIMB_STEPS, the instruction keeps the stops, so each node of a deep chain is climbed past once
   rather than once per number below it. */
typedef struct {
    th_node **items;
    size_t count;
    size_t capacity;
} xslt_node_list;

typedef struct {
    th_node_map stops; /* node to 1 plus the position of its stop in nodes, or -1 when the climb leaves the tree */
    xslt_node_list nodes;
    int kept;
} xslt_number_climb;

/* For each element a pre-order walk has entered, its nearest ancestor of the same expanded name: the next node a
   level="multiple" default count numbers above it. walk.positions maps an element to 1 plus the position of that
   ancestor in elements, or to -1 when it has none, and walk.last is the next node the walk enters. The walk depends on
   names alone, so every site shares it, and it starts only once a site climbs a deep chain. */
typedef struct {
    xslt_number_walk walk;
    xslt_node_list elements;
} xslt_number_lineage;

typedef struct {
    xslt_number_match count_match;
    xslt_number_match from_match;
    xslt_number_prefix explicit_any;
    xslt_number_walk any;
    xslt_number_memo count_memo;
    xslt_number_siblings count_siblings;
    xslt_number_climb climb;
} xslt_number_site;

typedef struct {
    th_node_map index; /* instruction to its position in list, plus one */
    xslt_number_site *list;
    size_t count;
    size_t capacity;
    const th_node *recent; /* the instruction numbered last, so a run of one instruction skips the index */
    size_t recent_position;
} xslt_number_sites;

/* A source text node detached by whitespace stripping (section 3.4), kept so the caller's
   tree is restored to its original shape after the transform returns. */
struct strip_entry {
    th_node *node;
    th_node *parent;
    th_node *next; /* the sibling that followed node, or NULL when node was the last child */
};

enum output_method { OUT_XML, OUT_HTML, OUT_TEXT };

typedef struct {
    const th_node *node;
    Py_ssize_t attr;
    const Py_UCS4 *mode;
    Py_ssize_t mode_len;
    xslt_rule *rule;
} xslt_dispatch_entry;

typedef struct engine {
    PyObject *module;
    th_tree *src_tree;
    th_tree *source_snapshot;
    th_tree *sheet_tree;
    th_tree *out_tree;
    th_tree *merged_tree; /* holds copies of the principal + imported stylesheets when importing */
    th_node *src_root;
    xp_id_cache src_ids; /* id() over the source, which a run leaves alone: indexed once per run, not walked per node */
    xp_lang_memo *src_langs; /* lang()'s answers over the source, kept across the run's evaluations */

    xslt_rule *rules;
    Py_ssize_t nrules;
    Py_ssize_t rules_cap;
    xslt_dispatch_entry *dispatch;
    size_t dispatch_capacity;
    size_t dispatch_count;
    xslt_named *named;
    Py_ssize_t nnamed;
    Py_ssize_t named_cap;
    xslt_global *globals;
    Py_ssize_t nglobals;
    Py_ssize_t globals_cap;
    xslt_key *keys;
    Py_ssize_t nkeys;
    Py_ssize_t keys_cap;
    xslt_attrset *attrsets;
    Py_ssize_t nattrsets;
    Py_ssize_t attrsets_cap;
    xslt_name_index named_index;
    xslt_name_index key_index;
    xslt_name_index attrset_index;
    xslt_name_index
        rule_names;       /* element-named rules by local name; an entry's first is the earliest rule's position + 1 */
    Py_ssize_t rule_wild; /* the first rule of the chain of rules any node may match, or -1 */
    Py_ssize_t rule_root; /* the first rule of the chain of rules whose pattern is "/", or -1 */
    int rule_dispatch;    /* whether best_rule caches its answers, see build_rule_index */
    Py_ssize_t *attrset_previous; /* with attrset_index, per set one past the earlier definition of its name, else 0 */
    xslt_slots attrset_uses;
    xslt_space *spaces;
    Py_ssize_t nspaces;
    Py_ssize_t spaces_cap;
    xslt_nsalias *aliases;
    Py_ssize_t naliases;
    Py_ssize_t aliases_cap;
    xslt_expr *expressions;
    size_t expression_cap;
    size_t expression_count;
    xslt_expr *patterns;
    size_t pattern_cap;
    size_t pattern_count;

    Py_UCS4 *xsl_prefix;
    Py_ssize_t xsl_prefix_len;
    int xsl_ns_dynamic;
    int xsl_marked; /* the xslt_marks classify the trees: dynamic, or some root does not bind the XSLT prefix itself */
    int lre_excludes; /* some element of the stylesheet carries exclude-result-prefixes, so an ancestor may exclude a
                         namespace */
    const Py_UCS4 *exclude_prefixes; /* aliases the stylesheet root's exclude-result-prefixes value */
    Py_ssize_t exclude_prefixes_len;
    const Py_UCS4 *cdata_elements; /* aliases xsl:output cdata-section-elements (space-separated QNames) */
    Py_ssize_t cdata_elements_len;
    const Py_UCS4 *ext_prefixes; /* aliases the root extension-element-prefixes value */
    Py_ssize_t ext_prefixes_len;
    int output_method;
    int method_seen; /* whether xsl:output named a method, so html auto-selection is suppressed */
    int omit_xml_decl;
    int simplified; /* the document element is a literal result element (section 2.3) */
    th_node *ns_cached_element;
    xslt_ns_decl *ns_decls;
    size_t ns_decls_len;
    size_t ns_decls_cap;
    int ns_counter;                 /* serial for the generated ns_N prefixes xsl:attribute namespace fixup mints */
    xslt_out_binding *out_bindings; /* xmlns:prefix attributes of large result elements, by URI */
    size_t out_bindings_cap;
    size_t out_bindings_len;
    th_node_map out_bindings_seen; /* large result element to the attributes recorded from it, plus one */
    th_node_map instructions;      /* stylesheet element to its enum xsl_instr, plus one, set once at compile time */
    int precedence;                /* import precedence being assigned as declarations are walked */

    /* Whether a non-empty default namespace is in scope at the output parent, which a no-namespace result element
       must undeclare. */
    int default_bound;
    th_node_map src_namespaced; /* source node -> 1 when its nearest default declaration is non-empty, 2 otherwise */
    xslt_scope_memo src_scope;
    /* The source element whose copy is the output parent's nearest result element, when every namespace node of that
       element is already in scope there. A copy of one of its children then checks only its own declarations. */
    th_node *out_src;

    struct strip_entry *stripped; /* text nodes detached from the source by whitespace stripping */
    Py_ssize_t nstripped;
    Py_ssize_t stripped_cap;

    var_bind *scope;
    Py_ssize_t scope_len;
    Py_ssize_t scope_cap;

    th_node *cur_node;
    Py_ssize_t cur_attr;
    Py_ssize_t ctx_pos;
    Py_ssize_t ctx_size;
    int gen_counter;
    int depth;
    int key_builds; /* key tables being built, each nested in the use expression of the one before */
#ifdef TH_STACK_FLOOR
    uintptr_t stack_floor; /* enter_deep_level's bound for the thread of this run; 0 until its first checked level */
#endif

    struct {
        uint32_t count;
        uint32_t from;
        uint32_t level;
        uint32_t format;
        uint32_t grouping_separator;
        uint32_t grouping_size;
    } number_attrs;
    xslt_number_sites number_sites;

    xslt_number_name current_number_name;
    const th_node *current_number_node;
    uint32_t default_namespace_atom;
    int number_namespaces;
    xslt_number_index number_index;
    xslt_marks marks;
    xslt_declarations src_declarations;
    xslt_declarations sheet_declarations;

    /* Reuse sibling counts to avoid quadratic scans during repeated numbering. Default counts depend on expanded names,
       so every default count instruction shares this memo and the counts of its sibling run; a counted pattern keeps
       its own in its site. */
    xslt_number_memo number_memo;
    xslt_number_name number_memo_name;
    xslt_number_siblings default_siblings;
    xslt_number_lineage number_lineage;

    const char *error;
    int py_error;
    int owns_model;
    /* compile_expression's error text for the handlers that recurse: here, not on their frames, which every nesting
       level repeats */
    char errbuf[256];
#ifdef TH_OPERATION_LIMIT
    size_t operations; /* stylesheet elements compiled, then instructions applied in one run */
#endif
} engine;

typedef struct {
    engine *eng;
    th_node *instruction;
    const Py_UCS4 *last_name;
    const Py_UCS4 *last_uri;
    Py_ssize_t last_uri_len;
} xslt_pattern_scope;

typedef struct {
    engine *eng;
    th_node *element;
} xslt_scope_source;

/* One alternative of a count or from pattern that depends on the call. A path xp_pattern_walks_back accepts is matched
   node by node through walk; any other alternative is evaluated once per call into nodes, with walk NULL. */
typedef struct {
    xp_pattern_walk *walk;
    match_set nodes;
} xslt_probe_alternative;

/* The count or from pattern of one xsl:number call. A pattern that reads nothing but the source and the instruction's
   namespaces has its nodes in set, kept on the instruction's site. One that reads current(), a variable or an extension
   function has its alternatives tested on each node the count visits, so a number costs those nodes and not the
   document. */
typedef struct {
    const match_set *set;
    xslt_probe_alternative *alternatives;
    int count;
    int failed;
    xslt_pattern_scope scope;
} xslt_number_pattern;

static int pattern_name_test(void *context, th_node *node, Py_ssize_t attr, const Py_UCS4 *name, Py_ssize_t name_len);
static int bind_expression_scope(engine *eng, xp_program *prog, th_node *element);
static int pattern_callback_needed(engine *eng, const xp_program *prog);
static int scan_static_name_pattern(engine *eng, const xp_program *prog, xp_result *matched);

/* The last of the first count attribute sets that bears the name, or -1. */
static Py_ssize_t attrset_scan(const engine *eng, const Py_UCS4 *name, Py_ssize_t name_len, Py_ssize_t count) {
    Py_ssize_t slot = count - 1;
    while (slot >= 0 && !str_eq(eng->attrsets[slot].name, eng->attrsets[slot].name_len, name, name_len)) {
        slot--;
    }
    return slot;
}

/* The last attribute set that bears the name, or -1. */
static Py_ssize_t attrset_find(const engine *eng, const Py_UCS4 *name, Py_ssize_t name_len) {
    if (eng->attrset_index.capacity != 0) {
        return name_index_find(&eng->attrset_index, name, name_len) - 1;
    }
    return attrset_scan(eng, name, name_len, eng->nattrsets);
}

/* The definition of the set's name before it, or -1; without the index there are too few sets for a scan to cost. */
static Py_ssize_t attrset_before(const engine *eng, Py_ssize_t slot) {
    if (eng->attrset_previous != NULL) {
        return eng->attrset_previous[slot] - 1;
    }
    return attrset_scan(eng, eng->attrsets[slot].name, eng->attrsets[slot].name_len, slot);
}

/* Same-named sets merge (section 7.1.4): a name resolves to its last definition and this chain leads back through the
   earlier ones, so a reference costs one slot however many definitions share the name. Kept out of line so the
   declaration loops of build_name_indexes compile as tightly as they would without it. */
TH_NOINLINE static int build_attrset_index(engine *eng) {
    eng->attrset_previous = PyMem_Malloc((size_t)eng->nattrsets * sizeof(*eng->attrset_previous));
    if (eng->attrset_previous == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;                       /* GCOVR_EXCL_LINE */
    }
    do {
        for (Py_ssize_t index = 0; index < eng->nattrsets && !eng->attrset_index.walked_far; index++) {
            eng->attrset_previous[index] =
                name_index_add(&eng->attrset_index, eng->attrsets[index].name, eng->attrsets[index].name_len, index);
        }
    } while (name_index_rekey(&eng->attrset_index));
    return 0;
}

static int build_name_indexes(engine *eng) {
    /* GCOVR_EXCL_BR_START: allocation failure */
    if (name_index_reserve(&eng->named_index, eng->nnamed) < 0 || name_index_reserve(&eng->key_index, eng->nkeys) < 0 ||
        name_index_reserve(&eng->attrset_index, eng->nattrsets) < 0) {
        return -1; /* GCOVR_EXCL_LINE */
    }
    /* GCOVR_EXCL_BR_STOP */
    /* a fill stops once it spends its walk budget, so a str_hash fill is linear; a rekey refills keyed */
    if (eng->named_index.capacity != 0) {
        do {
            for (Py_ssize_t index = eng->nnamed - 1; index >= 0 && !eng->named_index.walked_far; index--) {
                name_index_add(&eng->named_index, eng->named[index].name, eng->named[index].name_len, index);
            }
        } while (name_index_rekey(&eng->named_index));
    }
    if (eng->key_index.capacity != 0) {
        do {
            for (Py_ssize_t index = eng->nkeys - 1; index >= 0 && !eng->key_index.walked_far; index--) {
                name_index_add(&eng->key_index, eng->keys[index].name, eng->keys[index].name_len, index);
            }
        } while (name_index_rekey(&eng->key_index));
    }
    return eng->attrset_index.capacity != 0 ? build_attrset_index(eng) : 0;
}

/* Template calls, xsl:for-each, result-tree construction and use-attribute-sets chains recurse in C. How much C stack
   a level takes depends on what nests (in an optimized build about a third of a kilobyte for a named-template call
   and more than twice that for a template applied from an xsl:param default) and on the compiler, so this count
   bounds the recursion but cannot promise that a small thread stack holds all 400 levels. Each counted level deeper
   than XSLT_STACK_CHECK_DEPTH therefore also checks the stack in enter_deep_level. */
#define XSLT_MAX_DEPTH TH_DEPTH_LIMIT(400)

/* About 1 KiB of C stack per level, so 32 levels fit in half of musl's 128 KiB thread stack. Shallower levels skip both
   the stack check and the cap, so a level costs one comparison, as it did before the stack check existed. */
#define XSLT_STACK_CHECK_DEPTH 32
_Static_assert(XSLT_MAX_DEPTH > XSLT_STACK_CHECK_DEPTH, "enter_deep_level enforces the cap");

/* ---- xsl element identification ------------------------------------------- */

static const char XSLT_NS[] = "http://www.w3.org/1999/XSL/Transform";

static const Py_UCS4 *qname_local(const th_node *node, Py_ssize_t *local_len, Py_ssize_t *prefix_len) {
    for (Py_ssize_t index = 0; index < node->text_len; index++) {
        if (node->text[index] == ':') {
            *prefix_len = index;
            *local_len = node->text_len - index - 1;
            return node->text + index + 1;
        }
    }
    *prefix_len = 0;
    *local_len = node->text_len;
    return node->text;
}

static int marks_element_is_xsl(const xslt_marks *marks, const th_node *node) {
    Py_ssize_t slot = th_node_map_find(&marks->slots, node);
    return slot != 0 && marks->flags[slot - 1] != 0;
}

static int marks_attr_is_xsl(const xslt_marks *marks, const th_node *node, Py_ssize_t index) {
    Py_ssize_t slot = th_node_map_find(&marks->slots, node);
    return slot != 0 && marks->flags[slot + index] != 0;
}

static int is_xsl_fast(const engine *eng, const th_node *node, const char *local) {
    if (node->type != TH_NODE_ELEMENT) {
        return 0;
    }
    Py_ssize_t prefix_len = eng->xsl_prefix_len;
    if (node->text_len < prefix_len + 1 || node->text[prefix_len] != ':') {
        return 0;
    }
    if (memcmp(node->text, eng->xsl_prefix, (size_t)prefix_len * sizeof(Py_UCS4)) != 0) {
        return 0;
    }
    return ucs4_ascii_eq(node->text + prefix_len + 1, node->text_len - prefix_len - 1, local);
}

static int is_xsl_dynamic(const engine *eng, const th_node *node, const char *local) {
    if (node->type != TH_NODE_ELEMENT) {
        return 0;
    }
    Py_ssize_t local_len;
    Py_ssize_t prefix_len;
    const Py_UCS4 *name = qname_local(node, &local_len, &prefix_len);
    return ucs4_ascii_eq(name, local_len, local) && marks_element_is_xsl(&eng->marks, node);
}

static int is_xsl(const engine *eng, const th_node *node, const char *local) {
    return eng->xsl_ns_dynamic ? is_xsl_dynamic(eng, node, local) : is_xsl_fast(eng, node, local);
}

/* Whether an element is in the XSLT namespace (an xsl:* element), the test that tells
   an instruction from a literal result element. The caller only asks about elements. */
static int is_any_xsl_fast(const engine *eng, const th_node *node) {
    Py_ssize_t prefix_len = eng->xsl_prefix_len;
    return node->text_len > prefix_len + 1 && node->text[prefix_len] == ':' &&
           memcmp(node->text, eng->xsl_prefix, (size_t)prefix_len * sizeof(Py_UCS4)) == 0;
}

static int is_any_xsl_dynamic(const engine *eng, const th_node *node) {
    return marks_element_is_xsl(&eng->marks, node);
}

static const Py_UCS4 *attribute_value(const th_node_attr *attr, Py_ssize_t *out_len) {
    static const Py_UCS4 empty = 0;
    /* XML forbids a valueless attribute, so a parse_xml stylesheet never has one. */
    if (attr->value == NULL) { /* GCOVR_EXCL_BR_LINE */
        *out_len = 0;          /* GCOVR_EXCL_LINE */
        return &empty;         /* GCOVR_EXCL_LINE */
    }
    *out_len = attr->value_len;
    return attr->value;
}

static const Py_UCS4 *attr_lookup(th_tree *tree, const th_node *node, const char *name, Py_ssize_t name_len,
                                  Py_ssize_t *out_len) {
    Py_ssize_t index = th_node_attr_find(tree, (th_node *)node, name, name_len);
    return index < 0 ? NULL : attribute_value(&node->attrs[index], out_len);
}

XSLT_FORCEINLINE static const Py_UCS4 *number_attr(const engine *eng, const th_node *node, uint32_t atom,
                                                   const char *name, Py_ssize_t *out_len) {
    if (node->ns != TH_NS_HTML) {
        return attr_lookup(eng->sheet_tree, node, name, (Py_ssize_t)strlen(name), out_len);
    }
    if (atom != UINT32_MAX) {
        for (Py_ssize_t index = 0; index < node->attr_count; index++) {
            if (node->attrs[index].name_atom == atom) {
                return attribute_value(&node->attrs[index], out_len);
            }
        }
    }
    return NULL;
}

/* ---- error helpers -------------------------------------------------------- */

static int fail(engine *eng, const char *message) {
    if (strcmp(message, "out of memory") == 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        PyErr_NoMemory();                        /* GCOVR_EXCL_LINE */
        eng->py_error = 1;                       /* GCOVR_EXCL_LINE */
        return -1;                               /* GCOVR_EXCL_LINE */
    }
    eng->error = message;
    return -1;
}

static int fail_py(engine *eng) {
    eng->py_error = 1;
    return -1;
}

#ifdef TH_OPERATION_LIMIT
/* Fuzz builds pass -DTH_OPERATION_LIMIT so a super-linear stylesheet stops with a ValueError instead of a timeout that
   hides other findings. libxslt charges each parsed instruction (xslt.c xsltParseSequenceConstructor) and each
   instantiated one (transform.c xsltApplySequenceConstructor) the same way; release builds compile none of this. */
static int charge_operation(engine *eng, const char *phase) {
    if (++eng->operations <= TH_OPERATION_LIMIT) {
        return 0;
    }
    PyErr_Format(PyExc_ValueError, "xslt: %s exceeded the operation limit of %d", phase, TH_OPERATION_LIMIT);
    return fail_py(eng);
}
#endif

/* ---- compile a match pattern to an equivalent absolute expression --------- */

/* Split a pattern on top-level '|' (outside brackets, parentheses and string
   literals). Returns the count; fills starts/lens for up to `max` alternatives. */
static int split_union(const Py_UCS4 *src, Py_ssize_t len, Py_ssize_t *starts, Py_ssize_t *lens, int max) {
    int count = 0;
    Py_ssize_t begin = 0;
    int depth = 0;
    Py_UCS4 quote = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_UCS4 ch = src[index];
        if (quote != 0) {
            if (ch == quote) {
                quote = 0;
            }
            continue;
        }
        if (ch == '\'' || ch == '"') {
            quote = ch;
        } else if (ch == '[' || ch == '(') {
            depth++;
        } else if (ch == ']' || ch == ')') {
            depth--;
        } else if (ch == '|' && depth == 0) {
            if (count >= max) {
                return -1;
            }
            starts[count] = begin;
            lens[count] = index - begin;
            count++;
            begin = index + 1;
        }
    }
    if (count >= max) { /* GCOVR_EXCL_BR_LINE: the in-loop guard already rejects an over-long union */
        return -1;      /* GCOVR_EXCL_LINE */
    }
    starts[count] = begin;
    lens[count] = len - begin;
    return count + 1;
}

static void trim(const Py_UCS4 *src, Py_ssize_t len, Py_ssize_t *out_start, Py_ssize_t *out_len) {
    Py_ssize_t start = 0;
    Py_ssize_t end = len;
    while (start < end && ucs4_is_ws(src[start])) {
        start++;
    }
    while (end > start && ucs4_is_ws(src[end - 1])) {
        end--;
    }
    *out_start = start;
    *out_len = end - start;
}

/* The XSLT 1.0 default priority of a single location-path pattern (section 5.5):
   0.5 for a pattern with more than one step or any predicate, 0 for a lone QName
   name test, -0.25 for a prefixed wildcard (ns:*), -0.5 for an unqualified node
   test (*, node(), text(), comment(), processing-instruction()). */
static double default_priority(const Py_UCS4 *src, Py_ssize_t len) {
    Py_ssize_t start;
    Py_ssize_t trimmed;
    trim(src, len, &start, &trimmed);
    const Py_UCS4 *pattern = src + start;
    /* A multi-step ('/') or predicated ('[') pattern defaults to 0.5. */
    for (Py_ssize_t index = 0; index < trimmed; index++) {
        if (pattern[index] == '/' || pattern[index] == '[') {
            return 0.5;
        }
    }
    /* Only a single-step pattern reaches here (the / and [ scan above returned), so
       trimmed is at least one code point and pattern[0] is safe to read. */
    int attribute = pattern[0] == '@';
    const Py_UCS4 *name = attribute ? pattern + 1 : pattern;
    Py_ssize_t name_len = attribute ? trimmed - 1 : trimmed;
    if (name_len == 1 && name[0] == '*') {
        return -0.5;
    }
    if (ucs4_ascii_eq(name, name_len, "node()") || ucs4_ascii_eq(name, name_len, "text()") ||
        ucs4_ascii_eq(name, name_len, "comment()") || ucs4_ascii_eq(name, name_len, "processing-instruction()")) {
        return -0.5;
    }
    /* The -0.25 default for a prefixed wildcard (ns:*) has no case here: the reused
       XPath engine rejects the "prefix:*" name test at compile time, so such a pattern
       never reaches priority assignment. */
    /* A single QName name test (including processing-instruction('literal')) is 0; any
       other single-step form (a bare function call pattern) defaults to 0.5. */
    for (Py_ssize_t index = 0; index < name_len; index++) {
        if (name[index] == '(' && !ucs4_ascii_eq(name, index, "processing-instruction")) {
            return 0.5;
        }
    }
    return 0;
}

/* Whether a pattern alternative is already anchored at the document root: a leading "/", or an id() or key() call. */
static int pattern_anchored(const Py_UCS4 *pattern, Py_ssize_t trimmed) {
    if (trimmed > 0 && pattern[0] == '/') {
        return 1;
    }
    if (trimmed < 3) {
        return 0;
    }
    Py_ssize_t probe = -1;
    if (ucs4_has_prefix(pattern, trimmed, "id")) {
        probe = 2;
    } else if (ucs4_has_prefix(pattern, trimmed, "key")) {
        probe = 3;
    }
    while (probe >= 0 && probe < trimmed && ucs4_is_ws(pattern[probe])) {
        probe++;
    }
    return probe >= 0 && probe < trimmed && pattern[probe] == '(';
}

/* Compile a pattern as an absolute XPath expression whose result set equals the nodes the pattern matches. Each
   top-level alternative is anchored on its own: a relative alternative gains a leading "//" (descendant-or-self), an
   already-anchored one (/, //, id(, key() ) is used verbatim. Anchoring the whole union would leave every alternative
   after the first relative to the root, so `doc | @*` would lose its attributes. Returns the program, or NULL with
   eng->error / eng->py_error set. */
static xp_program *compile_pattern_new(engine *eng, const Py_UCS4 *src, Py_ssize_t len) {
    Py_ssize_t starts[64];
    Py_ssize_t lengths[64];
    int alternatives = split_union(src, len, starts, lengths, 64);
    if (alternatives < 0) { /* GCOVR_EXCL_BR_LINE: the same limit parse_template applies to its alternatives */
        fail(eng, "xslt: match pattern has too many alternatives"); /* GCOVR_EXCL_LINE */
        return NULL;                                                /* GCOVR_EXCL_LINE */
    }
    xb expr = {0};
    for (int index = 0; index < alternatives; index++) {
        Py_ssize_t start;
        Py_ssize_t trimmed;
        trim(src + starts[index], lengths[index], &start, &trimmed);
        const Py_UCS4 *pattern = src + starts[index] + start;
        if (index > 0 && xb_add_ascii(&expr, " | ") < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            xb_free(&expr);                                /* GCOVR_EXCL_LINE */
            fail(eng, "out of memory");                    /* GCOVR_EXCL_LINE */
            return NULL;                                   /* GCOVR_EXCL_LINE */
        }
        if (!pattern_anchored(pattern, trimmed) && xb_add_ascii(&expr, "//") < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            xb_free(&expr);                                                         /* GCOVR_EXCL_LINE */
            fail(eng, "out of memory");                                             /* GCOVR_EXCL_LINE */
            return NULL;                                                            /* GCOVR_EXCL_LINE */
        }
        if (xb_add(&expr, pattern, trimmed) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            xb_free(&expr);                        /* GCOVR_EXCL_LINE */
            fail(eng, "out of memory");            /* GCOVR_EXCL_LINE */
            return NULL;                           /* GCOVR_EXCL_LINE */
        }
    }
    char errbuf[256];
    xp_program *prog = xp_compile(expr.data, expr.len, errbuf, sizeof(errbuf), PyExc_ValueError);
    xb_free(&expr);
    if (prog == NULL) {
        if (!PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: allocation error */
            PyErr_Format(PyExc_ValueError, "xslt: bad match pattern: %s", errbuf);
        }
        fail_py(eng);
        return NULL;
    }
    return prog;
}

/* ---- the XPath variable scope --------------------------------------------- */

/* Each binding owns its value; the output tree owns its result tree fragment. */
static int scope_push(engine *eng, const Py_UCS4 *name, Py_ssize_t name_len, xp_result value, th_node *rtf) {
    if (eng->scope_len == eng->scope_cap) {
        Py_ssize_t cap = eng->scope_cap == 0 ? TH_INITIAL_CAPACITY(8) : eng->scope_cap * 2;
        var_bind *grown = PyMem_Realloc(eng->scope, (size_t)cap * sizeof(var_bind));
        if (grown == NULL) {        /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
            xp_result_free(&value); /* GCOVR_EXCL_LINE */
            return -1;              /* GCOVR_EXCL_LINE */
        }
        eng->scope = grown;
        eng->scope_cap = cap;
    }
    var_bind *binding = &eng->scope[eng->scope_len++];
    binding->name = (Py_UCS4 *)name;
    binding->name_len = name_len;
    binding->value = value;
    binding->rtf = rtf;
    return 0;
}

static void scope_drop(engine *eng, Py_ssize_t mark) {
    while (eng->scope_len > mark) {
        xp_result_free(&eng->scope[--eng->scope_len].value);
    }
}

/* XPath resolves the first match, so expose the innermost binding first. */
static void scope_bindings(engine *eng, xp_binding *storage, xp_bindings *out) {
    for (Py_ssize_t index = 0; index < eng->scope_len; index++) {
        const var_bind *binding = &eng->scope[eng->scope_len - index - 1];
        storage[index].name = binding->name;
        storage[index].name_len = binding->name_len;
        storage[index].value = binding->value;
    }
    out->items = storage;
    out->len = eng->scope_len;
}

/* ---- the XSLT extension functions ----------------------------------------- */

enum xslt_level { XSLT_LEVEL_TEMPLATE, XSLT_LEVEL_SOURCE, XSLT_LEVEL_ATTRSET, XSLT_LEVEL_KEY };

static int enter_deep_level(engine *eng, enum xslt_level level, const xslt_attrset *set);
static int build_key(engine *eng, xslt_key *key);

static int copy_result_value(const xp_result *src, xp_result *dst);

static int key_union(engine *eng, xslt_key *key, const xp_nodeset *members, xp_nodeset *out);

/* format-number: apply a DecimalFormat picture to a number (default decimal-format
   only: '.' decimal separator, ',' grouping, '#' optional digit, '0' required digit,
   '%' percent, '-' minus). Enough of section 12.3 for the common integer/decimal
   pictures. Returns 0 with *out set, or -1 on allocation failure. */
static int do_format_number(engine *eng, double value, const Py_UCS4 *picture, Py_ssize_t picture_len, xp_result *out) {
    (void)eng;
    /* Split picture into a positive and (optional) '-'-introduced negative subpicture. */
    Py_ssize_t split = -1;
    for (Py_ssize_t index = 0; index < picture_len; index++) {
        if (picture[index] == ';') {
            split = index;
            break;
        }
    }
    int negative = value < 0; /* negative zero formats without a sign, as libxslt does */
    /* A ';'-separated negative subpicture supplies its own prefix/suffix (often
       parentheses) and suppresses the automatic minus sign. */
    const Py_UCS4 *sub = picture;
    Py_ssize_t sub_len = split < 0 ? picture_len : split;
    if (negative && split >= 0) {
        sub = picture + split + 1;
        sub_len = picture_len - split - 1;
        negative = 0;
    }
    xb prefix = {0};
    xb suffix = {0};
    int percent = 0;
    int permille = 0;
    int min_int = 0;
    int frac_min = 0;
    int frac_max = 0;
    int grouping = 0; /* integer-digit positions after the rightmost ',' */
    int since_comma = 0;
    int had_comma = 0;
    int seen_digit = 0;
    int in_frac = 0;
    for (Py_ssize_t index = 0; index < sub_len; index++) {
        Py_UCS4 ch = sub[index];
        if (ch == '0' || ch == '#') {
            seen_digit = 1;
            if (!in_frac) {
                if (ch == '0') {
                    min_int++;
                }
                since_comma++;
            } else if (ch == '0') {
                frac_min++;
                frac_max++;
            } else {
                frac_max++;
            }
        } else if (ch == '.') {
            in_frac = 1;
        } else if (ch == ',') {
            had_comma = 1;
            since_comma = 0;
        } else {
            if (ch == '%') {
                percent = 1;
            } else if (ch == 0x2030) {
                permille = 1;
            }
            if (xb_add_char(seen_digit ? &suffix : &prefix, ch) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                goto oom;                                              /* GCOVR_EXCL_LINE */
            }
        }
    }
    if (had_comma) {
        grouping = since_comma;
    }
    double magnitude = fabs(value);
    if (percent) {
        magnitude *= 100.0;
    }
    if (permille) {
        magnitude *= 1000.0;
    }
    /* Round to frac_max fractional digits. */
    double scale = pow(10.0, frac_max);
    double rounded = floor(magnitude * scale + 0.5) / scale;
    char digits[512];
    int written = snprintf(digits, sizeof(digits), "%.*f", frac_max, rounded);
    (void)written;
    char *dot = strchr(digits, '.');
    char *int_part = digits;
    Py_ssize_t int_len = dot ? (Py_ssize_t)(dot - digits) : (Py_ssize_t)strlen(digits);
    const char *frac_part = dot ? dot + 1 : "";
    Py_ssize_t frac_have = (Py_ssize_t)strlen(frac_part);
    xb result = {0};
    if (negative && xb_add_char(&result, '-') < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
        goto oom2;                                   /* GCOVR_EXCL_LINE */
    }
    if (xb_add(&result, prefix.data, prefix.len) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
        goto oom2;                                      /* GCOVR_EXCL_LINE */
    }
    /* Integer part with left-padding to min_int and optional grouping. */
    Py_ssize_t pad = min_int > int_len ? min_int - int_len : 0;
    Py_ssize_t total_int = int_len > min_int ? int_len : min_int;
    for (Py_ssize_t index = 0; index < total_int; index++) {
        Py_ssize_t from_end = total_int - index;
        if (grouping > 0 && index > 0 && from_end % grouping == 0) {
            if (xb_add_char(&result, ',') < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                goto oom2;                       /* GCOVR_EXCL_LINE */
            }
        }
        char digit = index < pad ? '0' : int_part[index - pad];
        if (xb_add_char(&result, (Py_UCS4)(unsigned char)digit) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            goto oom2;                                                 /* GCOVR_EXCL_LINE */
        }
    }
    Py_ssize_t frac_show = frac_have;
    /* frac_min is never negative, so frac_show > frac_min already implies frac_show > 0. */
    while (frac_show > frac_min && frac_part[frac_show - 1] == '0') {
        frac_show--;
    }
    if (frac_show > 0) {
        if (xb_add_char(&result, '.') < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            goto oom2;                       /* GCOVR_EXCL_LINE */
        }
        for (Py_ssize_t index = 0; index < frac_show; index++) {
            if (xb_add_char(&result, (Py_UCS4)(unsigned char)frac_part[index]) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                goto oom2;                                                            /* GCOVR_EXCL_LINE */
            }
        }
    }
    if (xb_add(&result, suffix.data, suffix.len) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
        goto oom2;                                      /* GCOVR_EXCL_LINE */
    }
    xb_free(&prefix);
    xb_free(&suffix);
    result_string(out, result.data, result.len);
    return 0;
oom2: /* GCOVR_EXCL_START: allocation-failure cleanup */
    xb_free(&result);
oom:
    xb_free(&prefix);
    xb_free(&suffix);
    return -1;
    /* GCOVR_EXCL_STOP */
}

/* Kept out of line so the key() path of xslt_extension compiles as before. */
TH_NOINLINE static int item_id_suffix(char *buffer, size_t size, Py_ssize_t attr) {
    Py_ssize_t rank = xp_item_rank(attr);
    if (rank == PY_SSIZE_T_MAX) {
        return snprintf(buffer, size, "n");
    }
    return snprintf(buffer, size, "a%zd", rank - 1);
}

/* The XPath-engine extension hook: dispatch the XSLT-only functions. Returns 0 when
   handled (out filled), -2 when the name is not an XSLT function (the engine then
   reports an unknown function), -1 on a Python/allocation error. */
static int xslt_extension(void *vctx, xp_item context, const Py_UCS4 *name, Py_ssize_t name_len, const xp_result *args,
                          int argc, xp_result *out) {
    engine *eng = vctx;
    memset(out, 0, sizeof(*out));
    if (ucs4_ascii_eq(name, name_len, "current")) {
        out->kind = XP_NODESET;
        int rc = ns_push(&out->nodes, eng->cur_node, eng->cur_attr);
        if (rc < 0) {  /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1; /* GCOVR_EXCL_LINE */
        }
        return 0;
    }
    if (ucs4_ascii_eq(name, name_len, "key")) {
        if (argc != 2) {
            PyErr_SetString(PyExc_ValueError, "xslt: key() takes two arguments");
            return -1;
        }
        Py_ssize_t key_name_len = 0;
        Py_UCS4 *key_name = to_string(eng->src_tree, &args[0], &key_name_len);
        if (key_name == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;          /* GCOVR_EXCL_LINE */
        }
        xslt_key *key = NULL;
        if (eng->key_index.capacity != 0) {
            const Py_ssize_t position = name_index_find(&eng->key_index, key_name, key_name_len);
            key = position == 0 ? NULL : &eng->keys[position - 1];
        } else {
            for (Py_ssize_t index = 0; index < eng->nkeys; index++) {
                if (str_eq(eng->keys[index].name, eng->keys[index].name_len, key_name, key_name_len)) {
                    key = &eng->keys[index];
                    break;
                }
            }
        }
        PyMem_Free(key_name);
        if (key == NULL) {
            PyErr_SetString(PyExc_ValueError, "xslt: key() names an undeclared key");
            return -1;
        }
        if (!key->built && build_key(eng, key) < 0) {
            return -1;
        }
        out->kind = XP_NODESET;
        /* The lookup value: a node-set contributes each member's string-value, any
           other type its string. A one-member set's string is that member's string-value, so one lookup suffices. */
        if (args[1].kind == XP_NODESET && args[1].nodes.len != 1) {
            return key_union(eng, key, &args[1].nodes, &out->nodes);
        }
        Py_ssize_t value_len = 0;
        Py_UCS4 *value = to_string(eng->src_tree, &args[1], &value_len);
        if (value == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;       /* GCOVR_EXCL_LINE */
        }
        const nodevec *bucket = strmap_lookup(&key->table, value, value_len);
        PyMem_Free(value);
        return bucket == NULL ? 0 : key_append(key, bucket, &out->nodes);
    }
    if (ucs4_ascii_eq(name, name_len, "generate-id")) {
        const th_node *target = context.node;
        Py_ssize_t attr = context.attr;
        if (argc >= 1) {
            if (args[0].kind != XP_NODESET) {
                PyErr_SetString(PyExc_TypeError, "xslt: generate-id() wants a node-set");
                return -1;
            }
            if (args[0].nodes.len == 0) {
                result_string(out, NULL, 0);
                return 0;
            }
            target = args[0].nodes.items[0].node;
            attr = args[0].nodes.items[0].attr;
        }
        /* seq is unique within the one source tree an expression can reach; document() and result tree fragments yield
           no nodes from other trees. An attribute or namespace node shares its element's seq, so a suffix that starts
           with a letter keeps its id apart from the element's and from its siblings'. */
        char buffer[32];
        int written = snprintf(buffer, sizeof(buffer), "id%u", (unsigned)target->seq);
        if (attr != -1) {
            written += item_id_suffix(buffer + written, sizeof(buffer) - (size_t)written, attr);
        }
        Py_ssize_t str_len = 0;
        Py_UCS4 *str = ucs4_from_ascii(buffer, written, &str_len);
        if (str == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;     /* GCOVR_EXCL_LINE */
        }
        result_string(out, str, str_len);
        return 0;
    }
    if (ucs4_ascii_eq(name, name_len, "format-number")) {
        if (argc < 2) {
            PyErr_SetString(PyExc_ValueError, "xslt: format-number() takes at least two arguments");
            return -1;
        }
        double value = to_number(eng->src_tree, &args[0]);
        Py_ssize_t picture_len = 0;
        Py_UCS4 *picture = to_string(eng->src_tree, &args[1], &picture_len);
        if (picture == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;         /* GCOVR_EXCL_LINE */
        }
        int rc = do_format_number(eng, value, picture, picture_len, out);
        PyMem_Free(picture);
        return rc; /* GCOVR_EXCL_BR_LINE: rc<0 is an unforced allocation failure */
    }
    if (ucs4_ascii_eq(name, name_len, "system-property")) {
        if (argc != 1) { /* args is NULL when the call has no arguments */
            PyErr_SetString(PyExc_ValueError, "xslt: system-property() takes one argument");
            return -1;
        }
        Py_ssize_t prop_len = 0;
        Py_UCS4 *prop = to_string(eng->src_tree, &args[0], &prop_len);
        if (prop == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;      /* GCOVR_EXCL_LINE */
        }
        const char *reply = "";
        if (ucs4_ascii_eq(prop, prop_len, "xsl:version")) {
            result_number(out, 1.0);
            PyMem_Free(prop);
            return 0;
        }
        if (ucs4_ascii_eq(prop, prop_len, "xsl:vendor")) {
            reply = "turbohtml";
        } else if (ucs4_ascii_eq(prop, prop_len, "xsl:vendor-url")) {
            reply = "https://github.com/tox-dev/turbohtml";
        }
        PyMem_Free(prop);
        Py_ssize_t str_len = 0;
        Py_UCS4 *str = ucs4_from_ascii(reply, (Py_ssize_t)strlen(reply), &str_len);
        if (str == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;     /* GCOVR_EXCL_LINE */
        }
        result_string(out, str, str_len);
        return 0;
    }
    if (ucs4_ascii_eq(name, name_len, "function-available") || ucs4_ascii_eq(name, name_len, "element-available")) {
        if (argc != 1) {
            PyErr_Format(PyExc_ValueError, "xslt: %s-available() takes one argument",
                         ucs4_ascii_eq(name, name_len, "function-available") ? "function" : "element");
            return -1;
        }
        Py_ssize_t query_len = 0;
        Py_UCS4 *query = to_string(eng->src_tree, &args[0], &query_len);
        if (query == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;       /* GCOVR_EXCL_LINE */
        }
        static const char *const known[] = {
            "current",          "key", "generate-id", "format-number", "system-property", "function-available",
            "element-available"};
        int available = 0;
        for (size_t index = 0; index < sizeof(known) / sizeof(known[0]); index++) {
            if (ucs4_ascii_eq(query, query_len, known[index])) {
                available = 1;
                break;
            }
        }
        PyMem_Free(query);
        result_bool(out, available);
        return 0;
    }
    if (ucs4_ascii_eq(name, name_len, "unparsed-entity-uri") || ucs4_ascii_eq(name, name_len, "document")) {
        /* No external document loading: an empty result, the documented limitation. */
        if (ucs4_ascii_eq(name, name_len, "document")) {
            out->kind = XP_NODESET;
        } else {
            result_string(out, NULL, 0);
        }
        return 0;
    }
    return -2;
}

static int nodeslot_order_cmp(const void *first, const void *second) {
    const Py_ssize_t first_order = ((const nodeslot *)first)->order;
    const Py_ssize_t second_order = ((const nodeslot *)second)->order;
    return (first_order > second_order) - (first_order < second_order);
}

/* key() over a node-set: the union of the lookups for each member's string-value (XSLT 1.0 section 12.2). Members
   that share a value name one bucket, and a multi-valued use files one node under several values, so a bucket is
   walked once and a node taken once; appending a bucket per member instead builds members x bucket entries. Each
   bucket is in match order, which is document order, so the taken nodes are sorted by their match index. */
static int key_union(engine *eng, xslt_key *key, const xp_nodeset *members, xp_nodeset *out) {
    strmap walked = {0};
    match_set taken = {0};
    nodevec ordered = {0};
    int rc = -1;
    for (Py_ssize_t index = 0; index < members->len; index++) {
        Py_ssize_t value_len = 0;
        Py_UCS4 *value = item_string(eng->src_tree, members->items[index], &value_len);
        if (value == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
            goto done;       /* GCOVR_EXCL_LINE */
        }
        const nodevec *bucket = strmap_lookup(&key->table, value, value_len);
        /* strmap_bucket adds a slot only for a value it has not seen, so an unchanged count is a repeated value. */
        const size_t walked_before = walked.count;
        const nodevec *mark = bucket == NULL ? NULL : strmap_bucket(&walked, value, value_len);
        PyMem_Free(value);
        if (bucket == NULL) {
            continue;
        }
        if (mark == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
            goto done;      /* GCOVR_EXCL_LINE */
        }
        if (walked.count == walked_before) {
            continue;
        }
        for (Py_ssize_t slot = 0; slot < bucket->len; slot++) {
            const nodeslot *entry = &bucket->slots[slot];
            const Py_ssize_t attr = key_attr(key, entry);
            if (match_set_has(&taken, entry->node, attr)) {
                continue;
            }
            if (match_set_add(&taken, entry->node, attr) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                goto done;                                      /* GCOVR_EXCL_LINE */
            }
            if (nodevec_push(&ordered, entry->node, entry->order) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                goto done;                                               /* GCOVR_EXCL_LINE */
            }
        }
    }
    qsort(ordered.slots, (size_t)ordered.len, sizeof(nodeslot), nodeslot_order_cmp);
    for (Py_ssize_t slot = 0; slot < ordered.len; slot++) {
        const nodeslot *entry = &ordered.slots[slot];
        if (ns_append(out, entry->node, key_attr(key, entry)) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            goto done;                                               /* GCOVR_EXCL_LINE */
        }
    }
    rc = 0;
done:
    strmap_free(&walked);
    match_set_free(&taken);
    PyMem_Free(ordered.slots);
    return rc;
}

/* Evaluate a compiled program against a context item with the current scope and the XSLT extension functions.
   Returns the xp_eval_at status; fills *out on success. condition is set when the caller only converts *out to a
   boolean (xp_eval_condition_at). */
static int eval_program_as(engine *eng, const xp_program *prog, const xp_item *context, Py_ssize_t pos, Py_ssize_t size,
                           xp_result *out, int condition) {
    xp_binding storage[16];
    xp_binding *bindings = storage;
    if (prog->has_variables && eng->scope_len > 16) {
        bindings = PyMem_Malloc((size_t)eng->scope_len * sizeof(xp_binding));
        if (bindings == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
            fail_py(eng);       /* GCOVR_EXCL_LINE */
            PyErr_NoMemory();   /* GCOVR_EXCL_LINE */
            return -1;          /* GCOVR_EXCL_LINE */
        }
    }
    xp_bindings vars = {0};
    if (prog->has_variables) {
        scope_bindings(eng, bindings, &vars);
    }
    const char *feature = NULL;
    int status = condition ? xp_eval_condition_at(prog, eng->src_tree, context, eng->src_root, pos, size, &vars, NULL,
                                                  xslt_extension, eng, &eng->src_langs, &eng->src_ids, out, &feature)
                           : xp_eval_at(prog, eng->src_tree, context, eng->src_root, pos, size, &vars, NULL,
                                        xslt_extension, eng, &eng->src_langs, &eng->src_ids, out, &feature);
    if (bindings != storage) {
        PyMem_Free(bindings); /* GCOVR_EXCL_LINE: only the >64-binding path allocates */
    }
    if (status < 0 && !PyErr_Occurred()) {
        if (status == -1) {             /* GCOVR_EXCL_BR_LINE: allocation failure */
            PyErr_NoMemory();           /* GCOVR_EXCL_LINE */
        } else {                        /* GCOVR_EXCL_LINE */
            if (feature == NULL) {      /* GCOVR_EXCL_BR_LINE */
                feature = "evaluation"; /* GCOVR_EXCL_LINE */
            } /* GCOVR_EXCL_LINE */
            PyErr_Format(PyExc_ValueError, "xslt: expression error (%s)", feature);
        }
    }
    return status;
}

static int eval_program(engine *eng, const xp_program *prog, const xp_item *context, Py_ssize_t pos, Py_ssize_t size,
                        xp_result *out) {
    return eval_program_as(eng, prog, context, pos, size, out, 0);
}

/* ---- key tables ----------------------------------------------------------- */

/* Whether every item the expression at index selects is a node itself, never an attribute or namespace node: each
   union alternative is a location path whose last step that is not on the self axis is on neither of those axes. */
static int pattern_selects_nodes_only(const xp_program *prog, int32_t index) {
    const xn *expr = &prog->nodes[index];
    if (expr->kind == XN_UNION) {
        return pattern_selects_nodes_only(prog, expr->first) && pattern_selects_nodes_only(prog, expr->second);
    }
    if (expr->kind != XN_PATH || expr->second >= 0) {
        return 0;
    }
    uint8_t axis = AX_SELF;
    for (int32_t step = expr->first; step >= 0; step = prog->nodes[step].next) {
        if (prog->nodes[step].axis != AX_SELF) {
            axis = prog->nodes[step].axis;
        }
    }
    return axis != AX_ATTRIBUTE && axis != AX_NAMESPACE;
}

/* Record that match index `index` of a key is the attribute or namespace node `attr`. The first such match
   allocates the attrs of every index, so a key whose matches are all nodes themselves never pays for them. */
static TH_NOINLINE int key_record_attr(xslt_key *key, Py_ssize_t matches, Py_ssize_t index, Py_ssize_t attr) {
    if (key->attrs == NULL) {
        key->attrs = PyMem_Malloc((size_t)matches * sizeof(Py_ssize_t));
        if (key->attrs == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            PyErr_NoMemory();     /* GCOVR_EXCL_LINE */
            return -1;            /* GCOVR_EXCL_LINE */
        }
        for (Py_ssize_t slot = 0; slot < matches; slot++) {
            key->attrs[slot] = -1;
        }
    }
    key->attrs[index] = attr;
    return 0;
}

/* fill_key for a key whose match can select attribute or namespace nodes: each item is indexed under its own
   values, and the attrs of the matches are recorded for key() to hand back. Frees *matched. */
static TH_NOINLINE int fill_key_items(engine *eng, xslt_key *key, xp_result *matched, int direct_attribute,
                                      uint32_t use_atom) {
    for (Py_ssize_t index = 0; index < matched->nodes.len; index++) {
        th_node *node = matched->nodes.items[index].node;
        Py_ssize_t attr = matched->nodes.items[index].attr;
        if (attr != -1) {
            if (key_record_attr(key, matched->nodes.len, index, attr) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                xp_result_free(matched);                                     /* GCOVR_EXCL_LINE */
                return -1;                                                   /* GCOVR_EXCL_LINE */
            }
        } else if (direct_attribute) {
            th_node_attr *attributes;
            Py_ssize_t count = th_node_attributes(node, &attributes);
            for (Py_ssize_t slot = 0; slot < count; slot++) {
                if (attributes[slot].name_atom != use_atom) {
                    continue;
                }
                const Py_UCS4 empty = 0;
                const Py_UCS4 *value = attributes[slot].value == NULL ? &empty : attributes[slot].value;
                Py_ssize_t value_len = attributes[slot].value == NULL ? 0 : attributes[slot].value_len;
                nodevec *bucket = strmap_bucket_outlined(&key->table, value, value_len);
                if (bucket == NULL || nodevec_push(bucket, node, index) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                    xp_result_free(matched);                                   /* GCOVR_EXCL_LINE */
                    return -1;                                                 /* GCOVR_EXCL_LINE */
                }
                break;
            }
            continue;
        }
        xp_result used;
        int use_status = eval_program(eng, key->use_prog, &(xp_item){node, attr}, 1, 1, &used);
        if (use_status < 0) {
            xp_result_free(matched);
            return -1;
        }
        int rc = 0;
        if (used.kind == XP_NODESET) {
            for (Py_ssize_t slot = 0; slot < used.nodes.len; slot++) {
                xp_item item = used.nodes.items[slot];
                Py_ssize_t value_len = 0;
                Py_UCS4 *owned = NULL;
                const Py_UCS4 *value;
                if (xp_is_attribute(item.attr)) {
                    const th_node_attr *attribute = xp_item_attribute(eng->src_tree, item);
                    value = attribute->value;
                    value_len = value == NULL ? 0 : attribute->value_len;
                } else {
                    owned = item_string(eng->src_tree, item, &value_len);
                    if (owned == NULL) { /* GCOVR_EXCL_START: alloc */
                        rc = -1;
                        break;
                    } /* GCOVR_EXCL_STOP */
                    value = owned;
                }
                Py_UCS4 empty = 0;
                if (value == NULL) {
                    value = &empty;
                }
                nodevec *bucket = strmap_bucket_outlined(&key->table, value, value_len);
                PyMem_Free(owned);
                if (bucket == NULL || nodevec_push(bucket, node, index) < 0) { /* GCOVR_EXCL_START: alloc */
                    rc = -1;
                    break;
                } /* GCOVR_EXCL_STOP */
            }
        } else {
            Py_ssize_t value_len = 0;
            Py_UCS4 *value = to_string(eng->src_tree, &used, &value_len);
            if (value == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
                rc = -1;         /* GCOVR_EXCL_LINE */
            } else {             /* GCOVR_EXCL_LINE */
                nodevec *bucket = strmap_bucket_outlined(&key->table, value, value_len);
                PyMem_Free(value);
                if (bucket == NULL || nodevec_push(bucket, node, index) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                    rc = -1;                                                   /* GCOVR_EXCL_LINE */
                } /* GCOVR_EXCL_LINE */
            }
        }
        xp_result_free(&used);
        if (rc < 0) {                /* GCOVR_EXCL_BR_LINE: alloc */
            xp_result_free(matched); /* GCOVR_EXCL_LINE */
            return -1;               /* GCOVR_EXCL_LINE */
        }
    }
    xp_result_free(matched);
    return 0;
}

/* Populate one key's string->node table: for every source node the key match
   pattern selects, evaluate the use expression and index the node under each
   resulting string value. */
static int fill_key(engine *eng, xslt_key *key) {
    xp_result matched;
    const char *feature = NULL;
    xslt_pattern_scope scope = {.eng = eng, .instruction = key->instruction};
    int xml = th_tree_is_xml(eng->src_tree);
    int callback = xml && pattern_callback_needed(eng, key->match_prog);
    int status = 1;
    if (xml && !callback) {
        status = scan_static_name_pattern(eng, key->match_prog, &matched);
    }
    if (status == 1) {
        status = xml ? xp_eval_pattern_at(key->match_prog, eng->src_tree, eng->src_root, NULL, NULL,
                                          callback ? pattern_name_test : NULL, &scope, &matched, &feature)
                     : xp_eval_at(key->match_prog, eng->src_tree, &(xp_item){eng->src_root, -1}, eng->src_root, 1, 1,
                                  NULL, NULL, NULL, NULL, &eng->src_langs, NULL, &matched, &feature);
    }
    if (status < 0) { /* GCOVR_EXCL_BR_LINE: the key match compiled, so it evaluates */
        PyErr_Format(PyExc_ValueError, "xslt: key match failed"); /* GCOVR_EXCL_LINE */
        return fail_py(eng);                                      /* GCOVR_EXCL_LINE */
    }
    uint32_t use_atom;
    int direct_attribute = xp_single_attribute_atom(key->use_prog, eng->src_tree, &use_atom);
    /* A separate loop, so a key over elements, the common case, runs the loop it ran before attributes could
       match: the attr test per item kept the compiler from hoisting direct_attribute out of it. */
    if (!pattern_selects_nodes_only(key->match_prog, key->match_prog->root)) {
        return fill_key_items(eng, key, &matched, direct_attribute, use_atom);
    }
    for (Py_ssize_t index = 0; index < matched.nodes.len; index++) {
        th_node *node = matched.nodes.items[index].node;
        if (direct_attribute) {
            th_node_attr *attributes;
            Py_ssize_t count = th_node_attributes(node, &attributes);
            for (Py_ssize_t slot = 0; slot < count; slot++) {
                if (attributes[slot].name_atom != use_atom) {
                    continue;
                }
                const Py_UCS4 empty = 0;
                const Py_UCS4 *value = attributes[slot].value == NULL ? &empty : attributes[slot].value;
                Py_ssize_t value_len = attributes[slot].value == NULL ? 0 : attributes[slot].value_len;
                nodevec *bucket = strmap_bucket(&key->table, value, value_len);
                if (bucket == NULL || nodevec_push(bucket, node, index) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                    xp_result_free(&matched);                                  /* GCOVR_EXCL_LINE */
                    return -1;                                                 /* GCOVR_EXCL_LINE */
                }
                break;
            }
            continue;
        }
        xp_result used;
        int use_status = eval_program(eng, key->use_prog, &(xp_item){node, -1}, 1, 1, &used);
        if (use_status < 0) {
            xp_result_free(&matched);
            return -1;
        }
        int rc = 0;
        if (used.kind == XP_NODESET) {
            for (Py_ssize_t slot = 0; slot < used.nodes.len; slot++) {
                xp_item item = used.nodes.items[slot];
                Py_ssize_t value_len = 0;
                Py_UCS4 *owned = NULL;
                const Py_UCS4 *value;
                if (xp_is_attribute(item.attr)) {
                    const th_node_attr *attribute = xp_item_attribute(eng->src_tree, item);
                    value = attribute->value;
                    value_len = value == NULL ? 0 : attribute->value_len;
                } else {
                    owned = item_string(eng->src_tree, item, &value_len);
                    if (owned == NULL) { /* GCOVR_EXCL_START: alloc */
                        rc = -1;
                        break;
                    } /* GCOVR_EXCL_STOP */
                    value = owned;
                }
                Py_UCS4 empty = 0;
                if (value == NULL) {
                    value = &empty;
                }
                nodevec *bucket = strmap_bucket(&key->table, value, value_len);
                PyMem_Free(owned);
                if (bucket == NULL || nodevec_push(bucket, node, index) < 0) { /* GCOVR_EXCL_START: alloc */
                    rc = -1;
                    break;
                } /* GCOVR_EXCL_STOP */
            }
        } else {
            Py_ssize_t value_len = 0;
            Py_UCS4 *value = to_string(eng->src_tree, &used, &value_len);
            if (value == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
                rc = -1;         /* GCOVR_EXCL_LINE */
            } else {             /* GCOVR_EXCL_LINE */
                nodevec *bucket = strmap_bucket(&key->table, value, value_len);
                PyMem_Free(value);
                if (bucket == NULL || nodevec_push(bucket, node, index) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                    rc = -1;                                                   /* GCOVR_EXCL_LINE */
                } /* GCOVR_EXCL_LINE */
            }
        }
        xp_result_free(&used);
        if (rc < 0) {                 /* GCOVR_EXCL_BR_LINE: alloc */
            xp_result_free(&matched); /* GCOVR_EXCL_LINE */
            return -1;                /* GCOVR_EXCL_LINE */
        }
    }
    xp_result_free(&matched);
    return 0;
}

/* A use expression may call key() on a key that is not built yet, which builds that one from inside this build, so a
   chain of keys nests here. Each build takes a level of the template-nesting cap. A nested build also checks the stack
   at any depth: a level of the chain, with its use expression, takes several times the stack XSLT_STACK_CHECK_DEPTH
   assumes, so 32 unchecked levels overflow a 128 KiB thread, and a key builds at most once per run. */
static int build_key(engine *eng, xslt_key *key) {
    if ((++eng->depth > XSLT_STACK_CHECK_DEPTH || eng->key_builds > 0) &&
        enter_deep_level(eng, XSLT_LEVEL_KEY, NULL) < 0) {
        return -1;
    }
    key->built = 1;
    eng->key_builds++;
    int rc = fill_key(eng, key);
    eng->key_builds--;
    eng->depth--;
    return rc;
}

static int scan_static_name_pattern(engine *eng, const xp_program *prog, xp_result *matched) {
    const xn *path = &prog->nodes[prog->root];
    if (path->kind != XN_PATH || !path->absolute || path->first < 0) {
        return 1;
    }
    const xn *step = &prog->nodes[path->first];
    if (step->axis != AX_DESCENDANT || step->test != NT_NAME || step->first >= 0 || step->next >= 0) {
        return 1;
    }
    *matched = (xp_result){.kind = XP_NODESET};
    for (th_node *node = eng->src_root->first_child; node != NULL;) {
        if (node->type == TH_NODE_ELEMENT && node->ns == TH_NS_HTML && node->text_len == step->str_len &&
            memcmp(node->text, step->str, (size_t)step->str_len * sizeof(Py_UCS4)) == 0) {
            if (ns_push(&matched->nodes, node, -1) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                xp_result_free(matched);                  /* GCOVR_EXCL_LINE */
                return -1;                                /* GCOVR_EXCL_LINE */
            }
        }
        if (node->first_child != NULL) {
            node = node->first_child;
        } else {
            while (node != eng->src_root && node->next_sibling == NULL) {
                node = node->parent;
            }
            node = node == eng->src_root ? NULL : node->next_sibling;
        }
    }
    return 0;
}

/* ---- template matching ---------------------------------------------------- */

/* Populate a rule's matched-node set by evaluating its compiled pattern from the
   source root once and recording the selected items. A "/" rule never comes here: best_rule answers it from its own
   chain. Out of line: it runs once per rule, and inlined into
   apply_to_item its locals would sit in that frame on every level of an xsl:apply-templates recursion. */
TH_NOINLINE static int build_rule(engine *eng, xslt_rule *rule) {
    rule->built = 1;
    xp_result matched;
    const char *feature = NULL;
    xslt_pattern_scope scope = {.eng = eng, .instruction = rule->body};
    int status = th_tree_is_xml(eng->src_tree)
                     ? xp_eval_pattern_at(rule->prog, eng->src_tree, eng->src_root, xslt_extension, eng,
                                          pattern_callback_needed(eng, rule->prog) ? pattern_name_test : NULL, &scope,
                                          &matched, &feature)
                     : xp_eval_at(rule->prog, eng->src_tree, &(xp_item){eng->src_root, -1}, eng->src_root, 1, 1, NULL,
                                  NULL, xslt_extension, eng, &eng->src_langs, NULL, &matched, &feature);
    if (status < 0) {
        if (!PyErr_Occurred()) {   /* GCOVR_EXCL_BR_LINE: pattern evaluation sets an exception only on allocation */
            if (feature == NULL) { /* GCOVR_EXCL_BR_LINE: a -3/-4 error always names a feature */
                feature = "evaluation"; /* GCOVR_EXCL_LINE */
            } /* GCOVR_EXCL_LINE */
            PyErr_Format(PyExc_ValueError, "xslt: match pattern error (%s)", feature);
        }
        return fail_py(eng);
    }
    if (match_set_reserve(&rule->matched, (size_t)matched.nodes.len) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
        xp_result_free(&matched);                                           /* GCOVR_EXCL_LINE */
        return -1;                                                          /* GCOVR_EXCL_LINE */
    }
    for (Py_ssize_t index = 0; index < matched.nodes.len; index++) {
        int added = match_set_add(&rule->matched, matched.nodes.items[index].node, matched.nodes.items[index].attr);
        if (added < 0) {              /* GCOVR_EXCL_BR_LINE: alloc */
            xp_result_free(&matched); /* GCOVR_EXCL_LINE */
            return -1;                /* GCOVR_EXCL_LINE */
        }
    }
    xp_result_free(&matched);
    return 0;
}

static size_t dispatch_slot(const xslt_dispatch_entry *entries, size_t capacity, const th_node *node, Py_ssize_t attr,
                            const Py_UCS4 *mode, Py_ssize_t mode_len) {
    const size_t mode_hash = mode == NULL ? 0 : str_hash(mode, mode_len);
    size_t slot = th_hash_slot(node_hash(node, attr) ^ (uint32_t)mode_hash, capacity);
    for (size_t walked = 0;
         entries[slot].node != NULL &&
         (entries[slot].node != node || entries[slot].attr != attr || (entries[slot].mode == NULL) != (mode == NULL) ||
          (mode != NULL && !str_eq(entries[slot].mode, entries[slot].mode_len, mode, mode_len)));
         walked++) {
        slot = node_next(slot, walked, capacity - 1, node, attr, mode_hash); /* one item's modes stride apart */
    }
    return slot;
}

static int dispatch_grow(engine *eng) {
    size_t capacity;
    size_t bytes;
    /* GCOVR_EXCL_BR_START: alloc */
    if (!th_grow_cap(eng->dispatch_capacity + 1, eng->dispatch_capacity, 32, sizeof(*eng->dispatch), &capacity,
                     &bytes)) {
        return -1; /* GCOVR_EXCL_LINE */
    }
    /* GCOVR_EXCL_BR_STOP */
    xslt_dispatch_entry *entries = PyMem_Calloc(1, bytes);
    if (entries == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;         /* GCOVR_EXCL_LINE */
    }
    for (size_t index = 0; index < eng->dispatch_capacity; index++) {
        const xslt_dispatch_entry *entry = &eng->dispatch[index];
        if (entry->node != NULL) {
            entries[dispatch_slot(entries, capacity, entry->node, entry->attr, entry->mode, entry->mode_len)] = *entry;
        }
    }
    PyMem_Free(eng->dispatch);
    eng->dispatch = entries;
    eng->dispatch_capacity = capacity;
    return 0;
}

/* The best-matching rule for (node, attr) in the given mode, or NULL for none. The
   rule array is pre-sorted by descending (priority, position), so the
   first match wins the section 5.5 conflict resolution. The chains visit only the
   rules that can match node, in that same order, so a rule no node reaches is never built. */
static xslt_rule *best_rule(engine *eng, th_node *node, Py_ssize_t attr, const Py_UCS4 *mode, Py_ssize_t mode_len) {
    size_t slot = 0;
    const int cache = eng->rule_dispatch;
    if (cache) {
        if (eng->dispatch_count == eng->dispatch_capacity / 2) {
            /* GCOVR_EXCL_BR_START: alloc */
            if (dispatch_grow(eng) < 0) {
                PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
                fail_py(eng);     /* GCOVR_EXCL_LINE */
                return NULL;      /* GCOVR_EXCL_LINE */
            }
            /* GCOVR_EXCL_BR_STOP */
        }
        slot = dispatch_slot(eng->dispatch, eng->dispatch_capacity, node, attr, mode, mode_len);
        if (eng->dispatch[slot].node != NULL) {
            return eng->dispatch[slot].rule;
        }
    }
    Py_ssize_t named = -1;
    if (eng->rule_names.capacity != 0 && attr < 0 && node->type == TH_NODE_ELEMENT) {
        Py_ssize_t local_len;
        Py_ssize_t prefix_len;
        const Py_UCS4 *local = qname_local(node, &local_len, &prefix_len);
        named = name_index_find(&eng->rule_names, local, local_len) - 1;
    }
    Py_ssize_t wild = eng->rule_wild;
    Py_ssize_t root = node == eng->src_root ? eng->rule_root : -1;
    xslt_rule *winner = NULL;
    while (named >= 0 || wild >= 0 || root >= 0) {
        xslt_rule *rule;
        if (root >= 0 && (wild < 0 || root < wild)) {
            /* only the root reaches this chain, and a "/" pattern matches the root alone, so it needs no match set */
            rule = &eng->rules[root];
            root = rule->next;
            if ((rule->mode == NULL) == (mode == NULL) &&
                (mode == NULL || str_eq(rule->mode, rule->mode_len, mode, mode_len))) {
                winner = rule;
                break;
            }
            continue;
        }
        if (wild < 0 || (named >= 0 && named < wild)) {
            rule = &eng->rules[named];
            named = rule->next;
        } else {
            rule = &eng->rules[wild];
            wild = rule->next;
        }
        int rule_default = rule->mode == NULL;
        int want_default = mode == NULL;
        if (rule_default != want_default) {
            continue;
        }
        if (!want_default && !str_eq(rule->mode, rule->mode_len, mode, mode_len)) {
            continue;
        }
        if (!rule->built && build_rule(eng, rule) < 0) {
            if (!PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: match-set allocation failure */
                PyErr_NoMemory();    /* GCOVR_EXCL_LINE */
            } /* GCOVR_EXCL_LINE */
            fail_py(eng);
            return NULL;
        }
        if (match_set_has(&rule->matched, node, attr)) {
            winner = rule;
            break;
        }
    }
    if (cache) {
        eng->dispatch[slot] = (xslt_dispatch_entry){node, attr, mode, mode_len, winner};
        eng->dispatch_count++;
    }
    return winner;
}

/* ---- instruction instantiation -------------------------------------------- */

static int instantiate_body(engine *eng, th_node *body, th_node *out_parent);
static int apply_templates(engine *eng, th_node *instruction, th_node *out_parent, const Py_UCS4 *mode,
                           Py_ssize_t mode_len);
static const Py_UCS4 XPATH_DOT = '.';

static size_t expression_hash(const Py_UCS4 *source, Py_ssize_t length) {
    uintptr_t pointer = (uintptr_t)source;
    return (size_t)(pointer ^ (pointer >> 17) ^ (uintptr_t)length * 0x9e3779b97f4a7c15ULL);
}

static xp_program *expression_lookup(const engine *eng, const Py_UCS4 *source, Py_ssize_t length) {
    if (eng->expression_cap == 0) {
        return NULL;
    }
    size_t slot = expression_hash(source, length) & (eng->expression_cap - 1);
    while (eng->expressions[slot].program != NULL) {
        /* A stylesheet buffer address identifies one immutable expression slice. */
        /* GCOVR_EXCL_BR_START */
        if (eng->expressions[slot].source == source && eng->expressions[slot].length == length) {
            return eng->expressions[slot].program;
        }
        /* GCOVR_EXCL_BR_STOP */
        slot = (slot + 1) & (eng->expression_cap - 1);
    }
    return NULL;
}

static int expression_grow(engine *eng) {
    size_t new_cap = eng->expression_cap == 0 ? 16 : eng->expression_cap * 2;
    /* A stylesheet cannot exhaust size_t. */
    /* GCOVR_EXCL_BR_START */
    if (new_cap < eng->expression_cap || new_cap > SIZE_MAX / sizeof(xslt_expr)) {
        return -1; /* GCOVR_EXCL_LINE */
    }
    /* GCOVR_EXCL_BR_STOP */
    xslt_expr *expressions = PyMem_Calloc(new_cap, sizeof(xslt_expr));
    if (expressions == NULL) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
        return -1;             /* GCOVR_EXCL_LINE */
    }
    for (size_t index = 0; index < eng->expression_cap; index++) {
        xslt_expr expression = eng->expressions[index];
        if (expression.program == NULL) {
            continue;
        }
        size_t slot = expression_hash(expression.source, expression.length) & (new_cap - 1);
        while (expressions[slot].program != NULL) {
            slot = (slot + 1) & (new_cap - 1);
        }
        expressions[slot] = expression;
    }
    PyMem_Free(eng->expressions);
    eng->expressions = expressions;
    eng->expression_cap = new_cap;
    return 0;
}

static xp_program *compile_expression(engine *eng, const Py_UCS4 *source, Py_ssize_t length, char *error,
                                      size_t error_size) {
    xp_program *program = expression_lookup(eng, source, length);
    if (program != NULL) {
        return program;
    }
    if (!eng->owns_model) { /* GCOVR_EXCL_BR_LINE: compilation covers every immutable stylesheet expression */
        snprintf(error, error_size, "stylesheet expression was not compiled"); /* GCOVR_EXCL_LINE */
        return NULL;                                                           /* GCOVR_EXCL_LINE */
    }
    program = xp_compile(source, length, error, error_size, PyExc_ValueError);
    if (program == NULL) {
        return NULL;
    }
    /* Growth fails only on allocation or size overflow. */
    /* GCOVR_EXCL_BR_START */
    if ((eng->expression_count + 1) * 2 >= eng->expression_cap && expression_grow(eng) < 0) {
        xp_free(program); /* GCOVR_EXCL_LINE */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
        return NULL;      /* GCOVR_EXCL_LINE */
    }
    /* GCOVR_EXCL_BR_STOP */
    size_t slot = expression_hash(source, length) & (eng->expression_cap - 1);
    while (eng->expressions[slot].program != NULL) {
        slot = (slot + 1) & (eng->expression_cap - 1);
    }
    eng->expressions[slot] = (xslt_expr){source, length, program};
    eng->expression_count++;
    return program;
}

static xp_program *pattern_lookup(const engine *eng, const Py_UCS4 *source, Py_ssize_t length) {
    if (eng->pattern_cap == 0) {
        return NULL;
    }
    size_t slot = expression_hash(source, length) & (eng->pattern_cap - 1);
    while (eng->patterns[slot].program != NULL) {
        /* A stylesheet buffer address identifies one immutable pattern slice. */
        /* GCOVR_EXCL_BR_START */
        if (eng->patterns[slot].source == source && eng->patterns[slot].length == length) {
            return eng->patterns[slot].program;
        }
        /* GCOVR_EXCL_BR_STOP */
        slot = (slot + 1) & (eng->pattern_cap - 1);
    }
    return NULL;
}

static int pattern_grow(engine *eng) {
    size_t new_cap = eng->pattern_cap == 0 ? 16 : eng->pattern_cap * 2;
    /* A stylesheet cannot exhaust size_t. */
    /* GCOVR_EXCL_BR_START */
    if (new_cap < eng->pattern_cap || new_cap > SIZE_MAX / sizeof(xslt_expr)) {
        return -1; /* GCOVR_EXCL_LINE */
    }
    /* GCOVR_EXCL_BR_STOP */
    xslt_expr *patterns = PyMem_Calloc(new_cap, sizeof(xslt_expr));
    if (patterns == NULL) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
        return -1;          /* GCOVR_EXCL_LINE */
    }
    for (size_t index = 0; index < eng->pattern_cap; index++) {
        xslt_expr pattern = eng->patterns[index];
        if (pattern.program == NULL) {
            continue;
        }
        size_t slot = expression_hash(pattern.source, pattern.length) & (new_cap - 1);
        while (patterns[slot].program != NULL) {
            slot = (slot + 1) & (new_cap - 1);
        }
        patterns[slot] = pattern;
    }
    PyMem_Free(eng->patterns);
    eng->patterns = patterns;
    eng->pattern_cap = new_cap;
    return 0;
}

static xp_program *compile_pattern(engine *eng, const Py_UCS4 *source, Py_ssize_t length) {
    xp_program *program = pattern_lookup(eng, source, length);
    if (program != NULL) {
        return program;
    }
    if (!eng->owns_model) { /* GCOVR_EXCL_BR_LINE: compilation covers every immutable stylesheet pattern */
        PyErr_SetString(PyExc_RuntimeError, "xslt: stylesheet pattern was not compiled"); /* GCOVR_EXCL_LINE */
        fail_py(eng);                                                                     /* GCOVR_EXCL_LINE */
        return NULL;                                                                      /* GCOVR_EXCL_LINE */
    }
    program = compile_pattern_new(eng, source, length);
    if (program == NULL) {
        return NULL;
    }
    /* Growth fails only on allocation or size overflow. */
    /* GCOVR_EXCL_BR_START */
    if ((eng->pattern_count + 1) * 2 >= eng->pattern_cap && pattern_grow(eng) < 0) {
        xp_free(program); /* GCOVR_EXCL_LINE */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
        fail_py(eng);     /* GCOVR_EXCL_LINE */
        return NULL;      /* GCOVR_EXCL_LINE */
    }
    /* GCOVR_EXCL_BR_STOP */
    size_t slot = expression_hash(source, length) & (eng->pattern_cap - 1);
    while (eng->patterns[slot].program != NULL) {
        slot = (slot + 1) & (eng->pattern_cap - 1);
    }
    eng->patterns[slot] = (xslt_expr){source, length, program};
    eng->pattern_count++;
    return program;
}

/* Evaluate an attribute value template ("literal {expr} literal") into freshly
   allocated code points. Returns 0 with the buffer and its length set through out_data
   and out_len (the caller PyMem_Frees the buffer), or -1 on error. */
static int eval_avt(engine *eng, const Py_UCS4 *src, Py_ssize_t len, Py_UCS4 **out_data, Py_ssize_t *out_len) {
    xb buffer = {0};
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_UCS4 ch = src[index];
        if (ch == '{' /* GCOVR_EXCL_BR_LINE: compilation rejects a trailing open brace */ && index + 1 < len &&
            src[index + 1] == '{') {
            if (xb_add_char(&buffer, '{') < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                goto oom;                        /* GCOVR_EXCL_LINE */
            }
            index++;
        } else if (ch == '}' && index + 1 < len && src[index + 1] == '}') {
            if (xb_add_char(&buffer, '}') < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                goto oom;                        /* GCOVR_EXCL_LINE */
            }
            index++;
        } else if (ch == '{') {
            Py_ssize_t start = index + 1;
            Py_ssize_t end = start;
            Py_UCS4 quote = 0;
            while (end < len && (quote != 0 || src[end] != '}')) {
                if (quote != 0) {
                    if (src[end] == quote) {
                        quote = 0;
                    }
                } else if (src[end] == '\'' || src[end] == '"') {
                    quote = src[end];
                }
                end++;
            }
            char errbuf[256];
            xp_program *prog = compile_expression(eng, src + start, end - start, errbuf, sizeof(errbuf));
            /* A run only receives AVTs that precompile_stylesheet validated. */
            /* GCOVR_EXCL_START */
            if (prog == NULL) {
                xb_free(&buffer);
                if (!PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: allocation error */
                    PyErr_Format(PyExc_ValueError, "xslt: bad expression in attribute value template: %s", errbuf);
                }
                fail_py(eng);
                return -1;
            }
            /* GCOVR_EXCL_STOP */
            xp_result value;
            int status =
                eval_program(eng, prog, &(xp_item){eng->cur_node, eng->cur_attr}, eng->ctx_pos, eng->ctx_size, &value);
            if (status < 0) {
                xb_free(&buffer);
                fail_py(eng);
                return -1;
            }
            Py_ssize_t value_len = 0;
            Py_UCS4 *text = to_string(eng->src_tree, &value, &value_len);
            xp_result_free(&value);
            if (text == NULL) {   /* GCOVR_EXCL_BR_LINE: alloc */
                xb_free(&buffer); /* GCOVR_EXCL_LINE */
                goto oom;         /* GCOVR_EXCL_LINE */
            }
            int rc = xb_add(&buffer, text, value_len);
            PyMem_Free(text);
            if (rc < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                goto oom; /* GCOVR_EXCL_LINE */
            }
            index = end;
        } else if (xb_add_char(&buffer, ch) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            goto oom;                              /* GCOVR_EXCL_LINE */
        }
    }
    *out_data = buffer.data;
    *out_len = buffer.len;
    return 0;
oom: /* GCOVR_EXCL_START: allocation-failure path */
    xb_free(&buffer);
    fail(eng, "out of memory");
    return -1;
    /* GCOVR_EXCL_STOP */
}

/* Append a text node holding `data` to out_parent, merging is left to serialization. */
static int emit_text(engine *eng, th_node *out_parent, const Py_UCS4 *data, Py_ssize_t len) {
    if (len == 0) {
        return 0;
    }
    th_node *node = th_tree_make_data_node(eng->out_tree, TH_NODE_TEXT, data, len);
    if (node == NULL) {                    /* GCOVR_EXCL_BR_LINE: alloc */
        return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
    }
    th_node_append_child(out_parent, node);
    return 0;
}

/* Whether an expression is the self-context ".", which needs special handling when
   the current item is an attribute (the XPath evaluator has no attribute context). */
static int is_self_dot(const Py_UCS4 *src, Py_ssize_t len) {
    Py_ssize_t start;
    Py_ssize_t trimmed;
    trim(src, len, &start, &trimmed);
    return trimmed == 1 && src[start] == '.';
}

/* The string value of the current item (element/text/attribute/...), freshly allocated. */
static Py_UCS4 *current_string(engine *eng, Py_ssize_t *out_len) {
    xp_item item = {eng->cur_node, eng->cur_attr};
    return item_string(eng->src_tree, item, out_len);
}

/* Instantiate the string value of a select expression as text (xsl:value-of). */
static int do_value_of(engine *eng, th_node *instruction, th_node *out_parent) {
    Py_ssize_t select_len = 0;
    const Py_UCS4 *select = attr_lookup(eng->sheet_tree, instruction, "select", 6, &select_len);
    if (select == NULL) {
        return fail(eng, "xsl:value-of requires a select attribute");
    }
    if (xp_is_attribute(eng->cur_attr) && is_self_dot(select, select_len)) {
        Py_ssize_t text_len = 0;
        Py_UCS4 *text = current_string(eng, &text_len);
        if (text == NULL) {                    /* GCOVR_EXCL_BR_LINE: alloc */
            return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
        }
        int rc = emit_text(eng, out_parent, text, text_len);
        PyMem_Free(text);
        return rc;
    }
    xp_program *prog = compile_expression(eng, select, select_len, eng->errbuf, sizeof(eng->errbuf));
    if (prog == NULL) {          /* GCOVR_EXCL_BR_LINE: compilation rejects malformed selects before a run */
        if (!PyErr_Occurred()) { /* GCOVR_EXCL_LINE: allocation error */
            PyErr_Format(PyExc_ValueError, "xslt: bad value-of select: %s", eng->errbuf); /* GCOVR_EXCL_LINE */
        } /* GCOVR_EXCL_LINE */
        return fail_py(eng); /* GCOVR_EXCL_LINE */
    }
    xp_result value;
    int status = eval_program(eng, prog, &(xp_item){eng->cur_node, eng->cur_attr}, eng->ctx_pos, eng->ctx_size, &value);
    if (status < 0) {
        return fail_py(eng);
    }
    Py_ssize_t text_len = 0;
    Py_UCS4 *text = to_string(eng->src_tree, &value, &text_len);
    xp_result_free(&value);
    if (text == NULL) {                    /* GCOVR_EXCL_BR_LINE: alloc */
        return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
    }
    int rc = emit_text(eng, out_parent, text, text_len);
    PyMem_Free(text);
    return rc;
}

static int number_has_namespaces(engine *eng);

static int name_is_prefixed(const Py_UCS4 *name, Py_ssize_t len) {
    for (Py_ssize_t index = 0; index < len; index++) {
        if (name[index] == ':') {
            return 1;
        }
    }
    return 0;
}

static const th_node_attr *xmlns_decl(th_tree *tree, const th_node *element) {
    for (Py_ssize_t index = 0; index < element->attr_count; index++) {
        Py_ssize_t name_len = 0;
        const char *name = th_attr_name(tree, element->attrs[index].name_atom, &name_len);
        if (name_len == 5 && memcmp(name, "xmlns", 5) == 0) {
            return &element->attrs[index];
        }
    }
    return NULL;
}

/* The nearest default namespace declaration on a stylesheet element or one of its ancestors, or NULL. */
static const th_node_attr *sheet_default_decl(engine *eng, const th_node *element) {
    for (const th_node *node = element; node != NULL; node = node->parent) {
        const th_node_attr *decl = xmlns_decl(eng->sheet_tree, node);
        if (decl != NULL) {
            return decl;
        }
    }
    return NULL;
}

/* Whether the nearest default namespace declaration on source node `node` or an ancestor is non-empty. A walk stops at
   a node an earlier walk answered and records its answer for the nodes it passed, so a run answers each node once
   rather than rewalking a deep ancestor chain for every copy. Returns -1 when the memo cannot grow. */
static int source_namespaced(engine *eng, const th_node *node) {
    const th_node *stop = node;
    int namespaced = 0;
    for (; stop != NULL; stop = stop->parent) {
        TH_WORK_STEP();
        Py_ssize_t known = th_node_map_find(&eng->src_namespaced, stop);
        if (known != 0) {
            namespaced = known == 1;
            break;
        }
        const th_node_attr *decl = xmlns_decl(eng->src_tree, stop);
        if (decl != NULL) {
            namespaced = decl->value_len > 0;
            break;
        }
    }
    for (const th_node *walk = node; walk != stop; walk = walk->parent) {
        if (th_node_map_insert(&eng->src_namespaced, walk, namespaced ? 1 : 2) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;                                                                /* GCOVR_EXCL_LINE */
        }
    }
    return namespaced;
}

/* Whether source element `node` is in no namespace: unprefixed, and neither it nor an ancestor declares a non-empty
   default namespace. A source with no namespace declarations needs no walk. Returns -1 when the memo cannot grow. */
static int source_in_no_namespace(engine *eng, const th_node *node) {
    if (name_is_prefixed(node->text, node->text_len)) {
        return 0;
    }
    if (!number_has_namespaces(eng)) {
        return 1;
    }
    int namespaced = source_namespaced(eng, node);
    if (namespaced < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;        /* GCOVR_EXCL_LINE */
    }
    return !namespaced;
}

/* Writes the empty default namespace, which undeclares the default namespace an element inherits from its output
   parent, so the element stays in no namespace when the output is parsed again. */
static int undeclare_default(engine *eng, th_node *element) {
    if (th_node_attr_set(eng->out_tree, element, "xmlns", 5, NULL, 0, 1) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
        return fail(eng, "out of memory");                                      /* GCOVR_EXCL_LINE */
    }
    return 0;
}

/* Instantiate the content of a result element, with `bound` as the default-namespace state inside it and `scope` as the
   output source for the copies inside it (see engine.out_src). */
static int instantiate_under(engine *eng, th_node *body, th_node *element, int bound, th_node *scope) {
    int saved = eng->default_bound;
    th_node *saved_src = eng->out_src;
    eng->default_bound = bound;
    eng->out_src = scope;
    int rc = instantiate_body(eng, body, element);
    eng->default_bound = saved;
    eng->out_src = saved_src;
    return rc;
}

/* Instantiate a body into a fragment root, which no copy's output parent reaches, so no output source carries into it.
 */
static int instantiate_detached(engine *eng, th_node *body, th_node *root) {
    th_node *saved_src = eng->out_src;
    eng->out_src = NULL;
    int rc = instantiate_body(eng, body, root);
    eng->out_src = saved_src;
    return rc;
}

static int output_ns_in_scope(engine *eng, th_node *start, const char *name, Py_ssize_t name_len, const Py_UCS4 *value,
                              Py_ssize_t value_len);
static int pattern_declaration(xslt_declarations *decls, th_tree *tree, th_node *node, const char *prefix,
                               Py_ssize_t prefix_len, const th_node_attr **declared);

static int is_xmlns_name(const char *name, Py_ssize_t name_len) {
    return (name_len == 5 && memcmp(name, "xmlns", 5) == 0) || (name_len > 6 && memcmp(name, "xmlns:", 6) == 0);
}

static int declares_namespace(th_tree *tree, const th_node *node) {
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        Py_ssize_t name_len = 0;
        const char *name = th_attr_name(tree, node->attrs[index].name_atom, &name_len);
        if (is_xmlns_name(name, name_len)) {
            return 1;
        }
    }
    return 0;
}

/* The nearest ancestor-or-self of source node `node` that declares a namespace, or NULL when none does. A walk stops at
   a node an earlier walk answered and records its answer for the nodes it passed, so each node is climbed once per run.
 */
static int scope_nearest(engine *eng, th_node *node, th_node **nearest) {
    xslt_scope_memo *memo = &eng->src_scope;
    th_node *stop = node;
    th_node *found = NULL;
    Py_ssize_t cached = 0;
    for (; stop != NULL; stop = stop->parent) {
        TH_WORK_STEP();
        cached = th_node_map_find(&memo->resolved, stop);
        if (cached != 0) {
            break;
        }
        if (declares_namespace(eng->src_tree, stop)) {
            found = stop;
            break;
        }
    }
    Py_ssize_t value = cached;
    if (cached != 0) {
        found = memo->found[cached - 1];
    } else {
        if (memo->found_count == memo->found_capacity) {
            size_t capacity;
            size_t bytes;
            /* GCOVR_EXCL_BR_START: allocation failure */
            if (!th_grow_cap(memo->found_count + 1, memo->found_capacity, 16, sizeof(*memo->found), &capacity,
                             &bytes)) {
                return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
            }
            th_node **grown = PyMem_Realloc(memo->found, bytes);
            if (grown == NULL) {                   /* GCOVR_EXCL_BR_LINE: alloc */
                return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
            }
            /* GCOVR_EXCL_BR_STOP */
            memo->found = grown;
            memo->found_capacity = capacity;
        }
        memo->found[memo->found_count++] = found;
        value = (Py_ssize_t)memo->found_count;
    }
    for (th_node *walk = node; walk != stop; walk = walk->parent) {
        if (th_node_map_insert(&memo->resolved, walk, value) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            return fail(eng, "out of memory");                      /* GCOVR_EXCL_LINE */
        }
    }
    if (cached == 0 && stop != NULL && th_node_map_insert(&memo->resolved, stop, value) < 0) { /* GCOVR_EXCL_BR_LINE */
        return fail(eng, "out of memory");                                                     /* GCOVR_EXCL_LINE */
    }
    *nearest = found;
    return 0;
}

/* Writes source declaration `decl` onto the copy unless the output parent already binds its prefix to the same URI. */
static int copy_namespace_node(engine *eng, th_node *out_parent, th_node *copy, const th_node_attr *decl) {
    Py_ssize_t name_len = 0;
    const char *name = th_attr_name(eng->src_tree, decl->name_atom, &name_len);
    if (output_ns_in_scope(eng, out_parent, name, name_len, decl->value, decl->value_len)) {
        return 0;
    }
    int rc = th_node_attr_set(eng->out_tree, copy, name, name_len, decl->value, decl->value_len, 1);
    if (rc < 0) {                          /* GCOVR_EXCL_BR_LINE: alloc */
        return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
    }
    return 0;
}

/* Copies the declarations of declaring node `node` that bind their prefix for source element `src`: a prefix counts
   only at the nearest declaration, and the first default declaration seen is the nearest one. An empty binding is no
   namespace node, and the xml prefix is always in scope. A verbatim copy already carries the element's own
   declarations. */
static int copy_declared(engine *eng, th_node *out_parent, th_node *copy, th_node *src, th_node *node, int verbatim,
                         int *seen_default) {
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        const th_node_attr *decl = &node->attrs[index];
        Py_ssize_t name_len = 0;
        const char *name = th_attr_name(eng->src_tree, decl->name_atom, &name_len);
        if (!is_xmlns_name(name, name_len)) {
            continue;
        }
        if (name_len == 5) {
            if (*seen_default) {
                continue;
            }
            *seen_default = 1;
        } else {
            const th_node_attr *nearest = NULL;
            int resolved =
                pattern_declaration(&eng->src_declarations, eng->src_tree, src, name + 6, name_len - 6, &nearest);
            if (resolved < 0) {                    /* GCOVR_EXCL_BR_LINE: alloc */
                return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
            }
            if (nearest != decl) {
                continue;
            }
        }
        if (decl->value_len == 0 || (name_len == 9 && memcmp(name, "xmlns:xml", 9) == 0) || (verbatim && node == src)) {
            continue;
        }
        if (copy_namespace_node(eng, out_parent, copy, decl) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;                                              /* GCOVR_EXCL_LINE */
        }
    }
    return 0;
}

/* Writes the namespace nodes of source element `src` onto its copy, which is appended to out_parent. The namespace
   nodes of a copied element are every declaration in scope at it. When the output parent is inside the copy of the
   source's parent, that copy's scope already holds the inherited ones, so only the element's own declarations are
   checked; otherwise each declaring ancestor is walked. */
static int copy_namespaces(engine *eng, th_node *out_parent, th_node *copy, th_node *src, int verbatim) {
    int seen_default = 0;
    if (eng->out_src != NULL && eng->out_src == src->parent) {
        return copy_declared(eng, out_parent, copy, src, src, verbatim, &seen_default);
    }
    th_node *node = NULL;
    if (scope_nearest(eng, src, &node) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;                            /* GCOVR_EXCL_LINE */
    }
    while (node != NULL) {
        int rc = copy_declared(eng, out_parent, copy, src, node, verbatim, &seen_default);
        if (rc < 0) {  /* GCOVR_EXCL_BR_LINE: alloc */
            return -1; /* GCOVR_EXCL_LINE */
        }
        if (scope_nearest(eng, node->parent, &node) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;                                     /* GCOVR_EXCL_LINE */
        }
    }
    return 0;
}

/* Deep-copy a source subtree into the output tree, appended to out_parent. */
static int copy_of_node(engine *eng, th_node *out_parent, xp_item item) {
    if (xp_is_attribute(item.attr)) {
        const th_node_attr *attr = xp_item_attribute(eng->src_tree, item);
        Py_ssize_t name_len = 0;
        const char *attr_name = th_attr_name(eng->src_tree, attr->name_atom, &name_len);
        if (out_parent->type == TH_NODE_ELEMENT) {
            int rc = th_node_attr_set(eng->out_tree, out_parent, attr_name, name_len, attr->value, attr->value_len, 1);
            if (rc < 0) {                          /* GCOVR_EXCL_BR_LINE: alloc */
                return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
            }
        }
        return 0;
    }
    th_node *copy = th_tree_copy_node(eng->out_tree, eng->src_tree, item.node);
    if (copy == NULL) {                    /* GCOVR_EXCL_BR_LINE: alloc */
        return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
    }
    th_node_append_child(out_parent, copy);
    if (item.node->type == TH_NODE_ELEMENT && number_has_namespaces(eng)) {
        if (copy_namespaces(eng, out_parent, copy, item.node, 1) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;                                                  /* GCOVR_EXCL_LINE */
        }
    }
    if (eng->default_bound && item.node->type == TH_NODE_ELEMENT && xmlns_decl(eng->out_tree, copy) == NULL) {
        int no_namespace = source_in_no_namespace(eng, item.node);
        if (no_namespace < 0) {                /* GCOVR_EXCL_BR_LINE: alloc */
            return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
        }
        if (no_namespace && undeclare_default(eng, copy) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;                                          /* GCOVR_EXCL_LINE */
        }
    }
    return 0;
}

static int do_copy_of(engine *eng, th_node *instruction, th_node *out_parent) {
    Py_ssize_t select_len = 0;
    const Py_UCS4 *select = attr_lookup(eng->sheet_tree, instruction, "select", 6, &select_len);
    if (select == NULL) {
        return fail(eng, "xsl:copy-of requires a select attribute");
    }
    xp_program *prog = compile_expression(eng, select, select_len, eng->errbuf, sizeof(eng->errbuf));
    if (prog == NULL) {          /* GCOVR_EXCL_BR_LINE: compilation rejects malformed selects before a run */
        if (!PyErr_Occurred()) { /* GCOVR_EXCL_LINE: allocation error */
            PyErr_Format(PyExc_ValueError, "xslt: bad copy-of select: %s", eng->errbuf); /* GCOVR_EXCL_LINE */
        } /* GCOVR_EXCL_LINE */
        return fail_py(eng); /* GCOVR_EXCL_LINE */
    }
    /* A lone $var that is a result tree fragment copies the fragment's children. */
    if (prog->nodes[prog->root].kind == XN_VAR) {
        const xn *var = &prog->nodes[prog->root];
        for (Py_ssize_t index = eng->scope_len - 1; index >= 0; index--) {
            if (str_eq(eng->scope[index].name, eng->scope[index].name_len, var->str, var->str_len) &&
                eng->scope[index].rtf != NULL) {
                for (th_node *child = eng->scope[index].rtf->first_child; child != NULL; child = child->next_sibling) {
                    th_node *copy = th_tree_copy_node(eng->out_tree, eng->out_tree, child);
                    if (copy == NULL) {                    /* GCOVR_EXCL_BR_LINE: alloc */
                        return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
                    }
                    if (eng->default_bound && child->type == TH_NODE_ELEMENT &&
                        !name_is_prefixed(child->text, child->text_len) && xmlns_decl(eng->out_tree, copy) == NULL) {
                        if (undeclare_default(eng, copy) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                            return -1;                          /* GCOVR_EXCL_LINE */
                        }
                    }
                    th_node_append_child(out_parent, copy);
                }
                return 0;
            }
        }
    }
    xp_result value;
    int status = eval_program(eng, prog, &(xp_item){eng->cur_node, eng->cur_attr}, eng->ctx_pos, eng->ctx_size, &value);
    if (status < 0) {
        return fail_py(eng);
    }
    if (value.kind == XP_NODESET) {
        for (Py_ssize_t index = 0; index < value.nodes.len; index++) {
            if (copy_of_node(eng, out_parent, value.nodes.items[index]) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                xp_result_free(&value);                                        /* GCOVR_EXCL_LINE */
                return -1;                                                     /* GCOVR_EXCL_LINE */
            }
        }
        xp_result_free(&value);
        return 0;
    }
    Py_ssize_t text_len = 0;
    Py_UCS4 *text = to_string(eng->src_tree, &value, &text_len);
    xp_result_free(&value);
    if (text == NULL) {                    /* GCOVR_EXCL_BR_LINE: alloc */
        return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
    }
    int rc = emit_text(eng, out_parent, text, text_len);
    PyMem_Free(text);
    return rc;
}

/* Instantiate the string content of an instruction body into a fresh buffer, the way
   xsl:attribute/comment/processing-instruction collect their text. */
static int instantiate_string(engine *eng, th_node *body, Py_UCS4 **out_data, Py_ssize_t *out_len) {
    th_node *fragment = th_tree_make_fragment(eng->out_tree);
    if (fragment == NULL) {                /* GCOVR_EXCL_BR_LINE: alloc */
        return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
    }
    if (instantiate_detached(eng, body, fragment) < 0) {
        return -1;
    }
    xb buffer = {0};
    for (th_node *child = fragment->first_child; child != NULL; child = child->next_sibling) {
        Py_ssize_t child_len = 0;
        Py_UCS4 *child_text = th_node_text(eng->out_tree, child, &child_len);
        if (child_text == NULL) {              /* GCOVR_EXCL_BR_LINE: alloc */
            xb_free(&buffer);                  /* GCOVR_EXCL_LINE */
            return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
        }
        int rc = xb_add(&buffer, child_text, child_len);
        PyMem_Free(child_text);
        if (rc < 0) {                          /* GCOVR_EXCL_BR_LINE: alloc */
            xb_free(&buffer);                  /* GCOVR_EXCL_LINE */
            return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
        }
    }
    *out_data = buffer.data;
    *out_len = buffer.len;
    return 0;
}

static int slots_push(xslt_slots *slots, Py_ssize_t slot) {
    if (slots->len == slots->cap) {
        size_t cap;
        size_t bytes;
        /* GCOVR_EXCL_BR_START: size overflow and allocation failure */
        if (!th_grow_cap((size_t)slots->len + 1, (size_t)slots->cap, 8, sizeof(Py_ssize_t), &cap, &bytes)) {
            return -1; /* GCOVR_EXCL_LINE */
        }
        Py_ssize_t *grown = PyMem_Realloc(slots->items, bytes);
        if (grown == NULL) {
            return -1; /* GCOVR_EXCL_LINE */
        }
        /* GCOVR_EXCL_BR_STOP */
        slots->items = grown;
        slots->cap = (Py_ssize_t)cap;
    }
    slots->items[slots->len++] = slot;
    return 0;
}

/* Append the slot of the last definition of every set a use-attribute-sets value names, in order; a name defined
   several times stands for each definition, which attrset_before chains from that slot. */
static int attrsets_named(const engine *eng, const Py_UCS4 *names, Py_ssize_t names_len, xslt_slots *slots) {
    Py_ssize_t index = 0;
    while (index < names_len) {
        while (index < names_len && ucs4_is_ws(names[index])) {
            index++;
        }
        Py_ssize_t start = index;
        while (index < names_len && !ucs4_is_ws(names[index])) {
            index++;
        }
        if (index == start) {
            break;
        }
        Py_ssize_t slot = attrset_find(eng, names + start, index - start);
        if (slot >= 0 && slots_push(slots, slot) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;                                  /* GCOVR_EXCL_LINE */
        }
    }
    return 0;
}

static int raise_attrset_depth(engine *eng, const xslt_attrset *set) {
    PyObject *name = make_str(set->name, set->name_len);
    if (name != NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        PyErr_Format(PyExc_RecursionError,
                     "xslt: xsl:attribute-set '%U' exceeds the nesting limit of %d levels, which use-attribute-sets "
                     "chains share with template nesting; shorten the chain",
                     name, XSLT_MAX_DEPTH);
        Py_DECREF(name);
    }
    return fail_py(eng);
}

/* Admit a level deeper than XSLT_STACK_CHECK_DEPTH, or release it and raise RecursionError when it passes the cap or
   the thread's C stack has no room for it; `set` names the attribute set of an XSLT_LEVEL_ATTRSET level. Keeping the
   cap here lets each caller test one condition, which the cap's tests drive both ways, and leaves shallow levels one
   comparison.

   The interpreter's own guard compares the stack pointer with the thread's real limit only from CPython 3.14; before
   that it counts calls. Only its stack check is wanted, so the call is released at once. On Linux, macOS and Windows
   the thread's bounds are also read directly (th_stack_floor), which covers every Python version. Elsewhere an older
   interpreter relies on 400 levels fitting the thread's stack. */
TH_NOINLINE static int enter_deep_level(engine *eng, enum xslt_level level, const xslt_attrset *set) {
    if (eng->depth > XSLT_MAX_DEPTH) {
        eng->depth--;
        if (level == XSLT_LEVEL_ATTRSET) {
            return raise_attrset_depth(eng, set);
        }
        if (level == XSLT_LEVEL_KEY) {
            PyErr_Format(PyExc_RecursionError,
                         "xslt: building an xsl:key exceeds the nesting limit of %d levels, which key() calls in use "
                         "expressions share with template nesting; shorten the chain of keys",
                         XSLT_MAX_DEPTH);
            return fail_py(eng);
        }
        if (level == XSLT_LEVEL_SOURCE) {
            PyErr_Format(PyExc_RecursionError,
                         "xslt: source nesting exceeds %d levels for the built-in template rules; reduce the input "
                         "document depth",
                         XSLT_MAX_DEPTH);
            return fail_py(eng);
        }
        PyErr_Format(PyExc_RecursionError,
                     "xslt: template nesting exceeds %d levels; reduce the recursion depth, or iterate over long "
                     "lists with xsl:for-each",
                     XSLT_MAX_DEPTH);
        return fail_py(eng);
    }
#ifdef TH_STACK_FLOOR
    if (eng->stack_floor == 0) {
        eng->stack_floor = th_stack_floor();
    }
    if (TH_FRAME_ADDRESS() < eng->stack_floor) {
        eng->depth--;
        PyErr_SetString(PyExc_RecursionError,
                        "xslt: template nesting needs more C stack than this thread has left; reduce the recursion "
                        "depth, or run the transform on a thread with a larger stack");
        return fail_py(eng);
    }
#endif
    /* after the floor: CPython 3.14 aborts the process instead of raising when called from a frame already past its
       stack limit. Which check answers first depends on the thread's stack, so the coverage jobs may not take it. */
    if (Py_EnterRecursiveCall(" while applying an XSLT stylesheet") != 0) { /* GCOVR_EXCL_BR_LINE */
        eng->depth--;                                                       /* GCOVR_EXCL_LINE */
        return fail_py(eng);                                                /* GCOVR_EXCL_LINE */
    }
    Py_LeaveRecursiveCall();
    return 0;
}

/* Walking references last to first and recording each set at first sighting yields, reversed, the section 7.1.4
   expansion with each set kept at its last position, the one that decides its attribute values. slot is the last
   definition of a name and seen marks the name, whose definitions are always expanded together. */
static int order_attrset(engine *eng, Py_ssize_t slot, unsigned char *seen, xslt_slots *order) {
    if (seen[slot]) {
        return 0;
    }
    if (++eng->depth > XSLT_STACK_CHECK_DEPTH && enter_deep_level(eng, XSLT_LEVEL_ATTRSET, &eng->attrsets[slot]) < 0) {
        return -1;
    }
    seen[slot] = 1;
    int rc = 0;
    for (; rc == 0 && slot >= 0; slot = attrset_before(eng, slot)) {
        const xslt_attrset *set = &eng->attrsets[slot];
        rc = slots_push(order, slot) < 0 ? fail(eng, "out of memory") : 0; /* GCOVR_EXCL_BR_LINE: alloc */
        for (Py_ssize_t index = set->uses_end - 1; rc == 0 && index >= set->uses_start; index--) {
            TH_WORK_STEP();
            rc = order_attrset(eng, eng->attrset_uses.items[index], seen, order);
        }
    }
    eng->depth--;
    return rc;
}

/* A set the section 7.1.4 expansion reaches more than once writes the same attributes each time, so only its last
   occurrence runs; running it once per path would grow exponentially with the nesting of shared sets. */
static int apply_attribute_sets(engine *eng, const Py_UCS4 *names, Py_ssize_t names_len, th_node *out_element) {
    xslt_slots roots = {0};
    if (attrsets_named(eng, names, names_len, &roots) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
        return fail(eng, "out of memory");                   /* GCOVR_EXCL_LINE */
    }
    /* The common reference names one set that uses no other and is defined once: it expands to itself alone. */
    if (roots.len == 1) {
        const xslt_attrset *set = &eng->attrsets[roots.items[0]];
        if (set->uses_start == set->uses_end && attrset_before(eng, roots.items[0]) < 0) {
            PyMem_Free(roots.items);
            return instantiate_body(eng, set->body, out_element);
        }
    }
    xslt_slots order = {0};
    unsigned char *seen = PyMem_Calloc((size_t)eng->nattrsets, 1);
    int rc = seen == NULL ? fail(eng, "out of memory") : 0; /* GCOVR_EXCL_BR_LINE: alloc */
    for (Py_ssize_t index = roots.len - 1; rc == 0 && index >= 0; index--) {
        rc = order_attrset(eng, roots.items[index], seen, &order);
    }
    for (Py_ssize_t index = order.len - 1; rc == 0 && index >= 0; index--) {
        rc = instantiate_body(eng, eng->attrsets[order.items[index]].body, out_element);
    }
    PyMem_Free(seen);
    PyMem_Free(order.items);
    PyMem_Free(roots.items);
    return rc;
}

enum computed_name { NAME_ELEMENT, NAME_ATTRIBUTE, NAME_NAMESPACED_ATTRIBUTE, NAME_PI_TARGET };

/* A node name an XSLT instruction computes reaches the output unescaped, so XSLT 1.0 7.1.2, 7.1.3 and 7.3 hold it to
   these Namespaces in XML productions. */
static int xml_is_ncname(const Py_UCS4 *name, Py_ssize_t len) {
    if (len == 0 || name[0] == ':' || !is_name_start(name[0])) {
        return 0;
    }
    for (Py_ssize_t index = 1; index < len; index++) {
        if (name[index] == ':' || !is_name_char(name[index])) {
            return 0;
        }
    }
    return 1;
}

static int xml_is_qname(const Py_UCS4 *name, Py_ssize_t len) {
    for (Py_ssize_t colon = 0; colon < len; colon++) {
        if (name[colon] == ':') {
            return xml_is_ncname(name, colon) && xml_is_ncname(name + colon + 1, len - colon - 1);
        }
    }
    return xml_is_ncname(name, len);
}

/* A computed name reaches the output unescaped, so source data could spell extra markup. A name with the xmlns prefix
   would declare a namespace; only an attribute with a namespace URI may keep it, since 7.1.3 re-prefixes that one. */
static int computed_name_valid(enum computed_name kind, const Py_UCS4 *name, Py_ssize_t len) {
    if (kind == NAME_PI_TARGET) {
        return xml_is_ncname(name, len) &&
               !(len == 3 && lower_ascii(name[0]) == 'x' && lower_ascii(name[1]) == 'm' && lower_ascii(name[2]) == 'l');
    }
    return xml_is_qname(name, len) && !(kind != NAME_ELEMENT && ucs4_ascii_eq(name, len, "xmlns")) &&
           !(kind != NAME_NAMESPACED_ATTRIBUTE && len > 6 && ucs4_ascii_eq(name, 6, "xmlns:"));
}

/* XSLT 1.0 7.1.2 recovery for an invalid element name: the content lands in the parent without the element. It is
   built in a fragment first, where xsl:attribute finds no element and drops the attributes the element would carry. */
static int instantiate_without_element(engine *eng, th_node *instruction, th_node *out_parent) {
    th_node *holder = th_tree_make_fragment(eng->out_tree);
    if (holder == NULL) {                  /* GCOVR_EXCL_BR_LINE: alloc */
        return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
    }
    if (instantiate_detached(eng, instruction, holder) < 0) {
        return -1;
    }
    while (holder->first_child != NULL) {
        th_node *child = holder->first_child;
        th_node_remove(child);
        th_node_append_child(out_parent, child);
    }
    return 0;
}

static Py_ssize_t qname_colon(const Py_UCS4 *name, Py_ssize_t len) {
    for (Py_ssize_t index = 0; index < len; index++) {
        if (name[index] == ':') {
            return index;
        }
    }
    return -1;
}

/* The namespace an xsl:element creates its element in (section 7.1.2): the namespace attribute's value when present,
   else the stylesheet's binding of the name's prefix, or of the default namespace for an unprefixed name. *evaluated
   owns the buffer it returns. A NULL *uri or a zero *uri_len is no namespace. Returns -1 on error. */
static int element_namespace(engine *eng, th_node *instruction, const Py_UCS4 *name, Py_ssize_t colon,
                             Py_UCS4 **evaluated, const Py_UCS4 **uri, Py_ssize_t *uri_len) {
    *evaluated = NULL;
    Py_ssize_t ns_avt_len = 0;
    const Py_UCS4 *ns_avt = attr_lookup(eng->sheet_tree, instruction, "namespace", 9, &ns_avt_len);
    if (ns_avt != NULL) {
        if (eval_avt(eng, ns_avt, ns_avt_len, evaluated, uri_len) < 0) {
            return -1;
        }
        *uri = *evaluated;
        return 0;
    }
    if (colon < 0) {
        const th_node_attr *decl = sheet_default_decl(eng, instruction);
        *uri = decl == NULL ? NULL : decl->value;
        *uri_len = decl == NULL ? 0 : decl->value_len;
        return 0;
    }
    if (ucs4_ascii_eq(name, colon, "xml")) {
        *evaluated = ucs4_from_ascii(XP_XML_NS_URI, sizeof(XP_XML_NS_URI) - 1, uri_len);
        if (*evaluated == NULL) {              /* GCOVR_EXCL_BR_LINE: alloc */
            return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
        }
        *uri = *evaluated;
        return 0;
    }
    Py_ssize_t prefix_len = 0;
    char *prefix = ucs4_to_utf8(name, colon, &prefix_len);
    if (prefix == NULL) {                  /* GCOVR_EXCL_BR_LINE: alloc */
        return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
    }
    const th_node_attr *declared;
    int resolved =
        pattern_declaration(&eng->sheet_declarations, eng->sheet_tree, instruction, prefix, prefix_len, &declared);
    PyMem_Free(prefix);
    if (resolved < 0) {                    /* GCOVR_EXCL_BR_LINE: alloc */
        return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
    }
    if (declared == NULL) {
        return fail(eng, "xsl:element name uses an undeclared prefix");
    }
    *uri = declared->value;
    *uri_len = declared->value_len;
    return 0;
}

static int qname_join(xb *qname, const char *prefix, const Py_UCS4 *local, Py_ssize_t local_len) {
    if (xb_add_ascii(qname, prefix) < 0 || xb_add_char(qname, ':') < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;                                                        /* GCOVR_EXCL_LINE */
    }
    return xb_add(qname, local, local_len);
}

/* Declares xmlns:prefix (or xmlns when prefix_len is 0) on element to uri, unless an output ancestor already binds that
   prefix to uri. */
static int declare_element_ns(engine *eng, th_node *element, th_node *out_parent, const Py_UCS4 *prefix,
                              Py_ssize_t prefix_len, const Py_UCS4 *uri, Py_ssize_t uri_len) {
    xb decl = {0};
    int added = prefix_len > 0 ? qname_join(&decl, "xmlns", prefix, prefix_len) : xb_add_ascii(&decl, "xmlns");
    if (added < 0) {                       /* GCOVR_EXCL_BR_LINE: alloc */
        xb_free(&decl);                    /* GCOVR_EXCL_LINE */
        return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t decl_len = 0;
    char *utf8 = ucs4_to_utf8(decl.data, decl.len, &decl_len);
    xb_free(&decl);
    if (utf8 == NULL) {                    /* GCOVR_EXCL_BR_LINE: alloc */
        return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
    }
    int in_scope = output_ns_in_scope(eng, out_parent, utf8, decl_len, uri, uri_len);
    if (in_scope < 0) {                    /* GCOVR_EXCL_BR_LINE: alloc */
        PyMem_Free(utf8);                  /* GCOVR_EXCL_LINE */
        return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
    }
    int rc = in_scope ? 0 : th_node_attr_set(eng->out_tree, element, utf8, decl_len, uri, uri_len, 1);
    PyMem_Free(utf8);
    if (rc < 0) {                          /* GCOVR_EXCL_BR_LINE: alloc */
        return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
    }
    return 0;
}

/* Creates the element xsl:element names, in namespace uri (section 7.1.2), and declares what that needs: the default
   namespace for an unprefixed name, the name's prefix otherwise, or a generated ns_N when the prefix is reserved. */
static int instantiate_element(engine *eng, th_node *instruction, th_node *out_parent, const Py_UCS4 *name,
                               Py_ssize_t name_len, Py_ssize_t colon, const Py_UCS4 *uri, Py_ssize_t uri_len) {
    const Py_UCS4 *local = name + colon + 1;
    Py_ssize_t local_len = name_len - colon - 1;
    xb qname = {0};
    const Py_UCS4 *text = name;
    Py_ssize_t text_len = name_len;
    Py_ssize_t text_colon = colon;
    int bound = eng->default_bound;
    int declare = 0;
    int xml = eng->output_method == OUT_XML;
    if (uri_len == 0) {
        text = local;
        text_len = local_len;
        text_colon = -1;
        bound = 0;
    } else if (ucs4_ascii_eq(uri, uri_len, XP_XML_NS_URI)) {
        if (qname_join(&qname, "xml", local, local_len) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            xb_free(&qname);                                   /* GCOVR_EXCL_LINE */
            return fail(eng, "out of memory");                 /* GCOVR_EXCL_LINE */
        }
        text = qname.data;
        text_len = qname.len;
        text_colon = 3;
    } else if (colon < 0) {
        bound = xml;
        declare = xml;
    } else {
        declare = xml;
        if (ucs4_ascii_eq(name, colon, "xml")) {
            char generated[32];
            int made = snprintf(generated, sizeof(generated), "ns_%d", ++eng->ns_counter);
            if (qname_join(&qname, generated, local, local_len) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                xb_free(&qname);                                       /* GCOVR_EXCL_LINE */
                return fail(eng, "out of memory");                     /* GCOVR_EXCL_LINE */
            }
            text = qname.data;
            text_len = qname.len;
            text_colon = made;
        }
    }
    uint16_t atom = atom_for_name(text, text_len);
    th_node *element = th_tree_make_element(eng->out_tree, text, text_len, atom, 0);
    if (element == NULL) {                 /* GCOVR_EXCL_BR_LINE: alloc */
        xb_free(&qname);                   /* GCOVR_EXCL_LINE */
        return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
    }
    th_node_append_child(out_parent, element);
    int rc = 0;
    if (uri_len == 0) {
        if (eng->default_bound) {
            rc = undeclare_default(eng, element);
        }
    } else if (declare) {
        Py_ssize_t prefix_len = text_colon < 0 ? 0 : text_colon;
        rc = declare_element_ns(eng, element, out_parent, text, prefix_len, uri, uri_len);
    }
    xb_free(&qname);
    if (rc < 0) {  /* GCOVR_EXCL_BR_LINE: alloc */
        return -1; /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t use_len = 0;
    const Py_UCS4 *use = attr_lookup(eng->sheet_tree, instruction, "use-attribute-sets", 18, &use_len);
    if (use != NULL && apply_attribute_sets(eng, use, use_len, element) < 0) {
        return -1;
    }
    return instantiate_under(eng, instruction, element, bound, NULL);
}

/* xsl:element name={avt} namespace={avt}: create an element in its namespace and instantiate its body inside it. */
static int do_element(engine *eng, th_node *instruction, th_node *out_parent) {
    Py_ssize_t name_len = 0;
    const Py_UCS4 *name_avt = attr_lookup(eng->sheet_tree, instruction, "name", 4, &name_len);
    if (name_avt == NULL) {
        return fail(eng, "xsl:element requires a name attribute");
    }
    Py_UCS4 *name;
    Py_ssize_t resolved_len = 0;
    if (eval_avt(eng, name_avt, name_len, &name, &resolved_len) < 0) {
        return -1;
    }
    if (!computed_name_valid(NAME_ELEMENT, name, resolved_len)) {
        PyMem_Free(name);
        return instantiate_without_element(eng, instruction, out_parent);
    }
    Py_ssize_t colon = qname_colon(name, resolved_len);
    Py_UCS4 *evaluated;
    const Py_UCS4 *uri = NULL;
    Py_ssize_t uri_len = 0;
    int rc = element_namespace(eng, instruction, name, colon, &evaluated, &uri, &uri_len);
    if (rc == 0) {
        rc = instantiate_element(eng, instruction, out_parent, name, resolved_len, colon, uri, uri_len);
    }
    PyMem_Free(evaluated);
    PyMem_Free(name);
    return rc;
}

static uint32_t out_binding_hash(const th_node *element, const Py_UCS4 *uri, Py_ssize_t uri_len) {
    return th_name_hash(uri, uri_len * (Py_ssize_t)sizeof(Py_UCS4)) ^ (uint32_t)th_key_mix(element->seq);
}

/* The slot holding element's binding of uri, or the free slot it would take. The hash is keyed, so a document cannot
   choose URIs that share one run of slots. */
static xslt_out_binding *out_binding_slot(const engine *eng, const th_node *element, uint32_t hash, const Py_UCS4 *uri,
                                          Py_ssize_t uri_len) {
    size_t slot = th_hash_slot(hash, eng->out_bindings_cap);
    for (;;) {
        xslt_out_binding *entry = &eng->out_bindings[slot];
        /* & rather than &&: unequal texts under equal keyed hashes never occur, so no test reaches that branch */
        if (entry->element == NULL ||
            ((entry->element == element) & (entry->hash == hash) & str_eq(entry->uri, entry->uri_len, uri, uri_len))) {
            return entry;
        }
        TH_WORK_STEP();
        slot = (slot + 1) & (eng->out_bindings_cap - 1);
    }
}

static int out_binding_current(const th_node *element, const xslt_out_binding *entry) {
    return element->attrs[entry->position].value == entry->uri;
}

static int out_bindings_reserve(engine *eng) {
    if (eng->out_bindings_len + 1 <= eng->out_bindings_cap / 2) {
        return 0;
    }
    size_t capacity;
    size_t bytes;
    /* GCOVR_EXCL_BR_START: allocation failure */
    if (!th_grow_cap(eng->out_bindings_cap + 1, eng->out_bindings_cap, 16, sizeof(xslt_out_binding), &capacity,
                     &bytes)) {
        return -1; /* GCOVR_EXCL_LINE */
    }
    xslt_out_binding *grown = PyMem_Calloc(1, bytes);
    if (grown == NULL) {
        return -1; /* GCOVR_EXCL_LINE */
    }
    /* GCOVR_EXCL_BR_STOP */
    xslt_out_binding *old = eng->out_bindings;
    size_t old_cap = eng->out_bindings_cap;
    eng->out_bindings = grown;
    eng->out_bindings_cap = capacity;
    for (size_t index = 0; index < old_cap; index++) {
        if (old[index].element != NULL) {
            *out_binding_slot(eng, old[index].element, old[index].hash, old[index].uri, old[index].uri_len) =
                old[index];
        }
    }
    PyMem_Free(old);
    return 0;
}

/* Record the xmlns:prefix attributes element gained since the last call. The first position recorded for a URI is
   kept while it still binds that URI, which is the declaration a scan of the array meets first. */
static int out_bindings_record(engine *eng, th_node *element) {
    Py_ssize_t seen = th_node_map_find(&eng->out_bindings_seen, element);
    if (seen == 0) {
        if (th_node_map_insert(&eng->out_bindings_seen, element, 1) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;                                                     /* GCOVR_EXCL_LINE */
        }
        seen = 1;
    }
    if (out_bindings_reserve(eng) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;                       /* GCOVR_EXCL_LINE */
    }
    for (Py_ssize_t index = seen - 1; index < element->attr_count; index++) {
        TH_WORK_STEP();
        const th_node_attr *attr = &element->attrs[index];
        Py_ssize_t decl_len = 0;
        const char *decl = th_attr_name(eng->out_tree, attr->name_atom, &decl_len);
        if (decl_len <= 6 || memcmp(decl, "xmlns:", 6) != 0 || attr->value_len == 0) {
            continue;
        }
        if (out_bindings_reserve(eng) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;                       /* GCOVR_EXCL_LINE */
        }
        uint32_t hash = out_binding_hash(element, attr->value, attr->value_len);
        xslt_out_binding *entry = out_binding_slot(eng, element, hash, attr->value, attr->value_len);
        if (entry->element == NULL) {
            *entry = (xslt_out_binding){element, attr->value, attr->value_len, index, hash};
            eng->out_bindings_len++;
        } else if (!out_binding_current(element, entry)) {
            entry->uri = attr->value;
            entry->position = index;
        }
    }
    eng->out_bindings_seen.entries[th_node_map_slot(&eng->out_bindings_seen, element)].value = element->attr_count + 1;
    return 0;
}

/* The prefix out_parent already binds to nsuri, or NULL in *prefix. A large element answers from out_bindings, since
   each namespaced xsl:attribute can add a declaration and an attribute to the same element. When a recorded
   declaration was since rebound, the answer is NULL even if another declaration of the element binds nsuri, and the
   caller declares a fresh prefix. Returns -1 on allocation failure. */
static int output_prefix_for_uri(engine *eng, th_node *out_parent, const Py_UCS4 *nsuri, Py_ssize_t nsuri_len,
                                 const char **prefix, Py_ssize_t *prefix_len) {
    *prefix = NULL;
    if (out_parent->attr_count >= TH_ATTR_INDEX_MIN) {
        if (out_bindings_record(eng, out_parent) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;                                  /* GCOVR_EXCL_LINE */
        }
        const xslt_out_binding *entry =
            out_binding_slot(eng, out_parent, out_binding_hash(out_parent, nsuri, nsuri_len), nsuri, nsuri_len);
        if (entry->element != NULL && out_binding_current(out_parent, entry)) {
            Py_ssize_t decl_len = 0;
            const char *decl = th_attr_name(eng->out_tree, out_parent->attrs[entry->position].name_atom, &decl_len);
            *prefix = decl + 6;
            *prefix_len = decl_len - 6;
        }
        return 0;
    }
    for (Py_ssize_t index = 0; index < out_parent->attr_count; index++) {
        const th_node_attr *decl_attr = &out_parent->attrs[index];
        Py_ssize_t decl_len = 0;
        const char *decl = th_attr_name(eng->out_tree, decl_attr->name_atom, &decl_len);
        if (decl_len <= 6) {
            continue;
        }
        static const char xmlns[] = "xmlns:";
        int is_xmlns = 1;
        for (int probe = 0; probe < 6; probe++) {
            if (decl[probe] != xmlns[probe]) {
                is_xmlns = 0;
                break;
            }
        }
        if (!is_xmlns) {
            continue;
        }
        if (decl_attr->value_len != nsuri_len) {
            continue;
        }
        int same_uri = memcmp(decl_attr->value, nsuri, (size_t)nsuri_len * sizeof(Py_UCS4)) == 0;
        if (!same_uri) {
            continue;
        }
        *prefix = decl + 6;
        *prefix_len = decl_len - 6;
        return 0;
    }
    return 0;
}

/* xsl:attribute name={avt}: set an attribute on the containing result element. */
/* Place a namespaced attribute (xsl:attribute with a namespace) on out_parent under section
   7.1.3: reuse a prefix the element already binds to that URI, else declare a fresh generated
   ns_N prefix, then set the "prefix:local" attribute. */
static int do_attribute_ns(engine *eng, th_node *out_parent, const Py_UCS4 *name, Py_ssize_t name_len,
                           const Py_UCS4 *nsuri, Py_ssize_t nsuri_len, const Py_UCS4 *value, Py_ssize_t value_len) {
    Py_ssize_t local_start = 0;
    for (Py_ssize_t index = 0; index < name_len; index++) {
        if (name[index] == ':') {
            local_start = index + 1;
        }
    }
    const char *prefix;
    Py_ssize_t prefix_len = 0;
    if (output_prefix_for_uri(eng, out_parent, nsuri, nsuri_len, &prefix, &prefix_len) < 0) { /* GCOVR_EXCL_BR_LINE */
        return fail(eng, "out of memory");                                                    /* GCOVR_EXCL_LINE */
    }
    char generated[32];
    if (prefix == NULL) {
        int made = snprintf(generated, sizeof(generated), "ns_%d", ++eng->ns_counter);
        char decl[40];
        int decl_len = snprintf(decl, sizeof(decl), "xmlns:%s", generated);
        int rc = th_node_attr_set(eng->out_tree, out_parent, decl, decl_len, nsuri, nsuri_len, 1);
        if (rc < 0) {                          /* GCOVR_EXCL_BR_LINE: alloc */
            return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
        }
        prefix = generated;
        prefix_len = made;
    }
    xb qname = {0};
    for (Py_ssize_t index = 0; index < prefix_len; index++) {
        if (xb_add_char(&qname, (Py_UCS4)(unsigned char)prefix[index]) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            xb_free(&qname);                                                  /* GCOVR_EXCL_LINE */
            return fail(eng, "out of memory");                                /* GCOVR_EXCL_LINE */
        }
    }
    int colon = xb_add_char(&qname, ':');
    int local = xb_add(&qname, name + local_start, name_len - local_start);
    if (colon < 0 || local < 0) {          /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
        xb_free(&qname);                   /* GCOVR_EXCL_LINE */
        return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t utf8_len = 0;
    char *utf8 = ucs4_to_utf8(qname.data, qname.len, &utf8_len);
    xb_free(&qname);
    if (utf8 == NULL) {                    /* GCOVR_EXCL_BR_LINE: alloc */
        return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
    }
    int rc = th_node_attr_set(eng->out_tree, out_parent, utf8, utf8_len, value, value_len, 1);
    PyMem_Free(utf8);
    if (rc < 0) {                          /* GCOVR_EXCL_BR_LINE: alloc */
        return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
    }
    return 0;
}

static int do_attribute(engine *eng, th_node *instruction, th_node *out_parent) {
    if (out_parent->type != TH_NODE_ELEMENT) {
        return 0;
    }
    Py_ssize_t name_len = 0;
    const Py_UCS4 *name_avt = attr_lookup(eng->sheet_tree, instruction, "name", 4, &name_len);
    if (name_avt == NULL) {
        return fail(eng, "xsl:attribute requires a name attribute");
    }
    Py_UCS4 *name;
    Py_ssize_t resolved_len = 0;
    if (eval_avt(eng, name_avt, name_len, &name, &resolved_len) < 0) {
        return -1;
    }
    Py_ssize_t ns_avt_len = 0;
    const Py_UCS4 *ns_avt = attr_lookup(eng->sheet_tree, instruction, "namespace", 9, &ns_avt_len);
    Py_UCS4 *nsuri = NULL;
    Py_ssize_t nsuri_len = 0;
    if (ns_avt != NULL && eval_avt(eng, ns_avt, ns_avt_len, &nsuri, &nsuri_len) < 0) {
        PyMem_Free(name);
        return -1;
    }
    /* eval_avt yields a NULL buffer for an empty result, so a namespace="" attribute leaves nsuri
       NULL and falls through to a plain name; a non-NULL nsuri always has a positive length. */
    if (!computed_name_valid(nsuri != NULL ? NAME_NAMESPACED_ATTRIBUTE : NAME_ATTRIBUTE, name, resolved_len)) {
        PyMem_Free(name);
        PyMem_Free(nsuri);
        return 0; /* XSLT 1.0 7.1.3 recovery: skip the attribute */
    }
    Py_UCS4 *value;
    Py_ssize_t value_len = 0;
    if (instantiate_string(eng, instruction, &value, &value_len) < 0) {
        PyMem_Free(name);
        PyMem_Free(nsuri);
        return -1;
    }
    if (nsuri != NULL) {
        int rc = do_attribute_ns(eng, out_parent, name, resolved_len, nsuri, nsuri_len, value, value_len);
        PyMem_Free(name);
        PyMem_Free(value);
        PyMem_Free(nsuri);
        return rc;
    }
    Py_ssize_t utf8_len = 0;
    char *utf8 = ucs4_to_utf8(name, resolved_len, &utf8_len);
    PyMem_Free(name);
    if (utf8 == NULL) {                    /* GCOVR_EXCL_BR_LINE: alloc */
        PyMem_Free(value);                 /* GCOVR_EXCL_LINE */
        return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
    }
    int rc = th_node_attr_set(eng->out_tree, out_parent, utf8, utf8_len, value, value_len, 1);
    PyMem_Free(utf8);
    PyMem_Free(value);
    if (rc < 0) {                          /* GCOVR_EXCL_BR_LINE: alloc */
        return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
    }
    return 0;
}

/* xsl:copy: shallow-copy the current node and instantiate the body inside it. */
static int do_copy(engine *eng, th_node *instruction, th_node *out_parent) {
    if (xp_is_attribute(eng->cur_attr)) {
        const th_node_attr *attr = xp_item_attribute(eng->src_tree, (xp_item){eng->cur_node, eng->cur_attr});
        Py_ssize_t name_len = 0;
        const char *name = th_attr_name(eng->src_tree, attr->name_atom, &name_len);
        if (out_parent->type == TH_NODE_ELEMENT) {
            int rc = th_node_attr_set(eng->out_tree, out_parent, name, name_len, attr->value, attr->value_len, 1);
            if (rc < 0) {                          /* GCOVR_EXCL_BR_LINE: alloc */
                return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
            }
        }
        return 0;
    }
    th_node *node = eng->cur_node;
    if (node->type == TH_NODE_ELEMENT) {
        uint16_t atom = atom_for_name(node->text, node->text_len);
        th_node *element = th_tree_make_element(eng->out_tree, node->text, node->text_len, atom, 0);
        if (element == NULL) {                 /* GCOVR_EXCL_BR_LINE: alloc */
            return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
        }
        th_node_append_child(out_parent, element);
        if (number_has_namespaces(eng)) {
            if (copy_namespaces(eng, out_parent, element, node, 0) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                return -1;                                                /* GCOVR_EXCL_LINE */
            }
        }
        int bound = eng->default_bound;
        int namespaced = number_has_namespaces(eng) ? source_namespaced(eng, node) : 0;
        if (namespaced < 0) {                  /* GCOVR_EXCL_BR_LINE: alloc */
            return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
        }
        if (namespaced) {
            bound = 1;
        } else if (!name_is_prefixed(node->text, node->text_len)) {
            if (bound && undeclare_default(eng, element) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                return -1;                                      /* GCOVR_EXCL_LINE */
            }
            bound = 0;
        }
        Py_ssize_t use_len = 0;
        const Py_UCS4 *use = attr_lookup(eng->sheet_tree, instruction, "use-attribute-sets", 18, &use_len);
        if (use != NULL && apply_attribute_sets(eng, use, use_len, element) < 0) {
            return -1;
        }
        return instantiate_under(eng, instruction, element, bound, node);
    }
    if (node->type == TH_NODE_TEXT || node->type == TH_NODE_COMMENT || node->type == TH_NODE_PI) {
        th_node *copy = th_tree_copy_node(eng->out_tree, eng->src_tree, node);
        if (copy == NULL) {                    /* GCOVR_EXCL_BR_LINE: alloc */
            return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
        }
        th_node_append_child(out_parent, copy);
        return 0;
    }
    /* The document/root node copies as nothing; its body instantiates into the output. */
    return instantiate_body(eng, instruction, out_parent);
}

/* The recovery XSLT 1.0 sections 7.3 and 7.4 prescribe for data that would end a comment or processing instruction
   early; it keeps a transform over such source data running. */
static Py_UCS4 *space_after(const Py_UCS4 *data, Py_ssize_t len, Py_UCS4 mark, Py_UCS4 next, int at_end,
                            Py_ssize_t *out_len) {
    Py_UCS4 *out = PyMem_Malloc((size_t)(2 * len + 1) * sizeof(Py_UCS4));
    if (out == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        return NULL;   /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t write = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        out[write++] = data[index];
        if (data[index] == mark && (index + 1 < len ? data[index + 1] == next : at_end)) {
            out[write++] = ' ';
        }
    }
    *out_len = write;
    return out;
}

/* xsl:comment / xsl:processing-instruction. */
static int do_comment(engine *eng, th_node *instruction, th_node *out_parent) {
    Py_UCS4 *raw;
    Py_ssize_t raw_len = 0;
    if (instantiate_string(eng, instruction, &raw, &raw_len) < 0) {
        return -1;
    }
    Py_ssize_t data_len = 0;
    Py_UCS4 *data = space_after(raw, raw_len, '-', '-', 1, &data_len);
    PyMem_Free(raw);
    if (data == NULL) {                    /* GCOVR_EXCL_BR_LINE: alloc */
        return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
    }
    th_node *node = th_tree_make_data_node(eng->out_tree, TH_NODE_COMMENT, data, data_len);
    PyMem_Free(data);
    if (node == NULL) {                    /* GCOVR_EXCL_BR_LINE: alloc */
        return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
    }
    th_node_append_child(out_parent, node);
    return 0;
}

static int do_pi(engine *eng, th_node *instruction, th_node *out_parent) {
    Py_ssize_t name_len = 0;
    const Py_UCS4 *name_avt = attr_lookup(eng->sheet_tree, instruction, "name", 4, &name_len);
    if (name_avt == NULL) {
        return fail(eng, "xsl:processing-instruction requires a name attribute");
    }
    Py_UCS4 *target;
    Py_ssize_t target_len = 0;
    if (eval_avt(eng, name_avt, name_len, &target, &target_len) < 0) {
        return -1;
    }
    if (!computed_name_valid(NAME_PI_TARGET, target, target_len)) {
        PyMem_Free(target);
        return 0; /* XSLT 1.0 7.3 recovery: ignore the instruction */
    }
    Py_UCS4 *raw;
    Py_ssize_t raw_len = 0;
    if (instantiate_string(eng, instruction, &raw, &raw_len) < 0) {
        PyMem_Free(target);
        return -1;
    }
    Py_ssize_t data_len = 0;
    Py_UCS4 *data = space_after(raw, raw_len, '?', '>', 0, &data_len);
    PyMem_Free(raw);
    if (data == NULL) {                    /* GCOVR_EXCL_BR_LINE: alloc */
        PyMem_Free(target);                /* GCOVR_EXCL_LINE */
        return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
    }
    th_node *node = th_tree_make_pi(eng->out_tree, target, target_len, data, data_len);
    PyMem_Free(target);
    PyMem_Free(data);
    if (node == NULL) {                    /* GCOVR_EXCL_BR_LINE: alloc */
        return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
    }
    th_node_append_child(out_parent, node);
    return 0;
}

/* xsl:if / xsl:choose: evaluate a test to a boolean. */
static int eval_test(engine *eng, th_node *instruction, int *out_bool) {
    Py_ssize_t test_len = 0;
    const Py_UCS4 *test = attr_lookup(eng->sheet_tree, instruction, "test", 4, &test_len);
    if (test == NULL) {
        return fail(eng, "xsl:if/xsl:when requires a test attribute");
    }
    char errbuf[256];
    xp_program *prog = compile_expression(eng, test, test_len, errbuf, sizeof(errbuf));
    if (prog == NULL) {          /* GCOVR_EXCL_BR_LINE: compilation rejects malformed tests before a run */
        if (!PyErr_Occurred()) { /* GCOVR_EXCL_LINE: allocation error */
            PyErr_Format(PyExc_ValueError, "xslt: bad test: %s", errbuf); /* GCOVR_EXCL_LINE */
        } /* GCOVR_EXCL_LINE */
        return fail_py(eng); /* GCOVR_EXCL_LINE */
    }
    xp_result value;
    int status =
        eval_program_as(eng, prog, &(xp_item){eng->cur_node, eng->cur_attr}, eng->ctx_pos, eng->ctx_size, &value, 1);
    if (status < 0) {
        return fail_py(eng);
    }
    *out_bool = to_boolean(eng->src_tree, &value);
    xp_result_free(&value);
    return 0;
}

/* ---- sorting -------------------------------------------------------------- */

typedef struct {
    Py_UCS4 *key;
    union {
        Py_ssize_t key_len;
        Py_ssize_t index;
    };
    double number;
} sort_item;

typedef struct {
    xp_program *prog;
    int numeric;
    int descending;
    int direct_attribute;
    uint32_t attribute_atom;
} sort_spec;

typedef struct {
    Py_ssize_t index;
    const sort_item *items;
    const sort_spec *specs;
    int nspecs;
} sort_row;

static int sort_row_order(const void *left_ptr, const void *right_ptr) {
    const sort_row *left = left_ptr;
    const sort_row *right = right_ptr;
    for (int spec = 0; spec < left->nspecs; spec++) {
        const sort_item *left_item = &left->items[left->index * left->nspecs + spec];
        const sort_item *right_item = &right->items[right->index * right->nspecs + spec];
        int cmp;
        if (left->specs[spec].numeric) {
            int left_nan = isnan(left_item->number);
            int right_nan = isnan(right_item->number);
            if (left_nan && right_nan) {
                cmp = 0;
            } else if (left_nan) {
                cmp = -1;
            } else if (right_nan) {
                cmp = 1;
            } else {
                cmp = left_item->number < right_item->number ? -1 : (left_item->number > right_item->number ? 1 : 0);
            }
        } else {
            Py_ssize_t shared = left_item->key_len < right_item->key_len ? left_item->key_len : right_item->key_len;
            cmp = 0;
            for (Py_ssize_t position = 0; position < shared && cmp == 0; position++) {
                cmp = left_item->key[position] < right_item->key[position]
                          ? -1
                          : (left_item->key[position] > right_item->key[position] ? 1 : 0);
            }
            if (cmp == 0) {
                cmp =
                    left_item->key_len < right_item->key_len ? -1 : (left_item->key_len > right_item->key_len ? 1 : 0);
            }
        }
        if (cmp != 0) {
            return left->specs[spec].descending ? -cmp : cmp;
        }
    }
    /* Source order is the final key so qsort preserves XSLT stability. */
    return (left->index > right->index) - (left->index < right->index);
}

static inline int sort_number_compare(const sort_item *left, const sort_item *right, int descending) {
    if (left->number < right->number) {
        return descending ? 1 : -1;
    }
    if (left->number > right->number) {
        return descending ? -1 : 1;
    }
    if (left->number != left->number) {
        if (right->number == right->number) {
            return descending ? 1 : -1;
        }
    } else if (right->number != right->number) {
        return descending ? -1 : 1;
    }
    return (left->index > right->index) - (left->index < right->index);
}

static int sort_row_number_order(const void *left_ptr, const void *right_ptr) {
    return sort_number_compare(left_ptr, right_ptr, 0);
}

static int sort_row_number_descending(const void *left_ptr, const void *right_ptr) {
    return sort_number_compare(left_ptr, right_ptr, 1);
}

/* Compile the xsl:sort children of an instruction into sort specs. Returns the count
   (0 when none), or -1 on error, filling specs (up to `max`). */
static int compile_sorts(engine *eng, th_node *instruction, sort_spec *specs, int max) {
    int count = 0;
    for (th_node *child = instruction->first_child; child != NULL; child = child->next_sibling) {
        if (!is_xsl(eng, child, "sort")) {
            continue;
        }
        if (count >= max) {
            fail(eng, "xslt: too many sort keys");
            return -1;
        }
        Py_ssize_t select_len = 0;
        const Py_UCS4 *select = attr_lookup(eng->sheet_tree, child, "select", 6, &select_len);
        if (select == NULL) {
            select = &XPATH_DOT;
            select_len = 1;
        }
        xp_program *prog = compile_expression(eng, select, select_len, eng->errbuf, sizeof(eng->errbuf));
        if (prog == NULL) {          /* GCOVR_EXCL_BR_LINE: compilation rejects malformed sort selects before a run */
            if (!PyErr_Occurred()) { /* GCOVR_EXCL_LINE: allocation error */
                PyErr_Format(PyExc_ValueError, "xslt: bad sort select: %s", eng->errbuf); /* GCOVR_EXCL_LINE */
            } /* GCOVR_EXCL_LINE */
            fail_py(eng); /* GCOVR_EXCL_LINE */
            return -1;    /* GCOVR_EXCL_LINE */
        }
        Py_ssize_t type_len = 0;
        const Py_UCS4 *type = attr_lookup(eng->sheet_tree, child, "data-type", 9, &type_len);
        Py_ssize_t order_len = 0;
        const Py_UCS4 *order = attr_lookup(eng->sheet_tree, child, "order", 5, &order_len);
        specs[count].prog = prog;
        specs[count].numeric = type != NULL && ucs4_ascii_eq(type, type_len, "number");
        specs[count].descending = order != NULL && ucs4_ascii_eq(order, order_len, "descending");
        specs[count].direct_attribute = xp_single_attribute_atom(prog, eng->src_tree, &specs[count].attribute_atom);
        count++;
    }
    return count;
}

/* Sort a node-set in place by the given sort specs. Returns 0 or -1 on error. */
static int sort_nodeset(engine *eng, xp_nodeset *set, sort_spec *specs, int nspecs) {
    if (set->len < 2) {
        return 0;
    }
    sort_item *items = PyMem_Malloc((size_t)set->len * (size_t)nspecs * sizeof(sort_item));
    if (items == NULL) {                   /* GCOVR_EXCL_BR_LINE: alloc */
        return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
    }
    for (Py_ssize_t index = 0; index < set->len; index++) {
        for (int spec = 0; spec < nspecs; spec++) {
            sort_item *slot = &items[index * nspecs + spec];
            if (specs[spec].direct_attribute && set->items[index].attr == -1) {
                th_node_attr *attributes;
                Py_ssize_t count = th_node_attributes(set->items[index].node, &attributes);
                const Py_UCS4 *text = NULL;
                Py_ssize_t text_len = 0;
                for (Py_ssize_t attribute = 0; attribute < count; attribute++) {
                    if (attributes[attribute].name_atom == specs[spec].attribute_atom) {
                        text = attributes[attribute].value;
                        text_len = text == NULL ? 0 : attributes[attribute].value_len;
                        break;
                    }
                }
                slot->key = ucs4_dup(text, text_len);
                if (slot->key == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                    for (Py_ssize_t done = 0; done < index * nspecs + spec; done++) { /* GCOVR_EXCL_LINE */
                        PyMem_Free(items[done].key);                                  /* GCOVR_EXCL_LINE */
                    } /* GCOVR_EXCL_LINE */
                    PyMem_Free(items);                 /* GCOVR_EXCL_LINE */
                    return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
                }
                slot->key_len = text_len;
                slot->number = parse_number(slot->key, slot->key_len);
                if (nspecs == 1 && specs[spec].numeric) {
                    slot->index = index;
                }
                continue;
            }
            xp_result value;
            int status = eval_program(eng, specs[spec].prog, &set->items[index], index + 1, set->len, &value);
            if (status < 0) {
                /* Freeing already-computed keys only runs when a later key fails after
                   an earlier one succeeded, which a test cannot force deterministically. */
                for (Py_ssize_t done = 0; done < index * nspecs + spec; done++) { /* GCOVR_EXCL_BR_LINE */
                    PyMem_Free(items[done].key);                                  /* GCOVR_EXCL_LINE */
                } /* GCOVR_EXCL_LINE */
                PyMem_Free(items);
                fail_py(eng);
                return -1;
            }
            if (specs[spec].numeric && value.kind == XP_STRING) {
                slot->key = NULL;
                slot->number = parse_number(value.string, value.string_len);
                if (nspecs == 1) {
                    slot->index = index;
                }
                xp_result_free(&value);
                continue;
            }
            /* XSLT reads the key back from its string form. A finite number survives that round trip exactly (the
               shortest decimal parses back to the same double), while "Infinity" and "-Infinity" read as NaN. */
            if (specs[spec].numeric && value.kind == XP_NUMBER) {
                slot->key = NULL;
                slot->number = fabs(value.number) <= DBL_MAX ? value.number : (double)NAN;
                if (nspecs == 1) {
                    slot->index = index;
                }
                xp_result_free(&value);
                continue;
            }
            slot->key = to_string(eng->src_tree, &value, &slot->key_len);
            xp_result_free(&value);
            if (slot->key == NULL) {                                              /* GCOVR_EXCL_BR_LINE: alloc */
                for (Py_ssize_t done = 0; done < index * nspecs + spec; done++) { /* GCOVR_EXCL_LINE */
                    PyMem_Free(items[done].key);                                  /* GCOVR_EXCL_LINE */
                } /* GCOVR_EXCL_LINE */
                PyMem_Free(items);                 /* GCOVR_EXCL_LINE */
                return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
            }
            slot->number = parse_number(slot->key, slot->key_len);
            if (nspecs == 1 && specs[spec].numeric) {
                slot->index = index;
            }
        }
    }
    if (nspecs == 1 && specs[0].numeric) {
        qsort(items, (size_t)set->len, sizeof(sort_item),
              specs[0].descending ? sort_row_number_descending : sort_row_number_order);
        xp_item *sorted = PyMem_Malloc((size_t)set->len * sizeof(xp_item));
        if (sorted == NULL) {                                       /* GCOVR_EXCL_BR_LINE: alloc */
            for (Py_ssize_t index = 0; index < set->len; index++) { /* GCOVR_EXCL_LINE */
                PyMem_Free(items[index].key);                       /* GCOVR_EXCL_LINE */
            } /* GCOVR_EXCL_LINE */
            PyMem_Free(items);                 /* GCOVR_EXCL_LINE */
            return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
        }
        for (Py_ssize_t index = 0; index < set->len; index++) {
            sorted[index] = set->items[items[index].index];
        }
        memcpy(set->items, sorted, (size_t)set->len * sizeof(xp_item));
        PyMem_Free(sorted);
        for (Py_ssize_t index = 0; index < set->len; index++) {
            PyMem_Free(items[index].key);
        }
        PyMem_Free(items);
        return 0;
    }
    sort_row *order = PyMem_Malloc((size_t)set->len * sizeof(sort_row));
    if (order == NULL) {                                                 /* GCOVR_EXCL_BR_LINE: alloc */
        for (Py_ssize_t index = 0; index < set->len * nspecs; index++) { /* GCOVR_EXCL_LINE */
            PyMem_Free(items[index].key);                                /* GCOVR_EXCL_LINE */
        } /* GCOVR_EXCL_LINE */
        PyMem_Free(items);                 /* GCOVR_EXCL_LINE */
        return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
    }
    for (Py_ssize_t index = 0; index < set->len; index++) {
        order[index] = (sort_row){index, items, specs, nspecs};
    }
    qsort(order, (size_t)set->len, sizeof(sort_row), sort_row_order);
    xp_item *sorted = PyMem_Malloc((size_t)set->len * sizeof(xp_item));
    if (sorted == NULL) {                                                /* GCOVR_EXCL_BR_LINE: alloc */
        PyMem_Free(order);                                               /* GCOVR_EXCL_LINE */
        for (Py_ssize_t index = 0; index < set->len * nspecs; index++) { /* GCOVR_EXCL_LINE */
            PyMem_Free(items[index].key);                                /* GCOVR_EXCL_LINE */
        } /* GCOVR_EXCL_LINE */
        PyMem_Free(items);                 /* GCOVR_EXCL_LINE */
        return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
    }
    for (Py_ssize_t index = 0; index < set->len; index++) {
        sorted[index] = set->items[order[index].index];
    }
    memcpy(set->items, sorted, (size_t)set->len * sizeof(xp_item));
    PyMem_Free(sorted);
    PyMem_Free(order);
    for (Py_ssize_t index = 0; index < set->len * nspecs; index++) {
        PyMem_Free(items[index].key);
    }
    PyMem_Free(items);
    return 0;
}

/* ---- xsl:number ----------------------------------------------------------- */

/* Whether a code point is alphanumeric, the class that separates a format token from the
   separators around it (approximated to ASCII letters and digits, which cover the format
   tokens XSLT 1.0 defines: decimal, "a"/"A", "i"/"I"). */
static int alnum_cp(Py_UCS4 cp) {
    return (cp >= '0' && cp <= '9') || (cp >= 'a' && cp <= 'z') || (cp >= 'A' && cp <= 'Z');
}

static int format_number_token(xb *out, long value, Py_UCS4 style) {
    if ((style == 'a' || style == 'A') && value >= 1) {
        char buffer[32];
        int length = 0;
        long remaining = value;
        while (remaining > 0) {
            long digit = (remaining - 1) % 26;
            buffer[length++] = (char)(style + digit);
            remaining = (remaining - 1) / 26;
        }
        for (int index = length - 1; index >= 0; index--) {
            if (xb_add_char(out, (Py_UCS4)(unsigned char)buffer[index]) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                return -1;                                                     /* GCOVR_EXCL_LINE */
            }
        }
        return 0;
    }
    /* Roman numerals are only conventionally defined for 1..4999; larger values would emit an
       unbounded run of "M" (a memory-exhaustion hazard), so fall through to the decimal token,
       as libxslt does. Non-positive values have no roman/alphabetic form either. */
    if ((style == 'i' || style == 'I') && value >= 1 && value <= 4999) {
        static const int values[] = {1000, 900, 500, 400, 100, 90, 50, 40, 10, 9, 5, 4, 1};
        static const char *const lower[] = {"m", "cm", "d", "cd", "c", "xc", "l", "xl", "x", "ix", "v", "iv", "i"};
        static const char *const upper[] = {"M", "CM", "D", "CD", "C", "XC", "L", "XL", "X", "IX", "V", "IV", "I"};
        long remaining = value;
        for (int index = 0; index < 13; index++) {
            while (remaining >= values[index]) {
                const char *token = style == 'i' ? lower[index] : upper[index];
                if (xb_add_ascii(out, token) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                    return -1;                      /* GCOVR_EXCL_LINE */
                }
                remaining -= values[index];
            }
        }
        return 0;
    }
    /* Decimal digits. */
    char buffer[32];
    snprintf(buffer, sizeof(buffer), "%ld", value);
    return xb_add_ascii(out, buffer);
}

/* Emit a run of decimal digits left-padded to min_width and, when a grouping separator and a
   positive grouping size are given (section 7.7.1), split into groups from the right. */
static int emit_digits(xb *out, const char *raw, Py_ssize_t raw_len, Py_ssize_t min_width, const Py_UCS4 *gsep,
                       Py_ssize_t gsep_len, long gsize) {
    Py_ssize_t pad = min_width > raw_len ? min_width - raw_len : 0;
    Py_ssize_t total = raw_len + pad;
    int grouped = gsize >= 1 && gsep_len > 0;
    for (Py_ssize_t index = 0; index < total; index++) {
        if (grouped && index > 0 && (total - index) % gsize == 0) {
            if (xb_add(out, gsep, gsep_len) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                return -1;                         /* GCOVR_EXCL_LINE */
            }
        }
        char digit = index < pad ? '0' : raw[index - pad];
        if (xb_add_char(out, (Py_UCS4)(unsigned char)digit) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;                                             /* GCOVR_EXCL_LINE */
        }
    }
    return 0;
}

static int emit_decimal(xb *out, long value, Py_ssize_t min_width, const Py_UCS4 *gsep, Py_ssize_t gsep_len,
                        long gsize) {
    char raw[32];
    int raw_len = snprintf(raw, sizeof(raw), "%ld", value);
    return emit_digits(out, raw, raw_len, min_width, gsep, gsep_len, gsize);
}

/* Format one number with a format token: a token beginning with a digit is decimal (its leading
   digit run is the minimum width, with grouping), otherwise its trailing letter selects the
   alphabetic/roman style. */
static int format_one_number(xb *out, const Py_UCS4 *token, Py_ssize_t token_len, long value, const Py_UCS4 *gsep,
                             Py_ssize_t gsep_len, long gsize) {
    /* format_multi only passes a non-empty alnum token (or the synthetic "1"), so every code
       point is a letter or digit and comparing against '9' alone classifies a decimal token. */
    if (token[0] <= '9') {
        Py_ssize_t min_width = 0;
        while (min_width < token_len && token[min_width] <= '9') {
            min_width++;
        }
        return emit_decimal(out, value, min_width, gsep, gsep_len, gsize);
    }
    return format_number_token(out, value, token[token_len - 1]);
}

/* Format a list of numbers under a format string (section 7.7.1): a leading separator (prefix),
   then alternating format tokens and separators, then a trailing separator (suffix). A number
   past the last token reuses the last token and the separator before it; an empty format uses a
   single decimal token. */
static int format_multi(xb *out, const Py_UCS4 *format, Py_ssize_t format_len, const long *values, Py_ssize_t nvalues,
                        const Py_UCS4 *gsep, Py_ssize_t gsep_len, long gsize) {
    Py_ssize_t tok_start[32];
    Py_ssize_t tok_len[32];
    Py_ssize_t sep_start[33];
    Py_ssize_t sep_len[33];
    Py_ssize_t pos = 0;
    Py_ssize_t run = pos;
    while (pos < format_len && !alnum_cp(format[pos])) {
        pos++;
    }
    sep_start[0] = run;
    sep_len[0] = pos - run;
    Py_ssize_t ntok = 0;
    while (pos < format_len) {
        /* A format with more tokens than the fixed arrays hold cannot arise from a real picture. */
        if (ntok == 32) { /* GCOVR_EXCL_BR_LINE: overflow guard for the token arrays */
            break;        /* GCOVR_EXCL_LINE */
        }
        Py_ssize_t token = pos;
        while (pos < format_len && alnum_cp(format[pos])) {
            pos++;
        }
        tok_start[ntok] = token;
        tok_len[ntok] = pos - token;
        run = pos;
        while (pos < format_len && !alnum_cp(format[pos])) {
            pos++;
        }
        sep_start[ntok + 1] = run;
        sep_len[ntok + 1] = pos - run;
        ntok++;
    }
    static const Py_UCS4 one = '1';
    if (sep_len[0] != 0 && xb_add(out, format + sep_start[0], sep_len[0]) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;                                                               /* GCOVR_EXCL_LINE */
    }
    for (Py_ssize_t index = 0; index < nvalues; index++) {
        Py_ssize_t pick = index < ntok ? index : ntok - 1;
        if (index > 0) {
            int sep = ntok > 1 ? xb_add(out, format + sep_start[pick], sep_len[pick]) : xb_add_char(out, '.');
            if (sep < 0) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
                return -1; /* GCOVR_EXCL_LINE */
            }
        }
        const Py_UCS4 *tok = ntok > 0 ? format + tok_start[pick] : &one;
        Py_ssize_t tok_length = ntok > 0 ? tok_len[pick] : 1;
        int emitted = format_one_number(out, tok, tok_length, values[index], gsep, gsep_len, gsize);
        if (emitted < 0) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
            return -1;     /* GCOVR_EXCL_LINE */
        }
    }
    /* The trailing separator is the one after the last token; an all-punctuation format (no
       token) is entirely the prefix and has no suffix. */
    if (ntok > 0 && xb_add(out, format + sep_start[ntok], sep_len[ntok]) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;                                                              /* GCOVR_EXCL_LINE */
    }
    return 0;
}

/* Parse the grouping-size attribute as a base-10 integer; a value that is not a well-formed
   integer (or is non-positive) disables grouping, so 0 is returned. */
static long parse_grouping_size(const Py_UCS4 *text, Py_ssize_t len) {
    Py_ssize_t index = 0;
    int negative = 0;
    if (index < len && (text[index] == '-' || text[index] == '+')) {
        negative = text[index] == '-';
        index++;
    }
    if (index == len) {
        return 0;
    }
    long value = 0;
    for (; index < len; index++) {
        if (text[index] < '0' || text[index] > '9') {
            return 0;
        }
        value = value * 10 + (text[index] - '0');
    }
    return negative ? -value : value;
}

/* Raise the error of a count or from pattern evaluation that returned status. Returns -1. */
static int number_pattern_error(engine *eng, int status) {
    if (status == -1 && !PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        PyErr_NoMemory();                    /* GCOVR_EXCL_LINE */
    } /* GCOVR_EXCL_LINE */
    if (!PyErr_Occurred() || !PyErr_ExceptionMatches(PyExc_MemoryError)) { /* GCOVR_EXCL_BR_LINE */
        PyErr_Format(PyExc_ValueError, "xslt: xsl:number pattern error");
    }
    return fail_py(eng);
}

/* Add the nodes one alternative of a count or from pattern matches to set. Returns 0, or -1 on error. */
static int match_alternative(engine *eng, xslt_pattern_scope *scope, const xp_program *prog, match_set *set) {
    xp_result matched;
    const char *feature = NULL;
    int status = th_tree_is_xml(eng->src_tree)
                     ? xp_eval_pattern_at(prog, eng->src_tree, eng->src_root, xslt_extension, eng,
                                          pattern_callback_needed(eng, prog) ? pattern_name_test : NULL, scope,
                                          &matched, &feature)
                     : xp_eval_at(prog, eng->src_tree, &(xp_item){eng->src_root, -1}, eng->src_root, 1, 1, NULL, NULL,
                                  xslt_extension, eng, &eng->src_langs, NULL, &matched, &feature);
    if (status < 0) {
        return number_pattern_error(eng, status);
    }
    if (match_set_reserve(set, (size_t)matched.nodes.len) < 0) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
        xp_result_free(&matched);                                /* GCOVR_EXCL_LINE */
        PyErr_NoMemory();                                        /* GCOVR_EXCL_LINE */
        return fail_py(eng);                                     /* GCOVR_EXCL_LINE */
    }
    for (Py_ssize_t slot = 0; slot < matched.nodes.len; slot++) {
        xp_item item = matched.nodes.items[slot];
        int added = match_set_add(set, item.node, item.attr);
        if (added < 0) {              /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
            xp_result_free(&matched); /* GCOVR_EXCL_LINE */
            PyErr_NoMemory();         /* GCOVR_EXCL_LINE */
            return fail_py(eng);      /* GCOVR_EXCL_LINE */
        }
    }
    xp_result_free(&matched);
    return 0;
}

/* Set out up for one xsl:number call. A pattern that reads nothing but the source and the instruction's namespaces is
   evaluated over it once and kept on the site; the others are left to number_probe_has. So is a static pattern on the
   first call of a site that sets probe_first: there a from pattern stops the walk back at its nearest match, so one
   call passes only the nodes since, where indexing evaluates the pattern over the whole source, which pays off only
   once the site runs again. Returns 0, or -1 on error. */
static int number_pattern_open(engine *eng, th_node *instruction, const Py_UCS4 *pattern, Py_ssize_t len,
                               int probe_first, xslt_number_match *cache, xslt_number_pattern *out) {
    *out = (xslt_number_pattern){.set = &cache->matched, .scope = {.eng = eng, .instruction = instruction}};
    if (cache->source == pattern) {
        return 0;
    }
    Py_ssize_t starts[64];
    Py_ssize_t lengths[64];
    const xp_program *programs[64];
    int alternatives = split_union(pattern, len, starts, lengths, 64);
    int dynamic = 0;
    for (int index = 0; index < alternatives; index++) {
        programs[index] = compile_pattern(eng, pattern + starts[index], lengths[index]);
        if (programs[index] == NULL) { /* GCOVR_EXCL_BR_LINE: compilation validates stylesheet patterns */
            return -1;                 /* GCOVR_EXCL_LINE */
        }
        dynamic |= xp_pattern_reads_call(programs[index]);
    }
    if (dynamic || (probe_first && cache->probed != pattern)) {
        if (!dynamic) {
            cache->probed = pattern;
        }
        out->set = NULL;
        out->alternatives = PyMem_Calloc((size_t)alternatives, sizeof(*out->alternatives));
        if (out->alternatives == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            PyErr_NoMemory();            /* GCOVR_EXCL_LINE */
            return fail_py(eng);         /* GCOVR_EXCL_LINE */
        }
        out->count = alternatives;
        for (int index = 0; index < alternatives; index++) {
            xslt_probe_alternative *alternative = &out->alternatives[index];
            if (!xp_pattern_walks_back(programs[index])) {
                if (match_alternative(eng, &out->scope, programs[index], &alternative->nodes) < 0) {
                    return -1;
                }
                continue;
            }
            const char *feature = NULL;
            int status = xp_pattern_walk_start(programs[index], eng->src_tree, eng->src_root,
                                               th_tree_is_xml(eng->src_tree) ? pattern_name_test : NULL, &out->scope,
                                               &alternative->walk, &feature);
            if (status < 0) {
                return number_pattern_error(eng, status);
            }
        }
        return 0;
    }
    match_set_free(&cache->matched);
    cache->source = NULL;
    for (int index = 0; index < alternatives; index++) {
        if (match_alternative(eng, &out->scope, programs[index], &cache->matched) < 0) {
            return -1;
        }
    }
    cache->source = pattern;
    return 0;
}

static void number_pattern_close(xslt_number_pattern *pattern) {
    for (int index = 0; index < pattern->count; index++) {
        xp_pattern_walk_free(pattern->alternatives[index].walk);
        match_set_free(&pattern->alternatives[index].nodes);
    }
    PyMem_Free(pattern->alternatives);
}

/* Whether a pattern that depends on the call matches the item. An error is raised once and recorded in failed, which
   count_numbers checks when the count is done; until then every item answers no. */
static TH_NOINLINE int number_probe_has(engine *eng, xslt_number_pattern *pattern, th_node *node, Py_ssize_t attr) {
    for (int index = 0; index < pattern->count && !pattern->failed; index++) {
        xslt_probe_alternative *alternative = &pattern->alternatives[index];
        const char *feature = NULL;
        int status = alternative->walk == NULL
                         ? match_set_has(&alternative->nodes, node, attr)
                         : xp_pattern_match(alternative->walk, (xp_item){node, attr}, xslt_extension, eng, &feature);
        if (status < 0) {
            pattern->failed = 1;
            number_pattern_error(eng, status);
        }
        if (status != 0) {
            return status > 0;
        }
    }
    return 0;
}

static int number_pattern_has(engine *eng, xslt_number_pattern *pattern, th_node *node, Py_ssize_t attr) {
    return pattern->set != NULL ? match_set_has(pattern->set, node, attr) : number_probe_has(eng, pattern, node, attr);
}

static xslt_number_name number_local_name(const engine *eng, const th_node *node) {
    xslt_number_name result = {.type = node->type};
    if (node->type == TH_NODE_PI) {
        result.name = node->text;
        result.name_len = node->attr_count;
    } else if (node->type == TH_NODE_ELEMENT) {
        result.name = node->text;
        result.name_len = node->text_len;
        if (th_tree_is_xml(eng->src_tree)) {
            Py_ssize_t prefix_len;
            result.name = qname_local(node, &result.name_len, &prefix_len);
        }
    }
    return result;
}

static int number_has_namespaces(engine *eng) {
    if (eng->number_namespaces < 0) {
        uint32_t count = th_tree_attr_generation(eng->src_tree);
        /* xmlns is no static attribute name, so a tree without dynamic names declares no default namespace */
        eng->default_namespace_atom = count == 0 ? UINT32_MAX : th_attr_lookup(eng->src_tree, "xmlns", 5);
        eng->number_namespaces = eng->default_namespace_atom != UINT32_MAX;
        for (uint32_t index = 0; !eng->number_namespaces && index < count; index++) {
            Py_ssize_t length;
            const char *name = th_attr_name(eng->src_tree, TH_ATTR__DYNAMIC_BASE + index, &length);
            eng->number_namespaces = length > 6 && memcmp(name, "xmlns:", 6) == 0;
        }
    }
    return eng->number_namespaces;
}

static int pattern_callback_needed(engine *eng, const xp_program *prog) {
    return number_has_namespaces(eng) || !xp_pattern_is_static(prog);
}

/* Size the scratch that spells an "xmlns:prefix" declaration name for an atom lookup. */
static int number_prefix_reserve(xslt_number_index *index, size_t needed) {
    if (needed > index->prefix_capacity) {
        size_t capacity;
        size_t bytes;
        /* GCOVR_EXCL_BR_START: allocation failure */
        if (!th_grow_cap(needed, index->prefix_capacity, 64, 1, &capacity, &bytes)) {
            return -1; /* GCOVR_EXCL_LINE */
        }
        char *grown = PyMem_Realloc(index->prefix, bytes);
        if (grown == NULL) {
            return -1; /* GCOVR_EXCL_LINE */
        }
        /* GCOVR_EXCL_BR_STOP */
        index->prefix = grown;
        index->prefix_capacity = capacity;
        memcpy(index->prefix, "xmlns:", 6);
    }
    return 0;
}

static int number_namespace_atom(engine *eng, const th_node *node, const xslt_number_name *name, uint32_t *atom) {
    Py_ssize_t prefix_len = name->name == node->text ? 0 : name->name - node->text - 1;
    if (prefix_len == 0) {
        *atom = eng->default_namespace_atom;
        return 0;
    }
    xslt_number_index *index = &eng->number_index;
    if (number_prefix_reserve(index, (size_t)prefix_len * 4 + 7) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                                                      /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t length = write_utf8(node->text, prefix_len, index->prefix + 6);
    *atom = th_attr_lookup(eng->src_tree, index->prefix, length + 6);
    return 0;
}

/* Salted FNV-1a finished with the splitmix64 mix, as the XML parser hashes the names of its own tables
   (tokenizer/xml.c): the source document picks the names and namespaces hashed here, and unsalted ones could be
   chosen to share a slot. Python's randomized bytes hash is the salt: unpredictable per process, yet fixed under
   PYTHONHASHSEED. */
static int number_hash_seed(uint64_t *seed) {
    PyObject *salt = PyBytes_FromStringAndSize("turbohtml:xslt-number", 21);
    if (salt == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;      /* GCOVR_EXCL_LINE */
    }
    *seed = (uint64_t)PyObject_Hash(salt) ^ UINT64_C(14695981039346656037);
    Py_DECREF(salt);
    return 0;
}

static uint64_t number_hash_text(uint64_t hash, const Py_UCS4 *text, Py_ssize_t len) {
    for (Py_ssize_t index = 0; index < len; index++) {
        hash = (hash ^ text[index]) * UINT64_C(1099511628211);
    }
    return hash;
}

static uint32_t number_hash_finish(uint64_t hash) {
    hash = (hash ^ (hash >> 30)) * UINT64_C(0xBF58476D1CE4E5B9);
    hash = (hash ^ (hash >> 27)) * UINT64_C(0x94D049BB133111EB);
    return (uint32_t)(hash ^ (hash >> 31));
}

static xslt_number_uri *number_uri_slot(xslt_number_uri *uris, size_t capacity, uint32_t hash, const Py_UCS4 *text,
                                        Py_ssize_t len) {
    size_t slot = th_hash_slot(hash, capacity);
    while (uris[slot].id != 0 && !str_eq(uris[slot].text, uris[slot].len, text, len)) {
        slot = (slot + 1) & (capacity - 1);
    }
    return &uris[slot];
}

/* Give a namespace name the integer that stands for it in this index: equal texts get equal integers, so a walk
   over the document compares and hashes an element's namespace without reading its text again. The text is read
   here, once per declaration. Returns -1 on allocation failure. */
static int number_uri_id(xslt_number_index *index, const Py_UCS4 *text, Py_ssize_t len, uint32_t *id) {
    if (len == 0) {
        *id = 0;
        return 0;
    }
    if (index->uri_count == index->uri_capacity / 2) {
        if (index->uris == NULL && number_hash_seed(&index->uri_seed) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;                                                       /* GCOVR_EXCL_LINE */
        }
        size_t capacity;
        size_t bytes;
        /* GCOVR_EXCL_BR_START: allocation failure */
        if (!th_grow_cap(index->uri_capacity + 1, index->uri_capacity, 16, sizeof(*index->uris), &capacity, &bytes)) {
            return -1; /* GCOVR_EXCL_LINE */
        }
        xslt_number_uri *uris = PyMem_Calloc(1, bytes);
        if (uris == NULL) {
            return -1; /* GCOVR_EXCL_LINE */
        }
        /* GCOVR_EXCL_BR_STOP */
        for (size_t slot = 0; slot < index->uri_capacity; slot++) {
            const xslt_number_uri *uri = &index->uris[slot];
            if (uri->id != 0) {
                *number_uri_slot(uris, capacity, uri->hash, uri->text, uri->len) = *uri;
            }
        }
        PyMem_Free(index->uris);
        index->uris = uris;
        index->uri_capacity = capacity;
    }
    const uint32_t hash = number_hash_finish(number_hash_text(index->uri_seed, text, len));
    xslt_number_uri *uri = number_uri_slot(index->uris, index->uri_capacity, hash, text, len);
    if (uri->id == 0) {
        *uri = (xslt_number_uri){text, len, hash, (uint32_t)++index->uri_count};
    }
    *id = uri->id;
    return 0;
}

static int number_namespace_enter(xslt_number_index *index, th_tree *tree, const th_node *node) {
    for (Py_ssize_t offset = 0; offset < node->attr_count; offset++) {
        const th_node_attr *attr = &node->attrs[offset];
        Py_ssize_t length;
        const char *name = th_attr_name(tree, attr->name_atom, &length);
        if (length < 5 || memcmp(name, "xmlns", 5) != 0 || (length != 5 && name[5] != ':')) {
            continue;
        }
        if (attr->name_atom >= index->binding_capacity) {
            size_t capacity;
            size_t bytes;
            /* GCOVR_EXCL_BR_START: allocation failure */
            if (!th_grow_cap((size_t)attr->name_atom + 1, index->binding_capacity, 16, sizeof(*index->bindings),
                             &capacity, &bytes)) {
                return -1; /* GCOVR_EXCL_LINE */
            }
            xslt_number_binding *grown = PyMem_Realloc(index->bindings, bytes);
            if (grown == NULL) {
                return -1; /* GCOVR_EXCL_LINE */
            }
            /* GCOVR_EXCL_BR_STOP */
            memset(grown + index->binding_capacity, 0, (capacity - index->binding_capacity) * sizeof(*grown));
            index->bindings = grown;
            index->binding_capacity = capacity;
        }
        if (index->scope_count == index->scope_capacity) {
            size_t capacity;
            size_t bytes;
            /* GCOVR_EXCL_BR_START: allocation failure */
            if (!th_grow_cap(index->scope_count + 1, index->scope_capacity, 16, sizeof(*index->scope), &capacity,
                             &bytes)) {
                return -1; /* GCOVR_EXCL_LINE */
            }
            xslt_number_scope *grown = PyMem_Realloc(index->scope, bytes);
            if (grown == NULL) {
                return -1; /* GCOVR_EXCL_LINE */
            }
            /* GCOVR_EXCL_BR_STOP */
            index->scope = grown;
            index->scope_capacity = capacity;
        }
        uint32_t uri_id;
        if (number_uri_id(index, attr->value, attr->value_len, &uri_id) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;                                                         /* GCOVR_EXCL_LINE */
        }
        index->scope[index->scope_count++] =
            (xslt_number_scope){node, attr->name_atom, index->bindings[attr->name_atom]};
        index->bindings[attr->name_atom] = (xslt_number_binding){attr, uri_id};
    }
    return 0;
}

static const Py_UCS4 number_xml_uri[] = {'h', 't', 't', 'p', ':', '/', '/', 'w', 'w', 'w', '.', 'w',
                                         '3', '.', 'o', 'r', 'g', '/', 'X', 'M', 'L', '/', '1', '9',
                                         '9', '8', '/', 'n', 'a', 'm', 'e', 's', 'p', 'a', 'c', 'e'};

static void number_namespace_leave(xslt_number_index *index, const th_node *node) {
    while (index->scope_count != 0 && index->scope[index->scope_count - 1].node == node) {
        const xslt_number_scope *scope = &index->scope[--index->scope_count];
        index->bindings[scope->atom] = scope->previous;
    }
}

static int number_index_add(engine *eng, const th_node *node) {
    xslt_number_index *index = &eng->number_index;
    if (number_namespace_enter(index, eng->src_tree, node) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                                                /* GCOVR_EXCL_LINE */
    }
    xslt_number_name name = number_local_name(eng, node);
    if (node->ns != TH_NS_HTML) {
        name.ns = node->ns;
    } else if (name.name - node->text == 4 && ucs4_ascii_eq(node->text, 3, "xml")) {
        name.uri = number_xml_uri;
        name.uri_len = sizeof(number_xml_uri) / sizeof(number_xml_uri[0]);
        /* interned like a declared one, so a prefix a document binds to this name tallies with xml: */
        if (number_uri_id(index, name.uri, name.uri_len, &name.uri_id) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;                                                        /* GCOVR_EXCL_LINE */
        }
    } else {
        uint32_t atom;
        if (number_namespace_atom(eng, node, &name, &atom) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;                                            /* GCOVR_EXCL_LINE */
        }
        if (atom < index->binding_capacity && index->bindings[atom].attr != NULL) {
            name.uri = index->bindings[atom].attr->value;
            name.uri_len = index->bindings[atom].attr->value_len;
            name.uri_id = index->bindings[atom].uri_id;
            if (ucs4_ascii_eq(name.uri, name.uri_len, XP_SVG_NS_URI)) {
                name.ns = TH_NS_SVG;
                name.uri_len = 0;
                name.uri_id = 0;
            } else if (ucs4_ascii_eq(name.uri, name.uri_len, XP_MATHML_NS_URI)) {
                name.ns = TH_NS_MATHML;
                name.uri_len = 0;
                name.uri_id = 0;
            }
        }
    }
    if (index->positions.count == index->name_capacity) {
        size_t capacity;
        size_t bytes;
        /* GCOVR_EXCL_BR_START: allocation failure */
        if (!th_grow_cap(index->positions.count + 1, index->name_capacity, 16, sizeof(*index->names), &capacity,
                         &bytes)) {
            return -1; /* GCOVR_EXCL_LINE */
        }
        xslt_number_name *grown = PyMem_Realloc(index->names, bytes);
        if (grown == NULL) {
            return -1; /* GCOVR_EXCL_LINE */
        }
        /* GCOVR_EXCL_BR_STOP */
        index->names = grown;
        index->name_capacity = capacity;
    }
    index->names[index->positions.count] = name;
    return th_node_map_insert(&index->positions, node, (Py_ssize_t)index->positions.count + 1);
}

static int number_index_extend(engine *eng, const th_node *target) {
    xslt_number_index *index = &eng->number_index;
    if (index->positions.count == 0) {
        index->next = eng->src_root;
        /* A node remembered while there was no index is not in it, and number_name expects every element it is asked
           about to be: forget the node, so the next default count extends the index up to it. */
        eng->current_number_node = NULL;
    }
    if (th_node_map_find(&index->positions, target) != 0) {
        return 0;
    }
    for (;;) {
        th_node *node = index->next;
        if (node->type == TH_NODE_ELEMENT) {
            if (number_index_add(eng, node) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                return -1;                         /* GCOVR_EXCL_LINE */
            }
        }
        int reached = node == target;
        if (node->first_child != NULL) {
            index->next = node->first_child;
        } else {
            for (;;) {
                number_namespace_leave(index, node);
                if (node->next_sibling != NULL || node->parent == NULL) {
                    index->next = node->next_sibling;
                    break;
                }
                node = node->parent;
            }
        }
        if (reached) {
            return 0;
        }
    }
}

static xslt_number_name number_name(const engine *eng, const th_node *node) {
    if (node->type == TH_NODE_ELEMENT) {
        Py_ssize_t position = th_node_map_find(&eng->number_index.positions, node);
        return eng->number_index.names[position - 1];
    }
    return number_local_name(eng, node);
}

/* The expanded name a default count knows node by: node counts for the current node when this equals
   current_number_name. Without a namespace index no prefix is bound but the reserved xml one. */
static xslt_number_name number_default_name(const engine *eng, const th_node *node) {
    if (node->type != TH_NODE_ELEMENT || eng->number_index.positions.count != 0) {
        return number_name(eng, node);
    }
    xslt_number_name name = number_local_name(eng, node);
    name.ns = node->ns;
    if (node->ns == TH_NS_HTML && name.name - node->text == 4 && ucs4_ascii_eq(node->text, 3, "xml")) {
        name.uri = number_xml_uri;
        name.uri_len = sizeof(number_xml_uri) / sizeof(number_xml_uri[0]);
        name.uri_id = 1; /* the one namespace a document without declarations can name */
    }
    return name;
}

/* Steps a climb for a prefix may take before the memo answers instead. A step is an ancestor or an attribute compared,
   so an ordinary document is resolved by the climb alone and pays nothing for the memo. */
#define XSLT_CLIMB_STEPS 128

/* Climbs from node for the declaration of prefix. Returns 1 with the declaring attribute in declared, 0 when no node in
   scope declares it, and -1 once the climb has taken XSLT_CLIMB_STEPS steps without either. */
static int declaration_climb(th_tree *tree, th_node *node, const char *prefix, Py_ssize_t prefix_len,
                             const th_node_attr **declared) {
    Py_ssize_t steps = 0;
    for (; node != NULL; node = node->parent) {
        TH_WORK_STEP();
        steps += node->attr_count + 1;
        if (steps > XSLT_CLIMB_STEPS) {
            return -1;
        }
        for (Py_ssize_t index = 0; index < node->attr_count; index++) {
            Py_ssize_t name_len;
            const char *name = th_attr_name(tree, node->attrs[index].name_atom, &name_len);
            if (name_len == prefix_len + 6 && memcmp(name, "xmlns:", 6) == 0 &&
                memcmp(name + 6, prefix, (size_t)prefix_len) == 0) {
                *declared = &node->attrs[index];
                return 1;
            }
        }
    }
    *declared = NULL;
    return 0;
}

static int declarations_atom(xslt_declarations *decls, th_tree *tree, const char *prefix, Py_ssize_t prefix_len,
                             uint32_t *atom) {
    size_t needed = (size_t)prefix_len + 6;
    if (needed > decls->name_capacity) {
        size_t capacity;
        size_t bytes;
        /* GCOVR_EXCL_BR_START: allocation failure */
        if (!th_grow_cap(needed, decls->name_capacity, 64, 1, &capacity, &bytes)) {
            return -1; /* GCOVR_EXCL_LINE */
        }
        char *grown = PyMem_Realloc(decls->name, bytes);
        if (grown == NULL) {
            return -1; /* GCOVR_EXCL_LINE */
        }
        /* GCOVR_EXCL_BR_STOP */
        decls->name = grown;
        decls->name_capacity = capacity;
    }
    memcpy(decls->name, "xmlns:", 6);
    memcpy(decls->name + 6, prefix, (size_t)prefix_len);
    *atom = th_attr_lookup(tree, decls->name, (Py_ssize_t)needed);
    return 0;
}

static const th_node_attr *own_declaration(const th_node *node, uint32_t atom) {
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        if (node->attrs[index].name_atom == atom) {
            return &node->attrs[index];
        }
    }
    return NULL;
}

/* Resolves the declaration of a prefix in scope at node once the climb has given up. The memo records the answer at
   every node the climb passes, so a climb stops at the first node already answered and each node of a tree is passed
   once per prefix, however many names test it. */
static int declarations_resolve(xslt_declarations *decls, th_tree *tree, th_node *node, const char *prefix,
                                Py_ssize_t prefix_len, const th_node_attr **declared) {
    uint32_t atom;
    if (declarations_atom(decls, tree, prefix, prefix_len, &atom) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                                                       /* GCOVR_EXCL_LINE */
    }
    if (atom == UINT32_MAX) {
        *declared = NULL;
        return 0;
    }
    if (atom >= decls->capacity) {
        size_t capacity;
        size_t bytes;
        /* GCOVR_EXCL_BR_START: allocation failure */
        if (!th_grow_cap(atom + 1, decls->capacity, 16, sizeof(*decls->prefixes), &capacity, &bytes)) {
            return -1; /* GCOVR_EXCL_LINE */
        }
        xslt_prefix_memo *grown = PyMem_Realloc(decls->prefixes, bytes);
        if (grown == NULL) {
            return -1; /* GCOVR_EXCL_LINE */
        }
        /* GCOVR_EXCL_BR_STOP */
        memset(grown + decls->capacity, 0, (capacity - decls->capacity) * sizeof(*grown));
        decls->prefixes = grown;
        decls->capacity = capacity;
    }
    xslt_prefix_memo *memo = &decls->prefixes[atom];
    const th_node_attr *found = NULL;
    Py_ssize_t cached = 0;
    th_node *stop = node;
    for (; stop != NULL; stop = stop->parent) {
        TH_WORK_STEP();
        cached = th_node_map_find(&memo->resolved, stop);
        if (cached != 0) {
            break;
        }
        found = own_declaration(stop, atom);
        if (found != NULL) {
            break;
        }
    }
    Py_ssize_t value = cached;
    if (cached != 0) {
        found = memo->declared[cached - 1];
    } else {
        size_t capacity;
        size_t bytes;
        /* GCOVR_EXCL_BR_START: allocation failure */
        if (memo->declared_count == memo->declared_capacity) {
            if (!th_grow_cap(memo->declared_count + 1, memo->declared_capacity, 16, sizeof(*memo->declared), &capacity,
                             &bytes)) {
                return -1; /* GCOVR_EXCL_LINE */
            }
            const th_node_attr **grown = PyMem_Realloc(memo->declared, bytes);
            if (grown == NULL) {
                return -1; /* GCOVR_EXCL_LINE */
            }
            memo->declared = grown;
            memo->declared_capacity = capacity;
        }
        /* GCOVR_EXCL_BR_STOP */
        memo->declared[memo->declared_count++] = found;
        value = (Py_ssize_t)memo->declared_count;
    }
    for (th_node *walk = node; walk != stop; walk = walk->parent) {
        if (th_node_map_insert(&memo->resolved, walk, value) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;                                              /* GCOVR_EXCL_LINE */
        }
    }
    if (cached == 0 && stop != NULL && th_node_map_insert(&memo->resolved, stop, value) < 0) { /* GCOVR_EXCL_BR_LINE */
        return -1;                                                                             /* GCOVR_EXCL_LINE */
    }
    *declared = found;
    return 0;
}

/* The declaration of prefix in scope at node: a bounded climb, and the memo for a node the climb cannot finish. Returns
   -1 on allocation failure. */
static int pattern_declaration(xslt_declarations *decls, th_tree *tree, th_node *node, const char *prefix,
                               Py_ssize_t prefix_len, const th_node_attr **declared) {
    if (declaration_climb(tree, node, prefix, prefix_len, declared) >= 0) {
        return 0;
    }
    return declarations_resolve(decls, tree, node, prefix, prefix_len, declared);
}

static void declarations_free(xslt_declarations *decls) {
    for (size_t index = 0; index < decls->capacity; index++) {
        PyMem_Free(decls->prefixes[index].resolved.entries);
        PyMem_Free(decls->prefixes[index].declared);
    }
    PyMem_Free(decls->prefixes);
    PyMem_Free(decls->name);
}

static int pattern_local_equals_utf8(const Py_UCS4 *local, Py_ssize_t local_len, const char *source,
                                     Py_ssize_t source_len) {
    for (Py_ssize_t index = 0; index < local_len; index++) {
        if (local[index] < 0x80) {
            if (source_len == 0 || *source != (char)local[index]) {
                return 0;
            }
            source++;
            source_len--;
            continue;
        }
        char encoded[5];
        Py_ssize_t bytes = write_utf8(local + index, 1, encoded);
        if (source_len < bytes || memcmp(source, encoded, (size_t)bytes) != 0) {
            return 0;
        }
        source += bytes;
        source_len -= bytes;
    }
    return source_len == 0;
}

static int pattern_name_test(void *context, th_node *node, Py_ssize_t attr, const Py_UCS4 *name, Py_ssize_t name_len) {
    xslt_pattern_scope *scope = context;
    engine *eng = scope->eng;
    Py_ssize_t prefix_len = 0;
    while (prefix_len < name_len && name[prefix_len] != ':') {
        prefix_len++;
    }
    int prefixed = prefix_len < name_len;
    const Py_UCS4 *local = prefixed ? name + prefix_len + 1 : name;
    Py_ssize_t local_len = name_len - (prefixed ? prefix_len + 1 : 0);
    const Py_UCS4 *wanted_uri = NULL;
    Py_ssize_t wanted_uri_len = 0;
    if (prefixed && scope->last_name == name) {
        wanted_uri = scope->last_uri;
        wanted_uri_len = scope->last_uri_len;
    } else if (prefixed) {
        if (ucs4_ascii_eq(name, prefix_len, XP_XML_NS_PREFIX)) {
            static const Py_UCS4 xml_uri[] = {'h', 't', 't', 'p', ':', '/', '/', 'w', 'w', 'w', '.', 'w',
                                              '3', '.', 'o', 'r', 'g', '/', 'X', 'M', 'L', '/', '1', '9',
                                              '9', '8', '/', 'n', 'a', 'm', 'e', 's', 'p', 'a', 'c', 'e'};
            wanted_uri = xml_uri;
            wanted_uri_len = sizeof(xml_uri) / sizeof(xml_uri[0]);
        } else {
            Py_ssize_t prefix_bytes;
            char *prefix = ucs4_to_utf8(name, prefix_len, &prefix_bytes);
            if (prefix == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
                return -1;        /* GCOVR_EXCL_LINE */
            }
            const th_node_attr *declared;
            int resolved = pattern_declaration(&eng->sheet_declarations, eng->sheet_tree, scope->instruction, prefix,
                                               prefix_bytes, &declared);
            PyMem_Free(prefix);
            if (resolved < 0) {   /* GCOVR_EXCL_BR_LINE: allocation failure */
                PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
                return -1;        /* GCOVR_EXCL_LINE */
            }
            if (declared == NULL || declared->value == NULL) {
                PyErr_SetString(PyExc_ValueError, "xslt: undefined namespace prefix");
                return -1;
            }
            wanted_uri = declared->value;
            wanted_uri_len = declared->value_len;
        }
        scope->last_name = name;
        scope->last_uri = wanted_uri;
        scope->last_uri_len = wanted_uri_len;
    }
    if (node == NULL) {
        return 1;
    }
    if (attr >= 0) {
        Py_ssize_t source_len;
        const char *source = th_attr_name(eng->src_tree, node->attrs[attr].name_atom, &source_len);
        const char *colon = memchr(source, ':', (size_t)source_len);
        Py_ssize_t source_prefix_len = colon == NULL ? 0 : colon - source;
        const char *source_local = colon == NULL ? source : colon + 1;
        if (!pattern_local_equals_utf8(local, local_len, source_local, source_len - (source_local - source))) {
            return 0;
        }
        if (source_prefix_len == 0) {
            return wanted_uri_len == 0;
        }
        if (source_prefix_len == 3 && memcmp(source, "xml", 3) == 0) {
            return ucs4_ascii_eq(wanted_uri, wanted_uri_len, XP_XML_NS_URI);
        }
        const th_node_attr *declared;
        int resolved =
            pattern_declaration(&eng->src_declarations, eng->src_tree, node, source, source_prefix_len, &declared);
        if (resolved < 0) {   /* GCOVR_EXCL_BR_LINE: allocation failure */
            PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
            return -1;        /* GCOVR_EXCL_LINE */
        }
        return declared != NULL && declared->value != NULL && declared->value_len == wanted_uri_len &&
               (wanted_uri_len == 0 ||
                memcmp(declared->value, wanted_uri, (size_t)wanted_uri_len * sizeof(Py_UCS4)) == 0);
    }
    xslt_number_name source = number_local_name(eng, node);
    source.ns = node->ns;
    if (number_has_namespaces(eng) || eng->number_index.positions.count != 0 || source.name != node->text) {
        if (number_index_extend(eng, node) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            PyErr_NoMemory();                     /* GCOVR_EXCL_LINE */
            return -1;                            /* GCOVR_EXCL_LINE */
        }
    }
    if (eng->number_index.positions.count != 0) {
        source = number_name(eng, node);
    }
    if (source.name_len != local_len || memcmp(source.name, local, (size_t)local_len * sizeof(Py_UCS4)) != 0) {
        return 0;
    }
    if (source.ns == TH_NS_SVG) {
        return ucs4_ascii_eq(wanted_uri, wanted_uri_len, XP_SVG_NS_URI);
    }
    if (source.ns == TH_NS_MATHML) {
        return ucs4_ascii_eq(wanted_uri, wanted_uri_len, XP_MATHML_NS_URI);
    }
    return source.uri_len == wanted_uri_len &&
           (wanted_uri_len == 0 || memcmp(source.uri, wanted_uri, (size_t)wanted_uri_len * sizeof(Py_UCS4)) == 0);
}

static int number_names_equal(const xslt_number_name *left, const xslt_number_name *right) {
    return ((left->type ^ right->type) | (left->ns ^ right->ns) | (left->name_len ^ right->name_len) |
            (left->uri_len ^ right->uri_len)) == 0 &&
           (left->name_len == 0 || memcmp(left->name, right->name, (size_t)left->name_len * sizeof(Py_UCS4)) == 0) &&
           (left->uri_len == 0 || memcmp(left->uri, right->uri, (size_t)left->uri_len * sizeof(Py_UCS4)) == 0);
}

static int number_counts_name(const engine *eng, const th_node *node) {
    xslt_number_name name = number_default_name(eng, node);
    return number_names_equal(&name, &eng->current_number_name);
}

static int number_counts_default(const engine *eng, const th_node *node) {
    if (node->type != eng->cur_node->type) {
        return 0;
    }
    if (node->type == TH_NODE_ELEMENT && eng->number_index.positions.count == 0 &&
        node->text_len == eng->current_number_name.name_len) {
        return node->ns == eng->current_number_name.ns &&
               memcmp(node->text, eng->current_number_name.name, (size_t)node->text_len * sizeof(Py_UCS4)) == 0;
    }
    return number_counts_name(eng, node);
}

/* Whether the count pattern matches node, count NULL for the default. */
static int number_counts(engine *eng, xslt_number_pattern *count, th_node *node) {
    return count != NULL ? number_pattern_has(eng, count, node, -1) : number_counts_default(eng, node);
}

/* The next node in document order after node (pre-order), or NULL at the end. The level="any"
   walk always reaches the current node first, so the ascent never runs off the tree's end. */
static th_node *doc_next(th_node *node) {
    if (node->first_child != NULL) {
        return node->first_child;
    }
    while (node != NULL) { /* GCOVR_EXCL_BR_LINE: the walk stops at the current node, never at NULL */
        if (node->next_sibling != NULL) {
            return node->next_sibling;
        }
        node = node->parent;
    }
    return NULL; /* GCOVR_EXCL_LINE */
}

static long unindexed_any_number(engine *eng, th_node *start, xslt_number_pattern *count, xslt_number_pattern *from) {
    long counter = 0;
    for (th_node *node = start;;) {
        TH_WORK_STEP();
        if (number_counts(eng, count, node)) {
            counter++;
        }
        if (node == eng->src_root || (from != NULL && number_pattern_has(eng, from, node, -1))) {
            return counter;
        }
        if (node->prev_sibling != NULL) {
            node = node->prev_sibling;
            while (node->last_child != NULL) {
                node = node->last_child;
            }
        } else {
            node = node->parent;
        }
    }
}

/* The names a walk tallies all come from number_default_name in one state of the namespace index, where uri_id
   stands for the uri: reading the uri text here would cost its length at every element the walk passes. */
static uint32_t number_name_hash(uint64_t seed, const xslt_number_name *name) {
    uint64_t hash = seed;
    hash = (hash ^ (uint64_t)name->type) * UINT64_C(1099511628211);
    hash = (hash ^ (uint64_t)name->ns) * UINT64_C(1099511628211);
    hash = (hash ^ (uint64_t)name->uri_id) * UINT64_C(1099511628211);
    hash = (hash ^ (uint64_t)name->name_len) * UINT64_C(1099511628211);
    return number_hash_finish(number_hash_text(hash, name->name, name->name_len));
}

static int number_tally_names(const xslt_number_name *left, const xslt_number_name *right) {
    const Py_ssize_t differs = (left->type ^ right->type) | (left->ns ^ right->ns) |
                               (Py_ssize_t)(left->uri_id ^ right->uri_id) | (left->name_len ^ right->name_len);
    return differs == 0 &&
           (left->name_len == 0 || memcmp(left->name, right->name, (size_t)left->name_len * sizeof(Py_UCS4)) == 0);
}

static xslt_number_tally *number_tally_slot(xslt_number_tally *tallies, size_t capacity, uint32_t hash,
                                            const xslt_number_name *name) {
    size_t slot = th_hash_slot(hash, capacity);
    while (tallies[slot].count != 0 && !number_tally_names(&tallies[slot].name, name)) {
        slot = (slot + 1) & (capacity - 1);
    }
    return &tallies[slot];
}

static int number_tallies_grow(xslt_number_walk *walk) {
    if (walk->tallies == NULL && number_hash_seed(&walk->seed) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;                                                    /* GCOVR_EXCL_LINE */
    }
    size_t capacity;
    size_t bytes;
    /* GCOVR_EXCL_BR_START: alloc */
    if (!th_grow_cap(walk->tally_capacity + 1, walk->tally_capacity, 32, sizeof(*walk->tallies), &capacity, &bytes)) {
        return -1; /* GCOVR_EXCL_LINE */
    }
    /* GCOVR_EXCL_BR_STOP */
    xslt_number_tally *tallies = PyMem_Calloc(1, bytes);
    if (tallies == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;         /* GCOVR_EXCL_LINE */
    }
    for (size_t index = 0; index < walk->tally_capacity; index++) {
        const xslt_number_tally *tally = &walk->tallies[index];
        if (tally->count != 0) {
            *number_tally_slot(tallies, capacity, tally->hash, &tally->name) = *tally;
        }
    }
    PyMem_Free(walk->tallies);
    walk->tallies = tallies;
    walk->tally_capacity = capacity;
    return 0;
}

static void number_walk_free(xslt_number_walk *walk) {
    PyMem_Free(walk->positions.entries);
    PyMem_Free(walk->tallies);
}

/* Without the namespace index an element's expanded name is a function of its namespace and its text alone, so an
   element spelled like the recent source shares its tally without resolving the name again. Every walk starts at the
   document node, so a recent source is set before the first element arrives. */
static int number_repeats_source(const xslt_number_walk *walk, const th_node *node) {
    const th_node *source = walk->recent_source;
    return ((node->type ^ source->type) | (node->ns ^ source->ns) | (node->text_len ^ source->text_len)) == 0 &&
           memcmp(node->text, source->text, (size_t)node->text_len * sizeof(Py_UCS4)) == 0;
}

/* Number node among the nodes of its expanded name the walk has passed since the last from match. Returns the
   number, or -1 on allocation failure. */
static long number_walk_step(engine *eng, xslt_number_walk *walk, const th_node *node) {
    /* number_name expects every element it is asked about to be in the namespace index, which so far reaches the
       last element numbered or tested by a pattern: the walk to a comment or text node passes elements beyond it. */
    if (walk->indexed && node->type == TH_NODE_ELEMENT) {
        if (number_index_extend(eng, node) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;                            /* GCOVR_EXCL_LINE */
        }
    }
    xslt_number_tally *tally = walk->recent;
    if (eng->number_index.positions.count != 0 || node->type != TH_NODE_ELEMENT || !number_repeats_source(walk, node)) {
        const xslt_number_name name = number_default_name(eng, node);
        tally = walk->recent;
        if (tally == NULL || !number_tally_names(&tally->name, &name)) {
            if (walk->tally_count == walk->tally_capacity / 2) {
                if (number_tallies_grow(walk) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                    return -1;                       /* GCOVR_EXCL_LINE */
                }
            }
            const uint32_t hash = number_name_hash(walk->seed, &name);
            tally = number_tally_slot(walk->tallies, walk->tally_capacity, hash, &name);
            if (tally->count == 0) {
                tally->name = name;
                tally->hash = hash;
                walk->tally_count++;
            }
            walk->recent = tally;
        }
        walk->recent_source = node;
    }
    if (tally->epoch != walk->epoch) {
        tally->epoch = walk->epoch;
        tally->count = 0;
    }
    return ++tally->count;
}

/* Index an element of a sibling run before its default name is read, as remember_number_node does for the current node.
 */
static int number_index_child(engine *eng, th_node *node) {
    if (node->type == TH_NODE_ELEMENT && th_tree_is_xml(eng->src_tree)) {
        return number_index_extend(eng, node);
    }
    return 0;
}

/* Count every node of the sibling run that node belongs to in one pass, so a run whose counted nodes alternate with
   others is not rescanned at each of them. Returns the count of node, or -1 with an error set. */
static long number_fill_siblings(engine *eng, xslt_number_siblings *siblings, const match_set *count_set,
                                 th_node *node) {
    th_node *first = node;
    while (first->prev_sibling != NULL) {
        first = first->prev_sibling;
    }
    xslt_number_walk tallies = {0};
    long seen = 0;
    int failed = 0;
    for (th_node *child = first; child != NULL; child = child->next_sibling) {
        TH_WORK_STEP();
        long ordinal;
        if (count_set != NULL) {
            ordinal = seen + 1;
            seen += match_set_has(count_set, child, -1);
        } else {
            if (number_index_child(eng, child) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                failed = 1;                           /* GCOVR_EXCL_LINE */
                break;                                /* GCOVR_EXCL_LINE */
            }
            const xslt_number_name name = number_default_name(eng, child);
            if (tallies.tally_count == tallies.tally_capacity / 2) {
                if (number_tallies_grow(&tallies) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                    failed = 1;                          /* GCOVR_EXCL_LINE */
                    break;                               /* GCOVR_EXCL_LINE */
                }
            }
            const uint32_t hash = number_name_hash(tallies.seed, &name);
            xslt_number_tally *tally = number_tally_slot(tallies.tallies, tallies.tally_capacity, hash, &name);
            if (tally->count == 0) {
                tally->name = name;
                tally->hash = hash;
                tallies.tally_count++;
            }
            ordinal = ++tally->count;
        }
        if (th_node_map_insert(&siblings->counts, child, (Py_ssize_t)ordinal) < 0) { /* GCOVR_EXCL_BR_LINE */
            failed = 1;                                                              /* GCOVR_EXCL_LINE */
            break;                                                                   /* GCOVR_EXCL_LINE */
        }
    }
    number_walk_free(&tallies);
    if (failed) {            /* GCOVR_EXCL_BR_LINE: allocation failure */
        PyErr_NoMemory();    /* GCOVR_EXCL_LINE */
        return fail_py(eng); /* GCOVR_EXCL_LINE */
    }
    return (long)th_node_map_find(&siblings->counts, node);
}

/* The count of node in its sibling run, filled once per run. Default counts are dropped when the number index changes,
   since names resolved through it do not compare with names resolved without it. */
static long sibling_count(engine *eng, xslt_number_siblings *siblings, const match_set *count_set, th_node *node) {
    if (count_set == NULL) {
        const int indexed = eng->number_index.positions.count != 0;
        if (siblings->indexed != indexed) {
            PyMem_Free(siblings->counts.entries);
            *siblings = (xslt_number_siblings){.indexed = indexed};
        }
    }
    const Py_ssize_t position = th_node_map_find(&siblings->counts, node);
    if (position != 0) {
        return (long)position;
    }
    long count = 1;
    th_node *prev = node->prev_sibling;
    for (int scanned = 0; prev != NULL && scanned < 64; prev = prev->prev_sibling, scanned++) {
        TH_WORK_STEP();
        count += count_set != NULL ? match_set_has(count_set, prev, -1) : number_counts_default(eng, prev);
    }
    if (prev == NULL) {
        return count;
    }
    return number_fill_siblings(eng, siblings, count_set, node);
}

/* The count of node among the counted nodes before it in its sibling run, scanned back to the start of the run. A
   count pattern that depends on the current node answers differently at each number, so no answer is kept for it. */
static long scan_siblings(engine *eng, xslt_number_pattern *count, th_node *node) {
    long value = 1;
    for (th_node *prev = node->prev_sibling; prev != NULL; prev = prev->prev_sibling) {
        TH_WORK_STEP();
        value += number_counts(eng, count, prev);
    }
    return value;
}

static long level_number(engine *eng, xslt_number_site *site, xslt_number_pattern *count_pattern, th_node *node) {
    if (count_pattern != NULL && count_pattern->set == NULL) {
        return scan_siblings(eng, count_pattern, node);
    }
    const int have_count = count_pattern != NULL;
    xslt_number_memo *memo = have_count ? &site->count_memo : &eng->number_memo;
    if (!have_count && node == memo->node) {
        return memo->value;
    }
    long count;
    th_node *prev = node->prev_sibling;
    if (prev != NULL && prev == memo->node &&
        (have_count || number_names_equal(&eng->number_memo_name, &eng->current_number_name))) {
        count = memo->value + 1;
    } else {
        count = sibling_count(eng, have_count ? &site->count_siblings : &eng->default_siblings,
                              have_count ? count_pattern->set : NULL, node);
        if (count < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;   /* GCOVR_EXCL_LINE */
        }
    }
    memo->node = node;
    memo->value = count;
    if (!have_count) {
        eng->number_memo_name = eng->current_number_name;
    }
    return count;
}

/* level="any" with the default count, back to the nearest match of from_pattern when there is one. The walk keeps
   a tally per expanded name, not the tally of the name numbered last: a document that alternates two numbered
   names would otherwise restart it at every node. */
static int default_any_number(engine *eng, th_node *target, xslt_number_site *site, xslt_number_pattern *from,
                              long *out) {
    xslt_number_walk *walk = &site->any;
    const int indexed = eng->number_index.positions.count != 0;
    /* Names resolved before the namespace index existed do not compare with the ones resolved through it. */
    if (walk->indexed != indexed) {
        number_walk_free(walk);
        *walk = (xslt_number_walk){.indexed = indexed};
    }
    Py_ssize_t position = th_node_map_find(&walk->positions, target);
    if (position != 0) {
        *out = (long)position;
        return 0;
    }
    /* One numbering call does not amortize a document index. */
    if (!walk->repeated) {
        walk->repeated = 1;
        *out = unindexed_any_number(eng, target, NULL, from);
        return 0;
    }
    th_node *node = walk->last == NULL ? eng->src_root : doc_next(walk->last);
    for (;; node = doc_next(node)) {
        TH_WORK_STEP();
        if (from != NULL && match_set_has(from->set, node, -1)) {
            walk->epoch++;
        }
        const long number = number_walk_step(eng, walk, node);
        /* GCOVR_EXCL_BR_START: alloc */
        if (number < 0 || th_node_map_insert(&walk->positions, node, (Py_ssize_t)number) < 0) {
            PyErr_NoMemory();    /* GCOVR_EXCL_LINE */
            return fail_py(eng); /* GCOVR_EXCL_LINE */
        }
        /* GCOVR_EXCL_BR_STOP */
        if (node == target) {
            walk->last = node;
            *out = number;
            return 0;
        }
    }
}

static int explicit_any_number(engine *eng, th_node *target, xslt_number_site *site, xslt_number_pattern *count,
                               xslt_number_pattern *from, long *out) {
    xslt_number_prefix *cache = &site->explicit_any;
    if (cache->last == NULL) {
        cache->value = unindexed_any_number(eng, target, count, from);
        cache->last = target;
        *out = cache->value;
        return 0;
    }
    if (cache->last == target) {
        *out = cache->value;
        return 0;
    }
    Py_ssize_t position = th_node_map_find(&cache->positions, target);
    if (position != 0) {
        *out = (long)(position - 1);
        return 0;
    }
    long counter = cache->positions.entries == NULL ? 0 : cache->value;
    th_node *node = cache->positions.entries == NULL ? eng->src_root : doc_next(cache->last);
    for (;; node = doc_next(node)) {
        TH_WORK_STEP();
        if (from != NULL && match_set_has(from->set, node, -1)) {
            counter = 0;
        }
        if (match_set_has(count->set, node, -1)) {
            counter++;
        }
        /* Unmatched nodes can number zero; reserve zero for absent map entries. */
        if (th_node_map_insert(&cache->positions, node, counter + 1) < 0) { /* GCOVR_EXCL_BR_LINE: OOM */
            PyErr_NoMemory();                                               /* GCOVR_EXCL_LINE */
            return fail_py(eng);                                            /* GCOVR_EXCL_LINE */
        }
        if (node == target) {
            cache->last = node;
            cache->value = counter;
            *out = counter;
            return 0;
        }
    }
}

/* XSLT 1.0 erratum E24 makes a value that is NaN, infinite or below 0.5 a recoverable error, recovered by inserting
   its string value. A finite value too large for a long still formats, as the exact integer the double holds; past
   their range, the alphabetic and roman tokens format with the token "1" (XSLT 2.0 section 12.3). Kept out of line so
   the in-range path of do_number compiles as before. */
TH_NOINLINE static int emit_out_of_range_value(engine *eng, th_node *instruction, th_node *out_parent, double value,
                                               double rounded) {
    if (!(value >= 0.5) || value > DBL_MAX) {
        const xp_result number = {.kind = XP_NUMBER, .number = value};
        Py_ssize_t text_len = 0;
        Py_UCS4 *text = to_string(eng->src_tree, &number, &text_len);
        if (text == NULL) {                    /* GCOVR_EXCL_BR_LINE: alloc */
            return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
        }
        int rc = emit_text(eng, out_parent, text, text_len);
        PyMem_Free(text);
        return rc;
    }
    char digits[DBL_MAX_10_EXP + 2];
    int digits_len = snprintf(digits, sizeof(digits), "%.0f", rounded);
    Py_ssize_t format_len = 0;
    const Py_UCS4 *format = number_attr(eng, instruction, eng->number_attrs.format, "format", &format_len);
    Py_ssize_t gsep_len = 0;
    const Py_UCS4 *gsep =
        number_attr(eng, instruction, eng->number_attrs.grouping_separator, "grouping-separator", &gsep_len);
    Py_ssize_t gsize_len = 0;
    const Py_UCS4 *gsize_text =
        number_attr(eng, instruction, eng->number_attrs.grouping_size, "grouping-size", &gsize_len);
    long gsize = gsize_text != NULL ? parse_grouping_size(gsize_text, gsize_len) : 0;
    /* A single value takes the format's prefix, its first token and its suffix (section 7.7.1). */
    Py_ssize_t token_start = 0;
    while (token_start < format_len && !alnum_cp(format[token_start])) {
        token_start++;
    }
    Py_ssize_t width = 0;
    while (token_start + width < format_len && format[token_start + width] >= '0' &&
           format[token_start + width] <= '9') {
        width++;
    }
    Py_ssize_t token_end = token_start;
    while (token_end < format_len && alnum_cp(format[token_end])) {
        token_end++;
    }
    Py_ssize_t suffix_start = format_len;
    while (suffix_start > token_end && !alnum_cp(format[suffix_start - 1])) {
        suffix_start--;
    }
    xb buffer = {0};
    /* GCOVR_EXCL_BR_START: allocation failure */
    if (xb_add(&buffer, format, token_start) < 0 ||
        emit_digits(&buffer, digits, digits_len, width, gsep, gsep_len, gsize) < 0 ||
        (suffix_start < format_len && xb_add(&buffer, format + suffix_start, format_len - suffix_start) < 0)) {
        xb_free(&buffer);                  /* GCOVR_EXCL_LINE */
        return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
    }
    /* GCOVR_EXCL_BR_STOP */
    int rc = emit_text(eng, out_parent, buffer.data, buffer.len);
    xb_free(&buffer);
    return rc;
}

/* Remember the current node's name for the default count pattern, so the level counts below compare against it
   without re-deriving it per node. Returns -1 on allocation failure. */
static int remember_number_node(engine *eng) {
    eng->current_number_name = number_local_name(eng, eng->cur_node);
    if (eng->cur_node->type == TH_NODE_ELEMENT) {
        eng->current_number_name.ns = eng->cur_node->ns;
        if (th_tree_is_xml(eng->src_tree) && (number_has_namespaces(eng) || eng->number_index.positions.count != 0 ||
                                              eng->current_number_name.name != eng->cur_node->text)) {
            if (number_index_extend(eng, eng->cur_node) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                return -1;                                     /* GCOVR_EXCL_LINE */
            }
            eng->current_number_name = number_name(eng, eng->cur_node);
        }
    }
    eng->current_number_node = eng->cur_node;
    return 0;
}

/* The site of an xsl:number instruction, created on its first call. Returns NULL on allocation failure. */
static xslt_number_site *number_site(engine *eng, const th_node *instruction) {
    xslt_number_sites *sites = &eng->number_sites;
    if (sites->recent == instruction) {
        return &sites->list[sites->recent_position];
    }
    const Py_ssize_t position = th_node_map_find(&sites->index, instruction);
    if (position != 0) {
        sites->recent = instruction;
        sites->recent_position = (size_t)position - 1;
        return &sites->list[position - 1];
    }
    if (sites->count == sites->capacity) {
        size_t capacity;
        size_t bytes;
        /* GCOVR_EXCL_BR_START: allocation failure */
        if (!th_grow_cap(sites->count + 1, sites->capacity, 8, sizeof(*sites->list), &capacity, &bytes)) {
            return NULL; /* GCOVR_EXCL_LINE */
        }
        xslt_number_site *grown = PyMem_Realloc(sites->list, bytes);
        if (grown == NULL) {
            return NULL; /* GCOVR_EXCL_LINE */
        }
        /* GCOVR_EXCL_BR_STOP */
        memset(grown + sites->capacity, 0, (capacity - sites->capacity) * sizeof(*grown));
        sites->list = grown;
        sites->capacity = capacity;
    }
    if (th_node_map_insert(&sites->index, instruction, (Py_ssize_t)sites->count + 1) < 0) { /* GCOVR_EXCL_BR_LINE */
        return NULL;                                                                        /* GCOVR_EXCL_LINE */
    }
    sites->recent = instruction;
    sites->recent_position = sites->count;
    return &sites->list[sites->count++];
}

static void number_site_free(xslt_number_site *site) {
    match_set_free(&site->count_match.matched);
    match_set_free(&site->from_match.matched);
    PyMem_Free(site->explicit_any.positions.entries);
    number_walk_free(&site->any);
    PyMem_Free(site->count_siblings.counts.entries);
    PyMem_Free(site->climb.stops.entries);
    PyMem_Free(site->climb.nodes.items);
}

/* level="any": how many counted nodes precede the target in document order, back to the nearest from match. */
static int number_any(engine *eng, th_node *target, xslt_number_site *site, xslt_number_pattern *count,
                      xslt_number_pattern *from, long *counter) {
    if (from == NULL || from->set != NULL) {
        if (count == NULL) {
            return default_any_number(eng, target, site, from, counter);
        }
        if (count->set != NULL) {
            return explicit_any_number(eng, target, site, count, from, counter);
        }
    }
    *counter = unindexed_any_number(eng, target, count, from);
    return 0;
}

/* The numbers of one xsl:number instruction, outermost first. The stack array holds the usual count chain; a deeper
   chain moves the numbers to the heap, so no chain length is capped. */
typedef struct {
    long *data;
    Py_ssize_t len;
    Py_ssize_t cap;
    long stack[64];
} number_values;

static void number_values_init(number_values *values) {
    values->data = values->stack;
    values->len = 0;
    values->cap = Py_ARRAY_LENGTH(values->stack);
}

static void number_values_free(number_values *values) {
    if (values->data != values->stack) {
        PyMem_Free(values->data);
    }
}

/* Returns -1 on allocation failure. */
static int number_values_push(number_values *values, long value) {
    if (values->len == values->cap) {
        size_t cap;
        size_t bytes;
        const int fits = th_grow_cap((size_t)values->len + 1, (size_t)values->cap, Py_ARRAY_LENGTH(values->stack),
                                     sizeof(long), &cap, &bytes);
        if (!fits) {   /* GCOVR_EXCL_BR_LINE: allocation size overflow */
            return -1; /* GCOVR_EXCL_LINE */
        }
        long *grown = PyMem_Malloc(bytes);
        if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;       /* GCOVR_EXCL_LINE */
        }
        memcpy(grown, values->data, (size_t)values->len * sizeof(long));
        number_values_free(values);
        values->data = grown;
        values->cap = (Py_ssize_t)cap;
    }
    values->data[values->len++] = value;
    return 0;
}

/* Nodes a climb passes before its site keeps the stops: an ordinary document never climbs this far. */
#define XSLT_NUMBER_CLIMB_STEPS 32

/* Whether count and from answer the same at every call, so the stops one climb finds hold for the next. */
static int number_climb_keepable(const xslt_number_pattern *count, const xslt_number_pattern *from) {
    return (count == NULL || count->set != NULL) && (from == NULL || from->set != NULL);
}

/* Appends node to list. Returns 1 plus its position, or -1 on allocation failure. */
static Py_ssize_t node_list_add(xslt_node_list *list, th_node *node) {
    if (list->count == list->capacity) {
        size_t capacity;
        size_t bytes;
        /* GCOVR_EXCL_BR_START: allocation failure */
        if (!th_grow_cap(list->count + 1, list->capacity, 16, sizeof(*list->items), &capacity, &bytes)) {
            return -1; /* GCOVR_EXCL_LINE */
        }
        th_node **grown = PyMem_Realloc(list->items, bytes);
        if (grown == NULL) {
            return -1; /* GCOVR_EXCL_LINE */
        }
        /* GCOVR_EXCL_BR_STOP */
        list->items = grown;
        list->capacity = capacity;
    }
    list->items[list->count++] = node;
    return (Py_ssize_t)list->count;
}

/* The nearest ancestor-or-self of node that count or from matches, or NULL when none does or node is NULL. The climb
   ends at the first node it already answered and records its answer at every node it passed, so each node is passed
   once per site. The stops only save work: when one cannot be recorded, the answer stands without it. */
static th_node *number_climb(xslt_number_climb *climb, const match_set *count, const match_set *from, th_node *node) {
    th_node *top = node;
    Py_ssize_t known = 0;
    for (; top != NULL; top = top->parent) {
        TH_WORK_STEP();
        known = th_node_map_find(&climb->stops, top);
        if (known != 0) {
            break;
        }
        if ((from != NULL && match_set_has(from, top, -1)) || (count != NULL && match_set_has(count, top, -1))) {
            known = node_list_add(&climb->nodes, top);
            /* GCOVR_EXCL_BR_START: allocation failure */
            if (known < 0 || th_node_map_insert(&climb->stops, top, known) < 0) {
                return top; /* GCOVR_EXCL_LINE */
            }
            /* GCOVR_EXCL_BR_STOP */
            break;
        }
    }
    if (top == NULL) {
        known = -1;
    }
    for (th_node *passed = node; passed != top; passed = passed->parent) {
        if (th_node_map_insert(&climb->stops, passed, known) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            break;                                                  /* GCOVR_EXCL_LINE */
        }
    }
    return known < 0 ? NULL : climb->nodes.items[known - 1];
}

/* The tally of node's expanded name in the lineage walk, added on first sight. Returns NULL on allocation failure. */
static xslt_number_tally *number_lineage_tally(engine *eng, xslt_number_walk *walk, const th_node *node) {
    if (walk->tally_count == walk->tally_capacity / 2) {
        if (number_tallies_grow(walk) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return NULL;                     /* GCOVR_EXCL_LINE */
        }
    }
    const xslt_number_name name = number_default_name(eng, node);
    const uint32_t hash = number_name_hash(walk->seed, &name);
    xslt_number_tally *tally = number_tally_slot(walk->tallies, walk->tally_capacity, hash, &name);
    if (tally->count == 0) {
        *tally = (xslt_number_tally){.name = name, .count = 1, .open = 0, .hash = hash};
        walk->tally_count++;
    }
    return tally;
}

/* Sets above to the nearest ancestor of element with the same expanded name, or NULL. The lineage walk enters the
   document in order up to element the first time, keeping each name's innermost element it is inside. Returns -1 on
   allocation failure. */
static int number_lineage_above(engine *eng, th_node *element, th_node **above) {
    xslt_number_lineage *lineage = &eng->number_lineage;
    xslt_number_walk *walk = &lineage->walk;
    const int indexed = eng->number_index.positions.count != 0;
    /* Names resolved before the namespace index existed do not compare with the ones resolved through it. */
    if (walk->indexed != indexed) {
        number_walk_free(walk);
        *walk = (xslt_number_walk){.indexed = indexed};
        lineage->elements.count = 0;
    }
    Py_ssize_t known = th_node_map_find(&walk->positions, element);
    while (known == 0) {
        TH_WORK_STEP();
        th_node *node = walk->last == NULL ? eng->src_root : walk->last;
        if (node->type == TH_NODE_ELEMENT) {
            if (indexed) {
                if (number_index_extend(eng, node) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                    return -1;                            /* GCOVR_EXCL_LINE */
                }
            }
            xslt_number_tally *tally = number_lineage_tally(eng, walk, node);
            const Py_ssize_t entered = node_list_add(&lineage->elements, node);
            if (tally == NULL || entered < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                return -1;                      /* GCOVR_EXCL_LINE */
            }
            const Py_ssize_t answer = tally->open == 0 ? -1 : tally->open;
            if (th_node_map_insert(&walk->positions, node, answer) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                return -1;                                                /* GCOVR_EXCL_LINE */
            }
            tally->open = entered;
            if (node == element) {
                known = answer;
            }
        }
        if (node->first_child != NULL) {
            walk->last = node->first_child;
            continue;
        }
        for (;;) {
            if (node->type == TH_NODE_ELEMENT) {
                xslt_number_tally *tally = number_lineage_tally(eng, walk, node);
                if (tally == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                    return -1;       /* GCOVR_EXCL_LINE */
                }
                const Py_ssize_t closed = th_node_map_find(&walk->positions, node);
                tally->open = closed < 0 ? 0 : closed;
            }
            if (node->next_sibling != NULL || node->parent == NULL) {
                walk->last = node->next_sibling;
                break;
            }
            node = node->parent;
        }
    }
    *above = known < 0 ? NULL : lineage->elements.items[known - 1];
    return 0;
}

/* Appends the level number of node to values. Returns -1 on allocation failure. */
static int number_push_level(engine *eng, xslt_number_site *site, xslt_number_pattern *count, th_node *node,
                             number_values *values) {
    const long value = level_number(eng, site, count, node);
    if (value < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;   /* GCOVR_EXCL_LINE */
    }
    return number_values_push(values, value);
}

/* number_multiple once the site keeps its climb stops: it visits only the counted nodes and the from bound. A default
   count numbers the nodes of the current node's own expanded name, which the lineage walk links ancestor to ancestor,
   while the stops it keeps are those of from alone. Out of line, so the ordinary climb compiles as before. Returns -1
   on allocation failure. */
TH_NOINLINE static int number_kept_multiple(engine *eng, xslt_number_site *site, th_node *start,
                                            xslt_number_pattern *count, xslt_number_pattern *from,
                                            number_values *values) {
    const match_set *from_set = from == NULL ? NULL : from->set;
    if (count != NULL) {
        for (th_node *node = number_climb(&site->climb, count->set, from_set, start);
             node != NULL && (from_set == NULL || !match_set_has(from_set, node, -1));
             node = number_climb(&site->climb, count->set, from_set, node->parent)) {
            if (number_push_level(eng, site, count, node, values) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                return -1;                                               /* GCOVR_EXCL_LINE */
            }
        }
        return 0;
    }
    /* bound is the nearest from match: the nodes below it climb to it, and a node above it climbs elsewhere. */
    th_node *bound = number_climb(&site->climb, NULL, from_set, start);
    for (th_node *node = start; node != NULL && node != bound;) {
        if (number_climb(&site->climb, NULL, from_set, node) != bound) {
            break;
        }
        if (number_push_level(eng, site, NULL, node, values) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;                                              /* GCOVR_EXCL_LINE */
        }
        if (node->type != TH_NODE_ELEMENT) {
            break;
        }
        if (number_lineage_above(eng, node, &node) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;                                    /* GCOVR_EXCL_LINE */
        }
    }
    return 0;
}

/* level="multiple": one number per counted ancestor-or-self, outermost first, bounded by the nearest from ancestor.
   The walk runs innermost first, so its numbers are reversed in place once the chain ends. */
static int number_multiple(engine *eng, xslt_number_site *site, th_node *start, xslt_number_pattern *count,
                           xslt_number_pattern *from, number_values *values) {
    const Py_ssize_t first = values->len;
    if (site->climb.kept) {
        if (number_kept_multiple(eng, site, start, count, from, values) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;                                                         /* GCOVR_EXCL_LINE */
        }
    } else {
        int steps = 0;
        for (th_node *node = start; node != NULL; node = node->parent) {
            TH_WORK_STEP();
            if (from != NULL && number_pattern_has(eng, from, node, -1)) {
                break;
            }
            if (number_counts(eng, count, node)) {
                if (number_push_level(eng, site, count, node, values) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                    return -1;                                               /* GCOVR_EXCL_LINE */
                }
            }
            if (++steps == XSLT_NUMBER_CLIMB_STEPS) {
                site->climb.kept = number_climb_keepable(count, from);
            }
        }
    }
    for (Py_ssize_t low = first, high = values->len - 1; low < high; low++, high--) {
        const long swap = values->data[low];
        values->data[low] = values->data[high];
        values->data[high] = swap;
    }
    return 0;
}

/* number_single once the site keeps its climb stops. A climb only gets that far with a count pattern, since the
   default count matches the current node itself. Out of line, so the ordinary climb compiles as before. */
TH_NOINLINE static th_node *number_kept_single(xslt_number_site *site, th_node *start, xslt_number_pattern *count,
                                               xslt_number_pattern *from) {
    const match_set *from_set = from == NULL ? NULL : from->set;
    th_node *stop = number_climb(&site->climb, count->set, from_set, start);
    return stop != NULL && (from_set == NULL || !match_set_has(from_set, stop, -1)) ? stop : NULL;
}

/* level="single" (the default): the nearest ancestor-or-self that matches count, bounded by the nearest from
   ancestor, or NULL when there is none. Inlined, since a call costs more than the one step a default count climbs. */
XSLT_FORCEINLINE static th_node *number_single(engine *eng, xslt_number_site *site, th_node *start,
                                               xslt_number_pattern *count, xslt_number_pattern *from) {
    if (site->climb.kept) {
        return number_kept_single(site, start, count, from);
    }
    int steps = 0;
    for (th_node *node = start; node != NULL; node = node->parent) {
        TH_WORK_STEP();
        if (from != NULL && number_pattern_has(eng, from, node, -1)) {
            return NULL;
        }
        if (number_counts(eng, count, node)) {
            return node;
        }
        if (++steps == XSLT_NUMBER_CLIMB_STEPS) {
            site->climb.kept = number_climb_keepable(count, from);
        }
    }
    return NULL;
}

/* The numbers for an instruction whose context item is an attribute or namespace node. Such a node has no siblings,
   and the preceding axis holds no attribute or namespace nodes, so the item counts itself and the walks resume at its
   owner element. A default count matches only nodes of the item's own type, which no element is, so it never walks. */
static int number_attribute(engine *eng, xslt_number_site *site, xslt_number_pattern *count, xslt_number_pattern *from,
                            int any, int multiple, number_values *values) {
    th_node *owner = eng->cur_node;
    const int self_from = from != NULL && number_pattern_has(eng, from, owner, eng->cur_attr);
    const int self_counts = count == NULL || number_pattern_has(eng, count, owner, eng->cur_attr);
    if (any) {
        long counter = self_counts;
        if (count != NULL && !self_from) {
            long walked = 0;
            const int status = number_any(eng, owner, site, count, from, &walked);
            if (status < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                return -1;    /* GCOVR_EXCL_LINE */
            }
            counter += walked;
        }
        if (number_values_push(values, counter) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;                                 /* GCOVR_EXCL_LINE */
        }
        return 0;
    }
    if (self_from) {
        return 0;
    }
    if (multiple) {
        if (count != NULL) {
            const int status = number_multiple(eng, site, owner, count, from, values);
            if (status < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                return -1;    /* GCOVR_EXCL_LINE */
            }
        }
        if (self_counts) {
            if (number_values_push(values, 1) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                return -1;                           /* GCOVR_EXCL_LINE */
            }
        }
        return 0;
    }
    if (self_counts) {
        if (number_values_push(values, 1) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;                           /* GCOVR_EXCL_LINE */
        }
        return 0;
    }
    th_node *target = number_single(eng, site, owner, count, from);
    if (target != NULL) {
        const long value = level_number(eng, site, count, target);
        if (value < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;   /* GCOVR_EXCL_LINE */
        }
        if (number_values_push(values, value) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;                               /* GCOVR_EXCL_LINE */
        }
    }
    return 0;
}

/* count_numbers with its count and from patterns set up, each NULL when the instruction has none. */
static int count_with(engine *eng, th_node *instruction, xslt_number_site *site, xslt_number_pattern *count,
                      xslt_number_pattern *from, number_values *values) {
    Py_ssize_t level_len = 0;
    const Py_UCS4 *level = number_attr(eng, instruction, eng->number_attrs.level, "level", &level_len);
    const int any = level != NULL && ucs4_ascii_eq(level, level_len, "any");
    const int multiple = level != NULL && ucs4_ascii_eq(level, level_len, "multiple");
    if (xp_is_attribute(eng->cur_attr)) {
        return number_attribute(eng, site, count, from, any, multiple, values);
    }
    if (count == NULL && eng->current_number_node != eng->cur_node) {
        if (remember_number_node(eng) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            PyErr_NoMemory();                /* GCOVR_EXCL_LINE */
            return fail_py(eng);             /* GCOVR_EXCL_LINE */
        }
    }
    if (any) {
        long counter = 0;
        const int status = number_any(eng, eng->cur_node, site, count, from, &counter);
        if (number_values_push(values, counter) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;                                 /* GCOVR_EXCL_LINE */
        }
        return status;
    }
    if (multiple) {
        return number_multiple(eng, site, eng->cur_node, count, from, values);
    }
    th_node *target = number_single(eng, site, eng->cur_node, count, from);
    if (target != NULL) {
        const long value = level_number(eng, site, count, target);
        if (value < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;   /* GCOVR_EXCL_LINE */
        }
        if (number_values_push(values, value) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;                               /* GCOVR_EXCL_LINE */
        }
    }
    return 0;
}

/* The numbers xsl:number counts from the source tree when it has no value attribute, per its count, from and level
   attributes. Appends to values and returns -1 on failure. */
static int count_numbers(engine *eng, th_node *instruction, number_values *values) {
    xslt_number_site *site = number_site(eng, instruction);
    if (site == NULL) {      /* GCOVR_EXCL_BR_LINE: allocation failure */
        PyErr_NoMemory();    /* GCOVR_EXCL_LINE */
        return fail_py(eng); /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t count_len = 0;
    const Py_UCS4 *count_source = number_attr(eng, instruction, eng->number_attrs.count, "count", &count_len);
    Py_ssize_t from_len = 0;
    const Py_UCS4 *from_source = number_attr(eng, instruction, eng->number_attrs.from, "from", &from_len);
    xslt_number_pattern count_pattern = {0};
    xslt_number_pattern from_pattern = {0};
    /* once the from pattern is indexed, every later call reads the index, so only calls before ask for the level */
    int probe_first = 0;
    if (from_source != NULL && site->from_match.source != from_source) {
        Py_ssize_t level_len = 0;
        const Py_UCS4 *level = number_attr(eng, instruction, eng->number_attrs.level, "level", &level_len);
        probe_first = level != NULL && ucs4_ascii_eq(level, level_len, "any");
    }
    /* Compilation validates the count and from patterns before a run. */
    int status = count_source == NULL ? 0
                                      : number_pattern_open(eng, instruction, count_source, count_len, probe_first,
                                                            &site->count_match, &count_pattern);
    if (status == 0 && from_source != NULL) {
        status =
            number_pattern_open(eng, instruction, from_source, from_len, probe_first, &site->from_match, &from_pattern);
    }
    if (status == 0) {
        status = count_with(eng, instruction, site, count_source != NULL ? &count_pattern : NULL,
                            from_source != NULL ? &from_pattern : NULL, values);
    }
    if (status == 0 && (count_pattern.failed || from_pattern.failed)) {
        status = -1;
    }
    number_pattern_close(&count_pattern);
    number_pattern_close(&from_pattern);
    return status;
}

TH_NOINLINE static int do_number(engine *eng, th_node *instruction, th_node *out_parent) {
    number_values values;
    number_values_init(&values);
    Py_ssize_t value_len = 0;
    const Py_UCS4 *value_expr = number_attr(eng, instruction, TH_ATTR_VALUE, "value", &value_len);
    if (value_expr != NULL) {
        char errbuf[256];
        xp_program *prog = compile_expression(eng, value_expr, value_len, errbuf, sizeof(errbuf));
        if (prog == NULL) {          /* GCOVR_EXCL_BR_LINE: compilation rejects malformed values before a run */
            if (!PyErr_Occurred()) { /* GCOVR_EXCL_LINE: allocation error */
                PyErr_Format(PyExc_ValueError, "xslt: bad number value: %s", errbuf); /* GCOVR_EXCL_LINE */
            } /* GCOVR_EXCL_LINE */
            return fail_py(eng); /* GCOVR_EXCL_LINE */
        }
        xp_result result;
        int status =
            eval_program(eng, prog, &(xp_item){eng->cur_node, eng->cur_attr}, eng->ctx_pos, eng->ctx_size, &result);
        if (status < 0) {
            return fail_py(eng);
        }
        double value = to_number(eng->src_tree, &result);
        xp_result_free(&result);
        double rounded = floor(value + 0.5);
        /* -(double)LONG_MIN is the first double past LONG_MAX, so the cast is defined below it; NaN fails both tests */
        if (!(value >= 0.5 && rounded < -(double)LONG_MIN)) {
            return emit_out_of_range_value(eng, instruction, out_parent, value, rounded);
        }
        values.stack[values.len++] = (long)rounded;
    } else if (count_numbers(eng, instruction, &values) < 0) {
        number_values_free(&values);
        return -1;
    }
    Py_ssize_t format_len = 0;
    const Py_UCS4 *format = number_attr(eng, instruction, eng->number_attrs.format, "format", &format_len);
    Py_ssize_t gsep_len = 0;
    const Py_UCS4 *gsep =
        number_attr(eng, instruction, eng->number_attrs.grouping_separator, "grouping-separator", &gsep_len);
    Py_ssize_t gsize_len = 0;
    const Py_UCS4 *gsize_text =
        number_attr(eng, instruction, eng->number_attrs.grouping_size, "grouping-size", &gsize_len);
    long gsize = gsize_text != NULL ? parse_grouping_size(gsize_text, gsize_len) : 0;
    xb buffer = {0};
    int formatted = format_multi(&buffer, format, format_len, values.data, values.len, gsep, gsep_len, gsize);
    number_values_free(&values);
    if (formatted < 0) {                   /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
        xb_free(&buffer);                  /* GCOVR_EXCL_LINE */
        return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
    }
    int rc = emit_text(eng, out_parent, buffer.data, buffer.len);
    xb_free(&buffer);
    return rc;
}

/* ---- variables ------------------------------------------------------------ */

/* Evaluate a select attribute at the current node; owner names the instruction in the error. */
XSLT_FORCEINLINE static int eval_select(engine *eng, const Py_UCS4 *select, Py_ssize_t select_len, const char *owner,
                                        xp_result *out_value) {
    xp_program *prog = compile_expression(eng, select, select_len, eng->errbuf, sizeof(eng->errbuf));
    if (prog == NULL) {          /* GCOVR_EXCL_BR_LINE: compilation rejects malformed selects before a run */
        if (!PyErr_Occurred()) { /* GCOVR_EXCL_LINE: allocation error */
            PyErr_Format(PyExc_ValueError, "xslt: bad %s select: %s", owner, eng->errbuf); /* GCOVR_EXCL_LINE */
        } /* GCOVR_EXCL_LINE */
        return fail_py(eng); /* GCOVR_EXCL_LINE */
    }
    int status =
        eval_program(eng, prog, &(xp_item){eng->cur_node, eng->cur_attr}, eng->ctx_pos, eng->ctx_size, out_value);
    if (status < 0) {
        return fail_py(eng);
    }
    return 0;
}

/* Compute a variable/param binding value from its select attribute or body. On an
   RTF body, *out_rtf receives the fragment node (else NULL). */
static int compute_binding(engine *eng, th_node *declaration, xp_result *out_value, th_node **out_rtf) {
    *out_rtf = NULL;
    Py_ssize_t select_len = 0;
    const Py_UCS4 *select = attr_lookup(eng->sheet_tree, declaration, "select", 6, &select_len);
    if (select != NULL) {
        return eval_select(eng, select, select_len, "variable", out_value);
    }
    if (declaration->first_child == NULL) {
        result_string(out_value, NULL, 0);
        return 0;
    }
    th_node *fragment = th_tree_make_fragment(eng->out_tree);
    if (fragment == NULL) {                /* GCOVR_EXCL_BR_LINE: alloc */
        return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
    }
    if (instantiate_under(eng, declaration, fragment, 0, NULL) < 0) {
        return -1;
    }
    Py_ssize_t text_len = 0;
    Py_UCS4 *text = th_node_text(eng->out_tree, fragment, &text_len);
    if (text == NULL) {                    /* GCOVR_EXCL_BR_LINE: alloc */
        return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
    }
    result_string(out_value, text, text_len);
    *out_rtf = fragment;
    return 0;
}

/* ---- with-param collection for call-template / apply-templates ------------ */

typedef struct {
    const Py_UCS4 *name;
    Py_ssize_t name_len;
    xp_result value;
    th_node *rtf;
} param_pass;

/* Evaluate the xsl:with-param children of an instruction. Returns the count or -1;
   fills passes (up to `max`). The caller frees each value. */
static int collect_params(engine *eng, th_node *instruction, param_pass *passes, int max) {
    int count = 0;
    for (th_node *child = instruction->first_child; child != NULL; child = child->next_sibling) {
        if (!is_xsl(eng, child, "with-param")) {
            continue;
        }
        Py_ssize_t name_len = 0;
        const Py_UCS4 *name = attr_lookup(eng->sheet_tree, child, "name", 4, &name_len);
        if (name == NULL) {
            for (int index = 0; index < count; index++) {
                xp_result_free(&passes[index].value);
            }
            fail(eng, "xsl:with-param requires a name attribute");
            return -1;
        }
        if (count >= max) {
            for (int index = 0; index < count; index++) {
                xp_result_free(&passes[index].value);
            }
            fail(eng, "xslt: too many parameters");
            return -1;
        }
        xp_result value;
        th_node *rtf;
        if (compute_binding(eng, child, &value, &rtf) < 0) {
            for (int index = 0; index < count; index++) {
                xp_result_free(&passes[index].value);
            }
            return -1;
        }
        passes[count].name = name;
        passes[count].name_len = name_len;
        passes[count].value = value;
        passes[count].rtf = rtf;
        count++;
    }
    return count;
}

#define XSLT_MAX_PARAMS 16
#define XSLT_MAX_SORTS 8

/* Bind a template's xsl:param declarations, using a matching passed parameter when
   present else the declared default. Returns 0, or -1 on error. */
static int bind_params(engine *eng, th_node *template_body, param_pass *passes, int npasses, Py_ssize_t *out_mark) {
    *out_mark = eng->scope_len;
    for (th_node *child = template_body->first_child; child != NULL; child = child->next_sibling) {
        if (child->type == TH_NODE_TEXT) {
            continue;
        }
        if (!is_xsl(eng, child, "param")) {
            break; /* params must lead the body; the first non-param ends the run */
        }
        Py_ssize_t name_len = 0;
        const Py_UCS4 *name = attr_lookup(eng->sheet_tree, child, "name", 4, &name_len);
        if (name == NULL) {
            return fail(eng, "xsl:param requires a name attribute");
        }
        int matched = 0;
        for (int index = 0; index < npasses; index++) {
            if (str_eq(passes[index].name, passes[index].name_len, name, name_len)) {
                xp_result copy;
                if (copy_result_value(&passes[index].value, &copy) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                    return fail(eng, "out of memory");                    /* GCOVR_EXCL_LINE */
                }
                if (scope_push(eng, name, name_len, copy, passes[index].rtf) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                    return fail(eng, "out of memory");                              /* GCOVR_EXCL_LINE */
                }
                matched = 1;
                break;
            }
        }
        if (!matched) {
            xp_result value;
            th_node *rtf;
            if (compute_binding(eng, child, &value, &rtf) < 0) {
                return -1;
            }
            if (scope_push(eng, name, name_len, value, rtf) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                return fail(eng, "out of memory");                 /* GCOVR_EXCL_LINE */
            }
        }
    }
    return 0;
}

/* Deep-copy an xp_result value so a binding owns an independent copy. */
static int copy_result_value(const xp_result *src, xp_result *dst) {
    memset(dst, 0, sizeof(*dst));
    dst->kind = src->kind;
    if (src->kind == XP_NODESET) {
        for (Py_ssize_t index = 0; index < src->nodes.len; index++) {
            int rc = ns_push(&dst->nodes, src->nodes.items[index].node, src->nodes.items[index].attr);
            if (rc < 0) {            /* GCOVR_EXCL_BR_LINE: alloc */
                xp_result_free(dst); /* GCOVR_EXCL_LINE */
                return -1;           /* GCOVR_EXCL_LINE */
            }
        }
        return 0;
    }
    if (src->kind == XP_STRING) {
        dst->string = ucs4_dup(src->string, src->string_len);
        if (dst->string == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;             /* GCOVR_EXCL_LINE */
        }
        dst->string_len = src->string_len;
        return 0;
    }
    dst->number = src->number;
    dst->boolean = src->boolean;
    return 0;
}

/* ---- call-template -------------------------------------------------------- */

static int do_call_template(engine *eng, th_node *instruction, th_node *out_parent) {
    Py_ssize_t name_len = 0;
    const Py_UCS4 *name = attr_lookup(eng->sheet_tree, instruction, "name", 4, &name_len);
    if (name == NULL) {
        return fail(eng, "xsl:call-template requires a name attribute");
    }
    xslt_named *target = NULL;
    if (eng->named_index.capacity != 0) {
        const Py_ssize_t position = name_index_find(&eng->named_index, name, name_len);
        target = position == 0 ? NULL : &eng->named[position - 1];
    } else {
        for (Py_ssize_t index = 0; index < eng->nnamed; index++) {
            if (str_eq(eng->named[index].name, eng->named[index].name_len, name, name_len)) {
                target = &eng->named[index];
                break;
            }
        }
    }
    if (target == NULL) {
        return fail(eng, "xsl:call-template names an undeclared template");
    }
    /* The passes array lives on the heap, not the stack: do_call_template is on the
       deep-recursion path of a self-calling named template, and keeping this array off
       the frame lets the recursion run much deeper before the depth guard trips. */
    param_pass *passes = PyMem_Malloc((size_t)XSLT_MAX_PARAMS * sizeof(param_pass));
    if (passes == NULL) {                  /* GCOVR_EXCL_BR_LINE: alloc */
        return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
    }
    int npasses = collect_params(eng, instruction, passes, XSLT_MAX_PARAMS);
    if (npasses < 0) {
        PyMem_Free(passes);
        return -1;
    }
    Py_ssize_t mark;
    int rc = bind_params(eng, target->body, passes, npasses, &mark);
    if (rc == 0) {
        rc = instantiate_body(eng, target->body, out_parent);
    }
    scope_drop(eng, mark);
    for (int index = 0; index < npasses; index++) {
        xp_result_free(&passes[index].value);
    }
    PyMem_Free(passes);
    return rc;
}

/* ---- for-each ------------------------------------------------------------- */

/* Order the node-set an instruction selected by its xsl:sort children. Out of line so the sort specs stay off the
   frames of xsl:for-each and xsl:apply-templates, which recurse. */
TH_NOINLINE static int sort_selection(engine *eng, th_node *instruction, xp_nodeset *nodes) {
    sort_spec specs[XSLT_MAX_SORTS];
    int nspecs = compile_sorts(eng, instruction, specs, XSLT_MAX_SORTS);
    if (nspecs < 0) {
        return -1;
    }
    return nspecs > 0 ? sort_nodeset(eng, nodes, specs, nspecs) : 0;
}

static int do_for_each(engine *eng, th_node *instruction, th_node *out_parent) {
    Py_ssize_t select_len = 0;
    const Py_UCS4 *select = attr_lookup(eng->sheet_tree, instruction, "select", 6, &select_len);
    if (select == NULL) {
        return fail(eng, "xsl:for-each requires a select attribute");
    }
    xp_result value;
    if (eval_select(eng, select, select_len, "for-each", &value) < 0) {
        return -1;
    }
    if (value.kind != XP_NODESET) {
        xp_result_free(&value);
        return fail(eng, "xsl:for-each select is not a node-set");
    }
    if (sort_selection(eng, instruction, &value.nodes) < 0) {
        xp_result_free(&value);
        return -1;
    }
    th_node *saved_node = eng->cur_node;
    Py_ssize_t saved_attr = eng->cur_attr;
    Py_ssize_t saved_pos = eng->ctx_pos;
    Py_ssize_t saved_size = eng->ctx_size;
    int rc = 0;
    for (Py_ssize_t index = 0; index < value.nodes.len && rc == 0; index++) {
        eng->cur_node = value.nodes.items[index].node;
        eng->cur_attr = value.nodes.items[index].attr;
        eng->ctx_pos = index + 1;
        eng->ctx_size = value.nodes.len;
        rc = instantiate_body(eng, instruction, out_parent);
    }
    eng->cur_node = saved_node;
    eng->cur_attr = saved_attr;
    eng->ctx_pos = saved_pos;
    eng->ctx_size = saved_size;
    xp_result_free(&value);
    return rc;
}

/* ---- apply-templates ------------------------------------------------------ */

/* The built-in template rules (section 5.8): element/root recurse via
   apply-templates over children; text and attribute copy their string value. */
static int apply_builtin(engine *eng, th_node *node, Py_ssize_t attr, const Py_UCS4 *mode, Py_ssize_t mode_len,
                         th_node *out_parent);

static int apply_to_item(engine *eng, xp_item item, Py_ssize_t pos, Py_ssize_t size, const Py_UCS4 *mode,
                         Py_ssize_t mode_len, param_pass *passes, int npasses, th_node *out_parent) {
    xslt_rule *rule = best_rule(eng, item.node, item.attr, mode, mode_len);
    if (rule == NULL && eng->py_error) {
        return -1;
    }
    th_node *saved_node = eng->cur_node;
    Py_ssize_t saved_attr = eng->cur_attr;
    Py_ssize_t saved_pos = eng->ctx_pos;
    Py_ssize_t saved_size = eng->ctx_size;
    eng->cur_node = item.node;
    eng->cur_attr = item.attr;
    eng->ctx_pos = pos;
    eng->ctx_size = size;
    int rc;
    if (rule != NULL) {
        Py_ssize_t mark;
        rc = bind_params(eng, rule->body, passes, npasses, &mark);
        if (rc == 0) {
            rc = instantiate_body(eng, rule->body, out_parent);
        }
        scope_drop(eng, mark);
    } else {
        rc = apply_builtin(eng, item.node, item.attr, mode, mode_len, out_parent);
    }
    eng->cur_node = saved_node;
    eng->cur_attr = saved_attr;
    eng->ctx_pos = saved_pos;
    eng->ctx_size = saved_size;
    return rc;
}

static int apply_builtin(engine *eng, th_node *node, Py_ssize_t attr, const Py_UCS4 *mode, Py_ssize_t mode_len,
                         th_node *out_parent) {
    if (xp_is_attribute(attr)) {
        const th_node_attr *attribute = xp_item_attribute(eng->src_tree, (xp_item){node, attr});
        return emit_text(eng, out_parent, attribute->value, attribute->value_len);
    }
    if (node->type == TH_NODE_TEXT) {
        Py_ssize_t text_len = node->text_len;
        const Py_UCS4 *text = th_node_realize_text(eng->src_tree, node);
        if (text == NULL && text_len != 0) {   /* GCOVR_EXCL_BR_LINE: alloc */
            return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
        }
        return emit_text(eng, out_parent, text, text_len);
    }
    if (node->type == TH_NODE_ELEMENT || node->type == TH_NODE_DOCUMENT || node->type == TH_NODE_CONTENT) {
        /* a template application (section 5.8) like a matched rule, so it shares the cap that keeps a deep source off
           the C stack limit; libxslt counts it in ctxt->depth too (xsltApplyBuiltinTemplate) */
        if (++eng->depth > XSLT_STACK_CHECK_DEPTH && enter_deep_level(eng, XSLT_LEVEL_SOURCE, NULL) < 0) {
            return -1;
        }
        Py_ssize_t child_pos = 0;
        Py_ssize_t child_count = 0;
        for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
            child_count++;
        }
        int rc = 0;
        /* Only a wildcard-chain rule can match a text node, so without one a text child takes its built-in rule. */
        const int text_builtin = eng->rule_wild < 0;
        for (th_node *child = node->first_child; child != NULL && rc == 0; child = child->next_sibling) {
            child_pos++;
            rc = text_builtin && child->type == TH_NODE_TEXT
                     ? apply_builtin(eng, child, -1, mode, mode_len, out_parent)
                     : apply_to_item(eng, (xp_item){child, -1}, child_pos, child_count, mode, mode_len, NULL, 0,
                                     out_parent);
        }
        eng->depth--;
        return rc;
    }
    return 0; /* comment / PI / doctype: the built-in rule produces nothing */
}

static int apply_templates(engine *eng, th_node *instruction, th_node *out_parent, const Py_UCS4 *outer_mode,
                           Py_ssize_t outer_mode_len) {
    Py_ssize_t mode_len = 0;
    const Py_UCS4 *mode = attr_lookup(eng->sheet_tree, instruction, "mode", 4, &mode_len);
    if (mode == NULL) {
        mode = outer_mode;
        mode_len = outer_mode_len;
    }
    Py_ssize_t select_len = 0;
    const Py_UCS4 *select = attr_lookup(eng->sheet_tree, instruction, "select", 6, &select_len);
    xp_result value;
    if (select != NULL) {
        if (eval_select(eng, select, select_len, "apply-templates", &value) < 0) {
            return -1;
        }
        if (value.kind != XP_NODESET) {
            xp_result_free(&value);
            return fail(eng, "xsl:apply-templates select is not a node-set");
        }
    } else {
        /* Default: the children of the current node, in document order. */
        memset(&value, 0, sizeof(value));
        value.kind = XP_NODESET;
        if (!xp_is_attribute(eng->cur_attr)) {
            for (th_node *child = eng->cur_node->first_child; child != NULL; child = child->next_sibling) {
                if (ns_push(&value.nodes, child, -1) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                    xp_result_free(&value);                 /* GCOVR_EXCL_LINE */
                    return fail(eng, "out of memory");      /* GCOVR_EXCL_LINE */
                }
            }
        }
    }
    if (sort_selection(eng, instruction, &value.nodes) < 0) {
        xp_result_free(&value);
        return -1;
    }
    /* Heap, not stack: apply_templates is on the deep-recursion path (a template body
       applies templates that recurse), so keeping this array off the frame lets the
       recursion run much deeper before the depth guard trips. */
    param_pass *passes = PyMem_Malloc((size_t)XSLT_MAX_PARAMS * sizeof(param_pass));
    if (passes == NULL) {                  /* GCOVR_EXCL_BR_LINE: alloc */
        xp_result_free(&value);            /* GCOVR_EXCL_LINE */
        return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
    }
    int npasses = collect_params(eng, instruction, passes, XSLT_MAX_PARAMS);
    if (npasses < 0) {
        PyMem_Free(passes);
        xp_result_free(&value);
        return -1;
    }
    int rc = 0;
    for (Py_ssize_t index = 0; index < value.nodes.len && rc == 0; index++) {
        rc = apply_to_item(eng, value.nodes.items[index], index + 1, value.nodes.len, mode, mode_len, passes, npasses,
                           out_parent);
    }
    for (int index = 0; index < npasses; index++) {
        xp_result_free(&passes[index].value);
    }
    PyMem_Free(passes);
    xp_result_free(&value);
    return rc;
}

/* ---- the body instantiation walk ------------------------------------------ */

static const Py_UCS4 *alias_result_uri(const engine *eng, const char *prefix, Py_ssize_t prefix_len,
                                       Py_ssize_t *out_len);

static int ns_binding_matches(const th_node_attr *attr, const Py_UCS4 *value, Py_ssize_t value_len) {
    return attr->value_len == value_len &&
           (value_len == 0 || memcmp(attr->value, value, (size_t)value_len * sizeof(Py_UCS4)) == 0);
}

/* Whether an output ancestor of `start` already binds namespace `name` (an "xmlns" or
   "xmlns:prefix" spelling) to the identical URI. The nearest binding wins, so a rebinding
   to a different URI reports out-of-scope and the caller redeclares. Copied source
   attributes can give one output element any number of attributes under any number of
   literal children, so a large ancestor answers from its attribute index; a small one is
   scanned, which costs less than resolving the name's atom. Returns -1 on allocation
   failure. */
static int output_ns_in_scope(engine *eng, th_node *start, const char *name, Py_ssize_t name_len, const Py_UCS4 *value,
                              Py_ssize_t value_len) {
    for (th_node *anc = start; anc != NULL; anc = anc->parent) {
        if (anc->type != TH_NODE_ELEMENT) {
            continue;
        }
        if (anc->attr_count >= TH_ATTR_INDEX_MIN) {
            uint32_t atom = th_attr_lookup(eng->out_tree, name, name_len);
            if (atom == UINT32_MAX) {
                return 0; /* no output element carries the name */
            }
            Py_ssize_t index = th_node_attr_find_atom(eng->out_tree, anc, atom);
            if (index == -1) {
                continue;
            }
            if (index < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                return -1;   /* GCOVR_EXCL_LINE */
            }
            return ns_binding_matches(&anc->attrs[index], value, value_len);
        }
        for (Py_ssize_t index = 0; index < anc->attr_count; index++) {
            TH_WORK_STEP();
            const th_node_attr *attr = &anc->attrs[index];
            Py_ssize_t anc_len = 0;
            const char *anc_name = th_attr_name(eng->out_tree, attr->name_atom, &anc_len);
            if (anc_len == name_len && memcmp(anc_name, name, (size_t)name_len) == 0) {
                return ns_binding_matches(attr, value, value_len);
            }
        }
    }
    return 0;
}

/* Whether `prefix` is named in a whitespace-separated exclude-result-prefixes list, where the default namespace is
   named "#default". */
static int prefix_listed(const Py_UCS4 *list, Py_ssize_t list_len, const char *prefix, Py_ssize_t prefix_len) {
    Py_ssize_t index = 0;
    while (index < list_len) {
        while (index < list_len && ucs4_blank(&list[index], 1)) {
            index++;
        }
        Py_ssize_t start = index;
        while (index < list_len && !ucs4_blank(&list[index], 1)) {
            index++;
        }
        if (index - start == prefix_len) {
            Py_ssize_t offset = 0;
            while (offset < prefix_len && list[start + offset] == (Py_UCS4)(unsigned char)prefix[offset]) {
                offset++;
            }
            if (offset == prefix_len) {
                return 1;
            }
        }
    }
    return 0;
}

static const Py_UCS4 *xsl_prefixed_attr(const engine *eng, const th_node *node, const char *local, Py_ssize_t *out_len);

/* Whether the namespace node `prefix` is excluded at the literal result element `lre` (XSLT 1.0 7.1.1): the
   stylesheet's exclude-result-prefixes, and those of each literal result element from the root down to `lre`, apply
   to it and its descendants, so its namespace node is not copied to the output. */
static int prefix_excluded(const engine *eng, const th_node *lre, const char *prefix, Py_ssize_t prefix_len) {
    if (eng->lre_excludes) {
        for (const th_node *node = lre; node != NULL; node = node->parent) {
            Py_ssize_t list_len = 0;
            const Py_UCS4 *list = xsl_prefixed_attr(eng, node, "exclude-result-prefixes", &list_len);
            if (list != NULL && prefix_listed(list, list_len, prefix, prefix_len)) {
                return 1;
            }
        }
    }
    return prefix_listed(eng->exclude_prefixes, eng->exclude_prefixes_len, prefix, prefix_len);
}

/* Whether the xmlns declaration named `name` binds the literal result element's own prefix,
   which libxslt emits ahead of the other in-scope namespaces on the copied element. */
static int ns_decl_is_self_prefix(const th_node *lre, const char *name, Py_ssize_t name_len) {
    Py_ssize_t colon = -1;
    for (Py_ssize_t index = 0; index < lre->text_len; index++) {
        if (lre->text[index] == ':') {
            colon = index;
            break;
        }
    }
    if (colon < 0) {
        return 0; /* the literal result element is unprefixed */
    }
    if (name_len - 6 != colon) {
        return 0; /* the declaration's prefix has a different length */
    }
    /* Every caller passes an xmlns:prefix declaration, so this xmlns: guard never fails. */
    if (memcmp(name, "xmlns:", 6) != 0) { /* GCOVR_EXCL_BR_LINE */
        return 0;                         /* GCOVR_EXCL_LINE */
    }
    for (Py_ssize_t index = 0; index < colon; index++) {
        if ((Py_UCS4)(unsigned char)name[6 + index] != lre->text[index]) {
            return 0;
        }
    }
    return 1;
}

static int is_xsl_attr(const engine *eng, const th_node *node, Py_ssize_t index);

/* An excluded declaration still reaches the output when the literal result element's name or an attribute name uses
   its prefix, because exclude-result-prefixes only drops superfluous declarations. An unprefixed element name uses the
   default namespace; attribute names never do. */
static int lre_uses_decl(const engine *eng, const th_node *lre, const char *name, Py_ssize_t name_len, int prefixed) {
    if (!prefixed) {
        return !name_is_prefixed(lre->text, lre->text_len);
    }
    if (ns_decl_is_self_prefix(lre, name, name_len)) {
        return 1;
    }
    Py_ssize_t prefix_len = name_len - 6;
    for (Py_ssize_t index = 0; index < lre->attr_count; index++) {
        Py_ssize_t attr_len = 0;
        const char *attr_name = th_attr_name(eng->sheet_tree, lre->attrs[index].name_atom, &attr_len);
        if (attr_len > prefix_len && attr_name[prefix_len] == ':' &&
            memcmp(attr_name, name + 6, (size_t)prefix_len) == 0 && !is_xsl_attr(eng, lre, index)) {
            return 1;
        }
    }
    return 0;
}

static int cache_ns_decl(engine *eng, th_node *lre, th_node *anc, const th_node_attr *attr, const char *name,
                         Py_ssize_t name_len, int prefixed) {
    if ((prefixed ? prefix_excluded(eng, lre, name + 6, name_len - 6) : prefix_excluded(eng, lre, "#default", 8)) &&
        !lre_uses_decl(eng, lre, name, name_len, prefixed)) {
        return 0;
    }
    int overridden = 0;
    for (th_node *nearer = lre; nearer != anc; nearer = nearer->parent) {
        for (Py_ssize_t probe = 0; probe < nearer->attr_count; probe++) {
            if (nearer->attrs[probe].name_atom == attr->name_atom) {
                overridden = 1;
                break;
            }
        }
        if (overridden) {
            break;
        }
    }
    /* A namespace-alias (section 7.1.1) rebinds a stylesheet prefix to its result URI; the
       aliased declaration is emitted even when that URI is the XSLT namespace, which the
       unaliased path drops. */
    Py_ssize_t alias_len = 0;
    const Py_UCS4 *aliased =
        alias_result_uri(eng, prefixed ? name + 6 : "#default", prefixed ? name_len - 6 : 8, &alias_len);
    const Py_UCS4 *value = aliased != NULL ? aliased : attr->value;
    Py_ssize_t value_len = aliased != NULL ? alias_len : attr->value_len;
    if (overridden || (aliased == NULL && ucs4_ascii_eq(attr->value, attr->value_len, XSLT_NS))) {
        return 0;
    }
    if (eng->ns_decls_len == eng->ns_decls_cap) {
        size_t capacity;
        size_t bytes;
        /* GCOVR_EXCL_BR_START: allocation size overflow */
        if (!th_grow_cap(eng->ns_decls_len + 1, eng->ns_decls_cap, 4, sizeof(xslt_ns_decl), &capacity, &bytes)) {
            return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE: allocation size overflow */
        }
        /* GCOVR_EXCL_BR_STOP */
        xslt_ns_decl *grown = PyMem_Realloc(eng->ns_decls, bytes);
        if (grown == NULL) {                   /* GCOVR_EXCL_BR_LINE: alloc */
            return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
        }
        eng->ns_decls = grown;
        eng->ns_decls_cap = capacity;
    }
    eng->ns_decls[eng->ns_decls_len++] = (xslt_ns_decl){name, name_len, value, value_len};
    return 0;
}

static int cache_namespace_decls(engine *eng, th_node *lre) {
    eng->ns_cached_element = NULL;
    eng->ns_decls_len = 0;
    /* Two passes over the in-scope declarations (the walk stops at the attribute-less document
       node): the element's own-prefix binding first, then the rest in document order. */
    for (int self_pass = 1; self_pass >= 0; self_pass--) {
        for (th_node *anc = lre; anc != NULL; anc = anc->parent) {
            for (Py_ssize_t index = 0; index < anc->attr_count; index++) {
                const th_node_attr *attr = &anc->attrs[index];
                Py_ssize_t name_len = 0;
                const char *name = th_attr_name(eng->sheet_tree, attr->name_atom, &name_len);
                int prefixed = name_len > 6 && memcmp(name, "xmlns:", 6) == 0;
                int is_default = name_len == 5 && memcmp(name, "xmlns", 5) == 0;
                if (!prefixed && !is_default) {
                    continue;
                }
                if (ns_decl_is_self_prefix(lre, name, name_len) != self_pass) {
                    continue;
                }
                int rc = cache_ns_decl(eng, lre, anc, attr, name, name_len, prefixed);
                if (rc < 0) {  /* GCOVR_EXCL_BR_LINE: cache_ns_decl only fails on an unforced allocation */
                    return -1; /* GCOVR_EXCL_LINE */
                }
            }
        }
    }
    eng->ns_cached_element = lre;
    return 0;
}

/* The compiled stylesheet owns these spans; output bindings still depend on each result parent. */
static int copy_namespace_decls(engine *eng, th_node *lre, th_node *copy, th_node *out_parent) {
    if (eng->output_method != OUT_XML) {
        return 0;
    }
    if (eng->ns_cached_element != lre) {
        if (cache_namespace_decls(eng, lre) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;                             /* GCOVR_EXCL_LINE */
        }
    }
    for (size_t index = 0; index < eng->ns_decls_len; index++) {
        const xslt_ns_decl *decl = &eng->ns_decls[index];
        int in_scope = output_ns_in_scope(eng, out_parent, decl->name, decl->name_len, decl->value, decl->value_len);
        if (in_scope < 0) {                    /* GCOVR_EXCL_BR_LINE: alloc */
            return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
        }
        if (in_scope) {
            continue;
        }
        /* GCOVR_EXCL_BR_START: alloc */
        if (th_node_attr_set(eng->out_tree, copy, decl->name, decl->name_len, decl->value, decl->value_len, 1) < 0) {
            return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE: alloc */
        }
        /* GCOVR_EXCL_BR_STOP */
    }
    return 0;
}

/* Whether attribute `index` of node carries the stylesheet's XSLT-namespace prefix, i.e. it is an
   XSLT directive (xsl:exclude-result-prefixes, xsl:use-attribute-sets, ...) placed on a literal
   result element, which the spec strips from the output rather than copying. */
static int is_xsl_attr(const engine *eng, const th_node *node, Py_ssize_t index) {
    if (eng->xsl_ns_dynamic) {
        return marks_attr_is_xsl(&eng->marks, node, index);
    }
    Py_ssize_t name_len = 0;
    const char *name = th_attr_name(eng->sheet_tree, node->attrs[index].name_atom, &name_len);
    Py_ssize_t prefix_len = eng->xsl_prefix_len;
    if (name_len <= prefix_len || name[prefix_len] != ':') {
        return 0;
    }
    for (Py_ssize_t index = 0; index < prefix_len; index++) {
        if ((Py_UCS4)(unsigned char)name[index] != eng->xsl_prefix[index]) {
            return 0;
        }
    }
    return 1;
}

/* The value of the XSLT-prefixed attribute `local` on a literal result element (e.g.
   xsl:use-attribute-sets), or NULL when absent. Matches the stylesheet's actual XSLT prefix. */
static const Py_UCS4 *xsl_prefixed_attr(const engine *eng, const th_node *node, const char *local,
                                        Py_ssize_t *out_len) {
    Py_ssize_t local_len = (Py_ssize_t)strlen(local);
    if (!eng->xsl_ns_dynamic) {
        for (Py_ssize_t index = 0; index < node->attr_count; index++) {
            Py_ssize_t name_len = 0;
            const char *name = th_attr_name(eng->sheet_tree, node->attrs[index].name_atom, &name_len);
            if (!is_xsl_attr(eng, node, index)) {
                continue;
            }
            Py_ssize_t offset = eng->xsl_prefix_len + 1;
            if (name_len - offset == local_len && memcmp(name + offset, local, (size_t)local_len) == 0) {
                *out_len = node->attrs[index].value_len;
                return node->attrs[index].value;
            }
        }
        return NULL;
    }
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        Py_ssize_t name_len = 0;
        const char *name = th_attr_name(eng->sheet_tree, node->attrs[index].name_atom, &name_len);
        const char *colon = memchr(name, ':', (size_t)name_len);
        if (colon == NULL || name_len - (colon - name) - 1 != local_len ||
            memcmp(colon + 1, local, (size_t)local_len) != 0) {
            continue;
        }
        if (is_xsl_attr(eng, node, index)) {
            *out_len = node->attrs[index].value_len;
            return node->attrs[index].value;
        }
    }
    return NULL;
}

/* Whether a literal result element that writes no default declaration is in no namespace: unprefixed, with no
   stylesheet default in scope, unless a namespace-alias rebinds the default. Only asked where the output binds a
   default namespace, so a stylesheet default that was not written is that same non-empty binding. */
static int lre_no_namespace(engine *eng, th_node *element) {
    if (name_is_prefixed(element->text, element->text_len)) {
        return 0;
    }
    Py_ssize_t alias_len = 0;
    if (alias_result_uri(eng, "#default", 8, &alias_len) != NULL) {
        return alias_len == 0;
    }
    return sheet_default_decl(eng, element) == NULL;
}

/* The default-namespace state inside a literal result element once its namespace declarations are on the copy. A
   declaration written there sets the state; a no-namespace element without one under a bound default undeclares it. */
static TH_NOINLINE int literal_children_bound(engine *eng, th_node *element, th_node *copy) {
    const th_node_attr *declared = xmlns_decl(eng->out_tree, copy);
    if (declared != NULL) {
        return declared->value_len > 0;
    }
    if (eng->default_bound && lre_no_namespace(eng, element)) {
        if (undeclare_default(eng, copy) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;                          /* GCOVR_EXCL_LINE */
        }
        return 0;
    }
    return eng->default_bound;
}

/* Copy a literal result element into the output, resolving attribute value
   templates, then instantiate its children inside it. */
static int instantiate_literal(engine *eng, th_node *element, th_node *out_parent) {
    uint16_t atom = atom_for_name(element->text, element->text_len);
    th_node *copy = th_tree_make_element(eng->out_tree, element->text, element->text_len, atom, 0);
    if (copy == NULL) {                    /* GCOVR_EXCL_BR_LINE: alloc */
        return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
    }
    if (copy_namespace_decls(eng, element, copy, out_parent) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;                                                  /* GCOVR_EXCL_LINE */
    }
    int bound = eng->default_bound;
    if (bound || copy->attr_count != 0) {
        bound = literal_children_bound(eng, element, copy);
        if (bound < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;   /* GCOVR_EXCL_LINE */
        }
    }
    Py_ssize_t use_len = 0;
    const Py_UCS4 *use = xsl_prefixed_attr(eng, element, "use-attribute-sets", &use_len);
    if (use != NULL && apply_attribute_sets(eng, use, use_len, copy) < 0) {
        return -1;
    }
    for (Py_ssize_t index = 0; index < element->attr_count; index++) {
        TH_WORK_STEP();
        const th_node_attr *attr = &element->attrs[index];
        Py_ssize_t name_len = 0;
        const char *name = th_attr_name(eng->sheet_tree, attr->name_atom, &name_len);
        /* Namespace declarations are handled above; XSLT directives never reach the output. */
        if (name_len >= 6 && memcmp(name, "xmlns:", 6) == 0) {
            continue;
        }
        if (name_len == 5 && memcmp(name, "xmlns", 5) == 0) {
            continue;
        }
        if (is_xsl_attr(eng, element, index)) {
            continue;
        }
        Py_UCS4 *resolved;
        Py_ssize_t resolved_len = 0;
        /* An XML-parsed stylesheet never carries a valueless attribute, so the value is
           the template to resolve; a NULL value (length 0) resolves to the empty string. */
        if (eval_avt(eng, attr->value, attr->value_len, &resolved, &resolved_len) < 0) {
            return -1;
        }
        int rc = th_node_attr_set(eng->out_tree, copy, name, name_len, resolved, resolved_len, 1);
        PyMem_Free(resolved);
        if (rc < 0) {                          /* GCOVR_EXCL_BR_LINE: alloc */
            return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
        }
    }
    th_node_append_child(out_parent, copy);
    return instantiate_under(eng, element, copy, bound, NULL);
}

/* Whether an element is an extension element: its name carries a prefix declared in the
   stylesheet's extension-element-prefixes. Such an element is not a literal result element;
   an unsupported one runs its xsl:fallback children instead (section 14.1, 15). */
static int is_extension_element(const engine *eng, const th_node *node) {
    if (eng->ext_prefixes == NULL) {
        return 0;
    }
    Py_ssize_t colon = -1;
    for (Py_ssize_t index = 0; index < node->text_len; index++) {
        if (node->text[index] == ':') {
            colon = index;
            break;
        }
    }
    if (colon < 0) {
        return 0;
    }
    return name_in_token_list(eng->ext_prefixes, eng->ext_prefixes_len, node->text, colon);
}

/* Run an unknown extension element's xsl:fallback children (section 15): turbohtml does not
   dispatch extension elements, so their declared fallback is the instantiated result. */
static int instantiate_fallback(engine *eng, th_node *node, th_node *out_parent) {
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        if (is_xsl(eng, child, "fallback")) {
            if (instantiate_body(eng, child, out_parent) < 0) {
                return -1;
            }
        }
    }
    return 0;
}

/* The kinds a stylesheet element is classified into once, at compile time: the xsl:* instructions
   instantiate_classified dispatches on, a literal result element, and an extension element. XSL_PARAM, XSL_SORT,
   XSL_WITH_PARAM and XSL_WHEN are named so precompile_instruction and the body walk can tell them apart, and
   XSL_OTHER covers every other xsl:* element (fallback, apply-imports, ...) the core does not model; none of them
   instantiates anything. */
enum xsl_instr {
    XSL_OTHER,
    XSL_PARAM,
    XSL_SORT,
    XSL_WITH_PARAM,
    XSL_WHEN,
    XSL_LITERAL,
    XSL_EXTENSION,
    XSL_VALUE_OF,
    XSL_APPLY_TEMPLATES,
    XSL_CALL_TEMPLATE,
    XSL_FOR_EACH,
    XSL_IF,
    XSL_CHOOSE,
    XSL_TEXT,
    XSL_ELEMENT,
    XSL_ATTRIBUTE,
    XSL_COPY,
    XSL_COPY_OF,
    XSL_VARIABLE,
    XSL_NUMBER,
    XSL_COMMENT,
    XSL_PI,
    XSL_MESSAGE,
};

/* Classify an xsl:* element's local name into its instruction id in one pass. The caller
   has confirmed the xsl namespace (is_any_xsl), so only the local name is matched -- the
   prefix is not re-tested per candidate as a chain of is_xsl calls would. */
static enum xsl_instr xsl_classify(const Py_UCS4 *local, Py_ssize_t len) {
    if (ucs4_ascii_eq(local, len, "value-of")) {
        return XSL_VALUE_OF;
    }
    if (ucs4_ascii_eq(local, len, "apply-templates")) {
        return XSL_APPLY_TEMPLATES;
    }
    if (ucs4_ascii_eq(local, len, "call-template")) {
        return XSL_CALL_TEMPLATE;
    }
    if (ucs4_ascii_eq(local, len, "for-each")) {
        return XSL_FOR_EACH;
    }
    if (ucs4_ascii_eq(local, len, "if")) {
        return XSL_IF;
    }
    if (ucs4_ascii_eq(local, len, "choose")) {
        return XSL_CHOOSE;
    }
    if (ucs4_ascii_eq(local, len, "text")) {
        return XSL_TEXT;
    }
    if (ucs4_ascii_eq(local, len, "element")) {
        return XSL_ELEMENT;
    }
    if (ucs4_ascii_eq(local, len, "attribute")) {
        return XSL_ATTRIBUTE;
    }
    if (ucs4_ascii_eq(local, len, "copy")) {
        return XSL_COPY;
    }
    if (ucs4_ascii_eq(local, len, "copy-of")) {
        return XSL_COPY_OF;
    }
    if (ucs4_ascii_eq(local, len, "variable")) {
        return XSL_VARIABLE;
    }
    if (ucs4_ascii_eq(local, len, "number")) {
        return XSL_NUMBER;
    }
    if (ucs4_ascii_eq(local, len, "comment")) {
        return XSL_COMMENT;
    }
    if (ucs4_ascii_eq(local, len, "processing-instruction")) {
        return XSL_PI;
    }
    if (ucs4_ascii_eq(local, len, "message")) {
        return XSL_MESSAGE;
    }
    if (ucs4_ascii_eq(local, len, "param")) {
        return XSL_PARAM;
    }
    if (ucs4_ascii_eq(local, len, "sort")) {
        return XSL_SORT;
    }
    if (ucs4_ascii_eq(local, len, "with-param")) {
        return XSL_WITH_PARAM;
    }
    if (ucs4_ascii_eq(local, len, "when")) {
        return XSL_WHEN;
    }
    return XSL_OTHER;
}

static int emit_literal_text(engine *eng, th_node *out_parent, const th_node *literal) {
    if (literal->text_len == 0) {
        return 0;
    }
    th_node *node = th_tree_make_data_node(eng->out_tree, TH_NODE_TEXT, NULL, 0);
    if (node == NULL) {                    /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
        return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
    }
    /* The compiled snapshot outlives serialization and result-tree cleanup. */
    node->text = literal->text;
    node->text_len = literal->text_len;
    th_node_append_child(out_parent, node);
    return 0;
}

static int instantiate_non_element(engine *eng, th_node *node, th_node *out_parent) {
    if (node->type == TH_NODE_TEXT) {
        Py_ssize_t text_len = node->text_len;
        const Py_UCS4 *text = th_node_realize_text(eng->sheet_tree, node);
        if (text == NULL && text_len != 0) {   /* GCOVR_EXCL_BR_LINE: alloc */
            return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
        }
        return ucs4_blank(text, text_len) ? 0 : emit_literal_text(eng, out_parent, node);
    }
    if (node->type == TH_NODE_CDATA) {
        /* A CDATA section in the stylesheet is significant character data (never stripped as
           whitespace); it emits as text, which cdata-section-elements may later re-wrap. */
        Py_ssize_t text_len = node->text_len;
        const Py_UCS4 *text = th_node_realize_text(eng->sheet_tree, node);
        if (text == NULL && text_len != 0) {   /* GCOVR_EXCL_BR_LINE: alloc */
            return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
        }
        return emit_literal_text(eng, out_parent, node);
    }
    return 0;
}

/* The kind of a stylesheet element as a run instantiates it: an xsl:* instruction by its local name, an extension
   element, or a literal result element. Recorded once by precompile_stylesheet. */
static enum xsl_instr element_kind(const engine *eng, const th_node *node) {
    if (eng->xsl_ns_dynamic ? is_any_xsl_dynamic(eng, node) : is_any_xsl_fast(eng, node)) {
        Py_ssize_t local_len;
        Py_ssize_t prefix_len;
        const Py_UCS4 *local = qname_local(node, &local_len, &prefix_len);
        return xsl_classify(local, local_len);
    }
    return is_extension_element(eng, node) ? XSL_EXTENSION : XSL_LITERAL;
}

/* Every xsl:* instruction dispatches through this frame, so a recursive template repeats it once per nesting level.
   Scratch arrays stay out of it: do_number and sort_selection are TH_NOINLINE, and the compile error text lives in the
   engine. */
static int instantiate_classified(engine *eng, th_node *node, th_node *out_parent, enum xsl_instr instruction) {
    switch (instruction) {
    case XSL_VALUE_OF:
        return do_value_of(eng, node, out_parent);
    case XSL_APPLY_TEMPLATES:
        return apply_templates(eng, node, out_parent, NULL, 0);
    case XSL_CALL_TEMPLATE:
        return do_call_template(eng, node, out_parent);
    case XSL_FOR_EACH:
        return do_for_each(eng, node, out_parent);
    case XSL_IF: {
        int truth;
        if (eval_test(eng, node, &truth) < 0) {
            return -1;
        }
        return truth ? instantiate_body(eng, node, out_parent) : 0;
    }
    case XSL_CHOOSE:
        for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
            if (is_xsl(eng, child, "when")) {
                int truth;
                if (eval_test(eng, child, &truth) < 0) {
                    return -1;
                }
                if (truth) {
                    return instantiate_body(eng, child, out_parent);
                }
            } else if (is_xsl(eng, child, "otherwise")) {
                return instantiate_body(eng, child, out_parent);
            }
        }
        return 0;
    case XSL_TEXT: {
        for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
            if (child->type == TH_NODE_TEXT) {
                Py_ssize_t text_len = child->text_len;
                const Py_UCS4 *text = th_node_realize_text(eng->sheet_tree, child);
                if (text == NULL && text_len != 0) {   /* GCOVR_EXCL_BR_LINE: alloc */
                    return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
                }
                int rc = emit_literal_text(eng, out_parent, child);
                if (rc < 0) {  /* GCOVR_EXCL_BR_LINE: alloc */
                    return rc; /* GCOVR_EXCL_LINE */
                }
            }
        }
        return 0;
    }
    case XSL_ELEMENT:
        return do_element(eng, node, out_parent);
    case XSL_ATTRIBUTE:
        return do_attribute(eng, node, out_parent);
    case XSL_COPY:
        return do_copy(eng, node, out_parent);
    case XSL_COPY_OF:
        return do_copy_of(eng, node, out_parent);
    case XSL_VARIABLE: {
        Py_ssize_t name_len = 0;
        const Py_UCS4 *name = attr_lookup(eng->sheet_tree, node, "name", 4, &name_len);
        if (name == NULL) {
            return fail(eng, "xsl:variable requires a name attribute");
        }
        xp_result value;
        th_node *rtf;
        if (compute_binding(eng, node, &value, &rtf) < 0) {
            return -1;
        }
        if (scope_push(eng, name, name_len, value, rtf) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            return fail(eng, "out of memory");                 /* GCOVR_EXCL_LINE */
        }
        return 0;
    }
    case XSL_NUMBER:
        return do_number(eng, node, out_parent);
    case XSL_COMMENT:
        return do_comment(eng, node, out_parent);
    case XSL_PI:
        return do_pi(eng, node, out_parent);
    case XSL_MESSAGE: {
        Py_ssize_t terminate_len = 0;
        const Py_UCS4 *terminate = attr_lookup(eng->sheet_tree, node, "terminate", 9, &terminate_len);
        if (terminate != NULL && ucs4_ascii_eq(terminate, terminate_len, "yes")) {
            Py_UCS4 *text;
            Py_ssize_t text_len = 0;
            if (instantiate_string(eng, node, &text, &text_len) < 0) {
                return -1;
            }
            PyObject *message = make_str(text, text_len);
            PyMem_Free(text);
            if (message == NULL) {   /* GCOVR_EXCL_BR_LINE: alloc */
                return fail_py(eng); /* GCOVR_EXCL_LINE */
            }
            PyErr_Format(PyExc_RuntimeError, "xsl:message terminate: %U", message);
            Py_DECREF(message);
            return fail_py(eng);
        }
        return 0; /* a non-terminating message is discarded */
    }
    default:
        /* param and sort are read by their parent; anything else the core does not model
           (with-param, fallback, apply-imports, ...) instantiates nothing. */
        return 0;
    }
}

/* The kind precompile_stylesheet recorded for a stylesheet element. */
static int instruction_kind(engine *eng, const th_node *node, enum xsl_instr *kind) {
    Py_ssize_t slot = th_node_map_find(&eng->instructions, node);
    if (slot == 0) { /* GCOVR_EXCL_BR_LINE: precompile_stylesheet visits every element a run instantiates */
        return fail(eng, "xslt: stylesheet instruction was not compiled"); /* GCOVR_EXCL_LINE */
    }
    *kind = (enum xsl_instr)(slot - 1);
    return 0;
}

/* Instantiate one child of a body or the root (an xsl:* element, a literal result element, an extension element, or a
   text node) into out_parent. */
static int instantiate_child(engine *eng, th_node *node, enum xsl_instr kind, th_node *out_parent) {
    if (node->type != TH_NODE_ELEMENT) {
        return instantiate_non_element(eng, node, out_parent);
    }
    switch (kind) {
    case XSL_LITERAL:
        return instantiate_literal(eng, node, out_parent);
    case XSL_EXTENSION:
        return instantiate_fallback(eng, node, out_parent);
    default:
        return instantiate_classified(eng, node, out_parent, kind);
    }
}

static int instantiate_root(engine *eng, th_node *root, th_node *out_parent) {
    enum xsl_instr kind;
    if (instruction_kind(eng, root, &kind) < 0) { /* GCOVR_EXCL_LINE */
        return -1;                                /* GCOVR_EXCL_LINE */
    }
    return instantiate_child(eng, root, kind, out_parent);
}

/* Instantiate every child of `body`, except the param and sort declarations its parent reads. */
static int instantiate_body(engine *eng, th_node *body, th_node *out_parent) {
    if (++eng->depth > XSLT_STACK_CHECK_DEPTH && enter_deep_level(eng, XSLT_LEVEL_TEMPLATE, NULL) < 0) {
        return -1;
    }
    Py_ssize_t scope_mark = eng->scope_len;
    int rc = 0;
    for (th_node *child = body->first_child; child != NULL && rc == 0; child = child->next_sibling) {
        TH_WORK_STEP();
        enum xsl_instr kind = XSL_OTHER;
        if (child->type == TH_NODE_ELEMENT) {
            if (instruction_kind(eng, child, &kind) < 0) { /* GCOVR_EXCL_LINE */
                rc = -1;                                   /* GCOVR_EXCL_LINE */
                break;                                     /* GCOVR_EXCL_LINE */
            }
            if (kind == XSL_PARAM || kind == XSL_SORT) {
                continue;
            }
        }
#ifdef TH_OPERATION_LIMIT
        if ((rc = charge_operation(eng, "applying the stylesheet")) < 0) {
            break;
        }
#endif
        rc = instantiate_child(eng, child, kind, out_parent);
    }
    scope_drop(eng, scope_mark); /* local xsl:variable bindings fall out of scope */
    eng->depth--;
    return rc;
}

/* ---- stylesheet parsing --------------------------------------------------- */

static int push_rule(engine *eng, xslt_rule rule) {
    if (eng->nrules == eng->rules_cap) {
        Py_ssize_t cap = eng->rules_cap == 0 ? TH_INITIAL_CAPACITY(16) : eng->rules_cap * 2;
        xslt_rule *grown = PyMem_Realloc(eng->rules, (size_t)cap * sizeof(xslt_rule));
        if (grown == NULL) {                   /* GCOVR_EXCL_BR_LINE: alloc */
            return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
        }
        eng->rules = grown;
        eng->rules_cap = cap;
    }
    eng->rules[eng->nrules++] = rule;
    return 0;
}

static int parse_template(engine *eng, th_node *element, int *position) {
    Py_ssize_t name_len = 0;
    const Py_UCS4 *name = attr_lookup(eng->sheet_tree, element, "name", 4, &name_len);
    if (name != NULL) {
        if (eng->nnamed == eng->named_cap) {
            Py_ssize_t cap = eng->named_cap == 0 ? TH_INITIAL_CAPACITY(8) : eng->named_cap * 2;
            xslt_named *grown = PyMem_Realloc(eng->named, (size_t)cap * sizeof(xslt_named));
            if (grown == NULL) {                   /* GCOVR_EXCL_BR_LINE: alloc */
                return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
            }
            eng->named = grown;
            eng->named_cap = cap;
        }
        eng->named[eng->nnamed].name = (Py_UCS4 *)name;
        eng->named[eng->nnamed].name_len = name_len;
        eng->named[eng->nnamed].body = element;
        eng->nnamed++;
    }
    Py_ssize_t match_len = 0;
    const Py_UCS4 *match = attr_lookup(eng->sheet_tree, element, "match", 5, &match_len);
    if (match == NULL) {
        return 0;
    }
    Py_ssize_t mode_len = 0;
    const Py_UCS4 *mode = attr_lookup(eng->sheet_tree, element, "mode", 4, &mode_len);
    Py_ssize_t priority_len = 0;
    const Py_UCS4 *priority = attr_lookup(eng->sheet_tree, element, "priority", 8, &priority_len);
    int has_priority = priority != NULL;
    double explicit_priority = has_priority ? parse_number(priority, priority_len) : 0;
    Py_ssize_t starts[64];
    Py_ssize_t lens[64];
    int alternatives = split_union(match, match_len, starts, lens, 64);
    if (alternatives < 0) {
        return fail(eng, "xslt: match pattern has too many alternatives");
    }
    for (int index = 0; index < alternatives; index++) {
        xp_program *prog = compile_pattern(eng, match + starts[index], lens[index]);
        if (prog == NULL) {
            return -1;
        }
        xslt_rule rule = {0};
        rule.pattern = (Py_UCS4 *)(match + starts[index]);
        rule.pattern_len = lens[index];
        rule.prog = prog;
        rule.priority = has_priority ? explicit_priority : default_priority(match + starts[index], lens[index]);
        rule.position = (*position)++;
        rule.precedence = eng->precedence;
        rule.body = element;
        rule.mode = (Py_UCS4 *)mode;
        rule.mode_len = mode_len;
        if (push_rule(eng, rule) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;                  /* GCOVR_EXCL_LINE */
        }
    }
    return 0;
}

static int parse_key(engine *eng, th_node *element) {
    Py_ssize_t name_len = 0;
    const Py_UCS4 *name = attr_lookup(eng->sheet_tree, element, "name", 4, &name_len);
    Py_ssize_t match_len = 0;
    const Py_UCS4 *match = attr_lookup(eng->sheet_tree, element, "match", 5, &match_len);
    Py_ssize_t use_len = 0;
    const Py_UCS4 *use = attr_lookup(eng->sheet_tree, element, "use", 3, &use_len);
    if (name == NULL || match == NULL || use == NULL) {
        return fail(eng, "xsl:key requires name, match and use attributes");
    }
    xp_program *match_prog = compile_pattern(eng, match, match_len);
    if (match_prog == NULL) {
        return -1;
    }
    char errbuf[256];
    xp_program *use_prog = compile_expression(eng, use, use_len, errbuf, sizeof(errbuf));
    if (use_prog == NULL) {
        if (!PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: only allocation sets an exception during compilation */
            PyErr_Format(PyExc_ValueError, "xslt: bad key use expression: %s", errbuf);
        }
        return fail_py(eng);
    }
    if (bind_expression_scope(eng, use_prog, element) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return fail_py(eng);                                 /* GCOVR_EXCL_LINE */
    }
    if (eng->nkeys == eng->keys_cap) {
        Py_ssize_t cap = eng->keys_cap == 0 ? TH_INITIAL_CAPACITY(4) : eng->keys_cap * 2;
        xslt_key *grown = PyMem_Realloc(eng->keys, (size_t)cap * sizeof(xslt_key));
        if (grown == NULL) {                   /* GCOVR_EXCL_BR_LINE: alloc */
            return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
        }
        eng->keys = grown;
        eng->keys_cap = cap;
    }
    xslt_key key = {0};
    key.name = (Py_UCS4 *)name;
    key.name_len = name_len;
    key.instruction = element;
    key.match_prog = match_prog;
    key.use_prog = use_prog;
    eng->keys[eng->nkeys++] = key;
    return 0;
}

static int parse_attrset(engine *eng, th_node *element) {
    Py_ssize_t name_len = 0;
    const Py_UCS4 *name = attr_lookup(eng->sheet_tree, element, "name", 4, &name_len);
    if (name == NULL) {
        return fail(eng, "xsl:attribute-set requires a name attribute");
    }
    if (eng->nattrsets == eng->attrsets_cap) {
        Py_ssize_t cap = eng->attrsets_cap == 0 ? TH_INITIAL_CAPACITY(8) : eng->attrsets_cap * 2;
        xslt_attrset *grown = PyMem_Realloc(eng->attrsets, (size_t)cap * sizeof(xslt_attrset));
        if (grown == NULL) {                   /* GCOVR_EXCL_BR_LINE: alloc */
            return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
        }
        eng->attrsets = grown;
        eng->attrsets_cap = cap;
    }
    xslt_attrset *set = &eng->attrsets[eng->nattrsets++];
    set->name = name;
    set->name_len = name_len;
    set->body = element;
    set->precedence = eng->precedence;
    return 0;
}

static int raise_attrset_cycle(engine *eng, const Py_ssize_t *path, int depth, Py_ssize_t used) {
    int start = depth;
    while (path[start] != used) {
        start--;
    }
    xb chain = {0};
    for (int index = start; index <= depth; index++) {
        const xslt_attrset *set = &eng->attrsets[path[index]];
        /* GCOVR_EXCL_BR_START: allocation failure */
        if (xb_add(&chain, set->name, set->name_len) < 0 || xb_add_ascii(&chain, " -> ") < 0) {
            xb_free(&chain);     /* GCOVR_EXCL_LINE */
            return fail_py(eng); /* GCOVR_EXCL_LINE */
        }
        /* GCOVR_EXCL_BR_STOP */
    }
    const xslt_attrset *closing = &eng->attrsets[used];
    /* GCOVR_EXCL_BR_START: allocation failure */
    PyObject *text = xb_add(&chain, closing->name, closing->name_len) < 0 ? NULL : make_str(chain.data, chain.len);
    /* GCOVR_EXCL_BR_STOP */
    xb_free(&chain);
    if (text != NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        PyErr_Format(PyExc_ValueError,
                     "circular xsl:attribute-set reference: %U; remove one of these use-attribute-sets references",
                     text);
        Py_DECREF(text);
    }
    return fail_py(eng);
}

/* Section 7.1.4 forbids a set that uses itself, even one no template applies; the chain length this returns keeps
   order_attrset within XSLT_MAX_DEPTH. A definition is checked with the earlier ones of its name, as the one set they
   merge into, and the walk stops at an earlier one already checked, whose height covers the rest of the chain;
   heights[slot] is 0 unvisited, -1 while on the path, else the chain length. */
static int check_attrset(engine *eng, Py_ssize_t slot, int *heights, Py_ssize_t *path, int depth) {
    if (depth == XSLT_MAX_DEPTH) {
        return raise_attrset_depth(eng, &eng->attrsets[path[0]]);
    }
    path[depth] = slot;
    heights[slot] = -1;
    int height = 1;
    for (Py_ssize_t definition = slot; definition >= 0; definition = attrset_before(eng, definition)) {
        if (definition != slot && heights[definition] < 0) {
            /* An earlier definition still on the path: its uses lead back to its own name. */
            return raise_attrset_cycle(eng, path, depth - 1, definition);
        }
        if (definition != slot && heights[definition] > 0) {
            height = heights[definition] > height ? heights[definition] : height;
            break;
        }
        const xslt_attrset *set = &eng->attrsets[definition];
        for (Py_ssize_t index = set->uses_start; index < set->uses_end; index++) {
            Py_ssize_t used = eng->attrset_uses.items[index];
            if (heights[used] < 0) {
                return raise_attrset_cycle(eng, path, depth, used);
            }
            int used_height = heights[used] != 0 ? heights[used] : check_attrset(eng, used, heights, path, depth + 1);
            if (used_height < 0) {
                return -1;
            }
            height = used_height + 1 > height ? used_height + 1 : height;
        }
    }
    if (height > XSLT_MAX_DEPTH) {
        return raise_attrset_depth(eng, &eng->attrsets[slot]);
    }
    heights[slot] = height;
    return height;
}

/* Resolving names to slots once lets applying a set walk indexes instead of parsing names. */
static int resolve_attribute_sets(engine *eng) {
    if (eng->nattrsets == 0) {
        return 0;
    }
    for (Py_ssize_t slot = 0; slot < eng->nattrsets; slot++) {
        xslt_attrset *set = &eng->attrsets[slot];
        Py_ssize_t chain_len = 0;
        const Py_UCS4 *chain = attr_lookup(eng->sheet_tree, set->body, "use-attribute-sets", 18, &chain_len);
        set->uses_start = eng->attrset_uses.len;
        if (chain != NULL && attrsets_named(eng, chain, chain_len, &eng->attrset_uses) < 0) { /* GCOVR_EXCL_BR_LINE */
            return -1; /* GCOVR_EXCL_LINE: allocation failure */
        }
        set->uses_end = eng->attrset_uses.len;
    }
    int *heights = PyMem_Calloc((size_t)eng->nattrsets, sizeof(int));
    if (heights == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;         /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t path[XSLT_MAX_DEPTH];
    int height = 0;
    for (Py_ssize_t slot = 0; height >= 0 && slot < eng->nattrsets; slot++) {
        const xslt_attrset *set = &eng->attrsets[slot];
        if (heights[slot] == 0 && set->uses_start == set->uses_end && attrset_before(eng, slot) < 0) {
            heights[slot] = 1;
        }
        height = heights[slot] != 0 ? heights[slot] : check_attrset(eng, slot, heights, path, 0);
    }
    PyMem_Free(heights);
    return height < 0 ? -1 : 0;
}

/* The namespace URI bound to prefix on the stylesheet root ("#default" resolves the default
   namespace), or NULL when the prefix is not declared. */
static const Py_UCS4 *resolve_prefix_uri(engine *eng, th_node *root, const Py_UCS4 *prefix, Py_ssize_t prefix_len,
                                         Py_ssize_t *out_len) {
    int is_default = ucs4_ascii_eq(prefix, prefix_len, "#default"); /* the helper length-checks internally */
    for (Py_ssize_t index = 0; index < root->attr_count; index++) {
        Py_ssize_t name_len = 0;
        const char *name = th_attr_name(eng->sheet_tree, root->attrs[index].name_atom, &name_len);
        if (is_default) {
            if (name_len == 5 && memcmp(name, "xmlns", 5) == 0) {
                *out_len = root->attrs[index].value_len;
                return root->attrs[index].value;
            }
            continue;
        }
        if (name_len == prefix_len + 6 && memcmp(name, "xmlns:", 6) == 0) {
            Py_ssize_t offset = 0;
            while (offset < prefix_len && (Py_UCS4)(unsigned char)name[6 + offset] == prefix[offset]) {
                offset++;
            }
            if (offset == prefix_len) {
                *out_len = root->attrs[index].value_len;
                return root->attrs[index].value;
            }
        }
    }
    return NULL;
}

static int parse_namespace_alias(engine *eng, th_node *root, th_node *element) {
    Py_ssize_t style_len = 0;
    const Py_UCS4 *style = attr_lookup(eng->sheet_tree, element, "stylesheet-prefix", 17, &style_len);
    Py_ssize_t result_len = 0;
    const Py_UCS4 *result = attr_lookup(eng->sheet_tree, element, "result-prefix", 13, &result_len);
    if (style == NULL || result == NULL) {
        return fail(eng, "xsl:namespace-alias requires stylesheet-prefix and result-prefix");
    }
    Py_ssize_t uri_len = 0;
    const Py_UCS4 *uri = resolve_prefix_uri(eng, root, result, result_len, &uri_len);
    if (uri == NULL) {
        return fail(eng, "xsl:namespace-alias result-prefix is not a declared namespace");
    }
    if (eng->naliases == eng->aliases_cap) {
        Py_ssize_t cap = eng->aliases_cap == 0 ? TH_INITIAL_CAPACITY(4) : eng->aliases_cap * 2;
        xslt_nsalias *grown = PyMem_Realloc(eng->aliases, (size_t)cap * sizeof(xslt_nsalias));
        if (grown == NULL) {                   /* GCOVR_EXCL_BR_LINE: alloc */
            return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
        }
        eng->aliases = grown;
        eng->aliases_cap = cap;
    }
    xslt_nsalias *alias = &eng->aliases[eng->naliases++];
    alias->style_prefix = style;
    alias->style_prefix_len = style_len;
    alias->result_prefix = result;
    alias->result_prefix_len = result_len;
    alias->result_uri = uri;
    alias->result_uri_len = uri_len;
    alias->precedence = eng->precedence;
    return 0;
}

/* The result namespace URI a namespace-alias remaps a stylesheet prefix to, or NULL when the
   prefix is not aliased. "#default" names the default-namespace alias; prefix is an ASCII run. */
static const Py_UCS4 *alias_result_uri(const engine *eng, const char *prefix, Py_ssize_t prefix_len,
                                       Py_ssize_t *out_len) {
    for (Py_ssize_t index = 0; index < eng->naliases; index++) {
        const xslt_nsalias *alias = &eng->aliases[index];
        if (alias->style_prefix_len != prefix_len) {
            continue;
        }
        Py_ssize_t offset = 0;
        while (offset < prefix_len && alias->style_prefix[offset] == (Py_UCS4)(unsigned char)prefix[offset]) {
            offset++;
        }
        if (offset == prefix_len) {
            *out_len = alias->result_uri_len;
            return alias->result_uri;
        }
    }
    return NULL;
}

static int push_global(engine *eng, const Py_UCS4 *name, Py_ssize_t name_len, th_node *node, int is_param) {
    if (eng->nglobals == eng->globals_cap) {
        Py_ssize_t cap = eng->globals_cap == 0 ? TH_INITIAL_CAPACITY(8) : eng->globals_cap * 2;
        xslt_global *grown = PyMem_Realloc(eng->globals, (size_t)cap * sizeof(xslt_global));
        if (grown == NULL) {                   /* GCOVR_EXCL_BR_LINE: alloc */
            return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
        }
        eng->globals = grown;
        eng->globals_cap = cap;
    }
    eng->globals[eng->nglobals].name = (Py_UCS4 *)name;
    eng->globals[eng->nglobals].name_len = name_len;
    eng->globals[eng->nglobals].node = node;
    eng->globals[eng->nglobals].is_param = is_param;
    eng->nglobals++;
    return 0;
}

/* The section 3.4 specificity of a strip/preserve element name test, matching the default
   template priority of that NameTest: -0.5 for "*", -0.25 for a prefixed wildcard "ns:*",
   0 for a QName. */
static double space_specificity(const Py_UCS4 *name, Py_ssize_t name_len) {
    if (name_len == 1 && name[0] == '*') {
        return -0.5;
    }
    if (name_len >= 2 && name[name_len - 1] == '*' && name[name_len - 2] == ':') {
        return -0.25;
    }
    return 0;
}

/* Record every NameTest of an xsl:strip-space (strip=1) or xsl:preserve-space (strip=0)
   element as a space entry with its specificity and the current import precedence. */
static int parse_space(engine *eng, th_node *element, int strip) {
    Py_ssize_t list_len = 0;
    const Py_UCS4 *list = attr_lookup(eng->sheet_tree, element, "elements", 8, &list_len);
    if (list == NULL) {
        return fail(eng, "xsl:strip-space/xsl:preserve-space requires an elements attribute");
    }
    Py_ssize_t index = 0;
    while (index < list_len) {
        while (index < list_len && ucs4_is_ws(list[index])) {
            index++;
        }
        Py_ssize_t start = index;
        while (index < list_len && !ucs4_is_ws(list[index])) {
            index++;
        }
        if (index == start) {
            break;
        }
        if (eng->nspaces == eng->spaces_cap) {
            Py_ssize_t cap = eng->spaces_cap == 0 ? TH_INITIAL_CAPACITY(8) : eng->spaces_cap * 2;
            xslt_space *grown = PyMem_Realloc(eng->spaces, (size_t)cap * sizeof(xslt_space));
            if (grown == NULL) {                   /* GCOVR_EXCL_BR_LINE: alloc */
                return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
            }
            eng->spaces = grown;
            eng->spaces_cap = cap;
        }
        xslt_space *entry = &eng->spaces[eng->nspaces++];
        entry->name = list + start;
        entry->name_len = index - start;
        entry->strip = strip;
        entry->specificity = space_specificity(entry->name, entry->name_len);
        entry->precedence = eng->precedence;
    }
    return 0;
}

static void parse_output(engine *eng, th_node *element) {
    Py_ssize_t method_len = 0;
    const Py_UCS4 *method = attr_lookup(eng->sheet_tree, element, "method", 6, &method_len);
    if (method != NULL) {
        eng->method_seen = 1;
        if (ucs4_ascii_eq(method, method_len, "html")) {
            eng->output_method = OUT_HTML;
        } else if (ucs4_ascii_eq(method, method_len, "text")) {
            eng->output_method = OUT_TEXT;
        } else {
            eng->output_method = OUT_XML;
        }
    }
    Py_ssize_t omit_len = 0;
    const Py_UCS4 *omit = attr_lookup(eng->sheet_tree, element, "omit-xml-declaration", 20, &omit_len);
    if (omit != NULL && ucs4_ascii_eq(omit, omit_len, "yes")) {
        eng->omit_xml_decl = 1;
    }
    Py_ssize_t cdata_len = 0;
    const Py_UCS4 *cdata = attr_lookup(eng->sheet_tree, element, "cdata-section-elements", 22, &cdata_len);
    if (cdata != NULL) {
        eng->cdata_elements = cdata;
        eng->cdata_elements_len = cdata_len;
    }
}

static int marks_declares_xsl(const xslt_number_index *scope, th_tree *tree, const char *key, Py_ssize_t len) {
    uint32_t atom = th_attr_lookup(tree, key, len);
    if (atom >= scope->binding_capacity) {
        return 0;
    }
    const th_node_attr *attr = scope->bindings[atom].attr;
    return attr != NULL && ucs4_ascii_eq(attr->value, attr->value_len, XSLT_NS);
}

/* Whether the element's own prefix binds the XSLT namespace. A declared name is UTF-8, so a prefix code point above
   0xFF never matches one; each lower code point is compared as one byte. */
static int marks_element_flag(xslt_number_index *scope, th_tree *tree, const th_node *element) {
    Py_ssize_t local_len;
    Py_ssize_t prefix_len;
    (void)qname_local(element, &local_len, &prefix_len);
    if (number_prefix_reserve(scope, (size_t)prefix_len + 6) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                                                  /* GCOVR_EXCL_LINE */
    }
    for (Py_ssize_t index = 0; index < prefix_len; index++) {
        if (element->text[index] > 0xFF) {
            return 0;
        }
        scope->prefix[index + 6] = (char)element->text[index];
    }
    return marks_declares_xsl(scope, tree, scope->prefix, prefix_len == 0 ? 5 : prefix_len + 6);
}

static int marks_attr_flag(xslt_number_index *scope, th_tree *tree, const th_node_attr *attr) {
    Py_ssize_t name_len = 0;
    const char *name = th_attr_name(tree, attr->name_atom, &name_len);
    const char *colon = memchr(name, ':', (size_t)name_len);
    if (colon == NULL) {
        return 0;
    }
    Py_ssize_t prefix_len = colon - name;
    if (number_prefix_reserve(scope, (size_t)prefix_len + 6) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                                                  /* GCOVR_EXCL_LINE */
    }
    memcpy(scope->prefix + 6, name, (size_t)prefix_len);
    return marks_declares_xsl(scope, tree, scope->prefix, prefix_len + 6);
}

static int marks_record(xslt_marks *marks, xslt_number_index *scope, th_tree *tree, const th_node *element) {
    int element_xsl = marks_element_flag(scope, tree, element);
    if (element_xsl < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;         /* GCOVR_EXCL_LINE */
    }
    size_t needed = (size_t)element->attr_count + 1;
    if (marks->flag_count + needed > marks->flag_capacity) {
        size_t capacity;
        size_t bytes;
        /* GCOVR_EXCL_BR_START: allocation failure */
        if (!th_grow_cap(marks->flag_count + needed, marks->flag_capacity, 64, sizeof(*marks->flags), &capacity,
                         &bytes)) {
            return -1; /* GCOVR_EXCL_LINE */
        }
        uint8_t *grown = PyMem_Realloc(marks->flags, bytes);
        if (grown == NULL) {
            return -1; /* GCOVR_EXCL_LINE */
        }
        /* GCOVR_EXCL_BR_STOP */
        marks->flags = grown;
        marks->flag_capacity = capacity;
    }
    size_t offset = marks->flag_count;
    uint8_t any = (uint8_t)element_xsl;
    marks->flags[offset] = any;
    for (Py_ssize_t index = 0; index < element->attr_count; index++) {
        int attr_xsl = marks_attr_flag(scope, tree, &element->attrs[index]);
        if (attr_xsl < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;      /* GCOVR_EXCL_LINE */
        }
        marks->flags[offset + 1 + index] = (uint8_t)attr_xsl;
        any |= (uint8_t)attr_xsl;
    }
    if (any == 0) {
        return 0;
    }
    marks->flag_count += needed;
    return th_node_map_insert(&marks->slots, element, (Py_ssize_t)offset + 1); /* GCOVR_EXCL_LINE */
}

/* A root nested in a larger document inherits its ancestors' declarations, which are in scope for its whole subtree. */
static int marks_enter_ancestors(xslt_number_index *scope, th_tree *tree, th_node *root) {
    size_t depth = 0;
    for (const th_node *ancestor = root->parent; ancestor != NULL; ancestor = ancestor->parent) {
        depth++;
    }
    if (depth == 0) {
        return 0;
    }
    const th_node **chain = PyMem_Malloc(depth * sizeof(*chain));
    if (chain == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;       /* GCOVR_EXCL_LINE */
    }
    size_t index = 0;
    for (const th_node *ancestor = root->parent; ancestor != NULL; ancestor = ancestor->parent) {
        chain[index++] = ancestor;
    }
    int status = 0;
    while (index-- > 0 && status == 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        status = number_namespace_enter(scope, tree, chain[index]);
    }
    PyMem_Free(chain);
    return status;
}

/* Mark every element and attribute of root's subtree that is in the XSLT namespace. */
static int marks_build(xslt_marks *marks, th_tree *tree, th_node *root) {
    xslt_number_index scope = {0};
    int status = marks_enter_ancestors(&scope, tree, root);
    for (th_node *node = root; node != NULL && status == 0;) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        TH_WORK_STEP();
        if (node->type == TH_NODE_ELEMENT) {
            status = number_namespace_enter(&scope, tree, node);
            if (status == 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                status = marks_record(marks, &scope, tree, node);
            }
        }
        th_node *next = preorder_next(node, root);
        for (th_node *closed = node; next != NULL && closed != next->parent; closed = closed->parent) {
            number_namespace_leave(&scope, closed);
        }
        node = next;
    }
    PyMem_Free(scope.bindings);
    PyMem_Free(scope.scope);
    PyMem_Free(scope.uris);
    PyMem_Free(scope.prefix);
    return status;
}

static void marks_free(xslt_marks *marks) {
    PyMem_Free(marks->slots.entries);
    PyMem_Free(marks->flags);
}

/* Resolve the namespace spelling used by the principal stylesheet root, falling
   back to any XSLT binding for a simplified stylesheet and then to "xsl". */
static int resolve_xsl_prefix(engine *eng, th_node *root) {
    const char *prefix = "xsl";
    Py_ssize_t prefix_len = 3;
    int resolved = 0;
    Py_ssize_t root_prefix_len = 0;
    while (root_prefix_len < root->text_len && root->text[root_prefix_len] != ':') {
        root_prefix_len++;
    }
    if (root_prefix_len == root->text_len) {
        root_prefix_len = 0;
    }
    for (Py_ssize_t index = 0; index < root->attr_count; index++) {
        const th_node_attr *attr = &root->attrs[index];
        Py_ssize_t name_len = 0;
        const char *name = th_attr_name(eng->sheet_tree, attr->name_atom, &name_len);
        int root_decl = root_prefix_len == 0 ? name_len == 5 && memcmp(name, "xmlns", 5) == 0
                                             : name_len == root_prefix_len + 6 && memcmp(name, "xmlns:", 6) == 0;
        for (Py_ssize_t offset = 0; root_decl && offset < root_prefix_len; offset++) {
            root_decl = (Py_UCS4)(unsigned char)name[offset + 6] == root->text[offset];
        }
        if (root_decl && ucs4_ascii_eq(attr->value, attr->value_len, XSLT_NS)) {
            prefix = name + name_len - root_prefix_len;
            prefix_len = root_prefix_len;
            resolved = 1;
            break;
        }
    }
    if (!resolved) {
        for (Py_ssize_t index = 0; index < root->attr_count; index++) {
            const th_node_attr *attr = &root->attrs[index];
            Py_ssize_t name_len = 0;
            const char *name = th_attr_name(eng->sheet_tree, attr->name_atom, &name_len);
            if (name_len > 6 && memcmp(name, "xmlns:", 6) == 0 &&
                ucs4_ascii_eq(attr->value, attr->value_len, XSLT_NS)) {
                prefix = name + 6;
                prefix_len = name_len - 6;
                break;
            }
        }
    }
    Py_UCS4 *owned = ucs4_from_ascii(prefix, prefix_len, &eng->xsl_prefix_len);
    if (owned == NULL) {                   /* GCOVR_EXCL_BR_LINE: alloc */
        return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
    }
    eng->xsl_prefix = owned;
    return 0;
}

static int namespace_declares_prefix(const engine *eng, const char *name, Py_ssize_t name_len) {
    if (name_len != eng->xsl_prefix_len + 6) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < eng->xsl_prefix_len; index++) {
        if ((Py_UCS4)(unsigned char)name[index + 6] != eng->xsl_prefix[index]) {
            return 0;
        }
    }
    return 1;
}

static int stylesheet_has_dynamic_namespace(const engine *eng, th_node *root) {
    for (th_node *node = root; node != NULL; node = preorder_next(node, root)) {
        if (node->type != TH_NODE_ELEMENT) {
            continue;
        }
        for (Py_ssize_t index = 0; index < node->attr_count; index++) {
            const th_node_attr *attr = &node->attrs[index];
            Py_ssize_t name_len = 0;
            const char *name = th_attr_name(eng->sheet_tree, attr->name_atom, &name_len);
            int declaration =
                (name_len == 5 && memcmp(name, "xmlns", 5) == 0) || (name_len > 6 && memcmp(name, "xmlns:", 6) == 0);
            if (!declaration) {
                continue;
            }
            int selected = namespace_declares_prefix(eng, name, name_len);
            int binds_xsl = ucs4_ascii_eq(attr->value, attr->value_len, XSLT_NS);
            if (node == root && selected) {
                continue;
            }
            if (selected || binds_xsl) {
                return 1;
            }
        }
    }
    return 0;
}

/* Sort rules by descending (priority, position) so best_rule's first
   match is the conflict-resolution winner. */
static int rule_order(const void *left_ptr, const void *right_ptr) {
    const xslt_rule *left = left_ptr;
    const xslt_rule *right = right_ptr;
    /* Conflict resolution (section 5.5): higher import precedence wins, then higher priority,
       then later document position. Precedence and position are small ints, so their signed
       difference orders them branchlessly (higher precedence, then later position, sorts first) --
       a ternary here leaves one arm that only some qsort implementations ever call, which diverges
       across C libraries. Priority is a double, so it keeps a ternary the single-stylesheet tests
       already exercise both arms of. */
    if (left->precedence != right->precedence) {
        return (int)(right->precedence - left->precedence);
    }
    if (left->priority != right->priority) {
        return left->priority < right->priority ? 1 : -1;
    }
    return (int)(right->position - left->position);
}

/* ---- output serialization ------------------------------------------------- */

/* Retype direct text children of cdata-section-elements so the serializer wraps them in CDATA. */
static void apply_cdata_sections(engine *eng, th_node *node) {
    for (th_node *child = node->first_child; child != NULL; child = preorder_next(child, node)) {
        if (child->type != TH_NODE_ELEMENT) {
            continue;
        }
        if (name_in_token_list(eng->cdata_elements, eng->cdata_elements_len, child->text, child->text_len)) {
            for (th_node *text = child->first_child; text != NULL; text = text->next_sibling) {
                if (text->type == TH_NODE_TEXT) {
                    text->type = TH_NODE_CDATA;
                }
            }
        }
    }
}

/* Whether an element carries a non-empty default-namespace declaration (xmlns=""), which puts
   it in a non-null namespace and so suppresses the html output method's auto-selection. */
static int has_default_namespace(engine *eng, th_node *element) {
    for (Py_ssize_t index = 0; index < element->attr_count; index++) {
        Py_ssize_t name_len = 0;
        const char *name = th_attr_name(eng->out_tree, element->attrs[index].name_atom, &name_len);
        if (name_len == 5 && memcmp(name, "xmlns", 5) == 0) {
            return element->attrs[index].value_len > 0;
        }
    }
    return 0;
}

/* Select the html output method when no xsl:output method was given and the result's document
   element is a null-namespace element named "html" (section 16, matching libxslt). */
static void auto_select_method(engine *eng, th_node *out_root) {
    if (eng->method_seen) {
        return;
    }
    for (th_node *child = out_root->first_child; child != NULL; child = child->next_sibling) {
        if (child->type == TH_NODE_TEXT) {
            if (ucs4_blank(child->text, child->text_len)) {
                continue;
            }
            return; /* significant text before the first element keeps the xml method */
        }
        if (child->type != TH_NODE_ELEMENT) {
            continue;
        }
        if (child->atom == TH_TAG_HTML && !has_default_namespace(eng, child)) {
            eng->output_method = OUT_HTML;
        }
        return;
    }
}

/* The text descendants of root joined into a string of the narrowest width that holds them, given their total length.
   Sizing the string first writes each character once, with no UCS-4 copy to grow and narrow. */
static PyObject *join_wide_text(th_node *root, Py_ssize_t length) {
    Py_UCS4 bits = 0;
    for (th_node *node = root->first_child; node != NULL && bits <= 0xFFFF; node = preorder_next(node, root)) {
        if (node->type == TH_NODE_TEXT) {
            for (Py_ssize_t index = 0; index < node->text_len; index++) {
                bits |= node->text[index];
            }
        }
    }
    Py_UCS4 maxchar = bits <= 0xFF ? 0xFF : bits <= 0xFFFF ? 0xFFFF : 0x10FFFF;
    PyObject *out = PyUnicode_New(length, th_str_maxchar(maxchar));
    if (out == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
        return NULL;   /* GCOVR_EXCL_LINE */
    }
    int kind = PyUnicode_KIND(out);
    Py_ssize_t at = 0;
    for (th_node *node = root->first_child; node != NULL; node = preorder_next(node, root)) {
        if (node->type != TH_NODE_TEXT) {
            continue;
        }
        const Py_UCS4 *text = node->text;
        Py_ssize_t text_len = node->text_len;
        if (kind == PyUnicode_1BYTE_KIND) {
            Py_UCS1 *destination = PyUnicode_1BYTE_DATA(out) + at;
            for (Py_ssize_t index = 0; index < text_len; index++) {
                destination[index] = (Py_UCS1)text[index];
            }
#ifndef PYPY_VERSION
        } else if (kind == PyUnicode_2BYTE_KIND) {
            Py_UCS2 *destination = PyUnicode_2BYTE_DATA(out) + at;
            for (Py_ssize_t index = 0; index < text_len; index++) {
                destination[index] = (Py_UCS2)text[index];
            }
#endif
        } else if (text_len > 0) {
            memcpy(PyUnicode_4BYTE_DATA(out) + at, text, (size_t)text_len * sizeof(Py_UCS4));
        }
        at += text_len;
    }
    return out;
}

/* Concatenate the text descendants of the output root (xsl:output method="text"). Most output is ASCII, so the text
   goes straight into an ASCII string, checking the width in the same pass; anything wider is joined again. */
static PyObject *serialize_text(engine *eng, th_node *root) {
    (void)eng;
    Py_ssize_t length = 0;
    for (th_node *node = root->first_child; node != NULL; node = preorder_next(node, root)) {
        if (node->type == TH_NODE_TEXT) {
            length += node->text_len;
        }
    }
    PyObject *out = PyUnicode_New(length, 0x7F);
    if (out == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
        return NULL;   /* GCOVR_EXCL_LINE */
    }
    Py_UCS1 *destination = PyUnicode_1BYTE_DATA(out);
    Py_UCS4 bits = 0;
    for (th_node *node = root->first_child; node != NULL && bits <= 0x7F; node = preorder_next(node, root)) {
        if (node->type == TH_NODE_TEXT) {
            const Py_UCS4 *text = node->text;
            Py_ssize_t text_len = node->text_len;
            for (Py_ssize_t index = 0; index < text_len; index++) {
                bits |= text[index];
                destination[index] = (Py_UCS1)text[index];
            }
            destination += text_len;
        }
    }
    if (bits <= 0x7F) {
        return out;
    }
    Py_DECREF(out);
    return join_wide_text(root, length);
}

static int raise_html_pi_gt(const th_node *pi) {
    PyObject *target = make_str(pi->text, pi->attr_count);
    if (target != NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        PyErr_Format(PyExc_ValueError,
                     "xslt: processing instruction '%U' holds '>', which ends a processing instruction under the html "
                     "output method; remove the '>' or use the xml output method",
                     target);
        Py_DECREF(target);
    }
    return -1;
}

/* WHATWG HTML 13.1.6 forbids comment text that starts with `>` or `->`: the tokenizer reads `<!-->` and `<!--->` as
   a whole empty comment, so the rest of the text would become markup. */
static int starts_like_comment_end(const Py_UCS4 *text, Py_ssize_t len) {
    Py_ssize_t start = len > 1 && text[0] == '-';
    return start < len && text[start] == '>';
}

/* HTML has no escape inside a comment or processing instruction, and the output method is final only once the result
   tree exists, so created and copied nodes get checked here; a PI's `>` is Serialization 3.1 err:SERE0015. */
static int prepare_html_leaves(engine *eng, th_node *root) {
    for (th_node *node = root; node != NULL; node = preorder_next(node, root)) {
        if (node->type == TH_NODE_PI) {
            for (Py_ssize_t index = 0; index < node->text_len; index++) {
                if (node->text[index] == '>') {
                    return raise_html_pi_gt(node);
                }
            }
        } else if (node->type == TH_NODE_COMMENT && starts_like_comment_end(node->text, node->text_len)) {
            Py_UCS4 *spaced = PyMem_Malloc((size_t)(node->text_len + 1) * sizeof(Py_UCS4));
            if (spaced == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
                PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
                return -1;        /* GCOVR_EXCL_LINE */
            }
            spaced[0] = ' ';
            memcpy(spaced + 1, node->text, (size_t)node->text_len * sizeof(Py_UCS4));
            int rc = th_node_set_data(eng->out_tree, node, spaced, node->text_len + 1);
            PyMem_Free(spaced);
            if (rc < 0) {         /* GCOVR_EXCL_BR_LINE: alloc */
                PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
                return -1;        /* GCOVR_EXCL_LINE */
            }
        }
    }
    return 0;
}

/* Serialize the output tree's children as XML or HTML markup. */
static PyObject *serialize_markup(engine *eng, th_node *root) {
    if (eng->output_method == OUT_HTML && prepare_html_leaves(eng, root) < 0) {
        return NULL;
    }
    th_serialize_opts opts = {0};
    opts.xml = eng->output_method == OUT_XML;
    if (eng->output_method == OUT_HTML) {
        opts.inject_meta = 1;
        opts.charset = "UTF-8";
        opts.charset_len = 5;
    }
    xb buffer = {0};
    if (eng->output_method == OUT_XML && !eng->omit_xml_decl) {
        if (xb_add_ascii(&buffer, "<?xml version=\"1.0\"?>\n") < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            xb_free(&buffer);                                         /* GCOVR_EXCL_LINE */
            return PyErr_NoMemory();                                  /* GCOVR_EXCL_LINE */
        }
    }
    for (th_node *child = root->first_child; child != NULL; child = child->next_sibling) {
        Py_ssize_t chunk_len = 0;
        Py_UCS4 *chunk = th_node_serialize(eng->out_tree, child, &opts, NULL, 0, &chunk_len);
        if (chunk == NULL) {
            xb_free(&buffer);
            /* copies of the declarations on an element XML leaves out past their allowance leave ValueError set */
            if (PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: the no-exception NULL is an unforceable allocation failure */
                return NULL;
            }
            return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        int rc = xb_add(&buffer, chunk, chunk_len);
        PyMem_Free(chunk);
        if (rc < 0) {                /* GCOVR_EXCL_BR_LINE: alloc */
            xb_free(&buffer);        /* GCOVR_EXCL_LINE */
            return PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
        }
    }
    PyObject *out = make_str(buffer.data, buffer.len);
    xb_free(&buffer);
    return out;
}

/* ---- engine lifecycle ----------------------------------------------------- */

static void engine_clear(engine *eng) {
    xp_id_cache_clear(&eng->src_ids);
    xp_lang_memo_free(eng->src_langs);
    PyMem_Free(eng->dispatch);
    for (size_t index = 0; index < eng->number_sites.count; index++) {
        number_site_free(&eng->number_sites.list[index]);
    }
    PyMem_Free(eng->number_sites.list);
    PyMem_Free(eng->number_sites.index.entries);
    PyMem_Free(eng->default_siblings.counts.entries);
    number_walk_free(&eng->number_lineage.walk);
    PyMem_Free(eng->number_lineage.elements.items);
    PyMem_Free(eng->number_index.positions.entries);
    PyMem_Free(eng->number_index.names);
    PyMem_Free(eng->number_index.bindings);
    PyMem_Free(eng->number_index.uris);
    PyMem_Free(eng->number_index.scope);
    PyMem_Free(eng->number_index.prefix);
    declarations_free(&eng->src_declarations);
    declarations_free(&eng->sheet_declarations);
    for (Py_ssize_t index = 0; index < eng->nrules; index++) {
        match_set_free(&eng->rules[index].matched);
    }
    PyMem_Free(eng->rules);
    for (Py_ssize_t index = 0; index < eng->nkeys; index++) {
        strmap_free(&eng->keys[index].table);
        PyMem_Free(eng->keys[index].attrs);
    }
    PyMem_Free(eng->keys);
    if (eng->owns_model) {
        PyMem_Free(eng->named_index.entries);
        PyMem_Free(eng->key_index.entries);
        PyMem_Free(eng->rule_names.entries);
        PyMem_Free(eng->instructions.entries);
        PyMem_Free(eng->attrset_index.entries);
        PyMem_Free(eng->attrset_previous);
        PyMem_Free(eng->attrset_uses.items);
        PyMem_Free(eng->named);
        PyMem_Free(eng->globals);
        PyMem_Free(eng->attrsets);
        PyMem_Free(eng->spaces);
        PyMem_Free(eng->aliases);
        PyMem_Free(eng->xsl_prefix);
        marks_free(&eng->marks);
        for (size_t index = 0; index < eng->expression_cap; index++) {
            if (eng->expressions[index].program != NULL) {
                xp_free(eng->expressions[index].program);
            }
        }
        PyMem_Free(eng->expressions);
        for (size_t index = 0; index < eng->pattern_cap; index++) {
            if (eng->patterns[index].program != NULL) {
                xp_free(eng->patterns[index].program);
            }
        }
        PyMem_Free(eng->patterns);
    }
    PyMem_Free(eng->stripped);
    scope_drop(eng, 0);
    PyMem_Free(eng->scope);
    PyMem_Free(eng->ns_decls);
    PyMem_Free(eng->out_bindings);
    PyMem_Free(eng->out_bindings_seen.entries);
    PyMem_Free(eng->src_namespaced.entries);
    PyMem_Free(eng->src_scope.resolved.entries);
    PyMem_Free(eng->src_scope.found);
    if (eng->out_tree != NULL) {
        th_tree_free(eng->out_tree);
    }
    if (eng->source_snapshot != NULL) {
        th_tree_free(eng->source_snapshot);
    }
    /* snapshot_principal_stylesheet allocates this tree before model cleanup. */
    /* GCOVR_EXCL_BR_START */
    if (eng->owns_model && eng->merged_tree != NULL) {
        th_tree_free(eng->merged_tree);
    }
    /* GCOVR_EXCL_BR_STOP */
}

static int engine_start_run(engine *eng, const engine *model, th_tree *src_tree, th_node *src_node) {
    *eng = *model;
    eng->owns_model = 0;
    eng->src_tree = src_tree;
    eng->src_root = th_tree_document(src_tree);
    eng->src_ids = (xp_id_cache){0};
    eng->src_langs = NULL;
    eng->out_tree = NULL;
    eng->dispatch = NULL;
    eng->dispatch_capacity = 0;
    eng->dispatch_count = 0;
    eng->rules = NULL;
    eng->nrules = 0;
    eng->rules_cap = 0;
    eng->keys = NULL;
    eng->nkeys = 0;
    eng->keys_cap = 0;
    eng->stripped = NULL;
    eng->nstripped = 0;
    eng->stripped_cap = 0;
    eng->scope = NULL;
    eng->scope_len = 0;
    eng->scope_cap = 0;
    eng->error = NULL;
    eng->py_error = 0;
    eng->ns_cached_element = NULL;
    eng->ns_decls = NULL;
    eng->ns_decls_len = 0;
    eng->ns_decls_cap = 0;
    eng->default_bound = 0;
    eng->src_namespaced = (th_node_map){0};
    eng->src_scope = (xslt_scope_memo){0};
    eng->out_src = NULL;
    eng->ns_counter = 0;
    eng->out_bindings = NULL;
    eng->out_bindings_cap = 0;
    eng->out_bindings_len = 0;
    eng->out_bindings_seen = (th_node_map){0};
    eng->gen_counter = 0;
    eng->depth = 0;
#ifdef TH_STACK_FLOOR
    eng->stack_floor = 0; /* the model may have been compiled on another thread */
#endif
#ifdef TH_OPERATION_LIMIT
    eng->operations = 0;
#endif
    eng->number_sites = (xslt_number_sites){0};
    eng->number_namespaces = -1;
    eng->current_number_node = NULL;
    eng->number_index = (xslt_number_index){0};
    eng->number_memo = (xslt_number_memo){0};
    eng->default_siblings = (xslt_number_siblings){0};
    eng->number_lineage = (xslt_number_lineage){0};
    th_node *root = src_node;
    while (root->parent != NULL) {
        root = root->parent;
    }
    if (root != eng->src_root || root->type == TH_NODE_CONTENT) {
        eng->source_snapshot = th_tree_new_rooted(TH_NODE_DOCUMENT, th_tree_is_xml(src_tree), th_tree_quirks(src_tree));
        if (eng->source_snapshot == NULL) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
            return -1;                      /* GCOVR_EXCL_LINE */
        }
        eng->src_root = th_tree_document(eng->source_snapshot);
        th_copy_memo memo = {0}; /* one for all the top-level nodes, so attribute values they share are copied once */
        for (th_node *child = root->type == TH_NODE_CONTENT ? root->first_child : root; child != NULL;
             child = child->next_sibling) {
            th_node *copy = th_tree_copy_node_sharing(eng->source_snapshot, src_tree, child, &memo);
            if (copy == NULL) {           /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
                th_copy_memo_free(&memo); /* GCOVR_EXCL_LINE */
                return -1;                /* GCOVR_EXCL_LINE */
            }
            th_node_append_child(eng->src_root, copy);
        }
        th_copy_memo_free(&memo);
        eng->src_tree = eng->source_snapshot;
    }
    if (model->nrules > 0) {
        eng->rules = PyMem_Malloc((size_t)model->nrules * sizeof(xslt_rule));
        if (eng->rules == NULL) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
            return -1;            /* GCOVR_EXCL_LINE */
        }
        memcpy(eng->rules, model->rules, (size_t)model->nrules * sizeof(xslt_rule));
        eng->nrules = model->nrules;
        eng->rules_cap = model->nrules;
        for (Py_ssize_t index = 0; index < model->nrules; index++) {
            memset(&eng->rules[index].matched, 0, sizeof(match_set));
            eng->rules[index].built = 0;
        }
    }
    if (model->nkeys > 0) {
        eng->keys = PyMem_Malloc((size_t)model->nkeys * sizeof(xslt_key));
        if (eng->keys == NULL) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
            return -1;           /* GCOVR_EXCL_LINE */
        }
        memcpy(eng->keys, model->keys, (size_t)model->nkeys * sizeof(xslt_key));
        eng->nkeys = model->nkeys;
        eng->keys_cap = model->nkeys;
        for (Py_ssize_t index = 0; index < model->nkeys; index++) {
            memset(&eng->keys[index].table, 0, sizeof(strmap));
            eng->keys[index].attrs = NULL;
            eng->keys[index].built = 0;
        }
    }
    return 0;
}

/* Bind the global variables and params (params overridable by the passed dict). */
static int bind_globals(engine *eng, PyObject *params) {
    for (Py_ssize_t index = 0; index < eng->nglobals; index++) {
        xslt_global *global = &eng->globals[index];
        if (global->is_param && params != NULL) {
            PyObject *key = make_str(global->name, global->name_len);
            if (key == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
                return -1;     /* GCOVR_EXCL_LINE */
            }
            PyObject *supplied = PyDict_GetItemWithError(params, key);
            Py_DECREF(key);
            if (supplied == NULL && PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: dict lookup cannot fail here */
                return -1;                              /* GCOVR_EXCL_LINE */
            }
            if (supplied != NULL) {
                Py_ssize_t expr_len = 0;
                Py_UCS4 *expr = PyUnicode_AsUCS4Copy(supplied);
                if (expr == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
                    return -1;      /* GCOVR_EXCL_LINE */
                }
                expr_len = PyUnicode_GET_LENGTH(supplied);
                char errbuf[256];
                xp_program *prog = xp_compile(expr, expr_len, errbuf, sizeof(errbuf), PyExc_ValueError);
                PyMem_Free(expr);
                if (prog == NULL) {
                    if (!PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: allocation error */
                        PyErr_Format(PyExc_ValueError, "xslt: bad parameter expression: %s", errbuf);
                    }
                    return fail_py(eng);
                }
                xp_result value;
                int status = eval_program(eng, prog, &(xp_item){eng->src_root, -1}, 1, 1, &value);
                xp_free(prog);
                if (status < 0) {
                    return fail_py(eng);
                }
                if (scope_push(eng, global->name, global->name_len, value, NULL) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                    return fail(eng, "out of memory");                                  /* GCOVR_EXCL_LINE */
                }
                continue;
            }
        }
        xp_result value;
        th_node *rtf;
        th_node *saved = eng->cur_node;
        eng->cur_node = eng->src_root;
        int rc = compute_binding(eng, global->node, &value, &rtf);
        eng->cur_node = saved;
        if (rc < 0) {
            return -1;
        }
        if (scope_push(eng, global->name, global->name_len, value, rtf) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            return fail(eng, "out of memory");                                 /* GCOVR_EXCL_LINE */
        }
    }
    return 0;
}

/* Walk one stylesheet root's top-level declarations at the current import precedence. */
static int analyze_root(engine *eng, th_node *sheet_root, int *position) {
    for (th_node *child = sheet_root->first_child; child != NULL; child = child->next_sibling) {
        if (child->type != TH_NODE_ELEMENT) {
            continue;
        }
        if (is_xsl(eng, child, "template")) {
            if (parse_template(eng, child, position) < 0) {
                return -1;
            }
        } else if (is_xsl(eng, child, "variable") || is_xsl(eng, child, "param")) {
            Py_ssize_t name_len = 0;
            const Py_UCS4 *name = attr_lookup(eng->sheet_tree, child, "name", 4, &name_len);
            if (name == NULL) {
                return fail(eng, "a global xsl:variable/xsl:param requires a name attribute");
            }
            if (push_global(eng, name, name_len, child, is_xsl(eng, child, "param")) < 0) { /* GCOVR_EXCL_BR_LINE */
                return -1;                                                                  /* GCOVR_EXCL_LINE */
            }
        } else if (is_xsl(eng, child, "output")) {
            parse_output(eng, child);
        } else if (is_xsl(eng, child, "key")) {
            if (parse_key(eng, child) < 0) {
                return -1;
            }
        } else if (is_xsl(eng, child, "strip-space")) {
            if (parse_space(eng, child, 1) < 0) {
                return -1;
            }
        } else if (is_xsl(eng, child, "preserve-space")) {
            if (parse_space(eng, child, 0) < 0) {
                return -1;
            }
        } else if (is_xsl(eng, child, "attribute-set")) {
            if (parse_attrset(eng, child) < 0) {
                return -1;
            }
        } else if (is_xsl(eng, child, "namespace-alias")) {
            if (parse_namespace_alias(eng, sheet_root, child) < 0) {
                return -1;
            }
        }
        /* decimal-format is not modeled; it parses without effect. */
    }
    return 0;
}

/* Whether the root declares the XSLT prefix bound to the XSLT namespace. Then the prefix comparison of is_any_xsl_fast
   classifies every element the way its ancestor climb does, so the marks are not needed. */
static int root_binds_xsl_prefix(const engine *eng, const th_node *root) {
    for (Py_ssize_t index = 0; index < root->attr_count; index++) {
        const th_node_attr *attr = &root->attrs[index];
        Py_ssize_t name_len = 0;
        const char *name = th_attr_name(eng->sheet_tree, attr->name_atom, &name_len);
        if (name_len != eng->xsl_prefix_len + 6 || memcmp(name, "xmlns:", 6) != 0) {
            continue;
        }
        int matches = 1;
        for (Py_ssize_t offset = 0; matches && offset < eng->xsl_prefix_len; offset++) {
            matches = (Py_UCS4)(unsigned char)name[offset + 6] == eng->xsl_prefix[offset];
        }
        if (matches) {
            return ucs4_ascii_eq(attr->value, attr->value_len, XSLT_NS);
        }
    }
    return 0;
}

static int stylesheet_has_exclusions(const engine *eng, th_node *root) {
    for (th_node *node = root; node != NULL; node = preorder_next(node, root)) {
        Py_ssize_t list_len = 0;
        if (node->type == TH_NODE_ELEMENT &&
            xsl_prefixed_attr(eng, node, "exclude-result-prefixes", &list_len) != NULL) {
            return 1;
        }
    }
    return 0;
}

/* Analyze the stylesheet: resolve the XSLT prefix and walk the top-level declarations of every
   imported stylesheet (lowest precedence first) then the principal stylesheet (highest). Returns
   0, or -1 with eng->error / eng->py_error set. */
static int analyze(engine *eng, th_node *sheet_root, th_node **imports, Py_ssize_t nimports) {
    if (resolve_xsl_prefix(eng, sheet_root) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;                                 /* GCOVR_EXCL_LINE */
    }
    eng->xsl_ns_dynamic = eng->xsl_prefix_len == 0 || stylesheet_has_dynamic_namespace(eng, sheet_root);
    for (Py_ssize_t index = 0; !eng->xsl_ns_dynamic && index < nimports; index++) {
        eng->xsl_ns_dynamic = stylesheet_has_dynamic_namespace(eng, imports[index]);
    }
    eng->lre_excludes = stylesheet_has_exclusions(eng, sheet_root);
    for (Py_ssize_t index = 0; !eng->lre_excludes && index < nimports; index++) {
        eng->lre_excludes = stylesheet_has_exclusions(eng, imports[index]);
    }
    eng->xsl_marked = eng->xsl_ns_dynamic || !root_binds_xsl_prefix(eng, sheet_root);
    for (Py_ssize_t index = 0; !eng->xsl_marked && index < nimports; index++) {
        eng->xsl_marked = !root_binds_xsl_prefix(eng, imports[index]);
    }
    if (eng->xsl_marked && marks_build(&eng->marks, eng->sheet_tree, sheet_root) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;                                                                      /* GCOVR_EXCL_LINE */
    }
    for (Py_ssize_t index = 0; eng->xsl_marked && index < nimports; index++) {
        if (marks_build(&eng->marks, eng->sheet_tree, imports[index]) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;                                                       /* GCOVR_EXCL_LINE */
        }
    }
    /* A document element that is not xsl:stylesheet/xsl:transform is a simplified stylesheet
       (section 2.3): a literal result element carrying xsl:version whose whole content is the
       body of an implicit template matching the document root. */
    if (!is_xsl(eng, sheet_root, "stylesheet") && !is_xsl(eng, sheet_root, "transform")) {
        eng->simplified = 1;
        return 0;
    }
    eng->exclude_prefixes_len = 0;
    eng->exclude_prefixes =
        attr_lookup(eng->sheet_tree, sheet_root, "exclude-result-prefixes", 23, &eng->exclude_prefixes_len);
    eng->ext_prefixes_len = 0;
    eng->ext_prefixes =
        attr_lookup(eng->sheet_tree, sheet_root, "extension-element-prefixes", 26, &eng->ext_prefixes_len);
    int position = 0;
    for (Py_ssize_t index = 0; index < nimports; index++) {
        eng->precedence = (int)index;
        if (analyze_root(eng, imports[index], &position) < 0) {
            return -1;
        }
    }
    eng->precedence = (int)nimports;
    if (analyze_root(eng, sheet_root, &position) < 0) {
        return -1;
    }
    if (eng->nrules > 1) {
        qsort(eng->rules, (size_t)eng->nrules, sizeof(xslt_rule), rule_order);
    }
    return 0;
}

/* ---- whitespace stripping (section 3.4) ----------------------------------- */

/* Whether whitespace-only text children of element E are stripped: the highest-precedence,
   then highest-specificity, strip/preserve entry matching E's name decides, with a strip
   entry stripping and no match preserving (elements default to preserve). */
static int element_strips_space(const engine *eng, const th_node *element) {
    int best_strip = 0;
    int have = 0;
    int best_precedence = 0;
    double best_specificity = 0;
    for (Py_ssize_t index = 0; index < eng->nspaces; index++) {
        const xslt_space *entry = &eng->spaces[index];
        /* A prefixed wildcard "ns:*" matches any element whose QName carries that prefix. */
        int prefixed_wildcard =
            entry->name_len >= 2 && entry->name[entry->name_len - 1] == '*' && entry->name[entry->name_len - 2] == ':';
        int matches = (entry->name_len == 1 && entry->name[0] == '*') ||
                      (prefixed_wildcard && element->text_len >= entry->name_len - 1 &&
                       memcmp(entry->name, element->text, (size_t)(entry->name_len - 1) * sizeof(Py_UCS4)) == 0) ||
                      (!prefixed_wildcard && entry->name_len == element->text_len &&
                       memcmp(entry->name, element->text, (size_t)element->text_len * sizeof(Py_UCS4)) == 0);
        if (!matches) {
            continue;
        }
        int better;
        if (!have) {
            better = 1;
        } else if (entry->precedence != best_precedence) {
            better = entry->precedence > best_precedence;
        } else {
            better = entry->specificity >= best_specificity;
        }
        if (better) {
            best_precedence = entry->precedence;
            best_specificity = entry->specificity;
            best_strip = entry->strip;
            have = 1;
        }
    }
    return best_strip;
}

static int strip_record(engine *eng, th_node *node, th_node *parent, th_node *next) {
    if (eng->nstripped == eng->stripped_cap) {
        Py_ssize_t cap = eng->stripped_cap == 0 ? TH_INITIAL_CAPACITY(16) : eng->stripped_cap * 2;
        struct strip_entry *grown = PyMem_Realloc(eng->stripped, (size_t)cap * sizeof(struct strip_entry));
        if (grown == NULL) {                   /* GCOVR_EXCL_BR_LINE: alloc */
            return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
        }
        eng->stripped = grown;
        eng->stripped_cap = cap;
    }
    eng->stripped[eng->nstripped].node = node;
    eng->stripped[eng->nstripped].parent = parent;
    eng->stripped[eng->nstripped].next = next;
    eng->nstripped++;
    return 0;
}

typedef struct {
    th_node *element;
    th_node *child;
    int preserve;
    int strips;
} strip_frame;

static int strip_push(engine *eng, strip_frame **frames, size_t *length, size_t *capacity, th_node *element,
                      int inherited_preserve) {
    if (*length == *capacity) {
        size_t grown_capacity;
        size_t bytes;
        /* Source depth cannot exhaust size_t. */
        /* GCOVR_EXCL_BR_START */
        if (!th_grow_cap(*length + 1, *capacity, 16, sizeof(strip_frame), &grown_capacity, &bytes)) {
            return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
        }
        /* GCOVR_EXCL_BR_STOP */
        strip_frame *grown = PyMem_Realloc(*frames, bytes);
        if (grown == NULL) {                   /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
            return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
        }
        *frames = grown;
        *capacity = grown_capacity;
    }
    int preserve = inherited_preserve;
    Py_ssize_t xmlspace_len = 0;
    const Py_UCS4 *xmlspace = attr_lookup(eng->src_tree, element, "xml:space", 9, &xmlspace_len);
    if (xmlspace != NULL) {
        preserve = ucs4_ascii_eq(xmlspace, xmlspace_len, "preserve");
    }
    (*frames)[(*length)++] =
        (strip_frame){element, element->first_child, preserve, !preserve && element_strips_space(eng, element)};
    return 0;
}

/* Detach strippable whitespace text in document order while tracking inherited xml:space on a heap stack. */
static int strip_walk(engine *eng, th_node *element, int inherited_preserve) {
    strip_frame *frames = NULL;
    size_t length = 0;
    size_t capacity = 0;
    int push_failed = strip_push(eng, &frames, &length, &capacity, element, inherited_preserve);
    if (push_failed < 0) {  /* GCOVR_EXCL_BR_LINE: failure requires allocation exhaustion */
        PyMem_Free(frames); /* GCOVR_EXCL_LINE */
        return -1;          /* GCOVR_EXCL_LINE */
    }
    while (length > 0) {
        strip_frame *frame = &frames[length - 1];
        if (frame->child == NULL) {
            length--;
            continue;
        }
        th_node *child = frame->child;
        frame->child = child->next_sibling;
        if (child->type == TH_NODE_TEXT) {
            int blank = frame->strips ? th_node_text_is_blank(eng->src_tree, child) : 0;
            if (blank < 0) {                       /* GCOVR_EXCL_BR_LINE: lazy text OOM */
                PyMem_Free(frames);                /* GCOVR_EXCL_LINE: allocation failure */
                return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE: allocation failure */
            }
            if (blank) {
                if (strip_record(eng, child, frame->element, frame->child) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                    PyMem_Free(frames);                                           /* GCOVR_EXCL_LINE */
                    return -1;                                                    /* GCOVR_EXCL_LINE */
                }
                th_node_remove(child);
            }
        } else if (child->type == TH_NODE_ELEMENT && /* GCOVR_EXCL_BR_LINE: failure requires allocation exhaustion */
                   strip_push(eng, &frames, &length, &capacity, child, frame->preserve) < 0) {
            PyMem_Free(frames); /* GCOVR_EXCL_LINE */
            return -1;          /* GCOVR_EXCL_LINE */
        }
    }
    PyMem_Free(frames);
    return 0;
}

/* The namespace a prefix a stylesheet expression uses is bound to on element: the declaration in scope there, nearest
   first, as a match pattern resolves its own prefixes. */
static int scope_prefix_resolve(void *context, const Py_UCS4 *prefix, Py_ssize_t prefix_len, const Py_UCS4 **uri,
                                Py_ssize_t *uri_len) {
    xslt_scope_source *source = context;
    Py_ssize_t prefix_bytes = 0;
    char *utf8 = ucs4_to_utf8(prefix, prefix_len, &prefix_bytes);
    if (utf8 == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
        return -1;        /* GCOVR_EXCL_LINE */
    }
    const th_node_attr *declared = NULL;
    int resolved = pattern_declaration(&source->eng->sheet_declarations, source->eng->sheet_tree, source->element, utf8,
                                       prefix_bytes, &declared);
    PyMem_Free(utf8);
    if (resolved < 0) {   /* GCOVR_EXCL_BR_LINE: allocation failure */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
        return -1;        /* GCOVR_EXCL_LINE */
    }
    if (declared == NULL) {
        return 0;
    }
    *uri = declared->value;
    *uri_len = declared->value_len;
    return 1;
}

/* Bind the prefixes an expression on element uses to the namespaces in scope there, so they resolve as the stylesheet
   declares them. */
static int bind_expression_scope(engine *eng, xp_program *prog, th_node *element) {
    xslt_scope_source source = {.eng = eng, .element = element};
    return xp_bind_namespaces(prog, scope_prefix_resolve, &source);
}

static int pattern_node_is_plain(uint8_t kind) {
    switch (kind) {
    case XN_PATH:
    case XN_STEP:
    case XN_PRED:
    case XN_NUM:
    case XN_LIT:
    case XN_EQ:
    case XN_NE:
    case XN_LT:
    case XN_LE:
    case XN_GT:
    case XN_GE:
    case XN_AND:
    case XN_OR:
        return 1;
    default:
        return 0;
    }
}

/* Set rule->key to the element name the pattern can match, or to NULL when any node may match it. A pattern that is not
   a plain path or comparison is never named: evaluating a function or a variable can raise whatever nodes the source
   holds. A prefixed name test raises on an HTML source the same way, so its rule stays in the wildcard chain and is
   built wherever the scan reaches it, as it always was. */
static void rule_element_key(xslt_rule *rule) {
    const xn *nodes = rule->prog->nodes;
    rule->key = NULL;
    for (int32_t index = 0; index < rule->prog->count; index++) {
        const xn *node = &nodes[index];
        if (!pattern_node_is_plain(node->kind) ||
            (node->kind == XN_STEP && node->test == NT_NAME && node->prefix_len > 0)) {
            return;
        }
    }
    const xn *path = &nodes[rule->prog->root];
    if (path->first < 0) {
        return;
    }
    int32_t last = path->first;
    while (nodes[last].next >= 0) {
        last = nodes[last].next;
    }
    const xn *step = &nodes[last];
    if (step->test != NT_NAME || (step->axis != AX_CHILD && step->axis != AX_DESCENDANT)) {
        return;
    }
    rule->key = step->str;
    rule->key_len = step->str_len;
}

/* Chain the rules best_rule consults: an element-named rule joins the chain of its name, and every other rule joins
   the wildcard chain. Each chain keeps the sorted order, so the first matching rule along it is the winner. */
static int build_rule_index(engine *eng) {
    Py_ssize_t named = 0;
    for (Py_ssize_t index = 0; index < eng->nrules; index++) {
        xslt_rule *rule = &eng->rules[index];
        rule_element_key(rule);
        named += rule->key != NULL;
    }
    /* GCOVR_EXCL_BR_START: allocation failure */
    if (name_index_reserve(&eng->rule_names, named) < 0) {
        return -1; /* GCOVR_EXCL_LINE */
    }
    /* GCOVR_EXCL_BR_STOP */
    Py_ssize_t wild = -1;
    Py_ssize_t root = -1;
    Py_ssize_t wild_len = 0;
    Py_ssize_t root_len = 0;
    for (Py_ssize_t index = eng->nrules - 1; index >= 0; index--) {
        xslt_rule *rule = &eng->rules[index];
        const xn *pattern = &rule->prog->nodes[rule->prog->root];
        if (pattern->kind == XN_PATH && pattern->first < 0) {
            rule->next = root;
            root = index;
            root_len++;
        } else if (eng->rule_names.capacity == 0 || rule->key == NULL) {
            rule->next = wild;
            wild = index;
            wild_len++;
        }
    }
    eng->rule_wild = wild;
    eng->rule_root = root;
    if (eng->rule_names.capacity != 0) {
        do {
            for (Py_ssize_t index = eng->nrules - 1; index >= 0 && !eng->rule_names.walked_far; index--) {
                xslt_rule *rule = &eng->rules[index];
                if (rule->key != NULL) {
                    rule->next = name_index_add(&eng->rule_names, rule->key, rule->key_len, index) - 1;
                }
            }
        } while (name_index_rekey(&eng->rule_names));
    }
    /* A lookup in the dispatch cache costs about what testing a few rules does, and on a single pass over the source
       every lookup misses, so the cache pays only where a node can walk a long chain. Each chain lists its rules in
       ascending order, so one descending sweep finds every named chain's length from the rule after it. */
    Py_ssize_t longest = 0;
    if (eng->rule_names.capacity != 0) {
        Py_ssize_t *lengths = PyMem_Calloc((size_t)eng->nrules, sizeof(Py_ssize_t));
        if (lengths == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;         /* GCOVR_EXCL_LINE */
        }
        for (Py_ssize_t index = eng->nrules - 1; index >= 0; index--) {
            const xslt_rule *rule = &eng->rules[index];
            if (rule->key != NULL) {
                lengths[index] = 1 + (rule->next < 0 ? 0 : lengths[rule->next]);
                longest = lengths[index] > longest ? lengths[index] : longest;
            }
        }
        PyMem_Free(lengths);
    }
    eng->rule_dispatch = eng->nrules >= 16 && wild_len + root_len + longest >= 4;
    return 0;
}

static int precompile_expression(engine *eng, th_node *element, const Py_UCS4 *source, Py_ssize_t length,
                                 const char *context) {
    char error[256];
    xp_program *prog = compile_expression(eng, source, length, error, sizeof(error));
    if (prog == NULL) {
        if (!PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: only allocation adds an exception during compilation */
            PyErr_Format(PyExc_ValueError, "xslt: bad %s: %s", context, error);
        }
        return fail_py(eng);
    }
    if (bind_expression_scope(eng, prog, element) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return fail_py(eng);                             /* GCOVR_EXCL_LINE */
    }
    return 0;
}

static int precompile_avt(engine *eng, th_node *element, const Py_UCS4 *source, Py_ssize_t length) {
    for (Py_ssize_t index = 0; index < length; index++) {
        if (source[index] != '{' || (index + 1 < length && source[index + 1] == '{')) {
            if (source[index] == '{') {
                index++;
            }
            continue;
        }
        Py_ssize_t start = ++index;
        Py_UCS4 quote = 0;
        while (index < length && (quote != 0 || source[index] != '}')) {
            if (quote != 0) {
                if (source[index] == quote) {
                    quote = 0;
                }
            } else if (source[index] == '\'' || source[index] == '"') {
                quote = source[index];
            }
            index++;
        }
        if (precompile_expression(eng, element, source + start, index - start,
                                  "expression in attribute value template") < 0) {
            return -1;
        }
    }
    return 0;
}

static int precompile_attribute(engine *eng, th_node *element, const char *name, const char *context) {
    Py_ssize_t length = 0;
    const Py_UCS4 *source = attr_lookup(eng->sheet_tree, element, name, (Py_ssize_t)strlen(name), &length);
    return source == NULL ? 0 : precompile_expression(eng, element, source, length, context);
}

static int precompile_pattern_attribute(engine *eng, th_node *element, const char *name) {
    Py_ssize_t length = 0;
    const Py_UCS4 *source = attr_lookup(eng->sheet_tree, element, name, (Py_ssize_t)strlen(name), &length);
    if (source == NULL) {
        return 0;
    }
    Py_ssize_t starts[64];
    Py_ssize_t lengths[64];
    int alternatives = split_union(source, length, starts, lengths, 64);
    if (alternatives < 0) {
        PyErr_Format(PyExc_ValueError, "xslt: xsl:number %s pattern has too many alternatives", name);
        return fail_py(eng);
    }
    for (int index = 0; index < alternatives; index++) {
        if (compile_pattern(eng, source + starts[index], lengths[index]) == NULL) {
            return -1;
        }
    }
    return 0;
}

static const char *select_context(enum xsl_instr kind) {
    switch (kind) {
    case XSL_APPLY_TEMPLATES:
        return "apply-templates select";
    case XSL_FOR_EACH:
        return "for-each select";
    case XSL_VALUE_OF:
        return "value-of select";
    case XSL_COPY_OF:
        return "copy-of select";
    case XSL_VARIABLE:
        return "variable select";
    case XSL_PARAM:
        return "param select";
    case XSL_WITH_PARAM:
        return "with-param select";
    case XSL_SORT:
        return "sort select";
    default:
        return NULL;
    }
}

static int precompile_instruction(engine *eng, th_node *element, enum xsl_instr kind) {
    const char *context = select_context(kind);
    if (context != NULL && precompile_attribute(eng, element, "select", context) < 0) {
        return -1;
    }
    if ((kind == XSL_IF || kind == XSL_WHEN) && precompile_attribute(eng, element, "test", "test") < 0) {
        return -1;
    }
    if (kind == XSL_NUMBER) {
        if (precompile_attribute(eng, element, "value", "number value") < 0 ||
            precompile_pattern_attribute(eng, element, "count") < 0 ||
            precompile_pattern_attribute(eng, element, "from") < 0) {
            return -1;
        }
    }
    int element_instruction = kind == XSL_ELEMENT;
    int attribute_instruction = kind == XSL_ATTRIBUTE;
    if (element_instruction || attribute_instruction || kind == XSL_PI) {
        Py_ssize_t length = 0;
        const Py_UCS4 *source = attr_lookup(eng->sheet_tree, element, "name", 4, &length);
        if (source != NULL && precompile_avt(eng, element, source, length) < 0) {
            return -1;
        }
    }
    if (element_instruction || attribute_instruction) {
        Py_ssize_t length = 0;
        const Py_UCS4 *source = attr_lookup(eng->sheet_tree, element, "namespace", 9, &length);
        if (source != NULL && precompile_avt(eng, element, source, length) < 0) {
            return -1;
        }
    }
    return 0;
}

static int precompile_literal_attributes(engine *eng, th_node *element) {
    for (Py_ssize_t index = 0; index < element->attr_count; index++) {
        const th_node_attr *attr = &element->attrs[index];
        Py_ssize_t name_len = 0;
        const char *name = th_attr_name(eng->sheet_tree, attr->name_atom, &name_len);
        if ((name_len == 5 && memcmp(name, "xmlns", 5) == 0) || (name_len >= 6 && memcmp(name, "xmlns:", 6) == 0) ||
            is_xsl_attr(eng, element, index)) {
            continue;
        }
        if (precompile_avt(eng, element, attr->value, attr->value_len) < 0) {
            return -1;
        }
    }
    return 0;
}

static int node_is_xsl(const engine *eng, const th_node *node) {
    return eng->xsl_marked ? marks_element_is_xsl(&eng->marks, node) : is_any_xsl_fast(eng, node);
}

static int precompile_stylesheet(engine *eng, th_node *root) {
    for (th_node *node = root; node != NULL; node = preorder_next(node, root)) {
        if (node->type != TH_NODE_ELEMENT) {
            continue;
        }
        TH_WORK_STEP();
#ifdef TH_OPERATION_LIMIT
        if (charge_operation(eng, "compiling the stylesheet") < 0) {
            return -1;
        }
#endif
        enum xsl_instr kind = element_kind(eng, node);
        if (th_node_map_insert(&eng->instructions, node, kind + 1) < 0) { /* GCOVR_EXCL_BR_LINE */
            return -1;                                                    /* GCOVR_EXCL_LINE */
        }
        if (node_is_xsl(eng, node)) {
            if (precompile_instruction(eng, node, kind) < 0) {
                return -1;
            }
        } else if (precompile_literal_attributes(eng, node) < 0) {
            return -1;
        }
    }
    return precompile_expression(eng, root, &XPATH_DOT, 1, "sort select");
}

/* Re-attach the stripped text nodes in reverse order, so each node's saved successor is
   already back in place, restoring the caller's source tree exactly. */
static void strip_restore(engine *eng) {
    for (Py_ssize_t index = eng->nstripped - 1; index >= 0; index--) {
        struct strip_entry *entry = &eng->stripped[index];
        if (entry->next != NULL) {
            th_node_insert_before(entry->parent, entry->node, entry->next);
        } else {
            th_node_append_child(entry->parent, entry->node);
        }
    }
}

/* ---- the module entry point ----------------------------------------------- */

static PyObject *run_transform(engine *eng, th_node *sheet_root, PyObject *params) {
    eng->out_tree = th_tree_new();
    if (eng->out_tree == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
    }
    th_node *out_root = th_tree_make_fragment(eng->out_tree);
    if (out_root == NULL) {      /* GCOVR_EXCL_BR_LINE: alloc */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
    }
    eng->cur_node = eng->src_root;
    eng->cur_attr = -1;
    eng->ctx_pos = 1;
    eng->ctx_size = 1;
    /* Whitespace stripping runs on the source tree before any query sees it; the detached
       nodes are restored below so the caller's tree survives the transform unchanged. */
    if (eng->nspaces > 0 && strip_walk(eng, eng->src_root, 0) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
        strip_restore(eng);                                          /* GCOVR_EXCL_LINE */
        return NULL;                                                 /* GCOVR_EXCL_LINE */
    }
    PyObject *result = NULL;
    int rc = bind_globals(eng, params);
    if (rc == 0) {
        rc = eng->simplified ? instantiate_root(eng, sheet_root, out_root)
                             : apply_to_item(eng, (xp_item){eng->src_root, -1}, 1, 1, NULL, 0, NULL, 0, out_root);
    }
    if (rc == 0) {
        auto_select_method(eng, out_root);
        if (eng->cdata_elements != NULL) {
            apply_cdata_sections(eng, out_root);
        }
        result = eng->output_method == OUT_TEXT ? serialize_text(eng, out_root) : serialize_markup(eng, out_root);
    }
    strip_restore(eng);
    return result;
}

/* The stylesheet's document element (xsl:stylesheet/xsl:transform, or a literal result element
   for a simplified stylesheet), or NULL when the tree holds no root element. */
static th_node *stylesheet_root(th_node *node) {
    if (node->type != TH_NODE_DOCUMENT) {
        return node->type == TH_NODE_ELEMENT ? node : NULL;
    }
    /* A parsed document always holds a root element, so the loop always breaks. */
    for (th_node *child = node->first_child; child != NULL; /* GCOVR_EXCL_BR_LINE */
         child = child->next_sibling) {
        if (child->type == TH_NODE_ELEMENT) {
            return child;
        }
    }
    return NULL; /* GCOVR_EXCL_LINE: an XML document always has a root element */
}

TH_NODE_API(static, PyObject *, stylesheet_import_hrefs,
            (PyObject * module, PyObject *stylesheet, PyObject *base, int allow_imports),
            (module, stylesheet, base, allow_imports),
            (PyObject * module, PyObject *stylesheet, PyObject *base, int allow_imports),
            is_node(stylesheet, PyModule_GetState(module)) ? (NodeObject *)stylesheet : NULL, NULL) {
    PyObject *hrefs = PyList_New(0);
    if (hrefs == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;     /* GCOVR_EXCL_LINE */
    }
    th_tree *tree;
    th_node *node;
    if (turbohtml_node_borrow(module, stylesheet, &tree, &node) < 0) {
        Py_DECREF(hrefs);
        return NULL;
    }
    PyObject *handle = turbohtml_node_handle(stylesheet);
    (void)handle;
    int error = 0;
    xslt_marks marks = {0};
    int marked = 0;
    Py_BEGIN_CRITICAL_SECTION(handle);
    th_node *root = stylesheet_root(node);
    if (root != NULL) {
        for (th_node *child = root->first_child; child != NULL; child = child->next_sibling) {
            if (child->type != TH_NODE_ELEMENT) {
                continue;
            }
            Py_ssize_t local_len;
            Py_ssize_t prefix_len;
            const Py_UCS4 *local = qname_local(child, &local_len, &prefix_len);
            if (!ucs4_ascii_eq(local, local_len, "import")) {
                continue;
            }
            if (!marked) {
                if (marks_build(&marks, tree, root) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                    PyErr_NoMemory();                      /* GCOVR_EXCL_LINE */
                    error = 1;                             /* GCOVR_EXCL_LINE */
                    break;                                 /* GCOVR_EXCL_LINE */
                }
                marked = 1;
            }
            if (!marks_element_is_xsl(&marks, child)) {
                continue;
            }
            if (!allow_imports) {
                PyErr_SetString(PyExc_ValueError, "xsl:import is disabled");
                error = 1;
                break;
            }
            if (base == Py_None) {
                PyErr_SetString(PyExc_ValueError,
                                "xsl:import needs a base_url to resolve the imported stylesheet's href against");
                error = 1;
                break;
            }
            Py_ssize_t href_len = 0;
            const Py_UCS4 *href_value = attr_lookup(tree, child, "href", 4, &href_len);
            if (href_value == NULL) {
                PyErr_SetString(PyExc_ValueError, "xsl:import requires an href attribute");
                error = 1;
                break;
            }
            PyObject *href = make_str(href_value, href_len);
            if (href == NULL || PyList_Append(hrefs, href) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                Py_XDECREF(href);                                 /* GCOVR_EXCL_LINE */
                error = 1;                                        /* GCOVR_EXCL_LINE */
                break;                                            /* GCOVR_EXCL_LINE */
            }
            Py_DECREF(href);
        }
    }
    Py_END_CRITICAL_SECTION();
    marks_free(&marks);
    if (error) {
        Py_DECREF(hrefs);
        return NULL;
    }
    return hrefs;
}

static PyObject *load_stylesheet_import(PyObject *module, th_resource_policy *policy, PyObject *base, PyObject *href) {
    PyObject *current_path = th_resource_path(policy, base, "base_url");
    if (current_path == NULL) {
        return NULL;
    }
    PyObject *current = th_resource_resolve(current_path);
    Py_DECREF(current_path);
    if (current == NULL) {
        return NULL;
    }
    PyObject *parent = PyObject_GetAttrString(current, "parent");
    PyObject *href_path = th_resource_path(policy, href, "href");
    if (parent == NULL) {      /* GCOVR_EXCL_BR_LINE: pathlib paths expose parent */
        Py_XDECREF(href_path); /* GCOVR_EXCL_LINE */
        Py_DECREF(current);    /* GCOVR_EXCL_LINE */
        return NULL;           /* GCOVR_EXCL_LINE */
    }
    if (href_path == NULL) {
        Py_DECREF(parent);
        Py_DECREF(current);
        return NULL;
    }
    PyObject *joined = PyNumber_TrueDivide(parent, href_path);
    Py_DECREF(href_path);
    Py_DECREF(parent);
    if (joined == NULL) {   /* GCOVR_EXCL_BR_LINE: pathlib joins fail only on allocation */
        Py_DECREF(current); /* GCOVR_EXCL_LINE */
        return NULL;        /* GCOVR_EXCL_LINE */
    }
    PyObject *path = th_resource_resolve(joined);
    if (path == NULL) {     /* GCOVR_EXCL_BR_LINE: Path.resolve fails here only on allocation */
        Py_DECREF(joined);  /* GCOVR_EXCL_LINE */
        Py_DECREF(current); /* GCOVR_EXCL_LINE */
        return NULL;        /* GCOVR_EXCL_LINE */
    }
    int confined = th_resource_check_root(policy, path, joined);
    Py_DECREF(joined);
    if (confined < 0) {
        Py_DECREF(path);
        Py_DECREF(current);
        return NULL;
    }
    PyObject *text = th_resource_read_text(policy, path);
    if (text == NULL) {
        Py_DECREF(path);
        Py_DECREF(current);
        return NULL;
    }
    PyObject *parse_xml = PyObject_GetAttrString(module, "parse_xml");
    if (parse_xml == NULL) { /* GCOVR_EXCL_BR_LINE: the extension module exposes parse_xml */
        Py_DECREF(text);     /* GCOVR_EXCL_LINE */
        Py_DECREF(path);     /* GCOVR_EXCL_LINE */
        Py_DECREF(current);  /* GCOVR_EXCL_LINE */
        return NULL;         /* GCOVR_EXCL_LINE */
    }
    PyObject *stylesheet = PyObject_CallOneArg(parse_xml, text);
    Py_DECREF(parse_xml);
    Py_DECREF(text);
    if (stylesheet == NULL) {
        Py_DECREF(path);
        Py_DECREF(current);
        return NULL;
    }
    PyObject *loaded = PyTuple_Pack(3, stylesheet, path, current);
    Py_DECREF(stylesheet);
    Py_DECREF(path);
    Py_DECREF(current);
    return loaded;
}

static int raise_import_cycle(PyObject *active, PyObject *path) {
    Py_ssize_t count = PyList_GET_SIZE(active);
    PyObject *parts = PyList_New(count + 1);
    if (parts == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;       /* GCOVR_EXCL_LINE */
    }
    for (Py_ssize_t index = 0; index < count; index++) {
        PyObject *part = PyObject_Str(PyList_GET_ITEM(active, index));
        if (part == NULL) {   /* GCOVR_EXCL_BR_LINE: Path.__str__ only allocates */
            Py_DECREF(parts); /* GCOVR_EXCL_LINE */
            return -1;        /* GCOVR_EXCL_LINE */
        }
        PyList_SET_ITEM(parts, index, part);
    }
    PyObject *part = PyObject_Str(path);
    if (part == NULL) {   /* GCOVR_EXCL_BR_LINE: Path.__str__ only allocates */
        Py_DECREF(parts); /* GCOVR_EXCL_LINE */
        return -1;        /* GCOVR_EXCL_LINE */
    }
    PyList_SET_ITEM(parts, count, part);
    PyObject *separator = PyUnicode_FromString(" -> ");
    if (separator == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        Py_DECREF(parts);    /* GCOVR_EXCL_LINE */
        return -1;           /* GCOVR_EXCL_LINE */
    }
    PyObject *chain = PyUnicode_Join(separator, parts);
    Py_XDECREF(separator);
    Py_DECREF(parts);
    if (chain == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;       /* GCOVR_EXCL_LINE */
    }
    PyErr_Format(PyExc_ValueError, "circular xsl:import: %U", chain);
    Py_DECREF(chain);
    return -1;
}

static int resolve_stylesheet_imports(PyObject *module, PyObject *stylesheet, PyObject *base,
                                      th_resource_policy *policy, PyObject *imports, PyObject *active,
                                      PyObject *active_set) {
    PyObject *hrefs = stylesheet_import_hrefs(module, stylesheet, base, 1);
    if (hrefs == NULL) {
        return -1;
    }
    Py_ssize_t count = PyList_GET_SIZE(hrefs);
    for (Py_ssize_t index = 0; index < count; index++) {
        PyObject *loaded = load_stylesheet_import(module, policy, base, PyList_GET_ITEM(hrefs, index));
        if (loaded == NULL) {
            Py_DECREF(hrefs);
            return -1;
        }
        PyObject *imported = PyTuple_GET_ITEM(loaded, 0);
        PyObject *next_base = PyTuple_GET_ITEM(loaded, 1);
        if (PyList_GET_SIZE(active) == 0) {
            PyObject *current_path = PyTuple_GET_ITEM(loaded, 2);
            if (PyList_Append(active, current_path) < 0) { /* GCOVR_EXCL_START: allocation failure */
                Py_DECREF(loaded);
                Py_DECREF(hrefs);
                return -1;
            } /* GCOVR_EXCL_STOP */
            if (PyDict_SetItem(active_set, current_path, Py_None) < 0) { /* GCOVR_EXCL_START: allocation failure */
                Py_DECREF(loaded);
                Py_DECREF(hrefs);
                return -1;
            } /* GCOVR_EXCL_STOP */
        }
        int contains = PyDict_Contains(active_set, next_base);
        if (contains < 0) { /* GCOVR_EXCL_START: canonical Path hashes do not fail */
            Py_DECREF(loaded);
            Py_DECREF(hrefs);
            return -1;
        } /* GCOVR_EXCL_STOP */
        if (contains) {
            int status = raise_import_cycle(active, next_base);
            Py_DECREF(loaded);
            Py_DECREF(hrefs);
            return status;
        }
        if (PyList_Append(active, next_base) < 0) { /* GCOVR_EXCL_START: allocation failure */
            Py_DECREF(loaded);
            Py_DECREF(hrefs);
            return -1;
        } /* GCOVR_EXCL_STOP */
        if (PyDict_SetItem(active_set, next_base, Py_None) < 0) { /* GCOVR_EXCL_START: allocation failure */
            Py_DECREF(loaded);
            Py_DECREF(hrefs);
            return -1;
        } /* GCOVR_EXCL_STOP */
        if (Py_EnterRecursiveCall(" while resolving xsl:import") != 0) { /* GCOVR_EXCL_START: recursion limit */
            Py_DECREF(loaded);
            Py_DECREF(hrefs);
            return -1;
        } /* GCOVR_EXCL_STOP */
        int status = resolve_stylesheet_imports(module, imported, next_base, policy, imports, active, active_set);
        Py_LeaveRecursiveCall();
        if (status == 0) {
            (void)PyDict_DelItem(active_set, next_base);
            (void)PySequence_DelItem(active, PyList_GET_SIZE(active) - 1);
            status = PyList_Append(imports, imported);
        }
        Py_DECREF(loaded);
        if (status < 0) {
            Py_DECREF(hrefs);
            return -1;
        }
    }
    Py_DECREF(hrefs);
    return 0;
}

PyObject *turbohtml_xslt_resolve_imports(PyObject *module, PyObject *args) {
    PyObject *stylesheet;
    PyObject *base = Py_None;
    PyObject *import_root = Py_None;
    int allow_imports = 1;
    /* GCOVR_EXCL_BR_START */
    if (!PyArg_ParseTuple(args, "O|OpO:_xslt_resolve_imports", &stylesheet, &base, &allow_imports, &import_root)) {
        return NULL; /* GCOVR_EXCL_LINE: private typed boundary */
    }
    /* GCOVR_EXCL_BR_STOP */
    PyObject *hrefs = stylesheet_import_hrefs(module, stylesheet, base, allow_imports);
    if (hrefs == NULL) {
        return NULL;
    }
    if (PyList_GET_SIZE(hrefs) == 0) {
        Py_DECREF(hrefs);
        Py_RETURN_NONE;
    }
    Py_DECREF(hrefs);
    th_resource_policy policy;
    if (th_resource_policy_init(&policy, "xsl:import", "import_root", import_root) < 0) {
        return NULL;
    }
    PyObject *imports = PyList_New(0);
    PyObject *active = PyList_New(0);
    PyObject *active_set = PyDict_New();
    if (imports == NULL || active == NULL || active_set == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        Py_XDECREF(imports);                                       /* GCOVR_EXCL_LINE */
        Py_XDECREF(active);                                        /* GCOVR_EXCL_LINE */
        Py_XDECREF(active_set);                                    /* GCOVR_EXCL_LINE */
        th_resource_policy_clear(&policy);                         /* GCOVR_EXCL_LINE */
        return NULL;                                               /* GCOVR_EXCL_LINE */
    }
    int status = resolve_stylesheet_imports(module, stylesheet, base, &policy, imports, active, active_set);
    th_resource_policy_clear(&policy);
    Py_DECREF(active_set);
    Py_DECREF(active);
    if (status < 0) {
        Py_DECREF(imports);
        return NULL;
    }
    return imports;
}

typedef struct {
    engine model;
    th_node *sheet_root;
} xslt_compiled;

static const char XSLT_CAPSULE[] = "turbohtml.XSLT";

static void xslt_compiled_free(PyObject *capsule) {
    xslt_compiled *compiled = PyCapsule_GetPointer(capsule, XSLT_CAPSULE);
    if (compiled == NULL) { /* GCOVR_EXCL_START: destructor only receives its own capsule */
        PyErr_Clear();
        return;
    } /* GCOVR_EXCL_STOP */
    engine_clear(&compiled->model);
    PyMem_Free(compiled);
}

static int snapshot_principal_stylesheet(xslt_compiled *compiled, PyObject *stylesheet_obj, th_tree *sheet_tree,
                                         th_node *sheet_node) {
    (void)stylesheet_obj; /* used by the critical-section macro only on free-threaded builds */
    compiled->model.merged_tree = th_tree_new();
    if (compiled->model.merged_tree == NULL) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
        return -1;                             /* GCOVR_EXCL_LINE */
    }
    int has_root = 0;
    Py_BEGIN_CRITICAL_SECTION(turbohtml_node_handle(stylesheet_obj));
    th_node *sheet_root = stylesheet_root(sheet_node);
    if (sheet_root != NULL) {
        has_root = 1;
        compiled->sheet_root = th_tree_copy_node(compiled->model.merged_tree, sheet_tree, sheet_root);
    }
    Py_END_CRITICAL_SECTION();
    if (!has_root) {
        PyErr_SetString(PyExc_ValueError, "xslt: the stylesheet has no root element");
        return -1;
    }
    if (compiled->sheet_root == NULL) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
        return -1;                      /* GCOVR_EXCL_LINE */
    }
    compiled->model.sheet_tree = compiled->model.merged_tree;
    return 0;
}

static int copy_imports(PyObject *module, xslt_compiled *compiled, PyObject *imports_obj, th_node ***out_imports,
                        Py_ssize_t *out_nimports) {
    Py_ssize_t nimports = imports_obj == Py_None ? 0 : PyList_GET_SIZE(imports_obj);
    th_node **imports = nimports == 0 ? NULL : PyMem_Malloc((size_t)nimports * sizeof(th_node *));
    if (nimports > 0 && imports == NULL) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
        return -1;                         /* GCOVR_EXCL_LINE */
    }
    for (Py_ssize_t index = 0; index < nimports; index++) {
        PyObject *item = PyList_GET_ITEM(imports_obj, index);
        th_tree *import_tree;
        th_node *import_node;
        /* The resolver returns only parse_xml results. */
        /* GCOVR_EXCL_BR_START */
        if (turbohtml_node_borrow(module, item, &import_tree, &import_node) < 0) {
            PyMem_Free(imports); /* GCOVR_EXCL_LINE */
            return -1;           /* GCOVR_EXCL_LINE */
        }
        /* GCOVR_EXCL_BR_STOP */
        int has_root = 0;
        Py_BEGIN_CRITICAL_SECTION(turbohtml_node_handle(item));
        th_node *import_root = stylesheet_root(import_node);
        if (import_root != NULL) { /* GCOVR_EXCL_BR_LINE: parse_xml rejects imported documents without a root */
            has_root = 1;
            imports[index] = th_tree_copy_node(compiled->model.merged_tree, import_tree, import_root);
        }
        Py_END_CRITICAL_SECTION();
        if (!has_root) { /* GCOVR_EXCL_START: imports are parsed XML documents */
            PyMem_Free(imports);
            PyErr_SetString(PyExc_ValueError, "xslt: an imported stylesheet has no root element");
            return -1;
        } /* GCOVR_EXCL_STOP */
        if (imports[index] == NULL) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
            PyMem_Free(imports);      /* GCOVR_EXCL_LINE */
            return -1;                /* GCOVR_EXCL_LINE */
        }
    }
    *out_imports = imports;
    *out_nimports = nimports;
    return 0;
}

TH_NODE_API(, PyObject *, turbohtml_xslt_compile, (PyObject * module, PyObject *args), (module, args),
            (PyObject * module, PyObject *args), node_argument(PyModule_GetState(module), args, NULL, 0, NULL), NULL) {
    PyObject *stylesheet_obj;
    PyObject *imports_obj = Py_None;
    /* The typed facade fixes this private signature. */
    /* GCOVR_EXCL_BR_START */
    if (!PyArg_ParseTuple(args, "O|O:_xslt_compile", &stylesheet_obj, &imports_obj)) {
        return NULL; /* GCOVR_EXCL_LINE: private typed boundary */
    }
    /* GCOVR_EXCL_BR_STOP */
    th_tree *sheet_tree;
    th_node *sheet_node;
    /* _xslt_resolve_imports validates this facade argument first. */
    /* GCOVR_EXCL_BR_START */
    if (turbohtml_node_borrow(module, stylesheet_obj, &sheet_tree, &sheet_node) < 0) {
        return NULL; /* GCOVR_EXCL_LINE */
    }
    /* GCOVR_EXCL_BR_STOP */
    xslt_compiled *compiled = PyMem_Calloc(1, sizeof(xslt_compiled));
    if (compiled == NULL) {      /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
    }
    compiled->model.module = module;
    compiled->model.output_method = OUT_XML;
    compiled->model.cur_attr = -1;
    compiled->model.owns_model = 1;
    if (snapshot_principal_stylesheet(compiled, stylesheet_obj, sheet_tree, sheet_node) < 0) {
        engine_clear(&compiled->model);
        PyMem_Free(compiled);
        /* GCOVR_EXCL_START */
        return PyErr_Occurred() ? NULL
                                : PyErr_NoMemory(); /* GCOVR_EXCL_BR_LINE: the second path is allocation failure */
        /* GCOVR_EXCL_STOP */
    }
    th_node **imports = NULL;
    Py_ssize_t nimports = 0;
    int status = copy_imports(module, compiled, imports_obj, &imports, &nimports);
    if (status == 0) { /* GCOVR_EXCL_BR_LINE: copy_imports fails only on allocation or an invalid parser result */
        status = analyze(&compiled->model, compiled->sheet_root, imports, nimports);
    }
    if (status == 0) {
        status = build_name_indexes(&compiled->model);
    }
    if (status == 0) {
        status = build_rule_index(&compiled->model);
    }
    if (status == 0) {
        status = resolve_attribute_sets(&compiled->model);
    }
    for (Py_ssize_t index = 0; status == 0 && index < nimports; index++) {
        status = precompile_stylesheet(&compiled->model, imports[index]);
    }
    if (status == 0) {
        status = precompile_stylesheet(&compiled->model, compiled->sheet_root);
    }
    PyMem_Free(imports);
    if (status < 0) {
        /* Allocation is the only error without an engine message or Python exception. */
        /* GCOVR_EXCL_BR_START */
        if (compiled->model.error != NULL && !PyErr_Occurred()) {
            PyErr_Format(PyExc_ValueError, "%s", compiled->model.error);
        } else if (!PyErr_Occurred()) {
            PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
        } /* GCOVR_EXCL_LINE */
        /* GCOVR_EXCL_BR_STOP */
        engine_clear(&compiled->model);
        PyMem_Free(compiled);
        return NULL;
    }
    compiled->model.number_attrs.count = th_attr_lookup(compiled->model.sheet_tree, "count", 5);
    compiled->model.number_attrs.from = th_attr_lookup(compiled->model.sheet_tree, "from", 4);
    compiled->model.number_attrs.level = th_attr_lookup(compiled->model.sheet_tree, "level", 5);
    compiled->model.number_attrs.format = th_attr_lookup(compiled->model.sheet_tree, "format", 6);
    compiled->model.number_attrs.grouping_separator =
        th_attr_lookup(compiled->model.sheet_tree, "grouping-separator", 18);
    compiled->model.number_attrs.grouping_size = th_attr_lookup(compiled->model.sheet_tree, "grouping-size", 13);
    PyObject *capsule = PyCapsule_New(compiled, XSLT_CAPSULE, xslt_compiled_free);
    if (capsule == NULL) {              /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
        engine_clear(&compiled->model); /* GCOVR_EXCL_LINE */
        PyMem_Free(compiled);           /* GCOVR_EXCL_LINE */
    } /* GCOVR_EXCL_LINE */
    return capsule;
}

TH_NODE_API(, PyObject *, turbohtml_xslt_transform, (PyObject * module, PyObject *args), (module, args),
            (PyObject * module, PyObject *args), node_argument(PyModule_GetState(module), args, NULL, 1, NULL), NULL) {
    PyObject *compiled_obj;
    PyObject *source_obj;
    PyObject *params = Py_None;
    if (!PyArg_ParseTuple(args, "OO|O:_xslt_transform", &compiled_obj, &source_obj, &params)) {
        return NULL;
    }
    if (params == Py_None) {
        params = NULL;
    } else if (!PyDict_Check(params)) {
        PyErr_SetString(PyExc_TypeError, "xslt: params must be a dict or None");
        return NULL;
    }
    if (/* GCOVR_EXCL_BR_LINE: Transform stores only the compile capsule */ !PyCapsule_CheckExact(compiled_obj)) {
        PyErr_SetString(PyExc_TypeError, "xslt: expected a compiled Transform"); /* GCOVR_EXCL_LINE */
        return NULL;                                                             /* GCOVR_EXCL_LINE */
    }
    xslt_compiled *compiled = PyCapsule_GetPointer(compiled_obj, XSLT_CAPSULE);
    if (compiled == NULL) { /* GCOVR_EXCL_BR_LINE: Transform stores only this capsule name */
        return NULL;        /* GCOVR_EXCL_LINE */
    }
    th_tree *src_tree;
    th_node *src_node;
    if (turbohtml_node_borrow(module, source_obj, &src_tree, &src_node) < 0) {
        return NULL;
    }
    engine eng = {0};
    PyObject *source_handle = Py_NewRef(turbohtml_node_handle(source_obj));
    PyObject *result = NULL;
    Py_BEGIN_CRITICAL_SECTION(source_handle);
    if (engine_start_run(&eng, &compiled->model, src_tree, src_node) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
        PyErr_NoMemory();                                                   /* GCOVR_EXCL_LINE */
    } else { /* GCOVR_EXCL_LINE: allocation-failure block end */
        result = run_transform(&eng, compiled->sheet_root, params);
    }
    /* whitespace stripping detaches the source's blank Text nodes for the run, with no index drop */
    handle_drop_slots((HandleObject *)source_handle);
    Py_END_CRITICAL_SECTION();
    /* fail() sets eng.error without a Python exception; fail_py() sets the exception and
       leaves eng.error NULL. The two are exclusive, so eng.error != NULL implies no
       exception is set and the message needs raising. */
    if (result == NULL && eng.error != NULL) {
        PyErr_Format(PyExc_ValueError, "%s", eng.error);
    }
    engine_clear(&eng);
    Py_DECREF(source_handle);
    return result;
}
