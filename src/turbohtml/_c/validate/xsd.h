/* XSD 1.0 structural validation over the parse_xml instance tree, interpreting the
   compiled schema's symbol table and the schema tree's particles directly. Content
   models (sequence / choice / all with minOccurs / maxOccurs, group and element refs)
   match with an NFA-style reachable-position set so repetition needs no backtracking;
   simple types resolve to a built-in datatype plus the constraining facets gathered
   from the restriction chain. Included once into validate/schema.c; every definition
   is static. */

#ifndef TURBOHTML_VALIDATE_XSD_H
#define TURBOHTML_VALIDATE_XSD_H

#ifndef TH_NOINLINE
#if defined(_MSC_VER)
#define TH_NOINLINE __declspec(noinline)
#elif defined(__GNUC__) || defined(__clang__)
#define TH_NOINLINE __attribute__((noinline))
#else
#define TH_NOINLINE
#endif
#endif

#if defined(_MSC_VER)
#define XSD_FORCEINLINE __forceinline
#else
#define XSD_FORCEINLINE inline __attribute__((always_inline))
#endif

/* An expected element name plus the declaration that supplies its type. */
typedef struct edecl {
    const Py_UCS4 *uri;
    Py_ssize_t uri_len;
    const Py_UCS4 *local;
    Py_ssize_t local_len;
    th_node *decl;
} edecl;

typedef struct {
    edecl *items;
    Py_ssize_t count, cap;
} edecl_vec;

/* Whether `node` is an XSD element whose local name is one of the listed particles. */
static int xsd_is_particle(const th_schema *schema, th_node *node, const char *const *locals, size_t count) {
    for (size_t index = 0; index < count; index++) {
        if (is_schema_el(schema, node, XSD_NS, locals[index])) {
            return 1;
        }
    }
    return 0;
}

static const char *const XSD_MODEL_GROUPS[] = {"sequence", "choice", "all"};
static const char *const XSD_PARTICLES[] = {"element", "sequence", "choice", "all", "group"};

/* Validation reads every property of a declaration (ref, name, type, use, minOccurs, ...) from the attributes of a
   schema element, again for each instance element that meets the declaration. A schema element may carry any number
   of attributes: namespace declarations, annotations from other namespaces, names XSD gives no meaning. Scanning them
   made each read cost the length of the list, so xsd_compile sorts every list this long by name, once, and
   xsd_attr_find binary-searches it. A shorter list is scanned, in the order it was written. */
#define XSD_MIN_SORTED_ATTRS 16

typedef struct {
    const char *name;
    Py_ssize_t name_len;
    th_node_attr attr;
} xattr_key;

/* Names are compared bytewise and shorter-first on a tie, the order memcmp gives UTF-8 names; they need not be
   NUL-terminated. */
static int xsd_name_order(const char *left, Py_ssize_t left_len, const char *right, Py_ssize_t right_len) {
    const int order = memcmp(left, right, (size_t)(left_len < right_len ? left_len : right_len));
    return order != 0 ? order : (left_len > right_len) - (left_len < right_len);
}

static int xsd_attr_key_order(const void *left, const void *right) {
    const xattr_key *a = left, *b = right;
    return xsd_name_order(a->name, a->name_len, b->name, b->name_len);
}

/* qsort hands its comparator no tree to read a name atom with, so each list is sorted through the names copied into
   xattr_key. The schema tree is private to the schema and XML allows a name once per element, so the order of a list
   carries no meaning; this runs before anything keeps a pointer into one. */
static void xsd_sort_attrs(th_schema *schema) {
    for (th_node *node = schema->root; node != NULL; node = preorder_next(node, schema->root)) {
        if (node->type != TH_NODE_ELEMENT || node->attr_count < XSD_MIN_SORTED_ATTRS) {
            continue;
        }
        xattr_key *keys = arena_alloc(&schema->mem, (size_t)node->attr_count * sizeof(xattr_key));
        if (keys == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return;         /* GCOVR_EXCL_LINE */
        }
        for (Py_ssize_t index = 0; index < node->attr_count; index++) {
            keys[index].attr = node->attrs[index];
            keys[index].name = th_attr_name(schema->tree, node->attrs[index].name_atom, &keys[index].name_len);
        }
        qsort(keys, (size_t)node->attr_count, sizeof(xattr_key), xsd_attr_key_order);
        for (Py_ssize_t index = 0; index < node->attr_count; index++) {
            node->attrs[index] = keys[index].attr;
        }
    }
}

/* The attribute called `name` in a list xsd_sort_attrs has sorted, or NULL. */
static TH_NOINLINE const th_node_attr *xsd_attr_find(th_tree *tree, const th_node *node, const char *name,
                                                     Py_ssize_t name_len) {
    Py_ssize_t low = 0, high = node->attr_count;
    while (low < high) {
        const Py_ssize_t middle = low + (high - low) / 2;
        Py_ssize_t len = 0;
        const char *bytes = th_attr_name(tree, node->attrs[middle].name_atom, &len);
        const int order = xsd_name_order(bytes, len, name, name_len);
        if (order == 0) {
            return &node->attrs[middle];
        }
        if (order < 0) {
            low = middle + 1;
        } else {
            high = middle;
        }
    }
    return NULL;
}

/* inline, so that the scan of a short list stays specialized for the literal name of each call */
static inline const Py_UCS4 *xsd_attr(th_tree *tree, th_node *node, const char *name, Py_ssize_t *out_len) {
    const Py_ssize_t name_len = (Py_ssize_t)strlen(name);
    const th_node_attr *attr = node->attr_count < XSD_MIN_SORTED_ATTRS ? attr_exact(tree, node, name, name_len)
                                                                       : xsd_attr_find(tree, node, name, name_len);
    if (attr == NULL) {
        return NULL;
    }
    *out_len = attr->value_len;
    return attr->value;
}

/* Whether `node` carries attribute `name` with exactly `value`. */
static int xsd_attr_is(th_tree *tree, th_node *node, const char *name, const char *value) {
    Py_ssize_t len = 0;
    const Py_UCS4 *attr = xsd_attr(tree, node, name, &len);
    if (attr == NULL) {
        return 0;
    }
    return u_eq_ascii(attr, len, value);
}

/* Whether a resolved namespace URI is the XSD namespace. */
static int is_xsd_uri(const Py_UCS4 *uri, Py_ssize_t uri_len) {
    if (uri == NULL) {
        return 0;
    }
    return u_eq_ascii(uri, uri_len, XSD_NS);
}

static int xsd_occurs_min(th_tree *tree, th_node *particle) {
    Py_ssize_t len = 0;
    const Py_UCS4 *value = xsd_attr(tree, particle, "minOccurs", &len);
    if (value == NULL) {
        return 1;
    }
    int total = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        total = total * 10 + (int)(value[index] - '0');
    }
    return total;
}

/* -1 means unbounded. */
static int xsd_occurs_max(th_tree *tree, th_node *particle) {
    Py_ssize_t len = 0;
    const Py_UCS4 *value = xsd_attr(tree, particle, "maxOccurs", &len);
    if (value == NULL) {
        return 1;
    }
    if (u_eq_ascii(value, len, "unbounded")) {
        return -1;
    }
    int total = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        total = total * 10 + (int)(value[index] - '0');
    }
    return total;
}

/* The (uri, local) an element particle expects, plus the declaration carrying its type
   (the referenced global element for a ref, else the particle itself). */
static void xsd_element_resolve(th_schema *schema, th_node *particle, edecl *out) {
    th_tree *tree = schema->tree;
    Py_ssize_t ref_len = 0;
    const Py_UCS4 *ref = xsd_attr(tree, particle, "ref", &ref_len);
    if (ref != NULL) {
        const Py_UCS4 *local, *prefix;
        Py_ssize_t local_len = 0, prefix_len = 0;
        split_prefix(ref, ref_len, &local, &local_len, &prefix, &prefix_len);
        out->local = local;
        out->local_len = local_len;
        out->uri = schema->target_ns;
        out->uri_len = schema->target_ns_len;
        /* xsd_compile has already rejected an element ref that resolves to nothing */
        out->decl = named_find(&schema->elements, local, local_len);
        return;
    }
    Py_ssize_t name_len = 0;
    const Py_UCS4 *name = xsd_attr(tree, particle, "name", &name_len);
    out->local = name;
    out->local_len = name_len;
    out->decl = particle;
    /* an element particle always has a parent (its model group or the schema) */
    int global = is_schema_el(schema, particle->parent, XSD_NS, "schema");
    if (global || schema->element_qualified) {
        out->uri = schema->target_ns;
        out->uri_len = schema->target_ns_len;
    } else {
        out->uri = NULL;
        out->uri_len = 0;
    }
}

static int qname_eq(const qname *name, const edecl *want) {
    if (!u_eq_u(name->local, name->local_len, want->local, want->local_len)) {
        return 0;
    }
    return u_eq_u(name->uri, name->uri_len, want->uri, want->uri_len);
}

/* The children validation asks a schema element for by name. xsd_cache_facets enters each one under its parent, so a
   lookup no longer scans the parent's children, which cost every instance element that looked as many steps as the
   schema had children. */
enum {
    XSD_COMPLEX_TYPE,
    XSD_SIMPLE_TYPE,
    XSD_COMPLEX_CONTENT,
    XSD_SIMPLE_CONTENT,
    XSD_EXTENSION,
    XSD_RESTRICTION,
    XSD_SEQUENCE, /* the content models, in the order xsd_content_model prefers them */
    XSD_CHOICE,
    XSD_ALL,
    XSD_GROUP,
    XSD_ELEMENT,
    XSD_ANY,
    XSD_ATTRIBUTE,
    XSD_ATTRIBUTE_GROUP,
    XSD_ANY_ATTRIBUTE,
    XSD_CHILD_KINDS
};

static const char *const XSD_CHILD_NAMES[XSD_CHILD_KINDS] = {
    "complexType", "simpleType", "complexContent", "simpleContent", "extension", "restriction", "sequence",
    "choice",      "all",        "group",          "element",       "any",       "attribute",   "attributeGroup",
    "anyAttribute"};

#define XSD_NO_ENTRY SIZE_MAX

/* A child of a schema element that validation reads, with the kind it was classified under. */
typedef struct {
    th_node *node;
    int kind;
    const struct xnode *own; /* the child's xnodes record */
} xchild;

typedef struct xchild_entry {
    th_node *first[XSD_CHILD_KINDS]; /* the first child of each name */
    th_node *model_group;            /* the first sequence, choice or all child, whichever leads */
    xchild *particles;               /* the element, model group, group and any children, in document order */
    size_t particle_count, particle_cap;
    xchild *attrs; /* every child xsd_collect_attrs reads, in document order */
    size_t attr_count, attr_cap;
} xchild_entry;

/* A schema element as xsd_cache_facets classified it on its visit, so its children read the kind instead of asking
   again. */
typedef struct xnode {
    int kind;         /* its XSD_CHILD_NAMES kind, or XSD_CHILD_KINDS */
    size_t children;  /* its child entry, or XSD_NO_ENTRY */
    struct xnode *up; /* its parent's record, NULL for the root */
    /* An xs:element or xs:attribute as validation reads it on every instance node, resolved once xsd_compile has
       checked the schema (see xsd_resolve_decls): the expected name and the declaration a ref names (else the node
       itself), and for an attribute its own use. want.decl stays NULL until then. */
    edecl want;
    uint64_t hash; /* named_hash of want.local, for an attribute */
    int use;
} xnode;

enum { XSD_USE_OPTIONAL, XSD_USE_REQUIRED, XSD_USE_PROHIBITED };

/* The xnodes record of a schema element, whose qname schema_node_qname returned as `name`: that qname sits in the
   element's sqnames entry, and xnodes shares the sqnames index. */
static xnode *xsd_xnode(const th_schema *schema, const qname *name) {
    const sqname_entry *at = (const sqname_entry *)((const char *)name - offsetof(sqname_entry, name));
    return &schema->xnodes[at - schema->sqnames];
}

/* xsd_element_resolve's answer, read from the particle's record once xsd_resolve_decls has filled it. */
static void xsd_element_name(th_schema *schema, th_node *particle, edecl *out) {
    if (schema->decls_resolved) {
        *out = xsd_xnode(schema, schema_node_qname(schema, particle))->want;
        return;
    }
    xsd_element_resolve(schema, particle, out);
}

/* The entry of a schema element; a node with none of these children has no entry and reads as the empty one. Only
   validation may ask, because xsd_cache_facets fills the entries as it walks. */
static const xchild_entry *xsd_child_entry(const th_schema *schema, th_node *node) {
    static const xchild_entry childless = {0};
    const size_t children = xsd_xnode(schema, schema_node_qname(schema, node))->children;
    return children == XSD_NO_ENTRY ? &childless : &schema->child_entries[children];
}

/* The first XSD child of `node` of the kind XSD_CHILD_NAMES names, or NULL. */
static th_node *xsd_child(const th_schema *schema, th_node *node, int kind) {
    return xsd_child_entry(schema, node)->first[kind];
}

/* The named group a group ref particle resolves to, or NULL for a group without ref, which has no model group. */
static th_node *xsd_group_target(th_schema *schema, th_node *group_ref) {
    Py_ssize_t ref_len = 0;
    const Py_UCS4 *ref = xsd_attr(schema->tree, group_ref, "ref", &ref_len);
    if (ref == NULL) { /* compile accepts a group without ref, which has no model group to resolve */
        return NULL;
    }
    const Py_UCS4 *local, *prefix;
    Py_ssize_t local_len = 0, prefix_len = 0;
    split_prefix(ref, ref_len, &local, &local_len, &prefix, &prefix_len);
    /* xsd_compile has already rejected a group ref that resolves to nothing */
    return named_find(&schema->groups, local, local_len);
}

/* The model group of a group ref particle, found by scanning the group's children. The compile walks ask before the
   child entries are sorted, so they cannot use xsd_group_model. */
static th_node *xsd_scan_group_model(th_schema *schema, th_node *group_ref) {
    th_node *group = xsd_group_target(schema, group_ref);
    if (group == NULL) {
        return NULL;
    }
    for (th_node *child = group->first_child; child != NULL; child = child->next_sibling) {
        if (xsd_is_particle(schema, child, XSD_MODEL_GROUPS, 3)) {
            return child;
        }
    } /* GCOVR_EXCL_LINE: llvm miscredits this loop-exit brace when the group has no model group */
    return NULL;
}

/* Resolve a group ref particle to the group's model-group child (sequence/choice/all). */
static th_node *xsd_group_model(th_schema *schema, th_node *group_ref) {
    return xsd_child_entry(schema, xsd_group_target(schema, group_ref))->model_group;
}

/* The xs:all a group ref resolves to, or NULL when its model group is not an xs:all. */
static th_node *xsd_group_all(th_schema *schema, th_node *group_ref) {
    th_node *model = xsd_group_model(schema, group_ref);
    return model != NULL && is_schema_el(schema, model, XSD_NS, "all") ? model : NULL;
}

/* The one list of a validate() call that each element refills with its attribute and element declarations: a list per
   instance would stay in the arena, which frees nothing until validation ends. */
static edecl_vec edecl_list(const th_schema *schema) {
    return (edecl_vec){schema->edecl_items, 0, schema->edecl_cap};
}

/* Make room in vec for `need` items, at least doubling, so a caller that knows how many it adds grows it once. */
static int edecl_reserve(th_schema *schema, edecl_vec *vec, Py_ssize_t need) {
    if (need > vec->cap) {
        Py_ssize_t next = vec->cap ? vec->cap * 2 : TH_INITIAL_CAPACITY(8);
        if (next < need) {
            next = need;
        }
        edecl *grown = arena_alloc(&schema->mem, (size_t)next * sizeof(edecl));
        if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return -1;       /* GCOVR_EXCL_LINE */
        }
        if (vec->count > 0) {
            memcpy(grown, vec->items, (size_t)vec->count * sizeof(edecl));
        }
        vec->items = grown;
        vec->cap = next;
        schema->edecl_items = grown;
        schema->edecl_cap = next;
    }
    return 0;
}

static int edecl_push(th_schema *schema, edecl_vec *vec, const edecl *item) {
    if (edecl_reserve(schema, vec, vec->count + 1) < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return -1;                                        /* GCOVR_EXCL_LINE */
    }
    vec->items[vec->count++] = *item;
    return 0;
}

/* Gather every element declaration reachable in a particle so a child can find its
   type by name (unique-particle-attribution makes the name→decl mapping unambiguous). */
static void xsd_collect_edecls(th_schema *schema, th_node *particle, edecl_vec *vec) {
    if (schema_stack_low(schema)) {
        return;
    }
    if (is_schema_el(schema, particle, XSD_NS, "element")) {
        edecl want;
        xsd_element_name(schema, particle, &want);
        edecl_push(schema, vec, &want);
        return;
    }
    if (is_schema_el(schema, particle, XSD_NS, "group")) {
        th_node *model = xsd_group_model(schema, particle);
        if (model != NULL) {
            xsd_collect_edecls(schema, model, vec);
        }
        return;
    }
    const xchild_entry *members = xsd_child_entry(schema, particle);
    for (size_t index = 0; index < members->particle_count; index++) {
        if (members->particles[index].kind != XSD_ANY) {
            xsd_collect_edecls(schema, members->particles[index].node, vec);
        }
    }
}

/* ---- content-model position-set matcher ---- */

/* The child positions a particle can stop at. items lists each position once and member flags it, so making, clearing
   and combining a set costs the positions it holds and not the element's child count. */
typedef struct {
    char *member;
    Py_ssize_t *items;
    Py_ssize_t count;
    Py_ssize_t size;            /* nchildren + 1 */
    struct posset_spare *block; /* the allocation holding items and member, handed back by posset_free */
} posset;

/* The bytes of a position set no matcher holds any more, kept for the next one to take. */
typedef struct posset_spare {
    struct posset_spare *next;
} posset_spare;

static int posset_empty(const posset *set) {
    return set->count == 0;
}

/* Returns whether the set gained the position. */
static int posset_add(posset *set, Py_ssize_t index) {
    if (set->member[index]) {
        return 0;
    }
    set->member[index] = 1;
    set->items[set->count++] = index;
    return 1;
}

/* Returns whether `into` gained a position. */
static int posset_or(posset *into, const posset *from) {
    int gained = 0;
    for (Py_ssize_t index = 0; index < from->count; index++) {
        gained |= posset_add(into, from->items[index]);
    }
    return gained;
}

static void posset_clear(posset *set) {
    for (Py_ssize_t index = 0; index < set->count; index++) {
        set->member[set->items[index]] = 0;
    }
    set->count = 0;
}

static void posset_copy(posset *into, const posset *from) {
    posset_clear(into);
    posset_or(into, from);
}

static int posset_same(const posset *left, const posset *right) {
    if (left->count != right->count) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < left->count; index++) {
        if (!right->member[left->items[index]]) {
            return 0;
        }
    }
    return 1;
}

/* An empty set. The matcher takes its working sets here and hands them back with posset_free, so matching one element
   holds a few sets per level of its model however often a particle repeats; the arena, which releases nothing until
   validation ends, would otherwise keep one per repetition. An element with another child count starts a new list. */
static posset posset_new(th_schema *schema, Py_ssize_t size) {
    size_t items_bytes = (size_t)size * sizeof(Py_ssize_t);
    posset_spare *spare = arena_alloc(&schema->mem, sizeof(posset_spare) + items_bytes + (size_t)size);
    if (spare == NULL) {                            /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return (posset){NULL, NULL, 0, size, NULL}; /* GCOVR_EXCL_LINE */
    }
    memset((char *)(spare + 1) + items_bytes, 0, (size_t)size);
    Py_ssize_t *items = (Py_ssize_t *)(spare + 1);
    return (posset){(char *)(items + size), items, 0, size, spare};
}

