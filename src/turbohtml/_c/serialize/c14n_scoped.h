#ifndef TURBOHTML_C14N_SCOPED_H
#define TURBOHTML_C14N_SCOPED_H

/* Canonicalization of a tree that stores a prefixed namespace declaration, xmlns:P. The HTML infoset binds no prefix
   of its own, so the ordinary walk in c14n.c renders none of these declarations. A tree with one is canonicalized here:
   the walk renders each declaration where Canonical XML renders it, and an element with nothing to render is written by
   the ordinary start-tag writer, so its output is the same. */

static int c14n_scoped_listed(const th_c14n_opts *opts, const char *prefix, Py_ssize_t len) {
    int listed = 0;
    for (Py_ssize_t index = 0; index < opts->inclusive_count && !listed; index++) {
        listed = strncmp(opts->inclusive[index], prefix, (size_t)len) == 0 && opts->inclusive[index][len] == '\0';
    }
    return listed;
}

/* The xml and xmlns prefixes are reserved, and xlink is rendered from an element's xlink: attributes, so none of
   them takes the prefixed-declaration path below. */
static int c14n_prefix_reserved(const char *prefix, Py_ssize_t len) {
    return (len == 3 && memcmp(prefix, "xml", 3) == 0) ||
           (len == 5 && (memcmp(prefix, "xmlns", 5) == 0 || memcmp(prefix, "xlink", 5) == 0));
}

/* A stored xmlns:P attribute with a non-empty value binds P. The HTML infoset binds no prefix of its own, so such a
   declaration renders only where the subtree uses P, as it does for a parsed XML tree. */
static int c14n_is_prefix_decl(const char *name, Py_ssize_t len, Py_ssize_t value_len) {
    return c14n_name_is_nsdecl(name, len) && len > 6 && value_len > 0 && !c14n_prefix_reserved(name + 6, len - 6);
}

static Py_ssize_t c14n_tag_prefix_len(const th_node *node) {
    Py_ssize_t colon = 0;
    while (colon < node->text_len && node->text[colon] != ':') {
        colon++;
    }
    return colon == node->text_len ? 0 : colon;
}

static Py_ssize_t c14n_attr_prefix_len(const char *name, Py_ssize_t len) {
    const char *colon = memchr(name, ':', (size_t)len);
    return colon == NULL ? 0 : colon - name;
}

/* The prefixed namespace scope. Each distinct declared prefix has a table entry, sorted by name, which records its
   innermost declaration and its innermost visible use on the open elements. Declarations and uses are stacks popped as
   their element closes, so a lookup costs a binary search and no walk over the ancestors. */
typedef struct {
    const char *name;
    Py_ssize_t name_len;
    int forced;
    int used;
    Py_ssize_t decl;
    Py_ssize_t use;
} c14n_ns_prefix;

typedef struct {
    Py_ssize_t prefix;
    const Py_UCS4 *uri;
    Py_ssize_t uri_len;
    const th_node *owner;
    Py_ssize_t shadowed;
} c14n_ns_decl;

typedef struct {
    Py_ssize_t prefix;
    Py_ssize_t decl;
    Py_ssize_t outer;
    const th_node *owner;
} c14n_ns_use;

typedef struct {
    th_tree *tree;
    int exclusive;
    c14n_ns_prefix *prefixes;
    Py_ssize_t prefix_count;
    c14n_ns_decl *decls;
    Py_ssize_t decls_len;
    c14n_ns_use *uses;
    Py_ssize_t uses_len;
    Py_ssize_t *candidates;
    char *tag_prefix; /* scratch: a tag name's prefix as UTF-8, since prefixes are compared as bytes */
    Py_ssize_t tag_prefix_cap;
    Py_ssize_t copied; /* code points of declarations written on an element that does not carry them */
} c14n_ns;

static int c14n_ns_name_cmp(const char *left, Py_ssize_t left_len, const char *right, Py_ssize_t right_len) {
    Py_ssize_t common = left_len < right_len ? left_len : right_len;
    int order = memcmp(left, right, (size_t)common);
    return order != 0 ? order : (left_len > right_len) - (left_len < right_len);
}

