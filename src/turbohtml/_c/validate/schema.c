/* XSD (XML Schema 1.0) and RELAX NG schema validation over a parse_xml tree.

   The engine behind turbohtml.validate.XMLSchema / RelaxNG (issue #539). A schema is
   compiled once into an in-memory model that borrows the parsed schema tree; each
   validation walks the instance tree the thin Python shim hands over and returns
   (valid, [errors]) where every error carries a message, a document-order path, a
   source line, and a coarse type. lxml's etree.XMLSchema / etree.RelaxNG shape the
   public API; the W3C XSD 1.0 and RELAX NG specs are the conformance authority.

   XSD structure is interpreted from the schema tree with a symbol table for the
   global element/type/attribute/group declarations. RELAX NG uses James Clark's
   derivative algorithm ("An algorithm for RELAX NG validation"): the schema compiles
   to a pattern algebra and validation takes the derivative of the pattern with respect
   to each start tag, attribute, text run, and end tag, which handles interleave and
   the repetition operators without backtracking.

   Schema names are read case-sensitively off the node (element tag from node->text,
   attribute names via th_attr_name) rather than the HTML-lowercasing atom finder, and
   namespaces resolve by walking the in-scope xmlns declarations, so an xs:/xsd:/
   default-namespace schema and a namespaced instance validate correctly. */

#include "core/common.h"
#include "dom/ownership.h"
#include "core/vec.h"
#include "core/work_steps.h"
#include "core/stack.h"

#include "tokenizer/binding.h" /* Py_BEGIN_CRITICAL_SECTION shim for the GIL/pre-3.13 build */

#include "dom/tree.h"

#include <math.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>

/* The non-ASCII halves of the name compare and hash stay out of line, so the ASCII loops inline into their callers. */
#ifndef TH_NOINLINE
#if defined(_MSC_VER)
#define TH_NOINLINE __declspec(noinline)
#elif defined(__GNUC__) || defined(__clang__)
#define TH_NOINLINE __attribute__((noinline))
#else
#define TH_NOINLINE
#endif
#endif

static const char XSD_NS[] = "http://www.w3.org/2001/XMLSchema";
static const char XSI_NS[] = "http://www.w3.org/2001/XMLSchema-instance";
static const char XSD_DT_NS[] = "http://www.w3.org/2001/XMLSchema-datatypes";
static const char RNG_NS[] = "http://relaxng.org/ns/structure/1.0";

/* Instance validation still has recursive grammar walks; schema compilation keeps its walks on explicit stacks.
   Preflight the tree far enough below the smallest supported thread stack that the recursive walks cannot exhaust it.
   The instance itself is descended with an
   explicit stack (walk_stack_reserve), so its nesting adds nothing to the grammar walk an element starts. */
#define TH_VALIDATE_MAX_DEPTH TH_DEPTH_LIMIT(400)

/* Instance levels validated by recursion on the C stack before the walk moves to the explicit stack. A level costs a
   few hundred bytes there, so the shallow levels stay small on any thread while the common shallow document pays no
   frame bookkeeping. */
#define TH_VALIDATE_SHALLOW_DEPTH 32

/* Each collected error keeps its own copy of the path, so the errors and the names in the paths are both capped. */
#define TH_VALIDATE_MAX_PATH_NAME 64
#define TH_VALIDATE_MAX_ERRORS 1000

/* ======================= bump arena ======================= */

typedef struct arena_block {
    struct arena_block *next;
    size_t used, cap;
} arena_block;

typedef struct {
    arena_block *head;
    int no_memory; /* an allocation failed, so what was built from this arena is partial */
} arena;

#ifdef FUZZING_BUILD_MODE_UNSAFE_FOR_PRODUCTION
/* One aligned slot, so a short schema opens several blocks. */
#define SCHEMA_ARENA_BLOCK 16u
#else
#define SCHEMA_ARENA_BLOCK 4096u
#endif

static void *arena_alloc(arena *mem, size_t size) {
#ifdef FUZZING_BUILD_MODE_UNSAFE_FOR_PRODUCTION
    size_t requested = size;
    size += TH_FUZZ_ARENA_GAP;
#endif
    size = (size + 15u) & ~(size_t)15u;
    arena_block *block = mem->head;
    if (block == NULL || block->cap - block->used < size) {
        size_t want = size > SCHEMA_ARENA_BLOCK ? size : SCHEMA_ARENA_BLOCK;
        arena_block *fresh = PyMem_Malloc(sizeof(arena_block) + want);
        if (fresh == NULL) {    /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            mem->no_memory = 1; /* GCOVR_EXCL_LINE: the compile or validate entry raises the MemoryError */
            return NULL;        /* GCOVR_EXCL_LINE */
        }
        fresh->next = mem->head;
        fresh->used = 0;
        fresh->cap = want;
#ifdef FUZZING_BUILD_MODE_UNSAFE_FOR_PRODUCTION
        TH_FUZZ_POISON(fresh + 1, want);
#endif
        mem->head = fresh;
        block = fresh;
    }
    void *out = (char *)(block + 1) + block->used;
    block->used += size;
#ifdef FUZZING_BUILD_MODE_UNSAFE_FOR_PRODUCTION
    TH_FUZZ_UNPOISON(out, requested);
#endif
    return out;
}

static void arena_free(arena *mem) {
    arena_block *block = mem->head;
    while (block != NULL) {
        arena_block *next = block->next;
#ifdef FUZZING_BUILD_MODE_UNSAFE_FOR_PRODUCTION
        TH_FUZZ_UNPOISON(block + 1, block->cap);
#endif
        PyMem_Free(block);
        block = next;
    }
    mem->head = NULL;
}

/* ======================= UCS4 helpers ======================= */

static int u_eq_ascii(const Py_UCS4 *text, Py_ssize_t len, const char *ascii) {
    for (Py_ssize_t index = 0; index < len; index++) {
        if (ascii[index] == '\0' || (Py_UCS4)(unsigned char)ascii[index] != text[index]) {
            return 0;
        }
    }
    return ascii[len] == '\0';
}

static int utf8_encode(Py_UCS4 codepoint, unsigned char *out) {
    if (codepoint < 0x80) {
        out[0] = (unsigned char)codepoint;
        return 1;
    }
    int extra = 1 + (codepoint >= 0x800) + (codepoint >= 0x10000);
    static const unsigned char lead[] = {0, 0xC0, 0xE0, 0xF0};
    out[0] = (unsigned char)(lead[extra] | (codepoint >> (6 * extra)));
    for (int index = 1; index <= extra; index++) {
        out[index] = (unsigned char)(0x80 | ((codepoint >> (6 * (extra - index))) & 0x3F));
    }
    return extra + 1;
}

TH_NOINLINE static int u_eq_utf8_wide(const Py_UCS4 *text, Py_ssize_t len, const char *bytes, Py_ssize_t bytes_len) {
    Py_ssize_t at = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        unsigned char encoded[4];
        int count = utf8_encode(text[index], encoded);
        if (bytes_len - at < count || memcmp(bytes + at, encoded, (size_t)count) != 0) {
            return 0;
        }
        at += count;
    }
    return at == bytes_len;
}

/* Whether UCS4 `text` equals the UTF-8 bytes of an interned name (th_attr_name). A name of as many bytes as code points
   is all ASCII, so that case compares a byte per code point; sign-extending the byte keeps a lead or continuation byte
   from equalling the Latin-1 code point of the same value. A longer byte string can match only when the last code point
   and the last byte are both ASCII and equal or both not ASCII, which rules out most names before any encoding. */
static inline int u_eq_utf8(const Py_UCS4 *text, Py_ssize_t len, const char *bytes, Py_ssize_t bytes_len) {
    if (bytes_len == len) {
        for (Py_ssize_t index = 0; index < len; index++) {
            if (text[index] != (Py_UCS4)(signed char)bytes[index]) {
                return 0;
            }
        }
        return 1;
    }
    /* UTF-8 spends one to four bytes per code point, so only len < bytes_len <= 4 * len can match (none when empty) */
    if ((size_t)(bytes_len - len - 1) >= (size_t)len * 3) {
        return 0;
    }
    Py_UCS4 last = text[len - 1];
    unsigned char tail = (unsigned char)bytes[bytes_len - 1];
    if (last < 0x80 ? last != tail : tail < 0x80) {
        return 0;
    }
    return u_eq_utf8_wide(text, len, bytes, bytes_len);
}

static Py_ssize_t utf8_length(const Py_UCS4 *text, Py_ssize_t len) {
    Py_ssize_t bytes = len;
    for (Py_ssize_t index = 0; index < len; index++) {
        bytes += (text[index] >= 0x80) + (text[index] >= 0x800) + (text[index] >= 0x10000);
    }
    return bytes;
}

static int u_eq_u(const Py_UCS4 *left, Py_ssize_t left_len, const Py_UCS4 *right, Py_ssize_t right_len) {
    return left_len == right_len && (left_len == 0 || memcmp(left, right, (size_t)left_len * sizeof(Py_UCS4)) == 0);
}

/* Whether a node carries character data (a text node or a CDATA section). */
static int is_chardata(const th_node *node) {
    return node->type == TH_NODE_TEXT || node->type == TH_NODE_CDATA;
}

static int is_xml_space(Py_UCS4 codepoint) {
    static const char whitespace[] = {' ', '\t', '\n', '\r'};
    for (size_t index = 0; index < sizeof(whitespace); index++) {
        if (codepoint == (Py_UCS4)(unsigned char)whitespace[index]) {
            return 1;
        }
    }
    return 0;
}

/* ======================= namespace-aware names ======================= */

typedef struct {
    const Py_UCS4 *local;
    Py_ssize_t local_len;
    const Py_UCS4 *uri;
    Py_ssize_t uri_len;
} qname;

