/* Structured-data extraction over a parsed tree, the C engine behind the Document.structured_data()/json_ld()/
   opengraph()/microdata() methods (wired into the document method table in dom/document.c).

   Scrapers reach for extruct or metadata_parser to pull JSON-LD, Microdata, and OpenGraph/Twitter metadata out of a
   page. The walk that locates each format runs here in C, under the per-tree critical section so a concurrent mutation
   cannot relink the tree mid-walk. The gathered values hold no reference back into the tree; the C pass assembles the
   plain dict/list/str pieces, then hands Microdata items to the MicrodataItem record class and the combined view to the
   StructuredData record class that the thin Python facade defines and registers on import (turbohtml._structured_data),
   so the typed result classes live in Python while all extraction stays in C. JSON-LD is the exception that proves the
   rule: the <script type="application/ld+json"> texts are gathered into a list of str in a pure-C pass, the critical
   section is released, and the stdlib json parsing runs in the same facade so the JSON grammar is not reinvented here.
   RDFa and microformats2 are a documented later phase. */

#include "core/common.h"

#include "tokenizer/binding.h" /* Py_BEGIN_CRITICAL_SECTION shim for the GIL/pre-3.13 build */
#include "dom/nodes.h"

#include <stdlib.h>
#include <string.h>

/* The HTML script type that flags a JSON-LD block, matched case-insensitively after trimming. */
static const char JSON_LD_TYPE[] = "application/ld+json";

/* Whether the UCS4 run, once surrounding ASCII whitespace is trimmed, equals `lit` ignoring ASCII case. */
static int ucs4_ieq_trimmed(const Py_UCS4 *value, Py_ssize_t len, const char *lit, Py_ssize_t lit_len) {
    Py_ssize_t start = 0;
    Py_ssize_t end = len;
    while (start < end && is_space(value[start])) {
        start++;
    }
    while (end > start && is_space(value[end - 1])) {
        end--;
    }
    if (end - start != lit_len) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < lit_len; index++) {
        Py_UCS4 c = value[start + index];
        Py_UCS4 folded = (c >= 'A' && c <= 'Z') ? (c | 0x20) : c;
        if (folded != (Py_UCS4)(unsigned char)lit[index]) {
            return 0;
        }
    }
    return 1;
}

/* Whether the UCS4 run begins with the ASCII literal `prefix`. */
static int ucs4_has_prefix(const Py_UCS4 *value, Py_ssize_t len, const char *prefix, Py_ssize_t prefix_len) {
    if (len < prefix_len) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < prefix_len; index++) {
        if (value[index] != (Py_UCS4)(unsigned char)prefix[index]) {
            return 0;
        }
    }
    return 1;
}

/* Whether a property/name token names an OpenGraph or Twitter card metadata key. An array+loop keeps the branch count
   stable instead of a chained || a compiler can leave with an unreachable arm. */
static int is_social_key(const Py_UCS4 *value, Py_ssize_t len) {
    static const char *const prefixes[] = {"og:", "twitter:"};
    static const Py_ssize_t prefix_lens[] = {3, 8};
    for (size_t index = 0; index < sizeof(prefixes) / sizeof(prefixes[0]); index++) {
        if (ucs4_has_prefix(value, len, prefixes[index], prefix_lens[index])) {
            return 1;
        }
    }
    return 0;
}

/* A named attribute's value as a new str, the empty string when the attribute is absent or valueless (the way
   getAttribute reports a missing URL attribute as ""). NULL only on the excluded allocation-failure path. */
static PyObject *attr_str_or_empty(th_tree *tree, th_node *node, const char *name, Py_ssize_t name_len) {
    Py_ssize_t index = th_node_attr_find(tree, node, name, name_len);
    if (index < 0 || node->attrs[index].value == NULL) {
        return PyUnicode_FromString("");
    }
    return ucs4_to_str(node->attrs[index].value, node->attrs[index].value_len);
}

/* The element tags whose Microdata property value is a single attribute rather than text content. `url` marks the
   URL-valued attributes (a/area/link href, media src, object data), whose value the spec resolves as a URL and so
   strips of surrounding ASCII whitespace; data/meter value and meta content are taken verbatim. */