static int c14n_ns_prefix_cmp(const void *left, const void *right) {
    const c14n_ns_prefix *first = left;
    const c14n_ns_prefix *second = right;
    TH_WORK_STEP();
    return c14n_ns_name_cmp(first->name, first->name_len, second->name, second->name_len);
}

static int c14n_ns_index_cmp(const void *left, const void *right) {
    Py_ssize_t first = *(const Py_ssize_t *)left;
    Py_ssize_t second = *(const Py_ssize_t *)right;
    return (first > second) - (first < second);
}

static Py_ssize_t c14n_ns_find(const c14n_ns *scope, const char *name, Py_ssize_t len) {
    Py_ssize_t low = 0;
    Py_ssize_t high = scope->prefix_count;
    while (low < high) {
        TH_WORK_STEP();
        Py_ssize_t middle = low + (high - low) / 2;
        int order = c14n_ns_name_cmp(scope->prefixes[middle].name, scope->prefixes[middle].name_len, name, len);
        if (order == 0) {
            return middle;
        }
        if (order < 0) {
            low = middle + 1;
        } else {
            high = middle;
        }
    }
    return -1;
}

static int c14n_ns_same_uri(const c14n_ns_decl *left, const c14n_ns_decl *right) {
    return left->uri_len == right->uri_len &&
           memcmp(left->uri, right->uri, (size_t)left->uri_len * sizeof(Py_UCS4)) == 0;
}

/* Count the attributes of the apex's ancestors and subtree, which bounds the declarations, and the prefixed names the
   subtree uses, which bounds the visible uses. */
static void c14n_ns_census(th_tree *tree, th_node *apex, Py_ssize_t *attributes, Py_ssize_t *names) {
    for (const th_node *ancestor = apex->parent; ancestor != NULL; ancestor = ancestor->parent) {
        if (ancestor->type == TH_NODE_ELEMENT) {
            *attributes += ancestor->attr_count;
        }
    }
    for (th_node *node = apex; node != NULL; node = preorder_next(node, apex)) {
        if (node->type != TH_NODE_ELEMENT) {
            continue;
        }
        *attributes += node->attr_count;
        *names += c14n_tag_prefix_len(node) > 0;
        for (Py_ssize_t index = 0; index < node->attr_count; index++) {
            Py_ssize_t name_len;
            const char *name = th_attr_name(tree, node->attrs[index].name_atom, &name_len);
            *names += c14n_attr_prefix_len(name, name_len) > 0;
        }
    }
}

static void c14n_ns_end(c14n_ns *scope) {
    PyMem_Free(scope->prefixes);
    PyMem_Free(scope->decls);
    PyMem_Free(scope->uses);
    PyMem_Free(scope->candidates);
    PyMem_Free(scope->tag_prefix);
}

/* Encode the first `len` code points of node's tag name as UTF-8 into the scratch buffer; -1 on allocation failure. */
static Py_ssize_t c14n_ns_tag_prefix(c14n_ns *scope, const th_node *node, Py_ssize_t len) {
    if (len * 4 > scope->tag_prefix_cap) {
        char *grown = PyMem_Realloc(scope->tag_prefix, (size_t)len * 4);
        if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        scope->tag_prefix = grown;
        scope->tag_prefix_cap = len * 4;
    }
    unsigned char *bytes = (unsigned char *)scope->tag_prefix;
    Py_ssize_t count = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_UCS4 character = node->text[index];
        if (character < 0x80) {
            bytes[count++] = (unsigned char)character;
        } else if (character < 0x800) {
            bytes[count++] = (unsigned char)(0xC0 | (character >> 6));
            bytes[count++] = (unsigned char)(0x80 | (character & 0x3F));
        } else if (character < 0x10000) {
            bytes[count++] = (unsigned char)(0xE0 | (character >> 12));
            bytes[count++] = (unsigned char)(0x80 | ((character >> 6) & 0x3F));
            bytes[count++] = (unsigned char)(0x80 | (character & 0x3F));
        } else {
            bytes[count++] = (unsigned char)(0xF0 | (character >> 18));
            bytes[count++] = (unsigned char)(0x80 | ((character >> 12) & 0x3F));
            bytes[count++] = (unsigned char)(0x80 | ((character >> 6) & 0x3F));
            bytes[count++] = (unsigned char)(0x80 | (character & 0x3F));
        }
    }
    return count;
}

