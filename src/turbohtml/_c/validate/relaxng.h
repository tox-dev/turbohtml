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
} nameclass;

struct pattern {
    int type;
    int nullable;
    pattern *p1, *p2;
    nameclass *nc;
    int def_index;
    int datatype_id;
    int value_ws;
    facetset *facets;
    const Py_UCS4 *value;
    Py_ssize_t value_len;
};

/* Per-validation hash-consing: a structurally identical derivative shares one node, so a choice drops a duplicate
   branch by pointer and the residual stays bounded on an ambiguous schema (Clark, "Avoiding exponential blowup"; jing
   PatternInterner, MSV ExpressionPool). */
typedef struct patintern {
    pattern **slots;
    size_t cap; /* power of two, 0 until first grow */
    size_t count;
} patintern;

static pattern *pat_new(th_schema *schema, int type) {
    pattern *pattern = arena_alloc(&schema->mem, sizeof(*pattern));
    if (pattern == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return NULL;       /* GCOVR_EXCL_LINE */
    }
    memset(pattern, 0, sizeof(*pattern));
    pattern->type = type;
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
    size_t slot = (size_t)pat_key_hash(type, p1, p2) & (table->cap - 1);
    while (table->slots[slot] != NULL) {
        pattern *node = table->slots[slot];
        if (node->type == type && node->p1 == p1 && node->p2 == p2) {
            return node;
        }
        slot = (slot + 1) & (table->cap - 1);
    }
    return NULL;
}

static uint64_t pat_key_hash(int type, pattern *p1, pattern *p2) {
    uint64_t hash = UINT64_C(1469598103934665603);
    hash = (hash ^ (uint64_t)(unsigned)type) * UINT64_C(1099511628211);
    hash = (hash ^ (uint64_t)(uintptr_t)p1) * UINT64_C(1099511628211);
    hash = (hash ^ (uint64_t)(uintptr_t)p2) * UINT64_C(1099511628211);
    return hash;
}

static void intern_insert(th_schema *schema, patintern *table, pattern *node) {
    if (table->cap == 0 || table->count * 2 >= table->cap) {
        size_t cap = table->cap ? table->cap * 2 : 64;
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
            size_t slot = (size_t)pat_key_hash(existing->type, existing->p1, existing->p2) & (cap - 1);
            while (slots[slot] != NULL) {
                slot = (slot + 1) & (cap - 1);
            }
            slots[slot] = existing;
        }
        table->slots = slots;
        table->cap = cap;
    }
    size_t slot = (size_t)pat_key_hash(node->type, node->p1, node->p2) & (table->cap - 1);
    while (table->slots[slot] != NULL) {
        slot = (slot + 1) & (table->cap - 1);
    }
    table->slots[slot] = node;
    table->count++;
}

static pattern *pat_choice_merge(th_schema *schema, pattern *p1, pattern *p2);

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
    if (p1->type != P_CHOICE && p2->type != P_CHOICE) { /* hot path: two leaves need no merge buffer */
        if (p1 == p2) {
            return p1;
        }
        return p1 < p2 ? pat_binary(schema, P_CHOICE, p1, p2) : pat_binary(schema, P_CHOICE, p2, p1);
    }
    return pat_choice_merge(schema, p1, p2);
}

static Py_ssize_t choice_leaf_count(const pattern *choice);
static void choice_collect(pattern *choice, pattern **out, Py_ssize_t *at);
static int pat_ptr_cmp(const void *left, const void *right);

