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
#include "dom/tree.h"
#include "tokenizer/binding.h"
#include "query/xpath/internal.h"

#include <math.h>
#include <string.h>

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
    int fits = th_grow_cap((size_t)(buf->len + extra), (size_t)buf->cap, 16, sizeof(Py_UCS4), &cap, &bytes);
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

enum output_method { OUT_XML, OUT_HTML, OUT_TEXT };

typedef struct engine {
    PyObject *module;
    th_tree *src_tree;
    th_tree *sheet_tree;
    th_tree *out_tree;
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

    Py_UCS4 *xsl_prefix;
    Py_ssize_t xsl_prefix_len;
    int output_method;
    int omit_xml_decl;

    var_bind *scope;
    Py_ssize_t scope_len;
    Py_ssize_t scope_cap;

    th_node *cur_node;
    Py_ssize_t cur_attr;
    Py_ssize_t ctx_pos;
    Py_ssize_t ctx_size;
    int gen_counter;
    int depth;

    const char *error;
    int py_error;
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

/* Whether node is an XSLT-namespace element whose local name is `local`, resolving
   the prefix bound to the XSLT namespace (usually "xsl") that the stylesheet declared. */
static int is_xsl(const engine *eng, const th_node *node, const char *local) {
    if (node->type != TH_NODE_ELEMENT) {
        return 0;
    }
    Py_ssize_t plen = eng->xsl_prefix_len;
    if (node->text_len < plen + 1 || node->text[plen] != ':') {
        return 0;
    }
    if (memcmp(node->text, eng->xsl_prefix, (size_t)plen * sizeof(Py_UCS4)) != 0) {
        return 0;
    }
    return ucs4_ascii_eq(node->text + plen + 1, node->text_len - plen - 1, local);
}

/* Whether an element is in the XSLT namespace (an xsl:* element), the test that tells
   an instruction from a literal result element. The caller only asks about elements. */
static int is_any_xsl(const engine *eng, const th_node *node) {
    Py_ssize_t plen = eng->xsl_prefix_len;
    return node->text_len > plen + 1 && node->text[plen] == ':' &&
           memcmp(node->text, eng->xsl_prefix, (size_t)plen * sizeof(Py_UCS4)) == 0;
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
static xp_program *compile_pattern(engine *eng, const Py_UCS4 *src, Py_ssize_t len) {
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
        if (!PyErr_Occurred()) {
            if (feature == NULL) {      /* GCOVR_EXCL_BR_LINE: a -3/-4 error always names a feature */
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

/* Evaluate an attribute value template ("literal {expr} literal") into freshly
   allocated code points. Returns 0 with the buffer and its length set through out_data
   and out_len (the caller PyMem_Frees the buffer), or -1 on error. */
static int eval_avt(engine *eng, const Py_UCS4 *src, Py_ssize_t len, Py_UCS4 **out_data, Py_ssize_t *out_len) {
    xb buffer = {0};
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_UCS4 ch = src[index];
        if (ch == '{' && index + 1 < len && src[index + 1] == '{') {
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
            xp_program *prog = xp_compile(src + start, end - start, errbuf, sizeof(errbuf));
            if (prog == NULL) {
                xb_free(&buffer);
                PyErr_Format(PyExc_ValueError, "xslt: bad expression in attribute value template: %s", errbuf);
                fail_py(eng);
                return -1;
            }
            xp_result value;
            int status = eval_program(eng, prog, eng->cur_node, eng->ctx_pos, eng->ctx_size, &value);
            xp_free(prog);
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
    xp_program *prog = xp_compile(select, select_len, errbuf, sizeof(errbuf));
    if (prog == NULL) {
        PyErr_Format(PyExc_ValueError, "xslt: bad value-of select: %s", errbuf);
        return fail_py(eng);
    }
    xp_result value;
    int status = eval_program(eng, prog, eng->cur_node, eng->ctx_pos, eng->ctx_size, &value);
    xp_free(prog);
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
    xp_program *prog = xp_compile(select, select_len, errbuf, sizeof(errbuf));
    if (prog == NULL) {
        PyErr_Format(PyExc_ValueError, "xslt: bad copy-of select: %s", errbuf);
        return fail_py(eng);
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
                        xp_free(prog);                     /* GCOVR_EXCL_LINE */
                        return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
                    }
                    th_node_append_child(out_parent, copy);
                }
                xp_free(prog);
                return 0;
            }
        }
    }
    xp_result value;
    int status = eval_program(eng, prog, eng->cur_node, eng->ctx_pos, eng->ctx_size, &value);
    xp_free(prog);
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
    return instantiate_body(eng, instruction, element);
}

/* xsl:attribute name={avt}: set an attribute on the containing result element. */
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
    xp_program *prog = xp_compile(test, test_len, errbuf, sizeof(errbuf));
    if (prog == NULL) {
        PyErr_Format(PyExc_ValueError, "xslt: bad test: %s", errbuf);
        return fail_py(eng);
    }
    xp_result value;
    int status = eval_program(eng, prog, eng->cur_node, eng->ctx_pos, eng->ctx_size, &value);
    xp_free(prog);
    if (status < 0) {
        return fail_py(eng);
    }
    *out_bool = to_boolean(eng->src_tree, &value);
    xp_result_free(&value);
    return 0;
}

/* ---- sorting -------------------------------------------------------------- */

typedef struct {
    th_node *node;
    Py_ssize_t attr;
    Py_UCS4 *key;
    Py_ssize_t key_len;
    double number;
} sort_item;

typedef struct {
    xp_program *prog;
    int numeric;
    int descending;
} sort_spec;

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
        static const Py_UCS4 dot = '.';
        if (select == NULL) {
            select = &dot;
            select_len = 1;
        }
        char errbuf[256];
        xp_program *prog = xp_compile(select, select_len, errbuf, sizeof(errbuf));
        if (prog == NULL) {
            for (int index = 0; index < count; index++) {
                xp_free(specs[index].prog);
            }
            PyErr_Format(PyExc_ValueError, "xslt: bad sort select: %s", errbuf);
            fail_py(eng);
            return -1;
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
    /* Precompute each spec's key per item, then insertion-sort (stable). */
    sort_item *items = PyMem_Malloc((size_t)set->len * (size_t)nspecs * sizeof(sort_item));
    if (items == NULL) {                   /* GCOVR_EXCL_BR_LINE: alloc */
        return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
    }
    for (Py_ssize_t index = 0; index < set->len; index++) {
        for (int spec = 0; spec < nspecs; spec++) {
            sort_item *slot = &items[index * nspecs + spec];
            slot->node = set->items[index].node;
            slot->attr = set->items[index].attr;
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
    /* Stable insertion sort over row indices. */
    Py_ssize_t *order = PyMem_Malloc((size_t)set->len * sizeof(Py_ssize_t));
    if (order == NULL) {                                                 /* GCOVR_EXCL_BR_LINE: alloc */
        for (Py_ssize_t index = 0; index < set->len * nspecs; index++) { /* GCOVR_EXCL_LINE */
            PyMem_Free(items[index].key);                                /* GCOVR_EXCL_LINE */
        } /* GCOVR_EXCL_LINE */
        PyMem_Free(items);                 /* GCOVR_EXCL_LINE */
        return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
    }
    for (Py_ssize_t index = 0; index < set->len; index++) {
        order[index] = index;
    }
    for (Py_ssize_t index = 1; index < set->len; index++) {
        Py_ssize_t current = order[index];
        Py_ssize_t probe = index - 1;
        while (probe >= 0) {
            Py_ssize_t compare = order[probe];
            int decision = 0;
            for (int spec = 0; spec < nspecs && decision == 0; spec++) {
                sort_item *a = &items[compare * nspecs + spec];
                sort_item *b = &items[current * nspecs + spec];
                int cmp;
                if (specs[spec].numeric) {
                    int a_nan = isnan(a->number);
                    int b_nan = isnan(b->number);
                    if (a_nan && b_nan) {
                        cmp = 0;
                    } else if (a_nan) {
                        cmp = -1;
                    } else if (b_nan) {
                        cmp = 1;
                    } else {
                        cmp = a->number < b->number ? -1 : (a->number > b->number ? 1 : 0);
                    }
                } else {
                    Py_ssize_t min_len = a->key_len < b->key_len ? a->key_len : b->key_len;
                    cmp = 0;
                    for (Py_ssize_t pos = 0; pos < min_len && cmp == 0; pos++) {
                        cmp = a->key[pos] < b->key[pos] ? -1 : (a->key[pos] > b->key[pos] ? 1 : 0);
                    }
                    if (cmp == 0) {
                        cmp = a->key_len < b->key_len ? -1 : (a->key_len > b->key_len ? 1 : 0);
                    }
                }
                if (specs[spec].descending) {
                    cmp = -cmp;
                }
                decision = cmp;
            }
            if (decision <= 0) {
                break;
            }
            order[probe + 1] = order[probe];
            probe--;
        }
        order[probe + 1] = current;
    }
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
        sorted[index] = set->items[order[index]];
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

static int format_number_token(xb *out, long value, Py_UCS4 style) {
    if (style == 'a' || style == 'A') {
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
    if (style == 'i' || style == 'I') {
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

/* Count preceding siblings (plus self) matching the same name as node, section 7.7
   single-level numbering with no count/from patterns. */
static long number_single(th_node *node) {
    long count = 1;
    for (th_node *prev = node->prev_sibling; prev != NULL; prev = prev->prev_sibling) {
        if (prev->type == node->type &&
            (node->type != TH_NODE_ELEMENT ||
             (prev->text_len == node->text_len &&
              memcmp(prev->text, node->text, (size_t)node->text_len * sizeof(Py_UCS4)) == 0))) {
            count++;
        }
    }
    return count;
}

static int do_number(engine *eng, th_node *instruction, th_node *out_parent) {
    long value;
    Py_ssize_t value_len = 0;
    const Py_UCS4 *value_expr = attr_lookup(eng->sheet_tree, instruction, "value", &value_len);
    if (value_expr != NULL) {
        char errbuf[256];
        xp_program *prog = xp_compile(value_expr, value_len, errbuf, sizeof(errbuf));
        if (prog == NULL) {
            PyErr_Format(PyExc_ValueError, "xslt: bad number value: %s", errbuf);
            return fail_py(eng);
        }
        xp_result result;
        int status = eval_program(eng, prog, eng->cur_node, eng->ctx_pos, eng->ctx_size, &result);
        xp_free(prog);
        if (status < 0) {
            return fail_py(eng);
        }
        value = (long)floor(to_number(eng->src_tree, &result) + 0.5);
        xp_result_free(&result);
    } else {
        value = eng->cur_attr >= 0 ? 1 : number_single(eng->cur_node);
    }
    Py_ssize_t format_len = 0;
    const Py_UCS4 *format = attr_lookup(eng->sheet_tree, instruction, "format", &format_len);
    Py_UCS4 style = '1';
    Py_ssize_t pad = 1;
    if (format != NULL && format_len > 0) {
        if (format[0] >= '0' && format[0] <= '9') {
            /* A decimal token's length is its minimum width: "01" pads to two digits. */
            pad = 0;
            while (pad < format_len && format[pad] >= '0' && format[pad] <= '9') {
                pad++;
            }
            style = '1';
        } else {
            style = format[format_len - 1];
        }
    }
    xb buffer = {0};
    if (style == '1' && pad > 1) {
        char raw[32];
        int raw_len = snprintf(raw, sizeof(raw), "%ld", value);
        for (Py_ssize_t index = raw_len; index < pad; index++) {
            if (xb_add_char(&buffer, '0') < 0) {   /* GCOVR_EXCL_BR_LINE: alloc */
                xb_free(&buffer);                  /* GCOVR_EXCL_LINE */
                return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
            }
        }
        if (xb_add_ascii(&buffer, raw) < 0) {  /* GCOVR_EXCL_BR_LINE: alloc */
            xb_free(&buffer);                  /* GCOVR_EXCL_LINE */
            return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
        }
    } else if (format_number_token(&buffer, value, style) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
        xb_free(&buffer);                                        /* GCOVR_EXCL_LINE */
        return fail(eng, "out of memory");                       /* GCOVR_EXCL_LINE */
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
        xp_program *prog = xp_compile(select, select_len, errbuf, sizeof(errbuf));
        if (prog == NULL) {
            PyErr_Format(PyExc_ValueError, "xslt: bad variable select: %s", errbuf);
            return fail_py(eng);
        }
        int status = eval_program(eng, prog, eng->cur_node, eng->ctx_pos, eng->ctx_size, out_value);
        xp_free(prog);
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
    xp_program *prog = xp_compile(select, select_len, errbuf, sizeof(errbuf));
    if (prog == NULL) {
        PyErr_Format(PyExc_ValueError, "xslt: bad for-each select: %s", errbuf);
        return fail_py(eng);
    }
    xp_result value;
    int status = eval_program(eng, prog, eng->cur_node, eng->ctx_pos, eng->ctx_size, &value);
    xp_free(prog);
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
        for (int index = 0; index < nspecs; index++) {
            xp_free(specs[index].prog);
        }
        xp_result_free(&value);
        return -1;
    }
    for (int index = 0; index < nspecs; index++) {
        xp_free(specs[index].prog);
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
        xp_program *prog = xp_compile(select, select_len, errbuf, sizeof(errbuf));
        if (prog == NULL) {
            PyErr_Format(PyExc_ValueError, "xslt: bad apply-templates select: %s", errbuf);
            return fail_py(eng);
        }
        int status = eval_program(eng, prog, eng->cur_node, eng->ctx_pos, eng->ctx_size, &value);
        xp_free(prog);
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
        for (int index = 0; index < nspecs; index++) {
            xp_free(specs[index].prog);
        }
        xp_result_free(&value);
        return -1;
    }
    for (int index = 0; index < nspecs; index++) {
        xp_free(specs[index].prog);
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

/* Copy a literal result element into the output, resolving attribute value
   templates, then instantiate its children inside it. */
static int instantiate_literal(engine *eng, th_node *element, th_node *out_parent) {
    uint16_t atom = atom_for_name(element->text, element->text_len);
    th_node *copy = th_tree_make_element(eng->out_tree, element->text, element->text_len, atom, 0);
    if (copy == NULL) {                    /* GCOVR_EXCL_BR_LINE: alloc */
        return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
    }
    for (Py_ssize_t index = 0; index < element->attr_count; index++) {
        const th_node_attr *attr = &element->attrs[index];
        Py_ssize_t name_len = 0;
        const char *name = th_attr_name(eng->sheet_tree, attr->name_atom, &name_len);
        /* Drop the stylesheet's own xmlns:xsl declaration; keep other attributes. */
        if (name_len >= 6 && memcmp(name, "xmlns:", 6) == 0) {
            continue;
        }
        if (name_len == 5 && memcmp(name, "xmlns", 5) == 0) {
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

/* Instantiate one instruction (an xsl:* element, a literal result element, or a text
   node) into out_parent. */
static int instantiate_one(engine *eng, th_node *node, th_node *out_parent) {
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
    if (node->type != TH_NODE_ELEMENT) {
        return 0; /* comments / PIs in the stylesheet are ignored */
    }
    if (!is_any_xsl(eng, node)) {
        return instantiate_literal(eng, node, out_parent);
    }
    if (is_xsl(eng, node, "value-of")) {
        return do_value_of(eng, node, out_parent);
    }
    if (is_xsl(eng, node, "apply-templates")) {
        return apply_templates(eng, node, out_parent, NULL, 0);
    }
    if (is_xsl(eng, node, "call-template")) {
        return do_call_template(eng, node, out_parent);
    }
    if (is_xsl(eng, node, "for-each")) {
        return do_for_each(eng, node, out_parent);
    }
    if (is_xsl(eng, node, "if")) {
        int truth;
        if (eval_test(eng, node, &truth) < 0) {
            return -1;
        }
        return truth ? instantiate_body(eng, node, out_parent) : 0;
    }
    if (is_xsl(eng, node, "choose")) {
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
    }
    if (is_xsl(eng, node, "text")) {
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
    if (is_xsl(eng, node, "element")) {
        return do_element(eng, node, out_parent);
    }
    if (is_xsl(eng, node, "attribute")) {
        return do_attribute(eng, node, out_parent);
    }
    if (is_xsl(eng, node, "copy")) {
        return do_copy(eng, node, out_parent);
    }
    if (is_xsl(eng, node, "copy-of")) {
        return do_copy_of(eng, node, out_parent);
    }
    if (is_xsl(eng, node, "variable")) {
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
    if (is_xsl(eng, node, "number")) {
        return do_number(eng, node, out_parent);
    }
    if (is_xsl(eng, node, "comment")) {
        return do_comment(eng, node, out_parent);
    }
    if (is_xsl(eng, node, "processing-instruction")) {
        return do_pi(eng, node, out_parent);
    }
    if (is_xsl(eng, node, "message")) {
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
    /* sort, with-param and param are handled by their parent; anything else the core
       does not model (fallback, apply-imports, ...) instantiates nothing. */
    return 0;
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
    for (th_node *child = body->first_child; child != NULL && rc == 0; child = child->next_sibling) {
        /* xsl:param leads a template body and xsl:sort leads a for-each; both are read
           by the parent, not instantiated. A stray xsl:with-param falls through to
           instantiate_one, which produces nothing for it. */
        if (is_xsl(eng, child, "param") || is_xsl(eng, child, "sort")) {
            continue;
        }
        rc = instantiate_one(eng, child, out_parent);
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
        rule.body = element;
        rule.mode = (Py_UCS4 *)mode;
        rule.mode_len = mode_len;
        if (push_rule(eng, rule) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            xp_free(prog);              /* GCOVR_EXCL_LINE */
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
    xp_program *use_prog = xp_compile(use, use_len, errbuf, sizeof(errbuf));
    if (use_prog == NULL) {
        xp_free(match_prog);
        PyErr_Format(PyExc_ValueError, "xslt: bad key use expression: %s", errbuf);
        return fail_py(eng);
    }
    if (eng->nkeys == eng->keys_cap) {
        Py_ssize_t cap = eng->keys_cap == 0 ? 4 : eng->keys_cap * 2;
        xslt_key *grown = PyMem_Realloc(eng->keys, (size_t)cap * sizeof(xslt_key));
        if (grown == NULL) {                   /* GCOVR_EXCL_BR_LINE: alloc */
            xp_free(match_prog);               /* GCOVR_EXCL_LINE */
            xp_free(use_prog);                 /* GCOVR_EXCL_LINE */
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

static void parse_output(engine *eng, th_node *element) {
    Py_ssize_t method_len = 0;
    const Py_UCS4 *method = attr_lookup(eng->sheet_tree, element, "method", &method_len);
    if (method != NULL) {
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
}

/* Resolve the prefix bound to the XSLT namespace from the stylesheet root's xmlns
   declarations, defaulting to "xsl". Sets eng->xsl_prefix to a freshly allocated
   code-point buffer (freed in engine_clear). Returns 0, or -1 on allocation failure. */
static int resolve_xsl_prefix(engine *eng, th_node *root) {
    const char *prefix = "xsl";
    Py_ssize_t prefix_len = 3;
    for (Py_ssize_t index = 0; index < root->attr_count; index++) {
        const th_node_attr *attr = &root->attrs[index];
        Py_ssize_t name_len = 0;
        const char *name = th_attr_name(eng->sheet_tree, attr->name_atom, &name_len);
        /* A parse_xml stylesheet never has a valueless attribute, so attr->value is set. */
        if (name_len > 6 && memcmp(name, "xmlns:", 6) == 0 &&
            ucs4_ascii_eq(attr->value, attr->value_len, "http://www.w3.org/1999/XSL/Transform")) {
            prefix = name + 6;
            prefix_len = name_len - 6;
        }
    }
    Py_UCS4 *owned = ucs4_from_ascii(prefix, prefix_len, &eng->xsl_prefix_len);
    if (owned == NULL) {                   /* GCOVR_EXCL_BR_LINE: alloc */
        return fail(eng, "out of memory"); /* GCOVR_EXCL_LINE */
    }
    eng->xsl_prefix = owned;
    return 0;
}

/* Sort rules by descending (priority, position) so best_rule's first
   match is the conflict-resolution winner. */
static int rule_order(const void *left_ptr, const void *right_ptr) {
    const xslt_rule *left = left_ptr;
    const xslt_rule *right = right_ptr;
    /* Import precedence is not modeled (no xsl:import), so a single stylesheet's rules
       order by priority then document position. Positions are unique small sequential
       integers, so their signed difference orders them branchlessly (later position
       sorts first) -- a ternary here leaves one arm that only some qsort implementations
       ever call, which diverges across C libraries. */
    if (left->priority != right->priority) {
        return left->priority < right->priority ? 1 : -1;
    }
    return (int)(right->position - left->position);
}

/* ---- output serialization ------------------------------------------------- */

/* Append the text of every Text descendant of node, in document order. */
static int collect_output_text(th_node *node, xb *buffer) {
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        if (child->type == TH_NODE_TEXT) {
            if (xb_add(buffer, child->text, child->text_len) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                return -1;                                          /* GCOVR_EXCL_LINE */
            }
        } else if (collect_output_text(child, buffer) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;                                       /* GCOVR_EXCL_LINE */
        }
    }
    return 0;
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
        xp_free(eng->rules[index].prog);
        match_set_free(&eng->rules[index].matched);
    }
    PyMem_Free(eng->rules);
    PyMem_Free(eng->named);
    PyMem_Free(eng->globals);
    for (Py_ssize_t index = 0; index < eng->nkeys; index++) {
        xp_free(eng->keys[index].match_prog);
        xp_free(eng->keys[index].use_prog);
        strmap_free(&eng->keys[index].table);
    }
    PyMem_Free(eng->keys);
    scope_drop(eng, 0);
    PyMem_Free(eng->scope);
    PyMem_Free(eng->xsl_prefix);
    if (eng->out_tree != NULL) {
        th_tree_free(eng->out_tree);
    }
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

/* Analyze the stylesheet: resolve the XSLT prefix and walk the top-level
   declarations. Returns 0, or -1 with eng->error / eng->py_error set. */
static int analyze(engine *eng, th_node *sheet_root) {
    if (resolve_xsl_prefix(eng, sheet_root) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;                                 /* GCOVR_EXCL_LINE */
    }
    int position = 0;
    for (th_node *child = sheet_root->first_child; child != NULL; child = child->next_sibling) {
        if (child->type != TH_NODE_ELEMENT) {
            continue;
        }
        if (is_xsl(eng, child, "template")) {
            if (parse_template(eng, child, &position) < 0) {
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
        }
        /* strip-space/preserve-space/decimal-format/attribute-set/import/include are
           not modeled; they parse without effect. */
    }
    if (eng->nrules > 1) {
        qsort(eng->rules, (size_t)eng->nrules, sizeof(xslt_rule), rule_order);
    }
    return 0;
}

/* ---- the module entry point ----------------------------------------------- */

static PyObject *run_transform(engine *eng, th_node *sheet_root, PyObject *params) {
    if (analyze(eng, sheet_root) < 0) {
        return NULL;
    }
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
    if (bind_globals(eng, params) < 0) {
        return NULL;
    }
    if (apply_to_item(eng, (xp_item){eng->src_root, -1}, 1, 1, NULL, 0, NULL, 0, out_root) < 0) {
        return NULL;
    }
    if (eng->output_method == OUT_TEXT) {
        return serialize_text(eng, out_root);
    }
    return serialize_markup(eng, out_root);
}

PyObject *turbohtml_xslt_transform(PyObject *module, PyObject *args) {
    PyObject *stylesheet_obj;
    PyObject *source_obj;
    PyObject *params = Py_None;
    if (!PyArg_ParseTuple(args, "OO|O", &stylesheet_obj, &source_obj, &params)) {
        return NULL;
    }
    if (params == Py_None) {
        params = NULL;
    } else if (!PyDict_Check(params)) {
        PyErr_SetString(PyExc_TypeError, "xslt: params must be a dict or None");
        return NULL;
    }
    th_tree *sheet_tree;
    th_node *sheet_node;
    if (turbohtml_node_borrow(module, stylesheet_obj, &sheet_tree, &sheet_node) < 0) {
        return NULL;
    }
    th_tree *src_tree;
    th_node *src_node;
    if (turbohtml_node_borrow(module, source_obj, &src_tree, &src_node) < 0) {
        return NULL;
    }
    (void)src_node; /* the transform roots at the source tree's document node */
    /* Locate the stylesheet's document element (xsl:stylesheet/xsl:transform). */
    th_node *sheet_root = sheet_node;
    if (sheet_root->type == TH_NODE_DOCUMENT) {
        /* A parsed document always holds a root element, so the loop always breaks and
           never runs to its natural (child == NULL) end. */
        for (th_node *child = sheet_root->first_child; child != NULL; /* GCOVR_EXCL_BR_LINE */
             child = child->next_sibling) {
            if (child->type == TH_NODE_ELEMENT) {
                sheet_root = child;
                break;
            }
        }
    }
    if (sheet_root->type != TH_NODE_ELEMENT) {
        PyErr_SetString(PyExc_ValueError, "xslt: the stylesheet has no root element");
        return NULL;
    }
    PyObject *source_handle = turbohtml_node_handle(source_obj);
    (void)source_handle; /* used only by the critical-section macro, a no-op on the GIL build */
    engine eng = {0};
    eng.module = module;
    eng.sheet_tree = sheet_tree;
    eng.src_tree = src_tree;
    eng.src_root = th_tree_document(src_tree);
    eng.output_method = OUT_XML;
    eng.cur_attr = -1;
    PyObject *result = NULL;
    /* The source tree drives every query; lock its handle for the free-threaded build.
       Both trees are read-only through the transform, and the output tree is private. */
    Py_BEGIN_CRITICAL_SECTION(source_handle);
    result = run_transform(&eng, sheet_root, params);
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