/* The schema tree is immutable once compiled, yet is_schema_el reclassifies its element
   nodes millions of times while a document validates (the content-model matcher revisits
   the same particles on every step). resolve_ns walking the ancestor chain per call is the
   validator's hottest cost, so each schema element node's namespace-resolved qname is
   computed once at compile time and looked up by node pointer here. Built before any
   compile classification runs and never mutated afterwards, so a compiled schema shared
   across validating threads stays read-only. The same holds for the QNames in the type and base attributes, which
   declarations read on every instance element. */
struct th_schema;
typedef struct {
    th_node *node;
    qname name;
    qname type;
    qname base;
} sqname_entry;
static const sqname_entry *schema_node_entry(const struct th_schema *schema, th_node *node);
static const qname *schema_node_qname(const struct th_schema *schema, th_node *node);

static void split_prefix(const Py_UCS4 *name, Py_ssize_t len, const Py_UCS4 **local, Py_ssize_t *local_len,
                         const Py_UCS4 **prefix, Py_ssize_t *prefix_len) {
    for (Py_ssize_t index = 0; index < len; index++) {
        if (name[index] == ':') {
            *prefix = name;
            *prefix_len = index;
            *local = name + index + 1;
            *local_len = len - index - 1;
            return;
        }
    }
    *prefix = name;
    *prefix_len = 0;
    *local = name;
    *local_len = len;
}

/* Whether a UTF-8 attribute name is a namespace declaration (`xmlns` or `xmlns:prefix`). */
static int is_xmlns_decl(const char *name, Py_ssize_t len) {
    if (len < 5 || memcmp(name, "xmlns", 5) != 0) {
        return 0;
    }
    return len == 5 || name[5] == ':';
}

static const th_node_attr *attr_exact(th_tree *tree, th_node *node, const char *name, Py_ssize_t nlen) {
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        Py_ssize_t alen = 0;
        const char *abytes = th_attr_name(tree, node->attrs[index].name_atom, &alen);
        if (alen == nlen && memcmp(abytes, name, (size_t)nlen) == 0) {
            return &node->attrs[index];
        }
    }
    return NULL;
}

static const Py_UCS4 *xml_namespace_uri(void) {
    static const Py_UCS4 xml_uri[] = {'h', 't', 't', 'p', ':', '/', '/', 'w', 'w', 'w', '.', 'w', '3',
                                      '.', 'o', 'r', 'g', '/', 'X', 'M', 'L', '/', '1', '9', '9', '8',
                                      '/', 'n', 'a', 'm', 'e', 's', 'p', 'a', 'c', 'e', 0};
    return xml_uri;
}

/* A lookup whose walk to the root would read more attributes than this answers from the attribute scope instead. The
   elements above a node of ordinary markup carry fewer between them, so there a lookup costs what the walk always did,
   while N lookups below elements carrying M attributes cost one probe each instead of M reads. */
#define ATTR_SCOPE_MIN_WALK 32

/* The attributes in force at one node of a tree: for each attribute name, the one the nearest ancestor-or-self element
   carries. It holds the path from the top of the tree down to the node last asked about and, by attribute atom, the
   innermost attribute of that name on the path, so asking about the next node re-reads only the elements the two
   paths do not share. */
typedef struct {
    th_node *node;
    Py_ssize_t mark; /* the undo entries from here up are this node's own attributes */
} attr_frame;

typedef struct {
    uint32_t atom;
    const th_node_attr *outer; /* what the name was bound to above the element */
} attr_undo;

typedef struct {
    th_tree *tree;
    arena *mem;
    attr_frame *frames; /* the path, the top of the tree first */
    Py_ssize_t len, cap;
    const th_node_attr **bound; /* by attribute atom, for the first `atoms` of them */
    Py_ssize_t atoms;
    attr_undo *undo;
    Py_ssize_t undo_len, undo_cap;
    unsigned char *key; /* "xmlns:" and the UTF-8 prefix being resolved */
    Py_ssize_t key_cap;
    uint64_t attr_version; /* the tree's when the path was read; a RELAX NG include edits the schema tree */
} attr_scope;

/* `items` moved to a larger arena block with room for `need` entries of `size` bytes, the first `used` kept and the
   rest zeroed, or NULL with the arena marked out of memory. */
static void *attr_scope_grow(arena *mem, const void *items, Py_ssize_t used, Py_ssize_t *cap, Py_ssize_t need,
                             size_t size) {
    size_t grown, bytes;
    if (!th_grow_cap((size_t)need, (size_t)*cap, 8, size, &grown, &bytes)) { /* GCOVR_EXCL_BR_LINE: bounded by tree */
        mem->no_memory = 1;                                                  /* GCOVR_EXCL_LINE */
        return NULL;                                                         /* GCOVR_EXCL_LINE */
    }
    unsigned char *fresh = arena_alloc(mem, bytes);
    if (fresh == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return NULL;     /* GCOVR_EXCL_LINE */
    }
    if (used > 0) {
        memcpy(fresh, items, (size_t)used * size);
    }
    memset(fresh + (size_t)used * size, 0, bytes - (size_t)used * size);
    *cap = (Py_ssize_t)grown;
    return fresh;
}

/* Make `bound` hold an entry for atom; -1 when the arena is out of memory. */
static int attr_scope_cover(attr_scope *scope, uint32_t atom) {
    if ((Py_ssize_t)atom < scope->atoms) {
        return 0;
    }
    const th_node_attr **bound = attr_scope_grow(scope->mem, scope->bound, scope->atoms, &scope->atoms,
                                                 (Py_ssize_t)atom + 1, sizeof(th_node_attr *));
    if (bound == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return -1;       /* GCOVR_EXCL_LINE */
    }
    scope->bound = bound;
    return 0;
}

static void attr_scope_unwind(attr_scope *scope, Py_ssize_t mark) {
    while (scope->undo_len > mark) {
        const attr_undo *entry = &scope->undo[--scope->undo_len];
        scope->bound[entry->atom] = entry->outer;
    }
}

/* Extend the path by the node its next frame names, binding that node's attributes over the ones above it. */
static int attr_scope_push(attr_scope *scope) {
    attr_frame *frame = &scope->frames[scope->len];
    const th_node *node = frame->node;
    Py_ssize_t count = node->type == TH_NODE_ELEMENT ? node->attr_count : 0;
    Py_ssize_t mark = scope->undo_len;
    if (mark + count > scope->undo_cap) {
        attr_undo *undo =
            attr_scope_grow(scope->mem, scope->undo, mark, &scope->undo_cap, mark + count, sizeof(attr_undo));
        if (undo == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return -1;      /* GCOVR_EXCL_LINE */
        }
        scope->undo = undo;
    }
    /* last to first, so the first of two attributes with one name is the one bound, as a forward scan finds it */
    for (Py_ssize_t index = count - 1; index >= 0; index--) {
        TH_WORK_STEP();
        const th_node_attr *attr = &node->attrs[index];
        if (attr_scope_cover(scope, attr->name_atom) < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            attr_scope_unwind(scope, mark);                 /* GCOVR_EXCL_LINE */
            return -1;                                      /* GCOVR_EXCL_LINE */
        }
        scope->undo[scope->undo_len++] = (attr_undo){attr->name_atom, scope->bound[attr->name_atom]};
        scope->bound[attr->name_atom] = attr;
    }
    frame->mark = mark;
    scope->len++;
    return 0;
}

/* Make the path end at node: keep the frames of the ancestors it shares with the last node, drop the rest and read the
   nodes below them. The whole path is compared, not only its end, so a node moved since is read again. */
static int attr_scope_enter(attr_scope *scope, th_node *node) {
    uint64_t version = th_tree_attr_version(scope->tree);
    if (scope->attr_version != version) {
        attr_scope_unwind(scope, 0);
        scope->len = 0;
        scope->attr_version = version;
    }
    Py_ssize_t depth = 0;
    for (const th_node *walk = node->parent; walk != NULL; walk = walk->parent) {
        TH_WORK_STEP();
        depth++;
    }
    if (depth >= scope->cap) {
        attr_frame *frames =
            attr_scope_grow(scope->mem, scope->frames, scope->len, &scope->cap, depth + 1, sizeof(attr_frame));
        if (frames == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return -1;        /* GCOVR_EXCL_LINE */
        }
        scope->frames = frames;
    }
    /* from node up, name the path in every frame that differs from it; the topmost of those is where it diverges */
    Py_ssize_t shared = depth + 1;
    for (Py_ssize_t level = depth; level >= 0; level--, node = node->parent) {
        if (level >= scope->len || scope->frames[level].node != node) {
            scope->frames[level].node = node;
            shared = level;
        }
    }
    if (shared < scope->len) {
        attr_scope_unwind(scope, scope->frames[shared].mark);
        scope->len = shared;
    }
    while (scope->len <= depth) {
        if (attr_scope_push(scope) < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return -1;                    /* GCOVR_EXCL_LINE */
        }
    }
    return 0;
}

/* The attribute called name in force at node, or NULL; NULL as well when the arena runs out, which fails the call.
   Attribute names are interned per tree, so a name no element carries is answered without reading the path. */
static const th_node_attr *attr_scope_find(attr_scope *scope, th_node *node, const char *name, Py_ssize_t nlen) {
    uint32_t atom = th_attr_lookup(scope->tree, name, nlen);
    if (atom == UINT32_MAX) {
        return NULL;
    }
    /* GCOVR_EXCL_BR_START: arena OOM is unforceable */
    if (attr_scope_enter(scope, node) < 0 || attr_scope_cover(scope, atom) < 0) {
        /* GCOVR_EXCL_BR_STOP */
        return NULL; /* GCOVR_EXCL_LINE */
    }
    return scope->bound[atom];
}

