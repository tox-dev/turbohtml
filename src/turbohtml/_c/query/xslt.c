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
#include "core/vec.h"
#include "dom/nodes.h"
#include "dom/tree.h"
#include "tokenizer/binding.h"
#include "query/xpath/internal.h"

#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <stdint.h>
#include <string.h>

#ifdef _WIN32
#include <io.h>
#include <windows.h>
#include <wchar.h>
#else
#include <unistd.h>
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

/* Encode a UCS-4 run as UTF-8 into a freshly PyMem-allocated NUL-terminated buffer,
   the form the tree attribute API and atom lookups take. NULL on allocation failure. */
static char *ucs4_to_utf8(const Py_UCS4 *src, Py_ssize_t len, Py_ssize_t *out_len) {
    char *out = PyMem_Malloc((size_t)len * 4 + 1);
    if (out == NULL) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
        return NULL;   /* GCOVR_EXCL_LINE */
    }
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
    *out_len = pos;
    return out;
}

/* The interned tag atom for a result element name, so an HTML-method serialization
   treats a known void element (br, img, ...) correctly. TH_TAG_UNKNOWN for a name
   that is not a known HTML tag; the name is matched ASCII-case-insensitively. */
static uint16_t atom_for_name(const Py_UCS4 *name, Py_ssize_t len) {
    if (len == 0 || len > 64) {
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

static size_t ptr_hash(const void *ptr, Py_ssize_t attr) {
    size_t value = (size_t)(uintptr_t)ptr;
    value ^= (size_t)attr * 0x9E3779B97F4A7C15ULL;
    value *= 0xff51afd7ed558ccdULL;
    value ^= value >> 33;
    return value;
}

static int match_set_grow(match_set *set) {
    size_t new_cap = set->cap == 0 ? 16 : set->cap * 2;
    match_slot *slots = PyMem_Calloc(new_cap, sizeof(match_slot));
    if (slots == NULL) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
        return -1;       /* GCOVR_EXCL_LINE */
    }
    for (size_t index = 0; index < set->cap; index++) {
        if (set->slots[index].used) {
            size_t probe = ptr_hash(set->slots[index].node, set->slots[index].attr) & (new_cap - 1);
            while (slots[probe].used) {
                probe = (probe + 1) & (new_cap - 1);
            }
            slots[probe] = set->slots[index];
        }
    }
    PyMem_Free(set->slots);
    set->slots = slots;
    set->cap = new_cap;
    return 0;
}

static int match_set_add(match_set *set, const th_node *node, Py_ssize_t attr) {
    if ((set->count + 1) * 2 >= set->cap) {
        if (match_set_grow(set) < 0) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
            return -1;                 /* GCOVR_EXCL_LINE */
        }
    }
    size_t probe = ptr_hash(node, attr) & (set->cap - 1);
    while (set->slots[probe].used) {
        /* A rule's match set is built from one duplicate-free pattern evaluation, so the
           same item is never re-added; the dedup guard is defensive. */
        if (set->slots[probe].node == node && set->slots[probe].attr == attr) { /* GCOVR_EXCL_BR_LINE */
            return 0;                                                           /* GCOVR_EXCL_LINE */
        } /* GCOVR_EXCL_LINE */
        probe = (probe + 1) & (set->cap - 1);
    }
    set->slots[probe].node = node;
    set->slots[probe].attr = attr;
    set->slots[probe].used = 1;
    set->count++;
    return 0;
}

static int match_set_has(const match_set *set, const th_node *node, Py_ssize_t attr) {
    if (set->cap == 0) {
        return 0;
    }
    size_t probe = ptr_hash(node, attr) & (set->cap - 1);
    while (set->slots[probe].used) {
        /* The attr comparison separates attribute items of one element; reaching its
           false arm needs a probe to land on a same-node different-attr slot, a hash
           collision a test cannot arrange deterministically. */
        if (set->slots[probe].node == node && set->slots[probe].attr == attr) { /* GCOVR_EXCL_BR_LINE */
            return 1;
        }
        probe = (probe + 1) & (set->cap - 1);
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
    th_node **nodes;
    Py_ssize_t len;
    Py_ssize_t cap;
} nodevec;

static int nodevec_push(nodevec *vec, th_node *node) {
    for (Py_ssize_t index = 0; index < vec->len; index++) {
        if (vec->nodes[index] == node) {
            return 0;
        }
    }
    if (vec->len == vec->cap) {
        Py_ssize_t cap = vec->cap == 0 ? 4 : vec->cap * 2;
        th_node **grown = PyMem_Realloc(vec->nodes, (size_t)cap * sizeof(th_node *));
        if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
            return -1;       /* GCOVR_EXCL_LINE */
        }
        vec->nodes = grown;
        vec->cap = cap;
    }
    vec->nodes[vec->len++] = node;
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
} strmap;

static size_t str_hash(const Py_UCS4 *str, Py_ssize_t len) {
    size_t value = 1469598103934665603ULL;
    for (Py_ssize_t index = 0; index < len; index++) {
        value ^= str[index];
        value *= 1099511628211ULL;
    }
    return value;
}

static int str_eq(const Py_UCS4 *left, Py_ssize_t left_len, const Py_UCS4 *right, Py_ssize_t right_len) {
    return left_len == right_len && memcmp(left, right, (size_t)left_len * sizeof(Py_UCS4)) == 0;
}

static int strmap_grow(strmap *map) {
    size_t new_cap = map->cap == 0 ? 16 : map->cap * 2;
    strmap_slot *slots = PyMem_Calloc(new_cap, sizeof(strmap_slot));
    if (slots == NULL) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
        return -1;       /* GCOVR_EXCL_LINE */
    }
    for (size_t index = 0; index < map->cap; index++) {
        if (map->slots[index].used) {
            size_t probe = str_hash(map->slots[index].str, map->slots[index].str_len) & (new_cap - 1);
            while (slots[probe].used) {
                probe = (probe + 1) & (new_cap - 1);
            }
            slots[probe] = map->slots[index];
        }
    }
    PyMem_Free(map->slots);
    map->slots = slots;
    map->cap = new_cap;
    return 0;
}

/* Return the node-vector for a key string, creating the slot (copying str) if absent.
   NULL on allocation failure. */
static nodevec *strmap_bucket(strmap *map, const Py_UCS4 *str, Py_ssize_t str_len) {
    if ((map->count + 1) * 2 >= map->cap) {
        if (strmap_grow(map) < 0) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
            return NULL;            /* GCOVR_EXCL_LINE */
        }
    }
    size_t probe = str_hash(str, str_len) & (map->cap - 1);
    while (map->slots[probe].used) {
        if (str_eq(map->slots[probe].str, map->slots[probe].str_len, str, str_len)) {
            return &map->slots[probe].nodes;
        }
        probe = (probe + 1) & (map->cap - 1);
    }
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

static const nodevec *strmap_lookup(const strmap *map, const Py_UCS4 *str, Py_ssize_t str_len) {
    if (map->cap == 0) {
        return NULL;
    }
    size_t probe = str_hash(str, str_len) & (map->cap - 1);
    while (map->slots[probe].used) {
        if (str_eq(map->slots[probe].str, map->slots[probe].str_len, str, str_len)) {
            return &map->slots[probe].nodes;
        }
        probe = (probe + 1) & (map->cap - 1);
    }
    return NULL;
}

static void strmap_free(strmap *map) {
    for (size_t index = 0; index < map->cap; index++) {
        if (map->slots[index].used) {
            PyMem_Free(map->slots[index].str);
            PyMem_Free(map->slots[index].nodes.nodes);
        }
    }
    PyMem_Free(map->slots);
    map->slots = NULL;
    map->cap = 0;
    map->count = 0;
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
    xp_program *match_prog;
    xp_program *use_prog;
    strmap table;
    int built;
} xslt_key;

typedef struct {
    Py_UCS4 *name;
    Py_ssize_t name_len;
    xp_result value;
    th_node *rtf;
} var_bind;

/* A named xsl:attribute-set (section 7.1.4). Its body holds xsl:attribute children; the
   precedence orders redefinitions across import boundaries (higher importer wins). */
typedef struct {
    const Py_UCS4 *name;
    Py_ssize_t name_len;
    th_node *body;
    int precedence;
} xslt_attrset;

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

/* A source text node detached by whitespace stripping (section 3.4), kept so the caller's
   tree is restored to its original shape after the transform returns. */
struct strip_entry {
    th_node *node;
    th_node *parent;
    th_node *next; /* the sibling that followed node, or NULL when node was the last child */
};

enum output_method { OUT_XML, OUT_HTML, OUT_TEXT };

typedef struct engine {
    PyObject *module;
    th_tree *src_tree;
    th_tree *sheet_tree;
    th_tree *out_tree;
    th_tree *merged_tree; /* holds copies of the principal + imported stylesheets when importing */
    th_node *src_root;

    xslt_rule *rules;
    Py_ssize_t nrules;
    Py_ssize_t rules_cap;
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
    int ns_counter; /* serial for the generated ns_N prefixes xsl:attribute namespace fixup mints */
    int precedence; /* import precedence being assigned as declarations are walked */

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

    /* One level's number is its preceding matching siblings plus one, so numbering a run of siblings rescans the
       whole run for each of them and costs O(n^2) over the run. The memo carries the previous answer forward:
       level_number(node) is level_number(node->prev_sibling) plus whether that sibling counted. It holds the
       criteria the answer was computed under, because the default criteria follow the current node's type and
       name, and a run numbered under different criteria cannot reuse it. */
    const th_node *number_memo_node;
    long number_memo_value;
    /* The xsl:number element the memo was taken for. The count set itself is a local of the instruction handler, so
       its address repeats across calls and cannot identify the criteria; the instruction can, since its count
       attribute is fixed and two instructions are two nodes. */
    const th_node *number_memo_instruction;
    int number_memo_type;
    const Py_UCS4 *number_memo_name;
    Py_ssize_t number_memo_name_len;

    const char *error;
    int py_error;
    int owns_model;
} engine;

/* A cap on template-instantiation nesting (recursive apply-templates / named-template
   calls, xsl:for-each and result-tree construction). The transform recurses in C, so
   this guard turns a runaway or pathologically deep stylesheet into a clean
   RecursionError instead of a C stack overflow. It is sized well below the depth that
   overflows a small (~256 KB) thread stack -- each nesting level costs about half a
   kilobyte, so 400 levels stay under ~200 KB with a wide safety margin over the frame
   growth other compilers produce. Deep list processing should use xsl:for-each, which
   iterates rather than recursing. */
#define XSLT_MAX_DEPTH 400

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

static int node_prefix_is_xsl(th_tree *tree, const th_node *node, Py_ssize_t prefix_len) {
    for (const th_node *ancestor = node; ancestor != NULL; ancestor = ancestor->parent) {
        for (Py_ssize_t index = 0; index < ancestor->attr_count; index++) {
            const th_node_attr *attr = &ancestor->attrs[index];
            Py_ssize_t name_len = 0;
            const char *name = th_attr_name(tree, attr->name_atom, &name_len);
            int matches = prefix_len == 0 ? name_len == 5 && memcmp(name, "xmlns", 5) == 0
                                          : name_len == prefix_len + 6 && memcmp(name, "xmlns:", 6) == 0;
            for (Py_ssize_t offset = 0; matches && offset < prefix_len; offset++) {
                matches = (Py_UCS4)(unsigned char)name[offset + 6] == node->text[offset];
            }
            if (matches) {
                return ucs4_ascii_eq(attr->value, attr->value_len, XSLT_NS);
            }
        }
    }
    return 0;
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
    return ucs4_ascii_eq(name, local_len, local) && node_prefix_is_xsl(eng->sheet_tree, node, prefix_len);
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
    Py_ssize_t local_len;
    Py_ssize_t prefix_len;
    (void)qname_local(node, &local_len, &prefix_len);
    return node_prefix_is_xsl(eng->sheet_tree, node, prefix_len);
}

/* The value of node's attribute named `name` (ASCII), or NULL when absent. Returns a
   borrowed pointer into the tree; *out_len receives the length. A valueless attribute
   reports an empty (non-NULL) run. */
static const Py_UCS4 *attr_lookup(th_tree *tree, const th_node *node, const char *name, Py_ssize_t *out_len) {
    Py_ssize_t index = th_node_attr_find(tree, (th_node *)node, name, (Py_ssize_t)strlen(name));
    if (index < 0) {
        return NULL;
    }
    const th_node_attr *attr = &node->attrs[index];
    static const Py_UCS4 empty = 0;
    /* XML forbids a valueless attribute, so a parse_xml stylesheet never has one. */
    if (attr->value == NULL) { /* GCOVR_EXCL_BR_LINE */
        *out_len = 0;          /* GCOVR_EXCL_LINE */
        return &empty;         /* GCOVR_EXCL_LINE */
    }
    *out_len = attr->value_len;
    return attr->value;
}

