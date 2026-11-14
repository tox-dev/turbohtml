#include "serialize/internal.h"
#include "serialize/c14n_writers.h"

#include "dom/tree.h"
#include "dom/tree_internal.h"

#include <stdlib.h>
#include <string.h>

static const char C14N_XML[] = "http://www.w3.org/XML/1998/namespace";
#define C14N_STACK_ATTRS 32

static int c14n_xml_1_1_inherits_xml_attribute(const char *name, Py_ssize_t len) {
    return (len == 8 && (memcmp(name + 4, "lang", 4) == 0 || memcmp(name + 4, "base", 4) == 0)) ||
           (len == 9 && memcmp(name + 4, "space", 5) == 0);
}

static int c14n_prefix_forced(const th_c14n_opts *opts, const char *prefix, Py_ssize_t prefix_len) {
    if (prefix_len == 0) {
        prefix = "#default";
        prefix_len = 8;
    }
    int forced = 0;
    for (Py_ssize_t index = 0; index < opts->inclusive_count && !forced; index++) {
        forced = strncmp(opts->inclusive[index], prefix, (size_t)prefix_len) == 0 &&
                 opts->inclusive[index][prefix_len] == '\0';
    }
    return forced;
}

static int c14n_name_is_nsdecl(const char *name, Py_ssize_t len) {
    return (len == 5 && memcmp(name, "xmlns", 5) == 0) || (len > 6 && memcmp(name, "xmlns:", 6) == 0);
}

typedef struct {
    const char *prefix;
    Py_ssize_t prefix_len;
    const Py_UCS4 *uri;
    Py_ssize_t uri_len;
} c14n_xml_ns;

typedef struct {
    const char *name;
    Py_ssize_t name_len;
    const char *local;
    Py_ssize_t local_len;
    const Py_UCS4 *uri;
    const char *ascii_uri;
    Py_ssize_t uri_len;
    const Py_UCS4 *value;
    Py_ssize_t value_len;
} c14n_xml_attr;

/* One distinct prefix the walk has met, in a declaration or in a name. `binding` is its innermost
   in-scope c14n_xml_binding and `use` the innermost open element's c14n_xml_use of it, each -1 when
   there is none, so neither answer costs a scan of the declarations in scope. `forced` marks a prefix
   rendered wherever it is in scope: every prefix in inclusive mode, an InclusiveNamespaces PrefixList
   member in exclusive mode. */
typedef struct {
    const char *name;
    Py_ssize_t name_len;
    char *owned; /* the name's storage when a tag name introduced it, NULL when an attribute name holds it */
    uint64_t hash;
    Py_ssize_t binding;
    Py_ssize_t use;
    int forced;
} c14n_xml_prefix;

/* One xmlns declaration in scope, popped when the element that carries it closes. */
typedef struct {
    c14n_xml_ns ns;
    Py_ssize_t prefix;   /* index into the scope's prefixes */
    Py_ssize_t shadowed; /* the binding of the same prefix this one hides until it closes, or -1 */
    const th_node *owner;
} c14n_xml_binding;

/* One open element's visible use of a prefix. Exclusive canonicalization declares a namespace on an
   element only when the nearest output ancestor that visibly uses the prefix has it bound otherwise. */
typedef struct {
    Py_ssize_t prefix;  /* index into the scope's prefixes */
    Py_ssize_t binding; /* what the prefix is bound to on the using element, or -1 */
    Py_ssize_t outer;   /* the nearest using ancestor's use of the same prefix, or -1 */
    const th_node *owner;
} c14n_xml_use;

/* The namespace scope of the element being written, kept on the walk: an element pushes its own
   declarations and uses as it opens and pops them as it closes, so its cost follows its own attributes
   and not the declarations it inherits. Deriving the scope from the ancestors for every element made a
   root with N declarations and N children cost O(N^3). */
typedef struct {
    const th_c14n_opts *opts;
    c14n_xml_prefix *prefixes;
    Py_ssize_t prefixes_len, prefixes_cap;
    Py_ssize_t *slots; /* open addressing over the prefix name: an index into prefixes plus one, 0 when free */
    Py_ssize_t slots_cap;
    c14n_xml_binding *bindings;
    Py_ssize_t bindings_len, bindings_cap;
    c14n_xml_use *uses;
    Py_ssize_t uses_len, uses_cap;
    char *tag_prefix; /* reusable buffer holding a tag name's prefix as UTF-8 */
    Py_ssize_t tag_prefix_cap;
    uint64_t hash_seed; /* per-walk salt so chosen prefixes cannot flood one slot */
} c14n_xml_scope;