static void c14n_ns_mark_used(c14n_ns *scope, const char *name, Py_ssize_t len) {
    Py_ssize_t prefix = c14n_ns_find(scope, name, len);
    if (prefix >= 0) {
        scope->prefixes[prefix].used = 1;
    }
}

/* Sort the prefixes declared on the apex's ancestors and subtree into the table, once each, and mark the exclusive
   forced ones. */
static void c14n_ns_collect(c14n_ns *scope, th_tree *tree, th_node *apex, const th_c14n_opts *opts) {
    Py_ssize_t collected = 0;
    for (const th_node *ancestor = apex->parent; ancestor != NULL; ancestor = ancestor->parent) {
        if (ancestor->type != TH_NODE_ELEMENT) {
            continue;
        }
        for (Py_ssize_t index = 0; index < ancestor->attr_count; index++) {
            Py_ssize_t name_len;
            const char *name = th_attr_name(tree, ancestor->attrs[index].name_atom, &name_len);
            if (c14n_is_prefix_decl(name, name_len, ancestor->attrs[index].value_len)) {
                scope->prefixes[collected++] = (c14n_ns_prefix){name + 6, name_len - 6, 0, 0, -1, -1};
            }
        }
    }
    for (th_node *node = apex; node != NULL; node = preorder_next(node, apex)) {
        if (node->type != TH_NODE_ELEMENT) {
            continue;
        }
        for (Py_ssize_t index = 0; index < node->attr_count; index++) {
            Py_ssize_t name_len;
            const char *name = th_attr_name(tree, node->attrs[index].name_atom, &name_len);
            if (c14n_is_prefix_decl(name, name_len, node->attrs[index].value_len)) {
                scope->prefixes[collected++] = (c14n_ns_prefix){name + 6, name_len - 6, 0, 0, -1, -1};
            }
        }
    }
    qsort(scope->prefixes, (size_t)collected, sizeof(c14n_ns_prefix), c14n_ns_prefix_cmp);
    for (Py_ssize_t index = 0; index < collected; index++) {
        if (scope->prefix_count == 0 ||
            c14n_ns_name_cmp(scope->prefixes[scope->prefix_count - 1].name,
                             scope->prefixes[scope->prefix_count - 1].name_len, scope->prefixes[index].name,
                             scope->prefixes[index].name_len) != 0) {
            scope->prefixes[scope->prefix_count++] = scope->prefixes[index];
        }
    }
    for (Py_ssize_t index = 0; index < scope->prefix_count; index++) {
        c14n_ns_prefix *prefix = &scope->prefixes[index];
        prefix->forced = opts->exclusive && c14n_scoped_listed(opts, prefix->name, prefix->name_len);
    }
}

/* Push the ancestors' declarations, innermost first, so the nearest binding of a prefix wins; the walk never pops
   them. */
static void c14n_ns_push_ancestors(c14n_ns *scope, th_tree *tree, th_node *apex) {
    for (const th_node *ancestor = apex->parent; ancestor != NULL; ancestor = ancestor->parent) {
        if (ancestor->type != TH_NODE_ELEMENT) {
            continue;
        }
        for (Py_ssize_t index = 0; index < ancestor->attr_count; index++) {
            Py_ssize_t name_len;
            const char *name = th_attr_name(tree, ancestor->attrs[index].name_atom, &name_len);
            if (!c14n_is_prefix_decl(name, name_len, ancestor->attrs[index].value_len)) {
                continue;
            }
            Py_ssize_t prefix = c14n_ns_find(scope, name + 6, name_len - 6);
            if (scope->prefixes[prefix].decl < 0) {
                scope->decls[scope->decls_len] = (c14n_ns_decl){prefix, ancestor->attrs[index].value,
                                                                ancestor->attrs[index].value_len, ancestor, -1};
                scope->prefixes[prefix].decl = scope->decls_len++;
            }
        }
    }
}