static posset posset_alloc(th_schema *schema, Py_ssize_t size) {
    if (schema->posset_size != size) {
        schema->posset_spares = NULL;
        schema->posset_size = size;
    }
    posset_spare *spare = schema->posset_spares;
    if (spare == NULL) {
        return posset_new(schema, size);
    }
    schema->posset_spares = spare->next;
    Py_ssize_t *items = (Py_ssize_t *)(spare + 1);
    return (posset){(char *)(items + size), items, 0, size, spare};
}

/* Clears the set, so the block the next matcher takes starts with no position flagged. */
static void posset_free(th_schema *schema, posset *set) {
    posset_clear(set);
    set->block->next = schema->posset_spares;
    schema->posset_spares = set->block;
}

static void xsd_match_once(th_schema *schema, th_tree *inst, th_node *particle, th_node **children, const posset *in,
                           posset *out);

enum { WILD_STRICT, WILD_LAX, WILD_SKIP };

static int xsd_process_contents(th_schema *schema, th_node *wild) {
    Py_ssize_t len = 0;
    const Py_UCS4 *value = xsd_attr(schema->tree, wild, "processContents", &len);
    if (value != NULL && u_eq_ascii(value, len, "skip")) {
        return WILD_SKIP;
    }
    return value != NULL && u_eq_ascii(value, len, "lax") ? WILD_LAX : WILD_STRICT;
}

static int xsd_namespace_equal(const Py_UCS4 *left, Py_ssize_t left_len, const Py_UCS4 *right, Py_ssize_t right_len) {
    return left_len == right_len && (left_len == 0 || memcmp(left, right, (size_t)left_len * sizeof(Py_UCS4)) == 0);
}

static int xsd_token_admits(const th_schema *schema, const Py_UCS4 *token, Py_ssize_t len, const Py_UCS4 *uri,
                            Py_ssize_t uri_len) {
    const int in_target = xsd_namespace_equal(uri, uri_len, schema->target_ns, schema->target_ns_len);
    if (u_eq_ascii(token, len, "##any")) {
        return 1;
    }
    if (u_eq_ascii(token, len, "##other")) {
        return uri_len > 0 && !in_target;
    }
    if (u_eq_ascii(token, len, "##local")) {
        return uri_len == 0;
    }
    if (u_eq_ascii(token, len, "##targetNamespace")) {
        return in_target;
    }
    return xsd_namespace_equal(uri, uri_len, token, len);
}

/* Whether a wildcard's namespace attribute admits a name in `uri`; the absent namespace has a length of 0. */
static int xsd_wild_admits(const th_schema *schema, th_node *wild, const Py_UCS4 *uri, Py_ssize_t uri_len) {
    Py_ssize_t spec_len = 0;
    const Py_UCS4 *spec = xsd_attr(schema->tree, wild, "namespace", &spec_len);
    if (spec == NULL) {
        return 1;
    }
    Py_ssize_t at = 0;
    for (;;) {
        while (at < spec_len && is_xml_space(spec[at])) {
            at++;
        }
        if (at == spec_len) {
            return 0;
        }
        const Py_ssize_t start = at;
        while (at < spec_len && !is_xml_space(spec[at])) {
            at++;
        }
        if (xsd_token_admits(schema, spec + start, at - start, uri, uri_len)) {
            return 1;
        }
    }
}

/* The claims of one complex element's children: the wildcard that consumed each child in the match. */
static th_node **xsd_wild_claims(th_schema *schema, size_t count) {
    if (count > schema->wild_claim_cap) {
        size_t capacity, bytes;
        const int fits = th_grow_cap(count, schema->wild_claim_cap, 8, sizeof(th_node *), &capacity, &bytes);
        if (!fits) {     /* GCOVR_EXCL_BR_LINE: allocation size overflow */
            return NULL; /* GCOVR_EXCL_LINE */
        }
        th_node **grown = arena_alloc(&schema->mem, bytes);
        if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return NULL;     /* GCOVR_EXCL_LINE */
        }
        schema->wild_claims = grown;
        schema->wild_claim_cap = capacity;
    }
    memset(schema->wild_claims, 0, count * sizeof(th_node *));
    return schema->wild_claims;
}

/* Applies the term minOccurs times, leaving the positions after the last repetition in cur. An empty repetition still
   counts, so the count stops early only once a repetition leaves the set unchanged: every later one would too. */
static int xsd_match_min_repeats(th_schema *schema, th_tree *inst, th_node *particle, th_node **children, int min,
                                 posset *cur, posset *next) {
    for (int count = 0; count < min; count++) {
        xsd_match_once(schema, inst, particle, children, cur, next);
        if (posset_empty(next)) {
            return 0;
        }
        if (posset_same(cur, next)) {
            return 1;
        }
        posset spare = *cur;
        *cur = *next;
        *next = spare;
    }
    return 1;
}

/* A bounded particle takes minOccurs repetitions, then steps one repetition at a time up to maxOccurs, stopping once a
   repetition reaches no position the result lacks: every later one then reaches only positions it already holds. */
static void xsd_match_bounded(th_schema *schema, th_tree *inst, th_node *particle, th_node **children, const posset *in,
                              int min, int max, posset *out) {
    if (min > max) { /* the compiler keeps an inverted occurs range, and no child count satisfies it */
        return;
    }
    posset cur = posset_alloc(schema, in->size);
    posset next = posset_alloc(schema, in->size);
    if (!cur.member || !next.member) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return;                        /* GCOVR_EXCL_LINE */
    }
    posset_copy(&cur, in);
    if (xsd_match_min_repeats(schema, inst, particle, children, min, &cur, &next)) {
        posset_copy(out, &cur);
        for (int count = min; count < max; count++) {
            xsd_match_once(schema, inst, particle, children, &cur, &next);
            if (!posset_or(out, &next)) {
                break;
            }
            posset spare = cur;
            cur = next;
            next = spare;
        }
    }
    posset_free(schema, &next);
    posset_free(schema, &cur);
}

/* An unbounded particle reaches its minOccurs-th set of positions, then closes it under one more repetition. The
   closure steps only from the positions each round adds, so a position is stepped from once however many repetitions
   reach it. */
static void xsd_match_unbounded(th_schema *schema, th_tree *inst, th_node *particle, th_node **children,
                                const posset *in, int min, posset *out) {
    posset cur = posset_alloc(schema, in->size);
    posset next = posset_alloc(schema, in->size);
    if (!cur.member || !next.member) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return;                        /* GCOVR_EXCL_LINE */
    }
    posset_copy(&cur, in);
    if (xsd_match_min_repeats(schema, inst, particle, children, min, &cur, &next)) {
        posset_copy(out, &cur);
        while (!posset_empty(&cur)) {
            xsd_match_once(schema, inst, particle, children, &cur, &next);
            posset_clear(&cur);
            for (Py_ssize_t index = 0; index < next.count; index++) {
                if (posset_add(out, next.items[index])) {
                    posset_add(&cur, next.items[index]);
                }
            }
        }
    }
    posset_free(schema, &next);
    posset_free(schema, &cur);
}

/* Apply the particle's minOccurs/maxOccurs, accumulating every reachable position. */
static void xsd_match_occurs(th_schema *schema, th_tree *inst, th_node *particle, th_node **children, const posset *in,
                             posset *out) {
    posset_clear(out);
    int min = xsd_occurs_min(schema->tree, particle);
    int max = xsd_occurs_max(schema->tree, particle);
    if (max < 0) {
        xsd_match_unbounded(schema, inst, particle, children, in, min, out);
    } else {
        xsd_match_bounded(schema, inst, particle, children, in, min, max, out);
    }
}

static void xsd_match_once(th_schema *schema, th_tree *inst, th_node *particle, th_node **children, const posset *in,
                           posset *out) {
    if (schema_stack_low(schema)) {
        return;
    }
    posset_clear(out);
    /* xsd_cache_facets classified the particle at compile time, so a visit reads its kind rather than comparing
       names */
    const int kind = xsd_xnode(schema, schema_node_qname(schema, particle))->kind;
    if (kind == XSD_ELEMENT) {
        edecl want;
        xsd_element_name(schema, particle, &want);
        for (Py_ssize_t index = 0; index < in->count; index++) {
            TH_WORK_STEP();
            Py_ssize_t pos = in->items[index];
            if (pos + 1 >= in->size) {
                continue;
            }
            if (qname_eq(&schema->child_names[pos], &want)) {
                posset_add(out, pos + 1);
            }
        }
        return;
    }
    if (kind == XSD_GROUP) {
        th_node *model = xsd_group_model(schema, particle);
        if (model != NULL) {
            xsd_match_once(schema, inst, model, children, in, out);
        }
        return;
    }
    if (kind == XSD_ANY) {
        for (Py_ssize_t index = 0; index < in->count; index++) {
            TH_WORK_STEP();
            Py_ssize_t pos = in->items[index];
            if (pos + 1 >= in->size) {
                continue;
            }
            const qname *name = &schema->child_names[pos];
            if (xsd_wild_admits(schema, particle, name->uri, name->uri_len)) {
                schema->wild_claims[pos] = particle;
                posset_add(out, pos + 1);
            }
        }
        return;
    }
    if (kind == XSD_CHOICE) {
        posset branch = posset_alloc(schema, in->size);
        if (branch.member == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return;                  /* GCOVR_EXCL_LINE */
        }
        const xchild_entry *members = xsd_child_entry(schema, particle);
        for (size_t index = 0; index < members->particle_count; index++) {
            xsd_match_occurs(schema, inst, members->particles[index].node, children, in, &branch);
            posset_or(out, &branch);
        }
        posset_free(schema, &branch);
        return;
    }
    /* sequence: thread the position set through each child particle in order */
    posset cur = posset_alloc(schema, in->size);
    posset next = posset_alloc(schema, in->size);
    if (cur.member == NULL || next.member == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return;                                      /* GCOVR_EXCL_LINE */
    }
    posset_copy(&cur, in);
    const xchild_entry *members = xsd_child_entry(schema, particle);
    for (size_t index = 0; index < members->particle_count; index++) {
        xsd_match_occurs(schema, inst, members->particles[index].node, children, &cur, &next);
        posset spare = cur;
        cur = next;
        next = spare;
    }
    posset_copy(out, &cur);
    posset_free(schema, &next);
    posset_free(schema, &cur);
}

/* One element particle of an xs:all group. */
typedef struct {
    edecl want;
    int required; /* set on the first member of a name only: whether a valid instance has to contain that name */
} xall_member;

/* The element names of an xs:all group. Validation looks every child up by name, and a walk of the group per child cost
   particles x children; a sorted table, unlike a hashed one, cannot be flooded by a choice of names. */
typedef struct xall_entry {
    th_node *node;
    xall_member *members;   /* every element particle, in document order */
    xall_member **by_name;  /* the first member of each name, the one a child matches, sorted by name */
    xall_member **required; /* those of by_name a valid instance contains, in document order */
    Py_ssize_t name_count, required_count;
} xall_entry;

static int xsd_text_order(const Py_UCS4 *left, Py_ssize_t left_len, const Py_UCS4 *right, Py_ssize_t right_len) {
    if (left_len != right_len) {
        return left_len < right_len ? -1 : 1;
    }
    return left_len == 0 ? 0 : memcmp(left, right, (size_t)left_len * sizeof(Py_UCS4));
}

/* A total order whose ties are exactly the names qname_eq calls equal. */
static int edecl_order(const edecl *left, const edecl *right) {
    int order = xsd_text_order(left->local, left->local_len, right->local, right->local_len);
    return order != 0 ? order : xsd_text_order(left->uri, left->uri_len, right->uri, right->uri_len);
}

static int xsd_member_order(const void *left, const void *right) {
    const xall_member *left_member = *(const xall_member *const *)left;
    const xall_member *right_member = *(const xall_member *const *)right;
    return (left_member > right_member) - (left_member < right_member);
}

/* Members of one name sort in document order, so the first of them leads its run. */
static int xsd_member_name_order(const void *left, const void *right) {
    const xall_member *left_member = *(const xall_member *const *)left;
    const xall_member *right_member = *(const xall_member *const *)right;
    int order = edecl_order(&left_member->want, &right_member->want);
    return order != 0 ? order : xsd_member_order(left, right);
}

static TH_NOINLINE int xsd_record_all(th_schema *schema, th_node *all) {
    if (schema->all_count == schema->all_cap) {
        size_t capacity, bytes;
        const int fits = th_grow_cap(schema->all_count + 1, schema->all_cap, 8, sizeof(xall_entry), &capacity, &bytes);
        if (!fits) {   /* GCOVR_EXCL_BR_LINE: allocation size overflow */
            return -1; /* GCOVR_EXCL_LINE */
        }
        xall_entry *entries = arena_alloc(&schema->mem, bytes);
        if (entries == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return -1;         /* GCOVR_EXCL_LINE */
        }
        if (schema->all_count > 0) {
            memcpy(entries, schema->all_entries, schema->all_count * sizeof(xall_entry));
        }
        schema->all_entries = entries;
        schema->all_cap = capacity;
    }
    xall_entry *group = &schema->all_entries[schema->all_count++];
    size_t count = 0;
    for (th_node *particle = all->first_child; particle != NULL; particle = particle->next_sibling) {
        count += (size_t)is_schema_el(schema, particle, XSD_NS, "element");
    }
    group->node = all;
    group->name_count = group->required_count = 0;
    group->members = arena_alloc(&schema->mem, (count + 1) * sizeof(xall_member));
    group->by_name = arena_alloc(&schema->mem, (count + 1) * sizeof(xall_member *));
    group->required = arena_alloc(&schema->mem, (count + 1) * sizeof(xall_member *));
    /* GCOVR_EXCL_BR_START: arena OOM is unforceable */
    if (group->members == NULL || group->by_name == NULL || group->required == NULL) {
        /* GCOVR_EXCL_BR_STOP */
        return -1; /* GCOVR_EXCL_LINE */
    }
    count = 0;
    for (th_node *particle = all->first_child; particle != NULL; particle = particle->next_sibling) {
        if (!is_schema_el(schema, particle, XSD_NS, "element") || xsd_occurs_max(schema->tree, particle) == 0) {
            continue;
        }
        xall_member *member = &group->members[count];
        xsd_element_name(schema, particle, &member->want);
        member->required = xsd_occurs_min(schema->tree, particle) != 0;
        group->by_name[count++] = member;
    }
    qsort(group->by_name, count, sizeof(xall_member *), xsd_member_name_order);
    /* a child matches the first declaration of its name, so a later one only adds its minOccurs to the first */
    for (size_t index = 0; index < count; index++) {
        xall_member *member = group->by_name[index];
        xall_member *first = index > 0 ? group->by_name[group->name_count - 1] : NULL;
        if (first != NULL && edecl_order(&first->want, &member->want) == 0) {
            first->required |= member->required;
            member->required = 0;
        } else {
            group->by_name[group->name_count++] = member;
        }
    }
    for (size_t index = 0; index < count; index++) {
        if (group->members[index].required) {
            group->required[group->required_count++] = &group->members[index];
        }
    }
    return 0;
}

static int xsd_all_order(const void *left, const void *right) {
    const uintptr_t left_node = (uintptr_t)((const xall_entry *)left)->node;
    const uintptr_t right_node = (uintptr_t)((const xall_entry *)right)->node;
    return (left_node > right_node) - (left_node < right_node);
}

/* xsd_cache_facets records every xs:all element of the schema, so the search always hits. */
static const xall_entry *xsd_cached_all(const th_schema *schema, const th_node *all) {
    size_t low = 0, high = schema->all_count;
    while (low < high) {
        const size_t middle = low + (high - low) / 2;
        if ((uintptr_t)schema->all_entries[middle].node < (uintptr_t)all) {
            low = middle + 1;
        } else {
            high = middle;
        }
    }
    return &schema->all_entries[low];
}

/* The member of the group that expects `name`, or NULL. */
static const xall_member *xsd_all_find(const xall_entry *group, const qname *name) {
    const edecl want = {name->uri, name->uri_len, name->local, name->local_len, NULL};
    Py_ssize_t low = 0, high = group->name_count;
    while (low < high) {
        const Py_ssize_t middle = low + (high - low) / 2;
        if (edecl_order(&group->by_name[middle]->want, &want) < 0) {
            low = middle + 1;
        } else {
            high = middle;
        }
    }
    if (low == group->name_count || !qname_eq(name, &group->by_name[low]->want)) {
        return NULL;
    }
    return group->by_name[low];
}

/* One child of the instance that names a member of an xs:all group, with the child's position among the children. */
typedef struct {
    const xall_member *member;
    Py_ssize_t child;
} xall_hit;

static int xsd_hit_order(const void *left, const void *right) {
    const xall_hit *left_hit = left;
    const xall_hit *right_hit = right;
    const int by_member = (left_hit->member > right_hit->member) - (left_hit->member < right_hit->member);
    return by_member != 0 ? by_member : (left_hit->child > right_hit->child) - (left_hit->child < right_hit->child);
}

static int xsd_hit_member_order(const void *left, const void *right) {
    const xall_member *left_member = ((const xall_hit *)left)->member;
    const xall_member *right_member = ((const xall_hit *)right)->member;
    return (left_member > right_member) - (left_member < right_member);
}

/* An element whose child elements are still to validate. xsd_validate_tree keeps these on an explicit stack
   (walk_stack_reserve) instead of recursing once per instance level. */
typedef struct {
    th_node **children;
    Py_ssize_t nchildren, next;
    th_node **decls;         /* the declaration typing each child, NULL for one the content model does not name */
    th_node *all;            /* the `all` group when that is the content model: it types the children itself */
    th_node *instance;       /* the element a missing member is reported on: schema nodes carry no source position */
    const xall_entry *group; /* that group's members by name */
    const xall_member **matched; /* the member each child is validated against, NULL for one naming none or a repeat */
    xall_hit *hits;              /* the children naming a member, sorted by member then position */
    size_t hit_count;
    Py_ssize_t present; /* the required weight of the members some child names */
    Py_ssize_t mark;    /* path length to restore once the element is done */
    Py_ssize_t ns_mark; /* namespace bindings to unbind once the element is done */
} xsd_frame;

/* Match each child of an `all` group to the member it names. Each member occurs at most once (XML Schema Part 1 limits
   an xs:all particle to maxOccurs 0 or 1), so a second child naming it matches nothing and is reported when its turn
   comes. */
static void xsd_all_match(valctx *ctx, xsd_frame *frame, th_node *all, th_node **children, Py_ssize_t nchildren) {
    th_schema *schema = ctx->schema;
    const xall_entry *group = xsd_cached_all(schema, all);
    const xall_member **matched = arena_alloc(&schema->mem, (size_t)(nchildren + 1) * sizeof(xall_member *));
    xall_hit *hits = arena_alloc(&schema->mem, (size_t)(nchildren + 1) * sizeof(xall_hit));
    if (matched == NULL || hits == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return;                            /* GCOVR_EXCL_LINE */
    }
    size_t hit_count = 0;
    for (Py_ssize_t index = 0; index < nchildren; index++) {
        TH_WORK_STEP();
        matched[index] = xsd_all_find(group, &schema->child_names[index]);
        if (matched[index] != NULL) {
            hits[hit_count++] = (xall_hit){matched[index], index};
        }
    }
    qsort(hits, hit_count, sizeof(xall_hit), xsd_hit_order);
    Py_ssize_t present = 0;
    for (size_t index = 0; index < hit_count; index++) {
        if (index > 0 && hits[index].member == hits[index - 1].member) {
            matched[hits[index].child] = NULL;
        } else {
            present += hits[index].member->required;
        }
    }
    frame->children = children;
    frame->nchildren = nchildren;
    frame->all = all;
    frame->group = group;
    frame->matched = matched;
    frame->hits = hits;
    frame->hit_count = hit_count;
    frame->present = present;
}