/* ---- error helpers -------------------------------------------------------- */

static int fail(engine *eng, const char *message) {
    eng->error = message;
    return -1;
}

static int fail_py(engine *eng) {
    eng->py_error = 1;
    return -1;
}

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

/* Compile one pattern alternative as an absolute XPath expression whose result set
   equals the nodes the pattern matches: a relative pattern gains a leading
   "//" (descendant-or-self), an already-anchored one (/, //, id(, key() ) is used
   verbatim. Returns the program, or NULL with eng->error / eng->py_error set. */
static xp_program *compile_pattern_new(engine *eng, const Py_UCS4 *src, Py_ssize_t len) {
    Py_ssize_t start;
    Py_ssize_t trimmed;
    trim(src, len, &start, &trimmed);
    const Py_UCS4 *pattern = src + start;
    xb expr = {0};
    int anchored = trimmed > 0 && pattern[0] == '/';
    if (!anchored && trimmed >= 3) {
        /* id(...)/... and key(...)/... are already document-anchored function calls. */
        Py_ssize_t probe = -1;
        if (ucs4_has_prefix(pattern, trimmed, "id")) {
            probe = 2;
        } else if (ucs4_has_prefix(pattern, trimmed, "key")) {
            probe = 3;
        }
        while (probe >= 0 && probe < trimmed && ucs4_is_ws(pattern[probe])) {
            probe++;
        }
        anchored = probe >= 0 && probe < trimmed && pattern[probe] == '(';
    }
    if (!anchored && xb_add_ascii(&expr, "//") < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
        xb_free(&expr);                               /* GCOVR_EXCL_LINE */
        fail(eng, "out of memory");                   /* GCOVR_EXCL_LINE */
        return NULL;                                  /* GCOVR_EXCL_LINE */
    }
    if (xb_add(&expr, pattern, trimmed) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
        xb_free(&expr);                        /* GCOVR_EXCL_LINE */
        fail(eng, "out of memory");            /* GCOVR_EXCL_LINE */
        return NULL;                           /* GCOVR_EXCL_LINE */
    }
    char errbuf[256];
    xp_program *prog = xp_compile(expr.data, expr.len, errbuf, sizeof(errbuf));
    xb_free(&expr);
    if (prog == NULL) {
        PyErr_Format(PyExc_ValueError, "xslt: bad match pattern: %s", errbuf);
        fail_py(eng);
        return NULL;
    }
    return prog;
}

/* ---- the XPath variable scope --------------------------------------------- */

/* Push a binding (newest first, so the XPath evaluator's first-match lookup finds
   the innermost scope). The binding takes ownership of value and rtf. Returns 0, or
   -1 on allocation failure (value is freed). */
static int scope_push(engine *eng, const Py_UCS4 *name, Py_ssize_t name_len, xp_result value, th_node *rtf) {
    if (eng->scope_len == eng->scope_cap) {
        Py_ssize_t cap = eng->scope_cap == 0 ? 8 : eng->scope_cap * 2;
        var_bind *grown = PyMem_Realloc(eng->scope, (size_t)cap * sizeof(var_bind));
        if (grown == NULL) {        /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
            xp_result_free(&value); /* GCOVR_EXCL_LINE */
            return -1;              /* GCOVR_EXCL_LINE */
        }
        eng->scope = grown;
        eng->scope_cap = cap;
    }
    memmove(&eng->scope[1], &eng->scope[0], (size_t)eng->scope_len * sizeof(var_bind));
    eng->scope[0].name = (Py_UCS4 *)name;
    eng->scope[0].name_len = name_len;
    eng->scope[0].value = value;
    eng->scope[0].rtf = rtf;
    eng->scope_len++;
    return 0;
}

/* Drop the front `n` bindings (the most recently pushed), freeing their values. */
static void scope_drop(engine *eng, Py_ssize_t mark) {
    while (eng->scope_len > mark) {
        xp_result_free(&eng->scope[0].value);
        memmove(&eng->scope[0], &eng->scope[1], (size_t)(eng->scope_len - 1) * sizeof(var_bind));
        eng->scope_len--;
    }
}

/* Build the xp_bindings view over the current scope for one evaluation. */
static void scope_bindings(engine *eng, xp_binding *storage, xp_bindings *out) {
    for (Py_ssize_t index = 0; index < eng->scope_len; index++) {
        storage[index].name = eng->scope[index].name;
        storage[index].name_len = eng->scope[index].name_len;
        storage[index].value = eng->scope[index].value;
    }
    out->items = storage;
    out->len = eng->scope_len;
}

/* ---- the XSLT extension functions ----------------------------------------- */

static int build_key(engine *eng, xslt_key *key);

static int copy_result_value(const xp_result *src, xp_result *dst);

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

/* The XPath-engine extension hook: dispatch the XSLT-only functions. Returns 0 when
   handled (out filled), -2 when the name is not an XSLT function (the engine then
   reports an unknown function), -1 on a Python/allocation error. */
