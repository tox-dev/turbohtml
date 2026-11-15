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

/* Resolve a group ref particle to the group's model-group child (sequence/choice/all). */
static th_node *xsd_group_model(th_schema *schema, th_node *group_ref) {
    Py_ssize_t ref_len = 0;
    const Py_UCS4 *ref = xsd_attr(schema->tree, group_ref, "ref", &ref_len);
    if (ref == NULL) { /* compile accepts a group without ref, which has no model group to resolve */
        return NULL;
    }
    const Py_UCS4 *local, *prefix;
    Py_ssize_t local_len = 0, prefix_len = 0;
    split_prefix(ref, ref_len, &local, &local_len, &prefix, &prefix_len);
    /* xsd_compile has already rejected a group ref that resolves to nothing */
    th_node *group = named_find(&schema->groups, local, local_len);
    for (th_node *child = group->first_child; child != NULL; child = child->next_sibling) {
        if (xsd_is_particle(schema, child, XSD_MODEL_GROUPS, 3)) {
            return child;
        }
    } /* GCOVR_EXCL_LINE: llvm miscredits this loop-exit brace when the group has no model group */
    return NULL;
}

/* The xs:all a group ref resolves to, or NULL when its model group is not an xs:all. */
static th_node *xsd_group_all(th_schema *schema, th_node *group_ref) {
    th_node *model = xsd_group_model(schema, group_ref);
    return model != NULL && is_schema_el(schema, model, XSD_NS, "all") ? model : NULL;
}