/* A canonical choice is a right-leaning chain of non-choice leaves, sorted by address and free of duplicates. */
static pattern *pat_choice_merge(th_schema *schema, pattern *p1, pattern *p2) {
    Py_ssize_t total = choice_leaf_count(p1) + choice_leaf_count(p2);
    pattern **leaves = arena_alloc(&schema->mem, (size_t)total * sizeof(pattern *));
    if (leaves == NULL) {                            /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return pat_binary(schema, P_CHOICE, p1, p2); /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t len = 0;
    choice_collect(p1, leaves, &len);
    choice_collect(p2, leaves, &len);
    qsort(leaves, (size_t)total, sizeof(pattern *), pat_ptr_cmp);
    pattern *result = leaves[total - 1];
    for (Py_ssize_t index = total - 2; index >= 0; index--) {
        if (leaves[index] != leaves[index + 1]) {
            result = pat_binary(schema, P_CHOICE, leaves[index], result);
        }
    }
    return result;
}

static Py_ssize_t choice_leaf_count(const pattern *choice) {
    Py_ssize_t count = 1;
    while (choice->type == P_CHOICE) {
        count++;
        choice = choice->p2;
    }
    return count;
}

static void choice_collect(pattern *choice, pattern **out, Py_ssize_t *at) {
    while (choice->type == P_CHOICE) {
        out[(*at)++] = choice->p1;
        choice = choice->p2;
    }
    out[(*at)++] = choice;
}

/* Total order on nodes by address, so a canonical choice has a single shape per leaf set. */
static int pat_ptr_cmp(const void *left, const void *right) {
    pattern *left_pat = *(pattern *const *)left;
    pattern *right_pat = *(pattern *const *)right;
    return (left_pat > right_pat) - (left_pat < right_pat);
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

static int def_add(th_schema *schema, const th_node_attr *name, th_node *node) {
    def_vec *defines = &schema->defines;
    Py_ssize_t index = def_find(defines, name->value, name->value_len);
    if (index >= 0) {
        def_entry *entry = &defines->items[index];
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
    if (defines->len == defines->cap) {
        Py_ssize_t cap = defines->cap ? defines->cap * 2 : 8;
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
        if (combined == NULL) {
            combined = part;
        } else {
            nameclass *choice = nc_new(schema, NC_CHOICE);
            choice->a = combined;
            choice->b = part;
            combined = choice;
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
        nameclass *nc = nc_new(schema, NC_EXCEPT);
        nc->a = nc_new(schema, NC_ANY);
        nc->b = rng_nameclass_choice_children(schema, except);
        return nc;
    }
    if (is_schema_el(schema, node, RNG_NS, "nsName")) {
        nameclass *nc = nc_new(schema, NC_NS);
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
        nameclass *excepted = nc_new(schema, NC_EXCEPT);
        excepted->a = nc;
        excepted->b = rng_nameclass_choice_children(schema, except);
        return excepted;
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
        if (nc_contains(nc->a, name)) {
            return 1;
        }
        return nc_contains(nc->b, name);
    default: /* NC_EXCEPT */
        if (!nc_contains(nc->a, name)) {
            return 0;
        }
        return !nc_contains(nc->b, name);
    }
}

static int rng_datatype_id(th_schema *schema, th_node *node) {
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
        return DT_STRING;
    }
    if (!xsd_library) {
        return u_eq_ascii(type->value, type->value_len, "token") ? DT_TOKEN : DT_STRING;
    }
    int builtin = dt_lookup(type->value, type->value_len);
    return builtin == DT_UNKNOWN ? DT_STRING : builtin;
}

static pattern *rng_build(th_schema *schema, th_node *node);
static int rng_check_interleave_node(th_schema *schema, th_node *interleave);

/* Group the pattern children of a container into a single pattern (Empty when none). */
static pattern *rng_build_children(th_schema *schema, th_node *node, th_node *skip) {
    pattern *combined = schema->p_empty;
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        if (child->type != TH_NODE_ELEMENT || child == skip) {
            continue;
        }
        combined = pat_group(schema, combined, rng_build(schema, child));
    }
    return combined;
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
        const th_node_attr *name = attr_exact(tree, node, "name", 4);
        th_node *skip = NULL;
        if (name != NULL) {
            node_pat->nc = nc_from_qname(schema, node, name->value, name->value_len, 0);
        } else {
            th_node *nc_child = first_element_child(node);
            node_pat->nc = rng_build_nameclass(schema, nc_child);
            skip = nc_child;
        }
        node_pat->p1 = rng_build_children(schema, node, skip);
        return node_pat;
    }
    if (is_schema_el(schema, node, RNG_NS, "attribute")) {
        pattern *node_pat = pat_new(schema, P_ATTRIBUTE);
        const th_node_attr *name = attr_exact(tree, node, "name", 4);
        th_node *skip = NULL;
        if (name != NULL) {
            node_pat->nc = nc_from_qname(schema, node, name->value, name->value_len, 1);
        } else {
            th_node *nc_child = first_element_child(node);
            node_pat->nc = rng_build_nameclass(schema, nc_child);
            skip = nc_child;
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
        pattern *combined = schema->p_notallowed;
        for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
            if (child->type == TH_NODE_ELEMENT) {
                combined = pat_choice(schema, combined, rng_build(schema, child));
            }
        }
        return combined;
    }
    if (is_schema_el(schema, node, RNG_NS, "interleave")) {
        /* compile time only: validation builds defines lazily, after the scan has checked them */
        if (schema->intern == NULL && rng_check_interleave_node(schema, node) < 0) {
            return schema->p_notallowed;
        }
        pattern *combined = schema->p_empty;
        for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
            if (child->type == TH_NODE_ELEMENT) {
                combined = pat_interleave(schema, combined, rng_build(schema, child));
            }
        }
        return combined;
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
        node_pat->p1 = rng_build_children(schema, node, NULL);
        return node_pat;
    }
    if (is_schema_el(schema, node, RNG_NS, "data")) {
        pattern *node_pat = pat_new(schema, P_DATA);
        node_pat->datatype_id = rng_datatype_id(schema, node);
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
            }
        }
        node_pat->facets = facets;
        return node_pat;
    }
    if (is_schema_el(schema, node, RNG_NS, "value")) {
        pattern *node_pat = pat_new(schema, P_VALUE);
        node_pat->datatype_id = rng_datatype_id(schema, node);
        node_pat->value = element_text_raw(tree, node, &node_pat->value_len);
        node_pat->value_ws = dt_default_ws(node_pat->datatype_id);
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
            node_pat->def_index = (int)index;
            return node_pat;
        }
        return schema->p_notallowed;
    }
    return schema->p_notallowed;
}

