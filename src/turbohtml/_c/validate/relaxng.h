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
    pattern *p1, *p2;
    nameclass *nc;
    int def_index;
    int datatype_id;
    int value_ws;
    facetset *facets;
    const Py_UCS4 *value;
    Py_ssize_t value_len;
};

static pattern *pat_new(th_schema *schema, int type) {
    pattern *pattern = arena_alloc(&schema->mem, sizeof(*pattern));
    if (pattern == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return NULL;       /* GCOVR_EXCL_LINE */
    }
    memset(pattern, 0, sizeof(*pattern));
    pattern->type = type;
    return pattern;
}

static pattern *pat_binary(th_schema *schema, int type, pattern *p1, pattern *p2) {
    pattern *node = pat_new(schema, type);
    if (node == NULL) {              /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return schema->p_notallowed; /* GCOVR_EXCL_LINE */
    }
    node->p1 = p1;
    node->p2 = p2;
    return node;
}

/* Smart constructors: absorb notAllowed / empty so derivatives stay bounded. */
static pattern *pat_choice(th_schema *schema, pattern *p1, pattern *p2) {
    if (p1->type == P_NOTALLOWED) {
        return p2;
    }
    if (p2->type == P_NOTALLOWED) {
        return p1;
    }
    return pat_binary(schema, P_CHOICE, p1, p2);
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
    pattern *node = pat_new(schema, P_ONEMORE);
    if (node == NULL) {              /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return schema->p_notallowed; /* GCOVR_EXCL_LINE */
    }
    node->p1 = p1;
    return node;
}

/* ---- pattern compilation ---- */