/* Mark the prefixes the subtree uses; an inclusive apex renders a declaration only when some name uses its prefix.
   Returns -1 with an exception set on allocation failure. */
static int c14n_ns_mark_uses(c14n_ns *scope, th_tree *tree, th_node *apex) {
    for (th_node *node = apex; node != NULL; node = preorder_next(node, apex)) {
        /* a left-out element writes neither its name nor its attributes */
        if (node->type != TH_NODE_ELEMENT || (node->atom == TH_TAG_UNKNOWN && !ser_xml_element_name_wellformed(node))) {
            continue;
        }
        Py_ssize_t tag_len = c14n_tag_prefix_len(node);
        if (tag_len > 0) {
            Py_ssize_t bytes = c14n_ns_tag_prefix(scope, node, tag_len);
            /* GCOVR_EXCL_START: allocation failure cannot be forced from a test */
            if (bytes < 0) {
                PyErr_NoMemory();
                return -1;
            }
            /* GCOVR_EXCL_STOP */
            c14n_ns_mark_used(scope, scope->tag_prefix, bytes);
        }
        for (Py_ssize_t index = 0; index < node->attr_count; index++) {
            Py_ssize_t name_len;
            const char *name = th_attr_name(tree, node->attrs[index].name_atom, &name_len);
            Py_ssize_t prefix_len = c14n_attr_prefix_len(name, name_len);
            if (prefix_len > 0) {
                c14n_ns_mark_used(scope, name, prefix_len);
            }
        }
    }
    return 0;
}

/* Size the scope for the apex's ancestors and subtree, then fill its prefix table, ancestor declarations and use marks.
   Returns -1 with an exception set on allocation failure. */
static int c14n_ns_begin(c14n_ns *scope, th_tree *tree, th_node *apex, const th_c14n_opts *opts) {
    Py_ssize_t attributes = 0;
    Py_ssize_t names = 0;
    c14n_ns_census(tree, apex, &attributes, &names);
    scope->tree = tree;
    scope->exclusive = opts->exclusive;
    scope->prefix_count = 0;
    scope->decls_len = 0;
    scope->uses_len = 0;
    scope->tag_prefix = NULL;
    scope->tag_prefix_cap = 0;
    scope->copied = 0;
    scope->prefixes = PyMem_Malloc((size_t)(attributes + 1) * sizeof(c14n_ns_prefix));
    scope->decls = PyMem_Malloc((size_t)(attributes + 1) * sizeof(c14n_ns_decl));
    scope->uses = PyMem_Malloc((size_t)(names + 1) * sizeof(c14n_ns_use));
    scope->candidates = PyMem_Malloc((size_t)(attributes + names + 2) * sizeof(Py_ssize_t));
    /* GCOVR_EXCL_START: allocation failure cannot be forced from a test */
    if (scope->prefixes == NULL || scope->decls == NULL || scope->uses == NULL || scope->candidates == NULL) {
        c14n_ns_end(scope);
        PyErr_NoMemory();
        return -1;
    }
    /* GCOVR_EXCL_STOP */
    c14n_ns_collect(scope, tree, apex, opts);
    c14n_ns_push_ancestors(scope, tree, apex);
    /* GCOVR_EXCL_START: allocation failure cannot be forced from a test */
    if (c14n_ns_mark_uses(scope, tree, apex) < 0) {
        c14n_ns_end(scope);
        return -1;
    }
    /* GCOVR_EXCL_STOP */
    return 0;
}