/* Report each required member of an `all` group that no child matched, once the group's children are done. */
static void xsd_all_missing(valctx *ctx, xsd_frame *frame) {
    const xall_entry *group = frame->group;
    if (frame->present == group->required_count) {
        return;
    }
    for (Py_ssize_t index = 0; index < group->required_count; index++) {
        TH_WORK_STEP();
        const xall_member *member = group->required[index];
        const xall_hit probe = {member, 0};
        if (bsearch(&probe, frame->hits, frame->hit_count, sizeof(xall_hit), xsd_hit_member_order) != NULL) {
            continue;
        }
        char buffer[256];
        report(ctx, frame->instance, "structure", "required element '%s' is missing",
               name_utf8(member->want.local, member->want.local_len, buffer, sizeof(buffer)));
    }
}

/* ---- simple types ---- */

/* How many named bases a simple type's facets are gathered through, counted from that type. It also ends a circular
   chain; past it the base reads as a plain string. */
#define XSD_MAX_BASE_DEPTH 40

static int xsd_builtin_id(const Py_UCS4 *local, Py_ssize_t local_len) {
    const int builtin = dt_lookup(local, local_len);
    return builtin == DT_UNKNOWN ? DT_STRING : builtin;
}

/* One simpleType or simpleContent. Each holds only what its own derivation states and links to the types it derives
   from, so a type with many facets is stored once however many types derive from it. */
enum { XSD_ATOMIC, XSD_LIST, XSD_UNION };

/* The item type of a list or one member type of a union: a simpleType of the schema, or else a built-in datatype. */
typedef struct {
    th_node *type;
    int builtin;
} xsd_member;

typedef struct xfacet_entry {
    th_node *node;
    int variety;         /* XSD_LIST or XSD_UNION when xs:list or xs:union defines this very simpleType */
    xsd_member *members; /* then its item type, or its member types in schema order */
    Py_ssize_t member_count, member_cap;
    const struct xfacet_entry *root; /* the list or union the chain of restrictions ends in; NULL for an atomic type */
    unsigned char root_state;        /* 0 unsettled, 1 on the walk that settles it, 2 settled */
    facetset *own; /* the derivation's own facets; NULL for a list or union, which reads as its base */
    /* the named type the base attribute resolves to, and the inline simpleType a derivation without a base, or a
       simpleContent restriction beside its base, narrows; xsd_link_facets resolves both to entries */
    th_node *base_node, *inner_node;
    struct xfacet_entry *base, *inner;
    int base_id;  /* what a base attribute gives when no named type is followed; -1 without a base attribute */
    int keeps_ws; /* an extension keeps the whiteSpace rule of its named base */
    /* the built-in type, whiteSpace rule and scalar facets the chain folds to, and the enumerations and patterns of
       the one derivation that states them, shared with it */
    facetset facets;
    facet_levels levels; /* enumerations or patterns stated on several derivations, in the order they were folded */
} xfacet_entry;

static int xsd_member_push(th_schema *schema, xfacet_entry *entry, th_node *type, int builtin) {
    if (entry->member_count == entry->member_cap) {
        const Py_ssize_t next = entry->member_cap ? entry->member_cap * 2 : 4;
        xsd_member *grown = arena_alloc(&schema->mem, (size_t)next * sizeof(xsd_member));
        if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return -1;       /* GCOVR_EXCL_LINE */
        }
        if (entry->member_count > 0) {
            memcpy(grown, entry->members, (size_t)entry->member_count * sizeof(xsd_member));
        }
        entry->members = grown;
        entry->member_cap = next;
    }
    entry->members[entry->member_count].type = type;
    entry->members[entry->member_count++].builtin = builtin;
    return 0;
}

/* The built-in datatype a QName names in an xs:list or xs:union, or else DT_STRING with *named set to the simpleType
   it names; a name that resolves to neither is left for xsd_require_members to reject. */
static int xsd_member_name(th_schema *schema, th_node *node, const Py_UCS4 *name, Py_ssize_t name_len,
                           th_node **named) {
    const Py_UCS4 *local, *prefix, *uri;
    Py_ssize_t local_len = 0, prefix_len = 0, uri_len = 0;
    split_prefix(name, name_len, &local, &local_len, &prefix, &prefix_len);
    resolve_ns(schema->tree_ns, node, prefix, prefix_len, &uri, &uri_len);
    if (is_xsd_uri(uri, uri_len)) {
        *named = NULL;
        return xsd_builtin_id(local, local_len);
    }
    *named = named_find(&schema->simple_types, local, local_len);
    return DT_STRING;
}

static Py_ssize_t xsd_name_end(const Py_UCS4 *names, Py_ssize_t len, Py_ssize_t at) {
    while (at < len && !is_xml_space(names[at])) {
        at++;
    }
    return at;
}

/* The item type of an xs:list or the member types of an xs:union: the names its attribute gives, then its simpleType
   children. */
static int xsd_gather_members(th_schema *schema, xfacet_entry *entry, th_node *derivation) {
    Py_ssize_t names_len = 0;
    const Py_UCS4 *names =
        xsd_attr(schema->tree, derivation, entry->variety == XSD_LIST ? "itemType" : "memberTypes", &names_len);
    for (Py_ssize_t at = 0; names != NULL && at < names_len; at++) {
        if (is_xml_space(names[at])) {
            continue;
        }
        const Py_ssize_t end = xsd_name_end(names, names_len, at);
        th_node *named;
        const int builtin = xsd_member_name(schema, derivation, names + at, end - at, &named);
        if (xsd_member_push(schema, entry, named, builtin) < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return -1;                                            /* GCOVR_EXCL_LINE */
        }
        at = end;
    }
    for (th_node *child = derivation->first_child; child != NULL; child = child->next_sibling) {
        if (!is_schema_el(schema, child, XSD_NS, "simpleType")) {
            continue;
        }
        if (xsd_member_push(schema, entry, child, DT_STRING) < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return -1;                                              /* GCOVR_EXCL_LINE */
        }
    }
    return 0;
}

/* The xs:list or xs:union child that makes a simpleType one, with its item or member types; a simpleType with neither
   is atomic. */
static int xsd_gather_variety(th_schema *schema, xfacet_entry *entry) {
    th_node *derivation = first_schema_child(schema, entry->node, XSD_NS, "list");
    entry->variety = XSD_LIST;
    if (derivation == NULL) {
        derivation = first_schema_child(schema, entry->node, XSD_NS, "union");
        entry->variety = derivation != NULL ? XSD_UNION : XSD_ATOMIC;
    }
    return derivation != NULL ? xsd_gather_members(schema, entry, derivation) : 0;
}

static int xsd_gather_facets(th_schema *schema, xfacet_entry *entry, int content) {
    th_tree *tree = schema->tree;
    th_node *extension = content ? first_schema_child(schema, entry->node, XSD_NS, "extension") : NULL;
    th_node *derivation =
        extension != NULL ? extension : first_schema_child(schema, entry->node, XSD_NS, "restriction");
    if (derivation == NULL) {
        return content ? 0 : xsd_gather_variety(schema, entry);
    }
    entry->own = arena_alloc(&schema->mem, sizeof(facetset));
    if (entry->own == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return -1;            /* GCOVR_EXCL_LINE */
    }
    facetset_init(entry->own, DT_STRING);
    entry->own->ws = -1; /* not stated */
    Py_ssize_t base_len = 0;
    const Py_UCS4 *base = xsd_attr(tree, derivation, "base", &base_len);
    if (base != NULL) {
        const qname written = schema_node_entry(schema, derivation)->base;
        const Py_UCS4 *local = written.local, *uri = written.uri;
        Py_ssize_t local_len = written.local_len, uri_len = written.uri_len;
        if (is_xsd_uri(uri, uri_len)) {
            entry->base_id = xsd_builtin_id(local, local_len);
        } else {
            entry->base_id = DT_STRING;
            entry->keeps_ws = extension != NULL;
            entry->base_node = named_find(&schema->simple_types, local, local_len);
            if (entry->base_node == NULL && content) {
                th_node *complex = named_find(&schema->complex_types, local, local_len);
                entry->base_node =
                    complex != NULL ? first_schema_child(schema, complex, XSD_NS, "simpleContent") : NULL;
            }
        }
    }
    if (base == NULL || content) {
        entry->inner_node = first_schema_child(schema, derivation, XSD_NS, "simpleType");
    }
    for (th_node *facet = derivation->first_child; facet != NULL; facet = facet->next_sibling) {
        TH_WORK_STEP();
        if (facet->type != TH_NODE_ELEMENT) {
            continue;
        }
        const qname name = schema_node_entry(schema, facet)->name;
        if (name.uri == NULL || !u_eq_ascii(name.uri, name.uri_len, XSD_NS)) {
            continue;
        }
        Py_ssize_t value_len = 0;
        const Py_UCS4 *value = xsd_attr(tree, facet, "value", &value_len);
        if (value != NULL) {
            const int status = facet_add(&schema->mem, entry->own, name.local, name.local_len, value, value_len);
            if (status < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM */
                return -1;    /* GCOVR_EXCL_LINE */
            }
        }
    }
    return 0;
}

static int xsd_check_names(th_schema *schema, th_node *node, const qname *name, int kind);
static int xsd_check_all_slot(th_schema *schema, th_node *node);
static int xsd_record_facets(th_schema *schema, th_node *node, int content);
static int xsd_child_kind(const qname *name);
static int xsd_record_child(th_schema *schema, size_t *slot, th_node *child, const xnode *own);

/* XSD 1.0 All Group Limited: an xs:all particle, written or reached through a group ref, occurs once, because its
   members are matched once. */
static int xsd_check_all_occurs(th_schema *schema, th_node *particle) {
    th_node *model =
        is_schema_el(schema, particle, XSD_NS, "group") ? xsd_scan_group_model(schema, particle) : particle;
    if (model == NULL || !is_schema_el(schema, model, XSD_NS, "all") || xsd_occurs_max(schema->tree, particle) == 1) {
        return 0;
    }
    PyErr_SetString(PyExc_ValueError, "an xs:all group may occur only once: its maxOccurs must be 1");
    return -1;
}

/* The model group of an xs:group is its whole content, so XSD 1.0 gives it no occurrence: minOccurs and maxOccurs
   belong on the xs:group reference that uses the definition. Runs after the children, so an xs:all that occurs more
   than once is reported first. */
static int xsd_check_group_model(th_schema *schema, th_node *group) {
    Py_ssize_t len = 0;
    for (th_node *child = group->first_child; child != NULL; child = child->next_sibling) {
        if (!xsd_is_particle(schema, child, XSD_MODEL_GROUPS, 3) ||
            (xsd_attr(schema->tree, child, "minOccurs", &len) == NULL &&
             xsd_attr(schema->tree, child, "maxOccurs", &len) == NULL)) {
            continue;
        }
        const qname *model = schema_node_qname(schema, child);
        char buffer[32];
        PyErr_Format(PyExc_ValueError,
                     "xs:%s in an xs:group cannot declare minOccurs or maxOccurs; set them on the xs:group reference",
                     name_utf8(model->local, model->local_len, buffer, sizeof(buffer)));
        return -1;
    }
    return 0;
}

static xnode *xsd_cache_facets_at(th_schema *schema, th_node *node, xnode *parent);

/* The reference checks ride the facet walk so each node's cached qname (kind 0 always builds it) is looked up once. A
   node's own checks run as the walk enters it and an xs:group's model check as the walk leaves it. The walk carries
   the record of the current node's parent, so no node looks its parent up. */
static int xsd_cache_facets(th_schema *schema, th_node *root) {
    schema->xnodes = arena_alloc(&schema->mem, (size_t)schema->sqname_count * sizeof(xnode));
    if (schema->xnodes == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return -1;                /* GCOVR_EXCL_LINE */
    }
    th_node *node = root;
    xnode *parent = NULL;
    while (node != NULL) {
        if (node->type == TH_NODE_ELEMENT) {
            xnode *own = xsd_cache_facets_at(schema, node, parent);
            if (own == NULL) {
                return -1;
            }
            if (node->first_child != NULL) {
                node = node->first_child;
                parent = own;
                continue;
            }
        }
        while (node != root && node->next_sibling == NULL) {
            node = node->parent;
            const xnode *left = parent;
            parent = parent->up;
            if (left->kind == XSD_GROUP && xsd_check_group_model(schema, node) < 0) {
                return -1;
            }
        }
        node = node == root ? NULL : node->next_sibling;
    }
    return 0;
}

/* Classify node once and enter it under its parent, whose record the walk passes in: the walk reaches a parent before
   its children. Returns node's record, or NULL with an exception set. */
static xnode *xsd_cache_facets_at(th_schema *schema, th_node *node, xnode *parent) {
    const qname *name = schema_node_qname(schema, node);
    const int xsd = is_xsd_uri(name->uri, name->uri_len);
    const int kind = xsd ? xsd_child_kind(name) : XSD_CHILD_KINDS;
    xnode *own = xsd_xnode(schema, name);
    own->kind = kind;
    own->children = XSD_NO_ENTRY;
    own->up = parent;
    if (xsd) {
        if (xsd_check_names(schema, node, name, kind) < 0) {
            return NULL;
        }
        if (kind == XSD_SIMPLE_CONTENT || kind == XSD_SIMPLE_TYPE) {
            if (xsd_record_facets(schema, node, kind == XSD_SIMPLE_CONTENT) < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM */
                return NULL;                                                       /* GCOVR_EXCL_LINE */
            }
        } else if (kind == XSD_ALL) {
            if (xsd_check_all_slot(schema, node) < 0 || xsd_check_all_occurs(schema, node) < 0) {
                return NULL;
            }
            if (xsd_record_all(schema, node) < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
                return NULL;                        /* GCOVR_EXCL_LINE */
            }
        }
    }
    /* only the root, an xs:schema whose kind is XSD_CHILD_KINDS, has no parent record */
    if (kind == XSD_CHILD_KINDS || parent->kind == XSD_CHILD_KINDS || parent->kind == XSD_SIMPLE_TYPE ||
        parent->kind == XSD_ANY) {
        return own;
    }
    if (xsd_record_child(schema, &parent->children, node, own) < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM */
        return NULL;                                                  /* GCOVR_EXCL_LINE */
    }
    return own;
}

static int xsd_require_ref(th_schema *schema, th_node *node, named_vec *table, const char *kind, int required);
static int xsd_require_type(th_schema *schema, th_node *node, int allow_complex);
static int xsd_require_members(th_schema *schema, th_node *node, const char *attr, int list);
static TH_NOINLINE int xsd_is_builtin_type(const Py_UCS4 *local, Py_ssize_t local_len);
static int xsd_require_base(th_schema *schema, th_node *node);

/* The content model of a complexType, or of the complexContent extension or restriction that holds one. */
static int xsd_is_content_slot(th_schema *schema, th_node *node) {
    if (is_schema_el(schema, node, XSD_NS, "complexType")) {
        return 1;
    }
    return (is_schema_el(schema, node, XSD_NS, "extension") || is_schema_el(schema, node, XSD_NS, "restriction")) &&
           is_schema_el(schema, node->parent, XSD_NS, "complexContent");
}

/* An xs:all group is validated as an unordered set only as a content model, so XSD 1.0 allows it only there (All Group
   Limited): as a complexType's content, or as a group definition's own model. */
static int xsd_check_all_slot(th_schema *schema, th_node *node) {
    th_node *parent = node->parent;
    if (is_schema_el(schema, node, XSD_NS, "group")) {
        th_node *model = xsd_scan_group_model(schema, node);
        if (model == NULL || !is_schema_el(schema, model, XSD_NS, "all")) {
            return 0;
        }
        if (xsd_is_content_slot(schema, parent)) {
            schema->named_all = 1;
            return 0;
        }
    } else if (xsd_is_content_slot(schema, parent) || is_schema_el(schema, parent, XSD_NS, "group")) {
        return 0;
    }
    PyErr_SetString(PyExc_ValueError,
                    "an xs:all group may only be the content model of a complex type or a group definition");
    return -1;
}

/* A dangling reference or an unenforced identity constraint would let validation accept documents the schema forbids,
   so both are compile errors, as in libxml2 (src-resolve), Xerces and xmlschema. */
static int xsd_check_names(th_schema *schema, th_node *node, const qname *name, int kind) {
    if (kind == XSD_CHILD_KINDS) {
        const Py_UCS4 *local = name->local;
        const Py_ssize_t local_len = name->local_len;
        if (u_eq_ascii(local, local_len, "unique") || u_eq_ascii(local, local_len, "key") ||
            u_eq_ascii(local, local_len, "keyref")) {
            char buffer[32];
            PyErr_Format(PyExc_ValueError,
                         "identity constraint xs:%s is not supported; remove it or enforce it outside the schema",
                         name_utf8(local, local_len, buffer, sizeof(buffer)));
            return -1;
        }
        if (u_eq_ascii(local, local_len, "list")) {
            return xsd_require_members(schema, node, "itemType", 1);
        }
        if (u_eq_ascii(local, local_len, "union")) {
            return xsd_require_members(schema, node, "memberTypes", 0);
        }
    } else if (kind == XSD_ELEMENT) {
        if (xsd_require_ref(schema, node, &schema->elements, "element", 0) < 0 ||
            xsd_require_type(schema, node, 1) < 0) {
            return -1;
        }
    } else if (kind == XSD_ATTRIBUTE) {
        if (xsd_require_ref(schema, node, &schema->attributes, "attribute", 0) < 0 ||
            xsd_require_type(schema, node, 0) < 0) {
            return -1;
        }
    } else if (kind == XSD_GROUP) {
        if (xsd_require_ref(schema, node, &schema->groups, "group", node->parent != schema->root) < 0) {
            return -1;
        }
        if (xsd_check_all_slot(schema, node) < 0) {
            return -1;
        }
        return xsd_check_all_occurs(schema, node);
    } else if (kind == XSD_ATTRIBUTE_GROUP) {
        return xsd_require_ref(schema, node, &schema->attr_groups, "attribute group", 0);
    } else if (kind == XSD_EXTENSION || kind == XSD_RESTRICTION) {
        if (kind == XSD_EXTENSION && is_schema_el(schema, node->parent, XSD_NS, "complexContent")) {
            schema->complex_extension = 1;
        }
        return xsd_require_base(schema, node);
    }
    return 0;
}

static int xsd_require_ref(th_schema *schema, th_node *node, named_vec *table, const char *kind, int required) {
    Py_ssize_t ref_len = 0;
    const Py_UCS4 *ref = xsd_attr(schema->tree, node, "ref", &ref_len);
    if (ref == NULL) {
        if (required) {
            PyErr_Format(PyExc_ValueError, "a %s in a content model must have a ref attribute", kind);
            return -1;
        }
        return 0;
    }
    const Py_UCS4 *local, *prefix;
    Py_ssize_t local_len = 0, prefix_len = 0;
    split_prefix(ref, ref_len, &local, &local_len, &prefix, &prefix_len);
    if (named_find(table, local, local_len) != NULL) {
        return 0;
    }
    char buffer[256];
    PyErr_Format(PyExc_ValueError, "%s reference '%s' does not resolve to a declared %s", kind,
                 name_utf8(ref, ref_len, buffer, sizeof(buffer)), kind);
    return -1;
}