/* The attribute called name on node or on its nearest ancestor element that carries one, or NULL. */
static const th_node_attr *inherited_attr(attr_scope *scope, th_node *node, const char *name, Py_ssize_t nlen) {
    th_tree *tree = scope->tree;
    Py_ssize_t walked = 0;
    for (th_node *walk = node; walk != NULL; walk = walk->parent) {
        if (walk->type != TH_NODE_ELEMENT) {
            continue;
        }
        walked += walk->attr_count;
        if (walked > ATTR_SCOPE_MIN_WALK) {
            return attr_scope_find(scope, node, name, nlen);
        }
        for (Py_ssize_t index = 0; index < walk->attr_count; index++) {
            TH_WORK_STEP();
            Py_ssize_t alen = 0;
            const char *abytes = th_attr_name(tree, walk->attrs[index].name_atom, &alen);
            if (alen == nlen && memcmp(abytes, name, (size_t)nlen) == 0) {
                return &walk->attrs[index];
            }
        }
    }
    return NULL;
}

/* resolve_ns from the attribute scope. The declaration of a prefix is the attribute named "xmlns:" and the prefix's
   UTF-8 bytes, which is how u_eq_utf8 pairs the two on the walk. Out of line so the walk, which runs for every name,
   keeps its registers. */
static TH_NOINLINE const th_node_attr *xmlns_scoped(attr_scope *scope, th_node *node, const Py_UCS4 *prefix,
                                                    Py_ssize_t prefix_len) {
    Py_ssize_t need = utf8_length(prefix, prefix_len) + 6;
    if (need > scope->key_cap) {
        unsigned char *key = attr_scope_grow(scope->mem, NULL, 0, &scope->key_cap, need, 1);
        if (key == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return NULL;   /* GCOVR_EXCL_LINE */
        }
        static const unsigned char xmlns_colon[6] = {'x', 'm', 'l', 'n', 's', ':'};
        memcpy(key, xmlns_colon, sizeof(xmlns_colon));
        scope->key = key;
    }
    Py_ssize_t nlen = 6;
    for (Py_ssize_t index = 0; index < prefix_len; index++) {
        nlen += utf8_encode(prefix[index], scope->key + nlen);
    }
    /* no prefix looks up the bare xmlns that binds the default namespace */
    return attr_scope_find(scope, node, (const char *)scope->key, prefix_len == 0 ? 5 : nlen);
}

/* The declaration binding prefix at node, read from the ancestors while they carry at most ATTR_SCOPE_MIN_WALK
   attributes and from the scope past that. Inlined once for the empty prefix, so the common default-namespace lookup
   compares one fixed name. */
static inline const th_node_attr *xmlns_decl(attr_scope *scope, th_node *node, const Py_UCS4 *prefix,
                                             Py_ssize_t prefix_len) {
    Py_ssize_t prefix_bytes = utf8_length(prefix, prefix_len);
    Py_ssize_t walked = 0;
    for (th_node *walk = node; walk != NULL; walk = walk->parent) {
        if (walk->type != TH_NODE_ELEMENT) {
            continue;
        }
        walked += walk->attr_count;
        if (walked > ATTR_SCOPE_MIN_WALK) {
            return xmlns_scoped(scope, node, prefix, prefix_len);
        }
        for (Py_ssize_t index = 0; index < walk->attr_count; index++) {
            TH_WORK_STEP();
            Py_ssize_t alen = 0;
            const char *abytes = th_attr_name(scope->tree, walk->attrs[index].name_atom, &alen);
            if (prefix_len == 0 ? alen == 5 && memcmp(abytes, "xmlns", 5) == 0
                                : alen == prefix_bytes + 6 && memcmp(abytes, "xmlns:", 6) == 0 &&
                                      u_eq_utf8(prefix, prefix_len, abytes + 6, alen - 6)) {
                return &walk->attrs[index];
            }
        }
    }
    return NULL;
}

static void resolve_ns(attr_scope *scope, th_node *node, const Py_UCS4 *prefix, Py_ssize_t prefix_len,
                       const Py_UCS4 **uri, Py_ssize_t *uri_len) {
    if (prefix_len == 3 && u_eq_ascii(prefix, 3, "xml")) {
        *uri = xml_namespace_uri();
        *uri_len = sizeof("http://www.w3.org/XML/1998/namespace") - 1;
        return;
    }
    const th_node_attr *decl =
        prefix_len == 0 ? xmlns_decl(scope, node, NULL, 0) : xmlns_decl(scope, node, prefix, prefix_len);
    *uri = decl == NULL ? NULL : decl->value;
    *uri_len = decl == NULL ? 0 : decl->value_len;
}

static qname node_qname(attr_scope *scope, th_node *element, const Py_UCS4 *name, Py_ssize_t name_len) {
    const Py_UCS4 *local, *prefix;
    Py_ssize_t local_len = 0, prefix_len = 0;
    split_prefix(name, name_len, &local, &local_len, &prefix, &prefix_len);
    qname out = {local, local_len, NULL, 0};
    resolve_ns(scope, element, prefix, prefix_len, &out.uri, &out.uri_len);
    return out;
}

/* The on-the-spot resolution a below-threshold schema uses; defined after th_schema, which is opaque here. */
static qname schema_direct_qname(const struct th_schema *schema, th_node *node);

static int is_schema_el(const struct th_schema *schema, th_node *node, const char *ns, const char *local) {
    if (node->type != TH_NODE_ELEMENT) {
        return 0;
    }
    const qname *cached = schema_node_qname(schema, node);
    /* a schema below the cache threshold resolves the name on the spot; the direct walk is cheap at that size */
    qname direct;
    if (cached == NULL) {
        direct = schema_direct_qname(schema, node);
        cached = &direct;
    }
    if (!u_eq_ascii(cached->local, cached->local_len, local)) {
        return 0;
    }
    if (cached->uri == NULL) {
        return 0;
    }
    return u_eq_ascii(cached->uri, cached->uri_len, ns);
}

/* The first element child of `node` in the schema namespace `ns` with local name
   `local`, or NULL. */
static th_node *first_schema_child(const struct th_schema *schema, th_node *node, const char *ns, const char *local) {
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        TH_WORK_STEP();
        if (is_schema_el(schema, child, ns, local)) {
            return child;
        }
    }
    return NULL;
}

/* ======================= error sink ======================= */

typedef struct {
    char *data;
    Py_ssize_t len, cap;
} pathbuf;

static int path_reserve(pathbuf *path, Py_ssize_t extra) {
    size_t need = (size_t)path->len + (size_t)extra;
    if (need <= (size_t)path->cap) {
        return 0;
    }
    size_t cap, bytes;
    int ok = th_grow_cap(need, (size_t)path->cap, 64, 1, &cap, &bytes);
    if (!ok) {     /* GCOVR_EXCL_BR_LINE: a path this long cannot arise from a real document */
        return -1; /* GCOVR_EXCL_LINE */
    }
    char *grown = PyMem_Realloc(path->data, bytes);
    if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;       /* GCOVR_EXCL_LINE */
    }
    path->data = grown;
    path->cap = (Py_ssize_t)cap;
    return 0;
}

static void path_encode(pathbuf *path, const Py_UCS4 *local, Py_ssize_t local_len) {
    for (Py_ssize_t index = 0; index < local_len; index++) {
        Py_UCS4 codepoint = local[index];
        if (codepoint < 0x80) {
            path->data[path->len++] = (char)codepoint;
        } else if (codepoint < 0x800) {
            path->data[path->len++] = (char)(0xC0 | (codepoint >> 6));
            path->data[path->len++] = (char)(0x80 | (codepoint & 0x3F));
        } else if (codepoint < 0x10000) {
            path->data[path->len++] = (char)(0xE0 | (codepoint >> 12));
            path->data[path->len++] = (char)(0x80 | ((codepoint >> 6) & 0x3F));
            path->data[path->len++] = (char)(0x80 | (codepoint & 0x3F));
        } else {
            path->data[path->len++] = (char)(0xF0 | (codepoint >> 18));
            path->data[path->len++] = (char)(0x80 | ((codepoint >> 12) & 0x3F));
            path->data[path->len++] = (char)(0x80 | ((codepoint >> 6) & 0x3F));
            path->data[path->len++] = (char)(0x80 | (codepoint & 0x3F));
        }
    }
}

static int path_push_cut(pathbuf *path, const Py_UCS4 *local) {
    if (path_reserve(path, TH_VALIDATE_MAX_PATH_NAME * 4 + 4) < 0) { /* GCOVR_EXCL_BR_LINE: reserve only fails on OOM */
        return -1;                                                   /* GCOVR_EXCL_LINE */
    }
    path->data[path->len++] = '/';
    path_encode(path, local, TH_VALIDATE_MAX_PATH_NAME);
    /* U+2026 occurs in no XML name, so a cut cannot be mistaken for part of a name */
    memcpy(path->data + path->len, "\xE2\x80\xA6", 3);
    path->len += 3;
    return 0;
}

static int path_push(pathbuf *path, const Py_UCS4 *local, Py_ssize_t local_len) {
    if (local_len > TH_VALIDATE_MAX_PATH_NAME) {
        return path_push_cut(path, local);
    }
    if (path_reserve(path, local_len * 4 + 1) < 0) { /* GCOVR_EXCL_BR_LINE: reserve only fails on OOM */
        return -1;                                   /* GCOVR_EXCL_LINE */
    }
    path->data[path->len++] = '/';
    path_encode(path, local, local_len);
    return 0;
}

struct th_schema;

typedef struct {
    PyObject *errors; /* list of (message, path, line, type) tuples */
    th_tree *tree;
    struct th_schema *schema;
    pathbuf path;
    int failed;
    int collect_errors;
    Py_ssize_t error_count;
} valctx;