static int c14n_ns_wanted(const c14n_ns *scope, Py_ssize_t prefix) {
    return scope->exclusive ? scope->prefixes[prefix].forced : scope->prefixes[prefix].used;
}

/* Record that node visibly uses prefix [name, name+len) and bind it to the declaration in scope there. A prefix the
   node already used keeps its one record. A forced prefix is rendered by its declarations alone, so it keeps none. */
static void c14n_ns_use_name(c14n_ns *scope, const th_node *node, const char *name, Py_ssize_t len, Py_ssize_t mark) {
    Py_ssize_t prefix = c14n_ns_find(scope, name, len);
    if (prefix < 0 || scope->prefixes[prefix].decl < 0 || scope->prefixes[prefix].forced ||
        scope->prefixes[prefix].use >= mark) {
        return;
    }
    scope->uses[scope->uses_len] =
        (c14n_ns_use){prefix, scope->prefixes[prefix].decl, scope->prefixes[prefix].use, node};
    scope->prefixes[prefix].use = scope->uses_len++;
}

/* Push the prefixed declarations node carries. */
static void c14n_ns_declare(c14n_ns *scope, const th_node *node) {
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        Py_ssize_t name_len;
        const char *name = th_attr_name(scope->tree, node->attrs[index].name_atom, &name_len);
        if (!c14n_is_prefix_decl(name, name_len, node->attrs[index].value_len)) {
            continue;
        }
        Py_ssize_t prefix = c14n_ns_find(scope, name + 6, name_len - 6);
        scope->decls[scope->decls_len] = (c14n_ns_decl){prefix, node->attrs[index].value, node->attrs[index].value_len,
                                                        node, scope->prefixes[prefix].decl};
        scope->prefixes[prefix].decl = scope->decls_len++;
    }
}

/* Push the prefixed declarations node carries, then collect the ones it must render, sorted by prefix into
   scope->candidates. An apex answers for every binding in scope; below it a declaration renders where it differs from
   the one it shadows outside the left-out run node is written in place of, and an exclusive visible use renders where
   it differs from the nearest using ancestor's. run is where that run's declarations start, or where node's own do. A
   forced prefix keeps no uses, so each prefix is a candidate from one source only and the list has no repeats. Returns
   the count, or -1 with ValueError set for a relative namespace URI. */
/* Kept out of line: the ordinary walk inlines its step, and these only run on a subtree with prefixed declarations. */
static Py_ssize_t c14n_ns_enter(c14n_ns *scope, const th_node *node, int is_apex, Py_ssize_t run) {
    Py_ssize_t use_mark = scope->uses_len;
    c14n_ns_declare(scope, node);
    if (scope->exclusive) {
        Py_ssize_t tag_len = c14n_tag_prefix_len(node);
        if (tag_len > 0) {
            Py_ssize_t bytes = c14n_ns_tag_prefix(scope, node, tag_len);
            if (bytes < 0) {      /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
                return -1;        /* GCOVR_EXCL_LINE */
            }
            c14n_ns_use_name(scope, node, scope->tag_prefix, bytes, use_mark);
        }
        for (Py_ssize_t index = 0; index < node->attr_count; index++) {
            Py_ssize_t name_len;
            const char *name = th_attr_name(scope->tree, node->attrs[index].name_atom, &name_len);
            Py_ssize_t prefix_len = c14n_attr_prefix_len(name, name_len);
            if (prefix_len > 0) {
                c14n_ns_use_name(scope, node, name, prefix_len, use_mark);
            }
        }
    }
    Py_ssize_t count = 0;
    if (is_apex) {
        for (Py_ssize_t index = 0; index < scope->prefix_count; index++) {
            TH_WORK_STEP();
            if (scope->prefixes[index].decl >= 0 && c14n_ns_wanted(scope, index)) {
                scope->candidates[count++] = index;
            }
        }
    } else {
        for (Py_ssize_t index = run; index < scope->decls_len; index++) {
            Py_ssize_t prefix = scope->decls[index].prefix;
            if (scope->prefixes[prefix].decl != index) {
                continue; /* a nearer declaration in the left-out run, or node's own, hides it */
            }
            Py_ssize_t shadowed = scope->decls[index].shadowed;
            while (shadowed >= run) {
                shadowed = scope->decls[shadowed].shadowed;
            }
            if (c14n_ns_wanted(scope, prefix) &&
                (shadowed < 0 || !c14n_ns_same_uri(&scope->decls[index], &scope->decls[shadowed]))) {
                scope->candidates[count++] = prefix;
            }
        }
    }
    for (Py_ssize_t index = use_mark; index < scope->uses_len; index++) {
        const c14n_ns_use *use = &scope->uses[index];
        if (use->outer < 0 ||
            !c14n_ns_same_uri(&scope->decls[use->decl], &scope->decls[scope->uses[use->outer].decl])) {
            scope->candidates[count++] = use->prefix;
        }
    }
    qsort(scope->candidates, (size_t)count, sizeof(Py_ssize_t), c14n_ns_index_cmp);
    for (Py_ssize_t index = 0; index < count; index++) {
        const c14n_ns_decl *decl = &scope->decls[scope->prefixes[scope->candidates[index]].decl];
        if (!c14n_absolute_uri(decl->uri, decl->uri_len)) {
            PyErr_SetString(PyExc_ValueError, "relative namespace URI cannot be canonicalized");
            return -1;
        }
    }
    return count;
}