typedef struct {
    uint16_t atom;
    const char *attr;
    Py_ssize_t attr_len;
    unsigned char url;
} microdata_attr_prop;

static const microdata_attr_prop MICRODATA_ATTR_PROPS[] = {
    {TH_TAG_AUDIO, "src", 3, 1},   {TH_TAG_EMBED, "src", 3, 1},    {TH_TAG_IFRAME, "src", 3, 1},
    {TH_TAG_IMG, "src", 3, 1},     {TH_TAG_SOURCE, "src", 3, 1},   {TH_TAG_TRACK, "src", 3, 1},
    {TH_TAG_VIDEO, "src", 3, 1},   {TH_TAG_A, "href", 4, 1},       {TH_TAG_AREA, "href", 4, 1},
    {TH_TAG_LINK, "href", 4, 1},   {TH_TAG_OBJECT, "data", 4, 1},  {TH_TAG_DATA, "value", 5, 0},
    {TH_TAG_METER, "value", 5, 0}, {TH_TAG_META, "content", 7, 0},
};

static PyObject *build_item(module_state *state, th_tree *tree, th_node *element);

/* A URL-valued attribute's value with leading and trailing ASCII whitespace stripped (the URL parser trims it), or the
   empty string when the attribute is absent or valueless. NULL only on the excluded allocation-failure path. */
static PyObject *attr_url_or_empty(th_tree *tree, th_node *node, const char *name, Py_ssize_t name_len) {
    Py_ssize_t index = th_node_attr_find(tree, node, name, name_len);
    if (index < 0 || node->attrs[index].value == NULL) {
        return PyUnicode_FromString("");
    }
    const Py_UCS4 *value = node->attrs[index].value;
    Py_ssize_t start = 0;
    Py_ssize_t end = node->attrs[index].value_len;
    while (start < end && is_space(value[start])) {
        start++;
    }
    while (end > start && is_space(value[end - 1])) {
        end--;
    }
    return ucs4_to_str(value + start, end - start);
}

/* A named attribute's value as a new str, or None when the attribute is absent or valueless. NULL only on the excluded
   allocation-failure path. */
static PyObject *attr_value_or_none(const th_node_attr *attr) {
    if (attr == NULL || attr->value == NULL) {
        return Py_NewRef(Py_None);
    }
    return ucs4_to_str(attr->value, attr->value_len);
}

/* One Microdata property element's value: a nested MicrodataItem when it carries itemscope, otherwise the HTML
   Microdata value algorithm (a URL attribute for the link/media tags, datetime or text for <time>, content for <meta>,
   else the element's text content). NULL only on the excluded allocation-failure path. */
static PyObject *microdata_value(module_state *state, th_tree *tree, th_node *element) {
    if (find_node_attr(element, TH_ATTR_ITEMSCOPE) != NULL) {
        return build_item(state, tree, element);
    }
    if (element->atom == TH_TAG_TIME) {
        Py_ssize_t datetime = th_node_attr_find(tree, element, "datetime", 8);
        if (datetime >= 0 && element->attrs[datetime].value != NULL) {
            return ucs4_to_str(element->attrs[datetime].value, element->attrs[datetime].value_len);
        }
        return str_from_accessor(th_node_text, tree, element);
    }
    for (size_t index = 0; index < sizeof(MICRODATA_ATTR_PROPS) / sizeof(MICRODATA_ATTR_PROPS[0]); index++) {
        const microdata_attr_prop *prop = &MICRODATA_ATTR_PROPS[index];
        if (element->atom == prop->atom) {
            return prop->url ? attr_url_or_empty(tree, element, prop->attr, prop->attr_len)
                             : attr_str_or_empty(tree, element, prop->attr, prop->attr_len);
        }
    }
    return str_from_accessor(th_node_text, tree, element);
}

/* Append `value` under every whitespace-separated name in the itemprop run to the properties dict, each name mapping to
   a list of values in document order. -1 only on the excluded allocation-failure path. */