/* Whether a name, given by its namespace URI and local part, names a built-in type or a declared one. */
static int xsd_names_type(th_schema *schema, const Py_UCS4 *uri, Py_ssize_t uri_len, const Py_UCS4 *local,
                          Py_ssize_t local_len, int allow_complex) {
    if (is_xsd_uri(uri, uri_len)) {
        return xsd_is_builtin_type(local, local_len);
    }
    return named_find(&schema->simple_types, local, local_len) != NULL ||
           (allow_complex && named_find(&schema->complex_types, local, local_len) != NULL);
}

/* allow_complex matches where the element and attribute validators look a type up. */
static int xsd_require_type(th_schema *schema, th_node *node, int allow_complex) {
    Py_ssize_t type_len = 0;
    const Py_UCS4 *type = xsd_attr(schema->tree, node, "type", &type_len);
    if (type == NULL) {
        return 0;
    }
    const qname written = schema_node_entry(schema, node)->type;
    if (xsd_names_type(schema, written.uri, written.uri_len, written.local, written.local_len, allow_complex)) {
        return 0;
    }
    char buffer[256];
    PyErr_Format(PyExc_ValueError, "type '%s' does not resolve to a declared type",
                 name_utf8(type, type_len, buffer, sizeof(buffer)));
    return -1;
}

/* One name of an xs:list's itemType or an xs:union's memberTypes, resolved in the namespace scope of its element. */
static int xsd_require_member(th_schema *schema, th_node *node, const Py_UCS4 *name, Py_ssize_t name_len) {
    const Py_UCS4 *local, *prefix, *uri;
    Py_ssize_t local_len = 0, prefix_len = 0, uri_len = 0;
    split_prefix(name, name_len, &local, &local_len, &prefix, &prefix_len);
    resolve_ns(schema->tree_ns, node, prefix, prefix_len, &uri, &uri_len);
    if (xsd_names_type(schema, uri, uri_len, local, local_len, 0)) {
        return 0;
    }
    char buffer[256];
    PyErr_Format(PyExc_ValueError, "type '%s' does not resolve to a declared type",
                 name_utf8(name, name_len, buffer, sizeof(buffer)));
    return -1;
}

/* A list needs exactly one item type and a union at least one member type, each named by the attribute or given as a
   simpleType child. A value of either is checked against every name, so each must resolve. */
static int xsd_require_members(th_schema *schema, th_node *node, const char *attr, int list) {
    Py_ssize_t names_len = 0, count = 0;
    const Py_UCS4 *names = xsd_attr(schema->tree, node, attr, &names_len);
    for (Py_ssize_t at = 0; names != NULL && at < names_len; at++) {
        if (is_xml_space(names[at])) {
            continue;
        }
        const Py_ssize_t end = xsd_name_end(names, names_len, at);
        if (xsd_require_member(schema, node, names + at, end - at) < 0) {
            return -1;
        }
        count++;
        at = end;
    }
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        count += is_schema_el(schema, child, XSD_NS, "simpleType");
    }
    if (list ? count == 1 : count > 0) {
        return 0;
    }
    PyErr_Format(PyExc_ValueError,
                 list ? "xs:list requires one item type: an %s attribute or a simpleType child"
                      : "xs:union requires a member type: a %s attribute or a simpleType child",
                 attr);
    return -1;
}

static TH_NOINLINE int xsd_is_builtin_type(const Py_UCS4 *local, Py_ssize_t local_len) {
    if (dt_lookup(local, local_len) != DT_UNKNOWN) {
        return 1;
    }
    /* These XSD 1.0 types retain the existing fallback without dedicated datatype handlers. */
    static const char *const names[] = {"anyType"};
    for (size_t index = 0; index < sizeof(names) / sizeof(names[0]); index++) {
        if (u_eq_ascii(local, local_len, names[index])) {
            return 1;
        }
    }
    return 0;
}

static int xsd_require_base(th_schema *schema, th_node *node) {
    th_tree *tree = schema->tree;
    Py_ssize_t base_len = 0;
    const Py_UCS4 *base = xsd_attr(tree, node, "base", &base_len);
    if (base == NULL) {
        return 0;
    }
    const qname written = schema_node_entry(schema, node)->base;
    const Py_UCS4 *local = written.local, *uri = written.uri;
    Py_ssize_t local_len = written.local_len, uri_len = written.uri_len;
    if (is_xsd_uri(uri, uri_len)) {
        return 0;
    }
    th_node *parent = node->parent;
    int found;
    if (is_schema_el(schema, parent, XSD_NS, "simpleType")) {
        found = named_find(&schema->simple_types, local, local_len) != NULL;
    } else if (is_schema_el(schema, parent, XSD_NS, "simpleContent")) {
        found = named_find(&schema->simple_types, local, local_len) != NULL ||
                named_find(&schema->complex_types, local, local_len) != NULL;
    } else {
        found = named_find(&schema->complex_types, local, local_len) != NULL;
    }
    if (found) {
        return 0;
    }
    char buffer[256];
    PyErr_Format(PyExc_ValueError, "base type '%s' does not resolve to a declared type",
                 name_utf8(base, base_len, buffer, sizeof(buffer)));
    return -1;
}

static int xsd_record_facets(th_schema *schema, th_node *node, int content) {
    if (schema->facet_count == schema->facet_cap) {
        size_t capacity, bytes;
        const int fits =
            th_grow_cap(schema->facet_count + 1, schema->facet_cap, 8, sizeof(xfacet_entry), &capacity, &bytes);
        if (!fits) {   /* GCOVR_EXCL_BR_LINE: allocation size overflow */
            return -1; /* GCOVR_EXCL_LINE */
        }
        xfacet_entry *entries = arena_alloc(&schema->mem, bytes);
        if (entries == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return -1;         /* GCOVR_EXCL_LINE */
        }
        if (schema->facet_count > 0) {
            memcpy(entries, schema->facet_entries, schema->facet_count * sizeof(xfacet_entry));
        }
        schema->facet_entries = entries;
        schema->facet_cap = capacity;
    }
    xfacet_entry *entry = &schema->facet_entries[schema->facet_count++];
    *entry = (xfacet_entry){.node = node, .base_id = -1};
    return xsd_gather_facets(schema, entry, content);
}

/* Room for one more element in an arena array that doubles; NULL when the arena or the size arithmetic fails. */
static void *xsd_grow(th_schema *schema, void *items, size_t count, size_t *cap, size_t elem_size) {
    if (count < *cap) {
        return items;
    }
    size_t capacity, bytes;
    if (!th_grow_cap(count + 1, *cap, 8, elem_size, &capacity, &bytes)) { /* GCOVR_EXCL_BR_LINE: size overflow */
        return NULL;                                                      /* GCOVR_EXCL_LINE */
    }
    void *grown = arena_alloc(&schema->mem, bytes);
    if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return NULL;     /* GCOVR_EXCL_LINE */
    }
    if (count > 0) {
        memcpy(grown, items, count * elem_size);
    }
    *cap = capacity;
    return grown;
}

/* The XSD_CHILD_NAMES kind of an XSD element's local name, or XSD_CHILD_KINDS for a name validation never looks up. */
static int xsd_child_kind(const qname *name) {
    /* the length, and where two names share one their first or second letter, leaves one candidate to compare */
    int kind;
    switch (name->local_len) {
    case 3:
        kind = name->local[1] == 'l' ? XSD_ALL : XSD_ANY;
        break;
    case 5:
        kind = XSD_GROUP;
        break;
    case 6:
        kind = XSD_CHOICE;
        break;
    case 7:
        kind = XSD_ELEMENT;
        break;
    case 8:
        kind = XSD_SEQUENCE;
        break;
    case 9:
        kind = name->local[0] == 'e' ? XSD_EXTENSION : XSD_ATTRIBUTE;
        break;
    case 10:
        kind = XSD_SIMPLE_TYPE;
        break;
    case 11:
        kind = name->local[0] == 'c' ? XSD_COMPLEX_TYPE : XSD_RESTRICTION;
        break;
    case 12:
        kind = XSD_ANY_ATTRIBUTE;
        break;
    case 13:
        kind = XSD_SIMPLE_CONTENT;
        break;
    case 14:
        kind = name->local[0] == 'c' ? XSD_COMPLEX_CONTENT : XSD_ATTRIBUTE_GROUP;
        break;
    default:
        return XSD_CHILD_KINDS;
    }
    return u_eq_ascii(name->local, name->local_len, XSD_CHILD_NAMES[kind]) ? kind : XSD_CHILD_KINDS;
}

static int xsd_push_child(th_schema *schema, xchild **items, size_t *count, size_t *cap, th_node *child,
                          const xnode *own) {
    xchild *grown = xsd_grow(schema, *items, *count, cap, sizeof(xchild));
    if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return -1;       /* GCOVR_EXCL_LINE */
    }
    *items = grown;
    grown[(*count)++] = (xchild){child, own->kind, own};
    return 0;
}

/* Enter `child`, an XSD element of this name, into its parent's entry when validation looks for that name. *slot is the
   index of that entry, XSD_NO_ENTRY until the parent's first such child opens it. */
static int xsd_record_child(th_schema *schema, size_t *slot, th_node *child, const xnode *own) {
    const int kind = own->kind;
    if (*slot == XSD_NO_ENTRY) {
        xchild_entry *entries =
            xsd_grow(schema, schema->child_entries, schema->child_count, &schema->child_cap, sizeof(xchild_entry));
        if (entries == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return -1;         /* GCOVR_EXCL_LINE */
        }
        schema->child_entries = entries;
        *slot = schema->child_count++;
        memset(&entries[*slot], 0, sizeof(xchild_entry));
    }
    xchild_entry *entry = &schema->child_entries[*slot];
    if (entry->first[kind] == NULL) {
        entry->first[kind] = child;
    }
    if (entry->model_group == NULL && (kind == XSD_SEQUENCE || kind == XSD_CHOICE || kind == XSD_ALL)) {
        entry->model_group = child;
    }
    if (kind == XSD_ATTRIBUTE || kind == XSD_ATTRIBUTE_GROUP || kind == XSD_ANY_ATTRIBUTE ||
        kind == XSD_COMPLEX_CONTENT || kind == XSD_SIMPLE_CONTENT) {
        return xsd_push_child(schema, &entry->attrs, &entry->attr_count, &entry->attr_cap, child, own);
    }
    if (kind == XSD_ELEMENT || kind == XSD_ANY || kind == XSD_SEQUENCE || kind == XSD_CHOICE || kind == XSD_ALL ||
        kind == XSD_GROUP) {
        return xsd_push_child(schema, &entry->particles, &entry->particle_count, &entry->particle_cap, child, own);
    }
    return 0;
}

static int xsd_facet_order(const void *left, const void *right) {
    const uintptr_t left_node = (uintptr_t)((const xfacet_entry *)left)->node;
    const uintptr_t right_node = (uintptr_t)((const xfacet_entry *)right)->node;
    return (left_node > right_node) - (left_node < right_node);
}

/* xsd_cache_facets records every simpleType and simpleContent of the schema, so the search always hits. */
static xfacet_entry *xsd_facet_entry(const th_schema *schema, const th_node *node) {
    size_t low = 0, high = schema->facet_count;
    while (low < high) {
        const size_t middle = low + (high - low) / 2;
        if ((uintptr_t)schema->facet_entries[middle].node < (uintptr_t)node) {
            low = middle + 1;
        } else {
            high = middle;
        }
    }
    return &schema->facet_entries[low];
}

typedef struct {
    const facetset **items;
    size_t count, cap;
} xfacet_levels;

static int xsd_push_level(arena *mem, xfacet_levels *levels, const facetset *level) {
    if (levels->count == levels->cap) {
        size_t capacity, bytes;
        const int fits = th_grow_cap(levels->count + 1, levels->cap, 16, sizeof(*levels->items), &capacity, &bytes);
        if (!fits) {   /* GCOVR_EXCL_BR_LINE: allocation size overflow */
            return -1; /* GCOVR_EXCL_LINE */
        }
        const facetset **grown = arena_alloc(mem, bytes);
        if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return -1;       /* GCOVR_EXCL_LINE */
        }
        if (levels->count > 0) {
            memcpy(grown, levels->items, levels->count * sizeof(*grown));
        }
        levels->items = grown;
        levels->cap = capacity;
    }
    levels->items[levels->count++] = level;
    return 0;
}

typedef struct {
    xfacet_levels enums, patterns;
} xfacet_fold;

/* A derivation xsd_fold_facets is inside. */
typedef struct {
    const xfacet_entry *entry;
    int depth;   /* hops from the type being linked */
    int base_id; /* the built-in type reached so far */
    int stage;   /* 0 before the named base, 1 before the inline type, 2 before the derivation's own facets */
    int took;    /* the last stage pushed the derivation it folds, whose result the next stage takes */
} xfacet_frame;

/* Fold a derivation's own facets into `facets` once its base and inline type are folded; the built-in type reached. */
static int xsd_fold_own(arena *mem, const xfacet_entry *entry, int base_id, facetset *facets, xfacet_fold *fold) {
    const facetset *own = entry->own;
    if (base_id < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return -1;     /* GCOVR_EXCL_LINE */
    }
    facets->base_id = base_id;
    if (!entry->keeps_ws) {
        facets->ws = dt_default_ws(base_id);
    }
    if (facet_inherit(mem, facets, own) < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return -1;                             /* GCOVR_EXCL_LINE */
    }
    if (own->enum_count > 0) {
        if (xsd_push_level(mem, &fold->enums, own) < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return -1;                                    /* GCOVR_EXCL_LINE */
        }
    }
    if (own->pattern_count > 0) {
        if (xsd_push_level(mem, &fold->patterns, own) < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return -1;                                       /* GCOVR_EXCL_LINE */
        }
    }
    return base_id;
}

/* Fold `root` into `facets`: each derivation's named base first, then its inline type, then its own facets. A later
   scalar facet replaces an earlier one, and every enumeration and pattern on the way applies, so only a pointer to each
   derivation that states some is kept. A hop counts toward XSD_MAX_BASE_DEPTH from the type being linked. Inline types
   nest as deep as the schema, so the derivations under way sit on an explicit stack. Returns the built-in type
   reached, or -1 when out of memory. */
static int xsd_fold_facets(arena *mem, const xfacet_entry *root, facetset *facets, xfacet_fold *fold) {
    xfacet_frame local[16];
    xfacet_frame *stack = local;
    Py_ssize_t depth = 1, cap = 16;
    stack[0] = (xfacet_frame){root, 0, 0, 0, 0};
    int result = 0;
    while (depth > 0) {
        xfacet_frame *top = &stack[depth - 1];
        const xfacet_entry *entry = top->entry;
        const xfacet_entry *next = NULL;
        if (top->took) {
            top->base_id = result;
            top->took = 0;
        }
        if (top->stage == 0) {
            TH_WORK_STEP();
            if (entry->own == NULL) {
                result = facets->base_id;
                depth--;
                continue;
            }
            top->base_id = facets->base_id;
            if (entry->base_id >= 0) {
                if (entry->base != NULL && top->depth < XSD_MAX_BASE_DEPTH) {
                    next = entry->base;
                } else {
                    top->base_id = entry->base_id;
                }
            }
        } else if (top->stage == 1) {
            next = entry->inner;
        } else {
            result = xsd_fold_own(mem, entry, top->base_id, facets, fold);
            if (result < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
                break;        /* GCOVR_EXCL_LINE */
            }
            depth--;
            continue;
        }
        top->stage++;
        if (next != NULL) {
            xfacet_frame *grown = walk_reserve(stack, local, depth, &cap, sizeof(xfacet_frame));
            if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                result = -1;     /* GCOVR_EXCL_LINE */
                break;           /* GCOVR_EXCL_LINE */
            }
            stack = grown;
            stack[depth - 1].took = 1;
            stack[depth] = (xfacet_frame){next, stack[depth - 1].depth + 1, 0, 0, 0};
            depth++;
        }
    }
    walk_release(stack, local);
    return result;
}

static int xsd_keep_levels(arena *mem, const xfacet_levels *levels, const facetset *const **kept, Py_ssize_t *count) {
    const facetset **copy = arena_alloc(mem, levels->count * sizeof(*copy));
    if (copy == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return -1;      /* GCOVR_EXCL_LINE */
    }
    memcpy(copy, levels->items, levels->count * sizeof(*copy));
    *kept = copy;
    *count = (Py_ssize_t)levels->count;
    return 0;
}

/* The list or union that the chain of restrictions from each entry ends in, so a value of a restriction of a list is
   checked item by item however long the chain is. Each entry leads to one next entry, so the walks share their tails
   and every entry is settled once; a chain that ends in an atomic type, or loops back on itself, has no root. */
static int xsd_settle_roots(th_schema *schema) {
    const size_t count = schema->facet_count;
    xfacet_entry **path = arena_alloc(&schema->mem, count * sizeof(*path));
    if (path == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return -1;      /* GCOVR_EXCL_LINE */
    }
    for (size_t index = 0; index < count; index++) {
        const xfacet_entry *root = NULL;
        size_t depth = 0;
        xfacet_entry *cur = &schema->facet_entries[index];
        for (;;) {
            if (cur->root_state == 2) {
                root = cur->root;
                break;
            }
            if (cur->root_state == 1) {
                break;
            }
            cur->root_state = 1;
            path[depth++] = cur;
            if (cur->variety != XSD_ATOMIC) {
                root = cur;
                break;
            }
            cur = cur->inner != NULL ? cur->inner : cur->base;
            if (cur == NULL) {
                break;
            }
        }
        while (depth > 0) {
            xfacet_entry *settled = path[--depth];
            settled->root = root;
            settled->root_state = 2;
        }
    }
    return 0;
}

/* Runs once the entries are sorted and stay put. Each type folds its own chain because the depth cutoff counts from
   it, but a hop costs the fixed-size scalar facets and one pointer, not a copy of the base's facets. */
static int xsd_link_facets(th_schema *schema) {
    for (size_t index = 0; index < schema->facet_count; index++) {
        xfacet_entry *entry = &schema->facet_entries[index];
        if (entry->base_node != NULL) {
            entry->base = xsd_facet_entry(schema, entry->base_node);
        }
        if (entry->inner_node != NULL) {
            entry->inner = xsd_facet_entry(schema, entry->inner_node);
        }
    }
    if (xsd_settle_roots(schema) < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return -1;                      /* GCOVR_EXCL_LINE */
    }
    xfacet_fold fold = {{NULL, 0, 0}, {NULL, 0, 0}};
    for (size_t index = 0; index < schema->facet_count; index++) {
        xfacet_entry *entry = &schema->facet_entries[index];
        facetset *facets = &entry->facets;
        facetset_init(facets, DT_STRING);
        fold.enums.count = fold.patterns.count = 0;
        if (xsd_fold_facets(&schema->mem, entry, facets, &fold) < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM */
            return -1;                                                 /* GCOVR_EXCL_LINE */
        }
        if (fold.enums.count == 1) {
            facets->enums = fold.enums.items[0]->enums;
            facets->enum_count = fold.enums.items[0]->enum_count;
        } else if (fold.enums.count > 1) {
            const int kept =
                xsd_keep_levels(&schema->mem, &fold.enums, &entry->levels.enums, &entry->levels.enum_count);
            if (kept < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
                return -1;  /* GCOVR_EXCL_LINE */
            }
        }
        if (fold.patterns.count == 1) {
            facets->patterns = fold.patterns.items[0]->patterns;
            facets->pattern_count = fold.patterns.items[0]->pattern_count;
        } else if (fold.patterns.count > 1) {
            const int kept =
                xsd_keep_levels(&schema->mem, &fold.patterns, &entry->levels.patterns, &entry->levels.pattern_count);
            if (kept < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
                return -1;  /* GCOVR_EXCL_LINE */
            }
        }
    }
    return 0;
}

