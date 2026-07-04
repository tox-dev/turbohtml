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
   RDFa (item-shaped, following the RDFa Core/Lite processing rules for @vocab/@prefix term expansion) and Dublin Core
   (<meta name="dc.*">) join Microdata and OpenGraph in the same walk; microformats2 remains a documented later phase.
 */

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

/* Whether an og:/twitter: key names a URL-valued property, so an active base_url absolutizes its content. The Open
   Graph protocol types og:url and the og:image/og:audio/og:video URLs (plus their :url/:secure_url structured forms) as
   URLs, and the Twitter card spec types twitter:image and twitter:player (plus twitter:player:stream); every other key
   (title, type, description, dimensions, ...) is an opaque string left verbatim. Matched case-insensitively; an
   array+loop keeps the branch count stable. */
static int is_url_social_key(const Py_UCS4 *value, Py_ssize_t len) {
    static const char *const keys[] = {
        "og:url",
        "og:image",
        "og:image:url",
        "og:image:secure_url",
        "og:audio",
        "og:audio:url",
        "og:audio:secure_url",
        "og:video",
        "og:video:url",
        "og:video:secure_url",
        "twitter:image",
        "twitter:image:src",
        "twitter:player",
        "twitter:player:stream",
    };
    for (size_t index = 0; index < sizeof(keys) / sizeof(keys[0]); index++) {
        if (ucs4_ieq_trimmed(value, len, keys[index], (Py_ssize_t)strlen(keys[index]))) {
            return 1;
        }
    }
    return 0;
}

/* Absolutize *value against `base` in place: a relative URL joins onto base, an absolute one is left as is, an empty
   value stays empty (an absent attribute is not "the base URL"). A value the URL parser rejects (an unclosed IPv6
   bracket left in the page) stays verbatim rather than failing the whole extraction; only the caller's own base_url is
   validated up front. -1 with an error set only on the excluded allocation-failure path. */