static void report(valctx *ctx, th_node *node, const char *type, const char *fmt, ...) {
    if (ctx->failed) { /* GCOVR_EXCL_BR_LINE: failed is set only by an unforceable allocation failure */
        return;        /* GCOVR_EXCL_LINE */
    }
    ctx->error_count++;
    if (!ctx->collect_errors || ctx->error_count > TH_VALIDATE_MAX_ERRORS) {
        return;
    }
    va_list args;
    va_start(args, fmt);
    PyObject *message = th_str_format_v(fmt, args);
    va_end(args);
    Py_ssize_t line = 0, col = 0;
    if (node != NULL) { /* GCOVR_EXCL_BR_LINE: every report call site passes a real node */
        th_node_source_position(ctx->tree, node, &line, &col);
    }
    PyObject *path = PyUnicode_FromStringAndSize(ctx->path.data, ctx->path.len);
    PyObject *tuple = NULL;
    if (message != NULL && path != NULL) { /* GCOVR_EXCL_BR_LINE: str construction failure is unforceable */
        tuple = Py_BuildValue("(NNns)", message, path, line, type);
    } else { /* GCOVR_EXCL_START: str construction failure cannot be forced from a test */
        Py_XDECREF(message);
        Py_XDECREF(path);
    } /* GCOVR_EXCL_STOP */
    int appended =
        tuple != NULL && PyList_Append(ctx->errors, tuple) == 0; /* GCOVR_EXCL_BR_LINE: append OOM unforceable */
    if (!appended) {     /* GCOVR_EXCL_BR_LINE: list append OOM is unforceable */
        ctx->failed = 1; /* GCOVR_EXCL_LINE */
    } /* GCOVR_EXCL_LINE: llvm attributes the unexecuted fall-through to this brace */
    Py_XDECREF(tuple);
}

static const Py_UCS4 EMPTY_UCS4[1] = {0};

/* Copy up to bufsize-1 bytes of a UCS4 name as UTF-8 into buf (for %s messages),
   returning buf NUL-terminated. */
static const char *name_utf8(const Py_UCS4 *name, Py_ssize_t len, char *buf, size_t bufsize) {
    size_t out = 0;
    for (Py_ssize_t index = 0; index < len && out + 4 < bufsize; index++) {
        Py_UCS4 codepoint = name[index];
        if (codepoint < 0x80) {
            buf[out++] = (char)codepoint;
        } else if (codepoint < 0x800) {
            buf[out++] = (char)(0xC0 | (codepoint >> 6));
            buf[out++] = (char)(0x80 | (codepoint & 0x3F));
        } else if (codepoint < 0x10000) {
            buf[out++] = (char)(0xE0 | (codepoint >> 12));
            buf[out++] = (char)(0x80 | ((codepoint >> 6) & 0x3F));
            buf[out++] = (char)(0x80 | (codepoint & 0x3F));
        } else {
            buf[out++] = (char)(0xF0 | (codepoint >> 18));
            buf[out++] = (char)(0x80 | ((codepoint >> 12) & 0x3F));
            buf[out++] = (char)(0x80 | ((codepoint >> 6) & 0x3F));
            buf[out++] = (char)(0x80 | (codepoint & 0x3F));
        }
    }
    buf[out] = 0;
    return buf;
}

/* The first character-data child's text of a schema element (a name/value/param
   literal), or the empty run. Used at compile time, without a validation context; a
   text span that cannot be realized reads as the empty run with MemoryError set. */
static const Py_UCS4 *element_text_raw(th_tree *tree, th_node *node, Py_ssize_t *out_len) {
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        if (is_chardata(child)) {
            const Py_UCS4 *text = child->text_len == 0 ? EMPTY_UCS4 : th_node_realize_text(tree, child);
            if (text == NULL) {    /* GCOVR_EXCL_BR_LINE: lazy text OOM */
                PyErr_NoMemory();  /* GCOVR_EXCL_LINE: the compile stops on the pending exception */
                *out_len = 0;      /* GCOVR_EXCL_LINE: allocation failure */
                return EMPTY_UCS4; /* GCOVR_EXCL_LINE: allocation failure */
            }
            *out_len = child->text_len;
            return text;
        }
    } /* GCOVR_EXCL_LINE: llvm miscredits this loop-exit brace when the element has no text child */
    *out_len = 0;
    return EMPTY_UCS4;
}

/* Decode a UTF-8 attribute name into `out`, returning the code-point count. A UTF-8 byte
   sequence yields at most one code point per byte, so `out` sized to `len` always fits.
   Instance names are well-formed UTF-8, so a truncated tail is not handled. */
static Py_ssize_t utf8_to_ucs4(const char *bytes, Py_ssize_t len, Py_UCS4 *out) {
    Py_ssize_t in = 0, produced = 0;
    while (in < len) {
        unsigned char lead = (unsigned char)bytes[in];
        Py_UCS4 codepoint;
        int extra;
        if (lead < 0x80) {
            codepoint = lead;
            extra = 0;
        } else if ((lead >> 5) == 0x6) {
            codepoint = lead & 0x1Fu;
            extra = 1;
        } else if ((lead >> 4) == 0xE) {
            codepoint = lead & 0x0Fu;
            extra = 2;
        } else {
            codepoint = lead & 0x07u;
            extra = 3;
        }
        /* th_attr_name returns complete, well-formed UTF-8, so every continuation byte
           of a multi-byte lead is present within the buffer. */
        for (int index = 1; index <= extra; index++) {
            codepoint = (codepoint << 6) | ((unsigned char)bytes[in + index] & 0x3Fu);
        }
        out[produced++] = codepoint;
        in += extra + 1;
    }
    return produced;
}

/* ============================================================
   The schema struct and the datatype/facet, XSD, and RELAX NG
   engines are defined below in dependency order.
   ============================================================ */

typedef struct {
    const Py_UCS4 *name;
    Py_ssize_t len;
    th_node *node;
} named_node;

typedef struct {
    named_node *items;
    named_node **slots;
    Py_ssize_t len, cap;
    size_t slot_cap;
} named_vec;

typedef struct pattern pattern;
typedef struct nsnode nsnode;

/* compile-time 7.4 check: a persistent treap of name classes and how many it holds, see rng_names_merge */
typedef struct nstree {
    nsnode *root;
} nstree;

/* compile-time 7.4 check: what a branch matches, as exact names and as wildcard or choice classes */
typedef struct nameset {
    nstree names, wild;
    int text;
} nameset;

/* compile-time 7.4 check: a pair of sets whose conflict test found no overlap, keyed by their roots */
typedef struct nspair {
    nsnode *roots[4]; /* small exact, small wildcard, large exact, large wildcard */
} nspair;

typedef struct def_part {
    th_node *node;
    struct def_part *next;
} def_part;

typedef struct def_entry {
    const Py_UCS4 *name;
    Py_ssize_t len;
    th_node *first; /* first <define> element for this name */
    def_part *extra, *last;
    pattern *built;  /* memoized pattern, NULL until first resolved */
    int scan_height; /* compile-time scan: -1 unvisited, -2 expanding, else the levels nested above its elements */
    int scan_attrs;  /* compile-time scan: an attribute pattern sits in the define, refs followed, elements not */
    nameset names;   /* compile-time 7.4 check: what a ref to this define matches, final once names_state is 2 */
    int names_state; /* compile-time 7.4 check: 0 unvisited, 1 on the DFS stack, 2 done */
} def_entry;

/* compile-time 7.3 check: the attribute patterns a pattern holds. Up to RNG_ATTRS_FEW stay as their nodes and are
   compared pair by pair; past that they move into `set`, which merges by the 7.4 name-set rules. */
#define RNG_ATTRS_FEW 4
typedef struct rng_attrs {
    nameset set;
    th_node *few[RNG_ATTRS_FEW];
    int count;
} rng_attrs;

/* compile-time 7.3 check, one per define in a side table: the attributes a ref to the define holds, whether one is an
   anyName or nsName attribute outside oneOrMore or zeroOrMore, and whether the record is built. Kept out of def_entry
   so a grammar that never refs a define from an element pays nothing for it. */
typedef struct def_attrs {
    rng_attrs attrs;
    int bad;
    int built;
} def_attrs;

/* An attribute pattern's name class, built once for the 7.3 check and taken over by rng_build. `set` holds the class
   as a name set once the check needs one; it lives in names_mem, so only the compile reads it. */
typedef struct rng_attr_class {
    th_node *node;
    struct nameclass *nc;
    nameset set;
    int has_set;
} rng_attr_class;

typedef struct {
    def_entry *items;
    size_t *slots;
    Py_ssize_t len, cap;
    size_t slot_cap;
} def_vec;

/* The xmlns declarations of the elements open on one validation walk, keyed by the prefix as the attribute spells it
   (empty for the default namespace). Each element binds its own on entry and unbinds them on exit, so a name resolves
   with one probe instead of a walk over every ancestor's attributes. */
typedef struct {
    const char *prefix;
    Py_ssize_t prefix_len;
    const Py_UCS4 *uri;
    Py_ssize_t uri_len;
    int bound;
} nsbinding;

typedef struct {
    Py_ssize_t binding;
    const Py_UCS4 *uri;
    Py_ssize_t uri_len;
    int bound;
} nsundo;

typedef struct {
    nsbinding *bindings;
    Py_ssize_t count, cap;
    Py_ssize_t *slots; /* binding index + 1, 0 for an empty slot; twice the binding capacity */
    size_t slot_cap;
    nsundo *undo;
    Py_ssize_t undo_count, undo_cap;
} nsscope;

