/* RELAX NG simplification steps 4.5 to 4.7: resolve each include and externalRef href against the element's base URI,
   read the referenced schema through the shared resource policy, and graft it into the schema tree, so the compiler
   sees one grammar. Included once into validate/schema.c after relaxng.h; every definition is static. */

#ifndef TURBOHTML_VALIDATE_RELAXNG_RESOURCES_H
#define TURBOHTML_VALIDATE_RELAXNG_RESOURCES_H

#include "core/resource.h"

typedef struct {
    th_schema *schema;
    th_resource_policy policy;
    PyObject *urljoin;
    PyObject *active; /* resolved paths of the documents being loaded, outermost first, for the 4.6/4.7 loop check */
} rng_loader;

typedef struct {
    th_node **items;
    Py_ssize_t len;
    size_t cap;
} rng_nodes;

static PyObject *rng_str(const Py_UCS4 *data, Py_ssize_t len) {
    return PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, data, len);
}

static int rng_collect_references(th_schema *schema, th_node *node, rng_nodes *out) {
    if (node->type != TH_NODE_ELEMENT) {
        return 0;
    }
    if (rng_is_reference(schema, node)) {
        if (out->len == (Py_ssize_t)out->cap) {
            size_t capacity;
            size_t bytes;
            /* GCOVR_EXCL_BR_START: a schema tree cannot hold enough references to overflow size_t. */
            if (!th_grow_cap((size_t)out->len + 1, out->cap, 8, sizeof(th_node *), &capacity, &bytes)) {
                PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
                return -1;        /* GCOVR_EXCL_LINE */
            }
            /* GCOVR_EXCL_BR_STOP */
            th_node **grown = PyMem_Realloc(out->items, bytes);
            if (grown == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
                PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
                return -1;        /* GCOVR_EXCL_LINE */
            }
            out->items = grown;
            out->cap = capacity;
        }
        out->items[out->len++] = node;
    }
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        if (rng_collect_references(schema, child, out) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;                                        /* GCOVR_EXCL_LINE */
        }
    }
    return 0;
}

/* The base URI of node (XML Base 4.2): the document URI with every xml:base from the document element down to node
   applied in turn. A detached subtree, the root of a document just read, ends the walk at its own root. */
static PyObject *rng_base_uri(rng_loader *loader, th_node *node, PyObject *document_uri) {
    PyObject *parent_uri = node->parent == NULL || node->parent->type != TH_NODE_ELEMENT
                               ? Py_NewRef(document_uri)
                               : rng_base_uri(loader, node->parent, document_uri);
    const th_node_attr *base = attr_exact(loader->schema->tree, node, "xml:base", 8);
    if (parent_uri == NULL || base == NULL) {
        return parent_uri;
    }
    PyObject *value = rng_str(base->value, base->value_len);
    if (value == NULL) {       /* GCOVR_EXCL_BR_LINE: allocation failure */
        Py_DECREF(parent_uri); /* GCOVR_EXCL_LINE */
        return NULL;           /* GCOVR_EXCL_LINE */
    }
    PyObject *joined = PyObject_CallFunctionObjArgs(loader->urljoin, parent_uri, value, NULL);
    Py_DECREF(value);
    Py_DECREF(parent_uri);
    return joined;
}