static int xsd_check_simple(valctx *ctx, th_node *node, int builtin_id, th_node *simpletype, const Py_UCS4 *value,
                            Py_ssize_t len, int item);

/* A list is valid when each of its items is valid against the item type. Its items are read from the value as written:
   checking one may normalize into the buffer the collapsed copy is made in, so that copy is only made afterwards, for
   the facets of the list. The length facets count items. */
static int xsd_check_list(valctx *ctx, th_node *node, const xfacet_entry *list, const xfacet_entry *entry,
                          const Py_UCS4 *value, Py_ssize_t len) {
    int ok = 1;
    for (Py_ssize_t at = 0; at < len;) {
        if (is_xml_space(value[at])) {
            at++;
            continue;
        }
        const Py_ssize_t end = xsd_name_end(value, len, at);
        if (xsd_check_simple(ctx, node, list->members[0].builtin, list->members[0].type, value + at, end - at, 1) < 0) {
            ok = 0;
        }
        at = end;
    }
    Py_ssize_t norm_len = 0;
    const Py_UCS4 *norm = dt_normalize_ws(ctx->schema, 2, value, len, &norm_len);
    if (!facet_check(ctx, node, &entry->facets, &entry->levels, norm, norm_len, dt_list_count(norm, norm_len))) {
        ok = 0;
    }
    return ok ? 2 : -1;
}

/* A union is valid when one of its member types accepts the value, and the members before it rejecting the value is no
   violation. The accepting member's whitespace rule is the one the facets of a restriction of the union see. An item of
   a list has no whitespace to normalize, so it is passed as written. */
static int xsd_check_union(valctx *ctx, th_node *node, const xfacet_entry *choice, const xfacet_entry *entry,
                           const Py_UCS4 *value, Py_ssize_t len, int item) {
    const Py_ssize_t error_count = ctx->error_count;
    const int collect_errors = ctx->collect_errors;
    int ws = -1;
    ctx->collect_errors = 0;
    for (Py_ssize_t index = 0; index < choice->member_count && ws < 0; index++) {
        ws = xsd_check_simple(ctx, node, choice->members[index].builtin, choice->members[index].type, value, len, item);
    }
    ctx->collect_errors = collect_errors;
    ctx->error_count = error_count;
    if (ws < 0) {
        char buffer[128];
        report(ctx, node, "datatype", "value '%s' is not valid for any member type of the union",
               name_utf8(value, len, buffer, sizeof(buffer)));
        return -1;
    }
    Py_ssize_t norm_len = len;
    const Py_UCS4 *norm = item ? value : dt_normalize_ws(ctx->schema, ws, value, len, &norm_len);
    return facet_check(ctx, node, &entry->facets, &entry->levels, norm, norm_len, norm_len) ? ws : -1;
}

/* Check a value against a simple type (built-in id, or a named/inline simpleType node, or the simpleContent node of a
   complexType), reporting on `node`. `item` says the value is one item of a list, which holds no whitespace to
   normalize. Returns the whitespace rule the value was normalized with, or -1 when it is not valid. */
static int xsd_check_simple(valctx *ctx, th_node *node, int builtin_id, th_node *simpletype, const Py_UCS4 *value,
                            Py_ssize_t len, int item) {
    th_schema *schema = ctx->schema;
    if (schema_stack_low(schema)) {
        return -1;
    }
    facetset builtin;
    const facetset *facets;
    static const facet_levels no_levels = {NULL, NULL, 0, 0};
    const facet_levels *levels = &no_levels;
    if (simpletype != NULL) {
        const xfacet_entry *entry = xsd_facet_entry(schema, simpletype);
        if (entry->root != NULL) {
            return entry->root->variety == XSD_LIST ? xsd_check_list(ctx, node, entry->root, entry, value, len)
                                                    : xsd_check_union(ctx, node, entry->root, entry, value, len, item);
        }
        facets = &entry->facets;
        levels = &entry->levels;
    } else {
        facetset_init(&builtin, builtin_id);
        facets = &builtin;
    }
    Py_ssize_t norm_len = len;
    const Py_UCS4 *norm = item ? value : dt_normalize_ws(schema, facets->ws, value, len, &norm_len);
    if (!dt_check_lexical(facets->base_id, norm, norm_len)) {
        char buffer[128];
        report(ctx, node, "datatype", "value '%s' is not a valid %s", name_utf8(norm, norm_len, buffer, sizeof(buffer)),
               DT_NAMES[facets->base_id].name);
        return -1;
    }
    const Py_ssize_t units = dt_is_list(facets->base_id) ? dt_list_count(norm, norm_len) : norm_len;
    return facet_check(ctx, node, facets, levels, norm, norm_len, units) ? facets->ws : -1;
}

/* Validate a text value against a simple type (built-in id, or a named/inline
   simpleType node, or the simpleContent node of a complexType). Reports on `node`. */
static void xsd_validate_simple(valctx *ctx, th_node *node, int builtin_id, th_node *simpletype, const Py_UCS4 *value,
                                Py_ssize_t len) {
    xsd_check_simple(ctx, node, builtin_id, simpletype, value, len, 0);
}

/* ---- attributes ---- */

typedef struct {
    Py_ssize_t *slots;
    size_t capacity;
    /* Without slots, a bit per instance attribute local name (xsd_attr_bit), set once `checked` declarations have
       asked, so a long list of declarations passes over the ones whose name no attribute can have. */
    uint64_t names;
    int checked;
} xattr_index;

/* The bit of a 64-bit filter a name with this hash sets. */
static inline uint64_t xsd_attr_bit(uint64_t hash) {
    return UINT64_C(1) << (hash >> 58);
}

static uint64_t xsd_attr_hash_bytes(const char *bytes, Py_ssize_t len) {
    uint64_t hash = UINT64_C(1469598103934665603);
    for (Py_ssize_t byte = 0; byte < len; byte++) {
        hash ^= (unsigned char)bytes[byte];
        hash *= UINT64_C(1099511628211);
    }
    return hash;
}

/* An instance attribute name split at its prefix. A prefix with no declaration in scope leaves the whole name as the
   local name in no namespace, which is how an HTML-parsed tree keeps it. */
typedef struct {
    const char *local;
    Py_ssize_t local_len;
    const Py_UCS4 *uri;
    Py_ssize_t uri_len;
} xattr_name;

static xattr_name xsd_attr_name(th_schema *schema, const char *name, Py_ssize_t len) {
    xattr_name out = {name, len, NULL, 0};
    for (Py_ssize_t at = 0; at < len; at++) {
        if (name[at] != ':') {
            continue;
        }
        if (at == 3 && memcmp(name, "xml", 3) == 0) {
            out.local = name + at + 1;
            out.local_len = len - at - 1;
            out.uri = xml_namespace_uri();
            out.uri_len = sizeof("http://www.w3.org/XML/1998/namespace") - 1;
            return out;
        }
        const Py_ssize_t index = ns_find_bytes(&schema->ns, name, at);
        if (index >= 0 && schema->ns.bindings[index].bound) {
            out.local = name + at + 1;
            out.local_len = len - at - 1;
            out.uri = schema->ns.bindings[index].uri;
            out.uri_len = schema->ns.bindings[index].uri_len;
        }
        return out;
    }
    return out;
}

static int xsd_bytes_is(const char *bytes, Py_ssize_t len, const char *literal) {
    const size_t literal_len = strlen(literal);
    return (size_t)len == literal_len && memcmp(bytes, literal, literal_len) == 0;
}

/* xsi:type, xsi:nil, xsi:schemaLocation and xsi:noNamespaceSchemaLocation are not checked against attribute uses or
   wildcards (XSD 1.1 §3.4.4.2). Other attributes in the XSI namespace are. */
static int xsd_attr_exempt(const xattr_name *name) {
    if (name->uri == NULL || !u_eq_ascii(name->uri, name->uri_len, XSI_NS)) {
        return 0;
    }
    return xsd_bytes_is(name->local, name->local_len, "type") || xsd_bytes_is(name->local, name->local_len, "nil") ||
           xsd_bytes_is(name->local, name->local_len, "schemaLocation") ||
           xsd_bytes_is(name->local, name->local_len, "noNamespaceSchemaLocation");
}

/* The namespace a declared attribute's name is in: the target namespace for a global declaration or a qualified local
   one, and none for an unqualified local one. */
static const Py_UCS4 *xsd_attr_decl_uri(th_schema *schema, th_node *decl, Py_ssize_t *len) {
    Py_ssize_t form_len = 0;
    const Py_UCS4 *form = xsd_attr(schema->tree, decl, "form", &form_len);
    const int qualified = form != NULL ? u_eq_ascii(form, form_len, "qualified") : schema->attribute_qualified;
    if (is_schema_el(schema, decl->parent, XSD_NS, "schema") || qualified) {
        *len = schema->target_ns_len;
        return schema->target_ns;
    }
    *len = 0;
    return NULL;
}

/* Whether an instance attribute is the declared attribute (local, uri) it is matched against. */
static int xsd_attr_is_decl(th_schema *schema, th_tree *tree, const th_node_attr *attr, const Py_UCS4 *local,
                            Py_ssize_t local_len, const Py_UCS4 *uri, Py_ssize_t uri_len) {
    Py_ssize_t name_len = 0;
    const char *bytes = th_attr_name(tree, attr->name_atom, &name_len);
    const xattr_name name = xsd_attr_name(schema, bytes, name_len);
    return u_eq_utf8(local, local_len, name.local, name.local_len) && u_eq_u(name.uri, name.uri_len, uri, uri_len);
}

static int xsd_index_attrs(valctx *ctx, th_node *instance, xattr_index *index) {
    if (instance->attr_count < 32) {
        return 0;
    }
    size_t bytes;
    /* GCOVR_EXCL_BR_START: allocation size overflow */
    if (!th_grow_cap((size_t)instance->attr_count * 2, 0, 64, sizeof(*index->slots), &index->capacity, &bytes)) {
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation failure */
        return -1;        /* GCOVR_EXCL_LINE: allocation failure */
    }
    /* GCOVR_EXCL_BR_STOP */
    index->slots = arena_alloc(&ctx->schema->mem, bytes);
    if (index->slots == NULL) { /* GCOVR_EXCL_BR_LINE: arena allocation failure */
        PyErr_NoMemory();       /* GCOVR_EXCL_LINE: allocation failure */
        return -1;              /* GCOVR_EXCL_LINE: allocation failure */
    }
    memset(index->slots, 0, bytes);
    for (Py_ssize_t position = 0; position < instance->attr_count; position++) {
        Py_ssize_t length;
        const char *name = th_attr_name(ctx->tree, instance->attrs[position].name_atom, &length);
        const xattr_name qualified = xsd_attr_name(ctx->schema, name, length);
        size_t slot = (size_t)xsd_attr_hash_bytes(qualified.local, qualified.local_len) & (index->capacity - 1);
        while (index->slots[slot] != 0) {
            slot = (slot + 1) & (index->capacity - 1);
        }
        index->slots[slot] = position + 1;
    }
    return 0;
}

/* The value an instance attribute holds against its declaration: the fixed value, then the type. */
static void xsd_validate_attr_value(valctx *ctx, th_node *instance, th_node *effective, const th_node_attr *present,
                                    const char *name_bytes) {
    th_schema *schema = ctx->schema;
    th_tree *tree = schema->tree;
    const Py_UCS4 *value = present->value;
    if (value == NULL) {    /* GCOVR_EXCL_BR_LINE: an XML attribute always has a value */
        value = EMPTY_UCS4; /* GCOVR_EXCL_LINE */
    } /* GCOVR_EXCL_LINE: llvm miscredits the closing brace of the unexecuted guard */
    Py_ssize_t value_len = present->value_len;
    Py_ssize_t fixed_len = 0;
    const Py_UCS4 *fixed = xsd_attr(tree, effective, "fixed", &fixed_len);
    if (fixed != NULL && !u_eq_u(value, value_len, fixed, fixed_len)) {
        report(ctx, instance, "structure", "attribute '%s' must equal its fixed value", name_bytes);
    }
    Py_ssize_t type_len = 0;
    const Py_UCS4 *type = xsd_attr(tree, effective, "type", &type_len);
    th_node *inline_type = xsd_child(schema, effective, XSD_SIMPLE_TYPE);
    if (type != NULL) {
        th_node *named = NULL;
        const qname written = schema_node_entry(schema, effective)->type;
        const Py_UCS4 *local = written.local, *uri = written.uri;
        Py_ssize_t local_len = written.local_len, uri_len = written.uri_len;
        int base_id = DT_STRING;
        if (is_xsd_uri(uri, uri_len)) {
            base_id = xsd_builtin_id(local, local_len);
        } else {
            named = named_find(&schema->simple_types, local, local_len);
        }
        xsd_validate_simple(ctx, instance, base_id, named, value, value_len);
    } else if (inline_type != NULL) {
        xsd_validate_simple(ctx, instance, DT_STRING, inline_type, value, value_len);
    }
}

/* Validate one declared attribute against the instance, honoring use/fixed/default. */
static XSD_FORCEINLINE void xsd_validate_attr_decl(valctx *ctx, th_node *instance, const xnode *use,
                                                   xattr_index *attrs) {
    th_schema *schema = ctx->schema;
    th_tree *inst = ctx->tree;
    const Py_UCS4 *name = use->want.local;
    Py_ssize_t name_len = use->want.local_len;
    if (name == NULL) {
        return;
    }
    if (attrs->slots == NULL && ++attrs->checked >= 8) {
        if (attrs->checked == 8) {
            for (Py_ssize_t index = 0; index < instance->attr_count; index++) {
                Py_ssize_t length = 0;
                const char *bytes = th_attr_name(inst, instance->attrs[index].name_atom, &length);
                const xattr_name qualified = xsd_attr_name(schema, bytes, length);
                attrs->names |= xsd_attr_bit(xsd_attr_hash_bytes(qualified.local, qualified.local_len));
            }
        }
        if (!(attrs->names & xsd_attr_bit(use->hash)) && use->use != XSD_USE_REQUIRED) {
            return;
        }
    }
    const th_node_attr *present = NULL;
    if (attrs->slots != NULL) {
        size_t slot = (size_t)use->hash & (attrs->capacity - 1);
        while (attrs->slots[slot] != 0) {
            const th_node_attr *candidate = &instance->attrs[attrs->slots[slot] - 1];
            if (xsd_attr_is_decl(schema, inst, candidate, name, name_len, use->want.uri, use->want.uri_len)) {
                present = candidate;
                break;
            }
            slot = (slot + 1) & (attrs->capacity - 1);
        }
    }
    for (Py_ssize_t index = 0; attrs->slots == NULL && index < instance->attr_count; index++) {
        if (xsd_attr_is_decl(schema, inst, &instance->attrs[index], name, name_len, use->want.uri, use->want.uri_len)) {
            present = &instance->attrs[index];
            break;
        }
    }
    if (present == NULL && use->use != XSD_USE_REQUIRED) {
        return;
    }
    char namebuf[256];
    const char *name_bytes = name_utf8(name, name_len, namebuf, sizeof(namebuf));
    if (present == NULL) {
        report(ctx, instance, "structure", "required attribute '%s' is missing", name_bytes);
        return;
    }
    if (use->use == XSD_USE_PROHIBITED) {
        report(ctx, instance, "structure", "attribute '%s' is prohibited", name_bytes);
        return;
    }
    xsd_validate_attr_value(ctx, instance, use->want.decl, present, name_bytes);
}

/* Resolve every xs:element and xs:attribute once xsd_compile has checked their refs, for validation to read on each
   instance node instead of looking the ref, name, use and form attributes up again. */
static void xsd_resolve_decls(th_schema *schema) {
    th_tree *tree = schema->tree;
    for (Py_ssize_t index = 0; index < schema->sqname_count; index++) {
        xnode *own = &schema->xnodes[index];
        th_node *decl = schema->sqnames[index].node;
        if (own->kind == XSD_ELEMENT) {
            xsd_element_resolve(schema, decl, &own->want);
            continue;
        }
        if (own->kind != XSD_ATTRIBUTE) {
            continue;
        }
        th_node *effective = decl;
        Py_ssize_t ref_len = 0;
        const Py_UCS4 *ref = xsd_attr(tree, decl, "ref", &ref_len);
        if (ref != NULL) {
            const Py_UCS4 *local, *prefix;
            Py_ssize_t local_len = 0, prefix_len = 0;
            split_prefix(ref, ref_len, &local, &local_len, &prefix, &prefix_len);
            /* xsd_compile has already rejected an attribute ref that resolves to nothing */
            effective = named_find(&schema->attributes, local, local_len);
        }
        own->want = (edecl){.decl = effective};
        own->want.local = xsd_attr(tree, effective, "name", &own->want.local_len);
        if (own->want.local != NULL) {
            own->want.uri = xsd_attr_decl_uri(schema, effective, &own->want.uri_len);
            own->hash = named_hash(own->want.local, own->want.local_len);
        }
        Py_ssize_t use_len = 0;
        const Py_UCS4 *written = xsd_attr(tree, decl, "use", &use_len);
        own->use = written == NULL                              ? XSD_USE_OPTIONAL
                   : u_eq_ascii(written, use_len, "required")   ? XSD_USE_REQUIRED
                   : u_eq_ascii(written, use_len, "prohibited") ? XSD_USE_PROHIBITED
                                                                : XSD_USE_OPTIONAL;
    }
    schema->decls_resolved = 1;
}

/* Collect and validate the declared attributes of a complexType (own, attributeGroup
   refs, and the extension base), then flag any undeclared instance attribute. */
static void xsd_validate_attrs(valctx *ctx, th_node *instance, th_node *scope, edecl_vec *declared);

/* The content child a complexType derives through: XSD allows one, and with several every walk takes the same one, so
   the base chain xsd_check_chains bounds is the chain validation recurses along. */
static th_node *xsd_type_content(const th_schema *schema, th_node *ctype) {
    th_node *content = first_schema_child(schema, ctype, XSD_NS, "complexContent");
    return content != NULL ? content : first_schema_child(schema, ctype, XSD_NS, "simpleContent");
}