static int edecl_push(th_schema *schema, edecl_vec *vec, const edecl *item) {
    if (vec->count == vec->cap) {
        Py_ssize_t next = vec->cap ? vec->cap * 2 : TH_INITIAL_CAPACITY(8);
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
    for (th_node *child = particle->first_child; child != NULL; child = child->next_sibling) {
        if (xsd_is_particle(schema, child, XSD_PARTICLES, 5)) {
            xsd_collect_edecls(schema, child, vec);
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

/* A bounded particle takes every repetition from the positions the one before reached, and stops after maxOccurs. */
static void xsd_match_bounded(th_schema *schema, th_tree *inst, th_node *particle, th_node **children, const posset *in,
                              int min, int max, posset *out) {
    posset seen = posset_alloc(schema, in->size);
    posset cur = posset_alloc(schema, in->size);
    posset next = posset_alloc(schema, in->size);
    if (!seen.member || !cur.member || !next.member) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return;                                        /* GCOVR_EXCL_LINE */
    }
    posset_copy(&cur, in);
    posset_copy(&seen, in);
    if (min == 0) {
        posset_or(out, in);
    }
    for (int count = 1; count <= max; count++) {
        xsd_match_once(schema, inst, particle, children, &cur, &next);
        if (posset_empty(&next)) {
            break;
        }
        if (count >= min) {
            posset_or(out, &next);
        }
        if (!posset_or(&seen, &next)) { /* a nullable body would otherwise repeat forever */
            break;
        }
        posset spare = cur;
        cur = next;
        next = spare;
    }
    posset_free(schema, &next);
    posset_free(schema, &cur);
    posset_free(schema, &seen);
}

/* An unbounded particle reaches its minOccurs-th set of positions, then closes it under one more repetition. The
   closure steps only from the positions each round adds, so a position is stepped from once however many repetitions
   reach it. */
static void xsd_match_unbounded(th_schema *schema, th_tree *inst, th_node *particle, th_node **children,
                                const posset *in, int min, posset *out) {
    posset seen = posset_alloc(schema, in->size);
    posset cur = posset_alloc(schema, in->size);
    posset next = posset_alloc(schema, in->size);
    if (!seen.member || !cur.member || !next.member) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return;                                        /* GCOVR_EXCL_LINE */
    }
    posset_copy(&cur, in);
    posset_copy(&seen, in);
    int reached = 1;
    for (int count = 1; reached && count <= min; count++) {
        xsd_match_once(schema, inst, particle, children, &cur, &next);
        reached = !posset_empty(&next);
        if (count < min) {
            /* a repetition adding no position ends the count, so a settled set is not repeated minOccurs times */
            reached = reached && posset_or(&seen, &next);
        }
        posset spare = cur;
        cur = next;
        next = spare;
    }
    if (reached) {
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
    posset_free(schema, &seen);
}

/* Apply the particle's minOccurs/maxOccurs, accumulating every reachable position. */
static void xsd_match_occurs(th_schema *schema, th_tree *inst, th_node *particle, th_node **children, const posset *in,
                             posset *out, int in_all) {
    posset_clear(out);
    int min = xsd_occurs_min(schema->tree, particle);
    int max = xsd_occurs_max(schema->tree, particle);
    if (in_all) { /* a member of an xs:all group occurs at most once, whatever its declared maxOccurs */
        min = min != 0;
        max = max != 0;
    }
    if (max < 0) {
        xsd_match_unbounded(schema, inst, particle, children, in, min, out);
    } else {
        xsd_match_bounded(schema, inst, particle, children, in, min, max, out);
    }
}

static void xsd_match_once(th_schema *schema, th_tree *inst, th_node *particle, th_node **children, const posset *in,
                           posset *out) {
    posset_clear(out);
    if (is_schema_el(schema, particle, XSD_NS, "element")) {
        edecl want;
        xsd_element_name(schema, particle, &want);
        for (Py_ssize_t index = 0; index < in->count; index++) {
            TH_WORK_STEP();
            Py_ssize_t pos = in->items[index];
            if (pos + 1 >= in->size) {
                continue;
            }
            qname name = node_qname(inst, children[pos], children[pos]->text, children[pos]->text_len, 0);
            if (qname_eq(&name, &want)) {
                posset_add(out, pos + 1);
            }
        }
        return;
    }
    if (is_schema_el(schema, particle, XSD_NS, "group")) {
        th_node *model = xsd_group_model(schema, particle);
        if (model != NULL) {
            xsd_match_once(schema, inst, model, children, in, out);
        }
        return;
    }
    if (is_schema_el(schema, particle, XSD_NS, "choice")) {
        posset branch = posset_alloc(schema, in->size);
        if (branch.member == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return;                  /* GCOVR_EXCL_LINE */
        }
        for (th_node *child = particle->first_child; child != NULL; child = child->next_sibling) {
            /* wildcards are not matched: xs:any stays the branch that consumes nothing, without a descent */
            if (is_schema_el(schema, child, XSD_NS, "any")) {
                posset_or(out, in);
                continue;
            }
            /* an annotation is no branch, and xsd_check_chains bounds only the group refs that particles reach */
            if (!xsd_is_particle(schema, child, XSD_PARTICLES, 5)) {
                continue;
            }
            xsd_match_occurs(schema, inst, child, children, in, &branch, 0);
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
    const int in_all = is_schema_el(schema, particle, XSD_NS, "all");
    for (th_node *child = particle->first_child; child != NULL; child = child->next_sibling) {
        if (!xsd_is_particle(schema, child, XSD_PARTICLES, 5)) {
            continue;
        }
        xsd_match_occurs(schema, inst, child, children, &cur, &next, in_all);
        posset spare = cur;
        cur = next;
        next = spare;
    }
    posset_copy(out, &cur);
    posset_free(schema, &next);
    posset_free(schema, &cur);
}

static void xsd_validate_element(valctx *ctx, th_node *instance, th_node *decl);

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

/* Validate an `all` group: each child element matches the member it names and order is ignored. Each member occurs at
   most once (XML Schema Part 1 limits an xs:all particle to maxOccurs 0 or 1), so a second child naming it is reported
   and not validated. */
static void xsd_validate_all(valctx *ctx, th_node *all, th_node **children, Py_ssize_t nchildren) {
    th_schema *schema = ctx->schema;
    th_tree *inst = ctx->tree;
    const xall_entry *group = xsd_cached_all(schema, all);
    /* the member each child is validated against, NULL for a child that names none or repeats a member */
    const xall_member **matched = arena_alloc(&schema->mem, (size_t)(nchildren + 1) * sizeof(xall_member *));
    xall_hit *hits = arena_alloc(&schema->mem, (size_t)(nchildren + 1) * sizeof(xall_hit));
    if (matched == NULL || hits == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return;                            /* GCOVR_EXCL_LINE */
    }
    size_t hit_count = 0;
    for (Py_ssize_t index = 0; index < nchildren; index++) {
        TH_WORK_STEP();
        qname name = node_qname(inst, children[index], children[index]->text, children[index]->text_len, 0);
        matched[index] = xsd_all_find(group, &name);
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
    for (Py_ssize_t index = 0; index < nchildren; index++) {
        TH_WORK_STEP();
        if (matched[index] == NULL) {
            char buffer[256];
            report(ctx, children[index], "structure", "element '%s' is not allowed here",
                   name_utf8(children[index]->text, children[index]->text_len, buffer, sizeof(buffer)));
        } else {
            xsd_validate_element(ctx, children[index], matched[index]->want.decl);
        }
    }
    if (present == group->required_count) {
        return;
    }
    for (Py_ssize_t index = 0; index < group->required_count; index++) {
        TH_WORK_STEP();
        const xall_member *member = group->required[index];
        const xall_hit probe = {member, 0};
        if (bsearch(&probe, hits, hit_count, sizeof(xall_hit), xsd_hit_member_order) != NULL) {
            continue;
        }
        char buffer[256];
        report(ctx, all, "structure", "required element '%s' is missing",
               name_utf8(member->want.local, member->want.local_len, buffer, sizeof(buffer)));
    }
}

/* ---- simple types ---- */

/* Resolve a simpleType node (or a builtin base) into a facet set. Returns the base
   built-in datatype id, gathering restriction facets and following the base chain. */
static int xsd_gather_facets(th_schema *schema, th_node *simpletype, facetset *facets, int depth, int content);

static int xsd_is_builtin_base(th_schema *schema, th_node *owner, const Py_UCS4 *base, Py_ssize_t base_len) {
    const Py_UCS4 *local, *prefix, *uri;
    Py_ssize_t local_len = 0, prefix_len = 0, uri_len = 0;
    split_prefix(base, base_len, &local, &local_len, &prefix, &prefix_len);
    resolve_ns(schema->tree, owner, prefix, prefix_len, &uri, &uri_len);
    return is_xsd_uri(uri, uri_len);
}

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
    int content = 0;
    if (named == NULL && is_schema_el(schema, owner->parent, XSD_NS, "simpleContent")) {
        th_node *complex = named_find(&schema->complex_types, local, local_len);
        named = complex != NULL ? first_schema_child(schema, complex, XSD_NS, "simpleContent") : NULL;
        content = 1;
    }
    if (named != NULL && depth < 40) {
        return xsd_gather_facets(schema, named, facets, depth + 1, content);
    }
    return DT_STRING;
}

static int xsd_gather_facets(th_schema *schema, th_node *simpletype, facetset *facets, int depth, int content) {
    th_tree *tree = schema->tree;
    th_node *extension = content ? first_schema_child(schema, simpletype, XSD_NS, "extension") : NULL;
    th_node *restriction =
        extension != NULL ? extension : first_schema_child(schema, simpletype, XSD_NS, "restriction");
    if (restriction == NULL) { /* list / union fall back to a permissive string base */
        return facets->base_id;
    }
    Py_ssize_t base_len = 0;
    const Py_UCS4 *base = xsd_attr(tree, restriction, "base", &base_len);
    int base_id = facets->base_id;
    if (base != NULL) {
        base_id = xsd_base_id(schema, restriction, base, base_len, facets, depth);
    }
    /* a simpleContent restriction may narrow its named base with an inline simpleType, applied after the base */
    th_node *inner = base == NULL || content ? first_schema_child(schema, restriction, XSD_NS, "simpleType") : NULL;
    if (inner != NULL) {
        base_id = xsd_gather_facets(schema, inner, facets, depth + 1, 0);
    }
    if (base_id < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return -1;     /* GCOVR_EXCL_LINE */
    }
    facets->base_id = base_id;
    /* an extension keeps the whiteSpace rule of a named base, which the gather above has already set */
    const int keeps_ws = extension != NULL && base != NULL && !xsd_is_builtin_base(schema, restriction, base, base_len);
    if (!keeps_ws) {
        facets->ws = dt_default_ws(base_id);
    }
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
            const int status = facet_add(&schema->mem, facets, name.local, name.local_len, value, value_len);
            if (status < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM */
                return -1;    /* GCOVR_EXCL_LINE */
            }
        }
    }
    return base_id;
}

typedef struct xfacet_entry {
    th_node *node;
    facetset facets;
} xfacet_entry;

static int xsd_check_names(th_schema *schema, th_node *node, const Py_UCS4 *local, Py_ssize_t local_len);
static int xsd_check_all_slot(th_schema *schema, th_node *node);
static int xsd_record_facets(th_schema *schema, th_node *node, int content);

/* XSD 1.0 All Group Limited: an xs:all particle, written or reached through a group ref, occurs once, because its
   members are matched once. */
static int xsd_check_all_occurs(th_schema *schema, th_node *particle) {
    th_node *model = is_schema_el(schema, particle, XSD_NS, "group") ? xsd_group_model(schema, particle) : particle;
    if (model == NULL || !is_schema_el(schema, model, XSD_NS, "all") || xsd_occurs_max(schema->tree, particle) == 1) {
        return 0;
    }
    PyErr_SetString(PyExc_ValueError, "an xs:all group may occur only once: its maxOccurs must be 1");
    return -1;
}

/* The reference checks ride the facet walk so each node's cached qname (kind 0 always builds it) is looked up once. */
static int xsd_cache_facets(th_schema *schema, th_node *node) {
    const qname *name = schema_node_qname(schema, node);
    if (is_xsd_uri(name->uri, name->uri_len)) {
        if (xsd_check_names(schema, node, name->local, name->local_len) < 0) {
            return -1;
        }
        const int content = u_eq_ascii(name->local, name->local_len, "simpleContent");
        if (content || u_eq_ascii(name->local, name->local_len, "simpleType")) {
            if (xsd_record_facets(schema, node, content) < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
                return -1;                                      /* GCOVR_EXCL_LINE */
            }
        } else if (u_eq_ascii(name->local, name->local_len, "all")) {
            if (xsd_check_all_slot(schema, node) < 0 || xsd_check_all_occurs(schema, node) < 0) {
                return -1;
            }
            if (xsd_record_all(schema, node) < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
                return -1;                          /* GCOVR_EXCL_LINE */
            }
        }
    }
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        if (child->type == TH_NODE_ELEMENT && xsd_cache_facets(schema, child) < 0) {
            return -1;
        }
    }
    return 0;
}

static int xsd_require_ref(th_schema *schema, th_node *node, named_vec *table, const char *kind);
static int xsd_require_type(th_schema *schema, th_node *node, int allow_complex);
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
        if (xsd_group_all(schema, node) == NULL) {
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
static int xsd_check_names(th_schema *schema, th_node *node, const Py_UCS4 *local, Py_ssize_t local_len) {
    if (u_eq_ascii(local, local_len, "unique") || u_eq_ascii(local, local_len, "key") ||
        u_eq_ascii(local, local_len, "keyref")) {
        char buffer[32];
        PyErr_Format(PyExc_ValueError,
                     "identity constraint xs:%s is not supported; remove it or enforce it outside the schema",
                     name_utf8(local, local_len, buffer, sizeof(buffer)));
        return -1;
    }
    if (u_eq_ascii(local, local_len, "element")) {
        if (xsd_require_ref(schema, node, &schema->elements, "element") < 0 || xsd_require_type(schema, node, 1) < 0) {
            return -1;
        }
    } else if (u_eq_ascii(local, local_len, "attribute")) {
        if (xsd_require_ref(schema, node, &schema->attributes, "attribute") < 0 ||
            xsd_require_type(schema, node, 0) < 0) {
            return -1;
        }
    } else if (u_eq_ascii(local, local_len, "group")) {
        if (xsd_require_ref(schema, node, &schema->groups, "group") < 0) {
            return -1;
        }
        if (xsd_check_all_slot(schema, node) < 0) {
            return -1;
        }
        return xsd_check_all_occurs(schema, node);
    } else if (u_eq_ascii(local, local_len, "attributeGroup")) {
        return xsd_require_ref(schema, node, &schema->attr_groups, "attribute group");
    } else if (u_eq_ascii(local, local_len, "extension") || u_eq_ascii(local, local_len, "restriction")) {
        return xsd_require_base(schema, node);
    }
    return 0;
}

static int xsd_require_ref(th_schema *schema, th_node *node, named_vec *table, const char *kind) {
    Py_ssize_t ref_len = 0;
    const Py_UCS4 *ref = xsd_attr(schema->tree, node, "ref", &ref_len);
    if (ref == NULL) {
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

/* allow_complex matches where the element and attribute validators look a type up. */
static int xsd_require_type(th_schema *schema, th_node *node, int allow_complex) {
    th_tree *tree = schema->tree;
    Py_ssize_t type_len = 0;
    const Py_UCS4 *type = xsd_attr(tree, node, "type", &type_len);
    if (type == NULL) {
        return 0;
    }
    const Py_UCS4 *local, *prefix, *uri;
    Py_ssize_t local_len = 0, prefix_len = 0, uri_len = 0;
    split_prefix(type, type_len, &local, &local_len, &prefix, &prefix_len);
    resolve_ns(tree, node, prefix, prefix_len, &uri, &uri_len);
    if (is_xsd_uri(uri, uri_len)) {
        if (xsd_is_builtin_type(local, local_len)) {
            return 0;
        }
    } else if (named_find(&schema->simple_types, local, local_len) != NULL ||
               (allow_complex && named_find(&schema->complex_types, local, local_len) != NULL)) {
        return 0;
    }
    char buffer[256];
    PyErr_Format(PyExc_ValueError, "type '%s' does not resolve to a declared type",
                 name_utf8(type, type_len, buffer, sizeof(buffer)));
    return -1;
}

static TH_NOINLINE int xsd_is_builtin_type(const Py_UCS4 *local, Py_ssize_t local_len) {
    if (dt_lookup(local, local_len) != DT_UNKNOWN) {
        return 1;
    }
    /* These XSD 1.0 types retain the existing fallback without dedicated datatype handlers. */
    static const char *const names[] = {"anyType",  "NOTATION",   "ID",    "IDREF",     "IDREFS", "ENTITY", "ENTITIES",
                                        "NMTOKENS", "gYearMonth", "gYear", "gMonthDay", "gDay",   "gMonth"};
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
    const Py_UCS4 *local, *prefix, *uri;
    Py_ssize_t local_len = 0, prefix_len = 0, uri_len = 0;
    split_prefix(base, base_len, &local, &local_len, &prefix, &prefix_len);
    resolve_ns(tree, node, prefix, prefix_len, &uri, &uri_len);
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
    entry->node = node;
    facetset_init(&entry->facets, DT_STRING);
    const int status = xsd_gather_facets(schema, node, &entry->facets, 0, content);
    if (status < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return -1;    /* GCOVR_EXCL_LINE */
    }
    return 0;
}

static int xsd_facet_order(const void *left, const void *right) {
    const uintptr_t left_node = (uintptr_t)((const xfacet_entry *)left)->node;
    const uintptr_t right_node = (uintptr_t)((const xfacet_entry *)right)->node;
    return (left_node > right_node) - (left_node < right_node);
}

static const facetset *xsd_cached_facets(const th_schema *schema, const th_node *node) {
    size_t low = 0, high = schema->facet_count;
    while (low < high) {
        const size_t middle = low + (high - low) / 2;
        if ((uintptr_t)schema->facet_entries[middle].node < (uintptr_t)node) {
            low = middle + 1;
        } else {
            high = middle;
        }
    }
    return &schema->facet_entries[low].facets;
}

/* Validate a text value against a simple type (built-in id, or a named/inline
   simpleType node, or the simpleContent node of a complexType). Reports on `node`. */
static void xsd_validate_simple(valctx *ctx, th_node *node, int builtin_id, th_node *simpletype, const Py_UCS4 *value,
                                Py_ssize_t len) {
    th_schema *schema = ctx->schema;
    facetset builtin;
    const facetset *facets;
    if (simpletype != NULL) {
        facets = xsd_cached_facets(schema, simpletype);
    } else {
        facetset_init(&builtin,
                      builtin_id >= 0 ? builtin_id : DT_STRING); /* GCOVR_EXCL_BR_LINE: negative only on arena OOM */
        facets = &builtin;
    }
    Py_ssize_t norm_len = 0;
    const Py_UCS4 *norm = dt_normalize_ws(schema, facets->ws, value, len, &norm_len);
    if (!dt_check_lexical(facets->base_id, norm, norm_len)) {
        char buffer[128];
        report(ctx, node, "datatype", "value '%s' is not a valid %s", name_utf8(norm, norm_len, buffer, sizeof(buffer)),
               DT_NAMES[facets->base_id].name);
        return;
    }
    facet_check(ctx, node, facets, norm, norm_len);
}

/* ---- attributes ---- */

typedef struct {
    Py_ssize_t *slots;
    size_t capacity;
} xattr_index;

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
        uint64_t hash = UINT64_C(1469598103934665603);
        for (Py_ssize_t byte = 0; byte < length; byte++) {
            hash ^= (unsigned char)name[byte];
            hash *= UINT64_C(1099511628211);
        }
        size_t slot = (size_t)hash & (index->capacity - 1);
        while (index->slots[slot] != 0) {
            slot = (slot + 1) & (index->capacity - 1);
        }
        index->slots[slot] = position + 1;
    }
    return 0;
}

/* Validate one declared attribute against the instance, honoring use/fixed/default. */
static void xsd_validate_attr_decl(valctx *ctx, th_node *instance, th_node *decl, const xattr_index *attrs) {
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
        /* xsd_compile has already rejected an attribute ref that resolves to nothing */
        effective = named_find(&schema->attributes, local, local_len);
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
    if (attrs->slots != NULL) {
        size_t slot = (size_t)named_hash(name, name_len) & (attrs->capacity - 1);
        while (attrs->slots[slot] != 0) {
            const th_node_attr *candidate = &instance->attrs[attrs->slots[slot] - 1];
            Py_ssize_t length;
            const char *bytes = th_attr_name(inst, candidate->name_atom, &length);
            if (length == name_len && u_eq_ascii(name, name_len, bytes)) {
                present = candidate;
                break;
            }
            slot = (slot + 1) & (attrs->capacity - 1);
        }
    }
    for (Py_ssize_t index = 0; attrs->slots == NULL && index < instance->attr_count; index++) {
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
    th_node *inline_type = first_schema_child(schema, effective, XSD_NS, "simpleType");
    if (type != NULL) {
        th_node *named = NULL;
        const Py_UCS4 *local, *prefix, *uri;
        Py_ssize_t local_len = 0, prefix_len = 0, uri_len = 0;
        split_prefix(type, type_len, &local, &local_len, &prefix, &prefix_len);
        resolve_ns(tree, effective, prefix, prefix_len, &uri, &uri_len);
        if (!is_xsd_uri(uri, uri_len)) {
            named = named_find(&schema->simple_types, local, local_len);
        }
        int base_id = DT_STRING;
        if (named == NULL) {
            facetset probe;
            facetset_init(&probe, DT_STRING);
            base_id = xsd_base_id(schema, effective, type, type_len, &probe, 0);
        }
        xsd_validate_simple(ctx, instance, base_id, named, value, value_len);
    } else if (inline_type != NULL) {
        xsd_validate_simple(ctx, instance, DT_STRING, inline_type, value, value_len);
    }
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

static void xsd_collect_attrs(valctx *ctx, th_node *instance, th_node *scope, edecl_vec *declared,
                              const xattr_index *attrs) {
    th_schema *schema = ctx->schema;
    th_tree *tree = schema->tree;
    /* Only a complexType has a base. An attribute group or a derivation holds attributes and attribute group refs, so
       from there the walk follows nothing but the refs the attribute group cycle check covers. */
    for (th_node *child = scope->first_child; child != NULL; child = child->next_sibling) {
        if (is_schema_el(schema, child, XSD_NS, "attribute")) {
            xsd_validate_attr_decl(ctx, instance, child, attrs);
            th_node *eff = child;
            Py_ssize_t ref_len = 0;
            const Py_UCS4 *ref = xsd_attr(tree, child, "ref", &ref_len);
            if (ref != NULL) {
                const Py_UCS4 *local, *prefix;
                Py_ssize_t local_len = 0, prefix_len = 0;
                split_prefix(ref, ref_len, &local, &local_len, &prefix, &prefix_len);
                /* xsd_compile has already rejected an attribute ref that resolves to nothing */
                eff = named_find(&schema->attributes, local, local_len);
            }
            Py_ssize_t name_len = 0;
            const Py_UCS4 *name = xsd_attr(tree, eff, "name", &name_len);
            if (name != NULL) {
                edecl item = {NULL, 0, name, name_len, eff};
                edecl_push(schema, declared, &item);
            }
        } else if (is_schema_el(schema, child, XSD_NS, "attributeGroup")) {
            Py_ssize_t ref_len = 0;
            const Py_UCS4 *ref = xsd_attr(tree, child, "ref", &ref_len);
            if (ref != NULL) {
                const Py_UCS4 *local, *prefix;
                Py_ssize_t local_len = 0, prefix_len = 0;
                split_prefix(ref, ref_len, &local, &local_len, &prefix, &prefix_len);
                /* xsd_compile has already rejected an attributeGroup ref that resolves to nothing */
                xsd_collect_attrs(ctx, instance, named_find(&schema->attr_groups, local, local_len), declared, attrs);
            }
        } else if ((is_schema_el(schema, child, XSD_NS, "complexContent") ||
                    is_schema_el(schema, child, XSD_NS, "simpleContent")) &&
                   is_schema_el(schema, scope, XSD_NS, "complexType") && child == xsd_type_content(schema, scope)) {
            th_node *derivation = first_schema_child(schema, child, XSD_NS, "extension");
            if (derivation == NULL) {
                derivation = first_schema_child(schema, child, XSD_NS, "restriction");
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
                        xsd_collect_attrs(ctx, instance, base_type, declared, attrs);
                    }
                }
                xsd_collect_attrs(ctx, instance, derivation, declared, attrs);
            }
        }
    }
}

static void xsd_validate_attrs(valctx *ctx, th_node *instance, th_node *scope, edecl_vec *declared) {
    th_tree *inst = ctx->tree;
    xattr_index attrs = {0};
    if (xsd_index_attrs(ctx, instance, &attrs) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        ctx->failed = 1;                              /* GCOVR_EXCL_LINE: allocation failure */
        return;                                       /* GCOVR_EXCL_LINE: allocation failure */
    }
    xsd_collect_attrs(ctx, instance, scope, declared, &attrs);
    named_vec names = {0};
    if (declared->count >= 32 && instance->attr_count >= 32) {
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
    }
    for (Py_ssize_t index = 0; index < instance->attr_count; index++) {
        Py_ssize_t alen = 0;
        const char *abytes = th_attr_name(inst, instance->attrs[index].name_atom, &alen);
        if (is_xmlns_decl(abytes, alen)) {
            continue;
        }
        int known = 0;
        if (names.slots != NULL) {
            uint64_t hash = UINT64_C(1469598103934665603);
            for (Py_ssize_t byte = 0; byte < alen; byte++) {
                hash ^= (unsigned char)abytes[byte];
                hash *= UINT64_C(1099511628211);
            }
            size_t slot = (size_t)hash & (names.slot_cap - 1);
            while (names.slots[slot] != NULL) {
                named_node *item = names.slots[slot];
                if (item->len == alen && u_eq_ascii(item->name, item->len, abytes)) {
                    known = 1;
                    break;
                }
                slot = (slot + 1) & (names.slot_cap - 1);
            }
        }
        for (Py_ssize_t decl = 0; names.slots == NULL && decl < declared->count; decl++) {
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

static th_node *xsd_content_model(const th_schema *schema, th_node *scope) {
    static const char *const models[] = {"sequence", "choice", "all", "group"};
    for (size_t index = 0; index < sizeof(models) / sizeof(models[0]); index++) {
        th_node *model = first_schema_child(schema, scope, XSD_NS, models[index]);
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
    th_node *complex_content = first_schema_child(schema, ctype, XSD_NS, "complexContent");
    if (complex_content != NULL) {
        th_node *extension = first_schema_child(schema, complex_content, XSD_NS, "extension");
        th_node *derivation =
            extension != NULL ? extension : first_schema_child(schema, complex_content, XSD_NS, "restriction");
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
        return xsd_content_model(schema, derivation);
    }
    return xsd_content_model(schema, ctype);
}

static int xsd_is_mixed(const th_schema *schema, th_node *ctype) {
    th_tree *tree = schema->tree;
    if (xsd_attr_is(tree, ctype, "mixed", "true")) {
        return 1;
    }
    th_node *complex_content = first_schema_child(schema, ctype, XSD_NS, "complexContent");
    if (complex_content != NULL) {
        return xsd_attr_is(tree, complex_content, "mixed", "true");
    }
    return 0;
}

static void xsd_validate_complex(valctx *ctx, th_node *instance, th_node *ctype) {
    th_schema *schema = ctx->schema;
    th_tree *inst = ctx->tree;
    edecl_vec declared = {NULL, 0, 0};
    xsd_validate_attrs(ctx, instance, ctype, &declared);

    th_node *simple_content = first_schema_child(schema, ctype, XSD_NS, "simpleContent");
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

    th_node *base_model;
    th_node *model = xsd_effective_model(schema, ctype, &base_model);
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
    Py_ssize_t at = 0;
    for (th_node *child = instance->first_child; child != NULL; child = child->next_sibling) {
        if (child->type == TH_NODE_ELEMENT) {
            children[at++] = child;
        }
    }

    th_node *all = NULL;
    if (model != NULL && is_schema_el(schema, model, XSD_NS, "all")) {
        all = model;
    } else if (model != NULL && schema->named_all && is_schema_el(schema, model, XSD_NS, "group")) {
        all = xsd_group_all(schema, model);
    }
    if (all != NULL) {
        /* an optional xs:all, direct or through a group ref, is satisfied by empty content */
        if (nchildren > 0 || xsd_occurs_min(schema->tree, model) != 0) {
            xsd_validate_all(ctx, all, children, nchildren);
        }
        return;
    }
    if (model == NULL && base_model == NULL) {
        if (nchildren > 0) {
            report(ctx, instance, "structure", "element must be empty");
        }
        return;
    }

    posset start = posset_alloc(schema, nchildren + 1);
    posset end = posset_alloc(schema, nchildren + 1);
    if (start.member == NULL || end.member == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return;                                       /* GCOVR_EXCL_LINE */
    }
    posset_add(&start, 0);
    if (base_model != NULL) {
        posset after_base = posset_alloc(schema, nchildren + 1);
        if (after_base.member == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return;                      /* GCOVR_EXCL_LINE */
        }
        xsd_match_occurs(schema, inst, base_model, children, &start, &after_base, 0);
        if (model != NULL) {
            xsd_match_occurs(schema, inst, model, children, &after_base, &end, 0);
        } else {
            posset_copy(&end, &after_base);
        }
    } else {
        xsd_match_occurs(schema, inst, model, children, &start, &end, 0);
    }

    if (!end.member[nchildren]) {
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

static void xsd_validate_element(valctx *ctx, th_node *instance, th_node *decl) {
    th_schema *schema = ctx->schema;
    th_tree *tree = schema->tree;
    qname name = node_qname(ctx->tree, instance, instance->text, instance->text_len, 0);
    Py_ssize_t mark = ctx->path.len;
    if (path_push(&ctx->path, name.local, name.local_len) < 0) { /* GCOVR_EXCL_BR_LINE: path OOM is unforceable */
        ctx->failed = 1;                                         /* GCOVR_EXCL_LINE */
        PyErr_NoMemory();                                        /* GCOVR_EXCL_LINE */
        return;                                                  /* GCOVR_EXCL_LINE */
    }

    Py_ssize_t type_len = 0;
    const Py_UCS4 *type = xsd_attr(tree, decl, "type", &type_len);
    th_node *inline_complex = first_schema_child(schema, decl, XSD_NS, "complexType");
    th_node *inline_simple = first_schema_child(schema, decl, XSD_NS, "simpleType");
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
            int base_id = simple == NULL ? xsd_base_id(schema, decl, type, type_len, &(facetset){0}, 0) : DT_STRING;
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

enum { CHAIN_GROUP, CHAIN_ATTRGROUP, CHAIN_TYPE };

static int xsd_check_chains(th_schema *schema, int kind);
static int xsd_check_expansion(th_schema *schema);

static int xsd_compile(th_schema *schema) {
    th_tree *tree = schema->tree;
    if (!is_schema_el(schema, schema->root, XSD_NS, "schema")) {
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
        if (name == NULL || name_len == 0) {
            if (is_schema_el(schema, child, XSD_NS, "element")) {
                PyErr_SetString(PyExc_ValueError, "global element requires a name");
                return 0;
            }
            continue;
        }
        named_vec *bucket = NULL;
        if (is_schema_el(schema, child, XSD_NS, "element")) {
            bucket = &schema->elements;
        } else if (is_schema_el(schema, child, XSD_NS, "complexType")) {
            bucket = &schema->complex_types;
        } else if (is_schema_el(schema, child, XSD_NS, "simpleType")) {
            bucket = &schema->simple_types;
        } else if (is_schema_el(schema, child, XSD_NS, "attribute")) {
            bucket = &schema->attributes;
        } else if (is_schema_el(schema, child, XSD_NS, "group")) {
            bucket = &schema->groups;
        } else if (is_schema_el(schema, child, XSD_NS, "attributeGroup")) {
            bucket = &schema->attr_groups;
        }
        if (bucket != NULL) {
            named_push(schema, bucket, name, name_len, child);
        }
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
        xsd_check_chains(schema, CHAIN_TYPE) < 0 || xsd_check_expansion(schema) < 0) {
        return 0;
    }
    if (schema->facet_count > 1) {
        qsort(schema->facet_entries, schema->facet_count, sizeof(xfacet_entry), xsd_facet_order);
    }
    if (schema->all_count > 1) {
        qsort(schema->all_entries, schema->all_count, sizeof(xall_entry), xsd_all_order);
    }
    return 1;
}

static named_vec *xsd_chain_vec(th_schema *schema, int kind);
static int xsd_chain_visit(th_schema *schema, int kind, Py_ssize_t index, int *state, int depth);

static int xsd_check_chains(th_schema *schema, int kind) {
    named_vec *vec = xsd_chain_vec(schema, kind);
    if (vec->len == 0) {
        return 0;
    }
    int *state = arena_alloc(&schema->mem, (size_t)vec->len * sizeof(int));
    if (state == NULL) {  /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
        return -1;        /* GCOVR_EXCL_LINE */
    }
    memset(state, 0, (size_t)vec->len * sizeof(int));
    for (Py_ssize_t index = 0; index < vec->len; index++) {
        if (state[index] == 0 && xsd_chain_visit(schema, kind, index, state, 0) < 0) {
            return -1;
        }
    }
    return 0;
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
static int xsd_type_base_edge(th_schema *schema, th_node *ctype, int *state, int depth, int *longest);
static int xsd_chain_refscan(th_schema *schema, int kind, th_node *node, int *state, int depth, int *longest);

/* Validation follows these refs by C recursion (xsd_collect_edecls, xsd_match_once, xsd_effective_model,
   xsd_collect_attrs). Well above any real schema (xmlschema already fails below 200 hops) and far below the smallest
   supported thread stack. */
#define TH_XSD_MAX_REF_DEPTH TH_DEPTH_LIMIT(100)

/* state[index] is 0 unvisited, -1 on the DFS stack (a back edge is a cycle), else the resolved chain length. depth caps
   the recursion itself; the length cap also catches a chain reached through memoized nodes. */
static int xsd_chain_visit(th_schema *schema, int kind, Py_ssize_t index, int *state, int depth) {
    named_vec *vec = xsd_chain_vec(schema, kind);
    if (state[index] == -1) {
        char buffer[256];
        PyErr_Format(PyExc_ValueError, "circular %s reference to '%s'", xsd_chain_kind(kind),
                     name_utf8(vec->items[index].name, vec->items[index].len, buffer, sizeof(buffer)));
        return -1;
    }
    if (state[index] > 0) {
        return state[index];
    }
    if (depth >= TH_XSD_MAX_REF_DEPTH) {
        PyErr_Format(PyExc_ValueError, "%s reference chain exceeds %d hops; flatten the schema or shorten the chain",
                     xsd_chain_kind(kind), TH_XSD_MAX_REF_DEPTH);
        return -1;
    }
    state[index] = -1;
    int longest = 0;
    int status = kind == CHAIN_TYPE ? xsd_type_base_edge(schema, vec->items[index].node, state, depth, &longest)
                                    : xsd_chain_refscan(schema, kind, vec->items[index].node, state, depth, &longest);
    if (status < 0) {
        return -1;
    }
    int length = longest + 1;
    if (length > TH_XSD_MAX_REF_DEPTH) {
        PyErr_Format(PyExc_ValueError, "%s reference chain exceeds %d hops; flatten the schema or shorten the chain",
                     xsd_chain_kind(kind), TH_XSD_MAX_REF_DEPTH);
        return -1;
    }
    state[index] = length;
    return length;
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

/* A-extends-B-extends-A is a cycle; a built-in or simple-type base ends the chain. */
static int xsd_type_base_edge(th_schema *schema, th_node *ctype, int *state, int depth, int *longest) {
    th_node *content = xsd_type_content(schema, ctype);
    if (content == NULL) {
        return 0;
    }
    th_node *deriv = first_schema_child(schema, content, XSD_NS, "extension");
    if (deriv == NULL) {
        deriv = first_schema_child(schema, content, XSD_NS, "restriction");
    }
    if (deriv == NULL) {
        return 0;
    }
    Py_ssize_t base_len = 0;
    const Py_UCS4 *base = xsd_attr(schema->tree, deriv, "base", &base_len);
    if (base == NULL) {
        return 0;
    }
    const Py_UCS4 *local, *prefix;
    Py_ssize_t local_len = 0, prefix_len = 0;
    split_prefix(base, base_len, &local, &local_len, &prefix, &prefix_len);
    Py_ssize_t target = named_find_index(&schema->complex_types, local, local_len);
    if (target < 0) {
        return 0;
    }
    int reached = xsd_chain_visit(schema, CHAIN_TYPE, target, state, depth + 1);
    if (reached < 0) {
        return -1;
    }
    *longest = reached; /* a type derives from a single base, so this is the whole chain below it */
    return 0;
}

/* Group refs sit inside model groups, so those are descended; an element is a leaf since no ref reaches across one. */
static int xsd_chain_refscan(th_schema *schema, int kind, th_node *node, int *state, int depth, int *longest) {
    const char *ref_el = kind == CHAIN_GROUP ? "group" : "attributeGroup";
    named_vec *vec = xsd_chain_vec(schema, kind);
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        if (child->type != TH_NODE_ELEMENT) {
            continue;
        }
        Py_ssize_t ref_len = 0;
        const Py_UCS4 *ref =
            is_schema_el(schema, child, XSD_NS, ref_el) ? xsd_attr(schema->tree, child, "ref", &ref_len) : NULL;
        if (ref != NULL) {
            const Py_UCS4 *local, *prefix;
            Py_ssize_t local_len = 0, prefix_len = 0;
            split_prefix(ref, ref_len, &local, &local_len, &prefix, &prefix_len);
            int reached = xsd_chain_visit(schema, kind, named_find_index(vec, local, local_len), state, depth + 1);
            if (reached < 0) {
                return -1;
            }
            if (reached > *longest) {
                *longest = reached;
            }
        } else if (kind == CHAIN_GROUP && xsd_is_particle(schema, child, XSD_MODEL_GROUPS, 3) &&
                   xsd_chain_refscan(schema, kind, child, state, depth, longest) < 0) {
            return -1;
        }
    }
    return 0;
}

/* The chain checks bound how deep references go, not how much they unfold to in total. */
#define TH_XSD_MAX_EXPANSION 20000

static int xsd_expansion_cap(int total) {
    return total > TH_XSD_MAX_EXPANSION ? TH_XSD_MAX_EXPANSION + 1 : total;
}

static int xsd_scope_expansion(th_schema *schema, th_node *scope, int *const *memo, int own);

/* Validation follows exactly these refs; xsd_check_chains has found them acyclic and shallow,
   so this recursion ends. */
static int xsd_ref_expansion(th_schema *schema, int kind, th_node *node, const char *attr, int *const *memo) {
    Py_ssize_t ref_len = 0;
    const Py_UCS4 *ref = xsd_attr(schema->tree, node, attr, &ref_len);
    if (ref == NULL) {
        return 0;
    }
    const Py_UCS4 *local, *prefix;
    Py_ssize_t local_len = 0, prefix_len = 0;
    split_prefix(ref, ref_len, &local, &local_len, &prefix, &prefix_len);
    named_vec *vec = xsd_chain_vec(schema, kind);
    Py_ssize_t index = named_find_index(vec, local, local_len);
    if (index < 0) {
        return 0;
    }
    if (memo[kind][index] == 0) { /* a measured definition counts itself, so its size is never 0 */
        memo[kind][index] = xsd_scope_expansion(schema, vec->items[index].node, memo, 1);
    }
    return memo[kind][index];
}

static int xsd_particle_expansion(th_schema *schema, th_node *particle, int *const *memo, int own) {
    if (is_schema_el(schema, particle, XSD_NS, "element")) {
        return own;
    }
    if (is_schema_el(schema, particle, XSD_NS, "group")) {
        return own + xsd_ref_expansion(schema, CHAIN_GROUP, particle, "ref", memo);
    }
    int total = own;
    for (th_node *child = particle->first_child; child != NULL && total <= TH_XSD_MAX_EXPANSION;
         child = child->next_sibling) {
        if (xsd_is_particle(schema, child, XSD_PARTICLES, 5)) {
            total += xsd_particle_expansion(schema, child, memo, own);
        }
    }
    return xsd_expansion_cap(total);
}

/* A group holds no attributes and an attributeGroup no particles, so each is walked only for what validation reads. */
static int xsd_scope_expansion(th_schema *schema, th_node *scope, int *const *memo, int own) {
    static const char *const models[] = {"sequence", "choice", "all", "group"};
    const int particles = !is_schema_el(schema, scope, XSD_NS, "attributeGroup");
    const int attributes = !is_schema_el(schema, scope, XSD_NS, "group");
    th_node *content = is_schema_el(schema, scope, XSD_NS, "complexType") ? xsd_type_content(schema, scope) : NULL;
    int total = own;
    for (th_node *child = scope->first_child; child != NULL && total <= TH_XSD_MAX_EXPANSION;
         child = child->next_sibling) {
        if (attributes && is_schema_el(schema, child, XSD_NS, "attribute")) {
            total += own;
        } else if (attributes && is_schema_el(schema, child, XSD_NS, "attributeGroup")) {
            total += own + xsd_ref_expansion(schema, CHAIN_ATTRGROUP, child, "ref", memo);
        } else if (particles && xsd_is_particle(schema, child, models, 4)) {
            total += xsd_particle_expansion(schema, child, memo, own);
        } else if (child == content) {
            th_node *derivation = first_schema_child(schema, child, XSD_NS, "extension");
            if (derivation == NULL) {
                derivation = first_schema_child(schema, child, XSD_NS, "restriction");
            }
            if (derivation != NULL) {
                total += xsd_ref_expansion(schema, CHAIN_TYPE, derivation, "base", memo) +
                         xsd_scope_expansion(schema, derivation, memo, own);
            }
        }
    }
    return xsd_expansion_cap(total);
}

/* Every complex type, named or inline, is what some element is validated against. */
static int xsd_check_type_expansion(th_schema *schema, th_node *node, int *const *memo) {
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        if (is_schema_el(schema, child, XSD_NS, "complexType") &&
            xsd_scope_expansion(schema, child, memo, 0) > TH_XSD_MAX_EXPANSION) {
            PyErr_Format(PyExc_ValueError,
                         "complex type references expand to more than %d particles and attributes; flatten the schema "
                         "or refer to each group, attribute group, and base type fewer times",
                         TH_XSD_MAX_EXPANSION);
            return -1;
        }
        if (xsd_check_type_expansion(schema, child, memo) < 0) {
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
