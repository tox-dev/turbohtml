/* XSD 1.0 structural validation over the parse_xml instance tree, interpreting the
   compiled schema's symbol table and the schema tree's particles directly. Content
   models (sequence / choice / all with minOccurs / maxOccurs, group and element refs)
   match with an NFA-style reachable-position set so repetition needs no backtracking;
   simple types resolve to a built-in datatype plus the constraining facets gathered
   from the restriction chain. Included once into validate/schema.c; every definition
   is static. */

#ifndef TURBOHTML_VALIDATE_XSD_H
#define TURBOHTML_VALIDATE_XSD_H

/* An expected element name plus the declaration that supplies its type. */
typedef struct {
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
static int xsd_is_particle(th_tree *tree, th_node *node, const char *const *locals, size_t count) {
    for (size_t index = 0; index < count; index++) {
        if (is_schema_el(tree, node, XSD_NS, locals[index])) {
            return 1;
        }
    }
    return 0;
}

static const char *const XSD_MODEL_GROUPS[] = {"sequence", "choice", "all"};
static const char *const XSD_PARTICLES[] = {"element", "sequence", "choice", "all", "group"};

static const Py_UCS4 *xsd_attr(th_tree *tree, th_node *node, const char *name, Py_ssize_t *out_len) {
    const th_node_attr *attr = attr_exact(tree, node, name, (Py_ssize_t)strlen(name));
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
static void xsd_element_name(th_schema *schema, th_node *particle, edecl *out) {
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
        th_node *global = named_find(&schema->elements, local, local_len);
        out->decl = global != NULL ? global : particle;
        return;
    }
    Py_ssize_t name_len = 0;
    const Py_UCS4 *name = xsd_attr(tree, particle, "name", &name_len);
    out->local = name;
    out->local_len = name_len;
    out->decl = particle;
    /* an element particle always has a parent (its model group or the schema) */
    int global = is_schema_el(tree, particle->parent, XSD_NS, "schema");
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

/* Resolve a group ref particle to the group's model-group child (sequence/choice/all). */
static th_node *xsd_group_model(th_schema *schema, th_node *group_ref) {
    Py_ssize_t ref_len = 0;
    const Py_UCS4 *ref = xsd_attr(schema->tree, group_ref, "ref", &ref_len);
    const Py_UCS4 *local, *prefix;
    Py_ssize_t local_len = 0, prefix_len = 0;
    split_prefix(ref, ref_len, &local, &local_len, &prefix, &prefix_len);
    th_node *group = named_find(&schema->groups, local, local_len);
    if (group == NULL) {
        return NULL;
    }
    for (th_node *child = group->first_child; child != NULL; child = child->next_sibling) {
        if (xsd_is_particle(schema->tree, child, XSD_MODEL_GROUPS, 3)) {
            return child;
        }
    } /* GCOVR_EXCL_LINE: llvm miscredits this loop-exit brace when the group has no model group */
    return NULL;
}

static int edecl_push(th_schema *schema, edecl_vec *vec, const edecl *item) {
    if (vec->count == vec->cap) {
        Py_ssize_t next = vec->cap ? vec->cap * 2 : 8;
        edecl *grown = arena_alloc(&schema->mem, (size_t)next * sizeof(edecl));
        if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return -1;       /* GCOVR_EXCL_LINE */
        }
        if (vec->count > 0) {
            memcpy(grown, vec->items, (size_t)vec->count * sizeof(edecl));
        }
        vec->items = grown;
        vec->cap = next;
    }
    vec->items[vec->count++] = *item;
    return 0;
}

/* Gather every element declaration reachable in a particle so a child can find its
   type by name (unique-particle-attribution makes the name→decl mapping unambiguous). */
static void xsd_collect_edecls(th_schema *schema, th_node *particle, edecl_vec *vec) {
    th_tree *tree = schema->tree;
    if (is_schema_el(tree, particle, XSD_NS, "element")) {
        edecl want;
        xsd_element_name(schema, particle, &want);
        edecl_push(schema, vec, &want);
        return;
    }
    if (is_schema_el(tree, particle, XSD_NS, "group")) {
        th_node *model = xsd_group_model(schema, particle);
        if (model != NULL) {
            xsd_collect_edecls(schema, model, vec);
        }
        return;
    }
    for (th_node *child = particle->first_child; child != NULL; child = child->next_sibling) {
        if (xsd_is_particle(tree, child, XSD_PARTICLES, 5)) {
            xsd_collect_edecls(schema, child, vec);
        }
    }
}

/* ---- content-model position-set matcher ---- */

typedef struct {
    char *pos;
    Py_ssize_t size; /* nchildren + 1 */
} posset;

static void posset_or(posset *into, const posset *from) {
    for (Py_ssize_t index = 0; index < into->size; index++) {
        into->pos[index] |= from->pos[index];
    }
}

static int posset_empty(const posset *set) {
    for (Py_ssize_t index = 0; index < set->size; index++) {
        if (set->pos[index]) {
            return 0;
        }
    }
    return 1;
}

static char *posset_alloc(th_schema *schema, Py_ssize_t size) {
    char *pos = arena_alloc(&schema->mem, (size_t)size);
    if (pos != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on unforceable arena OOM */
        memset(pos, 0, (size_t)size);
    }
    return pos;
}

static void xsd_match_once(th_schema *schema, th_tree *inst, th_node *particle, th_node **children, const posset *in,
                           posset *out);

/* Apply the particle's minOccurs/maxOccurs, accumulating every reachable position. */
static void xsd_match_occurs(th_schema *schema, th_tree *inst, th_node *particle, th_node **children, const posset *in,
                             posset *out) {
    memset(out->pos, 0, (size_t)out->size);
    int min = xsd_occurs_min(schema->tree, particle);
    int max = xsd_occurs_max(schema->tree, particle);
    if (min == 0) {
        posset_or(out, in);
    }
    posset seen = {posset_alloc(schema, in->size), in->size};
    posset cur = {posset_alloc(schema, in->size), in->size};
    if (seen.pos == NULL || cur.pos == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return;                                /* GCOVR_EXCL_LINE */
    }
    memcpy(cur.pos, in->pos, (size_t)in->size);
    posset_or(&seen, in);
    for (int count = 1; max < 0 || count <= max; count++) {
        posset next = {posset_alloc(schema, in->size), in->size};
        if (next.pos == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return;             /* GCOVR_EXCL_LINE */
        }
        xsd_match_once(schema, inst, particle, children, &cur, &next);
        if (posset_empty(&next)) {
            break;
        }
        if (count >= min) {
            posset_or(out, &next);
        }
        int progressed = 0;
        for (Py_ssize_t index = 0; index < next.size; index++) {
            if (next.pos[index] && !seen.pos[index]) {
                progressed = 1;
                seen.pos[index] = 1;
            }
        }
        if (!progressed) { /* a nullable body would otherwise repeat forever */
            break;
        }
        cur = next;
    }
}

static void xsd_match_once(th_schema *schema, th_tree *inst, th_node *particle, th_node **children, const posset *in,
                           posset *out) {
    th_tree *tree = schema->tree;
    memset(out->pos, 0, (size_t)out->size);
    if (is_schema_el(tree, particle, XSD_NS, "element")) {
        edecl want;
        xsd_element_name(schema, particle, &want);
        for (Py_ssize_t pos = 0; pos + 1 < in->size; pos++) {
            if (!in->pos[pos]) {
                continue;
            }
            qname name = node_qname(inst, children[pos], children[pos]->text, children[pos]->text_len, 0);
            if (qname_eq(&name, &want)) {
                out->pos[pos + 1] = 1;
            }
        }
        return;
    }
    if (is_schema_el(tree, particle, XSD_NS, "group")) {
        th_node *model = xsd_group_model(schema, particle);
        if (model != NULL) {
            xsd_match_once(schema, inst, model, children, in, out);
        }
        return;
    }
    if (is_schema_el(tree, particle, XSD_NS, "choice")) {
        for (th_node *child = particle->first_child; child != NULL; child = child->next_sibling) {
            if (child->type != TH_NODE_ELEMENT) {
                continue;
            }
            posset branch = {posset_alloc(schema, in->size), in->size};
            if (branch.pos == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
                return;               /* GCOVR_EXCL_LINE */
            }
            xsd_match_occurs(schema, inst, child, children, in, &branch);
            posset_or(out, &branch);
        }
        return;
    }
    /* sequence: thread the position set through each child particle in order */
    posset cur = {posset_alloc(schema, in->size), in->size};
    if (cur.pos == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return;            /* GCOVR_EXCL_LINE */
    }
    memcpy(cur.pos, in->pos, (size_t)in->size);
    for (th_node *child = particle->first_child; child != NULL; child = child->next_sibling) {
        if (child->type != TH_NODE_ELEMENT) {
            continue;
        }
        posset next = {posset_alloc(schema, in->size), in->size};
        if (next.pos == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return;             /* GCOVR_EXCL_LINE */
        }
        xsd_match_occurs(schema, inst, child, children, &cur, &next);
        cur = next;
    }
    memcpy(out->pos, cur.pos, (size_t)out->size);
}

static void xsd_validate_element(valctx *ctx, th_node *instance, th_node *decl);
static void xsd_validate_element_inner(valctx *ctx, th_node *instance, th_node *decl);

/* Validate an `all` group: each child element matches a distinct particle by name,
   with its per-particle occurs honored and order ignored. */
static void xsd_validate_all(valctx *ctx, th_node *all, th_node **children, Py_ssize_t nchildren) {
    th_schema *schema = ctx->schema;
    th_tree *tree = schema->tree;
    th_tree *inst = ctx->tree;
    for (Py_ssize_t index = 0; index < nchildren; index++) {
        qname name = node_qname(inst, children[index], children[index]->text, children[index]->text_len, 0);
        th_node *matched = NULL;
        for (th_node *particle = all->first_child; particle != NULL; particle = particle->next_sibling) {
            if (!is_schema_el(tree, particle, XSD_NS, "element")) {
                continue;
            }
            edecl want;
            xsd_element_name(schema, particle, &want);
            if (qname_eq(&name, &want)) {
                matched = particle;
                break;
            }
        }
        if (matched == NULL) {
            char buffer[256];
            report(ctx, children[index], "structure", "element '%s' is not allowed here",
                   name_utf8(children[index]->text, children[index]->text_len, buffer, sizeof(buffer)));
        } else {
            edecl want;
            xsd_element_name(schema, matched, &want);
            xsd_validate_element(ctx, children[index], want.decl);
        }
    }
    for (th_node *particle = all->first_child; particle != NULL; particle = particle->next_sibling) {
        if (!is_schema_el(tree, particle, XSD_NS, "element") || xsd_occurs_min(tree, particle) == 0) {
            continue;
        }
        edecl want;
        xsd_element_name(schema, particle, &want);
        int present = 0;
        for (Py_ssize_t index = 0; index < nchildren && !present; index++) {
            qname name = node_qname(inst, children[index], children[index]->text, children[index]->text_len, 0);
            present = qname_eq(&name, &want);
        }
        if (!present) {
            char buffer[256];
            report(ctx, all, "structure", "required element '%s' is missing",
                   name_utf8(want.local, want.local_len, buffer, sizeof(buffer)));
        }
    }
}

/* ---- simple types ---- */

/* Resolve a simpleType node (or a builtin base) into a facet set. Returns the base
   built-in datatype id, gathering restriction facets and following the base chain. */
static int xsd_gather_facets(th_schema *schema, th_node *simpletype, facetset *facets, int depth);

static int xsd_base_id(th_schema *schema, th_node *owner, const Py_UCS4 *base, Py_ssize_t base_len, facetset *facets,
                       int depth) {
    const Py_UCS4 *local, *prefix, *uri;
    Py_ssize_t local_len = 0, prefix_len = 0, uri_len = 0;
    split_prefix(base, base_len, &local, &local_len, &prefix, &prefix_len);
    resolve_ns(schema->tree, owner, prefix, prefix_len, &uri, &uri_len);
    if (is_xsd_uri(uri, uri_len)) {
        int builtin = dt_lookup(local, local_len);
        return builtin == DT_UNKNOWN ? DT_STRING : builtin;
    }
    th_node *named = named_find(&schema->simple_types, local, local_len);
    if (named != NULL && depth < 40) {
        return xsd_gather_facets(schema, named, facets, depth + 1);
    }
    return DT_STRING;
}

static int xsd_gather_facets(th_schema *schema, th_node *simpletype, facetset *facets, int depth) {
    th_tree *tree = schema->tree;
    th_node *restriction = first_schema_child(tree, simpletype, XSD_NS, "restriction");
    if (restriction == NULL) { /* list / union fall back to a permissive string base */
        return facets->base_id;
    }
    Py_ssize_t base_len = 0;
    const Py_UCS4 *base = xsd_attr(tree, restriction, "base", &base_len);
    int base_id = facets->base_id;
    if (base != NULL) {
        base_id = xsd_base_id(schema, restriction, base, base_len, facets, depth);
    } else {
        th_node *inner = first_schema_child(tree, restriction, XSD_NS, "simpleType");
        if (inner != NULL) {
            base_id = xsd_gather_facets(schema, inner, facets, depth + 1);
        }
    }
    facets->base_id = base_id;
    facets->ws = dt_default_ws(base_id);
    for (th_node *facet = restriction->first_child; facet != NULL; facet = facet->next_sibling) {
        if (facet->type != TH_NODE_ELEMENT) {
            continue;
        }
        qname name = node_qname(tree, facet, facet->text, facet->text_len, 0);
        if (name.uri == NULL || !u_eq_ascii(name.uri, name.uri_len, XSD_NS)) {
            continue;
        }
        Py_ssize_t value_len = 0;
        const Py_UCS4 *value = xsd_attr(tree, facet, "value", &value_len);
        if (value != NULL) {
            facet_add(&schema->mem, facets, name.local, name.local_len, value, value_len);
        }
    }
    return base_id;
}

/* Validate a text value against a simple type (built-in id, or a named/inline
   simpleType node). Reports on `node`. */
static void xsd_validate_simple(valctx *ctx, th_node *node, int builtin_id, th_node *simpletype, const Py_UCS4 *value,
                                Py_ssize_t len) {
    th_schema *schema = ctx->schema;
    facetset facets;
    facetset_init(&facets,
                  builtin_id >= 0 ? builtin_id : DT_STRING); /* GCOVR_EXCL_BR_LINE: builtin_id is always >= 0 */
    if (simpletype != NULL) {
        xsd_gather_facets(schema, simpletype, &facets, 0);
    }
    Py_ssize_t norm_len = 0;
    const Py_UCS4 *norm = dt_normalize_ws(&schema->mem, facets.ws, value, len, &norm_len);
    if (!dt_check_lexical(facets.base_id, norm, norm_len)) {
        char buffer[128];
        report(ctx, node, "datatype", "value '%s' is not a valid %s", name_utf8(norm, norm_len, buffer, sizeof(buffer)),
               DT_NAMES[facets.base_id].name);
        return;
    }
    facet_check(ctx, node, &facets, norm, norm_len);
}

/* ---- attributes ---- */

/* Validate one declared attribute against the instance, honoring use/fixed/default. */
static void xsd_validate_attr_decl(valctx *ctx, th_node *instance, th_node *decl) {
    th_schema *schema = ctx->schema;
    th_tree *tree = schema->tree;
    th_tree *inst = ctx->tree;
    th_node *effective = decl;
    Py_ssize_t ref_len = 0;
    const Py_UCS4 *ref = xsd_attr(tree, decl, "ref", &ref_len);
    if (ref != NULL) {
        const Py_UCS4 *local, *prefix;
        Py_ssize_t local_len = 0, prefix_len = 0;
        split_prefix(ref, ref_len, &local, &local_len, &prefix, &prefix_len);
        th_node *global = named_find(&schema->attributes, local, local_len);
        if (global != NULL) {
            effective = global;
        }
    }
    Py_ssize_t name_len = 0;
    const Py_UCS4 *name = xsd_attr(tree, effective, "name", &name_len);
    if (name == NULL) {
        return;
    }
    Py_ssize_t use_len = 0;
    const Py_UCS4 *use = xsd_attr(tree, decl, "use", &use_len);
    int required = use != NULL && u_eq_ascii(use, use_len, "required");
    int prohibited = use != NULL && u_eq_ascii(use, use_len, "prohibited");
    char namebuf[256];
    const char *name_bytes = name_utf8(name, name_len, namebuf, sizeof(namebuf));
    const th_node_attr *present = NULL;
    for (Py_ssize_t index = 0; index < instance->attr_count; index++) {
        Py_ssize_t alen = 0;
        const char *abytes = th_attr_name(inst, instance->attrs[index].name_atom, &alen);
        if ((Py_ssize_t)alen == name_len && u_eq_ascii(name, name_len, abytes)) {
            present = &instance->attrs[index];
            break;
        }
    }
    if (present == NULL) {
        if (required) {
            report(ctx, instance, "structure", "required attribute '%s' is missing", name_bytes);
        }
        return;
    }
    if (prohibited) {
        report(ctx, instance, "structure", "attribute '%s' is prohibited", name_bytes);
        return;
    }
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
    th_node *inline_type = first_schema_child(tree, effective, XSD_NS, "simpleType");
    if (type != NULL) {
        facetset probe;
        facetset_init(&probe, DT_STRING);
        int base_id = xsd_base_id(schema, effective, type, type_len, &probe, 0);
        th_node *named = NULL;
        const Py_UCS4 *local, *prefix, *uri;
        Py_ssize_t local_len = 0, prefix_len = 0, uri_len = 0;
        split_prefix(type, type_len, &local, &local_len, &prefix, &prefix_len);
        resolve_ns(tree, effective, prefix, prefix_len, &uri, &uri_len);
        if (!is_xsd_uri(uri, uri_len)) {
            named = named_find(&schema->simple_types, local, local_len);
        }
        xsd_validate_simple(ctx, instance, base_id, named, value, value_len);
    } else if (inline_type != NULL) {
        xsd_validate_simple(ctx, instance, DT_STRING, inline_type, value, value_len);
    }
}

/* Collect and validate the declared attributes of a complexType (own, attributeGroup
   refs, and the extension base), then flag any undeclared instance attribute. */
static void xsd_validate_attrs(valctx *ctx, th_node *instance, th_node *scope, edecl_vec *declared);

static void xsd_collect_attrs(valctx *ctx, th_node *instance, th_node *scope, edecl_vec *declared) {
    th_schema *schema = ctx->schema;
    th_tree *tree = schema->tree;
    for (th_node *child = scope->first_child; child != NULL; child = child->next_sibling) {
        if (is_schema_el(tree, child, XSD_NS, "attribute")) {
            xsd_validate_attr_decl(ctx, instance, child);
            th_node *eff = child;
            Py_ssize_t ref_len = 0;
            const Py_UCS4 *ref = xsd_attr(tree, child, "ref", &ref_len);
            if (ref != NULL) {
                const Py_UCS4 *local, *prefix;
                Py_ssize_t local_len = 0, prefix_len = 0;
                split_prefix(ref, ref_len, &local, &local_len, &prefix, &prefix_len);
                th_node *global = named_find(&schema->attributes, local, local_len);
                eff = global != NULL ? global : child;
            }
            Py_ssize_t name_len = 0;
            const Py_UCS4 *name = xsd_attr(tree, eff, "name", &name_len);
            if (name != NULL) {
                edecl item = {NULL, 0, name, name_len, eff};
                edecl_push(schema, declared, &item);
            }
        } else if (is_schema_el(tree, child, XSD_NS, "attributeGroup")) {
            Py_ssize_t ref_len = 0;
            const Py_UCS4 *ref = xsd_attr(tree, child, "ref", &ref_len);
            if (ref != NULL) {
                const Py_UCS4 *local, *prefix;
                Py_ssize_t local_len = 0, prefix_len = 0;
                split_prefix(ref, ref_len, &local, &local_len, &prefix, &prefix_len);
                th_node *group = named_find(&schema->attr_groups, local, local_len);
                if (group != NULL) {
                    xsd_collect_attrs(ctx, instance, group, declared);
                }
            }
        } else if (is_schema_el(tree, child, XSD_NS, "complexContent") ||
                   is_schema_el(tree, child, XSD_NS, "simpleContent")) {
            th_node *derivation = first_schema_child(tree, child, XSD_NS, "extension");
            if (derivation == NULL) {
                derivation = first_schema_child(tree, child, XSD_NS, "restriction");
            }
            if (derivation != NULL) {
                Py_ssize_t base_len = 0;
                const Py_UCS4 *base = xsd_attr(tree, derivation, "base", &base_len);
                if (base != NULL) {
                    const Py_UCS4 *local, *prefix, *uri;
                    Py_ssize_t local_len = 0, prefix_len = 0, uri_len = 0;
                    split_prefix(base, base_len, &local, &local_len, &prefix, &prefix_len);
                    resolve_ns(tree, derivation, prefix, prefix_len, &uri, &uri_len);
                    th_node *base_type = named_find(&schema->complex_types, local, local_len);
                    if (base_type != NULL) {
                        xsd_collect_attrs(ctx, instance, base_type, declared);
                    }
                }
                xsd_collect_attrs(ctx, instance, derivation, declared);
            }
        }
    }
}

static void xsd_validate_attrs(valctx *ctx, th_node *instance, th_node *scope, edecl_vec *declared) {
    th_tree *inst = ctx->tree;
    xsd_collect_attrs(ctx, instance, scope, declared);
    for (Py_ssize_t index = 0; index < instance->attr_count; index++) {
        Py_ssize_t alen = 0;
        const char *abytes = th_attr_name(inst, instance->attrs[index].name_atom, &alen);
        if (is_xmlns_decl(abytes, alen)) {
            continue;
        }
        int known = 0;
        for (Py_ssize_t decl = 0; decl < declared->count; decl++) {
            if ((Py_ssize_t)alen == declared->items[decl].local_len &&
                u_eq_ascii(declared->items[decl].local, declared->items[decl].local_len, abytes)) {
                known = 1;
                break;
            }
        }
        /* skip attributes in a foreign namespace (xsi:*, xml:*, other) */
        int prefixed = 0;
        for (Py_ssize_t byte = 0; byte < alen; byte++) {
            if (abytes[byte] == ':') {
                prefixed = 1;
                break;
            }
        }
        if (!known && !prefixed) {
            report(ctx, instance, "structure", "attribute '%s' is not declared", abytes);
        }
    }
}

/* ---- complex-type content ---- */

static th_node *xsd_content_model(th_tree *tree, th_node *scope) {
    static const char *const models[] = {"sequence", "choice", "all", "group"};
    for (size_t index = 0; index < sizeof(models) / sizeof(models[0]); index++) {
        th_node *model = first_schema_child(tree, scope, XSD_NS, models[index]);
        if (model != NULL) {
            return model;
        }
    }
    return NULL;
}

/* The effective particle for a complexType, following a complexContent extension so a
   derived type's base content precedes its own. Returns NULL for empty/simple content. */
static th_node *xsd_effective_model(th_schema *schema, th_node *ctype, th_node **base_model_out) {
    th_tree *tree = schema->tree;
    *base_model_out = NULL;
    th_node *complex_content = first_schema_child(tree, ctype, XSD_NS, "complexContent");
    if (complex_content != NULL) {
        th_node *extension = first_schema_child(tree, complex_content, XSD_NS, "extension");
        th_node *derivation =
            extension != NULL ? extension : first_schema_child(tree, complex_content, XSD_NS, "restriction");
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
                if (base_type != NULL) {
                    th_node *ignore;
                    *base_model_out = xsd_effective_model(schema, base_type, &ignore);
                }
            }
        }
        return xsd_content_model(tree, derivation);
    }
    return xsd_content_model(tree, ctype);
}