static void xsd_collect_attrs(valctx *ctx, th_node *instance, th_node *scope, edecl_vec *declared, xattr_index *attrs,
                              edecl_vec *wilds) {
    th_schema *schema = ctx->schema;
    th_tree *tree = schema->tree;
    /* Only a complexType has a base. An attribute group or a derivation holds attributes and attribute group refs, so
       from there the walk follows nothing but the refs the attribute group cycle check covers. */
    const xchild_entry *children = xsd_child_entry(schema, scope);
    th_node *content = NULL;
    if (is_schema_el(schema, scope, XSD_NS, "complexType")) { /* the one xsd_type_content picks */
        content = children->first[XSD_COMPLEX_CONTENT] != NULL ? children->first[XSD_COMPLEX_CONTENT]
                                                               : children->first[XSD_SIMPLE_CONTENT];
    }
    /* GCOVR_EXCL_BR_START: arena OOM is unforceable */
    if (edecl_reserve(schema, declared, declared->count + (Py_ssize_t)children->attr_count) < 0) {
        return; /* GCOVR_EXCL_LINE */
    }
    /* GCOVR_EXCL_BR_STOP */
    for (size_t index = 0; index < children->attr_count; index++) {
        th_node *child = children->attrs[index].node;
        const int kind = children->attrs[index].kind;
        if (kind == XSD_ATTRIBUTE) {
            const xnode *use = children->attrs[index].own;
            xsd_validate_attr_decl(ctx, instance, use, attrs);
            if (use->want.local != NULL) {
                declared->items[declared->count++] = use->want;
            }
        } else if (kind == XSD_ATTRIBUTE_GROUP) {
            Py_ssize_t ref_len = 0;
            const Py_UCS4 *ref = xsd_attr(tree, child, "ref", &ref_len);
            if (ref != NULL) {
                const Py_UCS4 *local, *prefix;
                Py_ssize_t local_len = 0, prefix_len = 0;
                split_prefix(ref, ref_len, &local, &local_len, &prefix, &prefix_len);
                /* xsd_compile has already rejected an attributeGroup ref that resolves to nothing */
                xsd_collect_attrs(ctx, instance, named_find(&schema->attr_groups, local, local_len), declared, attrs,
                                  wilds);
            }
        } else if (kind == XSD_ANY_ATTRIBUTE) {
            edecl item = {NULL, 0, NULL, 0, child};
            edecl_push(schema, wilds, &item);
        } else if (child == content) {
            th_node *derivation = xsd_child(schema, child, XSD_EXTENSION);
            if (derivation == NULL) {
                derivation = xsd_child(schema, child, XSD_RESTRICTION);
            }
            if (derivation != NULL) {
                Py_ssize_t base_len = 0;
                const Py_UCS4 *base = xsd_attr(tree, derivation, "base", &base_len);
                if (base != NULL) {
                    const qname written = schema_node_entry(schema, derivation)->base;
                    const Py_UCS4 *local = written.local;
                    Py_ssize_t local_len = written.local_len;
                    th_node *base_type = named_find(&schema->complex_types, local, local_len);
                    if (base_type != NULL) {
                        xsd_collect_attrs(ctx, instance, base_type, declared, attrs, wilds);
                    }
                }
                xsd_collect_attrs(ctx, instance, derivation, declared, attrs, wilds);
            }
        }
    }
}

/* An attribute an xs:anyAttribute admits without a declaration. A global attribute declaration is in the target
   namespace, so an attribute finds one only when its prefix binds that namespace (an unprefixed one, none). */
static void xsd_validate_wild_attr(valctx *ctx, th_node *instance, th_node *wild, const th_node_attr *present,
                                   const char *name, const xattr_name *qualified) {
    th_schema *schema = ctx->schema;
    const int mode = xsd_process_contents(schema, wild);
    if (mode == WILD_SKIP) {
        return;
    }
    th_node *decl = NULL;
    if (u_eq_u(qualified->uri, qualified->uri_len, schema->target_ns, schema->target_ns_len)) {
        Py_UCS4 *scratch = arena_alloc(&schema->mem, (size_t)(qualified->local_len + 1) * sizeof(Py_UCS4));
        if (scratch == NULL) { /* GCOVR_EXCL_BR_LINE: arena allocation failure */
            ctx->failed = 1;   /* GCOVR_EXCL_LINE */
            PyErr_NoMemory();  /* GCOVR_EXCL_LINE */
            return;            /* GCOVR_EXCL_LINE */
        }
        Py_ssize_t len = utf8_to_ucs4(qualified->local, qualified->local_len, scratch);
        decl = named_find(&schema->attributes, scratch, len);
    }
    if (decl != NULL) {
        xsd_validate_attr_value(ctx, instance, decl, present, name);
    } else if (mode == WILD_STRICT) {
        report(ctx, instance, "structure", "attribute '%s' is not declared", name);
    }
}

/* The first wildcard whose namespace constraint admits an attribute in `uri`, in the order the walk collected them. */
static th_node *xsd_admitting_wild(const th_schema *schema, const edecl_vec *wilds, const Py_UCS4 *uri,
                                   Py_ssize_t uri_len) {
    for (Py_ssize_t index = 0; index < wilds->count; index++) {
        th_node *wild = wilds->items[index].decl;
        if (xsd_wild_admits(schema, wild, uri, uri_len)) {
            return wild;
        }
    }
    return NULL;
}

static void xsd_validate_attrs(valctx *ctx, th_node *instance, th_node *scope, edecl_vec *declared) {
    th_tree *inst = ctx->tree;
    xattr_index attrs = {0};
    if (xsd_index_attrs(ctx, instance, &attrs) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        ctx->failed = 1;                              /* GCOVR_EXCL_LINE: allocation failure */
        return;                                       /* GCOVR_EXCL_LINE: allocation failure */
    }
    edecl_vec wilds = edecl_list(ctx->schema);
    xsd_collect_attrs(ctx, instance, scope, declared, &attrs, &wilds);
    named_vec names = {0};
    if (declared->count >= 32 && instance->attr_count >= 32) {
        names = ctx->schema->attr_names; /* the buffers of the element indexed before this one */
        names.len = 0;
        for (Py_ssize_t index = 0; index < declared->count; index++) {
            edecl *item = &declared->items[index];
            /* GCOVR_EXCL_BR_START: arena allocation failure */
            if (named_push(ctx->schema, &names, item->local, item->local_len, item->decl) < 0) {
                ctx->failed = 1;  /* GCOVR_EXCL_LINE: arena allocation failure */
                PyErr_NoMemory(); /* GCOVR_EXCL_LINE: arena allocation failure */
                return;           /* GCOVR_EXCL_LINE: arena allocation failure */
            }
            /* GCOVR_EXCL_BR_STOP */
        }
        if (named_index(ctx->schema, &names) < 0) { /* GCOVR_EXCL_BR_LINE: arena allocation failure */
            ctx->failed = 1;                        /* GCOVR_EXCL_LINE */
            PyErr_NoMemory();                       /* GCOVR_EXCL_LINE */
            return;                                 /* GCOVR_EXCL_LINE */
        }
        ctx->schema->attr_names = names;
    }
    for (Py_ssize_t index = 0; index < instance->attr_count; index++) {
        Py_ssize_t alen = 0;
        const char *abytes = th_attr_name(inst, instance->attrs[index].name_atom, &alen);
        if (is_xmlns_decl(abytes, alen)) {
            continue;
        }
        const xattr_name qualified = xsd_attr_name(ctx->schema, abytes, alen);
        if (xsd_attr_exempt(&qualified)) {
            continue;
        }
        int known = 0;
        if (names.slots != NULL) {
            uint64_t hash = UINT64_C(1469598103934665603);
            for (Py_ssize_t byte = 0; byte < qualified.local_len; byte++) {
                hash ^= (unsigned char)qualified.local[byte];
                hash *= UINT64_C(1099511628211);
            }
            size_t slot = (size_t)hash & (names.slot_cap - 1);
            while (names.slots[slot] != NULL) {
                named_node *item = names.slots[slot];
                Py_ssize_t decl_uri_len = 0;
                const Py_UCS4 *decl_uri = xsd_attr_decl_uri(ctx->schema, item->node, &decl_uri_len);
                if (u_eq_utf8(item->name, item->len, qualified.local, qualified.local_len) &&
                    u_eq_u(qualified.uri, qualified.uri_len, decl_uri, decl_uri_len)) {
                    known = 1;
                    break;
                }
                slot = (slot + 1) & (names.slot_cap - 1);
            }
        }
        for (Py_ssize_t decl = 0; names.slots == NULL && decl < declared->count; decl++) {
            const edecl *item = &declared->items[decl];
            if (u_eq_utf8(item->local, item->local_len, qualified.local, qualified.local_len) &&
                u_eq_u(qualified.uri, qualified.uri_len, item->uri, item->uri_len)) {
                known = 1;
                break;
            }
        }
        if (!known) {
            th_node *wild = xsd_admitting_wild(ctx->schema, &wilds, qualified.uri, qualified.uri_len);
            if (wild == NULL) {
                report(ctx, instance, "structure", "attribute '%s' is not declared", abytes);
            } else {
                xsd_validate_wild_attr(ctx, instance, wild, &instance->attrs[index], abytes, &qualified);
            }
        }
    }
}

/* ---- complex-type content ---- */

static th_node *xsd_content_model(const th_schema *schema, th_node *scope) {
    const xchild_entry *children = xsd_child_entry(schema, scope);
    for (int kind = XSD_SEQUENCE; kind <= XSD_GROUP; kind++) {
        if (children->first[kind] != NULL) {
            return children->first[kind];
        }
    }
    return NULL;
}

/* The complexType a complexContent extension of ctype derives from, or NULL when it names none. */
static th_node *xsd_extension_base(th_schema *schema, th_node *ctype) {
    th_node *complex_content = first_schema_child(schema, ctype, XSD_NS, "complexContent");
    th_node *extension =
        complex_content != NULL ? first_schema_child(schema, complex_content, XSD_NS, "extension") : NULL;
    Py_ssize_t base_len = 0;
    const Py_UCS4 *base = extension != NULL ? xsd_attr(schema->tree, extension, "base", &base_len) : NULL;
    if (base == NULL) {
        return NULL;
    }
    const Py_UCS4 *local, *prefix;
    Py_ssize_t local_len = 0, prefix_len = 0;
    split_prefix(base, base_len, &local, &local_len, &prefix, &prefix_len);
    return named_find(&schema->complex_types, local, local_len);
}

/* The content model a complexType declares, or else the one nearest to it along its extension chain. */
static th_node *xsd_nearest_model(th_schema *schema, th_node *ctype) {
    th_node *complex_content = first_schema_child(schema, ctype, XSD_NS, "complexContent");
    if (complex_content == NULL) {
        return xsd_content_model(schema, ctype);
    }
    th_node *extension = first_schema_child(schema, complex_content, XSD_NS, "extension");
    th_node *derivation =
        extension != NULL ? extension : first_schema_child(schema, complex_content, XSD_NS, "restriction");
    th_node *model = derivation != NULL ? xsd_content_model(schema, derivation) : NULL;
    if (model != NULL || extension == NULL) {
        return model;
    }
    th_node *base = xsd_extension_base(schema, ctype);
    return base != NULL ? xsd_nearest_model(schema, base) : NULL;
}

/* The content a complexType with no particles of its own inherits: the nearest model, with a group ref resolved to its
   model group. */
static TH_NOINLINE th_node *xsd_inherited_model(th_schema *schema, th_node *ctype) {
    th_node *model = xsd_nearest_model(schema, ctype);
    if (model != NULL && is_schema_el(schema, model, XSD_NS, "group")) {
        return xsd_group_model(schema, model);
    }
    return model;
}

/* The effective content of a complexType. Returns its own particle, NULL for empty/simple content. A complexContent
   extension appends that particle to its base type's content, which the base may itself have extended, so the particles
   inherited along the whole base chain are counted in *base_count and, when base_models is not NULL, stored there
   root-most type first. A restriction restates its content, which ends the climb. */
static th_node *xsd_effective_model(th_schema *schema, th_node *ctype, th_node **base_models, Py_ssize_t *base_count) {
    th_tree *tree = schema->tree;
    th_node *complex_content = xsd_child(schema, ctype, XSD_COMPLEX_CONTENT);
    if (complex_content != NULL) {
        th_node *extension = xsd_child(schema, complex_content, XSD_EXTENSION);
        th_node *derivation = extension != NULL ? extension : xsd_child(schema, complex_content, XSD_RESTRICTION);
        if (derivation == NULL) {
            return NULL;
        }
        if (extension != NULL) {
            Py_ssize_t base_len = 0;
            const Py_UCS4 *base = xsd_attr(tree, derivation, "base", &base_len);
            if (base != NULL) {
                const Py_UCS4 *local, *prefix;
                Py_ssize_t local_len = 0, prefix_len = 0;
                split_prefix(base, base_len, &local, &local_len, &prefix, &prefix_len);
                th_node *base_type = named_find(&schema->complex_types, local, local_len);
                th_node *base_model =
                    base_type != NULL ? xsd_effective_model(schema, base_type, base_models, base_count) : NULL;
                if (base_model != NULL) {
                    if (base_models != NULL) {
                        base_models[*base_count] = base_model;
                    }
                    (*base_count)++;
                }
            }
        }
        return xsd_content_model(schema, derivation);
    }
    return xsd_content_model(schema, ctype);
}

static int xsd_is_mixed(const th_schema *schema, th_node *ctype) {
    th_tree *tree = schema->tree;
    if (xsd_attr_is(tree, ctype, "mixed", "true")) {
        return 1;
    }
    th_node *complex_content = xsd_child(schema, ctype, XSD_COMPLEX_CONTENT);
    if (complex_content != NULL) {
        return xsd_attr_is(tree, complex_content, "mixed", "true");
    }
    return 0;
}

/* Validate an element against its complex type. Its child elements are checked against the content model here and
   queued on the frame; xsd_frame_next hands each one out with the declaration that types it. */
/* The declaration a child claimed by a wildcard validates against: the global element of its name. Strict requires
   one, lax validates against it when present, and skip validates nothing. */
static th_node *xsd_wild_decl(valctx *ctx, th_node *child, th_node *wild) {
    th_schema *schema = ctx->schema;
    const int mode = xsd_process_contents(schema, wild);
    if (mode == WILD_SKIP) {
        return NULL;
    }
    qname name = element_qname_scoped(schema, ctx->tree, child);
    th_node *decl = named_find(&schema->elements, name.local, name.local_len);
    if (decl != NULL) {
        edecl want;
        xsd_element_name(schema, decl, &want);
        if (!qname_eq(&name, &want)) {
            decl = NULL;
        }
    }
    if (decl == NULL && mode == WILD_STRICT) {
        char buffer[256];
        report(ctx, child, "structure", "no global element declaration for '%s'",
               name_utf8(child->text, child->text_len, buffer, sizeof(buffer)));
    }
    return decl;
}