typedef struct th_schema {
    int kind; /* 0 = XSD, 1 = RELAX NG */
    th_tree *tree;
    PyObject *source;
    arena mem;
    th_node *root;
    /* XSD */
    const Py_UCS4 *target_ns;
    Py_ssize_t target_ns_len;
    int element_qualified, attribute_qualified;
    named_vec elements, complex_types, simple_types, attributes, groups, attr_groups;
    /* RELAX NG */
    pattern *start;
    pattern *p_empty, *p_notallowed, *p_text;
    int rng_references; /* the annotation pruning walk met an include or externalRef with href */
    int rng_attributes; /* the annotation pruning walk met an attribute pattern, so the 7.3 check has work */
    def_vec defines;
    /* compile-time 7.3 check: one record per define, NULL until a scope refs a define */
    def_attrs *def_attrs_table;
    /* the 7.3 check's attribute name classes by node, open addressed in `mem` so a later rng_build reuses them */
    rng_attr_class **rng_attr_classes;
    size_t rng_attr_classes_cap, rng_attr_classes_len;
    arena names_mem;       /* compile-time 7.4 check: the persistent name sets, freed when the compile ends */
    Py_ssize_t rng_work;   /* compile-time 7.4 check: units spent against RNG_INTERLEAVE_WORK_LIMIT */
    Py_ssize_t rng_nodes;  /* compile-time 7.4 check: treap nodes allocated against RNG_INTERLEAVE_NODE_LIMIT */
    th_node **rng_checked; /* compile-time 7.4 check: open-addressed set of interleaves already checked */
    nsnode **rng_table;    /* compile-time 7.4 check: hash-consed treap nodes, open addressed */
    size_t rng_table_cap, rng_table_len;
    size_t rng_checked_cap, rng_checked_len;
    nspair rng_disjoint[8]; /* compile-time 7.4 check: the last disjoint pairs, round robin */
    size_t rng_disjoint_len, rng_disjoint_next;
    struct patintern *intern;    /* per-validation pattern hash-consing table; NULL while compiling */
    struct rng_routing *routing; /* per-validation operand index of the container joins; NULL until events grow large */
    int route_count;             /* container joins built, compiled and then lazily built ones */
    struct patmemo *memo;        /* the memo in use: event_memo, or token_memo inside a <list>; NULL while compiling */
    struct patmemo *event_memo;  /* per-validation derivative memo of the event in progress */
    struct patmemo *token_memo;  /* the memo the tokens of a <list> take turns in, one event each */
    size_t pattern_count;        /* patterns the compiled schema holds, which sizes the free steps */
    size_t shared_patterns;      /* pattern_count when the validation started: a pattern numbered below it belongs to
                                    the compiled schema, which other validations and threads read at the same time */
    Py_ssize_t memo_free;        /* free steps an event starts with: MEMO_FREE_STEPS, or more for a larger schema */
    Py_ssize_t memo_budget; /* counts the current event's free steps down; the step that takes it below zero engages the
                               memo */
    Py_ssize_t choice_limit; /* leaves a choice built during validation may hold: 0 until a wide merge computes it, -1
                                once a merge ran past it */
    Py_ssize_t event_work;   /* merge steps and memoized derivatives of the event in progress, against the limit
                                rng_event_spend() takes from the choice limit */
    /* the schema tree's attribute scope; a compile and every validate() call bring their own, so a schema shared
       across threads stays read-only */
    attr_scope *tree_ns;
    /* every schema element node's resolved qname, sorted by node pointer for is_schema_el */
    sqname_entry *sqnames;
    Py_ssize_t sqname_count;
    /* every pattern facet and RELAX NG pattern param in document order, and the distinct ones sorted by text for
       regex_full_match to search */
    struct rpattern *regex_patterns, **regex_sorted;
    size_t regex_count, regex_cap, regex_distinct;
    size_t regex_states;            /* states in all compiled patterns, drawn from the one RX_MAX_STATES budget */
    size_t regex_max_states;        /* states in the largest compiled pattern, sizing the match buffers */
    struct rscratch *regex_scratch; /* match buffers of one validate() call, built on its first pattern check */
    /* the buffer one validate() call normalizes whitespace into, and the characters it holds; see dt_normalize_ws */
    Py_UCS4 *ws_buffer;
    Py_ssize_t ws_cap;
    /* the buffer one validate() call copies a long number into to convert it; see dt_value_double */
    char *number_buffer;
    Py_ssize_t number_cap;
    /* XSD position sets of one validate() call that no matcher holds, and the length they were made for */
    struct posset_spare *posset_spares;
    Py_ssize_t posset_size;
    /* XSD name lookup list of one validate() call, refilled for each element, and the index over it that an element
       with many attributes builds */
    struct edecl *edecl_items;
    Py_ssize_t edecl_cap;
    named_vec attr_names;
    struct xfacet_entry *facet_entries;
    size_t facet_count, facet_cap;
    /* every xs:all group's members indexed by name, sorted by the group's node pointer */
    struct xall_entry *all_entries;
    size_t all_count, all_cap;
    /* the children XSD validation asks a schema element for, one entry per parent that has any */
    struct xchild_entry *child_entries;
    size_t child_count, child_cap;
    /* per sqnames entry of an XSD schema, at the same index: what xsd_cache_facets classified the element as */
    struct xnode *xnodes;
    /* the xmlns declarations in scope on the instance walk of one validate() call */
    nsscope ns;
    int complex_extension; /* a complexContent extension exists, so the compile-time xs:all extension check has work to
                              do */
    int all_inherited; /* a complex type extends an xs:all content model without adding particles, so validation looks
                          up an inherited model for the types that declare none */
    int named_all;     /* a content model reaches an xs:all through a group ref, so validation resolves group refs */
    uintptr_t stack_floor; /* the lowest frame address a validation level may run at; 0 when the thread has no bound */
    int stack_exhausted;   /* a recursive walk stopped at stack_floor, so the validation raises RecursionError */
} th_schema;

/* A walk that descends by recursion asks this before each level. Once the thread's C stack is nearly spent it
   returns its failure value and sets stack_exhausted, and the validation raises RecursionError as it unwinds. The
   helpers a walk calls over the tree it has just built take no check: each level costs them less stack than it cost
   the walk. */
static inline int schema_stack_low(th_schema *schema) {
    if (TH_FRAME_ADDRESS() < schema->stack_floor) {
        schema->stack_exhausted = 1;
        return 1;
    }
    return 0;
}

/* Look up a schema element node's precomputed qname. schema_build_qname_cache enters every
   element node of the schema tree before any classification runs, and is_schema_el only ever
   asks about schema element nodes, so the search always hits. */
static qname schema_direct_qname(const struct th_schema *schema, th_node *node) {
    return node_qname(schema->tree_ns, node, node->text, node->text_len);
}

static const sqname_entry *schema_node_entry(const th_schema *schema, th_node *node) {
    if (schema->sqname_count == 0) { /* a schema below the cache threshold resolves directly; see the build gate */
        return NULL;
    }
    Py_ssize_t lo = 0, hi = schema->sqname_count - 1;
    while (lo <= hi) { /* GCOVR_EXCL_BR_LINE: the searched-for node is always present, so the loop exits by return */
        Py_ssize_t mid = lo + ((hi - lo) >> 1);
        th_node *at = schema->sqnames[mid].node;
        if (at == node) {
            return &schema->sqnames[mid];
        }
        if (at < node) {
            lo = mid + 1;
        } else {
            hi = mid - 1;
        }
    }
    return NULL; /* GCOVR_EXCL_LINE: every schema element node is cached, so the search never misses */
}

static const qname *schema_node_qname(const th_schema *schema, th_node *node) {
    const sqname_entry *entry = schema_node_entry(schema, node);
    return entry == NULL ? NULL : &entry->name;
}

/* The node after `node` in document order, past its descendants, without leaving `root`'s subtree; NULL at its end.
   Schema tree walks step with this and schema_walk_next instead of recursing once per level, so a schema nested up to
   TH_VALIDATE_MAX_DEPTH costs them no C stack. */
static th_node *schema_walk_skip(const th_node *root, th_node *node) {
    while (node != root && node->next_sibling == NULL) {
        node = node->parent;
    }
    return node == root ? NULL : node->next_sibling;
}

/* The node after `node` in document order within `root`'s subtree, or NULL at its end. */
static th_node *schema_walk_next(const th_node *root, th_node *node) {
    return node->first_child != NULL ? node->first_child : schema_walk_skip(root, node);
}

/* Compile walks that carry state per level keep their levels in an explicit stack of `frame_size` slots, which starts
   as `local`, an array in the caller's frame, and moves to the heap past it. Unlike walk_stack_reserve this stack is
   freed when the walk ends (walk_release), since the schema arena lives as long as the compiled schema. Returns the
   stack with room for slot `depth`, or NULL with MemoryError set, in which case the caller still releases `frames`. */
static void *walk_reserve(void *frames, const void *local, Py_ssize_t depth, Py_ssize_t *cap, size_t frame_size) {
    if (depth < *cap) {
        return frames;
    }
    void *grown = PyMem_Malloc((size_t)*cap * 2 * frame_size);
    if (grown == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
        return NULL;      /* GCOVR_EXCL_LINE */
    }
    memcpy(grown, frames, (size_t)depth * frame_size);
    if (frames != local) {
        PyMem_Free(frames);
    }
    *cap *= 2;
    return grown;
}

static void walk_release(void *frames, const void *local) {
    if (frames != local) {
        PyMem_Free(frames);
    }
}

static Py_ssize_t schema_count_elements(th_node *document) {
    Py_ssize_t total = 0;
    for (th_node *node = schema_walk_next(document, document); node != NULL; node = schema_walk_next(document, node)) {
        total += node->type == TH_NODE_ELEMENT;
    }
    return total;
}