/* Pop what node declared and used as it closes, restoring what each entry hid. */
static void c14n_ns_leave(c14n_ns *scope, const th_node *node) {
    while (scope->uses_len > 0 && scope->uses[scope->uses_len - 1].owner == node) {
        TH_WORK_STEP();
        const c14n_ns_use *use = &scope->uses[--scope->uses_len];
        scope->prefixes[use->prefix].use = use->outer;
    }
    while (scope->decls_len > 0 && scope->decls[scope->decls_len - 1].owner == node) {
        TH_WORK_STEP();
        const c14n_ns_decl *decl = &scope->decls[--scope->decls_len];
        scope->prefixes[decl->prefix].decl = decl->shadowed;
    }
}

static int c14n_ns_before_xlink(const c14n_ns *scope, Py_ssize_t prefix) {
    return c14n_ns_name_cmp(scope->prefixes[prefix].name, scope->prefixes[prefix].name_len, "xlink", 5) < 0;
}

static void c14n_put_xlink_decl(sbuf *out) {
    sbuf_puts(out, " xmlns:xlink=\"");
    sbuf_puts(out, C14N_XLINK);
    sbuf_putc(out, '"');
}

/* Returns 0, or -1 with ValueError set once a copied declaration exceeds the allowance. */
static int c14n_ns_put_decl(sbuf *out, c14n_ns *scope, const th_node *node, Py_ssize_t prefix) {
    const c14n_ns_prefix *name = &scope->prefixes[prefix];
    const c14n_ns_decl *decl = &scope->decls[name->decl];
    int copy = decl->owner != node;
    if (copy && !c14n_ns_copy_funded(out, scope->copied, name->name_len + decl->uri_len + 10)) {
        return -1;
    }
    Py_ssize_t start = out->len;
    sbuf_puts(out, " xmlns:");
    sbuf_put_utf8(out, name->name, name->name_len);
    sbuf_puts(out, "=\"");
    c14n_put_attr(out, decl->uri, decl->uri_len);
    sbuf_putc(out, '"');
    if (copy) {
        scope->copied += out->len - start; /* what was written: escaping can lengthen the URI */
    }
    return 0;
}

/* Emit the collected prefixed declarations and the xlink one in prefix order. Returns -1 with ValueError set when
   c14n_ns_put_decl refuses one. */