static int rng_raise_loop(PyObject *active, PyObject *path) {
    PyObject *chain = PyList_New(0);
    if (chain == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;       /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t count = PyList_GET_SIZE(active);
    for (Py_ssize_t index = 0; index <= count; index++) {
        PyObject *part = PyObject_Str(index < count ? PyList_GET_ITEM(active, index) : path);
        if (part == NULL || PyList_Append(chain, part) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            Py_XDECREF(part);                                 /* GCOVR_EXCL_LINE */
            Py_DECREF(chain);                                 /* GCOVR_EXCL_LINE */
            return -1;                                        /* GCOVR_EXCL_LINE */
        }
        Py_DECREF(part);
    }
    PyObject *separator = PyUnicode_FromString(" -> ");
    PyObject *joined = separator == NULL ? NULL : PyUnicode_Join(separator, chain); /* GCOVR_EXCL_BR_LINE: alloc */
    Py_XDECREF(separator);
    Py_DECREF(chain);
    if (joined == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;        /* GCOVR_EXCL_LINE */
    }
    PyErr_Format(PyExc_ValueError, "circular RELAX NG reference: %U", joined);
    Py_DECREF(joined);
    return -1;
}

/* The pattern elements of the RELAX NG grammar (section 3), the only roots an externalRef may load (4.6). */
static int rng_is_pattern(th_schema *schema, th_node *node) {
    static const char *const patterns[] = {"element",  "attribute",  "group",       "interleave", "choice",
                                           "optional", "zeroOrMore", "oneOrMore",   "list",       "mixed",
                                           "ref",      "parentRef",  "empty",       "text",       "value",
                                           "data",     "notAllowed", "externalRef", "grammar"};
    for (size_t index = 0; index < sizeof(patterns) / sizeof(patterns[0]); index++) {
        if (is_schema_el(schema, node, RNG_NS, patterns[index])) {
            return 1;
        }
    }
    return 0;
}

static int rng_is_overridden(th_schema *schema, th_node *node, const Py_UCS4 *name, Py_ssize_t name_len) {
    if (name == NULL) {
        return is_schema_el(schema, node, RNG_NS, "start");
    }
    if (!is_schema_el(schema, node, RNG_NS, "define")) {
        return 0;
    }
    const th_node_attr *defined = attr_exact(schema->tree, node, "name", 4);
    return defined != NULL && u_eq_u(defined->value, defined->value_len, name, name_len);
}

/* Section 4.7 removes the start components (name NULL) or the defines called name from an included grammar, looking
   through div, and an override that matches nothing is an error. */
static int rng_remove_overridden(th_schema *schema, th_node *container, const Py_UCS4 *name, Py_ssize_t name_len) {
    int found = 0;
    th_node *child = container->first_child;
    while (child != NULL) {
        th_node *next = child->next_sibling;
        if (rng_is_overridden(schema, child, name, name_len)) {
            th_node_remove(child);
            found = 1;
        } else if (is_schema_el(schema, child, RNG_NS, "div")) {
            found |= rng_remove_overridden(schema, child, name, name_len);
        }
        child = next;
    }
    return found;
}

static int rng_apply_overrides(th_schema *schema, th_node *overrides, th_node *grammar, PyObject *path) {
    for (th_node *child = overrides->first_child; child != NULL; child = child->next_sibling) {
        if (is_schema_el(schema, child, RNG_NS, "div")) {
            if (rng_apply_overrides(schema, child, grammar, path) < 0) {
                return -1;
            }
        } else if (is_schema_el(schema, child, RNG_NS, "start")) {
            if (!rng_remove_overridden(schema, grammar, NULL, 0)) {
                PyErr_Format(PyExc_ValueError,
                             "RELAX NG include overrides start, which the included grammar does not define: %S", path);
                return -1;
            }
        } else if (is_schema_el(schema, child, RNG_NS, "define")) {
            const th_node_attr *name = attr_exact(schema->tree, child, "name", 4);
            if (name != NULL && !rng_remove_overridden(schema, grammar, name->value, name->value_len)) {
                char buffer[256];
                PyErr_Format(PyExc_ValueError,
                             "RELAX NG include overrides define '%s', which the included grammar does not define: %S",
                             name_utf8(name->value, name->value_len, buffer, sizeof(buffer)), path);
                return -1;
            }
        }
    }
    return 0;
}

/* Rename an element to div in its own namespace prefix, the form 4.7 turns an include and its grammar into. */
static int rng_rename_div(th_schema *schema, th_node *node) {
    const Py_UCS4 *local;
    const Py_UCS4 *prefix;
    Py_ssize_t local_len = 0;
    Py_ssize_t prefix_len = 0;
    split_prefix(node->text, node->text_len, &local, &local_len, &prefix, &prefix_len);
    Py_UCS4 tag[256];
    if (prefix_len + 4 > (Py_ssize_t)(sizeof(tag) / sizeof(tag[0]))) {
        PyErr_SetString(PyExc_ValueError, "RELAX NG namespace prefix is too long");
        return -1;
    }
    Py_ssize_t length = 0;
    if (prefix_len > 0) {
        memcpy(tag, prefix, (size_t)prefix_len * sizeof(Py_UCS4));
        tag[prefix_len] = ':';
        length = prefix_len + 1;
    }
    tag[length++] = 'd';
    tag[length++] = 'i';
    tag[length++] = 'v';
    if (th_node_rename(schema->tree, node, tag, length, TH_TAG_UNKNOWN) < 0) { /* GCOVR_EXCL_BR_LINE: allocation */
        PyErr_NoMemory();                                                      /* GCOVR_EXCL_LINE */
        return -1;                                                             /* GCOVR_EXCL_LINE */
    }
    return 0;
}

static th_node *rng_resolve_subtree(rng_loader *loader, th_node *top, PyObject *document_uri);

/* Read the document reference names into the schema tree, already resolved and pruned; NULL with an exception set. */
static th_node *rng_read_reference(rng_loader *loader, PyObject *path, int include) {
    th_schema *schema = loader->schema;
    PyObject *text = th_resource_read_text(&loader->policy, path);
    if (text == NULL) {
        return NULL;
    }
    th_tree *loaded = parse_schema_source(text);
    if (loaded == NULL) {
        Py_DECREF(text);
        return NULL;
    }
    if (th_node_check_max_depth(th_tree_document(loaded), TH_VALIDATE_MAX_DEPTH, "schema compilation") < 0) {
        th_tree_free(loaded);
        Py_DECREF(text);
        return NULL;
    }
    /* loaded's text nodes are spans borrowing text, so text has to stay until the copy has materialized them */
    th_node *copy = th_tree_copy_node(schema->tree, loaded, document_root(loaded));
    th_tree_free(loaded);
    Py_DECREF(text);
    if (copy == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
        return NULL;      /* GCOVR_EXCL_LINE */
    }
    if (include ? !is_schema_el(schema, copy, RNG_NS, "grammar") : !rng_is_pattern(schema, copy)) {
        PyErr_Format(PyExc_ValueError,
                     include ? "RELAX NG include target is not a grammar: %S"
                             : "RELAX NG externalRef target is not a pattern: %S",
                     path);
        return NULL;
    }
    rng_prune_annotations(schema, copy);
    PyObject *uri = PyObject_CallMethod(path, "as_uri", NULL);
    if (uri == NULL) { /* GCOVR_EXCL_BR_LINE: a resolved path is absolute, so as_uri cannot fail */
        return NULL;   /* GCOVR_EXCL_LINE */
    }
    if (PyList_Append(loader->active, path) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        Py_DECREF(uri);                            /* GCOVR_EXCL_LINE */
        return NULL;                               /* GCOVR_EXCL_LINE */
    }
    th_node *resolved = NULL;
    /* GCOVR_EXCL_BR_START: reaching the interpreter's C stack limit takes a chain of thousands of files */
    if (Py_EnterRecursiveCall(" while resolving RELAX NG references") == 0) {
        /* GCOVR_EXCL_BR_STOP */
        resolved = rng_resolve_subtree(loader, copy, uri);
        Py_LeaveRecursiveCall();
    }
    Py_DECREF(uri);
    (void)PySequence_DelItem(loader->active, PyList_GET_SIZE(loader->active) - 1);
    return resolved;
}

/* Replace one include or externalRef with the schema its href names; returns the node now standing in its place. */
static th_node *rng_load_reference(rng_loader *loader, th_node *reference, PyObject *document_uri) {
    th_schema *schema = loader->schema;
    int include = is_schema_el(schema, reference, RNG_NS, "include");
    const th_node_attr *href = attr_exact(schema->tree, reference, "href", 4);
    for (Py_ssize_t index = 0; index < href->value_len; index++) {
        if (href->value[index] == '#') { /* 4.5: the href URI reference must not have a fragment identifier */
            PyErr_SetString(PyExc_ValueError, "RELAX NG href must not contain a fragment identifier");
            return NULL;
        }
    }
    PyObject *base = rng_base_uri(loader, reference, document_uri);
    if (base == NULL) {
        return NULL;
    }
    PyObject *href_text = rng_str(href->value, href->value_len);
    if (href_text == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        Py_DECREF(base);     /* GCOVR_EXCL_LINE */
        return NULL;         /* GCOVR_EXCL_LINE */
    }
    PyObject *target = PyObject_CallFunctionObjArgs(loader->urljoin, base, href_text, NULL);
    Py_DECREF(href_text);
    Py_DECREF(base);
    if (target == NULL) {
        return NULL;
    }
    PyObject *located = th_resource_path(&loader->policy, target, "href");
    Py_DECREF(target);
    if (located == NULL) {
        return NULL;
    }
    PyObject *path = th_resource_resolve(located);
    Py_DECREF(located);
    if (path == NULL) { /* GCOVR_EXCL_BR_LINE: Path.resolve fails here only on allocation */
        return NULL;    /* GCOVR_EXCL_LINE */
    }
    if (th_resource_check_root(&loader->policy, path) < 0) {
        Py_DECREF(path);
        return NULL;
    }
    int looping = PySequence_Contains(loader->active, path);
    th_node *copy = looping ? NULL : rng_read_reference(loader, path, include);
    if (looping) {
        rng_raise_loop(loader->active, path);
    } else if (copy != NULL && include) {
        if (rng_apply_overrides(schema, reference, copy, path) < 0) {
            copy = NULL;
        }
    }
    Py_DECREF(path);
    if (copy == NULL) {
        return NULL;
    }
    /* 4.3 gives each document its own datatypeLibrary inheritance, so the graft must not pick up the referrer's */
    if (attr_exact(schema->tree, copy, "datatypeLibrary", 15) == NULL) {
        /* GCOVR_EXCL_BR_START: setting an attribute fails only on allocation */
        if (th_node_attr_set(schema->tree, copy, "datatypeLibrary", 15, EMPTY_UCS4, 0, 1) < 0) {
            PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
            return NULL;      /* GCOVR_EXCL_LINE */
        }
        /* GCOVR_EXCL_BR_STOP */
    }
    if (include) {
        if (rng_rename_div(schema, reference) < 0 || rng_rename_div(schema, copy) < 0) {
            return NULL;
        }
        (void)th_node_attr_del(schema->tree, reference, "href", 4);
        th_node_insert_before(reference, copy, reference->first_child);
        return reference;
    }
    const th_node_attr *ns = attr_exact(schema->tree, reference, "ns", 2);
    if (ns != NULL && attr_exact(schema->tree, copy, "ns", 2) == NULL) {
        /* GCOVR_EXCL_BR_START: setting an attribute fails only on allocation */
        if (th_node_attr_set(schema->tree, copy, "ns", 2, ns->value, ns->value_len, 1) < 0) {
            PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
            return NULL;      /* GCOVR_EXCL_LINE */
        }
        /* GCOVR_EXCL_BR_STOP */
    }
    if (reference->parent != NULL) {
        th_node_insert_before(reference->parent, copy, reference);
        th_node_remove(reference);
    }
    return copy;
}

/* Resolve every reference at or below top; the subtree's root afterwards, which replaces top when top was itself an
   externalRef, or NULL with an exception set. */
static th_node *rng_resolve_subtree(rng_loader *loader, th_node *top, PyObject *document_uri) {
    rng_nodes references = {0};
    if (rng_collect_references(loader->schema, top, &references) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;                                                    /* GCOVR_EXCL_LINE */
    }
    th_node *root = top;
    for (Py_ssize_t index = 0; index < references.len; index++) {
        th_node *replacement = rng_load_reference(loader, references.items[index], document_uri);
        if (replacement == NULL) {
            root = NULL;
            break;
        }
        if (references.items[index] == top) {
            root = replacement;
        }
    }
    PyMem_Free(references.items);
    return root;
}

/* Graft every referenced schema into the tree before compilation; 0, or -1 with an exception set. */
static int rng_resolve_resources(th_schema *schema, PyObject *base_url, PyObject *include_root) {
    if (!schema->rng_references) {
        return 0;
    }
    if (base_url == Py_None) {
        PyErr_SetString(PyExc_ValueError, "RELAX NG include and externalRef need a base_url to resolve href against");
        return -1;
    }
    rng_loader loader = {.schema = schema};
    if (th_resource_policy_init(&loader.policy, "RELAX NG", "include_root", include_root) < 0) {
        return -1;
    }
    PyObject *located = th_resource_path(&loader.policy, base_url, "base_url");
    PyObject *path = located == NULL ? NULL : th_resource_resolve(located);
    Py_XDECREF(located);
    PyObject *uri = path == NULL ? NULL : PyObject_CallMethod(path, "as_uri", NULL);
    int status = -1;
    if (uri != NULL) {
        PyObject *parse = PyImport_ImportModule("urllib.parse");
        loader.urljoin =
            parse == NULL ? NULL : PyObject_GetAttrString(parse, "urljoin"); /* GCOVR_EXCL_BR_LINE: bundled module */
        Py_XDECREF(parse);
        loader.active = PyList_New(0);
        /* GCOVR_EXCL_BR_START: only allocation fails the bundled urllib lookup and the one-item list */
        if (loader.urljoin != NULL && loader.active != NULL && PyList_Append(loader.active, path) == 0) {
            /* GCOVR_EXCL_BR_STOP */
            th_node *root = rng_resolve_subtree(&loader, schema->root, uri);
            if (root != NULL) {
                schema->root = root;
                status = th_node_check_max_depth(th_tree_document(schema->tree), TH_VALIDATE_MAX_DEPTH,
                                                 "schema compilation");
            }
        }
        Py_XDECREF(loader.active);
        Py_XDECREF(loader.urljoin);
        Py_DECREF(uri);
    }
    Py_XDECREF(path);
    th_resource_policy_clear(&loader.policy);
    return status;
}

#endif /* TURBOHTML_VALIDATE_RELAXNG_RESOURCES_H */