static int xsd_is_mixed(th_tree *tree, th_node *ctype) {
    if (xsd_attr_is(tree, ctype, "mixed", "true")) {
        return 1;
    }
    th_node *complex_content = first_schema_child(tree, ctype, XSD_NS, "complexContent");
    if (complex_content != NULL) {
        return xsd_attr_is(tree, complex_content, "mixed", "true");
    }
    return 0;
}

static void xsd_validate_complex(valctx *ctx, th_node *instance, th_node *ctype) {
    th_schema *schema = ctx->schema;
    th_tree *tree = schema->tree;
    th_tree *inst = ctx->tree;
    edecl_vec declared = {NULL, 0, 0};
    xsd_validate_attrs(ctx, instance, ctype, &declared);

    th_node *simple_content = first_schema_child(tree, ctype, XSD_NS, "simpleContent");
    if (simple_content != NULL) {
        for (th_node *child = instance->first_child; child != NULL; child = child->next_sibling) {
            if (child->type == TH_NODE_ELEMENT) {
                report(ctx, child, "structure", "simple-content element must not contain child elements");
                break;
            }
        }
        th_node *extension = first_schema_child(tree, simple_content, XSD_NS, "extension");
        th_node *derivation =
            extension != NULL ? extension : first_schema_child(tree, simple_content, XSD_NS, "restriction");
        Py_ssize_t text_len = 0;
        const Py_UCS4 *text = element_text(ctx, instance, &text_len);
        if (derivation != NULL) {
            Py_ssize_t base_len = 0;
            const Py_UCS4 *base = xsd_attr(tree, derivation, "base", &base_len);
            facetset probe;
            facetset_init(&probe, DT_STRING);
            int base_id = base != NULL ? xsd_base_id(schema, derivation, base, base_len, &probe, 0) : DT_STRING;
            xsd_validate_simple(ctx, instance, base_id, NULL, text, text_len);
        }
        return;
    }

    th_node *base_model;
    th_node *model = xsd_effective_model(schema, ctype, &base_model);
    int mixed = xsd_is_mixed(tree, ctype);
    if (!mixed) {
        for (th_node *child = instance->first_child; child != NULL; child = child->next_sibling) {
            if (is_chardata(child) && !th_node_text_is_blank(inst, child)) {
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
    Py_ssize_t at = 0;
    for (th_node *child = instance->first_child; child != NULL; child = child->next_sibling) {
        if (child->type == TH_NODE_ELEMENT) {
            children[at++] = child;
        }
    }

    if (model != NULL && is_schema_el(tree, model, XSD_NS, "all")) {
        xsd_validate_all(ctx, model, children, nchildren);
        return;
    }
    if (model == NULL && base_model == NULL) {
        if (nchildren > 0) {
            report(ctx, instance, "structure", "element must be empty");
        }
        return;
    }

    posset start = {posset_alloc(schema, nchildren + 1), nchildren + 1};
    posset end = {posset_alloc(schema, nchildren + 1), nchildren + 1};
    if (start.pos == NULL || end.pos == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return;                                 /* GCOVR_EXCL_LINE */
    }
    start.pos[0] = 1;
    if (base_model != NULL) {
        posset after_base = {posset_alloc(schema, nchildren + 1), nchildren + 1};
        if (after_base.pos == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return;                   /* GCOVR_EXCL_LINE */
        }
        xsd_match_occurs(schema, inst, base_model, children, &start, &after_base);
        if (model != NULL) {
            xsd_match_occurs(schema, inst, model, children, &after_base, &end);
        } else {
            memcpy(end.pos, after_base.pos, (size_t)end.size);
        }
    } else {
        xsd_match_occurs(schema, inst, model, children, &start, &end);
    }

    if (!end.pos[nchildren]) {
        report(ctx, instance, "structure", "content of element does not match its content model");
    }

    edecl_vec decls = {NULL, 0, 0};
    if (base_model != NULL) {
        xsd_collect_edecls(schema, base_model, &decls);
    }
    if (model != NULL) {
        xsd_collect_edecls(schema, model, &decls);
    }
    for (Py_ssize_t index = 0; index < nchildren; index++) {
        qname name = node_qname(inst, children[index], children[index]->text, children[index]->text_len, 0);
        for (Py_ssize_t decl = 0; decl < decls.count; decl++) {
            if (qname_eq(&name, &decls.items[decl])) {
                xsd_validate_element(ctx, children[index], decls.items[decl].decl);
                break;
            }
        }
    }
}

/* ---- element ---- */

/* Bound the per-element recursion so a pathologically deep document cannot overflow the
   thread stack; report once and stop descending past the cap. */
static void xsd_validate_element(valctx *ctx, th_node *instance, th_node *decl) {
    if (ctx->depth >= TH_VALIDATE_MAX_DEPTH) {
        report(ctx, instance, "structure", "maximum element nesting depth exceeded");
        return;
    }
    ctx->depth++;
    xsd_validate_element_inner(ctx, instance, decl);
    ctx->depth--;
}

static void xsd_validate_element_inner(valctx *ctx, th_node *instance, th_node *decl) {
    th_schema *schema = ctx->schema;
    th_tree *tree = schema->tree;
    qname name = node_qname(ctx->tree, instance, instance->text, instance->text_len, 0);
    Py_ssize_t mark = ctx->path.len;
    if (path_push(&ctx->path, name.local, name.local_len) < 0) { /* GCOVR_EXCL_BR_LINE: path OOM is unforceable */
        ctx->failed = 1;                                         /* GCOVR_EXCL_LINE */
        return;                                                  /* GCOVR_EXCL_LINE */
    }

    Py_ssize_t type_len = 0;
    const Py_UCS4 *type = xsd_attr(tree, decl, "type", &type_len);
    th_node *inline_complex = first_schema_child(tree, decl, XSD_NS, "complexType");
    th_node *inline_simple = first_schema_child(tree, decl, XSD_NS, "simpleType");
    if (type != NULL) {
        const Py_UCS4 *local, *prefix, *uri;
        Py_ssize_t local_len = 0, prefix_len = 0, uri_len = 0;
        split_prefix(type, type_len, &local, &local_len, &prefix, &prefix_len);
        resolve_ns(tree, decl, prefix, prefix_len, &uri, &uri_len);
        int is_xsd_ns = is_xsd_uri(uri, uri_len);
        th_node *complex = is_xsd_ns ? NULL : named_find(&schema->complex_types, local, local_len);
        if (complex != NULL) {
            xsd_validate_complex(ctx, instance, complex);
        } else {
            th_node *simple = is_xsd_ns ? NULL : named_find(&schema->simple_types, local, local_len);
            int base_id = xsd_base_id(schema, decl, type, type_len, &(facetset){0}, 0);
            for (th_node *child = instance->first_child; child != NULL; child = child->next_sibling) {
                if (child->type == TH_NODE_ELEMENT) {
                    report(ctx, child, "structure", "simple-typed element must not contain child elements");
                    break;
                }
            }
            Py_ssize_t text_len = 0;
            const Py_UCS4 *text = element_text(ctx, instance, &text_len);
            xsd_validate_simple(ctx, instance, is_xsd_ns ? base_id : DT_STRING, simple, text, text_len);
        }
    } else if (inline_complex != NULL) {
        xsd_validate_complex(ctx, instance, inline_complex);
    } else if (inline_simple != NULL) {
        Py_ssize_t text_len = 0;
        const Py_UCS4 *text = element_text(ctx, instance, &text_len);
        xsd_validate_simple(ctx, instance, DT_STRING, inline_simple, text, text_len);
    }
    ctx->path.len = mark;
}

/* ---- compile & entry ---- */

static int xsd_compile(th_schema *schema) {
    th_tree *tree = schema->tree;
    if (!is_schema_el(tree, schema->root, XSD_NS, "schema")) {
        PyErr_SetString(PyExc_ValueError, "root element is not an xs:schema");
        return 0;
    }
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
        if (name == NULL) {
            continue;
        }
        named_vec *bucket = NULL;
        if (is_schema_el(tree, child, XSD_NS, "element")) {
            bucket = &schema->elements;
        } else if (is_schema_el(tree, child, XSD_NS, "complexType")) {
            bucket = &schema->complex_types;
        } else if (is_schema_el(tree, child, XSD_NS, "simpleType")) {
            bucket = &schema->simple_types;
        } else if (is_schema_el(tree, child, XSD_NS, "attribute")) {
            bucket = &schema->attributes;
        } else if (is_schema_el(tree, child, XSD_NS, "group")) {
            bucket = &schema->groups;
        } else if (is_schema_el(tree, child, XSD_NS, "attributeGroup")) {
            bucket = &schema->attr_groups;
        }
        if (bucket != NULL) {
            named_push(schema, bucket, name, name_len, child);
        }
    }
    return 1;
}

static void xsd_validate_root(valctx *ctx, th_node *root) {
    th_schema *schema = ctx->schema;
    qname name = node_qname(ctx->tree, root, root->text, root->text_len, 0);
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
    xsd_validate_element(ctx, root, decl);
}

#endif /* TURBOHTML_VALIDATE_XSD_H */