static int add_property(PyObject *properties, const Py_UCS4 *names, Py_ssize_t names_len, PyObject *value) {
    PyObject *tokens = split_token_list(names, names_len);
    if (tokens == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t count = PyList_GET_SIZE(tokens);
    for (Py_ssize_t index = 0; index < count; index++) {
        PyObject *key = PyList_GET_ITEM(tokens, index);
        PyObject *bucket = PyDict_GetItemWithError(properties, key);
        if (bucket != NULL) {
            Py_INCREF(bucket);
        } else {
            if (PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: lookup fails only on an unforceable error */
                goto error;         /* GCOVR_EXCL_LINE: error path */
            }
            bucket = PyList_New(0);
            if (bucket == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                goto error;       /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            if (PyDict_SetItem(properties, key, bucket) < 0) { /* GCOVR_EXCL_BR_LINE: insert fails only on alloc */
                Py_DECREF(bucket);                             /* GCOVR_EXCL_LINE: allocation-failure path */
                goto error;                                    /* GCOVR_EXCL_LINE */
            }
        }
        int append_failed = PyList_Append(bucket, value) < 0;
        Py_DECREF(bucket);
        if (append_failed) { /* GCOVR_EXCL_BR_LINE: append fails only on unforceable allocation */
            goto error;      /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    Py_DECREF(tokens);
    return 0;
error:                 /* GCOVR_EXCL_LINE: shared cleanup for the unreachable allocation-failure arms */
    Py_DECREF(tokens); /* GCOVR_EXCL_LINE */
    return -1;         /* GCOVR_EXCL_LINE */
}

/* A growable array of element pointers backing the property crawl's memory, pending, and results lists. A realloc
   failure is the only failure and cannot be forced from a test. */
typedef struct {
    th_node **items;
    Py_ssize_t len;
    Py_ssize_t cap;
} node_stack;

/* Append `node`; -1 only on the excluded allocation-failure path. */
static int node_stack_push(node_stack *stack, th_node *node) {
    if (stack->len == stack->cap) {
        Py_ssize_t cap = stack->cap < 8 ? 8 : stack->cap * 2;
        th_node **items = PyMem_Realloc(stack->items, (size_t)cap * sizeof(th_node *));
        if (items == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        stack->items = items;
        stack->cap = cap;
    }
    stack->items[stack->len++] = node;
    return 0;
}

static int node_stack_contains(const node_stack *stack, const th_node *node) {
    for (Py_ssize_t index = 0; index < stack->len; index++) {
        if (stack->items[index] == node) {
            return 1;
        }
    }
    return 0;
}

/* The first element in `document` (pre-order) whose id attribute equals the UCS4 run [id, id + id_len), or NULL when
   none matches. ids are compared case-sensitively, the way getElementById does. */
static th_node *element_by_id(th_node *document, const Py_UCS4 *id, Py_ssize_t id_len) {
    for (th_node *node = document->first_child; node != NULL; node = preorder_next(node, document)) {
        if (node->type != TH_NODE_ELEMENT) {
            continue;
        }
        const th_node_attr *attr = find_node_attr(node, TH_ATTR_ID);
        if (attr != NULL && attr->value != NULL && attr->value_len == id_len &&
            memcmp(attr->value, id, (size_t)id_len * sizeof(Py_UCS4)) == 0) {
            return node;
        }
    }
    return NULL;
}

/* Push every id in root's itemref attribute onto `pending`, resolving each token to the first element carrying it (an
   unresolved token is skipped, per the spec). -1 only on the excluded allocation-failure path. */
static int push_itemref_targets(th_tree *tree, th_node *root, node_stack *pending) {
    Py_ssize_t itemref = th_node_attr_find(tree, root, "itemref", 7);
    if (itemref < 0 || root->attrs[itemref].value == NULL) {
        return 0;
    }
    const Py_UCS4 *value = root->attrs[itemref].value;
    Py_ssize_t value_len = root->attrs[itemref].value_len;
    th_node *document = th_tree_document(tree);
    Py_ssize_t cursor = 0;
    while (cursor < value_len) {
        while (cursor < value_len && is_space(value[cursor])) {
            cursor++;
        }
        Py_ssize_t start = cursor;
        while (cursor < value_len && !is_space(value[cursor])) {
            cursor++;
        }
        if (cursor > start) {
            th_node *target = element_by_id(document, &value[start], cursor - start);
            if (target != NULL) {
                if (node_stack_push(pending, target) < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
                    return -1;                              /* GCOVR_EXCL_LINE: allocation-failure path */
                }
            }
        }
    }
    return 0;
}

/* Add the element children of `parent` to `pending`. -1 only on the excluded allocation-failure path. */
static int push_element_children(th_node *parent, node_stack *pending) {
    for (th_node *child = parent->first_child; child != NULL; child = child->next_sibling) {
        if (child->type == TH_NODE_ELEMENT) {
            if (node_stack_push(pending, child) < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
                return -1;                             /* GCOVR_EXCL_LINE: allocation-failure path */
            }
        }
    }
    return 0;
}

/* Gather the property elements of the item rooted at `root` into `results`, following the WHATWG "the properties of an
   item" algorithm: crawl root's descendants plus every element its itemref names, stopping at a nested itemscope
   (whose descendants belong to that nested item) and visiting each element at most once so an itemref cycle
   terminates. -1 only on the excluded allocation-failure path. */
static int crawl_item_properties(th_tree *tree, th_node *root, node_stack *results) {
    node_stack memory = {NULL, 0, 0};
    node_stack pending = {NULL, 0, 0};
    int status = -1;
    if (node_stack_push(&memory, root) < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        goto done;                            /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (push_element_children(root, &pending) < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        goto done;                                   /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (push_itemref_targets(tree, root, &pending) < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        goto done;                                        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    while (pending.len > 0) {
        th_node *current = pending.items[--pending.len];
        if (node_stack_contains(&memory, current)) {
            continue; /* already crawled: a microdata error the spec skips */
        }
        if (node_stack_push(&memory, current) < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
            goto done;                               /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (find_node_attr(current, TH_ATTR_ITEMSCOPE) == NULL) {
            if (push_element_children(current, &pending) < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
                goto done;                                      /* GCOVR_EXCL_LINE: allocation-failure path */
            }
        }
        const th_node_attr *itemprop = find_node_attr(current, TH_ATTR_ITEMPROP);
        /* an empty (itemprop="") or valueless (itemprop) attribute carries value == NULL, so a non-NULL value already
           names at least one property; no separate length check is needed */
        if (itemprop != NULL && itemprop->value != NULL) {
            if (node_stack_push(results, current) < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
                goto done;                               /* GCOVR_EXCL_LINE: allocation-failure path */
            }
        }
    }
    status = 0;
done:
    PyMem_Free(memory.items);
    PyMem_Free(pending.items);
    return status;
}

/* Depth of `node` below the document root, its number of ancestors. */
static Py_ssize_t node_depth(th_node *node) {
    Py_ssize_t depth = 0;
    for (th_node *parent = node->parent; parent != NULL; parent = parent->parent) {
        depth++;
    }
    return depth;
}

/* Negative when `left` precedes `right` in document (pre-order) order, positive otherwise; never called with equal
   nodes, so the ancestor-equal arm is the one-is-an-ancestor-of-the-other case. */
static int node_before(th_node *left, th_node *right) {
    Py_ssize_t left_depth = node_depth(left);
    Py_ssize_t right_depth = node_depth(right);
    th_node *walk_left = left;
    th_node *walk_right = right;
    for (Py_ssize_t index = left_depth; index > right_depth; index--) {
        walk_left = walk_left->parent;
    }
    for (Py_ssize_t index = right_depth; index > left_depth; index--) {
        walk_right = walk_right->parent;
    }
    if (walk_left == walk_right) {
        return left_depth < right_depth ? -1 : 1;
    }
    while (walk_left->parent != walk_right->parent) {
        walk_left = walk_left->parent;
        walk_right = walk_right->parent;
    }
    for (th_node *sibling = walk_left->next_sibling; sibling != NULL; sibling = sibling->next_sibling) {
        if (sibling == walk_right) {
            return -1;
        }
    }
    return 1;
}

static int node_ptr_before(const void *left, const void *right) {
    return node_before(*(th_node *const *)left, *(th_node *const *)right);
}

/* Crawl the properties of the item rooted at `element` into `properties`, in document (tree) order per the spec's
   final sort, each itemprop name mapping to its list of values. -1 only on the excluded allocation-failure path. */
static int collect_properties(module_state *state, th_tree *tree, th_node *element, PyObject *properties) {
    node_stack results = {NULL, 0, 0};
    int status = -1;
    if (crawl_item_properties(tree, element, &results) < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        goto done;                                            /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (results.len > 1) {
        qsort(results.items, (size_t)results.len, sizeof(th_node *), node_ptr_before);
    }
    for (Py_ssize_t index = 0; index < results.len; index++) {
        th_node *property = results.items[index];
        const th_node_attr *itemprop = find_node_attr(property, TH_ATTR_ITEMPROP);
        PyObject *value = microdata_value(state, tree, property);
        if (value == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            goto done;       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        int failed = add_property(properties, itemprop->value, itemprop->value_len, value) < 0;
        Py_DECREF(value);
        if (failed) {  /* GCOVR_EXCL_BR_LINE: allocation-failure path */
            goto done; /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    status = 0;
done:
    PyMem_Free(results.items);
    return status;
}

/* Steal `value` into slot `index` of `tuple`, returning -1 only on the excluded allocation-failure path (a NULL value
   the section builder could not allocate). */
static int tuple_set_or_fail(PyObject *tuple, Py_ssize_t index, PyObject *value) {
    if (value == NULL) { /* GCOVR_EXCL_BR_LINE: the section is built only from unforceable allocations */
        return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyTuple_SET_ITEM(tuple, index, value);
    return 0;
}

/* Build one MicrodataItem(type, id, properties): the verbatim itemtype / itemid attribute (or None when absent or
   valueless) and a properties mapping of each itemprop name to its list of values, each value a str or a nested
   MicrodataItem. NULL only on the excluded allocation-failure path. */
static PyObject *build_item(module_state *state, th_tree *tree, th_node *element) {
    PyObject *properties = PyDict_New();
    if (properties == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;          /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (collect_properties(state, tree, element, properties) < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        Py_DECREF(properties);                                      /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;                                                /* GCOVR_EXCL_LINE */
    }
    PyObject *type_obj = attr_value_or_none(find_node_attr(element, TH_ATTR_ITEMTYPE));
    if (type_obj == NULL) {    /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        Py_DECREF(properties); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;           /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t itemid = th_node_attr_find(tree, element, "itemid", 6);
    PyObject *id_obj = attr_value_or_none(itemid >= 0 ? &element->attrs[itemid] : NULL);
    if (id_obj == NULL) {      /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        Py_DECREF(type_obj);   /* GCOVR_EXCL_LINE: allocation-failure path */
        Py_DECREF(properties); /* GCOVR_EXCL_LINE */
        return NULL;           /* GCOVR_EXCL_LINE */
    }
    PyObject *item = PyObject_CallFunctionObjArgs(state->microdata_item_type, type_obj, id_obj, properties, NULL);
    Py_DECREF(type_obj);
    Py_DECREF(id_obj);
    Py_DECREF(properties);
    return item;
}

/* Gather the verbatim text of every <script type="application/ld+json"> under the document into a list of str, leaving
   the JSON parsing to the Python facade. NULL only on the excluded allocation-failure path. */
static PyObject *gather_json_ld(PyObject *self) {
    PyObject *texts = PyList_New(0);
    if (texts == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_tree *tree = tree_of(self);
    th_node *root = ((NodeObject *)self)->node;
    int failed = 0;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    for (th_node *node = root->first_child; node != NULL; node = preorder_next(node, root)) {
        if (node->type != TH_NODE_ELEMENT || node->atom != TH_TAG_SCRIPT) {
            continue;
        }
        const th_node_attr *type_attr = find_node_attr(node, TH_ATTR_TYPE);
        if (type_attr == NULL || type_attr->value == NULL) {
            continue;
        }
        if (!ucs4_ieq_trimmed(type_attr->value, type_attr->value_len, JSON_LD_TYPE,
                              (Py_ssize_t)sizeof(JSON_LD_TYPE) - 1)) {
            continue;
        }
        PyObject *text = str_from_accessor(th_node_text, tree, node);
        if (text == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            failed = 1;     /* GCOVR_EXCL_LINE: allocation-failure path */
            break;          /* GCOVR_EXCL_LINE */
        }
        int append_failed = PyList_Append(texts, text) < 0;
        Py_DECREF(text);
        if (append_failed) { /* GCOVR_EXCL_BR_LINE: append fails only on unforceable allocation */
            failed = 1;      /* GCOVR_EXCL_LINE: allocation-failure path */
            break;           /* GCOVR_EXCL_LINE */
        }
    }
    Py_END_CRITICAL_SECTION();
    if (failed) {         /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        Py_DECREF(texts); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;      /* GCOVR_EXCL_LINE */
    }
    return texts;
}

/* The property/name attribute on a <meta> that carries an og:/twitter: key, or NULL when neither does. property wins
   over name when both qualify (OpenGraph uses property, Twitter uses name, but pages mix the two). */
static const th_node_attr *social_meta_attr(th_tree *tree, th_node *node) {
    Py_ssize_t property = th_node_attr_find(tree, node, "property", 8);
    if (property >= 0 && node->attrs[property].value != NULL &&
        is_social_key(node->attrs[property].value, node->attrs[property].value_len)) {
        return &node->attrs[property];
    }
    const th_node_attr *name = find_node_attr(node, TH_ATTR_NAME);
    if (name != NULL && name->value != NULL && is_social_key(name->value, name->value_len)) {
        return name;
    }
    return NULL;
}

/* Map every <meta property=og:*> / <meta name=twitter:*> key to its content value (last occurrence wins). NULL only on
   the excluded allocation-failure path. */
static PyObject *gather_opengraph(PyObject *self) {
    PyObject *result = PyDict_New();
    if (result == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_tree *tree = tree_of(self);
    th_node *root = ((NodeObject *)self)->node;
    int failed = 0;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    for (th_node *node = root->first_child; node != NULL; node = preorder_next(node, root)) {
        if (node->type != TH_NODE_ELEMENT || node->atom != TH_TAG_META) {
            continue;
        }
        const th_node_attr *key_attr = social_meta_attr(tree, node);
        if (key_attr == NULL) {
            continue;
        }
        PyObject *key = ucs4_to_str(key_attr->value, key_attr->value_len);
        if (key == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            failed = 1;    /* GCOVR_EXCL_LINE: allocation-failure path */
            break;         /* GCOVR_EXCL_LINE */
        }
        PyObject *content = attr_str_or_empty(tree, node, "content", 7);
        if (content == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            Py_DECREF(key);    /* GCOVR_EXCL_LINE: allocation-failure path */
            failed = 1;        /* GCOVR_EXCL_LINE */
            break;             /* GCOVR_EXCL_LINE */
        }
        int set_failed = PyDict_SetItem(result, key, content) < 0;
        Py_DECREF(key);
        Py_DECREF(content);
        if (set_failed) { /* GCOVR_EXCL_BR_LINE: insert fails only on unforceable allocation */
            failed = 1;   /* GCOVR_EXCL_LINE: allocation-failure path */
            break;        /* GCOVR_EXCL_LINE */
        }
    }
    Py_END_CRITICAL_SECTION();
    if (failed) {          /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        Py_DECREF(result); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;       /* GCOVR_EXCL_LINE */
    }
    return result;
}

/* Build one MicrodataItem for every top-level Microdata item (an element with itemscope and no itemprop, so it is not a
   property of another item). NULL only on the excluded allocation-failure path. */
static PyObject *gather_microdata(PyObject *self) {
    PyObject *items = PyList_New(0);
    if (items == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_tree *tree = tree_of(self);
    module_state *state = state_of(self);
    th_node *root = ((NodeObject *)self)->node;
    int failed = 0;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    for (th_node *node = root->first_child; node != NULL; node = preorder_next(node, root)) {
        if (node->type != TH_NODE_ELEMENT || find_node_attr(node, TH_ATTR_ITEMSCOPE) == NULL ||
            find_node_attr(node, TH_ATTR_ITEMPROP) != NULL) {
            continue;
        }
        PyObject *item = build_item(state, tree, node);
        if (item == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            failed = 1;     /* GCOVR_EXCL_LINE: allocation-failure path */
            break;          /* GCOVR_EXCL_LINE */
        }
        int append_failed = PyList_Append(items, item) < 0;
        Py_DECREF(item);
        if (append_failed) { /* GCOVR_EXCL_BR_LINE: append fails only on unforceable allocation */
            failed = 1;      /* GCOVR_EXCL_LINE: allocation-failure path */
            break;           /* GCOVR_EXCL_LINE */
        }
    }
    Py_END_CRITICAL_SECTION();
    if (failed) {         /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        Py_DECREF(items); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;      /* GCOVR_EXCL_LINE */
    }
    return items;
}

/* Document.json_ld() -> list. Parses every JSON-LD block through the registered Python facade. */
PyObject *turbohtml_document_json_ld(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    PyObject *texts = gather_json_ld(self);
    if (texts == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *parsed = PyObject_CallOneArg(state_of(self)->json_ld_parser, texts);
    Py_DECREF(texts);
    return parsed;
}

/* Document.opengraph() -> dict. */
PyObject *turbohtml_document_opengraph(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    return gather_opengraph(self);
}

/* Document.microdata() -> list[MicrodataItem]. */
PyObject *turbohtml_document_microdata(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    return gather_microdata(self);
}

/* Document.structured_data() -> StructuredData(json_ld, microdata, opengraph, microformats, rdfa). microformats and
   rdfa are a later phase, present as empty lists so the record's shape is stable. NULL only on the excluded
   allocation-failure path. */
PyObject *turbohtml_document_structured_data(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    PyObject *args = PyTuple_New(5);
    if (args == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int failed = 0;
    failed |= tuple_set_or_fail(args, 0, turbohtml_document_json_ld(self, NULL));
    failed |= tuple_set_or_fail(args, 1, gather_microdata(self));
    failed |= tuple_set_or_fail(args, 2, gather_opengraph(self));
    failed |= tuple_set_or_fail(args, 3, PyList_New(0));
    failed |= tuple_set_or_fail(args, 4, PyList_New(0));
    if (failed != 0) {   /* GCOVR_EXCL_BR_LINE: a section build fails only on unforceable allocation */
        Py_DECREF(args); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;     /* GCOVR_EXCL_LINE */
    }
    PyObject *result = PyObject_Call(state_of(self)->structured_data_type, args, NULL);
    Py_DECREF(args);
    return result;
}

/* Store the JSON-LD text parser and the MicrodataItem / StructuredData record classes the C walks build their results
   from; turbohtml._structured_data registers all three on import. */
PyObject *turbohtml_register_structured_data(PyObject *module, PyObject *args) {
    PyObject *parser = NULL;
    PyObject *microdata_item = NULL;
    PyObject *structured_data = NULL;
    if (!PyArg_ParseTuple(args, "OOO", &parser, &microdata_item, &structured_data)) { /* GCOVR_EXCL_BR_LINE: the
                                          facade always registers with three callables */
        return NULL; /* GCOVR_EXCL_LINE: argument-error path */
    }
    module_state *state = PyModule_GetState(module);
    Py_XSETREF(state->json_ld_parser, Py_NewRef(parser));
    Py_XSETREF(state->microdata_item_type, Py_NewRef(microdata_item));
    Py_XSETREF(state->structured_data_type, Py_NewRef(structured_data));
    Py_RETURN_NONE;
}