/* Make room for one more `size`-byte item after the `len` an array holds. Returns the array, moved
   when it grew, or NULL on allocation failure with the array left as it was. */
static void *c14n_xml_reserve(void *items, Py_ssize_t len, Py_ssize_t *cap, size_t size) {
    if (len < *cap) {
        return items;
    }
    size_t grown_cap;
    size_t bytes;
    int grew = th_grow_cap((size_t)len + 1, (size_t)*cap, 8, size, &grown_cap, &bytes);
    if (!grew) {     /* GCOVR_EXCL_BR_LINE: size overflow needs a length no allocation could hold */
        return NULL; /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
    }
    void *grown = PyMem_Realloc(items, bytes);
    if (grown != NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        *cap = (Py_ssize_t)grown_cap;
    }
    return grown;
}

/* Salted FNV-1a under a splitmix64 finalizer, the hash the XML parser keys its own prefix table with. */
static uint64_t c14n_xml_prefix_hash(const c14n_xml_scope *scope, const char *name, Py_ssize_t name_len) {
    uint64_t hash = scope->hash_seed ^ UINT64_C(14695981039346656037);
    for (Py_ssize_t offset = 0; offset < name_len; offset++) {
        hash = (hash ^ (unsigned char)name[offset]) * UINT64_C(1099511628211);
    }
    hash = (hash ^ (hash >> 30)) * UINT64_C(0xBF58476D1CE4E5B9);
    hash = (hash ^ (hash >> 27)) * UINT64_C(0x94D049BB133111EB);
    return hash ^ (hash >> 31);
}

/* The slot holding prefix [name, name+name_len), or the free slot it would take. */
static Py_ssize_t c14n_xml_prefix_slot(const c14n_xml_scope *scope, uint64_t hash, const char *name,
                                       Py_ssize_t name_len) {
    Py_ssize_t slot = (Py_ssize_t)(hash & (uint64_t)(scope->slots_cap - 1));
    while (scope->slots[slot] != 0) {
        const c14n_xml_prefix *known = &scope->prefixes[scope->slots[slot] - 1];
        if (known->name_len == name_len && memcmp(known->name, name, (size_t)name_len) == 0) {
            return slot;
        }
        slot = (slot + 1) & (scope->slots_cap - 1);
    }
    return slot;
}

/* The record of prefix [name, name+name_len), or NULL when the walk has not met it. */
static const c14n_xml_prefix *c14n_xml_prefix_find(const c14n_xml_scope *scope, const char *name, Py_ssize_t name_len) {
    if (scope->slots_cap == 0) {
        return NULL;
    }
    Py_ssize_t slot = c14n_xml_prefix_slot(scope, c14n_xml_prefix_hash(scope, name, name_len), name, name_len);
    return scope->slots[slot] == 0 ? NULL : &scope->prefixes[scope->slots[slot] - 1];
}