static pattern *rng_resolve(th_schema *schema, int def_index);

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
        if (is_schema_el(schema->tree, child, RNG_NS, "except")) {
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
    if (is_schema_el(tree, node, RNG_NS, "name")) {
        Py_ssize_t len = 0;
        const Py_UCS4 *text = element_text_raw(tree, node, &len);
        return nc_from_qname(schema, node, text, len, 0);
    }
    if (is_schema_el(tree, node, RNG_NS, "anyName")) {
        th_node *except = first_schema_child(tree, node, RNG_NS, "except");
        if (except == NULL) {
            return nc_new(schema, NC_ANY);
        }
        nameclass *nc = nc_new(schema, NC_EXCEPT);
        nc->a = nc_new(schema, NC_ANY);
        nc->b = rng_nameclass_choice_children(schema, except);
        return nc;
    }
    if (is_schema_el(tree, node, RNG_NS, "nsName")) {
        nameclass *nc = nc_new(schema, NC_NS);
        const th_node_attr *ns = attr_exact(tree, node, "ns", 2);
        if (ns == NULL) {
            rng_scope_ns(tree, node, &nc->uri, &nc->uri_len);
        } else {
            nc->uri = ns->value;
            nc->uri_len = ns->value_len;
        }
        th_node *except = first_schema_child(tree, node, RNG_NS, "except");
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
    if (is_schema_el(tree, node, RNG_NS, "empty")) {
        return schema->p_empty;
    }
    if (is_schema_el(tree, node, RNG_NS, "text")) {
        return schema->p_text;
    }
    if (is_schema_el(tree, node, RNG_NS, "notAllowed")) {
        return schema->p_notallowed;
    }
    if (is_schema_el(tree, node, RNG_NS, "element")) {
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
    if (is_schema_el(tree, node, RNG_NS, "attribute")) {
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
    if (is_schema_el(tree, node, RNG_NS, "group")) {
        return rng_build_children(schema, node, NULL);
    }
    if (is_schema_el(tree, node, RNG_NS, "choice")) {
        pattern *combined = schema->p_notallowed;
        for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
            if (child->type == TH_NODE_ELEMENT) {
                combined = pat_choice(schema, combined, rng_build(schema, child));
            }
        }
        return combined;
    }
    if (is_schema_el(tree, node, RNG_NS, "interleave")) {
        pattern *combined = schema->p_empty;
        for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
            if (child->type == TH_NODE_ELEMENT) {
                combined = pat_interleave(schema, combined, rng_build(schema, child));
            }
        }
        return combined;
    }
    if (is_schema_el(tree, node, RNG_NS, "optional")) {
        return pat_choice(schema, rng_build_children(schema, node, NULL), schema->p_empty);
    }
    if (is_schema_el(tree, node, RNG_NS, "zeroOrMore")) {
        return pat_choice(schema, pat_onemore(schema, rng_build_children(schema, node, NULL)), schema->p_empty);
    }
    if (is_schema_el(tree, node, RNG_NS, "oneOrMore")) {
        return pat_onemore(schema, rng_build_children(schema, node, NULL));
    }
    if (is_schema_el(tree, node, RNG_NS, "mixed")) {
        return pat_interleave(schema, rng_build_children(schema, node, NULL), schema->p_text);
    }
    if (is_schema_el(tree, node, RNG_NS, "list")) {
        pattern *node_pat = pat_new(schema, P_LIST);
        node_pat->p1 = rng_build_children(schema, node, NULL);
        return node_pat;
    }
    if (is_schema_el(tree, node, RNG_NS, "data")) {
        pattern *node_pat = pat_new(schema, P_DATA);
        node_pat->datatype_id = rng_datatype_id(schema, node);
        facetset *facets = arena_alloc(&schema->mem, sizeof(*facets));
        if (facets == NULL) {            /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return schema->p_notallowed; /* GCOVR_EXCL_LINE */
        }
        facetset_init(facets, node_pat->datatype_id);
        for (th_node *param = node->first_child; param != NULL; param = param->next_sibling) {
            if (is_schema_el(tree, param, RNG_NS, "param")) {
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
    if (is_schema_el(tree, node, RNG_NS, "value")) {
        pattern *node_pat = pat_new(schema, P_VALUE);
        node_pat->datatype_id = rng_datatype_id(schema, node);
        node_pat->value = element_text_raw(tree, node, &node_pat->value_len);
        node_pat->value_ws = dt_default_ws(node_pat->datatype_id);
        return node_pat;
    }
    if (is_schema_el(tree, node, RNG_NS, "ref")) {
        const th_node_attr *name = attr_exact(tree, node, "name", 4);
        for (Py_ssize_t index = 0; index < schema->defines.len; index++) {
            if (u_eq_u(schema->defines.items[index].name, schema->defines.items[index].len, name->value,
                       name->value_len)) {
                pattern *node_pat = pat_new(schema, P_REF);
                node_pat->def_index = (int)index;
                return node_pat;
            }
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
    for (th_node *define = schema->root->first_child; define != NULL; define = define->next_sibling) {
        if (!is_schema_el(schema->tree, define, RNG_NS, "define")) {
            continue;
        }
        const th_node_attr *name = attr_exact(schema->tree, define, "name", 4);
        if (name == NULL) {
            continue;
        }
        if (!u_eq_u(name->value, name->value_len, entry->name, entry->len)) {
            continue;
        }
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
    }
    if (combined == NULL) {              /* GCOVR_EXCL_BR_LINE: every def_index has at least one matching define */
        combined = schema->p_notallowed; /* GCOVR_EXCL_LINE */
    } /* GCOVR_EXCL_LINE: llvm miscredits the closing brace of the unexecuted guard */
    entry->built = combined;
    return entry->built;
}

/* ---- derivatives ---- */

static int rng_nullable(th_schema *schema, pattern *p) {
    switch (p->type) {
    case P_EMPTY:
    case P_TEXT:
        return 1;
    case P_CHOICE:
        return rng_nullable(schema, p->p1) || rng_nullable(schema, p->p2);
    case P_GROUP:
    case P_INTERLEAVE:
        return rng_nullable(schema, p->p1) && rng_nullable(schema, p->p2);
    case P_ONEMORE:
        return rng_nullable(schema, p->p1);
    case P_REF: {
        def_entry *entry = &schema->defines.items[p->def_index];
        if (entry->building) { /* a ref recursive without an element guard is not nullable */
            return 0;
        }
        entry->building = 1;
        int nullable = rng_nullable(schema, rng_resolve(schema, p->def_index));
        entry->building = 0;
        return nullable;
    }
    default:
        return 0;
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
        if (!regex_full_match(&schema->mem, facets->patterns[index].ptr, facets->patterns[index].len, norm, norm_len)) {
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
    case P_REF: {
        def_entry *entry = &schema->defines.items[p->def_index];
        if (entry->building) { /* a ref recursive without an element guard consumes no text */
            return schema->p_notallowed;
        }
        entry->building = 1;
        pattern *derived = rng_text_deriv(schema, rng_resolve(schema, p->def_index), value, len);
        entry->building = 0;
        return derived;
    }
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
    case P_REF: {
        def_entry *entry = &schema->defines.items[p->def_index];
        if (entry->building) { /* a ref recursive without an element guard carries no attribute */
            return schema->p_notallowed;
        }
        entry->building = 1;
        pattern *derived = rng_att_deriv(schema, rng_resolve(schema, p->def_index), name, value, len);
        entry->building = 0;
        return derived;
    }
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
    case P_REF: {
        def_entry *entry = &schema->defines.items[p->def_index];
        if (entry->building) { /* a recursive content ref carries no unmatched attribute; keep it opaque */
            return p;
        }
        entry->building = 1;
        pattern *closed = rng_start_tag_close(schema, rng_resolve(schema, p->def_index));
        entry->building = 0;
        return closed;
    }
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
static pattern *rng_child_element_inner(valctx *ctx, pattern *p, th_node *element);

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
            Py_ssize_t len = 0;
            const Py_UCS4 *text = th_node_data(tree, child, &len);
            if (!rng_is_whitespace(text, len)) {
                current = rng_text_deriv(schema, current, text, len);
            }
        }
    }
    return current;
}

/* Derive the pattern over one child element, localizing any failure to it. */
/* Bound the per-element recursion so a pathologically deep document cannot overflow the
   thread stack; report once and stop descending past the cap. */
static pattern *rng_child_element(valctx *ctx, pattern *p, th_node *element) {
    if (ctx->depth >= TH_VALIDATE_MAX_DEPTH) {
        report(ctx, element, "structure", "maximum element nesting depth exceeded");
        return ctx->schema->p_notallowed;
    }
    ctx->depth++;
    pattern *result = rng_child_element_inner(ctx, p, element);
    ctx->depth--;
    return result;
}

static pattern *rng_child_element_inner(valctx *ctx, pattern *p, th_node *element) {
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
    Py_ssize_t before = PyList_GET_SIZE(ctx->errors);
    pattern *content = rng_children_deriv(ctx, closed, element);
    pattern *ended = rng_end_tag_deriv(schema, content);
    /* endTagDeriv yields NotAllowed exactly when this element's own content failed; a
       non-nullable residual is just the continuation the parent's siblings must match. */
    if (ended->type == P_NOTALLOWED && PyList_GET_SIZE(ctx->errors) == before) {
        char buffer[256];
        report(ctx, element, "structure", "content of element '%s' does not match the schema",
               name_utf8(element->text, element->text_len, buffer, sizeof(buffer)));
    }
    ctx->path.len = mark;
    return ended;
}

/* ---- compile & entry ---- */

static int rng_compile(th_schema *schema) {
    th_tree *tree = schema->tree;
    schema->p_empty = pat_new(schema, P_EMPTY);
    schema->p_notallowed = pat_new(schema, P_NOTALLOWED);
    schema->p_text = pat_new(schema, P_TEXT);
    if (!is_schema_el(tree, schema->root, RNG_NS, "grammar")) {
        schema->start = rng_build(schema, schema->root);
        return 1;
    }
    for (th_node *child = schema->root->first_child; child != NULL; child = child->next_sibling) {
        if (is_schema_el(tree, child, RNG_NS, "define")) {
            const th_node_attr *name = attr_exact(tree, child, "name", 4);
            if (name == NULL) {
                continue;
            }
            int found = 0;
            for (Py_ssize_t index = 0; index < schema->defines.len && !found; index++) {
                found = u_eq_u(schema->defines.items[index].name, schema->defines.items[index].len, name->value,
                               name->value_len);
            }
            if (found) {
                continue;
            }
            if (schema->defines.len == schema->defines.cap) {
                Py_ssize_t cap = schema->defines.cap ? schema->defines.cap * 2 : 8;
                def_entry *items = arena_alloc(&schema->mem, (size_t)cap * sizeof(def_entry));
                if (items == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
                    return 0;        /* GCOVR_EXCL_LINE */
                }
                if (schema->defines.len > 0) {
                    memcpy(items, schema->defines.items, (size_t)schema->defines.len * sizeof(def_entry));
                }
                schema->defines.items = items;
                schema->defines.cap = cap;
            }
            def_entry *entry = &schema->defines.items[schema->defines.len++];
            entry->name = name->value;
            entry->len = name->value_len;
            entry->first = child;
            entry->built = NULL;
            entry->building = 0;
        }
    }
    th_node *start = first_schema_child(tree, schema->root, RNG_NS, "start");
    if (start == NULL) {
        PyErr_SetString(PyExc_ValueError, "grammar has no start element");
        return 0;
    }
    schema->start = rng_build_children(schema, start, NULL);
    return 1;
}

static void rng_validate_root(valctx *ctx, th_node *root) {
    th_schema *schema = ctx->schema;
    Py_ssize_t before = PyList_GET_SIZE(ctx->errors);
    pattern *result = rng_child_element(ctx, schema->start, root);
    if (!rng_nullable(schema, result) && PyList_GET_SIZE(ctx->errors) == before) {
        report(ctx, root, "structure", "document does not match the schema");
    }
}

#endif /* TURBOHTML_VALIDATE_RELAXNG_H */