static Py_ssize_t ns_scope_enter(th_schema *schema, th_tree *tree, th_node *element);
static void ns_scope_leave(th_schema *schema, Py_ssize_t mark);
static qname node_qname_scoped(th_schema *schema, const Py_UCS4 *name, Py_ssize_t name_len, int is_attr);

/* The QName an attribute holds, its prefix resolved in scope at `element`, or an empty qname when the attribute is
   absent. */
static qname attr_qname_scoped(th_schema *schema, th_node *element, const char *name, Py_ssize_t name_len) {
    const th_node_attr *attr = attr_exact(schema->tree, element, name, name_len);
    if (attr == NULL) {
        return (qname){NULL, 0, NULL, 0};
    }
    return node_qname_scoped(schema, attr->value, attr->value_len, 0);
}

/* The element's declarations are entered before this runs, so they apply to its own name and attribute values. */
static void schema_fill_qname(th_schema *schema, th_node *element, Py_ssize_t at) {
    schema->sqnames[at].node = element;
    schema->sqnames[at].name = node_qname_scoped(schema, element->text, element->text_len, 0);
    schema->sqnames[at].type = attr_qname_scoped(schema, element, "type", 4);
    schema->sqnames[at].base = attr_qname_scoped(schema, element, "base", 4);
}

static int sqname_cmp(const void *left, const void *right) {
    th_node *left_node = ((const sqname_entry *)left)->node;
    th_node *right_node = ((const sqname_entry *)right)->node;
    return (left_node > right_node) - (left_node < right_node);
}

static int schema_build_qname_cache(th_schema *schema) {
    th_node *document = th_tree_document(schema->tree);
    Py_ssize_t count = schema_count_elements(document);
    schema->sqnames = arena_alloc(&schema->mem, (size_t)count * sizeof(sqname_entry));
    if (schema->sqnames == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return -1;                 /* GCOVR_EXCL_LINE */
    }
    /* the scope mark each open element entered at, so leaving the element unbinds the declarations it made */
    Py_ssize_t local[16];
    Py_ssize_t *marks = local;
    Py_ssize_t cap = 16, depth = 0, at = 0;
    /* a parsed schema always has a root element, so the document has a first child */
    th_node *node = document->first_child;
    for (;;) {
        if (node->type == TH_NODE_ELEMENT) {
            Py_ssize_t *grown = walk_reserve(marks, local, depth, &cap, sizeof(Py_ssize_t));
            if (grown == NULL) {            /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                walk_release(marks, local); /* GCOVR_EXCL_LINE */
                return -1;                  /* GCOVR_EXCL_LINE */
            }
            marks = grown;
            marks[depth++] = ns_scope_enter(schema, schema->tree, node);
            schema_fill_qname(schema, node, at++);
            if (node->first_child != NULL) {
                node = node->first_child;
                continue;
            }
        }
        for (;;) {
            if (node->type == TH_NODE_ELEMENT) {
                ns_scope_leave(schema, marks[--depth]);
            }
            if (node->next_sibling != NULL) {
                node = node->next_sibling;
                break;
            }
            node = node->parent;
            if (node == document) {
                walk_release(marks, local);
                schema->sqname_count = count;
                qsort(schema->sqnames, (size_t)count, sizeof(sqname_entry), sqname_cmp);
                return 0;
            }
        }
    }
}

static int named_push(th_schema *schema, named_vec *vec, const Py_UCS4 *name, Py_ssize_t len, th_node *node) {
    if (vec->len == vec->cap) {
        Py_ssize_t cap = vec->cap ? vec->cap * 2 : TH_INITIAL_CAPACITY(8);
        named_node *items = arena_alloc(&schema->mem, (size_t)cap * sizeof(named_node));
        if (items == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return -1;       /* GCOVR_EXCL_LINE */
        }
        if (vec->len > 0) {
            memcpy(items, vec->items, (size_t)vec->len * sizeof(named_node));
        }
        vec->items = items;
        vec->cap = cap;
    }
    vec->items[vec->len].name = name;
    vec->items[vec->len].len = len;
    vec->items[vec->len].node = node;
    vec->len++;
    return 0;
}

TH_NOINLINE static uint64_t named_hash_wide(const Py_UCS4 *name, Py_ssize_t len) {
    uint64_t hash = UINT64_C(1469598103934665603);
    for (Py_ssize_t index = 0; index < len; index++) {
        unsigned char encoded[4];
        int count = utf8_encode(name[index], encoded);
        for (int byte = 0; byte < count; byte++) {
            hash ^= encoded[byte];
            hash *= UINT64_C(1099511628211);
        }
    }
    return hash;
}

/* FNV-1a over the UTF-8 encoding of the name, the hash ns_hash_bytes and the attribute indexes take over interned
   attribute-name bytes, so a table keyed one way is probed the other way without decoding. An ASCII name has one byte
   per code point, so only a name holding a wider code point is hashed a second time. */
static inline uint64_t named_hash(const Py_UCS4 *name, Py_ssize_t len) {
    uint64_t hash = UINT64_C(1469598103934665603);
    Py_UCS4 seen = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        seen |= name[index];
        hash ^= name[index];
        hash *= UINT64_C(1099511628211);
    }
    return seen < 0x80 ? hash : named_hash_wide(name, len);
}

static int named_index(th_schema *schema, named_vec *vec) {
    if (vec->len < 8) {
        return 0;
    }
    size_t cap = vec->slot_cap > 8 ? vec->slot_cap : 8; /* a vector indexed again keeps the table it has */
    while (cap < (size_t)vec->len * 2) {
        cap *= 2;
    }
    named_node **slots = cap == vec->slot_cap ? vec->slots : arena_alloc(&schema->mem, cap * sizeof(named_node *));
    if (slots == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return -1;       /* GCOVR_EXCL_LINE */
    }
    memset(slots, 0, cap * sizeof(named_node *));
    for (Py_ssize_t index = 0; index < vec->len; index++) {
        named_node *item = &vec->items[index];
        size_t slot = (size_t)named_hash(item->name, item->len) & (cap - 1);
        while (slots[slot] != NULL) {
            slot = (slot + 1) & (cap - 1);
        }
        slots[slot] = item;
    }
    vec->slots = slots;
    vec->slot_cap = cap;
    return 0;
}

static Py_ssize_t named_find_index(const named_vec *vec, const Py_UCS4 *name, Py_ssize_t len);

static th_node *named_find(const named_vec *vec, const Py_UCS4 *name, Py_ssize_t len) {
    Py_ssize_t index = named_find_index(vec, name, len);
    return index < 0 ? NULL : vec->items[index].node;
}

/* The XSD cycle checks key per-definition state by index, so they need the slot, not only its node. */
static Py_ssize_t named_find_index(const named_vec *vec, const Py_UCS4 *name, Py_ssize_t len) {
    if (vec->slots == NULL) {
        for (Py_ssize_t index = 0; index < vec->len; index++) {
            if (u_eq_u(vec->items[index].name, vec->items[index].len, name, len)) {
                return index;
            }
        }
        return -1;
    }
    size_t slot = (size_t)named_hash(name, len) & (vec->slot_cap - 1);
    while (vec->slots[slot] != NULL) {
        named_node *item = vec->slots[slot];
        if (u_eq_u(item->name, item->len, name, len)) {
            return item - vec->items;
        }
        slot = (slot + 1) & (vec->slot_cap - 1);
    }
    return -1;
}

static uint64_t ns_hash_bytes(const char *bytes, Py_ssize_t len) {
    uint64_t hash = UINT64_C(1469598103934665603);
    for (Py_ssize_t index = 0; index < len; index++) {
        hash ^= (unsigned char)bytes[index];
        hash *= UINT64_C(1099511628211);
    }
    return hash;
}

static int ns_grow(th_schema *schema) {
    nsscope *scope = &schema->ns;
    Py_ssize_t cap = scope->cap ? scope->cap * 2 : 8;
    size_t slot_cap = (size_t)cap * 2;
    nsbinding *items = arena_alloc(&schema->mem, (size_t)cap * sizeof(nsbinding));
    Py_ssize_t *slots = arena_alloc(&schema->mem, slot_cap * sizeof(Py_ssize_t));
    if (items == NULL || slots == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return -1;                        /* GCOVR_EXCL_LINE */
    }
    if (scope->count > 0) {
        memcpy(items, scope->bindings, (size_t)scope->count * sizeof(nsbinding));
    }
    memset(slots, 0, slot_cap * sizeof(Py_ssize_t));
    for (Py_ssize_t index = 0; index < scope->count; index++) {
        size_t slot = (size_t)ns_hash_bytes(items[index].prefix, items[index].prefix_len) & (slot_cap - 1);
        while (slots[slot] != 0) {
            slot = (slot + 1) & (slot_cap - 1);
        }
        slots[slot] = index + 1;
    }
    scope->bindings = items;
    scope->cap = cap;
    scope->slots = slots;
    scope->slot_cap = slot_cap;
    return 0;
}

static Py_ssize_t ns_find_bytes(const nsscope *scope, const char *prefix, Py_ssize_t len) {
    if (scope->slots == NULL) {
        return -1;
    }
    size_t mask = scope->slot_cap - 1;
    for (size_t slot = (size_t)ns_hash_bytes(prefix, len) & mask; scope->slots[slot] != 0; slot = (slot + 1) & mask) {
        TH_WORK_STEP();
        const nsbinding *binding = &scope->bindings[scope->slots[slot] - 1];
        if (binding->prefix_len == len && (len == 0 || memcmp(binding->prefix, prefix, (size_t)len) == 0)) {
            return scope->slots[slot] - 1;
        }
    }
    return -1;
}