static int c14n_ns_put(sbuf *out, c14n_ns *scope, const th_node *node, Py_ssize_t count, int render_xlink) {
    Py_ssize_t index = 0;
    for (; index < count && c14n_ns_before_xlink(scope, scope->candidates[index]); index++) {
        if (c14n_ns_put_decl(out, scope, node, scope->candidates[index]) < 0) {
            return -1;
        }
    }
    if (render_xlink) {
        c14n_put_xlink_decl(out);
    }
    for (; index < count; index++) {
        if (c14n_ns_put_decl(out, scope, node, scope->candidates[index]) < 0) {
            return -1;
        }
    }
    return 0;
}

/* The end of the declaration that starts at start in tag, when lead is what it starts with; start otherwise. A
   declaration's value is escaped, so its closing quote is the first one after lead. */
static Py_ssize_t c14n_scoped_decl_end(const sbuf *tag, Py_ssize_t start, const char *lead) {
    Py_ssize_t length = (Py_ssize_t)strlen(lead);
    if (start + length > tag->len) {
        return start;
    }
    for (Py_ssize_t index = 0; index < length; index++) {
        if (tag->data[start + index] != (Py_UCS4)lead[index]) {
            return start;
        }
    }
    Py_ssize_t end = start + length;
    while (tag->data[end] != '"') {
        end++;
    }
    return end + 1;
}

/* The start tag of an element that renders prefixed declarations. c14n_left_out_open_tag writes the default and
   xlink declarations ahead of the attributes, so they are split off its output: the scope's declarations are written
   between them, in prefix order, and the attributes follow. Returns -1, with ValueError set when c14n_ns_put refused a
   declaration, once out has failed. */
