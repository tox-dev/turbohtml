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
#include "core/vec.h"

#include "tokenizer/binding.h" /* Py_BEGIN_CRITICAL_SECTION shim for the GIL/pre-3.13 build */

#include "dom/tree.h"

#include <stdarg.h>
#include <stdlib.h>
#include <string.h>

static const char XSD_NS[] = "http://www.w3.org/2001/XMLSchema";
static const char XSD_DT_NS[] = "http://www.w3.org/2001/XMLSchema-datatypes";
static const char RNG_NS[] = "http://relaxng.org/ns/structure/1.0";
static const char XML_URI[] = "http://www.w3.org/XML/1998/namespace";

/* Schema compilation and instance validation still have recursive grammar walks. Preflight the tree far enough below
   the smallest supported thread stack that those walks cannot exhaust it. */
#define TH_VALIDATE_MAX_DEPTH 400

/* ======================= bump arena ======================= */

typedef struct arena_block {
    struct arena_block *next;
    size_t used, cap;
} arena_block;

typedef struct {
    arena_block *head;
} arena;

static void *arena_alloc(arena *mem, size_t size) {
    size = (size + 15u) & ~(size_t)15u;
    arena_block *block = mem->head;
    if (block == NULL || block->cap - block->used < size) {
        size_t want = size > 4096u ? size : 4096u;
        arena_block *fresh = PyMem_Malloc(sizeof(arena_block) + want);
        if (fresh == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;     /* GCOVR_EXCL_LINE */
        }
        fresh->next = mem->head;
        fresh->used = 0;
        fresh->cap = want;
        mem->head = fresh;
        block = fresh;
    }
    void *out = (char *)(block + 1) + block->used;
    block->used += size;
    return out;
}