static void xsd_validate_complex(valctx *ctx, th_node *instance, th_node *ctype, xsd_frame *frame) {
    th_schema *schema = ctx->schema;
    th_tree *inst = ctx->tree;
    edecl_vec declared = edecl_list(schema);
    xsd_validate_attrs(ctx, instance, ctype, &declared);

    th_node *simple_content = xsd_child(schema, ctype, XSD_SIMPLE_CONTENT);
    if (simple_content != NULL) {
        for (th_node *child = instance->first_child; child != NULL; child = child->next_sibling) {
            if (child->type == TH_NODE_ELEMENT) {
                report(ctx, child, "structure", "simple-content element must not contain child elements");
                break;
            }
        }
        Py_ssize_t text_len = 0;
        const Py_UCS4 *text = element_text(ctx, instance, &text_len);
        xsd_validate_simple(ctx, instance, DT_STRING, simple_content, text, text_len);
        return;
    }

    Py_ssize_t nbase = 0;
    th_node *model = xsd_effective_model(schema, ctype, NULL, &nbase);
    int mixed = xsd_is_mixed(schema, ctype);
    if (!mixed) {
        for (th_node *child = instance->first_child; child != NULL; child = child->next_sibling) {
            if (!is_chardata(child)) {
                continue;
            }
            int blank = th_node_text_is_blank(inst, child);
            if (blank < 0) {      /* GCOVR_EXCL_BR_LINE: lazy text OOM */
                ctx->failed = 1;  /* GCOVR_EXCL_LINE: allocation failure */
                PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation failure */
                return;           /* GCOVR_EXCL_LINE: allocation failure */
            }
            if (!blank) {
                report(ctx, instance, "structure", "element must not contain character data");
                break;
            }
        }
    }

    Py_ssize_t nchildren = 0;
    for (th_node *child = instance->first_child; child != NULL; child = child->next_sibling) {
        if (child->type == TH_NODE_ELEMENT) {
            nchildren++;
        }
    }
    th_node **children = arena_alloc(&schema->mem, (size_t)(nchildren + 1) * sizeof(th_node *));
    if (children == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return;             /* GCOVR_EXCL_LINE */
    }
    /* the content model matcher compares each child's name at every position it tries, so each is resolved once */
    qname *names = arena_alloc(&schema->mem, (size_t)(nchildren + 1) * sizeof(qname));
    if (names == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return;          /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t at = 0;
    for (th_node *child = instance->first_child; child != NULL; child = child->next_sibling) {
        if (child->type == TH_NODE_ELEMENT) {
            names[at] = element_qname_scoped(schema, inst, child);
            children[at++] = child;
        }
    }
    schema->child_names = names;

    th_node *content = model == NULL && schema->all_inherited ? xsd_nearest_model(schema, ctype) : model;
    th_node *all = NULL;
    if (content != NULL && is_schema_el(schema, content, XSD_NS, "all")) {
        all = content;
    } else if (content != NULL && schema->named_all && is_schema_el(schema, content, XSD_NS, "group")) {
        all = xsd_group_all(schema, content);
    }
    if (all != NULL) {
        /* an optional xs:all, direct or through a group ref, is satisfied by empty content */
        if (nchildren > 0 || xsd_occurs_min(schema->tree, content) != 0) {
            frame->instance = instance;
            xsd_all_match(ctx, frame, all, children, nchildren);
        }
        return;
    }
    if (model == NULL && nbase == 0) {
        if (nchildren > 0) {
            report(ctx, instance, "structure", "element must be empty");
        }
        return;
    }

    th_node **claims = xsd_wild_claims(schema, (size_t)nchildren + 1);
    if (claims == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return;           /* GCOVR_EXCL_LINE */
    }
    /* every particle of the content in order: what the base chain supplies, then the type's own */
    th_node **models = arena_alloc(&schema->mem, (size_t)(nbase + 1) * sizeof(th_node *));
    posset start = posset_alloc(schema, nchildren + 1);
    posset end = posset_alloc(schema, nchildren + 1);
    if (models == NULL || start.member == NULL || end.member == NULL) { /* GCOVR_EXCL_BR_LINE: OOM is unforceable */
        return;                                                         /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t nmodels = 0;
    if (nbase > 0) {
        xsd_effective_model(schema, ctype, models, &nmodels);
    }
    if (model != NULL) {
        models[nmodels++] = model;
    }
    posset_add(&end, 0);
    for (Py_ssize_t index = 0; index < nmodels; index++) { /* each particle starts where the one before it stopped */
        posset spare = start;
        start = end;
        end = spare;
        xsd_match_occurs(schema, inst, models[index], children, &start, &end);
    }

    if (!end.member[nchildren]) {
        report(ctx, instance, "structure", "content of element does not match its content model");
    }

    if (nchildren == 0) {
        return;
    }
    /* Each child's declaration is found before any child is validated, since validating one refills the list. */
    th_node **child_decls = arena_alloc(&schema->mem, (size_t)nchildren * sizeof(th_node *));
    if (child_decls == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return;                /* GCOVR_EXCL_LINE */
    }
    edecl_vec decls = edecl_list(schema);
    for (Py_ssize_t index = 0; index < nmodels; index++) {
        xsd_collect_edecls(schema, models[index], &decls);
    }
    for (Py_ssize_t index = 0; index < nchildren; index++) {
        child_decls[index] = NULL;
        for (Py_ssize_t decl = 0; decl < decls.count; decl++) {
            if (qname_eq(&names[index], &decls.items[decl])) {
                child_decls[index] = decls.items[decl].decl;
                break;
            }
        }
    }
    for (Py_ssize_t index = 0; index < nchildren; index++) {
        if (child_decls[index] == NULL && claims[index] != NULL) {
            child_decls[index] = xsd_wild_decl(ctx, children[index], claims[index]);
        }
    }
    frame->children = children;
    frame->nchildren = nchildren;
    frame->decls = child_decls;
}

/* ---- element ---- */

/* The next queued child that a declaration types, with that declaration in *decl, or NULL once the element's
   children are done. A child the content model does not name has no type to validate against and is passed over. */
static th_node *xsd_frame_next(valctx *ctx, xsd_frame *frame, th_node **decl) {
    while (frame->next < frame->nchildren) {
        const Py_ssize_t position = frame->next++;
        th_node *child = frame->children[position];
        if (frame->all != NULL) {
            TH_WORK_STEP();
            if (frame->matched[position] == NULL) {
                char buffer[256];
                report(ctx, child, "structure", "element '%s' is not allowed here",
                       name_utf8(child->text, child->text_len, buffer, sizeof(buffer)));
                continue;
            }
            *decl = frame->matched[position]->want.decl;
            return child;
        }
        *decl = frame->decls[position];
        if (*decl != NULL) {
            return child;
        }
    }
    if (frame->all != NULL) {
        xsd_all_missing(ctx, frame);
    }
    return NULL;
}

/* XSD 1.0 §3.3.4 allows a simple-typed element no attribute except these four xsi ones. */
static int xsd_simple_attr_allowed(const qname *name) {
    if (name->uri == NULL || !u_eq_ascii(name->uri, name->uri_len, XSI_NS)) {
        return 0;
    }
    return u_eq_ascii(name->local, name->local_len, "type") || u_eq_ascii(name->local, name->local_len, "nil") ||
           u_eq_ascii(name->local, name->local_len, "schemaLocation") ||
           u_eq_ascii(name->local, name->local_len, "noNamespaceSchemaLocation");
}

static TH_NOINLINE void xsd_check_simple_attrs(valctx *ctx, th_node *instance) {
    th_schema *schema = ctx->schema;
    for (Py_ssize_t index = 0; index < instance->attr_count; index++) {
        Py_ssize_t alen = 0;
        const char *abytes = th_attr_name(ctx->tree, instance->attrs[index].name_atom, &alen);
        if (is_xmlns_decl(abytes, alen)) {
            continue;
        }
        Py_UCS4 *scratch = arena_alloc(&schema->mem, (size_t)(alen + 1) * sizeof(Py_UCS4));
        if (scratch == NULL) { /* GCOVR_EXCL_BR_LINE: arena allocation failure */
            ctx->failed = 1;   /* GCOVR_EXCL_LINE */
            PyErr_NoMemory();  /* GCOVR_EXCL_LINE */
            return;            /* GCOVR_EXCL_LINE */
        }
        Py_ssize_t nlen = utf8_to_ucs4(abytes, alen, scratch);
        qname name = node_qname_scoped(schema, scratch, nlen, 1);
        if (!xsd_simple_attr_allowed(&name)) {
            report(ctx, instance, "structure", "simple-typed element must not have attributes");
            return;
        }
    }
}

/* Validate one element against its declaration, queuing its child elements on the frame. */
static void xsd_validate_element(valctx *ctx, th_node *instance, th_node *decl, xsd_frame *frame) {
    th_schema *schema = ctx->schema;
    th_tree *tree = schema->tree;
    frame->ns_mark = ns_scope_enter(schema, ctx->tree, instance);
    /* the error path spells local names, so the element's namespace is not resolved here */
    const Py_UCS4 *local, *prefix;
    Py_ssize_t local_len = 0, prefix_len = 0;
    split_prefix(instance->text, instance->text_len, &local, &local_len, &prefix, &prefix_len);
    frame->mark = ctx->path.len;
    frame->next = 0;
    frame->nchildren = 0;
    frame->all = NULL;
    if (path_push(&ctx->path, local, local_len) < 0) { /* GCOVR_EXCL_BR_LINE: path OOM is unforceable */
        ctx->failed = 1;                               /* GCOVR_EXCL_LINE */
        PyErr_NoMemory();                              /* GCOVR_EXCL_LINE */
        return;                                        /* GCOVR_EXCL_LINE */
    }

    Py_ssize_t type_len = 0;
    const Py_UCS4 *type = xsd_attr(tree, decl, "type", &type_len);
    if (type != NULL) {
        const qname written = schema_node_entry(schema, decl)->type;
        const Py_UCS4 *local = written.local, *uri = written.uri;
        Py_ssize_t local_len = written.local_len, uri_len = written.uri_len;
        int is_xsd_ns = is_xsd_uri(uri, uri_len);
        th_node *complex = is_xsd_ns ? NULL : named_find(&schema->complex_types, local, local_len);
        if (complex != NULL) {
            xsd_validate_complex(ctx, instance, complex, frame);
        } else {
            th_node *simple = is_xsd_ns ? NULL : named_find(&schema->simple_types, local, local_len);
            if (instance->attr_count > 0) {
                xsd_check_simple_attrs(ctx, instance);
            }
            for (th_node *child = instance->first_child; child != NULL; child = child->next_sibling) {
                if (child->type == TH_NODE_ELEMENT) {
                    report(ctx, child, "structure", "simple-typed element must not contain child elements");
                    break;
                }
            }
            Py_ssize_t text_len = 0;
            const Py_UCS4 *text = element_text(ctx, instance, &text_len);
            xsd_validate_simple(ctx, instance, is_xsd_ns ? xsd_builtin_id(local, local_len) : DT_STRING, simple, text,
                                text_len);
        }
        return;
    }
    const xchild_entry *inline_types = xsd_child_entry(schema, decl);
    if (inline_types->first[XSD_COMPLEX_TYPE] != NULL) {
        xsd_validate_complex(ctx, instance, inline_types->first[XSD_COMPLEX_TYPE], frame);
    } else if (inline_types->first[XSD_SIMPLE_TYPE] != NULL) {
        if (instance->attr_count > 0) {
            xsd_check_simple_attrs(ctx, instance);
        }
        for (th_node *child = instance->first_child; child != NULL; child = child->next_sibling) {
            if (child->type == TH_NODE_ELEMENT) {
                report(ctx, child, "structure", "simple-typed element must not contain child elements");
                break;
            }
        }
        Py_ssize_t text_len = 0;
        const Py_UCS4 *text = element_text(ctx, instance, &text_len);
        xsd_validate_simple(ctx, instance, DT_STRING, inline_types->first[XSD_SIMPLE_TYPE], text, text_len);
    }
}

/* Validate the children an element queued below the shallow levels, each one's children in turn, on the explicit stack.
   An element is validated on the way in and left once its queued children are, so errors come out in document order. */
static void xsd_validate_walk(valctx *ctx, const xsd_frame *base) {
    xsd_frame *open = NULL;
    Py_ssize_t depth = 0, cap = 0;
    open = walk_stack_reserve(&ctx->schema->mem, open, depth, &cap, sizeof(xsd_frame));
    if (open == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return;         /* GCOVR_EXCL_LINE: the validate entry raises the MemoryError */
    }
    open[depth++] = *base;
    for (;;) {
        th_node *child;
        th_node *decl;
        child = xsd_frame_next(ctx, &open[depth - 1], &decl);
        if (child != NULL) {
            open = walk_stack_reserve(&ctx->schema->mem, open, depth, &cap, sizeof(xsd_frame));
            if (open == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
                return;         /* GCOVR_EXCL_LINE: the validate entry raises the MemoryError */
            }
            xsd_validate_element(ctx, child, decl, &open[depth++]);
            continue;
        }
        ctx->path.len = open[depth - 1].mark;
        ns_scope_leave(ctx->schema, open[depth - 1].ns_mark);
        if (--depth == 0) {
            return;
        }
    }
}

/* Validate an element and everything below it, in document order. The first TH_VALIDATE_SHALLOW_DEPTH levels recurse
   on the C stack, where a frame costs nothing extra; deeper levels queue on the explicit stack in xsd_validate_walk. */
static void xsd_validate_tree(valctx *ctx, th_node *instance, th_node *decl, Py_ssize_t depth) {
    xsd_frame frame;
    xsd_validate_element(ctx, instance, decl, &frame);
    if (depth >= TH_VALIDATE_SHALLOW_DEPTH) {
        xsd_validate_walk(ctx, &frame);
        return;
    }
    th_node *child;
    th_node *child_decl;
    while ((child = xsd_frame_next(ctx, &frame, &child_decl)) != NULL) {
        xsd_validate_tree(ctx, child, child_decl, depth + 1);
    }
    ctx->path.len = frame.mark;
    ns_scope_leave(ctx->schema, frame.ns_mark);
}

/* ---- compile & entry ---- */

enum { CHAIN_GROUP, CHAIN_ATTRGROUP, CHAIN_TYPE };

static int xsd_check_chains(th_schema *schema, int kind);
static int xsd_check_expansion(th_schema *schema);
static int xsd_check_all_extension(th_schema *schema, th_node *node);
static int xsd_check_varieties(th_schema *schema);

static int xsd_compile(th_schema *schema) {
    th_tree *tree = schema->tree;
    if (!is_schema_el(schema, schema->root, XSD_NS, "schema")) {
        PyErr_SetString(PyExc_ValueError, "root element is not an xs:schema");
        return 0;
    }
    xsd_sort_attrs(schema);
    Py_ssize_t ns_len = 0;
    const Py_UCS4 *ns = xsd_attr(tree, schema->root, "targetNamespace", &ns_len);
    schema->target_ns = ns;
    schema->target_ns_len = ns == NULL ? 0 : ns_len;
    schema->element_qualified = xsd_attr_is(tree, schema->root, "elementFormDefault", "qualified");
    schema->attribute_qualified = xsd_attr_is(tree, schema->root, "attributeFormDefault", "qualified");

    for (th_node *child = schema->root->first_child; child != NULL; child = child->next_sibling) {
        if (child->type != TH_NODE_ELEMENT) {
            continue;
        }
        Py_ssize_t name_len = 0;
        const Py_UCS4 *name = xsd_attr(tree, child, "name", &name_len);
        named_vec *bucket = NULL;
        const char *kind = NULL;
        if (is_schema_el(schema, child, XSD_NS, "element")) {
            bucket = &schema->elements;
            kind = "element";
        } else if (is_schema_el(schema, child, XSD_NS, "complexType")) {
            bucket = &schema->complex_types;
            kind = "complexType";
        } else if (is_schema_el(schema, child, XSD_NS, "simpleType")) {
            bucket = &schema->simple_types;
            kind = "simpleType";
        } else if (is_schema_el(schema, child, XSD_NS, "attribute")) {
            bucket = &schema->attributes;
            kind = "attribute";
        } else if (is_schema_el(schema, child, XSD_NS, "group")) {
            bucket = &schema->groups;
            kind = "group";
        } else if (is_schema_el(schema, child, XSD_NS, "attributeGroup")) {
            bucket = &schema->attr_groups;
            kind = "attributeGroup";
        }
        if (bucket == NULL) {
            continue;
        }
        if (name == NULL || name_len == 0) {
            PyErr_Format(PyExc_ValueError, "global %s requires a name", kind);
            return 0;
        }
        named_push(schema, bucket, name, name_len, child);
    }
    named_vec *tables[] = {&schema->elements,   &schema->complex_types, &schema->simple_types,
                           &schema->attributes, &schema->groups,        &schema->attr_groups};
    for (size_t index = 0; index < sizeof(tables) / sizeof(tables[0]); index++) {
        if (named_index(schema, tables[index]) < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            PyErr_NoMemory();                         /* GCOVR_EXCL_LINE */
            return 0;                                 /* GCOVR_EXCL_LINE */
        }
    }
    /* the cycle checks below assume every group, attributeGroup and base ref resolves */
    if (xsd_cache_facets(schema, schema->root) < 0) {
        if (!PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: only an arena OOM returns without an exception set */
            PyErr_NoMemory();    /* GCOVR_EXCL_LINE */
        } /* GCOVR_EXCL_LINE: llvm attributes the unexecuted fall-through to this brace */
        return 0;
    }
    if (xsd_check_chains(schema, CHAIN_GROUP) < 0 || xsd_check_chains(schema, CHAIN_ATTRGROUP) < 0 ||
        xsd_check_chains(schema, CHAIN_TYPE) < 0 || xsd_check_expansion(schema) < 0 ||
        (schema->complex_extension && xsd_check_all_extension(schema, schema->root) < 0)) {
        return 0;
    }
    if (schema->facet_count > 1) {
        qsort(schema->facet_entries, schema->facet_count, sizeof(xfacet_entry), xsd_facet_order);
    }
    if (xsd_link_facets(schema) < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        PyErr_NoMemory();              /* GCOVR_EXCL_LINE */
        return 0;                      /* GCOVR_EXCL_LINE */
    }
    if (xsd_check_varieties(schema) < 0) {
        return 0;
    }
    xsd_resolve_decls(schema);
    if (schema->all_count > 1) {
        qsort(schema->all_entries, schema->all_count, sizeof(xall_entry), xsd_all_order);
    }
    return 1;
}

/* How far a chain reaches: the refs it follows and the particles it nests on the way. The matcher recurses once per
   particle of every group it crosses, not once per ref, so a group chain is held to both. */
typedef struct {
    int hops;
    int levels;
} chain_len;

static named_vec *xsd_chain_vec(th_schema *schema, int kind);
static int xsd_chain_visit(th_schema *schema, int kind, Py_ssize_t start, chain_len *state);
static int xsd_check_group_uses(th_schema *schema, const chain_len *state);

/* The node after `node` in document order within `root`'s subtree, or NULL at its end, with `*depth` moved by the
   levels the step goes down or up. */
static th_node *xsd_walk_depth(const th_node *root, th_node *node, int *depth) {
    if (node->first_child != NULL) {
        (*depth)++;
        return node->first_child;
    }
    while (node != root && node->next_sibling == NULL) {
        node = node->parent;
        (*depth)--;
    }
    return node == root ? NULL : node->next_sibling;
}

/* The depth of the deepest element under `root`, counting `root` as 1. */
static int xsd_tree_height(th_node *root) {
    int depth = 1, height = 1;
    for (th_node *node = root; node != NULL; node = xsd_walk_depth(root, node, &depth)) {
        if (node->type == TH_NODE_ELEMENT && depth > height) {
            height = depth;
        }
    }
    return height;
}

static int xsd_check_chains(th_schema *schema, int kind) {
    named_vec *vec = xsd_chain_vec(schema, kind);
    if (vec->len == 0) {
        return 0;
    }
    chain_len *state = arena_alloc(&schema->mem, (size_t)vec->len * sizeof(chain_len));
    if (state == NULL) {  /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
        return -1;        /* GCOVR_EXCL_LINE */
    }
    memset(state, 0, (size_t)vec->len * sizeof(chain_len));
    for (Py_ssize_t index = 0; index < vec->len; index++) {
        if (state[index].hops == 0 && xsd_chain_visit(schema, kind, index, state) < 0) {
            return -1;
        }
    }
    if (kind != CHAIN_GROUP) {
        return 0;
    }
    int deepest = 0;
    for (Py_ssize_t index = 0; index < vec->len; index++) {
        if (state[index].levels > deepest) {
            deepest = state[index].levels;
        }
    }
    /* a ref sits no deeper than the tree, so the walk can refuse only when the tree and the deepest chain together
       reach the limit */
    if (deepest + xsd_tree_height(schema->root) < TH_VALIDATE_MAX_DEPTH) {
        return 0;
    }
    return xsd_check_group_uses(schema, state);
}

static named_vec *xsd_chain_vec(th_schema *schema, int kind) {
    if (kind == CHAIN_GROUP) {
        return &schema->groups;
    }
    if (kind == CHAIN_ATTRGROUP) {
        return &schema->attr_groups;
    }
    return &schema->complex_types;
}

static const char *xsd_chain_kind(int kind);

/* Validation follows these refs by C recursion (xsd_collect_edecls, xsd_match_once, xsd_effective_model,
   xsd_collect_attrs). Well above any real schema (xmlschema already fails below 200 hops) and far below the smallest
   supported thread stack. */
#define TH_XSD_MAX_REF_DEPTH TH_DEPTH_LIMIT(100)

/* Whether a chain is past a cap, with the error set if so. Hops alone do not bound the matcher: every group on a chain
   can hold its ref under as many particles as the tree limit leaves room for, so the particles a chain nests are held
   to the depth a schema tree may have without refs. */
static int xsd_chain_too_long(int kind, chain_len length) {
    if (length.hops > TH_XSD_MAX_REF_DEPTH) {
        PyErr_Format(PyExc_ValueError, "%s reference chain exceeds %d hops; flatten the schema or shorten the chain",
                     xsd_chain_kind(kind), TH_XSD_MAX_REF_DEPTH);
        return 1;
    }
    if (length.levels >= TH_VALIDATE_MAX_DEPTH) {
        PyErr_Format(PyExc_ValueError,
                     "%s reference chain nests %d levels or deeper; flatten the schema or shorten the chain",
                     xsd_chain_kind(kind), TH_VALIDATE_MAX_DEPTH);
        return 1;
    }
    return 0;
}

/* A definition the chain check is inside, with the refs below it still to follow. */
typedef struct {
    Py_ssize_t index;  /* the definition's slot in the table of its kind */
    th_node *cursor;   /* the next node under the definition to look at for a ref, NULL once all are seen */
    chain_len depth;   /* the hops and particles from the definition the check started at down to this one */
    chain_len longest; /* the longest chain below the definition found so far */
    int level;         /* the particles between the definition and the cursor */
    int ref_level;     /* the particles from the definition down to the ref last returned, counting the ref */
} xchain_frame;

/* Folds a chain that starts at the ref `frame` last returned into the longest one below its definition. */
static void xsd_chain_reach(xchain_frame *frame, const chain_len *below) {
    if (below->hops > frame->longest.hops) {
        frame->longest.hops = below->hops;
    }
    if (frame->ref_level + below->levels > frame->longest.levels) {
        frame->longest.levels = frame->ref_level + below->levels;
    }
}

/* The complex type a type derives from: A-extends-B-extends-A is a cycle; a built-in or simple-type base, or none, ends
   the chain with -1. */
static Py_ssize_t xsd_type_base_index(th_schema *schema, th_node *ctype) {
    th_node *content = xsd_type_content(schema, ctype);
    if (content == NULL) {
        return -1;
    }
    th_node *deriv = first_schema_child(schema, content, XSD_NS, "extension");
    if (deriv == NULL) {
        deriv = first_schema_child(schema, content, XSD_NS, "restriction");
    }
    if (deriv == NULL) {
        return -1;
    }
    Py_ssize_t base_len = 0;
    const Py_UCS4 *base = xsd_attr(schema->tree, deriv, "base", &base_len);
    if (base == NULL) {
        return -1;
    }
    const Py_UCS4 *local, *prefix;
    Py_ssize_t local_len = 0, prefix_len = 0;
    split_prefix(base, base_len, &local, &local_len, &prefix, &prefix_len);
    return named_find_index(&schema->complex_types, local, local_len);
}

/* The definition the next ref under `frame` names, or -1 once none is left. A type derives from a single base. Group
   refs sit inside model groups, so those are descended and each counts as a particle the matcher nests; an element is
   a leaf since no ref reaches across one. */
static Py_ssize_t xsd_chain_next(th_schema *schema, int kind, xchain_frame *frame) {
    named_vec *vec = xsd_chain_vec(schema, kind);
    th_node *definition = vec->items[frame->index].node;
    if (kind == CHAIN_TYPE) {
        if (frame->cursor == NULL) {
            return -1;
        }
        frame->cursor = NULL;
        return xsd_type_base_index(schema, definition);
    }
    const char *ref_el = kind == CHAIN_GROUP ? "group" : "attributeGroup";
    while (frame->cursor != NULL) {
        th_node *child = frame->cursor;
        const int level = frame->level + 1;
        if (kind == CHAIN_GROUP && child->first_child != NULL && xsd_is_particle(schema, child, XSD_MODEL_GROUPS, 3)) {
            frame->cursor = child->first_child;
            frame->level = level;
        } else {
            th_node *walk = child;
            while (walk != definition && walk->next_sibling == NULL) {
                walk = walk->parent;
                frame->level--;
            }
            frame->cursor = walk == definition ? NULL : walk->next_sibling;
        }
        if (child->type != TH_NODE_ELEMENT) {
            continue;
        }
        if (level > frame->longest.levels) {
            frame->longest.levels = level;
        }
        Py_ssize_t ref_len = 0;
        const Py_UCS4 *ref =
            is_schema_el(schema, child, XSD_NS, ref_el) ? xsd_attr(schema->tree, child, "ref", &ref_len) : NULL;
        if (ref != NULL) {
            const Py_UCS4 *local, *prefix;
            Py_ssize_t local_len = 0, prefix_len = 0;
            split_prefix(ref, ref_len, &local, &local_len, &prefix, &prefix_len);
            frame->ref_level = level;
            return named_find_index(vec, local, local_len);
        }
    }
    return -1;
}

/* state[index].hops is 0 unvisited, -1 on the DFS stack (a back edge is a cycle), else state[index] is the resolved
   chain length. The caps apply on the way down and again to the resolved length, which also catches a chain reached
   through memoized definitions. Group definitions nest model groups as deep as the schema between hops, so the walk
   keeps the definitions it is inside on an explicit stack and its scan of each one on parent links. */
static int xsd_chain_visit(th_schema *schema, int kind, Py_ssize_t start, chain_len *state) {
    named_vec *vec = xsd_chain_vec(schema, kind);
    xchain_frame local[16];
    xchain_frame *stack = local;
    Py_ssize_t depth = 1, cap = 16;
    th_node *first = kind == CHAIN_TYPE ? vec->items[start].node : vec->items[start].node->first_child;
    stack[0] = (xchain_frame){start, first, {0, 0}, {0, 0}, 0, 0};
    state[start].hops = -1;
    int status = -1;
    for (;;) {
        xchain_frame *top = &stack[depth - 1];
        const Py_ssize_t target = xsd_chain_next(schema, kind, top);
        if (target >= 0) {
            if (state[target].hops == -1) {
                char buffer[256];
                PyErr_Format(PyExc_ValueError, "circular %s reference to '%s'", xsd_chain_kind(kind),
                             name_utf8(vec->items[target].name, vec->items[target].len, buffer, sizeof(buffer)));
                break;
            }
            if (state[target].hops > 0) {
                xsd_chain_reach(top, &state[target]);
                continue;
            }
            const chain_len below = {top->depth.hops + 1, top->depth.levels + top->ref_level};
            if (xsd_chain_too_long(kind, (chain_len){below.hops + 1, below.levels})) {
                break;
            }
            xchain_frame *grown = walk_reserve(stack, local, depth, &cap, sizeof(xchain_frame));
            if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                break;           /* GCOVR_EXCL_LINE */
            }
            stack = grown;
            th_node *definition = vec->items[target].node;
            stack[depth] =
                (xchain_frame){target, kind == CHAIN_TYPE ? definition : definition->first_child, below, {0, 0}, 0, 0};
            depth++;
            state[target].hops = -1;
            continue;
        }
        top->longest.hops++;
        if (xsd_chain_too_long(kind, top->longest)) {
            break;
        }
        state[top->index] = top->longest;
        if (--depth == 0) {
            status = 0;
            break;
        }
        xsd_chain_reach(&stack[depth - 1], &state[top->index]);
    }
    walk_release(stack, local);
    return status;
}

/* A group is matched wherever a ref names it, below the particles that ref already sits under, so every ref in the tree
   is held to the depth limit with its chain expanded in place. th_node_check_max_depth counts the document as depth 0,
   which puts xs:schema at 1. */
static int xsd_check_group_uses(th_schema *schema, const chain_len *state) {
    th_node *root = schema->root;
    int depth = 1;
    for (th_node *node = xsd_walk_depth(root, root, &depth); node != NULL; node = xsd_walk_depth(root, node, &depth)) {
        Py_ssize_t ref_len = 0;
        const Py_UCS4 *ref =
            is_schema_el(schema, node, XSD_NS, "group") ? xsd_attr(schema->tree, node, "ref", &ref_len) : NULL;
        if (ref != NULL) {
            const Py_UCS4 *local, *prefix;
            Py_ssize_t local_len = 0, prefix_len = 0;
            split_prefix(ref, ref_len, &local, &local_len, &prefix, &prefix_len);
            Py_ssize_t target = named_find_index(&schema->groups, local, local_len);
            if (xsd_chain_too_long(CHAIN_GROUP, (chain_len){0, depth + state[target].levels})) {
                return -1;
            }
        }
    }
    return 0;
}

static const char *xsd_chain_kind(int kind) {
    if (kind == CHAIN_GROUP) {
        return "group";
    }
    if (kind == CHAIN_ATTRGROUP) {
        return "attribute group";
    }
    return "complex type";
}

/* The chain checks bound how deep references go, not how much they unfold to in total. */
#define TH_XSD_MAX_EXPANSION 20000

static int xsd_expansion_cap(int total) {
    return total > TH_XSD_MAX_EXPANSION ? TH_XSD_MAX_EXPANSION + 1 : total;
}

/* A scope or particle the expansion count is inside. A scope is a complex type, a derivation or a referenced
   definition, and a particle a model group; each counts its children. */
typedef struct {
    th_node *next;       /* the next child to count */
    th_node *content;    /* a complex type's derivation slot */
    th_node *derivation; /* a derivation whose own children are counted once its base is */
    int *memo;           /* where a referenced definition's size goes, NULL for an inline scope */
    int total, own;
    int bias;       /* added to the count when it goes to the level above */
    int particles;  /* a scope that holds particles; a group holds no attributes and an attributeGroup no particles */
    int attributes; /* a scope that holds attributes; a particle frame holds neither and counts its particle children */
} xexp_frame;

/* Puts `node` on the stack; `scope` picks between a scope and a model group particle. */
static xexp_frame *xsd_expansion_push(th_schema *schema, xexp_frame *stack, const xexp_frame *local, Py_ssize_t *depth,
                                      Py_ssize_t *cap, th_node *node, int scope, int own, int bias, int *memo) {
    xexp_frame *grown = walk_reserve(stack, local, *depth, cap, sizeof(xexp_frame));
    if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;     /* GCOVR_EXCL_LINE */
    }
    const int particles = scope && !is_schema_el(schema, node, XSD_NS, "attributeGroup");
    const int attributes = scope && !is_schema_el(schema, node, XSD_NS, "group");
    th_node *content =
        scope && is_schema_el(schema, node, XSD_NS, "complexType") ? xsd_type_content(schema, node) : NULL;
    grown[(*depth)++] = (xexp_frame){node->first_child, content, NULL, memo, own, own, bias, particles, attributes};
    return grown;
}