/* Double the slot table and re-slot every record by its stored hash. */
static int c14n_xml_slots_grow(c14n_xml_scope *scope) {
    size_t cap;
    size_t bytes;
    size_t needed = ((size_t)scope->prefixes_len + 1) * 2;
    int grew = th_grow_cap(needed, (size_t)scope->slots_cap, 64, sizeof(Py_ssize_t), &cap, &bytes);
    if (!grew) {   /* GCOVR_EXCL_BR_LINE: size overflow needs a length no allocation could hold */
        return -1; /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
    }
    Py_ssize_t *slots = PyMem_Calloc(1, bytes);
    if (slots == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (Py_ssize_t index = 0; index < scope->prefixes_len; index++) {
        size_t slot = (size_t)scope->prefixes[index].hash & (cap - 1);
        while (slots[slot] != 0) {
            slot = (slot + 1) & (cap - 1);
        }
        slots[slot] = index + 1;
    }
    PyMem_Free(scope->slots);
    scope->slots = slots;
    scope->slots_cap = (Py_ssize_t)cap;
    return 0;
}

/* The index of the record of prefix [name, name+name_len), entered on first sight, or -1 on allocation
   failure. The table grows before one more record would pass a load factor of 1/2, which keeps probing
   amortized O(1). `transient` says the name lives in the scope's tag buffer, so a new record copies it. */
static Py_ssize_t c14n_xml_prefix_intern(c14n_xml_scope *scope, const char *name, Py_ssize_t name_len, int transient) {
    if ((scope->prefixes_len + 1) * 2 > scope->slots_cap) {
        if (c14n_xml_slots_grow(scope) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;                        /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    uint64_t hash = c14n_xml_prefix_hash(scope, name, name_len);
    Py_ssize_t slot = c14n_xml_prefix_slot(scope, hash, name, name_len);
    if (scope->slots[slot] != 0) {
        return scope->slots[slot] - 1;
    }
    c14n_xml_prefix *prefixes =
        c14n_xml_reserve(scope->prefixes, scope->prefixes_len, &scope->prefixes_cap, sizeof(c14n_xml_prefix));
    if (prefixes == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;          /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    scope->prefixes = prefixes;
    char *owned = NULL;
    if (transient) {
        owned = PyMem_Malloc((size_t)name_len);
        if (owned == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        memcpy(owned, name, (size_t)name_len);
        name = owned;
    }
    int forced = !scope->opts->exclusive || c14n_prefix_forced(scope->opts, name, name_len);
    scope->prefixes[scope->prefixes_len] = (c14n_xml_prefix){name, name_len, owned, hash, -1, -1, forced};
    scope->slots[slot] = ++scope->prefixes_len;
    return scope->prefixes_len - 1;
}

static int c14n_xml_absolute_uri(const Py_UCS4 *uri, Py_ssize_t len) {
    if (len == 0) {
        return 1;
    }
    if (len >= 4 && uri[0] == 'u' && uri[1] == 'r' && uri[2] == 'n' && uri[3] == ':') {
        return 1;
    }
    if (len >= 5 && uri[0] == 'h' && uri[1] == 't' && uri[2] == 't' && uri[3] == 'p' &&
        (uri[4] == ':' || (len >= 6 && uri[4] == 's' && uri[5] == ':'))) {
        return 1;
    }
    Py_UCS4 first = uri[0] | 0x20;
    if (first < 'a' || first > 'z') {
        return 0;
    }
    for (Py_ssize_t index = 1; index < len; index++) {
        Py_UCS4 character = uri[index] | 0x20;
        if (character == ':') {
            return 1;
        }
        if ((character < 'a' || character > 'z') && (character < '0' || character > '9') && character != '+' &&
            character != '-' && character != '.') {
            return 0;
        }
    }
    return 0;
}

/* Enter the xmlns declarations `element` carries. The apex's ancestors are entered innermost first,
   so `inherited` keeps only the prefixes nothing nearer has bound; the walk never pops those. */
static int c14n_xml_scope_declare(c14n_xml_scope *scope, th_tree *tree, const th_node *element, int inherited) {
    for (Py_ssize_t index = 0; index < element->attr_count; index++) {
        Py_ssize_t name_len;
        const char *name = th_attr_name(tree, element->attrs[index].name_atom, &name_len);
        if (!c14n_name_is_nsdecl(name, name_len)) {
            continue;
        }
        const char *prefix = name_len == 5 ? "" : name + 6;
        Py_ssize_t prefix_len = name_len == 5 ? 0 : name_len - 6;
        if (prefix_len == 3 && memcmp(prefix, "xml", 3) == 0) {
            continue;
        }
        Py_ssize_t record = c14n_xml_prefix_intern(scope, prefix, prefix_len, 0);
        if (record < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;    /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (inherited && scope->prefixes[record].binding >= 0) {
            continue;
        }
        c14n_xml_binding *bindings =
            c14n_xml_reserve(scope->bindings, scope->bindings_len, &scope->bindings_cap, sizeof(c14n_xml_binding));
        if (bindings == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;          /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        scope->bindings = bindings;
        c14n_xml_binding *binding = &scope->bindings[scope->bindings_len];
        binding->ns = (c14n_xml_ns){prefix, prefix_len, element->attrs[index].value, element->attrs[index].value_len};
        binding->prefix = record;
        binding->shadowed = scope->prefixes[record].binding;
        binding->owner = element;
        scope->prefixes[record].binding = scope->bindings_len++;
    }
    return 0;
}

/* Record that `node` visibly uses prefix [name, name+name_len), with the binding the prefix has there.
   A forced prefix is rendered without regard to its use, so it keeps no record; neither does a use
   under the same binding as the nearest one, which a descendant would compare alike and which covers
   the element's own second use of a prefix. */
static int c14n_xml_scope_use(c14n_xml_scope *scope, const th_node *node, const char *name, Py_ssize_t name_len,
                              int transient) {
    Py_ssize_t record = c14n_xml_prefix_intern(scope, name, name_len, transient);
    if (record < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    c14n_xml_prefix *prefix = &scope->prefixes[record];
    if (prefix->forced || (prefix->use >= 0 && scope->uses[prefix->use].binding == prefix->binding)) {
        return 0;
    }
    c14n_xml_use *uses = c14n_xml_reserve(scope->uses, scope->uses_len, &scope->uses_cap, sizeof(c14n_xml_use));
    if (uses == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    scope->uses = uses;
    scope->uses[scope->uses_len] = (c14n_xml_use){record, prefix->binding, prefix->use, node};
    prefix->use = scope->uses_len++;
    return 0;
}

/* Record the prefixes `node` visibly uses: the one its tag name carries, or the default namespace for
   an unprefixed name, and the one each attribute name carries. A tag name is stored as code points, so
   its prefix is first written out as the UTF-8 that attribute names, and so prefixes, are kept in. */
static int c14n_xml_scope_uses(c14n_xml_scope *scope, th_tree *tree, const th_node *node) {
    Py_ssize_t colon = 0;
    while (colon < node->text_len && node->text[colon] != ':') {
        colon++;
    }
    if (colon == node->text_len) {
        if (c14n_xml_scope_use(scope, node, "", 0, 0) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure only */
            return -1;                                       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    } else if (colon > 0) {
        if (colon * 4 > scope->tag_prefix_cap) {
            char *grown = PyMem_Realloc(scope->tag_prefix, (size_t)colon * 4);
            if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            scope->tag_prefix = grown;
            scope->tag_prefix_cap = colon * 4;
        }
        unsigned char *encoded = (unsigned char *)scope->tag_prefix;
        Py_ssize_t byte = 0;
        for (Py_ssize_t index = 0; index < colon; index++) {
            Py_UCS4 character = node->text[index];
            if (character < 0x80) {
                encoded[byte++] = (unsigned char)character;
            } else if (character < 0x800) {
                encoded[byte++] = (unsigned char)(0xC0 | (character >> 6));
                encoded[byte++] = (unsigned char)(0x80 | (character & 0x3F));
            } else if (character < 0x10000) {
                encoded[byte++] = (unsigned char)(0xE0 | (character >> 12));
                encoded[byte++] = (unsigned char)(0x80 | ((character >> 6) & 0x3F));
                encoded[byte++] = (unsigned char)(0x80 | (character & 0x3F));
            } else {
                encoded[byte++] = (unsigned char)(0xF0 | (character >> 18));
                encoded[byte++] = (unsigned char)(0x80 | ((character >> 12) & 0x3F));
                encoded[byte++] = (unsigned char)(0x80 | ((character >> 6) & 0x3F));
                encoded[byte++] = (unsigned char)(0x80 | (character & 0x3F));
            }
        }
        int used = c14n_xml_scope_use(scope, node, scope->tag_prefix, byte, 1);
        if (used < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;  /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        Py_ssize_t name_len;
        const char *name = th_attr_name(tree, node->attrs[index].name_atom, &name_len);
        for (Py_ssize_t offset = 1; offset < name_len; offset++) {
            if (name[offset] != ':') {
                continue;
            }
            if (c14n_xml_scope_use(scope, node, name, offset, 0) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                return -1;                                              /* GCOVR_EXCL_LINE: allocation-failure path */
            }
        }
    }
    return 0;
}

/* Pop what `node` declared and used as it closes, restoring what each entry hid. */
static void c14n_xml_scope_close(c14n_xml_scope *scope, const th_node *node) {
    while (scope->bindings_len > 0 && scope->bindings[scope->bindings_len - 1].owner == node) {
        const c14n_xml_binding *closing = &scope->bindings[--scope->bindings_len];
        scope->prefixes[closing->prefix].binding = closing->shadowed;
    }
    while (scope->uses_len > 0 && scope->uses[scope->uses_len - 1].owner == node) {
        const c14n_xml_use *closing = &scope->uses[--scope->uses_len];
        scope->prefixes[closing->prefix].use = closing->outer;
    }
}

static void c14n_xml_scope_free(c14n_xml_scope *scope) {
    for (Py_ssize_t index = 0; index < scope->prefixes_len; index++) {
        PyMem_Free(scope->prefixes[index].owned);
    }
    PyMem_Free(scope->prefixes);
    PyMem_Free(scope->slots);
    PyMem_Free(scope->bindings);
    PyMem_Free(scope->uses);
    PyMem_Free(scope->tag_prefix);
}

static int c14n_xml_uri_equal(const c14n_xml_ns *left, const c14n_xml_ns *right) {
    return left->uri_len == right->uri_len &&
           memcmp(left->uri, right->uri, (size_t)left->uri_len * sizeof(Py_UCS4)) == 0;
}

/* Whether an element declares `namespace`. `has_ancestor` says an output ancestor answers for the
   prefix (the parent for a forced prefix, the nearest visible user otherwise) and `inherited` is what
   that ancestor has it bound to, NULL when unbound; a binding the ancestor already carries is omitted. */
static int c14n_xml_ns_render(const c14n_xml_ns *namespace, int has_ancestor, const c14n_xml_ns *inherited) {
    int reset_default = 0;
    if (has_ancestor) {
        if (inherited != NULL ? c14n_xml_uri_equal(namespace, inherited) : namespace->uri_len == 0) {
            return 0;
        }
        reset_default = inherited != NULL && inherited->uri_len > 0;
    }
    return namespace->prefix_len > 0 || namespace->uri_len > 0 || reset_default;
}

static int c14n_xml_relative_uri(const c14n_xml_ns *namespace) {
    if (c14n_xml_absolute_uri(namespace->uri, namespace->uri_len)) {
        return 0;
    }
    PyErr_SetString(PyExc_ValueError, "relative namespace URI cannot be canonicalized");
    return 1;
}

/* Gather the declarations `node` writes from the scope it has just entered: `own_bindings` and
   `own_uses` are where its entries start. The apex has no output ancestor, so it answers for every
   binding in scope; below it a forced prefix can only differ from the parent's where the element
   declares it, and a visibly used one only where the element recorded a use. Returns the count, or -1
   with ValueError set for a relative namespace URI. */
static Py_ssize_t c14n_xml_ns_collect(const c14n_xml_scope *scope, int is_apex, Py_ssize_t own_bindings,
                                      Py_ssize_t own_uses, c14n_xml_ns *namespaces) {
    Py_ssize_t count = 0;
    if (is_apex) {
        for (Py_ssize_t index = 0; index < scope->prefixes_len; index++) {
            const c14n_xml_prefix *prefix = &scope->prefixes[index];
            if (prefix->binding < 0) {
                continue;
            }
            const c14n_xml_ns *namespace = &scope->bindings[prefix->binding].ns;
            if (c14n_xml_relative_uri(namespace)) {
                return -1;
            }
            if (prefix->forced && c14n_xml_ns_render(namespace, 0, NULL)) {
                namespaces[count++] = *namespace;
            }
        }
    } else {
        for (Py_ssize_t index = own_bindings; index < scope->bindings_len; index++) {
            const c14n_xml_binding *binding = &scope->bindings[index];
            if (c14n_xml_relative_uri(&binding->ns)) {
                return -1;
            }
            const c14n_xml_ns *inherited = binding->shadowed < 0 ? NULL : &scope->bindings[binding->shadowed].ns;
            if (scope->prefixes[binding->prefix].forced && c14n_xml_ns_render(&binding->ns, 1, inherited)) {
                namespaces[count++] = binding->ns;
            }
        }
    }
    for (Py_ssize_t index = own_uses; index < scope->uses_len; index++) {
        const c14n_xml_use *use = &scope->uses[index];
        if (use->binding < 0) {
            continue;
        }
        const c14n_xml_use *outer = use->outer < 0 ? NULL : &scope->uses[use->outer];
        const c14n_xml_ns *inherited = outer == NULL || outer->binding < 0 ? NULL : &scope->bindings[outer->binding].ns;
        if (c14n_xml_ns_render(&scope->bindings[use->binding].ns, outer != NULL, inherited)) {
            namespaces[count++] = scope->bindings[use->binding].ns;
        }
    }
    return count;
}

static int c14n_xml_ns_cmp(const void *left, const void *right) {
    const c14n_xml_ns *first = left;
    const c14n_xml_ns *second = right;
    Py_ssize_t common = first->prefix_len < second->prefix_len ? first->prefix_len : second->prefix_len;
    int order = memcmp(first->prefix, second->prefix, (size_t)common);
    return order != 0 ? order : (first->prefix_len > second->prefix_len) - (first->prefix_len < second->prefix_len);
}

static c14n_xml_attr c14n_xml_make_attr(const char *name, Py_ssize_t name_len, const Py_UCS4 *value,
                                        Py_ssize_t value_len, const c14n_xml_scope *scope) {
    c14n_xml_attr attr = {name, name_len, name, name_len, NULL, "", 0, value, value_len};
    const char *colon = memchr(name, ':', (size_t)name_len);
    if (colon != NULL) {
        Py_ssize_t prefix_len = colon - name;
        attr.local = colon + 1;
        attr.local_len = name_len - prefix_len - 1;
        if (prefix_len == 3 && memcmp(name, "xml", 3) == 0) {
            attr.ascii_uri = C14N_XML;
            attr.uri_len = (Py_ssize_t)sizeof(C14N_XML) - 1;
        } else {
            const c14n_xml_prefix *prefix = c14n_xml_prefix_find(scope, name, prefix_len);
            if (prefix != NULL && prefix->binding >= 0) {
                attr.uri = scope->bindings[prefix->binding].ns.uri;
                attr.uri_len = scope->bindings[prefix->binding].ns.uri_len;
            }
        }
    }
    return attr;
}

static Py_UCS4 c14n_xml_uri_char(const c14n_xml_attr *attr, Py_ssize_t index) {
    return attr->uri != NULL ? attr->uri[index] : (unsigned char)attr->ascii_uri[index];
}

static int c14n_xml_attr_cmp(const void *left, const void *right) {
    const c14n_xml_attr *first = left;
    const c14n_xml_attr *second = right;
    Py_ssize_t common = first->uri_len < second->uri_len ? first->uri_len : second->uri_len;
    for (Py_ssize_t index = 0; index < common; index++) {
        Py_UCS4 left_char = c14n_xml_uri_char(first, index);
        Py_UCS4 right_char = c14n_xml_uri_char(second, index);
        if (left_char != right_char) {
            return left_char < right_char ? -1 : 1;
        }
    }
    if (first->uri_len != second->uri_len) {
        return first->uri_len < second->uri_len ? -1 : 1;
    }
    common = first->local_len < second->local_len ? first->local_len : second->local_len;
    int order = memcmp(first->local, second->local, (size_t)common);
    return order != 0 ? order : (first->local_len > second->local_len) - (first->local_len < second->local_len);
}

static int c14n_xml_already_has(const c14n_xml_attr *attrs, Py_ssize_t count, const char *name, Py_ssize_t name_len) {
    for (Py_ssize_t index = 0; index < count; index++) {
        if (attrs[index].name_len == name_len && memcmp(attrs[index].name, name, (size_t)name_len) == 0) {
            return 1;
        }
    }
    return 0;
}

static void c14n_xml_open_tag(sbuf *out, th_tree *tree, th_node *node, const th_node *apex, const th_c14n_opts *opts,
                              c14n_xml_scope *scope) {
    sbuf_putc(out, '<');
    sbuf_put_ucs4(out, node->text, node->text_len);

    Py_ssize_t own_bindings = scope->bindings_len;
    Py_ssize_t own_uses = scope->uses_len;
    int entered = c14n_xml_scope_declare(scope, tree, node, 0);
    if (opts->exclusive && entered == 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        entered = c14n_xml_scope_uses(scope, tree, node);
    }
    if (entered < 0) {   /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        out->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
        return;          /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t namespace_capacity =
        node == apex ? scope->prefixes_len : scope->bindings_len - own_bindings + scope->uses_len - own_uses;
    c14n_xml_ns stack_namespaces[C14N_STACK_ATTRS];
    c14n_xml_ns *namespaces = namespace_capacity <= C14N_STACK_ATTRS
                                  ? stack_namespaces
                                  : PyMem_Malloc((size_t)namespace_capacity * sizeof(c14n_xml_ns));
    if (namespaces == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        out->failed = 1;      /* GCOVR_EXCL_LINE: allocation-failure path */
        return;               /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t ns_count = c14n_xml_ns_collect(scope, node == apex, own_bindings, own_uses, namespaces);
    if (ns_count < 0) {
        if (namespaces != stack_namespaces) {
            PyMem_Free(namespaces);
        }
        out->failed = 1;
        return;
    }
    if (ns_count > 1) {
        qsort(namespaces, (size_t)ns_count, sizeof(c14n_xml_ns), c14n_xml_ns_cmp);
    }
    for (Py_ssize_t index = 0; index < ns_count; index++) {
        sbuf_puts(out, " xmlns");
        if (namespaces[index].prefix_len > 0) {
            sbuf_putc(out, ':');
            sbuf_put_utf8(out, namespaces[index].prefix, namespaces[index].prefix_len);
        }
        sbuf_puts(out, "=\"");
        c14n_put_attr(out, namespaces[index].uri, namespaces[index].uri_len);
        sbuf_putc(out, '"');
    }

    Py_ssize_t attr_capacity = node->attr_count;
    if (node == apex && !opts->exclusive) {
        for (const th_node *ancestor = node->parent; ancestor != NULL; ancestor = ancestor->parent) {
            if (ancestor->type == TH_NODE_ELEMENT) {
                attr_capacity += ancestor->attr_count;
            }
        }
    }
    c14n_xml_attr stack_attrs[C14N_STACK_ATTRS];
    c14n_xml_attr *attrs =
        attr_capacity <= C14N_STACK_ATTRS ? stack_attrs : PyMem_Malloc((size_t)attr_capacity * sizeof(c14n_xml_attr));
    if (attrs == NULL) {                      /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        if (namespaces != stack_namespaces) { /* GCOVR_EXCL_LINE: allocation failure cannot be forced */
            PyMem_Free(namespaces);           /* GCOVR_EXCL_LINE: allocation-failure path */
        } /* GCOVR_EXCL_LINE: allocation-failure path */
        out->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
        return;          /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t count = 0;
    int needs_base_fixup = 0;
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        Py_ssize_t name_len;
        const char *name = th_attr_name(tree, node->attrs[index].name_atom, &name_len);
        if (!c14n_name_is_nsdecl(name, name_len)) {
            attrs[count++] =
                c14n_xml_make_attr(name, name_len, node->attrs[index].value, node->attrs[index].value_len, scope);
        }
    }
    if (node == apex && !opts->exclusive) {
        for (const th_node *ancestor = node->parent; ancestor != NULL; ancestor = ancestor->parent) {
            if (ancestor->type != TH_NODE_ELEMENT) {
                continue;
            }
            for (Py_ssize_t index = 0; index < ancestor->attr_count; index++) {
                Py_ssize_t name_len;
                const char *name = th_attr_name(tree, ancestor->attrs[index].name_atom, &name_len);
                needs_base_fixup |= name_len == 8 && memcmp(name, "xml:base", 8) == 0;
                if (name_len <= 4 || memcmp(name, "xml:", 4) != 0 ||
                    (opts->version == 1 && !c14n_xml_1_1_inherits_xml_attribute(name, name_len))) {
                    continue;
                }
                if (!c14n_xml_already_has(attrs, count, name, name_len)) {
                    attrs[count++] = c14n_xml_make_attr(name, name_len, ancestor->attrs[index].value,
                                                        ancestor->attrs[index].value_len, scope);
                }
            }
        }
    }
    Py_UCS4 *fixed_base = NULL;
    if (node == apex && opts->version == 1 && needs_base_fixup) {
        Py_ssize_t base_len;
        if (c14n_base_fixup(tree, node, &fixed_base, &base_len) < 0) {
            if (attrs != stack_attrs) {
                PyMem_Free(attrs);
            }
            if (namespaces != stack_namespaces) {
                PyMem_Free(namespaces);
            }
            out->failed = 1;
            return;
        }
        Py_ssize_t base_index = 0;
        while (attrs[base_index].name_len != 8 || memcmp(attrs[base_index].name, "xml:base", 8) != 0) {
            base_index++;
        }
        if (base_len == 0) {
            attrs[base_index] = attrs[--count];
        } else {
            attrs[base_index].value = fixed_base;
            attrs[base_index].value_len = base_len;
        }
    }
    if (count > 1) {
        qsort(attrs, (size_t)count, sizeof(c14n_xml_attr), c14n_xml_attr_cmp);
    }
    for (Py_ssize_t index = 0; index < count; index++) {
        sbuf_putc(out, ' ');
        sbuf_put_utf8(out, attrs[index].name, attrs[index].name_len);
        sbuf_puts(out, "=\"");
        c14n_put_attr(out, attrs[index].value, attrs[index].value_len);
        sbuf_putc(out, '"');
    }
    if (attrs != stack_attrs) {
        PyMem_Free(attrs);
    }
    if (fixed_base != NULL) {
        PyMem_Free(fixed_base);
    }
    if (namespaces != stack_namespaces) {
        PyMem_Free(namespaces);
    }
    sbuf_putc(out, '>');
}

#if defined(_MSC_VER)
#define C14N_NOINLINE __declspec(noinline)
#elif defined(__GNUC__) || defined(__clang__)
#define C14N_NOINLINE __attribute__((noinline))
#else
#define C14N_NOINLINE
#endif

static C14N_NOINLINE th_node *c14n_xml_step(sbuf *out, th_tree *tree, th_node *node, const th_node *apex,
                                            const th_c14n_opts *opts, c14n_xml_scope *scope) {
    th_node *descend = NULL;
    switch ((enum th_node_type)node->type) { /* GCOVR_EXCL_BR_LINE: node types are exhaustive */
    case TH_NODE_ELEMENT:
        c14n_xml_open_tag(out, tree, node, apex, opts, scope);
        if (node->first_child != NULL) {
            descend = node->first_child;
        } else {
            c14n_close_tag(out, node);
            c14n_xml_scope_close(scope, node);
        }
        break;
    case TH_NODE_TEXT:
    case TH_NODE_CDATA:
        if (need_text(tree, node) == NULL && node->text_len > 0) { /* GCOVR_EXCL_BR_LINE: lazy text OOM */
            out->failed = 1;                                       /* GCOVR_EXCL_LINE: allocation failure */
            return NULL;                                           /* GCOVR_EXCL_LINE: allocation failure */
        }
        c14n_put_text(out, node->text, node->text_len);
        break;
    case TH_NODE_COMMENT:
        if (opts->with_comments) {
            sbuf_puts(out, "<!--");
            sbuf_put_xml_comment(out, node->text, node->text_len);
            sbuf_puts(out, "-->");
        }
        break;
    case TH_NODE_PI:
        c14n_put_pi(out, node);
        break;
    case TH_NODE_DOCTYPE:
        break;
    case TH_NODE_CONTENT:            /* GCOVR_EXCL_LINE: parsed XML does not create content nodes */
    case TH_NODE_DOCUMENT:           /* GCOVR_EXCL_LINE: documents use c14n_xml_document */
        descend = node->first_child; /* GCOVR_EXCL_LINE: unreachable from parsed XML subtree */
        break;                       /* GCOVR_EXCL_LINE: unreachable from parsed XML subtree */
    }
    if (descend != NULL) {
        return descend;
    }
    while (node != apex) {
        if (node->next_sibling != NULL) {
            return node->next_sibling;
        }
        node = node->parent;
        if (node->type == TH_NODE_ELEMENT) { /* GCOVR_EXCL_BR_LINE: parsed XML subtree parents are elements */
            c14n_close_tag(out, node);
            c14n_xml_scope_close(scope, node);
        }
    }
    return NULL;
}

static void c14n_xml_subtree(sbuf *out, th_tree *tree, th_node *apex, const th_c14n_opts *opts) {
    c14n_xml_scope scope;
    memset(&scope, 0, sizeof(scope));
    scope.opts = opts;
    /* Salt the prefix hash with Python's randomized bytes hash, as the XML parser salts its own tables,
       so chosen prefixes cannot be precomputed to collide into one slot and restore the scans. */
    PyObject *salt = PyBytes_FromStringAndSize("turbohtml:c14n-prefix", 21);
    if (salt == NULL) {  /* GCOVR_EXCL_BR_LINE: bytes allocation cannot be forced from a test */
        out->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
        return;          /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    scope.hash_seed = (uint64_t)PyObject_Hash(salt);
    Py_DECREF(salt);
    for (const th_node *ancestor = apex->parent; ancestor != NULL; ancestor = ancestor->parent) {
        if (ancestor->type != TH_NODE_ELEMENT) {
            continue;
        }
        if (c14n_xml_scope_declare(&scope, tree, ancestor, 1) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure only */
            out->failed = 1;                                         /* GCOVR_EXCL_LINE: allocation-failure path */
            break;                                                   /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    /* a failed element leaves its scope half entered, so the walk ends with it */
    th_node *node = apex;
    while (node != NULL && !out->failed) {
        node = c14n_xml_step(out, tree, node, apex, opts, &scope);
    }
    c14n_xml_scope_free(&scope);
}

static void c14n_xml_document(sbuf *out, th_tree *tree, th_node *doc, const th_c14n_opts *opts) {
    int seen_root = 0;
    for (th_node *child = doc->first_child; child != NULL; child = child->next_sibling) {
        if (child->type == TH_NODE_ELEMENT) {
            c14n_xml_subtree(out, tree, child, opts);
            seen_root = 1;
        } else if (child->type == TH_NODE_COMMENT && opts->with_comments) {
            if (seen_root) {
                sbuf_putc(out, '\n');
            }
            sbuf_puts(out, "<!--");
            sbuf_put_xml_comment(out, child->text, child->text_len);
            sbuf_puts(out, "-->");
            if (!seen_root) {
                sbuf_putc(out, '\n');
            }
        } else if (child->type == TH_NODE_PI) {
            if (seen_root) {
                sbuf_putc(out, '\n');
            }
            c14n_put_pi(out, child);
            if (!seen_root) {
                sbuf_putc(out, '\n');
            }
        }
    }
}

Py_UCS4 *th_node_canonicalize_xml(th_tree *tree, th_node *node, const th_c14n_opts *opts, Py_ssize_t *out_len) {
    sbuf out = {NULL, 0, 0, 0};
    if (node->type == TH_NODE_DOCUMENT) {
        sbuf_presize_for_c14n(&out, tree);
        c14n_xml_document(&out, tree, node, opts);
    } else {
        c14n_xml_subtree(&out, tree, node, opts);
    }
    return sbuf_finish(&out, out_len);
}