static void arena_free(arena *mem) {
    arena_block *block = mem->head;
    while (block != NULL) {
        arena_block *next = block->next;
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

static int u_eq_u(const Py_UCS4 *left, Py_ssize_t left_len, const Py_UCS4 *right, Py_ssize_t right_len) {
    if (left_len != right_len) {
        return 0;
    }
    return memcmp(left, right, (size_t)left_len * sizeof(Py_UCS4)) == 0;
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
   across validating threads stays read-only. */
struct th_schema;
typedef struct {
    th_node *node;
    qname name;
} sqname_entry;
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

/* The constant xml-prefix namespace as UCS4, widened once; every later call reuses the filled buffer. */
static const Py_UCS4 *xml_namespace_uri(void) {
    static Py_UCS4 xml_uri[sizeof(XML_URI)];
    for (Py_ssize_t index = 0; index < (Py_ssize_t)sizeof(XML_URI) - 1; index++) {
        xml_uri[index] = (Py_UCS4)(unsigned char)XML_URI[index];
    }
    return xml_uri;
}

static void resolve_ns(th_tree *tree, th_node *node, const Py_UCS4 *prefix, Py_ssize_t prefix_len, const Py_UCS4 **uri,
                       Py_ssize_t *uri_len) {
    if (prefix_len == 3 && u_eq_ascii(prefix, 3, "xml")) {
        *uri = xml_namespace_uri();
        *uri_len = sizeof(XML_URI) - 1;
        return;
    }
    for (th_node *walk = node; walk != NULL; walk = walk->parent) {
        if (walk->type != TH_NODE_ELEMENT) {
            continue;
        }
        for (Py_ssize_t index = 0; index < walk->attr_count; index++) {
            Py_ssize_t alen = 0;
            const char *abytes = th_attr_name(tree, walk->attrs[index].name_atom, &alen);
            if (prefix_len == 0 && alen == 5 && memcmp(abytes, "xmlns", 5) == 0) {
                *uri = walk->attrs[index].value;
                *uri_len = walk->attrs[index].value_len;
                return;
            }
            if (prefix_len != 0 && alen == prefix_len + 6 && memcmp(abytes, "xmlns:", 6) == 0 &&
                u_eq_ascii(prefix, prefix_len, abytes + 6)) {
                *uri = walk->attrs[index].value;
                *uri_len = walk->attrs[index].value_len;
                return;
            }
        }
    }
    *uri = NULL;
    *uri_len = 0;
}

static qname node_qname(th_tree *tree, th_node *element, const Py_UCS4 *name, Py_ssize_t name_len, int is_attr) {
    const Py_UCS4 *local, *prefix;
    Py_ssize_t local_len = 0, prefix_len = 0;
    split_prefix(name, name_len, &local, &local_len, &prefix, &prefix_len);
    qname out = {local, local_len, NULL, 0};
    if (prefix_len == 0 && is_attr) {
        return out;
    }
    resolve_ns(tree, element, prefix, prefix_len, &out.uri, &out.uri_len);
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

static int path_push(pathbuf *path, const Py_UCS4 *local, Py_ssize_t local_len) {
    if (path_reserve(path, local_len * 4 + 1) < 0) { /* GCOVR_EXCL_BR_LINE: reserve only fails on OOM */
        return -1;                                   /* GCOVR_EXCL_LINE */
    }
    path->data[path->len++] = '/';
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
    return 0;
}

struct th_schema;

typedef struct {
    PyObject *errors; /* list of (message, path, line, type) tuples */
    th_tree *tree;
    struct th_schema *schema;
    pathbuf path;
    int failed;
} valctx;

static void report(valctx *ctx, th_node *node, const char *type, const char *fmt, ...) {
    if (ctx->failed) { /* GCOVR_EXCL_BR_LINE: failed is set only by an unforceable allocation failure */
        return;        /* GCOVR_EXCL_LINE */
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
   literal), or the empty run. Used at compile time, without a validation context. */
static const Py_UCS4 *element_text_raw(th_tree *tree, th_node *node, Py_ssize_t *out_len) {
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        if (is_chardata(child)) {
            return th_node_data(tree, child, out_len);
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

typedef struct def_part {
    th_node *node;
    struct def_part *next;
} def_part;

typedef struct def_entry {
    const Py_UCS4 *name;
    Py_ssize_t len;
    th_node *first; /* first <define> element for this name */
    def_part *extra, *last;
    pattern *built; /* memoized pattern, NULL until first resolved */
    int building;   /* recursion guard */
} def_entry;

typedef struct {
    def_entry *items;
    size_t *slots;
    Py_ssize_t len, cap;
    size_t slot_cap;
} def_vec;

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
    def_vec defines;
    /* every schema element node's resolved qname, sorted by node pointer for is_schema_el */
    sqname_entry *sqnames;
    Py_ssize_t sqname_count;
} th_schema;

/* Look up a schema element node's precomputed qname. schema_build_qname_cache enters every
   element node of the schema tree before any classification runs, and is_schema_el only ever
   asks about schema element nodes, so the search always hits. */
static qname schema_direct_qname(const struct th_schema *schema, th_node *node) {
    return node_qname(schema->tree, node, node->text, node->text_len, 0);
}

static const qname *schema_node_qname(const th_schema *schema, th_node *node) {
    if (schema->sqname_count == 0) { /* a schema below the cache threshold resolves directly; see the build gate */
        return NULL;
    }
    Py_ssize_t lo = 0, hi = schema->sqname_count - 1;
    while (lo <= hi) { /* GCOVR_EXCL_BR_LINE: the searched-for node is always present, so the loop exits by return */
        Py_ssize_t mid = lo + ((hi - lo) >> 1);
        th_node *at = schema->sqnames[mid].node;
        if (at == node) {
            return &schema->sqnames[mid].name;
        }
        if (at < node) {
            lo = mid + 1;
        } else {
            hi = mid - 1;
        }
    }
    return NULL; /* GCOVR_EXCL_LINE: every schema element node is cached, so the search never misses */
}

static Py_ssize_t schema_count_elements(th_node *parent) {
    Py_ssize_t total = 0;
    for (th_node *child = parent->first_child; child != NULL; child = child->next_sibling) {
        if (child->type == TH_NODE_ELEMENT) {
            total += 1 + schema_count_elements(child);
        }
    }
    return total;
}

/* One in-scope xmlns binding; the stack grows entering an element that declares bindings and shrinks leaving
   it, so resolving a prefix is a top-down scan of what is in scope instead of an ancestor walk. */
typedef struct {
    const char *prefix; /* UTF-8 bytes into the interned attribute name; "" for the default namespace */
    Py_ssize_t prefix_len;
    const Py_UCS4 *uri;
    Py_ssize_t uri_len;
} ns_binding;

typedef struct {
    ns_binding *items;
    Py_ssize_t len;
    Py_ssize_t cap;
} ns_scope;

static int ns_scope_push(th_schema *schema, ns_scope *scope, const char *prefix, Py_ssize_t prefix_len,
                         const Py_UCS4 *uri, Py_ssize_t uri_len) {
    if (scope->len == scope->cap) {
        Py_ssize_t cap = scope->cap ? scope->cap * 2 : 8;
        ns_binding *items = arena_alloc(&schema->mem, (size_t)cap * sizeof(ns_binding));
        if (items == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return -1;       /* GCOVR_EXCL_LINE */
        }
        if (scope->len > 0) {
            memcpy(items, scope->items, (size_t)scope->len * sizeof(ns_binding));
        }
        scope->items = items;
        scope->cap = cap;
    }
    scope->items[scope->len].prefix = prefix;
    scope->items[scope->len].prefix_len = prefix_len;
    scope->items[scope->len].uri = uri;
    scope->items[scope->len].uri_len = uri_len;
    scope->len++;
    return 0;
}

/* The innermost binding for prefix, or no namespace at all. The xml prefix needs no special case here: the
   cache resolves schema-element tag prefixes, an xml:-prefixed schema element is foreign, and is_schema_el
   maps both the xml namespace and no namespace to "not a schema element" -- resolve_ns keeps the xml rule for
   the document path, where xml:lang and friends are observable. */
static void ns_scope_resolve(const ns_scope *scope, const Py_UCS4 *prefix, Py_ssize_t prefix_len, const Py_UCS4 **uri,
                             Py_ssize_t *uri_len) {
    for (Py_ssize_t index = scope->len - 1; index >= 0; index--) {
        const ns_binding *binding = &scope->items[index];
        if (binding->prefix_len == prefix_len && u_eq_ascii(prefix, prefix_len, binding->prefix)) {
            *uri = binding->uri;
            *uri_len = binding->uri_len;
            return;
        }
    }
    *uri = NULL;
    *uri_len = 0;
}

static int schema_fill_qnames(th_schema *schema, th_node *parent, Py_ssize_t *at, ns_scope *scope) {
    for (th_node *child = parent->first_child; child != NULL; child = child->next_sibling) {
        if (child->type != TH_NODE_ELEMENT) {
            continue;
        }
        /* the bindings this element declares extend the in-scope stack for its subtree only */
        Py_ssize_t pushed = scope->len;
        for (Py_ssize_t index = 0; index < child->attr_count; index++) {
            Py_ssize_t alen = 0;
            const char *abytes = th_attr_name(schema->tree, child->attrs[index].name_atom, &alen);
            if (!is_xmlns_decl(abytes, alen)) {
                continue;
            }
            /* a bare xmlns binds the default namespace; its prefix is the empty string, not a slice past the name */
            const char *decl_prefix = alen > 5 ? abytes + 6 : "";
            Py_ssize_t decl_prefix_len = alen > 5 ? alen - 6 : 0;
            /* GCOVR_EXCL_BR_START: arena OOM is unforceable from a test */
            if (ns_scope_push(schema, scope, decl_prefix, decl_prefix_len, child->attrs[index].value,
                              child->attrs[index].value_len) < 0) {
                /* GCOVR_EXCL_BR_STOP */
                return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
            }
        }
        const Py_UCS4 *local;
        const Py_UCS4 *prefix;
        Py_ssize_t local_len = 0;
        Py_ssize_t prefix_len = 0;
        split_prefix(child->text, child->text_len, &local, &local_len, &prefix, &prefix_len);
        qname name = {local, local_len, NULL, 0};
        ns_scope_resolve(scope, prefix, prefix_len, &name.uri, &name.uri_len);
        schema->sqnames[*at].node = child;
        schema->sqnames[*at].name = name;
        (*at)++;
        if (schema_fill_qnames(schema, child, at, scope) < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return -1;                                          /* GCOVR_EXCL_LINE */
        }
        scope->len = pushed;
    }
    return 0;
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
    Py_ssize_t at = 0;
    ns_scope scope = {0};
    if (schema_fill_qnames(schema, document, &at, &scope) < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return -1;                                               /* GCOVR_EXCL_LINE */
    }
    schema->sqname_count = count;
    qsort(schema->sqnames, (size_t)count, sizeof(sqname_entry), sqname_cmp);
    return 0;
}

static int named_push(th_schema *schema, named_vec *vec, const Py_UCS4 *name, Py_ssize_t len, th_node *node) {
    if (vec->len == vec->cap) {
        Py_ssize_t cap = vec->cap ? vec->cap * 2 : 8;
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

static uint64_t named_hash(const Py_UCS4 *name, Py_ssize_t len) {
    uint64_t hash = UINT64_C(1469598103934665603);
    for (Py_ssize_t index = 0; index < len; index++) {
        hash ^= name[index];
        hash *= UINT64_C(1099511628211);
    }
    return hash;
}

static int named_index(th_schema *schema, named_vec *vec) {
    if (vec->len < 8) {
        return 0;
    }
    size_t cap = 8;
    while (cap < (size_t)vec->len * 2) {
        cap *= 2;
    }
    named_node **slots = arena_alloc(&schema->mem, cap * sizeof(named_node *));
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

static th_node *named_find(const named_vec *vec, const Py_UCS4 *name, Py_ssize_t len) {
    if (vec->slots == NULL) {
        for (Py_ssize_t index = 0; index < vec->len; index++) {
            if (u_eq_u(vec->items[index].name, vec->items[index].len, name, len)) {
                return vec->items[index].node;
            }
        }
        return NULL;
    }
    size_t slot = (size_t)named_hash(name, len) & (vec->slot_cap - 1);
    while (vec->slots[slot] != NULL) {
        named_node *item = vec->slots[slot];
        if (u_eq_u(item->name, item->len, name, len)) {
            return item->node;
        }
        slot = (slot + 1) & (vec->slot_cap - 1);
    }
    return NULL;
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
        if (is_chardata(child)) {
            Py_ssize_t data_len = 0;
            const Py_UCS4 *data = th_node_data(ctx->tree, child, &data_len);
            memcpy(buffer + offset, data, (size_t)child->text_len * sizeof(Py_UCS4));
            offset += child->text_len;
        }
    }
    buffer[offset] = 0;
    *out_len = offset;
    return buffer;
}

#include "validate/datatypes.h"
#include "validate/xsd.h"
#include "validate/relaxng.h"

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
    if (!PyArg_ParseTuple(args, "iU", &kind, &source)) { /* GCOVR_EXCL_BR_LINE: the shim always passes (int, str) */
        return NULL;                                     /* GCOVR_EXCL_LINE */
    }
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
    /* The cache repays its build through the XSD content-model matcher, which re-classifies the same schema
       elements on every particle step; RELAX NG's derivative walk never revisits at that rate, and the gated
       compile benchmark showed the eager build as a pure 8% loss there, so RNG resolves names directly. */
    if (kind == 0 && schema_build_qname_cache(schema) < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        schema_free(schema);                                 /* GCOVR_EXCL_LINE */
        return PyErr_NoMemory();                             /* GCOVR_EXCL_LINE */
    }
    int ok = kind == 0 ? xsd_compile(schema) : rng_compile(schema);
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

PyObject *turbohtml_schema_validate(PyObject *module, PyObject *args) {
    PyObject *capsule, *node_obj;
    if (!PyArg_ParseTuple(args, "OO", &capsule, &node_obj)) { /* GCOVR_EXCL_BR_LINE: the shim always passes two args */
        return NULL;                                          /* GCOVR_EXCL_LINE */
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
    PyObject *errors = PyList_New(0);
    if (errors == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE */
    }
    valctx ctx = {errors, tree, schema, {NULL, 0, 0}, 0};
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
    PyMem_Free(ctx.path.data);
    if (ctx.failed) {
        Py_DECREF(errors);
        return NULL;
    }
    int valid = PyList_GET_SIZE(errors) == 0;
    return Py_BuildValue("(ON)", valid ? Py_True : Py_False, errors);
}