/* Counts what a scope of `own` weight unfolds to through the refs validation follows; xsd_check_chains has found them
   acyclic and shallow, and each referenced definition is counted once into `memo`. Model groups nest as deep as the
   schema at every hop, so the scopes and particles under way sit on an explicit stack. -1 when out of memory. */
static int xsd_scope_expansion(th_schema *schema, th_node *scope, int *const *memo) {
    static const char *const models[] = {"sequence", "choice", "all", "group"};
    xexp_frame local[16] = {0};
    xexp_frame *stack = local;
    Py_ssize_t depth = 0, cap = 16;
    int result = -1;
    (void)xsd_expansion_push(schema, stack, local, &depth, &cap, scope, 1, 0, 0, NULL); /* the local array has room */
    for (;;) {
        xexp_frame *top = &stack[depth - 1];
        th_node *push = NULL;
        int push_scope = 1, push_own = top->own, push_bias = 0;
        int *push_memo = NULL;
        th_node *child = top->next;
        if (top->derivation != NULL) {
            push = top->derivation;
            top->derivation = NULL;
        } else if (child == NULL || top->total > TH_XSD_MAX_EXPANSION) {
            const int size = xsd_expansion_cap(top->total);
            if (top->memo != NULL) {
                *top->memo = size;
            }
            if (--depth == 0) {
                result = size;
                break;
            }
            stack[depth - 1].total += top->bias + size;
            continue;
        } else {
            top->next = child->next_sibling;
            int ref_kind = -1;
            const char *ref_attr = "ref";
            if (!top->particles && !top->attributes) {
                if (!xsd_is_particle(schema, child, XSD_PARTICLES, 5)) {
                    continue;
                }
                if (is_schema_el(schema, child, XSD_NS, "element")) {
                    top->total += top->own;
                    continue;
                }
                if (is_schema_el(schema, child, XSD_NS, "group")) {
                    ref_kind = CHAIN_GROUP;
                    push_bias = top->own;
                } else {
                    push = child;
                    push_scope = 0;
                }
            } else if (top->attributes && is_schema_el(schema, child, XSD_NS, "attribute")) {
                top->total += top->own;
                continue;
            } else if (top->attributes && is_schema_el(schema, child, XSD_NS, "attributeGroup")) {
                ref_kind = CHAIN_ATTRGROUP;
                push_bias = top->own;
            } else if (top->particles && xsd_is_particle(schema, child, models, 4)) {
                if (is_schema_el(schema, child, XSD_NS, "group")) {
                    ref_kind = CHAIN_GROUP;
                    push_bias = top->own;
                } else {
                    push = child;
                    push_scope = 0;
                }
            } else if (child == top->content) {
                th_node *derivation = first_schema_child(schema, child, XSD_NS, "extension");
                if (derivation == NULL) {
                    derivation = first_schema_child(schema, child, XSD_NS, "restriction");
                }
                if (derivation == NULL) {
                    continue;
                }
                top->derivation = derivation;
                ref_kind = CHAIN_TYPE;
                ref_attr = "base";
                child = derivation;
            } else {
                continue;
            }
            if (ref_kind >= 0) {
                /* a ref adds what its definition unfolds to, measured once: a measured definition counts itself, so
                   its size is never 0 */
                Py_ssize_t ref_len = 0;
                const Py_UCS4 *ref = xsd_attr(schema->tree, child, ref_attr, &ref_len);
                Py_ssize_t index = -1;
                if (ref != NULL) {
                    const Py_UCS4 *local_name, *prefix;
                    Py_ssize_t local_len = 0, prefix_len = 0;
                    split_prefix(ref, ref_len, &local_name, &local_len, &prefix, &prefix_len);
                    index = named_find_index(xsd_chain_vec(schema, ref_kind), local_name, local_len);
                }
                if (index < 0 || memo[ref_kind][index] != 0) {
                    top->total += push_bias + (index < 0 ? 0 : memo[ref_kind][index]);
                    continue;
                }
                push = xsd_chain_vec(schema, ref_kind)->items[index].node;
                push_own = 1;
                push_memo = &memo[ref_kind][index];
            }
        }
        xexp_frame *grown =
            xsd_expansion_push(schema, stack, local, &depth, &cap, push, push_scope, push_own, push_bias, push_memo);
        if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            break;           /* GCOVR_EXCL_LINE */
        }
        stack = grown;
    }
    walk_release(stack, local);
    return result;
}

/* An xs:all content model, written directly or as a group ref to one. */
static int xsd_is_all_model(th_schema *schema, th_node *model) {
    return model != NULL && (is_schema_el(schema, model, XSD_NS, "all") ||
                             (is_schema_el(schema, model, XSD_NS, "group") && xsd_group_all(schema, model) != NULL));
}

/* XSD 1.0 cos-all-limited: an xs:all content model cannot be extended with particles, since the base's members would
   then sit beside particles the base does not allow. Nor can it be the particle an extension adds to a base with
   content, since the base's particles would then sit beside it. */
static int xsd_check_all_extension(th_schema *schema, th_node *node) {
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        if (is_schema_el(schema, child, XSD_NS, "complexType")) {
            th_node *complex_content = first_schema_child(schema, child, XSD_NS, "complexContent");
            th_node *extension =
                complex_content != NULL ? first_schema_child(schema, complex_content, XSD_NS, "extension") : NULL;
            th_node *base = xsd_extension_base(schema, child);
            th_node *inherited = base != NULL ? xsd_inherited_model(schema, base) : NULL;
            if (inherited != NULL && is_schema_el(schema, inherited, XSD_NS, "all")) {
                if (xsd_content_model(schema, extension) != NULL) {
                    PyErr_SetString(PyExc_ValueError, "an xs:all content model cannot be extended with particles");
                    return -1;
                }
                schema->all_inherited = 1;
            } else if (inherited != NULL && xsd_is_all_model(schema, xsd_content_model(schema, extension))) {
                PyErr_SetString(PyExc_ValueError, "an xs:all content model cannot extend a base type with content");
                return -1;
            }
        }
        if (xsd_check_all_extension(schema, child) < 0) {
            return -1;
        }
    }
    return 0;
}

/* Every complex type, named or inline, is what some element is validated against. */
static int xsd_check_type_expansion(th_schema *schema, th_node *root, int *const *memo) {
    for (th_node *node = schema_walk_next(root, root); node != NULL; node = schema_walk_next(root, node)) {
        if (!is_schema_el(schema, node, XSD_NS, "complexType")) {
            continue;
        }
        const int size = xsd_scope_expansion(schema, node, memo);
        if (size < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;  /* GCOVR_EXCL_LINE */
        }
        if (size > TH_XSD_MAX_EXPANSION) {
            PyErr_Format(PyExc_ValueError,
                         "complex type references expand to more than %d particles and attributes; flatten the schema "
                         "or refer to each group, attribute group, and base type fewer times",
                         TH_XSD_MAX_EXPANSION);
            return -1;
        }
    }
    return 0;
}

static int xsd_check_expansion(th_schema *schema) {
    /* a reference resolves only to a named definition, so without one no type can expand */
    if (schema->groups.len == 0 && schema->attr_groups.len == 0 && schema->complex_types.len == 0) {
        return 0;
    }
    Py_ssize_t groups = schema->groups.len, attr_groups = schema->attr_groups.len;
    size_t bytes = (size_t)(groups + attr_groups + schema->complex_types.len) * sizeof(int);
    int *sizes = arena_alloc(&schema->mem, bytes);
    if (sizes == NULL) {  /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
        return -1;        /* GCOVR_EXCL_LINE */
    }
    memset(sizes, 0, bytes);
    int *const memo[] = {sizes, sizes + groups, sizes + groups + attr_groups}; /* in CHAIN_* order */
    return xsd_check_type_expansion(schema, schema->root, memo);
}

/* A value of a union is tried against its member types until one accepts it, and a member that is itself a list or
   union is tried through its own members, so the checks one value takes can multiply at every level. The limit is well
   above the handful of member types a real union has. */
#define TH_XSD_MAX_UNION_TRIALS 1000

/* For a list or union a value is checked against: the atomic checks one value of it can take, and how many levels of
   lists and unions it nests. trials is 0 until measured and -1 while it is measured. */
typedef struct {
    int trials, depth;
} xsd_reach;

/* A list or union whose members are being measured, and the member it resolves next. */
typedef struct {
    const xfacet_entry *root;
    Py_ssize_t next;
    int trials, deepest;
} xsd_visit;

/* Measures `start` and every list or union its members reach. An explicit stack holds the ones under way, since a
   definition can nest as deep as the schema does and compiling does not recurse. */
static int xsd_measure_root(th_schema *schema, const xfacet_entry *start, xsd_reach *memo, xsd_visit *stack) {
    static const xsd_reach atomic = {1, 0};
    size_t depth = 1;
    memo[start - schema->facet_entries].trials = -1;
    stack[0] = (xsd_visit){start, 0, 0, 0};
    while (depth > 0) {
        xsd_visit *top = &stack[depth - 1];
        if (top->next == top->root->member_count) {
            if (top->deepest >= TH_XSD_MAX_REF_DEPTH) {
                PyErr_Format(PyExc_ValueError,
                             "simple type nests xs:list and xs:union definitions more than %d deep; flatten the schema "
                             "or shorten the chain",
                             TH_XSD_MAX_REF_DEPTH);
                return -1;
            }
            xsd_reach *reach = &memo[top->root - schema->facet_entries];
            reach->trials = top->trials;
            reach->depth = top->deepest + 1;
            depth--;
            continue;
        }
        const xsd_member *member = &top->root->members[top->next];
        const xfacet_entry *type = member->type != NULL ? xsd_facet_entry(schema, member->type) : NULL;
        const xfacet_entry *child = type != NULL ? type->root : NULL;
        const xsd_reach *reached = &atomic;
        if (child != NULL) {
            xsd_reach *measured = &memo[child - schema->facet_entries];
            if (measured->trials == 0) {
                measured->trials = -1;
                stack[depth++] = (xsd_visit){child, 0, 0, 0};
                continue;
            }
            if (measured->trials < 0) {
                PyErr_SetString(PyExc_ValueError, "circular simple type definition through xs:list or xs:union");
                return -1;
            }
            reached = measured;
        }
        top->trials += reached->trials;
        if (top->trials > TH_XSD_MAX_UNION_TRIALS) {
            PyErr_Format(PyExc_ValueError,
                         "simple type tries a value against more than %d union member types; flatten the schema or "
                         "name each member type fewer times",
                         TH_XSD_MAX_UNION_TRIALS);
            return -1;
        }
        if (reached->depth > top->deepest) {
            top->deepest = reached->depth;
        }
        top->next++;
    }
    return 0;
}

/* Validation follows item and member types by C recursion, so a definition that leads back to itself, nests too deep
   or fans out too far is a compile error. Each list and union is measured once. */
static int xsd_check_varieties(th_schema *schema) {
    const size_t bytes = schema->facet_count * sizeof(xsd_reach);
    xsd_reach *memo = arena_alloc(&schema->mem, bytes);
    xsd_visit *stack = arena_alloc(&schema->mem, schema->facet_count * sizeof(xsd_visit));
    if (memo == NULL || stack == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        PyErr_NoMemory();                /* GCOVR_EXCL_LINE */
        return -1;                       /* GCOVR_EXCL_LINE */
    }
    memset(memo, 0, bytes);
    for (size_t index = 0; index < schema->facet_count; index++) {
        const xfacet_entry *root = schema->facet_entries[index].root;
        if (root != NULL && memo[root - schema->facet_entries].trials == 0 &&
            xsd_measure_root(schema, root, memo, stack) < 0) {
            return -1;
        }
    }
    return 0;
}

static void xsd_validate_root(valctx *ctx, th_node *root) {
    th_schema *schema = ctx->schema;
    ns_scope_enter_ancestors(schema, ctx->tree, root);
    qname name = element_qname_scoped(schema, ctx->tree, root);
    th_node *decl = named_find(&schema->elements, name.local, name.local_len);
    if (decl == NULL) {
        report(ctx, root, "structure", "no global element declaration for the document root");
        return;
    }
    edecl want;
    xsd_element_name(schema, decl, &want);
    if (!qname_eq(&name, &want)) {
        report(ctx, root, "structure", "document root is in the wrong namespace");
        return;
    }
    xsd_validate_tree(ctx, root, decl, 0);
}

#endif /* TURBOHTML_VALIDATE_XSD_H */