static int resolve_in_place(PyObject *base, PyObject **value) {
    if (PyUnicode_GET_LENGTH(*value) == 0) {
        return 0;
    }
    PyObject *resolved = th_url_resolve(base, *value);
    if (resolved == NULL) {
        if (!PyErr_ExceptionMatches(PyExc_ValueError)) { /* GCOVR_EXCL_BR_LINE: only url_percent_encode allocation */
            return -1;                                   /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyErr_Clear(); /* a malformed URL in the page is not fatal; keep the raw value verbatim */
        return 0;
    }
    Py_SETREF(*value, resolved);
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

static PyObject *build_item(module_state *state, th_tree *tree, th_node *element, PyObject *base);

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
   else the element's text content). A URL-valued attribute is absolutized against `base` when the caller passed one
   (base is NULL otherwise, leaving values verbatim). NULL only on the excluded allocation-failure path. */
static PyObject *microdata_value(module_state *state, th_tree *tree, th_node *element, PyObject *base) {
    if (find_node_attr(element, TH_ATTR_ITEMSCOPE) != NULL) {
        return build_item(state, tree, element, base);
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
            if (!prop->url) {
                return attr_str_or_empty(tree, element, prop->attr, prop->attr_len);
            }
            PyObject *value = attr_url_or_empty(tree, element, prop->attr, prop->attr_len);
            if (value == NULL) { /* GCOVR_EXCL_BR_LINE: attr_url_or_empty only fails on allocation */
                return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            if (base != NULL) {
                if (resolve_in_place(base, &value) < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
                    Py_DECREF(value);                     /* GCOVR_EXCL_LINE: allocation-failure path */
                    return NULL;                          /* GCOVR_EXCL_LINE */
                }
            }
            return value;
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
static int collect_properties(module_state *state, th_tree *tree, th_node *element, PyObject *properties,
                              PyObject *base) {
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
        PyObject *value = microdata_value(state, tree, property, base);
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
static PyObject *build_item(module_state *state, th_tree *tree, th_node *element, PyObject *base) {
    PyObject *properties = PyDict_New();
    if (properties == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;          /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (collect_properties(state, tree, element, properties, base) < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure */
        Py_DECREF(properties);                                            /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;                                                      /* GCOVR_EXCL_LINE */
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

/* Map every <meta property=og:*> / <meta name=twitter:*> key to its content value (last occurrence wins). A URL-valued
   key's content is absolutized against `base` when the caller passed one (base is NULL otherwise, leaving every value
   verbatim). NULL only on the excluded allocation-failure path. */
static PyObject *gather_opengraph(PyObject *self, PyObject *base) {
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
        if (base != NULL && is_url_social_key(key_attr->value, key_attr->value_len)) {
            if (resolve_in_place(base, &content) < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
                Py_DECREF(key);                         /* GCOVR_EXCL_LINE: allocation-failure path */
                Py_DECREF(content);                     /* GCOVR_EXCL_LINE */
                failed = 1;                             /* GCOVR_EXCL_LINE */
                break;                                  /* GCOVR_EXCL_LINE */
            }
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
   property of another item). URL-valued properties are absolutized against `base` when the caller passed one (base is
   NULL otherwise, leaving values verbatim). NULL only on the excluded allocation-failure path. */
static PyObject *gather_microdata(PyObject *self, PyObject *base) {
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
        PyObject *item = build_item(state, tree, node, base);
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

/* The RDFa 1.1 initial context: the prefix -> IRI mappings a page inherits without declaring a @prefix, so schema:,
   dc:, foaf:, and the other well-known vocabularies expand out of the box (W3C rdfa-1.1 context). A page @prefix
   overrides an entry of the same name for its subtree. */
typedef struct {
    const char *prefix;
    const char *iri;
} rdfa_prefix;

static const rdfa_prefix RDFA_DEFAULT_PREFIXES[] = {
    {"as", "https://www.w3.org/ns/activitystreams#"},
    {"csvw", "http://www.w3.org/ns/csvw#"},
    {"cat", "http://www.w3.org/ns/dcat#"},
    {"cc", "http://creativecommons.org/ns#"},
    {"cnt", "http://www.w3.org/2011/content#"},
    {"ctag", "http://commontag.org/ns#"},
    {"dc", "http://purl.org/dc/terms/"},
    {"dc11", "http://purl.org/dc/elements/1.1/"},
    {"dcat", "http://www.w3.org/ns/dcat#"},
    {"dcterms", "http://purl.org/dc/terms/"},
    {"dctypes", "http://purl.org/dc/dcmitype/"},
    {"dqv", "http://www.w3.org/ns/dqv#"},
    {"duv", "https://www.w3.org/ns/duv#"},
    {"foaf", "http://xmlns.com/foaf/0.1/"},
    {"gr", "http://purl.org/goodrelations/v1#"},
    {"grddl", "http://www.w3.org/2003/g/data-view#"},
    {"ht", "http://www.w3.org/2006/http#"},
    {"ical", "http://www.w3.org/2002/12/cal/icaltzd#"},
    {"ldp", "http://www.w3.org/ns/ldp#"},
    {"ma", "http://www.w3.org/ns/ma-ont#"},
    {"oa", "http://www.w3.org/ns/oa#"},
    {"og", "http://ogp.me/ns#"},
    {"org", "http://www.w3.org/ns/org#"},
    {"owl", "http://www.w3.org/2002/07/owl#"},
    {"prov", "http://www.w3.org/ns/prov#"},
    {"qb", "http://purl.org/linked-data/cube#"},
    {"rdf", "http://www.w3.org/1999/02/22-rdf-syntax-ns#"},
    {"rdfa", "http://www.w3.org/ns/rdfa#"},
    {"rdfs", "http://www.w3.org/2000/01/rdf-schema#"},
    {"rev", "http://purl.org/stuff/rev#"},
    {"rif", "http://www.w3.org/2007/rif#"},
    {"rr", "http://www.w3.org/ns/r2rml#"},
    {"schema", "http://schema.org/"},
    {"sd", "http://www.w3.org/ns/sparql-service-description#"},
    {"sioc", "http://rdfs.org/sioc/ns#"},
    {"skos", "http://www.w3.org/2004/02/skos/core#"},
    {"skosxl", "http://www.w3.org/2008/05/skos-xl#"},
    {"v", "http://rdf.data-vocabulary.org/#"},
    {"vcard", "http://www.w3.org/2006/vcard/ns#"},
    {"void", "http://rdfs.org/ns/void#"},
    {"wdr", "http://www.w3.org/2007/05/powder#"},
    {"wdrs", "http://www.w3.org/2007/05/powder-s#"},
    {"xhv", "http://www.w3.org/1999/xhtml/vocab#"},
    {"xml", "http://www.w3.org/XML/1998/namespace"},
    {"xsd", "http://www.w3.org/2001/XMLSchema#"},
};

/* A fresh prefix map seeded with the RDFa 1.1 initial context, the base every document walk starts from. NULL only on
   the excluded allocation-failure path. */
static PyObject *build_default_prefixes(void) {
    PyObject *prefixes = PyDict_New();
    if (prefixes == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (size_t index = 0; index < sizeof(RDFA_DEFAULT_PREFIXES) / sizeof(RDFA_DEFAULT_PREFIXES[0]); index++) {
        PyObject *iri = PyUnicode_FromString(RDFA_DEFAULT_PREFIXES[index].iri);
        if (iri == NULL) {       /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            Py_DECREF(prefixes); /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;         /* GCOVR_EXCL_LINE */
        }
        int failed = PyDict_SetItemString(prefixes, RDFA_DEFAULT_PREFIXES[index].prefix, iri) < 0;
        Py_DECREF(iri);
        if (failed) {            /* GCOVR_EXCL_BR_LINE: insert fails only on unforceable allocation */
            Py_DECREF(prefixes); /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;         /* GCOVR_EXCL_LINE */
        }
    }
    return prefixes;
}

/* The in-scope RDFa evaluation context threaded down the walk: the current @vocab (borrowed, NULL when none), the
   prefix map (borrowed, always non-NULL), and the base URL IRIs resolve against (borrowed, NULL to keep them verbatim).
 */
typedef struct {
    PyObject *vocab;
    PyObject *prefixes;
    PyObject *base;
} rdfa_ctx;

/* Expand an RDFa TERMorCURIEorAbsIRI token to its IRI: a bare term (no colon) joins the in-scope @vocab, a prefixed
   token prefix:reference joins the prefix's IRI when the prefix is declared, and anything else (an undeclared prefix,
   an absolute IRI, or a bare term with no @vocab in scope) is kept verbatim. NULL only on the excluded
   allocation-failure path. */
static PyObject *rdfa_expand_term(PyObject *token, PyObject *vocab, PyObject *prefixes) {
    Py_ssize_t length = PyUnicode_GET_LENGTH(token);
    Py_ssize_t colon = PyUnicode_FindChar(token, (Py_UCS4)':', 0, length, 1);
    if (colon < 0) {
        if (vocab != NULL) {
            return PyUnicode_Concat(vocab, token);
        }
        return Py_NewRef(token);
    }
    PyObject *prefix = PyUnicode_Substring(token, 0, colon);
    if (prefix == NULL) { /* GCOVR_EXCL_BR_LINE: substring fails only on unforceable allocation */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *iri = PyDict_GetItemWithError(prefixes, prefix);
    Py_DECREF(prefix);
    if (iri == NULL) {
        if (PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: lookup fails only on an unforceable error */
            return NULL;        /* GCOVR_EXCL_LINE: error path */
        }
        return Py_NewRef(token);
    }
    PyObject *reference = PyUnicode_Substring(token, colon + 1, length);
    if (reference == NULL) { /* GCOVR_EXCL_BR_LINE: substring fails only on unforceable allocation */
        return NULL;         /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *result = PyUnicode_Concat(iri, reference);
    Py_DECREF(reference);
    return result;
}

/* Merge the whitespace-separated `prefix: iri` pairs of a @prefix attribute into `prefixes`. A `prefix:` token names a
   prefix (the trailing colon is dropped) and the next token is its IRI; a token that is neither is skipped, and a
   trailing `prefix:` with no IRI is dropped (RDFa 1.1 processing). -1 only on the excluded allocation-failure path. */
static int parse_prefix_decls(PyObject *prefixes, const Py_UCS4 *value, Py_ssize_t len) {
    PyObject *pending = NULL;
    Py_ssize_t cursor = 0;
    while (cursor < len) {
        while (cursor < len && is_space(value[cursor])) {
            cursor++;
        }
        Py_ssize_t start = cursor;
        while (cursor < len && !is_space(value[cursor])) {
            cursor++;
        }
        if (cursor == start) {
            break;
        }
        if (pending == NULL) {
            if (value[cursor - 1] == (Py_UCS4)':') {
                pending = ucs4_to_str(&value[start], cursor - start - 1);
                if (pending == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                    return -1;         /* GCOVR_EXCL_LINE: allocation-failure path */
                }
            }
            continue;
        }
        PyObject *iri = ucs4_to_str(&value[start], cursor - start);
        if (iri == NULL) {      /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            Py_DECREF(pending); /* GCOVR_EXCL_LINE: allocation-failure path */
            return -1;          /* GCOVR_EXCL_LINE */
        }
        int failed = PyDict_SetItem(prefixes, pending, iri) < 0;
        Py_DECREF(pending);
        pending = NULL;
        Py_DECREF(iri);
        if (failed) {  /* GCOVR_EXCL_BR_LINE: insert fails only on unforceable allocation */
            return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    Py_XDECREF(pending);
    return 0;
}

/* The child's in-scope @vocab: a new str for a non-empty @vocab, NULL (the vocabulary cleared) for an empty or
   valueless one, or the parent's borrowed value when the child declares none. *owned records whether *out is a new
   reference the caller must release. -1 only on the excluded allocation-failure path. */
static int resolve_child_vocab(th_tree *tree, th_node *child, PyObject *parent, PyObject **out, int *owned) {
    Py_ssize_t index = th_node_attr_find(tree, child, "vocab", 5);
    if (index < 0) {
        *out = parent;
        *owned = 0;
        return 0;
    }
    const th_node_attr *attr = &child->attrs[index];
    if (attr->value == NULL) {
        *out = NULL;
        *owned = 0;
        return 0;
    }
    PyObject *value = ucs4_to_str(attr->value, attr->value_len);
    if (value == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    *out = value;
    *owned = 1;
    return 0;
}

/* The child's in-scope prefix map: a copy of the parent's updated with the child's @prefix declarations, or the
   parent's borrowed map when the child declares none. *owned records whether *out is a new reference to release. -1
   only on the excluded allocation-failure path. */
static int resolve_child_prefixes(th_tree *tree, th_node *child, PyObject *parent, PyObject **out, int *owned) {
    Py_ssize_t index = th_node_attr_find(tree, child, "prefix", 6);
    if (index < 0 || child->attrs[index].value == NULL) {
        *out = parent;
        *owned = 0;
        return 0;
    }
    PyObject *copy = PyDict_Copy(parent);
    if (copy == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (parse_prefix_decls(copy, child->attrs[index].value, child->attrs[index].value_len) < 0) { /* GCOVR_EXCL_BR_LINE:
                                                                                 allocation-failure path */
        Py_DECREF(copy); /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;       /* GCOVR_EXCL_LINE */
    }
    *out = copy;
    *owned = 1;
    return 0;
}

/* Drop whichever of the child context's vocab/prefixes were freshly allocated by resolve_child_ctx. */
static void release_child_ctx(rdfa_ctx *child_ctx, int vocab_owned, int prefixes_owned) {
    if (vocab_owned) {
        Py_DECREF(child_ctx->vocab);
    }
    if (prefixes_owned) {
        Py_DECREF(child_ctx->prefixes);
    }
}

/* Derive the child's evaluation context from the parent's, applying the child's @vocab and @prefix. *vocab_owned and
   *prefixes_owned record which of the two carried a new reference, for release_child_ctx to drop after the subtree is
   walked. -1 only on the excluded allocation-failure path. */
static int resolve_child_ctx(th_tree *tree, th_node *child, rdfa_ctx parent, rdfa_ctx *child_ctx, int *vocab_owned,
                             int *prefixes_owned) {
    PyObject *vocab = NULL;
    int vocab_rc = resolve_child_vocab(tree, child, parent.vocab, &vocab, vocab_owned);
    if (vocab_rc < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *prefixes = NULL;
    if (resolve_child_prefixes(tree, child, parent.prefixes, &prefixes, prefixes_owned) < 0) { /* GCOVR_EXCL_BR_LINE:
                                                                                        allocation-failure path */
        rdfa_ctx partial = {vocab, NULL, NULL};       /* GCOVR_EXCL_LINE: allocation-failure path */
        release_child_ctx(&partial, *vocab_owned, 0); /* GCOVR_EXCL_LINE */
        return -1;                                    /* GCOVR_EXCL_LINE */
    }
    child_ctx->vocab = vocab;
    child_ctx->prefixes = prefixes;
    child_ctx->base = parent.base;
    return 0;
}

/* The resource subject of a typeof element: its @about, else @resource, else @href, else @src (resolved against base
   when set), or None for a blank node when it names none. NULL only on the excluded allocation-failure path. */
static PyObject *rdfa_subject(th_tree *tree, th_node *element, PyObject *base) {
    static const char *const names[] = {"about", "resource", "href", "src"};
    static const Py_ssize_t lens[] = {5, 8, 4, 3};
    for (size_t index = 0; index < sizeof(names) / sizeof(names[0]); index++) {
        if (th_node_attr_find(tree, element, names[index], lens[index]) < 0) {
            continue;
        }
        PyObject *iri = attr_url_or_empty(tree, element, names[index], lens[index]);
        if (iri == NULL) { /* GCOVR_EXCL_BR_LINE: attr_url_or_empty fails only on allocation */
            return NULL;   /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (base != NULL) {
            if (resolve_in_place(base, &iri) < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
                Py_DECREF(iri);                     /* GCOVR_EXCL_LINE: allocation-failure path */
                return NULL;                        /* GCOVR_EXCL_LINE */
            }
        }
        return iri;
    }
    return Py_NewRef(Py_None);
}

static PyObject *build_rdfa_item(th_tree *tree, module_state *state, th_node *element, rdfa_ctx ctx);
static int collect_rdfa_properties(th_tree *tree, module_state *state, th_node *element, rdfa_ctx ctx,
                                   PyObject *properties);

/* The expanded @typeof IRIs of an element as a list, empty for a valueless typeof. The caller only reaches here for an
   element carrying typeof, so the attribute is present. NULL only on the excluded allocation-failure path. */
static PyObject *expand_typeof(th_tree *tree, th_node *element, rdfa_ctx ctx) {
    Py_ssize_t index = th_node_attr_find(tree, element, "typeof", 6);
    const th_node_attr *attr = &element->attrs[index];
    if (attr->value == NULL) {
        return PyList_New(0);
    }
    PyObject *tokens = split_token_list(attr->value, attr->value_len);
    if (tokens == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *result = PyList_New(0);
    if (result == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_DECREF(tokens); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;       /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t count = PyList_GET_SIZE(tokens);
    for (Py_ssize_t token = 0; token < count; token++) {
        PyObject *expanded = rdfa_expand_term(PyList_GET_ITEM(tokens, token), ctx.vocab, ctx.prefixes);
        if (expanded == NULL) { /* GCOVR_EXCL_BR_LINE: expansion fails only on unforceable allocation */
            Py_DECREF(tokens);  /* GCOVR_EXCL_LINE: allocation-failure path */
            Py_DECREF(result);  /* GCOVR_EXCL_LINE */
            return NULL;        /* GCOVR_EXCL_LINE */
        }
        int failed = PyList_Append(result, expanded) < 0;
        Py_DECREF(expanded);
        if (failed) {          /* GCOVR_EXCL_BR_LINE: append fails only on unforceable allocation */
            Py_DECREF(tokens); /* GCOVR_EXCL_LINE: allocation-failure path */
            Py_DECREF(result); /* GCOVR_EXCL_LINE */
            return NULL;       /* GCOVR_EXCL_LINE */
        }
    }
    Py_DECREF(tokens);
    return result;
}

/* One RDFa property element's object: the @content literal, else a nested RdfaItem when the element carries @typeof,
   else the @resource/@href/@src IRI (absolutized against base when set), else a <time> @datetime literal, else the
   element's text content. NULL only on the excluded allocation-failure path. */
static PyObject *rdfa_value(th_tree *tree, module_state *state, th_node *element, rdfa_ctx ctx) {
    Py_ssize_t content = th_node_attr_find(tree, element, "content", 7);
    if (content >= 0) {
        const th_node_attr *attr = &element->attrs[content];
        if (attr->value == NULL) {
            return PyUnicode_FromString("");
        }
        return ucs4_to_str(attr->value, attr->value_len);
    }
    if (th_node_attr_find(tree, element, "typeof", 6) >= 0) {
        return build_rdfa_item(tree, state, element, ctx);
    }
    static const char *const iri_names[] = {"resource", "href", "src"};
    static const Py_ssize_t iri_lens[] = {8, 4, 3};
    for (size_t index = 0; index < sizeof(iri_names) / sizeof(iri_names[0]); index++) {
        if (th_node_attr_find(tree, element, iri_names[index], iri_lens[index]) < 0) {
            continue;
        }
        PyObject *iri = attr_url_or_empty(tree, element, iri_names[index], iri_lens[index]);
        if (iri == NULL) { /* GCOVR_EXCL_BR_LINE: attr_url_or_empty fails only on allocation */
            return NULL;   /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (ctx.base != NULL) {
            if (resolve_in_place(ctx.base, &iri) < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
                Py_DECREF(iri);                         /* GCOVR_EXCL_LINE: allocation-failure path */
                return NULL;                            /* GCOVR_EXCL_LINE */
            }
        }
        return iri;
    }
    if (element->atom == TH_TAG_TIME) {
        Py_ssize_t datetime = th_node_attr_find(tree, element, "datetime", 8);
        if (datetime >= 0 && element->attrs[datetime].value != NULL) {
            return ucs4_to_str(element->attrs[datetime].value, element->attrs[datetime].value_len);
        }
    }
    return str_from_accessor(th_node_text, tree, element);
}

/* Build one RdfaItem(vocab, type, resource, properties) for the typeof element rooted at `element`, its properties
   collected from the subtree under the element's own evaluation context. NULL only on the excluded allocation-failure
   path. */
static PyObject *build_rdfa_item(th_tree *tree, module_state *state, th_node *element, rdfa_ctx ctx) {
    PyObject *properties = PyDict_New();
    if (properties == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;          /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (collect_rdfa_properties(tree, state, element, ctx, properties) < 0) { /* GCOVR_EXCL_BR_LINE: alloc-failure */
        Py_DECREF(properties);                                                /* GCOVR_EXCL_LINE: allocation-failure */
        return NULL;                                                          /* GCOVR_EXCL_LINE */
    }
    PyObject *type_list = expand_typeof(tree, element, ctx);
    if (type_list == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        Py_DECREF(properties); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;           /* GCOVR_EXCL_LINE */
    }
    PyObject *resource = rdfa_subject(tree, element, ctx.base);
    if (resource == NULL) {    /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        Py_DECREF(type_list);  /* GCOVR_EXCL_LINE: allocation-failure path */
        Py_DECREF(properties); /* GCOVR_EXCL_LINE */
        return NULL;           /* GCOVR_EXCL_LINE */
    }
    PyObject *vocab = ctx.vocab != NULL ? Py_NewRef(ctx.vocab) : Py_NewRef(Py_None);
    PyObject *item = PyObject_CallFunctionObjArgs(state->rdfa_item_type, vocab, type_list, resource, properties, NULL);
    Py_DECREF(vocab);
    Py_DECREF(type_list);
    Py_DECREF(resource);
    Py_DECREF(properties);
    return item;
}

/* Append `value` under every expanded name in the property run to the properties dict, each name mapping to a list of
   values in document order. -1 only on the excluded allocation-failure path. */
static int rdfa_add_property(PyObject *properties, const Py_UCS4 *names, Py_ssize_t names_len, rdfa_ctx ctx,
                             PyObject *value) {
    PyObject *tokens = split_token_list(names, names_len);
    if (tokens == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t count = PyList_GET_SIZE(tokens);
    int status = -1;
    for (Py_ssize_t token = 0; token < count; token++) {
        PyObject *key = rdfa_expand_term(PyList_GET_ITEM(tokens, token), ctx.vocab, ctx.prefixes);
        if (key == NULL) { /* GCOVR_EXCL_BR_LINE: expansion fails only on unforceable allocation */
            goto done;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyObject *bucket = PyDict_GetItemWithError(properties, key);
        if (bucket != NULL) {
            Py_INCREF(bucket);
        } else {
            if (PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: lookup fails only on an unforceable error */
                Py_DECREF(key);     /* GCOVR_EXCL_LINE: error path */
                goto done;          /* GCOVR_EXCL_LINE */
            }
            bucket = PyList_New(0);
            if (bucket == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                Py_DECREF(key);   /* GCOVR_EXCL_LINE: allocation-failure path */
                goto done;        /* GCOVR_EXCL_LINE */
            }
            if (PyDict_SetItem(properties, key, bucket) < 0) { /* GCOVR_EXCL_BR_LINE: insert fails only on alloc */
                Py_DECREF(key);                                /* GCOVR_EXCL_LINE: allocation-failure path */
                Py_DECREF(bucket);                             /* GCOVR_EXCL_LINE */
                goto done;                                     /* GCOVR_EXCL_LINE */
            }
        }
        Py_DECREF(key);
        int append_failed = PyList_Append(bucket, value) < 0;
        Py_DECREF(bucket);
        if (append_failed) { /* GCOVR_EXCL_BR_LINE: append fails only on unforceable allocation */
            goto done;       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    status = 0;
done:
    Py_DECREF(tokens);
    return status;
}

/* Process one child of an item's subtree: record its @property value(s), then descend unless it is itself a @typeof
   (whose own subtree belongs to the nested item, mirroring microdata's nested itemscope). -1 only on the excluded
   allocation-failure path. */
static int collect_rdfa_child(th_tree *tree, module_state *state, th_node *child, rdfa_ctx ctx, PyObject *properties) {
    Py_ssize_t property = th_node_attr_find(tree, child, "property", 8);
    if (property >= 0 && child->attrs[property].value != NULL) {
        PyObject *value = rdfa_value(tree, state, child, ctx);
        if (value == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        int failed = rdfa_add_property(properties, child->attrs[property].value, child->attrs[property].value_len, ctx,
                                       value) < 0;
        Py_DECREF(value);
        if (failed) {  /* GCOVR_EXCL_BR_LINE: allocation-failure path */
            return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    if (th_node_attr_find(tree, child, "typeof", 6) >= 0) {
        return 0;
    }
    return collect_rdfa_properties(tree, state, child, ctx, properties);
}

/* Crawl the descendants of the item rooted at `element`, gathering their @property values under `properties` and
   threading each child's @vocab/@prefix context. -1 only on the excluded allocation-failure path. */
static int collect_rdfa_properties(th_tree *tree, module_state *state, th_node *element, rdfa_ctx ctx,
                                   PyObject *properties) {
    for (th_node *child = element->first_child; child != NULL; child = child->next_sibling) {
        if (child->type != TH_NODE_ELEMENT) {
            continue;
        }
        rdfa_ctx child_ctx;
        int vocab_owned;
        int prefixes_owned;
        if (resolve_child_ctx(tree, child, ctx, &child_ctx, &vocab_owned, &prefixes_owned) < 0) { /* GCOVR_EXCL_BR_LINE:
                                                                                        allocation-failure path */
            return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        int status = collect_rdfa_child(tree, state, child, child_ctx, properties);
        release_child_ctx(&child_ctx, vocab_owned, prefixes_owned);
        if (status < 0) { /* GCOVR_EXCL_BR_LINE: a child fails only on unforceable allocation */
            return -1;    /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    return 0;
}

/* Walk `element`'s subtree for the outermost @typeof elements, building one top-level RdfaItem each (its own subtree is
   consumed by build_rdfa_item, so the walk does not descend into it) and threading @vocab/@prefix down. -1 only on the
   excluded allocation-failure path. */
static int walk_rdfa(th_tree *tree, module_state *state, th_node *element, rdfa_ctx ctx, PyObject *items) {
    for (th_node *child = element->first_child; child != NULL; child = child->next_sibling) {
        if (child->type != TH_NODE_ELEMENT) {
            continue;
        }
        rdfa_ctx child_ctx;
        int vocab_owned;
        int prefixes_owned;
        if (resolve_child_ctx(tree, child, ctx, &child_ctx, &vocab_owned, &prefixes_owned) < 0) { /* GCOVR_EXCL_BR_LINE:
                                                                                        allocation-failure path */
            return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        int status;
        if (th_node_attr_find(tree, child, "typeof", 6) >= 0) {
            PyObject *item = build_rdfa_item(tree, state, child, child_ctx);
            if (item == NULL) { /* GCOVR_EXCL_BR_LINE: build fails only on unforceable allocation */
                release_child_ctx(&child_ctx, vocab_owned, prefixes_owned); /* GCOVR_EXCL_LINE: alloc-failure path */
                return -1;                                                  /* GCOVR_EXCL_LINE */
            }
            status = PyList_Append(items, item);
            Py_DECREF(item);
        } else {
            status = walk_rdfa(tree, state, child, child_ctx, items);
        }
        release_child_ctx(&child_ctx, vocab_owned, prefixes_owned);
        if (status < 0) { /* GCOVR_EXCL_BR_LINE: append/recursion fails only on unforceable allocation */
            return -1;    /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    return 0;
}

/* Build one RdfaItem for every top-level RDFa resource, absolutizing IRIs against `base` when the caller passed one.
   NULL only on the excluded allocation-failure path. */
static PyObject *gather_rdfa(PyObject *self, PyObject *base) {
    PyObject *items = PyList_New(0);
    if (items == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *prefixes = build_default_prefixes();
    if (prefixes == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_DECREF(items);   /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;        /* GCOVR_EXCL_LINE */
    }
    th_tree *tree = tree_of(self);
    module_state *state = state_of(self);
    th_node *root = ((NodeObject *)self)->node;
    rdfa_ctx ctx = {NULL, prefixes, base};
    int failed = 0;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    failed = walk_rdfa(tree, state, root, ctx, items) < 0;
    Py_END_CRITICAL_SECTION();
    Py_DECREF(prefixes);
    if (failed) {         /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        Py_DECREF(items); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;      /* GCOVR_EXCL_LINE */
    }
    return items;
}

/* Whether a <meta name> value starts with a case-insensitive Dublin Core prefix (dc. or dcterms.). */
static int ucs4_has_prefix_ci(const Py_UCS4 *value, Py_ssize_t len, const char *prefix, Py_ssize_t prefix_len) {
    if (len < prefix_len) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < prefix_len; index++) {
        Py_UCS4 c = value[index];
        Py_UCS4 folded = (c >= 'A' && c <= 'Z') ? (c | 0x20) : c;
        if (folded != (Py_UCS4)(unsigned char)prefix[index]) {
            return 0;
        }
    }
    return 1;
}

static int is_dc_name(const Py_UCS4 *value, Py_ssize_t len) {
    static const char *const prefixes[] = {"dc.", "dcterms."};
    static const Py_ssize_t prefix_lens[] = {3, 8};
    for (size_t index = 0; index < sizeof(prefixes) / sizeof(prefixes[0]); index++) {
        if (ucs4_has_prefix_ci(value, len, prefixes[index], prefix_lens[index])) {
            return 1;
        }
    }
    return 0;
}

/* The UCS4 run as a new str with ASCII A-Z folded to lower case, so a `DC.Title` name keys the same as `dc.title`. NULL
   only on the excluded allocation-failure path. */
static PyObject *ucs4_lower_str(const Py_UCS4 *data, Py_ssize_t len) {
    Py_UCS4 *buffer = PyMem_Malloc((size_t)len * sizeof(Py_UCS4));
    if (buffer == NULL) {        /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_UCS4 c = data[index];
        buffer[index] = (c >= 'A' && c <= 'Z') ? (c | 0x20) : c;
    }
    PyObject *result = ucs4_to_str(buffer, len);
    PyMem_Free(buffer);
    return result;
}

/* Map every <meta name="dc.*"> / <meta name="dcterms.*"> name (lower-cased) to its content value, last occurrence
   winning. NULL only on the excluded allocation-failure path. */
static PyObject *gather_dublin_core(PyObject *self) {
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
        const th_node_attr *name = find_node_attr(node, TH_ATTR_NAME);
        if (name == NULL || name->value == NULL) {
            continue;
        }
        if (!is_dc_name(name->value, name->value_len)) {
            continue;
        }
        PyObject *key = ucs4_lower_str(name->value, name->value_len);
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

/* The effective document base for an opt-in base_url: the caller's URL validated once (so a malformed one raises an
   actionable ValueError here rather than surfacing deep in the walk) with any <base href> resolved against it (HTML
   spec 4.2.3). A malformed <base href> is ignored so an untrusted page cannot break the extraction. Returns a new
   reference, NULL with an exception set on a bad base_url. */
static PyObject *resolve_base(PyObject *self, PyObject *base_url) {
    PyObject *probe = th_url_resolve(base_url, base_url);
    if (probe == NULL) {
        if (PyErr_ExceptionMatches(PyExc_ValueError)) { /* GCOVR_EXCL_BR_LINE: the else is an allocation-only failure */
            PyErr_Clear();
            PyErr_Format(PyExc_ValueError, "base_url %R is not a valid absolute URL to resolve relative URLs against",
                         base_url);
        }
        return NULL;
    }
    Py_DECREF(probe);
    PyObject *base = th_document_base_url(self, base_url);
    if (base == NULL) {
        if (!PyErr_ExceptionMatches(PyExc_ValueError)) { /* GCOVR_EXCL_BR_LINE: only allocation past a validated base */
            return NULL;                                 /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyErr_Clear(); /* a malformed <base href> falls back to base_url rather than failing */
        return Py_NewRef(base_url);
    }
    return base;
}

/* Parse the optional base_url the opengraph/microdata/structured_data methods share, `spec` naming the method for the
   error. None or an omitted argument leaves *base NULL (verbatim output); a str resolves the effective document base.
   Returns 0 on success, -1 with an exception on a bad type or a malformed base_url. */
static int parse_base_url(PyObject *self, PyObject *args, PyObject *kwargs, const char *spec, PyObject **base) {
    static char *keywords[] = {"base_url", NULL};
    PyObject *base_url = NULL;
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, spec, keywords, &base_url)) {
        return -1;
    }
    *base = NULL;
    if (base_url == NULL || base_url == Py_None) {
        return 0;
    }
    if (!PyUnicode_Check(base_url)) {
        PyErr_Format(PyExc_TypeError, "base_url must be a str or None, got %.200s", Py_TYPE(base_url)->tp_name);
        return -1;
    }
    *base = resolve_base(self, base_url);
    return *base == NULL ? -1 : 0;
}

/* Document.opengraph(base_url=None) -> dict. */
PyObject *turbohtml_document_opengraph(PyObject *self, PyObject *args, PyObject *kwargs) {
    PyObject *base = NULL;
    if (parse_base_url(self, args, kwargs, "|O:opengraph", &base) < 0) {
        return NULL;
    }
    PyObject *result = gather_opengraph(self, base);
    Py_XDECREF(base);
    return result;
}

/* Document.microdata(base_url=None) -> list[MicrodataItem]. */
PyObject *turbohtml_document_microdata(PyObject *self, PyObject *args, PyObject *kwargs) {
    PyObject *base = NULL;
    if (parse_base_url(self, args, kwargs, "|O:microdata", &base) < 0) {
        return NULL;
    }
    PyObject *result = gather_microdata(self, base);
    Py_XDECREF(base);
    return result;
}

/* Document.rdfa(base_url=None) -> list[RdfaItem]. */
PyObject *turbohtml_document_rdfa(PyObject *self, PyObject *args, PyObject *kwargs) {
    PyObject *base = NULL;
    if (parse_base_url(self, args, kwargs, "|O:rdfa", &base) < 0) {
        return NULL;
    }
    PyObject *result = gather_rdfa(self, base);
    Py_XDECREF(base);
    return result;
}

/* Document.dublin_core() -> dict. */
PyObject *turbohtml_document_dublin_core(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    return gather_dublin_core(self);
}

/* Document.structured_data(base_url=None) -> StructuredData(json_ld, microdata, opengraph, microformats, rdfa,
   dublin_core). A base_url absolutizes the microdata, opengraph, and RDFa URL fields; json_ld stays verbatim (resolving
   @id inside arbitrary JSON is a separate concern) and Dublin Core content is verbatim (its values are literals, not
   typed URLs). microformats is still a later phase, present as an empty list so the record's shape is stable. NULL only
   on the excluded allocation-failure path (or with an exception set on a bad base_url). */
PyObject *turbohtml_document_structured_data(PyObject *self, PyObject *args, PyObject *kwargs) {
    PyObject *base = NULL;
    if (parse_base_url(self, args, kwargs, "|O:structured_data", &base) < 0) {
        return NULL;
    }
    PyObject *sections = PyTuple_New(6);
    if (sections == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_XDECREF(base);   /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;        /* GCOVR_EXCL_LINE */
    }
    int failed = 0;
    failed |= tuple_set_or_fail(sections, 0, turbohtml_document_json_ld(self, NULL));
    failed |= tuple_set_or_fail(sections, 1, gather_microdata(self, base));
    failed |= tuple_set_or_fail(sections, 2, gather_opengraph(self, base));
    failed |= tuple_set_or_fail(sections, 3, PyList_New(0));
    failed |= tuple_set_or_fail(sections, 4, gather_rdfa(self, base));
    failed |= tuple_set_or_fail(sections, 5, gather_dublin_core(self));
    Py_XDECREF(base);
    if (failed != 0) {       /* GCOVR_EXCL_BR_LINE: a section build fails only on unforceable allocation */
        Py_DECREF(sections); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;         /* GCOVR_EXCL_LINE */
    }
    PyObject *result = PyObject_Call(state_of(self)->structured_data_type, sections, NULL);
    Py_DECREF(sections);
    return result;
}

/* Store the JSON-LD text parser and the MicrodataItem / RdfaItem / StructuredData record classes the C walks build
   their results from; turbohtml._structured_data registers all four on import. */
PyObject *turbohtml_register_structured_data(PyObject *module, PyObject *args) {
    PyObject *parser = NULL;
    PyObject *microdata_item = NULL;
    PyObject *rdfa_item = NULL;
    PyObject *structured_data = NULL;
    int parsed = PyArg_ParseTuple(args, "OOOO", &parser, &microdata_item, &rdfa_item, &structured_data);
    if (!parsed) {   /* GCOVR_EXCL_BR_LINE: the facade always registers with four callables */
        return NULL; /* GCOVR_EXCL_LINE: argument-error path */
    }
    module_state *state = PyModule_GetState(module);
    Py_XSETREF(state->json_ld_parser, Py_NewRef(parser));
    Py_XSETREF(state->microdata_item_type, Py_NewRef(microdata_item));
    Py_XSETREF(state->rdfa_item_type, Py_NewRef(rdfa_item));
    Py_XSETREF(state->structured_data_type, Py_NewRef(structured_data));
    Py_RETURN_NONE;
}