static int c14n_scoped_open_tag(sbuf *out, th_tree *tree, th_node *node, const th_node *apex,
                                const c14n_left_out *left_out, const th_c14n_opts *opts, const th_node **xlink_binding,
                                c14n_ns *scope, Py_ssize_t count) {
    sbuf tag = {NULL, 0, 0, 0, 0};
    c14n_left_out_open_tag(&tag, tree, node, apex, left_out, opts, xlink_binding);
    if (tag.failed) {         /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        out->failed = 1;      /* GCOVR_EXCL_LINE: the split offsets below read the whole tag */
        PyMem_Free(tag.data); /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;            /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t default_end = c14n_scoped_decl_end(&tag, 1 + node->text_len, " xmlns=\"");
    Py_ssize_t xlink_end = c14n_scoped_decl_end(&tag, default_end, " xmlns:xlink=\"");
    sbuf_put_ucs4(out, tag.data, default_end);
    if (c14n_ns_put(out, scope, node, count, xlink_end != default_end) < 0) {
        out->failed = 1;
        PyMem_Free(tag.data);
        return -1;
    }
    sbuf_put_ucs4(out, tag.data + xlink_end, tag.len - xlink_end);
    PyMem_Free(tag.data);
    return 0;
}

/* Enter node, which the canonical form leaves out: its declarations are in scope for its children, which render them
   as their own. */
static int c14n_scoped_left_out(c14n_ns *scope, c14n_left_outs *left_outs, const th_node *node, const th_node *apex,
                                const c14n_left_out *left_out) {
    Py_ssize_t run = left_out != NULL ? left_out->run : scope->decls_len;
    c14n_ns_declare(scope, node);
    if (node->first_child == NULL) {
        c14n_ns_leave(scope, node);
        return 0;
    }
    return c14n_left_out_push(left_outs, node, apex, left_out, run);
}

/* Emit one node of the subtree rooted at apex, the scoped counterpart of c14n_step: an element pushes its declarations
   and visible uses, and pops them as it closes. */
static th_node *c14n_scoped_step(sbuf *out, th_tree *tree, th_node *node, const th_node *apex, const th_c14n_opts *opts,
                                 c14n_ns *scope, const th_node **xlink_binding, c14n_left_outs *left_outs) {
    th_node *descend = NULL;
    switch ((enum th_node_type)node->type) { /* GCOVR_EXCL_BR_LINE: node types are exhaustive */
    case TH_NODE_ELEMENT: {
        const c14n_left_out *left_out = c14n_left_out_of(left_outs, node->parent);
        if (node->atom == TH_TAG_UNKNOWN && !ser_xml_element_name_wellformed(node)) {
            if (c14n_scoped_left_out(scope, left_outs, node, apex, left_out) < 0) { /* GCOVR_EXCL_BR_LINE: OOM */
                out->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
                return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            descend = node->first_child;
            break;
        }
        Py_ssize_t count = c14n_ns_enter(scope, node, c14n_is_apex(node, apex, left_out),
                                         left_out != NULL ? left_out->run : scope->decls_len);
        if (count < 0) {
            out->failed = 1;
            return NULL;
        }
        if (count == 0) {
            c14n_left_out_open_tag(out, tree, node, apex, left_out, opts, xlink_binding);
        } else if (c14n_scoped_open_tag(out, tree, node, apex, left_out, opts, xlink_binding, scope, count) < 0) {
            return NULL;
        }
        if (node->first_child != NULL) {
            descend = node->first_child;
        } else {
            c14n_close_tag(out, node);
            c14n_ns_leave(scope, node);
        }
        break;
    }
    case TH_NODE_TEXT:
    case TH_NODE_CDATA: /* a CDATA section canonicalizes as its escaped character data */
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
    case TH_NODE_DOCTYPE: /* the document type declaration is dropped from the canonical form */
        break;
    case TH_NODE_CONTENT:
    case TH_NODE_DOCUMENT:
        descend = node->first_child; /* a transparent container emits only its children */
        break;
    }
    if (descend != NULL) {
        return descend;
    }
    while (node != apex) {
        if (*xlink_binding == node) {
            *xlink_binding = NULL;
        }
        if (node->next_sibling != NULL) {
            return node->next_sibling;
        }
        node = node->parent;
        if (c14n_left_out_of(left_outs, node) != NULL) {
            left_outs->len--;
            c14n_ns_leave(scope, node);
        } else if (node->type == TH_NODE_ELEMENT) {
            c14n_close_tag(out, node);
            c14n_ns_leave(scope, node);
        }
    }
    return NULL;
}

static void c14n_scoped_subtree(sbuf *out, th_tree *tree, th_node *apex, const th_c14n_opts *opts) {
    c14n_ns scope;
    /* GCOVR_EXCL_START: allocation failure cannot be forced from a test */
    if (c14n_ns_begin(&scope, tree, apex, opts) < 0) {
        out->failed = 1;
        return;
    }
    /* GCOVR_EXCL_STOP */
    const th_node *xlink_binding = !opts->exclusive && c14n_xlink_in_scope(tree, apex->parent) ? apex->parent : NULL;
    c14n_left_outs left_outs = {NULL, 0, 0};
    th_node *node = apex;
    while (node != NULL) {
        node = c14n_scoped_step(out, tree, node, apex, opts, &scope, &xlink_binding, &left_outs);
    }
    PyMem_Free(left_outs.items);
    c14n_ns_end(&scope);
}

/* The document walk of c14n_document, with each element child canonicalized through the scoped walk. */
static void c14n_scoped_document(sbuf *out, th_tree *tree, th_node *doc, const th_c14n_opts *opts) {
    int seen_root = 0;
    for (th_node *child = doc->first_child; child != NULL; child = child->next_sibling) {
        if (child->type == TH_NODE_ELEMENT) {
            c14n_scoped_subtree(out, tree, child, opts);
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

static Py_UCS4 *c14n_scoped_canonicalize(th_tree *tree, th_node *node, const th_c14n_opts *opts, Py_ssize_t *out_len) {
    sbuf out = {NULL, 0, 0, 0, 0};
    if (node->type == TH_NODE_DOCUMENT) {
        sbuf_presize_for_c14n(&out, tree);
        c14n_scoped_document(&out, tree, node, opts);
    } else {
        c14n_scoped_subtree(&out, tree, node, opts);
    }
    return sbuf_finish(&out, out_len);
}

#endif