static int xslt_extension(void *vctx, th_node *context_node, const Py_UCS4 *name, Py_ssize_t name_len,
                          const xp_result *args, int argc, xp_result *out) {
    engine *eng = vctx;
    (void)context_node;
    memset(out, 0, sizeof(*out));
    if (ucs4_ascii_eq(name, name_len, "current")) {
        out->kind = XP_NODESET;
        if (eng->cur_attr < 0 && ns_push(&out->nodes, eng->cur_node, -1) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;                                                          /* GCOVR_EXCL_LINE */
        }
        if (eng->cur_attr >= 0 && ns_push(&out->nodes, eng->cur_node, eng->cur_attr) < 0) { /* GCOVR_EXCL_BR_LINE */
            return -1;                                                                      /* GCOVR_EXCL_LINE */
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
        for (Py_ssize_t index = 0; index < eng->nkeys; index++) {
            if (str_eq(eng->keys[index].name, eng->keys[index].name_len, key_name, key_name_len)) {
                key = &eng->keys[index];
                break;
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
           other type its string. */
        if (args[1].kind == XP_NODESET) {
            for (Py_ssize_t index = 0; index < args[1].nodes.len; index++) {
                Py_ssize_t value_len = 0;
                Py_UCS4 *value = item_string(eng->src_tree, args[1].nodes.items[index], &value_len);
                if (value == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
                    return -1;       /* GCOVR_EXCL_LINE */
                }
                const nodevec *bucket = strmap_lookup(&key->table, value, value_len);
                PyMem_Free(value);
                for (Py_ssize_t slot = 0; bucket != NULL && slot < bucket->len; slot++) {
                    if (ns_push(&out->nodes, bucket->nodes[slot], -1) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                        return -1;                                           /* GCOVR_EXCL_LINE */
                    }
                }
            }
            return 0;
        }
        Py_ssize_t value_len = 0;
        Py_UCS4 *value = to_string(eng->src_tree, &args[1], &value_len);
        if (value == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;       /* GCOVR_EXCL_LINE */
        }
        const nodevec *bucket = strmap_lookup(&key->table, value, value_len);
        PyMem_Free(value);
        for (Py_ssize_t slot = 0; bucket != NULL && slot < bucket->len; slot++) {
            if (ns_push(&out->nodes, bucket->nodes[slot], -1) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                return -1;                                           /* GCOVR_EXCL_LINE */
            }
        }
        return 0;
    }
    if (ucs4_ascii_eq(name, name_len, "generate-id")) {
        const th_node *target = eng->cur_node;
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
        }
        char buffer[32];
        int written = snprintf(buffer, sizeof(buffer), "id%zx", (size_t)(uintptr_t)target);
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

/* Evaluate a compiled program against the current node with the current scope and
   the XSLT extension functions. Returns the xp_eval status; fills *out on success. */
static int eval_program(engine *eng, const xp_program *prog, th_node *context, Py_ssize_t pos, Py_ssize_t size,
                        xp_result *out) {
    xp_binding storage[16];
    xp_binding *bindings = storage;
    if (eng->scope_len > 16) {
        bindings = PyMem_Malloc((size_t)eng->scope_len * sizeof(xp_binding));
        if (bindings == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
            fail_py(eng);       /* GCOVR_EXCL_LINE */
            PyErr_NoMemory();   /* GCOVR_EXCL_LINE */
            return -1;          /* GCOVR_EXCL_LINE */
        }
    }
    xp_bindings vars;
    scope_bindings(eng, bindings, &vars);
    const char *feature = NULL;
    int status = xp_eval_at(prog, eng->src_tree, context, pos, size, &vars, NULL, xslt_extension, eng, out, &feature);
    if (bindings != storage) {
        PyMem_Free(bindings); /* GCOVR_EXCL_LINE: only the >64-binding path allocates */
    }
    if (status < 0 && !PyErr_Occurred()) {
        /* A -3/-4 error always names a feature; only an unforced allocation failure
           returns <0 with none, so the fallback string is exercised nowhere. */
        if (feature == NULL) {      /* GCOVR_EXCL_BR_LINE */
            feature = "evaluation"; /* GCOVR_EXCL_LINE */
        } /* GCOVR_EXCL_LINE */
        PyErr_Format(PyExc_ValueError, "xslt: expression error (%s)", feature);
    }
    return status;
}

/* ---- key tables ----------------------------------------------------------- */

/* Populate one key's string->node table: for every source node the key match
   pattern selects, evaluate the use expression and index the node under each
   resulting string value. */
static int build_key(engine *eng, xslt_key *key) {
    key->built = 1;
    xp_result matched;
    const char *feature = NULL;
    int status =
        xp_eval_at(key->match_prog, eng->src_tree, eng->src_root, 1, 1, NULL, NULL, NULL, NULL, &matched, &feature);
    if (status < 0) { /* GCOVR_EXCL_BR_LINE: the key match compiled, so it evaluates */
        PyErr_Format(PyExc_ValueError, "xslt: key match failed"); /* GCOVR_EXCL_LINE */
        return fail_py(eng);                                      /* GCOVR_EXCL_LINE */
    }
    for (Py_ssize_t index = 0; index < matched.nodes.len; index++) {
        th_node *node = matched.nodes.items[index].node;
        xp_result used;
        int use_status = eval_program(eng, key->use_prog, node, 1, 1, &used);
        if (use_status < 0) {
            xp_result_free(&matched);
            return -1;
        }
        int rc = 0;
        if (used.kind == XP_NODESET) {
            for (Py_ssize_t slot = 0; slot < used.nodes.len; slot++) {
                Py_ssize_t value_len = 0;
                Py_UCS4 *value = item_string(eng->src_tree, used.nodes.items[slot], &value_len);
                if (value == NULL) { /* GCOVR_EXCL_START: alloc */
                    rc = -1;
                    break;
                } /* GCOVR_EXCL_STOP */
                nodevec *bucket = strmap_bucket(&key->table, value, value_len);
                PyMem_Free(value);
                if (bucket == NULL || nodevec_push(bucket, node) < 0) { /* GCOVR_EXCL_START: alloc */
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
                if (bucket == NULL || nodevec_push(bucket, node) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                    rc = -1;                                            /* GCOVR_EXCL_LINE */
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

/* ---- template matching ---------------------------------------------------- */

/* Populate a rule's matched-node set by evaluating its compiled pattern from the
   source root once and recording the selected items. */
static int build_rule(engine *eng, xslt_rule *rule) {
    rule->built = 1;
    xp_result matched;
    const char *feature = NULL;
    int status =
        xp_eval_at(rule->prog, eng->src_tree, eng->src_root, 1, 1, NULL, NULL, xslt_extension, eng, &matched, &feature);
    if (status < 0) {
        if (!PyErr_Occurred()) {   /* GCOVR_EXCL_BR_LINE: pattern evaluation sets an exception only on allocation */
            if (feature == NULL) { /* GCOVR_EXCL_BR_LINE: a -3/-4 error always names a feature */
                feature = "evaluation"; /* GCOVR_EXCL_LINE */
            } /* GCOVR_EXCL_LINE */
            PyErr_Format(PyExc_ValueError, "xslt: match pattern error (%s)", feature);
        }
        return fail_py(eng);
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

/* The best-matching rule for (node, attr) in the given mode, or NULL for none. The
   rule array is pre-sorted by descending (priority, position), so the
   first match wins the section 5.5 conflict resolution. */
static xslt_rule *best_rule(engine *eng, th_node *node, Py_ssize_t attr, const Py_UCS4 *mode, Py_ssize_t mode_len) {
    for (Py_ssize_t index = 0; index < eng->nrules; index++) {
        xslt_rule *rule = &eng->rules[index];
        int rule_default = rule->mode == NULL;
        int want_default = mode == NULL;
        if (rule_default != want_default) {
            continue;
        }
        if (!want_default && !str_eq(rule->mode, rule->mode_len, mode, mode_len)) {
            continue;
        }
        if (!rule->built && build_rule(eng, rule) < 0) {
            return NULL;
        }
        if (match_set_has(&rule->matched, node, attr)) {
            return rule;
        }
    }
    return NULL;
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
    program = xp_compile(source, length, error, error_size);
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
                PyErr_Format(PyExc_ValueError, "xslt: bad expression in attribute value template: %s", errbuf);
                fail_py(eng);
                return -1;
            }
            /* GCOVR_EXCL_STOP */
            xp_result value;
            int status = eval_program(eng, prog, eng->cur_node, eng->ctx_pos, eng->ctx_size, &value);
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
    const Py_UCS4 *select = attr_lookup(eng->sheet_tree, instruction, "select", &select_len);
    if (select == NULL) {
        return fail(eng, "xsl:value-of requires a select attribute");
    }
    if (eng->cur_attr >= 0 && is_self_dot(select, select_len)) {
        Py_ssize_t text_len = 0;
        Py_UCS4 *text = current_string(eng, &text_len);
        if (text == NULL) {                    /* GCOVR_EXCL_BR_LINE: alloc */
            return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
        }
        int rc = emit_text(eng, out_parent, text, text_len);
        PyMem_Free(text);
        return rc;
    }
    char errbuf[256];
    xp_program *prog = compile_expression(eng, select, select_len, errbuf, sizeof(errbuf));
    if (prog == NULL) { /* GCOVR_EXCL_BR_LINE: compilation rejects malformed selects before a run */
        PyErr_Format(PyExc_ValueError, "xslt: bad value-of select: %s", errbuf); /* GCOVR_EXCL_LINE */
        return fail_py(eng);                                                     /* GCOVR_EXCL_LINE */
    }
    xp_result value;
    int status = eval_program(eng, prog, eng->cur_node, eng->ctx_pos, eng->ctx_size, &value);
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

/* Deep-copy a source subtree into the output tree, appended to out_parent. */
static int copy_of_node(engine *eng, th_node *out_parent, xp_item item) {
    if (item.attr >= 0) {
        const th_node_attr *attr = &item.node->attrs[item.attr];
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
    return 0;
}

static int do_copy_of(engine *eng, th_node *instruction, th_node *out_parent) {
    Py_ssize_t select_len = 0;
    const Py_UCS4 *select = attr_lookup(eng->sheet_tree, instruction, "select", &select_len);
    if (select == NULL) {
        return fail(eng, "xsl:copy-of requires a select attribute");
    }
    char errbuf[256];
    xp_program *prog = compile_expression(eng, select, select_len, errbuf, sizeof(errbuf));
    if (prog == NULL) { /* GCOVR_EXCL_BR_LINE: compilation rejects malformed selects before a run */
        PyErr_Format(PyExc_ValueError, "xslt: bad copy-of select: %s", errbuf); /* GCOVR_EXCL_LINE */
        return fail_py(eng);                                                    /* GCOVR_EXCL_LINE */
    }
    /* A lone $var that is a result tree fragment copies the fragment's children. */
    if (prog->nodes[prog->root].kind == XN_VAR) {
        const xn *var = &prog->nodes[prog->root];
        for (Py_ssize_t index = 0; index < eng->scope_len; index++) {
            if (str_eq(eng->scope[index].name, eng->scope[index].name_len, var->str, var->str_len) &&
                eng->scope[index].rtf != NULL) {
                for (th_node *child = eng->scope[index].rtf->first_child; child != NULL; child = child->next_sibling) {
                    th_node *copy = th_tree_copy_node(eng->out_tree, eng->out_tree, child);
                    if (copy == NULL) {                    /* GCOVR_EXCL_BR_LINE: alloc */
                        return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
                    }
                    th_node_append_child(out_parent, copy);
                }
                return 0;
            }
        }
    }
    xp_result value;
    int status = eval_program(eng, prog, eng->cur_node, eng->ctx_pos, eng->ctx_size, &value);
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
    if (instantiate_body(eng, body, fragment) < 0) {
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

/* Apply the named attribute sets (section 7.1.4) to out_element: each named set's own
   use-attribute-sets are applied first, then its xsl:attribute children set attributes on the
   element, so a later source wins over the sets and a set's own attributes win over the ones it
   chains to. `names` is the whitespace-separated use-attribute-sets value. */
static int apply_attribute_sets(engine *eng, const Py_UCS4 *names, Py_ssize_t names_len, th_node *out_element) {
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
        for (Py_ssize_t slot = 0; slot < eng->nattrsets; slot++) {
            xslt_attrset *set = &eng->attrsets[slot];
            if (!str_eq(set->name, set->name_len, names + start, index - start)) {
                continue;
            }
            Py_ssize_t chain_len = 0;
            const Py_UCS4 *chain = attr_lookup(eng->sheet_tree, set->body, "use-attribute-sets", &chain_len);
            if (chain != NULL && apply_attribute_sets(eng, chain, chain_len, out_element) < 0) {
                return -1;
            }
            if (instantiate_body(eng, set->body, out_element) < 0) {
                return -1;
            }
        }
    }
    return 0;
}

/* xsl:element name={avt}: create an element and instantiate its body inside it. */
static int do_element(engine *eng, th_node *instruction, th_node *out_parent) {
    Py_ssize_t name_len = 0;
    const Py_UCS4 *name_avt = attr_lookup(eng->sheet_tree, instruction, "name", &name_len);
    if (name_avt == NULL) {
        return fail(eng, "xsl:element requires a name attribute");
    }
    Py_UCS4 *name;
    Py_ssize_t resolved_len = 0;
    if (eval_avt(eng, name_avt, name_len, &name, &resolved_len) < 0) {
        return -1;
    }
    uint16_t atom = atom_for_name(name, resolved_len);
    th_node *element = th_tree_make_element(eng->out_tree, name, resolved_len, atom, 0);
    PyMem_Free(name);
    if (element == NULL) {                 /* GCOVR_EXCL_BR_LINE: alloc */
        return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
    }
    th_node_append_child(out_parent, element);
    Py_ssize_t use_len = 0;
    const Py_UCS4 *use = attr_lookup(eng->sheet_tree, instruction, "use-attribute-sets", &use_len);
    if (use != NULL && apply_attribute_sets(eng, use, use_len, element) < 0) {
        return -1;
    }
    return instantiate_body(eng, instruction, element);
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
    const char *prefix = NULL;
    Py_ssize_t prefix_len = 0;
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
        prefix = decl + 6;
        prefix_len = decl_len - 6;
        break;
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
    const Py_UCS4 *name_avt = attr_lookup(eng->sheet_tree, instruction, "name", &name_len);
    if (name_avt == NULL) {
        return fail(eng, "xsl:attribute requires a name attribute");
    }
    Py_UCS4 *name;
    Py_ssize_t resolved_len = 0;
    if (eval_avt(eng, name_avt, name_len, &name, &resolved_len) < 0) {
        return -1;
    }
    Py_UCS4 *value;
    Py_ssize_t value_len = 0;
    if (instantiate_string(eng, instruction, &value, &value_len) < 0) {
        PyMem_Free(name);
        return -1;
    }
    Py_ssize_t ns_avt_len = 0;
    const Py_UCS4 *ns_avt = attr_lookup(eng->sheet_tree, instruction, "namespace", &ns_avt_len);
    Py_UCS4 *nsuri = NULL;
    Py_ssize_t nsuri_len = 0;
    if (ns_avt != NULL && eval_avt(eng, ns_avt, ns_avt_len, &nsuri, &nsuri_len) < 0) {
        PyMem_Free(name);
        PyMem_Free(value);
        return -1;
    }
    /* eval_avt yields a NULL buffer for an empty result, so a namespace="" attribute leaves nsuri
       NULL and falls through to a plain name; a non-NULL nsuri always has a positive length. */
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
    if (eng->cur_attr >= 0) {
        const th_node_attr *attr = &eng->cur_node->attrs[eng->cur_attr];
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
        Py_ssize_t use_len = 0;
        const Py_UCS4 *use = attr_lookup(eng->sheet_tree, instruction, "use-attribute-sets", &use_len);
        if (use != NULL && apply_attribute_sets(eng, use, use_len, element) < 0) {
            return -1;
        }
        return instantiate_body(eng, instruction, element);
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

/* xsl:comment / xsl:processing-instruction. */
static int do_comment(engine *eng, th_node *instruction, th_node *out_parent) {
    Py_UCS4 *data;
    Py_ssize_t data_len = 0;
    if (instantiate_string(eng, instruction, &data, &data_len) < 0) {
        return -1;
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
    const Py_UCS4 *name_avt = attr_lookup(eng->sheet_tree, instruction, "name", &name_len);
    if (name_avt == NULL) {
        return fail(eng, "xsl:processing-instruction requires a name attribute");
    }
    Py_UCS4 *target;
    Py_ssize_t target_len = 0;
    if (eval_avt(eng, name_avt, name_len, &target, &target_len) < 0) {
        return -1;
    }
    Py_UCS4 *data;
    Py_ssize_t data_len = 0;
    if (instantiate_string(eng, instruction, &data, &data_len) < 0) {
        PyMem_Free(target);
        return -1;
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
    const Py_UCS4 *test = attr_lookup(eng->sheet_tree, instruction, "test", &test_len);
    if (test == NULL) {
        return fail(eng, "xsl:if/xsl:when requires a test attribute");
    }
    char errbuf[256];
    xp_program *prog = compile_expression(eng, test, test_len, errbuf, sizeof(errbuf));
    if (prog == NULL) { /* GCOVR_EXCL_BR_LINE: compilation rejects malformed tests before a run */
        PyErr_Format(PyExc_ValueError, "xslt: bad test: %s", errbuf); /* GCOVR_EXCL_LINE */
        return fail_py(eng);                                          /* GCOVR_EXCL_LINE */
    }
    xp_result value;
    int status = eval_program(eng, prog, eng->cur_node, eng->ctx_pos, eng->ctx_size, &value);
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
    Py_ssize_t key_len;
    double number;
} sort_item;

typedef struct {
    xp_program *prog;
    int numeric;
    int descending;
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
        const Py_UCS4 *select = attr_lookup(eng->sheet_tree, child, "select", &select_len);
        if (select == NULL) {
            select = &XPATH_DOT;
            select_len = 1;
        }
        char errbuf[256];
        xp_program *prog = compile_expression(eng, select, select_len, errbuf, sizeof(errbuf));
        if (prog == NULL) { /* GCOVR_EXCL_BR_LINE: compilation rejects malformed sort selects before a run */
            PyErr_Format(PyExc_ValueError, "xslt: bad sort select: %s", errbuf); /* GCOVR_EXCL_LINE */
            fail_py(eng);                                                        /* GCOVR_EXCL_LINE */
            return -1;                                                           /* GCOVR_EXCL_LINE */
        }
        Py_ssize_t type_len = 0;
        const Py_UCS4 *type = attr_lookup(eng->sheet_tree, child, "data-type", &type_len);
        Py_ssize_t order_len = 0;
        const Py_UCS4 *order = attr_lookup(eng->sheet_tree, child, "order", &order_len);
        specs[count].prog = prog;
        specs[count].numeric = type != NULL && ucs4_ascii_eq(type, type_len, "number");
        specs[count].descending = order != NULL && ucs4_ascii_eq(order, order_len, "descending");
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
            xp_result value;
            int status = eval_program(eng, specs[spec].prog, set->items[index].node, index + 1, set->len, &value);
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
        }
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

/* Emit value as decimal digits, left-padded to min_width and, when a grouping separator and a
   positive grouping size are given (section 7.7.1), split into groups from the right. */
static int emit_decimal(xb *out, long value, Py_ssize_t min_width, const Py_UCS4 *gsep, Py_ssize_t gsep_len,
                        long gsize) {
    char raw[32];
    int raw_len = snprintf(raw, sizeof(raw), "%ld", value);
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
    if (xb_add(out, format + sep_start[0], sep_len[0]) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;                                            /* GCOVR_EXCL_LINE */
    }
    for (Py_ssize_t index = 0; index < nvalues; index++) {
        Py_ssize_t pick = index < ntok ? index : ntok - 1;
        if (index > 0 && ntok > 0) {
            int sep = xb_add(out, format + sep_start[pick], sep_len[pick]);
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

/* Build a node set matching one of a pattern's union alternatives (the count/from patterns of
   xsl:number). Returns 0 with set filled, or -1 on error. */
static int build_matcher(engine *eng, const Py_UCS4 *pattern, Py_ssize_t len, match_set *set) {
    Py_ssize_t starts[64];
    Py_ssize_t lens[64];
    int alternatives = split_union(pattern, len, starts, lens, 64);
    if (alternatives < 0) { /* GCOVR_EXCL_BR_LINE: compilation rejects oversized unions before a run */
        return fail(eng, "xslt: xsl:number pattern has too many alternatives"); /* GCOVR_EXCL_LINE */
    }
    for (int index = 0; index < alternatives; index++) {
        xp_program *prog = compile_pattern(eng, pattern + starts[index], lens[index]);
        if (prog == NULL) { /* GCOVR_EXCL_BR_LINE: compilation rejects malformed patterns before a run */
            return -1;      /* GCOVR_EXCL_LINE */
        }
        xp_result matched;
        const char *feature = NULL;
        int status =
            xp_eval_at(prog, eng->src_tree, eng->src_root, 1, 1, NULL, NULL, xslt_extension, eng, &matched, &feature);
        if (status < 0) { /* GCOVR_EXCL_BR_LINE: the pattern compiled, so it evaluates */
            PyErr_Format(PyExc_ValueError, "xslt: xsl:number pattern error"); /* GCOVR_EXCL_LINE */
            return fail_py(eng);                                              /* GCOVR_EXCL_LINE */
        }
        for (Py_ssize_t slot = 0; slot < matched.nodes.len; slot++) {
            xp_item item = matched.nodes.items[slot];
            int added = match_set_add(set, item.node, item.attr);
            if (added < 0) {              /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
                xp_result_free(&matched); /* GCOVR_EXCL_LINE */
                return -1;                /* GCOVR_EXCL_LINE */
            }
        }
        xp_result_free(&matched);
    }
    return 0;
}

/* Whether node counts under xsl:number: membership of the compiled count set when count was
   given, else the default -- same node type and, for elements, the same name as the current
   node (section 7.7). */
static int number_counts(const engine *eng, const match_set *count_set, int have_count, const th_node *node) {
    if (have_count) {
        return match_set_has(count_set, node, -1);
    }
    if (node->type != eng->cur_node->type) {
        return 0;
    }
    return node->type != TH_NODE_ELEMENT ||
           (node->text_len == eng->cur_node->text_len &&
            memcmp(node->text, eng->cur_node->text, (size_t)node->text_len * sizeof(Py_UCS4)) == 0);
}

/* Whether the memo was taken under the same criteria this call uses, so its answer still applies: the same xsl:number
   instruction, and, when that instruction names no count pattern, the same current-node type and name the default
   criteria read. The caller reaches this only for a node whose previous sibling is the memo's node. */
static int number_memo_applies(const engine *eng, const th_node *instruction, int have_count) {
    if (eng->number_memo_instruction != instruction) {
        return 0;
    }
    return have_count ||
           (eng->number_memo_type == (int)eng->cur_node->type && /* GCOVR_EXCL_BR_LINE: a memo is
               consulted only across siblings, which one run never spans a type change in */
            eng->number_memo_name_len == eng->cur_node->text_len &&
            memcmp(eng->number_memo_name, eng->cur_node->text, (size_t)eng->cur_node->text_len * sizeof(Py_UCS4)) == 0);
}

/* The count of node plus its preceding siblings that match the count criteria (one level's
   number). Numbering a run of siblings walks it once in total rather than once per sibling: the
   answer for a node is the answer for its previous sibling plus whether that sibling counted. */
static long level_number(engine *eng, const th_node *instruction, const match_set *count_set, int have_count,
                         th_node *node) {
    long count = 1;
    th_node *prev = node->prev_sibling;
    if (prev != NULL && prev == eng->number_memo_node && number_memo_applies(eng, instruction, have_count)) {
        /* the memo holds the node the previous call numbered, and a call only ever numbers a node that met
           the count criteria, so reaching it through prev means prev counted */
        count = eng->number_memo_value + 1;
    } else {
        for (; prev != NULL; prev = prev->prev_sibling) {
            if (number_counts(eng, count_set, have_count, prev)) {
                count++;
            }
        }
    }
    eng->number_memo_node = node;
    eng->number_memo_value = count;
    eng->number_memo_instruction = instruction;
    eng->number_memo_type = (int)eng->cur_node->type;
    eng->number_memo_name = eng->cur_node->text;
    eng->number_memo_name_len = eng->cur_node->text_len;
    return count;
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

static int do_number(engine *eng, th_node *instruction, th_node *out_parent) {
    long values[64];
    Py_ssize_t nvalues = 0;
    Py_ssize_t value_len = 0;
    const Py_UCS4 *value_expr = attr_lookup(eng->sheet_tree, instruction, "value", &value_len);
    match_set count_set = {0};
    match_set from_set = {0};
    int have_count = 0;
    int have_from = 0;
    if (value_expr != NULL) {
        char errbuf[256];
        xp_program *prog = compile_expression(eng, value_expr, value_len, errbuf, sizeof(errbuf));
        if (prog == NULL) { /* GCOVR_EXCL_BR_LINE: compilation rejects malformed values before a run */
            PyErr_Format(PyExc_ValueError, "xslt: bad number value: %s", errbuf); /* GCOVR_EXCL_LINE */
            return fail_py(eng);                                                  /* GCOVR_EXCL_LINE */
        }
        xp_result result;
        int status = eval_program(eng, prog, eng->cur_node, eng->ctx_pos, eng->ctx_size, &result);
        if (status < 0) {
            return fail_py(eng);
        }
        values[nvalues++] = (long)floor(to_number(eng->src_tree, &result) + 0.5);
        xp_result_free(&result);
    } else if (eng->cur_attr >= 0) {
        values[nvalues++] = 1;
    } else {
        Py_ssize_t count_len = 0;
        const Py_UCS4 *count = attr_lookup(eng->sheet_tree, instruction, "count", &count_len);
        Py_ssize_t from_len = 0;
        const Py_UCS4 *from = attr_lookup(eng->sheet_tree, instruction, "from", &from_len);
        /* Compilation validates the count and from patterns before a run. */
        if (count != NULL) {
            have_count = 1;
            /* GCOVR_EXCL_BR_START */
            if (build_matcher(eng, count, count_len, &count_set) < 0) {
                match_set_free(&count_set); /* GCOVR_EXCL_LINE */
                return -1;                  /* GCOVR_EXCL_LINE */
            }
            /* GCOVR_EXCL_BR_STOP */
        }
        if (from != NULL) {
            have_from = 1;
            /* GCOVR_EXCL_BR_START */
            if (build_matcher(eng, from, from_len, &from_set) < 0) {
                match_set_free(&count_set); /* GCOVR_EXCL_LINE */
                match_set_free(&from_set);  /* GCOVR_EXCL_LINE */
                return -1;                  /* GCOVR_EXCL_LINE */
            }
            /* GCOVR_EXCL_BR_STOP */
        }
        Py_ssize_t level_len = 0;
        const Py_UCS4 *level = attr_lookup(eng->sheet_tree, instruction, "level", &level_len);
        if (level != NULL && ucs4_ascii_eq(level, level_len, "any")) {
            long counter = 0;
            /* The current node is a descendant of the source root, so the walk always breaks at
               it before the loop condition can see a NULL. */
            for (th_node *node = eng->src_root; node != NULL; /* GCOVR_EXCL_BR_LINE */ node = doc_next(node)) {
                if (have_from && match_set_has(&from_set, node, -1)) {
                    counter = 0;
                }
                if (number_counts(eng, &count_set, have_count, node)) {
                    counter++;
                }
                if (node == eng->cur_node) {
                    break;
                }
            }
            values[nvalues++] = counter;
        } else if (level != NULL && ucs4_ascii_eq(level, level_len, "multiple")) {
            th_node *chain[64];
            Py_ssize_t depth = 0;
            for (th_node *node = eng->cur_node; node != NULL; node = node->parent) {
                /* A count chain deeper than the fixed buffer cannot arise from a real document. */
                if (depth == 64) { /* GCOVR_EXCL_BR_LINE: overflow guard for the chain buffer */
                    break;         /* GCOVR_EXCL_LINE */
                }
                if (have_from && match_set_has(&from_set, node, -1)) {
                    break;
                }
                if (number_counts(eng, &count_set, have_count, node)) {
                    chain[depth++] = node;
                }
            }
            for (Py_ssize_t index = depth - 1; index >= 0; index--) {
                values[nvalues++] = level_number(eng, instruction, &count_set, have_count, chain[index]);
            }
        } else {
            /* single (the default): the nearest ancestor-or-self that matches count, bounded by
               the nearest from ancestor. */
            th_node *target = NULL;
            for (th_node *node = eng->cur_node; node != NULL; node = node->parent) {
                if (have_from && match_set_has(&from_set, node, -1)) {
                    break;
                }
                if (number_counts(eng, &count_set, have_count, node)) {
                    target = node;
                    break;
                }
            }
            if (target != NULL) {
                values[nvalues++] = level_number(eng, instruction, &count_set, have_count, target);
            }
        }
    }
    match_set_free(&count_set);
    match_set_free(&from_set);
    Py_ssize_t format_len = 0;
    const Py_UCS4 *format = attr_lookup(eng->sheet_tree, instruction, "format", &format_len);
    Py_ssize_t gsep_len = 0;
    const Py_UCS4 *gsep = attr_lookup(eng->sheet_tree, instruction, "grouping-separator", &gsep_len);
    Py_ssize_t gsize_len = 0;
    const Py_UCS4 *gsize_text = attr_lookup(eng->sheet_tree, instruction, "grouping-size", &gsize_len);
    long gsize = gsize_text != NULL ? parse_grouping_size(gsize_text, gsize_len) : 0;
    xb buffer = {0};
    int formatted = format_multi(&buffer, format, format_len, values, nvalues, gsep, gsep_len, gsize);
    if (formatted < 0) {                   /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
        xb_free(&buffer);                  /* GCOVR_EXCL_LINE */
        return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
    }
    int rc = emit_text(eng, out_parent, buffer.data, buffer.len);
    xb_free(&buffer);
    return rc;
}

/* ---- variables ------------------------------------------------------------ */

/* Compute a variable/param binding value from its select attribute or body. On an
   RTF body, *out_rtf receives the fragment node (else NULL). */
static int compute_binding(engine *eng, th_node *declaration, xp_result *out_value, th_node **out_rtf) {
    *out_rtf = NULL;
    Py_ssize_t select_len = 0;
    const Py_UCS4 *select = attr_lookup(eng->sheet_tree, declaration, "select", &select_len);
    if (select != NULL) {
        char errbuf[256];
        xp_program *prog = compile_expression(eng, select, select_len, errbuf, sizeof(errbuf));
        if (prog == NULL) { /* GCOVR_EXCL_BR_LINE: compilation rejects malformed bindings before a run */
            PyErr_Format(PyExc_ValueError, "xslt: bad variable select: %s", errbuf); /* GCOVR_EXCL_LINE */
            return fail_py(eng);                                                     /* GCOVR_EXCL_LINE */
        }
        int status = eval_program(eng, prog, eng->cur_node, eng->ctx_pos, eng->ctx_size, out_value);
        if (status < 0) {
            return fail_py(eng);
        }
        return 0;
    }
    if (declaration->first_child == NULL) {
        result_string(out_value, NULL, 0);
        return 0;
    }
    th_node *fragment = th_tree_make_fragment(eng->out_tree);
    if (fragment == NULL) {                /* GCOVR_EXCL_BR_LINE: alloc */
        return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
    }
    if (instantiate_body(eng, declaration, fragment) < 0) {
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
        const Py_UCS4 *name = attr_lookup(eng->sheet_tree, child, "name", &name_len);
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
        const Py_UCS4 *name = attr_lookup(eng->sheet_tree, child, "name", &name_len);
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
    const Py_UCS4 *name = attr_lookup(eng->sheet_tree, instruction, "name", &name_len);
    if (name == NULL) {
        return fail(eng, "xsl:call-template requires a name attribute");
    }
    xslt_named *target = NULL;
    for (Py_ssize_t index = 0; index < eng->nnamed; index++) {
        if (str_eq(eng->named[index].name, eng->named[index].name_len, name, name_len)) {
            target = &eng->named[index];
            break;
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

static int do_for_each(engine *eng, th_node *instruction, th_node *out_parent) {
    Py_ssize_t select_len = 0;
    const Py_UCS4 *select = attr_lookup(eng->sheet_tree, instruction, "select", &select_len);
    if (select == NULL) {
        return fail(eng, "xsl:for-each requires a select attribute");
    }
    char errbuf[256];
    xp_program *prog = compile_expression(eng, select, select_len, errbuf, sizeof(errbuf));
    if (prog == NULL) { /* GCOVR_EXCL_BR_LINE: compilation rejects malformed selects before a run */
        PyErr_Format(PyExc_ValueError, "xslt: bad for-each select: %s", errbuf); /* GCOVR_EXCL_LINE */
        return fail_py(eng);                                                     /* GCOVR_EXCL_LINE */
    }
    xp_result value;
    int status = eval_program(eng, prog, eng->cur_node, eng->ctx_pos, eng->ctx_size, &value);
    if (status < 0) {
        return fail_py(eng);
    }
    if (value.kind != XP_NODESET) {
        xp_result_free(&value);
        return fail(eng, "xsl:for-each select is not a node-set");
    }
    sort_spec specs[XSLT_MAX_SORTS];
    int nspecs = compile_sorts(eng, instruction, specs, XSLT_MAX_SORTS);
    if (nspecs < 0) {
        xp_result_free(&value);
        return -1;
    }
    if (nspecs > 0 && sort_nodeset(eng, &value.nodes, specs, nspecs) < 0) {
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
    if (attr >= 0) {
        const th_node_attr *attribute = &node->attrs[attr];
        return emit_text(eng, out_parent, attribute->value, attribute->value_len);
    }
    if (node->type == TH_NODE_TEXT) {
        Py_ssize_t text_len = 0;
        Py_UCS4 *text = th_node_data(eng->src_tree, node, &text_len);
        if (text == NULL) {                    /* GCOVR_EXCL_BR_LINE: alloc */
            return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
        }
        int rc = emit_text(eng, out_parent, text, text_len);
        PyMem_Free(text);
        return rc;
    }
    if (node->type == TH_NODE_ELEMENT || node->type == TH_NODE_DOCUMENT || node->type == TH_NODE_CONTENT) {
        Py_ssize_t child_pos = 0;
        Py_ssize_t child_count = 0;
        for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
            child_count++;
        }
        int rc = 0;
        for (th_node *child = node->first_child; child != NULL && rc == 0; child = child->next_sibling) {
            child_pos++;
            rc = apply_to_item(eng, (xp_item){child, -1}, child_pos, child_count, mode, mode_len, NULL, 0, out_parent);
        }
        return rc;
    }
    return 0; /* comment / PI / doctype: the built-in rule produces nothing */
}

static int apply_templates(engine *eng, th_node *instruction, th_node *out_parent, const Py_UCS4 *outer_mode,
                           Py_ssize_t outer_mode_len) {
    Py_ssize_t mode_len = 0;
    const Py_UCS4 *mode = attr_lookup(eng->sheet_tree, instruction, "mode", &mode_len);
    if (mode == NULL) {
        mode = outer_mode;
        mode_len = outer_mode_len;
    }
    Py_ssize_t select_len = 0;
    const Py_UCS4 *select = attr_lookup(eng->sheet_tree, instruction, "select", &select_len);
    xp_result value;
    if (select != NULL) {
        char errbuf[256];
        xp_program *prog = compile_expression(eng, select, select_len, errbuf, sizeof(errbuf));
        if (prog == NULL) { /* GCOVR_EXCL_BR_LINE: compilation rejects malformed selects before a run */
            PyErr_Format(PyExc_ValueError, "xslt: bad apply-templates select: %s", errbuf); /* GCOVR_EXCL_LINE */
            return fail_py(eng);                                                            /* GCOVR_EXCL_LINE */
        }
        int status = eval_program(eng, prog, eng->cur_node, eng->ctx_pos, eng->ctx_size, &value);
        if (status < 0) {
            return fail_py(eng);
        }
        if (value.kind != XP_NODESET) {
            xp_result_free(&value);
            return fail(eng, "xsl:apply-templates select is not a node-set");
        }
    } else {
        /* Default: the children of the current node, in document order. */
        memset(&value, 0, sizeof(value));
        value.kind = XP_NODESET;
        if (eng->cur_attr < 0) {
            for (th_node *child = eng->cur_node->first_child; child != NULL; child = child->next_sibling) {
                if (ns_push(&value.nodes, child, -1) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                    xp_result_free(&value);                 /* GCOVR_EXCL_LINE */
                    return fail(eng, "out of memory");      /* GCOVR_EXCL_LINE */
                }
            }
        }
    }
    sort_spec specs[XSLT_MAX_SORTS];
    int nspecs = compile_sorts(eng, instruction, specs, XSLT_MAX_SORTS);
    if (nspecs < 0) {
        xp_result_free(&value);
        return -1;
    }
    if (nspecs > 0 && sort_nodeset(eng, &value.nodes, specs, nspecs) < 0) {
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

/* Whether an output ancestor of `start` already binds namespace `name` (an "xmlns" or
   "xmlns:prefix" spelling) to the identical URI. The nearest binding wins, so a rebinding
   to a different URI reports out-of-scope and the caller redeclares. */
static int output_ns_in_scope(engine *eng, th_node *start, const char *name, Py_ssize_t name_len, const Py_UCS4 *value,
                              Py_ssize_t value_len) {
    for (th_node *anc = start; anc != NULL; anc = anc->parent) {
        if (anc->type != TH_NODE_ELEMENT) {
            continue;
        }
        for (Py_ssize_t index = 0; index < anc->attr_count; index++) {
            const th_node_attr *attr = &anc->attrs[index];
            Py_ssize_t anc_len = 0;
            const char *anc_name = th_attr_name(eng->out_tree, attr->name_atom, &anc_len);
            if (anc_len != name_len || memcmp(anc_name, name, (size_t)name_len) != 0) {
                continue;
            }
            return attr->value_len == value_len && memcmp(attr->value, value, (size_t)value_len * sizeof(Py_UCS4)) == 0;
        }
    }
    return 0;
}

/* Whether `prefix` appears in the stylesheet's exclude-result-prefixes list (a whitespace-
   separated set of prefixes; the default namespace is named "#default"), so its namespace
   node is not copied to the output. */
static int prefix_excluded(const engine *eng, const char *prefix, Py_ssize_t prefix_len) {
    Py_ssize_t index = 0;
    while (index < eng->exclude_prefixes_len) {
        while (index < eng->exclude_prefixes_len && ucs4_blank(&eng->exclude_prefixes[index], 1)) {
            index++;
        }
        Py_ssize_t start = index;
        while (index < eng->exclude_prefixes_len && !ucs4_blank(&eng->exclude_prefixes[index], 1)) {
            index++;
        }
        if (index - start == prefix_len) {
            Py_ssize_t offset = 0;
            while (offset < prefix_len &&
                   eng->exclude_prefixes[start + offset] == (Py_UCS4)(unsigned char)prefix[offset]) {
                offset++;
            }
            if (offset == prefix_len) {
                return 1;
            }
        }
    }
    return 0;
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

/* Copy one in-scope namespace declaration `attr` onto the output copy, honoring
   exclude-result-prefixes, an inner override, namespace-alias remapping and the XSLT-namespace
   drop, and de-duplicating against the output parent's in-scope declarations. */
static int copy_one_ns_decl(engine *eng, th_node *lre, th_node *anc, const th_node_attr *attr, const char *name,
                            Py_ssize_t name_len, int prefixed, th_node *copy, th_node *out_parent) {
    if (prefixed ? prefix_excluded(eng, name + 6, name_len - 6) : prefix_excluded(eng, "#default", 8)) {
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
    if (overridden || (aliased == NULL && ucs4_ascii_eq(attr->value, attr->value_len, XSLT_NS)) ||
        output_ns_in_scope(eng, out_parent, name, name_len, value, value_len)) {
        return 0;
    }
    int rc = th_node_attr_set(eng->out_tree, copy, name, name_len, value, value_len, 1);
    if (rc < 0) {                          /* GCOVR_EXCL_BR_LINE: alloc */
        return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
    }
    return 0;
}

/* Copy the stylesheet namespace declarations in scope at literal result element `lre` onto
   its output copy (XSLT 1.0 section 7.1.1): every in-scope namespace node except the XSLT
   namespace itself and any exclude-result-prefixes namespace, de-duplicated against the
   declarations already in scope on the output parent so a prefix is redeclared only where it
   is not already bound to that URI. The element's own prefix declaration is emitted first (as
   libxslt does). The html and text output methods carry no namespace nodes (matching libxslt). */
static int copy_namespace_decls(engine *eng, th_node *lre, th_node *copy, th_node *out_parent) {
    if (eng->output_method != OUT_XML) {
        return 0;
    }
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
                int rc = copy_one_ns_decl(eng, lre, anc, attr, name, name_len, prefixed, copy, out_parent);
                if (rc < 0) {  /* GCOVR_EXCL_BR_LINE: copy_one_ns_decl only fails on an unforced allocation */
                    return -1; /* GCOVR_EXCL_LINE */
                }
            }
        }
    }
    return 0;
}

/* Whether attribute name `name` carries the stylesheet's XSLT-namespace prefix, i.e. it is an
   XSLT directive (xsl:exclude-result-prefixes, xsl:use-attribute-sets, ...) placed on a literal
   result element, which the spec strips from the output rather than copying. */
static int is_xsl_attr(const engine *eng, const th_node *node, const char *name, Py_ssize_t name_len) {
    if (eng->xsl_ns_dynamic) {
        const char *colon = memchr(name, ':', (size_t)name_len);
        if (colon == NULL) {
            return 0;
        }
        Py_ssize_t prefix_len = colon - name;
        for (const th_node *ancestor = node; ancestor != NULL; ancestor = ancestor->parent) {
            for (Py_ssize_t index = 0; index < ancestor->attr_count; index++) {
                const th_node_attr *attr = &ancestor->attrs[index];
                Py_ssize_t decl_len = 0;
                const char *decl = th_attr_name(eng->sheet_tree, attr->name_atom, &decl_len);
                if (decl_len == prefix_len + 6 && memcmp(decl, "xmlns:", 6) == 0 &&
                    memcmp(decl + 6, name, (size_t)prefix_len) == 0) {
                    return ucs4_ascii_eq(attr->value, attr->value_len, XSLT_NS);
                }
            }
        }
        return 0;
    }
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
            if (!is_xsl_attr(eng, node, name, name_len)) {
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
        if (is_xsl_attr(eng, node, name, name_len)) {
            *out_len = node->attrs[index].value_len;
            return node->attrs[index].value;
        }
    }
    return NULL;
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
    Py_ssize_t use_len = 0;
    const Py_UCS4 *use = xsl_prefixed_attr(eng, element, "use-attribute-sets", &use_len);
    if (use != NULL && apply_attribute_sets(eng, use, use_len, copy) < 0) {
        return -1;
    }
    for (Py_ssize_t index = 0; index < element->attr_count; index++) {
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
        if (is_xsl_attr(eng, element, name, name_len)) {
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
    return instantiate_body(eng, element, copy);
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

/* The instructions instantiate_one dispatches on directly. XSL_OTHER covers every other
   xsl:* element (sort, with-param, param, fallback, apply-imports, ...), which the parent
   consumes or the core does not model, so it instantiates nothing. */
enum xsl_instr {
    XSL_OTHER,
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
    return XSL_OTHER;
}

static int instantiate_non_element(engine *eng, th_node *node, th_node *out_parent) {
    if (node->type == TH_NODE_TEXT) {
        Py_ssize_t text_len = 0;
        Py_UCS4 *text = th_node_data(eng->sheet_tree, node, &text_len);
        if (text == NULL) {                    /* GCOVR_EXCL_BR_LINE: alloc */
            return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
        }
        int rc = 0;
        if (!ucs4_blank(text, text_len)) {
            rc = emit_text(eng, out_parent, text, text_len);
        }
        PyMem_Free(text);
        return rc;
    }
    if (node->type == TH_NODE_CDATA) {
        /* A CDATA section in the stylesheet is significant character data (never stripped as
           whitespace); it emits as text, which cdata-section-elements may later re-wrap. */
        Py_ssize_t text_len = 0;
        Py_UCS4 *text = th_node_data(eng->sheet_tree, node, &text_len);
        if (text == NULL) {                    /* GCOVR_EXCL_BR_LINE: alloc */
            return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
        }
        int rc = emit_text(eng, out_parent, text, text_len);
        PyMem_Free(text);
        return rc;
    }
    return 0;
}

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
        Py_UCS4 *text;
        Py_ssize_t text_len = 0;
        int rc = 0;
        for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
            if (child->type == TH_NODE_TEXT) {
                text = th_node_data(eng->sheet_tree, child, &text_len);
                if (text == NULL) {                    /* GCOVR_EXCL_BR_LINE: alloc */
                    return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
                }
                rc = emit_text(eng, out_parent, text, text_len);
                PyMem_Free(text);
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
        const Py_UCS4 *name = attr_lookup(eng->sheet_tree, node, "name", &name_len);
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
        const Py_UCS4 *terminate = attr_lookup(eng->sheet_tree, node, "terminate", &terminate_len);
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
        /* sort, with-param and param are handled by their parent; anything else the core
           does not model (fallback, apply-imports, ...) instantiates nothing. */
        return 0;
    }
}

/* Instantiate one instruction (an xsl:* element, a literal result element, or a text
   node) into out_parent. */
static int instantiate_one_fast(engine *eng, th_node *node, th_node *out_parent) {
    if (node->type != TH_NODE_ELEMENT) {
        return instantiate_non_element(eng, node, out_parent);
    }
    if (!is_any_xsl_fast(eng, node)) {
        if (is_extension_element(eng, node)) {
            return instantiate_fallback(eng, node, out_parent);
        }
        return instantiate_literal(eng, node, out_parent);
    }
    Py_ssize_t offset = eng->xsl_prefix_len + 1;
    return instantiate_classified(eng, node, out_parent, xsl_classify(node->text + offset, node->text_len - offset));
}

static int instantiate_one_dynamic(engine *eng, th_node *node, th_node *out_parent) {
    if (node->type != TH_NODE_ELEMENT) {
        return instantiate_non_element(eng, node, out_parent);
    }
    if (!is_any_xsl_dynamic(eng, node)) {
        if (is_extension_element(eng, node)) {
            return instantiate_fallback(eng, node, out_parent);
        }
        return instantiate_literal(eng, node, out_parent);
    }
    Py_ssize_t local_len;
    Py_ssize_t prefix_len;
    const Py_UCS4 *local = qname_local(node, &local_len, &prefix_len);
    return instantiate_classified(eng, node, out_parent, xsl_classify(local, local_len));
}

/* Instantiate every child of `body` (skipping the param declarations already bound). */
static int instantiate_body(engine *eng, th_node *body, th_node *out_parent) {
    if (++eng->depth > XSLT_MAX_DEPTH) {
        eng->depth--;
        PyErr_SetString(PyExc_RecursionError, "xslt: template nesting too deep");
        return fail_py(eng);
    }
    Py_ssize_t scope_mark = eng->scope_len;
    int rc = 0;
    if (eng->xsl_ns_dynamic) {
        for (th_node *child = body->first_child; child != NULL && rc == 0; child = child->next_sibling) {
            if (is_xsl_dynamic(eng, child, "param") || is_xsl_dynamic(eng, child, "sort")) {
                continue;
            }
            rc = instantiate_one_dynamic(eng, child, out_parent);
        }
    } else {
        for (th_node *child = body->first_child; child != NULL && rc == 0; child = child->next_sibling) {
            /* xsl:param leads a template body and xsl:sort leads a for-each; both are read
               by the parent, not instantiated. A stray xsl:with-param produces nothing. */
            if (is_xsl_fast(eng, child, "param") || is_xsl_fast(eng, child, "sort")) {
                continue;
            }
            rc = instantiate_one_fast(eng, child, out_parent);
        }
    }
    scope_drop(eng, scope_mark); /* local xsl:variable bindings fall out of scope */
    eng->depth--;
    return rc;
}

/* ---- stylesheet parsing --------------------------------------------------- */

static int push_rule(engine *eng, xslt_rule rule) {
    if (eng->nrules == eng->rules_cap) {
        Py_ssize_t cap = eng->rules_cap == 0 ? 16 : eng->rules_cap * 2;
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
    const Py_UCS4 *name = attr_lookup(eng->sheet_tree, element, "name", &name_len);
    if (name != NULL) {
        if (eng->nnamed == eng->named_cap) {
            Py_ssize_t cap = eng->named_cap == 0 ? 8 : eng->named_cap * 2;
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
    const Py_UCS4 *match = attr_lookup(eng->sheet_tree, element, "match", &match_len);
    if (match == NULL) {
        return 0;
    }
    Py_ssize_t mode_len = 0;
    const Py_UCS4 *mode = attr_lookup(eng->sheet_tree, element, "mode", &mode_len);
    Py_ssize_t priority_len = 0;
    const Py_UCS4 *priority = attr_lookup(eng->sheet_tree, element, "priority", &priority_len);
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
    const Py_UCS4 *name = attr_lookup(eng->sheet_tree, element, "name", &name_len);
    Py_ssize_t match_len = 0;
    const Py_UCS4 *match = attr_lookup(eng->sheet_tree, element, "match", &match_len);
    Py_ssize_t use_len = 0;
    const Py_UCS4 *use = attr_lookup(eng->sheet_tree, element, "use", &use_len);
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
    if (eng->nkeys == eng->keys_cap) {
        Py_ssize_t cap = eng->keys_cap == 0 ? 4 : eng->keys_cap * 2;
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
    key.match_prog = match_prog;
    key.use_prog = use_prog;
    eng->keys[eng->nkeys++] = key;
    return 0;
}

static int parse_attrset(engine *eng, th_node *element) {
    Py_ssize_t name_len = 0;
    const Py_UCS4 *name = attr_lookup(eng->sheet_tree, element, "name", &name_len);
    if (name == NULL) {
        return fail(eng, "xsl:attribute-set requires a name attribute");
    }
    if (eng->nattrsets == eng->attrsets_cap) {
        Py_ssize_t cap = eng->attrsets_cap == 0 ? 8 : eng->attrsets_cap * 2;
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
    const Py_UCS4 *style = attr_lookup(eng->sheet_tree, element, "stylesheet-prefix", &style_len);
    Py_ssize_t result_len = 0;
    const Py_UCS4 *result = attr_lookup(eng->sheet_tree, element, "result-prefix", &result_len);
    if (style == NULL || result == NULL) {
        return fail(eng, "xsl:namespace-alias requires stylesheet-prefix and result-prefix");
    }
    Py_ssize_t uri_len = 0;
    const Py_UCS4 *uri = resolve_prefix_uri(eng, root, result, result_len, &uri_len);
    if (uri == NULL) {
        return fail(eng, "xsl:namespace-alias result-prefix is not a declared namespace");
    }
    if (eng->naliases == eng->aliases_cap) {
        Py_ssize_t cap = eng->aliases_cap == 0 ? 4 : eng->aliases_cap * 2;
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
        Py_ssize_t cap = eng->globals_cap == 0 ? 8 : eng->globals_cap * 2;
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
    const Py_UCS4 *list = attr_lookup(eng->sheet_tree, element, "elements", &list_len);
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
            Py_ssize_t cap = eng->spaces_cap == 0 ? 8 : eng->spaces_cap * 2;
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
    const Py_UCS4 *method = attr_lookup(eng->sheet_tree, element, "method", &method_len);
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
    const Py_UCS4 *omit = attr_lookup(eng->sheet_tree, element, "omit-xml-declaration", &omit_len);
    if (omit != NULL && ucs4_ascii_eq(omit, omit_len, "yes")) {
        eng->omit_xml_decl = 1;
    }
    Py_ssize_t cdata_len = 0;
    const Py_UCS4 *cdata = attr_lookup(eng->sheet_tree, element, "cdata-section-elements", &cdata_len);
    if (cdata != NULL) {
        eng->cdata_elements = cdata;
        eng->cdata_elements_len = cdata_len;
    }
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

static th_node *xslt_preorder_next(th_node *root, th_node *node) {
    if (node->first_child != NULL) {
        return node->first_child;
    }
    while (node != root && node->next_sibling == NULL) {
        node = node->parent;
    }
    return node == root ? NULL : node->next_sibling;
}

/* Append the text of every Text descendant of node, in document order. */
static int collect_output_text(th_node *node, xb *buffer) {
    for (th_node *child = node->first_child; child != NULL; child = xslt_preorder_next(node, child)) {
        if (child->type == TH_NODE_TEXT && /* GCOVR_EXCL_BR_LINE: the second condition fails only on allocation */
            xb_add(buffer, child->text, child->text_len) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;                                          /* GCOVR_EXCL_LINE */
        }
    }
    return 0;
}

/* Retype direct text children of cdata-section-elements so the serializer wraps them in CDATA. */
static void apply_cdata_sections(engine *eng, th_node *node) {
    for (th_node *child = node->first_child; child != NULL; child = xslt_preorder_next(node, child)) {
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

/* Concatenate the text descendants of the output root (xsl:output method="text"). */
static PyObject *serialize_text(engine *eng, th_node *root) {
    (void)eng;
    xb buffer = {0};
    if (collect_output_text(root, &buffer) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
        xb_free(&buffer);                         /* GCOVR_EXCL_LINE */
        return PyErr_NoMemory();                  /* GCOVR_EXCL_LINE */
    }
    PyObject *out = make_str(buffer.data, buffer.len);
    xb_free(&buffer);
    return out;
}

/* Serialize the output tree's children as XML or HTML markup. */
static PyObject *serialize_markup(engine *eng, th_node *root) {
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
        if (chunk == NULL) {         /* GCOVR_EXCL_BR_LINE: alloc */
            xb_free(&buffer);        /* GCOVR_EXCL_LINE */
            return PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
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
    for (Py_ssize_t index = 0; index < eng->nrules; index++) {
        match_set_free(&eng->rules[index].matched);
    }
    PyMem_Free(eng->rules);
    for (Py_ssize_t index = 0; index < eng->nkeys; index++) {
        strmap_free(&eng->keys[index].table);
    }
    PyMem_Free(eng->keys);
    if (eng->owns_model) {
        PyMem_Free(eng->named);
        PyMem_Free(eng->globals);
        PyMem_Free(eng->attrsets);
        PyMem_Free(eng->spaces);
        PyMem_Free(eng->aliases);
        PyMem_Free(eng->xsl_prefix);
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
    if (eng->out_tree != NULL) {
        th_tree_free(eng->out_tree);
    }
    /* snapshot_principal_stylesheet allocates this tree before model cleanup. */
    /* GCOVR_EXCL_BR_START */
    if (eng->owns_model && eng->merged_tree != NULL) {
        th_tree_free(eng->merged_tree);
    }
    /* GCOVR_EXCL_BR_STOP */
}

static int engine_start_run(engine *eng, const engine *model, th_tree *src_tree) {
    *eng = *model;
    eng->owns_model = 0;
    eng->src_tree = src_tree;
    eng->src_root = th_tree_document(src_tree);
    eng->out_tree = NULL;
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
    eng->ns_counter = 0;
    eng->gen_counter = 0;
    eng->depth = 0;
    eng->number_memo_node = NULL;
    eng->number_memo_instruction = NULL;
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
                xp_program *prog = xp_compile(expr, expr_len, errbuf, sizeof(errbuf));
                PyMem_Free(expr);
                if (prog == NULL) {
                    PyErr_Format(PyExc_ValueError, "xslt: bad parameter expression: %s", errbuf);
                    return fail_py(eng);
                }
                xp_result value;
                int status = eval_program(eng, prog, eng->src_root, 1, 1, &value);
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
            const Py_UCS4 *name = attr_lookup(eng->sheet_tree, child, "name", &name_len);
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
    /* A document element that is not xsl:stylesheet/xsl:transform is a simplified stylesheet
       (section 2.3): a literal result element carrying xsl:version whose whole content is the
       body of an implicit template matching the document root. */
    if (!is_xsl(eng, sheet_root, "stylesheet") && !is_xsl(eng, sheet_root, "transform")) {
        eng->simplified = 1;
        return 0;
    }
    eng->exclude_prefixes_len = 0;
    eng->exclude_prefixes =
        attr_lookup(eng->sheet_tree, sheet_root, "exclude-result-prefixes", &eng->exclude_prefixes_len);
    eng->ext_prefixes_len = 0;
    eng->ext_prefixes = attr_lookup(eng->sheet_tree, sheet_root, "extension-element-prefixes", &eng->ext_prefixes_len);
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
        Py_ssize_t cap = eng->stripped_cap == 0 ? 16 : eng->stripped_cap * 2;
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
    const Py_UCS4 *xmlspace = attr_lookup(eng->src_tree, element, "xml:space", &xmlspace_len);
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
            if (frame->strips && th_node_text_is_blank(eng->src_tree, child)) {
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

static int precompile_expression(engine *eng, const Py_UCS4 *source, Py_ssize_t length, const char *context) {
    char error[256];
    if (compile_expression(eng, source, length, error, sizeof(error)) != NULL) {
        return 0;
    }
    if (!PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: only allocation adds an exception during compilation */
        PyErr_Format(PyExc_ValueError, "xslt: bad %s: %s", context, error);
    }
    return fail_py(eng);
}

static int precompile_avt(engine *eng, const Py_UCS4 *source, Py_ssize_t length) {
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
        if (precompile_expression(eng, source + start, index - start, "expression in attribute value template") < 0) {
            return -1;
        }
    }
    return 0;
}

static int precompile_attribute(engine *eng, th_node *element, const char *name, const char *context) {
    Py_ssize_t length = 0;
    const Py_UCS4 *source = attr_lookup(eng->sheet_tree, element, name, &length);
    return source == NULL ? 0 : precompile_expression(eng, source, length, context);
}

static int precompile_pattern_attribute(engine *eng, th_node *element, const char *name) {
    Py_ssize_t length = 0;
    const Py_UCS4 *source = attr_lookup(eng->sheet_tree, element, name, &length);
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

static int precompile_instruction(engine *eng, th_node *element) {
    const char *select_context = NULL;
    const char *select_instructions[] = {"apply-templates", "for-each", "value-of",   "copy-of",
                                         "variable",        "param",    "with-param", "sort"};
    for (size_t index = 0; index < sizeof(select_instructions) / sizeof(select_instructions[0]); index++) {
        if (is_xsl(eng, element, select_instructions[index])) {
            select_context = select_instructions[index];
            break;
        }
    }
    if (select_context != NULL) {
        char context[64];
        snprintf(context, sizeof(context), "%s select", select_context);
        if (precompile_attribute(eng, element, "select", context) < 0) {
            return -1;
        }
    }
    if ((is_xsl(eng, element, "if") || is_xsl(eng, element, "when")) &&
        precompile_attribute(eng, element, "test", "test") < 0) {
        return -1;
    }
    if (is_xsl(eng, element, "number")) {
        if (precompile_attribute(eng, element, "value", "number value") < 0 ||
            precompile_pattern_attribute(eng, element, "count") < 0 ||
            precompile_pattern_attribute(eng, element, "from") < 0) {
            return -1;
        }
    }
    if (is_xsl(eng, element, "element") || is_xsl(eng, element, "attribute") ||
        is_xsl(eng, element, "processing-instruction")) {
        Py_ssize_t length = 0;
        const Py_UCS4 *source = attr_lookup(eng->sheet_tree, element, "name", &length);
        if (source != NULL && precompile_avt(eng, source, length) < 0) {
            return -1;
        }
    }
    if (is_xsl(eng, element, "attribute")) {
        Py_ssize_t length = 0;
        const Py_UCS4 *source = attr_lookup(eng->sheet_tree, element, "namespace", &length);
        if (source != NULL && precompile_avt(eng, source, length) < 0) {
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
            is_xsl_attr(eng, element, name, name_len)) {
            continue;
        }
        if (precompile_avt(eng, attr->value, attr->value_len) < 0) {
            return -1;
        }
    }
    return 0;
}

static int node_is_xsl(engine *eng, th_node *node) {
    Py_ssize_t local_len;
    Py_ssize_t prefix_len;
    (void)qname_local(node, &local_len, &prefix_len);
    return node_prefix_is_xsl(eng->sheet_tree, node, prefix_len);
}

static int precompile_stylesheet(engine *eng, th_node *root) {
    for (th_node *node = root; node != NULL; node = preorder_next(node, root)) {
        if (node->type != TH_NODE_ELEMENT) {
            continue;
        }
        if (node_is_xsl(eng, node)) {
            if (precompile_instruction(eng, node) < 0) {
                return -1;
            }
        } else if (precompile_literal_attributes(eng, node) < 0) {
            return -1;
        }
    }
    return precompile_expression(eng, &XPATH_DOT, 1, "sort select");
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
        rc = eng->simplified ? (eng->xsl_ns_dynamic ? instantiate_one_dynamic(eng, sheet_root, out_root)
                                                    : instantiate_one_fast(eng, sheet_root, out_root))
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

static PyObject *stylesheet_import_hrefs(PyObject *module, PyObject *stylesheet, PyObject *base, int allow_imports) {
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
            if (!ucs4_ascii_eq(local, local_len, "import") || !node_prefix_is_xsl(tree, child, prefix_len)) {
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
            const Py_UCS4 *href_value = attr_lookup(tree, child, "href", &href_len);
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
    if (error) {
        Py_DECREF(hrefs);
        return NULL;
    }
    return hrefs;
}

typedef struct {
    PyObject *path_type;
    PyObject *urlparse;
    PyObject *url2pathname;
    PyObject *root;
    int allow_imports;
#ifdef _WIN32
    HANDLE root_handle;
    wchar_t *root_final;
    size_t root_final_len;
#else
    int root_fd;
#endif
} import_policy;

static void import_descriptor_close(int descriptor) {
#ifdef _WIN32
    _close(descriptor);
#else
    close(descriptor);
#endif
}

static Py_ssize_t import_descriptor_read(int descriptor, char *buffer, size_t size) {
#ifdef _WIN32
    int count;
    Py_BEGIN_ALLOW_THREADS count = _read(descriptor, buffer, (unsigned int)size);
#else
    ssize_t count;
    Py_BEGIN_ALLOW_THREADS count = read(descriptor, buffer, size);
#endif
    Py_END_ALLOW_THREADS return (Py_ssize_t)count;
}

static void import_policy_clear(import_policy *policy) {
    Py_DECREF(policy->path_type);
    Py_DECREF(policy->urlparse);
    Py_DECREF(policy->url2pathname);
    Py_XDECREF(policy->root);
#ifdef _WIN32
    if (policy->root_handle != INVALID_HANDLE_VALUE) {
        CloseHandle(policy->root_handle);
    }
    PyMem_Free(policy->root_final);
#else
    if (policy->root_fd >= 0) {
        import_descriptor_close(policy->root_fd);
    }
#endif
}

static PyObject *import_path_from_url(import_policy *policy, PyObject *value, const char *name) {
    if (!PyUnicode_Check(value)) {
        return PyObject_CallOneArg(policy->path_type, value);
    }
    PyObject *parsed = PyObject_CallOneArg(policy->urlparse, value);
    if (parsed == NULL) {
        return NULL;
    }
    PyObject *scheme = PyObject_GetAttrString(parsed, "scheme");
    PyObject *netloc = PyObject_GetAttrString(parsed, "netloc");
    PyObject *url_path = PyObject_GetAttrString(parsed, "path");
    /* GCOVR_EXCL_BR_START: ParseResult guarantees the scheme, netloc and path attributes. */
    if (scheme == NULL || netloc == NULL || url_path == NULL) {
        Py_XDECREF(scheme);   /* GCOVR_EXCL_LINE */
        Py_XDECREF(netloc);   /* GCOVR_EXCL_LINE */
        Py_XDECREF(url_path); /* GCOVR_EXCL_LINE */
        Py_DECREF(parsed);    /* GCOVR_EXCL_LINE */
        return NULL;          /* GCOVR_EXCL_LINE */
    }
    /* GCOVR_EXCL_BR_STOP */
    int is_file = PyUnicode_CompareWithASCIIString(scheme, "file") == 0;
    int has_scheme = PyUnicode_GET_LENGTH(scheme) != 0;
    int windows_drive = PyUnicode_GET_LENGTH(value) > 2 && PyUnicode_ReadChar(value, 1) == ':' &&
                        (PyUnicode_ReadChar(value, 2) == '\\' || PyUnicode_ReadChar(value, 2) == '/');
    PyObject *local = NULL;
    if (is_file) {
        int local_host =
            PyUnicode_GET_LENGTH(netloc) == 0 || PyUnicode_CompareWithASCIIString(netloc, "localhost") == 0;
        if (!local_host) {
            PyErr_Format(PyExc_ValueError, "xsl:import %s file URL must point to a local path", name);
        } else {
            local = PyObject_CallOneArg(policy->url2pathname, url_path);
        }
    } else if ((has_scheme && !windows_drive) || PyUnicode_GET_LENGTH(netloc) != 0) {
        PyErr_Format(PyExc_ValueError, "xsl:import %s must be a local path or file URL", name);
    } else {
        local = PyObject_CallOneArg(policy->url2pathname, value);
    }
    Py_DECREF(url_path);
    Py_DECREF(netloc);
    Py_DECREF(scheme);
    Py_DECREF(parsed);
    if (local == NULL) {
        return NULL;
    }
    PyObject *path = PyObject_CallOneArg(policy->path_type, local);
    Py_DECREF(local);
    return path;
}

static PyObject *import_resolve(PyObject *path) {
    return PyObject_CallMethod(path, "resolve", NULL);
}

static int import_check_root(import_policy *policy, PyObject *path) {
    if (policy->root == NULL) {
        return 0;
    }
    PyObject *inside = PyObject_CallMethod(path, "is_relative_to", "O", policy->root);
    if (inside == NULL) { /* GCOVR_EXCL_BR_LINE: Path.is_relative_to returns bool or allocates */
        return -1;        /* GCOVR_EXCL_LINE */
    }
    int allowed = PyObject_IsTrue(inside);
    Py_DECREF(inside);
    if (allowed < 0) { /* GCOVR_EXCL_BR_LINE: bool truth testing cannot fail */
        return -1;     /* GCOVR_EXCL_LINE */
    }
    if (!allowed) {
        PyErr_Format(PyExc_ValueError, "xsl:import path escapes import_root: %S", path);
        return -1;
    }
    return 0;
}

#ifdef _WIN32
static HANDLE import_windows_open(PyObject *path, DWORD access, DWORD share, DWORD flags) {
    PyObject *text = PyObject_Str(path);
    if (text == NULL) {
        return INVALID_HANDLE_VALUE;
    }
    wchar_t *wide = PyUnicode_AsWideCharString(text, NULL);
    Py_DECREF(text);
    if (wide == NULL) {
        return INVALID_HANDLE_VALUE;
    }
    HANDLE handle;
    Py_BEGIN_ALLOW_THREADS handle = CreateFileW(wide, access, share, NULL, OPEN_EXISTING, flags, NULL);
    Py_END_ALLOW_THREADS PyMem_Free(wide);
    if (handle == INVALID_HANDLE_VALUE) {
        PyErr_SetExcFromWindowsErrWithFilenameObject(PyExc_OSError, 0, path);
    }
    return handle;
}

static wchar_t *import_windows_final_path(HANDLE handle, size_t *length) {
    DWORD size;
    Py_BEGIN_ALLOW_THREADS size = GetFinalPathNameByHandleW(handle, NULL, 0, FILE_NAME_NORMALIZED | VOLUME_NAME_DOS);
    Py_END_ALLOW_THREADS if (size == 0) {
        PyErr_SetFromWindowsErr(0);
        return NULL;
    }
    wchar_t *path = PyMem_Malloc(((size_t)size + 1) * sizeof(*path));
    if (path == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
        return NULL;      /* GCOVR_EXCL_LINE */
    }
    DWORD written;
    Py_BEGIN_ALLOW_THREADS written =
        GetFinalPathNameByHandleW(handle, path, size + 1, FILE_NAME_NORMALIZED | VOLUME_NAME_DOS);
    Py_END_ALLOW_THREADS if (written == 0 || written > size) {
        PyMem_Free(path);
        PyErr_SetFromWindowsErr(0);
        return NULL;
    }
    *length = written;
    return path;
}

static int import_windows_path_inside(import_policy *policy, const wchar_t *path, size_t length) {
    size_t root_length = policy->root_final_len;
    while (root_length > 0 &&
           (policy->root_final[root_length - 1] == L'\\' || policy->root_final[root_length - 1] == L'/')) {
        root_length--;
    }
    if (length < root_length || wcsncmp(policy->root_final, path, root_length) != 0) {
        return 0;
    }
    return length == root_length || path[root_length] == L'\\' || path[root_length] == L'/';
}

static int import_open_root(import_policy *policy) {
    policy->root_handle = import_windows_open(policy->root, FILE_READ_ATTRIBUTES, FILE_SHARE_READ | FILE_SHARE_WRITE,
                                              FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT);
    if (policy->root_handle == INVALID_HANDLE_VALUE) {
        return -1;
    }
    policy->root_final = import_windows_final_path(policy->root_handle, &policy->root_final_len);
    return policy->root_final == NULL ? -1 : 0;
}

static int import_open_file(import_policy *policy, PyObject *path) {
    HANDLE handle = import_windows_open(path, GENERIC_READ | FILE_READ_ATTRIBUTES,
                                        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, FILE_ATTRIBUTE_NORMAL);
    if (handle == INVALID_HANDLE_VALUE) {
        return -1;
    }
    size_t final_length;
    wchar_t *final_path = import_windows_final_path(handle, &final_length);
    if (final_path == NULL) {
        CloseHandle(handle);
        return -1;
    }
    int inside = import_windows_path_inside(policy, final_path, final_length);
    PyMem_Free(final_path);
    if (!inside) {
        CloseHandle(handle);
        PyErr_Format(PyExc_ValueError, "xsl:import path escapes import_root: %S", path);
        return -1;
    }
    int descriptor = _open_osfhandle((intptr_t)handle, _O_RDONLY | _O_BINARY);
    if (descriptor < 0) {
        CloseHandle(handle);
        PyErr_SetFromErrnoWithFilenameObject(PyExc_OSError, path);
    }
    return descriptor;
}
#else
static PyObject *import_fs_bytes(PyObject *path) {
    PyObject *value = PyObject_Str(path);
    if (value == NULL) { /* GCOVR_EXCL_BR_LINE: pathlib.Path.__str__ only allocates */
        return NULL;     /* GCOVR_EXCL_LINE */
    }
    PyObject *bytes = PyUnicode_EncodeFSDefault(value);
    Py_DECREF(value);
    return bytes;
}

static int import_open_beneath(int anchor, PyObject *path, PyObject *error_path, int require_directory) {
    PyObject *bytes = import_fs_bytes(path);
    if (bytes == NULL) { /* GCOVR_EXCL_BR_LINE: pathlib paths encode unless allocation fails */
        return -1;       /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t length = PyBytes_GET_SIZE(bytes);
    char *parts = PyMem_Malloc((size_t)length + 1);
    if (parts == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
        Py_DECREF(bytes); /* GCOVR_EXCL_LINE */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
        return -1;        /* GCOVR_EXCL_LINE */
    }
    memcpy(parts, PyBytes_AS_STRING(bytes), (size_t)length);
    parts[length] = '\0';
    Py_DECREF(bytes);
    int descriptor;
    do {
        descriptor = fcntl(anchor, F_DUPFD_CLOEXEC, 0);
    } while (descriptor < 0 && errno == EINTR); /* GCOVR_EXCL_BR_LINE: requires a signal during fcntl */
    if (descriptor < 0) {  /* GCOVR_EXCL_BR_LINE: requires process-wide descriptor exhaustion */
        PyMem_Free(parts); /* GCOVR_EXCL_LINE */
        PyErr_SetFromErrnoWithFilenameObject(PyExc_OSError, error_path); /* GCOVR_EXCL_LINE */
        return -1;                                                       /* GCOVR_EXCL_LINE */
    }
    char *cursor = parts;
    while (*cursor == '/') {
        cursor++;
    }
    while (*cursor != '\0') {
        char *separator = strchr(cursor, '/');
        if (separator != NULL) {
            *separator = '\0';
        }
        int last_component = separator == NULL;
        int flags = O_RDONLY | O_CLOEXEC | O_NOFOLLOW;
        if (!last_component || require_directory) {
            flags |= O_DIRECTORY;
        }
        int opened_descriptor;
        do {
            Py_BEGIN_ALLOW_THREADS opened_descriptor = openat(descriptor, cursor, flags);
            Py_END_ALLOW_THREADS
        } while (opened_descriptor < 0 && errno == EINTR); /* GCOVR_EXCL_BR_LINE: requires a signal during openat */
        if (opened_descriptor < 0) {
            int error = errno;
            import_descriptor_close(descriptor);
            PyMem_Free(parts);
            if (error == ELOOP || error == ENOTDIR) {
                PyErr_Format(PyExc_ValueError, "xsl:import path escapes import_root: %S", error_path);
            } else {
                errno = error;
                PyErr_SetFromErrnoWithFilenameObject(PyExc_OSError, error_path);
            }
            return -1;
        }
        import_descriptor_close(descriptor);
        descriptor = opened_descriptor;
        if (last_component) {
            break;
        }
        cursor = separator + 1;
    }
    PyMem_Free(parts);
    return descriptor;
}

static int import_open_root(import_policy *policy) {
    int anchor = open("/", O_RDONLY | O_CLOEXEC | O_DIRECTORY);
    if (anchor < 0) {                      /* GCOVR_EXCL_BR_LINE: supported POSIX systems expose the filesystem root */
        PyErr_SetFromErrno(PyExc_OSError); /* GCOVR_EXCL_LINE */
        return -1;                         /* GCOVR_EXCL_LINE */
    }
    policy->root_fd = import_open_beneath(anchor, policy->root, policy->root, 1);
    import_descriptor_close(anchor);
    return policy->root_fd < 0 ? -1 : 0;
}

static int import_open_file(import_policy *policy, PyObject *path) {
    PyObject *relative = PyObject_CallMethod(path, "relative_to", "O", policy->root);
    if (relative == NULL) { /* GCOVR_EXCL_BR_LINE: import_check_root established containment */
        return -1;          /* GCOVR_EXCL_LINE */
    }
    int descriptor = import_open_beneath(policy->root_fd, relative, path, 0);
    Py_DECREF(relative);
    return descriptor;
}
#endif

static PyObject *import_read_descriptor(int descriptor, PyObject *path) {
    char *data = NULL;
    size_t length = 0;
    size_t capacity = 0;
    for (;;) {
        if (length == capacity) {
            size_t grown_capacity;
            size_t grown_bytes;
            /* GCOVR_EXCL_BR_START: address space cannot hold the input. */
            if (!th_grow_cap(length + 1, capacity, 65536, sizeof(*data), &grown_capacity, &grown_bytes)) {
                PyMem_Free(data);                    /* GCOVR_EXCL_LINE */
                PyErr_NoMemory();                    /* GCOVR_EXCL_LINE */
                import_descriptor_close(descriptor); /* GCOVR_EXCL_LINE */
                return NULL;                         /* GCOVR_EXCL_LINE */
            }
            /* GCOVR_EXCL_BR_STOP */
            char *grown = PyMem_Realloc(data, grown_bytes);
            if (grown == NULL) {                     /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
                PyMem_Free(data);                    /* GCOVR_EXCL_LINE */
                PyErr_NoMemory();                    /* GCOVR_EXCL_LINE */
                import_descriptor_close(descriptor); /* GCOVR_EXCL_LINE */
                return NULL;                         /* GCOVR_EXCL_LINE */
            }
            data = grown;
            capacity = grown_capacity;
        }
        size_t remaining = capacity - length;
        Py_ssize_t count = import_descriptor_read(descriptor, data + length, remaining < 65536 ? remaining : 65536);
        if (count < 0 && errno == EINTR) { /* GCOVR_EXCL_BR_LINE: requires a signal during the read syscall */
            continue;                      /* GCOVR_EXCL_LINE */
        }
        if (count < 0) {
            int error = errno;
            PyMem_Free(data);
            import_descriptor_close(descriptor);
            errno = error;
            PyErr_SetFromErrnoWithFilenameObject(PyExc_OSError, path);
            return NULL;
        }
        if (count == 0) {
            break;
        }
        length += (size_t)count;
    }
    import_descriptor_close(descriptor);
    PyObject *text = PyUnicode_DecodeUTF8(data, (Py_ssize_t)length, "strict");
    PyMem_Free(data);
    return text;
}

static PyObject *import_read_text(import_policy *policy, PyObject *path) {
    if (policy->root == NULL) {
        return PyObject_CallMethod(path, "read_text", "s", "utf-8");
    }
    int descriptor = import_open_file(policy, path);
    if (descriptor < 0) {
        return NULL;
    }
    return import_read_descriptor(descriptor, path);
}

static PyObject *load_stylesheet_import(PyObject *module, import_policy *policy, PyObject *base, PyObject *href) {
    PyObject *current_path = import_path_from_url(policy, base, "base_url");
    if (current_path == NULL) {
        return NULL;
    }
    PyObject *current = import_resolve(current_path);
    Py_DECREF(current_path);
    if (current == NULL) { /* GCOVR_EXCL_BR_LINE: Path.resolve fails here only on allocation */
        return NULL;       /* GCOVR_EXCL_LINE */
    }
    PyObject *parent = PyObject_GetAttrString(current, "parent");
    PyObject *href_path = import_path_from_url(policy, href, "href");
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
    PyObject *path = import_resolve(joined);
    Py_DECREF(joined);
    if (path == NULL) {     /* GCOVR_EXCL_BR_LINE: Path.resolve fails here only on allocation */
        Py_DECREF(current); /* GCOVR_EXCL_LINE */
        return NULL;        /* GCOVR_EXCL_LINE */
    }
    if (import_check_root(policy, path) < 0) {
        Py_DECREF(path);
        Py_DECREF(current);
        return NULL;
    }
    PyObject *text = import_read_text(policy, path);
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

static int import_policy_init(import_policy *policy, int allow_imports, PyObject *import_root) {
    memset(policy, 0, sizeof(*policy));
    policy->allow_imports = allow_imports;
#ifdef _WIN32
    policy->root_handle = INVALID_HANDLE_VALUE;
#else
    policy->root_fd = -1;
#endif
    PyObject *pathlib = PyImport_ImportModule("pathlib");
    PyObject *parse = PyImport_ImportModule("urllib.parse");
    PyObject *request = PyImport_ImportModule("urllib.request");
    /* Bundled standard-library imports fail only on allocation. */
    /* GCOVR_EXCL_BR_START */
    if (pathlib == NULL || parse == NULL || request == NULL) {
        Py_XDECREF(pathlib); /* GCOVR_EXCL_LINE */
        Py_XDECREF(parse);   /* GCOVR_EXCL_LINE */
        Py_XDECREF(request); /* GCOVR_EXCL_LINE */
        return -1;           /* GCOVR_EXCL_LINE */
    }
    /* GCOVR_EXCL_BR_STOP */
    policy->path_type = PyObject_GetAttrString(pathlib, "Path");
    policy->urlparse = PyObject_GetAttrString(parse, "urlparse");
    policy->url2pathname = PyObject_GetAttrString(request, "url2pathname");
    Py_DECREF(request);
    Py_DECREF(parse);
    Py_DECREF(pathlib);
    /* Bundled modules expose these attributes. */
    /* GCOVR_EXCL_BR_START */
    if (policy->path_type == NULL || policy->urlparse == NULL || policy->url2pathname == NULL) {
        Py_XDECREF(policy->path_type);    /* GCOVR_EXCL_LINE */
        Py_XDECREF(policy->urlparse);     /* GCOVR_EXCL_LINE */
        Py_XDECREF(policy->url2pathname); /* GCOVR_EXCL_LINE */
        return -1;                        /* GCOVR_EXCL_LINE */
    }
    /* GCOVR_EXCL_BR_STOP */
    if (import_root != Py_None) {
        PyObject *root_path = PyObject_CallOneArg(policy->path_type, import_root);
        if (root_path == NULL) {
            import_policy_clear(policy);
            return -1;
        }
        policy->root = import_resolve(root_path);
        Py_DECREF(root_path);
        if (policy->root == NULL) {      /* GCOVR_EXCL_BR_LINE: Path.resolve fails here only on allocation */
            import_policy_clear(policy); /* GCOVR_EXCL_LINE */
            return -1;                   /* GCOVR_EXCL_LINE */
        }
        if (import_open_root(policy) < 0) {
            import_policy_clear(policy);
            return -1;
        }
    }
    return 0;
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

static int resolve_stylesheet_imports(PyObject *module, PyObject *stylesheet, PyObject *base, import_policy *policy,
                                      PyObject *imports, PyObject *active, PyObject *active_set) {
    PyObject *hrefs = stylesheet_import_hrefs(module, stylesheet, base, policy->allow_imports);
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
    import_policy policy;
    if (import_policy_init(&policy, allow_imports, import_root) < 0) {
        return NULL;
    }
    PyObject *imports = PyList_New(0);
    PyObject *active = PyList_New(0);
    PyObject *active_set = PyDict_New();
    if (imports == NULL || active == NULL || active_set == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        Py_XDECREF(imports);                                       /* GCOVR_EXCL_LINE */
        Py_XDECREF(active);                                        /* GCOVR_EXCL_LINE */
        Py_XDECREF(active_set);                                    /* GCOVR_EXCL_LINE */
        import_policy_clear(&policy);                              /* GCOVR_EXCL_LINE */
        return NULL;                                               /* GCOVR_EXCL_LINE */
    }
    int status = resolve_stylesheet_imports(module, stylesheet, base, &policy, imports, active, active_set);
    import_policy_clear(&policy);
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

PyObject *turbohtml_xslt_compile(PyObject *module, PyObject *args) {
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
    PyObject *capsule = PyCapsule_New(compiled, XSLT_CAPSULE, xslt_compiled_free);
    if (capsule == NULL) {              /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
        engine_clear(&compiled->model); /* GCOVR_EXCL_LINE */
        PyMem_Free(compiled);           /* GCOVR_EXCL_LINE */
    } /* GCOVR_EXCL_LINE */
    return capsule;
}

PyObject *turbohtml_xslt_transform(PyObject *module, PyObject *args) {
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
    (void)src_node; /* the transform roots at the source tree's document node */
    engine eng = {0};
    if (engine_start_run(&eng, &compiled->model, src_tree) < 0) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
        engine_clear(&eng);                                       /* GCOVR_EXCL_LINE */
        return PyErr_NoMemory();                                  /* GCOVR_EXCL_LINE */
    }
    PyObject *source_handle = turbohtml_node_handle(source_obj);
    (void)source_handle; /* used only by the critical-section macro, a no-op on the GIL build */
    PyObject *result = NULL;
    Py_BEGIN_CRITICAL_SECTION(source_handle);
    result = run_transform(&eng, compiled->sheet_root, params);
    Py_END_CRITICAL_SECTION();
    /* fail() sets eng.error without a Python exception; fail_py() sets the exception and
       leaves eng.error NULL. The two are exclusive, so eng.error != NULL implies no
       exception is set and the message needs raising. */
    if (result == NULL && eng.error != NULL) {
        PyErr_Format(PyExc_ValueError, "%s", eng.error);
    }
    engine_clear(&eng);
    return result;
}