static pattern *rng_resolve(th_schema *schema, int def_index) {
    def_entry *entry = &schema->defines.items[def_index];
    if (entry->built != NULL) {
        return entry->built;
    }
    entry->built = schema->p_empty; /* placeholder guards direct build recursion */
    pattern *combined = NULL;
    th_node *define = entry->first;
    def_part *part = entry->extra;
    for (;;) {
        pattern *body = rng_build_children(schema, define, NULL);
        const th_node_attr *combine = attr_exact(schema->tree, define, "combine", 7);
        int interleave = 0;
        if (combine != NULL) {
            interleave = u_eq_ascii(combine->value, combine->value_len, "interleave");
        }
        if (combined == NULL) {
            combined = body;
        } else if (interleave) {
            combined = pat_interleave(schema, combined, body);
        } else {
            combined = pat_choice(schema, combined, body);
        }
        if (part == NULL) {
            break;
        }
        define = part->node;
        part = part->next;
    }
    entry->built = combined;
    return entry->built;
}

/* ---- derivatives ---- */

static int rng_nullable(th_schema *schema, pattern *p) {
    if (p->nullable >= 0) {
        return p->nullable;
    }
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
    const Py_UCS4 *norm = dt_normalize_ws(&schema->mem, facets->ws, value, len, &norm_len);
    if (!dt_check_lexical(datatype_id, norm, norm_len)) {
        return 0;
    }
    if (facets->has_min_length && norm_len < facets->min_length) {
        return 0;
    }
    if (facets->has_max_length && norm_len > facets->max_length) {
        return 0;
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

static pattern *rng_text_deriv(th_schema *schema, pattern *p, const Py_UCS4 *value, Py_ssize_t len);

static int rng_value_match(th_schema *schema, pattern *p, const Py_UCS4 *value, Py_ssize_t len) {
    if (rng_nullable(schema, rng_text_deriv(schema, p, value, len))) {
        return 1;
    }
    return rng_is_whitespace(value, len) && rng_nullable(schema, p);
}

static pattern *rng_text_deriv(th_schema *schema, pattern *p, const Py_UCS4 *value, Py_ssize_t len) {
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
    case P_VALUE: {
        Py_ssize_t nl = 0;
        const Py_UCS4 *norm = dt_normalize_ws(&schema->mem, p->value_ws, value, len, &nl);
        Py_ssize_t vl = 0;
        const Py_UCS4 *vnorm = dt_normalize_ws(&schema->mem, p->value_ws, p->value, p->value_len, &vl);
        return u_eq_u(norm, nl, vnorm, vl) ? schema->p_empty : schema->p_notallowed;
    }
    case P_DATA:
        return rng_datatype_ok(schema, p->datatype_id, p->facets, value, len) ? schema->p_empty : schema->p_notallowed;
    case P_LIST: {
        pattern *derived = p->p1;
        Py_ssize_t index = 0;
        while (index < len) {
            while (index < len && is_xml_space(value[index])) {
                index++;
            }
            Py_ssize_t start = index;
            while (index < len && !is_xml_space(value[index])) {
                index++;
            }
            if (index > start) {
                derived = rng_text_deriv(schema, derived, value + start, index - start);
            }
        }
        return rng_nullable(schema, derived) ? schema->p_empty : schema->p_notallowed;
    }
    case P_REF: /* the compile-time 4.19 check rules out a ref that recurses here without an element */
        return rng_text_deriv(schema, rng_resolve(schema, p->def_index), value, len);
    default:
        return schema->p_notallowed;
    }
}

/* applyAfter over an After/Choice tree: replace each After tail t with either
   group(t, tail) (mode 0, for group/oneOrMore) or after(t, tail) (mode 1, for after). */
static pattern *rng_apply_after(th_schema *schema, pattern *p, pattern *tail, int as_after);
static pattern *rng_apply_after_interleave_left(th_schema *schema, pattern *p, const qname *name);
static pattern *rng_apply_after_interleave_right(th_schema *schema, pattern *p, const qname *name);

static pattern *rng_start_tag_open(th_schema *schema, pattern *p, const qname *name) {
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

static pattern *rng_att_deriv(th_schema *schema, pattern *p, const qname *name, const Py_UCS4 *value, Py_ssize_t len) {
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

static pattern *rng_start_tag_close(th_schema *schema, pattern *p) {
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

static pattern *rng_end_tag_deriv(th_schema *schema, pattern *p) {
    if (p->type == P_CHOICE) {
        return pat_choice(schema, rng_end_tag_deriv(schema, p->p1), rng_end_tag_deriv(schema, p->p2));
    }
    if (p->type == P_AFTER) {
        return rng_nullable(schema, p->p1) ? p->p2 : schema->p_notallowed;
    }
    return schema->p_notallowed;
}

static pattern *rng_child_element(valctx *ctx, pattern *p, th_node *element);

/* Derive over an element's child list, ignoring whitespace-only text between elements
   and allowing insignificant whitespace when the content is a single text run. */
static pattern *rng_children_deriv(valctx *ctx, pattern *p, th_node *element) {
    th_schema *schema = ctx->schema;
    th_tree *tree = ctx->tree;
    Py_ssize_t element_children = 0, text_children = 0;
    for (th_node *child = element->first_child; child != NULL; child = child->next_sibling) {
        if (child->type == TH_NODE_ELEMENT) {
            element_children++;
        } else if (is_chardata(child)) {
            text_children++;
        }
    }
    if (element_children == 0 && text_children == 0) {
        return p;
    }
    if (element_children == 0) {
        Py_ssize_t len = 0;
        const Py_UCS4 *text = element_text(ctx, element, &len);
        pattern *derived = rng_text_deriv(schema, p, text, len);
        return rng_is_whitespace(text, len) ? pat_choice(schema, p, derived) : derived;
    }
    pattern *current = p;
    for (th_node *child = element->first_child; child != NULL; child = child->next_sibling) {
        if (child->type == TH_NODE_ELEMENT) {
            current = rng_child_element(ctx, current, child);
        } else if (is_chardata(child)) {
            Py_ssize_t len = child->text_len;
            const Py_UCS4 *text = len == 0 ? EMPTY_UCS4 : th_node_realize_text(tree, child);
            if (text == NULL) {              /* GCOVR_EXCL_BR_LINE: text realization allocation failure */
                ctx->failed = 1;             /* GCOVR_EXCL_LINE */
                PyErr_NoMemory();            /* GCOVR_EXCL_LINE */
                return schema->p_notallowed; /* GCOVR_EXCL_LINE */
            }
            if (!rng_is_whitespace(text, len)) {
                current = rng_text_deriv(schema, current, text, len);
            }
        }
    }
    return current;
}

/* Derive the pattern over one child element, localizing any failure to it. */
static pattern *rng_child_element(valctx *ctx, pattern *p, th_node *element) {
    th_schema *schema = ctx->schema;
    th_tree *tree = ctx->tree;
    qname name = node_qname(tree, element, element->text, element->text_len, 0);
    Py_ssize_t mark = ctx->path.len;
    if (path_push(&ctx->path, name.local, name.local_len) < 0) { /* GCOVR_EXCL_BR_LINE: path OOM is unforceable */
        ctx->failed = 1;                                         /* GCOVR_EXCL_LINE */
        return schema->p_notallowed;                             /* GCOVR_EXCL_LINE */
    }
    pattern *opened = rng_start_tag_open(schema, p, &name);
    if (opened->type == P_NOTALLOWED) {
        char buffer[256];
        report(ctx, element, "structure", "element '%s' is not allowed here",
               name_utf8(element->text, element->text_len, buffer, sizeof(buffer)));
        ctx->path.len = mark;
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
        qname aname = node_qname(tree, element, scratch, nlen, 1);
        const Py_UCS4 *value = element->attrs[index].value;
        if (value == NULL) {    /* GCOVR_EXCL_BR_LINE: an XML attribute always has a value */
            value = EMPTY_UCS4; /* GCOVR_EXCL_LINE */
        } /* GCOVR_EXCL_LINE: llvm miscredits the closing brace of the unexecuted guard */
        after_attrs = rng_att_deriv(schema, after_attrs, &aname, value, element->attrs[index].value_len);
    }
    pattern *closed = rng_start_tag_close(schema, after_attrs);
    Py_ssize_t before = ctx->error_count;
    pattern *content = rng_children_deriv(ctx, closed, element);
    pattern *ended = rng_end_tag_deriv(schema, content);
    /* endTagDeriv yields NotAllowed exactly when this element's own content failed; a
       non-nullable residual is just the continuation the parent's siblings must match. */
    if (ended->type == P_NOTALLOWED && ctx->error_count == before) {
        char buffer[256];
        report(ctx, element, "structure", "content of element '%s' does not match the schema",
               name_utf8(element->text, element->text_len, buffer, sizeof(buffer)));
    }
    ctx->path.len = mark;
    return ended;
}

/* ---- compile & entry ---- */

static int rng_scan(th_schema *schema, th_node *container, int depth);

static int rng_compile(th_schema *schema) {
    th_tree *tree = schema->tree;
    schema->p_empty = pat_new(schema, P_EMPTY);
    schema->p_notallowed = pat_new(schema, P_NOTALLOWED);
    schema->p_text = pat_new(schema, P_TEXT);
    if (!is_schema_el(schema, schema->root, RNG_NS, "grammar")) {
        /* no defines, so no ref cycles; rng_build checks 4.10 and 7.4 inline instead of a second walk */
        schema->start = rng_build(schema, schema->root);
        return PyErr_Occurred() ? 0 : 1;
    }
    for (th_node *child = schema->root->first_child; child != NULL; child = child->next_sibling) {
        if (is_schema_el(schema, child, RNG_NS, "define")) {
            const th_node_attr *name = attr_exact(tree, child, "name", 4);
            if (name == NULL) {
                continue;
            }
            if (def_add(schema, name, child) < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
                PyErr_NoMemory();                   /* GCOVR_EXCL_LINE */
                return 0;                           /* GCOVR_EXCL_LINE */
            }
        }
    }
    th_node *start = first_schema_child(schema, schema->root, RNG_NS, "start");
    if (start == NULL) {
        PyErr_SetString(PyExc_ValueError, "grammar has no start element");
        return 0;
    }
    for (Py_ssize_t index = 0; index < schema->defines.len; index++) {
        schema->defines.items[index].cycle_depth = -1;
    }
    if (rng_scan(schema, start, 0) < 0) {
        return 0;
    }
    schema->start = rng_build_children(schema, start, NULL);
    return 1;
}

static int rng_scan_node(th_schema *schema, th_node *node, int depth);

/* Checks 4.10, 4.19 and 7.4 on reachable patterns only: an unreachable <define> is never built, so it can neither
   crash nor blow up, and dead definitions add no compile cost. */
static int rng_scan(th_schema *schema, th_node *container, int depth) {
    for (th_node *child = container->first_child; child != NULL; child = child->next_sibling) {
        if (child->type != TH_NODE_ELEMENT) {
            continue;
        }
        if (rng_scan_node(schema, child, depth) < 0) {
            return -1;
        }
    }
    return 0;
}

enum { RNG_SCAN_OTHER, RNG_SCAN_REF, RNG_SCAN_INTERLEAVE, RNG_SCAN_ELEMENT };

static int rng_scan_kind(const th_schema *schema, th_node *node);
static int rng_scan_define(th_schema *schema, Py_ssize_t def_index, int depth);

static int rng_scan_node(th_schema *schema, th_node *node, int depth) {
    switch (rng_scan_kind(schema, node)) {
    case RNG_SCAN_REF: {
        const th_node_attr *name = attr_exact(schema->tree, node, "name", 4);
        if (name == NULL) {
            PyErr_SetString(PyExc_ValueError, "RELAX NG <ref> is missing the required name attribute");
            return -1;
        }
        Py_ssize_t index = def_find(&schema->defines, name->value, name->value_len);
        return index >= 0 ? rng_scan_define(schema, index, depth) : 0;
    }
    case RNG_SCAN_INTERLEAVE:
        return rng_check_interleave_node(schema, node) < 0 ? -1 : rng_scan(schema, node, depth);
    case RNG_SCAN_ELEMENT:
        return rng_scan(schema, node, depth + 1);
    default:
        return rng_scan(schema, node, depth);
    }
}

static int rng_scan_kind(const th_schema *schema, th_node *node) {
    const Py_UCS4 *local, *prefix;
    Py_ssize_t local_len = 0, prefix_len = 0;
    split_prefix(node->text, node->text_len, &local, &local_len, &prefix, &prefix_len);
    int kind;
    if (u_eq_ascii(local, local_len, "ref")) {
        kind = RNG_SCAN_REF;
    } else if (u_eq_ascii(local, local_len, "interleave")) {
        kind = RNG_SCAN_INTERLEAVE;
    } else if (u_eq_ascii(local, local_len, "element")) {
        kind = RNG_SCAN_ELEMENT;
    } else {
        return RNG_SCAN_OTHER; /* most nodes are not a restriction keyword: skip the namespace resolution */
    }
    qname name = schema_direct_qname(schema, node);
    return u_eq_ascii(name.uri, name.uri_len, RNG_NS) ? kind : RNG_SCAN_OTHER;
}

/* RELAX NG 4.19: meeting a define again at the element depth where its expansion began is a ref loop with no
   <element> between, which libxml2 ("Detected a cycle in %s references") and jing/MSV reject too. */
static int rng_scan_define(th_schema *schema, Py_ssize_t def_index, int depth) {
    def_entry *entry = &schema->defines.items[def_index];
    if (entry->cycle_depth == -1) {
        entry->cycle_depth = depth;
        int ret = rng_scan(schema, entry->first, depth);
        for (def_part *part = entry->extra; ret == 0 && part != NULL; part = part->next) {
            ret = rng_scan(schema, part->node, depth);
        }
        entry->cycle_depth = -2;
        return ret;
    }
    if (entry->cycle_depth == depth) {
        char buffer[256];
        PyErr_Format(PyExc_ValueError, "RELAX NG define '%s' references itself with no element in between",
                     name_utf8(entry->name, entry->len, buffer, sizeof(buffer)));
        return -1;
    }
    return 0;
}

typedef struct {
    nameclass **items;
    Py_ssize_t len, cap;
    int text;
} ncvec;

static int rng_branch_nameclasses(th_schema *schema, th_node *node, ncvec *out);
static int ncvec_push(th_schema *schema, ncvec *vec, nameclass *nc);
static int nc_name_eq(const nameclass *left, const nameclass *right);

/* RELAX NG 7.4: an ambiguous interleave drives the derivative into exponential memory; libxml2 ("Element or text
   conflicts in interleave") and jing/MSV reject it too. */
static int rng_check_interleave_node(th_schema *schema, th_node *interleave) {
    ncvec seen = {NULL, 0, 0, 0};
    for (th_node *branch = interleave->first_child; branch != NULL; branch = branch->next_sibling) {
        if (branch->type != TH_NODE_ELEMENT) {
            continue;
        }
        ncvec here = {NULL, 0, 0, 0};
        if (rng_branch_nameclasses(schema, branch, &here) < 0) { /* GCOVR_EXCL_BR_LINE: unforceable arena OOM */
            return -1;                                           /* GCOVR_EXCL_LINE */
        }
        if (here.text && seen.text) {
            PyErr_SetString(PyExc_ValueError, "RELAX NG interleave has text in more than one branch");
            return -1;
        }
        for (Py_ssize_t here_index = 0; here_index < here.len; here_index++) {
            for (Py_ssize_t seen_index = 0; seen_index < seen.len; seen_index++) {
                if (nc_name_eq(here.items[here_index], seen.items[seen_index])) {
                    PyErr_SetString(PyExc_ValueError,
                                    "RELAX NG interleave has the same element name in more than one branch");
                    return -1;
                }
            }
        }
        for (Py_ssize_t here_index = 0; here_index < here.len; here_index++) {
            if (ncvec_push(schema, &seen, here.items[here_index]) < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM */
                return -1;                                               /* GCOVR_EXCL_LINE */
            }
        }
        seen.text |= here.text;
    }
    return 0;
}

static nameclass *rng_pattern_nameclass(th_schema *schema, th_node *node, int is_attr);

/* An <element>'s content is a new level and an <attribute>'s text is not interleave text. A <ref> or a wildcard/choice
   name class is not one concrete name, so it falls to the interning backstop rather than a false rejection. */
static int rng_branch_nameclasses(th_schema *schema, th_node *node, ncvec *out) {
    if (is_schema_el(schema, node, RNG_NS, "element")) {
        nameclass *nc = rng_pattern_nameclass(schema, node, 0);
        return nc->type == NC_NAME ? ncvec_push(schema, out, nc) : 0;
    }
    if (is_schema_el(schema, node, RNG_NS, "text")) {
        out->text = 1;
        return 0;
    }
    if (is_schema_el(schema, node, RNG_NS, "attribute") || is_schema_el(schema, node, RNG_NS, "ref")) {
        return 0;
    }
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        if (child->type != TH_NODE_ELEMENT) {
            continue;
        }
        if (rng_branch_nameclasses(schema, child, out) < 0) { /* GCOVR_EXCL_BR_LINE: only ncvec_push arena OOM fails */
            return -1;                                        /* GCOVR_EXCL_LINE */
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

static int ncvec_push(th_schema *schema, ncvec *vec, nameclass *nc) {
    if (vec->len == vec->cap) {
        Py_ssize_t cap = vec->cap ? vec->cap * 2 : 8;
        nameclass **items = arena_alloc(&schema->mem, (size_t)cap * sizeof(nameclass *));
        if (items == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return -1;       /* GCOVR_EXCL_LINE */
        }
        if (vec->len > 0) {
            memcpy(items, vec->items, (size_t)vec->len * sizeof(nameclass *));
        }
        vec->items = items;
        vec->cap = cap;
    }
    vec->items[vec->len++] = nc;
    return 0;
}

static int nc_name_eq(const nameclass *left, const nameclass *right) {
    return u_eq_u(left->local, left->local_len, right->local, right->local_len) &&
           u_eq_u(left->uri, left->uri_len, right->uri, right->uri_len);
}

static void rng_validate_root(valctx *ctx, th_node *root) {
    th_schema *schema = ctx->schema;
    patintern *intern = arena_alloc(&schema->mem, sizeof(*intern));
    if (intern == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        ctx->failed = 1;  /* GCOVR_EXCL_LINE */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
        return;           /* GCOVR_EXCL_LINE */
    }
    memset(intern, 0, sizeof(*intern));
    schema->intern = intern;
    Py_ssize_t before = ctx->error_count;
    pattern *result = rng_child_element(ctx, schema->start, root);
    if (!rng_nullable(schema, result) && ctx->error_count == before) {
        report(ctx, root, "structure", "document does not match the schema");
    }
}

#endif /* TURBOHTML_VALIDATE_RELAXNG_H */