static Py_ssize_t ns_find_units(const nsscope *scope, const Py_UCS4 *prefix, Py_ssize_t len) {
    if (scope->slots == NULL) {
        return -1;
    }
    size_t mask = scope->slot_cap - 1;
    for (size_t slot = (size_t)named_hash(prefix, len) & mask; scope->slots[slot] != 0; slot = (slot + 1) & mask) {
        TH_WORK_STEP();
        const nsbinding *binding = &scope->bindings[scope->slots[slot] - 1];
        if (u_eq_utf8(prefix, len, binding->prefix, binding->prefix_len)) {
            return scope->slots[slot] - 1;
        }
    }
    return -1;
}

static Py_ssize_t ns_add(th_schema *schema, const char *prefix, Py_ssize_t len) {
    nsscope *scope = &schema->ns;
    if (scope->count == scope->cap && ns_grow(schema) < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return -1;                                           /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t index = scope->count++;
    scope->bindings[index] = (nsbinding){prefix, len, NULL, 0, 0};
    size_t mask = scope->slot_cap - 1;
    size_t slot = (size_t)ns_hash_bytes(prefix, len) & mask;
    while (scope->slots[slot] != 0) {
        slot = (slot + 1) & mask;
    }
    scope->slots[slot] = index + 1;
    return index;
}

static void ns_undo_push(th_schema *schema, Py_ssize_t index) {
    nsscope *scope = &schema->ns;
    if (scope->undo_count == scope->undo_cap) {
        Py_ssize_t cap = scope->undo_cap ? scope->undo_cap * 2 : 16;
        nsundo *items = arena_alloc(&schema->mem, (size_t)cap * sizeof(nsundo));
        if (items == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return;          /* GCOVR_EXCL_LINE */
        }
        if (scope->undo_count > 0) {
            memcpy(items, scope->undo, (size_t)scope->undo_count * sizeof(nsundo));
        }
        scope->undo = items;
        scope->undo_cap = cap;
    }
    const nsbinding *binding = &scope->bindings[index];
    scope->undo[scope->undo_count++] = (nsundo){index, binding->uri, binding->uri_len, binding->bound};
}

/* Binds the declarations of `element` for the walk below it and returns the mark to unbind them to. The declarations
   are applied last-to-first, so the first one in document order is the one that stays bound. */
static Py_ssize_t ns_scope_enter(th_schema *schema, th_tree *tree, th_node *element) {
    nsscope *scope = &schema->ns;
    Py_ssize_t mark = scope->undo_count;
    for (Py_ssize_t position = element->attr_count - 1; position >= 0; position--) {
        TH_WORK_STEP();
        Py_ssize_t alen = 0;
        const char *abytes = th_attr_name(tree, element->attrs[position].name_atom, &alen);
        if (!is_xmlns_decl(abytes, alen) || alen == 6) {
            continue;
        }
        const char *prefix = alen == 5 ? NULL : abytes + 6;
        Py_ssize_t prefix_len = alen == 5 ? 0 : alen - 6;
        Py_ssize_t index = ns_find_bytes(scope, prefix, prefix_len);
        if (index < 0) {
            index = ns_add(schema, prefix, prefix_len);
            if (index < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
                continue;    /* GCOVR_EXCL_LINE */
            }
        }
        ns_undo_push(schema, index);
        nsbinding *binding = &scope->bindings[index];
        binding->uri = element->attrs[position].value;
        binding->uri_len = element->attrs[position].value_len;
        binding->bound = 1;
    }
    return mark;
}

/* A walk that starts below the document root still resolves names against the declarations above its root. */
static void ns_scope_enter_ancestors(th_schema *schema, th_tree *tree, th_node *root) {
    Py_ssize_t depth = 0;
    for (th_node *walk = root->parent; walk != NULL; walk = walk->parent) {
        depth += walk->type == TH_NODE_ELEMENT;
    }
    th_node **chain = arena_alloc(&schema->mem, (size_t)depth * sizeof(th_node *));
    if (chain == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return;          /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t at = depth;
    for (th_node *walk = root->parent; walk != NULL; walk = walk->parent) {
        if (walk->type == TH_NODE_ELEMENT) {
            chain[--at] = walk;
        }
    }
    for (Py_ssize_t index = 0; index < depth; index++) {
        ns_scope_enter(schema, tree, chain[index]);
    }
}

static void ns_scope_leave(th_schema *schema, Py_ssize_t mark) {
    nsscope *scope = &schema->ns;
    while (scope->undo_count > mark) {
        const nsundo *undo = &scope->undo[--scope->undo_count];
        nsbinding *binding = &scope->bindings[undo->binding];
        binding->uri = undo->uri;
        binding->uri_len = undo->uri_len;
        binding->bound = undo->bound;
    }
}

/* Resolves a name against the declarations in scope, so the caller has entered the element that owns the name. */
static qname node_qname_scoped(th_schema *schema, const Py_UCS4 *name, Py_ssize_t name_len, int is_attr) {
    const Py_UCS4 *local, *prefix;
    Py_ssize_t local_len = 0, prefix_len = 0;
    split_prefix(name, name_len, &local, &local_len, &prefix, &prefix_len);
    qname out = {local, local_len, NULL, 0};
    if (prefix_len == 0 && is_attr) {
        return out;
    }
    if (prefix_len == 3 && u_eq_ascii(prefix, 3, "xml")) {
        out.uri = xml_namespace_uri();
        out.uri_len = sizeof("http://www.w3.org/XML/1998/namespace") - 1;
        return out;
    }
    Py_ssize_t index = ns_find_units(&schema->ns, prefix, prefix_len);
    if (index >= 0 && schema->ns.bindings[index].bound) {
        out.uri = schema->ns.bindings[index].uri;
        out.uri_len = schema->ns.bindings[index].uri_len;
    }
    return out;
}

/* Resolves a child's name before the child is entered, so the child's own declarations apply to its name as well. */
static qname element_qname_scoped(th_schema *schema, th_tree *tree, th_node *element) {
    Py_ssize_t mark = ns_scope_enter(schema, tree, element);
    qname name = node_qname_scoped(schema, element->text, element->text_len, 0);
    ns_scope_leave(schema, mark);
    return name;
}

/* Concatenate an element's direct character-data children into an arena buffer. */
static const Py_UCS4 *element_text(valctx *ctx, th_node *element, Py_ssize_t *out_len) {
    Py_ssize_t total = 0;
    for (th_node *child = element->first_child; child != NULL; child = child->next_sibling) {
        if (is_chardata(child)) {
            total += child->text_len;
        }
    }
    Py_UCS4 *buffer = arena_alloc(&ctx->schema->mem, (size_t)(total + 1) * sizeof(Py_UCS4));
    if (buffer == NULL) {  /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        *out_len = 0;      /* GCOVR_EXCL_LINE */
        return EMPTY_UCS4; /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t offset = 0;
    for (th_node *child = element->first_child; child != NULL; child = child->next_sibling) {
        if (is_chardata(child) && child->text_len > 0) {
            const Py_UCS4 *data = th_node_realize_text(ctx->tree, child);
            if (data == NULL) {    /* GCOVR_EXCL_BR_LINE: text realization allocation failure */
                ctx->failed = 1;   /* GCOVR_EXCL_LINE */
                PyErr_NoMemory();  /* GCOVR_EXCL_LINE */
                *out_len = 0;      /* GCOVR_EXCL_LINE */
                return EMPTY_UCS4; /* GCOVR_EXCL_LINE */
            }
            memcpy(buffer + offset, data, (size_t)child->text_len * sizeof(Py_UCS4));
            offset += child->text_len;
        }
    }
    buffer[offset] = 0;
    *out_len = offset;
    return buffer;
}

/* Both validators keep the instance elements they are inside on an explicit stack in the validation arena instead of
   recursing once per nesting level, so the document's depth costs no C stack. Returns `frames` with room for one more
   past `depth`, or NULL when the arena is out of memory. */
static void *walk_stack_reserve(arena *mem, void *frames, Py_ssize_t depth, Py_ssize_t *cap, size_t frame_size) {
    if (depth < *cap) {
        return frames;
    }
    size_t capacity, bytes;
    const int fits = th_grow_cap((size_t)depth + 1, (size_t)*cap, 16, frame_size, &capacity, &bytes);
    if (!fits) {            /* GCOVR_EXCL_BR_LINE: the depth preflight keeps the stack far below an overflowing size */
        mem->no_memory = 1; /* GCOVR_EXCL_LINE: the validate entry raises the MemoryError */
        return NULL;        /* GCOVR_EXCL_LINE */
    }
    void *grown = arena_alloc(mem, bytes);
    if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return NULL;     /* GCOVR_EXCL_LINE */
    }
    if (depth > 0) {
        memcpy(grown, frames, (size_t)depth * frame_size);
    }
    *cap = (Py_ssize_t)capacity;
    return grown;
}

static th_tree *parse_schema_source(PyObject *source);
static th_node *document_root(th_tree *tree);

#include "validate/datatypes.h"
#include "validate/xsd.h"
#include "validate/relaxng.h"
#include "validate/relaxng_resources.h"

/* ======================= compiled-schema capsule ======================= */

static const char CAPSULE_NAME[] = "turbohtml._html.schema";

static void schema_free(th_schema *schema) {
    arena_free(&schema->mem);
    if (schema->tree != NULL) { /* GCOVR_EXCL_BR_LINE: the tree is set before any compile that could fail */
        th_tree_free(schema->tree);
    }
    Py_XDECREF(schema->source);
    PyMem_Free(schema);
}

static void capsule_destructor(PyObject *capsule) {
    th_schema *schema = PyCapsule_GetPointer(capsule, CAPSULE_NAME);
    if (schema != NULL) { /* GCOVR_EXCL_BR_LINE: the capsule always carries the compiled schema */
        schema_free(schema);
    }
}

static th_tree *parse_schema_source(PyObject *source) {
    th_tree *tree = th_tree_parse_xml(PyUnicode_KIND(source), PyUnicode_DATA(source), PyUnicode_GET_LENGTH(source));
    if (tree == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
        return NULL;      /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t error_count = 0;
    const th_parse_error *errors = th_tree_errors(tree, &error_count);
    if (error_count > 0) {
        PyErr_Format(PyExc_ValueError, "malformed schema: %s at line %zd", errors[0].code, errors[0].line);
        th_tree_free(tree);
        return NULL;
    }
    return tree;
}

static th_node *document_root(th_tree *tree) {
    th_node *node = th_tree_document(tree)->first_child;
    for (; node != NULL; node = node->next_sibling) { /* GCOVR_EXCL_BR_LINE: a parsed document always has a root */
        if (node->type == TH_NODE_ELEMENT) {
            return node;
        }
    }
    return NULL; /* GCOVR_EXCL_LINE: a parsed XML document always has a root element */
}

PyObject *turbohtml_schema_compile(PyObject *module, PyObject *args) {
    (void)module;
    int kind;
    PyObject *source;
    PyObject *base_url = Py_None;
    PyObject *include_root = Py_None;
    /* GCOVR_EXCL_BR_START: the shim always passes (int, str) and RelaxNG adds the two resource options */
    if (!PyArg_ParseTuple(args, "iU|OO", &kind, &source, &base_url, &include_root)) {
        return NULL; /* GCOVR_EXCL_LINE */
    }
    /* GCOVR_EXCL_BR_STOP */
    th_tree *tree = parse_schema_source(source);
    if (tree == NULL) {
        return NULL;
    }
    if (th_node_check_max_depth(th_tree_document(tree), TH_VALIDATE_MAX_DEPTH, "schema compilation") < 0) {
        th_tree_free(tree);
        return NULL;
    }
    th_schema *schema = PyMem_Calloc(1, sizeof(th_schema));
    if (schema == NULL) {        /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(tree);      /* GCOVR_EXCL_LINE */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
    }
    schema->kind = kind;
    schema->tree = tree;
    schema->source = Py_NewRef(source);
    schema->root = document_root(tree);
    attr_scope tree_ns = {.tree = tree, .mem = &schema->mem};
    schema->tree_ns = &tree_ns;
    /* The cache repays its build through the XSD content-model matcher, which re-classifies the same schema
       elements on every particle step; RELAX NG's derivative walk never revisits at that rate, and the gated
       compile benchmark showed the eager build as a pure 8% loss there, so RNG resolves names directly. */
    if (kind == 0 && schema_build_qname_cache(schema) < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        schema_free(schema);                                 /* GCOVR_EXCL_LINE */
        return PyErr_NoMemory();                             /* GCOVR_EXCL_LINE */
    }
    int ok = kind == 0 ? xsd_compile(schema) : rng_compile(schema, base_url, include_root);
    arena_free(&schema->names_mem);
    /* a pattern that exceeds the regex limits sets a ValueError */
    ok = ok && regex_cache_schema(schema, schema->root) == 0;
    schema->tree_ns = NULL;
    /* a build that lost an arena allocation is partial, so an error it raised is a symptom of that loss */
    if (schema->mem.no_memory) { /* GCOVR_EXCL_BR_LINE: arena OOM */
        PyErr_NoMemory();        /* GCOVR_EXCL_LINE */
        ok = 0;                  /* GCOVR_EXCL_LINE */
    } /* GCOVR_EXCL_LINE: arena OOM */
    if (!ok) {
        schema_free(schema);
        return NULL;
    }
    PyObject *capsule = PyCapsule_New(schema, CAPSULE_NAME, capsule_destructor);
    if (capsule == NULL) {   /* GCOVR_EXCL_BR_LINE: capsule creation failure is unforceable */
        schema_free(schema); /* GCOVR_EXCL_LINE */
        return NULL;         /* GCOVR_EXCL_LINE */
    }
    return capsule;
}

TH_NODE_API(, PyObject *, turbohtml_schema_validate, (PyObject * module, PyObject *args, PyObject *kwargs),
            (module, args, kwargs), (PyObject * module, PyObject *args, PyObject *kwargs),
            node_argument(PyModule_GetState(module), args, kwargs, 1, "node"), NULL) {
    PyObject *capsule, *node_obj;
    int collect_errors = 1;
    static char *keywords[] = {"schema", "node", "collect_errors", NULL};
    if (!PyArg_ParseTupleAndKeywords(/* GCOVR_EXCL_BR_LINE: the shim supplies fixed argument types */
                                     args, kwargs, "OO|$p", keywords, &capsule, &node_obj, &collect_errors)) {
        return NULL; /* GCOVR_EXCL_LINE */
    }
    th_schema *schema = PyCapsule_GetPointer(capsule, CAPSULE_NAME);
    if (schema == NULL) { /* GCOVR_EXCL_BR_LINE: the shim always passes the capsule it compiled */
        return NULL;      /* GCOVR_EXCL_LINE */
    }
    th_tree *tree;
    th_node *node;
    if (turbohtml_node_borrow(module, node_obj, &tree, &node) < 0) {
        return NULL;
    }
    /* Both validators read the root's tag name and attributes, which only an element has. */
    if (node->type != TH_NODE_DOCUMENT && node->type != TH_NODE_ELEMENT) {
        PyErr_SetString(PyExc_TypeError, "schema validation requires a Document or an Element");
        return NULL;
    }
    PyObject *errors = PyList_New(0);
    if (errors == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE */
    }
    /* Validation buffers and lazy RELAX NG definitions must not outlive this call or mutate a shared schema. */
    th_schema local = *schema;
    local.mem = (arena){0};
    local.ns = (nsscope){0};
    local.regex_scratch = NULL;
    local.ws_buffer = NULL;
    local.ws_cap = 0;
    local.number_buffer = NULL;
    local.number_cap = 0;
    local.posset_spares = NULL;
    local.edecl_items = NULL;
    local.edecl_cap = 0;
    local.attr_names = (named_vec){0};
    attr_scope tree_ns = {.tree = local.tree, .mem = &local.mem};
    local.tree_ns = &tree_ns;
#ifdef TH_STACK_FLOOR
    local.stack_floor = th_stack_floor();
#endif
    local.stack_exhausted = 0;
    if (local.defines.len > 0) {
        const size_t bytes = (size_t)local.defines.len * sizeof(def_entry);
        local.defines.items = arena_alloc(&local.mem, bytes);
        if (local.defines.items == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            Py_DECREF(errors);             /* GCOVR_EXCL_LINE: allocation failure */
            return PyErr_NoMemory();       /* GCOVR_EXCL_LINE: allocation failure */
        }
        memcpy(local.defines.items, schema->defines.items, bytes);
    }
    valctx ctx = {errors, tree, &local, {NULL, 0, 0}, 0, collect_errors, 0};
    th_node *root = node->type == TH_NODE_DOCUMENT ? document_root(tree) : node;
    Py_BEGIN_CRITICAL_SECTION(turbohtml_node_handle(node_obj));
    if (root == NULL) { /* GCOVR_EXCL_BR_LINE: parse_xml rejects a rootless document, so the shim never passes one */
        report(&ctx, node, "structure", "document has no root element"); /* GCOVR_EXCL_LINE */
    } else if (th_node_check_max_depth(root, TH_VALIDATE_MAX_DEPTH, "schema validation") < 0) {
        ctx.failed = 1;
    } else if (schema->kind == 0) {
        xsd_validate_root(&ctx, root);
    } else {
        rng_validate_root(&ctx, root);
    }
    Py_END_CRITICAL_SECTION();
    const int exhausted = regex_exhausted(&local);
    PyMem_Free(ctx.path.data);
    arena_free(&local.mem);
    /* a derivative or pattern check that lost an allocation, a RELAX NG choice that ran past its limit, or a pattern
       check that spent its step budget degraded to a mismatch, so its verdict is unknown; only an arena OOM sets
       ctx.failed during validation */
    /* an arena OOM comes first: a limit or budget reached after a lost allocation is a symptom of that loss */
    if ((local.mem.no_memory || local.choice_limit < 0 || exhausted) && !ctx.failed) { /* GCOVR_EXCL_BR_LINE: OOM */
        ctx.failed = 1;
        if (local.mem.no_memory) { /* GCOVR_EXCL_BR_LINE: arena OOM */
            PyErr_NoMemory();      /* GCOVR_EXCL_LINE */
        } else if (local.choice_limit < 0) {
            PyErr_SetString(PyExc_ValueError, "RELAX NG content model too ambiguous to validate");
        } else {
            PyErr_Format(PyExc_ValueError,
                         "schema patterns need more than %d NFA steps on top of %d per character matched to validate "
                         "this document; lower their repeat counts",
                         RX_BUDGET_BASE, RX_BUDGET_PER_CHAR);
        }
    }
    if (local.stack_exhausted) {
        ctx.failed = 1;
        PyErr_SetString(PyExc_RecursionError,
                        "schema validation needs more C stack than this thread has left; run it on a thread with a "
                        "larger stack, or use a shallower schema");
    }
    if (ctx.failed) {
        Py_DECREF(errors);
        return NULL;
    }
    int valid = ctx.error_count == 0;
    return Py_BuildValue("(ON)", valid ? Py_True : Py_False, errors);
}

#ifdef FUZZING_BUILD_MODE_UNSAFE_FOR_PRODUCTION
char th_fuzz_schema_arena_overread(size_t size) {
    arena mem = {NULL};
    const volatile char *first = arena_alloc(&mem, size);
    (void)arena_alloc(&mem, size);
    char value = first[size];
    arena_free(&mem);
    return value;
}
#endif
